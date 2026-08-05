import sys, json, time
sys.path.insert(0, '.')
import market_cap_research as mc

with open('../output/candidate_pool_500.json') as f:
    pool = json.load(f)

print(f"Fetching shares-outstanding for {len(pool)} tickers...", flush=True)
t0 = time.time()
summary = mc.backfill_shares_outstanding(pool)
elapsed = time.time() - t0
print(f"Done in {elapsed:.1f}s", flush=True)
print("succeeded:", len(summary["succeeded"]))
print("no_cik:", len(summary["no_cik"]), summary["no_cik"])
print("no_data:", len(summary["no_data"]), summary["no_data"])
print("errors:", len(summary["errors"]), summary["errors"])
print("cik_changed:", summary["cik_changed"])

with open('../output/shares_fetch_summary.json', 'w') as f:
    json.dump(summary, f, indent=2, default=str)
