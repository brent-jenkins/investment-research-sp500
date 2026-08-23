"""OUTCOME verification for the yfinance-boundary normalisation.

A test asserting that _yf_symbol("BRK.B") == "BRK-B", or that a MOCKED
yf.Ticker received "BRK-B", proves ROUTING and not OUTCOME -- per the standing
sub-shape, a test that mocks the mechanism it claims to verify can only confirm
the mock. This script makes REAL yfinance calls through the REAL production
functions and checks that data actually comes back.

Read-only: calls the fetchers only, never write_ticker_fundamentals() or
write_ticker_sector(), so it mutates nothing.

Run before and after the fix to see the difference:
    git stash && python research/ticker_format_2026-08-23/verify_outcome.py
    git stash pop && python research/ticker_format_2026-08-23/verify_outcome.py
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

DOTTED = ["BF.B", "BRK.B"]
CONTROL = ["AAPL"]          # must be unaffected by the change
RATE = 2.5


def main() -> int:
    from data.ingestion import (fetch_ticker_fundamentals,
                                fetch_ticker_fundamentals_history,
                                fetch_ticker_sector)

    out = {}
    for tk in DOTTED + CONTROL:
        row = {}
        sector = fetch_ticker_sector(tk)
        row["sector"] = sector
        time.sleep(RATE)

        rec = fetch_ticker_fundamentals(tk)
        row["fundamentals"] = None if rec is None else {
            "reporting_period": rec["reporting_period"],
            "statement_type": rec["statement_type"],
            "non_null_fields": sorted(k for k, v in rec["values"].items() if v is not None),
        }
        time.sleep(RATE)

        hist = fetch_ticker_fundamentals_history(tk, max_quarters=4)
        row["history_quarters"] = [h["reporting_period"] for h in hist]
        time.sleep(RATE)

        row["PASS"] = bool(sector) and rec is not None and len(hist) > 0
        out[tk] = row
        print(f"{tk:<8} sector={sector!r:<24} "
              f"fundamentals={'OK ' + rec['reporting_period'] if rec else 'NONE'!s:<20} "
              f"history={len(hist)} quarters   -> {'PASS' if row['PASS'] else 'FAIL'}",
              flush=True)

    (HERE / "verify_outcome.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    failed = [t for t, r in out.items() if not r["PASS"]]
    print()
    if failed:
        print(f"FAILED for: {', '.join(failed)}")
        return 1
    print("All tickers returned real data from all three fetchers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
