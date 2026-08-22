"""READ-ONLY probe: what does Tapestry actually look like in T212's live
instrument metadata today? Performs a single GET via get_instruments().
Writes nothing, to the database or anywhere else."""
import json, sys
sys.path.insert(0, "/root/investment-research")
from brokers.trading212 import Trading212Client, resolve_instrument_candidate

insts = Trading212Client(live=False).get_instruments()
print("TOTAL_INSTRUMENTS", len(insts))

def row(i):
    return {k: i.get(k) for k in ("ticker", "shortName", "name", "currencyCode", "type", "isin", "addedOn")}

print("\n-- ticker == TPR_US_EQ (the convention guess)")
print(json.dumps([row(i) for i in insts if str(i.get("ticker")) == "TPR_US_EQ"], indent=1))

print("\n-- shortName == TPR")
print(json.dumps([row(i) for i in insts if str(i.get("shortName") or "").strip().upper() == "TPR"], indent=1))

print("\n-- name contains TAPESTRY")
print(json.dumps([row(i) for i in insts if "TAPESTRY" in str(i.get("name") or "").upper()], indent=1))

print("\n-- transposed _EQ_US suffix, whole instrument set")
print(json.dumps([row(i) for i in insts if str(i.get("ticker") or "").endswith("_EQ_US")], indent=1))

print("\n-- suffix counts")
print("_US_EQ", sum(1 for i in insts if str(i.get("ticker") or "").endswith("_US_EQ")))
print("_EQ_US", sum(1 for i in insts if str(i.get("ticker") or "").endswith("_EQ_US")))

print("\n-- what the UNMODIFIED resolver returns for TPR today")
print(json.dumps(resolve_instrument_candidate("TPR", insts), indent=1))
