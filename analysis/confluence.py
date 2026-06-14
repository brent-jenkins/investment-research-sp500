"""Confluence engine: aggregates module scores into a final BUY/SELL/NEUTRAL signal."""

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from analysis.macro import score_macro
from analysis.mean_reversion import score_mean_reversion
from analysis.regime import classify_regime
from analysis.trend import score_trend
from analysis.volatility import score_volatility
from analysis.volume import score_volume
from data.ingestion import get_price_df


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def generate_signal(
    ticker: str,
    df: Optional[pd.DataFrame] = None,
    config: Optional[dict] = None,
) -> dict:
    """Run all analysis modules and aggregate into a final signal.

    Args:
        ticker: Asset to analyse.
        df: Pre-loaded price DataFrame. Fetched from DB if not provided.
        config: Loaded config dict. Loaded from file if not provided.

    Returns:
        Dict with keys: ticker, signal ('BUY'/'SELL'/'NEUTRAL'),
        confluence_score, strength, and individual module scores.
    """
    if config is None:
        config = _load_config()
    if df is None:
        df = get_price_df(ticker, days=config["data"]["history_days"])

    sig_cfg = config["signals"]
    conf_cfg = config["confluence"]

    regime = classify_regime(df, sig_cfg["medium_window"], sig_cfg["long_window"])
    trend = score_trend(
        df,
        sig_cfg["macd_fast"],
        sig_cfg["macd_slow"],
        sig_cfg["macd_signal"],
        sig_cfg["adx_period"],
        sig_cfg["adx_trend_threshold"],
    )
    mean_rev = score_mean_reversion(
        df,
        sig_cfg["rsi_period"],
        bb_period=sig_cfg["bollinger_period"],
        bb_std=sig_cfg["bollinger_std"],
    )
    vol = score_volatility(df, sig_cfg["atr_period"])
    volume = score_volume(df, sig_cfg["volume_avg_period"])
    macro = score_macro(ticker)

    scores = [regime, trend, mean_rev, vol, volume, macro]
    bullish = sum(1 for s in scores if s == 1)
    bearish = sum(1 for s in scores if s == -1)
    total = conf_cfg["total_signal_modules"]

    buy_thr = conf_cfg["buy_threshold"]
    sell_thr = conf_cfg["sell_threshold"]
    min_req = conf_cfg["min_signals_required"]

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
        "ticker": ticker,
        "signal": final_signal,
        "confluence_score": round(confluence_score, 3),
        "strength": round(strength, 3),
        "regime_score": regime,
        "trend_score": trend,
        "mean_reversion_score": mean_rev,
        "volatility_score": vol,
        "volume_score": volume,
        "macro_score": macro,
    }
