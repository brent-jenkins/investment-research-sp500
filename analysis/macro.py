"""Macro indicators: FRED series (real yields, credit spreads, yield curve, liquidity, dollar)
plus local VIX.  Data is fetched once per EOD scheduler run and stored in the
macro_indicators table; compute_macro() reads from the DB so it stays fast during
per-ticker analysis loops.
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml
from sqlalchemy import text

from data.database import get_engine
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

    if series_id == "ECBDFR":
        # ECB Deposit Facility Rate: identical "cut is bullish / hike is
        # bearish" logic to DFEDTARU below, applied to the Eurozone's policy
        # rate instead of the Fed's.
        if (pct_30d or 0) < -2.0:
            return 1
        if (pct_30d or 0) > 2.0:
            return -1
        if z_score < -1.0:
            return 1
        if z_score > 1.0:
            return -1
        return 0

    if series_id == "IRLTLT01DEM156N":
        # Germany 10Y Bund yield: unlike DFII10 (a *real* yield with a
        # natural "cheap money" band), a nominal Bund yield has no inherent
        # good/bad level — direction depends entirely on context, so this is
        # scored purely off its own 2-year z-score. Well above its own recent
        # history = tightening/inflation-risk signal (bearish); well below =
        # easing signal (bullish).
        if z_score > 1.0:
            return -1
        if z_score < -1.0:
            return 1
        return 0

    if series_id == "IRSTCI01JPM156N":
        # Japan interbank overnight rate ("Interest Rates: Immediate Rates:
        # Call Money/Interbank Rate: Total for Japan") — used as a BoJ
        # policy-rate proxy (replaced IRSTCB01JPM156N 2026-07-10, which
        # stopped updating Dec 2023). This is the actual market call-money
        # rate, not the BoJ's announced target — expect it to sit slightly
        # below the announced target (e.g. ~0.7% vs. an announced 1.0% as
        # of 2026-07) rather than matching it exactly; that gap is normal
        # and, if anything, more representative of real borrowing costs and
        # carry-trade dynamics than the headline target figure. A hike
        # raises carry-trade-unwind risk for global risk assets (see e.g.
        # the August 2024 selloff); a cut eases it — same direction/
        # threshold logic as ECBDFR/DFEDTARU.
        if (pct_30d or 0) < -2.0:
            return 1
        if (pct_30d or 0) > 2.0:
            return -1
        if z_score < -1.0:
            return 1
        if z_score > 1.0:
            return -1
        return 0

    if series_id == "CHNGDPRAPSMEI":
        # China real GDP (constant prices, annual level) — replaced
        # CHNGDPNQDSMEI 2026-07-10, which stopped updating Jul 2023. A level
        # series like M2SL/BOGMBASE, so only year-over-year growth is
        # meaningful, not the level itself. Higher growth = bullish for
        # commodities/EM exposure (FXI, EEM).
        if (pct_30d or 0) > 1.0:
            return 1
        if (pct_30d or 0) < -1.0:
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
# Regional grouping — international FRED series are weighted by how much of
# the actual portfolio's exposure that region represents, rather than every
# series (US and international alike) counting equally in one flat average.
# See CLAUDE.md's "Macro module — regional grouping" section for rationale.
# ---------------------------------------------------------------------------

_DEFAULT_REGIONAL_WEIGHTS = {"us": 0.60, "europe": 0.25, "asia": 0.15}

# Display-only staleness thresholds (days) per region — international series
# (especially quarterly ones like China GDP) can lag by more than one
# reporting period, so they get a much longer grace period than the mostly
# daily/monthly US series before being flagged stale in the dashboard. This
# never blocks scoring — a stale-but-available value is still used (see
# fetch_and_store_macro_indicators()'s longer international lookback below).
_STALE_THRESHOLD_DAYS = {"us": 35, "europe": 90, "asia": 90}

# Per-series overrides where the region default doesn't fit a specific
# series' actual reporting cadence.
#
# CHNGDPRAPSMEI is annual, and FRED dates each annual observation at the
# *start* of the year it covers (e.g. the "2025" value is stored dated
# 2025-01-01), not its publish date — verified against production 2026-07-10:
# the 2025 observation (published ~March 2026, ~4-5 months old in real terms)
# was already showing as "555d old" purely from age-since-period-start
# arithmetic (2025-01-01 -> 2026-07-10 is ~555 days on its own). An initial
# 400-day override was too tight for this dating convention: OECD's ~14-15
# month publish lag means a freshly-published observation can already be
# ~430+ days old by FRED's date-stamp, and it then remains the latest
# available row for up to another ~12 months until next year's print lands —
# a normal, non-stale worst case of close to 800 days between real updates.
# 800 comfortably covers that full cycle while still catching genuine
# multi-year discontinuation (the predecessor series this replaced went
# 1,105 days without an update before being swapped out).
_STALE_THRESHOLD_DAYS_SERIES_OVERRIDE = {"CHNGDPRAPSMEI": 800}


def _regional_weights(config: dict) -> dict[str, float]:
    weights = config.get("signals", {}).get("macro_regional_weights")
    return weights if weights else dict(_DEFAULT_REGIONAL_WEIGHTS)


def _series_region(series_id: str, config: dict) -> str:
    """Region a FRED series belongs to. Defaults to 'us' when the series has
    no 'region' field — backward compatible with config written before
    regional grouping existed."""
    meta = config.get("macro", {}).get("fred_series", {}).get(series_id, {})
    return meta.get("region") or "us"


def _row_age_days(row: Optional[dict]) -> Optional[int]:
    """Age in days of a stored macro_indicators row's date, or None if unknown."""
    if not row:
        return None
    d = row.get("date")
    if d is None:
        return None
    try:
        ts = pd.Timestamp(d)
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)
        return (datetime.utcnow() - ts.to_pydatetime()).days
    except Exception:
        return None


def _regional_macro_score(
    rows: dict[str, dict],
    vix_score: Optional[int],
    config: dict,
    extra_scores_by_region: Optional[dict[str, list[int]]] = None,
) -> dict:
    """Group per-series FRED scores (+ VIX, always 'us') by region, then
    combine the regional averages via config's macro_regional_weights.

    Graceful degradation: a configured region with no available data is
    dropped and its weight is redistributed proportionally across the
    remaining regions that do have data, rather than treating missing data
    as a neutral 0 (a genuinely-neutral European reading and "Europe fetch
    failed entirely" should not look identical) or raising.

    Returns:
        {"region_scores": {region: avg}, "region_weights": {region: weight
        actually applied}, "weighted_score": float}
    """
    region_scores_raw: dict[str, list[int]] = {}

    def _add(region: str, score: int) -> None:
        region_scores_raw.setdefault(region, []).append(score)

    if vix_score is not None:
        _add("us", vix_score)  # VIX is a US-sourced index

    for sid, row in rows.items():
        sc = row.get("score")
        if sc is not None:
            _add(_series_region(sid, config), int(sc))

    for region, extra_scores in (extra_scores_by_region or {}).items():
        for sc in extra_scores:
            _add(region, sc)

    region_avgs = {
        region: sum(scores) / len(scores)
        for region, scores in region_scores_raw.items()
        if scores
    }

    configured_weights = _regional_weights(config)
    available = {r: w for r, w in configured_weights.items() if r in region_avgs and w > 0}
    total_w = sum(available.values())

    if total_w > 0:
        applied_weights = {r: w / total_w for r, w in available.items()}
    elif region_avgs:
        # No configured region with a positive weight has data (e.g. every
        # weight is 0, or region_avgs contains a region key not present in
        # macro_regional_weights at all) — fall back to a flat average across
        # whatever regions do have data rather than silently returning 0.
        applied_weights = {r: 1.0 / len(region_avgs) for r in region_avgs}
    else:
        applied_weights = {}

    weighted_score = sum(region_avgs[r] * w for r, w in applied_weights.items())

    return {
        "region_scores":  region_avgs,
        "region_weights": applied_weights,
        "weighted_score": weighted_score,
    }


# ---------------------------------------------------------------------------
# DB upsert helper
# ---------------------------------------------------------------------------

_MACRO_UPSERT_SQL = text("""
    INSERT INTO macro_indicators (series_id, date, value, pct_change_30d, z_score_2y, score)
    VALUES (:series_id, :date, :value, :pct_change_30d, :z_score_2y, :score)
    ON CONFLICT(series_id, date) DO UPDATE SET
        value          = excluded.value,
        pct_change_30d = excluded.pct_change_30d,
        z_score_2y     = excluded.z_score_2y,
        score          = excluded.score
""")


def _bulk_upsert_macro_rows(rows: list[dict]) -> None:
    """Upsert many macro_indicators rows in one statement.

    Uses raw SQL with a list-of-dicts parameter set (conn.execute(text(...),
    rows)) rather than sqlite_insert(MacroIndicator).values([...]) — the
    same fix, for the same reason, as analysis/backtest.py's
    _store_signals_batch(): the SQLAlchemy Core/ORM multi-row VALUES + ON
    CONFLICT construct measured far slower purely from query-building
    overhead, independent of row count. Passing a list of dicts here instead
    dispatches through the DBAPI's executemany() with one prepared statement
    reused across all rows. Matters here because a full historical backfill
    can write thousands of rows for a single daily series.
    """
    if not rows:
        return
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(_MACRO_UPSERT_SQL, rows)


def _last_stored_date(series_id: str) -> Optional[datetime]:
    """Most recent stored date for series_id in macro_indicators, or None if
    the series has no history yet (fresh install, or newly added to
    config.yaml and never backfilled or fetched before)."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT MAX(date) AS d FROM macro_indicators WHERE series_id = :sid"),
                {"sid": series_id},
            ).mappings().first()
    except Exception:
        return None
    d = row.get("d") if row else None
    if not d:
        return None
    try:
        ts = pd.Timestamp(d)
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)
        return ts.to_pydatetime()
    except Exception:
        return None


def _scored_df_to_rows(series_id: str, scored: pd.DataFrame) -> list[dict]:
    """Convert a _fetch_scored_series() DataFrame into macro_indicators row dicts.

    Dates are formatted via strftime("%Y-%m-%d %H:%M:%S.%f") rather than
    passed through as raw datetime objects. This bulk path writes through
    raw SQL (_bulk_upsert_macro_rows(), for executemany() performance — see
    its docstring), which bypasses SQLAlchemy's own DateTime type decorator
    and hands the DBAPI a plain Python datetime instead; sqlite3's default
    adapter renders that via isoformat(), which *omits* the fractional-
    second component entirely when microsecond == 0 (true for every one of
    these daily/monthly FRED observations). SQLAlchemy's own SQLite dialect,
    by contrast, always renders a DateTime column WITH the ".000000" suffix
    — so without this explicit strftime, this path silently wrote a
    different literal string format than every row inserted via the ORM
    (e.g. the pre-backfill single-snapshot fetches this table already had),
    mixing "YYYY-MM-DD HH:MM:SS" and "YYYY-MM-DD HH:MM:SS.000000" in the same
    column. pandas' to_datetime() caches a format from the first values it
    sees when applied to a whole column, then raises on the first value that
    doesn't match once dates from both formats interleave in date order —
    exactly what broke analysis/backtest.py:_load_macro_history()'s
    pd.to_datetime(df["date"]) the first time this was backfilled. The same
    class of bug is documented elsewhere in this codebase (see CLAUDE.md's
    "Cutoff comparison uses strftime, not isoformat()" note) — explicit
    strftime formatting matching the dialect's own convention is the fix
    every time it comes up.
    """
    rows = []
    for idx, row in scored.iterrows():
        d = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        pct = row["pct_change_30d"]
        rows.append({
            "series_id":      series_id,
            "date":           d.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "value":          float(row["value"]),
            "pct_change_30d": float(pct) if pd.notna(pct) else None,
            "z_score_2y":     float(row["z_score_2y"]),
            "score":          int(row["score"]),
        })
    return rows


# ---------------------------------------------------------------------------
# Shared FRED fetch + scoring core — used by both the incremental EOD fetch
# and the full historical backfill, so neither can silently drift from the
# other's scoring logic.
# ---------------------------------------------------------------------------

def _fetch_scored_series(
    fred,
    series_id: str,
    series_meta: dict,
    observation_start: str,
    observation_end: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """Fetch one FRED series over [observation_start, observation_end] and
    compute per-date value/pct_change_30d/z_score_2y/score, vectorized
    across the whole fetched window.

    z_score_2y is a TRAILING rolling statistic (window ending at each date —
    ~2 years for US series, ~6 years for international, matching the region
    lookback used elsewhere in this module), not a single whole-window mean/
    std applied to every date. Using the whole fetched span's statistics for
    every historical date would let an early date's z-score be computed
    using knowledge of years of *future* data relative to that date — a
    genuine look-ahead bias that would contaminate any backtest built on
    this history (this codebase's backtest engine is otherwise carefully
    look-ahead free — see analysis/backtest.py throughout). A rolling
    trailing window keeps every date's stats computed only from data
    available as of that date, mirroring what the live incremental fetch
    would actually have produced had it been run fresh on each of those
    historical days.

    Returns a DataFrame indexed by date with columns [value, pct_change_30d,
    z_score_2y, score], or None if there isn't enough data to score at all.
    """
    kwargs: dict = {"observation_start": observation_start}
    if observation_end:
        kwargs["observation_end"] = observation_end
    raw = fred.get_series(series_id, **kwargs).dropna()
    if len(raw) < 5:
        return None

    # Monthly/quarterly series have far fewer observations than daily ones
    is_monthly = len(raw) < 100
    lookback   = 1 if is_monthly else 30
    pct_chg    = raw.pct_change(periods=lookback) * 100

    region      = series_meta.get("region") or "us"
    window_days = 730 if region == "us" else 2190  # ~6 years for international — see module docstring
    roll_mean   = raw.rolling(f"{window_days}D").mean()
    roll_std    = raw.rolling(f"{window_days}D").std()
    z_score     = ((raw - roll_mean) / roll_std).where(roll_std > 0, 0.0).fillna(0.0)

    aug_meta = dict(series_meta)
    scores = [
        _score_fred_series(
            series_id, float(v),
            float(p) if pd.notna(p) else 0.0,
            float(z), aug_meta,
        )
        for v, p, z in zip(raw.to_numpy(), pct_chg.to_numpy(), z_score.to_numpy())
    ]

    return pd.DataFrame({
        "value":          raw.astype(float),
        "pct_change_30d": pct_chg,
        "z_score_2y":     z_score,
        "score":          scores,
    })


# ---------------------------------------------------------------------------
# Full historical backfill — one-off (or re-run whenever a series is added)
# ---------------------------------------------------------------------------

def backfill_macro_indicators(config: Optional[dict] = None, start_date: str = "2019-01-01") -> dict[str, int]:
    """One-off full-history backfill of every configured FRED series into
    macro_indicators, from start_date through today.

    fetch_and_store_macro_indicators() (the function that runs on every EOD
    job) historically only ever wrote each series' single latest
    observation — fine for keeping the live per-ticker macro score current,
    but it meant macro_indicators held almost no history beyond however long
    the EOD job itself had been running. Since analysis/backtest.py's
    _macro_scores() reads this exact table for its own vectorized macro
    scoring (see "Backtest mirroring" in CLAUDE.md's "Macro module" section),
    a 2019-present backtest was effectively macro-blind (VIX only, since VIX
    is read fresh from price_data rather than macro_indicators) for nearly
    its entire span. This function fixes that retroactively — run it via the
    Status page's "Backfill FRED History" button after a fresh deployment,
    or whenever a new series is added to config.yaml.

    Every date's pct_change_30d/z_score_2y/score is computed from a TRAILING
    window ending at that date (see _fetch_scored_series()'s docstring), not
    the whole fetched span's statistics — so the backfilled history stays
    look-ahead free and safe to feed into the backtest.

    Rate-limited to roughly one series per 0.5s between fetches (17 series
    -> ~8s of throttling, comfortably inside FRED's request limits) — see
    _fetch_scored_series() for the actual per-series FRED call.

    Returns:
        Dict mapping series_id -> number of rows written (0 on failure or
        insufficient data — a failed series never aborts the remaining ones).
    """
    if config is None:
        config = _load_config()

    macro_cfg  = config.get("macro", {})
    fred_key   = macro_cfg.get("fred_api_key", "") or os.environ.get("FRED_API_KEY", "")
    series_cfg = macro_cfg.get("fred_series", {})

    if not fred_key:
        logger.info("No FRED API key — skipping macro backfill.")
        return {}
    if not series_cfg:
        logger.info("No fred_series configured — skipping macro backfill.")
        return {}

    try:
        from fredapi import Fred
    except ImportError:
        logger.warning("fredapi not installed — skipping macro backfill.")
        return {}

    fred       = Fred(api_key=fred_key)
    today      = datetime.today().strftime("%Y-%m-%d")
    series_ids = list(series_cfg.keys())
    results: dict[str, int] = {}

    for i, series_id in enumerate(series_ids):
        series_meta = series_cfg[series_id]
        try:
            scored = _fetch_scored_series(
                fred, series_id, series_meta,
                observation_start=start_date, observation_end=today,
            )
            if scored is None or scored.empty:
                logger.warning("Backfill: insufficient FRED data for %s.", series_id)
                results[series_id] = 0
            else:
                rows = _scored_df_to_rows(series_id, scored)
                _bulk_upsert_macro_rows(rows)
                results[series_id] = len(rows)
                logger.info(
                    "Backfilling %s: fetched %d observations from %s to %s",
                    series_id, len(rows),
                    scored.index.min().strftime("%Y-%m-%d"),
                    scored.index.max().strftime("%Y-%m-%d"),
                )
        except Exception as exc:
            logger.warning("Backfill failed for %s: %s", series_id, exc)
            results[series_id] = 0

        if i < len(series_ids) - 1:
            time.sleep(0.5)  # FRED API rate-limit courtesy delay between series

    successes  = sum(1 for n in results.values() if n > 0)
    total_rows = sum(results.values())
    if successes:
        from data.database import set_metadata as _set_meta
        _set_meta("last_fred_backfill", datetime.today().strftime("%Y-%m-%dT%H:%M:%S"))
    logger.info(
        "Macro backfill complete: %d/%d series stored, %d total observations.",
        successes, len(results), total_rows,
    )

    return results


# ---------------------------------------------------------------------------
# FRED fetch & store — called once per EOD scheduler run
# ---------------------------------------------------------------------------

def fetch_and_store_macro_indicators(config: Optional[dict] = None) -> dict[str, bool]:
    """Fetch all configured FRED series and upsert new observations into
    macro_indicators. Runs on every EOD scheduler job.

    Incremental (2026-07-08): rather than writing only each series' single
    latest observation (the original behaviour — and still what happens on
    a series' very first-ever fetch, see below), this writes every
    observation from _fetch_scored_series()'s fetched window whose date
    falls on or after (last stored date − 7 days). The 7-day buffer catches
    FRED revisions to recent observations that can arrive after their
    original publish date. A series with no stored history yet (no backfill
    has been run, or the series was just added to config.yaml) falls back to
    writing the whole fetched window, matching backfill_macro_indicators()'s
    own behaviour — so a fresh deployment self-heals into full history
    incrementally even without ever clicking the Status page's backfill
    button, just far more slowly (one EOD run's worth of window at a time).

    This still fetches the full ~2-year (~6-year for international series)
    window from FRED every run, exactly as before — narrowing the *fetch
    itself* to only the days since the last stored date (a literal reading
    of "fetch since last stored date") was considered and rejected:
    pct_change_30d and z_score_2y are trailing-window statistics that need
    that much history as context to compute correctly for the newest
    date(s); fetching only the last week would leave nothing to compute a
    30-day change or a multi-year z-score against. Only the *write* volume
    is reduced by this change — the FRED API call cost per run is unchanged
    from before.

    Returns:
        Dict mapping series_id → True (stored — including "nothing new to
        write since last run") or False (failed).
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
    results: dict[str, bool] = {}

    for series_id, series_meta in series_cfg.items():
        try:
            region = series_meta.get("region") or "us"
            lookback_days = 730 if region == "us" else 2190  # ~6 years for international series — see docstring
            start = (datetime.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

            scored = _fetch_scored_series(fred, series_id, series_meta, observation_start=start)
            if scored is None or scored.empty:
                logger.warning("Insufficient FRED data for %s.", series_id)
                results[series_id] = False
                continue

            last_stored = _last_stored_date(series_id)
            if last_stored is not None:
                cutoff   = last_stored - timedelta(days=7)
                to_write = scored[scored.index >= cutoff]
            else:
                to_write = scored  # first-ever fetch for this series

            if to_write.empty:
                results[series_id] = True
                continue

            rows = _scored_df_to_rows(series_id, to_write)
            _bulk_upsert_macro_rows(rows)
            results[series_id] = True

            latest     = scored.iloc[-1]
            latest_pct = latest["pct_change_30d"]
            logger.info(
                "Macro indicator %s: value=%.4f pct30d=%s z=%.2f score=%d (%d row(s) written)",
                series_id, float(latest["value"]),
                f"{latest_pct:.2f}%" if pd.notna(latest_pct) else "N/A",
                float(latest["z_score_2y"]), int(latest["score"]), len(rows),
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
    - composite_score  (float ∈ [-1, +1], not discretised — regional weighted blend)
    - region_scores    (dict, e.g. {"us": 0.5, "europe": 0.25, "asia": 0.1} — only
                        regions with at least one scored series)
    - region_weights   (dict — the weights actually applied after any
                        graceful-degradation redistribution; sums to 1.0)
    - region_weights_config (dict — the nominal config.yaml weights, for display)
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
        region = _series_region(sid, config)
        trend = ("rising" if (pct or 0) > 0 else "falling" if (pct or 0) < 0 else "flat")
        if val is None:
            display_value = "—"
        elif sid == "DPCREDIT":
            display_value = f"{val:.2f}%"
        elif abs(val) > 100:
            display_value = (f"{pct:+.1f}% MoM" if pct is not None else "—")
        else:
            display_value = f"{val:+.2f}"
        context = _extra_context(sid, val, pct, sc) if val is not None else "No data"
        age_days = _row_age_days(rows.get(sid))
        stale_threshold = _STALE_THRESHOLD_DAYS_SERIES_OVERRIDE.get(sid, _STALE_THRESHOLD_DAYS.get(region, 90))
        is_stale = age_days is not None and age_days > stale_threshold
        if is_stale:
            context += f" (stale — {age_days}d old)"
        extra_series.append({
            "series_id":     sid,
            "label":         label,
            "display_value": display_value,
            "trend":         trend,
            "context":       context,
            "score":         sc,
            "in_signals":    sid in SIGNALS_ACTIVE,
            "region":        region,
            "stale":         is_stale,
            "age_days":      age_days,
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

    # ── Composite macro health score (regional weighting) ─────────────────────
    # Include VIX score alongside stored FRED series scores
    scores: list[int] = []
    vix_level = _vix_from_db()
    vix_sc: Optional[int] = None
    if vix_level is not None:
        _, vix_sc = _classify_vix(vix_level)
        scores.append(vix_sc)

    for row in rows.values():
        sc = row.get("score")
        if sc is not None:
            scores.append(int(sc))

    regional  = _regional_macro_score(rows, vix_sc, config)
    composite = regional["weighted_score"]
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
        "region_scores":     {r: round(v, 3) for r, v in regional["region_scores"].items()},
        "region_weights":    {r: round(v, 3) for r, v in regional["region_weights"].items()},
        "region_weights_config": _regional_weights(config),
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
        Composite = regional-weighted blend of all available indicator scores
        (VIX + stored series, grouped by region and combined via
        signals.macro_regional_weights — see _regional_macro_score() and
        CLAUDE.md's "Macro module — regional grouping" section).
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
    rows = _get_stored_rows(config)

    # Yield curve spread/regime for backward-compat storage in indicators table
    yc_spread = None
    yc_regime = None
    fallback_scores_by_region: dict[str, list[int]] = {}

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
                fallback_scores_by_region.setdefault(
                    _series_region("T10Y2Y", config), []
                ).append(yc_score_fallback)

    # Base composite score — regional weighted blend, not a flat average
    regional = _regional_macro_score(rows, vix_score, config, fallback_scores_by_region)
    avg      = regional["weighted_score"]
    group    = _ticker_group(ticker, config)

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
