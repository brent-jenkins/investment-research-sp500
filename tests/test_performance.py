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
    compute_sector_breakdown,
    get_account_value_history,
    get_brief_cards,
    get_crisis_status,
    get_current_account_summary,
    get_pending_candidates,
    get_pnl_rows,
    get_ticker_status,
    get_top_trades,
    get_unconfirmed_exits,
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


def test_ticker_status_explicit_tickers_bypasses_full_universe_lookup(mocker):
    """Regression for the 2026-08-04 fix: a real, measured ~60s Dashboard
    delay was traced to get_ticker_status() always computing status for the
    FULL live ~503-ticker candidate pool (via get_live_stock_candidates(),
    itself ~11s alone on production) even though get_brief_cards() only
    ever consults the handful of tickers with an open position. Passing
    `tickers=` explicitly must bypass get_live_stock_candidates() entirely,
    not just skip iterating the extra tickers."""
    bullish = _price_frame(400, 100.0, 0.0015)

    def _fake_get_price_df(ticker, days=8000):
        return bullish.copy()

    live_pool_lookup = mocker.patch(
        "analysis.universe.get_live_stock_candidates",
        side_effect=AssertionError("get_live_stock_candidates() must not be called when tickers= is given"),
    )
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)

    rows = get_ticker_status(_config(), tickers=["VOO"])
    assert len(rows) == 1
    assert rows[0]["ticker"] == "VOO"
    live_pool_lookup.assert_not_called()


def test_ticker_status_empty_tickers_list_returns_empty_without_lookup(mocker):
    live_pool_lookup = mocker.patch(
        "analysis.universe.get_live_stock_candidates",
        side_effect=AssertionError("must not be called for an explicit empty tickers list"),
    )
    assert get_ticker_status(_config(), tickers=[]) == []
    live_pool_lookup.assert_not_called()


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
# Dashboard functions (2026-08-02) — get_current_account_summary(),
# get_account_value_history(), get_top_trades(), get_pending_candidates(),
# get_unconfirmed_exits(). Backs the Dashboard page that replaced Daily
# Briefing — see CLAUDE.md's dedicated section.
# ---------------------------------------------------------------------------

def test_get_current_account_summary_no_snapshot_returns_zeros(db_engine):
    assert get_current_account_summary() == {
        "total_value_gbp": 0.0, "cash_gbp": 0.0, "invested_gbp": 0.0, "deployed_pct": 0.0,
    }


def test_get_current_account_summary_parses_real_snapshot(db_engine, mocker):
    import json
    snapshot = {"totalValue": 52340.50, "cash": {"availableToTrade": 47000.0},
                "investments": {"currentValue": 5340.50}}
    mocker.patch("output.performance.get_metadata_str", return_value=json.dumps(snapshot))

    result = get_current_account_summary()

    assert result["total_value_gbp"] == 52340.50
    assert result["cash_gbp"] == 47000.0
    assert result["invested_gbp"] == 5340.50
    assert result["deployed_pct"] == pytest.approx(5340.50 / 52340.50 * 100)


def test_get_current_account_summary_malformed_json_returns_zeros(db_engine, mocker):
    mocker.patch("output.performance.get_metadata_str", return_value="not valid json")
    assert get_current_account_summary()["total_value_gbp"] == 0.0


def _seed_value_history_row(engine, *, day_offset: int, total_value: float):
    from datetime import date, timedelta as _td
    d = (date.today() - _td(days=day_offset)).isoformat()
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO account_value_history (date, total_value_gbp, cash_gbp, invested_gbp, captured_at)
            VALUES (:d, :tv, :tv, 0, :now)
        """), {"d": d, "tv": total_value, "now": datetime.utcnow().isoformat()})


def test_get_account_value_history_empty_table_anchors_at_starting_balance(db_engine):
    """No real snapshots at all yet (the account was just reset) -- the
    chart must still show a real starting-balance anchor, not an empty
    result that would render as a blank/zero-looking chart."""
    rows = get_account_value_history(days=7, starting_balance_gbp=50_000.0)
    assert len(rows) == 1
    assert rows[0]["total_value_gbp"] == 50_000.0


def test_get_account_value_history_prepends_anchor_when_real_data_is_thinner_than_window(db_engine):
    """Real snapshots exist, but only for the last couple of days -- still
    thinner than the requested window, so the £50,000 anchor must still
    be prepended so the true starting point is visible."""
    _seed_value_history_row(db_engine, day_offset=1, total_value=50_120.0)
    _seed_value_history_row(db_engine, day_offset=0, total_value=50_340.0)

    rows = get_account_value_history(days=7, starting_balance_gbp=50_000.0)

    assert rows[0]["total_value_gbp"] == 50_000.0
    assert [r["total_value_gbp"] for r in rows[1:]] == [50_120.0, 50_340.0]


def test_get_account_value_history_no_anchor_needed_when_real_data_covers_full_range(db_engine):
    """Once real history genuinely covers the requested window, no
    synthetic point should be prepended -- only real data."""
    from datetime import date, timedelta as _td
    days = 7
    for offset in range(days, -1, -1):
        _seed_value_history_row(db_engine, day_offset=offset, total_value=50_000.0 + offset)

    rows = get_account_value_history(days=days, starting_balance_gbp=50_000.0)

    cutoff = (date.today() - _td(days=days)).isoformat()
    assert rows[0]["date"] == cutoff
    assert len(rows) == days + 1
    # No duplicate/synthetic row -- the real row already covers the cutoff date.
    assert sum(1 for r in rows if r["date"] == cutoff) == 1


def test_get_top_trades_empty(db_engine):
    assert get_top_trades() == ([], [])


def _seed_pnl_row(engine, *, ticker: str, pnl_gbp: float, exit_reason="SIGNAL_OFF"):
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pnl
                (ticker, t212_ticker, buy_order_id, sell_order_id, entry_date, exit_date,
                 entry_price, exit_price, quantity, currency, pnl_gbp, pnl_pct,
                 holding_days, is_etf, momentum_pct_at_entry, exit_reason, created_at)
            VALUES (:t, :t, :bid, :sid, :entry, :exit, 100.0, 100.0, 10, 'USD',
                    :pnl, :pnl_pct, 10, 0, 0.05, :reason, :now)
        """), {
            "t": ticker, "bid": hash(ticker) % 100000, "sid": (hash(ticker) % 100000) + 1,
            "entry": (datetime.utcnow() - timedelta(days=10)).isoformat(),
            "exit": datetime.utcnow().isoformat(), "pnl": pnl_gbp,
            "pnl_pct": pnl_gbp / 1000, "reason": exit_reason, "now": datetime.utcnow().isoformat(),
        })


def test_get_top_trades_ranks_winners_and_losers_correctly(db_engine):
    for ticker, pnl in [("A", 500), ("B", -300), ("C", 200), ("D", -100), ("E", 50)]:
        _seed_pnl_row(db_engine, ticker=ticker, pnl_gbp=pnl)

    winners, losers = get_top_trades(limit=5)

    assert [w["ticker"] for w in winners] == ["A", "C", "E"]
    assert [l["ticker"] for l in losers] == ["B", "D"]


def test_get_top_trades_respects_limit(db_engine):
    for i in range(8):
        _seed_pnl_row(db_engine, ticker=f"W{i}", pnl_gbp=100.0 + i)

    winners, _ = get_top_trades(limit=5)

    assert len(winners) == 5
    assert winners[0]["ticker"] == "W7"  # highest pnl_gbp first


def test_get_pending_candidates_empty(db_engine):
    assert get_pending_candidates() == []


def _seed_pending_row(engine, *, ticker: str, status: str, momentum_pct: float):
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, stop_price, var_pct, status, created_at)
            VALUES (:t, :t, 0, 'USD', :now, 100.0, 500.0, :mom, 90.0, 0.02, :status, :now)
        """), {"t": ticker, "mom": momentum_pct, "status": status, "now": datetime.utcnow().isoformat()})


def test_get_pending_candidates_returns_every_status_ordered_by_momentum(db_engine):
    """2026-08-03 fix — this used to filter to WHERE status = 'PENDING'
    only, so a resolved candidate (submitted, skipped, rejected, superseded)
    silently disappeared from every consumer even though its row was still
    sitting in pending_orders, fully within the 30-day retention window.
    Every status must now come back, still ordered by momentum descending."""
    _seed_pending_row(db_engine, ticker="LOWMOM", status="PENDING", momentum_pct=0.05)
    _seed_pending_row(db_engine, ticker="HIGHMOM", status="PENDING", momentum_pct=0.30)
    _seed_pending_row(db_engine, ticker="SUBMITTEDTICK", status="SUBMITTED", momentum_pct=0.50)
    _seed_pending_row(db_engine, ticker="SKIPPEDTICK", status="SKIPPED_PRICE_MOVED", momentum_pct=0.40)
    _seed_pending_row(db_engine, ticker="REJECTEDTICK", status="REJECTED_CAPACITY", momentum_pct=0.20)
    _seed_pending_row(db_engine, ticker="SUPERSEDEDTICK", status="SUPERSEDED", momentum_pct=0.10)

    rows = get_pending_candidates()

    assert [r["ticker"] for r in rows] == [
        "SUBMITTEDTICK", "SKIPPEDTICK", "HIGHMOM", "REJECTEDTICK", "SUPERSEDEDTICK", "LOWMOM",
    ]


def test_get_pending_candidates_includes_status_and_resolution_note(db_engine):
    """The dashboard needs the row's own real status and its resolution_note
    (why it resolved that way) to show a resolved candidate meaningfully,
    not just that it's no longer PENDING."""
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, stop_price, var_pct, status, resolution_note,
                 resolved_at, created_at)
            VALUES ('SNDK', 'SNDK1_US_EQ', 0, 'USD', :now, 1214.83, 806.81, 26.9979, 817.94,
                    -0.6197, 'SKIPPED_PRICE_MOVED',
                    'price moved +3.11% since decision (decided 1214.83 -> now 1252.56), exceeds 3% sanity threshold',
                    :now, :now)
        """), {"now": datetime.utcnow().isoformat()})

    rows = get_pending_candidates()

    assert len(rows) == 1
    assert rows[0]["status"] == "SKIPPED_PRICE_MOVED"
    assert "price moved +3.11%" in rows[0]["resolution_note"]


def test_get_unconfirmed_exits_empty(db_engine):
    assert get_unconfirmed_exits() == []


def test_get_unconfirmed_exits_returns_only_placed_sell_orders(db_engine):
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, quantity, exit_reason, placed_at, created_at)
            VALUES ('SELLPENDING', 'SELLPENDING_US_EQ', 'SELL', 'PLACED', 5, 'SIGNAL_OFF', :now, :now)
        """), {"now": datetime.utcnow().isoformat()})
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, quantity, exit_reason, placed_at, created_at)
            VALUES ('SELLFILLED', 'SELLFILLED_US_EQ', 'SELL', 'FILLED', 5, 'SIGNAL_OFF', :now, :now)
        """), {"now": datetime.utcnow().isoformat()})
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, quantity, placed_at, created_at)
            VALUES ('BUYPLACED', 'BUYPLACED_US_EQ', 'BUY', 'PLACED', 5, :now, :now)
        """), {"now": datetime.utcnow().isoformat()})

    rows = get_unconfirmed_exits()

    assert len(rows) == 1
    assert rows[0]["ticker"] == "SELLPENDING"


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


def test_brief_cards_lost_membership_alone_does_not_change_status(db_engine, monkeypatch, mocker):
    """Changed 2026-08-02 -- membership no longer participates in the live
    status classification at all (see analysis/decision.py:
    evaluate_exit_reason()'s own docstring). is_member=False, with the
    position otherwise healthy, must still read ON TRACK -- "INDEX
    REMOVAL" no longer exists as a possible status. Membership is still
    shown on the card (current_is_member), purely informational."""
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("brokers.trading212.Trading212Client._fx_rates", staticmethod(lambda: (1.27, 1.08)))

    _seed_buy_fill(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=50.0, is_etf=False, days_ago=10)
    _seed_resolution(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ")
    _fake_snapshot(mocker, "AAPL_US_EQ", 5, 100.0, 110.0)  # above stop, price rising

    status_rows = [{"ticker": "AAPL", "sig_on": True, "is_member": False, "crisis_now": False}]

    cards = get_brief_cards(_config(), ticker_status_rows=status_rows)
    assert cards[0]["status"] == "ON TRACK"
    assert cards[0]["current_is_member"] is False


def test_brief_cards_lost_membership_still_shows_stop_loss_when_genuinely_hit(db_engine, monkeypatch, mocker):
    """The flip side -- losing membership doesn't make a position immune
    to its own genuine exit conditions either. STOP_LOSS still wins over
    SIGNAL_OFF exactly as before, unaffected by is_member=False."""
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("brokers.trading212.Trading212Client._fx_rates", staticmethod(lambda: (1.27, 1.08)))

    _seed_buy_fill(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=99999.0, is_etf=False)
    _seed_resolution(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ")
    _fake_snapshot(mocker, "AAPL_US_EQ", 5, 100.0, 90.0)

    status_rows = [{"ticker": "AAPL", "sig_on": False, "is_member": False, "crisis_now": False}]

    cards = get_brief_cards(_config(), ticker_status_rows=status_rows)
    assert cards[0]["status"] == "STOP LOSS"


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


def test_brief_cards_without_ticker_status_rows_only_computes_status_for_held_tickers(
    db_engine, monkeypatch, mocker,
):
    """End-to-end regression for the 2026-08-04 fix, via the real code path
    a Dashboard render actually takes (ticker_status_rows omitted). AAPL has
    a VERIFIED resolution (a real, currently-tracked instrument) but no
    current open position; VOO does. get_live_stock_candidates() is mocked
    to return a large pool including both, plus many more tickers that were
    never resolved at all — get_price_df() must only ever be called for
    VOO, the sole ticker that's actually held, proving get_brief_cards()
    scopes its internal get_ticker_status() call correctly rather than
    silently falling back to computing status for the full pool."""
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch("brokers.trading212.Trading212Client._fx_rates", staticmethod(lambda: (1.27, 1.08)))

    _seed_buy_fill(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=1.0, is_etf=True, days_ago=1)
    _seed_resolution(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ")
    _seed_resolution(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ")  # resolved, but never held
    _fake_snapshot(mocker, "I50Dl_EQ", 10, 100.0, 105.0)  # only VOO appears in the live snapshot

    big_pool = ["VOO", "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA"]
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=big_pool)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=90)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)

    bullish = _price_frame(400, 100.0, 0.0015)
    calls: list[str] = []

    def _fake_get_price_df(ticker, days=8000):
        calls.append(ticker)
        # "^GSPC" is a legitimate, single, always-required read (the
        # crisis-check series inside get_ticker_status()) -- unrelated to
        # the per-candidate scoping this test guards. Any per-TICKER read
        # other than the one genuinely held position (VOO) is the
        # regression this test exists to catch.
        if ticker not in ("^GSPC", "VOO"):
            raise AssertionError(f"get_price_df() called for unheld ticker {ticker!r} — scoping fix regressed")
        return bullish.copy()

    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", side_effect=_fake_get_price_df)

    cards = get_brief_cards(_config())  # ticker_status_rows omitted -- the real Dashboard path
    assert len(cards) == 1
    assert cards[0]["ticker"] == "VOO"
    assert cards[0]["status"] == "ON TRACK"
    # "^GSPC" once (crisis series) + "VOO" once (the sole held ticker) --
    # never any of the other 7 pool tickers.
    assert calls == ["^GSPC", "VOO"]


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
    # entry_blocked now sources from brokers.trading212.is_crisis_mode_active_now()
    # (2026-08-03) -- a separate module-level get_price_df binding the
    # data.ingestion mock above does not reach, and which would otherwise
    # make a real DB/yfinance call. Mocked directly at that boundary.
    mocker.patch("brokers.trading212.is_crisis_mode_active_now", return_value=False)

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
    mocker.patch("brokers.trading212.is_crisis_mode_active_now", return_value=True)

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
    mocker.patch("brokers.trading212.is_crisis_mode_active_now", return_value=True)

    status = get_crisis_status()
    assert status["entry_blocked"] is True
    assert status["is_crisis_today"] is False
    assert status["days_since_trigger"] == 4


def test_crisis_status_entry_blocked_sources_from_authoritative_check_not_daily_bar_alone(mocker):
    """The decisive proof this defers to is_crisis_mode_active_now()
    rather than recomputing its own daily-bar-only verdict (2026-08-03):
    stored daily-bar data shows a completely calm day (is_crisis_today
    correctly False, computed independently from the same data), yet
    entry_blocked still follows the authoritative check when it reports
    True (the shape of a real intraday-only crisis, invisible to the
    daily-bar data alone)."""
    calm = _price_frame(60, 100.0, 0.001)
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", return_value=calm)
    mocker.patch("brokers.trading212.is_crisis_mode_active_now", return_value=True)

    status = get_crisis_status()
    assert status["is_crisis_today"] is False  # daily-bar data itself shows nothing wrong
    assert status["entry_blocked"] is True      # but the authoritative check says blocked


def test_crisis_status_entry_blocked_false_when_authoritative_check_cannot_determine(mocker):
    """is_crisis_mode_active_now() returning None (a check failure) must
    map to entry_blocked=False here -- a display-only function fails
    toward showing nothing, never a fabricated or stale verdict."""
    calm = _price_frame(60, 100.0, 0.001)
    import data.ingestion as ingestion_mod
    mocker.patch.object(ingestion_mod, "get_price_df", return_value=calm)
    mocker.patch("brokers.trading212.is_crisis_mode_active_now", return_value=None)

    status = get_crisis_status()
    assert status["entry_blocked"] is False


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


# ---------------------------------------------------------------------------
# compute_sector_breakdown() -- sector concentration of open positions, by
# count and by capital deployed. Reporting only, per CLAUDE.md's "Sector
# concentration monitoring" section -- no gating, no thresholds.
#
# READ-ONLY against ticker_sector (2026-08-09 fix) -- these tests seed the
# real (in-memory) DB via write_ticker_sector() rather than mocking
# data.ingestion.get_or_fetch_sector(), since compute_sector_breakdown() no
# longer calls that function at all -- see test_sector_breakdown_render_path_
# never_calls_yfinance below for the direct proof of that.
# ---------------------------------------------------------------------------

def _card(ticker: str, current_value_gbp: float) -> dict:
    return {"ticker": ticker, "current_value_gbp": current_value_gbp}


def test_sector_breakdown_empty_portfolio_returns_all_zero(db_engine):
    result = compute_sector_breakdown([])

    assert result["by_count"] == {}
    assert result["by_capital_gbp"] == {}
    assert result["by_capital_pct"] == {}
    assert result["total_positions"] == 0
    assert result["total_capital_gbp"] == 0.0


def test_sector_breakdown_count_and_capital_views_can_differ(db_engine):
    """A sector with fewer, larger positions can dominate the capital view
    while trailing on the count view -- both are reported, deliberately not
    collapsed into one number."""
    from data.database import write_ticker_sector
    write_ticker_sector("AAA", "Technology")
    write_ticker_sector("BBB", "Technology")
    write_ticker_sector("CCC", "Healthcare")

    cards = [_card("AAA", 1000.0), _card("BBB", 1000.0), _card("CCC", 8000.0)]
    result = compute_sector_breakdown(cards)

    assert result["by_count"] == {"Healthcare": 1, "Technology": 2}
    assert result["by_capital_gbp"] == {"Healthcare": 8000.0, "Technology": 2000.0}
    assert result["by_capital_pct"] == {"Healthcare": 80.0, "Technology": 20.0}
    assert result["total_positions"] == 3
    assert result["total_capital_gbp"] == 10000.0


def test_sector_breakdown_sorted_descending_by_capital(db_engine):
    from data.database import write_ticker_sector
    write_ticker_sector("AAA", "Small")
    write_ticker_sector("BBB", "Big")

    cards = [_card("AAA", 100.0), _card("BBB", 9900.0)]
    result = compute_sector_breakdown(cards)

    assert list(result["by_capital_gbp"].keys()) == ["Big", "Small"]
    assert list(result["by_count"].keys()) == ["Big", "Small"]  # same order in both views


def test_sector_breakdown_uncategorized_sector_still_counted_in_totals(db_engine):
    """A ticker with no ticker_sector row (never pre-warmed, or genuinely
    unresolved when it was) must not silently drop out of the totals -- it
    groups under 'Uncategorized' and still counts."""
    cards = [_card("MYSTERY", 5000.0)]
    result = compute_sector_breakdown(cards)

    assert result["by_count"] == {"Uncategorized": 1}
    assert result["by_capital_gbp"] == {"Uncategorized": 5000.0}
    assert result["total_positions"] == 1
    assert result["total_capital_gbp"] == 5000.0


def test_sector_breakdown_missing_capital_field_defaults_to_zero_not_a_crash(db_engine):
    """A card missing current_value_gbp entirely (should not happen in
    practice, but must not crash the aggregation) contributes 0 capital,
    still counts toward position count."""
    from data.database import write_ticker_sector
    write_ticker_sector("NOFIELD", "Technology")

    result = compute_sector_breakdown([{"ticker": "NOFIELD"}])

    assert result["by_count"] == {"Technology": 1}
    assert result["by_capital_gbp"] == {"Technology": 0.0}
    assert result["total_positions"] == 1


def test_sector_breakdown_mixed_known_and_uncategorized_sectors(db_engine):
    from data.database import write_ticker_sector
    write_ticker_sector("AAA", "Technology")
    write_ticker_sector("BBB", None)  # attempted, genuinely unresolved -- still a row
    write_ticker_sector("CCC", "Technology")

    cards = [_card("AAA", 3000.0), _card("BBB", 1000.0), _card("CCC", 6000.0)]
    result = compute_sector_breakdown(cards)

    assert result["by_count"] == {"Technology": 2, "Uncategorized": 1}
    assert result["by_capital_gbp"] == {"Technology": 9000.0, "Uncategorized": 1000.0}
    assert result["total_capital_gbp"] == 10000.0


def test_sector_breakdown_uncategorized_covers_both_never_cached_and_unresolved(db_engine):
    """Two different underlying states -- 'DDD' has never been looked up at
    all, 'BBB' was looked up and yfinance had nothing -- must display
    identically as 'Uncategorized'; the render path doesn't distinguish
    them (only the pre-warm script's own reporting does)."""
    from data.database import write_ticker_sector
    write_ticker_sector("BBB", None)  # attempted, unresolved

    cards = [_card("BBB", 1000.0), _card("DDD", 1000.0)]  # DDD: no row at all
    result = compute_sector_breakdown(cards)

    assert result["by_count"] == {"Uncategorized": 2}
    assert result["by_capital_gbp"] == {"Uncategorized": 2000.0}


def test_sector_breakdown_render_path_never_calls_yfinance(db_engine, mocker):
    """The render path must be a pure DB read -- a cache miss must never
    trigger a synchronous yfinance call during a page render or email
    generation. Direct proof: yf.Ticker is patched to raise if called at
    all; a ticker with no ticker_sector row must still resolve (to
    'Uncategorized') without tripping it."""
    mock_yf = mocker.patch(
        "data.ingestion.yf.Ticker",
        side_effect=AssertionError("compute_sector_breakdown() must never call yfinance"),
    )

    cards = [_card("NEVERCACHED", 5000.0)]
    result = compute_sector_breakdown(cards)

    assert result["by_count"] == {"Uncategorized": 1}
    mock_yf.assert_not_called()
