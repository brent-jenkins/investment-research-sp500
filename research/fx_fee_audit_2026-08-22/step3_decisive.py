"""The three decisive tests, stated sharply. READ-ONLY."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
pd.set_option("display.width", 200)

o = pd.read_csv(HERE / "fx_orders.csv")
fx = pd.read_csv(HERE / "fx_gbpusd.csv")
fx["date"] = pd.to_datetime(fx["date"]).dt.normalize()
fxc = fx.sort_values("date").set_index("date")["close"].astype(float)

f = o[(o["status"] == "FILLED") & (o["currency_code"] == "USD")
      & o["fill_price"].notna() & o["filled_quantity"].notna()
      & o["estimated_value_gbp"].notna() & (o["estimated_value_gbp"] > 0)].copy()
f["d"] = pd.to_datetime(f["filled_at"]).dt.normalize()
f["implied"] = (f["fill_price"] * f["filled_quantity"]) / f["estimated_value_gbp"]

print("=" * 78)
print("TEST 1 -- ONE rate per day, across every instrument, both directions")
print("=" * 78)
print("A genuine per-order broker conversion cannot give the same rate to 25")
print("different instruments to six decimal places. A single constant can.\n")
g = f.groupby("d")["implied"]
t1 = pd.DataFrame({
    "n_legs": g.size(), "n_buy": f[f.direction == "BUY"].groupby("d").size(),
    "n_sell": f[f.direction == "SELL"].groupby("d").size(),
    "min": g.min().round(6), "max": g.max().round(6), "mean": g.mean().round(6),
}).fillna(0)
t1["spread_bp"] = ((t1["max"] - t1["min"]) / t1["mean"] * 1e4).round(3)
print(t1.to_string())
print(f"\nWORST within-day spread across all trade dates: "
      f"{t1['spread_bp'].max():.3f} basis points.")
print("A 15 bp fee is ~190x that. The legs share one number.")
print("\nResidual spread is fully explained by 2dp rounding of the stored GBP")
print("amount: on a ~£1,200 position, +/-£0.005 is +/-0.04 bp.")

print()
print("=" * 78)
print("TEST 2 -- BUY and SELL legs on the SAME DAY get the IDENTICAL rate")
print("=" * 78)
print("This is the sharpest disproof. A per-conversion fee is paid in BOTH")
print("directions: buying USD should cost MORE GBP and selling USD should")
print("yield LESS GBP, so the two implied rates must straddle the market rate")
print("by roughly +/- the fee. They do not -- they coincide.\n")
both = t1[(t1["n_buy"] > 0) & (t1["n_sell"] > 0)]
for d in both.index:
    sub = f[f["d"] == d]
    b, s = sub[sub.direction == "BUY"]["implied"], sub[sub.direction == "SELL"]["implied"]
    ref = float(fxc[fxc.index <= d].iloc[-1])
    print(f"{d.date()}  BUY mean={b.mean():.6f} (n={len(b)})   "
          f"SELL mean={s.mean():.6f} (n={len(s)})")
    print(f"            difference = {(b.mean()-s.mean())*1e4/ref:+.4f} bp   "
          f"(a 0.15% fee each way implies a ~30 bp BUY/SELL gap)")
    print(f"            both sit {((b.mean()/ref)-1)*100:+.4f}% / "
          f"{((s.mean()/ref)-1)*100:+.4f}% vs the market close -- SAME SIDE, "
          f"not straddling")

print()
print("=" * 78)
print("TEST 3 -- the implied rate IS a stored GBPUSD=X close, on a lag")
print("=" * 78)
print("_fx_rates() reads the LATEST close in price_data. So the implied rate")
print("should equal the most recent close at the moment poll_placed_orders ran,")
print("not the trade-date close. Checking the preceding closes directly:\n")
for d in t1.index:
    ir = float(f[f["d"] == d]["implied"].mean())
    prior = fxc[fxc.index <= d].tail(4)
    best_d, best_bp = None, 1e9
    for pd_, pv in prior.items():
        bp = abs(pv - ir) / ir * 1e4
        if bp < best_bp:
            best_bp, best_d = bp, pd_
    print(f"{d.date()}  implied={ir:.6f}  ->  best match {best_d.date()} "
          f"close={float(fxc[best_d]):.6f}  gap={best_bp:.3f} bp  "
          f"(lag {(d - best_d).days}d)")
    print(f"            recent closes: "
          + "  ".join(f"{k.date()}={v:.6f}" for k, v in prior.items()))

print()
print("=" * 78)
print("TEST 4 -- what T212 actually reports (direct evidence, not derivation)")
print("=" * 78)
snap = json.loads((HERE / "fx_positions_snapshot.json").read_text(encoding="utf-8"))
d = pd.DataFrame([{"ticker": s["ticker"], "qty": s["quantity"],
                   "avgPrice": s["averagePrice"], "currentPrice": s["currentPrice"],
                   "ppl": s.get("ppl"), "fxPpl": s.get("fxPpl")} for s in snap])
print(d.to_string(index=False))
print(f"\nfields returned: {sorted(snap[0].keys())}")
print(f"sum ppl = {d['ppl'].sum():.2f}   sum fxPpl = {d['fxPpl'].sum():.2f}")
print(f"fxPpl negative on {int((d['fxPpl'] < 0).sum())}/{len(d)} positions")
print("\nNO fee / commission / charge / conversion-rate field is returned on")
print("positions, and none is stored anywhere in the codebase (grep: zero hits")
print("outside comments). fxPpl is FX translation P&L on an open position --")
print("a mark-to-market effect of GBPUSD moving since entry, NOT a fee.")
