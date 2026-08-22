"""Run the four TREATED arms (N = 14, 8, 5, 3) with full instrumentation.

Per PREREGISTRATION.md Sec 3, each arm's three sizing constants are DERIVED
from N and reduce exactly to the live values at N = 14:

    per_trade_pct_N = max_total_deployed / N
    max_risk_pct_N  = max_risk_pct * (N_nom / N),  N_nom = 0.70 / 0.05 = 14
    cash_fraction_N = max(0.25, (max_total_deployed / N) / (1 - max_total_deployed))

max_risk_pct is supplied via a COPIED config dict, never a code edit.
The uncapped baseline control is produced by step2_verify.py.

Usage:  python step3_run_arms.py [N ...]     (default: 14 8 5 3)
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

from slot_engine import simulate_slot_capped  # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)

MAX_DEPLOYED = float(BASE["trading212_invest"]["max_total_deployed"])   # 0.70
LIVE_PER_TRADE = float(BASE["trading212_invest"]["per_trade_pct"])      # 0.05
LIVE_MAX_RISK = float(BASE["trading212_invest"]["max_risk_pct"])        # 0.01
N_NOM = MAX_DEPLOYED / LIVE_PER_TRADE                                   # 14.0


def arm_params(n: int) -> dict:
    return {
        "slot_cap": n,
        "per_trade_pct": MAX_DEPLOYED / n,
        "max_risk_pct": LIVE_MAX_RISK * (N_NOM / n),
        "cash_fraction": max(0.25, (MAX_DEPLOYED / n) / (1.0 - MAX_DEPLOYED)),
    }


def main() -> None:
    arms = [int(a) for a in sys.argv[1:]] or [14, 8, 5, 3]
    summary = {}
    for pool in ("pool2_clean", "pool1"):
        with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
            cache = pickle.load(fh)
        caches, gspc = cache["caches"], cache["gspc_close"]
        for n in arms:
            p = arm_params(n)
            cfg = yaml.safe_load(yaml.safe_dump(BASE))          # deep copy
            cfg["trading212_invest"]["max_risk_pct"] = p["max_risk_pct"]
            t0 = time.time()
            trades, panel, admissions, slot_full, rejections = simulate_slot_capped(
                caches, cfg, gspc,
                per_trade_pct=p["per_trade_pct"],
                max_deployed_pct=MAX_DEPLOYED,
                slot_cap=p["slot_cap"],
                cash_fraction=p["cash_fraction"],
                instrument=True,
            )
            dt = time.time() - t0
            tag = f"{pool}_n{n}"
            pd.DataFrame(trades).to_csv(HERE / f"trades_{tag}.csv", index=False)
            pd.DataFrame(panel).to_csv(HERE / f"panel_{tag}.csv", index=False)
            pd.DataFrame(admissions).to_csv(HERE / f"admissions_{tag}.csv", index=False)
            pd.DataFrame(slot_full).to_csv(HERE / f"slotfull_{tag}.csv", index=False)
            pd.DataFrame(rejections).to_csv(HERE / f"rejections_{tag}.csv", index=False)
            summary[tag] = {
                "params": p,
                "trades": len(trades),
                "panel_rows": len(panel),
                "admissions": len(admissions),
                "slot_full_events": len(slot_full),
                "capacity_rejections": len(rejections),
                "runtime_s": round(dt, 1),
            }
            print(f"{tag}: {len(trades)} trades, {len(slot_full)} slot-full events, "
                  f"{dt:.0f}s", flush=True)
            (HERE / "step3_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
