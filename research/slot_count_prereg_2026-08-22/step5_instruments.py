"""PREREGISTRATION.md Sec 4 -- the return-stage instruments, for the THREE
TESTED arms (n8, n5, n3). n14 is reported as NOT TESTED, never as a null.

  4.1  cost-adjusted excess return over ^SP500TR vs baseline's own excess
  4.2  drawdown, Clause A (relative, 15pp) and Clause B (absolute, -60%)
  4.3  outcome concentration: trades, top-3 / top-10 share, decile curve
  4.4  single-ticker dominance, ticker-level, ABSOLUTE not signed, ceiling 0.40
  4.5  overlap + the pre-registered randomisation null
  4.6  costs modelled PER ARM (FX drag and idle-cash credit reported separately)
  4.7  paired per-decision instrument + the three pre-registered fallbacks
  4.8  block-pass count, corroborating only

Cost template, unchanged from prior studies: FX = 0.30% round trip on total
notional, annualised as a % of mean account balance; idle-cash credit =
realised idle fraction x mean ^IRX. Reference series are the exit-mechanics
study's own committed read-only cache -- the same benchmark the reproduced
baseline was measured against.

NOTE ON UNITS: this study's panel stores current_deployed_pct as a FRACTION;
the exit-mechanics panel stored it as a PERCENT. Handled explicitly below.
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

from analysis.backtest import compute_metrics_fixed, make_blocks  # noqa: E402
from analysis.validation import _block_passes  # noqa: E402

FX_ROUND_TRIP = 0.0030
STARTING_CAPITAL = 50000.0
TESTED = ["n8", "n5", "n3"]
NOT_TESTED = ["n14"]
NOMINAL = {"n14": 14, "n8": 8, "n5": 5, "n3": 3}
BAR_PP = 1.5
DD_CLAUSE_A_PP = 15.0
DD_CLAUSE_B_ABS = -60.0
TOP3_FLAG = 50.0
DOMINANCE_CEILING = 0.40
SHARED_KEY_FLOOR = 100
NULL_DRAWS = 1000
SEED = 20260822


def cagr(s: pd.Series) -> float:
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100.0


def maxdd(s: pd.Series) -> float:
    return float((s / s.cummax() - 1.0).min() * 100.0)


def load(pool, arm):
    td = pd.read_csv(HERE / f"trades_{pool}_{arm}.csv")
    pn = pd.read_csv(HERE / f"panel_{pool}_{arm}.csv")
    pn["date"] = pd.to_datetime(pn["date"])
    ad = pd.read_csv(HERE / f"admissions_{pool}_{arm}.csv")
    return td, pn.set_index("date").sort_index(), ad


def key(df):
    return df["ticker"] + "|" + pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")


def core(pool, arm, ref):
    td, pn, ad = load(pool, arm)
    eq = pn["current_balance"].astype(float)
    tr = ref["^SP500TR"].reindex(eq.index, method="ffill").dropna()
    irx = ref["^IRX"].reindex(eq.index, method="ffill")
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25

    notional = float(td["position_gbp"].sum())
    fx_pp = (notional * FX_ROUND_TRIP / yrs) / float(eq.mean()) * 100.0
    # panel stores a FRACTION in this study (see module docstring)
    idle_pp = float((1.0 - pn["current_deployed_pct"]).clip(lower=0).mean()) * float(irx.mean())
    raw = cagr(eq)
    adj = raw - fx_pp + idle_pp
    bench = cagr(tr)

    pnl = td["pnl_gbp"].astype(float)
    tot = float(pnl.sum())
    srt = pnl.sort_values(ascending=False)
    n = len(td)
    dec = []
    for d in range(10):
        lo, hi = int(round(d * n / 10)), int(round((d + 1) * n / 10))
        dec.append(round(100.0 * float(srt.iloc[lo:hi].sum()) / tot, 1) if tot else None)

    return {
        "arm": arm, "nominal_N": NOMINAL.get(arm),
        "n_trades": n,
        "mean_n_open": round(float(pn["n_open_positions"].mean()), 2),
        "realised_frac_of_nominal": (round(float(pn["n_open_positions"].mean()) / NOMINAL[arm], 3)
                                     if arm in NOMINAL else None),
        "mean_deployed_pct": round(float(pn["current_deployed_pct"].mean()) * 100.0, 2),
        "final_equity": round(float(eq.iloc[-1]), 2),
        "total_pnl_gbp": round(tot, 2),
        "frictionless_cagr": round(raw, 3),
        "fx_drag_pp": round(fx_pp, 3),
        "idle_credit_pp": round(idle_pp, 3),
        "cost_adjusted_cagr": round(adj, 3),
        "benchmark_cagr": round(bench, 3),
        "excess_vs_sp500tr": round(adj - bench, 3),
        "max_drawdown_pct": round(maxdd(eq), 2),
        "win_pct": round(100.0 * float((pnl > 0).mean()), 2),
        "mean_hold_days": round(float(td["holding_days"].mean()), 1),
        "distinct_tickers": int(td["ticker"].nunique()),
        "top3_pnl_share_pct": round(100.0 * float(srt.head(3).sum()) / tot, 2) if tot else None,
        "top10_pnl_share_pct": round(100.0 * float(srt.head(10).sum()) / tot, 2) if tot else None,
        "top_decile_pnl_share_pct": dec[0],
        "pnl_decile_curve_pct": dec,
        "top_ticker_pnl_share_pct": (
            round(100.0 * float(td.groupby("ticker")["pnl_gbp"].sum().max()) / tot, 2) if tot else None),
        "mean_rank_within_day": round(float(ad["rank_within_day"].mean()), 2),
        "median_rank_pctile_within_day": round(float(ad["rank_pctile_within_day"].median()) * 100, 3),
        "mean_eligible_per_admission": round(float(ad["n_eligible"].mean()), 1),
    }


def dominance(base_td, arm_td):
    """Sec 4.4 -- max_t |delta_t| / sum_t |delta_t|, grouped by TICKER,
    absolute on both sides so a large winner and a large loser cannot cancel."""
    a = base_td.groupby("ticker")["pnl_gbp"].sum()
    b = arm_td.groupby("ticker")["pnl_gbp"].sum()
    d = b.reindex(a.index.union(b.index), fill_value=0.0) - a.reindex(a.index.union(b.index), fill_value=0.0)
    denom = float(d.abs().sum())
    if denom == 0 or len(d) == 0:
        return {"measurable": False, "verdict": "BLOCKED -- unmeasurable"}
    top = d.abs().idxmax()
    share = float(d.abs().max()) / denom
    # position-level context (each trade separately), reported not gating
    pa = base_td.set_index(key(base_td))["pnl_gbp"]
    pb = arm_td.set_index(key(arm_td))["pnl_gbp"]
    pa, pb = pa[~pa.index.duplicated()], pb[~pb.index.duplicated()]
    idx = pa.index.union(pb.index)
    pd_ = pb.reindex(idx, fill_value=0.0) - pa.reindex(idx, fill_value=0.0)
    pdenom = float(pd_.abs().sum())
    return {
        "measurable": True,
        "n_contributing_tickers": int((d.abs() > 0).sum()),
        "top_ticker": str(top),
        "ticker_dominance": round(share, 4),
        "position_dominance_context": round(float(pd_.abs().max()) / pdenom, 4) if pdenom else None,
        "verdict": "PASS" if share <= DOMINANCE_CEILING else "EXCEEDS 0.40 CEILING",
    }


def overlap_and_null(base_td, arm_td, base_ad, arm_ad, rng):
    """Sec 4.5 -- Jaccard overlaps plus the pre-registered randomisation null:
    condition on the arm's own admission dates, draw the same number of
    admissions from each date's real gate-passing eligible set."""
    ka, kb = set(key(base_td)), set(key(arm_td))
    ta, tb = set(base_td["ticker"]), set(arm_td["ticker"])
    da = base_td.nlargest(max(1, len(base_td) // 10), "pnl_gbp")
    db = arm_td.nlargest(max(1, len(arm_td) // 10), "pnl_gbp")
    kda, kdb = set(key(da)), set(key(db))

    # Null: per admission date, the arm drew n_admitted from n_eligible.
    # P(a given baseline key on that date is drawn) = n_admitted / n_eligible.
    base_by_date = base_td.assign(d=pd.to_datetime(base_td["entry_date"]).dt.strftime("%Y-%m-%d")) \
                          .groupby("d").size()
    arm_dates = arm_ad.groupby("date").agg(n_adm=("ticker", "size"),
                                           n_elig=("n_eligible", "max"))
    draws = []
    for _ in range(NULL_DRAWS):
        exp = 0.0
        for d, row in arm_dates.iterrows():
            nb = int(base_by_date.get(d, 0))
            if nb == 0 or row.n_elig <= 0:
                continue
            p = min(1.0, float(row.n_adm) / float(row.n_elig))
            exp += rng.binomial(nb, p)
        draws.append(exp)
    draws = np.array(draws, dtype=float)
    obs = len(ka & kb)
    return {
        "exact_key_jaccard": round(len(ka & kb) / len(ka | kb), 4),
        "ticker_jaccard": round(len(ta & tb) / len(ta | tb), 4),
        "top_decile_exact_jaccard": round(len(kda & kdb) / len(kda | kdb), 4),
        "top_decile_ticker_jaccard": round(
            len(set(da["ticker"]) & set(db["ticker"])) / len(set(da["ticker"]) | set(db["ticker"])), 4),
        "observed_shared_keys": int(obs),
        "null_mean_shared": round(float(draws.mean()), 1),
        "null_sd": round(float(draws.std()), 1),
        "null_p5": round(float(np.percentile(draws, 5)), 1),
        "null_p95": round(float(np.percentile(draws, 95)), 1),
        "observed_percentile_in_null": round(100.0 * float((draws <= obs).mean()), 1),
        "reading": ("structural -- observed overlap is at or above chance"
                    if obs >= np.percentile(draws, 95)
                    else "observed overlap BELOW the null -- arms select different names"),
    }


def paired(base_td, arm_td):
    """Sec 4.7 -- exact-key pairing plus the disagreement-population fallback."""
    a, b = base_td.copy(), arm_td.copy()
    a["key"], b["key"] = key(a), key(b)
    shared = set(a["key"]) & set(b["key"])
    sa = a[a["key"].isin(shared)].set_index("key")["pnl_pct"]
    sb = b[b["key"].isin(shared)].set_index("key")["pnl_pct"]
    sa, sb = sa[~sa.index.duplicated()], sb[~sb.index.duplicated()]
    idx = sa.index.intersection(sb.index)
    diff = sb.loc[idx] - sa.loc[idx]
    identical = int((diff.abs() < 1e-12).sum())
    aonly, bonly = a[~a["key"].isin(shared)], b[~b["key"].isin(shared)]
    return {
        "n_shared_keys": int(len(idx)),
        "meets_shared_key_floor": bool(len(idx) >= SHARED_KEY_FLOOR),
        "byte_identical_shared_trades": identical,
        "arm_better_on_shared_pct": round(100.0 * float((diff > 0).mean()), 2) if len(idx) else None,
        "mean_shared_delta_pnl_pp": round(100.0 * float(diff.mean()), 3) if len(idx) else None,
        "n_baseline_only": int(len(aonly)), "n_arm_only": int(len(bonly)),
        "baseline_only_win_pct": round(100.0 * float((aonly["pnl_gbp"] > 0).mean()), 2) if len(aonly) else None,
        "arm_only_win_pct": round(100.0 * float((bonly["pnl_gbp"] > 0).mean()), 2) if len(bonly) else None,
        "baseline_only_mean_pnl_pct": round(100.0 * float(aonly["pnl_pct"].mean()), 3) if len(aonly) else None,
        "arm_only_mean_pnl_pct": round(100.0 * float(bonly["pnl_pct"].mean()), 3) if len(bonly) else None,
        "baseline_only_median_pnl_pct": round(100.0 * float(aonly["pnl_pct"].median()), 3) if len(aonly) else None,
        "arm_only_median_pnl_pct": round(100.0 * float(bonly["pnl_pct"].median()), 3) if len(bonly) else None,
        "meets_disagreement_floor": bool(len(aonly) >= SHARED_KEY_FLOOR and len(bonly) >= SHARED_KEY_FLOOR),
    }


def displaced(base_td, arm_slotfull, pool, arm):
    """Sec 4.7 fallback 3 -- baseline positions the arm's slot cap turned away
    on the very day baseline admitted them. Both outcomes come from the SAME
    baseline run, so this carries zero cascade contamination.

    Coverage caveat, disclosed not silent: the turned-away ticker list is
    truncated at SLOT_FULL_TICKER_LOG_CAP (50) by rank, so this measures the
    50 highest-ranked turned-away candidates per date, not all of them.
    """
    if arm_slotfull.empty:
        return {"measurable": False}
    sf = arm_slotfull.copy()
    sf["date"] = pd.to_datetime(sf["date"]).dt.strftime("%Y-%m-%d")
    turned = {}
    for d, grp in sf.groupby("date"):
        s = set()
        for v in grp["tickers"].dropna():
            s.update(str(v).split(";"))
        turned[d] = s
    b = base_td.copy()
    b["d"] = pd.to_datetime(b["entry_date"]).dt.strftime("%Y-%m-%d")
    mask = b.apply(lambda r: r["ticker"] in turned.get(r["d"], ()), axis=1)
    disp, kept = b[mask], b[~mask]
    if disp.empty:
        return {"measurable": False, "n_displaced": 0}
    return {
        "measurable": True,
        "n_displaced": int(len(disp)), "n_retained": int(len(kept)),
        "displaced_win_pct": round(100.0 * float((disp["pnl_gbp"] > 0).mean()), 2),
        "retained_win_pct": round(100.0 * float((kept["pnl_gbp"] > 0).mean()), 2),
        "displaced_mean_pnl_pct": round(100.0 * float(disp["pnl_pct"].mean()), 3),
        "retained_mean_pnl_pct": round(100.0 * float(kept["pnl_pct"].mean()), 3),
        "displaced_median_pnl_pct": round(100.0 * float(disp["pnl_pct"].median()), 3),
        "retained_median_pnl_pct": round(100.0 * float(kept["pnl_pct"].median()), 3),
        "coverage_note": "turned-away list truncated to top 50 by rank per date",
    }


def blocks(td, gspc):
    """Sec 4.8 -- corroborating only. Uses the SHARED _block_passes() and
    make_blocks() rather than a reimplementation."""
    t = td.copy()
    t["exit_date"] = pd.to_datetime(t["exit_date"])
    start, end = pd.Timestamp("2007-01-03"), pd.Timestamp("2026-08-07")
    npass = 0
    for b0, b1 in make_blocks(start, end, n_blocks=16):
        sub = t[(t["exit_date"] >= b0) & (t["exit_date"] <= b1)].to_dict("records")
        m = compute_metrics_fixed(sub, STARTING_CAPITAL)
        w = gspc[(gspc.index >= b0) & (gspc.index <= b1)]
        sp_dd = float((w / w.cummax() - 1.0).min() * 100.0) if len(w) >= 2 else None
        if _block_passes(m, sp_dd):
            npass += 1
    return npass


def main() -> None:
    with open(ROOT / "research" / "exit_mechanics_prereg_2026-08-11" / "_reference_series.pkl", "rb") as fh:
        ref = pickle.load(fh)
    with open(HERE / "_cache_pool2_clean.pkl", "rb") as fh:
        gspc = pickle.load(fh)["gspc_close"]

    out = {}
    for pool in ("pool2_clean", "pool1"):
        rng = np.random.default_rng(SEED)
        base_td, base_pn, base_ad = load(pool, "baseline")
        res = {"baseline": core(pool, "baseline", ref)}
        res["baseline"]["block_pass_16"] = blocks(base_td, gspc)
        base_ex = res["baseline"]["excess_vs_sp500tr"]

        for arm in TESTED + NOT_TESTED:
            td, pn, ad = load(pool, arm)
            r = core(pool, arm, ref)
            r["delta_vs_baseline_pp"] = round(r["excess_vs_sp500tr"] - base_ex, 3)
            r["clears_bar"] = bool(r["delta_vs_baseline_pp"] >= BAR_PP)
            dd_delta = r["max_drawdown_pct"] - res["baseline"]["max_drawdown_pct"]
            r["dd_delta_vs_baseline_pp"] = round(dd_delta, 2)
            r["clause_A_dd_relative"] = ("BREACH" if dd_delta < -DD_CLAUSE_A_PP else "pass")
            r["clause_B_dd_absolute"] = ("BREACH" if r["max_drawdown_pct"] < DD_CLAUSE_B_ABS else "pass")
            r["top3_flag"] = ("EXCEEDS 50%" if (r["top3_pnl_share_pct"] or 0) > TOP3_FLAG else "ok")
            r["block_pass_16"] = blocks(td, gspc)
            r["dominance"] = dominance(base_td, td)
            r["overlap"] = overlap_and_null(base_td, td, base_ad, ad, rng)
            r["paired"] = paired(base_td, td)
            sf_path = HERE / f"slotfull_{pool}_{arm}.csv"
            r["displaced"] = displaced(base_td, pd.read_csv(sf_path), pool, arm) \
                if sf_path.exists() else {"measurable": False}
            r["status"] = "TESTED" if arm in TESTED else "NOT TESTED (Sec 2.1 materiality)"
            res[arm] = r
        out[pool] = res

    (HERE / "step5_instruments.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    for pool, res in out.items():
        print(f"\n{'='*104}\n{pool}\n{'='*104}")
        print(f"{'arm':<10}{'N':>6}{'meanN':>7}{'trades':>7}{'fCAGR':>8}{'fx':>7}{'idle':>6}"
              f"{'costCAGR':>9}{'excess':>8}{'dVSbase':>9}{'maxDD':>8}{'ddDelta':>8}{'blocks':>7}")
        for arm, r in res.items():
            print(f"{arm:<10}{str(r['nominal_N'] or '-'):>6}{r['mean_n_open']:>7.2f}{r['n_trades']:>7}"
                  f"{r['frictionless_cagr']:>8.3f}{r['fx_drag_pp']:>7.3f}{r['idle_credit_pp']:>6.3f}"
                  f"{r['cost_adjusted_cagr']:>9.3f}{r['excess_vs_sp500tr']:>8.3f}"
                  f"{r.get('delta_vs_baseline_pp', 0.0):>+9.3f}{r['max_drawdown_pct']:>8.2f}"
                  f"{r.get('dd_delta_vs_baseline_pp', 0.0):>+8.2f}{r['block_pass_16']:>5}/16")
    print("\nWrote step5_instruments.json")


if __name__ == "__main__":
    main()
