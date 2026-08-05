"""Isolates how much of curated-40's fresh-vs-original gap is explained by
the 2026-08-03 momentum floor (min_momentum_pct=0.02, added AFTER the
original curated-40 study) vs ordinary data-recency drift. Re-runs
curated-40 with an in-memory config override forcing min_momentum_pct back
to 0.0 (the original study's own implicit gate: momentum > 0.0 only, no
absolute floor) -- reentry_cooldown_days is left at its real config value
of 0, since that was already established to be a no-op relative to "no
cooldown mechanism at all" (0 and 1 are provably identical, and the
original engine had no cooldown concept at all -- see the reset_wrapper's
own faithfulness check and CLAUDE_HISTORY.md's "re-entry cooldown sweep"
0d==1d finding).
"""
import copy
import json
from pathlib import Path

import pandas as pd

import env_setup

env_setup.patch_all()
config = env_setup.load_config()

from analysis import universe
from analysis.backtest import precompute_ticker, slice_ticker, compute_metrics_fixed, compute_peak_relative_drawdown_pct, equity_curve_gbp
from data.ingestion import get_price_df
from reset_wrapper import build_reset_wrapper, verify_faithful

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
SPLIT = pd.Timestamp("2020-01-01")
RESET_CAPITAL = 50000.0
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

config_no_floor = copy.deepcopy(config)
config_no_floor["trading212_invest"]["min_momentum_pct"] = 0.0

tickers = sorted(universe.STOCK_MEMBERSHIP.keys())
caches = []
for t in tickers:
    c = precompute_ticker(t, config)
    if c is not None:
        c = slice_ticker(c, START, END)
    caches.append(c)

gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
gspc_df.index = pd.to_datetime(gspc_df.index)
gspc_close_full = gspc_df["close"].astype(float)
gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

per_trade_pct = float(config["trading212_invest"]["per_trade_pct"])
max_deployed_pct = float(config["trading212_invest"]["max_total_deployed"])

wrapped = build_reset_wrapper()
verify_faithful(wrapped, caches, config_no_floor, gspc_close, per_trade_pct, max_deployed_pct)
print("wrapper verified faithful (no-floor config)", flush=True)

trades = wrapped(
    caches, config_no_floor, gspc_close, per_trade_pct, max_deployed_pct,
    reset_boundary_ts=SPLIT, reset_capital=RESET_CAPITAL,
)
trades_df = pd.DataFrame(trades)
trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"])
test_trades = trades_df[trades_df["entry_date"] >= SPLIT].to_dict("records")

m = compute_metrics_fixed(test_trades, RESET_CAPITAL)
peak_dd = compute_peak_relative_drawdown_pct(test_trades, RESET_CAPITAL)
ec = equity_curve_gbp(pd.DataFrame(test_trades), starting_capital_gbp=RESET_CAPITAL)
ending = float(ec["equity_gbp"].iloc[-1]) if not ec.empty else RESET_CAPITAL

result = {
    "n_trades": len(test_trades),
    "win_rate_pct": round(m.get("win_rate", 0.0) * 100, 2),
    "profit_factor": m.get("profit_factor"),
    "cagr_pct": m.get("cagr_pct"),
    "max_drawdown_pct_fixed_denom": m.get("max_drawdown_pct"),
    "max_drawdown_pct_peak_relative": peak_dd,
    "ending_balance_gbp": round(ending, 2),
    "config_min_momentum_pct": 0.0,
    "note": "min_momentum_pct forced to 0.0 (the original study's own implicit gate) to isolate the momentum-floor's contribution to the fresh-vs-original curated-40 gap",
}
print("CURATED-40 TEST metrics with momentum floor forced OFF:", result, flush=True)
with open(OUTPUT_DIR / "curated40_no_momentum_floor_result.json", "w") as f:
    json.dump(result, f, indent=2, default=str)
