"""Stage 0 -- sample 'other_reason' missing tickers, attempt retrieval via the
real production ingestion path (data/ingestion.py:pull_history), and validate
each recovered series for symbol reuse before counting it as a success.

Runs against the isolated research DB copy only.
"""
import csv
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import data.database as dbmod  # noqa: E402

RESEARCH_DB = REPO_ROOT / "database" / "coverage_research.db"
dbmod.get_db_path = lambda: RESEARCH_DB

import data.ingestion as ing  # noqa: E402
ing.get_engine = dbmod.get_engine

from sqlalchemy import text  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
random.seed(20260806)


def load_other_reason():
    rows = list(csv.DictReader(open(OUT_DIR / "missing_ticker_classification.csv")))
    return [r for r in rows if r["category"] == "other_reason"]


def stratified_sample(rows, n=28, min_pre_2015=10):
    def end_year(r):
        return int(r["membership_end"][:4])

    pre_2015 = [r for r in rows if end_year(r) < 2015]
    post_2015 = [r for r in rows if end_year(r) >= 2015]
    random.shuffle(pre_2015)
    random.shuffle(post_2015)

    sample = pre_2015[:min_pre_2015]
    remaining_slots = n - len(sample)
    # Fill the rest by drawing evenly across all remaining years for spread
    rest_pool = pre_2015[min_pre_2015:] + post_2015
    random.shuffle(rest_pool)
    sample += rest_pool[:remaining_slots]
    return sample


def validate_recovered(ticker, membership_start, membership_end, engine):
    """Run the mandatory symbol-reuse validation checks. Returns
    (accepted: bool, reasons: list[str], stats: dict).
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT date, close FROM price_data WHERE ticker = :t ORDER BY date"),
            {"t": ticker},
        ).fetchall()

    if not rows:
        return False, ["no_rows_returned"], {}

    dates = [r[0] if isinstance(r[0], datetime) else datetime.strptime(str(r[0])[:10], "%Y-%m-%d") for r in rows]
    closes = [r[1] for r in rows]
    min_d, max_d = dates[0].date(), dates[-1].date()

    reasons = []

    # Check 1: does the returned series actually reach the membership window?
    # Require at least one row within 45 days of membership_end (proves data
    # continues up to near the real delisting date), UNLESS the window's own
    # end is in the future relative to the data we have (still-live edge case
    # -- not expected here since these are all past-ended windows).
    near_end_window = [
        d for d in dates if abs((d.date() - membership_end).days) <= 45
    ]
    if not near_end_window:
        reasons.append(
            f"no_price_row_within_45d_of_membership_end "
            f"(data spans {min_d}..{max_d}, membership ended {membership_end})"
        )

    # Check 2: series should start at or before roughly the later of
    # membership_start / the data window (2007-01-01) -- not start years
    # after the membership window closed (the MER case: data starts in
    # 2026, 17 years after membership ended in 2008).
    effective_expected_start = max(membership_start, date(2007, 1, 1))
    if min_d > membership_end:
        reasons.append(
            f"entire_series_postdates_membership_end "
            f"(first row {min_d} is after membership ended {membership_end}) -- likely symbol reuse"
        )
    elif min_d > effective_expected_start + timedelta(days=400):
        reasons.append(
            f"series_starts_suspiciously_late "
            f"(first row {min_d}, expected near {effective_expected_start})"
        )

    # Check 3: discontinuity around the delisting date -- a long gap
    # followed by a restart at a very different price level.
    for i in range(1, len(dates)):
        gap_days = (dates[i].date() - dates[i - 1].date()).days
        if gap_days > 90:
            spans_delisting = dates[i - 1].date() <= membership_end <= dates[i].date() + timedelta(days=90)
            prev_close, next_close = closes[i - 1], closes[i]
            if prev_close and next_close and prev_close > 0:
                ratio = next_close / prev_close
                if ratio > 3 or ratio < (1 / 3):
                    reasons.append(
                        f"discontinuity_gap_{gap_days}d_price_ratio_{ratio:.2f}x "
                        f"between {dates[i-1].date()} and {dates[i].date()} "
                        f"(spans_delisting={spans_delisting})"
                    )

    stats = {
        "n_rows": len(rows),
        "min_date": str(min_d),
        "max_date": str(max_d),
        "membership_start": str(membership_start),
        "membership_end": str(membership_end),
    }
    accepted = len(reasons) == 0
    return accepted, reasons, stats


def main():
    rows = load_other_reason()
    sample = stratified_sample(rows, n=28, min_pre_2015=10)
    sample.sort(key=lambda r: r["membership_end"])

    print(f"Sampled {len(sample)} tickers ({sum(1 for r in sample if int(r['membership_end'][:4]) < 2015)} with membership ending before 2015)")
    for r in sample:
        print(f"  {r['ticker']:8s} {r['membership_start']} .. {r['membership_end']}")

    tickers = [r["ticker"] for r in sample]
    print("\n=== Fetching via production ingestion path (data/ingestion.py:pull_history) ===")
    results = ing.pull_history(tickers, start_date="2007-01-01")

    engine = dbmod.get_engine()
    out_rows = []
    n_fetch_success = 0
    n_validated_accept = 0
    reject_reasons_count = {}

    for r in sample:
        t = r["ticker"]
        mstart = datetime.strptime(r["membership_start"], "%Y-%m-%d").date()
        mend = datetime.strptime(r["membership_end"], "%Y-%m-%d").date()
        rows_written = results.get(t, 0)
        if rows_written > 0:
            n_fetch_success += 1
            accepted, reasons, stats = validate_recovered(t, mstart, mend, engine)
        else:
            accepted, reasons, stats = False, ["fetch_returned_zero_rows"], {}

        if accepted:
            n_validated_accept += 1
        else:
            for reason in reasons:
                key = reason.split(" ")[0].split("(")[0]
                reject_reasons_count[key] = reject_reasons_count.get(key, 0) + 1

        out_rows.append(
            {
                "ticker": t,
                "membership_start": r["membership_start"],
                "membership_end": r["membership_end"],
                "rows_fetched": rows_written,
                "validation_accepted": accepted,
                "reject_reasons": "; ".join(reasons),
                **stats,
            }
        )
        status = "ACCEPTED" if accepted else "REJECTED"
        print(f"{t:8s} rows={rows_written:5d}  {status:9s} {reasons}")

    with open(OUT_DIR / "stage0_sample_results.csv", "w", newline="") as f:
        fieldnames = [
            "ticker", "membership_start", "membership_end", "rows_fetched",
            "validation_accepted", "reject_reasons", "n_rows", "min_date", "max_date",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    print(f"\n=== Summary ===")
    print(f"Sampled: {len(sample)}")
    print(f"Fetch returned >0 rows: {n_fetch_success} ({n_fetch_success/len(sample)*100:.1f}%)")
    print(f"Passed symbol-reuse validation: {n_validated_accept} ({n_validated_accept/len(sample)*100:.1f}%)")
    print(f"Rejection reasons breakdown: {reject_reasons_count}")
    print(f"\nWrote {OUT_DIR / 'stage0_sample_results.csv'}")


if __name__ == "__main__":
    main()
