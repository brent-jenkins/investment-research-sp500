"""One-off remediation script: run verify_ticker_identity() across all 408
tickers added by the 2026-08-06 survivorship-bias remediation (not just
the 39 already flagged by the earlier reversal-based investigation).
Writes results to ticker_data_quality via write_data_quality_status().

Caches raw yfinance .info responses to a local JSON file in this
directory (re-runnable without re-hitting yfinance) -- a research-script
convenience only; production's ingestion-time screen doesn't need one,
since a ticker_data_quality row is itself the "already checked" record.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-data-quality")

import json
import time

import pandas as pd
import yaml

from data.database import init_db, write_data_quality_status
from data.ingestion import verify_ticker_identity

OUT_DIR = "research/data_quality_remediation_2026-08-07"

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

init_db()

tickers = pd.read_csv(
    "research/earnings_dampener_reverify_2026-08-07/missing_earnings_tickers.csv"
)["ticker"].tolist()
print(f"Verifying identity for {len(tickers)} tickers (the full 408-ticker remediation-added population)", flush=True)

# Corroborating context: the earlier reversal-based classification, where available.
classified = pd.read_csv("research/recycled_ticker_investigation_2026-08-07/step3_classified_tickers.csv")
reversal_by_ticker = dict(zip(classified["ticker"], classified["classification"]))

rows = []
t0 = time.time()
for i, ticker in enumerate(tickers):
    result = verify_ticker_identity(ticker, CONFIG)
    result["evidence"]["prior_reversal_classification"] = reversal_by_ticker.get(ticker, "none")
    write_data_quality_status(
        ticker, result["status"], result["reason"], json.dumps(result["evidence"]),
    )
    rows.append({
        "ticker": ticker, "status": result["status"], "reason": result["reason"],
        "sentinel_case": result["sentinel_case"], **result["evidence"],
    })
    if (i + 1) % 25 == 0:
        print(f"  {i+1}/{len(tickers)} done ({time.time()-t0:.0f}s)", flush=True)

df = pd.DataFrame(rows)
df.to_csv(f"{OUT_DIR}/identity_verification_results.csv", index=False)

print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)
print("\n=== Verdict counts ===", flush=True)
print(df["status"].value_counts().to_string(), flush=True)

sentinel_count = df["sentinel_case"].sum()
print(f"\n=== _SENTINEL_END coverage blind spot ===", flush=True)
print(f"{sentinel_count}/{len(df)} tickers have a membership window that never closed -- "
      f"Check B not applicable, verdict rests on Check A alone for these.", flush=True)

needs_review = df[df["status"] == "NEEDS_REVIEW"]
needs_review.to_csv(f"{OUT_DIR}/needs_review.csv", index=False)
print(f"\n=== NEEDS_REVIEW ({len(needs_review)}/{len(df)}) -- reported for adjudication, not decided here ===", flush=True)
print(needs_review[["ticker", "reason"]].to_string(), flush=True)

# Cross-check against the investigation's own 22 LIKELY_RECYCLED / 17
# LIKELY_CORPORATE_ACTION as a sanity check.
print("\n=== Corroboration check against the prior reversal-based classification ===", flush=True)
print(pd.crosstab(df["status"], df["prior_reversal_classification"]).to_string(), flush=True)
