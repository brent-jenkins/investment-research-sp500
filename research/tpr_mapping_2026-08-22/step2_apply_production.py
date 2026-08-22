"""Write the ONE hand-verified instrument_resolutions row for TPR.

Runs ON THE PRODUCTION SERVER, against the real database, as an explicit and
separate apply step -- per CLAUDE.md's standing requirement that a script whose
output is a database mutation has a SIDE EFFECT, not just a result, and that
"it ran and printed success" does not distinguish a local run from an applied
one. Verification is a separate script (step3_verify_production.py) that
re-queries on a fresh connection rather than trusting this script's own output.

WHAT IS AND IS NOT CHANGED
  * Exactly one row: instrument_resolutions WHERE ticker='TPR'.
  * resolve_instrument_candidate() is NOT modified. At 1 failure in 503, a
    stricter rule that fails visibly beats a looser one that maps wrongly --
    the XLF/XLK MAPPING_CORRECTION is what the looser version cost.
  * No config change, no trading-logic change, no other row touched.

THE VALUES ARE TAKEN FROM T212's OWN METADATA, not assumed:
    ticker      TPR_EQ_US     (NOT TPR_US_EQ -- the suffix segments are
                               transposed; this is the whole reason the
                               automatic match failed)
    currency    USD           (the instrument's own currencyCode field; the
                               sleeve study found instrument currency and
                               account currency differ, so this is read, not
                               inferred from the GBP account)
    shortName   TPR
    name        Tapestry
    isin        US8760301072

SAFETY: refuses to write unless the existing row is exactly the expected
FAILED row, so a re-run after any change to the row is a no-op that reports
rather than silently overwriting.
"""
import sqlite3
import sys
from datetime import datetime

DB = "/root/investment-research/database/investment_research.db"
TICKER = "TPR"
INSTRUMENT_ID = "TPR_EQ_US"
CURRENCY = "USD"
NOTE = (
    "manually resolved 2026-08-22 -- verified by hand against a live "
    "get_instruments() pull. Automatic resolution FAILED because T212's "
    "instrument id for Tapestry is TPR_EQ_US, with the suffix segments "
    "transposed relative to the {TICKER}_US_EQ convention: the guess "
    "TPR_US_EQ does not exist, and resolve_instrument_candidate()'s "
    "fallback search filters on a _US_EQ suffix BEFORE comparing shortName, "
    "so the one instrument whose shortName really is 'TPR' never entered the "
    "candidate set. Exactly 3 of 17,403 instruments use the transposed form "
    "(TPR, ELF, LFUS -- all added 2019-06-26), so the resolver rule was left "
    "unchanged. Unambiguous: exactly one instrument has shortName == TPR "
    "(TPR_EQ_US, USD, STOCK, ISIN US8760301072). A second Tapestry listing "
    "shares that ISIN (COYd_EQ, EUR, shortName COY) but is a European line, "
    "not a competing match. See CLAUDE.md 'TPR instrument mapping'."
)
EXPECTED_EXISTING_NOTE = "no US-listed equity instrument found with shortName == TPR"


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    before = conn.execute(
        "SELECT ticker, t212_instrument_id, currency, status, note FROM "
        "instrument_resolutions WHERE ticker = ?", (TICKER,)).fetchone()

    if before is None:
        sys.exit(f"ABORT: no instrument_resolutions row for {TICKER} -- expected a FAILED row.")
    print("BEFORE:", dict(before))

    if before["status"] != "FAILED" or before["note"] != EXPECTED_EXISTING_NOTE:
        sys.exit(
            "ABORT: the existing row is not the expected FAILED row this fix was "
            "investigated against. Re-investigate rather than overwriting.\n"
            f"  status={before['status']!r} note={before['note']!r}")

    counts_before = dict(conn.execute(
        "SELECT status, COUNT(*) FROM instrument_resolutions GROUP BY status").fetchall())
    print("COUNTS BEFORE:", counts_before)

    now = datetime.utcnow().isoformat()
    with conn:
        cur = conn.execute(
            "UPDATE instrument_resolutions "
            "SET t212_instrument_id = ?, currency = ?, status = 'VERIFIED', "
            "    note = ?, resolved_at = ? "
            "WHERE ticker = ? AND status = 'FAILED'",
            (INSTRUMENT_ID, CURRENCY, NOTE, now, TICKER))
    print("ROWS UPDATED:", cur.rowcount)
    if cur.rowcount != 1:
        sys.exit(f"ABORT: expected to update exactly 1 row, updated {cur.rowcount}.")

    print("\nApply step complete. This script's own output is NOT the verification --"
          "\nrun step3_verify_production.py on a fresh connection.")


if __name__ == "__main__":
    main()
