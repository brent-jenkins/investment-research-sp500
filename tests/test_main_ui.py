"""UI-entry-point tests for main.py using Streamlit's AppTest harness --
navigates the app the way a real user would (page selection, button
clicks), rather than calling underlying functions directly.

Covers the Backtest page's due-diligence report download flow, which
previously existed only at the very bottom of the page, after a full
1-2 minute battery run and six other report sections -- reachable in
principle, but not discoverable in practice (see CLAUDE.md's "Due
diligence report UI discoverability fix" section). The button was moved
to render immediately once a battery result exists, right after the
"Last run" caption, matching V1's Robustness-page precedent of the
download button sitting above the detail panels rather than below them.

main.py runs real module-level setup on every AppTest.run() (init_db(),
_start_scheduler_at_startup()) -- these are patched to use the in-memory
db_engine fixture's engine and to never start a real background
scheduler, so this test never touches the real project database, calls
yfinance, or starts a real APScheduler instance.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

# Every AppTest.from_file("main.py") construction below must set an
# authenticated session -- main.py now gates all content behind a login
# form (see auth.py) -- before at.run(), the same way it already sets
# st.session_state["page"] to skip straight to the page under test.
_ADMIN_SESSION = {"id": 1, "username": "test_admin", "role": "admin"}

# Absolute path, not the bare string "main.py" -- AppTest.from_file()
# resolves a relative path by walking the call stack to find its caller's
# file (per its own docstring), which is fragile to anything that shifts
# stack depth between this file and the from_file() call (pytest plugin
# versions, wrapping, etc.). Confirmed as a REAL failure, not theoretical:
# this broke in GitHub Actions CI (a different anyio version than the one
# locally installed, both satisfying requirements.txt's unpinned `>=`
# constraint) while passing locally every time -- an absolute path sidesteps
# the whole stack-walking mechanism and cannot be affected by this class of
# environment drift.
_MAIN_PY = str(Path(__file__).resolve().parent.parent / "main.py")


def _fast_validation_result():
    """A small, fast, deterministic run_full_validation() result -- same
    shape tests/test_dd_report.py's _sample_result() uses, so the real
    (unmocked) generate_dd_report_pdf() can run against it quickly."""
    blocks = []
    for i in range(16):
        blocks.append({
            "block": i, "start": f"20{7 + i:02d}-01-01", "end": f"20{8 + i:02d}-01-01",
            "n_trades": 10, "strat_cagr": 10.0 + i, "strat_dd": -20.0,
            "sp_cagr": 8.0, "sp_dd": -18.0, "passed": i != 1,
        })
    metrics_shape = {
        "total_trades": 3, "win_rate": 0.5, "cagr_pct": 12.3,
        "max_drawdown_pct": -25.0, "profit_factor": 1.8,
    }
    trades = [
        {"ticker": "VOO", "entry_date": "2019-01-01", "exit_date": "2019-06-01",
         "pnl_pct": 0.05, "position_gbp": 1000.0, "pnl_gbp": 50.0},
        {"ticker": "VOO", "entry_date": "2021-01-01", "exit_date": "2021-06-01",
         "pnl_pct": -0.02, "position_gbp": 1000.0, "pnl_gbp": -20.0},
    ]
    return {
        "start": "2007-01-01", "end": "2026-07-30", "starting_capital_gbp": 50000.0,
        # The live-universe (2026-08-02) shape -- what run_full_validation
        # actually returns now that the Backtest page always calls it with
        # live_universe=True, mark_to_market=True. See tests/test_dd_report.py's
        # own matching _sample_result(live_universe=True) shape.
        "universe": {
            "as_of": "2026-07-30", "candidate_universe_count": 1204,
            "live_eligible_count": 503, "excluded_count": 701,
            "excluded_sample": ["ENRON", "WCOM", "LEH"],
        },
        "live_universe": True,
        "backtest_convention": "continuous_walk",
        "engine_used": "compounding_default",
        "deployment_basis": "mark_to_market",
        "n_tickers_with_data": 73, "n_tickers_total": 73,
        "total_trades": len(trades),
        "train": dict(metrics_shape), "test": dict(metrics_shape),
        "train_sp": {"cagr_pct": 7.5, "max_drawdown_pct": -55.0},
        "test_sp": {"cagr_pct": 15.0, "max_drawdown_pct": -33.9},
        "full_sp": {"cagr_pct": 9.5, "max_drawdown_pct": -55.0},
        "full_raw": dict(metrics_shape), "full_ex_best": dict(metrics_shape),
        "blocks": blocks, "n_blocks_pass": 15, "n_blocks_total": 16,
        "walk_forward": {"n_total": 223, "n_positive": 183, "success_rate_pct": 82.1},
        "cost_sensitivity": {0: dict(metrics_shape), 10: dict(metrics_shape), 25: dict(metrics_shape)},
        # Deliberately distinctive numbers, shared verbatim with
        # tests/test_dd_report.py's own sample result -- see that file's
        # matching comment. If the Backtest page ever started recomputing
        # this instead of reading it from `result`, these specific figures
        # would stop showing up and this file's own test would catch it.
        "gbp_growth": {
            "starting_capital_gbp": 50000.0,
            "train": {"strategy_ending_gbp": 71234.56, "sp500_ending_gbp": 65432.10},
            "test": {"strategy_ending_gbp": 88765.43, "sp500_ending_gbp": 54321.98},
        },
        # Deliberately distinctive, materially milder than max_drawdown_pct
        # above (-25.0%) -- proves the page shows the genuine peak-relative
        # figure from `result`, not a recomputation, and that it's visibly
        # different from the fixed-denominator figure.
        "peak_relative_drawdown": {"train": -9.87, "test": -11.23, "full": -12.34},
        "trades": trades,
    }


@pytest.fixture
def app(db_engine, monkeypatch, mocker):
    """A ready-to-run AppTest instance pointed at the Backtest page, with
    every DB/scheduler side effect neutralised.

    Ordering matters here and is easy to get wrong: main.py has never been
    importable in this test suite because it runs _start_scheduler_at_startup()
    (an @st.cache_resource call) at module level, which starts a real
    APScheduler background thread -- including live T212 API calls -- as a
    side effect of merely importing the module (see output/performance.py's
    own module docstring). Patching "data.scheduler.start_scheduler" is what
    actually neutralises this, and it MUST happen before anything triggers
    main.py's own `from data.scheduler import start_scheduler` import
    statement -- patching "main.start_scheduler" directly is not equivalent
    and does not help if main.py has already been imported (its own name
    binding is a frozen snapshot taken at import time). Patching
    "data.scheduler.start_scheduler" only requires importing data.scheduler
    itself, which has no dangerous module-level side effects, so it's safe
    to do before main.py exists in sys.modules at all -- unlike touching
    anything under "main." directly, which would trigger that first import
    immediately, mock or no mock yet in place for the thing it's about to
    bind.
    """
    mocker.patch("data.scheduler.start_scheduler")
    mocker.patch(
        "analysis.validation.run_full_validation",
        return_value=_fast_validation_result(),
    )
    # This line is main.py's first import in the whole test session -- both
    # patches above are already active by this point.
    monkeypatch.setattr("main.get_engine", lambda: db_engine)

    at = AppTest.from_file(_MAIN_PY, default_timeout=30)
    at.session_state["auth_user"] = _ADMIN_SESSION
    at.session_state["page"] = "backtest"
    return at


def test_backtest_page_reachable_via_sidebar_navigation(app):
    """Sanity check the Backtest page itself renders via the normal
    session-state page-selection path (the same one the sidebar radio
    writes to), before testing the report flow on top of it."""
    at = app.run()
    assert not at.exception
    assert any("Backtest" in m.value for m in at.markdown)


def test_dd_report_button_appears_immediately_after_battery_run(app):
    """The 'Download Due Diligence Report' section must render as soon as
    a battery result exists -- right after the run, not buried below the
    other report sections."""
    at = app.run()
    at.button(key="run_full_validation").click().run()

    assert not at.exception
    assert any("Download Due Diligence Report" in m.value for m in at.markdown)
    assert at.button(key="gen_dd_report")


def test_dd_report_downloads_a_real_pdf_via_the_ui_path(app):
    """End-to-end: navigate to Backtest, run the battery, click Generate,
    confirm a real PDF comes out through the same st.session_state path
    the download_button reads from, and that the download button itself
    renders with the expected label -- not just that the underlying
    generate_dd_report_pdf() function works in isolation (already covered
    by tests/test_dd_report.py)."""
    at = app.run()
    at.button(key="run_full_validation").click().run()
    at.button(key="gen_dd_report").click().run()

    assert not at.exception
    pdf_bytes = at.session_state["_dd_report_pdf"]
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000

    downloads = at.get("download_button")
    assert len(downloads) == 1
    assert "Download Due Diligence Report" in downloads[0].label


def test_gbp_growth_shown_on_backtest_page_from_result_not_recomputed(app):
    """The £50k V2-vs-S&P comparison on the Backtest page must come
    straight from result['gbp_growth'] -- proven by the same deliberately
    distinctive numbers used in tests/test_dd_report.py's sample result
    (see that file's matching comment). If the Backtest page ever started
    recomputing this instead of reading it from `result`, these specific
    figures would stop appearing here."""
    at = app.run()
    at.button(key="run_full_validation").click().run()

    assert not at.exception
    metric_values = [m.value for m in at.metric]
    assert any("71,235" in v for v in metric_values)  # train strategy ending
    assert any("65,432" in v for v in metric_values)  # train S&P ending
    assert any("88,765" in v for v in metric_values)  # test strategy ending
    assert any("54,322" in v for v in metric_values)  # test S&P ending


def test_backtest_page_calls_run_full_validation_with_live_universe_and_mtm(db_engine, monkeypatch, mocker):
    """The 'Run Full Validated Battery' button must call run_full_validation()
    with live_universe=True and mark_to_market=True explicitly -- not the
    old bare/default call -- proving the page genuinely runs the current,
    real live engine (2026-08-02) rather than the frozen 73-ticker
    cost-basis configuration it used before. See CLAUDE.md's "rebuild the
    backtest/DD-report page to genuinely run against the current, real
    live engine" section."""
    mocker.patch("data.scheduler.start_scheduler")
    mock_run = mocker.patch(
        "analysis.validation.run_full_validation",
        return_value=_fast_validation_result(),
    )
    monkeypatch.setattr("main.get_engine", lambda: db_engine)

    at = AppTest.from_file(_MAIN_PY, default_timeout=30)
    at.session_state["auth_user"] = _ADMIN_SESSION
    at.session_state["page"] = "backtest"
    at = at.run()
    at.button(key="run_full_validation").click().run()

    assert not at.exception
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs.get("live_universe") is True
    assert kwargs.get("mark_to_market") is True


def test_backtest_page_states_no_etfs_and_continuous_walk(app):
    """The page's own top-of-page captions must explicitly state both the
    no-ETFs fact and the continuous-walk convention -- per the standing
    requirement that this is always labelled, never left implicit (see
    CLAUDE.md's "Two backtest conventions" section)."""
    at = app.run()

    all_text = " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)
    assert "no ETFs" in all_text.lower() or "no etfs" in all_text.lower()
    assert "continuous walk" in all_text.lower()


def test_backtest_page_universe_boxes_show_membership_not_tradeability(app):
    """The three universe metric boxes must reflect genuine S&P 500 index
    membership (candidate_universe_count/live_eligible_count/excluded_count)
    -- never a T212-tradeability concept, which no longer exists as a
    static, gate-able list at all. The page's caption is allowed to name
    "T212" explicitly to state the distinction (the task's own framing
    does this too) -- what must never appear is a metric DELTA describing
    tradeability/mapping status instead of membership."""
    at = app.run()

    metrics = {m.label: m for m in at.metric}
    assert "Candidate Universe" in metrics
    assert "Live-Eligible Today" in metrics
    assert "Excluded Today" in metrics
    for label in ("Candidate Universe", "Live-Eligible Today", "Excluded Today"):
        delta_text = (metrics[label].delta or "").lower()
        assert "tradeable" not in delta_text
        assert "t212" not in delta_text
        assert "mapping" not in delta_text


def test_backtest_page_shows_both_drawdown_conventions_distinctly(app):
    """The page must show both the fixed-denominator max_drawdown_pct AND
    the genuine peak-relative figure from result['peak_relative_drawdown'],
    read directly (not recomputed), and they must read as visibly
    different numbers -- per this project's own standing "state the
    convention explicitly" discipline, since the two can diverge widely."""
    at = app.run()
    at.button(key="run_full_validation").click().run()

    assert not at.exception
    metric_values = [m.value for m in at.metric]
    assert any("-9.87" in v for v in metric_values)   # train peak-relative
    assert any("-11.23" in v for v in metric_values)  # test peak-relative
    all_text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "peak-relative" in all_text.lower()


# ---------------------------------------------------------------------------
# Instruments page (2026-08-02, replaces Charts) -- a searchable list of
# every tracked instrument, sourced from the same dynamic live candidate
# pool everything else now reads from (get_all_watchlist_entries()).
# ---------------------------------------------------------------------------

_SAMPLE_WATCHLIST_ENTRIES = [
    {"ticker": "^GSPC", "name": "S&P 500", "exchange": "NYSE"},
    {"ticker": "AAPL", "name": "AAPL", "exchange": "NYSE"},
    {"ticker": "MSFT", "name": "MSFT", "exchange": "NYSE"},
]


@pytest.fixture
def instruments_app(db_engine, monkeypatch, mocker):
    """Same import-ordering discipline as the `app` fixture above -- see
    its own docstring for why every monkeypatch/mocker.patch call must
    happen before AppTest.from_file("main.py") is constructed, not after.

    Patches data.ingestion.get_all_watchlist_entries (the SOURCE module),
    not main.get_all_watchlist_entries -- AppTest re-execs main.py's own
    top-level `from data.ingestion import ... get_all_watchlist_entries
    ...` import statement on every .run(), which would silently re-bind a
    main-level patch back to the real function. Patching the upstream
    source survives that re-exec, the same reason main.get_engine's own
    patch above works via data.database.get_engine already being patched
    by the db_engine fixture itself.
    """
    mocker.patch("data.scheduler.start_scheduler")
    monkeypatch.setattr("main.get_engine", lambda: db_engine)
    monkeypatch.setattr("data.ingestion.get_all_watchlist_entries", lambda config: list(_SAMPLE_WATCHLIST_ENTRIES))

    at = AppTest.from_file(_MAIN_PY, default_timeout=30)
    at.session_state["auth_user"] = _ADMIN_SESSION
    at.session_state["page"] = "instruments"
    return at


def test_instruments_page_reachable_via_sidebar_navigation(instruments_app):
    at = instruments_app.run()
    assert not at.exception
    assert any("Instruments" in m.value for m in at.markdown)


def test_instruments_page_shows_every_tracked_ticker_from_the_live_pool(instruments_app):
    """Sourced from get_all_watchlist_entries() -- the same dynamic pool
    (reference_tickers + get_live_stock_candidates()) everything else now
    reads from, not a static list."""
    at = instruments_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert set(df["Ticker"]) == {"^GSPC", "AAPL", "MSFT"}
    assert "finance.yahoo.com/quote/AAPL" in df.loc[df["Ticker"] == "AAPL", "Yahoo Finance"].iloc[0]
    # Reference tickers with special characters (^, =) are URL-encoded in
    # the link, but shown raw in the Ticker column.
    assert "%5EGSPC" in df.loc[df["Ticker"] == "^GSPC", "Yahoo Finance"].iloc[0]


def test_instruments_page_search_filters_by_ticker_or_name(instruments_app):
    at = instruments_app.run()
    at.text_input[0].set_value("MSFT").run()

    assert not at.exception
    df = at.dataframe[0].value
    assert set(df["Ticker"]) == {"MSFT"}


def test_instruments_page_search_is_case_insensitive_and_matches_name(instruments_app):
    at = instruments_app.run()
    at.text_input[0].set_value("s&p 500").run()

    assert not at.exception
    df = at.dataframe[0].value
    assert set(df["Ticker"]) == {"^GSPC"}


def test_sidebar_nav_has_instruments_not_charts(instruments_app):
    at = instruments_app.run()
    nav_options = at.radio[0].options
    assert "Instruments" in nav_options
    assert "Charts" not in nav_options


def _seed_instrument_resolution(db_engine, *, ticker, status, t212_instrument_id=None,
                                 currency=None, note=None):
    from sqlalchemy import text as sa_text
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO instrument_resolutions
                (ticker, t212_instrument_id, currency, status, note)
            VALUES (:ticker, :t212_instrument_id, :currency, :status, :note)
        """), {
            "ticker": ticker, "t212_instrument_id": t212_instrument_id,
            "currency": currency, "status": status, "note": note,
        })


def test_instruments_page_shows_resolution_status_alongside_each_ticker(db_engine, instruments_app):
    """Resolution status moved here 2026-08-03 from a disconnected
    "Instrument Mapping" expander on the Status page (see CLAUDE.md's
    Status-page-restructuring section) -- it belongs alongside each
    ticker's other details on this already-per-instrument page, joined by
    ticker, not a second separate table. A ticker with no resolution row
    (e.g. ^GSPC, a reference ticker never evaluated as a trading
    candidate) shows "—", not a fabricated status."""
    _seed_instrument_resolution(db_engine, ticker="AAPL", status="VERIFIED",
                                 t212_instrument_id="AAPL_US_EQ", currency="USD")
    _seed_instrument_resolution(db_engine, ticker="MSFT", status="FAILED",
                                 note="no confident match found")

    at = instruments_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert "Resolution Status" in df.columns
    assert df.loc[df["Ticker"] == "AAPL", "Resolution Status"].iloc[0] == "VERIFIED"
    assert df.loc[df["Ticker"] == "AAPL", "T212 Instrument"].iloc[0] == "AAPL_US_EQ"
    assert df.loc[df["Ticker"] == "MSFT", "Resolution Status"].iloc[0] == "FAILED"
    assert df.loc[df["Ticker"] == "MSFT", "Note"].iloc[0] == "no confident match found"
    # ^GSPC has no resolution row at all -- shown as "—", not fabricated.
    assert df.loc[df["Ticker"] == "^GSPC", "Resolution Status"].iloc[0] == "—"

    all_text = " ".join(c.value for c in at.caption)
    assert "1 verified" in all_text
    assert "1 failed" in all_text


def test_old_charts_page_functions_are_gone_not_dormant():
    """The old Charts page (grouped landing cards + per-ticker candlestick
    detail view) must be removed entirely, not left as unreachable dead
    code -- confirmed directly against the real module, not inferred from
    the nav list alone."""
    import main as main_module
    assert not hasattr(main_module, "_render_charts_landing")
    assert not hasattr(main_module, "_candlestick_chart")
    assert not hasattr(main_module, "_volume_bar_chart")
    assert not hasattr(main_module, "_GROUP_LABELS")
    assert hasattr(main_module, "_render_instruments_page")


# ---------------------------------------------------------------------------
# Dashboard page's EOD status badge (2026-08-02) -- the direct "did EOD
# actually run" signal, placed at the very top of the default landing page
# so it's genuinely checkable first thing each morning.
# ---------------------------------------------------------------------------

@pytest.fixture
def dashboard_app(db_engine, monkeypatch, mocker):
    """Renders against the REAL config.yaml (trading212_invest.enabled:
    true in this repo) rather than trying to patch main._load_config --
    that function is DEFINED locally in main.py (not imported), so it
    would be silently re-defined back to the original on every AppTest
    rerun, the same re-exec hazard documented on the instruments_app
    fixture above. The badge renders unconditionally before any
    trading212_invest.enabled check either way, and every downstream
    Dashboard section's own output.performance function is already
    "never raises, empty-safe against a blank DB" by this codebase's own
    standing convention, so no further mocking is needed for the page to
    render cleanly against the fresh, empty db_engine fixture.

    Clears st.cache_data before every test (2026-08-02) -- @st.cache_data
    is a genuine process-wide cache that outlives any single AppTest
    instance, and _get_projected_admission_cached()'s leading-underscore
    `_config` parameter is deliberately excluded from Streamlit's cache
    key (the standard convention for an unhashed argument) -- meaning
    every call across the whole pytest session would otherwise share ONE
    cache entry, so whichever test happened to run first would silently
    poison every test after it with its own mocked projection. Confirmed
    this was a real, not hypothetical, risk: the fix was written after a
    test relying on a fresh [] projection got the previous test's
    stale SNDK/WBD result instead.
    """
    import streamlit as st
    st.cache_data.clear()
    mocker.patch("data.scheduler.start_scheduler")
    monkeypatch.setattr("main.get_engine", lambda: db_engine)
    monkeypatch.setattr("data.ingestion.get_all_watchlist_entries", lambda config: [])

    at = AppTest.from_file(_MAIN_PY, default_timeout=30)
    at.session_state["auth_user"] = _ADMIN_SESSION
    at.session_state["page"] = "dashboard"
    return at


def test_dashboard_shows_no_run_recorded_when_eod_has_never_run(dashboard_app):
    at = dashboard_app.run()
    assert not at.exception
    assert any("no run recorded yet" in w.value.lower() for w in at.warning)


def test_dashboard_shows_completed_eod_status(db_engine, dashboard_app):
    from data.scheduler import _record_eod_completion
    from datetime import date as _date
    _record_eod_completion("COMPLETED", _date(2026, 8, 4), price_pull_ran=True, order_pipeline_ran=True)

    at = dashboard_app.run()
    assert not at.exception
    assert any("completed" in s.value.lower() and "2026-08-04" in s.value for s in at.success)


def test_dashboard_shows_failed_eod_status(db_engine, dashboard_app):
    from data.scheduler import _record_eod_completion
    from datetime import date as _date
    _record_eod_completion("FAILED", _date(2026, 8, 4), error="yfinance is down")

    at = dashboard_app.run()
    assert not at.exception
    assert any("failed" in e.value.lower() and "yfinance is down" in e.value for e in at.error)


def test_dashboard_shows_noop_eod_status(db_engine, dashboard_app):
    from data.scheduler import _record_eod_completion
    from datetime import date as _date
    _record_eod_completion("NOOP_NOT_A_TRADING_WINDOW", _date(2026, 8, 1))

    at = dashboard_app.run()
    assert not at.exception
    assert any("skipped" in i.value.lower() for i in at.info)


def test_dashboard_shows_stale_warning_when_eod_record_is_old(db_engine, dashboard_app):
    """A COMPLETED verdict recorded days ago must NOT render as a fresh
    green checkmark -- that would be exactly the "plausible-looking but
    wrong" silent-failure mode this badge exists to avoid."""
    from datetime import datetime as _dt, timedelta as _td
    from sqlalchemy import text as sa_text
    import json as _json

    old_payload = {
        "status": "COMPLETED", "date": "2026-07-28",
        "recorded_at": (_dt.utcnow() - _td(hours=72)).isoformat(),
        "price_pull_ran": True, "order_pipeline_ran": True,
    }
    with db_engine.begin() as conn:
        conn.execute(sa_text(
            "CREATE TABLE IF NOT EXISTS system_metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
        ))
        conn.execute(sa_text(
            "INSERT OR REPLACE INTO system_metadata (key, value, updated_at) VALUES ('eod_completion', :v, :t)"
        ), {"v": _json.dumps(old_payload), "t": _dt.utcnow().isoformat()})

    at = dashboard_app.run()
    assert not at.exception
    assert any("stale" in w.value.lower() and "72h" in w.value for w in at.warning)
    # Must NOT also show the green "completed" success box for a stale record.
    assert not any("completed" in s.value.lower() for s in at.success)


# ---------------------------------------------------------------------------
# Dashboard crisis-mode warning banner (2026-08-03) -- sourced from
# output.performance.get_crisis_status()'s entry_blocked field, which
# itself defers to brokers.trading212.is_crisis_mode_active_now() -- the
# same authoritative check order submission re-confirms fresh, never a
# separate computation.
# ---------------------------------------------------------------------------

def test_dashboard_shows_crisis_banner_when_entry_blocked(db_engine, dashboard_app, mocker):
    mocker.patch("output.performance.get_crisis_status", return_value={
        "entry_blocked": True, "is_crisis_today": True, "today_return_pct": -9.2,
    })

    at = dashboard_app.run()

    assert not at.exception
    assert any("crisis override active" in e.value.lower() for e in at.error)


def test_dashboard_no_crisis_banner_under_normal_conditions(db_engine, dashboard_app, mocker):
    mocker.patch("output.performance.get_crisis_status", return_value={
        "entry_blocked": False, "is_crisis_today": False, "today_return_pct": 0.3,
    })

    at = dashboard_app.run()

    assert not at.exception
    assert not any("crisis override active" in e.value.lower() for e in at.error)


def test_dashboard_no_crisis_banner_when_status_is_empty(db_engine, dashboard_app, mocker):
    """No price data yet / an empty get_crisis_status() result must not
    be misread as "blocked" -- confirms the absent-key case is handled
    the same as an explicit False, not a KeyError or a false positive."""
    mocker.patch("output.performance.get_crisis_status", return_value={})

    at = dashboard_app.run()

    assert not at.exception
    assert not any("crisis override active" in e.value.lower() for e in at.error)


def test_dashboard_crisis_banner_text_describes_new_entries_only(db_engine, dashboard_app, mocker):
    """The banner must state plainly that ONLY new entries are blocked --
    existing positions continue to be monitored/exited normally -- not a
    vague "trading is blocked" that could be misread as open positions
    also being frozen."""
    mocker.patch("output.performance.get_crisis_status", return_value={"entry_blocked": True})

    at = dashboard_app.run()

    assert not at.exception
    banner_text = " ".join(e.value.lower() for e in at.error if "crisis override active" in e.value.lower())
    assert banner_text  # the banner was actually found
    assert "new entries" in banner_text
    assert "existing positions" in banner_text
    assert "monitored" in banner_text and ("exited" in banner_text or "normally" in banner_text)


# ---------------------------------------------------------------------------
# Dashboard exit-check positions-fetch failure banner (2026-08-03) --
# sourced from brokers.trading212.get_exit_check_fetch_status(), a
# distinct, always-current record separate from the generic invest_error
# key. Same "shown or not present at all, never a muted third state"
# convention as the crisis banner above -- see
# _render_exit_check_fetch_status_banner()'s own docstring and
# CLAUDE.md's "structural safeguards against future T212 API scheduling
# collisions" section, Part C.
# ---------------------------------------------------------------------------

def test_dashboard_shows_exit_check_fetch_failure_banner(db_engine, dashboard_app, mocker):
    mocker.patch("brokers.trading212.get_exit_check_fetch_status", return_value={
        "status": "FETCH_FAILED", "error": "T212 rate limited",
        "recorded_at": datetime.utcnow().isoformat(),
    })

    at = dashboard_app.run()

    assert not at.exception
    banner_text = " ".join(e.value.lower() for e in at.error)
    assert "could not fetch open positions" in banner_text
    assert "t212 rate limited" in banner_text


def test_dashboard_no_exit_check_banner_when_status_is_ok(db_engine, dashboard_app, mocker):
    mocker.patch("brokers.trading212.get_exit_check_fetch_status", return_value={
        "status": "OK", "recorded_at": datetime.utcnow().isoformat(),
    })

    at = dashboard_app.run()

    assert not at.exception
    assert not any("could not fetch open positions" in e.value.lower() for e in at.error)


def test_dashboard_no_exit_check_banner_when_nothing_ever_recorded(db_engine, dashboard_app, mocker):
    """A brand-new deployment with no exit check having run yet must not
    show a false alarm -- absence of a record is not evidence of a
    failure."""
    mocker.patch("brokers.trading212.get_exit_check_fetch_status", return_value=None)

    at = dashboard_app.run()

    assert not at.exception
    assert not any("could not fetch open positions" in e.value.lower() for e in at.error)


def test_dashboard_no_exit_check_banner_when_failure_record_is_stale(db_engine, dashboard_app, mocker):
    """A real failure recorded days ago (e.g. trading was disabled
    afterward, or a long weekend/holiday gap with no real exit check
    since) must not keep showing as a current alarm -- confirms the
    _EXIT_CHECK_FETCH_STALE_HOURS cutoff is actually applied, the same
    "don't show a possibly-false alarm" discipline the EOD status
    badge's own staleness check already established."""
    old_recorded_at = (datetime.utcnow() - timedelta(hours=100)).isoformat()
    mocker.patch("brokers.trading212.get_exit_check_fetch_status", return_value={
        "status": "FETCH_FAILED", "error": "T212 is down", "recorded_at": old_recorded_at,
    })

    at = dashboard_app.run()

    assert not at.exception
    assert not any("could not fetch open positions" in e.value.lower() for e in at.error)


def test_dashboard_exit_check_banner_states_which_exit_checks_were_skipped(db_engine, dashboard_app, mocker):
    """The banner must name what was actually skipped -- not a vague
    "something went wrong" -- so an operator immediately understands the
    real, concrete consequence (every exit-priority check for every
    open position, for that one cycle)."""
    mocker.patch("brokers.trading212.get_exit_check_fetch_status", return_value={
        "status": "FETCH_FAILED", "error": "boom", "recorded_at": datetime.utcnow().isoformat(),
    })

    at = dashboard_app.run()

    banner_text = " ".join(e.value.lower() for e in at.error)
    assert "stop-loss" in banner_text
    assert "signal-off" in banner_text
    assert "crisis-exit" in banner_text
    assert "max-holding" in banner_text


def _seed_invest_order(db_engine, *, ticker, direction="BUY", status="FILLED",
                        estimated_value_gbp=1000.0, is_etf=False, placed_at=None):
    from sqlalchemy import text as sa_text
    from datetime import datetime
    now = placed_at or datetime.utcnow().isoformat()
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, estimated_value_gbp,
                 var_pct, momentum_pct, status, is_etf, account_type, placed_at)
            VALUES (:ticker, :t212_ticker, :direction, 10.0, :estimated_value_gbp,
                    0.02, 0.15, :status, :is_etf, 'DEMO', :now)
        """), {
            "ticker": ticker, "t212_ticker": f"{ticker}_US_EQ", "direction": direction,
            "estimated_value_gbp": estimated_value_gbp, "status": status,
            "is_etf": is_etf, "now": now,
        })


# ---------------------------------------------------------------------------
# Dashboard "Recent Orders" (2026-08-03) -- moved here from the Status page
# (see CLAUDE.md's Status-page-restructuring section). invest_orders is
# real, permanent order history -- a genuinely different table from
# pending_orders (the Candidate Queue above), so this is a relocation for
# organisation, not a de-duplication.
# ---------------------------------------------------------------------------

def test_dashboard_recent_orders_shows_real_invest_orders_rows(db_engine, dashboard_app):
    _seed_invest_order(db_engine, ticker="AAPL", direction="BUY", status="FILLED", estimated_value_gbp=2500.0)
    _seed_invest_order(db_engine, ticker="MSFT", direction="SELL", status="FAILED", estimated_value_gbp=1800.0)

    at = dashboard_app.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Recent Orders" in all_text

    dfs = [d.value for d in at.dataframe if "Ticker" in d.value.columns and "Dir" in d.value.columns]
    assert dfs, "expected a Recent Orders dataframe with Ticker/Dir columns"
    odf = dfs[0]
    assert set(odf["Ticker"]) == {"AAPL", "MSFT"}
    assert odf.loc[odf["Ticker"] == "AAPL", "Status"].iloc[0] == "FILLED"
    assert odf.loc[odf["Ticker"] == "MSFT", "Status"].iloc[0] == "FAILED"


def test_dashboard_recent_orders_empty_state(dashboard_app):
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(c.value for c in at.caption)
    assert "No orders recorded yet" in all_text


# ---------------------------------------------------------------------------
# Performance page removed 2026-08-03 -- content relocated onto the
# Dashboard (Open Positions replacing Top Trades, plus new collapsible
# Closed Positions/Performance Metrics sections) and the Status page
# (Candidate Queue, tested in its own dedicated section above; Placed,
# Unconfirmed Exits). See CLAUDE.md's "Performance page removed — content
# relocated" section for the full three-way principle behind this split.
# ---------------------------------------------------------------------------

def test_dashboard_shows_open_positions_replacing_top_trades(dashboard_app):
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Open Positions" in all_text
    assert "Top Trades" not in all_text
    assert "Top 5 Winners" not in all_text
    assert "Top 5 Losers" not in all_text


def test_dashboard_open_positions_empty_state(dashboard_app):
    """No T212 credentials in the test environment -- get_brief_cards()
    correctly returns [] (see tests/test_performance.py:test_brief_cards_
    no_credentials_returns_empty), so the Dashboard's own empty-state
    copy must render, not an exception or a blank section."""
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(i.value for i in at.info)
    assert "No open positions" in all_text


def test_dashboard_closed_positions_is_a_collapsible_expander(dashboard_app):
    """A genuine st.expander (collapsible by construction -- the widget
    itself IS the "one click away" mechanism), not a flat, always-visible
    section like Open Positions/Recent Orders above it. AppTest's public
    Expander element doesn't expose its expanded=False default as a
    readable attribute, so this confirms presence/labelling -- the
    underlying source (main.py) is the direct source of truth for the
    expanded=False default itself."""
    at = dashboard_app.run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert any("Closed Positions" in label for label in labels)


def test_dashboard_performance_metrics_is_a_collapsible_expander(dashboard_app):
    at = dashboard_app.run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert any("Performance Metrics" in label for label in labels)


def test_dashboard_candidate_queue_and_unconfirmed_exits_are_gone(dashboard_app):
    """Both moved to the Status page 2026-08-03 -- neither is confirmed,
    settled real-money activity, so neither belongs on the Dashboard any
    more (see CLAUDE.md's three-way relocation principle)."""
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Candidate Queue" not in all_text
    assert "Placed, Unconfirmed Exits" not in all_text


def test_status_page_shows_placed_unconfirmed_exits_section(status_app):
    """Alongside Candidate Queue (covered by its own dedicated test
    section above) -- confirms this second relocated section also
    genuinely renders on the Status page, with its own empty-state copy
    in the default (no seeded SELL orders) test environment."""
    at = status_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Placed, Unconfirmed Exits" in all_text
    info_text = " ".join(i.value for i in at.info)
    assert "No exits currently awaiting confirmation" in info_text


def test_performance_page_and_route_are_fully_removed():
    """Confirmed genuinely removed, not disabled or hidden -- matching
    this project's own "confirmed removed, stays removed" pattern (e.g.
    test_scheduler_never_registers_monday_premarket_job, test_old_
    charts_page_functions_are_gone_not_dormant, test_status_page_danger_
    zone_and_manual_trigger_buttons_are_gone)."""
    import main
    assert not hasattr(main, "_render_performance_page")
    assert not hasattr(main, "_asset_type_map"), (
        "_asset_type_map was confirmed genuinely dead code (zero call sites, "
        "not even from the Performance page it was originally built for) "
        "and removed alongside the page -- its continued presence suggests "
        "the cleanup wasn't actually done"
    )


def test_dashboard_navigation_no_longer_offers_performance(dashboard_app):
    at = dashboard_app.run()
    assert not at.exception
    # Select by option content, not index -- the Dashboard page's own
    # "Account Value" range selector (Week/Month/Year) is also an
    # st.radio and renders before the sidebar nav in element order.
    nav_radio = next(r for r in at.radio if "Dashboard" in r.options)
    nav_options = list(nav_radio.options)
    assert "Performance" not in nav_options
    assert set(nav_options) == {"Dashboard", "Instruments", "Backtest", "Status", "Users"}


# ---------------------------------------------------------------------------
# Status page — job-recency staleness fixes (2026-08-03)
#
# The Status page's "Last Data Refresh"/"Last Scheduled Run" cards and the
# Pipeline table's two rows used a plain elapsed-hours count
# (main.py:_age_level(), warn 48h/error 96h for price refresh, 48h/72h for
# order logic) to decide OK/STALE/OVERDUE. A real production incident
# (16 positions opened at 14:30 UTC / 15:30 BST on a Monday, right after
# the stock_order_submission job actually ran) showed two separate bugs:
#
# 1. last_scheduled_run/last_scheduled_run_name were only ever stamped by
#    _eod_job()/_monday_premarket_job() — _stock_order_submission_job()
#    (the job that places real orders) and _weekly_summary_job() never
#    touched either key, so these two widgets (and the sidebar's "Last
#    scheduled run" caption, which reads the identical key) kept showing
#    an older job's name/timestamp long after a newer, more relevant job
#    had genuinely fired. Fixed in data/scheduler.py (see
#    tests/test_scheduler.py's own stamping tests for that half).
# 2. The naive 48h/96h-style thresholds misread an ordinary Friday-to-
#    Monday gap (63+ real hours, but only ONE genuine NYSE trading day) as
#    stale — inconsistent with the same page's own "Fresh (≤3 trading
#    days)" convention used for per-ticker price coverage. Fixed by
#    _trading_day_age_level()/_nyse_trading_days_since() (main.py),
#    reusing the identical NYSE-trading-day-count approach.
#
# Both fixes are exercised below via real, independently-computed NYSE
# calendar dates (pandas_market_calendars) rather than a frozen/mocked
# "today" — so these tests pass regardless of which real calendar day the
# suite happens to run on, the same discipline used elsewhere in this
# project for calendar-dependent tests.
# ---------------------------------------------------------------------------

def _real_nyse_sessions_last_30_days():
    """The exact NYSE session list main.py:_nyse_trading_days_since() itself
    computes (same library, same ~30-day lookback ending today) — used here
    to derive test timestamps that are genuinely N trading days stale as of
    whenever this test actually runs, without needing to freeze time."""
    import pandas_market_calendars as mcal
    from datetime import datetime as _dt, timedelta as _td

    today = _dt.utcnow().date()
    lookback = (_dt.utcnow() - _td(days=30)).date()
    cal = mcal.get_calendar("NYSE")
    sched = cal.schedule(start_date=lookback.strftime("%Y-%m-%d"), end_date=today.strftime("%Y-%m-%d"))
    return sorted(pd.Timestamp(d).date() for d in sched.index)


def _seed_metadata(db_engine, key: str, value: str) -> None:
    from sqlalchemy import text as sa_text
    with db_engine.begin() as conn:
        conn.execute(sa_text(
            "CREATE TABLE IF NOT EXISTS system_metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
        ))
        conn.execute(sa_text(
            "INSERT OR REPLACE INTO system_metadata (key, value, updated_at) VALUES (:k, :v, :t)"
        ), {"k": key, "v": value, "t": "2026-01-01T00:00:00"})


def _status_card_level(at, label: str) -> str:
    """Extract the colour-coded level ("ok"/"warn"/"error"/"info") of a
    top-level _status_card() by its label — each card renders as its own
    separate markdown element (col.markdown(...)) carrying a
    'border-left:3px solid {colour}' style, so this finds the element
    containing the given label and maps its colour back through
    main._LEVEL_COLOUR.

    Superseded 2026-08-03's own _pipeline_row_badge() helper: the standalone
    "Pipeline" section (whose two rows that helper parsed) was removed and
    folded into Scheduler Health's "End of Day" row as part of that same
    day's Pipeline/Scheduler-Health consolidation (see CLAUDE.md). The
    underlying staleness mechanism these tests actually validate —
    _trading_day_age_level()/_nyse_trading_days_since() — is unchanged and
    still drives the "Last Data Refresh"/"Last Scheduled Run" top-level
    cards this helper now reads instead."""
    import re
    import main as _main_mod
    colour_to_level = {v: k for k, v in _main_mod._LEVEL_COLOUR.items()}
    for m in at.markdown:
        if f">{label}</div>" in m.value:
            match = re.search(r"border-left:3px solid (#[0-9a-fA-F]{6})", m.value)
            if match:
                return colour_to_level.get(match.group(1), "unknown")
    raise AssertionError(f"No status card found for label {label!r}")


@pytest.fixture
def status_app(db_engine, monkeypatch, mocker):
    """Same import-ordering discipline as the `app`/`dashboard_app` fixtures
    above — see the `app` fixture's own docstring for why every patch must
    happen before AppTest.from_file("main.py") is constructed."""
    import streamlit as st
    st.cache_data.clear()
    mocker.patch("data.scheduler.start_scheduler")
    monkeypatch.setattr("main.get_engine", lambda: db_engine)
    monkeypatch.setattr(
        "data.ingestion.get_all_watchlist_entries",
        lambda config: [{"ticker": "AAPL", "name": "Apple", "exchange": "NYSE"}],
    )

    at = AppTest.from_file(_MAIN_PY, default_timeout=30)
    at.session_state["auth_user"] = _ADMIN_SESSION
    at.session_state["page"] = "status"
    return at


# ---------------------------------------------------------------------------
# Status page Candidate Queue Status column + filter (2026-08-02, relocated
# here 2026-08-03 from the Dashboard — see CLAUDE.md's "Performance page
# removed — content relocated" section for the three-way principle: the
# Dashboard now holds confirmed, settled real-money activity, while the
# Status page holds anything still pending or in flight, awaiting
# resolution, grouped with system health/config). Sourced from brokers.
# trading212.project_pending_order_admission(), the same read-only
# projection built during the urgent capacity/momentum verification, not a
# separate/approximate computation. Test names/logic are otherwise
# unchanged from their original Dashboard-page form — only the fixture
# (status_app instead of dashboard_app) and function names moved.
# ---------------------------------------------------------------------------

def _seed_pending_row(db_engine, *, ticker, momentum_pct, position_gbp=1000.0,
                       is_etf=False, status="PENDING", resolution_note=None,
                       decided_at=None):
    from sqlalchemy import text as sa_text
    from datetime import datetime
    now = decided_at or datetime.utcnow().isoformat()
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, stop_price, var_pct, status, resolution_note, created_at)
            VALUES (:ticker, :t212_ticker, :is_etf, 'USD', :now, 100.0,
                    :position_gbp, :momentum_pct, 90.0, 0.02, :status, :resolution_note, :now)
        """), {
            "ticker": ticker, "t212_ticker": f"{ticker}_US_EQ", "is_etf": is_etf,
            "now": now, "position_gbp": position_gbp, "momentum_pct": momentum_pct,
            "status": status, "resolution_note": resolution_note,
        })


def test_status_page_candidate_table_shows_status_from_the_real_projection(db_engine, status_app, mocker):
    _seed_pending_row(db_engine, ticker="SNDK", momentum_pct=27.0, position_gbp=806.81)
    _seed_pending_row(db_engine, ticker="WBD", momentum_pct=0.98, position_gbp=2500.0)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "SNDK", "momentum_pct": 27.0, "position_gbp": 806.81, "projected_status": "PROJECTED_ADMIT"},
        {"ticker": "WBD", "momentum_pct": 0.98, "position_gbp": 2500.0, "projected_status": "PROJECTED_REJECT_CAPACITY"},
    ])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert "Status" in df.columns
    assert df.loc[df["Ticker"] == "SNDK", "Status"].iloc[0] == "PROJECTED_ADMIT"
    assert df.loc[df["Ticker"] == "WBD", "Status"].iloc[0] == "PROJECTED_REJECT_CAPACITY"
    # Sourced from the real function, not a separate computation -- confirmed
    # it was actually called, not just that the right values happened to appear.
    from brokers.trading212 import project_pending_order_admission
    project_pending_order_admission.assert_called_once()


def test_status_page_candidate_status_labelled_as_projection_not_guarantee(db_engine, status_app, mocker):
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=0.5)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 0.5, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = status_app.run()

    assert not at.exception
    all_text = " ".join(c.value for c in at.caption)
    assert "projection" in all_text.lower()
    assert "not a guarantee" in all_text.lower()


def test_status_page_candidate_status_filter_narrows_the_table(db_engine, status_app, mocker):
    _seed_pending_row(db_engine, ticker="SNDK", momentum_pct=27.0)
    _seed_pending_row(db_engine, ticker="WBD", momentum_pct=0.98)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "SNDK", "momentum_pct": 27.0, "position_gbp": 806.81, "projected_status": "PROJECTED_ADMIT"},
        {"ticker": "WBD", "momentum_pct": 0.98, "position_gbp": 2500.0, "projected_status": "PROJECTED_REJECT_CAPACITY"},
    ])

    at = status_app.run()
    at.multiselect(key="candidate_status_filter").set_value(["PROJECTED_ADMIT"]).run()

    assert not at.exception
    df = at.dataframe[0].value
    assert set(df["Ticker"]) == {"SNDK"}


def test_status_page_candidate_status_shows_unavailable_when_projection_fails(db_engine, status_app, mocker):
    """A ticker with no matching projection entry (e.g. the projection
    call itself failed and returned []) must show a distinct
    "unavailable" status, never silently read as rejected."""
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=0.5)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert df.loc[df["Ticker"] == "AAPL", "Status"].iloc[0] == "PROJECTION_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Resolved candidates must not silently disappear (2026-08-03) -- real
# incident: SNDK was the top-ranked overnight candidate, 16 real orders
# were placed via the 14:30 UTC submission job, and SNDK was nowhere to be
# found on the Candidate Queue once it resolved to SKIPPED_PRICE_MOVED --
# get_pending_candidates() filtered to WHERE status = 'PENDING' only, even
# though the row was still sitting in pending_orders, fully within its
# 30-day retention window. See CLAUDE.md's write-up of the real SNDK
# resolution for the exact evidence.
# ---------------------------------------------------------------------------

def test_status_page_candidate_table_shows_a_resolved_candidate_not_just_pending_ones(db_engine, status_app, mocker):
    """The real SNDK scenario: a resolved (SKIPPED_PRICE_MOVED) row must
    still appear in the table -- not disappear -- showing its real,
    final status (not a PROJECTED_* guess) and its resolution_note."""
    _seed_pending_row(
        db_engine, ticker="SNDK", momentum_pct=26.997925, position_gbp=806.81,
        status="SKIPPED_PRICE_MOVED",
        resolution_note="price moved +3.11% since decision (decided 1214.83 -> now 1252.56), exceeds 3% sanity threshold",
    )
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=1.0, status="PENDING")
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 1.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert "SNDK" in set(df["Ticker"])
    sndk_row = df.loc[df["Ticker"] == "SNDK"].iloc[0]
    assert sndk_row["Status"] == "SKIPPED_PRICE_MOVED"
    assert "price moved +3.11%" in sndk_row["Note"]


def test_status_page_candidate_status_filter_offers_resolved_statuses_not_just_projected(db_engine, status_app, mocker):
    """The filter dropdown's options come from whatever's genuinely in the
    data (main.py already derives it dynamically via cdf["Status"].unique())
    -- this failed silently before the fix only because the underlying
    query itself excluded every non-PENDING row, so the dropdown never had
    a resolved status to offer in the first place."""
    _seed_pending_row(db_engine, ticker="SNDK", momentum_pct=26.9, status="SKIPPED_PRICE_MOVED",
                      resolution_note="price moved too much")
    _seed_pending_row(db_engine, ticker="WBD", momentum_pct=6.6, status="REJECTED_CAPACITY",
                      resolution_note="capacity already full")
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=1.0, status="PENDING")
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 1.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = status_app.run()

    assert not at.exception
    options = set(at.multiselect(key="candidate_status_filter").options)
    assert {"SKIPPED_PRICE_MOVED", "REJECTED_CAPACITY", "PROJECTED_ADMIT"} <= options

    at.multiselect(key="candidate_status_filter").set_value(["SKIPPED_PRICE_MOVED"]).run()
    df = at.dataframe[0].value
    assert set(df["Ticker"]) == {"SNDK"}


def test_status_page_candidate_table_a_ticker_with_two_historical_rows_shows_each_rows_own_status(db_engine, status_app, mocker):
    """A ticker can have several historical pending_orders rows (e.g. an
    earlier SUPERSEDED decision and the later, final one that replaced
    it) -- confirms Status is looked up per ROW, not by ticker, which
    would otherwise silently show one row's status on the other."""
    _seed_pending_row(db_engine, ticker="SNDK", momentum_pct=26.0, status="SUPERSEDED",
                      resolution_note="cleared for fresh re-run", decided_at="2026-08-02T21:31:10")
    _seed_pending_row(db_engine, ticker="SNDK", momentum_pct=27.0, status="SKIPPED_PRICE_MOVED",
                      resolution_note="price moved too much", decided_at="2026-08-02T21:50:29")
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    sndk_rows = df.loc[df["Ticker"] == "SNDK"]
    assert len(sndk_rows) == 2
    assert set(sndk_rows["Status"]) == {"SUPERSEDED", "SKIPPED_PRICE_MOVED"}


def test_status_page_candidate_table_no_change_to_retention_only_to_visibility(db_engine, status_app, mocker):
    """Explicit confirmation this is a query/filter fix, not a retention
    change: a resolved row from several days ago (well within the
    existing 30-day pending_orders retention window -- unaffected by this
    task) is shown exactly like a same-day one; nothing here alters how
    long rows are kept, only whether the UI can reach them."""
    _seed_pending_row(
        db_engine, ticker="OLDROW", momentum_pct=5.0, status="SUBMITTED",
        decided_at="2026-07-20T21:30:00",
    )
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert "OLDROW" in set(df["Ticker"])
    assert df.loc[df["Ticker"] == "OLDROW", "Status"].iloc[0] == "SUBMITTED"


# ---------------------------------------------------------------------------
# Candidate-table momentum-strength label (2026-08-02) -- a qualitative
# label (Weak/Moderate/Strong/Very Strong) alongside the raw momentum
# percentage, using RELATIVE percentile bands recomputed from the current
# live candidate pool every render (see main.py:_compute_momentum_
# strength_bands()'s own docstring for the relative-vs-absolute
# reasoning). Also removes the "Type" column, which always read "STOCK"
# for every row now that ETFs are fully out of live trading (see
# CLAUDE.md's "ETF sleeve removed from live trading").
# ---------------------------------------------------------------------------

def test_status_page_candidate_table_strength_label_uses_relative_percentile_bands(db_engine, status_app, mocker):
    """10 candidates with momentum 1..10 -- exact, hand-computed pandas
    quantile boundaries (q25=3.25, q75=7.75, q90=9.1) put 1-3 in Weak,
    4-7 in Moderate, 8-9 in Strong, and 10 alone in Very Strong. Proves
    the bands are genuinely derived from THIS pool's own distribution,
    not a fixed/arbitrary cutoff."""
    tickers = [f"T{i}" for i in range(1, 11)]
    projection = []
    for i, t in enumerate(tickers, start=1):
        _seed_pending_row(db_engine, ticker=t, momentum_pct=float(i))
        projection.append({"ticker": t, "momentum_pct": float(i), "position_gbp": 1000.0,
                            "projected_status": "PROJECTED_ADMIT"})
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=projection)

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    strength_by_ticker = dict(zip(df["Ticker"], df["Strength"]))
    for t in ["T1", "T2", "T3"]:
        assert strength_by_ticker[t] == "Weak", strength_by_ticker
    for t in ["T4", "T5", "T6", "T7"]:
        assert strength_by_ticker[t] == "Moderate", strength_by_ticker
    for t in ["T8", "T9"]:
        assert strength_by_ticker[t] == "Strong", strength_by_ticker
    assert strength_by_ticker["T10"] == "Very Strong", strength_by_ticker


def test_status_page_candidate_table_raw_momentum_percentage_still_shown_alongside_strength(db_engine, status_app, mocker):
    """The qualitative label must be ADDITIVE, never a replacement for the
    raw figure -- same "show both, hide neither" principle as the
    fixed-denominator/peak-relative drawdown pair shown together
    elsewhere in this app."""
    _seed_pending_row(db_engine, ticker="SNDK", momentum_pct=26.997925)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "SNDK", "momentum_pct": 26.997925, "position_gbp": 806.81, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert "Momentum" in df.columns
    assert "Strength" in df.columns
    row = df.loc[df["Ticker"] == "SNDK"].iloc[0]
    assert "2699.8%" in row["Momentum"]  # raw percentage, unmodified
    assert row["Strength"]  # a real qualitative label, not blank


def test_status_page_candidate_table_type_column_removed(db_engine, status_app, mocker):
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=1.0)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 1.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert "Type" not in df.columns


def test_status_page_candidate_table_strength_shows_placeholder_with_too_few_candidates(db_engine, status_app, mocker):
    """Fewer than 4 usable momentum values -- not enough to form 4
    meaningful percentile bands -- must show a plain placeholder, never a
    misleading or arbitrarily-assigned label."""
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=1.0)
    _seed_pending_row(db_engine, ticker="MSFT", momentum_pct=2.0)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 1.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
        {"ticker": "MSFT", "momentum_pct": 2.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert set(df["Strength"]) == {"—"}


def test_status_page_candidate_table_strength_caption_states_relative_reasoning(db_engine, status_app, mocker):
    for i, t in enumerate(["A", "B", "C", "D"], start=1):
        _seed_pending_row(db_engine, ticker=t, momentum_pct=float(i))
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": t, "momentum_pct": float(i), "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"}
        for i, t in enumerate(["A", "B", "C", "D"], start=1)
    ])

    at = status_app.run()

    assert not at.exception
    all_text = " ".join(c.value for c in at.caption)
    assert "relative" in all_text.lower()
    assert "recalculated" in all_text.lower() or "fresh" in all_text.lower()


def test_status_page_price_refresh_not_stale_after_one_trading_day_gap(db_engine, status_app):
    """A refresh genuinely only 1 NYSE trading day old (e.g. Friday's EOD
    run, as of the following Monday) must show the "ok" level on the "Last
    Data Refresh" card — not the "warn"/"error" a plain elapsed-hours count
    would incorrectly show for a 63+ hour gap that is still only one real
    trading day."""
    sessions = _real_nyse_sessions_last_30_days()
    one_trading_day_ago = sessions[-2]  # exactly 1 session strictly after this date
    _seed_metadata(db_engine, "last_full_refresh", f"{one_trading_day_ago.isoformat()}T21:30:00")

    at = status_app.run()

    assert not at.exception
    level = _status_card_level(at, "Last Data Refresh")
    assert level == "ok", f"expected ok for a 1-trading-day-old refresh, got {level}"


def test_status_page_price_refresh_shows_overdue_for_a_genuine_multi_day_gap(db_engine, status_app):
    """Confirms the fix isn't just 'always ok' — a refresh genuinely many
    NYSE trading days old must still show the "error" level."""
    sessions = _real_nyse_sessions_last_30_days()
    many_trading_days_ago = sessions[0]  # the oldest date in the whole lookback window
    _seed_metadata(db_engine, "last_full_refresh", f"{many_trading_days_ago.isoformat()}T21:30:00")

    at = status_app.run()

    assert not at.exception
    level = _status_card_level(at, "Last Data Refresh")
    assert level == "error", f"expected error for a many-trading-day-old refresh, got {level}"


def test_status_page_order_logic_row_shows_most_recent_job_not_an_older_one(db_engine, status_app):
    """The "Last Scheduled Run" card reads last_scheduled_run/
    last_scheduled_run_name — this confirms the page genuinely surfaces
    whatever job most recently stamped that key (here: a real, current
    Stock Order Submission run), not a stale value left over from an older
    job. The scheduler-side fix that makes _stock_order_submission_job()
    actually stamp this key is covered in tests/test_scheduler.py — this
    test covers the display side."""
    sessions = _real_nyse_sessions_last_30_days()
    today_or_last_session = sessions[-1]
    _seed_metadata(db_engine, "last_scheduled_run", f"{today_or_last_session.isoformat()}T14:30:00")
    _seed_metadata(db_engine, "last_scheduled_run_name", "Stock Order Submission")

    at = status_app.run()

    assert not at.exception
    level = _status_card_level(at, "Last Scheduled Run")
    assert level == "ok"
    assert any("Stock Order Submission" in m.value for m in at.markdown)


# ---------------------------------------------------------------------------
# Status page — "Trade Alerts" renamed to "Open Trade Monitor", and the
# separate "Pipeline" section merged into "Scheduler Health" (2026-08-03)
#
# "Trade Alerts" read as a passive notification system; the underlying
# hourly job (maybe_exit_positions()) actively evaluates stop-loss/
# signal-off/max-holding/crisis conditions and places real SELL orders —
# arguably the most safety-critical recurring job in the system. Renamed
# to make that plain. Purely a label change — job id/function/schedule
# untouched (see tests/test_scheduler.py for those).
#
# "Pipeline" and "Scheduler Health" were two separate sections both
# answering "what's happening with the scheduled jobs" — Pipeline's two
# rows (price refresh coverage, last-run-type) are both genuinely produced
# by the single "End of Day" job Scheduler Health already lists, so they
# were folded into that row's new Description column rather than kept as
# a standalone section. The merged section moved into Pipeline's former
# (near-the-top) position rather than staying further down the page.
# ---------------------------------------------------------------------------

def test_status_page_pipeline_section_is_gone(status_app):
    at = status_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "### Pipeline" not in all_text
    assert "1 · Price Data Refresh" not in all_text
    assert "2 · Order Logic" not in all_text


def test_status_page_shows_open_trade_monitor_not_trade_alerts(db_engine, status_app):
    """Job rows (including the renamed "Open Trade Monitor" label) only
    render once the scheduler is detected as active — via either a real
    in-process APScheduler or a recent daemon heartbeat — otherwise the
    page shows the "Scheduler is not running" warning instead. Seed a
    fresh heartbeat so the daemon-mode rendering branch (the one that
    actually runs in production) executes.

    Scoped to markdown rendered from the "### Scheduler Health" heading
    onward, not the whole page: the "Last Deployment" card above it renders
    the latest real git commit's own subject line verbatim, and a commit
    describing this exact rename legitimately contains the literal phrase
    "Trade Alerts" as prose — a whole-page substring check would (and, when
    first written, did) false-positive on that unrelated, expected text."""
    from datetime import datetime as _dt
    _seed_metadata(db_engine, "scheduler_heartbeat", _dt.utcnow().isoformat())

    at = status_app.run()
    assert not at.exception
    sh_idx = next(i for i, m in enumerate(at.markdown) if "Scheduler Health" in m.value)
    scoped_text = " ".join(m.value for m in at.markdown[sh_idx:])
    assert "Open Trade Monitor" in scoped_text
    assert "Trade Alerts" not in scoped_text


def test_status_page_end_of_day_row_shows_folded_in_pipeline_detail(db_engine, status_app):
    """The "End of Day" Scheduler Health row's new Description column must
    show both of the old Pipeline section's two data points — price
    coverage and last-run-type — since that job is what genuinely produces
    both."""
    from datetime import datetime as _dt
    _seed_metadata(db_engine, "last_run_type", "full")
    _seed_metadata(db_engine, "scheduler_heartbeat", _dt.utcnow().isoformat())

    at = status_app.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "End of Day" in all_text
    # price_desc: either genuine coverage text or the no-data placeholder —
    # either way it must appear somewhere the old Pipeline row would have
    # shown it.
    assert ("tickers up to date" in all_text
            or "No price data yet" in all_text
            or "missing" in all_text)
    assert "Full Refresh" in all_text


def test_status_page_scheduler_health_appears_before_scheduled_runs(status_app):
    """The merged section must sit in Pipeline's former (near-the-top)
    position — before "Scheduled Runs" — not further down the page where
    Scheduler Health used to live."""
    at = status_app.run()
    assert not at.exception

    sh_idx = next(i for i, m in enumerate(at.markdown) if "Scheduler Health" in m.value)
    sr_idx = next(i for i, m in enumerate(at.markdown) if "### Scheduled Runs" in m.value)
    assert sh_idx < sr_idx, "Scheduler Health must render before Scheduled Runs"


# ---------------------------------------------------------------------------
# Status page restructuring (2026-08-03) — Manual Controls moved to a
# bottom "Danger Zone", Email Notifications removed entirely, T212 Invest
# Account split (live state removed as a Dashboard duplicate, config/
# methodology kept), Recent Orders moved to the Dashboard, Instrument
# Mapping moved to the Instruments page. See CLAUDE.md's
# Status-page-restructuring section.
# ---------------------------------------------------------------------------

def test_status_page_email_notifications_section_is_gone(status_app):
    at = status_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Email Notifications" not in all_text


def test_status_page_danger_zone_and_manual_trigger_buttons_are_gone(status_app):
    """The Danger Zone section (Manual Controls: "Refresh Data + Orders
    Now" / "Order Logic Only") was removed entirely 2026-08-03, not
    disabled or hidden — confirmed genuinely absent, matching this
    project's own "confirmed removed, stays removed" pattern
    (e.g. test_scheduler_never_registers_monday_premarket_job,
    test_old_charts_page_functions_are_gone_not_dormant). Reasoning: the
    manual buttons were a rare-but-real collision risk (a trigger firing
    from the separate Streamlit UI process, outside every scheduling
    safeguard built for the actual scheduled jobs — see CLAUDE.md's
    "structural safeguards against future T212 API scheduling
    collisions" section), and the original case for keeping them
    (recovery from a missed scheduled run) has been substantially
    superseded by autonomous monitoring built since: the EOD-completion
    signal, the crisis banner, and the exit-check fetch-status banner
    all now surface a missed or failed run automatically. See CLAUDE.md's
    "remove the manual 'trigger pipeline now' buttons entirely" section."""
    at = status_app.run()
    assert not at.exception

    all_text = " ".join(m.value for m in at.markdown)
    assert "Danger Zone" not in all_text
    assert "Manual Controls" not in all_text

    button_keys = {b.key for b in at.button}
    assert "status_full_refresh" not in button_keys
    assert "status_run_orders" not in button_keys

    import main
    assert not hasattr(main, "get_all_tickers"), (
        "get_all_tickers was imported into main.py only for the removed "
        "buttons' own use -- its continued presence suggests the import "
        "wasn't actually cleaned up"
    )
    assert not hasattr(main, "pull_incremental"), (
        "pull_incremental was imported into main.py only for the removed "
        "'Refresh Data + Orders Now' button -- its continued presence "
        "suggests the import wasn't actually cleaned up"
    )


def test_status_page_t212_account_no_longer_shows_live_state(db_engine, status_app, monkeypatch):
    """Account value/cash/invested/open-position count are genuinely
    duplicated by the Dashboard's own account cards -- confirmed removed
    from the Status page's T212 Invest Account section (config.yaml in
    this repo already has trading212_invest.enabled: true, so this
    exercises the real "enabled" branch; T212 credentials are set via
    monkeypatch to reach the branch that used to render the metrics)."""
    import json as _json
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    _seed_metadata(db_engine, "t212_account_snapshot", _json.dumps({
        "totalValue": 50000.0,
        "cash": {"availableToTrade": 40000.0},
        "investments": {"currentValue": 10000.0},
    }))
    _seed_metadata(db_engine, "t212_positions_snapshot", _json.dumps([{"ticker": "AAPL_US_EQ"}]))

    at = status_app.run()

    assert not at.exception
    metric_labels = {m.label for m in at.metric}
    assert "Account Value" not in metric_labels
    assert "Available Cash" not in metric_labels
    assert "Invested" not in metric_labels
    assert "Open Positions" not in metric_labels

    all_text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "Dashboard" in all_text
    assert "Instruments" in all_text

    # The refresh action itself is kept -- it's a still-useful ACTION, not
    # duplicated "state", and it refreshes the same snapshot the Dashboard
    # reads.
    button_keys = {b.key for b in at.button}
    assert "t212_refresh_btn" in button_keys


def test_status_page_recent_orders_and_instrument_mapping_sections_are_gone(status_app, monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")

    at = status_app.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Recent Orders" not in all_text
    assert "Instrument mapping" not in all_text
    assert "instrument resolutions" not in all_text.lower()


# ---------------------------------------------------------------------------
# Status page — "Last Deployment" card shows the deployed commit hash as a
# clickable GitHub link (2026-08-05). deploy.sh writes the real `git
# rev-parse HEAD` to a `.deployed_commit` file at deploy time; the Status
# page reads that file (main.py:_deployed_commit_hash()) rather than
# re-deriving it from `git log` at app runtime. See CLAUDE.md's deploy
# pipeline sections for why deploy-time capture beats a runtime git call
# here (it reflects what was actually deployed even after a stray manual
# `git pull` on the server).
# ---------------------------------------------------------------------------

def test_deployed_commit_hash_reads_the_deploy_time_file(tmp_path, monkeypatch):
    import main as _main_mod
    fake_file = tmp_path / ".deployed_commit"
    full_hash = "d1a067a1234567890abcdef1234567890abcdef"
    fake_file.write_text(full_hash + "\n", encoding="utf-8")
    monkeypatch.setattr(_main_mod, "_DEPLOYED_COMMIT_FILE", fake_file)

    assert _main_mod._deployed_commit_hash() == full_hash


def test_deployed_commit_hash_returns_none_when_file_absent(tmp_path, monkeypatch):
    """Local dev / any environment deploy.sh never ran in -- the Status page
    must not crash or show a broken link, just omit it."""
    import main as _main_mod
    monkeypatch.setattr(_main_mod, "_DEPLOYED_COMMIT_FILE", tmp_path / "does_not_exist")

    assert _main_mod._deployed_commit_hash() is None


def test_github_commit_url_is_correctly_formed():
    import main as _main_mod
    assert (
        _main_mod._github_commit_url("d1a067a1234567890abcdef1234567890abcdef")
        == "https://github.com/brent-jenkins/investment-research-sp500/commit/d1a067a1234567890abcdef1234567890abcdef"
    )


@pytest.fixture
def deployed_commit_file():
    """Writes to the REAL `.deployed_commit` path at the repo root (not a
    tmp_path double), because AppTest.from_file("main.py") re-execs the
    actual main.py source fresh on every .run() in its own namespace --
    monkeypatching the separately-imported `main` module's
    _DEPLOYED_COMMIT_FILE constant has no effect on that fresh execution,
    the same re-exec gotcha the `app`/`instruments_app` fixtures' own
    docstrings already document for get_engine/get_all_watchlist_entries.
    _ROOT is computed from main.py's own __file__, so the real file is the
    only thing AppTest's execution will ever actually read.

    Saves/restores any pre-existing content so a stray leftover local
    `.deployed_commit` (there shouldn't be one -- it's deploy.sh/server-only
    and gitignored) is never lost."""
    import main as _main_mod
    real_file = _main_mod._DEPLOYED_COMMIT_FILE
    original = real_file.read_text(encoding="utf-8") if real_file.exists() else None
    try:
        yield real_file
    finally:
        if original is None:
            real_file.unlink(missing_ok=True)
        else:
            real_file.write_text(original, encoding="utf-8")


def test_status_page_shows_deployed_commit_hash_as_a_github_link(status_app, deployed_commit_file):
    full_hash = "d1a067a1234567890abcdef1234567890abcdef"
    deployed_commit_file.write_text(full_hash, encoding="utf-8")

    at = status_app.run()
    assert not at.exception

    deploy_card = next(
        (m.value for m in at.markdown if ">Last Deployment</div>" in m.value), None
    )
    assert deploy_card is not None, "Last Deployment card not found"
    assert "d1a067a" in deploy_card, "short (7-char) hash not shown"
    assert (
        f'href="https://github.com/brent-jenkins/investment-research-sp500/commit/{full_hash}"'
        in deploy_card
    ), "hash is not a link to the real GitHub commit page"
    assert 'target="_blank"' in deploy_card


def test_status_page_last_deployment_card_has_no_hash_link_when_file_absent(status_app, deployed_commit_file):
    deployed_commit_file.unlink(missing_ok=True)

    at = status_app.run()
    assert not at.exception

    deploy_card = next(
        (m.value for m in at.markdown if ">Last Deployment</div>" in m.value), None
    )
    assert deploy_card is not None, "Last Deployment card not found"
    assert "github.com" not in deploy_card
