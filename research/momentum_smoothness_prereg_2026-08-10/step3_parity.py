"""Step 3 — §8 sanity gate + §9 degenerate parity.

Two distinct checks, both before any real arm result is trusted:

  1. DEGENERATE PARITY (§9.1): smoothness_engine with smoothness_threshold=None
     must reproduce the relative-ranking study's own Arm B trade set exactly.
     This proves every part of the engine except the filter line is a faithful
     copy -- the strongest verification available.

  2. SANITY GATE (§8): that same Arm A output must reproduce the published
     figures (Pool 2 CLEAN: 4,876 trades, GBP 166,830.11). Compared as trade
     SETS on a canonical sort, never as totals -- a matching total with a
     differing set has already happened in this project.
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
REF = OUT.parent / "relative_ranking_prereg_2026-08-09"
with open(r"c:\GitHub\investment-app-smoothness\config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

PER_TRADE_PCT = 0.05
MAX_DEPLOYED = 0.70
KEY = ["ticker", "entry_date", "exit_date"]
FAILURES = []


def canon(df):
    d = df.copy()
    for c in ("entry_date", "exit_date"):
        d[c] = pd.to_datetime(d[c]).dt.strftime("%Y-%m-%d")
    return d.sort_values(KEY).reset_index(drop=True)


def run_arm_a(label):
    with open(OUT / f"_cache_{label}.pkl", "rb") as fh:
        blob = pickle.load(fh)
    t0 = time.time()
    res = simulate_smoothness_filtered(
        blob["caches"], CONFIG, blob["gspc_close"],
        per_trade_pct=PER_TRADE_PCT, max_deployed_pct=MAX_DEPLOYED,
        smoothness_threshold=None,          # filter OFF -> must equal Arm A
    )
    print(f"  {label}: {len(res['trades'])} trades ({time.time()-t0:.0f}s)", flush=True)
    return res


print("=" * 78)
print("STEP 3 — DEGENERATE PARITY (§9.1) + SANITY GATE (§8)")
print("=" * 78)

for label, ref_file, exp_trades, exp_pnl in [
    ("pool2_clean", "trades_pool2_clean_B.csv", 4876, 166830.11),
    ("pool1", "trades_pool1_B.csv", 4757, None),
]:
    print(f"\n--- {label} ---")
    res = run_arm_a(label)
    ours = canon(pd.DataFrame(res["trades"]))
    ref = canon(pd.read_csv(REF / ref_file))

    print(f"  ours: {len(ours)} trades, total P&L GBP {ours['pnl_gbp'].sum():,.2f}")
    print(f"  ref : {len(ref)} trades, total P&L GBP {ref['pnl_gbp'].sum():,.2f}")

    same_n = len(ours) == len(ref)
    print(f"  [{'PASS' if same_n else 'FAIL'}] trade counts match")
    if not same_n:
        FAILURES.append(f"{label} count")

    # Trade-SET comparison on the canonical key.
    ours_keys = set(map(tuple, ours[KEY].values))
    ref_keys = set(map(tuple, ref[KEY].values))
    only_ours = ours_keys - ref_keys
    only_ref = ref_keys - ours_keys
    set_match = not only_ours and not only_ref
    print(f"  [{'PASS' if set_match else 'FAIL'}] trade SETS identical "
          f"(ours-only={len(only_ours)}, ref-only={len(only_ref)})")
    if not set_match:
        FAILURES.append(f"{label} set")
        for k in sorted(only_ours)[:8]:
            print(f"      ours-only: {k}")
        for k in sorted(only_ref)[:8]:
            print(f"      ref-only : {k}")

    if same_n and set_match:
        m = ours.merge(ref, on=KEY, suffixes=("_o", "_r"))
        dp = (m["pnl_gbp_o"] - m["pnl_gbp_r"]).abs().max()
        print(f"  [{'PASS' if dp < 0.01 else 'FAIL'}] per-trade P&L identical (max abs diff {dp:.6f})")
        if dp >= 0.01:
            FAILURES.append(f"{label} pnl")

    if exp_trades is not None:
        ok = len(ours) == exp_trades
        print(f"  [{'PASS' if ok else 'FAIL'}] §8 gate: {exp_trades} published trades")
        if not ok:
            FAILURES.append(f"{label} published count")
    if exp_pnl is not None:
        got = float(ours["pnl_gbp"].sum())
        ok = abs(got - exp_pnl) < 0.02
        print(f"  [{'PASS' if ok else 'FAIL'}] §8 gate: published P&L GBP {exp_pnl:,.2f} (got {got:,.2f})")
        if not ok:
            FAILURES.append(f"{label} published pnl")

    fs = res["filter_stats"]
    ok = fs["excluded_threshold"] == 0 and fs["excluded_nan"] == 0
    print(f"  [{'PASS' if ok else 'FAIL'}] filter inert when disabled: {fs}")
    if not ok:
        FAILURES.append(f"{label} filter not inert")

    with open(OUT / f"_armA_{label}.pkl", "wb") as fh:
        pickle.dump(res, fh)

print("\n" + "=" * 78)
if FAILURES:
    print(f"FAILED — {FAILURES}")
    sys.exit(1)
print("PARITY + SANITY GATE PASSED — arm A reproduces the published Arm B exactly")
print("=" * 78)
