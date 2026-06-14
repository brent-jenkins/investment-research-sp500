"""Market regime detection using ^GSPC stored indicators as the reference."""

import logging
from enum import Enum
from typing import Optional

from sqlalchemy import text

from data.database import get_engine

logger = logging.getLogger(__name__)

# Reference asset for regime detection — must exist in the watchlist
_REFERENCE_TICKER = "^GSPC"


class Regime(str, Enum):
    """Four named market regimes, each carrying its own indicator weight profile."""
    TRENDING_BULL   = "TRENDING_BULL"
    TRENDING_BEAR   = "TRENDING_BEAR"
    RANGING_LOW_VOL = "RANGING_LOW_VOL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN         = "UNKNOWN"


# Human-readable descriptions shown in the UI
REGIME_DESCRIPTIONS: dict[str, str] = {
    Regime.TRENDING_BULL:   "Market trending strongly upward — trend signals carry extra weight.",
    Regime.TRENDING_BEAR:   "Market trending strongly downward — trend and volatility signals dominate.",
    Regime.RANGING_LOW_VOL: "Low-volatility consolidation — mean-reversion signals carry extra weight.",
    Regime.HIGH_VOLATILITY: "Elevated volatility or fear — volatility and macro signals dominate.",
    Regime.UNKNOWN:         "Insufficient stored data to classify regime — equal weights applied.",
}

# UI accent colours for each regime
REGIME_COLOURS: dict[str, str] = {
    Regime.TRENDING_BULL:   "#26a69a",   # teal / green
    Regime.TRENDING_BEAR:   "#ef5350",   # red
    Regime.RANGING_LOW_VOL: "#6366f1",   # indigo / blue
    Regime.HIGH_VOLATILITY: "#f59e0b",   # amber
    Regime.UNKNOWN:         "#6b7280",   # grey
}

# Module weight profiles — must sum to 1.0 per regime
REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    Regime.TRENDING_BULL: {
        "trend":          0.35,
        "mean_reversion": 0.10,
        "volatility":     0.15,
        "volume":         0.25,
        "macro":          0.15,
    },
    Regime.TRENDING_BEAR: {
        "trend":          0.30,
        "mean_reversion": 0.15,
        "volatility":     0.25,
        "volume":         0.15,
        "macro":          0.15,
    },
    Regime.RANGING_LOW_VOL: {
        "trend":          0.10,
        "mean_reversion": 0.40,
        "volatility":     0.15,
        "volume":         0.20,
        "macro":          0.15,
    },
    Regime.HIGH_VOLATILITY: {
        "trend":          0.10,
        "mean_reversion": 0.15,
        "volatility":     0.40,
        "volume":         0.15,
        "macro":          0.20,
    },
    Regime.UNKNOWN: {
        "trend":          0.20,
        "mean_reversion": 0.20,
        "volatility":     0.20,
        "volume":         0.20,
        "macro":          0.20,
    },
}


def detect_regime() -> dict:
    """Detect the current market regime using stored ^GSPC indicators.

    Reads the most recent row from the ``indicators`` table for ^GSPC and
    classifies using the following priority-ordered rules:

    1. HIGH_VOLATILITY  — VIX > 30 (overrides all other conditions)
    2. TRENDING_BULL    — price > SMA200, ADX > 25, VIX < 20
    3. TRENDING_BEAR    — price < SMA200, ADX > 25, VIX > 20
    4. RANGING_LOW_VOL  — ADX < 20, VIX < 15
    5. UNKNOWN          — fallback when indicators are unavailable or ambiguous

    Returns:
        Dict with:
        - ``regime``      — :class:`Regime` enum value
        - ``weights``     — module weight dict for this regime
        - ``description`` — human-readable regime explanation
        - ``colour``      — hex accent colour for the UI
        - ``details``     — raw indicator values used for classification
    """
    details: dict = {
        "ticker": _REFERENCE_TICKER,
        "price":  None,
        "sma_200": None,
        "adx":    None,
        "vix":    None,
    }

    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT i.sma_200, i.adx, i.vix_level, p.close AS price "
                    "FROM indicators i "
                    "JOIN price_data p ON i.ticker = p.ticker AND i.date = p.date "
                    "WHERE i.ticker = :ticker "
                    "ORDER BY i.date DESC LIMIT 1"
                ),
                {"ticker": _REFERENCE_TICKER},
            ).mappings().first()
    except Exception as exc:
        logger.error("Regime detection DB query failed: %s", exc)
        row = None

    if row is None:
        regime = Regime.UNKNOWN
        return _build_result(regime, details)

    price   = row.get("price")
    sma_200 = row.get("sma_200")
    adx     = row.get("adx")
    vix     = row.get("vix_level")
    details.update({"price": price, "sma_200": sma_200, "adx": adx, "vix": vix})

    # Priority-ordered classification
    if vix is not None and float(vix) > 30:
        regime = Regime.HIGH_VOLATILITY
    elif (
        price is not None and sma_200 is not None
        and adx is not None and vix is not None
        and float(price) > float(sma_200)
        and float(adx) > 25
        and float(vix) < 20
    ):
        regime = Regime.TRENDING_BULL
    elif (
        price is not None and sma_200 is not None
        and adx is not None and vix is not None
        and float(price) < float(sma_200)
        and float(adx) > 25
        and float(vix) > 20
    ):
        regime = Regime.TRENDING_BEAR
    elif (
        adx is not None and vix is not None
        and float(adx) < 20
        and float(vix) < 15
    ):
        regime = Regime.RANGING_LOW_VOL
    else:
        regime = Regime.UNKNOWN

    return _build_result(regime, details)


def _build_result(regime: Regime, details: dict) -> dict:
    return {
        "regime":      regime,
        "weights":     REGIME_WEIGHTS[regime],
        "description": REGIME_DESCRIPTIONS[regime],
        "colour":      REGIME_COLOURS[regime],
        "details":     details,
    }
