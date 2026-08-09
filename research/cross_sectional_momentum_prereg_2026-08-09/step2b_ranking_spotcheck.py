"""Verification item 2 (PREREGISTRATION.md Section 12): hand-compute the
top-40 (and top-40-with-floor, for D) directly from raw price_data for five
real rebalance dates spanning different regimes, independent of the engine's
own code, and compare against the engine's actual admitted list for those
dates (read from the trades/panel CSVs once the real runs complete).

Regimes: pre-2008, inside the 2008-09 crisis block, 2015-2020 calm,
2020 (COVID), 2022 (rate-hike drawdown).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg")
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg\research\cross_sectional_momentum_prereg_2026-08-09")

import pickle

import pandas as pd

from analysis import universe, crisis
from analysis.decision import passes_momentum_floor, resolve_min_momentum_floor
from cross_sectional_signal import compute_cross_sectional_rank_series
import yaml

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/cross_sectional_momentum_prereg_2026-08-09"
CHECK_DATES = [
    pd.Timestamp("2007-08-01"),   # pre-2008
    pd.Timestamp("2008-11-03"),   # inside the 2008-09 crisis block
    pd.Timestamp("2017-03-01"),   # 2015-2020 calm
    pd.Timestamp("2020-06-01"),   # 2020 (post-COVID-crash rebound window)
    pd.Timestamp("2022-08-01"),   # 2022 rate-hike drawdown
]


def hand_compute_for_pool(pool_label, dates, top_n=40):
    with open(f"{OUT_DIR}/_cache_{pool_label}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = {c["ticker"]: c for c in data["caches"] if c is not None}
    gspc_close = data["gspc_close_full"]
    raw_crisis = crisis.compute_raw_trigger(gspc_close)
    entry_blocked = crisis.extend_trigger(raw_crisis)
    min_floor = resolve_min_momentum_floor(CONFIG)

    # Compute each ticker's rank series ONCE, reuse .asof() across all dates.
    rank_series_by_ticker = {}
    for ticker, c in caches.items():
        close = c["frame"]["close"].astype(float)
        rank_series_by_ticker[ticker] = compute_cross_sectional_rank_series(close)

    results = {}
    for dt in dates:
        blocked_today = bool(entry_blocked.asof(dt)) if len(entry_blocked) else False
        cand_no_floor, cand_floor = [], []
        for ticker, rank_series in rank_series_by_ticker.items():
            val = rank_series.asof(dt) if len(rank_series) else None
            if val is None or pd.isna(val):
                continue
            if not universe.is_member(ticker, dt.date()):
                continue
            if blocked_today:
                continue
            cand_no_floor.append((ticker, float(val)))
            if passes_momentum_floor(float(val), min_floor):
                cand_floor.append((ticker, float(val)))
        cand_no_floor.sort(key=lambda x: -x[1])
        cand_floor.sort(key=lambda x: -x[1])
        results[dt] = {
            "entry_blocked": blocked_today,
            "top_n_no_floor": [t for t, _ in cand_no_floor[:top_n]],
            "top_n_with_floor": [t for t, _ in cand_floor[:top_n]],
        }
    return results


def main():
    import json
    for pool_label in ["pool1", "pool2_clean"]:
        print(f"\n=== Pool: {pool_label} ===", flush=True)
        results = hand_compute_for_pool(pool_label, CHECK_DATES, 40)
        for dt, r in results.items():
            print(f"{dt.date()}: entry_blocked={r['entry_blocked']}, "
                  f"hand-computed top-40 (no floor) n={len(r['top_n_no_floor'])}, "
                  f"top-40 (with floor) n={len(r['top_n_with_floor'])}", flush=True)
            out = {"date": str(dt.date()), "pool": pool_label, **r}
            with open(f"{OUT_DIR}/handcheck_{pool_label}_{dt.date()}.json", "w") as fh:
                json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()
