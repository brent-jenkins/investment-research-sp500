"""One-off catch-up pass to bring ticker_fundamentals to full roster coverage.

The nightly EOD rotation (data/ingestion.py:refresh_fundamentals_chunk, called
from _eod_job) is deliberately bounded at FUNDAMENTALS_CHUNK_SIZE tickers/night
so it can never grow into the kind of long unbroken loop that silently killed
the data-quality backstop for two days. That bound is correct for the steady
state and is NOT changed here. It does mean the first full pass over the roster
takes ~20 nights, and until it completes the nightly
candidate_fundamentals_snapshot links only the fraction of each night's
candidates that happen to fall in the covered alphabetical prefix. That part of
the timeline is artificial delay rather than inherent, and this script removes
it in one supervised run.

OPERATOR-DRIVEN, like prewarm_sector_data.py -- deliberately NOT wired into
data/scheduler.py. Nothing about the nightly rotation changes; once this has
run it simply finds nothing new to fetch and spends its budget refreshing the
oldest rows instead, which is its steady-state job anyway.

WHAT IT DOES NOT DO: it does not backfill. Every row written here is
provenance='point_in_time' because refresh_fundamentals_chunk() is the only
thing it calls. backfill_fundamentals_chunk() writes 'restated' rows, which can
never be pooled with forward rows in an analysis, and is a separate decision.

RATE LIMITING is inherited, not reimplemented: refresh_fundamentals_chunk()
already sleeps config["data"]["rate_limit_seconds"] between every ticker. At the
measured ~3.66s/ticker a full remaining roster is tens of minutes of continuous
fetching. There is deliberately no parallelism and no reduced throttle -- the
yfinance rate limiter has bitten this project before, and the cost of tripping
it (a long backoff, or a block) is far higher than the cost of waiting.

TERMINATION -- the non-obvious part, and the reason this is not a for-loop.
"Repeat until never_fetched_remaining == 0" DOES NOT TERMINATE. A ticker that
returns no data has no row written, so it stays never-fetched forever and sorts
to the front of the next chunk's queue. BF.B and BRK.B are exactly this case
already: this system stores the dotted form while yfinance expects BF-B/BRK-B,
so both have been consuming 2 of the 25 nightly slots every night since the
recorder shipped (visible directly in the fetch cadence: 25, 25, then 23/night).
The loop therefore stops when a chunk fails to SHRINK the never-fetched set,
which is correct for any permanently-unresolvable ticker rather than just these
two, and reports the survivors by name instead of spinning on them.

BLACKOUT: a new chunk is never started inside the EOD/membership window
(21:00-22:00 UTC) or the order-submission window (14:15-14:45 UTC). The first is
the only other thing in this system that fetches from yfinance, so overlapping
it is the one genuine contention risk; the second is when real orders are
placed. The script waits and re-checks rather than aborting, so it is safe to
start and leave running.

Usage (intended: on the server, against the real database -- see below):
    python3.11 catchup_fundamentals.py
    python3.11 catchup_fundamentals.py --max-chunks 4     # bounded trial run

WHERE TO RUN IT. On the SERVER. The output of this script is a database
MUTATION, not a computed result, and a script run locally writes only to the
local copy -- the gap that left CLAUDE.md asserting a data-quality state
production did not have for a full day (see "A research script whose output is a
database mutation needs an explicit, separate production-apply step"). Running
it where the real database lives removes the transfer step entirely rather than
adding a second thing to verify.
"""
import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# (start_hhmm, end_hhmm) UTC, inclusive of start, exclusive of end.
_BLACKOUT_WINDOWS = (
    ((21, 0), (22, 0)),      # membership_refresh 21:15 + scheduled_eod 21:30 -- the only other yfinance caller
    ((14, 15), (14, 45)),    # stock_order_submission 14:30 -- real orders
)
_BLACKOUT_POLL_SECONDS = 60


def _in_blackout(now: datetime | None = None) -> str | None:
    """Return a human label for the active blackout window, or None."""
    now = now or datetime.now(timezone.utc)
    hm = (now.hour, now.minute)
    for start, end in _BLACKOUT_WINDOWS:
        if start <= hm < end:
            return f"{start[0]:02d}:{start[1]:02d}-{end[0]:02d}:{end[1]:02d} UTC"
    return None


def _never_fetched(config: dict) -> list[str]:
    """The live-pool tickers with no ticker_fundamentals row of any provenance.

    Deliberately the same definition refresh_fundamentals_chunk() uses for its
    own queue, so progress measured here and work done there cannot disagree.
    """
    from sqlalchemy import text

    from analysis import universe
    from data.database import get_engine

    pool = sorted(universe.get_live_stock_candidates())
    with get_engine().connect() as conn:
        seen = {r[0] for r in conn.execute(text(
            "SELECT DISTINCT ticker FROM ticker_fundamentals"))}
    return [t for t in pool if t not in seen]


def catchup_fundamentals(config: dict | None = None, max_chunks: int = 100,
                         chunk_size: int | None = None, verbose: bool = True) -> dict:
    """Run refresh_fundamentals_chunk() until the roster stops shrinking.

    Kept separate from the __main__ guard so it can be called directly. Never
    raises: refresh_fundamentals_chunk() already returns its own error rather
    than propagating, and this loop stops on the first one it sees.
    """
    from data.ingestion import (FUNDAMENTALS_CHUNK_SIZE, _load_config,
                                refresh_fundamentals_chunk)

    config = config or _load_config()
    chunk_size = chunk_size or FUNDAMENTALS_CHUNK_SIZE

    remaining = _never_fetched(config)
    result = {
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "never_fetched_at_start": len(remaining),
        "chunks_run": 0, "attempted": 0, "written": 0, "no_data": 0,
        "newly_covered": 0, "stalled": False, "error": None,
        "unresolvable": [], "blackout_waits": 0, "elapsed_seconds": 0.0,
    }
    if verbose:
        print(f"[catchup] {result['never_fetched_at_start']} never-fetched tickers at start "
              f"(chunk size {chunk_size})", file=sys.stderr, flush=True)

    t0 = time.time()
    while remaining and result["chunks_run"] < max_chunks:
        while (window := _in_blackout()) is not None:
            result["blackout_waits"] += 1
            if verbose:
                print(f"[catchup] inside blackout window {window} -- waiting "
                      f"{_BLACKOUT_POLL_SECONDS}s before the next chunk",
                      file=sys.stderr, flush=True)
            time.sleep(_BLACKOUT_POLL_SECONDS)

        before = len(remaining)
        res = refresh_fundamentals_chunk(config, chunk_size=chunk_size)
        result["chunks_run"] += 1
        result["attempted"] += res.get("attempted", 0)
        result["written"] += res.get("written", 0)
        result["no_data"] += res.get("no_data", 0)

        if res.get("error"):
            result["error"] = res["error"]
            if verbose:
                print(f"[catchup] chunk {result['chunks_run']} returned an error, stopping: "
                      f"{res['error']}", file=sys.stderr, flush=True)
            break

        remaining = _never_fetched(config)
        gained = before - len(remaining)
        result["newly_covered"] += gained
        if verbose:
            print(f"[catchup] chunk {result['chunks_run']:>3}: attempted "
                  f"{res.get('attempted', 0)}, written {res.get('written', 0)}, "
                  f"no_data {res.get('no_data', 0)} | never-fetched {before} -> "
                  f"{len(remaining)} (+{gained} covered) | "
                  f"{round(time.time() - t0)}s elapsed", file=sys.stderr, flush=True)

        if gained == 0:
            # Every ticker still in the queue returned no data and therefore
            # wrote no row, so it will be selected again forever. Stop rather
            # than spin -- and name them, because a permanently-unresolvable
            # ticker silently consuming a nightly slot is the thing worth
            # knowing about.
            result["stalled"] = True
            result["unresolvable"] = list(remaining)
            if verbose:
                print(f"[catchup] chunk {result['chunks_run']} covered nothing new -- the "
                      f"{len(remaining)} remaining ticker(s) return no data and would repeat "
                      f"forever. Stopping.", file=sys.stderr, flush=True)
            break

    result["never_fetched_remaining"] = len(remaining)
    result["elapsed_seconds"] = round(time.time() - t0, 1)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-chunks", type=int, default=100,
                    help="safety cap on chunks (default 100)")
    ap.add_argument("--chunk-size", type=int, default=None,
                    help="tickers per chunk (default FUNDAMENTALS_CHUNK_SIZE)")
    args = ap.parse_args()

    r = catchup_fundamentals(max_chunks=args.max_chunks, chunk_size=args.chunk_size)
    print(
        f"[catchup] DONE in {r['elapsed_seconds']}s: {r['chunks_run']} chunks, "
        f"{r['attempted']} attempted, {r['written']} rows written, {r['no_data']} no-data, "
        f"{r['newly_covered']} newly covered "
        f"({r['never_fetched_at_start']} -> {r['never_fetched_remaining']} never-fetched).",
        file=sys.stderr,
    )
    if r["unresolvable"]:
        print(f"[catchup] permanently unresolvable ({len(r['unresolvable'])}): "
              f"{', '.join(r['unresolvable'])}", file=sys.stderr)
    if r["error"]:
        print(f"[catchup] ERROR: {r['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
