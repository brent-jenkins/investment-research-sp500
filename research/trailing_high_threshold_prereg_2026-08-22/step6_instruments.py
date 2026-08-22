"""Sec 6 instruments for every completed cell.

Cost-adjusted CAGR = frictionless CAGR - FX drag + idle-cash credit, per cell
(never deferred: turnover IS the treatment here, and the dials move it in
opposite directions).

Also: excess vs ^SP500TR, drawdown, turnover, exit composition, capacity-turnover
(separate from direct effect), paired per-decision instrument vs baseline, and
top-decile overlap.

Usage: python step4_instruments.py <pool> [cell_id ...]
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

FX_ROUND_TRIP = 0.0030
BASELINE = "a_none"   # this study: baseline IS the live s3.0 config


def cagr(s: pd.Series) -> float:
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100.0


def maxdd(s: pd.Series) -> float:
    return float((s / s.cummax() - 1.0).min() * 100.0)


def load_cell(pool, cid):
    td = pd.read_csv(HERE / f"trades_{pool}_{cid}.csv")
    pn = pd.read_csv(HERE / f"panel_{pool}_{cid}.csv")
    pn["date"] = pd.to_datetime(pn["date"])
    return td, pn.set_index("date").sort_index()


def analyse(pool, cid, ref, base_td=None):
    td, pn = load_cell(pool, cid)
    eq = pn["current_balance"].astype(float)
    tr = ref["^SP500TR"].reindex(eq.index, method="ffill").dropna()
    irx = ref["^IRX"].reindex(eq.index, method="ffill")
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25

    notional = float(td["position_gbp"].sum())
    fx = notional * FX_ROUND_TRIP
    fx_pp = (fx / yrs) / float(eq.mean()) * 100.0
    idle_pp = float((1 - pn["current_deployed_pct"] / 100.0).clip(lower=0).mean()) * float(irx.mean())
    raw = cagr(eq)
    adj = raw - fx_pp + idle_pp
    bench = cagr(tr)

    mix = td["exit_reason"].value_counts()
    r = {
        "pool": pool, "cell": cid, "n_trades": int(len(td)),
        "final_equity": round(float(eq.iloc[-1]), 2),
        "frictionless_cagr": round(raw, 3),
        "fx_drag_pp": round(fx_pp, 3), "idle_credit_pp": round(idle_pp, 3),
        "cost_adjusted_cagr": round(adj, 3),
        "benchmark_cagr": round(bench, 3),
        "excess_vs_sp500tr": round(adj - bench, 3),
        "max_drawdown_pct": round(maxdd(eq), 2),
        "win_pct": round(100 * float((td["pnl_gbp"] > 0).mean()), 2),
        "mean_hold_days": round(float(td["holding_days"].mean()), 2),
        "total_notional_gbp": round(notional, 2),
        "mean_deployed_pct": round(float(pn["current_deployed_pct"].mean()), 3),
        "mean_n_open": round(float(pn["n_open_positions"].mean()), 2),
        "exit_mix_pct": {k: round(100.0 * v / len(td), 2) for k, v in mix.items()},
        "exit_mix_n": {k: int(v) for k, v in mix.items()},
        "exit_pnl_by_reason": {k: round(float(v), 2)
                               for k, v in td.groupby("exit_reason")["pnl_gbp"].sum().items()},
    }

    if base_td is not None and cid != BASELINE:
        a, b = base_td.copy(), td.copy()
        for f in (a, b):
            f["key"] = f["ticker"] + "|" + pd.to_datetime(f["entry_date"]).dt.strftime("%Y-%m-%d")
        ka, kb = set(a["key"]), set(b["key"])
        shared = ka & kb
        sa = a[a["key"].isin(shared)].set_index("key")["pnl_pct"]
        sb = b[b["key"].isin(shared)].set_index("key")["pnl_pct"]
        sa, sb = sa[~sa.index.duplicated()], sb[~sb.index.duplicated()]
        idx = sa.index.intersection(sb.index)
        diff = (sb.loc[idx] - sa.loc[idx])
        aonly, bonly = a[~a["key"].isin(shared)], b[~b["key"].isin(shared)]
        # top-decile overlap
        topa = set(a.nlargest(max(1, len(a) // 10), "pnl_gbp")["key"])
        topb = set(b.nlargest(max(1, len(b) // 10), "pnl_gbp")["key"])
        r["paired"] = {
            "n_shared_keys": int(len(idx)),
            "cell_better_on_shared_pct": round(100.0 * float((diff > 0).mean()), 2),
            "mean_shared_delta_pnl_pct": round(100.0 * float(diff.mean()), 4),
            "median_shared_delta_pnl_pct": round(100.0 * float(diff.median()), 4),
            "n_baseline_only": int(len(aonly)), "n_cell_only": int(len(bonly)),
            "baseline_only_win_pct": round(100 * float((aonly["pnl_gbp"] > 0).mean()), 2) if len(aonly) else None,
            "cell_only_win_pct": round(100 * float((bonly["pnl_gbp"] > 0).mean()), 2) if len(bonly) else None,
            "baseline_only_mean_pnl_pct": round(100 * float(aonly["pnl_pct"].mean()), 3) if len(aonly) else None,
            "cell_only_mean_pnl_pct": round(100 * float(bonly["pnl_pct"].mean()), 3) if len(bonly) else None,
            "meets_min_sample_floor": bool(len(aonly) >= 100 and len(bonly) >= 100),
            "top_decile_jaccard": round(len(topa & topb) / len(topa | topb), 4),
            "cell_pnl_share_from_non_shared": round(
                100.0 * float(bonly["pnl_gbp"].sum()) / float(b["pnl_gbp"].sum()), 2)
            if float(b["pnl_gbp"].sum()) else None,
        }
        r["capacity_turnover"] = {
            "trade_count_delta": int(len(b) - len(a)),
            "trade_count_delta_pct": round(100.0 * (len(b) - len(a)) / len(a), 2),
            "notional_delta_pct": round(
                100.0 * (notional - float(a["position_gbp"].sum())) / float(a["position_gbp"].sum()), 2),
        }
    return r


def main() -> None:
    pool = sys.argv[1]
    cells = sys.argv[2:] or [BASELINE, "h60", "h90", "hunlimited"]
    with open(HERE / "_reference_series.pkl", "rb") as fh:
        ref = pickle.load(fh)
    base_td = pd.read_csv(HERE / f"trades_{pool}_{BASELINE}.csv")

    out, base_ex = {}, None
    for cid in cells:
        if not (HERE / f"trades_{pool}_{cid}.csv").exists():
            print(f"  {cid}: not run yet"); continue
        r = analyse(pool, cid, ref, base_td)
        out[cid] = r
        if cid == BASELINE:
            base_ex = r["excess_vs_sp500tr"]

    print(f"\n{'cell':>12} {'n':>6} {'fCAGR':>7} {'fx':>7} {'idle':>6} {'costCAGR':>9} "
          f"{'excess':>8} {'dvsBase':>8} {'maxDD':>8} {'hold':>7} {'dep%':>6}")
    for cid, r in out.items():
        d = round(r["excess_vs_sp500tr"] - base_ex, 3) if base_ex is not None else float("nan")
        r["delta_vs_baseline_pp"] = d
        print(f"{cid:>12} {r['n_trades']:>6,} {r['frictionless_cagr']:>7.3f} "
              f"{r['fx_drag_pp']:>7.3f} {r['idle_credit_pp']:>6.3f} {r['cost_adjusted_cagr']:>9.3f} "
              f"{r['excess_vs_sp500tr']:>8.3f} {d:>+8.3f} {r['max_drawdown_pct']:>8.2f} "
              f"{r['mean_hold_days']:>7.1f} {r['mean_deployed_pct']:>6.2f}")

    print("\nexit composition (% of exits):")
    for cid, r in out.items():
        print(f"  {cid:>12}: {r['exit_mix_pct']}")

    print("\npaired per-decision instrument (PRIMARY evidence) + overlap:")
    for cid, r in out.items():
        p = r.get("paired")
        if not p:
            continue
        print(f"  {cid:>12}: shared={p['n_shared_keys']:,}  cell better on shared="
              f"{p['cell_better_on_shared_pct']}%  mean delta={p['mean_shared_delta_pnl_pct']:+.3f}pp  "
              f"topdec_jacc={p['top_decile_jaccard']}  floor_ok={p['meets_min_sample_floor']}")
        print(f"  {'':>12}  base-only {p['n_baseline_only']:,} @ {p['baseline_only_win_pct']}%/"
              f"{p['baseline_only_mean_pnl_pct']}%   cell-only {p['n_cell_only']:,} @ "
              f"{p['cell_only_win_pct']}%/{p['cell_only_mean_pnl_pct']}%   "
              f"non-shared P&L share={p['cell_pnl_share_from_non_shared']}%")

    (HERE / f"instruments_{pool}.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote instruments_{pool}.json")


if __name__ == "__main__":
    main()
