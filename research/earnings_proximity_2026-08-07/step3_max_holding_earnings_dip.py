"""Step 3: the user's specific scenario -- a MAX_HOLDING exit that locks
in a loss (or a small gain) while the position is still mid-recovery from
an earnings-driven dip earlier in the hold, before the 30-day cap forces
the exit. Different question from max_holding_sweep_2026-08-06 (blanket
cap widening, already rejected) -- this tests one specific, evidenced
subgroup: does THIS population specifically keep climbing after the cap
forces it out, more than an ordinary net-negative MAX_HOLDING exit does?

Population: the 2,696 real MAX_HOLDING exits from the baseline trade set
(today's real config, unmodified production engine).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pandas as pd
from scipy import stats

from data.database import get_engine
from sqlalchemy import text

OUT_DIR = "research/earnings_proximity_2026-08-07"
EARNINGS_WINDOW = 3  # calendar days -- trough must be within this of an earnings date
HORIZONS = (10, 20, 30, 60, 90)

trades = pd.read_csv("research/vix_ma_giveback_2026-08-07/trades_baseline.csv", parse_dates=["entry_date", "exit_date"])
earnings = pd.read_csv(f"{OUT_DIR}/earnings_dates.csv", parse_dates=["earnings_date"])
earnings_by_ticker = {t: sorted(g["earnings_date"].tolist()) for t, g in earnings.groupby("ticker")}

mh = trades[trades["exit_reason"] == "MAX_HOLDING"].copy()
print(f"MAX_HOLDING trades: {len(mh)}", flush=True)

engine = get_engine()


def price_path(ticker, start, end):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT date, close FROM price_data WHERE ticker=:t AND date>=:s AND date<=:e ORDER BY date ASC"),
            {"t": ticker, "s": start.isoformat(), "e": end.isoformat()},
        ).fetchall()
    return [(pd.Timestamp(r[0]), float(r[1])) for r in rows]


def price_n_trading_days_after(ticker, after_date, n):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT date, close FROM price_data WHERE ticker=:t AND date>:d ORDER BY date ASC LIMIT :n"),
            {"t": ticker, "d": after_date.isoformat(), "n": n},
        ).fetchall()
    if len(rows) < n:
        return None
    return float(rows[n - 1][1])


def nearest_earnings_gap_days(ticker, date):
    dates = earnings_by_ticker.get(ticker)
    if not dates:
        return None
    diffs = [(date - d).days for d in dates]
    return min(diffs, key=abs)


rows = []
for _, tr in mh.iterrows():
    path = price_path(tr["ticker"], tr["entry_date"], tr["exit_date"])
    if len(path) < 3:
        continue
    trough_date, trough_price = min(path, key=lambda x: x[1])
    gap = nearest_earnings_gap_days(tr["ticker"], trough_date)
    earnings_dip = gap is not None and abs(gap) <= EARNINGS_WINDOW
    still_recovering = trough_price < tr["exit_price"] < tr["entry_price"]  # dipped, partly climbed back, still net negative at cap
    rows.append({
        "ticker": tr["ticker"], "entry_date": tr["entry_date"], "exit_date": tr["exit_date"],
        "entry_price": tr["entry_price"], "exit_price": tr["exit_price"], "pnl_pct": tr["pnl_pct"],
        "trough_date": trough_date, "trough_price": trough_price,
        "trough_gap_from_entry_pct": (trough_price - tr["entry_price"]) / tr["entry_price"] * 100,
        "earnings_dip": earnings_dip, "still_recovering_net_negative": still_recovering,
        "target_scenario": earnings_dip and still_recovering,
    })

df = pd.DataFrame(rows)
df.to_csv(f"{OUT_DIR}/max_holding_dip_classification.csv", index=False)
print(f"Classified {len(df)} trades", flush=True)
print(f"  earnings_dip during hold: {df['earnings_dip'].sum()} ({df['earnings_dip'].mean():.1%})", flush=True)
print(f"  still recovering, net negative at cap: {df['still_recovering_net_negative'].sum()} ({df['still_recovering_net_negative'].mean():.1%})", flush=True)
print(f"  TARGET SCENARIO (both): {df['target_scenario'].sum()} ({df['target_scenario'].mean():.1%})", flush=True)

# Control group: net-negative-at-cap MAX_HOLDING trades WITHOUT an
# earnings-proximate trough -- isolates whether earnings specifically
# matters, vs. any recovering-but-still-negative trade behaving the same.
target = df[df["target_scenario"]]
control = df[df["still_recovering_net_negative"] & ~df["earnings_dip"]]
print(f"\nTarget scenario (earnings dip + still recovering + net negative): n={len(target)}", flush=True)
print(f"Control (still recovering + net negative, NO earnings dip): n={len(control)}", flush=True)

print("\n=== Counterfactual: price N trading days after the ACTUAL MAX_HOLDING exit ===", flush=True)
for horizon in HORIZONS:
    t_recs, c_recs = [], []
    for _, tr in target.iterrows():
        fwd = price_n_trading_days_after(tr["ticker"], tr["exit_date"], horizon)
        if fwd is not None:
            t_recs.append((fwd - tr["entry_price"]) / tr["entry_price"])
    for _, tr in control.iterrows():
        fwd = price_n_trading_days_after(tr["ticker"], tr["exit_date"], horizon)
        if fwd is not None:
            c_recs.append((fwd - tr["entry_price"]) / tr["entry_price"])
    t_arr = pd.Series(t_recs)
    c_arr = pd.Series(c_recs)
    print(f"\n--- {horizon} trading days after the cap ---", flush=True)
    print(f"  TARGET (n={len(t_arr)}): mean would-be pnl%={t_arr.mean()*100:.2f}  median={t_arr.median()*100:.2f}  "
          f"pct now >entry(win)={(t_arr>0).mean():.1%}  pct reached original entry+10%={(t_arr>0.10).mean():.1%}", flush=True)
    print(f"  CONTROL (n={len(c_arr)}): mean would-be pnl%={c_arr.mean()*100:.2f}  median={c_arr.median()*100:.2f}  "
          f"pct now >entry(win)={(c_arr>0).mean():.1%}  pct reached original entry+10%={(c_arr>0.10).mean():.1%}", flush=True)
    if len(t_arr) > 5 and len(c_arr) > 5:
        u, p = stats.mannwhitneyu(t_arr, c_arr)
        print(f"  Mann-Whitney p={p:.4f}", flush=True)

print(f"\nActual pnl%% at the real cap exit -- TARGET mean={target['pnl_pct'].mean()*100:.2f} CONTROL mean={control['pnl_pct'].mean()*100:.2f}", flush=True)
