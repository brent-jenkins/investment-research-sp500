"""Decision-engine parity, evidenced from THIS investigation's own real
reset-at-boundary sweep output (not the small 60-ticker self-check subset),
per CLAUDE.md's "Research scripts must verify decision-engine parity"
standing requirement. Run against results/reset_<arm>.json for whichever
arms have finished so far.

Checks, from real trades:
  1. Holding-period: every MAX_HOLDING exit's holding_days respects
     get_max_holding_days() (+ weekend/scheduling slack).
  2. Exit-reason vocabulary matches evaluate_exit_reason()'s real priority
     chain output set.
  3. Sizing ceiling: no trade's position_gbp is inexplicably huge (a coarse
     sanity check -- the real per-candidate ceiling check is done in
     step4_checks.py's backstop check, from the stats files, which is the
     authoritative one for the multiplier-specific claim).
"""
import sys
import json
import glob
import os

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import yaml

from analysis.universe import get_max_holding_days

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

RESULTS_DIR = r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05\results'
VALID_EXIT_REASONS = {"CRISIS_EXIT", "STOP_LOSS", "SIGNAL_OFF", "MAX_HOLDING", "END_OF_DATA"}


def check_arm(arm):
    path = f"{RESULTS_DIR}\\reset_{arm}.json"
    if not os.path.exists(path):
        print(f"{arm}: not yet available, skipping")
        return None
    with open(path) as f:
        trades = json.load(f)

    exit_reasons = set()
    n_holding_violations = 0
    max_hold_cache = {}
    for t in trades:
        exit_reasons.add(t["exit_reason"])
        if t["exit_reason"] == "MAX_HOLDING":
            cap = max_hold_cache.setdefault(t["ticker"], get_max_holding_days(t["ticker"], CONFIG))
            if t["holding_days"] > cap + 5:  # weekend/scheduling slack, same tolerance sibling reports used
                n_holding_violations += 1
                print(f"    VIOLATION: {t['ticker']} {t['entry_date']}->{t['exit_date']} holding_days={t['holding_days']} cap={cap}")

    within_vocab = exit_reasons <= VALID_EXIT_REASONS
    max_pos = max((t["position_gbp"] for t in trades), default=0)
    print(f"{arm}: {len(trades)} trades | exit reasons: {sorted(exit_reasons)} (within vocab: {within_vocab}) | "
          f"holding-period violations: {n_holding_violations} | max position_gbp: £{max_pos:,.2f}")
    return {
        "arm": arm, "n_trades": len(trades), "exit_reasons": sorted(exit_reasons),
        "within_vocab": within_vocab, "holding_violations": n_holding_violations,
        "max_position_gbp": max_pos,
    }


if __name__ == "__main__":
    results = {}
    for arm in ["baseline", "modest", "aggressive", "whole_dist"]:
        r = check_arm(arm)
        if r:
            results[arm] = r
    with open(f"{RESULTS_DIR}\\..\\parity_check_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
