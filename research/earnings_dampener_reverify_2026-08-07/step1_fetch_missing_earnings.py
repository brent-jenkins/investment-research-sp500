"""Fetch earnings-date history for the 408 tickers needed to cover the
FULL historical candidate universe (get_all_known_stock_tickers(), not
just today's 503 live/survivor candidates) that weren't already fetched
in research/earnings_proximity_2026-08-07/earnings_dates.csv. Combined
with that file, gives full coverage for the corrected-universe re-run."""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import time

import pandas as pd
import yfinance as yf

OUT_DIR = "research/earnings_dampener_reverify_2026-08-07"

missing = pd.read_csv(f"{OUT_DIR}/missing_earnings_tickers.csv")["ticker"].tolist()
print(f"{len(missing)} tickers to fetch", flush=True)

rows = []
failures = []
t0 = time.time()
for i, ticker in enumerate(missing):
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
        print(f"  {i+1}/{len(missing)} done ({time.time()-t0:.0f}s, {len(failures)} failures)", flush=True)
    time.sleep(0.3)

df = pd.DataFrame(rows).drop_duplicates()
df.to_csv(f"{OUT_DIR}/earnings_dates_additional.csv", index=False)
print(f"\nWrote {len(df)} rows for {df['ticker'].nunique()}/{len(missing)} tickers", flush=True)
print(f"Failures: {len(failures)}", flush=True)
if failures:
    pd.DataFrame(failures, columns=["ticker", "error"]).to_csv(f"{OUT_DIR}/fetch_failures.csv", index=False)
print(f"Total runtime: {time.time()-t0:.0f}s", flush=True)
