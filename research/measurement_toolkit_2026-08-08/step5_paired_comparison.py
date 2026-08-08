"""Paired per-decision comparison: for two trades files sharing the same
entry logic (so a shared (ticker, entry_date) means "the identical decision,
realized under two different exit/signal regimes"), pair on that key and
compare realized outcomes directly -- no portfolio, no capacity, no cascade.
Modelled on step4_capacity_and_whipsaw.py's whipsaw pairing (stop-exit +
re-entry, paired and evaluated per-case, not at portfolio level).

Valid whenever entry admission is capacity-uncontaminated for the SHARED
subset -- true by construction: a shared (ticker, entry_date) means capacity
allowed that exact entry under BOTH arms, so its own before/after outcome
comparison carries no capacity effect regardless of what else in the two
arms' broader trade lists differs.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import json

import pandas as pd


def paired_comparison(trades_x_path: str, trades_y_path: str, label_x: str, label_y: str) -> dict:
    x = pd.read_csv(trades_x_path)
    y = pd.read_csv(trades_y_path)
    x_by_key = {(r["ticker"], r["entry_date"]): r for _, r in x.iterrows()}
    y_by_key = {(r["ticker"], r["entry_date"]): r for _, r in y.iterrows()}
    shared_keys = sorted(set(x_by_key) & set(y_by_key))

    pairs = []
    for k in shared_keys:
        rx, ry = x_by_key[k], y_by_key[k]
        pairs.append({
            "ticker": k[0], "entry_date": k[1],
            f"{label_x}_pnl_pct": rx["pnl_pct"], f"{label_y}_pnl_pct": ry["pnl_pct"],
            f"{label_x}_pnl_gbp": rx["pnl_gbp"], f"{label_y}_pnl_gbp": ry["pnl_gbp"],
            f"{label_x}_exit_reason": rx["exit_reason"], f"{label_y}_exit_reason": ry["exit_reason"],
            "pnl_pct_diff": ry["pnl_pct"] - rx["pnl_pct"],
            f"{label_y}_beats_{label_x}": ry["pnl_pct"] > rx["pnl_pct"],
        })
    pairs_df = pd.DataFrame(pairs)

    if pairs_df.empty:
        return {"label_x": label_x, "label_y": label_y, "n_total_x": len(x), "n_total_y": len(y),
                "n_shared_pairs": 0, "note": "NO SHARED PAIRS -- instrument not usable for this comparison"}, pairs_df

    n_shared = len(pairs_df)
    n_identical = int((pairs_df["pnl_pct_diff"].abs() < 1e-9).sum())
    n_differ = n_shared - n_identical
    win_col = f"{label_y}_beats_{label_x}"
    win_rate_of_differing = round(100 * pairs_df.loc[pairs_df["pnl_pct_diff"].abs() >= 1e-9, win_col].mean(), 1) if n_differ else None

    result = {
        "label_x": label_x, "label_y": label_y,
        "n_total_x": len(x), "n_total_y": len(y),
        "n_shared_pairs": n_shared,
        "shared_as_pct_of_x": round(100 * n_shared / len(x), 1),
        "shared_as_pct_of_y": round(100 * n_shared / len(y), 1),
        "n_identical_outcome": n_identical, "n_differing_outcome": n_differ,
        f"win_rate_{label_y}_over_{label_x}_pct_of_differing": win_rate_of_differing,
        "mean_pnl_pct_diff": round(float(pairs_df["pnl_pct_diff"].mean()), 4),
        "median_pnl_pct_diff": round(float(pairs_df["pnl_pct_diff"].median()), 4),
        "std_pnl_pct_diff": round(float(pairs_df["pnl_pct_diff"].std()), 4),
        "total_pnl_gbp_diff": round(float((pairs_df[f"{label_y}_pnl_gbp"] - pairs_df[f"{label_x}_pnl_gbp"]).sum()), 2),
        "pnl_pct_diff_percentiles": {
            p: round(float(pairs_df["pnl_pct_diff"].quantile(p / 100)), 4) for p in [5, 25, 50, 75, 95]
        },
    }
    return result, pairs_df


if __name__ == "__main__":
    x_path, y_path, label_x, label_y, out_prefix = sys.argv[1:6]
    result, pairs_df = paired_comparison(x_path, y_path, label_x, label_y)
    print(json.dumps(result, indent=2, default=str))
    pairs_df.to_csv(f"{out_prefix}_pairs.csv", index=False)
    with open(f"{out_prefix}_summary.json", "w") as fh:
        json.dump(result, fh, indent=2, default=str)


def disagreement_population_comparison(trades_x_path: str, trades_y_path: str, label_x: str, label_y: str) -> dict:
    """Two-sample comparison for cases where pairing by shared key isn't the
    right tool (e.g. adj_close vs close, where a differing admission usually
    means the ENTRY itself differs, not just the exit) -- compares the
    outcome distribution of positions unique to Y vs positions unique to X.
    Best used against UNCAPPED runs (see step3_uncapped_arm.py), where
    "unique to one arm" is driven overwhelmingly by genuine signal
    disagreement rather than capacity-cascade admission noise."""
    x = pd.read_csv(trades_x_path)
    y = pd.read_csv(trades_y_path)
    x_keys = set(zip(x["ticker"], x["entry_date"]))
    y_keys = set(zip(y["ticker"], y["entry_date"]))

    x_only = x[x.apply(lambda r: (r["ticker"], r["entry_date"]) not in y_keys, axis=1)]
    y_only = y[y.apply(lambda r: (r["ticker"], r["entry_date"]) not in x_keys, axis=1)]

    def _summary(df):
        if df.empty:
            return {"n": 0}
        return {
            "n": len(df),
            "mean_pnl_pct": round(float(df["pnl_pct"].mean()), 4),
            "median_pnl_pct": round(float(df["pnl_pct"].median()), 4),
            "std_pnl_pct": round(float(df["pnl_pct"].std()), 4),
            "win_rate_pct": round(100 * float((df["pnl_pct"] > 0).mean()), 1),
            "mean_pnl_gbp": round(float(df["pnl_gbp"].mean()), 2),
            "total_pnl_gbp": round(float(df["pnl_gbp"].sum()), 2),
        }

    x_only_s, y_only_s = _summary(x_only), _summary(y_only)
    return {
        "label_x": label_x, "label_y": label_y,
        "n_total_x": len(x), "n_total_y": len(y),
        f"{label_x}_only": x_only_s, f"{label_y}_only": y_only_s,
        "mean_pnl_pct_diff_y_only_minus_x_only": (
            round(y_only_s["mean_pnl_pct"] - x_only_s["mean_pnl_pct"], 4)
            if x_only_s["n"] and y_only_s["n"] else None
        ),
    }
