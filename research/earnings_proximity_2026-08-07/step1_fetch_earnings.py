"""Fetch earnings-date history (yfinance, Yahoo's own reported-earnings
calendar, capped at 100 quarters/~25 years by Yahoo's own API) for every
ticker that appears in the real baseline trade population
(research/vix_ma_giveback_2026-08-07/trades_baseline.csv, 388 distinct
tickers). Cached as CSV per this project's research reproducibility
standard -- external data backing a finding must be preserved locally."""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import time

import pandas as pd
import yfinance as yf

OUT_DIR = "research/earnings_proximity_2026-08-07"

trades = pd.read_csv("research/vix_ma_giveback_2026-08-07/trades_baseline.csv")
tickers = sorted(trades["ticker"].unique())
print(f"{len(tickers)} distinct tickers to fetch", flush=True)

rows = []
failures = []
t0 = time.time()
for i, ticker in enumerate(tickers):
    try:
        ed = yf.Ticker(ticker).get_earnings_dates(limit=100)
        if ed is None or ed.empty:
            failures.append((ticker, "empty"))
            continue
        for dt in ed.index:
            rows.append({"ticker": ticker, "earnings_date": pd.Timestamp(dt).tz_localize(None).normalize()})
    except Exception as exc:
        failures.append((ticker, str(exc)[:200]))
    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{len(tickers)} done ({time.time()-t0:.0f}s elapsed, {len(failures)} failures so far)", flush=True)
    time.sleep(0.3)

df = pd.DataFrame(rows).drop_duplicates()
df.to_csv(f"{OUT_DIR}/earnings_dates.csv", index=False)
print(f"\nWrote {len(df)} earnings-date rows for {df['ticker'].nunique()}/{len(tickers)} tickers", flush=True)
print(f"Failures: {len(failures)}", flush=True)
if failures:
    pd.DataFrame(failures, columns=["ticker", "error"]).to_csv(f"{OUT_DIR}/earnings_fetch_failures.csv", index=False)
    for t, e in failures[:20]:
        print(f"  {t}: {e}", flush=True)
print(f"Total runtime: {time.time()-t0:.0f}s", flush=True)
