# Momentum smoothness as a candidacy filter on relative-rank admission — PRE-REGISTRATION (2026-08-10)

**Status: PRE-REGISTERED, NOT RUN.** No arm has been executed, no engine built, no code written
beyond this document. Every number below is a commitment made before seeing any result.

---

## §0 — Motivation, and what this is NOT a re-test of

Part 2 of the selection audit (`research/selection_audit_2026-08-08/REPORT.md`) found
Spearman(momentum-at-entry, `pnl_pct`) = **−0.033** across 3,436 trades — indistinguishable from
zero. The only reliable signal was at the bottom margin (D1, barely above the entry floor, is
unambiguously the worst bucket); above that, momentum decile has no ordering power.

**That is a finding about one signal family, not about entry information in general.** Nearly every
candidate this project has tested is price-derived momentum in some form: absolute momentum,
cross-sectional relative rank, gate width, MA extension, `adj_close` vs `close`. Momentum
*smoothness* — whether a given trailing return was accumulated steadily or in a handful of jumps —
is a genuinely different construction over the same stored data, and has direct published support
(Da, Gurun & Warachka, *"Frog in the Pan: Continuous Information and Momentum"*, RFS 2014, which
proposes an information-discreteness measure and finds momentum built from continuous information
persists better than momentum built from discrete jumps).

**It is also a RANKING refinement, which is the point.** Ranking is the only admission mechanism
this project has shown to work: Arm B sits at the **99th percentile of 100 random-admission draws**
and delivered **+1.993pp** cost-adjusted excess return, robust in direction across **20/20**
perturbation draws. Testing a refinement of the one thing that works is a better use of a study slot
than a sixth independent lever.

**Explicitly not claimed**: that this replicates the published result. Different universe, different
horizon, different construction (a single-name candidacy filter, not a cross-sectional double-sort),
different cost model. The literature motivates the hypothesis; it does not license the conclusion.

---

## §1 — Arms

| Arm | Admission ordering | Candidacy |
|---|---|---|
| **A** (baseline) | Cross-sectional relative rank (12-minus-1), i.e. the relative-ranking study's own Arm B | Current live gate: `passes_entry_gate()` + 2% momentum floor |
| **B1** | Identical to A | A's gate **plus** smoothness ≥ `T1` |
| **B2** | Identical to A | A's gate **plus** smoothness ≥ `T2` |
| **B3** | Identical to A | A's gate **plus** smoothness ≥ `T3` |

**Baseline is Arm B of the relative-ranking study, NOT current production.** Production remains on
`absolute_momentum` by deliberate sequencing (the flip awaits forward shadow evidence). That
sequencing does not constrain this backtest, which runs whichever ranking an arm specifies. Testing
smoothness against the production ranking would answer a question nobody intends to act on.

**Filter form, not blended score — deliberate, and not revisited if the result is null.** A composite
combining rank position with a smoothness term introduces a weighting parameter, and CLAUDE.md
records that V1's blended confluence scoring was abandoned precisely because it was untestable: every
disappointing result invites re-tuning the weight, and no result ever falsifies anything. The filter
has one threshold and no weight. **If the filter shows nothing, the blended form is not a rescue and
will not be implemented** — it is recorded in §11 as a deferred variant purely so a future reader
knows it was considered and why it was declined.

**Insertion point**: exactly one place — the eligible-candidate set that `_admit_group()` sorts,
after `passes_entry_gate()` and `passes_momentum_floor()`, before the ranking sort. Nothing else in
the pipeline is touched: sizing, capacity accounting, every exit reason (`CRISIS_EXIT`, `STOP_LOSS`,
`SIGNAL_OFF`, `MAX_HOLDING`) and the `SIGNAL_OFF` input in particular stay keyed to absolute momentum
in every arm, byte-identical to A.

---

## §2 — The metric, specified exactly (per the reproducibility standard)

### Chosen: (i) signed positive-day fraction. Rejected: (ii) path efficiency.

For a close series over the lookback window ending at date *t*:

```
r_i          = daily simple return, close_i / close_{i-1} - 1,  for the 252 days ending at t
n_pos        = count(r_i > 0)
n_neg        = count(r_i < 0)
N            = 252                      # window length in trading days; zero-return days
                                        # count toward N but toward neither n_pos nor n_neg
momentum_t   = close_t / close_{t-252} - 1        # the existing gate momentum, unchanged

smoothness_t = sign(momentum_t) * (n_pos - n_neg) / N
```

Higher = smoother. This is the negation of Da/Gurun/Warachka's information-discreteness measure
(`ID = sgn(PRET) × (%neg − %pos)`), re-signed so that larger means better, matching every other
ranking quantity in this codebase.

**The filter**: a candidate is excluded from candidacy when `smoothness_t < T`.

### Why (i) and not (ii) — this is not a coin-flip, (ii) is actively wrong for this hypothesis

Path efficiency (`net return / Σ|daily returns|`, i.e. Kaufman's Efficiency Ratio) measures
**directness**, not **smoothness**, and the two come apart exactly on the case this study exists to
isolate. Worked through before choosing:

- **A single +250% jump, flat every other day**: net = 250%, Σ|daily| ≈ 250%, **ER ≈ 1.0 — maximum
  efficiency.**
- **A steady climb, same total, spread over 252 days**: in log terms net = Σ|daily|, **ER ≈ 1.0 —
  also maximum efficiency.**

ER cannot distinguish them, because a single jump *is* the most direct possible path. ER was designed
to separate trending from choppy/mean-reverting, which is a different question. Under (i) the same two
cases separate cleanly: the jump scores ≈ 0 (one positive day out of 252), the steady climb scores
high.

Using (ii) would therefore not merely be a weaker choice — it would score the jumpiest possible
candidate as maximally smooth, i.e. **a filter that reports clean for a reason unrelated to what it is
checking.** That is this codebase's named recurring failure class, and catching it at specification
time rather than after a null result is the entire value of writing the metric down first.

### Lookback window: 252 trading days, matching `MOMENTUM_LOOKBACK_DAYS`

Matches `analysis/signals.py:MOMENTUM_LOOKBACK_DAYS = 252` exactly. Three reasons:

1. **The filter gates candidacy, and candidacy is determined by the 252-day gate momentum** — the
   filter qualifies the same move the gate admits on.
2. **It reuses `_MAX_ANCHOR_GAP_DAYS = 400` with its existing calibration unchanged** (252 trading
   days ≈ 365 calendar days; 400 gives slack for holidays without tolerating a real gap). A different
   window would need a new constant with a new justification, which is a free parameter this study
   does not need.
3. One fewer degree of freedom.

**Stated tension, not hidden**: Arm A *ranks* on the 12-minus-1 value (`close_{t-21}/close_{t-252}`),
which deliberately excludes the most recent month for short-term reversal reasons, whereas this
smoothness window includes it. Judging the smoothness of a slightly different span than the one being
ranked is a real inconsistency. It is accepted here because the filter acts on the **gate**, not the
sort, and the gate is the 252-day value. The aligned `(t-252, t-21)` variant is recorded as deferred
in §11.

### Staleness protection — mandatory, and the specific failure it prevents

A day-counting computation across a genuine data gap **silently returns a real, wrong number instead
of `NaN`** — the identical failure `_MAX_ANCHOR_GAP_DAYS` exists to prevent for momentum (a synthetic
~16-month gap once produced a real +170% momentum reading, treated as a valid signal). A 252-row
window that actually spans four calendar years counts real returns from the wrong period and produces
a perfectly plausible smoothness value.

`smoothness_t` is `NaN` whenever:
- fewer than 252 prior rows exist, **or**
- the window's first date is more than `_MAX_ANCHOR_GAP_DAYS` (400) calendar days before *t*.

**Verified before any arm runs**, using the fixture style already in `tests/test_signals.py`
(`test_momentum_nan_immediately_after_a_real_gap`,
`test_momentum_unaffected_by_a_short_gap_within_tolerance`,
`test_momentum_recovers_once_a_full_gapless_window_exists_past_the_gap`):
1. Synthetic 40-trading-day gap → `NaN` at the seam.
2. Short gap inside tolerance → unaffected, real value.
3. Recovery once a full gapless 252-day window exists past the gap.
4. Real-data scan across all candidate-days in both pools, reporting how often the guard fires. A
   plausible small count is expected; a large one is a finding to explain before trusting any arm.

### `NaN` handling — the degenerate case, decided in advance

**A `NaN` smoothness EXCLUDES the candidate**, and NaN-driven exclusions are **counted and reported
separately** from threshold-driven exclusions.

Reasoning, stated because both directions are defensible: treating unknown as "pass" would let a data
gap silently widen candidacy; treating it as "fail" could let a misfiring guard starve admission and
produce a deployment failure that *looks* like a filter effect. The separate count is what makes the
two distinguishable after the fact.

**A prediction that doubles as a check**: because the gate's own momentum uses the same 252-day window
and the same guard, a candidate with `NaN` smoothness should already have `NaN` momentum and have been
excluded by the gate. **NaN-driven exclusions should therefore be ≈ 0 by construction.** If they are
not, that discrepancy is a defect in the new code, not a property of the data, and must be explained
before any arm's result is trusted.

---

## §3 — Threshold calibration (pre-registered, runs before the arms, produces no outcome data)

Absolute thresholds are chosen — not cross-sectional percentiles — because the hypothesis is an
absolute quality bar ("this move was too erratic"), and because a percentile filter would make §6's
realised-exclusion check **vacuous by construction** (exclusion fixed at the percentile, whatever the
data does).

Absolute thresholds carry the opposite risk: a value picked from intuition could exclude ~nothing (a
no-op arm, the n5/n10 failure) or ~everything in a choppy regime (starvation, the deployment failure).
Both are avoided by a **calibration step fixed in advance**:

1. Compute `smoothness_t` for **every gate-passing candidate-day** across the full 2007–2026 walk, in
   both pools. **No backtest is run. No outcome, P&L, or trade data is touched** — this measures the
   distribution of the independent variable only, which is why it does not compromise the
   pre-registration.
2. Set `T1`, `T2`, `T3` at the **10th, 25th, and 40th percentiles** of that pooled distribution.
3. **Round to 3 decimal places, record the literal numbers in `CALIBRATION.md`, and freeze them
   before a single arm runs.** They are absolute thresholds thereafter, not percentiles — realised
   exclusion will drift away from 10/25/40% across eras, and that drift is an informative outcome
   (§6), not a defect.

Nominal exclusion of 10/25/40% is chosen to bracket a plausible effect while keeping the tightest arm
well short of starvation. Three thresholds plus baseline = **4 arms × 2 pools = 8 runs.**

**Read as a curve, per the gate-width study's discipline.** Monotonic improvement across T1→T3 means
the optimum is **unbracketed** and the study has not found a best value — that will be reported as
such, explicitly, rather than crowning T3 because it happens to be the endpoint. An interior maximum
is the only shape that identifies a threshold.

---

## §4 — Deployment parity as a DESIGN constraint, not a post-hoc guard

**A filter removes candidates, so the specific, named risk is under-deployment.** Two studies have
failed on exactly this — the monthly-shortlist admission redesign (mean deployment ~24pp below
baseline) and cross-sectional monthly (22.5–51pp below) — and in both the attractive portfolio-level
result reduced to "held more cash", not better selection.

**Why this arm is structurally safer, argued in advance and then checked**: unlike both failures, no
arm here gates entries to a periodic rebalance grid. Every day remains an entry-attempt day, and the
capacity walk is skip-and-continue, so a filtered-out candidate frees its slot to the next eligible
candidate *the same day*. The gate-width study — the closest structural analogue, narrowing candidacy
on a daily clock — held deployment within **≤0.5pp** across every arm. The mechanism predicts parity;
it does not guarantee it, because unlike gate-width this filter can remove candidates from the *top*
of the rank order, not just the tail.

**Reported per arm, per pool**: realised mean deployment %, realised mean open-position count, and
their deltas vs A.

**Rule, fixed now**: if an arm's mean deployment falls **more than 2pp below** arm A's, that arm is
reported as **NOT TESTED for selection quality** — not as a failure and not as a null. It would be
testing cash-holding, which is a different question. Mean open-position count is reported alongside
because the position-count study showed deployment can hold while position count moves substantially
(2pp deployment parity across arms whose realised counts ranged 11.8–35.5).

---

## §5 — Primary criterion and margin

**Primary metric**: cumulative **cost-adjusted excess return over `^SP500TR`**, compared against
**arm A's own excess return** (not against production, not against the raw index).

Costs modelled per arm using the established template (Σ real `position_gbp` × the T212 fee for FX
drag; realised idle-% × the mean `^IRX` rate for idle-cash credit), because **a filter changes trade
count and therefore turnover** — so FX cost is a treatment effect here, not a constant that falls out
of a relative comparison. Deferring it would bias toward whichever arm trades more, which is the exact
error the deployment-cap sweep made.

**Required margin: ≥ +1.5pp over arm A, on Pool 2 CLEAN.**

Justified against the measured **~1.08pp** noise floor: +1.5pp is ~1.4× the floor, the same bar every
recent study in this sequence used, and the smallest margin at which a result is distinguishable from
pool-perturbation noise with any confidence. This project has been burned twice by sub-floor deltas
treated as signal (the earnings dampener's ±2 blocks; the deployment-cap sweep's entire 1.06pp spread
sitting inside the 1.08pp floor).

**Stated plainly: this is a demanding bar for a refinement of an already-strong arm, and it may well
fail.** Arm B earned +1.993pp over production; asking a refinement for another +1.5pp is asking a lot.
Lowering the bar because "a refinement deserves a smaller threshold" would be precisely the
goalpost-moving that pre-registration exists to prevent, so the bar stays where every other study in
this sequence put it.

**Pools**: Pool 2 CLEAN is **decisive**. Pool 1 is **direction-only** corroboration. A cross-pool sign
disagreement is reported as an unreconciled disagreement, per the dual-provenance standing
requirement — not resolved toward whichever pool is more favourable.

---

## §6 — Realised filter effect (the n5/n10 lesson, applied twice)

The position-count study's n5/n10 arms never built the portfolios their names described, and only a
purpose-built instrument caught it. The analogous failure here is a filter that **excludes almost
nothing**, producing a null that looks like evidence against the hypothesis when it is really evidence
that the hypothesis was never tested.

**Reported per threshold, per pool:**

1. **Candidate-day exclusion rate** — excluded candidate-days ÷ total gate-passing candidate-days,
   with raw counts, split into threshold-driven and NaN-driven (§2).
2. **Exclusion rate by year** — a filter excluding 25% overall but ~0% in half the eras is
   regime-dependent, and a single pooled number would hide that.
3. **Admission displacement — the sharper check, and the one that actually matters.** How many
   admissions changed identity because of the filter: candidates the filter removed that arm A
   *actually admitted*, and the candidates admitted in their place. The selection audit found only
   **7–8%** of admitted trades sit outside the top quartile of their day's eligible set, and **92–93%**
   of admissions happen only because that day's own exits freed a slot. A filter can therefore exclude
   a large fraction of *candidate-days* while touching almost no *admissions* — the pool it thins may
   be overwhelmingly candidates that were never going to win a slot.

**Rule, fixed now**: an arm whose candidate-day exclusion is **< 5%**, **or** whose admission
displacement is **< 5% of arm A's admissions**, is reported as **NOT A MEANINGFUL TEST** of the
hypothesis at that threshold — explicitly not as a null result.

---

## §7 — Corroborating instruments

**Drawdown ceiling — one-sided, against WORSENING, stated directionally.** An earlier study's gap
comparison misfired by treating a two-sided gap as informative. Using magnitudes:

> An arm fails if `|DD_arm| − |DD_A| > 15pp` (i.e. its peak-relative drawdown is more than 15
> percentage points **deeper** than arm A's). **An improvement never fails, at any magnitude.**

Directionally: the ceiling protects against the filter buying return with risk. It says nothing about
an arm that reduces drawdown, which is reported but cannot trigger failure.

**Paired per-decision instrument.** All arms share the daily entry clock, so exact
`(ticker, entry_date)` pairing is constructible — unlike the monthly studies, which needed month-level
pairing. Two comparisons, per the measurement toolkit:
- **Shared-key**: outcomes on `(ticker, entry_date)` pairs both arms admitted. Capacity-uncontaminated
  by construction.
- **Disagreement-population**: outcome distribution of admissions unique to each arm — the instrument
  that made Arm B's own case, and the one that would expose a portfolio gain not traceable to better
  individual decisions.

**Expected overlap: 25–55%**, deliberately wide. The relative-ranking study predicted 45–65% and
measured **21.8–23.1%** — wrong by roughly 2×, because capacity-cascade amplification reshapes the
trade sequence far more than the size of the change suggests. A wide band is the honest expression of
how poorly this quantity has been predicted. **A miss is informative, not disqualifying**; a *high*
reading (≥ 80%) would indicate the filter barely perturbs admission and would corroborate §6's
not-a-meaningful-test finding.

**Minimum sample floors**: ≥ 200 shared `(ticker, entry_date)` pairs, and ≥ 150 unique admissions per
arm. Below either, the paired instrument is reported as underpowered rather than as evidence.

**Top-decile trade overlap, reported alongside every headline gap.** Ticker-Jaccard of the top decile
by P&L, A vs each arm (prior comparable: 0.66–0.69). A large headline gap with near-total top-decile
overlap indicates cascade reshuffling rather than genuinely different selection.

**Block count: corroborating only.** 16-block pass count, reported with the measured **3-block**
resolution floor stated next to it. It cannot discriminate at this arm-to-arm distance and no
conclusion will rest on it.

---

## §8 — Sanity gate

**Arm A must reproduce the relative-ranking study's own Arm B figures on Pool 2 CLEAN: 4,876 trades,
£166,830.11 total P&L** (independently reproduced twice already — by the study itself and by
`research/relative_ranking_promotion_verify_2026-08-09/` through the real production code path).

**Compare trade SETS, not totals.** A matching total with a differing set has already occurred in this
project, and the database moves daily. Both frames sorted onto a canonical
`(ticker, entry_date, exit_date)` order before diffing — a first comparison pass in the promotion
verification showed hundreds of spurious "mismatches" purely from raw insertion order.

**Benign discrepancies must be enumerated, never waved through.** Recent precedent: 7 `END_OF_DATA`
trades revised by a boundary-date price update, and `VTRS`'s admission shifting with one more day of
data. Any diff is diagnosed row by row and recorded in the report; an unexplained diff halts the study.

**Pools reconfirmed fresh at run time**, not inherited: Pool 1 = `get_live_stock_candidates()`
(503), Pool 2 CLEAN = `SELECT ticker FROM ticker_data_quality WHERE status = 'CLEAN'` (612). Both
counts and the `price_data` max date recorded in the report, per the derived-state standing rule (a
worktree's DB copy is not current merely because some copy somewhere was refreshed).

> **CORRECTION, 2026-08-10, made after commit `f69e68f` but before any arm ran.** This section
> originally named `get_research_stock_candidates()` as Pool 2 CLEAN. That is a **different pool** —
> it returns `get_all_known_stock_tickers() − get_quarantined_tickers()` = **1,017** tickers
> (including `NEEDS_REVIEW` and never-screened names), whereas the relative-ranking study's "Pool 2
> CLEAN" is the **612** tickers with `ticker_data_quality.status = 'CLEAN'`, confirmed by reading that
> study's own `step0_build_caches.py`. Using the wrong one would have made the §8 sanity gate fail
> against 4,876 / £166,830.11 for an entirely benign reason, and the most likely response to a failing
> gate — "the database has moved on" — would have been wrong. Recorded as a correction rather than
> silently fixed, per the reproducibility standard; the pool definition is a stated input, not an
> implementation detail.

---

## §9 — Engine verification

A research copy of `simulate_portfolio_compounding_mtm()` with the filter added at the single
insertion point in §1, additively instrumented. Verified before any result is trusted:

1. **Degenerate-case parity — the strongest check available.** A test-only toggle disabling the filter
   must reproduce arm A **byte-identical** (trade-for-trade, 0 row-level mismatches, both pools). This
   proves every part of the engine except the filter line is a faithful copy. The relative-ranking
   study established this pattern and it caught nothing only because nothing was broken.
2. **Exit-wiring / signal separation**: 30 sampled trades confirming `SIGNAL_OFF` traces to absolute
   momentum, never to smoothness or rank.
3. **Sizing**: 30 sampled trades recomputed against the engine's own emitted pre-entry balance.
4. **Filter hand-check**: `smoothness_t` hand-computed from raw closes for 5 tickers × 3 dates,
   matched against the engine's value.
5. **Staleness guard**: the four checks in §2.

---

## §10 — THE WALK-AWAY SENTENCE

> **I will not adopt any smoothness-filter arm if: no arm clears +1.5pp cumulative cost-adjusted
> excess return over arm A's own excess return on Pool 2 CLEAN; or if the clearing arm's peak-relative
> drawdown is more than 15pp deeper than arm A's; or if the clearing arm's mean deployment falls more
> than 2pp below arm A's (in which case it is reported as not tested, not as a result); or if the
> clearing arm excludes less than 5% of candidate-days or displaces less than 5% of arm A's admissions
> (not a meaningful test); or if the paired per-decision instrument, on a sample clearing its floors,
> shows the clearing arm's own unique admissions are not distinguishably better than arm A's own
> unique admissions.**
>
> **If the curve is monotonic across T1→T3, I will report the optimum as unbracketed and will NOT
> crown the endpoint threshold.**
>
> **If the result is null, I will not implement the blended-score variant as a rescue.**

---

## §11 — Deferred variants (recorded, deliberately NOT implemented)

- **Blended rank/smoothness score** — introduces a weighting parameter; declined per §1.
- **Window aligned to the ranking span** `(t-252, t-21)` — resolves §2's stated inconsistency but
  needs a new staleness constant.
- **Cross-sectional percentile filter** instead of an absolute bar — would bound deployment risk
  tighter but makes §6's exclusion check vacuous.
- **Smoothness as a tie-break within rank** rather than a filter — a third form, neither filter nor
  blend.
- **Smoothness as an exit condition** — out of scope; this study touches candidacy only.

---

## §12 — Constraints

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path. All work
in a research worktree, local, against a local database copy confirmed gitignored. Nothing adopted,
nothing merged, nothing deployed. `trading212_invest.live` untouched.

**Full trade-level output preserved** for every arm and pool before any research clone is deleted, per
the standing requirement — the headline figure must remain auditable against its own trades.
