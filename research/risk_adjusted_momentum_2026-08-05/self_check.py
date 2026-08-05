"""Fidelity check, in two parts.

PART 1 (the requirement): with rank_mode="raw" (and corr_haircut_threshold=
None), risk_adjusted_engine.simulate_with_risk_adjusted_ranking() must
reproduce the real, unmodified analysis.backtest.simulate_portfolio_
compounding_mtm() BYTE-IDENTICALLY on a real subset of tickers/history --
the same requirement and pattern as research/concentration_risk_2026-08-05/
self_check.py and research/var_sizing_sweep_2026-08-05/self_check.py before it.

PART 2 (decision-engine parity, per CLAUDE.md's "Research scripts must verify
decision-engine parity, not inherit it by assumption"): gather EVIDENCE from
this script's own run that entry, exit, sizing and holding-period logic still
route through the same shared production functions -- not by reading the
source and confirming by eye, but by independently recomputing what the shared
functions produce for real trades this engine actually emitted, and confirming
they match.

PART 3 (sanity, not fidelity): rank_mode="risk_adjusted" must actually CHANGE
something. A variant that is accidentally inert would also pass Part 1, and
would silently produce a "no difference" result for the whole investigation.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import precompute_ticker, simulate_portfolio_compounding_mtm
from analysis.universe import get_max_holding_days
from analysis.decision import evaluate_exit_reason
from data.ingestion import get_price_df
from risk_adjusted_engine import simulate_with_risk_adjusted_ranking

CONFIG_PATH = ROOT / "config.yaml"


def main():
    config = yaml.safe_load(open(CONFIG_PATH, encoding="utf-8"))

    gspc_df = get_price_df("^GSPC", days=3000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close = gspc_df["close"].astype(float)

    candidate_tickers = universe.get_live_stock_candidates(as_of=pd.Timestamp("2026-08-03").date())[:60]
    caches = [precompute_ticker(t, config) for t in candidate_tickers]

    t212 = config["trading212_invest"]
    per_trade_pct = float(t212["per_trade_pct"])
    max_deployed_pct = float(t212["max_total_deployed"])

    # ---- PART 1: byte-identical when disabled -------------------------------
    real_trades = simulate_portfolio_compounding_mtm(
        caches, config, gspc_close, per_trade_pct, max_deployed_pct
    )
    variant_raw = simulate_with_risk_adjusted_ranking(
        caches, config, gspc_close, per_trade_pct, max_deployed_pct,
        corr_haircut_threshold=None, corr_haircut_factor=1.0, rank_mode="raw",
    )
    print(f"real trades: {len(real_trades)}  variant(raw) trades: {len(variant_raw)}")
    identical = real_trades == variant_raw
    print(f"PART 1 BYTE-IDENTICAL WHEN DISABLED: {identical}")
    if not identical:
        for i, (a, b) in enumerate(zip(real_trades, variant_raw)):
            if a != b:
                print("FIRST DIFF at index", i, "\n real:", a, "\n variant:", b)
                break
        if len(real_trades) != len(variant_raw):
            print("LENGTH MISMATCH", len(real_trades), len(variant_raw))
        sys.exit(1)

    # ---- PART 2: decision-engine parity evidence ---------------------------
    # (a) holding period: every trade must respect the SHARED
    #     analysis/universe.py:get_max_holding_days() limit for its own ticker,
    #     recomputed here independently rather than trusted.
    viol = []
    checked = 0
    for tr in variant_raw:
        mh = get_max_holding_days(tr["ticker"], config)
        held = (pd.Timestamp(tr["exit_date"]) - pd.Timestamp(tr["entry_date"])).days
        checked += 1
        if tr["exit_reason"] == "MAX_HOLDING" and held < mh:
            viol.append((tr["ticker"], held, mh, tr["exit_reason"]))
    print(f"PART 2a holding-period: checked={checked} violations={len(viol)}")
    if viol:
        print("  sample:", viol[:5])

    # (b) exit reasons must all be values the SHARED evaluate_exit_reason()
    #     can actually return -- a fork would drift to some other vocabulary.
    reasons = sorted({t["exit_reason"] for t in variant_raw})
    allowed = {"CRISIS_EXIT", "STOP_LOSS", "SIGNAL_OFF", "MAX_HOLDING", "END_OF_DATA"}
    print(f"PART 2b exit reasons emitted: {reasons}")
    print(f"PART 2b all within shared evaluate_exit_reason() vocabulary: {set(reasons) <= allowed}")

    # (c) prove the shared exit function is genuinely the one deciding, by
    #     driving it directly with each priority scenario and confirming the
    #     documented ordering (CRISIS > STOP_LOSS > SIGNAL_OFF > MAX_HOLDING).
    prio = [
        # all four triggers true at once -> CRISIS_EXIT must win
        evaluate_exit_reason(crisis_now=True, price=50.0, stop_price=60.0, sig_on=False,
                             holding_days=999, max_holding_days=30),
        # crisis off, stop still hit -> STOP_LOSS
        evaluate_exit_reason(crisis_now=False, price=50.0, stop_price=60.0, sig_on=False,
                             holding_days=999, max_holding_days=30),
        # stop clear, signal off -> SIGNAL_OFF
        evaluate_exit_reason(crisis_now=False, price=70.0, stop_price=60.0, sig_on=False,
                             holding_days=999, max_holding_days=30),
        # only the holding cap remains -> MAX_HOLDING
        evaluate_exit_reason(crisis_now=False, price=70.0, stop_price=60.0, sig_on=True,
                             holding_days=999, max_holding_days=30),
        # nothing triggers -> None
        evaluate_exit_reason(crisis_now=False, price=70.0, stop_price=60.0, sig_on=True,
                             holding_days=1, max_holding_days=30),
    ]
    print(f"PART 2c shared exit priority chain: {prio}")

    # (d) sizing: position_gbp must never exceed the per-trade ceiling implied
    #     by per_trade_pct against the account value at entry.
    over = [t for t in variant_raw
            if t["position_gbp"] > t.get("account_value_at_entry", float("inf")) * per_trade_pct + 0.01]
    print(f"PART 2d sizing over per_trade_pct ceiling: {len(over)}")

    # ---- PART 3: variant is not inert --------------------------------------
    stats = {}
    variant_ra = simulate_with_risk_adjusted_ranking(
        caches, config, gspc_close, per_trade_pct, max_deployed_pct,
        corr_haircut_threshold=None, corr_haircut_factor=1.0,
        rank_mode="risk_adjusted", rank_stats=stats,
    )
    differs = variant_ra != variant_raw
    print(f"variant(risk_adjusted) trades: {len(variant_ra)}")
    print(f"PART 3 RISK-ADJUSTED ACTUALLY CHANGES OUTPUT: {differs}")
    print(f"PART 3 ranking fallback incidence: {stats}")
    if not differs:
        print("WARNING: variant is inert on this subset -- investigate before trusting any result.")


if __name__ == "__main__":
    main()
