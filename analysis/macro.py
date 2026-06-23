"""Macro indicators: FRED series (real yields, credit spreads, yield curve, liquidity, dollar)
plus local VIX.  Data is fetched once per EOD scheduler run and stored in the
macro_indicators table; compute_macro() reads from the DB so it stays fast during
per-ticker analysis loops.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import MacroIndicator, get_engine
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)

# Tickers that are themselves macro instruments — skip macro scoring to avoid
# self-referential logic (TLT IS bond prices; VIX IS volatility, etc.).
_MACRO_TICKERS = frozenset({"^VIX", "TLT", "IEF", "HYG"})

# Watchlist groups whose members receive the CFNAI economic-cycle adjustment.
_EQUITY_GROUPS = frozenset({
    "indices", "sector_etfs", "international", "uk_etfs",
    "tech_subsectors", "financial_subsectors", "healthcare_subsectors",
    "international_expanded",
})
_CRYPTO_GROUPS   = frozenset({"crypto", "crypto_expanded"})
_RATE_BEARISH    = frozenset({"VNQ", "XLRE", "ITB", "MUB", "SHY", "BND"})
_RATE_BULLISH    = frozenset({"KBE", "KRE", "XLF"})
_REAL_ESTATE_SET = frozenset({"VNQ", "XLRE", "ITB"})
_GOLD_SET        = frozenset({"GLD", "SLV"})

# Series that have ticker-specific routing or overlay triggers (vs. composite-only).
# Imported by main.py to drive 'Used in signals' dashboard tags.
SIGNALS_ACTIVE: frozenset[str] = frozenset({
    "DFII10", "T10YIE", "BAMLH0A0HYM2", "BAMLC0A0CM",
    "T10Y2Y", "T10Y3M",
    "CFNAI", "DFEDTARU", "M2SL", "PERMIT", "DTWEXBGS",
})


def _ticker_group(ticker: str, config: dict) -> Optional[str]:
    for gname, entries in config.get("watchlist", {}).items():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("ticker") == ticker:
                    return gname
    return None


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
    if key := os.environ.get("FRED_API_KEY"):
        cfg.setdefault("macro", {})["fred_api_key"] = key
    return cfg


# ---------------------------------------------------------------------------
# VIX helpers (local DB)
# ---------------------------------------------------------------------------

def _vix_from_db(days: int = 5) -> Optional[float]:
    """Return the latest VIX close stored in the local price_data table."""
    try:
        vix_df = get_price_df("^VIX", days=days)
        if vix_df.empty:
            return None
        return float(vix_df["close"].iloc[-1])
    except Exception:
        return None


def _classify_vix(level: float) -> tuple[str, int]:
    """VIX regime label and score: low/normal → bullish bias, high/extreme → bearish."""
    if level < 12:
        return "low", 1
    if level < 20:
        return "normal", 0
    if level < 30:
        return "high", -1
    return "extreme", -1


# ---------------------------------------------------------------------------
# Yield curve helpers (kept for backward compatibility with indicators table)
# ---------------------------------------------------------------------------

def _yield_curve_from_fred(api_key: str, lookback_days: int = 10) -> Optional[float]:
    """Fetch the latest T10Y2Y spread from FRED directly (fallback when DB empty)."""
    try:
        from fredapi import Fred
        fred = Fred(api_key=api_key)
        start = (datetime.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        series = fred.get_series("T10Y2Y", observation_start=start).dropna()
        return float(series.iloc[-1]) if not series.empty else None
    except Exception as exc:
        logger.warning("FRED T10Y2Y fallback fetch failed: %s", exc)
        return None


def _classify_yield_curve(spread: float) -> tuple[str, int]:
    """Map 10Y-2Y spread to regime label and score."""
    if spread > 0.5:
        return "normal", 1
    if spread >= -0.1:
        return "flat", 0
    return "inverted", -1


# ---------------------------------------------------------------------------
# Per-series scoring rules
# ---------------------------------------------------------------------------

def _score_fred_series(
    series_id: str, value: float, pct_30d: float, z_score: float,
    series_meta: dict,
) -> int:
    """Return -1 / 0 / +1 for a FRED series based on its relationship to risk assets.

    All thresholds below treat the series in its native FRED units:
    - BAMLH0A0HYM2 / BAMLC0A0CM are in percent (1.0 = 100 bps).
    - Yield spreads (T10Y2Y, T10Y3M, DFII10, T10YIE) are in percent.
    - CFNAI is a dimensionless composite index (Chicago Fed); > 0 = above-trend growth.
    - PERMIT is total housing permits in thousands (seasonally adjusted annual rate).
    - BOGMBASE / M2SL are billions of USD — only their pct_change matters.
    - series_meta may include _consecutive_declining (int) for momentum-based series.
    """
    if series_id == "DFII10":
        # Real yields: low / negative = cheap money = bullish for equities & gold
        if value < 0.5 and z_score < 0.5:
            return 1
        if value > 2.0 or z_score > 1.0:
            return -1
        return 0

    if series_id == "T10YIE":
        # Breakeven inflation: 1.5-2.5% = healthy; outside range → headwind
        if 1.5 <= value <= 2.5:
            return 1
        if value < 1.0 or value > 3.5:
            return -1
        return 0

    if series_id == "BAMLH0A0HYM2":
        # HY OAS (percent; 1.0 = 100 bps). Widen = stress.
        stress_pct  = float(series_meta.get("stress_threshold_pct", 5.0))
        jump_pct    = float(series_meta.get("stress_30d_jump_pct",   1.0))
        if value < 3.0 and (pct_30d or 0) < 10:
            return 1
        if value > stress_pct or (pct_30d or 0) > jump_pct * 100 / 3.0:
            return -1  # rapid widening: >1% absolute in 30d
        return 0

    if series_id == "BAMLC0A0CM":
        # IG OAS (percent; 1.0 = 100 bps)
        if value < 0.8:
            return 1
        if value > 1.5:
            return -1
        return 0

    if series_id == "M2SL":
        # M2: growing = supportive of asset prices, contracting = bearish
        if (pct_30d or 0) > 0.3:
            return 1
        if (pct_30d or 0) < -0.3:
            return -1
        return 0

    if series_id == "DPCREDIT":
        # Discount window: sudden spike = banking stress
        if z_score > 2.0:
            return -1
        if z_score < 0.5:
            return 1
        return 0

    if series_id == "T10Y2Y":
        # 10Y-2Y: normal term structure = risk-on; inversion = recession signal
        if value > 0.5:
            return 1
        if value < -0.1:
            return -1
        return 0

    if series_id == "T10Y3M":
        # 10Y-3M: inverts earlier than 10Y-2Y and is often a better recession predictor
        if value > 0.5:
            return 1
        if value < 0.0:
            return -1
        return 0

    if series_id == "CFNAI":
        # Chicago Fed National Activity Index: composite of 85 monthly indicators.
        # > 0 = above-trend growth; < -0.70 = increasing recession likelihood.
        if value > 0:
            return 1
        if value <= -0.70:
            return -1
        return 0

    if series_id == "PERMIT":
        # Housing permits (SAAR, thousands): leading indicator for construction and economy.
        # Monthly series so pct_30d reflects one-month change.
        if (pct_30d or 0) > 1.0:   # rising trend (>1 % month-over-month)
            return 1
        if (pct_30d or 0) < -1.0:  # falling trend (<-1 % month-over-month)
            return -1
        return 0

    if series_id == "DTWEXBGS":
        # Trade-weighted dollar: rising dollar = headwind for multinational earnings & EM
        if z_score < -0.5:
            return 1   # dollar falling = bullish
        if z_score > 0.5:
            return -1  # dollar rising = bearish
        return 0

    if series_id == "BOGMBASE":
        # Monetary base: expanding (QE) = liquidity tailwind; contracting (QT) = headwind
        if (pct_30d or 0) > 0.3:
            return 1
        if (pct_30d or 0) < -0.3:
            return -1
        return 0

    if series_id == "DFEDTARU":
        # Fed Funds Target Rate upper bound.  A recent cut (pct_30d < 0) is
        # bullish; a recent hike is bearish.  When no change occurred, compare
        # the current level to the 2-year average: rate well above normal history
        # = restrictive = bearish; well below = accommodative = bullish.
        if (pct_30d or 0) < -2.0:   # rate cut (≥ 25 bps) in last observation period
            return 1
        if (pct_30d or 0) > 2.0:    # rate hike in last observation period
            return -1
        if z_score < -1.0:           # rate materially below 2y average (accommodative)
            return 1
        if z_score > 1.0:            # rate materially above 2y average (restrictive)
            return -1
        return 0

    logger.debug("No scoring rule for FRED series %s — scoring neutral", series_id)
    return 0


# ---------------------------------------------------------------------------
# DB upsert helper
# ---------------------------------------------------------------------------

def _upsert_macro_indicator(
    series_id: str,
    date: datetime,
    value: float,
    pct_change_30d: Optional[float],
    z_score_2y: float,
    score: int,
) -> None:
    engine = get_engine()
    stmt = sqlite_insert(MacroIndicator).values(
        series_id=series_id,
        date=date,
        value=value,
        pct_change_30d=pct_change_30d,
        z_score_2y=z_score_2y,
        score=score,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["series_id", "date"],
        set_={
            "value":          stmt.excluded.value,
            "pct_change_30d": stmt.excluded.pct_change_30d,
            "z_score_2y":     stmt.excluded.z_score_2y,
            "score":          stmt.excluded.score,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)


# ---------------------------------------------------------------------------
# FRED fetch & store — called once per EOD scheduler run
# ---------------------------------------------------------------------------

def fetch_and_store_macro_indicators(config: Optional[dict] = None) -> dict[str, bool]:
    """Fetch all configured FRED series, compute stats, upsert to macro_indicators.

    Fetches 2 years of history per series to compute the z-score baseline.
    Only the latest observation is written to the DB; historical rows are used
    in-memory to derive pct_change_30d and z_score_2y.

    Returns:
        Dict mapping series_id → True (stored) or False (failed).
    """
    if config is None:
        config = _load_config()

    macro_cfg   = config.get("macro", {})
    fred_key    = macro_cfg.get("fred_api_key", "") or os.environ.get("FRED_API_KEY", "")
    series_cfg  = macro_cfg.get("fred_series", {})

    if not fred_key:
        logger.info("No FRED API key — skipping macro indicator fetch.")
        return {}
    if not series_cfg:
        logger.info("No fred_series configured — skipping macro indicator fetch.")
        return {}

    try:
        from fredapi import Fred
    except ImportError:
        logger.warning("fredapi not installed — skipping macro indicator fetch.")
        return {}

    fred = Fred(api_key=fred_key)
    start = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")
    results: dict[str, bool] = {}

    for series_id, series_meta in series_cfg.items():
        try:
            raw = fred.get_series(series_id, observation_start=start).dropna()
            if len(raw) < 5:
                logger.warning("Insufficient FRED data for %s (%d points).", series_id, len(raw))
                results[series_id] = False
                continue

            # Monthly series have far fewer observations than daily ones
            is_monthly = len(raw) < 100
            lookback   = 1 if is_monthly else 30

            current_value = float(raw.iloc[-1])
            pct_chg = (
                float(raw.pct_change(periods=lookback).iloc[-1] * 100)
                if len(raw) > lookback else None
            )

            mean_2y = float(raw.mean())
            std_2y  = float(raw.std())
            z_score = float((current_value - mean_2y) / std_2y) if std_2y > 0 else 0.0

            aug_meta = dict(series_meta)
            score = _score_fred_series(
                series_id, current_value, pct_chg or 0.0, z_score, aug_meta
            )

            # Convert pandas Timestamp → datetime
            last_date = raw.index[-1]
            if hasattr(last_date, "to_pydatetime"):
                last_date = last_date.to_pydatetime()

            _upsert_macro_indicator(series_id, last_date, current_value, pct_chg, z_score, score)
            results[series_id] = True
            logger.info(
                "Macro indicator %s: value=%.4f pct30d=%s z=%.2f score=%d",
                series_id, current_value,
                f"{pct_chg:.2f}%" if pct_chg is not None else "N/A",
                z_score, score,
            )
        except Exception as exc:
            logger.warning("Failed to fetch FRED series %s: %s", series_id, exc)
            results[series_id] = False

    successes = sum(1 for ok in results.values() if ok)
    if successes:
        from data.database import set_metadata as _set_meta
        _set_meta("last_fred_run", datetime.today().strftime("%Y-%m-%dT%H:%M:%S"))
        logger.info("Stamped last_fred_run (%d/%d series fetched).", successes, len(results))

    return results


# ---------------------------------------------------------------------------
# Read stored macro data from DB
# ---------------------------------------------------------------------------

def _get_stored_rows(config: dict) -> dict[str, dict]:
    """Return the latest stored row for each configured series_id."""
    series_ids = list(config.get("macro", {}).get("fred_series", {}).keys())
    if not series_ids:
        return {}

    engine = get_engine()
    rows: dict[str, dict] = {}
    try:
        with engine.connect() as conn:
            for sid in series_ids:
                row = conn.execute(
                    text(
                        "SELECT series_id, date, value, pct_change_30d, z_score_2y, score "
                        "FROM macro_indicators WHERE series_id = :sid "
                        "ORDER BY date DESC LIMIT 1"
                    ),
                    {"sid": sid},
                ).mappings().first()
                if row:
                    rows[sid] = dict(row)
    except Exception as exc:
        logger.warning("Could not read macro_indicators: %s", exc)
    return rows


# ---------------------------------------------------------------------------
# Dashboard data builder — used by the Daily Briefing UI
# ---------------------------------------------------------------------------

def get_macro_dashboard(config: Optional[dict] = None) -> dict:
    """Return a structured snapshot of all macro indicators for the briefing UI.

    Reads from the macro_indicators table (no FRED API calls).  Returns an
    empty-but-valid dict if no data has been stored yet.

    Dashboard structure:
    - real_yield, real_yield_trend, real_yield_z
    - breakeven, breakeven_trend
    - hy_oas_pct, hy_oas_bps, hy_oas_pct_30d, credit_status
    - ig_oas_pct, ig_oas_bps
    - t10y2y, t10y2y_shape, t10y3m, t10y3m_shape
    - composite_score  (float ∈ [-1, +1], not discretised)
    - n_bullish, n_bearish, n_neutral, n_series
    - summary          (plain-English one-liner)
    - series_data      (raw dict of all stored rows)
    - has_data         (bool — False when macro_indicators is empty)
    """
    if config is None:
        config = _load_config()

    rows = _get_stored_rows(config)

    def _get(sid: str, field: str, default=None):
        return rows.get(sid, {}).get(field, default)

    # ── Real yields (DFII10) ─────────────────────────────────────────────────
    real_yield   = _get("DFII10", "value")
    real_yield_z = _get("DFII10", "z_score_2y")
    real_yield_trend = (
        "falling" if (real_yield_z is not None and real_yield_z < -0.3) else
        "rising"  if (real_yield_z is not None and real_yield_z >  0.3) else
        "flat"
    )

    # ── Breakeven inflation (T10YIE) ─────────────────────────────────────────
    breakeven   = _get("T10YIE", "value")
    breakeven_z = _get("T10YIE", "z_score_2y")
    breakeven_trend = (
        "rising"  if (breakeven_z is not None and breakeven_z >  0.3) else
        "falling" if (breakeven_z is not None and breakeven_z < -0.3) else
        "flat"
    )

    # ── Credit spreads ───────────────────────────────────────────────────────
    hy_oas_pct    = _get("BAMLH0A0HYM2", "value")
    hy_oas_pct30d = _get("BAMLH0A0HYM2", "pct_change_30d")
    hy_oas_bps    = round(hy_oas_pct * 100, 0) if hy_oas_pct is not None else None

    hy_series_meta = config.get("macro", {}).get("fred_series", {}).get("BAMLH0A0HYM2", {})
    stress_thr = float(hy_series_meta.get("stress_threshold_pct", 5.0))

    credit_status = None
    if hy_oas_pct is not None:
        if hy_oas_pct > stress_thr or (hy_oas_pct30d or 0) > float(hy_series_meta.get("stress_30d_jump_pct", 1.0)) * 100 / 3.0:
            credit_status = "STRESSED"
        elif hy_oas_pct > 3.0:
            credit_status = "ELEVATED"
        else:
            credit_status = "NORMAL"

    ig_oas_pct = _get("BAMLC0A0CM", "value")
    ig_oas_bps = round(ig_oas_pct * 100, 0) if ig_oas_pct is not None else None

    # ── Leading indicators and other non-specialty series (dynamic) ─────────────
    # Series that have bespoke rendering in the UI; everything else → extra_series.
    _SPECIALTY = frozenset({
        "DFII10", "T10YIE",
        "BAMLH0A0HYM2", "BAMLC0A0CM",
        "T10Y2Y", "T10Y3M",
        "DFEDTARU", "PERMIT",
    })

    fred_series_cfg = (config or {}).get("macro", {}).get("fred_series", {})

    def _extra_context(sid: str, val: float, pct: Optional[float], sc: int) -> str:
        if sid == "CFNAI":
            if val > 0:
                return "Above-trend growth"
            if val > -0.70:
                return "Below-trend growth"
            return "Recession signal"
        if sid == "DPCREDIT":
            ffr = _get("DFEDTARU", "value")
            if ffr is not None:
                spread = val - ffr
                if abs(spread) <= 0.25:
                    return "At Fed Funds rate — normal conditions"
                if spread > 0.25:
                    return f"Above Fed Funds by {spread:.2f}% — tightening signal"
                return f"Below Fed Funds by {abs(spread):.2f}%"
        return {1: "Positive signal", -1: "Negative signal", 0: "Neutral"}.get(sc, "Neutral")

    extra_series: list[dict] = []
    for sid, scfg in fred_series_cfg.items():
        if sid in _SPECIALTY:
            continue
        val  = _get(sid, "value")
        pct  = _get(sid, "pct_change_30d")
        sc   = int(_get(sid, "score") or 0)
        label = scfg.get("label", sid)
        trend = ("rising" if (pct or 0) > 0 else "falling" if (pct or 0) < 0 else "flat")
        if val is None:
            display_value = "—"
        elif sid == "DPCREDIT":
            display_value = f"{val:.2f}%"
        elif abs(val) > 100:
            display_value = (f"{pct:+.1f}% MoM" if pct is not None else "—")
        else:
            display_value = f"{val:+.2f}"
        extra_series.append({
            "series_id":     sid,
            "label":         label,
            "display_value": display_value,
            "trend":         trend,
            "context":       _extra_context(sid, val, pct, sc) if val is not None else "No data",
            "score":         sc,
            "in_signals":    sid in SIGNALS_ACTIVE,
        })

    permit_val    = _get("PERMIT",  "value")
    permit_pct1m  = _get("PERMIT",  "pct_change_30d")
    permit_score  = _get("PERMIT",  "score")

    # ── Fed Funds Target Rate (DFEDTARU) ──────────────────────────────────────
    fed_rate       = _get("DFEDTARU", "value")
    fed_rate_pct30 = _get("DFEDTARU", "pct_change_30d")
    fed_rate_z     = _get("DFEDTARU", "z_score_2y")
    fed_rate_score = _get("DFEDTARU", "score")
    # Trend: "cut" / "hiked" / "unchanged"
    if fed_rate_pct30 is not None and fed_rate_pct30 < -2.0:
        fed_rate_trend = "cut"
    elif fed_rate_pct30 is not None and fed_rate_pct30 > 2.0:
        fed_rate_trend = "hiked"
    else:
        fed_rate_trend = "unchanged"

    # ── Yield curve ─────────────────────────────────────────────────────────
    t10y2y = _get("T10Y2Y", "value")
    t10y3m = _get("T10Y3M", "value")

    def _yc_shape_10y2y(val: Optional[float]) -> Optional[str]:
        if val is None:
            return None
        if val > 0.5:
            return "normal"
        if val >= -0.1:
            return "flat"
        return "inverted"

    def _yc_shape_10y3m(val: Optional[float]) -> Optional[str]:
        if val is None:
            return None
        if val > 0.5:
            return "normal"
        if val >= 0.0:
            return "flat"
        return "inverted"

    # ── Composite macro health score ─────────────────────────────────────────
    # Include VIX score alongside stored FRED series scores
    scores: list[int] = []
    vix_level = _vix_from_db()
    if vix_level is not None:
        _, vix_sc = _classify_vix(vix_level)
        scores.append(vix_sc)

    for row in rows.values():
        sc = row.get("score")
        if sc is not None:
            scores.append(int(sc))

    composite = sum(scores) / len(scores) if scores else 0.0
    n_bullish  = sum(1 for s in scores if s > 0)
    n_bearish  = sum(1 for s in scores if s < 0)
    n_neutral  = sum(1 for s in scores if s == 0)

    # ── Plain-English summary ────────────────────────────────────────────────
    parts: list[str] = []
    if real_yield is not None:
        parts.append(
            f"Real yields {real_yield_trend} ({real_yield:+.2f}%)"
        )
    if breakeven is not None:
        parts.append(f"inflation expectations {breakeven_trend} ({breakeven:.2f}%)")
    if credit_status:
        parts.append(f"credit spreads {credit_status.lower()}")
    yc_shape = _yc_shape_10y2y(t10y2y)
    if yc_shape:
        parts.append(f"yield curve {yc_shape}")
    cfnai_entry = next((e for e in extra_series if e["series_id"] == "CFNAI"), None)
    if cfnai_entry and cfnai_entry["display_value"] != "—":
        parts.append(f"CFNAI {cfnai_entry['trend']} ({cfnai_entry['display_value']}) — {cfnai_entry['context'].lower()}")

    if composite >= 0.25:
        env = "supportive environment for risk assets"
    elif composite <= -0.25:
        env = "cautious environment for risk assets"
    else:
        env = "mixed signals for risk assets"

    summary = (", ".join(parts).capitalize() + " — " + env) if parts else env.capitalize()

    return {
        "has_data":          bool(rows),
        "real_yield":        real_yield,
        "real_yield_trend":  real_yield_trend,
        "real_yield_z":      real_yield_z,
        "breakeven":         breakeven,
        "breakeven_trend":   breakeven_trend,
        "hy_oas_pct":        hy_oas_pct,
        "hy_oas_bps":        hy_oas_bps,
        "hy_oas_pct_30d":    hy_oas_pct30d,
        "credit_status":     credit_status,
        "ig_oas_pct":        ig_oas_pct,
        "ig_oas_bps":        ig_oas_bps,
        "t10y2y":            t10y2y,
        "t10y2y_shape":      _yc_shape_10y2y(t10y2y),
        "t10y3m":            t10y3m,
        "t10y3m_shape":      _yc_shape_10y3m(t10y3m),
        "extra_series":      extra_series,
        "permit":            permit_val,
        "permit_pct1m":      permit_pct1m,
        "permit_score":      permit_score,
        "fed_rate":          fed_rate,
        "fed_rate_trend":    fed_rate_trend,
        "fed_rate_score":    fed_rate_score,
        "fed_rate_z":        fed_rate_z,
        "composite_score":   round(composite, 3),
        "n_bullish":         n_bullish,
        "n_bearish":         n_bearish,
        "n_neutral":         n_neutral,
        "n_series":          len(scores),
        "summary":           summary,
        "series_data":       rows,
    }


# ---------------------------------------------------------------------------
# compute_macro — called per-ticker during analysis/confluence runs
# ---------------------------------------------------------------------------

def compute_macro(ticker: str, config: Optional[dict] = None) -> dict:
    """Compute the macro layer score for a single ticker.

    Reads stored macro_indicators rows (fast, no network calls).  Falls back to
    a direct FRED fetch of T10Y2Y if the table is empty (e.g. before the first
    EOD scheduler run).  VIX is always sourced from the local price_data table.

    Scoring:
        Composite = average of all available indicator scores (VIX + stored series).
        Classified: ≥ 0.25 → +1, ≤ -0.25 → -1, else 0.

    Macro assets (^VIX, TLT, IEF, HYG) are returned as neutral to avoid
    self-referential scoring.

    Returns:
        Dict with 'score' (-1/0/1) and raw values for indicators table storage.
    """
    if config is None:
        config = _load_config()

    empty = {
        "score":              0,
        "yield_curve_spread": None,
        "yield_curve_regime": None,
        "vix_level":          None,
        "vix_regime":         None,
    }

    if ticker in _MACRO_TICKERS:
        return empty

    # VIX (always from local DB)
    vix_level = _vix_from_db()
    vix_regime, vix_score = (None, 0)
    if vix_level is not None:
        vix_regime, vix_score = _classify_vix(vix_level)

    # Stored FRED indicator scores
    rows      = _get_stored_rows(config)
    all_scores = [vix_score]

    for row in rows.values():
        sc = row.get("score")
        if sc is not None:
            all_scores.append(int(sc))

    # Yield curve spread/regime for backward-compat storage in indicators table
    yc_spread = None
    yc_regime = None

    t10y2y_row = rows.get("T10Y2Y")
    if t10y2y_row and t10y2y_row.get("value") is not None:
        yc_spread = float(t10y2y_row["value"])
        yc_regime, _ = _classify_yield_curve(yc_spread)
    else:
        # Fallback: direct FRED fetch (only when macro_indicators is empty)
        fred_key = config.get("macro", {}).get("fred_api_key", "")
        if fred_key:
            yc_spread = _yield_curve_from_fred(fred_key)
            if yc_spread is not None:
                yc_regime, yc_score_fallback = _classify_yield_curve(yc_spread)
                all_scores.append(yc_score_fallback)

    # Base composite score
    avg   = sum(all_scores) / len(all_scores) if all_scores else 0.0
    group = _ticker_group(ticker, config)

    # Per-ticker adjustments from FRED indicators that weren't previously routed
    ticker_adj = 0.0

    # CFNAI — economic cycle modifier for equity assets
    cfnai_val = rows.get("CFNAI", {}).get("value")
    if cfnai_val is not None and group in _EQUITY_GROUPS:
        if cfnai_val > 0.20:
            ticker_adj += 0.10      # above-trend growth → bullish equities
        elif cfnai_val < -0.70:
            ticker_adj -= 0.20      # recession signal → strongly bearish
        elif cfnai_val < -0.20:
            ticker_adj -= 0.10      # below-trend → mildly bearish

    # DFEDTARU — rate-sensitive asset scoring
    dfedtaru_pct = rows.get("DFEDTARU", {}).get("pct_change_30d")
    if dfedtaru_pct is not None:
        if dfedtaru_pct > 2.0:      # rates rising (hike)
            if ticker in _RATE_BEARISH:
                ticker_adj -= 0.10
            elif ticker in _RATE_BULLISH:
                ticker_adj += 0.10
        elif dfedtaru_pct < -2.0:   # rates falling (cut)
            if ticker in _RATE_BEARISH:
                ticker_adj += 0.10
            elif ticker in _RATE_BULLISH:
                ticker_adj -= 0.10

    # M2SL — liquidity tailwind/headwind for crypto and gold
    m2_pct = rows.get("M2SL", {}).get("pct_change_30d")
    if m2_pct is not None:
        is_crypto = group in _CRYPTO_GROUPS
        if m2_pct > 0.5:
            if is_crypto:
                ticker_adj += 0.10
            elif ticker in _GOLD_SET:
                ticker_adj += 0.05
        elif m2_pct < -0.5:
            if is_crypto:
                ticker_adj -= 0.10

    # PERMIT — housing permits trend for real estate
    permit_pct = rows.get("PERMIT", {}).get("pct_change_30d")
    if permit_pct is not None and ticker in _REAL_ESTATE_SET:
        if permit_pct < -5.0:
            ticker_adj -= 0.10      # permits falling sharply → bearish real estate

    avg     += ticker_adj
    combined = 1 if avg >= 0.25 else (-1 if avg <= -0.25 else 0)

    return {
        "score":              combined,
        "yield_curve_spread": yc_spread,
        "yield_curve_regime": yc_regime,
        "vix_level":          vix_level,
        "vix_regime":         vix_regime,
    }
