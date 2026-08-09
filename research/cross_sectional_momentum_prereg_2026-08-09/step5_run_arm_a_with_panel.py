"""Arm A (baseline), with a deployment panel -- needed for the deployment
guard (Section 7.3), which the plain simulate_portfolio_compounding_mtm()
call doesn't return. Reuses redesign_engine.py's simulate_with_panel() in
its DEGENERATE case (rebalance_frequency="daily", shortlist_size=None),
already proven byte-identical to the real, unmodified production engine
(research/admission_redesign_prereg_2026-08-08/REPORT.md: "4,749 trades,
GBP240,360.75, 0 row-level mismatches") -- not a new, unverified engine.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg")
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg\research\admission_redesign_prereg_2026-08-08")

import pickle
import time

import pandas as pd
import yaml

from redesign_engine import simulate_with_panel

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/cross_sectional_momentum_prereg_2026-08-09"
PER_TRADE_PCT = 0.05
MAX_DEPLOYED_PCT = 0.70


def main():
    pool = sys.argv[1] if len(sys.argv) > 1 else "pool1"
    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = [c for c in data["caches"] if c is not None]
    t0 = time.time()
    result = simulate_with_panel(
        caches, CONFIG, data["gspc_close"], PER_TRADE_PCT, MAX_DEPLOYED_PCT,
        rebalance_frequency="daily", shortlist_size=None,
    )
    trades, panel = result["trades"], result["panel"]
    total_pnl = sum(t["pnl_gbp"] for t in trades)
    print(f"[A/{pool}] {len(caches)} tickers, {len(trades)} trades, total_pnl={total_pnl:.2f} ({time.time()-t0:.0f}s)", flush=True)
    pd.DataFrame(trades).to_csv(f"{OUT_DIR}/trades_{pool}_A.csv", index=False)
    pd.DataFrame(panel).to_csv(f"{OUT_DIR}/panel_{pool}_A.csv", index=False)
    print(f"[A/{pool}] wrote trades_{pool}_A.csv, panel_{pool}_A.csv", flush=True)


if __name__ == "__main__":
    main()
