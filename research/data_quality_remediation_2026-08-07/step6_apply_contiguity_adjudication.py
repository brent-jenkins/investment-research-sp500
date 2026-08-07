"""PHASE 2 -- applies the user-approved contiguity rule to the 209
NEEDS_REVIEW population, per PHASE1_CONTIGUITY_REPORT.md.

Rule (approved as-is): gap_missing_trading_days >= 100 -> QUARANTINED.
Otherwise -> stays NEEDS_REVIEW (no promotion to CLEAN -- absence of a gap
is not evidence of genuineness; 9/19 confirmed-recycled controls also show
no gap).

Reason-code distinction (per explicit user review of CAR specifically):
  - CAR: ENTITY_DISCONTINUITY_CORPORATE -- gap timing lines up with Avis
    Budget Group's real 2006 Cendant spinoff. Two different entities under
    one symbol, but a legitimate corporate event, not symbol-squatting --
    flagged separately so a future reader knows this one is a candidate
    for truncation-based repair, unlike a squatter series.
  - Everything else: RECYCLED_SYMBOL (default). Five other tickers in the
    79 (DEC, DIGI, EQ, MI, USBC) were flagged LIKELY_CORPORATE_ACTION by
    the PRIOR reversal classifier too, but their gaps are 1,107-5,681
    missing trading days (4-22+ years) -- nothing like CAR's clean
    107-trading-day spinoff-transition profile. Deliberately NOT given the
    same distinction: a real spinoff doesn't leave a company's own stock
    untraded for two decades. Reported here, not silently generalised.

Every one of the 209 gets its evidence JSON updated with the contiguity
check result (merged with the existing verify_ticker_identity() evidence,
never overwritten) regardless of which bucket it lands in -- so a future
adjudicator sees the Check C result even for tickers that stay
NEEDS_REVIEW.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-data-quality")

import json

import pandas as pd

from data.database import get_engine, write_data_quality_status
from data.ingestion import check_boundary_contiguity
from sqlalchemy import text

OUT_DIR = "research/data_quality_remediation_2026-08-07"
CAR_SPECIAL_CASE = "CAR"


def main():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ticker, reason, evidence FROM ticker_data_quality WHERE status = 'NEEDS_REVIEW'")
        ).fetchall()
    print(f"Adjudicating {len(rows)} NEEDS_REVIEW tickers via contiguity (Check C)...", flush=True)

    quarantined, stayed = [], []
    for ticker, old_reason, evidence_json in rows:
        old_evidence = json.loads(evidence_json)
        c = check_boundary_contiguity(ticker, old_evidence.get("last_membership_end"))

        merged_evidence = dict(old_evidence)
        merged_evidence["contiguity_check"] = {
            "gap_calendar_days": c["gap_calendar_days"],
            "gap_missing_trading_days": c["gap_missing_trading_days"],
            "no_data_before_end_date_at_all": c["no_data_before_end_date_at_all"],
            "gap_exceeds_threshold": c["gap_exceeds_threshold"],
            "threshold_trading_days": 100,
        }

        if c["gap_exceeds_threshold"]:
            if ticker == CAR_SPECIAL_CASE:
                tag = "ENTITY_DISCONTINUITY_CORPORATE"
                tag_reason = (
                    f"{tag}: boundary gap of {c['gap_missing_trading_days']} missing NYSE trading days "
                    f"({c['gap_calendar_days']} calendar days) spanning the real membership end_date "
                    f"({c['last_membership_end']}) coincides with Avis Budget Group's real 2006 spinoff "
                    "from Cendant Corporation -- a legitimate corporate event, not symbol-squatting, but "
                    "still two different entities under one symbol (pre-2006 rows belong to Cendant-era "
                    "CAR, post-2006 rows to Avis Budget Group). Momentum computed across that seam is "
                    "still meaningless. Potentially salvageable by truncation if repair work is ever "
                    "undertaken -- unlike a squatter series. Original identity-check reason retained: "
                    f"{old_reason}"
                )
            else:
                tag = "RECYCLED_SYMBOL"
                tag_reason = (
                    f"{tag}: boundary gap of {c['gap_missing_trading_days']} missing NYSE trading days "
                    f"({c['gap_calendar_days']} calendar days) spanning the real membership end_date "
                    f"({c['last_membership_end']}) -- price_data is not verified continuous with the "
                    "real S&P constituent; mechanism not independently confirmed (could be symbol-"
                    "squatting or an unverified corporate event). Original identity-check reason "
                    f"retained: {old_reason}"
                )
            write_data_quality_status(ticker, "QUARANTINED", tag_reason, json.dumps(merged_evidence))
            quarantined.append((ticker, tag, c["gap_missing_trading_days"]))
        else:
            write_data_quality_status(ticker, "NEEDS_REVIEW", old_reason, json.dumps(merged_evidence))
            stayed.append((ticker, c["gap_missing_trading_days"]))

    print(f"\nQUARANTINED via contiguity: {len(quarantined)}", flush=True)
    print(f"  ENTITY_DISCONTINUITY_CORPORATE: {sum(1 for _, t, _ in quarantined if t == 'ENTITY_DISCONTINUITY_CORPORATE')}", flush=True)
    print(f"  RECYCLED_SYMBOL: {sum(1 for _, t, _ in quarantined if t == 'RECYCLED_SYMBOL')}", flush=True)
    print(f"Stayed NEEDS_REVIEW (inconclusive): {len(stayed)}", flush=True)

    with engine.connect() as conn:
        final_counts = conn.execute(
            text("SELECT status, COUNT(*) FROM ticker_data_quality GROUP BY status")
        ).fetchall()
    print(f"\nFinal ticker_data_quality counts: {dict(final_counts)}", flush=True)

    pd.DataFrame(
        [{"ticker": t, "reason_tag": tag, "gap_missing_trading_days": g} for t, tag, g in quarantined]
    ).to_csv(f"{OUT_DIR}/step6_quarantined_via_contiguity.csv", index=False)
    pd.DataFrame(
        [{"ticker": t, "gap_missing_trading_days": g} for t, g in stayed]
    ).to_csv(f"{OUT_DIR}/step6_stayed_needs_review.csv", index=False)
    print(f"\nWrote step6_quarantined_via_contiguity.csv and step6_stayed_needs_review.csv", flush=True)


if __name__ == "__main__":
    main()
