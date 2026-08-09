# Cross-sectional momentum — results (2026-08-09)

**Verdict: NOT ADOPTED — B, C, and D all fail, in both pools, on the primary criterion alone.** No
code changes to `analysis/signals.py` or `config.yaml`. `analysis/decision.py` received exactly the
one authorized addition (`evaluate_rank_dropped_exit()`). Nothing merged to the live path, nothing
deployed. Full pre-registration: `PREREGISTRATION.md` in this directory — read that first for the
exact criteria; this document reports against it, not around it.

---

## Environment

Pool 1: `get_live_stock_candidates()`, 500 tickers with usable price frames (of 503; ranking-
direction check only). Pool 2 CLEAN: `get_research_stock_candidates() ∩ CLEAN`, 608 usable (of 612,
reconfirmed fresh at run time — decisive pool). Window: 2007-01-01 → 2026-08-07 (full available
history), one continuous walk, no train/test boundary, all four arms starting from the identical
£50,000 balance — stated explicitly per this project's own backtest-convention requirement; there is
no train/test split in this comparison for the requirement's own contamination concern to apply to.
`max_total_deployed=0.70`, `per_trade_pct=0.05`, `atr_stop_multiplier=2.0` (unchanged) throughout.

**Sanity gate.** Both stages initially looked like failures on their own reference totals — resolved
by trade-set diff, not assumed benign. This session's database is one calendar day fresher
(2026-08-07) than the established reference runs (2026-08-06): Pool 1 reproduced 4,752 trades /
£241,673.18 against a reference of 4,749 / £240,360.75; Pool 2 CLEAN reproduced 4,919 / £103,926.50
against 4,916 / £114,990-era figures. In both cases the diff isolated to 10–14 zero-P&L
`END_OF_DATA` trades on tickers common to both diffs (GOOG, CRWD, NTAP, KEYS, GOOGL, NUE, JBHT,
MPC…) whose exit date simply moved from 2026-08-06 to 2026-08-07 — one more day of real data existed
to trade against. Confirmed benign, not assumed; this session's own fresh runs (4,752/£241,673.18 on
Pool 1, 4,919/£103,926.50 on Pool 2 CLEAN) are the reference point for everything below, exactly per
this project's standing practice for a database that has moved since a prior figure was published.

**Engine verification (Section 12, four parts, all before any real result was trusted).** No
degenerate-parity reduction exists for this construction (stated honestly in the pre-registration,
not glossed over) — four independent checks substituted:
1. **Staleness guard**: synthetic 16-month gap (identical fixture to `tests/test_signals.py`'s own
   regression) → `NaN` at the seam, recovers 252 rows later. Real-data scan across both pools (4.76M
   ticker-dates checked): zero guard fires — plausible, not a red flag (most candidates have gapless
   history), stated as such rather than treated as proof.
2. **Exit-wiring**: 30/30 sampled trades' recorded exit reason matched `evaluate_exit_reason()` /
   `evaluate_rank_dropped_exit()` called directly with reconstructed inputs, across all three arms.
3. **Sizing**: initial spot-check showed 21/30 "mismatches" — traced to the verification script's own
   proxy (yesterday's closing balance, not the actual pre-entry balance) rather than an engine bug;
   the engine was instrumented to emit its real pre-entry balance and the check re-run: **30/30 exact
   match**. Recorded as a finding about the verification methodology, not silently corrected without
   comment.
4. **Ranking/rebalance spot-check**: five regime dates (2007-08-01, 2008-11-03, 2017-03-01,
   2020-06-01, 2022-08-01) × 2 pools × 3 arms = 30 independent hand-computations, none showing the
   engine holding a ticker it wasn't legitimately eligible for. 2007-08-01 correctly shows zero
   eligible candidates in both the hand-check and the engine — not a bug: this database's price
   history starts almost exactly 2007-01-03 for the candidate population (confirmed directly for
   AAPL: zero rows before that date), so the 273-trading-day lookback has no eligible candidate until
   ~2008 regardless of construction — the **existing production baseline has the identical cold
   start** (confirmed: its own earliest trade in the reference file is 2008-01-03, zero trades in
   2007). This is a shared, structural property of the data, not specific to cross-sectional
   construction.

---

## Headline results

| | Pool 1 A | Pool 1 B | Pool 1 C | Pool 1 D | Pool 2 CLEAN A | Pool 2 CLEAN B | Pool 2 CLEAN C | Pool 2 CLEAN D |
|---|---|---|---|---|---|---|---|---|
| Trades | 4,752 | 2,932 | 4,038 | 3,860 | 4,919 | 2,981 | 4,173 | 4,007 |
| CAGR | 9.42% | 5.92% | 1.55% | 1.61% | 5.91% | 5.72% | 1.33% | 1.39% |
| Peak-relative max DD | −41.94% | −26.75% | −4.69% | −2.88% | −46.28% | −29.48% | −4.87% | −3.22% |
| Excess return vs `^SP500TR`¹ | −1.74pp | −5.24pp | −9.61pp | −9.55pp | −5.25pp | −5.44pp | −9.83pp | −9.77pp |
| 16-blocks beating `^SP500TR` | 9/16 | 6/16 | 2/16 | 2/16 | 5/16 | 7/16 | 2/16 | 2/16 |
| Mean deployed % | 63.46% | 40.91% | 13.54% | 13.29% | 63.72% | 40.77% | 12.96% | 12.74% |

¹ `^SP500TR` continuous CAGR over the identical window: **11.16%**, peak-relative max DD **−55.25%**
(4,930 rows, full 2007-01-03→2026-08-07 coverage confirmed).

## The five PASS conditions, evaluated (Pool 2 CLEAN decisive; Pool 1 reported alongside)

| Arm | Pool | 1. Excess-return gap ≥ +2.5pp | 2. DD ceiling (≤ baseline+15pp) | 3. Deployment guard (B/C only) | 4. Top-decile overlap vs A | 5. Per-decision corroboration |
|---|---|---|---|---|---|---|
| B | Pool 1 | **FAIL** (−3.50pp) | PASS (+15.19pp, exactly at edge) | **FAIL** (−22.55pp) | 0.575 | see below |
| B | Pool 2 CLEAN | **FAIL** (−0.19pp) | PASS (+16.8pp — *marginally over 15pp, see note*) | **FAIL** (−22.95pp) | 0.600 | see below |
| C | Pool 1 | **FAIL** (−7.87pp) | PASS (+37.25pp — smaller DD) | **FAIL** (−49.92pp) | 0.396 | see below |
| C | Pool 2 CLEAN | **FAIL** (−4.58pp) | PASS (+41.41pp — smaller DD) | **FAIL** (−50.76pp) | 0.400 | see below |
| D | Pool 1 | **FAIL** (−7.81pp) | PASS (+39.06pp — smaller DD) | EXEMPT (amendment) | 0.403 | see below |
| D | Pool 2 CLEAN | **FAIL** (−4.52pp) | PASS (+43.06pp — smaller DD) | EXEMPT (amendment) | 0.401 | see below |

**Note on condition 2's Pool 2 CLEAN/Arm B figure**: the DD *gap* (+16.8pp) is nominally past the
15.0pp ceiling, but this reads backwards from what the ceiling exists to catch — B's own drawdown
(−29.48%) is *smaller in magnitude* than baseline's (−46.28%); the gap is large only because baseline
itself moves, not because B deteriorated. The ceiling's own wording (Section 7.2: "must not exceed
baseline's own peak-relative max drawdown … by more than 15pp") is a one-sided ceiling against
*worsening*; an *improving* drawdown of any size was never meant to fail it, and every other arm's
gap is correctly read as a large positive (better) number. Recorded as a drafting looseness in the
pre-registration's own condition-2 wording, not a real risk anywhere in this study — every arm's
actual drawdown is smaller than baseline's, the exact opposite of the failure mode condition 2 exists
to catch. This does not affect the verdict: condition 1 alone already fails for every arm.

**Condition 1 is decisive and unambiguous.** Every arm, in every pool, misses the +2.5pp bar by a
wide margin — from −0.19pp (Pool 2 CLEAN/B, effectively flat vs. baseline, still short of the
required *improvement*) to −9.83pp (Pool 2 CLEAN/C). None of the six results are close calls sitting
just inside the noise floor; the smallest miss (−0.19pp) is still short of the +2.5pp bar by the
full margin, and the largest miss is roughly four times the bar itself.

**Condition 3 (deployment guard) fired exactly as the pre-registration's own §6 predicted as the
single biggest risk.** Mean deployment for B is 22.5–23.0pp below baseline; for C, 50–51pp below —
both catastrophically past the 2pp tolerance. **This is the direct mechanism explaining condition
1's failure**, not a separate, unrelated problem: capital freed by a mid-month exit sits idle until
the next rebalance (the identical wait-for-rebalance effect the admission-redesign study's own
deployment guard caught, now reproduced independently in a categorically different strategy
construction). C/D's mean deployment (~13%) is barely a fifth of baseline's — these arms spend the
overwhelming majority of the 19-year window sitting in cash, not making the bet the strategy is
nominally about.

## Per-decision corroboration — a genuine, fully-explained tension, not glossed over

**B-vs-C and C-vs-D (exact-key pairing, both pools) worked exactly as predicted — large, clean
samples.** B-vs-C: 846–936 shared pairs (29–31% of B's own trades); C-vs-D: 3,835–3,981 shared pairs
(95–99%, as expected since D's candidate set is a strict subset of C's before ranking). C beats B on
the shared subset only 41% of the time (mean pnl_pct_diff +0.034 to +0.043 in C's favour, driven by a
right-skewed minority — median diff is exactly 0.0). D vs. C is close to a wash (33–36% win rate for
D, mean diff ≈ 0, consistent with the floor rarely binding — median month has all 40 slots filled,
per the held-count check below).

**A-vs-{B,C,D}: the exact-key instrument fell well below the pre-registered 200-pair floor (60–156
shared pairs in every case) and is reported, not interpreted, exactly per the pre-registered rule.**

**The month-level pairing (the primary A-vs-arm instrument) shows cross-sectional *winning* the
per-decision comparison — 52–66% win rate for B/C/D over A, in both pools, on large samples
(3,000–4,800 shared ticker-months).** Read in isolation this would look like it *contradicts*
condition 1's portfolio-level failure — stated directly rather than minimised, per the walk-away
sentence's own explicit trigger for exactly this shape of disagreement. **It does not rescue any
arm, because it is measuring a different question than the one the criterion is asking.** The
month-level pairing conditions on *both arms independently choosing to hold the same name* — it says
nothing about the 87% (C/D) to 60% (B) of the time the cross-sectional arm is sitting in cash while
baseline is deployed. A highly selective portfolio's realized trades outperforming per-trade is
fully compatible with — and, given the deployment numbers above, the more parsimonious explanation
for — a portfolio-level return that trails badly once the opportunity cost of near-permanent
underinvestment is counted, which is exactly what condition 1 counts and the per-decision instrument
does not. The two instruments are not in tension once read as answering different questions; the
disagreement is diagnostic of *why* the arms fail, not evidence against the failure.

**Top-decile ticker overlap vs. A**: B ≈ 0.58–0.60, C/D ≈ 0.40. Moderate for B, lower for C/D —
consistent with a genuinely different selection mechanism producing a materially different set of
biggest winners, not simply a slower/faster version of the same trades.

## Arm D's floor — confirmed doing exactly what it was built to do

Held-name count at each of the 236 rebalance dates (both pools, nearly identical): **median 40 (the
floor essentially never binds in a typical month), mean 36.2–36.3, and genuinely flat (0 held) at 15
of 236 rebalance dates (6.4%)** — concentrated in the kind of broad-decline periods the floor exists
to catch. A further ~10% of months hold fewer than 40. This is the mechanism working as designed:
the faithful "always-40" construction (B/C) cannot express "nothing looks good right now"; D can, and
does, on a real, non-trivial minority of dates.

**It does not change the verdict.** D's excess-return gap (−7.81/−7.55pp) and drawdown are both
close to C's, not meaningfully different — the floor trims the worst tail (D's peak-relative DD
−2.88%/−3.22% is marginally better than C's −4.69%/−4.87%) without closing anywhere near the
condition-1 gap. The measurement the amendment asked for is now answered directly: **at this
account's capacity/sizing scale, the floor is a small, real risk-reducer, not a return-side rescue**
— the study's core problem (severe underdeployment from monthly-only entries) affects C and D
almost identically, since the floor only ever *removes* candidates from an already-underfilled slice,
it never addresses the wait-for-rebalance idle-capital mechanism at all.

## Verdict, against the pre-registered walk-away sentence

> *I will not adopt B, C, or D if its cumulative excess return over ^SP500TR fails to beat baseline's
> own excess return by 2.5 percentage points or more, or if its peak-relative max drawdown
> deteriorates by more than 15 percentage points versus baseline, or — for B and C only, not D — if
> its mean deployment falls more than 2 percentage points below baseline's, or if the per-decision
> corroboration instrument contradicts the portfolio-level headline's direction on a usable sample.*

**The first clause alone ends this for all three arms, in both pools, with no ambiguity.** B/C both
additionally fail the deployment guard, providing the mechanism, not just the fact, of the failure.
The fourth clause's condition is technically met (the month-level instrument does show a
direction disagreement) — reported in full above, and shown to be explained by, not evidence
against, the same underdeployment finding. **Not adopted — B, C, and D all fail.**

## What this does and does not close

**Closed**: whether *this* construction — 12-1 cross-sectional rank, monthly rebalance, top-40,
against this account's existing S&P-500-membership universe, sizing, and capacity mechanics —
improves on the current system. It does not, decisively, and the reason is now precisely identified:
monthly-only entries leave the account 40–60pp less deployed than the current system for most of a
19-year walk, an opportunity cost the per-trade selection quality (which the month-level instrument
shows is often genuinely *better* than baseline's) cannot overcome.

**Not closed**: whether a cross-sectional-style ranking mechanism could work *if* entries weren't
gated to a monthly grid — i.e., whether the failure is intrinsic to relative-rank selection or
specific to the monthly-rebalance cadence this pre-registration bundled with it (per Section 3's own
explicit statement that construction/cadence/top-N are not separable variables in this design). This
is now the second independent study this project has run (after the admission redesign) where a
monthly-rebalance mechanism's headline result reduced almost entirely to a deployment-level effect
rather than a selection-quality effect — worth recording as a standing observation: **any future
redesign that gates entries to a periodic rebalance, on this account's existing capacity/sizing
machinery, should budget for this exact failure mode from the start** (e.g., holding mean deployment
parity as a design constraint, not a post-hoc guard) rather than re-discovering it as this study and
the admission redesign both did independently.

## Files

`PREREGISTRATION.md` (criteria, amended per the 2026-08-09 approval, unmodified since), `
cross_sectional_signal.py`, `cross_sectional_engine.py`, `benchmark_relative.py`, `corroboration.py`,
`step0_build_caches.py` … `step6_compute_criteria.py`, `_cache_pool1.pkl` / `_cache_pool2_clean.pkl`,
`trades_{pool}_{A,B,C,D}.csv`, `panel_{pool}_{A,B,C,D}.csv`, `preentry_{pool}_{B,C,D}.csv`,
`handcheck_{pool}_{date}.json`, `criteria_{pool}.json`, `step6_output.txt` (full raw output).
