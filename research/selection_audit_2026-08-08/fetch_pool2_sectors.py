"""Fetch sector/industry for Pool 2 (get_research_stock_candidates(), 1,042
tickers) candidates not already covered by the existing sector maps
(entry_volume_sector_2026-08-07's 503-ticker Pool-1 map, and the enriched-
export's 331-traded-ticker map). Needed for Part 3's candidate-pool sector-
composition comparison. Read-only against yfinance; writes a new sector map,
does not touch either existing source file.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-selection-audit")

import time

import pandas as pd
import yfinance as yf

from analysis import universe

OUT_DIR = r"c:\GitHub\investment-app-selection-audit\research\selection_audit_2026-08-08"

existing1 = pd.read_csv(
    r"c:\GitHub\investment-app\research\entry_volume_sector_2026-08-07\sector_map.csv"
)[["ticker", "sector", "industry"]].drop_duplicates(subset="ticker")
existing2 = pd.read_csv(
    r"c:\GitHub\investment-app\research\full_trade_export_enriched_corrected_2026-08-06\full_trade_export_enriched_corrected_2007-2026.csv"
)[["ticker", "sector", "industry"]].drop_duplicates(subset="ticker")
existing_map = dict(zip(existing1["ticker"], zip(existing1["sector"], existing1["industry"])))
for t, s, i in existing2.itertuples(index=False):
    existing_map.setdefault(t, (s, i))
print(f"Reusing sector data for {len(existing_map)} tickers from existing maps", flush=True)

pool2 = sorted(universe.get_research_stock_candidates())
print(f"Pool 2 candidates: {len(pool2)}", flush=True)

missing = [t for t in pool2 if t not in existing_map]
print(f"Need to fetch: {len(missing)}", flush=True)

rows = []
for t in pool2:
    if t in existing_map:
        sector, industry = existing_map[t]
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
    time.sleep(0.25)

df = pd.DataFrame(rows)
df.to_csv(f"{OUT_DIR}/sector_map_pool2.csv", index=False)
print(f"\nWrote sector_map_pool2.csv: {len(df)} tickers ({df['sector'].notna().sum()} with a real sector value)", flush=True)
print(f"Failures: {len(failures)}", flush=True)
if failures:
    pd.DataFrame(failures, columns=["ticker", "error"]).to_csv(f"{OUT_DIR}/sector_fetch_failures_pool2.csv", index=False)
print(df["sector"].value_counts(dropna=False), flush=True)
