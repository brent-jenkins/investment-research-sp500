"""Capacity-turnover split -- mandatory per CLAUDE.md's standing
requirement and this task's own spec: direct effect (more capital per
opportunity already being taken) and admission effect (more slots ->
lower-ranked candidates now admitted) reported SEPARATELY, never blended.

Method:
  - Match trades between the baseline (cap=0.50) and each higher-cap arm
    by (block, ticker, entry_date) -- the natural key for "the same
    opportunity."
  - DIRECT EFFECT: for opportunities taken under BOTH arms, compare
    position_gbp. This isolates the pure sizing effect on trades that
    would have happened regardless of the cap.
  - ADMISSION EFFECT: trades present in the higher-cap arm but NOT in the
    baseline -- genuinely new admissions the extra capacity allowed.
    Momentum at entry for these (read directly from the ticker cache's own
    `momentum` column, the same continuous trailing-12-month value
    simulate_portfolio_compounding_mtm()'s own priority ranking uses --
    not re-derived, read straight from the frame precompute_ticker()
    already built) is compared, by decile, against the baseline-admitted
    population's own momentum distribution. If the newly-admitted trades
    skew toward lower deciles, that is dilution -- a real cost, reported
    as such.
"""
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
RESULTS = HERE / "results"
CAPS = [0.50, 0.70, 0.95]


def cap_tag(cap):
    return str(cap).replace(".", "")


def momentum_at_entry(cache: dict, ticker: str, entry_date) -> float:
    c = cache.get(ticker)
    if c is None or c.get("frame") is None:
        return None
    frame = c["frame"]
    ts = pd.Timestamp(entry_date)
    if ts not in frame.index:
        return None
    v = frame.loc[ts, "momentum"]
    return float(v) if pd.notna(v) else None


def decile_table(values: pd.Series) -> dict:
    s = values.dropna()
    if s.empty:
        return {}
    try:
        deciles = pd.qcut(s, 10, labels=False, duplicates="drop")
    except ValueError:
        return {"note": "insufficient distinct values for deciles", "n": int(len(s)),
                "mean": float(s.mean()), "median": float(s.median())}
    counts = deciles.value_counts().sort_index()
    return {
        "n": int(len(s)), "mean": round(float(s.mean()), 4), "median": round(float(s.median()), 4),
        "decile_counts": {int(k): int(v) for k, v in counts.items()},
        "decile_edges": [round(float(x), 4) for x in pd.qcut(s, 10, duplicates="drop").cat.categories.map(
            lambda iv: iv.right).tolist()],
    }


def main() -> None:
    with open(HERE / "ticker_caches.pkl", "rb") as f:
        cache = pickle.load(f)

    base = pd.read_csv(RESULTS / f"trades_cap{cap_tag(0.50)}.csv")
    base["key"] = list(zip(base["block"], base["ticker"], base["entry_date"]))
    base_momentum = base.apply(lambda r: momentum_at_entry(cache, r["ticker"], r["entry_date"]), axis=1)
    base_result = {
        "cap": 0.50, "n_trades": len(base),
        "baseline_momentum_decile": decile_table(base_momentum),
    }

    out = {"baseline": base_result, "arms": {}}

    for cap in CAPS:
        if abs(cap - 0.50) < 1e-9:
            continue
        tag = cap_tag(cap)
        td = pd.read_csv(RESULTS / f"trades_cap{tag}.csv")
        td["key"] = list(zip(td["block"], td["ticker"], td["entry_date"]))

        common_keys = set(base["key"]) & set(td["key"])
        matched_base = base[base["key"].isin(common_keys)].set_index("key")
        matched_new = td[td["key"].isin(common_keys)].set_index("key")
        # align
        common_keys_sorted = sorted(common_keys)
        pos_base = matched_base.loc[common_keys_sorted, "position_gbp"].values
        pos_new = matched_new.loc[common_keys_sorted, "position_gbp"].values
        pct_change = (pos_new - pos_base) / pos_base * 100.0

        admission_effect = td[~td["key"].isin(set(base["key"]))]
        adm_momentum = admission_effect.apply(
            lambda r: momentum_at_entry(cache, r["ticker"], r["entry_date"]), axis=1)

        lost_trades = base[~base["key"].isin(set(td["key"]))]

        out["arms"][tag] = {
            "cap": cap,
            "n_trades_total": len(td),
            "n_trades_baseline": len(base),
            "trade_count_change": len(td) - len(base),
            "direct_effect": {
                "n_matched_opportunities": len(common_keys),
                "position_gbp_pct_change_mean": round(float(np.mean(pct_change)), 2) if len(pct_change) else None,
                "position_gbp_pct_change_median": round(float(np.median(pct_change)), 2) if len(pct_change) else None,
                "position_gbp_baseline_mean": round(float(np.mean(pos_base)), 2) if len(pos_base) else None,
                "position_gbp_arm_mean": round(float(np.mean(pos_new)), 2) if len(pos_new) else None,
            },
            "admission_effect": {
                "n_new_admissions": len(admission_effect),
                "n_baseline_trades_lost": len(lost_trades),
                "new_admission_momentum_decile": decile_table(adm_momentum),
            },
        }

    (RESULTS / "capacity_turnover_summary.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
