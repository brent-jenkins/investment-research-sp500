"""Run one paired perturbation draw: identical perturbed pool/date/tie-break
state fed to BOTH arms. Usage: python step2_run_paired_draw.py <draw_id>

Arm A is run via relative_ranking_engine.py's own _sort_key="momentum" mode
(proven byte-identical to raw production simulate_portfolio_compounding_mtm()
-- see the relative-ranking study's own degenerate-case check, and
step3_degenerate_check.py in this directory for a fresh spot-check under a
real perturbation) rather than calling simulate_portfolio_compounding_mtm()
directly -- this gives Arm A a real panel (deployment, n_open_positions) for
free, from the SAME engine code path as Arm B, so both arms' panels are
directly comparable and neither needed separate instrumentation.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-relranking-robustness")
sys.path.insert(0, r"c:\GitHub\investment-app-relranking-robustness\research\relative_ranking_prereg_2026-08-09")

import json
import pickle
import random
import time

import pandas as pd
import yaml

from analysis.backtest import slice_ticker
from data.ingestion import get_price_df
from relative_ranking_engine import simulate_relative_ranking

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/relranking_robustness_2026-08-09"
BASE_START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-07")  # matches this session's fresh price_data max date
MAX_DEPLOYED_PCT = 0.70
PER_TRADE_PCT = 0.05


def build_perturbed_inputs(draw):
    with open(f"{OUT_DIR}/_cache_full_history.pkl", "rb") as fh:
        data = pickle.load(fh)
    full_caches = data["caches"]
    tickers = list(data["candidate_tickers"])

    ptype, param = draw["ptype"], draw["param"]
    start = BASE_START
    if ptype == "drop_ticker":
        tickers = [t for t in tickers if t != param]
    elif ptype == "shift_start":
        start = BASE_START + pd.Timedelta(days=int(param))
    elif ptype == "shuffle_tiebreak":
        rng = random.Random(int(param))
        rng.shuffle(tickers)
    else:
        raise ValueError(f"unknown ptype {ptype!r}")

    caches = []
    for t in tickers:
        c = full_caches.get(t)
        if c is not None:
            c = slice_ticker(c, start, END)
        caches.append(c)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= start) & (gspc_close_full.index <= END)]
    return caches, gspc_close, start


def main():
    draw_id = sys.argv[1]
    with open(f"{OUT_DIR}/draws.json") as fh:
        draws = {d["draw_id"]: d for d in json.load(fh)}
    draw = draws[draw_id]

    t0 = time.time()
    caches, gspc_close, start = build_perturbed_inputs(draw)
    n_usable = sum(1 for c in caches if c is not None)
    print(f"[{draw_id}] ptype={draw['ptype']} param={draw['param']} start={start.date()} "
          f"n_tickers={len(caches)} n_usable={n_usable} ({time.time()-t0:.0f}s)", flush=True)

    result_a = simulate_relative_ranking(
        caches, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
        apply_rank_dropped_exit=False, _sort_key="momentum",
    )
    trades_a = pd.DataFrame(result_a["trades"])
    panel_a = pd.DataFrame(result_a["panel"])
    print(f"[{draw_id}] A: {len(trades_a)} trades, pnl={trades_a['pnl_gbp'].sum():.2f}, "
          f"mean_deployed={panel_a['current_deployed_pct'].mean():.2f} ({time.time()-t0:.0f}s)", flush=True)

    result_b = simulate_relative_ranking(
        caches, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
        apply_rank_dropped_exit=False, _sort_key="rank",
    )
    trades_b = pd.DataFrame(result_b["trades"])
    panel_b = pd.DataFrame(result_b["panel"])
    print(f"[{draw_id}] B: {len(trades_b)} trades, pnl={trades_b['pnl_gbp'].sum():.2f}, "
          f"mean_deployed={panel_b['current_deployed_pct'].mean():.2f} ({time.time()-t0:.0f}s total)", flush=True)

    trades_a.to_csv(f"{OUT_DIR}/trades_A_{draw_id}.csv", index=False)
    panel_a.to_csv(f"{OUT_DIR}/panel_A_{draw_id}.csv", index=False)
    trades_b.to_csv(f"{OUT_DIR}/trades_B_{draw_id}.csv", index=False)
    panel_b.to_csv(f"{OUT_DIR}/panel_B_{draw_id}.csv", index=False)
    with open(f"{OUT_DIR}/meta_{draw_id}.json", "w") as fh:
        json.dump({"draw_id": draw_id, "ptype": draw["ptype"], "param": draw["param"],
                    "start": str(start.date()), "end": str(END.date()), "n_usable": n_usable}, fh)
    print(f"[{draw_id}] wrote trades/panel A+B, meta ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
