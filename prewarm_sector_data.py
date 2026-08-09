"""Pre-warm ticker_sector for the full live candidate pool -- run once after
deploying sector-concentration monitoring, and safe to re-run at any time
(already-cached tickers are skipped, no re-fetch, no sleep spent on them).

Added 2026-08-09 alongside the sector-concentration feature specifically so
the Dashboard/weekly-email render path (output/performance.py:
compute_sector_breakdown()) never has to fetch from yfinance itself -- that
path is READ-ONLY against ticker_sector by design (see that function's own
docstring); this script is the only place a bulk fetch happens. Covers the
full live candidate pool (analysis.universe.get_live_stock_candidates(),
~500 tickers), not just today's ~20 open positions, so a future entry into
a name that isn't currently held is already covered by the time it opens --
today's holdings alone would leave every *other* live candidate a first-time
synchronous-looking gap the moment it's first held (closed only after that
position's own lazy... except there is no lazy fetch left in the render path
at all post-2026-08-09 -- an un-prewarmed new holding now displays
"Uncategorized" until the next pre-warm run, never a stall).

Throttled at config["data"]["rate_limit_seconds"] (2.5s, the project standard)
between GENUINE fetches only -- a rerun where most tickers are already
cached completes in seconds, not the full ~500-ticker runtime. Per-ticker
fetch failures never raise (data.ingestion.fetch_ticker_sector() already
catches everything internally); a defensive try/except around the DB write
here too, consistent with this project's belt-and-suspenders convention for
backstop scripts (see data/ingestion.py:scan_unverified_tickers()). A row is
written for every genuine attempt, resolved or not, so an unresolvable
ticker is never re-queried on a future re-run either.

Does NOT add a scheduled/recurring job -- deliberately out of scope per the
same judgement CLAUDE.md's "Sector concentration monitoring" section
already recorded (more infrastructure than a reporting-only feature at this
position count needs); this is a one-off (or manually re-run) operational
script, invoked over SSH, not wired into data/scheduler.py.

Usage:
    python prewarm_sector_data.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def prewarm_sector_data(config: dict | None = None) -> dict:
    """Returns {"total", "already_cached", "fetched", "resolved",
    "unresolved", "errors", "elapsed_seconds"}. Kept separate from the
    __main__ guard so tests can call this directly."""
    from analysis.universe import get_live_stock_candidates
    from data.database import get_ticker_sector, write_ticker_sector
    from data.ingestion import _load_config, fetch_ticker_sector

    config = config or _load_config()
    rate_limit = config["data"]["rate_limit_seconds"]

    tickers = sorted(get_live_stock_candidates())
    result = {
        "total": len(tickers), "already_cached": 0, "fetched": 0,
        "resolved": 0, "unresolved": 0, "errors": [],
    }
    t0 = time.time()
    for ticker in tickers:
        cached = get_ticker_sector(ticker)
        if cached is not None:
            result["already_cached"] += 1
            continue
        try:
            sector = fetch_ticker_sector(ticker)  # never raises internally
            write_ticker_sector(ticker, sector)
            result["fetched"] += 1
            if sector:
                result["resolved"] += 1
            else:
                result["unresolved"] += 1
        except Exception as exc:
            result["errors"].append(f"{ticker}: {exc}")
        time.sleep(rate_limit)  # only spent on a genuine fetch attempt, never on a cache hit

    result["elapsed_seconds"] = round(time.time() - t0, 1)
    return result


def main() -> int:
    result = prewarm_sector_data()
    print(
        f"Sector pre-warm complete in {result['elapsed_seconds']}s: "
        f"{result['total']} live candidates, {result['already_cached']} already cached, "
        f"{result['fetched']} newly fetched ({result['resolved']} resolved, "
        f"{result['unresolved']} unresolved), {len(result['errors'])} errors.",
        file=sys.stderr,
    )
    if result["errors"]:
        for e in result["errors"][:20]:
            print(f"  error: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
