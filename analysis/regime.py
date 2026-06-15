"""Market regime detection using ^GSPC stored indicators as the reference."""

import logging
from enum import Enum
from typing import Optional

from sqlalchemy import text

from data.database import get_engine, get_metadata_str, set_metadata

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

# Module weight profiles — must sum to 1.0 per regime.
# Correlation is a cross-asset confirming signal; it gets 0.10 across all regimes
# with the savings taken from the dominant module(s) in each regime.
REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    Regime.TRENDING_BULL: {
        "trend":          0.30,  # was 0.35
        "mean_reversion": 0.10,
        "volatility":     0.15,
        "volume":         0.20,  # was 0.25
        "macro":          0.15,
        "correlation":    0.10,
    },
    Regime.TRENDING_BEAR: {
        "trend":          0.25,  # was 0.30
        "mean_reversion": 0.15,
        "volatility":     0.20,  # was 0.25
        "volume":         0.15,
        "macro":          0.15,
        "correlation":    0.10,
    },
    Regime.RANGING_LOW_VOL: {
        "trend":          0.10,
        "mean_reversion": 0.35,  # was 0.40
        "volatility":     0.15,
        "volume":         0.15,  # was 0.20
        "macro":          0.15,
        "correlation":    0.10,
    },
    Regime.HIGH_VOLATILITY: {
        "trend":          0.10,
        "mean_reversion": 0.15,
        "volatility":     0.35,  # was 0.40
        "volume":         0.15,
        "macro":          0.15,  # was 0.20
        "correlation":    0.10,
    },
    Regime.UNKNOWN: {
        "trend":          0.18,  # was 0.20
        "mean_reversion": 0.18,
        "volatility":     0.18,
        "volume":         0.18,
        "macro":          0.18,
        "correlation":    0.10,
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


def detect_overlays() -> dict[str, bool]:
    """Check stored correlations for significant pair divergences that signal a regime overlay.

    Reads the most recent row for each key pair from the ``correlations`` table.
    Returns immediately with all-False if no correlation data exists yet (fresh install).

    Overlay definitions:
    - ``MACRO_STRESS``:      TLT/^GSPC DIVERGING (z ≥ 1.5) — bonds and equities moving together,
                             breaking the traditional flight-to-safety relationship.
    - ``VOL_REGIME_SHIFT``:  ^VIX/^GSPC DIVERGING (z ≥ 1.5) — VIX and the market moving in the
                             same direction, signalling an atypical fear-gauge environment.

    Results are stored in ``system_metadata`` under ``active_overlays`` (comma-separated list
    of active keys, or ``none``) so the Signals page can display them without re-querying.

    Returns:
        Dict mapping overlay name → bool (True = currently active).
    """
    overlays: dict[str, bool] = {
        "MACRO_STRESS":     False,
        "VOL_REGIME_SHIFT": False,
        "CREDIT_STRESS":    False,
    }

    _PAIR_OVERLAY = [
        ("TLT",  "^GSPC", "MACRO_STRESS"),
        ("^VIX", "^GSPC", "VOL_REGIME_SHIFT"),
    ]

    try:
        engine = get_engine()
        with engine.connect() as conn:
            for ticker_a, ticker_b, overlay_key in _PAIR_OVERLAY:
                row = conn.execute(
                    text(
                        "SELECT divergence, z_score FROM correlations "
                        "WHERE ticker_a = :a AND ticker_b = :b "
                        "ORDER BY date DESC LIMIT 1"
                    ),
                    {"a": ticker_a, "b": ticker_b},
                ).mappings().first()

                if row and row["divergence"] == "DIVERGING":
                    z = abs(float(row["z_score"] or 0))
                    if z >= 1.5:
                        overlays[overlay_key] = True
                        logger.info(
                            "Overlay %s active: %s/%s z=%.2f",
                            overlay_key, ticker_a, ticker_b, z,
                        )
    except Exception as exc:
        logger.warning("Overlay detection failed: %s", exc)

    # CREDIT_STRESS: HYG underperforming TLT by > 1 standard deviation.
    # The spread z-score is computed in compute_all_pairs() (which runs before
    # generate_signals()) and stored in system_metadata to avoid a full
    # price-data recalculation here.
    try:
        spread_z_str = get_metadata_str("hyg_tlt_spread_z")
        if spread_z_str is not None:
            spread_z = float(spread_z_str)
            if spread_z < -1.0:
                overlays["CREDIT_STRESS"] = True
                logger.info(
                    "Overlay CREDIT_STRESS active: HYG/TLT return-spread z=%.2f", spread_z
                )
    except Exception as exc:
        logger.warning("CREDIT_STRESS detection failed: %s", exc)

    # Persist so the UI can display without recomputing
    active_str = ",".join(k for k, v in overlays.items() if v)
    try:
        set_metadata("active_overlays", active_str or "none")
    except Exception as exc:
        logger.warning("Could not store active_overlays: %s", exc)

    return overlays


# Plain-English descriptions shown on the Signals page
OVERLAY_DESCRIPTIONS: dict[str, dict] = {
    "MACRO_STRESS": {
        "label":  "Macro Stress",
        "colour": "#dc2626",
        "text": (
            "Long bonds (TLT) and equities (S&P 500) are moving together — this breaks "
            "the traditional flight-to-safety relationship where bonds rise when equities fall. "
            "Historically unusual and often precedes a macro regime shift. "
            "Buy signal confidence is reduced by 20% for all equity and bond assets."
        ),
    },
    "VOL_REGIME_SHIFT": {
        "label":  "Vol Regime Shift",
        "colour": "#f59e0b",
        "text": (
            "VIX and the S&P 500 are moving in the same direction — normally they are "
            "inversely correlated (VIX spikes when equities fall). This atypical behaviour "
            "suggests the fear gauge is mis-calibrated or that a structural shift is underway. "
            "Treat directional signals with extra caution."
        ),
    },
    "CREDIT_STRESS": {
        "label":  "Credit Stress",
        "colour": "#7c3aed",
        "text": (
            "High-yield bonds (HYG) are underperforming long Treasuries (TLT) by more than "
            "one standard deviation — a widening credit spread that historically precedes "
            "equity market stress. Falling junk bond prices relative to safe-haven bonds "
            "signal rising default-risk perception in the credit market. "
            "Buy signal confidence is reduced by 15% across all equity assets."
        ),
    },
}


def _build_result(regime: Regime, details: dict) -> dict:
    return {
        "regime":      regime,
        "weights":     REGIME_WEIGHTS[regime],
        "description": REGIME_DESCRIPTIONS[regime],
        "colour":      REGIME_COLOURS[regime],
        "details":     details,
    }
