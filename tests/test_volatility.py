"""Tests for analysis/volatility.py: VaR-based position sizing and change
point detection (CPD).

compute_var_position_size() (and its detailed sibling
compute_var_position_size_detailed()) size a T212 order so its expected
holding-period loss at a given confidence level stays within a configured
% of account value — see that module's docstrings for the full formula.

detect_change_points() (and its integration into compute_volatility())
identifies structural breaks in a ticker's return-variance regime — see the
"Change point detection" section further down and that module's docstrings.

Tests here seed price_data directly via the db_engine fixture (in-memory
SQLite — tests/conftest.py) since the functions read price history through
data.ingestion.get_price_df(), never yfinance.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
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


# ---------------------------------------------------------------------------
# Change point detection (detect_change_points(), and its integration into
# compute_volatility()) — analysis/volatility.py
# ---------------------------------------------------------------------------
#
# detect_change_points() identifies the most recent date a ticker's return
# variance structurally shifted, via a cumulative-sum-of-squares statistic
# (ICSS-style — see that function's docstring for why a literal rolling-
# variance CUSUM control chart was tried first and rejected: it false-
# triggers on the autocorrelation a rolling window itself introduces).

_CPD_CONFIG = {
    "signals": {
        "atr_period": 14, "bollinger_period": 20, "bollinger_std": 2.0,
        "cpd_lookback_days": 300, "cpd_threshold": 3.0, "cpd_recent_window": 90,
    }
}


def _seed_ohlc_at(engine, ticker: str, closes: list[float], base: datetime) -> None:
    """Insert one flat OHLC PriceData row per close, dated base - (n-i) days.

    Takes an explicit *base* (rather than calling datetime.utcnow() itself,
    as _seed_prices() above does) so tests can compute the exact expected
    calendar date of a synthetic break using the same reference point used
    for seeding, without a race against the wall clock.
    """
    n = len(closes)
    with Session(engine) as session:
        for i, close in enumerate(closes):
            days_ago = n - i
            session.add(PriceData(
                ticker=ticker,
                date=base - timedelta(days=days_ago),
                open=close, high=close, low=close, close=close, volume=1_000_000,
            ))
        session.commit()


def _closes_with_break(
    seed: int, n: int, break_idx: int, vol_before: float, vol_after: float,
) -> list[float]:
    """Synthetic close series: vol_before daily vol for the first break_idx
    days, vol_after for the rest."""
    rng = np.random.default_rng(seed)
    rets = np.empty(n)
    rets[:break_idx] = rng.normal(0, vol_before, break_idx)
    rets[break_idx:] = rng.normal(0, vol_after, n - break_idx)
    return list(100 * np.exp(np.cumsum(rets)))


def test_detect_change_points_finds_known_break_within_5_trading_days(db_engine):
    """A synthetic series with an obvious variance shift (low vol first
    portion, high vol second) must be located within +/-5 trading days of
    the true break — the seed/break position here are empirically verified
    (see the change-point prototyping in this feature's implementation) to
    land within +/-1 day, well inside the required tolerance."""
    from analysis.volatility import detect_change_points

    n, break_idx = 300, 250
    base = datetime.utcnow()
    closes = _closes_with_break(seed=10, n=n, break_idx=break_idx, vol_before=0.01, vol_after=0.04)
    _seed_ohlc_at(db_engine, "BREAKY", closes, base)

    result = detect_change_points("BREAKY", _CPD_CONFIG)

    assert result["last_change_point"] is not None
    expected_date = (base - timedelta(days=n - break_idx)).date()
    assert abs((result["last_change_point"] - expected_date).days) <= 5
    # break is ~50 days before the latest stored date -- within the 90-day
    # cpd_recent_window, so this also exercises the "recent" flag.
    assert result["structural_break"] is True
    assert result["pre_change_vol"] is not None
    assert result["post_change_vol"] is not None
    assert result["post_change_vol"] > result["pre_change_vol"]


def test_detect_change_points_no_break_in_stable_series(db_engine):
    """A series with constant volatility throughout must report no change
    point at all, not a spurious one."""
    from analysis.volatility import detect_change_points

    n = 300
    base = datetime.utcnow()
    closes = _closes_with_break(seed=5, n=n, break_idx=150, vol_before=0.015, vol_after=0.015)
    _seed_ohlc_at(db_engine, "STABLE", closes, base)

    result = detect_change_points("STABLE", _CPD_CONFIG)

    assert result["last_change_point"] is None
    assert result["days_since_change"] is None
    assert result["structural_break"] is False


def test_structural_break_flag_only_true_within_recent_window(db_engine):
    """structural_break must be True for a break inside cpd_recent_window
    (90 days) and False for an equally-detectable break further back."""
    from analysis.volatility import detect_change_points

    n = 300
    base = datetime.utcnow()

    recent_closes = _closes_with_break(seed=10, n=n, break_idx=250, vol_before=0.01, vol_after=0.04)
    _seed_ohlc_at(db_engine, "RECENTBREAK", recent_closes, base)
    recent = detect_change_points("RECENTBREAK", _CPD_CONFIG)
    assert recent["last_change_point"] is not None
    assert recent["structural_break"] is True

    old_closes = _closes_with_break(seed=3, n=n, break_idx=100, vol_before=0.01, vol_after=0.04)
    _seed_ohlc_at(db_engine, "OLDBREAK", old_closes, base)
    old = detect_change_points("OLDBREAK", _CPD_CONFIG)
    assert old["last_change_point"] is not None
    assert old["days_since_change"] > 90
    assert old["structural_break"] is False


def test_post_change_point_data_used_for_atr_when_recent_break(db_engine):
    """A recent structural break must recompute ATR from only the post-break
    slice, not the full fixed window — see compute_volatility()'s docstring.
    hist_vol_20 is mathematically invariant to this slice (a trailing
    rolling(20) read only ever looks at the last 20 rows, identical either
    way), so this test checks atr_pct, the indicator that actually responds
    to the slice via ATR's EWM smoothing."""
    from analysis.volatility import compute_volatility
    from data.ingestion import get_price_df

    n, break_idx = 300, 272  # 28 rows post-break -- clears the 21-row floor
    base = datetime.utcnow()
    closes = _closes_with_break(seed=10, n=n, break_idx=break_idx, vol_before=0.01, vol_after=0.05)
    _seed_ohlc_at(db_engine, "POSTCHANGE", closes, base)

    df = get_price_df("POSTCHANGE", days=3650)

    with_cpd = compute_volatility(df, _CPD_CONFIG, vix_close=None, ticker="POSTCHANGE")
    without_cpd = compute_volatility(df, _CPD_CONFIG, vix_close=None, ticker=None)

    assert with_cpd["structural_break"] is True
    assert without_cpd["structural_break"] is False  # ticker=None skips CPD entirely
    assert with_cpd["atr_pct"] != without_cpd["atr_pct"]


def test_score_reduced_when_vol_expands_after_structural_break(mocker):
    """post_change_vol > pre_change_vol * 1.5 after a recent break subtracts
    1 from the volatility score. detect_change_points() is mocked with a
    controlled result so this isolates just the score-adjustment branch from
    the ATR/VIX percentile scoring exercised by the other tests above."""
    from analysis.volatility import compute_volatility

    n = 300
    dates = list(pd.date_range("2023-01-01", periods=n, freq="D"))
    closes = _closes_with_break(seed=0, n=n, break_idx=n, vol_before=0.012, vol_after=0.012)
    df = pd.DataFrame({
        "date": dates, "open": closes, "close": closes,
        "high": [c * 1.005 for c in closes], "low": [c * 0.995 for c in closes],
    })

    baseline = compute_volatility(df, _CPD_CONFIG, vix_close=None, ticker=None)
    assert baseline["score"] == 0  # seed=0 empirically verified neutral (see prototyping)

    mocker.patch(
        "analysis.volatility.detect_change_points",
        return_value={
            "last_change_point": dates[280].date(),
            "days_since_change": 19,
            "pre_change_vol": 0.15,
            "post_change_vol": 0.30,  # 2x -- above the 1.5x expansion threshold
            "vol_regime": "ELEVATED",
            "structural_break": True,
        },
    )
    result = compute_volatility(df, _CPD_CONFIG, vix_close=None, ticker="MOCKED")

    assert result["score"] == baseline["score"] - 1


def test_score_increased_when_vol_compresses_after_structural_break(mocker):
    """post_change_vol < pre_change_vol * 0.67 after a recent break adds 1
    to the volatility score."""
    from analysis.volatility import compute_volatility

    n = 300
    dates = list(pd.date_range("2023-01-01", periods=n, freq="D"))
    closes = _closes_with_break(seed=0, n=n, break_idx=n, vol_before=0.012, vol_after=0.012)
    df = pd.DataFrame({
        "date": dates, "open": closes, "close": closes,
        "high": [c * 1.005 for c in closes], "low": [c * 0.995 for c in closes],
    })

    baseline = compute_volatility(df, _CPD_CONFIG, vix_close=None, ticker=None)
    assert baseline["score"] == 0

    mocker.patch(
        "analysis.volatility.detect_change_points",
        return_value={
            "last_change_point": dates[280].date(),
            "days_since_change": 19,
            "pre_change_vol": 0.30,
            "post_change_vol": 0.15,  # half -- below the 0.67x compression threshold
            "vol_regime": "LOW",
            "structural_break": True,
        },
    )
    result = compute_volatility(df, _CPD_CONFIG, vix_close=None, ticker="MOCKED")

    assert result["score"] == baseline["score"] + 1


def test_hist_vol_20_disagreement_has_no_effect_on_score():
    """compute_volatility()'s score is an equal-weight combination of ATR%
    percentile, VIX regime, and the change-point adjustment — hist_vol_20
    (20-day annualised realised vol) is computed and returned for storage/
    display only (Charts page indicator panel), never fed into the score. A
    production correlation analysis (200 rows, 49 tickers, 2026-07-05)
    measured ATR% vs hist_vol_20 at 0.929 — both are trailing realised-
    volatility measures, so scoring both would double-count one signal.

    Feeds two OHLC series that share an identical wide intraday wick
    (+/-5%, which dominates True Range and therefore ATR%/atr_score) but
    very different close-to-close volatility (0.1% vs 3% daily std, which
    is all hist_vol_20 reads) — a deliberate ATR%/hist_vol_20 divergence.
    Both must score identically despite hist_vol_20 swinging from ~1% to
    ~37% annualised, proving hist_vol_20 has no effect on the score."""
    from analysis.volatility import compute_volatility

    def _build(n: int, close_noise_std: float, wick_pct: float, seed: int) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        log_rets = rng.normal(0, close_noise_std, n)
        closes = 100 * np.exp(np.cumsum(log_rets))
        dates = pd.date_range("2015-01-01", periods=n, freq="B")
        return pd.DataFrame({
            "date": dates, "close": closes,
            "high": closes * (1 + wick_pct), "low": closes * (1 - wick_pct), "open": closes,
        })

    config = {"signals": {"atr_period": 14, "bollinger_period": 20, "bollinger_std": 2.0}}

    df_calm_close = _build(300, close_noise_std=0.001, wick_pct=0.05, seed=1)
    df_vol_close  = _build(300, close_noise_std=0.03,  wick_pct=0.05, seed=1)

    result_calm = compute_volatility(df_calm_close, config, vix_close=15.0, ticker=None)
    result_vol  = compute_volatility(df_vol_close, config, vix_close=15.0, ticker=None)

    # Sanity: hist_vol_20 genuinely diverges between the two series (~1% vs
    # ~37% annualised) while atr_pct barely moves (dominated by the shared
    # wick, not the close path).
    assert result_vol["hist_vol_20"] > result_calm["hist_vol_20"] * 10
    assert result_calm["atr_pct"] == pytest.approx(result_vol["atr_pct"], rel=0.02)

    assert result_calm["score"] == result_vol["score"]


def test_change_point_detection_no_look_ahead_bias(db_engine):
    """Appending further (non-eventful) trailing data after an already-
    detected break must not retroactively relocate the detected break to a
    new, spurious position — only a trailing/expanding window is ever used,
    never a centered one that would let later data influence where an
    earlier break is placed. Some small drift in the exact date is expected
    even so: cpd_lookback_days is a fixed-length trailing window, so once 60
    more days are appended the window itself slides forward and no longer
    starts at exactly the same historical date — it's a materially
    different (though overlapping) slice, not "the same data plus a tail",
    so exact reproduction isn't the property being tested here."""
    from analysis.volatility import detect_change_points

    n, break_idx = 300, 250
    base = datetime.utcnow()
    closes = _closes_with_break(seed=10, n=n, break_idx=break_idx, vol_before=0.01, vol_after=0.04)
    _seed_ohlc_at(db_engine, "NOLOOKAHEAD", closes, base)
    short_result = detect_change_points("NOLOOKAHEAD", _CPD_CONFIG)
    assert short_result["last_change_point"] is not None

    # Extend with 60 more days in the SAME (post-break) vol regime -- no new
    # break -- and re-seed under a fresh ticker with the later "today".
    rng_extra = np.random.default_rng(99)
    extra_rets = rng_extra.normal(0, 0.04, 60)
    extended_closes = closes + list(closes[-1] * np.exp(np.cumsum(extra_rets)))
    later_base = base + timedelta(days=60)
    _seed_ohlc_at(db_engine, "NOLOOKAHEAD_EXT", extended_closes, later_base)
    extended_result = detect_change_points("NOLOOKAHEAD_EXT", _CPD_CONFIG)

    assert extended_result["last_change_point"] is not None
    assert abs((extended_result["last_change_point"] - short_result["last_change_point"]).days) <= 5


# ---------------------------------------------------------------------------
# GARCH(1,1) dynamic stop loss (compute_garch_stop()) — analysis/volatility.py
# ---------------------------------------------------------------------------
#
# Replaces the fixed ATR(14) x atr_stop_multiplier stop with a forward-
# looking 1-day GARCH volatility forecast scaled to the position's holding
# period, falling back to the old ATR stop when GARCH can't be trusted.

_GARCH_CONFIG = {
    "signals": {
        "atr_period": 14, "bollinger_period": 20, "bollinger_std": 2.0,
        "garch_lookback_days": 300, "garch_stop_multiplier": 2.0,
        "garch_min_observations": 100, "cpd_recent_window": 90,
        "min_stop_pct": 0.5, "max_stop_pct": 80.0,
    },
    "backtest": {"atr_stop_multiplier": 2.0, "max_holding_days": 30},
}


def _seed_ohlc_wicked_at(
    engine, ticker: str, closes: list[float], wick_pct: float, base: datetime,
) -> None:
    """Insert one OHLC row per close with a fixed +/-wick_pct intraday
    high/low band around each close, independent of the actual close-to-
    close move. Used to inflate ATR (which reads high/low) without touching
    GARCH's forecast (which only ever reads close-to-close log returns) —
    isolates the "low volatility" test from ATR's separate high/low-driven
    behaviour, which a fixed close-only volatility can't otherwise control.
    """
    n = len(closes)
    with Session(engine) as session:
        for i, close in enumerate(closes):
            days_ago = n - i
            session.add(PriceData(
                ticker=ticker,
                date=base - timedelta(days=days_ago),
                open=close, high=close * (1 + wick_pct), low=close * (1 - wick_pct),
                close=close, volume=1_000_000,
            ))
        session.commit()


def test_garch_stop_wider_than_atr_in_high_volatility_period(db_engine):
    """A ticker whose close-to-close returns recently became much more
    volatile must get a GARCH stop wider than the equivalent ATR stop —
    GARCH's forward-looking forecast reacts to the new regime, and the
    holding-period scaling (sqrt(holding_days)) applied on the GARCH side
    (see compute_garch_stop()'s docstring) widens it further relative to
    ATR(14), which has no comparable holding-period scaling."""
    from analysis.volatility import compute_garch_stop, _atr_fallback_stop

    n, break_idx = 300, 250
    base = datetime.utcnow()
    closes = _closes_with_break(seed=42, n=n, break_idx=break_idx, vol_before=0.01, vol_after=0.05)
    _seed_ohlc_at(db_engine, "GARCHHIGHVOL", closes, base)

    garch = compute_garch_stop("GARCHHIGHVOL", entry_price=closes[-1], direction="LONG", config=_GARCH_CONFIG)
    atr = _atr_fallback_stop("GARCHHIGHVOL", closes[-1], "LONG", _GARCH_CONFIG)

    assert garch["vol_source"] == "garch"
    assert garch["stop_distance_pct"] > atr["stop_distance_pct"]


def test_garch_stop_tighter_than_atr_in_low_volatility_period(db_engine):
    """A ticker with genuinely calm close-to-close returns but wide intraday
    wicks (independent of the actual day-to-day move) must get a GARCH stop
    tighter than the equivalent ATR stop — ATR reads high/low and is
    inflated by the wicks; GARCH only ever reads closes, so it correctly
    reflects the calm underlying return series regardless of intraday
    noise."""
    from analysis.volatility import compute_garch_stop, _atr_fallback_stop

    n = 300
    base = datetime.utcnow()
    rng = np.random.default_rng(7)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, 0.005, n))))
    _seed_ohlc_wicked_at(db_engine, "GARCHLOWVOL", closes, wick_pct=0.05, base=base)

    garch = compute_garch_stop("GARCHLOWVOL", entry_price=closes[-1], direction="LONG", config=_GARCH_CONFIG)
    atr = _atr_fallback_stop("GARCHLOWVOL", closes[-1], "LONG", _GARCH_CONFIG)

    assert garch["vol_source"] == "garch"
    assert garch["stop_distance_pct"] < atr["stop_distance_pct"]


def test_garch_stop_distance_bounded_by_min_and_max_stop_pct(db_engine):
    """Stop distance must never fall outside [min_stop_pct, max_stop_pct],
    regardless of how extreme the underlying volatility is."""
    from analysis.volatility import compute_garch_stop

    n = 300
    base = datetime.utcnow()

    rng_calm = np.random.default_rng(11)
    calm_closes = list(100 * np.exp(np.cumsum(rng_calm.normal(0, 0.0005, n))))
    _seed_ohlc_at(db_engine, "GARCHFLOOR", calm_closes, base)
    floor_result = compute_garch_stop("GARCHFLOOR", entry_price=calm_closes[-1], direction="LONG", config=_GARCH_CONFIG)
    assert floor_result["stop_distance_pct"] >= _GARCH_CONFIG["signals"]["min_stop_pct"]

    rng_wild = np.random.default_rng(12)
    wild_closes = list(100 * np.exp(np.cumsum(rng_wild.normal(0, 0.15, n))))
    _seed_ohlc_at(db_engine, "GARCHCEIL", wild_closes, base)
    ceil_result = compute_garch_stop("GARCHCEIL", entry_price=wild_closes[-1], direction="LONG", config=_GARCH_CONFIG)
    assert ceil_result["stop_distance_pct"] <= _GARCH_CONFIG["signals"]["max_stop_pct"]


def test_garch_stop_falls_back_to_atr_with_too_few_observations(db_engine):
    """Fewer than garch_min_observations daily returns -> ATR fallback, not
    an attempted (and likely unstable) GARCH fit on a thin sample."""
    from analysis.volatility import compute_garch_stop

    base = datetime.utcnow()
    closes = _closes_with_break(seed=1, n=50, break_idx=50, vol_before=0.02, vol_after=0.02)
    _seed_ohlc_at(db_engine, "GARCHTHIN", closes, base)

    result = compute_garch_stop("GARCHTHIN", entry_price=closes[-1], direction="LONG", config=_GARCH_CONFIG)

    assert result["vol_source"] == "atr_fallback"
    assert result["garch_daily_vol"] is None
    assert result["stop_price"] is not None


def test_garch_stop_falls_back_to_atr_when_fitting_fails(db_engine, mocker):
    """A GARCH fitting exception (convergence error, numerical error, etc.)
    must fall back to the ATR stop rather than propagating or returning a
    broken result."""
    from analysis.volatility import compute_garch_stop

    base = datetime.utcnow()
    closes = _closes_with_break(seed=2, n=300, break_idx=300, vol_before=0.015, vol_after=0.015)
    _seed_ohlc_at(db_engine, "GARCHFAIL", closes, base)

    mocker.patch("arch.arch_model", side_effect=RuntimeError("mock convergence failure"))

    result = compute_garch_stop("GARCHFAIL", entry_price=closes[-1], direction="LONG", config=_GARCH_CONFIG)

    assert result["vol_source"] == "atr_fallback"
    assert result["garch_daily_vol"] is None
    assert result["stop_price"] is not None


def test_garch_stop_uses_only_post_change_point_data(db_engine):
    """A recent, in-window change_point_date must calibrate GARCH on only
    the post-break data — verified by comparing against the unrestricted
    (full-window, blended) fit, which is measurably different when the
    pre-break segment is a dramatically different regime (here: a crash
    followed by a calm period).

    Overrides this test file's own _GARCH_CONFIG (garch_min_observations:
    100, used elsewhere in this file for test isolation) down to 60 so a
    break within cpd_recent_window (90 days) can supply enough post-break
    rows to qualify — 60 is what config.yaml itself now ships (lowered from
    an original 100, which made this branch unreachable: a break young
    enough to count as "recent" could never supply 100 post-break rows —
    see the code comment in compute_garch_stop() next to this check).
    """
    from analysis.volatility import compute_garch_stop

    config = {
        "signals": {**_GARCH_CONFIG["signals"], "garch_min_observations": 60},
        "backtest": _GARCH_CONFIG["backtest"],
    }

    n, break_idx = 300, 230  # 70 days post-break: within the 90-day recent window, >= 60 min observations
    base = datetime.utcnow()
    closes = _closes_with_break(seed=3, n=n, break_idx=break_idx, vol_before=0.15, vol_after=0.006)
    _seed_ohlc_at(db_engine, "GARCHCPD", closes, base)
    change_date = (base - timedelta(days=n - break_idx)).date()

    blended = compute_garch_stop("GARCHCPD", entry_price=closes[-1], direction="LONG", config=config, change_point_date=None)
    post_only = compute_garch_stop("GARCHCPD", entry_price=closes[-1], direction="LONG", config=config, change_point_date=change_date)

    assert blended["garch_daily_vol"] != post_only["garch_daily_vol"]
    # Post-change-only calibration reflects the calm regime (~0.006 daily
    # vol) much more closely than the blended fit, which is dragged up by
    # the crash-era history.
    assert post_only["garch_daily_vol"] < blended["garch_daily_vol"]


def test_garch_stop_long_below_entry_short_above_entry(db_engine):
    """LONG stop must sit below entry price; SHORT stop must sit above it."""
    from analysis.volatility import compute_garch_stop

    base = datetime.utcnow()
    closes = _closes_with_break(seed=4, n=300, break_idx=300, vol_before=0.015, vol_after=0.015)
    _seed_ohlc_at(db_engine, "GARCHDIR", closes, base)
    entry_price = closes[-1]

    long_result = compute_garch_stop("GARCHDIR", entry_price=entry_price, direction="LONG", config=_GARCH_CONFIG)
    short_result = compute_garch_stop("GARCHDIR", entry_price=entry_price, direction="SHORT", config=_GARCH_CONFIG)

    assert long_result["stop_price"] < entry_price
    assert short_result["stop_price"] > entry_price
