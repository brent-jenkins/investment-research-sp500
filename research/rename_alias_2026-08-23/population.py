"""(a) and (e) -- size the rename population structurally, and price its impact.

Derived from the membership source's own reconstructed history in
universe_membership plus price_data, NOT from memory of which companies renamed.

THE STRUCTURAL SIGNATURE OF A RENAME, stated before counting:
  1. an OLD symbol's last membership window CLOSES, and
  2. a NEW symbol's first window OPENS within HANDOFF_DAYS of that close, and
  3. the OLD symbol has ZERO price rows (yfinance serves nothing for a symbol
     that no longer exists), and
  4. the NEW symbol's price history STARTS BEFORE its own membership window
     opened -- because Yahoo carries the company's full pre-rename series under
     the new symbol.

(3) and (4) together are what separate a rename from a plain
removal-plus-unrelated-addition: an unrelated new entrant's price history starts
at ITS OWN listing, not before the predecessor's exit.

This is a candidate set, not a proven one -- an acquisition where the acquirer
was already listed produces the same shape. It is an UPPER BOUND, which is the
useful direction for a risk question.
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "database" / "investment_research.db"

SENTINEL = date(2099, 12, 31)
HANDOFF_DAYS = 21
DATA_START = date(2007, 1, 3)


def d(v):
    return date.fromisoformat(str(v)[:10])


def main():
    from analysis import universe

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    pool = set(universe.get_live_stock_candidates())

    wins = {}
    for t, s, e in c.execute("SELECT ticker, start_date, end_date FROM universe_membership"):
        wins.setdefault(t, []).append((d(s), d(e)))
    for t in wins:
        wins[t].sort()

    px = {t: (n, d(mn), d(mx)) for t, n, mn, mx in c.execute(
        "SELECT ticker, COUNT(*), MIN(date), MAX(date) FROM price_data GROUP BY ticker")}

    closes = [(t, w[-1][1]) for t, w in wins.items() if w[-1][1] < SENTINEL]
    opens = [(t, w[0][0]) for t, w in wins.items()]
    print(f"universe_membership tickers: {len(wins)}   with a CLOSED final window: {len(closes)}")

    candidates, handoffs = [], 0
    for old, close_d in closes:
        if old in px:
            continue                                  # (3) old must have no price data
        for new, open_d in opens:
            if new == old:
                continue
            if not (0 <= (open_d - close_d).days <= HANDOFF_DAYS):
                continue
            handoffs += 1
            if new not in px:
                continue
            n_new, first_new, _ = px[new]
            if first_new < max(open_d, DATA_START):   # (4) history predates its own window
                candidates.append({
                    "old": old, "new": new,
                    "old_close": close_d.isoformat(), "new_open": open_d.isoformat(),
                    "handoff_days": (open_d - close_d).days,
                    "new_rows": n_new, "new_first_price": first_new.isoformat(),
                    "new_in_live_pool": new in pool,
                    "new_history_predates_window_by_days": (max(open_d, DATA_START) - first_new).days,
                })

    print(f"handoff pairs within {HANDOFF_DAYS} days of a close (old has no price data): {handoffs}")
    print(f"RENAME-SHAPED candidates (old dataless AND new history predates its window): {len(candidates)}")
    print(f"  of which the new symbol is in the LIVE POOL today: "
          f"{sum(1 for r in candidates if r['new_in_live_pool'])}")

    print(f"\n{'old':<8}{'new':<8}{'closed':<12}{'opened':<12}{'gap':>4}  "
          f"{'new rows':>9}{'new first':>13}  {'live':<5}")
    for r in sorted(candidates, key=lambda r: r["old_close"]):
        print(f"{r['old']:<8}{r['new']:<8}{r['old_close']:<12}{r['new_open']:<12}"
              f"{r['handoff_days']:>4}  {r['new_rows']:>9}{r['new_first_price']:>13}  "
              f"{'YES' if r['new_in_live_pool'] else 'no':<5}")

    # ---- (e) impact: could any of this have truncated a momentum window?
    print("\n" + "=" * 84)
    print("(e) IMPACT -- do any of these new symbols have a SHORT series today?")
    print("=" * 84)
    short = [r for r in candidates if r["new_rows"] < 252]
    print(f"  new symbols with fewer than 252 rows (momentum uncomputable): {len(short)}")
    print(f"  new symbols in the live pool with a full 252+ row series      : "
          f"{sum(1 for r in candidates if r['new_in_live_pool'] and r['new_rows'] >= 252)}")

    orphan_pool = sorted(t for t in pool if t not in px)
    print(f"  LIVE-POOL tickers with NO price data at all                   : "
          f"{len(orphan_pool)} {orphan_pool}")

    (HERE / "population.json").write_text(json.dumps(
        {"handoff_pairs": handoffs, "candidates": candidates,
         "live_pool_without_price_data": orphan_pool}, indent=2), encoding="utf-8")
    print("\nwritten: population.json")


if __name__ == "__main__":
    main()
