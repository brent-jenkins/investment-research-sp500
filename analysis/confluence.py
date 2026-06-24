"""Confluence scoring: applies regime-weighted module scores to produce signals."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import yaml
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from analysis.correlations import compute_correlations
from analysis.macro import compute_macro
from analysis.mean_reversion import compute_mean_reversion
from analysis.regime import Regime, detect_regime, detect_overlays
from analysis.trend import compute_trend
from analysis.volatility import compute_volatility
from analysis.volume import compute_volume
from data.database import Signal, get_engine, get_metadata_str, init_db, set_metadata
from data.ingestion import get_all_tickers, get_price_df

# Modules whose accuracy can be tracked via the trades table
# (correlation_score is not stored on trades, so it stays at its regime weight)
_TRACKABLE_MODULES = ["trend", "mean_reversion", "volatility", "volume", "macro"]
_ALL_MODULES       = _TRACKABLE_MODULES + ["correlation"]

logger = logging.getLogger(__name__)

# Watchlist groups whose assets are subject to the MACRO_STRESS buy-confidence modifier
# (equity indices, sector ETFs, bonds, and international equity indices).
_EQUITY_BOND_GROUPS: frozenset[str] = frozenset({
    "indices", "sector_etfs", "bonds", "international",
})

# Equity-only groups subject to the CREDIT_STRESS modifier (bonds excluded because
# credit stress specifically reduces confidence in equity — not fixed-income — longs).
_EQUITY_ONLY_GROUPS: frozenset[str] = frozenset({
    "indices", "sector_etfs", "international",
})


def _equity_bond_tickers(config: dict) -> frozenset[str]:
    """Return the set of tickers belonging to equity index or bond watchlist groups."""
    result: set[str] = set()
    for group, entries in config.get("watchlist", {}).items():
        if group in _EQUITY_BOND_GROUPS and isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict) and "ticker" in e:
                    result.add(e["ticker"])
    return frozenset(result)


def _equity_only_tickers(config: dict) -> frozenset[str]:
    """Return equity-only tickers (indices, sector ETFs, international) — bonds excluded."""
    result: set[str] = set()
    for group, entries in config.get("watchlist", {}).items():
        if group in _EQUITY_ONLY_GROUPS and isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict) and "ticker" in e:
                    result.add(e["ticker"])
    return frozenset(result)


def _compute_gspc_corr(df: pd.DataFrame, gspc_df: pd.DataFrame, days: int = 30) -> Optional[float]:
    """Compute trailing `days`-day return correlation between a ticker and ^GSPC.

    Used by the regime direction filter to distinguish assets that move with the
    broad market (high positive correlation) from those that move independently
    or inversely (low/negative correlation).

    Returns None when either DataFrame lacks sufficient data.
    """
    try:
        a_ret = df["close"].pct_change().replace([float("inf"), float("-inf")], float("nan"))
        g_ret = gspc_df["close"].pct_change().replace([float("inf"), float("-inf")], float("nan"))
        combined = pd.concat([a_ret.rename("a"), g_ret.rename("g")], axis=1).dropna()
        if len(combined) < days:
            return None
        corr = float(combined.tail(days)[["a", "g"]].corr().iloc[0, 1])
        return None if pd.isna(corr) else corr
    except Exception:
        return None


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_regime_thresholds(regime_name: str, config: dict) -> tuple[float, float]:
    """Return (buy_threshold, sell_threshold) for the given regime.

    All five regimes must have entries under confluence.regime_thresholds in
    config.yaml.  Falls back to 0.55/0.55 if a regime is missing (e.g. during
    a config reload race), but logs a warning so the gap is visible.
    """
    regime_cfg = config.get("confluence", {}).get("regime_thresholds", {}).get(regime_name)
    if regime_cfg is None:
        logger.warning("No regime_thresholds entry for %s — using 0.55 default", regime_name)
        return 0.55, 0.55
    return float(regime_cfg["buy_threshold"]), float(regime_cfg["sell_threshold"])


def _classify(score: float, buy_threshold: float = 0.55, sell_threshold: float = 0.55) -> str:
    """Map a weighted confluence score to a named signal classification.

    STRONG_BUY / STRONG_SELL boundaries come from the regime-aware thresholds;
    the BUY / SELL midpoint is fixed at ±0.30.
    """
    mid = 0.30
    thresholds = [
        ( buy_threshold,  1.00, "STRONG_BUY"),
        ( mid,  buy_threshold,  "BUY"),
        (-mid,   mid,           "NEUTRAL"),
        (-sell_threshold, -mid, "SELL"),
        (-1.00, -sell_threshold, "STRONG_SELL"),
    ]
    for lo, hi, label in sorted(thresholds, key=lambda t: -t[0]):
        if lo <= score <= hi:
            return label
    return "NEUTRAL"


def _weighted_score(scores: dict[str, int], weights: dict[str, float]) -> float:
    """Compute the dot product of module scores and their regime weights.

    Args:
        scores:  Dict mapping module name → score (-1, 0, or 1).
        weights: Dict mapping module name → weight (sums to 1.0).

    Returns:
        Weighted confluence score in the range [-1.0, +1.0].
    """
    return round(
        sum(scores.get(k, 0) * w for k, w in weights.items()), 3
    )


# ---------------------------------------------------------------------------
# Feedback learning helpers
# ---------------------------------------------------------------------------

def _fetch_trades_for_learning() -> list[dict]:
    """Minimal SQL query returning only the columns needed for accuracy learning."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT signal_type, confluence_score, regime, pnl, "
                "trend_score, mean_reversion_score, volatility_score, "
                "volume_score, macro_score "
                "FROM trades WHERE is_open = 0 AND pnl IS NOT NULL"
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def recalculate_learned_weights(config: dict) -> Optional[dict]:
    """Derive per-module weights from closed-trade accuracy and persist them.

    Win rate for each module is measured as: (calls where the module's sign
    matched the trade outcome) / (total non-neutral calls by that module).
    Correlation score is not stored on trades so it inherits a neutral 50% rate.
    Returns the new weight dict, or None if there are insufficient trades.
    """
    learning_cfg = config.get("learning", {})
    if not learning_cfg.get("enabled", True):
        return None

    min_trades = learning_cfg.get("min_trades_before_adjustment", 20)
    max_w      = learning_cfg.get("max_single_weight", 0.30)
    min_w      = learning_cfg.get("min_single_weight", 0.05)

    trades = _fetch_trades_for_learning()
    if len(trades) < min_trades:
        logger.info("Learning: %d closed trades — minimum is %d, skipping", len(trades), min_trades)
        return None

    win_rates: dict[str, float] = {}
    for module in _TRACKABLE_MODULES:
        col = f"{module}_score"
        directional = [t for t in trades if t.get(col) is not None and int(t.get(col, 0)) != 0]
        if not directional:
            win_rates[module] = 0.5
            continue
        correct = sum(
            1 for t in directional
            if (int(t[col]) > 0 and float(t.get("pnl", 0)) > 0)
            or (int(t[col]) < 0 and float(t.get("pnl", 0)) < 0)
        )
        win_rates[module] = correct / len(directional)

    win_rates["correlation"] = 0.5  # not trackable, held at neutral

    # Convert win rates to proportional raw weights
    raw = {k: max(0.001, v) for k, v in win_rates.items()}
    total = sum(raw.values())
    normalized = {k: v / total for k, v in raw.items()}

    # Clamp then renormalize
    clamped = {k: max(min_w, min(max_w, v)) for k, v in normalized.items()}
    total_c = sum(clamped.values())
    final_weights = {k: round(v / total_c, 4) for k, v in clamped.items()}

    # Persist to metadata
    n = len(trades)
    payload = {"weights": final_weights, "win_rates": win_rates, "n_trades": n}
    set_metadata("learned_weights", json.dumps(payload))

    # Append to weight history (keep last 20 snapshots)
    history_raw = get_metadata_str("weight_history") or "[]"
    try:
        history = json.loads(history_raw)
    except Exception:
        history = []
    history.append({
        "ts":       datetime.utcnow().isoformat(),
        "n_trades": n,
        "weights":  final_weights,
    })
    set_metadata("weight_history", json.dumps(history[-20:]))

    logger.info("Learned weights updated from %d trades: %s", n, final_weights)
    return final_weights


def maybe_update_weights(config: dict) -> bool:
    """Trigger weight recalculation if the trade count has hit an update boundary.

    Should be called after each trade is closed.  Returns True when weights
    were actually recalculated.
    """
    learning_cfg = config.get("learning", {})
    if not learning_cfg.get("enabled", True):
        return False

    min_trades = learning_cfg.get("min_trades_before_adjustment", 20)
    freq       = learning_cfg.get("weight_update_frequency", 10)

    trades = _fetch_trades_for_learning()
    n = len(trades)
    if n < min_trades:
        return False
    if (n - min_trades) % freq == 0:
        recalculate_learned_weights(config)
        return True
    return False


def get_learned_weights() -> Optional[dict]:
    """Return the stored learned-weight payload, or None if not yet computed.

    Returns a dict with keys: ``weights`` (per-module), ``win_rates``,
    ``n_trades``.
    """
    raw = get_metadata_str("learned_weights")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def get_weight_history() -> list[dict]:
    """Return the stored weight-history snapshots (up to 20), oldest first."""
    raw = get_metadata_str("weight_history")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _effective_weights(regime_weights: dict, config: dict) -> dict:
    """Blend regime weights with learned weights when learning is enabled.

    Uses a 60 / 40 split: 60% regime-derived, 40% accuracy-derived.
    Because both dicts sum to 1.0, the blend also sums to 1.0.
    Falls back to pure regime weights when learning is disabled or no
    learned weights exist yet.
    """
    if not config.get("learning", {}).get("enabled", True):
        return regime_weights
    learned = get_learned_weights()
    if learned is None:
        return regime_weights
    lw = learned["weights"]
    blended = {
        k: round(0.6 * regime_weights.get(k, 0.0) + 0.4 * lw.get(k, 0.0), 4)
        for k in regime_weights
    }
    return blended


def score_ticker(
    ticker: str,
    regime_result: dict,
    config: dict,
    vix_close: Optional[float] = None,
    overlays: Optional[dict] = None,
    gspc_df: Optional[pd.DataFrame] = None,
) -> dict:
    """Compute a regime-weighted confluence signal for a single ticker.

    Calls all seven ``compute_*`` analysis functions, applies the regime's
    weight profile, and classifies the result.

    When ``overlays`` contains active flags the following modifiers are applied
    before classification:

    - ``MACRO_STRESS``: positive (buy) scores for equity and bond assets are
      multiplied by 0.80 — a 20% reduction in buy-signal confidence.

    Args:
        ticker:        Asset symbol to score.
        regime_result: Return value of :func:`~analysis.regime.detect_regime`.
        config:        Loaded config.yaml dict.
        vix_close:     Pre-fetched VIX close price (avoids redundant DB reads).
        overlays:      Active regime overlays from :func:`~analysis.regime.detect_overlays`.
        gspc_df:       Pre-fetched ^GSPC price DataFrame for correlation-aware regime
                       direction filtering (avoids a redundant DB read per ticker).

    Returns:
        Dict with ticker, signal classification, weighted score, per-module scores,
        and a comma-separated ``overlay_applied`` field.
    """
    df = get_price_df(ticker, days=config["data"]["history_days"])

    trend_res  = compute_trend(df, config)
    mr_res     = compute_mean_reversion(df, config)
    vol_res    = compute_volatility(df, config, vix_close=vix_close)
    volume_res = compute_volume(df, config)
    macro_res  = compute_macro(ticker, config)
    corr_res   = compute_correlations(ticker, config)

    module_scores = {
        "trend":          trend_res["score"],
        "mean_reversion": mr_res["score"],
        "volatility":     vol_res["score"],
        "volume":         volume_res["score"],
        "macro":          macro_res["score"],
        "correlation":    corr_res["score"],
    }

    weights = _effective_weights(regime_result["weights"], config)

    # When the correlation module has no data at all (no direct pairs, no
    # usable proxy), redistribute its weight proportionally across the other
    # five modules so untracked assets aren't penalised by an empty layer.
    if corr_res.get("untracked", False):
        corr_w = weights.get("correlation", 0.0)
        if corr_w > 0:
            other_sum = sum(w for k, w in weights.items() if k != "correlation")
            if other_sum > 0:
                scale = 1.0 / other_sum
                weights = {
                    k: round(w * scale, 6) if k != "correlation" else 0.0
                    for k, w in weights.items()
                }

    w_score = _weighted_score(module_scores, weights)

    # Apply regime overlays
    overlay_applied: list[str] = []
    if overlays and overlays.get("MACRO_STRESS"):
        if ticker in _equity_bond_tickers(config) and w_score > 0:
            w_score = round(w_score * 0.80, 3)
            overlay_applied.append("MACRO_STRESS")

    # CREDIT_STRESS: 15% reduction for equity (not bond) buy signals
    if overlays and overlays.get("CREDIT_STRESS"):
        if ticker in _equity_only_tickers(config) and w_score > 0:
            w_score = round(w_score * 0.85, 3)
            overlay_applied.append("CREDIT_STRESS")

    regime_name = regime_result["regime"].value
    buy_threshold, sell_threshold = get_regime_thresholds(regime_name, config)
    classification = _classify(w_score, buy_threshold, sell_threshold)

    # Regime direction filter: suppress counter-trend signals in strongly trending regimes,
    # but only for assets that move with the broad market (^GSPC correlation > threshold).
    # Assets with negative or low correlation (e.g. GLD, FXI) may be selling/buying for
    # independent reasons and are allowed through even in a trending regime.
    if config.get("signals", {}).get("regime_direction_filter", True):
        corr_threshold = float(
            config.get("signals", {}).get("regime_filter_corr_threshold", 0.5)
        )
        if regime_name == "TRENDING_BULL" and classification in ("SELL", "STRONG_SELL"):
            gspc_corr = (
                _compute_gspc_corr(df, gspc_df)
                if gspc_df is not None and not gspc_df.empty
                else None
            )
            # Unknown correlation → conservative default: suppress
            if gspc_corr is None or gspc_corr > corr_threshold:
                logger.debug(
                    "%s SELL suppressed in TRENDING_BULL (^GSPC corr=%.2f, threshold=%.2f)",
                    ticker, gspc_corr if gspc_corr is not None else float("nan"), corr_threshold,
                )
                classification = "NEUTRAL"
            else:
                logger.debug(
                    "%s SELL allowed in TRENDING_BULL (^GSPC corr=%.2f ≤ threshold=%.2f)",
                    ticker, gspc_corr, corr_threshold,
                )
        elif regime_name == "TRENDING_BEAR" and classification in ("BUY", "STRONG_BUY"):
            gspc_corr = (
                _compute_gspc_corr(df, gspc_df)
                if gspc_df is not None and not gspc_df.empty
                else None
            )
            if gspc_corr is None or gspc_corr > corr_threshold:
                logger.debug(
                    "%s BUY suppressed in TRENDING_BEAR (^GSPC corr=%.2f, threshold=%.2f)",
                    ticker, gspc_corr if gspc_corr is not None else float("nan"), corr_threshold,
                )
                classification = "NEUTRAL"
            else:
                logger.debug(
                    "%s BUY allowed in TRENDING_BEAR (^GSPC corr=%.2f ≤ threshold=%.2f)",
                    ticker, gspc_corr, corr_threshold,
                )

    return {
        "ticker":               ticker,
        "regime":               regime_result["regime"].value,
        "signal":               classification,
        "confluence_score":     w_score,
        "trend_score":          module_scores["trend"],
        "mean_reversion_score": module_scores["mean_reversion"],
        "volatility_score":     module_scores["volatility"],
        "volume_score":         module_scores["volume"],
        "macro_score":          module_scores["macro"],
        "correlation_score":    module_scores["correlation"],
        "overlay_applied":      ",".join(overlay_applied),
    }


def _persist_signal(result: dict, as_of: datetime) -> None:
    """Upsert a single signal result into the ``signals`` table."""
    engine = get_engine()
    stmt = sqlite_insert(Signal).values(
        ticker=result["ticker"],
        date=as_of,
        regime_score=0,   # legacy column — regime now stored in 'regime'
        trend_score=result["trend_score"],
        mean_reversion_score=result["mean_reversion_score"],
        volatility_score=result["volatility_score"],
        volume_score=result["volume_score"],
        macro_score=result["macro_score"],
        correlation_score=result["correlation_score"],
        confluence_score=result["confluence_score"],
        signal=result["signal"],
        strength=abs(result["confluence_score"]),
        notes=result["regime"],
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "date"],
        set_={
            "trend_score":          stmt.excluded.trend_score,
            "mean_reversion_score": stmt.excluded.mean_reversion_score,
            "volatility_score":     stmt.excluded.volatility_score,
            "volume_score":         stmt.excluded.volume_score,
            "macro_score":          stmt.excluded.macro_score,
            "correlation_score":    stmt.excluded.correlation_score,
            "confluence_score":     stmt.excluded.confluence_score,
            "signal":               stmt.excluded.signal,
            "strength":             stmt.excluded.strength,
            "notes":                stmt.excluded.notes,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def generate_signals(
    tickers: Optional[list[str]] = None,
    config: Optional[dict] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Generate regime-weighted confluence signals for all watchlist assets.

    Workflow:
    1. Detect the current market regime from stored ^GSPC indicators.
    2. For each ticker, compute fresh module scores via the analysis modules.
    3. Apply the regime's weight profile to produce a weighted score ∈ [-1, 1].
    4. Classify as STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL.
    5. Persist to the ``signals`` table (upsert keyed on ticker + today's date).

    Args:
        tickers:           Symbols to score. Defaults to the full watchlist.
        config:            Loaded config dict. Read from file if not provided.
        progress_callback: Called as ``cb(current, total, ticker)`` after each
                           ticker — use to drive a Streamlit progress bar.

    Returns:
        Dict with:
        - ``regime``  — the detected :class:`~analysis.regime.Regime` result dict
        - ``signals`` — list of per-ticker signal dicts
        - ``errors``  — list of dicts for any tickers that failed
        - ``as_of``   — datetime the signals were generated
    """
    init_db()
    if config is None:
        config = _load_config()
    if tickers is None:
        tickers = get_all_tickers(config)

    as_of = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # Detect regime once — applies to all tickers
    regime_result = detect_regime()
    logger.info("Regime detected: %s", regime_result["regime"])

    # Detect regime overlays from stored correlation data; stored to metadata for UI display
    overlays = detect_overlays()
    active_overlay_str = ",".join(k for k, v in overlays.items() if v)
    logger.info("Active overlays: %s", active_overlay_str or "none")

    # Pre-fetch VIX and GSPC once to avoid one DB query per ticker
    vix_df = get_price_df("^VIX", days=5)
    vix_close = float(vix_df["close"].iloc[-1]) if not vix_df.empty else None

    gspc_df = get_price_df("^GSPC", days=config["data"]["history_days"])

    signals: list[dict] = []
    errors:  list[dict] = []
    total = len(tickers)

    for idx, ticker in enumerate(tickers, 1):
        try:
            result = score_ticker(
                ticker, regime_result, config,
                vix_close=vix_close, overlays=overlays, gspc_df=gspc_df,
            )
            _persist_signal(result, as_of)
            signals.append(result)
        except Exception as exc:
            logger.error("Signal generation failed for %s: %s", ticker, exc)
            errors.append({"ticker": ticker, "error": str(exc)})

        if progress_callback:
            progress_callback(idx, total, ticker)

    set_metadata("last_analysis_run", datetime.utcnow().isoformat())
    return {
        "regime":  regime_result,
        "signals": signals,
        "errors":  errors,
        "as_of":   as_of,
    }


def get_latest_signals(tickers: Optional[list[str]] = None) -> list[dict]:
    """Read the most recent signal row per ticker from the database.

    Returns a list of dicts sorted by confluence_score descending, ready
    for display.  Missing tickers (no signals yet) are excluded.
    """
    if tickers is None:
        tickers = get_all_tickers()

    engine = get_engine()
    rows = []
    with engine.connect() as conn:
        for ticker in tickers:
            row = conn.execute(
                text(
                    "SELECT ticker, date, signal, confluence_score, "
                    "trend_score, mean_reversion_score, volatility_score, "
                    "volume_score, macro_score, notes AS regime "
                    "FROM signals WHERE ticker = :ticker "
                    "ORDER BY date DESC LIMIT 1"
                ),
                {"ticker": ticker},
            ).mappings().first()
            if row:
                rows.append(dict(row))

    return sorted(rows, key=lambda r: r.get("confluence_score") or 0, reverse=True)


def get_last_signal_timestamp() -> Optional[datetime]:
    """Return the most recent ``date`` stored in the signals table."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT MAX(date) FROM signals")
        ).scalar()
    if result is None:
        return None
    try:
        return datetime.fromisoformat(str(result))
    except Exception:
        return None
