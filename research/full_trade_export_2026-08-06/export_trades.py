"""Full 2007-2026 trade-history CSV export -- personal manual exploration,
NOT a hypothesis test. Read-only, no variant comparison, no block
validation needed (per task instructions) -- this exports the real
baseline's own trade list exactly as the current, unmodified production
logic would generate it.

Backtest convention: CONTINUOUS WALK (analysis.validation.run_full_validation()
is always a continuous 2007-2026 walk; see its own docstring) -- appropriate
here since this is not a variant comparison, just "run the real engine and
export everything it produced."

Engine: analysis.backtest.simulate_portfolio_compounding_mtm() via
analysis.validation.run_full_validation(config, mark_to_market=True,
live_universe=True) -- the one engine confirmed to genuinely match live
execution. No custom harness, no monkeypatching, no custom candidate pool.

RE-RUN FRESH, not reused from any of tonight's saved trade JSONs -- this
needs to reflect the CURRENT implementation specifically, and a real
production fix (3496224, "Fix silent BRK.B/BF.B ticker bug and rolling-
window start-date drift") landed partway through tonight's session, after
several earlier investigations' own trade-generation runs.

FX rate usage -- confirmed by direct code trace before writing this
script, not assumed:
  - grep across analysis/*.py (decision.py, backtest.py, signals.py,
    universe.py, crisis.py, volatility.py, validation.py) for
    GBPUSD/EURUSD/_fx_rates/_to_gbp: ZERO matches. Neither FX rate is read
    by, or has any path into, entry/exit/sizing/momentum/signal/crisis
    logic anywhere.
  - Every real usage lives in brokers/trading212.py (_fx_rates(),
    _to_gbp(), get_instrument_price_gbp(), the fill-price-to-GBP
    conversion in poll_placed_orders()) and output/performance.py
    (_t212_ccy_to_gbp(), used for displaying position value/P&L in GBP) --
    all currency-conversion/reporting only, converting a USD- or
    EUR-denominated broker figure into GBP for bookkeeping/display.
  - CONCLUSION: both FX rates are reporting/currency-conversion inputs
    only in the current system, never a trading-decision input. Included
    in this export unconditionally regardless (per task instruction) --
    an unused series with a real correlation to trade outcomes would
    itself be a new candidate worth knowing about.

S&P 500 level: analysis/crisis.py's is_crisis_day()/is_entry_blocked()/
compute_raw_trigger() all take a `gspc_close` parameter -- the real
crisis-check literally reads ^GSPC's daily close. Exported here as
entry_sp500_level/exit_sp500_level for exactly that reason.

Honest limitation, stated upfront: the underlying price_data is daily
bars only -- entry_date/exit_date are DATES, not timestamps. No
intraday time-of-open/time-of-close exists anywhere in this system's
historical data, so none is fabricated here.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import time

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import precompute_ticker
from analysis.validation import run_full_validation
from data.ingestion import get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\full_trade_export_2026-08-06'
DOWNLOADS_PATH = r'C:\Users\user\Downloads\sp500invest_full_trade_export_2007-2026.csv'
END = pd.Timestamp("2026-08-03")  # matches real DB coverage max date exactly
START = pd.Timestamp("2007-01-01")


def _series_for(ticker: str) -> pd.Series:
    """Date-indexed close-price series for a reference ticker (^GSPC,
    GBPUSD=X, EURUSD=X), forward-filled so a trade date that happens to
    land on a day the series itself is missing (rare, e.g. an FX-only
    holiday mismatch) still resolves to the most recent known value
    rather than producing a gap."""
    df = get_price_df(ticker, days=8000)
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    return df["close"].astype(float).sort_index()


def _lookup(series: pd.Series, date: pd.Timestamp):
    """Most recent value at or before `date` -- ffill lookup, never
    look-ahead (only ever used here for descriptive export, not any
    decision, so no look-ahead-bias concern regardless)."""
    sub = series.loc[:date]
    return float(sub.iloc[-1]) if len(sub) else None


def main():
    t0 = time.time()
    print("=== Full 2007-2026 trade-history CSV export ===", flush=True)

    # ------------------------------------------------------------------
    # 1. Real backtest run -- FRESH, current code (post 3496224).
    # ------------------------------------------------------------------
    print("Running real backtest (simulate_portfolio_compounding_mtm, "
          "live_universe=True, continuous walk 2007-2026)...", flush=True)
    result = run_full_validation(
        CONFIG, start=START, end=END, mark_to_market=True, live_universe=True,
    )
    print(f"  engine_used={result['engine_used']} deployment_basis={result['deployment_basis']} "
          f"backtest_convention={result['backtest_convention']}", flush=True)
    print(f"  total_trades={result['total_trades']}  n_tickers_with_data={result['n_tickers_with_data']}"
          f"/{result['n_tickers_total']}", flush=True)

    trades = result["trades"]
    trades_df = pd.DataFrame(trades)
    exit_reason_counts = trades_df["exit_reason"].value_counts()
    n_max_hold = int(exit_reason_counts.get("MAX_HOLDING", 0))
    print("  exit_reason breakdown:\n" + exit_reason_counts.to_string(), flush=True)
    print(f"  MAX_HOLDING share: {100*n_max_hold/len(trades_df):.1f}% "
          f"(tonight's established baseline: 3161 trades / 56.8% MAX_HOLDING)", flush=True)

    # ------------------------------------------------------------------
    # 2. Reference series: ^GSPC (crisis-check input), GBPUSD=X, EURUSD=X
    #    (currency-conversion/reporting only -- see module docstring).
    # ------------------------------------------------------------------
    print("\nLoading reference series (^GSPC, GBPUSD=X, EURUSD=X)...", flush=True)
    gspc = _series_for("^GSPC")
    gbpusd = _series_for("GBPUSD=X")
    eurusd = _series_for("EURUSD=X")
    print(f"  ^GSPC: {gspc.index.min().date()} -> {gspc.index.max().date()}, {len(gspc)} rows")
    print(f"  GBPUSD=X: {gbpusd.index.min().date()} -> {gbpusd.index.max().date()}, {len(gbpusd)} rows")
    print(f"  EURUSD=X: {eurusd.index.min().date()} -> {eurusd.index.max().date()}, {len(eurusd)} rows")

    # ------------------------------------------------------------------
    # 3. Momentum-at-entry: recompute per-ticker frames (precompute_ticker,
    #    the real engine's own function, unmodified) to recover the
    #    momentum value each entry actually saw -- not stored on the trade
    #    dict itself.
    # ------------------------------------------------------------------
    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"\nBuilding precompute_ticker() caches for {len(candidate_tickers)} live candidates "
          f"(momentum-at-entry lookup)...", flush=True)
    frames = {}
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            frames[ticker] = c["frame"]
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(candidate_tickers)} tickers cached "
                  f"({time.time()-t0:.0f}s elapsed)", flush=True)
    print(f"  {len(frames)}/{len(candidate_tickers)} tickers have usable frames", flush=True)

    # ------------------------------------------------------------------
    # 4. Build the export rows.
    # ------------------------------------------------------------------
    print(f"\nBuilding {len(trades)} export rows...", flush=True)
    rows = []
    missing_momentum = 0
    for tr in trades:
        entry_date = pd.Timestamp(tr["entry_date"])
        exit_date = pd.Timestamp(tr["exit_date"])
        ticker = tr["ticker"]

        frame = frames.get(ticker)
        momentum_at_entry = None
        if frame is not None and entry_date in frame.index:
            m = frame.loc[entry_date, "momentum"]
            momentum_at_entry = None if pd.isna(m) else float(m)
        else:
            missing_momentum += 1

        rows.append({
            "ticker": ticker,
            "win_loss": "WIN" if tr["is_win"] else "LOSS",
            "pnl_gbp": round(tr["pnl_gbp"], 2),
            "pnl_pct": round(tr["pnl_pct"] * 100, 4),
            "entry_date": entry_date.date().isoformat(),
            "exit_date": exit_date.date().isoformat(),
            "holding_days": tr["holding_days"],
            "entry_sp500_level": _lookup(gspc, entry_date),
            "exit_sp500_level": _lookup(gspc, exit_date),
            "entry_gbpusd_reporting_only": _lookup(gbpusd, entry_date),
            "exit_gbpusd_reporting_only": _lookup(gbpusd, exit_date),
            "entry_eurusd_reporting_only": _lookup(eurusd, entry_date),
            "exit_eurusd_reporting_only": _lookup(eurusd, exit_date),
            "exit_reason": tr["exit_reason"],
            "momentum_at_entry_pct": (
                round(momentum_at_entry * 100, 4) if momentum_at_entry is not None else None
            ),
        })

    out_df = pd.DataFrame(rows)
    out_df = out_df.sort_values("entry_date").reset_index(drop=True)
    print(f"  momentum-at-entry missing for {missing_momentum}/{len(trades)} trades "
          f"(ticker/date not in live-candidate frame cache, e.g. a ticker that has since "
          f"left the live universe)", flush=True)

    out_df.to_csv(f"{OUT_DIR}/full_trade_export_2007-2026.csv", index=False)
    print(f"\nWrote {len(out_df)} rows -> {OUT_DIR}/full_trade_export_2007-2026.csv", flush=True)

    out_df.to_csv(DOWNLOADS_PATH, index=False)
    print(f"Also wrote a copy -> {DOWNLOADS_PATH}", flush=True)

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
