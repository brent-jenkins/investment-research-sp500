# PART 2 — rename / alias investigation (2026-08-23). READ-ONLY. Nothing built.

**ANSWER: renames are real, small, and currently harmless — and the reason they are harmless is not
the one the hypothesis assumed.** The expected failure was "the new symbol has a short contiguous
series, so a momentum window is silently truncated." That does not happen: **Yahoo backfills the
company's ENTIRE pre-rename history under the new symbol**, so a renamed ticker arrives with a full
2007-onward series, not a short one.

**Measured impact: 3 of 565 trades involve any rename-affected symbol, totalling −£28.58 of
£348,548.74 (−0.01%), and all three had 507–624 rows available at entry — no truncation.** On the
evidence this closes the question, and the recommendation is **do not build an alias map**.

The genuinely dangerous shape — a member with no usable price data — **does exist**, is much larger
than the rename population, and is **already documented as this project's survivorship gap**. Renames
are a subset of that mechanism, and the better-behaved subset. Detail in §5.

No database writes. `verify_ticker_identity()` was confirmed write-free by AST inspection before being
called (its callers do the writing).

**Database**: the LOCAL research copy — `price_data` through 2026-08-07, 871 tickers; 1,204
`universe_membership` tickers; live pool 503. Named per the standing rule. This copy predates
`ticker_fundamentals` and is used here only for `price_data` and `universe_membership`, both complete.

---

## (a) How many tickers are affected by a known symbol change, 2007–2026?

**11 rename events**, derived from Wikipedia's "Selected changes to the list of S&P 500 components"
table (386 rows dated 2007-01-01 or later, of 407 total; cached 2026-08-07 by the
`needs_review_adjudication` work, reused rather than re-fetched) by selecting rows whose **`reason`
text explicitly describes a rename or ticker/symbol change** — not by recalling which companies
renamed.

| date | reason (truncated) | old | new | old rows | new rows | new in live pool |
|---|---|---|---|---|---|---|
| 2010-06-30 | Company split. QEP retained, but ticker changed | STR | QEP | 0 | 0 | no |
| 2012-10-02 | Old Kraft Foods renamed Mondelez | KFT | MDLZ | 0 | 4,930 | **YES** |
| 2016-01-05 | WSH merges with TW (and renames to WLTW) | FOSL | WLTW | 4,930 | 0 | no |
| 2017-09-01 | Dow Chemical renamed to DowDuPont, ticker changed | DOW | DWDP | 1,856 | 0 | no |
| 2017-11-15 | QuintilesIMS changed its name to IQVIA | Q | IQV | 196 | 3,332 | **YES** |
| 2019-06-11 | BMS changed to AMCR post merger with Amcor | BMS | AMCR | 3,130 | 3,578 | **YES** |
| 2020-03-02 | Gardner Denver / Ingersoll Rand | XEC | IR | 0 | 2,322 | **YES** |
| 2022-01-10 | Willis Towers Watson changed WLTW → WTW | WLTW | WTW | 0 | 4,930 | **YES** |
| 2023-07-10 | Everest Re renamed to Everest Group | RE | EG | 0 | 4,930 | **YES** |
| 2024-02-01 | Ceridian changed its ticker CDAY → DAY | CDAY | DAY | 0 | 0 | no |
| 2024-03-25 | FLEETCOR renamed to Corpay | FLT | CPAY | 0 | 3,933 | **YES** |

**A caveat on the table's own pairing, stated rather than glossed**: the `added_ticker`/`removed_ticker`
columns are the index changes announced on that DATE, and are not always the rename's own two symbols.
`FOSL → WLTW` is the clearest case — Fossil was removed for unrelated reasons the same day WLTW was
added. **The `reason` text identifies the event; the two columns do not reliably identify the pair.**

**A structural derivation was attempted first and is reported as having failed**, because the failure
is informative. The signature "old symbol's window closes, a new one opens within 21 days, old has no
price data, new's price history predates its own window" produced **308 candidate pairs** — a
cross-product, because condition (4) is true of almost any established company joining the index, not
just a renamed one (`FLT` alone paired with five different new symbols on one date). An upper bound
that loose is not a measurement. The name/reason evidence is what makes the count defensible, and this
is the same conclusion the `NEEDS_REVIEW` adjudication reached: structure narrows, names decide.

## (b) What does a rename actually do to the stored data?

**Consistent across every case checked, and the opposite of the hypothesis:**

| pair | old symbol | new symbol |
|---|---|---|
| FB → META | 0 rows, no windows | **3,575 rows from 2012-05-18** (Facebook's real IPO), window opens 2013-12-23 |
| UTX → RTX | 0 rows, window 1996→2020-03-03 | **4,930 rows from 2007-01-03**, window opens 2020-04-03 |
| ANTM → ELV | 0 rows, window 2002→2022-06-21 | **4,930 rows from 2007-01-03**, window opens 2022-06-28 |
| WLTW → WTW | 0 rows, window 2016→2021-12-20 | **4,930 rows from 2007-01-03**, window opens 2022-01-10 |
| ABC → COR | 0 rows, window 2001→2023-08-25 | **4,929 rows from 2007-01-03**, window opens 2023-08-30 |

**The old symbol goes dataless and out of the pool; the new symbol arrives with the company's full
history.** Yahoo serves the complete series under the current symbol and nothing under the retired
one. The membership source closes one window and opens the other, so **eligibility follows the rename
automatically** — the system tracks the company correctly without knowing a rename occurred.

**One anomaly worth recording rather than smoothing: FISV.** `FISV` has 4,928 rows through 2026-08-06
**and is a current member** (windows `2001-04-02→2023-05-16` and `2025-11-11→2099-12-31`), while `FI`
has 0 rows and a closed window `2023-06-07→2025-11-04`. The source therefore shows the symbol reverting
after ~2.5 years. Not investigated further — it is stable, has data, and is in the pool — but it is the
one case in this set that does not fit the clean one-way pattern.

## (c) Would `_MAX_ANCHOR_GAP_DAYS` catch it? — No, and it is not what protects us

`compute_momentum_series()` computes `close / close.shift(252) - 1` and NaNs any row whose 252-back
anchor is more than **400 calendar days** away. **That guard is for a GAPPED series.** A renamed
symbol's series is short-and-contiguous or (as measured above) full-and-contiguous — either way the
gap test never fires. Confirmed as predicted, not assumed.

**What actually protects the system is a different mechanism: insufficient rows.** With fewer than 252
rows `shift(252)` yields NaN, momentum is NaN, `compute_signal_series()` resolves NaN to `False`, and
the ticker simply cannot be admitted. **The failure mode is fail-safe — a missed opportunity, never a
wrong trade** — but it is worth being explicit that this is an accident of the row-count requirement
rather than the staleness guard doing its job.

## (d) Does `verify_ticker_identity()` catch it? — No, and it structurally cannot

Run live on both sides of three pairs:

| ticker | status | quote_type | long_name |
|---|---|---|---|
| FB | **QUARANTINED** | `ETF` | **ProShares S&P 500 Dynamic Da…** |
| META | CLEAN | EQUITY | Meta Platforms, Inc. |
| UTX | **QUARANTINED** | `NONE` | None |
| RTX | CLEAN | EQUITY | RTX Corporation |
| ANTM | **QUARANTINED** | `NONE` | None |
| ELV | CLEAN | EQUITY | Elevance Health, Inc. |

**The check is per-symbol and has no cross-symbol notion, so a rename is invisible to it as a rename.**
It quarantines the retired symbol — correct, and useful — and passes the new one as CLEAN. It never
records that the two are the same company, and could not without a second input.

**An incidental confirmation worth keeping: `FB` has been RECYCLED.** It now resolves to a ProShares
ETF, and the recycled-symbol check catches it exactly as designed. That is the *loud* failure class
working. `UTX`/`ANTM` return a populated-but-degenerate `quoteType: 'NONE'`, which quarantines under
the same rule as the validated PALM/PDG/RX precedent — correctly, since neither symbol should be
trusted again.

## (e) What would the impact have been? — Immaterial, measured

**Cross-referenced every rename-involved symbol (old and new, all 11 events) against the preserved
trade sets for the current live configuration:**

| pool | trades | involving a rename symbol | P&L from those | share of total |
|---|---|---|---|---|
| Pool 2 CLEAN | 565 | **3** (all `BMS`, Jan–Jul 2009) | **−£28.58** | **−0.01%** of £348,548.74 |
| Pool 1 | 522 | **0** | — | — |

**And none was truncated: rows available at entry were 507, 615 and 624** — all comfortably past the
252 the momentum window needs.

**Current exposure is zero, checked directly**: of 503 live-pool tickers, **0 have no price data**, and
**490 have price history starting at or before their membership did**. The 13 exceptions are all
genuine listing events, not renames — `Q` (196 rows, 2025 listing), `SNDK` (the confirmed 2025 Western
Digital spinoff), `CEG`, `DOW`, `FOXA`/`FOX`, `IR`, `DELL`, `HLT`, `HCA`, `KMI`, `DG` — and a
short-series name simply produces NaN momentum until it has 252 rows.

## §5 — The larger problem this sits inside, which is NOT new

Scanning every membership window for member-days with no usable price data found **292 windows with
more than 200 missing member-days, 230 of them with no data at all** — including names as ordinary as
`BK`, `MMC`, `K`, `IPG`, `WBA`, `HES`, `JNPR`, `MRO`, `DFS`, `CMA`.

**This is the already-documented survivorship gap** (the 370 tickers the 2026-08-06 remediation could
not recover, out of ~701), not a rename defect: yfinance stops serving a symbol once the company is
acquired, delisted or renamed away, so a ticker that left the index has no retrievable history at all.
**Every one of those windows is closed** — only 3 are open and in today's pool (`FOXA`, `IR`, `FOX`),
and all 3 have full recent series.

**The distinction that matters: this is a BACKTEST-COVERAGE problem, not a live-trading one**, and it
is already recorded as such. Renames are a subset of the same mechanism and the *better-behaved*
subset, because the company's history survives under the new symbol instead of vanishing.

## Recommendation

**Do not build an alias map.** It would address 11 events over 19 years whose measured impact is
−0.01% of P&L across 3 trades, none truncated, with zero current exposure — and it would not touch the
230-ticker gap that actually costs coverage, because that gap is missing data, not mislabelled data.

**Two things worth doing instead, neither urgent, both cheap:**
1. **Record the `FISV`/`FI` reversion** so it is a known case rather than a surprise if it moves again.
2. If rename handling is ever revisited, the trigger should be **a live-pool ticker with fewer than 252
   rows**, which is the only condition under which a rename could cost anything — and it is directly
   checkable, unlike a rename itself.

## Files

`investigate.py` / `investigate.json` (parts b and d), `population.py` / `population.json` (the failed
structural derivation, kept because the failure is the argument for the name-based one),
`wikipedia_renames.json` (part a). All read-only; no database writes, nothing built.
