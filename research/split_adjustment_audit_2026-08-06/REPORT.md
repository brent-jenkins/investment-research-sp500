# Split-adjustment corruption — detection, fix, and verification (2026-08-06)

## Headline

**No cliff artefact currently exists anywhere in `price_data`.** The mechanism
described in the task spec is real and was live in production code
(`pull_incremental()`'s rolling 10-day window silently discarding the
overlap signal that would have caught a retroactive restatement) — but it
has never actually fired, because no ticker in this system's tracked
universe has split since its own history was first ingested. **Status:
confirmed latent, not manifest** — this matches, and now has direct
evidence for, the claim already on record in `CLAUDE.md`.

The production fix (Task A2) ships regardless of this null result, per the
task spec's own instruction: the mechanism is real and will fire on the
first split of an already-ingested ticker, so the defect needed fixing
whether or not it had already caused damage. Task A3 (repair) has an empty
scope — there is nothing to repair.

## Verification evidence

Three independent checks, run against a **read-only, freshly `scp`'d
production database copy** (2026-08-06 19:03 UTC, `price_data`: 2,455,916
rows, 540 tickers, 2007-01-01 → 2026-08-05 — coverage/freshness confirmed
directly before running anything, not assumed).

**Step 1 — blanket consecutive-trading-day ratio scan.** For every ticker,
`close[t] / close[t-1]` computed across its own trading-day sequence,
flagged outside `[0.5, 2.0]`. **14 hits across 13 tickers**, out of the
entire 2.4M-row table.

**Step 2 — classify each hit against real yfinance `.splits`.** Each hit's
ticker's real split history was checked for a split whose ratio matches AND
whose date falls within a 30-calendar-day proximity window of the hit (the
plausible range for a 10-day-rolling-window-induced cliff, generously
padded). A first pass at this step had a real bug: it matched a hit against
*any* split anywhere in a ticker's full history regardless of temporal
distance, which spuriously classified 7 genuine, independently-documented
market events as "cliff artefacts" — APA/OXY's March 2020 COVID oil-price
crash matched against a 2004 2:1 split sixteen years earlier; GL's April
2024 Fuzzy Panda short-report crash matched an 1997 split; PCG's January
2019 bankruptcy-filing crash matched a 1983 split; MO's March 2008 Philip
Morris International spinoff matched a 1966 split; HIG's two October/
December 2008 financial-crisis moves matched a 1998 split. All seven are
real, independently verifiable market/corporate events, not corruption.
Fixed by requiring temporal proximity, not just a ratio match — result
after the fix: **13/14 hits classify as `real_market_move`, 1/14
(`EXPE`, 2011-12-21, a real 2:1 split reflected exactly 1 day after the
split date) as `genuine_split_correctly_reflected`. Zero cliff artefacts.**

**Step 3 — independent walk of every real split, regardless of step 1's
threshold.** Every ticker this system has ever known as a genuine S&P 500
constituent (`analysis.universe.get_all_known_stock_tickers()`, 1,205
tickers) was checked for real yfinance `.splits` events falling inside that
ticker's own DB coverage window, and the database's own close series was
read directly across each such date — this catches a split whose corruption
ratio would fall *inside* `[0.5, 2.0]` and therefore be invisible to step 1
(e.g. a 3:2 split, raw ratio 0.667, well inside the band). **296 real split
events found and checked. A properly split-adjusted series shows
`close_after / close_before` ≈ 1.0 at the split date (yfinance has already
retroactively rebased every prior Close onto the current post-split basis —
that is the whole mechanism this defect exploits, and also what makes a
*correctly* ingested series show no discontinuity at all). All 296 events
show `observed_ratio` within 15% of 1.0 (range 0.927–1.180); zero show a
ratio matching the raw, unadjusted split factor (which would be the actual
cliff-artefact signature).**

*(Note: this script's first run had an inverted label on this exact check —
`correctly_reflected` was defined as "ratio matches the raw split factor,"
backwards from the correct "ratio is smooth, near 1.0." This produced a
misleading "247 NOT correctly reflected" figure in the first pass, which on
inspection was actually 247 correctly-adjusted, artefact-free splits. Fixed
directly in `detect_cliffs.py` before this report was written; the
underlying `observed_ratio` numbers were correct throughout — only the
boolean label was wrong. Corrected CSV re-derived from the already-captured
data, no re-fetch from yfinance needed.)*

**Three independent methods, one answer: zero cliff artefacts in
production `price_data` today.**

## The fix (Task A2)

`data/ingestion.py:_find_retroactive_restatement(df, ticker, tolerance=0.01)`
— compares every incoming row's close against whatever is already stored
for the same `(ticker, date)`, for every date the incoming pull overlaps.
`pull_incremental()`'s existing rolling 10-day window already re-fetches
dates it has already stored — this signal was always present in every
incremental pull, it was just being discarded by the upsert instead of
checked first.

**Tolerance: 1% relative divergence.** Chosen to comfortably clear ordinary
vendor-revision noise (observed to be sub-cent rounding, well under 0.1% in
this dataset) while comfortably catching even the smallest common split
ratios (a 5:4 split changes the ratio by 20%, 3:2 by 33%, 2:1 by 50% — all
at least 20x the tolerance).

On divergence: `pull_incremental()` logs a loud `WARNING`-level message
naming the ticker, hit count, and an example date/values, then triggers a
full-history re-fetch (`start=history_start_date, end=now`) and replaces
via the existing `_upsert_price_rows()` upsert-on-`(ticker, date)`
semantics — this is safe and idempotent by construction, per that
function's own existing guarantee. Deliberately **not** gated on
yfinance's `.splits` metadata as the trigger (only used as corroboration
during detection/classification above) — the overlap comparison catches
any retroactive restatement: reverse splits, spin-off adjustments, vendor
corrections, not just an ordinary forward split.

## Task A3 — repair

**Empty scope.** No ticker was found corrupted by any of the three
detection methods above, so there is nothing to re-fetch and replace.

## Task A4 — verify

- **Detection re-run after the "repair"**: N/A (nothing was repaired); the
  three-method detection above already reflects the current, unmodified
  state of production `price_data`.
- **Regression test**: `tests/test_ingestion.py` — `_find_retroactive_restatement()`
  unit tests (empty overlap, within-tolerance, split-sized divergence,
  mixed overlapping/non-overlapping dates) and two end-to-end
  `pull_incremental()` tests: one simulating a full retroactive restatement
  (stored pre-split closes vs. an incoming post-split-basis 10-day pull)
  confirming a full-history re-fetch is triggered and the entire stored
  history ends up replaced on the new basis; one confirming the ordinary,
  no-restatement case still takes the cheap single-fetch path. All pass.
- **Recurring monitoring job**: `data/ingestion.py:detect_price_cliffs()`
  (the production version of step 1's scan, no yfinance calls — a backstop,
  not the primary defense) now runs every EOD job
  (`data/scheduler.py:_eod_job()`, own try/except, cannot block the rest of
  the job), and `output/briefing.py:send_price_cliff_alert()` emails on any
  hit using this project's standard content-based-dedup alert pattern
  (never re-sends for an unchanged hit set; a genuinely new/different hit
  sends immediately). Deliberately flags for manual review rather than
  attempting the yfinance-cross-referenced classification in the hot path —
  a network call per hit is too slow/fragile for a scheduled job, and the
  expected hit rate after this fix is near-zero.
- **`is_member()` / live exit logic for a repaired ticker**: N/A (nothing
  repaired). No interaction risk regardless — the fix operates entirely
  inside the ingestion layer, before `is_member()`/`analysis/decision.py`
  ever see the data; a full-history re-fetch changes *what* is stored, not
  *how* anything downstream reads or interprets `price_data`.

## Test suite / discipline

Full suite (`pytest`, project root): **913 passed**, 0 failed. Ran twice —
once for the new/modified tests in isolation, once for the complete suite
— both clean. One test-suite hygiene issue was found and fixed along the
way: several pre-existing `_eod_job()` tests don't mock `get_engine()` (an
existing gap predating this change, via the also-unmocked
`purge_old_pending_orders()` call), and this worktree happens to have a
real, freshly-copied 527MB production DB present locally — my first,
naive addition of `detect_price_cliffs()` (an O(2.4M-row) full-table scan)
to `_eod_job()` made that pre-existing gap 3x more expensive
(58s → 20s once fixed, against a ~19-20s unrelated baseline confirmed via
`git stash`), and briefly violated this project's own stated "no test
touches the real project database" invariant more materially than the
pre-existing gap already did. Fixed by explicitly mocking
`data.ingestion.detect_price_cliffs` in every affected test site (11
call sites + the shared `_mock_eod_dependencies()` helper).

**Deploy discipline (not yet done — awaiting confirmation):** this is a
production change touching the live ingestion path
(`data/ingestion.py:pull_incremental()`, called by the real `scheduled_eod`
job) and the scheduler (`data/scheduler.py:_eod_job()`). Per this project's
standing deploy practice: push to `main` triggers the test-gated GitHub
Actions → webhook → `deploy.sh` pipeline automatically, with its own
blackout-window deferral (`scheduled_eod`'s 21:00–22:00 UTC window is the
relevant one here, since that is the only job calling the changed code).
No manual `.backup`/SSH step is needed for this change specifically (no
schema change, no destructive data operation) — the existing webhook
pipeline's own `verify_deploy.sh` post-deploy check is the standard gate.
**Ingestion runtime impact**: the new per-ticker overlap check is a single
indexed `(ticker, date)` range query against already-open rows — negligible
per-ticker overhead on the common (no-restatement) path. A *triggered* full
re-fetch is materially more expensive than the normal 10-day pull (a full
`history_start_date`→now fetch instead of a 10-day one) but is expected to
be rare (at most once per ticker per split event, going forward) and is
logged loudly specifically so a rare, expensive event is visible rather
than silently absorbed into normal EOD runtime.

## Caveats

- This audit covers the **current, currently-tracked universe only**
  (`analysis.universe.get_all_known_stock_tickers()`, 1,205 tickers as of
  2026-08-06). A ticker that split during a gap in its own ingestion
  history (see `CLAUDE.md`'s "universe coverage remediation" — 701 of 1,204
  ever-known constituents have no `price_data` at all) cannot be checked
  for this defect, because there is no data to check.
- Step 3's `is_cliff_artefact` / `is_smooth` classification uses a ±15%
  band around 1.0 (smooth) or around the raw split ratio (artefact). This
  is deliberately generous — real single-day volatility around a
  split date is common — and no split event fell in the ambiguous middle
  ground between the two bands in this run, so the exact width was never
  load-bearing for today's null result.
- This confirms the *current* state is clean and that the fix prevents
  *future* recurrence. It says nothing about whether a past, now-corrected
  vendor-side data revision could have transiently affected historical
  backtest figures already published in `CLAUDE.md` before self-correcting
  — that class of question is out of scope for this investigation, which
  was specifically about the split-adjustment cliff mechanism.

## Files

- `research/split_adjustment_audit_2026-08-06/detect_cliffs.py` — the
  three-step detection script (kept for reproducibility, not deleted —
  contains the corrected classification logic).
- `step1_ratio_breaks_raw.csv` — all 14 raw ratio-break hits.
- `step2_classified_ratio_breaks.csv` — hits classified against real
  yfinance `.splits` history with the proximity-corrected logic.
- `step3_all_known_splits_checked.csv` — all 296 real split events checked
  directly against the database, with the corrected `is_cliff_artefact`/
  `correctly_reflected` columns.
- `run_log.txt` — full run output (including the yfinance "possibly
  delisted" warnings for the ~700 known-but-never-ingested/long-delisted
  tickers — expected, not an error; see the coverage caveat above).
- Code changes: `data/ingestion.py` (`_find_retroactive_restatement()`,
  wired into `pull_incremental()`; `detect_price_cliffs()`),
  `data/scheduler.py` (`_eod_job()` monitoring step),
  `output/briefing.py` (`send_price_cliff_alert()`),
  `analysis/signals.py` (Task B docstring correction).
- Test changes: `tests/test_ingestion.py`, `tests/test_briefing.py`,
  `tests/test_scheduler.py`.
