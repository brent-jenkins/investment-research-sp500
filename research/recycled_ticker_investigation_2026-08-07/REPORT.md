# Recycled-ticker-symbol corruption — investigation (2026-08-07)

**Scope discipline, confirmed:** investigation only. No schema changes, no data changes, no ticker exclusions, no production code touched. Run entirely against a local, gitignored copy of the production database (`database/investment_research.db`, pulled earlier today: 871 tickers, 3,461,961 rows, through 2026-08-06) — nothing was run on the server. Nothing was written back to the database. `git status` confirms no production files modified by this investigation.

## Origin

The earnings-dampener re-verification (using a corrected, non-survivorship-biased candidate pool — `get_all_known_stock_tickers()`, 1,205 tickers, instead of `get_live_stock_candidates(as_of=today)`, 503) produced a whole-run P&L of £2.21M at deployment cap 0.70 — a 66x jump from £33K at cap 0.50, and 37x higher than the matched `earnings_shrink` variant at the same 0.70 cap (£59K). That divergence was the tripwire. One trade (`TIE`, entered 2011-02-03 at $19.37, exited 2011-02-18 at a raw close of $15,900.00 — an 819x, 81,900% "return") accounted for £467,417 of it directly. This report is the investigation that followed.

## Headline

**The corruption is real, widespread, and specific to the population added by the 2026-08-06 survivorship-bias remediation — not a new problem in the originally-tested 388/503-ticker set.** 39 of 871 tickers in the full database (4.5%) show unexplained price discontinuities; 22 are confidently identified as recycled-ticker-symbol corruption (an unrelated entity's price history sharing a delisted company's old symbol — the same mechanism CLAUDE.md already documents for `CFC`/`NCC`/`WB`). All 22 are among the 408 tickers added specifically to fix survivorship bias; **zero** are in the original 388-ticker population every other finding from today's session was built on.

## 1. Detector methodology and results

`step1_detector.py` (saved in this directory). Ratio scan widened to `[0.2, 5.0]` per the task spec (production's own `detect_price_cliffs()` uses `[0.5, 2.0]`). "Explained by a split factor recorded on that row" is operationalized using the one signal actually available in the schema (`price_data` has no split-factor column): if `adj_close` stays smooth (ratio within `[0.85, 1.15]`) at a transition where `close` jumps, that's a real, recognized adjustment — excluded. If `adj_close` jumps in lockstep with `close`, nothing in the stored data explains it — flagged as **unexplained**.

- **3,436 raw ratio-break transitions** across 41 tickers, full table.
- **2 explained** by `adj_close` staying smooth (genuine, recognized adjustments).
- **3,434 unexplained** — across **39 tickers**.

**Classification** (the key discriminator per the task spec — does the series revisit a price level it already departed from, i.e. reverse direction, vs. move through a monotone one-time sequence of distinct levels):

| Classification | Count | Definition |
|---|---|---|
| LIKELY_RECYCLED | 22 | Series revisits an already-departed price regime — oscillation, consistent with two interleaved, unrelated price series under one symbol. |
| LIKELY_CORPORATE_ACTION | 17 | Every regime is a distinct, never-revisited level — monotone, consistent with a genuine one-time event (a real split, or a real single price collapse). |
| AMBIGUOUS | 0 | (Reserved for insufficient/borderline cases — none arose in this run.) |

Full lists: `LIKELY_RECYCLED` = BMC, BOL, CBE, CFC, CIN, CNG, COMS, CPWR, GDW, HM, HPC, KRI, MCIC, MEE, MII, NCC, PBG, PTV, SRR, TIE, TNB, UCM. `LIKELY_CORPORATE_ACTION` = DEC, DIGI, EP, EQ, FMCC, FNMA, FSH, GLK, HET, MI, ORX, PALM, PDG, POM, RX, USBC, UVN.

**Sanity check the classifier is working correctly:** `CFC` and `NCC` — the two tickers CLAUDE.md already independently confirmed as reused symbols (unrelated mutual funds, found via the 2026-08-06 Universe coverage remediation's own metadata cross-check) — are both correctly classified `LIKELY_RECYCLED` here, with no prior knowledge fed to the detector. `FMCC`/`FNMA` — the two tickers CLAUDE.md already confirmed as the *genuine* companies with a real, single, catastrophic price collapse (2008-09-08 conservatorship) — are both correctly classified `LIKELY_CORPORATE_ACTION` (single transition, monotone).

## 2. Root cause, as far as this investigation can establish it

The 2026-08-06 "Universe coverage remediation" backfilled price data for previously-missing historical tickers using **symbol-based** yfinance lookups — the same mechanism that produces a stale/reused-symbol hit is entirely plausible to recur at scale during a bulk 331-ticker (later 408-ticker, today) backfill, since that original remediation's own metadata cross-check was a **28-ticker sample**, not a full audit (per CLAUDE.md's own record: "9/28 (32.1%) passed mandatory symbol-reuse validation"). The same-day `split_adjustment_audit_2026-08-06` ran its own three-method detection and found **zero cliff artefacts** — but that audit's own stated caveat is exact: it could only check tickers that already had `price_data` at the time it ran, and most of today's 408-ticker population either had no data yet or was backfilled after. Neither of the two 2026-08-06 investigations comprehensively covered this specific population; production's own `detect_price_cliffs()` deliberately scopes *alerting* away from historical/delisted tickers (a correct call for noise reduction, but it means this sat completely invisible until today's attempt to actually *use* the population as backtest candidates).

## 3. (a) Live blast radius — confirmed zero, by direct query

```sql
SELECT COUNT(*) FROM invest_orders WHERE ticker = :t   -- 0 for every one of the 39 affected tickers
SELECT COUNT(*) FROM pending_orders WHERE ticker = :t  -- 0
SELECT COUNT(*) FROM pnl WHERE ticker = :t              -- 0
```
**No affected ticker has ever appeared in any live/held table.** Also confirmed directly: `get_live_stock_candidates()` (today) returns **zero** of the 39 affected tickers — none are reachable as a live trading candidate. This is structurally guaranteed, not coincidental: every one of the 39 is delisted-before-today by construction (see part b), and `get_live_stock_candidates()` filters to current members only. **No live-trading risk from this corruption exists today**, and none is possible under the current architecture unless a ticker were both corrupted *and* a genuine current index member — a combination this investigation found zero instances of.

## 4. (b) Affected-ticker counts, three ways

| | LIKELY_RECYCLED (22) | LIKELY_CORPORATE_ACTION (17) |
|---|---|---|
| In the original 388 (today's earlier VIX/MA/DD, earnings, entry-volume, sector-relative research) | **0** | **0** |
| In the 408 newly added for survivorship-bias correction | 22 | 13 |
| In reference tickers (`^GSPC`, `GBPUSD=X`, `EURUSD=X`) | 0 | 0 |
| In neither tested set (has price_data but <300 rows, excluded from any candidate pool tested today) | 0 | 4 (PALM, PDG, POM, RX) |

**Every one of today's earlier findings (VIX sizing, market-regime gate, peak-DD giveback, earnings-proximity clustering, entry-volume, sector-relative momentum) is unaffected by this specific corruption** — none of the 388 tickers those investigations traded are contaminated. The corruption is fully confined to the population this specific re-verification effort just introduced.

## 5. (c) Momentum impact — quantified, not assumed, and severe

For each `LIKELY_RECYCLED` ticker, its `analysis.signals.compute_momentum_series()` value was compared against the full cross-section of all 796 usable candidates' momentum on the same date, on every date it had a computed value.

**All 22 of 22 LIKELY_RECYCLED tickers produced at least one top-decile (≥90th percentile) momentum reading. 9,339 top-decile-days total across the 22 tickers**, ranging from 4.2% of a ticker's own trading days (GDW) up to 59.3% (HM). Example manufactured momentum peaks: `TNB` 2,018,082%, `CBE` 5,182,500%, `MCIC` 75,900%, `GDW` 799,020%, `CNG` 1,062,431% — obviously not real 12-month returns, directly caused by the price-scale discontinuities feeding straight into the trailing-return calculation.

**This is not incidental exposure — it is systematic, structural preference.** Momentum-ranked, capacity-constrained admission sorts candidates by momentum descending; a manufactured multi-hundred-thousand-percent reading will always win a contested slot against any real candidate. Full detail: `step_c_momentum_impact.csv`.

## 6. (d) Trade/P&L exposure in the corrected-universe re-verification runs

| Variant | Total trades | Total P&L | Affected trades (39 tickers) | Affected P&L | Recycled-only trades | Recycled-only P&L |
|---|---|---|---|---|---|---|
| cap05_baseline | 3,723 | £33,203.17 | 107 (2.9%) | −£10,609.39 (−32.0% of total) | 79 | −£10,156.45 |
| cap05_shrink | 4,317 | £39,918.21 | 101 (2.3%) | −£11,638.33 (−29.2% of total) | 76 | −£11,230.94 |
| cap07_baseline | 5,052 | **£2,211,986.14** | 108 (2.1%) | **+£453,096.56** (20.5% of total) | 82 | **+£453,552.55** |
| cap07_shrink | 5,737 | £59,188.12 | 104 (1.8%) | −£10,971.12 (−18.5% of total) | 81 | −£11,143.59 |

**This directly explains the original anomaly.** In three of four runs, the recycled-ticker population is a modest net *loser* (−£10K to −£12K, consistent with a corrupted series mechanically producing repeated near-total-loss `STOP_LOSS` exits — e.g. `TIE` alone shows six separate −99.9% stop-outs across the four runs, an artifact signature, not real trading). The one outlier — `cap07_baseline`'s +£453,553 — is overwhelmingly the single `TIE` trade (+£467,417) identified at the start of this investigation. That trade's position size differed between `cap07_baseline` and `cap07_shrink` specifically because `TIE` happened to have a real earnings date inside the dampener's window at that exact entry — the dampener *accidentally* suppressed the corruption's impact in one run, masking the underlying problem rather than fixing it. **Every number produced by the corrected-universe re-verification is unsafe to use as-is.**

## 7. (e) `_MAX_ANCHOR_GAP_DAYS` — confirmed does not catch this, and the code already predicted it would not

`analysis/signals.py:41` (`_MAX_ANCHOR_GAP_DAYS = 400`) guards against a *calendar* gap between a momentum anchor and today — its own comment (lines 26-34) explicitly describes this exact recycled-symbol scenario as the motivating concern, but the guard only checks `gap_days = (close.index - anchor_dates).days > 400`, i.e. whether trading days are *missing* from the date index. A recycled symbol's rows are date-contiguous (no missing days — `TIE`'s Feb 2011 jump happens between two consecutive stored trading days, 2011-02-03 and 2011-02-18, a completely normal calendar gap for a stock with no news on some days). **Confirmed directly**: none of the 3,434 unexplained transitions coincide with a calendar gap large enough to trip this guard. The mechanism this guard defends against (a genuine hole in the date index) and the mechanism actually observed here (a same-day price-level discontinuity in a contiguous series) are different failure modes; the guard was never designed to catch the second one.

## 8. (f) Spot-checks against real company identity

| Ticker | Classification | yfinance `.info` today | Verdict |
|---|---|---|---|
| TIE | LIKELY_RECYCLED | `quoteType=MUTUALFUND`, `exchange=YHD`, `longName=None` | **Classification holds.** Symbol currently resolves to an unrelated fund, not Titanium Metals Corp (the real historical S&P 500 constituent). Regime table shows a plausible early period (2007–2010, median ~$13, consistent with real TIE's historical range) followed by oscillation into the thousands from Feb 2010 onward. |
| BMC | LIKELY_RECYCLED | `quoteType=MUTUALFUND`, `exchange=YHD`, `longName=None` | **Classification holds.** Regimes alternate repeatedly between ~$700–900 and ~$11,000–22,000 from 2012 onward — real BMC Software was acquired/went private in 2013; nothing in this pattern is a plausible real corporate history. |
| MEE | LIKELY_RECYCLED | `quoteType=MUTUALFUND`, `exchange=YHD`, `longName=None` | **Classification holds**, with a caveat: regimes bounce between values as low as $0.10–0.30 and as high as $25+ repeatedly, starting from the very first stored row (2007-08-03) — unlike TIE, there is no obviously-clean early real-company period visible in the stored data at all. Real Massey Energy was acquired by Alpha Natural Resources in 2011; this series does not look like genuine MEE history at any point checked. |
| FNMA | LIKELY_CORPORATE_ACTION | `quoteType=EQUITY`, `longName=Federal National Mortgage Association` | **Classification confirmed correct** — matches CLAUDE.md's own prior, independent finding (real 2008-09-08 conservatorship collapse). The stored median-close jump ($52.94 pre-window → $1.54 post-window) is a whole-regime median over a ~20-month span, not the specific day-of-collapse closes CLAUDE.md quotes ($7.04→$0.73) — different statistics over different windows, not a contradiction. |
| PALM | LIKELY_CORPORATE_ACTION | `quoteType=MUTUALFUND`, `exchange=YHD`, `longName=None` | **Classification does NOT hold — a real misclassification, found and reported, not hidden.** `price_data`'s entire stored `PALM` history runs 2014-12-22 → 2020-01-08. The real Palm, Inc. was acquired by HP and delisted in mid-2010 — there is **zero overlap** between the stored data and the real company's actual trading window. Every row is the recycled entity; PALM was only classified `LIKELY_CORPORATE_ACTION` because its regimes happen to move monotonically (never revisiting a prior level) rather than oscillating. |

**This spot-check surfaces a real, structural blind spot in the reversal-based classifier, not just a mislabeled example.** The reversal test correctly identifies *oscillating* recycled series but cannot distinguish a *monotone* recycled series (a replacement entity's price history that happens to trend one direction) from a genuine one-time corporate action. **The `LIKELY_CORPORATE_ACTION` bucket (17 tickers) should be read as "does not exhibit the oscillation signature," not as "confirmed clean."** At least one of the 17 (PALM) is confirmed corrupted despite the label; the other 16 have not been individually re-verified by this investigation and should not be assumed safe without the same identity check applied here.

## 9. Recommendation

**The recovered 408-ticker population is not safely usable as-is.** At minimum, the 22 `LIKELY_RECYCLED` tickers are confirmed corrupted and demonstrably distort both trade-level P&L (part d) and the momentum-ranked admission mechanism itself (part c, the more serious of the two — this doesn't just add a few bad trades, it systematically displaces legitimate candidates whenever a manufactured momentum spike occurs). The `LIKELY_CORPORATE_ACTION` bucket cannot be trusted without further, per-ticker verification — this investigation's own spot-check found one of five checked tickers (PALM) to be corrupted despite its label, and made no attempt to check the other 16.

This does not mean the survivorship-bias fix itself was a bad idea — the underlying motivation (296 delisted tickers with real, usable price data, per the earlier finding) remains sound. It means the recovery needs the same identity-verification rigor the original 2026-08-06 remediation applied to only a 28-ticker sample, applied here to the full 39 flagged tickers at minimum, and ideally to the full 408 before any of them are trusted in a backtest candidate pool. That fix, and any resulting re-verification of the earnings dampener or anything else, is a decision for you, not made here — this report is investigation only, per the task's own instruction.

## Files

- `step1_detector.py` — the full detection/classification script (re-runnable).
- `step1_flagged_transitions_raw.csv` — all 3,436 raw ratio-break transitions, with `explained_by_adj_close`.
- `step3_classified_tickers.csv` / `.json` — per-ticker classification, transition counts, and (JSON only) full regime tables.
- `step_c_momentum_impact.csv` — per-ticker top-decile-momentum-day counts and manufactured peak values.
- `step1_run.log` — full detector run output.
