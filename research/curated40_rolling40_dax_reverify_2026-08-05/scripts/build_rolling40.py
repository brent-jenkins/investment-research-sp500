"""Builds the rolling-40 (quarterly-reconstituted top-40-by-market-cap)
membership schedule from the SEC-EDGAR-sourced market_cap_history table
this investigation resurrected (market_cap_research.py, populated by
fetch_shares.py), matching CLAUDE_HISTORY.md's "Curated-40 vs. rolling-40
reproduction investigation (2026-08-02)" own stated methodology: quarterly
reconstitutions, 2009-12-31 through 2026-06-30 (extended here through the
latest quarter-end inside this run's own [start, end] window, 2026-06-30,
since price/shares data doesn't yet cover 2026-09-30).

Writes output/rolling40_schedule.json: {quarter_end_date_iso: [ticker, ...]}
(exactly 40 tickers per quarter once enough of the pool has usable
market-cap data; fewer only in the earliest quarters where SEC XBRL
coverage hasn't started for most filers yet -- consistent with this
project's own documented ~2009-for-large-filers phase-in).
"""
import json
import sys
from datetime import date
from pathlib import Path

import env_setup

env_setup.patch_all()

import market_cap_research as mc

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

with open(OUTPUT_DIR / "candidate_pool_500.json") as f:
    POOL = json.load(f)


def quarter_ends(start: date, end: date) -> list[date]:
    out = []
    y, q_month = start.year, ((start.month - 1) // 3) * 3 + 3
    d = date(y, q_month, 1)
    # step to the actual last day of that quarter
    import calendar
    def qend(yy, mm):
        return date(yy, mm, calendar.monthrange(yy, mm)[1])
    cur = qend(y, q_month)
    while cur <= end:
        if cur >= start:
            out.append(cur)
        if q_month == 12:
            y, q_month = y + 1, 3
        else:
            q_month += 3
        cur = qend(y, q_month)
    return out


if __name__ == "__main__":
    qends = quarter_ends(date(2009, 12, 31), date(2026, 6, 30))
    print(f"{len(qends)} quarter-end reconstitution dates: {qends[0]} .. {qends[-1]}")

    schedule: dict[str, list[str]] = {}
    coverage_counts = []
    for q in qends:
        ranked = []
        for t in POOL:
            mcap = mc.market_cap(t, q)
            if mcap is not None and mcap > 0:
                ranked.append((t, mcap))
        ranked.sort(key=lambda x: -x[1])
        top40 = [t for t, _ in ranked[:40]]
        schedule[q.isoformat()] = top40
        coverage_counts.append(len(ranked))

    print(f"tickers with resolvable market cap per quarter: min={min(coverage_counts)} "
          f"max={max(coverage_counts)} (first 8 quarters: {coverage_counts[:8]})")

    with open(OUTPUT_DIR / "rolling40_schedule.json", "w") as f:
        json.dump(schedule, f, indent=2)
    print(f"wrote {OUTPUT_DIR / 'rolling40_schedule.json'}")

    # Quick sanity spot-check, printed for the write-up: most recent
    # quarter's top-10 by market cap, and how many quarters META appears in
    # (should be zero -- META has zero XBRL rows, per the fetch summary).
    last_q = qends[-1].isoformat()
    print(f"Top 10 as of {last_q}: {schedule[last_q][:10]}")
    meta_appearances = sum(1 for q, lst in schedule.items() if "META" in lst)
    print(f"META appears in {meta_appearances} of {len(schedule)} quarters (expect 0 -- known SEC/XBRL gap)")
