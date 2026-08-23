"""PART 2 -- rename/alias investigation. READ-ONLY, nothing built.

A format mismatch (BRK.B vs BRK-B) fails LOUDLY: no data, a FAILED resolution
row, a never-fetched ticker that never leaves the queue. A rename is the
opposite: the company keeps its identity and changes symbol, and nothing errors.
This establishes whether that silent case actually bites here.

No database writes. verify_ticker_identity() is confirmed write-free (its
callers do the writing), so calling it here is a read.

Database: the LOCAL research copy, price_data through 2026-08-07, 871 tickers,
1,204 universe_membership tickers. Stated per the standing "name which database"
rule -- this copy predates ticker_fundamentals and is used only for price_data
and universe_membership, both of which it carries in full.
"""
import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "database" / "investment_research.db"
RATE = 2.5

# Known S&P 500 symbol changes where the COMPANY continued (same entity, new
# symbol) -- as distinct from an acquisition, delisting or bankruptcy. Chosen to
# span the window and both directions of outcome.
RENAMES = [
    ("FB", "META", "Facebook -> Meta Platforms, 2022-06"),
    ("UTX", "RTX", "United Technologies -> Raytheon Technologies, 2020-04"),
    ("ANTM", "ELV", "Anthem -> Elevance Health, 2022-06"),
    ("WLTW", "WTW", "Willis Towers Watson plc rebrand, 2022-01"),
    ("ABC", "COR", "AmerisourceBergen -> Cencora, 2023-08"),
    ("FISV", "FI", "Fiserv ticker change, 2023-06"),
    ("CBE", "ETN", "Cooper Industries -> Eaton, 2012-11 (acquisition, control)"),
]


def q(c, sql, params=()):
    return c.execute(sql, params).fetchall()


def series(c, t):
    n, mn, mx = q(c, "SELECT COUNT(*), MIN(date), MAX(date) FROM price_data WHERE ticker=?", (t,))[0]
    win = [(str(a)[:10], str(b)[:10]) for a, b in
           q(c, "SELECT start_date, end_date FROM universe_membership WHERE ticker=? ORDER BY start_date", (t,))]
    return {"rows": n, "first": str(mn)[:10] if mn else None,
            "last": str(mx)[:10] if mx else None, "windows": win}


def main():
    from analysis import universe
    from data.ingestion import verify_ticker_identity

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    pool = set(universe.get_live_stock_candidates())
    known = set(universe.get_all_known_stock_tickers())
    out = {"pairs": {}, "identity": {}}

    print("=" * 96)
    print("(b) WHAT A RENAME DOES TO THE STORED DATA")
    print("=" * 96)
    print(f"{'pair':<28}{'symbol':<7}{'rows':>6}  {'first':<11}{'last':<11}{'in pool':<9}windows")
    for old, new, why in RENAMES:
        out["pairs"][f"{old}->{new}"] = {"why": why}
        for t in (old, new):
            s = series(c, t)
            out["pairs"][f"{old}->{new}"][t] = s | {"in_live_pool": t in pool,
                                                    "in_known_tickers": t in known}
            print(f"{why[:27]:<28}{t:<7}{s['rows']:>6}  {str(s['first']):<11}{str(s['last']):<11}"
                  f"{'YES' if t in pool else 'no':<9}{s['windows']}")
        print()

    print("=" * 96)
    print("(d) DOES verify_ticker_identity() SEE A RENAME? -- run on both sides of three pairs")
    print("=" * 96)
    for old, new, _ in RENAMES[:3]:
        for t in (old, new):
            r = verify_ticker_identity(t)
            out["identity"][t] = r
            print(f"  {t:<6} status={str(r.get('status')):<14} reason={str(r.get('reason'))[:44]:<46}"
                  f" quote_type={r.get('evidence', {}).get('quote_type')!r}"
                  f" long_name={str(r.get('evidence', {}).get('long_name'))[:28]!r}")
            time.sleep(RATE)

    (HERE / "investigate.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("\nwritten: investigate.json")


if __name__ == "__main__":
    main()
