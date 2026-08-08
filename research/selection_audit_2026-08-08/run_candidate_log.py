"""Run the instrumented engine with log_candidates=True (momentum admission
mode, i.e. reproducing the real production ranking) against a given pool's
cache, for Parts 4/5/6. Usage: python run_candidate_log.py <pool1|pool2_final>
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-selection-audit")

import pickle
import time

import pandas as pd
import yaml

from instrumented_engine import simulate_instrumented

OUT_DIR = r"c:\GitHub\investment-app-selection-audit\research\selection_audit_2026-08-08"
CACHE_DIR = r"c:\GitHub\investment-app-selection-audit\research\stop_loss_reconfiguration_2026-08-07"
MAX_DEPLOYED_PCT = 0.70


def main():
    pool = sys.argv[1]
    with open(r"c:\GitHub\investment-app-selection-audit\config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    per_trade_pct = float(config.get("trading212_invest", {}).get("per_trade_pct", 0.05))

    t0 = time.time()
    with open(f"{CACHE_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    print(f"[{pool}] cache loaded ({time.time()-t0:.0f}s)", flush=True)

    result = simulate_instrumented(
        caches, config, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
        disable_stop_loss=False, admission_mode="momentum", log_candidates=True,
    )
    trades = pd.DataFrame(result["trades"])
    rejections = pd.DataFrame(result["rejections"])
    candidate_log = pd.DataFrame(result["candidate_log"])

    trades.to_csv(f"{OUT_DIR}/{pool}_trades_instrumented.csv", index=False)
    rejections.to_csv(f"{OUT_DIR}/{pool}_rejections_instrumented.csv", index=False)
    candidate_log.to_csv(f"{OUT_DIR}/{pool}_candidate_log.csv", index=False)

    print(f"[{pool}] trades={len(trades)} total_pnl={trades['pnl_gbp'].sum():.2f} "
          f"rejections={len(rejections)} candidate_log_rows={len(candidate_log)} "
          f"({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
