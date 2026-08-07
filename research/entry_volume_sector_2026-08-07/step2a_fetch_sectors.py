"""Fetch sector/industry for every live-candidate ticker not already
covered by the 2026-08-06 enriched export's sector map (341 tickers).
Reuses that data where available (no re-fetch needed) rather than
re-pulling all 503 from yfinance."""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import time

import pandas as pd
import yfinance as yf

from analysis import universe

OUT_DIR = "research/entry_volume_sector_2026-08-07"
END = pd.Timestamp("2026-08-06")

existing = pd.read_csv(
    "research/full_trade_export_enriched_corrected_2026-08-06/full_trade_export_enriched_corrected_2007-2026.csv"
)[["ticker", "sector", "industry"]].drop_duplicates(subset="ticker")
existing_map = dict(zip(existing["ticker"], zip(existing["sector"], existing["industry"])))
print(f"Reusing sector data for {len(existing_map)} tickers from the 2026-08-06 enriched export", flush=True)

candidates = universe.get_live_stock_candidates(as_of=END.date())
print(f"Live candidates: {len(candidates)}", flush=True)

missing = [t for t in candidates if t not in existing_map]
print(f"Need to fetch: {len(missing)}", flush=True)

rows = []
for t, (sector, industry) in existing_map.items():
    if t in candidates:
        rows.append({"ticker": t, "sector": sector, "industry": industry})

t0 = time.time()
failures = []
for i, ticker in enumerate(missing):
    try:
        info = yf.Ticker(ticker).info
        sector = info.get("sector")
        industry = info.get("industry")
        rows.append({"ticker": ticker, "sector": sector, "industry": industry})
    except Exception as exc:
        failures.append((ticker, str(exc)[:200]))
    if (i + 1) % 25 == 0:
        print(f"  {i+1}/{len(missing)} fetched ({time.time()-t0:.0f}s)", flush=True)
    time.sleep(0.3)

df = pd.DataFrame(rows)
df.to_csv(f"{OUT_DIR}/sector_map.csv", index=False)
print(f"\nWrote sector_map.csv: {len(df)} tickers ({df['sector'].notna().sum()} with a real sector value)", flush=True)
print(f"Failures: {len(failures)}", flush=True)
if failures:
    pd.DataFrame(failures, columns=["ticker", "error"]).to_csv(f"{OUT_DIR}/sector_fetch_failures.csv", index=False)
print(df["sector"].value_counts(dropna=False), flush=True)
