# 16-block benchmark comparison against four passive alternatives (2026-08-06)

**Type:** validation of an existing single-window result. Measurement only —
no mechanism investigated, no strategy change, no adoption decision, nothing
committed or deployed.

**Question:** across 16 independent historical blocks, how does the strategy
compare against four passive alternatives — including two matched to its own
risk profile — and does the single-window 2020-2026 result generalise?

---

## Headline

**Yes, the single-window result generalises, and the strategy does not beat
the simplest passive alternative that delivers its own risk profile.**

1. **Against leg C (drawdown-matched index+cash — the primary result), the
   strategy is a coin flip on block count and loses on aggregate.** It wins
   8/16 blocks on the harshest reasonable treatment and 9/16 on the most
   favourable. Median per-block gap ranges from **+0.09pp to +2.44pp**
   depending on assumptions — a band entirely inside the per-block dispersion
   (sd ≈ 8pp). But its wins are smaller than its losses (mean win +6.12pp vs
   mean loss −7.47pp), so chaining the 16 reset-at-boundary blocks the
   strategy compounds to **7.20%/yr against leg C's 7.54% (C-zero) and 8.09%
   (C-real)**. It is behind on both cash treatments.

2. **The strategy finishes last of all five legs on chained aggregate return:**
   strategy 7.20% < C-zero-tr 7.54% < 60/40 8.00% < C-real-tr 8.09% <
   S&P price-return 8.91% (and the S&P's own total return is higher still).

3. **The 2020-2026 single-window gap is NOT an outlier.** Its −2.99pp CAGR gap
   vs the S&P sits at the **31st percentile** of the 16 per-block gaps (5/16
   blocks are worse), against a per-block median of −0.57pp and mean of
   −2.10pp. The window is a mildly unfavourable draw, not an aberration — the
   underperformance vs the index is typical, not a property of that window.

4. **The strategy's apparent defensiveness is beta reduction, not skill — and
   this is the single most informative result here.** Against the S&P the
   defensive pattern is real: it wins 2/3 falling-market blocks (mean
   +5.20pp) and only 5/13 rising ones (mean −3.78pp). But against a passive
   leg holding *the same drawdown* via cash, the pattern **inverts**: it wins
   only 1/3 falling blocks (mean −1.52 to −3.05pp) and 8/13 rising blocks
   (mean +1.74pp). Cash replicates the downside protection more cheaply than
   the strategy produces it; what edge the strategy has appears in rising
   markets, not falling ones.

**Not softened, and not overstated:** the per-block win rate against leg C is
genuinely 50/50, not a clear loss, and 14 of 16 blocks matched the target
drawdown exactly, so the comparison is a fair one on its own terms. The
aggregate loss is driven by asymmetric tails rather than by consistently
losing. See "Where a different reasonable assumption would change the call"
below — two of the four leg-C variants are close enough that they turn on
stated assumptions.

---

## Verification gate (passed before any block output was trusted)

The cited single-window figures reproduce **exactly**, all six:

| | Strategy | S&P 500 |
|---|---|---|
| CAGR | 10.74% (ref 10.74%) | 13.73% (ref 13.73%) |
| Max DD (peak-relative) | −15.70% (ref −15.7%) | −33.92% (ref −33.92%) |
| Total return | +95.74% (ref +95.74%) | +133.30% (ref +133.3%) |

**One correction to the task spec.** The reference window ends **2026-08-03**,
not 2026-08-06. At an 08-06 end date (two extra sessions in the local DB) the
same code returns strategy 11.26% / +101.93% and S&P 14.03% / +137.47% —
neither matches. 2026-08-03 reproduces all six figures to the stated decimal.
All block work therefore runs 2007-01-01 → **2026-08-03**. Raw output in
`verify_single_window_2026-08-03.json`.

**Decision-engine parity verified with evidence from this run's own trades**
(required by CLAUDE.md's standing rule; `research_parity_check.py`), not
inherited by assumption: all 4,087 trades carry exit reasons only the shared
`evaluate_exit_reason()` chain can emit, `INDEX_REMOVAL` absent; 739 trades
running past the raw 30-calendar-day cap all exited on the first available
trading day against each ticker's own real trading calendar, checked against
an independently recomputed `get_max_holding_days()`; a 400-trade sample were
all genuine index members on their entry date, all had signal ON recomputed
from `signals.compute_signal_series()`, and all cleared the independently
resolved `passes_momentum_floor()` at 0.02; sizing stayed inside the shared
ceiling; and **14.25% of STOP_LOSS exits filled below their stop**, which is
direct proof the corrected 2026-08-06 intraday-touch engine is genuinely
active rather than the old fill-at-stop convention.

*Two checks failed on first run and were traced to bugs in the check itself,
not to engine divergence: `stop_distance_pct` is stored in percent (6.08) not
as a fraction, and the holding cap is evaluated in calendar days but can only
fire on a trading day, so weekend spillover to 32–33 days is correct
behaviour. Recorded because "the check was wrong" is only a safe conclusion
once demonstrated.*

---

## Conventions (stated explicitly, per CLAUDE.md's standing requirement)

- **Reset-at-boundary.** Every block starts fresh at £50,000 with zero
  positions, truncated to its own window. Correct convention for a
  cross-variant comparison; never the continuous walk.
- **Corrected engine.** `simulate_portfolio_compounding_mtm()`, carrying the
  2026-08-06 realistic intraday-touch STOP_LOSS fill as its permanent
  default. Not the frozen exempt engines.
- **Both drawdown conventions reported** for every leg (peak-relative and
  fixed-denominator) in the per-block CSVs.
- **CAGR is annualised over the same block calendar span for every leg,
  strategy included.** This deliberately does *not* use
  `compute_metrics_fixed()`'s own `cagr_pct` for the strategy, which
  annualises over the span of its *trades* rather than the block window —
  against a passive leg held for the whole block that is an
  apples-to-oranges denominator. The production-convention figure is retained
  alongside as `strat_cagr_tradespan` in `block_comparison_wide.csv`.
- **Denomination: all legs unconverted USD**, scaled onto a nominal GBP
  starting capital. Symmetric across legs; no FX conversion applied to any leg.
- **Rebalancing: monthly** (calendar month start) for legs C and D.
- **Chained aggregate** figures compound the 16 reset-at-boundary block
  returns in sequence. That is a derived summary of block results, *not* a
  continuous walk, and is labelled as such wherever quoted.

---

## Leg C — the primary result

Partial index exposure plus cash, weight solved **per block** by bisection on
the actual constructed curve's realised drawdown (never a linear-scaling
shortcut) to reproduce that block's own realised strategy drawdown.

**Match quality: 14/16 blocks matched the target drawdown exactly.** The two
misses are blocks 8 and 15, where the strategy's own drawdown was *deeper*
than 100% index exposure could reach; leg C is capped at w=1.0 there and
achieves a shallower drawdown than the strategy, so in those blocks the
strategy is strictly dominated on both axes rather than merely behind.

| Leg C variant | Wins (of 16) | Median gap | Mean gap | sd |
|---|---|---|---|---|
| C-zero, price return | **9** (56.2%) | +2.44pp | +1.13pp | 7.91 |
| C-zero, total return | **8** (50.0%) | +1.05pp | −0.14pp | 8.26 |
| C-real, price return | **8** (50.0%) | +1.40pp | +0.59pp | 8.01 |
| C-real, total return | **8** (50.0%) | +0.09pp | −0.67pp | 8.34 |

**C-zero vs C-real spread is ~1.0pp of median gap** (2.44 → 1.40 on price
return; 1.05 → 0.09 on total return). The two cash treatments do **not** fall
on opposite sides of the strategy on median — both remain marginally positive
— so the spec's "opposite sides" headline case does not arise. But on
*mean* gap C-zero-tr (−0.14pp) and C-real-tr (−0.67pp) are both negative
while their medians are positive, which is the tail asymmetry that drives the
aggregate result.

**Dispersion — the shape matters more than the count.** The 16 C-real-tr gaps,
sorted: −16.71, −11.09, −9.89, −7.04, −5.60, −3.47, −3.45, −2.48, +2.66,
+2.80, +3.10, +3.50, +6.13, +7.25, +7.54, +15.96. This is not "loses narrowly
in 12 blocks"; it is a near-symmetric 8/8 split with **losses averaging
−7.47pp against wins averaging +6.12pp**. The strategy loses slightly more
when it loses than it gains when it wins, and that gap is what turns a 50/50
block count into an aggregate deficit.

---

## Legs A, B, D — supporting context

| Leg | Wins (of 16) | Median gap | Mean gap |
|---|---|---|---|
| A — S&P 500, price return | 7 (43.8%) | −0.57pp | −2.10pp |
| A — S&P 500, est. total return | 4 (25.0%) | −2.63pp | −4.12pp |
| B — NASDAQ-100, price return | 1 (6.2%) | −7.98pp | −8.64pp |
| B — NASDAQ-100, est. total return | 1 (6.2%) | −8.59pp | −9.57pp |
| D — 60/40, price-return equity sleeve | 6 (37.5%) | −1.39pp | +0.63pp |
| D — 60/40, est. total-return equity sleeve | 6 (37.5%) | −2.73pp | −0.55pp |

Leg B came out as predicted and is reported as required: the strategy is
substantially a tech-momentum vehicle, and against the NASDAQ-100 it wins
**1 block in 16**. The S&P is indeed the flattering large-cap comparator.

**Leg D is the notable one.** A plain monthly-rebalanced 60/40 — requiring no
signal, no universe maintenance, no execution infrastructure — beats the
strategy in 10 of 16 blocks and on chained aggregate (8.00% vs 7.20%). On
*return per unit of drawdown* the strategy does win 9/16 against it, so this
is not a clean sweep, but 60/40 is not a comparator the strategy clears.

On return-per-unit-drawdown the strategy fares better across the board
(A-pr 9/16, D 9/16, C 8–9/16, B 5/16) — its drawdowns genuinely are smaller.
That is the one axis where it consistently holds up, and it is exactly the
axis leg C was built to neutralise.

---

## Where the 2020-2026 window sits

- Single-window gap vs S&P price return: **−2.99pp**
- Per-block gaps vs the same leg: median **−0.57pp**, mean **−2.10pp**,
  sd 7.87, range [−19.91, +13.42]
- **Percentile rank: 31st. 5 of 16 blocks are worse.**

The window is modestly unfavourable but well inside the distribution. The
premise that 2020-2026 is "close to a worst case" for this strategy is **not
supported** — block 11 (2020-06→2021-09, the mega-cap melt-up, strategy
15.16% vs S&P 35.07%, a −19.91pp gap) is the genuine worst case and is far
more extreme than the full-window figure. The single-window result was not
misleading, and treating it as an established finding was justified.

---

## Era decomposition (required)

| Era | Blocks | vs C-zero-tr | median gap | vs A-pr | median gap |
|---|---|---|---|---|---|
| 2007-2009 GFC | 2 | **0/2** | −7.35pp | 1/2 | +1.08pp |
| 2010-2019 bull (**dominant**) | 9 | 5/9 (55.6%) | +2.83pp | 4/9 | −0.95pp |
| 2020-2026 recent | 5 | 3/5 (60.0%) | +2.66pp | 2/5 | −6.73pp |

**The leg C result holds in the dominant era**, at 5/9 with a +2.83pp median —
i.e. the same coin flip seen in aggregate, not an artefact of one atypical
window. It does *not* hold in the GFC era, where the strategy loses both
blocks to leg C by a wide margin (−7.35pp median). That is the same finding
as the regime result below: in the one era where a defensive profile should
have paid, cash-plus-index beat it.

---

## Regime conditioning (reported, not acted on)

Regime defined by the sign of the S&P's own price return over the block
(3 falling blocks: 0, 1, 12; 13 rising).

| Leg | Falling (n=3) | Rising (n=13) |
|---|---|---|
| A — S&P price return | **2/3 (66.7%)**, mean +5.20pp | 5/13 (38.5%), mean −3.78pp |
| B — NASDAQ-100 pr | 1/3, mean +3.98pp | **0/13**, mean −11.56pp |
| C-zero-tr | 1/3, mean −2.17pp | **7/13 (53.8%)**, mean +0.33pp |
| C-real-tr | 1/3, mean −3.05pp | 7/13 (53.8%), mean −0.13pp |
| D — 60/40 tr | 1/3, mean −0.82pp | 5/13 (38.5%), mean −0.49pp |

The defensive pattern trade-level analysis suggested **is real against the
index** — largest outperformance in block 12 (2021-09→2022-11: strategy
+6.41% vs S&P −7.01%, a +13.42pp gap) and block 1 (the GFC acute window,
+2.36pp), largest underperformance in block 11 (the 2021 melt-up, −19.91pp)
and block 8 (2016-17, −14.14pp). So it is systematic, not a calendar-year
artefact.

**But it does not survive drawdown matching.** Against leg C the strategy wins
only 1 of 3 falling blocks and 7 of 13 rising ones — the reverse of its
profile against the index. The downside protection is exposure reduction,
which cash delivers at lower cost.

---

## Measured vs estimated

**Measured:** all strategy figures; S&P and NASDAQ-100 price returns; AGG
total return (`adj_close`, coupons reinvested — price return alone would badly
misstate a bond sleeve); ^IRX cash rates; leg C's solved weights and realised
drawdowns.

**Estimated:** index *total* returns. `^GSPC` and `^NDX` are price-return
series (`close == adj_close` for 100% of stored rows). The total-return
variant is built by scaling each price index by the **realised** dividend
reinvestment factor of its matching ETF — SPY for the S&P, QQQ for the
NASDAQ-100 — i.e. a measured factor rather than an assumed constant yield,
but still an estimate of the *index's* own total return because it is net of
each ETF's expense ratio (SPY ~0.0945%/yr, QQQ ~0.20%/yr) and tracking
difference. It therefore **understates** index total return slightly.

Implied annualised yields actually applied, per era (these are outputs, not
inputs — reported so the assumption is checkable):

| Era | S&P | NASDAQ-100 |
|---|---|---|
| 2007-2009 | 2.05% | 0.34% |
| 2010-2019 | 2.08% | 1.01% |
| 2020-2026 | 1.36% | 0.56% |
| full period mean | 1.85% | 0.79% |

These land squarely on the spec's own expectation (~1.3–1.5%/yr for the S&P
over 2020-2026, higher earlier; a materially lower NASDAQ figure), which is
an independent sanity check on the method.

**Cash series for C-real:** ^IRX, the 13-week US Treasury bill discount rate,
accrued actual/365. Mean 1.53% over the full period (2.36% GFC era, 0.54%
2010-2019, 2.98% 2020-2026).

---

## Caveats that materially affect interpretation

**1. Leg C is a deliberately hard test, not a fair one.** Its weight is solved
ex-post, per block, from that block's own realised strategy drawdown —
information the strategy did not have. A narrow strategy win against leg C is
therefore stronger than it looks, and a narrow loss weaker than it looks. The
result here is neither narrow nor decisive: a 50/50 block count with an
aggregate deficit.

**2. Survivorship bias in the strategy leg, flattering it in early blocks —
pre-existing, not introduced here.** The membership table knows 1,204
historical S&P 500 constituents; `price_data` holds only 509 tickers. Usable
candidate coverage runs from **52.5% of that era's real members in block 1 to
99.4% in block 15** (`universe_coverage_by_block.csv`). The missing names are
largely those that left the index — disproportionately failures. The
benchmark legs use real index series with no such bias. Early-block strategy
figures are therefore optimistic by an unquantified amount, and the true
comparison is likely somewhat worse for the strategy than reported.

**3. Block 0 is momentum-warm-up-crippled.** Price data begins 2006-12-01 and
the signal needs ~12 months, so **zero** tickers have usable momentum at block
0's start; it produced 59 trades against 228–303 in every other block.
Excluding it changes little (leg C win rates move 8→8 and 9→9 of 15; leg A
7/16→7/15), and both scopes are reported in `headline_win_rates.csv`.

**4. Two excluded costs running in opposite directions — neither modelled,
both stated, neither used as a tiebreaker.** FX conversion cost (~1.28pp of
CAGR, full-history) falls almost entirely on the strategy leg, which converts
on every trade where a passive leg converts twice per block — excluding it
**flatters** the strategy. Interest on idle cash (order of 1pp of CAGR) is not
credited to the strategy's ~50% uninvested balance — excluding it **penalises**
the strategy. These roughly offset; net bias is small and its sign uncertain.

**5. The strategy leg is itself price-return.** It trades on `close`;
`adj_close` is read by nothing in the codebase, and CLAUDE.md records the
resulting understatement at 2.32% of returns on the full continuous-walk run.
So strategy-vs-benchmark-total-return is the harshest treatment, and
strategy-vs-benchmark-price-return is closer to symmetric — though not exactly,
since the strategy is only ~50% deployed and so forgoes roughly half the
dividend a fully-invested leg does. Leg C at w≈0.5 is the most nearly
symmetric comparison in the whole set on this axis, which is a point in its
favour as the primary comparator.

### Where a different reasonable assumption would change the call

- **C-real-tr median gap is +0.09pp.** Any of: crediting the strategy's idle
  cash, a slightly different cash series, or quarterly instead of monthly
  rebalancing could flip its sign. Treat "the strategy edges leg C on median"
  as unsupported; "it is indistinguishable from leg C on median" is the
  defensible statement.
- **The aggregate ordering is more robust than the per-block one.** The
  strategy trails leg C by 0.34pp (C-zero) to 0.89pp (C-real) of chained CAGR.
  The C-real figure would survive most reasonable perturbations; the C-zero
  figure is close enough that it should not be leaned on alone.
- **Correcting survivorship bias would move every result against the
  strategy**, and is the largest unquantified term here.

---

## Files

| File | Contents |
|---|---|
| `block_comparison_wide.csv` | One row per block, all legs side by side; leg C weights, realised drawdowns and match flags; implied dividend yields |
| `per_block_per_leg.csv` | Long form: one row per block per leg — CAGR, total return, both drawdown conventions, return per unit drawdown |
| `headline_win_rates.csv` | Win rates + gap distribution per leg, both scopes (all 16, ex-block-0) |
| `era_decomposition.csv` | Per-era win rates and median/mean gaps per leg |
| `regime_conditioning.csv` | Rising/falling benchmark split per leg |
| `block_strategy_trades.csv` | **All 4,087 trades**, block-tagged — full trade-level output preserved per CLAUDE.md's standing requirement |
| `block_results_raw.json` | Complete raw result objects |
| `universe_coverage_by_block.csv` | Candidate-universe price-data coverage per block (the survivorship caveat, quantified) |
| `verify_single_window_2026-08-03.json` | Reproduction-gate output |
| `research_*.py` | The four scripts: single-window verification, block harness, parity check, analysis |

Run locally in an isolated `git worktree` against a copied production
database, per CLAUDE.md's standing practice. Nothing committed, nothing
pushed, no production file modified. `^NDX`, `AGG`, `^IRX`, `SPY` and `QQQ`
were ingested into the **copied** database via the real production path
(`data/ingestion.py:pull_history`).
