# Trailing exit to replace/augment the MAX_HOLDING 30-day cap (2026-08-06)

**Type:** pre-specified single-mechanism test. Three variants, no tuning beyond
them, no open search. Nothing changed, nothing committed to production.

**Hypothesis under test:** the 30-day MAX_HOLDING cap truncates a valuable
right tail (1,723 trades exiting on the cap at +8.5% / 86% win rate; 357 of
them still at their running peak at +16.1%), and a trailing exit reacting to
realized price action recovers part of it — largest effect in melt-ups.

---

## Headline

**The hypothesis is rejected, and it fails on its own premise rather than
only on capacity grounds. None of the three variants comes close to the
adoption bar; all three are worse than baseline.**

1. **Every variant loses to baseline on chained aggregate.** Baseline 7.20%
   CAGR → T3 6.77%, T1 5.65%, T2 4.64%. All three also move *further* from
   the passive legs they were meant to close the gap to (leg C-zero 7.54%,
   leg D 8.00%, leg C-real 8.09%). Per-block, T1 and T2 beat baseline in
   **3 of 16** blocks each, T3 in **8 of 16** with a negative median gap
   (−0.31pp).

2. **The pre-specified acid test fails. The cap-truncation diagnosis is not
   supported.** Block 11's −19.91pp gap vs the S&P becomes **−23.95pp (T1)**
   and **−26.58pp (T2)** — materially *worse* — and **−19.16pp (T3)**, closing
   0.75pp of a 19.91pp gap, i.e. **3.8% of it**. Stating it as the spec
   requires: no variant materially closes the melt-up gap.

3. **The premise itself is false, shown like-for-like with capacity excluded
   by construction.** Following the *same* trades (same ticker, same entry
   date) through each variant, the baseline MAX_HOLDING trades that exited
   **still at their running peak** — the exact population the hypothesis
   rests on — get **worse**, not better:

   | | n | baseline | variant | Δ | % improved |
   |---|---|---|---|---|---|
   | T1, at-peak | 125 | +14.48% | +13.17% | **−1.31pp** | 34% |
   | T2, at-peak | 113 | +13.82% | +8.91% | **−4.91pp** | 22% |
   | T3, at-peak | 153 | +13.92% | +14.07% | +0.15pp | 41% |

   The mechanism is visible directly in the pooled exit mix: baseline
   MAX_HOLDING exits average **+8.32%**; the TRAILING_STOP exits that replace
   them average **+4.91% (T1)** and **+2.86% (T2)**. A trailing stop must wait
   for a 2.5–3.0×ATR pullback before it fires, and that giveback consistently
   exceeds the extra upside captured. The cap is not truncating winners — it
   is harvesting them near a local peak, which is why it looks so good in the
   trade-level statistics that motivated this test.

4. **Capacity reverses the direct effect for two of the three variants** —
   the failure mode flagged in advance, and it repeated. Exactly as with the
   MA-extension finding.

**Not overstated:** T3's *direct* effect is genuinely positive (+£2,442) and
its at-peak trades do improve very slightly (+0.15pp). The rejection of T3 is
driven by capacity, not by the direct effect. T1's direct effect is
statistically indistinguishable from zero (+£330 across 16 blocks on £50k
each). Only T2 is bad on every axis.

---

## Verification gate

**Fidelity — byte-identical when disabled.** `trailing/variant_engine.py` is
*generated* from `analysis/backtest.py:simulate_portfolio_compounding_mtm()`
(lines 1174–1668) by `build_variant_engine.py`, which applies exactly five
asserted, auditable string replacements; nothing else differs. With
`variant="baseline"` it reproduces the production engine's full trade list
**byte-identically** — SHA-256 of the complete ordered trade list, every
field:

| Block | Production | variant="baseline" | |
|---|---|---|---|
| 1 (2008-03-23→2009-06-13) | 268 trades, `12fcbbd29eb4b732` | 268, `12fcbbd29eb4b732` | IDENTICAL |
| 6 (2014-05-06→2015-07-27) | 236 trades, `3b4bbf7ba499eb86` | 236, `3b4bbf7ba499eb86` | IDENTICAL |
| 11 (2020-06-19→2021-09-09) | 261 trades, `e841e6613719ae76` | 261, `e841e6613719ae76` | IDENTICAL |

Blocks chosen to span a crash, an ordinary bull block and the acid-test
melt-up. This simultaneously proves the extended precompute (adding `high`
and `ma14`) is **inert** — production ran against the *same* extended caches
the variants use, so any contamination from the extra columns would have
surfaced here. (`fidelity_gate.txt`)

**Independently, the sweep's own baseline leg reproduces the preserved
2026-08-06 benchmark run to 0.0000pp** on all 16 blocks' CAGR — so the leg C
and leg D curves reused from that run remain directly comparable without
re-solving, as required.

**Decision-engine parity.** Every exit decision still routes through the
shared `analysis/decision.py:evaluate_exit_reason()`. T1/T2 disable the cap by
passing `max_holding_days=None` — that function's *own documented* "unknown,
don't trigger this check" input, not a local reimplementation of its priority
chain. The trailing stop occupies MAX_HOLDING's slot at the **end** of the
chain, so CRISIS_EXIT / STOP_LOSS / SIGNAL_OFF keep strict precedence,
exactly as specified. Entry, sizing, admission and re-entry logic are
untouched and still call the shared production functions.

---

## Conventions

- Reset-at-boundary, £50,000 fresh per block, zero positions, 16 blocks,
  **2007-01-01 → 2026-08-03** (the verified reproduction vintage, not 08-06).
- Corrected engine only: mark-to-market, with the 2026-08-06 intraday-touch
  STOP_LOSS fill as permanent default.
- **Trailing-stop fill convention (as required):** trigger on the day's
  **low**, fill at **min(trail_price, that day's open)** — the identical
  intraday-touch convention STOP_LOSS uses, so a day that gaps straight
  through the trailing level fills at the open, never at an unachievable
  level.
- **Non-look-ahead by construction:** the trailing level is built from the
  highest high and the ATR observed **through yesterday** (both ratcheted only
  *after* today's exit check), then tested against **today's** low. Setting
  the level from today's own high while triggering on today's low would
  silently assume the high preceded the low intraday.
- T3's MA_14 comparison uses today's close against today's MA_14, consistent
  with how SIGNAL_OFF and MAX_HOLDING already resolve at the close.
- Both drawdown conventions reported per variant per block
  (`variant_blocks.csv`).

---

## Per-block results

CAGR by variant (baseline = current strategy, corrected engine):

| Block | Start | Era | Baseline | T1 | T2 | T3 |
|---|---|---|---|---|---|---|
| 0 | 2007-01-01 | GFC | −5.30 | −7.10 | −5.21 | **−4.59** |
| 1 | 2008-03-23 | GFC | −22.94 | −24.01 | −23.65 | **−20.20** |
| 2 | 2009-06-13 | bull | **16.22** | 15.61 | 11.61 | 11.44 |
| 3 | 2010-09-03 | bull | 6.91 | **9.79** | 5.88 | 4.82 |
| 4 | 2011-11-24 | bull | **22.97** | 17.18 | 19.10 | 22.21 |
| 5 | 2013-02-13 | bull | 10.32 | 11.11 | **11.68** | 10.55 |
| 6 | 2014-05-06 | bull | **13.59** | 12.70 | 11.39 | 12.81 |
| 7 | 2015-07-27 | bull | **0.35** | −0.45 | −0.75 | −1.74 |
| 8 | 2016-10-17 | bull | 9.10 | 8.90 | 8.67 | **9.81** |
| 9 | 2018-01-07 | bull | 0.61 | −2.99 | −0.88 | **1.86** |
| 10 | 2019-03-30 | bull | **6.62** | 4.87 | 1.05 | 4.96 |
| 11 | 2020-06-19 | recent | 15.16 | 11.12 | 8.49 | **15.91** |
| 12 | 2021-09-09 | recent | **6.41** | 5.06 | 0.73 | 1.94 |
| 13 | 2022-11-30 | recent | 11.00 | 10.75 | 3.67 | **11.13** |
| 14 | 2024-02-20 | recent | 6.81 | **7.01** | 4.39 | 4.62 |
| 15 | 2025-05-12 | recent | 27.30 | 19.86 | 27.44 | **32.61** |

**The variants genuinely bind** — this is not a null result from an inert
change. Baseline max holding is 33 days in every block; T1 reaches 55–169
days, T2 52–144, T3 57–63 (its 60-day ceiling). Mean holding rises from ~20
to ~24 days (T1/T3) and *falls* to ~17 (T2, whose tighter trail churns
faster).

### Summary vs baseline and vs the passive legs

| Variant | Wins vs baseline | Median gap | Wins vs leg C-real | Wins vs leg D | Chained CAGR |
|---|---|---|---|---|---|
| baseline | — | — | 8/16 | 6/16 | **7.20%** |
| T1 | 3/16 | −0.98pp | 6/16 | 5/16 | 5.65% |
| T2 | 3/16 | −1.84pp | 6/16 | 4/16 | 4.64% |
| T3 | 8/16 | −0.31pp | 8/16 | 4/16 | 6.77% |

Reference (unchanged, reused from the preserved 2026-08-06 run):
leg C-zero-tr **7.54%**, leg D 60/40 **8.00%**, leg C-real-tr **8.09%**,
leg A S&P price-return **8.91%**.

**Adoption bar** was "clearly, exceptionally strong vs baseline **AND** closes
the gap to leg C / leg D." No variant beats baseline on aggregate, and all
three *widen* the gap to both passive legs. **None is adoptable; the bar is
not close to met.**

---

## Capacity-turnover split (mandatory, never blended)

The decomposition is **exact, not an approximation** — `pnl = position × r`,
so for a trade in both runs `pnl_V − pnl_B = pos_B(r_V − r_B) + (pos_V −
pos_B)r_V`, and trades in only one run contribute their whole P&L. Measured
residual across all 48 variant-blocks: **£0.00**.

| Variant | (a) Direct exit effect | (b) Sizing effect | (c) Turnover effect | Capacity (b+c) | Net |
|---|---|---|---|---|---|
| T1 | **+£330** | −£228 | −£16,185 | −£16,413 | **−£16,083** |
| T2 | −£4,333 | −£226 | −£21,169 | −£21,396 | **−£25,729** |
| T3 | **+£2,442** | −£675 | −£6,083 | −£6,758 | **−£4,316** |

*(£ on £50,000 per block, summed across 16 blocks. Trades filled only by the
variant: T1 2,303, T2 3,383, T3 2,135. Trades the baseline filled that the
variant no longer does: T1 2,924, T2 2,896, T3 2,651.)*

**Capacity reverses the direct effect for T1 and T3 — flagged prominently as
required.** T1's direct effect is +£330 (economically nil) against a −£16,413
capacity cost, a ~50× reversal. T3's direct effect is genuinely positive at
+£2,442 but is swamped ~2.8× by −£6,758 of capacity cost. **T3 would look
like a modest improvement on any first-order per-trade analysis and is in
fact a loss once re-simulated** — precisely the red flag the spec named in
advance.

Note T2's turnover cost (−£21,169) is the largest despite it filling *more*
trades than baseline: the freed capacity is spent on lower-ranked candidates
that collectively underperform the trades they displaced. More turnover is
not more return.

---

## Era decomposition

| Era | Blocks | T1 wins | T1 median | T2 wins | T2 median | T3 wins | T3 median |
|---|---|---|---|---|---|---|---|
| 2007-2009 GFC | 2 | 0/2 | −1.44pp | 1/2 | −0.31pp | **2/2** | **+1.73pp** |
| **2010-2019 bull (dominant)** | 9 | 2/9 | −0.80pp | 1/9 | −1.49pp | 3/9 | −0.78pp |
| 2020-2026 recent | 5 | 1/5 | −1.35pp | 1/5 | −5.68pp | 3/5 | +0.13pp |

**In the dominant 2010-2019 era every variant loses**, T3 included (3/9,
median −0.78pp). T3's only positive era is the 2-block GFC — far too small a
sample to carry a finding, and the exact "lives only in a small atypical
window" pattern this project already rejects findings for. T3's 2020-2026
median of +0.13pp is inside noise.

This kills the "largest effect in melt-up regimes" part of the hypothesis
specifically: the recent era is where T1 and T2 do *worst* (−1.35pp and
−5.68pp medians).

---

## Measured vs estimated

**Measured (full re-simulation, nothing first-order):** every variant figure
here — all 15,698 trades across 4 variants × 16 blocks, each a complete
independent re-simulation including all admission and capacity effects. The
capacity decomposition is exact arithmetic on those trades. The fidelity
hashes are exact. The at-peak classification is computed from real stored
price data (exit close equal to the running max close since entry).

**Estimated / inherited:** leg C and leg D figures are reused unchanged from
the 2026-08-06 benchmark run as instructed (not re-solved), and carry that
run's own stated estimate — index total returns derived from SPY/QQQ realised
dividend factors, net of expense ratio. Leg comparisons are therefore exactly
as reliable as in that report, no better and no worse.

**One small discrepancy, stated rather than smoothed:** the hypothesis cited
357 at-peak MAX_HOLDING trades at +16.1%; I measure **403 at +15.21%** on the
same baseline. The difference is a classification-boundary matter (I count a
trade as at-peak when its exit close equals the highest close since entry, to
1e-9). It does not affect the direction or size of any result — the same
population gets worse under T1/T2 either way.

---

## Caveats

1. **This tests one mechanism family, as specified.** It does not show that
   no exit rule can help — only that Chandelier trailing at 3.0×/2.5×ATR and
   trend-conditional cap release to 60 days all fail, decisively. Do not
   generalise it to "exit timing does not matter."

2. **No parameter search was run, deliberately.** Only the three
   pre-specified variants. T2 being worse than T1 is a monotone hint that
   *looser* trails might be less bad, but chasing that is exactly the tuning
   this brief excluded, and T1's direct effect being ~zero suggests the
   ceiling on that direction is low.

3. **The trailing state resets at each block boundary** along with everything
   else, which is correct under reset-at-boundary but means a position that
   would have run across a boundary is truncated. This affects all variants
   and baseline identically (baseline's own END_OF_DATA exits do the same),
   so it does not bias the comparison.

4. **Daily-bar granularity limits the trailing fill**, exactly as it does the
   STOP_LOSS fill: a daily `low` says the level was touched, not when, and
   not the intraday path. Same known limit already recorded for the
   2026-08-06 stop-fill correction; it is not made worse here, but the
   trailing variants trigger on it far more often (1,929–3,094 TRAILING_STOP
   exits vs ~1,000 STOP_LOSS), so they lean on that assumption harder than
   baseline does. If anything this **flatters** the variants.

5. **The survivorship-bias caveat from the 2026-08-06 benchmark run carries
   over unchanged** (early-block candidate coverage 52.5% → 99.4%). It
   affects baseline and variants alike and so is roughly neutral for this
   comparison, unlike for the passive-leg comparison.

---

## Files

| File | Contents |
|---|---|
| `variant_blocks.csv` | Per block per variant: CAGR, total return, both drawdown conventions, PF, trade count, mean/max holding days, exit mix |
| `variant_blocks_enriched.csv` | The above joined to the preserved leg A / C / D per-block figures |
| `variant_summary.csv` | Win rates and median/mean gaps vs baseline and each passive leg; chained aggregates |
| `capacity_decomposition.csv` | Exact three-way split per variant per block, with residual column (all £0.00) |
| `era_decomposition.csv` | Per-era win rates and gaps |
| `variant_trades.csv.gz` | **All 15,698 trades**, variant- and block-tagged |
| `fidelity_gate.txt` | Byte-identity proof output |
| `hypothesis_check_output.txt` | Like-for-like trace of the at-peak MAX_HOLDING population |
| `analysis_output.txt` | Full analysis run output |
| `variant_engine.py` | The generated variant engine |
| `build_variant_engine.py` | The generator (the 5 audited replacements) |
| `precompute.py`, `run_fidelity_check.py`, `run_sweep.py`, `analyse.py`, `hypothesis_check.py` | Harness |

Run locally in an isolated `git worktree` against a copied production
database. No production file modified; `analysis/backtest.py` and
`analysis/decision.py` were read-only throughout.
