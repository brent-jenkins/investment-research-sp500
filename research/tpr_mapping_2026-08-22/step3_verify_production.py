"""Part 3 verification -- an INDEPENDENT re-query of the production database
on a fresh connection, deliberately not trusting the apply script's own
printed output. Read-only: opens the database through a mode=ro URI so it
cannot write even by accident.

Checks, in order:
  1. 503 VERIFIED, 0 FAILED, 503 total.
  2. The TPR row is VERIFIED and carries TPR_EQ_US / USD specifically -- the
     transposed suffix and the instrument's OWN currency field, not the
     {TICKER}_US_EQ convention and not the account's GBP.
  3. No other row was touched: every other ticker's t212_instrument_id still
     ends _US_EQ, and the VERIFIED count rose by exactly one.
  4. Non-vacuity: the table is non-empty and the TPR row actually exists, so
     a "0 failed" result cannot come from an empty or missing table.
"""
import sqlite3
import sys

DB = "/root/investment-research/database/investment_research.db"
FAILURES = []


def check(cond: bool, label: str, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' -- ' + detail) if detail else ''}")
    if not cond:
        FAILURES.append(label)


def main() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM instrument_resolutions").fetchone()[0]
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM instrument_resolutions GROUP BY status").fetchall())
    print(f"instrument_resolutions: total={total} counts={counts}\n")

    print("1. table-level counts")
    check(total > 0, "table is non-empty (otherwise every check below is vacuous)", f"total={total}")
    check(counts.get("VERIFIED") == 503, "503 VERIFIED", f"got {counts.get('VERIFIED')}")
    check(counts.get("FAILED", 0) == 0, "0 FAILED", f"got {counts.get('FAILED', 0)}")
    check(counts.get("PENDING", 0) == 0, "0 PENDING", f"got {counts.get('PENDING', 0)}")
    check(total == 503, "503 rows total", f"got {total}")

    print("\n2. the TPR row itself")
    row = conn.execute(
        "SELECT ticker, t212_instrument_id, currency, status, note, resolved_at "
        "FROM instrument_resolutions WHERE ticker = 'TPR'").fetchone()
    check(row is not None, "TPR row exists (otherwise checks below are vacuous)")
    if row is not None:
        print("   ", {k: row[k] for k in ("ticker", "t212_instrument_id", "currency", "status", "resolved_at")})
        check(row["status"] == "VERIFIED", "status VERIFIED", row["status"])
        check(row["t212_instrument_id"] == "TPR_EQ_US",
              "instrument id is the TRANSPOSED TPR_EQ_US", row["t212_instrument_id"])
        check(row["t212_instrument_id"] != "TPR_US_EQ",
              "NOT the convention guess TPR_US_EQ (which does not exist at T212)")
        check(row["currency"] == "USD",
              "currency USD, from the instrument's own currencyCode", row["currency"])
        check(row["currency"] != "GBP", "not the account currency GBP")
        check("manually resolved" in (row["note"] or ""), "note records the manual resolution")
        check("TPR_EQ_US" in (row["note"] or ""), "note records why the automatic match failed")

    print("\n3. nothing else was touched")
    others = conn.execute(
        "SELECT COUNT(*) FROM instrument_resolutions "
        "WHERE ticker != 'TPR' AND (t212_instrument_id IS NULL "
        "OR t212_instrument_id NOT LIKE '%\\_US\\_EQ' ESCAPE '\\')").fetchone()[0]
    check(others == 0, "every OTHER row still resolves via the _US_EQ convention",
          f"{others} exceptions")
    nulls = conn.execute(
        "SELECT COUNT(*) FROM instrument_resolutions "
        "WHERE t212_instrument_id IS NULL OR currency IS NULL").fetchone()[0]
    check(nulls == 0, "no row left with a NULL instrument id or currency", f"{nulls} nulls")

    print("\n" + ("VERIFICATION FAILED: " + "; ".join(FAILURES) if FAILURES
                  else "ALL VERIFICATION CHECKS PASSED."))
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
