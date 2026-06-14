"""Confluence engine: aggregates module scores into a final BUY/SELL/NEUTRAL signal."""

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from analysis.macro import compute_macro
from analysis.mean_reversion import compute_mean_reversion
from analysis.trend import compute_trend
from analysis.volatility import compute_volatility
from analysis.volume import compute_volume
from data.ingestion import get_price_df


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def generate_signal(
    ticker: str,
    df: Optional[pd.DataFrame] = None,
    config: Optional[dict] = None,
    vix_close: Optional[float] = None,
) -> dict:
    """Run all analysis modules and aggregate into a final signal.

    Args:
        ticker: Asset to analyse.
        df: Pre-loaded price DataFrame. Fetched from DB if not provided.
        config: Loaded config dict. Loaded from file if not provided.
        vix_close: Pre-fetched VIX close (avoids redundant DB reads when
            generating signals for many tickers in a batch).

    Returns:
        Dict with keys: ticker, signal ('BUY'/'SELL'/'NEUTRAL'),
        confluence_score (-1 to +1), strength (0-1), and per-module scores.
    """
    if config is None:
        config = _load_config()
    if df is None:
        df = get_price_df(ticker, days=config["data"]["history_days"])

    trend_res  = compute_trend(df, config)
    mr_res     = compute_mean_reversion(df, config)
    vol_res    = compute_volatility(df, config, vix_close=vix_close)
    volume_res = compute_volume(df, config)
    macro_res  = compute_macro(ticker, config)

    scores = [
        trend_res["score"],
        mr_res["score"],
        vol_res["score"],
        volume_res["score"],
        macro_res["score"],
    ]

    conf_cfg = config["confluence"]
    total    = conf_cfg["total_signal_modules"]
    buy_thr  = conf_cfg["buy_threshold"]
    sell_thr = conf_cfg["sell_threshold"]
    min_req  = conf_cfg["min_signals_required"]

    bullish = sum(1 for s in scores if s == 1)
    bearish = sum(1 for s in scores if s == -1)

    if bullish >= min_req and bullish / total >= buy_thr:
        final_signal = "BUY"
        strength = bullish / total
    elif bearish >= min_req and bearish / total >= sell_thr:
        final_signal = "SELL"
        strength = bearish / total
    else:
        final_signal = "NEUTRAL"
        strength = max(bullish, bearish) / total

    confluence_score = (bullish - bearish) / total

    return {
        "ticker":               ticker,
        "signal":               final_signal,
        "confluence_score":     round(confluence_score, 3),
        "strength":             round(strength, 3),
        "regime_score":         0,   # regime folded into trend module
        "trend_score":          trend_res["score"],
        "mean_reversion_score": mr_res["score"],
        "volatility_score":     vol_res["score"],
        "volume_score":         volume_res["score"],
        "macro_score":          macro_res["score"],
    }
