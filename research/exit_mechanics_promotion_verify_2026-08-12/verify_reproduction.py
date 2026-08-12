"""THE decisive promotion check: does the promoted PRODUCTION code, driven only
by config, reproduce the exit-mechanics sweep's own published figures?

The sweep ran through a research engine (sweep_engine.py). Promotion moved the
capability into production (analysis/universe.py's zero-means-disabled cap and
analysis/decision.py:resolve_var_horizon_days()). If the promoted code cannot
reproduce the study exactly, the promotion changed behaviour relative to the
finding it promotes, and that must be caught here rather than after a flip.

Published references (Pool 2 CLEAN, exit_mechanics_prereg_2026-08-11):
    baseline h30  (2.0x, 30d)        : 4,876 trades, GBP 166,830.11
    winner   s3.0 (3.0x, unlimited)  :   565 trades, GBP 348,548.74

Uses raw, unmodified simulate_portfolio_compounding_mtm() -- no research engine.
"""
import pickle
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis.backtest import simulate_portfolio_compounding_mtm   # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)

REF = {
    "baseline": {"stop": 2.0, "hold": 30, "n": 4876, "pnl": 166830.11},
    "winner":   {"stop": 3.0, "hold": 0,  "n": 565,  "pnl": 348548.74},
}


def main() -> None:
    with open(HERE / "_cache_pool2_clean.pkl", "rb") as fh:
        c = pickle.load(fh)
    ok = True
    for name, r in REF.items():
        cfg = yaml.safe_load(yaml.safe_dump(BASE))
        cfg["backtest"]["atr_stop_multiplier"] = r["stop"]
        cfg["trading212_invest"]["stock_max_holding_days"] = r["hold"]
        t0 = time.time()
        trades = simulate_portfolio_compounding_mtm(
            c["caches"], cfg, c["gspc_close"], per_trade_pct=0.05, max_deployed_pct=0.70)
        td = pd.DataFrame(trades)
        n, pnl = len(td), float(td["pnl_gbp"].sum())
        match = (n == r["n"]) and abs(pnl - r["pnl"]) < 0.01
        ok &= match
        print(f"{name:>9} (stop {r['stop']}x, hold {r['hold']}): "
              f"{n:,} trades, GBP {pnl:,.2f}  "
              f"[reference {r['n']:,} / GBP {r['pnl']:,.2f}]  "
              f"{'MATCH' if match else 'MISMATCH'}  ({time.time()-t0:.0f}s)", flush=True)
        if r["hold"] == 0:
            mix = td["exit_reason"].value_counts().to_dict()
            print(f"          exit mix: {mix}")
            print(f"          MAX_HOLDING exits: {mix.get('MAX_HOLDING', 0)} (must be 0)")
            ok &= (mix.get("MAX_HOLDING", 0) == 0)

    print("\n" + ("PASS -- promoted production code reproduces the study exactly"
                  if ok else "FAIL -- promotion changed behaviour"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
