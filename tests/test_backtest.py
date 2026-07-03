"""Tests for entry_signal_overrides gating in analysis/backtest.py:simulate_trades(),
and for the config-driven settings (max holding days, ATR stop multiplier,
exit_signal) that make the backtest mirror the live system rather than
accepting independent runtime override parameters.

simulate_trades() takes a pre-built signal DataFrame (as produced by
compute_signal_history()) and a plain config dict — it never touches the DB,
yfinance, or any broker API, so these tests build the signal history
directly with pandas.
"""

import inspect

import pandas as pd

from analysis.backtest import run_backtest, simulate_trades
from analysis.regime import get_max_holding_days

_CONFIG = {
    "backtest": {
        "atr_stop_multiplier": 2.0,
        "max_holding_days": 30,
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
    back to backtest.entry_signal (STRONG_BUY) — a plain BUY signal
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


# ---------------------------------------------------------------------------
# allowed_entry_regimes — the regime gate, checked before entry_signal_overrides
# ---------------------------------------------------------------------------

_GATED_CONFIG = {
    "backtest": {
        "atr_stop_multiplier": 2.0,
        "max_holding_days": 30,
        "entry_signal": "STRONG_BUY",
        "entry_signal_overrides": {
            "MIXED": "STRONG_BUY",
            "TRENDING_BULL": "STRONG_BUY",
            "TRENDING_BEAR": "STRONG_BUY",
        },
        "allowed_entry_regimes": ["TRENDING_BULL", "TRENDING_BEAR", "HIGH_VOLATILITY"],
    },
}


def test_no_trades_open_in_mixed_regime_even_on_strong_buy():
    """MIXED is excluded from allowed_entry_regimes, so no position should
    open there regardless of signal strength — even a STRONG_BUY signal
    (which would clear the entry_signal_overrides threshold on its own) must
    still be blocked by the regime gate, since the regime gate is checked
    first and entry_signal_overrides is never reached for a blocked regime."""
    df = _signal_df([_signal_row("MIXED", "STRONG_BUY", 100.0 + i) for i in range(5)])
    trades = simulate_trades(df, "TESTX", _GATED_CONFIG)
    assert trades == []


def test_no_trades_open_in_ranging_low_vol_regime():
    """RANGING_LOW_VOL is also excluded — insufficient backtest data to trust
    either way, so it's blocked the same as MIXED, not left open by omission."""
    df = _signal_df([_signal_row("RANGING_LOW_VOL", "STRONG_BUY", 100.0 + i) for i in range(5)])
    trades = simulate_trades(df, "TESTX", _GATED_CONFIG)
    assert trades == []


def test_trades_still_open_in_allowed_regime_with_gate_configured():
    """The gate excludes specific regimes, not entries in general — a regime
    that IS in allowed_entry_regimes must still open trades normally, subject
    only to the ordinary entry_signal_overrides threshold check."""
    df = _signal_df([_signal_row("TRENDING_BULL", "STRONG_BUY", 100.0 + i) for i in range(5)])
    trades = simulate_trades(df, "TESTX", _GATED_CONFIG)
    assert len(trades) == 1
    assert trades[0]["regime_at_entry"] == "TRENDING_BULL"


def test_open_trade_still_exits_normally_after_regime_becomes_blocked():
    """The regime gate only affects new entries. A trade opened while
    TRENDING_BULL was allowed must still exit normally (here: on signal
    reversal) even after the regime rolls into blocked MIXED — the exit
    phase is never gated by allowed_entry_regimes."""
    rows = (
        [_signal_row("TRENDING_BULL", "STRONG_BUY", 100.0 + i) for i in range(3)]
        + [_signal_row("MIXED", "STRONG_SELL", 103.0 - i) for i in range(3)]
    )
    df = _signal_df(rows)
    trades = simulate_trades(df, "TESTX", _GATED_CONFIG)
    assert len(trades) == 1
    assert trades[0]["regime_at_entry"] == "TRENDING_BULL"
    assert trades[0]["exit_reason"] == "SIGNAL_REVERSAL"


# ---------------------------------------------------------------------------
# get_max_holding_days() — per-ticker asset-class holding limits, the single
# source of truth used by both simulate_trades() (here) and the live T212
# exit checks (data/scheduler.py) so backtested and live holding-period
# limits always agree.
# ---------------------------------------------------------------------------

_HOLD_CONFIG = {
    "backtest": {
        "max_holding_days": 30,
        "max_holding_days_forex": 5,
        "max_holding_days_crypto": 14,
        "max_holding_days_bonds": 45,
    },
    "trading212_invest": {
        "max_holding_days": 90,
        "ucits_mapping": {
            "VOO": {"ticker": "I50Dl_EQ", "currency": "USD"},
            "TLT": {"ticker": "IDGAl_EQ", "currency": "USD"},
        },
    },
    "trading212_stocks": {
        "max_holding_days": 30,
    },
    "watchlist": {
        "us_large_cap": [
            {"ticker": "AAPL", "name": "Apple", "exchange": "NYSE", "tradeable": True},
        ],
    },
}


def test_get_max_holding_days_returns_90_for_ucits_mapping_etf():
    """A ticker in trading212_invest.ucits_mapping uses the ETF backstop
    (90 days), the same limit maybe_place_t212_orders() applies live — even
    though TLT is also in the bond-ETF set, ucits_mapping takes priority."""
    assert get_max_holding_days("VOO", _HOLD_CONFIG) == 90
    assert get_max_holding_days("TLT", _HOLD_CONFIG) == 90


def test_get_max_holding_days_returns_30_for_us_large_cap_stock():
    """A ticker in watchlist.us_large_cap (and not in ucits_mapping) uses the
    trading212_stocks backstop (30 days) — the stricter individual-stock limit."""
    assert get_max_holding_days("AAPL", _HOLD_CONFIG) == 30


def test_get_max_holding_days_returns_45_for_bond_etf_not_in_ucits_mapping():
    """SHY has no ucits_mapping entry (a documented gap — see CLAUDE.md) and
    isn't a us_large_cap stock, so it falls through to the bond-ETF bucket."""
    assert get_max_holding_days("SHY", _HOLD_CONFIG) == 45


def test_get_max_holding_days_returns_5_for_forex():
    assert get_max_holding_days("GBPUSD=X", _HOLD_CONFIG) == 5


def test_get_max_holding_days_returns_14_for_crypto():
    assert get_max_holding_days("BTC-USD", _HOLD_CONFIG) == 14


def test_get_max_holding_days_falls_back_to_default_for_everything_else():
    assert get_max_holding_days("XOM", _HOLD_CONFIG) == 30


def test_simulate_trades_applies_per_ticker_hold_limit_not_the_flat_default():
    """A ucits_mapping ETF held past the flat 30-day default but under its
    real 90-day ETF limit must NOT be forced out by MAX_HOLDING — proving
    simulate_trades() is using get_max_holding_days() per-ticker, not a
    single global backtest.max_holding_days value."""
    rows = [_signal_row("MIXED", "STRONG_BUY", 100.0)] + [
        _signal_row("MIXED", "NEUTRAL", 100.0 + i) for i in range(1, 40)
    ]
    df = _signal_df(rows)
    cfg = {
        "backtest": {"atr_stop_multiplier": 2.0, "max_holding_days": 30, "entry_signal": "STRONG_BUY"},
        "trading212_invest": {
            "max_holding_days": 90,
            "ucits_mapping": {"VOO": {"ticker": "I50Dl_EQ", "currency": "USD"}},
        },
    }
    trades = simulate_trades(df, "VOO", cfg)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] != "MAX_HOLDING"


# ---------------------------------------------------------------------------
# ATR stop multiplier and exit_signal — read from config, not accepted as
# independent override parameters.
# ---------------------------------------------------------------------------

def test_run_backtest_signature_has_no_atr_or_max_hold_override_params():
    """run_backtest() must read atr_stop_multiplier and max_holding_days from
    the config dict it's given, not accept them as separate parameters that
    would duplicate (and could drift from) config.yaml. Only date range,
    ticker scope, and run name are genuinely runtime-controlled."""
    params = set(inspect.signature(run_backtest).parameters)
    assert "atr_stop_multiplier" not in params
    assert "max_holding_days" not in params
    assert params == {"tickers", "start_date", "end_date", "config", "name"}


def test_simulate_trades_stop_loss_uses_atr_multiplier_from_config():
    """The stop-loss exit price must be entry_price - atr_stop_multiplier * ATR,
    using whatever multiplier is in config['backtest']['atr_stop_multiplier'] —
    proving the multiplier is read from config, not hardcoded or passed
    separately."""
    cfg = {
        "backtest": {"atr_stop_multiplier": 3.0, "max_holding_days": 30, "entry_signal": "STRONG_BUY"},
    }
    rows = [_signal_row("MIXED", "STRONG_BUY", 100.0)] + [
        {**_signal_row("MIXED", "NEUTRAL", 100.0 - 3.0 * 1.0), "atr": 1.0}
    ]
    df = _signal_df(rows)
    trades = simulate_trades(df, "TESTX", cfg)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "STOP_LOSS"
    assert trades[0]["exit_price"] == 100.0 - 3.0 * 1.0


def test_simulate_trades_exit_signal_strong_sell_only_ignores_plain_sell():
    """trading212_invest.exit_signal = 'STRONG_SELL' must restrict signal-
    reversal exits to STRONG_SELL only — a plain SELL must not close the
    position, mirroring the live exit_signal threshold."""
    cfg = {
        "backtest": {"atr_stop_multiplier": 2.0, "max_holding_days": 30, "entry_signal": "STRONG_BUY"},
        "trading212_invest": {"exit_signal": "STRONG_SELL"},
    }
    rows = (
        [_signal_row("MIXED", "STRONG_BUY", 100.0)]
        + [_signal_row("MIXED", "SELL", 100.0 + i) for i in range(1, 5)]
    )
    df = _signal_df(rows)
    trades = simulate_trades(df, "TESTX", cfg)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] != "SIGNAL_REVERSAL"


def test_simulate_trades_exit_signal_defaults_to_sell_when_unset():
    """With no trading212_invest.exit_signal key, the default is 'SELL' —
    a plain SELL signal must close a LONG position, matching current/prior
    (pre-config-gating) behaviour exactly."""
    cfg = {
        "backtest": {"atr_stop_multiplier": 2.0, "max_holding_days": 30, "entry_signal": "STRONG_BUY"},
    }
    rows = (
        [_signal_row("MIXED", "STRONG_BUY", 100.0)]
        + [_signal_row("MIXED", "SELL", 100.0 + i) for i in range(1, 5)]
    )
    df = _signal_df(rows)
    trades = simulate_trades(df, "TESTX", cfg)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "SIGNAL_REVERSAL"
