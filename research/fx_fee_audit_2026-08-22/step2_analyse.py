"""FX-fee audit. READ-ONLY over exported CSVs. No writes anywhere.

TEST A is decisive and runs first: if the GBP amount stored against a filled
order is a value THIS SYSTEM computed from its own GBPUSD=X series, then the
"implied rate" recovered from it is that series' own close and carries no
information about what T212 charged. Test A checks whether each implied rate
matches a stored GBPUSD=X close exactly.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
pd.set_option("display.width", 220)

orders = pd.read_csv(HERE / "fx_orders.csv")
pnl = pd.read_csv(HERE / "fx_pnl.csv")
fx = pd.read_csv(HERE / "fx_gbpusd.csv")
fx["date"] = pd.to_datetime(fx["date"]).dt.normalize()
fx = fx.sort_values("date").reset_index(drop=True)
fx_close = fx.set_index("date")["close"].astype(float)

print("=" * 78)
print("0. POPULATION")
print("=" * 78)
print(f"invest_orders rows                : {len(orders)}")
print(f"  by status  : {orders['status'].value_counts().to_dict()}")
print(f"  by currency: {orders['currency_code'].value_counts(dropna=False).to_dict()}")
print(f"pnl rows                          : {len(pnl)}")
print(f"  by currency: {pnl['currency'].value_counts(dropna=False).to_dict()}")
print(f"  by exit_reason: {pnl['exit_reason'].value_counts().to_dict()}")

f = orders[(orders["status"] == "FILLED")
           & orders["fill_price"].notna()
           & orders["filled_quantity"].notna()
           & orders["estimated_value_gbp"].notna()
           & (orders["estimated_value_gbp"] > 0)].copy()
f["filled_at"] = pd.to_datetime(f["filled_at"])
f["trade_date"] = f["filled_at"].dt.normalize()
f["usd_value"] = f["fill_price"].astype(float) * f["filled_quantity"].astype(float)
f["implied_rate"] = f["usd_value"] / f["estimated_value_gbp"].astype(float)
usd = f[f["currency_code"] == "USD"].copy()
print(f"\nFILLED orders usable              : {len(f)}   of which USD: {len(usd)}")
print(f"  USD legs by direction: {usd['direction'].value_counts().to_dict()}")

print()
print("=" * 78)
print("TEST A (DECISIVE) -- is the implied rate a COPY of our own GBPUSD=X series?")
print("=" * 78)
print("For each USD leg, find the stored GBPUSD=X close nearest the implied rate,")
print("and report the relative gap. An exact match means the GBP figure was")
print("computed by this system from price_data, not reported by the broker.")
print()
closes = fx_close.values
dates = fx_close.index
rows = []
for r in usd.itertuples(index=False):
    ir = float(r.implied_rate)
    j = int(np.argmin(np.abs(closes - ir)))
    rows.append({
        "id": r.id, "ticker": r.ticker, "dir": r.direction,
        "trade_date": r.trade_date.date(), "usd_value": round(r.usd_value, 2),
        "gbp_stored": r.estimated_value_gbp, "implied_rate": round(ir, 6),
        "nearest_close": round(float(closes[j]), 6),
        "nearest_close_date": dates[j].date(),
        "rel_gap_bp": round(1e4 * abs(closes[j] - ir) / ir, 3),
        "lag_days": (r.trade_date.date() - dates[j].date()).days,
    })
A = pd.DataFrame(rows)
print(A.to_string(index=False))
print()
n_exact = int((A["rel_gap_bp"] < 1.0).sum())
print(f"legs whose implied rate matches a stored close to <1 basis point: "
      f"{n_exact}/{len(A)}")
print(f"  median gap to nearest stored close: {A['rel_gap_bp'].median():.4f} bp")
print(f"  lag (trade_date - matched close date) distribution: "
      f"{A['lag_days'].value_counts().sort_index().to_dict()}")

print()
print("=" * 78)
print("TEST B -- implied rate vs GBPUSD=X close ON the trade date, per leg")
print("=" * 78)


def close_on_or_before(d):
    s = fx_close[fx_close.index <= d]
    return float(s.iloc[-1]) if len(s) else np.nan


usd["ref_close"] = usd["trade_date"].map(close_on_or_before)
usd["diff_pct"] = (usd["implied_rate"] / usd["ref_close"] - 1.0) * 100.0
B = usd[["id", "ticker", "direction", "trade_date", "implied_rate",
         "ref_close", "diff_pct"]].copy()
B["trade_date"] = B["trade_date"].dt.date
B["implied_rate"] = B["implied_rate"].round(6)
B["ref_close"] = B["ref_close"].round(6)
B["diff_pct"] = B["diff_pct"].round(4)
print(B.to_string(index=False))


def describe(s, label):
    if not len(s):
        print(f"  {label:<12} (none)")
        return
    print(f"  {label:<12} n={len(s):<3} mean={s.mean():+.4f}%  median={s.median():+.4f}%  "
          f"sd={s.std():.4f}  min={s.min():+.4f}%  max={s.max():+.4f}%  "
          f"n_pos={int((s > 0).sum())} n_neg={int((s < 0).sum())}")


print("\ndistribution of (implied / trade-date close - 1):")
describe(usd["diff_pct"], "ALL legs")
describe(usd.loc[usd["direction"] == "BUY", "diff_pct"], "BUY legs")
describe(usd.loc[usd["direction"] == "SELL", "diff_pct"], "SELL legs")
print("\n  A real per-conversion fee is ONE-DIRECTIONAL on both legs.")
print("  Two-sided scatter centred near zero is not a fee signature.")

print()
print("=" * 78)
print("TEST C -- the same question asked of the pnl table")
print("=" * 78)
p = pnl[pnl["currency"] == "USD"].copy()
p["native_pnl"] = ((p["exit_price"].astype(float) - p["entry_price"].astype(float))
                   * p["quantity"].astype(float))
p = p[p["pnl_gbp"].astype(float).abs() > 1e-9]
p["implied_rate"] = p["native_pnl"] / p["pnl_gbp"].astype(float)
p["exit_d"] = pd.to_datetime(p["exit_date"]).dt.normalize()
p["ref_close"] = p["exit_d"].map(close_on_or_before)
p["diff_pct"] = (p["implied_rate"] / p["ref_close"] - 1.0) * 100.0
print(p[["ticker", "exit_d", "native_pnl", "pnl_gbp", "implied_rate",
         "ref_close", "diff_pct"]].round(4).to_string(index=False))
if len(p):
    describe(p["diff_pct"], "pnl legs")

print()
print("=" * 78)
print("TEST D -- broker-reported fields in the raw positions snapshot")
print("=" * 78)
snap = json.loads((HERE / "fx_positions_snapshot.json").read_text(encoding="utf-8"))
print(f"positions: {len(snap)}")
print(f"fields T212 actually returns: {sorted(snap[0].keys())}")
print("\nppl vs fxPpl (fxPpl is T212's own FX translation P&L, not a fee):")
d = pd.DataFrame([{ "ticker": s["ticker"], "qty": s["quantity"],
                    "avgPrice": s["averagePrice"], "currentPrice": s["currentPrice"],
                    "ppl": s.get("ppl"), "fxPpl": s.get("fxPpl")} for s in snap])
d["fx_over_ppl_pct"] = (d["fxPpl"].abs() / d["ppl"].abs().replace(0, np.nan) * 100).round(2)
print(d.to_string(index=False))
print(f"\nfxPpl present on {int(d['fxPpl'].notna().sum())}/{len(d)} positions; "
      f"sum fxPpl = {d['fxPpl'].sum():.2f}, sum ppl = {d['ppl'].sum():.2f}")
print("NOTE: no explicit fee/commission/conversion field is returned by T212 here.")
