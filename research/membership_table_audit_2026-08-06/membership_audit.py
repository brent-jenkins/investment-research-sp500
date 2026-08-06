"""Task B -- membership-table completeness audit.

Diffs universe_membership's point-in-time membership on four dates
(2008-01-01, 2011-01-01, 2015-01-01, 2020-01-01) against an independent
reference dataset (hanshof/sp500_constituents on GitHub -- a different
maintainer/pipeline from this project's own source, fja05680/sp500).

Read-only against the production DB (SELECT only, no writes). Reference
CSV preserved alongside this script per the Research Reproducibility
Standard.
"""
import csv
import pickle
import sqlite3
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF_CSV = HERE / "hanshof_sp500_reference.csv"
TARGETS = [date(2008, 1, 1), date(2011, 1, 1), date(2015, 1, 1), date(2020, 1, 1)]


def load_reference():
    rows = []
    with open(REF_CSV) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            d = date.fromisoformat(row[0])
            tickers = set(row[1].split(","))
            rows.append((d, tickers))
    rows.sort(key=lambda x: x[0])
    return rows


def effective_snapshot(rows, target):
    best = None
    for d, tickers in rows:
        if d <= target:
            best = (d, tickers)
        else:
            break
    return best


def normalize(t):
    t = t.replace(".", "-").upper()
    if t == "FB":  # known rename, already in this codebase's own alias map
        return "META"
    return t


def load_our_windows(db_path="database/investment_research.db"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT ticker, start_date, end_date FROM universe_membership")
    rows = cur.fetchall()
    windows = {}
    for t, s, e in rows:
        sd = date.fromisoformat(str(s)[:10])
        ed = date.fromisoformat(str(e)[:10])
        windows.setdefault(t, []).append((sd, ed))
    return windows


def main():
    ref_rows = load_reference()
    windows = load_our_windows()

    out_rows = []
    for target in TARGETS:
        our_members = {tk for tk, ws in windows.items() if any(s <= target <= e for s, e in ws)}
        ref_date, ref_members = effective_snapshot(ref_rows, target)
        our_norm = {normalize(x) for x in our_members}
        ref_norm = {normalize(x) for x in ref_members}
        intersect = our_norm & ref_norm
        missing_from_ours = sorted(ref_norm - our_norm)
        extra_in_ours = sorted(our_norm - ref_norm)
        completeness = len(intersect) / len(ref_norm) * 100

        print(f"=== {target} (reference effective {ref_date}) ===")
        print(f"  reference count: {len(ref_norm)}")
        print(f"  our count: {len(our_norm)}")
        print(f"  completeness vs reference: {completeness:.1f}%")
        print(f"  in reference, missing from ours ({len(missing_from_ours)}): {missing_from_ours}")
        print(f"  in ours, absent from reference ({len(extra_in_ours)}): {extra_in_ours}")
        print()

        out_rows.append({
            "target_date": target, "reference_effective_date": ref_date,
            "reference_count": len(ref_norm), "our_count": len(our_norm),
            "completeness_pct": round(completeness, 1),
            "missing_from_ours": ";".join(missing_from_ours),
            "extra_in_ours": ";".join(extra_in_ours),
        })

    with open(HERE / "membership_audit_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)


if __name__ == "__main__":
    main()
