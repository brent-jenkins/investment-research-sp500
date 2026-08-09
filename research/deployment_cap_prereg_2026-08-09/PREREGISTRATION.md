# Deployment-cap (max_total_deployed) study — pre-registration (2026-08-09)

**PRE-REGISTRATION ONLY. Nothing has been run beyond the pre-check in §0, which was specifically
requested to be done cheaply and reported before writing arms.** Config-level sweep, no new engine —
kept short deliberately, per instruction.

## 0. Pre-check — resolved: the cap is NOT rarely binding, the study is not moot

**The stated premise ("mean realised deployment was ~63% against a 0.70 cap, i.e. not binding") is
correct as a full-sample average but misleading as a description of current, steady-state behaviour —
checked directly against data already on hand from the position-count study, not assumed either way.**
Two independent measures, both pools, using `research/position_count_prereg_2026-08-09`'s own
`panel_{pool}_n22.csv` (baseline, `per_trade_pct=0.05`, `max_total_deployed=0.70` — today's actual
config) and `rejections_{pool}_n22.csv`:

**1. The deployed-% distribution is not centred near its own 63% mean — it's bimodal, and the upper
mode sits at the cap.** Median `current_deployed_pct` is **68.97% (Pool 1) / 69.10% (Pool 2 CLEAN)** —
within 1–1.03pp of the 0.70 cap, not near the 63% mean at all. 33–35% of all trading days sit at
≥69.5% (essentially pinned against the ceiling; max observed is 72.6–72.9%, achievable because the cap
only gates *new entries* — an already-open position's mark-to-market value can drift the realised
figure above the cap between entries, since capacity isn't re-checked continuously). **The 63% full-
sample mean is an artefact of averaging in 2007 (zero trades, cold start) and 2008–2009 (crisis-driven
mass exits) — from 2010 onward, every single year shows mean deployment 66–69.5% and median 68–69.5%,
both pools, no exception.**

**2. In steady state, the cap binds on essentially every trading day.** Restricting to 2010 onward
(4,174 of 4,930 trading days, both pools): a qualifying, ranked, gate-passed candidate was rejected
purely for exceeding the 0.70 cap (`REJECTED_CAPACITY`) on **99.7% of steady-state trading days, both
pools** (4,163/4,174). This is not a rare, occasional constraint — it is close to the default state of
the system in every year since the crisis.

**Verdict: proceed.** The cap is overwhelmingly the binding constraint in steady state, not sizing or
availability — the opposite of what the full-sample mean alone suggested. The four arms below are
justified and specified in full.

## 1. Motivation

Direct continuation of the deployment-parity work in `research/position_count_prereg_2026-08-09`
(§0 above reuses that study's own panel/rejection data) and the two prior looks at this lever named in
the task: `research/deployment_cap_sweep_2026-08-06` (0.50/0.70/0.95, cost-adjusted CAGR at every level
underperformed the 0.50 baseline once FX/turnover costs were modelled — CLAUDE.md's "A cost is safe to
defer only while it's constant across arms" section) and the earnings-dampener finding CLAUDE.md itself
already flags as weak (`+2 blocks` at 0.70, "sits exactly inside" the later-measured ±2-block noise
floor — CLAUDE.md's "16-block pass rate has a measured resolution floor" section). **Both predate the
realistic intraday-touch stop-fill correction (2026-08-06), the recycled-ticker-symbol quarantine
(2026-08-07/08), and the current FX+idle-cash cost template** — worth re-testing on today's corrected
data rather than treated as settled, per the task's own framing. Neither prior look is treated as
already having answered this question; both inform the cost-modelling and margin choices below.

## 2. The arms

| Arm | `max_total_deployed` |
|---|---|
| Baseline | 0.70 (today's actual config value) |
| A | 0.80 |
| B | 0.90 |
| C | 0.95 |

**No new engine, no new `per_trade_pct` derivation.** Unlike the position-count study, this study
varies only `max_total_deployed`, which `simulate_portfolio_compounding_mtm()` (and its already-
parity-verified instrumented copy, `position_count_engine.py`'s `simulate_with_panel()`, reused
directly from the immediately preceding study) already takes as a direct call argument. Four calls per
pool, nothing else.

## 3. Held fixed, without exception

**Position-count configuration: `per_trade_pct=0.05`, unchanged — the position-count study concluded
NOT ADOPTED, so today's actual production value is what carries forward, not a value chosen for this
study.** Also fixed: daily admission cadence, `atr_stop_multiplier=2.0`, `stock_max_holding_days=30`,
the 2% momentum floor, absolute-momentum ranking (unchanged sort), universe, engine, cost template
(FX round-trip + idle-cash credit at mean `^IRX`, modelled per-arm — see §5), all exit priority and
logic unchanged.

## 4. The deployment guard is inverted here — a verification, not a disqualifying check

**In the position-count study, an unchanged mean-deployment figure across arms was the thing that let
n5/n10 slip past a naive check — the guard existed to catch an arm winning by holding more cash than
it should.** Here, deployment level *is* the treatment: an arm that fails to realise a materially
different mean deployment than baseline hasn't been tested at all, the same shape of concern Sec 1a
raised for the position-count study's own extremes, now inverted. **Required check, reported for every
arm**: realised mean deployment %, compared against that arm's own nominal cap. If an arm's realised
mean deployment doesn't track its cap — e.g. the 0.95 arm realises 80% because sizing or candidate
availability binds before the cap does — that arm is reported as not testing what it claims, exactly
per Sec 1a's own precedent, not folded into the return comparison as if the cap had been reached.
**A concrete reason to expect this could bite at the top of the range, stated in advance**: §0 already
found the *current* 0.70 cap doesn't fully saturate to 70.00% even in steady state (median ~69%, not
70%) — a comparable few-point gap between nominal and realised should be expected at every cap tested,
not treated as a failure on its own; only a gap large enough to leave an arm's realised deployment
closer to baseline's own than to its own nominal cap (the same materiality test the position-count
study used) counts as a genuine not-tested verdict.

## 5. Criteria

**Primary — excess return over `^SP500TR`, per block and cumulative**, computed identically to the
position-count study (`research/measurement_toolkit_2026-08-08/step4_benchmark_relative.py`,
continuous-walk convention). Reported as each arm's own excess return, and as that arm's excess return
minus baseline's own.

**Required margin: ≥1.5pp cumulative cost-adjusted excess-return improvement over baseline**, same
bar and same justification as the position-count study — the measured ~1.08pp noise floor (17-draw
perturbation study, 16/17 within ±0.13pp), 1.5pp being ~1.4x the largest single draw observed. Kept
unchanged rather than raised for a "bigger" structural change, consistent with both prior studies'
own reasoning: the bar exists to clear noise, not to scale with how large the tested change looks.

**Drawdown ceiling — one-sided against worsening only**, mirroring the position-count study's own
corrected wording (a direction, not a gap-vs-CAGR trade-off): an arm's peak-relative max drawdown must
not exceed baseline's own by more than **15pp** — the same magnitude used in the position-count study,
kept for consistency between the two studies run back to back rather than re-derived from a different
anchor. **Expected to bind, and expected to bind for a genuinely different reason than the position-
count study's own drawdown finding, stated explicitly so the two aren't conflated**: the position-count
study found *more* positions at *constant* total deployment improved drawdown (diversification). This
study raises total deployment itself, at constant `per_trade_pct` — less of the portfolio sits in cash
during a market-wide decline, a direct exposure effect, not a concentration effect. Part 7 of the
selection audit's own ~13–17pp concentration-vs-random drawdown gap is a plausible-magnitude anchor for
a comparably-sized exposure change, not a mechanism match.

**Costs modelled per-arm, not deferred — required, not optional, per the standing rule.** Trade count
scales directly with `max_total_deployed` (the 2026-08-06 sweep measured 4,086→5,717→10,401 across
0.50/0.70/0.95 on the pre-correction data; a comparable scaling is expected here). FX round-trip cost
(0.30% of each arm's own real total notional) and idle-cash credit (each arm's own real mean idle %,
at the mean `^IRX` rate over the window) computed per arm from that arm's own real trade-level output,
exactly the `cost_adjustment()` methodology already built and reused twice.

**Curve discipline.** All four arms (0.70, 0.80, 0.90, 0.95) reported together, in cap order, on
excess return and drawdown. A monotonic trend means the optimum is not bracketed by this study, stated
directly rather than crowning 0.95. A non-monotonic curve (an interior cap beating both a lower and a
higher neighbour) is the finding this design is positioned to detect.

**Top-decile trade overlap**, exact `(ticker, entry_date)` Jaccard and ticker-only Jaccard, reported
alongside every headline gap — same two measures both preceding studies used, for direct
comparability.

**Operational headroom — qualitative context, not a scored condition, stated once and not re-litigated
per arm.** At 90–95% deployment, there is little room to admit a new high-conviction candidate without
an existing position first exiting — a real, live-relevant constraint on responsiveness this backtest's
own frictionless mechanics don't penalise directly (admission is still evaluated correctly against
whatever capacity exists that day; what's absent is any cost for *how often* capacity was simply
unavailable when a strong candidate appeared). Reported in the write-up as context for the
recommendation, not folded into any pass/fail criterion above.

## 6. Paired per-decision instrument

**Included following the established practice of both preceding studies, though not itemised in the
brief** — reused because it is exactly the tool this project already relies on to distinguish a
genuine quality difference from capacity-cascade reshuffling (CLAUDE.md's "capacity-cascade
amplification" standing note), and a deployment-cap change alters *which* candidates get admitted
(more capacity → deeper into the ranked list gets reached) at least as directly as either prior study's
own treatment did. Exact `(ticker, entry_date)` pairing against baseline (all arms share the daily
clock, no rebalancing), win rate / mean return on each arm's own unique admissions vs. baseline's,
100-trade minimum-sample floor before interpreting, unchanged from precedent.

**Block count: corroborating only**, same 3-block resolution floor (6–9 range) as both preceding
studies.

## 7. Pools

Pool 1 (`get_live_stock_candidates()`, direction only, not decisive) and Pool 2 CLEAN
(`get_research_stock_candidates() ∩ status == 'CLEAN'`, decisive) — **both reconfirmed fresh at run
time**, not assumed from this pre-registration's own §0 figures, which are already several hours old
by the time any arm actually runs.

## 8. Sanity gate

**The 0.70 (baseline) arm must reproduce the position-count study's own n22 baseline trade set —
compared as trade SETS (ticker, entry_date, exit_date, exit_reason, P&L), never totals alone.** Since
this study's baseline is the identical call (`per_trade_pct=0.05, max_total_deployed=0.70`) already
verified byte-identical to raw production in the position-count study, this is expected to pass
trivially — but must still be checked directly against a fresh reference at run time, not assumed to
still hold, per this project's own repeated "derived state decays" lesson (database content moves
daily even when no code changes).

## The walk-away sentence

**I will not adopt any deployment-cap target if: no arm clears +1.5pp cumulative cost-adjusted excess
return over baseline's own excess return without exceeding baseline's own peak-relative drawdown by
more than 15pp; or if an arm's realised mean deployment sits closer to baseline's own realised
deployment than to its own nominal cap (that arm is reported as not-tested, not as a result); or if
the trend across 0.70→0.80→0.90→0.95 is monotonic (no cap is bracketed as an optimum); or if the
paired per-decision instrument shows a winning arm's own unique admissions are not distinguishable
from baseline's on a usable sample.** A higher cap that wins mainly by scaling turnover into a cost
template that was supposed to catch exactly that (per the 2026-08-06 sweep's own prior finding), or
that never actually reaches the deployment level it's named for, has not demonstrated that the current
0.70 cap is too conservative — only that a number moved.

## Constraints re-confirmed

No changes to `analysis/signals.py`, `analysis/decision.py`, or the live path. `config.yaml` is not
modified — every arm's `max_total_deployed` is passed as a direct argument to the existing, unmodified
`simulate_portfolio_compounding_mtm()` / `position_count_engine.py` harness, research only. Nothing
adopted, nothing merged, nothing deployed.
