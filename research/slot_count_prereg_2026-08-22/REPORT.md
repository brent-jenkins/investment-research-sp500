# Hard slot-count concentration study — RESULTS (2026-08-22)

**VERDICT: NOT ADOPTED.** No arm clears the pre-registered +1.5pp cost-adjusted excess-return bar on
Pool 2 CLEAN. The best arm, `n3`, lands at **+1.004pp** — below the bar, and inside the measured
~1.08pp noise floor. The walk-away sentence fires on its primary clause.

**The study was NOT decided by the drawdown ceiling, and that refutes the honest prior it was written
with.** Both drawdown clauses pass for every arm in both pools; Clause B's −60% absolute floor was
never approached. **Concentration did not deepen drawdown here — at three and five positions it
reduced it.**

Nothing adopted, nothing merged, nothing deployed. No changes to `analysis/signals.py`,
`analysis/decision.py`, `config.yaml`, or any live path. No database writes.

Companion documents: `PREREGISTRATION.md` (approved before any run), `GATE_REPORT.md` (§7
verification and the §2 gate, reviewed and approved before this stage).

---

## 1. Headline — both pools

Cost-adjusted excess return over `^SP500TR`; Δ is against baseline's own excess. Peak-relative max
drawdown. Bar +1.5pp, noise floor ~1.08pp, block-pass resolution floor 3.

### Pool 2 CLEAN (decisive)

| arm | realised N | positions | frictionless CAGR | FX pp | idle pp | cost-adj CAGR | excess | **Δ vs base** | maxDD | ΔDD | blocks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline (uncapped) | 11.78 | 565 | 11.177 | 0.268 | 0.532 | 11.441 | +0.278 | — | −43.88 | — | 11/16 |
| n8 | 6.23 | 298 | 8.294 | 0.250 | 0.546 | 8.591 | −2.572 | **−2.850** | −48.86 | −4.98 | 10/16 |
| n5 | 4.11 | 178 | 9.546 | 0.239 | 0.552 | 9.859 | −1.304 | **−1.582** | −39.48 | **+4.40** | 12/16 |
| n3 | 2.43 | 84 | 12.071 | 0.183 | 0.556 | 12.445 | +1.282 | **+1.004** | −40.85 | **+3.03** | 9/16 |
| *n14 — NOT TESTED* | *10.91* | *505* | *10.376* | *0.243* | *0.557* | *10.691* | *−0.472* | *−0.750* | *−44.00* | *−0.12* | *12/16* |

### Pool 1 (direction only)

| arm | realised N | positions | cost-adj CAGR | excess | **Δ vs base** | maxDD | ΔDD | blocks |
|---|---|---|---|---|---|---|---|---|
| baseline (uncapped) | 11.72 | 522 | 12.515 | +1.352 | — | −40.41 | — | 12/16 |
| n8 | 6.23 | 301 | 10.532 | −0.631 | **−1.983** | −46.19 | −5.78 | 10/16 |
| n5 | 4.06 | 180 | 10.246 | −0.917 | **−2.269** | −41.95 | −1.54 | 10/16 |
| n3 | 2.43 | 85 | 13.759 | +2.596 | **+1.244** | −38.06 | **+2.35** | 9/16 |
| *n14 — NOT TESTED* | *10.92* | *465* | *12.489* | *+1.327* | *−0.025* | *−40.00* | *+0.41* | *12/16* |

**"A book capped at N that runs at ~0.8N" is the accurate description of every treated arm, and it
belongs beside every number above.** Realised means are 6.23 of 8, 4.11 of 5, 2.43 of 3 — because
`per_trade_pct_N = max_total_deployed / N` makes the deployment cap and the slot cap bind at the
same nominal point, so mark-to-market appreciation reaches 70% deployed before the Nth slot fills.
`n3` is not three positions; it is 2.43.

---

## 2. The primary criterion — clause fires

**Best arm on the decisive pool: `n3` at +1.004pp, against a +1.5pp bar and a ~1.08pp noise floor.**
It misses the bar by a third and does not clear the floor at all. `n8` (−2.850pp) and `n5`
(−1.582pp) are worse than baseline outright.

**The curve is non-monotonic, and the two pools agree on the endpoints but not the interior.** Both
pools place `n3` best and both place `n8` and `n5` below baseline; Pool 2 orders n8 < n5 < n3 while
Pool 1 orders n5 < n8 < n3. Per §3's curve-discipline rule, an interior optimum whose location is
unstable across independently-provenanced pools is not a located optimum. The one stable feature is
that the tightest arm is the best one — which, on this evidence, cannot be separated from the
tightest arm also being the one with 84 trades.

**Costs were modelled per arm, as §4.6 required, and turned out immaterial.** FX drag falls
0.268 → 0.183 pp/yr as trade count falls 565 → 84; idle-cash credit rises 0.532 → 0.556 pp/yr. Both
move in concentration's favour exactly as predicted, but the total swing across all arms is ~0.11pp
— under a tenth of the bar. Modelling them was still mandatory (they vary with the treatment, so
deferring them would have been a bias), but unlike the deployment-cap study they decide nothing here.
The reason is that the current live configuration is already very low-turnover: baseline pays only
0.268 pp/yr of FX, so there is little left for concentration to save.

---

## 3. The drawdown prior was wrong, and this is the study's most interesting finding

Part 7 of the selection audit established that momentum ranking sits at the 99th percentile of random
admission on return **and the 98th-worst on drawdown** — concentration buys return and spends
drawdown. The pre-registration therefore expected the drawdown ceiling to be the binding constraint,
said so explicitly, and set Clause B at −60% precisely because Clause A alone would have tolerated
−58.88%.

**Neither clause fires anywhere.** Worst drawdown in the study is `n8` at −48.86%, eleven points
inside Clause B. And the direction inverts at the tight end: `n5` and `n3` are **better** than
baseline on drawdown, in both pools (+4.40 / +3.03pp on Pool 2, −1.54 / +2.35pp on Pool 1). The
drawdown curve is non-monotonic in the same shape as the return curve, worst at `n8`.

**Why this is not a contradiction of Part 7.** Part 7 varied *which* candidates fill a roughly fixed
number of slots; this study varies *how many* slots exist at fixed deployment. Those are different
manipulations. A plausible mechanism — offered as a hypothesis, not a measured result — is that the
concentrated arms trade far less (84 vs 565 positions) and therefore ride through drawdowns that
baseline stops out of and re-enters. It is consistent with the exit-mechanics finding that this
system's edge lies in not churning what it holds. It has not been tested here and should not be
reported as though it had.

What this does mean concretely: **the honest prior that "3 positions could plausibly breach any
reasonable ceiling" was wrong**, and the arm most likely to have been decided on the ceiling was
instead decided on the primary criterion.

---

## 4. Outcome concentration — where `n3` fails independently of the bar

| | n8 | n5 | **n3** | baseline |
|---|---|---|---|---|
| positions | 298 | 178 | **84** | 565 |
| top-3 trades' share of own P&L | 48.07% | **62.12%** | **71.25%** | 25.47% |
| top-10 trades' share | 92.04% | 108.27% | **115.70%** | 64.04% |
| largest single ticker's share | 30.27% | 38.91% | **59.99%** | 16.98% |
| distinct tickers ever held | 146 | 97 | **62** | 238 |

All figures from this run's own trade files. Baseline is itself concentrated — this system's edge has
always lived in a handful of names (the selection audit's Part 1 measured a top decile carrying 257%
of P&L on a much larger trade population) — but `n3` is concentrated by a further factor of nearly
three on the top-3 measure and 3.5× on the single-ticker measure.

**`n3`'s entire result is three trades and one name.** Its top three positions carry 71.25% of its
total P&L, and NVDA alone carries 59.99%. Pool 1 agrees directionally (53.66% top-3). Per the
walk-away sentence, an otherwise-clearing arm above the 50% threshold has its pass **downgraded to
"not robust — driven by a small number of trades"**. `n3` does not clear the bar, so this is not what
decides the study — but it is the reason a +1.004pp reading should not be treated as a near-miss
worth chasing. `n5` also exceeds the threshold (62.12% / 54.21%).

Stated plainly, as the instruction asked: **this is a lottery ticket, not a strategy.**

---

## 5. Single-ticker dominance PASSED — and the pass is worthless here

Every arm passes the 0.40 ceiling (worst: `n3` at 0.2438 on NVDA). **That pass is an artefact of the
metric's denominator, not evidence of diversification**, and it belongs in this project's standing
"a check that reports clean for a reason unrelated to the thing it was checking" class.

The metric is `max_t |Δ_t| / Σ_t |Δ_t|` over per-ticker P&L differences from baseline. At `n3` the
union spans **227 contributing tickers** — overwhelmingly names *baseline held and `n3` did not*, each
contributing a non-zero `|Δ_t|`. The denominator is therefore inflated by the arms being near-disjoint,
which is exactly the condition under which the arm is *most* concentrated. So the guard reads 0.24
"diversified" for an arm whose own P&L is 59.99% one ticker.

**The metric was specified for comparing arms with largely overlapping trade populations** (its
calibration reference, the relative-rank/AMAT precedent, had thousands of shared trades). Applied to
a 84-vs-565 comparison it cannot do the job its ceiling implies. The binding concentration evidence
here is the within-arm top-3 and top-ticker shares in §4, not this guard. Recorded rather than
quietly relied on.

---

## 6. The exact-key paired instrument is structurally INAPPLICABLE to this study

§4.7 predicted the shared-key count would fall below the 100 floor for `n3` and `n5`. It did — 27 and
57 on Pool 2 (28 and 75 on Pool 1); only `n8` clears the floor (133 / 139). **But the deeper finding is that the
instrument carries no information here at all, at any sample size:**

**Every single shared trade is byte-identical. 133/133, 57/57, 27/27 (Pool 2); 139/139, 75/75, 28/28
(Pool 1).**

That is forced by construction. A slot cap changes *which* positions are held; it never changes how a
held position behaves. If both arms admit the same ticker on the same date, they compute the same
stop from the same ATR, hit the same exit condition on the same day, and realise the identical
`pnl_pct`. Exact-key pairing can only ever return "no difference."

This is a genuine methodological result, not a shortfall of this study's sample: **for any pure
admission-count or admission-ordering change, the exact-key paired instrument is inapplicable in
principle** — unlike the exit-mechanics sweep, where the max-holding dial changed exit dates on shared
keys and the instrument had something to measure. Any future study of this shape should plan for the
disagreement-population comparison as its primary per-decision instrument from the start.

### What the applicable instruments say

**Disagreement-population comparison (§4.7 fallback 1)** — arm-only vs baseline-only admissions:

| | arm-only n | arm-only win% / mean P&L% | baseline-only n | baseline-only win% / mean P&L% | floor met |
|---|---|---|---|---|---|
| n8 (P2) | 165 | 27.88% / +9.41% | 432 | 31.25% / +13.14% | yes — **arm worse** |
| n5 (P2) | 121 | 33.88% / +18.81% | 508 | 29.72% / +13.70% | yes — arm better |
| n3 (P2) | 57 | 35.09% / +28.02% | 538 | 28.81% / +12.75% | **no — below floor** |
| n8 (P1) | 162 | 32.10% / +7.86% | 383 | 31.33% / +13.58% | yes — **arm worse** |
| n5 (P1) | 105 | 31.43% / +10.33% | 447 | 29.53% / +13.93% | yes — **arm worse** |
| n3 (P1) | 57 | 35.09% / +23.65% | 494 | 29.15% / +14.01% | **no — below floor** |

`n3`'s unique admissions look markedly better in both pools — and at n=57 that is **below the
pre-registered 100 floor and is therefore reported as a bare count and not interpreted**, in either
direction, exactly as §4.7 committed in advance. Given §4 shows three trades carry 71% of that arm's
P&L, a 57-position mean is precisely the statistic a pre-registered floor exists to stop anyone
reading. `n5` clears the floor and **disagrees between pools** (better on Pool 2, worse on Pool 1).
`n8` clears the floor and is worse in both.

**Rank-at-admission (§4.7 fallback 2 — the primary corroborating instrument, valid at any trade
count).** Median within-day rank percentile of admitted positions:

| pool | baseline | n14 | n8 | n5 | n3 |
|---|---|---|---|---|---|
| Pool 2 CLEAN | 4.65% | 2.38% | 2.04% | 1.52% | **1.49%** |
| Pool 1 | 4.04% | 2.41% | 2.50% | 1.79% | **1.72%** |

**Monotone in both pools: tighter arms genuinely admit from further up the same ranked list.** The
mechanism does exactly what the design claims. This clause of the walk-away sentence does not fire —
the arms fail on outcome, not on failing to express the intended behaviour.

**Displaced admissions (§4.7 fallback 3).** In all three tested arms, in both pools, the baseline
positions the slot cap turned away performed *worse* than those it kept (e.g. `n3` Pool 2: displaced
405 @ 28.4% win / +11.26% mean vs retained 160 @ 30.0% / +15.73%) — mildly favourable to the cap.
**Read with caution and not relied on**: the turned-away list is truncated to the 50 highest-ranked
candidates per date (disclosed, not silent), "retained" includes dates on which the cap never bound,
and the sign **inverts for `n14`** (displaced 95 @ 41.05% / +21.24% vs retained 470 @ 26.38% /
+10.77%). An instrument that reverses between adjacent arms is not carrying a reliable signal.

---

## 7. Overlap — low, and structural, exactly as predicted

| | exact-key Jaccard | ticker Jaccard | top-decile Jaccard | observed shared | null mean ± sd | percentile |
|---|---|---|---|---|---|---|
| n8 (P2) | 0.182 | 0.580 | 0.181 | 133 | 79.5 ± 3.9 | 100th |
| n5 (P2) | 0.083 | 0.390 | 0.074 | 57 | 36.3 ± 3.1 | 100th |
| n3 (P2) | 0.043 | 0.261 | 0.049 | 27 | 14.8 ± 2.6 | 100th |

**Every observed overlap sits at the 100th percentile of its own randomisation null** — roughly
1.7–1.9× chance. Low raw overlap is forced by construction (84 positions cannot overlap much with
565) and the arms select the *same* names far more often than random assignment would. Per §4.5, this
is read as structural, not as evidence the arms differ in selection. The null did its job: without
it, a 0.043 Jaccard would have looked like a large effect.

---

## 8. Block-pass count — corroborating only, and non-discriminating as expected

Pool 2 CLEAN: baseline 11/16, n8 10, n5 12, n3 9, n14 12. Every delta is within the measured
3-block resolution floor, so none is read as evidence either way. Notably `n3` — the best arm on the
primary criterion — has the *worst* block count, one more reminder that block deltas of this size
carry no reliable sign.

---

## 9. The walk-away sentence, clause by clause

1. **Realised mean closer to baseline than to own target** — **FIRES for `n14`** (10.91: 0.87 from
   baseline's 11.78, 3.10 from its own 14). Reported NOT TESTED, never as a null. Does not fire for
   n8/n5/n3.
2. **Deployment diverges >4pp** — does not fire. Worst is −1.92pp; every arm is inside the 2pp clean
   tier.
3. **Fewer than 50 positions** — does not fire. Minimum is 84 (`n3`).
4. **No arm clears +1.5pp on Pool 2 CLEAN** — **FIRES.** Best is `n3` at +1.004pp. **This clause
   alone decides the study.**
5. **Drawdown Clause A (>15pp worse) or Clause B (worse than −60%)** — does not fire anywhere.
6. **Top-3 trades >50% of an otherwise-clearing arm's P&L** — **FIRES for `n5` (62.12%) and `n3`
   (71.25%)**; moot for adoption since neither clears the bar, but recorded because it is why
   `n3`'s reading is not a near-miss.
7. **Single-ticker dominance >40%** — does not fire (worst 0.244), but see §5: the pass is an
   artefact of the metric's denominator in a near-disjoint comparison and should not be credited.
8. **>60% of P&L from pre-2010 entries** — does not fire (worst 6.9%).
9. **Clearing arm not admitting from further up the ranked list** — does not fire; the rank
   instrument is monotone in both pools.

**Two clauses fire (1 and 4, plus 6 conditionally). NOT ADOPTED.** Per §9, the perturbation
robustness test is earned only by an arm that fires no clause; none qualifies, so it was not run.

---

## 10. What this closes, and what it does not

**Closes.** The position-count study left open whether genuine tight concentration helps or hurts,
and named a hard slot count as the design needed to answer it. That design has now been built,
verified byte-identical to production at its degenerate setting, and run — **with deployment parity
actually achieved at three concurrent positions (63.50% vs baseline's 65.10%), which is what the
earlier attempt could not do.** The answer is that concentration to 8, 5 or 3 slots does not deliver a
detectable return improvement on this system: two of three tested arms are worse than baseline, the
third is inside the noise floor and is 71% three trades.

**The question is answered as well as this system can answer it, and better than before.** The
combined verdict of the two studies is that capital deployment and concentration are both settled
levers: `per_trade_pct` cannot express concentration at all, and a hard slot count expresses it
cleanly and buys nothing.

**Does not close.** (a) Whether the drawdown *improvement* at n5/n3 is real is untested — it was a
one-sided ceiling here, never a criterion, and promoting it now would be exactly the metric-switching
pre-registration exists to prevent. It is a candidate for a separately pre-registered study, with the
84-trade sample problem addressed at the design stage. (b) The ~0.8N mechanism means no arm tested a
book of *exactly* N positions; a design that decoupled the slot budget from `max_total_deployed / N`
would test something slightly different. (c) Nothing here speaks to concentration at N between 8 and
baseline's 11.78, where `n14` was uninformative.

---

## 11. Method notes worth carrying forward

1. **The exact-key paired instrument cannot work for a pure admission-count or admission-ordering
   change** — every shared key is byte-identical by construction (§6). Plan for the
   disagreement-population comparison as primary, and add a decision-level instrument such as
   rank-at-admission that stays valid at any trade count.
2. **`max_t |Δ_t| / Σ_t |Δ_t|` dominance is diluted to uselessness when the arms are near-disjoint**
   (§5). Pair it with a within-arm concentration measure whenever trade counts differ by more than a
   few times.
3. **A `holding_days` column in this codebase is CALENDAR days.** Deriving mean concurrent positions
   as `trades × mean_hold ÷ trading_days` overstates it by ~45% (§1a of `GATE_REPORT.md`). Derive
   occupancy from a trading-day index instead, and cross-check it against a panel counter.
4. **Do not widen a tolerance to absorb a rounding artefact.** The §7.5 size check's nine £0.01
   misses were fixed by demanding exact reproduction within the input's known rounding interval; a 1p
   tolerance would have left the check permanently blind to a real 1p sizing error.

---

## 12. Provenance

**Backtest convention**: continuous walk, 2007-01-03 → 2026-08-07, every arm from an identical
£50,000 start; no train/test boundary, so reset-at-boundary is not applicable. **Deployment-cap
accounting**: mark-to-market throughout. **Database**: local research copy in the dedicated worktree
`C:\GitHub\investment-app-slotcount`, `price_data` through 2026-08-07, 612 CLEAN, 608 usable Pool 2
CLEAN / 500 Pool 1, verified fresh by direct query at run time. **No database writes.**
`^SP500TR`/`^IRX` reused read-only from the exit-mechanics study's committed cache — deliberately the
same benchmark series the reproduced baseline was measured against. **Costs**: FX 0.30% round trip on
total notional, annualised against mean account balance; idle-cash credit at realised idle fraction ×
mean `^IRX`.

**Full trade-level output preserved** per this project's standing requirement:
`trades_{pool}_{arm}.csv`, `panel_{pool}_{arm}.csv`, `admissions_{pool}_{arm}.csv`,
`slotfull_{pool}_{arm}.csv`, `rejections_{pool}_{arm}.csv`, plus `step2_verification.json`,
`step2b_decision_parity.json`, `step4_gate.json`, `step5_instruments.json`.
