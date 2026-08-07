"""Step 4: the user's proposal -- if a position is already profitable and
a known earnings date is coming up before the position would otherwise
exit, would taking profit right before that earnings date have preserved
MORE value than what actually happened (holding through the earnings
event and whatever exit reason eventually fired)?

For every real baseline trade (5,397, today's real config, unmodified
production engine): does the hold span an earnings date? Was the position
profitable on the last trading day before that earnings date? If so,
compare that pre-earnings price against the trade's REAL, actual exit
price -- not a further forward-looking counterfactual, since the question
here is "would exiting THEN have beaten what we actually did," not "would
exiting then have beaten holding forever."
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pandas as pd
from scipy import stats

from data.database import get_engine
from sqlalchemy import text

OUT_DIR = "research/earnings_proximity_2026-08-07"
PROFIT_THRESHOLDS = (0.0, 0.02, 0.05)  # "already winning" defined as pnl_pct > this, at the pre-earnings point

trades = pd.read_csv("research/vix_ma_giveback_2026-08-07/trades_baseline.csv", parse_dates=["entry_date", "exit_date"])
earnings = pd.read_csv(f"{OUT_DIR}/earnings_dates.csv", parse_dates=["earnings_date"])
earnings_by_ticker = {t: sorted(g["earnings_date"].tolist()) for t, g in earnings.groupby("ticker")}

engine = get_engine()


def price_path(ticker, start, end):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT date, close FROM price_data WHERE ticker=:t AND date>=:s AND date<=:e ORDER BY date ASC"),
            {"t": ticker, "s": start.isoformat(), "e": end.isoformat()},
        ).fetchall()
    return [(pd.Timestamp(r[0]), float(r[1])) for r in rows]


rows = []
for _, tr in trades.iterrows():
    e_dates = earnings_by_ticker.get(tr["ticker"])
    if not e_dates:
        continue
    # Earnings dates that fall strictly inside the hold (position was
    # genuinely open across the event, not just near it at entry/exit).
    spanning = [d for d in e_dates if tr["entry_date"] < d < tr["exit_date"]]
    if not spanning:
        continue
    first_earnings = min(spanning)  # the first earnings event the hold ran into
    path = price_path(tr["ticker"], tr["entry_date"], first_earnings)
    if len(path) < 2:
        continue
    # Last trading day strictly before the earnings date.
    pre = [p for p in path if p[0] < first_earnings]
    if not pre:
        continue
    pre_date, pre_price = pre[-1]
    pre_pnl_pct = (pre_price - tr["entry_price"]) / tr["entry_price"]
    rows.append({
        "ticker": tr["ticker"], "entry_date": tr["entry_date"], "exit_date": tr["exit_date"],
        "exit_reason": tr["exit_reason"], "entry_price": tr["entry_price"],
        "first_earnings_in_hold": first_earnings, "pre_earnings_date": pre_date,
        "pre_earnings_pnl_pct": pre_pnl_pct, "actual_pnl_pct": tr["pnl_pct"],
        "preemptive_would_beat_actual": pre_pnl_pct > tr["pnl_pct"],
        "improvement_pct_points": (pre_pnl_pct - tr["pnl_pct"]) * 100,
    })

df = pd.DataFrame(rows)
df.to_csv(f"{OUT_DIR}/preemptive_profit_take.csv", index=False)
print(f"Trades whose hold spans a real earnings date: {len(df)}/{len(trades)} ({len(df)/len(trades):.1%})", flush=True)

for thresh in PROFIT_THRESHOLDS:
    sub = df[df["pre_earnings_pnl_pct"] > thresh]
    print(f"\n=== Already winning at the pre-earnings point (pnl% > {thresh*100:.0f}%): n={len(sub)} ===", flush=True)
    if sub.empty:
        continue
    print(f"  Would preemptive exit beat what actually happened? {sub['preemptive_would_beat_actual'].mean():.1%} of the time", flush=True)
    print(f"  Mean pre-earnings pnl%={sub['pre_earnings_pnl_pct'].mean()*100:.2f}  Mean actual final pnl%={sub['actual_pnl_pct'].mean()*100:.2f}", flush=True)
    print(f"  Mean improvement if preemptive={sub['improvement_pct_points'].mean():.2f}pp  median={sub['improvement_pct_points'].median():.2f}pp", flush=True)
    print(f"  By eventual real exit_reason:", flush=True)
    for reason, g in sub.groupby("exit_reason"):
        print(f"    {reason:12s} n={len(g):4d}  preemptive-wins={g['preemptive_would_beat_actual'].mean():.1%}  "
              f"mean improvement={g['improvement_pct_points'].mean():.2f}pp", flush=True)
    w, p = stats.wilcoxon(sub["pre_earnings_pnl_pct"], sub["actual_pnl_pct"])
    print(f"  Wilcoxon signed-rank (pre-earnings vs actual, paired): p={p:.6f}", flush=True)

    # Money-weighted: aggregate improvement across this population, as a
    # rough headline figure (position_gbp not carried in trades_baseline
    # for this join -- approximate via the pp improvement alone here;
    # exact GBP figure would need a full re-simulation, flagged below).
    total_pp_gain = sub["improvement_pct_points"].sum()
    print(f"  Sum of per-trade pp improvement across this population: {total_pp_gain:.1f}pp (NOT a GBP figure -- position sizes differ per trade)", flush=True)
