"""§13.2 — reconfirm database state and pool sizes by DIRECT QUERY at run time.

Never inherited from the exit sweep's recorded state or from a prior session, per
the standing "derived state living in one place while work happens in another"
requirement. READ-ONLY: no writes of any kind.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DB = ROOT / "database" / "investment_research.db"

print(f"database file : {DB}")
print(f"exists        : {DB.exists()}  size={DB.stat().st_size:,} bytes")
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

n_rows, n_tick = c.execute(
    "SELECT COUNT(*), COUNT(DISTINCT ticker) FROM price_data").fetchone()
dmin, dmax = c.execute("SELECT MIN(date), MAX(date) FROM price_data").fetchone()
print(f"price_data    : {n_rows:,} rows / {n_tick} distinct tickers")
print(f"date range    : {dmin[:10]} .. {dmax[:10]}")

print("\nticker_data_quality:")
for status, n in c.execute(
        "SELECT status, COUNT(*) FROM ticker_data_quality GROUP BY status ORDER BY 2 DESC"):
    print(f"  {status:<14} {n}")

print("\nreference series present:")
for t in ("^GSPC", "^SP500TR", "^IRX"):
    row = c.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM price_data WHERE ticker=?", (t,)).fetchone()
    print(f"  {t:<10} rows={row[0]:<6} {str(row[1])[:10]} .. {str(row[2])[:10]}")
c.close()

# Pool sizes via the real production functions. data/database.py:get_db_path()
# resolves config["database"]["path"] relative to ITS OWN project root, which in
# this worktree is the copy above -- confirmed, not assumed, by the assertion.
from data import database as _db
assert _db.get_db_path().resolve() == DB.resolve(), (
    f"production code would read {_db.get_db_path()}, not {DB}")
print("\nproduction get_db_path() resolves to the SAME file: OK")
from analysis import universe

pool1 = sorted(universe.get_live_stock_candidates())
pool2 = sorted(universe.get_research_stock_candidates())
print(f"\nPool 1 get_live_stock_candidates()     : {len(pool1)}")
print(f"Pool 2 get_research_stock_candidates() : {len(pool2)}")

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
have = {r[0] for r in c.execute("SELECT DISTINCT ticker FROM price_data")}
c.close()
print(f"Pool 1 usable (has price_data)         : {len(set(pool1) & have)}")
print(f"Pool 2 usable (has price_data)         : {len(set(pool2) & have)}")

print("\n--- exit sweep recorded (2026-08-12), for comparison ONLY ---")
print("    price_data through 2026-08-07, 612 CLEAN, 608 usable Pool 2 CLEAN / 500 Pool 1")
print("    A mismatch is a finding to diagnose, not a number to adopt silently.")
