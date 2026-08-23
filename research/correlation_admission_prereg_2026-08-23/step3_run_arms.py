"""Run the arms. Usage: python step3_run_arms.py <pool> [k ...]

Sec 2.4's grid: baseline k=0, diversifying +0.25/+0.50/+1.00, reinforcing
-0.25/-0.50/-1.00. One pool per invocation so runs can be parallelised
without each process holding two caches.

Writes trades / panel / admissions / corr_log per arm. Emits NO return
metric -- Sec 3 forbids any return figure before the gate is complete, and
this script deliberately cannot produce one.
"""
import json
import pickle
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from corr_engine import CorrelationBook, simulate_corr_admission  # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)

ARM_NAMES = {0.0: "baseline", 0.25: "D1", 0.5: "D2", 1.0: "D3",
             -0.25: "R1", -0.5: "R2", -1.0: "R3"}


def main() -> None:
    pool = sys.argv[1]
    ks = [float(x) for x in sys.argv[2:]] or [0.0, 0.25, 0.5, 1.0, -0.25, -0.5, -1.0]

    with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
        cache = pickle.load(fh)
    caches, gspc = cache["caches"], cache["gspc_close"]

    dates = None
    for c in caches:
        if c is None:
            continue
        dates = c["frame"].index if dates is None else dates.union(c["frame"].index)
    book = CorrelationBook(caches, dates)

    ptp = float(BASE["trading212_invest"]["per_trade_pct"])
    mdp = float(BASE["trading212_invest"]["max_total_deployed"])

    summary = {}
    for k in ks:
        arm = ARM_NAMES[k]
        tag = f"{pool}_{arm}"
        t0 = time.time()
        trades, panel, admissions, corr_log = simulate_corr_admission(
            caches, BASE, gspc, ptp, mdp, k=k, book=book, instrument=True)
        dt = time.time() - t0
        pd.DataFrame(trades).to_csv(HERE / f"trades_{tag}.csv", index=False)
        pd.DataFrame(panel).to_csv(HERE / f"panel_{tag}.csv", index=False)
        pd.DataFrame(admissions).to_csv(HERE / f"admissions_{tag}.csv", index=False)
        pd.DataFrame(corr_log).to_csv(HERE / f"corrlog_{tag}.csv", index=False)
        summary[tag] = {"k": k, "trades": len(trades), "admissions": len(admissions),
                        "runtime_s": round(dt, 1)}
        print(f"{tag}: k={k:+.2f}  {len(trades)} trades  {dt:.0f}s", flush=True)
        (HERE / f"step3_summary_{pool}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
