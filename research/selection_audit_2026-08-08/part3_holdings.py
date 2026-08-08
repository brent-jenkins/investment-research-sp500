"""Part 3 of the descriptive selection audit: what did the system actually
hold. Read-only, uses the same preserved enriched-corrected export as parts
1-2 (3,436 trades, sector/industry already pulled for the 331 tickers that
actually traded) plus Pool 1's existing 503-ticker sector map (candidate-pool
baseline) and, if present, the freshly-fetched Pool 2 sector map.
"""
import json

import pandas as pd

SRC = r"c:\GitHub\investment-app\research\full_trade_export_enriched_corrected_2026-08-06\full_trade_export_enriched_corrected_2007-2026.csv"
POOL1_SECTOR_MAP = r"c:\GitHub\investment-app\research\entry_volume_sector_2026-08-07\sector_map.csv"
POOL2_SECTOR_MAP = r"c:\GitHub\investment-app-selection-audit\research\selection_audit_2026-08-08\sector_map_pool2.csv"
OUT_DIR = r"c:\GitHub\investment-app-selection-audit\research\selection_audit_2026-08-08"

df = pd.read_csv(SRC, parse_dates=["entry_date", "exit_date"])
n = len(df)
results = {}

# ---------------------------------------------------------------------------
# Sector breakdown of positions vs candidate pool composition
# ---------------------------------------------------------------------------
sector_by_count = df["sector"].value_counts(dropna=False)
sector_by_capital = df.groupby("sector", dropna=False)["position_gbp_at_entry"].sum().sort_values(ascending=False)
total_capital = df["position_gbp_at_entry"].sum()

pool1_map = pd.read_csv(POOL1_SECTOR_MAP)
pool1_sector_dist = pool1_map["sector"].value_counts(dropna=False, normalize=True)

held_dist = (sector_by_count / n)
sector_tilt = pd.DataFrame({
    "held_pct_of_trades": held_dist,
    "candidate_pool_pct_(Pool1_503)": pool1_sector_dist,
}).fillna(0.0)
sector_tilt["tilt_ratio_(held/pool)"] = sector_tilt["held_pct_of_trades"] / sector_tilt["candidate_pool_pct_(Pool1_503)"].replace(0, float("nan"))
sector_tilt = sector_tilt.sort_values("held_pct_of_trades", ascending=False)

print("--- Sector: held (by trade count) vs Pool 1 candidate composition ---")
print(sector_tilt.round(4).to_string())

results["sector_by_count"] = sector_by_count.to_dict()
results["sector_by_capital_gbp"] = sector_by_capital.round(2).to_dict()
results["sector_by_capital_pct"] = (sector_by_capital / total_capital).round(4).to_dict()
results["pool1_candidate_sector_pct"] = pool1_sector_dist.round(4).to_dict()
results["sector_tilt_vs_pool1"] = sector_tilt.round(4).reset_index().rename(columns={"index": "sector"}).to_dict("records")

try:
    pool2_map = pd.read_csv(POOL2_SECTOR_MAP)
    pool2_sector_dist = pool2_map["sector"].value_counts(dropna=False, normalize=True)
    sector_tilt2 = pd.DataFrame({
        "held_pct_of_trades": held_dist,
        "candidate_pool_pct_(Pool2_1042)": pool2_sector_dist,
    }).fillna(0.0)
    sector_tilt2["tilt_ratio_(held/pool)"] = sector_tilt2["held_pct_of_trades"] / sector_tilt2["candidate_pool_pct_(Pool2_1042)"].replace(0, float("nan"))
    sector_tilt2 = sector_tilt2.sort_values("held_pct_of_trades", ascending=False)
    results["sector_tilt_vs_pool2"] = sector_tilt2.round(4).reset_index().rename(columns={"index": "sector"}).to_dict("records")
    print("\n--- Sector: held vs Pool 2 candidate composition ---")
    print(sector_tilt2.round(4).to_string())
except FileNotFoundError:
    print("\n(Pool 2 sector map not yet available -- skipped, will be added when fetch completes)")

# ---------------------------------------------------------------------------
# Repeat names
# ---------------------------------------------------------------------------
distinct_tickers = df["ticker"].nunique()
trade_counts = df["ticker"].value_counts()
reentries = trade_counts[trade_counts > 1]
top20 = (
    df.groupby("ticker")
    .agg(n_trades=("ticker", "size"), total_pnl_gbp=("pnl_gbp", "sum"), win_rate=("pnl_gbp", lambda s: (s > 0).mean()))
    .sort_values("n_trades", ascending=False)
    .head(20)
)

print(f"\n--- Repeat names ---")
print(f"Distinct tickers ever held: {distinct_tickers}")
print(f"Tickers traded more than once: {len(reentries)} ({len(reentries)/distinct_tickers:.1%} of distinct tickers)")
print(f"Max re-entries for a single ticker: {trade_counts.max()} ({trade_counts.idxmax()})")
print(top20.round(2).to_string())

results["repeat_names"] = {
    "distinct_tickers": int(distinct_tickers),
    "tickers_traded_more_than_once": int(len(reentries)),
    "max_trades_single_ticker": int(trade_counts.max()),
    "max_trades_ticker_name": str(trade_counts.idxmax()),
    "top_20_most_traded": top20.round(2).reset_index().to_dict("records"),
}

# ---------------------------------------------------------------------------
# Holding period distribution by exit reason
# ---------------------------------------------------------------------------
hp = df.groupby("exit_reason")["holding_days"].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9])
print(f"\n--- Holding period by exit reason ---")
print(hp.round(1).to_string())
results["holding_period_by_exit_reason"] = hp.round(2).reset_index().to_dict("records")

with open(f"{OUT_DIR}/part3_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nWrote {OUT_DIR}/part3_results.json")
