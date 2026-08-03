# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Research reproducibility standard (2026-07-31)

**Before a research clone backing a significant finding is deleted, the exact formula must be captured in this file — literal code or precise pseudocode with exact thresholds and operators — not a prose description.** This is a hard requirement for any future task that deletes a research clone after shipping a finding, not a suggestion.

**Why this exists.** "Block pass/fail formula reconciliation (2026-07-31)" (below, under the V2 architecture sections) found that this file's only record of the 16-block validation criteria was a prose paraphrase — "no crisis-adjacent block materially loses money" — that turned out not to mean what it said. The real gate was `PF > 1.0`, a hard number that was never written down as such anywhere. A second stated criterion ("CAGR ≥ S&P's own") was never actually part of the real per-block formula at all. The disposable research clone that implemented and ran the real check had already been deleted (this project's own standing practice for exploratory code) by the time anyone needed to check the paraphrase against it — so the mismatch went undetected for as long as nothing forced an exact reproduction, and reproducing the incident's 14/16 headline result at the aggregate level alone was not enough to catch it: the two *specific* failing blocks silently didn't match until the formula itself was rebuilt and checked block-by-block.

**The rule.** Prose is for *why* a threshold was chosen, not for *what* the threshold is. Any write-up that reports a pass/fail outcome, an adopt/reject decision, or a validated result must state the exact rule that produced it — inequality, operator, and number — inline or in a code block, before the backing implementation is removed. "Materially," "meaningfully," "roughly," "significantly," "close to," and similar words are acceptable around a stated number (e.g. "materially worse (42.9% vs. 41.2%)") but must never stand in *for* the number or the comparison operator itself.

**Extended 2026-08-02 — preserve full trade-level output, not just the formula.** The rule above closes the "what was the exact threshold" gap; it does not close a second, related gap the curated-40-vs-rolling-40 investigation (2026-08-02, see "V2 stock universe broadening" and its follow-on sections) ran directly into: a fresh, independent reproduction of curated-40's test-period backtest could prove the *methodology* was internally deterministic (two runs, 8.5 hours apart, produced byte-identical results) but could not explain why it differed by ~35% from a previously-cited headline figure (£442,558.70 vs. a reproduced £288,323.01), because only the methodology description and an aggregate summary survived from the original run — the disposable research clone that produced the original trade-level output had already been deleted by the time anyone needed to diff against it. Knowing the formula was not enough; without the original trades themselves (ticker, entry/exit date, price, P&L, exit reason — the same shape `backtest_trades`/`t212_pnl` already store for every real run), there was nothing concrete left to trace a discrepancy against, only two competing aggregate numbers and no way to arbitrate between them.

**The rule.** Before a research clone backing a finding that ships as a *headline figure* — a number this file cites, a number a decision gets made from, a number a future comparison will be benchmarked against — is deleted, its full trade-level output must be preserved somewhere durable, not just the methodology that produced it. This does not apply to every intermediate research step or exploratory dead end (most of this file's "researched, not adopted" sections never needed this and still don't); it applies specifically to whatever number actually gets cited going forward. "Durable" means outside the disposable clone itself — a CSV/parquet export committed alongside the write-up, a row-set written to a table in the real (non-disposable) database, or an equivalent that survives the clone's own deletion — not a description detailed enough that a reader assumes it could be regenerated identically on demand, which is exactly the assumption this incident showed doesn't hold in practice (regenerating it independently is necessary but not sufficient for auditing a *specific* prior figure, only for establishing a new one). Applied going forward, not retroactively — the two figures already in tension here (£442,558.70 and £288,323.01) cannot be reconciled after the fact, precisely because neither one's underlying trades survived to be diffed.

## Two backtest conventions — reset-at-boundary vs. continuous walk (2026-08-02) — standing requirement

**Two legitimate backtest conventions exist, answering different questions — always state explicitly which one a report uses, never leave it implicit.** This is a standing requirement for all future backtest/validation work in this codebase, not a note specific to any one day's investigation — the same "label it, don't leave it implicit" discipline this file already requires for cost-basis vs. mark-to-market deployment-cap accounting (see "Live/backtest deployment-cap parity" below) now applies to this second, independent methodology choice.

**Why this exists.** The curated-40-vs-rolling-40 weighting/ordering investigation (2026-08-02) found that running a test-period comparison as one continuous 2007–2026 walk contaminates any comparison between two variants: whichever variant compounds to a bigger balance during the training years mechanically sizes every one of its test-period trades bigger via `per_trade_pct`, regardless of that later trade's own merit — a real, measured effect, not a theoretical risk. Confirmed directly: curated-40 entered the test period with 1.49x rolling-40's account balance purely from 2009–2019 performance, and that one number alone inflated roughly 73% of curated-40's apparent test-period return advantage once the two were reweighted to a common position size. But the continuous walk is not simply "wrong" — it is the *correct* methodology for a different, equally legitimate question, and conflating the two is what caused the contamination to go unnoticed until it was checked directly.

**The two conventions:**

1. **Reset-at-boundary** — every variant being compared resets to the same fresh starting balance at the test-period boundary, rather than carrying forward whatever it compounded to during training. **This is the default for any comparison between two or more variants** — which selection method, which parameter value, which universe, which sizing scheme is better. Train-period luck contaminating a cross-variant comparison is a real, measured problem here, not a hypothetical one, and there is no good reason to reweight after the fact when the clean version can be built in from the start.

2. **Continuous walk** — the account compounds through train into test with no reset, exactly as a real, continuously-running account would. **This is the correct methodology for reporting what a single, already-decided strategy would realistically have returned if actually run since inception** — not a comparison between variants, but a report of what one real account would show. A real account cannot reset itself at an arbitrary date; this convention reflects that correctly, and reset-at-boundary would be the wrong (and misleading) choice for this question.

**Concrete example, for calibration.** "Portfolio-capped validation findings" / "Live execution switched to the compounding two-parameter scheme" (does the currently-shipped system beat buy-and-hold S&P 500 in £ terms) is a continuous-walk question — it reports one strategy's realistic single-account outcome, never a comparison between competing variants. The curated-40 vs. rolling-40 vs. momentum-only research thread (which stock-selection approach is actually better) is a reset-at-boundary question — comparing variants' relative quality, where letting one variant's train-period luck carry into the test-period comparison is exactly the contamination this section exists to prevent. Same underlying backtest engine and the same train/test date split in both cases — the only difference is the question being asked, and that difference changes which convention is correct.

**The rule going forward.** Any future backtest or validation report must state explicitly which of the two conventions it used — inline, the same way a report must already state whether it used cost-basis or mark-to-market deployment-cap accounting. Do not leave this implicit and do not assume a reader can infer it from context: two legitimate methodologies sitting side-by-side with no clear labeling has already caused real, measured confusion more than once in this project's history (this section's own motivating incident, and the cost-basis/mark-to-market gap it parallels). A report comparing variants that does not state "reset-at-boundary" should be treated as unverified for that purpose until re-checked; a report of a single strategy's realistic track record that resets at a boundary anyway should be treated as understating what a real account would actually have shown.

## Experimental/research work runs locally, not on the server (2026-08-03) — standing practice

**Backtests, parameter sweeps, and variant comparisons should run in a local Claude Code session, not on the production server.** The local machine has substantially more resources available for this kind of work (4 physical cores/8 threads, 32GB RAM) than the server (2 vCPU, 3.7GB) — a compute-heavy sweep run locally is both faster and carries zero risk to the live system, versus contending with the server's own scheduled jobs and live order placement for CPU/memory it wasn't sized to share.

**This does not apply to production deployments or live bug fixes.** Those still happen on the actual server, since that's where the real system runs — this practice is specifically about research/experimentation (the many disposable-research-clone investigations documented throughout this file), not about where production changes get made or verified.

**Before any local sweep, verify the local environment is current — don't assume it.** Confirm directly, not from memory of a prior session: a fresh copy of the production database, with coverage actually checked (not just "recently copied"); current code, including any recent fixes relevant to whatever is being tested (a sweep run against stale code silently reproduces whatever bug the latest fix just closed); and the database file confirmed gitignored and outside any commit path, so a multi-gigabyte local DB copy never accidentally lands in a commit. Skipping this check is exactly the kind of gap this file's own "Research reproducibility standard" and "Research scripts must verify decision-engine parity" sections already warn against for a different reason — a research finding is only as trustworthy as the environment it was produced in.

**There is exactly one implementation of any given trading decision — never two hand-written copies that are merely supposed to agree.** "Decision logic" means anything that determines *what should happen*: what qualifies for entry, how a position is sized, which candidates win when capacity is scarce, what triggers an exit and in what priority. It does not mean *execution mechanics* — live legitimately needs a real broker-API call (`Trading212Client`), retry/back-off, and DB bookkeeping that the backtest simulates instead; that split is fine and is not what this rule is about. Anywhere the *same decision* has to be made by both live order placement (`brokers/trading212.py`) and the backtest engine (`analysis/backtest.py`), both must call one shared function — never two independently-written expressions of the same rule.

**Why this is a hard rule, not a style preference.** In a single day (2026-08-01) three separate pieces of decision logic were found to have quietly drifted apart between live and backtest before anyone was looking for it — each discovered by accident, during unrelated work, not by any systematic check:

1. **Portfolio/deployment caps** — the backtest engine (`run_candidate()`/`simulate_trades()`) had no cross-ticker portfolio cap at all, while live enforced `max_open_positions`/`max_total_deployed` for real. Every backtest figure in this file up to that point was generated by an engine that never modelled the one constraint shaping live trade selection the most (see "Portfolio-capped backtest engine").
2. **Compounding** — live already sized positions against the account's real, current, compounding balance (`client.get_account_summary()["totalValue"]`, read fresh every call); the backtest's own portfolio-capped mode still sized against a fixed notional constant that never grew or shrank (see "Compounding portfolio backtest").
3. **Trade priority under scarce capacity** — both engines picked which candidate wins when capacity binds, but via an arbitrary, never-designed `(is_etf, config-order)` rule that nobody had actually decided was correct (see "Trade prioritization and capacity-allocation engine").

None of these three was caught by a test — the test suite at the time tested each side's *own* reproducibility (did a re-run match the last documented figure), never whether live's actual answer matched backtest's actual answer *for the same scenario*. That is precisely the gap this section closes.

**The mechanism — `analysis/decision.py`.** `evaluate_exit_reason()` (crisis > index-removal > stop-loss > signal-off > max-holding, the exact validated priority) and `passes_entry_gate()` (signal on AND currently a member AND not crisis-blocked) are the two decisions with the widest blast radius, and were found independently hand-coded as the same if/elif chain / boolean expression in **five** places before this task: `brokers/trading212.py`'s `maybe_exit_positions()`/`maybe_place_orders()`, `analysis/backtest.py`'s `simulate_trades()`/`simulate_portfolio()`/`simulate_portfolio_compounding()`, and `output/performance.py`'s `get_brief_cards()` (a display-only recomputation for the Daily Briefing/Performance page's position-status badge — no order is placed there, but a UI showing "ON TRACK" for a position the live system is actually about to close is its own real bug). All five now call the two functions in `analysis/decision.py` instead. Every input tolerates `None` ("unknown, don't trigger this check") rather than relying on Python's `not None == True` coercion — live and the backtest engine never pass `None` for any of these (both only ever call these functions with confirmed values), but `output/performance.py`'s display path genuinely can (a ticker with insufficient history, a stale API read), and a naive `not sig_on`-style extraction would have silently misclassified "unknown" as a definite trigger.

**What was reviewed and left duplicated, on purpose, with the reason recorded rather than silently accepted:**
- **Candidate priority ranking** (momentum descending, tie-break by insertion order) is expressed once in SQL (`brokers/trading212.py:_load_pending_orders()`: `ORDER BY momentum_pct DESC, decided_at ASC`) and once in Python (`analysis/backtest.py:simulate_portfolio_compounding()`'s `_admit_group()`: `sort(key=lambda c: (-c[1], idx))`). These cannot literally share one function — one runs as a database query, the other as an in-memory sort over a day's candidate list — so this is accepted, reviewed duplication of a simple rule, not overlooked duplication. Both sides are covered by their own tests asserting momentum-descending order (`tests/test_execution.py`, `tests/test_portfolio_backtest.py`).
- **The deployment-cap arithmetic — this claim was WRONG, corrected 2026-08-01.** This bullet originally read: "`_submit_pending_orders()`'s `deployed_after_pct > max_deployed_pct` vs. `simulate_portfolio_compounding()`'s `would_deploy_pct > max_deployed_pct` is functionally the identical check but was left as two call sites rather than extracted... reviewed and left as-is." That verdict checked only that both sides used the same comparison operator and threshold — it never checked that both sides' `invested`/`deployed` numerator meant the same thing. They didn't: live's `deployed_after_pct` uses `invested = summary["investments"]["currentValue"]`, T212's genuine mark-to-market valuation of open positions; `simulate_portfolio_compounding()`'s `current_deployed` was pure cost basis — the sum of each open position's original entry-time `position_gbp`, never adjusted for any unrealized gain or loss for as long as the position stayed open. A winning position never "grew" for capacity purposes under the old backtest engine; a losing one never "shrank." This is exactly the class of divergence `tests/test_live_backtest_parity.py` was built to catch — but that suite's scope was `analysis/decision.py`'s two functions only (`evaluate_exit_reason`/`passes_entry_gate`); deployment-cap logic was never in its test matrix at all, so it was never positioned to catch this regardless of the review above. See "Live/backtest deployment-cap parity" below for the full investigation, the fix (`analysis/backtest.py:simulate_portfolio_compounding_mtm()`, a genuinely mark-to-market engine kept alongside the original cost-basis one, selectable via `analysis.validation.run_full_validation(..., mark_to_market=True)`), and which of today's validated findings needed re-verification as a result. The underlying reason two call sites exist at all (rather than one shared function) is unchanged and still correct: `simulate_portfolio()` deliberately uses a different, simpler, frozen formula for reproducibility reasons unrelated to this bug — extracting a shared helper still risks destabilizing that frozen engine. What was wrong was calling the two REMAINING sites (live's real submission check, backtest's compounding-mode check) "functionally identical" without verifying it.
- **`analysis/universe.py` (`is_member`, `get_max_holding_days`), `analysis/signals.py` (`compute_signal_series`), `analysis/crisis.py` (`is_crisis_day`/`is_entry_blocked`/`compute_raw_trigger`/`extend_trigger`), and `analysis/volatility.py` (`_atr`, `_resolve_entry_stop`, `_var_position_size_core`)** were already single, shared implementations before this task — confirmed during the audit, not assumed. No changes were needed to any of them.

**The enforcement mechanism — `tests/test_live_backtest_parity.py`, part of the standard suite, not a one-off check.** Unlike every other test in this suite (which tests live or the backtest engine in isolation), this file drives **both real code paths** — `brokers/trading212.py`'s actual `maybe_place_orders()`/`maybe_exit_positions()` and `analysis/backtest.py`'s actual `simulate_trades()` — against byte-identical synthetic price history, and asserts they reach the identical decision: 4 entry scenarios (fires when all three conditions hold; blocked by signal, by crisis, by non-membership) and 5 exit scenarios (one per priority level: `CRISIS_EXIT`, `STOP_LOSS`, `SIGNAL_OFF`, `MAX_HOLDING`, `INDEX_REMOVAL`), each constructed so exactly one trigger fires and the others are deliberately kept out of range. Only the Trading212 HTTP client and the DB/price-data read layer are mocked; every decision-making function itself runs for real on both sides. `tests/test_decision.py` separately unit-tests `analysis/decision.py`'s two functions directly (priority ordering, `None`-tolerance) — that is not a substitute for the parity test, since a shared function only protects against drift if every call site is actually wired to it; the parity test is what catches a call site that quietly stops calling the shared function or passes it the wrong argument, which a unit test on the function in isolation cannot.

**Verified to actually fail, not just pass.** Before trusting this as a safeguard, two realistic call-site bugs were deliberately injected into `brokers/trading212.py` (uncommitted, reverted immediately after) and the parity suite re-run each time: (1) hardcoding `is_member=True` into the exit call (silently disabling `INDEX_REMOVAL` for stocks) — `test_exit_parity_index_removal` failed, all 8 other parity tests still passed; (2) hardcoding `entry_blocked=False` into the entry-gate call (silently ignoring the crisis entry-block) — `test_entry_parity_blocked_during_crisis_despite_positive_signal` failed, all 8 others still passed. Both times the failure was specific to the exact scenario the injected bug would affect, not a blanket breakage — confirming the test suite catches a real divergence with a localized, diagnosable failure rather than either missing it or failing everything indiscriminately.

**How to apply this going forward.** Any new decision that both live order placement and the backtest engine need to make belongs in `analysis/decision.py` (or an equally shared module) from the start — never written once "for now" in one file with a note to mirror it later. If a change to entry/exit/sizing/priority logic is ever proposed, it should change the shared function, and `tests/test_live_backtest_parity.py` should keep passing without modification (a scenario needing to change to keep passing is itself a signal the change altered priority/semantics, not just an implementation detail). If a genuinely new call site for an existing shared decision is added (e.g. a future live-preview or dry-run mode), it must call the existing function in `analysis/decision.py`, not re-derive the condition inline — and a new parity scenario should be added for it, following this section's own pattern, not deferred.

## Research scripts must verify decision-engine parity, not inherit it by assumption (standing requirement)

**The production consolidation above (`analysis/decision.py`, the shared `get_max_holding_days()`/`_var_position_size_core()`/etc., and the Layer 1/Layer 2 enforcement mechanisms) protects the live codebase and the standard backtest engines. It does not automatically protect a disposable research script.** Every rolling-window/rolling-N study, universe-broadening study, or sweep built in this project is a fresh, one-off script — and most of them monkeypatch `analysis.universe.is_member()`, build a custom candidate pool, or otherwise reach into the middle of the pipeline exactly the way the original five live/backtest divergences (exit priority, entry gate, `is_etf` dispatch, deployment-cap tracking, mark-to-market sizing — see "Live/backtest decision-logic parity" above) were first introduced. A script can correctly patch the one function it means to patch while silently sidestepping something else downstream — exit logic, sizing, holding-period dispatch — without anyone noticing, because the script still runs, still produces plausible-looking numbers, and still *looks* correct on inspection. This is not a hypothetical: it is the same failure shape "Live/backtest decision-logic parity" documents happening five separate times in one day, and nothing about writing a new research script instead of production code makes that shape less likely — if anything, a research script's disposability makes it less likely anyone reviews it as carefully.

**The rule.** Any research script that (a) constructs a custom candidate pool/universe, or (b) monkeypatches or otherwise overrides a universe-lookup function such as `is_member()`, must explicitly verify — with real evidence gathered from that specific script's own run, not assumed from "the production codebase is consolidated" — that entry, exit, sizing, and holding-period logic all still route through the same shared functions production uses: `analysis/decision.py`'s `evaluate_exit_reason()`/`passes_entry_gate()`/`is_asset_class_enabled()`/`resolve_dynamic_max_position_gbp()`/`resolve_cash_constrained_position_gbp()`/`is_position_size_viable()`, plus `analysis/universe.py:get_max_holding_days()` and `analysis/volatility.py:_var_position_size_core()`. **"The production codebase is consolidated" is a fact about production, not a fact about the new script — it has to be re-checked for that specific script, every time**, precisely because the whole point of a monkeypatch is to intercept the pipeline somewhere, and there is no guarantee the interception is clean.

**The verification pattern to follow — evidence, not inspection.** Don't just read the script and confirm by eye that it calls the right functions (this is exactly the kind of review that already failed once — see the corrected deployment-cap bullet above, where "reviewed and left as-is" checked the comparison operator but not what the operator's inputs actually meant). Instead: pull actual outputs for a sample of trades from the script's own run — holding-period durations, sizing outputs, exit reasons — and independently recompute what the shared functions would produce for the same inputs, then confirm they match. For example, for a rolling-N study that monkeypatches `is_member()`: take a handful of real trades the script produced, look up each one's ticker and entry conditions, call `get_max_holding_days()` directly with those same inputs, and confirm the trade's actual holding period respected that limit — don't just grep the script for a call to `get_max_holding_days()` and stop there, since a call being present doesn't guarantee its result was actually used downstream.

**This is a standing requirement for all future research work, not a note about today's incidents.** Every "researched, not adopted" and "researched, adopted" section elsewhere in this file that touches a custom universe or a monkeypatched membership function should be read as having implicitly needed this check — going forward, any new one must state explicitly, in its own write-up, what evidence was gathered and what it showed, the same way this file already insists on an exact formula (see "Research reproducibility standard" above) rather than a prose paraphrase. A research script that skips this check should not have its findings folded into this file's record as though the finding were parity-verified.

## Dashboard page replaces Daily Briefing (2026-08-02)

The sidebar's default landing page is now **Dashboard**, not Daily Briefing — the old page (regime status bar, overlay strips, daily narrative, FOMC key events, top opportunities, Macro Dashboard, Open Trade Monitor) is gone, along with `_render_briefing_page()` and `_render_trade_card_compact()`. Every one of those V1-era concepts (regime narrative, FOMC events, macro overlays, opportunity scanning) has no V2 equivalent — V2 trades one signal (12-month momentum) against a point-in-time-gated stock universe, with no regime classifier and no confluence score to narrate — so the old page had already been thin by necessity since the V1→V2 cutover (see "V2 architecture" above) and was carrying content this system no longer computes. Its replacement is a genuine account-overview page, built from what V2 actually has: real T212 account state, the pending-order queue, and closed-trade P&L.

**What the removal affects — checked directly, not assumed.** `_render_trade_card_compact()` had zero remaining callers once the old page's Open Positions section was deleted (confirmed via grep before removing it) — full-detail open-position cards (`_render_trade_card()`, `_get_open_cards()`, `positions_summary_text()`, `POSITIONS_RULES_EXPLANATION`) already live on the **Performance** page and are unaffected. Two stray strings referencing "Daily Briefing" were updated: the Status page's T212 Account tab caption ("Open positions are shown in full on the **Performance** and **Daily Briefing** pages" → "...on the **Performance** page") and a Performance-page code comment that cited the old page's ordering convention (repointed at this file's own "Decision-relevant, tone-neutral context on open position cards" section instead, since that's the actual source of the alphabetical-not-ranked convention both pages follow). Every other `_get_t212_invest_brief_cards()`/`get_brief_cards()` caller was already on the Performance page and needed no change. Two intentional historical-documentation mentions of "Daily Briefing" remain elsewhere in this file (matching this project's own convention of not rewriting past write-ups to erase what was true at the time).

**Sections built** (five, exactly matching the task's own scope — no carryover of the old page's "Open Positions"/"Every Tracked Ticker" sections, since those are already fully covered on the Performance page and per-ticker eligibility detail wasn't in the requested scope):

1. **Account balance** — `get_current_account_summary()` (`output/performance.py`), reading the scheduler-maintained `t212_account_snapshot` from `system_metadata` (no direct API call from the UI thread, matching every other T212-data-reading page in this app). Shown prominently at the top: total value, cash, invested, snapshot age.
2. **Account-value chart** — `get_account_value_history(days, starting_balance_gbp=50_000.0)`, week/month/year range selector (`_VALUE_CHART_RANGES`), fed by the new EOD-captured daily snapshots (below). Anchored to start at the real £50,000 initial balance rather than £0 or the first available data point, so the chart reads correctly even with almost no history yet — a synthetic leading point at `(range_start, 50_000.0)` is prepended when the real history doesn't already reach back that far, exactly the "not an empty-looking chart" requirement the task specified.
3. **Top 5 winners / top 5 losers** — `get_top_trades(limit=5)`, reading `t212_pnl`. Correctly renders as empty/near-empty right now (zero live trading history since the account reset) — this is stated in the UI as expected, not surfaced as an error state.
4. **Candidate queue** — `get_pending_candidates()`, reading `pending_orders` with `status='PENDING'`.
5. **Placed-but-unconfirmed exits** — `get_unconfirmed_exits()`, reading `invest_orders` for `direction='SELL' AND status='PLACED'` — real orders sitting in exactly the T212 fill-lag state this file already documents elsewhere (orders resolving 60+ hours after being placed on this demo account), not a hypothetical "decided but not yet submitted" queue (`maybe_exit_positions()` decides-and-executes in one hourly call, so no such intermediate state exists for exits).

All five backing functions live in `output/performance.py` (never `main.py` directly — this module cannot be imported in tests, since it starts a real scheduler with live T212 calls at module level) and are plain, never-raising DB reads, wrapped in `main.py` by thin `@st.cache_data(ttl=60)` functions per this codebase's established pattern.

**EOD-based account-value history — the decided design, built exactly as specified.** `data/database.py` gained `AccountValueHistory` (`account_value_history` table, unique on `date` — one row per calendar day, not one per snapshot fetch), and `data/scheduler.py:_fetch_t212_snapshot(config, record_history: bool = False)` gained the new parameter: when `True`, it upserts a row (`total_value_gbp`/`cash_gbp`/`invested_gbp`, keyed on today's UTC date) immediately after the account summary it already fetches on every call — no new API call, one extra DB write on top of a fetch that already happens daily. Only `_eod_job()`'s own call site passes `record_history=True`; the 60-second dispatcher and the hourly trade-alert job's calls to the same function both keep the default `False`, so the table accumulates a genuine one-row-per-day time series rather than many rows a day. A same-day re-run (a manual "Refresh Data" click, or the EOD job somehow firing twice) upserts that day's row rather than duplicating it. Never raises — a history-recording failure must not affect the account snapshot that was already successfully written moments earlier.

Covered by 6 new tests in `tests/test_scheduler.py` (default doesn't record, `record_history=True` writes the correct row, a same-day re-run upserts rather than duplicates, a disabled `trading212_invest` config writes nothing, `_record_account_value_history()` never raises on a malformed summary, and the EOD job itself is confirmed — via its actual call arguments, not just that some call happened — to be the one call site passing `record_history=True`) and 13 new tests in `tests/test_performance.py` covering all five new `output/performance.py` functions (empty-state, populated-state, and malformed/missing-data cases for each).

## Running the app

> **Historical framing (added 2026-08-03).** Like "## Architecture" below, this section largely describes the superseded V1 system — First-run instructions referencing Run Analysis/Generate Signals buttons that no longer exist, an eleven-job schedule that includes FOMC/BoJ/ECB reaction jobs and FRED macro fetching (both fully removed), and several subsections (Central bank event monitoring, the two Macro module entries, FRED historical backfill) describing infrastructure that has since been deleted entirely. One exception: **"### Historical data extension — 2007-01-01"** below is still genuinely load-bearing — the price-history depth it describes is exactly what the current Backtest page's "2007–present" validated battery runs against. The schedule table immediately below has been corrected in place (a wrong table is more actively misleading than wrong prose left as historical record); the deeper V1-era subsections after it have not been individually rewritten — treat them as historical record only. For the current, real operational picture, see "V2 architecture — phased rebuild, Stage 1" onward, and specifically "Exchange-aware order submission" and "Market-calendar-aware scheduling" for how the live schedule actually works today.

```powershell
# Install dependencies
pip install -r requirements.txt

# Launch the Streamlit dashboard
python -m streamlit run main.py
```

There is no manual first-run step. `main.py:_initialise()` runs on every Streamlit session start and synchronously computes signals against whatever price history already exists; on a genuinely empty database, the very next EOD job (or a manual "Refresh Data + Orders Now" click on the Status page) populates price history and immediately places orders from the same run — there is no separate "Run Analysis" / "Generate Signals" step in the current system (both were V1-era Signals-page buttons; the Signals page itself no longer exists).

The current schedule is seven scheduled activities, run by the standalone `investment-research-scheduler.service` daemon (`scheduler_daemon.py`) regardless of whether anyone has the dashboard open:

| Schedule | Job id | What it does |
|---|---|---|
| **Every 60 seconds** | `job_dispatcher` | Checks `pending_t212_fetch` in `system_metadata`; if set, calls `_fetch_t212_snapshot()` to refresh the T212 account/positions snapshot |
| **Daily 21:15 UTC** (NYSE-calendar-gated internally, no `day_of_week` filter) | `membership_refresh` | `analysis/universe.py:refresh_membership_from_source()` — re-fetches the point-in-time S&P 500 membership source, 15 minutes ahead of EOD so the live candidate pool is never computed against stale membership data |
| **Daily 21:30 UTC** (NYSE-calendar-gated internally) | `scheduled_eod` (`_eod_job`) | The only job that pulls prices — `pull_incremental()` for open exchanges, then the momentum/crisis decide phase, which queues BUY decisions into `pending_orders` (not submitted yet) and runs the unconditional exit check; also runs `purge_old_pending_orders()` |
| **Mon–Fri 14:30 UTC** | `stock_order_submission` | Submits queued BUY decisions — 60 minutes after NYSE's regular open, not right at it, to avoid the most volatile window for a market order's fill (see "Submission-time crisis re-check" and the exchange-aware-submission section for the fresh price-sanity and crisis re-checks applied here) |
| **Mon–Fri 00:00–21:00 + 23:00 UTC (hourly)** | `trade_alert_check` | Runs the exit check (crisis/stop-loss/signal-off/max-holding), polls placed order status, refreshes the T212 account/positions snapshot |
| **Mon 07:00 UTC** | `monday_premarket` | Cancels any BUY order still `PLACED` from Friday's EOD run (>30h old — T212 doesn't fill market orders outside trading hours), then re-runs the decide phase against Friday's closing prices |
| **Sunday 09:00 Europe/London** (DST-correct, not a fixed UTC offset) | `weekly_summary` | Sends the weekly status email — open positions, this week's realised+unrealised P&L, positions closed with exit reason, whether the crisis override fired |

The Streamlit web UI (`investment-research.service`) also starts an in-process scheduler as a fallback, but checks the `scheduler_heartbeat` key in `system_metadata` first — if less than 90 seconds old, the daemon is active and the in-process scheduler is skipped, preventing duplicate job execution.

**Both systemd services must be restarted on every deploy** — `investment-research` (the UI) and `investment-research-scheduler` (the daemon that actually places orders) each keep their own in-memory copy of the code; a deploy that only restarts the UI leaves the scheduler daemon running the old order logic. `deploy.sh`/`deploy.ps1`/`setup.sh` always restart both.

### Monday pre-market job

The Friday 21:30 UTC EOD run can place BUY orders that are still sitting in `PLACED` status when markets reopen Monday — T212 doesn't fill market orders outside trading hours, so a Friday-evening order can sit unfilled all weekend. A lot can change in that window (geopolitical events, Sunday futures pricing, macro news), so an order that made sense Friday afternoon may no longer reflect a signal worth acting on by Monday morning.

`data/scheduler.py:_monday_premarket_job()` fires at 07:00 UTC Mondays only (fixed directly in `start_scheduler()`'s own `CronTrigger` — the Tue-Fri sibling job this used to coordinate with, `_pre_market_job()`, was removed 2026-07-31 as confirmed-redundant; see "Pre-market/US-open scheduled runs removed" below) and does two things in sequence:

1. **`_cancel_stale_weekend_orders(config)`** — reads every `PLACED` `BUY` row from `t212_invest_orders` (both ETF and stock orders — they share the table, and a stale order is a stale order regardless of which automation path placed it) older than `_STALE_ORDER_HOURS` (30h). For each, calls `client.cancel_order(order_id)` via the T212 API and updates the row to `CANCELLED` with a `cancelled_at` timestamp, logging `"Monday pre-market: cancelling stale weekend order for {ticker} (placed {N} hours ago)."` No-op if T212 credentials aren't configured; never raises — a failure to cancel one order is logged and the rest still get a chance, matching the "never raises" contract the rest of `brokers/trading212.py`'s automation functions follow.
2. **`_run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)`** — recomputes signals against the latest stored price data (Friday's closing prices; no data pull, since prices haven't changed since Friday close). `maybe_place_t212_orders()` / `maybe_place_stock_orders()` and `maybe_exit_t212_positions()` / `maybe_exit_stock_positions()` all fire as part of this same call (identical to every other analysis run) — a ticker still showing `STRONG_BUY` in `TRENDING_BULL` after cancellation gets a fresh order; a signal that weakened over the weekend does not get a replacement, and any position whose signal now calls for exit is closed.

30 hours was chosen over the ~33.5h a genuine Friday-EOD order would actually be by Monday 07:00 to leave a safety margin, while staying well clear of same-day orders if this job were ever triggered on an unexpected day. Both the hour/minute and the stale-order cancellation are registered directly in `start_scheduler()`, hardcoded at 07:00 UTC — there is no separate `scheduler.runs.monday_premarket` config key. (Until 2026-07-31 this hour/minute was instead read from `scheduler.runs.pre_market`, so the Monday job always matched whatever time its Tue-Fri sibling was configured for; now that the sibling job itself is gone, hardcoding is simpler and equally correct.)

**Cutoff comparison uses `strftime`, not `isoformat()`.** SQLAlchemy's SQLite `DateTime` dialect stores `placed_at` as `"YYYY-MM-DD HH:MM:SS.ffffff"` (space-separated); `datetime.isoformat()` produces `"YYYY-MM-DDTHH:MM:SS.ffffff"` (`T`-separated). SQLite compares these as plain strings, and `' '` (0x20) sorts before `'T'` (0x54) — so whenever an order's `placed_at` falls on the *same calendar date* as the cutoff, an `isoformat()`-built cutoff string would make that order compare as "older" regardless of its actual clock time, incorrectly cancelling orders younger than `_STALE_ORDER_HOURS`. `_cancel_stale_weekend_orders()` builds the cutoff string via `cutoff.strftime("%Y-%m-%d %H:%M:%S.%f")` to match the storage format exactly — the same class of bug documented in "T212 P&L reconstruction" below for `_fx_rate_as_of()`/`_module_scores_as_of()`, caught here via `test_monday_premarket_cutoff_comparison_correct_when_dates_match` in `tests/test_scheduler.py` before it ever reached production.

### Central bank event monitoring — BoJ / ECB (2026-07-28)

Mirrors the existing FOMC reaction job (`data/scheduler.py:_fomc_reaction_job()`, registered at 19:05 UTC weekdays — see the table above and `analysis/fomc.py`) for the two other central banks this system already tracks via dedicated correlation pairs: `USDJPY=X/^GSPC` (carry-trade proxy for BoJ policy) and `VGK/ECBDFR` (European equities vs the ECB deposit rate — both pairs documented under "Cross-asset correlation module" below).

**Dates live in config.yaml, not hardcoded in code — a deliberate departure from the FOMC pattern.** FOMC's decision/minutes dates are a hardcoded module-level list in `analysis/fomc.py`, updated in code annually. BoJ/ECB dates instead live under `central_bank_events.boj.dates` / `central_bank_events.ecb.dates` in `config.yaml` (ISO date strings), read by the new `analysis/central_bank.py` module — both calendars are published a full year in advance and essentially never move once set, so keeping them in config.yaml lets a date be corrected without a code deploy. `central_bank_events.fomc` also exists in config.yaml, but is a **structural placeholder only** — nothing reads it; FOMC continues to source its dates from `analysis/fomc.py` exactly as before. It's included so all three central banks the scheduler watches are documented in one place in config.yaml, not because FOMC's mechanism changed.

**IMPORTANT — update `central_bank_events.boj.dates` and `.ecb.dates` every January** with that year's published meeting calendar (boj.or.jp / ecb.europa.eu), the same way `analysis/fomc.py`'s hardcoded lists get updated annually. As of this writing the lists hold the remaining 2026 dates only.

**`analysis/central_bank.py`** provides:
- `is_boj_decision_day(config, check_date=None)` / `is_ecb_decision_day(config, check_date=None)` — pure, config-driven, no network dependency (mirrors `analysis/fomc.py:is_fomc_decision_day()`, except it takes `config` since dates aren't hardcoded).
- `get_central_bank_events(config, days_ahead=7)` — same dict shape as `analysis/fomc.py:get_fomc_events()` (`date`/`title`/`event_type`/`days_away`), consumed by the Daily Briefing's Key Events strip (below).

**Scheduler jobs** (`data/scheduler.py`) — `_boj_reaction_job()` (registered `boj_reaction`, 03:30 UTC weekdays) and `_ecb_reaction_job()` (registered `ecb_reaction`, 12:30 UTC weekdays) both follow `_fomc_reaction_job()`'s exact shape: fire every weekday via a plain `CronTrigger`, and internally no-op (just a `logger.debug` and return) unless today matches the relevant decision-day list — **not** one-off per-meeting-date `trigger="date"` jobs. This keeps the scheduler's job count fixed (two jobs registered once at startup) rather than growing/shrinking as dates are added, and means updating the date lists in config.yaml takes effect on the next scheduler restart with no re-registration logic needed.
- **BoJ time (03:30 UTC / 04:30 BST)**: BoJ decisions don't land at a fixed time the way the Fed's 2:00 PM US/Eastern release does — they've historically landed anywhere from roughly 02:00 to 05:00 BST. 04:30 BST is a safe default that catches the post-decision market reaction window without firing before a plausible decision time.
- **ECB time (12:30 UTC / 13:30 BST)**: the ECB announces its rate decision at a fixed 12:15 UTC (13:15 CET); this job fires 15 minutes later to capture the immediate reaction (President's press conference follows ~45 minutes after the decision itself, so 12:30 UTC catches the decision reaction, not the press-conference reaction).
- Both call `_run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)` on a decision day — identical to `_fomc_reaction_job()` — then additionally send an alert email via `output/briefing.py:send_central_bank_alert()` (wrapped in its own `try/except` so an email failure never blocks the analysis pipeline that already ran).

**`_fomc_reaction_job()` gained the identical alert-email call the same day (2026-07-28), for consistency across all three central bank jobs.** When BoJ/ECB monitoring first shipped, the FOMC job was deliberately left as analysis-refresh-only, since no `_send_fomc_alert()` had ever existed to mirror — its only effect was a signal refresh, surfaced passively via the Key Events strip below. That asymmetry (BoJ/ECB emailing, FOMC not) was corrected immediately after: `_fomc_reaction_job()` now calls `send_central_bank_alert()` too, with `bank="Fed"`, `watch_tickers=["^GSPC", "TLT", "HYG"]`, and `correlation_pairs=[("TLT", "^GSPC"), ("HYG", "TLT"), ("KBE", "T10Y2Y")]` — reusing three already-defined `RELATIONSHIP_MAP` pairs (flight-to-safety, credit-spread proxy, and yield-curve-vs-bank-stocks) rather than inventing a new one, the same "reuse what's already validated" principle documented under "MIXED-regime research — threshold-reuse lesson" above. All three central bank reaction jobs (FOMC, BoJ, ECB) now follow the exact same shape: fire every weekday, no-op unless today is a decision day, run the analysis pipeline, then send `send_central_bank_alert()` in its own `try/except`.

**`output/briefing.py:send_central_bank_alert(bank, label, watch_tickers, correlation_pairs, context, config)`** — follows the same HTML+plain-text, `_smtp_send()`-based structure as `send_vix_spike_alert()` (see "Email alerts" below), extended with: the current regime (`analysis.regime.get_latest_regime_name()`), the current signal for each ticker in `watch_tickers` (`output.performance.get_current_signal()`), and the latest z-score/divergence status for each pair in `correlation_pairs` (`analysis.correlations.get_stored_correlations()`, matched by exact `ticker_a`/`ticker_b`) — a correlation-lookup failure is caught and logged, degrading to an email with no correlation rows rather than no email at all. `_boj_reaction_job()` passes `watch_tickers=["USDJPY=X", "EWJ"]` and `correlation_pairs=[("USDJPY=X", "^GSPC")]`; `_ecb_reaction_job()` passes `watch_tickers=["VGK", "EWG"]` and `correlation_pairs=[("VGK", "ECBDFR")]`. Email accent colours (`#f59e0b` BoJ / `#6366f1` ECB) match the Asia/Europe region-pill colours used elsewhere in the app (Signals page's MACRO REGIONS strip) rather than inventing new ones.

**UI.** The Daily Briefing's existing Key Events strip (`main.py`, in the FOMC/Fed calendar block) now merges `analysis.fomc.get_fomc_events()` with `analysis.central_bank.get_central_bank_events()` into one sorted list, so BoJ/ECB pills render inline with FOMC pills using the same colour-coded pattern (BoJ amber, ECB indigo). The existing "CAUTION — Fed Decision" banner (shown when a decision is ≤2 days away) was generalised to a small `_CAUTION_META` lookup so BoJ/ECB decisions get their own bank-appropriate wording ("watch USDJPY for carry-trade unwind signals" / "European equity signals (VGK/EWG) may move on the announcement") rather than duplicating the banner-rendering code three times. The Status page's Scheduler Health table gains `boj_reaction`/`ecb_reaction` rows in both the in-process and daemon-mode branches, labelled "BoJ Reaction"/"ECB Reaction" with a "(BoJ/ECB days only)" suffix — like the existing `fomc_reaction` row, the displayed "Next Run" is the next weekday the job *fires* (03:30/12:30 UTC), not necessarily the next actual decision date, since the job itself no-ops on non-decision days; the actual next decision date is what the Key Events strip shows. The Log Viewer's "Scheduler" category filter gained `"boj"`/`"ecb"` keywords alongside the existing `"fomc"`.

**Blackout-window check.** The new job times (03:30 UTC / 04:30 BST for BoJ, 12:30 UTC / 13:30 BST for ECB) fall well outside all four deploy blackout windows documented under "Deployment timing" below (06:45–07:15, 13:15–14:00, and 21:00–22:00 UTC on weekdays, 09:45–10:15 UTC on Sundays) — no change needed to `deploy.sh`/`deploy.ps1`'s blackout-window checks.

Covered by `tests/test_central_bank.py`: `is_boj_decision_day()`/`is_ecb_decision_day()` true/false/missing-config cases, unparseable date strings being skipped rather than raising, `get_central_bank_events()` merging and window-filtering, both reaction jobs' no-op-on-non-decision-day and runs-pipeline-and-sends-alert-on-decision-day paths, a broken alert email never blocking the analysis pipeline, and `send_central_bank_alert()`'s disabled-notifications early return, happy path, and graceful degradation when the correlation lookup fails.

The FRED API key lives in `config.yaml` under `macro.fred_api_key`. Without it, all FRED-sourced scoring returns 0; VIX scoring (sourced from the local `^VIX` price data) still works. FRED series tracked (17 total — 13 US + 4 international, see "Macro module — regional grouping" below): T10Y2Y, T10Y3M, DFII10 (real yields), T10YIE (breakeven inflation), BAMLH0A0HYM2 (HY OAS), BAMLC0A0CM (IG OAS), M2SL, DPCREDIT (discount window), BOGMBASE, CFNAI (Chicago Fed National Activity Index), PERMIT (housing permits), DTWEXBGS (trade-weighted dollar), DFEDTARU (Fed funds rate upper bound), ECBDFR (ECB deposit rate), IRLTLT01DEM156N (Germany 10Y Bund yield), IRSTCI01JPM156N (Japan interbank overnight rate, BoJ policy proxy), CHNGDPRAPSMEI (China real GDP growth, annual). Tickers that are themselves macro instruments (^VIX, TLT, IEF, HYG) receive a neutral macro score of 0 to avoid self-referential scoring.

### Macro module — regional grouping

Every FRED series in `macro.fred_series` (config.yaml) carries a `region` field (`"us"`, `"europe"`, or `"asia"`) alongside its existing `label`/`category`. A series with no `region` field defaults to `"us"` — this is what keeps the 13 original US series working unchanged, and what a config predating this feature falls back to automatically. `analysis/macro.py:_series_region(series_id, config)` reads it.

**Why regional, not flat.** Every FRED series used to contribute equally to one flat-averaged composite score, regardless of which economy it described. That was fine while every series was US-sourced, but averaging a Eurozone rate or a China GDP reading in at the same weight as a US series overstates how much either region should actually move a score for a portfolio that is predominantly US-exposed (with secondary European exposure via VGK/EWG-style tickers and Asian/EM exposure via FXI/EEM and commodity-linked tickers). `analysis/macro.py:_regional_macro_score(rows, vix_score, config, extra_scores_by_region=None)` groups every scored series (+ VIX, always folded into `"us"` since it's a US-sourced index) by region, averages each region's own scores, then blends the regional averages using `signals.macro_regional_weights` (config.yaml — default `us: 0.60, europe: 0.25, asia: 0.15`, must sum to 1.0). Both `compute_macro()` (the per-ticker confluence-module score) and `get_macro_dashboard()` (the UI composite) call this same function — no new confluence weight was needed, since the macro module's total weight in `REGIME_WEIGHTS` is unchanged; only what feeds *into* that one score changed. `compute_macro()`'s return shape is unchanged (`score`, `yield_curve_spread`, `yield_curve_regime`, `vix_level`, `vix_regime`) — no downstream caller needed to change.

**Graceful degradation.** If a region has no scored series available at all (e.g. FRED unreachable that run, or every series in that region failed to fetch), `_regional_macro_score()` drops it from the blend and redistributes its configured weight proportionally across the remaining regions with data, rather than either treating the missing region as a neutral 0 (a genuinely-neutral Europe and "Europe fetch failed entirely" would then look identical) or raising. If not even one region has any data, the composite is 0.0 — the same "no data" behaviour the module always had.

**The 4 new international series** (added 2026-07-07, scored in `_score_fred_series()` exactly like every existing series — no generic "score direction" config field, matching this file's existing per-series-branch style rather than introducing a new abstraction for four series):

| Series | Region | Frequency | Direction |
|---|---|---|---|
| `ECBDFR` (ECB Deposit Facility Rate) | europe | Daily | `bearish_high` — cut is bullish, hike is bearish (identical logic to `DFEDTARU`, applied to the ECB) |
| `IRLTLT01DEM156N` (Germany 10Y Bund Yield) | europe | Monthly | Context-dependent — no natural "good" level like a real yield has, so scored purely off its own 2-year z-score |
| `IRSTCI01JPM156N` (Japan Interbank Overnight Rate, BoJ Proxy) | asia | Monthly | `bearish_high` — a hike raises carry-trade-unwind risk for global risk assets (e.g. the August 2024 selloff); a cut eases it |
| `CHNGDPRAPSMEI` (China Real GDP Growth, annual) | asia | Annual | `bullish_high` — scored off year-over-year growth (a level series, like `M2SL`/`BOGMBASE` — the level itself isn't meaningful, only its trend) |

**`IRSTCB01JPM156N` originally substituted for an invalid series ID.** The originally proposed series for the BoJ policy rate was `"MOPJPY"` — verified (2026-07-07) not to be a real FRED series ID. `IRSTCB01JPM156N` ("Interest Rates: Immediate Rates (< 24 Hours): Central Bank Rates: Total for Japan", OECD-sourced, monthly) was FRED's actual BoJ-policy-rate-equivalent series and was substituted in directly, the same way `VIXY→VXX` is substituted in `trading212_invest.ucits_mapping` (see "Ticker substitution" below) — documented inline in config.yaml rather than silently swapped.

**Both Asia series were replaced again 2026-07-10 after going stale on FRED's own side** (see "A real FRED-side data limitation surfaced during backfill" under "FRED historical backfill" below for when this was first discovered). `IRSTCB01JPM156N` stopped updating December 2023 — the Daily Briefing macro dashboard was showing a 952-day staleness warning and a stuck value of 0.30%, against an actual BoJ policy rate of 1.0%, meaning the module had been silently scoring this series neutral-to-wrong for well over a year. It was replaced with **`IRSTCI01JPM156N`** ("Interest Rates: Immediate Rates: Call Money/Interbank Rate: Total for Japan", OECD-sourced, monthly), which is actively updated and tracks BoJ policy decisions accurately — same `bearish_high` direction/threshold logic, unchanged. Verified live after deploy: it reads ~0.7% against an officially-announced 1.0% target — this is expected, not a bug, since `IRSTCI01JPM156N` is the actual market call-money rate rather than the announced target, and running slightly below it is normal (arguably more representative of real borrowing costs/carry-trade dynamics than the headline figure). `CHNGDPNQDSMEI` (China nominal GDP, quarterly) stopped updating July 2023 (1,105 days stale) and was replaced with **`CHNGDPRAPSMEI`** ("National Accounts: GDP by Expenditure: Constant Prices: GDP: Total for China"), an OECD annual real-GDP-growth series last updated March 2026 (5.0% growth for 2025) — same `bullish_high` direction, but scored off year-over-year rather than quarter-over-quarter change since the series is annual, not quarterly (see the frequency column above and the per-series staleness override immediately below).

**International series can lag by more than one reporting period** (annual China GDP especially). `fetch_and_store_macro_indicators()` uses a ~6-year `observation_start` lookback for any series with `region != "us"`, rather than the ~2-year window used for US series — a fixed 2-year window would silently exclude every row FRED has for a series lagging by more than a couple of quarters, making it report "insufficient data" indefinitely even though a perfectly usable last-known value exists further back. There is no freshness requirement on the value used: whatever the last available observation is gets scored, regardless of age. This is display-only surfaced in the dashboard: `_STALE_THRESHOLD_DAYS = {"us": 35, "europe": 90, "asia": 90}` flags an `extra_series` tile's `context` string with `"(stale — Nd old)"` past that region's threshold — international series get a much longer grace period than the mostly daily/monthly US series before being flagged, matching how much more slowly they update. `_STALE_THRESHOLD_DAYS_SERIES_OVERRIDE = {"CHNGDPRAPSMEI": 800}` further overrides the flat 90-day Asia default for this one series specifically. This was originally shipped at 400 and had to be raised the same day: FRED dates each annual observation at the *start* of the year it covers (e.g. the "2025" value is dated 2025-01-01), not its publish date, and OECD's own publish lag runs ~14-15 months on top of that — verified live 2026-07-10, where the freshly-published 2025 observation (5.0% growth, published ~March 2026) was already displaying as "555d old" purely from that date-stamping convention, comfortably clearing the 400-day override even though the data was current. 800 covers a full normal publish cycle (~14-15 months to first publish, plus up to ~12 more months as the latest available row before the next annual print lands — a worst-case just under 800 days) while still catching genuine multi-year discontinuation, since the predecessor series this replaced went 1,105 days without an update. This never blocks or degrades scoring, only the tile's displayed text.

**US monthly series had the identical dating-convention bug, just not yet caught (fixed 2026-07-15).** `CFNAI`, `M2SL`, and `BOGMBASE` were all flagged `"(stale — 74d old)"` on the Macro Dashboard despite the underlying data being current — verified directly against the FRED API (not just inferred from the codebase): all three genuinely had no observation newer than 2026-05-01 available from FRED itself at the time. Same root cause as `CHNGDPRAPSMEI` above — FRED dates each observation at the *start* of the period it covers, not its publish date — just at monthly instead of annual scale: these three publish 3-5 weeks after month-end and then remain the latest value for another ~month until superseded, so a worst-case age of ~77-86 days between real updates is normal, not a fetch failure. The flat 35-day `"us"` default (tuned for the mostly-daily/weekly series in the same region) was flagging this routine publish lag as broken. `_STALE_THRESHOLD_DAYS_SERIES_OVERRIDE` gained `CFNAI: 90`, `M2SL: 90`, `BOGMBASE: 90` — reusing the existing europe/asia 90-day default rather than inventing a new constant, since the worst-case gap comfortably fits inside it.

**`PERMIT` had no staleness detection at all — a distinct, worse gap found during the same investigation, not a second instance of the false positive above.** `PERMIT` (like `DFII10`, `T10YIE`, `BAMLH0A0HYM2`, `BAMLC0A0CM`, `T10Y2Y`, `T10Y3M`, `DFEDTARU`) is in `main.py`'s `_SPECIALTY` set — series rendered via their own bespoke dashboard tile rather than the generic `extra_series` loop, which is the only place `age_days`/`stale` were ever actually computed. PERMIT shares CFNAI/M2SL/BOGMBASE's exact monthly period-start-dating pattern (confirmed against FRED: also stuck on the 2026-05-01 observation at the time) but, being bespoke-rendered, had never had staleness computed in either direction — so nothing on the dashboard would have revealed a genuine multi-month gap either. Fixed by adding `PERMIT: 90` to the override dict and computing `age_days`/`stale` inside PERMIT's bespoke block, surfaced on the Daily Briefing's Housing Permits tile the same `"(stale — Nd old)"` way the extra_series tiles already show it.

**The remaining 7 `_SPECIALTY` series had the identical structural gap, closed the same day in a separate commit.** `DFII10`, `T10YIE`, `BAMLH0A0HYM2`, `BAMLC0A0CM`, `T10Y2Y`, `T10Y3M`, and `DFEDTARU` are all bespoke-rendered and, like PERMIT, had never computed `age_days`/`stale`. Unlike CFNAI/M2SL/BOGMBASE/PERMIT, though, all 7 were confirmed genuinely daily series directly against the FRED API (`"frequency": "Daily"` / `"Daily, Close"` / `"Daily, 7-Day"`, latest observation dated today or the prior business day at the time of the check) — no period-start-dating mismatch, so the plain 35-day `"us"` default applies with no per-series override needed. This was a latent monitoring gap rather than an active bug — all 7 were fresh when found — but without a staleness computation, a future silent failure on any of them (FRED discontinuation, a swallowed fetch error) would have had no way to surface on the dashboard at all, the same blind spot PERMIT had. The repeated age/threshold/`is_stale` logic (previously duplicated between the `extra_series` loop and the PERMIT block) was factored into a shared `analysis/macro.py:_series_staleness(row, sid, region)` helper, now used by all 9 staleness checks in the module — the 2 monthly-series overrides above plus these 7 daily-series defaults — so a future threshold change applies uniformly. Shipped as two separate commits despite landing the same day: the CFNAI/M2SL/BOGMBASE/PERMIT change fixed an active bug (a real false-positive users could see), while this one adds defensive monitoring to series that were never actually broken — different intent and review posture, kept apart so a future `git bisect` on either doesn't pull in the other.

**UI.** The Macro Dashboard (Daily Briefing) shows a regional breakdown line under the composite score bar (`US: +0.50 · Europe: +0.25 · Asia: +0.10`) plus the nominal configured weights (`Regional weights: US 60% · Europe 25% · Asia 15%`), and groups indicator tiles under `🇺🇸 United States (60%)` / `🇪🇺 Europe (25%)` / `🌏 Asia (15%)` headers — a series with no stored data yet (no FRED key, or a failed fetch) shows a "No data" tile the same way PERMIT/Fed Rate already did, rather than being hidden; the section header itself only disappears if config.yaml defines no series at all for that region. The Signals page's compact header block gets a `MACRO REGIONS` strip showing each region's score converted to a 0–100% "bullishness" pill (`(score + 1) / 2`), alongside the existing overlay/catch-up strips.

**Signals page `MACRO REGIONS` strip label was ambiguous, fixed 2026-07-28.** The pill values themselves were always genuine bullishness (`(score + 1) / 2`, never the config weight), but the strip's trailing text read `"bullishness by region (macro_regional_weights) — see Macro Dashboard for detail"` — naming the config key rather than showing the actual weight numbers, so a reader had no way to tell the pill percentage (bullishness) apart from the region's portfolio weight, and the caption immediately below it (`"...Europe 0% = both tracked European series are currently bearish..."`) referenced a number the strip text didn't visibly corroborate. Fixed by rendering the live `region_weights_config` values inline instead: `"regional bullishness — US 60% · Europe 25% · Asia 15% portfolio weights · see Macro Dashboard for detail"` — the weights are read from `get_macro_dashboard()`'s `region_weights_config` at render time (same source the Macro Dashboard's own "Regional weights:" line uses), not hardcoded, so the text stays correct if `signals.macro_regional_weights` in config.yaml ever changes. No change to the pill computation, the caption below it, or any other page.

### Macro module — release-calendar staleness (2026-07-16)

The day-count thresholds and overrides above (`_STALE_THRESHOLD_DAYS`, `_STALE_THRESHOLD_DAYS_SERIES_OVERRIDE`) were always a guess at each series' publish lag — CFNAI/M2SL/BOGMBASE/PERMIT needed a hand-tuned 90-day override the day after they were first checked, and the next new monthly series added to `config.yaml` would silently reproduce the same false-stale bug (or, for a bespoke-rendered `_SPECIALTY` series, no detection at all) until someone happened to notice and tune another override by hand. `_series_staleness()` now checks FRED's actual publish schedule first, via `analysis/macro.py:_calendar_staleness()`, falling back to the day-count threshold only when the calendar check can't be computed — the day-count overrides above remain in place as that fallback, unchanged.

**Mechanism.** Per series: look up its `release_id` (`fred/series/release`) and its own reporting frequency (`fred/series` → `frequency_short`), then that release's full publish calendar (`fred/release/dates`, `include_release_dates_with_no_data=true` — includes future scheduled dates, not just past ones). `_next_expected_release(series_id, last_obs_date)` computes the earliest calendar date on/after `last_obs_date + 2 × period_days` (`period_days` derived from `frequency_short`: D→1, W→7, BW→14, M→31, Q→92, SA→183, A→366). `_calendar_staleness()` flags stale only once today is more than `_RELEASE_GRACE_DAYS` (2) past that computed date.

**"2×", not "1×", period_days is deliberate.** Every series checked publishes with real-world lag longer than one full reporting period — e.g. CFNAI's May value (dated 2026-05-01) isn't published until 2026-06-25 (verified via FRED's `realtime_start` for that exact observation) — so a 1× threshold would land on the release still finishing the *current* period's backlog, not the one bringing the next new observation.

**Verified against real FRED vintage data (`realtime_start` — the date an observation actually became public) before being trusted, not assumed from the release calendar's shape alone.** The formula matched exactly for six monthly-cadence series (CFNAI, M2SL, BOGMBASE, PERMIT, IRSTCI01JPM156N, IRLTLT01DEM156N) and was separately checked for two daily series with meeting-driven release *names* that could plausibly suggest a sparse calendar (DFEDTARU on "FOMC Press Release", ECBDFR on "Key ECB Interest Rates") — both confirmed to publish a fresh `realtime_start` every business day regardless of whether the underlying rate changed, so the formula correctly computes a short (2–3 day) next-expected-release for both rather than jumping to the next policy meeting.

**Release IDs found (10 distinct, covering 17 configured series) — series sharing a release_id share one cached calendar, fetched once per refresh, not once per series:**

| release_id | Release name | Series | Cadence |
|---|---|---|---|
| 18 | H.15 Selected Interest Rates | `DFII10`, `DPCREDIT` | Daily |
| 304 | Interest Rate Spreads | `T10YIE`, `T10Y2Y`, `T10Y3M` | Daily |
| 209 | ICE BofA Indices | `BAMLH0A0HYM2`, `BAMLC0A0CM` | Daily |
| 21 | H.6 Money Stock Measures | `M2SL`, `BOGMBASE` | Monthly |
| 219 | Chicago Fed National Activity Index | `CFNAI` | Monthly |
| 27 | New Residential Construction | `PERMIT` | Monthly (irregular, ~23 dates/year — multiple releases some months) |
| 17 | H.10 Foreign Exchange Rates | `DTWEXBGS` | Weekly (~52 dates/year) — see the DTWEXBGS note below |
| 101 | FOMC Press Release | `DFEDTARU` | Daily (365 dates/year, every calendar day) |
| 484 | Key ECB Interest Rates | `ECBDFR` | Daily |
| 205 | Main Economic Indicators (OECD) | `IRLTLT01DEM156N`, `IRSTCI01JPM156N`, `CHNGDPRAPSMEI` | Monthly batch — mismatched for `CHNGDPRAPSMEI` specifically, see below |

**`DTWEXBGS` is labelled "Daily" by FRED and its own observations do land daily** (confirmed directly — 2026-07-10 through 2026-07-06 each have their own value), **but its release_id's own calendar is only weekly** (~52 dates/year, apparently Mondays). This doesn't cause a false-stale risk the way the monthly-series mismatch below does — a weekly-cadence calendar for a series that updates daily just makes the computed next-expected-release *looser* (up to ~7 days) than the series' true daily cadence, which is still tighter than the old flat 35-day default and therefore strictly an improvement, not a regression. Left as-is rather than special-cased.

**`CHNGDPRAPSMEI` is permanently excluded from the calendar mechanism** (`_CALENDAR_STALENESS_UNSUPPORTED` in `analysis/macro.py`) and keeps using its existing hand-tuned 800-day day-count override unconditionally — this is not a fallback-on-failure case, it's a deliberate, permanent exclusion. It shares release_id 205 with two Monthly series but is itself Annual, so the shared release fires ~12× more often than `CHNGDPRAPSMEI` itself gets new data. Checked directly against real vintage history for two consecutive annual prints (not assumed): the 2×-period formula, applied to the 2024 print (`realtime_start` 2025-02-17), computes `next_release = 2026-01-15` — but the 2025 print's actual `realtime_start` was 2026-03-16, two months later. Shipping the formula for this series would have reproduced roughly two months of false-stale flagging every year, on exactly the series this whole effort started by fixing. Separately, release_id 205's own calendar isn't populated far into the future the way domestic Fed/Treasury/Census releases are — confirmed live 2026-07-15, it had no scheduled dates past 2026-06-15, versus a full year ahead for every other release_id checked — so the mechanism would frequently have no forward date to check against for this series regardless of the frequency mismatch.

**`IRLTLT01DEM156N`/`IRSTCI01JPM156N` (also on release_id 205) hit the same calendar limitation on a recurring basis, but were NOT excluded — confirmed to be a different, narrower case, not a second CHNGDPRAPSMEI (found 2026-07-15, the day after the mechanism first deployed, when the log showed exactly two recurring `WARNING`s and both traced back to these series).** Unlike `CHNGDPRAPSMEI`, the 2×-period formula computes *correctly* for both whenever release_id 205's calendar has enough forward coverage — verified against real vintage data in the original check (their May 2026 next-release predictions matched FRED's actual `realtime_start` exactly, the same validation CFNAI/M2SL/BOGMBASE/PERMIT got). The problem is purely that release_id 205's calendar frequently *doesn't* reach far enough forward for a Monthly series' 2×31-day threshold, the same forward-horizon limitation documented for `CHNGDPRAPSMEI` above. Confirmed this is structural, not a stale-cache artifact worth just re-pulling: a completely fresh, independent live query (not reading any cache) returned the identical 2026-06-15 ceiling — meaning FRED's own `release/dates` data for this release genuinely has no entry past mid-June yet, despite the release's own consistent ~15th-of-month pattern (Jan 15, Feb 16, Mar 16, Apr 15, May 15, Jun 15) making a July entry already a month overdue. OECD's release apparently isn't added to FRED's calendar until close to when it actually happens, unlike domestic Fed/Treasury/Census releases. `_CALENDAR_KNOWN_LIMITED_HORIZON = {"IRLTLT01DEM156N", "IRSTCI01JPM156N"}` (distinct from `_CALENDAR_STALENESS_UNSUPPORTED`, which skips the check entirely) marks these two for **log-level only** — the calendar check still runs and still takes over correctly the moment OECD's next date lands, only the routine `WARNING` is downgraded to `INFO` so the recurring, expected fallback doesn't read as "investigate this" in server logs the way a genuine lookup failure should.

**Caching — read path never calls FRED.** `refresh_release_calendars(config)` is the *only* function that calls `series/release`, `series`, or `release/dates` — it runs once per EOD scheduler job, immediately after `fetch_and_store_macro_indicators()`, in its own `try/except` (a failed refresh never blocks the rest of the pipeline), **and from the Status page's "Refresh Data + Analysis" button** (a second, independent call site for the same steps — see below for why that needed its own fix). Results are cached in `system_metadata` as JSON — `fred_release_meta_{series_id}` (`release_id`, `frequency_short`, `fetched_at`) and `fred_release_calendar_{release_id}` (`dates`, `fetched_at`) — following the same key-value-JSON pattern `learned_weights`/`weight_history` already use. `_next_expected_release()`/`_calendar_staleness()` only ever *read* this cache (`get_metadata_str`); they never make a live HTTP call. This matters because `get_macro_dashboard()` (and therefore `_series_staleness()`) runs on every Daily Briefing/Signals page render — a page render must never block on a live FRED call the way `compute_macro()` never calls FRED directly either, always reading `macro_indicators` instead. A cache entry older than `_RELEASE_CACHE_TTL_HOURS` (36 — a bit over one EOD cycle, so a single missed refresh doesn't immediately degrade) is treated as absent. `_series_staleness()` logs at `WARNING` when it falls back to the day-count threshold for an otherwise-unexplained reason, `INFO` for the known-recurring `_CALENDAR_KNOWN_LIMITED_HORIZON` case above, and not at all for `CHNGDPRAPSMEI` (which always and deliberately uses the fallback, so warning on every call would just be log noise for expected, permanent behaviour).

Covered by `tests/test_macro.py`: calendar caching and cross-series calendar deduplication, per-series/per-calendar failure isolation, `CHNGDPRAPSMEI` never contacting FRED, the six verified monthly-series vintage cases plus the DFEDTARU/ECBDFR short-gap cases, not-stale/stale boundaries, every fallback trigger (missing cache, expired cache, calendar not reaching the needed date), and the fallback-warning logging (present/absent as appropriate). The 32 pre-existing day-count staleness tests needed no changes and now double as the regression guarantee for the fallback path, since none of them seed a release-calendar cache.

**The Status page's "Refresh Data + Analysis" button is a second, independent call site for the EOD steps — found missing this wiring the day after ship.** `main.py`'s manual-controls handler for that button doesn't call `data/scheduler.py:_eod_job()`; it's a separate, hand-rolled sequence (price pull → `fetch_and_store_macro_indicators()` → `run_analysis()` → `generate_signals()`) that happens to mirror the EOD job's steps rather than reusing them. Wiring `refresh_release_calendars()` into `_eod_job()` alone missed this second path entirely — clicking the button ran a full data refresh with no calendar refresh and no log line from `refresh_release_calendars()` at all, silently leaving every series on the day-count fallback indefinitely if the scheduler daemon's own EOD run hadn't fired yet. Fixed by adding the identical `try/except`-wrapped call immediately after the button's existing FRED-fetch step. **Any future call site that replicates EOD-job steps needs the same check** — `grep -rn "fetch_and_store_macro_indicators"` across the repo is the fastest way to find every place that needs to stay paired with `refresh_release_calendars()`; as of this fix there are exactly two (the scheduler job and this button), both wired.

**Backtest mirroring — reverted to latest-observation-only (2026-07-08).** `analysis/backtest.py` does **not** read `compute_macro()`'s stored `indicators.macro_score` value at all — like every other module in the backtest engine, it re-implements macro scoring as a fully independent vectorized function, `_macro_scores()`. It imports `_series_region()`/`_regional_weights()` directly from `analysis/macro.py` (shared core, not a re-hardcoded mapping) so the regional blend formula itself always matches the live path. However, `_macro_scores()` deliberately does **not** feed the backtest a time-varying per-date history: each FRED series' single most recent stored value (from `_load_macro_history()`'s full `macro_indicators` read) is broadcast as a **constant** across the entire backtest date range, exactly matching what `compute_macro()` has always done live — it only ever reads the latest row per series, never a historical lookup for a specific past date. This was not the original design (an earlier version read genuine per-date history with graceful degradation when a region had no data yet); see "Macro module regime-weight rebalance (2026-07-08)" immediately below for why it was rolled back. `analysis/macro.py:backfill_macro_indicators()` (see "FRED historical backfill" further below) still populates full multi-year history into `macro_indicators` regardless — that depth just isn't consumed by the backtest yet, pending the deferred re-tuning study.

### Macro module regime-weight rebalance (2026-07-08)

Shortly after the regional-grouping feature shipped, a full historical FRED backfill (see "FRED historical backfill" below) was built specifically so the backtest's macro module could use genuine multi-year history instead of a single snapshot. Running a full 2019-present backtest immediately afterward surfaced a serious regression, and the investigation that followed changed the conclusion from "ship the deeper history" to "revert the backtest to latest-observation-only, pending a bigger study":

**The regression.** With real FRED history loaded via `_load_macro_history()` and fed into `_macro_scores()` as a genuinely time-varying per-date series (each date reading whatever the actual historical value was, not a constant), a full tradeable-asset 2019-present backtest produced:

| | Documented baseline | With real macro history (macro weight 0.30) |
|---|---|---|
| Trades | 253 | 752 |
| Win rate | 47.0% | 42.6% |
| Profit factor | 2.48 | 1.86 |
| Sharpe | 0.792 | 0.581 |
| Max drawdown | -30.3% | **-64.6%** |
| Macro module agreement % (module attribution) | — | 89.6% (up from 18.2% with the pre-backfill single-snapshot data used to validate the regional-grouping feature itself) |

The mechanism: `TRENDING_BULL`'s `macro` weight was 0.30 — the highest of any module — set by the 2026-07-04 rebalance (below) based on a study that measured macro agreeing with trade direction only 0.9% of the time. That 0.9% figure was itself a symptom of a bug: `fetch_and_store_macro_indicators()` only ever stored each series' single latest snapshot, so `macro_indicators` held almost no real history and the module scored near-neutral on nearly every historical date. Once genuine history existed, macro started agreeing with trade direction 89.6% of the time instead of 0.9% — and had *negative* discriminating power once it did (41.7% win rate when agreeing vs. 50.0% when disagreeing). A 0.30 weight on a module that fires on 90% of dates, with a mild bias toward the wrong outcome, is a very different thing from a 0.30 weight on a module that almost never fires — hence the trade-count explosion and drawdown collapse.

**Four weight attempts, none recovered — all on identical price/FRED data, only `TRENDING_BULL`'s weights changed:**

| Attempt | trend | mean_rev | volatility | volume | macro | correlation | Trades | Win rate | PF | Sharpe | Max DD | Macro agreement % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (documented baseline, pre-backfill) | 0.20 | 0.05 | 0.20 | 0.15 | 0.30 | 0.10 | 253 | 47.0% | 2.48 | 0.792 | -30.3% | 18.2% |
| 1 — original weights, real history | 0.20 | 0.05 | 0.20 | 0.15 | 0.30 | 0.10 | 752 | 42.6% | 1.86 | 0.581 | -64.6% | 89.6% |
| 2 — macro cut to 0.15 | 0.25 | 0.05 | 0.25 | 0.20 | 0.15 | 0.10 | 769 | 42.5% | 2.01 | 0.621 | -74.1% | 55.1% |
| 3 — macro cut to 0.10 | 0.25 | 0.05 | 0.30 | 0.20 | 0.10 | 0.10 | 798 | 41.9% | 1.95 | 0.601 | -75.2% | 53.1% |
| 4 — macro to 0.00, proportional redistribution | 0.2857 | 0.0714 | 0.2857 | 0.2143 | 0.00 | 0.1429 | 875 | 43.8% | 2.13 | 0.662 | -73.4% | 46.3% (weight 0, so purely incidental) |

Trade count kept *climbing* as macro's weight fell (752 → 769 → 798 → 875) and max drawdown never recovered below roughly -64% in any attempt — including attempt 4, where macro had **zero** influence on scoring at all. That result rules out "macro's weight is miscalibrated" as the (whole) explanation: if removing macro's influence entirely still produces a max drawdown more than double the baseline's, the problem cannot be macro's weight specifically.

A mechanistic hypothesis was checked directly against the data rather than assumed: attempt 1's own module attribution described trend/volume as "100%/98.8% agreement" in `TRENDING_BULL`, which suggested that concentrating more weight on trend/volatility/volume (attempts 2-3) might be mechanically saturating the `0.55` `STRONG_BUY` threshold regardless of what any other module said. Checking the actual per-date signal history (not the entered-trades subset the original figure came from) showed this isn't true: trend scores +1 on only 64.8% of all `TRENDING_BULL` days, volatility 46.9%, volume 3.5% — nowhere near universal. `STRONG_BUY` day frequency was also roughly flat across all four attempts (29.4% / 26.6% / 28.4% / — day-level not computed for attempt 4). The "100%/98.8%" language in the 2026-07-04 study was specific to the 347 trades that had already been *entered* in that narrower study, not a general day-level agreement rate — a materially different statistic than it reads as.

**Conclusion.** This was never really a single-module weight-calibration problem. The entire `TRENDING_BULL` weight scheme — all six weights, and quite possibly the `0.55` `STRONG_BUY` threshold itself — was implicitly tuned, over several rebalances, around a macro module that was silently near-inert for as long as it existed. Fixing macro doesn't just mean re-deriving macro's own weight; it invalidates the equilibrium every other weight was tuned against too, and no single-module adjustment (four were tried) can cleanly re-derive that from scratch. A proper fix needs a full re-tuning study — re-running the module-attribution methodology across all six modules using real post-backfill data, and likely re-examining the `0.55` threshold too — which is materially larger scope than a weight tweak and is **deferred, not attempted here**.

**What shipped as a result:**
- `REGIME_WEIGHTS[Regime.TRENDING_BULL]` in `analysis/regime.py` is **unchanged** — back to the 2026-07-04 values (`macro: 0.30`), which is what the 253-trade documented baseline still reflects.
- `analysis/backtest.py:_macro_scores()` was reverted to latest-observation-only (see the "Backtest mirroring" note above) — the backtest does not consume the backfilled history's time-varying depth for now.
- `analysis/macro.py:backfill_macro_indicators()`, the Status page's "Backfill FRED History" button, the incremental-fetch changes to `fetch_and_store_macro_indicators()`, and the regional-grouping feature itself are all kept — none of that is live-trading-affecting, and the backfilled history remains necessary groundwork for the deferred re-tuning study.
- The 253-trade baseline documented throughout this file (see the "Backtesting engine" callout box) **remains the reference** — verified to still reproduce (227 trades, 44.9% win rate, PF 2.60, Sharpe 0.779, max drawdown -29.5% on a fresh run with today's latest macro snapshot rather than whatever snapshot was live when 253 was first measured — the same order of magnitude and well within the kind of day-to-day variation a single-snapshot macro input would be expected to produce, not a regression).
- A full six-module (and possibly threshold) weight re-tuning study using the now-available real historical FRED data is the natural next step, whenever it's prioritized.

### TRENDING_BULL module weight re-tuning study (2026-07-08)

The deferred re-tuning study flagged above was attempted the same day, following a structured methodology: derive candidate weights from genuine module-attribution discriminating power, then validate each candidate against the actual production backtest path.

**A methodology problem surfaced before any weight was derived.** The plan called for computing module attribution from a backtest run using `_macro_scores()` exactly as shipped (latest-observation-only — see "Backtest mirroring" above). Running that and inspecting the per-trade module scores directly showed the attribution would have been meaningless: with macro's score a single constant broadcast across the *entire* backtest window, and — as a consequence of that flat macro contribution — trend/volatility/volume all also landing at their single maximum value on every one of the 227 entered trades (a selection artifact: the confluence score can only reach the 0.55 `STRONG_BUY` threshold when they're all maxed, given macro contributes nothing that day), **four of the five modules being tuned had zero score variance in the sample**. `module_attribution()`'s own output for that run reflects this directly: Trend/Volatility/Volume all show exactly 100%/0% agreement splits, and Macro 0%/44.9% — degenerate figures driven by which single arbitrary value each module happened to be, not measured discriminating power. Only `mean_reversion` (which varied between 0 and −1 across trades) had any real signal to attribute. Deriving 4 of 5 weights from this would have meant deriving them from noise.

**Fix: split attribution from validation.** Module attribution was instead computed from a full-history run — `_macro_scores()` temporarily using genuine per-date time-varying history (the same mechanism investigated and reverted above), giving every module real score variance to measure. The resulting candidate weight sets were then validated by running the actual, currently-shipped backtest path (latest-observation-only) — so the go/no-go decision is still made against what production actually does, only the *attribution* step draws on deeper data.

**Attribution (752-trade full-history run, `correlation` excluded per this study's own constraint — fixed at 0.10 throughout):**

| Module | Agreement % | Win rate (agree) | Win rate (disagree) | Discriminating power |
|---|---|---|---|---|
| Trend | 85.0% | 44.8% | 30.1% | +14.7 |
| Volume | 15.0% | 47.8% | 41.6% | +6.2 |
| Volatility | 59.4% | 41.6% | 43.9% | −2.3 |
| Macro | 89.6% | 41.7% | 50.0% | −8.3 |
| Mean Reversion | 0.0% | 0.0% (undefined — never agrees) | 42.6% | −42.6 |

**Deriving weights from signed discriminating power** (which can't be used directly as a proportion — negative values would need negative weight) used a shift-then-normalize-then-clamp method: shift all five values by `−min(dp) + 1` into strictly positive range (preserving relative spacing), allocate the 0.90 non-correlation budget proportionally to the shifted values, then clamp any result under the 0.05 floor up to 0.05 and proportionally shrink the rest to re-absorb the difference (no value hit the 0.35 cap in either direction). Result: **trend 0.268, mean_reversion 0.05 (floor), volatility 0.190, volume 0.229, macro 0.163**.

**Validation — both candidates underperformed the baseline on every risk-adjusted metric, using the real production backtest path:**

| Weights | Trades | Win rate | PF | Sharpe | Max DD |
|---|---|---|---|---|---|
| **Baseline** (documented; also re-confirmed this same day: 227/44.9%/2.60/0.779/-29.5%, see above) | 253 | 47.0% | 2.48 | 0.792 | -30.3% |
| Attribution-derived (trend 0.268, mean_rev 0.05, vol 0.190, volume 0.229, macro 0.163) | 353 | 42.5% | 2.13 | 0.667 | -43.7% |
| Conservative fallback (macro cut to floor 0.05, trend/volatility/volume absorb the freed 0.25 proportionally to their baseline shares: trend 0.2909, volatility 0.2909, volume 0.2182, mean_rev 0.05) | 875 | 44.3% | 2.16 | 0.675 | -73.4% |

Neither clears any of the three stated bars (PF ≥~2.48, max DD no worse than -35%, Sharpe ≥~0.75) — both are worse on every axis, and the conservative fallback in particular reproduces almost exactly the same catastrophic drawdown as the earlier investigation's macro=0.05-adjacent attempts, consistent with that investigation's conclusion that the problem isn't really macro's specific weight (see above).

**Conclusion: inconclusive, not negative.** `REGIME_WEIGHTS[Regime.TRENDING_BULL]` is left unchanged (`macro: 0.30`, matching the 2026-07-04/2026-07-08-confirmed baseline). This result doesn't mean no better weight set exists — it means the two candidates tried, derived from attribution against real *historical* macro conditions during 2019–2026, don't hold up when validated against a backtest that (correctly, matching live) only ever sees *today's* single macro snapshot. The attribution and the validation are measuring genuinely different things by design (that's the whole reason for splitting them), and a wider search (different derivation formulas, a genuine six-module joint search, or revisiting the `0.55` `STRONG_BUY` threshold itself) might still find something better — none of that was attempted here. **Repeat this study once 20+ real T212 closed positions exist** (`t212_pnl` — see "Feedback learning" above for the existing threshold this system already tracks toward) so attribution can be computed against genuine executed outcomes instead of a simulated backtest at all; that would sidestep both the frozen-snapshot degeneracy and the historical-vs-live scoring mismatch this study had to work around.

### FRED historical backfill

`fetch_and_store_macro_indicators()` (the function the EOD scheduler job calls) originally only ever wrote each FRED series' single most recent observation to `macro_indicators` — sufficient for the live per-ticker macro score, which only ever needs "today's" value, but it meant the table held almost no historical depth beyond however long that job itself had been running. `analysis/macro.py:backfill_macro_indicators(config, start_date="2019-01-01")` fixes this retroactively: it fetches each configured series' **full** history from `start_date` through today and stores every observation as its own row (not just the latest), using the same per-date rolling-window scoring `_fetch_scored_series()` computes for the incremental fetch (see below) — so backfilled and incrementally-fetched rows are scored identically, never using look-ahead statistics (each date's `z_score_2y` is a trailing window ending at that date, never the whole fetched span's mean/std — see `_fetch_scored_series()`'s own docstring).

Run it via the Status page's **"Backfill FRED History"** button (Manual Controls section, alongside "Run Analysis Now" / "Refresh Data + Analysis") after a fresh deployment, or whenever a new series is added to `config.yaml`'s `macro.fred_series` — otherwise that series has no history until enough EOD runs have accumulated it one day at a time. Rate-limited to roughly one series per 0.5s between FRED calls (17 series → ~8s of throttling) to stay comfortably within FRED's request limits; the whole backfill typically completes in well under a minute. On completion the button reports `✓ Backfill complete — {N} observations stored across {M} series (earliest: {date})`; a series with too little data (or a FRED-side failure) is reported separately without aborting the rest.

**A real FRED-side data limitation surfaced during backfill, not a bug**: `BAMLH0A0HYM2`/`BAMLC0A0CM` (HY/IG credit OAS) only have data on FRED itself from 2023-07-07 onward — confirmed by querying FRED directly with no date filter at all — despite otherwise being long-running series. The original `CHNGDPNQDSMEI` (China GDP) and `IRSTCB01JPM156N` (BoJ rate) series stopped updating at 2023-07-01 and 2023-12-01 respectively, apparently discontinued/lagging on FRED's side rather than a fetch issue — both were replaced 2026-07-10 with actively-maintained alternatives (`CHNGDPRAPSMEI`, `IRSTCI01JPM156N`) once the staleness reached 1,105 and 952 days and was visibly surfacing wrong values in the Daily Briefing (see "The 4 new international series" above for the replacement detail). The backfill correctly stores whatever history FRED actually has for each series rather than requiring a specific depth — this is a standing property to watch for whenever a new international series is added, not a one-time fix.

`fetch_and_store_macro_indicators()` itself is now incremental rather than always overwriting just the latest row: for each series it still fetches the same ~2-year (~6-year international) rolling window from FRED every run (unchanged — needed as trailing context so `pct_change_30d`/`z_score_2y` compute correctly for the newest date), but only **writes** rows dated on/after `(last stored date − 7 days)` — the 7-day buffer catches FRED revisions to recent observations that can arrive after their original publish date. A series with no stored history at all (fresh install, or newly added to config.yaml, never backfilled) falls back to writing its entire fetched window, so a deployment that's never had the backfill button clicked still self-heals into full history over time, just incrementally, one EOD run's rolling window at a time. This eliminates the redundant full-table rewrite every EOD job used to implicitly risk if it had ever tried to persist more than the latest row.

Note the FRED series count referenced throughout this section — 17 total (13 US + 4 international) — is unchanged by the backfill; it only changes how much *history* is stored per series, not which series are tracked.

### Historical data extension — 2007-01-01 (2026-07-30)

Every backtest study up to this point was bounded by whatever `price_data`/`macro_indicators` already held — in practice ~2016 onward for most tickers (`data.history_days` was 3650, ~10 years, and both `pull_incremental()` and `pull_history()` are forward-only in effect: neither ever asks yfinance for anything before a ticker's own already-stored earliest date, no matter how large `history_days` is raised). That meant every round of research testing (weight search, single-module tests, regime-change exits) was constrained to the two crisis episodes inside 2019–2026 — COVID and the 2021–22 bear market — too small a sample to know whether "fails in these two blocks" generalises to crises broadly or is specific to those two episodes' particular character.

**Availability check (2026-07-30), all 112 watchlist tickers + all 17 FRED series, via live yfinance/FRED queries:** most of the tradeable universe reaches back much further than the stored 2016 floor — SPDR sector ETFs and mega-cap single stocks to 1998 or earlier, `^GSPC` itself to 1927. The real constraint is a handful of newer, more specialised instruments. Of the 70 currently-`tradeable` tickers: **59/70 (84%) have data by 2007-01-01**, and **63/70 (90%) by 2008-09-01** (the Lehman collapse) — the remaining 7 (`AVGO`, `AMLP`, `VOO`, `SKYY`, `INDA`, `GOLD`, `PDBC`) simply phase in between 2009 and 2014, the same kind of listing-date gap the system already handles for recently-added `us_large_cap` tickers. On the FRED side, only `BAMLH0A0HYM2`/`BAMLC0A0CM` (credit OAS, 2023-07-31) and `DFEDTARU` (Fed funds target *range*, 2008-12-16 — the Fed didn't publish a range before its GFC-era policy response) are meaningfully limited; every other series comfortably predates 2007, several back to the 1950s-1970s. **Recommendation: 2007-01-01**, not the round-numbered 2010 originally suggested — the data clearly supports reaching the genuine onset of the GFC (not just the crash itself) at a tradeable-universe coverage level (84-90%) barely different from what 2010 would have offered, so there was no reason to leave that extra crisis episode on the table.

**`data/ingestion.py:backfill_price_history(tickers=None, start_date="2007-01-01")`** is the new backward-backfill mechanism this required — mirrors `backfill_macro_indicators()`'s "one-off full-range fetch from start_date" pattern, adapted for price data's own hazard: yfinance's `Close` is split-adjusted *retroactively*, so a full re-fetch-and-upsert of a ticker's entire history could silently shift already-stored values for a ticker that's split since the DB was first populated. Rather than accept that risk, the function only ever requests `[start_date, existing_earliest_date)` — strictly *before* whatever a ticker's own earliest stored row already is — making it impossible by construction to alter or re-write any already-stored date, regardless of any split since. A ticker with no stored data at all falls back to a full fetch from `start_date` (the same first-time-download path `pull_incremental()` already uses); a ticker whose earliest stored date is already `<= start_date` is skipped entirely. `_upsert_price_rows()`'s existing upsert-on-`(ticker, date)` semantics make repeat runs idempotent.

**Validated in an isolated clone before touching production** (same discipline as every backtest research round above): backfilled 6 tickers (`AAPL`, `VOO`, `SOXX`, `GLD`, `NVDA`, `XLK`) from 2007-01-01, then confirmed two ways — a byte-for-byte checksum of every already-stored pre-2019 row (identical before/after), and a full `compute_signal_history()`/`simulate_trades()` replay over the existing 2019-04-12→2023-12-31 backtest window on the now-extended database, compared trade-for-trade against the untouched production database: **50/50 trades byte-identical**. No gaps beyond ordinary weekend/holiday spacing, and price continuity across the old/new data boundary confirmed visually for all 6 (no split-adjustment discontinuity). This reproducibility guarantee is actually stronger than it needs to be for the *current* code: every existing backtest call site uses a fixed `get_price_df(ticker, days=4000)` (~11 years back from *today*), which doesn't reach back to 2007 at all regardless of how much history now exists in the table — the new data is inert until a future study deliberately asks for a longer window.

Applied to production the same day: full 2007-01-01 price backfill across all 112 tickers, plus `analysis/macro.py:backfill_macro_indicators(config, start_date="2007-01-01")` (no code change needed there — it already accepted a `start_date` parameter). `data.history_days` raised from 3650 to 7150 (~19.6 years) so a brand-new ticker added in the future gets the same depth on its first download, not just tickers that already existed at backfill time. Covered by `tests/test_ingestion.py`: the skip-when-already-covered case, the strictly-before-existing-earliest-date fetch boundary, the no-existing-data fallback, and an end-to-end guarantee that existing rows are never overwritten.

**Suggested train/test split for a future study (proposal only, not adopted here)**: something like train `2007-01-01→2019-12-31` (GFC, the 2011 European sovereign debt selloff, the 2015-16 China/oil selloff, 2018's Q4 volatility spike) / test `2020-01-01→2026-07-30` (COVID, the 2021-22 bear market, and the current rally) would put every crisis episode this project has tested against so far entirely in the *held-out* period, with the GFC and several genuinely different stress episodes available for training instead. Not run as part of this task — a deliberate scope boundary, since changing the split changes what every prior round's "Block 1/Block 3" findings would even mean, and deserves its own dedicated study rather than a folded-in extra.

Key config sections added since initial release:
- `notifications.weekly_summary.enabled` — controls the Sunday 10:00 UTC weekly summary email independently of `notifications.email.enabled`.
- `backtest.max_holding_days`, `backtest.atr_stop_multiplier`, `backtest.walk_forward_months`, `backtest.walk_forward_step` — configure the backtesting engine.
- `backtest.entry_signal`, `entry_signal_overrides`, `allowed_entry_regimes` — entry gating for `analysis/backtest.py:simulate_trades()`. `entry_signal: "STRONG_BUY"` limits entries to STRONG_BUY/STRONG_SELL; `"BUY"` also accepts BUY/SELL. `entry_signal_overrides` applies a different threshold per detected regime (see "Per-regime entry signal overrides" below). `allowed_entry_regimes` blocks new entries entirely in any regime not listed (see "Regime entry gate" below) — checked before `entry_signal_overrides`. Kept under `backtest:` rather than `trading212_invest:`/`trading212_stocks:` because simulated backtest trades can go LONG or SHORT, unlike those two long-only cash-account automation paths. (These keys previously lived under a `virtual_trading:` section that also drove a now-removed live automated paper-trading feature — see "Removed: virtual trading" below.)
- `trading212_invest.enabled`, `live`, `max_position_gbp`, `max_total_deployed`, `max_open_positions`, `entry_signal`, `entry_signal_overrides`, `allowed_entry_regimes`, `exit_signal`, `ucits_mapping` — control T212 Invest API integration. `entry_signal: "STRONG_BUY"` restricts entries to STRONG_BUY only; `"BUY"` also accepts BUY or STRONG_BUY (same pattern for `exit_signal`). `entry_signal_overrides` applies a different threshold per detected regime; `allowed_entry_regimes` blocks entries entirely outside the listed regimes (see "Per-regime entry signal overrides" and "Regime entry gate" below). `max_open_positions` (default 999 when absent — unlimited in practice) caps the number of simultaneously open positions regardless of `max_total_deployed`, checked in the BUY phase immediately after the deployed-capital check — see "Broker integration — Trading212 Invest" below. `ucits_mapping` maps internal research tickers to T212 UCITS instrument IDs — only mapped tickers receive automated orders. Requires `T212_API_KEY` and `T212_API_SECRET` env vars. `live: false` is the hard default and is enforced in code. `position_sizing`, `var_confidence`, `var_lookback_days`, `max_risk_pct`, `min_position_gbp` (also present, independently, under `trading212_stocks`) control VaR-based position sizing — see "VaR position sizing" below; `max_position_gbp` remains the hard ceiling either way.

## Pre-Demo Checklist

Before opening the laptop in front of an investor:
1. Open Status page — confirm no OVERDUE rows in Pipeline table
2. Open Macro Dashboard — confirm no "(stale — N d old)" warnings on Asia tiles
3. Send a test email from Status page — confirm "SP500INVEST" branding in subject and header
4. Confirm both services running: `systemctl status investment-research investment-research-scheduler`
5. Note current regime and prepare verbal explanation for why entries are/aren't blocked
6. Have the daemon incident explanation ready for Performance page (7 pre-fix trades)

Suggested demo flow: Daily Briefing → Signals → Charts (live ticker) → Backtest → Robustness → Performance → Status

## Running tests

```powershell
pytest                        # run the full suite from the project root
pytest tests/test_trend.py    # run a single module
```

The suite lives in `tests/` (configured via `[tool.pytest.ini_options]` / `testpaths = ["tests"]` in `pyproject.toml`) and is fully self-contained: no test touches the real project database, calls yfinance, or calls the live Trading212 API. DB-touching tests depend on the `db_engine` fixture in `tests/conftest.py`, which creates a fresh in-memory SQLite database (schema from `data.database.Base`, kept alive via `StaticPool` since production code calls `get_engine()` fresh on every DB access) and patches every module-level `get_engine` binding the code under test actually resolves at runtime (`data.database`, `analysis.regime`, `analysis.confluence`, `data.ingestion`, `output.performance` — each `from data.database import get_engine` creates its own name binding, so patching `data.database.get_engine` alone does not affect modules that imported it at their own module level). External services (the Trading212 HTTP client, yfinance) are mocked with `unittest.mock` / `pytest-mock`, never called for real. Runs identically on Windows (local dev) and Ubuntu (server) — nothing in the suite is platform-specific.

**The pre-push hook runs this suite automatically** before deploying to `main` (see "Git hooks" below) — a failing test blocks both the GitHub push and the server deploy. To bypass it in an emergency: `git push --no-verify`. This should not be done on `main` under normal circumstances — it skips the only safety net between a broken change and production.

## Architecture

> **Historical framing (added 2026-08-03).** The remainder of this document is a genuine chronological build log, not a snapshot of the current system — later entries correct, supersede, or entirely remove what earlier entries describe, and that's by design (see the "Research reproducibility standard" near the top of this file for why superseded work is preserved rather than deleted). The two subsections immediately below — **"### Data flow"** and its eight-page bullet list — are the single most consequential case of this: they describe the original V1 system (a seven-module technical/macro confluence engine, regime detection, ~112 watchlist assets across 21 groups, and pages named Daily Briefing / Signals / Charts / Relationships / Robustness) almost in its entirety, none of which exists in the codebase any more. **For the current, live architecture, go directly to "### V2 architecture — phased rebuild, Stage 1 (2026-07-31)"** (search for that heading) and read forward from there — that section and everything after it describes the single 12-month-momentum + crisis-override system that actually runs today, across 5 pages (Dashboard, Instruments, Performance, Backtest, Status). Everything between here and "### V2 architecture" remains as written, correctly reflecting what was true when each entry was made, but should not be read as a description of the system as it exists today.

### Data flow

```
yfinance ──► data/ingestion.py ──► SQLite (price_data table)
                                          │
                                          ▼
                              analysis/engine.py:run_analysis()
                              ├── analysis/trend.py
                              ├── analysis/mean_reversion.py
                              ├── analysis/volatility.py
                              ├── analysis/volume.py
                              ├── analysis/macro.py  (FRED + VIX from DB)
                              ├── analysis/correlations.py:compute_all_pairs()
                              └── analysis/fomc.py  (FOMC calendar; gates 19:05 scheduler job)
                                          │
                                          ▼
                              indicators table + correlations table
                                          │
                                          ▼
                              analysis/regime.py:detect_regime()
                              (reads ^GSPC indicators → Regime enum)
                                          │
                                          ▼
                              analysis/confluence.py:generate_signals()
                              (regime-weighted dot product per ticker, 7 modules)
                                          │
                                          ▼
                                    signals table
```

`main.py` is the only entry point. It inserts the project root into `sys.path` so that `from data.X import ...` and `from analysis.X import ...` resolve correctly regardless of how Streamlit invokes the file.

The app has eight pages toggled via a sidebar radio button (default: **Daily Briefing**). The sidebar shows Last Refresh and Last Analysis timestamps. The watchlist has approximately **112 assets across 21 groups**. Every page except Robustness itself shows a small persistent caption at the top — `📊 In-Sample: ... | ✅/⚠️ Out-of-Sample: ... | Live trades: N | Live return: X%` — reminding the reader which numbers on that page are in-sample backtest results vs. genuinely out-of-sample T212 executions; see "Robustness page" below.
- **Daily Briefing** — single compact status bar (regime pill + ^GSPC stats + VIX/yield/USD + active overlay pills in one bordered row), regime description + threshold captions below (confluence score threshold, plus the effective T212 ETF / T212 Stocks entry threshold for the current regime — flagged "(override)" when it differs from the global `entry_signal`, "blocked" plus, for T212 Stocks, its narrower allowed-regime list in parentheses (e.g. "(TRENDING_BULL only)") when the current regime isn't in that path's `allowed_entry_regimes`, and a standalone `⛔ Entries blocked — MIXED regime (...)` caption listing every blocked path), overlay detail strips when active, a rule-based "Today's Briefing" plain-English market narrative card immediately below the status bar and overlay strips (above FOMC key events, top opportunities, Macro Dashboard, and the open trade monitor — see "Daily narrative" below), FOMC key events, top long/short opportunities, Macro Dashboard, then the Open Trade Monitor showing a compact one-line status card per open T212 Invest position — both UCITS ETFs and direct US stocks — (sorted by urgency, status badge + ticker + asset type + day count + GBP P&L only; full detail — signal delta, SL/TP, live R:R — moved to the **Performance** page, see below); top opportunities filtered to tradeable assets (T212 Invest tickers excluded from opportunities while a position is held).
- **Signals** — compact header block (overlays + catch-up strips + regime badge + signal distribution pills + timestamp, all in one bordered card); market intelligence plain-English summary; ranked table of all assets by confluence score with module symbols; Generate Signals button
- **Charts** — a **Market Regime Trend** chart (ADX/VIX/S&P%-vs-SMA200 across every analysis run, with entries-blocked shading and a 3M/6M/All range selector — see "Regime metrics history" below) sits above the grouped asset cards landing; selecting an asset from the landing shows the candlestick chart, volume, and indicator panel; a "← All Assets" button returns to the landing
- **Relationships** — compact single-line header (pair count + last-computed timestamp, read dynamically from `RELATIONSHIP_MAP`); 30-day correlation matrix heatmap across all price-data tickers (FRED series excluded); pair-level table (30d corr, 90d corr, historical norm, z-score, CONFIRMING/DIVERGING/NEUTRAL status); plain-English divergence narratives (correlations recompute automatically — no manual button)
- **Performance** — T212 Invest execution performance, driven entirely by real broker fills (no manual/simulated trade data of any kind — see "Removed: virtual trading" and "Removed: manual CFD trade logging" below). Four sections: **Open Positions** (full detail `_render_trade_card()` cards, reusing `_get_t212_invest_brief_cards()` — same scheduler-maintained snapshot as the Daily Briefing, no direct API calls — each card's detail row includes "VaR at entry" when the position was sized under `position_sizing: var`, see "VaR position sizing" below); **Closed Positions** (table read from `t212_pnl` — see "T212 P&L reconstruction" below — always rendered regardless of row count, `"No closed positions yet — data will appear as T212 positions close."` when empty); **Performance Metrics** (gated behind 5 closed positions — below that, just a win/loss/total-P&L caption; at 5+, stat tiles + equity curve + regime/exit-reason breakdown tables, reusing `analysis/backtest_metrics.py`'s existing functions via a column-renaming adapter rather than reimplementing the same aggregation logic — see "Backtesting engine" below); **Learning System** (live `t212_pnl` count vs. `learning.min_trades_before_adjustment`, plus learned weights vs. static regime weights if any have been computed — see "Feedback learning" below).
- **Robustness** — due-diligence dashboard answering "why should these results be trusted?" rather than the Performance page's "what happened?" — see "Robustness page" below for the full four-panel breakdown.
- **Backtest** — 5-tab page: Run & Overview (date range presets, ticker scope — tradeable-only / T212 CFD Only / all assets; equity curve, metrics, trade table; collapsible T212 CFD comparison panel that filters any existing run to `trading212_instrument` tickers and shows side-by-side metrics + excluded-ticker trade counts without re-running), Period Analysis (COVID Crash / 2022 Bear / 2023–24 Recovery + per-regime breakdown), Module Attribution (win rate when each module agreed vs disagreed with trade direction), Signal Decay (holding period histogram, exit reason distribution, avg hold by regime, Performance by Exit Reason table with a plain-English interpretation caption), Walk-Forward (rolling 6-month windows)
- **Status** — system status grid (deployment, uptime, DB size, last refresh/analysis, next scheduled run); Manual Controls section with four buttons: "Run Analysis Now" (analysis + signals only, no data pull), "Refresh Data + Analysis" (price pull + FRED macro fetch + FRED release-calendar refresh + analysis + signals, matching the full EOD scheduler job — see "Macro module — release-calendar staleness" below for why this button is a second call site that has to be kept in sync with the scheduler job's own steps), "Backfill FRED History" (see "FRED historical backfill" below), and "Backfill Regime Metrics History" (see "Regime metrics history" below — reconstructs the Charts page's trend chart from existing indicators/signals data, day-level resolution only, safe to re-run); Send Test Email button and Send Weekly Summary Now button side by side; **T212 Invest Account** section (auto-loads on first visit to the page — shows the DEMO/LIVE badge, position/deployment limits, a "Position sizing" caption showing **VaR** (with confidence/lookback/max-risk/floor/ceiling params) or **Fixed** (flat £ amount) depending on `trading212_invest.position_sizing`, and the effective entry threshold for the current regime, or `⛔ Entry blocked (MIXED regime not in allowed_entry_regimes)` in place of the threshold when the current regime is blocked, split into three tabs: **Account** tab shows live account balance metrics, deployed-capital progress bar, order log from `t212_invest_orders` (ETF and stock orders together, including a "VaR at Entry" column when the order was VaR-sized), retry/delete buttons for FAILED orders; **Stocks** tab shows the `trading212_stocks` entry threshold/regime-gate status, position/deployment limits, and recent stock orders (also with a "VaR at Entry" column) filtered from `t212_invest_orders` by ticker membership in `watchlist.us_large_cap`; **Mapping** tab shows the UCITS instrument table; open positions removed — shown on Daily Briefing instead); Scheduler Health grid

The Trade Log page was removed permanently — see "Removed: manual CFD trade logging" below. The Performance page that was also removed at the same time has since been rebuilt from scratch, driven by T212 execution data instead of manually-logged CFD trades — see the bullet above.

### SP500Invest brand identity

The app is branded as **SP500Invest** ("Systematic large-cap momentum research.") throughout the UI. This shipped in two steps: **Step 1** (name, sidebar, footer, page titles — no colour/layout changes) and **Step 2** (the dark navy theme below). The electric-blue accent, `#00C2FF`, is used consistently in every element rather than each picking its own shade.

- **Browser tab / page config** — `st.set_page_config(page_title="SP500Invest", page_icon="Z", ...)` at the top of `main()`. The tab title is fixed application-wide by this single call; no per-page `st.set_page_config()` calls exist or are needed.
- **Sidebar header** — replaces the old plain `st.title("Investment Research")` with an `st.sidebar.markdown(..., unsafe_allow_html=True)` block: the "SP500INVEST" wordmark (bold, letter-spaced, `#00C2FF`) over the tagline "Systematic large-cap momentum research." in muted grey (`#8899AA`), directly above the page-navigation radio.
- **Page wordmark** — `main.py:_render_page_wordmark(page_name)` prints a small inline "SP500INVEST · {page_name}" prefix (wordmark in `#00C2FF`, page name in `#8899AA`) at the very top of each page's main content area, immediately before that page's own `st.markdown("## ...")`/`st.title(...)` heading — a subtle identity mark, not a heading replacement. **[corrected 2026-08-03 — the original list of eight call sites here (`_render_briefing_page`, `_render_signals_page`, `_render_charts_landing`, `_render_relationships_page`, `_render_robustness_page`, and others) named V1-era page-render functions that no longer exist; see "### Data flow" above for why.]** Called once per page render path against the current 5-page set: `_render_dashboard_page`, `_render_instruments_page`, `_render_performance_page`, `_render_backtest_page`, `_render_status_page`.
- **Footer** — `main.py:_render_footer()` renders a centred, muted-grey strip ("SP500INVEST · Systematic large-cap momentum research. · hello@example.com · example.com", the email and domain both linked in `#00C2FF`) after an `st.markdown("---")` divider. Called at the true end of each of the current five page render paths above — including before every early `return` a page can take — so the footer still appears on every actual rendered state, not only the "happy path."
- **Persistent out-of-sample badge** — `_render_out_of_sample_badge()` was a Robustness-page feature; the Robustness page itself no longer exists (see "### Data flow" above) and this badge was removed along with it, not carried forward. Left here as historical record only.
- **`.streamlit/config.toml.example`** — gained an empty `[general]` section (a no-op placeholder, since Streamlit has no general-level "title" setting — the tab title comes entirely from `set_page_config` above) alongside the pre-existing `gatherUsageStats = false` under `[browser]`.

Verified via `streamlit.testing.v1.AppTest`: instantiate a fresh `AppTest.from_file("main.py")` and set `at.session_state["page"]` directly per page key rather than clicking through the sidebar nav radio across reruns on one shared instance — clicking through accumulates stale widget state for controls that only exist on other pages and raises a `KeyError` unrelated to app code. This was originally verified against all eight V1-era pages; the current five pages are covered by `tests/test_main_ui.py`'s own `AppTest`-based suite instead, following the identical pattern.

**Step 2 — dark navy theme.** `.streamlit/config.toml.example` gained a `[theme]` section (`primaryColor = "#00C2FF"`, `backgroundColor = "#0A0F1E"`, `secondaryBackgroundColor = "#111829"`, `textColor = "#CCDDEE"`, `font = "sans serif"`) — this drives the global page background, sidebar background, input fields, buttons, and `st.metric`/`st.info`/`st.warning`/`st.error`/`st.dataframe` styling automatically; there is no per-widget override for any of those. The real `.streamlit/config.toml` stays gitignored per the file's own header comment — copy `config.toml.example` locally (or let `setup.sh` do it on the server) to pick up the theme.

- **`main.py:_apply_dark_theme(fig)`** — a shared helper (defined next to `_candlestick_chart`) that every one of the app's ~13 Plotly figures now routes through as the last step before `st.plotly_chart(...)`, via `fig.update_layout(template="plotly_dark", paper_bgcolor="#0A0F1E", plot_bgcolor="#111829", font=dict(color="#CCDDEE"), xaxis=dict(gridcolor="#1E2D45", linecolor="#1E2D45"), yaxis=dict(...))`. Plotly's `update_layout()` merges nested axis dicts rather than replacing them (verified directly — a prior `xaxis=dict(side=..., tickangle=...)` call survives a later `xaxis=dict(gridcolor=...)` call intact), so calling this after a chart's own `update_layout()` is always safe regardless of what that chart already set. `_candlestick_chart()`/`_volume_bar_chart()` return `_apply_dark_theme(fig)` directly; every other chart call site calls it as a statement immediately before `st.plotly_chart()`. The one deliberate exception is the Monthly Returns Heatmap on the Robustness page, whose custom red/white/green colourscale had its white midpoint changed to `#1f2937` (matching the Relationships page's own correlation-heatmap neutral colour) so a "zero" cell blends into the theme instead of flashing white.
- **Inline HTML/CSS colour audit** — main.py has dozens of hand-built `st.markdown(..., unsafe_allow_html=True)` cards, badges, and tables predating this theme, most tuned for light-on-white contrast. Fixed by category, not by blanket-recolouring every hex code (badges/pills using a saturated colour as both text and low-alpha tinted background, e.g. `color:{c}` on `background:{_hex_rgba(c, 0.1)}`, are self-consistent and theme-safe on any background, so those were left alone):
  - Muted secondary text tuned for white backgrounds (`#64748b`, `#94a3b8`, `#475569`, `#6b7280`, `#1e293b` — Tailwind slate, used ~90 times for timestamps, captions, table sub-text) was too low-contrast against the new dark background and was bulk-replaced with the theme's own `#8899AA` secondary-text colour.
  - Light divider borders meant to separate rows/cards on white (`border...solid #e2e8f0` / `#f1f5f9`) were replaced with `#1E2D45` (the same border colour `_apply_dark_theme()` uses for chart gridlines) so table headers and card outlines read as intentional structure rather than a stray bright line.
  - The Status page's deployed-capital progress bar's empty "track" (`background:#e2e8f0`) was changed to `#1E2D45` for the same reason.
  - The Daily Briefing's "Fed Decision — CAUTION" card paired dark-amber text (`#b45309`, `#92400e`) with a light-cream background at 12.5% opacity (`background:#fef3c720`) — a classic light-mode alert-box pairing. At that opacity the effective background is a dark warm-grey once composited over the page, not the pale cream the text colours were tuned against, so the text was unreadable; both were changed to `#fbbf24` (the same bright amber already used for the "CATCH-UP" badge elsewhere on the page).
  - Text colours that were already light (`#cbd5e1`, `#e2e8f0` used as `color:`, not `background:`) were left untouched — light text is exactly what a dark background needs, and re-touching these would have been pure churn.
- Verified by launching the app against the local `.streamlit/config.toml` and driving it with Playwright (`chromium.launch()` + screenshots of the real `[data-testid="stMain"]` scroll container, not `body`/`full_page=True`, which under-reports height in this app) across all eight pages plus a per-ticker Charts detail view — every page confirmed fully readable with no invisible or unreadable text, dark-themed Plotly charts (candlestick, volume, equity curves, correlation/monthly heatmaps), and the wordmark/footer/badge all popping correctly against the navy background. Playwright and PyMuPDF are dev-time-only verification tools (installed ad hoc, same as the PDF-export visual QA above) — neither is a project dependency.

### Configuration

Everything flows from `config.yaml`. All indicator periods, confluence thresholds, scheduler timing, rate limits, and the watchlist are read at runtime — nothing is hardcoded. Every module that needs config calls its own `_load_config()` helper (resolves via `Path(__file__).parent.parent / "config.yaml"`).

### Analysis engine

`analysis/engine.py:run_analysis()` is the Phase 2 orchestrator. For each ticker it:
1. Loads price data via `get_price_df()`
2. Calls each `compute_*()` function, which returns a dict of `{"score": int, ...raw values}`
3. Merges all raw values and upserts them into the `indicators` table for the latest date

Each `compute_*` function in the analysis modules returns the **latest** scalar values only (not a full time series). The score from each module feeds into `confluence.py:generate_signal()`.

### Mean reversion module — RSI/Z-score, Bollinger %B dropped from scoring

`analysis/mean_reversion.py:compute_mean_reversion()` scores mean reversion as an equal-weight (50%/50%) combination of RSI and the 52-week Z-score — both must agree (both oversold or both overbought) for a non-zero score. Bollinger %B (`bb_pct_b`) was removed from the score on 2026-07-05: a production correlation analysis (200 rows, 49 tickers) measured RSI vs Bollinger %B at **0.927** — both are measurements of the same short-term overbought/oversold extremity relative to a recent trading range, so scoring both double-counted one signal rather than adding an independent one. Before this change the module used a 3-vote scheme (RSI must agree, plus at least one of BB%B/Z-score) — functionally close to the same outcome most of the time given the 0.927 correlation, but redundant by construction.

`bb_pct_b` and `bb_width` are still computed and persisted to the `indicators` table exactly as before — the Charts page indicator panel reads them directly — only their contribution to the score was removed. `analysis/backtest.py:_mean_rev_scores()` mirrors the live 2-component scoring (no BB%B) for the same reason `_ma_cross_vote()` below is mirrored: backtested and live signal history must stay consistent.

A full-history backtest comparison (2019–present, 64 tradeable assets, identical code/data/dates, only this scoring change applied) showed the effect is negligible: 253 trades in both the before and after runs, win rate 47.43% → 47.04%, profit factor 2.51 → 2.48, Sharpe 0.799 → 0.792, max drawdown unchanged at -30.29%. Treated as neutral and shipped rather than reverted.

### Trend module — direction-aware MA cross vote

`analysis/trend.py:compute_trend()` scores trend as 2-of-3 votes: price vs SMA200, the SMA50/SMA200 cross, and MACD vs its signal line (ADX-gated). The SMA50/SMA200 vote is **not** a pure positional check — `_ma_cross_vote()` also weighs the SMA50 slope, because a positional-only check treats a stale golden cross (SMA50 still above SMA200 but rolling over from an earlier uptrend) as fully bullish, when it is actually warning of an impending death cross:

- SMA50 sloping the same way as its cross (rising in a golden cross, falling in a death cross) → vote matches the cross direction (+1 / -1).
- SMA50 sloping against its own cross, but the lines are still well apart → vote is neutral (0) — a stale cross with fading momentum, not yet a reversal.
- SMA50 sloping against its own cross **and** the lines have converged to within `signals.ma_cross_near_pct` (default 2%) of each other → vote flips to the opposite direction — a new cross is imminent.

The slope lookback (`signals.ma_cross_slope_window`, default 10 trading days) and convergence threshold (`signals.ma_cross_near_pct`, default 0.02) are both configurable. When there isn't enough history yet to read the slope, the vote falls back to the pure positional check. The raw positional `golden_cross` flag (1 = golden, -1 = death, 0 = neither) is kept unchanged for the "just crossed" / "sustained" event history and drives the "Golden ✓" / "Death ✗" label on the Charts page indicator panel; the direction-aware result is stored separately as `ma_cross_score` on the `indicators` table and used for scoring. When `ma_cross_score` disagrees with the positional `golden_cross` flag, the indicator panel shows a "⚠ SMA50 declining/rising — cross weakening/imminent" caption under the MA Cross metric so the discrepancy is visible rather than silently affecting only the score.

`analysis/backtest.py` mirrors this exact logic in a vectorized `_ma_cross_vote()` so historical backtests replay the same scoring behaviour as live analysis.

### Volatility module — change point detection

`analysis/volatility.py:compute_volatility()` scores volatility from ATR percentile and VIX regime (unchanged from before this feature — see the module's docstring). Both use a **fixed** lookback window (ATR 14-day, historical vol 20-day, ATR percentile ranked over 252 days), which can't distinguish a ticker that has always been volatile from one that recently entered a new volatility regime. `detect_change_points(ticker, config, method="cusum")` addresses this by locating the most recent structural break in a ticker's return-variance regime, providing structural context for volatility scoring today. (A GARCH-based stop-loss calibration was built on top of this later, then removed after backtesting underperformed the ATR stop — see "GARCH stop loss — why it was removed" below; change point detection itself is unaffected and remains active.)

**Detection methods** — selected via the `method` parameter:

- `"cusum"` (default/primary): a cumulative-sum-of-squares statistic (Inclan & Tiao 1994, "ICSS") computed via `_icss_change_point()`. `Dk = Ck/CT - k/T` (Ck = cumulative sum of squared daily log returns through day k, CT = the total). Under constant variance, Dk fluctuates near zero; a genuine variance break produces a sharp peak in `|Dk|`. The standardised statistic `sqrt(T/2) * max|Dk|` is compared against `signals.cpd_threshold` (default 3.0 — deliberately stricter than the classical 95%/99% asymptotic critical values of ~1.36/~1.63, so only pronounced regime shifts qualify as a "structural break"). **This deliberately is not a textbook CUSUM control chart applied to the rolling 20-day variance series** (the design initially tried, matching a literal reading of "apply CUSUM on the variance series"): a rolling window shares 19 of its 20 points with its neighbour, so the resulting series is heavily autocorrelated, and a control-chart CUSUM (designed for near-independent samples) drifts and false-triggers on that autocorrelation alone — verified empirically during implementation, it flagged a change point in a *stable*, no-break synthetic series on every trial, and located true breaks nowhere near the actual break date. Cumulative sum of squared returns is the standard, well-behaved alternative for this exact problem and is still fundamentally a cumulative-sum method, so the "CUSUM" name in the public API is kept despite the different underlying statistic.
- `"variance_ratio"` (secondary/validation): a scanning two-sided F-test (`_variance_ratio_change_point()`, requires `scipy`). Splits the return series at each candidate point (5-day stride), computes the variance on each side, and selects the split with the **strongest** evidence (largest F-statistic among splits with p < 0.05) — not simply the most recent qualifying split. A literal "most recent significant split" (also tried first) picks a point near the very end of the scan range almost every time a real break exists: once volatility has genuinely shifted, nearly every later split still tests significant against an increasingly break-dominated "before" segment, so "most recent" stops meaning "where the break happened" and just means "near the end of the scannable range" — also verified empirically. Selecting the strongest split instead is the standard binary-segmentation approach and locates the true break precisely. Available for manual cross-validation of a `"cusum"` result; `compute_volatility()` does not call it automatically.

**Inputs and outputs.** Reads the last `signals.cpd_lookback_days` (default 504, ~2 trading years) of daily closes from `price_data` (own `get_price_df()` call, not the `df` already loaded by the caller — keeps the function's signature simple and DB-access self-contained, at the cost of one extra query per ticker per run; results are cached in-process per `(ticker, today's date, method)` since `analyse_ticker()` and `score_ticker()` both call `compute_volatility()` — and therefore this — once each per ticker per analysis run). Returns:

| Field | Meaning |
|---|---|
| `last_change_point` | Date of the most recent structural break, or `None` |
| `days_since_change` | Trading days since that break, or `None` |
| `pre_change_vol` / `post_change_vol` | Annualised return vol before/after the break, or `None` |
| `vol_regime` | `"LOW"` / `"NORMAL"` / `"HIGH"` / `"ELEVATED"` — the most recent 20-day annualised vol classified against its own percentile distribution over the lookback window (<25th / 25th–75th / 75th–90th / >90th) |
| `structural_break` | `True` only if `last_change_point` falls within `signals.cpd_recent_window` (default 90) days of the most recent stored date |

**Integration into `compute_volatility()`.** When `structural_break` is `True` and the post-change-point slice of price data has at least `max(atr_period + 5, 21)` rows (21 covers `hist_vol_20`'s 20-day rolling window on returns, which needs one more row than the ATR floor at the default `atr_period=14`), ATR is recomputed from that slice instead of the full window — `hist_vol_20` is **not** affected by this (a mathematical invariant: a trailing `rolling(20)` read only ever looks at its last 20 rows, which are identical whether the input is the full window or a post-change-point slice of it, since slicing only trims rows from the front — see the code comment at that computation for detail). The ATR-percentile ranking used for the base `atr_score` still always uses the **full** window's own ATR% history regardless of any slice swap — percentile ranking needs the original longer history to mean anything. On top of the existing ATR/VIX-derived score, a structural-break adjustment applies: `post_change_vol > pre_change_vol × 1.5` (vol expanded) subtracts 1; `post_change_vol < pre_change_vol × 0.67` (vol compressed) adds 1; no break, or a marginal one, leaves the score unchanged. The combined score is clamped back to `[-1, 1]`.

`vol_regime`, `structural_break` (stored as 0/1), and `days_since_change_point` are persisted on the `indicators` table and shown on the Charts page indicator panel's Volatility section: a colour-coded Vol Regime tile (green/grey/amber/red for LOW/NORMAL/HIGH/ELEVATED) and, when `structural_break` is set, an amber "⚠ Regime shift detected N days ago" caption.

**Scope note — the backtest engine does not yet reflect this.** Unlike the trend module's `_ma_cross_vote()` (mirrored in `analysis/backtest.py` — see above), change point detection has **not** been vectorized into `analysis/backtest.py`'s `_vol_scores()`. Doing so correctly (recomputing a change point "as of" every historical date using only data strictly before that date, to stay look-ahead-free) is a materially larger task than a single rolling-window formula — a naive per-date recompute is O(n²) per ticker — and was deliberately left out of this feature's scope. A `run_backtest()` after this feature shipped therefore produces results **identical** to before it: `_vol_scores()` never calls `compute_volatility()` or `detect_change_points()` at all, so nothing in the simulated trade selection changes. This is expected, not a bug — treat any backtest comparison against a pre-CPD baseline as a "confirms no regression" check only, not a validation of the score adjustment's real trading impact. Mirroring CPD into the backtest engine (with a look-ahead-free, performant incremental design) is a natural follow-up if that validation is wanted.

`analysis/volatility.py` requires `scipy` (added to `requirements.txt` for this feature) for the `"variance_ratio"` method's F-test; the default `"cusum"` path has no scipy dependency.

**`hist_vol_20` excluded from scoring (2026-07-05).** The module's score is an equal-weight combination of three genuinely distinct components: ATR% percentile, VIX regime, and the change-point adjustment above. `hist_vol_20` (20-day annualised realised vol, computed from close-to-close log returns) was never actually a fourth scoring input in the live path — the score formula above only ever read `atr_score` and `vix_score` — but a production correlation analysis (200 rows, 49 tickers) confirmed why: ATR% vs `hist_vol_20` measured **0.929**, both being trailing realised-volatility measures over similar windows. `hist_vol_20` remains computed and persisted to the `indicators` table (Charts page indicator panel's "Hist Vol 20" tile reads it) — this change only documents and formalises the existing exclusion, and confirms `analysis/backtest.py:_vol_scores()` (ATR% + VIX only, no `hist_vol_20`, no CPD per the scope note above) needed no code change either, since it never included `hist_vol_20` to begin with.

### Regime detection

`analysis/regime.py:detect_regime()` is a rule-based classifier (an HMM confidence gate that validated this call was implemented, backtested, and removed — see "HMM confidence gate — why it's disabled" below).

`_detect_regime_rules()` reads the most recent stored ^GSPC indicators (SMA 200, ADX, VIX) and applies priority-ordered rules:
1. VIX > 30 → `HIGH_VOLATILITY`
2. price > SMA200, ADX > 25, VIX < 20 → `TRENDING_BULL`
3. price < SMA200, ADX > 25, VIX > 20 → `TRENDING_BEAR`
4. ADX < 20, VIX < 15 → `RANGING_LOW_VOL`
5. else → `MIXED`

`detect_regime()` calls this rule function and returns the `Regime` under the `"regime"` key, alongside `weights`, `description`, `colour`, and `details` (the raw indicator values used for classification).

Each `Regime` carries a `REGIME_WEIGHTS` dict that sums to 1.0 across the six analysis modules. Weights by regime:

| Module | TRENDING_BULL | TRENDING_BEAR | RANGING_LOW_VOL | HIGH_VOLATILITY | MIXED |
|--------|:---:|:---:|:---:|:---:|:---:|
| trend | 0.20 | 0.25 | 0.10 | 0.10 | 0.18 |
| mean_reversion | 0.05 | 0.15 | 0.35 | 0.15 | 0.18 |
| volatility | 0.20 | 0.20 | 0.15 | 0.35 | 0.18 |
| volume | 0.15 | 0.15 | 0.15 | 0.15 | 0.18 |
| macro | 0.30 | 0.15 | 0.15 | 0.15 | 0.18 |
| correlation | 0.10 | 0.10 | 0.10 | 0.10 | 0.10 |

**TRENDING_BULL weights rebalanced 2026-07-04** based on module-attribution analysis of 347 TRENDING_BULL-only backtest trades: `trend` (100% agreement rate with the LONG direction) and `volume` (98.8% agreement) confirm the regime but showed zero/minimal discriminating power between winning and losing entries, so both were reduced (trend 0.30→0.20, volume 0.20→0.15). `mean_reversion` is almost always contrarian in a trending market (0.6% agreement) and was cut to the floor (0.10→0.05) so a low mean-reversion score — the normal state in this regime — doesn't meaningfully penalise otherwise-good entries. `macro` showed the highest discriminating power of any module — only 0.9% agreement, but a 66.7% win rate when it did agree vs. 41.6% when it disagreed — and was doubled (0.15→0.30) to reflect that. `volatility` (70.3% agreement, 42.6% vs 39.8% win rate) was increased slightly (0.15→0.20) as a moderate-discrimination counterbalance. Other regimes' weights are unchanged. **These weights are unchanged as of 2026-07-08** despite discovering that macro's "0.9% agreement" figure above was itself a symptom of a bug in how little historical FRED data existed at the time — see "Macro module regime-weight rebalance (2026-07-08)" earlier in this file for the full investigation, four weight attempts tried, and why none of them shipped.

### HMM confidence gate — why it's disabled (removed)

A 3-state Gaussian HMM confidence layer was implemented (`analysis/hmm_regime.py`, since removed) that validated — never replaced — the rule-based `detect_regime()` call, in both directions: it could **downgrade** a rule-based `TRENDING_BULL` to `MIXED` when HMM bull-state confidence was low, and **upgrade** a rule-based `MIXED` to `TRENDING_BULL` when confidence was very high. Motivation was that the rule-based ADX/VIX/SMA200 thresholds create sharp regime boundaries (ADX 24.9 is `MIXED`, ADX 25.1 is `TRENDING_BULL`) that a single noisy data point can flip, while HMM state probabilities are continuous.

Two separate full-history backtest comparisons (2019–present, tradeable assets, identical code/data) both showed the gate deteriorating every metric versus the rule-based-only baseline (236 trades / 45.8% win / PF 2.62 / Sharpe 0.80 / -29.5% DD):

| | Baseline (gate disabled) | Downgrade gate only | Downgrade + upgrade |
|---|---|---|---|
| Trades | 236 | 165 | 443 |
| Win rate | 45.8% | 42.4% | 36.3% |
| Profit factor | 2.62 | 1.69 | 1.36 |
| Sharpe | 0.80 | 0.548 | 0.312 |
| Max drawdown | -29.5% | -33.1% | -80.8% |

**Downgrade-only** blocked 45.3% of all rule-based `TRENDING_BULL` calls outright (since `allowed_entry_regimes` is `["TRENDING_BULL"]` only, a downgrade to `MIXED` doesn't just lower conviction, it blocks the entry). **Adding the upgrade path** made things markedly worse, not better: the 287 HMM-upgraded `MIXED → TRENDING_BULL` entries alone had a 34.2% win rate — a near-exact match for the `MIXED`-regime baseline (37.5% win rate, PF 0.123, -91.26% DD) that `allowed_entry_regimes` was built specifically to exclude, meaning the upgrade path was effectively re-admitting the same low-edge entries the gate was designed to filter out.

**Likely root cause**: the fitted model's state means showed two nearly-identical "bull-like" states (differing by less than 0.01 in standardised units) rather than a clean 3-way separation — a known HMM label-switching/instability failure mode. Since each calendar month refit independently (mirroring the live weekly refit), this instability could flip which state was labelled `BULL` between two adjacent, substantively similar periods, producing a noisy veto/admit signal rather than a stable one.

The mechanism (live gate, backtest mirror via a monthly-refit cache, and full pytest coverage) was removed entirely (2026-07-08) rather than left disabled, alongside the GARCH stop loss — see "GARCH stop loss — why it was removed" below for that companion removal. `analysis/regime.py:detect_regime()` is rule-based only now.

### Regime overlays

`analysis/regime.py:detect_overlays()` runs after each `compute_all_pairs()` call. Results are written to `system_metadata` under the key `active_overlays` (comma-separated key names, or `"none"`):

| Overlay key | Trigger | Confidence impact |
|-------------|---------|-------------------|
| `MACRO_STRESS` | TLT/^GSPC correlation DIVERGING (z ≥ 1.5) | −20% on equity/bond BUY signals |
| `VOL_REGIME_SHIFT` | ^VIX/^GSPC correlation DIVERGING (z ≥ 1.5) | Informational warning only |
| `CREDIT_STRESS` | HYG/TLT return-spread z-score < −1.0 | −15% on equity BUY signals |
| `RECESSION_RISK` | CFNAI < −0.70 for 2 consecutive months | −25% on all equity BUY signals |
| `EM_STRESS` | DTWEXBGS 30-day change > +3% (dollar surging) | −15% on EM asset BUY signals |

Active overlays are displayed as compact inline strips on the Signals and Daily Briefing pages. Confidence badges appear on opportunity cards in the Daily Briefing for affected tickers when overlays are active. `OVERLAY_DESCRIPTIONS` in `main.py` maps each key to its UI label, colour, and descriptive text.

### Per-regime entry signal overrides

Backtesting revealed uneven edge by regime — MIXED showed 416 trades at a 37.5% win rate, profit factor 0.123, and −91.26% max drawdown, while a short-sample backtest of TRENDING_BULL (PF 0.631) and TRENDING_BEAR (PF 0.202) looked like it showed genuine edge *(pre-weight-rebalance baseline — see "Current baseline" in the Backtesting engine section below for the current production numbers)*. Rather than trade every regime at the same conviction threshold, both T212 automation paths support a per-regime override of `entry_signal`:

```yaml
trading212_invest:            # and, identically, trading212_stocks:
  entry_signal: BUY                 # global default (fallback if no regime override)
  entry_signal_overrides:
    MIXED: STRONG_BUY
    RANGING_LOW_VOL: STRONG_BUY
    HIGH_VOLATILITY: STRONG_BUY
    TRENDING_BULL: STRONG_BUY
    TRENDING_BEAR: STRONG_BUY
```

`entry_signal_overrides` is keyed by `Regime` enum name (`analysis/regime.py`). A regime with no entry in the overrides dict falls back to the global `entry_signal`. Two shared helpers in `analysis/regime.py` implement this:

- `get_latest_regime_name()` — reads the regime stamped on the most recent `^GSPC` row in the `signals` table (not a fresh `detect_regime()` recomputation), so the regime used for the entry decision always matches the regime that actually weighted and thresholded the signal it's acting on.
- `resolve_entry_signal(cfg, regime_name)` — looks up `cfg["entry_signal_overrides"][regime_name]`, falling back to `cfg["entry_signal"]` (default `"STRONG_BUY"`) when there's no override or no known regime. `cfg` is the `trading212_invest`, `trading212_stocks`, or `backtest` config section — all three use the same shape.

**Why TRENDING_BULL/TRENDING_BEAR are STRONG_BUY, not BUY.** They were originally set to the looser `BUY` threshold on the theory above. A full 10-year backtest after `analysis/backtest.py:simulate_trades()` started applying this override (see "Backtesting engine" below) showed that was wrong: `simulate_trades()`'s signal-reversal exit has always accepted plain SELL/BUY, not just STRONG_SELL/STRONG_BUY — pairing that with a loosened BUY *entry* created high-churn whipsaw trading. TRENDING_BULL and TRENDING_BEAR are only ~25% of all regime-days across 2016–2026 but generated 90% of all simulated trades (2,899 of 3,231), and overall win rate/profit factor/drawdown all got materially worse (42.9% / 1.18 / −99.3% vs. 41.2% / 1.35 / −85.4% under STRONG_BUY-only). Verified as a real strategy-design effect rather than a code bug via an independent from-scratch reimplementation of the entry/exit walk-forward, which produced identical trade lists to the production function on real multi-year data (VOO 68/68, ^GSPC 63/63 trades, exact match).

With all five regimes reverted to uniform `STRONG_BUY`, a full-history production backtest reproduces the original pre-feature baseline exactly: 757 trades, 41.2% win rate, PF 1.35, −85.4% max drawdown, with MIXED at 416 trades/37.5% win rate, TRENDING_BULL at 313 trades/45.4% win rate, and TRENDING_BEAR at 27 trades/51.9% win rate *(pre-weight-rebalance baseline — predates both the 2026-07-04 TRENDING_BULL weight rebalance and the `allowed_entry_regimes` gate below, so its 757-trade, all-regime scope is not comparable to the current 253-trade, TRENDING_BULL-only "Current baseline")* — confirming `entry_signal_overrides` at these values is a functional no-op relative to the hardcoded STRONG_BUY-only behaviour that existed before this feature. The mechanism (config keys, `resolve_entry_signal()`, the regime-gated entry in `simulate_trades()` / live automation, and the full pytest suite) remains in place and tested if a specific regime's threshold is revisited in future — any such change should be validated against full production history the same way, not a short/partial sample, before deploying.

`maybe_place_t212_orders()` and `maybe_place_stock_orders()` (`brokers/trading212.py`) both call `get_latest_regime_name()` once per run and pass the result through `resolve_entry_signal()` to build the entry-signal set used in their BUY-phase filtering, replacing the previous "always read `entry_signal` directly from config" behaviour. The exit side (`exit_signal`) is unaffected — only entries get the per-regime override. The resolved regime name is also what gets written to the `regime` column on `t212_invest_orders` for the entry, rather than the per-ticker signal row's own (potentially stale, if that ticker wasn't part of the most recent run) regime value — this keeps the stored regime auditable against the threshold that was actually applied.

The Daily Briefing's regime status bar and the Status page's T212 Invest Account section both show the effective entry threshold for the currently detected regime (labelled "(override)" when it differs from the global default), so the active threshold is never just inferred from the global config value.

The backtest engine already applies per-date regime thresholds for the *confluence score* (`get_regime_thresholds()` in `analysis/confluence.py`, vectorised in `analysis/backtest.py`) — that logic determines whether a date classifies as BUY/STRONG_BUY (or SELL/STRONG_SELL) in the first place and is unrelated to this override. `simulate_trades()` in `analysis/backtest.py` additionally applies `entry_signal_overrides` on top of that classification, so simulated results reflect the same per-regime conviction gating as live automation: for each date with no open position it resolves the effective threshold via `resolve_entry_signal(bt_cfg, regime)` (`bt_cfg = config["backtest"]`) using the regime already stored in that date's row (from `compute_signal_history()`), then only enters when the date's signal meets that threshold (`STRONG_BUY`/`STRONG_SELL` only, or also plain `BUY`/`SELL` when the resolved threshold is `"BUY"`). It reads `backtest.entry_signal_overrides` specifically (not `trading212_invest`'s) because backtest trades can be LONG or SHORT, unlike T212 Invest's long-only cash-account model — see "Removed: virtual trading" below for why this config lives under `backtest:` rather than a dedicated live-automation section. Exit logic (stop loss, signal reversal, max holding) is unaffected — only entry gating changes.

### Regime entry gate (`allowed_entry_regimes`)

`entry_signal_overrides` alone can only tighten a regime down to `STRONG_BUY` — it cannot block entries outright, and 10 years of backtest data showed even STRONG_BUY-only entries in MIXED have no demonstrable edge (416 trades, 37.5% win rate, −91.26% max drawdown, 56.8% of all regime-days — the same pre-weight-rebalance MIXED-regime figures cited above under "Per-regime entry signal overrides"). `allowed_entry_regimes` is a stricter gate checked *before* `entry_signal_overrides` — a regime not in the list blocks new entries entirely, regardless of signal strength:

```yaml
trading212_invest:            # and, identically, trading212_stocks and backtest:
  allowed_entry_regimes:
    - TRENDING_BULL
    # TRENDING_BEAR, MIXED, RANGING_LOW_VOL, and HIGH_VOLATILITY are all
    # deliberately absent — every one of them is blocked
```

**Why only TRENDING_BULL.** A full-history backtest (2019–present, tradeable assets, 404 trades under the pre-gate TRENDING_BULL/TRENDING_BEAR/HIGH_VOLATILITY config) broke profit factor down by regime *(regime gate validation run — pre-weight-rebalance; scoped to the three pre-gate allowed regimes only, so its 404-trade count is not directly comparable to the current 253-trade, TRENDING_BULL-only "Current baseline")*: TRENDING_BULL stands out on every metric — profit factor **1.78**, average return **+1.14%**, max drawdown **-35.1%**. TRENDING_BEAR was originally allowed alongside TRENDING_BULL and HIGH_VOLATILITY, but the same backtest showed a profit factor of only **1.09** and an average return of **+0.19%** (barely above breakeven) with a **-46.9%** max drawdown — no meaningful edge next to TRENDING_BULL. `trading212_invest` and `trading212_stocks` are both long-only cash accounts, so that comparison applies directly; the backtest engine can also go short (via `backtest.allowed_entry_regimes`), but it's kept in sync with the other two anyway for consistency, so bear-regime entries are blocked there too, the same way MIXED and RANGING_LOW_VOL already were. HIGH_VOLATILITY, previously kept allowed "for lack of data to exclude it," is excluded on the same "not proven" basis now that TRENDING_BULL is confirmed as the standout regime. (An earlier version of this note cited profit factor 0.613/0.10 for TRENDING_BULL/TRENDING_BEAR — those numbers were incorrect; 1.78/1.09 are the figures verified by re-running `run_backtest()` against production data after this gate shipped.)

`analysis/regime.py:is_entry_allowed(config, regime_name, section)` implements this: reads `config[section]["allowed_entry_regimes"]` (`section` is `"trading212_invest"`, `"trading212_stocks"`, or `"backtest"`) and returns `False` when `regime_name` isn't in that list. If the key is absent entirely, every regime is allowed — the backwards-compatible default for configs written before this gate existed. `RANGING_LOW_VOL` has only 1 trade of backtest data, too little to trust either way, so it's excluded on the same "treat as unknown" principle rather than assumed safe by omission.

Wiring — in all call sites the gate is checked first, and only an allowed regime proceeds to the `entry_signal_overrides` threshold check:
- `maybe_place_t212_orders()` and `maybe_place_stock_orders()` (`brokers/trading212.py`) — checked at the start of the BUY phase, before the deployed-capital check. A blocked regime logs `"Skipping T212 Invest BUY phase — regime is TRENDING_BEAR, entries blocked"` (or the stock-path equivalent) and returns immediately. The EXIT phase runs first and is entirely unaffected — existing positions continue to be evaluated for exit (signal reversal, confirmed NEUTRAL, backstop time limit) regardless of the current regime.
- `simulate_trades()` (`analysis/backtest.py`) — checked per date, only when `open_trade is None`; a trade that opened while its regime was allowed continues to be monitored for exit normally even if the regime later rolls into a blocked one — the gate only ever suppresses new entries, never forces an early exit.

The Daily Briefing's regime status bar and the Status page's T212 Invest Account section show a `⛔ Entries blocked — MIXED regime` (or similar) caption whenever the current regime isn't in `allowed_entry_regimes`, replacing the entry-threshold caption that would otherwise imply a threshold that can never actually be met. These captions read `allowed_entry_regimes` directly from config at render time, so they update automatically whenever the list changes — no hardcoded regime names in `main.py`.

### Regime metrics history — Market Regime Trend chart (2026-07-24)

Before this feature, none of the Daily Briefing's point-in-time metrics (ADX, VIX, S&P % vs SMA200, regime, Macro Health, buy/sell signal breadth) were retained across analysis runs — `detect_regime()` always recomputes live from the `indicators` table's *latest* row per ticker, and `indicators`/`signals` upsert on `(ticker, date)`, so only the *last* of a calendar day's 2-3 scheduled runs ever survived even that far back. There was no way to see how conditions moved over time without manually remembering or scrolling back through old briefings.

**`analysis/regime_history.py`** adds a dedicated `regime_metrics_history` table (`RegimeMetricsHistory` in `data/database.py`) and is the only module that writes to or reads it:

- **`record_regime_snapshot(regime_result, signal_rows, config, macro_health_score, timestamp=None)`** is called once at the very end of `analysis/confluence.py:generate_signals()` — after the per-ticker signal loop, before `set_metadata("last_analysis_run", ...)` — with exactly the values that run computed: `regime_result["details"]` (the same `detect_regime()` call the rest of the run used, giving ADX/VIX/price/SMA200), buy/sell/strong-buy/strong-sell counts tallied from the run's own `signals` list (`_count_signals()`, pure), and `macro_health_score` from a fresh `get_macro_dashboard(config)` call (wrapped in its own `try/except` — a macro-dashboard failure must never block the signal-generation run it's attached to). Also records `entries_allowed` — `_regime_permits_entries(config, regime_name)`, `True` if *either* T212 automation path's `allowed_entry_regimes` admits the regime (independent of that path's `enabled` flag, since `enabled` is an operational toggle, not a strategy rule) — computed from **the config actually in effect at that run**, not re-derived later. Unlike every other table in this schema, `regime_metrics_history` is **not** keyed on `(ticker, date)` — it has no ticker, and it upserts on a **minute-truncated timestamp** instead, so it keeps one row per analysis run (2-3x/day on a normal weekday) rather than collapsing to one row per day. "Never raises" — a failure here is logged and swallowed, exactly like `analysis/risk.py`'s drawdown-scaling functions, since a snapshot-recording failure must never propagate into the signal generation it's attached to.

  Because `record_regime_snapshot()` is called from *inside* `generate_signals()` rather than being a sibling function each call site must remember to also invoke, every one of `generate_signals()`'s call sites gets a snapshot automatically with no additional wiring — unlike the FRED release-calendar refresh (see "Macro module — release-calendar staleness" below), there is no analogous "second call site forgot to pair it" risk here by construction. Confirmed by auditing every call site (2026-07-24): `data/scheduler.py:_run_analysis_pipeline()` — the one shared function behind *every* scheduled job (`_pre_market_job`, `_monday_premarket_job`, `_us_open_job`, `_eod_job`'s both branches, `_trade_alert_job()`'s VIX-spike immediate-analysis path, `_fomc_reaction_job`, and `_check_missed_eod_run()`'s startup catch-up) — plus `main.py`'s Signals page "Generate Signals" button, Status page "Run Analysis Now" and "Refresh Data + Analysis" buttons, and `_initialise()`'s first-ever-run startup analysis. All five distinct call sites resolve to the one real `analysis.confluence.generate_signals()` — no hand-rolled duplicate signal-generation path exists anywhere in the repo.

- **`get_regime_metrics_history(start=None, end=None)`** reads rows in `[start, end]` (either bound optional) sorted by timestamp, returning an empty (not `None`) DataFrame when there's no data — used by the Charts page for its 3M/6M/All range filter.

- **`compute_shading_bands(df)`** — pure, no DB/Streamlit/Plotly dependency — groups consecutive same-`entries_allowed` rows into contiguous `{start, end, allowed}` bands for the chart's background shading, each band running to the *next* differing row's timestamp so bands tile the range with no gaps. Operates at whatever resolution `df` actually has, so a same-day regime flip between two live runs is represented as its own band exactly like any other — no special-casing needed.

- **`summarise_resolution(df)`** — pure — reports `{n_backfilled, n_live, boundary_timestamp}`, where `boundary_timestamp` is the last backfilled row's timestamp (only set when `df` has both kinds). Exists because backfilled and live rows genuinely differ in two ways the chart must disclose rather than let read as an unexplained artifact — see "Backfill" below.

**Backfill.** `backfill_regime_metrics_history(config=None)` reconstructs day-level history for dates that predate this feature, from data that already exists in `indicators`/`signals`/`macro_indicators`:
- **Regime**: read directly from the stored `signals` row for that date (`COALESCE(s.regime, s.notes)`, handling both the current and legacy regime-storage columns — the same pattern `brokers/trading212.py` uses when stamping `T212InvestOrder.regime`), **not** re-derived from ADX/VIX/SMA200 — per the same "use what the system actually acted on" principle the live path follows.
- **ADX/VIX/SMA200%/buy-sell counts**: reconstructed straightforwardly from `indicators` (ADX/VIX/SMA200/price for `^GSPC`) and `signals` (grouped `COUNT(*) ... GROUP BY signal` per date across the whole watchlist).
- **Macro health**: best-effort, via `_macro_health_as_of()` — reuses `analysis.macro`'s private `_classify_vix()`/`_regional_macro_score()` directly (the same "import the private shared core rather than re-deriving it" pattern `analysis/backtest.py` already uses for `_series_region()`/`_regional_weights()`), reading each configured FRED series' latest value **on or before** the target date (`date(date) <= date(:d)`, per the SQLite string-comparison convention documented under "T212 P&L reconstruction" below) so there is no look-ahead. Returns `None` (not `0`, which would misleadingly read as "neutral") when no series has any data that far back.
- **`entries_allowed`** — this is the one field backfill **cannot** recover faithfully: it didn't exist before this feature, so there is no historical record of what `allowed_entry_regimes` actually was on any given past date. Backfill applies **today's** config to the historical regime value instead — a reasonable approximation given the gate has been `TRENDING_BULL`-only across both T212 paths for a while, but a genuine inference, not ground truth, and the UI must not present it as equivalent to a live-recorded row (see below).
- Idempotent (skips any calendar date that already has a row, whether from a prior backfill or a live snapshot) and safe to re-run — wired to the Status page's **"Backfill Regime Metrics History"** button, next to "Backfill FRED History".

**Two disclosure requirements the chart enforces, not just documents** — added 2026-07-24 after review flagged both as demo/DD-context risks:
1. **Resolution jump.** Backfilled rows are one point per calendar day; live rows (accumulating from this feature's ship date onward) are one point per analysis run, up to 3/day. A chart spanning both will show a real, visible density change at the boundary — expected, not a bug, but indistinguishable from a data artifact without disclosure. `_regime_trend_chart()` (`main.py`) draws a vertical marker at `summarise_resolution()`'s `boundary_timestamp`, and `_render_charts_landing()` prints an explicit caption stating the backfilled/live point counts whenever a view contains both.
2. **`entries_allowed` provenance.** A live row's shading is ground truth (config as it actually was, captured at decision time); a backfilled row's shading is inferred (today's config applied retroactively). The same caption states this explicitly — "inferred" for the pre-boundary stretch, "recorded" for the post-boundary stretch — and the Status page backfill button's own help text discloses it up front too, rather than only in a chart caption a user might not read.

**UI placement.** The chart lives on the **Charts** page (`main.py:_render_charts_landing()`), above the existing per-ticker grouped asset grid, not as a new sidebar page and not bolted onto Daily Briefing/Signals — Charts' whole existing job is already "time-series chart," so this is that same job applied to regime/market-health metrics instead of one ticker's price. Dual-axis Plotly line chart: ADX and VIX share the left axis (both roughly-0-100 oscillators, so the ADX=25/VIX=20 regime-gate threshold lines — dotted, `add_hline` — read naturally against both), S&P % vs SMA200 gets its own right axis (a differently-scaled percentage). Background shading marks **blocked** periods only (`#8899AA` at low opacity) — the default unshaded background already reads as "normal," mirroring the DD report's PDF equity-curve convention of shading only the stretches worth a second look, not the whole timeline. A `3M`/`6M`/`All` radio (`_RM_RANGE_DAYS`) filters via `get_regime_metrics_history(start=...)`.

Macro Health and signal-breadth counts are captured in every snapshot (so no future consumer loses that history) but are **not** plotted on this chart yet — deferred pending a decision on whether they belong as toggleable series on the same chart or a separate view (risk of clutter mixing a 0-100 oscillator pair with a bounded composite score and raw counts on one axis set).

Covered by `tests/test_regime_history.py` (27 tests): snapshot persistence and its exact field values, upsert-on-minute-truncated-timestamp idempotency, the "never raises" contract on malformed input, date-range filtering, `compute_shading_bands()`'s band-grouping logic (including the same-day-flip case), `summarise_resolution()`'s boundary detection (including the all-backfilled/all-live/no-data edge cases), and backfill's reconstruction correctness, idempotency, skip-when-no-signals-row behaviour, non-overwrite of live rows, and the macro-health as-of lookup (including a same-day-boundary and a later-observation-must-be-ignored case, confirming no look-ahead).

### Sector-rotation MIXED-regime sleeve — researched, not adopted (2026-07-24)

Investigated whether a narrower, sector-relative-strength entry path during MIXED regime (when `allowed_entry_regimes` blocks all new core-strategy entries — see "Regime entry gate" above) could add edge on top of the core strategy, without touching it. Built as a fully isolated backtest module (`analysis/sector_rotation_backtest.py`, never imported by `main.py`/`data/scheduler.py`/`brokers/trading212.py`) reusing the core engine's own primitives read-only (`_regime_history()`, `compute_signal_history()`, `_resolve_entry_stop()`, `_var_position_size_core()`, `compute_metrics()`/`equity_curve_gbp()`/`walk_forward()`), with its own mini-portfolio simulator (a position-capped joint walk across the 9 tradeable SPDR sector ETFs, since the core engine's per-ticker-independent simulation would silently allow full-size positions in all 9 sectors at once — not the strategy being tested). Never wired into the scheduler, the T212 execution path, or any config — code was deleted after the finding below rather than left in the repo, matching the GARCH-stop-loss/HMM-regime-gate precedent immediately below of removing rejected techniques entirely rather than leaving dead/disabled code in place.

**Rule set tested**: relative strength = sector's trailing return minus `^GSPC`'s, over both a 20-day and 60-day lookback, both required positive (thresholds 3%/5%) as a whipsaw guard; entry additionally gated on the sector ETF's own confluence score ≥ 0.70 (reusing the existing `HIGH_VOLATILITY` threshold value rather than inventing a new number); position sizing at half the standard VaR risk fraction and £ ceiling; a shortened 18-day max-holding backstop (vs. the standard 90-day ETF backstop) plus two rotation-specific exits (RS itself breaking down, or the regime leaving MIXED) on top of the reused ATR stop-loss.

**Finding 1 — the approved rule set produces essentially zero trades, confirmed as a genuine calibration miss, not a bug.** Backtested 2019–2026 against real price history for all 9 ETFs. Per-ticker diagnostics (XLK/XLF/XLE) showed the RS gate firing normally (126–337 qualifying MIXED-regime days per ticker) while `confluence_score` never once reached 0.70 for any ticker across the full 6+ year window (observed max 0.4–0.6) — the 5-module backtest confluence score for this universe simply doesn't clear that bar. A one-factor-at-a-time sensitivity sweep confirmed this wasn't threshold-precision noise: even 0.65 produced zero trades, 0.60 produced only 2.

**Finding 2 — isolating the RS idea alone (confluence gate disabled) showed no edge either**, ruling out "just a bad threshold" as the explanation: 207 trades, profit factor 0.98, Sharpe -0.04, max drawdown -54.4% (zero transaction cost) — a coin flip with a slight negative tilt. Winner concentration was low (top 10 trades = 33.4% of winning P&L), so the weak result wasn't a few lucky outliers masking real edge. Walk-forward (24-month rolling) split 16 positive-Sharpe/13 negative-Sharpe windows — no out-of-sample consistency. Realistic transaction costs (SPDR sector ETF spreads ≈1-2bps) made it monotonically worse (PF 0.98 → 0.95 → 0.91 at 0/2/5bps per side), as expected given this sleeve's much faster turnover (8.6-day avg hold vs. the core strategy's 34.5). Layered on top of a freshly-run baseline (227 trades, PF 2.60, -29.5% max DD — matches the documented baseline), the combined portfolio was worse on every risk-adjusted axis (combined max DD -56.1%, combined Sharpe 0.63) than the baseline alone.

**Conclusion: rejected, not deferred.** Two independent findings (the approved gate never firing; the underlying RS signal showing no edge even ungated) point the same direction — this isn't a case of a fixable threshold, it's the underlying relative-strength signal not having exploitable edge on this 9-ETF universe over this period. If revisited, the more promising next step is a different MIXED-regime scoring approach entirely (not a retuned cut of the same 5-module confluence score), or testing RS on individual `us_large_cap` stocks rather than already-diversified sector ETFs — not re-running this exact design with different numbers.

### Mean-reversion MIXED-regime sleeve — researched, not adopted (2026-07-24)

Second, independent MIXED-regime investigation, following directly from the sector-rotation research above. Rationale: a module-attribution study (see "TRENDING_BULL module weight re-tuning study (2026-07-08)" above) found Mean Reversion was the only equity-relevant module showing genuine, non-saturated variation between winning and losing `TRENDING_BULL` trades (-0.32 avg score on wins vs. -0.37 on losses) — trend/volatility/volume are pinned at their maximum score by that regime's entry threshold and show no real discriminating signal in that sample, but Mean Reversion's weight is too small to be gated the same way. That's a structural reason (not a hunch) to test whether the module carries usable signal standalone, in the low-ADX, no-confirmed-trend conditions mean reversion is textbook-expected to do relatively better in.

Built the same way as the sector-rotation module: a fully isolated backtest (`analysis/mean_reversion_backtest.py`, never imported by `main.py`/`data/scheduler.py`/`brokers/trading212.py`, deleted after the finding below per the same GARCH/HMM precedent of not leaving rejected research in the repo), reusing core-engine primitives read-only — critically, `analysis/backtest.py:_mean_rev_scores()` (the vectorized mirror of `analysis/mean_reversion.py:compute_mean_reversion()`'s live RSI(14)+52-week-Z-score scoring) **reused wholesale, unmodified**, rather than a new or borrowed threshold — see the methodological note below for why this design choice mattered. Its own mini-portfolio simulator was needed for the same reason the rotation module's was: a survey found up to 23 tickers simultaneously qualifying (`mr_score == +1` and regime `== MIXED`) on a single date across the 70-ticker tradeable universe, so independent per-ticker simulation would have silently allowed two dozen full-size positions to open at once.

**Rule set tested**: entry on `mean_reversion score == +1` (the module's own already-tuned {-1,0,+1} classification — RSI<30 and 52-week Z<-1.5 both required) AND regime `== MIXED`, across the full 70-ticker tradeable watchlist with no ticker curation (deliberately not narrowed to "bonds and defensives" despite that being the top-qualifier profile — pre-filtering the universe by an assumption the score itself already tests per-date would have been a second, circular free parameter). Exit priority: ATR stop-loss (reused unmodified) → regime leaving MIXED → `mean_reversion score` leaving +1 ("reversion resolved," reusing the same entry signal symmetrically rather than a new target-price threshold) → a 7-trading-day max-holding backstop (the one genuinely new numeric parameter in the whole design, vs. 9 in the rejected rotation design — see the methodological note). Position sizing at half the standard VaR risk fraction and £ ceiling, flagged explicitly as a placeholder carried over from the rotation research rather than re-derived. A concurrent-position cap (swept 3/5/8/12) gates new entries on open-position *count*, not £ exposure.

**Finding 1 — a real signal, but thin and decisively cost-fragile.** Backtested 2019–2026, 373 trades (64 of 70 tickers had sufficient history — the 6 skipped are all `us_large_cap` additions from 2026-07-11, too recent). 65.4% win rate, profit factor 1.05, Sharpe 0.16, total return +18.1% at zero transaction cost — the classic mean-reversion shape (frequent small wins, occasional large losses), genuinely better than the rotation sleeve's flat coin-flip, but thin. Realistic costs are decisive here specifically because avg hold is only 3.1 days (vs. the rotation sleeve's 8.6): PF 1.05 → 1.01 → 0.94 and Sharpe +0.16 → +0.03 → **-0.17** at 0/2/5bps per side; total return flips from +18% to **-19%** at 5bps.

**Finding 2 — the cluster-day split (the central finding of this investigation).** Every trade was tagged with the number of tickers simultaneously qualifying on its own entry date, bucketed isolated (1) / small cluster (2-4) / large cluster (5+):

| Cluster bucket | Trades | Win rate | Profit factor | Sharpe | Max drawdown |
|---|---|---|---|---|---|
| Isolated (1 qualifier) | 99 | 64.7% | 0.80 | -0.57 | -32.9% |
| Small cluster (2-4) | 190 | **69.5%** | **1.53** | **+1.31** | -24.0% |
| Large cluster (5+) | 84 | 57.1% | 0.69 | **-1.29** | -45.3% |

Not monotonic — small clusters are where the real edge lives, isolated single-ticker signals are mediocre-to-negative, and only large clusters (broad-market stress, by construction the days the core strategy's own regime gate is designed to sit out) are clearly where money is lost.

**Finding 3 — trade-level drill-down confirms the specific failure mode is fast crashes, not bear markets generally.** All 9 trades entered during the COVID Crash window (`ANALYSIS_PERIODS`, Feb 24-26 2020, the crash's onset) lost — 0% win rate, -£120 total. Every stop-loss fired correctly and individually small (-1.6% to -8.2% per trade), but because 5-7 positions opened simultaneously in that 3-day window, the losses stacked rather than being prevented — the per-trade stop protects each position but does nothing to stop the correlated cluster from forming in the first place. By contrast, the slower-moving 2022 Bear Market (110 trades, 59.1% win rate, +£30) and the 2023-24 Recovery (104 trades, 66.3% win rate, +£38) were both net positive — mean reversion had room to actually work when the decline wasn't a sudden crash. Walk-forward (24-month rolling, 30 windows) matches this exactly: the 8 negative-Sharpe windows (down to -3.00) are almost entirely the windows spanning COVID; every window starting after mid-2020 is solidly positive bar a few late, thin-sample ones.

**Finding 4 — the dollar-terms reality check.** The -46.0% max-drawdown figure quoted above uses the standard fully-compounded-% convention (`(1+pnl_pct).cumprod()`), which implicitly assumes reinvesting the whole account into every trade — not representative of actual risk given how small the VaR-sized positions are. In real £ terms on the `backtest.starting_capital_gbp` £10,000 baseline: **total profit £45, max drawdown -£149**. Even in its best light, this strategy's absolute-dollar edge is negligible — a fact the percentage metrics alone don't convey, and worth checking directly rather than trusting a compounded-% headline whenever position sizing is this conservative.

**A cluster-aware position-sizing variant was tested and did not clearly help.** Dividing `position_gbp` by the day's qualifying-ticker count on cluster days cut max £ drawdown by 60% (-£149 → -£60) but also crushed total profit (£45 → £3.50), because the uniform 1/N dampening shrinks the genuinely-good small-cluster bucket by the same rule as the bad large-cluster one — not a targeted fix. (Note: win rate/PF/Sharpe/max-drawdown-% are mathematically identical between flat and cluster-aware sizing — the same "only `position_gbp`/`pnl_gbp` can move, never `pnl_pct`" property documented under "Drawdown-based position scaling" above — so this comparison has to be read in £ terms, not the standard %-based metrics.)

**Conclusion: not pursued further, but a more genuinely open question than the rotation sleeve's.** Real, broad-based signal (low winner concentration — top 10 trades were only 16.7% of winning P&L; spread across 20+ tickers) that is nonetheless decisively cost-fragile, economically trivial in absolute £ terms at appropriately conservative sizing, and carries a specific, identified, unresolved tail risk (correlated entries at the onset of a fast crash). **One untested idea for the record, flagged explicitly as unvalidated speculation extracted from this backtest's own results, not a finding**: a two-sided cluster filter — trade only on 2-4 simultaneous qualifiers, skip both isolated (1) and large-cluster (5+) days entirely, rather than just sizing large clusters down. Any future attempt at this needs its own independent walk-forward validation; the cluster-bucket table above was itself derived from this exact backtest's own trades and cannot be used to justify a rule built to exploit it without repeating the validation on held-out data.

### MIXED-regime research — threshold-reuse lesson (2026-07-24)

The single most important lesson from these two investigations, worth its own callout since it's directly reusable on any future MIXED-regime (or other cross-regime) research: **don't reuse a threshold value calibrated in one context as a gate in a structurally different context without first empirically sanity-checking that it produces a reasonable trade count.** The sector-rotation investigation's approved design borrowed the `HIGH_VOLATILITY` regime's 0.70 confluence threshold on the reasonable-sounding logic that trading without broad-market confirmation deserves at least that level of conviction — but the 5-module backtest confluence score for that 9-ETF universe never once reached 0.70 across 6+ years of data, silently starving the design to zero trades and costing a full build-then-diagnose cycle to discover.

The mean-reversion investigation's design was the direct, deliberate fix: rather than borrowing or inventing a threshold on the Mean Reversion module's continuous inputs, it reused the module's *entire already-tuned scoring function* (`_mean_rev_scores()`) unmodified — collapsing "what threshold?" into "does this already-validated signal fire at all here?", verified empirically (861 qualifying ticker-days across 59/64 tickers) *before* any rule set was proposed, not after. The measurable result: the rejected rotation design carried 9 genuinely novel/tunable parameters; the mean-reversion design carried 3 (`max_holding_days` plus two reused position-sizing fractions) — a direct, quantified consequence of reuse-the-whole-signal over borrow-a-threshold, not just a stated preference.

### Removed: virtual trading

A third automated entry path, `virtual_trading`, used to run alongside `trading212_invest` and `trading212_stocks`: it opened and closed simulated (no real money) LONG/SHORT positions live via `_process_virtual_trades()` in `data/scheduler.py`, called at the end of every analysis pipeline run, and fed those simulated trades into the feedback-learning system (see "Feedback learning" below). It has been removed entirely — `_process_virtual_trades()`, its scheduler call site, the currency-conversion helpers it alone used (`_instrument_ccy()`, `_gbp_to_ccy_rate()`), the VIRTUAL-specific immediate stop-loss close in `_trade_alert_job()`, the Backtest page's "Virtual Trading" tab, and the weekly summary email's "Virtual Portfolio" section are all gone.

`analysis/backtest.py:simulate_trades()` still needs LONG+SHORT entry-gating config of its own, though — unlike `trading212_invest`/`trading212_stocks`, which are long-only cash accounts, backtest trades can go either direction, so gating them on the T212 sections wouldn't make sense. Rather than deleting that config, its three keys (`entry_signal`, `entry_signal_overrides`, `allowed_entry_regimes`) moved from the old `virtual_trading:` section into the existing `backtest:` section in `config.yaml` — see "Per-regime entry signal overrides" and "Regime entry gate" above for the shared mechanism, and "Backtesting engine" below for `simulate_trades()` itself. This is a config relocation only, not a value or logic change — `simulate_trades()` was re-verified to produce identical results after the move.

Historical `trades` table rows with `account_type = 'VIRTUAL'` are not deleted (the table itself is never dropped) but are no longer opened, closed, or read by any live code path or UI page — they are inert history only. `output/briefing.py`'s weekly-email trade queries still filter them out of "real" accuracy stats (`account_type != 'VIRTUAL'`) for this reason.

### Removed: manual CFD trade logging

The original Trade Log page (manual trade entry form, open-position tracker with per-trade SL/TP edit expanders, close-trade form, position-sizing guidance) and the original Performance page (equity curve, win rate by signal type/regime, indicator hit-rate table, closed trades list — both driven by manually-logged rows in the `trades` table) were removed from `main.py` together. The Trade Log page is gone permanently — there is no automated replacement for manual CFD trade entry. The Performance page has since been **rebuilt from scratch**, driven entirely by real T212 execution data (`t212_pnl`) instead of manually-logged trades — see the **Performance** page bullet near the top of this file, "T212 P&L reconstruction", and "Feedback learning" below. The sidebar has eight pages today: the six that remained post-removal, the rebuilt Performance page, and the Robustness page added later — see "Robustness page" below.

Removed along with the two original pages (permanently — none of this was reintroduced by the Performance page rebuild):
- **`output/performance.py`**: `log_trade()`, `close_trade()`, `update_trade_plan()`, `get_open_trades()`, `get_all_trades()`, `classify_trade_theme()`, `_swap_basis_amount()`, and the theme-classification constants (`THEME_LABELS`, `_USD_BASE_FOREX`, `_USD_QUOTE_FOREX`, `_BOND_TICKERS`, `_COMMODITY_TICKERS`, `_CRYPTO_TICKERS`). `get_current_signal()`, `get_latest_price()`, `get_t212_closed_positions()` (renamed from `get_closed_trades()` once it was rewired to `t212_pnl` — see "Trade lifecycle" above), and `FOREX_QUOTE_CCY` are kept — see "Trade lifecycle" above for why each survives.
- **`output/briefing.py`**: `enrich_open_trade()` and `compute_trade_status()`, plus the entire manual-CFD alert-email chain that had no other caller once the CFD alerting loop was removed from `_trade_alert_job()` — `send_alert_email()`, `should_send_alert()`, `_build_alert_html()`, `_options_block_html()`, `_option_row_h()`, `_stop_distance_str()`, `_current_regime()`, `_next_analysis_run_uk()`, and the `_STATUS_EMAIL_LABELS`, `_REGIME_SIGNAL_NOTES`, `_STATUS_PRIORITY` constants. The weekly summary email's "Open Trades" section (plain-text and HTML) was removed for the same reason — see "Email alerts" below.
- **`data/scheduler.py`**: the entire CFD-trade alerting body of `_trade_alert_job()` — the enrich-based STOP LOSS/EXIT SIGNAL email loop and the independent price-level SL/TP proximity check — along with the now-unused `_is_exchange_in_session()` helper. The job's VIX spike check and its unconditional T212 poll/snapshot/exit-check calls are untouched; see "Automatic scheduling" above.
- **`main.py`** (original removal): `_render_trade_log_page()`, the original `_render_performance_page()`, `_render_learning_dashboard()`, `_pnl_colour()` (plus `_PNL_GREEN`/`_PNL_RED`), and their sidebar entries. The Daily Briefing's Open Trade Monitor and the daily narrative's open-position input both simplified from "CFD trades (enriched) + T212 cards, merged" down to just `_get_t212_invest_brief_cards(config)` directly — see "Broker integration" above. The "Send Test Email" button's open-trade count switched from CFD trades to T212 positions for the same reason.
- **`config.yaml`**: the `accounts:` section (Practice/Live CFD account balances and position-sizing percentages), which only the Trade Log page read.

Any CFD trade rows already in the `trades` table (account_type PRACTICE or LIVE) are not deleted — the table itself is never dropped — but are no longer opened, closed, monitored, or alerted on by any live code path, and are no longer read by any live code either: `get_t212_closed_positions()` (formerly `get_closed_trades()`) now reads `t212_pnl`, not `trades` — see "Trade lifecycle" above. `trades` rows are inert history only, kept for the record but never queried again.

### Confluence scoring

`analysis/confluence.py:generate_signals()` calls `score_ticker()` for each asset, which:
1. Calls all seven `compute_*` functions fresh from stored price data
2. Computes `weighted_score = Σ(module_score × regime_weight)` ∈ [-1, 1]
3. Classifies as `STRONG_BUY` (≥`buy_threshold`), `BUY` (≥0.3), `NEUTRAL`, `SELL` (≤-0.3), `STRONG_SELL` (≤`-sell_threshold`) — STRONG boundaries are regime-aware: `get_regime_thresholds(regime_name, config)` reads `confluence.regime_thresholds.<REGIME>` from config (all five regimes have explicit entries; no global fallback). TRENDING_BULL raises sell_threshold to 0.70 (harder to short in a bull), TRENDING_BEAR raises buy_threshold to 0.70, MIXED tightens to 0.60/0.60, HIGH_VOLATILITY to 0.70/0.70, RANGING_LOW_VOL stays at 0.55/0.55. The backtest engine applies the same per-date regime thresholds via vectorised Series. All downstream consumers (email alerts, T212 automation, trade health status) read the already-classified `signal` string from the DB — no re-classification from raw scores. The Daily Briefing shows the current effective thresholds and approximate module agreement required (e.g. "Threshold: 0.60 — requires ~4/7 modules to agree")
4. Upserts to the `signals` table keyed on `(ticker, today's date)`

The seventh module is `compute_correlations(ticker, config)` from `analysis/correlations.py`. It finds all predefined pairs involving the ticker, checks if the current rolling correlation matches the expected direction (CONFIRMING/DIVERGING/NEUTRAL), and returns a score of -1/0/1. Most watchlist tickers appear in zero or one pair and receive score=0; tickers like `^GSPC`, `GLD`, and `^VIX` appear in multiple pairs and receive a blended score.

### Feedback learning

`confluence.py:recalculate_learned_weights()` reads all closed T212 positions via `_fetch_trades_for_learning()` and computes per-module win rates (direction matched profitable outcome). `_fetch_trades_for_learning()` queries `t212_pnl` (see "T212 P&L reconstruction" below), aliasing `pnl_gbp AS pnl` so the win/loss comparison logic below it is unchanged from before the rewiring. Win rates are normalised and clamped between `min_single_weight` (0.05) and `max_single_weight` (0.30) from config, then stored in `system_metadata` under `learned_weights` (JSON, including `n_trades` — the `t212_pnl` row count the snapshot was computed from). When learned weights exist they override the static `REGIME_WEIGHTS` in `score_ticker()`. The correlation module is held at a neutral 0.5 win rate since its score is not stored on `t212_pnl`. A history of the last 20 snapshots is kept under `weight_history`. `maybe_update_weights()` is called at the end of each `generate_signals()` run and only triggers recalculation when the closed-position count hits a new multiple of the configured boundary (`learning.min_trades_before_adjustment`, default 20).

Both virtual trading and manual CFD trade logging have been removed (see "Removed: virtual trading" and "Removed: manual CFD trade logging" above) — `t212_pnl` is the only closed-position data source this system reads. Because it is populated only as real T212 Invest positions actually close (roughly 5–10 exits/year in practice), it will take a while to accumulate the 20 rows `min_trades_before_adjustment` requires before the first automatic recalculation fires; until then `score_ticker()` continues using whatever `learned_weights` snapshot already existed (most likely none, since no snapshot has ever been computed from real T212 data), or the static `REGIME_WEIGHTS` if none exists. The Performance page's Learning System section shows the live `t212_pnl` count against this threshold so progress is visible without querying the database directly.

### Tradeable vs analysis-only assets

Each watchlist entry in `config.yaml` has a `tradeable: true/false` flag. **`tradeable: true` means "eligible for automated T212 Invest orders"** — specifically, the ticker appears in `trading212_invest.ucits_mapping` or in `watchlist.us_large_cap`; every other ticker is `tradeable: false`. This is a narrower meaning than the flag originally had: before virtual trading was removed (see "Removed: virtual trading" above), `tradeable: true` covered every asset virtual trading could open a simulated LONG/SHORT position on — a much broader set with no relationship to what T212 Invest could actually execute. All 104 watchlist tickers (the total at the time) were re-audited against this narrower rule on 2026-07-03, flipping 13 tickers from `true` to `false` (four major forex pairs, `VIXY`, `IGV`, `KRE`, `VNQ`, `JNK`, `XLRE`, `USO`, `GDX`, `SHY` — none had a `ucits_mapping` entry or `us_large_cap` membership) and leaving 64 `tradeable: true` / 40 `tradeable: false` out of that day's 104-ticker total. Two further `tradeable: false` entries (`SPMO`, `MTUM` — the new `factor_etfs` group) have been added since, bringing the split to 64 `tradeable: true` / 42 `tradeable: false` out of 106. Three more `tradeable: true` `us_large_cap` entries (`NFLX`, `DIS`, `ORCL`) were added 2026-07-11, bringing the split to 67 `tradeable: true` / 42 `tradeable: false` out of 109. A further three `tradeable: true` `us_large_cap` entries (`GE`, `LIN`, `ISRG`) were added the same day, bringing the current split to 70 `tradeable: true` / 42 `tradeable: false` out of 112 — see "Adding assets to the watchlist" below for the per-ticker rationale.

`get_all_tickers()` returns all tickers regardless of this flag — analysis-only (`tradeable: false`) assets still generate signals, feed correlations/macro scoring, and participate in regime detection; the tradeable subset is derived at startup in `main.py` as `tradeable_set` and only affects opportunity filtering, order-placement eligibility, and UI display. Assets with `tradeable: false` (e.g. ^GSPC, ^IXIC, CPER, CORN) are excluded from opportunity filtering on the Daily Briefing. The Signals page shows all assets but displays a "Tradeable" sub-count on every signal distribution type. The `brief_include_analysis` checkbox on the Daily Briefing allows temporarily including analysis-only assets in the opportunities list.

### Trading212 instrument mapping

Watchlist entries may carry `trading212_instrument: "XAG/USD"` when the T212 instrument name differs from the yfinance ticker. At startup, `main.py` builds a `t212_names: dict[str, str]` mapping `ticker → t212_name` from all watchlist entries that have this field — previously used to pre-fill the Platform Instrument field on the now-removed Trade Log form (see "Removed: manual CFD trade logging" below).

### Cross-asset correlation module

`analysis/correlations.py` defines a `RELATIONSHIP_MAP` of **34 predefined pairs** with expected correlation direction (+1 positive, -1 inverse). The original 10 pairs cover core macro relationships (GLD/UUP, USO/XLE, CPER/^GSPC, HYG/^GSPC, ^VIX/^GSPC, TLT/^GSPC, GBPUSD=X/^FTSE, EURUSD=X/^GSPC, BTC-USD/^GSPC, SLV/GLD as a risk-appetite ratio). The expanded set adds: TLT/GLD, USO/UNG, XLF/TLT, XLK/^VIX, XLU/TLT, GLD/^GSPC, EEM/UUP, HYG/TLT, BTC-USD/GLD, XLE/USO, SOXX/^GSPC, KRE/TLT, VNQ/TLT, FXI/UUP, EMB/UUP, KBE/T10Y2Y, GLD/DFII10, INDA/UUP.

**6 more pairs added 2026-07-08** covering the new `factor_etfs` group, a carry-trade proxy, ECB rate sensitivity, and two sector-concentration monitors: `SPMO/^GSPC` and `MTUM/^GSPC` (two independently-methodologied momentum-factor ETFs vs the market — both underperforming simultaneously is a stronger rotation warning than either alone), `USDJPY=X/^GSPC` (carry-trade proxy — a BoJ hike strengthens JPY/falls USDJPY and unwinds the yen carry trade, pressuring global risk assets), `VGK/ECBDFR` (European equities vs the ECB deposit rate — a third FRED-series pair leg, see below), `NVDA/SOXX` (concentration risk — NVDA now dominates SOXX's weighting; divergence signals either an NVDA-specific event or sector performance broadening/narrowing beyond its largest constituent), and `JPM/KBE` (large-cap vs broader bank-sector coherence — JPM outperforming KBE signals flight-to-quality within financials, KBE outperforming JPM signals confidence in the broader, more rate-sensitive bank sector). A `CPER/^GSPC` pair was also requested in this batch but was **not added** — it already existed in the original 10 (Copper vs S&P 500); adding it again would have been a duplicate.

**FRED series as pair legs:** Three pairs use FRED macro series (not yfinance price tickers) as one leg — `KBE/T10Y2Y` (bank stocks vs yield curve steepness), `GLD/DFII10` (gold vs real yields), and `VGK/ECBDFR` (European equities vs the ECB deposit rate, added 2026-07-08). `_FRED_PAIR_SERIES: frozenset[str] = frozenset({"T10Y2Y", "DFII10", "ECBDFR"})` gates which legs use macro data — any FRED series already tracked in `macro.fred_series` (config.yaml) can be added as a pair leg this same way, since `_get_macro_changes(series_id, days)` reads generically by `series_id` from `macro_indicators` with no per-series branching. `_get_macro_changes()` returns a `pd.Series` of day-over-day changes using `diff()` not `pct_change()` — yield series can cross zero and `pct_change()` is undefined there. FRED legs do not generate catch-up opportunity flags (`return_30d = None`). `_ALL_PAIR_TICKERS` is derived dynamically by filtering out `_FRED_PAIR_SERIES` members, so only price-data tickers appear in the correlation matrix heatmap.

Per pair, the module computes: 30-day rolling correlation, 90-day rolling correlation, historical mean/std of the 30d rolling series, z-score, and a CONFIRMING/DIVERGING/NEUTRAL status. DIVERGING requires both a sign mismatch (current vs expected) AND z-score ≥ 1.5. CONFIRMING requires a sign match AND z-score ≤ 1.0.

`compute_all_pairs(config)` is called once at the end of `run_analysis()` (not per-ticker) and upserts results to the `correlations` table (`ticker_a, ticker_b, date` unique constraint).

`build_correlation_matrix(window=30)` returns a square pandas DataFrame of pairwise correlations across all price-data tickers referenced in any pair (FRED series excluded) — used to render the Plotly heatmap on the Relationships page.

**Proxy inheritance (`CATEGORY_PROXY`):** Tickers that do not appear directly in any `RELATIONSHIP_MAP` pair can still receive a correlation score via `CATEGORY_PROXY: dict[str, str]` — a mapping from ticker to a related pair member whose pair score is blended at 50% into the ticker's confluence score. This allows analysis-only assets like COPX (→ GLD), XOP (→ USO), ITB (→ TLT), USDCNY=X (→ FXI), USDINR=X (→ INDA), and USDBRL=X (→ EWZ) to participate in correlation scoring without requiring bespoke pairs. A proxy inheritance table is rendered at the bottom of the Relationships page. Proxies are applied inside `compute_correlations(ticker, config)` — the direct-pair path runs first; proxy only fires when no direct pair exists.

The regime string is stored in both the `regime` column and the legacy `notes` column of the signals table. `get_latest_signals()` returns rows sorted by `confluence_score` descending.

### TA-Lib fallback pattern

Every analysis module tries `import talib` and sets `_TALIB_AVAILABLE`. All computation paths have a pure-pandas/numpy fallback so the app runs without TA-Lib. Always maintain both paths when editing or adding indicator logic.

### Database writes

All writes use `sqlite_insert(...).on_conflict_do_update(...)` (upsert) so every operation is idempotent. The unique constraint is `(ticker, date)` on every table. `get_session()` returns a new session each call — callers must close it (use `try/finally`). The engine is created fresh each call using `NullPool` (no connection pooling) so every `with conn:` block always receives a brand-new DBAPI connection — this avoids stale-pool errors in Streamlit's multi-threaded rerun environment. WAL mode and `PRAGMA foreign_keys=ON` are applied via a SQLAlchemy `"connect"` event listener on every new connection, so they are always active without a separate setup query.

For **reading** indicator data in the UI, use raw SQL via `get_engine().connect()` rather than ORM queries — the ORM mapper can silently return `None` when the live schema has columns the mapper hasn't seen. See `_render_indicator_panel()` in `main.py` for the pattern.

`data/database.py` also defines a `SystemMetadata` key-value table (`key TEXT PRIMARY KEY, value TEXT, updated_at DATETIME`). Two helpers manage it: `set_metadata(key, value)` (upserts via `INSERT OR REPLACE`) and `get_metadata(key) -> datetime | None` (parses the stored ISO string). `pull_incremental()` stamps `last_refresh_run` and `generate_signals()` stamps `last_analysis_run` so the Status page can display actual wall-clock times rather than midnight trade dates.

The Signals page ranked table uses `st.dataframe()` with a Pandas `Styler` (`.map()` per column subset) rather than a custom HTML table. This lets Streamlit's theme control fonts, row backgrounds, and borders while only the meaningful cells carry explicit colours (signal badge, score, module symbols). Set `height=header_px + row_height_px * len(rows)` to expand the frame to its full content and suppress the scrollbar.

### UK-time display in the UI

All storage, scheduling, and internal computation is UTC — `datetime.utcnow()`, APScheduler cron triggers, `config.yaml`'s `scheduler.runs` block, and every DB timestamp column are all UTC throughout the backend. The UI, however, must never show a raw UTC value or a hardcoded "HH:MM UTC" string to the user — every timestamp or scheduled-run time rendered on a page is converted to UK local time (GMT in winter, BST in summer) via two helpers defined near the top of `main.py`:

- `_fmt_uk(dt, fmt="%Y-%m-%d %H:%M")` — converts a naive-UTC or UTC-aware `datetime` to `Europe/London` local time, formats it with `fmt`, and appends `"GMT"` or `"BST"` (from `uk.dst()`) automatically. Never bake the literal string `"UTC"` into the `fmt` argument — `_fmt_uk` already appends the correct live abbreviation, so a hardcoded `"UTC"` in the format string produces a mislabeled result (e.g. `"21:30 UTC BST"`).
- `_uk_sched_time(hour, minute)` — converts a fixed UTC hour:minute (e.g. the 07:00/13:30/21:30 scheduler slots) to UK local time using *today's* DST offset, for copy like "updates at 07:00, 13:30 & 21:30 UTC" that would otherwise hardcode the UTC clock time directly into UI strings.

`output/briefing.py` (weekly summary and trade-alert emails) defines an identical `_fmt_uk` and applies the same rule. When adding any new UI text or email copy that references a time — a schedule slot, a "last updated" timestamp, a DB-stored `placed_at`/`fetched_at` string — route it through one of these two helpers rather than displaying the raw UTC value or hardcoding a UTC clock time into the string.

### Status page and startup automation

`main.py:_initialise()` is called on every Streamlit session start. On the **first** request of a session it checks `_has_price_data()` (a lightweight `SELECT 1 FROM price_data LIMIT 1`). If price data exists it synchronously runs `run_analysis()` then `generate_signals()` for the full watchlist and stores a completion message in `st.session_state["_startup_msg"]`. The main render loop pops that key and calls `st.toast()` so the user sees a non-intrusive notification.

**Scheduler startup:** `_start_scheduler_at_startup()` is a `@st.cache_resource` function called at module level in `main.py` (executes once per Streamlit server process). It reads `scheduler_heartbeat` from `system_metadata` — if a heartbeat was written less than 90 seconds ago the standalone daemon is active and the function returns immediately, preventing double job execution. If no recent heartbeat is found it calls `start_scheduler()` to start an in-process APScheduler as a fallback (for local dev or if the daemon is not running). The Scheduler Health card on the Status page shows one of three states: "RUNNING (daemon · heartbeat Xs ago)", "RUNNING (in-process)", or "STOPPED".

Below that top-level state, the Status page also renders a per-job Scheduler Health table (Job / Schedule / Next Run / In / Status) — one row per registered job, built from live APScheduler `next_run_time` values when the in-process scheduler is running, or from reconstructed `CronTrigger` objects (mirroring `data/scheduler.py`'s own trigger definitions) when only the standalone daemon's heartbeat is detected. Every row's Status badge is "OK" purely from the trigger's next-fire time, **except** the End of Day row, which additionally flags **OVERDUE** when `last_full_refresh` (`system_metadata`) is more than 26 hours old on a weekday — a genuine "the actual EOD data pull hasn't completed recently" signal, independent of whether the next scheduled fire time is hours away.

**Monday exemption for the EOD OVERDUE flag (2026-07-06 fix).** No EOD job runs on Saturday or Sunday, so `last_full_refresh` is *always* more than 26 hours old every Monday until that day's own 21:30 UTC EOD run completes (Friday 21:30 UTC → Monday morning is already ~58h) — the original `> 26h and weekday` check had no weekend awareness, so it displayed a false OVERDUE alarm on the End of Day row for roughly 22 hours every single Monday (00:00–21:30 UTC) with nothing actually wrong. Both places this check appears (the in-process-scheduler branch and the daemon-mode branch — the two render alternately depending on which scheduler is detected, so the logic is duplicated once per branch) now additionally exempt the case `now.weekday() == 0 and now < 21:30 UTC` from the OVERDUE flag, leaving genuine missed-run detection on Tuesday–Friday (and Monday evening onward) untouched. This is a display-only fix — it does not change `_run_analysis_pipeline()`, `_monday_premarket_job()`, or any other scheduling/order-placement behaviour, only the Status page's own OVERDUE badge computation.

`_render_home_page()` powers both the **Status** page and the Charts landing (shown when no asset is selected). It draws a 2×3 status grid (Last Deployment, Service Uptime, Database Size, Last Data Refresh, Last Analysis Run, Next Scheduled Run) using `st.cache_data.clear()` at entry to flush stale values, then `@st.cache_data(ttl=60)` wrappers for the two metadata reads. Cards are colour-coded green/amber/red via `_age_level()`. All dynamic values passed to `unsafe_allow_html` are sanitised with `html.escape()`. Service uptime is read from `psutil.Process().create_time()`.

**`data/scheduler.py`** registers eleven APScheduler jobs. All standard analysis runs share `_run_analysis_pipeline(config, tickers, run_type)` which calls `run_analysis()` → `generate_signals()` → stamps `last_run_type` → `maybe_place_t212_orders()` → `maybe_place_stock_orders()` → `maybe_place_v2_orders()` (the first two are no-ops as of the 2026-07-31 V2 cutover — see "V2 live execution and cutover" below — the third is the live path). `generate_signals()` itself stamps `last_analysis_run`. The EOD job additionally calls `pull_incremental()` for open exchanges (per-exchange calendar check), stamps `last_full_refresh`, and calls `_fetch_t212_snapshot(config)` to keep the account/positions snapshot current. Each step is in its own `try/except` so one failure never blocks the rest. A `_dispatch_pending_jobs()` job runs every 60 seconds; it writes the current UTC time to `system_metadata` under `scheduler_heartbeat` (used by `_start_scheduler_at_startup()` to detect a live daemon), then checks `pending_t212_fetch` and, when set, calls `_fetch_t212_snapshot()` — this is how the UI Refresh button triggers an out-of-band T212 data fetch without making direct API calls from the Streamlit thread. `_fomc_reaction_job()` fires at 19:05 UTC every weekday but calls `analysis/fomc.py:is_fomc_decision_day()` and immediately returns on non-FOMC days; on decision days it runs `_run_analysis_pipeline()` to capture the post-announcement market reaction before the regular 21:30 EOD run. The FOMC calendar uses a hardcoded 2025–2026 schedule plus a Fed press-release API check for real-time confirmation. `_boj_reaction_job()` (03:30 UTC weekdays) and `_ecb_reaction_job()` (12:30 UTC weekdays) follow the identical fire-every-weekday-and-no-op-unless-decision-day shape, but check `analysis/central_bank.py:is_boj_decision_day()`/`is_ecb_decision_day()` against config.yaml-sourced dates instead of a hardcoded list, and additionally send an alert email — see "Central bank event monitoring — BoJ / ECB" below. One more job, `_monday_premarket_job()` (registered as `monday_premarket`, `day_of_week="mon"` only), replaces `_pre_market_job()` on Mondays — see "Monday pre-market job" above. `_v2_membership_refresh_job()` (registered as `v2_membership_refresh`, 21:15 UTC weekdays, 15 minutes ahead of the EOD job) calls `analysis/v2/universe.py:refresh_membership_from_source()` inside its own try/except — see "V2 architecture" below.

`_trade_alert_job()` runs hourly 00:00–21:00 + 23:00 UTC on weekdays (covering all major market sessions). Manually-logged CFD trade alerting (the STOP LOSS / EXIT SIGNAL email loop, and the price-level SL/TP proximity check) was removed along with the Trade Log page — see "Removed: manual CFD trade logging" above; any CFD rows already in the `trades` table are no longer monitored or alerted on. The job now runs, unconditionally on every firing: a VIX spike check (>20% intraday jump vs. the previous close triggers an immediate analysis-only pipeline run and a spike alert email, once per calendar day), `poll_placed_orders()`, a T212 account/positions snapshot refresh, and `maybe_exit_t212_positions()` / `maybe_exit_stock_positions()` (see "Broker integration" below).

`_weekly_summary_job()` runs every Sunday at 10:00 UTC. It calls `output/briefing.py:send_weekly_summary_email(config)` which gathers all data from the DB (no external API calls) and sends an HTML email with plain-text fallback covering regime/overlays, signal snapshot, 7-day signal performance, open trades, closed trades P&L, accuracy progress, macro pulse, and diverging pairs. Can be triggered on demand via the "Send Weekly Summary Now" button on the Status page. Controlled independently via `notifications.weekly_summary.enabled`.

### Trade lifecycle

Manual CFD trade logging (`log_trade()`, `close_trade()`, `get_open_trades()`, `enrich_open_trade()`) was removed — see "Removed: manual CFD trade logging" above. `output/performance.py` now only provides:
- `get_current_signal(ticker)` — reads the most recent signals row (uses `notes AS regime` since the `regime` column is written by the legacy path). Used by the live T212 exit checks (`brokers/trading212.py`).
- `get_latest_price(ticker)` — live price via a 5-day yfinance window; the UI wraps this with `@st.cache_data(ttl=300)` to limit API calls. Used by the hourly trade-alert job (VIX spike check) and Daily Briefing opportunity cards.
- `get_t212_closed_positions(from_date=None, to_date=None)` — renamed from `get_closed_trades()` when it was rewired to read `t212_pnl` instead of the legacy `trades` table (see "T212 P&L reconstruction" below). Every row is a real T212 order fill, so the `account_type` filter parameter the old CFD-era signature had (to exclude synthetic VIRTUAL rows) was dropped entirely — there's no synthetic data left to filter out. Since T212 Invest is a long-only cash account, each returned dict gets a synthetic `"direction": "LONG"` key for callers that display trade direction, and `pnl_gbp` is aliased to `"pnl"` for backward-compatible field access. Used by the weekly summary email's "T212 Positions Closed This Week" and "Signal Accuracy Progress" sections (`output/briefing.py:_gather_weekly_data()`) and by the Performance page's Closed Positions / Performance Metrics sections (`main.py:_get_t212_pnl_rows()` reads `t212_pnl` directly rather than going through this function, but both read the same table).

`FOREX_QUOTE_CCY` in `performance.py` maps forex CFD tickers to their quote currency (USD for GBP/USD, EUR/USD, AUD/USD; JPY for USD/JPY; etc.). It's kept as a defensive fallback: `main.py:_render_trade_card()` — the full-detail card renderer used for T212 Invest positions on the Performance page's Open Positions section — references it in a non-GBP P&L-display branch, but that branch is theoretically unreachable for T212-only data — T212 Invest cards always set `unreal_pnl_gbp` directly (see `_get_t212_invest_brief_cards()` above), and T212 Invest never trades forex instruments in the first place (see "Forex exclusion" above). Retained in case a future card source ever needs the fallback rather than deleted outright.

`output/signals.py` is pure-pandas analysis over a list[dict] of closed trades — no DB calls. Functions: `accuracy_summary`, `accuracy_by_signal_type`, `accuracy_by_regime`, `accuracy_by_indicator`, `equity_curve`. Still not called from any page — the rebuilt Performance page reuses `analysis/backtest_metrics.py`'s existing functions (`compute_metrics`, `equity_curve`, `regime_performance`, `exit_reason_breakdown`) against a `t212_pnl`-derived DataFrame instead (see the **Performance** page bullet near the top of this file), since those were already written to the shape the Backtest page needed and only required a column-renaming adapter (`main.py:_t212_pnl_metrics_df()`) rather than a second, near-duplicate aggregation implementation. `output/signals.py` remains unused; a future consumer could still reuse it if `main.py` ever needs list-of-dicts-shaped analysis instead of DataFrame-shaped.

### Daily narrative

`output/briefing.py:_generate_daily_narrative(regime, macro_data, relationships, signals, overlays, open_trades, config) -> str` produces the "Today's Briefing" card on the Daily Briefing page — three short plain-English `<p>` paragraphs (market environment, cross-asset relationships, system status/what to watch) rendered inside a bordered card immediately below the regime status bar and overlay detail strips, above FOMC key events, top opportunities, Macro Dashboard, and the Open Trade Monitor. It is **rule-based, not AI-generated**: every sentence is a conditional fragment assembled from threshold checks against the same data rendered elsewhere on the page, following the identical pattern the Signals page's "Market Intelligence" summary uses (`main.py:_render_signals_page`'s `_intel` list, joined into a single `st.info()` call). No call is made to the Anthropic/Claude API or any other external service, nothing is written to the database (no caching table — the narrative is cheap enough to regenerate fresh on every render), and no scheduler job is involved. Migrating to Claude API generation for more fluid phrasing is a considered future enhancement, not yet implemented.

Inputs (passed in by `main.py:_render_briefing_page()`). Because the card sits near the top of the page, above the sections that normally compute this data, most inputs are fetched under a dedicated `_narrative_`-prefixed local copy rather than reused from later variables — cheap, read-only DB calls, so the minor duplication (`macro_dash` and the signal-distribution counts are each read twice per render: once here, once by the Macro Dashboard / opportunities sections further down) is an accepted trade-off for the requested placement:
- `regime` — the `detect_regime()` dict (`regime`, `details.{price,sma_200,adx,vix}`), reused as-is from the status bar above. Note `regime["regime"]` is a real `Regime` (`str, Enum`) instance in production, not a plain string — `_generate_daily_narrative()` reads `.value` explicitly rather than `str()`-converting it, since `str()` on a `(str, Enum)` member renders as `"Regime.MIXED"` (Enum's default `__str__`), not the plain value.
- `macro_data` — `get_macro_context()` (reused from the status bar above) and a fresh `get_macro_dashboard()` call merged into one dict (their keys don't collide), giving both the regime-adjacent readings (VIX, yield curve shape, dollar trend) and the macro dashboard's composite score, bullish/bearish/neutral counts, and per-indicator context (CFNAI, real yields, breakeven inflation, credit spreads, Fed funds rate).
- `relationships` — `get_stored_correlations()`, called fresh.
- `signals` — a small dict of signal-distribution counts (`{"counts", "tradeable_counts", "total", "tradeable_total"}`) computed from a fresh `get_latest_signals()` call, not the raw signal rows and not the page's own later copy.
- `overlays` — the active-overlays set already read from `system_metadata`, reused as-is from the status bar above.
- `open_trades` — a list of dicts with a `status` key (ON TRACK / WEAKENING / EXIT SIGNAL / STOP LOSS), from `_get_t212_invest_brief_cards(config)` — the same call the Open Trade Monitor section further down uses.
- `config` — the full app config, read via `analysis.regime.is_entry_allowed()` per automation path (`trading212_invest`, `trading212_stocks`) so paragraph 3 never claims the system is "actively entering positions" unless at least one enabled path is actually unblocked for the current regime — this is the mechanism that keeps the narrative from contradicting the status bar's own `⛔ Entries blocked` caption for the same regime.

Threshold rules used to build the sentence fragments: ADX < 20 → "weak momentum", 20–25 → "moderate", > 25 → "strong trend"; VIX < 15 → "suppressed", 15–20 → "calm", 20–30 → "elevated", > 30 → "high volatility"; macro composite score > 0.3 → "supportive", −0.3 to 0.3 → "neutral/mixed", < −0.3 → "cautionary"; CFNAI < −0.70 → "recessionary", −0.70–0 → "running below trend", > 0 → "running above trend"; a DIVERGING correlation pair with |Z| > 1.5 is named explicitly by label; zero DIVERGING pairs → "cross-asset relationships are broadly normal"; `TRENDING_BULL` with no active overlays and at least one unblocked automation path → "actively entering new positions"; any other combination → "not opening new positions", with the specific blocking reason(s) (regime gate and/or active overlay) named.

Covered by `tests/test_briefing.py` — pure unit tests (no `db_engine` fixture needed, since the function takes no DB or network dependency) covering: MIXED regime wording, a DIVERGING pair above the Z=1.5 threshold being named, TRENDING_BULL-with-no-overlays producing "actively entering", an active RECESSION_RISK overlay being mentioned, non-empty output for all five regimes, and that a blocked regime (TRENDING_BEAR under the shipped `allowed_entry_regimes` config) never produces "actively entering" language.

### Schema migrations and startup fixes

New columns are added to existing tables via `_apply_migrations()` in `data/database.py`. Each `ALTER TABLE` runs in its own `engine.begin()` block so a duplicate-column error never aborts subsequent statements. `init_db()` calls both `Base.metadata.create_all()` and `_apply_migrations()`. It is called **at module level in `main.py`** (immediately after logging setup, before any function definitions) to guarantee tables exist on fresh deployments, and again inside `_initialise()` on every page render (idempotent — fast when schema is current). Never wrap multiple `ALTER TABLE` calls in a single `engine.begin()`.

`_apply_data_fixes(engine)` runs after migrations on every startup. Most fixes are keyed via `_migration_applied()` so they run only once and are skipped on every subsequent startup. One fix runs unconditionally on every startup: `DELETE FROM price_data WHERE close IS NULL OR close <= 0`. This removes incomplete intraday bars that yfinance can return for sessions that hadn't closed at fetch time. A single such row in the rolling window makes `pd.Series.rolling(n).mean()` return NaN for all subsequent `n` dates, breaking the SMA overlay on the chart. The ingestion layer (`_upsert_price_rows` in `data/ingestion.py`) now also skips these rows before they reach the database, so the startup DELETE is a no-op when no bad rows exist. Use the `_migration_applied` pattern for one-time corrections to existing trade rows; use the unconditional DELETE pattern for data-quality guards that must survive repeated incremental pulls.

**`_purge_unlisted_tickers(engine)` was removed 2026-08-01 — this paragraph is retained as historical record only, describing behavior that no longer exists.** It used to run on every startup, reading the current watchlist from config.yaml and deleting rows for any ticker no longer present from `price_data` (and, pre-V1-removal, `indicators`/`signals`/`correlations` too). See "`price_data` purge removed — permanent storage is now the design" below for why, what was verified before removing it, and what this means going forward.

### Additional database tables

Beyond the tables described above, `data/database.py` defines:

- **`MacroIndicator`** (`series_id, date` unique) — stores FRED series values with `pct_change_30d` (≈ month-over-month), `z_score_2y` (value vs 2-year rolling mean/std), and a −1/0/+1 `score`. `compute_macro(ticker, config)` reads from this table (no FRED API calls at analysis time) and computes a per-ticker composite score. After averaging all stored series scores + VIX, it applies ticker-specific adjustments: CFNAI boosts/penalises equity-group tickers (±0.10/−0.20) based on economic cycle; DFEDTARU trend routes ±0.10 to rate-sensitive assets (VNQ/XLRE/ITB bearish when rising, KBE/KRE/XLF bullish); M2SL growth routes ±0.10/+0.05 to crypto/gold; PERMIT falling >5% MoM penalises real estate tickers −0.10. `SIGNALS_ACTIVE` (frozenset exported from `analysis/macro.py`) lists which series have ticker-specific routing or overlay triggers — used by the dashboard to show "✓ Signals" vs "Composite" tags.
- **`OpportunityFlag`** (`ticker, flag_type, date` unique) — records catch-up opportunities detected when a correlation pair is DIVERGING with z-score ≥ 1.5. Fields include `z_score`, `pair_corr_30d`, `price_at_flag`, and `return_5d`/`return_20d` (populated on later runs). `get_active_opportunity_flags()` returns flags from the most recent analysis date; they are displayed as compact strips in the Signals and Daily Briefing header blocks.
- **`Performance`** (`ticker, date` unique) — rolling performance metrics: daily return, cumulative return, drawdown, max drawdown, Sharpe, Sortino, Calmar, win rate, avg win/loss, profit factor. Written by the analysis pipeline but not currently surfaced in any UI view. Not to be confused with the **Performance** UI page (see near the top of this file), which is unrelated and reads `t212_pnl` instead — this table's name predates the page and nothing currently connects the two.
- **`BacktestRun`** (`id` PK) — metadata for each backtest run: name, created_at, start_date, end_date, tickers_json, params_json, status (running/complete/failed), error_message, and summary metrics (total_trades, win_rate, avg_return_pct, sharpe_ratio, max_drawdown_pct, total_return_pct, avg_holding_days).
- **`BacktestSignal`** (`run_id, ticker, date` unique) — per-ticker per-date signal history generated during a backtest run: regime, confluence_score, signal classification, all five module scores, close_price, atr.
- **`BacktestTrade`** (`id` PK, FK to `run_id`) — simulated trades from a backtest run: ticker, direction, entry/exit date and price, entry_signal, exit_reason (STOP_LOSS / SIGNAL_REVERSAL / MAX_HOLDING / END_OF_DATA), pnl_pct, `stop_distance_pct` (ATR-based stop distance used at entry, % of entry price — see "GARCH stop loss — why it was removed" below), `drawdown_scale_factor` / `drawdown_pct_at_entry` (see "Drawdown-based position scaling" below), regime_at_entry, all five module scores at entry, holding_days, is_win.
- **`T212InvestOrder`** (`t212_invest_orders` table) — every T212 Invest order attempt (BUY and SELL, ETF and stock). See "Broker integration — Trading212 Invest" above for the full field list.
- **`T212Pnl`** (`t212_pnl` table, unique on `buy_order_id, sell_order_id`) — reconstructed realised P&L per closed T212 Invest position, derived from `T212InvestOrder` rows. See "T212 P&L reconstruction" below for the full field list and reconstruction mechanism.
- **`RegimeMetricsHistory`** (`regime_metrics_history` table, unique on minute-truncated `timestamp` — **not** `(ticker, date)`, since it has no ticker) — point-in-time regime/market-health snapshot captured on every `generate_signals()` run: `regime`, `entries_allowed` (0/1, snapshotted at write time), `adx`, `vix`, `sp500_pct_vs_sma200`, `macro_health_score`, `buy_count`/`sell_count`/`strong_buy_count`/`strong_sell_count`/`total_signals`, `is_backfilled` (0 for live rows, 1 for rows reconstructed by `backfill_regime_metrics_history()`). Powers the Charts page's Market Regime Trend chart — see "Regime metrics history — Market Regime Trend chart" above.

The `correlations` table uses column names `divergence` (CONFIRMING/DIVERGING/NEUTRAL), `z_score`, and `pair_score` — not `status_30d`, `z_score_30d`, or `expected_direction`. Always use the ORM model or check `data/database.py:CorrelationData` before writing raw SQL against this table.

### Backtesting engine

> **Current baseline (2019–present, 64 tradeable assets, as of 2026-07-05):**
> **253 trades · 47.0% win rate · PF 2.48 · Sharpe 0.792 · Max drawdown -30.3%**
> Exit reasons: Stop Loss 46.6% · Max Holding 37.5% · Signal Reversal 15.8%
>
> This is the ATR-stop, VaR-sizing, TRENDING_BULL-weight-rebalanced, post-indicator-redundancy-removal baseline — **the current production configuration** (`entry_signal: STRONG_BUY`, `allowed_entry_regimes: [TRENDING_BULL]` for `backtest`/`trading212_invest`/`trading212_stocks` alike). Every other backtest number quoted elsewhere in this file predates one or more of these changes (the 2026-07-04 TRENDING_BULL weight rebalance, VaR position sizing, or the 2026-07-05 indicator-redundancy scoring change) — each is annotated inline with which historical configuration it reflects. Whenever a scoring/config change is proposed, re-run a fresh full-history `run_backtest()` on this exact ticker/date scope both before and after the change (rather than diffing the "after" result against any number below other than this one) and update this callout once the new configuration ships.

`analysis/backtest.py` vectorizes the full signal logic over historical price data. It does **not** call the live analysis modules — instead it re-implements the scoring logic using `rolling()` and `ewm()` which are inherently look-ahead free. Key functions:

- `compute_signal_history(ticker, start_date, end_date, config)` → DataFrame of daily regime/scores/signals for a ticker. The correlation module is excluded; its 0.10 weight is re-normalised across the other five modules per date.
- `simulate_trades(signal_df, ticker, config)` → list of trade dicts. Entry is gated per date in two steps against `backtest` config: first `allowed_entry_regimes` (see "Regime entry gate" above) — a regime not in the list blocks entry outright, before any signal check — then `entry_signal_overrides` (see "Per-regime entry signal overrides" above) for an allowed regime — STRONG_BUY/STRONG_SELL only by default, or also plain BUY/SELL where the resolved threshold for that date's regime is `"BUY"`. Exit priority (unaffected by either gate): stop loss (entry ± `backtest.atr_stop_multiplier` × ATR) → signal reversal (gated by `trading212_invest.exit_signal` — "SELL" default reverses on SELL/STRONG_SELL, "STRONG_SELL" requires the strong variant only, mirroring `maybe_place_t212_orders()`'s live exit threshold) → per-ticker max holding period via `analysis/regime.py:get_max_holding_days(ticker, config)` → end of data. Every one of these values is read directly from `config` — `simulate_trades()` and `run_backtest()` take no override parameters that would duplicate config.yaml. **`config.yaml` is the single source of truth for backtest behaviour as well as live behaviour** — to test different settings, edit config.yaml and re-run; there is no separate backtest-only parameter set.

**VaR position sizing in the backtest.** Each entry is sized via the same shared formula live trading uses — see "VaR position sizing" below — rather than every simulated trade being equal-weight. `simulate_trades()` calls `analysis/volatility.py:_var_position_size_core()` directly (not `compute_var_position_size_detailed()`, which always reads price history relative to *today* and would look ahead if reused inside a historical walk-forward) with log returns computed from `signal_df["close"]` sliced strictly *before* the entry date (`close_series.loc[close_series.index < dt]`) — guaranteeing no look-ahead. Risk parameters (`var_confidence`, `var_lookback_days`, `max_risk_pct`, `min_position_gbp`, `max_position_gbp`) are read from `trading212_invest` (the backtest has no VaR config block of its own — entry-gating is kept separate under `backtest:` because it can go LONG or SHORT, but risk tolerance is not direction-specific). `account_value_gbp` for the formula is `backtest.starting_capital_gbp` (default £50,000) — a **notional constant**, not a real compounding balance: `simulate_trades()` runs one ticker at a time with no shared cross-ticker portfolio, so this value never grows or shrinks across a run the way a real account would. Falls back to `max_position_gbp × 0.5` (same as live) when fewer than `analysis/volatility.py:_MIN_VAR_RETURNS` (60) prior daily returns exist within the `signal_df` range passed to `simulate_trades()` — since that range is whatever the caller filtered to (`run_backtest()` passes only the requested date range, not the ticker's full history), trades opened very early in a short requested window may see this fallback rather than a true VaR estimate; a genuine data limitation of the current call signature, not a look-ahead risk. Each trade dict carries `position_gbp` (the sized amount) and `pnl_gbp` (`position_gbp × pnl_pct`) alongside the existing `pnl_pct`.
- `run_backtest(tickers, start_date, end_date, config, name)` → creates a `BacktestRun` record, iterates tickers, stores signals and trades in bulk, updates run with summary metrics, returns `run_id`. Only `tickers`, `start_date`, `end_date`, and `name` are genuinely runtime-controlled (date range, ticker scope, run name); everything else — holding limits, ATR multiplier, entry/exit thresholds, regime gate — comes from `config`.
- Query helpers: `get_all_runs()`, `get_run(run_id)`, `get_run_trades(run_id)` (returns DataFrame), `get_run_signals(run_id)`, `delete_run(run_id)`.

**`analysis/regime.py:get_max_holding_days(ticker, config)`** is the shared per-ticker holding-limit helper used by both `simulate_trades()` here and the live T212 exit checks (`brokers/trading212.py`), so backtested and live holding limits always agree for the same ticker. Priority order (first match wins):
1. Ticker in `trading212_invest.ucits_mapping` → `trading212_invest.max_holding_days` (ETF backstop, e.g. 90d — no leverage/swap costs, held longer).
2. Ticker in `watchlist.us_large_cap` → `trading212_stocks.max_holding_days` (individual-stock backstop, e.g. 30d — higher idiosyncratic risk than an ETF).
3. Ticker in the bond-ETF set (`TLT`, `IEF`, `SHY`, `TIP`, `LQD`, `JNK`, `EMB`, `MUB`, `HYG`) → `backtest.max_holding_days_bonds` (e.g. 45d). Several of these (TLT, IEF, HYG, LQD, TIP, EMB, MUB) are also in `ucits_mapping`, so rule 1 fires first for them — only `SHY` and `JNK` (no `ucits_mapping` entry) actually land in this bucket.
4. Ticker ends with `=X` (forex) → `backtest.max_holding_days_forex` (e.g. 5d — swap costs make long holds expensive).
5. Ticker ends with `-USD` (crypto) → `backtest.max_holding_days_crypto` (e.g. 14d — high volatility, shorter cycles).
6. Everything else → `backtest.max_holding_days` (default, e.g. 30d).

`analysis/backtest_metrics.py` is pure-pandas, no DB calls. It accepts a trades DataFrame from `get_run_trades()` and provides:
- `compute_metrics(df)` → full dict: win_rate, avg_return, Sharpe, Calmar, max_drawdown, profit_factor, max_loss_streak. When `df` has a populated `pnl_gbp` column (new backtest runs, post-VaR-sizing; also true of `t212_pnl`-derived DataFrames on the Performance page), the dict additionally gets `has_pnl_gbp: True` plus `total_pnl_gbp`, `avg_pnl_gbp`, `avg_win_gbp`, `avg_loss_gbp`, `profit_factor_gbp` — all computed from `pnl_gbp`, alongside the existing `_pct`-suffixed keys which are always computed from `pnl_pct` unchanged. This is additive by design: older `backtest_trades` rows have no `pnl_gbp` at all, so every existing caller reading only the `_pct` keys sees identical behaviour to before VaR sizing existed.
- `equity_curve(df)` → dated DataFrame (date, equity, drawdown_pct) — a normalised, compounding % return curve (`(1+pnl_pct).cumprod()`), unchanged.
- `equity_curve_gbp(df, starting_capital_gbp)` → dated DataFrame (date, equity_gbp, drawdown_gbp) — a true £ curve (`starting_capital_gbp + pnl_gbp.cumsum()`, **additive** not compounding, since VaR-sized trades only ever risk a slice of the account rather than the whole balance). Returns empty columns (not a flat curve) when `df` has no usable `pnl_gbp`, so callers can distinguish "no £-sized trades" from "a flat £ curve".
- `period_breakdown(df)` → metrics per predefined period (COVID Crash / 2022 Bear / 2023-24 Recovery).
- `module_attribution(df)` → per-module win rate when module agreed vs disagreed with trade direction. **Trend/Volatility/Volume showing a 100%/0% Agreement split is expected, not a bug (confirmed 2026-07-16 investigating an external report that the "Module Detail" table on the Backtest page's Module Attribution tab looked broken).** Pulling real per-trade `backtest_trades` rows for a fresh run confirmed `trend_at_entry`/`volatility_at_entry`/`volume_at_entry` are genuinely `1` on every single entered trade (227/227 checked) and `macro_at_entry` is genuinely `0` on every one — not an aggregation bug in `module_attribution()` itself, which correctly reflects whatever it's given. The cause is the same selection effect already documented under "TRENDING_BULL module weight re-tuning study (2026-07-08)" above: with Macro's backtest score pinned near-constant (latest-observation-only — see "Backtest mirroring" under "Macro module — release-calendar staleness") and weighted ~0.333 of the renormalised TRENDING_BULL total, only trades where Trend/Volatility/Volume all score +1 can mathematically clear the 0.55 `STRONG_BUY` threshold for this regime — so every trade that actually gets entered has those three pinned at +1 by construction, making their agreement% trivially 100 (or 0, for Macro, which never scores +1). `mean_reversion`'s weight (~0.056 renormalised) is too small to force this, so its real per-trade variation (seen varying between `0` and `-1` across the same 227 trades) survives selection — it's the only row in this table with genuine discriminating signal. Confirmed this table has no investor-facing exposure: `module_attribution()` has exactly one call site in the repo (`main.py`'s Backtest page, Module Attribution tab) and neither it nor any of the five `_at_entry` columns appear anywhere in `analysis/robustness.py` (the due-diligence PDF's own Attribution page uses the unrelated `compute_attribution()` sector/asset-class breakdown instead). Live trading is unaffected either way — live `compute_macro()` reads genuinely time-varying per-ticker data, unlike the backtest's latest-observation-only mirror. Fixed by adding an `st.caption()` directly under the Module Detail table explaining the artifact and pointing to Mean Reversion as the one module worth reading here — no scoring, aggregation, or entry-gating logic changed.
- `signal_decay(df)` → hold distribution histogram, exit reason percentages, avg hold by regime.
- `exit_reason_breakdown(df)` → DataFrame indexed by `exit_reason` with `trades`, `win_rate`, `avg_return`, `avg_hold`, and (when `df` has a populated `stop_distance_pct` column — additive, same pattern as `pnl_gbp` above) `avg_stop_distance_pct` — the per-exit-reason performance table rendered on the Signal Decay tab (colour-coded Avg Return, green/red) directly below the exit reason distribution, with a caption noting that MAX_HOLDING outperforming STOP_LOSS suggests the stop may be cutting winners short (widen `backtest.atr_stop_multiplier`), and the reverse suggests the time limit is holding losers too long.
- `walk_forward(df, window_months, step_months)` → list of rolling-window metric dicts.
- `regime_performance(df)` → win rate and return broken down by regime at entry.

Backtest config lives under `backtest:` in `config.yaml` (`starting_capital_gbp`, `max_holding_days`, `max_holding_days_forex`, `max_holding_days_crypto`, `max_holding_days_bonds`, `atr_stop_multiplier`, `walk_forward_months`, `walk_forward_step`). The `_render_backtest_page(config, watchlist)` function in `main.py` implements all six UI tabs. The **Run & Overview** tab's "Configure & Run" section only exposes genuinely runtime-controlled inputs — date range preset, ticker scope, run name — plus a read-only caption summarising the live config values that will be applied (e.g. "Using live config: ETF max hold 90d · Stock max hold 30d · Bond max hold 45d · entry STRONG_BUY · TRENDING_BULL only · stops: ATR 2.0×"). There are no sliders for max holding days or ATR multiplier — those come from `config.yaml` exactly as the live system uses them. The Simulated Trades table adds a `Stop Distance %` column (`—`/blank for backtest_trades rows predating this feature).

### Robustness page

The Backtest page (above) and the Performance page answer "what happened" — win rate, profit factor, drawdown, module attribution. The **Robustness** page answers a different question: "why should these results be trusted at all?" It sits between Performance and Backtest in the sidebar and is a pure read-only due-diligence view — it runs no backtest itself, only reads a previously-completed run selected from a dropdown (same `get_all_runs()` / `get_run_trades()` calls the Backtest page uses).

**`analysis/robustness.py:PARAMETER_FREEZE_DATE = date(2026, 7, 13)`.** Every regime weight, threshold, VaR parameter, and stop multiplier this codebase uses today was tuned and re-tuned against historical data up to this date — see the many "backtest and re-tune" sections elsewhere in this file (the 2026-07-04/2026-07-08 `TRENDING_BULL` weight rebalances, indicator-redundancy removal, the GARCH/HMM removals). Every number the Backtest and Performance pages show for that period is therefore **in-sample**: the config was shaped by seeing that same data. Any T212 Invest position that actually **closes** after this date is genuinely **out-of-sample** — the config was frozen (this date fixed, this file written) before that outcome existed. This is the single fact the whole page exists to keep visible, and the reason a small **persistent badge** (`main.py:_render_out_of_sample_badge()`) is shown at the top of every other page —

```
📊 In-Sample: 2019-04-12 → 2026-07-13  |  ✅ Out-of-Sample: 2026-07-14 → present  |  Live trades: 0  |  Live return: 0.0%
```

— pulling live trade count/return from `analysis/robustness.py:compute_out_of_sample_stats()` (reads `t212_pnl` for rows with `exit_date` strictly after the freeze date). The badge is intentionally omitted only on the Robustness page itself, which already shows this context in full via Panel 1. The icon flips to `⚠️` when the out-of-sample return since the freeze date is negative; otherwise (including the common "zero trades yet" case) it shows `✅`, matching how slowly `t212_pnl` accumulates real closes (see "Feedback learning" above). `BACKTEST_START_DATE = date(2019, 4, 12)` is the matching in-sample start date, shown as the left edge of the badge and Panel 1's "In-Sample Period".

**Four panels**, one per tab, all keyed off the run selected in the dropdown at the top of the page:

1. **Edge Quality** — the freeze-date banner (`⚠ Historical results include parameter optimisation` / `✅ Results after {freeze date} are fully out-of-sample`) plus: Time Invested % (share of business days between the run's first entry and last exit with at least one open position) and its complement Cash Allocation %; Profit Factor and Win Rate (straight from `analysis/backtest_metrics.py:compute_metrics()`); Expectancy per trade, in both % and £ at `backtest.starting_capital_gbp` (`compute_expectancy()` — see below); winner concentration (top 5/10/20 trades' share of total winning `pnl_pct`, `compute_winner_concentration()` — a highly concentrated edge, e.g. the top 10 trades accounting for the large majority of total profit, is a due-diligence red flag even if the aggregate profit factor looks strong); the exit-reason breakdown (count + % of trades per `exit_reason`); and the **Out-of-Sample Monitor** sub-section (freeze date, trades since freeze, return since freeze, and a colour-coded strip: green when the out-of-sample return is positive, red when negative, grey when there are zero out-of-sample trades yet).
2. **Returns Analysis** — an annual returns table (`compute_annual_returns()`: year, trade count, win rate, total return %, £ P&L, and a `vs_sp500_pct` column measured against the hardcoded `SP500_ANNUAL` reference dict — `{2019: 31.5, 2020: 18.4, 2021: 28.7, 2022: -18.1, 2023: 26.3, 2024: 25.0, 2025: 12.0}`, since `price_data` only holds a price-return ^GSPC series, not a dividend-adjusted total-return one); a monthly returns heatmap (`compute_monthly_heatmap()` — year × month grid of summed `pnl_pct` for trades exiting that month, red→white→green diverging colourscale, months/years with no exit shown as `0.0%` rather than a blank/missing cell, since "no trade closed" and "closed flat" should read the same way on this chart); and a dual-axis rolling-12-month return %/Sharpe line chart (`compute_rolling_metrics()`).
3. **Risk Analysis** — drawdown metrics from the run's equity curve (`compute_drawdown_metrics()`: current drawdown from peak, max drawdown — preferring the value already stored on `backtest_runs.max_drawdown_pct`, falling back to the equity curve's own minimum when that's absent — average drawdown across episodes deeper than 1%, and average/longest recovery time in calendar days from trough back to a new equity high); three Calmar ratio variants — lifetime, rolling 36-month, rolling 12-month (`compute_calmar_ratios()`, `Calmar = CAGR ÷ |max drawdown|`, captioned "Higher is better. >1.0 is considered strong."); and a rolling-12-month Sharpe chart with a horizontal reference line at `Sharpe = 1.0` (the same professional-benchmark line, reusing `compute_rolling_metrics()` from Panel 2).
4. **Attribution** — a `[Historical] [Live] [Combined]` radio toggle (`compute_attribution(run_id, config, source=...)`): **Historical** reads `backtest_trades` for the selected run, **Live** reads `t212_pnl` (empty/placeholder until real closes accumulate — never an error), **Combined** concatenates both. For whichever source is selected: ETF vs Stock, Domestic (US) vs International, and asset-class (Equity/Fixed Income/Commodity/Currency) breakdowns, each showing trade count, win rate, % of total profit, and profit factor per bucket; a sector attribution table (see `_ticker_sector_map()` below); and the **Regime Filter Contribution** block (`compute_regime_filter_contribution()` — see next section), a table comparing the run's actual (regime-gated) return/max-drawdown/Sharpe/trade-count against the second, unfiltered pass stored alongside it.

**Sector / asset-class classification (`analysis/robustness.py`).** `_ticker_sector_map(config)` maps every watchlist ticker to a display sector: `us_large_cap` tickers get a real sector from the hardcoded `US_LARGE_CAP_SECTORS` dict (mirroring the sector table in "Adding assets to the watchlist" below — Technology, Healthcare, Financials, Semiconductors, ...); every other ticker's "sector" is simply its watchlist group name, title-cased (e.g. `sector_etfs` → "Sector Etfs", `bonds` → "Bonds") — those groups already function as coarse asset-class buckets, so a second hardcoded map was not worth adding for them. `_asset_class_for()` / `_region_for()` / `_etf_or_stock_for()` layer a few more coarse buckets on top of the same group lookup (a `=X` suffix → Currency, a `-USD` suffix or `crypto`/`crypto_expanded` group → Crypto, `bonds`/`fixed_income_expanded` → Fixed Income, `commodities`/`commodities_expanded`/`real_assets`/`energy` → Commodity, everything else → Equity; `international`/`international_expanded`/`uk_etfs` groups → International, everything else → Domestic (US); `us_large_cap` → Stock, everything else → ETF) — deliberately simple group-name-based heuristics rather than a second per-ticker config field, consistent with how coarse this attribution view is meant to be.

**Regime filter contribution — two-pass backtest.** `analysis/backtest.py:run_backtest()` now runs a second, comparison-only simulation pass per ticker immediately after the normal one, reusing the same already-computed `in_range` signal history (no recomputation of the vectorized scoring — only `simulate_trades()`'s cheap per-date walk runs twice). The second pass uses a `copy.deepcopy()` of the run's own config with `backtest.allowed_entry_regimes` overwritten to admit every `Regime` value — i.e. the regime entry gate (see "Regime entry gate" above) is bypassed while every other setting (`entry_signal`, `entry_signal_overrides`, ATR stop multiplier, VaR/drawdown-scaling parameters) stays identical, so the only variable between the two passes is the regime gate itself. Both passes' `_quick_metrics()` summaries are stored as JSON on the new `backtest_runs.with_regime_filter` / `without_regime_filter` columns (`_store_regime_filter_contribution()`) — only the actually-gated pass's individual trades are persisted to `backtest_trades` or used for the run's own summary metrics; the unfiltered pass is comparison-only and never written trade-by-trade. `analysis/robustness.py:compute_regime_filter_contribution(run_id)` reads both JSON blobs back and computes the deltas (`return_improvement_pct`, `max_dd_reduction_pct`, `sharpe_improvement`) the UI table shows. Runs completed before this feature shipped have no stored comparison — the UI shows a re-run prompt (`available: False`) rather than an error.

**PDF due-diligence export.** A "📄 Download Due Diligence Report (PDF)" button sits above the four tabs (`st.download_button`, `type="primary"`) and calls `analysis/robustness.py:generate_robustness_pdf(run_id, config)`, which renders a standalone **eleven-page** PDF via ReportLab's Platypus framework: Cover, Executive Summary, Edge Quality, Annual Returns, Monthly Returns Heatmap, Risk Analysis, Attribution, Backtest Evidence, Walk-Forward Validation, **Live Validation**, and Methodology & Disclaimers. Every figure in the report is computed live from the same `compute_*()` functions the UI tabs use — `SP500_ANNUAL` and the `SP500_*` risk-adjusted constants (below) are the only hardcoded performance numbers anywhere in it. The brand's dark-navy UI look is deliberately **not** carried over into the PDF: it uses a white background throughout (a dark PDF background prints poorly and reads as unprofessional in most viewers), reserving navy/electric-blue for headers, table header rows, and section titles only — see the `_PDF_*` colour constants near the top of the PDF-export section of the file.

- **Backtest Evidence** — a matplotlib-rendered £ equity curve (`_render_equity_curve_png()`, embedded via `reportlab.platypus.Image`) with the COVID-crash (Feb–Apr 2020) and 2022-bear-market windows shaded and labelled. The label itself is computed live from the run's own trade entries falling in that window — `"Zero new entries"` only when the count is actually zero, otherwise `"N trade(s) entered"` — rather than assumed to always be zero. When a window shows zero entries, a caption below the image (also computed live) reports any carryover positions that closed inside it despite not entering there — see "Due diligence report accuracy fixes" below for why this exists and how it's worded. Below the image: a key-metrics table (trades/win rate/profit factor/Sharpe/max DD/avg hold/total return, read from the run's own stored `backtest_runs` summary plus `compute_edge_quality()`'s profit factor, which isn't itself a stored column) and a matplotlib exit-reason bar chart (`_render_exit_reason_chart_png()`, percentages read live from `compute_edge_quality()`'s exit-reason breakdown).
- **Walk-Forward Validation** — `compute_walk_forward_evidence(run_id, window_months=6, step_months=1)` mirrors `analysis/backtest_metrics.py:walk_forward()`'s own rolling-window logic but additionally computes `profit_factor` per window. This is deliberately a separate function rather than an addition to `walk_forward()` itself: `main.py`'s Backtest page Walk-Forward tab renames that function's returned dicts' keys positionally into a fixed column list, and adding a key there would silently break that rename for an unrelated page. The page opens with a bordered **summary box** (light-blue `#E8F4FD` background, navy border — `compute_walk_forward_summary(wf)`: windows tested, profitable windows (`test_profit_factor > 1.0`), and the resulting success rate, all computed from the same DataFrame the detail table below it renders, never hardcoded) before the full per-window table, and states plainly that this is a rolling-window re-evaluation of the same single backtest, not a genuine train-then-refit split.
- **Live Validation** (new page, inserted between Walk-Forward Validation and Methodology) — `compute_live_validation(run_id)` extends `compute_out_of_sample_stats()` (both now share one row-fetch helper, `_t212_pnl_rows_after_freeze()`, so there is exactly one SQL query for "t212_pnl rows closed after the freeze date") with win rate, profit factor, expectancy, max drawdown, and Sharpe — Sharpe only once the live track record spans at least 12 months (`sharpe_available`), since a Sharpe ratio from a handful of trades over a few weeks isn't meaningful. A colour-coded status box (green/red/grey, matching the persistent badge's own convention) shows days/trading-days since the freeze date. With zero live trades (the expected state for a long time — see "Feedback learning" above) the page shows a placeholder rather than a bare "no trades" message. Once live trades exist, a "Live vs In-Sample Comparison" table appears instead, comparing win rate / profit factor / expectancy / max drawdown between this run's backtest and the genuinely-out-of-sample live figures.

  **Section 2 — Current System Status (2026-07-14).** Below an `HRFlowable` divider and its own "Current System Status" heading, the same page adds a second, deliberately distinct block: the live *operational* state of the system at the moment the report was generated, never conflated with the performance figures in Section 1 above it. `compute_current_system_status(config)` — unlike every other `compute_*()` function in this module, which reads a frozen, completed `run_id` — reads the live DB fresh on every call: `analysis.regime.get_latest_regime_name()` / `is_entry_allowed()` for the regime and whether either T212 automation path currently accepts new entries; `t212_account_snapshot` / `t212_positions_snapshot` (`system_metadata`, the same scheduler-maintained snapshot `main.py` reads — no direct T212 API call) for open position count and account value; `analysis.risk.compute_drawdown_scale_factor()` (only called once an account value is available, since it requires one) for drawdown-from-peak and the drawdown-scaling tier; `get_metadata("last_full_refresh")` for the last analysis run; and a reimplementation of `main.py`'s own `_next_run_for_time()` soonest-of-three-jobs logic (`_next_scheduled_run_label()`) for the next scheduled run — reimplemented rather than imported, since `main.py` imports FROM this module and the reverse import would be circular. Every field is fetched independently inside its own `try/except`, logged and left `None` on failure rather than raising — a missing T212 snapshot renders "Data unavailable — run Refresh Data on Status page" per field, an undetermined regime renders "Unknown", and none of it can block PDF generation. Rendered as a single light-blue (`#E8F4FD`) box in the same style as the Walk-Forward summary box above, with `MIXED` in amber, `TRENDING_BULL` in green, `Blocked` in red, `Allowed` in green, and the drawdown tier coloured `NORMAL`→green / `CAUTION`,`REDUCED`→amber / `MINIMAL`→red — colours chosen to match the severity convention used elsewhere in the report (e.g. the recovery-time warning box below), not a new one invented for this section. A caption below the box reads "Operational status at time of report generation. Reflects live system state, not historical performance." This replaces the old inline regime-note sentence (`_current_regime_note(config)`, removed) that used to sit inside Section 1's zero-live-trades placeholder — that sentence read the identical live regime, but bolted an operational statement onto what should be a purely historical-performance section; it now appears exactly once, in its own dedicated section, never duplicated. Does not add a page break or change the report's page count — both sections render on the same PDF page whenever they jointly fit, same as before.
- **Annual Returns — risk-adjusted S&P 500 comparison.** A raw "outperformed N of M years" count (the page's original design) was removed: it's skewed by how much of each year the system was actually invested (`compute_edge_quality()`'s Time Invested %), overstating S&P outperformance in active years and understating it in cash-holding ones. In its place, a **Risk-Adjusted Benchmark Comparison** table sits below the existing per-year table, comparing the run's own CAGR (`compute_calmar_ratios()["lifetime"]["cagr_pct"]`), max drawdown, Calmar, Time Invested, and Sharpe against four hardcoded S&P 500 buy-and-hold reference constants — `SP500_CAGR_2019_2026 = 15.4`, `SP500_MAX_DRAWDOWN = -33.9` (COVID crash, March 2020), `SP500_SHARPE_2019_2026 = 0.88`, and `SP500_CALMAR` (derived from the first two, not independently hardcoded) — defined next to `SP500_ANNUAL` near the top of the file. The per-year underperformance explanation paragraph is kept (still dynamic, still names whichever years actually underperformed) — only the year-count sentence was removed.
- **Risk Analysis — standalone recovery-time warning box.** "Longest Recovery Time" used to be one row in the drawdown table; it's now additionally elevated into its own amber (`#FFF3CD` background, `#8A5A00` border) warning box immediately below that table, converting the day count to calendar months via `_recovery_days_to_calendar_months(days) = round(days / 21, 1)` (the standard ~21-trading-days-per-month convention) and phrasing it as years-and-months for readability. The box also names the run's own max drawdown and lifetime Calmar ratio inline, choosing "strong" vs. "modest" for the Calmar descriptor based on whether it's ≥ 1.0 rather than assuming it's always flattering.
- **Methodology & Disclaimers — "Known Limitations & Improvement Roadmap" (2026-07-14).** Sits between the existing "Backtest Assumptions and Limitations" bullet list and the contact footer, closing the report on a deliberately confident note rather than pure defensive boilerplate. Entirely static content (`_known_limitations_section()`, `_KNOWN_LIMITATIONS`, `_IMPROVEMENT_ROADMAP`, `_FOUNDERS_NOTE` constants near `_SYSTEM_DESCRIPTION_FULL`) — no `run_id`, no DB reads — since every figure quoted (the 2023 -5.4% vs. S&amp;P 500's +26.3%, February 2024's NVDA-driven +158.5% month, the 74.5% walk-forward window success rate, 56.1% cash-days, etc.) is a specific, already-known finding from earlier work documented elsewhere in this file (the macro `fillna(0)` bug and the TRENDING_BULL re-tuning study in particular), not something recomputed live. Three sub-sections: a six-row **Current Limitations** table (Limitation / Evidence / Impact columns, navy header, bold Limitation cells, italic grey Impact cells, alternating row shading) built as one small `Table` per row rather than a single combined table specifically so each row can be wrapped in its own `KeepTogether` — a `Table` flowable already only ever splits between rows, never mid-row, but per-row `KeepTogether` was requested explicitly and costs nothing extra; a three-tier **Improvement Roadmap** (Near-term / Medium-term / Longer-term, tier headings in `#00C2FF` electric blue, `→`-prefixed bullets); and a **Founder's Note** closing paragraph in the same light-blue (`#E8F4FD`) bordered box style as the Walk-Forward summary box. Each sub-section's heading is paired with only its first row/bullet inside a `KeepTogether` (enough to stop the heading being orphaned alone at the bottom of a page) rather than the whole sub-section, since the full section is long enough that forcing it onto one page would overflow — it flows onto a second physical page naturally via `SimpleDocTemplate`, same as any other over-length section in this report. The one literal `"S&amp;P 500"` in the Evidence column follows the standing `&`-followed-by-a-letter escaping rule immediately below.

Both matplotlib charts render headless (`matplotlib.use("Agg")`, set once at module import time, before `pyplot` is ever imported anywhere in-process) at ~150dpi and are embedded at a fixed size. A full eleven-page report for the largest available run (875 trades) completes in ~6–7 seconds end-to-end — still comfortably inside the UI's implicit budget despite the added page. `main.py:_cached_robustness_pdf(run_id, config)` wraps the call in `@st.cache_data(ttl=600)`, keyed on `(run_id, config)` by Streamlit's own content-based hashing (not object identity) — Streamlit reruns the whole page script on every widget interaction (e.g. switching the Attribution tab's Historical/Live/Combined radio), and without this cache every such interaction would silently re-render the full report from scratch.

**Page numbering, header, and footer.** `_NumberedCanvas` (subclassing `reportlab.pdfgen.canvas.Canvas`) buffers every page via an overridden `showPage()` and only draws the header (`"SP500INVEST"` top-left, `"Due Diligence Report"` top-right) and footer (`"CONFIDENTIAL"` bottom-centre, `"Page X of Y"` bottom-right) once every page has been captured and the total count is known, inside `save()` — the standard ReportLab recipe for a "Page X of Y" footer, since the total page count isn't knowable until the whole `Platypus` story has finished laying out. Several subsections (e.g. the Sector Attribution table and the "Regime Filter Contribution" heading immediately following it, on both the Executive Summary and Attribution pages) are wrapped in `reportlab.platypus.KeepTogether` — without it, a heading can get stranded alone at the bottom of a page with its table pushed onto the next one, which is exactly what an early build of this report did before the wrap was added. Caught by rendering the actual generated PDF to PNG (via PyMuPDF, dev-time only — not a project dependency) and inspecting it visually, not merely by confirming `doc.build()` didn't raise.

**A bare `&` immediately followed by a letter breaks inside `Paragraph()` text — verified empirically, also caught by PNG rendering, not by `doc.build()` succeeding.** ReportLab's Paragraph mini-XML parser treats `&` as the start of an entity reference; `"S&P 500"` was silently rendering as `"S&P; 500"` (the `&` dropped, a stray `;` inserted) everywhere it reached a `Paragraph`, including the Annual Returns page's own title. Confirmed by direct test: `"Methodology & Disclaimers"` and `"Bank & Co"` (space immediately after `&`) render correctly, while `"S&P 500"` and `"R&D spend"` (letter immediately after `&`, no space) both corrupt the same way — the parser can't be reading a space as a possible entity-name start, but happily attempts one from a letter. Plain Table cell strings are unaffected (they don't go through this parser) — only text passed to `Paragraph()`. Fixed by writing `"S&amp;P 500"` everywhere the literal phrase reaches a `Paragraph` (the Annual Returns page title and its risk-adjusted-comparison note); Table cell instances of `"S&P 500"` were already fine and left as plain `&`. Any future PDF page text containing an ampersand followed directly by a letter needs the same `&amp;` treatment — this is a standing hazard, not a one-time fix confined to "S&P".

**Due diligence report accuracy fixes (2026-07-16) — three issues flagged in external review of the 2026-07-14 report, plus one pre-existing bug found while verifying the fix.** All three were investigated against real data before any wording changed, not reworded from the reviewer's description directly — a fresh backtest run against the current production config reproduced 227 trades (matching a documented baseline elsewhere in this file), giving confidence its trade-level detail matches what any given production run would show.

1. **"Zero trades" was inaccurate; "Zero new entries" is correct — the two report sections weren't actually contradicting each other, just measuring different things without saying so.** `_EQUITY_HIGHLIGHT_PERIODS`' label counts by `entry_date` within the fixed calendar window — correctly zero for both COVID and the 2022 bear market, since the regime gate genuinely blocks new entries throughout both — but "trades" implied no trading activity touched the window at all, which was false: a position opened before the window can still exit inside it (the gate only ever blocks new entries, never forces an early exit — see "Regime entry gate" above), and the Annual Returns table (which counts by exit year) correctly showed 1 trade for 2022 and several for 2020. Fixed by renaming the label to `"Zero new entries"` and adding a caption below the chart image, computed fresh from the run's own trades on every report (not hardcoded to any one run's numbers): `"{period_label}: regime gate blocked new entries throughout. N carryover position(s) (opened before the window, closed inside it)."` — or, when a window genuinely has zero trades touching it at all, just the first sentence with no carryover count. `_render_equity_curve_png()`'s return type changed from `Optional[bytes]` to `Optional[tuple[bytes, list[str]]]` to carry these notes back to `_pdf_page_backtest_evidence()`, which renders them as `styles["Footnote"]` paragraphs immediately under the image.

   **The real 2022 trade, verified against a fresh backtest run**: SOXX, entered 2021-11-04 (regime TRENDING_BULL), closed via STOP_LOSS 2022-01-20 (-3.4%, -£64) — matching the flagged report exactly. The regime gate had already flipped away from TRENDING_BULL on 2021-11-26 (roughly 8 weeks before this trade closed) and never sustainably returned; the regime didn't reach an explicit TRENDING_BEAR classification until 2022-01-28, eight days after the trade's exit. Neither "closed before the gate flipped" nor "closed during a confirmed TRENDING_BEAR window" is quite right — the trade closed while the gate had already been blocking new entries for weeks, but before the regime's own classification specifically reached TRENDING_BEAR. Stating both true facts directly (zero new entries; one carryover exit) sidesteps needing to pick either framing.

   **A pre-existing, unrelated bug was found while rendering the fix to PNG for verification** (not just confirming the PDF built without raising, the same "PNG rendering catches what `doc.build()` succeeding doesn't" lesson as the `&`-entity bug above). The COVID and 2022-bear-market window labels were already overlapping illegibly in the currently-shipping report, independent of this wording change — the two windows sit close enough on this chart's ~7-year timescale that centred annotation text longer than roughly two short lines collides with its neighbour's. Fixed by keeping the on-chart annotation to `"Zero new entries"` only (2 lines total, matching the non-zero branch's existing brevity) and moving the fuller "regime gate blocked... N carryover positions..." explanation to the caption below the image, where there's no space constraint.

2. **Annual Returns' `Return %` and `P&L (GBP)` columns don't share a fixed £50,000 basis, and never did — a footnote was added rather than changing the calculation, since both figures were already correct for what they measure.** `compute_annual_returns()`'s `total_return_pct` is the sum of each trade's own individual `pnl_pct` (uncompounded, unrelated to any account size); `pnl_gbp` is the sum of each trade's own VaR-sized position P&L (`position_gbp × pnl_pct`, where `position_gbp` varies per trade with that ticker's volatility at entry — typically far smaller than £50,000). Neither is computed against a flat account value — only `compute_expectancy()`'s `expectancy_gbp` does that, applying the aggregate expectancy % to `backtest.starting_capital_gbp` as a single illustrative "what would one typical trade be worth on a standard account" figure, a genuinely different question than "what actually happened, summed across the real trades that year." This is why e.g. a 2024 return of +310.9% corresponded to only +£7,519, not ~£155k — flagged externally as an apparent non-reconciliation. Added a `styles["Footnote"]` paragraph directly under the Annual Returns table explaining both bases and pointing to Expectancy per Trade for the flat-£50,000 figure used there.

3. **Expectancy formula display now shows the win/loss split as 1dp percentages, not a fraction rounded to 2dp.** The PDF previously rendered e.g. `(7.70% x 0.45) - (2.57% x 0.55) = +2.01%`, but `0.45`/`0.55` was `win_rate_pct` (already rounded to 1dp elsewhere in `compute_expectancy()`) re-derived as a fraction and rounded a second time to 2dp — coarser than the headline `expectancy_pct` (computed from the full-precision `win_rate`) actually used, so hand-multiplying the displayed numbers gave a visibly different answer (+2.05% in the flagged example) than the headline figure. Fixed by displaying `win_rate_pct` and `100 - win_rate_pct` directly as percentages at their existing 1dp precision (e.g. `44.9%`/`55.1%`) instead of re-deriving and re-rounding a fraction — closes the gap from roughly 4bp to roughly 1bp, matching the precision already used everywhere else in the report and well within normal rounding tolerance.

Covered by `tests/test_robustness.py`: the win/loss split rendering as a percentage rather than a bare fraction, the Annual Returns footnote's presence (and that it doesn't retrigger the `&`-entity bug — `"P&amp;L"`, not a bare `"P&L"`, in the two new `Paragraph()` calls this feature added), a carryover trade correctly producing both the direct `_render_equity_curve_png()` return value and the rendered PDF caption text, and a genuinely-zero-trades window correctly omitting any carryover claim. The pitch deck's matching `"0 Trades during COVID & 2022"` stat is not fixed here — no pitch deck file exists in this repository (confirmed by search; no code generates deck content either), so that correction (`"Zero new entries during COVID and the 2022 bear market"`) has to be applied manually wherever the deck itself is maintained.

**Go-to-market context note added (2026-07-29).** The pitch deck and investor explainer now describe a B2B-first go-to-market strategy — licensing the signal engine to wealth managers, IFA networks, and robo-advisors, via API access, a white-label platform, or a research/due-diligence-reporting tier, rather than launching direct-to-retail first — with full FCA authorisation for automated investment advice remaining on the medium-term roadmap, needed only if/when a direct-to-retail channel is pursued later. The DD report's existing roadmap already framed FCA authorisation as "required for B2C retail launch" in its medium-term bucket, which was already consistent with this pivot and needed no change. What was missing was that the DD report never stated the B2B-first strategy itself, so a reader moving from the deck to the DD report saw a fleshed-out B2B narrative in one and no mention of it in the other. A new `_GTM_STRATEGY_NOTE` constant (defined next to `_FOUNDERS_NOTE`) is rendered as a `Paragraph` immediately under the "Improvement Roadmap" heading, inside the same `KeepTogether` block as the Near-term tier's heading and first bullet — stating the near-term B2B motion and its regulatory-classification caveat for the research tier, and pointing to the medium-term FCA line below it. The existing "FCA authorisation... required for B2C retail launch" bullet itself is unchanged. Separately, the Longer-term roadmap's "Institutional white-label API" bullet was reworded (not moved) from "signal engine and regime detection as standalone B2B product" to "a broader, self-service standalone B2B product, beyond the initial near-term pilot licensing motion" — the original wording read as a direct contradiction of the new near-term note (both described the same white-label concept but bucketed 18+ months out), and rewording it to explicitly describe a *later, broader* product than the initial pilot resolves that without moving the milestone itself. No other section (Executive Summary, System Description, Methodology's opening paragraphs) was found to assume a direct-to-retail near-term plan — all are broker/channel-agnostic — so nothing else in the report needed changes.

`reportlab` and `matplotlib` were added to `requirements.txt` for this feature — both are hard dependencies of `analysis/robustness.py` now, imported at module level (not behind a try/except fallback, unlike TA-Lib elsewhere in this codebase), since a PDF-capable environment is expected everywhere this module loads. `pypdf` was added under the `# Testing` section — `tests/test_robustness.py`'s Live Validation page tests extract the generated PDF's text to assert the placeholder vs. comparison-table content actually appears, rather than only checking that generation didn't raise.

**`analysis/robustness.py` functions** (all read-only; `run_id` refers to a completed `backtest_runs` row):

| Function | Returns |
|---|---|
| `compute_edge_quality(run_id)` | dict — everything Panel 1 needs in one call (aggregates the next three plus time-invested/cash-allocation/profit-factor/win-rate/exit-reason breakdown) |
| `compute_winner_concentration(run_id)` | dict — `top5_pct`/`top10_pct`/`top20_pct`, each N largest winning trades' share of total winning `pnl_pct` |
| `compute_expectancy(run_id)` | dict — `(avg_win_pct × win_rate) − (avg_loss_pct × loss_rate)` (`avg_loss_pct` taken as a positive magnitude — `compute_metrics()`'s own `avg_loss_pct` is negative, so this abs()'s it first rather than double-counting the sign), plus the £ equivalent at `backtest.starting_capital_gbp` |
| `compute_annual_returns(run_id)` | DataFrame — year, trades, win rate, return %, £ P&L, and vs-`SP500_ANNUAL` |
| `compute_monthly_heatmap(run_id)` | DataFrame — year × month grid of summed `pnl_pct`, 0.0 (not missing) for months with no exit |
| `compute_drawdown_metrics(run_id)` | dict — current/max/average drawdown, average/longest recovery time (calendar days) |
| `compute_calmar_ratios(run_id)` | dict — `{lifetime, rolling_36m, rolling_12m}`, each `{cagr_pct, max_dd_pct, calmar}` |
| `compute_rolling_metrics(run_id, window_days=365)` | DataFrame — one row per month-end, trailing-window return % and Sharpe (mirrors `walk_forward()`'s trade-level annualised-Sharpe convention rather than a daily-equity-curve Sharpe, since the equity curve here is a sparse step function between trade exits) |
| `compute_attribution(run_id, config, source="backtest")` | dict — sector table + ETF-vs-stock / domestic-vs-international / asset-class bucket breakdowns, for `source` in `"historical"`/`"live"`/`"combined"` |
| `compute_regime_filter_contribution(run_id)` | dict — the with-filter/without-filter comparison described above |
| `compute_out_of_sample_stats()` | dict — `t212_pnl` rows after `PARAMETER_FREEZE_DATE`: `n_trades`, `return_since_freeze_pct`, `pnl_since_freeze_gbp`, `status` (green/red/grey) |
| `compute_walk_forward_evidence(run_id, window_months=6, step_months=1)` | DataFrame — per-window trades/win-rate/profit-factor/max-DD for the PDF's Walk-Forward Validation page (see above) |
| `compute_walk_forward_summary(wf)` | dict — `{n_total, n_positive, success_rate_pct}` for the Walk-Forward summary box, from a `compute_walk_forward_evidence()` DataFrame |
| `compute_live_validation(run_id)` | dict — genuine post-freeze T212 performance (win rate/profit factor/expectancy/max DD/Sharpe) for the Live Validation page — see above |
| `compute_current_system_status(config)` | dict — live regime/entries-blocked/open-positions/account-value/drawdown-tier/last-analysis/next-scheduled-run snapshot for the Live Validation page's Section 2 — see above; takes no `run_id`, reads the live DB fresh on every call |
| `generate_robustness_pdf(run_id, config)` | bytes — the full eleven-page PDF report described above |

Covered by `tests/test_robustness.py`, which seeds `backtest_runs`/`backtest_trades`/`t212_pnl` rows directly into the `db_engine` fixture (unlike `analysis/backtest_metrics.py`'s pure-pandas functions, everything here reads via `analysis.backtest.get_run()`/`get_run_trades()`, so it needs the DB) and checks: winner-concentration top-N math, the expectancy formula's sign handling, that lifetime Calmar always equals its own reported `cagr_pct ÷ |max_dd_pct|`, that out-of-sample stats read exactly zero trades before any post-freeze `t212_pnl` row exists (and ignore rows dated before the freeze), sector mapping for both `us_large_cap` and non-`us_large_cap` tickers, `PARAMETER_FREEZE_DATE`'s value, that `generate_robustness_pdf()` returns a well-formed PDF (`b"%PDF-"` header, non-empty bytes) both for a normal run and when `t212_pnl`/the run's trades are empty, the walk-forward summary box's profitable-window count, `_recovery_days_to_calendar_months()`'s conversion (including the exact `711 → 33.9` spec example), `compute_live_validation()`'s zero-trade and populated-trade cases (and that Sharpe only becomes available past a 12-month span), and — via `pypdf` text extraction on the actual generated PDF — that the Live Validation page shows its placeholder text with no post-freeze `t212_pnl` rows and shows the "Live vs In-Sample Comparison" table once they exist.

### Backtest equity-curve ordering bug — Max Drawdown understated across all runs (2026-07-30)

`_quick_metrics()` (`analysis/backtest.py`) built its equity curve from `all_trades` in ticker-loop insertion order (fixed watchlist order — VOO, IWM, XLE, XLF, XLK...) rather than chronological order, so its `max_drawdown_pct` was a path-dependent statistic computed against a sequence of trades that never actually happened. This value is written once into `backtest_runs.max_drawdown_pct` at run completion and read verbatim by every downstream consumer that doesn't recompute it — the Robustness page's Executive Summary, the Annual Returns benchmark table, the Backtest Evidence key-metrics table, the Live Validation "In-Sample" column, and both columns of the Regime Filter Contribution table (`compute_regime_filter_contribution()`, since `with_filter`/`without_filter` both come from the same buggy `_quick_metrics()` call). `analysis/backtest_metrics.py:equity_curve()` already built the correct curve — sorted by `exit_date`, gap-filled to a daily axis, no backwards P&L realisation — and `analysis/robustness.py:compute_drawdown_metrics()` already called it internally, but then discarded its own correct result in favour of the stored (wrong) value for the Maximum Drawdown field specifically; every other field that function returns (current/average drawdown, both recovery times) was already correct.

Confirmed general across all 7 production runs, not Run #26-specific: stored `max_drawdown_pct` differed from a fresh `equity_curve()`-based calculation by -1.4 to +21.1 percentage points, understating true drawdown in 6 of 7 cases. `compute_calmar_ratios()`/`_calmar_for_slice()` were never affected — they've always built their own correct curve independently, which is why a DD report's Calmar Ratio table could be quietly right while its Executive Summary, two paragraphs above, was wrong for the same run.

**Fix:** `_quick_metrics()` now sorts `all_trades` by `exit_date` before building its equity curve, mirroring `equity_curve()`'s own documented rationale. `compute_drawdown_metrics()` no longer discards its own computed `max_drawdown_pct` in favour of the stored value.

**Data correction:** all 7 existing `backtest_runs` rows had `max_drawdown_pct` backfilled to the corrected value (DB backed up first, dry run reviewed before applying). Run #26's `with_regime_filter`/`without_regime_filter` JSON blobs were also corrected — `with_filter` now matches the scalar column (-35.63%); `without_filter` required re-deriving the unfiltered comparison pass specifically, since its individual trades were never persisted (only its buggy summary was) — corrected to -95.69% (was -68.99%), meaning the regime gate's real drawdown protection is +60.1pp, not the previously reported +39.8pp. Verified the gated pass reproduces Run #26's identical 258 trades / 44.6% win rate / 0.75 Sharpe / +517.8% return — this bug affected reporting only, never trade selection.

Deployed 2026-07-30 17:55 UTC (commit `2419d6b`). Full suite (314 tests) passed; no test had hardcoded a drawdown value tied to the bug.

### GARCH stop loss — why it was removed

A GARCH(1,1)-based dynamic stop (`analysis/volatility.py:compute_garch_stop()`, since removed) replaced the fixed `entry ± atr_stop_multiplier × ATR` stop with a forward-looking 1-day volatility forecast scaled by `sqrt(holding_days)`. It was mirrored into the backtest (`simulate_trades()`, via a monthly-refit cache to avoid O(n²) per-trade refitting) so live and backtested stops calibrated identically.

Two calibration attempts were tried, both underperforming the ATR baseline on every risk-adjusted metric (253 trades / 47.4% win / PF 2.51 / Sharpe 0.80 / -30.3% DD, full-history 2019–present, tradeable assets):

| | ATR stops (baseline) | GARCH flat multiplier | GARCH per-bucket multiplier |
|---|---|---|---|
| Trades | 253 | 239 | 245 |
| Win rate | 47.4% | **59.4%** | 53.1% |
| Profit factor | **2.51** | 2.11 | 2.44 |
| Sharpe | **0.80** | 0.59 | 0.753 |
| Max drawdown | **-30.3%** | -56.7% | -36.3% |
| STOP_LOSS exit rate | 46.6% | 0.8% | 38.4% |

**Attempt 1 (flat multiplier)**: GARCH's `sqrt(holding_days)` scaling has no ATR equivalent, making its stop distance structurally much wider than ATR's for the same ticker — wide enough that STOP_LOSS essentially stopped functioning as a loss-limiting mechanism (2 exits out of 239 trades vs. 118 out of 253 under ATR), nearly doubling max drawdown despite a higher win rate among the trades that did survive to a non-stop exit.

**Attempt 2 (per-holding-period bucket multipliers)**: calibrated three separate multipliers (stocks/bond-ETFs/other-ETFs, since the GARCH/ATR ratio was driven almost entirely by holding-period bucket — median 3.80×/7.85×/6.81× respectively) to bring the GARCH stop back to the same magnitude the ATR stop occupied. This recovered most of the damage (max drawdown -56.7% → -36.3%, STOP_LOSS rate 0.8% → 38.4%) and even improved win rate, but profit factor/Sharpe/max drawdown remained slightly worse than ATR — real ticker-to-ticker dispersion within each bucket (a bucket's *median* ratio still left roughly half its tickers with a stop wider than their true ATR-equivalent) kept the STOP_LOSS trigger rate muted below target.

Removed entirely rather than left disabled, alongside the HMM confidence gate (see "HMM confidence gate — why it's disabled" above — same day, same rationale for not leaving dead, disabled code in place). `analysis/volatility.py`'s ATR-based stop (`_atr_fallback_stop()`) is what remains in production, unconditionally.

### Quantitative technique status

A running summary of every backtest-validated technique touched in this file, its current live/backtest status, and its measured effect versus whatever baseline it was tested against (see the version notes throughout this section for exactly which historical configuration each comparison used — none of these are diffed against the current "Current baseline" above except where noted):

| Technique | Status | Backtest impact vs its own baseline |
|---|---|---|
| VaR position sizing | Live — active | Neutral on %-based metrics (win rate, PF, Sharpe); improves £ risk scaling (position size scales inversely with volatility — see "VaR position sizing" below) |
| Change point detection (CPD) | Live — active | Neutral (not yet mirrored into the backtest's `_vol_scores()` — see "Volatility module"'s Scope note; live-only structural-break score adjustment) |
| TRENDING_BULL weight rebalance (2026-07-04) | Live — active | PF 1.86 → 2.62, Sharpe 0.62 → 0.80, max drawdown -45.5% → -29.5% (see "Regime detection" above for the module-attribution rationale behind the weight changes) |
| GARCH stop losses | **Removed** | Worse than ATR at every calibration attempted (flat multiplier and per-holding-period buckets both fall short — see "GARCH stop loss — why it was removed" above) |
| HMM regime detection | **Removed** | Both the downgrade gate alone and the downgrade+upgrade combination deteriorated every metric versus their baseline (see "HMM confidence gate — why it's disabled" above) |
| Indicator redundancy removal (2026-07-05) | Live — active | Neutral (expected — `mean_reversion` is weighted only 0.05 in `TRENDING_BULL`, the regime nearly all trades enter under, so a scoring-component change inside that module has little room to move overall results — see "Mean reversion module" above) |
| Drawdown-based position scaling (2026-07-06) | Live — active (`drawdown_scaling.enabled: true`) | Mathematically neutral on %-based metrics (only `position_gbp`/`pnl_gbp` can move, never `pnl_pct`); zero of 253 trades triggered scaling in the backtest's per-ticker notional-equity scoping — see "Drawdown-based position scaling" above for why this is a "confirms no regression" check only, not a portfolio-level validation |
| Macro regional grouping (2026-07-07) | Live — active | Backtest: `_macro_scores()` uses latest-observation-only (unchanged mechanism from before this feature), so neutral — see "Macro module regime-weight rebalance (2026-07-08)" below for why the backtest doesn't yet consume the deeper regional history |
| FRED historical backfill (2026-07-08) | Infrastructure — live (`backfill_macro_indicators()`, kept in place) | Not consumed by the backtest yet — feeding it in exposed a `TRENDING_BULL` weight-calibration problem (see next row) rather than validating the backfill itself; the backfill mechanism is otherwise correct and tested |
| TRENDING_BULL weight re-tuning, attempt 1 (2026-07-08) | **Reverted** — `REGIME_WEIGHTS` unchanged, `_macro_scores()` back to latest-observation-only | Every weight tried (macro 0.30/0.15/0.10/0.00) against real historical macro data underperformed the 253-trade baseline (PF 1.86–2.13, Sharpe 0.58–0.66, max drawdown -64.6% to -75.2% vs. baseline's 2.48/0.792/-30.3%) — see "Macro module regime-weight rebalance (2026-07-08)" above for the full four-attempt table and root-cause analysis. A proper full re-tuning study is deferred, not attempted here |
| TRENDING_BULL weight re-tuning, attempt 2: structured attribution study (2026-07-08) | **Inconclusive — `REGIME_WEIGHTS` unchanged** | Split attribution (full-history, genuine per-module variance) from validation (production backtest path, latest-observation-only) after finding 4 of 5 modules had zero score variance under naive latest-only attribution. Both derived candidates underperformed baseline (attribution-derived: PF 2.13, Sharpe 0.667, max DD -43.7%; conservative macro-floor fallback: PF 2.16, Sharpe 0.675, max DD -73.4%). See "TRENDING_BULL module weight re-tuning study (2026-07-08)" above — recommended to repeat once 20+ T212 closed positions exist for attribution on real executed outcomes |
| Sector-rotation MIXED-regime sleeve (2026-07-24) | **Rejected — never adopted, research code deleted** | Approved rule set produced ~zero trades (confluence gate never reached 0.70 for any of 9 sector ETFs, 2019-2026); RS signal alone (gate disabled) showed no edge either — PF 0.98, Sharpe -0.04, 16/29 positive-Sharpe walk-forward windows. See "Sector-rotation MIXED-regime sleeve — researched, not adopted (2026-07-24)" above |
| Mean-reversion MIXED-regime sleeve (2026-07-24) | **Rejected — never adopted, research code deleted** | Real but thin signal (373 trades, PF 1.05, Sharpe 0.16 at zero cost) that turned decisively cost-fragile (Sharpe -0.17 at 5bps/side) and carries a specific unresolved tail risk — all 9 COVID-window trades lost (correlated entries at the crash's onset), while the slower 2022 bear market and 2023-24 recovery were both net positive. £45 total profit / -£149 max drawdown on £10,000 starting capital. See "Mean-reversion MIXED-regime sleeve — researched, not adopted (2026-07-24)" above |

### V2 architecture — phased rebuild, Stage 1 (2026-07-31)

Ten rounds of testing found no validated configuration of the six-module,
single-global-gate V1 architecture described throughout this file (see the
"Quantitative technique status" table above). A phased rebuild followed,
validating one layer at a time rather than assuming complexity upfront:

- **Phase 1 (validated)**: a single signal, no regime classifier at all —
  trailing 12-month total return per ticker, long if positive, flat
  otherwise (the AQR century-paper convention; a 200-day-SMA variant was
  also tested and performed almost as well). Tested against a genuinely
  **point-in-time-gated** universe — 39 US large-cap stocks (real S&P 500
  membership windows from the `fja05680/sp500` public dataset, verified
  against known facts; **no discretionary exclusions** — TSLA, META, and
  GOOGL included from their real membership dates, reversing V1's original
  subjective "geopolitical risk" exclusion) plus 33 broad ETFs. Result: 14
  of 16 historical blocks pass — the reconciled rule (see "Block pass/fail
  formula reconciliation" below): strategy max drawdown no worse than
  1.5× the S&P's own over the same window, AND strategy profit factor
  `> 1.0` over the same window; no per-block CAGR-vs-S&P gate — on both
  train (2007-2019) and test (2020-2026). Confirmed **not** a survivorship
  artifact — re-tested on the point-in-time-corrected universe specifically
  to rule this out, and the result held.
- **Phase 2 (rejected)**: a separate, multiplicative danger/exposure gate
  (S&P below its own 200-day average, and/or VIX ≥ 30) layered on top of
  Phase 1. Every variant underperformed the ungated Phase 1 baseline — the
  same "a broad, frequently-firing overlay suppresses genuine opportunity
  more than it protects" lesson found repeatedly in V1's own research
  history (rounds 4/5/8).
- **Phase 3 (partially useful, not adopted as a fix)**: a rare-firing
  crisis override — S&P single-day decline ≥ 7% (the real NYSE Level 1
  circuit-breaker level) forces a full defensive exit + entry block. Fired
  on only 7 days across the full 2007-2026 window, cost zero measured edge
  anywhere it didn't touch, and gave a small, real improvement in the COVID
  block — but did not clear the hard floor for the GFC-acute block, the one
  thing it was built to fix. A VIX-spike variant was also tested and
  rejected (fired ~15× more often, reproduced Phase 2's over-suppression
  problem).
- **Phase 4**: not attempted (conditional on Phases 1-3 validating
  together, which they didn't as a set).

Two robustness checks were run before any of this was treated as
production-ready: **European (DAX) stocks-only**, using the same
point-in-time discipline (Germany was the only market with a long-enough
verified membership record — FTSE 100/CAC 40/FTSE MIB had none, and EURO
STOXX 50's only public source starts 2014), found no edge and confirmed
the US result isn't trivially portable to a different, smaller market. A
**stocks-only vs. combined vs. ETF-only** breakdown found the ETF sleeve
adds real return but also adds risk (higher drawdown and volatility, no
consistent Sharpe advantage) — a portfolio-construction trade-off, not
resolved here.

**Stage 1 (this section) makes Phase 1 + the Phase-3 crisis override real,
tested, permanent code — `analysis/v2/` — with zero live-order wiring and
zero changes to V1's own behaviour.** `analysis/v2/universe.py` (point-in-time
membership, DB-backed with an in-process cache — see its
`clear_membership_cache()` docstring for why this matters at backtest
scale), `signals.py` (12-month momentum, the sole entry/exit signal),
`crisis.py` (the validated ≥7% circuit breaker), and `backtest.py` (reuses
V1's own ATR stop, VaR position sizing, and `get_max_holding_days()`
unmodified — only the entry/exit decision logic is new).

**V2's stock/ETF universe is deliberately kept out of `config.yaml`'s
watchlist** — adding TSLA/META/GOOGL there would also expose them to V1's
`get_all_tickers()` and `trading212_stocks` automation, which Stage 1 must
not do. `data/database.py:_purge_unlisted_tickers()` was extended with a
deferred import of `analysis.v2.universe.ALL_V2_TICKERS` so these tickers'
price history survives V1's own stale-ticker purge without ever being
added to the watchlist V1 itself reads. **This entire purge mechanism was
removed 2026-08-01** (see "`price_data` purge removed" below) — the
survives-the-purge concern this paragraph describes no longer applies to
anything, since nothing is purged any more.

**Ongoing membership tracking** (not a one-time snapshot):
`analysis/v2/universe.py:refresh_membership_from_source()` re-fetches the
same public dataset, reconstructs every candidate stock's full membership
history from scratch (cheap — a few seconds for the full ~30-year record —
so there's no incremental-diff state to drift out of sync), and reconciles
it against `v2_universe_membership`, detecting both new removals and new
re-additions. Not yet wired to a scheduler job in Stage 1 (a periodic
call — matching the source's own "every couple of months" update cadence —
is the natural next addition once Stage 2/3 make the universe load-bearing
for anything live).

**Reproducibility check (2026-07-31)**: `analysis/v2/backtest.py` run over
the identical 2007-2026 window, universe, signal, and crisis override
already validated in the disposable research clone. Result: **14/16
blocks pass, same two failing blocks (Block 1, Block 2/GFC-acute)** as the
adoption decision; TRAIN CAGR 13.29% vs. 13.22% originally, TEST CAGR
21.95% vs. 21.55% originally — both within half a percentage point.
Verified this gap is not a logic discrepancy: `STOCK_MEMBERSHIP`'s windows,
the ETF ticker list, the momentum-signal computation (including NaN
handling — confirmed `NaN > 0` evaluates to `False` in pandas, not
`NaN`), and every reused execution function are byte-identical to the
research clone. The remaining explanation is that `price_data` is a live,
continuously-updated store — TSLA/META/GOOGL were independently re-fetched
"as of now" for this check, and every ticker's trailing history has kept
accumulating incremental EOD updates in the time since the original
research clone (long since deleted) took its own snapshot. Treated as a
pass: the block-level pattern is exact, and the small CAGR gap sits inside
what a live database's ordinary data-recency drift would be expected to
produce.

**Performance note found and fixed during the reproducibility check**: the
first run took long enough (>15 minutes for one of 16 blocks) that it was
killed and diagnosed rather than left to finish — `is_member()` was
issuing a fresh DB query per ticker per day, an unnecessary bottleneck at
backtest scale (~5,000 days × 73 tickers × 18 runs). Fixed with the
in-process cache mentioned above (invalidated automatically by both
`seed_baseline_membership()` and `refresh_membership_from_source()`); the
full 18-run reproducibility check completed in 74 seconds after the fix.

**Explicitly not done in Stage 1**: no live order placement, no scheduler
wiring, no changes to `analysis/regime.py`, `analysis/macro.py`,
`config.yaml`'s watchlist, or any other file V1 depends on. V1's full test
suite (357 tests before this stage, all passing) passes unchanged after
this stage's `data/database.py` addition. Stage 2 (shadow validation —
running V2 alongside V1 in production, comparing what V2 *would* have
traded against V1's real executions, without placing any V2 orders) and
Stage 3 (cutover) are separate, not-yet-attempted future steps.

### V2 Tier 1 findings re-derived with exact numbers (2026-07-31)

Following the block pass/fail formula reconciliation and the new
"Research reproducibility standard" (top of this file), two of the phased
rebuild's findings that were originally recorded only as prose (their
research code long since deleted, per this project's standing practice)
were re-derived with exact numbers, since both are currently load-bearing:
the crisis override runs in live production code, and the stocks/ETF and
DAX findings back claims already sitting in the DD report and investor
explainer. Phase 2 (rejected) and the VIX-spike crisis-override variant
(also rejected) were deliberately **not** re-derived — neither informs
anything currently live, and the re-derivation cost wasn't judged worth it
for two already-rejected, non-load-bearing findings. They remain
unrecoverable Tier 1 findings, by choice, not oversight.

**Method.** Isolated research clone on the production server (own copy of
`analysis/`, `data/`, `config.yaml`, and a copied — never the live —
database file), read-only against that copy, zero writes to any
production table, deleted after this section was written. Full
2007-01-01 to 2026-07-30 window, all 73 production tickers (33 ETFs + 40
stock candidates), identical signal/crisis-override/VaR-sizing/exit logic
to `analysis/backtest.py`, identical 16-block boundaries
(`make_blocks()`), identical reconciled pass/fail rule (`_block_passes()`:
DD ≤ 1.5× benchmark AND PF > 1.0).

**1. Phase 3 crisis override — baseline (no override) vs. with override,
block by block.** A parallel `simulate_trades()` variant with the crisis
override forced off (no CRISIS_EXIT, no entry-block) was run alongside the
production path, both against the identical 73-ticker universe:

| | With override (= production) | Without override (baseline) |
|---|---|---|
| Total trades | 12,046 | 12,098 |
| Win rate | 44.11% | 43.81% |
| Profit factor | 1.71 | 1.68 |
| Sharpe | 0.565 | 0.547 |
| Max drawdown | -25.45% | -40.78% |
| CAGR | 11.31% | 11.17% |

Raw crisis trigger (S&P single-day ≤ -7%) fired on exactly **7 days**
across the full window, confirming the "fired on only 7 days" figure
directly rather than by memory. Block-by-block, the override changes
**exactly 2 of the 16 blocks** — all other 14 are byte-identical between
the two runs (same trade count, PF, Sharpe, DD to the reported precision):

| Block | Window | With override | Without override | S&P DD | Passes either way? |
|---|---|---|---|---|---|
| 1 (GFC-acute) | 2008-03-22→2009-06-12 | 297 trades, PF 0.78, DD -13.59%, CAGR -3.72% | 303 trades, PF 0.70, DD -16.43%, CAGR -5.75% | -52.58% | **No — fails on PF > 1.0 both ways** (0.78 and 0.70 are both < 1.0) |
| 10 (COVID) | 2019-03-27→2020-06-16 | 857 trades, PF 1.55, DD -23.47%, CAGR 24.08% | 909 trades, PF 1.29, DD -40.78%, CAGR 15.33% | -33.92% | **Yes — passes either way** (DD ≤ 1.5×33.92%=50.88% and PF > 1.0 both hold in both runs) |

This exactly reproduces the qualitative claim — "cost zero measured edge
anywhere it didn't touch" (14/16 blocks byte-identical) and "did not clear
the hard floor for the GFC-acute block" (PF stays below 1.0 with or
without the override) — but sharpens one part of it: the override's
effect on block 10 is a **real, substantial risk reduction** (max
drawdown nearly halves, -40.78% → -23.47%; Sharpe nearly doubles, 0.295 →
0.505) rather than a marginal one, and — a genuinely new observation not
in the original prose — **the override changes zero blocks from fail to
pass**: both blocks it touches (1 and 10) have the identical pass/fail
verdict with or without it, so the headline "14/16 blocks pass" figure
would be unchanged even with the override removed entirely. Its value is
risk-quality inside an already-passing window (COVID), not the pass/fail
tally. This is consistent with, not contradictory to, "partially useful,
not adopted as a fix" — confirmed, not overturned, by the exact numbers.

**2. Stocks-only vs. combined vs. ETF-only breakdown.** Same 2007-2026
window, same signal/crisis logic, three ticker subsets:

| | ETF-only (33) | Stocks-only (40) | Combined (73, = production) |
|---|---|---|---|
| Trades | 3,962 | 8,084 | 12,046 |
| CAGR | 4.97% | 9.98% | 11.31% |
| Max drawdown | -11.85% | -20.10% | -25.45% |
| Annualised volatility (daily £ equity curve) | 2.89% | 4.30% | 4.85% |
| Sharpe | 0.407 | 0.667 | 0.565 |
| Profit factor | 1.67 | 1.72 | 1.71 |

Adding the ETF sleeve to a stocks-only portfolio (stocks-only → combined):
CAGR rises (+1.33pp, "adds real return" confirmed), max drawdown worsens
(-20.10% → -25.45%, "higher drawdown" confirmed), annualised volatility
rises (4.30% → 4.85%, "higher volatility" confirmed). One point is more
precise than the original prose: Sharpe does not merely show "no
consistent advantage" — it **falls**, 0.667 → 0.565, a real decline on
this exact measure, not just a neutral wash. Directionally this sharpens
rather than contradicts the original "portfolio-construction trade-off,
not resolved here" framing.

**3. DAX (European stocks-only) — corrected 2026-07-31, superseding both
the original "found no edge" prose and this file's own first re-derivation
attempt earlier the same day.** The first re-derivation attempt (this same
day) concluded no free point-in-time DAX membership dataset existed,
having checked only Wikipedia's "Former DAX components" table (correctly
identified as an incomplete "Selected Key Changes" excerpt) and one
GitHub aggregator (2023-onward only, too short). **That conclusion was
wrong.** A follow-up check — prompted by a user-supplied set of specific,
independently-verifiable DAX membership dates that didn't match anything
in the first attempt's sources — found STOXX (the DAX index provider
itself) publishes a complete, official **"Historical Index Compositions
of DAX Equity Indices"** document, freely available at
`stoxx.com/document/Indices/Common/Indexguide/Historical_Index_Compositions.pdf`:
every DAX addition/removal since the index's 30 December 1987 launch,
with exact dates, through a 22 June 2026 edition. Spot-checked directly:
SAP's 1995-09-18 addition and Infineon's 2000-06-19 addition /
2009-03-23 removal / 2009-09-21 re-addition all matched independent web
sources to the exact day. This is at least as authoritative as the
`fja05680/sp500` source used for the US universe (it's the index
provider's own primary record, not a third-party aggregation) — the
"no genuine point-in-time DAX dataset exists via free sources" claim in
this file's first attempt is retracted.

A real `DAX_MEMBERSHIP` table for all 40 candidates was built directly
from this document's text (see the deleted research clone's
`dax_check_pit.py` for the exact per-ticker windows) and used to re-run
the backtest with genuine point-in-time gating — the same `is_member()`
role `analysis/universe.py` plays for the US universe. The correction is
large: **20 of the 40 tickers** had a real membership window meaningfully
narrower than "always eligible since 2007" — including several where the
first attempt's current-constituents approach was fabricating well over
a decade of false eligibility (`G24.DE` Scout24 joined 2025-09-22;
`G1A.DE` GEA Group joined 2025-09-22; `HNR1.DE` Hannover Re was a member
only 2009-03-23→2009-09-20 and 2022-03-21→present; `RHM.DE` Rheinmetall
joined 2023-03-20; `MTX.DE` MTU Aero Engines joined 2019-09-23; the seven
2021-09-20 DAX-30→40-expansion additions `AIR.PA`/`BNR.DE`/`PAH3.DE`/
`QIA.DE`/`SHL.DE`/`SY1.DE`/`ZAL.DE` were not members at all before that
date). Total trades dropped from 7,379 (first, wrong attempt) to **5,548**
(point-in-time corrected) — a 25% reduction, exactly the direction and
rough magnitude survivorship bias would predict.

Same signal/crisis-override logic (crisis trigger still keyed to the
S&P's own ≥7% single-day decline, matching what the live system actually
uses), benchmarked against `^GDAXI`, now with real point-in-time gating:

| | Strategy (point-in-time) | DAX (`^GDAXI`) benchmark |
|---|---|---|
| Train (2007-2019) CAGR | 6.71% | 5.41% |
| Train max drawdown | -14.45% | -54.77% |
| Test (2020-2026) CAGR | 10.06% | 10.37% |
| Test max drawdown | -18.56% | -38.78% |
| Full period CAGR | 6.22% | 7.11% |
| Full period max drawdown | -18.56% | -54.77% |
| Profit factor (train / test) | 1.46 / 1.41 | — |

**Blocks passed: 12/16 (75%)** — unchanged from the first (wrong) attempt
at the aggregate level, and the same 4 blocks fail both ways (0, 1, 9, 12)
— but the absolute numbers behind that tally are materially more modest:
the strategy no longer clearly beats the benchmark's CAGR (it does in
train, essentially ties in test, and trails slightly over the full
period), while still cutting max drawdown dramatically in every window
(e.g. full period -18.56% vs. the benchmark's own -54.77%).

**Final, corrected conclusion — neither "found no edge" nor "beats the
benchmark" is accurate; this is the trustworthy version, not a third
guess.** The strategy on point-in-time-gated DAX stocks provides **real,
substantial risk reduction** (drawdown roughly a third of the benchmark's
own in every period) but **not clearly better absolute return** —
CAGR is comparable to, not decisively better than, buy-and-hold DAX. This
is a genuinely different and more moderate finding than either prior
version: better than the original unquantified "no edge" (12/16 blocks
pass, a similar hit rate to the validated US universe's 14/16), and
substantially weaker than this file's own first re-derivation's apparent
CAGR outperformance (which is now confirmed to have been a survivorship-
bias artifact of testing only today's surviving constituents — removing
that bias cut the CAGR advantage from clearly-ahead to roughly-tied).
Whether the *original*, now-unrecoverable investigation used this same
STOXX source or something else can't be determined — its code is deleted
and no exact numbers were ever recorded — but this corrected re-derivation
is independently verified against real, dated, cross-checked data and
should be treated as the reference version going forward.

Not committed as a regression test — this is a one-off research
reproduction (matching the original findings' own one-off nature), not
new production logic; nothing in `brokers/`, `data/scheduler.py`, or
`analysis/backtest.py` changed as a result of this section.

### V2 live execution and cutover (2026-07-31)

Stage 2 (shadow validation) was deliberately skipped in favour of going
straight to cutover, on the reasoning that `trading212_v2.live` stays
`false` throughout (T212 **demo** account, no capital risk) — so the risk
worth managing wasn't financial, it was a bug reaching real (if play-money)
order placement and contaminating the demo track record V2 is about to
start building. Three pre-launch checks ran first, gated by an explicit
"stop and report" standard rather than pushing through on any surprise.

**Pre-launch Check 1 — V2 membership refresh wired into the scheduler.**
`data/scheduler.py:_v2_membership_refresh_job()` (21:15 UTC weekdays, 15
minutes ahead of the 21:30 EOD job) calls
`analysis.v2.universe.refresh_membership_from_source()` inside its own
try/except, registered via `_scheduler.add_job(..., id="v2_membership_refresh")`.
Covered by `tests/test_scheduler.py` confirming actual schedule registration
(day_of_week/hour/minute), not just that the underlying function works
standalone.

**Pre-launch Check 2 — crisis override verified against real historical
data, in the production code path.** The 7 real ≥7% single-day S&P declines
in the full price history (2008-09-29, 2008-10-09, 2008-10-15, 2008-12-01;
2020-03-09, 2020-03-12, 2020-03-16) were located directly from production
`^GSPC` data via `analysis/v2/crisis.py:compute_raw_trigger()`. Replaying
2020-03-12 (S&P −9.51%) through the actual deployed `is_crisis_day()` /
`is_entry_blocked()` confirmed both fire `True` that day, and tracing the
cooldown window day-by-day confirmed it correctly re-extends through
2020-03-16 (itself a second ≥7% day) and clears exactly 5 trading days
later on 2020-03-24 — the mechanism behaves exactly as designed against
genuine market data, not just synthetic test fixtures.

**Pre-launch Check 3 — zero open V1 positions, confirmed at the source.**
Cross-referencing `t212_invest_orders` initially showed an apparently
unbalanced XLF BUY/SELL count; investigating the actual rows found this
was an already-resolved, explicitly-documented "ghost position reconciled
manually" artifact (a cancelled SELL followed by a manual reconciliation
row), not a real open position. Confirmed directly against T212's live
demo API (not just the cached snapshot): `get_positions()` returned zero
positions, `get_account_summary()` showed £0 invested / £50,000 all cash.

**A real, unrelated V1 bug was found as a side effect of Check 3/mapping
verification, not fixed (V1 is being retired, not patched).**
`trading212_stocks`'s `us_large_cap` tickers were mapped to T212 using the
bare research ticker (e.g. `"AAPL"`) as the T212 instrument ticker — but
T212's actual live instrument tickers for direct US-listed stocks are
suffixed `_US_EQ` (e.g. `AAPL_US_EQ`; confirmed via `GET
/equity/metadata/instruments` returning zero matches for any bare ticker
and a match for every one of them under the `_US_EQ` suffix). Confirmed
zero stock-path orders have ever actually been placed (`trading212_stocks`
never had a qualifying TRENDING_BULL + STRONG_BUY signal fire), so this bug
was latent and never surfaced in production — but `trading212_stocks.enabled`
was `true` at the time of discovery, meaning the very next qualifying
signal would very likely have failed with a T212 "instrument not found"
rejection. Left as-is and undocumented-as-fixed in the code itself (V1's
own code is frozen, not extended, per this cutover) — recorded here as a
finding, not a change.

**V2's own instrument mapping, verified live per-ticker before use, not
just trusted from config.** All 33 V2 ETF tickers reuse
`trading212_invest.ucits_mapping`'s existing, already-verified T212 IDs
directly (same instrument, no reason to re-derive). All 40 V2 stock
tickers were checked individually against the live instruments endpoint:
38 resolve via the plain `{TICKER}_US_EQ` convention; **`META` resolves to
`FB_US_EQ`** (T212 never renamed the instrument's own ticker after the
Facebook→Meta rename — confirmed via the instrument's `name`/`shortName`
fields, "Meta Platforms"/"META") and **`RTX` resolves to `UTX_US_EQ`** (T212
retains the pre-merger "UTX" United Technologies ticker post-Raytheon
merger — confirmed via `name`/`shortName` "RTX"/"RTX"). `GOLD` (Barrick
Mining) also resolves live (`GOLD_US_EQ`) despite having an empty
membership window in `analysis/v2/universe.py:STOCK_MEMBERSHIP` — kept in
the mapping for completeness (matches the module's existing "fixed
candidate, permanently zero-eligible" pattern) but will never actually
place an order. The full, verified mapping lives in
`config.yaml:trading212_v2.instrument_mapping`.

**New tables — `V2InvestOrder` / `V2Pnl` (`v2_invest_orders` / `v2_pnl`),
not a shared extension of V1's `T212InvestOrder` / `T212Pnl`.** V1 is
retired, not extended, so V2's track record needed to be unambiguous from
the start — the same reasoning that made the corrected backtest a new run
rather than an overwrite of an existing one (see "Backtest equity-curve
ordering bug" above). `V2InvestOrder` gained a `stop_price` column V1's own
table no longer has: V1's live path has **no** stop-loss enforcement at all
(removed with the GARCH stop — see "T212 order fields (post-GARCH-removal)"
above), but V2's validated backtest actively enforces an ATR stop as its
3rd exit priority, so unlike V1, V2's live execution has to replicate that
— `stop_price` is fixed at entry (via the same `_resolve_entry_stop()`
helper the backtest calls) and compared against the latest stored close on
every exit check.

**`brokers/trading212_v2.py`** mirrors `brokers/trading212.py`'s structure
and safety invariants (retry/back-off HTTP client reuse, duplicate-SELL
guard, "never raises" contract, FIFO P&L reconstruction, order-status
polling for 404-on-fill) but with a genuinely new entry/exit decision core,
built to mirror `analysis/v2/backtest.py:simulate_trades()` **exactly**,
not a redesigned approximation:

- **Entry** (all three required): momentum signal on
  (`analysis.v2.signals.compute_signal_series()`), ticker currently a
  genuine index member or an ETF (`analysis.v2.universe.is_member()`), not
  inside the crisis entry-block window (`analysis.v2.crisis.is_entry_blocked()`).
- **Exit priority**, checked in this exact order — read directly from
  `simulate_trades()`'s own code, not assumed: `CRISIS_EXIT` (raw
  single-day trigger) → `INDEX_REMOVAL` (stocks only) → `STOP_LOSS`
  (fixed-at-entry ATR stop vs. latest close) → `SIGNAL_OFF` (momentum
  turned non-positive) → `MAX_HOLDING` (`analysis.regime.get_max_holding_days()`).
- **Position sizing reuses `trading212_invest`'s VaR config verbatim**
  (`var_confidence`/`var_lookback_days`/`max_risk_pct`/`min_position_gbp`/
  `max_position_gbp`), for both the ETF and stock legs of V2's universe
  alike — because that is what `simulate_trades()` itself reads
  (`var_cfg = config.get("trading212_invest", {})`), regardless of
  `is_etf`. Matching this exactly, not introducing a V2-specific VaR
  block, is what "exactly mirrors the validated backtest" means for
  sizing. `get_max_holding_days()` is reused unmodified too — V2's 33 ETFs
  and 37 of its 40 stocks already match a `trading212_invest.ucits_mapping`
  / `watchlist.us_large_cap` entry from V1 and get that ticker's proper
  ETF/stock-specific holding limit; `TSLA`/`META`/`GOOGL` (deliberately
  kept out of V1's watchlist — see Stage 1 above) fall through to the
  generic `backtest.max_holding_days` default, exactly as they did inside
  the validated backtest run itself.
- `poll_v2_placed_orders()` — a V2-specific counterpart to
  `poll_placed_orders()`, reusing `brokers.trading212._historical_fill()`
  directly (already generic — takes a client and order ID, no V1-table
  dependency). Without this, a `PLACED` row in `v2_invest_orders` would
  never resolve to `FILLED`, and P&L reconstruction (which only ever pairs
  `FILLED` rows) would never see a closed V2 position.

Wired into `data/scheduler.py`: `maybe_place_v2_orders()` alongside the two
V1 calls inside `_run_analysis_pipeline()` (every scheduled analysis run);
`maybe_exit_v2_positions()` and `poll_v2_placed_orders()` alongside V1's
inside `_trade_alert_job()` (hourly). Each call is in its own try/except,
matching the "one path's failure never blocks another's" convention every
other call in these two functions already follows. Covered by
`tests/test_scheduler.py` (call verification, not just registration) and
`tests/test_v2_execution.py` (21 tests: entry-gate combinations, all five
exit-priority orderings tested in isolation against each other, duplicate-
order guards, `max_open_positions`/`max_total_deployed` gates, the
never-raises contract, and FIFO P&L reconstruction including idempotency).

**Reproducibility check (Step 4) — two independent passes, both clean, run
directly against real production data on the server before any config flag
changed.** Rather than reimplementing the live decision logic a second time
(which would only prove the reimplementation agrees with itself), both
checks called the actual shared primitive functions
(`analysis.v2.signals`/`universe`/`crisis`) the same way
`brokers/trading212_v2.py` does, and compared against
`analysis/v2/backtest.py`'s own computed values for the same tickers/dates:

1. **Entry-gate inputs** (`sig_on`, `is_member`, `crisis_now`,
   `entry_blocked`) compared across all 73 V2 tickers × the 5 most recent
   real trading dates (365 combinations) — **0 mismatches**.
2. **Exit-precedence logic** applied to every ticker `simulate_trades()`
   itself left with an open position as of the latest real date (52 of 73
   tickers) — every one independently re-evaluated as "no exit," agreeing
   exactly with what the validated backtest's own walk-forward concluded —
   **52/52 agreements, 0 mismatches**.

Run from a temporary, read-only checkout on the server (symlinked to the
real `database/` directory, no writes) — the live services were never
touched until both checks passed cleanly.

**Cutover.** `config.yaml`: `trading212_invest.enabled` and
`trading212_stocks.enabled` both flipped to `false` (code kept intact, not
deleted — every call site's own `enabled` early-return is what actually
stops it acting); `trading212_v2.enabled` flipped to `true`.
`trading212_v2.live` stays `false`, matching `trading212_invest`/
`trading212_stocks`'s own hard block — this task does not touch the
demo/live distinction. Mutual exclusion between the two systems has no
separate code-level lock beyond these three flags: since
`maybe_place_t212_orders()`/`maybe_exit_t212_positions()`/
`maybe_place_stock_orders()`/`maybe_exit_stock_positions()` all early-return
the moment their own section's `enabled` is `false`, V1 becoming fully
inert is a direct, verified consequence of the flag flip, not a new
invariant to maintain elsewhere.

### V2 first-cycle deployment cap overshoot (2026-07-31)

The first real cycle triggered immediately after cutover placed **32 BUY
orders in a single pass**, deploying **~74% of the account (£36,754 of
£49,858)** — well above the configured 50% `max_total_deployed` cap, and
above the 20-position `max_open_positions` cap too (25 of the 32 had
already resolved to FILLED positions by the time this was caught).

**Root cause.** `maybe_place_v2_orders()`'s deployed-capital and open-position
checks (`if total_val > 0 and (invested / total_val) >= max_deployed_pct:
return` and the `current_open >= max_open` check) are both evaluated
**once, before the ticker loop starts** — not re-checked after each order
placed inside the loop. This is not a new bug introduced by V2: it is the
exact same pattern `brokers/trading212.py`'s own
`maybe_place_t212_orders()`/`maybe_place_stock_orders()` have always used.
V1 never exercised this failure mode in practice — its STRONG_BUY-in-
TRENDING_BULL entry gate rarely produced more than a handful of
simultaneous qualifiers from a cold start. V2's 12-month-momentum signal is
structurally different: a rising market can leave a large fraction of a
73-ticker universe simultaneously "long" at once, so the very first run
from a completely empty account (zero pre-loop deployed-%/position-count
to gate against) walked through most of the qualifying tickers before
either cap had any effect.

**Status as of this writing:** the T212 demo account was manually reset to
a clean £50,000 (confirmed live via the T212 API — `totalValue: 50000.0`,
`investments.currentValue: 0`, 0 open positions). The cap-enforcement code
fix itself had not yet been applied when this section was first written —
checked directly against both the local repository and the deployed server
(`brokers/trading212_v2.py`'s `max_total_deployed`/`max_open_positions`
checks were still the original pre-loop-only version in both places).

**Fix.** `maybe_place_v2_orders()`'s BUY-phase loop now re-checks both caps
on every iteration, using running `current_open`/`invested` totals updated
after each successful BUY (`current_open += 1`, `invested += est_value_gbp`)
— the exact same running-total pattern the loop already used for
`free_cash -= position_gbp`. The pre-loop checks are unchanged and still
run first (a fast path that avoids entering the loop at all when the cap
is already blown from the account's state before this run started); the
per-iteration checks are what actually close the gap, breaking the loop
the moment either cap is crossed by an order placed earlier in the *same*
run. Verified with three new tests in `tests/test_v2_execution.py`: a
5-simultaneous-qualifier cold start correctly stops at exactly 2 orders
under `max_open_positions: 2`, correctly stops at exactly 3 orders under
`max_total_deployed: 0.15` (each mocked order sizing to 5% of account
value), and a generous-caps control case confirms the fix doesn't
over-suppress — all 5 qualifiers still get an order when neither cap binds.

### Live pricing bug, exception isolation, and ETF mapping audit (2026-07-31)

Investigating why the account showed only ETF fills and no stock fills after
the V1 removal (see "V1 removal" section above) surfaced two independent,
real bugs in the live order-placement code — both predating the V1 removal
itself, inherited verbatim from the original `brokers/trading212_v2.py` —
plus a full identity audit of all 33 ETF mappings.

**Bug 1 — `get_instrument_price_gbp()`'s "live quote" path never worked, for
any instrument.** The function's original primary path read
`currentPrice`/`price` from `get_instruments()`'s response
(`/equity/metadata/instruments`) — but that endpoint never carries a price
field at all, for any instrument, stock or ETF (confirmed live against
`AAPL_US_EQ`, `CAT_US_EQ`, and `I50Dl_EQ`'s raw records — none have it).
Confirmed against T212's own public API documentation directly (not
guessed): the complete endpoint set is `/equity/account/summary`,
`/equity/orders*`, `/equity/positions`, `/equity/metadata/instruments`,
`/equity/metadata/exchanges`, and `/equity/history/*` — there is no
quotes/prices endpoint for an instrument not currently held, and
`/equity/orders/market` only accepts a share `quantity`, never a notional
value (T212's own docs: "the API currently only supports placing orders by
QUANTITY... value-based ordering exists on other Trading 212 platforms but
is not currently supported via the API"). This is a permanent, external
constraint, not something fixable by calling a different endpoint.

Every order placed to date had actually been sized off the *fallback* path
(the research ticker's own close price from `price_data`, assumed USD,
converted via GBPUSD) — silently, with no visibility into how far that
proxy price was from the T212 instrument's real per-share price. For a
single-stock mapping (e.g. `AAPL` → `AAPL_US_EQ`) this is a reasonable
proxy — same underlying share, same price. For an ETF/UCITS wrapper it is
not: wrapper providers pick an arbitrary NAV-per-share convention
independent of any US-listed equivalent, confirmed live — VOO trades around
$681/share on its US listing; its UCITS wrapper `I50Dl_EQ` trades around
£6.26/share, a ~109x difference with nothing to do with currency. XLF's
position (see Bug/mapping section below) was the case that made this
visible, but the same proxy-price mechanism sized *every* one of the first
13 live orders — most of them just happened to size to something
plausible-looking rather than obviously broken.

**Fix.** `get_instrument_price_gbp()` now has three tiers: (1) a genuinely
live quote from `get_positions()`'s `currentPrice`, if the instrument is
currently held; (2) a persisted last-known-price cache
(`system_metadata`, key `last_known_price_gbp:{t212_ticker}`, refreshed
every time tier 1 succeeds, valid up to 30 days old) — without this, fully
exiting any ETF position would make that instrument permanently unpriceable
for a future re-entry, since tier 1 has no data once a position closes;
(3) the research-ticker-close fallback, but **only** when `is_etf=False` —
for an ETF/UCITS wrapper with no live position and no usable cache entry,
the function now returns `0.0` (refuses to guess) and logs an explicit
`ERROR`, rather than silently sizing off a proxy that can be wrong by
orders of magnitude. Callers already treat `0.0` as "skip this ticker" (the
existing `if quantity <= 0: continue` guard), so this is a real, immediate
behavioural change: **a brand-new ETF mapping that has never once been
filled cannot be auto-traded** — there is no way to price it in advance
given T212's API, and the only way to bootstrap the cache is a deliberate,
manually-verified seed trade. This is an inherent consequence of the fix,
not a gap in it.

**Bug 2 — zero per-ticker/per-position exception isolation.** Neither the
BUY loop (`maybe_place_orders()`) nor the exit loop (`maybe_exit_positions()`)
wrapped their per-ticker computation (`_ticker_state()`,
`get_quantity_for_gbp()`, `get_instrument_price_gbp()`) in a try/except —
only the actual `place_market_order()` call had its own. An unhandled
exception on any one ticker (e.g. a T212 rate-limit exceeding the 3-retry
budget — observed directly, repeatedly, during this investigation) silently
killed every ticker after it in `instrument_mapping`'s dict order, with
nothing logged. This exactly explains an observed live incident: a real
cycle placed 13 BUY orders (`VOO` through `VGK`, positions 1–19 of 73 in
dict order, skipping non-qualifying tickers normally in between) and then
produced *zero* orders for all 54 remaining tickers — 14 more ETFs and every
one of the 40 stocks — even though a live re-check the same day found 26 of
those 40 stocks genuinely signal-eligible. Re-running `_ticker_state()` for
all 54 tickers the same day raised no exception for any of them, so the
specific transient trigger from that one run couldn't be reproduced or
named — but the structural gap (confirmed present in both the pre- and
post-V1-removal code) is the most parsimonious explanation for the exact
observed cutoff pattern, and was real regardless of what triggered it that
day. Fixed: both loops now wrap their per-item body in try/except, logging
which ticker/position and what exception, then `continue`-ing to the next
item — a `break`/`return` from a legitimate stopping condition (caps
exhausted, insufficient cash, a hard order failure) is control flow, not an
exception, so it still propagates through exactly as before.

**CAT's apparent zero-quantity bug — not a real bug, a test artifact.**
An earlier diagnostic pass sized CAT against an arbitrary flat £500 test
target and got `quantity=0` (floored from `500/600.96 ≈ 0.83`), read at the
time as a possible silent-failure case. Re-checked against the *real* VaR-sized
target the live system actually computes for CAT (£2,404.51, not £500):
`raw_qty ≈ 4.00`, quantity floors to 4 — a normal, correct, non-zero order.
No separate fix was needed; the original finding was an artifact of the
diagnostic's own arbitrary test amount, not a defect in the sizing code.

**ETF mapping audit — all 33 `instrument_mapping` ETF entries checked
against live T212 metadata (name, ISIN, currencyCode), 2026-07-31.**

- **`XLF` was mapped to a genuinely wrong instrument.** `STZXs_EQ` is
  "SPDR MSCI Europe Financials (Acc)" — European financials, not the US
  Financial Select Sector Index `XLF`'s momentum signal is computed
  against. Inherited verbatim from V1's `ucits_mapping` and never caught
  until a real live position exposed it (the position's real notional was
  roughly £8,000 — nowhere near the ~£25 the corrupted `estimated_value_gbp`
  record implied, since that record's own CHF-conversion had also silently
  failed to `0.0`; see Bug 1). The correct instrument does exist and was
  found live: **`SXLFl_EQ`** — "SPDR S&P U.S. Financials Select Sector
  (Acc)", ISIN `IE00BWBXM500`, USD-denominated — the same
  SPDR/State-Street Select Sector family every sibling `XL*` mapping
  already uses (`XLB`/`XLE`/`XLI`/`XLP`/`XLU`/`XLY` are all exact-match
  "SPDR S&P U.S. ... Select Sector (Acc)" instruments in USD). The old
  `STZXs_EQ` position (59 shares) was closed live via a real market SELL
  order (realised P&L −£5.46, logged in `invest_orders`/`pnl` with
  `exit_reason = MAPPING_CORRECTION` to distinguish it from a normal
  signal-driven exit) and `instrument_mapping.XLF` now points at
  `SXLFl_EQ`. Since this is a brand-new-to-us instrument with no live
  position and no cache entry, it is — correctly, per Bug 1's fix —
  untradeable by the automated pipeline until a deliberate seed trade
  establishes a real reference price.
- **`XLK` flagged, not remapped** at the time this audit was written —
  `XDWTl_EQ` is "Xtrackers MSCI World Information Technology" — global
  technology exposure, not the US-only Technology Select Sector Index `XLK`
  represents. A real scope mismatch, materially less severe than XLF's
  (still genuinely the technology sector, just broader geography than
  intended) — left in place pending a decision on whether to remap.
  **Resolved 2026-07-31** — see "XLK mapping resolution and ETF pricing
  lockout audit" below for the remap and the closed position.
- **12 of the 33 ETF mappings had a wrong declared `currency` in
  config.yaml** relative to the instrument's real, live `currencyCode`
  (`VOO`, `TLT`, `HYG`, `SLV`, `SOXX`, `EWJ`, `EEM`, `EWZ`, `INDA`, `FXI`,
  `SKYY` — plus `XLF`'s old mapping, moot now that it's been replaced).
  These didn't corrupt live pricing (the fixed `get_instrument_price_gbp()`
  reads currency from the live API directly, not from this declared
  field) but `_log_order()`'s `currency_code` falls back to it when the
  live lookup fails, so all 12 were corrected to match T212's real
  `currencyCode` for defence in depth.
- **The other 20 mappings** (`AMLP`, `EMB`, `EWG`, `GLD`, `IBB`, `IEF`,
  `IWM`, `KBE`, `LQD`, `MUB`, `PDBC`, `TIP`, `VGK`, and the six exact-match
  SPDR sector siblings above) all check out — either an exact match to the
  same provider/index, or (for a handful, e.g. `EEM`→a JPMorgan active EM
  fund, `SOXX`→an Amundi semiconductor fund) a different provider tracking
  the same underlying asset class/region, the same kind of substitution
  already documented and accepted elsewhere in this file (see "Ticker
  substitution" below).

**Track-record caveat.** The 13 filled orders from the 2026-07-31 cutover
cycle (see "V2 first-cycle deployment cap overshoot" above) were placed
under code that predates every fix in this section — the unpatched pricing
fallback (Bug 1), no exception isolation (Bug 2), and XLF's wrong mapping
were all live at the time. That cycle also did not run through either
systemd service (no trace in either service's journal for the exact
placement window), strongly suggesting it was placed via an ad-hoc manual
script during the cap-enforcement work rather than a genuine scheduled run.
**Treat those 13 orders as pre-production/shakedown activity, not the start
of V2's real track record.** The first cycle to run after this section's
fixes are deployed, verified, and `trading212_invest.pause_new_entries` is
lifted is the actual first cycle of the trustworthy, ongoing live record —
`analysis/robustness.py`-style out-of-sample accounting for this system (if
reintroduced) should treat that as the true starting point, not 2026-07-31's
initial cutover.

**Safety switch during this work.** `trading212_invest.pause_new_entries: true`
was added to `config.yaml` as soon as Bug 1/Bug 2 were found, blocking
`maybe_place_orders()`'s BUY phase entirely (any `run_type`) while both were
root-caused and fixed; `maybe_exit_positions()` was explicitly unaffected
throughout, since a stop-loss/signal-off/crisis exit is safety-critical and
must never be paused. Removed once this section's fixes were deployed and
verified.

Covered by new tests in `tests/test_execution.py`: live-position pricing,
ETF refusal with no cache, stock fallback still working, cache hit/staleness
behaviour, cache refresh on every live read, and per-item exception
isolation in both the BUY and exit loops (a simulated failure on one ticker
must not prevent the next ticker in the same run from being processed).

### Backtest page restored, re-validation, DD report, and Daily Briefing rebuild (2026-07-31)

`analysis/backtest.py` (the vectorized simulation engine, `precompute_ticker`/
`simulate_trades`/`run_candidate`/`compute_metrics_fixed`/`equity_curve_gbp`/
`walk_forward_pf`, all renamed from `analysis/v2/backtest.py` during the V1
removal and consolidation) had no UI page pointed at it any more — the old
Robustness page that used to trigger and display backtest runs was deleted
along with the rest of V1's `analysis/robustness.py`. Before building
anything new on top of it, the engine itself needed re-proving: `get_max_holding_days()`
was rewritten (now `analysis/universe.py:get_max_holding_days()`), sizing/
execution code was consolidated, and most of the surrounding module names
changed during the V1→V2 cutover — none of that had been re-validated against
the original adopted result since.

**Re-validation, run directly against production data via SSH** (local dev's
DB only holds ^GSPC back to 2016-07-06; production holds it from 2007-01-03
— see "Historical data extension" above). Full battery: momentum signal,
crisis override, the current live point-in-time-gated universe, train
(2007–2019) / test (2020–2026), all 16 blocks, concentration check,
walk-forward. Confirmed **current live universe composition directly**
(not assumed from before the refactor): 33 ETFs + 40 stock candidates = 73
total candidates, 39/40 stock candidates genuinely S&P 500 members today
(GOLD is the sole permanent non-member by design), 72 live-tradeable.

**Result: 12,046 total trades (2007–2026). TRAIN CAGR 13.23%, TEST CAGR
22.39%, 14/16 blocks pass, full-history concentration check negligible
(11.31% raw vs 11.28% ex-best CAGR), walk-forward 82.1% (183/223 positive
windows).** These closely match the numbers already on record in this file
("V2 architecture — phased rebuild" and "V2 live execution and cutover"
above: 14/16 blocks, TRAIN 13.22–13.29%, TEST 21.55–21.95%) — within the
same tolerance this file's own prior reproducibility check accepted as
ordinary live-database drift (more incremental EOD data accumulated since),
not a regression.

**Every figure above (including walk-forward) is a live, date-dependent
snapshot, not a fixed constant — expect it to drift by small amounts
day to day.** `run_full_validation()`'s `end` parameter defaults to
`datetime.date.today()` when the caller doesn't pin one, and neither the
Backtest page nor the DD report generator ever does — so any two runs on
different calendar days include a different amount of live-database EOD
data, even a single extra day's worth. Confirmed directly (2026-07-31):
pinning `end=2026-07-30` reproduces this section's own 82.1% (183/223)
exactly, alongside its other stats (12,046 trades, TRAIN 13.23%, TEST
22.39%, 14/16 blocks); the very same call with `end=2026-07-31` (one
calendar day later — the date a freshly-generated DD report used the
same day) gives **83.0% (185/223)**, with TEST CAGR nudging from 22.39%
to 22.41%. Both are correct, verified outputs of the identical, unchanged
`walk_forward_pf()` methodology (6-month rolling windows, stepped
monthly, ≥3 trades per window, `profit_factor > 1.0` pass bar) — the gap
is not a bug or an unexplained discrepancy, it's two of 223 windows near
the very end of the data (the thinnest-sample, least-mature ones, where a
handful of trades' PF sits closest to the 1.0 threshold) flipping from
fail to pass once one more day of price history landed. **Whichever
figure a fresh run produces on the day it's generated is the correct
one for that day** — there is no single "true" walk-forward percentage
to reconcile toward; a DD report or investor explainer should always
carry whatever `run_full_validation()` returns when it's actually
generated, not a number copied from a previous day's run.

**Honest discrepancy, reported rather than smoothed over.** The aggregate
14/16 count matches exactly, but the *specific two failing blocks* do not:
this reproduction failed **Block 0** (2007-01-01–2008-03-22) and **Block 2**
(2009-06-12–2010-09-02), whereas the prior record names "Block 1, Block
2/GFC-acute" as the two failures — and this run's own Block 1
(2008-03-22–2009-06-12, the actual acute crash window) **passes**
(strategy CAGR -3.72% vs. S&P -25.30%, strategy max DD -13.59% vs. S&P
-52.58%). Root cause, as far as it can be determined: the original
per-block pass/fail formula lived only in a disposable research clone,
deleted once its finding was adopted — this project's own standing
practice for exploratory code (see the many "deleted after the finding
below rather than left in the repo" notes throughout this file). What
exists now (`analysis/validation.py`) is a reconstruction of that rule from
this file's own prose ("strategy CAGR ≥ S&P's own, max drawdown ≤1.5× the
S&P's own, no block loses more than 20% of capital"), not a byte-identical
copy verified against the original. The underlying trade-simulation engine
itself (signals, crisis override, universe gating, VaR sizing, exit
priority) is unchanged and reproduces the same trade count/CAGR pattern —
this is a reconstructed-pass/fail-rule discrepancy, not a trading-logic
regression. Flagging this explicitly rather than treating "14/16 either
way" as good enough, per this project's own reproducibility standard.

**Resolved 2026-07-31 — see "Block pass/fail formula reconciliation
(2026-07-31)" below.** The reconstructed formula quoted above was wrong in
two independent ways (a missing profit-factor floor, and an extraneous
per-block CAGR-vs-S&P gate that was never actually part of the bar); once
corrected, the run reproduces the documented result exactly,
block-for-block, not just in aggregate.

**`analysis/validation.py`** is the new shared module — `run_full_validation(config, start, end)`
and `universe_snapshot(as_of=None)` — used by both the Backtest page and the
DD report generator, so neither can ever show a different number for the
same run. `analysis/backtest.py` gained `compute_cost_sensitivity(trades,
starting_capital, cost_bps_list)` (round-trip cost in bps subtracted from
every trade's return, recomputed via the existing `compute_metrics_fixed()`
— the corrected additive-£, exit-date-sorted methodology, never the old
cumprod approach — see "Backtest equity-curve ordering bug" above).

**Backtest page (`main.py:_render_backtest_page`)** — new sidebar page.
Shows the live universe snapshot, a "Run Full Validated Battery" button
(session-state cached — a full run takes roughly a minute), then TRAIN/TEST
metrics vs. the real S&P 500 over the same windows, the full-history
concentration check, the 16-block pass/fail table, the transaction-cost
sensitivity table (0/10/25bps), walk-forward stats, the £ equity curve, and
a "Generate Due Diligence Report (PDF)" trigger.

**`analysis/dd_report.py`** — the new due diligence report, deliberately
much shorter than V1's eleven-page `analysis/robustness.py` document (V2's
story is one signal, one universe-gating rule, one rare-firing override, not
six modules' worth of attribution panels). Generated live from
`run_full_validation()`'s own result dict — never hand-written and left to
go stale, the same principle the old report followed. Six sections:
Executive Summary, Methodology, Validated Results (16-block table,
concentration check, cost sensitivity, walk-forward, equity curve), and
Known Limitations. Reuses `equity_curve_gbp()`'s corrected additive-£
methodology throughout and the established `&amp;`-escaping rule for every
`Paragraph()`-rendered "S&P" (see "Due diligence report accuracy fixes"
above for the ReportLab entity-parsing bug this guards against — verified
here too via `pypdf` text extraction, not just `doc.build()` succeeding).

**Known limitations stated directly, not omitted**: (1) GFC-acute-scale
crashes remain a known, accepted gap — the crisis override helps but can't
rescue a position opened before a crash slow enough to build up before the
≥7% trigger fires; (2) universe breadth is survivorship-exposed at the
delisted/bankrupt level — point-in-time membership correction can't reach a
company whose data no longer exists anywhere; (3) whether the edge
generalizes beyond US mega-caps is unresolved, not disproven — one negative
German (DAX) result on a thinner evidence base; (4) US concentration is a
deliberate, currently-unaddressed choice, not an oversight.

**Daily Briefing rebuild (`main.py:_render_briefing_page`)** — the old page
was thin by necessity (V1's rich six-module output doesn't exist in V2's
single-signal model). Added, on top of the existing regime/crisis badge and
open-position cards: a **crisis-proximity strip** (today's actual S&P move
vs. the -7% trigger, in percentage points of remaining room — not just an
active/inactive flag; amber once within 2pp) via the new
`output/performance.py:get_crisis_status()`; **entry date and current
signal state** added directly to each open-position compact card (previously
only ticker/day-count/P&L); a **full ticker eligibility/momentum table**
covering every ticker in the live universe, not just open positions —
promoted from a collapsed expander to default-open, with a "Held" marker
column and sorted long-signal-first; and a **next membership refresh**
countdown (21:15 UTC weekdays, reusing the existing `_next_run_for_time()`
helper).

Covered by `tests/test_validation.py` (pure `_cagr_dd_for_series()` cases,
`universe_snapshot()` shape, and a mocked `run_full_validation()` exercising
the train/test split and block wiring against small synthetic trades — a
full production-scale run is a multi-minute operation validated via the SSH
reproducibility check above, not replayed verbatim in the unit suite),
`tests/test_dd_report.py` (well-formed PDF, the `&`-entity regression check,
known-limitations and block-count text presence), new `tests/test_backtest.py`
cases for `compute_cost_sensitivity()`, and new `tests/test_performance.py`
cases for `get_crisis_status()` (calm day, active trigger day, mid-cooldown).

No changes to any order-placement path (`brokers/trading212.py`,
`data/scheduler.py`) — this task is read-only against price/signal data and
UI-only in main.py.

### Due diligence report UI discoverability fix (2026-07-31)

`analysis/dd_report.py:generate_dd_report_pdf()` was built, tested (direct
function calls, rendered-PDF text extraction), and extended (the ETF
execution-venue limitation entry) multiple times the same day — but a
real user of the deployed app reported no way to find it. **Investigated
before assuming either way**: `main.py` did have a genuine call site
("Download Due Diligence Report" heading, "Generate Due Diligence Report
(PDF)" button, then a `st.download_button` once generated) — it was not
missing entirely, the way Step 1 of this task's own instructions
anticipated as one possibility. The problem was placement: the whole
block sat at the very bottom of the Backtest page, after Train/Test
Split, Full History & Concentration, the 16-block table, Transaction-Cost
Sensitivity, Walk-Forward, and the Equity Curve — six sections below
where a user's eye would land after clicking "Run Full Validated Battery"
— with no signal anywhere else in the app (sidebar, another page, a
caption before the button) that generating a report was even possible.
Reaching it required an expensive, unrelated-looking 1-2 minute
prerequisite (the battery run) with no upfront indication that doing so
would unlock anything.

**V1 precedent, confirmed via CLAUDE.md's own "PDF due-diligence export"
section**: the old Robustness page's equivalent button "sits above the
four [detail] tabs" — prominent, immediate, not buried below the report
content it summarises. V2 deliberately consolidated Robustness-equivalent
content into the one Backtest page rather than reviving a separate
sidebar page (see "Backtest page restored..." above — "V2's story is one
signal... the report only needs methodology [and] validated results"),
so adding a whole new sidebar page here would cut against that
established consolidation, not follow it. The fix instead moves the
button **within** the Backtest page, matching V1's own placement
principle: the "Download Due Diligence Report" section now renders
immediately after the "Last run: ..." caption — the first thing visible
once a battery result exists, before any of the six detail sections —
and the pre-run info message (shown before the battery has ever been
clicked) now states directly that running it also unlocks a downloadable
report, so a user who specifically wants the PDF knows what to do before
waiting through the page.

**Verified via `streamlit.testing.v1.AppTest`, navigating the way a real
user would** — not just confirming `generate_dd_report_pdf()` runs in
isolation (already covered by `tests/test_dd_report.py`): set
`at.session_state["page"] = "backtest"` (the same key the sidebar radio
itself writes to), ran the script, clicked the "Run Full Validated
Battery" button (`run_full_validation` mocked to a small, fast, synthetic
result — the real function is a multi-minute operation, confirmed
separately via SSH in earlier sections), confirmed the "Download Due
Diligence Report" heading and its button render immediately at that
point, clicked "Generate Due Diligence Report (PDF)" (the real,
unmocked `generate_dd_report_pdf()` — so this exercises actual PDF
generation, not a stub), and confirmed both a genuine PDF (`b"%PDF-"`
header, well-formed bytes) lands in the same `st.session_state` key the
`download_button` reads from, and the download button element itself
renders with the expected label.

**`main.py` had never been imported in this test suite before, for a
documented reason** (see `output/performance.py`'s own module
docstring): importing it runs `_start_scheduler_at_startup()` at module
level, which starts a real APScheduler background thread — including
live T212 API calls — as a side effect of the import itself. Making this
test safe required patching `data.scheduler.start_scheduler` (the
*source* module's attribute) **before** anything triggers `main.py`'s own
`from data.scheduler import start_scheduler` import statement — patching
`main.start_scheduler` directly doesn't work, because that name is a
frozen snapshot bound at import time, and by the time `main` exists as an
importable target to patch, the import (and the real scheduler start)
has already happened. `data.scheduler` itself has no risky module-level
code, so patching its attribute first, then only afterward touching
anything under `main.`, keeps the one-time-ever import of `main.py` in
this test process safe. Confirmed directly: the real `start_scheduler()`
body (traceable via a `system_metadata` heartbeat write it makes) ran
once when this ordering was wrong, and stopped appearing once fixed.

Covered by `tests/test_main_ui.py` (new — 3 tests): the Backtest page
renders via the same session-state page key the sidebar writes to, the
DD report section appears immediately after a battery run rather than
requiring a scroll past six other sections, and the full click-through
path produces a real, well-formed, downloadable PDF.

### Real £ comparison — V2 vs. buy-and-hold S&P 500 (2026-07-31)

The existing train/test CAGR comparison is accurate but not intuitive.
Added a genuine "what would £50,000 actually have become" comparison,
computed by the validation engine itself and shown on the Backtest page
and in the DD report — not derived externally and pasted in, matching
the same "one shared function, not two implementations that could
quietly diverge" lesson the walk-forward discrepancy earlier the same
day already established.

**S&P leg reuses the identical `^GSPC` series and window boundaries the
existing CAGR benchmarks already use** — `gspc_close_full` from
`get_price_df("^GSPC", ...)`, the same object `train_sp`/`test_sp`'s
`_cagr_dd_for_series()` calls already read. This is a **price-only**
index level, no dividend reinvestment (`^SP500TR`, the total-return
variant, is not tracked anywhere in this project) — deliberately the
same convention every other S&P figure in this file already uses, not a
second, differently-sourced or differently-conventioned benchmark
running alongside it. `analysis/validation.py:_sp500_ending_balance_gbp()`
computes the £ ending balance directly from the window's first/last
price (`starting_capital * end_price / start_price`) rather than
re-deriving it from an already-rounded CAGR percentage, so it matches
the CAGR figure's own math exactly rather than compounding a second,
lossier calculation on top of it.

**V2's leg reuses `equity_curve_gbp()`** — the same additive-£
methodology behind every other £ figure this project reports
(`compute_metrics_fixed()`, the DD report's own equity curve, the Run
#26 real-return reconciliation) — via
`analysis/validation.py:_strategy_ending_balance_gbp()`, which just reads
the final row of that curve for whichever trade subset (train or test)
it's given.

**`analysis/validation.py:compute_gbp_growth()`** ties both together and
is called once, inside `run_full_validation()`, using its own
already-computed `gspc_close_full`/`train_trades`/`test_trades`/
`starting_capital` — train and test are kept as two separate legs in
the returned dict, never blended into one full-period number (blending
would hide how much of any headline figure comes from the in-sample
training window, the same discipline the V2 total-return task earlier
today insisted on). The result lands under a new `"gbp_growth"` key in
`run_full_validation()`'s return dict, alongside (not replacing) the
existing CAGR/drawdown figures — both the Backtest page (`main.py`, a
new "£ Invested — Real Ending Balance" block directly under the
Train/Test CAGR section) and the DD report (`analysis/dd_report.py`,
two new rows in the Executive Summary's existing train/test table) read
this same key from the same `result` dict — neither computes its own
version.

Like every other figure `run_full_validation()` returns, this reflects
whatever `end` date the function naturally resolves to (today, live) —
never a hardcoded date — so it drifts by the same small day-to-day
amounts as TRAIN/TEST CAGR and walk-forward already do, for the same
reason (see the annotation on this file's "Backtest page restored..."
section above).

**Verified against real production data** (2026-07-31, `end` pinned to
match the day's other confirmed figures): £50,000 invested in TRAIN
(2007–2019) reached **£227,036** under the strategy vs. **£114,033**
buy-and-hold S&P 500; in TEST (2020–present), **£188,662** vs.
**£114,149** — consistent with the already-confirmed TRAIN CAGR 13.23%
(vs. S&P 6.55%) and TEST CAGR 22.39% (vs. S&P 13.38%).

Covered by `tests/test_validation.py` (new — 5 tests: the S&P leg matches
a direct price-ratio calculation, the strategy leg matches
`equity_curve_gbp()`'s own final balance, a no-trades leg stays flat at
starting capital, and `run_full_validation()`'s own shape test extended
to cover the new key), `tests/test_dd_report.py`, and `tests/test_main_ui.py`
(one new test each, both feeding the exact same deliberately distinctive,
not-plausibly-real numbers into their sample `result` fixtures and
confirming those exact figures appear in the rendered PDF / Backtest
page respectively — proving both consumers read from `result` rather
than recomputing their own version).

### Weekly summary email — Sunday 09:00 UK local time (2026-07-31)

Re-adds the one piece of V1's original `output/briefing.py` scope with a
genuine V2 equivalent (daily narrative, macro context, opportunity
building, VIX-spike/central-bank alerts were all signal/regime-driven
content tied to the six-module confluence system and had nothing to carry
forward). Sends a calm, factual weekly status email: every open position
(ticker, entry date, current signal state, unrealised P&L), account total,
the week's combined realised+unrealised P&L, positions closed since the
last summary with their exit reason, whether the crisis override fired,
and a one-line system-health confirmation.

**Own trigger path, deliberately separate from the T212 error-alert
cooldown.** `brokers/trading212.py:_send_t212_error_email()`'s cooldown
(`_T212_ERROR_EMAIL_LAST_SENT_KEY` / `notifications.email.alert_cooldown_hours`,
see "notifications.email enabled + cooldown wired up" below) exists to
stop a *persistent error* from flooding inboxes — a completely different
problem from "send exactly one scheduled email a week." Sharing a gate
between the two would mean a real error alert could go unsent because the
weekly summary "used up" the cooldown window, or vice versa.
`output/briefing.py:send_weekly_summary_email()` calls `_smtp_send()`
directly — same `notifications.email.enabled` master switch every caller
already respects, but no cooldown check at all, since the Sunday-only
schedule is itself the only gate this path needs. The one piece of state
it tracks, `last_weekly_summary_sent` (`system_metadata`), is written only
on a genuinely successful send and is used purely to bound *content* —
"what happened since last time" — never to decide *whether* to send.
Verified directly: a fresh `t212_error_email_last_sent` cooldown timestamp
does not block a weekly send, and a weekly send does not touch that key at
all (`tests/test_briefing.py`).

**Genuinely UK local time, not a fixed UTC hour — confirmed directly
against APScheduler's real `CronTrigger`, not assumed from documentation.**
`data/scheduler.py:_weekly_summary_job()` is registered with
`CronTrigger(day_of_week="sun", hour=9, minute=0, timezone="Europe/London")`
— the trigger's own `timezone` parameter, independent of
`start_scheduler()`'s scheduler-wide default (`tz`, always `"UTC"` for
every other job). Checked `get_next_fire_time()` directly for a January
Sunday (GMT) and a July Sunday (BST): the January fire time is 09:00 UTC,
the July one is 08:00 UTC — the same 9am UK wall-clock time, correctly
shifted by DST, not a value that would drift an hour off across the
boundary the way a hardcoded UTC hour would.

**Content sources — every figure reuses an existing, already-tested data
path, none of it re-derived.** Open positions: `output/performance.py:
get_brief_cards()`, the identical source and shape the Daily Briefing and
Performance page already use, sorted alphabetically by ticker (never by
status or P&L — see "Decision-relevant, tone-neutral context on open
position cards" below for why ranking by risk was removed from the UI in
the first place; the same principle applies here). Closed positions:
`output/performance.py:get_pnl_rows()`, filtered to `exit_date >=
last_weekly_summary_sent` (or 7 days ago on the very first run), each with
its stored `exit_reason` (`STOP_LOSS` / `SIGNAL_OFF` / `MAX_HOLDING` /
`CRISIS_EXIT` / `INDEX_REMOVAL`) stated plainly. Account total:
`t212_account_snapshot`'s own `totalValue` (`system_metadata`, the
scheduler-maintained snapshot — no direct API call from this path).
Crisis override: `analysis/crisis.py:compute_raw_trigger()` over the
window, reporting the specific date(s) if it fired and stating plainly
when it didn't (never omitting the field either way). System health:
`last_full_refresh` recency (3-day threshold — comfortably covers an
ordinary Friday-EOD-to-Sunday-morning gap without needing the Monday-
specific weekend exemption the Status page's own OVERDUE badge uses,
since this check only ever runs on a Sunday) plus both error-metadata keys
this project has accumulated (`invest_error`, set by
`brokers/trading212.py`'s order-placement error path, and
`t212_invest_error`, set by `data/scheduler.py:_fetch_t212_snapshot()`'s
own — a pre-existing, not-yet-reconciled duplication found while building
this, not something this task changes) checked together, either one being
non-empty reported as an unresolved error.

**Tone — same standard as the Daily Briefing/Performance page rebuild,
carried forward deliberately.** One neutral colour (`#334155`) used
unconditionally in the HTML body regardless of any figure's own sign — a
losing week renders with exactly the same styling as a winning one, never
red/green. No ranking by risk or performance anywhere (alphabetical
throughout). No call-to-action language — closed positions are named with
their exit reason as a plain record of what happened, not flagged for
attention. The Friday-close caveat is stated directly in both the text
and HTML bodies so a Sunday-morning read doesn't land as unexpectedly
stale.

Sample generated against real production data (2026-07-31): 0 open
positions, 1 position closed this week (`XLF`, `MAPPING_CORRECTION` — the
instrument-remapping close from earlier the same day, not a signal-driven
exit), account total £49,795.71, crisis override did not fire, system
healthy.

Covered by `tests/test_briefing.py` (19 new tests): window-start
resolution (defaults to 7 days on a first run, uses the stored timestamp
otherwise), closed-position window filtering, realised+unrealised P&L
arithmetic, account-total parsing (present and absent), the crisis-fired
and crisis-did-not-fire cases (both explicitly checkable, never silent),
the dual-key error check, the full forbidden-word tone scan on both the
text and HTML bodies, the one-colour-regardless-of-sign HTML check, the
alphabetical (not risk/P&L-ranked) ordering guarantee, and the three
separate-trigger-path tests described above. `tests/test_scheduler.py`
(5 new tests) covers job registration (including the genuine
`Europe/London` timezone on the trigger object itself, not just its cron
fields), the direct GMT/BST `CronTrigger` resolution check, and the job
function's call-through/never-raises behaviour.

### Block pass/fail formula reconciliation (2026-07-31)

Closed the discrepancy flagged immediately above, before anything built on
top of `analysis/validation.py` (the Backtest page, the DD report) was
trusted any further. Standard: find the literal original criteria first,
rule out the cheapest hypothesis (block boundaries) before touching the
formula, then fix whichever side is actually wrong.

**Step 1 — the literal original criteria, extracted verbatim.** Searched
every commit that ever touched `CLAUDE.md` or `analysis/v2/`/`analysis/`
for the phased-rebuild's 16-block finding — there is exactly **one**
place it was ever written down, in the "V2 architecture — phased rebuild,
Stage 1" section's Phase 1 bullet (commit `afa7794`, unchanged since):
*"Result: 14 of 16 historical blocks pass (CAGR ≥ S&P 500, drawdown ≤ 1.5×
S&P's own, no crisis-adjacent block materially loses money)."* No exact
date table was ever committed anywhere (block dates were always described
by number, never listed), no PF value was ever named, and "materially
loses money" was never quantified. The reproducibility check three
paragraphs later names *"Block 1, Block 2/GFC-acute"* as the two failing
blocks, again with no date table. **This is the entire literal record —
the disposable research clone that actually implemented and ran this
check was deleted once its finding was adopted (this project's standing
practice for exploratory code), and no more detailed version of the
criteria exists anywhere in git history.** This is itself worth stating
plainly: the "internal record" instructed to be treated as ground truth
in this task does not, in fact, contain a fully-specified formula — only
a paraphrase — which is precisely how the earlier reconstruction went
wrong without anyone being able to catch it at the time.

**Step 2 — block boundaries: match exactly, ruled out as the cause.**
`analysis/backtest.py:make_blocks()` (`boundaries = pd.date_range(start,
end, periods=n_blocks + 1)`) is byte-for-byte unchanged since the very
first V2 commit (`afa7794`) — confirmed directly via `git show
afa7794:analysis/v2/backtest.py`, not assumed. Both the disposable
clone's reproducibility check and `analysis/validation.py`'s
`VALIDATION_START` use the identical `2007-01-01` anchor, and the
production reproduction run used the identical `2026-07-30` end date
`tests/test_v2_backtest.py`'s own `test_make_blocks_returns_requested_
count_contiguous()` was written against from day one. The resulting 16
block dates (pinned in `tests/test_validation.py` as
`_EXPECTED_BLOCK_BOUNDARIES`) are therefore the only dates `make_blocks()`
could ever have produced for this window — a boundary mismatch was not
the cause.

**Step 3 — the formula itself was wrong, in two independent ways.**
`compute_metrics_fixed()`/`equity_curve_gbp()` (the additive-£,
exit-date-sorted DD/CAGR methodology from the round-10 fix — see "Backtest
equity-curve ordering bug" above) were confirmed unchanged since
`afa7794` too (`analysis/v2/backtest.py` imported both directly from
`analysis.backtest_metrics`, which already had the round-10 fix by the
time V2 was built — the round-10 bug lived in a completely separate,
now-deleted function, `_quick_metrics()`, that never touched
`backtest_metrics.py` at all). So the DD/CAGR *calculation* was never the
problem. The bug was in the *comparison* built on top of it, and pulling
the full per-block metrics (not just CAGR/DD) for a fresh production
reproduction exposed it directly:

| Block (0-idx) | Window | Strategy PF | Strategy DD | S&P DD | Old formula (CAGR≥S&P + DD≤1.5× + no >-20% loss) | Reconciled formula (DD≤1.5× + PF>1.0) |
|---|---|---|---|---|---|---|
| 0 | 2007-01-01→2008-03-22 | 0.10 | -19.04% | -18.64% | FAIL (CAGR) | **FAIL (PF)** |
| 1 | 2008-03-22→2009-06-12 (GFC-acute) | 0.78 | -13.59% | -52.58% | **PASS** | **FAIL (PF)** |
| 2 | 2009-06-12→2010-09-02 | 1.34 | -18.61% | -15.99% | FAIL (CAGR) | **PASS** |
| 3–15 | — | all >1.0 | — | — | PASS | PASS |

Two independent errors, each confirmed against the real reproduction data
rather than assumed:
1. **A per-block "strategy CAGR ≥ S&P's own CAGR" gate was never actually
   part of the bar.** `compute_metrics_fixed()`'s `cagr_pct` annualises
   over the *trades' own realised exit-date span* within the block, not
   the block's full nominal calendar window — for Block 0 specifically
   (right at the start of history, while the 252-day momentum lookback is
   still ramping up and trades are sparse), this collapses the effective
   "years" denominator to a fraction of the nominal ~1.22-year block,
   turning a real but modest -18.99% total loss into a wildly amplified
   -63.18% annualised CAGR. Even setting that distortion aside and
   comparing plain total returns instead, Block 2's strategy return
   (+14.35%) still trails the S&P's own (+18.15% over the same window) —
   yet Block 2 is required to pass under the documented record. A
   CAGR-or-total-return "must beat the index outright" gate cannot be
   reconciled with that fact no matter how it's computed; it was never
   real.
2. **A profit-factor hard floor was missing entirely.** The task
   instructions' own phrasing ("the PF hard floor") was the direct
   pointer to it, and this project already has a standing, precedented
   convention for exactly this bar:
   `analysis/backtest.py:walk_forward_pf()`'s `n_positive = sum(1 for pf
   in windows if pf > 1.0)` (mirrored pre-V1-removal in
   `compute_walk_forward_summary()`'s `test_profit_factor > 1.0`). Adding
   the identical `pf > 1.0` check as a second, independent gate — with no
   change to the DD leg — reproduces the documented result exactly.

**The "no crisis-adjacent block materially loses money" phrase was never
quantified anywhere and is not needed to reproduce the result** — Block
0's total return (-18.99%) would pass even an invented "-20%" floor
regardless, so its presence or absence changes nothing observable in this
data. The earlier reconstruction's specific "-20%" number was never
independently verified; it has been removed rather than kept as
unverified dead weight now that PF+DD alone are known to fully reproduce
the documented outcome.

**Labeling, not a code bug.** The original record's "Block 1, Block
2/GFC-acute" uses 1-indexed ordinal language (first block, second block)
without ever stating so; `analysis/validation.py`'s 0-indexed block list
is a completely ordinary code convention, not an error. Once that's
accounted for, "documented Block 1" = this file's Block 0 and "documented
Block 2" = this file's Block 1 — and Block 1 (2008-03-22 to 2009-06-12) is
genuinely the Lehman-collapse-to-March-2009-bottom acute crash window,
exactly matching the "/GFC-acute" annotation. Both the indexing
convention and the formula were imprecise in how they were originally
written up; neither was a defect in `make_blocks()`, the trade-simulation
engine, or the DD/CAGR calculation itself.

**Step 4 — resolved and reconfirmed.** `analysis/validation.py` now
exports a pure, separately-tested `_block_passes(strat_metrics, sp_dd)`
implementing `dd_ok AND pf_ok` only. Re-running the full battery on
production with the corrected formula reproduces **14/16 blocks pass,
with exactly blocks 0 and 1 (0-indexed) failing** — the documented "Block
1, Block 2/GFC-acute" result, block-for-block, not just in aggregate.
TRAIN/TEST CAGR, trade count, and every other figure from the prior
reproduction (12,046 trades, TRAIN CAGR 13.23%, TEST CAGR 22.39%) are
unaffected — only the block-level pass/fail classification changed, since
`_block_passes()` only ever gates the block table, never trade selection,
sizing, or any other reported metric.

Covered by `tests/test_validation.py`: `_block_passes()` reproducing the
real 16-block production result exactly (pinned as fixture data, not
re-run live in the unit suite — a full production-scale run is a
multi-minute operation, validated via the SSH reproduction above), the
`pf > 1.0` strict-inequality boundary, the `DD ≤ 1.5×` boundary, both legs
being independently required, the `sp_dd is None` graceful-default case,
and `make_blocks()`'s 16 boundary dates pinned exactly so a future change
can't silently shift which real-world dates any block index refers to.

### XLK mapping resolution and ETF pricing lockout audit (2026-07-31)

The three-tier pricing fix (live quote if held → cached price → research-ticker
fallback, stocks only — see "Live pricing bug, exception isolation, and ETF
mapping audit" above) correctly closed the XLF mis-sizing risk, but has a
side effect worth stating plainly: **any ETF with no current holding and no
cached price now has no automated path to ever being priced for a first-time
entry** — `get_instrument_price_gbp()` returns `0.0` and the caller's
existing `if quantity <= 0: continue` guard silently skips it. XLF and XLK
were both already held at the time that fix shipped, so neither was
immediately affected — but every other ETF mapping was exposed to the same
risk without anyone having checked how many.

**XLK — same treatment as XLF.** `XDWTl_EQ` ("Xtrackers MSCI World
Information Technology") was flagged but left unremapped in the original
audit — global tech exposure, not the US-only Technology Select Sector Index
`XLK`'s momentum signal is computed against. The same sibling-pattern check
that found `SXLFl_EQ` for XLF found the equivalent for XLK live:
**`SXLKl_EQ`** — "State Street SPDR S&P U.S. Technology Select Sector
(Acc)", ISIN `IE00BWBXM948`, USD — same SPDR/State Street Select Sector
family as every other `XL*` mapping. The old `XDWTl_EQ` position (15 shares,
entered 2026-07-31 13:06 UTC at $136.79) was closed via a real market SELL
order, logged with `exit_reason = MAPPING_CORRECTION` (`invest_orders` row
id 15) — identical convention to XLF's fix. `instrument_mapping.XLK` now
points at `SXLKl_EQ`. As with XLF, this is a brand-new-to-us instrument with
no live position and no cache entry, so it is correctly untradeable by the
automated pipeline until a deliberate seed trade or a future real fill
establishes a reference price.

**Part B — no seed mechanism existed.** Checked directly (not assumed):
grepped the whole codebase for `seed`/price-bootstrap logic, the Status page
for any admin control, and a `scripts/` directory. The only reference to a
"seed" price bootstrap anywhere was a sentence in
`get_instrument_price_gbp()`'s own docstring stating that one would be
needed — no code implementing it existed. (`analysis/universe.py`'s
`seed_baseline_membership()` is an unrelated concept — S&P 500 membership
windows, not prices.)

**Part C — `brokers/trading212.py:seed_instrument_price_gbp(t212_ticker,
price, currency)`.** Takes an operator-supplied price in the instrument's
own native currency (exactly as T212 displays it — matching
`instrument_mapping`'s own `currency` field), converts to GBP via the same
`Trading212Client._to_gbp()` every other price in this module uses, and
writes it into the identical `last_known_price_gbp:{t212_ticker}` cache key
`get_instrument_price_gbp()`'s cache tier already reads — with an added
`"source": "manual_seed"` field (the existing cache schema had no
provenance field at all; adding one doesn't break `_get_cached_price_gbp()`,
which only ever reads `price_gbp`/`as_of`). This means a freshly-seeded ETF
is priced through the *existing* cache tier with zero special-casing
anywhere else in the pricing chain, and the moment it's actually bought and
held, the next live-position read (tier 1) overwrites the seed exactly as it
would overwrite any other cached value — a seed is a one-time bootstrap,
never a standing override. Raises `ValueError` on a non-positive price or an
unconvertible currency, deliberately loud since a seed is a rare,
operator-initiated action. **The seed price must come from directly reading
T212's own displayed price — never from `get_price_df()` or any other
automated proxy**, which is exactly the mechanism that caused the XLF
mis-sizing incident in the first place; the function takes no research-ticker
parameter at all, so there is no way to accidentally wire it to that proxy.

A companion read-only function, `audit_etf_pricing_paths(config, client=None)`,
classifies every ETF in `instrument_mapping` as held / cached (unheld) /
locked out, reusing `get_cached_price_entry()` (a new sibling of
`_get_cached_price_gbp()` that also returns the `source` field, for
audit/display purposes — `_get_cached_price_gbp()` itself is unchanged).

**UI.** The Status page's T212 Invest Account section gained an "ETF pricing-path
audit" expander (held/cached/locked-out counts + a per-ticker table) and a
"Seed a manual price" form directly below it (ticker dropdown scoped to ETF
entries only, price + currency inputs, a mandatory "I have personally
verified this price on T212's own platform right now" confirmation checkbox
gating the submit button) — deliberately requiring an explicit human
confirmation step before the button is even clickable, not just a warning
caption, given what this mechanism exists to prevent.

**Part D — the audit, run live against production** (post-fix mapping,
before any seeding): **11 held, 0 cached-but-unheld, 22 locked out**, of 33
total ETF mappings.

Held (11): VOO, GLD, SLV, XLE, XLV, SOXX, EWJ, EWG, EEM, EWZ, VGK.

Locked out (22): TLT, IEF, HYG, LQD, **XLK**, **XLF**, INDA, FXI, SKYY, XLI,
XLY, XLP, XLU, XLB, KBE, PDBC, EMB, TIP, AMLP, MUB, IBB, IWM.

XLK and XLF both correctly show as locked out — both are brand-new-to-us
instruments with the position/cache history of their *old*, wrong mapping
now superseded, exactly the expected post-remap state described above.
**No ETF was seeded as part of this task** beyond the mechanism's own test
coverage (synthetic/mocked instruments in `tests/test_execution.py`, never
run against the real 22) — whether to seed all 22 now, or only
opportunistically when a real signal actually wants to trade one, is an
open decision, not made here.

Covered by new tests in `tests/test_execution.py`: cache population and
currency conversion (USD/GBP/GBX), `source` field correctness, rejection of
a non-positive price and an unconvertible currency, a seeded price
correctly pricing an unheld ETF through the existing cache tier, a
subsequent live-position quote correctly superseding a manual seed, and
`audit_etf_pricing_paths()`'s held/cached/locked-out classification
(including graceful handling of a live-position-fetch failure).

**Follow-up: is a locked-out ETF's signal-positive-but-unpriceable state
actually noticed, not just silently skipped?** Checked directly rather than
assumed. `maybe_place_orders()`'s BUY loop only ever reaches the
`get_quantity_for_gbp()` call for a ticker that has *already* cleared the
full entry gate (`sig_on` AND `is_member` AND not `entry_blocked`) — so a
`quantity <= 0` result at that point is never "no signal", it is
specifically "signal wants this ticker but it has no viable price."
Before this fix, the log line at that point (`"Could not calculate
quantity for %s (%s) — skipping."`) was generic — true, and not silent
(it's a `continue`, not a swallowed exception, so the loop correctly moves
on to the next ticker), but it didn't confirm the signal was actually
positive or point anywhere toward the fix, so a reader scanning logs would
have to already know the context to recognise this as "go seed this
ticker now" rather than some more mundane skip. Fixed by branching on
`state["is_etf"]`: for an ETF, the warning now reads *"momentum signal is
positive and this ticker is otherwise entry-eligible, but no viable price
is available (not currently held, no cached price) — entry skipped. Needs
a manual price seed (Status page → T212 Invest Account → ETF pricing-path
audit → Seed a manual price) before this ticker can ever be entered."* —
naming the ticker, confirming the signal state, and naming the exact fix
and where to find it, so the log line itself is the trigger for exactly
when to go seed it rather than needing a separate tracking mechanism. The
non-ETF branch is unchanged (a stock reaching this point is a different,
rarer failure mode — missing price data entirely — not something the seed
mechanism addresses). Also added `"price seed"` / `"no viable price"` /
`"locked out"` to the Status page Log Viewer's `"Orders"` filter category
keywords, so this surfaces there without needing "Errors Only" (which
already caught it too, since it logs at `WARNING`).

One nuance worth being explicit about: this event is only ever recorded in
the application log (`journalctl` / the Status page's Log Viewer) — unlike
a placed order, a skipped-for-no-price entry never gets a row in
`invest_orders`, so there is no queryable DB-backed record of "signal X
wanted ticker Y on date Z but couldn't price it." An operator who never
opens the Log Viewer (or whose retention window has rolled past the event)
would miss it. Left as-is for now, since the log line itself is
self-documenting and this system has no other precedent for logging
skipped-entry attempts to a table (a placed order is the unit of record
throughout `invest_orders`) — a DB-backed "unpriceable entry attempts"
table would be a reasonable future addition if this in practice proves too
easy to miss, not built pre-emptively here.

Verified with a new test in `tests/test_execution.py`
(`test_locked_out_etf_with_positive_signal_is_a_clean_logged_skip_not_a_silent_failure`):
two ETFs configured, one locked out (mocked `get_quantity_for_gbp` returns
`0.0` for it) and one priceable — confirms no order is placed for the
locked-out ticker, confirms the *other* ticker still gets its order (the
run does not halt after the locked-out ticker, proving this is a clean
`continue` and not something that trips the per-ticker exception-isolation
fix — see "Live pricing bug, exception isolation, and ETF mapping audit"
above), and asserts the exact warning text names both tickers, confirms
the signal was positive, and names the seed mechanism.

### Unified instrument structure (2026-07-31)

`watchlist.trading` (ticker/name/exchange — signal/eligibility) and
`trading212_invest.instrument_mapping` (nested ticker/currency/is_etf —
execution) were two separately-maintained config.yaml structures
describing the same 73 instruments, with nothing structurally enforcing
they stayed in sync. XLF's and XLK's wrong T212 mappings (see "XLK mapping
resolution and ETF pricing lockout audit" above) were only ever found by
accident, during an unrelated investigation — a ticker present in one
structure but silently missing from the other would have been the exact
same class of bug, just harder to spot. Consolidated into one `instruments:`
section, one record per tradeable instrument.

**Step 1 — investigation first.**
- Every call site reading either structure was checked directly (grep
  across the whole codebase, not assumed): `data/database.py`,
  `data/ingestion.py`, `data/scheduler.py`, `main.py`, `analysis/universe.py`,
  `brokers/trading212.py`, and `output/performance.py`. Most read only
  `watchlist` (price-pulling, Status page coverage/display, Charts landing
  grouping) or only `instrument_mapping` (`get_max_holding_days()`, the BUY/
  EXIT loops, the ETF pricing audit, `get_ticker_status()`/`get_brief_cards()`)
  — no call site read both together, which is exactly how the two could
  drift apart unnoticed.
- **Checked directly whether the two were already out of sync**: as of
  this task, they were not — 73/73 tickers matched exactly both ways, and
  `watchlist.core` had zero `instrument_mapping` entries (confirmed
  programmatically, not assumed). The *risk* the duplication created was
  real even though no live mismatch existed yet.
- `watchlist.core` (`^GSPC`, `GBPUSD=X`, `EURUSD=X`) confirmed to have no
  execution mapping today and is intentionally kept entirely separate
  going forward — see the design decision below.
- **config.yaml vs. `universe_membership` (DB-backed)**: judged config.yaml
  the correct home. `universe_membership` exists because S&P 500 index
  membership is *genuinely time-varying* data with an external source and
  a periodic refresh job (`refresh_membership_from_source()`) — a
  fundamentally different shape of data from an instrument's name,
  exchange, T212 ID, and currency, none of which change on any schedule;
  they change only when a human corrects a mapping error (exactly what
  happened with XLF/XLK). config.yaml is git-tracked specifically so a
  mapping change shows up in `git diff`/code review — moving this to the
  DB would lose that review surface for no benefit, since nothing about
  this data is dynamic.

**Step 2 — design.** One record per tradeable instrument, keyed by research
ticker, combining `name`/`exchange` (from the old `watchlist.trading`) with
`is_etf`/`t212_instrument_id`/`currency` (from the old `instrument_mapping`).
The nested `ticker` field was renamed to **`t212_instrument_id`** — the old
name collided confusingly with the watchlist's own top-level `ticker` key,
one meaning "our research symbol" and the other meaning "T212's own
internal instrument ID," a distinction that mattered every time someone
had to read the mapping carefully (as the XLF/XLK investigations did).
**GOLD's pattern is preserved exactly**: still present, still `is_etf: false`,
still carrying its explanatory comment ("Barrick Mining — verified live but
permanently zero-eligible... never a genuine S&P 500 constituent") inline
in `config.yaml` — real institutional memory, not something the migration
was allowed to silently drop. `XLK`'s and `XLF`'s remap comments (dated
2026-07-31, explaining exactly what was wrong and what replaced it) are
preserved the same way. `watchlist.core` stays a separate, plain list —
it never has and never will have execution fields, by design, not by
omission; `tests/test_config.py` asserts this structurally.

**Validation**: `tests/test_config.py` loads the real `config.yaml` (not a
synthetic fixture) and asserts every `instruments` entry has all five
required fields with sane types/values, that no two tickers share a
`t212_instrument_id`, that `watchlist.trading`/`instrument_mapping` haven't
reappeared, that `watchlist.core` never gains execution fields, and that
GOLD's specific preserved values are still there. Chose test-level
validation over a runtime load-time check because config loading itself is
already duplicated informally across five different `_load_config()`
functions (`main.py`, `data/ingestion.py`, `data/database.py`,
`brokers/trading212.py`, `analysis/universe.py` all define their own) — a
loudly-failing test that blocks the pre-push hook (see "Git hooks" below)
achieves the "structurally impossible to miss" goal without adding a sixth
validation call site.

**Step 3 — migration.** `data/ingestion.py` gained
`get_all_watchlist_entries(config)` — merges `watchlist.core` (list) with
the new `instruments:` (dict, ticker injected from the key) into one
uniform list of dicts; `get_all_tickers()` is now a thin wrapper over it.
`_build_watchlist()`, `_price_coverage()`, and `_render_charts_landing()`
(`main.py`) all use this instead of re-deriving the merge themselves.
`_render_charts_landing()`'s two-group display (`_GROUP_LABELS`) now reads
`"core"` from `watchlist` and `"instruments"` from the new section
(converted to the same list-of-dicts shape inline) — the grouping/rendering
loop body itself needed no change. Every other call site
(`analysis/universe.py:get_max_holding_days()`, `brokers/trading212.py`'s
`audit_etf_pricing_paths()`/`maybe_exit_positions()`/`maybe_place_orders()`,
`output/performance.py`'s `get_ticker_status()`/`get_brief_cards()`,
`main.py`'s `_asset_type_map()` and the Status page's mapping
table/ETF-audit expander/seed form) now reads `config.get("instruments", {})`
directly and `raw.get("t212_instrument_id")` in place of the old
`raw.get("ticker")`. `data/database.py:_purge_unlisted_tickers()` combined
both sources for its stale-ticker allow-list — since removed, see
"`price_data` purge removed" below.

**Step 4 — reproducibility check.** Ran `get_ticker_status()` (momentum
signal, index membership, crisis state per ticker) and
`get_max_holding_days()` for all 73 tickers against production data, once
on the pre-consolidation deployed commit (`c45d04e`) and once again after
deploying this consolidation — byte-identical results both times, as
expected: this is a pure config-structure refactor, no signal/eligibility/
holding-limit/pricing logic changed at all, only which config key each
value is read from. No difference found; nothing to stop and report.

### Step 5 — `notifications.email`: actively used, not dormant

Checked directly (not assumed): `output/briefing.py:_smtp_send()` — the
sole survivor of that module's original much larger scope — is called by
`brokers/trading212.py:_send_t212_error_email()`, itself called from four
sites: an API error during the BUY-loop's account-state setup, a failed
BUY order, an API error during the EXIT-loop's account-state setup, and a
failed... (the same function, four call sites covering both the entry and
exit paths' failure modes). **This is fully wired, working infrastructure,
not dormant configuration** — a genuine T212 API error or failed order
does send an email today.

**A related, distinct gap found in the same check, reported rather than
fixed**: `notifications.email.enabled` and `alert_cooldown_hours` (both
fully present in config.yaml, both documented as real knobs) are **not
read anywhere in the codebase** — grepped for both and found zero call
sites. `_send_t212_error_email()`/`_smtp_send()` fire unconditionally on
every qualifying error, with no cooldown and no way to disable them
short of removing SMTP credentials entirely. This means a persistent T212
API error (e.g. a rate-limit exceeding the retry budget on every scheduled
run) would send one email per occurrence indefinitely, and setting
`enabled: false` would not stop it. Not fixed here — this task's scope was
"check whether it's used," and gating behaviour is a design decision
(what cooldown, what constitutes "enabled" for an error-alert path that
was never built with a toggle) worth bringing back rather than making
unilaterally alongside an unrelated config consolidation. The
crisis-override-trigger idea mentioned in this task's own framing was not
built either, for the same reason.

**Resolved 2026-07-31 — see "notifications.email enabled + cooldown wired
up" below.** Both flags are now actually read and enforced.

### Pre-market/US-open scheduled runs removed (2026-07-31)

Two of the three daily scheduled runs — 07:00 UTC pre-market (Tue-Fri) and
13:30 UTC US-open — were `analysis_only`: they re-ran the order-logic
pipeline without pulling any new price data. Investigated whether this was
a genuine V1 vestige (this project's own established pattern this session
— same shape as the XLF/XLK mapping errors and the watchlist/
instrument_mapping duplication) before removing anything, per this
project's own "confirm precisely, don't just assume" standard.

**Step 1 — traced what `analysis_only` actually executes, end to end.**
`_run_analysis_pipeline(config, all_tickers, run_type)` (`data/scheduler.py`)
does exactly two things: stamps `last_run_type` in `system_metadata`, and
calls `brokers/trading212.py:maybe_place_orders(config, run_type)`. Inside
`maybe_place_orders()`, `run_type != "full"` takes one specific branch:
call `maybe_exit_positions(config)` and return immediately — no BUY phase,
no order-status polling, no account snapshot refresh. So the *entire*
effect of both runs, individually, reduces to one function call:
`maybe_exit_positions()`. Separately, `_pre_market_job()` (the Tue-Fri
07:00 job) also had its own data-pull step — for "exchanges that just
closed" (originally Asian markets, JPX/HKEX, under V1's much broader
112-asset watchlist) — which by construction can never affect anything
`analysis_only` itself does (a data pull and the order-logic call are two
separate steps in the same function). Confirmed the hourly
`trade_alert_check` job (`_trade_alert_job()`) independently calls all
three of `poll_placed_orders()`, `maybe_exit_positions()`, and
`_fetch_t212_snapshot()` on every firing, 00:00-21:00 + 23:00 UTC every
weekday — i.e. it already does everything either of the two runs in
question did, plus more, on its own unconditional hourly cadence.

**Step 2 — confirmed empirically, not just from reading the code.**
1. **The pre-market job's own data pull is a permanent no-op today**,
   confirmed by calling the actual live function against the actual live
   config rather than inferring it: `_build_ticker_exchange_map(config)`
   returns `{"NYSE", "FX"}` as the only two exchanges present anywhere in
   the current `instruments:` + `watchlist.core` universe (73+3 tickers,
   all either US-listed or FX — see "Unified instrument structure" above),
   and `_exchanges_closed_before({"NYSE", "FX"}, now)` returns an empty
   set — FX is explicitly excluded (never "closes"), and NYSE's own
   session for *today* hasn't even opened yet at 07:00 UTC, so it can
   never satisfy "closed before 07:00 UTC today." This is a direct
   consequence of the 2026-07-31 watchlist trim (V1's ~112-asset universe,
   which genuinely included Tokyo/Hong Kong-session tickers, cut down to
   V2's US-and-FX-only 73+3) — real historical log lines from Jul 29-31
   still showed a `JPX`-exchange data pull firing, but those all predate
   that trim's deploy; nothing in the *current* deployed config can ever
   trigger that branch again.
2. **The hourly job already fires at the identical minute.** Production
   logs for 2026-07-31 show `"Scheduled pre-market analysis starting"` and
   `"Trade alert check starting…"` both logging at `07:00:00` — the same
   second, from the same scheduler process. For the 13:30 US-open job,
   logs show the hourly job firing at 13:00:00, the US-open job at
   13:30:00, and the hourly job again at 14:00:00 — the US-open job sits
   inside a 30-60 minute window already bounded on both sides by the
   identical `maybe_exit_positions()` call the hourly job makes, using the
   same stored price data (unchanged since the prior EOD close) and the
   same live T212 position state. Genuinely direct evidence, not an
   inference from reading the scheduler code alone.

**Not touched: the Monday-only pre-market job.** `_monday_premarket_job()`
has one real, non-redundant action — `_cancel_stale_weekend_orders()` — not
replicated anywhere else in the system, so it was kept in full. Its own
trailing `_run_analysis_pipeline(..., RUN_ANALYSIS_ONLY)` call is subject
to the *exact same* redundancy argument as the two removed jobs (it's the
identical `maybe_exit_positions()`-only call), but this task's scope was
specifically the Tue-Fri 07:00 and 13:30 jobs — flagged here as an open
finding, not acted on unilaterally.

**Removed**: `_pre_market_job()`, `_us_open_job()`, and (now-dead once
`_pre_market_job()` was gone) `_get_calendar_session()` /
`_exchanges_closed_before()` — all deleted rather than left disabled, this
project's standing practice for confirmed-rejected code (same precedent as
the GARCH stop loss and HMM regime gate removals). `config.yaml`'s
`scheduler.runs` now has only `eod`; `start_scheduler()`'s `_JOB_FNS` dict
correspondingly only maps `"eod"`. The Monday job's hour/minute, previously
derived from `scheduler.runs.pre_market` so it tracked its Tue-Fri
sibling's schedule, is now hardcoded at 07:00 UTC directly in
`start_scheduler()` (that sibling no longer exists to track). `main.py`'s
Status page ("Next Scheduled Run" card, "Scheduled Runs" cards, Scheduler
Health table's in-process branch which reads live APScheduler jobs
directly and needed no change, and its daemon-mode reconstruction branch)
all updated to match — a new `_next_monday_at(hour, minute)` helper
computes the Monday job's own next-fire time directly (unlike
`_next_run_for_time()`, which only ever skips weekends, not the other four
weekdays), since that job no longer has a `scheduler.runs` entry to derive
a "next run" display from generically.

**Current schedule**: 21:30 UTC EOD (the only run that pulls prices),
07:00 UTC Monday-only pre-market (stale-order cancellation), hourly
trade-alert checks (00:00-21:00 + 23:00 UTC weekdays), 21:15 UTC membership
refresh, and the 60-second job dispatcher — five scheduled activities,
down from the wider set this file's top-of-file schedule table still
describes (that table, and the FOMC/BoJ/ECB reaction-job sections
immediately following the "Monday pre-market job" section above, predate
the V1 removal and describe jobs that no longer exist in this codebase —
a pre-existing documentation gap, out of scope to fully reconcile here,
but worth flagging so a future reader doesn't trust that table at face
value for the current schedule).

Covered by `tests/test_scheduler.py`:
`test_scheduler_registers_monday_job_mon_only_fixed_at_0700` (Monday job
still registers correctly at 07:00 UTC with no `scheduler.runs.pre_market`
key present at all, and confirms `scheduled_pre_market`/`scheduled_us_open`
are never registered under any job id) plus the existing Monday
stale-order-cancellation and membership-refresh registration tests, all of
which pass unchanged against the simplified config.

### notifications.email enabled + cooldown wired up (2026-07-31)

`_send_t212_error_email()` (`brokers/trading212.py`) sends unconditionally
on four call sites — a BUY-loop setup API error, a failed BUY order, an
EXIT-loop setup API error, a failed SELL order — via `output/briefing.py:
_smtp_send()`. `notifications.email.enabled` and `alert_cooldown_hours`
were both fully configured and documented (found during the config-
consolidation task above) but neither was ever actually read anywhere,
meaning a persistent error (e.g. a rate limit exceeding the retry budget
on every scheduled run) could email once per occurrence indefinitely, and
`enabled: false` did nothing.

**`enabled` is gated inside `_smtp_send()` itself**, not in
`_send_t212_error_email()` — the single low-level chokepoint every caller
goes through (today just the T212 error alert, but any future caller gets
this for free automatically, with no chance of a new call site forgetting
the check). When disabled, `_smtp_send()` returns `(False, "...")`
immediately, before ever opening an SMTP connection — confirmed via a
mocked `smtplib.SMTP` that asserts it's never even called.

**The cooldown is deliberately GLOBAL, not per-call-site or
per-error-message** — tracked via a single `system_metadata` key
(`t212_error_email_last_sent`), checked and updated inside
`_send_t212_error_email()`. A persistent underlying issue (a rate limit,
an auth failure) is one real problem, not four independent ones, and is
exactly the kind of thing likely to surface through more than one of the
four call sites in the same run — a per-call-site cooldown would still
let each site send its own email every cycle, doing nothing to stop the
flood the cooldown exists to prevent. `notifications.email.alert_cooldown_hours`
(default 8, matching the existing config.yaml value) gates it; skipped
sends log at `INFO` ("T212 error email skipped — within Xh cooldown...")
rather than being silently dropped.

**The last-sent timestamp is only updated on a genuinely successful
send** — `_smtp_send()` returning `False` (disabled, no recipients
configured, or a real SMTP failure) never touches it. This matters
specifically for the disabled case: an error occurring while
`enabled: false` must not suppress the first real alert once
notifications are turned back on — confirmed by a test that sends while
disabled and then asserts the cooldown key was never written at all.

Covered by `tests/test_briefing.py` (new — `_smtp_send()`'s disabled-skip,
enabled-send, and no-recipients cases, each confirmed via a mocked
`smtplib.SMTP` that is/isn't called) and new cases in
`tests/test_execution.py`: disabled config skips cleanly with no SMTP
attempt, a first-ever error sends regardless of cooldown, a second error
within the cooldown window is skipped, an error after the cooldown has
elapsed sends again, and a disabled-config error never consumes the
cooldown timestamp.

### Decision-relevant, tone-neutral context on open position cards (2026-07-31)

Position cards showed a trade was open with no sense of why it was still
open or what would close it — a plain functional gap, but the tone of any
fix mattered as much as the data: this system exists specifically to
remove reactive, emotionally-driven decision-making from investing, and a
dashboard that makes individual trades look urgent or at-risk would
quietly reintroduce exactly that. Uses only data already on the card
(`live_price`, `stop_price`, `days_held`, `max_holding_days` — see
`output/performance.py:get_brief_cards()`) — no new API call, no new
pricing pipeline.

**What was added, per position, on both the Daily Briefing's compact
cards and the Performance page's full-detail cards:**
- Distance to stop, stated plainly — `"{pct:.1f}% above stop"` — computed
  by `output/performance.py:compute_position_context()`.
- Days remaining until the max-holding backstop — `"{n}d until max-hold"`
  (compact) / `"day {held} of {max} ({n} days until max-holding limit)"`
  (full) — from the same function.
- A plain statement of the shared exit-rule shape —
  `position_exit_rule_text(max_holding_days)`: *"Closes automatically on
  any of: stop-loss hit, momentum turning negative, or day {N} of holding
  reached — whichever comes first."* Descriptive of the mechanism, not a
  prediction of which one will fire.

**Tone requirements, and how each was met:**
- **No color-coded urgency.** `position_context_style(pct_above_stop,
  days_remaining)` always returns the same neutral colour (`#8899AA`)
  regardless of either input — there is no conditional/threshold logic in
  it at all, by construction. Directly enforced by
  `test_position_context_style_is_neutral_regardless_of_proximity`, which
  sweeps both values across a wide range on either side of every
  meaningful threshold (deep in-the-money through past-stop; a year of
  runway through overdue) and asserts the returned colour set has exactly
  one member.
- **No ranking by proximity.** Both "Open Positions" lists (Daily Briefing
  and Performance page) previously sorted `open_cards` by
  `status_priority` descending — literally "most urgent first," the exact
  pattern this task's tone requirements name directly. Changed to a
  stable alphabetical sort by ticker on both pages. (Not independently
  unit-tested — main.py cannot be imported in tests, see
  `output/performance.py`'s own module docstring for why — verified by
  reading the changed sort key directly.)
- **No call-to-action elements.** Confirmed neither card ever had a
  button, link, or intervention prompt before this change, and none was
  added — the new content is text only.
- **Calm aggregate summary first.** `positions_summary_text(n)` —
  *"N position(s) open — portfolio operating within its normal rules."* —
  rendered before the per-position list on both pages, immediately
  followed by the one-time, page-level `POSITIONS_RULES_EXPLANATION`
  caption (the exit-rule mechanism explained once, so individual cards
  only need to state their own numbers, not repeat the full rule text
  every time).
- **Descriptive, not alarmist, language throughout.**
  `test_position_exit_rule_text_is_descriptive_not_predictive` and
  `test_positions_rules_explanation_has_no_call_to_action_language` grep
  the actual shipped strings for alarmist/CTA vocabulary ("risk",
  "danger", "urgent", "warning", "review", "click", "consider") and assert
  none of it is present — a real check against the copy, not just a
  writing-style intention.

**Scope note.** The pre-existing `_STATUS_COLOURS`/`_STATUS_ICONS`
green/amber/red badge system (ON TRACK/SIGNAL OFF/STOP LOSS/etc.) was left
unchanged — it reflects a genuine discrete state (which exit condition, if
any, is currently true), not a continuous proximity measure, and
redesigning it was a larger, separate decision outside this task's literal
scope ("using data already computed... add the following"). The tone
requirements were applied in full to everything newly added.

Covered by `tests/test_performance.py` (9 new tests): `compute_position_context()`'s
arithmetic and its None-handling for missing/zero fields (including a
price already past its stop, handled as a plain negative number, not a
special case), the neutral-styling invariant above, the exit-rule text's
content and its known-missing-`max_holding_days` fallback, and the
summary/explanation copy checks.

### Exchange-aware order submission (2026-07-31)

**What this fixes, and what it doesn't.** ETF orders (the 33 UCITS-wrapped
instruments, listed on the London Stock Exchange, 07:00–15:30 UTC) were
submitted at the fixed 21:30 UTC EOD time — over six hours after LSE had
closed for the day, a dead window. Stock orders (NYSE-listed, regular hours
13:30–20:00 UTC) landed in an unconfirmed after-hours window with no
verified fill behaviour on this account. This change decouples *when the
signal is computed* (still EOD, unchanged and validated) from *when the
resulting order is actually submitted*, and makes submission exchange-aware
— each instrument now submits during its own real trading session.

**This does NOT close the calendar gap between decision and execution** — a
Friday-computed decision still can't submit before Monday's relevant open,
since neither exchange is open at decide time either (a Friday EOD decision
waits through the weekend exactly as before). What changed is *ambiguity*:
predictable, session-correct submission instead of firing into a closed or
uncertain window. Do not read this as closing the timing gap itself — the
LSE-only constraint is a UK/EU securities-law restriction (Overseas Funds
Regime equivalence not extended to US-registered funds, applying equally to
every UK-regulated retail broker — see the PRIIPs/OFR investigation this
same day), not an engineering problem this mechanism, or any mechanism on
this side, can fix. A draft Known-Limitations entry for the eventual V2 DD
report covering this exact point (ETF execution venue vs. signal
computation) has been drafted separately and is pending incorporation into
`analysis/dd_report.py` — not yet wired in as part of this change.

**Mechanism — decide, then submit.** `brokers/trading212.py:maybe_place_orders()`
(called from the EOD job, unchanged timing) still computes every ticker's
momentum signal, index membership, and crisis-block status exactly as
before, and still runs the VaR-sized entry decision for each qualifier — but
instead of calling `place_market_order()` directly, it writes a `PENDING`
row to the new `pending_orders` table (`ticker`, `t212_ticker`, `is_etf`,
`currency`, `decided_at`, `decided_price`, `position_gbp`, `momentum_pct`,
`stop_price`, `var_pct`). A dedup guard (`_has_pending_order()`) stops a
ticker whose signal stays on across multiple EOD runs from accumulating
duplicate `PENDING` rows while submission is paused or behind. Decide-phase
no longer enforces `max_total_deployed` / `max_open_positions` at all —
queuing a decision commits no cash and opens no position, so there is
nothing yet for those caps to protect against; they moved to submission
time, where the account's live state is what actually matters.

Two new scheduled jobs, each acting only on its own asset class:

| Job | Schedule | Calls |
|---|---|---|
| `etf_order_submission` | 07:00 UTC weekdays (LSE open) | `brokers.trading212.submit_pending_etf_orders()` |
| `stock_order_submission` | 13:30 UTC weekdays (NYSE regular open) | `brokers.trading212.submit_pending_stock_orders()` |

Both are thin wrappers around the shared `_submit_pending_orders(config, is_etf)`,
which reads every `status = 'PENDING'` row for its own `is_etf` value
(oldest decision first), then — per row — re-checks it isn't already held or
already has a recent order (defensive, covers manual intervention or an
overnight exit), runs the price-sanity check (below), resolves the actual
share quantity from live instrument metadata (moved here from decide-phase,
since the real submission-time price is what quantity must be sized
against, not the decide-time close), and places the order. `max_total_deployed`
/ `max_open_positions` are enforced exactly as the old immediate-submission
BUY loop did — just against the account's state *at submission time* — and
binding either cap stops the run for that pass, leaving every row not yet
reached still `PENDING` (retried at the next submission window, not marked
skipped or lost). The exact same per-row exception isolation the 2026-07-31
BUY-loop fix established (see "V2 first-cycle deployment cap overshoot" /
"Live pricing bug, exception isolation" below) applies here too — one row's
unhandled exception never stops the rest of the queue.

**Price-sanity check — fail loud, don't guess.** Before submitting, each
row's `decided_price` (the close price at decide time) is compared against
`_latest_close(ticker)` — the ticker's own most recently stored close from
`price_data`, deliberately **not** a T212 quote: T212 has no live quote at
all for an unheld UCITS ETF (see `get_instrument_price_gbp()`'s docstring),
and even where a quote exists, the UCITS wrapper's own price is a different,
unrelated number from the underlying US ticker's price (see "Live pricing
bug..." below) — so a T212-side comparison would either be unavailable or
comparing the wrong thing. The research ticker's own close is exactly what
`decided_price` was built from, so it's the correct apples-to-apples
comparison for "has this moved a lot since the decision was made." If the
move exceeds `trading212_invest.price_sanity_threshold_pct` (default **3%**
either direction), the row is marked `SKIPPED_PRICE_MOVED` with a
resolution note and a clear `WARNING` log line naming the ticker, the
decided/current prices, and the threshold — the order is never placed on a
stale decision. **Why 3%**: comfortably above ordinary day-to-day noise for
a diversified ETF or a large-cap stock, but well below a genuine
earnings-surprise or macro-shock gap — the same "fail loud, don't guess"
principle as the 2026-07-31 `get_instrument_price_gbp()` pricing-bug fix.
One shared threshold across both asset classes, not split ETF/stock — kept
simple per the task's own framing ("some reasonable threshold"); revisit if
live data shows one asset class needs a different value.

**ETF submission ships paused; stock submission continues as today.**
`trading212_invest.etf_submission_enabled` defaults `False` when absent —
ETF submission is explicitly disabled in `config.yaml` (`etf_submission_enabled: false`)
until this mechanism has been deployed and independently verified against
the live demo account. **Do not flip this to `true` as part of deploying the
mechanism itself — that is a separate, later decision once submission
timing/pricing has been watched through at least one real LSE session.**
`trading212_stocks_submission_enabled` (`trading212_invest.stock_submission_enabled`)
defaults `True` — no behavioural change was requested for stocks, so the
stock submission path is live from this deploy. Both flags are read fresh
by `_submit_pending_orders()` on every call, so flipping ETF submission on
later needs no code change — only a config edit and a deploy.

**New table — `pending_orders`** (`PendingOrder` in `data/database.py`,
`id` PK, no unique constraint on `ticker` — dedup is enforced in code via
`_has_pending_order()`, not the schema). `status` is one of `PENDING` /
`SUBMITTED` / `FAILED` / `SKIPPED_PRICE_MOVED` / `SKIPPED_ALREADY_HELD`.
Rows are never deleted — a resolved row's `resolution_note`/`resolved_at`
give a full audit trail of every decision from the moment it was queued to
however it was ultimately resolved (or never resolved, if a row is still
sitting `PENDING` because e.g. ETF submission remains paused).

Covered by `tests/test_execution.py`: decide-phase queuing (a qualifying
entry writes a `PENDING` row, never calls `place_market_order()` directly),
the dedup guard, decide-phase ignoring both caps even when already blown,
submission acting only on its own asset class's rows, both submission
enable/disable defaults, the price-sanity check's skip and pass paths, both
caps enforced at submission time (pre-loop-equivalent and mid-run,
mirroring the original 2026-07-31 cap-overshoot fix's own test shape), the
locked-out-ETF clean-skip case (now at submission time, since quantity
resolution moved there), and per-row exception isolation. `tests/test_scheduler.py`
covers both new jobs' registration (schedule, job id, target function) and
that each job calls its corresponding `submit_pending_*` function and never
raises on failure.

### Membership-window bug — false INDEX_REMOVAL exits (2026-07-31)

`analysis/universe.py:_reconstruct_windows()` closed an **in-progress**
membership window (a ticker still present in the source CSV's most
recent/final row — i.e. genuinely still an active constituent as of the
latest available data) using that final row's own date, rather than
`_SENTINEL_END = date(2099, 12, 31)` — a constant defined in the module
specifically for this case ("still a member as of the latest known
data") but never actually referenced anywhere in the function. The
practical effect: the moment "today" advances past whatever date the
source dataset (`fja05680/sp500`) last happened to update, `is_member()`
incorrectly reports every currently-active constituent as removed.

**Discovered via a real incident, not a code review.** The
`v2_membership_refresh_job` (21:15 UTC weekdays) ran normally on
2026-07-31 and, per this bug, wrote `end_date = 2026-06-30` (the
source's own last-updated row at the time) for every ticker still active
as of that row — including `AAPL`, `AMZN`, `AMD`, `AVGO`, `BAC`, `CAT`.
The 21:30 UTC EOD run's exit phase then evaluated `is_member(ticker,
2026-07-31)` for these six (the only six stock tickers this system had
ever actually bought a position in), got `False`, and placed 6 real —
if queued/unfilled, demo-account — SELL orders logged `exit_reason =
INDEX_REMOVAL` against stocks that plainly never left the S&P 500.

**Scope check (2026-07-31), before any fix landed:** queried
`is_member()` for all 39 non-empty-window tickers in `STOCK_MEMBERSHIP`
as of today — **all 39 (100%) were misclassified** as non-members,
confirming this is a universe-wide defect, not specific to the six that
happened to trigger a real order. Cross-referenced against `invest_orders`
for every `INDEX_REMOVAL`-tagged row and every stock BUY ever placed:
**only those same 6 tickers had ever been bought** in this system's
history, so no additional false exits existed or were queued beyond the
known 6 — the other 33 were misclassified too (and would have been
wrongly blocked from new entries) but had no open position to falsely
sell.

**Fix.** `_reconstruct_windows()`'s trailing-window close now uses
`_SENTINEL_END` instead of `rows[-1][0]` when the ticker is still
present in the source's final row:

```python
if in_window and rows:
    windows.append((start, _SENTINEL_END))   # was: (start, rows[-1][0])
```

Two pre-existing tests (`test_refresh_detects_removal_and_readdition`,
`test_refresh_merges_known_ticker_rename`) had themselves codified the
buggy behaviour as correct — both asserted a still-open trailing window
closed at the synthetic CSV's own last-row date. Both updated to expect
`_SENTINEL_END` and to assert continued eligibility well past that row
(`2026-07-31`, `2099-01-01`). A new dedicated regression,
`test_reconstruct_windows_still_active_at_source_end`, covers the exact
real-world shape directly: a ticker present only in the source's final
row must resolve as a member for any date after that row, not silently
flip to removed.

**Deployed, then the stored data itself corrected.** The code fix alone
doesn't retroactively repair the `2026-06-30`-end-dated rows already
sitting in `universe_membership` — those were written by the buggy
function's last real run and stay wrong until a fresh
`refresh_membership_from_source()` call rewrites them (the function
always does a full per-ticker `DELETE` + re-`INSERT`, never a partial
patch). Ran that once by hand immediately after deploying, against a
`.backup`-snapshotted copy of the live DB taken first — confirmed all 39
previously-misclassified tickers, not just the original 6, now resolve
`is_member(ticker, date.today())` correctly.

**Cleanup.** The 6 queued (T212 status `NEW`, `filledQuantity: 0`,
`extendedHours: False` — unfilled, not rejected) false SELL orders were
cancelled directly via the T212 API and each cancellation verified
against a fresh `get_order()`/`get_positions()` read afterward, not
assumed from the local DB alone — matching this project's standing
"confirm directly against T212, not just the DB" discipline (see the
2026-07-31 pricing-bug and mapping-audit sections above). All 6 original
positions (`AAPL`, `AMZN`, `AMD`, `AVGO`, `BAC`, `CAT`) confirmed still
genuinely held afterward, with no erroneous order remaining against any
of them.

**A second, lower-severity imprecision noted but not changed as part of
this fix**: `STOCK_MEMBERSHIP`'s hardcoded baseline dict (the one-time
seed used only when `universe_membership` has no rows yet for a ticker)
closes its own "still active" windows at a fixed `"2026-12-31"` rather
than `_SENTINEL_END` — the identical shape of imprecision, just with a
longer fuse (won't misfire until the 2026 year boundary) and on a
code path that only ever matters before the first real refresh has run.
Left as-is since it's outside this fix's literal scope and materially
lower urgency; worth revisiting alongside any future universe-tracking
work.

### V2 stock universe broadening — researched, not adopted (2026-08-01)

Tested whether broadening `analysis/universe.py:STOCK_MEMBERSHIP` from the
curated 40 to a mechanically-selected, much larger real S&P 500 candidate
pool helps, hurts, or dilutes the validated result. Run in an isolated
server-side clone with its own copied DB, per this project's standing
research-clone discipline — deleted after this section was written, per
that same standing practice (see the many "deleted after the finding
below" precedents throughout this file). **This section exists specifically
to satisfy the "Research reproducibility standard" at the top of this
file** — the exact formula and numbers below are the permanent record now
that the disposable implementation is gone.

**Universe construction — exact, mechanical rule.** Source: the same
`fja05680/sp500` CSV `analysis/universe.py` already uses. Candidate
inclusion rule: `is_included(ticker) := ticker has ≥1 membership window
overlapping [2007-01-01, 2026-06-30] AND ticker is a genuine S&P 500
member in the source's own final row (dated 2026-06-30)` — i.e. restricted
to real, *currently-active* constituents, not the full historical roster
including long-delisted names. This is a mechanical, non-discretionary
cutoff (no stock-picking), chosen because most of the excluded names are
long-delisted/bankrupt/renamed-away and would either fail yfinance
resolution or risk resolving to an unrelated company that later reused the
same ticker symbol.

| Step | Count |
|---|---|
| Unique raw symbols anywhere in the source CSV (1996-01-02→2026-06-30) | 1,206 |
| Symbols with ≥1 window overlapping [2007-01-01, 2026-06-30] | 929 |
| Of those, currently active as of the source's 2026-06-30 row (the cutoff) | 503 |
| Overlap with the existing curated-40 `STOCK_MEMBERSHIP` | 39 of 40 (`GOLD` excluded — correctly, never a real member) |
| New candidates needing a fresh price fetch | 464 |
| Fetch outcome (`backfill_price_history`, 2007-01-01→present) | 464/464 wrote ≥1 row, 0 fetch failures |
| Excluded for insufficient history (`precompute_ticker()`'s own <300-row gate) | 3 (`FDXF`, `HONA`, `Q`) |
| **Final broadened universe** | **500 stocks + 33 unchanged ETFs = 533 tickers** |

Dot-tickers converted for yfinance (`BRK.B`→`BRK-B`, `BF.B`→`BF-B`); no
further rename-merging beyond the existing `_KNOWN_TICKER_RENAMES` (FB→META)
was attempted for the 464 new candidates — a stated, not exhaustively
verified, limitation. A company removed from the S&P 500 before
2026-06-30 (business decline, acquisition) is excluded even if it would
have contributed real in-window history — a direct consequence of the
"currently active" cutoff, not an oversight.

**Baseline reproduction, exact match (trusted before comparing further):**
unmodified clone, `run_full_validation(config, start=2007-01-01,
end=2026-07-30)` reproduced the already-documented figures exactly — 12,046
trades, TRAIN CAGR 13.23%, TEST CAGR 22.39%, 14/16 blocks (fail: blocks 0
& 1), walk-forward 82.1% (183/223), concentration 11.31%/11.28%, £ growth
train £227,036/£114,033 (strategy/S&P), test £188,662/£114,149.

**Full comparison — curated-40 (73 tickers total) vs. broadened (533),
same pinned `end=2026-07-30`, identical config/signal/crisis-override:**

| Metric | Curated-40 | Broadened | Direction |
|---|---|---|---|
| Total trades | 12,046 | 79,731 | 6.6× |
| TRAIN CAGR | 13.23% | 28.07% | higher (see caveat below) |
| TEST CAGR | 22.39% | 52.76% | higher (see caveat below) |
| TRAIN profit factor | 1.68 | 1.57 | **lower** |
| TEST profit factor | 1.74 | 1.51 | **lower** |
| TEST Sharpe | 0.576 | 0.507 | **lower** |
| Full-history PF (raw) | 1.71 | 1.54 | **lower** |
| Cost-sensitivity PF at 0/10/25bps | 1.71/1.55/1.34 | 1.54/1.40/1.21 | lower at every level |
| Per-block PF, worse in broadened vs. curated | — | **13 of 16 blocks** | see per-block table below |
| Concentration (full raw vs. ex-best CAGR) | 11.31% vs 11.28% | 21.20% vs 21.20% | negligible either way |
| Walk-forward success rate | 82.1% (183/223) | 83.0% (185/223) | marginally higher |
| Blocks passed / 16 (`_block_passes()`, unchanged formula) | 14/16 | **0/16** | **not a trading-quality signal — see caveat** |

Per-block profit factor (0-indexed, matching `make_blocks()`'s own
indexing): Block 0: 0.10→0.27, Block 1: 0.78→0.59, Block 2: 1.34→1.37,
Block 3: 1.43→1.39, Block 4: 2.37→2.13, Block 5: 2.31→2.09, Block 6:
1.92→1.75, Block 7: 1.48→1.31, Block 8: 2.93→2.32, Block 9: 1.37→1.22,
Block 10: 1.55→1.37, Block 11: 2.71→2.24, Block 12: 1.05→1.11, Block 13:
1.91→1.40, Block 14: 1.63→1.51, Block 15: 2.33→1.81 — only blocks 0, 2, and
12 improve; the other 13 decline, confirming the full-history PF drop is
broad-based, not one bad window.

**Position-cap caveat — why "0/16 blocks pass" is not a real finding.**
`analysis/backtest.py:run_candidate()`/`simulate_trades()` enforce **no**
cross-ticker cap at all (`trading212_invest.max_open_positions`=20,
`max_total_deployed`=0.5 exist only in live order placement, never in the
backtest) — every ticker is simulated independently against the same
shared *notional* `starting_capital_gbp` (£50,000) baseline. This was
already a known limitation at 73 tickers; at 533 it becomes severe, since
many more tickers can hold simultaneous positions with no aggregate
ceiling, driving `max_drawdown_pct` (computed as £ drawdown ÷ the fixed
£50,000 baseline) to implausible values (-154.78% to -325.22%) a real,
capped account could never reach. `_block_passes()`'s `dd ≤ 1.5 × sp_dd`
leg fails on every block purely because of this uncapped-exposure
artifact — **not** because the underlying trades got worse. Read the
profit-factor/Sharpe/walk-forward/cost-sensitivity figures above as the
trustworthy basis for the verdict below; do not read the block-pass count
as one.

**Trade composition — does broadening trade the same names, or genuinely
different ones?** Of 79,731 broadened-run trades: 3,962 in the unchanged
ETF sleeve (£73,025.73 total pnl_gbp), 8,084 in the curated-40 stocks
(£242,671.86 — byte-identical to the baseline run, since their data and
membership windows are unchanged), 67,685 in the 459 newly-traded
candidates (£1,412,167.20). Top-15-by-profit new names: MU, PWR, ORLY,
STX, AZO, CTAS, LRCX, KLAC — large, liquid, well-known S&P constituents,
not micro-caps or junk (the "currently active" cutoff mechanically
excludes anything below full S&P 500 membership). Bottom-15-by-profit are
also all new names (CSGP, MRVL, DOW, NCLH, LVS, PPL, ERIE, AMCR, MRNA,
DXCM, WY, AES, APO, SMCI, CRL) — skewing toward more volatile/story-stock
names the original curation implicitly avoided, though not by any rule
stated at curation time.

**Verdict: dilution, not amplification — and not a data-quality
artifact.** Profit factor and Sharpe decline consistently (full-history PF
1.71→1.54, TEST Sharpe 0.576→0.507, worse at every transaction-cost level,
worse in 13/16 individual blocks) — these are per-trade win/loss
statistics, not subject to the position-cap distortion above, so this is a
real signal. The trade-composition data rules out "it's just noise from
bad data": the new profitable names (MU, PWR, ORLY, STX, AZO, CTAS, LRCX,
KLAC) are genuine, liquid large-caps, not junk — so this isn't survivorship
or data-availability contamination. The mechanism is simply that the
12-month-momentum signal, applied to ~7× more real S&P 500 names, produces
a somewhat lower average edge per trade than the curated 40's specific
selection, while also pulling in a long tail of lower-quality names (SMCI,
MRNA, NCLH, LVS, DOW) that further drag on aggregate quality without
erasing the gains from the strong new additions. The curated 40 was chosen
for sector coverage/liquidity/risk-flag reasons, not backtested
performance (see "Adding assets to the watchlist" below for its own
selection rationale) — this study finds it happens to also land on
modestly higher trade-quality names than the broader mechanical S&P 500
roster does under this exact signal. **Not adopted**: broadening would
need either a genuine portfolio-level cap inside the backtest engine (to
make the drawdown/block-pass comparison trustworthy) and/or a quality
filter beyond raw index membership (the role the original curation was
implicitly already playing) before it would be worth revisiting.

Not committed as a regression test or config change — a one-off research
reproduction, matching this project's own established pattern for
rejected-sleeve studies (see "Sector-rotation MIXED-regime sleeve" and
"Mean-reversion MIXED-regime sleeve" above). Nothing in `analysis/`,
`brokers/`, `data/scheduler.py`, or `config.yaml` changed as a result.

### V2 moderate stock universe expansion (top-100/150 by market cap) — researched, not adopted (2026-08-01)

Direct follow-up to the full 500-stock broadening study immediately
above, testing whether a moderate expansion — top 100 by current market
cap, and separately top 150 — captures the genuine upside that study
found (Micron, O'Reilly, Lam Research, AutoZone, Cintas, Seagate, KLA,
Quanta Services) without the dilution its long tail of weak constituents
caused. Same isolated-clone discipline; this time the research clone was
deliberately kept until the full report below was drafted, then deleted
— satisfying this file's "Research reproducibility standard" without the
after-the-fact remediation the prior study needed.

**Universe construction.** Regenerated the prior study's exact 503-candidate
pool (`is_included(ticker) := window overlaps [2007-01-01, 2026-06-30] AND
active in the source's 2026-06-30 final row`, same `fja05680/sp500` CSV) —
reproduced identically (1,206 raw symbols → 929 overlapping → 503
currently-active). Ranked by current market cap (`yfinance
Ticker.fast_info.market_cap`, fetched 2026-08-01, 503/503 succeeded).
Top-100 floor: $121.1B (VRTX). Top-150 floor: $85.0B (CMCSA). Top-100 is a
strict subset of top-150.

**Curated-40 coverage — a genuine surprise, flagged as instructed:**
**ADBE and NEM both fall outside the top-100** by current market cap
(ranks 119/$99.5B and 120/$98.7B) despite being long-standing curated-40
members — edged out by ~20 names in the $100-130B band. Both are inside
top-150 (no curated-40 ticker falls outside top-150). So top-100's
"curated-40 sleeve" is actually only 37 of 39 real members; top-150
carries the full 39. New candidates needing a price backfill: 63 (top-100)
/ 111 (top-150, superset); 111/111 fetched successfully, 0 failures, and
unlike the full-500 study, 0 tickers were excluded for insufficient
history in either run (133/133 and 183/183) — market-cap-ranked names are
uniformly long-established. Ranking by *today's* market cap to pick a
2007-2026 backtest universe uses present-day information for historical
inclusion — the same hindsight-adjacent characteristic the prior study's
"currently active" cutoff already carried, not a new flaw here.

**Baseline reproduction, exact match:** unmodified clone reproduced
12,046 trades / TRAIN CAGR 13.23% / TEST CAGR 22.39% / 14/16 blocks (fail
0 & 1) / walk-forward 82.1% (183/223) — trusted before comparing further.

**Full comparison, `end=2026-07-30` throughout, trustworthy per-trade
metrics (not subject to the shared-notional-baseline distortion below):**

| Metric | Curated-40 (73) | Top-100 (133) | Top-150 (183) |
|---|---|---|---|
| Total trades | 12,046 | 22,803 | 30,784 |
| Train / Test win rate | 44.68% / 43.22% | 45.91% / 44.15% | 46.31% / 44.78% |
| Train / Test PF | 1.68 / 1.74 | 1.62 / 1.74 | 1.61 / 1.73 |
| Full-history PF | 1.71 | 1.67 | 1.66 |
| Train / Test Sharpe | 0.568 / 0.576 | 0.570 / 0.609 | 0.581 / 0.622 |
| Cost-sensitivity PF (0/10/25bps) | 1.71/1.55/1.34 | 1.67/1.52/1.32 | 1.66/1.51/1.31 |
| Walk-forward success rate | 82.1% (183/223) | 82.5% (184/223) | 83.4% (186/223) |

Sharpe, win rate, and walk-forward success rate all **modestly improve**
as the universe widens 40→100→150; full-history PF drifts down only
mildly (1.71→1.67→1.66) — a materially gentler pattern than the full
500-stock study's PF collapse to 1.54 with a 13/16-block decline.

**Distorted-by-the-same-known-artifact metrics, reported for completeness
only — do not treat as trustworthy:** Train/Test CAGR 13.23%/22.39% →
17.60%/33.10% (top-100) → 20.17%/38.73% (top-150); Full max DD -25.45% →
-50.72% → -67.92%; blocks passed/16: 14 → 3 → 1. Cause confirmed
identical to the full-500 study: `run_candidate()`/`simulate_trades()`
still enforce no cross-ticker cap, so every added ticker's independent
position sizes against the same shared £50,000 notional baseline. **New
this round — the drawdown/PF legs of `_block_passes()` were decomposed
separately**: profit factor alone clears >1.0 on the *identical* 14 of 16
blocks in all three universes (only blocks 0 and 1 fail on PF, always);
every block that flips pass→fail between curated-40 and top-100/150 does
so purely on the drawdown leg (`dd_ok`), never on `pf_ok`. This directly
confirms — not just asserts — that the block-pass-count collapse is an
artifact of the missing portfolio cap, not a trading-quality signal, at
any universe size tested so far.

**Trade composition — the core question.** Of the prior study's 8 named
winners (MU, LRCX, KLAC, STX, PWR, CTAS, ORLY, AZO): **4 captured in
top-100** (MU rank 13, LRCX 32, KLAC 47, STX 58), **5 in top-150** (+PWR,
rank 117) — CTAS/ORLY/AZO (ranks 156/175/242) remain excluded even at
top-150, simply not large-cap enough by current market cap despite strong
trading performance. Of the prior study's 15 named weak performers
(SMCI, MRNA, NCLH, LVS, DOW, PPL, ERIE, AMCR, DXCM, WY, AES, APO, CRL,
MRVL, CSGP): **14 of 15 excluded from top-150 entirely** (ranks
177-498) — the sole exception, MRVL (rank 73, $168.3B), is inside
top-100/150 and is indeed a small net loser there too (3 trades,
-£658.02). Actual trade-level results: top-100's 63 new (non-curated-40)
tickers traded 11,142 times for **+£283,219.04**, 61/63 net positive;
top-150's 110 new tickers traded 18,738 times for **+£483,266.47**,
106/110 net positive. Market-cap ranking is a real, if imperfect, quality
proxy — it reliably excludes the identified weak tail but also excludes
several genuine winners that simply aren't large-cap enough, and lets one
genuine large-cap loser (MRVL) through anyway.

**Verdict: a real but modest sweet spot — gentle dilution with a
favourable risk-adjusted trade-off, not an outright improvement over the
curated-40, and not an escape from the position-cap distortion.** Closer
to "capture the winners" than to "closer to 40" on the composition
evidence and on Sharpe/win-rate/walk-forward, but full-history PF does
still drift down (1.71→1.66) — dilution begins well before 500 stocks,
just far more gently than at full scale. **Not adopted**: the same
missing prerequisite as the full-500 study — a genuine cross-ticker
portfolio cap inside the backtest engine — would be needed before the
drawdown/block-pass side of this comparison could be trusted at any
universe size above the curated-40's own 73-ticker scope, and before
either expansion would be worth adopting outright rather than treating as
research.

Not committed as a regression test or config change — nothing in
`analysis/`, `brokers/`, `data/scheduler.py`, or `config.yaml` changed as
a result. Clone (`/root/v2_universe100_study`) deleted only after this
section's figures were fully drafted; production DB and both systemd
services confirmed untouched throughout (spot-checked zero new-ticker
rows in production, `git status` clean, service `ActiveEnterTimestamp`
unchanged across the task).

### Position-sizing caps (`max_position_gbp`/`max_open_positions`/`max_total_deployed`) — binding-frequency investigation (2026-08-01)

Investigation only, no config or code change. Asks two separate
questions about the three caps in the same VaR-sizing bundle inherited
unmodified from V1 into V2's `trading212_invest` section: (1) was any of
the three ever actually backtested/tuned, as distinct from
`var_confidence`/`var_lookback_days`/`max_risk_pct`, the other part of
the same sizing formula; (2) across the real validated backtest, how
often does each one actually bind (turn away a candidate/reduce a
position) versus sit unused as a rare backstop.

**Step 1 — never tested, confirmed directly from the record, not
inferred.** Every mention of `max_position_gbp`/`max_open_positions`/
`max_total_deployed` anywhere in this file is either descriptive of the
mechanism, or (see "V2 stock universe broadening" and "V2 moderate stock
universe expansion" above) a note that the backtest engine doesn't
enforce two of the three at all. `git log --all -p -- config.yaml`
confirms the current V2 values were set by direct judgment at the V1→V2
cutover, not a sweep — the commit's own inline comment states this
plainly: `max_open_positions: 20  # broader universe than either V1 path
(73 tickers vs 33/37) — a higher ceiling than V1's 10, still far below
"unlimited"`. V1's own history shows the same pattern one level further
back — `max_position_gbp` moved 500→200 (ETF→stock split) and
`max_open_positions` 10→5→10 across V1's lifetime purely via inline
reasoning comments, never a documented before/after backtest comparison
of one value against another. **None of the three has ever been swept,
compared, or backtested against an alternative, in either V1 or V2's
history.**

**Step 2 — methodology.** Single instrumented run against the current
live 73-ticker universe (curated-40 + 33 ETFs), `start=2007-01-01,
end=2026-07-30` (same window as every V2 validation to date), using
production data already in the DB — no new price fetch needed. Isolated
clone (`/root/cap-binding-study`, own copied DB, deleted immediately
after these figures were captured), `simulate_trades()`'s exact entry/
exit/sizing logic reproduced verbatim with three additions: (a) the raw,
pre-cap VaR position size — `raw_position_gbp = (starting_capital ×
max_risk_pct) / abs(holding_var)`, i.e. `_var_position_size_core()`'s own
formula before its `max(min_position_gbp, min(x, max_position_gbp))`
step — recorded alongside the actual (capped) `position_gbp` on every
trade; (b) an event-sweep over every trade's `(entry_date, exit_date,
position_gbp)` across all 73 tickers, incrementing/decrementing a running
open-position count and running deployed-£ total at each entry/exit, to
reconstruct concurrent exposure through time; (c) for every entry event
specifically, the running count/total *immediately before* that entry is
added, to measure how often the cap would already have been at or over
its limit at the moment a new candidate wanted in. Sanity check: this run
reproduced **exactly 12,046 trades**, matching the documented baseline —
trusted before reporting further.

Config values in effect: `starting_capital_gbp=£50,000`,
`max_open_positions=20`, `max_total_deployed=0.5` (=£25,000),
`max_position_gbp=£2,500`, `var_confidence=0.95`, `var_lookback_days=252`,
`max_risk_pct=0.01`, `min_position_gbp=£50`.

**Results — all three bind constantly, not rarely:**

| Cap | Metric | Result |
|---|---|---|
| `max_open_positions=20` | Days concurrent open positions > 20 | **4,097 / 4,401 (93.09%)** |
| | Max concurrent open positions ever reached | **70** (3.5× the cap) |
| | Entries where the cap was already ≥20 at the moment of entry | **11,483 / 12,046 (95.33%)** |
| `max_total_deployed=0.5` (£25,000) | Days deployed capital > cap | **4,268 / 4,401 (96.98%)** |
| | Max concurrent deployed capital ever reached | **£168,708.16 (337.4% of account)** |
| | Max / avg overage on a binding day | **£143,708.16 / £89,996.73** |
| | Entries where deployed capital was already ≥cap at the moment of entry | **11,860 / 12,046 (98.46%)** |
| `max_position_gbp=£2,500` | Trades where raw VaR size exceeded the cap before capping | **9,417 / 12,046 (78.18%)** |
| | Trades using the <60-sample fallback (not true VaR sizing) | 0 / 12,046 (0.00%) |
| | Median / mean / max raw-size-vs-cap ratio, capped trades only | **1.58× / 1.81× / 10.98×** |
| | ETF trades capped | 2,627 / 3,962 (66.30%) |
| | Stock trades capped | **6,790 / 8,084 (83.99%)** |

`max_open_positions` and `max_total_deployed` are, on this 73-ticker
universe, effectively **always** binding once the strategy has been
running a while, not occasional backstops — the uncapped-per-ticker
demand routinely runs to 3-7× either ceiling (this reuses the same
"no cross-ticker portfolio cap in `run_candidate()`/`simulate_trades()`"
fact already documented in the two universe-broadening studies above;
this investigation is the first to quantify exactly how far uncapped
demand actually runs past the live caps rather than just noting the caps
aren't modelled). `max_position_gbp` binds on close to 4 in 5 trades
overall, and **literally 100% of trades in the least-volatile curated-40
names** (WMT, COST, JNJ, PG all 100% capped; TMO/CVX/LLY/HD/MRK all
>94%) — for these tickers the £2,500 flat ceiling is the position size
in practice, and VaR sizing's own volatility-scaling has no effect on
them at all. The mechanism: a low-volatility stock has a small
`holding_var`, and `position_gbp = risk_budget / |holding_var|` grows
*larger*, not smaller, as volatility falls — so it's the calmest, not the
most volatile, names that hit the ceiling hardest. ETFs bind less often
than stocks (66.30% vs 83.99%) despite typically being lower-volatility
baskets — the `√holding_days` term in `_var_position_size_core()`'s
square-root-of-time scaling largely explains this: ETFs carry a 90-day
`max_holding_days` vs stocks' 30-day, so `√90≈9.49` vs `√30≈5.48` scales
their `holding_var` up more than their lower daily volatility scales it
down, more than offsetting the effect low volatility has on its own.

**Recommendation.** All three caps clear the "worth a full, properly
train/test-validated re-tuning round" bar Tasks 10-13 established for
`max_holding_days` — the opposite conclusion from a "rare backstop not
worth the effort." Unlike an occasional-backstop parameter, all three
here are essentially *always* the binding constraint under real VaR
demand on this exact universe: `max_open_positions`/`max_total_deployed`
cap ~95-98% of entries at the moment they fire, and `max_position_gbp`
caps ~78% of all trades outright (100% for several of the curated-40's
calmest, most liquid names). Because the caps bind this pervasively, the
*live* portfolio's real trade selection and sizing is currently being
shaped far more by these three fixed numbers than by the VaR formula
they sit on top of — a genuine re-tuning study here has much more
practical leverage than the same exercise would on a parameter that
rarely engages. Any such study would need the same missing prerequisite
already identified in both universe-broadening sections above — a
genuine cross-ticker portfolio simulation inside the backtest engine (the
current `run_candidate()`/`simulate_trades()` has none) — since without
it, changing any of these three caps cannot be backtested faithfully:
today's per-ticker-independent engine has no way to show what a
different cap would actually have allowed or blocked in a shared
account. Not attempted here — this section is Step 1/Step 2 only, no
parameter change proposed or made.

Investigation clone (`/root/cap-binding-study`) deleted only after these
figures were captured; production DB confirmed to have no trace of the
clone (`investment_research_capstudy` table absent from the production
file), `git status` clean on the live checkout, both systemd services'
`ActiveEnterTimestamp` unchanged throughout.

### Portfolio-capped backtest engine (2026-08-01)

Direct follow-up to the binding-frequency investigation immediately above,
which found `max_open_positions`/`max_total_deployed` bind on 93-98% of
days/entries even on the current 73-ticker universe under the existing
uncapped engine — meaning every backtest figure this file has published to
date (the "Current baseline" callout, every V2 phased-rebuild/validation
number, both universe-broadening studies) was generated by an engine that
never modelled the one constraint shaping live trade selection the most.
This section adds the missing mechanism as **permanent, tested,
production code** (not a disposable research clone — a departure from
every other backtest study in this file) and reports what it found on
first real use.

**Mechanism.** `analysis/backtest.py:simulate_portfolio(tickers_cache,
config, gspc_close)` walks every ticker together in one shared
chronological pass, enforcing `trading212_invest.max_open_positions` /
`max_total_deployed` as a single cap shared across the whole universe —
the cross-ticker constraint `run_candidate()`/`simulate_trades()` (left
completely unchanged; this is an additional, selectable mode) never
modelled at all, since every ticker there is simulated fully independently
against the same notional `starting_capital_gbp` baseline. Per-ticker
signal/exit/stop/VaR-sizing math is byte-identical to `simulate_trades()`
— only entries are additionally gated by shared portfolio state.

**Priority rule**, derived by reading `brokers/trading212.py`'s live
decide-then-submit pipeline directly (`maybe_place_orders()` /
`_submit_pending_orders()` / `_load_pending_orders()`), not assumed: on
any given day, (1) ETFs are prioritised before stocks — mirroring the
live 07:00 UTC ETF-submission-before-13:30-UTC-stock-submission ordering
— then (2) within each group, tickers are prioritised in the order they
appear in `tickers_cache` — mirroring config.yaml's `instruments:` dict
insertion order, which the live decide phase iterates and the live submit
phase's `ORDER BY decided_at ASC` ties break by (both alphabetical in the
current config.yaml, which is also the order
`analysis.universe.ALL_TICKERS` already produces). A cap binding stops
admitting further candidates for the rest of that day entirely (mirrors
`_submit_pending_orders()`'s `break`, not a per-candidate `continue`).

**Deliberate simplifications, stated explicitly, not silently:**
1. `account_value_gbp` for VaR sizing stays the fixed notional
   `backtest.starting_capital_gbp` — never compounding, identical to the
   uncapped engine — isolating the comparison to *only* the cap-enforcement
   difference, not a second change (a compounding account).
2. `max_total_deployed` is checked as "already at/over the cap BEFORE this
   candidate's own size is added" — not "would admitting this candidate
   push deployed capital over the cap" — exactly mirroring
   `_submit_pending_orders()`'s own check order: a single admitted order
   CAN push the running total past the cap; the next candidate is what
   actually gets blocked (see "V2 first-cycle deployment cap overshoot"
   above for the live incident this check shape was built to prevent).
3. Deployed capital is tracked as the sum of admitted trades' own
   committed `position_gbp` (cost basis at entry, incremented on entry,
   decremented by the same amount on exit) — not a continuous
   mark-to-market valuation the way T212's live
   `investments.currentValue` is; the backtest has no per-day valuation of
   every open position across tickers to mirror that field exactly.
4. A candidate rejected by either cap on a given day is simply not
   entered that day (skip, not queue/defer) — the closest daily-resolution
   analogue of a live `PENDING` row being retried at the next submission
   window, since this engine has no sub-daily submission windows to defer
   within.
5. A ticker can never exit and re-enter on the same day — mirroring
   `simulate_trades()`'s own per-ticker control flow exactly, where the
   `open_trade is None` entry check is evaluated once per row, using the
   state as of before that row. Entry candidacy each day is restricted to
   tickers already flat *before* that day's exits are processed — a
   position a same-day exit just freed is not eligible to re-claim its own
   slot until its own next trading day.

`max_position_gbp` is enforced exactly as before, inside
`_var_position_size_core()` itself — unaffected by any of the above.

Wired into `analysis/validation.py:run_full_validation(..., portfolio_mode:
bool = False)` — `False` (the default) reproduces every existing caller's
behaviour unchanged (the Backtest page, `analysis/dd_report.py`); `True`
swaps `run_candidate()` for `simulate_portfolio()`, with every other step
(train/test split, blocks, walk-forward, cost sensitivity, £ growth)
unchanged.

Covered by `tests/test_portfolio_backtest.py` (6 tests): `max_open_
positions` never exceeded plus the ETF-before-stock and input-order
priority rule (including the same-day-re-entry edge case above, where the
ticker that vacates a slot is *not* who reclaims it), `max_total_deployed`
blocking an entry independently of position count (and a generous-cap
control case confirming the block is genuinely caused by the cap), and —
the critical regression guard — `simulate_portfolio()` with both caps set
arbitrarily high reproduces `run_candidate()`'s trade list exactly (same
tickers, dates, prices, exit reasons, sizes, `pnl_pct`), both on synthetic
fixtures (with and without a crisis-override event) and re-confirmed
against real 2007-2026 production data (see below) — proving the new mode
introduces zero behavioural change when its caps don't bind.

### Portfolio-capped validation findings (2026-08-01)

Real caps (`max_open_positions=20`, `max_total_deployed=0.5`,
`max_position_gbp=2500` — the live `trading212_invest` config values), run
against an isolated server-side clone (own copied DB, deleted after this
section was drafted — see cleanup note below), `start=2007-01-01,
end=2026-07-30`, the identical window/signal/crisis-override every other
V2 validation figure in this file uses.

**Sanity check — confirmed exact on real production data, not just
synthetic fixtures.** `simulate_portfolio()` with both caps set
arbitrarily high (`max_open_positions=999999`, `max_total_deployed=999.0`)
reproduces `run_candidate()`'s real 73-ticker result exactly: 12,046
trades, TRAIN CAGR 13.23%, TEST CAGR 22.39%, 14/16 blocks — every reported
metric byte-identical between the two engines.

**The critical result — real caps on the 73-ticker (curated-40 + 33 ETF)
universe:**

| Metric | Uncapped (existing baseline) | Real caps (`max_open=20`, `max_deployed=50%`) |
|---|---|---|
| Total trades | 12,046 | **2,187** (-81.8%) |
| TRAIN CAGR | 13.23% | **3.14%** |
| TEST CAGR | 22.39% | **6.13%** |
| TRAIN profit factor | 1.68 | 1.49 |
| TEST profit factor | 1.74 | 1.87 |
| Full-history PF | 1.71 | 1.64 |
| TRAIN max drawdown | -25.45% | -9.57% |
| TEST max drawdown | -25.26% | -8.48% |
| Blocks passed / 16 | 14 | **13** (block 12 flips pass→fail; blocks 0 & 1 fail either way) |
| Walk-forward success rate | 82.1% (183/223) | 81.6% (182/223) |
| Cost-sensitivity PF (0/10/25bps) | 1.71/1.55/1.34 | 1.64/1.47/1.27 |
| TRAIN £50,000 → | £227,036 strategy vs £114,033 S&amp;P | **£72,839** strategy vs £114,033 S&amp;P |
| TEST £50,000 → | £188,662 strategy vs £114,149 S&amp;P | **£73,901** strategy vs £114,149 S&amp;P |

**Under real portfolio caps, the strategy underperforms buy-and-hold
S&amp;P 500 in £ terms in both windows** — the reverse of every previously
published comparison in this file, all of which used the uncapped engine.
PF/Sharpe/walk-forward hold up reasonably well under the caps (PF even
improves slightly in TEST, 1.74→1.87), but CAGR collapses to roughly a
quarter of its uncapped value: with only 20 concurrent slots and 50% of a
fixed-notional account available, the large majority of what the uncapped
engine treated as independent, fully-funded opportunities are simply never
taken. This is not a defect in the new mechanism — the huge-caps sanity
check above proves `simulate_portfolio()` reproduces the uncapped baseline
exactly when caps don't bind — it is the first genuine measurement of what
the live account's own configured caps do to this strategy's realistic
return, as opposed to the per-ticker-independent approximation every
number in this file predates.

**Top-100 / top-150 universes re-run under the identical real caps**, using
the same market-cap-ranked construction as "V2 moderate stock universe
expansion" above (freshly regenerated the same day — 503/503 candidates,
top-100 floor $118.8B, top-150 floor $82.8B, both close to but not
identical to that section's own floors, as expected from ordinary
intra-day market-cap drift; new-candidate counts matched exactly: 63 for
top-100, 111 for top-150). Uncapped reproduction first, as a trust check
(both closely match the already-documented figures — top-100: 23,144
trades / TRAIN 17.84% / TEST 33.27% / full PF 1.68, vs. the documented
22,803 / 17.60% / 33.10% / 1.67; top-150: 31,131 / 20.32% / 38.92% / 1.67,
vs. the documented 30,784 / 20.17% / 38.73% / 1.66) — before trusting the
new, real-cap figures:

| Metric | Curated-40 (73, capped) | Top-100 (133, capped) | Top-150 (183, capped) |
|---|---|---|---|
| Total trades | 2,187 | 2,246 | 2,245 |
| TRAIN / TEST CAGR | 3.14% / 6.13% | 2.64% / 5.73% | 2.42% / 5.96% |
| TRAIN / TEST profit factor | 1.49 / 1.87 | 1.40 / 1.77 | 1.33 / 1.84 |
| TRAIN / TEST win rate | — (not captured this run) | 36.0% / 37.2% | 35.5% / 37.9% |
| TRAIN / TEST Sharpe | — (not captured this run) | 0.300 / 0.493 | 0.260 / 0.520 |
| Full-history PF | 1.64 | 1.54 | 1.51 |
| TRAIN / TEST max drawdown | -9.57% / -8.48% | -16.68% / -9.37% | -20.68% / -8.99% |
| Blocks passed / 16 | 13 (0, 1, 12 fail) | 14 (0, 1 fail) | 14 (0, 1 fail) |
| Walk-forward success rate | 81.6% (182/223) | 78.5% (175/223) | 79.8% (178/223) |
| Cost-sensitivity PF (0/10/25bps) | 1.64/1.47/1.27 | 1.54/1.39/1.20 | 1.51/1.37/1.18 |

**The total trade count is almost completely flat across all three
universe sizes (2,187 → 2,246 → 2,245) despite the candidate pool growing
from 73 to 183 tickers (2.5×)** — direct, quantified confirmation that
`max_open_positions`/`max_total_deployed` are the dominant constraint on
trade count once real caps are applied, not universe breadth. A wider
universe mainly changes *which* 20-or-so slots get filled at any given
time (more candidates competing for the same fixed capacity), not *how
many* total positions the strategy can ever hold. Widening the universe
under real caps buys a small, genuine improvement on two fronts — full
PF/CAGR trend the "expected" direction relative to the trade-quality
findings already on record (this remains gentle dilution, matching the
top-100/150 uncapped study's own verdict, not reversed by capping) — and,
new here, **the block-pass count actually improves under caps as the
universe widens** (73-ticker capped: 13/16, failing blocks 0, 1, *and*
12; both 100/150-ticker capped runs: 14/16, only 0 and 1 fail, matching
the uncapped baseline's own two failures) — more candidates competing for
the same 20 slots gives the portfolio a better chance of finding a
qualifying trade in the one block (12) that the narrower 73-ticker
universe couldn't clear under real caps.

**Does the real-cap picture change the PF/Sharpe-only verdict already on
record? Materially, yes — but not in a direction that overturns the
"gentle dilution, real but modest sweet spot" conclusion; it sharpens what
that conclusion is actually worth in practice.** Under the uncapped
engine, widening from curated-40 to top-100/150 looked like a return
question (CAGR climbing sharply, 13.23%→17.84%/20.32%, alongside a mild PF
drift) with an admittedly-untrustworthy drawdown/block-pass side. Under
real caps, that entire CAGR story is moot — trade count barely moves, so
there is no meaningful extra return to capture from widening the universe
at all; a real account with these caps would place essentially the same
number of trades whether pointed at 73 or 183 candidates. What widening
*does* still buy under real caps is a modest, genuine derisking (the
extra block clears) and a slightly-worse full-history PF/win-rate profile
(the same gentle-dilution signal already on record) — i.e. the *return*
argument for widening the universe evaporates once portfolio caps are
modelled honestly, while the *dilution* argument survives essentially
unchanged. This is the first time either has been checked with a genuine
cross-ticker cap in the loop.

Both the top-100/150 studies above used a fresh, independently-refetched
market-cap ranking (not reused from the earlier, now-deleted research
clone) — see the exact figures above for how closely the two reproductions
matched despite the independent refetch, the same kind of ordinary
day-to-day drift already documented and accepted in this file's other
reproducibility checks.

Server-side clone (`/root/portfolio-cap-study`) deleted only after this
section's figures were fully drafted; production DB and both systemd
services confirmed untouched throughout (see cleanup note in the task
report this section was written from). Not committed as a config or
live-execution change — `trading212_invest`'s live caps, `analysis/
universe.py`'s curated-40 roster, and every scheduler/broker file are
unchanged; only `analysis/backtest.py`, `analysis/validation.py`, and the
new test file are new, uncommitted local changes pending review.

### Present-day market-cap ranking bias in the universe-broadening studies — investigated, not fixed (2026-08-01)

Both "V2 stock universe broadening" and "V2 moderate stock universe
expansion (top-100/150 by market cap)" above rank all ~503 point-in-time-
eligible S&P 500 candidates by **today's (2026) market cap** — a single,
static ranking applied uniformly across the entire 2007-2026 backtest
window. This is a second, subtler instance of the exact hindsight-bias
class the point-in-time *membership* correction already fixed once (see
"V2 architecture — phased rebuild, Stage 1" above) — one layer deeper:
not "was this company a member at the time" but "was this company
genuinely *large* at the time." Investigated for feasibility of a proper
fix and for how much it actually taints the findings already on record.
Investigation only — no backtest, universe, or config change made.

**Part 1 — historical point-in-time market-cap data: feasible, not free
of gaps.** No ready-made historical market-cap-ranking dataset exists
(checked directly: `fja05680/sp500`, the same source already used for
membership windows, has no market-cap or index-weight dimension, and no
sibling dataset from the same author or elsewhere adds one). The
components to reconstruct it are free and were verified live, not
assumed:

- **SEC EDGAR's XBRL company-concept API**
  (`data.sec.gov/api/xbrl/companyconcept/{CIK}/dei/EntityCommonStockSharesOutstanding.json`,
  free, no key, requires only a compliant `User-Agent` header — a plain
  `curl` succeeds where an unheadered `WebFetch` gets a 403) returns a
  company's full quarterly shares-outstanding history in one call, sourced
  from actual 10-Q/10-K cover-page disclosures. Verified against a known
  fact rather than trusted blind: NVDA's own series shows shares
  outstanding jumping 623M→2.50B (**4.01×**) exactly at the July 2021 4:1
  split, and 2.46B→24.53B (**9.97×**) exactly at the June 2024 10:1
  split — both dates and ratios match the real corporate actions.
- **Gap: real structured coverage only starts ~2009, not 2007.** The
  SEC's XBRL mandate phased in by filer size — worldwide public float
  ≥$5B (essentially this project's own target universe) had to start
  filing structured data mid-2009; smaller filers not until mid-2011.
  NVDA's own earliest structured record is 2009-01-25/2009-08-17
  depending on tag. **2007–mid-2009 (~13% of the 19-year backtest window)
  has no free structured source** — only unstructured pre-XBRL filing
  text, which would need scraping rather than a clean API call.
- `yfinance.get_shares_full()` was checked and ruled out as the primary
  source — confirmed via its own GitHub issue tracker to be annual-only
  (not quarterly) with documented duplicate-value bugs on some tickers
  and undocumented per-ticker depth, not something to build 2007-era
  figures on.
- **Scope to build it**: map ~500 candidates to CIKs via SEC's free
  `company_tickers.json`, pull one `companyconcept` call per ticker
  (~500 calls, minutes at SEC's ~10 req/sec polite limit), forward-fill
  each ticker's quarterly shares-outstanding between filing dates, and
  join against price history already stored in `price_data` to get
  `market_cap(ticker, date) = price(date) × shares_outstanding_as_of(date)`
  at any historical date, point-in-time correct by construction. Estimated
  **1-3 days of engineering** for the 2009-2026 core; the pre-2009 stretch
  is a separate, smaller, optional add-on (or can be excluded from any
  cap-based cutoff, which is defensible — it's the shortest and least
  AI-boom-distorted segment of the window anyway). Paid institutional
  sources (Sharadar via Nasdaq Data Link, CRSP, Compustat) exist and would
  also work, but are unnecessary given SEC EDGAR covers this for free.

**Part 2 — materiality: real, not a footnote.** Real historical year-end
market-cap figures (stockanalysis.com, cross-checked against the
underlying page data) were pulled for the 8 "named winner" tickers the
broadening/expansion sections credit with driving their reported CAGR
uplift:

| Ticker | 2007 | 2015 | 2019 | 2025 | Pattern |
|---|---:|---:|---:|---:|---|
| LRCX | $5.8B | $12.6B | $42.4B | $215.0B | mid-cap through 2018, mega-cap only from 2019 |
| MU | $5.5B | $15.4B | $59.7B | $321.2B | cyclical mid-cap through 2018, durably mega-cap only since ~2024 |
| KLAC | $8.8B | $10.8B | $28.1B | $159.7B | mid-cap through 2018, mega-cap only in the last ~3 years |
| STX | $13.6B | $11.0B | $15.6B | $58.8B | never plausibly top-100-caliber before ~2025 |
| PWR | $4.5B | $3.1B | $5.8B | $62.9B | small-cap for essentially all of 2007-2019, ~10x in 5 years on AI-data-center buildout |
| CTAS | $5.3B | $9.8B | $27.8B | $75.6B | steady compounder, no discontinuous jump |
| ORLY | $3.7B | $25.0B | $33.2B | $77.0B | steady compounder, no discontinuous jump |
| AZO | $7.6B | $22.4B | $28.1B | $56.2B | steady compounder, no discontinuous jump |

Against today's own top-100 floor (~$119-121B, per the expansion study's
own figures): **5 of the 8 named winners — LRCX, MU, KLAC, STX, PWR —
were 1-2 orders of magnitude below that for most of 2007-2019**, only
becoming genuinely large-cap in the last 2-6 years, concentrated in the
2023-2026 AI/semicap-equipment supercycle — the same shape as NVDA's own
trajectory ($7-18B through 2007-2015, mega-cap only from ~2017), just
landing on different tickers. The top-100 study's own text names 4 of
these (MU rank 13, LRCX rank 32, KLAC rank 47, STX rank 58) among the
tickers it credits with the reported CAGR uplift (13.23%→17.84% train,
22.39%→33.27% test, uncapped engine) — this isn't incidental exposure,
it's the specific mechanism the headline number rests on. CTAS/ORLY/AZO,
by contrast, were already solidly large/mid-cap well before the test
period — a much milder version of the same issue. NVDA itself is not a
named contributor in these studies — it sits inside the hand-curated 40
(picked by sector-coverage/liquidity criteria, not by any market-cap
ranking), and its trades are reported unchanged across every universe-size
variant, so this specific bias doesn't touch NVDA's own figures here even
though the underlying mechanism is identical.

**A partial, unrelated mitigant already on record — stated for
completeness, not as a reason to discount the finding.** "Portfolio-capped
validation findings" above independently found that once real
`max_open_positions`/`max_total_deployed` caps are modelled, the CAGR
uplift from widening the universe mostly evaporates anyway (trade count
barely moves: 2,187→2,246→2,245 across curated-40/top-100/top-150) — so
the specific claim this ranking bias would most inflate is already flagged
as not practically actionable, for an unrelated reason. That doesn't clean
up the PF/Sharpe/win-rate comparisons presented as the trustworthy side of
the same studies, though — those are still partly built on these same
tickers' pre-mega-cap years, when their liquidity, volatility regime, and
investor base were meaningfully different from today.

**Conclusion.** The "V2 stock universe broadening" and "V2 moderate stock
universe expansion" sections' reported upside should be read with this
caveat attached, not treated as a settled result — the tickers most
responsible for their headline gains are also the tickers most exposed to
this exact bias. A proper fix (point-in-time market-cap ranking, 1-3 days
per the scope above) has not been built; until it is, treat "top-100/150
by market cap" in this codebase's history as "top-100/150 by **2026**
market cap, applied retroactively," not a genuine point-in-time
ranking — the same caveat this file already states plainly for every
other in-sample finding per the Research reproducibility standard.

Investigation only, no research clone created or deleted — all evidence
above (SEC EDGAR live queries, stockanalysis.com market-cap series) was
gathered directly against public sources, not a local backtest run.
Nothing in `analysis/`, `config.yaml`, or any scheduler/broker file
changed as a result.

### Compounding portfolio backtest (2026-08-01)

**Part A — confirmed, not a live-trading gap.** `simulate_portfolio()`
(the shared-account cap-enforcement mode added earlier the same day —
see "Portfolio-capped backtest engine" above) shares `current_open`/
`current_deployed` running state across tickers, but sizes and cap-checks
every entry against the **fixed** `backtest.starting_capital_gbp`
constant throughout its whole run — it never compounds. Read directly
before building anything further: `brokers/trading212.py`'s live path
already compounds correctly, by construction — `maybe_place_orders()`
(the decide phase, line ~1508) and `_submit_pending_orders()` (the
submission phase, line ~1694) both call `client.get_account_summary()`
fresh on every invocation and pass `total_val = float(summary.get(
"totalValue", 0) or 0)` — T212's real, current account balance — directly
into `_var_position_size_core()` as `account_value_gbp`. There is no
notional reference figure anywhere on the live path to go stale; it asks
the broker what the account is actually worth right now, every time. **The
compounding gap that follows is backtest-methodology-only.**

**Mechanism.** `analysis/backtest.py:simulate_portfolio_compounding(
tickers_cache, config, gspc_close, per_trade_pct, max_deployed_pct)` is a
third, additional, selectable simulation mode — `run_candidate()`/
`simulate_trades()` and `simulate_portfolio()` are both left completely
unchanged. Reuses `simulate_portfolio()`'s exact structural skeleton
(day-by-day merged walk, Phase A exits before Phase B entries, the
"flat before today's exits are processed" same-day-no-reentry rule, the
ETF-before-stock-then-input-order entry priority) and its exact per-
ticker exit/stop/crisis logic verbatim. Two parameters replace the old
three (`max_position_gbp` flat-£, `max_open_positions` count,
`max_total_deployed` %):

- `per_trade_pct` — the VaR sizing ceiling for a new entry, as a fraction
  of the **current** (not starting) balance:
  `dynamic_max_position_gbp = current_balance * per_trade_pct`, fed into
  the unmodified `_var_position_size_core()` formula (`account_value_gbp`
  is also `current_balance`).
- `max_deployed_pct` — blocks further entries once `current_deployed /
  current_balance >= max_deployed_pct` — a compounding denominator,
  unlike `simulate_portfolio()`'s fixed one.

No `max_open_positions` cap at all — position count is an **emergent**
consequence of `per_trade_pct` and `max_deployed_pct` together (roughly
`max_deployed_pct / per_trade_pct` positions at full deployment), not a
third, independently-enforced rule. `current_balance` updates the moment
each Phase A exit is processed (`pnl_gbp` added immediately), so a later
same-day exit/entry sees the balance already moved; Phase B itself never
changes `current_balance` (only `current_deployed`), so it is stable
across the whole of one day's candidate-admission loop. A non-positive
`current_balance` on any day skips Phase B entirely for that day (no
exception, no divide-by-zero, no guessed size) — Phase A always still
runs. Wired into `analysis/validation.py:run_full_validation(...,
per_trade_pct=None, max_deployed_pct_compounding=None)` — both `None`
(the default) reproduces every existing caller's behaviour unchanged;
supplying both switches the engine to `simulate_portfolio_compounding()`,
taking priority over `portfolio_mode` if both were somehow passed
together (the three simulation engines are mutually exclusive, not
stackable).

Covered by 5 new tests in `tests/test_portfolio_backtest.py` (11 total in
that file after this addition): a winning first trade demonstrably grows
a later, independent ticker's position size (and the reverse for a
losing first trade); the deployment cap's denominator is provably the
grown balance, not the starting notional (constructed so a fixed-
denominator bug would flip the test from pass to fail); no explicit
position-count cap exists even when `trading212_invest.max_open_positions`
is configured low (10 tiny positions open concurrently against a
configured cap of 2); and a deliberately pathological single trade (an
extreme `per_trade_pct` combined with a crisis-forced exit at a
synthetic, deeply negative close) drives the balance negative with no
exception and confirms every later candidate is cleanly skipped. Full
local suite: 231 passed (226 pre-existing + 5 new).

**Sweep — 20 combinations, full grid, no reduction needed** (each run
completes in ~9-14s once the 73-ticker cache is precomputed once and
reused — the expensive part is identical across every combo). Isolated
server-side clone (`/root/compounding_sweep_clone`, own copied DB,
deleted after this section was drafted), `start=2007-01-01,
end=2026-07-30` — the identical window every other V2 validation figure
in this file uses. `per_trade_pct ∈ {2%, 3%, 5%, 7%, 10%}`,
`max_deployed_pct ∈ {50%, 65%, 75%, 90%}`:

| per_trade | max_deployed | Trades | TRAIN CAGR | TRAIN DD | TRAIN PF | TEST CAGR | TEST DD | TEST PF | Blocks | Failing blocks | WF % | Max concurrent |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2% | 50% | 5,446 | 5.52% | -12.00% | 1.62 | 14.44% | -13.89% | 1.86 | 13/16 | 0,1,12 | 83.9 | 27 |
| 2% | 65% | 7,355 | 7.64% | -16.38% | 1.63 | 22.67% | -28.08% | 1.86 | 13/16 | 0,1,12 | 82.1 | 35 |
| 2% | 75% | 8,680 | 9.08% | -22.06% | 1.64 | 27.77% | -37.56% | 1.81 | 10/16 | 0,1,12,13,14,15 | 81.6 | 41 |
| 2% | 90% | 10,336 | 11.00% | -30.05% | 1.68 | 34.70% | -53.07% | 1.77 | 9/16 | 0,1,9,12,13,14,15 | 81.6 | 49 |
| 3% | 50% | 3,353 | 3.92% | -12.06% | 1.48 | 11.53% | -10.69% | 1.89 | 13/16 | 0,1,12 | 79.4 | 23 |
| 3% | 65% | 4,572 | 6.70% | -18.90% | 1.60 | 19.26% | -23.62% | 1.91 | 13/16 | 0,1,12 | 82.1 | 29 |
| 3% | 75% | 5,511 | 8.24% | -22.20% | 1.61 | 25.68% | -36.08% | 1.91 | 12/16 | 0,1,12,15 | 83.9 | 33 |
| 3% | 90% | 6,803 | 10.24% | -31.25% | 1.62 | 33.41% | -54.32% | 1.86 | 9/16 | 0,1,8,9,12,13,14 | 82.5 | 38 |
| **5%** | **50%** | **2,180** | **3.27%** | **-11.57%** | **1.46** | **9.85%** | **-12.81%** | **1.90** | **14/16** | **0,1** | **83.4** | **20** |
| 5% | 65% | 2,812 | 5.20% | -18.24% | 1.52 | 15.79% | -21.38% | 1.92 | 13/16 | 0,1,12 | 80.7 | 26 |
| 5% | 75% | 3,272 | 6.31% | -22.23% | 1.53 | 19.89% | -23.93% | 1.92 | 12/16 | 0,1,8,12 | 81.2 | 30 |
| 5% | 90% | 4,043 | 8.16% | -33.36% | 1.56 | 25.71% | -46.74% | 1.88 | 8/16 | 0,1,7,8,9,12,13,15 | 79.8 | 34 |
| 7% | 50% | 1,804 | 2.81% | -13.90% | 1.39 | 7.60% | -15.97% | 1.71 | 13/16 | 0,1,12 | 76.7 | 18 |
| 7% | 65% | 2,305 | 4.51% | -19.63% | 1.50 | 14.97% | -22.05% | 2.03 | 13/16 | 0,1,12 | 82.1 | 24 |
| 7% | 75% | 2,672 | 5.64% | -21.36% | 1.51 | 17.85% | -22.72% | 1.96 | 13/16 | 0,1,12 | 80.7 | 27 |
| 7% | 90% | 3,268 | 6.56% | -26.83% | 1.45 | 24.43% | -28.11% | 2.02 | 10/16 | 0,1,8,12,13,15 | 79.4 | 32 |
| 10% | 50% | 1,563 | 1.59% | -14.11% | 1.23 | 6.11% | -14.53% | 1.68 | 12/16 | 0,1,9,12 | 70.9 | 16 |
| 10% | 65% | 2,079 | 3.34% | -18.21% | 1.36 | 11.79% | -15.44% | 1.90 | 12/16 | 0,1,9,12 | 78.9 | 22 |
| 10% | 75% | 2,393 | 4.09% | -20.00% | 1.39 | 14.18% | -22.35% | 1.91 | 13/16 | 0,1,12 | 79.4 | 25 |
| 10% | 90% | 2,890 | 5.50% | -24.89% | 1.43 | 20.23% | -28.78% | 1.93 | 12/16 | 0,1,8,12 | 77.6 | 30 |

`0,1` are the canonical failing blocks the uncapped, non-compounding
`run_candidate()` baseline also fails (GFC pre-crash and GFC-acute — see
"Block pass/fail formula reconciliation" above); every combo above fails
at least those two, and only `per_trade=5%, max_deployed=50%` fails
**exactly** those two and no others — the sole combination in the grid
that reproduces the established 14/16 failure pattern precisely rather
than adding new casualties. Sharpe ratios (not shown in the table above,
in the raw sweep JSON): highest test Sharpe in the grid is `2%/65%`
(train 0.506, test 0.600); `5%/50%` sits mid-pack (train 0.335, test
0.554).

**£ growth vs. buy-and-hold S&amp;P 500, same starting £50,000, same
train/test windows every other V2 figure in this file uses** (S&amp;P:
£114,033 TRAIN, £114,149 TEST — unchanged across every row, since the
benchmark leg is independent of these parameters): only the loosest
`max_deployed_pct` settings (75-90%, at every `per_trade_pct`) clear the
S&amp;P bar in TRAIN; every 50-65% setting underperforms buy-and-hold in
£ terms in **both** windows (e.g. `5%/50%`: £73,961 TRAIN / £92,643 TEST
vs. £114,033 / £114,149 S&amp;P). This mirrors, and is roughly the same
order of magnitude as, the fixed-notional `simulate_portfolio()` real-cap
result already on record above (£72,839 TRAIN / £73,901 TEST under
`max_open=20, max_deployed=50%`) — tightly-capped realistic sizing
underperforms passive S&amp;P exposure in £ terms under both the fixed
and the compounding engine; only materially looser deployment recovers
outright £ outperformance, and only at the cost of drawdown and fewer
passing blocks (75-90% settings drop to 8-10/16 blocks, adding failures
in blocks 7-9 and 13-15 beyond the canonical 0/1).

**Part D — VaR-differentiation check, three candidates**, each
instrumented to record the uncapped VaR-desired size
(`(current_balance × max_risk_pct) / abs(holding_var)`) against the
dynamic cap (`current_balance × per_trade_pct` at time of entry) for
every VaR-priced entry (fallback-path entries, <60 days of prior history,
excluded — they were never VaR-sized to begin with):

| Combo | VaR-priced entries | Capped | Capped % |
|---|---|---|---|
| **A: 5% / 50%** (14/16 blocks, canonical failure pattern) | 2,180 | 1,457 | **66.8%** |
| B: 2% / 50% (best CAGR-to-DD ratio) | 5,446 | 5,405 | **99.2%** |
| C: 2% / 65% (highest test Sharpe) | 7,355 | 7,310 | **99.4%** |

**B and C do not fix the flat-cap distortion — they reproduce it almost
exactly, just at a different number.** At `per_trade_pct=2%`, the ceiling
sits so far below what 1% `max_risk_pct` VaR sizing wants for most
tickers that capping is near-universal (99%+) and largely **undifferentiated
by volatility** — even genuinely volatile names (AMD 97-98%, SOXX
98%, TSLA 100%) get capped almost as often as calm ones (GLD, IEF, TLT,
XLP, XLU all 100%). Position sizing under B/C is effectively dictated by
the flat percentage, not the risk model — the same structural problem the
original flat-£2,500-cap investigation found, just parameterised
differently.

**A shows genuine, partial differentiation.** At `per_trade_pct=5%`,
overall capping drops to 66.8% and spreads across a real range: 0% for
`NVDA`/`AMD`, 8.7% `SLV`, 14.3% `NEM`, 20% `SOXX`, 33.3% `AMZN`/`FXI`,
40-44% `JPM`/`KBE`/`CAT` — clear volatility-linked differentiation absent
from B/C. But the calmest names in the universe — `IEF`, `TIP`, `LQD`,
`MUB` (bonds), `COST`, `JNJ`, `WMT` (staples), plus several other
low-beta names (`CVX`, `LLY`, `MRK`, `LIN`, `DIS`, `RTX`, `XOM`) — are
still capped **100% of the time**, identical in kind to the original
flat-£ finding (`WMT`/`COST`/`JNJ`/`PG` all 100% capped there too). A
spot-check at `per_trade_pct=10%` (not one of the three graded
candidates, run only to confirm the trend) drops overall capping to
20.3%, with capping now concentrated almost exclusively in fixed-income
names (`MUB` 100%, `TIP` 95.5%, `LQD` 95.2%, `IEF` 93.7%, `EMB` 91.4%,
`HYG` 74.6%) while most equities show 0%.

**Answer: partially, not fully — and the fix is continuous, not binary.**
Raising `per_trade_pct` relative to `max_risk_pct` genuinely reduces and
differentiates capping (66.8% at 5% → 20.3% at 10%), but at every level
tested, the lowest-volatility bond/staple names remain capped at or near
100% — a percentage-of-balance ceiling cannot fully escape this while
`max_risk_pct` (1%) stays fixed, because VaR sizing structurally wants
*more* £ exposure, not less, as volatility falls. The distortion shrinks
continuously as the ceiling loosens; it never fully disappears within the
tested range, and loosening it (as the sweep table above shows) trades
directly against drawdown and block-pass count.

**Recommendation: `per_trade_pct=5%, max_deployed_pct=50%` is the
combination worth adopting, if any — with real reservations, not an
unqualified endorsement.** It uniquely reproduces the established 14/16-
block, exactly-blocks-0-and-1 failure pattern (every other combo in the
grid adds new failing blocks beyond that canonical pair), sits at or near
the lowest drawdown in its own per_trade_pct bracket (TRAIN -11.57%, TEST
-12.81%), and shows the clearest genuine VaR-driven size differentiation
of the three graded candidates (66.8% capped, spread 0-100% by
volatility, vs. B/C's near-uniform 99%+). Max concurrent positions at
this setting is **20** — meaningfully more diversified across
simultaneous names than the fixed-notional real-cap mode's implicit
concentration, and comfortably inside what the crisis override (a single
force-exit-everything circuit breaker on a ≥7% S&amp;P single-day
decline) can act on cleanly in one day; a config admitting 40-50
concurrent positions (the 75-90% deployment settings) would mean a crisis
day forces a much larger simultaneous liquidation, with correspondingly
more slippage/execution risk the backtest itself cannot model.

The reservations: (1) TRAIN/TEST CAGR (3.27%/9.85%) is real but modest —
both windows underperform buy-and-hold S&amp;P 500 in £ terms, matching
the fixed-notional real-cap finding already on record above, so this is
not a "beats the market" result under realistic caps in either engine;
(2) the calmest, most defensive names in the universe are still sized
almost entirely by the flat ceiling rather than genuine risk-scaling —
the VaR formula's own volatility-awareness is largely inert for bonds and
staples at this setting, exactly as it was under the flat-£ cap; (3) nothing
here re-derives `max_risk_pct` itself (held fixed throughout this sweep at
the existing config default) — a joint sweep of `max_risk_pct` alongside
`per_trade_pct` might resolve the bond/staple saturation more cleanly
than raising `per_trade_pct` alone, and was not attempted here. If return
is prioritised over drawdown/block-count/diversification, the sweep table
shows a continuum of looser alternatives (up to `2%/90%`'s 34.70%
TRAIN CAGR) — none of which preserve the canonical block-failure pattern
or this candidate's concurrent-position discipline.

Investigation clone (`/root/compounding_sweep_clone`) deleted only after
this section's figures were captured; production confirmed unaffected —
`git status` clean on the live checkout (HEAD unchanged at the same
commit throughout), production DB checksum unchanged, both systemd
services' `ActiveEnterTimestamp` unchanged throughout. `analysis/backtest.py`,
`analysis/validation.py`, and `tests/test_portfolio_backtest.py`
remain new, uncommitted local changes pending review, alongside this
section.

### Live execution switched to the compounding two-parameter scheme (2026-08-01)

Direct follow-up to the two sections immediately above — the research was
adopted into real, permanent code the same day, not left as a backtest-only
finding. Two related but distinct problems are closed together, because
they shared one root cause: live order placement and the backtest engine
each grew their own answer to "how much capital does one trade get," and
those answers had silently diverged.

**Part A confirmed first, independently, before changing anything.** Live
order placement (`brokers/trading212.py`) already compounds correctly —
`maybe_place_orders()` and `_submit_pending_orders()` both call
`client.get_account_summary()` fresh on every invocation and pass
`total_val = summary["totalValue"]` — T212's real, current account balance
— directly into `_var_position_size_core()` as `account_value_gbp`. There
was never a live-trading bug here; the gap was entirely that the *backtest*
engine (`analysis/backtest.py:simulate_portfolio()`, this file's own
"Portfolio-capped backtest engine" section above) sized against a fixed
notional constant instead of a moving balance, so nothing had ever measured
live and backtest against the same yardstick.

**What changed, concretely:**

1. **`config.yaml`**: `trading212_invest.max_position_gbp` and
   `max_open_positions` are **removed entirely** — neither key exists in
   the file any more, and nothing reads them from config. `per_trade_pct:
   0.05` is new; `max_total_deployed: 0.5` is unchanged (its meaning was
   already "percentage of the account's current value," so it needed no
   rename — see Part A above for why that was already correct).
2. **`brokers/trading212.py`** — `maybe_place_orders()` (decide phase) now
   computes `dynamic_max_position_gbp = total_val * per_trade_pct` and
   feeds that into `_var_position_size_core()` in place of the old fixed
   `max_position_gbp`. This is a **ceiling on the VaR-computed size, not a
   target** — `_var_position_size_core()`'s own formula (`analysis/
   volatility.py`) computes the risk-based size first and only then applies
   `min(position_gbp, max_position_gbp)`; `per_trade_pct` never replaces
   VaR sizing, it only ever caps it down when VaR wants more. The <60-day
   fallback (`round(dynamic_max_position_gbp * 0.5, 2)`) scales the same
   way. `_submit_pending_orders()` (submission phase) had its entire
   `max_open_positions` cap-check block and `current_open` counter removed
   — position count is now an emergent consequence of `per_trade_pct` and
   `max_total_deployed` together, exactly matching
   `simulate_portfolio_compounding()`'s own design (see "Compounding
   portfolio backtest" above). The `max_total_deployed` check itself is
   unchanged — it was already checking `invested / total_val`, a
   compounding ratio, so Part A's finding meant this half of the mechanism
   needed no code change at all, only the documentation to say so plainly.
3. **`analysis/validation.py:run_full_validation()`** — **the default
   behaviour of a bare call changed**, closing the gap the task set out to
   close: no flags passed now runs `simulate_portfolio_compounding()` with
   `per_trade_pct`/`max_total_deployed` read straight from
   `config["trading212_invest"]` — the identical two keys, the identical
   values, live order placement reads. The Backtest page and the DD report
   generator (`analysis/dd_report.py`) both call `run_full_validation(config)`
   bare and needed no changes themselves to pick this up. Three explicit
   overrides exist purely to reproduce specific figures already published
   in this file on demand, never as a default: `uncapped=True` forces the
   original `run_candidate()` (reproduces the "Current baseline" figures);
   `portfolio_mode=True` forces `simulate_portfolio()` (reproduces
   "Portfolio-capped validation findings"); explicit `per_trade_pct`/
   `max_deployed_pct_compounding` arguments (the research-sweep interface)
   still work exactly as before, taking priority over config when both are
   supplied together.
4. **Historical reproducibility, preserved despite the config keys being
   gone.** `simulate_trades()`'s and `simulate_portfolio()`'s own
   `max_position_gbp`/`max_open_positions` fallback defaults (previously
   500/999 — generic placeholder values, never meant to be load-bearing
   while the config keys existed) are now hardcoded to the real historical
   values, **2500** and **20**, directly in `analysis/backtest.py` — so
   `run_full_validation(uncapped=True)` and `run_full_validation(
   portfolio_mode=True)` keep reproducing their respective published
   figures exactly, with no dependency on config.yaml carrying keys that
   are no longer part of the live, forward-looking configuration.
5. **`analysis/dd_report.py`**'s "Position Sizing, Portfolio Constraints &
   Exits" section now reads `result["engine_used"]`/`result["per_trade_pct"]`/
   `result["max_deployed_pct"]` and states the real, live-matching
   methodology (percentage-of-current-value ceiling and deployment cap,
   position count emergent) whenever the result came from either
   compounding variant — falling back to the old static VaR-only wording
   only for a report generated from one of the two explicitly-selected
   legacy engines, so the report can never misdescribe which methodology
   actually produced the figures it's showing.
6. **`main.py`**'s Status page T212 Invest Account badge now reads
   `per_trade_pct` instead of `max_position_gbp`/`max_open_positions`,
   showing "max N% of account/order · max M% deployed (position count is
   emergent — no separate cap)".

**Verified three ways before trusting this, not assumed:**

- **Full local suite passes** (238 tests, `pytest` from the project root)
  — new coverage added specifically for this change: dynamic per-trade
  sizing scaling exactly with a mocked account's `totalValue` (doubling
  the account exactly doubles the queued `position_gbp`, via the
  deterministic <60-day fallback formula, isolating the ceiling from any
  real VaR computation), confirmation that many pre-existing open
  positions no longer block new submissions (only the deployment cap
  does), the engine-selection priority order in `run_full_validation()`
  (five tests — default, config-absent fallback to 5%/50%, and all three
  explicit overrides, each asserting the correct underlying function was
  called with the correct arguments), and the DD report's methodology
  text correctly reflecting whichever engine actually produced a given
  result.
- **A bare `run_full_validation(config)` call, run against real 2007-2026
  production data in an isolated server clone (own copied DB, deleted only
  after these figures were captured, production never touched), exactly
  reproduced today's already-documented 5%/50% sweep result**: 2,180
  trades, TRAIN CAGR 3.27%, TEST CAGR 9.85%, 14/16 blocks with only 0 and
  1 failing, walk-forward 83.4%, £73,961/£92,643 train/test £ growth — a
  byte-for-byte match, confirming the new default genuinely resolves to
  the validated scheme rather than merely appearing to.
- **Both legacy explicit overrides, run against the same production data
  in the same clone, exactly reproduced their own already-published
  figures** despite `max_position_gbp`/`max_open_positions` no longer
  existing in config.yaml: `uncapped=True` → 12,046 trades / TRAIN 13.23%
  / TEST 22.39% / 14/16 blocks (the "Current baseline"); `portfolio_mode=True`
  → 2,187 trades / TRAIN 3.14% / TEST 6.13% / 13/16 blocks failing 0,1,12
  (the "Portfolio-capped validation findings" figures) — confirming the
  hardcoded historical fallback defaults in `analysis/backtest.py` (point 4
  above) do their job correctly.

**Not done as part of this change**: `trading212_invest.live` remains
`false` (unaffected either way — this change alters sizing/cap logic, not
the live/demo gate); no change to the crisis override, exit priority,
momentum signal, or universe-membership gating, all of which were already
identical between live and backtest and remain so. The reservations
already on record in "Compounding portfolio backtest" above still apply in
full now that this is live — most importantly, that both windows
underperform buy-and-hold S&P 500 in £ terms under this scheme, and the
calmest, most defensive names in the universe are still sized almost
entirely by the flat `per_trade_pct` ceiling rather than genuine
risk-scaling. Adopting this was a deliberate choice to make the live
system's real behaviour match its own validated research rather than
continuing to run on parameters nobody had actually re-derived since V1,
not a claim that 5%/50% is a fully solved, final answer.

### Trade prioritization and capacity-allocation engine (2026-08-01)

Direct follow-up to "Live execution switched to the compounding
two-parameter scheme" immediately above — that work closed the sizing/cap
gap between live and backtest but left the entry-ADMISSION ORDER exactly
as it always was: `priority_rank = (0 if is_etf else 1, idx)` — ETF group
first, then whatever order tickers happen to appear in `config.yaml`'s
`instruments:` dict. Confirmed-arbitrary, never designed, the same failure
mode that produces a rule like this in the first place: implement whatever
is easiest, not what's actually intended. This section replaces it with a
properly reasoned design, adopted live the same day it was built —
identical build-validate-deploy sequence as the portfolio-cap and
compounding work.

**Design, as agreed before building anything:**

1. **Priority is by signal confidence/strength, not position size** — two
   deliberately independent questions ("is this worth doing" vs. "can we
   fit it"). Signal strength = `momentum_pct`, the continuous trailing-12-
   month return already computed for every entry (this system is long-only
   and only ever enters when momentum > 0, so higher momentum is
   unambiguously "a stronger positive trend," with no sign-direction
   ambiguity to resolve). Nothing new to compute — `brokers/trading212.py`
   was already storing this on every `pending_orders` row
   (`_queue_pending_order(..., momentum_pct=...)`); it just wasn't being
   used to rank anything.
2. **Skip-and-continue, not resize-to-fit.** A candidate that doesn't fit
   remaining capacity is skipped — the walk continues to the NEXT candidate
   in priority order (a smaller, lower-priority one may still fit) — rather
   than either stopping entirely (the old `break` behaviour) or shrinking
   the candidate's own size to fit (a real, separate bug this task's
   investigation found and fixed — see point 4 below).
3. **`REJECTED_CAPACITY` status, never deleted** — a proposed-but-
   unadmitted candidate is fully recorded (same auditability principle as
   `SKIPPED_PRICE_MOVED`), with enough detail to reconstruct the decision
   afterward: ticker, date, is_etf, momentum, the position_gbp it would
   have taken, the would-be deployed fraction, the configured cap, and the
   account state at the moment of rejection.
4. **Capacity checks against real, live, current T212 account state at the
   moment each job runs — never a tracked/projected running total.** This
   was already how `_submit_pending_orders()` worked (one fresh
   `client.get_account_summary()` call at the top of each job, matching
   the compounding-sizing design above) and needed no new mechanism — it's
   what naturally resolves the ETF/stock cross-job timing question: the
   stock job (13:30 UTC) reads T212's actual current state, which already
   reflects whatever the ETF job (07:00 UTC) placed earlier that day.

**A real, related bug found and fixed while implementing point 2.**
`_submit_pending_orders()`'s existing `position_gbp = min(row["position_gbp"],
free_cash * 0.25)` line was itself a resize-to-fit — silently shrinking a
VaR-calibrated position down to a quarter of free cash, exactly the
anti-pattern design point 2 rejects. Removed: the full, intended
`position_gbp` is now used unconditionally; a candidate whose full size
exceeds free cash is `REJECTED_CAPACITY` (a new, second rejection reason
alongside the deployment-cap one), never silently shrunk and submitted
anyway.

**Live mechanism (`brokers/trading212.py`):**
- `_load_pending_orders()` now reads `ORDER BY momentum_pct DESC,
  decided_at ASC` (the second key a deterministic tiebreak for a genuine
  momentum tie, not a re-introduction of decide-order priority) instead of
  `ORDER BY decided_at ASC` — still called separately per asset class (two
  distinct scheduled jobs, LSE-hours vs. NYSE-hours), so ETF and stock
  candidates are never ranked against each other in one list; each job
  ranks only its own asset class's candidates. SQLite's default NULL
  ordering puts an unknown-momentum candidate last in the DESC sort,
  matching this project's existing NULLS-LAST convention elsewhere.
- `_submit_pending_orders()`'s loop no longer has a single top-of-loop cap
  check that `break`s the whole run. Each candidate's FULL position_gbp is
  computed, then checked against both `max_total_deployed` (would this
  candidate's own full size push deployed capital over the cap?) and free
  cash (does its full size exceed what's actually available?) — a miss on
  either resolves the row to `REJECTED_CAPACITY` with a detailed note and
  `continue`s to the next candidate; nothing stops the run early any more
  except a genuine order failure or exhausted free cash below the £10
  sanity floor.

**Backtest mechanism (`analysis/backtest.py:simulate_portfolio_compounding()`,
still the exact function `run_full_validation()` defaults to) — built to
mirror the live mechanism exactly, per this task's own explicit
requirement, not a parallel implementation that could drift:**
- `precompute_ticker()` now also stores a `momentum` column (the same
  `signals.compute_momentum_series()` already used internally to derive
  the boolean `signal` column) — additive, harmless for the two older
  engines that ignore it.
- **ETF/stock combined-vs-separate ranking, decided and stated
  explicitly, as asked.** Kept SEPARATE — one full ETF-candidate pass
  (ranked by that day's own momentum, descending) fully admitted before
  any stock candidate is even considered, exactly mirroring live's two
  physically separate submission jobs. This is temporal sequencing, not a
  priority judgement that ETFs matter more: live cannot combine them (an
  ETF candidate submits at 07:00 UTC regardless of what any stock
  candidate looks like at 13:30 UTC), so a backtest that combined them
  into one cross-asset-class list would systematically model something
  live can never do, breaking the "same path as live" requirement this
  task explicitly re-stated. Implemented via a nested `_admit_group()`
  helper (rank-then-skip-and-continue), called once for the day's ETF
  group then once for the stock group, sharing the same running
  `current_balance`/`current_deployed` state across both calls — so the
  stock pass sees exactly whatever capacity the ETF pass already
  consumed, same as live.
- `collect_rejections: bool = False` (new parameter, default preserves the
  original `list[dict]` return signature for every existing caller —
  `analysis/validation.py`, the compounding sweep) — when `True`, returns
  `(trades, rejections)` instead, `rejections` carrying the identical
  fields the live `REJECTED_CAPACITY` note does.
- **A real determinism bug found and fixed while building this**:
  `flat_before_today` (the day's entry-candidate pool) is a Python `set`,
  whose iteration order is not guaranteed stable. Under the OLD fixed
  `(is_etf, idx)` priority tuple this never mattered (idx is unique per
  ticker, so ties were structurally impossible) — under momentum-based
  ranking, a genuine tie (or, in a test fixture, several tickers all
  missing a momentum column) now CAN occur, and would otherwise resolve via
  that nondeterministic set order. Fixed by adding each ticker's original
  input-list `idx` as a deterministic last-resort tiebreak in
  `_admit_group()`'s sort key — reached only on a true momentum tie, never
  overriding a real priority difference.

**Verified three ways, matching this project's standing discipline:**
- **Full local suite passes (248 tests)** — new coverage: live submission
  order genuinely follows momentum descending regardless of decide order;
  skip-and-continue admits a smaller, lower-priority candidate after a
  larger, higher-priority one is rejected (both live and backtest); every
  `REJECTED_CAPACITY` record is self-consistent (recomputing
  `would_deploy_pct` from the stored `current_deployed_gbp`/
  `current_balance_gbp`/`position_gbp` matches the stored value and
  genuinely exceeds the cap); the full-position-size-never-shrunk fix,
  both the affordable case (full size used, not shrunk to `free_cash *
  0.25`) and the oversized case (`REJECTED_CAPACITY`, not silently
  resized); the ETF-then-stock live-state test (simulates the ETF job
  consuming capacity, updates the mocked account state to what T212 would
  genuinely report afterward, confirms the stock job's own fresh read sees
  that consumption — proving capacity is read live per job, not tracked
  across jobs); and the volatility-fairness comparison below.
- **A bare `run_full_validation(config)` call, run against real 2007-2026
  production data in an isolated server clone (deleted only after these
  figures were captured, production untouched), produced real, materially
  improved figures** — see the table below. Both legacy explicit overrides
  (`uncapped=True`, `portfolio_mode=True`) reproduced their own
  already-published figures exactly on the same data, confirming this
  change is correctly scoped to priority/capacity logic only.
- **The volatility-fairness question — measured directly, not assumed.**
  A synthetic 2x2 grid (low/high volatility x low/high momentum, 4 tickers
  per cell) all becoming candidates on the same day under a binding
  capacity constraint, compared against a reconstructed break-on-first-miss
  counterfactual computed from the exact same run's own data (not a second,
  potentially-drifting simulation): low-volatility (large-allocation)
  candidates were admitted at the same rate under both strategies in this
  test (1 of 8 either way — a single large position can consume half the
  entire capacity budget on its own, a structural limit no admission
  ordering can change), but high-volatility (small-allocation) candidates
  were admitted at DOUBLE the rate under skip-and-continue (6 of 8) versus
  break (3 of 8), since skip-and-continue keeps using leftover capacity
  break would have abandoned at the first miss. **Honest answer: skip-and-
  continue does not resolve the structural size disadvantage for large,
  calm names — that remains a mathematically unavoidable consequence of
  capacity constraints — but it substantially improves overall capital
  utilization and fairness for smaller trades, and never admits fewer
  large-allocation candidates than break would have (proven directly: the
  break-admitted low-vol set is always a subset of the skip-continue-
  admitted one).**

**Real production-data comparison, `start=2007-01-01, end=2026-07-30`,
same window/config every V2 figure in this file uses:**

| Metric | Old design (alphabetical priority, break-on-first-miss) | New design (momentum priority, skip-and-continue) |
|---|---|---|
| Total trades | 2,180 | 1,851 |
| TRAIN CAGR / DD / PF | 3.27% / -11.57% / 1.46 | 4.25% / -11.42% / 1.57 |
| TEST CAGR / DD / PF | 9.85% / -12.81% / 1.90 | 14.03% / -11.59% / 2.06 |
| Blocks passed / 16 | 14 (fail 0, 1) | 14 (fail 0, 1) — identical pair |
| Walk-forward | 83.4% (186/223) | 79.4% (177/223) |
| TRAIN £50,000 → | £73,961 (vs £114,033 S&amp;P) | £82,985 (vs £114,033 S&amp;P) |
| TEST £50,000 → | £92,643 (vs £114,149 S&amp;P) | **£118,380 (vs £114,149 S&amp;P)** |

**Fewer, better trades, on every risk-adjusted metric — and the TEST
window now genuinely beats buy-and-hold S&amp;P 500 in £ terms for the
first time under any capped/compounding configuration measured this
session.** TRAIN CAGR/PF both improve and drawdown is essentially
unchanged; TEST CAGR/PF both improve materially. Walk-forward success rate
is the one metric that's lower (79.4% vs 83.4%) — a genuinely mixed
signal worth stating plainly rather than only reporting the positive
numbers: fewer, more selective admissions apparently produced a small
number of additional losing 6-month windows even as the aggregate
train/test figures improved. Reservations already on record for the
underlying 5%/50% parameter choice itself (both windows still don't beat
the market outright on a fully risk-neutral view, the calmest names are
still ceiling-dominated) are unaffected by this change and still apply —
this section improves HOW candidates are chosen and fit within the
existing 5%/50% budget, it does not re-examine whether 5%/50% itself is
the right budget.

Server-side verification clone (`/root/priority-capacity-verify`) deleted
only after every figure above was captured; production DB and both
systemd services confirmed untouched throughout (same verification method
as every prior study today — `git status` clean, service
`ActiveEnterTimestamp` unchanged from before this task began). Not
committed as a config change — `trading212_invest.per_trade_pct`/
`max_total_deployed` are unchanged from "Live execution switched to the
compounding two-parameter scheme" above; this task only changed the
ADMISSION LOGIC layered on top of those two numbers.

### Market-cap data infrastructure — deployed, not wired to live trading (2026-08-01)

**`analysis/market_cap.py` (point-in-time market-cap reconstruction from
SEC EDGAR) and its `market_cap_history` table are now deployed to
production and populated with real data — this is a data-infrastructure
deploy only, and does not change what live trading does in any way.**
Shipped alongside three edge-case fixes found while building and
stress-testing this module (`analysis/signals.py`'s momentum calendar-gap
guard, `analysis/backtest.py`'s `END_OF_DATA` capacity-release fix, and
`market_cap.py`'s own `filed_date`-not-`period_end_date` look-ahead-bias
fix — commit `eaeb90e`, deployed 2026-08-01 12:24 UTC, no blackout window
active at deploy time, both services restarted together).

**Confirmed zero live consumers before deploying, not assumed.**
`grep -rn "market_cap\|MarketCapHistory"` across the whole repository
returns exactly five hits: the module itself, its own test file, the
`MarketCapHistory` model definition in `data/database.py`, this
documentation, and `tests/conftest.py`'s standard DB-fixture patching.
Nothing in `brokers/`, `data/scheduler.py`, `main.py`,
`analysis/decision.py`, `analysis/backtest.py`'s simulation engines,
`analysis/validation.py`, `analysis/confluence.py`, or `analysis/regime.py`
references either the module or the table. **No live order-placement
decision, no backtest/validation figure reported anywhere else in this
file, and no UI page reads from this data — it exists, and nothing
consumes it yet.** Whether/how to wire a market-cap-ranked universe into
anything live is a separate, not-yet-made decision; nothing in this
deploy should be read as having made it.

**Production data, backfilled the same day, verified against real
production price/filing data (not the disposable research clone this
mirrors):** the same 503-candidate pool construction rule already
documented under "V2 stock universe broadening" (`is_included(ticker) :=
window overlaps [2007-01-01, 2026-06-30] AND active in the source's
2026-06-30 final row`) reproduced **503 candidates** from a fresh fetch of
the same `fja05680/sp500` CSV. `backfill_shares_outstanding()` against all
503 resolved **459** (**25,573** total quarterly shares-outstanding rows,
`period_end_date` spanning 2007-12-31 → 2026-07-30) and left **44**
genuinely unresolvable under every tag in `SHARES_TAGS` — `ABNB`, `ABT`,
`AMT`, `BG`, `CB`, `COF`, `CTAS`, `CTSH`, `DASH`, `EL`, `ERIE`, `FDXF`,
`FTV`, `GLW`, `GRMN`, `HBAN`, `HCA`, `HONA`, `HRL`, `HUM`, `INCY`, `IQV`,
`KO`, `LEN`, `LII`, `MAS`, `META`, `MKC`, `PFG`, `RCL`, `RL`, `SLB`,
`SPGI`, `STZ`, `SWKS`, `TKO`, `TTD`, `UHS`, `VLTO`, `VRSK`, `WELL`, `WFC`,
`XOM`, `XYZ` — an exact match, count-for-count, against the figures
already recorded under "V2 stock universe broadening" and "V2 moderate
stock universe expansion" above, and including every large, well-known
name (`META`, `XOM`, `KO`, `WFC`, `SPGI`, `HCA`, `HUM`, `CTAS`) previously
cited as confirmed non-resolvers. Spot-checked directly against production
data before trusting it: `NVDA`'s stored shares-outstanding jumps
623,000,000 → 2,500,000,000 exactly at its real July 2021 4:1 split
(`period_end_date` 2021-05-21 → 2021-08-13, `filed_date` 2021-05-26 →
2021-08-20) — the identical 4.01× figure already verified in the research
clone, now reproduced against genuinely live production data.

**Deploy discipline, same standard as every production change today.**
Full local test suite (334, including two unrelated, deliberately
left-uncommitted `tests/test_execution.py` additions from earlier work)
passed before push; the pre-push hook re-ran it server-side-bound and
passed again before the push was allowed through. Deploy landed inside no
blackout window (`06:45–07:15`, `13:15–14:00`, `21:00–22:00` UTC weekdays,
`09:45–10:15` UTC Sundays — checked directly against `deploy.sh`'s actual
blackout logic, not assumed from this file's own known-stale schedule
table; the deploy also landed on a Saturday, when no scheduled job other
than the 60-second dispatcher fires at all). Production DB backed up
(`database/investment_research_pre_marketcap_backfill_20260801_122518.db`)
before the backfill wrote a single row. Both `investment-research` and
`investment-research-scheduler` confirmed restarted together
(`ActiveEnterTimestamp` identical on both, 2026-08-01 12:24:54 UTC) — the
new table was created automatically by `init_db()`'s
`Base.metadata.create_all()` on that restart, no manual migration needed
since this is a wholly new table, not an `ALTER TABLE` on an existing one.

### `price_data` purge removed — permanent storage is now the design (2026-08-01)

`data/database.py:_purge_unlisted_tickers()` — a startup hygiene function
dating from the V1 era, deleting any `price_data` row for a ticker not in
`config.yaml`'s watchlist/`instruments:` or `analysis.universe.ALL_TICKERS`
— has been **removed entirely**, not disabled. It ran on every service
startup and existed for a real reason (its own docstring: "keeps the DB in
sync when assets are removed from the watchlist without manual SQL"), but
that reason stopped fitting this project's actual direction the moment
market-cap research started needing broad, non-watchlist price history to
persist. The market-cap infrastructure deploy immediately above this
section hit this directly: a fresh backfill of the 503-candidate pool's
price history would have worked right up until the next service restart,
at which point every non-watchlist row would have been silently deleted
again — the same fate every future research task would keep re-triggering.

**Decision: `price_data` becomes permanent, traceable storage for
everything ever fetched, matching the pattern already established
elsewhere today** — `GOLD` kept as a permanently-zero-eligible candidate
rather than deleted from `STOCK_MEMBERSHIP`, `SKIPPED_PRICE_MOVED` and
`REJECTED_CAPACITY` recording a decision rather than discarding it,
the membership-window fix keeping a still-active ticker's window open
rather than closing it prematurely. None of those precedents destroy
data to keep the system "clean" — they flag, record, or simply leave
data alone. Deleting a ticker's price history the moment it falls out of
the watchlist was the odd one out.

**Step 1 — verified before removing, not after.** Checked directly
whether anything else in the codebase treats `price_data`'s distinct-ticker
set as an implicit proxy for "the live-relevant universe" without explicit
filtering — the exact failure shape `get_max_holding_days()` had before
its own same-day fix (see the parallel session's commit). Full repo sweep,
every hit reviewed individually:
- `analysis/backtest.py`/`analysis/validation.py` (the highest-stakes
  candidates, since this determines a live-reported backtest's candidate
  universe) — confirmed clean: `run_full_validation()`'s `candidate_tickers`
  is sourced explicitly from `universe.ALL_TICKERS`, never from a
  `price_data` query. `analysis/backtest.py` has zero direct references to
  `price_data` at all.
- `main.py`'s two broad, unfiltered `price_data` queries (`_price_coverage()`'s
  coverage grid, the Charts landing page's 1-day-change lookup) both build
  a dict keyed by ticker and then iterate the *explicit* watchlist
  (`get_all_watchlist_entries()`), only ever using the broad query result
  as a lookup — a ticker present in `price_data` but absent from the
  watchlist is simply never visited. Confirmed safe by tracing the actual
  iteration, not assumed from the query shape alone.
- `data/ingestion.py:get_last_update_timestamp()` (an unfiltered
  `MAX(price_data.date)`) has zero callers anywhere in the codebase —
  dead code, confirmed via grep, no fix needed.
- `brokers/trading212.py`'s three `price_data` reads (FX rate lookup,
  instrument price fallback) are all hardcoded to specific tickers or
  parameterized by a single passed-in ticker — never a broad scan.
- `analysis/market_cap.py`'s reads are all per-ticker by design (this
  module exists specifically to serve a broad candidate pool one ticker
  at a time) — correctly scoped, no change needed.
- `analysis/correlations.py` no longer exists (removed in the V1 removal),
  eliminating what would otherwise have been the highest-risk legacy call
  site.

**No hidden dependency found.** Nothing needed fixing before or alongside
the removal.

**Step 2 — removed.** `_purge_unlisted_tickers()` and its call site inside
`init_db()` are both deleted outright (not commented out, not
feature-flagged) — matching this project's standing practice for
confirmed-rejected mechanisms (the GARCH stop loss and HMM regime gate
removals follow the identical pattern). `_apply_data_fixes()`'s own
unconditional `DELETE FROM price_data WHERE close IS NULL OR close <= 0`
is untouched — that guard is a data-*quality* fix (incomplete intraday
bars), unrelated to watchlist membership, and still runs on every startup
exactly as before.

**Step 3 — practical outcome.** Price data fetched for any research
purpose — the market-cap candidate pool, a future universe-broadening
study, anything else — now persists in production's `price_data` table
indefinitely, surviving every future restart and deploy. A research task
that needs the same broad price coverage twice no longer needs to
re-fetch it, the same efficiency this file's market-cap-infrastructure
section above already established for `market_cap_history` specifically
(which was never touched by the purge in the first place) — now the
identical guarantee extends to price history too, and a dedicated
purge-exempt table (considered and rejected as an alternative — see the
interrupted design discussion this decision superseded) was never needed.

Covered by `tests/test_ingestion.py:test_init_db_does_not_delete_non_watchlist_ticker_price_data`
— a ticker with no `config.yaml`/`ALL_TICKERS` presence at all (exactly
what the old purge would have deleted) is confirmed to survive a real
`init_db()` call unchanged. Full suite (381 tests, +1 from this change)
passes.

### Live/backtest deployment-cap parity (2026-08-01) — a fourth instance of the top-of-file invariant, found the same day

**Confirmed: `simulate_portfolio_compounding()`'s deployment-cap tracking
was cost-basis; live trading's was genuine mark-to-market — a real
divergence, not a documentation slip.** Live (`brokers/trading212.py:
_submit_pending_orders()`) reads `invested = summary["investments"][
"currentValue"]` straight from T212's account summary — the broker's own
real, current valuation of every open position, reflecting whatever
unrealized gain or loss it currently carries. The backtest engine's
`current_deployed` was the running sum of each open position's *original*
`position_gbp` at entry, decremented by that same original figure on
exit (`current_deployed -= ot["position_gbp"]`) — never once adjusted for
how far the position's price had actually moved while it stayed open. A
position that had genuinely doubled in value still only ever "counted"
at its entry cost for capacity purposes; a position that had halved
still counted at full original cost too. `current_balance` (what
`per_trade_pct` sizing is measured against) had the identical gap — it
only ever moved on a *realised* exit, never reflecting a still-open
position's unrealized P&L, unlike T212's real `totalValue`.

**Why the existing parity suite didn't catch this — checked directly,
not assumed.** `tests/test_live_backtest_parity.py`'s entire test matrix
covers exactly two functions: `evaluate_exit_reason()` and
`passes_entry_gate()` in `analysis/decision.py` (4 entry scenarios, 5 exit
scenarios). Deployment-cap/capacity logic was never in its scope at
all — not a gap in coverage of something it was trying to test, but a
decision that was never on its list to test in the first place. Separately,
the "Live/backtest decision-logic parity" audit at the top of this file
*did* review the deployment-cap duplication specifically the same day and
explicitly decided to leave it as two call sites (see the corrected
bullet above) — but justified that decision by confirming the two sides
used the same comparison operator and threshold, never checking that
both sides' underlying `invested`/`deployed` numerator meant the same
thing. Structural similarity was verified; semantic equivalence wasn't.

**The fix — `analysis/backtest.py:simulate_portfolio_compounding_mtm()`,
a new, separately-callable engine kept alongside the original, not a
silent replacement.** Every result validated against the original
cost-basis `simulate_portfolio_compounding()` before this fix stays
exactly reproducible — that engine is completely unchanged, matching the
established "frozen engine, new one added alongside it" pattern
`simulate_portfolio()`/`run_candidate()` already set. The new engine
tracks cash and mark-to-market position value SEPARATELY, mirroring
T212's own `cash.availableToTrade` + `investments.currentValue` =
`totalValue` split: `cash` moves only on realised entry/exit transactions
(decreases by `position_gbp` on entry, increases by `position_gbp +
pnl_gbp` on exit); every open position's current value is recomputed on
demand as `position_gbp * (last_known_price / entry_price)`;
`current_balance` (`cash` + sum of all open positions' current value) and
`current_deployed` (that same sum alone) are both recomputed fresh once
per day, after that day's exits and before that day's entries — stable
across a single day's ETF-then-stock admission passes for the same
reason the cost-basis engine's own `current_balance` already was (moving
cash into an equally-valued new position doesn't change total account
value at the instant of entry). Wired into
`analysis.validation.run_full_validation(..., mark_to_market=True)` —
`False` by default, so nothing changes unless a caller opts in, and the
returned dict's `deployment_basis` field (`"cost_basis"` /
`"mark_to_market"`) makes which accounting basis produced any given
result explicit rather than inferred from `engine_used` alone.

**Verified to actually diverge in the correct direction, both ways, not
just built and trusted.** Two unit tests construct a still-*open*
position (never exited, so any capacity difference can only come from how
its unrealized value is or isn't counted) and check the admission
decision on a candidate's very first eligible day — deliberately not
"ever admitted across the whole run," since a run's own final-day
`END_OF_DATA` force-close realises P&L before that same day's entries are
considered, which would converge both engines and mask the exact thing
being tested. With a position that had doubled in value while still
open, the cost-basis engine admits a waiting candidate that the
mark-to-market engine correctly rejects (the doubled position now counts
for more capacity); with a position that had roughly halved, the reverse
holds (the mark-to-market engine correctly admits a candidate the
cost-basis engine still rejects). A third test confirms a still-open
position's unrealized loss never touches `cash` or produces a trade
record — unrealized P&L is not realised P&L.

Covered by 4 new tests in `tests/test_portfolio_backtest.py`
(appreciation reduces capacity, depreciation frees it, unrealized P&L
never leaks into cash or a trade record, `collect_rejections` return-shape
parity) and 3 new tests in `tests/test_validation.py` (the flag correctly
dispatches to the mtm engine, the default stays on the unchanged
cost-basis engine, and the flag correctly has no effect under `uncapped`/
`portfolio_mode`, which have no deployment-cap concept for it to apply
to). Full suite: 388 passing.

**Re-verification of today's key findings under genuine mark-to-market
tracking** — Part 9's capped-vs-priority-engine reversal, Part 11's
ETF-concentration finding (static-50's -87.49% drawdown), and the
curated-40-vs-rolling-40 result — is a separate, materially larger
compute task (three full historical re-runs) tracked and reported
independently; see the follow-up entry immediately below once it lands.

### Mark-to-market unbounded compounding defect (2026-08-01)

The first attempt to run the three re-verifications above surfaced a
genuine, severe defect in `simulate_portfolio_compounding_mtm()` before
any of the three findings could actually be re-checked — production-scale
runs produced `position_gbp` values of 10^60-10^76, every historical
validation block failing, CAGR in physically impossible ranges. Not a
reporting artifact — confirmed directly from real trade rows.

**Two independent, compounding causes, both found and fixed the same
day, not one:**

1. **Unrealized appreciation feeding forward into new-entry sizing.**
   `current_balance` (what `per_trade_pct` sizing is measured against)
   includes every open position's current mark-to-market value —
   deliberately, since that's what genuinely matches T212's real
   `totalValue`. But a single position that appreciates enormously while
   never triggering an exit condition inflates `current_balance`
   indefinitely, which inflates the sizing ceiling for every OTHER,
   unrelated new entry, which (if those also stay open and appreciate)
   inflates `current_balance` further still — an exponential feedback
   loop with no natural limit across a long enough backtest. Confirmed
   directly: a synthetic two-ticker scenario (one massively-appreciating,
   never-exiting position; a second, flat-priced ticker cycling
   entries/exits every 30 days, sized fresh each time) reproduced the
   mechanism cleanly — the second ticker's position size grew from £250
   to over £20,000 purely from the first ticker's unrelated, unrealized
   paper gains, with the second ticker's own price never moving at all.
2. **A separate, real bug in the same engine's cash bookkeeping.** `cash`
   was credited on every exit (`cash += position_gbp + pnl_gbp`) but
   never DEBITED on entry — found while implementing the fix for (1), not
   anticipated in advance. This meant every open position's cost basis
   was effectively double-counted in `current_balance = cash + mtm_value`
   — once as "still sitting in cash" (never actually spent) and again as
   `mtm_value` — compounding `current_balance` upward on every single
   entry/exit cycle regardless of whether the position was ever
   profitable or ever appreciated while open. This alone was confirmed to
   be a major contributor to the magnitude of the runaway, independent of
   cause (1).

**Is live trading exposed to the same defect? Checked directly, not
assumed — the answer is nuanced, not a clean yes/no.** Live sizing
(`brokers/trading212.py`) already reads T212's real `totalValue`
(mark-to-market, same as the backtest engine) for `dynamic_max_position_
gbp`, so cause (1)'s mechanism is structurally present in live too — the
only reason it's never been observed live is that the system has been
running for days, not years, and hasn't yet held a position through a
large enough unrealized run-up to expose it. **However, live already had
a real, independent, pre-existing defence the backtest engine was simply
missing**: `position_gbp = min(position_gbp, free_cash * 0.25)` — a
second clamp against `cash.availableToTrade`, T212's real, currently-
uninvested cash, which is NEVER inflated by unrealized gains on other
positions (cash only ever moves on a genuine realised transaction).
Confirmed directly by reading the code, not inferred: this meant live's
real-world exposure was already narrower than the backtest's clean
numerical runaway suggested — an oversized *sizing calculation* could
still occur (feeding off a corrupted `total_val`), but the *order actually
placed* was already bounded to at most 25% of real, currently-available
cash, not an unbounded figure. Not zero risk, but a materially different
(and better) failure mode than "could place an order for a nonsensical
amount" — and cause (2), the cash-double-counting bug, was specific to
the new backtest engine's own bookkeeping and never existed in live at
all, since live reads T212's real cash figure directly rather than
tracking it locally.

**The fix — one shared implementation, not a backtest-side patch,**
matching the exact pattern this project has now applied five times in
one day (see `analysis/decision.py`'s own docstring for the first four:
exit priority, entry gate, `is_etf` dispatch, deployment-cap tracking).
Two new functions in `analysis/decision.py`:

- `resolve_dynamic_max_position_gbp(account_value_gbp, per_trade_pct,
  absolute_max_position_gbp)` — `min(account_value_gbp * per_trade_pct,
  absolute_max_position_gbp)`. `absolute_max_position_gbp` is a new
  config key (`trading212_invest.absolute_max_position_gbp`, default
  £1,000,000) — deliberately **not** a reintroduction of the retired
  `max_position_gbp` (rejected 2026-08-01 as a PRIMARY sizing driver
  because a flat ceiling saturates VaR's own volatility differentiation
  for low-volatility names — see "Compounding portfolio backtest" above).
  This is a rarely-binding SAFETY backstop: `per_trade_pct` still governs
  sizing in every ordinary case; the ceiling only intervenes once that
  figure becomes absurd. Set high enough (20x+ a realistic account size)
  to be inert for any plausible real compounding trajectory while still
  decisively breaking the exponential shape of the defect.
- `resolve_cash_constrained_position_gbp(position_gbp, available_cash_gbp,
  cash_fraction=0.25)` — the same `min(position_gbp, cash * 0.25)` clamp
  live already had, now a shared function rather than an ad-hoc inline
  `min()`, and newly ported into `simulate_portfolio_compounding_mtm()`
  (which tracks its own `cash` variable, distinct from `current_balance`,
  specifically to support this).

Both functions are called identically by live order placement
(`brokers/trading212.py:maybe_place_orders()`) and
`simulate_portfolio_compounding_mtm()`. The original, frozen cost-basis
`simulate_portfolio_compounding()` also gets the
`resolve_dynamic_max_position_gbp()` ceiling (defense-in-depth — that
engine's `current_balance` only ever grows from realised profit, so it
was never vulnerable to cause (1)'s specific mechanism, but the shared
ceiling costs nothing and closes the theoretical gap) — it does NOT get
the cash-clamp fix, since it has no independently-tracked cash concept to
begin with and is deliberately left structurally unchanged, preserving
its own reproducibility guarantee for every already-published cost-basis
figure in this file.

**Verified the fix actually works, not just assumed.** A calibrated
synthetic scenario (one massively-appreciating never-exiting position,
one repeatedly-cycling second position) that previously grew the second
position's size from £250 toward tens of thousands now stays bounded to
low thousands, consistent with the cash clamp binding correctly. Direct,
deterministic unit tests on the two new functions themselves (`tests/
test_decision.py`) confirm the ceiling caps an absurdly inflated account
value at exactly the configured limit, with no off-by-one at the exact
boundary, and that the cash clamp is structurally immune to inflated
account-value figures by construction (it never reads `account_value_gbp`
at all). A genuinely multi-year (~10 simulated years), many-compounding-
entry scenario test (`tests/test_portfolio_backtest.py:
test_mtm_many_year_compounding_never_produces_unbounded_position_sizes`)
guards against a regression at meaningful scale — though, checked
directly and stated honestly: a 2-ticker synthetic scenario cannot
reproduce the FULL magnitude of the original production-scale defect
(which involved dozens of simultaneously-appreciating tickers across
thousands of trades over 19 years) within a fast unit test's runtime, so
this test is a plausibility/bounded-growth guard, not a literal
reproduction of the original 10^60-scale failure — production-scale
re-verification (the task this fix exists to unblock) is the real,
decisive confirmation, reported in the follow-up entry immediately below.

Full suite: 400 passing (7 new: 6 direct `resolve_dynamic_max_position_
gbp()`/`resolve_cash_constrained_position_gbp()` unit tests, 1 multi-year
scale scenario).

### Structural enforcement for live/backtest parity (2026-08-01)

Direct follow-up to the section immediately above — a targeted parity
test suite, however well-built, can only catch divergence in a decision
someone thought to test in advance, and the deployment-cap gap wasn't on
anyone's list until it was found by accident. Three layers, deliberately
of decreasing strength, added the same day:

1. **Mechanical scan for decision-shaped logic outside
   `analysis/decision.py`** — a repo-level check over `brokers/
   trading212.py` and `analysis/backtest.py`.
2. **A comprehensive, end-to-end parity test** — a full historical
   simulation through live's real decision code (dry-run, no orders
   placed) and the backtest engine over identical data, asserting the
   entire resulting trade sequence matches, not just a handful of
   hand-picked scenarios.
3. **A standing CLAUDE.md checklist** for any future change touching
   either file.

**Layer 1 — `tests/test_decision_logic_placement.py`, a mechanical AST
scan for decision-shaped logic outside `analysis/decision.py`.** Parses
`brokers/trading212.py` and `analysis/backtest.py`, finds every raw
Python comparison (`>`, `<`, `>=`, `<=`, `==`, `!=`) whose operand is a
genuine variable reference (not a coincidental string-literal mention)
matching a curated list of decision-signal keywords
(`deployed_pct`/`max_total_deployed`/`current_deployed`, `sig_on`,
`is_member`, `entry_blocked`, `exit_reason`, `holding_days`/`max_hold`,
`stop_price`, `crisis_now`), and fails if that comparison isn't in an
explicit, CLAUDE.md-documented whitelist. A comparison that's only
*reading* an already-decided outcome against a literal (`exit_reason ==
"STOP_LOSS"`, `exit_reason is None`) is excluded — that's consuming a
decision made elsewhere, not deriving a new one; a comparison against a
*number* (a threshold, a percentage, a day count) is never excluded,
since that's the actually dangerous shape — two places independently
picking a different operator or threshold for the same quantity, exactly
what the deployment-cap bug was.

**What it caught immediately, on its own first real run — not a
hypothetical, a genuine bug in the same day's own new code.** The brand
new `simulate_portfolio_compounding_mtm()` (built earlier the same task)
had already reproduced `would_deploy_pct = (current_deployed +
position_gbp) / current_balance` verbatim from the original cost-basis
engine — the scanner flagged both occurrences immediately. Fixed by
extracting `analysis/backtest.py:_would_deploy_pct()`, a small shared
helper both engines now call, rather than whitelisting the duplication —
the semantically risky part (what "deployed" means) is now singly
defined; only the trivial `> max_deployed_pct` branch (which differs
structurally per engine — different rejection-record shape, different
capacity bookkeeping) remains duplicated, and *that* remaining piece is
whitelisted with the reason recorded inline in the test file. Live's own
`deployed_after_pct > max_deployed_pct` in `_submit_pending_orders()`
stays a separate, deliberately whitelisted entry too — it reads a live
HTTP-sourced value and genuinely can't share one function with a
backtest-simulated equivalent.

**What Layer 1 does and doesn't catch, stated plainly (not overclaimed
— see the module's own docstring for the same text):** catches a raw
comparison using one of the listed keywords, living outside the
whitelist. Does NOT catch decision logic expressed any other way — a
lookup table, a dict-based dispatch, a call into a third function outside
`analysis/decision.py` that itself contains an unshared comparison, or a
comparison whose variable names don't happen to match the keyword list.
It is a targeted, high-precision scan for the one specific anti-pattern
this project has now been burned by (see `analysis/decision.py`'s own
docstring for the earlier three instances found the same day), not a
general-purpose decision-logic detector. Verified to actually fail on a
deliberately injected synthetic violation (`test_scanner_actually_fails_
on_a_deliberately_injected_violation`), and to correctly leave an
unrelated, keyword-free comparison alone.

**Layer 2 — `tests/test_comprehensive_replay_parity.py`, a genuinely
continuous multi-day, multi-ticker replay.** Unlike every other parity
test in this suite (which drives real live/backtest code against
hand-picked isolated scenarios — one decision, one day), this file plays
the role of T212's own API for real: a small in-process harness tracks
cash and open positions and returns a genuinely mark-to-market
`get_account_summary()`/`get_positions()` response computed from that
state — exactly what T212 itself would return — while live's actual
`maybe_place_orders()` / `submit_pending_etf_orders()` /
`submit_pending_stock_orders()` / `maybe_exit_positions()` run for real,
once per simulated trading day, across ~320 days and two tickers (an
ETF and a stock, deliberately competing for the same shared deployment
capacity) genuinely built to exercise the exact multi-day, capacity-
sharing behaviour a single-scenario test structurally cannot. Compared
against `simulate_portfolio_compounding_mtm()` run once over the
identical price history — the two must agree on the exact (ticker,
direction) → count of every BUY/SELL, not just "did entry ever happen."

**Two real, structural findings surfaced during this test's own
construction — reported here rather than quietly engineered around,
since surfacing exactly this kind of thing is the whole reason Layer 2
exists:**

1. **A blind spot in the comparison itself.** An early version compared
   plain *sets* of (ticker, direction) pairs — which silently treats
   "sold once" and "sold five times" as identical. A sustained-decline
   test scenario produced 5 separate `STOP_LOSS`-then-immediate-re-entry
   cycles in the backtest engine but only 1 on the live replay side, and
   the set-based comparison didn't notice. Fixed by comparing `Counter`s
   (exact occurrence counts), not sets — the test file's own docstring
   records this as the reason, not a hypothetical.
2. **A genuine, previously-undocumented live/backtest divergence around
   post-exit re-entry timing.** `brokers/trading212.py:
   _load_open_order_tickers(days=30)` blocks re-entry into any ticker
   with a `PLACED`/`FILLED` order in the last 30 real days — a real,
   intentional dedup guard in live trading. `simulate_portfolio_
   compounding_mtm()` (and every other backtest engine) has no equivalent
   concept at all: the moment a position closes and `open_trade` is
   `None` again, it's immediately eligible to re-enter the very next
   day if the signal still qualifies. This means: after a stop-loss on a
   ticker whose momentum hasn't yet turned negative, **live will not
   re-attempt that ticker for 30 days; every backtest figure in this file
   would have let it re-enter immediately.** This is a real, structural
   gap between live's actual behaviour and every backtest number
   reported to date — not fixed here (a design decision about the
   correct re-entry cooldown, if any, belongs in a follow-up task, not
   folded into building the detection mechanism itself), but explicitly
   flagged rather than engineered around: the test scenario was
   deliberately calibrated (a single severe price shock that flips
   momentum negative in the same move that triggers the stop, see
   `_rising_then_crashing_frame()`'s own docstring) to isolate capacity/
   sizing/timing parity from this separate question, not to hide that the
   divergence exists.

**Verified to actually fail, the same standard as everything else in
this file** — `test_replay_actually_fails_on_a_deliberately_injected_
live_bug` patches `brokers.trading212.evaluate_exit_reason` to always
return `None` (silently disabling every live exit) and confirms the
replay correctly diverges (STKA's live SELL count drops to zero while
backtest's doesn't), proving the mechanism catches a real behavioural
break without being told in advance which decision was broken —
unlike every other parity test in this suite, which has to be constructed
around a specific, anticipated scenario.

**What Layer 2 does and doesn't guarantee.** It replays a genuinely
continuous multi-day, multi-ticker, capacity-sharing scenario — not "full
19-year production history" (which would make the fast unit suite
impractically slow to run on every commit); the mechanism, once proven
correct on a meaningful multi-day walk, generalises, but a literal full-
history replay as a periodic, separate (non-unit-suite) job would be a
reasonable future addition, not built here. It exercises the ETF-then-
stock same-day ordering and shared deployment-cap admission exactly as
production does, but does not model every corner of live's real
async order-fill polling (a same-day instant-fill simplification is used
instead of a second, separately-mocked polling call — see the harness's
own comments for why this is a reasonable simplification, not a gap that
weakens what the test actually checks).

**Layer 3 — a standing checklist, the weakest of the three, added here
directly rather than as a separate file:**

> **Any future change to `brokers/trading212.py` or
> `analysis/backtest.py` must state explicitly, in its own commit
> message or PR description, whether it introduces or modifies a
> decision** — anything determining entry eligibility, exit triggers,
> priority ordering, or capacity/deployment gating (as opposed to
> execution mechanics: retry/back-off, DB bookkeeping, HTTP error
> handling). If it does, confirm the change goes through
> `analysis/decision.py` (or an equally shared module), not an inline
> comparison in either file — and that Layers 1-2 above still pass
> unmodified, or that any scenario needing to change is itself a signal
> the change altered semantics, not just an implementation detail. This
> is a backstop for a human or an agent moving fast, not a substitute
> for Layers 1-2 — it only works if whoever's making the change actually
> pauses to ask the question.

**Honest statement of what all three layers do and don't guarantee,
together — no overclaiming.** They do not make live/backtest divergence
*impossible*. A genuinely new kind of decision, expressed in a way Layer
1's keyword list doesn't recognise, living in a function Layer 2's
scenario doesn't happen to exercise, would still slip through undetected
until something else surfaces it — exactly the same way the deployment-
cap gap itself was found by accident, not by any prior mechanism. What
these three layers change is the *shape* of what future divergence needs
to look like to go unnoticed: Layer 1 needs a genuinely novel decision
pattern outside its keyword vocabulary; Layer 2 needs a scenario outside
what its multi-day, multi-ticker walk exercises; Layer 3 needs a change
author to skip a two-line checklist. Each is individually incomplete by
design and by honest admission — together they are a materially higher
bar than "we wrote one targeted test for the specific thing we already
found," which is what existed before this task and precisely what proved
insufficient. Execution-mechanics differences (live's real retry/back-off
HTTP logic, its DB bookkeeping) remain legitimately, permanently
different between the two sides and are correctly outside what any of
these three layers try to unify — see `analysis/decision.py`'s own
docstring for that boundary, unchanged by this work.

Full suite: 393 passing (5 new since the deployment-cap fix above: 3 in
`test_decision_logic_placement.py`, 2 in
`test_comprehensive_replay_parity.py`).

### Live and backtest consolidation audit (2026-08-01)

Direct follow-up, framed explicitly as the primary goal rather than one
item among several: today found **five** separate instances of live and
backtest diverging on decisions meant to be identical (exit priority,
entry gate, `is_etf` dispatch, deployment-cap tracking, mark-to-market
sizing) — not five unrelated bugs, one architectural problem showing up
five times, because live and backtest have never actually been one
system with two front doors. This section tracks the audit whose goal is
ending that: consolidating entry, exit, sizing, capacity, and priority
ranking into a single decision engine, with live and backtest reduced to
thin adapters differing only in where they get data from and what they
do with the result.

**First finding, Part A (manual mapping, not caught by Layer 1's own
mechanical scanner) — a genuinely missing check, not a duplicated one.**
Layer 1 scans for decision-shaped logic living *outside*
`analysis/decision.py` — it has no way to flag a decision that's
*missing* from one side entirely, since there's nothing duplicated to
detect. Manual line-by-line comparison of `maybe_place_orders()` against
`simulate_portfolio_compounding_mtm()` found exactly this shape: live has
always had `if position_gbp < 10: skip` immediately after its
cash-availability clamp — a backstop against a degenerate, near-worthless
order surviving every other sizing step. When
`resolve_cash_constrained_position_gbp()` was ported into the
mark-to-market backtest engine as part of the same day's earlier fix
(see "Mark-to-market unbounded compounding defect" above), this
corresponding floor was not — meaning a thin-cash scenario could have
silently opened a degenerate position in the backtest that live would
have correctly skipped. Fixed the same way as every other consolidation
today: `analysis/decision.py:is_position_size_viable(position_gbp,
min_viable_order_gbp=10.0)`, called identically by both
`brokers/trading212.py` and `simulate_portfolio_compounding_mtm()` (the
original cost-basis engine doesn't need it — its VaR floor,
`min_position_gbp`, is never followed by anything that could shrink a
position further, since it has no cash clamp). Verified with a
deterministic test that forces the cash clamp to return a sub-floor value
and confirms the candidate is never admitted (chasing an emergent
scenario naturally producing a sub-£10 position via starting-capital/
per_trade_pct tuning proved unreliable — the direct, mocked approach is
the same lesson already learned earlier the same day building
`test_comprehensive_replay_parity.py`'s injected-bug proof).

**This finding is itself evidence for the audit's own thesis**: Layer 1
and Layer 2, built the same day specifically to catch this class of
divergence, together caught a *duplicated* decision (the earlier
`_would_deploy_pct` computation) but neither is shaped to catch a
*missing* one — this gap was found only by deliberately re-reading both
functions side by side, the exact "someone happening to check that
specific piece closely enough" failure mode the whole audit exists to
stop relying on. Whether this points to a genuine Layer 1 extension (a
checklist of decisions known to exist on one side, verified present on
the other) is an open question for this audit's own conclusion, not
resolved here.

Full suite: 402 passing (2 new) at this checkpoint.

**Part A/B completion — the rest of the decision inventory, mapped and
verified.** Every remaining entry/exit/sizing/ceiling/clamp decision in
`brokers/trading212.py` and every one of `analysis/backtest.py`'s four
engines (`simulate_trades`, `simulate_portfolio`,
`simulate_portfolio_compounding`, `simulate_portfolio_compounding_mtm`)
was checked against `analysis/decision.py`'s six functions
(`evaluate_exit_reason`, `passes_entry_gate`, `is_asset_class_enabled`,
`resolve_dynamic_max_position_gbp`, `resolve_cash_constrained_position_gbp`,
`is_position_size_viable`) — every live decision now routes through the
shared module; every backtest engine either does too, or has a specific,
individually-reviewed reason it can't (documented inline in Layer 1's
whitelist, see below).

**A second real, live divergence found — not caught by the Part A finding
above, only by re-checking every call site of the fix that finding
produced.** `_submit_pending_orders()` (the live submission phase) had its
own hardcoded `if position_gbp < 10:` — the literal duplicate of the exact
check `is_position_size_viable()` was built to unify, sitting one call
site away from where the fix had already been applied
(`maybe_place_orders()`'s decide phase). Not yet diverged in value, but
exactly the "two hand-written copies merely supposed to agree" shape this
whole audit exists to close — a future change to the £10 floor would have
silently not applied at submission time. **Fixed**: now calls
`is_position_size_viable(position_gbp)` directly, matching the decide
phase exactly.

**Layer 1's scanner extended, and immediately proved itself on real code.**
`DECISION_SIGNAL_KEYWORDS` (`tests/test_decision_logic_placement.py`)
gained `"position_gbp"` — absent from the original vocabulary, which is
why the divergence above wasn't caught mechanically. Running the extended
scanner immediately surfaced a second hit, `_submit_pending_orders()`'s
`position_gbp > free_cash` — investigated and confirmed as a genuine,
reviewable exception (see the new `_WHITELISTED_SOURCE_SNIPPETS` entry):
a live-only, submission-time affordability re-check against free cash that
may have been consumed by other orders between decide-time and this asset
class's own submission window — a real-time state the backtest has no
equivalent for, structurally different from
`resolve_cash_constrained_position_gbp()`'s decide-time 25%-of-cash sizing
clamp (which both sides genuinely do share). One extension, one real bug
caught, one legitimate exception documented — confirming the "extend
Layer 1 when a new decision class is found missing, not just when one is
found duplicated" approach the earlier finding's own open question raised.

**Part B re-confirmed**: Layer 2 (`tests/test_comprehensive_replay_parity.py`)
re-run after both fixes above — both tests, including the
deliberately-injected-bug proof, still pass. No new replay scenario was
needed — the existing multi-ticker walk already exercises the sizing/
capacity path where both changes sit.

**Parts C–H — one finding per issue, evidence-based:**

- **C (test quality)**: `analysis/volatility.py:_var_position_size_core()`
  — the one function every sizing decision on both live and backtest
  ultimately funnels through — has **zero direct unit tests** anywhere
  (confirmed via `grep` across `tests/*.py`: no matches outside its own
  definition; only exercised indirectly through integration tests). This
  is the most foundational test-coverage gap found in the audit and is
  the likely reason the Part E clamp-ordering finding below went
  uncaught. Checked specifically for siblings of the membership-window
  bug (a test asserting behaviour that contradicts documented intent) and
  the 44-companies bug (a test mocking a real dependency wholesale in a
  way that never exercises real behaviour) — none found; the closest
  candidate, `test_full_position_size_never_shrunk_to_fit_free_cash`, was
  checked directly and confirmed to assert on real call arguments, not a
  coincidental pass. **Flagged, not fixed** — adding proper coverage to
  shared sizing math this late in a long day's changes carries real risk
  without dedicated review. **RESOLVED the same day — see "All three
  flagged-not-fixed items resolved" below.**
- **D (documentation accuracy)**: CLAUDE.md's own "Broker integration"
  summary called `get_instruments()`'s cache "24-hour" — stale; the
  actual code (`_INSTRUMENTS_TTL`) and this file's own later "Quantity
  sizing note" section both correctly say 1 hour (reduced once this cache
  became the source of live sizing prices, not just static metadata). The
  summary sentence was never updated when that fix shipped. **Fixed**
  (see the corrected sentence a few paragraphs above this section).
- **E (silent vs. loud failure)**: `_var_position_size_core()`'s clamp
  order is `max(min_position_gbp, min(position_gbp, max_position_gbp))`
  — the £50 floor applies **after** the ceiling, so if a dynamic ceiling
  (`resolve_dynamic_max_position_gbp()`'s output) ever drops below £50 (a
  severely depleted account, ~98%+ drawdown), the floor silently
  overrides the ceiling — contradicting the function's own docstring
  ("max_position_gbp remains a hard ceiling regardless of how low
  volatility gets"). Live and the MTM backtest engine both have a
  secondary defence (the cash clamp) that would likely catch this in
  practice before it matters; `simulate_portfolio_compounding()`
  (the current default engine, no cash clamp) does not. **Flagged, not
  fixed** — narrow trigger condition, and the same "don't touch shared
  sizing math without dedicated tests" caution as the Part C finding
  applies; the real fix is closing the Part C gap first, then revisiting
  the clamp order with a test in hand. **RESOLVED the same day — see
  "All three flagged-not-fixed items resolved" below for the exact
  trigger band and the fix.**
- **E (second instance)**: the unguarded-`.json()` pattern already known
  at `cancel_order()` (documented elsewhere in this file as a known, open
  issue) is **systemic across `Trading212Client`**, not unique to that one
  method — `get_order()`, `get_account_summary()`, `get_positions()`,
  `get_instruments()`, and `place_market_order()` all call `r.json()`
  directly with no local guard. Currently contained (every real caller
  wraps its own call in try/except, so nothing crashes a whole run), but
  none of these calls can distinguish "malformed/empty response body"
  from "genuine business failure" — a successful `place_market_order()`
  returning an unparseable body would be logged and recorded as a
  **FAILED** order even if T212 actually filled it. **Flagged, not
  fixed** — touching the shared HTTP client layer without dedicated new
  tests this late in the day carries real risk. **RESOLVED the same day —
  see "All three flagged-not-fixed items resolved" below.**
- **F (data-integrity fix consistency)**: the magnitude-outlier guard,
  amendment-conflict preference, and zero-guard (`analysis/market_cap.py`)
  are confirmed applied consistently at both real SEC-fetch call sites
  (`companyconcept` primary path and `companyfacts` fallback), not just
  the path originally tested — checked by reading both call sites
  directly. Separately: `is_price_data_stale()` (same module) is fully
  implemented with 6 passing unit tests and a clearly documented purpose,
  but has **zero call sites anywhere in production code** — not consulted
  by `analysis/universe.py`'s ranking/eligibility logic or anywhere else.
  Real infrastructure, currently inert. **Flagged** (also counted under
  Part H's dead-code finding below — same item, not double work).
- **G (flag-don't-delete consistency)**: no new violations found. The
  three existing `DELETE FROM` sites (`universe_membership` full
  reconciliation-refresh, `system_metadata`, the `price_data` bad-row
  cleanup) are each either an already-reviewed, documented exception or a
  legitimate derived-data refresh cycle (delete-then-reinsert on a fresh
  source pull), not a discarded decision or event.
  `_drop_magnitude_outliers()` dropping genuinely-wrong SEC rows with no
  authoritative correction available is a reasoned exception mirroring the
  pre-existing `price_data` NULL-row precedent, not a violation of this
  philosophy — the philosophy is about decisions/events, not garbage
  data. **Checked, nothing to fix.**
- **H (general quality)**: `is_price_data_stale()` (same finding as Part F
  above) is confirmed genuinely dead via full-repo grep — no caller
  anywhere. **Flagged.** The systemic unguarded-`.json()` pattern (Part E,
  second instance) is also a Part H finding under the same evidence.

**Summary table:**

| Area | Found | Fixed | Flagged only |
|---|---|---|---|
| Live/backtest decision divergence | 2 (the original `is_position_size_viable` gap in the backtest engine, plus `_submit_pending_orders()`'s own hardcoded duplicate) | 2 | 0 |
| Layer 1 scanner coverage | 1 keyword extended, 1 real hit caught, 1 legitimate exception documented | 1 | 0 |
| Test coverage gap | 1 (`_var_position_size_core()` untested) | 1 *(same day, see below)* | 0 |
| Documentation accuracy | 1 (stale cache-TTL claim) | 1 | 0 |
| Silent/loud failure modes | 2 (floor-over-ceiling clamp order; systemic unguarded `.json()`) | 2 *(same day, see below)* | 0 |
| Data-integrity fix consistency | confirmed consistent, 0 new gaps | — | — |
| Flag-don't-delete | confirmed consistent, 0 new violations | — | — |
| Dead code | 1 (`is_price_data_stale()`, overlaps the Part F finding) | 0 | 1 |

**Closing assessment.** Live and backtest genuinely share one decision
engine now, provably — every entry, exit, sizing-ceiling, cash-clamp, and
viability-floor decision routes through `analysis/decision.py`; Layer 1's
extended mechanical scan and Layer 2's full-replay test both pass clean,
and the mapping was independently re-derived by hand (not just read from
a prior claim) as part of this audit. Every decision still expressed as
two separate pieces of code is individually reviewed and recorded with a
specific, non-generic reason — `simulate_portfolio()`'s frozen
fixed-notional formula, live's real-API-sourced deployment check, the
SQL-vs-Python priority ranking, and (newly documented this session)
live's submission-time free-cash re-check — none of it is "nobody got
around to it." **All three items originally left open here (the
`_var_position_size_core()` test-coverage gap, the clamp-ordering bug,
and the unguarded-`.json()` pattern) were resolved the same day — see
"All three flagged-not-fixed items resolved" immediately below — closing
every gap this audit found, not just the ones it fixed inline.**

Full suite: 402 passing, confirmed again after every fix in this
completed pass. Nothing pushed; committed locally only, per this
session's standing practice.

**All three flagged-not-fixed items resolved the same day, not left as
known limitations.** Bears directly on live sizing/execution correctness,
so deferring them wasn't judged acceptable once identified.

- **Item 1 — `_var_position_size_core()` test coverage.** `tests/test_volatility.py`
  (new, 17 tests) — the single function every sizing decision on both
  sides depends on now has direct, thorough coverage: normal proportional
  sizing, the ceiling binding, the floor binding, the exact floor-vs-
  ceiling conflict this fix closes (see Item 2), a ceiling far below both
  bounds, equal ceiling/floor, an exact boundary case, the zero-variance
  early-return branch (both with a normal and a below-floor ceiling), an
  absurdly inflated ceiling not distorting a normal result, a near-zero
  floor, sqrt-of-time holding-period scaling (including the `holding_days=0`
  edge case), confidence-level sensitivity, `pd.Series`-vs-`np.ndarray`
  input equivalence, and rounding. All 17 passed on first run against the
  already-fixed implementation.
- **Item 2 — clamp ordering.** `analysis/volatility.py:_var_position_size_core()`
  changed from `max(min_position_gbp, min(position_gbp, max_position_gbp))`
  (floor applied last, could silently override the ceiling) to
  `min(max(position_gbp, min_position_gbp), max_position_gbp)` (ceiling
  applied last, always wins). **The third path's precise, concrete trigger
  condition** (as requested, not just "theoretically possible"): with the
  shipped defaults (`min_position_gbp=50`, `per_trade_pct=0.05`,
  `max_deployed_pct=0.5`), the bug could only actually admit an
  oversized trade in `simulate_portfolio_compounding()` — the current
  default backtest engine — when its running `current_balance` fell into
  the band `[£100, £1000)`: below £1000, `resolve_dynamic_max_position_gbp()`'s
  output (`current_balance × per_trade_pct`) drops below the £50 floor,
  triggering the conflict; below £100, the deployment-cap check
  (`would_deploy_pct > max_deployed_pct`) independently catches and
  rejects the resulting oversized position anyway, so the window where a
  bad trade could actually slip through and be admitted was specifically
  `£100 ≤ current_balance < £1000` — a materially depleted but not yet
  drained account. `simulate_trades()` and `simulate_portfolio()` were
  never exposed (`max_position_gbp` is a fixed £2,500 default in both, far
  above the £50 floor, never dynamically derived). Live order placement
  and `simulate_portfolio_compounding_mtm()` were already partially
  protected by their downstream cash clamp
  (`resolve_cash_constrained_position_gbp()`), which — being driven by
  genuinely available cash, never inflated by anything upstream — would
  usually shrink a floor-inflated size back down before it could exceed
  what the account actually holds; `simulate_portfolio_compounding()` has
  no such clamp (documented reason: no cash concept), which is exactly
  why it was the one path with no second line of defence. Fixing the
  shared function closes the gap on all four paths at once, by
  construction — no per-path fix was needed. As a direct, necessary
  consequence, `simulate_portfolio_compounding()` gained the same
  `is_position_size_viable()` floor-of-viability check the other two
  compounding-adjacent paths (live, MTM) already had — with the ceiling
  now correctly able to win, a severely depleted account could otherwise
  produce a position below `min_position_gbp` with nothing downstream to
  filter it out; this mirrors the identical check already present
  elsewhere rather than introducing a new pattern.
- **Item 3 — unguarded `.json()` calls.** All seven `Trading212Client`
  public methods (`get_account_summary`, `get_positions`, `get_instruments`,
  `place_market_order`, `cancel_order`, `get_order`, `get_order_history`)
  now route through one new shared method, `Trading212Client._parse_json(r,
  allow_empty=False)`, applied consistently rather than re-patching just
  the originally-flagged `cancel_order()` case. Two-tier handling: action
  endpoints (`place_market_order`, `cancel_order`, `allow_empty=True`) —
  where the HTTP status (already confirmed successful by `_request()`
  before `_parse_json()` is ever called) IS the actual success signal —
  return `{}` instead of raising on a malformed/empty body, logged at
  WARNING so the ambiguity is visible without being misreported as a
  failure; read endpoints (`allow_empty=False`, the default) still raise,
  since the body there IS the payload a caller needs and silently
  returning `{}` would risk a real account looking like it holds nothing.
  This directly closes a real bug found during the audit, not a
  hypothetical one: `data/scheduler.py:_cancel_stale_weekend_orders()`
  calls `cancel_order()` and previously had no protection against T212's
  DELETE endpoint returning 200/204 with no body (a standard REST
  convention) — a genuinely successful cancellation would raise, be caught
  by that function's own `except Exception`, and get logged/counted as a
  failed cancellation while the order stayed `PLACED` in the DB
  indefinitely, even though T212 had already cancelled it. `tests/test_t212_client.py`
  (new, 13 tests) is the first coverage in this suite to exercise
  `Trading212Client`'s real HTTP layer at all (every prior test mocked the
  client's methods directly) — mocks `requests.request` itself so
  `_request()`/`_parse_json()` genuinely run, covering: normal JSON on
  every method (no regression), both action endpoints correctly returning
  `{}` on a malformed/empty successful body instead of raising, every read
  endpoint still raising on a malformed body (including confirming
  `get_instruments()` never poisons its cache with a failed parse), and —
  the end-to-end regression proof — `_cancel_stale_weekend_orders()`
  itself, seeded with a real stale `PLACED` order and a mocked empty-body
  204 response, correctly recording `CANCELLED` rather than leaving the
  order stuck `PLACED` with a false error logged.

Full suite: 432 passing (402 + 17 + 13, all new tests passing on first
run against the already-implemented fixes). All three items are genuinely
resolved — not documented as accepted/known limitations — verified by
tests that exercise the real fixed code paths, not just asserted fixed.
Nothing pushed; committed locally only.

### Curated-40 vs. rolling-40 reproduction investigation (2026-08-02)

Direct follow-up to the "V2 stock universe broadening" / "V2 moderate
stock universe expansion" family of sections above, using the same
market-cap-ranked candidate pool for a genuinely rolling (quarterly-
reconstituted top-40-by-market-cap) universe compared against curated-40,
under the mark-to-market compounding engine
(`simulate_portfolio_compounding_mtm()` — the engine confirmed to
actually match live trading, not the frozen/cost-basis engines used for
earlier reproducibility baselines elsewhere in this file).

**Four root-cause findings, each checked against real trade-level data,
not assumed from the aggregate gap alone:**

1. **GOLD generates zero trades in curated-40 — confirmed clean, not a
   bug.** Checked directly against curated-40's full trade list and its
   test-period subset: 0 trades either way. Of curated-40's 40 tickers,
   only 39 ever traded; GOLD is the sole exception, exactly matching its
   permanently-empty `STOCK_MEMBERSHIP` window (see "V2 architecture" and
   "Present-day market-cap ranking bias" above for why GOLD is kept in
   the list at all despite never being a real S&amp;P 500 constituent).
   `passes_entry_gate()` rejects it consistently.
2. **META's contribution is real but not dominant.** 39 test-period
   trades, +£19,583.67, **8.2%** of curated-40's test-period P&amp;L —
   the 4th-largest single-ticker contributor, behind NVDA (13.3%), AMD
   (10.9%), and AVGO (10.2%). META is structurally excluded from
   rolling-40 by a known SEC/XBRL data gap (`market_cap_history` has zero
   rows for it — see "Market-cap data infrastructure" above for the "44
   genuinely unresolvable" tickers this belongs to). Recovering META's
   market-cap data would narrow curated-40's advantage somewhat but would
   not close it alone — NVDA+AMD+AVGO+META together are 47.4% of
   curated-40's total test-period profit, several comparably-sized
   contributors, not one outsized confound.
3. **Concentration — broad-based in both variants, at the trade level.**
   Top-1/3/5 tickers' share of test-period P&amp;L: curated-40 (39 traded
   tickers) 13.3% / 34.3% / 49.6%; rolling-40 (78 traded tickers) 11.1% /
   28.5% / 41.1%. Curated-40 is somewhat more concentrated by *ticker*
   (mechanically, from trading a fixed, narrower universe throughout),
   but at the *trade* level both variants are almost identically
   un-concentrated (top-20 winning trades ≈17-18% of winning P&amp;L in
   both cases) — neither variant's edge rests on a handful of blowout
   trades.
4. **Does market-cap rank predict trade quality? Mixed evidence, no
   forced conclusion.** "Core" rolling-universe members (top-40 in ≥50%
   of the 67 quarterly reconstitutions, 2009-12-31→2026-06-30) show a
   higher win rate than "marginal" boundary-hugging members (49.3% vs.
   44.0%) but a *lower* average return per trade (1.22% vs. 1.83%) — rank
   predicts eligibility consistency, not clearly per-trade magnitude, and
   the marginal bucket's sample (216 trades across 48 tickers) is too
   thin to trust that reversal as a stable effect. A separate check
   against the top-100/150 broadening studies' data (a different engine,
   only directionally informative) found no sharp quality cliff right at
   the rank-40 boundary specifically.

**A genuine, real bug found and fixed as a byproduct of this
investigation — see "Live pricing bug..." precedent for the same class
of fix.** The naive per-ticker market-cap ranking loop
(`analysis/market_cap.py:market_cap()`, via `_close_price_as_of()`)
opened a fresh SQLAlchemy connection on every single call — confirmed to
produce ~30,000 connections for one ranking step, and to leave a
comparable real workload still unfinished after 15+ minutes. Fixed
2026-08-02 by mirroring `_get_shares_series()`'s existing per-ticker
cache pattern: a new `_price_cache`, populated by one query per ticker
(the ticker's full ascending close-price history), with
`_close_price_as_of()` rewritten to `bisect.bisect_right()` against the
cached series instead of a SQL round-trip per call.
`get_engine()`'s own global `NullPool` behaviour is unchanged (deliberate,
for Streamlit's multi-threaded-rerun safety — see "Database writes"
below). Verified before/after: 105 `get_engine()` calls → 10, for an
identical 100-lookup/5-ticker workload; at real production scale, 30,753
genuine `market_cap()` calls completed in 51.2 seconds. Two regression
tests (`tests/test_market_cap.py`) confirmed to genuinely fail against
the pre-fix code (temporarily restored via `git stash` and re-tested,
not merely reasoned about) before being confirmed to pass against the
fix: `test_close_price_as_of_is_cached_across_calls` (mirrors the
existing shares-cache test) and
`test_market_cap_bulk_ranking_creates_bounded_not_linear_connections`
(asserts `get_engine()` call count stays `≤ 2×ticker_count` regardless of
lookup count).

**A second real fragility found and fixed the same way — see
"Consolidated fixes" below for the full writeup.**
`analysis/universe.py:get_max_holding_days()` used to route curated
`STOCK_MEMBERSHIP` tickers and every other non-ETF ticker through two
*separately-configured* values (`trading212_invest.stock_max_holding_days`
and `backtest.fallback_max_holding_days`) that happened to both be 30 —
coincidentally equal, not structurally linked, the exact same fragility
class as the `is_etf`-inference bug fixed earlier the same day. This
investigation's own data showed the "fallback" path was not an edge case
at all: 82 of rolling-40's 115 ever-admitted tickers (71%) — real S&amp;P
500 stocks simply outside the curated `STOCK_MEMBERSHIP` dict — hit it as
their everyday, dominant path. Consolidated to one value.

**Reproducibility gap — found, investigated honestly, and only partially
explained.** Two fully independent reproductions of curated-40's
test-period backtest (same code, same config, same window, run 8.5 hours
apart) produced byte-identical results to each other
(**£288,323.01**), proving the methodology is internally deterministic —
but both came in **~35% below** a previously-cited headline figure of
**£442,558.70**. NVDA/AMD/AVGO/META (the four largest contributors) were
diffed trade-by-trade between the two reproductions and matched to the
penny — ruling out ordinary data accumulation as the cause of any
divergence *between the two reproductions themselves* (there wasn't
one). The gap against the *original* £442,558.70 figure could not be
traced, because the original run's trade-level output no longer exists
— only its methodology and an aggregate summary survived the deletion of
the research clone that produced it (see the new "Extended 2026-08-02"
paragraph under "Research reproducibility standard" above, added
directly because of this gap). **Correction, stated plainly**: an
earlier verbal report of this investigation characterized the two
reproductions matching each other as proof the figure was stable "across
a real production EOD cycle" / "spanning genuine production data
updates." **That characterization was wrong and is corrected here.** The
two reproduction runs were both taken within a single Saturday-to-Sunday
window, during which no EOD job fires at all (see the scheduler table at
the top of this file) — no real data changed between them. What was
actually proven is narrower and still genuinely useful: the code is
internally deterministic (identical inputs reliably produce identical
outputs), not that the £288,323.01 figure is stable against real,
changing data. Rolling-40's own reproduction (a single attempt so far,
**£162,567.27** against an original **£159,558.73**, ~2% apart) has not
yet received the same two-independent-reproduction scrutiny curated-40's
figure did.

**Current standing, until a genuine post-EOD-cycle reproduction is
run:** treat **£288,323.01** as curated-40's currently-trustworthy
test-period figure; treat the original **£442,558.70** as unverifiable
against current data (its provenance can no longer be audited) rather
than wrong outright. **The real outstanding verification — reproducing
either figure again after a genuine Monday EOD cycle has actually run,
to see whether real data movement (not just code determinism) narrows or
widens either gap — has not been attempted and is explicitly out of
scope for the work done on 2026-08-02; it can only be done after 21:30
UTC Monday at the earliest.**

### Consolidated fixes from the curated-40 investigation (2026-08-02)

Four items from the investigation above, applied for real (not left as
disposable-clone-only findings):

- **`analysis/market_cap.py` connection-pooling fix** — see the
  investigation section above for the full before/after numbers and
  regression-test detail. Landed in the real module, not a throwaway
  script.
- **`get_max_holding_days()` consolidation** — `analysis/universe.py`
  now dispatches purely on `ETF_TICKERS` membership (90 days) vs.
  everything else (a single `trading212_invest.stock_max_holding_days`,
  default 30) — `STOCK_MEMBERSHIP` membership no longer affects the
  holding-day cap at all, only `is_member()`'s own separate eligibility
  check. `config.yaml`'s `backtest.fallback_max_holding_days` key is
  removed (both it and `stock_max_holding_days` were already 30 in
  production, so this is a pure fragility fix with zero effect on any
  already-computed backtest result — verified directly: `AAPL` (curated),
  `VOO` (ETF), and `MU` (a real stock outside `STOCK_MEMBERSHIP`, exactly
  the dominant rolling-universe-research case) all resolve to the
  identical values before and after). Every test that previously
  exercised the removed fallback branch (`tests/test_universe.py`,
  `tests/test_portfolio_backtest.py`) was updated to exercise the
  consolidated single-value dispatch instead, not deleted.
- **Research reproducibility standard extended** — see the new
  "Extended 2026-08-02" paragraph above: a finding that ships as a
  headline figure now needs its full trade-level output preserved
  somewhere durable (not just the methodology) before its disposable
  research clone is deleted.
- **The false "spans a real production EOD cycle" claim corrected** —
  see the investigation section above; it was never previously committed
  to this file in that wrong form (it existed only as a verbal report),
  so this is its first, corrected appearance in the permanent record,
  not a retraction of prior committed text.

Full local suite: 434 passing (432 + the 2 new `market_cap.py`
regression tests) after all four changes together. Nothing pushed;
committed locally only, per this session's standing practice. The real
post-Monday-EOD reproduction check remains genuinely outstanding — not
attempted as part of this work, and not attemptable before 21:30 UTC
Monday at the earliest.

### Position sizing, volatility, and tie-breaking — curated-40's return advantage largely re-explained (2026-08-02)

Direct follow-up to the investigation above, prompted by curated-40's
max position size (£31,682.80) sitting nearly 3x rolling-40's
(£11,426.10) — visible in every prior comparison but never directly
investigated as a possible driver of the return gap, separate from
which stocks were actually picked.

**Headline finding, stated first, per this file's own convention: ~73%
of the raw £ return gap between curated-40 and rolling-40 is a
position-sizing/compounding-carryover artifact, not stock selection —
but not for the reason initially hypothesized.** The working hypothesis
going in was "curated-40's fixed mega-cap roster is structurally calmer
than rolling-40's rotating mix of newer entrants, so VaR sizes it
bigger" — **checked directly and refuted** (see below). The real
mechanism is simpler and purely structural: `per_trade_pct` sizes every
new position as a percentage of the account's *current, compounding*
balance, and because train/test is a post-hoc split of one continuous
2007–2026 run (not two independent simulations), curated-40 had already
compounded to a materially larger balance than rolling-40 by the moment
the test period even begins — independent of anything about the test
period's own trades.

**Part A — position sizing, quantified precisely.** Average test-period
`position_gbp`: curated-40 £12,116.01 vs. rolling-40 £7,106.54 (1.70x).
Ceiling-hit rate (fraction of sizing events where `per_trade_pct`'s
ceiling, not VaR's own volatility-scaled risk budget, was the binding
constraint) went the *opposite* direction from the volatility
hypothesis: rolling-40 hits its ceiling **more** often (89.58% vs.
curated-40's 85.16%) — ruling out "curated hits the ceiling more because
it's calmer" as the mechanism before Part B's volatility check even ran.
The real driver, found by checking account balance at the exact
2020-01-01 train/test boundary: curated-40 entered the test period with
**£170,314.16**, rolling-40 with **£114,388.90** — a 1.49x gap purely
from 2009–2019 performance, before the period being compared at all.
That one number then mechanically inflates every subsequent test-period
position via the `per_trade_pct` ceiling, regardless of that trade's own
merit. A rough reweighting (holding each trade's own `pnl_pct` fixed,
rescaling `pnl_gbp` to a common reference position size — not a full
re-simulation, sized to be honest about the effect, not to prove a
predetermined answer) showed the raw £125,755.74 test-period gap
(curated £238,323.01 vs. rolling £112,567.27, a 2.12x ratio) shrinks to
**£33,629.78 once both variants are reweighted to a common average
position size — 73.3% of the raw gap disappears**. What survives —
1.910% vs. 1.482% mean return per trade, a **1.274x** ratio — matches
the plain, £-unweighted sum-of-`pnl_pct` comparison almost exactly
(20.57 vs. 16.15, same 1.274x), confirming this is the genuine,
sizing-independent selection edge, not an artifact of the reweighting
method itself.

**Part B — the "curated-40 is calmer" premise is refuted, not
confirmed, by real numbers.** Per-ticker annualized realized volatility
(daily log-return stdev × √252, test period): curated-40 mean 34.97% /
median 33.30% (n=40) vs. rolling-40 mean 36.80% / median 33.39% (n=115)
— medians essentially identical; rolling-40's slightly higher mean is
driven entirely by its much wider tail (SNDK at 105.90% vs. curated-40's
own max, TSLA at 64.96%), not a broad structural calmness difference.
More decisively, the trade-weighted `|holding_var|` — the actual
volatility measure `_var_position_size_core()` sizes against, pulled
from real sizing-event instrumentation rather than a proxy — shows
curated-40 **slightly more** volatile, not less: mean 14.608% vs.
rolling-40's 14.061% (median 13.574% vs. 13.111%). This is fully
consistent with, and mechanistically explains, Part A's ceiling-hit-rate
finding running the "wrong" way: marginally *higher* volatility means a
marginally *smaller* raw VaR-desired size relative to the ceiling, so
curated-40 hits its ceiling *less* often — the reverse of what the
original volatility hypothesis predicted. **Any future comparison
between a curated and a rolling/broadened universe should not assume a
narrower, longer-established roster is automatically calmer** — checked
directly here, twice, and found not to hold.

**Part C — tie-breaking: present in the code exactly where suspected,
confirmed empirically inert.** `analysis/backtest.py`'s admission logic
(`_admit_group()`, both `simulate_portfolio_compounding_mtm()` at line
~1261 and the cost-basis engine's identical logic at line ~911) sorts
candidates via `key=lambda c: (-c[1], per_ticker[c[0]]["idx"])` —
momentum descending as the primary key, but a secondary tie-break on
`idx`, the ticker's position in whatever order the caller's
`tickers_cache` list was built in. Since `analysis.universe.ALL_TICKERS`
is `tuple(sorted(...))` and every research script built for this
project's rolling-universe work also sources its ticker list via
`sorted(...)`, this tie-break is, in practice, still alphabetical ticker
order — the earlier priority-engine fix (see "Trade prioritization and
capacity-allocation engine" above) demoted the old ordering rule to a
tie-break rather than eliminating it outright. Checked empirically
whether it ever actually fires, not assumed either way: scanned every
day across the full 2009–2026 window (4,170 days with ≥2 eligible
candidates, both universes) for exact-float momentum ties among
signal-eligible tickers — **zero ties found, in either universe, across
the entire window**. Real, continuous trailing-12-month-return momentum
values computed from real daily closes essentially never collide
exactly between two different tickers. Confirmed present, confirmed to
have never fired, contributes nothing to the curated-40 vs. rolling-40
gap as measured.

**Updated, direct conclusion.** Of the ~£125,756 raw test-period £ gap,
roughly 73% (≈£92,126) is the position-sizing/compounding-carryover
artifact described above — not volatility-driven (Part B refutes that
specific mechanism directly) and not an ordering/tie-break artifact
(Part C confirms the residual tie-break exists but never fires). The
remaining ~27% (a **1.274x** ratio, 1.910% vs. 1.482% average return per
trade) is curated-40's genuine selection/timing edge — real, but
meaningfully smaller than the raw headline figures suggested. **Any
future citation of the curated-40 vs. rolling-40 comparison should use
the sizing-normalized ~1.27x figure as the trustworthy measure of
selection quality, not the raw ~2.1x £ figure**, which is substantially
inflated by an accounting mechanism (compounding-balance carryover from
before the comparison window) unrelated to which stocks were actually
picked.

Full local suite: 434 passing, confirmed both before and after (this
investigation only monkeypatched functions at runtime inside a
throwaway research script — no source files were modified). Nothing
pushed; the investigation itself was pure analysis, not a code or config
change, so there is nothing to commit here beyond this write-up.

### No market-cap gate at all — the "broadening dilutes" finding reverses under the corrected engine (2026-08-02)

Direct follow-up to the two investigations immediately above, and a
deliberate re-test of "V2 stock universe broadening" (earlier in this
file), which found genuine dilution when the candidate pool widened
from curated-40 to a full ~500-stock, market-cap-ranked universe. That
original study predates several real infrastructure defects this
project has since found and fixed: no cross-ticker portfolio cap at all
(see "Portfolio-capped backtest engine"), alphabetical/config-order
admission priority instead of momentum strength (see "Trade
prioritization and capacity-allocation engine"), a cost-basis rather
than genuine mark-to-market deployment tracker (see "Live/backtest
deployment-cap parity" and "Mark-to-market unbounded compounding
defect"), an `is_etf` inference bug, and the `stock_max_holding_days` /
`fallback_max_holding_days` split fragility consolidated earlier this
same day. Per this file's own reproducibility standard, that original
finding was exactly as provisional as curated-40's and rolling-40's own
figures were before their re-verification — this section re-tests it
under the corrected engine rather than assuming it still holds.

**Methodology — no market-cap pre-filter, full 503-candidate pool,
momentum ranks and capacity constraints are the only gates.** Built from
the same point-in-time-correct 503-candidate S&amp;P 500 pool this
project's market-cap research has used throughout (reconstructed via
`analysis/universe.py:_reconstruct_windows()` against the same
`fja05680/sp500` source, reproducing exactly 503 candidates, 500 usable
after excluding 3 for insufficient history — `FDXF`, `HONA`, `Q`,
matching the original study's own exclusions precisely). No quarterly
market-cap ranking or reconstitution is applied — every candidate that
clears the real, existing momentum signal and point-in-time-membership
gate competes on equal footing, exactly as the live universe's own
signal computation and entry gating already work, just applied to 503
tickers instead of 40.

**Balance-reset methodology, applied to all three variants for a
genuinely symmetric comparison.** The position-sizing investigation
immediately above found that a single continuous 2007–2026 walk
contaminates any test-period comparison — whichever variant compounds to
a bigger balance during training mechanically sizes every test-period
trade bigger, independent of that trade's own merit. Rather than
reweight after the fact again, this study built
`simulate_portfolio_compounding_mtm_reset_at_boundary()` — the real
production function, extracted via source introspection (not retyped,
eliminating transcription risk) with one additive change: the tracked
account balance is renormalized to exactly £50,000 the first day on or
after 2020-01-01, before that day's entries are evaluated (training-
period signal history and membership eligibility are unaffected; only
the £ balance tracker resets). Verified byte-identical to the real
function when the reset is unused (a curated-40 run with no reset
matched the real function's own 2,624 trades exactly) before being
trusted for anything. Curated-40 and rolling-40 were **also** re-run
with the identical reset treatment (cheap once the engine was validated,
~3 minutes) specifically so all three sit on the same clean footing —
not comparing a reset variant against two unreset ones.

**Full comparison, £50,000-reset basis, all three variants, test period
(2020-01-01→2026-07-31):**

| | Curated-40 | Rolling-40 (quarterly) | Full-500 (no gate) |
|---|---|---|---|
| Trades | 1,073 | 1,091 | **1,182** |
| Win rate | 47.53% | 45.83% | **48.82%** |
| Profit factor | 1.77 | 1.56 | **1.77** |
| CAGR | 14.31% | 11.08% | **19.83%** |
| Max drawdown | −15.83% | −11.89% | **−27.43%** |
| Test £50,000 → | £119,600.48 | £99,239.48 | **£162,802.54** |

**The edge survives sizing normalization and is even clearer per-trade.**
Average test position sizes across the three reset variants are already
close (£3,548.92 / £3,151.99 / £3,504.42, ≤1.13x spread — the reset
largely equalized sizing on its own), but a lighter-touch reweighting to
a common £3,401.78 reference size confirms the full-500 variant's edge
is not a sizing artifact: mean return per trade 2.606% vs. curated-40's
1.912% and rolling-40's 1.455% — the largest gap of the three, not a
shrinking one.

**Removing the gate helps — materially on return, at a real, quantified
cost on risk.** Full-500 beats both gated variants on CAGR and matches
curated-40's best-in-class profit factor, but its max drawdown
(−27.43%) is meaningfully worse than either gated variant's — a wider,
unconstrained candidate pool trades more often and absorbs more
idiosyncratic single-name risk along the way. A genuine trade-off, not a
one-sided improvement; anyone adopting this needs to accept materially
worse drawdown for materially better return, not assume both improve
together.

**The −27.43% figure is the fixed-denominator convention (against
starting capital); the genuine peak-relative decline is smaller — real
but a smaller number, checked directly, not assumed.** Computed from the
actual full-500 test-period equity curve: the fixed-denominator worst
point (2026-07-29, equity £161,421.09 against the test period's overall
peak of £175,135.81 on 2026-07-10) reads as −27.43% of the £50,000
starting capital. The genuine worst PEAK-RELATIVE decline occurred
earlier and at a different point entirely — 2025-04-25, equity
£96,695.62 against its own then-current running peak of £108,874.01 — a
**−11.19%** decline against the peak it actually fell from. The gap
between the two conventions is **16.24 percentage points** (a ~2.45x
overstatement by the fixed-denominator figure) — smaller than the ~97-
point gap the same convention produced for an earlier, uncapped
continuous-walk comparison this session found (the reset-at-boundary
methodology genuinely bounds the distortion, it just doesn't eliminate
it). **−11.19% is the number that reflects genuine downside risk from any
point an investor could actually have been holding at peak; −27.43%
should not be read as that.**

**Composition check — the extra edge is not a mega-cap rediscovery.**
Broken down by real, point-in-time SEC-sourced market cap at entry
(resolved for 1,107 of 1,182 test trades — 75 trades on tickers with
unresolvable SEC data, e.g. META/XOM/WFC, excluded from this specific
breakdown only, consistent with the "44 genuinely unresolvable" list
under "Market-cap data infrastructure" above): the mega-cap tier (≥£200B
— the only tier either gated variant can ever reach) has the *lowest*
mean return (1.65%) and win rate (44.5%) of the four profitable tiers.
The extra edge is overwhelmingly concentrated in the **£10–50B
"mid-large" tier** (518 trades, £53,167.24 pnl, 3.16% mean return,
51.5% win rate) — two orders of magnitude below rolling-40's own top-40
floor (£271.3B as of 2026-06-30) and therefore structurally unreachable
by any size-ranked gate at all. **Correction (2026-08-02) — the small-cap
(&lt;£2B) tier's reported −5.73% mean return / 13.6% win rate is a data-
quality artifact, not a genuine small-cap risk signal.** A direct follow-up
check against real point-in-time membership windows found none of the 7
affected tickers (`CMG`, `SPG`, `SMCI`, `FOX`, `FOXA`, `HOOD`, `CVNA`) is
a small or declining company at all — `CMG`, `SPG`, `FOXA`, `FOX` are all
long-standing, still-active index constituents, and `SMCI`/`HOOD`/`CVNA`
are recent *additions*; all seven are genuinely large, multi-billion-
dollar companies. Traced to two distinct `analysis/market_cap.py` bugs,
fixed the same day (see "Two real market-cap data bugs found and fixed"
below): a handful of stored placeholder/near-zero shares-outstanding
values being forward-filled as real readings, and a split-adjustment
basis mismatch between SEC's un-adjusted shares data and yfinance's
retroactively-adjusted prices. With both fixed, these 7 tickers correctly
resolve to their real, multi-billion-pound market caps (or, for the two
with insufficient remaining SEC data, `None` rather than a fabricated
tiny value) — they were never small-caps in the first place, so no
"smaller is always better/worse" conclusion should be drawn from this
tier at all pending a fresh re-run with the fix applied. Ticker-level
confirmation
matches: of 190 distinct tickers traded in the test period, **171 (90%)
are outside curated-40's fixed 40**, contributing 968 of 1,182 trades
(81.9%) and £95,480.24 of £112,802.54 total profit (84.6%) — real,
identifiable momentum stories in growing-but-not-yet-mega companies
(`WDC`, `STX`, `SNDK`, `PLTR`, `GEV`, `GLW`, `MU`, `LRCX`, `INTC`, `COP`,
among others), not statistical noise.

**Original full-500 study confirmed genuinely unreconstructable, not
guessed at.** Checked directly: no leftover files anywhere on the
server, and the schema no longer has a `backtest_runs` table at all
(removed post-V1-removal) — the original study's trade-level data does
not survive anywhere; only its prose summary in this file does,
predating the trade-level-preservation standing requirement added
earlier the same day.

**Updated statement — the original "broadening dilutes" finding does
NOT hold under the current, corrected engine, and the reversal has an
identifiable, not merely coincidental, cause.** Re-testing the identical
underlying question (broad vs. narrow universe) under the corrected
engine flips the conclusion: broadening now improves both CAGR and
profit factor, at a real, quantified drawdown cost, and the improvement
traces to a specific, economically sensible source (mid-large-cap
momentum names structurally invisible to any market-cap-ranked gate)
rather than to noise or a methodology artifact. This is precisely the
kind of infrastructure-dependent reversal this file's own
reproducibility standard exists to catch: the original finding was
correct for the engine it ran on, and that engine no longer exists.

Full local suite: 434 passing, confirmed before and after. Trade-level
output for all three reset variants (ticker/direction/entry_date/
exit_date/entry_price/exit_price/exit_reason/pnl_pct/position_gbp/
pnl_gbp/stop_distance_pct/holding_days/is_win — the same shape
`backtest_trades` already uses) was exported and preserved before the
research clone was deleted, per the trade-level-preservation standing
requirement this session added specifically because of the unrecoverable
original full-500 study. Nothing pushed as a result of the investigation
itself (pure analysis, no code/config change); this write-up is the
durable record.

**Second, independent reproduction (2026-08-02) — exact match, the
finding is settled.** Per this file's own reproducibility standard, a
finding this consequential got the same second-reproduction scrutiny
curated-40 and rolling-40 each received — using two genuinely different
implementation techniques from the first build, not a re-run of the same
script: the 503-candidate membership windows were rebuilt via a
vectorized pandas pivot + run-length-encoding approach (instead of the
original per-ticker `_reconstruct_windows()` loop), and the reset-at-
boundary engine via an AST-transformation splice of the balance-reset
check (instead of the original `inspect.getsource()` + textual anchor
replace) — verified byte-identical to the real, unmodified production
function before being trusted for anything. Cross-checking the
independently-rebuilt membership windows against production's real data
for all 503 candidates (not a sample) found exactly one mismatch —
**META** — traced to the second build searching only for the literal
ticker "META" and missing its entire 2013–2022 "FB" era, which
production's real `_reconstruct_windows()` correctly merges via
`_KNOWN_TICKER_RENAMES` (Facebook→Meta was a ticker rename, 2022-06-09,
not a membership lapse). Fixed by merging the legacy ticker's history
before windowing, the identical semantic reached by a different
mechanism; re-verified 0/503 mismatches afterward. With that one bug
caught and closed *before* it could contaminate the comparison, the full
independent pipeline reproduced every figure — trades, £-endings, win
rate, PF, Sharpe, CAGR, max drawdown, and the full market-cap-tier
composition breakdown down to the exact ticker-level percentages (90.0%
of traded tickers outside curated-40, 81.9% of trades, 84.6% of
profit) — **exactly, to the penny, with no residual gap left to
explain**. This is a stronger reproduction outcome than either
curated-40's (still an unresolved ~35% gap between its original and
re-verified figures) or rolling-40's (a small, traced-and-fixed ~2%
drift from one SEC filing tie-break affecting 2 of 67 quarters) — the
full-500 finding should now be treated as at least as settled as
rolling-40's, and did not require a second independent build to correct
anything about its own headline conclusion.

### Two real market-cap data bugs found and fixed (2026-08-02)

Direct follow-up to three specific checks on the full-500 no-gate
finding — investigating what the 22 small-cap (&lt;£2B) trades in that
study's composition breakdown actually were, before drawing any
conclusion about small-cap risk. Checked against real point-in-time
membership windows (`analysis.universe._reconstruct_windows()`): none of
the 7 affected tickers (`CMG`, `SPG`, `SMCI`, `FOX`, `FOXA`, `HOOD`,
`CVNA`) is a small or declining company at all — `CMG`, `SPG`, `FOXA`,
and `FOX` are all long-standing, still-active index constituents (member
since 2011, 2002, 2004, and 2015 respectively), and `SMCI`/`HOOD`/`CVNA`
are recent *additions* to the index (2024/2025/2025) — the opposite of
"declining toward removal." All seven are genuinely large, multi-billion-
dollar companies. Traced to real stored data, not assumed, this resolved
into two distinct `analysis/market_cap.py` bugs, both fixed the same day.

**Bug 1 — placeholder/near-zero shares-outstanding values being
forward-filled as real readings.** `shares_outstanding_as_of()` forward-
fills whatever the most recent stored value is — but for 5 of the 7
tickers, that stored value was a literal data-quality placeholder, not a
genuine reading. Checked directly against the raw stored rows: `FOX` and
`FOXA` each have exactly **one** stored row, a literal `1.0` (their
2019-03-18 spinoff-day filing — already known and named in
`_drop_magnitude_outliers()`'s own docstring as a case its
`_OUTLIER_MIN_ROWS` guard deliberately skips, since a single-row series
is too short for a median-based check to mean anything — but nothing
downstream ever caught it defensively). `SPG`, `HOOD`, and `CVNA` each
carry one or more stored rows of literal `0.0` that
`_dedupe_earliest_filed()`'s own `val<=0` guard should already exclude at
write time, but evidently didn't for these specific rows (predating that
guard, or a since-patched gap). **Fix**: a new, deliberately narrow
`_MIN_PLAUSIBLE_SHARES = 50.0` floor, applied defensively at READ time in
`_get_shares_series()` — protects every consumer regardless of how a bad
value entered the table, not just future writes. Kept intentionally low
(catches only genuine near-zero placeholders like 0/1, not a general
small-cap detector) after an initial attempt at a much higher,
"no real company has this few shares" floor (100,000) broke ~20
pre-existing tests that use small round numbers as illustrative fixtures
with no intent to represent realistic magnitudes — reverted in favour of
this narrower, better-targeted value plus the separate mechanism below
for genuinely large stray anomalies.

**Bug 2 — a split-adjustment basis mismatch between SEC shares data and
yfinance prices.** SEC's shares-outstanding figures are NOT retroactively
adjusted for later stock splits (a filing genuinely reports the real
share count as of that filing date), but `price_data`'s close prices
(yfinance-sourced) ARE retroactively split-adjusted — standard practice
for financial data providers, and already documented elsewhere in this
project for its price-history implications, but never previously
connected to `market_cap.py`'s own `shares × price` formula. For any
query date before a ticker's real split, this understates market cap by
roughly the split ratio: checked directly, `CMG`'s real 50:1 split (June
2024) and `SMCI`'s real 10:1 split (October 2024) each produced this
exact defect — CMG's real ~$70–80B market cap was computing as ~£1B for
pre-split 2024 query dates. **Fix**: `_split_adjust()`, a new function
in `_get_shares_series()`'s cache-population path — walks the sorted
shares series backward from the most recent row, and wherever a
consecutive-row ratio lands within 5% of a real, known stock-split
multiple (`_PLAUSIBLE_SPLIT_RATIOS`: 2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25,
30, 40, 50, 100, or the matching reverse-split reciprocal), retroactively
scales every earlier value up to the same basis — mirroring exactly how
yfinance already retroactively adjusts prices, so `market_cap()`'s
`shares × price` stays internally consistent at every historical date,
not just post-split ones. Deliberately restricted to close matches on
real, round split ratios (not "any large jump") specifically because
genuine organic share-count change — buybacks, secondary offerings,
equity grants — essentially never lands within a tight tolerance of one
of these exact values by coincidence; verified this holds for the
already-known real splits documented elsewhere in this module (NVDA
4.01x/9.97x, GOOGL 19.75x, AAPL, TSLA) as well as the two new ones found
here. Multiple splits in one ticker's history compound correctly (walks
backward, so each earlier split multiplies on top of any later
adjustment already applied) — verified against NVDA's real two-split
history (4:1 in 2021, then 10:1 in 2024) directly.

**A third, distinct anomaly found the same day, closed by a related but
separate mechanism.** `CMG` also carries a single stray stored row of
`27,962.0` (2022-04-25) sitting amid neighbours in the tens-of-millions
range — a ~1,000x magnitude defect that should have been caught by
`_drop_magnitude_outliers()` at write time but evidently wasn't for this
specific row. Too large in absolute terms to be caught by the narrow
`_MIN_PLAUSIBLE_SHARES` floor above (50 vs. 27,962), so a second,
genuinely relative check was added: `_is_relative_magnitude_outlier()`,
the exact predicate `_drop_magnitude_outliers()` already uses at write
time (extracted so both call sites share one implementation, not two
that could drift), re-applied defensively at read time in
`_get_shares_series()` against whatever is actually stored, regardless
of when or how it got there — skipped below `_OUTLIER_MIN_ROWS` (5), the
same robustness guard the write-time version already uses.

**Verified directly against real production data before being trusted —
not just synthetic tests.** Ran the fixed code against a read-only copy
of the real production database (never touching the live one), compared
to the same real data under the original, unfixed code: at a genuine
pre-split query date (2024-05-01), `CMG`'s computed market cap corrected
from **£1.72B to £86.2B** (exactly the 50x its real split ratio implies:
27,467,000 raw shares → 1,373,350,000 split-adjusted), and `SMCI`'s
corrected from **£4.13B to £41.3B** (exactly the 10x its real split
implies). For `SPG`/`HOOD` (partial, stale-but-genuine data remaining
after the placeholder rows are filtered), market cap now resolves to a
plausible, stale-but-not-catastrophically-wrong figure instead of
literal £0; for `FOX`/`FOXA`/`CVNA` (essentially no usable data
remaining), market cap now correctly returns `None` (honestly
"unavailable") instead of a fabricated near-zero value computed from a
placeholder share count.

**What this fix does NOT do, stated plainly.** It does not perform a
live SEC re-fetch for `SPG`, `FOX`, `FOXA`, `HOOD`, or `CVNA` — these five
tickers' remaining stored history is either genuinely stale (SPG/HOOD, no
filing captured since ~2010/2021) or effectively empty (FOX/FOXA/CVNA).
The code fix stops the WORST failure mode (a fabricated, wildly-wrong
market cap) for all five, but does not by itself restore full, current
data completeness — a live `backfill_shares_outstanding()` re-fetch for
these five specific tickers (already-existing, rate-limited, safe
infrastructure) would be a natural, low-risk follow-up, not attempted
here since it wasn't part of what was asked and involves a live write to
the production database.

Full local test suite: 447 passing (434 + 13 new tests covering both
fixes — `tests/test_market_cap.py`), confirmed before and after. One
pre-existing test (`test_shares_outstanding_as_of_forward_fills`) had its
"simulated split" fixture value changed from a genuinely split-like ratio
(~3.96x, coincidentally close to 4) to a non-split-like one (~1.29x),
since it now genuinely triggers the new split-adjustment logic and its
original assertions were written before that logic existed — its own
intent (forward-fill/no-look-ahead semantics) is unaffected; dedicated
split-adjustment coverage lives in the new tests instead. Committed
locally only, per this session's standing practice — the small-cap tier
composition figures in the section above are corrected in place rather
than requiring a full study re-run, since the fix's effect on those
specific 7 tickers is independently verified above.

### ETF/stock fair-competition study — guaranteed priority, not merit, drives most current ETF allocation (2026-08-02)

Direct follow-up to the full-500 no-gate momentum work above. Today's
priority-admission engine ranks ETFs and stocks in two entirely separate
lists, with every ETF fully admitted before any stock is even
evaluated — an artifact of the exchange-timing split (real live
submission happens at 07:00 UTC for ETFs, LSE hours, and 13:30 UTC for
stocks, NYSE hours; see "Exchange-aware order submission" above), not a
designed ratio. This mattered less against the old fixed 40-stock
curated pool; it matters far more now that the direction has shifted to
a full ~500-candidate, no-market-cap-gate approach, where far more real
stock candidates are genuinely competing for the same limited capacity —
a guaranteed, merit-independent ETF head start is a bigger distortion
under that pool than it was under the narrower one.

**Why this had to run before any ETF-sleeve-value test, not alongside or
after one.** A sleeve-value test run under the current guaranteed-
priority mechanism cannot distinguish "ETFs genuinely earn their place on
momentum merit" from "giving *anything* guaranteed capacity access
changes the numbers regardless of merit" — precisely the confound this
project's own "Portfolio-capped validation findings" section already
surfaced once, for a different pair of variants. This study exists
specifically to remove that confound before a sleeve-value question is
asked at all.

**The core design problem, and how it was actually resolved.** A naive
fix — rank all candidates together by momentum, but still physically
submit ETFs at 07:00 and stocks at 13:30 — does not produce fair
competition, it only relocates the unfairness: a lower-ranked ETF could
still consume capacity before a higher-ranked stock ever gets its own
turn, purely because it was evaluated first in wall-clock time. Genuine
fairness requires deciding *who wins* using the complete, combined
picture (both asset classes ranked together) before either submission
job consumes any capacity — while still respecting the real reason a
separate submission-time capacity re-check exists live (capacity can
genuinely shift between decision and submission — a position closing, an
emergency exit). Both principles were reconciled deliberately, not by
picking one:

- **Decision time (EOD)**: the existing `_admit_group()` capacity
  allocator (unchanged) is now called **once**, against every eligible
  ticker from both asset classes together, rather than twice in sequence
  (ETF group first, stock group second). Since `_admit_group()` already
  sorts purely by `(-momentum, idx)` with no asset-class term in that
  key, and already resolves membership/entry-gating per ticker
  internally, merging the input lists was sufficient — no other logic
  needed to change. This is what actually makes the ranking fair.
- **Submission time (live, 07:00/13:30 UTC)**: a daily-bar backtest has
  no separable "submission moment" inside one simulated day the way live
  trading genuinely does (real hours elapse between the two real
  submission jobs) — so for the BACKTEST specifically, the single
  combined admission pass already *is* the complete, correct
  implementation of both principles together; there is no second,
  later capacity snapshot inside one simulated day to build a check
  against. This was stated explicitly rather than silently dropped: the
  real, live form of this check (re-confirming a provisionally-won slot
  against genuinely live T212 account state at each actual submission
  job) belongs in `brokers/trading212.py`, and is flagged as a
  recommended follow-up below — **not built or modified as part of this
  study**, which was research-only against a disposable clone.

**Verification discipline matched the day's established standard.** The
combined-admission variant was built via AST transformation of the real,
current `simulate_portfolio_compounding_mtm()` (production commit
`d255f26`), not retyped. Both the untouched baseline and the new fair
variant were confirmed byte-identical to the real function when run with
admission logic disabled, before either was trusted for anything; the
fair variant was separately confirmed to genuinely diverge from the real
function on a mixed ETF+stock test set (2,286 vs. 2,130 trades),
confirming the admission change has real, measurable effect rather than
being a no-op.

**Full comparison, reset-at-boundary basis throughout (matching the
full-500 study's own convention — balance renormalized to £50,000 at
2020-01-01, training-period history/eligibility unaffected), full
503-stock + 33-ETF combined pool, test period 2020-01-01→2026-07-31:**

| | Baseline (guaranteed ETF priority — today's real mechanism) | Fair (combined momentum ranking) |
|---|---|---|
| Test trades | 724 | 1,170 |
| Win rate | 40.61% | 49.06% |
| Profit factor | 1.95 | 1.75 |
| Sharpe | 0.565 | 0.649 |
| CAGR | 9.56% | **19.69%** |
| Max drawdown (fixed-denominator) | −6.22% | −26.30% |
| Max drawdown (genuine peak-relative) | −4.91% | −8.44% |
| Test £50,000 → | £90,465.09 | **£161,436.21** |

**Not a one-sided win — a real, quantified risk/return trade-off, on
both drawdown conventions.** Fair competition more than doubles CAGR,
but genuine peak-relative drawdown also roughly doubles (−4.91% →
−8.44%) — the guaranteed ETF allocation under the current baseline
provides real diversification/stability that fair competition trades
away for higher, more stock-concentrated return. Neither figure should
be read in isolation.

**The emergent ETF/stock split — decisive, and the central finding of
this study.**

| | Baseline | Fair |
|---|---|---|
| ETF trades | 525 (72.5% of all trades) | **38 (3.2%)** |
| ETF share of total P&amp;L | 65.8% | **0.7%** |
| ETF share of deployed capacity | 76.3% | **2.6%** |
| Distinct ETFs winning ≥1 slot | 31 of 33 | **8 of 33** |

**ETFs' capacity share collapses from 76.3% to 2.6% once they have to
earn their place on momentum alone against ~500 real stock candidates.**
This is strong, direct evidence that the current guaranteed-priority
architecture — not genuine ETF momentum merit — drives most of the ETF
sleeve's present capacity share. Under fair competition, only 8 ETFs
ever won a slot: `EWZ`, `FXI`, `SKYY`, `SLV`, `SOXX`, `TLT`, `XLE`, `XLK`.

**The non-equity-exposure caveat — restated explicitly, not lost inside
this result.** Of the 10 ETFs with no stock substitute anywhere in this
system (`GLD`/`SLV`/`PDBC` — commodities; `TLT`/`IEF`/`TIP`/`MUB`/`LQD`/
`HYG`/`EMB` — seven separate bond/credit buckets), **only 2 (`SLV`,
`TLT`) won any capacity at all under fair competition; the other 8 won
zero.** This is expected, not a contradiction of the finding above: a
pure momentum ranking has no mechanism to value genuine crash-hedging or
non-equity correlation at all — that is a portfolio-construction
concept, not something a momentum score can see, by construction.
**"ETFs barely win capacity under merit-based competition" is not
evidence the ETF sleeve has no value** — it is evidence that pure
momentum ranking cannot see the specific value these 10 instruments
provide. This has to be weighed on its own, separate terms before any
real decision to trim the sleeve, not concluded away by this result.

**Flagged explicitly as the natural next step, not attempted here.**
Any future ETF-sleeve-value test must be re-run under this fair-
competition mechanism, not the current guaranteed-priority one, for the
exact reason stated above — a value test under guaranteed priority
cannot separate real merit from the confound. Separately, a genuine
**live-code recommendation** (research finding only, nothing built or
changed): `brokers/trading212.py`'s real decide/submit split should adopt
the identical principle — decide provisional admission using a combined
momentum ranking across both asset classes at EOD decision time, then
have each real submission job (07:00 UTC ETF, 13:30 UTC stock) confirm
its own provisionally-won slots still fit given genuinely live T212
account state at that later time, rather than the current architecture
where ETF submission consumes capacity first purely because it is
scheduled earlier in the day.

Full local test suite: 447 passing, confirmed before and after (this
study's new AST-transformed engine variant lives only in the now-deleted
disposable research clone — nothing was committed to the real
`analysis/backtest.py`). Nothing pushed; this write-up is the durable
record, alongside the preserved trade-level CSV exports (per the
trade-level-preservation standing requirement).

**The live-code recommendation above is now moot — superseded, not
implemented (2026-08-02).** See "ETF sleeve removed from live trading"
below: the ETF sleeve was removed from live trading entirely the same
day, using this study's 76.3%→2.6% capacity-share finding as the primary
evidence. With zero ETF tickers left as live candidates, there is no
ETF-vs-stock priority question left for `brokers/trading212.py` to
resolve — a combined-momentum-ranking submission-time reconciliation
would have nothing to reconcile. This recommendation is not being carried
forward as unfinished work; it is closed by the removal, not deferred
past it.

### ETF sleeve removed from live trading (2026-08-02)

**Decision made: drop ETFs from live trading entirely.** All 33 ETF
entries were removed from `config.yaml`'s `instruments:` section — not
disabled via `trading212_invest.etf_trading_enabled` (which already
existed as a flag-based off-switch, see "Clean, independent ETF/stock
trading switches" above), genuinely removed, so the ETF sleeve is no
longer a live candidate under any config state. `trading212_invest`'s
live-execution scope is now 40 US large-cap stocks only.

**Why genuine removal, not a flag.** This system already has an
established, explicitly-stated "flag, don't delete" convention (see the
many "kept, not removed" decisions throughout this file — `GOLD` kept as
a permanently-zero-eligible candidate, `SKIPPED_PRICE_MOVED`/
`REJECTED_CAPACITY` recording a decision rather than discarding it, the
GARCH-stop/HMM-gate precedent of removing *rejected research*, not live
config). ETFs are a deliberate exception to that convention, for the same
reasoning already applied earlier this session to removing SEC EDGAR
market-cap gating from the live path rather than merely disabling it: a
disabled-but-present ETF sleeve is a permanent, standing explainability
cost on a system that has spent this entire rebuild converging toward
"exactly one signal, one universe, one execution path" (see "V2
architecture" above) — a `false` flag sitting in config.yaml forces every
future reader of this file, every future audit, and every future
due-diligence conversation to re-explain *why* 33 ETF mappings still
exist and are still exercised by every backtest/validation code path even
though none of them can ever place a live order. Given the evidence below
was already decisive, carrying that explainability cost indefinitely — on
the promise of a future re-enable that may never happen — was judged not
worth it. If the ETF sleeve is ever reconsidered, re-adding the 33
`instruments:` entries (git history has the exact block verbatim, see
commit `56733d3` and earlier) is a config change, not a re-engineering
effort.

**The exact evidence this rests on** — from "ETF/stock fair-competition
study" immediately above, the same day: under today's real live
mechanism (ETFs guaranteed full priority admission before any stock is
even evaluated, purely because ETF submission is timed to LSE's 07:00 UTC
open and stock submission to NYSE's 13:30 UTC open — an artifact of
exchange-hours timing, never a designed merit judgment), ETFs captured
**76.3% of deployed capacity and 65.8% of total P&amp;L**, with 31 of 33
ETFs winning at least one slot. Once ranked on **momentum alone**,
combined fairly against ~500 real stock candidates with no asset-class
priority at all, ETF capacity share **collapsed to 2.6%** (P&amp;L share
0.7%), and only **8 of 33** ETFs ever won a single slot (`EWZ`, `FXI`,
`SKYY`, `SLV`, `SOXX`, `TLT`, `XLE`, `XLK`). This is direct, quantified
evidence that the ETF sleeve's entire current footprint in this system is
overwhelmingly a consequence of guaranteed scheduling priority, not
demonstrated momentum merit against the stock universe this system
already trades.

**What this decision explicitly does NOT claim — stated directly, not
implied away.** The fair-competition study itself was equally direct that
a pure momentum ranking has no mechanism to value genuine crash-hedging
or non-equity correlation at all — that is a portfolio-construction
question, not something a momentum score can see, by construction. Of the
10 ETFs with no stock substitute anywhere in this system (`GLD`/`SLV`/
`PDBC` — commodities; `TLT`/`IEF`/`TIP`/`MUB`/`LQD`/`HYG`/`EMB` — seven
bond/credit buckets), only 2 (`SLV`, `TLT`) won any capacity under fair
competition — the other 8 winning zero is expected, not damning, exactly
because momentum ranking structurally cannot see the drawdown-protection
value those instruments might genuinely provide in a crisis the momentum
signal itself doesn't anticipate. **That question — whether the
non-equity ETFs earn their place on a dimension other than momentum, i.e.
genuine portfolio-level drawdown protection — was still open and
untested at the moment this decision was made.** This is a deliberate
choice to act on decisive partial evidence (the capacity-share/P&amp;L
finding) given the live-trading deadline, not a claim that every question
about the ETF sleeve's value was answered first. The separate, already-
queued non-equity-ETF drawdown-protection test (cheap, could inform a
future reconsideration of this decision) remains queued and unrun — it
does **not** gate this decision, and this removal should not be read as
having pre-empted or answered it.

**Part B — what removing the sleeve made unnecessary, simplified
alongside the removal, not left running for nothing to do:**

- **The 07:00 UTC / LSE-open ETF order submission scheduler job
  (`etf_order_submission`, `data/scheduler.py:_etf_order_submission_job()`)
  was removed entirely**, not merely left registered against an empty
  queue. With no ETF entry left in `instruments:`, the EOD decide phase
  (`brokers/trading212.py:maybe_place_orders()`) can never queue an
  `is_etf=True` `pending_orders` row again — a submission job with
  structurally nothing to ever submit is dead weight, not a harmless
  no-op worth keeping registered and documented as a live schedule entry.
  `submit_pending_stock_orders()` (13:30 UTC / NYSE open) is completely
  unaffected and continues exactly as before.
- **`submit_pending_etf_orders()` itself was kept, not deleted, as a
  documented dead code path** — a one-line `is_etf=True` entry point into
  the shared `_submit_pending_orders()` machinery that
  `submit_pending_stock_orders()` also calls with `is_etf=False`. It is
  unreachable in production (no scheduler job calls it), but the existing
  test suite (`tests/test_execution.py`,
  `tests/test_comprehensive_replay_parity.py`) exercises
  `_submit_pending_orders()`'s generic capacity/price-sanity/quantity-
  resolution behaviour through this exact call shape; rewriting that
  coverage onto the stock-only entry point would have been a large,
  purely mechanical change for no behavioural benefit. Its docstring now
  states plainly that it is dead code, not disabled functionality.
- **The "LSE exchange-hours mismatch mitigation" design** — the whole
  reason a separate 07:00 UTC ETF submission job existed rather than one
  fixed 21:30 UTC submission time for every instrument (see "Exchange-
  aware order submission" above) — is now moot for the same reason: there
  is no LSE-listed UCITS wrapper left in the live candidate pool for its
  own trading session to matter.
- **General currency-conversion code was deliberately left untouched, not
  ripped out.** EUR/GBP/GBX handling in `get_instrument_price_gbp()` and
  `_to_gbp()` is currency-generic infrastructure, not ETF-specific
  complexity — it happened to only be exercised in practice by
  EUR/GBP/GBX-denominated ETFs (every one of the 40 remaining stocks is
  USD-denominated), but the code itself has no ETF-only branch and would
  correctly handle any future non-USD stock exactly as it does today.
  Removing genuinely reusable currency-conversion capability for zero
  behavioural benefit — every remaining instrument being USD is a fact
  about today's candidate pool, not a property this code depends on —
  would have been overreach, not simplification.
- **The fair-competition study's live-code recommendation is confirmed
  moot** (also stated inline in that section above): the recommended
  combined ETF/stock momentum-ranking-with-submission-time-reconciliation
  mechanism for `brokers/trading212.py` is not being built. With zero ETF
  candidates, there is no ETF-vs-stock priority question left to resolve.
- **Deliberately NOT touched**: `analysis/universe.py`'s `ETF_TICKERS`
  tuple and `ALL_TICKERS` (still 73 tickers, 33 ETF + 40 stock) remain
  exactly as they were — this is the research/backtest universe, a
  separate system from the live `instruments:` candidate pool by design
  (see "Unified instrument structure" above for why these were always two
  different things), and every historical CLAUDE.md finding that depends
  on the 73-ticker backtest universe (the phased-rebuild validation, the
  full-500 study, the fair-competition study itself) stays exactly
  reproducible. `is_asset_class_enabled()` (`analysis/decision.py`) also
  keeps its `is_etf` parameter and `etf_trading_enabled`/
  `stock_trading_enabled` config-key structure unchanged in code — only
  the config KEYS were removed from `config.yaml`, which is a pure no-op
  for live trading (an absent key still defaults to `True`, and there is
  simply no ticker left with `is_etf=True` in the live `instruments:` map
  for that default to ever matter).

**Verification, direct not inferred.** `config["instruments"]` now
contains exactly 40 tickers, zero with `is_etf: true`, with zero overlap
against `analysis.universe.ETF_TICKERS` — checked programmatically
against the real, current `config.yaml`, not assumed from the diff.
`brokers/trading212.py:maybe_place_orders()`'s decide-phase loop iterates
`config.get("instruments", {})` directly (not `analysis.universe.
ALL_TICKERS`), so this same check is a direct proof the live candidate
pool is now stock-only — there is no separate code path that could still
smuggle an ETF ticker into a live decision.

**Test suite and deploy.** Full local suite passing after the removal
(472 tests — one pre-existing test, `test_get_all_watchlist_entries_
combines_core_and_instruments`, asserted `"XLK" in tickers` and was
updated to assert `"AAPL"` instead, since it was directly checking the
now-removed ETF sleeve's presence; `tests/test_scheduler.py`'s ETF-
submission-job tests were replaced with a single test confirming
`etf_order_submission` is never registered and `_etf_order_submission_job`
no longer exists, rather than testing removed behaviour). Deployed the
same day — see the deploy log for the exact commit and timestamp.

### Market-cap infrastructure removed (2026-08-02)

`analysis/market_cap.py` and the `market_cap_history` table (see "Market-cap
data infrastructure — deployed, not wired to live trading" above) were
removed entirely, code and data both — not left as unused-but-present
infrastructure. Checked directly before removing anything, not assumed:
`grep -rln "market_cap|MarketCapHistory"` across every production `.py`
file found exactly two hits — the module itself and its own `data/
database.py` model definition — confirming `market_cap()` was never called
from any live decision-making path (`brokers/trading212.py`, the dynamic
candidate pool, the instrument-resolution logic) and nothing else in the
codebase imported from it. The full-500/top-100/top-150 universe-broadening
studies and the fair-competition study that used SEC EDGAR market-cap data
all worked from their own extracted copies inside disposable research
clones — never production's real file or table — so removing the
production copy costs nothing for reproducing any of those findings; their
own write-ups already state the exact formula/data needed per this file's
"Research reproducibility standard".

Verified on the production server directly (not assumed): `market_cap_history`
existed with 25,573 rows (matching the original backfill's own count). DB
backed up first (`investment_research_pre_marketcap_removal_<timestamp>.db`),
then the table dropped via a direct `DROP TABLE IF EXISTS`. `tests/
test_market_cap.py` (88 tests) removed alongside the module; `tests/
conftest.py`'s `_GET_ENGINE_BINDINGS` had its `analysis.market_cap.get_engine`
entry removed (the module no longer exists to patch). Full suite: 415
passing after removal (down from 503, matching the 88 removed tests).

### ETF pricing-path audit and manual-seed mechanism removed (2026-08-02)

Direct follow-up to "ETF sleeve removed from live trading" above — that
task's own UI change initially *relabelled* `audit_etf_pricing_paths()`'s
Status-page expander as "currently inert" rather than removing it, on the
same "flag, don't delete" reasoning this file applies to genuinely
reversible states elsewhere. That reasoning doesn't hold here: the T212
demo account was reset the same day specifically for a clean start, the
system has never placed a real live order, and there is no historical ETF
trade data this mechanism's presence would be preserving. A "0 ETF
positions, currently inert" display reads as a dormant capability that
could be switched back on, not as a decision that's been made and closed
— the same standard already applied to the EDGAR/market-cap removal above
and the `instruments:` config entries themselves.

Removed entirely, not relabelled: `audit_etf_pricing_paths()`,
`seed_instrument_price_gbp()`, and `get_cached_price_entry()`
(`brokers/trading212.py`), plus the Status page's whole "ETF pricing-path
audit" + "Seed a manual price" UI block (`main.py`). Checked each
function's real callers before removing (not assumed dead): `get_cached_
price_entry()` was called only by the audit function; `seed_instrument_
price_gbp()` and `audit_etf_pricing_paths()` were called only by the one
UI block being removed. `_get_cached_price_gbp()` (the private, bare-float
cache read `get_instrument_price_gbp()`'s own tier-2 path uses) and
`_cache_last_known_price_gbp()` were kept — these are genuinely generic,
still-live infrastructure (a stock currently unheld but priced before via
a real fill still uses this cache tier), unlike the audit/seed mechanism,
which existed specifically because an ETF/UCITS wrapper has no automated
fallback price path at all — a problem that only ever applied to ETFs,
and ETFs no longer exist as live candidates.

**A real bug found in passing, while checking the removed block's call
site.** The "Instrument mapping" expander directly above the ETF audit
block had already been rewired (same day, earlier in this session) to
read from the dynamic `instrument_resolutions` table instead of the old
static `config["instruments"]` dict — but the ETF audit/seed block below
it still referenced the old `mapping_full` local variable, which no
longer existed anywhere in scope. This block was already silently broken
(a `NameError` waiting to fire the next time anyone opened that expander)
before this task started; removing it fixes that bug as a side effect
rather than needing a separate patch.

A second, related stale reference was found and fixed the same way:
`_submit_pending_orders()`'s locked-out-ETF warning log message (`brokers/
trading212.py`) pointed operators at "Status page → T212 Invest Account →
ETF pricing-path audit → Seed a manual price" — a UI path that no longer
exists. This branch is itself structurally unreachable in production now
(no `is_etf=True` row can ever be queued — see "ETF sleeve removed from
live trading" above), kept only because `submit_pending_etf_orders()`
itself is deliberately kept as documented dead code for the shared
capacity/price-sanity logic's test coverage. The message was reworded to
state plainly this should never occur in production, rather than pointing
to a removed recovery path.

9 tests removed alongside the functions (`tests/test_execution.py`:
5 `seed_instrument_price_gbp` tests, 2 cache-tier interaction tests,
2 `audit_etf_pricing_paths` tests); the one remaining test that checked
the locked-out-ETF warning's exact wording
(`test_locked_out_etf_with_positive_signal_is_a_clean_logged_skip_not_a_silent_failure`)
was updated to match the reworded message rather than removed, since the
underlying exception-isolation behaviour it verifies is still real and
still worth covering. Full suite: 406 passing.

### config.yaml restructure — flattened reference tickers, removed `instruments:`, and a real EOD price-pull bug found along the way (2026-08-02)

Four related cleanups to config.yaml, done together since they all touch
the same file, plus one genuine production bug found and fixed while
verifying the second cleanup was safe.

**`instruments: {}` removed entirely.** Verified safe first, not assumed:
(1) grepped for direct `config["instruments"]` indexing (which would raise
`KeyError` on a fully-removed key) across every production file — none
found; `brokers/trading212.py`, `data/ingestion.py`, and `main.py` all used
`.get("instruments", {})`, which returns the identical `{}` whether the
key is present-and-empty or absent entirely, so removing it is a pure
no-op for all three; (2) `tests/test_config.py` DID use direct indexing in
five places against the real config.yaml (it would have broken) — those
tests were rewritten rather than left to fail, since the underlying thing
they validated (a hand-curated instrument mapping's structural integrity)
no longer exists to validate at all now that the mapping is fully dynamic.

**`watchlist: {core: [...]}` flattened to a top-level `reference_tickers:`
list.** `watchlist:` had exactly one child (`core`) left once `instruments:`
moved out to its own top-level key on 2026-07-31 — the wrapper added
nothing. Every reader updated: `data/ingestion.py:get_all_watchlist_entries()`
now reads `config.get("reference_tickers", [])` directly instead of
iterating `config.get("watchlist", {}).values()`; `main.py`'s Charts
landing page grouping and `_build_watchlist()` updated to match.

**The real bug: `data/scheduler.py:_build_ticker_exchange_map()` was
never actually merged with the dynamic candidate pool at all.** Found
while updating this function for the `watchlist:` flattening (Part B's own
"update every reader" requirement) — it had always hand-rolled its own
merge over `config.get("watchlist", {})` alone (predating even the
`instruments:` split, since `instruments:` was never nested under
`watchlist:` to begin with), rather than reusing `data/ingestion.py:
get_all_watchlist_entries()`, the one function that correctly includes the
dynamic live candidate pool. The practical effect, traced all the way
through: `_eod_job()` builds its actual price-pull ticker list
(`open_tickers`) directly from this map's own keys, then calls
`pull_incremental(tickers=open_tickers)` — an *explicit* ticker list that
bypasses `pull_incremental()`'s own default (`get_all_tickers(config)`,
which correctly includes the dynamic pool) entirely. This means the EOD
job's real price pull has been silently limited to the 3 reference tickers
(`^GSPC`, `GBPUSD=X`, `EURUSD=X`) — **never actually refreshing price data
for any of the dynamic S&amp;P 500 stock candidates** `get_live_stock_
candidates()` returns, since the day the dynamic candidate pool went live
(see "wire the momentum-only, full-S&amp;P-500 selection into live
execution" above). No test existed for `_build_ticker_exchange_map()` or
an EOD price-pull scenario at all, which is why this went uncaught.

Fixed by having `_build_ticker_exchange_map()` reuse `get_all_watchlist_entries()`
directly — every entry it returns already carries a real `exchange` field
(reference tickers explicitly, dynamic candidates as `"NYSE"` per
`data/ingestion.py`'s own default), so the fix is a direct call, not a
re-derivation. Two new regression tests added
(`tests/test_scheduler.py::test_build_ticker_exchange_map_includes_dynamic_candidates`,
`..._defaults_missing_exchange_to_nyse`). This bug predates today's
session — it was never introduced by the dynamic-candidate-pool wiring
itself, only exposed by auditing every reader while flattening
`watchlist:` — but its practical consequence (stale price data for the
entire live stock universe) is corrected as of this fix, deployed the same
day as the rest of this section.

**Comments trimmed to functional-only.** Every remaining comment in
config.yaml previously did two jobs — explain what a key does now, and
narrate the full incident history of how it got that way (dates, bug
names, section-by-section CLAUDE.md cross-references). The second job is
already this file's own job; duplicating it in config.yaml just made the
file harder to scan for no benefit. Every comment trimmed to a brief,
current-state explanation of what the key does and why it's set the way
it is, with CLAUDE.md pointed to only where a comment would otherwise be
meaningless without the context (e.g. the "V2 architecture" pointer on
`trading212_invest:`'s own header comment).

**`max_holding_days: 90` (under `trading212_invest:`) reworded to state
plainly it's research-only.** With zero ETFs in the live candidate pool,
this value is read only by the frozen research/backtest universe's ETF
sleeve (`analysis/universe.py:ETF_TICKERS`, via `get_max_holding_days()`)
— never for anything live. Left in place under `trading212_invest:` rather
than moved to `backtest:` (the backtest engine already reads this exact
key, under this exact section, by design — see "Live/backtest decision-
logic parity" above for why holding-limit logic is deliberately shared
between live and backtest through one function rather than two config
locations), but the comment now says directly that it's the frozen
research universe's value, not a live parameter, so it doesn't read as an
active live setting to a future reader.

**Part E (`stock_` prefix drop) — skipped, not attempted.** `stock_trading_
enabled`/`stock_submission_enabled`/`stock_max_holding_days` have no ETF
sibling left to disambiguate from, so dropping the prefix is purely
cosmetic. Checked the blast radius before deciding: `grep -rln` for these
three keys found 5 production files (`analysis/decision.py`, `analysis/
universe.py`, `analysis/validation.py`, `brokers/trading212.py`, plus
config.yaml itself) and 7 test files reading them. Renaming touches every
one of those call sites for zero functional benefit, while three other
substantive changes were already landing in the same session — judged not
worth the risk/effort ratio right now. Left as a documented, deliberate
skip, not an oversight; revisit if this section is touched again for an
unrelated reason.

Full suite: 404 passing after all four parts plus the bug fix.

### Live position reconciliation (2026-08-02)

**Why this needed real investigation, not just a review.** Held-position
lifecycle handling (`brokers/trading212.py:maybe_exit_positions()`) was
originally scoped when the live candidate pool was a fixed ~40 hand-curated
tickers, where a held ticker becoming unavailable mid-position was rare
enough to not be a real operational concern. That's no longer true: the
live pool is now the full, dynamically-tracked S&P 500 (~500 tickers, see
"wire the momentum-only, full-S&P-500 selection into live execution"
above), where delistings, acquisitions, and other corporate actions on a
held position will happen with real, ongoing regularity. Three scenarios
were checked directly against the actual code, not assumed already
covered — two real gaps and one real bug were found.

**Scenario 1 — cash-out acquisition (broker converts the position to cash
on its own): confirmed gap, fixed.** `maybe_exit_positions()`'s loop only
ever iterates `client.get_positions()` — T212's real, current holdings —
so a position T212 has already silently closed on its own simply never
appears in that loop at all; nothing notices, nothing reconciles.
Separately, `_reconstruct_pnl()` can only ever produce a `pnl` row by
FIFO-pairing a FILLED BUY with a FILLED SELL **this system placed** — a
broker-side closure with no SELL order from us can never produce a P&L
record no matter how long the system keeps running. The position would
simply vanish from tracking with zero trace.

**Scenario 2 — stock-swap acquisition (position converts into a different
instrument entirely): confirmed gap, fixed.** `maybe_exit_positions()`'s
`reverse_map` only recognizes instruments with a VERIFIED
`instrument_resolutions` row for some *already-tracked* research ticker.
The swap result is, by definition, a T212 instrument this system has never
resolved — it hits `if not ticker: continue  # not a managed instrument —
leave it alone` and is silently ignored forever. The original ticker's
position also disappears from `positions_by_t212` (T212's own books
correctly show it gone), so nothing on either side of the swap was ever
flagged.

**Scenario 3 — genuine bankruptcy/delisting (no real market to sell
into): confirmed TWO real bugs, both fixed.**
1. `if status == "FAILED": return` — a synchronous `place_market_order()`
   failure for ONE position halted the **entire** exit phase for that run,
   silently leaving every OTHER open position unchecked for
   crisis/stop-loss/signal-off/max-holding exits too. This directly
   contradicted the very try/except it sat inside, whose own comment
   states "one position's unhandled exception must never silently stop
   every other open position from being checked for exit" — a FAILED
   order status is exactly the single-position problem that principle was
   meant to cover, and a delisted/bankrupt ticker is precisely the
   scenario most likely to trigger it. Fixed: `continue` instead of
   `return`, matching the per-position isolation already established
   elsewhere in the same loop (see "Live pricing bug, exception isolation"
   above for the original precedent this fix restores).
2. `_has_recent_sell_order()`'s duplicate-guard cooldown covered
   `PLACED` (unconditionally) and `FILLED`/`FAILED` (within 24h) — but
   **not `REJECTED`**. A sell T212 initially *accepts* (status `PLACED`,
   gets an `order_id`) and only later resolves to `REJECTED` via
   `poll_placed_orders()` fell through both checks entirely: not
   unconditionally blocked (no longer `PLACED`), not cooldown-blocked
   (`REJECTED` wasn't in the covered-status set) — meaning nothing stopped
   an immediate retry on the very next exit check, as often as hourly,
   indefinitely, for a position that genuinely cannot be sold. Fixed:
   added `REJECTED` to the cooldown-eligible status set.

   On top of the cooldown fix, a genuinely delisted/bankrupt position
   still shouldn't retry forever even once slowed to hourly — nothing
   about "no real market" resolves on its own. `_count_recent_sell_
   failures(ticker)` counts consecutive FAILED/REJECTED SELL attempts
   since the ticker's last FILLED BUY; once it reaches
   `_SELL_REJECTION_GIVE_UP_THRESHOLD` (3), `maybe_exit_positions()` stops
   attempting a fresh sell for that position entirely and records a
   `REPEATED_SELL_REJECTION` reconciliation event instead — recognizing
   the position as likely worthless/unsellable and flagging it for manual
   review/write-off, rather than mechanically retrying against an
   unchanging condition.

**The mechanism — `reconcile_positions_with_broker()` and the new
`position_reconciliations` table.** Compares what this system's own order
history implies is open (`_locally_believed_open_positions()`: the latest
FILLED BUY per `t212_ticker` with no later FILLED SELL) against T212's
real, live positions — flagging, never guessing, anything that doesn't
reconcile:
- **`BROKER_CLOSED_NO_SELL`** — we believe it's open, T212 doesn't show it
  held, we never placed a SELL. Exact proceeds are genuinely **not known**
  from any T212 endpoint this codebase calls (no cash-transaction/
  corporate-action history endpoint is wired up) — `estimated_value_gbp`
  is left null rather than fabricated; the note states plainly that manual
  verification against T212's own transaction history is needed.
- **`UNRECOGNIZED_POSITION`** — T212 shows an instrument held with no
  VERIFIED mapping at all.
- **`REPEATED_SELL_REJECTION`** — see Scenario 3 above.

Each event type upserts on `(event_type, t212_ticker)` — a recurring
discrepancy refreshes its own `note`/`last_seen_at` rather than
accumulating duplicate rows, and deliberately never resets `status` back
to `OPEN` once a human has set it to `ACKNOWLEDGED` (nothing in code
transitions it automatically either way — that's a manual action, matching
`InstrumentResolution`'s own FAILED-never-auto-retried contract). An alert
email (`output/briefing.py:send_position_reconciliation_alert()`, same
enabled/cooldown-gated shape as `send_instrument_resolution_failure_alert()`
and `send_membership_staleness_alert()`, its own dedicated cooldown key)
fires whenever a `BROKER_CLOSED_NO_SELL` or `UNRECOGNIZED_POSITION` event
is found — `REPEATED_SELL_REJECTION` doesn't separately email, since it's
already loud in the logs and doesn't need the same urgency (the position
was already known to be exit-blocked before this event fires).

**Instrument-resolution invalidation — real gap, confirmed and closed.**
`resolve_and_cache_instrument()` trusts a VERIFIED row forever once
cached, by design (see its own docstring) — it never re-checks. Ticker
symbols get reused after a delisting/bankruptcy; if a brand-new, unrelated
company later takes the same symbol and becomes index-eligible again,
`resolve_and_cache_instrument()` would return the OLD company's cached
T212 instrument ID and currency without ever re-verifying, since VERIFIED
short-circuits any fresh lookup — the exact same "reuse a cached mapping
without confirming it still means what it used to" class of risk already
identified for historical market-cap data (see "Present-day market-cap
ranking bias" and the SEC EDGAR sections above), one layer closer to live
trading. `invalidate_stale_instrument_resolutions(live_candidate_tickers,
held_t212_tickers)` deletes a VERIFIED row once its ticker is BOTH no
longer a live candidate (genuinely removed from the index, not merely
absent from a stale fetch) AND has no currently-open position — the second
condition is essential, not optional: `_load_all_verified_instrument_
resolutions()` deliberately reads every VERIFIED row regardless of today's
candidate pool specifically so a formerly-eligible, now-removed ticker
still matches correctly for its own exit check (that removal is exactly
what triggers `INDEX_REMOVAL` in the first place) — invalidating a
still-held position's mapping would break its very next exit check.
Called from `reconcile_positions_with_broker()`, reusing the same live
`get_positions()` read the other two checks already need rather than a
second T212 API call.

**`BROKER_CLOSED_NO_SELL` refined to check for a real T212 order_id
directly (2026-08-02), not just infer from local order-history structure.**
The original version above flagged `BROKER_CLOSED_NO_SELL` purely from
"no FILLED SELL exists locally" + "T212 doesn't show it held" — it never
checked whether an actual T212 order_id existed for a SELL attempt that
just hadn't resolved to FILLED locally yet. This was a real precision gap,
not a hypothetical: `poll_placed_orders()` runs earlier in the same hourly
`_trade_alert_job()` cycle, but this account has a *documented* lag on
order-status resolution (see "Broker integration" above — "T212's demo
account has been observed leaving a market SELL sitting in PLACED for 60+
hours before poll_placed_orders() ever sees it resolve"). If `get_positions()`
(a real-time read) already shows a position gone because our own SELL
genuinely filled on T212's side, but the async order-status endpoint
hasn't caught up yet, the old check would misfire — flagging a routine,
already-in-progress exit as a mystery broker-side event.

`_most_recent_sell_order_since(t212_ticker, since)` looks up the most
recent SELL order **of any status** (not just FILLED) for the position
since its BUY, giving `reconcile_positions_with_broker()` a genuine
three-way read instead of a binary one:
- **A `PLACED` order_id exists** — the position's absence is consistent
  with that order having filled; this is `poll_placed_orders()` lag, not
  a discrepancy. Skipped entirely — no event recorded, no alert.
- **A `FAILED`/`REJECTED`/`CANCELLED` order_id exists** — still flagged
  (this genuinely wasn't resolved successfully in our own records), but
  the note now names the real order_id and its recorded status
  explicitly, and states the correct next step plainly: verify directly
  whether that specific order actually filled on T212's side despite the
  recorded status, before assuming an unrelated broker-side event.
- **No SELL order of any status exists** — the original, clean case:
  genuinely no order_id anywhere to explain the closure. Flagged with the
  same cash-out-acquisition note as before.

This is the direct order-ID check itself — matching or absence of a real
T212 order_id — not a stylistic rewording of the same inference. Covered
by 2 new tests: the PLACED-order skip case (confirms zero event recorded,
zero alert sent) and the REJECTED-order case (confirms the event still
fires but the note names the specific order_id and status). Full suite:
431 passing.

**How this runs going forward — not a one-time fix.**
`reconcile_positions_with_broker()` is wired into the hourly
`_trade_alert_job()` (`data/scheduler.py`) alongside `maybe_exit_positions()`
and `poll_placed_orders()`, in its own `try/except` (a reconciliation
failure must never block the exit check or snapshot refresh that share
its slot). Same cadence as the exit check, for the same reason: this is
safety-critical account-state visibility, not something that should wait
for the next full (07:00/13:30/21:30) run.

Covered by 24 new tests in `tests/test_execution.py` (the FAILED-halt
regression via a two-position exit run where one fails and the other must
still be attempted; `REJECTED`-cooldown coverage — including a same-
calendar-date string-comparison pitfall this project has already been
burned by once, documented under "T212 P&L reconstruction" below, avoided
here by using multi-day rather than just-past-24h offsets in the test data
itself; the give-up threshold both firing and not-yet-firing; all three
`reconcile_positions_with_broker()` discrepancy types plus the
no-discrepancy control case; both invalidation directions — deleted when
unheld, preserved when still held; and the upsert's ACKNOWLEDGED-status
preservation), 9 new tests in `tests/test_briefing.py` (mirroring the
existing instrument-resolution-failure alert's own test shape exactly),
and 3 new tests in `tests/test_scheduler.py` (wiring, and that a
reconciliation failure doesn't block the exit check or snapshot refresh
sharing its hourly slot). Full suite: 429 passing.

### Let momentum, not index removal, govern already-open positions (2026-08-02)

`INDEX_REMOVAL` sat second in `evaluate_exit_reason()`'s exit-priority
chain, above `STOP_LOSS`/`SIGNAL_OFF` — meaning a genuinely winning
position was force-closed the moment its ticker lost S&P 500 membership,
for a reason unrelated to its own trading behaviour. Inconsistent with
this system's own "no take-profit, no position-size cap, let winners run"
philosophy elsewhere. **Scope, precisely: this only ever concerns "removed
from the index but still a real, actively-traded, listed company."**
Genuine delisting/acquisition/bankruptcy is a different situation, already
handled by `reconcile_positions_with_broker()` (see "Live position
reconciliation" above) regardless of index status — untouched by this
change.

**Part A — a real, confirmed dependency, fixed first.** `data/ingestion.py:
get_all_watchlist_entries()` (the source of the daily price-pull ticker
list) merged `reference_tickers` with `analysis.universe.
get_live_stock_candidates()` only — the membership-gated live candidate
pool. A ticker that lost membership would drop out of that pool and
therefore out of the price-pull list entirely, silently starving
`pull_incremental()` of updates for it — making momentum impossible to
compute for an already-open position the moment it lost membership. This
would have made the rest of this change unimplementable. Fixed: a new
`brokers/trading212.py:get_held_research_tickers()` (a thin public wrapper
around the existing `_locally_believed_open_positions()` query — reused,
not duplicated) is now also merged into `get_all_watchlist_entries()`,
regardless of today's membership status. A deferred (function-body, not
module-level) import is used on both sides to avoid a circular import
between `data/ingestion.py` and `brokers/trading212.py` (which already
imports `get_price_df` from `data.ingestion` at module level).

**Part A2 — checked against real historical data before assuming
rarity.** Reconstructed the real S&P 500 membership history directly from
the same source `analysis/universe.py` uses (`fja05680/sp500`, full
1996–2026 CSV) and found every genuine removal event between 2007-01-01
and 2026-06-30 (457 events, ~448 distinct tickers). For each, re-ran the
actual production backtest engine (`analysis.backtest.simulate_trades()`,
`analysis.decision.evaluate_exit_reason()` unmodified at the time of the
check) against real Yahoo Finance price history to see whether the
strategy would genuinely have had an open position at the moment of
removal, and what momentum showed. 285 of 448 tickers (64%) had no usable
price history at all (long-delisted symbols Yahoo no longer serves) — of
the 163 with usable data, 34 produced a real `INDEX_REMOVAL` exit (33
within the 2007–2026 window). **Result: 30 of 33 (91%) had genuinely
positive momentum at the moment of forced closure — 20 of those were
actively *rising*, not just marginally positive (momentum up to +347%,
median +24%).** Only 3 were already negative/off, the case where
`SIGNAL_OFF` would have already produced the same outcome regardless.
This refuted the working hypothesis that most removals follow sustained
weakness — the opposite was true. Recognizable examples of a healthy,
rising position force-closed purely by `INDEX_REMOVAL`: BMC Software
(+347%), T-Mobile US (+67%), CIEN (+69%), Andeavor (+49%), Molex (+46%).
Many of these are acquisition-driven removals (a company bought at a
premium pops in price, then leaves the index as the deal closes) — a
common, legitimate removal pathway, not an anomaly in the sample.

**Part B — the decision-logic change.** `analysis/decision.py:
evaluate_exit_reason()` no longer accepts an `is_member` parameter at
all, and no longer checks membership anywhere in its priority chain —
which is now CRISIS_EXIT → STOP_LOSS → SIGNAL_OFF → MAX_HOLDING only.
`passes_entry_gate()` is completely unchanged — membership still gates
every new entry, including re-entry into a ticker whose prior position
has since fully exited. All 5 call sites (`brokers/trading212.py:
maybe_exit_positions()`; `analysis/backtest.py`'s four simulation engines
— `simulate_trades()`, `simulate_portfolio()`, `simulate_portfolio_
compounding()`, `simulate_portfolio_compounding_mtm()`; `output/
performance.py:get_brief_cards()`'s display path) had their `is_member=`
keyword argument removed from the `evaluate_exit_reason()` call — in three
of `analysis/backtest.py`'s Phase-A (exit) loops, `is_member` was computed
*solely* for that call and is now removed entirely rather than left as a
dead, unused local (Phase B's own entry-gate computation of `is_member`,
a separate call, is unaffected). `output/performance.py`'s status/
status_priority display dicts had their now-unreachable `"INDEX_REMOVAL"`/
`"INDEX REMOVAL"` entries removed for the same reason — the card's
`current_is_member` field itself is kept, purely informational.
`main.py`'s `_STATUS_COLOURS`/`_STATUS_ICONS` maps were deliberately left
untouched (harmless dead entries in an untested file — not worth the
risk of touching more of `main.py` for a cosmetic no-op).

**Confirmed unaffected, not assumed:** `passes_entry_gate()` still
requires `is_member is True` for any new entry, verified both by reading
the function (unchanged) and by a new test
(`test_non_member_ticker_still_cannot_be_newly_entered`).
`reconcile_positions_with_broker()` (genuine delisting/acquisition
handling) has zero reference to `is_member`/`evaluate_exit_reason`/
`analysis.universe` anywhere in its body — confirmed via direct grep — and
every one of its existing tests (`tests/test_execution.py`'s
`test_reconcile_*`) passed unchanged, with no modification needed.

**Test coverage.** `tests/test_decision.py`: `evaluate_exit_reason()`
raises `TypeError` on a stray `is_member=` kwarg (a structural guard
against the parameter quietly reappearing with a no-op default); a
position with positive momentum, price above stop, and holding days under
the limit produces no exit at all. `tests/test_backtest.py`: a stock
whose membership ends mid-holding, with momentum staying positive, no
longer force-exits — it rides to its normal `MAX_HOLDING` backstop
instead; a non-member ticker still cannot be newly entered.
`tests/test_execution.py`: `maybe_exit_positions()` end-to-end confirms
`is_member=False` alone (nothing else firing) places no SELL order at
all, and that `STOP_LOSS`/`SIGNAL_OFF` still fire normally regardless of
membership; 5 new tests for `get_held_research_tickers()` and the
`get_all_watchlist_entries()` merge (held-but-non-candidate ticker
included, no duplication when also a live candidate, empty/closed-position
cases). `tests/test_live_backtest_parity.py`: replaces the old
INDEX_REMOVAL parity scenario with one confirming live order placement
and the backtest engine still agree that a membership-losing position
rides to `MAX_HOLDING`. `tests/test_performance.py`: `get_brief_cards()`
shows `"ON TRACK"` (not `"INDEX REMOVAL"`, which no longer exists as a
possible status) for a non-member ticker with nothing else wrong, while
`current_is_member` still reports the true membership state; `STOP_LOSS`
still wins over `SIGNAL_OFF` regardless of membership.

No database write of any kind was needed for this change (pure
decision-logic + price-pull-list code) — no `.backup` was required.
Full local suite: **513 passing**, 0 failed.

### Market-calendar-aware scheduling (2026-08-02)

`scheduled_eod` and `membership_refresh` moved from a Mon-Fri `CronTrigger`
day-of-week filter to firing **every single day**, with a genuine
NYSE-calendar check as the first thing each job does once it fires. A
plain weekday filter already skipped weekends correctly, but had zero
concept of a market holiday (Thanksgiving, Christmas, ...) — it would
still fire the full order-decide pipeline on any weekday regardless of
whether NYSE was actually open that day.

**Confirmed this was a real, live bug, not a theoretical one.** Before
this fix, `_eod_job()`'s per-ticker exchange check only decided *which
tickers* to pull that day — `_ALWAYS_PULL`/`_ALWAYS_OPEN_EXCHANGES`
(FX/crypto reference tickers) guaranteed `open_tickers` was never empty
even on a day NYSE itself was closed, so the job always fell through to
`_run_analysis_pipeline(..., RUN_FULL)` regardless — meaning on a weekday
market holiday, this system was placing real order-decide logic against
stale price data (no genuine NYSE close had happened that day) every
single time one occurred.

**The gate — `_is_scheduled_trading_day(check_date)` (`data/scheduler.py`)**
reuses the existing `_is_exchange_open("NYSE", check_date)` helper
(itself already backed by `pandas_market_calendars`, used elsewhere in
this module for the EOD job's own per-exchange ticker-pull decisions) —
the same "reuse a maintained, purpose-built source instead of a
hand-rolled list that can go stale" lesson already applied to S&P 500
membership tracking itself (`analysis/universe.py`, the `fja05680/sp500`
dataset) rather than a manually-maintained holiday calendar. NYSE is the
only calendar this system's live decisions genuinely depend on now — the
entire ETF sleeve was removed from live trading 2026-08-02 (see "ETF
sleeve removed from live trading" above), so every live candidate is a
US-listed equity.

**Deliberately structured for a future extended-hours session, not just
today's requirement.** The scheduler registration itself must fire daily
with no day-of-week filter at all — this is the load-bearing part of the
design, not an implementation detail. If the trigger stayed Mon-Fri, the
job would never wake up on a Sunday to check anything, which would
structurally exclude a future extended-hours scenario (some platforms
already offer Sunday-evening pre-market data) before the job ever got the
chance to decide differently for it. `_is_scheduled_trading_day()` is kept
as its own named function (rather than inlining `_is_exchange_open("NYSE",
...)` at each call site) specifically so a future session-aware version
of this check is a change to one function, not a change to the scheduler
registration or either job's control flow. Not built now — only the
standard NYSE regular session is checked today — but the structure doesn't
foreclose it.

**Both jobs use the identical gate, verified directly** (not just
asserted): a dedicated test (`test_eod_and_membership_refresh_use_the_
identical_gate`) inspects both functions' source and confirms both call
`_is_scheduled_trading_day(today_utc)` — the same "one shared function,
not two independently hand-written copies" discipline this project has
already been burned by more than once (see `analysis/decision.py`'s own
docstring for the original four instances).

**A third, directly-related bug found and fixed as part of this same
change, not left for later.** `_check_missed_eod_run()` (the startup-time
catch-up mechanism, called once when the scheduler process starts) calls
`_run_analysis_pipeline()` directly rather than going through `_eod_job()`
— so it did **not** automatically inherit the new gate, and still had its
own, separate, hardcoded `now_utc.weekday() >= 5` guard with the identical
"no holiday awareness" gap. Worse: this change *increases* the chance of
this actually firing wrong — under the old always-run EOD behaviour,
`last_full_refresh` rarely went stale on a weekday holiday (the job ran
regardless); now that `_eod_job()` correctly no-ops on a holiday,
`last_full_refresh` legitimately stays stale from the last real trading
day, which is exactly the condition `_check_missed_eod_run()` reacts to.
A service restart during its 21:45-23:59 UTC catch-up window on a genuine
market holiday would have read that expected staleness as a "missed run"
and fired a real catch-up — including real order placement — against
stale data on a day the market is actually closed. Fixed by replacing the
standalone weekday check with the same `_is_scheduled_trading_day()` call
`_eod_job()`/`_membership_refresh_job()` use — one shared gate across all
three call sites now, not three independently-maintained ones.

**A related test-hygiene finding, worth recording since it's the exact
failure shape to watch for again.** Six pre-existing `_membership_refresh_
job()` tests never froze "today" at all — they implicitly depended on
whichever real calendar day the suite happened to run on. Adding the
calendar gate exposed this immediately: 3 hard-failed the moment the
suite ran on a real Sunday, but 3 more were worse — they kept *passing*,
only because their own assertions (`assert_not_called()`, "must not
raise") were trivially satisfied once the gate silently short-circuited
the job before ever reaching the logic they claimed to test. All six
fixed by freezing `scheduler.datetime` to an explicitly chosen, verified
real trading day (2026-08-04) rather than leaving them to depend on the
day the suite happens to execute on. Checked programmatically afterward
across every test in the file that calls `_eod_job()`/`_membership_
refresh_job()`/`_check_missed_eod_run()` (17 total) to confirm every one
now freezes the date — zero remaining exposure — and the full suite was
re-run successfully on a genuine real Sunday as the strongest possible
confirmation.

Covered by 17 tests in `tests/test_scheduler.py`: `_is_scheduled_trading_
day()` directly against real, independently-verified dates (an ordinary
Tuesday, Thanksgiving, a Saturday — no mocking of `pandas_market_calendars`
itself, exercising the genuine library); both jobs' registration confirmed
to carry no `day_of_week` filter (`field(...) == "*"`); the registered
`scheduled_eod` trigger's own `get_next_fire_time()` computed across a
real Friday-to-Saturday boundary to prove it genuinely would fire on a
weekend (not just that the raw cron field looks unfiltered); both jobs'
no-op behaviour on a real holiday and a real weekend, and normal operation
on an ordinary weekday, all proven at the decision layer (real downstream
functions asserted called/not-called, not just that the internal logic
*would* handle it correctly if reached); and the `_check_missed_eod_run()`
holiday-non-firing regression. Full suite: 443 passing.

Deployed 2026-08-02 (commit range to follow in the deploy log); production
DB backed up first per this session's standing discipline, even though
this change touches scheduling logic only, no data.

**Same-day follow-up fix — candidate selection was gated on the wrong
day.** The single combined `_is_scheduled_trading_day(today_utc)` gate
above correctly skipped the price pull on a non-trading day, but it also
skipped candidate selection/decision-queuing (the `RUN_FULL` decide phase)
on that same day — meaning a Sunday night (or any holiday night) queued
nothing at all for the next real session. Concretely: the account had
just been reset and `pending_orders` was empty, so a Sunday-night no-op
would have left Monday — the first day the live system could actually
trade — with zero orders submitted; the first real trade wouldn't have
happened until Tuesday.

**Fix — two independently-gated steps inside `_eod_job()`, not one
combined gate.** Price pull stays gated on whether *today* is a trading
day (correct — no new close exists on a non-trading day). Candidate
selection is now gated on `_has_upcoming_trading_day(today_utc)` — a
bounded (10-day) forward scan for the next real NYSE session, not a
literal "is tomorrow a trading day" check. That distinction matters: every
Friday's literal tomorrow is Saturday, so a naive single-day-ahead check
would have broken the single most common case (queuing Friday night for
Monday), not just the holiday-shifted edge case — a real forward scan is
what correctly and uniformly handles an ordinary weekend, a single-day
holiday, and a holiday-adjacent long weekend (e.g. Friday before a Monday
Labor Day, where the real next session is Tuesday) with no special-casing,
exactly because none of NYSE's real closures ever run longer than a few
consecutive days. The momentum signal itself needs no new same-day data to
produce a valid decision — `_ticker_state()` reads whatever the latest
*stored* close already is, so a Sunday night correctly computes off
Friday's close, exactly as an ordinary weeknight already computes off that
same evening's own just-pulled close.

Covered by 8 new/updated tests in `tests/test_scheduler.py`: direct
`_has_upcoming_trading_day()` checks against three real, independently-
verified dates (a Sunday finding Monday; a Friday before a Labor-Day-
shifted long weekend finding the real next session 4 calendar days out —
the decisive proof against a naive tomorrow-only check; Thanksgiving
finding the next day); `_eod_job()` proven at the decision layer for a
Sunday night (price pull NOT called, decide phase called with `RUN_FULL`
— the specific, urgent bug this closes), an ordinary weeknight (unchanged,
both price pull and `RUN_FULL` fire), the Friday-before-holiday case
(today is itself a trading day, so both gates already agree — confirms no
regression), and the two pre-existing holiday/weekend tests from the
original calendar-gating change corrected in place (they had asserted a
*full* no-op, which was itself the bug this fix closes — now assert price
pull skipped but candidate selection still runs). Full suite: 451 passing.

Deployed 2026-08-02, same evening, before that night's 21:30 UTC run;
production DB backed up first.

### Stock submission delayed past NYSE open — and a real sanity-check bug found investigating it (2026-08-02)

**Original goal**: move `stock_order_submission` from 13:30 UTC (right at
NYSE's regular open) to 30-60 minutes later, on the theory that the
existing `price_sanity_threshold_pct` check would then see a calmer,
more settled price instead of the opening-minute scramble. Investigating
this surfaced a materially bigger, unrelated finding first.

**Part A — intraday data feasibility, checked directly, not assumed.**
Every backtest in this project runs on daily bars; testing a submission-
timing effect needs a real intraday price at a specific point in the
session, which daily data can't provide. Tested live against Yahoo's
actual API responses (not vendor docs, not memory): `1m` interval data
is capped at ~8 days of real history; `5m`/`15m`/`30m` are capped at
~60 days (Yahoo's own error message: *"The requested range must be
within the last 60 days"*); `60m`/`1h` reaches back ~730 days (2 years —
Yahoo's own error message again confirms this exact boundary). Daily
bars and above have full history, as already used throughout this
project. **Conclusion**: no interval gives full 2007-2026 depth for this
question — the honest, best-available option is 60-minute bars over the
real 2-year window, which trades granularity (can only checkpoint at
+60min, not anywhere in the 30-60 min band) for statistical power (2
years of real, varied market conditions vs. 60 days of whatever the
market happens to be doing right now).

**The comparison, run for real** (39 tickers — this project's own
curated-40 candidate pool, minus GOLD — full available 2-year 60m-bar
history, 19,422 real ticker-days): a surprising, load-bearing result.
Opening-hour volatility was confirmed as expected — the first 60-minute
bar's own intraday range averages **2.39x** a later, settled hour's. But
distance-from-prior-close, which is what `price_sanity_threshold_pct`
actually measures, went the *opposite* direction from the original
premise: stdev of %-move-from-prior-close was **higher** at +60min
(1.83%) than at the literal open (1.42%), and the 3%-threshold trip rate
roughly **doubled** (3.71% of ticker-days at open vs. 7.29% at +60min).
Likely mechanism: the NYSE opening auction is a single-shot clearing
price that already aggregates all overnight order flow efficiently; the
following hour is often *trending* continuation (an "opening range
breakout" effect, well-documented market microstructure, and arguably
expected on a *momentum* system specifically), not mean reversion back
toward the prior close. **A 60-minute delay does not give the sanity
check a "calmer" price by the metric that check actually uses** — it
gives it a price that has, on average, moved further from the anchor.

**The bigger finding, verifying the above against the real code.**
Investigating this second-order effect required checking exactly what
the sanity check compares `decided_price` against — `_latest_close()`,
which called `get_price_df()`, a pure read of the STORED `price_data`
table. That table is only ever written by `pull_incremental()`, only
ever called from `_eod_job()`, once daily at 21:30 UTC. Stock submission
ran at 13:30 UTC the *next* day — hours *before* that day's own EOD
pull. **The sanity check was structurally comparing a value against
itself**: `_latest_close()` at 13:30 UTC could only ever return the
exact same close `decided_price` was already built from the night
before, for *any* submission time on that day, not just 13:30. Delaying
submission wouldn't have let the check see a fresher price under any
delay length — the check had no live price source feeding it at all.
This is why the empirical study above (using real yfinance intraday
data) was necessary in the first place: it was testing what a genuinely
live check *would* see, not what the actual shipped code *was* seeing,
which turned out to be nothing new regardless of timing.

**Fixed — `_live_current_price()` replaces `_latest_close()`.** Uses
`yfinance`'s `Ticker(ticker).fast_info.last_price` — a real, current
quote (subject to Yahoo's standard free-tier delay, typically ~15-20
minutes, still genuinely intraday rather than up to a full day stale).
Verified live before adopting: ~0.2-0.4s per ticker, no rate-limit
errors across repeated calls. T212's own API was considered and
rejected, same reasoning the original function already had:
`get_instruments()` carries no price field for any instrument at all
(confirmed live, see the 2026-07-31 ETF pricing-bug fix), and
`get_positions()`'s live price only exists for something already held,
which a not-yet-submitted `PENDING` order by definition isn't. "Fail
loud, don't guess" preserved exactly — returns `None` on any failure,
which the caller already treats as "leave pending, retry next window"
rather than silently falling back to a stale value that would
reproduce the exact bug this closes.

**Part B — the delay, implemented on the surviving rationale.** The
"informs a stale check" framing doesn't hold (the check was inert
regardless of timing, and is now fixed independent of timing too) — but
the **fill-timing** rationale survives on its own: a real market order
placed literally at the open executes against a price band with 2.39x
the range of a later, settled hour, a genuine, empirically-confirmed
exposure regardless of what the sanity check does or doesn't see.
`stock_order_submission` moved from 13:30 UTC to **14:30 UTC** — exactly
60 minutes, not a shorter point in the 30-60 minute range, chosen
specifically because that's the exact checkpoint the real data measured
(the first 60-minute bar's own close), not a padded guess partway
through it. `day_of_week="mon-fri"` on this job's trigger is unchanged —
out of scope for this task (separate from the market-calendar-awareness
work done for `scheduled_eod`/`membership_refresh` above).

**What this delay does and doesn't claim, stated plainly.** It reduces
exposure to the opening hour's wider price *range* (a real, measured
effect). It does **not** mean the eventual fill lands closer to the EOD
decision price on average — the same data that validated the range
effect showed the opposite for distance-from-close. These are two
different questions, and only the first one is what this change
addresses.

Covered by: 2 updated scheduler registration tests (14:30 UTC, not
13:30); 3 new/updated `_live_current_price()` tests in
`tests/test_execution.py` (returns the real `fast_info.last_price`,
returns `None` cleanly on a non-positive price or a genuine fetch
failure without raising) plus a decisive regression test
(`test_sanity_check_uses_live_price_not_stale_stored_close` — stored
`price_data` close set to a value that would NOT trip the check if
still (incorrectly) used, live quote set to a value that WOULD, check
must follow the live quote); the shared `_mock_price_data()` test helper
extended to also mock `_live_current_price()` by default (preserving
prior test semantics for every caller that doesn't specifically care
about the sanity check, since `_latest_close()` and `get_price_df()`'s
last close used to be identical) — this alone fixed 9 pre-existing
tests that started making real, unmocked network calls the moment the
underlying function changed; 2 more individual tests
(`test_submission_one_row_exception_does_not_stop_the_rest`,
`test_comprehensive_multiday_multiticker_replay_matches_backtest_trade_sequence`)
needed their own explicit `_live_current_price` mocks since they don't
route through the shared helper. Full suite: 446 passing.

Deployed 2026-08-02; production DB backed up first.

### Backtest/DD-report page rebuilt to genuinely run the current, real live engine (2026-08-02)

The page carried the caption "the same engine that drives live order placement" while actually running a configuration that no longer matched live in three material ways: it used the frozen, curated 73-ticker research universe (`analysis.universe.ALL_TICKERS` — 33 ETFs + 40 hand-picked stocks) instead of the genuinely dynamic ~503-ticker S&P 500 pool live order placement reads (`analysis.universe.get_live_stock_candidates()`); it included ETFs at all, despite the ETF sleeve being removed from live trading entirely the same day (see "ETF sleeve removed from live trading" above); and it defaulted to the cost-basis compounding engine rather than the mark-to-market one confirmed to genuinely match what live's own account-value reads do (see "Live/backtest deployment-cap parity" above). This was an actively false claim, not a stale label, and today's own full-500 broadening study had already shown real, measurable performance differences between the old and current approaches — fixed properly, not relabeled.

**`analysis/validation.py:run_full_validation()` gained a fifth, orthogonal flag, `live_universe: bool = False`.** `False` (the default, unchanged) keeps the frozen 73-ticker universe — required so the three historical-reproduction overrides (`uncapped=True` / `portfolio_mode=True` / the explicit compounding-sweep interface) keep reproducing their own already-published CLAUDE.md figures exactly, with zero risk of silently drifting. `True` replaces the candidate pool with `universe.get_live_stock_candidates(as_of=end.date())` — ETFs structurally absent (that function excludes them entirely, not merely flag-gated to zero), still respecting `trading212_invest.stock_trading_enabled` (a disabled switch empties the pool, mirroring the per-ticker gate live's own decide phase checks). The returned `"universe"` key reflects whichever universe the run actually used — the new `live_universe_snapshot()` when `True`, the unchanged `universe_snapshot()` otherwise — so a caller never has to infer which figures are behind it. **The Backtest page and DD report generator both now call `run_full_validation(config, live_universe=True, mark_to_market=True)` explicitly** — the only two flags needed, since the default-sourced `per_trade_pct`/`max_total_deployed` engine selection (see "Live execution switched to the compounding two-parameter scheme" above) already matches live with no further flags required.

**`analysis/universe.py:get_all_known_stock_tickers()`** — the raw "every ticker ever discovered as a genuine S&P 500 constituent" set was extracted out of `get_live_stock_candidates()`'s own body into its own function (union of every row `refresh_membership_from_source()` has ever written to `universe_membership`, plus `STOCK_MEMBERSHIP`'s own keys, minus `ETF_TICKERS`), with `get_live_stock_candidates()` refactored to call it — zero behavioural change, confirmed by the existing test suite passing unmodified. This is what lets `analysis.validation.live_universe_snapshot()` report from the exact same underlying set `get_live_stock_candidates()` filters, rather than a second, independently re-derived copy that could silently drift.

**`live_universe_snapshot(as_of=None)`** reports genuine S&P 500 index-membership eligibility only — never T212 tradeability, which is a materially different, live-execution-only concern that no longer even has a static, gate-able list to conflate with (ETFs are structurally absent; stock instrument mappings are resolved dynamically per-ticker via `resolve_and_cache_instrument()`, not a fixed dict). Three figures: `candidate_universe_count` (every ticker ever discovered), `live_eligible_count` (genuine members as of `as_of`), `excluded_count` (discovered historically but not currently members — delisted/removed/acquired constituents, plus a 10-name `excluded_sample`). The Backtest page's three "Candidate Universe" / "Live-Eligible Today" / "Excluded Today" metric boxes were rewired to this function, with their delta text and an explicit caption both stating plainly that these are membership figures, never T212 tradeability.

**Continuous-walk, stated explicitly.** `run_full_validation()` was already a continuous walk (one single simulated account runs `[start, end]` with no balance reset at the train/test boundary — train/test are computed by partitioning the resulting trades by `entry_date` after the fact) — this was true before this change and remains true regardless of any flag. What was missing was the standing label itself (see "Two backtest conventions" above): the result dict now carries `"backtest_convention": "continuous_walk"` unconditionally, and both the Backtest page and the DD report state it in a dedicated caption/paragraph, explaining directly why this is the correct convention for "what would the current, real system have actually returned since inception" (a single-strategy realistic-track-record question) rather than a reset-at-boundary comparison between variants.

**A pre-existing, independently-wrong block-pass-criteria caption was found and fixed while touching this page.** Both the Backtest page and the DD report had been describing the *original, retracted* per-block reconstruction ("strategy CAGR ≥ S&P 500's own... no block loses more than 20% of starting capital") rather than the *actual, reconciled* `_block_passes()` formula (`max drawdown ≤ 1.5× the S&P's own AND profit factor > 1.0`, no CAGR gate, no capital-loss floor — see "Block pass/fail formula reconciliation" above, which corrected this in code back on 2026-07-31 but never got the UI copy fixed to match). Both now state the real formula.

**A second, real accuracy problem was found and fixed the same way it surfaced everywhere else in this project: verify against a real run before shipping, don't assume.** A full production-scale verification run (isolated server clone, real copied DB, deleted after — same discipline as every backtest change this session) confirmed the code works — 500/503 tickers had usable data, 3,246 trades, ~12 minutes runtime — but also surfaced a test-period `max_drawdown_pct` of **-81.09%**, an order of magnitude worse than any previously-published figure at 73-ticker scale. Investigated directly rather than shipped as-is: `compute_metrics_fixed()`'s `max_drawdown_pct` uses the fixed-starting-capital-denominator convention (`(equity - peak) / starting_capital`, not `/ peak`) — already known from CLAUDE.md's own "No market-cap gate at all" section to overstate genuine risk once real growth has occurred, but never before checked at this scale. Computing the genuine peak-relative figure directly (`(equity - peak) / peak`) on the same real run's trades confirmed it: train -30.68% (nearly identical to the fixed-denominator -31.49%, since the worst point fell during the 2009 GFC bottom before the account had grown much past its £50,000 start) vs. **test -20.40%** against the fixed-denominator -81.09% — a genuinely different worst point entirely (2020-03-09, the COVID crash, vs. wherever the fixed-denominator "worst" point falls much later in a heavily-compounded account) and roughly a 4x overstatement, not a rounding difference.

**`analysis/backtest.py:compute_peak_relative_drawdown_pct(trades, starting_capital)`** — a new, shared, pure helper (peak-relative on the identical `equity_curve_gbp()` curve `compute_metrics_fixed()` already builds) — computed unconditionally in `run_full_validation()` for train/test/full (not gated on `live_universe`, since any run with a real equity curve can show this same distortion) and returned under a new `"peak_relative_drawdown"` dict key. Both the Backtest page (a new "Max DD (genuine peak-relative)" metric next to the renamed "Max DD (vs starting capital)", plus an explanatory caption) and the DD report (a parenthetical in the Executive Summary's Max Drawdown row, plus a footnote) now show both figures side by side, explicitly labelled — never the fixed-denominator number alone, per this project's own standing "state the convention explicitly" discipline.

**Real, verified production-scale figures (2026-08-02, `live_universe=True, mark_to_market=True`, 2007-01-01→2026-08-02), recorded here as the current reference point, not a permanent headline** — reproducible on demand by clicking the page's own button, so no disposable-clone trade-level export was preserved (unlike a one-off research finding, this is a live, always-rerunnable production feature):

| | TRAIN (2007–2019) | TEST (2020–present) |
|---|---|---|
| Trades | 2,060 | 1,186 |
| Win rate | 47.0% | 49.0% |
| Profit factor | 1.50 | 1.76 |
| CAGR | 9.56% | 36.24% |
| S&P 500 CAGR (same window) | 6.55% | 13.49% |
| Max DD (vs starting capital) | -31.49% | -81.09% |
| Max DD (genuine peak-relative) | -30.68% | -20.40% |
| £50,000 → Strategy | £150,500 | £382,147 |
| £50,000 → S&P 500 (buy & hold) | £114,033 | £114,949 |

500/503 tickers had usable data (3 excluded for insufficient history — the same `FDXF`/`HONA`/`Q` gap already documented under "V2 stock universe broadening"). **9/16 historical blocks pass** — materially fewer than the 13-14/16 previously reported at 73-ticker scale under various capped/MTM configurations. Not investigated further as part of this task (out of scope — this task's job was making the page genuinely reflect the real engine, not re-tuning or re-validating it), but the likely mechanism is the same fixed-denominator distortion documented above, applied per-block: a much larger, more actively-compounding ~500-ticker portfolio can produce larger *fixed-denominator* per-block drawdowns once the account has meaningfully grown, even where genuine per-block peak-relative risk is comparable — `_block_passes()` itself uses the fixed-denominator `max_drawdown_pct`, not the new peak-relative figure. Walk-forward: 201/223 windows profitable (90.1%). Full run: ~12 minutes (718.7s and 734.4s across two separate verification runs) — the Backtest page's spinner text states this directly rather than the old, now-inaccurate "a minute or two."

Full local suite: 499 passing (28 new tests across `tests/test_backtest.py`, `tests/test_universe.py`, `tests/test_validation.py`, `tests/test_dd_report.py`, `tests/test_main_ui.py`). Nothing in `brokers/trading212.py` or `data/scheduler.py` changed — this task was read-only against live price/signal data and UI/report-generation-only in `main.py`/`analysis/validation.py`/`analysis/dd_report.py`/`analysis/universe.py`/`analysis/backtest.py`.

### Charts page replaced with Instruments — a searchable list of the live candidate pool (2026-08-02)

The Charts page's landing view only ever rendered one card group — `reference_tickers` (^GSPC, GBPUSD=X, EURUSD=X) — because that was the only static `config.yaml` watchlist group left after the 2026-07-31/08-01 consolidation onto the dynamic live candidate pool (see "Unified instrument structure" and "wire the momentum-only, full-S&P-500 selection into live execution" above). The ~503-ticker dynamic pool was already being pulled into `get_all_watchlist_entries()` for price-history purposes, but had never been given its own card group — not useful once that pool became the actual, current live universe, and rendering ~503 individual candlestick-capable cards would have been a materially different, larger UI than a reference list needs.

**Replaced entirely, not relabeled.** `_render_charts_landing()`, the per-ticker candlestick detail view (`_candlestick_chart()`, `_volume_bar_chart()`, the inline chart-range/OHLCV block previously in `main()`), and the now-dead `_GROUP_LABELS`/`selected_ticker`/`chart_range` session-state keys are all removed outright — confirmed via a dedicated test (`test_old_charts_page_functions_are_gone_not_dormant`) that the functions no longer exist on the module at all, not merely unreachable. `get_price_df` was dropped from `main.py`'s own import line once its only caller was gone (still imported and used elsewhere via `data/ingestion.py` directly, e.g. `analysis/validation.py`).

**`_render_instruments_page(config)`** — a single, simple searchable table: Ticker, Name, and a direct Yahoo Finance quote link (`https://finance.yahoo.com/quote/{ticker}`, `urllib.parse.quote(ticker, safe='')`-encoded for tickers with special characters like `^GSPC`/`GBPUSD=X`, shown raw in the Ticker column). No in-app price chart — Yahoo Finance's own quote page does that; this page's job is "what's tracked and where do I look it up," not charting, matching the task's own explicit "no lookup needed" framing for the link itself. Sourced directly from `data.ingestion.get_all_watchlist_entries(config)` — the identical function `pull_incremental()`'s own ticker list and every other live-pool consumer already reads, so a brand-new S&P 500 entrant appears here automatically the next day, with no code change, the same guarantee `get_live_stock_candidates()` itself already provides. A plain `st.text_input` search box filters by substring match against either Ticker or Name (case-insensitive), with a "N of M instruments shown" caption.

**The "Name" column is honestly whatever name source already exists, not a new company-name lookup — stated directly, not silently overclaimed.** `get_all_watchlist_entries()` gives reference tickers a real display name (e.g. "S&P 500" for `^GSPC`), but for the ~503 dynamic S&P 500 candidates it currently sets `name == ticker` (see `data/ingestion.py`'s own docstring) — there is no company-name lookup or store anywhere in this codebase for that pool, and building one (a live per-ticker yfinance/T212 call at ~503-ticker scale, or a new persisted mapping needing its own ongoing-maintenance story) was out of this task's explicit "simple, no lookup needed" scope. The page shows exactly what exists today; a future enhancement could add a real name source without changing this page's own structure.

**Nav renamed consistently, not just the page content.** `_PAGES`/`_PAGE_KEYS` sidebar radio: `"Charts"` → `"Instruments"` (routing key `"charts"` → `"instruments"`). `_render_page_wordmark("Charts")` → `_render_page_wordmark("Instruments")`. No other page linked to or referenced the old Charts page (`selected_ticker` had zero other readers/writers, confirmed via grep before removing it) — the whole feature was self-contained, so no other cross-page reference needed updating.

Covered by 6 new tests in `tests/test_main_ui.py`: the page reachable via the same session-state routing path every other page uses; every sample ticker from a mocked `get_all_watchlist_entries()` call appears in the rendered table with a correctly-encoded Yahoo Finance link; the search box filters by ticker and, separately, by name (case-insensitive); the sidebar nav shows "Instruments" and no longer shows "Charts"; and the dedicated removal-confirmation test above. The mocking required patching `data.ingestion.get_all_watchlist_entries` (the upstream source), not `main.get_all_watchlist_entries` directly — `AppTest` re-execs `main.py`'s own top-level import statements on every `.run()`, which would silently re-bind a `main`-level patch back to the real function; this is the identical reason `main.get_engine`'s own patch in the pre-existing `app` fixture works via `data.database.get_engine` already being patched by the `db_engine` fixture itself, not because patching `main.get_engine` alone is sufficient.

Full local suite: 505 passing (6 new). Nothing in any broker/scheduler/analysis module changed — this task was UI-only in `main.py`.

### Full system audit (Parts A-H) and a direct EOD-completion signal (2026-08-02)

A combined-state audit run after the backtest/DD-report rebuild, the Charts→Instruments replacement, and the index-removal decoupling had all landed and deployed — none of the three had been verified together as one stacked whole. **Full suite against the actual combined state: 526 passing**, both live/backtest parity layers (the mechanical decision-logic-placement scanner and the comprehensive multi-day replay) still hold. No incoherence found between what each individual change reported and what the combined state actually does.

**Built: `_record_eod_completion()`/`get_eod_completion_status()`** (`data/scheduler.py`) — a direct `COMPLETED`/`FAILED`/`NOOP_NOT_A_TRADING_WINDOW` verdict written to `system_metadata` at the true end of every `_eod_job()` run, distinct from inferring health from an old `last_full_refresh` timestamp. `_eod_job()`'s body had no outer try/except before this — an unhandled exception in `pull_incremental()` or the order pipeline would have silently killed the job with nothing to show for it. A badge at the top of the Dashboard page (the default landing page) reads this directly, including a 30-hour staleness guard so a days-old `COMPLETED` verdict renders as a distinct warning rather than a falsely-fresh green checkmark. 12 new tests.

**Findings reported, not autonomously fixed** (per the audit's own scope): the `trading212_invest.live` safety switch has exactly one independent layer of protection (a single `if self.live: raise` in `Trading212Client.place_market_order()`, driven by the same boolean that also selects the live vs. demo API base URL — no secondary, independently-sourced gate exists anywhere); CLAUDE.md's own top-of-file EOD schedule table was found still describing FRED/`refresh_release_calendars()`/V1-named order functions that no longer exist in the real `_eod_job()`; several stale `INDEX_REMOVAL` references survived the decoupling change in `analysis/dd_report.py` (a customer-facing generated PDF), `brokers/trading212.py` docstrings, and `data/scheduler.py`'s `_trade_alert_job()` docstring; a held ticker can silently drop out of the daily price-pull list if `get_held_research_tickers()`'s DB query fails (logged, but the resulting behaviour looks unremarkable in the UI).

### Urgent pre-submission verification — candidate list capacity and momentum-data integrity (2026-08-02)

Raised the same night, hours before the first live submission: the `pending_orders` queue showed **318 tickers** — far more than a £50,000 account at `per_trade_pct=5%`/`max_total_deployed=50%` could ever fund — and several momentum figures looked implausible for genuine 12-month S&P 500 returns (SNDK +2699.8%, WDC +662.8%, MU +617.3%, LITE +549.9%, STX +480.7%, clustered in memory/storage/semiconductors) — the exact signature of the split-adjustment bugs found and fixed earlier the same night in the (now-removed) market-cap pipeline, raising the question of whether the same bug class had reached the momentum-signal price data too.

**Part A — genuinely capacity-filtered, confirmed by direct live simulation, not just by reading the code.** `maybe_place_orders()`'s decide phase deliberately queues every signal-qualifying candidate with **no** `max_total_deployed` enforcement at queue time — this is stated explicitly in the function's own code comment and is correct, working-as-designed behaviour, not a gap: capacity is enforced later, at submission time, against the account's real live state (see "Trade prioritization and capacity-allocation engine" above). Rather than trust that description, the exact skip-and-continue capacity walk `_submit_pending_orders()` runs was replayed directly against the real, live account state (£50,000, 100% cash) and the real 318-row queue, read-only, with no order placed: **17 of 318 admitted (47.6% projected deployment, under the 50% cap), 301 correctly rejected `REJECTED_CAPACITY`**. The mechanism works exactly as designed. No bug, no fix needed.

**Part B — genuine, verified market data, not a split-adjustment bug.** Pulled raw stored price history for all five flagged tickers and scanned the full trailing window for any single-day jump matching a clean split ratio (2:1, 4:1, 10:1, 50:1, etc.) — the exact CMG/SMCI signature. None found: the largest single-day moves were 26–29%, consistent with ordinary high-volatility earnings-day moves for volatile semiconductor names, not a step-function split artifact. Cross-checked independently against a **fresh, live yfinance pull** (bypassing the stored DB entirely): stored closes matched the live pull exactly for all five tickers, and `Ticker.actions` (the authoritative corporate-action record) showed **zero stock splits** for any of the five in the trailing-12-month window — only ordinary quarterly dividends. This also structurally rules out the CMG/SMCI bug *class* by construction: that bug was a shares-outstanding-vs-price basis mismatch feeding `market_cap()`, a function the momentum pipeline (pure price-return, no shares-outstanding input at all) never calls. **Conclusion: the extreme momentum figures are genuine** — five memory/storage/semiconductor names (SanDisk, Western Digital, Micron, Lumentum, Seagate) showing a coherent, sector-wide extreme rally, not five independent data-bug coincidences. No bug, no fix needed.

**Given no bug was found in either part, the existing queue was still cleared and regenerated from scratch as requested, rather than left standing on the strength of a "no bug found" verdict alone.** All 318 `PENDING` rows were resolved to `SUPERSEDED` via the existing `_resolve_pending()` mechanism (flagged with a note, never hard-deleted — consistent with this project's own "flag, don't delete" convention, confirmed as the pattern by the 13 pre-existing `SUPERSEDED` rows already in the table) and `maybe_place_orders()` was re-run for real against the now-deployed codebase (including the new EOD-completion-signal commit). **The fresh run reproduced the identical 318-candidate set and identical momentum values to the pixel** — expected and reassuring, since the underlying stored price data hadn't changed between the two runs, and itself a second, independent confirmation that the original run was deterministic and correct rather than a one-off fluke. The post-clear capacity simulation was re-run against the fresh queue and reproduced the identical 17-admitted/301-rejected result.

**Verdict: tomorrow's submission is safe to proceed as queued.** Both concerns were investigated directly against live production data and real code execution (not inferred), both came back clean, and the queue was regenerated from a verified-clean codebase state as an extra confirmatory step beyond what either finding alone required.

**A real deploy-timing gap surfaced immediately after: the Dashboard's new EOD-completion badge showed "no run recorded yet" despite EOD having genuinely run tonight.** Root cause confirmed directly, not assumed: tonight's real `_eod_job()` fired at 21:30 UTC, but the commit adding `_record_eod_completion()` only deployed at 21:47:50 UTC — 17 minutes later — so that run finished before the recording code existed. The subsequent manual `pending_orders` re-run (above) also called `maybe_place_orders()` directly rather than the full `_eod_job()`, so it didn't pass through the recording code either. Rather than leave the badge showing "no run recorded" for a full 24 hours until tomorrow night's real firing, tonight's record was backfilled directly from solid, independently-verifiable evidence (2026-08-02 was a Sunday, so `price_pull_ran=False` is deterministic, not a guess; the 318 genuine `PENDING` rows decided 21:30–21:31 UTC directly confirm the order pipeline ran and completed) — `snapshot_ran` was left `None`/unclaimed rather than guessed, since there's no independent evidence for that specific step from the original run. The backfilled record's `note` field states plainly that it was reconstructed retroactively and why, so it can never be mistaken for a live-recorded verdict. One-time — every future night's real run records itself normally.

### Bounded retention for `pending_orders` (2026-08-02)

`pending_orders` queues every signal-qualifying candidate before capacity filtering — confirmed the same night at 318 candidates, only 17 admitted (see "Urgent pre-submission verification" above). This ratio is structural, not a one-off: with a ~500-ticker live universe and capacity for roughly 10 positions, this table churns hundreds of resolved rows (`REJECTED_CAPACITY`/`SUPERSEDED`/`SKIPPED_*`) every single trading day, indefinitely. Left permanent, it would grow unbounded and eventually dwarf the genuinely meaningful trade history in `invest_orders` — the same unbounded-growth/explainability concern that justified fully removing the EDGAR market-cap pipeline earlier the same night rather than leaving it dormant.

**A deliberate, scoped exception to "flag, don't delete," not a contradiction of it.** That principle protects genuinely meaningful history — a real trade, a detected discrepancy, a decision that was actually acted on. `pending_orders`' resolved rows are structurally different: the large majority were never going to become a real trade by design (`REJECTED_CAPACITY` alone is ~95% of every day's queue). `invest_orders` (real trade/order history) is completely untouched by this — confirmed by a dedicated test that seeds an old, resolved `invest_orders` row and asserts it survives a purge run untouched.

**`brokers/trading212.py:purge_old_pending_orders(config=None, retention_days=None)`** — genuinely `DELETE`s rows where `status != 'PENDING'` AND `resolved_at` is older than the retention window. A still-`PENDING` row (a live, unresolved decision genuinely awaiting its submission window) is **never** purged regardless of age — deleting one would silently lose a real, still-intended decision, not routine noise. **30 days chosen as the default** (`trading212_invest.pending_orders_retention_days` in config.yaml) — long enough for near-term debugging/review of a specific day's admission decisions (the exact use case that came up during the same night's urgent verification), short enough to keep the table bounded to roughly one month of daily churn. At tonight's real ~300-resolved-rows-per-trading-day figure × ~21 trading days/month, steady-state is roughly 6,000–7,000 rows — vs. tens of thousands per year under unbounded retention.

Wired into `_eod_job()`, in its own `try/except` (a purge failure is routine, non-trading-relevant maintenance and must never turn an otherwise-successful EOD run into a `FAILED` verdict — matching the same "one step's failure never blocks another's" discipline every other step in this job already follows). Never raises.

Covered by 8 new tests in `tests/test_execution.py` (a row past the window is genuinely deleted; a row within it is preserved; a still-`PENDING` row survives regardless of age; mixed statuses in one pass; `config`-sourced vs. default retention; never raises on a DB failure; `invest_orders` confirmed completely untouched) and 2 in `tests/test_scheduler.py` (the real function is called on every EOD run; a purge failure doesn't turn `COMPLETED` into `FAILED`).

### Dashboard candidate-queue Status column — reusing the real admission projection (2026-08-02)

The confusion behind the same night's urgent verification happened specifically because the 318-row candidate list showed no visual distinction between "will actually be admitted" and "will be rejected on capacity" — understanding this required replaying the admission logic separately (done ad hoc, via SSH, during that investigation) rather than seeing it at a glance. Fixed directly on the Dashboard, reusing the exact mechanism built and used that night rather than a new, separate computation.

**`_capacity_fit_reason(position_gbp, invested, total_val, free_cash, max_deployed_pct)`** — the genuine capacity-fit decision (`deployed_after_pct > max_deployed_pct`, then `position_gbp > free_cash`) was extracted out of `_submit_pending_orders()`'s inline logic into its own small, pure function, with `_submit_pending_orders()` itself refactored to call it — a behaviour-preserving refactor, confirmed by every pre-existing `_submit_pending_orders()`/capacity test passing unchanged. This is what makes `project_pending_order_admission()` (below) a genuine reuse, not a second, independently-derived rule that could quietly drift from the real one — the same "one shared function" discipline this project applies throughout.

**`brokers/trading212.py:project_pending_order_admission(config)`** — a new, read-only function reusing `_load_pending_orders()`'s exact momentum-descending priority order and `_capacity_fit_reason()`'s exact rule, against a fresh, live read of the account's real current state. Never places an order, never resolves a `pending_orders` row, and deliberately does **not** run the price-sanity/already-held/quantity-viability checks `_submit_pending_orders()` also applies at genuine submission time — matching exactly what the night's own ad-hoc verification did, kept simple and fast for a dashboard-render-time read rather than the full submission-eligibility walk. Returns `PROJECTED_ADMIT` / `PROJECTED_REJECT_CAPACITY` per `PENDING` row; returns `[]` (never raises) on any failure — no honest projection can be made without real account state, so it refuses to guess rather than showing a wrong default.

**Labelled explicitly as a projection, never a guarantee** — both in the function's own docstring and in a caption on the Dashboard page itself, stating directly that the real admission decision only happens at actual submission time, against whatever the account's state is *at that moment*, which can genuinely differ (a position closing, an emergency exit, another candidate's order consuming capacity this projection assumed was still free).

**Dashboard wiring** (`main.py`) — the Candidate Queue table gains a Status column (joined from `_get_projected_admission_cached(config)`, a thin `@st.cache_data(ttl=60)` wrapper) and an `st.multiselect` filter over the distinct status values present, defaulting to all-selected so nothing is hidden by default. A ticker with no matching projection entry (the projection call failed entirely) shows `PROJECTION_UNAVAILABLE`, never silently defaulting to looking rejected.

**A real test-isolation bug found and fixed while building UI coverage for this** — not a pre-existing bug, but worth recording since it's a genuine hazard for any future `@st.cache_data`-wrapped function taking a leading-underscore (unhashed) parameter with no other hashable argument: `_get_projected_admission_cached(_config)`'s cache key is constant across every call in a process, since `_config` is deliberately excluded from Streamlit's hash. In the test suite, this meant whichever test happened to run first silently poisoned every later test in the same session with its own mocked projection — caught directly (a test expecting a fresh `[]` projection got a previous test's stale result instead), fixed by clearing `st.cache_data` at the start of the shared `dashboard_app` fixture. Production itself is unaffected (a 60-second TTL genuinely refreshing per real page load is the intended behaviour there) — this was purely a test-isolation gap.

Covered by 6 new tests in `tests/test_execution.py` (momentum-ranked admission with skip-and-continue capacity, purely read-only — no order placed, no row resolved, confirmed directly — empty on disabled/no-pending/account-read-failure, and a same-scenario cross-check proving `project_pending_order_admission()` and the real `submit_pending_stock_orders()` agree on which ticker is rejected) and 4 in `tests/test_main_ui.py` (Status column sourced from the real function — confirmed called, not just correct-looking; the projection/not-a-guarantee caption present; the filter genuinely narrows the table; `PROJECTION_UNAVAILABLE` shown rather than a silent default when the projection is empty).

Full local suite: 546 passing (18 new across both features).

### Instrument-resolution failure alert — content-based deduplication, not a time-based cooldown (2026-08-02)

Verification prompted directly by tonight's own real alert (TPR) firing correctly: could a second, genuinely different ticker's failure go completely unalerted if it occurred within the same cooldown window as an earlier alert?

**Finding 1 — aggregation was already correct, confirmed by reading both the caller and the function, not assumed.** `data/scheduler.py`'s post-decide-phase check calls `brokers/trading212.py:get_failed_instrument_resolutions()` — which returns every currently-`FAILED` `instrument_resolutions` row, a fresh, complete re-query on every call — and passes the **full** current set to `send_instrument_resolution_failure_alert(failed, config)` every time. This was never a per-ticker fire with a shared cooldown; the aggregation itself was never the gap.

**Finding 2 — the real gap was the cooldown, not the aggregation, confirmed by tracing the actual code path.** `send_instrument_resolution_failure_alert()`'s old gate was purely time-based (`alert_cooldown_hours`, default 8): if TPR failed and an alert went out, then a genuinely different ticker (e.g. `XYZ`) failed 30 minutes later, the next check would correctly build `failed = [TPR, XYZ]` — but the function would still return early on `"within cooldown"` before ever sending, because the check only ever asked "how long since the last email," never "does this email actually say something new." `XYZ`'s brand-new failure would go completely unalerted for up to the full 8-hour window, exactly defeating the alert's purpose — confirmed real, not hypothetical.

**Fix — content-based deduplication replaces the time-based cooldown entirely, per this task's own explicit instruction, not layered on top of it.** `output/briefing.py:send_instrument_resolution_failure_alert()` now compares the current sorted failed-ticker set (JSON-encoded) against `_INSTRUMENT_RESOLUTION_FAILURE_LAST_TICKERS_KEY` — the set actually emailed last time (`system_metadata`, its own dedicated key, unshared with any other alert's state) — and sends whenever that set has genuinely changed: a new failure appeared, an existing one resolved, or both, **regardless of how recently the last email went out**. An unchanged set never re-sends, no matter how many times the check runs. An empty `failed` list clears the stored set, so a later re-occurrence of the identical ticker is correctly treated as new rather than compared against a stale pre-resolution record.

**A deliberate behaviour change, stated explicitly rather than silently dropped.** The prior design's stated intent — "an already-known, still-unresolved failure keeps getting periodically re-flagged" — is superseded, per this task's own explicit requirement: a persistent, unchanged failure repeating the identical email every check cycle is exactly the spam a real alert should not produce. `data/scheduler.py`'s own wiring comment was updated to match. `notifications.email.alert_cooldown_hours` remains read and enforced by every other alert in this module (membership staleness, T212 order errors, position reconciliation) — this change is scoped to the instrument-resolution-failure alert only, since that is the one this task's finding was confirmed against; the identical time-based-cooldown class of gap likely exists in those other two alerts too (each gates purely on elapsed time, with no check on whether the underlying content actually changed) but was not investigated or touched here — flagged for a future task, not fixed pre-emptively alongside an unrelated finding.

Covered by 10 tests in `tests/test_briefing.py`: the four pre-existing regression cases (empty list never sends, disabled skips cleanly, first occurrence sends, ticker/note named in the body) confirmed unchanged; a new direct aggregation proof (two different tickers failing in the same check both appear, by ticker AND note, in one email); an unchanged set across two consecutive checks not resending; the exact gap this task closes — a second, different ticker failing in the same instant (zero elapsed time, no cooldown could possibly have lapsed) still triggers a fresh alert, since the set itself changed; a resolution (empty list) followed by the same ticker failing again later sending a fresh alert rather than being treated as an already-alerted repeat; disabled config not recording the ticker set; and the dedup key confirmed unshared with every other alert's own state.

Full local suite: 548 passing (2 net new).

### Dashboard candidate-table momentum-strength label (2026-08-02)

A raw momentum percentage (e.g. `+2699.8%`) forces a reader to mentally judge whether it's alarming or genuinely fine every time. Added a qualitative label alongside it, and removed the Dashboard's Candidate Queue table's "Type" column at the same time — it always read `STOCK` for every row once ETFs were removed from live trading entirely (see "ETF sleeve removed from live trading" above), so a column with zero variation across every row was pure dead space.

**Labeling convention — Weak / Moderate / Strong / Very Strong**, the standard investment-terminology tiering for momentum strength. Every candidate in `pending_orders` already has a positive signal by construction (this system is long/flat-only — a negative-momentum candidate never appears here), so the labels describe the *strength* of positive momentum, not a bullish/bearish spectrum — there is no "negative" tier.

**Bands are percentile-based, derived from the current live candidate pool's own momentum distribution, not fixed percentages** — `main.py:_compute_momentum_strength_bands()`: bottom 25% "Weak", middle 50% (25th–75th percentile) "Moderate", next 15% (75th–90th) "Strong", top 10% (90th+) "Very Strong" — the standard percentile/decile convention, top decile as "Very Strong". Recomputed fresh from the full current queue on every render, before any status filtering is applied (so filtering the table by status never changes what the bands themselves mean). Falls back to a plain `"—"` placeholder for every candidate when fewer than 4 usable momentum values exist — not enough data to form 4 meaningful bands, and a placeholder is honest where an arbitrarily-assigned label would not be.

**Relative bands were chosen deliberately over absolute fixed thresholds, and the reasoning is stated explicitly rather than assumed** — the identical trap this file has already documented for `max_holding_days` and the old flat position caps (see "Position sizing caps... binding-frequency investigation" above), just applied to a display value instead of a trading parameter. The whole candidate pool's momentum distribution moves with the market regime: a raging bull run lifts nearly every candidate's momentum together, while a flat or choppy market compresses the whole distribution toward zero. A fixed absolute cutoff would either mislabel most candidates most of the time (calibrated for one regime, applied in a different one) or need periodic manual re-tuning to stay meaningful — exactly the maintenance burden this project has repeatedly found itself paying down elsewhere. Relative percentile bands adapt automatically and always reflect real, current conditions. **The honest trade-off, stated rather than hidden**: labels are not numerically stable day to day — "Strong" today and "Strong" a month ago do not necessarily correspond to the same underlying momentum percentage. This is an accepted cost given the label's actual job — communicating today's relative standing among today's real candidates at a glance — not serving as a fixed, comparable-over-time benchmark.

**The raw percentage remains visible, always shown alongside the qualitative label, never replaced by it** — the same "show both, hide neither" principle already established for fixed-denominator vs. peak-relative drawdown (see "Backtest/DD-report page rebuilt..." above). The Candidate Queue table's Momentum column is unchanged; Strength is a new, additional column next to it.

Covered by 5 new tests in `tests/test_main_ui.py`: the exact band boundaries proven against a hand-computed pandas-quantile fixture (10 candidates, momentum 1–10, giving exact q25/q75/q90 = 3.25/7.75/9.1 — confirms the bands are genuinely derived from the pool's own distribution, not hardcoded); the raw percentage and the Strength label both present side by side for the same row; the Type column confirmed absent from the rendered table; the too-few-candidates placeholder case; and the relative-bands reasoning present in the page's own caption text.

Full local suite: 553 passing (5 new).

### Broker-side stop order — investigated, recommended with prerequisites, not yet built (2026-08-03)

Investigation only — no code changed, no order placed. Stop-loss today is purely software-monitored: `_resolve_entry_stop()` fixes an ATR-based level once at entry, `evaluate_exit_reason()` compares it against the latest price on the hourly `trade_alert_check` cycle, and a breach places a market SELL. This only protects a position if this project's own scheduled check runs and fires correctly — and this exact session already found multiple real cases of scheduled logic silently not running as intended (the membership-window bug, the pricing bug). A genuine broker-side stop order, placed directly with T212 at entry, would be enforced by T212's own systems continuously, independent of whether this project's own code is running.

**1. T212 genuinely supports stop orders — confirmed against the real API, not just documentation.** `POST /equity/orders/stop` (`ticker`, `quantity` — negative for a sell-stop, `stopPrice`, optional `timeValidity`) creates a market order once the instrument's Last Traded Price reaches `stopPrice`. Rate limit 1 req/2s (far tighter than market orders' 50/1m). `GET /equity/orders/{id}` and `DELETE /equity/orders/{id}` are the same generic per-order-id endpoints already used for market orders — nothing order-type-specific, confirmed by T212's own docs and directly against the live demo API: `POST /equity/orders/stop` with a deliberately nonexistent ticker (guaranteeing no real order could ever be created regardless of response) returned a genuine, structured `404 entity-not-found` ("Ticker does not exist") rather than a route-level 404 — proving the endpoint is real and reaches ticker-resolution before any other validation. `GET /equity/orders` (list all pending orders — an endpoint this project doesn't currently call anywhere) returned `200 []` live. `DELETE /equity/orders/999999999` on a fabricated order id returned a structured `404 entity-not-found` ("Order not found"), not a silent/ambiguous success — confirms `cancel_order()` will raise on an already-resolved order, which the lifecycle design below has to handle explicitly, not assume away.

**Genuine unknowns, honestly flagged rather than assumed** — resolving these requires placing one real (if deliberately unreachable) stop order, which this investigation's own "no live orders placed" constraint correctly excludes: the exact status string T212 returns for a stop order that is accepted but not yet triggered (needed to confirm it reads as non-terminal to the existing `poll_placed_orders()` polling loop, which only ever acts on `{FILLED, CANCELLED, REJECTED}` and otherwise correctly leaves a row untouched); and the practical behaviour of the documented "orders execute only in the main account currency" constraint for a GBP-denominated account trading USD-denominated US stocks. The second is lower-risk than it sounds — T212's own docs state this constraint applies uniformly across all order types, and market BUY/SELL orders for the identical USD instruments already fill successfully today, so there is no reason to expect stop orders behave differently — but it was not independently re-confirmed here, since doing so would require a real order.

**2. Lifecycle management — the orphaned-order risk is real, and closing it needs one new piece of logic, not a redesign.** The cleanest integration point for both halves is one this project already has almost entirely built:

- **Placement**, inside `poll_placed_orders()`, at the exact moment a BUY row genuinely transitions to `FILLED` (using the row's own already-resolved `effective_qty` — correctly the real filled quantity, not the originally intended one, so a partial fill sizes the stop correctly with no new tracking needed) — never at BUY submission time, since a stop-loss SELL placed before the BUY has actually filled would have no position to reference. On success, insert a normal `invest_orders` row: `direction='SELL'`, `status='PLACED'`, `order_id=<the real T212 stop-order id>`, `exit_reason='STOP_LOSS'`. This is the key design choice: representing the standing stop as an ordinary `PLACED` SELL row means the *existing* `poll_placed_orders()` polling loop, `_historical_fill()` 404-fallback, GBP fill-value conversion, and `_reconstruct_pnl()`'s FIFO BUY/SELL pairing all pick it up and resolve it correctly with **zero new code** in any of those functions — they already operate generically on `(order_id, status)`, with no assumption anywhere that a SELL was placed via `place_market_order()` specifically.
- **The orphaned-order risk, confirmed directly by reading `_has_recent_sell_order()`**: it already blocks a duplicate SELL attempt on *any* `status='PLACED'` row for that ticker, regardless of why it was placed — so representing the stop as a normal PLACED SELL row gets the STOP_LOSS case exactly right for free (if the software's own hourly check *also* detects the price breach, the existing duplicate guard correctly treats the standing stop as "already handled," no new code needed). But it gets a **different** exit reason wrong by exactly this same mechanism: if `evaluate_exit_reason()` returns `SIGNAL_OFF`/`MAX_HOLDING`/`CRISIS_EXIT`/`INDEX_REMOVAL` while a broker-side stop is still standing, the existing guard would *also* block the software's genuinely-needed market SELL — leaving the position open with only the (now wrong-reason) stop protecting it. Worse, if the position later closes by some other path (e.g. a future re-entry attempt is blocked by other guards, but a hypothetical redesign that ever let one through) with the stop order never cancelled, that stale stop would still be resting on T212's books and could fire against a **later, unrelated position** in the same ticker at a price level computed for the *old* entry — exactly the risk the task asked to check for, and confirmed real by tracing the code, not assumed. **The concrete fix**: in `maybe_exit_positions()`, immediately after `exit_reason` is determined and before the existing `_has_recent_sell_order()` check, when `exit_reason != "STOP_LOSS"` and a standing `PLACED`/`STOP_LOSS` SELL row exists for the ticker, call `cancel_order()` on it and mark that row `CANCELLED` first — only then does the existing code path (duplicate guard, market-sell placement) run, now correctly unblocked.
- **The residual race, handled gracefully rather than eliminated**: between the software deciding a non-price exit reason and the `cancel_order()` call actually reaching T212, the broker-side stop could still fire if price happens to be near the level at that exact moment — confirmed via probe 3 above that cancelling an already-resolved order returns a structured 404, not silent success, so this is detectable, not silent. The correct handling is to treat that 404 as "the broker's stop already closed it, not an error" — re-check `get_position()` before deciding whether the software's own market SELL is still needed, rather than blindly placing it. A genuinely benign race either way: whichever mechanism reaches the position first correctly closes it at a similar time and price.

**3. Parity is trivially satisfied by construction, not a second calculation.** The stop level a broker-side order would use is `row["stop_price"]` — the exact same value already stored on the FILLED BUY row from `_resolve_entry_stop("LONG", entry_price, atr, config)` at decide time, which is the identical value `evaluate_exit_reason()`'s software-side `STOP_LOSS` check already reads via `_load_open_position_context()`. Both paths read the same already-computed column; there is no second formula to keep in sync, avoiding exactly the class of live/backtest divergence this project has repeatedly found and fixed elsewhere (see `analysis/decision.py`'s own docstring).

**4. Rejection and partial-fill failure modes, checked directly, not assumed.** Confirmed live: an already-resolved order cannot be cancelled (structured 404, not a silent success) — the design above accounts for this. Not independently re-confirmed without placing a real order: what a genuine placement-time rejection looks like (e.g. a stop price the API considers invalid relative to current price) — expected, by analogy with probe 1's structured-error shape, to be a clean 4xx with a `detail` field this project's existing `_request()`/`_parse_json()` error handling already surfaces correctly, but this is inference from the confirmed error *shape*, not a literal reproduction of a stop-specific rejection. **The single most important NEW risk, not present anywhere else in this codebase's existing T212 integration**: T212's own docs state the stop endpoint is *not idempotent* in its beta form — "sending the same request multiple times may result in duplicate orders." `Trading212Client._request()`'s existing generic retry wrapper (up to 3 attempts, 1s/2s backoff on `RequestException`) was written for endpoints where a retried request is at worst a real duplicate trade, not a silently duplicated *standing* order that could later fire twice against the same or a rebuilt position. This generic retry logic must not be reused unmodified for a stop-order placement call — a dedicated method needs a single, non-retried attempt (fail loud and skip rather than blindly resend) given this documented behaviour.

**Recommendation: worth building as a genuine defense-in-depth layer — the mechanism is sound and integrates cleanly with existing polling/reconciliation infrastructure — but only with explicit prerequisites, not turned on directly from this investigation.** Before any real implementation: (1) ship behind a config flag defaulting off; (2) run exactly one supervised manual test on the demo account — a single real position, a deliberately unreachable stop price — to resolve the two genuine unknowns above (the pending-order status string, and final confirmation of currency behaviour) before any automated code path can ever place one; (3) build the stop-placement client method as a single-attempt, non-retried call, separate from the shared generic retry wrapper, given the documented non-idempotency; (4) land the cancel-before-alternate-exit change in `maybe_exit_positions()` before the feature is ever enabled, not after; (5) alert (reusing this project's now-established content-based alert pattern) if a stop order fails to place right after entry, so a silently-unprotected position is never invisible; (6) treat T212's own "beta" label as a standing reason to monitor for API behaviour changes going forward, not a build-once-and-forget feature.

### Submission-time crisis re-check (2026-08-03) — confirmed real gap, fixed before Monday

**Urgent finding, confirmed real by tracing the actual code, not assumed.** `passes_entry_gate()`'s crisis block (`analysis/decision.py`, backed by `analysis/crisis.py:is_entry_blocked()`) is only ever evaluated once, inside `maybe_place_orders()`'s decide phase at EOD (21:30 UTC). Stock submission fires up to ~17 hours later (14:30 UTC the next calendar day). Traced `_submit_pending_orders()` line by line: it checks already-held, a recent DB order, per-ticker price-sanity (`_live_current_price()`), position-size viability, and capacity fit — **it never called `passes_entry_gate()`, `crisis.is_entry_blocked()`, or `crisis.is_intraday_crisis()` at all before this fix.** A genuine, severe market decline beginning after decide time and before submission would not have stopped yesterday's now-stale BUY decisions from still going out — exactly the opposite of what the crisis override exists to prevent, and a real, not hypothetical, gap.

**Why simply "re-checking `is_entry_blocked()` at submission time" would not actually have fixed this — the crucial, non-obvious part of this investigation.** `compute_raw_trigger()`/`is_entry_blocked()` are daily-bar-only by design — they read `price_data`, which is only ever written once a day, at the 21:30 UTC EOD pull. At 14:30 UTC the next day, `price_data`'s most recent `^GSPC` row is still **yesterday's** close — the exact same row decide time already saw the night before. Re-running the identical daily-bar check against identical stored data at submission time would be a no-op that only *looked* like a fix. Closing the real gap needed a genuinely fresh, intraday data source.

**The fix — `analysis/crisis.py:is_intraday_crisis(gspc_close, live_price)`**, a new pure function comparing a genuinely live `^GSPC` quote (via the existing `_live_current_price()` — the same yfinance `fast_info.last_price` mechanism already used for the per-ticker submission-time sanity check, generalised to any ticker string including the index itself) against the most recent *stored* close, using the identical `PRICE_GAP_THRESHOLD` (-7%) `compute_raw_trigger()` already uses. Not a replacement for `is_entry_blocked()` — the two are complementary and both are checked: `is_intraday_crisis()` catches a decline not yet recorded as a daily bar; `is_entry_blocked()` still catches the multi-day cooldown-window case, which is already reflected in stored data and needs no live quote to detect.

**Wired into `_submit_pending_orders()`**, once per run (market-wide, not per-ticker — cheaper and correct, since the S&P's own crisis state doesn't vary by candidate), immediately after confirming there's anything pending to submit and before any per-row work: fetches `gspc_close` and a live `^GSPC` quote, checks both functions, and on a confirmed block resolves **every** currently-pending row for that asset class to a new terminal status, `SKIPPED_CRISIS_BLOCKED` (added to `PendingOrder.status`'s documented value set), with a note stating the block was re-evaluated fresh, not inherited. Nothing is submitted that run. Tomorrow's EOD decide cycle re-evaluates and re-queues anything still valid once the block clears — resolving today's stale batch loses nothing a fresh decide-time pass wouldn't naturally re-derive anyway.

**Fail-safe behaviour, deliberately asymmetric between "confirmed blocked" and "couldn't check."** A *confirmed* crisis (either check genuinely returns `True`) resolves all pending rows to the terminal `SKIPPED_CRISIS_BLOCKED` status. A *failure to even check* (a data-read exception — e.g. `_load_gspc_close()` raising) is treated differently: rows are left untouched, still `PENDING`, retried at the next submission window — mirroring exactly how a single ticker's own missing live price already leaves that row pending rather than assuming either "safe" or "blocked." This distinction matters: guessing "safe" on a check failure would silently reopen the exact gap this fix closes; but permanently discarding a legitimate queued decision over a transient network hiccup would be its own new, unnecessary cost. Confirmed via a dedicated test that forces `_load_gspc_close()` to raise: no order is placed, and the row survives as `PENDING`, not `SKIPPED_CRISIS_BLOCKED`.

**A real pre-existing test regression, found and fixed, not weakened around.** One capacity test (`test_capacity_fit_reason_matches_real_submission_rejection_for_the_same_inputs`) had never mocked `^GSPC`'s price data at all, since it predates this fix and had no reason to. Once wired in, its unmocked, empty `^GSPC` series correctly triggered the new fail-safe path (a genuine read error, since `_load_gspc_close()` calls `.set_index("date")` on an empty DataFrame with no columns) — confirming the fail-safe behaviour works exactly as designed, but breaking a test that wasn't trying to exercise it. Fixed by mocking `^GSPC` a normal, non-crashed price series (the correct fix — this is genuinely how production always looks, since `^GSPC` price history has existed continuously since 2007 — not a reason to loosen the new check).

Covered by 6 new pure unit tests in `tests/test_crisis.py` (`is_intraday_crisis()`'s threshold boundary, using the correct — most recent, not an earlier — stored close, and its None/empty/non-positive guards) and 5 new integration tests in `tests/test_execution.py`: the decisive case (crisis visible *only* via a live quote, completely absent from stored data — the exact scenario `is_entry_blocked()` alone could never have caught); the daily-bar cooldown case still correctly re-confirmed; a clean no-crisis control/regression; and the check-failure fail-safe case. Full local suite: 563 passing (10 new).

**Resolved before Monday's first live cycle** — both the stock submission job (14:30 UTC) and the (currently-paused, dead-code-only) ETF submission path share the identical `_submit_pending_orders()` function, so both are covered by this fix with no separate wiring needed.

### Crisis-mode dashboard banner (2026-08-03)

Nothing on the Dashboard previously surfaced when the crisis override was actually blocking new entries — a user checking the page during a real crisis would see normal-looking candidate/status displays with no indication anything was restricted.

**Extracted the shared authoritative check first, rather than let the banner become a second computation.** The submission-time crisis re-check above (`_submit_pending_orders()`) had its combined daily-bar/intraday logic inlined directly in that function. Pulled out into `brokers/trading212.py:is_crisis_mode_active_now() -> Optional[bool]` — the single, shared implementation of "is the crisis override blocking new entries right now," returning `True`/`False`/`None` (a data-read failure, distinct from a confirmed `False`). `_submit_pending_orders()` was refactored to call this same function rather than keep its own inline copy — a pure refactor, re-verified against all 6 pre-existing crisis-submission tests with no behaviour change.

**`output/performance.py:get_crisis_status()`'s `entry_blocked` field now defers to this same function**, rather than the daily-bar-only `crisis.is_entry_blocked()` it used exclusively before (built 2026-07-31, two days before the intraday check existed). This mattered concretely, not just for tidiness: `get_crisis_status()`'s own `today_return_pct`/`distance_to_threshold_pp` display fields are still computed from stored daily-bar data (legitimate — they're informational, not a gating decision), but before this fix, `entry_blocked` was too — meaning a crisis visible *only* intraday (the exact scenario `is_intraday_crisis()` exists to catch) would have shown the Dashboard reporting "not blocked" while order submission was already correctly refusing every new entry. A dedicated test proves this directly: stored daily-bar data shows a calm day (`is_crisis_today` correctly `False`), yet `entry_blocked` still follows the authoritative check when it reports `True`. `is_crisis_mode_active_now()` returning `None` (couldn't determine) maps to `entry_blocked=False` at this display-only layer — a display function fails toward showing nothing rather than a stale or fabricated verdict; the real safety mechanism lives entirely in `_submit_pending_orders()`'s own `None` handling, never in whether a banner happens to render.

**`main.py:_render_crisis_banner()`**, called from `_render_dashboard_page()` immediately after the trading-enabled check (so it doesn't render a redundant warning when trading is disabled entirely for an unrelated reason). Reads `_get_crisis_status()` (the existing `@st.cache_data(ttl=300)`-wrapped thin wrapper around `get_crisis_status()`) and renders an `st.error()` box only when `entry_blocked` is truthy — nothing at all otherwise, deliberately: a muted/inactive version of a warning banner risks being mistaken for a real one, so the only two states are "shown" and "not present on the page," never a third, quieter variant. The banner text states plainly that **only new entries** are affected — existing positions continue to be monitored and exited normally — matching this project's own "decision-relevant, tone-neutral" content standard elsewhere on this page, just for a genuinely urgent state this time.

Covered by: a direct unit test of `is_crisis_mode_active_now()` itself (True/False/None, using the same mocking pattern as the submission-time tests); 2 new `get_crisis_status()` tests in `tests/test_performance.py` (the daily-bar-vs-authoritative-source divergence proof above, and the `None`-maps-to-`False` case); and 4 Dashboard tests in `tests/test_main_ui.py` (banner appears when blocked, absent under normal conditions, absent on an empty/no-data status rather than misread as blocked, and the banner text contains both required halves — "new entries" and "existing positions" / "monitored"). A pre-existing gap in three `get_crisis_status()` tests was found and fixed in the same change: they mocked `data.ingestion.get_price_df` but not the new `is_crisis_mode_active_now()` boundary, which — unmocked — would have made a real DB read and a real yfinance network call inside the test suite; fixed by mocking `brokers.trading212.is_crisis_mode_active_now` directly, the correct boundary now that `get_crisis_status()` depends on it.

Full local suite: 570 passing (7 new).

### 30-day re-entry cooldown — live/backtest parity gap closed, and a real, material headline-figure change (2026-08-03)

**The gap, confirmed real.** `brokers/trading212.py:_load_open_order_tickers(days=30)` has always blocked live re-entry into a ticker with a `PLACED`/`FILLED` order in the last 30 calendar days — called from both the decide phase (`maybe_place_orders()`) and submission (`_submit_pending_orders()`). No simulation engine had an equivalent: `analysis/backtest.py` could freely re-enter a ticker the instant it exited. This is not a minor divergence — it directly violates this project's own protected "live/backtest decision-logic parity" invariant (see that section above), and given how many of today's headline findings rested on backtest results, it meant those results were produced by an engine with a genuine extra degree of freedom live has never had.

**Part A — the fix.** `analysis/decision.py:REENTRY_COOLDOWN_DAYS = 30` and `is_reentry_blocked(days_since_last_order, cooldown_days=REENTRY_COOLDOWN_DAYS)` are the single shared threshold and predicate. Following the exact precedent this file already established for candidate-priority-ranking (one SQL `ORDER BY`, one Python `sort()`, accepted duplication of a simple rule with a shared threshold and independent tests on each side — see "Live/backtest decision-logic parity" above): live's bulk SQL query (`_load_open_order_tickers()`) and the backtest engine's per-ticker inline check cannot literally share one function call, but both now read the identical `REENTRY_COOLDOWN_DAYS` constant instead of each hardcoding their own `30`, and both are independently tested against the same 30-day boundary.

**Only `analysis/backtest.py:simulate_portfolio_compounding_mtm()` enforces it** — the engine `analysis/validation.py:run_full_validation()` actually defaults to, the one confirmed to genuinely match live order placement. `simulate_trades()`, `simulate_portfolio()`, and `simulate_portfolio_compounding()` are deliberately, explicitly exempt: each is a frozen engine, kept alongside the corrected one specifically so an already-published historical figure that cites it (the "Current baseline" table, the "Portfolio-capped validation findings" table, the "Compounding portfolio backtest" sweep) stays exactly reproducible on demand. Adding new behaviour to a frozen engine would break that reproducibility guarantee rather than close a live-parity gap that matters for anything those three engines are still used for — each now states this exemption explicitly in its own docstring, a reviewed decision, not an oversight.

Covered by 8 new tests: 4 pure unit tests for `is_reentry_blocked()`/`REENTRY_COOLDOWN_DAYS` (`tests/test_decision.py`); 2 MTM-engine-level tests (`tests/test_portfolio_backtest.py`) — the decisive one proving a ticker whose signal turns back on the very next day after a `SIGNAL_OFF` exit does NOT re-enter until its exit is ≥30 calendar days old (exactly what the pre-fix engine would have done immediately), plus a control confirming a ticker's genuine first-ever entry is never treated as cooling down; 2 live-side tests (`tests/test_execution.py`) confirming `_load_open_order_tickers()`'s default now reads the shared constant, and a real DB-backed boundary check (29 days still blocks, 31 days doesn't) matching the backtest's own boundary exactly. Full local suite: 578 passing.

**Part B — does this change any headline finding? Yes, materially — reported directly, not averaged away.** The 91% index-removal-momentum finding ("Let momentum, not index removal, govern already-open positions" above) is unaffected by construction: it explicitly used `analysis.backtest.simulate_trades()` — one of the three deliberately-exempt frozen engines — confirmed by re-reading that section's own text, not re-run. The "Full-500 momentum-only (no market-cap gate)" finding does use the corrected engine, so it was re-verified directly.

**Methodology.** Isolated server-side clone (own copied DB, deleted after this section was drafted, production never touched — confirmed via `git status`/commit hash/service uptime before and after). The original investigation's `simulate_portfolio_compounding_mtm_reset_at_boundary()` wrapper (built via source introspection, since deleted along with its research clone) was reconstructed the identical way — `inspect.getsource()` on the real, current `simulate_portfolio_compounding_mtm()`, with one additive injection: `cash` is force-set so `current_balance` reads exactly £50,000 the first event date on or after 2020-01-01, once only, before that day's entries are evaluated. Verified faithful before trusting it for anything: with the reset date pushed past the end of all data (never fires), the wrapper reproduced the real, un-modified function's trades exactly, byte-for-byte, both before and after the cooldown fix (3088 and 3067 trades respectively, matching in full).

The SAME script was run twice against the SAME data (a 1,205-ticker historical candidate pool, 501 usable after excluding insufficient-history tickers — a real, larger set than the original study's frozen 503-candidate snapshot, since `universe_membership` has kept accumulating via the ongoing daily refresh job since 2026-08-02; this is expected data-recency drift, not a methodology change) — once against the pre-fix `analysis/backtest.py`, once against the post-fix version, differing in nothing else:

| | Pre-fix (BEFORE) | Post-fix (AFTER) | Change |
|---|---|---|---|
| Test-period trades (entry ≥ 2020-01-01) | 1,181 | 1,146 | −35 (−3.0%) |
| Win rate | 48.94% | 47.56% | −1.38pp |
| Profit factor | 1.77 | 1.72 | −0.05 |
| CAGR | 19.86% | 17.05% | **−2.81pp** |
| Max drawdown (fixed-denominator) | −27.48% | −23.28% | +4.20pp (improved) |
| Max drawdown (peak-relative) | −11.19% | −7.76% | +3.43pp (improved) |
| £50,000 → | £162,914.46 | £140,324.79 | **−£22,589.67 (−13.9%)** |

**The BEFORE column is itself a strong validation, not just a control**: 1,181 trades / 48.94% win rate / PF 1.77 / CAGR 19.86% / DD −27.48%/−11.19% / £162,914.46 essentially exactly reproduces the original published figures (1,182 / 48.82% / 1.77 / 19.83% / −27.43%/−11.19% / £162,802.54) — within the same ordinary day-to-day data-drift tolerance this file's own reproducibility checks have repeatedly accepted elsewhere. This confirms both that the reset-at-boundary reconstruction technique is faithful and that the pre-fix figures were correctly reported at the time.

**Direct, honest statement: this is a material change, not a rounding difference.** CAGR drops from ~19.9% to ~17.0% — roughly a 14% relative reduction in the headline growth figure — and the £-ending-balance drops by £22,590 (13.9%) on the identical £50,000 base. Drawdown genuinely *improves* under both conventions (the cooldown also prevents some rapid, whipsaw-prone re-entries into a ticker right after exiting it), so this is not a one-sided regression — but the CAGR and ending-£ figures specifically, the ones most likely to be quoted as headline numbers, are real, quantified, and lower. The direction is intuitively sound (removing 35 trades' worth of re-entry opportunity, ~3% of the total, naturally reduces total compounding) — not a symptom of a bug, but a real consequence of closing a genuine extra degree of freedom the pre-fix engine had that live never did.

**What this means going forward.** Every already-published "Full-500 momentum-only" figure in this file's "No market-cap gate at all" section was generated by the pre-fix engine and should be read as reflecting that engine, not the corrected one — the table above is now the reference for what the corrected engine actually produces on directly comparable data. The live Backtest page (`main.py`'s "Run Full Validated Battery" button, `run_full_validation(live_universe=True, mark_to_market=True)`) uses this exact engine — once this fix is deployed, the next click of that button will show these materially lower, more conservative CAGR/£-balance figures, not the previously-published ones. This is the corrected, live-parity-respecting result taking effect, not a regression to investigate.

**Not independently re-verified**: the curated-40 and rolling-40 columns of the same original comparison table, and the DAX/European reproduction. Both use much smaller candidate pools (39-40 tickers) where re-entry opportunities are inherently rarer, so the same fix is plausibly less impactful there — but this is a reasoned expectation, not a measured one, and should not be treated as confirmed without running the identical before/after comparison on those specific figures too.

### Re-entry cooldown set to 0 (config-driven) (2026-08-03)

The Part B comparison immediately above showed 0 days (no cooldown at all) clearly outperforming the 30-day default on every headline figure that moved — CAGR 19.86% vs. 17.05%, £50,000 → £162,914.46 vs. £140,324.79 — for a drawdown difference (−11.19% vs. −7.76% peak-relative) judged immaterial at this scale. Rather than leave the parity fix at a value already known to be worse, production was set to the current best-known value while a fuller sweep across intermediate values (below) checks whether something between the two endpoints does even better.

**Made genuinely config-driven, not hand-edited into the constant.** `REENTRY_COOLDOWN_DAYS = 30` (`analysis/decision.py`) remains in place unchanged, but purely as the fallback default for a config predating this parameter — the real value is resolved by the new `resolve_reentry_cooldown_days(config)`, reading `trading212_invest.reentry_cooldown_days` (falling back to the constant when absent). Both consumers now call this ONE function rather than each reading config independently: `brokers/trading212.py`'s two `_load_open_order_tickers()` call sites (`maybe_place_orders()`'s decide phase, `_submit_pending_orders()`) resolve it and pass `days=` explicitly; `analysis/backtest.py:simulate_portfolio_compounding_mtm()` resolves it once per run (alongside its other `trading212_invest`-sourced VaR parameters) and passes `cooldown_days=` explicitly into every `is_reentry_blocked()` call inside `_admit_group()`. This mirrors the established config-driven-parameter pattern already used throughout `trading212_invest:` (`per_trade_pct`, `max_total_deployed`, `price_sanity_threshold_pct`, `pending_orders_retention_days`, ...) — a Python constant as the backwards-compatible fallback, config as the real, live-editable value — and, as a direct practical consequence, makes every value in the sweep below actionable by editing `config.yaml` alone, with no code change needed per value tested.

**Production value**: `trading212_invest.reentry_cooldown_days: 0`.

**Confirmed inert for any order today.** The account had zero orders of any kind in the 30 days preceding this change (checked directly against production `invest_orders` — see the deploy record for this section's own commit for the exact query and result), so this change altered no order that would otherwise have been placed or blocked on this specific deploy; it takes effect only for whatever the system's next genuine re-entry candidate turns out to be.

Covered by 5 new tests: 3 in `tests/test_decision.py` (`resolve_reentry_cooldown_days()` reads a configured value, falls back to 30 when the key is absent — both a `{"trading212_invest": {}}` and a fully empty `{}` config — and confirms a resolved 0 genuinely un-blocks `is_reentry_blocked()`); 1 in `tests/test_portfolio_backtest.py` (the identical exit-then-immediate-signal-back-on scenario the original 30-day proof used, but with `reentry_cooldown_days: 0` in config, now re-enters the very next day rather than waiting 30); 1 in `tests/test_execution.py` (a real DB-backed row confirming the SAME just-placed order is blocked under the unchanged 30-day fallback default but not under a config-resolved `days=0`). Full local suite: 583 passing.

### Broker integration — Trading212 Invest

`brokers/trading212.py` contains two concerns:

**`Trading212Client`** — thin HTTP wrapper around the T212 Invest REST API (base URL switches between `demo.trading212.com` and `live.trading212.com` based on `live` flag). Authentication is HTTP Basic Auth over `T212_API_KEY:T212_API_SECRET`. All requests use `_request()` which retries up to 3 times with exponential back-off; HTTP 429 (rate-limited) gets a longer wait. On 4xx errors `_request()` reads the response body and includes it in the raised `HTTPError` message, so the T212 rejection reason is visible in error emails and the `error_message` column of `t212_invest_orders`. Methods: `get_account_summary()`, `get_positions()`, `get_instruments()` (module-level cache, `_INSTRUMENTS_TTL` — reduced from 24 hours to 1 hour once this cache also became the source of live sizing prices, not just static metadata; see "Quantity sizing note" below), `place_market_order(ticker, quantity)` (raises `RuntimeError` if `live=True` — live trading is hard-blocked), `cancel_order(order_id)`.

**Exit timing — `maybe_exit_t212_positions(config)` / `maybe_exit_stock_positions(config)`.** Exits are no longer tied to the EOD run. Each function closes any open position (ETF / stock respectively) whose signal or holding period calls for exit — trigger priority: immediate SELL/STRONG_SELL signal → confirmed NEUTRAL (after `signals.reversal_confirmation_runs` × 3h) → backstop time limit (`max_holding_days`, when `time_limit_as_backstop: true`) — and is called from two places:

- **Hourly**, from `data/scheduler.py:_trade_alert_job()`, unconditionally (no `run_type` gate) — a confirmed EXIT SIGNAL or STOP LOSS-equivalent condition now closes within roughly an hour instead of waiting up to 24h for the next EOD run.
- **From the EOD run**, via `maybe_place_t212_orders()` / `maybe_place_stock_orders()` (see below), which delegate their own exit phase to these same functions — exit logic is defined in exactly one place, not duplicated per call site.

Both hourly and EOD calls can land in the same rolling window, so each exit function guards against placing a duplicate SELL: before placing, `_has_recent_sell_order(us_ticker)` checks `t212_invest_orders` and skips (logging "already has a recent SELL order — skipping duplicate") if either (a) a SELL for that ticker is currently `PLACED` — **regardless of how old it is** — or (b) a SELL resolved to `FILLED`/`FAILED` within the last 24 hours.

**Why PLACED blocks regardless of age (2026-07-06 fix, superseding an earlier same-day fix that only added FAILED to a flat 24h window).** Production data showed T212's demo account can leave a market SELL sitting in `PLACED` for 60+ hours before `poll_placed_orders()` ever sees it resolve — four SELLs placed in the same batch on 2026-07-03 16:00 UTC (XLU, XLE, EWG, PDBC), all `NEUTRAL_CONFIRMED` exits, were still `PLACED` three days later; XLU and XLE only resolved to `FILLED` the morning of 2026-07-06 (the corresponding BUY fills for these same tickers took 9–10h to resolve too, suggesting this is a demo-account fill-latency characteristic, not a polling bug on this side). The original design only ever used a flat 24-hour lookback regardless of status, so once an original `PLACED` order aged past 24h without resolving, `_has_recent_sell_order()` stopped recognizing it as "still open," and the next hourly exit check placed a **second, duplicate SELL** on top of the still-pending first one — which T212 correctly rejected with `selling-equity-not-owned, owned: 0.0` (the shares were already committed to the first, unresolved order). This repeated every subsequent hour for as long as the original order stayed unresolved: XLU failed 7 times overnight before its July 3rd order finally resolved; PDBC failed once so far and, as of this writing, its own July 3rd order is still `PLACED`. The fix separates the two cases explicitly: an unresolved `PLACED` order blocks forever (there's no valid reason to ever place a second SELL while a first is still open), while a resolved `FILLED`/`FAILED` order only needs the original short cooldown (to stop the hourly job and a same-window EOD call from both selling a freshly-closed position, or a single transient failure from retrying every hour instead of once per day). Covered by `tests/test_t212_entry.py::test_exit_skips_duplicate_when_placed_sell_order_is_older_than_24h` and `test_exit_retries_sell_when_prior_failed_attempt_is_older_than_24h`.

**`maybe_place_t212_orders(config, run_type)`** — called from `_run_analysis_pipeline()` in `data/scheduler.py`.

*EXIT phase* — delegates to `maybe_exit_t212_positions(config)`, run regardless of `run_type` (mirroring the hourly call). Account state (positions, in particular) is fetched *before* this delegated call runs, so a ticker sold during this call's own exit phase still reads as held in the pre-exit `positions_by_ucits` snapshot used by the BUY phase below — it is skipped as "already held" for the rest of this run rather than immediately re-bought at (approximately) the price it was just sold at.

*BUY phase* — only runs on EOD (`run_type == "full"`) calls, which have fresh price data. First checks `allowed_entry_regimes` via `is_entry_allowed()` (see "Regime entry gate" above); if the current regime is blocked, logs `"Skipping T212 Invest BUY phase — regime is <regime>, entries blocked"` and returns immediately, before the deployed-capital check or the ticker loop. Otherwise, for each ticker in `ucits_mapping`, checks the current signal; if it is in `entry_signals` (derived from the regime-resolved `entry_signal` — see "Per-regime entry signal overrides" above; default: BUY or STRONG_BUY), checks that no position exists and no recent order is recorded in the DB (30-day window), then places a BUY market order sized via `get_quantity_for_gbp()`. Before placing, fetches `get_instruments()` metadata and applies two guards: (1) quantity is floored to an integer with `math.floor()` — all `ucits_mapping` instruments are EU/UK-listed ETFs that require whole-share quantities; T212's `maxQuantityPrecision` metadata field is not used because it is unreliable for these instruments (returns values that T212's own order API subsequently rejects); (2) the floored quantity must meet `minTradeQuantity` from the instruments metadata — if not, the order is skipped with a warning. Stops if total deployed ≥ `max_total_deployed` × account value, if currently open positions ≥ `max_open_positions` (checked right after the deployed-capital check, before the ticker loop — logs `"T212 Invest: N/M positions open — max_open_positions reached, skipping new entries."` and returns; default 999 when absent from config, i.e. unlimited), or if free cash < £10.

`entry_signal` and `exit_signal` in config control the threshold: `"BUY"` accepts BUY or STRONG_BUY; `"STRONG_BUY"` restricts to STRONG_BUY only (same pattern for exit). `entry_signal` is resolved per-regime via `entry_signal_overrides` before this filtering runs (see "Per-regime entry signal overrides" above); `exit_signal` has no regime override. All order attempts (success and failure) are written to `t212_invest_orders`. API errors set `t212_invest_error` in `system_metadata`, halt the current run, and send an alert email.

**`maybe_place_stock_orders(config, run_type)`** — a parallel automation path for direct US large-cap stocks, called from `_run_analysis_pipeline()` right after `maybe_place_t212_orders()`. Reads the separate `trading212_stocks` config block instead of `trading212_invest`, and its ticker universe comes from `watchlist.us_large_cap` instead of `ucits_mapping` — `brokers/trading212.py:_stock_universe(config)` returns the tradeable entries there, excluding any ticker already claimed by `trading212_invest.ucits_mapping` (defensive dedup; the two ticker universes are disjoint by design). No `ucits_mapping`-style dict is needed: the T212 instrument ticker defaults to the research ticker itself (e.g. `"AAPL"` → `"AAPL"`), overridden per watchlist entry via `trading212_instrument` only where the T212 name differs — the same field `_build_t212_names()` already reads elsewhere.

Structurally identical two-phase EXIT/BUY flow to `maybe_place_t212_orders()` — EXIT delegates to `maybe_exit_stock_positions(config)` regardless of `run_type`, same pre-exit account-state fetch ordering, same duplicate-SELL guard, same `_log_t212_order()` (stocks and ETFs share the one table, distinguished only by which tickers appear in each), and BUY only runs on `run_type == "full"` — with three deliberate differences:

- **Regime gate.** `trading212_stocks.allowed_entry_regimes` is `["TRENDING_BULL"]` only — same as `trading212_invest` and `backtest` (all three were aligned onto TRENDING_BULL-only after the full-history backtest showed TRENDING_BEAR has a 0.10 profit factor for long-only positions — see "Per-regime entry signal overrides" above). A single stock's downside in a bear or high-volatility regime is far less diversified than an ETF's, so this was always intended to be at least as strict for stocks even before the ETF path caught up to the same restriction. A blocked regime logs `"Skipping stock BUY phase — regime is <regime>, entries blocked for stocks"` and returns before the deployed-capital check, exactly mirroring the ETF path's early-return shape. The EXIT phase is unconditional — it runs before this check and is never regime-gated, so existing stock positions always continue to be evaluated for exit regardless of the current regime.
- **Smaller, tighter position sizing.** `max_position_gbp: 1000` (vs 2500 for ETFs) and `max_total_deployed: 0.3` (vs 0.5) — both reflect that individual stocks carry materially higher idiosyncratic risk (single-company earnings/news risk) than a diversified ETF basket. `max_holding_days: 30` (vs 90) — more active than ETFs but not as short as the forex CFD holding window. VaR position sizing (see "VaR position sizing" below) applies on top of this ceiling — a volatile stock like NVDA sizes smaller than a calmer one like WMT even though both share the same £1,000 cap. `max_open_positions: 10` — currently the same value as the ETF path (see "Regime entry gate" and `max_open_positions` above), sized independently per section like `max_position_gbp`, so the two can diverge without a code change.
- **Fractional-share ordering.** When `trading212_stocks.fractional_shares: true` and the T212 instruments API reports `maxQuantityPrecision > 0` for a ticker, the order quantity is `client.get_quantity_for_gbp()`'s GBP→USD-converted value (same conversion the ETF path uses) rounded to that precision — a decimal quantity — rather than floored to a whole share. Falls back to the identical `math.floor()` + `minTradeQuantity` guard used for UCITS ETFs when precision is 0/absent or `fractional_shares` is false. **Implementation note:** this reuses `place_market_order()`'s existing `/equity/orders/market` endpoint with a decimal `quantity` field — T212's market order endpoint accepts fractional quantities directly for instruments that support it, so there is no separate "value-based" order call implemented; "value-based vs quantity-based" in earlier design discussion collapses to the same endpoint here, just with or without the `math.floor()` step.

**EARNINGS GAP RISK:** neither `maybe_place_t212_orders()` nor `maybe_place_stock_orders()` checks an earnings calendar. A stop loss (or, in this codebase, the exit-signal/backstop logic that substitutes for one on a buy-and-hold cash position) can be gapped through overnight on an earnings announcement — the next scheduled check fills/exits at whatever price is then available, not any intended level. This is a materially larger risk for individual stocks than for diversified ETFs (one company's earnings miss moves that stock far more than any single constituent moves a basket) and is not currently mitigated anywhere in the pipeline.

**`poll_placed_orders(config)`** — called from `_fetch_t212_snapshot()` (and therefore from the 60-second dispatcher, the hourly trade-alert job, and the EOD job). Reads all rows with `status = 'PLACED'` from `t212_invest_orders` (including `quantity` and `currency_code`), calls `GET /equity/orders/{id}` for each, and updates `status` / `fill_price` / `filled_quantity` / `estimated_value_gbp` / `filled_at` / `cancelled_at` when a terminal state is reached (FILLED / CANCELLED / REJECTED). Tries multiple field names for fill price (`fillPrice`, `executionDetails.price`, `fillResult.price`) to be robust across T212 API versions. **404 handling:** T212 removes filled market orders from the orders endpoint once they become positions — a 404 does not mean cancelled.

When `GET /equity/orders/{id}` returns 404, the poller first tries `_historical_fill(client, order_id)`, which calls `client.get_order_history(limit=50)` (`GET /equity/history/orders`) and searches its `items` for a matching `order.id`. Unlike `get_order()`/`get_positions()` — which only reflect currently-active orders/positions and go dark (404 / disappear) the moment an order fully resolves — this endpoint retains the **true** fill price, quantity, and timestamp (`item["fill"]["price"|"quantity"|"filledAt"]`) indefinitely, alongside T212's own `walletImpact.netValue`/`realisedProfitLoss` (already GBP-converted, not currently consumed but available for a future enhancement). When a match is found, its real fill data is used directly — no guessing involved.

**Why the history lookup was added (2026-07-06 fix) — this used to be the only path:** `get_position(t212_ticker)` is checked for an active position (still used as the fallback when the order isn't found in the history page, or the history call itself fails): if one exists the order is marked FILLED; only if no position exists is it marked CANCELLED (direction-aware — see the code's own comment for the BUY/SELL distinction). For a SELL where no position remains, the old code derived a fill-price *estimate* from the T212 instrument's own `currentPrice`/`price` (`get_instruments()`) rather than a real fill price. In production, this returned `null` for the exact UCITS instruments affected (`SXLUl_EQ`, `SXLEl_EQ`, `XCMCd_EQ`, `VGERd_EQ` all confirmed returning `currentPrice: None` via a live check) — `poll_placed_orders()` still unconditionally marked the order `FILLED`, just with `fill_price = NULL`. Since a `PLACED`/`FILLED` order is never re-polled once resolved, that `NULL` was **permanent** — and `brokers/t212_learning.py:_reconstruct_t212_pnl()`'s `WHERE ... AND fill_price IS NOT NULL` filter silently excluded that closed position forever, so a real, already-closed T212 trade never appeared in `t212_pnl` and therefore never showed up on the Performance page's Closed Positions table, even though the position had genuinely closed. The order-history lookup returns the actual fill price T212 recorded, eliminating the guess entirely for any order still present in the last `limit` history entries. Covered by `tests/test_poll_placed_orders.py::test_sell_404_resolved_via_order_history_gets_real_fill_price`.

**Fill price extraction when history has no match (unchanged fallback):** tries `averagePrice`, `avgPrice`, `avg_price` from the position (skipping zero values); falls back to deriving average price as `currentPrice − ppl/quantity`; last resort uses `currentPrice`; for a SELL with no remaining position, falls back to the instrument-quote estimate described above (now known to be unreliable for some UCITS instruments — the history lookup existing as the primary path is what actually fixes this, not an improvement to the estimate itself). The position's `quantity` is included in the synthetic resp so `filled_quantity` is always stored. **`estimated_value_gbp` on fill:** after confirming FILLED, computes `fill_price × effective_quantity` converted to GBP using live FX rates read from `price_data` (`GBPUSD=X`, `EURUSD=X`). T212 returns `averagePrice` in the instrument's native currency (USD for most UCITS ETFs, EUR for European ones, GBP/GBX for UK-listed), so this conversion is essential to store the true GBP cost basis rather than a native-currency amount or the original intended position size.

**`filledAt` timezone normalisation (same-day follow-up fix).** T212's history endpoint returns `fill.filledAt` as `"...T...Z"` (e.g. `"2026-07-06T07:04:54.000Z"`). Python 3.11+'s `datetime.fromisoformat()` parses a trailing `"Z"` into a timezone-**aware** datetime, while every other `filled_at` in this DB is naive (`datetime.utcnow().isoformat()`, no tzinfo, no `"Z"`). Storing the raw T212 string as-is broke `_reconstruct_t212_pnl()`'s `(exit_dt - entry_dt).days` with `can't subtract offset-naive and offset-aware datetimes` — caught live in production immediately after the fix above shipped, when EWG's and PDBC's real closes both failed to reconstruct with exactly this error the first time `poll_placed_orders()` resolved them via history. `_historical_fill()` now normalises `filledAt` (`.replace("T", " ").replace("Z", "")`) to the same naive, space-separated format the rest of the codebase already uses before it ever reaches the DB. Covered by `tests/test_poll_placed_orders.py::test_sell_resolved_via_history_does_not_break_pnl_reconstruction`.

**Resolved data gap:** the two rows written with `fill_price = NULL` before the first fix shipped (`XLU`/`SXLUl_EQ` order id 11, `XLE`/`SXLEl_EQ` order id 13, both 2026-07-06) were manually corrected with their real fill price/timestamp fetched live from `get_order_history()` (the poller itself never revisits a resolved row, so these could not self-heal), then `_reconstruct_t212_pnl()` was run once by hand to populate `t212_pnl` for all four of that morning's closes (XLU, XLE, EWG, PDBC — the latter two self-healed automatically via the deployed fix). Every close from this point forward is handled automatically with no manual step.

The Status page renders a **T212 Invest Account** section. When disabled: info card. When enabled + keys present: account data is read from the **scheduler-maintained DB snapshot** (`t212_account_snapshot` / `t212_positions_snapshot` in `system_metadata`) — the Status page makes no direct T212 API calls. The "🔄 Refresh Account" button sets `pending_t212_fetch = "1"` in `system_metadata` and shows a "Refresh requested — updating within 60s" message; the 60-second dispatcher job picks up the flag and calls `_fetch_t212_snapshot()`. A "Last updated: Xm ago" caption shows snapshot age. The Status page does not auto-cycle; the log viewer has an opt-in 30-second auto-refresh toggle. Displays: 4 account metrics, a deployed-capital progress bar, a redirect caption pointing to the Daily Briefing for open positions, recent orders (last 10) with colour-coded status (FILLED=green, PLACED=amber, FAILED/CANCELLED/REJECTED=red) and a "VaR at Entry" column (`t212_invest_orders.var_pct` formatted as a %, or `—` for orders placed under `position_sizing: fixed`), a UCITS mapping table, and — when any FAILED orders are present — a **Retry failed orders** list. Each FAILED row shows the ticker, UCITS instrument ID, direction, quantity, timestamp, and truncated error message alongside a **↻ Retry** button and a **🗑 Delete** button. Clicking Retry re-fetches instrument metadata, applies the same precision floor and minimum-quantity guard, and places a new market order; success logs a new PLACED row in `t212_invest_orders` and shows an inline confirmation. Clicking Delete removes the row from `t212_invest_orders` permanently. The `t212_invest_error` banner (shown when a prior API error was stored in `system_metadata`) has a **✕ Clear** button that wipes the stored error key. Credentials (`T212_API_KEY`, `T212_API_SECRET`) are set in the systemd service file alongside `FRED_API_KEY`.

**`_get_t212_invest_brief_cards(config)`** (in `main.py`) is the shared helper that builds enriched trade-card dicts for open T212 Invest positions — covering **both** automation paths, ETFs (`trading212_invest.ucits_mapping`) and stocks (`trading212_stocks` + `watchlist.us_large_cap`), since both write into the same live T212 portfolio and the same `t212_invest_orders` table. It builds one unified `t212_ticker → (research_ticker, asset_type)` map (`asset_type` is `"ETF"` or `"STOCK"`; an ETF mapping wins on a ticker collision, though the two universes are disjoint by design) before reading `t212_positions_snapshot` from `system_metadata` (written by the scheduler's `_fetch_t212_snapshot()`) — no direct API calls are made from the UI thread. For each mapped ticker with an open T212 position it: reads the first FILLED BUY entry from `t212_invest_orders` for signal/score/date/stop context, reads the current signal from the `signals` table, applies simplified status rules (live price ≤ stored/fallback stop → **STOP LOSS**, checked first; STRONG_SELL → EXIT SIGNAL; SELL → WEAKENING; else ON TRACK) using whichever config section (`trading212_invest` or `trading212_stocks`) matches the card's `asset_type` for `max_holding_days`/`exit_on_signal`, and sets `unreal_pnl_gbp` directly from T212's `ppl` field (already GBP). Each card's `"asset_type"` field is what the Trade Log's **Type** badge column used to read before that page was removed (see "Removed: manual CFD trade logging" above), and `max_holding_days` on the card is the resolved value from whichever section applies (90 default for ETFs, 30 default for stocks). Two renderers consume these cards: the Daily Briefing's Open Trade Monitor uses `_render_trade_card_compact()` — a single-line status badge + ticker + asset type + day count + GBP P&L, sorted by `status_priority` — while the Performance page's Open Positions section uses the full-detail `_render_trade_card()` (signal delta, SL/TP, live R:R) against the same card list, so the two pages never diverge on which positions are open. T212 Invest tickers (ETF and stock) are added to `open_tickers` so they are excluded from Today's Opportunities while a position is held.

**Forex exclusion:** Tickers with a `=X` suffix (e.g. `GBPUSD=X`, `EURUSD=X`) are never candidates for T212 Invest automation. T212 Invest is a cash/equity account that only supports stocks and ETFs — forex instruments are available only through T212's CFD account, which has no public API. The `ucits_mapping` in config.yaml should never include `=X` tickers; the BUY phase skips them implicitly because they will never appear in that mapping.

**Ticker substitution:** Some entries in `ucits_mapping` intentionally map a research ticker to a different T212 instrument. For example, `VIXY` maps to `trading212_instrument: "VXX"` because T212 does not list VIXY; VXX (iPath S&P 500 VIX Short-Term Futures ETN) tracks the same VIX short-term futures index and is the practical equivalent. The rationale for each substitution is documented as an inline comment on the relevant `ucits_mapping` entry in `config.yaml`.

**`SHY`, `JNK`, and `XLRE` are analysis-only** (`tradeable: false`) because none has a `ucits_mapping` entry — per the "eligible for automated T212 Invest orders" definition of `tradeable` (see "Tradeable vs analysis-only assets" above), a ticker with no `ucits_mapping` entry and no `watchlist.us_large_cap` membership cannot be `tradeable: true`. `JNK`'s exclusion is intentional — `HYG` already maps to a US high-yield bond UCITS ETF covering the same exposure; mapping JNK too would open duplicate positions. `SHY` and `XLRE` are intentionally analysis-only — UCITS equivalents exist but not currently mapped; add to `ucits_mapping` if automation is desired (iShares $ Treasury Bond 1-3yr UCITS ETF for SHY; SPDR S&P U.S. Real Estate Select Sector UCITS ETF for XLRE — both inconsistent with the pattern of TLT/IEF/TIP being mapped and all other major SPDR sector ETFs being mapped). If adding them, locate the T212 instrument IDs via `GET /equity/instruments` and add entries under `ucits_mapping` in `config.yaml` — doing so would also need the watchlist entry's `tradeable` flag flipped back to `true` to match.

**Quantity sizing note:** UCITS instrument prices can differ from US research ticker prices by an order of magnitude in either direction — always use the T212 instrument price for sizing. `get_quantity_for_gbp(t212_ticker, gbp_amount, research_ticker=None, floor_to_whole_share=True)` sizes against the T212 instrument's own price via `get_instrument_price_gbp(t212_ticker, research_ticker=None)`, which reads `currentPrice` + `currencyCode` for `t212_ticker` from `get_instruments()` metadata and converts to GBP using live FX rates (`GBPUSD=X`, `EURUSD=X`, `GBX` pence handled as `/100`) read from `price_data`. This was a live-money bug: the previous implementation read the *research* ticker's US close price (e.g. IWM at ~$220) instead of the actual UCITS instrument's price (the IWM UCITS instrument RU2Kl_EQ trades at ~£5.25/share — a ~40x difference), and separately mis-sized XLF's UCITS instrument (STZXs_EQ, ~£130/share vs. US XLF's ~$50) — both confirmed against live demo-account fills before the fix. Only when the instruments API has no usable price for the instrument does `get_instrument_price_gbp()` fall back to `research_ticker`'s close price from `price_data` (assumed USD, the old behaviour) — this fallback logs a `WARNING`-level message naming the affected ticker so a mis-sized order is diagnosable from server logs rather than silent. Every sizing call also logs an `INFO`-level line (`"Sizing {ticker} ({t212_id}): price=... GBP, target=... GBP, quantity=..., estimated_value=... GBP"`) for the same reason. `floor_to_whole_share` defaults to `True` (UCITS ETF orders, which require whole shares); `maybe_place_stock_orders()` passes `False` and applies its own fractional-precision rounding downstream, since flooring inside `get_quantity_for_gbp()` would break fractional-share sizing. `estimated_value_gbp` on `t212_invest_orders` is computed from the actual `quantity × instrument_price_gbp` used for the order, not the pre-floor `position_gbp` target, so it reflects the true intended notional rather than an approximation.

The `get_instruments()` cache TTL (`brokers/trading212.py:_INSTRUMENTS_TTL`) was reduced from 24 hours to 1 hour because it is now also the source of the live price used for order sizing, not just mostly-static metadata (`currencyCode`, `minTradeQuantity`) — a 24-hour-stale price is an unacceptable sizing error for a market order placed today. Prices used for sizing may still be up to 1 hour stale; if this proves too coarse once real fills accumulate, consider a separate shorter-TTL cache dedicated to price lookups rather than further shrinking the metadata cache.

**Verification requirement:** this fix must be validated on the demo account before any live-trading change is considered. After deploying, manually trigger a test order for a previously-mis-sized instrument (e.g. IWM's UCITS mapping) and confirm the filled value is within 5% of `max_position_gbp` before relying on it further.

### VaR position sizing

Both `maybe_place_t212_orders()` and `maybe_place_stock_orders()` size new BUY orders with **Historical VaR** (Value at Risk) rather than a flat `max_position_gbp` for every ticker, when `trading212_invest.position_sizing` / `trading212_stocks.position_sizing` is `"var"` (the current default for both — `"fixed"` reproduces the pre-VaR flat-allocation behaviour exactly, used as a safety fallback and in tests). Position size scales inversely with the ticker's own historical volatility, so e.g. NVDA (materially more volatile than the market) sizes smaller than WMT for the same `max_position_gbp` ceiling — the two used to receive identical allocations regardless of risk.

`analysis/volatility.py:compute_var_position_size_detailed(ticker, account_value_gbp, max_position_gbp, config, confidence=0.95, section="trading212_invest")` (and its thin single-value wrapper `compute_var_position_size()`, kept for callers that only need the size) implements the formula:

1. **Historical VaR**, not Parametric or Monte Carlo. Reads the last `var_lookback_days` (default 252, ~1 trading year) of daily closes for `ticker` from `price_data` (no yfinance call — stays fast enough for ~64 tickers on every EOD run; a warm call completes in ~5ms, far under the 100ms/ticker budget), computes daily log returns, and takes the `(1 - var_confidence)` percentile — e.g. the 5th percentile at 95% confidence. Historical (percentile-based) VaR is used because equity/ETF returns are fat-tailed, so a Parametric VaR's normal-distribution assumption understates tail risk; Monte Carlo VaR was rejected as needlessly expensive for a per-ticker sizing calculation that has to run on every EOD pass.
2. **Scaled to the holding period** via the square-root-of-time rule: `holding_var = daily_var × sqrt(holding_days)`, where `holding_days` comes from `analysis/regime.py:get_max_holding_days(ticker, config)` — the same per-ticker holding-limit helper the live exit checks and backtest use, so the VaR horizon always matches how long the system actually intends to hold the position.
3. **Position size**: `position_gbp = (account_value_gbp × max_risk_pct) / abs(holding_var)` — sized so that a holding-period move of that magnitude would lose at most `max_risk_pct` (default 0.01 = 1%) of account value. Floored at `min_position_gbp` (default £50), capped at `max_position_gbp` — the existing `max_position_gbp` (£500 for ETFs, £200 for stocks) remains a hard ceiling regardless of how low volatility gets; `min_position_gbp` is a new floor so extreme volatility never sizes a position down to something not worth the order's fixed costs.
4. **Fallback**: fewer than 60 daily returns available (newly-listed ticker, data gap) → `max_position_gbp × 0.5`, logged as a warning, rather than sizing off a thin/noisy sample or blocking the order entirely.

`var_confidence`, `var_lookback_days`, `max_risk_pct`, and `min_position_gbp` are read from `config[section]` — `trading212_invest` or `trading212_stocks` — sized independently per path since ETF and individual-stock risk tolerance can differ (both currently ship with identical values, but can diverge without a code change). The BUY-phase call sites still additionally cap at 25% of available free cash, unchanged from before VaR sizing existed.

**Logging**: every VaR sizing call logs `"{T212 Invest|T212 Stocks}: VaR sizing {ticker} — daily VaR={daily_var:.2%}, holding VaR={holding_var:.2%}, position=£{position_gbp:.0f} (account=£{account_value_gbp:.0f}, max=£{max_position_gbp:.0f})"` at INFO level. The `_detailed` variant also returns `(position_gbp, daily_var, holding_var)` so the caller can persist `holding_var` as `t212_invest_orders.var_pct` (see below) without a second DB round-trip to recompute it.

**Shared core, two data sources.** The pure formula (percentile → sqrt-time scaling → risk-budget sizing → floor/cap) lives in `analysis/volatility.py:_var_position_size_core(log_returns, ...)`, which takes already-computed log returns and touches no DB — this is what makes it safe to share between two very different callers:
- `compute_var_position_size_detailed()` (live trading) fetches `log_returns` via `get_price_df()`, which always reads relative to *today*.
- `analysis/backtest.py:simulate_trades()` cannot use `get_price_df()` for this — a historical backtest needs the price history strictly *before* the entry date, not "as of today", or every entry would be silently look-ahead-biased. It instead slices the `signals["close"]` series already in memory (`close_series.loc[close_series.index < dt]`) and calls `_var_position_size_core()` directly with those pre-entry returns. See "VaR position sizing in the backtest" above, under "Backtesting engine".

`analysis/volatility.py:_MIN_VAR_RETURNS = 60` is the shared minimum-sample threshold both paths use for the fallback decision.

### Drawdown-based position scaling

`analysis/risk.py` is a mechanical, non-predictive risk control layered on top of VaR position sizing: it reduces new T212 BUY position sizes while the account is in drawdown from its own high water mark, independent of regime or signal strength. The intent is to respond automatically to *whatever* caused the drawdown — a regime transition, a late-cycle bull rollover, an unexpected event — without needing to identify or predict the cause, since the mechanism only ever looks at the account's own equity curve.

**Three graduated tiers, not a single cliff edge.** `trading212_invest.drawdown_scaling` / `trading212_stocks.drawdown_scaling` (identical shape, sized independently per the existing VaR-sizing precedent — both currently ship with identical values):

```yaml
drawdown_scaling:
  enabled: true
  lookback_days: 90           # see the high-water-mark note below — currently a monotonic HWM, not a true rolling window
  threshold_1: 0.05           # 5% drawdown from peak -> scale to 75% of normal size
  scale_factor_1: 0.75
  threshold_2: 0.10           # 10% drawdown from peak -> scale to 50% of normal size
  scale_factor_2: 0.50
  threshold_3: 0.15           # 15% drawdown from peak -> scale to 25% of normal size
  scale_factor_3: 0.25
  recovery_buffer: 0.02       # hysteresis — see below
```

`analysis/risk.py:compute_drawdown_scale_factor(account_value_gbp, config, section="trading212_invest") -> (scale_factor, current_drawdown_pct, status_label)` resolves the tier: `drawdown < threshold_1` → `NORMAL` (scale 1.0); `threshold_1 <= drawdown < threshold_2` → `CAUTION` (`scale_factor_1`); `threshold_2 <= drawdown < threshold_3` → `REDUCED` (`scale_factor_2`); `drawdown >= threshold_3` → `MINIMAL` (`scale_factor_3`). A graduated response gives a smoother risk reduction than a single on/off threshold would — position size steps down progressively as the drawdown deepens rather than snapping straight to a fixed reduced size the moment one line is crossed.

**Hysteresis (`recovery_buffer`).** Escalating to a more severe tier happens immediately — a risk control should never be slow to react to a *deepening* drawdown. Recovering to a less severe tier is deliberately damped: once `CAUTION` (drawdown ≥ `threshold_1`) is active, recovery back to `NORMAL` requires drawdown to fall below `threshold_1 - recovery_buffer`, not just below `threshold_1` itself — the same one-tier-at-a-time rule applies at every boundary. Without this, an account sitting right at a threshold would flip tiers (and therefore position sizes) back and forth on ordinary day-to-day noise. The pure tier-resolution logic — no DB access — lives in `analysis/risk.py:_drawdown_tier_from_pct(drawdown_pct, dd_cfg, prior_tier)`, shared by the live path (prior tier read from/written to `system_metadata`) and the backtest (prior tier tracked in memory per ticker — see below) so live and backtested drawdown scaling can never quietly drift apart, mirroring the shared-core pattern already used for VaR sizing.

**High water mark tracking.** `analysis/risk.py:update_peak_portfolio_value(current_value, config)` upserts `system_metadata.t212_peak_value_gbp` / `t212_peak_value_date` only when `current_value` exceeds the stored peak — a simple, monotonic high water mark that only ever increases and never decays with time. `data/scheduler.py:_fetch_t212_snapshot()` calls it on every scheduler-maintained account snapshot refresh (60-second dispatcher, hourly trade-alert job, EOD run) so the peak stays current without any manual intervention; `compute_drawdown_scale_factor()` also calls it defensively on every invocation, since every caller already has a genuine, freshly-read `account_value_gbp` in hand (from `client.get_account_summary()`) — this means drawdown scaling degrades gracefully even if the snapshot job hasn't run recently, rather than depending on job ordering. The `lookback_days` config key is accepted for parity with a true rolling-window peak, but this codebase has no persisted time series of portfolio values to reconstruct one from (only the current snapshot is ever stored) — implementing a genuine trailing-N-day peak would require a new history table, out of scope for this feature. A monotonic all-time high is also the more conservative choice for a risk control: it never lets an old high "expire" and quietly re-inflate position sizes before the account has actually recovered. The current tier is persisted under `system_metadata.t212_drawdown_tier` for UI display and so the live hysteresis check has state to compare against across calls.

Since `trading212_invest` and `trading212_stocks` automate the same underlying T212 account, the peak/tier metadata keys are account-level (one shared value), while the *thresholds and scale factors* applied to that shared drawdown are read per `section` — in principle allowing the two paths to diverge, though both ship identical defaults.

**Wiring.** `maybe_place_t212_orders()` and `maybe_place_stock_orders()` (`brokers/trading212.py`) call `compute_drawdown_scale_factor()` immediately after computing the VaR (or fixed) `position_gbp` for a BUY order, using their own section's config. When the returned scale factor is `< 1.0`, `position_gbp` is multiplied by it and then **re-floored at `min_position_gbp`** — the floor from VaR sizing still applies after drawdown scaling, so a severely-scaled position never drops below the minimum a T212 order is worth placing at all, exactly as it does for VaR sizing alone. Exit logic is entirely unaffected — this only ever scales *new* entries, mirroring how `allowed_entry_regimes` and `entry_signal_overrides` only ever gate entries, never exits.

**Backtest integration and its scoping limitation.** `analysis/backtest.py:simulate_trades()` reuses `trading212_invest.drawdown_scaling` (the backtest has no config block of its own for this, matching how it reuses `trading212_invest`'s VaR risk parameters — entry-gating alone is kept separate under `backtest:` because it can go LONG or SHORT). Unlike the live path (one shared T212 account), `simulate_trades()` runs **one ticker at a time with no cross-ticker portfolio** (the same scoping limitation `backtest.starting_capital_gbp` already has — see "VaR position sizing in the backtest" above) — so "drawdown" inside the backtest is tracked against a running **per-ticker notional equity curve** (`starting_capital_gbp` plus that ticker's own realised `pnl_gbp` so far in the run, with its own running peak and hysteresis tier), not a real shared account balance. `BacktestTrade.drawdown_scale_factor` / `drawdown_pct_at_entry` record this per-trade.

A full-history backtest comparison (2019–present, 64 tradeable assets, identical code/data/dates, only `drawdown_scaling.enabled` toggled) reproduced the exact **Current baseline** figures in both runs — 253 trades, 47.0% win rate, PF 2.48, Sharpe 0.792, max drawdown -30.29%, bit-for-bit identical `pnl_pct` and `pnl_gbp` per trade — and **zero of the 253 trades had `drawdown_scale_factor < 1.0`**. This is expected, not a bug, for the same reason VaR sizing is "neutral on %-based metrics" (see the Quantitative technique status table below): scaling only ever changes `position_gbp`/`pnl_gbp`, never `pnl_pct`, entry/exit timing, or trade selection, so win rate/profit factor/Sharpe/max-drawdown-% are mathematically incapable of moving. The zero-trigger result specifically reflects the per-ticker notional-equity scoping above: with `max_position_gbp` capped at £500 against a £10,000-per-ticker notional base (0.5%–5% of that ticker's own notional equity per trade) and each ticker in this scope only ever holding a handful of concurrent-in-time trades, no single ticker's own running equity curve drew down 5%+ from its own peak anywhere in the 2019–present window — so the tier mechanism, while fully wired and tested, never actually fired in this particular backtest scope. **Treat this backtest run as a "confirms no regression" check only, not a validation of the mechanism's real portfolio-level effect** — the same caveat already documented for change point detection not being mirrored into the backtest (see "Volatility module" above): a true test would need a shared, compounding, cross-ticker portfolio balance that `simulate_trades()`'s current one-ticker-at-a-time architecture does not have. Walk-forward windows (`analysis/backtest_metrics.py:walk_forward()`) were likewise identical between the two runs for the same reason, including the previously weak windows (2021-06 to 2022-04, 2023-02 to 2023-05) — no window's win rate, return, or drawdown changed. Shipped `enabled: true` regardless, since the mechanism is risk-neutral at the %-metric level in this backtest and provides real protection for the live, single-shared-account case the backtest architecture can't represent.

### T212 order fields (post-GARCH-removal)

The GARCH dynamic stop (see "GARCH stop loss — why it was removed" above) shipped disabled (`use_garch_stops: false`) throughout its lifetime, so its `stop_price`/`stop_distance_pct`/`vol_source` fields were **never actually populated** on any real `t212_invest_orders` row — the enforcement check that compared live price against a stored `stop_price` had accordingly never fired in production. Those three columns, `_compute_entry_stop()`, `_load_stop_prices()`, and the enforcement branch were removed entirely from `T212InvestOrder`/`T212Pnl` and `brokers/trading212.py` — T212 Invest currently has **no automated stop-loss enforcement**; positions close only on signal reversal, confirmed NEUTRAL, or the backstop time limit. `main.py:_get_t212_invest_brief_cards()` still computes an on-the-fly ATR stop via `analysis/volatility.py:_atr_fallback_stop()` for **display only** (shown on trade cards, never persisted, never enforced).

`T212InvestOrder` in `data/database.py` persists every order attempt: `order_id`, `ticker`, `t212_ticker`, `direction`, `quantity`, `estimated_price`, `estimated_value_gbp`, `fill_price`, `filled_quantity`, `status` (PLACED/FILLED/FAILED/CANCELLED/REJECTED), `signal_type`, `confluence_score`, `regime`, `exit_reason` (SELL_SIGNAL/STRONG_SELL_SIGNAL/NEUTRAL_CONFIRMED/TIME_LIMIT — null for BUY orders, see "T212 P&L reconstruction" below), `var_pct` (the `holding_var` used to size this BUY order — null for SELL orders and for BUY orders placed under `position_sizing: fixed`), `account_type` (DEMO/LIVE), `error_message`, timestamps. `estimated_value_gbp` is always written at order time (even for FAILED orders) using the intended `position_gbp`; it is overwritten on fill confirmation with the true GBP cost basis (`fill_price × filled_quantity` converted from native currency). `regime` is read via `COALESCE(s.regime, s.notes)` from the signals table to handle both the current and legacy regime storage paths.

### T212 P&L reconstruction

`brokers/t212_learning.py:_reconstruct_t212_pnl(config)` derives realised P&L for closed T212 Invest positions directly from `t212_invest_orders` — the same execution-audit table `maybe_place_t212_orders()`/`maybe_place_stock_orders()` write to — rather than requiring any separate manual trade-logging step. It is the sole data source for both the **Performance** page's Closed Positions section and the feedback-learning system (see "Feedback learning" above); no other closed-trade source exists in the app.

Mechanism: reads every `FILLED` order (`status = 'FILLED'`, both BUY and SELL) ordered by `ticker, filled_at ASC`, then FIFO-pairs each BUY with the next FILLED SELL for the same ticker via a `collections.deque` per ticker (safe because this codebase never holds more than one open T212 position per ticker at a time, so BUY/SELL pairs never interleave). For each newly-closed pair it computes:
- `pnl_gbp` — `(sell_fill_price − buy_fill_price) × quantity` in the instrument's native currency, converted to GBP using `GBPUSD=X` / `EURUSD=X` closes from `price_data` as of the *sell's* fill date (not necessarily today's rate, since this may be reconstructing a backlog).
- `pnl_pct`, `holding_days` — straightforward from the fill prices/dates.
- Signal/regime/module-score context — `signal_type`, `confluence_score`, and `regime` come from the BUY row (the entry decision); the five per-module scores (`trend_score`, `mean_reversion_score`, `volatility_score`, `volume_score`, `macro_score`) are looked up retroactively from the nearest `signals` row on or before the BUY's fill date, since `t212_invest_orders` only stores the aggregate `confluence_score`, not the per-module breakdown.
- `exit_reason` — copied directly from the SELL row's `exit_reason` column (see `T212InvestOrder` above).

Writes one row per newly-closed pair to the `t212_pnl` table (`T212Pnl` model), keyed on a `(buy_order_id, sell_order_id)` unique constraint. Idempotent by design: a Python-side pre-filter checks already-reconstructed pairs before attempting inserts, with the DB unique constraint (`on_conflict_do_nothing`) as a defensive backstop, so calling this repeatedly (it runs on every exit-check cadence) only ever writes rows for positions closed since the last call. "Never raises" contract — any DB error is logged and swallowed, matching the rest of `brokers/`'s automation functions.

**Date comparisons use SQLite's `date()` function, not raw `<=`.** A Python `datetime` with `microsecond=0` stringifies without a fractional-seconds component (e.g. `"2026-06-20 00:00:00"`), while SQLAlchemy's `DateTime` column always stores one (`.000000`). Since SQLite stores dates as TEXT and compares lexicographically, a raw `date <= :d` comparison silently excludes same-day rows more often than not — `date(date) <= date(:d)` normalises both sides to day granularity first, which is all that matters anyway since both `price_data` and `signals` are daily-bar tables.

Called from `maybe_exit_t212_positions(config)` and `maybe_exit_stock_positions(config)` — placed immediately after the `enabled` + credentials checks, **before** either function's `if not positions_by_ucits: return` / `if not positions_by_t212: return` early-return guard. This ordering matters: that guard fires exactly when there are currently zero open positions, which is precisely the state right after a position has just closed — reconstructing "after the position-checking logic" would mean it never actually runs for the single-position case. Since both exit functions already run on every hourly trade-alert cycle and from the EOD run (see "Exit timing" above), reconstruction effectively runs on the same cadence with no dedicated scheduler job of its own.

### Email alerts

`output/briefing.py` provides email functions that all send **HTML emails with plain-text fallback** via a shared `_smtp_send(subject, text_body, html_body, config)` helper. `_smtp_send` builds a `MIMEMultipart("alternative")` message, attaches both text and HTML parts, reads `smtp_pass` from config or `os.environ.get("SMTP_PASSWORD", "")`, and handles the SMTP connection/login/sendmail in one place. All HTML uses inline CSS only (no flex/grid, `bgcolor` attribute on TD elements for Outlook compatibility).

- `send_weekly_summary_email(config)` — gathers data via `_gather_weekly_data(config)` (DB-only, no external API calls). `_build_weekly_email_html(data, config)` produces HTML sections: Market Regime & Overlays, Signal Snapshot, Last Week's Performance, T212 Positions Closed This Week, Signal Accuracy Progress (HTML progress bar), Macro Pulse, Watch List. Plain text built separately by `_build_weekly_email(data, config)`. Sends stamped to `system_metadata` under `last_weekly_summary_sent`. Respects `notifications.weekly_summary.enabled` independently of `notifications.email.enabled`. The "Open Trades" section it used to include was removed along with manual CFD trade logging (see "Removed: manual CFD trade logging" above); "Closed This Week" and "Signal Accuracy Progress" both source from `t212_pnl` via `get_t212_closed_positions()` (see "Trade lifecycle" above), not the legacy `trades` table.
- `send_test_email(config, ...)` — `_build_test_html(config, regime, last_analysis, open_trade_count)` produces a status table + confirmation strip. Bypasses the enabled flag; always sends. Returns a success string with recipient addresses. `open_trade_count` is now the count of open T212 Invest positions (`len(_get_t212_invest_brief_cards(config))`), not CFD trades.
- `send_vix_spike_alert(vix_pct, vix_current, vix_prev, config)` — sent by the hourly job's VIX spike check when VIX rises >20% intraday vs. the previous close (once per calendar day).

Manual CFD trade alert emails (`send_alert_email()`, `should_send_alert()`, `_build_alert_html()`, `_options_block_html()`, and their exclusive helpers `_option_row_h()`, `_stop_distance_str()`, `_current_regime()`, `_next_analysis_run_uk()`, and the `_STATUS_EMAIL_LABELS`/`_REGIME_SIGNAL_NOTES`/`_STATUS_PRIORITY` constants) were removed along with the CFD alerting loop in `_trade_alert_job()` — see "Removed: manual CFD trade logging" above. `STATUS_COLOURS`, `STATUS_ICONS`, and `_pnl_color()` are kept — they're still used by `main.py:_render_trade_card()` (T212 cards) and the weekly summary email respectively.

When writing raw SQL against the `correlations` table in email or scheduler code, use the actual column names: `divergence` (not `status_30d`), `z_score` (not `z_score_30d`), `pair_score` (not `expected_direction`).

### yfinance MultiIndex handling

`yf.download()` for a single ticker returns a MultiIndex column DataFrame in yfinance ≥ 0.2.x. `ingestion.py` flattens this with `df.columns = [col[0] for col in df.columns]` before processing. Any new code that calls `yf.download` for a single ticker must do the same.

## Deployment (Ubuntu 22.04 / cloud VM)

The app runs as a systemd service behind a Caddy HTTPS reverse proxy.

### First-time setup on a fresh VM

```bash
# 1. Clone the repo
git clone https://github.com/YOUR/investment-research.git /root/investment-research
cd /root/investment-research

# 2. Run the one-time setup script (takes ~5 min)
bash setup.sh research.yourdomain.com
```

`setup.sh` installs Python 3.11, compiles the TA-Lib C library from source, installs Caddy, installs all packages system-wide via `python3.11 -m pip`, writes `.streamlit/config.toml`, and registers both `investment-research.service` (Streamlit web UI) and `investment-research-scheduler.service` (standalone scheduler daemon) with systemd. It also creates a symlink so the scheduler daemon inherits credentials from the same secrets drop-in file as the web UI service. There is no virtualenv — packages are installed at the system level.

### Password-protecting the dashboard

Caddy's `basicauth` directive guards the app. After `setup.sh` runs:

```bash
caddy hash-password          # enter your password, copy the hash output
nano /etc/caddy/Caddyfile    # replace REPLACE_WITH_HASH (and 'admin' if desired)
systemctl reload caddy
```

### Managing server credentials

API keys and passwords live in a systemd drop-in override file that is never touched by deploys:

```
/etc/systemd/system/investment-research.service.d/secrets.conf
```

```ini
[Service]
Environment=FRED_API_KEY=...
Environment=SMTP_PASSWORD=...
Environment=T212_API_KEY=...
Environment=T212_API_SECRET=...
```

After editing: `systemctl daemon-reload && systemctl restart investment-research && systemctl restart investment-research-scheduler`. The `investment-research.service` template in the repo has blank placeholders — never put real values there. The drop-in takes precedence and is unaffected by `deploy.sh` or `git pull`.

The scheduler daemon inherits the same credentials automatically. `setup.sh` and both deploy scripts create `/etc/systemd/system/investment-research-scheduler.service.d/secrets.conf` as a symlink pointing at the web UI drop-in — only one secrets file to manage.

### Ongoing deploys (from Windows)

The easiest path is a plain `git push origin main` — the pre-push hook calls `deploy.ps1` automatically (see [Git hooks](#git-hooks) below). To deploy without that hook, or from a machine where it isn't installed:

```bash
bash deploy.sh          # Git Bash — VM_IP is hardcoded in the script
```

```powershell
.\deploy.ps1            # PowerShell — gitignored, local-only; SSH key at ssh-keys\
```

Both scripts push to GitHub first, then SSH into the VM to `git pull`, reinstall any new packages, copy both service files to `/etc/systemd/system/`, ensure the secrets symlink exists for the scheduler service, run `systemctl daemon-reload`, and restart both `investment-research` and `investment-research-scheduler`. Service file changes in the repo take effect automatically on the next deploy.

**Two systemd services must both be restarted on every deploy** — there is no way to update just one:

- `investment-research` — the Streamlit web UI.
- `investment-research-scheduler` — the standalone APScheduler daemon (`scheduler_daemon.py`) that fires all ten scheduled jobs even when no browser is connected (see "Status page and startup automation" above).

Both processes import the same modules independently at startup and keep their own in-memory copy of the code until restarted. **If a deploy restarts `investment-research` but not `investment-research-scheduler`, the scheduler daemon keeps running the pre-deploy code** — including stale versions of `analysis/regime.py`'s `is_entry_allowed()` / `resolve_entry_signal()` and `brokers/trading212.py`'s order-placement logic. Since the daemon (not the web UI) is what actually places T212 orders on the EOD/pre-market/US-open schedule, this means newly-deployed regime gates, entry-signal overrides, or any other order-logic change silently fail to apply — orders keep being placed under the old rules until the daemon itself is restarted. `deploy.sh`, `deploy.ps1`, and `setup.sh` all restart/enable both services for exactly this reason — never edit them to restart only `investment-research`.

After any deploy that touches order logic, verify both services picked up the same code by checking their start times match the deploy:

```bash
ps aux | grep -E "streamlit|scheduler_daemon" | grep -v grep
systemctl status investment-research investment-research-scheduler
```

### Git hooks

`setup-hooks.sh` installs a pre-push hook that auto-deploys whenever you push to `main`. Run once after cloning:

```bash
bash setup-hooks.sh
```

The hook runs `pytest` from the project root first. A failing suite aborts the push (exit 1) with `❌ Tests failed — push aborted. Fix failing tests before deploying.` — nothing reaches GitHub or the server. A passing suite prints `✅ Tests passed — pushing now; deploy to platform.example.com will follow in the background.` and exits 0, letting the outer `git push` (the one you actually ran) proceed and push the commits itself. See "Running tests" above for what the suite covers and how it stays self-contained.

The hook does **not** push to GitHub itself — it schedules the SSH deploy as a detached background job (`nohup ... & disown`) that sleeps 15 seconds before running, giving the outer `git push` time to land on GitHub first, then SSHes in to `git pull` and restart the service. Output is appended to `/tmp/investment-research-deploy.log` on the local machine rather than the terminal, since the hook has already exited by the time it runs. (An earlier version of this hook pushed to GitHub from inside itself before deploying, which raced the outer `git push`: the hook's own push advanced `origin/main`, so the outer push — still holding the old remote SHA it read at hook-invocation time — got rejected with `cannot lock ref`. Not pushing from the hook at all removes the race.) To skip both the tests and the deploy for a single push: `git push --no-verify`.

### Deployment timing

Every scheduled job (see `start_scheduler()` in `data/scheduler.py`) fires at a fixed UTC (or, for the weekly summary, Europe/London) time and several place or submit live broker orders. A deploy that lands mid-job — `git pull` mid-way through, or `systemctl restart` killing the process mid-run — leaves the service running a half-updated version of the code or reading a half-updated `config.yaml` for the remainder of that run, and can commit broker order decisions based on that transitional state. The scheduled job windows most at risk:

| Window (UTC) | Weekdays only? | Job |
|---|---|---|
| 06:45–07:15 | Yes | `etf_order_submission` (07:00, submits queued ETF entries during LSE hours); `monday_premarket` (07:00, Mondays only — cancels stale weekend orders first, then re-runs the order pipeline) |
| 13:15–14:00 | Yes | `stock_order_submission` (13:30, submits queued stock entries during NYSE's regular open) |
| 21:00–22:00 | Yes | `membership_refresh` (21:15) and `scheduled_eod` (21:30, full data refresh + order placement) — EOD is the highest-risk job, the one that places T212 orders |
| 07:45–09:15 (Sun) | No — Sunday only | `weekly_summary` — fires at 09:00 *Europe/London*, not a fixed UTC time (08:00 UTC in BST, 09:00 UTC in GMT); the window covers both possible times with a buffer rather than the script computing today's DST state at runtime |

**Corrected 2026-08-02 — this table, and the two deploy scripts' own blackout checks, were stale in three ways, found and fixed together:** (1) the 06:45–07:15 and 13:15–14:00 windows still named the "pre-market analysis"/"US open analysis" jobs removed 2026-07-31 (see "Pre-market/US-open scheduled runs removed") — both time slots are still genuine order-placing windows today, just under the newer `etf_order_submission`/`stock_order_submission` names (see "Exchange-aware order submission"); (2) the Sunday window (previously 09:45–10:15) assumed a fixed 10:00 UTC weekly-summary time that predates that job moving to 09:00 Europe/London (see "Weekly summary email — Sunday 09:00 UK local time") — 10:00 UTC never matched either DST case, and the old window didn't even bracket the 09:00 GMT case correctly; (3) none of the three weekday windows ever checked day-of-week at all, so all three fired as false positives every Saturday/Sunday — confirmed directly by dry-running the range logic against injected times/weekdays, not assumed, before trusting the fix. A real deploy on Sunday 2026-08-02 at 13:26 UTC hit exactly this: the (then-stale) 13:15–14:00 window showed active, and proceeding required manually re-deriving that the underlying job no longer applied — this fix makes that judgment automatic instead of something every future Sunday/Saturday deploy has to re-derive from scratch.

**Blackout warning (`deploy.sh` / `deploy.ps1`):** both deploy scripts check the current UTC time before doing anything else. `deploy.sh` compares `date -u +%H%M`/`date -u +%u` against the ranges above (weekday-gated via a small `is_weekday()` helper for the three non-Sunday windows); `deploy.ps1` uses `[System.DateTime]::UtcNow`/`.DayOfWeek` the same way. If the current time falls inside any window, the script prints a warning naming the specific job and its run time (e.g. `⚠️  WARNING: Stock order submission job runs at 13:30 UTC weekdays — deploying now risks order logic running on transitional state.`), followed by `Note: deploy restarts BOTH investment-research and investment-research-scheduler.`, and prompts `Continue anyway? (y/N):`. Any answer other than `y`/`Y` aborts with exit code 1 before either script touches git or SSHes into the server — nothing is deployed. This is a warning, not a hard block, because some deploys (urgent hotfixes) legitimately need to go out regardless of the clock. The reminder matters more than usual here: a deploy that skips restarting the scheduler daemon is worse than no deploy at all — the web UI shows new code while the daemon keeps placing orders under the old logic (see "Ongoing deploys" above).

**Missed EOD catch-up (`data/scheduler.py:_check_missed_eod_run()`):** the inverse safety net — if a restart (from a blackout-window deploy or otherwise) causes the 21:30 EOD job itself to be skipped for the day, this runs at scheduler startup, after all jobs are registered, and detects it. It requires both: `last_full_refresh` (in `system_metadata`) is more than 20 hours old — a run from earlier the same day doesn't count as missed — and the current UTC time is between 21:45 and 23:59 — the 15-minute grace period after 21:30 plus the rest of the post-EOD window. Inside that window with a stale timestamp, it logs `"Missed EOD run detected — running catch-up pipeline now"` and runs `_run_analysis_pipeline(config, get_all_tickers(config), "full")` in a background daemon thread so `start_scheduler()` returns promptly. Outside the window it skips silently — the next scheduled job (tomorrow's 07:00 pre-market or 21:30 EOD) will pick up stale data naturally. A `None` `last_full_refresh` (fresh install, no data yet) also skips silently, and the existing weekend guard (no EOD job is ever scheduled Saturday/Sunday) is preserved. This replaced an earlier, looser version (`_maybe_catch_up_missed_eod`) that used a 26-hour threshold OR'd with an unbounded "past today's EOD time" check and re-ran the full `_eod_job` (data pull + FRED + T212 snapshot) rather than just the analysis/signal/order pipeline.

**Recommendation:** for deployments affecting order logic (`brokers/trading212.py`, `data/scheduler.py`, `analysis/confluence.py`, `config.yaml`), deploy before 21:00 UTC or after 22:00 UTC on weekdays.

### Key files

| File | Purpose |
|---|---|
| `setup.sh` | One-time server bootstrap (no virtualenv; installs packages system-wide) |
| `investment-research.service` | systemd unit for the Streamlit web UI |
| `investment-research-scheduler.service` | systemd unit for the standalone scheduler daemon (fires jobs at boot, no browser required) |
| `scheduler_daemon.py` | Entry point for the scheduler daemon — calls `init_db()` + `start_scheduler()` then sleeps in a heartbeat loop |
| `Caddyfile` | Caddy reverse-proxy template (gitignored — contains domain/hash) |
| `.streamlit/config.toml.example` | Reference for the server config created by `setup.sh` (actual file is gitignored) |
| `deploy.sh` | Git Bash deploy script (VM_IP hardcoded; SSH key at `ssh-keys/`) |
| `deploy.ps1` | PowerShell deploy script — gitignored (local-only, contains SSH key path) |
| `setup-hooks.sh` | Installs the pre-push git hook; run once after cloning |
| `config.yaml` | Runtime config — **actually git-tracked** (not in `.gitignore`, despite this file previously documenting it as gitignored/server-managed). Secret fields (`macro.fred_api_key`, `notifications.email.smtp_password`) are committed as blank placeholders — real values are supplied only via env vars (`FRED_API_KEY`, `SMTP_PASSWORD`, `T212_API_KEY`, `T212_API_SECRET`) in the server's systemd secrets drop-in, never written into this file. Verify those fields stay blank before committing any change here. |

### TA-Lib: Windows vs Linux

`requirements.txt` has both package names commented out with explanations. `setup.sh` installs `TA-Lib` (Linux wrapper) after compiling the C library. Windows dev installs `TA-Lib-Precompiled` manually if needed; all analysis modules have pure-pandas fallbacks so it is optional on both platforms.

### Useful server commands

```bash
journalctl -u investment-research -f              # web UI live logs
journalctl -u investment-research-scheduler -f   # scheduler daemon live logs
systemctl status investment-research             # web UI service health
systemctl status investment-research-scheduler   # scheduler daemon health
systemctl restart investment-research-scheduler  # restart scheduler (e.g. after config change)
systemctl reload caddy                           # apply Caddyfile changes without downtime
```

## Adding a new analysis module

1. Create `analysis/your_module.py` with a `compute_your_module(df, config) -> dict` returning `{"score": int, ...raw_values}`.
2. Call it in `analysis/engine.py:analyse_ticker()` and merge its raw values into `indicator_values`.
3. Call it in `analysis/confluence.py:score_ticker()` and add its score to `module_scores`.
4. Add `"your_module"` with a weight to every regime dict in `analysis/regime.py:REGIME_WEIGHTS` — weights must still sum to 1.0 per regime.
5. Add `your_module_score` to the `Signal` model in `data/database.py`, to `_apply_migrations()`, and to the upsert in `confluence.py:_persist_signal()`.
6. Add any new raw-value columns to the `Indicator` model and to `_apply_migrations()`.
7. Update `total_signal_modules` in `config.yaml`.

Note: `analysis/correlations.py` is an exception to the pattern — it operates on pairs of assets rather than a single ticker's DataFrame, so `compute_all_pairs()` is called once at the end of `run_analysis()` rather than inside `analyse_ticker()`, and `compute_correlations(ticker, config)` fetches its own data internally rather than accepting a pre-fetched `df`.

## The live trading universe — fully dynamic, no manual curation (rewritten 2026-08-03)

**This section used to describe a manual `watchlist:` config-editing workflow (add a ticker, set a `tradeable` flag, place it in a sector group). That workflow, that config section, and the flag no longer exist.** `config.yaml` has no `watchlist:` section at all any more — only `reference_tickers:`, a short, fixed list of monitoring-only instruments (`^GSPC`, `GBPUSD=X`, `EURUSD=X`) that never gets an execution mapping and essentially never changes. The live stock-trading universe is not configured anywhere — it is derived automatically, every day, from real S&P 500 index membership.

**How a ticker actually becomes tradeable.** `analysis/universe.py:get_live_stock_candidates()` returns every ticker this system has ever discovered as a genuine (former or current) S&P 500 constituent, filtered down to genuine membership *as of today* via `is_member()`. The discovery side, `refresh_membership_from_source()`, re-fetches a public, point-in-time S&P 500 membership dataset (`fja05680/sp500` on GitHub — see `analysis/universe.py`'s own module docstring for the verification this source received before being trusted) once a day, 15 minutes ahead of the EOD job (the `membership_refresh` scheduler job, 21:15 UTC). **There is no onboarding step for a new S&P 500 addition and no code change for a removal** — a stock added to the real index today becomes a live candidate the moment tomorrow's membership refresh discovers it; a stock removed from the index stops being one the same way. `passes_entry_gate()` (`analysis/decision.py`) still requires a positive 12-month-momentum signal and a clear crisis-override window before any of this turns into an actual entry — membership alone only ever determines *candidacy*, never a decision to buy.

**No discretionary exclusions — this is a stated design decision, not an oversight.** `analysis/universe.py`'s own docstring is explicit: a stock is eligible on any date it was a genuine index constituent, nothing else. TSLA, META, and GOOGL are all live candidates, included from their real membership dates. An earlier version of this system excluded these three names (plus TSM) for subjective "socio-political/geopolitical risk" reasons — that exclusion is deliberately not reproduced in the current design, and should not be reintroduced without a fresh, explicit decision; see the module's own docstring for the full reasoning, not just this summary. TSM itself is absent from the universe for a genuinely different, objective reason: Taiwan Semiconductor is a foreign private issuer, and foreign companies are structurally ineligible for S&P 500 inclusion — it has no membership window in the source data at any date, unrelated to the earlier subjective exclusion.

**A separate, deliberately frozen universe exists purely for backtest/research reproducibility — do not confuse the two.** `analysis/universe.py:ALL_TICKERS`/`ETF_TICKERS`/`STOCK_MEMBERSHIP` is a hand-curated, static set (33 broad ETFs + 40 US large-cap stocks, one of which — `GOLD`, Barrick Gold — is deliberately kept with an empty membership window since it was never a genuine constituent, kept only so a specific historical backtest figure stays reproducible) used only by the frozen research/backtest engines this file's own "V2 architecture" and later sections validate against. It is intentionally *not* kept in sync with the live universe — changing it would silently invalidate every already-published backtest figure that cites it. There is no live-trading reason to ever add a ticker here; doing so is a deliberate, rare research action (e.g. reproducing or extending an already-published study), not a routine "add an asset" task.

**What, if anything, genuinely still needs a manual edit today:** adding a new *reference* ticker (a fixed monitoring/conversion instrument like `^GSPC` or a currency pair) to `reference_tickers:` — genuinely rare, since the three there today cover the crisis-override index and the two FX conversions the system currently needs. There is no other manual asset-management step in the live system.
