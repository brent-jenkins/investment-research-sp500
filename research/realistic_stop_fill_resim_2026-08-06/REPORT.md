# Realistic (Intraday-Touch) STOP_LOSS Fill — Full Re-Simulation (2026-08-06)

**Urgent, and not a rejected research hypothesis.** Follow-up to the same-session `research/dividend_fill_convention_2026-08-06` audit, which found the backtest fires `STOP_LOSS` off the daily close and fills at exactly `stop_price` — both unrealistic — and flagged this as urgent specifically because live trading, since 2026-08-04, places a **real broker-side stop order** that genuinely fires on intraday touch. This is a live/backtest divergence in the shared engine every block validation tonight relied on, not a candidate strategy change to gate.

**Headline result: the true, fully re-simulated gap is far larger than the audit's own first-order estimate — GBP 357,308.59 (73.75% of reported P&L), against the audit's first-order GBP 212,267.47 (43.81%).** The corrected convention's 16-block pass rate drops from 10/16 to 9/16, and every single block's own CAGR/PF is materially worse, not just the one that flipped.

**Location of artifacts:** `research/realistic_stop_fill_resim_2026-08-06/` — `resimulate.py` (script, source of truth), `resimulate_run.log` (full run log), `trades_baseline.csv`/`trades_baseline_full.json`, `trades_corrected.csv`/`trades_corrected_full.json` (full trade-level output for both variants, preserved per this project's reproducibility standard since these are headline, citable figures), `headline_comparison.json`, `block_validation_results.json`.

## 1. Mechanism — an isolated, marked, TEMPORARY addition, already reverted

A new `realistic_stop_fill: bool = False` parameter was added directly to the real `analysis.backtest.simulate_portfolio_compounding_mtm()` (not a hand-duplicated research clone — the exit-evaluation logic lives inside a nested closure with no exposed seam to monkeypatch, and a duplicate would itself risk the exact drift this project's "Research scripts must verify decision-engine parity" standing requirement exists to prevent).

**Exact change, stated precisely:**
- `evaluate_exit_reason()` (`analysis/decision.py`) takes a single `price` argument, confirmed by direct source inspection to be used **only** for the `price <= stop_price` STOP_LOSS comparison — every other exit reason (`CRISIS_EXIT`/`SIGNAL_OFF`/`MAX_HOLDING`) is unaffected by what gets passed as `price`.
- When `realistic_stop_fill=True`, that day's **low** (not close) is passed as `price` for this check — the trigger becomes "did price touch the stop level intraday," matching the audit's own convention exactly (`low(d) <= stop_price`).
- On a `STOP_LOSS` fire, the fill price becomes `min(stop_price, that day's open)` — the stop price itself if the open was still above it (an achievable intraday fill), or the open if the day gapped straight through (the audit's "gap-through" case).
- Because the existing per-day walk is what discovers the earlier trigger day — nothing is patched in after the fact — this is a genuine re-simulation: an earlier exit frees capacity that day, which changes which other candidates get admitted, which changes their sizing, which compounds forward for the rest of the 19-year run.

**Verified behaviourally transparent at its default**: re-running with `realistic_stop_fill=False` reproduced the established baseline exactly — 3,161 trades, 1,796 `MAX_HOLDING`, 1,169 `STOP_LOSS` — asserted programmatically before trusting the corrected run.

**Reverted immediately after capturing all results below**: `git checkout -- analysis/backtest.py`, confirmed zero diff and the original function signature restored. No production code is left changed by this investigation.

## 2. Headline figures — baseline vs. corrected, full 2007-2026 continuous walk

| Metric | Baseline (current engine) | Corrected (realistic fill) | Change |
|---|---|---|---|
| Total trades | 3,161 | 3,436 | +275 (+8.7%) |
| Distinct tickers | 331 | 341 | +10 |
| `STOP_LOSS` exits | 1,169 | 1,526 | +357 (+30.5%) |
| `MAX_HOLDING` exits | 1,796 | 1,723 | −73 |
| **Total P&L (£)** | **£484,476.39** | **£127,167.80** | **−£357,308.59 (−73.75%)** |
| Win rate | 49.19% | 44.64% | −4.55pp |
| Profit factor | 1.65 | 1.29 | −0.36 |
| Sharpe ratio | 0.607 | 0.331 | −0.276 (−45.5%) |
| Max drawdown (fixed-denominator) | −88.77% | −50.38% | improved (see note below) |
| **Max drawdown (peak-relative)** | **−28.53%** | **−34.48%** | **worse by 5.95pp** |
| **CAGR** | **13.60%** | **7.05%** | **−6.55pp (roughly halved)** |
| **Total return** | **968.95%** | **254.34%** | **down to ~26% of original** |

**Note on the two drawdown conventions, stated explicitly per this project's own standing "state the convention" discipline**: the fixed-denominator figure *improves* under the corrected convention only because it is `(equity − peak) / starting_capital` — a materially smaller peak equity (£254k range vs. £969k range) mechanically produces a smaller fixed-denominator drawdown even though the account fell further from its own high. **The peak-relative figure — the one that actually answers "how far below its own high did this account fall" — is worse, not better, under the corrected convention.** Reporting only the fixed-denominator number here would have been actively misleading.

**More trades, not fewer, under the corrected convention** — a real, re-simulation-only effect the first-order estimate could not show: exiting earlier on a genuine stop touch frees capacity sooner, letting the engine admit additional candidates it otherwise wouldn't have room for that day. This is exactly the kind of cascading, compounding effect a per-trade delta cannot capture.

## 3. The true gap vs. the audit's own first-order estimate — confirmed directly, not estimated

```
TRUE re-simulated overstatement:        GBP 357,308.59  (73.75% of baseline total P&L)
Audit's own first-order estimate:       GBP 212,267.47  (43.81% of the SAME baseline total)
                                         (GBP 45,799.68 gap-through + GBP 166,467.79 late-trigger)

True gap / first-order estimate ratio:  1.68x
```

**The real, fully re-simulated overstatement is nearly 1.7x the audit's own first-order figure.** The audit's own explicit caveat — "Neither number re-simulates... treat these as scale and direction, sufficient to justify a dedicated investigation, not as a restated result" — was correct to hedge this hard: the true effect, once compounding is properly accounted for, is substantially larger than even the urgent first-order flag suggested. This is the expected direction for a compounding strategy (`position_gbp` scales with the account's own current balance): a materially worse early/mid-history stop-out rate slows account growth for the rest of the run, so every later trade is sized smaller in the corrected simulation than in the baseline — an effect a static per-trade delta cannot represent at all.

## 4. Block validation — corrected convention, all 16 windows

```
Baseline:  10/16 blocks passed
Corrected:  9/16 blocks passed
```

| Block | Baseline pass | Corrected pass | Baseline CAGR | Corrected CAGR | Baseline PF | Corrected PF |
|---|---|---|---|---|---|---|
| 0 | ✗ | ✗ | −27.56% | −32.03% | 0.43 | 0.33 |
| 1 | ✗ | ✗ | −15.77% | −21.00% | 0.43 | 0.33 |
| 2 | ✓ | ✓ | 11.45% | 6.65% | 1.85 | 1.54 |
| 3 | ✓ | ✓ | 11.33% | 7.42% | 1.62 | 1.40 |
| 4 | ✓ | ✓ | 22.43% | 17.33% | 2.99 | 2.54 |
| 5 | ✓ | ✓ | 19.70% | 9.96% | 1.90 | 1.48 |
| 6 | ✓ | ✓ | 31.30% | 15.20% | 2.76 | 1.68 |
| **7** | **✓** | **✗** | **16.04%** | **−1.52%** | **1.47** | **0.98** |
| 8 | ✗ | ✗ | 27.08% | 14.40% | 1.75 | 1.53 |
| 9 | ✓ | ✓ | 29.00% | 2.81% | 1.60 | 1.08 |
| 10 | ✓ | ✓ | 22.10% | 6.32% | 1.55 | 1.26 |
| 11 | ✓ | ✓ | 50.03% | 20.71% | 1.85 | 1.54 |
| 12 | ✓ | ✓ | 53.13% | 14.45% | 1.57 | 1.27 |
| 13 | ✗ | ✗ | 57.26% | 16.63% | 1.68 | 1.33 |
| 14 | ✗ | ✗ | 88.70% | 7.78% | 1.52 | 1.05 |
| 15 | ✗ | ✗ | 250.24% | 75.07% | 2.34 | 1.81 |

**Only one block (7) flips from pass to fail** — but this understates the real picture badly if read in isolation. **Every single block, passing or not, shows a materially worse CAGR and PF under the corrected convention** — several previously-comfortable passes (block 9: PF 1.60→1.08; block 14: PF 1.52→1.05) now sit barely above the `PF > 1.0` bar, one bad block away from flipping too. The block-pass-rate headline (10/16 → 9/16) is real but meaningfully understates how much of the reported edge was an artifact of the unrealistic fill convention — the P&L-level finding in Section 2-3 is the more honest measure of scale.

## 5. Explicit flag: does the technical reference document need a revision note? **Yes.**

CLAUDE.md's own "Concrete example, for calibration" paragraph (in the "Two backtest conventions" section) points directly at this exact question — "does the currently-shipped system beat buy-and-hold S&P 500 in £ terms" — as the standing, continuous-walk headline claim for the live-matching engine. The £484,476.39 total-P&L figure this investigation started from (the same run the dividend audit itself analysed) is cited in `CLAUDE_HISTORY.md` (line 8475) as the reference figure for that exact question, and is the most current, most-cited headline output of `simulate_portfolio_compounding_mtm()` in this codebase.

**That figure, and everything computed from it (CAGR, total return, Sharpe, both drawdown conventions, the 16-block pass count), is built on the unrealistic same-day-close-and-exact-stop-price fill convention this investigation corrects. Under the realistic convention, total P&L is ~26% of the previously reported figure, CAGR is roughly halved, and one additional 16-block window fails.** This is not a marginal wording issue — it is a headline-scale change to the system's own reported track record.

**This investigation does not revise that figure — per the task's explicit instruction, that is a separate, deliberate decision, not something to fold into an urgent audit follow-up.** But the honest answer to "does the technical reference document's performance narrative need revisiting" is **yes**, and this should not be left implicit. Any future reader relying on the £484,476.39 / 13.6% CAGR / 968.95% total-return figures, or the "beats buy-and-hold S&P 500" framing built on them, should treat those as **based on an since-identified-unrealistic fill convention**, pending the separate decision on whether and how to correct it.

## 6. What this investigation does NOT decide

- **Whether to change the production `evaluate_exit_reason()`/exit-fill convention.** That is a materially larger decision than fixing a backtest-accuracy issue alone — it touches how every historical figure in this codebase should be read, and whether live's own already-correct broker-side-stop behaviour needs any backtest-side change at all (arguably it does not — live is already realistic; only the *backtest's own self-assessment* was wrong). Not decided here.
- **Whether/how to revise CLAUDE_HISTORY.md's or CLAUDE.md's cited figures.** Flagged in Section 5, not executed.
- **Which of the two intraday-touch corrections (gap-through fill, or early trigger) matters more in isolation.** This investigation tested them together, exactly as the audit specified them as one combined, realistic convention — no attempt was made to isolate their individual contributions to the true re-simulated gap, since the task asked for the real combined number, not a decomposition.

## 7. Confirmation

- **`analysis/backtest.py` was temporarily modified (an additive, marked, isolated change), then reverted** — `git diff analysis/backtest.py` confirmed clean, function signature confirmed restored to its pre-investigation form, verified directly, not assumed.
- No other production files were modified: `config.yaml`, `analysis/decision.py`, `analysis/volatility.py`, `brokers/trading212.py` were all read-only throughout.
- No custom candidate pool or monkeypatched `is_member()` — both the baseline and corrected runs used the real, unmodified admission/entry-gate/sizing logic; only the STOP_LOSS trigger-price and fill-price were changed, and only for the corrected run.
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only queries.
