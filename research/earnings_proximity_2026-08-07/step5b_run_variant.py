"""Run ONE variant from the pickled cache and save its trades CSV.
Usage: python step5b_run_variant.py <label>
where <label> is baseline or earnings_shrink."""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/earnings_proximity_2026-08-07"
EARNINGS_DAMPENER_DAYS = 10
EARNINGS_DAMPENER_FACTOR = 0.5

VARIANT_KWARGS = {
    "baseline": lambda eb: dict(),
    "earnings_shrink": lambda eb: dict(
        earnings_dampener_mode="shrink", earnings_dampener_days=EARNINGS_DAMPENER_DAYS,
        earnings_dampener_factor=EARNINGS_DAMPENER_FACTOR, earnings_dates_by_ticker=eb,
    ),
}


def main():
    label = sys.argv[1]
    # Optional second arg: override max_deployed_pct (default: config's 0.8).
    cap_override = float(sys.argv[2]) if len(sys.argv) > 2 else None
    out_label = sys.argv[3] if len(sys.argv) > 3 else label

    t0 = time.time()
    t212_cfg = CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))
    max_deployed_pct = cap_override if cap_override is not None else float(t212_cfg.get("max_total_deployed", 0.8))

    with open(f"{OUT_DIR}/_cache.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    earnings_by_ticker = data["earnings_by_ticker"]
    print(f"Loaded cache ({time.time()-t0:.0f}s). Running variant '{label}' (max_deployed_pct={max_deployed_pct}) -> {out_label}...", flush=True)

    kwargs = VARIANT_KWARGS[label](earnings_by_ticker)
    label = out_label
    trades = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct, **kwargs,
    )
    df_v = pd.DataFrame(trades)
    ec = df_v["exit_reason"].value_counts()
    print(f"  total_trades={len(df_v)}  distinct_tickers={df_v['ticker'].nunique()}  "
          f"total_pnl_gbp={df_v['pnl_gbp'].sum():.2f}\n  by exit_reason:\n{ec}", flush=True)
    df_v.to_csv(f"{OUT_DIR}/trades_{label}.csv", index=False)
    print(f"Wrote trades_{label}.csv ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
