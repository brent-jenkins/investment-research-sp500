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


def _sample_result(with_trades=True):
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
