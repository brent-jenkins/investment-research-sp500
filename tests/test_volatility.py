"""Tests for VaR-based position sizing in analysis/volatility.py.

compute_var_position_size() (and its detailed sibling
compute_var_position_size_detailed()) size a T212 order so its expected
holding-period loss at a given confidence level stays within a configured
% of account value — see that module's docstrings for the full formula.
Tests here seed price_data directly via the db_engine fixture (in-memory
SQLite — tests/conftest.py) since the function reads price history through
data.ingestion.get_price_df(), never yfinance.
"""

from datetime import datetime, timedelta

import numpy as np
from sqlalchemy.orm import Session

from data.database import PriceData

_CONFIG = {
    "trading212_invest": {
        "var_confidence": 0.95,
        "var_lookback_days": 252,
        "max_risk_pct": 0.01,
        "min_position_gbp": 50,
    },
    "backtest": {"max_holding_days": 30},
}


def _seed_prices(engine, ticker: str, closes: list[float]) -> None:
    """Insert one PriceData row per close, most recent last, one calendar day apart."""
    n = len(closes)
    base = datetime.utcnow()
    with Session(engine) as session:
        for i, close in enumerate(closes):
            days_ago = n - i
            session.add(PriceData(
                ticker=ticker,
                date=base - timedelta(days=days_ago),
                open=close, high=close, low=close, close=close, volume=1_000_000,
            ))
        session.commit()


def _closes(seed: int, n: int, daily_vol: float, drift: float = 0.0) -> list[float]:
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(drift, daily_vol, n)
    return list(100 * np.exp(np.cumsum(log_returns)))


def test_high_volatility_ticker_gets_smaller_position_than_low_volatility(db_engine):
    from analysis.volatility import compute_var_position_size

    _seed_prices(db_engine, "CALM", _closes(seed=1, n=300, daily_vol=0.005))
    _seed_prices(db_engine, "WILD", _closes(seed=1, n=300, daily_vol=0.05))

    calm_position = compute_var_position_size("CALM", 10_000, 500, _CONFIG)
    wild_position = compute_var_position_size("WILD", 10_000, 500, _CONFIG)

    assert wild_position < calm_position


def test_position_capped_at_max_position_gbp(db_engine):
    """A near-flat (near-zero volatility) series would imply an enormous
    position from the raw VaR formula — the max_position_gbp ceiling must
    still apply regardless of how low volatility gets."""
    from analysis.volatility import compute_var_position_size

    _seed_prices(db_engine, "FLAT", _closes(seed=2, n=300, daily_vol=0.0005))

    position = compute_var_position_size("FLAT", 1_000_000, 500, _CONFIG)

    assert position == 500


def test_position_floored_at_min_position_gbp(db_engine):
    """Extreme volatility combined with a small account value would imply a
    tiny position from the raw VaR formula — min_position_gbp must still
    apply as a floor regardless of how high volatility gets."""
    from analysis.volatility import compute_var_position_size

    _seed_prices(db_engine, "EXTREME", _closes(seed=3, n=300, daily_vol=0.15))

    position = compute_var_position_size("EXTREME", 100, 500, _CONFIG)

    assert position == 50  # min_position_gbp from _CONFIG


def test_fallback_when_insufficient_history(db_engine, caplog):
    """Fewer than 60 days of price history -> falls back to
    max_position_gbp * 0.5 rather than sizing off a thin sample."""
    from analysis.volatility import compute_var_position_size

    _seed_prices(db_engine, "NEWLISTING", _closes(seed=4, n=30, daily_vol=0.02))

    position = compute_var_position_size("NEWLISTING", 10_000, 500, _CONFIG)

    assert position == 250.0  # 500 * 0.5


def test_fallback_when_no_price_history_at_all(db_engine):
    """No price_data rows for the ticker at all -> same fallback, not an error."""
    from analysis.volatility import compute_var_position_size

    position = compute_var_position_size("NEVERSEEN", 10_000, 500, _CONFIG)

    assert position == 250.0


def test_position_sizing_fixed_mode_returns_max_position_gbp_unchanged(db_engine, monkeypatch, mocker):
    """When trading212_invest.position_sizing is 'fixed' (or absent), the BUY
    phase must use the flat max_position_gbp ceiling and never call the VaR
    sizing function at all — the pre-VaR behaviour, reproduced exactly."""
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    monkeypatch.setattr("brokers.trading212.time.sleep", lambda *a, **k: None)

    mock_var = mocker.patch("brokers.trading212.compute_var_position_size_detailed")

    mock_client = mocker.MagicMock()
    mock_client.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 5_000.0},
        "investments": {"currentValue": 0.0},
    }
    mock_client.get_positions.return_value = []
    mock_client.get_instruments.return_value = []
    mock_client.get_quantity_for_gbp.return_value = 10.0
    mock_client.get_instrument_price_gbp.return_value = 50.0
    mock_client.place_market_order.return_value = {"id": "mock-order-1"}
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)

    from brokers.trading212 import maybe_place_t212_orders
    from data.database import Signal

    with Session(db_engine) as session:
        session.add(Signal(
            ticker="^GSPC", date=datetime.utcnow(), signal="NEUTRAL",
            confluence_score=0.0, regime="TRENDING_BULL",
        ))
        session.add(Signal(
            ticker="AAPL", date=datetime.utcnow(), signal="STRONG_BUY",
            confluence_score=0.6, regime="TRENDING_BULL",
        ))
        session.commit()

    config = {
        "trading212_invest": {
            "enabled": True,
            "live": False,
            "position_sizing": "fixed",  # explicit, but this is also the default when absent
            "max_position_gbp": 500,
            "max_total_deployed": 0.5,
            "entry_signal": "STRONG_BUY",
            "allowed_entry_regimes": ["TRENDING_BULL"],
            "exit_signal": "SELL",
            "ucits_mapping": {"AAPL": {"ticker": "AAPLUCITS_EQ", "currency": "USD"}},
        },
        "signals": {"reversal_confirmation_runs": 2},
    }
    maybe_place_t212_orders(config, run_type="full")

    mock_var.assert_not_called()
    mock_client.get_quantity_for_gbp.assert_called_once()
    # position_gbp is the second positional arg to get_quantity_for_gbp(ucits_ticker, position_gbp, ...)
    _args, _kwargs = mock_client.get_quantity_for_gbp.call_args
    assert _args[1] == 500  # max_position_gbp, uncapped by the (much larger) 25% free-cash limit


def test_no_look_ahead_bias_in_backtest_var_sizing():
    """simulate_trades() must size an entry's position using only prices
    strictly BEFORE the entry date — a volatility spike that happens AFTER
    entry must not change the position size that was already committed to.
    """
    import pandas as pd
    from analysis.backtest import simulate_trades

    n = 300
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    rng = np.random.default_rng(7)
    calm_returns = rng.normal(0, 0.005, n)
    closes_calm_future = 100 * np.exp(np.cumsum(calm_returns))

    entry_idx = 280  # comfortably >60 trading days into the series

    def _build_signals(closes: np.ndarray) -> pd.DataFrame:
        rows = []
        for i in range(n):
            sig = "STRONG_BUY" if i == entry_idx else ("SELL" if i == n - 1 else "NEUTRAL")
            rows.append({
                "close": closes[i], "atr": 1.0, "regime": "TRENDING_BULL", "signal": sig,
                "trend_score": 1, "mean_reversion_score": 0, "volatility_score": 0,
                "volume_score": 0, "macro_score": 0,
            })
        return pd.DataFrame(rows, index=dates)

    config = {
        "backtest": {
            "atr_stop_multiplier": 2.0, "max_holding_days": 15, "entry_signal": "STRONG_BUY",
            "entry_signal_overrides": {"TRENDING_BULL": "STRONG_BUY"},
            "starting_capital_gbp": 10000,
        },
        "trading212_invest": {
            "exit_signal": "SELL", "var_confidence": 0.95, "var_lookback_days": 252,
            "max_risk_pct": 0.01, "min_position_gbp": 50, "max_position_gbp": 500,
        },
    }

    # Baseline: calm prices both before AND after entry.
    trades_baseline = simulate_trades(_build_signals(closes_calm_future), "NOLOOKAHEAD", config)
    assert len(trades_baseline) == 1
    baseline_position = trades_baseline[0]["position_gbp"]

    # Same prices up to (and including) the entry date, but a huge volatility
    # spike injected AFTER entry — this must NOT change the entry's sizing.
    closes_spiked_future = closes_calm_future.copy()
    spike_returns = rng.normal(0, 0.25, n - entry_idx - 1)  # 50x the calm daily vol
    closes_spiked_future[entry_idx + 1:] = closes_spiked_future[entry_idx] * np.exp(np.cumsum(spike_returns))

    trades_spiked = simulate_trades(_build_signals(closes_spiked_future), "NOLOOKAHEAD", config)
    assert len(trades_spiked) == 1
    spiked_position = trades_spiked[0]["position_gbp"]

    assert spiked_position == baseline_position
