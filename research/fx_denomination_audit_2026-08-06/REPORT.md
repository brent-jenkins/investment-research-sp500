# FX Denomination, Symmetry, and Conversion Cost Audit (2026-08-06)

**Type: descriptive audit. No mechanism built, no adoption decision made — exactly as scoped.**

**Headline: no FX conversion exists anywhere in this system's price data, backtest engine, or benchmark comparison — every "£" figure this project has ever reported is actually a raw USD number wearing a currency label it was never converted into.** Questions (1) Denomination and (2) Benchmark symmetry therefore collapse into a labelling finding, exactly as the task's own branching logic anticipated. Question (3), conversion cost, is real, live, currently unmodelled, and — against the now-corrected 10.74% CAGR baseline — represents on the order of **3.1% of portfolio value per year**, roughly **29% of the entire return**.

**Location of artifacts:** `research/fx_denomination_audit_2026-08-06/` — this report is the only artifact; Phase 0's checks were cheap enough (grep + a handful of direct price comparisons) that no separate script or CSV output was needed, consistent with the task's own "cheapest possible resolution" framing.

## Prior-work check — was FX in the dividend/fill-convention audit's scope?

**No.** `research/dividend_fill_convention_2026-08-06/REPORT.md` was grepped directly for `FX`, `currency`, `GBP`, `USD`, `exchange rate` — zero matches. That audit covered dividend adjustment, commissions, and the STOP_LOSS fill convention exclusively. FX fell between the cracks entirely, not covered by any prior investigation tonight. This audit is the first to address it.

## Phase 0 — Binary descriptive check

### Code-path trace (checked first, cheapest)

`grep`'d `data/ingestion.py`, `analysis/backtest.py`, `analysis/validation.py`, and `analysis/benchmark_comparison.py` for `fx`, `currency`, `gbpusd`, `eurusd`, `exchange.rate`, `convert` (case-insensitive). **Zero relevant hits** — the only matches were `_yf_symbol()`'s ticker-notation conversion (`BRK.B` → `BRK-B`, unrelated to price/currency) and a comment noting `GBPUSD=X`/`EURUSD=X` are ingested as reference tickers, never a reference to anything *reading* them in this pipeline.

`price_data`'s own schema (`data/database.py:PriceData`) has **no currency column, no conversion metadata field of any kind** — every ticker's OHLCV is stored in whatever raw units yfinance returns, with nothing in the storage layer even capable of representing a currency or a conversion.

This matches, and extends, a finding already made independently earlier tonight (the enriched trade-export investigation): `grep -rn "GBPUSD\|EURUSD\|_fx_rates\|_to_gbp" analysis/*.py` returns zero hits — every real usage of the two stored FX series lives exclusively in `brokers/trading212.py` and `output/performance.py`, for converting a **live, broker-reported** USD/EUR position value into GBP for display — never anywhere in the backtest engine, and never applied to the historical `price_data` a backtest reads.

### Trade-level verification (5 trades, spanning 2008–2025, cross-checked against a fresh, independent yfinance pull)

Picked 5 real trades from the corrected engine's own trade-level output, one from the GFC era and one from post-2022 as required, then pulled **fresh** data directly from yfinance (bypassing the stored DB entirely, so a conversion baked into ingestion couldn't hide from this check):

| Ticker | Entry date | System `entry_price` | Fresh yfinance close (same date, `auto_adjust=False`) | Match |
|---|---|---|---|---|
| AAPL | 2008-01-03 | 6.961785793304443 | 6.961786 | ✓ exact |
| LOW | 2009-01-08 | 21.950000762939453 | 21.950001 | ✓ exact |
| MAR | 2015-01-09 | 78.7300033569336 | 78.730003 | ✓ exact |
| NVDA | 2022-01-03 | 30.121000289916992 | 30.121000 | ✓ exact |
| AXON | 2025-01-03 | 601.0599975585938 | 601.059998 | ✓ exact |

**All 5 match the raw USD close exactly, to float precision, with zero GBP/USD scaling.** For calibration on how obviously wrong a converted value would look: GBP/USD in early 2008 was ~1.97–2.00, so a genuinely GBP-converted AAPL entry would show roughly **13.7–13.9**, not 6.96 — the system's own recorded value is unambiguously the raw USD figure, not a converted one, at every one of the 5 dates checked (also spanning very different historical GBP/USD levels: ~2.00 in 2008, ~1.45 in 2009, ~1.51 in 2015, ~1.35 in 2022, ~1.25 in 2025 — a coincidental match at any single date would be one thing, but matching raw USD across five wildly different FX regimes rules out conversion conclusively).

### Verdict: **unconverted end-to-end.**

Per the task's own branching logic — questions (1) Denomination and (2) Benchmark symmetry collapse. Every reported £ figure in this project (backtests, the benchmark comparison, CLAUDE.md/CLAUDE_HISTORY.md's headline numbers) is a pure USD return wearing a `£` label. **This is a complete, valid, useful finding on its own terms** — no mechanism is missing to go build; the fix, if wanted, is a labelling correction plus a decision about whether genuine GBP reporting is worth building at all.

## Phase 2 — Benchmark symmetry (applies regardless of branch, per the task's own instruction)

**Denomination of the benchmark leg, stated explicitly per the task's requirement to do so in every future comparison output**: the S&P 500 benchmark leg (`analysis/validation.py:_cagr_dd_for_series()`/`_sp500_ending_balance_gbp()`, called from both `run_full_validation()` and the just-run `analysis/benchmark_comparison.py`) reads `^GSPC`'s raw `close` column directly from `price_data` — **the identical unconverted-USD treatment as the strategy leg.** The two legs are at least *internally consistent* with each other (both equally mislabeled), which directly answers one useful sub-question: **FX contributes nothing to the −37.56pp strategy-vs-S&P gap reported in the fresh comparison** — not because FX doesn't matter, but because neither leg has any FX arithmetic in it at all to contribute a bias one way or the other. (Resolving that gap itself is out of scope here, per the task's own instruction — this only addresses whether FX is a contributor, and it is not.)

**Price return vs. total return — checked directly, not assumed.** Queried `price_data` for every stored `^GSPC` row: `close == adj_close` for **100% of rows, zero exceptions**. `^GSPC` as published is a genuine price index — Yahoo Finance provides no dividend-adjusted variant for this specific ticker (unlike individual stocks, where `adj_close` meaningfully differs from `close`, confirmed in the dividend audit). **The benchmark leg is confirmed price-return, not total-return** — it understates the true, dividend-reinvested S&P 500 by roughly the index's own dividend yield (~1.5–2%/year), in the direction the task flagged: this makes the strategy look *relatively better* against the benchmark than it truly is versus a real total-return index. Switching from `close` to `adj_close` would not fix this (they're identical for this ticker) — a genuinely different series (`^SP500TR`, not currently ingested) would be needed.

**Standing principle for any future comparison, stated as required**: whatever treatment (converted or not) is applied to the strategy leg must be applied to the benchmark leg in the same commit. Currently both are symmetric by being equally untreated — the risk this guards against is real for the *next* change, not the current state.

## Phase 1 — Denomination decomposition

**Not built, per Phase 0's own branch instruction** ("If unconverted end-to-end → questions (1) and (2) collapse... Proceed to Phase 3 only"). No trade-level currency-translation decomposition, no era analysis of GBP/USD-driven drawdown flattery, was performed — there is no conversion in the current pipeline to decompose.

**One piece of the motivating evidence directly re-verified, for calibration, not re-derivation of the task's own cited figures**: queried the stored `GBPUSD=X`/`^GSPC` series directly. GBP/USD moved 1.301 (2020-02-18) → 1.149 (2020-03-20) over the COVID crash — a real, ~11.7% GBP depreciation against USD in three weeks, corroborating the task's own cited figures independently. Over the *full* comparison window, GBP/USD is close to flat (1.326 on 2020-01-01 → 1.348 as of 2026-08-01, GBP very slightly *stronger*) — confirming the task's own framing that FX nets out over 2020–2026 even though it moved sharply within it.

**Worth stating plainly, since the direction is counter-intuitive and easy to get backwards**: because nothing is actually converted, the system's reported −33.92% S&P drawdown for the COVID crash is the *harsher*, true-USD figure — not a GBP-flattered one. A genuine GBP investor's real experience over that specific window was shallower (roughly −22% to −25%, using the stored rates directly). So in this specific episode, the current mislabeling makes the reported figure look *worse* than a real GBP investor would have seen, not better — the opposite of the "correction is favourable on the full-history window" framing in the task's opening summary, which is about the *full-history, dollar-strengthening* effect, a separate and much longer-run phenomenon from this one sharp, short-lived, opposite-signed episode. Both are true; they are not the same claim, and blending them would be exactly the kind of single-headline-number error this task exists to prevent.

## Phase 3 — Conversion cost (the live-money question, applies regardless of Phase 0's branch)

**T212's FX fee, verified against current published terms via web search, not assumed**: **0.15% per conversion, confirmed** (Trading 212's own help centre: "the value of the trade will be converted to the stock's currency using the spot exchange rate + 0.15% FX fee"). A round trip (buy then sell) through a currency conversion is **0.30%**, matching the task's own "believed" figure exactly — now independently confirmed rather than assumed. [Source: Trading 212 Help Centre — "What is the FX fee? (Invest & Stocks ISA)"](https://helpcentre.trading212.com/hc/en-us/articles/360018909758-What-is-the-FX-fee-Invest-Stocks-ISA)

**Account currency — assessed from strong, convergent evidence, not a live API check (no account credentials available in this local research session).** Every part of this codebase — `config.yaml`'s `starting_capital_gbp`, every `position_gbp`/`pnl_gbp` field, `_var_position_size_core()`'s own `account_value_gbp` parameter — treats GBP as the account's **native, primary** unit, not a secondary display conversion; `_fx_rates()`/`_to_gbp()` exist specifically to convert *instrument* prices (USD/EUR) into the account's own GBP terms, which only makes sense if the account itself is GBP-denominated. Trading212 Invest (non-CFD) is a GBP-default product for UK users. **Assessed as GBP-funded with high confidence, though not directly confirmed via a live account query** — meaning the FX fee genuinely applies to every single trade (buying a USD instrument converts GBP→USD; selling converts back), not only on deposit/withdrawal. **One sub-question explicitly left open, stated honestly rather than guessed**: whether the *demo* account's own recorded historical fills show direct evidence of this fee being charged could not be checked here — that requires live account access this research session doesn't have.

**Quantified against real, empirical turnover — using the system's actual production settings, not the task's own illustrative 70%/5% figures.** Production config is `max_total_deployed=0.5` (50%, not 70%) / `per_trade_pct=0.05` (5%, matching the illustrative example) — replacing the assumptions as instructed:

```
Real production (50%/5%), from the fresh benchmark comparison just run:
  1,370 trades over 2020-01-01 -> 2026-08-06 (6.595 years) = 207.7 round trips/year
  Annual fee drag (round-trip fee x avg position fraction x turnover):
    207.7 x 0.30% x 5% = 3.12% of portfolio value per year
```

**Against the now-corrected 10.74% CAGR, this unmodelled cost represents ~29% of the entire return — not a rounding error, and materially more significant than it would have appeared against the old, overstated 19.29% figure (~16%), because the stop-fill correction already shrank the headline return this cost is being compared against.** This directly supports the task's own priority framing ("potentially same order of magnitude as the stop-fill correction"). Stated as a caveat: this uses `per_trade_pct` (5%) as the average position-value fraction, which is the VaR sizing formula's *ceiling*, not necessarily its realized average (positions are sometimes sized below it) — this is a reasonable order-of-magnitude estimate, not an exact sum of real per-trade position values, and the true figure could plausibly be somewhat lower.

**Deployment-cap sensitivity, using the real 9-cell trade-off table's own figures (`research/deployment_cap_tradeoff_2026-08-05`) — not re-run, per the task's explicit instruction, only assessed for bias:**

```
70%/5% cell: 1,734 trades, same 2020-2026 window -> 262.9 round trips/year
  Annual fee drag: 262.9 x 0.30% x 5% = 3.94% of portfolio value per year
  (+0.83pp/year MORE unmodelled drag than the 50%/5% baseline)
```

**The 9-cell table's own headline finding — CAGR rising to 30.78% at 70%/5% vs. 19.29% at baseline (both pre-stop-fill-correction figures, that table's own separate, already-flagged limitation) — was computed with zero FX-cost modelling in either cell, and the higher-deployment cell carries genuinely more unmodelled cost (3.94%/yr vs. 3.12%/yr), not the same amount.** This confirms the task's own hypothesis directly: **the table is biased toward higher deployment**, because its highest-turnover, highest-headline-CAGR cells are exactly the ones understating their own true cost the most. This does not overturn the table's qualitative "more deployment, more return, more drawdown" shape, but the *relative* attractiveness of the more aggressive cells is overstated by an amount that grows with deployment — a real, previously unquantified bias in a table this file already flags for re-verification for an unrelated reason (the stop-fill correction). **Not re-run here, per the task's explicit instruction** — flagged for whoever picks up that table's own re-verification to account for both effects together, not just the stop-fill one already on record.

## What this does and does not resolve

- **Resolved**: whether FX conversion exists anywhere in the pipeline (no); whether the benchmark leg is symmetric with the strategy leg (yes, symmetric — both equally unconverted); whether the benchmark is price- or total-return (confirmed price-return); whether FX explains any of the −37.56pp strategy-vs-S&P gap (no — neither leg has FX arithmetic to contribute one); the current T212 FX fee (confirmed 0.15%/0.30% round-trip via published terms); the order-of-magnitude annual cost this represents against real production turnover (~3.1%/year, ~29% of the corrected CAGR).
- **Not resolved, stated honestly**: literal confirmation the demo account is GBP-funded via a live query (assessed with high confidence from convergent code/product evidence, not directly confirmed); whether the demo account's actual recorded fills show FX-fee line items; the precise (vs. order-of-magnitude) fee cost using real per-trade position values rather than the `per_trade_pct` ceiling as a proxy.
- **Out of scope, per the task, and not attempted**: resolving the −37.56pp gap itself; re-running the deployment-cap trade-off table; any change to live trading configuration or code; the 18-item stop-fill re-verification queue.

## Recommendation

**No mechanism built, no adoption decision made — this is a complete Phase 0 (+2 +3) descriptive finding, exactly as scoped.** The headline result — no FX modelling exists anywhere, and a real, currently-unmodelled ~3%/year live trading cost applies to every trade this system places, GBP-funded — is itself the useful output. Whether to (a) build genuine currency-translation modelling for historical reporting accuracy, (b) build a live FX-cost term into position sizing/backtest, or (c) simply relabel existing figures as USD, are all separate, future decisions this audit deliberately does not make.

## Confirmation

- No production files were modified: `analysis/backtest.py`, `analysis/validation.py`, `analysis/benchmark_comparison.py`, `data/ingestion.py`, `data/database.py`, `config.yaml`, `brokers/trading212.py` were all read-only throughout.
- No custom candidate pool or monkeypatched `is_member()` — this audit read existing stored data and ran independent, external verification (a fresh yfinance pull, a web search) with no bearing on any trading decision.
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only queries.
- One external web search was performed (Trading 212's own published FX-fee terms) — no account-specific or authenticated data was accessed.
