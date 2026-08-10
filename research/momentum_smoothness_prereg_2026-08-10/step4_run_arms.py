"""Step 4 — run the three filtered arms (B1/B2/B3) on both pools.

Arm A is not re-run: step3_parity.py already produced and pickled it, having
verified it reproduces the published Arm B trade set exactly.
"""
import pickle
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, r"c:\GitHub\investment-app-smoothness")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from smoothness_engine import simulate_smoothness_filtered  # noqa: E402

OUT = Path(__file__).resolve().parent
with open(r"c:\GitHub\investment-app-smoothness\config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

with open(OUT / "_thresholds.pkl", "rb") as fh:
    TH = pickle.load(fh)

PER_TRADE_PCT = 0.05
MAX_DEPLOYED = 0.70

print("=" * 78)
print(f"STEP 4 — FILTERED ARMS  (frozen thresholds: {TH})")
print("=" * 78, flush=True)

for label in ("pool2_clean", "pool1"):
    with open(OUT / f"_cache_{label}.pkl", "rb") as fh:
        blob = pickle.load(fh)
    for arm, key in (("B1", "T1"), ("B2", "T2"), ("B3", "T3")):
        thr = TH[key]
        out_path = OUT / f"_arm{arm}_{label}.pkl"
        if out_path.exists():
            print(f"{arm} {label}: already done, skipping", flush=True)
            continue
        t0 = time.time()
        res = simulate_smoothness_filtered(
            blob["caches"], CONFIG, blob["gspc_close"],
            per_trade_pct=PER_TRADE_PCT, max_deployed_pct=MAX_DEPLOYED,
            smoothness_threshold=thr,
        )
        fs = res["filter_stats"]
        total_seen = fs["evaluated"] + fs["excluded_nan"]
        excl = fs["excluded_threshold"] + fs["excluded_nan"]
        pnl = sum(t["pnl_gbp"] for t in res["trades"])
        print(f"{arm} {label}: thr={thr:+.3f}  trades={len(res['trades'])}  "
              f"P&L=GBP {pnl:,.2f}  excluded={excl:,}/{total_seen:,} "
              f"({100*excl/max(total_seen,1):.1f}%)  nan={fs['excluded_nan']}  "
              f"({time.time()-t0:.0f}s)", flush=True)
        with open(out_path, "wb") as fh:
            pickle.dump(res, fh)

print("\nALL ARMS COMPLETE", flush=True)
