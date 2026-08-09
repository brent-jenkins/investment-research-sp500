# Cross-sectional momentum — pre-registration (2026-08-09)

**PRE-REGISTRATION ONLY. Nothing has been run.** No cache built, no backtest executed, no new
engine written, no production file touched. This document exists so the construction, the
criteria, and the walk-away condition are on record before any number exists. Per instruction,
this tests a genuinely different strategy construction — not a parameter variation of the current
system — and gets full pre-registration discipline as a result.

## 0. Motivation, restated precisely (not re-litigated)

Every study run against this system to date has tuned a parameter *inside* the current
construction: time-series momentum, each stock judged against its own trailing history, long/flat,
holding whatever clears the gate, sized and capacity-constrained the same way regardless of what
else is available that day. None has cleared its own pre-registered bar:

- **Gate-width** (`research/gatewidth_prereg_2026-08-08/`): restricting daily candidacy to the top
  N by momentum among gate-passers. Largest gap (+0.65pp cost-adjusted CAGR at Top-50) sat inside
  the ~1.08pp noise floor and never reached its own +1.5pp bar. Not adopted.
- **Admission redesign** (`research/admission_redesign_prereg_2026-08-08/REPORT.md`): monthly
  shortlist rebalancing within the *same* time-series signal. Failed on two of five PASS
  conditions — the deployment guard (mean deployment ~24pp below baseline, both pools) and the
  per-decision month-level pairing (a coin flip, 50.1–50.2% win rate, both pools, when both arms
  independently held the same ticker around the same time). Not adopted.
- **Selection audit** (`research/selection_audit_2026-08-08/REPORT.md`), Part 7: momentum-ranked
  admission from the *current* gate-passed pool sits at the 99th percentile of a 100-seed random-
  admission distribution on both frictionless and cost-adjusted CAGR — ranking is doing real work
  — but at the 98th-worst percentile on drawdown. The one mechanism that adds value already buys
  that value with concentration. Part 4: raw momentum-at-entry has ~zero trade-level predictive
  power (Spearman −0.033); what predicts is *rank-within-day*, a relative, capacity-conditioned
  measure — a hint that ranking stocks against each other, not against their own history, may be
  where the real signal lives, not merely a restatement of the existing mechanism.

**Cross-sectional momentum (Jegadeesh-Titman) is a different construction, not a variant of any of
the above.** Stocks are ranked against *each other* at a point in time, not against their own
trailing history; only the top slice is held, always; entries and exits are driven by relative rank
at a rebalance date, not a daily absolute-threshold gate. This has its own, separate academic
literature, its own known failure mode (concentration-driven crashes on sharp reversals — see §9),
and no parameter setting of the current system reduces to it. That is precisely why this gets its
own pre-registration rather than folding into an existing study's parameter sweep.

## 1. Priors, stated honestly before any run (per instruction)

- The published cross-sectional momentum premium has weakened substantially since the original
  1993 Jegadeesh-Titman sample period — well-documented in the post-2000 replication literature.
- The premium is typically concentrated in smaller-cap, less liquid names than the S&P 500
  membership this system trades — this study's own universe (§3) is by construction the *least*
  favourable cap range for the effect to show up strongly in.
- The premium is typically measured gross of transaction costs. This account's own FX round-trip
  drag (0.30% per leg, the established template used throughout this project) is a real, material
  cost that dominates thin edges — exactly the mechanism that reversed the deployment-cap sweep's
  headline finding (CLAUDE.md's "A cost is safe to defer only while it's constant across the arms
  being compared").
- **This is the strongest remaining candidate construction this project has identified, not a
  likely edge.** It is being tested because it is genuinely different and motivated by real
  findings above, not because it is expected to pass.

## 2. The three arms

| | A — Baseline | B — Cross-sectional, 30d max-holding | C — Cross-sectional, unlimited holding |
|---|---|---|---|
| Construction | Current production logic, unchanged | Cross-sectional (see §3) | Cross-sectional (see §3), identical to B |
| Max-holding | `stock_max_holding_days = 30` (config, unchanged) | **30 days, retained** | **Removed — `MAX_HOLDING` never fires** |
| Everything else vs. B | — | — | **Identical to B in every other respect** |

B and C differ in exactly one variable — this is the only genuinely separable comparison in this
study (§8 explains why B-vs-baseline and C-vs-baseline are each a bundle of construction + cadence
+ top-N + holding-period changes, inseparable from each other, per instruction).

## 3. Cross-sectional construction (B and C share this, in full)

**Ranking signal — 12-minus-1-month momentum, exact formula:**

```
At each monthly rebalance date t, for each candidate:
  p_1  = most recent close at or before (t - 21 trading days)
  p_12 = most recent close at or before (t - 252 trading days)
  rank_signal = p_1 / p_12 - 1
```

21 and 252 trading days are this project's standing approximation for "1 month" and "12 months"
(matching the trading-day conventions already used elsewhere in this codebase, e.g. ATR/VaR
lookback windows) — stated exactly, not left as "roughly a month/year." **The skip exists because
short-horizon returns exhibit reversal**: the most recent month is excluded from the ranking signal
specifically so the strategy is not simultaneously long-term-trend-following and short-term-
reversal-trading against a documented tendency in the same signal. This is the standard academic
construction (Jegadeesh & Titman 1993), not a modification of it.

**Eligibility at each rebalance date**: currently a genuine S&P 500 member (`is_member()`, same
function the live system and every backtest engine already use) **and** not inside the crisis
entry-block window **and** has at least 252+21 trading days of price history before t (so
`rank_signal` is computable). A candidate failing the history-depth requirement is excluded from
that month's ranking only — not from future months once it accumulates enough history.

**Ranking and selection**: rank all eligible candidates by `rank_signal`, descending. Hold the top
40. **This ranks and selects purely by relative rank, with no separate absolute-momentum floor
applied on top of it** — flagged explicitly as a design decision requiring your review, not a
silent default:

> The current system's `min_momentum_pct` floor (2%) and `passes_momentum_floor()` exist to filter
> an *absolute*, time-series signal. Cross-sectional selection is a *relative* signal by
> construction — the top 40 by rank are held regardless of whether the 40th-ranked name's own
> `rank_signal` would individually clear 2%, or even clear zero. This is the faithful academic
> construction (a "top decile"/"top N" cross-sectional portfolio does not additionally filter on
> the absolute value of the ranking variable) and is the interpretation this pre-registration
> proposes to run. **The alternative — applying `min_momentum_pct` as a floor on top of the top-40
> selection, so a broad market decline could shrink the held slice below 40 names — is a real,
> different construction and is not what is proposed here.** State now if you want that instead;
> it is a materially different (and, per the flagged failure mode below, materially safer) design.

**Rebalance cadence**: monthly, defined as the first trading day of each calendar month (matching
the admission-redesign study's own precedent for "monthly," for direct comparability). At each
rebalance date: re-rank all eligible candidates; any currently-held name still in the new top 40 is
kept, untouched; any currently-held name that has fallen out of the top 40 is exited (new exit
reason, §4); any top-40 name not currently held is a new entry, sized per §6.

**Construction, cadence, and top-N are not separable variables.** Cross-sectional momentum does not
exist as a strategy without all three together — a daily-rebalanced or single-stock version is a
different (and, per §1, differently-motivated) construction. This is one coherent alternative
strategy being tested as a unit, not three bundled parameter changes.

## 4. Exits — what's shared, what's new, and why

**Shared, unmodified, called exactly as today**: `analysis.decision.evaluate_exit_reason()` is
called identically to the current system, continuously (every day, not just on rebalance dates),
with one deliberate input difference: `sig_on` is always passed as `None` (there is no daily
time-series signal in this construction to report). Because the function treats `None` as "unknown,
don't trigger this check" (not as an affirmative trigger — see the function's own docstring),
`SIGNAL_OFF` structurally never fires for arms B/C. This is a real, correct consequence of the
construction, not a special case coded around it: **cross-sectional momentum has no daily on/off
signal, so it has no `SIGNAL_OFF` analog.** `CRISIS_EXIT` and `STOP_LOSS` fire exactly as they do
today, from the same shared function, unchanged. `MAX_HOLDING` also fires from the same shared
function: Arm B passes `max_holding_days=30` (config, unchanged); Arm C passes
`max_holding_days=None`, which the function's own existing `holding_days is not None and
max_holding_days is not None` guard already handles — disabling `MAX_HOLDING` needs no new code
path, only a different argument.

**New, additive, not in `analysis/decision.py`** — `RANK_DROPPED`: on a rebalance date only, for
any currently-held position not exited by one of the three checks above, if it is no longer in that
date's freshly-computed top 40, it is exited with reason `RANK_DROPPED`. This has no live-system
analog (the live system has no ranking/rebalance mechanism at all), so there is nothing existing to
share it with — it is new logic specific to this research engine, checked only at monthly rebalance
boundaries, never daily. Stated explicitly as an accepted exception to this project's "no
hand-written duplicate decision logic" invariant, for the same reason CLAUDE.md already accepts the
SQL-vs-Python candidate-priority duplication: there is no existing shared function to call.

**Overall exit priority for B/C**: `CRISIS_EXIT` → `STOP_LOSS` → `MAX_HOLDING` (Arm B only) →
`RANK_DROPPED` (rebalance days only). The first three are checked daily via the shared function
before `RANK_DROPPED` is ever evaluated, so a position that would be stopped out or crisis-exited
mid-month never reaches the rebalance-day rank check at all — it is already gone.

**Flagged for the results, not pre-judged (per instruction)**: under monthly rebalancing,
`STOP_LOSS`/`MAX_HOLDING` and `RANK_DROPPED` will mechanically overlap — a name that has been
declining enough to approach its stop is also likely to have fallen out of the top 40 by the next
rebalance, and would be exited by `RANK_DROPPED` anyway if the stop hadn't already fired first.
**The exit-reason breakdown (count and % by reason) will be reported per arm**, exactly as it is in
`research/selection_audit_2026-08-08/REPORT.md`'s Part 3 table, so this interaction is visible
rather than asserted.

## 5. Retained unchanged, not tuned (per instruction)

- **Universe**: S&P 500 membership (`is_member()`), unchanged. No separate market-cap filter added
  — membership already provides the size/quality/liquidity floor; point-in-time market caps are
  hard to source reliably and would be near-inert layered on top of index membership.
- **Crisis gate**: ≥7% single-day S&P fall, immediate, unchanged, checked identically for B/C via
  the same shared `evaluate_exit_reason()`/entry-block mechanism the current system uses. **This
  matters more, not less, under cross-sectional construction** — the literature's best-documented
  failure mode (Daniel & Moskowitz 2016, "momentum crashes") is a sharp reversal shortly after a
  market bottom, when a concentrated momentum portfolio can be caught wrong-footed exactly as the
  regime flips. Spring 2009 — immediately after this project's own crisis block ends
  (2008-03-23→2009-06-13) — is the textbook instance of this and will be scrutinized directly at
  the block level, not just in the aggregate (§9).
- **Stop-loss and exit detection**: retained, continuous, unchanged (§4).
- **Sizing, deployment cap, `per_trade_pct`**: unchanged. New entries at a rebalance are sized by
  the same `resolve_dynamic_max_position_gbp()` / VaR core / `resolve_cash_constrained_position_gbp()`
  / `is_position_size_viable()` chain the current system already uses, called identically, with
  `max_total_deployed=0.70` and `per_trade_pct=0.05` unchanged.

## 6. Capital freed mid-month — modelled honestly, flagged as the study's biggest risk

Entries happen only at monthly rebalance (§3); exits (`CRISIS_EXIT`/`STOP_LOSS`/`MAX_HOLDING`) can
happen any day. Capital freed by a mid-month exit is **not** redeployed until the next rebalance —
this is a structural consequence of monthly-only entries, not a separate design choice, and it is
**exactly the mechanism that caused the admission-redesign study's deployment guard to fail** (mean
deployment ~24pp below baseline in both pools, masquerading as a drawdown improvement). Per the
standing "a cost is safe to defer only while it's constant across the arms being compared" rule,
this will be modelled directly, not assumed away: both cross-sectional arms emit a **daily
deployment panel** (date, cash, mark-to-market position value, current balance, deployed %) exactly
as the admission-redesign engine already does, and mean deployed % is compared against baseline as
a first-class, mandatory guard (§7.4) — not a footnote.

**This is stated as the study's single biggest risk of a false positive, up front**: cross-sectional
is supposed to be "always fully invested in its slice" (per instruction), which argues for *higher*
exposure than baseline, not lower. If the deployment panel shows B/C sitting in *more* cash than
baseline despite that design intent, the mechanism is almost certainly the same wait-for-rebalance
idle-capital effect that sank the admission redesign — and per instruction, that must be reported as
the arm "winning by being out of the market," not credited to selection quality (§7.4).

## 7. Pass/fail criterion — written before any run, numeric, not vague

**Primary metric**: for each arm X, `excess_return_X = CAGR_X − CAGR_^SP500TR` (dividend-adjusted
S&P 500 total return, `^SP500TR`, same dates X actually covers), computed **cumulative (full window)
and per 16-block** — reusing this project's existing block boundaries for direct comparability with
every prior block-pass reference in CLAUDE.md. Not absolute CAGR, per instruction: the question is
whether cross-sectional beats the index by more than the current system does, not whether it beats
the index in isolation.

**Backtest convention, stated explicitly per this project's own standing requirement**: all three
arms run as **one continuous walk over the full available window (2007→present)**, each starting
from the identical initial balance, with **no train/test boundary and no reset**. This is neither
literally "reset-at-boundary" nor "single-account-since-inception" in the sense CLAUDE.md's own
two-conventions section describes — those labels presuppose a train/test split, and there isn't one
here: all three arms see the identical full window from the identical start. Stated explicitly so
this doesn't read as an oversight of the standing requirement — the requirement's actual concern
(one variant's train-period luck inflating its test-period comparison) does not apply when there is
no split at all.

**PASS requires ALL of the following, simultaneously, in Pool 2 CLEAN, for B and/or C individually**
(B and C are evaluated separately against A — one may pass while the other fails):

1. **`excess_return_{B,C} ≥ excess_return_A + 2.5 percentage points`** (cumulative, CAGR-equivalent).
   *Why 2.5, not smaller*: the measured noise floor for cost-adjusted CAGR under *trivial*
   perturbations of the *current* construction is ~1.08pp worst-single-draw / ~0.13pp typical
   (17-draw empirical run, 2026-08-08). Swapping the entire strategy construction — signal,
   ranking, cadence, and holding logic all at once — is a far larger structural change than a
   single-ticker drop or a tie-break reorder, so the bar is set well above the trivial-perturbation
   ceiling (2.5pp ≈ 2.3× the worst-case noise-floor draw), not merely nudged above it. This mirrors
   the reasoning the admission-redesign pre-registration used for its own 20%-relative Ratio bar.
2. **Drawdown ceiling**: peak-relative max drawdown for B/C must not exceed baseline's own
   peak-relative max drawdown (−54.02% per the selection audit's Pool 2-final Arm A figure — to be
   reconfirmed fresh on Pool 2 CLEAN at run time, §7 sanity gate) **by more than 15 percentage
   points, absolute** (i.e. no worse than approximately −69%). Set wider than the admission-
   redesign study's 8pp guard *deliberately* — that study's redesign was expected to reduce risk;
   this one's known failure mode (concentration-driven crashes) makes some drawdown deterioration a
   real possibility worth measuring rather than gating out before it can be seen. 15pp is a ceiling
   past which the concentration risk is judged to have overwhelmed any return benefit, not a target.
3. **Deployment/exposure guard (§6)**: `mean_deployed_pct_{B,C} ≥ mean_deployed_pct_A − 2.0
   percentage points`. Tolerance is deliberately tight (vs. the admission-redesign study's 15pp
   floor) because the *expected* direction here is the opposite — cross-sectional is designed to be
   always-fully-invested-in-slice, so a material deployment shortfall is itself evidence the
   wait-for-rebalance idle-capital effect (§6) is driving results, not a merely-acceptable side
   effect. If this fails, the arm's excess-return figure must be reported alongside the deployment
   gap and explicitly attributed to exposure, not selection.
4. **Top-decile trade overlap** (Jaccard, ticker identity, per the admission-redesign study's §6
   methodology) is reported alongside every headline gap for all three arm-pairs (A–B, A–C, B–C) —
   not a PASS/FAIL condition on its own, but mandatory context for interpreting 1–3.
5. **Per-decision corroboration** (§8) does not contradict the portfolio-level direction, on
   whichever instrument in §8 clears its own minimum-sample floor. If it falls below the floor, this
   condition is silently satisfied (not evaluated, not counted against or for) — per instruction,
   report the count and do not interpret an unusable sample.

**Corroborating only, never decisive on its own**: 16-block pass count — the block-pass count where
strategy CAGR beats `^SP500TR`'s own CAGR, per block. Measured resolution floor for this statistic
is **3 blocks** (6–9, 17-draw empirical-noise-floor run, 2026-08-08) — a delta of ≤3 blocks is not
read as evidence either way, and per CLAUDE.md's own capacity-cascade note, any per-block statistic
is structurally biased *toward the null* for a real, consistent effect (sequential capacity-
constrained admission smears a genuine improvement across many blocks rather than concentrating it
in one), so this cannot rescue a failing arm but also should not be over-read if a passing arm's
block count looks unremarkable.

## 8. Walk-away sentence (written now)

> *I will not adopt B or C if its cumulative excess return over ^SP500TR fails to beat baseline's
> own excess return by 2.5 percentage points or more, or if its peak-relative max drawdown
> deteriorates by more than 15 percentage points versus baseline, or if its mean deployment falls
> more than 2 percentage points below baseline's (evidence the result is an exposure artifact, not
> a selection improvement — the exact failure mode already found once this month for the admission
> redesign), or if the per-decision corroboration instrument (§9) contradicts the portfolio-level
> headline's direction on a usable sample. Any one of these outcomes, for a given arm, ends the
> investigation for that arm with "not adopted" — not a request to re-tune top-N or rebalance
> cadence and try again inside this same pre-registration.*

## 9. Per-decision corroboration — three instruments, stated in advance

**B vs. C — expected to be directly usable, exact-key pairing.** B and C share the identical
monthly rebalance grid and the identical ranking/selection mechanism — they differ *only* in
max-holding. Any entry both arms make will very likely share the same `(ticker, entry_date)` key
(both rebalance on the same dates, off the same ranking), so the literal, unmodified
`paired_comparison()` should produce a large, genuinely capacity/cascade-clean sample for this one
comparison — unlike every cross-cadence comparison this project has attempted so far. **This is the
single cleanest instrument in the whole study** and isolates the max-holding effect alone. No
minimum-sample floor concern is anticipated here, but the actual shared-pair count will still be
reported before any conclusion is drawn from it.

**A vs. {B, C} — expected to fail at the exact-key grain, exactly as it did for the admission
redesign, for the identical structural reason**: baseline's daily entry grid (~4,900 possible dates
over 19 years) vs. B/C's monthly grid (~228 rebalance dates) makes an exact `(ticker, entry_date)`
match between them rare and non-representative. **Pre-registered decision rule, reusing the
admission-redesign precedent exactly**: run `paired_comparison()` anyway, report the shared-pair
count; if it falls below **200 shared pairs**, report the count and do not interpret it.

**What will actually be used as the primary A-vs-{B,C} instrument** — the same **month-level
pairing** adaptation the admission-redesign study built and validated: for every calendar month and
every ticker held by *both* arms at any point overlapping that month, pair the two arms' realized
returns for that ticker/month. Not fully capacity/cascade-free (residual within-month capacity
dynamics can still differ between arms) — disclosed alongside any number this produces, not assumed
away, exactly as the admission-redesign report did.

**Supplementary**: `disagreement_population_comparison()` run literally, labelled as an arm-level
distributional comparison (not a decision-level isolation), for the same reason the admission-
redesign study gave — most trades will register as "unique to one arm" by exact date for the
structural cadence-mismatch reason above, not from genuine decision disagreement.

## 10. Pools

- **Pool 1** — `get_live_stock_candidates()`, ~503 tickers. **Ranking-direction check only** —
  maximal survivorship bias means its absolute CAGR/drawdown numbers are not used for §7's
  PASS/FAIL arithmetic, only to confirm the same-sign, same-rough-shape direction as Pool 2 CLEAN.
  A direction disagreement does not auto-fail the study but triggers the standing dual-provenance
  re-investigation discipline — not a silent average, not a discard of whichever pool disagrees.
- **Pool 2 CLEAN** — `get_research_stock_candidates()` ∩ `ticker_data_quality.status == 'CLEAN'`.
  **Decisive pool.** Count **to be reconfirmed fresh at run time, not assumed** — the CLEAN
  population has grown twice already this month (220 → 612, per the admission-redesign study's own
  detour) and may again. Chosen as decisive for the same reason the admission-redesign study chose
  it: this is a drawdown-sensitive comparison (§7.2), and `NEEDS_REVIEW`-tagged tickers have already
  been shown to disproportionately drive Pool 2's tail losses.
- Both engines (baseline call, new cross-sectional engine) read the identical candidate universe per
  pool — no separate "cross-sectional universe."

## 11. Sanity gate — before B or C is trusted

**Two-stage, matching the admission-redesign precedent, adapted for this project's own confirmed
Pool-2-CLEAN drift.**

**Stage 1 — engine/environment correctness, Pool 1 (stable reference, no drift concern)**: run the
**unmodified** `simulate_portfolio_compounding_mtm()` against Pool 1 and confirm **4,749 trades,
£240,360.75 total P&L**, byte-identical trade set (ticker/entry_date/exit_date/exit_reason/pnl_gbp
for every row, not just the total) against the existing reference file used identically by the
selection audit and the admission-redesign study. Any mismatch stops the study before Stage 2.

**Stage 2 — Pool 2 CLEAN baseline, established fresh, not checked against an old number.** Per
CLAUDE.md's own repeated finding that this specific pool's exact P&L figure legitimately drifts
day-to-day even at identical trade count (boundary-date price revisions, a marginal admission like
`VTRS` shifting with one more day of data — confirmed twice already this month), **the first Pool 2
CLEAN run of Arm A in this study becomes this study's own reference point**, not a stale prior
number. What *is* checked against prior runs: **trade count** and a **trade-set diff** (ticker +
entry_date) against the two most recent Pool 2 CLEAN Arm A runs on record (admission-redesign:
4,916 trades; gate-width's own re-confirmation) — any difference is diagnosed row-by-row (is it the
known `END_OF_DATA`/`VTRS`-style drift, or something new) before proceeding, per instruction to
compare trade sets, not just totals.

## 12. Engine-correctness verification — no degenerate-parity reduction exists, stated honestly

**Unlike gate-width and admission-redesign, this construction has no parameter setting that reduces
it to the current baseline engine** — there is no "shortlist=None" or "rebalance=daily" equivalent
for cross-sectional ranking, because the signal itself (relative rank vs. absolute time-series
threshold) is categorically different. A literal degenerate-case parity check, the verification
method every prior study in this project has used, is not available here. **Three-part alternative
verification is proposed instead**, each independently checkable before any real result is trusted:

1. **Exit-side reuse verification**: confirm the new engine's `CRISIS_EXIT`/`STOP_LOSS`/
   `MAX_HOLDING` decisions for a sample of positions match `analysis.decision.evaluate_exit_reason()`
   called directly with the same inputs — this function is unmodified and shared, so this is a
   wiring check (is the new engine actually calling it correctly), not a re-test of the function
   itself, which is already covered by `tests/test_decision.py`.
2. **Ranking/rebalance spot-check**: for a handful of real rebalance dates spanning different market
   regimes (at minimum: one pre-2008, one inside the 2008-09 crisis block, one in the 2015-2020
   calm period, one in 2020, one in 2022), hand-compute the top-40 list directly from raw
   `price_data` using the exact formula in §3, independent of the new engine's own code, and confirm
   the engine's admitted list for that date matches exactly.
3. **Sizing spot-check**: for a sample of real rebalance-day entries the new engine produces, confirm
   `resolve_dynamic_max_position_gbp()` and the VaR sizing chain, called directly with the same
   inputs, reproduce the engine's own recorded `position_gbp` — the same methodology the selection
   audit's `counterfactual_sim.py` used (30/30 exact match) before being trusted.

All three checks are run and reported before any B/C result is treated as meaningful.

## 13. Under-determined parameters — flagged now, not swept in this run

- **Top-40**: not derived from anything measured for this project's own capacity/deployment
  dynamics — the standard academic decile-equivalent size for this scale of universe, per
  instruction. Not swept.
- **Rebalance cadence (monthly)**: per instruction, not swept. Weekly or a fixed N-trading-day
  cadence are not tested here.
- **12-minus-1-month lookback**: the standard academic construction, per instruction. Not swept.
- **No absolute momentum floor on the top-40 selection** (§3): flagged above as the single largest
  open interpretation question in this document — please confirm or override before this is built.

## 14. What gets built, if this is approved (not done yet)

1. A new, additive, research-only engine (working name `simulate_cross_sectional_momentum()`),
   sharing `analysis.decision.evaluate_exit_reason()` and the full sizing chain unmodified for
   `CRISIS_EXIT`/`STOP_LOSS`/`MAX_HOLDING`/entry sizing; new logic only for the ranking computation
   (§3) and the `RANK_DROPPED` rebalance-day exit (§4). Two capacity variants (B: `max_holding=30`,
   C: `max_holding=None`) are the same engine, one config-level argument different.
2. The three-part verification plan (§12), run and reported before any B/C result is trusted.
3. Pool 1 and Pool 2 CLEAN caches, reused from existing research artifacts where still valid,
   rebuilt fresh where not (CLEAN population reconfirmed per §10).
4. Daily deployment panels for both cross-sectional arms (§6), and the deployment guard computed
   from them directly, not approximated.
5. The B-vs-C exact-pairing instrument and the A-vs-{B,C} month-level pairing instrument (§9), both
   built as their own functions per the admission-redesign precedent.
6. `research/cross_sectional_momentum_2026-08-09/REPORT.md` (or the actual run date) stating, for
   both pools and both arms: excess return over `^SP500TR` (cumulative and per-block), drawdown, the
   deployment guard, top-decile overlap for all three arm-pairs, exit-reason breakdown per arm
   (§4), both corroboration instruments' results with sample sizes, and the four PASS conditions
   evaluated explicitly against each arm.

**Nothing above has been executed. No cache, no engine, no run.**

## Constraints re-confirmed

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path. Every
new function this study needs is additive and research-only, following the established pattern
(`instrumented_engine.py`, `redesign_engine.py`, the measurement toolkit). Nothing adopted, nothing
merged, nothing deployed — this document is a plan for review, not a completed or partial run.
