# Gate-width (momentum shortlist) study — pre-registration (2026-08-08)

**PRE-REGISTRATION ONLY. Nothing has been run.** Small, single-variable study — kept short
deliberately, per instruction.

## 0. Motivation

From `research/selection_audit_2026-08-08/REPORT.md`: **Part 7** — momentum ranking sits at the
**99th percentile** of 100 random-admission runs on both frictionless and cost-adjusted CAGR;
ranking does real work. **The gate itself is weak** — momentum > 0 plus the 2% floor admits ~330
candidates, essentially everything above a low bar. **Part 5** — capacity-rejected candidates'
counterfactual win rate (43.9–44.7%) and mean return are close to the admitted population's own
(43.1–44.4%), consistent with a gate that isn't discriminating much within the field it passes.

Today's admission-redesign study bundled monthly batching with a shortlist and failed on the
deployment guard (won by holding 24pp more cash, not by choosing better) — it tested nothing about
gate width specifically, because batching confounded it. **This isolates that one variable: same
daily clock, same everything else, only how many names the ranking gets to choose among changes.**

## 1. The arms — one variable, four points on a curve

| Arm | Candidacy |
|---|---|
| Baseline | Current gate: momentum > 0 AND ≥2% floor (~330 candidates) |
| Top-100 | The 100 highest-momentum names among gate-passers that day |
| Top-50 | The 50 highest-momentum names among gate-passers that day |
| Top-25 | The 25 highest-momentum names among gate-passers that day |

**No new engine needed.** `research/admission_redesign_prereg_2026-08-08/redesign_engine.py`'s
`simulate_with_panel()` already implements exactly this truncation — shortlist size is applied
*after* the existing gate and momentum sort, *before* the capacity walk, as an additive parameter
with no effect when `shortlist_size=None` (already degenerate-case-verified byte-identical to
production on Pool 1 today). This study is four calls — `rebalance_frequency="daily"` (unchanged
from today's default; **this is not a rebalancing test**), `shortlist_size` ∈ {None, 100, 50, 25} —
against each pool, nothing new to build or verify beyond re-confirming the sanity gate (§7).

**Held fixed, without exception**: daily admission cadence, `atr_stop_multiplier=2.0`,
`max_total_deployed=0.70`, `per_trade_pct=0.05`, `stock_max_holding_days=30`, reentry cooldown, the
2% momentum floor itself, universe, engine, cost template (0.30% FX round-trip + idle-cash credit
at mean `^IRX`), exits (`STOP_LOSS`/`SIGNAL_OFF`/`MAX_HOLDING`/`CRISIS_EXIT`, unchanged priority).

## 2. Why this can't be gamed by cash-holding — and the guard anyway

Deployment mechanics are untouched: the same capacity opens at the same times, filled the same way,
just from a shorter ranked list. There is no structural channel for a shortlist arm to hold more
cash than baseline the way monthly batching did. **Reported anyway, not assumed**: mean deployment
% per arm. If any shortlist arm's mean deployment diverges from baseline by more than **5
percentage points**, that is flagged explicitly as evidence the shortlist is starving admission
(capacity going unfilled because fewer than the available slots have a qualifying candidate on a
given day) — a real, different failure mode from the redesign study's, and would mean the arm isn't
purely testing gate width either.

## 3. Criterion

**Primary**: cost-adjusted CAGR and peak-relative max drawdown, both first-class — reported for all
four arms together, not winner-vs-baseline only. Excess return over `^SP500TR` (dividend-adjusted
S&P 500, coverage already confirmed twice this project) reported alongside as context.

**Required margin, justified against the measured noise floor (~1.08pp cost-adjusted CAGR range
from 17 trivial single-ticker/date perturbations, 16/17 within ±0.13pp)**: an arm is treated as
*decisively* better than baseline only if cost-adjusted CAGR improves by **≥1.5pp** (≈1.4x the
largest single noise-floor draw ever observed — a materially larger bar than trivial-perturbation
noise, appropriate since removing ~230–305 candidates from the pool is a much bigger structural
change than dropping one ticker) **AND** peak-relative drawdown does not worsen by more than **5pp**
— a real ceiling, not just a reported number, because **Part 7 already established that
concentration buys return at drawdown's expense**, and a tighter shortlist concentrates by
construction. An arm clearing the CAGR bar by giving back more than 5pp of drawdown is not a win.

**Read the curve, not the winner.** All four arms' cost-adjusted CAGR and drawdown reported
together, in shortlist-size order. **If the trend from 330→100→50→25 is monotonic, the optimum is
not bracketed by this study and that will be stated directly** — a monotonic result argues for
testing narrower still, not for crowning Top-25 the winner. **A non-monotonic curve (an interior
width beating both wider and narrower neighbours) is the genuine finding this study is actually
positioned to detect**, and is the only shape of result that gets evaluated against the ≥1.5pp/≤5pp
bar above as a real candidate for adoption.

**Block-pass count: corroborating only.** Measured resolution floor is 3 blocks (6–9 range,
17-draw perturbation study) — any delta ≤3 blocks is not read as evidence either way.

**Top-decile trade overlap, reported alongside every headline gap, not once.** Two measures: exact
`(ticker, entry_date)` overlap of each arm's top-decile-by-P&L trades (meaningful here specifically,
since all arms share the identical daily clock — see §4), and ticker-only Jaccard (for
cross-study comparability with the selection audit and admission-redesign reports). Motivated
directly by Part 1 (top decile = 257% of total P&L) and the `DHI` noise-floor case — two arms can
differ mostly by which few large trades they happen to hold, and this is how that gets checked
rather than assumed away.

## 4. The paired per-decision instrument

**Unlike the last two studies, exact `(ticker, entry_date)` pairing should be directly constructible
here — arms share the identical daily clock, so this is stated as a testable prediction, not
assumed.** A trade admitted under Top-N is a candidate that also passed the exact same gate under
baseline; the only question is whether baseline's own capacity walk reached it too. Per Part 4,
~92–93% of real admissions are timing-driven and rank concentrated near the top of that day's
eligible list — capacity rarely reaches deep into the ranked field. **Prediction: high overlap
between baseline and every shortlist arm**, with genuine divergence concentrated on the rarer days
where capacity extends past rank 25/50/100. **This prediction is itself a real test of the
framework, not a formality — treated as such.** A *low* overlap would mean the shortlist changes
admission *timing*, not just *composition*, which would directly complicate §2's "deployment
mechanics are unchanged" premise the whole design rests on: if truncating the list changes *when*
capacity gets filled (not merely *which* candidate fills it), the cash-holding guard in §2 stops
being a sufficient check on its own, and that would need to be reported as a limitation on this
study's own central claim, not filed separately as an unrelated finding.

**Minimum-sample floor, stated in advance**: the comparison of interest is each arm's *unique*
admissions (baseline-only vs. shortlist-only) — win rate and mean return, same method as the
selection audit's own rejected-population counterfactual. **Fewer than 100 differing (non-shared)
trades for a given baseline-vs-arm pair will be reported as a bare count and not interpreted**,
consistent with the floor discipline already established in both prior studies this week.

## 5. Pools

**Pool 1** (`get_live_stock_candidates(as_of=today)`) — ranking-direction check only, absolute
figures not decisive (survivorship-biased by construction). **Pool 2 CLEAN** (
`get_research_stock_candidates()` ∩ `status == 'CLEAN'`) — decisive pool. **Currently 612 tickers**
(confirmed on production earlier today, via this project's own data-quality fix) — **to be
reconfirmed fresh at run time, not assumed to still be 612**, per this project's own now-twice-
learned lesson that this table changes and a stale count is exactly what causes a sanity gate to
fail for a benign reason. The database used for this study must itself be a fresh copy carrying
that fix — confirmed before any cache is built, not assumed because a prior session's worktree once
had it.

## 6. Sanity gate

**Baseline-arm engine, confirmed explicitly (raised on review): all four arms, including baseline,
run through `redesign_engine.py:simulate_with_panel()` — never the raw production
`simulate_portfolio_compounding_mtm()`.** Baseline = `rebalance_frequency="daily",
shortlist_size=None`; the three shortlist arms differ only in `shortlist_size`. This keeps the
four-arm comparison internally consistent (any behaviour specific to the custom engine is a
constant shared by all four, not a confound between them) — but internal consistency among the four
arms is a different claim from the custom engine matching *production* correctly, which is what the
sanity gate itself must establish, not assume.

**Two verification steps, added on review, both required before any real arm is trusted — closing
the gap between "verified degenerate with no truncation" and "behaves identically to production
with truncation active":**

1. **Pool 2 CLEAN degenerate-case check, not yet done.** The existing degenerate-case verification
   (daily, `shortlist_size=None`, byte-identical to the real engine, 0 row-level mismatches) was run
   on Pool 1 only, today, for the admission-redesign study. It has never been independently
   re-confirmed on Pool 2 CLEAN. Required here: run `redesign_engine.py` at
   `rebalance_frequency="daily", shortlist_size=None` against the Pool 2 CLEAN cache and confirm it
   reproduces the raw, unmodified `simulate_portfolio_compounding_mtm()`'s own output on that same
   cache — byte-identical, row-for-row — not inferred from the Pool 1 result by code-reading alone.
2. **Non-binding-shortlist check, new, specifically targeting the concern raised**: run
   `shortlist_size` set larger than any single day's gate-passing candidate count (proposed: 1000,
   comfortably above the ~330-candidate gate) and confirm it reproduces `shortlist_size=None`'s own
   output byte-identical. This isolates whether the truncation code path itself — independent of
   whether it ever actually binds — introduces any side effect beyond the truncation (e.g. via an
   unintended interaction with the wait-for-rebalance-cash or no-forced-slot-filling logic that
   engine also carries, written for the monthly-cadence study). A non-binding shortlist diverging
   from `shortlist_size=None` at daily cadence would mean the engine's admission mechanics are not
   currently the simple, isolated slice this study's own methodology depends on, and would block
   trusting any of arms 100/50/25 until understood.

**A fresh, current reference for Pool 2 CLEAN baseline already exists from today's admission-
redesign study** (`research/admission_redesign_prereg_2026-08-08/trades_pool2_clean_baseline.csv`,
4,916 trades) — computed against the same corrected database this study also needs, using the
identical unmodified engine at daily cadence. **Reused as the target for check 1 above, not
re-derived from scratch**: compared row for row (ticker, entry/exit date, exit reason, P&L), not
just matched on total trade count and P&L, per this project's own standing lesson from earlier today
(an exact-total match is not sufficient; `VTRS` alone changed a total while leaving the count
unchanged). If Pool 2 CLEAN's count has shifted from 612 by the time this runs (§5), the reference
is re-derived fresh instead of forced to match — noted explicitly in the eventual report either way,
not silently reconciled.

## The walk-away sentence

**I will not adopt any shortlist width if no arm clears +1.5pp cost-adjusted CAGR over baseline
without worsening peak-relative drawdown by more than 5pp, or if the trend across 330→100→50→25 is
monotonic (no width is bracketed as an optimum), or if the paired per-decision instrument shows a
shortlist arm's own unique admissions are not distinguishable from baseline's on a usable sample.**
A narrower gate that merely concentrates into fewer, larger bets — winning on CAGR while giving back
materially more drawdown, or winning only because a handful of top-decile trades happened to fall
inside a narrower list — has not demonstrated that the gate was actually weak, only that
concentration works exactly as expensively as Part 7 already showed it does.

## Constraints re-confirmed

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or the live path.
Research only — reuses `redesign_engine.py` unmodified. Nothing adopted, nothing merged, nothing
deployed.
