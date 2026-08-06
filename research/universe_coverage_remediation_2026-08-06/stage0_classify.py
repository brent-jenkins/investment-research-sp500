"""Stage 0 -- classify membership-table tickers absent from price_data.

Runs against the isolated research copy of the DB
(database/coverage_research.db), never the production file. See
CLAUDE.md's "Experimental/research work runs locally" for why.
"""
import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import data.database as dbmod  # noqa: E402

RESEARCH_DB = REPO_ROOT / "database" / "coverage_research.db"


def _patched_get_db_path():
    return RESEARCH_DB


dbmod.get_db_path = _patched_get_db_path

from sqlalchemy import text  # noqa: E402

HISTORY_START = date(2007, 1, 1)


def main():
    engine = dbmod.get_engine()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
        # Sanity: confirm we are NOT pointed at the live production file.
        db_file = conn.execute(text("PRAGMA database_list")).fetchall()
        print("Connected DB file:", db_file[0][2])

        membership_tickers = {
            r[0] for r in conn.execute(text("SELECT DISTINCT ticker FROM universe_membership"))
        }
        price_tickers = {
            r[0] for r in conn.execute(text("SELECT DISTINCT ticker FROM price_data"))
        }

        print(f"universe_membership distinct tickers: {len(membership_tickers)}")
        print(f"price_data distinct tickers: {len(price_tickers)}")

        missing = sorted(membership_tickers - price_tickers)
        print(f"Missing from price_data entirely: {len(missing)}")

        # For each missing ticker, get its membership window (min start, max end)
        rows = conn.execute(
            text(
                "SELECT ticker, MIN(start_date), MAX(end_date), COUNT(*) "
                "FROM universe_membership GROUP BY ticker"
            )
        ).fetchall()
        windows = {r[0]: (r[1], r[2], r[3]) for r in rows}

        # Unavailable-ticker flags set by the production ingestion path
        unavailable_rows = conn.execute(
            text(
                "SELECT key FROM system_metadata WHERE key LIKE 'unavailable_ticker:%' "
                "AND value = 'true'"
            )
        ).fetchall()
        unavailable_flagged = {r[0].split(":", 1)[1] for r in unavailable_rows}

        # price_data actual date bounds (to know what "the data window" really is,
        # not just the config's nominal start date)
        pd_bounds = conn.execute(
            text("SELECT MIN(date), MAX(date) FROM price_data")
        ).fetchone()
        print(f"price_data actual date bounds: {pd_bounds[0]} .. {pd_bounds[1]}")

    def parse_date(v):
        if v is None:
            return None
        if isinstance(v, str):
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        if isinstance(v, datetime):
            return v.date()
        return v

    true_survivorship = []
    other_reason = []
    no_membership_window = []

    for t in missing:
        w = windows.get(t)
        if w is None:
            no_membership_window.append(t)
            continue
        start, end, n_windows = w
        end_d = parse_date(end)
        start_d = parse_date(start)
        flagged_unavailable = t in unavailable_flagged
        has_dot = "." in t

        if end_d is not None and end_d < HISTORY_START:
            true_survivorship.append((t, start_d, end_d, n_windows, flagged_unavailable, has_dot))
        else:
            other_reason.append((t, start_d, end_d, n_windows, flagged_unavailable, has_dot))

    print()
    print(f"True survivorship (membership ended before {HISTORY_START}): {len(true_survivorship)}")
    print(f"Other reason (membership overlaps data window but still absent): {len(other_reason)}")
    print(f"No membership window found at all (unexpected): {len(no_membership_window)}")

    dot_notation_other = [r for r in other_reason if r[5]]
    flagged_other = [r for r in other_reason if r[4]]
    print(f"  ... of 'other reason': dot-notation tickers: {len(dot_notation_other)}")
    print(f"  ... of 'other reason': previously flagged unavailable by ingestion: {len(flagged_other)}")

    dot_notation_survivorship = [r for r in true_survivorship if r[5]]
    print(f"  ... of 'true survivorship': dot-notation tickers: {len(dot_notation_survivorship)}")

    # Write full classification to CSV for the record
    import csv

    out_dir = Path(__file__).resolve().parent
    with open(out_dir / "missing_ticker_classification.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "category", "membership_start", "membership_end", "n_windows",
                    "flagged_unavailable", "has_dot_notation"])
        for t, s, e, n, flag, dot in true_survivorship:
            w.writerow([t, "true_survivorship", s, e, n, flag, dot])
        for t, s, e, n, flag, dot in other_reason:
            w.writerow([t, "other_reason", s, e, n, flag, dot])
        for t in no_membership_window:
            w.writerow([t, "no_membership_window", "", "", "", "", ""])

    print()
    print(f"Wrote {out_dir / 'missing_ticker_classification.csv'}")

    # Print 'other_reason' list sorted by membership_end descending (most recent first)
    print()
    print("=== Sample of 'other_reason' tickers (should be recoverable), most-recent-ending first ===")
    other_sorted = sorted(other_reason, key=lambda r: (r[2] or date.min), reverse=True)
    for r in other_sorted[:40]:
        print(r)


if __name__ == "__main__":
    main()
