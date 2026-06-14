"""Macro / cross-asset signals: VIX level, yield-curve proxy, dollar strength."""

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from data.ingestion import get_price_df


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def score_macro(
    ticker: str,
    vix_df: Optional[pd.DataFrame] = None,
    tlt_df: Optional[pd.DataFrame] = None,
    vix_fear_threshold: float = 30.0,
    vix_calm_threshold: float = 20.0,
    tlt_days: int = 20,
) -> int:
    """Score the macro backdrop for a given asset.

    Uses VIX level as a fear gauge and TLT trend as a bond/risk proxy.
    Individual equity/commodity tickers use these scores; passing bond/vol
    tickers themselves returns 0 to avoid circular logic.

    Args:
        ticker: The asset being scored.
        vix_df: Optional pre-loaded VIX DataFrame (avoids redundant DB reads).
        tlt_df: Optional pre-loaded TLT DataFrame.
        vix_fear_threshold: VIX above this level is bearish for risk assets.
        vix_calm_threshold: VIX below this level is bullish for risk assets.
        tlt_days: Window for TLT trend determination.

    Returns:
        1 (macro tailwind), -1 (macro headwind), or 0 (neutral / not applicable).
    """
    # Avoid scoring macro assets against themselves
    if ticker in ("^VIX", "TLT", "IEF", "HYG"):
        return 0

    if vix_df is None:
        vix_df = get_price_df("^VIX", days=60)
    if tlt_df is None:
        tlt_df = get_price_df("TLT", days=60)

    vix_score = 0
    if not vix_df.empty:
        last_vix = vix_df["close"].iloc[-1]
        if last_vix < vix_calm_threshold:
            vix_score = 1
        elif last_vix > vix_fear_threshold:
            vix_score = -1

    tlt_score = 0
    if not tlt_df.empty and len(tlt_df) >= tlt_days:
        tlt_close = tlt_df["close"]
        tlt_trend = tlt_close.iloc[-1] - tlt_close.iloc[-tlt_days]
        # Rising bonds (TLT up) often signals risk-off; falling bonds = risk-on
        tlt_score = -1 if tlt_trend > 0 else 1

    combined = vix_score + tlt_score
    if combined > 0:
        return 1
    if combined < 0:
        return -1
    return 0
