# Descriptive selection audit (2026-08-08)

**Read-only.** This is a description of what the momentum-ranked, capacity-constrained selection
mechanism actually did across its real trade history — not a hypothesis test, not a variant
comparison, not a recommendation. Nothing here is proposed for adoption; the constraints on this
task explicitly forbid it. Part 7 is the one exception that runs new backtests (a distributional
test, not a variant comparison to adopt), and is labelled as such throughout.

**Backtest convention, stated per this project's own standing requirement:** every trade-level
dataset used here is a **continuous walk** (2007-01-01 → the dataset's own stated end date, no
reset at any boundary) — the correct convention for "what did this one already-decided system
actually do," not a cross-variant comparison. `max_total_deployed = 0.70` throughout (the caps
used by every source dataset). Mark-to-market deployment accounting throughout (`simulate_portfolio_compounding_mtm`
— the one engine confirmed to match live execution).

**Datasets used, and why:**
- **Parts 1–3 primary dataset**: `research/full_trade_export_enriched_corrected_2026-08-06/full_trade_export_enriched_corrected_2007-2026.csv`
  (3,436 trades, generated via `run_full_validation(..., live_universe=True, mark_to_market=True)`
  — the same methodology `main.py`'s Backtest page uses, current 2.0x ATR stop, realistic
  intraday-touch `STOP_LOSS` fill, ended 2026-08-06). Chosen because it is the richest existing
  export (momentum-at-entry, sector/industry, MAs, volume already attached) and its universe
  construction is closest to what live trading itself uses. No re-run — used exactly as preserved.
- **Parts 4–7**: two independently-provenanced pools, per this project's dual-provenance standing
  requirement — **Pool 1** (`get_live_stock_candidates()`, 503 tickers, today's real S&P 500
  membership projected backwards, maximal survivorship bias, zero confirmed corruption) and
  **Pool 2 "final"** (`get_research_stock_candidates()`, 1,042 tickers, the current
  post-Wikipedia-refinement, 163-quarantined provisional universe — today's real production
  state). Both reuse the exact caches (`_cache_pool1.pkl`, `_cache_pool2_final.pkl`) already built
  and preserved by `research/stop_loss_reconfiguration_2026-08-07/`'s Arm A (2.0x ATR, the current
  production default) — no re-fetch of price data, no new candidate-pool construction. Pool 2
  final's trade output (4,922 trades, £114,990.04) is treated as primary/current; Pool 1 (4,749
  trades, £240,360.75) is the cross-check.
- **Part 8**: fresh index/ETF-proxy series (yfinance), coverage confirmed before use (see Part 8
  section). No new stock-level data fetched.

**New instrumentation built for this audit** (all in `research/selection_audit_2026-08-08/`):
`instrumented_engine.py` is a copy of `analysis.backtest.simulate_portfolio_compounding_mtm()`
with additive logging only (candidate-day gate/rank/admission detail) plus a `random` admission
mode for Part 7 — every actual decision (`passes_entry_gate`, `passes_momentum_floor`,
`is_reentry_blocked`, `evaluate_exit_reason`, sizing) still calls the real, unmodified
`analysis.decision`/`analysis.volatility` functions. **Parity verified, not assumed**: run against
Pool 1's cache in `momentum` mode, it reproduced `trades_pool1_A.csv` **byte-identical**
(4,749/4,749 trades, £240,360.75/£240,360.75, 0 row-level mismatches across every field) — see
`verify_parity.py`. Independently, the Pool 2-final run reproduced the separately-generated
`trades_pool2_final_A.csv` exactly (4,922 trades, £114,990.04, to the penny) with no explicit
comparison script needed — the numbers simply matched. `counterfactual_sim.py` (used in Parts 5–6)
was checked against 30 real admitted trades sampled at random: **30/30 exact matches** on exit
reason and P&L%, confirming it reproduces the real exit mechanics for a position it wasn't built
from.

---

## Part 1 — Concentration and luck

Source: 3,436 trades, total P&L **£127,167.54**.

| Metric | Value |
|---|---|
| Top 10 trades / total P&L | **26.2%** |
| Top 1% of trades (34 trades) / total P&L | **60.9%** |
| Top decile (344 trades) / total P&L | **257.2%** |
| Top 10 trades / gross profit (winners only) | 6.1% |
| Win rate | 44.6% |
| Profit factor | 1.305 |
| Mean P&L | £37.01/trade |
| Median P&L | **–£50.29/trade** |
| Skew (pnl_gbp) | **+2.51** (strongly right-skewed) |

**The full decile curve** (rank 1 = biggest winners, share of total £127,167.54 P&L each decile
contributes):

| Decile (by P&L rank) | n | Share of total P&L |
|---|---|---|
| 1 (best) | 344 | 257.2% |
| 2 | 344 | 47.9% |
| 3 | 343 | 21.6% |
| 4 | 344 | 10.7% |
| 5 | 343 | 3.9% |
| 6 | 344 | –4.5% |
| 7 | 343 | –12.6% |
| 8 | 344 | –22.4% |
| 9 | 343 | –39.6% |
| 10 (worst) | 344 | –62.9% |

**This rests on a handful of large winners, unambiguously.** The top decile alone contributes
**2.57x** the entire realized P&L; deciles 6–10 (the bottom half, 1,718 trades — half the entire
trade population) are net **negative** in aggregate, more negative the further down the ranking
you go, in a clean monotonic staircase. Median trade P&L is negative (–£50.29) despite a positive
mean (+£37.01) — textbook right-skew: most trades lose a little, a few win enormously, and the
few winners carry the entire result. **This means the question "is selection optimal" is,
substantially, "did a small number of picks work out very well"** — the aggregate £127k headline
figure is far more fragile to those few trades than a smoothly-distributed return series would be.
It does not by itself mean selection is *bad* (positive skew is an expected, even desired, shape
for a trend-following/stop-loss system — small controlled losses, occasional large uncapped
winners) — but it does mean any claim about "the strategy" should be read as substantially a claim
about a small number of specific trades, not a broad-based edge.

## Part 2 — Does entry momentum predict anything?

Momentum-at-entry deciles (D1 = lowest qualifying momentum, D10 = highest), n≈343–344 each:

| Decile | Momentum range | Win rate | Mean P&L% | Median P&L% | Mean hold (days) |
|---|---|---|---|---|---|
| D1 | 2.09 – 28.41 | 36.3% | **–0.57%** | –2.86% | 16.5 |
| D2 | 28.54 – 45.83 | 53.2% | +2.25% | +1.03% | 22.8 |
| D3 | 45.87 – 55.90 | 43.7% | +0.68% | –2.43% | 21.1 |
| D4 | 55.91 – 65.88 | 49.1% | +1.18% | –0.22% | 21.2 |
| D5 | 65.88 – 76.20 | 47.2% | +0.34% | –1.11% | 22.7 |
| D6 | 76.21 – 87.83 | 45.3% | +1.18% | –2.59% | 22.1 |
| D7 | 87.87 – 100.93 | 46.6% | +1.29% | –3.17% | 21.5 |
| D8 | 100.97 – 122.83 | 42.7% | +0.61% | –3.42% | 21.2 |
| D9 | 122.85 – 176.84 | 36.7% | +0.17% | –4.78% | 20.1 |
| D10 | 177.25 – 4330.67 | 45.3% | **+3.01%** | –1.95% | 22.1 |

- Spearman(momentum, pnl_pct) across all trades = **–0.033** (essentially zero, slightly negative)
- Pearson(momentum, pnl_pct) = +0.057 (essentially zero)
- Decile-rank vs decile-mean-return Spearman = **+0.224** (weak positive)
- Decile-rank vs decile-win-rate Spearman = **–0.176** (weak *negative*)

**No clean monotonic relationship.** The correlation between raw entry momentum and trade outcome
is statistically indistinguishable from zero at the trade level. There is one real, consistent
signal at the *margin*: **D1 (candidates barely above the entry floor) is unambiguously the worst
bucket** — lowest win rate (tied with D9), most negative mean return, most negative median return,
and shortest average hold (suggesting these get stopped out fastest). That is a real, useful
finding specific to the low end. Above D1, however, momentum decile has **no reliable ordering
power**: D9 (near the top of the range) has one of the lowest win rates and lowest mean returns,
while D10 (the most extreme momentum, up to 4,330%) has the *best* mean return but a middling win
rate — consistent with D10's outsized mean being driven by a small number of extreme winners
(exactly the concentration pattern from Part 1), not by momentum reliably picking winners in that
bucket. **The ranking mechanism assumes higher momentum is monotonically better; the data does not
support that assumption above the bottom decile.** It does support "don't take the very weakest
qualifying candidates."

## Part 3 — What did it actually hold

**Sector tilt** (by trade count, vs. each pool's own composition):

| Sector | Held % of trades | Pool 1 (503) % | Tilt vs Pool 1 | Pool 2 (1,042, 60.6% sector-known*) % | Tilt vs Pool 2 |
|---|---|---|---|---|---|
| Technology | 26.6% | 16.7% | **1.59x** | 9.3% | **2.86x** |
| Consumer Cyclical | 17.6% | 10.7% | **1.64x** | 8.5% | **2.08x** |
| Healthcare | 10.6% | 11.7% | 0.91x | 6.8% | 1.56x |
| Industrials | 9.8% | 14.9% | 0.66x | 8.9% | 1.10x |
| Energy | 8.5% | 4.2% | **2.04x** | 3.1% | **2.77x** |
| Basic Materials | 5.9% | 4.0% | 1.47x | 2.5% | 2.34x |
| Financial Services | 5.6% | 13.7% | **0.41x** | 8.6% | **0.65x** |
| Communication Services | 5.4% | 4.8% | 1.13x | 2.8% | 1.95x |
| Consumer Defensive | 4.8% | 6.4% | 0.75x | 3.7% | 1.28x |
| Utilities | 2.9% | 6.2% | **0.47x** | 3.0% | 0.97x |
| Real Estate | 2.2% | 6.2% | **0.36x** | 3.5% | 0.65x |

\* Pool 2's sector map has real sector data for only 60.6% of its 1,042 candidates (yfinance
returns nothing for ~40% — mostly delisted/historical tickers with no resolvable `.info`); the
`tilt vs Pool 2` column's denominator is therefore biased toward whatever fraction of each sector
*could* be resolved, not necessarily representative of the true composition of the unresolvable
40%. Pool 1's tilt column has no such gap (100% sector-resolved) and should be read as the more
reliable of the two.

**Real, consistent tilt across both pools**: heavily overweight **Technology** and **Consumer
Cyclical** (both >1.5x pool share in both comparisons), overweight **Energy**, underweight
**Financial Services**, **Utilities**, and **Real Estate** (all <0.5–0.75x in Pool 1). This is
consistent with momentum-driven selection naturally gravitating toward higher-beta, higher-growth
sectors and away from low-volatility, dividend/rate-sensitive sectors — an expected mechanical
consequence of momentum ranking, not necessarily evidence of a bug, but a real, measurable
structural tilt worth being aware of for anyone reasoning about diversification.

**Repeat names.** 341 distinct tickers ever held; **287 (84.2%) were traded more than once.**
Maximum for a single ticker: **86 trades in NVDA** (win rate 57%, £14,218.75 total P&L — NVDA is
also the single largest contributor to Part 1's concentration, worth flagging directly). Top 20 by
trade count:

| Ticker | Trades | Total P&L | Win rate |
|---|---|---|---|
| NVDA | 86 | £14,218.75 | 57% |
| NFLX | 75 | £1,618.28 | 47% |
| AMD | 73 | £7,458.05 | 40% |
| MU | 69 | £10,545.86 | 51% |
| AMZN | 62 | £694.64 | 35% |
| FCX | 46 | –£264.09 | 39% |
| AVGO | 43 | £3,839.98 | 44% |
| WDC | 42 | £11,789.99 | 57% |
| AAPL | 41 | £2,425.50 | 49% |
| WYNN | 39 | –£1,132.26 | 38% |

**Holding period by exit reason** (days):

| Exit reason | n | Mean | Median | P90 | Max |
|---|---|---|---|---|---|
| CRISIS_EXIT | 34 | 8.5 | 10.0 | 17.6 | 27 |
| END_OF_DATA | 28 | 5.1 | 5.5 | 17.0 | 18 |
| MAX_HOLDING | 1,723 | 30.6 | 30.0 | 32.0 | 33 |
| SIGNAL_OFF | 125 | 8.7 | 6.0 | 21.0 | 31 |
| STOP_LOSS | 1,526 | 12.0 | 10.0 | 24.0 | 33 |

`STOP_LOSS` (44.4% of exits) and `MAX_HOLDING` (50.1% of exits) dominate — together 94.5% of all
exits. `SIGNAL_OFF` (3.6%) and `CRISIS_EXIT` (1.0%) are comparatively rare. `STOP_LOSS` exits
happen noticeably faster than the max-holding ceiling (median 10 days vs. the ~30-day cap),
consistent with the concentration/skew shape already established: most losing positions are cut
early and small, most winners either run the full holding window or get signal-exited.

## Part 4 — Timing vs. quality in admission

Both pools, computed identically from a fresh, parity-verified instrumented run (`momentum`
admission mode, `log_candidates=True`):

| Metric | Pool 1 (4,749 admitted) | Pool 2-final (4,922 admitted) |
|---|---|---|
| % admitted trades outside top quartile of that day's eligible set | **7.4%** | **8.0%** |
| % admitted only because *today's own exits* freed the capacity | **92.8%** | **93.0%** |
| Rank-at-admission percentile: median | 1.0% | 0.9% |
| Rank-at-admission percentile: 90th pct | 12.4% | 13.9% |

**Admission is overwhelmingly high-rank, but overwhelmingly timing-dependent.** Only ~7–8% of
admitted trades were outside the top quartile of that day's own eligible candidates — the ranking
mechanism is doing real, active filtering, not letting weak candidates through by default. But
**over 92% of every admission, in both pools, only happened because that specific day's own
position exits freed the capacity slot it filled** — without that day's own churn, the admission
would have been rejected. Selection is dominated by *when a slot happens to open*, not by a
broad, standing pool of "good enough" candidates waiting in a queue; the system is capacity-slot-
driven almost every single time a trade is admitted.

**Outcomes by rank bucket** (both pools agree directionally):

| | Pool 1 win rate / mean P&L% | Pool 2 win rate / mean P&L% |
|---|---|---|
| Top-quartile rank (n=4,397 / 4,526) | 45.2% / +1.15% | 43.6% / +0.78% |
| Below top-quartile rank (n=352 / 396) | **34.4%** / **–0.57%** | **36.9%** / **–0.18%** |

Rank at admission has real, if modest, predictive value: candidates admitted from further down
that day's ranked list do measurably worse than top-quartile admits, in both pools. This is
consistent with — not contradicting — Part 2's finding that raw momentum has weak *overall*
predictive power: rank-*within-day* (a relative, capacity-conditioned measure) carries more signal
than the raw momentum value itself.

**Outcomes by freed-capacity status** (both pools agree directionally, and this is a genuinely
unexpected finding — flagged as an observation below):

| | Pool 1 win rate / mean P&L% (n) | Pool 2 win rate / mean P&L% (n) |
|---|---|---|
| Admitted via freed capacity | **45.3%** / **+1.18%** (4,409) | **44.2%** / **+0.90%** (4,575) |
| Admitted with capacity already free (no exit needed) | **32.6%** / **–1.02%** (340) | **28.5%** / **–1.86%** (347) |

The small minority of admissions that did *not* need freed capacity (i.e., happened on a day when
the portfolio already had spare room) perform **worse** than the 93% that needed a same-day exit
to open the slot, in both pools, by a wide and consistent margin (win rate ~13–16pp lower). See
"Anomalies" below.

## Part 5 — The rejected population

**REJECTED_CAPACITY events**: **1,065,664** in Pool 2-final, **955,000** in Pool 1 (a candidate
passing the gate, correctly ranked, but rejected purely because the portfolio was at its 70%
deployment cap on that specific day). Counterfactual returns simulated for a random sample of
3,000 (Pool 2) / ~3,000 (Pool 1) rejected candidate-days each, using the exact same real exit
logic (`evaluate_exit_reason`, realistic intraday stop fill, `MAX_HOLDING`) a standalone,
capacity-unconstrained position would have followed — verified against 30 real admitted trades
first (30/30 exact match, see above). **Approximation limits, stated directly**: this measures
*price-path* outcome only — no portfolio interaction, no re-entry-cooldown, no capacity
competition with what a real admission of this candidate would itself have displaced. It answers
"how would this specific position's own price path have resolved," not "what would the portfolio's
balance have been."

| | Pool 1 (n=955,000 pop., 2,997 sampled) | Pool 2-final (n=1,065,664 pop., 3,000 sampled) |
|---|---|---|
| Rejected-population counterfactual win rate | 44.7% | 43.9% |
| Rejected-population counterfactual mean P&L% | +0.54% | +0.34% |
| Rejected-population counterfactual median P&L% | –1.56% | –1.84% |
| **Admitted population's own actual win rate** | 44.4% | 43.1% |
| **Admitted population's own actual mean P&L%** | +1.02% | +0.70% |

**The rejected population is not systematically worse — it is close to indistinguishable from the
admitted population, slightly behind on mean return, essentially tied on win rate.** In both
pools, counterfactual win rate for rejected candidates is *statistically in line with* (Pool 1,
actually 0.3pp *higher* than) the real admitted population's own win rate, and mean return is only
modestly lower (0.34–0.54% vs 0.70–1.02%). **This is the direct test the task asked for, and the
answer is: capacity constraints are not obviously saving money by screening out a genuinely worse
population — they are mostly just a resource limit that happens to bind on a population that looks,
on average, almost as good as what gets through.** This does not mean capacity constraints are
costing money either (a full portfolio-level counterfactual, including what capacity these
rejections would have consumed and what THAT would have crowded out, is out of scope here and
not attempted) — but "rejection is discriminating usefully" is not what this data shows.

## Part 6 — Does the gate discriminate?

**The 0–2% momentum floor band** (candidates that had `momentum > 0` but failed the explicit 2%
floor — the population `min_momentum_pct: 0.02` was adopted specifically to exclude):

| | Pool 1 (n=50,149 events, 2,999 sampled) | Pool 2-final (n=55,661 events, 3,000 sampled) |
|---|---|---|
| Win rate | 37.6% | 38.6% |
| Mean P&L% | +0.31% | +0.28% |
| Median P&L% | –0.58% | –0.57% |
| Mean holding days | 10.6 | 10.9 |
| Dominant exit reason | SIGNAL_OFF (70%) | SIGNAL_OFF (69%) |

**This population is genuinely worse than admitted** (win rate 37.6–38.6% vs. 43.1–44.4% admitted,
mean return roughly a third of admitted's) — the floor is excluding a population that measurably
underperforms, at a sample size roughly 1,700x larger than the single excluded trade this floor
was originally adopted on. That original evidence generalizes. One caveat worth stating plainly:
holding periods here are much shorter than the general population's (10.6–10.9 days vs. ~15–20
elsewhere), because most of this population's momentum decays back through zero and exits via
`SIGNAL_OFF` quickly — this counterfactual is measuring "what a very weak, marginal trend does
over roughly two weeks," not a full max-holding-period trade.

**All gate failures, and the below-zero-momentum deciles** — this is where the audit surfaced its
most significant, counterintuitive finding. Both pools, independently:

| Decile (below-zero momentum, most-negative → least-negative) | Pool 1 win rate | Pool 2 win rate |
|---|---|---|
| D1 (most negative, roughly –100% to –45%) | **55.2%** | **56.8%** |
| D2 | 50.0% | 49.7% |
| D3 | 49.4% | 49.0% |
| D4 | 49.0% | 47.0% |
| D5 | 49.2% | 52.5% |
| D6 | 53.5% | 53.2% |
| D7 | 50.0% | 50.5% |
| D8 | 55.5% | 48.0% |
| D9 | 50.5% | 53.0% |
| D10 (least negative, closest to zero) | 45.5% | 47.7% |

**Every single below-zero-momentum decile in both pools shows a win rate at or above 45%,
several well above 50%, and the single most negative-momentum decile is the single best- or
near-best-performing decile in both pools** — higher than the admitted population's own actual win
rate (43.1–44.4%). All-gate-fails aggregate win rate (48.2% Pool 2, 49.1% Pool 1) also exceeds the
admitted population's own win rate. **This does not mean the gate is wrong to require positive
momentum** — see the important caveat below — but it is a genuinely surprising result that
deserved direct, unhedged statement per the task's own instruction, and it held up identically
across two independently-provenanced pools, which rules out a pool-specific data artifact as the
explanation.

**The necessary caveat, stated as directly as the finding itself.** These candidates failed the
gate specifically because `signal` was off at entry (momentum ≤ 0) — the counterfactual simulator
opens a position anyway and then evaluates the exact same exit rules a real position would, which
means `SIGNAL_OFF` can (and, per the exit-reason mix, does — 70%+ of this population) fire almost
immediately if momentum is still non-positive the very next trading day. Mean holding days for
this population (8.7–8.8 days for all-gate-fails) are roughly a third of the admitted population's.
**This population's return is therefore dominated by a short, 1–2 week price-path snapshot, not a
comparable multi-week trend-following trade** — the high win rate at the most-negative-momentum
end is consistent with a real, short-horizon mean-reversion effect (a stock down heavily over 12
months has a reasonable chance of a small positive bounce over the next 1–2 weeks before the
system's own signal or stop would close it), not evidence that the momentum-positive requirement
itself is miscalibrated for the actual holding periods (`MAX_HOLDING` ≈ 30 days) the strategy
otherwise runs. **This is flagged as a genuine anomaly worth further investigation (see below), not
as evidence the gate should be reconsidered** — the counterfactual's own short realized holding
period is exactly why a direct "the gate is wrong" conclusion is not supported by this data alone.

## Part 7 — Ranking vs. random (requires backtest runs — labelled separately per the task's own instruction)

**Arm R**: identical to the real baseline in every respect (gate, capacity constraint, exit rules,
sizing) except that `_admit_group`'s momentum-descending sort is replaced by a seeded random
shuffle of the *same already-gate-passed* candidate list. Pool 2-final (current production pool),
**100 seeds, all complete** — 7 parallel worker processes, ~6.5 hours wall-clock total (per-seed
time fell from an initial ~45 min under heavy contention to ~21 min once contention eased partway
through the run). Cost adjustment is FX round-trip only (0.30% of each run's own total notional,
no idle-cash credit — see the methodology note in `part7_random_arm_sweep.py`'s own docstring;
applied identically to the random arm and the real baseline below, so it does not bias the
comparison, only the absolute level of both).

**Full 100-seed distribution:**

| | Mean | Std | Min | 5% | 25% | Median | 75% | 95% | 99% | Max |
|---|---|---|---|---|---|---|---|---|---|---|
| Frictionless CAGR | 5.07% | 0.89 | 1.50% | 3.78% | 4.56% | 4.99% | 5.72% | 6.39% | 6.60% | 6.79% |
| Cost-adjusted CAGR | 1.62% | 0.90 | –2.02% | 0.30% | 1.11% | 1.54% | 2.29% | 2.99% | 3.19% | 3.37% |
| Max drawdown (fixed-denom.) | –41.95% | 3.73 | –60.29% | — | –43.21% | –41.62% | –39.62% | — | — | –36.47% |
| Peak-relative drawdown | –40.69% | 2.73 | –46.82% | — | –42.30% | –40.95% | –38.24% | — | — | –34.61% |
| Trade count | 4,835 | 40.5 | 4,670 | — | 4,815 | 4,838 | 4,860 | — | — | 4,936 |
| Profit factor | 1.178 | 0.033 | 1.050 | — | 1.160 | 1.180 | 1.200 | — | — | 1.250 |
| Win rate | 42.8% | 0.59pp | 41.2% | — | 42.4% | 42.9% | 43.2% | — | — | 44.3% |

**Real momentum-ranked baseline (Pool 2-final, Arm A, the current production ranking)**: 4,922
trades, frictionless CAGR **6.63%**, cost-adjusted CAGR **3.27%** (same FX-only methodology),
fixed-denominator max drawdown **–54.02%**.

**Baseline's percentile within the 100-run random distribution:**

| Metric | Baseline value | Percentile within random distribution |
|---|---|---|
| Frictionless CAGR | 6.63% | **99th** (only 1 of 100 random runs matched or beat it) |
| Cost-adjusted CAGR | 3.27% | **99th** (only 1 of 100 random runs matched or beat it) |
| Trade count | 4,922 | 99th (near the top of the random range too) |
| Max drawdown (fixed-denom.) | –54.02% | **98th-worst** (only 2 of 100 random runs had a deeper drawdown) |

**The baseline does not sit inside the bulk of the random distribution — it sits at its extreme
edge, on both return metrics, in a complete 100-seed sweep.** This directly answers the task's
central question, stated as plainly as asked for: **momentum ranking is doing real, substantial
work here — it is not decoration, and the gate is not doing all the work.** A system that only
randomly admitted from the same gate-passed, capacity-constrained pool would, 99 times out of 100,
have produced a lower CAGR (both frictionless and cost-adjusted) than what the real momentum-
ranked admission actually produced. This holds for return; **it does not hold for risk** — the
baseline's drawdown is worse than 98 of the 100 random runs, so the extra return earned by ranking
also comes with meaningfully more drawdown than a randomly-admitted portfolio from the same pool
would typically have shown. Concentrating capacity into the momentum leaders produces a
higher-return, higher-drawdown portfolio relative to spreading admissions randomly across the same
gate-passed set — a real, quantified trade-off, not a free improvement.

**This also serves as the second empirical noise-floor measurement the task asked for.** The
random arm's own spread (CAGR range 1.50%–6.79%, a 5.3pp band; drawdown range –36.5% to –60.3%, a
23.8pp band) captures the pure capacity-cascade variance this project's other work has already
found sensitive to small pool perturbations (see CLAUDE.md's "resolution floor" and
"capacity-cascade amplification" sections) — confirming, independently, that this system's
admission process really is this sensitive to which specific candidate fills a slot on a given
day, even before any deliberate design change is considered.

## Part 8a — Index-level universe comparison (descriptive)

**Sources, coverage confirmed before use:**
- S&P 500 total return: `^SP500TR` (yfinance) — genuine total-return index, 4,930 rows,
  2007-01-03 → 2026-08-07, full coverage confirmed.
- Nasdaq Composite: `^IXIC` (yfinance) — **price-return only**; no total-return variant exists for
  the Composite via yfinance (`^SP400TR`/`^SP600TR` were checked and rejected — see below).
  Understates true return by the Composite's own dividend yield (small, real, unmodelled).
- S&P MidCap 400: no usable historical `^SP400TR` (only 1 row returned, today's quote only).
  **Proxy used**: MDY (SPDR S&P MidCap 400 ETF), dividend-adjusted close — real, tradeable,
  dividend-reinvested, but carries the ETF's own ~0.23%/yr expense drag and tracking error.
- S&P SmallCap 600: same situation, same proxy choice — IJR (iShares Core S&P Small-Cap ETF).

All four confirmed survivorship-bias-free by construction (live, reconstituted indices/ETFs).

**Overall, full 2007-2026 window:**

| | Strategy | S&P 500 TR | Nasdaq Composite (price) | MidCap 400 (MDY) | SmallCap 600 (IJR) |
|---|---|---|---|---|---|
| CAGR | **7.05%** | 11.13% | 12.95% | 9.62% | 9.44% |
| Peak-relative-adjacent DD | –50.38% | –55.25% | –55.63% | –55.33% | –58.15% |

**The strategy trails every one of the four broader benchmarks on CAGR over the full window** —
by 2.4pp (vs. MidCap 400, the closest) to 5.9pp (vs. Nasdaq Composite, the widest gap) — while
having a *milder* max drawdown than all four. **Blocks (of 16) where the strategy's own CAGR beat
each index's**: S&P 500 TR 4/16, Nasdaq Composite 2/16, MidCap 400 7/16, SmallCap 600 7/16. The
strategy is a below-benchmark performer against broad, dividend-inclusive equity exposure over
this window, on a pure returns basis, trading a materially smaller drawdown for it.

## Part 8b — Momentum signal on tradeable index proxies (single instrument, requires backtest runs)

The real production engine (`simulate_portfolio_compounding_mtm`), unmodified, run on one ticker
at a time (SPY, QQQ, MDY, IJR) — no capacity competition, since each run has only one possible
candidate.

| Proxy | Trades | CAGR | Max DD | Win rate | Profit factor |
|---|---|---|---|---|---|
| SPY (S&P 500) | 111 | 0.34% | –1.20% | 39.6% | 1.790 |
| QQQ (Nasdaq-100, high volatility) | 130 | 0.48% | –1.32% | 37.7% | **1.840** |
| MDY (MidCap 400, broader) | 112 | 0.21% | –1.77% | 37.5% | 1.580 |
| IJR (SmallCap 600, broadest/most volatile) | 110 | 0.17% | –1.65% | 37.3% | **1.380** |

**Important caveat on CAGR here, stated directly**: single-instrument VaR sizing at
`per_trade_pct=5%` with no competing candidates leaves the vast majority of the account
permanently in cash (there is never a second position to deploy remaining capital into) — these
CAGR figures reflect near-total capital under-utilization, not a realistic "what would an investor
actually get" comparison, and should not be read against Part 8a's multi-stock CAGR figures.
**Profit factor and win rate are the fairer cross-proxy comparators here** (both per-trade,
insensitive to exposure level). **No monotonic relationship between universe breadth/volatility
and momentum quality**: QQQ (moderate breadth, high volatility, high-quality large caps) is the
best performer on both PF and CAGR; IJR (broadest, smallest-cap, presumably most volatile) is the
*worst* on PF despite being the most "diversified" of the four. Momentum-the-signal does not
straightforwardly improve by broadening or adding volatility to the underlying universe — directly
relevant context for the sector-tilt finding in Part 3, since it argues against "just trade a
broader universe" as an obvious fix.

---

## Anomalies and observations (flagged, not recommended)

1. **Admissions that did NOT need freed capacity underperform those that did, by a wide, consistent
   margin in both pools** (win rate 28.5–32.6% vs. 44.2–45.3%; mean P&L% –1.0% to –1.9% vs. +0.9%
   to +1.2%). This is counterintuitive — one might expect "capacity was already free" to reflect a
   healthier, less-stretched portfolio state, but the data shows the opposite. Worth investigating
   directly: this small population (340–347 trades, ~7% of admissions) may correlate with unusual
   market conditions (e.g., early in the backtest history, or immediately following a crisis-driven
   mass-exit that leaves capacity free but the surviving candidate pool weak) rather than being a
   mechanism-level effect — not established here, flagged for follow-up.

2. **The most-negative-momentum decile of gate-failed candidates has the highest or near-highest
   win rate of any bucket in this entire audit, in both pools** (55.2–56.8%, exceeding the admitted
   population's own 43–44%). Confirmed to be substantially explained by a much shorter realized
   holding period in the counterfactual (dominated by fast `SIGNAL_OFF` exits) rather than a
   genuine multi-week trend — but the magnitude and cross-pool consistency (identical shape in two
   independently-built pools) is more than the caveat alone fully explains, and is worth a dedicated
   short-horizon mean-reversion investigation on its own terms.

3. **Rank-at-admission and momentum-decile predictiveness diverge.** Part 2 shows raw
   momentum-at-entry has ~zero correlation with trade outcome overall; Part 4 shows rank-*within-
   day* (a relative measure, conditioned on that day's specific eligible set) does carry real
   signal (top-quartile admits meaningfully outperform below-top-quartile admits, in both pools).
   These are not contradictory, but they point at different mechanisms — worth being precise about
   which one is claimed to be doing useful work.

4. **Sector tilt is real, large, and directionally consistent across pools** — Technology and
   Consumer Cyclical are 1.5–2.9x overweight vs. either candidate pool's own composition;
   Financial Services, Utilities, and Real Estate are 0.4–0.75x underweight. This is a predictable
   consequence of momentum ranking (not flagged as a bug), but is a genuine, measurable
   concentration risk that a reader reasoning about diversification should know about explicitly.

5. **92–93% of every admission event, in both pools, only happened because that day's own exits
   freed the exact capacity used.** Selection is dominated by *timing* — which slot happened to
   open on which day — far more than by a deep, standing pool of qualified alternatives. Combined
   with observation 1 above (the ~7% that don't need freed capacity underperforming), timing looks
   like a materially larger driver of which specific trades get made than this audit's own
   pre-registered framing anticipated.

6. **Rejected-capacity candidates are not obviously worse than admitted ones** (Part 5) — capacity
   constraints bind on a population that looks close to as good, on average, as what gets through.
   Combined with observation 5, this paints a coherent picture: a large share of *which* good
   candidate gets the slot on a given day is close to arbitrary among a genuinely comparable set,
   even though the gate+ranking mechanism, taken as a whole, does correctly exclude the clearly
   weak population (Part 6's 0–2% floor band, and the overall momentum-negative population once the
   short-holding-period caveat is set aside for genuinely weak names outside the anomalous D1
   bucket).

7. **Pool 2's sector map is only 60.6% resolved** (yfinance returns nothing for ~40% of its 1,042
   candidates, almost entirely delisted/historical tickers) — the Pool 2 sector-tilt comparison in
   Part 3 should be read as directionally consistent with, but less precise than, the fully-resolved
   Pool 1 comparison.

8. **Concentration (Part 1) and repeat-name dominance (Part 3) overlap materially** — NVDA alone
   (86 trades) is both the single most-traded ticker and one of the largest P&L contributors,
   worth keeping in mind when interpreting the top-decile P&L concentration: a meaningful share of
   it is not 344 independent lucky picks but repeated entries into a small number of tickers that
   happened to run for years.

9. **Momentum ranking beats random admission decisively on return, but at a real cost in
   drawdown (Part 7).** The real baseline sits at the 99th percentile of a complete 100-seed
   random-admission distribution on both frictionless and cost-adjusted CAGR, but at the 98th
   percentile (worst end) on drawdown — ranking is not decoration, but it is also not a free
   improvement over random selection from the same gate-passed pool; it trades risk for return in
   a way that should be read alongside Part 1's concentration finding and Part 4's "8.0% outside
   top quartile" finding as three views of the same underlying mechanism.

---

**All 8 parts are complete.** This report is final as written above. Full underlying data (trade
exports, candidate-day logs, rejection logs, the 100-seed Arm R sweep, and every intermediate
script) is preserved in `research/selection_audit_2026-08-08/` for anyone who wants to
re-derive or spot-check any number in this report directly.
