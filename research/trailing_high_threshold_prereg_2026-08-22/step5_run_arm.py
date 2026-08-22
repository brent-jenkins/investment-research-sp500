"""Run one sweep cell. Usage:

  python step3_run_cell.py <pool> <cell_id> <stop> <hold> <trail>

    stop  : 2.0 | 3.0 | none
    hold  : 30 | 60 | 90 | unlimited
    trail : none | 0.05 | 0.10

atr_stop_multiplier is applied via a COPIED config dict (never editing
config.yaml); "none" uses disable_stop_loss; "unlimited" passes
max_holding_days=None into the production evaluate_exit_reason().

Trail distance is FIXED at the cell's own stop multiplier (Sec 4). When the cell
has no ATR stop, the pre-committed fallback k=2.0 applies and the cell is what
Sec 4 named it: no initial stop, with a 2.0xATR trail engaging only after the
position has gained A%.
"""
import json
import pickle
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from trail_engine import simulate_exit_sweep      # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)

TRAIL_FALLBACK_K = 2.0   # Sec 4, pre-committed for the no-initial-stop case


def main() -> None:
    pool, cell_id, stop_s, hold_s, trail_s = sys.argv[1:6]
    cfg = yaml.safe_load(yaml.safe_dump(BASE))

    disable_stop = (stop_s == "none")
    if not disable_stop:
        cfg["backtest"]["atr_stop_multiplier"] = float(stop_s)
    unlimited = (hold_s == "unlimited")
    hold = None if unlimited else int(hold_s)
    trail = None if trail_s == "none" else float(trail_s)
    k = TRAIL_FALLBACK_K if disable_stop else float(stop_s)

    with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
        c = pickle.load(fh)

    t0 = time.time()
    res = simulate_exit_sweep(
        c["caches"], cfg, c["gspc_close"], per_trade_pct=0.05, max_deployed_pct=0.70,
        disable_stop_loss=disable_stop, max_holding_days_override=hold,
        unlimited_holding=unlimited, trail_activation=trail, trail_multiplier=k,
        trail_atr_source="current")   # Sec 4.3 RULING

    td = pd.DataFrame(res["trades"])
    pn = pd.DataFrame(res["panel"])
    td.to_csv(HERE / f"trades_{pool}_{cell_id}.csv", index=False)
    pn.to_csv(HERE / f"panel_{pool}_{cell_id}.csv", index=False)

    mix = td["exit_reason"].value_counts().to_dict()
    summary = {
        "pool": pool, "cell_id": cell_id, "stop": stop_s, "hold": hold_s, "trail": trail_s,
        "trail_k": k if trail is not None else None,
        "n_trades": int(len(td)),
        "total_pnl_gbp": round(float(td["pnl_gbp"].sum()), 2),
        "win_pct": round(100 * float((td["pnl_gbp"] > 0).mean()), 2),
        "mean_hold_days": round(float(td["holding_days"].mean()), 2),
        "total_notional_gbp": round(float(td["position_gbp"].sum()), 2),
        "exit_mix": {k2: int(v) for k2, v in mix.items()},
        "exit_pnl_by_reason": {k2: round(float(v), 2) for k2, v in
                               td.groupby("exit_reason")["pnl_gbp"].sum().items()},
        "n_trail_activated": int(td["trail_activated"].sum()),
        "mean_deployed_pct": round(float(pn["current_deployed_pct"].mean()), 3),
        "mean_n_open": round(float(pn["n_open_positions"].mean()), 2),
        "final_equity": round(float(pn["current_balance"].iloc[-1]), 2),
        "runtime_s": round(time.time() - t0, 1),
    }
    (HERE / f"summary_{pool}_{cell_id}.json").write_text(json.dumps(summary, indent=2))
    print(f"{pool}/{cell_id}: {summary['n_trades']:,} trades  £{summary['total_pnl_gbp']:,.0f}  "
          f"win {summary['win_pct']}%  hold {summary['mean_hold_days']}d  "
          f"dep {summary['mean_deployed_pct']}%  mix={summary['exit_mix']}  "
          f"[{summary['runtime_s']}s]", flush=True)


if __name__ == "__main__":
    main()
