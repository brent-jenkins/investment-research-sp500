# Gate-width (momentum shortlist) study — results (2026-08-08/09)

**Verdict: NOT ADOPTED.** No shortlist width clears the pre-registered +1.5pp cost-adjusted CAGR bar
on the decisive pool. An interior-optimum *shape* does appear (rising to Top-50, falling at Top-25)
— genuinely the kind of finding this study was built to detect — but its magnitude is small, sits
inside or at the edge of the measured ~1.08pp noise floor, and the one arm that looked most
interesting in isolation (Top-100) **disagrees sharply between the two pools**, which on its own is
enough to withhold any adoption-track conclusion. Full pre-registration: `PREREGISTRATION.md` in
this directory. No code changes to `signals.py`/`decision.py`/`config.yaml`/the live path. Nothing
adopted, nothing merged, nothing deployed.

## Verification, sanity gate — both closed cleanly, with one genuine prediction failure elsewhere (§4)

**Both engine-correctness checks added on review passed byte-identical**: `redesign_engine.py` at
(daily, `shortlist_size=None`) reproduces the raw, unmodified `simulate_portfolio_compounding_mtm()`
exactly on Pool 2 CLEAN (4,916 trades, £102,449.21, 0 row-level mismatches — closing the gap that
the original degenerate check was Pool 1-only); and `shortlist_size=1000` (non-binding) reproduces
`shortlist_size=None` exactly (0 mismatches) — the truncation code path introduces no side effect
beyond the truncation itself, directly answering the concern that the engine's other monthly-study
behaviours (wait-for-rebalance cash, no forced slot-filling) might leak into daily-cadence use.

**Sanity gate found two small, fully-diagnosed discrepancies against today's earlier reference — not
treated as bugs because they were actually diagnosed, not assumed benign.** Both Pool 1 and Pool 2
CLEAN baseline reruns differ from this morning's admission-redesign reference by ~0.3%, and in both
cases the *same* signature: exactly 7 trades exiting `END_OF_DATA` on 2026-08-06 (the last date in
the data) with revised close prices — a boundary-date price revision between snapshots, not a
candidate-set change. Pool 2 CLEAN additionally shows `VTRS` present in this morning's reference but
absent here, with total trade count unchanged (4,916 both times) — one more day of price data
changed `VTRS`'s own admission outcome, absorbed elsewhere via the same capacity-cascade mechanism
already documented extensively in this project. Both diagnoses done the same way as the original
`VTRS` incident: row-level diff, not a total-only comparison — and both are themselves a live,
same-day repeat of the "derived state decays" generalisation just added to CLAUDE.md.

## Results — all four arms, both pools, together

| | Pool 1 (direction only) | | | | Pool 2 CLEAN (decisive) | | | |
|---|---|---|---|---|---|---|---|---|
| | Baseline | Top-100 | Top-50 | Top-25 | Baseline | Top-100 | Top-50 | Top-25 |
| Trades | 4,749 | 4,770 | 4,725 | 4,687 | 4,916 | 4,852 | 4,808 | 4,826 |
| Cost-adj. CAGR | 7.132% | 5.031% | 7.271% | 6.747% | 3.395% | 3.908% | 4.043% | 3.418% |
| Δ vs. baseline | — | **−2.10pp** | +0.14pp | −0.39pp | — | +0.51pp | **+0.65pp** | +0.02pp |
| Peak-rel. DD | −41.94% | −41.94% | −41.10% | −41.34% | −46.28% | −46.28% | −46.07% | −47.79% |
| Mean deployed % | 63.46 | 63.38 | 63.32 | 63.20 | 63.72 | 63.56 | 63.46 | 63.41 |
| Excess vs. `^SP500TR` | −1.22 | −3.32 | −1.07 | −1.62 | −4.95 | −4.46 | −4.34 | −4.95 |
| Block-pass (Pool 2 CLEAN only) | — | — | — | — | 7/16 | 8/16 | 9/16 | 9/16 |

**§2's deployment guard passes cleanly, decisively, in both pools** — mean deployment moves by at
most 0.5pp across all four arms in either pool, nowhere near the pre-registered 5pp flag. Whatever
else this study's other numbers show, gate width is confirmed *not* changing this system's
deployment behaviour — the one thing it was explicitly designed to be immune to.

## The curve

**Pool 2 CLEAN (decisive) cost-adjusted CAGR, in shortlist-size order**: 3.395% (330) → 3.908% (100)
→ 4.043% (50) → 3.418% (25). **Non-monotonic — rises to an interior peak at Top-50, then falls back
to essentially baseline at Top-25.** Per the pre-registration's own framing, this is the shape the
study was positioned to detect, not a monotonic trend arguing for testing narrower still. **But its
magnitude does not clear the bar**: the largest gap (Top-50, +0.65pp) is well inside the measured
noise floor (~1.08pp from trivial single-perturbation draws) — smaller than the single largest
noise-floor deviation ever observed on this project, on a change far more structural than a trivial
perturbation. **No arm reaches the pre-registered +1.5pp requirement.** Drawdown never breaches the
5pp ceiling either direction, so the drawdown guard doesn't disqualify anything — there is simply
nothing large enough to qualify in the first place.

**Pool 1 tells a different, partly contradictory story.** Top-50 is close to flat (+0.14pp) and
Top-25 mildly negative (−0.39pp) — broadly consistent in *direction* and *magnitude* with Pool 2
CLEAN's own near-flat readings for those two widths. **Top-100 is the exception, and it's a large
one**: −2.10pp on Pool 1 (a real, noise-floor-clearing *underperformance*) against **+0.51pp on Pool
2 CLEAN** — opposite sign, both outside what either pool's own noise would explain on its own. This
is a genuine cross-pool disagreement, not a rounding difference, and per this project's dual-
provenance standing requirement it is reported as exactly that — not reconciled by picking whichever
pool tells the more convenient story. A plausible, unconfirmed hypothesis: Pool 1's own construction
(today's real membership projected backwards, maximal survivorship bias, no crisis-casualty
population) makes it a structurally different universe at rank 100, not just a smaller one — but
this is a hypothesis, not run down further here, consistent with how this project has previously
recorded similar cross-pool disagreements as an honest structural property rather than chased to a
false resolution.

## §4's prediction, checked and found wrong — reported as the genuine test it was designed to be

**The pre-registration predicted high `(ticker, entry_date)` overlap between baseline and every
shortlist arm, reasoning from Part 4's finding that ~92–93% of admissions are timing-driven.** The
actual overlap is **40.6–44.8%** (shared pairs as % of baseline), in both pools, across all three
shortlist widths — far below the prediction. **This is reported as the real, informative result the
pre-registration said it would be, not smoothed over**: a low overlap does mean the shortlist changes
*which specific candidate-days* end up admitted far more than Part 4's own aggregate figures
suggested on their own.

**It does not, on investigation, mean §2's "deployment mechanics are unchanged" premise is false** —
the two are separable, and were checked separately. Mean deployment (the actual guard metric) is
confirmed flat to within 0.5pp across every arm (§ above) — a direct, independent measurement, not
inferred from the overlap number. The low overlap is better explained by the same **capacity-cascade
amplification** this project has repeatedly documented (`CPWR`'s two-block flip from one ticker's 18
trades; the adj_close study's ~33% overlap from a signal change with a much smaller direct effect):
removing ranks 26+ (or 51+, 101+) from one day's shortlist changes that day's admission, which changes
available capacity for every subsequent day for the rest of the 19-year walk, compounding a narrow
rule change into a broad trade-level reshuffle. **The paired instrument's own "unique admissions"
comparison corroborates the cascade reading over a genuine quality-difference reading**: baseline-
only and shortlist-only subsets have close to identical win rates and mean returns in most pairings
(e.g. Pool 2 CLEAN baseline-only 41.9–42.7% win rate vs. shortlist-only 41.3–42.5% — never more than
~1.4pp apart), even where the *aggregate* portfolio gap between the same two arms is much larger —
consistent with this project's own standing rule that a large trade-level diff paired with a small,
inconsistent aggregate effect reads as "cascade-amplified, not decision-different."

**Minimum-sample floor**: comfortably cleared everywhere (2,622–2,922 differing trades per pairing,
against the pre-registered 100-trade floor) — every paired comparison above is interpreted, none
reported as a bare count.

## Top-decile overlap

Ticker-identity Jaccard: 0.775–0.828 across all six pairings (both pools, all three widths) —
substantial, real overlap in *which names* produce the biggest winners, even though exact
`(ticker, date)` overlap is much lower. Exact-pair Jaccard on the top-decile subset specifically:
0.217–0.249 — lower than the overall exact-pair rate, meaning the very best trades are, if anything,
slightly *less* likely to be exact-date-shared than the population at large. Consistent with, not
contradicting, the cascade explanation above: the big winners are still overwhelmingly the *same
companies*, just not always admitted on the *same specific day*.

## Walk-away sentence, checked explicitly

> *"I will not adopt any shortlist width if no arm clears +1.5pp cost-adjusted CAGR over baseline
> without worsening peak-relative drawdown by more than 5pp, or if the trend across 330→100→50→25 is
> monotonic (no width is bracketed as an optimum), or if the paired per-decision instrument shows a
> shortlist arm's own unique admissions are not distinguishable from baseline's on a usable sample."*

**The first clause fires outright**: no arm, in the decisive pool, clears +1.5pp (best case Top-50 at
+0.65pp). The second clause does not fire on its own (the curve is genuinely non-monotonic, an
interior optimum does appear) — but the walk-away sentence only needs one clause to fire, and the
magnitude bar was always the primary gate, the curve-shape clause a secondary one. The third clause
is close to firing rather than cleanly not: unique-admission win rates and mean returns are close
between baseline and every shortlist arm (§ above), which is a weaker, not-quite-identical version of
"not distinguishable" — read together with the magnitude finding, not as an independent tie-breaker.

**Not adopted.** The gate is confirmed weak in the sense the selection audit already established
(Part 5: capacity-rejected candidates perform close to as well as admitted ones) — but narrowing it
mechanically, at the three widths tested, does not turn that weakness into a usable, robust
improvement. The interior-optimum shape at Top-50 is worth remembering as a hint, not worth acting
on: it doesn't clear the pre-registered bar, and the one wider comparison point (Top-100) that did
look large on its own turned out to disagree in sign between the two pools it was checked against.

## What this doesn't close

A single-variable width sweep at three fixed points (100/50/25) cannot distinguish "this system's
optimum happens to be near 50" from "the true optimum is somewhere unsampled between 25 and 100, or
outside this range entirely" — the curve's own shape argues for finer sampling near 50 before
concluding anything about where a real optimum sits, not for concluding none exists. Given the
magnitude found here, that finer sweep would need to clear the same +1.5pp bar (or a tighter one, now
that this study has used up the "structural change, apply a generous margin" reasoning once) to be
worth running at all.

## Files

`PREREGISTRATION.md`, `step0_build_caches.py`, `step1_verify_engine.py`, `step3_run_arms.py`,
`step4_instruments.py`, `step4_all_results.json`, `trades_{pool1,pool2_clean}_{baseline,top100,top50,top25}.csv`,
`panel_{pool1,pool2_clean}_{baseline,top100,top50,top25}.csv`, `verify_pool2_clean_shortlistNone.csv`.
