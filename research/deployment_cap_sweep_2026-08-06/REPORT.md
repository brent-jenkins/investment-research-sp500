# Deployment cap sweep (50% / 70% / 95%), 16-block validated (2026-08-06)

**Type:** single-variable test with block validation. **No adoption decision** — per the task spec, this is a characterisation, not a recommendation.

**Question:** does raising `max_total_deployed` improve the strategy's standing against the passive alternatives from `research/block_benchmark_comparison_2026-08-06`, and does it change *which* passive alternative is the relevant comparator?

---

## Correction notice (added after initial publication, before this report was relied on further)

**The original version of this report claimed the strategy "overtakes leg C" at cap=0.95 on chained aggregate CAGR. That claim does not survive scrutiny and is withdrawn as a headline finding.** Three issues were raised and investigated after the first draft:

1. **A stored KLAC price ($7.199, 2016-10-17) used as evidence the parity divergence wasn't a data problem — investigated properly this time, not just re-asserted.** KLA Corporation executed a genuine, confirmed 10-for-1 forward stock split effective 2026-06-12 ([KLA IR press release](https://ir.kla.com/news-events/press-releases/detail/515/kla-corporation-announces-ten-to-one-stock-split-and); current price ~$193.81 confirmed independently). $7.199 is the correct, standard split-adjusted 2016-10-17 level given that real split (~$72 nominal ÷ 10). **This specific value is not corrupted.** A broader local-only scan for split-ratio discontinuities across all ~500 tickers actually traded in this sweep found zero genuine artifacts — the only non-noise hit (HIG, 2008-12-05, a ratio of 2.02) is a confirmed real, contemporaneously-reported +102% single-day rally on bailout-capital-adequacy news during the depths of the financial crisis, not a data bug. **Block 8's own parity divergence root cause was then resolved by a direct, decisive test**, run before this correction notice was finalized: the prior investigation's own byte-for-byte unmodified script, re-run against this checkout's current database (`research/block8_reproduction_test_2026-08-06/`), reproduces this sweep's own 258-trade/10.24%/£56,307.45 figures, not the originally-published 259/9.10%/£55,600.12 — proof the two database snapshots genuinely differ, and not evidence of nondeterminism in the shared production decision path. See "Decision-engine parity" for the full test.
2. **A real bug, found and fixed.** `deployment_and_risk.py`'s two reconstruction functions processed each day's exits before entries. A trade whose `entry_date == exit_date` (a fresh admission on a block's last day, immediately force-closed by the END_OF_DATA backstop) had its exit event fire as a no-op against an empty `active` dict, then its entry event left it stuck there permanently — silently dropping its cash credit and inflating every later day's `n_open`. In the per-block deployment reconstruction this polluted one day per block boundary (small effect: mean concurrent positions moved 11.46→11.43, 15.97→15.93, 29.20→29.13). In the continuous, 19-year joint-risk-gap panel the same bug let a stuck phantom inflate `n_open` for every day until that ticker traded again — a materially larger effect: **`indep_gap_60d` at cap=0.95 corrects from 2.231 to 2.057** (cap=0.50: 1.115→1.119; cap=0.70: 1.464→1.441). Fixed by reordering to entries-then-exits, which is provably correct for every other case (the production engine's own `flat_before_today` rule already forbids a ticker with a carried-in position from also getting a fresh entry the same day, so same-day entry+exit can only be this one backstop scenario). All figures below use the corrected numbers. See "Decision-engine parity" section for the mechanism in full.
3. **FX conversion cost and idle-cash interest — modelled explicitly for these three arms, not deferred.** Deferring these was correct in every *other* report in this project because they were common to every compared arm. They are not common here — they vary systematically with `max_total_deployed`, the variable under test, which makes deferring them a bias rather than a simplification. Modelled directly from this sweep's own real per-trade `position_gbp` (not the `per_trade_pct` ceiling the original FX audit used as a proxy) and each arm's own real realised-deployment figures: **the entire "leg C reversal" was an artifact of ignoring a cost that scales adversely with turnover and deployment.** See "Cost-adjusted comparison" below — cost-adjusted, the strategy loses to leg C at every cap value tested, and raising the cap does not monotonically help.

**The findings that survive, and are now the report's actual headline:** raising the cap admits progressively weaker signals (median entry momentum 0.706→0.541) while roughly doubling joint risk, deepening drawdown by 50%, and making the GFC era monotonically worse — three dominant-era blocks newly fail the pass bar at 0.95. On a frictionless basis this trades some aggregate return for materially worse risk and a changed product character. **Once realistic costs are included, even that frictionless return gain mostly evaporates** — raising the cap is not a way to close the gap with passive alternatives; if anything it widens it.

---

## Headline (frictionless, as originally computed — kept for reference, superseded by "Cost-adjusted comparison" below)

**Raising the cap does buy real frictionless return, and at 95% the strategy overtakes both re-solved leg C and the S&P on chained aggregate CAGR — but see the correction notice above: this result does not survive realistic transaction-cost modelling and should not be treated as the report's actual conclusion.** The risk-character findings below (points 3–7) are unaffected by the cost-adjustment issue and stand as reported.

1. **Leg C's solved weight rises toward 1.0 as the cap rises — confirmed, and it is the correct primary lens on everything else here.** Mean solved weight (C-zero-tr): **0.614 → 0.764 → 0.845** across 0.50/0.70/0.95. At higher deployment the strategy is progressively less distinguishable from near-full index exposure, which is exactly the comparator it struggled against in the prior report.

2. **Frictionless chained aggregate CAGR: 7.23% → 8.33% → 10.74%.** At 0.95 this frictionless figure beats both re-solved leg C variants (C-zero-tr 9.67%, C-real-tr 9.81%) and S&P price return (A-pr 8.87%). **This is the result the cost-adjustment section below overturns.**

3. **Block-pass rate falls, not rises: 12/16 → 13/16 → 10/16.** Blocks 6, 7 and 8 — all inside the *dominant* 2010–2019 era, the era the prior report's whole "holds in the dominant era" conclusion rested on — newly fail the standard drawdown-vs-S&P bar at 0.95 that they passed at 0.50/0.70. Mean strategy drawdown deepens monotonically: peak-relative **−9.46% → −12.24% → −14.21%**; fixed-denominator **−10.42% → −13.96% → −16.52%**.

4. **The GFC era gets decisively worse, not better, as the cap rises — this is the era where the risk actually bites.** Median gap vs C-zero-tr: **−7.35pp → −10.68pp → −12.09pp** (2 blocks, 0/2 wins at every cap value). The dominant 2010–2019 and recent 2020–2026 eras both improve with the cap on a frictionless basis (win rate vs C-zero-tr: 55.6%→55.6%→66.7%; vs A-pr: 44.4%→55.6%→66.7% in the dominant era) — but the one era a defensive/reduced-risk product is supposed to earn its keep in is exactly where raising deployment makes things worse, and by a widening margin.

5. **Realised deployment rises steadily with the cap but never reaches it, and the shortfall widens as the cap rises.** Mean realised deployment: **44.98% → 62.86% → 85.65%** (corrected) against permitted caps of 50/70/95 — shortfalls of 5.02pp, 7.14pp and 9.35pp respectively. The cap is doing real work (deployment keeps rising with it), but the widening shortfall at 95% shows the strategy increasingly can't *find* enough qualifying candidates to fill the extra headroom.

6. **Joint-risk-gap (`indep_gap_60d`, corrected) roughly doubles: 1.119 → 1.441 → 2.057 (+28.7%, then +42.8%, +83.8% overall).** Mean concurrent positions: 12.07 → 16.82 → 30.78 (this table's own convention; see "Internal consistency note" below for why this differs slightly from the deployment table's 11.43/15.93/29.13, and why that residual gap is legitimate, not a bug). This is a large, real worsening of concentration risk, consistent in direction with `deployment_cap_tradeoff_2026-08-05`'s own 50→70% finding (+23.6%).

7. **Capacity-turnover splits cleanly: almost none of the extra return comes from sizing existing opportunities bigger; nearly all of it is admission of new, progressively lower-momentum candidates — a real, quantified dilution cost, unaffected by the cost-adjustment or bug-fix issues above.** Direct effect (trades admitted at both the baseline and a higher cap): position size barely moves (mean position £ change **−0.11%** at 0.70, **−5.22%** at 0.95). Admission effect: **+1,631 trades at 0.70, +6,315 at 0.95** — and their momentum at entry skews steadily lower: baseline admitted-trade median momentum **0.706**; new admissions at 0.70 median **0.647**; new admissions at 0.95 median **0.541**.

---

## Stage 0 — coverage gate (run first, reported before the main sweep)

From `research/block_benchmark_comparison_2026-08-06/universe_coverage_by_block.csv`. The dominant 2010–2019 era corresponds to blocks 2–10 (2009-06-13 → 2020-06-21), matching the prior report's own era table (9 blocks).

| Block | Window start | `pct_usable` |
|---|---|---|
| 2 | 2009-06-13 | 54.9% |
| 3 | 2010-09-03 | 56.5% |
| 4 | 2011-11-25 | 59.2% |
| 5 | 2013-02-14 | 60.8% |
| 6 | 2014-05-07 | 62.8% |
| 7 | 2015-07-29 | 66.6% |
| 8 | 2016-10-18 | 70.8% |
| 9 | 2018-01-08 | 73.4% |
| 10 | 2019-04-01 | 79.3% |

**Dominant-era mean coverage: 64.9%. Every one of the 9 dominant-era blocks sits below ~90% coverage — the maximum is block 10 at 79.3%, still 11pp short.** This does not gate whether the sweep runs, but it gates how much weight its output can carry: the prior report's "the leg C result holds in the dominant era" conclusion — the same conclusion this sweep finds strengthening further at higher caps (point 4 above) — rests on a candidate universe from which historical failures have been partially removed (missing names are disproportionately those that left the index) for the entire span that conclusion is built on. **Every dominant-era number in this report, and in the wider project, should be read with that caveat attached — not as a qualitative aside, but as a concrete 64.9%-mean, 54.9–79.3%-range fact.**

---

## Conventions (stated explicitly)

- **Reset-at-boundary.** Every block starts fresh at £50,000 with zero positions. Cross-cap comparison = reset-at-boundary is correct (never continuous walk).
- **Corrected engine.** `simulate_portfolio_compounding_mtm()`, the 2026-08-06 intraday-touch STOP_LOSS fill as its permanent default. Not the frozen exempt engines.
- **Window: 2007-01-01 → 2026-08-03** (the prior report's own verified reproduction window, not 08-06), 16 blocks, £50,000 per block.
- **Single variable.** `max_total_deployed` ∈ {0.50, 0.70, 0.95}; `per_trade_pct` fixed at 0.05 throughout (config's own current value, asserted in code, not assumed).
- **Both drawdown conventions** (peak-relative, fixed-denominator) reported per cap, chained and per block.
- **CAGR annualised over the block calendar span**, identical methodology to the prior report (`strat_cagr_pct`, not the trade-span variant).
- **All legs unconverted USD**, symmetric, no FX conversion on any leg. Monthly rebalancing for legs C and D.
- **Chained aggregate** = the product of each block's own ending-balance ratio, compounded across all 16 reset-at-boundary blocks in sequence, annualised over the full 2007–2026 span. A derived summary of block results, *not* a continuous walk.

---

## Decision-engine parity — required gate, and where it didn't fully clear

The cap=0.50 arm was checked block-by-block against the prior report's own `block_results_raw.json`. **15 of 16 blocks reproduce exactly** (2 of those 15 — blocks 7 and 15 — differ by a few pence in ending balance, £0.02 and £0.04 respectively, consistent with floating-point summation-order noise, not an economic difference). **Block 8 diverges materially**: 258 trades vs. the prior run's 259, ending balance £56,307.45 vs. £55,600.12, CAGR 10.24% vs. 9.10%.

**Investigated, not waved through, per the task spec's "if it is not byte-identical, stop and report that first":**
- Block boundaries are byte-identical between the two runs (checked directly, all 16 blocks).
- `n_candidates` for block 8 matches exactly (506 both).
- The three tickers whose trades actually swapped (KLAC present only in the prior run; PCAR and CFG present only in this sweep) are all genuine index members throughout the block in both cases.
- This sweep's own harness is confirmed fully deterministic: block 8 re-run twice in-process, same database, produces byte-identical 258-trade output both times.

**KLAC's price specifically was investigated properly, not just re-asserted as "identical, therefore fine."** The first draft of this report used "$7.199 is identical between the two DB reads" as evidence against a data problem — that reasoning was called out, correctly, as the same inversion `backfill_price_history()`'s own docstring warns about: a row that is never re-fetched is stable *and* stale/wrong looks identical to a row that is stable *and* correct, from inside the same database alone. This was re-investigated against **external, independent evidence**, not the database: KLA Corporation executed a genuine, confirmed 10-for-1 forward stock split effective 2026-06-12 ([KLA IR press release](https://ir.kla.com/news-events/press-releases/detail/515/kla-corporation-announces-ten-to-one-stock-split-and) — "shareholders of record on June 4, 2026 ... trading on the adjusted basis beginning June 12, 2026"), and the current real KLAC price (~$193.81, independently checked) divided back through that one split lands almost exactly on the stored $7.199 for the equivalent 2016-10-17 level (~$72 nominal ÷ 10 ≈ $7.20). **This value is correct, standard split-adjustment — not corruption.**

A broader, systematic check was also run rather than stopping at the one ticker: `split_detection.py` scanned all ~500 tickers actually traded across the three cap arms for any single-day return matching a clean, implausible-as-ordinary-volatility split ratio (0.5, 0.25, 0.2, 1/7, 0.1, 0.05, 0.02, and their inverses). **Zero genuine artifacts found.** The one hit that survived filtering out ordinary-crash-day noise (HIG, 2008-12-05, ratio 2.02 ≈ a doubling) is a confirmed, contemporaneously-reported real event — Hartford Financial Group's stock genuinely surged +102% that day on a raised profit forecast and capital-adequacy reassurance, after having fallen 92% earlier that year — not a split-adjustment artifact (the ratio itself, 2.0236, is also too imprecise for a mechanical split adjustment, which would land exactly on 2.0000 to several decimals; real trading days produce messy ratios, splits produce exact ones).

**Conclusion: not non-determinism in the decision logic, not a bug in this harness, and — now checked properly rather than assumed — not data corruption either, for KLAC specifically or across the sweep's traded universe generally.**

**Root cause resolved by a direct, decisive test: the databases genuinely differ; this is bookkeeping, not nondeterminism in a shared production path.** Two candidate explanations remained open, and they differ sharply in seriousness — a database snapshot difference is routine bookkeeping; nondeterminism in `simulate_portfolio_compounding_mtm()` or any function it calls would undermine every comparison this project makes. The distinguishing test: `research/block8_reproduction_test_2026-08-06/` copied the prior investigation's own `research_block_benchmarks.py` **byte-for-byte, unmodified** (`config.yaml` copied alongside it as a read-only reference so its bare relative-path `open("config.yaml")` call resolves; `PYTHONPATH` set rather than touching a single line of the script itself) and re-ran the full 16-block harness against this checkout's current database. **Block 8 reproduced 258 trades / CAGR 10.24% / £56,307.45 — matching this sweep's own cap=0.50 arm exactly, not the originally-published 259 trades / CAGR 9.10% / £55,600.12.** The identical script, run against this checkout's database by an entirely separate process invocation, lands on the same answer this sweep's own independently-built harness already produced — while the *same* script, run earlier against a *different* database snapshot, produced a different answer. Same code, different database → different answer; same code, same database (now confirmed three independent ways: this sweep's original run, an in-process double-run, and this fully separate script invocation) → identical answer, every time. **That is the signature of a database difference, not nondeterminism.** The specific upstream data point that first diverges between the two database snapshots was not isolated (not needed, once the database-vs-nondeterminism question itself is answered).

**This affects only the absolute comparison against the separately-run prior investigation's own published figures for one of 16 blocks. It does not affect the cap=0.50 vs 0.70 vs 0.95 comparison this task exists to answer** — all three arms of this sweep were run against the identical, self-consistent local database and candidate universe, so the internal comparison remains valid even though this sweep's own cap=0.50 chained aggregate (7.23%) differs marginally from the prior report's separately-computed 7.20%.

---

## Per-cap, per-block results

Full detail in `results/sweep_comparison_wide.csv` (48 rows: 3 caps × 16 blocks). Summary:

| Cap | Frictionless chained CAGR | Cost-adjusted chained CAGR | Mean peak-rel. DD | Mean fixed DD | Blocks passing | Mean concurrent positions | Mean realised deployment |
|---|---|---|---|---|---|---|---|
| 0.50 | 7.23% | 5.62% | −9.46% | −10.42% | 12/16 | 11.43 | 44.98% |
| 0.70 | 8.33% | 5.43% | −12.24% | −13.96% | 13/16 | 15.93 | 62.86% |
| 0.95 | 10.74% | 6.23% | −14.21% | −16.52% | 10/16 | 29.13 | 85.65% |

Failing blocks: 0.50 → {0, 1, 7, 15}; 0.70 → {0, 1, 15}; 0.95 → {0, 1, **6, 7, 8**, 15}. Blocks 6, 7, 8 sit entirely inside the dominant 2010–2019 era and pass at both 0.50 and 0.70 — they fail *only* at 0.95, even though that same era's *aggregate* win rate against leg C and the S&P improves at 0.95 (see era decomposition below). The two things are not the same bar: aggregate return-vs-comparator can improve while individual-block absolute drawdown-vs-S&P still breaches.

---

## Leg C — re-solved per cap, per block (the reversal this task required)

**Not reused from the prior run.** Bisected fresh against each cap's own realised drawdown, per block, for all 4 variants (cash zero/real × index price/total-return) — 192 independent bisections (3 caps × 16 blocks × 4 variants).

| Cap | Mean C-zero-tr weight | Mean C-real-tr weight |
|---|---|---|
| 0.50 | 0.614 | 0.622 |
| 0.70 | 0.764 | 0.769 |
| 0.95 | 0.845 | 0.846 |

**The expected pattern occurs, and is the headline finding it was flagged as.** Weight rises toward 1.0 as the cap rises — at 0.95 leg C sits at a mean 0.845, materially closer to leg A's full index exposure than to the 0.50 baseline's near-50/50 split. Several individual blocks hit the w=1.0 ceiling outright at 0.95 (blocks where even full index exposure can't match the strategy's own deepened drawdown): the strategy is no longer being tested against a materially risk-reduced comparator at the higher caps — it is competing against something close to buy-and-hold.

---

## Headline win rates vs. every leg, per cap (never averaged)

| Leg | 0.50 | 0.70 | 0.95 |
|---|---|---|---|
| A — S&P 500, price return | 7/16 (−0.57pp) | 7/16 (−0.54pp) | **9/16 (+2.11pp)** |
| A — S&P 500, est. total return | 4/16 (−2.63pp) | 5/16 (−2.63pp) | 8/16 (−0.33pp) |
| B — NASDAQ-100, price return | 1/16 (−7.98pp) | 3/16 (−7.21pp) | 6/16 (−4.94pp) |
| B — NASDAQ-100, est. total return | 1/16 (−8.59pp) | 3/16 (−8.27pp) | 5/16 (−6.19pp) |
| D — 60/40, price-return sleeve | 6/16 (−1.39pp) | **10/16 (+2.03pp)** | **12/16 (+5.19pp)** |
| D — 60/40, est. total-return sleeve | 6/16 (−2.73pp) | 9/16 (+0.66pp) | 12/16 (+4.04pp) |
| C — dd-matched, cash zero, price return | 9/16 (+2.44pp) | 8/16 (+0.97pp) | 10/16 (+6.12pp) |
| C — dd-matched, cash zero, total return | 8/16 (+1.05pp) | 8/16 (−0.89pp) | **10/16 (+4.11pp)** |
| C — dd-matched, cash real, price return | 8/16 (+1.40pp) | 7/16 (−0.03pp) | 10/16 (+5.98pp) |
| C — dd-matched, cash real, total return | 8/16 (+0.09pp) | 7/16 (−1.86pp) | 10/16 (+4.03pp) |

(gaps are strategy CAGR minus leg CAGR, median across blocks)

**Note the non-monotonic dip at 0.70 against leg C** (win count and median gap both *worsen* slightly from 0.50 to 0.70 before improving sharply at 0.95) — leg C's own weight is rising faster than the strategy's edge over it in this intermediate range; the improvement is not linear and should not be extrapolated between the three tested points.

---

## Ordering — does it change? (frictionless — see cost-adjusted section immediately below for the figure that should actually be used)

| Cap | Frictionless chained CAGR | Rank (of 11 legs) |
|---|---|---|
| 0.50 | 7.23% | 8th |
| 0.70 | 8.33% | 7th |
| 0.95 | 10.74% | 4th (frictionless only) |

**This table is retained for transparency about what the original draft claimed, not as a usable conclusion.** On a frictionless basis, 0.95 appears to beat 9 of the other 10 legs. Once real transaction costs are included (below), this reorders back to something close to the original baseline picture at every cap tested — the frictionless "4th of 11" figure should not be quoted on its own.

---

## Cost-adjusted comparison — FX conversion cost and idle-cash interest, modelled specifically for these three arms

**Why this is required here and not elsewhere in this project's reports.** Every other backtest report in this codebase correctly defers FX conversion cost and idle-cash interest as "excluded, roughly offsetting, stated not modelled" — legitimate when both costs are common to every compared arm (a single strategy vs. a fixed set of passive legs, none of which trade at a rate that depends on the thing being tested). That is not this report. Trade count here **scales directly with the variable under test** (4,086 → 5,717 → 10,401 across 0.50/0.70/0.95), so FX cost — which scales with turnover — is a **treatment effect**, not a constant; and mean idle cash **shrinks** as the cap rises (55.02% → 37.14% → 14.35%), so the idle-cash credit that partly offsets FX cost elsewhere shrinks in the same direction the FX cost grows. Deferring both here would not be a simplification, it would be a bias toward the high-cap arm specifically — so both are modelled directly.

**Method — real per-trade data, not the `per_trade_pct` ceiling.** `research/fx_denomination_audit_2026-08-06` established T212's confirmed round-trip FX fee (0.15%/leg, 0.30% round-trip, verified against T212's own published terms) and used `per_trade_pct` (5%, the VaR sizing *ceiling*) as a proxy for average position size, since it didn't have full trade-level output handy for that specific calculation. This sweep does: FX cost per arm = Σ(`position_gbp`) × 0.30%, using every trade's own real recorded size — no proxy. Idle-cash credit per arm = mean idle % (100% − mean realised deployment, from the corrected reconstruction above) × the mean ^IRX (13-week T-bill) rate over the identical 2007-01-01→2026-08-03 window already used for leg C's own "real" cash treatment (1.525%/year) — the same rate, not a new assumption. Both expressed as an annualised percentage of each arm's own mean account balance and netted against the reported frictionless chained CAGR.

**Cross-check against the prior FX audit, before trusting this:** this sweep's own baseline (cap=0.50) turnover works out to 208.6 round trips/year — matching the prior audit's independently-cited 207.7 almost exactly, despite being computed from a completely different run. The real average position fraction of balance (3.91%) comes in below the 5% ceiling the prior audit used as a proxy, exactly in the direction that audit's own caveat anticipated ("the true figure could plausibly be somewhat lower").

| Cap | Round trips/yr | FX drag (pp/yr) | Mean idle | Idle credit (pp/yr) | Net adjustment | Frictionless CAGR | **Cost-adjusted CAGR** |
|---|---|---|---|---|---|---|---|
| 0.50 | 208.6 | −2.449 | 55.02% | +0.839 | **−1.610pp** | 7.23% | **5.62%** |
| 0.70 | 291.8 | −3.466 | 37.14% | +0.566 | **−2.899pp** | 8.33% | **5.43%** |
| 0.95 | 530.9 | −4.731 | 14.35% | +0.219 | **−4.512pp** | 10.74% | **6.23%** |

**Against leg C, cost-adjusted:**

| Cap | Cost-adjusted CAGR | vs. C-zero-tr | vs. C-real-tr | vs. A-pr |
|---|---|---|---|---|
| 0.50 | 5.62% | 7.51% — **loses** | 8.05% — **loses** | 8.87% — **loses** |
| 0.70 | 5.43% | 9.14% — **loses** | 9.47% — **loses** | 8.87% — **loses** |
| 0.95 | 6.23% | 9.67% — **loses** | 9.81% — **loses** | 8.87% — **loses** |

**The reversal does not survive at all — it isn't narrower, it's gone.** Cost-adjusted, the strategy loses to every one of leg C's variants and to the S&P at every cap value tested, including the 0.50 baseline (which the original, un-cost-adjusted 16-block report already found losing to leg C on a frictionless basis — cost-adjustment makes an already-marginal loss unambiguous). Raising the cap does not monotonically help even on a cost-adjusted basis: 5.62% → 5.43% → 6.23%, a dip then partial recovery, never approaching leg C's 9.67–9.81%. **The entire "strategy overtakes leg C at 0.95" finding in the original draft of this report was an artifact of comparing a frictionless strategy return against benchmark legs that are also frictionless but trade far less — leg C's own two annual rebalancing trades per block carry negligible FX cost by comparison, while the strategy's real turnover, which itself rises with the cap, does not.**

This is not evidence the cost model is exactly right — it is a rough, order-of-magnitude estimate (stated as such by the source FX audit itself), and neither the FX fee nor the idle-cash credit is applied with compounding effects on subsequent sizing (a genuinely lower net return would itself slightly reduce position sizes and therefore future FX costs — a second-order effect not modelled here, plausibly making the true adjusted figures very slightly less negative than shown). But the magnitude of the reversal (frictionless 10.74% vs. cost-adjusted 6.23% at 0.95, a 4.51pp gap against a headline "improvement" of only 3.51pp from the 0.50 baseline) is large enough that no plausible refinement of this estimate restores the original conclusion.

---

## Realised vs. permitted deployment

| Cap | Mean deployed | Median deployed | Shortfall vs. cap | Mean concurrent positions | Days cap "bound"* |
|---|---|---|---|---|---|
| 0.50 | 44.98% | 48.84% | 5.02pp | 11.43 | 87.27% |
| 0.70 | 62.86% | 68.74% | 7.14pp | 15.93 | 86.70% |
| 0.95 | 85.65% | 94.07% | 9.35pp | 29.13 | 86.41% |

(Corrected for the same-day entry/exit bug fix — see "Correction notice." The effect on this table is small since it only pollutes one day per block boundary in a per-block reconstruction; the same bug's effect was much larger in the joint-risk-gap table below, which runs as one continuous 19-year walk.)

\* "Cap bound" = share of block-days on which the real production function recorded ≥1 genuine `REJECTED_CAPACITY` rejection (not a deployed-% threshold heuristic) — the exact days a real candidate was turned away for lack of capacity, pooled across all 16 blocks (5,122 block-days total).

**Both things the task asked to distinguish are true simultaneously, and neither is the clean story.** The cap is *not* inert — realised deployment climbs steadily and substantially with it (45%→63%→86%), so raising the ceiling clearly does free real capital to be put to work; this is not the "candidate flow is fully binding, cap change is inert" scenario. But the shortfall between permitted and realised *widens* as the cap rises, and "cap bound" days stay essentially flat (~86–87%) at every level tested — meaning the constraint that's actually biting on any given day doesn't relax much as the ceiling rises. Mean concurrent positions at 0.50 (11.43) closely matches the task spec's own cited reference figure (~11.4).

**Internal consistency note.** This table's mean concurrent positions (11.43/15.93/29.13) differs slightly from the joint-risk-gap table's own figure (12.07/16.82/30.78) below. Both are now correct — the gap is a legitimate difference in denominator, not a bug: this table's per-block reconstruction includes block 0's ~370-day zero-position momentum warm-up period (no ticker has 12 months of trailing history yet, so genuinely zero candidates qualify), while the joint-risk-gap table's window starts at the very first real trade (2008-01-03), after that warm-up period has already passed. Restricting this table's own window to match (`date >= 2008-01-03`) moves its mean from 11.43 to a directly comparable 12.08 — matching the joint-risk table closely. Before the entries/exits bug fix, the two tables' `max_n_open` figures disagreed (a symptom of the real bug); after the fix, `max_n_open` matches exactly (20/28/41 in both) — the residual mean difference is fully explained by this windowing choice, stated explicitly here rather than left unreconciled.

---

## Risk reporting

**Drawdown, both conventions, chained/mean** — see the per-cap table above (mean peak-relative −9.46%/−12.24%/−14.21%; mean fixed −10.42%/−13.96%/−16.52%). Full per-block detail in `results/sweep_comparison_wide.csv`.

**Joint-risk-gap (`indep_gap_60d`)** — reusing `research/deployment_cap_tradeoff_2026-08-05`'s own formula verbatim, computed from each cap's own real day-by-day open-position weights (not extrapolated). **Corrected for the same-day entry/exit bug described in "Correction notice"** — the bug's effect was materially larger here than in the deployment table above, since this table runs as one continuous 19-year walk with no per-block reset to clear a stuck phantom position:

| Cap | Mean `indep_gap_60d` | p95 | Mean concurrent positions | Max concurrent positions |
|---|---|---|---|---|
| 0.50 | 1.119 | 1.952 | 12.07 | 20 |
| 0.70 | 1.441 (+28.7%) | 2.398 | 16.82 | 28 |
| 0.95 | 2.057 (+83.8% vs. baseline) | 3.120 | 30.78 | 41 |

(Pre-fix, buggy figures were 1.115/1.464/2.231 and max concurrent positions 27/36/59 — the bug inflated `max_n_open` substantially, since a handful of stuck phantoms accumulating over 19 years could coincide with a genuinely high-concurrency day. Post-fix, `max_n_open` matches the deployment table's own figure exactly at every cap, 20/28/41 — the two tables are now fully consistent with each other, modulo the legitimate windowing difference noted above.)

**The joint-risk gap this project already knows is real gets substantially worse as deployment rises — still roughly doubling from 50% to 95%, even after correction.** This is consistent in direction with, and larger in magnitude than, `deployment_cap_tradeoff_2026-08-05`'s own 50→70% grid finding (+23.6%) — this sweep's wider 50→95% range shows the effect compounding further rather than levelling off. Realised joint risk at 0.95 runs roughly 3.1x an independence-sizing assumption at the 95th percentile, up from ~2.0x at baseline. (Worth flagging, not investigated further here: `deployment_cap_tradeoff_2026-08-05`'s own `joint_risk_engine.py` — the source this formula was reused verbatim from — appears to share the identical exits-before-entries ordering, meaning its own previously-reported 1.239/1.385/1.532 figures may carry the same small-to-moderate inflation found here; not re-run as part of this task, which is scoped to the deployment-cap sweep, but noted for whoever next relies on that investigation's own numbers.)

**Block pass count** — 12/16 → 13/16 → **10/16**. Falls at the highest cap despite the return improvement, driven by three newly-failing dominant-era blocks (6, 7, 8) — see "Per-cap, per-block results" above.

---

## Capacity-turnover split (mandatory, reported separately)

**Direct effect** (opportunities admitted under both the baseline and a higher cap — matched by block/ticker/entry-date):

| Cap | Matched opportunities | Mean position £ change | Median position £ change |
|---|---|---|---|
| 0.70 | 1,645 | −0.11% | +0.01% |
| 0.95 | 1,191 | −5.22% | 0.00% |

Negligible, as expected: `per_trade_pct` is fixed at 5% of current balance throughout this sweep, so an individual position's size is set independently of the deployment cap — the cap governs how many positions can be open at once, not how big any one of them is.

**Admission effect** (trades present at the higher cap that were not admitted at baseline):

| Cap | New admissions | Baseline trades lost† | New-admission momentum: mean / median | Baseline momentum: mean / median |
|---|---|---|---|---|
| 0.70 | 4,072 | 2,441 | 0.858 / 0.647 | 0.953 / 0.706 |
| 0.95 | 9,210 | 2,895 | 0.690 / 0.541 | 0.953 / 0.706 |

† "Baseline trades lost" = (ticker, entry-date) pairs admitted at 0.50 that no longer appear at the higher cap — a downstream consequence of the capacity-constrained admission cascade reshuffling which candidates win on contested days, not evidence those tickers stopped qualifying.

**Nearly all of the extra return comes from admission, not sizing, and the newly-admitted trades are measurably, monotonically weaker on momentum.** Median momentum at entry falls from the baseline's 0.706, to 0.647 among 0.70's new admissions, to 0.541 among 0.95's — a real dilution cost, not a footnote: raising the cap does not primarily mean "take the same good trades bigger," it means "take several thousand more, progressively weaker trades." That this still nets out to a higher chained CAGR says the marginal admitted trade remains profitable on average even at low momentum deciles in this dataset — but it is a genuinely different kind of return than the concentrated, high-conviction profile at 0.50.

---

## Era decomposition (required)

| Era | Blocks | vs. C-zero-tr (0.50 → 0.70 → 0.95) | vs. A-pr (0.50 → 0.70 → 0.95) |
|---|---|---|---|
| 2007–2009 GFC | 2 | 0/2, 0/2, 0/2; median gap **−7.35pp → −10.68pp → −12.09pp** | 1/2, 0/2, 0/2; median gap +1.08pp → −4.79pp → −6.53pp |
| 2010–2019 bull (dominant) | 9 | 5/9, 5/9, **6/9**; median gap +2.83pp → +0.53pp → +2.71pp | 4/9, 5/9, **6/9**; median gap −0.95pp → +0.60pp → +2.90pp |
| 2020–2026 recent | 5 | 3/5, 3/5, **4/5**; median gap +2.66pp → +0.18pp → +5.70pp | 2/5, 2/5, **3/5**; median gap −6.73pp → −3.14pp → +6.22pp |

**The GFC era gets monotonically worse as the cap rises — the opposite direction from every other era, and the one result in this report that should weigh most heavily against raising the cap.** Both GFC blocks lose to leg C at every cap tested, and the margin of loss roughly doubles from 0.50 to 0.95 (median gap −7.35pp → −12.09pp). Meanwhile the dominant 2010–2019 era's already-marginal "holds" finding from the prior report **strengthens** with the cap on a frictionless basis (win rate 55.6%→55.6%→66.7% vs. both C-zero-tr and A-pr) — but that strengthening result is exactly the one Stage 0 flagged as resting on 64.9%-mean survivorship-biased coverage, so it should be read as *more* favourable-looking than the true, failure-inclusive picture would show, not less. **This table's win-rates are frictionless, like the ordering table above** — a genuine per-block cost adjustment was not built (it would require attributing FX cost to individual blocks rather than the aggregate), but since transaction costs scale with each block's own trade frequency, applying them would be expected to push every era's numbers down somewhat, more so at higher caps where turnover is higher — this table's directional pattern (GFC monotonically worsening, other eras frictionlessly improving) is the more robust part of this report's findings; the specific magnitudes above it are not cost-adjusted and should be read with that caveat.

---

## Excluded costs (stated, not modelled) — for anything OTHER than the three cap arms

FX conversion and idle-cash interest are modelled explicitly for the strategy leg across the three cap arms above (see "Cost-adjusted comparison") — they are not simply stated-and-deferred here, because they are a treatment effect for this specific comparison. They remain **un**modelled, appropriately, for: the passive legs (A/B/D/C), which trade far less frequently (leg C rebalances monthly, negligible FX cost by comparison) and are not the variable under test; and for anything in this project's other reports where turnover is genuinely constant across the compared arms.

---

## Interpretation

Per the task spec, **no adoption decision is made here.** Reading the results against the four stated scenarios — **cost-adjusted, since the frictionless comparison was shown above not to be the right one to reason from**:

- *"Standing improves against a re-solved leg C"* — **false, once costs are included.** The frictionless version of this claim (chained CAGR overtaking both leg C variants at 0.95) does not survive: cost-adjusted, the strategy loses to every leg C variant at every cap tested, including the 0.50 baseline.
- *"Standing improves against leg A but the strategy stops being a reduced-drawdown vehicle"* — **the second half is true and is now the report's real headline; the first half is false once costs are included.** Leg C's own weight climbing to a mean 0.845 at 0.95 is direct, quantified evidence the comparator itself has shifted toward full index exposure; the strategy's own mean drawdown deepening by 50% (peak-relative −9.46%→−14.21%) and 4 more blocks failing the standard pass bar confirm the product's risk character has genuinely changed. But this character change is not compensated by a real return gain once costs are included — it is a worse risk profile *without* the offsetting frictionless return that made it look like a trade-off in the first draft.
- *"Realised deployment barely moves"* — **false.** Deployment climbs substantially and monotonically with the cap; this is not a cheap-to-close question, and the cap does real, costly work (more trades, more turnover, more FX cost) even though the return it buys does not survive that same cost.
- *"Returns rise but drawdown and joint-risk rise faster"* — **true, and now the closest of the four scenarios to what actually happened, though understated even by its own framing.** On a frictionless basis, chained-aggregate return rose faster than mean drawdown in relative terms; cost-adjusted, return barely rises at all (5.62%→5.43%→6.23%, non-monotonic) while joint-risk-gap still roughly doubles (1.119→2.057) and drawdown still deepens by 50%. This is closer to "risk rises, return doesn't" than "both rise, risk rises faster." The era decomposition's directional pattern (GFC monotonically worse, other eras frictionlessly better) still stands as the most robust single finding in this report — a product whose worst-case behaviour degrades while its typical-case behaviour merely holds its own (not even clearly improves, once costs are considered) is not a case for raising the cap.

**Overall: raising the deployment cap is not a way to close the gap with the passive alternatives from the prior report.** It increases turnover, joint-risk, and drawdown; the frictionless return it appears to buy is consumed by the transaction costs that same higher turnover creates. The one place a genuine, unambiguous trade-off remains visible is the era decomposition: modestly better performance in calm/bull markets, purchased with a real, monotonically worsening loss in the one crisis era (GFC) a risk-managed product exists to protect against.

No further deployment work is implied to be warranted or unwarranted by this report — that decision, per the task spec, is for a human, informed by the trade-offs above: real return gain, real risk-character change, era-concentrated downside, and a substantial (not marginal) joint-risk-gap increase.

---

## Measured vs. estimated

**Measured:** all strategy figures at all 3 cap values (real production function, real trades); legs A/B/D price and total returns (reused unchanged from the prior report, which measured them directly); leg C's solved weights and realised drawdowns (bisected fresh here, 192 independent solves); realised deployment and joint-risk-gap (both reconstructed from this sweep's own real trade output — the deployment reconstruction is an exact replay of the account-model formulas `simulate_portfolio_compounding_mtm()` itself documents and uses, not an approximation).

**Estimated:** index total-return variants (inherited unchanged from the prior report — SPY/QQQ-derived dividend factors, net of ETF expense ratio). FX conversion cost and idle-cash interest are now **modelled** (not merely stated) for the strategy leg across the three cap arms — see "Cost-adjusted comparison" — using real per-trade position sizes and the real mean ^IRX rate, but the FX fee itself, and the assumption that costs don't compound into smaller subsequent position sizes, are still estimates, not exact.

---

## Working discipline

Local research session, real production functions called directly (`simulate_portfolio_compounding_mtm()`, `_block_passes()`, `_cagr_dd_for_series()`, `_strategy_ending_balance_gbp()` — all imported from `analysis.backtest`/`analysis.validation`, none reimplemented). Read-only on all production files — `config.yaml` untouched throughout; `max_total_deployed` and `per_trade_pct` were passed as direct function arguments to the shared engine, never written to config. SPY and ^IRX price history were ingested into this checkout's local database via the real production ingestion path (`data.ingestion.pull_history`), matching what the prior investigation did in its own copy — nothing else was ingested or modified. Nothing committed to git yet, nothing pushed, nothing deployed. Oversized intermediate artifacts (`ticker_caches.pkl`, ~107MB; three `rejections_capacity_cap*.csv` files, ~100MB each) are deliberately left untracked, per this project's own established practice — they are locally reproducible from `run_sweep.py` and not needed to audit the headline figures above, which are fully preserved in the tracked trade-level CSVs.

## Files

| File | Contents |
|---|---|
| `run_sweep.py` | Main harness: builds/caches ticker precompute, runs all 48 (cap, block) simulations, re-solves leg C (4 variants) per cap per block |
| `parity_check.py` | Fidelity gate vs. the prior report's cap=0.50 figures |
| `../block8_reproduction_test_2026-08-06/` | Separate sub-investigation: re-runs the prior investigation's own unmodified script against this checkout's database, resolving block 8's parity divergence as a database difference, not nondeterminism — see its own `README.md` |
| `deployment_and_risk.py` | Realised-deployment reconstruction + joint-risk-gap (`compute_indep_gap_for_trades`, reused verbatim from `deployment_cap_tradeoff_2026-08-05`) |
| `capacity_turnover.py` | Direct-effect vs. admission-effect split, momentum-decile |
| `analyse_final.py` | Headline win-rates, chained aggregate, ordering, era decomposition |
| `split_detection.py` | Local-only scan for split-ratio price discontinuities across every traded ticker (see "Decision-engine parity") |
| `cost_adjustment.py` | FX conversion cost + idle-cash interest, modelled per cap arm from real trade data (see "Cost-adjusted comparison") |
| `results/split_detection_hits.csv` | Raw output of the split-ratio scan, all ratios, before filtering to implausible-as-ordinary-volatility ones |
| `results/cost_adjustment_summary.json` | FX/idle-cash cost model output per cap, and the cost-adjusted CAGR vs. leg comparison |
| `results/sweep_block_results_raw.json` | Complete raw per-(cap,block) result objects, including all 4 leg-C variants |
| `results/sweep_comparison_wide.csv` | One row per (cap, block), all legs side by side |
| `results/trades_cap{05,07,095}.csv` | **Full trade-level output, preserved per cap value, block-tagged** — 4,086 / 5,717 / 10,401 trades respectively |
| `results/deployment_panel_cap{05,07,095}.csv` | Daily balance/deployed/n_open reconstruction, per cap |
| `results/joint_risk_panel_cap{05,07,095}.csv` | Daily `indep_gap_60d`/`n_open` panel, per cap |
| `results/capacity_turnover_summary.json` | Direct/admission effect split, full momentum-decile breakdowns |
| `results/deployment_and_risk_summary.json` | Realised-deployment and joint-risk-gap summaries, per cap |
| `results/final_analysis.json` | Headline win-rates, chained aggregate, ordering, era decomposition — the source for every table above |
| `ticker_caches.pkl` | Precomputed price/ATR/momentum/signal frames, all live candidates (**not tracked, ~107MB**) |
| `results/rejections_capacity_cap*.csv` | Every real `REJECTED_CAPACITY` event, per cap (**not tracked, ~100MB each**) |
