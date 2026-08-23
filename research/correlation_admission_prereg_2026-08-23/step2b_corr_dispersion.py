"""What does k = 1 actually MEAN? -- the correlation distribution's own
dispersion, measured before the gate is read rather than after.

Sec 2.3 defines k in rank space: "k = 1 means correlation matters exactly as
much as momentum rank". That statement is only as meaningful as the spread of
the underlying quantity. If within-day candidate-to-book correlation clusters
tightly -- most candidates between, say, 0.3 and 0.5 -- then the percentile
conversion stretches a narrow band across the full [0,1] range, and k is
amplifying small real differences rather than weighing large ones. That is not
wrong, and it is not a defect in the design: percentile conversion is what
makes k scale-free and interpretable at all. But it changes the reading of
every arm, and it is far better known BEFORE the gate than after.

Reports, per pool, over a sample of real trading days with a real held book:
  * within-day dispersion of corr_raw (sd, IQR, p90-p10, full range)
  * the RAW correlation difference a full percentile swing corresponds to --
    i.e. what k = 1 is actually trading a full rank position against
  * the same for a 1-decile percentile move, which is closer to the size of
    move the admission boundary actually sees

Read-only. Computes no return metric.
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from corr_engine import CORR_WINDOW, CorrelationBook  # noqa: E402

SAMPLE_EVERY = 61          # ~80 sampled days across the walk
HELD_SIZE = 14             # the book's own realised mean concurrent count


def main() -> None:
    out = {}
    for pool in ("pool2_clean", "pool1"):
        path = HERE / f"_cache_{pool}.pkl"
        if not path.exists():
            continue
        with open(path, "rb") as fh:
            cache = pickle.load(fh)
        caches = cache["caches"]
        dates = None
        for c in caches:
            if c is None:
                continue
            dates = c["frame"].index if dates is None else dates.union(c["frame"].index)
        book = CorrelationBook(caches, dates)

        per_day = []
        for di in range(CORR_WINDOW + 260, len(dates), SAMPLE_EVERY):
            day = dates[di]
            live = [c["ticker"] for c in caches
                    if c is not None and day in c["frame"].index]
            if len(live) < HELD_SIZE + 60:
                continue
            held = live[:HELD_SIZE]
            cand = live[HELD_SIZE:]
            cm = book.corr_to_held(di, cand, held)
            v = np.array([x for x in cm.values() if np.isfinite(x)])
            if v.size < 40:
                continue
            per_day.append({
                "date": str(day.date()), "n": int(v.size),
                "min": float(v.min()), "p10": float(np.percentile(v, 10)),
                "p25": float(np.percentile(v, 25)), "med": float(np.median(v)),
                "p75": float(np.percentile(v, 75)), "p90": float(np.percentile(v, 90)),
                "max": float(v.max()), "sd": float(v.std(ddof=1)),
                "iqr": float(np.percentile(v, 75) - np.percentile(v, 25)),
                "p90_p10": float(np.percentile(v, 90) - np.percentile(v, 10)),
                "full_range": float(v.max() - v.min()),
            })
        if not per_day:
            out[pool] = {"error": "no usable sampled days"}
            continue

        df = pd.DataFrame(per_day)
        df.to_csv(HERE / f"corr_dispersion_{pool}.csv", index=False)
        agg = {c: round(float(df[c].mean()), 4) for c in
               ("min", "p10", "p25", "med", "p75", "p90", "max", "sd", "iqr", "p90_p10", "full_range")}
        agg["days_sampled"] = int(len(df))
        agg["mean_candidates_per_day"] = round(float(df["n"].mean()), 1)

        # What k = 1 trades: a FULL percentile swing (0 -> 1) is, in raw
        # correlation terms, the day's full range. A one-decile move is
        # roughly (p90 - p10) / 8 per decile in the bulk of the distribution.
        agg["raw_corr_per_full_percentile_swing"] = agg["full_range"]
        agg["raw_corr_per_decile_in_bulk"] = round(agg["p90_p10"] / 8.0, 4)
        agg["interpretation"] = (
            "At k=1 a candidate at the day's LEAST-correlated extreme gains a full "
            "1.0 of adjusted score over one at the MOST-correlated extreme, and that "
            f"extreme-to-extreme gap corresponds to {agg['full_range']:.3f} of raw "
            f"correlation. A one-decile percentile move -- closer to what the "
            f"admission boundary actually sees -- corresponds to about "
            f"{agg['raw_corr_per_decile_in_bulk']:.4f} of raw correlation."
        )
        out[pool] = agg

    (HERE / "step2b_corr_dispersion.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    for pool, a in out.items():
        print(f"\n=== {pool}")
        if "error" in a:
            print("  ", a["error"]); continue
        print(f"  days sampled {a['days_sampled']}, mean {a['mean_candidates_per_day']} candidates/day")
        print(f"  within-day corr_raw:  min {a['min']:.3f}  p10 {a['p10']:.3f}  p25 {a['p25']:.3f}  "
              f"med {a['med']:.3f}  p75 {a['p75']:.3f}  p90 {a['p90']:.3f}  max {a['max']:.3f}")
        print(f"  dispersion         :  sd {a['sd']:.4f}  IQR {a['iqr']:.4f}  "
              f"p90-p10 {a['p90_p10']:.4f}  range {a['full_range']:.4f}")
        print(f"  {a['interpretation']}")


if __name__ == "__main__":
    main()
