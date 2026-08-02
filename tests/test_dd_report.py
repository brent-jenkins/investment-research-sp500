"""Tests for analysis/dd_report.py -- the V2 due diligence report generator.

Mirrors the "generated PDF is well-formed, text extraction confirms no
corruption" discipline V1's analysis/robustness.py test suite used before it
was deleted along with V1 (see CLAUDE.md's "Due diligence report accuracy
fixes" section for the exact `&`-entity bug this guards against).
"""
import pandas as pd
import pytest
from pypdf import PdfReader
import io

from analysis.dd_report import generate_dd_report_pdf


def _sample_trade(entry, exit_, pnl_pct, position_gbp=1000.0):
    return {
        "ticker": "VOO", "entry_date": entry, "exit_date": exit_,
        "pnl_pct": pnl_pct, "position_gbp": position_gbp,
        "pnl_gbp": position_gbp * pnl_pct,
    }


def _sample_result(with_trades=True, live_universe=False):
    blocks = []
    for i in range(16):
        blocks.append({
            "block": i, "start": f"20{7 + i:02d}-01-01", "end": f"20{8 + i:02d}-01-01",
            "n_trades": 10, "strat_cagr": 10.0 + i, "strat_dd": -20.0,
            "sp_cagr": 8.0, "sp_dd": -18.0, "passed": i != 1,
        })
    metrics_shape = {
        "total_trades": 3 if with_trades else 0, "win_rate": 0.5, "cagr_pct": 12.3,
        "max_drawdown_pct": -25.0,
    }
    trades = [
        _sample_trade("2019-01-01", "2019-06-01", 0.05),
        _sample_trade("2021-01-01", "2021-06-01", -0.02),
        _sample_trade("2022-01-01", "2022-06-01", 0.10),
    ] if with_trades else []
    # The dynamic-pool shape (2026-08-02) -- what analysis.validation.
    # run_full_validation(live_universe=True) actually returns, and what
    # main.py's Backtest page/DD report generation call always uses going
    # forward. The old frozen-universe shape (etf_count/stock_candidate_
    # count/...) is kept selectable via live_universe=False purely to
    # cover generate_dd_report_pdf()'s own backward-compatible branch --
    # nothing in production generates that shape any more.
    universe = (
        {
            "as_of": "2026-07-30", "candidate_universe_count": 1204,
            "live_eligible_count": 503, "excluded_count": 701,
            "excluded_sample": ["ENRON", "WCOM", "LEH"],
        } if live_universe else {
            "etf_count": 33, "stock_candidate_count": 40, "stock_member_count": 39,
            "non_members": ["GOLD"], "live_tradeable_count": 72, "total_candidates": 73,
        }
    )
    return {
        "start": "2007-01-01", "end": "2026-07-30", "starting_capital_gbp": 50000.0,
        "universe": universe,
        "live_universe": live_universe,
        "backtest_convention": "continuous_walk",
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
        # 2026-08-01 -- the default engine is now compounding_default,
        # sourced from trading212_invest.per_trade_pct / max_total_deployed
        # (5% / 50%, matching config.yaml's shipped defaults). See
        # test_generate_dd_report_pdf_position_sizing_text_reflects_engine
        # below.
        "engine_used": "compounding_default",
        "deployment_basis": "mark_to_market" if live_universe else "cost_basis",
        "per_trade_pct": 0.05,
        "max_deployed_pct": 0.5,
        # Deliberately distinctive numbers, not values a real computation
        # would plausibly produce -- shared verbatim with
        # tests/test_main_ui.py's own sample result. If either consumer
        # ever started recomputing this figure instead of reading it from
        # `result`, these specific numbers would stop appearing in its
        # output and the corresponding test would catch it.
        "gbp_growth": {
            "starting_capital_gbp": 50000.0,
            "train": {"strategy_ending_gbp": 71234.56, "sp500_ending_gbp": 65432.10},
            "test": {"strategy_ending_gbp": 88765.43, "sp500_ending_gbp": 54321.98},
        },
        "trades": trades,
    }


def test_generate_dd_report_pdf_well_formed():
    pdf_bytes = generate_dd_report_pdf(_sample_result(), {})
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000


def test_generate_dd_report_pdf_with_no_trades():
    pdf_bytes = generate_dd_report_pdf(_sample_result(with_trades=False), {})
    assert pdf_bytes.startswith(b"%PDF-")


def test_generate_dd_report_pdf_no_ampersand_entity_corruption():
    """The literal-'&'-followed-by-a-letter ReportLab Paragraph bug (see
    CLAUDE.md's "Due diligence report accuracy fixes" section) must not
    reappear -- every 'S&P' in body text must render as 'S&P', not 'S&P;'."""
    pdf_bytes = generate_dd_report_pdf(_sample_result(), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "S&P;" not in text
    assert "S&P 500" in text


def test_generate_dd_report_pdf_contains_known_limitations():
    pdf_bytes = generate_dd_report_pdf(_sample_result(), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "Known Limitations" in text
    assert "GFC" in text


def test_generate_dd_report_pdf_shows_block_pass_fail_counts():
    pdf_bytes = generate_dd_report_pdf(_sample_result(), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "15" in text and "16" in text


def test_generate_dd_report_pdf_position_sizing_text_reflects_engine():
    """The 'Position Sizing, Portfolio Constraints & Exits' section must
    state the ACTUAL per_trade_pct/max_deployed_pct the run used (read from
    result, never recomputed or hardcoded) for the compounding engine, and
    fall back to the old static (no-percentage) wording for a result
    generated by one of the legacy engines -- proving the report never
    misdescribes which methodology actually produced the figures it's
    reporting."""
    result = _sample_result()
    pdf_bytes = generate_dd_report_pdf(result, {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "5%" in text
    assert "50%" in text
    assert "current value" in text.lower()

    legacy_result = dict(result)
    legacy_result["engine_used"] = "uncapped"
    legacy_pdf_bytes = generate_dd_report_pdf(legacy_result, {})
    legacy_reader = PdfReader(io.BytesIO(legacy_pdf_bytes))
    # Normalise whitespace -- PDF text extraction can insert a line-wrap
    # newline between two words that are adjacent in the source text.
    legacy_text = " ".join(p.extract_text() for p in legacy_reader.pages).replace("\n", " ")
    assert "reused unmodified" in " ".join(legacy_text.split())


def test_generate_dd_report_pdf_shows_gbp_growth_from_result_not_recomputed():
    """The £50k V2-vs-S&P comparison must come straight from
    result['gbp_growth'] -- proven by using deliberately distinctive
    numbers no real computation would produce and confirming they appear
    verbatim (rounded to the nearest pound, matching the report's own
    f"£{...:,.0f}" formatting) in the rendered PDF."""
    pdf_bytes = generate_dd_report_pdf(_sample_result(), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "71,235" in text  # train strategy ending, rounded
    assert "65,432" in text  # train S&P ending, rounded
    assert "88,765" in text  # test strategy ending, rounded
    assert "54,322" in text  # test S&P ending, rounded


# ---------------------------------------------------------------------------
# live_universe=True shape (2026-08-02) -- the dynamic S&P 500 candidate
# pool, no ETFs, mark-to-market, continuous-walk. This is what production
# ALWAYS generates going forward (see CLAUDE.md's "rebuild the
# backtest/DD-report page to genuinely run against the current, real live
# engine"); the old frozen-universe shape covered above remains supported
# purely for backward compatibility, not because anything still produces it.
# ---------------------------------------------------------------------------

def test_generate_dd_report_pdf_live_universe_well_formed():
    pdf_bytes = generate_dd_report_pdf(_sample_result(live_universe=True), {})
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000


def test_generate_dd_report_pdf_live_universe_states_no_etfs():
    pdf_bytes = generate_dd_report_pdf(_sample_result(live_universe=True), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = " ".join(p.extract_text() for p in reader.pages).replace("\n", " ")
    assert "No ETFs" in text
    assert "removed from live trading" in text


def test_generate_dd_report_pdf_live_universe_states_continuous_walk():
    pdf_bytes = generate_dd_report_pdf(_sample_result(live_universe=True), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = " ".join(p.extract_text() for p in reader.pages).replace("\n", " ")
    assert "continuous-walk" in text.lower()
    assert "reset-at-boundary" in text.lower()


def test_generate_dd_report_pdf_live_universe_shows_real_membership_figures():
    """The live-universe universe figures (candidate_universe_count/
    live_eligible_count/excluded_count) must appear, not the old frozen
    73-ticker/etf_count shape."""
    pdf_bytes = generate_dd_report_pdf(_sample_result(live_universe=True), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = " ".join(p.extract_text() for p in reader.pages).replace("\n", " ")
    assert "503" in text   # live_eligible_count
    assert "1204" in text or "1,204" in text  # candidate_universe_count
    assert "701" in text   # excluded_count


def test_generate_dd_report_pdf_live_universe_states_mark_to_market():
    pdf_bytes = generate_dd_report_pdf(_sample_result(live_universe=True), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = " ".join(p.extract_text() for p in reader.pages).replace("\n", " ")
    assert "mark-to-market" in text.lower()


def test_generate_dd_report_pdf_frozen_universe_states_cost_basis_not_mtm():
    """The old, non-live-universe path must keep stating cost-basis, never
    accidentally claim mark-to-market for figures that were never computed
    that way."""
    pdf_bytes = generate_dd_report_pdf(_sample_result(live_universe=False), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = " ".join(p.extract_text() for p in reader.pages).replace("\n", " ")
    assert "cost-basis" in text.lower()


def test_generate_dd_report_pdf_block_pass_criteria_text_matches_the_real_formula():
    """The block-pass explanation must state the ACTUAL reconciled formula
    (DD <= 1.5x S&P's own AND profit factor > 1.0) -- not the earlier,
    retracted reconstruction (a per-block CAGR gate and an invented -20%
    capital floor), which was wrong and has since been corrected in
    analysis/validation.py:_block_passes(). See CLAUDE.md's "Block
    pass/fail formula reconciliation" section."""
    pdf_bytes = generate_dd_report_pdf(_sample_result(), {})
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = " ".join(p.extract_text() for p in reader.pages).replace("\n", " ")
    assert "profit factor" in text.lower()
    assert "greater than 1.0" in text.lower()
    assert "20% of capital" not in text
