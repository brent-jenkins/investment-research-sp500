"""Run ONE variant from the pickled cache and save its trades CSV. Usage:
  python step3b_run_variant.py <label>
where <label> is one of baseline/vix_only/ma_only/dd_only/combined."""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/vix_ma_giveback_2026-08-07"
MARKET_REGIME_MA_WINDOW = 200
GIVEBACK_PCT = 8.0

VARIANT_KWARGS = {
    "baseline": lambda vix: dict(),
    "vix_only": lambda vix: dict(vix_close=vix, sizing_mode="vix"),
    "ma_only": lambda vix: dict(market_regime_mode="gate", market_regime_ma_window=MARKET_REGIME_MA_WINDOW),
    "dd_only": lambda vix: dict(giveback_mode="peak_dd", giveback_pct=GIVEBACK_PCT),
    "vix_ma": lambda vix: dict(
        vix_close=vix, sizing_mode="vix",
        market_regime_mode="gate", market_regime_ma_window=MARKET_REGIME_MA_WINDOW,
    ),
    "vix_dd": lambda vix: dict(
        vix_close=vix, sizing_mode="vix",
        giveback_mode="peak_dd", giveback_pct=GIVEBACK_PCT,
    ),
    "ma_dd": lambda vix: dict(
        market_regime_mode="gate", market_regime_ma_window=MARKET_REGIME_MA_WINDOW,
        giveback_mode="peak_dd", giveback_pct=GIVEBACK_PCT,
    ),
    "combined": lambda vix: dict(
        vix_close=vix, sizing_mode="vix",
        market_regime_mode="gate", market_regime_ma_window=MARKET_REGIME_MA_WINDOW,
        giveback_mode="peak_dd", giveback_pct=GIVEBACK_PCT,
    ),
}


def main():
    label = sys.argv[1]
    t0 = time.time()
    t212_cfg = CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.8))

    with open(f"{OUT_DIR}/_cache.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    vix_close = data["vix_close"]
    print(f"Loaded cache ({time.time()-t0:.0f}s). Running variant '{label}'...", flush=True)

    kwargs = VARIANT_KWARGS[label](vix_close)
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
