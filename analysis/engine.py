"""Analysis engine: orchestrates all modules, persists to indicators table."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import yaml
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from analysis.correlations import compute_all_pairs
from analysis.macro import compute_macro
from analysis.mean_reversion import compute_mean_reversion
from analysis.trend import compute_trend
from analysis.volatility import compute_volatility
from analysis.volume import compute_volume
from data.database import Indicator, get_engine, get_session, init_db
from data.ingestion import get_all_tickers, get_price_df

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _upsert_indicator(engine, ticker: str, date: datetime, values: dict) -> None:
    """Upsert a single indicator row for (ticker, date)."""
    row = {"ticker": ticker, "date": date, **values}
    stmt = sqlite_insert(Indicator).values([row])
    # Build set_ from all keys except the unique constraint columns
    update_cols = {k: getattr(stmt.excluded, k) for k in row if k not in ("ticker", "date")}
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "date"],
        set_=update_cols,
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def analyse_ticker(
    ticker: str,
    config: dict,
    vix_close: Optional[float] = None,
) -> dict:
    """Run all analysis modules for a single ticker against its stored price data.

    Args:
        ticker: Symbol to analyse.
        config: Loaded config.yaml dict.
        vix_close: Latest VIX close (pre-fetched to avoid redundant DB reads).

    Returns:
        Dict containing 'ticker', 'score_*' from each module, 'raw' values,
        and the upserted indicator values for the latest date.
    """
    df = get_price_df(ticker, days=config["data"]["history_days"])

    trend_res = compute_trend(df, config)
    mr_res    = compute_mean_reversion(df, config)
    vol_res   = compute_volatility(df, config, vix_close=vix_close)
    volume_res = compute_volume(df, config)
    macro_res  = compute_macro(ticker, config)

    # Merge all raw indicator values (scores are kept separately)
    indicator_values = {
        # Trend
        "sma_20":       trend_res["sma_20"],
        "sma_50":       trend_res["sma_50"],
        "sma_200":      trend_res["sma_200"],
        "ema_20":       trend_res["ema_20"],
        "macd":         trend_res["macd"],
        "macd_signal":  trend_res["macd_signal"],
        "macd_hist":    trend_res["macd_hist"],
        "adx":          trend_res["adx"],
        "plus_di":      trend_res["plus_di"],
        "minus_di":     trend_res["minus_di"],
        "golden_cross": trend_res["golden_cross"],
        "ma_cross_score": trend_res["ma_cross_score"],
        # Mean reversion
        "rsi":          mr_res["rsi"],
        "bb_upper":     mr_res["bb_upper"],
        "bb_middle":    mr_res["bb_middle"],
        "bb_lower":     mr_res["bb_lower"],
        "bb_pct_b":     mr_res["bb_pct_b"],
        "bb_width":     mr_res["bb_width"],
        "z_score_52w":  mr_res["z_score_52w"],
        # Volatility
        "atr":          vol_res["atr"],
        "atr_pct":      vol_res["atr_pct"],
        "hist_vol_20":  vol_res["hist_vol_20"],
        "vix_level":    vol_res["vix_level"],
        "vix_regime":   vol_res["vix_regime"],
        # Volume
        "obv":          volume_res["obv"],
        "obv_trend":    volume_res["obv_trend"],
        "volume_sma_20": volume_res["volume_sma_20"],
        "volume_ratio": volume_res["volume_ratio"],
        # Macro
        "yield_curve_spread":  macro_res["yield_curve_spread"],
        "yield_curve_regime":  macro_res["yield_curve_regime"],
    }

    # Persist to indicators table for the most recent date in the price series
    if not df.empty:
        latest_date = df["date"].iloc[-1]
        if hasattr(latest_date, "to_pydatetime"):
            latest_date = latest_date.to_pydatetime()
        engine = get_engine()
        _upsert_indicator(engine, ticker, latest_date, indicator_values)

    return {
        "ticker": ticker,
        "score_trend":          trend_res["score"],
        "score_mean_reversion": mr_res["score"],
        "score_volatility":     vol_res["score"],
        "score_volume":         volume_res["score"],
        "score_macro":          macro_res["score"],
        "indicators":           indicator_values,
    }


def run_analysis(
    tickers: Optional[list[str]] = None,
    config: Optional[dict] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> list[dict]:
    """Run all analysis modules for every ticker and persist results.

    Args:
        tickers: Symbols to analyse. Defaults to the full watchlist.
        config: Loaded config dict. Loaded from file if not provided.
        progress_callback: Optional callable invoked as
            ``progress_callback(current_index, total, ticker)`` after each
            ticker completes — use this to drive a Streamlit progress bar.

    Returns:
        List of result dicts (one per ticker) as returned by :func:`analyse_ticker`.
    """
    init_db()
    if config is None:
        config = _load_config()
    if tickers is None:
        tickers = get_all_tickers(config)

    # Pre-fetch VIX once to avoid one DB query per ticker
    vix_df = get_price_df("^VIX", days=5)
    vix_close = float(vix_df["close"].iloc[-1]) if not vix_df.empty else None

    results = []
    total = len(tickers)
    for idx, ticker in enumerate(tickers, 1):
        logger.info("Analysing %s (%d/%d)…", ticker, idx, total)
        try:
            result = analyse_ticker(ticker, config, vix_close=vix_close)
        except Exception as exc:
            logger.error("Analysis failed for %s: %s", ticker, exc)
            result = {"ticker": ticker, "error": str(exc)}
        results.append(result)
        if progress_callback:
            progress_callback(idx, total, ticker)

    # Compute and persist all cross-asset pair correlations once per run
    try:
        logger.info("Computing cross-asset correlations…")
        compute_all_pairs(config)
    except Exception as exc:
        logger.error("Correlation computation failed: %s", exc)

    return results
