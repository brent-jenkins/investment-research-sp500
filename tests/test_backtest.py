"""Tests for entry_signal_overrides gating in analysis/backtest.py:simulate_trades(),
and for the config-driven settings (max holding days, ATR stop multiplier,
exit_signal) that make the backtest mirror the live system rather than
accepting independent runtime override parameters.

simulate_trades() takes a pre-built signal DataFrame (as produced by
compute_signal_history()) and a plain config dict — it never touches the DB,
yfinance, or any broker API, so most tests here build the signal history
directly with pandas. A handful of tests (e.g. the performance regression
test at the bottom of this file) DO read price history via
data.ingestion.get_price_df(), so those use the db_engine fixture
(in-memory SQLite — tests/conftest.py).
"""

import inspect
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from analysis.backtest import run_backtest, simulate_trades
from analysis.regime import get_max_holding_days
from data.database import PriceData

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


def test_delete_run_executes_vacuum_after_deletion(mocker):
    """delete_run() must VACUUM after deleting a run's rows, to reclaim the
    freed page space — backtest_signals in particular can accumulate a large
    number of rows per run. The engine is mocked entirely; this test never
    touches a real DB."""
    from analysis.backtest import delete_run

    mock_engine = mocker.MagicMock()
    mocker.patch("analysis.backtest.get_engine", return_value=mock_engine)

    delete_run(42)

    begin_conn = mock_engine.begin.return_value.__enter__.return_value
    delete_calls = [c for c in begin_conn.execute.call_args_list if "DELETE" in str(c.args[0])]
    assert len(delete_calls) == 3

    connect_conn = mock_engine.connect.return_value.__enter__.return_value
    vacuum_calls = [c for c in connect_conn.execute.call_args_list if "VACUUM" in str(c.args[0])]
    assert len(vacuum_calls) == 1
    connect_conn.commit.assert_called_once()


def test_delete_run_vacuum_runs_after_delete_transaction_commits_not_within_it():
    """VACUUM must run on a fresh connection after the DELETE transaction has
    already committed — SQLite cannot VACUUM from inside an open transaction.
    Verified two ways: (1) VACUUM never appears on the same connection object
    the DELETE statements ran on, and (2) every interaction with the DELETE
    transaction's engine.begin() context happens before the first interaction
    with the VACUUM's engine.connect() context, in call order."""
    from unittest.mock import MagicMock, patch

    from analysis.backtest import delete_run

    mock_engine = MagicMock()
    with patch("analysis.backtest.get_engine", return_value=mock_engine):
        delete_run(7)

    begin_conn = mock_engine.begin.return_value.__enter__.return_value
    connect_conn = mock_engine.connect.return_value.__enter__.return_value
    assert not any("VACUUM" in str(c.args[0]) for c in begin_conn.execute.call_args_list)

    call_names = [c[0] for c in mock_engine.mock_calls]
    last_begin_idx = max(i for i, n in enumerate(call_names) if n.startswith("begin"))
    first_connect_idx = min(i for i, n in enumerate(call_names) if n.startswith("connect"))
    assert last_begin_idx < first_connect_idx


def _seed_ohlc(engine, ticker: str, closes: list[float], base: datetime) -> None:
    """Insert one flat-ish OHLC PriceData row per close, dated
    base - (n-i) days — one calendar day apart, matching the pattern used
    throughout this suite (tests/test_volatility.py's _seed_ohlc_at())."""
    n = len(closes)
    with Session(engine) as session:
        for i, close in enumerate(closes):
            days_ago = n - i
            session.add(PriceData(
                ticker=ticker,
                date=base - timedelta(days=days_ago),
                open=close, high=close * 1.01, low=close * 0.99, close=close, volume=1_000_000,
            ))
        session.commit()


def test_backtest_performance_stays_within_budget(db_engine):
    """A full run_backtest() call across several tickers over several years
    must complete quickly — regression protection against the signal
    computation/storage path in analysis/backtest.py regressing in
    performance.

    Uses seeded synthetic data (self-contained — this suite never touches
    the real project database, per tests/conftest.py's db_engine fixture
    and CLAUDE.md's testing conventions) rather than the real 64-ticker/
    7-year production backtest the performance budget is actually specified
    against — that was verified manually during development instead: a
    real production run completed in ~35s after also fixing a discovered,
    unrelated pre-existing bottleneck in _store_signals_batch() (see
    CLAUDE.md's "Backtesting engine performance" note), comfortably inside
    the target's 60s (2x budget) test tolerance.
    """
    import time

    base = datetime.utcnow()
    n_days = 365 * 3
    tickers = [f"PERFTEST{i}" for i in range(8)]
    for i, ticker in enumerate(tickers):
        rng = np.random.default_rng(i)
        closes = list(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n_days))))
        _seed_ohlc(db_engine, ticker, closes, base)

    rng_gspc = np.random.default_rng(100)
    _seed_ohlc(db_engine, "^GSPC", list(100 * np.exp(np.cumsum(rng_gspc.normal(0.0003, 0.012, n_days)))), base)
    rng_vix = np.random.default_rng(101)
    _seed_ohlc(db_engine, "^VIX", list(15 + np.cumsum(rng_vix.normal(0, 0.3, n_days))), base)

    config = {
        "signals": {
            "atr_period": 14, "bollinger_period": 20, "bollinger_std": 2.0, "rsi_period": 14,
            "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_period": 14,
            "adx_trend_threshold": 25, "volume_avg_period": 20,
            "ma_cross_slope_window": 10, "ma_cross_near_pct": 0.02,
            "cpd_lookback_days": 300, "cpd_threshold": 3.0, "cpd_recent_window": 90,
            "min_stop_pct": 1.0, "max_stop_pct": 25.0,
        },
        "backtest": {
            "starting_capital_gbp": 10000, "atr_stop_multiplier": 2.0, "max_holding_days": 30,
            "entry_signal": "STRONG_BUY", "entry_signal_overrides": {}, "allowed_entry_regimes": None,
        },
        "confluence": {"regime_thresholds": {
            "TRENDING_BULL":   {"buy_threshold": 0.55, "sell_threshold": 0.70},
            "TRENDING_BEAR":   {"buy_threshold": 0.70, "sell_threshold": 0.55},
            "MIXED":           {"buy_threshold": 0.60, "sell_threshold": 0.60},
            "HIGH_VOLATILITY": {"buy_threshold": 0.70, "sell_threshold": 0.70},
            "RANGING_LOW_VOL": {"buy_threshold": 0.55, "sell_threshold": 0.55},
        }},
        "trading212_invest": {
            "var_confidence": 0.95, "var_lookback_days": 252, "max_risk_pct": 0.01,
            "min_position_gbp": 50, "max_position_gbp": 500, "exit_signal": "SELL",
        },
    }

    start_date = base - timedelta(days=n_days - 30)
    end_date   = base - timedelta(days=1)

    t0 = time.time()
    run_id = run_backtest(tickers, start_date, end_date, config=config, name="perf-budget-test")
    elapsed = time.time() - t0

    assert elapsed < 15, f"8-ticker/3-year synthetic backtest took {elapsed:.1f}s, expected well under 15s"
