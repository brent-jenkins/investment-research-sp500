"""Run Arms A, B, C for one pool, at the modelled and 2x stress borrow rates.

Usage: python step6_run_arms.py <pool1|pool2_clean>

Arm A is run through ls_engine with enable_shorts=False rather than through
simulate_portfolio_compounding_mtm() directly -- step5 proves the two are
byte-identical, and this way Arm A gets a real exposure panel from the same
code path as B and C, making per-draw comparison direct. Same approach the
relative-ranking robustness test used for the same reason.

Arm A carries no borrow cost (no shorts), so it is run once, not twice.

Full trade-level output is written per arm, per this project's standing
"preserve full trade-level output, not just the formula" requirement.
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

from ls_engine import simulate_long_short           # noqa: E402
import ls_primitives as lsp                         # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

PER_TRADE_PCT = 0.05

ARMS = {
    # arm: (long_cap, short_cap, net_band, enable_shorts, admission_order)
    "A": (0.70, 0.00, None, False, "long_first"),
    "B": (0.70, 0.30, None, True, "long_first"),
    "C": (0.50, 0.50, 0.10, True, "short_first"),
}


def run(pool: str, only_tags: set | None = None) -> None:
    with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
        cache = pickle.load(fh)
    caches, gspc = cache["caches"], cache["gspc_close"]
    n_usable = sum(1 for c in caches if c is not None)
    print(f"{pool}: {n_usable} usable tickers, gspc {gspc.index[0].date()} -> {gspc.index[-1].date()}",
          flush=True)

    summary = {}
    for arm, (lc, sc, nb, shorts, order) in ARMS.items():
        stresses = [1.0] if not shorts else [1.0, 2.0]
        for stress in stresses:
            tag = f"{arm}" if not shorts else f"{arm}_s{int(stress)}x"
            if only_tags is not None and tag not in only_tags:
                continue
            t0 = time.time()
            res = simulate_long_short(
                caches, CONFIG, gspc,
                per_trade_pct=PER_TRADE_PCT, long_cap=lc, short_cap=sc, net_band=nb,
                enable_shorts=shorts, borrow_stress=stress,
                rally_cfg=lsp.PRIMARY_CONFIG, admission_order=order, _sort_key="rank",
            )
            td = pd.DataFrame(res["trades"])
            pn = pd.DataFrame(res["panel"])
            td.to_csv(HERE / f"trades_{pool}_{tag}.csv", index=False)
            pn.to_csv(HERE / f"panel_{pool}_{tag}.csv", index=False)

            longs = td[td["direction"] == "LONG"] if len(td) else td
            shorts_df = td[td["direction"] == "SHORT"] if len(td) else td
            summary[tag] = {
                "arm": arm, "stress": stress, "long_cap": lc, "short_cap": sc, "net_band": nb,
                "admission_order": order,
                "n_trades": int(len(td)),
                "n_long": int(len(longs)), "n_short": int(len(shorts_df)),
                "gross_pnl_gbp": round(float(td["pnl_gbp"].sum()), 2) if len(td) else 0.0,
                "borrow_cost_gbp": round(float(td["borrow_cost_gbp"].sum()), 2) if len(td) else 0.0,
                "net_pnl_gbp": round(float(td["net_pnl_gbp"].sum()), 2) if len(td) else 0.0,
                "long_pnl_gbp": round(float(longs["net_pnl_gbp"].sum()), 2) if len(longs) else 0.0,
                "short_pnl_gbp": round(float(shorts_df["net_pnl_gbp"].sum()), 2) if len(shorts_df) else 0.0,
                "final_equity": round(float(pn["equity"].iloc[-1]), 2),
                "mean_long_pct": round(float(pn["long_pct"].mean()), 3),
                "mean_short_pct": round(float(pn["short_pct"].mean()), 3),
                "mean_gross_pct": round(float(pn["gross_pct"].mean()), 3),
                "mean_net_pct": round(float(pn["net_pct"].mean()), 3),
                "mean_n_long": round(float(pn["n_long"].mean()), 2),
                "mean_n_short": round(float(pn["n_short"].mean()), 2),
                "runtime_s": round(time.time() - t0, 1),
            }
            s = summary[tag]
            print(f"  {tag}: {s['n_trades']:,} trades ({s['n_long']:,}L/{s['n_short']:,}S)  "
                  f"net £{s['net_pnl_gbp']:,.0f}  (L £{s['long_pnl_gbp']:,.0f} / "
                  f"S £{s['short_pnl_gbp']:,.0f}, borrow £{s['borrow_cost_gbp']:,.0f})  "
                  f"gross {s['mean_gross_pct']:.1f}% net {s['mean_net_pct']:.1f}%  "
                  f"[{s['runtime_s']}s]", flush=True)

    suffix = "" if only_tags is None else "_" + "_".join(sorted(only_tags))
    (HERE / f"arms_summary_{pool}{suffix}.json").write_text(json.dumps(summary, indent=2))
    print(f"Wrote arms_summary_{pool}{suffix}.json", flush=True)


if __name__ == "__main__":
    tags = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
    run(sys.argv[1], tags)
