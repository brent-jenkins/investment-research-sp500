"""Two direct tests of whether the paired instrument's low overlap and low
shared-key win rate mean what they appear to mean.

TEST 1 -- EXPECTED OVERLAP BY CONSTRUCTION.
  If two arms hold 712 and 4,876 positions over the same window from the same
  eligible pool, what shared-key count would RANDOM assignment produce? A
  randomization null, conditioned on the dates the sparse arm actually admitted
  on: for each such date, draw as many tickers at random from that date's real
  gate-passing eligible set as the sparse arm admitted, and count how many land
  on the dense arm's actual admissions that date. 1,000 draws -> null
  distribution. If the observed overlap sits at or above that null, the low
  overlap is FORCED BY CONSTRUCTION and the walk-away clause must not fire on it.

TEST 2 -- IS THE SHARED-KEY SUBSET A BIASED SLICE?
  Hypothesis to test: positions BOTH arms hold are by definition ones the 30-day
  cap never bound on -- baseline exited them early for some other reason -- so
  they are systematically the shorter, less profitable holds, i.e. exactly the
  population where unlimited holding has the least to offer. If true, the
  shared-key win rate is measuring the wrong slice.
  Compared directly: baseline's own exit-reason mix, holding days and P&L on
  shared vs non-shared keys.

Usage: python step5_overlap_null_and_slice.py <pool> <cell_id>
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis import universe, crisis                              # noqa: E402
from analysis.decision import (passes_entry_gate, passes_momentum_floor,   # noqa: E402
                               resolve_min_momentum_floor)

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

N_DRAWS = 1000
SEED = 20260811
BASELINE = "h30"


def eligible_sets(cache, gspc_close, dates_needed):
    """Real gate-passing eligible set per date (signal, 2% floor, membership,
    not crisis-blocked) -- the same conditions the engine itself applies."""
    min_floor = resolve_min_momentum_floor(CONFIG)
    blocked = crisis.extend_trigger(crisis.compute_raw_trigger(gspc_close))
    need = set(dates_needed)
    elig = {d: [] for d in need}
    for c in cache["caches"]:
        if c is None:
            continue
        tk, fr = c["ticker"], c["frame"]
        idx = fr.index.intersection(pd.DatetimeIndex(sorted(need)))
        if len(idx) == 0:
            continue
        sub = fr.loc[idx]
        for dt, row in zip(sub.index, sub.itertuples()):
            if bool(blocked.get(dt, False)):
                continue
            sig = bool(row.signal) if pd.notna(row.signal) else False
            mv = float(row.momentum) if pd.notna(row.momentum) else None
            if not passes_entry_gate(sig_on=sig, is_member=universe.is_member(tk, dt.date()),
                                     entry_blocked=False):
                continue
            if not passes_momentum_floor(mv, min_floor):
                continue
            elig[dt].append(tk)
    return elig


def main() -> None:
    pool, cell = sys.argv[1], sys.argv[2]
    base = pd.read_csv(HERE / f"trades_{pool}_{BASELINE}.csv")
    arm = pd.read_csv(HERE / f"trades_{pool}_{cell}.csv")
    for f in (base, arm):
        f["entry_date"] = pd.to_datetime(f["entry_date"])

    bkeys = set(base["ticker"] + "|" + base["entry_date"].dt.strftime("%Y-%m-%d"))
    akeys = set(arm["ticker"] + "|" + arm["entry_date"].dt.strftime("%Y-%m-%d"))
    observed = len(bkeys & akeys)
    print(f"{pool}/{cell}: baseline {len(base):,} trades, arm {len(arm):,} trades, "
          f"observed shared keys = {observed}")

    # ---------------- TEST 1 ----------------
    with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
        cache = pickle.load(fh)
    arm_dates = sorted(set(arm["entry_date"]))
    print(f"\n### TEST 1 -- expected overlap by construction "
          f"({len(arm_dates)} dates the arm admitted on)")
    elig = eligible_sets(cache, cache["gspc_close"], arm_dates)

    base_by_date = base.groupby("entry_date")["ticker"].apply(set).to_dict()
    arm_by_date = arm.groupby("entry_date")["ticker"].apply(set).to_dict()

    rng = np.random.default_rng(SEED)
    draws = []
    usable = [d for d in arm_dates if len(elig.get(d, [])) >= 1]
    for _ in range(N_DRAWS):
        tot = 0
        for d in usable:
            pool_d = elig[d]
            k = min(len(arm_by_date[d]), len(pool_d))
            if k == 0:
                continue
            pick = rng.choice(len(pool_d), size=k, replace=False)
            chosen = {pool_d[i] for i in pick}
            tot += len(chosen & base_by_date.get(d, set()))
        draws.append(tot)
    draws = np.array(draws)
    pct = float((draws < observed).mean() * 100)
    print(f"  eligible-set size on those dates: median {np.median([len(elig[d]) for d in usable]):.0f}, "
          f"mean {np.mean([len(elig[d]) for d in usable]):.1f}")
    print(f"  baseline admits on those dates: {sum(len(base_by_date.get(d, set())) for d in usable):,}")
    print(f"  NULL expected shared keys: mean {draws.mean():.1f}  sd {draws.std():.1f}  "
          f"p5 {np.percentile(draws,5):.0f}  p95 {np.percentile(draws,95):.0f}")
    print(f"  OBSERVED {observed}  ->  {pct:.1f}th percentile of the null")
    verdict1 = ("AT OR ABOVE the null -- low overlap is FORCED BY CONSTRUCTION, "
                "the clause must not fire on it" if observed >= np.percentile(draws, 5)
                else "BELOW the null -- overlap is lower than random assignment would give")
    print(f"  => {verdict1}")

    # ---------------- TEST 2 ----------------
    print(f"\n### TEST 2 -- is the shared-key subset a biased slice?")
    base["key"] = base["ticker"] + "|" + base["entry_date"].dt.strftime("%Y-%m-%d")
    shared_mask = base["key"].isin(akeys)
    sh, nsh = base[shared_mask], base[~shared_mask]
    print(f"  baseline trades: shared with arm {len(sh):,}   not shared {len(nsh):,}")
    rows = []
    for name, g in (("SHARED (both arms held)", sh), ("NOT shared (baseline only)", nsh)):
        mix = (g["exit_reason"].value_counts(normalize=True) * 100).round(2).to_dict()
        r = {"subset": name, "n": int(len(g)),
             "mean_hold_days": round(float(g["holding_days"].mean()), 2),
             "median_hold_days": float(g["holding_days"].median()),
             "win_pct": round(100 * float((g["pnl_gbp"] > 0).mean()), 2),
             "mean_pnl_pct": round(100 * float(g["pnl_pct"].mean()), 3),
             "median_pnl_pct": round(100 * float(g["pnl_pct"].median()), 3),
             "exit_mix_pct": mix}
        rows.append(r)
        print(f"  {name:>28}: n={r['n']:>5}  hold {r['mean_hold_days']:>6.1f}d "
              f"(med {r['median_hold_days']:>4.0f})  win {r['win_pct']:>5.2f}%  "
              f"meanP {r['mean_pnl_pct']:>+7.3f}%  medP {r['median_pnl_pct']:>+7.3f}%")
        print(f"  {'':>28}  baseline exit mix: {mix}")

    mh_share_shared = rows[0]["exit_mix_pct"].get("MAX_HOLDING", 0.0)
    mh_share_other = rows[1]["exit_mix_pct"].get("MAX_HOLDING", 0.0)
    print(f"\n  MAX_HOLDING share of baseline exits: shared {mh_share_shared}% vs "
          f"non-shared {mh_share_other}%")
    verdict2 = ("BIASED SLICE CONFIRMED -- the shared set is systematically the population "
                "the 30-day cap did NOT bind on, i.e. where unlimited holding has least to offer"
                if mh_share_shared < mh_share_other else
                "NOT a biased slice in the predicted direction")
    print(f"  => {verdict2}")

    # shared-key delta distribution, incl. the median the headline omitted
    a2 = arm.copy()
    a2["key"] = a2["ticker"] + "|" + a2["entry_date"].dt.strftime("%Y-%m-%d")
    m = sh.set_index("key")["pnl_pct"].to_dict()
    a2 = a2[a2["key"].isin(m)]
    delta = (a2["pnl_pct"] - a2["key"].map(m)) * 100
    print(f"\n  shared-key delta (arm - baseline), pnl_pct points:")
    print(f"    n={len(delta):,}  better={100*float((delta>0).mean()):.2f}%  "
          f"mean={delta.mean():+.3f}  median={delta.median():+.3f}  "
          f"p10={delta.quantile(.1):+.2f}  p90={delta.quantile(.9):+.2f}  "
          f"sum={delta.sum():+.1f}")

    out = {"pool": pool, "cell": cell, "observed_shared": observed,
           "null_mean": round(float(draws.mean()), 2), "null_sd": round(float(draws.std()), 2),
           "null_p5": float(np.percentile(draws, 5)), "null_p95": float(np.percentile(draws, 95)),
           "observed_percentile_of_null": round(pct, 2), "test1_verdict": verdict1,
           "slice": rows, "test2_verdict": verdict2,
           "shared_delta": {"n": int(len(delta)),
                            "better_pct": round(100 * float((delta > 0).mean()), 2),
                            "mean": round(float(delta.mean()), 4),
                            "median": round(float(delta.median()), 4),
                            "sum": round(float(delta.sum()), 2)}}
    (HERE / f"overlap_null_{pool}_{cell}.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote overlap_null_{pool}_{cell}.json")


if __name__ == "__main__":
    main()
