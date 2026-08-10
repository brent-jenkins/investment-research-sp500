"""Benchmark-relative performance tracker (2026-08-10) -- the demo account
against buy-and-hold ^SP500TR. See CLAUDE.md's "Benchmark-relative
performance tracker" section.

Four things this file must prove:
1. The EOD-side computation (data/scheduler.py:_refresh_benchmark_comparison())
   anchors on the account's OWN earliest real row, computes
   anchor_balance * close(date)/close(anchor), upserts rather than
   duplicating, and creates NO synthetic history before the account existed.
2. Missing benchmark closes fall back to the most recent EARLIER close and
   never a later one -- a later close would leak future information into a
   historical row.
3. The render path reads stored rows ONLY -- no yfinance, no T212 -- the
   same guarantee every other display surface in this project carries.
4. The ingestion-time identity screen skips non-stock tickers, so adding an
   index series as a reference ticker cannot quarantine it (the regression
   that motivated the scoping fix shipped alongside this feature).
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text as sa_text


def _seed_account_history(engine, rows):
    """rows: [(date_str, total_value_gbp), ...]"""
    with engine.begin() as conn:
        for d, v in rows:
            conn.execute(sa_text(
                "INSERT INTO account_value_history (date, total_value_gbp, cash_gbp, "
                "invested_gbp, captured_at) VALUES (:d, :v, 0, 0, :now)"
            ), {"d": d, "v": v, "now": datetime.utcnow()})


def _seed_benchmark_prices(engine, rows, ticker="^SP500TR"):
    """rows: [(date_str, close), ...]"""
    with engine.begin() as conn:
        for d, c in rows:
            conn.execute(sa_text(
                "INSERT INTO price_data (ticker, date, open, high, low, close, volume) "
                "VALUES (:t, :d, :c, :c, :c, :c, 0)"
            ), {"t": ticker, "d": d, "c": c})


def _benchmark_rows(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(sa_text(
            "SELECT date, account_value_gbp, benchmark_value_gbp, benchmark_close, "
            "anchor_date, anchor_balance_gbp FROM benchmark_comparison ORDER BY date ASC"
        )).mappings().all()]


# ---------------------------------------------------------------------------
# _refresh_benchmark_comparison() -- the EOD-side computation
# ---------------------------------------------------------------------------

def test_benchmark_value_is_anchor_balance_scaled_by_the_close_ratio(db_engine):
    """The whole comparison in one assertion: the same starting capital,
    bought and held from the account's own start date."""
    from data.scheduler import _refresh_benchmark_comparison

    _seed_account_history(db_engine, [("2026-08-03", 50_000.0), ("2026-08-04", 51_000.0)])
    _seed_benchmark_prices(db_engine, [("2026-08-03", 100.0), ("2026-08-04", 110.0)])

    written = _refresh_benchmark_comparison({})
    assert written == 2

    rows = _benchmark_rows(db_engine)
    # Anchor day: ratio is exactly 1, so benchmark == the real starting balance.
    assert rows[0]["benchmark_value_gbp"] == pytest.approx(50_000.0)
    # +10% on the index -> the same capital would be worth £55,000.
    assert rows[1]["benchmark_value_gbp"] == pytest.approx(55_000.0)
    assert rows[1]["account_value_gbp"] == pytest.approx(51_000.0)
    # Anchor is recorded on every row, not just inferable at read time.
    assert all(str(r["anchor_date"]).startswith("2026-08-03") for r in rows)
    assert all(r["anchor_balance_gbp"] == pytest.approx(50_000.0) for r in rows)


def test_no_synthetic_history_before_the_account_existed(db_engine):
    """Benchmark prices reaching years further back must NOT produce rows
    before the account's own first day -- scope item 4: if the account's
    history is short, the comparison is short."""
    from data.scheduler import _refresh_benchmark_comparison

    _seed_account_history(db_engine, [("2026-08-03", 50_000.0)])
    _seed_benchmark_prices(db_engine, [
        ("2020-01-02", 50.0), ("2024-06-03", 80.0), ("2026-08-03", 100.0),
    ])

    _refresh_benchmark_comparison({})

    rows = _benchmark_rows(db_engine)
    assert len(rows) == 1
    assert str(rows[0]["date"]).startswith("2026-08-03")


def test_rerun_upserts_rather_than_duplicating(db_engine):
    """Same-day re-run refreshes the row, matching AccountValueHistory's
    own convention -- the EOD job can legitimately run twice."""
    from data.scheduler import _refresh_benchmark_comparison

    _seed_account_history(db_engine, [("2026-08-03", 50_000.0)])
    _seed_benchmark_prices(db_engine, [("2026-08-03", 100.0)])

    _refresh_benchmark_comparison({})
    _refresh_benchmark_comparison({})
    _refresh_benchmark_comparison({})

    assert len(_benchmark_rows(db_engine)) == 1


def test_rerun_picks_up_a_revised_account_value(db_engine):
    """Recomputing the whole series each run is what makes it self-healing;
    a corrected account value must propagate, not be frozen at first write."""
    from data.scheduler import _refresh_benchmark_comparison

    _seed_account_history(db_engine, [("2026-08-03", 50_000.0)])
    _seed_benchmark_prices(db_engine, [("2026-08-03", 100.0)])
    _refresh_benchmark_comparison({})

    with db_engine.begin() as conn:
        conn.execute(sa_text(
            "UPDATE account_value_history SET total_value_gbp = 52000 WHERE date = '2026-08-03'"
        ))
    _refresh_benchmark_comparison({})

    rows = _benchmark_rows(db_engine)
    assert len(rows) == 1
    assert rows[0]["account_value_gbp"] == pytest.approx(52_000.0)


def test_missing_close_falls_back_to_the_most_recent_earlier_close(db_engine):
    """An account row on a date the index has no close for (a holiday, a
    late ingestion) uses the previous close -- carried forward, which is
    what a real buy-and-hold position would show."""
    from data.scheduler import _refresh_benchmark_comparison

    _seed_account_history(db_engine, [
        ("2026-08-03", 50_000.0), ("2026-08-04", 51_000.0), ("2026-08-05", 52_000.0),
    ])
    # No close for 08-04.
    _seed_benchmark_prices(db_engine, [("2026-08-03", 100.0), ("2026-08-05", 120.0)])

    _refresh_benchmark_comparison({})

    rows = _benchmark_rows(db_engine)
    assert len(rows) == 3
    # 08-04 carries 08-03's close forward -> unchanged from the anchor value.
    assert rows[1]["benchmark_close"] == pytest.approx(100.0)
    assert rows[1]["benchmark_value_gbp"] == pytest.approx(50_000.0)
    # 08-05 uses its own real close.
    assert rows[2]["benchmark_value_gbp"] == pytest.approx(60_000.0)


def test_never_uses_a_later_close_for_an_earlier_date(db_engine):
    """Look-ahead guard, asserted directly rather than assumed from the
    fallback test above: an account row PREDATING every benchmark close is
    skipped entirely, never filled from the first available later price."""
    from data.scheduler import _refresh_benchmark_comparison

    _seed_account_history(db_engine, [("2026-08-03", 50_000.0), ("2026-08-10", 51_000.0)])
    # Benchmark data starts AFTER the account's own first day.
    _seed_benchmark_prices(db_engine, [("2026-08-10", 100.0)])

    written = _refresh_benchmark_comparison({})

    # The anchor itself has no usable close, so nothing is computed at all
    # rather than silently re-anchoring on a later date.
    assert written == 0
    assert _benchmark_rows(db_engine) == []


def test_returns_zero_and_writes_nothing_without_inputs(db_engine):
    """Both empty cases must be quiet no-ops, not exceptions -- this runs
    inside the EOD job."""
    from data.scheduler import _refresh_benchmark_comparison

    assert _refresh_benchmark_comparison({}) == 0

    _seed_account_history(db_engine, [("2026-08-03", 50_000.0)])
    # Account history but no benchmark prices yet (the real state on the
    # first run after adding the ticker, before the next price pull).
    assert _refresh_benchmark_comparison({}) == 0
    assert _benchmark_rows(db_engine) == []


def test_never_raises_on_a_broken_database(db_engine, monkeypatch):
    """Returns 0 rather than propagating -- the EOD job's real work must
    not be affected by a reporting-only step."""
    from data import scheduler

    def _boom():
        raise RuntimeError("db exploded")

    monkeypatch.setattr("data.database.get_engine", _boom)
    assert scheduler._refresh_benchmark_comparison({}) == 0


# ---------------------------------------------------------------------------
# compute_benchmark_spread() / benchmark_spread_caveat() -- pure functions
# ---------------------------------------------------------------------------

def _spread_row(d, account, benchmark, anchor="2026-08-03", anchor_balance=50_000.0):
    return {
        "date": d, "account_value_gbp": account, "benchmark_value_gbp": benchmark,
        "anchor_date": anchor, "anchor_balance_gbp": anchor_balance,
    }


def test_spread_is_measured_against_the_benchmark_not_the_starting_balance():
    from output.performance import compute_benchmark_spread

    spread = compute_benchmark_spread([_spread_row("2026-08-10", 55_000.0, 50_000.0)])
    assert spread["available"] is True
    assert spread["spread_gbp"] == pytest.approx(5_000.0)
    # +10% relative to the benchmark's 50,000 -- NOT relative to the anchor.
    assert spread["spread_pct"] == pytest.approx(0.10)


def test_spread_uses_the_latest_row_and_reports_elapsed_days():
    from output.performance import compute_benchmark_spread

    spread = compute_benchmark_spread([
        _spread_row("2026-08-03", 50_000.0, 50_000.0),
        _spread_row("2026-09-02", 52_000.0, 51_000.0),
    ])
    assert spread["account_value_gbp"] == pytest.approx(52_000.0)
    assert spread["elapsed_days"] == 30
    assert spread["as_of"] == date(2026, 9, 2)


def test_spread_unavailable_rather_than_zero_when_nothing_is_stored():
    """A £0.00 spread would read as the account exactly matching the index;
    "not computed yet" is a different statement and must stay distinct."""
    from output.performance import compute_benchmark_spread

    for rows in ([], [_spread_row("2026-08-10", 55_000.0, None)],
                 [_spread_row("2026-08-10", 55_000.0, 0.0)]):
        spread = compute_benchmark_spread(rows)
        assert spread["available"] is False
        assert spread["spread_gbp"] is None


def test_caveat_states_elapsed_period_and_is_symmetric_about_direction():
    """The caveat must hedge a favourable number exactly as firmly as an
    unfavourable one -- it warns against over-reading, not against bad
    news."""
    from output.performance import benchmark_spread_caveat, compute_benchmark_spread

    ahead = compute_benchmark_spread([_spread_row("2026-08-11", 60_000.0, 50_000.0)])
    behind = compute_benchmark_spread([_spread_row("2026-08-11", 40_000.0, 50_000.0)])

    ahead_text = benchmark_spread_caveat(ahead)
    behind_text = benchmark_spread_caveat(behind)
    assert ahead_text == behind_text, "the caveat must not vary with the sign of the spread"
    assert "8 days" in ahead_text
    assert "63%" in ahead_text


def test_caveat_softens_only_once_the_period_is_long_enough():
    from output.performance import (
        BENCHMARK_MEANINGFUL_MIN_DAYS, benchmark_spread_caveat, compute_benchmark_spread,
    )

    short_end = (date(2026, 8, 3) + timedelta(days=BENCHMARK_MEANINGFUL_MIN_DAYS - 1)).isoformat()
    long_end = (date(2026, 8, 3) + timedelta(days=BENCHMARK_MEANINGFUL_MIN_DAYS)).isoformat()

    short = compute_benchmark_spread([_spread_row(short_end, 52_000.0, 50_000.0)])
    long = compute_benchmark_spread([_spread_row(long_end, 52_000.0, 50_000.0)])

    assert short["is_meaningful_period"] is False
    assert long["is_meaningful_period"] is True
    assert "too short to mean anything" in benchmark_spread_caveat(short)
    assert "too short to mean anything" not in benchmark_spread_caveat(long)
    # Even past the floor the structural caveat stays -- it never becomes a
    # bare number with no context.
    assert "63%" in benchmark_spread_caveat(long)


def test_caveat_handles_the_not_yet_computed_case():
    from output.performance import benchmark_spread_caveat, compute_benchmark_spread

    assert benchmark_spread_caveat(compute_benchmark_spread([])) == "No benchmark comparison computed yet."


# ---------------------------------------------------------------------------
# Render path reads stored state only
# ---------------------------------------------------------------------------

def test_render_path_never_calls_yfinance_or_t212(db_engine, monkeypatch):
    """The house guarantee for every display surface -- patches both to
    raise if touched at all, then exercises the real read path."""
    import yfinance

    def _explode(*a, **k):
        raise AssertionError("render path made a network call")

    monkeypatch.setattr(yfinance, "Ticker", _explode)
    monkeypatch.setattr("brokers.trading212.Trading212Client", _explode)

    from output.performance import (
        compute_benchmark_spread, get_benchmark_comparison_history,
    )
    from data.scheduler import _refresh_benchmark_comparison

    _seed_account_history(db_engine, [("2026-08-03", 50_000.0), ("2026-08-04", 51_000.0)])
    _seed_benchmark_prices(db_engine, [("2026-08-03", 100.0), ("2026-08-04", 110.0)])
    _refresh_benchmark_comparison({})

    rows = get_benchmark_comparison_history(days=3650)
    spread = compute_benchmark_spread(rows)
    assert spread["available"] is True
    assert spread["benchmark_value_gbp"] == pytest.approx(55_000.0)


def test_history_read_returns_empty_rather_than_raising_before_first_run(db_engine):
    from output.performance import get_benchmark_comparison_history

    assert get_benchmark_comparison_history(days=30) == []


# ---------------------------------------------------------------------------
# The scoping fix: an index series must never be identity-screened
# ---------------------------------------------------------------------------

def test_ingestion_screen_skips_non_stock_tickers(db_engine, monkeypatch):
    """The regression that motivated the fix: verify_ticker_identity() can
    only ever affirm quote_type == "EQUITY", so screening ^SP500TR would
    quarantine the benchmark series by construction. Confirmed against the
    real function on production before the fix: status QUARANTINED,
    quote_type 'INDEX'."""
    from data import ingestion

    called = []
    monkeypatch.setattr(ingestion, "verify_ticker_identity",
                        lambda t, c=None: called.append(t) or {
                            "status": "QUARANTINED", "reason": "x", "evidence": {}})
    monkeypatch.setattr("analysis.universe.get_all_known_stock_tickers", lambda: {"AAPL"})

    ingestion._screen_newly_backfilled_ticker("^SP500TR", {})
    assert called == [], "an index series must not be identity-screened"

    ingestion._screen_newly_backfilled_ticker("AAPL", {})
    assert called == ["AAPL"], "a genuine stock must still be screened"


def test_ingestion_screen_skips_rather_than_screens_when_the_stock_list_is_unavailable(
    db_engine, monkeypatch,
):
    """Fail-safe direction, asserted explicitly: leaving a real stock
    unscreened merely defers it to the next backstop scan, whereas
    screening a non-stock writes a wrong, persistent QUARANTINED row no
    later pass would revisit."""
    from data import ingestion

    called = []
    monkeypatch.setattr(ingestion, "verify_ticker_identity",
                        lambda t, c=None: called.append(t) or {
                            "status": "CLEAN", "reason": "x", "evidence": {}})

    def _boom():
        raise RuntimeError("universe unavailable")

    monkeypatch.setattr("analysis.universe.get_all_known_stock_tickers", _boom)

    ingestion._screen_newly_backfilled_ticker("AAPL", {})
    assert called == []


def test_benchmark_ticker_is_total_return_not_price_only():
    """^GSPC is price-only and excludes dividends, flattering this system
    by roughly 1.5-2%/yr; the research runs all benchmark against the
    total-return series, and the dashboard must match them."""
    from data.scheduler import BENCHMARK_TICKER

    assert BENCHMARK_TICKER == "^SP500TR"


def test_benchmark_ticker_is_configured_as_a_reference_ticker():
    """It has to be in reference_tickers or the existing EOD ingestion
    never pulls it -- the comparison would silently never populate."""
    import yaml
    from pathlib import Path

    config = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text())
    tickers = {e["ticker"] for e in config["reference_tickers"]}
    assert "^SP500TR" in tickers
    # ^GSPC stays: it is the crisis-trigger series, not interchangeable.
    assert "^GSPC" in tickers


# ---------------------------------------------------------------------------
# Weekly email + Dashboard rendering
# ---------------------------------------------------------------------------

def _weekly_data_with_spread(account, benchmark, as_of="2026-08-11", anchor="2026-08-03"):
    from output.performance import compute_benchmark_spread
    return {"benchmark_spread": compute_benchmark_spread([
        _spread_row(as_of, account, benchmark, anchor=anchor),
    ])}


def test_weekly_email_line_states_both_values_the_spread_and_the_caveat():
    from output.briefing import _benchmark_spread_line

    line = _benchmark_spread_line(_weekly_data_with_spread(51_362.06, 51_794.74))
    assert "51,362.06" in line and "51,794.74" in line
    assert "-432.68" in line
    assert "-0.84%" in line
    assert "2026-08-11" in line          # as-of stamp
    assert "8 days elapsed" in line      # mandatory elapsed period
    assert "63%" in line                 # mandatory structural caveat


def test_weekly_email_line_respects_the_established_forbidden_word_tone_standard():
    """This email has a tested calm/no-call-to-action standard; a new line
    must not smuggle in language the rest of it already bans -- checked for
    a favourable spread, an unfavourable one, and the not-yet-computed
    case, since the wording branches."""
    from output.briefing import _benchmark_spread_line
    from tests.test_briefing import _FORBIDDEN_WORDS

    for data in (
        _weekly_data_with_spread(60_000.0, 50_000.0),
        _weekly_data_with_spread(40_000.0, 50_000.0),
        _weekly_data_with_spread(52_000.0, 50_000.0, as_of="2027-08-11"),
        {"benchmark_spread": None},
    ):
        line = _benchmark_spread_line(data).lower()
        for word in _FORBIDDEN_WORDS:
            assert word not in line, f"forbidden word '{word}' in benchmark line: {line}"


def test_weekly_email_line_handles_missing_spread_without_raising():
    from output.briefing import _benchmark_spread_line

    assert "not computed yet" in _benchmark_spread_line({})
    assert "not computed yet" in _benchmark_spread_line({"benchmark_spread": None})


def test_value_history_chart_overlays_the_benchmark_without_a_second_fill():
    """Two filled areas on one axis read as stacked rather than compared;
    the fill belongs to the account series whose floor it describes."""
    from main import _value_history_chart

    account = [{"date": "2026-08-03", "total_value_gbp": 50_000.0},
               {"date": "2026-08-04", "total_value_gbp": 51_000.0}]
    bench = [{"date": "2026-08-03", "benchmark_value_gbp": 50_000.0},
             {"date": "2026-08-04", "benchmark_value_gbp": 55_000.0}]

    fig = _value_history_chart(account, bench)
    assert len(fig.data) == 2
    assert fig.data[0].fill == "tozeroy"
    assert fig.data[1].fill is None
    assert fig.layout.showlegend is True


def test_value_history_chart_unchanged_when_no_benchmark_data_yet():
    """Before the first EOD run the chart must render exactly as it did
    before this feature -- one trace, no legend, no empty second series."""
    from main import _value_history_chart

    account = [{"date": "2026-08-03", "total_value_gbp": 50_000.0}]
    for bench in (None, [], [{"date": "2026-08-03", "benchmark_value_gbp": None}]):
        fig = _value_history_chart(account, bench)
        assert len(fig.data) == 1
        assert fig.layout.showlegend is False
