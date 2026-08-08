# Monthly-shortlist admission redesign — results (2026-08-08)

**Verdict: NOT ADOPTED.** Two of the pre-registration's five PASS conditions fail — the deployment
guard and the per-decision corroboration check — either one alone is sufficient per the
pre-registered walk-away sentence. No code changes to `signals.py`, `config.yaml`, `decision.py`,
or the live path. Nothing merged, nothing deployed. Full pre-registration:
`PREREGISTRATION.md` in this directory — read that first for the exact criteria; this document
reports against it, not around it.

**This report also documents a real, unplanned detour.** The pre-registered sanity gate (§7)
failed on its first run — not because of a bug in this study, but because it caught a genuine,
confirmed production defect: the data-quality identity-verification backstop had never completed
successfully since it was deployed, leaving 463 tickers (including every megacap) completely
unscreened, and the one screening pass that had ever run (this study's own, mid-investigation) had
its own domain-scoping bug that wrongly quarantined `^GSPC` and every ETF ticker. Both are now
fixed, tested, deployed, and independently verified on production. See "Detour" below for the full
record — it is not a footnote to this study, it's a real finding in its own right.

---

## Environment

Pool 1: `get_live_stock_candidates()`, 503 tickers (ranking-direction check only, per §3 —
survivorship-biased, absolute figures not decisive). Pool 2 CLEAN: `get_research_stock_candidates()`
∩ `ticker_data_quality.status == 'CLEAN'`, **612 tickers** (up from the pre-registration's assumed
220 — see "Detour"; 608 had usable price frames). Both pools rebuilt fresh against the corrected,
fully-screened database (confirmed identical on local and production — see "Detour"). All four
runs used `max_total_deployed=0.70`, `per_trade_pct=0.05`, `atr_stop_multiplier=2.0` (unchanged),
same cost template (0.30% FX round-trip + idle-cash credit at the mean `^IRX` rate, 1.5266%/yr over
the window) for both arms.

**Sanity gate, re-baselined.** The pre-registration's original reference (4,922 trades /
£114,990.04 / 7-16 blocks, from `trades_pool2_final_A.csv`) is now confirmed **stale** — it was
computed against a database snapshot from a since-deleted worktree that, it turns out, also never
had `VTRS` (a genuine, currently-CLEAN ticker) screened. The fresh run against the corrected,
fully-screened database reproduces **4,922 trades / £115,363.15 / 7/16 blocks** — this is now the
reference for full Pool 2 going forward. Trade count is identical; only which specific trades fill
capacity differs (the `VTRS`-inclusion effect, via the same capacity-cascade mechanism CLAUDE.md's
"resolution floor" section already documents). This number also happens to exactly match a prior,
independently-reported figure in CLAUDE.md's own "worked example" — direct evidence that finding
was *also* computed against a `VTRS`-unscreened database state, now explained rather than left as
an unexplained coincidence.

**Degenerate-case parity, confirmed before any real run was trusted.** `redesign_engine.py`'s
`simulate_with_panel(rebalance_frequency="daily", shortlist_size=None)` reproduced the real,
unmodified `simulate_portfolio_compounding_mtm()`'s own Pool 1 output **byte-identical** — 4,749
trades, £240,360.75, 0 row-level mismatches across every field.

---

## Results

| | Pool 1 baseline | Pool 1 redesign | Pool 2 CLEAN baseline | Pool 2 CLEAN redesign |
|---|---|---|---|---|
| Trades | 4,749 | 2,825 | 4,916 | 2,872 |
| Frictionless CAGR | 9.92% | 6.41% | 6.19% | 5.14% |
| Cost-adjusted CAGR | 7.141% | 5.306% | 3.405% | 4.042% |
| Peak-relative max DD | −41.94% | −20.56% | −46.28% | −22.82% |
| Mean deployed % | 63.46% | 39.46% | 63.72% | 39.36% |
| Ratio (cost-adj CAGR / \|DD\|) | 0.1703 | 0.2581 | 0.0736 | 0.1771 |
| 16-block pass (Pool 2 CLEAN only) | — | — | 7/16 | 10/16 |

**On the surface, the redesign looks like a clear win**: drawdown roughly halves in both pools,
the Ratio improves 1.5–2.4x, and in Pool 2 CLEAN cost-adjusted CAGR is even slightly higher. This
is exactly the shape of result the pre-registration's five-condition gate — not a single headline
number — was built to stress-test before anyone got to react to it.

## The five PASS conditions, evaluated (Pool 2 CLEAN, the decisive pool; Pool 1 reported alongside for direction)

| # | Condition | Pool 2 CLEAN | Pool 1 (direction check) |
|---|---|---|---|
| 1 | Ratio ≥ 1.20× baseline | **PASS** (2.407x) | **PASS** (1.516x) |
| 2 | DD not worse than baseline+8pp | **PASS** (DD improves, doesn't worsen) | **PASS** |
| 3 | Cost-adj CAGR ≥ baseline−1.5pp | **PASS** (4.042 ≥ 1.905 floor) | **FAIL** (5.306 < 5.641 floor) |
| 4 | Mean deployed ≥ baseline−15pp | **FAIL** (39.36 vs. 48.72 floor, gap −24.4pp) | **FAIL** (gap −24.0pp) |
| 5 | Per-decision corroboration doesn't contradict portfolio-level direction | **FAIL** (see below) | **FAIL** (see below) |

**Both pools agree on the failure**, not just the decisive one — Pool 1 fails condition 3 as well
as 4 and 5, making its own case even less ambiguous than Pool 2 CLEAN's.

### Condition 4 — the deployment guard fired exactly as designed

Redesign's mean deployment is **~24 percentage points lower** than baseline's in both pools
(39.4% vs. 63.5–63.7%) — comfortably past the 15pp floor. This is the wait-for-rebalance cash
design feature (§5) showing up exactly where the pre-registration predicted it might: a large share
of the drawdown improvement and Ratio gain is consistent with the redesign simply holding
dramatically more cash on average, not with better per-trade decisions. This alone does not prove
the redesign adds no value — but it means the headline Ratio number cannot be trusted without the
next check.

### Condition 5 — the per-decision instrument contradicts the portfolio-level headline

The literal exact-`(ticker, entry_date)` paired comparison was, as anticipated in §4, **not
usable** — 142–143 shared pairs in both pools, below the pre-registered 200-pair floor, reported
and not interpreted.

The **month-level adaptation** (§4's primary instrument for this study) *is* usable, on a real
sample (1,703–1,770 differing outcomes per pool, comparable in size to the stop A/B validation's
own 2,444-pair uncapped sample):

| Pool | Shared ticker-months | Differing outcomes | Redesign win rate | Mean pnl_pct diff (redesign − baseline) |
|---|---|---|---|---|
| Pool 1 | 1,809 | 1,703 | **50.2%** | **−0.36pp** |
| Pool 2 CLEAN | 1,870 | 1,770 | **50.1%** | **−0.18pp** |

**A coin flip, slightly negative, in both pools.** When both arms independently held the same
ticker around the same time, the redesign's own realized outcome was not better than baseline's —
if anything, marginally worse. This is the same shape of finding the measurement toolkit's stop
A/B validation already produced this same day: a large, attractive portfolio-level gap (here:
better Ratio, better CAGR, much better drawdown) that does not survive contact with a
capacity/cascade-uncontaminated, per-decision view. **The portfolio-level headline is not a lie —
the redesign's account-level path genuinely did draw down less and earn a better risk-adjusted
return over this specific 19-year run — but the evidence does not support attributing that to
better selection.** It is far more consistent with a combination of (a) holding more cash
(condition 4) and (b) a different, lower-turnover admission path compounding differently through
19 years of capacity-cascade-sensitive sequencing (per CLAUDE.md's own repeatedly-confirmed
sensitivity to exactly this kind of change) than with the redesign systematically picking better
trades.

## Corroborating only — block-pass count and top-decile overlap

**Block-pass** (Pool 2 CLEAN only, per §1): baseline 7/16, redesign 10/16 — a **+3 delta**, sitting
right at the edge of the measured **3-block resolution floor** from the 2026-08-08 empirical
noise-floor run. Per the pre-registration's own instruction, this is reported as context, not as
independent evidence — it neither strengthens nor rescues the verdict above, which rests on the
five PASS conditions, not on this number.

**Top-decile ticker overlap** (Jaccard, ticker-identity only, per §6): 0.590 (Pool 1), 0.570 (Pool 2
CLEAN) — roughly 57–59% overlap between each arm's biggest winners. Moderate, not extreme: the two
arms are not simply holding an unrelated set of lottery tickets (a low-overlap read would have
undermined the whole comparison per Part 1/6's concentration findings), but a real ~40% of each
arm's top-decile names are unique to that arm — consistent with, not contradicting, the capacity-
cascade explanation above.

## Verdict, stated against the pre-registered walk-away sentence

> *"I will not adopt this redesign if it worsens Pool 2 CLEAN drawdown by 8pp or more for any CAGR
> gain, or gives back more than 1.5pp of cost-adjusted CAGR to buy a drawdown improvement, or if it
> earns a flattering ratio mainly by holding more cash (mean deployment falling more than 15pp
> below baseline's), or if the per-decision paired/distributional instrument contradicts the
> portfolio-level headline's direction."*

The third and fourth clauses both fire. **Not adopted.** The redesign is not shown to be a better
selection mechanism — it is shown to produce a smoother equity curve substantially by holding more
cash and trading less often, with no confirmed per-decision quality improvement once capacity-
cascade effects are controlled for. This is a real, useful, negative result, arrived at exactly the
way the pre-registration was built to arrive at it: by a criterion written down before any number
existed, not by post-hoc argument about which of several attractive-looking numbers to trust.

**What this does not close.** Per §0's revised scope, this arm deliberately tested batching +
shortlist size only, not selective admission (holding a slot empty when nothing clears a quality
bar). Since the mechanism that *did* pass here (Ratio, CAGR, drawdown) is now understood to be
substantially a cash-holding/compounding-path artifact rather than a selection improvement, a
follow-up arm testing genuine selective admission would need its own pre-registration and, per this
result, an explicit plan for isolating selection quality from deployment level from the start
(e.g. holding mean deployment roughly constant between arms, or reporting the Ratio only alongside
condition-4-style deployment parity) — not assumed to inherit a clean bill of health from this run.

---

## Detour: the data-quality backstop was silently broken, and the identity check had a scoping bug

Not part of the admission-redesign question, but found directly while pre-registered sanity-gate
work was in progress, confirmed rather than assumed at every step, fixed, tested, deployed, and
independently re-verified before this study's own real runs resumed.

**What was found.** The sanity gate's first run failed on P&L only (trades and block-pass matched
exactly) — traced to one extra ticker, `VTRS`, present in a freshly-built Pool 2 cache but absent
from the reference. `VTRS` had full 2007–2026 price history and real, current index membership, but
**zero row in `ticker_data_quality`** — neither `CLEAN`, `QUARANTINED`, nor `NEEDS_REVIEW`. A direct
count (`price_data` tickers with no `ticker_data_quality` row) found **463**, not 1 — including
`AAPL`, `MSFT`, `GOOGL`, `AMZN`, `TSLA`, `NVDA`, and every ETF — confirmed identical (463) on both
the local research copy and production via SSH, ruling out a local-only artifact.

**Root cause, confirmed via `journalctl`, not inferred.** `scan_unverified_tickers()` (the intended
ongoing backstop) had never once logged either success or failure in the full month of retained
production logs — across both EOD cycles since it was deployed (2026-08-06, 2026-08-07). At this
project's standard 2.5s/ticker rate limit, a 463-ticker backlog is a ~19-minute unbroken yfinance
loop inside the EOD job — long enough to not survive whatever is causing the scheduler service's
own frequent restarts, with the process most likely killed via signal rather than raising a
catchable exception (consistent with zero warning logs despite `logger.warning` demonstrably
working elsewhere in the same job).

**A second, independent bug surfaced once the backlog was actually run for the first time.**
`verify_ticker_identity()`'s Check A can only ever affirm `quote_type == "EQUITY"` — an ETF, an FX
pair, or an index-level series fails it by construction, not as a corner case. `scan_unverified_
tickers()`'s SQL had no stock restriction, so the first successful run wrote `QUARANTINED` for
`^GSPC` (the crisis-trigger reference series itself) and ~33 ETF tickers. Inert today only because
nothing currently reads quarantine status on those paths — a property of today's callers, not of
the data. Separately, `IAC` exposed a real logic bug: a gracefully-handled yfinance 404 (empty dict,
no raised exception) was scored identically to a real, populated, confirmed-different-identity
response, auto-quarantining on zero evidence.

**Three fixes shipped** (`data/ingestion.py`, `data/scheduler.py`, commit `0a170ca`, 953 tests
passing): (1) `scan_unverified_tickers()` scoped to `analysis.universe.get_all_known_stock_tickers()`
only. (2) Chunked at 50 tickers/call (~2min at the rate limit), self-resuming with no new progress
table (a screened ticker drops out of the next call's own unscreened-ticker query). (3) Explicit
completion logging on both the success path (even when `checked=0`) and the failure path
(`data_quality_scan_error` now populated in the EOD completion record, previously silently absent
either way). `verify_ticker_identity()`'s empty-response case now routes to `NEEDS_REVIEW`, narrowly
— confirmed directly, not assumed, that the already-validated `PALM`/`PDG`/`RX` populated-but-
degenerate-response cases still quarantine unchanged.

**Applied and independently verified, local and production, both matching exactly**: 36 non-stock
rows deleted, `IAC` re-screened, the remaining 427 previously-unscreened stock tickers screened
under the fixed code. Final state, confirmed via direct SQL query (not either script's own summary)
on both databases: **612 CLEAN / 35 NEEDS_REVIEW / 188 QUARANTINED / 835 total**, zero non-stock
rows remaining, all six spot-checked megacaps `CLEAN`. The override for genuine departed-but-still-
real companies checked directly, not assumed: `SGP` (SpyGlass Pharma, a real different equity) →
`NEEDS_REVIEW`; `MER`/`CAM`/`PCL`/`SPLS` (all resolve to real but *non-equity* replacement products —
a fund or ETF, genuine recycled-symbol cases, not the FNMA pattern) → correctly `QUARANTINED`.

**Not yet closed**: the *scheduled* path (the EOD job's own chunked call) has not been observed
completing successfully even once — today's application was a manual SSH invocation, which proves
the code is correct but not that it survives a real EOD cycle. Monday 2026-08-10 (the next NYSE
trading day; today is Saturday, so no EOD job fires regardless) is the first opportunity to check
for `data_quality_checked` in that day's real EOD completion record. Recorded as an explicit open
item below, not assumed closed by today's manual run.

---

## Meta-lessons for CLAUDE.md (recorded here, added to that file separately)

1. **A P&L reference figure carries an implicit database-state dependency that continuous
   ingestion makes decay** — an exact-total sanity gate will eventually fail for a benign reason
   (new data arrived), and that failure is not distinguishable from a real bug without a trade-set
   diff. Pin references to a snapshot, or design gates to compare trade sets/counts rather than a
   single total, from the start.
2. **A backstop that has not been *observed* completing successfully isn't a backstop, no matter
   how reasonable its own code looks.** This one had a correct-looking design, a clean docstring,
   and its own try/except isolation — and still silently never ran to completion across every
   opportunity it had, for two full days, with nothing anywhere flagging the gap until an unrelated
   sanity gate happened to trip over one of its symptoms.
3. **A check's validity domain must match its actual input population, not the population it was
   designed against.** `verify_ticker_identity()` was correct for the stock-recycled-symbol problem
   it was built and validated for; feeding it every ticker in `price_data` (not just stocks) wasn't
   a conservative extension, it was applying a domain-specific test outside its domain, and it
   silently mis-scored the crisis-trigger series itself the first time it got the chance to run.

## Files

`PREREGISTRATION.md` (the criteria, unmodified since approval except the two agreed edits made
before any run), `redesign_engine.py`, `step0_build_pool2_caches.py`, `step1_sanity_gate.py`,
`step2_degenerate_case_parity.py`, `step3_run_arms.py`, `step4_instruments.py`,
`step4_all_results.json`, `trades_{pool1,pool2_clean}_{baseline,redesign}.csv`,
`panel_{pool1,pool2_clean}_{baseline,redesign}.csv`.
