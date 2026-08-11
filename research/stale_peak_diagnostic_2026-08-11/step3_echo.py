"""Stale-peak diagnostic, step 3 -- where ECHO sits.

Computes ECHO's own entry-context metrics on the identical definitions used for
the 4,876 admitted positions, and places it in those distributions by
percentile. Also verifies the described price path ($22 -> ~$125 -> ~$88)
against real stored data rather than taking it as given.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis import signals                          # noqa: E402
from data.ingestion import get_price_df               # noqa: E402

LOOKBACK = signals.MOMENTUM_LOOKBACK_DAYS
DECIDED = pd.Timestamp("2026-08-06")     # EOD decide date (pending_orders.decided_at)
ENTRY = pd.Timestamp("2026-08-07")       # submission/fill date


def main() -> None:
    df = get_price_df("ECHO", days=8000).set_index("date")
    df.index = pd.to_datetime(df.index)
    close = df["close"].astype(float)
    mom = signals.compute_momentum_series(close)
    rnk = signals.compute_relative_rank_series(close)

    out = {}
    for label, dt in (("decided_2026-08-06", DECIDED), ("entry_2026-08-07", ENTRY)):
        if dt not in close.index:
            print(f"{label}: {dt.date()} not a trading day in stored data")
            continue
        i = close.index.get_loc(dt)
        lo = max(0, i - LOOKBACK)
        win = close.iloc[lo:i + 1]
        peak = float(win.max())
        peak_pos = int(win.values.argmax())
        peak_date = win.index[peak_pos]
        entry_close = float(close.iloc[i])
        rec = {
            "date": str(dt.date()),
            "close": round(entry_close, 4),
            "window_start": str(win.index[0].date()),
            "window_rows": len(win),
            "window_low": round(float(win.min()), 4),
            "peak_close": round(peak, 4),
            "peak_date": str(peak_date.date()),
            "below_peak_pct": round((peak - entry_close) / peak * 100.0, 3),
            "days_since_peak": (len(win) - 1) - peak_pos,
            "momentum_at_entry_pct": round(float(mom.iloc[i]) * 100.0, 3),
            "relative_rank_at_entry_pct": round(float(rnk.iloc[i]) * 100.0, 3),
            "close_252_rows_back": round(float(close.iloc[lo]), 4),
        }
        out[label] = rec
        print(f"\n--- ECHO @ {label} ---")
        for k, v in rec.items():
            print(f"  {k:28} {v}")

    # Percentile placement against the primary (current live ordering) trade set.
    td = pd.read_csv(HERE / "metrics_B_relative_rank.csv")
    td = td[td["below_peak_pct"].notna()]
    e = out.get("entry_2026-08-07") or out.get("decided_2026-08-06")
    pl = {}
    for field, val in (("below_peak_pct", e["below_peak_pct"]),
                       ("days_since_peak", e["days_since_peak"]),
                       ("momentum_at_entry_pct", e["momentum_at_entry_pct"])):
        pct = 100.0 * float((td[field] < val).mean())
        pl[field] = {"value": val, "percentile_among_admitted": round(pct, 2)}
        print(f"\n  ECHO {field} = {val}  -> {pct:.2f}th percentile of the "
              f"{len(td):,} admitted positions")
    out["percentiles_vs_admitted"] = pl

    # Which below_peak bucket does ECHO land in, and how did that bucket do?
    v = e["below_peak_pct"]
    bucket = ("0-5%" if v < 5 else "5-10%" if v < 10 else "10-20%" if v < 20
              else "20-30%" if v < 30 else ">=30%")
    g = td[(td["below_peak_pct"] >= {"0-5%": 0, "5-10%": 5, "10-20%": 10, "20-30%": 20, ">=30%": 30}[bucket])
           & (td["below_peak_pct"] < {"0-5%": 5, "5-10%": 10, "10-20%": 20, "20-30%": 30, ">=30%": 1e9}[bucket])]
    print(f"\n  ECHO falls in the '{bucket}' bucket: n={len(g):,}, "
          f"win {100*(g['pnl_gbp']>0).mean():.2f}%, mean P&L% {100*g['pnl_pct'].mean():.3f}%")
    out["echo_bucket"] = {"bucket": bucket, "n": int(len(g)),
                          "win_pct": round(100 * float((g["pnl_gbp"] > 0).mean()), 2),
                          "mean_pnl_pct": round(100 * float(g["pnl_pct"].mean()), 3)}

    # Verify the described path against real data, rather than assuming it.
    last3y = close[close.index >= pd.Timestamp("2023-08-01")]
    print(f"\n--- ECHO real price path (stored data) ---")
    print(f"  2025-08-07 close: {float(close.asof(pd.Timestamp('2025-08-07'))):.2f}")
    print(f"  trailing-window low  {float(last3y.min()):.2f}  on {last3y.idxmin().date()}")
    print(f"  all-time-in-window peak {out['entry_2026-08-07']['peak_close']:.2f} on "
          f"{out['entry_2026-08-07']['peak_date']}")
    print(f"  2026-08-07 close: {float(close.loc[ENTRY]):.2f}")
    out["path_check"] = {
        "close_2025_08_07": round(float(close.asof(pd.Timestamp("2025-08-07"))), 2),
        "min_since_2023_08": round(float(last3y.min()), 2),
        "min_since_2023_08_date": str(last3y.idxmin().date()),
    }

    (HERE / "echo_placement.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote echo_placement.json")


if __name__ == "__main__":
    main()
