"""Step 2: earnings-proximity diagnostic against the real baseline trade
population (research/vix_ma_giveback_2026-08-07/trades_baseline.csv,
5,397 trades, today's real config, unmodified production engine).

Three questions:
  A) Are STOP_LOSS exits disproportionately clustered near an earnings
     date, vs. a base rate and vs. other exit reasons?
  B) For STOP_LOSS exits that DID land near earnings: would the trade
     have recovered (gone on to be a winner) if held past the stop --
     i.e. is this "real" trend reversal or an earnings-gap false alarm?
  C) Does entering a position near an earnings date correlate with a
     worse outcome (idiosyncratic gap risk unrelated to the momentum
     thesis)?

No engine changes -- this is a pure diagnostic against the real production
trade set and stored price_data, per this project's "diagnose before
building" discipline (see the VIX/MA/giveback investigation's own Step 1
pattern for the template).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import numpy as np
import pandas as pd
from scipy import stats

from data.database import get_engine
from sqlalchemy import text

OUT_DIR = "research/earnings_proximity_2026-08-07"
WINDOWS = (1, 2, 3, 5)  # calendar-day windows around an earnings date

trades = pd.read_csv("research/vix_ma_giveback_2026-08-07/trades_baseline.csv", parse_dates=["entry_date", "exit_date"])
earnings = pd.read_csv(f"{OUT_DIR}/earnings_dates.csv", parse_dates=["earnings_date"])
earnings_by_ticker = {t: sorted(g["earnings_date"].tolist()) for t, g in earnings.groupby("ticker")}

print(f"Trades: {len(trades)}  Earnings-date rows: {len(earnings)}  Tickers with earnings data: {len(earnings_by_ticker)}", flush=True)


def nearest_earnings_gap_days(ticker: str, date: pd.Timestamp):
    """Signed calendar days from *date* to the NEAREST earnings date for
    *ticker* (negative = date is before that earnings report, positive =
    after). None if the ticker has no earnings data at all."""
    dates = earnings_by_ticker.get(ticker)
    if not dates:
        return None
    diffs = [(date - d).days for d in dates]
    return min(diffs, key=abs)


trades["exit_earnings_gap_days"] = trades.apply(lambda r: nearest_earnings_gap_days(r["ticker"], r["exit_date"]), axis=1)
trades["entry_earnings_gap_days"] = trades.apply(lambda r: nearest_earnings_gap_days(r["ticker"], r["entry_date"]), axis=1)

n_no_earnings_data = trades["exit_earnings_gap_days"].isna().sum()
print(f"Trades with no earnings data for their ticker at all: {n_no_earnings_data} ({n_no_earnings_data/len(trades):.1%})", flush=True)
scoped = trades.dropna(subset=["exit_earnings_gap_days"]).copy()
print(f"Scoped population (has earnings data): {len(scoped)}\n", flush=True)

# ----------------------------------------------------------------------
# Question A: is STOP_LOSS disproportionately near earnings?
# ----------------------------------------------------------------------
print("=== A) Exit-proximity-to-earnings by exit_reason ===", flush=True)
theoretical_base = {w: min(1.0, 4 * (2 * w + 1) / 252) for w in WINDOWS}
for w in WINDOWS:
    near = scoped["exit_earnings_gap_days"].abs() <= w
    print(f"\n--- window = +/-{w} calendar days (theoretical base rate ~{theoretical_base[w]:.1%}, assuming 4 earnings/yr, uniform) ---", flush=True)
    overall_rate = near.mean()
    print(f"  overall: {near.sum()}/{len(scoped)} = {overall_rate:.2%}", flush=True)
    for reason, g in scoped.groupby("exit_reason"):
        g_near = g["exit_earnings_gap_days"].abs() <= w
        print(f"  {reason:12s}: {g_near.sum():5d}/{len(g):5d} = {g_near.mean():.2%}", flush=True)
    # Chi-square: is STOP_LOSS's near-earnings rate different from the
    # rate among all OTHER exit reasons combined?
    sl = scoped[scoped["exit_reason"] == "STOP_LOSS"]
    other = scoped[scoped["exit_reason"] != "STOP_LOSS"]
    sl_near = (sl["exit_earnings_gap_days"].abs() <= w).sum()
    other_near = (other["exit_earnings_gap_days"].abs() <= w).sum()
    table = [[sl_near, len(sl) - sl_near], [other_near, len(other) - other_near]]
    chi2, p, _, _ = stats.chi2_contingency(table)
    print(f"  chi2 STOP_LOSS-near-rate vs other-reasons-near-rate: chi2={chi2:.2f} p={p:.4f}", flush=True)

# ----------------------------------------------------------------------
# Question B: do near-earnings STOP_LOSS trades recover if held?
# ----------------------------------------------------------------------
print("\n=== B) Counterfactual: would near-earnings STOP_LOSS trades have recovered if held? ===", flush=True)
W_EARNINGS = 2  # +/-2 calendar days, the primary window for this question
HORIZONS = (10, 20, 30)  # trading days past the actual exit

engine = get_engine()


def price_on_or_after(ticker: str, target_date: pd.Timestamp):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT close FROM price_data WHERE ticker=:t AND date>=:d ORDER BY date ASC LIMIT 1"),
            {"t": ticker, "d": target_date.isoformat()},
        ).fetchone()
    return float(row[0]) if row else None


def price_n_trading_days_after(ticker: str, exit_date: pd.Timestamp, n: int):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT date, close FROM price_data WHERE ticker=:t AND date>:d ORDER BY date ASC LIMIT :n"),
            {"t": ticker, "d": exit_date.isoformat(), "n": n},
        ).fetchall()
    if len(rows) < n:
        return None
    return float(rows[n - 1][1])


sl_trades = scoped[scoped["exit_reason"] == "STOP_LOSS"].copy()
sl_trades["near_earnings"] = sl_trades["exit_earnings_gap_days"].abs() <= W_EARNINGS
print(f"STOP_LOSS trades: {len(sl_trades)} total, {sl_trades['near_earnings'].sum()} near earnings (+/-{W_EARNINGS}d), {(~sl_trades['near_earnings']).sum()} not near", flush=True)

for horizon in HORIZONS:
    recoveries = []
    for _, tr in sl_trades.iterrows():
        fwd_price = price_n_trading_days_after(tr["ticker"], tr["exit_date"], horizon)
        if fwd_price is None:
            continue
        would_be_pnl_pct = (fwd_price - tr["entry_price"]) / tr["entry_price"]
        recoveries.append({
            "ticker": tr["ticker"], "near_earnings": tr["near_earnings"],
            "actual_pnl_pct": tr["pnl_pct"], "would_be_pnl_pct_if_held": would_be_pnl_pct,
            "would_have_been_win": would_be_pnl_pct > 0,
        })
    rdf = pd.DataFrame(recoveries)
    rdf.to_csv(f"{OUT_DIR}/stop_loss_recovery_horizon_{horizon}d.csv", index=False)
    near = rdf[rdf["near_earnings"]]
    far = rdf[~rdf["near_earnings"]]
    print(f"\n--- {horizon} trading days after the stop (n={len(rdf)}) ---", flush=True)
    print(f"  NEAR earnings (n={len(near)}): actual mean pnl%={near['actual_pnl_pct'].mean()*100:.2f}  "
          f"would-be mean pnl%-if-held={near['would_be_pnl_pct_if_held'].mean()*100:.2f}  "
          f"would-have-recovered-to-win={near['would_have_been_win'].mean():.1%}", flush=True)
    print(f"  FAR from earnings (n={len(far)}): actual mean pnl%={far['actual_pnl_pct'].mean()*100:.2f}  "
          f"would-be mean pnl%-if-held={far['would_be_pnl_pct_if_held'].mean()*100:.2f}  "
          f"would-have-recovered-to-win={far['would_have_been_win'].mean():.1%}", flush=True)
    if len(near) > 5 and len(far) > 5:
        u, p = stats.mannwhitneyu(near["would_be_pnl_pct_if_held"], far["would_be_pnl_pct_if_held"])
        print(f"  Mann-Whitney (would-be pnl%-if-held, near vs far): p={p:.4f}", flush=True)

# ----------------------------------------------------------------------
# Question C: entry-side proximity vs. outcome.
# ----------------------------------------------------------------------
print("\n=== C) Entry-proximity-to-earnings vs. outcome ===", flush=True)
for w in WINDOWS:
    near = scoped["entry_earnings_gap_days"].abs() <= w
    near_pop = scoped[near]
    far_pop = scoped[~near]
    print(f"\n--- window = +/-{w}d (n_near={len(near_pop)}, n_far={len(far_pop)}) ---", flush=True)
    print(f"  near-entry mean pnl%={near_pop['pnl_pct'].mean()*100:.2f} median={near_pop['pnl_pct'].median()*100:.2f} win_rate={(near_pop['pnl_pct']>0).mean():.1%}", flush=True)
    print(f"  far-entry  mean pnl%={far_pop['pnl_pct'].mean()*100:.2f} median={far_pop['pnl_pct'].median()*100:.2f} win_rate={(far_pop['pnl_pct']>0).mean():.1%}", flush=True)
    if len(near_pop) > 5 and len(far_pop) > 5:
        u, p = stats.mannwhitneyu(near_pop["pnl_pct"], far_pop["pnl_pct"])
        print(f"  Mann-Whitney p={p:.4f}", flush=True)

trades.to_csv(f"{OUT_DIR}/trades_with_earnings_gaps.csv", index=False)
print("\nWrote trades_with_earnings_gaps.csv", flush=True)
