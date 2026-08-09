# Position-count (concurrent-holdings target) study — pre-registration (2026-08-09)

**Config-level sweep, no new engine — kept short deliberately, per instruction. Approved 2026-08-09
with two amendments (§1a, folded into §2's guard and the walk-away sentence below) — both about
viability at the extremes of the curve, not the middle. Thresholds otherwise unchanged from the
version reviewed.**

## 0. Motivation

From `research/selection_audit_2026-08-08/REPORT.md`, **Part 7**: momentum-ranked admission sits at
the **99th percentile** of 100 random-admission runs on both frictionless and cost-adjusted CAGR —
ranking works *because* it concentrates capacity into the strongest-ranked names, not despite it.
The same mechanism is why baseline's own drawdown sits at the **98th-worst percentile** of that same
distribution: concentration buys return and spends drawdown, in the same direction, from the same
cause. Position count is the lever that controls *how much* concentration the ranking gets to
express — holding fewer positions means each one is drawn from further up the same ranked list,
mechanically. This is the second of the two untested selection levers `gatewidth_prereg_2026-08-08`
identified alongside gate width; unlike gate width (which narrows *candidacy*), this narrows
*capacity* directly, which is the variable Part 7's own experiment actually manipulated. Live
currently runs at `per_trade_pct=0.05`, `max_total_deployed=0.70`, realizing a mean of roughly 22
concurrent positions — the reference point every arm here is compared against.

**Standing risks this design must not re-trip, read directly from CLAUDE.md before writing anything
below**: the measured noise floor (~1.08pp cost-adjusted CAGR range, 16/17 draws within ±0.13pp; block
count range 3, 6–9); capacity-cascade amplification (a small direct change can produce a much larger
trade-level reshuffle without a comparable aggregate effect); and the standing observation that two
prior periodic-rebalance redesigns each collapsed to a deployment-level artifact once checked against
a capacity/cascade-uncontaminated instrument. This study does not touch rebalance cadence at all
(admission stays on the existing daily clock throughout), but the deployment-parity failure mode is
explicitly in scope here for a different, structural reason — see §2.

## 1. The arms — a curve, not a point

| Arm | Target N | `per_trade_pct` | Derivation |
|---|---|---|---|
| 5 | 5 | **0.14** | `max_total_deployed / N = 0.70 / 5` |
| 10 | 10 | **0.07** | `0.70 / 10` |
| 22 (baseline) | ~22 (current realized behaviour) | **0.05** (unchanged) | Not derived from N — this is today's actual config value, kept as-is as the reference arm |
| 40 | 40 | **0.0175** | `0.70 / 40` |

**Derivation basis, stated explicitly.** `analysis/backtest.py:simulate_portfolio_compounding()`'s
own docstring states position count is "roughly `1/per_trade_pct` positions at full deployment" — at
`max_total_deployed=0.70` that's `0.70/per_trade_pct`. Solving for `per_trade_pct` given a target N
gives the three derived values above. **This is a nominal target, not a guarantee, and the
pre-registration treats it as such**: `_var_position_size_core()` sizes every position via historical
VaR against `min(max(VaR_size, min_position_gbp), account_value × per_trade_pct)` — the
`per_trade_pct` figure is a *ceiling*, not a fixed size, and VaR sizing scales inversely with each
candidate's own trailing volatility. This is exactly why live's real book (per_trade_pct=0.05, nominal
ceiling ~14 positions) realizes ~22 — VaR sizing routinely comes in under the ceiling. **The realized
mean position count per arm is a required output of this study, not an input** — reported plainly
against its nominal target, not substituted for it.

**No new engine.** `simulate_portfolio_compounding_mtm()` already takes `per_trade_pct` as a direct
call argument (`analysis/backtest.py:1174`) — this study is four direct calls per pool at the four
`per_trade_pct` values above, `max_total_deployed=0.70` fixed throughout. `config.yaml` is not edited;
the research harness passes each value straight into the function call.

**Held fixed, without exception**: `max_total_deployed=0.70`, daily admission cadence,
`atr_stop_multiplier=2.0`, `stock_max_holding_days=30`, reentry cooldown, the 2% momentum floor,
absolute-momentum ranking (unchanged sort — `_admit_group`'s momentum-descending order), universe,
engine, cost template (0.30% FX round-trip + idle-cash credit at mean `^IRX`, per this project's own
"a cost is safe to defer only while constant across arms" standard — turnover scales with N here, so
this is modelled, not deferred), all exit priority and logic.

## 1a. Two named viability risks at the extremes — added on review, both "the config value may not
produce the portfolio it nominally describes"

**These are one point stated twice, at opposite ends of the curve.** Both `per_trade_pct` derivations
in §1 are ceilings fed into `_var_position_size_core()`, not fixed sizes — VaR sizing scales inversely
with each candidate's own trailing volatility, so the realized portfolio can diverge from the nominal
target in either direction depending on which end of the curve is being tested.

**N=40 (`per_trade_pct=0.0175`) — sub-minimum rejection risk.** A nominal position at this ceiling is
small; low-volatility names size up toward the ceiling, high-volatility names size down, and some
candidates will land under `min_position_gbp` (£50, the VaR formula's own internal floor) or, after
the cash-availability clamp, under `is_position_size_viable()`'s final £10 floor. Every such candidate
is currently *silently* skipped by the production engine (`if not is_position_size_viable(position_gbp):
continue` — not logged even when `collect_rejections=True`) — invisible in the standard trade
export. **New requirement: the research harness must instrument this skip explicitly** (a
`REJECTED_SUBMINIMUM` event, distinct from `REJECTED_CAPACITY`) and report the count per arm. If a
meaningful fraction of the 40-arm's intended admissions are rejected sub-minimum, the arm realizes
fewer than 40 concurrent positions for a reason unrelated to selection quality, and the curve's
right-hand end is not measuring what it claims.

**N=5 (`per_trade_pct=0.14`) — under-allocation risk.** `absolute_max_position_gbp` is set high enough
to be inert at this scale, but VaR sizing may simply decline to allocate a full 14% share to a
volatile name, realizing more like 8–10 positions rather than 5 and under-deploying relative to the
70% cap. §2's existing named mechanical risk and 2pp deployment guard already cover this — the
addition here is a reporting requirement, not a new mechanism: **realized mean position count per arm,
alongside mean deployment %, so a failure here is legible as "the arm couldn't be built as specified"
rather than as "concentration didn't help."**

**Treatment.** Both risks are reported the same way: **realized mean position count and
`REJECTED_SUBMINIMUM` count are required outputs for every arm** (not just 40 and 5 — reported for
all four, so the two middle arms serve as an implicit control showing these numbers behave normally
away from the extremes). Any arm whose realized mean position count diverges materially from its
nominal target — read alongside, not overridden by, the existing 2pp deployment guard, since the two
failure shapes are related but not identical (an arm can be within 2pp on deployment while still
realizing a materially different position count than intended, e.g. by holding fewer, larger
positions) — is treated as **not-tested for that arm, not as a result**: reported and explained, not
folded into the return comparison as if the nominal target had been achieved.

## 2. Deployment parity — a design constraint, checked at a tighter bar than precedent

**Two prior studies (admission-redesign, cross-sectional momentum) each won on a headline metric
purely by holding more cash, not by better selection — both caught only by an explicit deployment
guard, not assumed away.** This study's own arms carry a real, specific, *mechanical* risk of the
same failure, distinct from those studies' cause (periodic rebalancing) and worth stating precisely
rather than gesturing at generically:

**At N=5, a single VaR-sized position may not be able to consume its full nominal 14% share.** VaR
sizing is capped at `account_value × per_trade_pct` but is *not* pulled up to that ceiling — a
lower-volatility candidate sizes smaller regardless of how much ceiling room exists. With only 5 slots
and no forced slot-filling, a run of lower-volatility admits could leave the 5-arm structurally
under-deployed relative to its own 70% cap, in a way the 22-position baseline (which has 4x the
slots to average across) would not show at the same target cap. This is the opposite failure shape
from the redesign studies (a structural ceiling-consumption limit, not a wait-for-rebalance idle-cash
gap), but it produces the identical symptom — a flattering return number that is actually a
under-deployment artifact — so it gets the identical guard.

**Guard, tighter than the 5pp precedent used for gate-width (that study had no equivalent mechanical
risk to guard against; this one does, so the bar is tightened accordingly)**: mean deployment % for
every arm must track baseline's own mean deployment within **2 percentage points**. **If any arm
diverges beyond 2pp, that arm is reported as not testing what it claims to test and is excluded from
the return comparison entirely — not down-weighted, not reported alongside its returns as a caveat.**
This is stated as a hard exclusion, not a soft flag, precisely because the last two studies' own
failure mode was letting an attractive return number stand next to a deployment caveat that a reader
could discount rather than treat as disqualifying.

## 3. Criteria

**Primary — excess return over `^SP500TR`, per block and cumulative.** Computed identically for every
arm using `research/measurement_toolkit_2026-08-08/step4_benchmark_relative.py` (already built,
coverage already confirmed 2006-01-03→2026-08-07), continuous-walk convention throughout (this
studies one already-decided system's realistic behaviour at each N, not a train/test split). Reported
as each arm's own excess return, and separately as **that arm's excess return minus baseline's own
excess return** — the comparison of interest.

**Required margin**: an arm counts as decisively better than baseline only if cumulative excess-return
improvement over baseline's own excess return is **≥1.5pp**. Justification: `^SP500TR`'s own return
is deterministic and identical across arms, so noise in excess return equals noise in the arm's own
CAGR — the same measured ~1.08pp floor (17-draw perturbation study, 16/17 within ±0.13pp) applies
directly. 1.5pp is ~1.4x the single largest noise-floor draw ever observed on this project, the same
bar the gate-width study used for its own structural change (candidate-pool width) — kept unchanged
here rather than raised, even though this study's own range (5 to 40 positions, an 8x span) is more
aggressive than gate-width's (25 to 330 candidates, a 13x span on a much weaker lever per Part 7):
raising the bar further would primarily protect against noise this study is comparably exposed to,
not a demonstrated larger source of variance specific to position count.

**Drawdown ceiling — one-sided against worsening only, not blended into a ratio or a CAGR trade-off.**
(The admission-redesign study's own condition-2 — "gives back more than 1.5pp of cost-adjusted CAGR to
buy a drawdown improvement" — compared a *gap between two deltas*, which is a strictly harder,
differently-shaped test than what "does drawdown get worse" actually asks; fixed here by testing
drawdown alone, directionally.) **Condition: an arm's peak-relative max drawdown must not exceed
baseline's own peak-relative max drawdown by more than 15 percentage points.** An arm whose drawdown
is equal to or *better* than baseline's always passes this condition regardless of its return. 15pp is
calibrated against Part 7's own measurement of the *same* concentration mechanism at a much smaller
scale: baseline (today's real ranking) already sits ~13–17pp worse on drawdown than the median/95th
percentile of 100 random-admission draws from the identical gate-passed pool. Since this study varies
concentration far more aggressively than ranking-vs-random did, a comparable or larger drawdown cost
at the 5-position end is the expected, not the surprising, outcome — 15pp is a real ceiling, not a
number chosen to always pass. **This condition is expected to do real work and bind at N=5**, per the
task's own framing; a breach there is a legitimate, informative not-adopted result, not a near-miss to
argue around.

**Curve discipline.** All four arms (5, 10, 22, 40) reported together, in position-count order, on
both excess return and drawdown. **A monotonic trend across the full range means the optimum is not
bracketed by this study, stated directly rather than crowning either endpoint.** A non-monotonic curve
(an interior N beating both a tighter and a looser neighbour) is the finding this design is actually
positioned to detect, and is the only shape evaluated against the ≥1.5pp/≤15pp bars above as a real
adoption candidate.

**Outcome variance — specific to this study, required reporting, not optional context.**
- **Total trade count per arm.** Fewer concurrent slots mechanically means fewer total trades over the
  same 19-year walk (turnover per slot is not expected to change materially, since exit logic is
  unchanged) — reported plainly so the sample size behind every other number in this section is
  visible.
- **Top-decile trade overlap vs. baseline**, both exact `(ticker, entry_date)` and ticker-only
  Jaccard — same two measures the gate-width study used, for direct comparability.
- **For the 5 and 10 arms specifically: each arm's own top 3 trades by P&L, as a percentage of that
  arm's own total P&L.** Motivated directly by Part 1 (top decile = 257% of the full 3,436-trade
  population's total P&L; the bottom half is net negative) — a concentrated arm with an order of
  magnitude fewer trades is mechanically more exposed to having its entire result explained by two or
  three positions. **If the top 3 trades' combined P&L exceeds 50% of that arm's total P&L, this is
  stated plainly as the finding it is** — "this arm's apparent edge is a small number of trades, not a
  broadly better selection process" — not folded quietly into a passing headline number. This is a
  reporting requirement for both arms regardless of whether either clears the primary bar; a passing
  arm that also trips this threshold has its pass explicitly downgraded (see walk-away sentence).

**Block count: corroborating only.** Measured resolution floor is 3 blocks (6–9 range, 17-draw
perturbation study) — any delta ≤3 blocks is not read as evidence either way, consistent with the
gate-width study's own treatment.

## 4. Paired per-decision instrument

**All arms share the identical daily admission clock** (this study changes only `per_trade_pct`, never
rebalance cadence), so exact `(ticker, entry_date)` pairing against baseline is directly constructible
— same method as the gate-width study, `paired_comparison()` /
`disagreement_population_comparison()` from `research/measurement_toolkit_2026-08-08/`.

**Prediction, stated in advance and treated as a real test, not a formality.** Naively, position count
changes only *capacity*, not *candidacy or rank order* — a candidate that wins a slot at N=5 should,
by construction, have ranked high enough to also win a slot most days at N=22 or N=40, since a tighter
arm only ever admits from the *top* of the same ranked, gate-passed list. This predicts an asymmetric
overlap: most of the 5-arm's and 10-arm's own admitted trades should also appear in baseline's larger
set (narrow-arm-own-trades mostly a subset of baseline), while the reverse direction (what fraction of
baseline's trades also appear in the 5/10 arms) should be much lower. For the 40-arm, the prediction
inverts: baseline's trades should mostly appear inside the 40-arm's own larger set.

**This naive prediction is explicitly flagged as at risk from the same mechanism that already falsified
an equivalent prediction once.** The gate-width study predicted high absolute overlap from an almost
identical "narrower list is a subset of the wider one" argument and measured only 40.6–44.8%, traced to
capacity-cascade amplification: a different capacity draw changes which exits happen and when, which
changes available slots for the rest of the 19-year walk, compounding a narrow direct change into a
broad trade-level reshuffle. **Reported here as the real, informative result it would be either way** —
if the subset prediction holds, that is itself evidence position count changes capacity cleanly without
much cascade-driven reshuffling of *which* candidates get in; if it doesn't, that is evidence the
cascade effect dominates position-count changes too, and is reported as such rather than smoothed over.

**Minimum-sample floor, stated in advance**: fewer than 100 differing (non-shared) trades for a given
baseline-vs-arm pairing is reported as a bare count, not interpreted — same floor as both prior studies
this project has run this way.

## 5. Pools

**Pool 1** (`get_live_stock_candidates(as_of=today)`) — ranking-direction check only, not decisive
(maximal survivorship bias by construction). **Pool 2 CLEAN** (`get_research_stock_candidates() ∩
status == 'CLEAN'`) — decisive pool. **Both reconfirmed fresh at run time**, not assumed from any
prior session's count, per this project's now-repeatedly-learned "derived state decays" lesson —
the database used for this study must itself be verified current before any cache is built.

## 6. Sanity gate

**The 22-position (baseline) arm is not a new computation — it is `per_trade_pct=0.05`, today's actual
config value, passed unchanged into the unmodified, real `simulate_portfolio_compounding_mtm()`.**
Required check: this arm's own trade-level output, on each pool, must match a freshly-generated
reference run of the same function at the same `per_trade_pct`/`max_total_deployed` on the same
database snapshot — **compared as trade SETS (ticker, entry/exit date, exit reason, P&L), never by
total P&L or trade count alone**, per this project's own repeated finding that an exact-total match is
not sufficient and a benign, fully-diagnosable discrepancy (a boundary-date price revision, one
ticker's admission outcome shifting) should be expected and explained, not treated as a failure by
default. Any discrepancy is row-diffed and explained in the report, exactly as the last two studies
did, before anything else in this study is trusted.

## The walk-away sentence

**I will not adopt any position-count target if: no arm clears +1.5pp cumulative excess return over
baseline's own excess return without exceeding baseline's own peak-relative drawdown by more than
15pp; or if any arm's mean deployment diverges from baseline by more than 2pp (that arm is excluded
from the return comparison, not merely caveated); or if any arm's realized mean position count
diverges materially from its nominal target, or shows a non-trivial `REJECTED_SUBMINIMUM` count (that
arm is reported as not-tested, not as a result, per §1a); or if the trend across 5→10→22→40 is
monotonic (no position count is bracketed as an optimum); or if a concentrated arm (5 or 10) that
would otherwise pass has more than 50% of its own total P&L explained by its own top 3 trades (that
arm's pass is downgraded to "not robust — driven by a small number of trades," not treated as a
genuine win); or if the paired per-decision instrument shows a winning arm's own unique admissions are
not distinguishable from baseline's on a usable sample.** A tighter arm that wins mainly by holding
more cash, by getting lucky on two or three positions, by a cascade-driven trade reshuffle, or by a
config value that quietly failed to build the portfolio it named, rather than by genuinely better
concentration, has not demonstrated that position count is an under-exploited lever — only that Part
7's already-known concentration trade-off (more return, more drawdown) looks however it happens to look
at whichever N was tested.

## Constraints re-confirmed

No changes to `analysis/signals.py`, `analysis/decision.py`, or the live path. `config.yaml` is not
modified — every arm's `per_trade_pct` is passed as a direct argument to the existing, unmodified
`simulate_portfolio_compounding_mtm()` in the research harness only. Nothing adopted, nothing merged,
nothing deployed.
