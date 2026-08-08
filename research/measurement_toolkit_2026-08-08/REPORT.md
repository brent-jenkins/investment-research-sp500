# Measurement toolkit for small effects — four instruments, two validations

**Motivation, confirmed by every instrument below**: whole-portfolio backtests are a high-variance
instrument for small effects, because changing one variable reshuffles capacity-constrained
admission, which propagates through 19 years of compounding. This toolkit builds four
capacity-free-or-quantified alternatives, then validates them against two questions this project
already has portfolio-level answers for.

## Instrument 1 — empirical noise floor

**Method**: the standard baseline (`get_research_stock_candidates()`, cap 0.70,
`simulate_portfolio_compounding_mtm()`) run 18 times — 1 unperturbed baseline + 17 trivial,
substantively-meaningless perturbations: 8 single-ticker drops, 6 walk-start-date shifts (+1 to
+10 calendar days), 3 admission tie-break reorderings (shuffling the input ticker-list order that
sets the `idx` tie-break key — a pure research-script choice, no `analysis/backtest.py` change).
17, not 20, for time/resource reasons (this machine was sharing CPU with another active session
throughout the run) — reported plainly rather than padded to a round number.

### The distribution (17 perturbed runs vs. the unperturbed baseline)

| Metric | Baseline | Perturbed min | Perturbed max | Perturbed mean | Perturbed std | **Range** |
|---|---|---|---|---|---|---|
| Cost-adjusted CAGR | 3.380% | 3.321% | **4.401%** | 3.445% | 0.249 | **1.08pp** |
| Peak-relative max DD | −47.83% | −47.83% | −47.66% | −47.82% | 0.041 | 0.17pp |
| Total trades | 4,922 | 4,922 | 4,938 | 4,924 | 4.6 | 16 |
| 16-block pass count | 7 | 6 | **9** | 7.06 | 0.56 | **3 blocks** |

**The CAGR spread is uncomfortably wide, exactly as you expected — but it has a clear, bimodal
structure, not a diffuse one.** 16 of 17 perturbed runs land within **[3.321%, 3.512%]** of the
3.380% baseline — a tight ±0.13pp band. The 18th (dropping `DHI` alone) landed at **4.401%, +1.02pp
from baseline** — entirely responsible for the reported 1.08pp range. Every start-date shift and
every tie-break shuffle produced a change of ≤0.005pp; 5 of 8 ticker drops (`SNI`, `ADI`, `MFE`,
`PNR`, `TDY`) were **exact, byte-identical no-ops** — `SNI`/`MFE` were never in the usable-tickers
subset to begin with, `ADI`/`PNR`/`TDY` were usable but never won a capacity-relevant admission
slot in this specific run. Only tickers that were both usable *and* actually admitted at some point
(`LDOS`, `EQIX`, `DHI`) moved the number at all, and only `DHI` — evidently a heavily-traded
participant whose removal freed capacity for a materially different downstream sequence — moved it
by more than 0.13pp. **The noise floor is not "everything is noisy" — it's "most trivial
perturbations are true no-ops, and the rare ones that aren't can be large."** This is the same
shape of finding as the `CPWR` block flip, now confirmed on 17 independent draws rather than one.

**The block-pass noise floor is, if anything, slightly wider than previously documented.** The
existing CLAUDE.md standing requirement quantified ±2 blocks from the single `CPWR` case; this
17-draw sample shows a **3-block range** (6 to 9) — `DHI`'s drop moved it +2 (7→9, matching `CPWR`'s
own magnitude), `EQIX`'s moved it −1 (7→6). Reinforces, not just repeats, the existing rule: ≤2-3
block deltas from any single trivial perturbation should not be read as a discriminating result on
their own.

**Drawdown and trade count are far more stable than CAGR or block count** — 0.17pp and 16 trades
respectively, both tight. If a future finding needs a robust primary metric, peak-relative drawdown
looks like the steadiest of the four measured here.

Full per-run detail: `step2_noise_floor_summary.csv`. Trade-level output preserved for all 18 runs
(`trades_run*.csv`).

## Instrument 2 — paired per-decision comparison

**Method**: for two trades files sharing the same underlying entry logic, pair on `(ticker,
entry_date)`. A shared key means capacity allowed that exact entry under both arms — so its own
before/after outcome comparison carries zero capacity effect regardless of what else differs
between the two arms' broader trade lists. Modelled directly on the stop-loss study's own whipsaw
analysis (paired, per-case, no portfolio walk). A second function, `disagreement_population_
comparison()`, handles the case where pairing by shared key isn't the right tool (adj_close vs.
close, where a differing admission usually means the *entry itself* differs) — compares the outcome
*distribution* of positions unique to each arm instead.

Applied to both validations below — results reported there rather than duplicated here.

## Instrument 3 — uncapped diagnostic arm

**Method**: calls the real, unmodified `simulate_portfolio_compounding_mtm()` with `max_deployed_pct`
driven to 1,000,000% and `absolute_max_position_gbp` overridden to 1e15 in a copied config dict — no
`analysis/backtest.py` change, just unusual parameter values from a research script, so admission
becomes deterministic given the signal (modulo one honestly-reported residual: same-day cash
competition, since `resolve_cash_constrained_position_gbp()`'s real cash constraint is deliberately
*not* removed — you still can't buy more than you have cash for, a genuine economic constraint, not
an arbitrary ceiling). Confirmed **zero capacity rejections** in every uncapped run (`rejection_
reasons={}`). Never reachable from any production path — purely a research script's argument choice.

Applied to both validations below.

## Instrument 4 — benchmark-relative reporting

**Method**: `^SP500TR` (S&P 500 Total Return, dividends reinvested) — coverage confirmed directly
before use: 5,181 daily rows, 2006-01-03 through 2026-08-07, full coverage of the 2007-2026 backtest
window plus buffer. Reusable function (`step4_benchmark_relative.py:benchmark_relative_report()`)
computes continuous-walk excess CAGR and per-block (reset-at-boundary) excess return vs. this series
on the identical dates the strategy was measured over.

**Sanity-check result** (Pool 2 all, Arm A — the standard baseline): cost-adjusted CAGR 3.38% vs.
`^SP500TR`'s own 11.13% CAGR over the identical window — **excess CAGR −7.75pp**, beating the
benchmark in only 5/16 blocks. Sobering but consistent with this project's own repeatedly-stated
context (a system that still trails passive equity after costs). Available for use in any future
report; not run against every arm here given time constraints, since it wasn't needed to answer
either validation question below.

---

## Validation (a) — adj_close vs. close

**Portfolio-level said**: not decisive. Best cost-adjusted CAGR gap +0.43pp (Pool 2 CLEAN, below the
+0.50pp floor), best block concordance 8/16 (a coin flip), Pool 1 and Pool 2 CLEAN disagreeing on
direction.

**Uncapped instrument (Pool 2 CLEAN)**: admission overlap jumped from the capped run's ~33%/Jaccard
~20% to **72.5%/68.8%** — direct, quantified confirmation that most of the capped disagreement was
capacity-cascade noise, not genuine signal disagreement. Aggregate uncapped P&L still favoured close
(£21,367.83) over adj_close (£10,682.27), a ~2x gap — but:

**Paired instrument (disagreement-population comparison, uncapped)**: the actual per-decision
question — among the 5,333 close-only and 6,370 adj_close-only admissions, do the outcomes differ?
**No, not meaningfully.** Mean pnl_pct 0.0020 vs. 0.0024 (a 0.04pp difference), **identical medians**
(−0.0115 both), win rates 41.1% vs. 41.9%. The large aggregate £ gap (£10,685.56) is not explained by
per-decision quality — the per-trade £ means are both near zero and even point the opposite direction
from the pnl_pct means (A-only +£0.84/trade, B-only −£0.37/trade vs. B-only having the *higher*
pnl_pct mean) — a signature of compounding-path/position-sizing divergence (the two arms' account
balances diverge over 19 years once their trade sequences diverge, so identical-percentage trades get
sized differently), not of one signal picking better trades than the other.

**Verdict: the new instruments AGREE with the portfolio-level finding, they do not contradict it.**
Confirmed present (the mechanism is real — sector tilt, positive control), confirmed small
(disagreement-population outcomes are nearly identical), and now additionally confirmed that even the
aggregate £ gap that does exist is a sizing artifact rather than a signal-quality difference. This
strengthens, rather than merely repeats, the original "not adopted" conclusion.

## Validation (b) — stop Arm A vs. Arm B

**Portfolio-level said**: B (no stop) wins cost-adjusted CAGR by 1.94pp (Pool 2, current pool).

**Paired instrument, capped (existing trades, no re-run needed)**: only 528/4,922 shared positions
(10.7%) — of the 168 that differ in outcome, B wins only **45.8%** (n too small for a confident
read: SE≈3.9pp, so not distinguishable from 50% at 95% confidence). Shared-subset total P&L diff:
+£3,528.68 — roughly a tenth of the portfolio-level gap.

**Uncapped instrument**: total P&L nearly IDENTICAL between having a stop (£246,480.69, 59,886
trades) and not (£245,463.95, 40,297 trades) — a 0.4% difference, despite A having 48.6% more trades.
Paired comparison on this uncapped, much larger sample (6,712 shared pairs, **2,444 differing** — 14x
the capped sample): B wins **51.4%** of differing cases (95% CI ≈ [49.4%, 53.4%] — right at the edge
of distinguishable from a coin flip, leaning marginally toward B). Shared-subset total P&L diff:
+£8,874.97.

**Is 1.94pp above the empirical noise floor from Instrument 1?** The noise floor's single largest
perturbation-driven deviation (dropping `DHI` alone) was +1.02pp. 1.94pp is **larger than the single
largest noise-floor draw observed**, but only by a factor of ~1.9x, on a sample of just 17 draws — not
overwhelming evidence either way, stated as a ratio rather than a verdict.

**Verdict: this is the significant methodological finding you flagged as a possibility — the paired
instruments do NOT support the portfolio-level headline's magnitude.** On a genuinely capacity-free,
much larger sample (2,444 paired decisions vs. one 19-year path), removing the stop wins barely more
than half the time, and total P&L is nearly identical uncapped. The 1.94pp portfolio-level gap is
larger than what either paired instrument shows at the per-decision level, and larger than the single
biggest noise-floor draw — real, but the portfolio-level number appears to substantially **overstate**
the size of B's true advantage, likely because capacity-cascade amplification (a stop-loss exit frees
capacity earlier, letting different subsequent candidates in for the rest of the 19-year walk — the
same mechanism `CLAUDE.md`'s capacity-cascade note already documents) inflates the portfolio-level
comparison well beyond what the underlying decision actually earns.

**This does not reverse the recommendation** — B still wins the paired comparison directionally
(51.4%, uncapped shared-subset P&L favors B) — but it should be read as a much weaker, noisier
advantage than the 1.94pp headline suggested, closer to "a small, real edge, mostly amplified by
portfolio mechanics" than "B is clearly better." Reported plainly, not reconciled: the portfolio-level
and paired numbers disagree on *magnitude*, and that disagreement is itself the finding.

---

## Guardrail checks

- **Contradictions reported plainly, not reconciled** — validation (b)'s paired/uncapped result
  materially undercuts the portfolio-level headline's magnitude; stated above without softening.
- **No instrument declared broken for lack of data** — every comparison had a workable sample
  (smallest: 168 differing pairs, capped stop A/B; largest: 2,444, uncapped stop A/B).
- **17 perturbations, not 20** — resource/time constraint, stated plainly in Instrument 1 rather than
  padded silently to the round number.
- Full trade-level output preserved for every run; large `rejections_*.csv`/`.pkl` caches excluded
  from the commit per this project's established convention (none were large this time — no
  `collect_rejections=True` runs produced a large file, unlike the earlier stop-loss/adj_close work).

## What was NOT done, per explicit scope

No production code, config, or signal changes. Nothing adopted. `analysis/signals.py`, `config.yaml`,
and the live path were not touched.
