"""PHASE 1 -- ANALYSIS ONLY. No status changes, no writes to ticker_data_quality.

Contiguity-across-membership-end-date analysis for the 209 NEEDS_REVIEW
tickers, plus control tickers (FNMA, PALM, a sample of the 19 confirmed
LIKELY_RECYCLED QUARANTINED tickers) for rule validation.

For each ticker, within the window [end_date-90d, end_date+365d]:
  a. Largest gap in price_data (calendar days AND missing NYSE trading days).
  b. (reported separately, in step6) distribution of these gaps.
  c. date_overrun_days (already in the evidence JSON, carried through).
  d. Price-scale continuity: median close of the 30 trading rows immediately
     before the largest gap (or before end_date if no gap > 2 calendar days
     exists in the window) vs. the 30 rows immediately after.
  e. long_name (from the cached identity_verification_results.csv -- no new
     yfinance calls) vs. historical constituent name. NOTE: this project's
     universe_membership table has NO company-name column and the
     fja05680/sp500 source is ticker-only -- there is no "historical
     constituent name from the membership data" to compare against. Reported
     honestly as unavailable, not fabricated via a new external fetch.
  f. Prior reversal classification, as context only.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-data-quality")

import json
import sqlite3
import time

import pandas as pd
import pandas_market_calendars as mcal

DB_PATH = "database/investment_research.db"
OUT_DIR = "research/data_quality_remediation_2026-08-07"

WINDOW_BEFORE_DAYS = 90
WINDOW_AFTER_DAYS = 365
MIN_GAP_CALENDAR_DAYS = 4  # below this is indistinguishable from a normal weekend/3-day-weekend; anything smaller isn't even a candidate "gap"
SCALE_LOOKBACK_N = 30


def _nyse_calendar():
    return mcal.get_calendar("NYSE")


def analyze_ticker(conn, cal, ticker: str, last_membership_end: str) -> dict:
    """Two gap measurements, deliberately not one -- an early run using only
    a window-bounded gap search silently mis-scored PALM/NCC as
    "insufficient_data" (0 rows in [-90d, +365d]) when their real signal is
    a gap so large (PALM: ~12 years) it extends far outside any window this
    size. Capping the search to the window would have hidden the exact
    signal being tested for.

    1. boundary_gap -- UNBOUNDED search (whole price_data history) for the
       nearest row at/before end_date and the nearest row after end_date.
       This is what actually catches PALM/NCC-shaped corruption: a symbol
       that sits dormant for years after the real company delists before an
       unrelated entity's history begins under the same ticker.
    2. window_gap -- the largest gap strictly among rows falling inside
       [end_date-90d, end_date+365d], independent of the boundary itself --
       catches a distinct gap pattern elsewhere in the window, if any.
    Reported "largest gap" for a ticker is whichever of the two is bigger;
    both are kept in the output for transparency, not collapsed silently.
    """
    end_date = pd.Timestamp(last_membership_end).date()
    win_start = end_date - pd.Timedelta(days=WINDOW_BEFORE_DAYS)
    win_end = end_date + pd.Timedelta(days=WINDOW_AFTER_DAYS)

    # -- Unbounded boundary gap --
    before_row = conn.execute(
        "SELECT date, close FROM price_data WHERE ticker = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (ticker, pd.Timestamp(end_date).isoformat()),
    ).fetchone()
    after_row = conn.execute(
        "SELECT date, close FROM price_data WHERE ticker = ? AND date > ? ORDER BY date ASC LIMIT 1",
        (ticker, pd.Timestamp(end_date).isoformat()),
    ).fetchone()
    boundary_gap_cal_days = boundary_gap_missing_td = None
    if before_row is not None and after_row is not None:
        d0 = pd.Timestamp(before_row[0]).date()
        d1 = pd.Timestamp(after_row[0]).date()
        boundary_gap_cal_days = (d1 - d0).days
        if boundary_gap_cal_days >= MIN_GAP_CALENDAR_DAYS:
            expected = cal.valid_days(start_date=(d0 + pd.Timedelta(days=1)).isoformat(),
                                       end_date=(d1 - pd.Timedelta(days=1)).isoformat())
            boundary_gap_missing_td = len(expected)
        else:
            boundary_gap_missing_td = 0
    elif after_row is not None and before_row is None:
        # No row at/before end_date anywhere in this ticker's history -- the
        # gap is at minimum "end_date to first known row" (a lower bound;
        # the true gap could be larger if the real company's own last trade
        # predates this project's earliest ingested data for it).
        d1 = pd.Timestamp(after_row[0]).date()
        boundary_gap_cal_days = (d1 - end_date).days
        expected = cal.valid_days(start_date=end_date.isoformat(),
                                   end_date=(d1 - pd.Timedelta(days=1)).isoformat())
        boundary_gap_missing_td = len(expected)

    # -- Window-bounded gap (independent of the boundary) --
    win_rows = conn.execute(
        "SELECT date, close FROM price_data WHERE ticker = ? AND date >= ? AND date <= ? ORDER BY date",
        (ticker, win_start.isoformat(), win_end.isoformat()),
    ).fetchall()
    window_gap_cal_days = window_gap_missing_td = 0
    win_gap_start = win_gap_end = None
    if len(win_rows) >= 2:
        wdates = [pd.Timestamp(r[0]).date() for r in win_rows]
        for i in range(1, len(wdates)):
            d0, d1 = wdates[i - 1], wdates[i]
            cd = (d1 - d0).days
            if cd < MIN_GAP_CALENDAR_DAYS:
                continue
            expected = cal.valid_days(start_date=(d0 + pd.Timedelta(days=1)).isoformat(),
                                       end_date=(d1 - pd.Timedelta(days=1)).isoformat())
            missing = len(expected)
            if missing > window_gap_missing_td:
                window_gap_missing_td, window_gap_cal_days, win_gap_start, win_gap_end = missing, cd, d0, d1

    # -- Pick the larger of the two gaps as "largest gap" (item a) --
    boundary_td = boundary_gap_missing_td or 0
    if boundary_td >= window_gap_missing_td:
        max_gap_missing_td = boundary_td
        max_gap_cal_days = boundary_gap_cal_days or 0
        gap_source = "boundary"
    else:
        max_gap_missing_td = window_gap_missing_td
        max_gap_cal_days = window_gap_cal_days
        gap_source = "window"

    # -- Price-scale continuity (item d): 30 rows before/after the LARGER
    # gap identified above (or before/after end_date if no gap at all). --
    all_rows = conn.execute(
        "SELECT date, close FROM price_data WHERE ticker = ? ORDER BY date", (ticker,)
    ).fetchall()
    dates_all = [pd.Timestamp(r[0]).date() for r in all_rows]
    closes_all = [r[1] for r in all_rows]

    if gap_source == "boundary" and before_row is not None and after_row is not None:
        anchor_before = pd.Timestamp(before_row[0]).date()
        anchor_after = pd.Timestamp(after_row[0]).date()
    elif gap_source == "window" and win_gap_start is not None:
        anchor_before, anchor_after = win_gap_start, win_gap_end
    else:
        anchor_before = anchor_after = end_date

    before_closes = [c for d, c in zip(dates_all, closes_all) if d <= anchor_before][-SCALE_LOOKBACK_N:]
    after_closes = [c for d, c in zip(dates_all, closes_all) if d >= anchor_after][:SCALE_LOOKBACK_N]
    med_before = float(pd.Series(before_closes).median()) if before_closes else None
    med_after = float(pd.Series(after_closes).median()) if after_closes else None
    scale_ratio = (med_after / med_before) if (med_before and med_after and med_before != 0) else None

    return {
        "ticker": ticker, "last_membership_end": last_membership_end,
        "n_rows_in_window": len(win_rows),
        "boundary_gap_calendar_days": boundary_gap_cal_days, "boundary_gap_missing_trading_days": boundary_gap_missing_td,
        "window_gap_calendar_days": window_gap_cal_days, "window_gap_missing_trading_days": window_gap_missing_td,
        "max_gap_calendar_days": max_gap_cal_days, "max_gap_missing_trading_days": max_gap_missing_td,
        "gap_source": gap_source,
        "median_close_before": med_before, "median_close_after": med_after,
        "scale_ratio": scale_ratio,
        "n_before_rows": len(before_closes), "n_after_rows": len(after_closes),
        "no_data_before_end_date_at_all": before_row is None,
        "insufficient_data": len(win_rows) < 2 and before_row is None,
    }


def main():
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    cal = _nyse_calendar()

    # NEEDS_REVIEW population (209) + evidence.
    dq_rows = conn.execute(
        "SELECT ticker, status, evidence FROM ticker_data_quality WHERE status = 'NEEDS_REVIEW'"
    ).fetchall()
    print(f"NEEDS_REVIEW population: {len(dq_rows)} tickers", flush=True)

    id_results = pd.read_csv(f"{OUT_DIR}/identity_verification_results.csv")
    long_name_by_ticker = dict(zip(id_results["ticker"], id_results["long_name"]))
    prior_class_by_ticker = dict(zip(id_results["ticker"], id_results["prior_reversal_classification"]))

    # Controls: FNMA is already in the 209 (included naturally). PALM is not
    # part of the 408 at all (never ran through verify_ticker_identity()) --
    # added explicitly. ALL 19 confirmed LIKELY_RECYCLED QUARANTINED tickers
    # are included (not a sample) so the rule is validated against the full
    # known-corrupt population, not a cherry-picked subset.
    control_tickers = ["PALM"] + [
        "BMC", "BOL", "CBE", "CFC", "CIN", "CNG", "GDW", "HM", "HPC",
        "KRI", "MEE", "MII", "NCC", "PBG", "PTV", "SRR", "TIE", "TNB", "UCM",
    ]
    control_evidence = {}
    for t in control_tickers:
        row = conn.execute("SELECT status, evidence FROM ticker_data_quality WHERE ticker = ?", (t,)).fetchone()
        if row is None:
            # PALM: never ran through verify_ticker_identity() (not in the
            # 408). Compute last_membership_end directly from universe
            # membership windows for this control.
            windows = conn.execute(
                "SELECT end_date FROM universe_membership WHERE ticker = ? ORDER BY end_date DESC LIMIT 1", (t,)
            ).fetchone()
            last_end = windows[0] if windows else None
            control_evidence[t] = {"status": "NOT_IN_DQ_TABLE (not part of the 408)", "last_membership_end": last_end}
        else:
            ev = json.loads(row[1])
            control_evidence[t] = {"status": row[0], "last_membership_end": ev.get("last_membership_end")}

    results = []
    for i, (ticker, status, evidence_json) in enumerate(dq_rows):
        ev = json.loads(evidence_json)
        last_end = ev.get("last_membership_end")
        r = analyze_ticker(conn, cal, ticker, last_end)
        r["date_overrun_days"] = ev.get("date_overrun_days")
        r["stored_boundary_gap_days"] = ev.get("boundary_gap_days")  # the pre-existing production evidence field, for comparison
        r["long_name"] = long_name_by_ticker.get(ticker)
        r["prior_reversal_classification"] = prior_class_by_ticker.get(ticker, "none")
        r["population"] = "needs_review"
        results.append(r)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(dq_rows)} done ({time.time()-t0:.0f}s)", flush=True)

    print(f"NEEDS_REVIEW analysis done ({time.time()-t0:.0f}s). Computing controls...", flush=True)
    for t in control_tickers:
        last_end = control_evidence[t]["last_membership_end"]
        if last_end is None:
            print(f"  SKIP {t}: no membership end_date found", flush=True)
            continue
        r = analyze_ticker(conn, cal, t, last_end)
        r["date_overrun_days"] = None
        r["boundary_gap_days_narrow"] = None
        r["long_name"] = None
        r["prior_reversal_classification"] = prior_class_by_ticker.get(t, "none")
        r["population"] = f"control ({control_evidence[t]['status']})"
        results.append(r)

    df = pd.DataFrame(results)
    df.to_csv(f"{OUT_DIR}/step5_contiguity_features.csv", index=False)
    print(f"\nWrote step5_contiguity_features.csv ({len(df)} rows, {time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
