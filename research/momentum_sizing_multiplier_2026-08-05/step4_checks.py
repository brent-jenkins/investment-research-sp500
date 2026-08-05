"""Step 4, checks 1 and 2 -- real, quantified answers from this run's own
trade-level and per-candidate sizing output (results/reset_*.json,
results/reset_*_stats.json).

Check 1 -- absolute_max_position_gbp backstop interaction: does the
re-applied dynamic_max_position_gbp ceiling (which already incorporates
absolute_max_position_gbp via resolve_dynamic_max_position_gbp()) still
correctly cap any position the multiplier would otherwise push over it?
Quantified from multiplier_stats' own "backstop_bound" flag (real,
per-candidate evidence, not inferred).

Check 2 -- trade-count / capacity displacement: does raising size for
high-conviction candidates reduce total trade count by displacing capacity
for others via max_total_deployed + momentum-ranked skip-and-continue
admission? Characterized via (a) raw trade-count change, (b) how much of
each variant's trade set is the SAME (ticker, entry_date) pair as baseline
vs genuinely different entries, (c) total capital-days deployed (sum of
position_gbp * holding_days) as a measure of whether capital is being
reallocated (similar total deployment, different split) or genuinely
withdrawn from use (materially lower total deployment).
"""
import json

RESULTS_DIR = r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05\results'
ARMS = ["baseline", "modest", "aggressive", "whole_dist"]


def load_trades(arm):
    with open(f"{RESULTS_DIR}\\reset_{arm}.json") as f:
        return json.load(f)


def load_stats(arm):
    try:
        with open(f"{RESULTS_DIR}\\reset_{arm}_stats.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def check1_backstop():
    print("=" * 70)
    print("CHECK 1 -- absolute_max_position_gbp / dynamic_max_position_gbp backstop")
    print("=" * 70)
    print("NOTE: the raw 'backstop_bound' field recorded during the run has a float-")
    print("epsilon false-positive at multiplier==1.0 (position_gbp compared against an")
    print("UNROUNDED dynamic_max_position_gbp vs. position_gbp itself rounded to 2dp --")
    print("binary float representation makes an exactly-equal value compare as fractionally")
    print("greater in ~1e-10 terms). Confirmed directly: every such flagged case has ZERO")
    print("£ overshoot (final_presized - ceiling == 0.0 to the penny) -- harmless, since")
    print("min() with an equal value is a no-op regardless, but the raw count is corrected")
    print("below using an explicit 1p tolerance and restricted to genuinely multiplied")
    print("candidates, which is the only case this check is actually about.")
    out = {}
    for arm in ARMS:
        if arm == "baseline":
            continue
        stats = load_stats(arm)
        n_total = len(stats)
        n_multiplied = sum(1 for s in stats if s["multiplier"] != 1.0)
        mult_ne1 = [s for s in stats if s["multiplier"] != 1.0]
        # Genuine overshoot: the post-multiplier, PRE-clamp value exceeded the
        # ceiling by more than 1p (not a float-noise artifact).
        real_bound = [s for s in mult_ne1
                      if s["position_gbp_post_multiplier_pre_clamp"] - s["dynamic_max_position_gbp"] > 0.01]
        # Of those, how many were FULLY absorbed by the ceiling (final == ceiling,
        # i.e. the schedule's nominal multiplier was completely neutralized for
        # that one candidate, not just partially trimmed)?
        fully_capped = [s for s in mult_ne1
                         if abs(s["position_gbp_final_presized"] - s["dynamic_max_position_gbp"]) < 0.01]
        examples = real_bound[:5]
        print(f"\n{arm}: {n_total} sized candidates, {n_multiplied} with multiplier != 1.0")
        print(f"    genuinely pushed pre-clamp value > ceiling by >1p (backstop bound for real): "
              f"{len(real_bound)} / {n_multiplied} ({100*len(real_bound)/n_multiplied if n_multiplied else 0:.1f}%)")
        print(f"    of multiplied candidates, ended up EXACTLY at the ceiling (nominal multiplier "
              f"fully absorbed, not just trimmed): {len(fully_capped)} / {n_multiplied} "
              f"({100*len(fully_capped)/n_multiplied if n_multiplied else 0:.1f}%)")
        for e in examples:
            print(f"    {e['ticker']} {e['date']}: label={e['strength_label']} mult={e['multiplier']} "
                  f"var_sized=£{e['position_gbp_var']:.2f} post_mult_pre_clamp=£{e['position_gbp_post_multiplier_pre_clamp']:.2f} "
                  f"ceiling=£{e['dynamic_max_position_gbp']:.2f} final=£{e['position_gbp_final_presized']:.2f}")
        out[arm] = {
            "n_total": n_total, "n_multiplied": n_multiplied,
            "n_genuinely_backstop_bound": len(real_bound),
            "n_fully_capped_at_ceiling": len(fully_capped),
        }
    return out


def check2_capacity_displacement():
    print("\n" + "=" * 70)
    print("CHECK 2 -- trade-count / capacity displacement")
    print("=" * 70)
    trades = {arm: load_trades(arm) for arm in ARMS}
    baseline_keys = {(t["ticker"], t["entry_date"]) for t in trades["baseline"]}

    out = {}
    for arm in ARMS:
        tl = trades[arm]
        keys = {(t["ticker"], t["entry_date"]) for t in tl}
        shared = keys & baseline_keys
        only_here = keys - baseline_keys
        only_baseline = baseline_keys - keys
        total_position_gbp = sum(t["position_gbp"] for t in tl)
        total_capital_days = sum(t["position_gbp"] * t["holding_days"] for t in tl)
        distinct_tickers = len({t["ticker"] for t in tl})
        avg_position_gbp = total_position_gbp / len(tl) if tl else 0.0
        print(f"\n{arm}: {len(tl)} trades, {distinct_tickers} distinct tickers, "
              f"avg position £{avg_position_gbp:,.2f}, total capital-days £{total_capital_days:,.0f}")
        if arm != "baseline":
            print(f"    entries shared with baseline (same ticker+entry_date): {len(shared)} / {len(keys)} "
                  f"({100*len(shared)/len(keys) if keys else 0:.1f}%)")
            print(f"    entries only in {arm} (not in baseline): {len(only_here)}")
            print(f"    entries only in baseline (displaced/not present in {arm}): {len(only_baseline)}")
        out[arm] = {
            "n_trades": len(tl), "distinct_tickers": distinct_tickers,
            "avg_position_gbp": round(avg_position_gbp, 2),
            "total_position_gbp": round(total_position_gbp, 2),
            "total_capital_days_gbp": round(total_capital_days, 2),
            "shared_with_baseline": len(shared) if arm != "baseline" else None,
            "only_in_arm": len(only_here) if arm != "baseline" else None,
            "only_in_baseline_displaced": len(only_baseline) if arm != "baseline" else None,
        }
    return out


if __name__ == "__main__":
    r1 = check1_backstop()
    r2 = check2_capacity_displacement()
    with open(f"{RESULTS_DIR}\\..\\step4_check1_check2.json", "w") as f:
        json.dump({"backstop": r1, "capacity_displacement": r2}, f, indent=2, default=str)
    print("\nSaved step4_check1_check2.json")
