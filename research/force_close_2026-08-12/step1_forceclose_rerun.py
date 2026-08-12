"""Genuine force-close re-run at a given cutoff: the cache is TRUNCATED at the
cutoff date and both arms re-simulated, so every position still open at that
date is closed by the engine at that date's close (END_OF_DATA) as a real
simulated exit, not as a post-hoc mark.

Purpose: validate empirically that truncating the already-computed equity curve
gives the identical answer, so the remaining cutoffs can be evaluated without
20 more 15-minute simulations. If the two disagree, the truncation shortcut is
wrong and every cutoff must be re-run.

Usage: python step1_forceclose_rerun.py <pool> <cutoff YYYY-MM-DD>
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
SWEEP = ROOT / "research" / "exit_mechanics_prereg_2026-08-11"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SWEEP))

from sweep_engine import simulate_exit_sweep      # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)


def main() -> None:
    pool, cutoff = sys.argv[1], pd.Timestamp(sys.argv[2])
    with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
        c = pickle.load(fh)

    caches = []
    for x in c["caches"]:
        if x is None:
            caches.append(None); continue
        fr = x["frame"]
        fr2 = fr[fr.index <= cutoff]
        caches.append({"ticker": x["ticker"], "frame": fr2} if len(fr2) > 300 else None)
    gspc = c["gspc_close"][c["gspc_close"].index <= cutoff]

    out = {"pool": pool, "cutoff": str(cutoff.date())}
    for name, stop, unl in (("h30", 2.0, False), ("s3.0", 3.0, True)):
        cfg = yaml.safe_load(yaml.safe_dump(BASE))
        cfg["backtest"]["atr_stop_multiplier"] = stop
        t0 = time.time()
        r = simulate_exit_sweep(caches, cfg, gspc, per_trade_pct=0.05, max_deployed_pct=0.70,
                                disable_stop_loss=False,
                                max_holding_days_override=None if unl else 30,
                                unlimited_holding=unl, trail_activation=None,
                                trail_multiplier=stop)
        td = pd.DataFrame(r["trades"]); pn = pd.DataFrame(r["panel"])
        tag = name.replace(".", "")
        td.to_csv(HERE / f"fc_trades_{pool}_{tag}_{cutoff.date()}.csv", index=False)
        pn.to_csv(HERE / f"fc_panel_{pool}_{tag}_{cutoff.date()}.csv", index=False)
        eod = td[td["exit_reason"] == "END_OF_DATA"]
        out[name] = {
            "n_trades": int(len(td)),
            "total_pnl_gbp": round(float(td["pnl_gbp"].sum()), 2),
            "final_equity": round(float(pn["current_balance"].iloc[-1]), 2),
            "n_force_closed": int(len(eod)),
            "force_closed_pnl_gbp": round(float(eod["pnl_gbp"].sum()), 2),
            "strategy_closed_pnl_gbp": round(float(td["pnl_gbp"].sum() - eod["pnl_gbp"].sum()), 2),
            "runtime_s": round(time.time() - t0, 1),
        }
        print(f"{pool}/{name}@{cutoff.date()}: {out[name]['n_trades']:,} trades, "
              f"total £{out[name]['total_pnl_gbp']:,.0f} "
              f"(force-closed {out[name]['n_force_closed']} worth "
              f"£{out[name]['force_closed_pnl_gbp']:,.0f}), "
              f"equity £{out[name]['final_equity']:,.0f} [{out[name]['runtime_s']}s]", flush=True)

    (HERE / f"fc_rerun_{pool}_{cutoff.date()}.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
