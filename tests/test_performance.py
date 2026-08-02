"""Tests for output/performance.py — the display data-shaping logic behind
the Performance/Status/Daily-Briefing pages.

main.py itself cannot be imported in tests (it starts a real scheduler at
module level) — see output/performance.py's own docstring for why this
module exists as a separate, directly-testable extraction.
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text as sa_text

from output.performance import (
    get_brief_cards,
    get_crisis_status,
    get_pnl_rows,
    get_ticker_status,
    pnl_metrics_df,
)


def _price_frame(n_days: int, start_price: float, daily_drift: float) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=n_days)
    closes = [start_price]
    for _ in range(n_days - 1):
        closes.append(closes[-1] * (1 + daily_drift))
    closes = np.array(closes)
    return pd.DataFrame({
        "date": dates, "open": closes, "high": closes * 1.01,
        "low": closes * 0.99, "close": closes,
    })


def _config() -> dict:
    return {"trading212_invest": {}}


# ---------------------------------------------------------------------------
# get_ticker_status — tickers now come from
# analysis.universe.get_live_stock_candidates() (the dynamic, DB-backed
# live candidate pool), not a static config.yaml `instruments:` mapping —
# see CLAUDE.md's "wire the momentum-only, full-S&P-500 selection into
# live execution" section. Mocked directly rather than seeded via the DB,
# since these tests only care about get_ticker_status()'s own per-ticker
# behaviour, not universe membership reconstruction (already covered by
# tests/test_universe.py).
# ---------------------------------------------------------------------------

def test_ticker_status_empty_mapping_returns_empty(mocker):
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=[])
    assert get_ticker_status(_config()) == []


def test_ticker_status_gspc_unavailable_returns_empty(mocker):
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO"])
    mocker.patch("analysis.signals.compute_signal_series")
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", return_value=pd.DataFrame())
    assert get_ticker_status(_config()) == []


def test_ticker_status_bullish_ticker_reports_long(mocker):
    bullish = _price_frame(400, 100.0, 0.0015)

    def _fake_get_price_df(ticker, days=8000):
        return bullish.copy()

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO"])
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)

    rows = get_ticker_status(_config())
    assert len(rows) == 1
    r = rows[0]
    assert r["sig_on"] is True
    assert r["is_member"] is True
    assert r["crisis_now"] is False
    assert r["entry_blocked"] is False
    assert r["momentum_pct"] > 0
    assert r["insufficient_history"] is False


def test_ticker_status_insufficient_history_flagged(mocker):
    short_frame = _price_frame(50, 100.0, 0.001)  # < 300 rows

    def _fake_get_price_df(ticker, days=8000):
        return short_frame.copy()

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO"])
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", side_effect=_fake_get_price_df)

    rows = get_ticker_status(_config())
    assert len(rows) == 1
    assert rows[0]["insufficient_history"] is True
    assert rows[0]["sig_on"] is None


def test_ticker_status_per_ticker_exception_does_not_break_others(mocker):
    bullish = _price_frame(400, 100.0, 0.0015)

    def _fake_get_price_df(ticker, days=8000):
        if ticker == "VOO":
            raise RuntimeError("boom")
        return bullish.copy()

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO", "AAPL"])
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)

    rows = get_ticker_status(_config())
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["VOO"]["insufficient_history"] is True
    assert by_ticker["AAPL"]["insufficient_history"] is False
    assert by_ticker["AAPL"]["sig_on"] is True


# ---------------------------------------------------------------------------
# get_pnl_rows / pnl_metrics_df
# ---------------------------------------------------------------------------

def test_get_pnl_rows_empty(db_engine):
    assert get_pnl_rows() == []


def test_get_pnl_rows_returns_seeded_rows(db_engine):
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pnl
                (ticker, t212_ticker, buy_order_id, sell_order_id, entry_date, exit_date,
                 entry_price, exit_price, quantity, currency, pnl_gbp, pnl_pct,
                 holding_days, is_etf, momentum_pct_at_entry, exit_reason, created_at)
            VALUES
                ('VOO', 'I50Dl_EQ', 1, 2, :entry, :exit, 100.0, 110.0, 10, 'USD',
                 78.7, 0.10, 10, 1, 0.05, 'SIGNAL_OFF', :now)
        """), {
            "entry": (datetime.utcnow() - timedelta(days=10)).isoformat(),
            "exit": datetime.utcnow().isoformat(),
            "now": datetime.utcnow().isoformat(),
        })

    rows = get_pnl_rows()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "VOO"
    assert rows[0]["exit_reason"] == "SIGNAL_OFF"


def test_pnl_metrics_df_empty():
    assert pnl_metrics_df([]).empty


def test_pnl_metrics_df_shapes_columns():
    rows = [{
        "ticker": "VOO", "entry_date": "2026-07-01", "exit_date": "2026-07-11",
        "pnl_pct": 0.05, "pnl_gbp": 50.0,
    }, {
        "ticker": "AAPL", "entry_date": "2026-07-05", "exit_date": "2026-07-15",
        "pnl_pct": -0.02, "pnl_gbp": -20.0,
    }]
    df = pnl_metrics_df(rows)
    assert list(df["is_win"]) == [True, False]
    assert (df["direction"] == "LONG").all()
    assert pd.api.types.is_datetime64_any_dtype(df["entry_date"])


# ---------------------------------------------------------------------------
# get_brief_cards — exit precedence must match brokers/trading212.py exactly
# ---------------------------------------------------------------------------

def _seed_buy_fill(engine, *, ticker, t212_ticker, stop_price, is_etf, momentum_pct=0.05, days_ago=10):
    entry_dt = datetime.utcnow() - timedelta(days=days_ago)
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, momentum_pct, stop_price, account_type, currency_code,
                 placed_at, filled_at, created_at)
            VALUES (:t, :tt, 'BUY', 10, 100.0, 10, 'FILLED', :etf, :mom, :stop, 'DEMO', 'USD',
                    :at, :at, :at)
        """), {"t": ticker, "tt": t212_ticker, "etf": is_etf, "mom": momentum_pct,
               "stop": stop_price, "at": entry_dt.isoformat()})


def _fake_snapshot(mocker, t212_ticker, quantity, avg_price, current_price):
    import json
    positions = [{"ticker": t212_ticker, "quantity": quantity, "averagePrice": avg_price,
                  "currentPrice": current_price, "ppl": (current_price - avg_price) * quantity}]
    mocker.patch("output.performance.get_metadata_str", return_value=json.dumps(positions))


def _seed_resolution(engine, *, ticker, t212_ticker, currency="USD"):
    """Seed one VERIFIED row into instrument_resolutions — replaces the
    old static config.yaml `instruments:` mapping these tests used to
    pass via _config(mapping=...). See CLAUDE.md's "wire the momentum-
    only, full-S&P-500 selection into live execution" section."""
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO instrument_resolutions
                (ticker, t212_instrument_id, currency, status, note, attempted_at, resolved_at)
            VALUES (:t, :tt, :ccy, 'VERIFIED', 'seeded for test', :now, :now)
        """), {"t": ticker, "tt": t212_ticker, "ccy": currency, "now": datetime.utcnow().isoformat()})


def test_brief_cards_no_credentials_returns_empty(db_engine, monkeypatch):
    monkeypatch.delenv("T212_API_KEY", raising=False)
    monkeypatch.delenv("T212_API_SECRET", raising=False)
    assert get_brief_cards(_config()) == []


def test_brief_cards_no_verified_resolutions_returns_empty(db_engine, monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    assert get_brief_cards(_config()) == []


def test_brief_cards_crisis_takes_priority(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("brokers.trading212.Trading212Client._fx_rates", staticmethod(lambda: (1.27, 1.08)))

    _seed_buy_fill(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=99999.0, is_etf=True)
    _seed_resolution(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ")
    _fake_snapshot(mocker, "I50Dl_EQ", 10, 100.0, 105.0)

    status_rows = [{"ticker": "VOO", "sig_on": True, "is_member": True, "crisis_now": True}]

    cards = get_brief_cards(_config(), ticker_status_rows=status_rows)
    assert len(cards) == 1
    assert cards[0]["status"] == "CRISIS EXIT"


def test_brief_cards_index_removal_before_stop_and_signal(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("brokers.trading212.Trading212Client._fx_rates", staticmethod(lambda: (1.27, 1.08)))

    _seed_buy_fill(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=99999.0, is_etf=False)
    _seed_resolution(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ")
    _fake_snapshot(mocker, "AAPL_US_EQ", 5, 100.0, 90.0)

    status_rows = [{"ticker": "AAPL", "sig_on": False, "is_member": False, "crisis_now": False}]

    cards = get_brief_cards(_config(), ticker_status_rows=status_rows)
    assert cards[0]["status"] == "INDEX REMOVAL"


def test_brief_cards_stop_loss_before_signal_off(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("brokers.trading212.Trading212Client._fx_rates", staticmethod(lambda: (1.27, 1.08)))

    _seed_buy_fill(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=95.0, is_etf=True)
    _seed_resolution(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ")
    _fake_snapshot(mocker, "I50Dl_EQ", 10, 100.0, 90.0)  # price 90 <= stop 95

    status_rows = [{"ticker": "VOO", "sig_on": False, "is_member": True, "crisis_now": False}]

    cards = get_brief_cards(_config(), ticker_status_rows=status_rows)
    assert cards[0]["status"] == "STOP LOSS"


def test_brief_cards_signal_off_before_max_holding(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("brokers.trading212.Trading212Client._fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    _seed_buy_fill(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=1.0,
                    is_etf=True, days_ago=999)  # would also trigger MAX_HOLDING
    _seed_resolution(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ")
    _fake_snapshot(mocker, "I50Dl_EQ", 10, 100.0, 105.0)

    status_rows = [{"ticker": "VOO", "sig_on": False, "is_member": True, "crisis_now": False}]

    cards = get_brief_cards(_config(), ticker_status_rows=status_rows)
    assert cards[0]["status"] == "SIGNAL OFF"


def test_brief_cards_max_holding_when_nothing_else_fires(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("brokers.trading212.Trading212Client._fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    _seed_buy_fill(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=1.0,
                    is_etf=True, days_ago=999)
    _seed_resolution(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ")
    _fake_snapshot(mocker, "I50Dl_EQ", 10, 100.0, 105.0)

    status_rows = [{"ticker": "VOO", "sig_on": True, "is_member": True, "crisis_now": False}]

    cards = get_brief_cards(_config(), ticker_status_rows=status_rows)
    assert cards[0]["status"] == "MAX HOLDING"


def test_brief_cards_on_track_when_nothing_fires(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("brokers.trading212.Trading212Client._fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch("analysis.universe.get_max_holding_days", return_value=90)

    _seed_buy_fill(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=1.0,
                    is_etf=True, days_ago=1)
    _seed_resolution(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ")
    _fake_snapshot(mocker, "I50Dl_EQ", 10, 100.0, 105.0)

    status_rows = [{"ticker": "VOO", "sig_on": True, "is_member": True, "crisis_now": False}]

    cards = get_brief_cards(_config(), ticker_status_rows=status_rows)
    assert cards[0]["status"] == "ON TRACK"
    assert cards[0]["unreal_pct"] == pytest.approx(5.0)


def test_brief_cards_skips_ticker_with_no_open_position(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("output.performance.get_metadata_str", return_value="[]")

    _seed_resolution(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ")
    assert get_brief_cards(_config(), ticker_status_rows=[]) == []


# ---------------------------------------------------------------------------
# get_crisis_status
# ---------------------------------------------------------------------------

def test_crisis_status_empty_price_data_returns_empty(mocker):
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", return_value=pd.DataFrame())
    assert get_crisis_status() == {}


def test_crisis_status_calm_day_reports_no_override(mocker):
    calm = _price_frame(60, 100.0, 0.001)  # steady small gains, no crisis day
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", return_value=calm)

    status = get_crisis_status()
    assert status["is_crisis_today"] is False
    assert status["entry_blocked"] is False
    assert status["threshold_pct"] == pytest.approx(-7.0)
    assert status["today_return_pct"] is not None
    assert status["distance_to_threshold_pp"] > 0


def test_crisis_status_today_is_crisis_day(mocker):
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=30)
    closes = [100.0] * 29 + [100.0 * 0.90]  # -10% on the last day
    df = pd.DataFrame({
        "date": dates, "open": closes, "high": closes, "low": closes, "close": closes,
    })
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", return_value=df)

    status = get_crisis_status()
    assert status["is_crisis_today"] is True
    assert status["entry_blocked"] is True
    assert status["days_since_trigger"] == 0
    assert status["last_trigger_date"] is not None


def test_crisis_status_within_cooldown_after_trigger(mocker):
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=30)
    closes = [100.0] * 25 + [90.0, 90.0, 90.0, 90.0, 90.0]
    closes[25] = 90.0  # -10% trigger 4 trading days before the last row
    df = pd.DataFrame({
        "date": dates, "open": closes, "high": closes, "low": closes, "close": closes,
    })
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", return_value=df)

    status = get_crisis_status()
    assert status["entry_blocked"] is True
    assert status["is_crisis_today"] is False
    assert status["days_since_trigger"] == 4


# ---------------------------------------------------------------------------
# Position context — decision-relevant, neutral-tone position card content
# added 2026-07-31 (see CLAUDE.md's "..." section for the tone requirements
# this exists to satisfy: no color-coded urgency, no ranking, no CTAs).
# ---------------------------------------------------------------------------

from output.performance import (
    POSITIONS_RULES_EXPLANATION,
    compute_position_context,
    position_context_style,
    position_exit_rule_text,
    positions_summary_text,
)


def test_compute_position_context_computes_pct_above_stop_and_days_remaining():
    ctx = compute_position_context({
        "live_price": 110.0, "stop_price": 100.0,
        "days_held": 12, "max_holding_days": 90,
    })
    assert ctx["pct_above_stop"] == pytest.approx(10.0)
    assert ctx["days_remaining"] == 78


def test_compute_position_context_handles_missing_fields():
    assert compute_position_context({})["pct_above_stop"] is None
    assert compute_position_context({})["days_remaining"] is None
    assert compute_position_context({"live_price": 110.0})["pct_above_stop"] is None
    assert compute_position_context({"days_held": 5})["days_remaining"] is None


def test_compute_position_context_zero_stop_price_does_not_divide_by_zero():
    ctx = compute_position_context({"live_price": 100.0, "stop_price": 0.0})
    assert ctx["pct_above_stop"] is None


def test_compute_position_context_price_below_stop_is_still_a_plain_number():
    """A position past its stop is still just a fact, not a special case --
    e.g. mid-exit, live_price can sit right at or fractionally below the
    stop; the function must not raise or special-case it."""
    ctx = compute_position_context({
        "live_price": 99.0, "stop_price": 100.0,
        "days_held": 5, "max_holding_days": 30,
    })
    assert ctx["pct_above_stop"] == pytest.approx(-1.0)


def test_position_context_style_is_neutral_regardless_of_proximity():
    """The core tone requirement: no position is ever styled differently
    based on how close it is to its stop or its holding deadline. Sweeps
    a wide range of values on both sides of every meaningful threshold
    (deep in-the-money, a hair above stop, exactly at stop, past stop;
    plenty of days left, one day left, zero days left, overdue) and
    asserts the returned colour never varies."""
    pct_values = [500.0, 50.0, 5.0, 1.0, 0.1, 0.0, -0.1, -5.0, None]
    days_values = [365, 30, 5, 1, 0, -1, None]

    colours = {
        position_context_style(pct, days)
        for pct in pct_values
        for days in days_values
    }
    assert len(colours) == 1, f"styling must be invariant to proximity, got: {colours}"


def test_position_exit_rule_text_is_descriptive_not_predictive():
    text = position_exit_rule_text(90)
    assert "90" in text
    assert "stop-loss hit" in text
    assert "momentum turning negative" in text
    # No alarmist/predictive language.
    for word in ("risk", "danger", "urgent", "warning", "act now", "review"):
        assert word not in text.lower()


def test_position_exit_rule_text_handles_missing_max_holding_days():
    text = position_exit_rule_text(None)
    assert "stop-loss" in text.lower()


def test_positions_summary_text_pluralises_correctly():
    assert positions_summary_text(0) == "No positions open."
    assert positions_summary_text(1) == "1 position open — portfolio operating within its normal rules."
    assert positions_summary_text(3) == "3 positions open — portfolio operating within its normal rules."


def test_positions_rules_explanation_has_no_call_to_action_language():
    for phrase in ("click", "review", "close this", "consider", "you should"):
        assert phrase not in POSITIONS_RULES_EXPLANATION.lower()
