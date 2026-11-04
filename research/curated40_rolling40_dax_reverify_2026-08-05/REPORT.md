# Report: Re-verifying curated-40 / rolling-40 / DAX against the corrected engine (2026-08-05)

## 1. Original findings, reconstructed from CLAUDE_HISTORY.md, with git-log-verified dates

### 1a. Timeline (git-log-verified, not prose-only)

| Event | Commit | Timestamp |
|---|---|---|
| DAX corrected finding | `b527f6b` | 2026-07-31 19:27:40 +0100 |
| Price-sanity check introduced | `4b554ec` | 2026-07-31 18:39:19 +0100 |
| Curated-40 vs rolling-40 investigation (Parts C+D writeup) | `36b90d7` | 2026-08-02 10:00:43 +0100 |
| "No market-cap gate at all" (reset-at-boundary 3-way table) | `e147f2f` | 2026-08-02 11:40:25 +0100 |
| Second reproduction of full-500 finding | `002bbe4` | 2026-08-02 12:29:25 +0100 |
| **INDEX_REMOVAL removed from exit priority** (`029e2ec`, "Let momentum, not index removal, govern already-open positions") | `029e2ec` | 2026-08-02 21:57:15 +0100 |
| Re-entry cooldown parity fix | `2e13780` | 2026-08-03 12:51:41 +0100 |
| Re-entry cooldown set to 0 (config) | `3982e91` | 2026-08-03 13:30:28 +0100 |
| Momentum floor implemented | `47bd128` | 2026-08-03 19:01:48 +0100 |

**Correction to the task brief's own framing, found by checking git log rather than trusting prose order**: price-sanity (`4b554ec`) predates **all three** studies, not just some of them — it landed 48 minutes before the DAX correction itself, same evening. This turns out not to matter for the verdict (see §1c), but the ordering claim needed checking, not assuming.

**A fourth, independently-discovered production change relevant to DAX specifically, not named in the task brief**: `029e2ec` (2026-08-02 21:57) removed `INDEX_REMOVAL` from `evaluate_exit_reason()`'s priority chain entirely — a real, deliberate, evidence-based change (91% of real historical index-removal exits had positive momentum) that applies to **all** backtest engines, including the frozen `simulate_trades()` DAX used. This was found by diffing `simulate_trades()`'s source between the DAX commit and HEAD (`git show b527f6b:analysis/backtest.py` vs current), not by trusting the "frozen/exempt" framing at face value — the function is exempt from the *three named* fixes, but not frozen in any absolute sense. This is exactly the kind of thing the task's "cross-check every date claim against real git history" instruction exists to catch, and it turned out to be load-bearing for DAX's result (§3).

### 1b. Curated-40 / rolling-40 / full-500 — original findings

**What was tested** (`CLAUDE_HISTORY.md`, "No market-cap gate at all — the 'broadening dilutes' finding reverses under the corrected engine", 2026-08-02): three candidate universes run through `simulate_portfolio_compounding_mtm()` (the engine confirmed to match live), all sharing the same real, point-in-time S&P 500 membership pool (503 candidates → 500 after excluding `FDXF`/`HONA`/`Q` for insufficient history):
- **Curated-40**: `analysis.universe.STOCK_MEMBERSHIP`'s 40 hand-picked tickers.
- **Rolling-40**: quarterly (67 reconstitutions, 2009-12-31→2026-06-30) top-40-by-market-cap, market cap from the since-deleted `analysis/market_cap.py`'s SEC-EDGAR-XBRL pipeline.
- **Full-500**: the full 500-candidate pool, no market-cap gate at all — real membership is the only gate.

**Backtest convention, stated explicitly per this file's own requirement**: **reset-at-boundary**. Each variant reset to exactly £50,000 (and zero open positions) the first event date on or after 2020-01-01, via `simulate_portfolio_compounding_mtm_reset_at_boundary()` — a wrapper built by `inspect.getsource()`-introspecting the real function, verified byte-identical to the unmodified function when the reset is inert. Test period = 2020-01-01 → 2026-07-31.

**Original headline table** (£50,000-reset basis, test period):

| | Curated-40 | Rolling-40 | Full-500 |
|---|---|---|---|
| Trades | 1,073 | 1,091 | 1,182 |
| Win rate | 47.53% | 45.83% | 48.82% |
| Profit factor | 1.77 | 1.56 | 1.77 |
| CAGR | 14.31% | 11.08% | 19.83% |
| Max DD (fixed-denom) | −15.83% | −11.89% | −27.43% (peak-relative: −11.19%) |
| £50,000 → | £119,600.48 | £99,239.48 | £162,802.54 |

**Verdict at the time**: full-500 beats both gated variants on CAGR and *matches* curated-40's best-in-class PF (both 1.77), at a real, quantified drawdown cost. Config at the time (2026-08-02): re-entry cooldown had **no mechanism at all** in this engine (the parity fix, `2e13780`, didn't land until 2026-08-03 12:51 — a full day later); the momentum floor did not exist as a concept at all (`min_momentum_pct` was implemented 2026-08-03 19:01, `47bd128`).

### 1c. DAX — original finding

**What was tested** (`CLAUDE_HISTORY.md`, "DAX (European stocks-only) — corrected 2026-07-31", commit `b527f6b`, 19:27): `analysis.backtest.run_candidate()`/`simulate_trades()` — the uncapped, non-compounding, per-ticker-independent VaR-sized engine, the only engine that existed at the time (`simulate_portfolio`/`simulate_portfolio_compounding`/`simulate_portfolio_compounding_mtm` weren't added until 2026-08-01, per `git log --oneline -S"def simulate_portfolio_compounding" -- analysis/backtest.py`). A real, point-in-time `DAX_MEMBERSHIP` table (all 40 candidates) was built from STOXX's own official "Historical Index Compositions of DAX Equity Indices" PDF, benchmarked against `^GDAXI`.

**Backtest convention**: not stated explicitly in the original text (predates the "Two backtest conventions" section, added 2026-08-02, `c5cc128`) — but moot for this specific engine: `simulate_trades()` has no compounding/balance-carryover concept at all (every ticker sizes independently off the same fixed notional baseline), so continuous-walk and reset-at-boundary are numerically identical for it. Confirmed as a continuous 2007–2026 walk, sliced into 16 blocks after the fact via `make_blocks()`.

**Original headline table**:

| | Strategy (point-in-time) | `^GDAXI` benchmark |
|---|---|---|
| Train (2007-2019) CAGR | 6.71% | 5.41% |
| Train max DD | −14.45% | −54.77% |
| Test (2020-2026) CAGR | 10.06% | 10.37% |
| Test max DD | −18.56% | −38.78% |
| Full CAGR | 6.22% | 7.11% |
| Full max DD | −18.56% | −54.77% |
| Profit factor (train/test) | 1.46 / 1.41 | — |

**Blocks passed: 12/16 (75%)**, failing blocks 0, 1, 9, 12 (0-indexed, `_block_passes()`: DD ≤ 1.5×benchmark AND PF > 1.0).

**Verdict at the time**: real, substantial risk reduction (drawdown roughly a third of the benchmark's) but **not** clearly better absolute return.

## 2. Fresh results against the current, corrected engine

**Environment verified current before anything else**: `research/curated40_rolling40_dax_reverify_2026-08-05/` created; a working copy of `database/investment_research.db` (527,179,776 bytes, `price_data` covering 2007-01-01→2026-08-03, 540 distinct tickers) copied to an isolated `work.db` inside the research directory — never the production DB — and deleted after all figures were captured. `git status` at session start showed only `database/` untracked; confirmed clean at session end (a concurrent session's edits to `README.md`/`brokers/trading212.py`/`config.yaml`/`data/ingestion.py`/two test files were observed mid-session and had landed as a real commit by the time I checked again — not caused by, or interfering with, this investigation, which never edited a tracked file). Current code (`3496224`, `git log -1`).

### 2a. Curated-40, full-500, rolling-40 — reset-at-boundary, current config

All three re-run via `simulate_portfolio_compounding_mtm_reset_at_boundary()` (the identical reset-wrapper technique the original study used, rebuilt fresh via source-introspection against **today's** `simulate_portfolio_compounding_mtm()`, re-verified byte-identical to the real function when the reset is inert, for each of the three candidate pools separately). **Backtest convention: reset-at-boundary**, £50,000 reset at 2020-01-01, test period 2020-01-01→2026-08-03 (3 days past the original's 2026-07-31 end — the latest date `price_data` covers). **Config: today's real production defaults**, read live from `config.yaml`: `reentry_cooldown_days: 0`, `min_momentum_pct: 0.02`, `per_trade_pct: 0.05`, `max_total_deployed: 0.5`.

| | Curated-40 | Rolling-40 | Full-500 |
|---|---|---|---|
| Trades | 1,063 | 1,099 | 1,184 |
| Win rate | 48.35% | 46.50% | 49.49% |
| Profit factor | **1.88** | 1.59 | 1.79 |
| CAGR | 15.67% | 12.02% | 19.99% |
| Max DD (fixed-denom / peak-relative) | −16.78% / −8.23% | −15.27% / −8.92% | −27.57% / −11.12% |
| £50,000 → | £130,414.70 | £105,585.81 | £165,985.98 |

**Candidate pools used, and why they should be trusted as faithful to the original**:
- Curated-40 = `analysis.universe.STOCK_MEMBERSHIP` (40 tickers) directly — no reconstruction needed, this dict still exists unchanged in the codebase.
- Full-500 = the intersection of `universe_membership`'s 1,204 distinct tickers and `price_data`'s 540 distinct tickers = **exactly 503**, minus **`FDXF`/`HONA`/`Q`** (47/34/192 rows respectively, below the 300-row `precompute_ticker()` gate) = **500** — an exact match, ticker-for-ticker, to the original study's own reported exclusions. This is strong, independent evidence the reconstructed pool is the same one the original study used, not a coincidentally-similar one.
- Rolling-40 = the same 500-ticker pool, additionally gated by a quarterly (67 reconstitutions, 2009-12-31→2026-06-30, exact match to the original's own count) top-40-by-market-cap schedule, rebuilt from scratch via `analysis/market_cap.py` (deleted from production 2026-08-02, commit `0753cc6`, "confirmed unused by any live decision-making path… every research study that used it worked from a disposable clone's own copy" — resurrected here via `git show` into this investigation's own `research_engine.py` DB shim, never touching production). **483/500 candidates resolved real SEC EDGAR XBRL shares-outstanding data**; 16 `no_data` (including **`META`**, matching the original's own named finding — "structurally excluded from rolling-40 by a known SEC/XBRL data gap" — an exact independent confirmation) and 1 `no_cik` (`AEP`).

**Decision-engine parity, verified with real evidence from this run's own output (not code inspection alone), per CLAUDE.md's standing requirement**: for rolling-40, `analysis.universe.is_member()` was monkeypatched — layered as an AND on top of the real membership check, never replacing it. A random 25-trade sample from the actual output was independently re-checked against the schedule (not the monkeypatch itself): **25/25 correctly in that quarter's top-40 AND a genuine S&P member**. Holding-period sampling flagged 9/25 as ">30 days" (31–32 vs. `max_hold`=30) — investigated directly, not dismissed: e.g. `AVGO` entered 2024-05-03, `AAPL` entered 2021-02-26; confirmed these are all legitimate 1–2 calendar-day weekend overshoots of `evaluate_exit_reason()`'s `holding_days >= max_holding_days` check (the exit only fires on the next *available* price row, which can land 31–32 calendar days out when day 30 falls on a non-trading day) — not a parity gap, and the identical benign pattern was independently observed in the DAX run (§2b) on a completely different ticker set, confirming it's a general property of the shared exit function, not specific to either monkeypatch.

### 2b. DAX — current engine (`simulate_trades()`, still frozen w.r.t. the 3 named fixes), fresh `DAX_MEMBERSHIP`

**A material, disclosed difference from the original**: the original study's `dax_check_pit.py` (and its exact 40-ticker candidate list) is gone — deleted with its disposable research clone, per this project's own standing practice, before the trade-level-preservation requirement existed. This investigation rebuilt `DAX_MEMBERSHIP` from scratch from **two independent, cross-checked sources**: (1) STOXX's own official PDF (`stoxx.com/document/Indices/Common/Indexguide/Historical_Index_Compositions.pdf`, downloaded fresh, 60 pages, DAX section = pages 4–5, extracted via PyMuPDF's structured table detection, not raw-text layout guessing); (2) Wikipedia's "DAX" article "Former DAX components" table (dated, per-event, with explicit reasons). Every date the two sources both covered agreed exactly. The candidate pool used is **today's real current DAX-40 roster** (not necessarily byte-identical to the original's unrecoverable list) — but **every one** of the original write-up's own named examples (`G24.DE` 2025-09-22, `G1A.DE` 2025-09-22, `HNR1.DE`'s two-window pattern 2009-03-23→2009-09-20 & 2022-03-21→present, `RHM.DE` 2023-03-20, `MTX.DE` 2019-09-23, and all seven of `AIR.PA`/`BNR.DE`/`PAH3.DE`/`QIA.DE`/`SHL.DE`/`SY1.DE`/`ZAL.DE` from the 2021-09-20 DAX 30→40 expansion) is present with the identical date, in this reconstruction — strong evidence this is the same, or a near-identical, pool.

**Backtest convention: continuous walk** (numerically identical to reset-at-boundary for this specific non-compounding engine, per §1c). `run_candidate()`/`simulate_trades()` called against the reconstructed `DAX_MEMBERSHIP` (`analysis.universe.is_member` monkeypatched for the 40 DAX tickers only), current `config.yaml`, current `analysis.decision.evaluate_exit_reason()`.

| | Strategy (fresh) | `^GDAXI` (fresh) | Strategy (original) | `^GDAXI` (original) |
|---|---|---|---|---|
| Train CAGR | 6.49% | 5.41% | 6.71% | 5.41% |
| Train max DD | −14.45% | −54.77% (n/a) | −14.45% | −54.77% |
| Test CAGR | 10.09% | 10.61% | 10.06% | 10.37% |
| Test max DD | −18.56% | −38.78% (n/a) | −18.56% | −38.78% |
| Full CAGR | 6.05% | 7.19% | 6.22% | 7.11% |
| Full max DD | −18.56% | −54.77% (n/a) | −18.56% | −54.77% |
| PF (train/test) | 1.46 / 1.41 | — | 1.46 / 1.41 | — |

**PF and max-DD reproduce to the exact reported decimal in every single reported field** (1.46/1.41; −14.45%/−18.56%/−18.56%). CAGR is within 0.02–0.22pp of the original throughout, and the benchmark's own train CAGR matches exactly (5.41%=5.41%) — this level of agreement is strong, direct evidence the reconstructed 40-ticker pool and membership windows are faithful to the original, and that any remaining gap is ordinary data-recency drift (3 extra days: end pinned to 2026-08-03 here vs. 2026-07-30/31 originally), not a reconstruction error.

**Blocks passed: 14/16**, failing only blocks 9 (2018-01-07→2019-03-30, PF 0.72) and 12 (2021-09-09→2022-11-30, PF 0.77) — **not** 12/16. Blocks 0 (2007-01-01→2008-03-23, PF 1.17) and 1 (2008-03-23→2009-06-13, PF 1.03) now pass where they originally failed.

**Decision-engine parity**: 25/25 sample trades independently confirmed genuinely DAX-eligible against `DAX_MEMBERSHIP` directly (not the monkeypatch); the same benign 30→31/32-day holding-period overshoot pattern as §2a (8/25), for the identical weekend-gap reason, confirmed directly against real price-row gaps (e.g. `BAYN.DE` entered 2014-02-06, next available row after the 30-day mark is 2014-03-10 — day 30 fell on a Saturday).

## 3. Per-variant verdict: still holds, or needs updating

### Curated-40 vs. rolling-40 vs. full-500 — **still holds**, with one precise, quantified update

**The headline ranking is unchanged**: full-500 > curated-40 > rolling-40 on CAGR (19.99% > 15.67% > 12.02%, vs. originally 19.83% > 14.31% > 11.08%); rolling-40 has the smallest drawdown of the three in both eras; curated-40's relative CAGR edge over rolling-40 is essentially unchanged (fresh 15.67/12.02 = **1.30×**, original 14.31/11.08 = **1.29×**).

**What needs updating, precisely — a sentence-level correction to the original's own summary wording**: the original wrote "full-500 … *matches* curated-40's best-in-class profit factor" (both 1.77). **This no longer holds.** Under today's actual production config, curated-40's PF is **1.88**, clearly above full-500's **1.79** — a tie has become a real, if modest, lead for curated-40 specifically on profit factor. **Root cause, isolated with a direct sensitivity run, not inferred**: re-running curated-40 with `min_momentum_pct` forced back to 0.0 (matching the original study's own implicit gate, everything else unchanged) reproduces the original almost exactly — 1,076 trades, PF **1.77**, CAGR 14.13%, DD −15.80%, £119,389.73 (vs. original 1,073/1.77/14.31%/−15.83%/£119,600.48, a gap fully inside ordinary data-recency drift). The 2026-08-03 momentum floor (`min_momentum_pct: 0.02`, postdating the original study) is confirmed as the specific, near-entire cause of curated-40's shift — and it has a **materially larger effect on the narrow 40-ticker pool** (PF 1.77→1.88, CAGR +1.54pp) than the same floor has on the broad 500-ticker pool (per the floor's own original validation sweep: CAGR 19.86%→19.88%, ~0.02pp) — consistent with, not contradicting, this project's already-documented finding that narrower candidate pools are more sensitive to capacity-cascade path-dependency effects (the identical mechanism the re-entry-cooldown sweep and momentum-floor sweep both already found and named).

**Replacement wording for the permanent record**: *"Full-500 beats both gated variants on CAGR at a real, quantified drawdown cost; it no longer matches curated-40's profit factor — under today's config (which includes the 2026-08-03 2% momentum floor), curated-40's PF (1.88) is clearly higher than full-500's (1.79), a gap explained almost entirely by the floor's disproportionately larger effect on the narrower 40-ticker pool, not by any change in relative stock-selection quality."* The re-entry cooldown fix and its production value (0) are confirmed **irrelevant** to all three figures — `cooldown_days=0` is proven identical to "no cooldown mechanism at all" (the pre-fix state) by construction (`is_reentry_blocked(days_since_exit>=1, cooldown_days=0)` always evaluates `False`, and `_admit_group()`'s `flat_before_today` set guarantees `days_since_exit>=1` on the earliest possible re-entry day) — independently re-confirmed here by the near-exact match once the momentum floor alone was neutralized. Price-sanity is confirmed structurally inapplicable — `grep -n "is_submission_price_move_blocked" analysis/backtest.py` returns nothing; it is a submission-time-only mechanism in `brokers/trading212.py`, never called from any backtest engine.

### DAX — **still holds on its core claim**; the block-pass count needs updating, for a reason unrelated to this task's three named fixes

**The core qualitative conclusion is unchanged and confirmed by an almost-exact reproduction**: *"real, substantial risk reduction (drawdown roughly a third of the benchmark's own) but not clearly better absolute return."* Fresh test CAGR (10.09%) still essentially ties the benchmark's (10.61%), exactly as originally (10.06% vs. 10.37%); fresh full-period DD (−18.56%, byte-identical to the original) is still roughly a third of `^GDAXI`'s own (−54.77%, byte-identical).

**What needs updating, precisely**: **Blocks passed: 14/16 (87.5%), not 12/16 (75%)** — blocks 0 and 1 flip from fail to pass. This is **not** an effect of the re-entry cooldown fix, momentum floor, or price-sanity check (`simulate_trades()` is confirmed exempt from all three: cooldown/momentum-floor by explicit docstring exemption, price-sanity by the same `grep` above). It is caused by a **fourth, separately-dated production change**: `029e2ec` (2026-08-02 21:57, "Let momentum, not index removal, govern already-open positions") removed `INDEX_REMOVAL` from `evaluate_exit_reason()`'s exit priority for **every** engine, including `simulate_trades()`. Under the pre-`029e2ec` engine, a DAX position could be force-closed the moment its ticker's real membership window ended (which, per the original correction's own finding, happens far more often and more consequentially for DAX's choppier, narrower windows than for the US universe); under the current engine it rides to its normal exit trigger instead. Given DAX's whole premise is point-in-time membership gating with several genuinely narrow/gapped windows, this is a mechanistically plausible, directly attributable explanation, not a coincidence.

**Replacement wording for the permanent record**: *"Blocks passed: 14/16 (87.5%) under the current engine, not 12/16 — the improvement traces to the 2026-08-02 removal of INDEX_REMOVAL from the shared exit-priority chain (a real, deliberate, evidence-based production change, unrelated to the re-entry-cooldown/momentum-floor/price-sanity fixes), not to any of those three, and not to a reconstruction artifact — PF and drawdown reproduce to the exact original decimal in every reported field, confirming the point-in-time membership reconstruction itself is faithful."* Blocks 9 and 12 (PF 0.72 and 0.77 respectively) still fail, unchanged in verdict from the original.

## 4. Confirmation

**Touched, all under `research/curated40_rolling40_dax_reverify_2026-08-05/`** (repo root: `c:\GitHub\investment-research-sp500`):
- `scripts/`: `env_setup.py`, `research_engine.py`, `reset_wrapper.py`, `market_cap_research.py` (resurrected from `git show c422646:analysis/market_cap.py`, DB access repointed to this investigation's own working DB), `dax_membership.py`, `fetch_shares.py`, `build_rolling40.py`, `fetch_dax_prices.py`, `run_variant.py`, `run_rolling40.py`, `run_dax.py`, `run_curated40_sensitivity.py`, `aggregate_results.py`.
- `output/`: `candidate_pool_500.json`, `curated40_{result,full_trades,test_trades}.{json,csv}`, `curated40_no_momentum_floor_result.json`, `full500_{result,full_trades,test_trades}.{json,csv}`, `rolling40_{result,full_trades,test_trades}.{json,csv}`, `rolling40_schedule.json`, `dax_{result,full_trades}.{json,csv}`, `shares_fetch_summary.json`, `dax_wikipedia_raw.txt`, `stoxx_historical_compositions.{pdf,txt}` — full trade-level output preserved for all four re-runs, per the Research Reproducibility Standard, precisely because this exact investigation thread is the one that standard was written to protect.
- `logs/`: raw run logs for all background executions.
- A working copy of the production DB (`work.db`, plus a resurrected `market_cap_history` table populated from real SEC EDGAR data) was used throughout, kept isolated to this directory, and **deleted** after every figure above was captured and exported to CSV/JSON — matching this project's standing "isolated clone, own copied DB, deleted after use" practice.

**Not touched**: no file under `analysis/`, `brokers/`, `data/` (production modules), `config.yaml`, `main.py`, `.github/`, or any deploy script was read-modified — every production module was only ever imported and, where needed, monkeypatched in-process (`analysis.universe.is_member`, `data.ingestion.get_engine`, `analysis.universe.get_engine`) for the lifetime of a single research script's own process, never on disk. `CLAUDE.md`/`CLAUDE_HISTORY.md` were read only, never edited (updating the permanent record with the §3 replacement wording is left to the coordinating session, per the task's own delivery instructions). **Nothing was committed or pushed** — `git status` at the end of this session shows only `database/` and the four `research/*_2026-08-05/` directories (three from other sessions tonight, one mine) as untracked; no tracked file shows a diff attributable to this investigation. A concurrent session's edits to six tracked files were observed mid-task (not caused by this investigation, confirmed by direct diff/timestamp inspection) and had resolved into a clean commit (`3496224`) by the time this investigation finished — no conflict, no interference, in either direction.
