"""Compare the hand-computed top-40 lists (step2b) against the engine's own
ACTUAL held positions on the same dates, reconstructed from a real trades
CSV (entry_date <= dt < exit_date). This is the direct engine-vs-independent-
computation comparison Section 12's verification item 2 calls for.
"""
import json

import pandas as pd

OUT_DIR = "research/cross_sectional_momentum_prereg_2026-08-09"
CHECK_DATES = ["2007-08-01", "2008-11-03", "2017-03-01", "2020-06-01", "2022-08-01"]


def held_on_date(trades_csv, dt):
    df = pd.read_csv(trades_csv)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    dt = pd.Timestamp(dt)
    held = df[(df["entry_date"] <= dt) & (df["exit_date"] > dt)]
    return set(held["ticker"])


def compare(pool_label, arm_label, floor_variant):
    trades_csv = f"{OUT_DIR}/trades_{pool_label}_{arm_label}.csv"
    try:
        pd.read_csv(trades_csv, nrows=1)
    except FileNotFoundError:
        print(f"[{pool_label}/{arm_label}] trades file not ready yet, skipping", flush=True)
        return
    print(f"\n=== {pool_label} / Arm {arm_label} (floor variant: {floor_variant}) ===", flush=True)
    for date_str in CHECK_DATES:
        with open(f"{OUT_DIR}/handcheck_{pool_label}_{date_str}.json") as fh:
            hc = json.load(fh)
        hand_top_n = set(hc[floor_variant])
        engine_held = held_on_date(trades_csv, date_str)
        # A rebalance date's hand-computed top-40 is the ELIGIBLE set; the
        # engine's actual held set on that exact date reflects entries that
        # succeeded (real row present, sizing viable, capacity available) --
        # a strict subset relationship is expected, not exact equality,
        # whenever capacity/sizing/no-real-row-today constraints bind.
        # Report engine-held tickers NOT in hand_top_n (would be a real bug --
        # engine holding something it shouldn't be eligible for) separately
        # from hand_top_n tickers not held (expected -- capacity/timing).
        held_but_not_eligible = engine_held - hand_top_n
        eligible_but_not_held = hand_top_n - engine_held
        print(f"{date_str}: hand-eligible={len(hand_top_n)} engine-held={len(engine_held)} "
              f"held-but-NOT-eligible(BUG if >0)={len(held_but_not_eligible)} "
              f"eligible-but-not-held(expected, capacity)={len(eligible_but_not_held)}", flush=True)
        if held_but_not_eligible:
            print(f"  *** POTENTIAL BUG *** held_but_not_eligible: {held_but_not_eligible}", flush=True)


if __name__ == "__main__":
    for pool_label in ["pool1", "pool2_clean"]:
        compare(pool_label, "B", "top_n_no_floor")
        compare(pool_label, "C", "top_n_no_floor")
        compare(pool_label, "D", "top_n_with_floor")
