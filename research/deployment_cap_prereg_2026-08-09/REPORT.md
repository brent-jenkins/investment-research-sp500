# Deployment-cap (max_total_deployed) study — results (2026-08-09)

**Verdict: NOT ADOPTED.** Two arms (0.80, 0.95) nominally clear the pre-registered +1.5pp cost-adjusted
excess-return bar on Pool 2 CLEAN — but the paired per-decision instrument shows **none** of the three
higher-cap arms' own unique admissions are distinguishably better than baseline's, the walk-away
sentence's own explicit disqualifying clause. Independently, the curve is erratic and does not
replicate in shape across pools, and the entire spread of non-baseline excess-return deltas
(+0.521pp to +1.585pp, a 1.06pp range) is close to the measured noise floor (1.08pp) — both cut
against trusting either nominal "win" as real. `config.yaml` is not modified, no code changed on any
live path, nothing adopted. Full pre-registration: `PREREGISTRATION.md` in this directory (§0's
pre-check, already run and reported separately, found the cap genuinely binding — confirmed again
here, see the deployment guard section below).

## Verification — sanity gate, clean

The 0.70 (baseline) arm is the identical call (`per_trade_pct=0.05`, `max_total_deployed=0.70`) already
byte-identical-verified in the position-count study — confirmed again here by diffing this study's own
c70 trade output against `research/position_count_prereg_2026-08-09/trades_{pool}_n22.csv` row-for-row
(ticker, entry_date): **0 differing trades, exact P&L match, both pools** (Pool 1: 4,752 trades,
£241,673.18; Pool 2 CLEAN: 4,919 trades, £103,926.50). Caches reused directly from the position-count
study, reconfirmed unchanged immediately before this run (612 CLEAN, price data through 2026-08-07,
identical to the DB state those caches were originally built from).

## The deployment guard — inverted per the pre-registration, confirms reading (a) across the whole range

**Every arm's realized deployment tracks its own nominal cap closely — the §0 pre-check's finding
(reading (a): the cap genuinely rejects viable candidates) holds not just at 0.70 but across the full
0.70→0.95 range tested.**

| | Pool 1 | | | Pool 2 CLEAN | | |
|---|---|---|---|---|---|---|
| Arm | Nominal cap | Mean deployed | Median deployed | Nominal cap | Mean deployed | Median deployed |
| c70 | 70% | 63.46% | 68.97% | 70% | 63.72% | 69.10% |
| c80 | 80% | 72.46% | 78.95% | 80% | 72.73% | 79.03% |
| c90 | 90% | 81.38% | 88.56% | 90% | 81.59% | 88.54% |
| c95 | 95% | 86.41% | 94.13% | 95% | 86.63% | 94.16% |

**Median deployment sits within ~1–1.5pp of the nominal cap at every level, both pools** — the same
gap size observed for baseline itself in the §0 pre-check (median 68.97–69.10% vs. a 70% cap), not a
growing or shrinking gap as the cap rises. **Materiality test applied** (closer to own nominal target,
or closer to baseline's own realized level — the same test the position-count study used): every arm's
mean deployment sits far closer to its own target than to baseline's (e.g. c95: 8.4–8.6pp from its own
95% target vs. 23pp from baseline's own ~64% mean) — **no arm fails this guard.** Unlike the
position-count study's n5/n10 arms, this lever does what it's named for at every level tested.

## Results — all four arms, both pools, together

| | Pool 1 (direction only) | | | | Pool 2 CLEAN (decisive) | | | |
|---|---|---|---|---|---|---|---|---|
| | c70 (baseline) | c80 | c90 | c95 | c70 (baseline) | c80 | c90 | c95 |
| Trades | 4,752 | 5,402 | 6,490 | 9,479 | 4,919 | 5,560 | 6,617 | 9,587 |
| Cost-adj. CAGR | 7.164% | 7.239% | 7.860% | 6.875% | 3.440% | 5.002% | 3.961% | 5.025% |
| Excess vs `^SP500TR` (cost-adj.) | −3.999 | −3.924 | −3.303 | −4.288 | −7.723 | −6.161 | −7.202 | −6.138 |
| Δ vs baseline | — | +0.075pp | +0.696pp | −0.289pp | — | **+1.562pp** | +0.521pp | **+1.585pp** |
| Peak-rel. DD | −41.94% | −44.49% | −46.65% | −45.68% | −46.28% | −48.35% | −52.67% | −48.79% |
| Δ DD vs baseline | — | −2.55pp | −4.71pp | −3.74pp | — | −2.07pp | −6.39pp | −2.51pp |
| Mean n open positions | 13.98 | 15.92 | 19.04 | 27.82 | 14.23 | 16.16 | 19.28 | 28.15 |
| FX drag %/yr | 3.343 | 3.831 | 4.384 | 4.633 | 3.343 | 3.834 | 4.310 | 4.609 |
| Idle credit %/yr | 0.557 | 0.420 | 0.284 | 0.207 | 0.553 | 0.416 | 0.281 | 0.204 |
| Block-pass (Pool 2 CLEAN only) | — | — | — | — | 7/16 | 5/16 | 5/16 | 5/16 |

## Primary criterion — two arms nominally clear the bar, but the margin is thin and the shape is erratic

**Pool 2 CLEAN: c80 (+1.562pp) and c95 (+1.585pp) both clear the pre-registered +1.5pp bar; c90
(+0.521pp), sandwiched between them, does not — a real, non-monotonic dip, not a smooth ramp.** This is
already a flag worth stating plainly before anything else: a lever that should behave roughly
monotonically (more deployment → more exposure → more return, up to some point of diminishing returns)
instead shows a dip at the middle value that very nearly returns to baseline's own excess return,
bracketed by two higher values on either side. **The entire spread across the three non-baseline arms
(+0.521pp to +1.585pp, a 1.06pp range) is close to the measured ~1.08pp noise floor** (17-draw
perturbation study) — the same order of magnitude as pure measurement noise, which argues against
reading either c80 or c95's nominal "clear" as a reliable, reproducible effect rather than noise that
happened to land on the favourable side of the 1.5pp bar this time.

**Pool 1 shows a different, partly contradictory shape**: c90 is the (mild, non-bar-clearing) peak
(+0.696pp), with c80 nearly flat (+0.075pp) and c95 actually *negative* (−0.289pp) — the interior value
that failed in Pool 2 CLEAN is the one value that looks best in Pool 1. **Per this project's
dual-provenance standing requirement, this is reported as a genuine, unreconciled cross-pool
disagreement in curve shape, not resolved by picking whichever pool tells the more favourable story** —
consistent with how the gate-width study's own Top-100 cross-pool sign flip was handled.

## Drawdown ceiling — never binds, but confirms the exposure mechanism, not the concentration one

**No arm breaches the 15pp one-sided ceiling in either pool.** Drawdown does worsen monotonically with
deployment in Pool 2 CLEAN (c80 −2.07pp, c90 −6.39pp, c95 −2.51pp — non-monotonic in exact size but
consistently worse than baseline throughout) and in Pool 1 similarly (−2.55pp, −4.71pp, −3.74pp) — the
expected-in-advance exposure effect (less cash cushion during a market-wide decline), not the
position-count study's opposite-direction diversification effect, exactly as the pre-registration
distinguished the two mechanisms. The ceiling was calibrated generously enough that it never actually
discriminates between arms here — worth noting for any future re-run of this lever that a tighter
ceiling might be more informative, though it changes nothing about this study's own verdict.

## Costs — modelled per-arm as required, and large enough to be doing real work in the verdict

**FX drag rises substantially with the cap** (3.34%/yr at c70 to 4.61–4.63%/yr at c95, both pools — a
~1.3pp/yr increase) as turnover scales with available capacity; **idle-cash credit shrinks
correspondingly** (0.55%/yr to ~0.20%/yr) as less capital sits in cash. **Worth stating as an explicit
sensitivity, not just a methodology note**: the net cost adjustment's own swing across the tested range
(roughly 1.5pp/yr net, from the combination of rising FX drag and shrinking idle credit) is comparable
in size to the entire primary-criterion effect being measured (a ~1.06pp spread across arms). The 0.30%
FX round-trip assumption is carried over unchanged from prior work, not re-derived here — a materially
different real-world FX cost would plausibly move this study's own verdict, which is exactly why costs
had to be modelled per-arm rather than deferred (per the standing rule this study was built to honour),
but also means the specific numbers above should be read as sensitive to that one assumption.

## Top-decile overlap and paired per-decision instrument — the decisive finding

**Top-decile overlap** (exact `(ticker, entry_date)` Jaccard / ticker-only Jaccard, vs. baseline):

| | c80 | c90 | c95 |
|---|---|---|---|
| Pool 1 | 0.161 / 0.768 | 0.147 / 0.710 | 0.087 / 0.559 |
| Pool 2 CLEAN | 0.201 / 0.721 | 0.132 / 0.698 | 0.079 / 0.550 |

Falls off steadily as the cap widens — expected, since a wider cap reaches further down the ranked list
and holds far more positions concurrently (28 vs. 14 at c95 vs. c70), diluting the top-decile overlap
mechanically.

**Paired per-decision instrument — every pairing comfortably clears the 100-trade minimum-sample floor
(6,722–12,236 differing trades per pairing). None of the three higher-cap arms' own unique admissions
show a distinguishable edge over baseline's own unique admissions, in either pool:**

| Pool 2 CLEAN | c70-only win / mean P&L% | armX-only win / mean P&L% |
|---|---|---|
| vs. c80 | 43.04% / 0.54% | 42.93% / 0.73% |
| vs. c90 | 43.13% / 0.59% | 42.36% / 0.64% |
| vs. c95 | 43.87% / 0.81% | 43.42% / 0.72% |

Win rates are close throughout (within ~0.8pp), and where they differ, the higher-cap arm's own unique
admissions are slightly *worse* on win rate in every single pairing, not better — including for c80 and
c95, the two arms that nominally cleared the primary bar. **This is the walk-away sentence's own
explicit disqualifying condition, and it fires for all three arms, not just the ones that failed the
primary criterion.**

**A mechanistic reading worth recording, without letting it override the pre-registered rule.** This
result is plausibly the *expected* signature of what raising `max_total_deployed` actually does,
distinct from what a periodic-rebalance or gate-width change does. Per_trade_pct is unchanged, so this
lever doesn't change *which* candidates are preferred — it changes how many can be held concurrently,
reaching deeper into a ranked list that Part 5 of the selection audit already found is "not obviously
worse" on average than the admitted population. Admitting more of a similarly-good population, funded
by more capital rather than more cash sitting idle, is closer to a leverage/exposure effect than a
selection-quality effect — under that reading, "unique admissions aren't better" is not surprising, it's
consistent with the mechanism. **This is not treated as grounds to excuse the clause firing.** The
walk-away sentence was written specifically so a plausible-sounding explanation discovered after seeing
the result cannot talk the study out of its own pre-registered disqualifying condition — that is the
entire point of writing it in advance. The observation is recorded as a hypothesis worth testing on its
own terms in a future study (e.g. a leverage-specific instrument rather than reusing the selection-quality
paired comparison unchanged), not as a reason to adopt here.

## Block count — corroborating only, but doesn't discriminate between the nominal "winners" and the "loser"

Pool 2 CLEAN: baseline 7/16, c80 5/16, c90 5/16, c95 5/16 — all three non-baseline arms show the
identical −2 block move (within the measured 3-block noise floor, not itself decisive), but **the block
count does not distinguish c80/c95 (which nominally clear the primary bar) from c90 (which doesn't)** —
if the primary metric's ordering reflected a real effect, some differentiation here would be a
reasonable thing to hope for, even accounting for the resolution floor. Its absence is one more small
piece of corroboration for reading the whole excess-return spread as noise rather than signal.

## Operational headroom — qualitative, not scored, as pre-registered

At 90–95% median realized deployment (c90/c95), the account routinely operates with only 5–12% of
capital in cash — very little room to admit a new high-conviction candidate without an existing
position first exiting. This backtest's own mechanics don't penalise this directly (admission is still
evaluated correctly against whatever capacity exists that day), but it's a real, live-relevant
responsiveness cost this write-up flags for the recommendation rather than folding into any scored
criterion: a materially thinner cash buffer than today's 0.70 cap provides, for a return effect this
study cannot confirm is real.

## Walk-away sentence, checked clause by clause

> *"I will not adopt any deployment-cap target if: no arm clears +1.5pp cumulative cost-adjusted excess
> return over baseline's own excess return without exceeding baseline's own peak-relative drawdown by
> more than 15pp; or if an arm's realised mean deployment sits closer to baseline's own realised
> deployment than to its own nominal cap; or if the trend across 0.70→0.80→0.90→0.95 is monotonic; or
> if the paired per-decision instrument shows a winning arm's own unique admissions are not
> distinguishable from baseline's on a usable sample."*

1. **Does not fire on its own** — c80 and c95 both clear +1.5pp without breaching the 15pp drawdown
   ceiling. Read alongside the noise-floor-comparable spread and the cross-pool shape disagreement
   above, this "pass" is treated with real caution, not celebrated as a clean result — but the clause
   itself, taken literally, does not disqualify either arm.
2. Does not fire — the deployment guard passes cleanly for all four arms (materiality test above).
3. Does not fire — the curve is genuinely non-monotonic in both pools (though the two pools disagree on
   *which* shape of non-monotonicity, itself a flag, not a pass).
4. **Fires, for all three arms, not just the ones that failed the primary criterion.** No arm's own
   unique admissions show a distinguishable edge over baseline's — win rate is at or slightly below
   baseline's own unique-admission win rate in every single pairing, both pools.

**Not adopted.** Clause 4 fires cleanly and on its own would be sufficient. It is reinforced, not
contradicted, by everything else: the two nominal "wins" (c80, c95) sit inside a spread of results
roughly the size of the measured noise floor, the curve shape does not replicate across pools, and the
block-pass count cannot tell the nominal winners apart from the nominal loser. **This also converges
with, rather than overturns, the two prior looks at this lever named in the motivation** — the
2026-08-06 sweep (0.50/0.70/0.95, cost-adjusted CAGR underperformed 0.50 at every level once costs were
modelled) and the earnings-dampener finding CLAUDE.md itself already flags as resting on a
noise-floor-sized block delta. Re-tested on fully corrected data (the realistic stop-fill, the
recycled-ticker quarantine, the current cost template) specifically because those two priors predate all
three corrections, this study reaches substantially the same place: raising `max_total_deployed` above
0.70 has not been shown to deliver a robust, real improvement, on three independent looks across two
different database states.

## What this doesn't close

**Whether a leverage/exposure-style benefit from higher deployment is real, just not detectable by the
selection-quality-style paired instrument reused here, is a genuinely open question** — flagged, not
answered, in the mechanistic-reading section above. A future study wanting to test this specifically
would need an instrument built for that hypothesis (e.g. comparing portfolio-level compounding effects
directly, holding per-trade selection quality constant by construction, rather than checking whether
*unique* admissions look better) — a different design question, not a re-run of this one. **What this
study does close**: `max_total_deployed` in the 0.80–0.95 range does track its own nominal cap cleanly
(unlike the position-count study's low-end `per_trade_pct` arms) — any future sweep of this lever does
not need to re-litigate whether the parameter does what it's named for.

## Files

`PREREGISTRATION.md`, `step1_run_arms.py`, `step2_instruments.py`, `step2_all_results.json`,
`trades_{pool1,pool2_clean}_{c70,c80,c90,c95}.csv`, `panel_{pool1,pool2_clean}_{c70,c80,c90,c95}.csv`,
`rejections_{pool1,pool2_clean}_{c70,c80,c90,c95}.csv`. Caches reused directly from
`research/position_count_prereg_2026-08-09/_cache_{pool}.pkl`, not rebuilt.
