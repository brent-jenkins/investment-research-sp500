# Correlation-aware admission ordering — RESULTS (2026-08-23)

**VERDICT: NOT ADOPTED. Every one of the twelve arms is worse than baseline — in both directions, at
every k, in both pools.** Not one clears the +1.5pp bar; not one is even positive. On the decisive
pool the damage is **monotone in |k| in both directions**, so the curve does not merely fail to find
an optimum — **it points at k = 0.**

**This is the strongest form a null can take**: not "no effect detected", but "baseline sits at a
local optimum in this dimension, and moving either way costs."

Research only. No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any
live path. No database writes. Nothing adopted.

Pre-registration: `PREREGISTRATION.md`, approved before any run. §6 verification and the §3 gate were
completed and reviewed **before** any return figure was computed — `step4_gate.py` contains no code
capable of producing one.

---

## 1. §4.1 Primary criterion — Pool 2 CLEAN (decisive)

| arm | k | trades | frictionless | FX | idle | cost-adj CAGR | excess | **Δ vs base** | maxDD | ΔDD | book corr | blocks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.00 | 565 | 11.177 | 0.268 | 0.532 | **11.441** | +0.278 | — | −43.88 | — | 0.3538 | 11/16 |
| D1 | +0.25 | 582 | 7.461 | 0.299 | 0.542 | 7.704 | −3.459 | **−3.737** | −42.53 | +1.35 | 0.2869 | 10/16 |
| D2 | +0.50 | 583 | 8.147 | 0.304 | 0.543 | 8.385 | −2.778 | **−3.056** | −41.26 | +2.62 | 0.2809 | 13/16 |
| D3 | +1.00 | 609 | 6.931 | 0.324 | 0.542 | 7.149 | −4.014 | **−4.292** | −43.30 | +0.58 | 0.2634 | 10/16 |
| R1 | −0.25 | 575 | 9.847 | 0.286 | 0.531 | 10.092 | −1.070 | **−1.348** | −44.67 | −0.79 | 0.4246 | 12/16 |
| R2 | −0.50 | 593 | 8.927 | 0.304 | 0.530 | 9.153 | −2.010 | **−2.288** | −44.50 | −0.62 | 0.4575 | 13/16 |
| R3 | −1.00 | 593 | 8.122 | 0.308 | 0.526 | 8.340 | −2.823 | **−3.101** | −45.08 | −1.20 | 0.4645 | 11/16 |

### Pool 1 (direction only) — agrees on sign for all six arms

| arm | k | cost-adj CAGR | **Δ vs base** | maxDD | ΔDD | book corr | blocks |
|---|---|---|---|---|---|---|---|
| baseline | 0.00 | 12.515 | — | −40.41 | — | 0.3546 | 12/16 |
| D1 | +0.25 | 9.652 | **−2.863** | −37.39 | +3.02 | 0.2837 | 13/16 |
| D2 | +0.50 | 11.077 | **−1.438** | −35.35 | +5.06 | 0.2867 | 13/16 |
| D3 | +1.00 | 7.719 | **−4.795** | −38.56 | +1.85 | 0.2745 | 10/16 |
| R1 | −0.25 | 8.815 | **−3.700** | −39.53 | +0.88 | 0.4178 | 13/16 |
| R2 | −0.50 | 9.076 | **−3.439** | −40.73 | −0.32 | 0.4361 | 12/16 |
| R3 | −1.00 | 10.308 | **−2.207** | −40.47 | −0.06 | 0.4564 | 12/16 |

Damage runs **−1.348pp to −4.795pp**, outside the measured ~1.08pp noise floor in ten of twelve
combinations. Costs were modelled per arm as required and are immaterial to the verdict: FX drag
0.268 → 0.324 pp/yr, idle credit essentially flat at ~0.53, a total swing of ~0.06pp against effects
of 1.3–4.8pp.

---

## 2. MY PRIOR WAS RIGHT ON DIRECTION AND WRONG IN THE WAY THAT MATTERS

§9 predicted reinforcing would look better than diversifying, from Part 7's finding that momentum
ranking beats random admission at the 99th percentile *because* it concentrates.

**The directional prediction holds cleanly: reinforcing beats diversifying at every matched |k| on the
decisive pool** — R1 (−1.348) vs D1 (−3.737), R2 (−2.288) vs D2 (−3.056), R3 (−3.101) vs D3 (−4.292).

**And it produced no positive result whatsoever.** The best reinforcing arm still destroys 1.348pp.

**That is the honest framing and the transferable caution: a positive prior reasoned from an adjacent
finding survived as an ordering and failed completely as a prediction of sign.** Part 7 varied *which*
candidates fill roughly fixed slots; this varied their *correlation structure*. §9 itself flagged that
the two are not the same manipulation — the flag was right, and the reasoning still produced a
direction that beat its opposite while losing to doing nothing. **An adjacent finding can tell you
which of two wrong answers is less wrong.**

---

## 3. The saturation prediction was made before returns existed, and D3 confirmed it

The §3 gate found that diversification **saturates** while concentration does not: book correlation
falls 0.354 → 0.287 at k=0.25 and then only to 0.263 at k=1.00, while the reinforcing side rises
monotonically 0.425 → 0.458 → 0.465.

**D3 is the worst arm in the study (−4.292pp) and bought 0.024 of further decorrelation over D1 for
0.56pp of extra damage.** That is the floor effect made quantitative: past k ≈ 0.25 the adjustment is
spending rank quality on decorrelation that is not available, because a book of momentum leaders is
selected for having risen together.

**This is the gate's diagnostic value demonstrated, not asserted.** The prediction was recorded before
any return figure existed and the returns confirmed it. It also means **D3's underperformance relative
to D1 is the mechanism, not evidence against diversification in general.**

---

## 4. §5 — THE MECHANISTIC EXPLANATION: both directions spend rank quality

The §5 secondary instrument was pre-registered with a prediction: diversifying should admit from
*further down* the raw momentum-rank list, reinforcing from *further up*, monotonically in k.

**The prediction is wrong, and being wrong is what explains the result.** Mean raw momentum-rank
percentile of admitted positions:

| | R3 (−1.0) | R2 | R1 | **baseline** | D1 | D2 | D3 (+1.0) |
|---|---|---|---|---|---|---|---|
| Pool 2 CLEAN | 0.7856 | 0.7879 | 0.7966 | **0.7953** | 0.7947 | 0.7907 | 0.7681 |
| Pool 1 | 0.7633 | 0.7931 | 0.8069 | **0.7998** | 0.8013 | 0.7805 | 0.7684 |

**Not monotone — an inverted U, peaking near k = 0 and falling away in BOTH directions.** Reinforcing
admits from further down the rank list too, because high-correlation candidates are not the same
population as high-rank candidates.

**So the walk-away clause on rank monotonicity fires** — moot, since the primary criterion already
fires, but it is the sharpest single explanation available: **any adjustment away from k = 0 spends
raw rank quality, and raw rank quality is what this system's edge is made of.** Part 7 established
that ranking beats random admission at the 99th percentile; both arms of this study move admission
away from pure rank, and both are punished roughly in proportion.

**The mechanism itself expressed perfectly** — correlation percentile at admission moves 0.500
(baseline) → 0.287 (D3) → 0.732 (R3), and raw correlation at admission 0.278 → 0.481. Nothing failed
to engage. It engaged exactly as designed and cost money.

### The disagreement-population instrument agrees, 12 out of 12

Arm-only admissions versus baseline-only admissions, mean P&L%:

| | D1 | D2 | D3 | R1 | R2 | R3 |
|---|---|---|---|---|---|---|
| Pool 2: baseline-only | 16.57 | 15.61 | 16.62 | 13.74 | 17.23 | 16.27 |
| Pool 2: **arm-only** | **7.97** | **9.13** | **7.12** | **11.03** | **11.99** | **9.80** |
| Pool 1: baseline-only | 17.40 | 18.26 | 19.47 | 21.52 | 18.73 | 18.31 |
| Pool 1: **arm-only** | **10.07** | **13.54** | **9.28** | **11.50** | **10.55** | **13.31** |

**Every combination clears the 100-admission floor, and in all twelve the arm's own unique admissions
are worse on mean P&L than baseline's own unique admissions.** Win rates are mixed — four arms show a
*higher* arm-only win rate (R1 Pool 1: 37.04% vs 32.84%) — which is the Arm C shape from the
relative-ranking study reappearing: more frequent, smaller wins that do not net out against fewer,
larger ones in a system that lives on the right tail.

---

## 5. §4.2 — D2 on Pool 1 is the case the asymmetry clause was written for

**Diversifying improved drawdown in all six arm/pool combinations** (+0.58 to +5.06pp); reinforcing
worsened it in five of six. **Clause A (15pp) and Clause B (−60%) pass everywhere** — drawdown was
never the binding constraint.

**D2 on Pool 1: drawdown improves 5.06pp, block-pass goes 12/16 → 13/16, and return falls 1.438pp.**

Without §4.2's pre-registered asymmetry clause that writes up as a qualified win. With it, it is
exactly what it is: **diversification improving drawdown is the expected behaviour of
diversification, and here it is priced at 1.438pp of annual return, not free.** The
momentum-smoothness precedent — which declined to promote a monotone +15.07pp drawdown improvement for
precisely this reason — is why the clause existed in advance rather than being argued afterwards.

---

## 6. §4.4 — correlation and sector concentration came apart, as suspected

Held-day-weighted sector Herfindahl, Pool 2 CLEAN: baseline **0.1256**, diversifying 0.113–0.116,
reinforcing **0.110–0.129**.

**Reinforcing raised book correlation to 0.465 while LOWERING sector concentration** (R3: HHI 0.110
vs baseline 0.126; Technology's share falls 26.4% → 19.4%, with Industrials rising to 16.2%). The two
measures genuinely dissociate: correlation picks up common factor exposure that crosses sector labels,
so a book can become more correlated and less sector-concentrated at once.

§4.4 predicted they were "related but not identical" and asked for both. That was worth doing —
reporting sector concentration alone would have described the reinforcing arms as *diversifying*.

**Coverage caveat, stated not buried**: the preserved sector map covers 193 of 238 baseline tickers;
**13.4% of Pool 2 held-days are Unmapped** (0.0% on Pool 1) and are excluded from the HHI but reported
in the shares. The dissociation is large enough to survive that gap, but the HHI values are not exact.

---

## 7. §4.6 — overlap is structural, confirmed against its own null

Exact-key Jaccard 0.228–0.310, ticker Jaccard 0.608–0.713, top-decile exact Jaccard 0.113–0.207 —
the same shape every ordering change in this project has produced (relative-rank measured 0.218–0.231).

**Every arm's observed overlap sits at the 100th percentile of its own randomisation null** (e.g.
Pool 2 D1: 240 observed against 170.2 ± 3.7 expected). Low overlap is **forced by construction and
~1.4× chance**, so it contextualises the arms rather than measuring an effect — exactly as §4.6
pre-registered.

**§4.7 block counts: 10–13 of 16 against baseline's 11–12**, every delta inside the 3-block resolution
floor. Non-discriminating, as pre-registered — and worth noting D2 goes 13/16 on both pools while
losing 1.4–3.1pp, one more demonstration that block deltas of this size carry no reliable sign.

---

## 8. The walk-away sentence, clause by clause

1. **Any arm changes <5% of admissions → NOT TESTED.** Does not fire. All twelve arms changed
   108–131% of admissions.
2. **Deployment diverges >4pp.** Does not fire. Worst is −0.69pp.
3. **No arm clears +1.5pp on Pool 2 CLEAN.** **FIRES.** Best is R1 at **−1.348pp** — negative, not
   merely short. **This alone decides the study.**
4. **Drawdown Clause A / Clause B.** Do not fire.
5. **A clearing arm's advantage rests on drawdown rather than the primary criterion.** No clearing
   arm exists; §4.2's clause was nevertheless load-bearing for how D2/Pool 1 is written up (§5 above).
6. **Disagreement instrument shows a clearing arm's unique admissions not distinguishably better.**
   No clearing arm; **and the instrument independently shows arm-only admissions worse on mean P&L in
   12 of 12 combinations**, so it would have fired had one cleared.
7. **Rank-at-admission not monotone in k.** **FIRES.** Inverted U in both pools (§4).
8. **A clearing arm changes admissions without changing realised book correlation.** Does not fire —
   book correlation moved 0.354 → 0.263 / 0.465, confirmed at the gate before returns existed.

**Two clauses fire. NOT ADOPTED.** Per the walk-away sentence's own asymmetry note, no perturbation
robustness test was run: it is earned only by an arm firing no clause, and none qualifies.

---

## 9. Verification and gate, for the record

**§6.1 degenerate parity at k = 0: 0 mismatches on both pools** (565 / £348,548.74 and 522 /
£429,108.58), byte-identical to raw production **while running the entire new path** — correlations
computed, percentiles taken, adjusted sort applied. **§6.2: 50/50 sampled days reorder at k=1 and
50/50 are unchanged at k=0** — both halves, because parity alone would be consistent with a mechanism
that never engages. **§6.3**: the measure recovered all four share-class control pairs (GOOG/GOOGL
0.9952, UA/UAA 0.9937, FOX/FOXA 0.9855, NWS/NWSA 0.9678) against unrelated pairs at −0.214 to +0.237.
**§6.4** staleness guard: undefined across a >400-day gap, defined before, recovered after, no mocking
of the correlation computation anywhere. **§7 sanity gate: 0 trade-SET mismatches** on both pools.

**An unplanned cross-check worth recording**: the 120-day measure returned GOOG/GOOGL at **0.9952**
where the share-class study's independent computation over full overlapping history returned
**0.9968** — different window, different estimator path, neither aware of the other, agreeing to
0.0016.

**The §3 gate** (reported in full before returns) found all twelve arms TESTED at twentyfold the 5%
floor, 101–116 dates per arm with genuine same-day substitution, undefined-correlation share ≤0.1%,
and deployment parity within 0.69pp.

---

## 10. Conclusion, and the two caveats that bind the claims

**Correlation-aware admission ordering does not work in this system, in either direction.** The
mechanism engaged exactly as designed — book correlation moved from 0.354 to 0.263 or 0.465, admission
correlation percentile from 0.500 to 0.287 or 0.732 — and every configuration of it lost money. The
explanation is mechanistic and consistent across both pools: **any departure from pure rank ordering
spends raw rank quality, which is where this system's measured edge lives.**

**CAVEAT 1 — portfolio-level claims only.** The split-half test measured **0.49 Spearman rank
agreement** between adjacent 60-day halves, with per-estimate noise of 0.072 raw correlation — **2.54×
a one-decile move**. Individual candidate orderings are roughly half noise, so **no per-candidate
reading of these results is supported.** The defensible claim is exactly this: *books built at 0.26 or
0.46 mean correlation both underperform one built at 0.35 by momentum rank alone.* Aggregate
composition shifted reliably (§4.3 proves it) while per-candidate ordering did not — both are true and
only the first supports a conclusion.

**CAVEAT 2 — one measure at one window, and the follow-up has a poor prior.** A 252-day window would
roughly halve the estimation noise at the cost of measuring a staler book. It is the one genuinely
open variant this null leaves. **It should not be expected to reverse anything**: a staler book
measured more precisely is still measuring correlation, and correlation in *either* direction cost
money here — the failure is not obviously an estimation-quality failure. Recorded as open, not as
promising.

**What this does NOT overturn**: the share-class exclusion (2026-08-22) remains justified on its own
narrow terms. It is a hard exclusion over four hand-verified pairs at 0.97–0.99 — the degenerate
extreme — adopted on risk grounds with no return claim. **This null is a statement about the
gradient, not about the twin**, exactly as §0 pre-registered.

**This is roughly the 62nd experiment in this project's record, and the second consecutive one to find
that a plausible refinement of admission ordering costs return.** Combined with the slot-count and
position-count studies, the accumulating picture is that the admission mechanism — relative-rank
ordering into a capacity-constrained daily clock — is at a local optimum across every dimension tested
so far: how many positions, how much capital, which candidates by correlation.

---

## 11. Files and provenance

`PREREGISTRATION.md`, `corr_engine.py`, `step1_build_caches.py`, `step2_verify.py`,
`step2b_corr_dispersion.py`, `step3_run_arms.py`, `step4_gate.py`, `step5_instruments.py`,
`step2_verification.json`, `step2b_corr_dispersion.json`, `step4_gate_{pool}.json`,
`step5_instruments_{pool}.json`, `step5b_rank_at_admission.json`, `corr_dispersion_{pool}.csv`,
`trades_/panel_/admissions_/corrlog_{pool}_{arm}.csv` (7 arms × 2 pools).

**Backtest convention**: continuous walk, 2007-01-03 → 2026-08-07, every arm from an identical £50,000
start; no train/test boundary, so reset-at-boundary is not applicable. **Deployment-cap accounting**:
mark-to-market throughout. **Database**: local research copy, `price_data` through 2026-08-07, 612
CLEAN, 608 usable Pool 2 CLEAN / 500 Pool 1, verified by direct query at run time. `^SP500TR`/`^IRX`
reused read-only from the exit-mechanics study's committed cache. **No database writes.** Nothing
adopted, nothing merged into the trading logic, nothing deployed.

**Correlation-measure dispersion, for anyone reusing this**: within-day candidate-to-book correlation
has median ~0.34 with IQR 0.120 and p10–p90 of 0.227, against a full range of 0.543 — a tight core
with thin tails. A one-decile percentile move corresponds to only **0.028** of raw correlation, which
is why the split-half noise figure matters as much as it does.
