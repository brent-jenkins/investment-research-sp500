"""Tests for entry_signal_overrides gating in analysis/backtest.py:simulate_trades().

simulate_trades() takes a pre-built signal DataFrame (as produced by
compute_signal_history()) and a plain config dict — it never touches the DB,
yfinance, or any broker API, so these tests build the signal history
directly with pandas.
"""

import pandas as pd

from analysis.backtest import simulate_trades

_CONFIG = {
    "backtest": {
        "atr_stop_multiplier": 2.0,
        "max_holding_days": 30,
    },
    "virtual_trading": {
        "entry_signal": "STRONG_BUY",
        "entry_signal_overrides": {
            "MIXED": "STRONG_BUY",
            "TRENDING_BULL": "BUY",
            "TRENDING_BEAR": "BUY",
        },
    },
}


def _signal_row(regime: str, signal: str, close: float) -> dict:
    return {
        "close": close,
        "atr": 1.0,
        "regime": regime,
        "signal": signal,
        "trend_score": 1 if signal in ("BUY", "STRONG_BUY") else -1,
        "mean_reversion_score": 0,
        "volatility_score": 0,
        "volume_score": 0,
        "macro_score": 0,
    }


def _signal_df(rows: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="B")
    return pd.DataFrame(rows, index=idx)


def test_mixed_regime_buy_signal_does_not_open_a_trade():
    """MIXED overrides entry_signal to STRONG_BUY; a plain BUY signal must not
    be enough to open a simulated trade."""
    df = _signal_df([_signal_row("MIXED", "BUY", 100.0 + i) for i in range(5)])
    trades = simulate_trades(df, "TESTX", _CONFIG)
    assert trades == []


def test_mixed_regime_strong_buy_signal_opens_a_trade():
    """MIXED requires STRONG_BUY, and a STRONG_BUY signal meets that bar —
    a trade must open."""
    df = _signal_df([_signal_row("MIXED", "STRONG_BUY", 100.0 + i) for i in range(5)])
    trades = simulate_trades(df, "TESTX", _CONFIG)
    assert len(trades) == 1
    assert trades[0]["direction"] == "LONG"
    assert trades[0]["entry_signal"] == "STRONG_BUY"
    assert trades[0]["regime_at_entry"] == "MIXED"


def test_trending_bull_regime_buy_signal_opens_a_trade():
    """TRENDING_BULL overrides entry_signal to the looser BUY threshold, so a
    plain BUY signal is sufficient to open a trade in this regime — the same
    signal that is rejected in MIXED."""
    df = _signal_df([_signal_row("TRENDING_BULL", "BUY", 100.0 + i) for i in range(5)])
    trades = simulate_trades(df, "TESTX", _CONFIG)
    assert len(trades) == 1
    assert trades[0]["direction"] == "LONG"
    assert trades[0]["entry_signal"] == "BUY"
    assert trades[0]["regime_at_entry"] == "TRENDING_BULL"


def test_trending_bear_regime_sell_signal_opens_a_short_trade():
    """TRENDING_BEAR also loosens to BUY threshold (its SELL-side equivalent),
    so a plain SELL signal should open a SHORT trade."""
    df = _signal_df([_signal_row("TRENDING_BEAR", "SELL", 100.0 - i) for i in range(5)])
    trades = simulate_trades(df, "TESTX", _CONFIG)
    assert len(trades) == 1
    assert trades[0]["direction"] == "SHORT"
    assert trades[0]["entry_signal"] == "SELL"


def test_high_volatility_regime_with_no_override_falls_back_to_global_default():
    """HIGH_VOLATILITY has no entry in entry_signal_overrides, so it must fall
    back to virtual_trading.entry_signal (STRONG_BUY) — a plain BUY signal
    must not open a trade."""
    df = _signal_df([_signal_row("HIGH_VOLATILITY", "BUY", 100.0 + i) for i in range(5)])
    trades = simulate_trades(df, "TESTX", _CONFIG)
    assert trades == []


def test_regime_change_mid_series_applies_the_new_regimes_threshold():
    """The entry threshold is resolved per-date, not once for the whole run —
    a BUY signal that starts in MIXED (rejected) must still be able to open
    once the regime rolls into TRENDING_BULL (accepted) on a later date."""
    rows = (
        [_signal_row("MIXED", "BUY", 100.0 + i) for i in range(3)]
        + [_signal_row("TRENDING_BULL", "BUY", 110.0 + i) for i in range(3)]
    )
    df = _signal_df(rows)
    trades = simulate_trades(df, "TESTX", _CONFIG)
    assert len(trades) == 1
    assert trades[0]["regime_at_entry"] == "TRENDING_BULL"
    assert trades[0]["entry_date"] == df.index[3].date()
