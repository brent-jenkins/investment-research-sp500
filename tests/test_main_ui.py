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
        "universe": {
            "etf_count": 33, "stock_candidate_count": 40, "stock_member_count": 39,
            "non_members": ["GOLD"], "live_tradeable_count": 72, "total_candidates": 73,
        },
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
