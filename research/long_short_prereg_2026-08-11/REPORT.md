# Long/short momentum — results (2026-08-11)

**Verdict: NOT ADOPTED. Both arms fail the primary criterion decisively, in both pools, in the same
direction, by a margin no measurement uncertainty in this project can absorb.** Against a bar of
**+1.5pp**, Arm B (long-biased) comes in **−7.17pp** (Pool 2 CLEAN) / **−9.17pp** (Pool 1) and Arm C
(market-neutral) **−9.96pp** / **−11.66pp** below baseline on cost-adjusted excess return over
`^SP500TR`. The walk-away sentence's first clause fires outright, and two more fire behind it.
Nothing here is adoptable regardless — T212 Invest does not support shorting — and nothing was
adopted, merged, or deployed.

**Dual provenance is unusually strong here.** The two independently-provenanced pools agree in
direction and in magnitude, and the one dimension on which they differ — short-sleeve severity — 
differs in **exactly the direction prior 5 predicted, for the structural reason prior 5 gave**
(see §9a). That is the best outcome dual-provenance validation can produce: agreement where
agreement is meaningful, and a disagreement that is explained in advance rather than reconciled
after the fact.

**The single most useful result is not the failure itself but why it happens: the short sleeve loses
£33,093 GROSS, before a penny of borrow cost.** The modelled borrow assumption — the design's
weakest input, and the one prior 4 warned would be optimistic exactly where it matters — turns out
**not to be load-bearing for the verdict**. This is a finding about the short momentum signal, not
about a cost assumption, which makes it far more durable than a cost-sensitive result would have
been.

Research only. No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any
live path. Built and run from a dedicated worktree (`c:\GitHub\investment-app-longshort`, branch
`research/long-short-2026-08-11`).

---

## 1. Verification — every check passed before any arm result was trusted

| Check | Result |
|---|---|
| §11.2 **Reflection test** | **PASS** — 200,360 cases (200,000 randomized + 324 boundary + 36 None-contract), **0 mismatches**; all five exit outcomes exercised 11,189–66,657 times, far above the 100-case coverage floor |
| §11 **Cache parity** | **PASS** — 2,629,599 cells across 80 sampled tickers byte-identical to production `precompute_ticker()`; `high` present, non-degenerate, correctly bounding OHLC |
| §11.1 **Degenerate parity** | **PASS, both admission modes** — `ls_engine` with `enable_shorts=False` reproduces raw production `simulate_portfolio_compounding_mtm()` **byte-identical, 0 row-level mismatches** on the canonical sort: `relative_rank` 4,876 trades / £166,830.11 and `absolute_momentum` 4,919 / £103,926.50 |
| §12 **Sanity gate** | **PASS to the penny, both modes** — `absolute_momentum` 4,919 trades / £103,926.50 and `relative_rank` 4,876 / £166,830.11, exactly matching the relative-ranking study's published references |
| §11.3 **Short exit wiring** | **PASS, both pools** — 40/40 exit reasons independently recomputed; every short `STOP_LOSS` confirmed fired off the day's `high` and filled at `max(stop, open)`; **0 short trades exited via `CRISIS_EXIT`** (the §3 design, asserted directly rather than assumed) |
| §11.4 **Sizing** | **PASS, both pools** — 40/40 against the engine's **own emitted `equity_at_entry`**, never a reconstructed proxy; 40/40 entry stops correct, **0 short stops at or below entry** |
| §11.5 **Borrow arithmetic** | **PASS, both pools** — 20/20 recomputed by hand from daily MTM notional and daily tier |
| §11.7 **Signal separation** | **PASS** — `relative_rank` reaches no gate or exit evaluator on either sleeve |

**The `high` gate (§4) was real, not theoretical.** `precompute_ticker()` genuinely returns
`{close, atr, signal, momentum, relative_rank, open, low}` — `high` is consumed for ATR and
discarded. The research cache had to add it, and the reimplementation was then verified cell-by-cell
against production rather than trusted.

---

## 2. Pre-run calibration (§14) — every threshold survived unchanged

Run before any arm, on independent variables only. **Nothing was re-tuned**, which is the outcome
pre-registration is meant to produce and frequently does not.

**§14.2 Rally breaker, PRIMARY (dd ≤ −20% at t−1, r5 ≥ +10%, 10-day cooldown):** 11 fire days,
**4 episodes** — 2008-10-31, 2008-11-26, **2009-03-13**, 2020-03-27 — blocking short entries on 1.28%
of days. Inside the expected 3–12 range, and **it fires in the 2009 momentum-crash window, four
trading days after the 2009-03-09 bottom**, which is the case it was explicitly designed for.

Sensitivity grid, all five configurations enumerated in advance:

| Config | dd gate | r5 | cooldown | episodes | fires in 2009 window? |
|---|---|---|---|---|---|
| **PRIMARY** | −0.20 | +0.10 | 10 | 4 | **yes** (03-13, 03-16, 03-18) |
| loose | −0.15 | +0.08 | 10 | 5 | yes (6 days) |
| tight | −0.25 | +0.12 | 10 | 2 | **NO** |
| cd-short | −0.20 | +0.10 | 5 | 4 | yes |
| cd-long | −0.20 | +0.10 | 20 | 4 | yes |

**The `tight` cell never fires in 2009 at all** — by §6.4's pre-committed reading that is a design
failure for that configuration, recorded before any result existed rather than discovered afterwards.

**§14.1 HTB frequency:** 7.21% of short-eligible candidate-days (Pool 2 CLEAN) / 6.58% (Pool 1) —
inside the expected 2–8% band, so neither the >15% recalibration trigger nor the <0.5% "not
meaningfully tested" trigger fired. Leg hits are dominated by volatility (39,047) over drawdown
(9,796) and price (7,260).

**§14.3 Short supply:** mean 128.1 candidates/day (Pool 2 CLEAN), median 120, 5.11% zero-days — and
every zero-day is 2007 cold start (momentum needs 252 rows; the universe's history begins
2007-01-03). **Prior 7's worst case does not bite at the candidate level: Arm C is genuinely
testable, not a starved book.** Supply is strongly regime-dependent (2013: mean 38.9; 2009: 228.4;
2022: 237.7).

---

## 3. Results — Pool 2 CLEAN (decisive pool)

Continuous-walk convention. Cost-adjusted CAGR = raw CAGR (already net of borrow, which the engine
deducts from cash daily) − FX drag + idle-cash credit. Modelled borrow = `s1x`, 2× stress = `s2x`.

| | **A** (baseline) | **B** modelled | **B** stress | **C** modelled | **C** stress |
|---|---|---|---|---|---|
| Trades | 4,876 (4,876L/0S) | 7,686 (4,862L/2,824S) | 7,692 | 7,980 (3,524L/4,456S) | 7,924 |
| Final equity | £216,830 | £70,720 | £56,547 | £37,586 | £33,890 |
| Raw CAGR (net of borrow) | 7.776% | 1.785% | 0.630% | −1.446% | −1.965% |
| FX drag | −3.331 | −4.702 | −4.645 | −4.674 | −4.606 |
| Idle credit | +0.553 | +0.750 | +0.750 | +1.160 | +1.161 |
| **Cost-adj CAGR** | **4.998%** | **−2.167%** | **−3.265%** | **−4.960%** | **−5.410%** |
| Benchmark `^SP500TR` | 11.163% | 11.163% | 11.163% | 11.163% | 11.163% |
| **Excess vs benchmark** | **−6.165pp** | **−13.330pp** | **−14.428pp** | **−16.123pp** | **−16.573pp** |
| **Δ vs baseline (PRIMARY)** | — | **−7.165pp** | **−8.263pp** | **−9.958pp** | **−10.408pp** |
| Peak-rel. max drawdown | −47.51% | −58.46% | −60.34% | −58.66% | −59.94% |
| Δ drawdown vs baseline | — | 10.95pp worse | 12.83pp worse | 11.15pp worse | 12.43pp worse |
| Monthly corr to `^SP500TR` | 0.724 | 0.340 | 0.295 | **−0.127** | −0.109 |
| Daily corr | 0.669 | 0.256 | 0.252 | **−0.153** | −0.149 |
| Mean long / short exposure | 63.7 / 0.0 | 63.8 / 26.9 | 63.8 / 26.9 | 45.5 / 44.8 | 45.5 / 44.7 |
| Mean **gross** / **net** | 63.7 / 63.7 | **90.6 / 36.9** | 90.6 / 36.9 | **90.3 / 0.8** | 90.2 / 0.8 |
| Mean positions (L/S) | 14.3 / 0.0 | 14.2 / 7.8 | 14.2 / 7.8 | 10.3 / 12.2 | 10.3 / 12.2 |
| Blocks beating benchmark | 5/16 | 4/16 | 4/16 | 3/16 | 3/16 |

**The required margin was +1.5pp. Arm B delivers −7.17pp and Arm C −9.96pp.** These are not
near-misses inside a noise floor — they are five to nine times the measured ~1.08pp floor, in the
wrong direction, at both borrow rates, monotonically worse under stress.

### 3a. Pool 1 (direction only) — same answer, slightly worse

| | **A** | **B** modelled | **B** stress | **C** modelled | **C** stress |
|---|---|---|---|---|---|
| Trades | 4,757 (4,757L/0S) | 7,538 (4,766L/2,772S) | 7,546 | 7,644 (3,377L/4,267S) | 7,693 |
| **Cost-adj CAGR** | **7.134%** | **−2.039%** | **−2.398%** | **−4.524%** | **−6.287%** |
| Excess vs benchmark | −4.029pp | −13.202pp | −13.561pp | −15.687pp | −17.450pp |
| **Δ vs baseline** | — | **−9.173pp** | **−9.532pp** | **−11.658pp** | **−13.421pp** |
| Max drawdown | −37.89% | −55.57% | −56.30% | −47.72% | −58.85% |
| Monthly corr | 0.732 | 0.302 | 0.307 | **−0.131** | −0.137 |
| Mean gross / net | 63.4 / 63.4 | 90.5 / 36.7 | 90.4 / 36.6 | 89.8 / 0.8 | 89.8 / 0.7 |
| Blocks beating benchmark | 5/16 | 3/16 | 4/16 | 3/16 | 2/16 |

**Every qualitative conclusion replicates**: both arms fail by a wide margin, C fails worse than B, C
achieves genuine neutrality (net 0.8%, corr −0.131, gross 89.8%) and is worth nothing, and stress
borrow makes everything worse. The failure margins are *larger* on Pool 1, for the reason set out in
§9a.

---

## 4. The 2009 momentum-crash window — reported separately and prominently, as pre-committed

The standard 16-block grid puts block 2 at 2008-03-25 → 2009-06-15, which **bisects the episode**
(block 2 holds the March bottom, block 3 the rebound tail) — confirmed by direct computation, which
is exactly why §9.5 required a dedicated window.

**Window: 2009-03-09 → 2009-09-30.**

| | A | B modelled | C modelled |
|---|---|---|---|
| Arm return | +7.04% | **−27.18%** | **−32.93%** |
| `^SP500TR` return | +58.25% | +58.25% | +58.25% |
| Excess | −51.20pp | **−85.43pp** | **−91.17pp** |
| Max drawdown in window | −4.20% | −27.66% | −33.42% |
| Short-sleeve P&L | £0 | −£8,030 | −£12,514 |
| `RALLY_EXIT` count | 0 | 19 | 13 |

**The rally breaker fired, correctly and on time, and the short book was still destroyed.** Baseline
went up 7% through the rebound; the long/short arms lost 27–33%. This is the canonical momentum
crash behaving exactly as prior 2 said it would, and the breaker converted a catastrophe into a
smaller catastrophe rather than preventing one. **Per the pre-commitment: the arms' disadvantage does
not merely persist in the 2009 window, it roughly doubles there.**

### 4a. The more important discovery: the breaker is structurally blind to the worst episode

Per-block short-sleeve P&L identifies the worst block as **not 2009 but block 12
(2020-06-22 → 2021-09-13): −£10,012, 26.8% of Arm B's entire short-sleeve loss** — and the rally
breaker fired **zero** times in it.

Measured directly: across those 310 trading days, `^GSPC`'s drawdown from its trailing 252-day peak
reached a minimum of only **−11.1%** (mean −1.9%), and **0 of 310 days** were at or below the −20%
gate. The breaker could not fire, by construction.

**This is the drawdown gate's own design working against it.** That gate is what stops a naive "exit
shorts on any strong up day" from firing constantly in bull markets — the failure mode §6.1 was
written to avoid — and it is precisely what makes the breaker incapable of seeing a momentum crash
that occurs *while the index is at all-time highs*. The 2020–21 rally in beaten-down and heavily
shorted names is exactly that: the worst-performing names ripping upward with no index drawdown
anywhere in sight.

This is an instance of the standing class (a check reporting clean for a reason unrelated to what it
was checking), and I predicted the class in §6.4 but named the wrong degenerate case. I pre-committed
to reading "0 fires" as "untested, not safe." The actual failure is subtler and worse: the breaker
**fires correctly in the episode it was designed for** and is **structurally blind to a different
momentum crash of the same kind**. A breaker validated only against 2009 would have looked fine.

---

## 5. Why the short sleeve fails — signal, not cost

| Arm | Short gross P&L | Borrow cost | Short net P&L | Short win rate | n |
|---|---|---|---|---|---|
| B modelled | **−£33,093** | £4,294 | −£37,387 | 36.3% | 2,824 |
| B stress | −£33,840 | £7,528 | −£41,368 | 35.4% | 2,847 |
| C modelled | **−£43,583** | £5,119 | −£48,702 | 35.8% | 4,456 |
| C stress | −£37,546 | £9,065 | −£46,611 | 36.3% | 4,413 |

**Borrow is 13% of Arm B's short-sleeve loss. Set it to zero and the sleeve still loses £33,093.**
The verdict does not rest on the modelled assumption at all — which is the strongest thing that can
be said for a result whose weakest input was, by design, a number nobody can measure.

Short win rate is **36.3%** against the long sleeve's ~44%. Exit mix (Arm B): `STOP_LOSS` 1,434
(50.8%), `MAX_HOLDING` 1,184, `SIGNAL_OFF` 133, `RALLY_EXIT` 58, `END_OF_DATA` 15 — over half of all
shorts are stopped out, the classic squeeze signature.

**Prior 4 confirmed empirically in both pools, not merely asserted:**

| | Shorts actually taken that were HTB | Candidate-day HTB base rate | Concentration |
|---|---|---|---|
| Pool 2 CLEAN | 26.59% (751/2,824) | 7.21% | **3.7×** |
| Pool 1 | 22.94% (636/2,772) | 6.58% | **3.5×** |

The ranking genuinely does select disproportionately into the expensive, hard-to-borrow tail, at
almost identical concentration in two independently-built pools. The cost model is optimistic exactly
where it matters most, as predicted — it simply does not matter here, because the sleeve fails before
cost is applied at all.

**§9.6 unrealizable-risk share: 5.4–6.5%** of short-sleeve gross profit (57 of 1,025 winners for Arm
B), far below the 30% pre-committed threshold. The clause does not fire — and this *strengthens* the
negative result: the short sleeve's profits are not inflated by unrealizable delisting/sub-$1
outcomes, and it loses badly anyway.

---

## 6. Arm C — market neutrality achieved mechanically, and worthless

**The construction did exactly what it was designed to do.** Mean net exposure **0.8%**, mean gross
exposure **90.3%**, monthly correlation to `^SP500TR` **−0.127** (daily −0.153). That is genuine
market neutrality: near-zero net, near-zero correlation, at full gross exposure.

**And the §9.2 degenerate-case guard confirms it is not a cash book.** I pre-committed that
correlation below 0.30 would be read as "not meaningfully satisfied" if mean gross exposure fell
below 0.50. Gross is 90.3% — comfortably above. **Arm C is a real neutral book, not cash wearing a
neutral label.** The guard was built to catch a false positive and instead certified that the
construction was genuine, which is the more useful outcome.

Against the four pre-registered QUALIFIED POSITIVE conditions:

| Condition | Required | Actual | |
|---|---|---|---|
| (i) cost-adjusted CAGR | ≥ +3.0%/yr | **−4.960%** | **FAIL** |
| (ii) \|monthly correlation\| | ≤ 0.30 | **0.127** | **PASS** |
| (iii) peak-rel. max drawdown | ≤ 25% | **−58.66%** | **FAIL** |
| (iv) 2009 window doesn't reverse sign | — | −32.93% vs benchmark +58.25% | **FAIL** |

**One of four.** Arm C delivers the decorrelation it promised and pairs it with a negative return and
an equity-like drawdown. A market-neutral book that loses money with a 59% drawdown is not a
diversifier; it is a worse asset with a prettier correlation coefficient. **This is the cleanest
possible answer to the question I pre-committed to weighing in advance** — the low-correlation result
arrived exactly as hoped, and is worth nothing, because condition (i) was never a formality.

---

## 7. The paired per-decision instrument (§10, long sleeve only)

Not constructible for the short sleeve — baseline has no shorts, so no counterpart exists, and no
sample size creates one. Stated in advance; unchanged by any result.

| | Overlap (% of arm) | Jaccard | A-only win / mean P&L% | Arm-only win / mean P&L% |
|---|---|---|---|---|
| B modelled | 38.1% | 0.235 | 45.22% / 1.036% | **44.70% / 0.990%** |
| C modelled | 28.9% | 0.138 | 44.29% / 0.880% | 44.65% / 1.077% |

Both comfortably clear the 100-differing-trade floor.

**Arm B's own unique long admissions are worse than baseline's on both measures** — lower win rate
and lower mean P&L%. The walk-away sentence's sixth clause fires. Arm C's are marginally better on
both, so that clause does not fire for C, but C fails on every other ground.

**Overlap prediction, checked honestly:** §10 predicted 10–25% for Arm B. On the Jaccard measure
(0.235 — the measure comparable to the relative-ranking study's own 21.8–23.1%) the prediction
holds. On the share-of-arm measure (38.1%) it is exceeded. The two denominators were not
distinguished clearly enough in the pre-registration; recording the ambiguity rather than picking
whichever reading makes the prediction look right.

---

## 8. The walk-away sentence, clause by clause

> *"I will not treat this study as showing that a short sleeve helps if: no arm clears +1.5pp …; or
> if a clearing arm's advantage disappears at 2× stress borrow; or … in the 2009 momentum-crash
> window; or if a clearing arm fails at ≥2 of the four non-PRIMARY rally-breaker configurations; or if
> more than 30% of short-sleeve gross profit comes from the unrealizable-risk bucket; or if Arm B's
> long-sleeve paired instrument shows its unique admissions are not distinguishable from baseline's;
> or if Arm C posts near-zero correlation at mean gross exposure below 0.50."*

1. **No arm clears +1.5pp** — Pool 2 CLEAN: B −7.17pp, C −9.96pp; Pool 1: B −9.17pp, C −11.66pp.
   **FIRES, in both pools.**
2. **Stress borrow** — every arm deteriorates further in both pools. Moot, since no arm cleared, but
   directionally confirmed. **N/A.**
3. **2009 window** — the disadvantage roughly doubles there (Pool 2 CLEAN B: −85.43pp excess in-window
   vs −13.33pp overall; Pool 1 B: −83.66pp vs −13.20pp). **FIRES, in both pools.**
4. **Rally-breaker configurations** — not reached (no arm cleared, so there is no clearing result to
   test for threshold-fragility). The `tight` cell's failure to fire in 2009 is recorded regardless.
   **N/A.**
5. **Unrealizable-risk >30%** — 5.4–6.5% (Pool 2 CLEAN), 9.3–10.8% (Pool 1). **Does not fire** (and
   strengthens the negative — the short sleeve's profits are not inflated by unrealizable outcomes,
   and it loses anyway).
6. **Arm B's unique long admissions not distinguishable** — Pool 2 CLEAN: worse on **both** win rate
   and mean P&L% (44.70%/0.990% vs baseline's 45.22%/1.036%). **FIRES on the decisive pool.** Pool 1
   is mixed (worse win rate 44.14% vs 45.57%, marginally better mean P&L% 1.021% vs 1.000%), so this
   clause fires cleanly only on Pool 2 CLEAN — recorded as the partial split it is rather than as a
   uniform result.
7. **Arm C near-zero correlation at gross <0.50** — gross is 90.3% / 89.8%. **Does not fire**; C's
   neutrality is genuine in both pools.

**Three clauses fire, the first of them decisively, in both pools, and on its own sufficient.** The
sentence is a veto, and it vetoes.

---

## 9. Priors — which held, which were confirmed, which were wrong

| # | Prior | Outcome |
|---|---|---|
| 1 | Short-side momentum has a weaker published record | **Confirmed, strongly.** 36.3% win rate, −£33k gross |
| 2 | Momentum crashes are severe and concentrated | **Confirmed.** 2009 window −27% to −33%; block 12 alone is 26.8% of the loss |
| 3 | Borrow costs are modelled, not measured | **True, and it did not matter** — the failure precedes cost |
| 4 | Best short candidates are hardest to borrow | **Confirmed and quantified: 26.59% vs 7.21% base rate, 3.7×** |
| 5 | Pool 1 cannot contain terminal-decline short winners | **Confirmed, in the predicted direction and mechanism** — see §9a |
| 6 | Arm B's long sleeve will not reproduce baseline | **Confirmed** — Jaccard 0.235, and long P&L collapses £166,830 → £58,107 via the equity path |
| 7 | Arm C underdeployed in bull markets | **Wrong at the candidate level** — supply is ample (mean 128/day) and C ran 90.3% gross. C fails on signal, not starvation |
| 8 | Multiple comparisons | Not engaged — this is a negative result, which multiple-comparison concerns do not manufacture |

### 9a. The cross-pool difference — predicted in advance, confirmed in direction and mechanism

Prior 5 stated, before any run, that Pool 1 is today's S&P 500 membership projected backwards — every
ticker survived to today **by construction** — so it structurally cannot contain a bankruptcy, a
delisting-to-zero, or a terminal decline. On the long side that is a second-order effect; **on the
short side it is first-order, because those are precisely the short sleeve's best possible outcomes.**
The prediction was therefore that Pool 1's short sleeve should perform *worse* than Pool 2 CLEAN's.

Measured, Arm B modelled:

| | Short gross P&L | Borrow | Short net P&L | Short win rate |
|---|---|---|---|---|
| Pool 2 CLEAN | −£33,093 | £4,294 | −£37,387 | 36.3% |
| **Pool 1** | **−£59,753** | £4,119 | **−£63,872** | **33.6%** |

**Pool 1's short sleeve loses 1.8× as much, at a 2.7pp lower win rate, on near-identical borrow
cost.** That is the predicted direction, at a material magnitude, and it propagates cleanly into the
headline: Pool 1's failure margins are ~2pp wider than Pool 2 CLEAN's for both arms.

**This is reported as a structural property of the two pools, not as a defect to reconcile**, per the
standing dual-provenance requirement. It is also the reason Pool 2 CLEAN is correctly designated the
decisive pool here: it is the only one of the two that *can* contain the population the short side
depends on, so it gives the short sleeve its **best available chance** — and the short sleeve fails
there anyway. **The decisive pool is the one most favourable to the hypothesis, and the hypothesis
still loses.**

One incidental observation that runs the other way and is recorded rather than suppressed: Pool 1's
unrealizable-risk share is *higher* (9.3–10.8%) than Pool 2 CLEAN's (5.4–6.5%), which is initially
counterintuitive for a survivors-only pool. The flag covers sub-$1 exits, `END_OF_DATA` exits, and
price-cliff tickers — and in a survivors-only universe the binding component is `END_OF_DATA`
(positions still open at the walk's end), not delisting. Both remain far below the 30% threshold, so
neither changes any verdict.

**Prior 6's mechanism is worth stating precisely, because the magnitude exceeded the prediction.**
Arm B's long sleeve is not merely a *different* set of trades from baseline — it is a much *worse*
one: £166,830 → £58,107. The long rules are byte-identical; what changed is the equity path. Short
losses shrink account equity, `per_trade_pct` sizes every subsequent long position off that smaller
equity, and the damage compounds across 19 years. **The short sleeve does not merely lose its own
money; it degrades the long book that would otherwise have been profitable.**

---

## 10. What this closes, and what it does not

**Closes:** a symmetric short sleeve on this system's own momentum signal, ranked by the same
relative-rank mechanism that was adopted for the long side, does not work — at either allocation
(long-biased or market-neutral), at either borrow rate, **in both independently-provenanced pools**,
including the pool structurally most favourable to it. It fails on signal
quality, before costs, and it damages the long book through the shared equity path. Given how far
outside any measured noise floor the result sits (5–9× the ~1.08pp floor), this does not need a
robustness test to be trusted as a negative; §12's mandatory 20-draw perturbation test was scoped for
a *clearing* arm and is not triggered.

**Does not close:**
1. **Whether a different short construction could work.** This tested the *symmetric mirror* of the
   long gate, deliberately, so that allocation was the only variable between B and C. A short signal
   built on different information (crowding, borrow-fee levels themselves, quality/accruals,
   short-interest) is untested and unaddressed by this result.
2. **Whether a better rally breaker exists.** §4a shows the drawdown-conditional design is
   structurally blind to a momentum crash at index highs. A breaker keyed on cross-sectional
   dispersion, or on the short book's own drawdown rather than the index's, would not have that blind
   spot. Untested.
3. **Venue.** Out of scope by instruction and unaffected: T212 Invest does not support shorting.
4. **Whether shorting helps a differently-sized book.** Gross exposure reached ~90% in both arms;
   a smaller short sleeve would lose proportionally less, but nothing here suggests it would gain.

---

## 11. Files

`PREREGISTRATION.md`, `ls_primitives.py`, `ls_engine.py`,
`step0_fetch_reference_series.py`, `step1_build_caches.py`, `step2_verify_cache_parity.py`,
`step3_calibration.py`, `step4_reflection_test.py`, `step5a_run_production_reference.py`,
`step5b_compare_parity.py`, `step6_run_arms.py`, `step7_instruments.py`, `step8_verify_engine.py`,
`calibration_results.json`, `instruments_{pool}.json`, `arms_summary_{pool}_{tag}.json`,
`trades_{pool}_{tag}.csv`, `panel_{pool}_{tag}.csv`, `_ref_production_{mode}.csv`, `_log_*.txt`.

Full trade-level output is preserved for every arm, per this project's standing requirement.

**Methodology notes — backtest convention, stated explicitly per this project's standing
requirement.** **Continuous walk**, for all three arms, over the full 2007-01-03 → 2026-08-07 window.
The reset-at-boundary convention is this project's default for cross-variant comparisons, and it is
**not applicable here**: reset-at-boundary exists to stop one variant's *train-period* luck from
mechanically sizing its *test-period* trades, and this study has no train/test split at all — every
arm starts from the identical £50,000 on the identical first day and is compared over one common
window. There is no boundary to reset at. This matches the convention every study in this sequence
(relative-ranking, position-count, deployment-cap, gate-width, smoothness) already used for the same
reason.

Database state: local research copy, `price_data` through **2026-08-07**, 612 CLEAN
tickers, 608 usable Pool 2 CLEAN / 500 usable Pool 1 — reconfirmed by direct query at run time.
`^SP500TR` and `^IRX` were fetched read-only into a research-local cache (5,433 and 5,428 rows to
2026-08-07) because the DB copy predates `^SP500TR`'s addition to `reference_tickers`; no database
write occurred at any point in this study.
