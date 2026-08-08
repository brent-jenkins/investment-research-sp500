# Should the momentum signal read `adj_close` instead of `close`?

**Verdict: no decisive winner. The mechanism is real and confirmed working exactly as theorized —
adj_close-based momentum does preferentially admit dividend-paying names — but the aggregate
economic effect is small, inconsistent in direction across pools, and does not clear the
pre-registered bar in any pool.** Not adopted. Full trade-level output preserved for all 6 runs.

## Pre-registered criterion (recap)

Arm B (adj_close) wins decisively only if ALL of: (1) aggregate cost-adjusted CAGR advantage
≥ +0.50pp, (2) same-direction win in ≥12/16 blocks, (3) peak DD for the winning arm no more than
3pp worse, (4) holds independently in both Pool 1 and Pool 2-CLEAN. Block count itself is
explicitly not decisive (±2-block noise floor).

## Pre-run checks (all passed, before any full backtest was trusted)

- **Coverage**: `adj_close` has 0 NULLs across 3,462,643 `price_data` rows, all 871 tickers. No
  confound.
- **Positive control (KDP/MO/WY)**: close-based momentum stayed deeply negative through each
  ticker's affected window (KDP 8/260 positive days, MO 0/260, WY 8/260); adj_close-based momentum
  was materially different and far less distorted (KDP 260/260 positive, MO 106/260, WY 260/260).
  Wiring confirmed correct before anything else was trusted.
- **Decision-engine parity spot-check**: 8 sampled Arm-B-only trades (Pool 2 CLEAN) all priced at
  exactly the cached `close` value (never `adj_close`) — the one non-negotiable assertion. Every
  admission difference was explained: 6/8 by higher `momentum_adj` at entry (consistent with the
  dividend hypothesis — the ticker won a capacity slot it would have lost under close-ranking), 2/8
  by unchanged momentum but a capacity-cascade reshuffle elsewhere (the same mechanism already
  documented in this project's other pool-perturbation findings).

## Admission overlap — reported prominently, per instruction

| Pool | Arm A trades | Arm B trades | Shared positions | Overlap (% of A) | Jaccard |
|---|---|---|---|---|---|
| Pool 1 | 4,749 | 4,800 | 1,531 | 32.2% | 19.1% |
| Pool 2 CLEAN | 4,482 | 4,485 | 1,485 | 33.1% | 19.8% |
| Pool 2 all | 4,922 | 4,997 | 1,649 | 33.5% | 19.9% |

**Overlap is low (~33%, Jaccard ~20%) in every pool — the two arms build substantially different
portfolios, not nearly-identical ones.** This rules out "any CAGR difference is just a handful of
names" as an explanation either way. But it cuts the other direction from a simple "therefore the
effect is real and large": with only ~1/3 of positions shared, most of what differs between arms is
not a direct dividend-driven admission swap but the **capacity-cascade mechanism** already
documented elsewhere in this project (change one admission, and momentum-ranked, capacity-
constrained sequential admission reshuffles a large fraction of everything downstream). The parity
spot-check's own 2/8 cascade-only cases are a direct, small-sample illustration of exactly this.
Any headline CAGR delta should be read as "signal change plus a large amount of cascade
amplification," not a clean per-trade dividend effect.

## Direction check — sector tilt (cheap, and the mechanism confirms cleanly)

Reused `research/entry_volume_sector_2026-08-07/sector_map.csv` (prior study's cached sector data)
rather than re-fetching. Mean (trades under B − trades under A) per ticker, dividend-heavy sectors
(Utilities/Consumer Defensive/Real Estate) vs. all others:

| Pool | Dividend-sector mean delta | Other-sector mean delta | Direction |
|---|---|---|---|
| Pool 1 | +0.72 (n=68) | +0.01 (n=314) | **Confirms** |
| Pool 2 CLEAN | +3.56 (n=27) | −0.61 (n=77) | **Confirms** |
| Pool 2 all | +0.40 (n=68) | +0.13 (n=303) | **Confirms** |

All three pools tilt the predicted direction — Utilities, Real Estate, and Consumer Defensive
consistently show more admissions under `adj_close`; Technology and Communication Services
consistently show fewer. Pool 2 CLEAN's tilt is the strongest and cleanest (though only 51% sector
coverage there — 101/205 traded tickers unmapped, a real gap, noted honestly rather than
smoothed over). **This is a clean, triangulated confirmation that the mechanism works exactly as
the theory predicts** — the open question is only whether the resulting economic effect is large
enough and consistent enough to adopt, which the next section answers no to.

## Headline numbers, both conventions

Reset-at-boundary is the correct convention for this arm-vs-arm comparison (continuous walk
reported alongside for context only, per standing requirement to state both).

### Continuous walk (context only)

| Pool | Arm | Trades | Raw CAGR | Cost-adj. CAGR | Peak DD | Blocks pass |
|---|---|---|---|---|---|---|
| Pool 1 | A (close) | 4,749 | 9.38% | 6.51% | −41.94% | 7/16 |
| Pool 1 | B (adj_close) | 4,800 | 8.18% | 5.30% | −44.84% | 8/16 |
| Pool 2 CLEAN | A | 4,482 | −0.47% | −3.22% | −35.07% | 7/16 |
| Pool 2 CLEAN | B | 4,485 | −0.03% | −2.79% | −30.54% | 7/16 |
| Pool 2 all | A | 4,922 | 6.28% | 3.38% | −47.83% | 7/16 |
| Pool 2 all | B | 4,997 | 6.18% | 3.22% | −52.20% | 9/16 |

### Decisive-criterion evaluation (reset-at-boundary, per pre-registration)

| Criterion | Pool 1 | Pool 2 CLEAN (primary) | Pool 2 all (secondary) |
|---|---|---|---|
| 1. Cost-adj. CAGR advantage ≥ +0.50pp | **−1.21pp — FAIL** (A wins) | **+0.43pp — FAIL** (below floor) | **−0.16pp — FAIL** (A wins) |
| 2. Same-direction win ≥12/16 blocks | **5/16 — FAIL** | **8/16 — FAIL** | **7/16 — FAIL** |
| 3. Winning-arm peak DD ≤3pp worse | B worse by 2.90pp (passes, moot) | B *better* by 4.53pp (passes, moot) | B worse by 4.37pp (fails, moot) |
| 4. Holds independently, both pools | **FAIL** — Pool 1 and Pool 2 CLEAN disagree on direction | | |

**No criterion is met in any pool. Criterion 4 fails outright on its own** — Pool 1 favors Arm A
(−1.21pp) while Pool 2 CLEAN favors Arm B (+0.43pp, itself below the decisive floor). This is not a
borderline pass being denied on a technicality: even the single best number across all three pools
(Pool 2 CLEAN's +0.43pp) falls short of the pre-registered +0.50pp floor, and no pool comes close to
the 12/16 block-concordance bar (best is 8/16, a coin flip).

## Capacity-turnover (per standing requirement, reported separately)

| Pool | Arm A trades | Arm B trades | Net delta |
|---|---|---|---|
| Pool 1 | 4,749 | 4,800 | +51 |
| Pool 2 CLEAN | 4,482 | 4,485 | +3 |
| Pool 2 all | 4,922 | 4,997 | +75 |

Small net deltas in every pool, consistent with a genuine but modest shift in admission mix rather
than a wholesale change in trading frequency — the large *gross* churn (~67-68% of positions differ,
per the admission-overlap table) is a reshuffling of *which* names get in, not a change in *how
many* trades happen overall.

## Reading this result

The mechanism is not in question — three independent, cheap checks (positive control, parity
spot-check, sector tilt) all confirm `adj_close`-based momentum does exactly what the theory
predicts: it corrects the dividend-exclusion bias and preferentially admits dividend-paying names,
with utilities/staples/REITs showing the clearest tilt in every pool tested. What is in question is
whether that correction is large enough, net of its side effects (the capacity-cascade
amplification visible in the low ~33% admission overlap), to net out as a decisive economic
improvement — and on this pool, this cap, and this cost model, it is not. Pool 1 and Pool 2 CLEAN
even disagree on which arm wins after costs, which is itself informative: whatever effect exists is
smaller than the noise introduced by which survivorship/quarantine population is used to measure it.

**Not adopted. `analysis/signals.py` untouched.** This does not retire the underlying dividend-
exclusion concern (still a real, documented bias in `close`-based momentum) — it establishes that
the specific fix tested here (swap the signal input wholesale to `adj_close`) is not a clear win at
current settings, not that the bias doesn't matter at all. A future revisit, if any, would need
either a larger effect elsewhere (a different cap, a different cost regime) or a source of a
decisive result other than this specific swap.

## What was NOT done, per explicit scope

`signals.py`, `config.yaml`, the stop-loss work, the quarantine work, and the earnings dampener were
not touched. No production change, no config change, no deployment.

## Trade-level output preserved

`trades_{pool1,pool2_clean,pool2_all}_{A,B}.csv` (6 files), `step5_admission_overlap_*.json`,
`step6_sector_tilt_*.csv`, `step7_block_validation_results.json`/`step7_summary.csv`,
`step8_cost_adjustment.json` — all kept alongside this report per standing requirement.
