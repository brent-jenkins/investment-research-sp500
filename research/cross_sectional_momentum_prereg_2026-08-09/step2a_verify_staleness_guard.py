"""Verification item 4 (PREREGISTRATION.md Section 12): the new 12-minus-1
momentum computation must carry the same anchor-gap staleness guard
compute_momentum_series() has. Two checks:

(a) Synthetic multi-month gap, mirroring the exact 2026-08-01 regression
    this project already has for compute_momentum_series() (tests/test_signals.py's
    _gapped_close_series()) -- confirms a stale anchor produces NaN, not a
    real, wrong, non-NaN number, for BOTH the 1-month and 12-month anchor.
(b) Real-data scan across the full candidate pool: how often does the guard
    actually fire on real price history. Reported as a count either way --
    a zero count is plausible (most tickers have gapless history) and not
    itself a red flag.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg")

import pickle

import pandas as pd

from cross_sectional_signal import compute_cross_sectional_rank_series


def _gapped_close_series():
    """Identical construction to tests/test_signals.py's own fixture --
    300 business days, a ~16-month gap, 300 more business days."""
    dates1 = pd.bdate_range("2015-01-01", periods=300)
    dates2 = pd.bdate_range("2017-06-01", periods=300)
    idx = pd.DatetimeIndex(list(dates1) + list(dates2))
    vals = list(range(100, 400)) + list(range(400, 700))
    return pd.Series(vals, index=idx)


def check_a_synthetic_gap():
    close = _gapped_close_series()
    rank = compute_cross_sectional_rank_series(close)
    # Row 300 is the first row after the gap. Its naive 252-rows-back anchor
    # (row 48) and 21-rows-back anchor (row 279) -- the 252 anchor is the
    # one that crosses the gap and must be caught.
    ok_300 = pd.isna(rank.iloc[300])
    ok_310 = pd.isna(rank.iloc[310])
    print(f"[4a] rank.iloc[300] is NaN: {ok_300} (expected True)")
    print(f"[4a] rank.iloc[310] is NaN: {ok_310} (expected True)")
    # 252 genuine trading days after the gap ends, signal should resume.
    ok_recover = pd.notna(rank.iloc[300 + 252])
    print(f"[4a] rank.iloc[300+252] is real (recovered): {ok_recover} (expected True)")
    passed = ok_300 and ok_310 and ok_recover
    print(f"[4a] SYNTHETIC GAP CHECK: {'PASS' if passed else 'FAIL'}")
    return passed


def check_b_real_data_scan():
    total_fired = 0
    total_dates_checked = 0
    tickers_with_any_fire = 0
    for pool_path in [
        "research/cross_sectional_momentum_prereg_2026-08-09/_cache_pool1.pkl",
        "research/cross_sectional_momentum_prereg_2026-08-09/_cache_pool2_clean.pkl",
    ]:
        with open(pool_path, "rb") as fh:
            data = pickle.load(fh)
        for c in data["caches"]:
            if c is None:
                continue
            close = c["frame"]["close"].astype(float)
            rank = compute_cross_sectional_rank_series(close)
            # "fired" = NaN in the rank series at a point where BOTH raw
            # shifts (p1, p12) would have had enough ROWS to compute a
            # naive value -- i.e. genuinely caught a gap, not just early
            # history. Approximate: rows from index 252 onward.
            if len(close) > 252:
                tail = rank.iloc[252:]
                raw_p1 = close.shift(21).iloc[252:]
                raw_p12 = close.shift(252).iloc[252:]
                raw_computable = raw_p1.notna() & raw_p12.notna()
                fired = tail.isna() & raw_computable
                n_fired = int(fired.sum())
                total_fired += n_fired
                total_dates_checked += len(tail)
                if n_fired > 0:
                    tickers_with_any_fire += 1
        print(f"[4b] {pool_path}: {len(data['caches'])} tickers scanned", flush=True)

    print(f"[4b] Total dates checked (post-252-row warmup, across both pools): {total_dates_checked}")
    print(f"[4b] Total staleness-guard fires: {total_fired}")
    print(f"[4b] Tickers with at least one fire: {tickers_with_any_fire}")


if __name__ == "__main__":
    a_passed = check_a_synthetic_gap()
    check_b_real_data_scan()
    print(f"\nOverall staleness-guard verification: {'PASS' if a_passed else 'FAIL -- STOP, do not trust the engine'}")
