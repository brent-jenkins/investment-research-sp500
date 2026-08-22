# High-threshold trailing stop — pre-registration (2026-08-22)

**PRE-REGISTRATION ONLY. Nothing built, nothing run, no cell executed, no database touched.**
Single variable: **trailing-stop activation threshold**. Everything else is held at the current live
configuration.

**Two specification questions in §4.3 and §4.4 need a ruling before anything is built.** §4.3 in
particular is not cosmetic — under the exit sweep's existing implementation the arms would test a
mechanism roughly 1.3× to 3× *tighter* than intended at the moment of activation, tightening further
as a position runs, which would manufacture a decisive-looking negative for a reason unrelated to
the hypothesis. That is the standing failure class, and it is avoidable by deciding now.

---

## RULINGS — given 2026-08-22, BEFORE any cell was run, and now fixed

Recorded here rather than folded silently into §4 so the record shows *when* they were fixed. Neither
is revisited after seeing results.

- **§4.3 — ATR is RECOMPUTED AS OF THE CURRENT DATE**, not frozen at entry. The trail level is
  `hwm − 3.0 × ATR(t)`, using the `atr` column already present in each cached frame. Rationale, which
  is arithmetic rather than preference: a frozen-entry-ATR trail at B200 applies an effective
  **1.0×-equivalent** width to the strategy's best positions — a multiplier the sweep already
  established is materially worse than 3.0×, non-monotonically — so the arm would test "tight stop
  applied only to the right tail" while claiming to test "activation threshold." Current-date ATR
  keeps "3.0× ATR" meaning the same economic thing at +200% as at entry, and the study remains one
  variable.
- **§4.4 — the entry stop remains live after activation.** Effective stop is
  `max(entry_stop, trail)`. A decision, not an implementation accident.

**Consequence for verification**: V1's degenerate parity is unaffected (with `trail_activation=None`
no trail is ever armed, so the ATR source is never consulted), but the four reused unit tests were
written against the frozen-entry convention and **must be re-derived for current-date ATR before
they can be trusted** — their synthetic frames pin `atr` at a constant 1.0, so tests (a)–(d) are
expected to carry over unchanged, and that expectation must be *verified*, not assumed. Added as V7.

---

## 0. The gap this tests, and what it does not re-litigate

The exit-mechanics sweep (2026-08-11/12) tested trailing activation at **+5%** and **+10%**. Both
cost **~5.4pp**, collapsing the winning construction to barely above baseline, and the mechanism was
diagnosed precisely: `TRAILING_STOP` took 1,420 of 2,388 exits, trade count rose 565 → 2,388, mean
hold fell 149 → 42 days, and **win rate ROSE (28.85% → 42.46%) while returns fell** — Arm C's finding
replicated. Cutting winners short produces more frequent, smaller wins in a strategy that lives on
the right tail. **That result stands and is not re-litigated here.** No arm below +30% is proposed.

**The untested case is structurally different.** A stop fixed at entry becomes *functionally inert*
once a position has run far. The live MRNA position is **+136% with its stop 176% below current
price** — in practice it has no stop at all. Generalised: **this system has protection on every
position except the ones that have run furthest**, which are also the positions with the most
accumulated gain to give back. A high activation threshold touches only that handful and leaves the
ordinary right-tail machinery untouched. The +5%/+10% results, which fired on 59% of all exits, say
nothing about it.

**The motivating example is explicitly barred from counting as evidence (§9).**

---

## 1. Baseline — the current live configuration

| parameter | value | provenance |
|---|---|---|
| admission ordering | **`relative_rank`** | adopted 2026-08-11 |
| `atr_stop_multiplier` | **3.0** | adopted; verified live on the server 2026-08-22 |
| `stock_max_holding_days` | **0** (disabled) | adopted; verified live 2026-08-22 |
| trailing stop | **none** | — |
| `per_trade_pct` / `max_total_deployed` | 0.05 / 0.70 | unchanged |

This is the exit sweep's `s3.0` cell, now live. **Preserved reference set: Pool 2 CLEAN 565 trades /
£348,548.74** (`research/exit_mechanics_prereg_2026-08-11/trades_pool2_clean_s3.0.csv`), Pool 1 from
`trades_pool1_s3.0.csv`.

**Note the baseline has moved since the sweep.** The sweep's baseline was `h30` (2.0×/30-day) and
`s3.0` was its *winner*. Here `s3.0` **is** the baseline. Every Δ in this study is measured against
£348,548.74, not against £166,830.11. A figure quoted from the sweep's tables without checking which
baseline it was relative to will be wrong by the full +6.443pp.

---

## 2. The arms — one variable, four levels plus control

| arm | activation | trail distance | ratchet |
|---|---|---|---|
| **A (baseline)** | none | — | — |
| **B30** | **+30%** | 3.0× ATR | up only |
| **B50** | **+50%** | 3.0× ATR | up only |
| **B100** | **+100%** | 3.0× ATR | up only |
| **B200** | **+200%** | 3.0× ATR | up only |

Trail distance is **fixed at the live 3.0× multiplier and is NOT swept** — one variable. Activation
is measured on the **close** against entry price. Once activated, the trail never deactivates and
never lowers.

---

## 3. STAGE 0 — REALISED EFFECT SIZE, REPORTED FIRST, BEFORE ANY RETURN FIGURE

**This is the gate that determines whether the rest of the study means anything, and it is reported
before any return number is computed, not alongside them.** Read-only, from the preserved baseline
trade sets — no cell is run to produce it.

### 3.1 What is measured

For every one of the 565 (Pool 2 CLEAN) baseline positions, the **peak close-to-entry gain reached
at any point during the holding period**:

```
peak_gain = max(close[t]) / entry_price - 1,  for t in [entry_date, exit_date]
```

**Close, not high** — matching the HWM convention the arm itself uses (§4.2), so the population
measured is exactly the population the arm could act on.

Reported per threshold T ∈ {0.30, 0.50, 1.00, 2.00}:

| quantity | why |
|---|---|
| **n_reached** — positions with `peak_gain ≥ T` | the affected population's size |
| as % of 565 | scale |
| their share of total baseline P&L | what is at stake |
| median days from entry to first crossing | how much of the hold is post-activation |
| median gain *given up* between peak and actual exit | **the size of the prize** — the giveback a trail could plausibly capture |
| distribution of baseline exit reasons within the set | what currently closes them |

### 3.2 THE MATERIALITY FLOOR — fixed now, before any number is seen

An arm is **TESTED** only if **both**:

- **n_reached ≥ 30**, and
- **n_fired ≥ 10** actual `TRAILING_STOP` exits in that arm's own run.

Otherwise the arm is reported as **NOT TESTED**, and — the n5/n10 lesson from the position-count
study, applied verbatim — **its return figure is not interpreted as a null, is not reported as
evidence of no effect, and is not used to argue the mechanism does not work.** An arm that touches
three positions has not tested anything; saying so is the honest result, and the position-count
study's `n5`/`n10` arms are the precedent for reporting exactly this rather than a spurious null.

**Why 30 and 10.** 30 is the population floor below which a win-rate or mean-outcome comparison in a
strategy whose top 10 trades carry 64% of P&L is dominated by one or two positions. 10 is the firing
floor below which a single trailing exit moves the arm's entire P&L delta. Both are deliberately
*low* bars — they are floors for "is this measurable at all", not for "is this well-powered".

### 3.3 A concentration guard, always reported

For every arm, the **share of the arm-vs-baseline P&L difference attributable to the single largest
contributing position**, computed as `|Δpnl_gbp|` for that position over `Σ|Δpnl_gbp|` across all
differing positions. **Ceiling 40%**, the same figure and the same reasoning as the forward
shadow-comparison guard. Absolute, not signed, so a large protected gain and a large forgone one
cannot cancel to zero and report a concentrated sample as diverse.

**With fewer than three contributing positions some position necessarily exceeds 40% arithmetically.
That is the correct answer, not an artefact** — a two-position sample cannot separate a rule from
one lucky exit — and it means this guard doubles as a diversity floor alongside §3.2's raw counts.

### 3.4 The stated limit of Stage 0

**Stage 0 measures the baseline population and is therefore an UPPER BOUND on the affected set, not
a prediction of `n_fired`.** Once a trail fires, that position's capital returns early and the
downstream admission sequence changes for the rest of the walk — so the arm's own realised
population will differ. `n_reached` is a read-only property of a preserved trade set; `n_fired` can
only come from the run. Both are reported; neither substitutes for the other.

---

## 4. Mechanism specification

### 4.1 Reuse, not rewrite

`research/exit_mechanics_prereg_2026-08-11/sweep_engine.py:simulate_exit_sweep()` already implements
activation, ratcheting, the anti-lookahead ordering, and `TRAILING_STOP` labelling, and its four unit
tests in `step2_verify.py` already cover them. **Both are reused verbatim.** No trailing logic is
rewritten, and nothing is added to `analysis/decision.py` — this is research-only, exactly as the
sweep held it.

### 4.2 The anti-lookahead ordering — load-bearing, restated so it cannot drift

Each day, in this exact order:

1. **CHECK** the stop against today's **LOW**, using the trail level as it stood at **yesterday's
   close**.
2. Only if the position survived: check **activation** against today's **CLOSE**.
3. Update the high-water mark from today's **CLOSE**.
4. Ratchet the trail **up only**.

Doing 3–4 before 1 lets a single day's range both tighten the stop and then trigger it, manufacturing
exits no sequentially-executed trailing stop could produce — invisibly, since the result would look
like an ordinary `STOP_LOSS`. Unit test **(d)** is the direct demonstration and must pass
non-vacuously.

### 4.3 ⚠ SPECIFICATION QUESTION 1 — *which* ATR? This materially changes what is being tested

The existing implementation trails at `hwm − k × atr_at_entry` — **ATR frozen at entry**. For the
+5%/+10% thresholds the sweep tested, that is nearly harmless. **At these thresholds it is not**, and
the arithmetic is exact rather than speculative:

> trail width as a fraction of current price
> = `3 × ATR_entry / price_now`
> = `3 × (ATR_entry / price_entry) × (price_entry / price_now)`

So at the moment of activation the trail is narrower than the entry stop by exactly the activation
multiple:

| arm | tightening at activation | and it keeps tightening as the position runs further |
|---|---|---|
| B30 | **1.3× tighter** | |
| B50 | **1.5× tighter** | |
| B100 | **2.0× tighter** | |
| B200 | **3.0× tighter** | at +722% (NVDA's real held gain) it is **8.2× tighter** |

**This is not a trailing stop tightening against its high-water mark — which is the intended
mechanism — it is the trail decoupling from volatility, which is an artefact.** The sweep's own
finding is that **3.0× is the interior optimum and 2.0× is materially worse** (+6.443 vs +3.634,
non-monotonic, both pools). An at-entry-ATR trail at B200 applies an effective **1.0×-equivalent**
width to the strategy's very best positions — a stop already known to be worse than the one those
positions survived, applied only where the tail lives.

**An arm so specified would very likely return a decisive negative for a reason that has nothing to
do with the hypothesis.** That is the standing "check that reports clean for a reason unrelated to
the thing it was checking" class, arriving before the run rather than after it, which is the only
point at which it is cheap.

**RECOMMENDED: recompute ATR as of the current date** (the same `atr` column already present in each
ticker's cached frame), so the trail stays volatility-proportional and "3.0× ATR" means the same
economic thing at +200% as at entry. This is a **specification choice, not a second swept variable** —
the multiplier stays fixed at 3.0 in every arm.

**Alternative if you prefer the frozen convention**: keep `atr_at_entry` but state in the report that
the arms test a *jointly* tightening trail, and that a negative result cannot be attributed to
activation threshold alone. I do not recommend this — it confounds the one variable the study exists
to isolate.

**Needs your ruling. Whichever is chosen is fixed before any cell runs and is not revisited after
seeing results.**

### 4.4 ⚠ SPECIFICATION QUESTION 2 — does the entry stop remain live after activation?

Proposed and recommended: **yes**. The effective stop is `max(entry_stop, trail)`, which is what
ratchet-up-only already produces mechanically, since the trail only ever raises the level. Stated
explicitly so it is a decision rather than an accident of the implementation. Under this rule a
position that activates and then retraces exits at the *trail*, never below the entry stop, and
`TRAILING_STOP` vs `STOP_LOSS` labelling follows the existing `trail_has_raised` test.

---

## 5. Criteria

### 5.1 PRIMARY — cost-adjusted excess return over `^SP500TR`, Δ vs baseline's own excess

**Required margin: +1.5pp**, the project's standing bar — 1.4× the measured ~1.08pp cost-adjusted
CAGR noise floor, and the same bar the exit sweep, gate-width, position-count, deployment-cap and
relative-ranking studies used. Consistency matters more than re-deriving it.

**A null (|Δ| < 1.08pp) is the modal expected outcome and is NOT adoption.** It is, however,
genuinely informative here in a way it usually is not: it would mean high-threshold protection is
approximately *free* at portfolio level, which is a different and more interesting finding than the
+5%/+10% arms' decisive ~5.4pp cost.

**THE PRIMARY CRITERION MAY BE STRUCTURALLY INCAPABLE OF RESOLVING THIS QUESTION, and that is
pre-registered rather than discovered afterwards.** If B200 touches ~20 of 565 positions, the largest
portfolio-level effect it could possibly produce may itself sit inside the ±1.08pp floor. A null
would then be uninformative about the mechanism and informative only about the instrument — the
identical shape as the position-count study's deployment guard passing `n5`/`n10` cleanly while being
structurally unable to see the failure those arms actually had. **This is why §3 comes first and why
§5.2 is designated the actual question**: if the affected population is small, §5.2 is the only
instrument that can speak, and the primary criterion must be reported as non-discriminating rather
than as a null.

### 5.2 THE ACTUAL QUESTION — paired outcome distribution on the threshold-reaching population

Reported **separately from, and given equal prominence to, the primary criterion.**

Pair on `(ticker, entry_date)` between baseline and arm, restricted to positions whose baseline
`peak_gain ≥ T`. A shared key is **capacity-uncontaminated by construction**, so this comparison
carries zero cascade effect regardless of what else differs. Instrument reused from
`research/measurement_toolkit_2026-08-08/step5_paired_comparison.py`.

For each paired position, report the decomposition:

| quantity | definition |
|---|---|
| **byte-identical** | arm never diverged — trail armed but never fired. **Counted separately and NEVER scored as "not better"** (the exit sweep's 11.96% figure was 235 identical trades scored as failures) |
| **genuinely diverged** | trail fired; baseline exited by another reason |
| **gain PROTECTED** | diverged positions where arm exit > baseline exit. Σ and distribution |
| **upside FORGONE** | diverged positions where arm exit < baseline exit. Σ and distribution |
| **net** | protected − forgone, in £ and pp, with the count each way |

**Both sides are reported in full. The headline for this instrument is the net together with the two
gross components, never the net alone** — "the trail protected £X" and "the trail forgave £Y" are
the two halves of the actual question, and a net figure conceals which mechanism dominates.

**Pre-registered floor**: fewer than **10 genuinely diverged** paired decisions is reported as
directional only and explicitly not promoted to strong evidence — the discipline the exit sweep held
to with its own 91-decision subset.

### 5.3 Capacity-turnover, reported separately from the direct effect

Mandatory under the standing capacity-turnover requirement, and **unusually important here**: the
sweep records that under unlimited holding **capacity locks up early** — 144 entries in 2008, 131 in
2009, then 6 in 2010 and single digits in several later years. A trailing exit therefore does not
merely truncate one position; **it opens a slot that has been shut for a decade.** The
capacity-turnover effect could plausibly exceed the direct effect and could run in either direction.

Reported separately, never blended into one headline:

1. **Direct effect** — §5.2's paired population, the positions the trail actually acted on.
2. **Capacity-turnover effect** — total trade count per arm; count of admissions the arm made that
   baseline never made; their aggregate P&L; and the date distribution of the freed slots.

**If the headline gap traces mainly to new admissions rather than to the directly-affected positions,
the arm has not demonstrated the hypothesis** — it has demonstrated that freeing locked capacity has
value, which is a different and separately-testable claim. That is walk-away clause (5).

### 5.4 Drawdown — one-sided ceiling, stated directionally

**Ceiling: peak-relative max drawdown worsening by more than 15pp vs baseline fires a walk-away
clause.** Consistent with the exit sweep.

**Directional statement, fixed now: drawdown is EXPECTED to improve**, since protecting run-up gains
is the mechanism's whole purpose. **An improvement is therefore not evidence in the arm's favour and
must not be promoted to a criterion after the fact.** The momentum-smoothness study is the direct
precedent: its drawdown improved monotonically with filter strength in both pools, and promoting that
after the primary criterion failed would have been exactly the metric-switching pre-registration
exists to prevent. Drawdown here is a ceiling against harm, nothing more.

**A drawdown that WORSENS is the diagnostically interesting case** and is reported prominently if it
occurs — it would indicate the trail is realising losses that baseline rides out, or that
capacity-turnover has concentrated the book.

### 5.5 Turnover and costs — modelled per-arm, not deferred

FX cost scales with turnover and turnover is expected to vary across arms, so **the cost is a
treatment effect, not a constant, and deferring it would bias toward whichever arm trades less.**
Modelled per-arm using the established template: `Σ(real position_gbp) × the T212 fee` for FX, and
realised-idle-% × the mean `^IRX` rate for idle-cash credit, both annualised as a % of mean account
balance and netted against reported CAGR. Reported per arm: total trades, `TRAILING_STOP` share of
exits, full exit composition, mean holding days, FX pp/yr, idle-cash pp/yr.

**Exit composition must shift in the direction the dial predicts** (`SIGNAL_OFF`/`END_OF_DATA` →
`TRAILING_STOP`). A headline that moves without its own composition shift is treated as a cascade
artefact, not a result — walk-away clause (6).

### 5.6 Top-decile trade overlap — central here, not routine

**These arms act specifically on the right tail, so this is reported alongside every headline gap,
not in an appendix.** Per arm vs baseline:

- shared `(ticker, entry_date)` keys, as % of each arm, and Jaccard
- **the same, restricted to each arm's own top-decile-by-P&L trades**
- share of each arm's total P&L coming from non-shared trades
- **explicit identification of which top-decile baseline positions the arm truncated, and by how
  much** — the single most direct read on whether the mechanism damages the population that carries
  the strategy

**Low overlap is not itself a finding.** The standing cascade-amplification note applies: a raw
trade-level diff systematically overstates how different two arms' decision logic is. If a large
trade-level diff coexists with a small aggregate effect, the correct read is "cascade-amplified", not
"materially different". Where overlap looks anomalous, the exit sweep's **randomization null**
(conditioned on the dates the sparse arm admitted on, drawing from each date's real gate-passing
eligible set, 1,000 draws) is the pre-registered test — the same test that showed its own alarming
11.96% figure was 4× *higher* than chance.

---

## 6. Verification — before any cell result is trusted

| # | check | pass condition |
|---|---|---|
| V1 | **Degenerate parity** — arm A with `trail_activation=None` | **byte-identical** to the preserved `s3.0` set, 0 row-level mismatches on a canonical `(ticker, entry_date, exit_date)` sort |
| V2 | Zero `TRAILING_STOP` exits when activation is off | exactly 0, both pools |
| V3 | The four trailing unit tests, **non-vacuously** | 4/4 pass, `_require()` confirms every test produced ≥1 trade |
| V4 | Anti-lookahead demonstration, test (d) specifically | day-3 low above yesterday's trail does **not** exit |
| V5 | Activation is never retroactive | no position shows `trail_activated` with `peak_gain < T` |
| V6 | Ratchet monotonicity | recorded `final_stop ≥ initial_stop` for every trailing exit, sampled and recomputed by hand for ≥20 |
| V7 | **Unit tests re-derived for current-date ATR** (per the RULINGS above) | 4/4 still pass non-vacuously, and the expectation that a constant-`atr` fixture makes them carry over unchanged is **verified, not assumed** |

**V3's non-vacuity clause is not boilerplate.** The sweep's first unit-test run passed **vacuously on
empty trade lists** because `is_member()` rejected every synthetic ticker, and it surfaced only
because a later test happened to index `[0]` and raise. **A test that has not been observed producing
a non-empty result has not been observed at all.**

---

## 7. Sanity gate

Baseline must reproduce the live configuration's known figures on Pool 2 CLEAN: **565 trades /
£348,548.74**.

**Compared as trade SETS, not totals** — a matching total with a differing trade set is the failure
mode a total cannot see, and this project has hit reference-figure decay twice (once from a
since-deleted worktree's DB snapshot, once from one further day of ingestion revising `END_OF_DATA`
exits). A small, fully row-diagnosed discrepancy traceable to a legitimate data update is acceptable
and must be **diagnosed row-by-row and reported**, never waved through on a matching total.

---

## 8. Pools and database state

- **Pool 2 CLEAN** (`get_research_stock_candidates()`) — **decisive**.
- **Pool 1** (`get_live_stock_candidates()`) — **direction only**, per the dual-provenance standing
  requirement.
- A **cross-pool sign disagreement is reported as unreconciled**, not resolved toward the more
  convenient pool. Pool 1 is survivors-only by construction and is a structurally different universe,
  not merely a smaller one.
- Database state **reconfirmed by direct query at run time** — CLEAN ticker count, `price_data` max
  date, usable pool sizes — never inherited from the sweep's own recorded state or from a prior
  session. `^SP500TR`/`^IRX` read-only into a research-local cache.
- **Backtest convention: continuous walk**, 2007-01-03 → the reconfirmed max date, all arms from the
  identical £50,000 start. No train/test boundary exists, so reset-at-boundary does not apply.

---

## 9. THE MOTIVATING EXAMPLE IS BARRED FROM COUNTING AS EVIDENCE

MRNA prompted this study. **A finding that the trail would have protected MRNA is not evidence the
mechanism works** — it is evidence the mechanism was specified by looking at MRNA. Fixed now:

- **No threshold is chosen, adjusted, or defended by reference to any single position.** The four
  levels are fixed at +30/+50/+100/+200 before any run and are not tuned afterwards.
- MRNA's own outcome under each arm may be reported **as illustration only**, explicitly labelled as
  such, and **carries no weight in any criterion**.
- The same bar applies to NVDA, AVGO and the other named top-10 contributors — a per-name result is
  narrative, never evidence.

This is the standing generalisation applied in advance: **passing the case a guard was designed for
is not evidence it covers the class that case belongs to, and the pass is exactly what stops anyone
looking further.** The population-level result in §3 and §5.2 is what counts.

---

## 10. Honest priors, stated before any result

| # | prior | confidence |
|---|---|---|
| 1 | **The mechanism truncates the exact population that produces the strategy's return.** The prior is unfavourable. | high |
| 2 | Higher thresholds will do **less damage** than +5%/+10% simply by firing less — a monotone attenuation toward zero rather than a sign change. | moderate |
| 3 | **B200 will likely fail §3.2's materiality floor**, and possibly B100. The most probable outcome of this study is "two arms not tested, two arms null-to-negative." | moderate |
| 4 | **Capacity turnover may dominate the direct effect** given how early capacity locks up, and its sign is genuinely unpredictable. | moderate, low confidence in direction |
| 5 | If any arm helps, **B30 or B50 is the more likely candidate** — enough firings to matter — but those are also closest to the already-failed +10%. The window between "fires enough to measure" and "fires little enough not to damage the tail" may be **empty**. | this is the real question |
| 6 | **Multiple comparisons.** ~60 experiments; one adopted improvement. Four more arms here. A single arm clearing +1.5pp on one pool is weak evidence and would earn a robustness test, not adoption. | high |
| 7 | The structural oddity is **real regardless of the outcome**: a stop that goes inert precisely when a position has most to give back. A null here does not dissolve it — it means *this* remedy does not pay, and the oddity may simply be the price of the right tail. | high |

---

## 11. THE WALK-AWAY SENTENCE

> **An arm is NOT ADOPTED if ANY of the following holds:**
> **(1)** it fails to clear **+1.5pp** cost-adjusted excess return over baseline's own excess on
> Pool 2 CLEAN;
> **(2)** it fails §3.2's materiality floor (`n_reached ≥ 30` **and** `n_fired ≥ 10`) — in which
> case it is reported **NOT TESTED**, never as a null;
> **(3)** peak-relative max drawdown worsens by **more than 15pp** vs baseline;
> **(4)** on the paired threshold-reaching population (§5.2), the arm's own outcomes are **not
> distinguishably better** than baseline's on the same `(ticker, entry_date)` keys — byte-identical
> trades excluded from the comparison, not scored as failures;
> **(5)** the headline gap traces **mainly to capacity-turnover admissions** rather than to the
> directly-affected positions (§5.3);
> **(6)** exit composition does **not** shift as the dial predicts, or a **single position exceeds
> 40%** of the measured difference (§3.3).
>
> **An arm that fires no clause has earned a paired perturbation robustness test — NOT adoption.**
> Real promotion would additionally require the production-module and four-parity-layer work the
> relative-rank promotion required.
>
> **No post-hoc reasoning rescues an arm from clauses (2) through (6).** A plausible mechanism
> explaining why a criterion "shouldn't apply in this case" is precisely what this sentence exists to
> override. The deployment-cap study is the precedent: a coherent leverage-not-selection reading of
> its disqualifying clause was recorded as a hypothesis for a later study and was **not** allowed to
> overturn the verdict.

**If robustness testing is later run**, the `shuffle_tiebreak` dimension is **inert by construction**
in this system — admission sorts on a continuous float, so exact ties never bind — and its draws must
not be counted as confirmations. Both prior studies that used it produced byte-identical values and
one of them counted them as evidence.

---

## 12. Constraints re-confirmed

- **No changes** to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path.
- Trailing logic stays **research-only**, in `sweep_engine.py`. Nothing added to `decision.py`.
- **No database writes of any kind.** `^SP500TR`/`^IRX` read-only into a research-local cache.
- Runs **locally**, in the dedicated worktree `investment-app-trailing-ht`
  (branch `research/trailing-high-threshold-2026-08-22`), never on the server.
- **Nothing adopted, nothing merged, nothing deployed.**

---

## 13. Order of execution, once approved

1. Ruling on **§4.3** (which ATR) and **§4.4** (entry stop after activation).
2. Reconfirm database state and pool sizes by direct query.
3. **Stage 0 (§3) — read-only, reported and reviewed BEFORE any cell is run.** If every arm fails the
   materiality floor at this stage, the study stops here and reports that, which is a legitimate
   outcome rather than a failure to complete.
4. Verification V1–V6.
5. Sanity gate (§7).
6. Arms B30/B50/B100/B200, both pools.
7. Report: **effect size first**, then §5.2's protected/forgone decomposition, then the primary
   criterion, then everything else.
