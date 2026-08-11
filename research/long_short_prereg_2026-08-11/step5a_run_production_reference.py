"""Run raw, unmodified production simulate_portfolio_compounding_mtm() for one
admission_ranking_mode and PERSIST its trades.

Split out from the comparison deliberately. The first version of step5 ran the
simulations and compared in one process; a trivial bug in the comparison
(production returns a trades LIST, not a dict) destroyed ~50 minutes of
completed simulation. Expensive computation and cheap comparison now live in
separate steps, so a comparison bug costs seconds to fix, not an hour to
recompute -- the same "preserve full trade-level output" discipline this
project already requires for headline figures, applied to verification.

Usage: python step5a_run_production_reference.py <relative_rank|absolute_momentum>
"""
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from analysis.backtest import simulate_portfolio_compounding_mtm     # noqa: E402
from ls_engine import simulate_long_short                            # noqa: E402

import pickle                                                        # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE_CONFIG = yaml.safe_load(f)


def main() -> None:
    mode = sys.argv[1]
    sort_key = "rank" if mode == "relative_rank" else "momentum"

    with open(HERE / "_cache_pool2_clean.pkl", "rb") as fh:
        cache = pickle.load(fh)
    caches, gspc = cache["caches"], cache["gspc_close"]

    cfg = yaml.safe_load(yaml.safe_dump(BASE_CONFIG))
    cfg["trading212_invest"]["admission_ranking_mode"] = mode

    t0 = time.time()
    prod_trades = simulate_portfolio_compounding_mtm(
        caches, cfg, gspc, per_trade_pct=0.05, max_deployed_pct=0.70)
    df = pd.DataFrame(prod_trades)
    df.to_csv(HERE / f"_ref_production_{mode}.csv", index=False)
    print(f"production/{mode}: {len(df):,} trades, £{df['pnl_gbp'].sum():,.2f} "
          f"({time.time()-t0:.0f}s) -> _ref_production_{mode}.csv", flush=True)

    # The ls_engine relative_rank/shorts-off run is already produced by
    # step6 as Arm A (trades_pool2_clean_A.csv); only the momentum-mode
    # counterpart needs generating here.
    if mode == "absolute_momentum":
        t0 = time.time()
        res = simulate_long_short(
            caches, cfg, gspc, per_trade_pct=0.05, long_cap=0.70,
            enable_shorts=False, _sort_key=sort_key)
        df2 = pd.DataFrame(res["trades"])
        df2.to_csv(HERE / f"_ref_lsengine_{mode}.csv", index=False)
        print(f"ls_engine/{mode}: {len(df2):,} trades, £{df2['pnl_gbp'].sum():,.2f} "
              f"({time.time()-t0:.0f}s) -> _ref_lsengine_{mode}.csv", flush=True)


if __name__ == "__main__":
    main()
