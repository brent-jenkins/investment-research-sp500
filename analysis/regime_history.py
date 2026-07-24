"""Point-in-time regime/market-health snapshots for the Daily Briefing trend chart.

Every generate_signals() run (analysis/confluence.py) calls
record_regime_snapshot() at the end of the run with exactly the values that
run computed and acted on — ADX/VIX/S&P-vs-SMA200 from the same
detect_regime() call the rest of that run used, buy/sell counts from the
signals it just generated, and macro health from get_macro_dashboard(). This
keeps the chart always in sync with what the Daily Briefing showed at that
moment, per CLAUDE.md's convention for this feature.

Unlike every other table in this schema (indicators, signals, ...), which
upserts on (ticker, date) and therefore only ever keeps the *last* analysis
run of a calendar day, this table keeps one row per run (minute-truncated
timestamp) — the whole point of the chart is to show how these metrics moved
across the day's 2-3 scheduled runs, not just the day's closing snapshot.

backfill_regime_metrics_history() reconstructs day-level history (one row per
calendar date) from what already exists in indicators/signals/macro_indicators
for dates before this feature shipped — see its own docstring for exactly
what it can and cannot recover.
"""

import logging
from datetime import date as date_cls, datetime, time
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from analysis.regime import is_entry_allowed
from data.database import RegimeMetricsHistory, get_engine

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    """Load config.yaml from the project root — same per-module helper
    pattern every analysis/*.py file uses (see CLAUDE.md's "Configuration"
    section)."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

# Both T212 automation paths' allowed_entry_regimes gate new entries — a
# regime is treated as "entries allowed" for chart-shading purposes if
# *either* path's own gate admits it, independent of whether that path is
# currently toggled `enabled` (enabled is an operational on/off switch, not
# part of the strategy's own regime rules, and both paths are kept in sync
# at ["TRENDING_BULL"] per CLAUDE.md's "Regime entry gate" section anyway).
_ENTRY_GATE_SECTIONS = ("trading212_invest", "trading212_stocks")


def _regime_permits_entries(config: dict, regime_name: str) -> bool:
    """True if at least one T212 automation path's allowed_entry_regimes admits regime_name."""
    return any(
        is_entry_allowed(config, regime_name, section)
        for section in _ENTRY_GATE_SECTIONS
    )


def _count_signals(signal_rows: list[dict]) -> dict[str, int]:
    """Pure helper — tallies signal classifications from a generate_signals() run's
    per-ticker result list. No DB access, so it's unit-testable in isolation."""
    buy = sum(1 for r in signal_rows if r.get("signal") in ("BUY", "STRONG_BUY"))
    sell = sum(1 for r in signal_rows if r.get("signal") in ("SELL", "STRONG_SELL"))
    strong_buy = sum(1 for r in signal_rows if r.get("signal") == "STRONG_BUY")
    strong_sell = sum(1 for r in signal_rows if r.get("signal") == "STRONG_SELL")
    return {
        "buy_count": buy, "sell_count": sell,
        "strong_buy_count": strong_buy, "strong_sell_count": strong_sell,
        "total_signals": len(signal_rows),
    }


def record_regime_snapshot(
    regime_result: dict,
    signal_rows: list[dict],
    config: dict,
    macro_health_score: Optional[float] = None,
    timestamp: Optional[datetime] = None,
) -> None:
    """Persist one row capturing this generate_signals() run's regime/market metrics.

    Never raises — a failure here must not block the actual signal generation
    it's recording, matching the "never raises" contract used throughout
    brokers/ and analysis/risk.py for the same reason.

    Idempotent: upserts keyed on minute-truncated timestamp, so a rapid
    double-invocation (e.g. a double-clicked "Run Analysis Now") updates the
    same row instead of creating a duplicate.
    """
    try:
        regime = regime_result["regime"]
        regime_name = regime.value if hasattr(regime, "value") else str(regime)
        details = regime_result.get("details", {}) or {}
        price, sma_200 = details.get("price"), details.get("sma_200")
        adx, vix = details.get("adx"), details.get("vix")

        sp500_pct = None
        if price is not None and sma_200:
            sp500_pct = (float(price) / float(sma_200) - 1) * 100

        counts = _count_signals(signal_rows)
        entries_allowed = _regime_permits_entries(config, regime_name)
        ts = (timestamp or datetime.utcnow()).replace(second=0, microsecond=0)

        engine = get_engine()
        with engine.begin() as conn:
            stmt = sqlite_insert(RegimeMetricsHistory).values(
                timestamp=ts,
                regime=regime_name,
                entries_allowed=1 if entries_allowed else 0,
                adx=adx,
                vix=vix,
                sp500_pct_vs_sma200=sp500_pct,
                macro_health_score=macro_health_score,
                is_backfilled=0,
                **counts,
            )
            update_cols = {
                col: getattr(stmt.excluded, col)
                for col in (
                    "regime", "entries_allowed", "adx", "vix",
                    "sp500_pct_vs_sma200", "macro_health_score",
                    "buy_count", "sell_count", "strong_buy_count",
                    "strong_sell_count", "total_signals", "is_backfilled",
                )
            }
            stmt = stmt.on_conflict_do_update(index_elements=["timestamp"], set_=update_cols)
            conn.execute(stmt)
    except Exception as exc:
        logger.error("Failed to record regime metrics snapshot: %s", exc)


def get_regime_metrics_history(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> pd.DataFrame:
    """Read regime_metrics_history rows in [start, end] (either bound optional), sorted by timestamp.

    Returns an empty DataFrame (not None) when there is no data yet, so
    callers can render a consistent "no data" state without a None check.
    """
    query = (
        "SELECT timestamp, regime, entries_allowed, adx, vix, "
        "sp500_pct_vs_sma200, macro_health_score, buy_count, sell_count, "
        "strong_buy_count, strong_sell_count, total_signals, is_backfilled "
        "FROM regime_metrics_history"
    )
    conditions, params = [], {}
    if start is not None:
        conditions.append("timestamp >= :start")
        params["start"] = start
    if end is not None:
        conditions.append("timestamp <= :end")
        params["end"] = end
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY timestamp ASC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()

    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["entries_allowed"] = df["entries_allowed"].astype(bool)
    return df


def compute_shading_bands(df: pd.DataFrame) -> list[dict]:
    """Group consecutive same-entries_allowed rows into contiguous bands for
    the trend chart's background shading. Pure — no DB/Streamlit/Plotly
    dependency — so it's unit-testable independent of chart rendering.

    Each band runs from its first row's timestamp to the *next* differing
    row's timestamp, so bands tile the whole visible range with no gaps.
    Operates at whatever resolution *df* has: for backfilled history that's
    once/day, for live data it's however many analysis runs actually fired
    that day (2-3x on a normal weekday) — so a same-day regime flip between
    two runs is represented as its own band exactly like any other, with no
    special-casing needed for the "resolution" question this was designed
    to answer.

    Returns [] for an empty or single-row df — nothing meaningful to shade.
    """
    if df.empty or len(df) < 2:
        return []

    ts = df["timestamp"].tolist()
    allowed = df["entries_allowed"].tolist()

    bands: list[dict] = []
    start_idx = 0
    for i in range(1, len(df)):
        if allowed[i] != allowed[start_idx]:
            bands.append({"start": ts[start_idx], "end": ts[i], "allowed": allowed[start_idx]})
            start_idx = i
    bands.append({"start": ts[start_idx], "end": ts[-1], "allowed": allowed[start_idx]})
    return bands


def summarise_resolution(df: pd.DataFrame) -> dict:
    """Describe the backfilled-vs-live resolution mix in *df*, for the chart
    to disclose rather than let a viewer mistake a genuine resolution change
    for a data artifact.

    Backfilled rows (is_backfilled=1) are one point per calendar day —
    indicators/signals only ever kept the day's *last* analysis run before
    this feature existed, so that's the ceiling on what could be recovered.
    Live rows (is_backfilled=0) are one point per actual analysis run
    (2-3x/day on a normal weekday) going forward. A chart spanning both
    will show a real density jump at the boundary between them — this
    function locates that boundary so the chart can mark it explicitly.

    entries_allowed provenance differs by the same split: a live row's
    entries_allowed was computed from the config actually in effect at that
    run (ground truth, captured at decision time — see
    record_regime_snapshot()). A backfilled row's entries_allowed is
    *inferred* — today's allowed_entry_regimes config applied retroactively
    to the historical regime value, since no historical config snapshot is
    retained (see backfill_regime_metrics_history()'s own docstring). The
    chart must not present both as equally authoritative.

    Returns:
        {
          "n_backfilled": int, "n_live": int,
          "boundary_timestamp": Timestamp | None,  # last backfilled row's
                                                      # timestamp, only when
                                                      # the df has both kinds
        }
    """
    if df.empty:
        return {"n_backfilled": 0, "n_live": 0, "boundary_timestamp": None}

    is_backfilled = df["is_backfilled"].astype(bool)
    n_backfilled = int(is_backfilled.sum())
    n_live = int((~is_backfilled).sum())

    boundary_timestamp = None
    if n_backfilled and n_live:
        boundary_timestamp = df.loc[is_backfilled, "timestamp"].max()

    return {"n_backfilled": n_backfilled, "n_live": n_live, "boundary_timestamp": boundary_timestamp}


# ---------------------------------------------------------------------------
# Backfill — reconstructs day-level history from data that already existed
# before this feature shipped.
# ---------------------------------------------------------------------------

def _gspc_daily_rows(engine) -> list[dict]:
    """One row per calendar date already stored for ^GSPC, joining indicators
    (ADX/VIX/SMA200) with price_data (close) and signals (regime — COALESCE
    handles both the current `regime` column and the legacy `notes` column,
    mirroring the same COALESCE pattern brokers/trading212.py uses when
    stamping T212InvestOrder.regime).
    """
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT i.date AS date, i.adx AS adx, i.vix_level AS vix, "
            "i.sma_200 AS sma_200, p.close AS price, "
            "COALESCE(s.regime, s.notes) AS regime "
            "FROM indicators i "
            "JOIN price_data p ON i.ticker = p.ticker AND i.date = p.date "
            "LEFT JOIN signals s ON s.ticker = i.ticker AND s.date = i.date "
            "WHERE i.ticker = '^GSPC' "
            "ORDER BY i.date ASC"
        )).mappings().all()
    return [dict(r) for r in rows]


def _signal_counts_for_date(engine, d) -> dict[str, int]:
    """Buy/sell/strong-buy/strong-sell/total signal counts across the whole
    watchlist for a single stored date — reconstructed from the signals table,
    which (like indicators) accumulates one row per ticker per calendar date."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT signal, COUNT(*) AS n FROM signals WHERE date(date) = date(:d) GROUP BY signal"),
            {"d": d},
        ).mappings().all()
    by_signal = {r["signal"]: r["n"] for r in rows}
    strong_buy = by_signal.get("STRONG_BUY", 0)
    strong_sell = by_signal.get("STRONG_SELL", 0)
    return {
        "buy_count": strong_buy + by_signal.get("BUY", 0),
        "sell_count": strong_sell + by_signal.get("SELL", 0),
        "strong_buy_count": strong_buy,
        "strong_sell_count": strong_sell,
        "total_signals": sum(by_signal.values()),
    }


def _macro_health_as_of(engine, config: dict, as_of_d, vix_level: Optional[float]) -> Optional[float]:
    """Best-effort reconstruction of the regional macro composite score as it
    would have read on a historical date, using the FRED history already
    retained in macro_indicators (see CLAUDE.md's "FRED historical backfill"
    section). Reuses analysis.macro's private regional-blend helpers directly
    — the same "import the private shared core rather than re-deriving it"
    pattern analysis/backtest.py already uses for _series_region/_regional_weights.

    Returns None if macro.py isn't importable, no FRED series are configured,
    or no series has any data on/before as_of_d — a genuine "we don't know",
    not a 0 (which would misleadingly read as "neutral").
    """
    try:
        from analysis.macro import _classify_vix, _regional_macro_score
    except Exception:
        return None

    series_ids = list((config.get("macro", {}) or {}).get("fred_series", {}).keys())
    rows: dict[str, dict] = {}
    if series_ids:
        with engine.connect() as conn:
            for sid in series_ids:
                row = conn.execute(
                    text(
                        "SELECT series_id, value, pct_change_30d, z_score_2y, score "
                        "FROM macro_indicators WHERE series_id = :sid AND date(date) <= date(:d) "
                        "ORDER BY date DESC LIMIT 1"
                    ),
                    {"sid": sid, "d": as_of_d},
                ).mappings().first()
                if row:
                    rows[sid] = dict(row)

    vix_score = None
    if vix_level is not None:
        _, vix_score = _classify_vix(float(vix_level))

    if not rows and vix_score is None:
        return None

    return _regional_macro_score(rows, vix_score, config).get("weighted_score")


def backfill_regime_metrics_history(config: Optional[dict] = None) -> dict:
    """Reconstruct day-level regime_metrics_history rows for dates that predate
    this feature, from data that already exists in indicators/signals/
    macro_indicators.

    Resolution limitation: indicators/signals upsert on (ticker, date), so
    only the *last* analysis run of each historical calendar day ever
    survived — this can only ever recover one row per day for the past, not
    the 2-3x/day resolution record_regime_snapshot() captures going forward.

    entries_allowed limitation: computed against *today's* allowed_entry_regimes
    config, not whatever config was actually live on that historical date
    (no historical config snapshots are retained) — a reasonable
    approximation given the gate has been TRENDING_BULL-only across both
    paths for a while, but flagged here since it's a real limitation, not
    silently assumed correct.

    regime is read directly from the stored signals row for that date (not
    re-derived from ADX/VIX/SMA200), per CLAUDE.md's instruction that shading
    must reflect what the system actually acted on operationally.

    Idempotent — skips any calendar date that already has a
    regime_metrics_history row (from a prior backfill run or a live
    snapshot), so it is safe to call repeatedly (e.g. from a Status page
    button) as new historical data accumulates between calls.

    Returns {"rows_written": int, "earliest_date": str | None, "latest_date": str | None}.
    """
    if config is None:
        config = _load_config()

    engine = get_engine()
    gspc_rows = _gspc_daily_rows(engine)

    with engine.connect() as conn:
        existing_dates = {
            r["d"] for r in conn.execute(
                text("SELECT date(timestamp) AS d FROM regime_metrics_history")
            ).mappings().all()
        }

    written: list[str] = []
    for row in gspc_rows:
        d = row["date"]
        d_key = str(d)[:10]
        if d_key in existing_dates:
            continue
        if row.get("regime") is None:
            # No signals row for this date at all — can't recover buy/sell
            # counts or a genuine acted-on regime, so skip rather than guess.
            continue

        regime_name = row["regime"]
        price, sma_200 = row.get("price"), row.get("sma_200")
        sp500_pct = (float(price) / float(sma_200) - 1) * 100 if (price and sma_200) else None

        counts = _signal_counts_for_date(engine, d)
        macro_health = _macro_health_as_of(engine, config, d, row.get("vix"))
        entries_allowed = _regime_permits_entries(config, regime_name)

        ts = datetime.combine(
            d if isinstance(d, date_cls) else pd.to_datetime(d).date(),
            time(0, 0),
        )

        try:
            with engine.begin() as conn:
                stmt = sqlite_insert(RegimeMetricsHistory).values(
                    timestamp=ts,
                    regime=regime_name,
                    entries_allowed=1 if entries_allowed else 0,
                    adx=row.get("adx"),
                    vix=row.get("vix"),
                    sp500_pct_vs_sma200=sp500_pct,
                    macro_health_score=macro_health,
                    is_backfilled=1,
                    **counts,
                )
                stmt = stmt.on_conflict_do_nothing(index_elements=["timestamp"])
                conn.execute(stmt)
            written.append(d_key)
        except Exception as exc:
            logger.warning("Regime metrics backfill failed for %s: %s", d_key, exc)

    return {
        "rows_written": len(written),
        "earliest_date": min(written) if written else None,
        "latest_date": max(written) if written else None,
    }
