"""Sec 8/9/10 -- all reporting instruments.

Costs (Sec 8), primary excess-return criterion (9.1), correlation (9.2),
drawdown (9.3), exposure (9.4), blocks + the 2009 momentum-crash window (9.5),
the short sleeve's unrealizable-risk share (9.6), and the paired per-decision
instrument (Sec 10, LONG SLEEVE ONLY -- there is no baseline counterpart to
pair a short against, and no sample size creates one).

IMPORTANT ON DOUBLE-COUNTING: borrow cost is ALREADY inside each arm's equity
curve (it is deducted from cash daily by the engine). FX and idle-cash are NOT
(the engine is otherwise frictionless), so only those two are applied here.

Usage: python step7_instruments.py <pool1|pool2_clean>
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

from analysis.backtest import make_blocks                              # noqa: E402
from data.ingestion import _KNOWN_PRICE_CLIFF_EXCEPTIONS               # noqa: E402

FX_ROUND_TRIP_FEE = 0.0030
MOMENTUM_CRASH = (pd.Timestamp("2009-03-09"), pd.Timestamp("2009-09-30"))
CLIFF_TICKERS = {t for (t, _d) in _KNOWN_PRICE_CLIFF_EXCEPTIONS}


def load_reference():
    with open(HERE / "_reference_series.pkl", "rb") as fh:
        return pickle.load(fh)


def cagr(series: pd.Series) -> float:
    if len(series) < 2 or series.iloc[0] <= 0 or series.iloc[-1] <= 0:
        return float("nan")
    yrs = (series.index[-1] - series.index[0]).days / 365.25
    return ((series.iloc[-1] / series.iloc[0]) ** (1 / yrs) - 1) * 100.0


def max_drawdown_pct(series: pd.Series) -> float:
    """Peak-relative max drawdown, as a negative percentage."""
    peak = series.cummax()
    return float((series / peak - 1.0).min() * 100.0)


def analyse(tag, pool, ref, arm_a_trades=None):
    td = pd.read_csv(HERE / f"trades_{pool}_{tag}.csv")
    pn = pd.read_csv(HERE / f"panel_{pool}_{tag}.csv")
    pn["date"] = pd.to_datetime(pn["date"])
    pn = pn.set_index("date").sort_index()
    eq = pn["equity"].astype(float)

    tr = ref["^SP500TR"].reindex(eq.index, method="ffill").dropna()
    eq_al = eq.reindex(tr.index)
    irx = ref["^IRX"].reindex(eq.index, method="ffill")

    years = (eq.index[-1] - eq.index[0]).days / 365.25

    # ---- Sec 8: costs NOT already in the equity curve ----
    total_notional = float(td["position_gbp"].sum()) if len(td) else 0.0
    fx_cost = total_notional * FX_ROUND_TRIP_FEE
    mean_equity = float(eq.mean())
    fx_drag_pp = (fx_cost / years) / mean_equity * 100.0
    mean_free_frac = float((pn["free_cash"] / pn["equity"]).clip(lower=0).mean())
    mean_irx = float(irx.mean())
    idle_credit_pp = mean_free_frac * mean_irx

    raw_cagr = cagr(eq)                       # already net of borrow
    cost_adj_cagr = raw_cagr - fx_drag_pp + idle_credit_pp
    bench_cagr = cagr(tr)
    excess = cost_adj_cagr - bench_cagr

    # ---- Sec 9.2: correlation ----
    m_arm = eq_al.resample("ME").last().pct_change().dropna()
    m_ben = tr.resample("ME").last().pct_change().dropna()
    idx = m_arm.index.intersection(m_ben.index)
    corr_m = float(np.corrcoef(m_arm.loc[idx], m_ben.loc[idx])[0, 1]) if len(idx) > 2 else float("nan")
    d_arm = eq_al.pct_change().dropna()
    d_ben = tr.pct_change().dropna()
    didx = d_arm.index.intersection(d_ben.index)
    corr_d = float(np.corrcoef(d_arm.loc[didx], d_ben.loc[didx])[0, 1]) if len(didx) > 2 else float("nan")

    # ---- Sec 9.5: blocks + the 2009 momentum-crash window ----
    blocks = make_blocks(eq.index[0], eq.index[-1], 16)
    block_rows = []
    for bi, (bs, be) in enumerate(blocks, 1):
        e = eq[(eq.index >= bs) & (eq.index <= be)]
        b = tr[(tr.index >= bs) & (tr.index <= be)]
        if len(e) < 2 or len(b) < 2:
            continue
        ar = (e.iloc[-1] / e.iloc[0] - 1) * 100.0
        br = (b.iloc[-1] / b.iloc[0] - 1) * 100.0
        block_rows.append({"block": bi, "start": str(bs.date()), "end": str(be.date()),
                          "arm_return_pct": round(ar, 2), "bench_return_pct": round(br, 2),
                          "excess_pct": round(ar - br, 2), "beats_bench": bool(ar > br)})

    ms, me = MOMENTUM_CRASH
    e = eq[(eq.index >= ms) & (eq.index <= me)]
    b = tr[(tr.index >= ms) & (tr.index <= me)]
    crash = None
    if len(e) >= 2 and len(b) >= 2:
        td_c = td.copy()
        td_c["exit_date"] = pd.to_datetime(td_c["exit_date"])
        in_win = td_c[(td_c["exit_date"] >= ms) & (td_c["exit_date"] <= me)]
        sh = in_win[in_win["direction"] == "SHORT"]
        crash = {
            "arm_return_pct": round((e.iloc[-1] / e.iloc[0] - 1) * 100.0, 2),
            "bench_return_pct": round((b.iloc[-1] / b.iloc[0] - 1) * 100.0, 2),
            "excess_pct": round(((e.iloc[-1] / e.iloc[0]) - (b.iloc[-1] / b.iloc[0])) * 100.0, 2),
            "max_drawdown_pct": round(max_drawdown_pct(e), 2),
            "n_short_exits": int(len(sh)),
            "short_net_pnl_gbp": round(float(sh["net_pnl_gbp"].sum()), 2) if len(sh) else 0.0,
            "n_rally_exits": int((in_win["exit_reason"] == "RALLY_EXIT").sum()),
        }

    # ---- Sec 9.6: unrealizable-risk share of short profit ----
    unreal = None
    sh_all = td[td["direction"] == "SHORT"] if len(td) else td
    if len(sh_all):
        winners = sh_all[sh_all["net_pnl_gbp"] > 0]
        gross_profit = float(winners["net_pnl_gbp"].sum())
        flag = (
            (winners["exit_price"] < 1.00)
            | (winners["exit_reason"] == "END_OF_DATA")
            | (winners["ticker"].isin(CLIFF_TICKERS))
        )
        unreal_profit = float(winners.loc[flag, "net_pnl_gbp"].sum())
        unreal = {
            "short_gross_profit_gbp": round(gross_profit, 2),
            "unrealizable_profit_gbp": round(unreal_profit, 2),
            "unrealizable_share_pct": round(100.0 * unreal_profit / gross_profit, 2) if gross_profit > 0 else 0.0,
            "n_flagged": int(flag.sum()), "n_winners": int(len(winners)),
        }

    # ---- Sec 10: paired per-decision instrument, LONG SLEEVE ONLY ----
    paired = None
    if arm_a_trades is not None:
        a_l = arm_a_trades[arm_a_trades["direction"] == "LONG"].copy()
        x_l = td[td["direction"] == "LONG"].copy()
        for f in (a_l, x_l):
            f["key"] = f["ticker"] + "|" + pd.to_datetime(f["entry_date"]).dt.strftime("%Y-%m-%d")
        ka, kx = set(a_l["key"]), set(x_l["key"])
        shared = ka & kx
        a_only, x_only = a_l[~a_l["key"].isin(shared)], x_l[~x_l["key"].isin(shared)]
        paired = {
            "n_shared": len(shared),
            "overlap_pct_of_arm": round(100.0 * len(shared) / len(kx), 2) if kx else 0.0,
            "overlap_pct_of_baseline": round(100.0 * len(shared) / len(ka), 2) if ka else 0.0,
            "jaccard": round(len(shared) / len(ka | kx), 4) if (ka | kx) else 0.0,
            "n_a_only": int(len(a_only)), "n_arm_only": int(len(x_only)),
            "a_only_win_pct": round(100.0 * float((a_only["net_pnl_gbp"] > 0).mean()), 2) if len(a_only) else None,
            "arm_only_win_pct": round(100.0 * float((x_only["net_pnl_gbp"] > 0).mean()), 2) if len(x_only) else None,
            "a_only_mean_pnl_pct": round(100.0 * float(a_only["pnl_pct"].mean()), 4) if len(a_only) else None,
            "arm_only_mean_pnl_pct": round(100.0 * float(x_only["pnl_pct"].mean()), 4) if len(x_only) else None,
            "meets_min_sample_floor": bool(len(a_only) >= 100 and len(x_only) >= 100),
        }

    return {
        "tag": tag, "pool": pool, "years": round(years, 2),
        "n_trades": int(len(td)),
        "n_long": int((td["direction"] == "LONG").sum()) if len(td) else 0,
        "n_short": int((td["direction"] == "SHORT").sum()) if len(td) else 0,
        "final_equity": round(float(eq.iloc[-1]), 2),
        "raw_cagr_incl_borrow": round(raw_cagr, 3),
        "fx_drag_pp_per_yr": round(fx_drag_pp, 3),
        "idle_credit_pp_per_yr": round(idle_credit_pp, 3),
        "borrow_cost_total_gbp": round(float(td["borrow_cost_gbp"].sum()), 2) if len(td) else 0.0,
        "cost_adjusted_cagr": round(cost_adj_cagr, 3),
        "benchmark_cagr_sp500tr": round(bench_cagr, 3),
        "excess_vs_sp500tr": round(excess, 3),
        "max_drawdown_pct": round(max_drawdown_pct(eq), 2),
        "corr_monthly_sp500tr": round(corr_m, 4),
        "corr_daily_sp500tr": round(corr_d, 4),
        "mean_long_pct": round(float(pn["long_pct"].mean()), 2),
        "mean_short_pct": round(float(pn["short_pct"].mean()), 2),
        "mean_gross_pct": round(float(pn["gross_pct"].mean()), 2),
        "mean_net_pct": round(float(pn["net_pct"].mean()), 2),
        "p10_gross_pct": round(float(pn["gross_pct"].quantile(0.10)), 2),
        "p90_gross_pct": round(float(pn["gross_pct"].quantile(0.90)), 2),
        "mean_n_long": round(float(pn["n_long"].mean()), 2),
        "mean_n_short": round(float(pn["n_short"].mean()), 2),
        "pct_days_short_sleeve_empty": round(100.0 * float((pn["n_short"] == 0).mean()), 2),
        "blocks": block_rows,
        "n_blocks_beating_bench": sum(1 for r in block_rows if r["beats_bench"]),
        "momentum_crash_2009": crash,
        "unrealizable_risk": unreal,
        "paired_long_sleeve": paired,
    }


def main() -> None:
    pool = sys.argv[1]
    ref = load_reference()
    arm_a = pd.read_csv(HERE / f"trades_{pool}_A.csv")

    tags = ["A", "B_s1x", "B_s2x", "C_s1x", "C_s2x"]
    out = {}
    for tag in tags:
        out[tag] = analyse(tag, pool, ref, arm_a_trades=None if tag == "A" else arm_a)
        r = out[tag]
        print(f"\n--- {pool} / {tag} ---")
        print(f"  trades {r['n_trades']:,} ({r['n_long']:,}L / {r['n_short']:,}S)   "
              f"final £{r['final_equity']:,.0f}")
        print(f"  cost-adj CAGR {r['cost_adjusted_cagr']:.3f}%  "
              f"(raw {r['raw_cagr_incl_borrow']:.3f}, fx -{r['fx_drag_pp_per_yr']:.3f}, "
              f"idle +{r['idle_credit_pp_per_yr']:.3f}, borrow £{r['borrow_cost_total_gbp']:,.0f})")
        print(f"  benchmark {r['benchmark_cagr_sp500tr']:.3f}%   EXCESS {r['excess_vs_sp500tr']:+.3f}pp")
        print(f"  maxDD {r['max_drawdown_pct']:.2f}%   corr(m) {r['corr_monthly_sp500tr']:.3f}   "
              f"corr(d) {r['corr_daily_sp500tr']:.3f}")
        print(f"  exposure: long {r['mean_long_pct']:.1f}% short {r['mean_short_pct']:.1f}% "
              f"gross {r['mean_gross_pct']:.1f}% net {r['mean_net_pct']:.1f}%   "
              f"n {r['mean_n_long']:.1f}L/{r['mean_n_short']:.1f}S")
        print(f"  blocks beating benchmark: {r['n_blocks_beating_bench']}/16")
        if r["momentum_crash_2009"]:
            c = r["momentum_crash_2009"]
            print(f"  2009 crash window: arm {c['arm_return_pct']:+.2f}% vs bench "
                  f"{c['bench_return_pct']:+.2f}% (excess {c['excess_pct']:+.2f}pp), "
                  f"maxDD {c['max_drawdown_pct']:.2f}%, short P&L £{c['short_net_pnl_gbp']:,.0f}, "
                  f"{c['n_rally_exits']} RALLY_EXIT")
        if r["unrealizable_risk"]:
            u = r["unrealizable_risk"]
            print(f"  unrealizable-risk share of short profit: {u['unrealizable_share_pct']:.1f}% "
                  f"({u['n_flagged']}/{u['n_winners']} winners)")
        if r["paired_long_sleeve"]:
            p = r["paired_long_sleeve"]
            print(f"  paired(long): overlap {p['overlap_pct_of_arm']:.1f}% of arm, "
                  f"jaccard {p['jaccard']:.3f}; A-only {p['a_only_win_pct']}%/"
                  f"{p['a_only_mean_pnl_pct']}%  arm-only {p['arm_only_win_pct']}%/"
                  f"{p['arm_only_mean_pnl_pct']}%  floor_ok={p['meets_min_sample_floor']}")

    (HERE / f"instruments_{pool}.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote instruments_{pool}.json")


if __name__ == "__main__":
    main()
