"""Tests for analysis/regime.py: detect_regime() and resolve_entry_signal().

detect_regime() reads its inputs from the indicators/price_data tables, so
these tests use the db_engine fixture (in-memory SQLite) to seed exactly the
^GSPC row each classification rule depends on. resolve_entry_signal() is a
pure function over a config dict and needs no DB at all.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from analysis.regime import Regime, detect_regime, resolve_entry_signal
from data.database import Indicator, PriceData


def _seed_gspc(engine, *, price: float, sma_200: float, adx: float, vix: float) -> None:
    """Insert one matching indicators + price_data row for ^GSPC on a fixed date.

    detect_regime() joins indicators and price_data ON date, so both rows
    must share the exact same date value for the query to return a match.
    """
    as_of = datetime(2026, 1, 15)
    with Session(engine) as session:
        session.add(PriceData(ticker="^GSPC", date=as_of, close=price))
        session.add(Indicator(ticker="^GSPC", date=as_of, sma_200=sma_200, adx=adx, vix_level=vix))
        session.commit()


def test_high_volatility_when_vix_above_30(db_engine):
    """VIX > 30 overrides every other condition and forces HIGH_VOLATILITY."""
    _seed_gspc(db_engine, price=100, sma_200=90, adx=10, vix=35)
    result = detect_regime()
    assert result["regime"] == Regime.HIGH_VOLATILITY


def test_trending_bull_when_price_above_sma200_strong_adx_low_vix(db_engine):
    """price > SMA200, ADX > 25, VIX < 20 -> TRENDING_BULL."""
    _seed_gspc(db_engine, price=110, sma_200=100, adx=30, vix=15)
    result = detect_regime()
    assert result["regime"] == Regime.TRENDING_BULL


def test_trending_bear_when_price_below_sma200_strong_adx_elevated_vix(db_engine):
    """price < SMA200, ADX > 25, VIX > 20 (but not > 30) -> TRENDING_BEAR."""
    _seed_gspc(db_engine, price=90, sma_200=100, adx=30, vix=25)
    result = detect_regime()
    assert result["regime"] == Regime.TRENDING_BEAR


def test_ranging_low_vol_when_adx_and_vix_both_low(db_engine):
    """ADX < 20 and VIX < 15, with no trend or fear signal -> RANGING_LOW_VOL."""
    _seed_gspc(db_engine, price=100, sma_200=100, adx=15, vix=10)
    result = detect_regime()
    assert result["regime"] == Regime.RANGING_LOW_VOL


def test_mixed_when_no_rule_applies_cleanly(db_engine):
    """ADX and VIX both in the 'unclear' band (fail every other rule) -> MIXED."""
    _seed_gspc(db_engine, price=100, sma_200=100, adx=22, vix=18)
    result = detect_regime()
    assert result["regime"] == Regime.MIXED


def test_mixed_when_no_indicator_data_exists(db_engine):
    """With no ^GSPC row at all the fallback must be conservative -> MIXED."""
    result = detect_regime()
    assert result["regime"] == Regime.MIXED


# ---------------------------------------------------------------------------
# resolve_entry_signal() — pure function, no DB involved
# ---------------------------------------------------------------------------

_OVERRIDE_CFG = {
    "entry_signal": "BUY",
    "entry_signal_overrides": {
        "MIXED": "STRONG_BUY",
        "RANGING_LOW_VOL": "STRONG_BUY",
        "HIGH_VOLATILITY": "STRONG_BUY",
        "TRENDING_BULL": "BUY",
        "TRENDING_BEAR": "BUY",
    },
}


def test_resolve_entry_signal_returns_override_for_each_regime():
    """Every regime with an explicit override entry must return that value,
    not the global default — this is the core per-regime gating behaviour."""
    for regime_name, expected in _OVERRIDE_CFG["entry_signal_overrides"].items():
        assert resolve_entry_signal(_OVERRIDE_CFG, regime_name) == expected


def test_resolve_entry_signal_falls_back_to_global_default_for_unknown_regime():
    """A regime name with no entry in entry_signal_overrides must fall back to
    the global entry_signal, not raise or silently default to STRONG_BUY."""
    assert resolve_entry_signal(_OVERRIDE_CFG, "SOME_UNKNOWN_REGIME") == "BUY"


def test_resolve_entry_signal_falls_back_to_global_default_when_regime_is_none():
    """A None regime (e.g. no signal history yet) must use the global default
    rather than crash on a dict lookup with a None key."""
    assert resolve_entry_signal(_OVERRIDE_CFG, None) == "BUY"


def test_resolve_entry_signal_defaults_to_strong_buy_when_entry_signal_unset():
    """With no entry_signal key at all in cfg, the documented default is
    STRONG_BUY (the strictest, safest choice)."""
    assert resolve_entry_signal({}, "MIXED") == "STRONG_BUY"
