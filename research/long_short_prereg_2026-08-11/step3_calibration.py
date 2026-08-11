"""Sec 14 -- PRE-RUN CALIBRATION. Runs BEFORE any arm, on independent
variables only: no arm results, no outcome data, no P&L anywhere in this file.

Three parts, each with its reading pre-committed in the pre-registration:

  14.1 HTB flag frequency      -- expected 2-8%; >15% => recalibrate; <0.5% =>
                                  borrow model not meaningfully tested.
  14.2 Rally-breaker firing    -- expected 3-12 episodes for PRIMARY; 0 fires =>
                                  breaker untested, NOT safe; >25 => not a
                                  breaker; no 2009 fire => design failure.
  14.3 Short candidate supply  -- determines whether Arm C is testable at all
                                  or is a throttled long book.

Whatever these report is what gets frozen. Thresholds are not tuned against
anything downstream.
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

import yaml                                                      # noqa: E402
from analysis import universe                                    # noqa: E402
from analysis.decision import resolve_min_momentum_floor         # noqa: E402
import ls_primitives as lsp                                      # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

MOMENTUM_CRASH_START = pd.Timestamp("2009-03-09")
MOMENTUM_CRASH_END = pd.Timestamp("2009-09-30")


def calibrate_rally_breaker(gspc_close: pd.Series) -> dict:
    """14.2 -- ^GSPC only. No pool data, no arm results."""
    print("=" * 78)
    print("14.2  RALLY BREAKER -- firing dates on ^GSPC alone")
    print("=" * 78)
    out = {}
    for name, cfg in lsp.SENSITIVITY_CONFIGS.items():
        raw = lsp.compute_rally_raw_trigger(gspc_close, cfg["dd_gate"], cfg["r5"])
        fire_dates = list(raw[raw].index)
        # Group consecutive/near-consecutive fires into episodes (>=10 trading
        # days apart starts a new episode) so "how often does this engage"
        # is not inflated by a single rebound firing on several adjacent days.
        episodes, prev = [], None
        for d in fire_dates:
            if prev is None or (raw.index.get_loc(d) - raw.index.get_loc(prev)) > 10:
                episodes.append([d])
            else:
                episodes[-1].append(d)
            prev = d
        blocked = lsp.extend_rally_trigger(raw, cfg["cooldown"])
        in_2009 = [d for d in fire_dates
                   if MOMENTUM_CRASH_START <= d <= MOMENTUM_CRASH_END]
        out[name] = {
            "config": cfg,
            "n_fire_days": len(fire_dates),
            "n_episodes": len(episodes),
            "fire_dates": [str(d.date()) for d in fire_dates],
            "episode_starts": [str(e[0].date()) for e in episodes],
            "pct_days_short_entry_blocked": round(100.0 * float(blocked.mean()), 2),
            "fires_in_2009_crash_window": [str(d.date()) for d in in_2009],
        }
        tag = " <== PRIMARY" if name == "PRIMARY" else ""
        print(f"\n{name}{tag}: dd<={cfg['dd_gate']:.2f}, r5>={cfg['r5']:.2f}, "
              f"cooldown={cfg['cooldown']}d")
        print(f"  fire days: {len(fire_dates)}   episodes: {len(episodes)}   "
              f"short entries blocked on {out[name]['pct_days_short_entry_blocked']}% of days")
        print(f"  episode starts: {out[name]['episode_starts']}")
        print(f"  fires within 2009-03-09..2009-09-30: {out[name]['fires_in_2009_crash_window'] or 'NONE'}")

    p = out["PRIMARY"]
    print("\n--- PRIMARY reading, per the pre-committed rules (Sec 6.4) ---")
    if p["n_fire_days"] == 0:
        print("  *** 0 FIRES: breaker is INERT. The rally-breaker design element is")
        print("      UNTESTED. Short-sleeve results must NOT be described as having")
        print("      survived momentum-crash risk. Silence is not safety.")
    elif p["n_episodes"] > 25:
        print(f"  *** {p['n_episodes']} EPISODES > 25: this is a trading rule, not a rare")
        print("      breaker. RE-SPECIFY before running any arm.")
    else:
        print(f"  OK: {p['n_episodes']} episodes (expected 3-12).")
    if not p["fires_in_2009_crash_window"]:
        print("  *** NO 2009 FIRE: the breaker failed at the one case it was")
        print("      explicitly designed for. DESIGN FAILURE -- report prominently.")
    else:
        print(f"  OK: fires in the 2009 momentum-crash window "
              f"({len(p['fires_in_2009_crash_window'])} day(s)).")
    return out


def calibrate_htb_and_supply(label: str, cache: dict, gspc_close: pd.Series) -> dict:
    """14.1 + 14.3 -- short-eligible candidate-days and their HTB share.

    'Short-eligible' means the Sec 3 gate WITHOUT the borrow-adjusted floor
    (which depends on the HTB flag being calibrated here) and without the
    rally block: momentum < 0, momentum <= -min_momentum_pct, is_member.
    Using the plain 2% floor here avoids a circularity where the flag's
    frequency is measured on a population the flag itself defined.
    """
    print("\n" + "=" * 78)
    print(f"14.1 / 14.3  HTB FREQUENCY AND SHORT SUPPLY -- {label}")
    print("=" * 78)
    min_floor = resolve_min_momentum_floor(CONFIG)
    raw_crisis = None

    per_date_counts: dict = {}
    per_date_htb: dict = {}
    n_eligible = 0
    n_htb = 0
    htb_leg_counts = {"price": 0, "drawdown": 0, "vol": 0}

    for cached in cache["caches"]:
        if cached is None:
            continue
        ticker = cached["ticker"]
        frame = cached["frame"]
        mom = frame["momentum"]
        elig_mask = (mom < 0) & (mom <= -min_floor)
        if not elig_mask.any():
            continue
        sub = frame[elig_mask]
        for dt, row in zip(sub.index, sub.itertuples()):
            d = dt.date()
            if not universe.is_member(ticker, d):
                continue
            n_eligible += 1
            per_date_counts[dt] = per_date_counts.get(dt, 0) + 1
            htb = lsp.is_hard_to_borrow(row.close, row.dd252, row.vol60)
            if htb:
                n_htb += 1
                per_date_htb[dt] = per_date_htb.get(dt, 0) + 1
                if row.close < lsp.HTB_PRICE_THRESHOLD:
                    htb_leg_counts["price"] += 1
                if not pd.isna(row.dd252) and row.dd252 <= lsp.HTB_DRAWDOWN_THRESHOLD:
                    htb_leg_counts["drawdown"] += 1
                if not pd.isna(row.vol60) and row.vol60 >= lsp.HTB_VOL_THRESHOLD:
                    htb_leg_counts["vol"] += 1

    htb_pct = 100.0 * n_htb / n_eligible if n_eligible else 0.0
    all_dates = pd.DatetimeIndex(sorted(set(gspc_close.index)))
    counts = pd.Series({d: per_date_counts.get(d, 0) for d in all_dates})
    counts = counts[counts.index >= pd.Timestamp("2007-01-01")]

    print(f"\n14.1  Short-eligible candidate-days: {n_eligible:,}")
    print(f"      HTB-flagged: {n_htb:,}  ({htb_pct:.2f}%)")
    print(f"      leg hit counts (non-exclusive): {htb_leg_counts}")
    if htb_pct > 15.0:
        print("      *** >15%: thresholds MIS-CALIBRATED for an S&P 500 universe.")
        print("          Re-set on the flag's own distribution and re-freeze.")
    elif htb_pct < 0.5:
        print("      *** <0.5%: HTB tier effectively INERT. The borrow-cost model")
        print("          was NOT meaningfully tested at the tier that matters.")
    else:
        print("      OK: within/near the expected 2-8% band.")

    zero_days = int((counts == 0).sum())
    print(f"\n14.3  Short candidates per trading day (2007-01-01 onward, "
          f"{len(counts):,} days):")
    print(f"      mean {counts.mean():.1f}   median {counts.median():.0f}   "
          f"p10 {counts.quantile(0.10):.0f}   p90 {counts.quantile(0.90):.0f}   "
          f"max {counts.max():.0f}")
    print(f"      days with ZERO short candidates: {zero_days:,} "
          f"({100.0*zero_days/len(counts):.2f}%)")
    by_year = counts.groupby(counts.index.year).agg(["mean", "min", "max"]).round(1)
    print("\n      by year (mean / min / max):")
    for yr, r in by_year.iterrows():
        print(f"        {yr}: {r['mean']:7.1f} / {r['min']:5.0f} / {r['max']:5.0f}")

    return {
        "n_short_eligible_candidate_days": n_eligible,
        "n_htb_flagged": n_htb,
        "htb_pct": round(htb_pct, 3),
        "htb_leg_counts": htb_leg_counts,
        "short_candidates_per_day": {
            "mean": round(float(counts.mean()), 2),
            "median": float(counts.median()),
            "p10": float(counts.quantile(0.10)),
            "p90": float(counts.quantile(0.90)),
            "max": float(counts.max()),
            "n_days": int(len(counts)),
            "n_zero_days": zero_days,
            "pct_zero_days": round(100.0 * zero_days / len(counts), 3),
        },
        "by_year": {int(y): {k: float(v) for k, v in r.items()} for y, r in by_year.iterrows()},
    }


def main() -> None:
    results = {}
    with open(HERE / "_cache_pool2_clean.pkl", "rb") as fh:
        c2 = pickle.load(fh)
    results["rally_breaker"] = calibrate_rally_breaker(c2["gspc_close"])

    for label, path in (("pool2_clean", "_cache_pool2_clean.pkl"), ("pool1", "_cache_pool1.pkl")):
        with open(HERE / path, "rb") as fh:
            cache = pickle.load(fh)
        results[label] = calibrate_htb_and_supply(label, cache, cache["gspc_close"])

    (HERE / "calibration_results.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {HERE/'calibration_results.json'} -- THRESHOLDS NOW FROZEN.")


if __name__ == "__main__":
    main()
