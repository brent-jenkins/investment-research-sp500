"""Sec 11 -- prove the research cache's SHARED columns are byte-identical to
production's own analysis/backtest.py:precompute_ticker() output.

step1_build_caches.py had to reimplement precompute_ticker() to retain `high`
(which production computes ATR from and then discards). A reimplementation is
exactly the "two hand-written copies that are merely supposed to agree" shape
this project's standing invariant forbids, so it is verified rather than
trusted: for a sample of tickers, the production function is called directly
and every shared column compared elementwise.

Also confirms `high` is genuinely present and non-degenerate -- the Sec 4 hard
gate. A cache that silently carried an all-NaN `high` column would let short
stops never fire, which is a check reporting clean for a reason unrelated to
what it was checking.
"""
import pickle
import random
import sys
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis.backtest import precompute_ticker, slice_ticker   # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

START = pd.Timestamp("2007-01-01")
SHARED_COLS = ["close", "atr", "signal", "momentum", "relative_rank", "open", "low"]
N_SAMPLE = 40
SEED = 20260811


def main() -> None:
    failures = []
    for label in ("pool2_clean", "pool1"):
        with open(HERE / f"_cache_{label}.pkl", "rb") as fh:
            cache = pickle.load(fh)
        end = cache["end"]
        usable = [c for c in cache["caches"] if c is not None]
        rng = random.Random(SEED)
        sample = rng.sample(usable, min(N_SAMPLE, len(usable)))
        print(f"\n{label}: {len(usable)} usable, checking {len(sample)} sampled tickers "
              f"against production precompute_ticker()")

        n_cells = 0
        for cached in sample:
            ticker = cached["ticker"]
            prod = slice_ticker(precompute_ticker(ticker, CONFIG), START, end)
            if prod is None:
                failures.append(f"{label}/{ticker}: production returned None, research did not")
                continue
            ours, theirs = cached["frame"], prod["frame"]
            if not ours.index.equals(theirs.index):
                failures.append(f"{label}/{ticker}: index mismatch "
                                f"({len(ours)} vs {len(theirs)} rows)")
                continue
            for col in SHARED_COLS:
                a, b = ours[col], theirs[col]
                same = ((a == b) | (a.isna() & b.isna())).all()
                n_cells += len(a)
                if not same:
                    n_diff = int((~((a == b) | (a.isna() & b.isna()))).sum())
                    failures.append(f"{label}/{ticker}: column {col} differs in {n_diff} rows")

            # Sec 4 hard gate: `high` must be real, not an all-NaN placeholder,
            # and must genuinely bound the day's close/open from above.
            hi = ours["high"]
            if hi.isna().all():
                failures.append(f"{label}/{ticker}: `high` is entirely NaN")
            elif (hi < ours["close"] - 1e-9).any() or (hi < ours["open"] - 1e-9).any():
                n_bad = int(((hi < ours["close"] - 1e-9) | (hi < ours["open"] - 1e-9)).sum())
                failures.append(f"{label}/{ticker}: `high` below close/open in {n_bad} rows")
            elif (hi < ours["low"] - 1e-9).any():
                failures.append(f"{label}/{ticker}: `high` below `low`")

        print(f"  compared {n_cells:,} cells across {len(SHARED_COLS)} shared columns")

    if failures:
        print(f"\nFATAL: {len(failures)} failures:")
        for f_ in failures[:20]:
            print("  ", f_)
        sys.exit(1)
    print("\nPASS -- research cache is byte-identical to production on every shared "
          "column; `high` is present, non-degenerate, and correctly bounds OHLC.")


if __name__ == "__main__":
    main()
