"""Part 8a: index-level universe comparison (descriptive, no new stock data).

Fetches broad-index series (source stated per series below) and compares the
system's REALIZED trade-level returns (from the same preserved
full_trade_export_enriched_corrected export used in Parts 1-3) against each,
per block (make_blocks(..., n_blocks=16), the exact boundaries and
compute_metrics_fixed()/(_cagr_dd_for_series() functions run_full_validation()
itself uses -- imported directly, not re-derived) and overall.

Sources and coverage, confirmed before use:
  - S&P 500 total return: ^SP500TR (yfinance). Full coverage 2007-01-03 to
    2026-08-07 confirmed (4,930 rows) -- a genuine total-return index series,
    not a proxy.
  - Nasdaq Composite: ^IXIC (yfinance). Full coverage confirmed. PRICE-RETURN
    ONLY -- yfinance carries no total-return variant for the Composite (no
    ^IXICTR exists). Understates true return by the Composite's own dividend
    yield (~0.7-1%/yr in recent years, higher in earlier years) -- smaller
    bias than for a value-tilted index, but a real, stated one, not modelled.
  - S&P MidCap 400: no ^SP400TR history available via yfinance (confirmed:
    only 1 row returned, today's quote only -- not usable historically).
    Proxy used instead: MDY (SPDR S&P MidCap 400 ETF Trust), adjusted close
    (auto_adjust=True, dividends reinvested). A real, tradeable, dividend-
    reinvested series -- genuinely closer to "total return" than a raw price
    index -- but carries the ETF's own ~0.23%/yr expense ratio and any
    tracking error, which a true index-level total-return series would not.
  - S&P SmallCap 600: same situation and same proxy choice, IJR (iShares
    Core S&P Small-Cap ETF), same caveats.

This does NOT need point-in-time membership data for any of these -- all
four are already survivorship-bias-free by construction (they track live,
reconstituted indices/ETFs, not a fixed historical ticker list).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-selection-audit")

import json

import pandas as pd
import yaml
import yfinance as yf

from analysis.backtest import make_blocks, compute_metrics_fixed
from analysis.validation import _cagr_dd_for_series

SRC = r"c:\GitHub\investment-app\research\full_trade_export_enriched_corrected_2026-08-06\full_trade_export_enriched_corrected_2007-2026.csv"
OUT_DIR = r"c:\GitHub\investment-app-selection-audit\research\selection_audit_2026-08-08"

with open(r"c:\GitHub\investment-app-selection-audit\config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)
starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 10000))

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")  # matches the enriched-export run's own end date

trades_df = pd.read_csv(SRC, parse_dates=["entry_date", "exit_date"])
# compute_metrics_fixed()/compute_metrics() require is_win (bool) and pnl_pct
# as a FRACTION (it multiplies by 100 itself) -- this export's own pnl_pct
# column is already in percent units (e.g. -6.08 meaning -6.08%), and has no
# is_win column (win_loss is a string "WIN"/"LOSS" instead). Both derived
# here, not re-computed from anything -- pure unit/column-name reconciliation.
trades_df["is_win"] = trades_df["pnl_gbp"] > 0
trades_df["pnl_pct"] = trades_df["pnl_pct"] / 100.0
print(f"Loaded {len(trades_df)} trades, total_pnl={trades_df['pnl_gbp'].sum():.2f}")

SERIES = {
    "SP500_TR": "^SP500TR",
    "Nasdaq_Composite_price_only": "^IXIC",
    "MidCap400_via_MDY_div_adj": "MDY",
    "SmallCap600_via_IJR_div_adj": "IJR",
}

closes = {}
for label, ticker in SERIES.items():
    auto_adj = ticker in ("MDY", "IJR")
    h = yf.Ticker(ticker).history(start="2007-01-01", end="2026-08-08", auto_adjust=auto_adj)
    h.index = h.index.tz_localize(None)
    n_rows = len(h)
    span = (h.index.min(), h.index.max()) if n_rows else (None, None)
    print(f"{label} ({ticker}): {n_rows} rows, {span[0]} -> {span[1]}, auto_adjust={auto_adj}")
    assert n_rows > 4000, f"{ticker} coverage looks insufficient ({n_rows} rows) -- aborting"
    closes[label] = h["Close"].astype(float)
    closes[label].index = pd.to_datetime(closes[label].index)

blocks = make_blocks(START, END, n_blocks=16)
results = {"n_blocks": len(blocks), "blocks": []}

for i, (b_start, b_end) in enumerate(blocks):
    strat_trades = trades_df[(trades_df["exit_date"] >= b_start) & (trades_df["exit_date"] <= b_end)].to_dict("records")
    strat_m = compute_metrics_fixed(strat_trades, starting_capital)
    row = {
        "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
        "n_trades": len(strat_trades), "strat_cagr_pct": strat_m["cagr_pct"], "strat_dd_pct": strat_m["max_drawdown_pct"],
        "strat_pf": strat_m.get("profit_factor", 0.0),
    }
    for label, series in closes.items():
        cagr, dd = _cagr_dd_for_series(series, b_start, b_end)
        row[f"{label}_cagr_pct"] = round(cagr, 2) if cagr is not None else None
        row[f"{label}_dd_pct"] = round(dd, 2) if dd is not None else None
        row[f"strategy_beats_{label}_cagr"] = (
            (strat_m["cagr_pct"] > cagr) if (cagr is not None) else None
        )
    results["blocks"].append(row)

# Overall (full-window) comparison
full_strat = compute_metrics_fixed(trades_df.to_dict("records"), starting_capital)
overall = {"strategy_cagr_pct": full_strat["cagr_pct"], "strategy_dd_pct": full_strat["max_drawdown_pct"], "strategy_pf": full_strat.get("profit_factor")}
for label, series in closes.items():
    cagr, dd = _cagr_dd_for_series(series, START, END)
    overall[f"{label}_cagr_pct"] = round(cagr, 2) if cagr is not None else None
    overall[f"{label}_dd_pct"] = round(dd, 2) if dd is not None else None
results["overall"] = overall

n_blocks_beat = {}
for label in closes:
    n_blocks_beat[label] = sum(1 for r in results["blocks"] if r.get(f"strategy_beats_{label}_cagr"))
results["n_blocks_strategy_beats_each_index"] = n_blocks_beat

print("\n--- Overall (2007-2026) ---")
print(json.dumps(overall, indent=2))
print("\n--- Blocks where strategy beat each index's CAGR (of 16) ---")
print(json.dumps(n_blocks_beat, indent=2))
print("\n--- Per-block detail ---")
for r in results["blocks"]:
    print(f"B{r['block']:2d} [{r['start']}..{r['end']}] n={r['n_trades']:3d} strat_cagr={r['strat_cagr_pct']:7.2f}  "
          f"SP500TR={r['SP500_TR_cagr_pct']:7.2f}  IXIC={r['Nasdaq_Composite_price_only_cagr_pct']:7.2f}  "
          f"MDY={r['MidCap400_via_MDY_div_adj_cagr_pct']:7.2f}  IJR={r['SmallCap600_via_IJR_div_adj_cagr_pct']:7.2f}")

with open(f"{OUT_DIR}/part8a_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nWrote {OUT_DIR}/part8a_results.json")
