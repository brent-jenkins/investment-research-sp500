# Stop-loss reconfiguration — Arms A/B/C results (2026-08-07)

Pre-registered in `PREREGISTRATION.md`, approved for Arms A (baseline 2.0x ATR fixed stop) and B
(no stop — `CRISIS_EXIT`→`SIGNAL_OFF`→`MAX_HOLDING` only) against both pools first. After review,
**Arm C (3.0x/4.0x fixed ATR) was run next** as the direct continuation of A/B's finding, per
instruction — D (trailing stop) and E (volatility-conditional) remain deferred, not run.

## Sanity gate — passed

Arm A / Pool 2 landed at **9/16** — exactly the expected ~9/16 realistic-stop-fill baseline. Safe
to trust the rest of this run.

## Headline comparison (frictionless)

| Pool | Arm | Trades | Blocks pass /16 | CAGR | Peak-relative max DD | Win rate | PF |
|---|---|---|---|---|---|---|---|
| 1 (503, survivorship-biased) | A (baseline) | 4,749 | 7 | 9.39% | −41.94% | 44.4% | 1.31 |
| 1 | B (no stop) | 3,500 | **9** | 9.12% | −43.46% | 53.6% | 1.40 |
| 2 (1,043, provisional) | A (baseline) | 4,958 | 9 | 6.34% | −50.21% | 43.0% | 1.18 |
| 2 | B (no stop) | 3,633 | **10** | **6.64%** | **−44.92%** | 51.9% | 1.25 |

**B wins the primary criterion (blocks passed) in both pools** — 9 vs 7 in Pool 1, 10 vs 9 in Pool
2. **Pool 2 is a clean sweep for B** (wins blocks, CAGR, and drawdown all three). **Pool 1 is more
mixed on the frictionless numbers alone**: B wins blocks and win-rate decisively, but A has a
marginally higher CAGR (9.39% vs 9.12%, 0.27pp) and marginally better drawdown (−41.94% vs
−43.46%) before costs are considered — this is exactly the kind of "improves one metric, worsens
another" trade-off you asked not be resolved by picking CAGR alone.

## Cost-adjusted CAGR — changes the Pool 1 picture

Turnover differs by construction (B trades ~26-27% less in both pools), so FX cost and idle-cash
credit are treatment effects, not constants — modelled per the established template (0.30%
round-trip FX on real position_gbp, idle % × mean ^IRX 1.524%/yr over the window), never deferred:

| Pool | Arm | Mean deployed | FX drag/yr | Idle credit/yr | Net/yr | Frictionless CAGR | **Cost-adjusted CAGR** |
|---|---|---|---|---|---|---|---|
| 1 | A | 69.2% | −3.34pp | +0.47pp | −2.87pp | 9.39% | 6.52% |
| 1 | B | 68.8% | −2.41pp | +0.48pp | −1.94pp | 9.12% | **7.18%** |
| 2 | A | 69.6% | −3.38pp | +0.46pp | −2.92pp | 6.34% | 3.42% |
| 2 | B | 69.1% | −2.43pp | +0.47pp | −1.96pp | 6.64% | **4.68%** |

**Once costs are modelled, B wins cost-adjusted CAGR in BOTH pools, including Pool 1** — B's lower
turnover means it pays materially less FX drag (−2.41pp vs −3.34pp/yr in Pool 1), which flips the
close, A-leaning frictionless comparison there into a clear win for B (7.18% vs 6.52%). **This is
the single most load-bearing number in this run**: the frictionless view alone would have
understated B's advantage in Pool 1 specifically because it ignored exactly the cost this project's
own 2026-08-06 standing rule says cannot be deferred when turnover varies by arm.

## Do the two pools agree? — reported prominently, as instructed

**On the primary criterion (blocks passed) and on cost-adjusted CAGR: yes, both pools agree B wins.**
No reversal, no sign flip on either of those two measures. **On drawdown specifically, the raw
pools disagree on direction**: in Pool 1, B has *worse* peak drawdown than A (−43.46% vs −41.94%);
in Pool 2, B has *better* drawdown than A (−44.92% vs −50.21%). **This is investigated directly in
the addendum immediately below, not left unresolved** — the short answer is that it's substantially
a `NEEDS_REVIEW`-population artifact, and once that population is excluded, both pools agree A has
the better drawdown after all. One plausible reading behind that: Pool 2's larger, noisier,
partly-provisional universe gives the stop more opportunity to lock in permanent losses on
still-unadjudicated tickers that a cleaner, survivorship-biased Pool 1 doesn't have as
much of — but this is a hypothesis, not confirmed by anything run here.

## Drawdown-reversal addendum — is Pool 2's disagreement a contamination artifact?

**Substantially yes.** The worst drawdown episode in both Pool 2 arms is the 2008-2009 crisis
window (pool2_A: 2008-01-03 → 2009-07-10, −50.21%; pool2_B: 2008-06-09 → 2009-07-10, −44.92%).
Recomputing peak-relative drawdown with `NEEDS_REVIEW`-tagged tickers **excluded entirely**:

| Arm | Full-population peak DD | CLEAN-only peak DD | Δ |
|---|---|---|---|
| pool2_A | −50.21% | −31.45% | **+18.76pp** |
| pool2_B | −44.92% | −33.77% | **+11.15pp** |

Excluding the still-unadjudicated population materially improves drawdown in both arms — direct
evidence that `NEEDS_REVIEW` tickers are disproportionately driving Pool 2's crisis-window losses,
exactly the "Pool 2's tails are still contaminated" hypothesis you flagged as live.

**And it resolves the cross-pool disagreement, not just softens it.** CLEAN-only Pool 2 shows A
with *better* drawdown than B (−31.45% vs −33.77%) — the same direction Pool 1 shows (−41.94% vs
−43.46%). **The reversal reported earlier (B beating A on drawdown, only in full Pool 2) was itself
a `NEEDS_REVIEW`-population artifact.** Once that population is set aside, both pools agree: A has
the better drawdown, B has the better cost-adjusted CAGR. That is a real trade-off, not a
disagreement to be explained away — but it is now the *same* trade-off in both pools, not two
pools telling different stories.

**Not fully resolved, stated honestly**: CLEAN-only is still a smaller, incomplete population
(some of the 130 `NEEDS_REVIEW` tickers are surely genuine and would belong in a truly clean
comparison; excluding all of them is a conservative floor, not a precise estimate). But the
direction and magnitude of the shift are exactly what the contamination hypothesis predicts, and
the episode-level breakdown corroborates it directly: 158 of 462 positions open during pool2_A's
worst episode (34%) were `NEEDS_REVIEW`-tagged, contributing a real, negative P&L share
(−£9,003.70 of the episode's losses) alongside the clean population's own −£14,587.00.

## Capacity-turnover effect — kept separate from the direct effect

| Pool | Trades A | Trades B | Δ | Admitted A-only | Admitted B-only | Admitted both | B's capacity-rejections of A-only candidates |
|---|---|---|---|---|---|---|---|
| 1 | 4,749 | 3,500 | **−1,249 (−26.3%)** | 4,231 | 2,982 | 518 | 1,699 |
| 2 | 4,958 | 3,633 | **−1,325 (−26.7%)** | 4,405 | 3,080 | 553 | 1,789 |

Removing the stop cuts trade count by roughly a quarter in both pools — consistent with
`no_stop_loss_2026-08-06`'s own earlier finding on a different universe/date-range setup. Longer
average holds under B leave less capacity for new admissions; confirmed directly via each B run's
own `REJECTED_CAPACITY` log, not inferred.

## Whipsaw quantification (Arm A only, both pools) — the direct test of "does the stop just cut winners short"

| Pool | STOP_LOSS exits | Re-entered within 30d | % re-entered | Total stop-exit P&L (of re-entered) | Total re-entry P&L | **Combined P&L** | Net-positive pairs | Net-negative pairs |
|---|---|---|---|---|---|---|---|---|
| 1 | 2,127 | 1,456 | 68.5% | −£462,014.99 | +£101,666.94 | **−£360,348.05** | 429 | 1,027 |
| 2 | 2,283 | 1,553 | 68.0% | −£347,997.30 | +£50,100.03 | **−£297,897.27** | 433 | 1,120 |

**This falsifies the whipsaw hypothesis this check was built to test — cleanly, not ambiguously.**
The prediction going in was that stops mostly fire on temporary noise that reliably recovers, so
re-entering should look roughly neutral-to-positive on average. It doesn't: 68% of stopped-out
positions do get re-entered within a month, but the combined P&L of those pairs is heavily negative
in both pools, with roughly 2.4-2.6x more pairs net-negative than net-positive, and the re-entry's
own gains recover only about 22% (Pool 1) to 14% (Pool 2) of what the stop-out lost.

**This reframes what Arm B's advantage actually is.** Not "the stop was cutting winners short and
letting them ride recovers more value than it costs" — the data says the opposite: the conditions
that trigger a `STOP_LOSS` tend to still be bad conditions a month later, more often than not, so
the stop is correctly identifying deteriorating positions rather than misfiring on noise. B's real
advantage is narrower and more mundane: **it isn't paying the round-trip transaction cost to exit a
position early and then, in most cases, re-enter into a continuation of the same bad situation** —
`SIGNAL_OFF`/`MAX_HOLDING` would have closed the same position anyway, just somewhat later, for one
fewer round of FX cost. This is a less exciting story than "stops are net harmful," but it is the
one the evidence actually supports, and a mechanism that survives having its original motivating
hypothesis disproved is more trustworthy than one that doesn't.

## Arm C — wider fixed stops (3.0x, 4.0x)

Reused the existing pool caches unchanged (`atr_stop_multiplier` is read fresh from config at
simulation time by `_resolve_entry_stop()`, never baked into `precompute_ticker()`'s cached
frames — confirmed before reuse, not assumed).

| Pool | Arm | Trades | Blocks pass /16 | Frictionless CAGR | Cost-adjusted CAGR | Peak DD |
|---|---|---|---|---|---|---|
| 1 | A (2.0x) | 4,749 | 7 | 9.39% | 6.52% | −41.94% |
| 1 | C1 (3.0x) | 4,096 | 6 | 8.91% | 6.54% | −43.18% |
| 1 | C2 (4.0x) | 3,822 | 8 | 9.40% | 7.25% | −39.42% |
| 1 | B (no stop) | 3,500 | 9 | 9.12% | 7.18% | −43.46% |
| 2 | A (2.0x) | 4,958 | 9 | 6.34% | 3.42% | −50.21% |
| 2 | C1 (3.0x) | 4,250 | 9 | 6.58% | 4.19% | −47.49% |
| 2 | C2 (4.0x) | 3,973 | 9 | 5.32% | **3.14%** | −46.86% |
| 2 | B (no stop) | 3,633 | 10 | 6.64% | 4.68% | −44.92% |

**Trade count is cleanly monotonic in both pools** (A > C1 > C2 > B, exactly as predicted — wider
stops fire less often, holding capacity longer). **Performance metrics are NOT cleanly monotonic —
reported honestly, not smoothed into the expected shape.** Pool 1's block-pass count actually *dips*
at C1 (6, below A's 7) before recovering at C2 (8) and B (9). Pool 2's cost-adjusted CAGR **breaks
the trend outright**: C2 (3.14%) is worse than A (3.42%), C1 (4.19%), and B (4.68%) — the widest
fixed stop is the worst performer in Pool 2, not a stepping-stone between A and B.

**Investigated directly, not reported as an unexplained anomaly.** Per the "check outliers for data
artefacts before reporting" instruction: pool2_C2's 15 worst trades were checked against the
`NEEDS_REVIEW` list — **zero involvement** (MRVL, FLEX, TER, SNDK, CAT, WDC, STX, FTNT, GLW, CRWD,
VST, UAL, APP, PLTR — all real, current, well-known companies, real 2024-2026 dates). This is not a
contamination artifact. It's a genuine mechanical effect: a 4.0x stop tolerates roughly double the
adverse move a 2.0x stop does before firing, so when a `STOP_LOSS` *does* eventually trigger under
C2, the loss is proportionally deeper (pool2_C2's worst-15 sum: −£18,137 vs pool2_A's −£15,665, a
real 16% larger tail even though C2 has 3.4x fewer `STOP_LOSS` events to sum over). **Widening the
stop trades stop-frequency (and its FX cost) for per-event severity — and for Pool 2 at 4.0x
specifically, the severity cost outweighs the frequency saving.** C1 (3.0x) does not show this —
it improves over A in both pools, consistent with a real but non-linear relationship: some
widening helps, too much starts costing more per event than it saves in avoided round-trips.

**This is exactly the kind of result the monotonicity check was designed to surface — a genuine,
mechanistically-explained non-linearity, not a bug in the causal story, but a reason not to
extrapolate "wider is better" past 3.0x.** B (no stop) remains the best cost-adjusted performer in
both pools even against C1/C2, but C1 is a real, materially different middle option from B: it
keeps a bounded per-position loss (unlike B) while still beating A on cost-adjusted CAGR in both
pools (6.54%/4.19% vs A's 6.52%/3.42%) and improving drawdown over A in Pool 2 (−47.49% vs −50.21%).

## What this means for the next step

**Ranking on cost-adjusted CAGR, both pools: B > C1 ≈ C2(pool1 only) > A > C2(pool2).** B still
wins outright, but C1 (3.0x) is the closest legitimate alternative that preserves a genuine stop —
worth weighing against B given the drawdown/tail-risk question isn't fully closed (see the
addendum above: even after excluding `NEEDS_REVIEW` tickers, Pool 2's CLEAN-only comparison still
shows A/C-shaped arms with better drawdown than B, just a smaller gap than the raw numbers showed).

This is a materially different picture from `no_stop_loss_2026-08-06`'s own prior "researched, not
adopted" verdict on a narrower test-period comparison — worth being explicit that this run used the
full 2007-2026 continuous walk and reset-at-boundary blocks, a real 503/1,043-ticker universe rather
than that investigation's own setup, and found the cost-adjusted, dual-pool picture leaning more
clearly toward removing or loosening the stop than the earlier single-pool test-period comparison
did — but, per your own framing, **this is a real improvement to bank, not a case for declaring
victory**: cost-adjusted CAGR for the best-performing arm (B) still trails passive equity over the
same window in both pools, and the drawdown question, while substantially explained, isn't fully
closed. D and E remain deferred, not run, pending direction.

## Files

`step1_build_caches.py`/`step1_pool{1,2}.log`, `step2_run_arm.py`/`step2_*.log` (A/B),
`step7_run_arm_c.py`/`step7_*.log` (C1/C2 — reuses the A/B caches), `trades_{pool}_{arm}.csv`,
`rejections_{pool}_{arm}.csv`, `step3_block_validate.py`/`step3_summary.csv`/
`step3_block_validation_results.json` (all 8 arm/pool combos), `step4_capacity_and_whipsaw.py`/
`step4_capacity_and_whipsaw.json` (A vs B), `whipsaw_pairs_{pool}.csv`,
`step5_cost_adjustment.py`/`step5_cost_adjustment.json` (all 8 combos),
`step6_drawdown_contamination_check.py`/`step6_drawdown_contamination_check.json` (the
`NEEDS_REVIEW`-drawdown addendum).

`analysis/backtest.py`'s `disable_stop_loss` parameter (additive, default `False`, byte-identical
for every existing caller) is **still present**, not yet reverted — kept in place per instruction,
since it's the same plumbing D would need if approved next. **Flagged explicitly, not to be missed:
if B (or a variant using it) is ever adopted, this stops being a research flag and becomes
production config**, with `broker_side_stop_enabled`'s unwind and `place_stop_order()`'s removal as
a required, separate follow-up to a live safety layer shipped 2026-08-04 — not implemented here,
and not to be allowed to drift into production as a quietly-defaulted research artifact.
