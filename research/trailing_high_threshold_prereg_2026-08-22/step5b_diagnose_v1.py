"""V1 diagnosis. n and total P&L match exactly; 90/85 rows do not. Per §7 a
discrepancy must be diagnosed ROW BY ROW, never waved through on a matching
total. Also persists the baseline arm (a_none) for the instruments step.
"""
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
SWEEP = ROOT / "research" / "exit_mechanics_prereg_2026-08-11"

from trail_engine import simulate_exit_sweep                       # noqa: E402

BASE = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))

for pool, ref in (("pool2_clean", "trades_pool2_clean_s3.0.csv"),
                  ("pool1", "trades_pool1_s3.0.csv")):
    cfg = yaml.safe_load(yaml.safe_dump(BASE))
    cfg["backtest"]["atr_stop_multiplier"] = 3.0
    c = pickle.load(open(HERE / f"_cache_{pool}.pkl", "rb"))
    t0 = time.time()
    res = simulate_exit_sweep(
        c["caches"], cfg, c["gspc_close"], per_trade_pct=0.05, max_deployed_pct=0.70,
        disable_stop_loss=False, max_holding_days_override=None, unlimited_holding=True,
        trail_activation=None, trail_multiplier=3.0, trail_atr_source="current")
    got = pd.DataFrame(res["trades"])
    pn = pd.DataFrame(res["panel"])
    got.to_csv(HERE / f"trades_{pool}_a_none.csv", index=False)
    pn.to_csv(HERE / f"panel_{pool}_a_none.csv", index=False)

    exp = pd.read_csv(SWEEP / ref)
    for d in (got, exp):
        for k in ("entry_date", "exit_date"):
            d[k] = pd.to_datetime(d[k]).dt.strftime("%Y-%m-%d")
    key = ["ticker", "entry_date", "exit_date"]
    g = got.sort_values(key).reset_index(drop=True)
    e = exp.sort_values(key).reset_index(drop=True)

    print("=" * 74)
    print(f"{pool}   n_got={len(g)}  n_exp={len(e)}   "
          f"pnl_got=£{g['pnl_gbp'].sum():,.2f}  pnl_exp=£{e['pnl_gbp'].sum():,.2f}   "
          f"[{time.time()-t0:.0f}s]")
    print("=" * 74)

    # 1) Are the trade IDENTITIES the same set?
    kg = set(map(tuple, g[key].values))
    ke = set(map(tuple, e[key].values))
    print(f"identity sets equal (ticker,entry,exit) : {kg == ke}")
    print(f"  only in got: {len(kg - ke)}   only in exp: {len(ke - kg)}")
    for x in list(kg - ke)[:5]:
        print(f"    got-only {x}")
    for x in list(ke - kg)[:5]:
        print(f"    exp-only {x}")

    # 2) Which COLUMNS actually differ, on the aligned frames?
    print("\nper-column mismatch counts (aligned on the sorted key):")
    for col in ["ticker", "entry_date", "exit_date", "exit_reason", "entry_price",
                "exit_price", "pnl_gbp", "pnl_pct", "position_gbp", "holding_days",
                "initial_stop", "final_stop"]:
        if col not in g.columns or col not in e.columns:
            continue
        if g[col].dtype.kind in "fi" and e[col].dtype.kind in "fi":
            d = (g[col].astype(float) - e[col].astype(float)).abs()
            exact = int((d > 0).sum())
            near = int((d > 1e-6).sum())
            big = int((d > 1e-2).sum())
            print(f"  {col:<14} exact!=:{exact:>4}   >1e-6:{near:>4}   >1e-2:{big:>4}   "
                  f"max|Δ|={d.max():.3e}")
        else:
            n = int((g[col].astype(str) != e[col].astype(str)).sum())
            print(f"  {col:<14} differ:{n:>4}")

    # 3) If exit_reason differs anywhere, that is a REAL behavioural difference.
    if "exit_reason" in g.columns:
        m = g["exit_reason"].astype(str) != e["exit_reason"].astype(str)
        print(f"\nexit_reason mismatches: {int(m.sum())}")
        if m.any():
            print(pd.crosstab(e.loc[m, "exit_reason"], g.loc[m, "exit_reason"]).to_string())
            print(g.loc[m, key + ["exit_reason", "pnl_gbp"]].head(10).to_string())

    # 4) Duplicate keys would make the sort ambiguous -- check.
    dup_g = int(g.duplicated(subset=key).sum())
    dup_e = int(e.duplicated(subset=key).sum())
    print(f"\nduplicate (ticker,entry,exit) keys: got={dup_g} exp={dup_e}")
    print()
