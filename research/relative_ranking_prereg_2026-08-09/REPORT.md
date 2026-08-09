# Relative-ranking (cross-sectional rank, daily admission) study — results (2026-08-09)

**Verdict: Arm B clears every pre-registered bar — the first arm in this project's entire recent
research sequence (position-count, deployment-cap, gate-width, cross-sectional-monthly) to do so.**
Arm C (B plus the continuous `RANK_DROPPED` exit) also clears its own bars individually, but does not
improve on B — it modestly underperforms B in both pools. **Recommendation: B's mechanism (admission
ordered by cross-sectional relative rank instead of absolute momentum, everything else in the live
system unchanged) is the strongest candidate this project has produced for real promotion. C's added
de-selection exit should not be layered on top.** Stated plainly: this is a research finding, not an
implementation — no code changed on any live path in this study, `config.yaml` untouched, nothing
merged or deployed. Real promotion would need its own follow-up (see "What this doesn't close").

## Verification — all checks passed before any real result was trusted

**Staleness guard**: synthetic 40-trading-day gap correctly produces `NaN` at the seam — PASS.

**Degenerate-case parity — stronger than the pre-registration's own plan required.** The pre-
registration stated no degenerate-case reduction existed for this construction (mirroring the monthly
cross-sectional study's own honest statement); on review, the engine was built with a test-only
`_sort_key="momentum"` toggle that makes one exist. With that toggle set, `relative_ranking_engine.py`
reproduced the raw, unmodified `simulate_portfolio_compounding_mtm()` **byte-identical on Pool 2 CLEAN
— 4,919 trades, £103,926.50, 0 row-level mismatches.** This proves every part of the new engine except
the one sort-key line (gate, sizing, exits, capacity walk) is a faithful copy of production, not merely
documented as one.

**Exit-wiring / signal-separation**: 30/30 sampled Arm B trades' `SIGNAL_OFF` exits confirmed to trace
to the *absolute* momentum value at exit, never the cross-sectional rank — the one thing §1's narrowing
depends on not leaking, confirmed directly.

**Ranking hand-check**: 4 regime dates, Pool 2 CLEAN, hand-computed top-5 by cross-sectional rank —
plausible, dated momentum leaders throughout (UAL/BC/INCY in mid-2010's recovery, PAYC/AXON/SWKS in
2015, TSLA/ENPH/DXCM/MRNA/NVDA in the 2020 COVID-recovery rally, OXY/RRC/DVN/TRGP/EQT — all energy
names — in the 2022 energy rally). No red flags.

**Sanity gate**: Arm A reused directly from `research/position_count_prereg_2026-08-09`'s already-
verified baseline trades (identical inputs: `per_trade_pct=0.05`, `max_total_deployed=0.70`) — reuse
confirmed valid, not assumed, by re-querying the database immediately before this study's own real runs
(612 CLEAN, price data through 2026-08-07 — unchanged since those files were generated earlier the same
session).

## Results — all three arms, both pools, together

| | Pool 1 (direction only) | | | Pool 2 CLEAN (decisive) | | |
|---|---|---|---|---|---|---|
| | A (baseline) | B | C | A (baseline) | B | C |
| Trades | 4,752 | 4,757 | 5,955 | 4,919 | 4,876 | 6,065 |
| Cost-adj. CAGR | 7.164% | 7.698% | 6.681% | 3.440% | 5.433% | 5.190% |
| Excess vs `^SP500TR` (cost-adj.) | −3.999 | −3.465 | −4.482 | −7.723 | −5.730 | −5.973 |
| Δ vs baseline | — | **+0.534pp** | −0.483pp | — | **+1.993pp** | +1.750pp |
| Peak-rel. DD | −41.94% | −36.80% | −39.81% | −46.28% | −47.80% | −44.88% |
| Δ DD vs baseline | — | +5.14pp (better) | +2.13pp (better) | — | −1.52pp (worse) | +1.40pp (better) |
| Mean deployed % | 63.46 | 63.42 | 63.31 | 63.72 | 63.74 | 63.51 |
| Δ deployed vs baseline | — | −0.04pp | −0.15pp | — | +0.02pp | −0.21pp |
| Mean n open positions | 13.98 | 13.96 | 13.80 | 14.23 | 14.27 | 14.00 |
| Block-pass (Pool 2 CLEAN only) | — | — | — | 7/16 | 7/16 | 7/16 |

**B clears the +1.5pp primary bar decisively on the decisive pool — +1.993pp, nearly 2x the measured
~1.08pp noise floor** — and does so without breaching the 15pp drawdown ceiling (drawdown actually
worsens only 1.52pp on Pool 2 CLEAN, and *improves* 5.14pp on Pool 1). **C also clears the bar
(+1.750pp), but by less than B in both pools** — the B→C delta (adding the continuous `RANK_DROPPED`
exit) is **negative** in both pools (Pool 2 CLEAN: −0.243pp; Pool 1: −1.017pp, enough to push C's own
Pool 1 delta into negative territory). This is the curve/pattern-discipline check the pre-registration's
§6 asked for, and the answer is clean: **the added de-selection mechanism does not help — the entry-
ordering change alone (B) is where the value is.**

## The deployment guard — confirms the same-day-redeployment design worked as intended

**Every arm, both pools, within 0.21pp of baseline's own mean deployment** — the tightest parity this
project's whole sequence of studies has measured, for either arm. This is the direct, designed
consequence of §4's structural argument: every day is an entry-attempt day for every arm (never gated
to a periodic rebalance), and cross-sectional rank's own zero-sum property means a `RANK_DROPPED` exit
almost always has a same-day replacement candidate ready. Confirmed, not merely argued: C — the arm
with the added exit mechanism, the one place a redeployment gap could plausibly have opened — shows the
*smallest* deviation of the two non-baseline arms in Pool 2 CLEAN (−0.21pp) and a modest one in Pool 1
(−0.15pp). Neither arm is reported as not-tested; both cleanly test what they claim to.

## The A-vs-B overlap prediction — wrong, and reported as such, not smoothed over

**Predicted 45–65%; measured 21.8–23.1%, both pools** — well below even the low end of the pre-
registered range, though far below the 80% disqualifying threshold in the other direction (so this does
**not** trigger "B is not a meaningful test," if anything the opposite conclusion). **This is a genuinely
larger capacity-cascade effect than any prior comparison in this project's sequence has measured for a
change this narrow** — B changes *only* the sort key among an *unchanged* eligible candidate set (no
gate change, no truncation, unlike every prior "overlap" comparison this project has run), and still
only ~22% of admissions are exact-date-shared with baseline. Reported as a real, informative finding
about just how sensitive this capacity-constrained, sequential-admission system is to admission *order*
specifically — reordering alone, with nothing else touched, reshapes the majority of the 19-year trade
sequence. A-vs-C overlap is lower still (17.2–21.2%), consistent with the added exit mechanism
compounding the same cascade effect further.

**Top-decile ticker overlap remains moderate-to-high throughout** (A-vs-B: 0.66–0.69 ticker-Jaccard;
A-vs-C: 0.65–0.66) despite the low exact-date overlap — the same shape this project has found in every
comparable comparison: largely the same *names* producing the biggest winners, rarely on the exact same
day, consistent with cascade reshuffling of timing rather than a wholesale change in which companies
matter.

## The paired per-decision instrument — the decisive corroboration, not a disqualifying signal

**Both B and C show a real, distinguishable edge on their own unique admissions over baseline's own
unique admissions, in both pools — the walk-away sentence's own fourth clause does not fire for either
arm:**

| | A-only win / mean P&L% | B-only win / mean P&L% | C-only win / mean P&L% |
|---|---|---|---|
| Pool 1 | 45.27% / 1.03% | **45.75% / 1.07%** | **47.13%** / 0.87% |
| Pool 2 CLEAN | 43.57% / 0.63% | **44.30% / 0.89%** | **46.22%** / 0.74% |

**B's edge is consistent and real on both measures, in both pools** — higher win rate (+0.48pp Pool 1,
+0.73pp Pool 2 CLEAN) and higher mean P&L% (+0.04pp Pool 1, +0.26pp Pool 2 CLEAN, the larger of the two
by a wide relative margin). This is the single most important piece of evidence in this study: it means
B's portfolio-level gain is not an artefact of holding more or less cash, or of a lucky reshuffling —
the specific trades B makes that baseline doesn't are, on average, genuinely better trades, exactly what
Part 4 of the selection audit and the monthly cross-sectional study's own month-level pairing both
already suggested but couldn't isolate cleanly until now.

**C's edge is real on win rate (+1.86pp Pool 1, +2.46pp Pool 2 CLEAN — larger than B's) but mixed on
magnitude** (−0.24pp Pool 1, +0.05pp Pool 2 CLEAN) — consistent with `RANK_DROPPED` cutting more
positions earlier/smaller (a higher hit rate of modest wins, not a change in the size of the big ones),
which is also consistent with C's own higher trade count (6,065 vs. B's 4,876 on Pool 2 CLEAN) and its
failure to beat B on the portfolio-level headline despite the higher win rate — more, smaller wins that
don't net out ahead of B's fewer, better-sized ones.

## Block count — corroborating only, uninformative here

Pool 2 CLEAN: A/B/C all show identically 7/16 — inside the measured 3-block resolution floor, and not
expected to discriminate at this arm-to-arm distance regardless (this project's own standing note on the
block-pass resolution floor). Consistent with, not contradicting, everything above; simply not a useful
signal at this scale of comparison.

## Walk-away sentence, checked clause by clause, for both arms

> *"I will not adopt B or C if: no arm clears +1.5pp cumulative cost-adjusted excess return over
> baseline's own excess return without exceeding baseline's own peak-relative drawdown by more than
> 15pp; or if any arm's mean deployment diverges from baseline by more than 2pp; or if the A-vs-B
> admission overlap comes back ≥80%; or if the paired per-decision instrument shows a winning arm's own
> unique admissions are not distinguishable from baseline's on a usable sample."*

**Arm B:**
1. Clears +1.5pp (+1.993pp) without breaching 15pp drawdown — does not fire.
2. Deployment diverges 0.02pp — does not fire.
3. A-vs-B overlap is 21.8–21.9%, far below 80% — does not fire.
4. B-only admissions are distinguishably better on both win rate and mean P&L%, both pools — does not
   fire.

**No clause fires for B. B clears the walk-away sentence in full.**

**Arm C:**
1. Clears +1.5pp (+1.750pp) without breaching 15pp drawdown (drawdown actually improves) — does not
   fire.
2. Deployment diverges 0.21pp — does not fire.
3. (Clause scoped to A-vs-B only per the pre-registration; C's own overlap, 17.2–21.2%, would not
   trigger it either.)
4. C-only admissions are distinguishably better on win rate in both pools — does not fire, though the
   magnitude signal is mixed (see above).

**No clause fires for C either, taken in isolation.** But per §6's curve-discipline requirement, C is
compared directly against B, not evaluated only against baseline — and on that comparison, C adds
turnover and complexity for a *worse* portfolio-level result than B alone, in both pools. **C is not
recommended over B**, not because C fails its own pre-registered bar, but because the pre-registration's
own comparative requirement (§6) was built specifically to catch exactly this shape of outcome: an
addition that individually looks fine but doesn't earn its own keep against the simpler alternative
already sitting next to it.

## What this doesn't close

**This is a backtest research finding, not a production change, and promotion would need real follow-up
work this study did not do.** Specifically, if B's mechanism is ever built into the live system: (1) the
cross-sectional rank computation would need to move from this study's research-only
`cross_sectional_signal.py` into `analysis/signals.py` (or an equivalent shared module) as a genuine,
tested production signal — not left as research code a live caller reaches into, per this project's own
"one implementation of any given trading decision" invariant; (2) the four structural-enforcement layers
this project already runs for live/backtest parity (`tests/test_decision_logic_placement.py`,
`tests/test_comprehensive_replay_parity.py`, `tests/test_live_backtest_parity.py`,
`tests/test_one_sided_decision_rules.py`) would need genuine coverage of the new ranking logic, not
retrofitted after the fact; (3) live trading remains demo-only regardless (`trading212_invest.live:
false`), so nothing here is urgent from a real-money-risk standpoint, but the research-to-production gap
is real and should not be skipped because the backtest result looks strong. **What this study does
close**: whether relative-rank *ordering* of an otherwise-unchanged daily admission process beats
absolute-momentum ordering — tested cleanly, with the cadence confound the monthly study couldn't remove
fully separated out, and the answer is yes, on the decisive pool, corroborated by the per-decision
instrument, not just the portfolio-level headline.

## Files

`PREREGISTRATION.md`, `cross_sectional_signal.py` (reused unmodified from the monthly study),
`relative_ranking_engine.py`, `step0_build_caches.py`, `step1_verification.py`, `step2_run_arms.py`,
`step3_instruments.py`, `step3_all_results.json`, `trades_{pool1,pool2_clean}_{A,B,C}.csv`,
`panel_{pool1,pool2_clean}_{A,B,C}.csv` (Arm A's trades/panel reused directly from
`research/position_count_prereg_2026-08-09`'s baseline, byte-identical inputs).
