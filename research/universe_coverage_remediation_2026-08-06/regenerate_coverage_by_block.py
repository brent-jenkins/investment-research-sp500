"""Regenerate universe_coverage_by_block.csv against the post-backfill DB,
matching the original methodology's column semantics exactly:
  n_members_at_block_end       -- len(get_live_stock_candidates(as_of=block_end))
  n_with_any_price_data        -- of those, how many have ANY price_data row at all
  n_usable_covering_block_start -- of those, how many have a row at/before block_start
                                    (i.e. history actually reaches back that far)
  pct_usable = n_usable_covering_block_start / n_members_at_block_end * 100
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import data.database as dbmod  # noqa: E402
RESEARCH_DB = REPO_ROOT / "database" / "coverage_research_postbackfill.db"
dbmod.get_db_path = lambda: RESEARCH_DB

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from analysis import universe  # noqa: E402
from analysis.backtest import make_blocks  # noqa: E402

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
N_BLOCKS = 16


def main():
    engine = dbmod.get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ticker, MIN(date) FROM price_data GROUP BY ticker")).fetchall()
    min_date = {t: pd.Timestamp(d) for t, d in rows}
    has_any = set(min_date.keys())

    blocks = make_blocks(START, END, n_blocks=N_BLOCKS)
    out = []
    for i, (bs, be) in enumerate(blocks):
        cands = universe.get_live_stock_candidates(as_of=be.date())
        n_members = len(cands)
        n_any = sum(1 for t in cands if t in has_any)
        n_usable = sum(1 for t in cands if t in min_date and min_date[t] <= bs)
        pct = round(n_usable / n_members * 100, 1) if n_members else 0.0
        out.append({
            "block": i, "start": bs.date().isoformat(), "end": be.date().isoformat(),
            "n_members_at_block_end": n_members, "n_with_any_price_data": n_any,
            "n_usable_covering_block_start": n_usable, "pct_usable": pct,
        })
        print(out[-1])

    df = pd.DataFrame(out)
    out_path = Path(__file__).resolve().parent / "universe_coverage_by_block_postbackfill.csv"
    df.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")

    dominant_era = df[(df["block"] >= 3) & (df["block"] <= 9)]
    print(f"\nDominant era (blocks 3-9) mean pct_usable: {dominant_era['pct_usable'].mean():.1f}%")


if __name__ == "__main__":
    main()
