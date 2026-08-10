# Momentum-smoothness candidacy filter — results (2026-08-10)

**Verdict: NOT ADOPTED.** One arm (B1, the loosest threshold) clears the pre-registered +1.5pp bar
on the decisive pool — by **0.051pp** — and is **negatively signed on Pool 1**. The walk-away
sentence does not technically fire, but the sentence is a veto, not an adoption trigger, and an
unreconciled cross-pool sign disagreement is not a basis for adopting anything.

**A real, separate, cross-pool-consistent finding did emerge, and it is not the one the study was
built to test**: peak-relative drawdown improves **monotonically with filter strength in both
pools**, by up to 15pp, at a cost in return. That was a one-sided ceiling in the pre-registration,
not a criterion, and it is reported below as an observation motivating a differently-specified future
study — explicitly not promoted to a finding this study is entitled to claim.

Pre-registration committed at `68326ac` before any result existed. Engine/verification at `ec07fbe`.

---

## Verification — all gates passed before any arm result was trusted

**Metric behaviour confirms the specification-time analysis** that chose (i) signed positive-day
fraction over (ii) path efficiency:

| construction | smoothness |
|---|---|
| single +250% jump, flat every other day | **+0.0040** |
| steady climb, same total, over 252 days | **+1.0000** |

Path efficiency would have scored both ≈1.0 — it measures directness, and a single jump is the most
direct possible path. Choosing (ii) would have scored the jumpiest possible candidate as maximally
smooth and produced a null that looked like evidence against the hypothesis rather than against the
measurement.

**Staleness guard**: `NaN` at the seam of a synthetic 2.4-year gap; unaffected by a 5-day halt;
recovers once a full gapless 252-day window exists past the gap; bounded within [−1, 1].

**Degenerate parity (§9.1) and sanity gate (§8) — exact.** With the filter disabled the engine
reproduced the published Arm B **trade set** identically (0 ours-only, 0 ref-only; max per-trade P&L
difference **0.000000**): Pool 2 CLEAN **4,876 trades / £166,830.11**, Pool 1 **4,757 trades /
£267,685.07**. Filter confirmed inert when off.

**The §2 degenerate-case prediction held exactly: NaN smoothness = 0 of 2,967,281 gate-passing
candidate-days, both pools** — which is what made any non-zero count in an arm a defect to explain
rather than a data property. None occurred.

**One correction, recorded not silently applied.** §8 originally named `get_research_stock_candidates()`
as Pool 2 CLEAN — a different pool (1,017 tickers) from the relative-ranking study's 612
(`status='CLEAN'`). Caught before running; using it would have failed the sanity gate for a benign
reason, and the obvious diagnosis ("the database moved on") would have been wrong.

**Frozen thresholds** (§3, from the independent variable only — no backtest, no outcome data):
`T1 = −0.004` (p10), `T2 = +0.032` (p25), `T3 = +0.052` (p40), over 2,967,281 candidate-days. The
distribution is tighter than the hypothesis implicitly assumes: mean +0.068, std 0.056, full range
[−0.167, +0.302].

---

## Results

### Primary — cost-adjusted excess return over `^SP500TR`, vs arm A's own excess (bar: +1.5pp)

| | Pool 2 CLEAN (decisive) | | Pool 1 (direction only) | |
|---|---|---|---|---|
| arm | excess | Δ vs A | excess | Δ vs A |
| A | −5.730 | — | −3.465 | — |
| **B1** (−0.004) | −4.179 | **+1.551** ✅ | −3.885 | **−0.420** ✗ |
| B2 (+0.032) | −5.355 | +0.375 | −2.318 | +1.147 |
| B3 (+0.052) | −6.245 | −0.515 | −3.845 | −0.380 |

**B1 clears the bar by 0.051pp — 3.4% of the bar itself, and 1.44× the ~1.08pp noise floor.** On
Pool 1 the same arm is **negative**. The two pools disagree in sign on the primary metric for the
only arm that clears.

### Curve shape — interior peaks in both pools, at DIFFERENT thresholds

Over the threshold sequence {none, T1, T2, T3}:
- Pool 2 CLEAN: {0, **+1.551**, +0.375, −0.515} — interior peak at **T1**
- Pool 1: {0, −0.420, **+1.147**, −0.380} — interior peak at **T2**

Both are genuinely bracketed (not monotonic, so the gate-width study's unbracketed-endpoint problem
does not apply), but they peak at different places. A bracketed optimum whose location is not stable
across independently-provenanced pools is not a located optimum.

### Deployment guard — PASSES cleanly, the design argument held

| pool | A | B1 | B2 | B3 |
|---|---|---|---|---|
| Pool 2 CLEAN | 63.74% | 63.22% (−0.52) | 62.56% (−1.18) | 62.00% (−1.74) |
| Pool 1 | 63.42% | 63.03% (−0.39) | 62.35% (−1.07) | 61.71% (−1.71) |

Every arm within 2pp; mean open positions fall only 14.27→13.46 (Pool 2). **The §4 structural
argument was correct**: because every day remains an entry-attempt day and admission is
skip-and-continue, a filtered-out candidate frees its slot to the next eligible candidate the same
day. No arm is reported as not-tested. This is the third study to check this guard and the first
where a candidacy-narrowing mechanism passed it comfortably.

### §6 realised filter effect — all arms are meaningful tests

| pool | arm | candidate-days excluded | admissions displaced | meaningful? |
|---|---|---|---|---|
| Pool 2 CLEAN | B1 | 9.52% | 6.91% | yes |
| | B2 | 28.37% | 17.88% | yes |
| | B3 | 41.70% | 27.26% | yes |
| Pool 1 | B1 | 9.28% | 6.56% | yes |
| | B2 | 27.83% | 18.02% | yes |
| | B3 | 41.02% | 26.68% | yes |

**The predicted gap between exclusion and displacement is real and large** — the filter removes
roughly 1.5× more candidate-days than it displaces actual admissions (9.5%→6.9%, 28.4%→17.9%,
41.7%→27.3%), exactly as the selection audit's "92–93% of admissions are capacity-timed" finding
predicted. It did not, however, collapse below the 5% floor. **These are genuine tests, and the null
is a null about the hypothesis, not about the instrument** — which is precisely what §6 existed to
establish.

### Paired per-decision instrument — internally consistent, cross-pool contradictory

| pool | A-only win / mean P&L% | B1-only win / mean P&L% |
|---|---|---|
| Pool 2 CLEAN | 43.97% / 0.88% | **44.79% / 1.14%** |
| Pool 1 | **44.96% / 1.09%** | 44.41% / 1.05% |

Samples clear both floors (n_differing 5,955–7,047; ≥200 shared pairs). B1's own unique admissions
are genuinely better than A's on Pool 2 — on both win rate and magnitude — and genuinely worse on
Pool 1. The instrument agrees with each pool's own portfolio-level result, which rules out a
cash-holding or cascade artefact **and confirms the disagreement is real rather than measurement
noise in one of them.**

### Overlap — the first approximately-correct prediction in this sequence

Predicted 25–55%; measured **37.1 / 28.6 / 24.4%** (Pool 2) and **36.5 / 27.7 / 23.5%** (Pool 1).
B1/B2 inside the band, B3 marginally below. Worth recording: the relative-ranking study predicted
45–65% and measured 21.8–23.1%, so this is the first overlap prediction here to land near its range.
Top-decile ticker-Jaccard 0.66–0.78 — moderate-to-high, the same "same names, different days" pattern
every comparable study has found.

### Block count — uninformative, as expected

Pool 2 CLEAN: A 7/16, B1 6/16, B2 6/16, B3 7/16. Entirely inside the measured 3-block resolution
floor. No conclusion rests on it.

---

## Walk-away sentence, clause by clause (B1, the only arm clearing the bar)

1. Clears +1.5pp on Pool 2 CLEAN (+1.551pp) — **does not fire**
2. Drawdown not >15pp deeper — peak-relative *improves* 6.11pp — **does not fire**
3. Deployment not >2pp below A (−0.52pp) — **does not fire**
4. Exclusion ≥5% (9.52%) and displacement ≥5% (6.91%) — **does not fire**
5. Paired instrument: B1-only distinguishably better than A-only on Pool 2 — **does not fire**

**No clause fires. And the arm is still not adopted.**

The walk-away sentence is a **veto, not an adoption trigger** — its non-firing makes adoption
permissible, not mandatory. Three things make adoption unjustified here, none of which is a
post-hoc threshold change:

1. **The margin is 0.051pp on a 150bp bar.** The bar was set at 1.4× the ~1.08pp noise floor
   precisely because smaller effects cannot be distinguished from pool perturbation. An effect
   clearing that bar by 3.4% of the bar is, on this project's own measured resolution, at the bar.
2. **Cross-pool sign disagreement on the primary metric** (+1.551pp vs −0.420pp), which §5 and the
   standing dual-provenance requirement mandate reporting as unreconciled. An unreconciled
   disagreement is not evidence of an effect.
3. **The paired instrument reproduces the disagreement rather than resolving it** — B1-only beats
   A-only on Pool 2 and loses on Pool 1. This is the corroborating instrument agreeing that the two
   pools genuinely differ, not that one of them is noisy.

**A fourth signal, specific to B1 and worth naming**: B1 is the only arm in either pool where the two
drawdown measures move in *opposite* directions — peak-relative improves 6.11pp while
fixed-denominator worsens **16.55pp** (−68.44 → −84.99) on Pool 2. Every other arm improves on both.
This project shows both measures together precisely because they can diverge; for the one arm that
clears the bar, they do.

---

## The finding the study did not set out to make

**Peak-relative drawdown improves monotonically with filter strength, in both pools, and the effect
is large:**

| pool | A | B1 | B2 | B3 |
|---|---|---|---|---|
| Pool 2 CLEAN | −47.80% | −41.69% | −33.11% | **−32.73%** |
| Pool 1 | −36.80% | −33.82% | −28.79% | **−24.56%** |

Monotonic in both pools, agreeing in direction at every threshold — **the only quantity in this study
that does** — reaching +15.07pp (Pool 2) and +12.24pp (Pool 1) at B3. It is not a cash-holding
artefact: deployment falls at most 1.74pp, which cannot account for a 15pp drawdown change. Excluding
erratically-achieved momentum genuinely reduces tail risk.

**It is also not free, and not what was tested.** B3 has the best drawdown in both pools and the
*worst* return in both (−0.515pp Pool 2, −0.380pp Pool 1). This is a return-for-risk trade, and the
pre-registration made drawdown a one-sided ceiling, not a criterion. **Promoting it to a headline now
would be exactly the metric-switching pre-registration exists to prevent**, so it is recorded as an
observation only. A study whose primary criterion is risk-adjusted (or which holds return parity and
tests drawdown) would be a legitimate, differently-specified follow-up — and would need its own
pre-registration, not a re-read of this one.

---

## Deferred variants — unchanged, and NOT rescued

Per §1, a null does not license elaborating the filter into a blended score, and none is implemented.
The §11 list stands as written: blended rank/smoothness score, `(t-252, t-21)` aligned window,
cross-sectional percentile form, smoothness as tie-break, smoothness as exit condition.

## Constraints honoured

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path. Local
research worktree, local DB copy (confirmed gitignored), pools reconfirmed fresh at run time (Pool 1
= 503, Pool 2 CLEAN = 612, `price_data` through 2026-08-07). Full trade-level output preserved:
`trades_{pool}_{arm}.csv` and `panel_{pool}_{arm}.csv` for all 8 arm/pool combinations. Nothing
adopted, nothing merged, nothing deployed.
