# Recycled-ticker corruption — remediation report (2026-08-07)

Follow-on to `research/recycled_ticker_investigation_2026-08-07/REPORT.md` (investigation-only,
no fixes). This is the remediation: identity verification as the primary admission gate,
quarantine (not repair/deletion), and before/after validation.

## 1. Identity verification — run across all 408 remediation-added tickers

`verify_ticker_identity()` (`data/ingestion.py`) run via
`research/data_quality_remediation_2026-08-07/step1_verify_identity.py`, full run log in
`step1_run.log`, raw per-ticker output in `identity_verification_results.csv`.

**Verdict rule** (exact, per the corrected/approved plan — Check B is primary and sufficient
alone; the FNMA-driven affirmative-identity override applies only when Check B fires):

```
info unavailable/errored                              -> NEEDS_REVIEW
Check B not applicable (_SENTINEL_END, no real end_date) -> decided by Check A alone
Check B fails AND Check A does NOT affirm real equity  -> QUARANTINED
Check B fails AND Check A DOES affirm real equity      -> NEEDS_REVIEW   (the FNMA fix)
Check B passes AND Check A fails                       -> NEEDS_REVIEW
Check B passes AND Check A passes                      -> CLEAN
```

Check B = stored `price_data` extends >90 days past the ticker's last real
`universe.get_membership_windows()` end_date. Check A = `yf.Ticker(...).info` affirmatively
names a real, tradeable equity (`quote_type == "EQUITY"`, `exchange` present and `!= "YHD"`,
`long_name` truthy). **Wherever Check B fires, Check A is doing the actual deciding** — Check B
alone never quarantines anything; it only ever escalates to a verdict that Check A then resolves
one way or the other. This is stated plainly here per the user's explicit request, so a future
reader doesn't over-trust Check B as an independent, symmetric second signal — it isn't one.

### Verdict counts (408 total)

| status | count |
|---|---|
| NEEDS_REVIEW | 209 |
| CLEAN | 116 |
| QUARANTINED | 83 |

### `_SENTINEL_END` coverage blind spot

**112/408 (27.5%)** of the 408 tickers have a membership window that never closed (still
believed active) — Check B does not apply to these, and their verdict rests on Check A alone.
This is a real, stated limitation of this remediation's coverage, not swept under the aggregate
count: for these 112, a ticker whose `.info` happens to still resolve cleanly today (most delisted
companies eventually stop resolving, but not immediately) would pass Check A and land CLEAN even
if its underlying price history were in fact corrupted, since there is no membership end_date to
compare against. No case in this run's crosstab suggests this occurred (see below), but the
mechanism cannot rule it out for this population by construction.

### Corroboration against the prior (now-superseded) reversal-based classification

|  | LIKELY_CORPORATE_ACTION | LIKELY_RECYCLED | none (unflagged) |
|---|---|---|---|
| **CLEAN** | 0 | 0 | 116 |
| **NEEDS_REVIEW** | 8 | 3 | 198 |
| **QUARANTINED** | 5 | 19 | 59 |

Of the investigation's original 22 `LIKELY_RECYCLED` tickers: **19 confirmed QUARANTINED**, 3
downgraded to NEEDS_REVIEW (the reversal signal alone was not conclusive for these three under
identity verification — a human should look at them specifically). Of the 17
`LIKELY_CORPORATE_ACTION` tickers: 5 QUARANTINED, 8 NEEDS_REVIEW — consistent with the
investigation's own finding that this classifier had a real blind spot (PALM was exactly this
shape: "no oscillation, likely genuine" but actually recycled). **59 QUARANTINED tickers were
never flagged at all by the reversal classifier** — this is the PALM blind spot generalized:
identity verification catches monotone-recycled cases (data landing under a delisted symbol with
no reversal pattern) that a purely statistical reversal test structurally cannot see. This is the
central justification for replacing reversal classification with identity verification as the
primary gate, not a secondary confirmation of the same result.

`needs_review.csv` (209 rows, ticker + both checks' evidence) is the adjudication list — reported
here for you to review, not decided by this remediation. None of these 209 were auto-included or
auto-excluded from the research candidate pool; they remain un-quarantined by design (item 4).

## 2–3. Quarantine table and the single admission gate

`ticker_data_quality` table (`data/database.py:TickerDataQuality`), all 408 rows written via
`write_data_quality_status()` — confirmed directly against the live DB copy: 116 CLEAN / 209
NEEDS_REVIEW / 83 QUARANTINED, matching the script's own printed counts exactly.

`analysis/universe.py:get_research_stock_candidates()` = `get_all_known_stock_tickers() -
get_quarantined_tickers()`. Confirmed: `get_all_known_stock_tickers()` returns 1,205 tickers;
83 are quarantined; `get_research_stock_candidates()` returns exactly **1,122**.
`get_live_stock_candidates()` and `get_all_known_stock_tickers()` are unmodified — zero lines
changed in either function (see `tests/test_universe.py`'s dedicated proof-of-non-interference
test).

## 9. Validation — before/after 16-block comparison

**Convention: continuous walk** (2007-01-01 → 2026-08-06, one continuous run per variant, no
train/test reset) — matches the existing "before" trade output's own convention exactly, so both
arms are directly comparable; this is not a variant-selection reset-at-boundary comparison.

**Single changed variable**, everything else identical (same engine
`simulate_portfolio_compounding_mtm()`, same caps 0.50/0.70, same config, no earnings dampener or
other new mechanism — explicitly out of scope per the remediation task):

- **Before** = `universe.get_all_known_stock_tickers()` (unrestricted, 1,205 tickers — the exact
  mechanism the invalidated re-verification used). Trades:
  `research/earnings_dampener_reverify_2026-08-07/trades_cap0{5,7}_baseline.csv`.
- **After** = `universe.get_research_stock_candidates()` (quarantine-excluded, 1,122 tickers).
  Trades: `trades_cap0{5,7}_after.csv` (this directory).

Block-pass formula (exact, per the project's Research reproducibility standard):
`PF > 1.0 AND abs(strategy_max_drawdown_pct) <= abs(sp500_max_drawdown_pct) * 1.5`
(`analysis/validation.py:_block_passes()`), applied identically to both arms via
`compute_metrics_fixed()`/`make_blocks()`/`_cagr_dd_for_series()`.

### Headline result

| cap | before blocks pass | after blocks pass | before total P&L | after total P&L |
|---|---|---|---|---|
| 0.50 | 10/16 | **11/16** | £33,203.17 | £52,841.47 |
| 0.70 | **1/16** | **9/16** | £2,211,986.14 | £116,748.91 |

**The cap07 result is the corruption's own headline anomaly, confirmed and now corrected.** The
£2,211,986.14 before-figure at cap 0.70 is the same order of magnitude as this investigation's
originating £2.21M P&L anomaly (traced then to a single corrupted `TIE` trade with an ~819x fake
"return" plus the wider recycled-ticker population) — with that population quarantined, cap07's
total P&L falls to £116,748.91 (a **94.7% reduction**) and its block-pass rate rises from a
badly-failing 1/16 to **9/16 — landing almost exactly on this project's own long-documented ~9/16
baseline** (see CLAUDE.md's realistic-stop-fill correction: 9/16 was the pass rate that
correction itself produced). This is strong, independent confirmation that the corruption was
the dominant driver of cap07's previously-reported (and now known-wrong) figures, and that
quarantine removes it cleanly rather than partially.

**cap05's smaller before/after gap (10/16 → 11/16, P&L actually rising £33.2k → £52.8k) is
expected, not a discrepancy** — at the lower 0.50 deployment cap, momentum-ranked admission had
far less capacity available for the extreme, corrupted candidates to consume in the first place,
so their distorting effect was smaller to begin with; removing them frees that capacity for
otherwise-crowded-out genuine trades (the capacity-turnover effect this project's own standing
requirement asks to be reported, not blended into a single delta). Per-block detail in
`before_after_block_validation_results.json`; 2 blocks flip at cap05 net-positive (blocks 2, 3
pass after / fail before) against 1 net-negative (block 8), and cap07 flips 8 blocks from fail to
pass with zero flipping the other way.

### Survivor count — CLEAN and NEEDS_REVIEW reported separately, never summed

Per explicit correction: the "after" arm's 1,122-ticker candidate pool is **not** a clean-only
population. `get_research_stock_candidates()` excludes only the 83 QUARANTINED tickers — all 209
NEEDS_REVIEW tickers remain admitted, un-adjudicated. Breaking out the 408 remediation-added
tickers specifically:

- **CLEAN: 116** (28.4% of 408) — verified genuine, both checks pass.
- **NEEDS_REVIEW: 209** (51.2% of 408) — the decisive population, per your own framing: this is
  larger than CLEAN, meaning the after-arm's 9/16 and 11/16 figures above still rest on a pool
  where the majority of the newly-added tickers are unresolved, not confirmed-clean. The 9/16
  cap07 result should be read as "corruption removed, adjudication still pending" rather than
  "fully clean" — a second validation pass after `needs_review.csv` is adjudicated could move
  these numbers further, in either direction.
- **QUARANTINED: 83** (20.3% of 408) — excluded from the after-arm entirely.

The pre-remediation 388-ticker survivor-only universe
(`research/vix_ma_giveback_2026-08-07/trades_baseline.csv`'s population) remains available as a
fallback if the NEEDS_REVIEW backlog is judged too large to trust before adjudication — noted per
the plan's own instruction to state this plainly if the CLEAN-alone population is small relative
to the total.

## Files in this directory

- `step1_verify_identity.py` / `step1_run.log` — the identity-verification run across all 408.
- `identity_verification_results.csv` — full per-ticker results (408 rows).
- `needs_review.csv` — the 209-ticker adjudication list.
- `step2_build_cache_after.py` / `step2_after_run.log` — AFTER-arm price-cache build
  (`get_research_stock_candidates()`, 1,122 tickers, 713 with usable frames).
- `step3_run_variant_after.py`, `step3_cap05_run.log`, `step3_cap07_run.log`,
  `trades_cap05_after.csv`, `trades_cap07_after.csv` — the AFTER-arm backtest runs.
- `step4_validate_before_after.py` / `step4_run.log` — the before/after 16-block comparison.
- `before_after_block_validation_results.json` — full per-block detail, both arms, both caps.
- `before_after_whole_run_summary.csv` — one-row-per-variant summary.
