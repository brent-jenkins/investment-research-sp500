"""PAIRED PERTURBATION TEST for the winning cell (s3.0 = stop 3.0x, unlimited
holding, no trailing) against the live baseline (h30 = stop 2.0x, 30-day cap).

PAIRED BY CONSTRUCTION: for every draw, the identical perturbed ticker list,
start date and tie-break order is fed to BOTH arms in the same process, from the
same in-memory perturbed cache. The quantity of interest is the DELTA's own
distribution, never two independently-perturbed runs compared after the fact.

FOUR PERTURBATION DIMENSIONS (two added at the user's direction):
  drop_ticker      -- TARGETED at the top-10 P&L contributors, not random. With
                      only 565 trades and 73.6% of P&L in 10 of 238 tickers,
                      random drops would mostly be no-ops and would not test
                      dominance at all. This is the explicit dominance check.
  shift_start      -- walk start date moved forward
  shuffle_tiebreak -- candidate insertion order shuffled (admission tie-break)
  end_date         -- NEW DIMENSION. Applied to every draw, at zero extra
                      compute, by truncating each arm's own equity curve.

WHY EQUITY-CURVE TRUNCATION IS EXACT FOR end_date, not an approximation: the
simulation is a strictly sequential day-by-day walk, so truncating the input data
at date D produces trades identical to the full run's trades up to D, plus
END_OF_DATA exits at D for whatever is still open. Those exits realise at the
same close the panel already marked the position at, so the equity curve up to D
is byte-identical either way. Re-running would cost ~15 min/cell to reproduce a
series already in hand. The one thing truncation does NOT give is a per-window
trades file, so per-window ticker attribution is done on the full-window trades.

Usage: python step7_perturbation.py <draw_id> <kind> <arg>
       kind in {drop_ticker, shift_start, shuffle_tiebreak, none}
"""
import json
import pickle
import random
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from sweep_engine import simulate_exit_sweep      # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)

POOL = "pool2_clean"
FX = 0.0030
CUTOFFS = ["2022-08-05", "2023-08-04", "2024-08-02", "2025-08-01", "2026-08-07"]


def cagr(s):
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100.0


def perturb(caches, gspc, kind, arg):
    if kind == "drop_ticker":
        return [c for c in caches if c is None or c["ticker"] != arg], gspc
    if kind == "shift_start":
        n = int(arg)
        start = gspc.index[0] + pd.Timedelta(days=n)
        out = []
        for c in caches:
            if c is None:
                out.append(None); continue
            fr = c["frame"]
            fr2 = fr[fr.index >= start]
            out.append({"ticker": c["ticker"], "frame": fr2} if len(fr2) > 300 else None)
        return out, gspc[gspc.index >= start]
    if kind == "shuffle_tiebreak":
        out = list(caches)
        random.Random(int(arg)).shuffle(out)
        return out, gspc
    return list(caches), gspc


def run_arm(caches, gspc, stop, hold_unlimited):
    cfg = yaml.safe_load(yaml.safe_dump(BASE))
    cfg["backtest"]["atr_stop_multiplier"] = stop
    return simulate_exit_sweep(
        caches, cfg, gspc, per_trade_pct=0.05, max_deployed_pct=0.70,
        disable_stop_loss=False,
        max_holding_days_override=None if hold_unlimited else 30,
        unlimited_holding=hold_unlimited, trail_activation=None, trail_multiplier=stop)


def metrics_at(pn_df, td_df, ref, cutoff=None):
    pn = pn_df.set_index("date").sort_index()
    eq = pn["current_balance"].astype(float)
    if cutoff is not None:
        eq = eq[eq.index <= pd.Timestamp(cutoff)]
    if len(eq) < 50:
        return None
    tr = ref["^SP500TR"].reindex(eq.index, method="ffill").dropna()
    irx = ref["^IRX"].reindex(eq.index, method="ffill")
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    sub = td_df[td_df["entry_date"] <= eq.index[-1]]
    fx_pp = (float(sub["position_gbp"].sum()) * FX / yrs) / float(eq.mean()) * 100.0
    idle_pp = float((1 - pn.loc[eq.index, "current_deployed_pct"] / 100.0)
                    .clip(lower=0).mean()) * float(irx.mean())
    return round(cagr(eq) - fx_pp + idle_pp - cagr(tr), 4)


def main() -> None:
    draw_id, kind, arg = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "")
    with open(HERE / "_reference_series.pkl", "rb") as fh:
        ref = pickle.load(fh)
    with open(HERE / f"_cache_{POOL}.pkl", "rb") as fh:
        c = pickle.load(fh)

    caches, gspc = perturb(c["caches"], c["gspc_close"], kind, arg)
    t0 = time.time()
    out = {"draw_id": draw_id, "kind": kind, "arg": arg,
           "n_tickers": sum(1 for x in caches if x is not None)}

    res = {}
    for name, stop, unl in (("base", 2.0, False), ("s30", 3.0, True)):
        r = run_arm(caches, gspc, stop, unl)
        td = pd.DataFrame(r["trades"]); pn = pd.DataFrame(r["panel"])
        td["entry_date"] = pd.to_datetime(td["entry_date"])
        pn["date"] = pd.to_datetime(pn["date"])
        res[name] = (td, pn)
        out[f"{name}_n_trades"] = int(len(td))
        out[f"{name}_pnl"] = round(float(td["pnl_gbp"].sum()), 2)

    # end_date as a formal dimension -- every draw evaluated at every cutoff
    by_cut = {}
    for cut in CUTOFFS:
        eb = metrics_at(res["base"][1], res["base"][0], ref, cut)
        es = metrics_at(res["s30"][1], res["s30"][0], ref, cut)
        if eb is None or es is None:
            continue
        by_cut[cut] = {"base_excess": eb, "s30_excess": es, "delta": round(es - eb, 4)}
    out["by_end_date"] = by_cut
    out["delta_full_window"] = by_cut.get("2026-08-07", {}).get("delta")
    out["runtime_s"] = round(time.time() - t0, 1)

    (HERE / f"pert_{draw_id}.json").write_text(json.dumps(out, indent=2))
    d = out["delta_full_window"]
    print(f"{draw_id} [{kind} {arg}]: base {out['base_n_trades']:,}tr  "
          f"s30 {out['s30_n_trades']:,}tr  delta(full)={d:+.3f}pp  "
          f"deltas by cutoff: {[by_cut[c]['delta'] for c in CUTOFFS if c in by_cut]}  "
          f"[{out['runtime_s']}s]", flush=True)


if __name__ == "__main__":
    main()
