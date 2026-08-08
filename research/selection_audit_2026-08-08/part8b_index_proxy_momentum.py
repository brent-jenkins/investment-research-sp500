"""Part 8b: run the momentum signal ITSELF on tradeable index proxies as
SINGLE instruments (no competition for capacity -- there is only ever one
candidate). Uses the real, current, unmodified production engine
(analysis.backtest.simulate_portfolio_compounding_mtm), imported directly --
not re-derived -- fed a one-ticker cache built the same way
step1_build_caches.py builds every other cache in this project. is_etf=True
is implied for all four proxies via analysis.universe.ETF_TICKERS membership
check inside the engine; none of SPY/MDY/IJR/QQQ needs S&P-500-stock
membership gating (is_member() short-circuits to True whenever is_etf).

Answers: does 12-month momentum work better on a broader (MDY/IJR, mid/small
cap) or more volatile (QQQ, tech-heavy) universe than the large-cap SPY
benchmark, when applied as a single-instrument, capacity-unconstrained
strategy? This is a genuinely different question from Part 8a (which asked
whether the CURRENT MULTI-STOCK strategy beats the indices) -- here the
question is whether momentum-the-signal, not stock-picking, works better on
one proxy than another.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-selection-audit")

import json

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import precompute_ticker, slice_ticker, simulate_portfolio_compounding_mtm, compute_metrics_fixed
from data.ingestion import get_price_df

OUT_DIR = r"c:\GitHub\investment-app-selection-audit\research\selection_audit_2026-08-08"
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")

with open(r"c:\GitHub\investment-app-selection-audit\config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)
starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 10000))
per_trade_pct = float(CONFIG.get("trading212_invest", {}).get("per_trade_pct", 0.05))
max_deployed_pct = 0.70  # matches the fixed value used throughout this audit's other pool runs

gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
gspc_df.index = pd.to_datetime(gspc_df.index)
gspc_close_full = gspc_df["close"].astype(float)
gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

PROXIES = {"SPY": "S&P 500 (large-cap benchmark)", "QQQ": "Nasdaq-100 (tech-heavy, high volatility)",
           "MDY": "S&P MidCap 400 (broader, mid-cap)", "IJR": "S&P SmallCap 600 (broader, small-cap, most volatile)"}

# None of these 4 index-tracking ETFs are in the frozen research ETF_TICKERS
# tuple (that list is specific to the old, since-removed ETF sleeve's own
# hand-picked instruments -- SPY itself was never a candidate there). The
# engine's is_etf flag (ticker in universe.ETF_TICKERS) is what makes it skip
# the S&P-500-STOCK membership check (is_member(), which would incorrectly
# reject an index ETF that is not itself an S&P 500 constituent stock) --
# temporarily extending the tuple for the duration of this read-only script
# is a data-membership lookup, not a change to any decision/threshold logic;
# restored immediately after, same pattern as this project's other research
# monkeypatches of pure/lookup functions (see full_trade_export_enriched's
# own precedent).
_orig_etf_tickers = universe.ETF_TICKERS
universe.ETF_TICKERS = tuple(set(_orig_etf_tickers) | set(PROXIES))

results = {}
for ticker, desc in PROXIES.items():
    assert ticker in universe.ETF_TICKERS, f"{ticker} unexpectedly not in ETF_TICKERS -- is_member() gating would apply"
    c = precompute_ticker(ticker, CONFIG)
    assert c is not None, f"precompute_ticker failed for {ticker}"
    c = slice_ticker(c, START, END)
    assert c is not None and not c["frame"].empty, f"slice_ticker produced empty frame for {ticker}"

    trades = simulate_portfolio_compounding_mtm(
        [c], CONFIG, gspc_close, per_trade_pct, max_deployed_pct, collect_rejections=False,
    )
    m = compute_metrics_fixed(trades, starting_capital)
    n_trades = len(trades)
    win_rate = sum(1 for t in trades if t["pnl_gbp"] > 0) / n_trades if n_trades else 0.0
    by_reason = pd.Series([t["exit_reason"] for t in trades]).value_counts().to_dict() if trades else {}
    print(f"{ticker} ({desc}): n_trades={n_trades} cagr={m['cagr_pct']:.2f}% dd={m['max_drawdown_pct']:.2f}% "
          f"win_rate={win_rate:.1%} pf={m.get('profit_factor', 0):.3f} exits={by_reason}")
    results[ticker] = {
        "description": desc, "n_trades": n_trades, "cagr_pct": m["cagr_pct"], "max_drawdown_pct": m["max_drawdown_pct"],
        "win_rate": round(win_rate, 4), "profit_factor": m.get("profit_factor"), "exit_reason_counts": by_reason,
        "total_pnl_gbp": round(sum(t["pnl_gbp"] for t in trades), 2),
    }

universe.ETF_TICKERS = _orig_etf_tickers  # restore -- read-only script, must not leave global state patched

with open(f"{OUT_DIR}/part8b_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nWrote {OUT_DIR}/part8b_results.json")
