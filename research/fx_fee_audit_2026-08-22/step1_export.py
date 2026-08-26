"""READ-ONLY export of the tables needed for the FX-fee audit. Runs on the
server; writes CSV to /tmp only. No database writes of any kind."""
import csv
import json
import sqlite3

DB = "/root/investment-research/database/investment_research.db"
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
c.row_factory = sqlite3.Row


def dump(name, sql):
    rows = c.execute(sql).fetchall()
    p = f"/tmp/fx_{name}.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        if rows:
            w = csv.writer(fh)
            w.writerow(rows[0].keys())
            for r in rows:
                w.writerow(["" if v is None else v for v in r])
    print(f"{name}: {len(rows)} rows -> {p}")


dump("orders", """
    SELECT id, order_id, ticker, t212_ticker, direction, quantity, estimated_price,
           estimated_value_gbp, fill_price, filled_quantity, status, currency_code,
           exit_reason, placed_at, filled_at, created_at
    FROM invest_orders ORDER BY created_at
""")
dump("pnl", "SELECT * FROM pnl ORDER BY entry_date")
dump("gbpusd", """
    SELECT date, open, high, low, close FROM price_data
    WHERE ticker='GBPUSD=X' ORDER BY date
""")
dump("eurusd", """
    SELECT date, close FROM price_data WHERE ticker='EURUSD=X' ORDER BY date
""")

# Raw broker-reported position snapshot -- the only place T212's own numbers
# (ppl / fxPpl) are stored verbatim.
row = c.execute(
    "SELECT value FROM system_metadata WHERE key='t212_positions_snapshot'").fetchone()
if row:
    with open("/tmp/fx_positions_snapshot.json", "w", encoding="utf-8") as fh:
        fh.write(row[0])
    pos = json.loads(row[0])
    print(f"positions snapshot: {len(pos)} positions -> /tmp/fx_positions_snapshot.json")
    print("  keys present:", sorted(pos[0].keys()) if pos else "(empty)")

# Anything else in system_metadata that might carry a raw broker response.
print("\nsystem_metadata keys (looking for raw responses / fee fields):")
for (k,) in c.execute("SELECT key FROM system_metadata ORDER BY key"):
    print("  ", k)
c.close()
