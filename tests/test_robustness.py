"""Tests for analysis/robustness.py — the Robustness page's due-diligence
computations (parameter-freeze tracking, winner concentration, expectancy,
Calmar ratios, out-of-sample stats, and sector/asset-class attribution).

Unlike analysis/backtest_metrics.py's pure-pandas functions, every compute_*()
function here reads a run's backtest_trades rows (and, for out-of-sample
stats, t212_pnl) via analysis.backtest.get_run()/get_run_trades() — so these
tests seed rows into the db_engine fixture's in-memory database rather than
building DataFrames directly.
"""

from datetime import date, timedelta
from io import BytesIO

from sqlalchemy.orm import Session

from analysis.robustness import (
    BACKTEST_START_DATE,
    PARAMETER_FREEZE_DATE,
    _recovery_days_to_calendar_months,
    _render_equity_curve_png,
    _ticker_sector_map,
    compute_attribution,
    compute_calmar_ratios,
    compute_expectancy,
    compute_live_validation,
    compute_out_of_sample_stats,
    compute_walk_forward_summary,
    compute_winner_concentration,
    generate_robustness_pdf,
)
from data.database import BacktestRun, BacktestTrade, T212Pnl

_PDF_TEST_CONFIG = {
    "watchlist": {
        "us_large_cap": [{"ticker": "AAPL", "name": "Apple"}],
        "sector_etfs":  [{"ticker": "XLK", "name": "Tech ETF"}],
    },
    "backtest": {"starting_capital_gbp": 10000},
}


def _seed_run(engine, **overrides) -> int:
    defaults = dict(name="test-run", status="complete")
    defaults.update(overrides)
    with Session(engine) as session:
        run = BacktestRun(**defaults)
        session.add(run)
        session.commit()
        return run.id


def _seed_trade(engine, run_id: int, **overrides) -> None:
    defaults = dict(
        ticker="TEST", direction="LONG",
        entry_date=date(2024, 1, 1), exit_date=date(2024, 1, 10),
        entry_price=100.0, exit_price=110.0,
        entry_signal="STRONG_BUY", exit_reason="MAX_HOLDING",
        pnl_pct=0.10, holding_days=9, is_win=True,
        regime_at_entry="TRENDING_BULL",
    )
    defaults.update(overrides)
    with Session(engine) as session:
        session.add(BacktestTrade(run_id=run_id, **defaults))
        session.commit()


# ---------------------------------------------------------------------------
# PARAMETER_FREEZE_DATE
# ---------------------------------------------------------------------------

def test_parameter_freeze_date_is_2026_07_13():
    assert PARAMETER_FREEZE_DATE == date(2026, 7, 13)


def test_backtest_start_date_precedes_freeze_date():
    assert BACKTEST_START_DATE < PARAMETER_FREEZE_DATE


# ---------------------------------------------------------------------------
# compute_winner_concentration()
# ---------------------------------------------------------------------------

def test_compute_winner_concentration_identifies_top_5_10_20(db_engine):
    run_id = _seed_run(db_engine)

    winners = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]  # sums to 5.5
    for i, pnl in enumerate(winners):
        _seed_trade(
            db_engine, run_id, ticker=f"W{i}", pnl_pct=pnl, is_win=True,
            entry_date=date(2024, 1, 1) + timedelta(days=i * 3),
            exit_date=date(2024, 1, 5) + timedelta(days=i * 3),
        )
    # A loser must not affect winner concentration at all.
    _seed_trade(
        db_engine, run_id, ticker="L1", pnl_pct=-0.5, is_win=False,
        entry_date=date(2024, 6, 1), exit_date=date(2024, 6, 5),
    )

    result = compute_winner_concentration(run_id)

    total = sum(winners)
    expected_top5  = round(sum(sorted(winners, reverse=True)[:5])  / total * 100, 1)
    expected_top10 = round(sum(sorted(winners, reverse=True)[:10]) / total * 100, 1)

    assert result["top5_pct"] == expected_top5
    assert result["top10_pct"] == expected_top10
    # Only 10 winners exist at all, so "top 20" is necessarily 100% of profit.
    assert result["top20_pct"] == 100.0
    assert result["top5_pct"] < result["top10_pct"] <= result["top20_pct"]


def test_compute_winner_concentration_empty_run_returns_zeros(db_engine):
    run_id = _seed_run(db_engine)
    result = compute_winner_concentration(run_id)
    assert result == {"top5_pct": 0.0, "top10_pct": 0.0, "top20_pct": 0.0}


# ---------------------------------------------------------------------------
# compute_expectancy()
# ---------------------------------------------------------------------------

def test_compute_expectancy_matches_formula(db_engine):
    """expectancy = (avg_win * win_rate) - (avg_loss * loss_rate), with
    avg_loss taken as a positive magnitude (compute_metrics()'s avg_loss_pct
    is negative, so compute_expectancy() must abs() it before combining —
    this test would fail with the wrong sign if that abs() were removed)."""
    run_id = _seed_run(db_engine)

    # 3 wins of +10%, 2 losses of -5%.
    for i in range(3):
        _seed_trade(
            db_engine, run_id, ticker=f"W{i}", pnl_pct=0.10, is_win=True,
            entry_date=date(2024, 1, 1 + i), exit_date=date(2024, 1, 5 + i),
        )
    for i in range(2):
        _seed_trade(
            db_engine, run_id, ticker=f"L{i}", pnl_pct=-0.05, is_win=False,
            entry_date=date(2024, 2, 1 + i), exit_date=date(2024, 2, 5 + i),
        )

    result = compute_expectancy(run_id)

    win_rate, loss_rate = 3 / 5, 2 / 5
    avg_win_pct, avg_loss_pct = 10.0, 5.0  # magnitudes, in percentage points
    expected_pct = (avg_win_pct * win_rate) - (avg_loss_pct * loss_rate)

    assert result["expectancy_pct"] == round(expected_pct, 3)
    assert result["win_rate_pct"] == 60.0


def test_compute_expectancy_empty_run_returns_zero(db_engine):
    run_id = _seed_run(db_engine)
    result = compute_expectancy(run_id)
    assert result["expectancy_pct"] == 0.0
    assert result["expectancy_gbp"] == 0.0


# ---------------------------------------------------------------------------
# compute_calmar_ratios()
# ---------------------------------------------------------------------------

def test_compute_calmar_ratios_lifetime_matches_cagr_over_max_drawdown(db_engine):
    """The lifetime Calmar figure must always equal CAGR / |max drawdown| —
    checked by recomputing that ratio from the function's own reported
    cagr_pct/max_dd_pct rather than asserting hardcoded numbers, so the test
    validates the relationship regardless of the exact equity-curve values."""
    run_id = _seed_run(db_engine)

    # A winner establishes a peak, then a loser guarantees a real drawdown
    # below that peak so max_dd_pct is negative (not the empty/zero branch).
    _seed_trade(
        db_engine, run_id, ticker="W1", pnl_pct=0.20, is_win=True,
        entry_date=date(2023, 1, 1), exit_date=date(2023, 6, 1),
    )
    _seed_trade(
        db_engine, run_id, ticker="L1", pnl_pct=-0.05, is_win=False,
        entry_date=date(2023, 6, 2), exit_date=date(2024, 1, 1),
    )

    result = compute_calmar_ratios(run_id)
    lifetime = result["lifetime"]

    assert lifetime["max_dd_pct"] < 0
    expected_calmar = round(lifetime["cagr_pct"] / abs(lifetime["max_dd_pct"]), 2)
    assert lifetime["calmar"] == expected_calmar


def test_compute_calmar_ratios_empty_run_returns_zeroed_variants(db_engine):
    run_id = _seed_run(db_engine)
    result = compute_calmar_ratios(run_id)
    for variant in ("lifetime", "rolling_36m", "rolling_12m"):
        assert result[variant] == {"cagr_pct": 0.0, "max_dd_pct": 0.0, "calmar": 0.0}


# ---------------------------------------------------------------------------
# compute_out_of_sample_stats()
# ---------------------------------------------------------------------------

def test_compute_out_of_sample_stats_zero_trades_before_any_t212_pnl_rows(db_engine):
    result = compute_out_of_sample_stats()
    assert result["n_trades"] == 0
    assert result["status"] == "grey"
    assert result["return_since_freeze_pct"] == 0.0
    assert result["pnl_since_freeze_gbp"] == 0.0
    assert result["freeze_date"] == PARAMETER_FREEZE_DATE


def test_compute_out_of_sample_stats_ignores_rows_before_freeze_date(db_engine):
    """A t212_pnl row that closed before the freeze date is in-sample, not
    out-of-sample, and must not be counted here even though it exists."""
    with Session(db_engine) as session:
        session.add(T212Pnl(
            ticker="AAPL", buy_order_id=1, sell_order_id=2,
            entry_date=PARAMETER_FREEZE_DATE - timedelta(days=30),
            exit_date=PARAMETER_FREEZE_DATE - timedelta(days=1),
            pnl_pct=0.05, pnl_gbp=50.0,
        ))
        session.commit()

    result = compute_out_of_sample_stats()
    assert result["n_trades"] == 0
    assert result["status"] == "grey"


def test_compute_out_of_sample_stats_counts_rows_after_freeze_date(db_engine):
    with Session(db_engine) as session:
        session.add(T212Pnl(
            ticker="AAPL", buy_order_id=1, sell_order_id=2,
            entry_date=PARAMETER_FREEZE_DATE + timedelta(days=1),
            exit_date=PARAMETER_FREEZE_DATE + timedelta(days=10),
            pnl_pct=0.05, pnl_gbp=50.0,
        ))
        session.commit()

    result = compute_out_of_sample_stats()
    assert result["n_trades"] == 1
    assert result["status"] == "green"
    assert result["return_since_freeze_pct"] == 5.0
    assert result["pnl_since_freeze_gbp"] == 50.0


# ---------------------------------------------------------------------------
# _ticker_sector_map() / compute_attribution()
# ---------------------------------------------------------------------------

def test_ticker_sector_map_uses_hardcoded_sectors_for_us_large_cap():
    config = {
        "watchlist": {
            "us_large_cap": [
                {"ticker": "AAPL", "name": "Apple"},
                {"ticker": "JPM",  "name": "JP Morgan"},
            ],
            "sector_etfs": [
                {"ticker": "XLK", "name": "Tech ETF"},
            ],
        }
    }
    sectors = _ticker_sector_map(config)
    assert sectors["AAPL"] == "Technology"
    assert sectors["JPM"] == "Financials"
    # Non-us_large_cap tickers fall back to their watchlist group name.
    assert sectors["XLK"] == "Sector Etfs"


def test_compute_attribution_maps_tickers_to_sectors_via_watchlist_config(db_engine):
    run_id = _seed_run(db_engine)
    _seed_trade(
        db_engine, run_id, ticker="AAPL", pnl_pct=0.10, is_win=True,
        entry_date=date(2024, 1, 1), exit_date=date(2024, 1, 5),
    )
    _seed_trade(
        db_engine, run_id, ticker="XLK", pnl_pct=0.05, is_win=True,
        entry_date=date(2024, 2, 1), exit_date=date(2024, 2, 5),
    )

    config = {
        "watchlist": {
            "us_large_cap": [{"ticker": "AAPL", "name": "Apple"}],
            "sector_etfs":  [{"ticker": "XLK", "name": "Tech ETF"}],
        }
    }

    result = compute_attribution(run_id, config, source="historical")

    assert result["available"] is True
    sector_names = set(result["sector_table"]["name"])
    assert "Technology" in sector_names
    assert "Sector Etfs" in sector_names


def test_compute_attribution_no_data_returns_unavailable(db_engine):
    run_id = _seed_run(db_engine)
    result = compute_attribution(run_id, {"watchlist": {}}, source="historical")
    assert result["available"] is False


def test_compute_attribution_live_source_reads_t212_pnl(db_engine):
    run_id = _seed_run(db_engine)
    with Session(db_engine) as session:
        session.add(T212Pnl(
            ticker="AAPL", buy_order_id=1, sell_order_id=2,
            entry_date=date(2026, 1, 1), exit_date=date(2026, 1, 10),
            pnl_pct=0.08, pnl_gbp=80.0,
        ))
        session.commit()

    config = {"watchlist": {"us_large_cap": [{"ticker": "AAPL", "name": "Apple"}]}}
    result = compute_attribution(run_id, config, source="live")

    assert result["available"] is True
    assert "Technology" in set(result["sector_table"]["name"])


# ---------------------------------------------------------------------------
# generate_robustness_pdf()
# ---------------------------------------------------------------------------

def test_generate_robustness_pdf_returns_valid_pdf_bytes(db_engine):
    run_id = _seed_run(db_engine, name="pdf-test-run")
    for i in range(5):
        _seed_trade(
            db_engine, run_id,
            ticker="AAPL" if i % 2 == 0 else "XLK",
            pnl_pct=0.05 if i % 2 == 0 else -0.02,
            pnl_gbp=50.0 if i % 2 == 0 else -20.0,
            is_win=(i % 2 == 0),
            exit_reason="MAX_HOLDING" if i % 2 == 0 else "STOP_LOSS",
            entry_date=date(2024, 1, 1) + timedelta(days=i * 10),
            exit_date=date(2024, 1, 5) + timedelta(days=i * 10),
        )

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes[:5] == b"%PDF-"


def test_generate_robustness_pdf_includes_known_limitations_section(db_engine):
    """The Methodology & Disclaimers page's closing section documents known
    limitations and the improvement roadmap proactively — verify it renders
    into a well-formed PDF (header check) and that its heading text actually
    reaches the page, the same way the Live Validation page content checks
    below verify their own sections via pypdf text extraction."""
    run_id = _seed_run(db_engine, name="pdf-test-known-limitations")
    _seed_trade(db_engine, run_id, ticker="AAPL", pnl_pct=0.05, pnl_gbp=50.0, is_win=True)

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes[:5] == b"%PDF-"

    text = _extract_pdf_text(pdf_bytes)
    assert "Known Limitations" in text
    assert "Improvement Roadmap" in text
    assert "Founder" in text


def test_generate_robustness_pdf_handles_empty_t212_pnl_gracefully(db_engine):
    """A fresh DB has zero t212_pnl rows — the Out-of-Sample Monitor and the
    Attribution page's Live/Combined sources must render a placeholder
    ("no live trades yet") rather than raising, since this is the normal
    state for a long time after this feature ships (see CLAUDE.md's
    "Feedback learning" section on how slowly t212_pnl accumulates)."""
    run_id = _seed_run(db_engine, name="pdf-test-run-no-live")
    _seed_trade(db_engine, run_id, ticker="AAPL", pnl_pct=0.05, pnl_gbp=50.0, is_win=True)

    with Session(db_engine) as session:
        assert session.query(T212Pnl).count() == 0  # the precondition this test exercises

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)

    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) > 1000


def test_generate_robustness_pdf_handles_run_with_no_trades_at_all(db_engine):
    """An empty run (e.g. a backtest over a ticker scope with no eligible
    entries) must still produce a valid PDF with placeholder text on every
    page that would otherwise show a table — not raise."""
    run_id = _seed_run(db_engine, name="pdf-test-run-empty")

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)

    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) > 0


# ---------------------------------------------------------------------------
# compute_walk_forward_summary() — Improvement 2
# ---------------------------------------------------------------------------

def test_compute_walk_forward_summary_counts_profitable_windows():
    import pandas as pd

    wf = pd.DataFrame({
        "train_start":        ["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01", "2020-05-01"],
        "train_end":          ["2020-06-30", "2020-07-31", "2020-08-31", "2020-09-30", "2020-10-31"],
        "test_trades":        [10, 12, 8, 15, 9],
        "test_win_rate_pct":  [50.0, 45.0, 60.0, 40.0, 55.0],
        "test_profit_factor": [1.5, 0.8, 2.1, 0.95, 1.0],  # 2 above 1.0, 1.0 itself not "above"
        "test_max_dd_pct":    [-10.0, -20.0, -5.0, -30.0, -12.0],
    })

    result = compute_walk_forward_summary(wf)

    assert result["n_total"] == 5
    assert result["n_positive"] == 2  # 1.5 and 2.1 are the only values > 1.0
    assert result["success_rate_pct"] == 40.0


def test_compute_walk_forward_summary_empty_input_returns_zeros():
    import pandas as pd
    result = compute_walk_forward_summary(pd.DataFrame())
    assert result == {"n_total": 0, "n_positive": 0, "success_rate_pct": 0.0}


# ---------------------------------------------------------------------------
# _recovery_days_to_calendar_months() — Improvement 3
# ---------------------------------------------------------------------------

def test_recovery_days_to_calendar_months_matches_spec_example():
    # 711 trading days / 21 trading-days-per-month ~= 33.857... -> 33.9
    assert _recovery_days_to_calendar_months(711) == 33.9


def test_recovery_days_to_calendar_months_zero_days_is_zero():
    assert _recovery_days_to_calendar_months(0) == 0.0


def test_recovery_days_to_calendar_months_rounds_to_one_decimal():
    assert _recovery_days_to_calendar_months(21) == 1.0
    assert _recovery_days_to_calendar_months(42) == 2.0
    assert _recovery_days_to_calendar_months(30) == round(30 / 21, 1)


# ---------------------------------------------------------------------------
# compute_live_validation() — Improvement 4
# ---------------------------------------------------------------------------

def test_compute_live_validation_zero_trades_before_any_post_freeze_rows(db_engine):
    result = compute_live_validation(run_id=1)
    assert result["n_trades"] == 0
    assert result["status"] == "grey"
    assert result["win_rate_pct"] is None
    assert result["sharpe_available"] is False


def test_compute_live_validation_computes_stats_from_post_freeze_rows(db_engine):
    with Session(db_engine) as session:
        session.add(T212Pnl(
            ticker="AAPL", buy_order_id=1, sell_order_id=2,
            entry_date=PARAMETER_FREEZE_DATE + timedelta(days=1),
            exit_date=PARAMETER_FREEZE_DATE + timedelta(days=10),
            pnl_pct=0.10, pnl_gbp=100.0, holding_days=9,
        ))
        session.add(T212Pnl(
            ticker="XLK", buy_order_id=3, sell_order_id=4,
            entry_date=PARAMETER_FREEZE_DATE + timedelta(days=15),
            exit_date=PARAMETER_FREEZE_DATE + timedelta(days=25),
            pnl_pct=-0.04, pnl_gbp=-40.0, holding_days=10,
        ))
        session.commit()

    result = compute_live_validation(run_id=1)

    assert result["n_trades"] == 2
    assert result["win_rate_pct"] == 50.0
    assert result["return_since_freeze_pct"] == 6.0  # (0.10 - 0.04) * 100
    assert result["status"] == "green"
    assert result["sharpe_available"] is False  # span well under 12 months


def test_compute_live_validation_sharpe_only_available_after_12_months(db_engine):
    with Session(db_engine) as session:
        session.add(T212Pnl(
            ticker="AAPL", buy_order_id=1, sell_order_id=2,
            entry_date=PARAMETER_FREEZE_DATE + timedelta(days=1),
            exit_date=PARAMETER_FREEZE_DATE + timedelta(days=400),
            pnl_pct=0.10, pnl_gbp=100.0, holding_days=399,
        ))
        session.add(T212Pnl(
            ticker="XLK", buy_order_id=3, sell_order_id=4,
            entry_date=PARAMETER_FREEZE_DATE + timedelta(days=2),
            exit_date=PARAMETER_FREEZE_DATE + timedelta(days=410),
            pnl_pct=-0.04, pnl_gbp=-40.0, holding_days=408,
        ))
        session.commit()

    result = compute_live_validation(run_id=1)
    assert result["sharpe_available"] is True


# ---------------------------------------------------------------------------
# Live Validation PDF page — Improvement 4 (content checks via pypdf)
# ---------------------------------------------------------------------------

def _extract_pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_live_validation_page_shows_placeholder_when_no_post_freeze_trades(db_engine):
    run_id = _seed_run(db_engine, name="pdf-live-empty")
    _seed_trade(db_engine, run_id, ticker="AAPL", pnl_pct=0.05, pnl_gbp=50.0, is_win=True)

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)
    text = _extract_pdf_text(pdf_bytes)

    assert "Live Validation" in text
    assert "No live trades have been completed" in text
    assert "Live vs In-Sample Comparison" not in text


def test_live_validation_page_shows_comparison_table_when_post_freeze_trades_exist(db_engine):
    run_id = _seed_run(db_engine, name="pdf-live-populated")
    _seed_trade(db_engine, run_id, ticker="AAPL", pnl_pct=0.05, pnl_gbp=50.0, is_win=True)

    with Session(db_engine) as session:
        session.add(T212Pnl(
            ticker="AAPL", buy_order_id=1, sell_order_id=2,
            entry_date=PARAMETER_FREEZE_DATE + timedelta(days=1),
            exit_date=PARAMETER_FREEZE_DATE + timedelta(days=10),
            pnl_pct=0.05, pnl_gbp=50.0, holding_days=9,
        ))
        session.commit()

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)
    text = _extract_pdf_text(pdf_bytes)

    assert "Live Validation" in text
    assert "Live vs In-Sample Comparison" in text
    assert "No live trades have been completed" not in text


# ---------------------------------------------------------------------------
# Due-diligence report accuracy fixes (2026-07-16) — flagged in external
# review of the 2026-07-14 report: an inaccurate "zero trades" chart label
# for windows that actually had carryover-position exits, an unexplained
# GBP P&L basis mismatch against the Expectancy section's flat-£50,000
# figure, and a rounding-induced mismatch in the displayed expectancy
# formula. See CLAUDE.md for the full investigation (including the real
# 2022 SOXX trade — entered 2021-11-04 while TRENDING_BULL, closed via
# stop-loss 2022-01-20 — used to verify these fixes against real data
# before they were applied).
# ---------------------------------------------------------------------------

def test_expectancy_pdf_displays_percentage_split_not_bare_fraction(db_engine):
    """The win/loss split must render as a percentage (e.g. "44.4%"), not a
    fraction rounded to 2dp (e.g. "0.44") — the fraction rounding was
    coarse enough that hand-multiplying the displayed formula produced a
    visibly different result than the headline expectancy figure."""
    run_id = _seed_run(db_engine, name="pdf-expectancy-precision")
    # 4 wins / 9 trades = 44.44...% — a split that doesn't round cleanly.
    for i in range(4):
        _seed_trade(db_engine, run_id, ticker=f"W{i}", pnl_pct=0.05, pnl_gbp=50.0,
                    is_win=True, entry_date=date(2024, 1, 1) + timedelta(days=i))
    for i in range(5):
        _seed_trade(db_engine, run_id, ticker=f"L{i}", pnl_pct=-0.02, pnl_gbp=-20.0,
                    is_win=False, entry_date=date(2024, 2, 1) + timedelta(days=i))

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)
    text = _extract_pdf_text(pdf_bytes)

    assert "44.4%" in text
    assert "55.6%" in text
    # The old fraction-rounded display would have shown "0.44" / "0.56".
    assert "x 0.44)" not in text
    assert "x 0.56)" not in text


def test_annual_returns_footnote_explains_gbp_pnl_basis(db_engine):
    """The Annual Returns table must carry a note clarifying that Return %
    and P&L (GBP) are each summed from individual trade outcomes, not
    computed against a fixed £50,000 account — otherwise a reader
    reasonably expects e.g. a 310% annual return to imply ~3x the account
    value in £ profit, which it does not."""
    run_id = _seed_run(db_engine, name="pdf-annual-footnote")
    _seed_trade(db_engine, run_id, ticker="AAPL", pnl_pct=0.10, pnl_gbp=100.0, is_win=True)

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)
    text = _extract_pdf_text(pdf_bytes)

    assert "VaR-sized position" in text
    assert "will not reconcile" in text
    # Confirms the ReportLab "&" + letter entity-parsing bug (documented in
    # CLAUDE.md) didn't resurface in this new paragraph.
    assert "P&L;" not in text
    assert "P&L (GBP)" in text


def test_equity_curve_carryover_note_for_trade_closing_in_covid_window(db_engine):
    """A trade opened before the COVID window (2020-02-01 to 2020-04-30)
    that closes inside it must not be silently absent from the "zero
    entries" finding — the on-chart annotation itself ("Zero new entries")
    is baked into the rendered image and can't be checked via text
    extraction, so this asserts directly against _render_equity_curve_png()'s
    returned carryover_notes (unit-level) and separately confirms the same
    text reaches the actual PDF (integration-level) — resolving the exact
    contradiction an external reviewer flagged against the Annual Returns
    table, where this same trade shows as "1 trade" for 2022... (here,
    2020, but the identical mechanism)."""
    run_id = _seed_run(db_engine, name="pdf-covid-carryover")
    _seed_trade(
        db_engine, run_id, ticker="AAPL",
        entry_date=date(2019, 12, 1), exit_date=date(2020, 3, 1),
        pnl_pct=-0.03, pnl_gbp=-30.0, is_win=False, exit_reason="STOP_LOSS",
    )

    result = _render_equity_curve_png(run_id, _PDF_TEST_CONFIG)
    assert result is not None
    _png_bytes, carryover_notes = result
    assert any("COVID crash" in n and "1 carryover position" in n for n in carryover_notes)

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)
    text = _extract_pdf_text(pdf_bytes)
    assert "regime gate blocked new entries throughout" in text
    assert "1 carryover position" in text


def test_equity_curve_no_carryover_mentioned_when_truly_no_trades_touch_window(db_engine):
    """A window with genuinely zero trades touching it (no entry, no exit)
    must not claim a carryover position that doesn't exist."""
    run_id = _seed_run(db_engine, name="pdf-covid-no-carryover")
    _seed_trade(db_engine, run_id, ticker="AAPL",
                entry_date=date(2019, 6, 1), exit_date=date(2019, 12, 1),
                pnl_pct=0.05, pnl_gbp=50.0, is_win=True)
    _seed_trade(db_engine, run_id, ticker="MSFT",
                entry_date=date(2020, 6, 1), exit_date=date(2020, 12, 1),
                pnl_pct=0.05, pnl_gbp=50.0, is_win=True)

    pdf_bytes = generate_robustness_pdf(run_id, _PDF_TEST_CONFIG)
    text = _extract_pdf_text(pdf_bytes)

    assert "regime gate blocked new entries throughout" in text
    assert "carryover position" not in text
