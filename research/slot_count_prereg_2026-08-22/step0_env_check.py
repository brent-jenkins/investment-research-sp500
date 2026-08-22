"""Step 0 -- environment / derived-state verification, per PREREGISTRATION.md Sec 5 and
CLAUDE.md's derived-state rule ("query it directly, fresh, in the place it's about to be used").

Read-only. No writes of any kind.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from data.database import get_engine, get_db_path
from analysis import universe


def main() -> None:
    print(f"DB file this session will read: {get_db_path()}")
    eng = get_engine()
    with eng.connect() as c:
        n_px = c.execute(text("SELECT COUNT(DISTINCT ticker) FROM price_data")).scalar()
        dmin, dmax = c.execute(
            text("SELECT MIN(date), MAX(date) FROM price_data")
        ).fetchone()
        rows = c.execute(text("SELECT COUNT(*) FROM price_data")).scalar()
        dq = dict(
            c.execute(
                text("SELECT status, COUNT(*) FROM ticker_data_quality GROUP BY status")
            ).fetchall()
        )
        sptr = c.execute(
            text(
                "SELECT COUNT(*), MIN(date), MAX(date) FROM price_data WHERE ticker='^SP500TR'"
            )
        ).fetchone()
        irx = c.execute(
            text(
                "SELECT COUNT(*), MIN(date), MAX(date) FROM price_data WHERE ticker='^IRX'"
            )
        ).fetchone()
        gspc = c.execute(
            text(
                "SELECT COUNT(*), MIN(date), MAX(date) FROM price_data WHERE ticker='^GSPC'"
            )
        ).fetchone()

    print(f"price_data: {rows:,} rows, {n_px} distinct tickers, {dmin} -> {dmax}")
    print(f"ticker_data_quality: {dq}")
    print(f"^SP500TR: {sptr}")
    print(f"^IRX    : {irx}")
    print(f"^GSPC   : {gspc}")

    pool1 = sorted(universe.get_live_stock_candidates())
    research = set(universe.get_research_stock_candidates())
    with eng.connect() as c:
        clean = {
            r[0]
            for r in c.execute(
                text("SELECT ticker FROM ticker_data_quality WHERE status='CLEAN'")
            ).fetchall()
        }
    pool2_clean = sorted(research & clean)

    print(f"Pool 1 (get_live_stock_candidates)        : {len(pool1)}")
    print(f"get_research_stock_candidates()           : {len(research)}")
    print(f"CLEAN rows in ticker_data_quality         : {len(clean)}")
    print(f"Pool 2 CLEAN (research & CLEAN)           : {len(pool2_clean)}")

    # usable = has price data at all
    with eng.connect() as c:
        have_px = {
            r[0] for r in c.execute(text("SELECT DISTINCT ticker FROM price_data")).fetchall()
        }
    print(f"Pool 1 usable (has price_data)            : {len(set(pool1) & have_px)}")
    print(f"Pool 2 CLEAN usable (has price_data)      : {len(set(pool2_clean) & have_px)}")

    out = Path(__file__).resolve().parent
    (out / "pool1_tickers.txt").write_text("\n".join(pool1), encoding="utf-8")
    (out / "pool2_clean_tickers.txt").write_text("\n".join(pool2_clean), encoding="utf-8")
    print(f"\nWrote pool ticker lists to {out}")


if __name__ == "__main__":
    main()
