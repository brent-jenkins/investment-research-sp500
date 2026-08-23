"""DIAGNOSTIC B -- are trade outcomes serially correlated?

READ-ONLY. No arms, no engine, no adoption. Reads the PRESERVED trade sets and
panels for the current live configuration and the ^GSPC series already in
price_data. No database writes.

MOTIVATION: a proposal to pause new admissions after a run of losses. That rule
only makes sense if outcomes are serially correlated. If they are independent,
pausing means sitting out periods identical in expectation to any other -- and
momentum strategies historically have their best periods immediately after
drawdowns, so pausing would systematically miss recoveries.

PRE-COMMITTED: the expected result is no meaningful serial correlation. Point 5
decides whether anything found is a SEQUENCE effect or shared market
conditions; only the former would support a pausing rule.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from data.database import get_engine, get_db_path  # noqa: E402

REF = ROOT / "research/exit_mechanics_prereg_2026-08-11"
POOLS = {"pool2_clean": "pool2_clean", "pool1": "pool1"}


def load_gspc() -> pd.Series:
    with get_engine().connect() as c:
        df = pd.read_sql(text(
            "SELECT date, close FROM price_data WHERE ticker='^GSPC' ORDER BY date"), c)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df.set_index("date")["close"].astype(float)


def block_bootstrap_ci(x: np.ndarray, lag: int, n: int = 2000, block: int = 20, seed: int = 20260823):
    """Moving-block bootstrap CI for an autocorrelation. Plain iid CIs would be
    too narrow here: the trades overlap in time by construction, so the series
    is not independent even under the null of no SEQUENCE effect."""
    rng = np.random.default_rng(seed)
    m = len(x)
    if m <= lag + block:
        return None, None
    nblocks = int(np.ceil(m / block))
    stats = []
    for _ in range(n):
        starts = rng.integers(0, m - block, size=nblocks)
        s = np.concatenate([x[i:i + block] for i in starts])[:m]
        a, b = s[:-lag], s[lag:]
        if a.std() == 0 or b.std() == 0:
            continue
        stats.append(float(np.corrcoef(a, b)[0, 1]))
    if not stats:
        return None, None
    return round(float(np.percentile(stats, 2.5)), 4), round(float(np.percentile(stats, 97.5)), 4)


def main() -> None:
    print(f"DB: {get_db_path()}")
    gspc = load_gspc()
    out = {}

    for pool in POOLS:
        t = pd.read_csv(REF / f"trades_{pool}_s3.0.csv")
        pn = pd.read_csv(REF / f"panel_{pool}_s3.0.csv")
        t["entry_date"] = pd.to_datetime(t["entry_date"])
        t["exit_date"] = pd.to_datetime(t["exit_date"])
        t = t.sort_values("exit_date").reset_index(drop=True)
        pn["date"] = pd.to_datetime(pn["date"])
        pn = pn.set_index("date").sort_index()

        r = t["pnl_pct"].astype(float).to_numpy() * 100.0
        n = len(r)
        res = {"n_trades": n, "mean_pnl_pct": round(float(r.mean()), 3),
               "median_pnl_pct": round(float(np.median(r)), 3),
               "win_rate_pct": round(100.0 * float((r > 0).mean()), 2)}

        # ---- 1. autocorrelation, lags 1-10
        ac = {}
        for lag in range(1, 11):
            a, b = r[:-lag], r[lag:]
            rho = float(np.corrcoef(a, b)[0, 1])
            lo, hi = block_bootstrap_ci(r, lag)
            ac[lag] = {"pearson": round(rho, 4), "ci95": [lo, hi],
                       "spearman": round(float(pd.Series(a).corr(pd.Series(b), method="spearman")), 4)}
        res["autocorrelation"] = ac

        # ---- 2/3. conditional on prior outcome and on runs
        def after(cond_fn, k):
            idx = [i for i in range(k, n) if cond_fn(r[i - k:i])]
            if not idx:
                return None
            nxt = r[idx]
            return {"n": len(idx), "mean_pnl_pct": round(float(nxt.mean()), 3),
                    "median_pnl_pct": round(float(np.median(nxt)), 3),
                    "win_rate_pct": round(100.0 * float((nxt > 0).mean()), 2)}

        res["unconditional"] = {"n": n, "mean_pnl_pct": res["mean_pnl_pct"],
                                "median_pnl_pct": res["median_pnl_pct"],
                                "win_rate_pct": res["win_rate_pct"]}
        res["after_losses"] = {f"run_{k}": after(lambda w: bool((w <= 0).all()), k)
                               for k in (1, 2, 3, 5)}
        res["after_wins"] = {f"run_{k}": after(lambda w: bool((w > 0).all()), k)
                             for k in (1, 2, 3, 5)}

        # ---- 4. portfolio-level: forward return after a drawdown
        eq = pn["current_balance"].astype(float)
        dd = (eq / eq.cummax() - 1.0) * 100.0
        fwd = {}
        for h in (30, 90):
            tgt = eq.index + pd.Timedelta(days=h)
            pos = eq.index.searchsorted(tgt, side="right") - 1
            ok = (pos > np.arange(len(eq))) & (tgt <= eq.index[-1])
            f = np.full(len(eq), np.nan)
            f[ok] = eq.to_numpy()[pos[ok]] / eq.to_numpy()[np.arange(len(eq))[ok]] - 1.0
            fwd[h] = pd.Series(f * 100.0, index=eq.index)
        pf = {}
        for thr in (0, 5, 10, 20, 30):
            m = dd <= -thr if thr else pd.Series(True, index=dd.index)
            row = {"days": int(m.sum()), "pct_of_days": round(100.0 * float(m.mean()), 1)}
            for h in (30, 90):
                v = fwd[h][m].dropna()
                row[f"fwd{h}_mean"] = round(float(v.mean()), 3) if len(v) else None
                row[f"fwd{h}_median"] = round(float(v.median()), 3) if len(v) else None
                row[f"fwd{h}_n"] = int(len(v))
            pf[f"dd_ge_{thr}pct" if thr else "unconditional"] = row
        res["portfolio_after_drawdown"] = pf

        # ---- 5. control for the concurrent index return
        gi = gspc.reindex(pd.date_range(gspc.index[0], gspc.index[-1])).ffill()
        mkt = []
        for _, row in t.iterrows():
            try:
                a = gi.loc[row["entry_date"]]
                b = gi.loc[row["exit_date"]]
                mkt.append((b / a - 1.0) * 100.0)
            except KeyError:
                mkt.append(np.nan)
        mkt = np.array(mkt, dtype=float)
        good = np.isfinite(mkt)
        X = np.column_stack([np.ones(good.sum()), mkt[good]])
        y = r[good]
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        ac_res = {}
        for lag in (1, 2, 3, 5, 10):
            a, b = resid[:-lag], resid[lag:]
            ac_res[lag] = round(float(np.corrcoef(a, b)[0, 1]), 4)
        res["market_control"] = {
            "n_with_market": int(good.sum()),
            "beta_on_concurrent_index_return": round(float(beta[1]), 4),
            "r2": round(float(1 - resid.var() / y.var()), 4),
            "residual_autocorrelation": ac_res,
            "raw_lag1": ac[1]["pearson"],
        }
        out[pool] = res

    (HERE / "diagnostic_b.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    for pool, res in out.items():
        print(f"\n{'='*90}\n{pool}  --  n={res['n_trades']}, mean {res['mean_pnl_pct']:+.3f}%, "
              f"median {res['median_pnl_pct']:+.3f}%, win {res['win_rate_pct']}%\n{'='*90}")
        print("1. autocorrelation of pnl_pct, ordered by exit date")
        print(f"   {'lag':>4}{'pearson':>10}{'95% CI (block bootstrap)':>28}{'spearman':>11}")
        for lag, v in res["autocorrelation"].items():
            ci = f"[{v['ci95'][0]}, {v['ci95'][1]}]" if v["ci95"][0] is not None else "n/a"
            print(f"   {lag:>4}{v['pearson']:>10.4f}{ci:>28}{v['spearman']:>11.4f}")
        u = res["unconditional"]
        print(f"\n2/3. conditional on prior outcomes (unconditional: n={u['n']} "
              f"mean {u['mean_pnl_pct']:+.3f}% win {u['win_rate_pct']}%)")
        for label, blk in (("after N losses", res["after_losses"]), ("after N wins", res["after_wins"])):
            for kk, v in blk.items():
                if v:
                    print(f"   {label:<15} {kk:<8} n={v['n']:>4}  mean {v['mean_pnl_pct']:+8.3f}%  "
                          f"median {v['median_pnl_pct']:+8.3f}%  win {v['win_rate_pct']:>5.2f}%")
        print("\n4. portfolio-level forward return after a drawdown from peak")
        print(f"   {'condition':<18}{'days':>7}{'%days':>7}{'fwd30 mean':>12}{'fwd90 mean':>12}{'fwd90 med':>11}")
        for k, v in res["portfolio_after_drawdown"].items():
            print(f"   {k:<18}{v['days']:>7}{v['pct_of_days']:>7.1f}"
                  f"{v['fwd30_mean']:>12.3f}{v['fwd90_mean']:>12.3f}{v['fwd90_median']:>11.3f}")
        mc = res["market_control"]
        print(f"\n5. controlling for concurrent index return over each trade's own holding period")
        print(f"   beta {mc['beta_on_concurrent_index_return']}  R2 {mc['r2']}  "
              f"(n={mc['n_with_market']})")
        print(f"   raw lag-1 {mc['raw_lag1']}  ->  residual autocorrelation {mc['residual_autocorrelation']}")


if __name__ == "__main__":
    main()
