"""Per-decision corroboration instruments, per PREREGISTRATION.md Section 9.

paired_comparison() / disagreement_population_comparison(): copied from
research/measurement_toolkit_2026-08-08/step5_paired_comparison.py's own
already-validated functions (not re-derived) -- adapted only to be
self-contained in this study's own directory rather than depending on a
different worktree's sys.path.

month_level_pairing(): the admission-redesign study's own adaptation
(PREREGISTRATION.md Section 9), re-implemented here identically -- for
every calendar month, pair the two arms' realized returns for every ticker
held by BOTH arms at any point overlapping that month.
"""
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
                "n_shared_pairs": 0, "note": "NO SHARED PAIRS -- instrument not usable for this comparison"}

    n_shared = len(pairs_df)
    n_identical = int((pairs_df["pnl_pct_diff"].abs() < 1e-9).sum())
    n_differ = n_shared - n_identical
    win_col = f"{label_y}_beats_{label_x}"
    win_rate_of_differing = round(100 * pairs_df.loc[pairs_df["pnl_pct_diff"].abs() >= 1e-9, win_col].mean(), 1) if n_differ else None

    return {
        "label_x": label_x, "label_y": label_y,
        "n_total_x": len(x), "n_total_y": len(y),
        "n_shared_pairs": n_shared,
        "shared_as_pct_of_x": round(100 * n_shared / len(x), 1),
        "shared_as_pct_of_y": round(100 * n_shared / len(y), 1),
        "n_identical_outcome": n_identical, "n_differing_outcome": n_differ,
        f"win_rate_{label_y}_over_{label_x}_pct_of_differing": win_rate_of_differing,
        "mean_pnl_pct_diff": round(float(pairs_df["pnl_pct_diff"].mean()), 4),
        "median_pnl_pct_diff": round(float(pairs_df["pnl_pct_diff"].median()), 4),
        "total_pnl_gbp_diff": round(float((pairs_df[f"{label_y}_pnl_gbp"] - pairs_df[f"{label_x}_pnl_gbp"]).sum()), 2),
    }


def month_level_pairing(trades_x_path: str, trades_y_path: str, label_x: str, label_y: str) -> dict:
    """For every calendar month and every ticker held by BOTH arms at any
    point overlapping that month, pair the two arms' realized returns for
    that ticker/month. Not fully capacity/cascade-free (residual within-
    month capacity dynamics can still differ) -- reported as such, not
    assumed clean, exactly per the pre-registration's own framing."""
    x = pd.read_csv(trades_x_path)
    y = pd.read_csv(trades_y_path)
    for df in (x, y):
        df["entry_date"] = pd.to_datetime(df["entry_date"])
        df["exit_date"] = pd.to_datetime(df["exit_date"])

    def _ticker_months(df):
        rows = []
        for _, r in df.iterrows():
            months = pd.period_range(r["entry_date"], r["exit_date"], freq="M")
            for m in months:
                rows.append({"ticker": r["ticker"], "month": m, "pnl_pct": r["pnl_pct"], "pnl_gbp": r["pnl_gbp"]})
        return pd.DataFrame(rows)

    x_tm = _ticker_months(x)
    y_tm = _ticker_months(y)
    if x_tm.empty or y_tm.empty:
        return {"label_x": label_x, "label_y": label_y, "n_shared_ticker_months": 0,
                "note": "NO SHARED TICKER-MONTHS -- instrument not usable"}

    # One row per (ticker, month) per arm -- if a ticker/month has multiple
    # trades in one arm (re-entries within the same month), average them.
    x_agg = x_tm.groupby(["ticker", "month"], as_index=False).agg(pnl_pct=("pnl_pct", "mean"), pnl_gbp=("pnl_gbp", "sum"))
    y_agg = y_tm.groupby(["ticker", "month"], as_index=False).agg(pnl_pct=("pnl_pct", "mean"), pnl_gbp=("pnl_gbp", "sum"))

    merged = x_agg.merge(y_agg, on=["ticker", "month"], suffixes=(f"_{label_x}", f"_{label_y}"))
    if merged.empty:
        return {"label_x": label_x, "label_y": label_y, "n_shared_ticker_months": 0,
                "note": "NO SHARED TICKER-MONTHS -- instrument not usable"}

    diff = merged[f"pnl_pct_{label_y}"] - merged[f"pnl_pct_{label_x}"]
    n_shared = len(merged)
    n_differ = int((diff.abs() >= 1e-9).sum())
    win_rate = round(100 * (diff[diff.abs() >= 1e-9] > 0).mean(), 1) if n_differ else None

    return {
        "label_x": label_x, "label_y": label_y,
        "n_shared_ticker_months": n_shared, "n_differing_outcome": n_differ,
        f"win_rate_{label_y}_over_{label_x}_pct_of_differing": win_rate,
        "mean_pnl_pct_diff": round(float(diff.mean()), 4),
        "median_pnl_pct_diff": round(float(diff.median()), 4),
    }


def disagreement_population_comparison(trades_x_path: str, trades_y_path: str, label_x: str, label_y: str) -> dict:
    x = pd.read_csv(trades_x_path)
    y = pd.read_csv(trades_y_path)
    x_keys = set(zip(x["ticker"], x["entry_date"].astype(str)))
    y_keys = set(zip(y["ticker"], y["entry_date"].astype(str)))

    x_only = x[x.apply(lambda r: (r["ticker"], str(r["entry_date"])) not in y_keys, axis=1)]
    y_only = y[y.apply(lambda r: (r["ticker"], str(r["entry_date"])) not in x_keys, axis=1)]

    def _summary(df):
        if df.empty:
            return {"n": 0}
        return {
            "n": len(df),
            "mean_pnl_pct": round(float(df["pnl_pct"].mean()), 4),
            "median_pnl_pct": round(float(df["pnl_pct"].median()), 4),
            "win_rate_pct": round(100 * float((df["pnl_pct"] > 0).mean()), 1),
            "total_pnl_gbp": round(float(df["pnl_gbp"].sum()), 2),
        }

    x_only_s, y_only_s = _summary(x_only), _summary(y_only)
    return {
        "label_x": label_x, "label_y": label_y,
        "n_total_x": len(x), "n_total_y": len(y),
        f"{label_x}_only": x_only_s, f"{label_y}_only": y_only_s,
    }
