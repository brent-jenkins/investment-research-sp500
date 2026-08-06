"""Targeted diagnostic: arm3/arm4 (skip=21) show absurd CAGR (779%/11250%) in
block 3 (2010-09-03 -> 2011-11-24) while arm1/arm2 (skip=0) show normal
figures for the same block. Find the offending ticker(s)/momentum value(s)
without waiting for the full 16-block run to finish.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))

import pandas as pd
import yaml

from analysis import universe
import arms as ARMS_MOD

BS = pd.Timestamp("2010-09-03")
BE = pd.Timestamp("2011-11-24")

config = yaml.safe_load(open(HERE.parent.parent / "config.yaml"))
cands = universe.get_live_stock_candidates(as_of=BE.date())
print(f"{len(cands)} candidates as of block end")

worst = []
for i, t in enumerate(cands):
    c3 = ARMS_MOD.precompute_ticker_arm(t, config, 3)
    if c3 is None:
        continue
    f3 = c3["frame"]
    window = f3[(f3.index >= BS) & (f3.index <= BE)]
    if window.empty:
        continue
    mmax = window["momentum"].max()
    if pd.notna(mmax) and mmax > 5.0:  # >500% momentum reading -- clearly suspect
        row = window.loc[window["momentum"].idxmax()]
        worst.append((t, float(mmax), window["momentum"].idxmax(), float(row["close"])))
    if (i + 1) % 150 == 0:
        print(f"  checked {i+1}/{len(cands)}")

worst.sort(key=lambda x: -x[1])
print(f"\n{len(worst)} tickers with arm3 momentum > 500% inside block 3:")
for t, m, d, c in worst[:20]:
    print(f"  {t}: momentum={m*100:.1f}% on {d.date()}, close={c}")

if worst:
    bad_ticker = worst[0][0]
    bad_date = worst[0][2]
    print(f"\n--- Investigating {bad_ticker} around {bad_date.date()} ---")
    from data.ingestion import get_price_df
    raw = get_price_df(bad_ticker, days=8000).set_index("date")
    raw.index = pd.to_datetime(raw.index)
    raw = raw.sort_index()
    idx = raw.index.get_indexer([bad_date], method="nearest")[0]
    lo = max(0, idx - 260)
    hi = min(len(raw), idx + 5)
    print(raw.iloc[lo:hi][["close", "adj_close"]].to_string())
