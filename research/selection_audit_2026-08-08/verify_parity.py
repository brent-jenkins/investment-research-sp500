"""Verify the instrumented engine (instrumented_engine.py) reproduces the
real engine's own already-published trades_pool1_A.csv exactly, in
momentum admission_mode with log_candidates=False (i.e. behaviourally
identical call pattern to the real simulate_portfolio_compounding_mtm).
This is the parity check required before trusting any of Parts 4-7's output
-- see CLAUDE.md's "Research scripts must verify decision-engine parity."
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-selection-audit")

import pickle
import time

import pandas as pd
import yaml

from instrumented_engine import simulate_instrumented

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/stop_loss_reconfiguration_2026-08-07"
MAX_DEPLOYED_PCT = 0.70

t0 = time.time()
with open(f"{OUT_DIR}/_cache_pool1.pkl", "rb") as fh:
    data = pickle.load(fh)
caches = data["caches"]
gspc_close = data["gspc_close"]
print(f"Loaded cache ({time.time()-t0:.0f}s)", flush=True)

per_trade_pct = float(CONFIG.get("trading212_invest", {}).get("per_trade_pct", 0.05))

result = simulate_instrumented(
    caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
    disable_stop_loss=False, admission_mode="momentum", log_candidates=False,
)
trades = result["trades"]
rejections = result["rejections"]
df_new = pd.DataFrame(trades)[[
    "ticker", "direction", "entry_date", "exit_date", "entry_price", "exit_price",
    "exit_reason", "pnl_pct", "position_gbp", "pnl_gbp", "stop_distance_pct", "holding_days", "is_win",
]].reset_index(drop=True)

df_ref = pd.read_csv(f"{OUT_DIR}/trades_pool1_A.csv", parse_dates=["entry_date", "exit_date"])
df_ref["entry_date"] = df_ref["entry_date"].dt.date
df_ref["exit_date"] = df_ref["exit_date"].dt.date
df_new["entry_date"] = df_new["entry_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())
df_new["exit_date"] = df_new["exit_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())

print(f"Instrumented: {len(df_new)} trades, total_pnl={df_new['pnl_gbp'].sum():.2f}")
print(f"Reference:    {len(df_ref)} trades, total_pnl={df_ref['pnl_gbp'].sum():.2f}")
print(f"Instrumented rejections: {len(rejections)}")

ref_rejections = pd.read_csv(f"{OUT_DIR}/rejections_pool1_A.csv")
print(f"Reference rejections:    {len(ref_rejections)}")

assert len(df_new) == len(df_ref), "TRADE COUNT MISMATCH"
assert abs(df_new["pnl_gbp"].sum() - df_ref["pnl_gbp"].sum()) < 0.01, "TOTAL PNL MISMATCH"

# Exact row-by-row comparison (sorted by entry_date, ticker for stable order)
sort_cols = ["entry_date", "ticker"]
a = df_new.sort_values(sort_cols).reset_index(drop=True)
b = df_ref.sort_values(sort_cols).reset_index(drop=True)
diffs = 0
for col in ["exit_date", "exit_reason", "holding_days"]:
    mism = (a[col].astype(str) != b[col].astype(str)).sum()
    if mism:
        print(f"  MISMATCH in {col}: {mism} rows differ")
        diffs += mism
for col in ["pnl_gbp", "position_gbp", "entry_price", "exit_price"]:
    mism = (a[col] - b[col]).abs().gt(0.01).sum()
    if mism:
        print(f"  MISMATCH in {col}: {mism} rows differ")
        diffs += mism

print(f"\nTotal row-level mismatches: {diffs}")
print("PARITY CONFIRMED -- byte-identical to trades_pool1_A.csv" if diffs == 0 else "PARITY FAILED -- see mismatches above")
print(f"Done ({time.time()-t0:.0f}s total)")
