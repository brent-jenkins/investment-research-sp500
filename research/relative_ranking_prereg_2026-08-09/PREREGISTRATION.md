# Relative-ranking (cross-sectional rank, daily admission) study — pre-registration (2026-08-09)

**PRE-REGISTRATION ONLY. Nothing has been run, nothing built.** Working from a dedicated worktree
(`c:\GitHub\investment-app-relative-ranking`, branch `research/relative-ranking-2026-08-09`) — the
concurrent-session collision found earlier today means the shared main checkout is no longer where
this project's own research work should happen, per its own standing practice.

## 0. Motivation

`research/cross_sectional_momentum_prereg_2026-08-09/REPORT.md` failed decisively on its primary
criterion (condition 1: −0.19pp to −9.83pp against a +2.5pp bar, every arm, both pools) — but the
failure was attributed precisely, not just observed: monthly-gated entries left the account
22.5–51pp underdeployed relative to baseline (condition 3), an opportunity cost large enough to
plausibly swamp any selection-quality effect on its own. **The same report's month-level per-decision
instrument showed the opposite signal on selection quality specifically**: cross-sectional's own
realized trades won the per-decision comparison against baseline's own realized trades on the same
ticker/month **52–66% of the time, on large samples (3,000–4,800 shared ticker-months), in both
pools**. That report's own "What this does and does not close" section states this directly: whether
relative-rank selection works *if entries aren't gated to a monthly grid* was never tested — cadence
and construction were bundled together by design in that study (its own §3), and are separated here
for the first time.

**This study removes the cadence confound entirely.** All three arms admit and evaluate on the
existing *daily* clock the production system already uses — the monthly rebalance grid, and the
severe underdeployment it caused, plays no role here at all.

## 1. The arms

**Narrowed on review — B changes the admission sort key only, nothing else.** The first draft of this
section swapped the cross-sectional rank signal in everywhere absolute momentum flowed (gate, floor,
`SIGNAL_OFF`, sort key), which changed exits in B too — collapsing B and C into two variants of
rank-based exit rather than an attribution arm plus a clean second variable. Corrected: gate and every
exit reason stay the standard, unmodified absolute-momentum-based production logic for *all three* arms.
Only the *order* in which gate-passing candidates are offered the day's available capacity changes,
for B and C.

| Arm | Entry gate | 2% floor applies to | `SIGNAL_OFF` fires on | Admission sort key | Extra exit |
|---|---|---|---|---|---|
| A — Baseline | Absolute momentum > 0, member, not crisis-blocked | Absolute momentum | Absolute momentum ≤ 0 | Absolute momentum, descending | None |
| B — Narrow | *Identical to A* | *Identical to A* | *Identical to A* | **12-minus-1 cross-sectional rank, descending** | None |
| C — Fuller | *Identical to A* | *Identical to A* | *Identical to A* | *Identical to B* | **`RANK_DROPPED`, evaluated every day** |

**A is not a new construction at all** — it is `analysis.backtest.simulate_portfolio_compounding_mtm()`,
called unmodified, exactly as every other study this project has run against it.

**B changes exactly one thing relative to A: the key `_admit_group()`'s candidate sort uses.**
`compute_cross_sectional_rank_series()` (already built, `research/cross_sectional_momentum_prereg_2026-08-09/cross_sectional_signal.py`
— `p_1(t)/p_12(t) - 1`, 21/252-trading-day anchors, the existing staleness guard reused unmodified, not
rebuilt) is computed for every candidate alongside the existing absolute-momentum value, but is used
*only* to reorder the already-gate-passed candidate list before the capacity-constrained skip-and-
continue walk — it never touches `passes_entry_gate()`, `passes_momentum_floor()`, or
`evaluate_exit_reason()`'s `sig_on` input, all three of which keep reading the standard absolute
momentum value, byte-identical to A. This is deliberately the narrowest possible test of "does *who
gets the next scarce slot* matter" — the eligible candidate *set* on any given day is identical between
A and B; only the *order of preference* among that identical set differs. Same daily event walk, same
Phase-A-exits-before-Phase-B-entries structure, same capacity-constrained skip-and-continue admission,
same VaR sizing chain, same stop/crisis/holding-period exits as A throughout.

**C is B plus one addition: `evaluate_rank_dropped_exit()` (already in `analysis/decision.py`, added
for the monthly study, not modified here — reused, per the constraints in §9) checked every day**, not
only on a monthly rebalance date, using the same cross-sectional rank value that already drives B's
admission ordering. Priority is unchanged from the monthly study's own documented ordering:
`evaluate_exit_reason()` (`CRISIS_EXIT`/`STOP_LOSS`/`SIGNAL_OFF`/`MAX_HOLDING` — all still
absolute-momentum-driven, identical to A/B) is checked first, every day, for every open position; only
a position that survives that check is then evaluated against
`evaluate_rank_dropped_exit(is_in_current_top_n=...)`. **This keeps B and C cleanly separated as two
variables, not two flavours of the same one**: B isolates whether relative-rank *ordering* of an
unchanged eligible set matters; C adds, on top of that, whether relative rank should also govern
*de-selection* of an already-held position — the entry gate and every standard exit reason are
identical across all three arms throughout.

## 2. N — the top-N threshold for C's RANK_DROPPED exit, specified and justified

**N=30, distinct from the monthly study's own N=40, and justified against a different anchor.** The
monthly study's N=40 was chosen as a target *occupancy* level for a rebalance-driven construction meant
to hold close to 40 names. This study's daily-admission arms have no such target — occupancy is
emergent from `per_trade_pct`/`max_total_deployed` exactly as it is for baseline, and the position-count
and deployment-cap studies already measured that baseline's own realized mean occupancy at these exact
config values (`per_trade_pct=0.05`, `max_total_deployed=0.70`) is **13.98 (Pool 1) / 14.23 (Pool 2
CLEAN)** — not 40. Per the task's own instruction ("note that the gate-width study found list length
above ~30 inert because capacity binds first, so N here governs the EXIT threshold more than the entry
pool"): N=40 here would sit roughly 3x above typical realized occupancy, meaning a held position would
need to fall a very long way down the ranked field (out of the top 40 of ~500–600 rankable candidates)
before RANK_DROPPED ever fires — plausibly inert for the same reason gate-width found candidate-pool
width inert once it exceeds what capacity can actually reach. **N=30 sits roughly 2.1x realized
occupancy** — comfortably above typical holdings (so RANK_DROPPED is not mechanically triggered by
ordinary day-to-day rank noise among already-held, comfortably-top-ranked names) while still tight
enough (top ~5–6% of the rankable universe) that a real, non-trivial relative decline is required to
breach it. **This is a genuine design choice with real sensitivity to the exact value chosen, stated
plainly rather than presented as more principled than it is** — no data exists yet to confirm 30 is
better calibrated than, say, 20 or 25; a future sensitivity sweep on N specifically would be a
reasonable follow-up if C's own result is close to a decision boundary, not attempted here.

## 3. Held fixed, and one reading stated explicitly rather than left ambiguous

Daily admission cadence (all three arms), `max_total_deployed=0.70`, `per_trade_pct=0.05`,
`atr_stop_multiplier=2.0`, `stock_max_holding_days=30`, the 2% momentum floor — **applied to absolute
momentum for all three arms, unchanged from A, never to the cross-sectional rank value** (per §1's
narrowing: a percentage floor on a relative-rank quantity doesn't mean anything comparable to what it
means on an absolute-return quantity, and B/C's entry gate is byte-identical to A's regardless). Same
universe (Pool 1 / Pool 2 CLEAN, §7), same cost template (FX round-trip + idle-cash credit, computed per
arm — turnover is expected to differ across arms, particularly C, so per this project's own standing
cost-deferral rule this is modelled, not assumed constant, even though not itemised as its own bullet in
the brief).

**"Same engine" is read as "the same shared decision functions and daily-walk architecture," not "the
literal same Python entry point" — stated explicitly since B/C cannot literally call
`simulate_portfolio_compounding_mtm()` unmodified (there is no existing degenerate-case reduction from
that function to a cross-sectional-ranked variant, exactly as the monthly study's own engine found for
its construction).** B/C need a new engine file — a daily-cadence sibling of
`cross_sectional_engine.py`, reusing every shared decision function that engine already reuses
(`evaluate_exit_reason`, `evaluate_rank_dropped_exit`, `is_reentry_blocked`,
`resolve_dynamic_max_position_gbp`/`_var_position_size_core`/`resolve_cash_constrained_position_gbp`/
`is_position_size_viable`, `passes_momentum_floor`) — never a second, independently-written copy of any
of them. If this reading of "same engine" is wrong, flag it before any code is written.

## 4. Deployment parity is a design constraint here, not a post-hoc guard

**The one mechanism that could break it, named directly, per the task's instruction.** C's
`RANK_DROPPED` exit adds turnover beyond A's own baseline — if freed capital from a rank-dropped exit
cannot be redeployed the same day, it drifts idle exactly the way the monthly study's wait-for-rebalance
mechanism did.

**How the engine is designed to prevent this, not just checked after the fact.** All three arms run on
the identical daily-walk skeleton every other daily engine in this project already uses (`position_count_engine.py`,
the production engine itself): **Phase A (every exit, including C's `RANK_DROPPED`) always completes
before that same day's Phase B (entries)** — a position exiting today via any reason immediately frees
its capital for that same day's admission pass, never deferred to a future date. This is structurally
different from, and immune to, the monthly study's own failure mode: that engine only ever attempted
entries on the first trading day of a calendar month, so capital freed mid-month sat idle by
construction regardless of same-day-redeployment logic, because there was no next entry attempt until
the following rebalance date. Here, every day is an entry-attempt day for every arm.

**A further, structural reason parity is expected to hold for C specifically, stated as a prediction to
verify, not assumed**: cross-sectional rank is zero-sum in a way absolute momentum is not — a name can
only drop out of the top-N because some other name's rank rose to displace it, and that displacing name
is, by construction, not currently held (or it would already occupy that rank slot) and therefore a
live candidate for the very slot `RANK_DROPPED` just freed. Assuming it also clears the ordinary entry
gate (membership, not crisis-blocked, re-entry cooldown, sizing viability), same-day replacement is the
expected common case, not a hoped-for one.

**The residual risk, distinct from the monthly mechanism and expected to be small**: on a day where
the displacing candidate fails a *different* gate condition (crisis-blocked, cooldown, sizing), the
freed slot could sit briefly unfilled — the same ordinary risk baseline itself already carries on any
day with more exits than qualifying entrants, not a new failure mode C introduces. **Measured directly,
not assumed**: mean deployment % for every arm, checked against baseline's own, with the same 2pp
tolerance the position-count and deployment-cap studies both used. Any arm whose realized deployment
diverges beyond that is reported as not testing what it claims, not compared on returns — unchanged
from both prior studies' own standard.

## 5. Expected-effect-size warning — a testable prediction, not a formality

**12-minus-1 cross-sectional rank and 12-month absolute momentum are the same underlying signal minus
one month of skip** — they overlap heavily at the top of any given day's ranking, and Arm B may be
close to inert for the same reason gate-width's own Top-100 arm barely moved the needle: reordering a
highly-correlated ranking signal changes little about which names cluster at the top. **B's own
eligible candidate set on any given day is now, per §1's narrowing, identical to A's — only the order
of preference among that identical set differs.** This makes B structurally closer to gate-width's own
comparison than the broader drop-in swap this section originally described: gate-width reordered *and*
truncated an unchanged gate's output; B here only reorders, never truncates. **Prediction, stated in
advance**: exact `(ticker, entry_date)` admission overlap between A and B in the 45–65% range — still
well below what a naive "these are basically the same signal" argument would suggest (~90%+), because
Part 4 of the selection audit already established that ~92–93% of admissions are timing-driven (only
7–8% of admissions come from outside that day's own top quartile) — meaning *who wins a scarce slot on
a capacity-constrained day* is sensitive to the exact ranking order even when the two signals are highly
correlated, and this project's own repeated finding is that capacity-cascade amplification pushes
measured overlap well below any naive same-signal argument in every comparable case tried so far
(gate-width: predicted ~92%, measured 40.6–44.8%; the adj_close momentum study found a similar gap, from
a change arguably narrower than this one). **If actual overlap comes back very high (≥80%), that is
reported plainly as "B is not a meaningful test of relative-rank selection, only a near-relabeling of
the same admissions" — not smoothed into a result.** If it comes back low, consistent with the
prediction or below it, that is reported as consistent with — not independent confirmation of — the
capacity-cascade mechanism this project has
now observed in every daily-clock signal-swap comparison it has run.

## 6. Criteria

**Primary — excess return over `^SP500TR`, per block and cumulative**, computed identically to the two
preceding studies (`step4_benchmark_relative.py`-style methodology, continuous-walk convention),
reported as each arm's own excess return and as that arm's excess return minus baseline's own.

**Required margin: ≥1.5pp cumulative cost-adjusted excess-return improvement over baseline** — same bar,
same justification as both preceding studies (the measured ~1.08pp noise floor, 1.5pp being ~1.4x the
largest single draw observed). Kept unchanged rather than raised or lowered, consistent with treating
this as the project's standing bar for this class of comparison, not re-derived per study.

**Drawdown ceiling — one-sided against worsening only**, corrected wording per the task's own
instruction (the cross-sectional study's own condition 2 compared a *gap* rather than a *direction*,
which produced a confusing, backwards-reading "failure" on an arm whose drawdown had actually
*improved* — recorded in that study's own report as a drafting looseness, fixed here rather than
repeated): **an arm's peak-relative max drawdown must not exceed baseline's own by more than 15
percentage points.** An arm whose drawdown is equal to or better than baseline's always passes this
condition regardless of return — the same fix already applied in both the position-count and
deployment-cap studies' own walk-away sentences, now applied a third time.

**Deployment parity**: per §4, ≤2pp divergence from baseline for every arm, a design constraint checked
directly, not inferred.

**Top-decile trade overlap**, exact `(ticker, entry_date)` Jaccard and ticker-only Jaccard, reported
alongside every headline gap — same two measures every study in this sequence has used.

**Curve/pattern discipline**: B and C reported together against A, not just C-vs-A in isolation — since
C is explicitly B plus one mechanism, the B→C delta isolates what the added `RANK_DROPPED` exit itself
contributes, separate from what swapping the ranking signal alone contributes.

**Block count: corroborating only**, same 3-block resolution floor (6–9 range) as every prior study in
this sequence.

## 7. The paired per-decision instrument

**All three arms share the identical daily clock — exact `(ticker, entry_date)` pairing against baseline
is directly constructible**, unlike the monthly study where only a coarser month-level pairing worked.
**Expected overlap range stated in advance, per §5's own prediction**: 45–65% for A-vs-B; **likely
somewhat lower for A-vs-C**, since C's added `RANK_DROPPED` exit changes not just *which* candidates get
admitted but *when* existing ones are forced to exit, propagating further into the admission sequence
for the rest of the walk via the same capacity-cascade mechanism this project has repeatedly measured
(gate-width's own shortlist arms showed lower overlap than a truncation-only argument would predict, for
exactly this reason). **Minimum-sample floor: 100 differing (non-shared) trades per pairing**, unchanged
from precedent — below that, reported as a bare count, not interpreted.

## 8. Verification plan — no degenerate-case parity exists for B/C, alternate checks required before trusting either

**Stated honestly, as the monthly study's own pre-registration already stated for its construction**:
there is no existing production engine B/C reduce to when their own new parameters are set to a
no-op value, so byte-identical parity verification (the position-count and deployment-cap studies' own
standard) is not available here. **The same four-part alternate verification the monthly study used is
reused, adapted to the daily engine, before any real B/C result is trusted:**
1. **Staleness guard** — the synthetic-gap regression already built for the monthly signal
   (`cross_sectional_signal.py`, unmodified — the signal computation itself does not change between the
   monthly and daily constructions) reused directly, plus a real-data scan across both pools' full
   history for any genuine guard fire.
2. **Exit-wiring** — a sample of trades' recorded exit reason checked against `evaluate_exit_reason()` /
   `evaluate_rank_dropped_exit()` called directly with the trade's own reconstructed inputs, across B
   and C — including explicit confirmation that every `SIGNAL_OFF` exit's own `sig_on` input traces back
   to the *absolute* momentum value, never the cross-sectional rank, for all three arms (the one thing
   §1's narrowing depends on not leaking).
3. **Sizing** — spot-checked against the engine's own emitted pre-entry balance (not a proxy computed
   after the fact, per the monthly study's own documented lesson about exactly this failure mode).
4. **Ranking hand-check** — a small number of regime dates, both pools, both arms, hand-computing
   `compute_cross_sectional_rank_series()`'s own top-N set and confirming the engine's admissions/holds
   never include a ticker that wasn't legitimately eligible that day.
5. **Signal-separation check, new, specifically targeting §1's narrowing**: confirm directly (not by
   code-reading alone) that B/C's gate-passing candidate set on a sample of dates is identical to what A
   would compute for the same dates — i.e. that swapping the sort key genuinely did not also change who
   is eligible. A B/C eligible set differing from A's own on any sampled date would mean the gate signal
   was not as cleanly separated from the sort signal as §1 requires, and would need to be fixed before
   any B/C result is trusted.

Arm A needs none of this — it is the unmodified production call, verified the same way as every prior
study's own baseline arm (§9).

## 9. Sanity gate

**Arm A must reproduce a fresh reference run of the unmodified `simulate_portfolio_compounding_mtm()`
at `per_trade_pct=0.05`, `max_total_deployed=0.70`** — compared as trade SETS (ticker, entry_date,
exit_date, exit_reason, P&L), never totals alone, per this project's own repeated "derived state
decays" lesson. The position-count and deployment-cap studies' own baseline trade files
(`trades_{pool}_n22.csv` / `trades_{pool}_c70.csv`) are available as an immediate cross-check but not
assumed still current — the database may have moved since either was generated, so a fresh reference is
still required at run time, with any discrepancy row-diffed and explained (not assumed benign) exactly
as the last three studies in this sequence have each done.

## 10. Pools

Pool 1 (`get_live_stock_candidates()`, ranking-direction only) and Pool 2 CLEAN
(`get_research_stock_candidates() ∩ status == 'CLEAN'`, decisive) — **both reconfirmed fresh at run
time**, not assumed from any earlier session's count, per this project's now-repeated lesson on exactly
this point.

## The walk-away sentence

**I will not adopt B or C if: no arm clears +1.5pp cumulative cost-adjusted excess return over
baseline's own excess return without exceeding baseline's own peak-relative drawdown by more than 15pp;
or if any arm's mean deployment diverges from baseline by more than 2pp (that arm is reported as
not-tested, not as a result); or if the A-vs-B admission overlap comes back ≥80% (B is reported as not a
meaningful test of relative-rank selection, not evaluated as a result on its own return numbers); or if
the paired per-decision instrument shows a winning arm's own unique admissions are not distinguishable
from baseline's on a usable sample.** An arm that wins mainly by holding more or less cash than baseline,
that is functionally a relabeling of baseline's own admissions rather than a genuinely different
selection, or whose extra return cannot be traced to its own unique decisions being better than
baseline's, has not demonstrated that relative-rank selection is better than absolute momentum — only
that a number moved for a reason this study was specifically built to rule out before crediting it.

## Constraints re-confirmed

No changes to `analysis/signals.py` or `config.yaml`. The only permitted `analysis/decision.py` usage is
calling the existing, unmodified `evaluate_rank_dropped_exit()` — no new function, no modification to
any existing one. Research only, in the dedicated worktree, not the shared main checkout. Nothing
adopted, nothing merged, nothing deployed.
