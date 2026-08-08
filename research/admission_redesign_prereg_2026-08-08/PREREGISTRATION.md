# Monthly-shortlist admission redesign — pre-registration (2026-08-08)

**PRE-REGISTRATION ONLY. Nothing has been run.** No cache built, no backtest executed, no
production file touched. This document exists so the criteria, the caveats, and the walk-away
condition are on record *before* any number exists that could tempt a rationalization after the
fact. Per instruction, this gets the strictest version of this project's own process: the first
change all week that would actually alter trading behaviour if it were ever adopted.

## 0. Motivation, restated precisely (not re-litigated)

From `research/selection_audit_2026-08-08/REPORT.md`:
- **Part 7**: momentum-ranked admission sits at the **99th percentile** of a 100-run random-admission
  distribution on both frictionless CAGR (6.63% vs. 1.50–6.79% random range) and cost-adjusted CAGR
  (3.27% vs. −2.02% to 3.37%). Ranking is doing real work.
- **Part 4**: **92–93%** of every real admission, in both pools, happened only because that day's own
  exits freed the exact capacity slot filled — selection is dominated by *timing*, not by comparing
  the full field of qualified candidates against each other.
- **Part 7, also**: baseline's −54.02% max drawdown is worse than 98/100 random runs. The one
  mechanism that adds value already buys that value with concentration — a redesign that
  concentrates *further* (top-50 shortlist, monthly batching) is not free to ignore drawdown.

**The redesign under test** — one arm, vs. current baseline, nothing else varied:

| | Baseline (current production logic) | Redesign |
|---|---|---|
| Candidate pool per decision | Whatever passes the gate *today* | Top 50 by momentum among ALL gate-passing candidates, re-ranked at each rebalance |
| Entry timing | Continuous — any day a slot opens | Monthly rebalance only |
| Slot-filling | Greedy skip-and-continue down whatever's eligible that day | Greedy skip-and-continue down the top-50 shortlist, only on rebalance days |
| Freed mid-cycle capital | Redeployed same day | Sits idle until the next rebalance |
| Exits | `CRISIS_EXIT` → `STOP_LOSS` → `SIGNAL_OFF` → `MAX_HOLDING`, checked continuously | **Unchanged** — identical priority, identical continuous checking |
| `atr_stop_multiplier`, `max_total_deployed`, `per_trade_pct`, universe, engine, cost template | 2.0 / 0.70 / 0.05 / (see §3) / `simulate_portfolio_compounding_mtm()`-derived / established FX+idle template | **Identical, held fixed** |

**Scope, stated as a deliberate choice, not left as an interpretation (revised after review — see
§8's changelog note): this arm tests batching + shortlist ONLY.** Slot-filling remains greedy —
rank the top-50 shortlist by momentum, admit down it in order until capacity binds, monthly. It does
**not** add a quality bar beyond top-50-plus-capacity, and it does **not** leave a slot unfilled when
capacity and shortlist candidates both exist. This means the redesign as specified here addresses
Part 4's finding only partially: widening the field from "whoever's eligible today" to "the best 50
across the whole universe, monthly" already removes most of the same-day-availability effect, but
does not test *selective* admission — holding a slot empty because nothing on the shortlist clears
an absolute merit bar, independent of whether capacity exists. That third mechanism is real,
motivated by the same Part 4 finding, and deliberately **out of scope for this arm** — a follow-up
arm (shortlist + monthly cadence + an explicit quality floor, e.g. admit only shortlisted names
above some momentum threshold, leaving capacity unused otherwise) is the natural next pre-
registration if this one passes, not something to fold in here as a fifth uncontrolled variable.

---

## 1. Pass/fail criterion — written before any run, numeric, not vague

**Primary metric**: `Ratio = cost_adjusted_CAGR% / |peak_relative_max_drawdown%|` — "return per unit of
drawdown." Computed identically for both arms, in **Pool 2 CLEAN only** (§3). CAGR and drawdown are
also reported individually — the ratio is the *deciding framing*, not the only number shown.

**PASS requires ALL FIVE of the following, simultaneously, in Pool 2 CLEAN:**

1. **`Ratio_redesign ≥ 1.20 × Ratio_baseline`** (redesign's return-per-unit-of-drawdown must beat
   baseline's by at least 20% relative). *Why 20%, not smaller*: no noise-floor measurement exists
   yet for this exact ratio (flagged as a gap, §8) — the existing measured noise floor for its
   numerator alone (cost-adjusted CAGR, ~3.8% relative / 0.13pp typical, 1.08pp worst single-draw,
   from the 2026-08-08 measurement-toolkit run) is for *trivial* perturbations of the *same*
   admission design. Swapping daily-greedy for monthly-shortlist is a far larger structural change,
   so the bar is set well above the trivial-perturbation floor, not merely above it.
2. **`DD_redesign ≤ DD_baseline + 8.0 percentage points`** (absolute ceiling). This number is not
   arbitrary — it is exactly the boundary from your own worked bad-case example ("−54% to −62%").
   Anything at or past that boundary is an automatic fail regardless of what CAGR does.
3. **`CAGR_adj_redesign ≥ CAGR_adj_baseline − 1.5 percentage points`** (absolute floor on return).
   Prevents a degenerate "pass" where the ratio improves because the redesign trades so little it
   barely deploys capital — great ratio, useless strategy. A redesign that wins on drawdown but
   gives back more than 1.5pp of cost-adjusted CAGR to get there does not pass, no matter what the
   ratio says.
4. **`mean_deployed_pct_redesign ≥ mean_deployed_pct_baseline − 15.0 percentage points`** (guards
   the *denominator* side of the same degenerate case condition 3 guards on the numerator side): an
   arm that simply sits in more cash gets a smaller drawdown and a flattering ratio without doing
   anything the redesign is actually meant to test. Computed directly from each arm's own daily
   deployment panel (§5) — not a proxy. Wait-for-rebalance cash is a design feature of this redesign
   specifically, so this is a real, expected risk here, not a hypothetical guard.
5. **Per-decision corroboration** (§4) does not contradict the portfolio-level direction. If the
   portfolio-level numbers say redesign wins but the paired/distributional check says the
   comparable, cascade-uncontaminated subset of decisions is flat or favours baseline — exactly the
   pattern already found for stop Arm A vs. B — that is an automatic fail, independent of 1–4.

**Corroborating only, never decisive on its own**: 16-block pass count. The measured resolution
floor for this statistic is **3 blocks** (6–9, from the 17-draw empirical-noise-floor run,
2026-08-08) — wider than the earlier ±2 estimate, not narrower — and per the capacity-cascade note,
any per-block statistic is structurally biased toward showing *no* difference for a small, real,
consistent effect, since sequential capacity-constrained admission smears a genuine improvement
across many blocks rather than concentrating it in one. A block-count delta of ≤3 will not be read
as evidence either way. A larger delta is reported but still subordinate to the five PASS conditions
above.

## 2. The walk-away sentence (written now, not skipped)

**I will not adopt this redesign if it worsens Pool 2 CLEAN peak-relative drawdown by 8 percentage
points or more for any amount of CAGR gain, or if it gives back more than 1.5pp of cost-adjusted
CAGR to buy a drawdown improvement, or if it earns a flattering ratio mainly by holding more cash
(mean deployment falling more than 15pp below baseline's), or if the per-decision paired/
distributional instrument contradicts the portfolio-level headline's direction — the exact failure
mode already found once this month for the stop-loss A/B comparison, where a 1.94pp portfolio-level
gap did not survive contact with a capacity-free per-decision view.** Any one of these four outcomes
ends the investigation with "not adopted," not a request to re-tune the shortlist size or rebalance
frequency and try again inside this same pre-registration.

## 3. Universes — both pools, explicit construction, explicit role

- **Pool 1** — `analysis.universe.get_live_stock_candidates(as_of=<run date>)`, ~503 tickers.
  **Ranking-direction check only.** Maximal survivorship bias (today's real membership projected
  backwards) means its own absolute CAGR/drawdown numbers are not trustworthy in absolute terms and
  will not be used for §1's PASS/FAIL arithmetic — only to confirm the *direction* of whatever Pool
  2 CLEAN finds (does redesign beat baseline on Ratio here too, same sign, same rough shape). A
  direction disagreement between Pool 1 and Pool 2 CLEAN does not automatically fail the study, but
  triggers the same re-investigation discipline as any dual-pool disagreement under the standing
  "Dual-provenance validation" rule — not a silent average, not a discard of whichever pool
  disagrees.
- **Pool 2 CLEAN** — `analysis.universe.get_research_stock_candidates()` **intersected with**
  `ticker_data_quality.status == 'CLEAN'` (last confirmed count: 220 tickers, 2026-08-08 — **to be
  re-confirmed fresh at run time**, not assumed, since the `CLEAN` population has grown before and
  may again). **Decisive pool** for §1's PASS/FAIL numbers, specifically because this is a
  drawdown-sensitive comparison and the stop-loss reconfiguration study already found
  `NEEDS_REVIEW`-tagged tickers disproportionately drove Pool 2's *tail* losses — precisely the
  statistic (drawdown) this redesign is being judged on most strictly. Using the full,
  unrestricted `get_research_stock_candidates()` pool (1,042 tickers, includes 25 `NEEDS_REVIEW`)
  for a drawdown-decisive comparison would re-import exactly the contamination risk that rule
  exists to close.
- Both engines (baseline call, redesign engine) read the **identical** shortlist/candidate universe
  per pool — no separate "redesign universe."

## 4. The paired per-decision instrument — what it means here, stated in advance

**The literal, off-the-shelf `paired_comparison()` (exact `(ticker, entry_date)` key match) is very
likely NOT usable in its intended sense for this comparison, and this is stated now rather than
discovered after a run.** That function's validity rests on a shared key meaning "capacity allowed
the identical decision under both arms" — true for the stop A/B and adj_close comparisons because
both arms in those studies shared the *same daily entry cadence* and differed only in exit/signal
treatment. Here, the redesign's entries live on a monthly grid (~228 rebalance dates over 19 years)
while baseline's live on a daily grid (~4,900 possible dates) — a genuine `(ticker, entry_date)`
match between the two arms would require baseline to have independently chosen to enter the exact
same ticker on the exact same rebalance date, which will be rare and not representative of anything
except coincidence. **Pre-registered decision rule**: run the literal `paired_comparison()` anyway
and report the shared-pair count; if it falls below **200 shared pairs** (the smallest sample size
the measurement toolkit's own validations treated as workable — the stop A/B capped comparison used
168), it will be reported as "not usable at this grain" rather than interpreted, exactly as
instructed, not improvised into a headline number.

**What will actually be built and used as the primary per-decision check — a genuine adaptation, not
the toolkit's function unmodified, flagged as such**: a **month-level pairing**. For every
calendar month, and every ticker held by *both* arms at any point overlapping that month (regardless
of the exact entry day within it), pair the two arms' own realized returns for that ticker/month.
This controls for "did both arms independently converge on the same name around the same time" —
the closest analog to "shared decision" the cadence mismatch allows — but it is **not** fully
capacity-cascade-free the way the exact-key version is: within-month capacity dynamics still differ
between the two arms, so a residual cascade contribution is possible and will be disclosed, not
assumed away, alongside any number this produces.

**Supplementary, cheap, explicitly-caveated**: `disagreement_population_comparison()` run literally
(comparing the full outcome distribution of each arm's trades, since nearly all will register as
"unique" by exact date for the structural reason above, not from genuine signal disagreement). This
will be labelled as an arm-level distributional comparison, not a decision-level isolation, since the
tool's own "unique-to-one-arm reflects genuine disagreement" assumption does not transfer cleanly to
a cadence-different comparison.

**The uncapped-diagnostic-arm instrument (Instrument 3) does not transfer cleanly here either, flagged
in advance rather than forced.** Its validity rests on removing the *capital-deployment* capacity
constraint to make admission deterministic given the signal. The redesign already has a second,
independent scarcity mechanism — the top-50 shortlist itself — that an uncapped `max_total_deployed`
does nothing to remove. Running baseline uncapped remains useful (as a reference point for how much
of baseline's own edge is deployment-cascade-amplified, reusing the existing 2026-08-08 measurement
directly rather than re-running it). An "uncapped redesign" is not pre-registered as a planned run —
if it turns out to be useful during execution, it needs its own pre-registration, not an improvised
extension of this one.

## 5. Modelling the wait-for-rebalance cash — honestly, not silently redeployed

Capital freed by a mid-month exit is **not** available for the redesign's Phase B until the next
rebalance date, by construction — the new engine's exit logic (Phase A) runs identically every day,
crediting `cash` exactly as today; only Phase B (entries) is gated to rebalance dates. This
mechanically raises the redesign's average idle-cash fraction relative to baseline, and **per the
standing "a cost is safe to defer only while it's constant across the arms being compared" rule,
idle-cash credit will be modelled for both arms, not deferred** — this comparison is exactly the
case that rule exists for, since idle level differs by construction between the two arms, not
merely by chance. Both engines will emit a **daily deployment panel** (date, cash, mark-to-market
position value, `current_balance`, `current_deployed`, `current_deployed_pct`) so mean idle-% is
computed directly per arm, not approximated — reusing the established
`research/deployment_cap_sweep_2026-08-06/cost_adjustment.py` template (FX: 0.30% round-trip on real
`position_gbp`; idle credit: mean idle-% × mean `^IRX` rate over the identical window) for both the
FX leg (already standard) and, this time, the idle-cash leg computed from each arm's own real panel
rather than skipped.

## 6. Top-decile trade overlap — reported alongside every headline gap, not just once

Exact `(ticker, entry_date)` overlap of each arm's top-decile-by-P&L trades will be near-meaningless
for the same cadence-mismatch reason as §4. Instead: **top-decile ticker overlap**, a Jaccard measure
computed on ticker identity alone (ignoring date) — `|tickers in both arms' top-decile P&L trades| /
|tickers in either arm's top-decile P&L trades|`. Reported next to every headline CAGR/Ratio/DD gap
in the eventual report, not as a one-off footnote — per Part 1's own finding (top decile = 257% of
total P&L) and the `DHI`-single-ticker noise-floor precedent, two arms can differ mostly by which
lottery tickets they happen to hold, and this number is how that gets checked rather than assumed
away.

## 7. Sanity gate — exact, reusable reference figures, checked before anything else is trusted

Before Pool 2 CLEAN or the redesign engine is touched, the **unmodified**
`simulate_portfolio_compounding_mtm()` will be run against the **full, unrestricted** Pool 2
(`get_research_stock_candidates()`, current quarantine state, cap 0.70, Arm A / 2.0x ATR — i.e. the
exact configuration already published) and must reproduce, exactly:
- **4,922 trades, total P&L £114,990.04** (`research/stop_loss_reconfiguration_2026-08-07/trades_pool2_final_A.csv`
  — independently confirmed twice already: once by that study's own run, once by this session's
  selection-audit parity check, byte-identical both times).
- **7/16 block-pass count** (the current documented reference point for this exact pool, per
  CLAUDE.md's "16-block pass rate has a measured resolution floor" section — explicitly the
  *re-baselined* figure, not the older 9/16 one).

**If either figure doesn't reproduce exactly, the run stops and the discrepancy is reported before
building the Pool 2 CLEAN cache, the redesign engine, or anything else.** This gate exists
specifically so a stale database, a code drift, or an environment mismatch is caught before it can
quietly bias the actual comparison — the same discipline this project already requires before any
local sweep (CLAUDE.md's "Before any local research/backtest sweep" note).

**No equivalent published reference exists yet for Pool 2 CLEAN (220-ticker) at cap 0.70** — the
closest prior figure (`research/stop_loss_reconfiguration_2026-08-07`'s CLEAN-only drawdown
recomputation, −31.45% peak DD) used the *old*, pre-Wikipedia-refinement 116-ticker CLEAN population,
not the current 220-ticker one, and is not a valid reproduction target. The first Pool 2 CLEAN
baseline run in this study **becomes** the new reference point for this specific pool, established
fresh, not checked against an incompatible old number.

**Flag, carried forward from review, not resolved by anything above**: this means the *decisive*
pool — the one §1's five PASS conditions are actually computed on — has no independent reproduction
check at all. The sanity gate confirms the engine and environment are correct on full Pool 2; it
says nothing about whether the Pool 2 CLEAN restriction itself is being applied correctly, since
there is no prior number to check it against. **If anything in the Pool 2 CLEAN results looks
surprising, the first hypothesis should be the new engine or the CLEAN-filter construction, not the
finding** — this asymmetry is worth remembering specifically at the moment a result looks
interesting enough to want to believe.

## 8. Under-determined parameters — flagged now, not swept in this run

- **Shortlist size (50)**: not derived from anything measured — a first-pass value. Context, not a
  defence: at `max_total_deployed=0.70` / `per_trade_pct=0.05`, baseline typically holds roughly
  ~14 positions at full deployment, so a shortlist of 50 gives ~3.5x headroom over that, but the
  actual right number (is 50 meaningfully different from 30 or 80 for this redesign's own dynamics)
  is unknown and explicitly **not** swept here.
- **Rebalance cadence (monthly, defined as the first trading day of each calendar month)**: a
  first-pass value. Weekly, or a fixed N-trading-day cadence, are not tested here.
- **Selective admission ("no forced slot-filling" in the strict sense) is out of scope for this arm
  by deliberate decision, not left as an ambiguous interpretation** — see §0's revised scope
  paragraph. This arm isolates batching + shortlist size; a quality-gated follow-up arm is the
  next pre-registration if this one passes, not a variable folded in here.
- **A held position that falls out of the top-50 shortlist between rebalances is NOT force-exited**
  — per the instruction that only entries batch, exits stay continuous on the existing four reasons
  only. This is a real design choice with a real alternative (force-trim positions that fall out of
  the shortlist) that is not being tested here.
- **Hold count**: no separate cap beyond the existing capacity/cash mechanics — not an independent
  parameter of the redesign as specified, noted here only so it isn't mistaken for an oversight.

## What gets built, if this is approved (not done yet)

1. A new, additive, research-only engine (working name `simulate_monthly_shortlist_rebalance()`,
   copied from `simulate_portfolio_compounding_mtm()`'s skeleton, Phase A exit loop **byte-for-byte
   unchanged**, calling the same shared `analysis.decision`/`analysis.volatility` functions
   unmodified) plus a **degenerate-case parity check**: rebalance frequency set to "every day" and
   shortlist size set to ≥ universe size must reproduce the real baseline engine's own trade list
   exactly, before any real (monthly, top-50) run is trusted — the same "verify against production
   before use" discipline `instrumented_engine.py` and the measurement toolkit already established.
2. A Pool 2 CLEAN cache (220-ticker, current state) and a Pool 1 cache (if not already reusable from
   an existing, still-valid research artifact).
3. The month-level pairing adaptation (§4), built as its own small function, not a repurposed
   toolkit script.
4. Daily deployment panels (§5) from both engines, and idle-cash cost adjustment computed from them.
5. `research/admission_redesign_2026-08-08/REPORT.md` (or the actual run date) stating, for both
   pools: baseline vs. redesign CAGR/DD/Ratio, the four PASS/FAIL conditions evaluated explicitly,
   the paired/distributional corroboration result, top-decile ticker overlap, and the block-pass
   delta reported as corroborating-only.

**Nothing above has been executed. No cache, no engine, no run.**

## Constraints re-confirmed

No changes to `analysis/signals.py`, `config.yaml`, `analysis/decision.py`, or any live path. Every
new function this study needs is additive and research-only, following this project's own
established pattern (`instrumented_engine.py`, the measurement toolkit's `step3_uncapped_arm.py`)
rather than modifying a shared production function. Nothing adopted, nothing merged, nothing
deployed — this document is a plan for review, not a completed or partial run.
