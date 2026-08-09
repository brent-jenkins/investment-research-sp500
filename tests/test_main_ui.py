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


def test_instruments_page_converts_dot_notation_tickers_to_yahoo_hyphen_form(db_engine, monkeypatch, mocker):
    """BRK.B and BF.B are real S&P 500 dot share-class tickers -- Yahoo
    Finance's own URLs only resolve the hyphen form (BRK-B, BF-B), the
    identical conversion data/ingestion.py:_yf_symbol() already applies to
    outgoing yfinance calls (see its docstring, and commit d1a067a). This
    page's link construction must reuse that same shared function rather
    than assume the raw dot-notation ticker is directly usable in a Yahoo
    Finance URL -- it wasn't, until this test/fix.

    The Ticker column itself keeps the dot form (matches
    universe_membership / DB storage) -- only the outgoing Yahoo Finance
    link is converted, same scoping _yf_symbol() itself documents."""
    mocker.patch("data.scheduler.start_scheduler")
    monkeypatch.setattr("main.get_engine", lambda: db_engine)
    monkeypatch.setattr(
        "data.ingestion.get_all_watchlist_entries",
        lambda config: [
            {"ticker": "BRK.B", "name": "BRK.B", "exchange": "NYSE"},
            {"ticker": "BF.B", "name": "BF.B", "exchange": "NYSE"},
            {"ticker": "AAPL", "name": "AAPL", "exchange": "NYSE"},
        ],
    )

    at = AppTest.from_file(_MAIN_PY, default_timeout=30)
    at.session_state["auth_user"] = _ADMIN_SESSION
    at.session_state["page"] = "instruments"
    at = at.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert set(df["Ticker"]) == {"BRK.B", "BF.B", "AAPL"}
    assert "finance.yahoo.com/quote/BRK-B" in df.loc[df["Ticker"] == "BRK.B", "Yahoo Finance"].iloc[0]
    assert "finance.yahoo.com/quote/BF-B" in df.loc[df["Ticker"] == "BF.B", "Yahoo Finance"].iloc[0]
    # Dot-free tickers are unaffected by the conversion.
    assert "finance.yahoo.com/quote/AAPL" in df.loc[df["Ticker"] == "AAPL", "Yahoo Finance"].iloc[0]


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
# output.performance.get_crisis_status()'s entry_blocked field. Changed
# 2026-08-09: that field is now a READ-ONLY stored snapshot (data.scheduler.
# _market_hours_check_job()'s hourly write), not a live call made from this
# render path -- see CLAUDE.md's "UI render-path blocking audit" section,
# finding 3. The tests below mock get_crisis_status() directly, so they
# exercise the banner's own rendering logic unchanged; the no-live-call
# guarantee itself is proven separately, in test_performance.py.
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


def test_dashboard_crisis_banner_shows_as_of_timestamp_when_available(db_engine, dashboard_app, mocker):
    """2026-08-09: the banner reads a stored (hourly) snapshot now, not a
    live call -- it must show when that snapshot was last taken, honestly,
    rather than implying real-time freshness it doesn't have. The stored
    value is naive-UTC (matching data.scheduler's own datetime.utcnow()
    convention); _fmt_uk() converts it to UK local time for display (here,
    13:05 UTC -> 14:05 BST in August) -- asserting on the converted value,
    not the raw stored one, since that conversion is the correct, intended
    behaviour, not something to route around in the test."""
    mocker.patch("output.performance.get_crisis_status", return_value={
        "entry_blocked": True, "entry_blocked_checked_at": "2026-08-09T13:05:00",
    })

    at = dashboard_app.run()

    assert not at.exception
    banner_text = " ".join(e.value for e in at.error if "crisis override active" in e.value.lower())
    assert banner_text
    assert "2026-08-09 14:05 bst" in banner_text.lower()
    assert "checked hourly" in banner_text.lower()


def test_dashboard_crisis_banner_shows_time_unknown_when_timestamp_missing(db_engine, dashboard_app, mocker):
    """A stored-but-unstamped or missing entry_blocked_checked_at must not
    crash the render -- falls back to an explicit 'time unknown' rather
    than a blank or fabricated timestamp."""
    mocker.patch("output.performance.get_crisis_status", return_value={"entry_blocked": True})

    at = dashboard_app.run()

    assert not at.exception
    banner_text = " ".join(e.value.lower() for e in at.error if "crisis override active" in e.value.lower())
    assert banner_text
    assert "time unknown" in banner_text


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
    """No open_position_cards_snapshot has ever been written in the fresh
    db_engine test database (2026-08-09 -- see CLAUDE.md's "UI render-path
    blocking audit" follow-up, finding 4: this section now reads a stored
    snapshot instead of calling get_brief_cards() directly), so
    get_stored_open_position_cards() correctly returns {"cards": [],
    "fetched_at": None} and the Dashboard's own empty-state
    copy must render, not an exception or a blank section."""
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(i.value for i in at.info)
    assert "No open positions" in all_text


def test_dashboard_open_positions_no_refresh_yet_caption(db_engine, dashboard_app):
    """A brand-new deployment (no open_position_cards_fetched_at ever
    written -- the hourly account_state_refresh job hasn't completed a
    cycle yet) must show an explicit "hasn't populated yet" caption, not
    a blank section or a fabricated timestamp -- same convention as the
    crisis banner's own "time unknown" handling."""
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(c.value for c in at.caption)
    assert "no open-position refresh has completed yet" in all_text.lower()


# ---------------------------------------------------------------------------
# Dashboard open-position cards now read a stored snapshot only (2026-08-09)
# -- see CLAUDE.md's "UI render-path blocking audit" follow-up, finding 4.
# data.scheduler._account_state_job() (hourly) computes and stores the card
# data; main.py's _get_open_cards() -> output.performance.get_stored_open_
# position_cards() is a pure system_metadata read. These tests seed that
# stored snapshot directly via set_metadata(), the same pattern the crisis-
# status tests already use for crisis_mode_active/crisis_mode_checked_at.
# ---------------------------------------------------------------------------

def _seed_open_position_cards_snapshot(fetched_at="2026-08-09T10:05:00", cards=None):
    import json as _json
    from data.database import set_metadata

    if cards is None:
        cards = [{
            "ticker": "VOO", "t212_ticker": "I50Dl_EQ", "asset_type": "STOCK",
            "entry_price": 100.0, "quantity": 10.0, "entry_date": "2026-08-01T00:00:00",
            "days_held": 8, "max_holding_days": 90, "live_price": 105.0,
            "currency": "USD", "current_value_gbp": 800.0, "unreal_pnl_gbp": 40.0,
            "unreal_pct": 5.0, "momentum_pct_at_entry": 0.12, "stop_price": 95.0,
            "current_sig_on": True, "current_is_member": True, "crisis_now": False,
            "status": "ON TRACK", "status_priority": 0,
        }]
    set_metadata("open_position_cards_snapshot", _json.dumps(cards))
    if fetched_at is not None:
        set_metadata("open_position_cards_fetched_at", fetched_at)
    return cards


def test_dashboard_open_positions_renders_from_stored_snapshot(db_engine, dashboard_app):
    _seed_open_position_cards_snapshot()

    at = dashboard_app.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "VOO" in all_text
    assert "ON TRACK" in all_text
    assert "No open positions" not in " ".join(i.value for i in at.info)


def test_dashboard_open_positions_shows_as_of_timestamp(db_engine, dashboard_app):
    _seed_open_position_cards_snapshot(fetched_at="2026-08-09T10:05:00")

    at = dashboard_app.run()

    assert not at.exception
    all_text = " ".join(c.value for c in at.caption).lower()
    assert "as of" in all_text
    assert "refreshed hourly" in all_text


def test_dashboard_open_positions_stale_refresh_keeps_last_good_cards_and_original_timestamp(
    db_engine, dashboard_app,
):
    """If the hourly refresh has failed since, the last good snapshot and
    its ORIGINAL fetched_at must keep showing -- never blank, never an
    error, never a silently-refreshed timestamp for stale data. Simulated
    here simply by seeding a snapshot and confirming the Dashboard shows
    exactly the seeded (old) timestamp back, unmodified, on every render --
    the render path has no mechanism to advance it itself."""
    _seed_open_position_cards_snapshot(fetched_at="2026-08-01T09:00:00")

    at = dashboard_app.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "VOO" in all_text
    caption_text = " ".join(c.value for c in at.caption)
    assert "2026-08-01" in caption_text


def test_dashboard_open_position_cards_render_path_never_calls_t212_or_yfinance(
    db_engine, dashboard_app, mocker,
):
    """The decisive render-path proof (2026-08-09) -- matching
    test_sector_breakdown_render_path_never_calls_yfinance and
    test_crisis_status_render_path_never_calls_the_live_check's own shape
    (tests/test_performance.py): patches BOTH the Trading212 client and
    yfinance to raise if invoked at all while the Dashboard's open-position
    cards render, with a real stored snapshot seeded so the section has
    something to actually render rather than trivially short-circuiting on
    an empty list."""
    _seed_open_position_cards_snapshot()

    mocker.patch(
        "brokers.trading212.Trading212Client",
        side_effect=AssertionError("Dashboard open-position cards must never construct a Trading212Client"),
    )
    mocker.patch(
        "yfinance.Ticker",
        side_effect=AssertionError("Dashboard open-position cards must never call yfinance"),
    )

    at = dashboard_app.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "VOO" in all_text


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


# ---------------------------------------------------------------------------
# Forward shadow comparison Dashboard section (2026-08-09) -- see CLAUDE.md's
# "Forward shadow comparison" section. Pure DB reads; must never call T212
# or yfinance from the render path, matching this project's established
# render-path-blocking-audit standard.
# ---------------------------------------------------------------------------

def _seed_admission_ranking_shadow_row(
    engine, *, pending_order_id, ticker="AAPL", active_mode="absolute_momentum",
    admitted_momentum=True, admitted_rank=False, outcome_available=False,
    pnl_gbp=None, pnl_pct=None, decided_at="2026-08-09T21:30:00",
):
    from sqlalchemy import text as sa_text
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO admission_ranking_shadow
                (pending_order_id, decided_at, ticker, is_etf, momentum_pct, relative_rank_pct,
                 position_gbp, momentum_rank_position, relative_rank_rank_position,
                 admitted_under_absolute_momentum, admitted_under_relative_rank, active_mode,
                 outcome_available, pnl_gbp, pnl_pct, exit_reason, closed_at, recorded_at)
            VALUES (:pid, :decided_at, :ticker, 0, 0.10, 0.05, 500.0, 1, 1, :am, :ar, :mode,
                    :oa, :pnl_gbp, :pnl_pct, :er, :closed_at, :decided_at)
        """), {
            "pid": pending_order_id, "decided_at": decided_at, "ticker": ticker,
            "am": admitted_momentum, "ar": admitted_rank, "mode": active_mode,
            "oa": outcome_available, "pnl_gbp": pnl_gbp, "pnl_pct": pnl_pct,
            "er": "SIGNAL_OFF" if outcome_available else None,
            "closed_at": decided_at if outcome_available else None,
        })


def test_dashboard_shadow_comparison_is_a_collapsible_expander(dashboard_app):
    at = dashboard_app.run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert any("Forward Shadow Comparison" in label for label in labels)


def test_dashboard_shadow_comparison_empty_state_no_crash(dashboard_app):
    """No shadow rows recorded yet (a fresh deploy, or before the first
    stock submission run) -- must show a plain, honest "not recorded yet"
    caption, never crash or show a fabricated zero."""
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.caption)
    assert "No shadow-comparison decisions recorded yet" in all_text


def test_dashboard_shadow_comparison_shows_bucket_counts(db_engine, dashboard_app):
    _seed_admission_ranking_shadow_row(db_engine, pending_order_id=1, ticker="A", admitted_momentum=True, admitted_rank=True)
    _seed_admission_ranking_shadow_row(db_engine, pending_order_id=2, ticker="B", admitted_momentum=True, admitted_rank=False)
    _seed_admission_ranking_shadow_row(db_engine, pending_order_id=3, ticker="C", admitted_momentum=False, admitted_rank=True)

    at = dashboard_app.run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Shared admissions"] == "1"
    assert metrics["Absolute Momentum-only admissions"] == "1"
    assert metrics["Alternative-only admissions"] == "1"


def test_dashboard_shadow_comparison_states_the_structural_limitation(db_engine, dashboard_app):
    """The inactive ranking's own unique admissions must be explicitly
    labelled as never having a live outcome -- not silently shown as a
    bare number a reader could mistake for a 0% win rate."""
    _seed_admission_ranking_shadow_row(db_engine, pending_order_id=1, ticker="C", admitted_momentum=False, admitted_rank=True)
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.caption)
    assert "never actually traded" in all_text
    assert "no live outcome" in all_text


def test_dashboard_shadow_comparison_win_rate_gated_below_five_closed(db_engine, dashboard_app):
    for i in range(4):
        _seed_admission_ranking_shadow_row(
            db_engine, pending_order_id=i + 1, ticker=f"T{i}", admitted_momentum=True, admitted_rank=False,
            outcome_available=True, pnl_gbp=10.0, pnl_pct=0.05,
        )
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.caption)
    assert "not yet interpretable" in all_text
    assert "4/5" in all_text


def test_dashboard_shadow_comparison_shows_real_stats_at_five_closed(db_engine, dashboard_app):
    for i in range(5):
        _seed_admission_ranking_shadow_row(
            db_engine, pending_order_id=i + 1, ticker=f"T{i}", admitted_momentum=True, admitted_rank=False,
            outcome_available=True, pnl_gbp=10.0, pnl_pct=0.05,
        )
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.caption)
    assert "win rate 100.0%" in all_text


def test_dashboard_shadow_comparison_shows_review_trigger_progress(db_engine, dashboard_app):
    _seed_admission_ranking_shadow_row(db_engine, pending_order_id=1, ticker="A")
    at = dashboard_app.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.caption)
    assert "Review trigger:" in all_text
    assert "/30 paired outcomes" in all_text


def test_dashboard_shadow_comparison_render_path_never_calls_t212_or_yfinance(db_engine, dashboard_app, mocker):
    """The decisive render-path proof, matching this project's established
    pattern (sector breakdown, crisis banner, open-position cards) --
    patches both the T212 client and yfinance to raise if invoked at all
    while this section renders, with real seeded data so the section has
    something non-trivial to actually render."""
    _seed_admission_ranking_shadow_row(db_engine, pending_order_id=1, ticker="A", outcome_available=True, pnl_gbp=10.0, pnl_pct=0.05)
    mocker.patch(
        "brokers.trading212.Trading212Client",
        side_effect=AssertionError("Shadow comparison section must never construct a Trading212Client"),
    )
    mocker.patch(
        "yfinance.Ticker",
        side_effect=AssertionError("Shadow comparison section must never call yfinance"),
    )
    at = dashboard_app.run()
    assert not at.exception


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
                       decided_at=None, admission_rank_value=None):
    from sqlalchemy import text as sa_text
    from datetime import datetime
    now = decided_at or datetime.utcnow().isoformat()
    # admission_rank_value (2026-08-09 admission-ranking promotion) defaults
    # to mirror momentum_pct -- the real decide phase always populates both,
    # and get_pending_candidates()/get_pending_candidates_ranked() now sort
    # by admission_rank_value DESC, not momentum_pct DESC. Leaving this NULL
    # (as an earlier version of this helper did) left every seeded row tied
    # on the real sort key, falling through to decided_at ASC -- i.e. plain
    # insertion order -- which is what caused
    # test_status_page_candidate_table_ranked_by_submission_priority to
    # observe seeding order instead of momentum order.
    if admission_rank_value is None:
        admission_rank_value = momentum_pct
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, admission_rank_value, stop_price, var_pct,
                 status, resolution_note, created_at)
            VALUES (:ticker, :t212_ticker, :is_etf, 'USD', :now, 100.0,
                    :position_gbp, :momentum_pct, :admission_rank_value, 90.0, 0.02,
                    :status, :resolution_note, :now)
        """), {
            "ticker": ticker, "t212_ticker": f"{ticker}_US_EQ", "is_etf": is_etf,
            "now": now, "position_gbp": position_gbp, "momentum_pct": momentum_pct,
            "admission_rank_value": admission_rank_value,
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


def test_status_page_candidate_queue_shows_snapshot_as_of_timestamp(db_engine, status_app, mocker):
    """2026-08-09 -- the projection now reads a stored, hourly-refreshed
    T212 snapshot rather than a live one (see brokers/trading212.py:
    project_pending_order_admission()'s docstring), so its own staleness
    must be visible right where the projection is consumed, not only on
    the account panel further up the page."""
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=0.5)
    checked_at = (datetime.utcnow() - timedelta(minutes=42)).isoformat()
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 0.5, "position_gbp": 1000.0,
         "projected_status": "PROJECTED_ADMIT", "snapshot_checked_at": checked_at},
    ])

    at = status_app.run()

    assert not at.exception
    all_text = " ".join(c.value for c in at.caption)
    assert "snapshot" in all_text.lower()
    assert "42m ago" in all_text


def test_status_page_candidate_queue_snapshot_caption_handles_missing_timestamp(db_engine, status_app, mocker):
    """No stored snapshot yet (e.g. immediately after a fresh deploy) must
    render a plain "unavailable" caption, never crash or fabricate a time."""
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=0.5)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 0.5, "position_gbp": 1000.0,
         "projected_status": "PROJECTED_ADMIT", "snapshot_checked_at": None},
    ])

    at = status_app.run()

    assert not at.exception
    all_text = " ".join(c.value for c in at.caption)
    assert "no account snapshot stored yet" in all_text.lower()


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
# Candidate Queue narrowed to an operational preview (2026-08-09) -- "what is
# likely to be bought tomorrow, in the order it will be attempted," not a
# complete history. See CLAUDE.md's Candidate Queue narrowing section.
# Resolved rows (REJECTED_CAPACITY/SUBMITTED/SKIPPED_*/SUPERSEDED) are no
# longer queried or shown on this page at all -- they stay in pending_orders,
# completely untouched, for research/audit use elsewhere (confirmed directly
# by output/performance.py:get_pending_candidates(), still unchanged, and by
# tests/test_performance.py's own coverage of it). The status_filter
# multiselect, the Strength label, and the Note/Decided At columns are gone
# along with it -- this is a deliberate simplification to the four fields
# that answer the operational question, not an oversight.
# ---------------------------------------------------------------------------

def test_status_page_candidate_table_shows_pending_only_not_resolved_rows(db_engine, status_app, mocker):
    """A resolved row (SKIPPED_PRICE_MOVED) must NOT appear on this page --
    the inverse of the pre-2026-08-09 requirement. It remains fully queryable
    via output/performance.py:get_pending_candidates(), just not surfaced
    here."""
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
    assert "SNDK" not in set(df["Ticker"])
    assert "AAPL" in set(df["Ticker"])


def test_status_page_candidate_table_no_status_filter_widget(db_engine, status_app, mocker):
    """The status_filter multiselect is gone -- there is only ever one
    status population shown (PENDING projections), so a filter over it
    serves no purpose."""
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=1.0)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 1.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = status_app.run()

    assert not at.exception
    assert len(at.multiselect) == 0


def test_status_page_candidate_table_ranked_by_submission_priority(db_engine, status_app, mocker):
    """Rows read top-down in the exact order real submission would attempt
    them -- momentum descending -- with an explicit Rank column, not just
    an implicit table order."""
    _seed_pending_row(db_engine, ticker="LOWMOM", momentum_pct=1.0)
    _seed_pending_row(db_engine, ticker="HIGHMOM", momentum_pct=9.0)
    _seed_pending_row(db_engine, ticker="MIDMOM", momentum_pct=5.0)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "HIGHMOM", "momentum_pct": 9.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
        {"ticker": "MIDMOM", "momentum_pct": 5.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
        {"ticker": "LOWMOM", "momentum_pct": 1.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_REJECT_CAPACITY"},
    ])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert list(df["Ticker"]) == ["HIGHMOM", "MIDMOM", "LOWMOM"]
    assert list(df["Rank"]) == [1, 2, 3]


def test_status_page_candidate_table_cutoff_caption_visible(db_engine, status_app, mocker):
    """A truncated view must never be mistaken for an empty tail -- the
    'showing top N of M pending' caption is always present when there are
    more PENDING rows than the cutoff shows."""
    projection = []
    for i in range(40):
        t = f"T{i}"
        _seed_pending_row(db_engine, ticker=t, momentum_pct=float(40 - i))
        status = "PROJECTED_ADMIT" if i < 17 else "PROJECTED_REJECT_CAPACITY"
        projection.append({"ticker": t, "momentum_pct": float(40 - i), "position_gbp": 1000.0,
                            "projected_status": status})
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=projection)

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert len(df) < 40
    all_text = " ".join(c.value for c in at.caption)
    assert f"top {len(df)} of 40 pending" in all_text


def test_status_page_candidate_table_trimmed_columns(db_engine, status_app, mocker):
    """Rank, ticker, momentum, strength, position size, projected status --
    the fields that answer "what will be bought tomorrow, and how it
    ranks against today's pool." No decide timestamps or resolution
    notes (2026-08-09 narrowing); Strength restored the same day after
    initially being dropped -- a qualitative read on today's pool is
    directly relevant to this question, not merely investigation detail."""
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=1.0)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 1.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert list(df.columns) == ["Rank", "Ticker", "Momentum", "Strength", "Sized Position", "Status"]


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


def test_status_page_candidate_queue_fully_deployed_note_shown_when_all_rejected(db_engine, status_app, mocker):
    """The real production edge case: every PENDING candidate projects
    PROJECTED_REJECT_CAPACITY, none PROJECTED_ADMIT -- must show an
    explicit note that the account is fully deployed, so this doesn't
    read as a broken projection."""
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=1.0)
    _seed_pending_row(db_engine, ticker="MSFT", momentum_pct=2.0)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 1.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_REJECT_CAPACITY"},
        {"ticker": "MSFT", "momentum_pct": 2.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_REJECT_CAPACITY"},
    ])

    at = status_app.run()

    assert not at.exception
    all_text = " ".join(c.value for c in at.caption)
    assert "fully deployed" in all_text.lower()


def test_status_page_candidate_queue_no_fully_deployed_note_when_some_admitted(db_engine, status_app, mocker):
    """The note must not fire when at least one candidate is genuinely
    projected to be admitted -- only the all-rejected edge case."""
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=1.0)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 1.0, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = status_app.run()

    assert not at.exception
    all_text = " ".join(c.value for c in at.caption)
    assert "fully deployed" not in all_text.lower()


def test_status_page_candidate_queue_render_path_never_calls_t212_or_yfinance(db_engine, status_app, mocker):
    """The decisive render-path proof (2026-08-09), matching the sector-
    breakdown/crisis-status/open-position-cards tests' own shape: patches
    BOTH the Trading212 client and yfinance to raise if invoked at all
    while the Candidate Queue renders, with a real PENDING row and stored
    T212 snapshot seeded so the section has something to actually render."""
    from data.database import set_metadata
    set_metadata("t212_account_snapshot", '{"totalValue": 10000.0, '
                  '"cash": {"availableToTrade": 10000.0}, "investments": {"currentValue": 0.0}}')
    set_metadata("t212_snapshot_fetched_at", datetime.utcnow().isoformat())
    _seed_pending_row(db_engine, ticker="AAPL", momentum_pct=1.0)

    mocker.patch(
        "brokers.trading212.Trading212Client",
        side_effect=AssertionError("Candidate Queue must never construct a Trading212Client"),
    )
    mocker.patch(
        "yfinance.Ticker",
        side_effect=AssertionError("Candidate Queue must never call yfinance"),
    )

    at = status_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert "AAPL" in set(df["Ticker"])


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


# ---------------------------------------------------------------------------
# Regression: Status page must not clear other pages' st.cache_data caches
# (2026-08-09 fix). Before this fix, _render_status_page() called the blanket
# st.cache_data.clear() as its very first statement -- leftover from the
# removed manual "trigger pipeline now" buttons (which correctly scoped their
# own clear() calls inside their own button handlers; this one was never
# cleaned up when those buttons were removed). The blanket call wiped every
# @st.cache_data-decorated function's cache app-wide, not just Status's own,
# silently defeating the ttl= guard on every other cached page (most
# importantly the Dashboard's own yfinance-capable crisis-status cache and
# Status's own T212-account-summary-capable projection cache) every time a
# user visited Status.
# ---------------------------------------------------------------------------

def test_visiting_status_page_does_not_reset_dashboard_cache(db_engine, monkeypatch, mocker):
    """Navigate Dashboard -> Status -> Dashboard within ONE AppTest session
    (the real-world shape of the bug: Streamlit's st.cache_data is a single
    process-wide cache shared across every page, not per-page) and confirm a
    Dashboard-cached function is not re-invoked by the intervening Status
    visit -- it should still be within its own ttl=60 window."""
    import streamlit as st
    st.cache_data.clear()
    mocker.patch("data.scheduler.start_scheduler")
    monkeypatch.setattr("main.get_engine", lambda: db_engine)
    monkeypatch.setattr("data.ingestion.get_all_watchlist_entries", lambda config: [])

    call_count = {"n": 0}
    real_summary = {
        "total_value_gbp": 0.0, "cash_gbp": 0.0,
        "invested_gbp": 0.0, "deployed_pct": 0.0,
    }

    def _counting_summary():
        call_count["n"] += 1
        return real_summary

    mocker.patch("output.performance.get_current_account_summary", side_effect=_counting_summary)

    at = AppTest.from_file(_MAIN_PY, default_timeout=30)
    at.session_state["auth_user"] = _ADMIN_SESSION

    at.session_state["page"] = "dashboard"
    at.run()
    assert not at.exception
    assert call_count["n"] == 1, "first Dashboard render should call the (uncached) function once"

    at.session_state["page"] = "status"
    at.run()
    assert not at.exception
    # Status does not itself call get_current_account_summary(), so this
    # must remain exactly 1 -- proves Status's own render didn't touch it.
    assert call_count["n"] == 1

    at.session_state["page"] = "dashboard"
    at.run()
    assert not at.exception
    # THE regression check: before the fix, Status's blanket
    # st.cache_data.clear() would have wiped Dashboard's cache, forcing
    # this second Dashboard render to call the function again (n == 2).
    # With the fix, the ttl=60 cache from the first render is still valid.
    assert call_count["n"] == 1, (
        "Dashboard's cached function was re-invoked after visiting Status -- "
        "st.cache_data.clear() is defeating the cache again"
    )


def test_no_blanket_cache_clear_on_status_page():
    """Cheap structural guard, in addition to the behavioural test above:
    the Status page's render function must never call the blanket
    st.cache_data.clear()/st.cache_resource.clear() -- if a future change
    reintroduces it (even scoped differently), this fails loudly and
    immediately rather than only being caught by the slower AppTest above."""
    import inspect
    import main

    source = inspect.getsource(main._render_status_page)
    assert "cache_data.clear()" not in source
    assert "cache_resource.clear()" not in source


# ---------------------------------------------------------------------------
# Log Viewer download buttons (2026-08-09 fix) -- the "Last 24 h"/"Last 7
# days" downloads used to run their journalctl subprocess call unconditionally
# on every render (st.download_button's `data=` has no lazy/callable form, so
# feeding it directly from a fresh subprocess call meant that call fired
# every time the section rendered, not only on click -- looked gated, wasn't).
# Fixed to a two-step prepare-then-download flow: the subprocess call now
# only runs inside an explicit st.button(...) click handler.
# ---------------------------------------------------------------------------

_SAMPLE_LOG_LINE = "2026-08-09T12:00:00+01:00 host investment-research[1234]: INFO: something happened\n"


def _mock_journalctl(mocker, since_24h_output="24h log data", since_7d_output="7d log data"):
    """Mocks subprocess.run so the initial -n 200 fetch (which gates whether
    the whole Log Viewer section renders at all) succeeds with one real
    line, while tracking every JOURNALCTL call's args so tests can assert on
    which ones actually fired. The Status page also shells out to `git log`
    for its deploy-info card (main.py's _git_log_info()) -- that call is
    let through with a harmless empty result rather than tracked, since it's
    unrelated to this fix and not itself gated by anything being tested here."""
    calls = []

    def _fake_run(args, **kwargs):
        if args and args[0] != "journalctl":
            result = mocker.MagicMock()
            result.returncode = 1
            result.stdout = ""
            return result
        calls.append(args)
        result = mocker.MagicMock()
        result.returncode = 0
        if "-n" in args:
            result.stdout = _SAMPLE_LOG_LINE
        elif "24 hours ago" in args:
            result.stdout = since_24h_output
        elif "7 days ago" in args:
            result.stdout = since_7d_output
        else:
            result.stdout = ""
        return result

    mocker.patch("main.subprocess.run", side_effect=_fake_run)
    return calls


def test_log_viewer_renders_without_calling_journalctl_since_downloads(status_app, mocker):
    """Rendering the Status page (with the Log Viewer section populated)
    must call journalctl exactly once -- the initial -n 200 fetch needed
    to show the viewer itself -- and must NOT call the --since 24h/7d
    variants at all until their own Prepare button is clicked."""
    calls = _mock_journalctl(mocker)

    at = status_app.run()

    assert not at.exception
    assert len(calls) == 1, f"expected exactly 1 journalctl call (the initial fetch), got {len(calls)}: {calls}"
    assert "-n" in calls[0]
    assert not any("--since" in c for c in calls)


def test_log_viewer_prepare_24h_button_fetches_and_shows_download(status_app, mocker):
    """st.download_button isn't natively recognised by this Streamlit
    version's AppTest (renders as an UnknownElement, no dedicated
    accessor) -- verified via st.session_state instead, which is the same
    underlying data the download button reads from, so this is an
    equally direct proof, not a weaker proxy."""
    calls = _mock_journalctl(mocker, since_24h_output="the real 24h data")

    at = status_app.run()
    assert not at.exception
    assert "_log_dl_24h_data" not in at.session_state, (
        "prepared data must not exist before Prepare is clicked"
    )

    at.button(key="log_prep_24h").click().run()

    assert not at.exception
    assert any("24 hours ago" in c for c in calls), "Prepare click must trigger the --since 24-hours-ago fetch"
    assert any(c[:2] == ["journalctl", "-u"] for c in calls if "24 hours ago" in c)
    assert at.session_state["_log_dl_24h_data"] == "the real 24h data"


def test_log_viewer_prepare_7d_button_fetches_and_shows_download(status_app, mocker):
    calls = _mock_journalctl(mocker, since_7d_output="the real 7-day data")

    at = status_app.run()
    at.button(key="log_prep_7d").click().run()

    assert not at.exception
    assert any("7 days ago" in c for c in calls)
    assert at.session_state["_log_dl_7d_data"] == "the real 7-day data"


def test_log_viewer_24h_prepare_does_not_trigger_7d_fetch(status_app, mocker):
    """Each Prepare button must only fetch its own window -- clicking one
    must not incidentally trigger the other's journalctl call."""
    calls = _mock_journalctl(mocker)

    at = status_app.run()
    at.button(key="log_prep_24h").click().run()

    assert not at.exception
    assert any("24 hours ago" in c for c in calls)
    assert not any("7 days ago" in c for c in calls)
    assert "_log_dl_7d_data" not in at.session_state


def test_log_viewer_prepare_failure_shows_not_available_not_a_crash(status_app, mocker):
    def _failing_run(args, **kwargs):
        result = mocker.MagicMock()
        result.returncode = 0
        result.stdout = ""
        if args and args[0] != "journalctl":
            return result
        if "24 hours ago" in args:
            raise OSError("journalctl not found")
        result.stdout = _SAMPLE_LOG_LINE if "-n" in args else ""
        return result

    mocker.patch("main.subprocess.run", side_effect=_failing_run)

    at = status_app.run()
    at.button(key="log_prep_24h").click().run()

    assert not at.exception
    assert at.session_state["_log_dl_24h_data"] == "(not available)"
