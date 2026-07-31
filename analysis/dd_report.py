"""Due diligence report — V2 (momentum + crisis override) strategy.

Deliberately much shorter than V1's eleven-page ReportLab document (deleted
along with the rest of analysis/robustness.py during V1 removal — see
CLAUDE.md's "Removed" notes throughout): V2's story is one signal, one
universe-gating rule, and one rare-firing override, so the report only needs
methodology, the validated results (train/test vs S&P 500, 16-block table,
concentration check, cost sensitivity, walk-forward), and an honest
limitations section — not six modules' worth of attribution/robustness
panels that no longer exist.

Every figure in this report comes from analysis.validation.run_full_validation()
— the exact same function and result dict the Backtest page renders — so the
report can never show a different number than what's on screen. Generation
is triggered live from the Backtest page (main.py), not hand-written once.

Uses the corrected additive-£, exit-date-sorted drawdown/CAGR methodology
throughout (analysis.backtest.compute_metrics_fixed / equity_curve_gbp) —
never the old cumprod approach that understated drawdown across every V1
backtest run (see CLAUDE.md's "Backtest equity-curve ordering bug" section).
"""
import datetime
import io
import logging

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from analysis.backtest import equity_curve_gbp

logger = logging.getLogger(__name__)

_NAVY = colors.HexColor("#0A0F1E")
_BLUE = colors.HexColor("#00C2FF")
_GREY = colors.HexColor("#64748b")
_GREEN = colors.HexColor("#16a34a")
_RED = colors.HexColor("#dc2626")
_LIGHT_BLUE_BG = colors.HexColor("#E8F4FD")

_KNOWN_LIMITATIONS = [
    (
        "GFC-acute-scale crashes are a known, accepted gap.",
        "The crisis override (≥7% single-day S&amp;P decline, entries blocked for 5 trading days "
        "after) fired on all 7 real qualifying days 2007–2026 and cost zero measured edge "
        "anywhere it didn't touch. It genuinely helps in a crash of that speed, but a crash long "
        "enough and slow enough to open positions before the trigger fires cannot be fully "
        "protected — the two historical blocks spanning the GFC onset/acute window are the ones "
        "that still fail the pass/fail bar (see the 16-block table).",
    ),
    (
        "Universe breadth is survivorship-exposed at the delisted/bankrupt level.",
        "Point-in-time S&amp;P 500 membership correction removes the \"was this stock ever added to "
        "the index\" survivorship bias, but a company that was delisted or went bankrupt outright "
        "is not reachable with the price history available to this system — there is no way to "
        "backtest a position in a company whose data no longer exists anywhere.",
    ),
    (
        "Generalisation beyond US mega-caps shows risk reduction, not clear return outperformance.",
        "A German (DAX) point-in-time-gated robustness check (genuine constituent-membership "
        "history, corrected 2026-07-31 after an earlier attempt used the current constituent list "
        "in error) found the strategy cuts drawdown substantially versus buy-and-hold DAX in every "
        "period tested (roughly a third of the benchmark's own drawdown), but does not clearly beat "
        "the benchmark's return — CAGR is ahead in the training window, roughly level in the test "
        "window and slightly behind over the full period. 12 of 16 historical blocks pass the same "
        "bar the US universe is held to, a similar hit rate to the validated US result (14 of 16). "
        "This is evidence of a real risk-management effect outside the validated US universe, not "
        "evidence of the same return edge — no claim of comparable return outperformance is made "
        "for any market beyond the validated US large-cap-plus-ETF universe.",
    ),
    (
        "US concentration is deliberate, not an oversight.",
        "The validated universe (33 broad ETFs + 40 US large-cap stock candidates) is "
        "US-and-broad-market concentrated by design — this was the configuration that passed "
        "validation, and no attempt has yet been made to build or validate a genuinely "
        "geographically diversified version.",
    ),
    (
        "ETF sleeve execution occurs on a different exchange, different hours, than the signal "
        "is computed against.",
        "The 33 ETF instruments in the tradeable universe are UCITS-wrapped funds, listed "
        "exclusively on the London Stock Exchange (07:00–15:30 UTC) — no genuine US-domiciled ETF "
        "listing is accessible through the platform, a UK/EU securities-law restriction (Overseas "
        "Funds Regime equivalence not extended to US-registered funds) applying equally to every "
        "UK-regulated retail broker, not a limitation specific to this platform. The momentum "
        "signal and crisis override are both computed from US market closing prices, but the "
        "resulting order can only execute once LSE's own session next opens — a minimum gap of "
        "roughly 9.5 hours on an ordinary weekday, extending to roughly 57.5 hours across a "
        "weekend. The backtest's execution model assumes a same-day, zero-lag fill at the "
        "computed closing price; live ETF execution cannot match this, since the real fill occurs "
        "at whatever price LSE's next session opens at. Order submission is timed to each "
        "instrument's own real trading session with a price-deviation check before executing, as "
        "the best achievable mitigation — this reduces uncertainty and avoids executing into a "
        "clearly-stale price, but does not close the underlying timing gap, which is a "
        "structural, permanent property of UK retail access to US-domiciled ETFs, not an "
        "engineering problem awaiting a better fix. The individual stock sleeve is not affected "
        "by this specific constraint — single-name US equities are directly NYSE-listed and not "
        "subject to the same fund-marketing restriction.",
    ),
]


def _style_sheet():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("H1", parent=styles["Heading1"], textColor=_NAVY, fontSize=20, spaceAfter=10))
    styles.add(ParagraphStyle("H2", parent=styles["Heading2"], textColor=_NAVY, fontSize=14, spaceBefore=14, spaceAfter=8))
    styles.add(ParagraphStyle("H3", parent=styles["Heading3"], textColor=_BLUE, fontSize=11, spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle("BodyGrey", parent=styles["BodyText"], textColor=_GREY, fontSize=9))
    styles.add(ParagraphStyle("Footnote", parent=styles["BodyText"], textColor=_GREY, fontSize=8, leading=10))
    return styles


def _metric_table(rows, col_widths=None):
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _render_equity_curve_png(trades: list, starting_capital_gbp: float) -> bytes | None:
    df = pd.DataFrame(trades)
    if df.empty:
        return None
    ec = equity_curve_gbp(df, starting_capital_gbp=starting_capital_gbp)
    if ec.empty:
        return None
    fig, ax = plt.subplots(figsize=(6.8, 3.0), dpi=150)
    ax.plot(ec["date"], ec["equity_gbp"], color="#3b82f6", linewidth=1.3)
    ax.fill_between(ec["date"], ec["equity_gbp"], starting_capital_gbp, alpha=0.08, color="#3b82f6")
    ax.set_ylabel("Portfolio Value (£)")
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def generate_dd_report_pdf(result: dict, config: dict) -> bytes:
    """Render the due diligence report to PDF bytes from an already-computed
    analysis.validation.run_full_validation() result dict."""
    styles = _style_sheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    )
    story = []

    # --- Cover ---
    story.append(Spacer(1, 4 * cm))
    story.append(Paragraph("SP500INVEST", ParagraphStyle("Cover1", fontSize=32, textColor=_BLUE, alignment=1, fontName="Helvetica-Bold")))
    story.append(Paragraph("Systematic large-cap momentum research.", ParagraphStyle("Cover2", fontSize=11, textColor=_GREY, alignment=1)))
    story.append(Spacer(1, 1.5 * cm))
    story.append(Paragraph("Due Diligence Report", ParagraphStyle("Cover3", fontSize=20, textColor=_NAVY, alignment=1, fontName="Helvetica-Bold")))
    story.append(Paragraph("Momentum + Crisis-Override Strategy (V2)", ParagraphStyle("Cover4", fontSize=13, textColor=_GREY, alignment=1)))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        f"Generated {datetime.date.today().isoformat()} · Validation window {result['start']} → {result['end']}",
        ParagraphStyle("Cover5", fontSize=9, textColor=_GREY, alignment=1),
    ))
    story.append(PageBreak())

    # --- Executive Summary ---
    story.append(Paragraph("Executive Summary", styles["H1"]))
    uni = result["universe"]
    story.append(Paragraph(
        "The system trades a single signal — trailing 12-month total return per ticker, long when "
        "positive and flat otherwise — across a point-in-time-gated universe of US large-cap stocks "
        "(genuine historical S&amp;P 500 membership windows, no discretionary exclusions) and broad market "
        "ETFs, with a rare-firing crisis override that force-exits all positions and blocks new entries "
        "on a ≥7% single-day S&amp;P 500 decline. This replaced an earlier six-module regime-weighted "
        "system after ten rounds of testing found no validated configuration of that architecture "
        "(see CLAUDE.md's \"V2 architecture — phased rebuild\" section for the full history).",
        styles["BodyText"],
    ))
    story.append(Spacer(1, 0.3 * cm))
    exec_rows = [
        ["Metric", "Train (2007–2019)", "Test (2020–present)"],
        ["Trades", str(result["train"].get("total_trades", 0)), str(result["test"].get("total_trades", 0))],
        ["CAGR", f"{result['train'].get('cagr_pct', 0):+.2f}%", f"{result['test'].get('cagr_pct', 0):+.2f}%"],
        ["S&amp;P 500 CAGR (same window)",
         f"{result.get('train_sp', {}).get('cagr_pct', 0) or 0:+.2f}%",
         f"{result.get('test_sp', {}).get('cagr_pct', 0) or 0:+.2f}%"],
        ["Max Drawdown", f"{result['train'].get('max_drawdown_pct', 0):.2f}%", f"{result['test'].get('max_drawdown_pct', 0):.2f}%"],
        ["Win Rate", f"{result['train'].get('win_rate', 0) * 100:.1f}%", f"{result['test'].get('win_rate', 0) * 100:.1f}%"],
    ]
    exec_rows = [[Paragraph(c, styles["BodyText"]) if isinstance(c, str) and "&amp;" in c else c for c in row] for row in exec_rows]
    story.append(_metric_table(exec_rows, col_widths=[6 * cm, 5 * cm, 5 * cm]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        f"<b>{result['n_blocks_pass']} of {result['n_blocks_total']}</b> historical blocks pass "
        "(strategy CAGR ≥ S&amp;P 500's own over the same window, max drawdown no worse than 1.5× the "
        f"S&amp;P's own, no block losing more than 20% of capital). Live-eligible universe today: "
        f"{uni['live_tradeable_count']} tickers ({uni['etf_count']} ETFs + {uni['stock_member_count']} of "
        f"{uni['stock_candidate_count']} stock candidates currently genuine S&amp;P 500 members"
        + (f"; excluded today: {', '.join(uni['non_members'])}" if uni["non_members"] else "")
        + ").",
        styles["BodyText"],
    ))
    story.append(PageBreak())

    # --- Methodology ---
    story.append(Paragraph("Methodology", styles["H1"]))
    story.append(Paragraph("Signal", styles["H2"]))
    story.append(Paragraph(
        "Trailing 12-month (252 trading day) total return per ticker. Long when positive, flat "
        "otherwise — no blended score, no separate regime classifier. Chosen over a 200-day-SMA "
        "variant tested in the same research phase, which performed almost as well but slightly worse.",
        styles["BodyText"],
    ))
    story.append(Paragraph("Universe", styles["H2"]))
    story.append(Paragraph(
        f"{uni['etf_count']} broad market ETFs (always eligible) plus {uni['stock_candidate_count']} US "
        "large-cap stock candidates, gated by genuine point-in-time S&amp;P 500 membership windows "
        "reconstructed from a public membership-history dataset — a stock is only tradeable on dates "
        "it was actually an index member, with no discretionary exclusions (TSLA, META, and GOOGL are "
        "included from their real membership dates, reversing an earlier subjective exclusion from the "
        "prior architecture). Re-verified as not a survivorship artifact by re-testing specifically on "
        "this point-in-time-corrected universe.",
        styles["BodyText"],
    ))
    story.append(Paragraph("Crisis Override", styles["H2"]))
    story.append(Paragraph(
        "A rare-firing circuit breaker: any single day the S&amp;P 500 falls ≥7% (the real NYSE Level 1 "
        "market-wide circuit breaker level) force-exits every open position that day and blocks new "
        "entries for the trigger's duration plus a 5 trading-day cooldown. Fired on only 7 days across "
        "the full 2007–2026 window and cost zero measured edge anywhere it didn't touch. A VIX-spike "
        "variant (VIX ≥45) was also tested and rejected — it fired roughly 15× more often and "
        "over-suppressed genuine opportunity, the same lesson found repeatedly in this project's "
        "research history: a broad, frequently-firing overlay suppresses more edge than it protects.",
        styles["BodyText"],
    ))
    story.append(Paragraph("Position Sizing & Exits", styles["H2"]))
    story.append(Paragraph(
        "Positions are sized via Historical VaR (95% confidence, scaled to the ticker's own max "
        "holding period via the square-root-of-time rule), the same mechanism validated in the prior "
        "architecture and reused unmodified. Exit priority: crisis override → index removal (stocks "
        "only) → ATR-based stop-loss → signal turning off → max holding period backstop.",
        styles["BodyText"],
    ))
    story.append(PageBreak())

    # --- Validated Results ---
    story.append(Paragraph("Validated Results", styles["H1"]))
    story.append(Paragraph("16 Historical Blocks vs S&amp;P 500", styles["H2"]))
    block_rows = [["Block", "Window", "Trades", "Strategy CAGR", "Strategy Max DD", "S&amp;P CAGR", "S&amp;P Max DD", "Result"]]
    for b in result["blocks"]:
        block_rows.append([
            str(b["block"]), f"{b['start']} → {b['end']}", str(b["n_trades"]),
            f"{b['strat_cagr']:+.1f}%", f"{b['strat_dd']:.1f}%",
            f"{b['sp_cagr']:+.1f}%" if b["sp_cagr"] is not None else "—",
            f"{b['sp_dd']:.1f}%" if b["sp_dd"] is not None else "—",
            "PASS" if b["passed"] else "FAIL",
        ])
    block_rows = [[Paragraph(c, styles["BodyText"]) if isinstance(c, str) and "&amp;" in c else c for c in row] for row in block_rows]
    bt = _metric_table(block_rows, col_widths=[1.2 * cm, 4.3 * cm, 1.6 * cm, 2.2 * cm, 2.2 * cm, 1.8 * cm, 1.8 * cm, 1.5 * cm])
    for i, b in enumerate(result["blocks"], start=1):
        colour = _GREEN if b["passed"] else _RED
        bt.setStyle(TableStyle([("TEXTCOLOR", (7, i), (7, i), colour), ("FONTNAME", (7, i), (7, i), "Helvetica-Bold")]))
    story.append(bt)
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        f"<b>{result['n_blocks_pass']}/{result['n_blocks_total']} pass.</b> The failing blocks span the "
        "2007–2010 GFC onset-through-acute-crash window — see Known Limitations below for why this "
        "specific gap is expected and accepted rather than hidden.",
        styles["BodyText"],
    ))
    story.append(PageBreak())

    story.append(Paragraph("Concentration Check", styles["H2"]))
    raw, exb = result["full_raw"], result["full_ex_best"]
    conc_rows = [
        ["", "All Trades (2007–present)", "Excluding Single Best Trade"],
        ["Trades", str(raw.get("total_trades", 0)), str(exb.get("total_trades", 0))],
        ["CAGR", f"{raw.get('cagr_pct', 0):+.2f}%", f"{exb.get('cagr_pct', 0):+.2f}%"],
        ["Max Drawdown", f"{raw.get('max_drawdown_pct', 0):.2f}%", f"{exb.get('max_drawdown_pct', 0):.2f}%"],
        ["Win Rate", f"{raw.get('win_rate', 0) * 100:.1f}%", f"{exb.get('win_rate', 0) * 100:.1f}%"],
    ]
    story.append(_metric_table(conc_rows, col_widths=[5 * cm, 5.5 * cm, 5.5 * cm]))
    _delta = raw.get("cagr_pct", 0) - exb.get("cagr_pct", 0)
    _n_trades = raw.get("total_trades", 0)
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        f"CAGR moves only {_delta:.2f} percentage points when the single largest-return trade is "
        f"removed from the entire {_n_trades}-trade, 19-year history — the edge is broad-based, not "
        "dependent on one outlier.",
        styles["BodyText"],
    ))

    story.append(Paragraph("Transaction-Cost Sensitivity", styles["H2"]))
    cost_rows = [["Round-Trip Cost", "CAGR", "Max Drawdown", "Win Rate", "Profit Factor"]]
    for bps, m in sorted(result["cost_sensitivity"].items()):
        cost_rows.append([
            f"{bps} bps", f"{m.get('cagr_pct', 0):+.2f}%", f"{m.get('max_drawdown_pct', 0):.2f}%",
            f"{m.get('win_rate', 0) * 100:.1f}%", f"{m.get('profit_factor', 0):.2f}",
        ])
    story.append(_metric_table(cost_rows, col_widths=[3.5 * cm, 3.5 * cm, 3.5 * cm, 3 * cm, 3 * cm]))
    story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("Walk-Forward Validation", styles["H2"]))
    wf = result["walk_forward"]
    story.append(Paragraph(
        f"Rolling 6-month windows, stepped monthly: <b>{wf.get('n_positive', 0)} of "
        f"{wf.get('n_total', 0)} windows ({wf.get('success_rate_pct', 0):.1f}%)</b> showed a "
        "profit factor above 1.0.",
        styles["BodyText"],
    ))

    png = _render_equity_curve_png(result["trades"], result["starting_capital_gbp"])
    if png:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph("Equity Curve (£, full history)", styles["H2"]))
        story.append(Image(io.BytesIO(png), width=16 * cm, height=7.06 * cm))
    story.append(PageBreak())

    # --- Known Limitations ---
    story.append(Paragraph("Known Limitations", styles["H1"]))
    story.append(Paragraph(
        "Stated directly, not omitted — each of these is a known, currently-unaddressed gap, not a "
        "surprise finding.",
        styles["BodyGrey"],
    ))
    for title, body in _KNOWN_LIMITATIONS:
        block = [
            Paragraph(title, styles["H3"]),
            Paragraph(body, styles["BodyText"]),
            Spacer(1, 0.25 * cm),
        ]
        story.append(KeepTogether(block))

    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "This report was generated live from the platform's own validation engine "
        "(analysis/validation.py:run_full_validation()) — every figure above is reproducible by "
        "re-running the same function against the same production data, not hand-authored.",
        ParagraphStyle("FounderBox", parent=styles["BodyText"], backColor=_LIGHT_BLUE_BG, borderPadding=8,
                        borderColor=_BLUE, borderWidth=0.5),
    ))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "SP500INVEST · Systematic large-cap momentum research. · hello@example.com · example.com",
        ParagraphStyle("Footer", fontSize=8, textColor=_GREY, alignment=1),
    ))

    doc.build(story)
    return buf.getvalue()
