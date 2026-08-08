"""Positive control, run BEFORE anything else per explicit instruction: KDP/MO/WY
have permanent unadjusted close cliffs (research/split_cliff_remediation_2026-08-08/
REPORT.md) that adj_close does not. If adj_close-based momentum doesn't show a
materially different (and much less distorted) reading for these three during
their affected windows, the wiring is wrong and nothing else should be trusted.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import pandas as pd

from analysis import signals
from data.ingestion import get_price_df

CASES = [
    ("KDP", "2018-07-10"),
    ("MO", "2008-03-31"),
    ("WY", "2010-07-20"),
]


def main():
    for ticker, event_date in CASES:
        df = get_price_df(ticker, days=8000)
        df = df.set_index("date")
        df.index = pd.to_datetime(df.index)
        close = df["close"].astype(float)
        adj_close = df["adj_close"].astype(float)

        mom_close = signals.compute_momentum_series(close)
        mom_adj = signals.compute_momentum_series(adj_close)

        event = pd.Timestamp(event_date)
        # window: from event date to ~252 trading days after (the full period
        # the cliff can still be inside the trailing 12-month lookback)
        window = mom_close.index[(mom_close.index >= event)]
        window = window[:260]

        wc = mom_close.loc[window]
        wa = mom_adj.loc[window]
        n_pos_close = int((wc > 0).sum())
        n_pos_adj = int((wa > 0).sum())
        print(f"\n=== {ticker} (event {event_date}) ===")
        print(f"  close-based momentum:    min={wc.min():.3f} max={wc.max():.3f}  positive days: {n_pos_close}/{len(wc)}")
        print(f"  adj_close-based momentum: min={wa.min():.3f} max={wa.max():.3f}  positive days: {n_pos_adj}/{len(wa)}")
        # spot values right at/after the event
        near = window[:5]
        print(f"  first 5 rows after event -- close_mom: {[round(v,3) for v in wc.loc[near].tolist()]}")
        print(f"  first 5 rows after event -- adj_mom:   {[round(v,3) for v in wa.loc[near].tolist()]}")


if __name__ == "__main__":
    main()
