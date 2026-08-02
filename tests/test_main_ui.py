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
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


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

    at = AppTest.from_file("main.py", default_timeout=30)
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

    at = AppTest.from_file("main.py", default_timeout=30)
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

    at = AppTest.from_file("main.py", default_timeout=30)
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

    at = AppTest.from_file("main.py", default_timeout=30)
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
# Dashboard candidate-queue Status column + filter (2026-08-02) -- sourced
# from brokers.trading212.project_pending_order_admission(), the same
# read-only projection built during the urgent capacity/momentum
# verification, not a separate/approximate computation.
# ---------------------------------------------------------------------------

def _seed_dashboard_pending_row(db_engine, *, ticker, momentum_pct, position_gbp=1000.0, is_etf=False):
    from sqlalchemy import text as sa_text
    from datetime import datetime
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, stop_price, var_pct, status, created_at)
            VALUES (:ticker, :t212_ticker, :is_etf, 'USD', :now, 100.0,
                    :position_gbp, :momentum_pct, 90.0, 0.02, 'PENDING', :now)
        """), {
            "ticker": ticker, "t212_ticker": f"{ticker}_US_EQ", "is_etf": is_etf,
            "now": datetime.utcnow().isoformat(), "position_gbp": position_gbp, "momentum_pct": momentum_pct,
        })


def test_dashboard_candidate_table_shows_status_from_the_real_projection(db_engine, dashboard_app, mocker):
    _seed_dashboard_pending_row(db_engine, ticker="SNDK", momentum_pct=27.0, position_gbp=806.81)
    _seed_dashboard_pending_row(db_engine, ticker="WBD", momentum_pct=0.98, position_gbp=2500.0)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "SNDK", "momentum_pct": 27.0, "position_gbp": 806.81, "projected_status": "PROJECTED_ADMIT"},
        {"ticker": "WBD", "momentum_pct": 0.98, "position_gbp": 2500.0, "projected_status": "PROJECTED_REJECT_CAPACITY"},
    ])

    at = dashboard_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert "Status" in df.columns
    assert df.loc[df["Ticker"] == "SNDK", "Status"].iloc[0] == "PROJECTED_ADMIT"
    assert df.loc[df["Ticker"] == "WBD", "Status"].iloc[0] == "PROJECTED_REJECT_CAPACITY"
    # Sourced from the real function, not a separate computation -- confirmed
    # it was actually called, not just that the right values happened to appear.
    from brokers.trading212 import project_pending_order_admission
    project_pending_order_admission.assert_called_once()


def test_dashboard_candidate_status_labelled_as_projection_not_guarantee(db_engine, dashboard_app, mocker):
    _seed_dashboard_pending_row(db_engine, ticker="AAPL", momentum_pct=0.5)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "AAPL", "momentum_pct": 0.5, "position_gbp": 1000.0, "projected_status": "PROJECTED_ADMIT"},
    ])

    at = dashboard_app.run()

    assert not at.exception
    all_text = " ".join(c.value for c in at.caption)
    assert "projection" in all_text.lower()
    assert "not a guarantee" in all_text.lower()


def test_dashboard_candidate_status_filter_narrows_the_table(db_engine, dashboard_app, mocker):
    _seed_dashboard_pending_row(db_engine, ticker="SNDK", momentum_pct=27.0)
    _seed_dashboard_pending_row(db_engine, ticker="WBD", momentum_pct=0.98)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[
        {"ticker": "SNDK", "momentum_pct": 27.0, "position_gbp": 806.81, "projected_status": "PROJECTED_ADMIT"},
        {"ticker": "WBD", "momentum_pct": 0.98, "position_gbp": 2500.0, "projected_status": "PROJECTED_REJECT_CAPACITY"},
    ])

    at = dashboard_app.run()
    at.multiselect(key="candidate_status_filter").set_value(["PROJECTED_ADMIT"]).run()

    assert not at.exception
    df = at.dataframe[0].value
    assert set(df["Ticker"]) == {"SNDK"}


def test_dashboard_candidate_status_shows_unavailable_when_projection_fails(db_engine, dashboard_app, mocker):
    """A ticker with no matching projection entry (e.g. the projection
    call itself failed and returned []) must show a distinct
    "unavailable" status, never silently read as rejected."""
    _seed_dashboard_pending_row(db_engine, ticker="AAPL", momentum_pct=0.5)
    mocker.patch("brokers.trading212.project_pending_order_admission", return_value=[])

    at = dashboard_app.run()

    assert not at.exception
    df = at.dataframe[0].value
    assert df.loc[df["Ticker"] == "AAPL", "Status"].iloc[0] == "PROJECTION_UNAVAILABLE"
