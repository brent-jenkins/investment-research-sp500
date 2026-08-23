"""READ-ONLY DIAGNOSTIC: does a volatility signal -- alone, or combined with an
index moving average -- separate subsequent ^GSPC outcomes?

No arms, no engine, no backtest, no position sizing, no admission, no capacity.
A property of one index series. Reads gspc.csv (extracted read-only by
step1_extract_gspc.py) and analysis/crisis.py's own shared trigger. Writes only
its own output files.

CONVENTIONS, stated because they change what the numbers mean:
  * Realised vol = std of daily LOG returns over the trailing window, INCLUSIVE
    of day t (information available at t's close), annualised by sqrt(252).
  * Forward return over H calendar days = close at the last trading day at or
    before t+H, over close at t. NaN when t+H exceeds the data.
  * Forward max drawdown over 90 calendar days = min(price / running_peak - 1)
    over the path [t, t+90d], with the running peak SEEDED at t's close -- i.e.
    the worst peak-to-trough an investor holding from t would have lived
    through. Reported as a negative percentage.
  * Volatility deciles are assigned over the FULL SAMPLE. That is hindsight
    bucketing and is fine for a property question, but it is not tradable, so
    the headline top-decile condition is ALSO recomputed with a point-in-time
    EXPANDING percentile (see step 6) to confirm the separation is not an
    artifact of knowing the whole distribution in advance.
  * Overlapping forward windows make daily observations heavily autocorrelated.
    Day counts are NOT sample sizes. Contiguous EPISODE counts are reported for
    every condition, following this project's own precedent.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis import crisis  # noqa: E402

ANN = np.sqrt(252.0)


def episodes(mask: pd.Series) -> int:
    """Number of contiguous True runs -- the honest sample size for a
    condition whose days are serially dependent."""
    m = mask.fillna(False).astype(bool).values
    return int(np.sum(m & ~np.concatenate(([False], m[:-1]))))


def forward_return(close: pd.Series, days: int) -> pd.Series:
    idx = close.index
    target = idx + pd.Timedelta(days=days)
    pos = idx.searchsorted(target, side="right") - 1        # last trading day <= t+H
    out = np.full(len(idx), np.nan)
    ok = (pos > np.arange(len(idx))) & (target <= idx[-1])
    out[ok] = close.values[pos[ok]] / close.values[np.arange(len(idx))[ok]] - 1.0
    return pd.Series(out * 100.0, index=idx)


def forward_maxdd(close: pd.Series, days: int) -> pd.Series:
    idx = close.index
    v = close.values
    target = idx + pd.Timedelta(days=days)
    end = idx.searchsorted(target, side="right") - 1
    out = np.full(len(idx), np.nan)
    for i in range(len(idx)):
        j = end[i]
        if j <= i or target[i] > idx[-1]:
            continue
        path = v[i:j + 1]
        out[i] = (path / np.maximum.accumulate(path) - 1.0).min()
    return pd.Series(out * 100.0, index=idx)


def summarise(df: pd.DataFrame, mask=None) -> dict:
    d = df if mask is None else df[mask]
    d = d.dropna(subset=["fwd30", "fwd90", "fwd90_dd"])
    if d.empty:
        return {"n": 0}
    return {
        "n": int(len(d)),
        "fwd30_mean": round(float(d["fwd30"].mean()), 2),
        "fwd30_med": round(float(d["fwd30"].median()), 2),
        "fwd90_mean": round(float(d["fwd90"].mean()), 2),
        "fwd90_med": round(float(d["fwd90"].median()), 2),
        "fwd90_dd_mean": round(float(d["fwd90_dd"].mean()), 2),
        "fwd90_dd_med": round(float(d["fwd90_dd"].median()), 2),
        "fwd90_dd_p10": round(float(d["fwd90_dd"].quantile(0.10)), 2),
        "pct_fwd90_negative": round(100.0 * float((d["fwd90"] < 0).mean()), 1),
    }


def main() -> None:
    px = pd.read_csv(HERE / "gspc.csv", parse_dates=["date"]).set_index("date").sort_index()
    close = px["close"].astype(float)
    n = len(close)

    logret = np.log(close / close.shift(1))
    df = pd.DataFrame(index=close.index)
    df["close"] = close
    df["vol20"] = logret.rolling(20).std() * ANN * 100.0
    df["vol60"] = logret.rolling(60).std() * ANN * 100.0
    for w in (7, 14, 30):
        sma = close.rolling(w).mean()
        df[f"sma{w}"] = sma
        df[f"above{w}"] = close > sma
        df[f"dist{w}"] = (close / sma - 1.0) * 100.0

    df["fwd30"] = forward_return(close, 30)
    df["fwd90"] = forward_return(close, 90)
    df["fwd90_dd"] = forward_maxdd(close, 90)

    # Existing production crisis mechanism, for overlap (shared function, not
    # a reimplementation).
    raw = crisis.compute_raw_trigger(close)
    df["crisis_block"] = crisis.extend_trigger(raw).reindex(df.index).fillna(False)

    evaluable = df.dropna(subset=["vol20", "vol60", "fwd30", "fwd90", "fwd90_dd"])
    out = {
        "rows_total": n,
        "window": [str(close.index[0].date()), str(close.index[-1].date())],
        "rows_evaluable": int(len(evaluable)),
        "note_effective_sample": (
            f"{len(evaluable)} days but overlapping 90-day forward windows: "
            f"~{len(evaluable)//63} non-overlapping 90-day observations"),
        "baseline_all_days": summarise(df),
    }

    # ---------------------------------------------------------------- (a)
    for col in ("vol20", "vol60"):
        dec = pd.qcut(df[col], 10, labels=False, duplicates="drop")
        rows = []
        for d in range(10):
            m = dec == d
            s = summarise(df, m)
            s["decile"] = d + 1
            s["vol_range"] = [round(float(df.loc[m, col].min()), 1),
                              round(float(df.loc[m, col].max()), 1)] if m.any() else None
            rows.append(s)
        out[f"a_deciles_{col}"] = rows

    # ---------------------------------------------------------------- (b)
    ma_rows = {}
    for w in (7, 14, 30):
        ma_rows[f"above_sma{w}"] = summarise(df, df[f"above{w}"])
        ma_rows[f"below_sma{w}"] = summarise(df, ~df[f"above{w}"] & df[f"sma{w}"].notna())
        ma_rows[f"below_sma{w}_pct_days"] = round(
            100.0 * float((~df[f"above{w}"] & df[f"sma{w}"].notna()).mean()), 1)
        ma_rows[f"below_sma{w}_episodes"] = episodes(~df[f"above{w}"] & df[f"sma{w}"].notna())
    out["b_ma_position"] = ma_rows

    # ---------------------------------------------------------------- (c)
    combo = {}
    for thresh_name, q in (("top_decile", 0.90), ("top_quintile", 0.80)):
        cut = float(df["vol20"].quantile(q))
        hv = df["vol20"] >= cut
        for w in (7, 14, 30):
            below = ~df[f"above{w}"] & df[f"sma{w}"].notna()
            combo[f"{thresh_name}_sma{w}"] = {
                "vol_cut": round(cut, 2),
                "high_vol_AND_below": summarise(df, hv & below),
                "high_vol_AND_above": summarise(df, hv & ~below & df[f"sma{w}"].notna()),
                "low_vol_AND_below": summarise(df, ~hv & below),
                "neither": summarise(df, ~hv & ~below & df[f"sma{w}"].notna()),
                "fires_pct_days": {
                    "high_vol_alone": round(100.0 * float(hv.mean()), 1),
                    "below_alone": round(100.0 * float(below.mean()), 1),
                    "both": round(100.0 * float((hv & below).mean()), 1),
                },
                "episodes": {
                    "high_vol_alone": episodes(hv),
                    "below_alone": episodes(below),
                    "both": episodes(hv & below),
                },
            }
    out["c_combination"] = combo

    # ---------------------------------------------------------------- (d)
    cut90 = float(df["vol20"].quantile(0.90))
    hv = df["vol20"] >= cut90
    pers = {"single_day": {**summarise(df, hv),
                           "fires_pct_days": round(100.0 * float(hv.mean()), 1),
                           "episodes": episodes(hv)}}
    for k in (5, 10, 20):
        sustained = hv.rolling(k).sum() >= k          # elevated on all of the last k days
        sustained = sustained.fillna(False)
        pers[f"sustained_{k}d"] = {**summarise(df, sustained),
                                   "fires_pct_days": round(100.0 * float(sustained.mean()), 1),
                                   "episodes": episodes(sustained)}
        both = sustained & ~df["above30"]
        pers[f"sustained_{k}d_and_below_sma30"] = {
            **summarise(df, both),
            "fires_pct_days": round(100.0 * float(both.mean()), 1),
            "episodes": episodes(both)}
    out["d_persistence"] = pers

    # ---------------------------------------------------------------- (e)
    out["e_firing_frequency"] = {
        "vol20_top_decile": {"pct_days": round(100.0 * float(hv.mean()), 1),
                             "episodes": episodes(hv),
                             "median_episode_len_days": int(
                                 pd.Series(hv.values).groupby(
                                     (~pd.Series(hv.values)).cumsum()).sum().replace(0, np.nan).median())},
        "below_sma30": {"pct_days": round(100.0 * float((~df["above30"]).mean()), 1),
                        "episodes": episodes(~df["above30"])},
        "existing_crisis_entry_block": {
            "pct_days": round(100.0 * float(df["crisis_block"].mean()), 2),
            "episodes": episodes(df["crisis_block"]),
            "raw_trigger_days": int(raw.sum())},
        "overlap_high_vol_with_existing_crisis_block": {
            "high_vol_days_already_blocked_pct": round(
                100.0 * float(df.loc[hv, "crisis_block"].mean()), 1),
            "crisis_block_days_that_are_high_vol_pct": round(
                100.0 * float(hv[df["crisis_block"]].mean()), 1)},
    }

    # ------------------------------------------------- (6) tradability check
    # Point-in-time expanding percentile: at each day, rank today's vol20
    # against only the history available up to that day (min 252 obs).
    r = df["vol20"].expanding(min_periods=252).rank(pct=True)
    hv_pit = (r >= 0.90).fillna(False)
    out["f_point_in_time_top_decile"] = {
        **summarise(df, hv_pit),
        "fires_pct_days": round(100.0 * float(hv_pit.mean()), 1),
        "episodes": episodes(hv_pit),
        "agreement_with_full_sample_decile_pct": round(
            100.0 * float((hv_pit == hv).mean()), 1),
    }
    below30 = ~df["above30"] & df["sma30"].notna()
    out["f_point_in_time_combo"] = summarise(df, hv_pit & below30)
    out["f_point_in_time_combo"]["fires_pct_days"] = round(
        100.0 * float((hv_pit & below30).mean()), 1)
    out["f_point_in_time_combo"]["episodes"] = episodes(hv_pit & below30)

    (HERE / "diagnostic.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    df.to_csv(HERE / "daily_panel.csv")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
