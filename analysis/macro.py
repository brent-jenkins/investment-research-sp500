"""Macro indicators: FRED yield curve (10Y-2Y spread), VIX regime."""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from data.ingestion import get_price_df

logger = logging.getLogger(__name__)

# Tickers that are themselves macro indicators — skip yield-curve scoring
# to avoid circular logic (TLT IS bond prices; VIX IS volatility).
_MACRO_TICKERS = frozenset({"^VIX", "TLT", "IEF", "HYG"})


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _yield_curve_from_fred(api_key: str, lookback_days: int = 10) -> Optional[float]:
    """Fetch the latest 10Y-2Y Treasury spread from FRED (series T10Y2Y).

    Args:
        api_key: FRED API key from config.
        lookback_days: How many days back to search for the latest data point
            (FRED sometimes lags weekends / holidays).

    Returns:
        The most recent spread value in percentage points, or None on failure.
    """
    try:
        from fredapi import Fred  # imported here so missing package = graceful None
        fred = Fred(api_key=api_key)
        start = (datetime.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        series = fred.get_series("T10Y2Y", observation_start=start)
        series = series.dropna()
        if series.empty:
            return None
        return float(series.iloc[-1])
    except Exception as exc:
        logger.warning("FRED fetch failed: %s", exc)
        return None


def _classify_yield_curve(spread: float) -> tuple[str, int]:
    """Map a 10Y-2Y spread to a regime label and score.

    Returns:
        (regime_label, score) where:
        - spread > 0.5  → "normal",   +1 (term premium present, risk-on)
        - -0.1 to 0.5  → "flat",      0
        - < -0.1       → "inverted", -1 (recession signal)
    """
    if spread > 0.5:
        return "normal", 1
    if spread >= -0.1:
        return "flat", 0
    return "inverted", -1


def _vix_from_db(days: int = 5) -> Optional[float]:
    """Return the latest VIX closing price stored in the local database."""
    try:
        vix_df = get_price_df("^VIX", days=days)
        if vix_df.empty:
            return None
        return float(vix_df["close"].iloc[-1])
    except Exception:
        return None


def _classify_vix(level: float) -> tuple[str, int]:
    """Classify VIX level into regime and score.

    Returns:
        (regime_label, score) — low/normal → positive bias, high/extreme → negative.
    """
    if level < 12:
        return "low", 1
    if level < 20:
        return "normal", 0
    if level < 30:
        return "high", -1
    return "extreme", -1


def compute_macro(ticker: str, config: Optional[dict] = None) -> dict:
    """Compute macro indicators for a given ticker.

    Data sources:
    - FRED API for 10Y-2Y yield curve spread (requires ``macro.fred_api_key``
      in config.yaml — returns None gracefully if key is missing or invalid).
    - Local SQLite database for VIX price data.

    Scoring:
    - Yield curve normal (+1) or inverted (-1), else 0.
    - VIX low/normal (+1/0) or high/extreme (-1).
    - Combined = average, then mapped: ≥ 0.5 → +1, ≤ -0.5 → -1, else 0.

    Macro assets (^VIX, TLT, IEF, HYG) are returned as neutral (score=0)
    to avoid self-referential scoring.

    Args:
        ticker: The asset being scored.
        config: Loaded config.yaml dict. Loaded from file if not provided.

    Returns:
        Dict with 'score' (-1/0/1) and raw macro indicator values.
    """
    if config is None:
        config = _load_config()

    empty = {
        "score": 0,
        "yield_curve_spread": None,
        "yield_curve_regime": None,
        "vix_level": None,
        "vix_regime": None,
    }

    if ticker in _MACRO_TICKERS:
        return empty

    macro_cfg = config.get("macro", {})
    fred_key  = macro_cfg.get("fred_api_key", "")

    # Yield curve
    yc_spread = None
    yc_regime = None
    yc_score  = 0
    if fred_key:
        yc_spread = _yield_curve_from_fred(fred_key)
        if yc_spread is not None:
            yc_regime, yc_score = _classify_yield_curve(yc_spread)
    else:
        logger.debug("No FRED API key configured — skipping yield curve.")

    # VIX
    vix_level = _vix_from_db()
    vix_regime = None
    vix_score  = 0
    if vix_level is not None:
        vix_regime, vix_score = _classify_vix(vix_level)

    # Combined score
    active_scores = [s for s in (yc_score, vix_score) if s != 0 or
                     (yc_spread is not None or vix_level is not None)]
    if not active_scores:
        combined = 0
    else:
        avg = sum(active_scores) / len(active_scores)
        combined = 1 if avg >= 0.5 else (-1 if avg <= -0.5 else 0)

    return {
        "score":               combined,
        "yield_curve_spread":  yc_spread,
        "yield_curve_regime":  yc_regime,
        "vix_level":           vix_level,
        "vix_regime":          vix_regime,
    }
