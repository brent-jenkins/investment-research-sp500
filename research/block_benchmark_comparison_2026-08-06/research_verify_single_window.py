"""Verification gate: reproduce the cited 2020-01-01 -> 2026-08-06
single-window figures using the REAL production reset-at-boundary leg
(analysis.benchmark_comparison.run_current_strategy_leg) before any block
output is trusted.

Cited reference figures (from the task spec):
    CAGR                       10.74%
    Max DD (peak-relative)    -15.70%
    Total return              +95.74%
"""
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

from analysis.benchmark_comparison import run_current_strategy_leg, run_sp500_benchmark_leg
from data.ingestion import get_price_df

CONFIG = yaml.safe_load(open("config.yaml"))
START = pd.Timestamp("2020-01-01")
END = pd.Timestamp("2026-08-06")
CAPITAL = 50_000.0

REFERENCE = {"cagr_pct": 10.74, "max_drawdown_pct_peak_relative": -15.7, "total_pct_change": 95.74}

if __name__ == "__main__":
    strat = run_current_strategy_leg(CONFIG, START, END, CAPITAL)

    gspc = get_price_df("^GSPC", days=8000).set_index("date")
    gspc.index = pd.to_datetime(gspc.index)
    sp = run_sp500_benchmark_leg(gspc["close"].astype(float), START, END, CAPITAL)

    out = {"strategy": strat, "sp500": sp, "reference": REFERENCE}
    print(json.dumps(out, indent=2, default=str))

    print("\n=== REPRODUCTION CHECK ===")
    ok = True
    for k, ref in REFERENCE.items():
        got = strat[k]
        delta = got - ref
        # tolerance: 0.05pp -- tighter than any rounding in the cited figures
        good = abs(delta) <= 0.05
        ok = ok and good
        print(f"{k:40s} ref={ref:>10.2f}  got={got:>10.2f}  delta={delta:>+8.2f}  {'OK' if good else 'MISMATCH'}")
    print(f"\nOVERALL: {'REPRODUCED' if ok else 'DID NOT REPRODUCE'}")
    Path("verify_single_window.json").write_text(json.dumps(out, indent=2, default=str))
    sys.exit(0 if ok else 1)
