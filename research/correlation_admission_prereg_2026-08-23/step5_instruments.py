"""PREREGISTRATION.md Sec 4 + Sec 5 -- the return stage.

Run ONLY after the Sec 3 gate is complete and reviewed. Sec 4.3 (realised
book correlation) and Sec 4.5 (deployment) are already reported by
step4_gate.py, which belongs before returns; they are re-surfaced here for
the headline table only.

  4.1  cost-adjusted excess return over ^SP500TR vs baseline's own excess
  4.2  drawdown, Clause A (15pp relative) and Clause B (-60% absolute)
  4.4  sector composition (Herfindahl + top-3 shares), held-day weighted
  4.6  overlap + randomisation null
  4.7  block-pass count, corroborating only
  5    disagreement-population comparison + rank-at-admission
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
START_CAP = 50000.0
ARMS = ["baseline", "D1", "D2", "D3", "R1", "R2", "R3"]
K = {"baseline": 0.0, "D1": 0.25, "D2": 0.5, "D3": 1.0, "R1": -0.25, "R2": -0.5, "R3": -1.0}
BAR = 1.5
DD_A, DD_B = 15.0, -60.0
FLOOR = 100
NULL_DRAWS = 1000
SEED = 20260823
SECTOR_MAP = ROOT / "research/entry_volume_sector_2026-08-07/sector_map.csv"


def cagr(s):
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100.0


def maxdd(s):
    return float((s / s.cummax() - 1.0).min() * 100.0)


def key(df):
    return df["ticker"] + "|" + pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")


def main() -> None:
    pool = sys.argv[1]
    with open(ROOT / "research/exit_mechanics_prereg_2026-08-11/_reference_series.pkl", "rb") as fh:
        ref = pickle.load(fh)
    with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
        gspc = pickle.load(fh)["gspc_close"]
    sec = pd.read_csv(SECTOR_MAP).set_index("ticker")["sector"].to_dict()
    rng = np.random.default_rng(SEED)

    base_t = pd.read_csv(HERE / f"trades_{pool}_baseline.csv")
    base_keys, base_tick = set(key(base_t)), set(base_t["ticker"])
    gate = json.load(open(HERE / f"step4_gate_{pool}.json"))

    out = {}
    base_excess = None
    for arm in ARMS:
        t = pd.read_csv(HERE / f"trades_{pool}_{arm}.csv")
        pn = pd.read_csv(HERE / f"panel_{pool}_{arm}.csv")
        ad = pd.read_csv(HERE / f"admissions_{pool}_{arm}.csv")
        pn["date"] = pd.to_datetime(pn["date"])
        pn = pn.set_index("date").sort_index()
        eq = pn["current_balance"].astype(float)
        tr = ref["^SP500TR"].reindex(eq.index, method="ffill").dropna()
        irx = ref["^IRX"].reindex(eq.index, method="ffill")
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25

        notional = float(t["position_gbp"].sum())
        fx_pp = (notional * FX_ROUND_TRIP / yrs) / float(eq.mean()) * 100.0
        idle_pp = float((1.0 - pn["current_deployed_pct"]).clip(lower=0).mean()) * float(irx.mean())
        raw = cagr(eq)
        adj = raw - fx_pp + idle_pp
        bench = cagr(tr)

        # 4.4 sector composition, HELD-DAY weighted (portfolio composition over
        # time, not admission counts). Unmapped tickers are reported, never
        # silently dropped.
        cnt, unmapped = {}, 0
        for h in pn["held"].fillna(""):
            for tk in str(h).split(";"):
                if not tk:
                    continue
                s = sec.get(tk)
                if s is None or (isinstance(s, float) and np.isnan(s)):
                    unmapped += 1
                    s = "Unmapped"
                cnt[s] = cnt.get(s, 0) + 1
        tot = sum(cnt.values()) or 1
        shares = sorted(((v / tot, k) for k, v in cnt.items()), reverse=True)
        hhi = sum((v / tot) ** 2 for k, v in cnt.items() if k != "Unmapped")

        # 4.7 blocks
        tt = t.copy(); tt["exit_date"] = pd.to_datetime(tt["exit_date"])
        npass = 0
        for b0, b1 in make_blocks(pd.Timestamp("2007-01-03"), pd.Timestamp("2026-08-07"), 16):
            sub = tt[(tt["exit_date"] >= b0) & (tt["exit_date"] <= b1)].to_dict("records")
            m = compute_metrics_fixed(sub, START_CAP)
            w = gspc[(gspc.index >= b0) & (gspc.index <= b1)]
            sp = float((w / w.cummax() - 1.0).min() * 100.0) if len(w) >= 2 else None
            npass += int(_block_passes(m, sp))

        r = {
            "k": K[arm], "n_trades": int(len(t)),
            "frictionless_cagr": round(raw, 3), "fx_drag_pp": round(fx_pp, 3),
            "idle_credit_pp": round(idle_pp, 3), "cost_adjusted_cagr": round(adj, 3),
            "benchmark_cagr": round(bench, 3), "excess_vs_sp500tr": round(adj - bench, 3),
            "max_drawdown_pct": round(maxdd(eq), 2),
            "win_pct": round(100.0 * float((t["pnl_gbp"] > 0).mean()), 2),
            "mean_hold_days": round(float(t["holding_days"].mean()), 1),
            "distinct_tickers": int(t["ticker"].nunique()),
            "block_pass_16": npass,
            "sector_hhi": round(hhi, 4),
            "top3_sectors": [(k, round(100 * v, 1)) for v, k in shares[:3]],
            "unmapped_held_day_share_pct": round(100.0 * unmapped / tot, 1),
            "realised_book_correlation": gate[arm].get("realised_book_correlation"),
            "mean_deployed_pct": gate[arm]["mean_deployed_pct"],
            "admissions_changed_pct": gate[arm]["admissions_changed_pct"],
            "median_admitted_within_day_pctile": gate[arm].get("median_admitted_within_day_pctile"),
        }
        if arm == "baseline":
            base_excess = r["excess_vs_sp500tr"]
        else:
            r["delta_vs_baseline_pp"] = round(r["excess_vs_sp500tr"] - base_excess, 3)
            r["clears_bar"] = bool(r["delta_vs_baseline_pp"] >= BAR)
            dd_d = r["max_drawdown_pct"] - out["baseline"]["max_drawdown_pct"]
            r["dd_delta_vs_baseline_pp"] = round(dd_d, 2)
            r["clause_A_dd"] = "BREACH" if dd_d < -DD_A else "pass"
            r["clause_B_dd"] = "BREACH" if r["max_drawdown_pct"] < DD_B else "pass"

            # 4.6 overlap + null
            ks, tk = set(key(t)), set(t["ticker"])
            da = base_t.nlargest(max(1, len(base_t) // 10), "pnl_gbp")
            db = t.nlargest(max(1, len(t) // 10), "pnl_gbp")
            obs = len(ks & base_keys)
            arm_dates = ad.groupby("date").agg(n_adm=("ticker", "size"), n_el=("n_eligible", "max"))
            b_by_date = base_t.assign(d=pd.to_datetime(base_t["entry_date"]).dt.strftime("%Y-%m-%d")).groupby("d").size()
            draws = []
            for _ in range(NULL_DRAWS):
                e = 0
                for d, row in arm_dates.iterrows():
                    nb = int(b_by_date.get(str(d), 0))
                    if nb and row.n_el > 0:
                        e += rng.binomial(nb, min(1.0, row.n_adm / row.n_el))
                draws.append(e)
            draws = np.array(draws, float)
            r["overlap"] = {
                "exact_key_jaccard": round(len(ks & base_keys) / len(ks | base_keys), 4),
                "ticker_jaccard": round(len(tk & base_tick) / len(tk | base_tick), 4),
                "top_decile_exact_jaccard": round(
                    len(set(key(da)) & set(key(db))) / len(set(key(da)) | set(key(db))), 4),
                "top_decile_ticker_jaccard": round(
                    len(set(da["ticker"]) & set(db["ticker"])) / len(set(da["ticker"]) | set(db["ticker"])), 4),
                "observed_shared": int(obs), "null_mean": round(float(draws.mean()), 1),
                "null_sd": round(float(draws.std()), 1),
                "observed_pctile_in_null": round(100.0 * float((draws <= obs).mean()), 1),
            }

            # 5 disagreement population
            aonly = base_t[~key(base_t).isin(ks)]
            bonly = t[~key(t).isin(base_keys)]
            r["disagreement"] = {
                "n_baseline_only": int(len(aonly)), "n_arm_only": int(len(bonly)),
                "meets_floor": bool(len(aonly) >= FLOOR and len(bonly) >= FLOOR),
                "baseline_only_win_pct": round(100.0 * float((aonly["pnl_gbp"] > 0).mean()), 2) if len(aonly) else None,
                "arm_only_win_pct": round(100.0 * float((bonly["pnl_gbp"] > 0).mean()), 2) if len(bonly) else None,
                "baseline_only_mean_pnl_pct": round(100.0 * float(aonly["pnl_pct"].mean()), 3) if len(aonly) else None,
                "arm_only_mean_pnl_pct": round(100.0 * float(bonly["pnl_pct"].mean()), 3) if len(bonly) else None,
                "baseline_only_median_pnl_pct": round(100.0 * float(aonly["pnl_pct"].median()), 3) if len(aonly) else None,
                "arm_only_median_pnl_pct": round(100.0 * float(bonly["pnl_pct"].median()), 3) if len(bonly) else None,
            }
        out[arm] = r

    (HERE / f"step5_instruments_{pool}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n{'='*118}\n{pool}\n{'='*118}")
    print(f"{'arm':<9}{'k':>7}{'trades':>7}{'fCAGR':>8}{'fx':>6}{'idle':>6}{'costCAGR':>9}"
          f"{'excess':>8}{'dVSbase':>9}{'maxDD':>8}{'ddDelta':>8}{'bookCorr':>9}{'blocks':>7}")
    for arm, r in out.items():
        print(f"{arm:<9}{r['k']:>+7.2f}{r['n_trades']:>7}{r['frictionless_cagr']:>8.3f}"
              f"{r['fx_drag_pp']:>6.3f}{r['idle_credit_pp']:>6.3f}{r['cost_adjusted_cagr']:>9.3f}"
              f"{r['excess_vs_sp500tr']:>8.3f}{r.get('delta_vs_baseline_pp', 0.0):>+9.3f}"
              f"{r['max_drawdown_pct']:>8.2f}{r.get('dd_delta_vs_baseline_pp', 0.0):>+8.2f}"
              f"{str(r['realised_book_correlation']):>9}{r['block_pass_16']:>5}/16")
    print(f"\nWrote step5_instruments_{pool}.json")


if __name__ == "__main__":
    main()
