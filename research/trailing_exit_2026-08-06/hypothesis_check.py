"""Direct test of the pre-specified hypothesis's own premise.

The hypothesis rests on baseline MAX_HOLDING trades -- and specifically on
those still at their running peak when the 30-day cap fired -- being a
truncated right tail that a trailing exit would recover. This traces those
exact trades through the variant runs: same ticker, same entry date, so the
comparison is like-for-like and independent of any capacity effect.

"At its running peak at exit" is computed from real price data: the exit
close equals the highest close observed between entry and exit.
"""
import pandas as pd

from data.ingestion import get_price_df

td = pd.read_csv("trailing/out/variant_trades.csv", parse_dates=["entry_date", "exit_date"])

base = td[td.variant == "baseline"].copy()
mh = base[base.exit_reason == "MAX_HOLDING"].copy()
print(f"baseline MAX_HOLDING trades: {len(mh)}  "
      f"mean pnl {mh.pnl_pct.mean()*100:.2f}%  win rate {(mh.pnl_pct>0).mean()*100:.1f}%")

# --- which of them exited AT their running peak -------------------------
closes: dict[str, pd.Series] = {}
for t in mh.ticker.unique():
    df = get_price_df(t, days=9000)
    if not df.empty:
        s = df.set_index("date")["close"].astype(float)
        s.index = pd.to_datetime(s.index)
        closes[t] = s

at_peak = []
for r in mh.itertuples():
    s = closes.get(r.ticker)
    if s is None:
        continue
    w = s[(s.index >= r.entry_date) & (s.index <= r.exit_date)]
    if len(w) and float(w.iloc[-1]) >= float(w.max()) - 1e-9:
        at_peak.append((r.ticker, r.entry_date))
peak_keys = set(at_peak)
mh["at_peak"] = [(r.ticker, r.entry_date) in peak_keys for r in mh.itertuples()]
pk = mh[mh.at_peak]
print(f"  ... of which still at their running peak at exit: {len(pk)}  "
      f"mean pnl {pk.pnl_pct.mean()*100:.2f}%")
print("  (hypothesis cited 357 such trades at +16.1% avg)\n")

# --- follow the SAME trades through each variant ------------------------
print("=== SAME TRADES (ticker+entry_date), baseline vs variant ===")
print("Like-for-like: capacity effects excluded by construction.\n")
for v in ["T1", "T2", "T3"]:
    sub = td[td.variant == v].copy()
    sub["k"] = list(zip(sub.ticker, sub.entry_date))
    si = sub.drop_duplicates("k").set_index("k")
    for label, frame in (("all MAX_HOLDING", mh), ("MAX_HOLDING at peak", pk)):
        f = frame.copy()
        f["k"] = list(zip(f.ticker, f.entry_date))
        common = [k for k in f.k if k in si.index]
        fb = f.set_index("k").loc[common]
        fv = si.loc[common]
        d = (fv.pnl_pct - fb.pnl_pct) * 100
        better = (d > 0).mean() * 100
        print(f"{v}  {label:22s} n={len(common):5d}  "
              f"baseline {fb.pnl_pct.mean()*100:6.2f}%  ->  {v} {fv.pnl_pct.mean()*100:6.2f}%  "
              f"(delta {d.mean():+6.2f}pp, {better:.0f}% of trades improved)")
        print(f"{'':4s}{'':22s}        new exit mix: "
              f"{fv.exit_reason.value_counts().to_dict()}")
    print()
