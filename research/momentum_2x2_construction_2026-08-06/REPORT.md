# Momentum construction 2×2 factorial: dividend-adjustment × skip-month (2026-08-06)

## Headline

**Neither hypothesis clears the adoption bar. Nothing is adopted from this
task, per its own scope — this section states what the evidence shows, not
a recommendation to ship anything.**

- **Dividend hypothesis (arm 1 vs arm 2, price return vs total return):
  no meaningful effect.** Mean CAGR delta +0.25pp (5.10% → 5.35%), arm 2
  wins only 6/16 blocks, paired t-test p=0.76. In the dominant era
  (2010-2019) arm 2 is actually *worse* than arm 1 (6.38% vs 7.14%).
  Sector-mix evidence directly **contradicts** the hypothesis's own
  proposed mechanism: switching to dividend-inclusive ranking should
  *reduce* Technology's outsized profit concentration if price-return
  ranking were the cause — instead Technology's net-profit share *rises*,
  from 70.6% (arm 1) to 77.3% (arm 2).
- **Skip-month hypothesis (arm 1 vs arm 3, 12-0 vs 12-1): a small,
  borderline-significant improvement, not a strong one.** Mean CAGR delta
  +1.68pp (5.10% → 6.78%), arm 3 wins 12/16 blocks, paired t-test p=0.069
  (misses conventional 0.05 significance). Decile monotonicity is not
  restored (see below) — the improvement, such as it is, is not
  accompanied by the mechanism-level evidence that would make it
  convincing.
- **Interaction (arm 4, both changes combined) is the strongest of the
  four** (+2.13pp vs arm 1, 13/16 blocks, p=0.012) — per the task's own
  pre-specification this is reported, not grounds for adoption on its own,
  and per the adoption bar it would need to be "clearly, exceptionally
  strong" against baseline **and** improved standing against a properly
  re-solved leg C, neither of which this result is.
- **All four arms underperform the passive benchmarks by a wide margin**
  in this reset-at-boundary comparison (mean CAGR 5.10-7.23% vs 8.38-11.94%
  for legA/legC/legD's total-return variants, win rate 2-3/16 against
  legA_tr for every arm) — see the "leg benchmark comparison — not fully
  re-solved" caveat below for why this comparison is approximate, not
  final.
- **Momentum decile monotonicity is not restored by any arm.** All four
  show near-zero, noisy Pearson correlations (0.003 to 0.070) and, notably,
  *negative* Spearman correlation in every single arm (-0.048 to -0.067) —
  if anything slightly worse for the two arms with the best headline CAGR
  (arm 3: -0.060, arm 4: -0.067) than for the baseline (arm 1: -0.051).
  This is the clearest single piece of evidence in this study: neither
  hypothesis's proposed fix produces a cleaner-ranking momentum signal,
  which undercuts trusting either headline CAGR number as a genuine
  construction improvement rather than noise.

**"No meaningful difference" is the honest characterization of the
dividend hypothesis. The skip-month hypothesis is "a small, inconclusive
lean toward better" — not nothing, but well short of what the task's own
adoption bar requires, and not supported by the decile evidence.**

## A real, unplanned finding along the way: a second, distinct data-corruption class

Before any of the above could be trusted, block 3 (2010-09-03→2011-11-24)
produced CAGR readings of **+779% (arm 3) and +11,250% (arm 4)** against a
sane -12.26% for the untouched production baseline computed the same way.
Traced directly (`diag_block3.py`, `results/diag_block3_full.log`): the
cause was **not** the split-adjustment cliff Task A fixed. `TIE`'s stored
price history alternates almost day-to-day between normal values
(~$8-28) and wild spikes (~$15,990-28,200) — erratic, interleaved garbage,
not a single clean step the way a real split-adjustment cliff would look.

Scoped with a full-database ratio-break scan: **2,747 hits across 68
tickers** in the current database (834 tickers) — up from 13 tickers/14
hits when Task A1 audited a smaller, 540-ticker snapshot a few hours
earlier (the database has grown via other, concurrent ingestion work in
between). Two of the newly-appearing names, `NCC` and `CFC`, are tickers
CLAUDE.md's own coverage-remediation section already names as confirmed
cases of "a different, unrelated company under the same reused symbol" —
this is that exact, previously-flagged risk, now manifest in real data.

Classified by hit-count magnitude (a real stock's genuine trading history
essentially never produces double-digit-to-hundreds of >2×/<0.5× single-day
moves; the 13 tickers Task A1 already verified as genuine market moves,
plus three more spot-checked here — GME's real Jan-2021 squeeze, RX's real
decade-spread moves, FMCC's real 2008 GSE-conservatorship crash — all show
1-4 hits each): **20 tickers show an unambiguous corruption signature**
(18-568 hits each) and were excluded from this study's universe
(`CORRUPTED_TICKERS` in `run_arms.py`). A further ~10 tickers showed mixed
reversion evidence (some hits revert to the pre-spike level within 10
trading days — corruption; some don't — plausibly genuine) and were
deliberately left in rather than excluded on weaker evidence.

**Live-trading exposure: none.** Checked directly against the same
database: zero of the 68 flagged tickers are current S&P 500 members, and
`invest_orders` has zero rows for any of them, ever. This is a
research/backtest-data-integrity issue, not a live-money one — but it is a
**second, distinct root cause** from Task A's split-adjustment bug (a
brand-new ticker's first-ever full-history fetch has nothing to compare
against, so Task A's overlap-based restatement check does not, and cannot,
catch it), and is a real gap worth its own triage. See
`results/full_db_ratio_breaks.csv` for the full scan.

## Verification evidence

**Preconditions (per task spec, checked before running anything):**
- Task A's fix (`_find_retroactive_restatement()`) and Task B's corrected
  docstrings are present in the code this study ran against (worktree
  based on origin/main `4b7e644`).
- `adj_close` coverage: 0 rows null/non-positive (of 3,331,647), and
  `close != adj_close` on 77.9% of rows — re-derived directly in this
  study, not just cited from the earlier pre-check. The "if equal
  everywhere the arm is void" failure condition does not hold.
- Staleness guard (`_MAX_ANCHOR_GAP_DAYS`) for the shifted 12-1 anchor:
  **identical NaN count across all 4 arms** — 162,040/2,832,480 (5.7208%)
  — confirming the guard correctly spans the full 252-row window
  regardless of where inside that window the numerator (`t` or `t-21`)
  sits, exactly as `arms.py`'s own reasoning predicts. No change to
  report; this was the expected, correct outcome.

**Isolation constraint (adjusted prices for signal only, never
execution) — proven empirically, not just architecturally.** For every
pair of trades sharing the same (ticker, block, entry date) — i.e. both
arms made the identical admission decision — across arm1↔arm2 and
arm3↔arm4:
- **`entry_price` is 100.0000% identical** in every case (1,969 and 2,072
  common-decision trades respectively).
- **`exit_price` is 100.0000% identical** among the subset that *also*
  share the same `exit_date` and `exit_reason` (1,949/1,969 and
  2,043/2,072 respectively).
- The remaining ~1% of common-entry trades diverge on exit_date/reason —
  traced directly (sample in the working notes): every case is a
  legitimately different `SIGNAL_OFF` timing, because the two arms'
  momentum signals genuinely differ for the same ticker. This is expected,
  correct behaviour, not a leak — a stock's signal turning off on a
  different date under a different basis is exactly what the study is
  testing for, and it correctly cascades into a different exit.
- **Conclusion: no adjusted-price leakage into execution anywhere in this
  study.** Entry/exit fills, stops, and ATR all used real, raw prices in
  every arm, confirmed on real output, not just by code inspection.

**Decision-engine parity — evidence gathered, not assumed.** `arms.py`'s
`precompute_ticker_arm()` calls `analysis.volatility._atr()` directly (the
same production function, unmodified) and only substitutes the
`momentum`/`signal` columns; `run_arms.py` calls
`analysis.backtest.simulate_portfolio_compounding_mtm()` — the real,
unmodified production engine — for every arm, meaning entry gating, exit
priority, VaR sizing, and admission/capacity logic are the *same function
calls* production and every other validated backtest in this codebase use,
not a re-derived parallel implementation. `verify_arm1_identity()`
(`arms.py`) asserts arm 1's `precompute_ticker_arm()` output is
byte-identical, column-by-column, to production's own
`precompute_ticker()` — this is the direct evidence requirement CLAUDE.md's
"Research scripts must verify decision-engine parity" section calls for,
not merely confirming the right functions are *named* in the script.

**Arm 1 parity/reproduction gate — passed, with one explained, benign
exception.** Arm 1 (today's exact production construction), run against
the legacy 503-ticker universe reconstruction of `research/
block_benchmark_comparison_2026-08-06`'s own database, reproduces that
study's published 16-block results **byte-identically on 15/16 blocks**
(CAGR, trade count, and peak-relative drawdown all match to the reported
precision). Block 8 diverges (258 vs. 259 trades, CAGR 10.24% vs. 9.10%) —
traced to an **already-independently-diagnosed, benign cause**: another
investigation earlier tonight (`research/block8_reproduction_test_2026-08-06/`,
a different session) ran the prior study's own unmodified script against
this checkout's current database and got **258 trades, CAGR 10.24%** —
exactly matching this study's figure, not the original published one. Their
conclusion (confirmed independently by this study reaching the same
number via a completely different, freshly-written script) is that the two
investigations' databases genuinely differ (this database has grown since
the original study ran), not that the shared decision engine is
non-deterministic. **Gate treated as passed**: the reproduction is exact
everywhere the underlying data is unchanged, and the one place it isn't
has an independently-corroborated, non-methodological explanation.

## Conventions (stated explicitly, per CLAUDE.md's standing requirement)

- **Reset-at-boundary**, not continuous walk — this is a cross-variant
  comparison (which momentum construction is better), which is the
  reset-at-boundary case per CLAUDE.md's "Two backtest conventions"
  section. £50,000 capital per block, every arm, every block.
- **Engine**: `simulate_portfolio_compounding_mtm()` — mark-to-market
  capacity tracking, momentum-ranked priority admission, the permanent
  realistic intraday-touch `STOP_LOSS` fill (2026-08-06 default).
- **16 blocks**, `make_blocks(2007-01-01, 2026-08-03, n_blocks=16)` — the
  exact, unmodified production block-boundary function, reused verbatim
  from `research/block_benchmark_comparison_2026-08-06`'s own convention,
  not re-invented.
- **Both drawdown conventions** reported: peak-relative
  (`compute_peak_relative_drawdown_pct`) and fixed-denominator
  (`compute_metrics_fixed()["max_drawdown_pct"]`).
- **Universe**: today's live, point-in-time-correct S&P 500 membership per
  block end (`analysis.universe.get_live_stock_candidates`), minus the 20
  confirmed-corrupted tickers (see above).

## Per-block results (all 4 arms)

| blk | era | arm1 CAGR | arm2 CAGR | arm3 CAGR | arm4 CAGR | arm1 ddPk | arm2 ddPk | arm3 ddPk | arm4 ddPk | arm1 n | arm2 n | arm3 n | arm4 n |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | GFC | -9.45 | -9.90 | -6.91 | -6.74 | -12.96 | -13.12 | -11.02 | -11.02 | 73 | 72 | 69 | 69 |
| 1 | GFC | -30.20 | -30.51 | -20.56 | -20.93 | -40.70 | -40.63 | -32.38 | -34.04 | 325 | 351 | 354 | 370 |
| 2 | bull | 12.63 | 15.17 | 12.68 | 14.89 | -12.35 | -10.91 | -11.24 | -11.69 | 322 | 318 | 355 | 335 |
| 3 | bull | 1.85 | 0.78 | -2.14 | 1.97 | -12.30 | -11.81 | -12.06 | -11.30 | 265 | 279 | 265 | 271 |
| 4 | bull | 21.89 | 17.53 | 18.20 | 18.79 | -2.91 | -3.61 | -3.19 | -4.08 | 233 | 239 | 241 | 238 |
| 5 | bull | 12.24 | 7.02 | 10.86 | 11.27 | -6.34 | -5.11 | -5.88 | -5.15 | 276 | 270 | 261 | 266 |
| 6 | bull | 7.54 | 5.38 | 8.58 | 8.02 | -5.77 | -5.19 | -6.08 | -6.17 | 274 | 281 | 265 | 281 |
| 7 | bull | -0.96 | -2.10 | 3.13 | 0.27 | -11.77 | -11.03 | -11.28 | -10.27 | 291 | 292 | 276 | 281 |
| 8 | bull | 6.28 | 10.17 | 8.49 | 11.05 | -5.00 | -2.86 | -3.34 | -2.89 | 255 | 253 | 252 | 258 |
| 9 | bull | -1.85 | -5.05 | 1.08 | 0.22 | -14.30 | -16.32 | -16.90 | -14.68 | 294 | 299 | 282 | 285 |
| 10 | bull | 4.61 | 8.55 | 8.41 | 5.61 | -10.09 | -8.61 | -8.61 | -10.97 | 257 | 249 | 265 | 271 |
| 11 | recent | 10.77 | 10.68 | 11.08 | 10.73 | -5.40 | -6.50 | -5.80 | -4.97 | 273 | 278 | 274 | 274 |
| 12 | recent | 2.55 | 5.76 | 6.47 | 7.65 | -10.34 | -7.99 | -10.38 | -7.25 | 316 | 302 | 304 | 289 |
| 13 | recent | 9.59 | 8.52 | 13.59 | 11.36 | -6.35 | -6.59 | -7.04 | -7.62 | 258 | 257 | 255 | 253 |
| 14 | recent | 6.81 | 10.23 | 10.06 | 12.81 | -15.85 | -15.80 | -14.78 | -14.90 | 296 | 291 | 286 | 290 |
| 15 | recent | 27.30 | 33.38 | 25.47 | 28.74 | -9.65 | -11.91 | -9.75 | -11.07 | 284 | 296 | 295 | 296 |

Total trades: arm1 4,292 · arm2 4,327 · arm3 4,299 · arm4 4,327.

## Era decomposition (dominant era reported separately)

| Era | arm1 mean CAGR | arm2 | arm3 | arm4 |
|---|---|---|---|---|
| 2007-2009 GFC (2 blocks) | -19.82% | -20.21% | -13.73% | -13.84% |
| **2010-2019 bull (9 blocks, dominant)** | **7.14%** | **6.38%** | **7.70%** | **8.01%** |
| 2020-2026 recent (5 blocks) | 11.40% | 13.71% | 13.33% | 14.26% |

In the dominant era specifically, arm 2 (dividend/total-return) is *worse*
than baseline — the only era where this reverses is GFC, where all arms
struggled but the 12-1 arms lost meaningfully less (-13.7% vs -19.8-20.2%),
plausibly because skipping the most recent month reduces exposure to
short-term reversal whipsaws during acute crisis volatility. This is
noted as a plausible mechanism, not confirmed causally.

## Leg-benchmark comparison — not fully re-solved, stated as a limitation

The task calls for leg C **re-solved per arm**. This was not completed
within the time available for this study — the comparison below reuses
`research/block_benchmark_comparison_2026-08-06`'s own frozen leg A/C/D
`_tr` (total-return) figures unchanged, which is an apples-to-baseline
comparison, not a true per-arm re-solve (leg C's own weighting mechanism
depends on the strategy's realised drawdown, which differs per arm).
**Treat the numbers below as directionally informative, not as a rigorous
per-arm leg C comparison**:

| Arm | mean CAGR | beats legA_tr (11.94%) | beats legC_tr real (8.50%) | beats legD_tr (8.38%) |
|---|---|---|---|---|
| 1 | 5.10% | 2/16 | 6/16 | 4/16 |
| 2 | 5.35% | 2/16 | 5/16 | 3/16 |
| 3 | 6.78% | 3/16 | 7/16 | 5/16 |
| 4 | 7.23% | 3/16 | 6/16 | 5/16 |

All four arms underperform all three passive references on average, in
this reset-at-boundary convention. This is a materially different
question from "does the live, continuously-compounding production system
beat buy-and-hold" (a continuous-walk question CLAUDE.md answers
elsewhere, favourably) — the two conventions are not comparable, per
CLAUDE.md's own standing rule, and this result says nothing about that
separate, already-answered question.

## Momentum decile analysis

| Arm | Pearson | Spearman | n |
|---|---|---|---|
| 1 (baseline) | 0.0300 | -0.0512 | 4,292 |
| 2 (adj_close) | 0.0705 | -0.0484 | 4,327 |
| 3 (12-1) | 0.0430 | -0.0600 | 4,299 |
| 4 (adj_close, 12-1) | 0.0035 | -0.0665 | 4,327 |

No arm restores monotonicity. All four Spearman correlations are negative
and all are small in magnitude — arm 1's figures here (Pearson 0.030,
Spearman -0.051) are in the same near-zero, negative-Spearman regime as
the task's own cited reference (Pearson 0.057, Spearman -0.033 on a
different, larger trade population), confirming this study's methodology
reproduces the same qualitative shape rather than an artifact of a
different measurement approach. Notably, the two arms with the best
headline CAGR (3 and 4) have the *worst* Spearman correlation of the
four — headline CAGR and rank-quality point in different directions here,
which is itself evidence against trusting either headline number as a
genuine construction improvement.

## Sector mix per arm

| Arm | Technology trade share | Technology NET profit share |
|---|---|---|
| 1 (baseline) | 22.1% | 70.6% |
| 2 (adj_close) | 21.9% | **77.3%** (higher, not lower) |
| 3 (12-1) | 21.5% | 58.1% |
| 4 (adj_close, 12-1) | 21.4% | 55.0% |

Sector matched for 82.5-83.7% of trades (via a pre-existing ticker→sector
mapping from `research/full_trade_export_enriched_2026-08-06/`, not
independently re-derived here). **This directly weighs against the
dividend hypothesis's own proposed mechanism**: switching to
dividend-inclusive (total-return) ranking should *reduce* Technology's
outsized profit concentration if price-return ranking penalizing dividend
payers were the cause of that concentration — instead it *increases*
(70.6%→77.3%). The skip-month change (12-0→12-1, independent of basis)
does measurably reduce Technology's concentration (70.6%→58.1%,
77.3%→55.0%), which is at least directionally consistent with *some* real
effect from the skip-month construction, separate from and not explained
by the dividend hypothesis.

## Capacity-turnover split

Trade counts differ modestly across arms (4,292 / 4,327 / 4,299 / 4,327) —
a ~0.8% spread, not a large admission effect. This was not decomposed
further into "direct re-ranking effect" vs. "admission/capacity effect"
components within the time available for this study — a genuine gap
against the task's own reporting requirement, noted here rather than
glossed over. Given the small overall spread, a large hidden
capacity-turnover effect is unlikely to be masking the headline
comparisons, but this is inference from the aggregate, not a direct
measurement.

## Universe coverage caveat

Per CLAUDE.md's standing note: universe coverage carries a permanent,
unfixable survivorship bias (dominant-era, 2010-2019, usable coverage
64.3%), which flatters every arm's absolute figures roughly equally. It
does not invalidate the cross-arm comparisons above (all four arms share
the identical, now-additionally-corruption-filtered universe at every
block), but every absolute CAGR/drawdown figure in this report should be
read as optimistic in absolute terms, same as every other backtest figure
in this codebase.

## Measured vs. estimated

**Measured directly, this run**: all per-block/per-arm metrics, the
isolation proof, the arm-1 parity check, the NaN/staleness-guard
comparison, the decile correlations, the sector-share figures, the
corrupted-ticker classification and its live-trading-exposure check.
**Estimated / reused, not re-derived**: the leg A/C/D benchmark figures
(frozen from the prior study, not re-solved per arm — see caveat above);
the ticker→sector mapping (reused from a pre-existing enrichment export,
not independently rebuilt); the capacity-turnover decomposition (not
completed, noted as a gap).

## Adoption bar assessment

Neither hypothesis clears "clearly, exceptionally strong against baseline
**and** improved standing against a properly re-solved leg C." The
dividend hypothesis fails outright (no effect, contradicted by its own
proposed sector-mix mechanism). The skip-month hypothesis shows a small,
borderline-significant CAGR improvement not corroborated by decile
evidence — the honest characterization is "not proven," not "shown and
withheld pending a higher bar." **Nothing is adopted, consistent with
this task's scope regardless of outcome.**

## Caveats

- Leg C not re-solved per arm (see above) — the strongest single
  limitation on this report's benchmark comparison.
- Capacity-turnover direct-vs-admission decomposition not completed.
- Sector mapping covers 82.5-83.7% of trades, reused rather than
  independently rebuilt; unmatched trades are excluded from the sector
  tables entirely rather than bucketed as "unknown."
- The ~10 borderline-corruption tickers (3-9 ratio-break hits, mixed
  reversion evidence) were left in the universe on weaker evidence than
  the 20 excluded ones — a real, if likely small, residual data-quality
  risk in every arm's figures (shared equally across arms, so unlikely to
  bias the cross-arm comparison, but not zero).
- This entire study answers a cross-variant question (reset-at-boundary
  convention) — it says nothing about whether the live, continuously
  compounding production system beats passive benchmarks in realistic,
  single-account terms (a separate, continuous-walk question already
  answered favourably elsewhere in this codebase).

## Files

- `arms.py` — 2×2 arm definitions, exact formulas, isolation-constraint
  documentation, `verify_arm1_identity()`.
- `run_arms.py` — the 16-block × 4-arm sweep driver, including the
  `CORRUPTED_TICKERS` exclusion and its full justification.
- `diag_block3.py`, `results/diag_block3_full.log` — the block-3
  corruption root-cause trace.
- `results/full_db_ratio_breaks.csv` — the full 68-ticker corruption scan.
- `results/arm{1,2,3,4}_blocks.json`, `results/arm{1,2,3,4}_trades.csv` —
  full per-block metrics and full trade-level output, arm-tagged, for all
  four live-universe arms.
- `results/arm1_legacy_blocks.json`, `results/arm1_legacy_trades.csv` —
  the legacy-universe reproduction run used for the parity gate.
- `parity_check.py` — decision-engine parity / isolation-proof script
  (partially superseded by the isolation checks run directly against the
  trade CSVs above; kept for reference).

Nothing in this directory has been committed or pushed, per the task's own
requirement — left for manual review.
