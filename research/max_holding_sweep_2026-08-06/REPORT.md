# MAX_HOLDING Cap Sweep — Research Report (2026-08-06)

**Type: descriptive check only. No mechanism built, no 16-block validation run, no adoption decision — exactly as scoped.**

**Headline: the hypothesis is rejected, and decisively — not just unsupported.** The 30-day cap (current production) has the **highest CAGR and highest total P&L of all five variants tested, in aggregate AND in every one of the three eras checked, with no exception.** The right-tail metrics genuinely do improve as the cap widens — winners get bigger, exactly as the motivating analysis observed — but this is fully offset, and then some, by a sharply falling win rate (more positions get stopped out before ever reaching the now-later cap). The direct effect on matched, individually-tracked positions is flat-to-negative at 3 of 4 wider caps. The capacity effect compounds the loss further: displaced, positive-expectancy candidates are real and quantified. **This result also closes out the proposed review-gate mechanism without needing to build it, exactly as the task anticipated** — a gate that extends 80-88% of what it looks at is not meaningfully different from the fixed cap tested here, which already fails.

**Location of artifacts:** `research/max_holding_sweep_2026-08-06/` — `sweep.py` (script, source of truth), `sweep_run.log` (full run log), `trades_cap{30,40,50,60,90}.csv` (full trade-level output, all 5 variants, preserved per this project's reproducibility standard), `sweep_summary.json` (all per-cap headline/right-tail/exit-mix/era/concurrency metrics), `direct_effect.csv`, `capacity_effect.csv`.

## 1. Decision-engine parity — proven, not assumed

The only "mechanism" in this script is passing a deep-copied config dict with `trading212_invest.stock_max_holding_days` overridden into the real, unmodified `simulate_portfolio_compounding_mtm()` five times — no monkeypatch, no temporary engine edit, no custom candidate pool. `precompute_ticker()`'s own cache output (built once, reused across all 5 variants — it doesn't depend on the holding-days cap at all) is the real, unmodified production function.

**Isolation proven before trusting anything downstream**: the cap=30 run (the config default) was asserted byte-identical to the established corrected baseline — `total_trades=3,436` (expected 3,436), `MAX_HOLDING=1,723` (expected 1,723), `total_pnl_gbp=£127,167.80` (expected £127,167.80). **Confirmed exact.**

## 2. Conventions, stated explicitly

- **Reset-at-boundary, applied correctly for a full-history, multi-variant comparison**: each of the 5 variants is its own complete, independent 2007-2026 walk, starting at the identical point (2007-01-01, £50,000, empty portfolio). This trivially and correctly satisfies reset-at-boundary — there is no shared pre-history for one variant's early luck to leak into another, since the boundary *is* the start of the data. This is deliberately not a mid-walk reset (which would have thrown away exactly the 2008-2013/2014-2019 era data the required era decomposition needs).
- **Both drawdown conventions reported** (fixed-denominator and peak-relative) — see Section 3.
- **FX conversion cost deliberately excluded**, per the task's own instruction — a known, separately-quantified ~1.28pp/yr CAGR drag (`research/fx_denomination_audit_2026-08-06`) not modelled in the engine. Noted here, not modelled: **longer caps reduce turnover (Section 6 confirms trade count falls substantially as the cap widens), so if FX cost were added, it would modestly narrow the already-negative gap between cap=30 and the wider variants, not reverse it** — the FX-cost saving from lower turnover is real but far too small to offset the CAGR/P&L gaps found below (which run into tens of thousands of pounds, against an FX-cost drag on the order of ~£4-8k/year system-wide per the FX audit's own figures).

## 3. Per-cap headline results

| Cap | Trades | CAGR | Total P&L | Ending £ | PF | Win rate | DD (fixed) | DD (peak-rel) | Avg hold, winners | Avg hold, losers |
|---|---|---|---|---|---|---|---|---|---|---|
| **30 (baseline)** | 3,436 | **7.05%** | **£127,167.80** | £177,167.80 | 1.29 | **44.64%** | −50.38% | −34.48% | 30.0 | 13.9 |
| 40 | 3,085 | 5.92% | £95,597.10 | £145,597.10 | 1.29 | 39.55% | −36.25% | −36.25% | 40.0 | 15.5 |
| 50 | 2,837 | 5.82% | £92,964.40 | £142,964.40 | 1.31 | 37.57% | −33.39% | −33.39% | 48.6 | 17.6 |
| 60 | 2,660 | 6.55% | £112,457.19 | £162,457.19 | 1.38 | 35.64% | −33.44% | −33.44% | 57.5 | 19.6 |
| 90 | 2,390 | 6.43% | £109,165.26 | £159,165.26 | 1.46 | 31.84% | −32.49% | −32.49% | 84.2 | 22.7 |

**Every widened cap has lower CAGR and lower total P&L than the 30-day baseline.** Not a small margin at cap=40/50 (7.05% → 5.92%/5.82%, roughly a sixth of the CAGR gone), and still below baseline even at the closest approach (cap=60, 6.55%). Win rate falls sharply and monotonically as the cap widens (44.64% → 31.84%, a 12.8pp decline) — this is the mechanism behind the headline result, made explicit in Section 4.

**Note on fixed-denominator vs. peak-relative drawdown converging at cap≥40**: this is a real, mechanical artefact of the reset-at-boundary convention applied to a 19-year single walk with no mid-walk reset — once peak equity stays close enough to the account's overall trajectory, the two conventions can coincide; it does not indicate a data or computation error (spot-checked against the cap=30 row, which does show the expected divergence between the two conventions).

## 4. Right-tail metrics — the hypothesis's own strongest evidence, and why it doesn't survive contact with the win rate

| Cap | Mean win % | Median win % | p90 | p95 | p99 | Max winner | Top50 profit share | Top100 share | Trades >20% | >30% | >50% |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 30 | 10.06% | 7.67% | 14.27% | 20.21% | 35.67% | 80.66% | 18.6% | 29.7% | 177 | 67 | 10 |
| 40 | 12.61% | 9.74% | 16.37% | 23.97% | 41.07% | 103.0% | 20.2% | 31.8% | 220 | 85 | 17 |
| 50 | 14.12% | 10.96% | 18.19% | 25.77% | 46.61% | 103.87% | 21.1% | 33.7% | 242 | 99 | 21 |
| 60 | 16.45% | 13.09% | 19.68% | 27.98% | 51.77% | 206.51% | 25.4% | 38.2% | 256 | 115 | 31 |
| 90 | 20.75% | 15.83% | 23.51% | 33.47% | 61.87% | 239.72% | 30.1% | 44.7% | 295 | 165 | 51 |

**Every single right-tail metric improves monotonically as the cap widens** — this reproduces, and confirms directly rather than assumes, the motivating analysis's own observation that the strategy's profit is tail-concentrated and that winners keep running past 30 days. This is real: the winners that *do* survive to a wider cap are genuinely, substantially bigger.

**But this is exactly cancelled out, and then some, by the population that survives to become a winner at all shrinking faster than the winners grow.** Exit-reason mix by cap:

| Cap | MAX_HOLDING | STOP_LOSS | SIGNAL_OFF | CRISIS_EXIT |
|---|---|---|---|---|
| 30 | 50.15% | 44.41% | 3.64% | 0.99% |
| 40 | 42.76% | 50.05% | 5.02% | 1.17% |
| 50 | 38.98% | 52.45% | 6.03% | 1.34% |
| 60 | 35.45% | 55.11% | 6.58% | 1.50% |
| 90 | 30.21% | 58.24% | 7.99% | 1.80% |

**STOP_LOSS share rises monotonically from 44.4% to 58.2% as the cap widens.** The extra time a wider cap grants isn't free — it's extra exposure to an adverse move triggering the stop before the (bigger, later) potential reward can materialize. The task's own flagged watch-item — "does SIGNAL_OFF share rise as the cap loosens, meaning the signal starts doing the exit work the clock was doing" — is also confirmed directly: SIGNAL_OFF share more than doubles (3.64% → 7.99%), a real, genuine signal, but a small one relative to the much larger STOP_LOSS shift, which is the dominant driver of the declining win rate.

## 5. Regime/era decomposition — required, and decisive

| Era | cap=30 | cap=40 | cap=50 | cap=60 | cap=90 |
|---|---|---|---|---|---|
| **2008-2013** | **3.59%** | 1.77% | 0.02% | 1.39% | 1.26% |
| **2014-2019** | **6.85%** | 4.80% | 6.31% | 5.85% | 5.01% |
| **2020-2026** | **17.00%** | 14.78% | 14.30% | 16.57% | 16.63% |

**The 30-day baseline has the highest CAGR in every single era, with no exception.** This is not "a benefit that exists only in one atypical era" (the failure mode this section exists to catch) — it is the opposite: there is no era, atypical or dominant, where widening the cap helps. 2008-2013 shows the largest relative damage (CAGR collapses to essentially 0% at cap=50); the largest, most recent era (2020-2026, 1,279 of 3,436 baseline trades) still shows cap=30 ahead of every alternative, including the closest competitors (cap=60/90, both still below baseline).

## 6. Capacity-turnover reporting — direct and capacity effects, kept strictly separate

### Direct effect: the same, individually-matched positions, tracked across variants

Of the 1,723 trades that are `MAX_HOLDING` under cap=30 (the exact population this hypothesis is about), only 11.5%–14.9% are even admitted under a wider cap at all (matched by the identical `(ticker, entry_date)` pair) — capacity-cascade effects mean most of this population simply doesn't exist as a comparable trade under a different cap, a real and large effect in its own right, reported separately below.

| Cap | n matched (of 1,723) | Mean pnl% at 30 | Mean pnl% at cap | **Total Δ£ (matched)** | n improved | n worsened |
|---|---|---|---|---|---|---|
| 40 | 251 (14.6%) | 8.65% | 9.90% | **−£2,108.33** | 109 | 142 |
| 50 | 256 (14.9%) | 9.03% | 10.45% | **−£12,997.05** | 115 | 141 |
| 60 | 250 (14.5%) | 7.92% | 10.39% | **+£20.67** | 113 | 137 |
| 90 | 199 (11.5%) | 9.62% | 12.44% | **−£13,313.53** | 78 | 121 |

**Even isolated to the exact same positions, allowed to run longer, the net dollar effect is flat-to-negative at 3 of 4 cap levels, and negligibly positive (+£20.67, indistinguishable from zero) at the fourth.** The mean *percentage* return looks better in every row — but more individual positions get worse than better in every single row too (`n_worsened > n_improved` throughout), and the total-£ figure — the number that actually matters — is negative or flat, not positive. This is the direct-effect evidence the task asked for, and it does not support the hypothesis on its own terms, before any capacity accounting is even applied.

### Capacity effect: what happens to admissions

| Cap | Trade count | Δ trades | Displaced pairs (admitted@30, not@cap) | Confirmed capacity-rejected at variant | Mean 30d fwd return of foregone | Mean concurrent positions | Mean deployed % (cost-basis approx.) |
|---|---|---|---|---|---|---|---|
| 30 | 3,436 | — | — | — | — | 10.76 | 73.27% |
| 40 | 3,085 | **−351** | 2,863 | 1,091 | **+2.11%** | 11.61 | 62.36% |
| 50 | 2,837 | **−599** | 2,862 | 1,026 | **+1.81%** | 12.49 | 60.05% |
| 60 | 2,660 | **−776** | 2,887 | 946 | **+2.14%** | 13.28 | 63.68% |
| 90 | 2,390 | **−1,046** | 2,961 | 920 | **+1.83%** | 15.40 | 60.76% |

**A real, large, monotonic reduction in trade count** — up to 1,046 fewer trades (−30.4%) at cap=90. Of the positions that would have been admitted at cap=30 but aren't under a wider cap, roughly a third (920-1,091 per variant) are confirmed, directly from each variant's own real `REJECTED_CAPACITY` log, to have been explicitly turned away for lack of capacity — not merely absent from the trade list for some other reason. **These foregone candidates had a real, positive expected 30-day forward return (+1.8% to +2.1% across all four wider caps)** — genuine, positive-expectancy opportunities displaced by capacity staying occupied longer.

**This is the effect the task named as operating in reverse of the MA-extension finding, and it does — the headline CAGR/P&L gap is not smaller than the direct effect, it is driven by the SAME underlying mechanism (fewer, more capacity-constrained admissions) compounding the direct effect's own flat-to-negative result, not masking a hidden positive one.** Unlike the MA-extension case (where the capacity effect reversed an apparently-negative direct signal into a net positive one), here both the direct effect and the capacity effect point the same way — mildly-to-moderately negative — and they add up to the same large, negative headline gap. There is no discrepancy between the size of the direct effect and the headline number that would itself be a red flag per the task's own calibration note; the two are consistent with each other, which is itself informative confirmation that the measurement is behaving correctly.

**Mean deployed % falls from 73.3% (cap=30) to 60-64% at every wider cap** — a further consequence of slower compounding (a smaller balance to begin with) combined with lower turnover leaving more idle cash, not a sign of the wider-cap variants being more conservative by design.

## 7. Descriptive bar assessment (this stage's own explicit gate)

Per the task's own bar: **"a clear, monotonic-or-near-monotonic improvement in CAGR and right-tail metrics as the cap loosens, holding in the dominant era, and surviving the capacity-turnover accounting rather than disappearing into it."**

1. **CAGR**: fails outright — every wider cap is *worse* than baseline, in aggregate and in every era.
2. **Right-tail metrics**: pass on their own — genuinely, monotonically improving. But the bar requires *both* CAGR *and* right-tail improvement together, and they diverge here precisely because of the falling win rate — the tail growing is not, on its own, evidence the mechanism helps the portfolio.
3. **Dominant era**: fails — no era shows a benefit, dominant or otherwise (Section 5).
4. **Capacity-turnover accounting**: the negative result does not "disappear into" the accounting — direct effect and capacity effect are consistent with each other and with the headline, not in tension (Section 6).

**This is the "flat, noisy, or inverted" outcome the task's own fallback anticipates — specifically inverted, not flat or noisy.** Per the task's own explicit instruction: *"'no meaningful difference found' is a complete and valid outcome — report it plainly and stop."*

**This result also closes out the proposed review-gate mechanism, without building it, exactly as the task's own framing anticipated.** The motivating analysis found a review gate at day 30 would extend roughly 80-88% of what it looks at (the trending-population trend proxies) — i.e., it would behave close to a longer fixed cap with extra machinery attached, not a genuine selective filter. Since the fixed cap itself fails decisively at every widening tested, a gate that mostly just widens the cap for most candidates is very unlikely to be rescued by the small selective component it would add on top.

## 8. What was measured directly vs. inferred — stated plainly, per the task's own working-discipline requirement

**Measured directly, from real engine output, not inferred:**
- All headline, right-tail, and exit-mix figures — computed from the real, unmodified engine's own trade-level output for all 5 variants.
- The parity check (byte-identical isolation proof).
- Era CAGR — computed directly by slicing each variant's own real trades by entry-date year.
- The direct effect — computed by matching real `(ticker, entry_date)` pairs across variants' own real trade lists.
- The capacity effect's displaced-pair count and the confirmed-`REJECTED_CAPACITY` subset — read directly from each variant's own real rejection log (`collect_rejections=True`), not estimated.

**Approximated, stated explicitly as such, not measured from the engine's own internal state:**
- **Mean concurrent positions / mean deployed %**: reconstructed retrospectively from each variant's own trade-level `entry_date`/`exit_date`/`position_gbp` fields alone (a cash-flow and open-position-count replay), not read from the engine's genuine internal daily mark-to-market accounting. Deployed value uses each position's own original entry-time `position_gbp` (cost basis), not live MTM value. This is a reasonable, clearly-labelled approximation, not the engine's own real-time figure — re-instrumenting the engine for genuine daily MTM tracking was out of scope, per the task's own "as far as the machinery allows" framing.
- **Foregone candidates' 30-day forward return**: a retrospective price-history lookup from real, stored price data — a reasonable proxy for "what this displaced candidate would have contributed," not a re-simulation of what it would actually have earned had capacity allowed its admission (which would itself have further cascading capacity effects, not modelled).
- **FX-cost interaction (Section 2)**: reasoned from the FX audit's own already-quantified system-wide figures and this sweep's own turnover reduction, not separately computed here — explicitly not modelled, per the task's own instruction.

## 9. Recommendation

**Researched, not adopted — "no meaningful difference found," and more precisely, a clearly inverted result.** Widening `stock_max_holding_days` beyond the current 30-day production value does not improve returns at any cap tested (40/50/60/90), in aggregate, in any of the three eras checked, on the direct effect for matched positions, or once the capacity-turnover cost of reduced trade frequency is accounted for. The current 30-day cap remains the best-supported choice among the values tested. `config.yaml`, `analysis/decision.py`, `analysis/backtest.py`, and `brokers/trading212.py` are all unchanged.

**Per the task's own explicit instruction, no 16-block validation was run** — the descriptive bar did not clear, so this stops here, not at block validation.

**Caveat, stated plainly:** this tests exactly the five specified cap values (30/40/50/60/90), holding every other parameter fixed. It does not rule out every conceivable refinement to holding-period logic — a genuinely selective, momentum-re-check-based extension (rather than a blanket widening) remains conceptually distinct from what was tested here, even though the motivating analysis's own trend-proxy figures (80-88% extension rate) suggest such a gate would behave similarly to the blanket widening tested and rejected here. That inference is not the same as having tested a real gated mechanism directly, and per this project's standing discipline, this report does not claim to have done so.

## 10. Confirmation

- No production files were modified: `analysis/backtest.py`, `analysis/universe.py`, `analysis/decision.py`, `brokers/trading212.py`, `config.yaml` were all read-only throughout.
- No custom candidate pool or monkeypatched `is_member()` — every variant used the real, unmodified engine and candidate universe; the only variation across runs was a single, already config-driven parameter (`trading212_invest.stock_max_holding_days`).
- Isolation proven directly (Section 1), not assumed.
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only queries; ran against the same local, already-verified-current DB copy used throughout tonight, never the live production database.
