# High-threshold trailing stop — results (2026-08-22)

**VERDICT: NOT ADOPTED. All three testable arms fail the primary criterion decisively, in the wrong
direction, in both pools. B200 was NOT TESTED — it failed the pre-registered materiality floor by one
position.** Walk-away clause (1) fires for B30, B50 and B100.

**Research only. No change to `analysis/signals.py`, `analysis/decision.py`, `config.yaml` or any
live path. No database writes. Nothing adopted, nothing merged, nothing deployed.**

Verification V1–V7 all pass (`VERIFICATION.md`). Sanity gate exact, both pools. Stage 0 and the §6
path check are in `STAGE0_REPORT.md`.

---

## 1. PRIMARY CRITERION — cost-adjusted excess return over `^SP500TR`, Δ vs baseline

Bar: **+1.5pp**. Noise floor: **~1.08pp**.

| arm | trades | cost-adj CAGR | excess | **Δ vs baseline** | maxDD | ΔDD | mean hold | deployment |
|---|---|---|---|---|---|---|---|---|
| **Pool 2 CLEAN (decisive)** | | | | | | | | |
| A (baseline) | 565 | 11.441 | +0.278 | **0.000** | −43.88 | — | 149.3d | 65.10% |
| B30 | 1,299 | 6.354 | −4.809 | **−5.087** | −45.15 | −1.27 | 77.9d | 64.15% |
| B50 | 1,042 | 7.442 | −3.721 | **−3.999** | −44.74 | −0.86 | 95.9d | 64.39% |
| B100 | 806 | 9.682 | −1.481 | **−1.759** | −43.88 | 0.00 | 119.7d | 64.44% |
| **Pool 1 (direction only)** | | | | | | | | |
| A (baseline) | 522 | 12.515 | +1.352 | **0.000** | −40.41 | — | 160.7d | 65.06% |
| B30 | 1,213 | 10.004 | −1.159 | **−2.511** | −40.50 | −0.09 | 81.3d | 63.88% |
| B50 | 991 | 10.446 | −0.717 | **−2.069** | −39.87 | +0.54 | 99.0d | 64.02% |
| B100 | 732 | 11.080 | −0.083 | **−1.435** | −40.41 | 0.00 | 127.8d | 64.03% |

**Every arm is negative, every arm is outside the noise floor, and both pools agree on sign and
ordering** (B100 > B50 > B30). No cross-pool disagreement to reconcile.

**Prior 2 confirmed exactly**: monotone attenuation toward zero as the threshold rises, not a sign
change. Extrapolating the Pool 2 curve (−5.087 → −3.999 → −1.759) toward zero would require a
threshold well beyond +200% — i.e. beyond the point where the population is too small to test at all.
**There is no interior optimum. The window prior 5 asked about is empty.**

---

## 2. THE ACTUAL QUESTION (§5.2) — protected versus forgone, and the answer is unusually clean

Paired on `(ticker, entry_date)`, restricted to positions whose baseline path reached the threshold.
Byte-identical trades counted **separately**, never as "not better".

| arm | reached & shared | identical | **diverged** | protected | forgone | **£ protected** | **£ forgone** | **net** |
|---|---|---|---|---|---|---|---|---|
| **Pool 2 CLEAN** | | | | | | | | |
| B30 | 51 | 2 | 49 | 20 | 29 | **+£7,772** | **−£89,674** | **−£81,901** |
| B50 | 36 | 0 | 36 | 11 | 25 | +£5,844 | −£75,204 | −£69,360 |
| B100 | 28 | 0 | 28 | **14** | **14** | +£10,712 | −£38,200 | **−£27,488** |
| **Pool 1** | | | | | | | | |
| B30 | 43 | 1 | 42 | 17 | 25 | +£7,856 | −£70,653 | −£62,797 |
| B50 | 33 | 0 | 33 | 11 | 22 | +£8,649 | −£63,208 | −£54,558 |
| B100 | 22 | 0 | 22 | **12** | **10** | +£8,778 | −£27,181 | **−£18,403** |

All six clear the pre-registered ≥10-diverged floor. **The trail does protect gains — it is simply
that protection is worth an order of magnitude less than what it costs.**

**Per-position magnitudes make the asymmetry explicit:**

| arm | mean £ per protected position | mean £ per forgone position | **ratio** |
|---|---|---|---|
| B30 (P2) | +£389 | −£3,092 | **7.9×** |
| B50 (P2) | +£531 | −£3,008 | **5.7×** |
| B100 (P2) | +£765 | −£2,729 | **3.6×** |
| B100 (P1) | +£731 | −£2,718 | **3.7×** |

**The decisive observation is B100.** On Pool 2 it protects **exactly as many positions as it
forgoes (14:14)** and still nets **−£27,488**. On Pool 1 it protects **more often than it forgoes
(12:10)** and still nets **−£18,403**, with a *positive* median Δ of +2.53pp against a mean of
−30.71pp.

**The trail wins the count and loses the money.** A win-rate read of this mechanism is not merely
uninformative, it is actively inverted — and portfolio win rate confirms it: **28.85% → 34.64%** at
B30 while cost-adjusted CAGR falls 11.441 → 6.354. That is Arm C's finding and the exit sweep's
+5%/+10% finding, replicated a third time at thresholds nobody had tested.

---

## 3. §5.3 CAPACITY TURNOVER — the majority of the damage is NOT the truncation

Reported separately from the direct effect, as required. Total P&L Δ decomposes exactly:

| arm | total P&L Δ | **direct effect** (§5.2 net) | **capacity residual** | direct share |
|---|---|---|---|---|
| B30 (P2) | −£221,843 | −£81,901 | **−£139,942** | 36.9% |
| B50 (P2) | −£190,547 | −£69,360 | **−£121,187** | 36.4% |
| B100 (P2) | −£95,363 | −£27,488 | **−£67,875** | 28.8% |
| B30 (P1) | −£139,963 | −£62,797 | −£77,166 | 44.9% |
| B50 (P1) | −£123,263 | −£54,558 | −£68,705 | 44.3% |
| B100 (P1) | −£93,150 | −£18,403 | −£74,747 | 19.8% |

**55–80% of the damage is capacity turnover, not truncation.** The arms admit 421–1,000 positions
baseline never held, and those admissions carry real positive P&L (£110,893–£281,932) — yet the
residual is strongly negative, because the capital was recovered from far better positions than it
was redeployed into. Freeing locked capacity early did not help; **it hurt more than cutting the
winners short did.**

This is a genuine surprise relative to §5.3's own framing, which anticipated capacity turnover might
*rescue* an arm. It does the opposite, in every arm, in both pools. **Walk-away clause (5)'s
condition — the effect tracing mainly to capacity turnover rather than to directly-affected
positions — is met, though it is moot for adoption since clause (1) already fires.** It matters for
interpretation: the harm is broader than "the trail cuts winners short."

---

## 4. THE FINDING WORTH CARRYING FORWARD — the trail did not deliver the risk reduction that is its entire purpose

§5.4 pre-registered drawdown as a one-sided ceiling and stated that improvement was *expected* and
would **not** count as evidence in favour. **It did not even improve.** Pool 2: −43.88% baseline
against −45.15 / −44.74 / −43.88. Pool 1: −40.41% against −40.50 / −39.87 / −40.41. Flat to slightly
worse, and §5.4 named a worsening as the diagnostically interesting case.

**The mechanism is §5.3.** Mean deployment is essentially identical across every arm (63.9–65.1%, a
spread of 1.2pp). Capital protected out of one position is immediately redeployed into another, so
**position-level protection does not become portfolio-level protection.** A trailing stop reduces
*one position's* exposure; it does not reduce the *book's*, because the book refills.

This generalises past this study: **any protective exit mechanism operating inside a
capacity-constrained, always-redeploying portfolio should be expected to convert protection into
turnover rather than into risk reduction, unless it is paired with a rule that keeps the freed
capital out of the market.** Neither this study nor the exit sweep tested that pairing.

---

## 5. §5.6 TOP-DECILE OVERLAP — the arms dismantle the population that carries the return

| arm | shared (% base / % arm) | Jaccard | **top-decile Jaccard** | top-decile positions truncated | £ cost of those truncations |
|---|---|---|---|---|---|
| B30 (P2) | 52.9 / 23.0 | 0.191 | **0.057** | **21** | −£86,720 |
| B50 (P2) | 54.0 / 29.3 | 0.234 | **0.081** | 19 | −£73,662 |
| B100 (P2) | 61.2 / 42.9 | 0.338 | **0.153** | 18 | −£30,208 |
| B30 (P1) | 52.1 / 22.4 | 0.186 | **0.055** | 18 | −£66,948 |
| B50 (P1) | 56.1 / 29.6 | 0.240 | **0.079** | 18 | −£60,595 |
| B100 (P1) | 59.6 / 42.5 | 0.330 | **0.106** | 13 | −£19,315 |

**Top-decile Jaccard of 0.057–0.153 means the arms and baseline barely share a top decile at all.**
Against overall Jaccard of 0.19–0.34, the *tail* is disrupted about three times as much as the book
as a whole — which is exactly what a mechanism acting only on run-up positions should do, and exactly
why it is fatal here.

18–21 of baseline's ~56 top-decile positions are directly truncated, and per §3.1 the affected
population carries **124.9% of total P&L at B30**. Standing cascade caution applies (low overlap is
not itself a finding) — but here the low overlap is accompanied by a large, consistent, same-signed
aggregate move in both pools, so it is not cascade noise.

---

## 6. Exit composition and costs — both shift as the dial predicts

| arm (P2) | SIGNAL_OFF | STOP_LOSS | TRAILING_STOP | FX pp/yr |
|---|---|---|---|---|
| A | 52.74% | 33.10% | — | 0.268 |
| B30 | 20.55% | 45.57% | **26.94%** | 0.848 |
| B50 | 29.46% | 44.43% | 17.85% | 0.648 |
| B100 | 38.83% | 40.82% | 9.55% | 0.496 |

`SIGNAL_OFF` converts into `TRAILING_STOP` monotonically with threshold, so **clause (6) does not
fire** — no headline moved without its own composition shift.

`STOP_LOSS` *also* rises sharply (33.1% → 45.6%), which is §5.3 visible in the mix: the extra
positions admitted with freed capacity are ordinary new entries, and they stop out at the ordinary
rate. Costs modelled per-arm as required — FX drag more than triples (0.268 → 0.848 pp/yr) as
turnover rises, idle credit essentially constant (0.532–0.551). FX is a genuine treatment effect
here, not a deferred constant.

---

## 7. Walk-away sentence, clause by clause

| clause | B30 | B50 | B100 |
|---|---|---|---|
| (1) clears +1.5pp on Pool 2 CLEAN | **FIRES** (−5.087) | **FIRES** (−3.999) | **FIRES** (−1.759) |
| (2) materiality floor | pass (127/125) | pass (92/92) | pass (55/54) |
| (3) drawdown >15pp worse | pass (−1.27) | pass (−0.86) | pass (0.00) |
| (4) paired outcomes not distinguishably better | **FIRES** | **FIRES** | **FIRES** |
| (5) traces mainly to capacity turnover | **condition met** (63.1%) | **met** (63.6%) | **met** (71.2%) |
| (6) composition doesn't shift as predicted | pass | pass | pass |

**Clause (1) fires alone and sufficiently for all three. NOT ADOPTED.**

**B200: NOT TESTED**, per §3.2 and clause (2) — `n_reached` = 29 (Pool 2) and 25 (Pool 1) against a
floor of 30 fixed before any number was seen. Not run, and **its return figure is not estimated,
reported, or interpreted in either direction.** The §6 path check showed `n_fired` would have been 29
of 29, so the failure is on the population half of the gate alone, by one position.

---

## 8. Priors — how they fared

| # | prior | outcome |
|---|---|---|
| 1 | Mechanism truncates the population producing the return; prior unfavourable | **Confirmed decisively** |
| 2 | Higher thresholds attenuate toward zero rather than flip sign | **Confirmed exactly** — −5.087 → −3.999 → −1.759, no sign change |
| 3 | B200 likely fails materiality, possibly B100 | **Half right** — B200 fails, B100 passes comfortably |
| 4 | Capacity turnover may dominate, sign unpredictable | **Confirmed it dominates (55–80%); sign is decisively negative** |
| 5 | The window between "fires enough to measure" and "fires little enough not to damage the tail" may be empty | **It is empty.** Every threshold that fires enough to measure damages the tail; the one that might not (B200) cannot be tested |
| 6 | Multiple comparisons | Not reached — nothing cleared |
| 7 | The structural oddity is real regardless of outcome | **Stands. See §9.** |

---

## 9. The structural oddity is NOT dissolved — and Stage 0 quantified it

The motivating observation survives this null intact: a stop fixed at entry **does** go inert exactly
when a position has most to give back, and Stage 0 measured the consequence precisely — the median
position reaching +30% surrenders **48.5pp**, keeping only **45.5%** of its peak.

**What this study establishes is that a trailing stop is the wrong remedy, not that the giveback is
imaginary.** §3 of the Stage 0 report gives the reason: `SIGNAL_OFF` is 80–85% of exits in the
affected population, and a 252-day momentum reading cannot turn negative without a large fall from
peak. **The giveback is substantially the price of admission to the right tail** — pay it and you
hold the winners that carry 124% of P&L; refuse it and, as measured here, you lose far more than you
save.

**MRNA is excluded from this conclusion as evidence**, per §9 of the pre-registration. It is not
cited in support of any result above.

---

## 10. What this does not close

1. **B200 remains genuinely untested**, and per Stage 0 §4 "NOT TESTED" is not "safe" — it is the arm
   with the least measurable effect acting on 95.7% of total P&L.
2. **The freed-capital pairing (§4) is untested.** Every arm here immediately redeploys. A protective
   exit combined with a rule holding that capital out of the market is a different mechanism and was
   never run — though the two rebalancing studies' deployment findings are a strong prior against it.
3. **Perturbation robustness was not run.** Not required: it is earned by an arm that fires no clause,
   and none qualified.
4. **The giveback problem is open.** This study closes one remedy, decisively, and leaves the
   phenomenon it was aimed at unaddressed.

---

## 11. Files and state

`PREREGISTRATION.md` (with the RULINGS recorded before any run), `STAGE0_REPORT.md`,
`VERIFICATION.md`, `trail_engine.py`, `step0_confirm_state.py`, `step0b_reconcile_pools.py`,
`step1_stage0_effect_size.py`, `step2_path_check.py`, `step3_build_caches.py`, `step4_verify.py`,
`step5_run_arm.py`, `step5b_diagnose_v1.py`, `step6_instruments.py`,
`step7_sec52_sec53_sec56.py`, `trades_{pool}_{arm}.csv`, `panel_{pool}_{arm}.csv`,
`summary_{pool}_{arm}.json`, `instruments_{pool}.json`, `sec52_{pool}_{arm}.csv`,
`sec56_truncated_{pool}_{arm}.csv`, `stage0_results.json`, `pathcheck_results.json`,
`sec52_sec53_sec56_results.json`.

**Backtest convention**: continuous walk, 2007-01-03 → 2026-08-07, all arms from an identical £50,000
start. No train/test boundary, so reset-at-boundary does not apply.
**Database state**: local research copy, `price_data` through **2026-08-07**, **612 CLEAN**, **608
usable Pool 2 CLEAN / 500 Pool 1** — reconfirmed by direct query at run time, matching the exit
sweep's recorded state exactly. `^SP500TR`/`^IRX` read-only from a research-local cache. **No database
writes.**
