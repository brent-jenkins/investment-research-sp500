"""Reconcile this session's pool sizes against the exit sweep's recorded
608 usable Pool 2 CLEAN / 500 Pool 1. READ-ONLY, no writes.

Reproduces step1_build_caches.py's own roster definition exactly:
    Pool 2 CLEAN = get_research_stock_candidates() INTERSECT {status='CLEAN'}
    Pool 1       = get_live_stock_candidates()
and "usable" = precompute_ticker() returns non-None (enough history to
compute ATR/signal/momentum/relative_rank), which is a stricter condition
than "has at least one price_data row".
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DB = ROOT / "database" / "investment_research.db"

import yaml
from analysis import universe
from analysis.backtest import precompute_ticker
from data import database as _db

assert _db.get_db_path().resolve() == DB.resolve()
CONFIG = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
clean = {r[0] for r in c.execute(
    "SELECT ticker FROM ticker_data_quality WHERE status='CLEAN'")}
c.close()
print(f"CLEAN tickers                                : {len(clean)}")

p2 = sorted(set(universe.get_research_stock_candidates()) & clean)
p1 = sorted(universe.get_live_stock_candidates())
print(f"Pool 2 CLEAN roster (research INTERSECT CLEAN): {len(p2)}")
print(f"Pool 1 roster (get_live_stock_candidates)     : {len(p1)}")

for label, tks in (("Pool 2 CLEAN", p2), ("Pool 1", p1)):
    ok = sum(1 for tk in tks if precompute_ticker(tk, CONFIG) is not None)
    print(f"{label:<13} usable (precompute_ticker non-None): {ok}/{len(tks)}")

print("\nexit sweep recorded: 612 CLEAN, 608 usable Pool 2 CLEAN / 500 Pool 1")
