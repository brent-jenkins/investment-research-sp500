# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**This is the short, current-state file.** It holds the standing invariants a session must never violate, a technical summary of the system as it actually is right now, and the practical day-to-day operational material (tests, deployment, server commands). For the full chronological build log — every dated investigation, incident, fix, and rejected experiment that led to today's system, preserved verbatim — see **`CLAUDE_HISTORY.md`** in the project root. This file (`CLAUDE.md`) was split from a single ~8,300-line file on 2026-08-04 specifically so the material a session needs most doesn't have to be found by scrolling past thousands of lines of historical detail; nothing was deleted, corrected, or summarized in the split — `CLAUDE_HISTORY.md` is the original narrative, unedited, and this file's standing-invariant sections below are moved from it verbatim. Everything else in this file (the architecture summary and the operational sections) is freshly written, verified directly against the current codebase at split time, not copied from the old file's own scattered "reference-looking" sections — several of those were checked directly against the current code while planning this split and found to describe defunct V1-era modules, removed config keys, and a superseded scheduler layout, which is exactly the kind of drift a single continuously-growing chronological file makes easy to miss. Treat anything in `CLAUDE_HISTORY.md` as a record of what was true *when it was written*, not as current fact, unless cross-checked against this file or the real code.

## Standing invariants and requirements

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

**A research script whose output is a database mutation needs an explicit, separate production-apply step — "runs locally" covers the computation, not the write (standing requirement, 2026-08-08).** The Wikipedia-cross-reference adjudication (`research/needs_review_adjudication_2026-08-07/step3_refined_matcher_and_apply.py`) ran locally per this section's own practice, correctly — but its 105 `write_data_quality_status()` calls went to the local research DB copy only, via the script's own relative `database/investment_research.db` path, and were never separately applied to the server. The commit that shipped it (`c4450b7`) contained no production code at all — CLAUDE.md itself was updated to state 220 CLEAN / 25 NEEDS_REVIEW / 163 QUARANTINED as current fact, and stayed wrong for a full day, until an audit caught the gap directly rather than the claim being re-verified. **The failure mode is specific and easy to miss**: a script that mutates state has a *side effect*, not just a *result* — "I ran this locally and it worked" is true and also insufficient, because the mutation lives only in the environment it ran against. A pure-computation research script (a backtest, a sweep) has no environment-locality problem — its output is a number or a file, copied into the write-up by hand. A DB-mutating script's output is a row that only exists where the script pointed, and nothing about "the script ran and printed success" distinguishes a local-only run from a production-applied one.

**The rule.** Before treating a database-mutating research script's result as production state — and before writing that result into CLAUDE.md as current fact — confirm which database the script actually wrote to, and if it was the local copy, explicitly re-run (or port and re-run) the write step against the server's real database over SSH, then **independently re-query production directly** (not the script's own printed summary) to confirm the mutation landed. This mirrors the discipline this project already applies to the pre-warm reconciliation pattern (`data-quality`'s own SSH pre-warm steps, verified by direct `SELECT` against the live DB, not the script's own output) — the gap here was that this specific script's *research* half ("compute the adjudication") got that treatment and its *apply* half ("write it to the database that matters") didn't, because nothing marked the two as separate steps needing separate verification.

**Any documented state claim in CLAUDE.md or CLAUDE_HISTORY.md must name which database it describes, not leave it implicit.** "Final counts: X CLEAN / Y QUARANTINED / Z NEEDS_REVIEW" reads as a fact about the system; it is actually a fact about *whichever database was queried to produce that sentence*, and a reader (human or a future session) has no way to tell local from production from the sentence alone. State it explicitly — "confirmed on production via SSH, `<date>`" or "local research DB only, not yet applied to production" — every time, the same way this file already insists every backtest report state which of the two backtest conventions it used, and every cost-adjacent report state which cost is deferred. A claim with no named database should be treated as unverified for production purposes until re-checked.

**Every local Claude Code session uses its own `git worktree` (`git worktree add <path> <branch>`) — unconditionally, from the first tool call, never contingent on first checking whether another session happens to be active.** This rule was originally conditional ("if more than one session is running at the same time") — tightened to absolute 2026-08-09 after a third incident (below) showed the condition itself is the problem: a session has no reliable way to know whether another session is concurrently touching the same checkout, so "check first, then decide whether to isolate" is exactly the gap that let the first two incidents happen at all. A short-lived session doing a single quick edit is not exempt — a `git status`/`git add`/`git commit` sequence that overlaps a concurrent session's own in-flight commit takes seconds, not minutes, to cause damage.

Three incidents on record, escalating in severity, all on this same shared main checkout:
1. Two genuinely separate sessions (UI work, research experiments) had their git staging briefly interleave — one session's uncommitted edits or in-flight commit sequence became visible to, and momentarily reordered by, the other. Caught and separated cleanly before anything was lost, via careful `git status`/`git diff --cached`/`git reflog` checks before and after each commit.
2. A second, similar interleave, same shared checkout, same pattern — caught the same way.
3. **A commit landed on `main` containing literal, unresolved git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) inside `CLAUDE.md` itself** (commit `2e77cdd`) — a concurrent session's own merge/rebase resolution was never actually completed before it committed, and nothing caught it before the commit reached `main`. Found by a different session doing unrelated work, not by any automated check (there is no test that scans committed prose files for conflict markers), and fixed as its own dedicated follow-up commit (`f14bf2f`), verified via `git diff HEAD` to confirm only the three marker lines changed. Unlike incidents 1-2, this one shipped to `main` before being caught — the closest this project has come to a real, silent corruption of its own standing documentation from concurrent-session interference alone.

The first two incidents were caught by careful manual `git` hygiene in the moment; the third was caught by luck (an unrelated session happening to read the affected section). Relying on catching this after the fact — three times now — is avoidable entirely with separate worktrees from the start, which give each session its own working directory and index while still sharing the one underlying repository and object store. A repo-wide `git grep` for conflict markers across every tracked `.py`/`.md`/`.yaml`/`.yml` file was run 2026-08-09 following incident 3, to confirm no other file was silently carrying the same damage — clean, nothing else found — but this is a one-time sweep, not a standing check; a future session finding this section should re-run the same sweep if there's any reason to suspect another concurrent-session incident occurred since.

**There is exactly one implementation of any given trading decision — never two hand-written copies that are merely supposed to agree.** "Decision logic" means anything that determines *what should happen*: what qualifies for entry, how a position is sized, which candidates win when capacity is scarce, what triggers an exit and in what priority. It does not mean *execution mechanics* — live legitimately needs a real broker-API call (`Trading212Client`), retry/back-off, and DB bookkeeping that the backtest simulates instead; that split is fine and is not what this rule is about. Anywhere the *same decision* has to be made by both live order placement (`brokers/trading212.py`) and the backtest engine (`analysis/backtest.py`), both must call one shared function — never two independently-written expressions of the same rule.

**Why this is a hard rule, not a style preference.** In a single day (2026-08-01) three separate pieces of decision logic were found to have quietly drifted apart between live and backtest before anyone was looking for it — each discovered by accident, during unrelated work, not by any systematic check:

1. **Portfolio/deployment caps** — the backtest engine (`run_candidate()`/`simulate_trades()`) had no cross-ticker portfolio cap at all, while live enforced `max_open_positions`/`max_total_deployed` for real. Every backtest figure in this file up to that point was generated by an engine that never modelled the one constraint shaping live trade selection the most (see "Portfolio-capped backtest engine").
2. **Compounding** — live already sized positions against the account's real, current, compounding balance (`client.get_account_summary()["totalValue"]`, read fresh every call); the backtest's own portfolio-capped mode still sized against a fixed notional constant that never grew or shrank (see "Compounding portfolio backtest").
3. **Trade priority under scarce capacity** — both engines picked which candidate wins when capacity binds, but via an arbitrary, never-designed `(is_etf, config-order)` rule that nobody had actually decided was correct (see "Trade prioritization and capacity-allocation engine").

None of these three was caught by a test — the test suite at the time tested each side's *own* reproducibility (did a re-run match the last documented figure), never whether live's actual answer matched backtest's actual answer *for the same scenario*. That is precisely the gap this section closes.

**The mechanism — `analysis/decision.py`.** `evaluate_exit_reason()` (crisis > index-removal > stop-loss > signal-off > max-holding, the exact validated priority) and `passes_entry_gate()` (signal on AND currently a member AND not crisis-blocked) are the two decisions with the widest blast radius, and were found independently hand-coded as the same if/elif chain / boolean expression in **five** places before this task: `brokers/trading212.py`'s `maybe_exit_positions()`/`maybe_place_orders()`, `analysis/backtest.py`'s `simulate_trades()`/`simulate_portfolio()`/`simulate_portfolio_compounding()`, and `output/performance.py`'s `get_brief_cards()` (a display-only recomputation for the Daily Briefing/Performance page's position-status badge — no order is placed there, but a UI showing "ON TRACK" for a position the live system is actually about to close is its own real bug). All five now call the two functions in `analysis/decision.py` instead. Every input tolerates `None` ("unknown, don't trigger this check") rather than relying on Python's `not None == True` coercion — live and the backtest engine never pass `None` for any of these (both only ever call these functions with confirmed values), but `output/performance.py`'s display path genuinely can (a ticker with insufficient history, a stale API read), and a naive `not sig_on`-style extraction would have silently misclassified "unknown" as a definite trigger.

**What was reviewed and left duplicated, on purpose, with the reason recorded rather than silently accepted:**
- **Candidate priority ranking** (momentum descending, tie-break by insertion order) is expressed once in SQL (`brokers/trading212.py:_load_pending_orders()`: `ORDER BY admission_rank_value DESC, decided_at ASC` — **corrected 2026-08-10**; this read `momentum_pct DESC` until the 2026-08-09 admission-ranking promotion renamed the sort column, and this line was not updated alongside it, so it described the wrong column for a day) and once in Python (`analysis/backtest.py:simulate_portfolio_compounding()`'s `_admit_group()`: `sort(key=lambda c: (-c[1], idx))`). These cannot literally share one function — one runs as a database query, the other as an in-memory sort over a day's candidate list — so this is accepted, reviewed duplication of a simple rule, not overlooked duplication. Both sides are covered by their own tests asserting momentum-descending order (`tests/test_execution.py`, `tests/test_portfolio_backtest.py`).
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

## Capacity-turnover reporting — standing requirement (2026-08-06)

**Why this exists.** Confirmed real and material by the realistic-stop-fill correction itself, not a theoretical concern: making `STOP_LOSS` exits fire earlier (a change to WHEN positions close, not which ones open) raised total trade count from 3,161 to 3,436 — 275 MORE trades, not the same trades simply ending sooner. Freeing capacity earlier let the momentum-ranked, capacity-constrained admission system (`_admit_group()`'s skip-and-continue) admit candidates that would otherwise never have gotten a slot that day. This is a structural property of this system's capacity-constrained, compounding admission design, not a coincidence specific to the stop-fill fix — any change to exit timing/conditions, or to admission/entry conditions that affect trade frequency, has the same shape of effect: change WHEN or WHETHER a slot opens, and the whole downstream trade sequence for the rest of the run is different, not just shorter or longer.

**The rule.** Any investigation that changes exit timing/conditions, or admission/entry conditions that affect trade frequency, must report two things SEPARATELY, never blended into one headline number:
1. **The direct effect on the positions actually affected by the change** — e.g., for a tighter stop, what happened to the specific trades that now exit differently.
2. **The capacity-turnover effect** — how the change affects total trade count / distinct opportunities taken overall, and a reasonable accounting of what filled (or didn't fill) any freed or consumed capacity. A per-trade delta on the directly-affected population answers a genuinely different question than "what is this change actually worth" whenever capacity is shared and constrained — see history's "Realistic (intraday-touch) STOP_LOSS fill made the permanent default" for exactly this gap: the dividend/fill-convention audit's own first-order ~£212k estimate measured only the direct effect on already-identified trades; the true, fully re-simulated figure (£357,308.59, ~1.7x larger) only emerged once the capacity-turnover effect was measured too.

**Worked examples from tonight, for calibration — what this looks like done well.** The MA-extension-ceiling investigation's gate and re-ranking mechanisms both explicitly quantified contested-slot swaps (how many trades were removed vs. newly admitted, and how many dates saw a genuine same-day substitution where one candidate displaced another) rather than reporting only "N trades blocked." The stop-fill correction itself found and reported the trade-count increase (3,161→3,436) as a first-class part of its own headline comparison, not a footnote. Both should be read as the standard to match going forward, not a one-off nicety specific to those two investigations.

**Standing requirement for all future research work, not a note about tonight's incidents.** Any investigation touching exit timing or entry/admission frequency that reports only a direct-effect number, with no capacity-turnover accounting, should be treated as incomplete for that purpose until re-checked.

## A cost is safe to defer only while it's constant across the arms being compared (standing requirement, 2026-08-06)

**Why this exists.** FX conversion cost and idle-cash interest have been legitimately parked as "excluded, roughly offsetting, stated not modelled" across four separate reports in this project (the FX denomination audit itself, the 16-block benchmark comparison, and others) — correct every time, because in each of those comparisons every arm traded at essentially the same turnover and held essentially the same average cash balance, so the omitted cost was a constant that fell out of any *relative* comparison even though it biased every arm's own absolute figure. The deployment-cap sweep (`research/deployment_cap_sweep_2026-08-06`) broke that premise for the first time: trade count scaled directly with the variable under test (4,086 → 5,717 → 10,401 across 0.50/0.70/0.95 `max_total_deployed`), so FX cost — which scales with turnover — stopped being a constant and became a **treatment effect**. Deferring it there was not a simplification carried over from every prior report; it was a bias toward whichever arm traded more, discovered only because a reader who had not run the numbers correctly flagged that the "leg C reversal" headline finding looked too good relative to how it was earned. Modelled directly (real per-trade position sizes, real realised-deployment figures, the same T212 fee and ^IRX rate already established elsewhere), the entire reversal disappeared: cost-adjusted CAGR (5.62%/5.43%/6.23%) loses to every re-solved leg-C variant at every cap tested, including the 0.50 baseline, versus a frictionless headline (7.23%/8.33%/10.74%) that appeared to overtake leg C at the top of the range.

**The rule.** Before deferring any cost or credit as "excluded, stated not modelled" in a comparison between two or more arms, check explicitly whether that cost's own size depends on the variable being varied — trade frequency, position size, holding period, deployment level, anything the comparison itself changes. If it's genuinely constant across every arm (the common case — most single-variable tests hold turnover roughly fixed), deferring it is still correct and no extra work is needed. If it varies systematically with the arm — as it will for almost anything that changes turnover, deployment, or trade frequency — deferring it stops being a simplification and becomes a bias toward whichever arm the cost favours, and it must be modelled for that comparison specifically, not merely flagged with a qualitative "the offset weakens at higher X" sentence. A qualitative direction-only caveat is not sufficient once a reader would need the actual magnitude to know whether a headline conclusion survives — as it did not here.

**How to apply this going forward.** Any future single-variable sweep or comparison should ask this question explicitly, in the write-up itself, for every cost this project has ever deferred (FX conversion, idle-cash interest, and any other cost identified in the future): does this vary across the arms being compared? If yes, model it for those arms specifically, using real trade-level data where available rather than a proxy — this sweep's own `cost_adjustment.py` is a template (Σ(real position_gbp) × the real T212 fee, real realised-idle-% × the real mean ^IRX rate, both expressed as an annualised % of mean account balance and netted against the reported CAGR) for how to do this cheaply once full trade-level output already exists, which it should per this file's own "preserve full trade-level output" standing requirement above.

## Dual-provenance validation for any provisional-universe research (standing requirement, 2026-08-07)

**Why this exists.** The stop-loss reconfiguration study (`research/stop_loss_reconfiguration_2026-08-07/`) ran every arm against two pools deliberately — Pool 1 (`get_live_stock_candidates()`, survivorship-biased but zero confirmed corruption) and Pool 2 (`get_research_stock_candidates()`, the provisional post-quarantine universe) — specifically so the two could be checked against each other, not merged into one blended figure. That check surfaced a real cross-pool disagreement on peak-relative drawdown (Pool 2's raw numbers showed Arm B beating Arm A on drawdown; Pool 1 showed the opposite) precisely in the tail statistic (worst-episode drawdown), not the aggregate, where both pools' CAGR and block-pass rankings agreed throughout.

**Corrected 2026-08-08 — the disagreement is now understood as a structural property of the two pools, not a contamination artifact, and dual-pool validation's real job is surfacing that distinction, not "resolving" it toward agreement.** The first pass at explaining the disagreement (excluding the then-116-ticker `NEEDS_REVIEW`-adjacent-adjudicated `CLEAN` set from Pool 2) happened to flip the comparison back into agreement with Pool 1, and was reported as "the disagreement dissolves once contamination is excluded." That conclusion did not survive a second look: once the `CLEAN` population grew from 116 to 220 via a genuinely independent adjudication pass (see "NEEDS_REVIEW adjudication — Wikipedia cross-reference" below), the comparison **reversed back** to Pool 2's original direction (B beating A on drawdown again). The 116-ticker sample was too small and unrepresentative to have carried the "resolved" conclusion — a real error, recorded here rather than quietly corrected. **The working hypothesis now**: Pool 1 is today's real S&P 500 membership projected backwards — every ticker in it survived to today *by construction*, so it structurally cannot contain a 2008-09 crisis casualty. Pool 2 can, because it isn't restricted to survivors. A stop mechanism's drawdown behaviour genuinely differing between a universe where every name survived and one where some didn't is not contamination — it is exactly what survivorship bias predicts, and should be recorded as a known structural property of the two pools, not chased as an open defect to resolve by further data cleaning. What the second pass *did* correctly show: contamination's **magnitude** on this statistic shrank substantially as real evidence was applied (the exclusion's effect on drawdown fell from ~18.8pp to ~3.9pp as the `CLEAN` population grew) — most of what looked like a contamination effect in the first pass was actually genuine tickers being wrongly excluded, not genuine corruption.

**The rule, unchanged in substance, sharpened in what "resolving" a disagreement means.** Any research that uses `get_research_stock_candidates()` (or any other provisional/not-fully-adjudicated universe) for a finding that will inform a real decision must run the same comparison against a second, independently-provenanced pool, and report explicitly whether the two agree. If they disagree, **do not treat a single reconciliation attempt as closing the question** — a small or unrepresentative adjudicated subset can flip a comparison by chance, exactly as it did here, and a directional flip on a small sample is not evidence of resolution. Re-check with a larger or differently-sourced adjudicated set before reporting a disagreement as explained. If the disagreement persists across genuinely independent re-checks, consider whether it reflects a real structural difference between the pools (survivorship bias, universe size, era composition) rather than continuing to treat it as a data-quality defect — some cross-pool disagreements are the correct, honest answer, not a bug to be cleaned away.

**How to apply this going forward.** Treat "run it against a second pool" as a standard step for any provisional-universe research from this point on. Every figure produced from `get_research_stock_candidates()` alone, without this cross-check, should be read as carrying an unquantified tail-risk caveat until either the cross-check is run or the underlying `NEEDS_REVIEW` population is adjudicated down to a size where this class of inversion becomes implausible — and even then, a persistent disagreement should be investigated for a structural explanation, not assumed away.

## The 16-block pass rate has a measured resolution floor of roughly ±2 blocks under trivial pool changes — not a fine-grained discriminator (standing requirement, 2026-08-08)

**Why this exists.** Re-running the standard `get_research_stock_candidates()` baseline after promoting 104 tickers to `CLEAN` and 1 (`CPWR`) to `QUARANTINED` via the Wikipedia cross-reference (see "NEEDS_REVIEW adjudication" below) produced **7/16 blocks passed, against the long-established ~9/16 baseline** — investigated directly rather than accepted or re-run until a friendlier number appeared. The only pool difference from the last confirmed-9/16 run was exactly one ticker: `CPWR`, removed. `CPWR` itself had 18 trades, all in 2008-2011; the block that actually contains those trades (2008-03-23 → 2009-06-13) did not change pass/fail at all. **The two blocks that flipped (2013-02-14 → 2014-05-07 and 2014-05-07 → 2015-07-29) are years after `CPWR` last traded**, with materially different trade counts and profit factors from the prior run despite `CPWR` having zero trades in either block in the original run either. This is a genuine capacity-cascade effect, not a bug: `simulate_portfolio_compounding_mtm()` is a strictly sequential, capacity-constrained, compounding simulation, so removing one actively-churning 2008-2011 position changes exactly which capacity was available on every subsequent admission decision for the rest of the 19-year walk, and by 2013-2015 that changed admission history had propagated into a genuinely different trade sequence. Consistent with, not contradicting, this project's own repeatedly-documented sensitivity to exactly this kind of change (the realistic-stop-fill correction's +275 trades, the deployment-cap sweep's turnover sensitivity, and others) — but this is the first time the sensitivity was measured precisely enough to quantify as a **resolution floor**: one ticker, removed from a 1,043-ticker pool, moved the pass rate by two full blocks (12.5 percentage points).

**The rule.** Any finding whose conclusion rests on a 16-block pass-rate delta of ±2 blocks or fewer is inside this measured noise floor and must not be treated as a discriminating result on its own — it needs a different or additional criterion (CAGR, cost-adjusted CAGR, drawdown, a larger delta) to be trustworthy. This is not hypothetical: **the earnings-dampener recommendation's entire basis at `max_total_deployed=0.70`** ("+2 blocks", `9/16` vs baseline `7/16` — see CLAUDE_HISTORY.md's "Earnings-date size dampener" entry) **sits exactly inside this noise floor**, discovered after the fact, not before — that finding is not retracted here (a ±2-block delta is not proof of nothing, just not proof on its own), but it should be re-read as weaker evidence than it was originally presented as, pending a larger-delta or CAGR-based re-confirmation. The stop-loss reconfiguration study's headline result is **not** undermined by this — B beating A on cost-adjusted CAGR in both pools rests on turnover and FX cost, not on block counts — but any of that study's own sub-claims that leaned on a small block-count delta specifically should be read with the same caution.

**How to apply this going forward.** (1) Any future finding reporting a block-pass delta should state the delta size next to the claim, and treat anything ≤2 blocks as suggestive, not conclusive, pending a corroborating metric. (2) **When adjudicating a `NEEDS_REVIEW`-style population, do not re-run the standard block validation after each individual ticker's status change** — batch every change first, then validate once. Re-validating one ticker at a time against this specific metric will generate cascade-driven block deltas of exactly the size that looks like a real signal but is actually pool-perturbation noise, and the temptation to interpret each one will waste real time chasing an artifact of the mechanism documented here, not of the ticker being adjudicated. (3) A block-pass number that moves for an *understood, benign* reason (like this one) should be re-baselined to the new figure, not carried forward as if the old number were still the reference point — but the reason it moved must be recorded alongside the new baseline, so a future reader does not mistake a benign, explained shift for a regression.

**Worked example — the same delta, opposite economic consequence (2026-08-08).** Closing a gap left by the `CPWR` re-baseline above (only the 0.70-cap sanity gate had been re-run), the identical single-ticker removal was checked at `max_total_deployed=0.50` too. It moved by the same **±2 blocks** (11/16 → 9/16) as the already-documented 0.70-cap move (9/16 → 7/16) — but total P&L moved in the **opposite direction**: 0.50-cap P&L *rose* 38.3% (£52,841.47 → £73,086.75) while 0.70-cap P&L *fell* 1.2% (£116,748.91 → £115,363.15). Same cause, same block-count magnitude, opposite sign on the number that actually matters. This is the clearest available demonstration that a block-pass delta carries no reliable sign, let alone magnitude, on its own — treat this pair as the reference example when explaining the rule above, not just the abstract statement of it. See `research/production_closeout_revalidation_2026-08-08/REPORT.md` for the full capacity-turnover breakdown (removing one ticker's 18 trades reshuffled ~35-38% of all trades via cascade at both caps).

## Capacity-cascade amplification inflates any measured difference between two backtest arms — standing note (2026-08-08)

**Why this exists.** Three independent measurements, on three unrelated questions, have now shown the same shape of effect: a small, targeted change to one part of a `simulate_portfolio_compounding_mtm()` candidate pool or admission ranking produces a *much* larger change in the resulting trade sequence than the size of the original change would suggest, because momentum-ranked, capacity-constrained sequential admission propagates any local change into a genuinely different admission history for the rest of the run.

1. **The `CPWR` block flip** (see "16-block pass rate has a measured resolution floor" above) — removing one ticker's 18 trades (out of thousands) flipped 2 of 16 blocks, years after that ticker last traded.
2. **The cap-0.50/0.70 P&L-direction reversal** (worked example, same section above) — the identical single-ticker removal, checked at two different deployment caps, produced the *same* ±2-block delta but *opposite-signed* P&L consequences, and a direct trade-by-trade diff showed **~35-38% of all trades changed identity** (removed or newly admitted) from removing just one ticker's 18 trades.
3. **The adj_close-vs-close admission overlap** (`research/adj_close_momentum_2026-08-08/REPORT.md`) — two arms differing only in which price series feeds the momentum signal shared only ~33% of their admitted positions (Jaccard ~20%). Tracing the difference showed most of it was capacity-cascade reshuffling (a ticker's ranking shifted because *some other* ticker's ranking changed, not because its own signal changed), not a direct per-trade consequence of the signal change being tested.
4. **The stop-loss study's own 1.94pp headline, checked against a capacity-free instrument, turned out to be substantially inflated** (`research/measurement_toolkit_2026-08-08/REPORT.md`, validation (b)) — see that section below for the full result.

**The rule.** A raw trade-level diff between two backtest arms (which positions are shared, which are unique to one arm) systematically overstates how different the two arms' *decision logic* actually is, because most of the divergence is cascade amplification of a smaller number of direct differences, not direct differences themselves. Do not read a low overlap percentage (or a large "trades changed" count) as itself evidence that an effect is large — check whether the *aggregate* metric (CAGR, cost-adjusted CAGR, P&L) moved by a comparable amount; if a large trade-level diff coexists with a small or inconsistent aggregate effect (as in observation 3 above — 33% overlap, but a cost-adjusted CAGR gap that failed to clear a modest pre-registered floor), the correct read is "cascade-amplified, not decision-different," not "materially different strategies."

**A reusable measurement toolkit now exists specifically to test small effects without this contamination — `research/measurement_toolkit_2026-08-08/`, built and validated 2026-08-08.** Four instruments, importable as plain functions from that directory (not part of `analysis/`, since these are research-only diagnostics, never called from any production path):
- **Empirical noise floor** (`step1_build_full_history_cache.py` + `step2_run_perturbation.py` + `step2b_aggregate_noise_floor.py`) — runs the standard baseline against itself with trivial perturbations (single-ticker drops, walk-start-date shifts, admission tie-break reordering) and reports the actual distribution of cost-adjusted CAGR/drawdown/trade-count/block-pass-count. First real measurement: 17 draws, cost-adjusted CAGR range 1.08pp but bimodal — 16/17 within ±0.13pp, one active-ticker drop responsible for the entire range. Block-pass range 3 (6-9), reinforcing rather than merely repeating the resolution-floor note above.
- **Paired per-decision comparison** (`step5_paired_comparison.py`) — `paired_comparison()` pairs two trades files on `(ticker, entry_date)`; a shared key is capacity-uncontaminated by construction, so the outcome comparison for that subset carries zero cascade effect regardless of what else differs. `disagreement_population_comparison()` handles the case where pairing by shared key isn't the right tool (an entry-gate difference, not an exit-timing difference) — compares the outcome *distribution* of positions unique to each arm instead. Modelled on the stop-loss study's own whipsaw analysis.
- **Uncapped diagnostic arm** (`step3_uncapped_arm.py`) — calls the real, unmodified `simulate_portfolio_compounding_mtm()` with `max_deployed_pct` and `absolute_max_position_gbp` driven to effectively-infinite via a copied config dict (never a code change, never reachable from production) so admission becomes deterministic given the signal and cascade disappears. The real cash constraint (`resolve_cash_constrained_position_gbp()`) is deliberately left untouched — a genuine economic limit, not an arbitrary ceiling.
- **Benchmark-relative reporting** (`step4_benchmark_relative.py`) — excess return over `^SP500TR` (S&P 500 Total Return, dividends reinvested, coverage confirmed 2006-01-03 through 2026-08-07) on the identical dates a strategy run covers, both continuous-walk and per-block. Built and sanity-checked (cost-adjusted baseline trails `^SP500TR` by −7.75pp CAGR, beats it in 5/16 blocks) but not yet wired into every report — available for reuse.

Validated against two known results before being trusted: adj_close (agreed with the portfolio-level "no decisive winner" — the disaggregated per-decision comparison showed near-identical outcomes) and the stop-loss study (did **not** agree with the portfolio-level magnitude — see below). Use this toolkit, not a raw trade-level diff, for any future small-effect comparison.

**How to apply this going forward.** Any research comparing two arms should report admission/trade overlap (shared positions as % of each arm, Jaccard similarity) alongside the aggregate metrics, not as a substitute for them — the overlap number contextualizes *how* the arms differ (direct vs. cascade), the aggregate numbers say *whether it matters*. Treat a low overlap number as a flag to look closer at mechanism (as the parity spot-check in the adj_close study did — tracing individual differing trades to either "higher priority value" or "cascade, unchanged priority value"), not as a headline finding on its own.

## Descriptive selection audit — what the system's admission mechanism actually did, and whether momentum ranking is real (2026-08-08)

**Not a hypothesis test, nothing adopted — a read-only description of the real trade history, run specifically because every prior investigation in this file tests a *variant*, and none had directly asked "is the ranking mechanism itself doing anything."** Full report: `research/selection_audit_2026-08-08/REPORT.md`. Two independently-provenanced pools throughout (Pool 1 = `get_live_stock_candidates()`, 503 tickers; Pool 2-final = `get_research_stock_candidates()`, 1,042 tickers, current 163-quarantined state) — both agreed directionally on every finding below. New instrumentation (`instrumented_engine.py`, an additive-logging copy of `simulate_portfolio_compounding_mtm()`) was parity-verified byte-identical against both pools' already-published trade files before being trusted, and a counterfactual single-position simulator was verified against 30 real admitted trades (30/30 exact match) before being used for the rejected/gate-failed population analysis.

**Headline finding — momentum ranking is real, not decoration, but it trades risk for return.** The task this audit was built to answer directly: does momentum-*ranked* admission beat *random* admission from the identical gate-passed, capacity-constrained pool? Arm R (100 seeds, real production engine, only the admission sort replaced with a seeded shuffle of the same eligible set) puts the real baseline at the **99th percentile of the full 100-run distribution on both frictionless CAGR (6.63% vs. a 1.50–6.79% random range) and cost-adjusted CAGR (3.27% vs. −2.02% to 3.37%)** — only 1 of 100 random runs matched or beat it. **This is not true of drawdown**: the baseline's −54.02% max drawdown is worse than 98 of the 100 random runs (which cluster around −37% to −47%). Ranking concentrates capacity into higher-conviction, more correlated bets — real extra return, real extra drawdown, not a free improvement. The random arm's own spread is also a second empirical noise-floor measurement, independent of the perturbation-based one in the measurement-toolkit section above (CAGR range 5.3pp, drawdown range 23.8pp across 100 seeds from ranking-order alone).

**P&L is concentrated in a small number of trades — stated so any single-number headline is read with this in mind.** Top decile of trades (by P&L) = **257% of total P&L**; the bottom half of all 3,436 trades is net negative in aggregate. Median trade P&L is negative (−£50.29) despite a positive mean (+£37.01) — right-skewed, as expected for a stop-loss/trend system, but a reminder that "the strategy's edge" is substantially a claim about a minority of trades, not a broad base.

**Raw entry momentum barely predicts trade outcome.** Spearman(momentum-at-entry, pnl_pct) across 3,436 trades = **−0.033** — indistinguishable from zero. The one real, consistent signal is at the bottom: the lowest-momentum qualifying decile is reliably the worst performer. Above that, no monotonic relationship holds — the ranking mechanism's own assumption (higher momentum = better) is not supported by the trade-level data outside the bottom margin. **Rank-*within-day*, a different and more conditioned measure, does carry real signal** (top-quartile-at-admission trades outperform below-top-quartile admits in both pools) — reconciled with the headline finding above: relative rank against that day's specific eligible set predicts better than the raw momentum value does.

**Admission is overwhelmingly timing-driven, not a deep queue of alternatives.** **92–93% of every admission, in both pools, only happened because that specific day's own position exits freed the capacity slot it filled.** Only 7–8% of admitted trades were outside the top quartile of that day's own eligible candidates, so the ranking is doing real, active filtering — but which *specific* good candidate wins a slot is dominated by which day a slot happens to open. Capacity-rejected candidates are not obviously worse than admitted ones (a sampled counterfactual: rejected-population win rate 43.9–44.7% vs. admitted's own 43.1–44.4%, mean return modestly lower) — capacity constraints bind on a population that looks close to as good, on average, as what gets through.

**Two genuine anomalies flagged, not resolved.** (1) The small minority of admissions that did *not* need freed capacity underperform, by a wide margin, the 93% that did (win rate 28.5–32.6% vs. 44.2–45.3% in both pools) — counterintuitive, not explained here. (2) The most-negative-momentum decile of gate-*failed* candidates shows the highest or near-highest win rate of any bucket in this entire audit (55.2–56.8%, both pools, exceeding the admitted population's own 43–44%) — substantially, not fully, explained by a much shorter counterfactual holding period (dominated by fast `SIGNAL_OFF` exits, consistent with short-horizon mean reversion rather than a real multi-week trend). Both held up identically across both independently-built pools, ruling out a pool-specific data artifact.

**Sector tilt is real and structural, not a bug.** Technology and Consumer Cyclical are 1.5–2.9x overweight vs. either candidate pool's own composition; Financial Services, Utilities, and Real Estate are 0.4–0.75x underweight — an expected mechanical consequence of momentum ranking naturally favouring higher-beta sectors, but a genuine, measurable concentration risk.

**The strategy trails broad-index benchmarks on raw CAGR over the full 2007-2026 window, with a milder drawdown.** Against `^SP500TR` (genuine total return, 11.13% CAGR), Nasdaq Composite (price-only, 12.95%), and dividend-adjusted MDY/IJR proxies for MidCap 400/SmallCap 600 (9.62%/9.44%), the strategy's own 7.05% trails all four — winning only 2-7 of 16 blocks depending on the index — while showing a milder max drawdown (−50.38% vs. −55% to −58% for the four benchmarks). Separately, single-instrument momentum tested on SPY/QQQ/MDY/IJR shows no monotonic relationship between universe breadth/volatility and momentum quality (QQQ best on profit factor, IJR — the broadest/smallest-cap — worst), arguing against "just trade a broader universe" as an obvious fix for the CAGR gap.

## Data-quality backstop was silently broken since deploy; identity check had a scoping bug — both fixed (2026-08-08)

**Found as a detour from the admission-redesign pre-registration below** (see
`research/admission_redesign_prereg_2026-08-08/REPORT.md`'s own "Detour" section for the full
narrative) — its sanity gate failed on one extra ticker (`VTRS`), which traced to a genuine,
confirmed production defect, not a bug in that study.

**Scale, confirmed not assumed.** `price_data` tickers with zero row in `ticker_data_quality`:
**463**, identical on both the local research DB and production via direct SSH query — including
`AAPL`/`MSFT`/`GOOGL`/`AMZN`/`TSLA`/`NVDA` and every ETF. The 408-ticker population documented
above (220/163/25) was only ever the 2026-08-07 remediation's own newly-backfilled batch — the
~540-ticker population that predates that remediation (every megacap, every ETF, every reference
ticker) was never screened by anything at all, an assumption ("already live-traded, so probably
fine") that was never actually verified until now.

**Root cause of the backstop never closing this gap, confirmed via `journalctl`, not inferred.**
`scan_unverified_tickers()` (deployed 2026-08-07 specifically to be this backstop) never logged
either success or failure once in a full month of retained production logs, across both EOD cycles
since deploy (2026-08-06, 2026-08-07). At `rate_limit_seconds=2.5`, a 463-ticker backlog is a
~19-minute unbroken yfinance loop inside the EOD job — long enough not to survive whatever causes
this service's own frequent restarts, most likely killed by signal rather than raising a catchable
exception (consistent with zero warnings logged despite `logger.warning` demonstrably firing
elsewhere in the same job, e.g. the instrument-resolution-failure warning).

**A second, independent bug surfaced the moment the backlog was actually run for the first time.**
`verify_ticker_identity()`'s Check A can only ever affirm `quote_type == "EQUITY"` — an ETF, an FX
pair, or an index-level series fails it by construction, not as a corner case handled
conservatively. `scan_unverified_tickers()`'s SQL had no stock restriction, so its first successful
run wrote `QUARANTINED` for **`^GSPC` — the crisis-trigger reference series itself** — and ~33 ETF
tickers. Inert today only because nothing currently reads quarantine status on the reference or ETF
paths; that is a property of today's callers, not of the data, which is why the fix belongs in the
scan's own scope, not in every future caller having to route around it. Separately, `IAC` exposed a
real logic bug in `verify_ticker_identity()` itself: a gracefully-handled yfinance 404 (empty dict,
no raised exception) was scored identically to a real, populated, confirmed-different-identity
response and auto-quarantined on zero evidence.

**Three fixes shipped to `scan_unverified_tickers()`** (`data/ingestion.py`, `data/scheduler.py`,
commit `0a170ca`, 953 tests passing, including new regression coverage for every case below):
(1) scoped to `analysis.universe.get_all_known_stock_tickers()` only; (2) chunked at 50
tickers/call (~2min at the rate limit), self-resuming with no new progress table needed (a screened
ticker drops out of the next call's own unscreened-ticker query); (3) explicit completion logging
on both the success path (even `checked=0` now logs) and the failure path
(`details["data_quality_scan_error"]` now populated in the EOD completion record, previously
silently absent either way — this was the actual reason the original failure went undetected for
two full days). `verify_ticker_identity()`'s empty-response case now routes to `NEEDS_REVIEW`,
narrowly — confirmed directly, not assumed, that the already-validated `PALM`/`PDG`/`RX`
populated-but-degenerate-response cases (a real, informative `quote_type` even with a placeholder
exchange/name) still quarantine unchanged.

**Applied and independently re-verified, local and production, both matching exactly.** 36
non-stock rows deleted, `IAC` re-screened, the remaining 427 previously-unscreened stock tickers
screened under the fixed code (427 = 463 − 36, confirmed identical on both databases: 25
quarantined + 10 needs-review + 392 clean in both). **Final counts, current as of 2026-08-08,
confirmed via direct SQL query on both databases, not either script's own summary: 612 CLEAN / 35
NEEDS_REVIEW / 188 QUARANTINED / 835 total, zero non-stock rows remaining.** (This supersedes the
220/163/25/408 figure above for "current total state" purposes — that figure remains an accurate
historical record of the 2026-08-07 remediation batch specifically, not of the full table.) The
FNMA-pattern override checked directly against real cases, not assumed: `SGP` (SpyGlass Pharma, a
real *different equity*) → `NEEDS_REVIEW`; `MER`/`CAM`/`PCL`/`SPLS` (all resolve to real but
*non-equity* replacement products — a fund or ETF — genuine recycled-symbol cases, not the FNMA
pattern) → correctly `QUARANTINED`.

**Not yet closed, stated plainly rather than assumed fixed.** The *scheduled* path (the EOD job's
own chunked call, on the real scheduler, under real restart pressure) has not been observed
completing successfully even once — today's application was a manual SSH invocation, proving the
code is correct, not that it survives a real EOD cycle. 2026-08-08 is a Saturday; no EOD job fires
regardless of any of this. **Monday 2026-08-10's real EOD completion record is the first
opportunity to check for a populated `data_quality_checked` key** (or, if it fails again,
`data_quality_scan_error` — now populated either way, which is itself the point of fix #3). Treat
this backstop as unverified-in-production until that check is done, not as fixed-and-forgotten.

**Meta-lessons, of standing relevance beyond this incident:**
1. **A P&L reference figure carries an implicit database-state dependency that continuous
   ingestion makes decay.** An exact-total sanity gate will eventually fail for a benign reason
   (new data legitimately arrived), and that failure is not distinguishable from a real bug without
   a trade-set diff, not just a total. This surfaced the whole incident above — worth designing
   future gates to compare trade sets/counts alongside any single total from the start, or to pin
   references to a snapshot explicitly.
2. **A backstop that has not been *observed* completing successfully isn't a backstop**, no matter
   how correct its own code looks on inspection. This one had a clean design, a clear docstring, and
   its own try/except isolation — and still silently never ran to completion across every
   opportunity it had, for two full days, with nothing anywhere flagging the gap until an unrelated
   sanity gate happened to trip over one of its symptoms.
3. **A check's validity domain must match its actual input population, not the population it was
   originally designed and validated against.** `verify_ticker_identity()` was correct for the
   stock-recycled-symbol problem it was built for; feeding it every ticker in `price_data` wasn't a
   conservative extension, it was applying a domain-specific test outside its domain, and it
   silently mis-scored the crisis-trigger series itself the first time it got the chance to run.

## Monthly-shortlist admission redesign — pre-registered, tested, NOT adopted (2026-08-08)

**Motivated directly by the selection audit above** (Part 7: ranking beats random at the 99th
percentile; Part 4: 92-93% of admissions only happen because that day's own exits freed capacity).
The arm: top-50-by-momentum shortlist, monthly rebalance (entries only — exits stayed continuous,
unchanged), no forced slot-filling beyond the shortlist, freed capital waits for the next rebalance.
Full pre-registration and results: `research/admission_redesign_prereg_2026-08-08/PREREGISTRATION.md`
/ `REPORT.md`.

**On the surface it looked like a clear win**: peak-relative drawdown roughly halved in both pools
(Pool 2 CLEAN: −46.28% → −22.82%; Pool 1: −41.94% → −20.56%), and the pre-registered deciding metric
(cost-adjusted CAGR ÷ |drawdown|) improved 1.5–2.4x. **It failed anyway, on two of the five
pre-registered PASS conditions, either one alone sufficient per the walk-away sentence written
before any run:** mean deployment fell ~24 percentage points below baseline in both pools (past the
15pp floor — the redesign's smoother equity curve is substantially explained by holding much more
cash on average, not better selection), and the month-level paired per-decision instrument (built
specifically because exact-date pairing wasn't usable here, per the pre-registration's own §4)
showed a **coin flip — 50.1–50.2% win rate, both pools** — when both arms independently held the
same ticker around the same time, the redesign's own realized outcome was not better than
baseline's. This is the identical shape of finding the measurement-toolkit's stop A/B validation
already produced the same day: a large, attractive portfolio-level gap that does not survive
contact with a capacity/cascade-uncontaminated, per-decision view.

**Not a wasted result.** The pre-registration was built specifically to catch this shape of false
positive before it could tempt adoption, and it did — cleanly, on criteria fixed in advance, not by
post-hoc argument about which of several attractive numbers to trust. Selective admission (holding
a slot empty when nothing clears a quality bar, deliberately out of scope for this arm per its own
§0) remains a live, different, and now better-specified question — its own pre-registration would
need to isolate deployment level from selection quality from the start, not inherit a clean bill of
health from this result.

## Derived state living in one place while work happens in another — a single generalisation, not three incidents (2026-08-08)

**The same failure shape hit three times in one day, each with a different surface.** (1) The
Wikipedia-adjudication commit wrote 105 `write_data_quality_status()` calls to the *local* research
database only; CLAUDE.md asserted the result as current fact for a full day before an audit caught
that production still held the pre-refinement state (see "A research script whose output is a
database mutation needs an explicit, separate production-apply step" above). (2) The admission-
redesign study's sanity gate failed on a stray `VTRS` trade, tracing to a P&L reference figure
computed against a database snapshot from a since-deleted worktree — the figure decayed the moment
ingestion moved on, with nothing to signal that it had (see "Data-quality backstop" above, meta-
lesson 1). (3) The very next study's setup found the *main checkout's own* local database was still
on the pre-fix state (220/25/163) — the correction from (2)'s own fix had landed in a since-deleted
worktree's copy and on production, never back into the checkout every future worktree gets created
from.

**The generalisation**: any derived or mutable state — a database, a quality/status table, a
computed reference figure, a cache — can exist in more than one independently-evolving copy at once
(a worktree, production, a research artifact, the main checkout), and fixing or updating one copy
does nothing to the others by default. "I already fixed this" is a claim about *whichever copy the
fix actually ran against*, never a claim about every other copy that happens to share the same name
or nominally represent "the same" state. This is not a database-specific lesson — it is the same
shape as the webhook-vs-native-deploy duplication this project already removed structurally (see
"GitHub Actions as the real test gate" below) and the five independent live/backtest decision-logic
divergences documented earlier still ("There is exactly one implementation of any given trading
decision") — anywhere the same fact needs to be true in two places, it will silently stop being true
in one of them eventually, and "eventually" has so far meant "same day, three times."

**The rule.** Before trusting any derived state — a quality table's current counts, a P&L reference
figure, a cache, a database file — in a new worktree, a new session, or after any fix, **query it
directly, fresh, in the place it's about to be used**, rather than inferring it from "I fixed this
earlier," "this is the main copy," or "this matches what I last saw." A new worktree's own copy of
`database/investment_research.db` is not guaranteed current just because *a* copy somewhere was
fixed — confirm the specific file this session will actually read from.

## Gate-width (momentum shortlist) study — pre-registered, tested, NOT adopted (2026-08-08/09)

**Isolated one variable the admission-redesign study above couldn't**: restricting daily candidacy
to the top N by momentum among gate-passers (N ∈ {100, 50, 25} vs. the current ~330-candidate gate),
with entries staying on the continuous daily clock throughout — explicitly not a rebalancing test.
Full pre-registration and results: `research/gatewidth_prereg_2026-08-08/PREREGISTRATION.md` /
`REPORT.md`.

**An interior optimum does appear on the decisive pool (Pool 2 CLEAN) — cost-adjusted CAGR rises
from 3.395% (baseline) to a peak of 4.043% at Top-50, then falls back to 3.418% at Top-25 — but the
largest gap (+0.65pp) sits inside the measured ~1.08pp noise floor and never reaches the
pre-registered +1.5pp bar.** Not adopted on that basis alone. Two further findings, both surfaced by
instruments built specifically to catch a false positive here: **the deployment guard passed
cleanly** (mean deployment moves ≤0.5pp across every arm in both pools — gate width, unlike monthly
batching, genuinely does not change deployment behaviour, confirming the mechanism this study
targeted). **The pre-registered overlap prediction was wrong, and reported as such rather than
smoothed over**: exact `(ticker, entry_date)` overlap between baseline and every shortlist arm was
predicted high (per Part 4's timing-driven-admission finding) but measured 40.6–44.8% — explained on
investigation as capacity-cascade amplification (the same mechanism behind `CPWR`'s two-block flip
and the adj_close study's own low overlap), not a sign the shortlist changes admission timing in a
way that breaks the deployment-guard finding, which was independently confirmed via the panel data
directly, not inferred from the overlap number.

**The one arm that looked most interesting in isolation disagreed sharply between pools — read as a
reason for caution, not resolved.** Top-100 shows +0.51pp on Pool 2 CLEAN but **−2.10pp on Pool 1**
— opposite sign, both outside noise on their own pool. Per this project's dual-provenance standing
requirement, reported as a genuine, unreconciled cross-pool disagreement (a plausible but unconfirmed
hypothesis: Pool 1's maximal survivorship bias makes it a structurally different universe at rank
100, not merely a smaller one) rather than picking whichever pool told the more convenient story.

**Same-day repeat of the derived-state lesson above, twice more.** The sanity gate against this
morning's already-fresh reference found two small (~0.3%), fully row-diagnosed discrepancies in
both pools — the same 7 `END_OF_DATA`-exit trades revised by a boundary-date price update, plus (Pool
2 CLEAN) `VTRS`'s own admission outcome shifting with one more day of data. Neither is a bug; both
are the same "a reference figure decays the moment ingestion moves on" phenomenon, caught by
comparing trade sets rather than totals, per the standing rule two sections above — now confirmed
live, same day, a second time.

## Sleeve-allocation (equity/defensive) study — pre-registered, feasibility-checked, SHELVED without running (2026-08-09)

**A two-sleeve design (equity momentum + a defensive sleeve of Treasuries/defensive sectors, binary
switched on `^GSPC` 12-month momentum) was pre-registered, approved with required edits, then parked
mid-way through its own required pre-run step — confirming T212 tradeability of the five proposed
instruments (SHY, TLT, XLU, XLP, XLV). Resuming that check found two separate, each independently
sufficient, reasons to shelve the study without ever running an arm.** Full detail:
`research/sleeve_allocation_prereg_2026-08-08/INSTRUMENT_FEASIBILITY_REPORT.md` and that
directory's `PREREGISTRATION.md` (closure verdict appended, not merged into a new engine or config).

**T212 (UK/EU retail) cannot offer the US-domiciled originals directly — no PRIIPs KID — so every
proposed instrument needed a UCITS equivalent, resolved from a real, live `get_instruments()` pull
(cached, not guessed), using a name-keyword search rather than `resolve_instrument_candidate()`
(that function's `{TICKER}_US_EQ`/shortName-match convention is stock-specific and returns zero
matches for any ETF — stated explicitly as the methodology substitution, per this project's own
standing practice of never silently assuming availability).** Genuine, sector-matched UCITS
equivalents were found for all five components — including, for the three sector legs, SPDR's own
UCITS wrapper of the identical Select Sector index each US original tracks — directly contradicting
the pre-registration's own stated worry that no clean sector equivalent might exist.

**Reason 1 — the 2008 block is untestable with real tradeable instruments, regardless of any other
finding.** The earliest date at which all five components have genuine history is **2015-07-07**,
bounded by the three sector legs (the sector-fund UCITS wrappers, both the SPDR and iShares
families, did not exist before 2015/2017). This is over six years after the project's own
2008-03-23→2009-06-13 crisis block ends. A defensive sleeve's entire rationale is protection against
exactly this kind of event; a study restricted to 2015-onward would only ever see one genuine equity
drawdown (2022) and one fast V-shaped one (2020) — neither resembling the scenario the sleeve exists
for. This is a fatal, not merely reduced-power, problem for this design.

**Reason 2 — the Acc/Dist accounting mismatch on the sector legs is not confined to the untested
window, and is larger than the effect this study was measuring for.** No sector-matched,
USD-denominated, **distributing** UCITS equivalent of XLU/XLP/XLV exists on T212 — only accumulating
share classes (one Dist alternative exists for Health Care only, at the cost of ~5 more years of
history). Measured against the real US series over the full available overlap (2015-07-07→2026,
11.1 years): **+2.86 pp/yr (XLU), +2.53 pp/yr (XLP), +1.42 pp/yr (XLV)** CAGR divergence, compounding
to **35–69% cumulative divergence**. Backtesting the US (Dist) series while the actual held
instrument is Acc means simulated and achievable returns would differ by more than the margin needed
to declare a win — a measurement error larger than the signal being tested, not a stated,
acceptable-to-defer limitation in the sense this project's cost-deferral standard (see "A cost is
safe to defer only while it's constant across the arms being compared" above) allows.

**Combined with priors already on record** (the structurally similar gate-width study's Phase 2:
every variant underperformed on every pool; this design's plausible upside was always bounded by
"Treasury yield during defensive periods minus mistiming cost"), the study was shelved rather than
run in a degraded (2015-onward, accounting-biased) form. The idea itself is not judged wrong — the
instruments actually available to hold it don't support testing it honestly.

**Two things salvaged.** (1) **A new price-data artifact for the catalogue**: the GBP-denominated
UCITS share class proposed for the SHY leg (`IBTSl_EQ`, ISIN `IE00B14X4S71`, LSE) has an unexplained
**~145x price cliff on 2009-01-02** — confirmed a genuine data artifact, not a real market move (the
US original, `SHY`, has zero comparable breaks anywhere in its history; the USD-denominated share
class of the *same* underlying fund, `IBTSs_EQ`, has none either). Root cause not identified
(currency-redenomination or ticker-reuse are both plausible, consistent with this project's own
already-documented recycled-symbol corruption class — see "Recycled-ticker-symbol corruption"
above — but this is a UCITS/LSE instrument, outside that mechanism's stock-only scope, and was not
investigated further since it was never going to be used). Recorded here so this ISIN's GBP share
class isn't trusted at face value if ever revisited. (2) **The underlying problem this study was
trying to solve — idle cash between equity positions earns nothing — remains real and unaddressed.**
The cheapest honest fix isn't a sleeve: it's parking flat capital in a single short-duration
instrument. SHY's UCITS equivalent, used correctly (`IBTSs_EQ`, the **USD** share class, not the
GBP one), supports this cleanly from **2009-01-05** with tight tracking (correlation 0.367, CAGR
diff +0.10 pp/yr, cumulative divergence +1.77 over 17.6 years) — one instrument, one accounting
treatment, no blend, no switching signal. Not pursued now; a candidate for a much smaller, separately
pre-registered study later.

No code changed on any live path. No study arm was ever run. No `price_data`/`ticker_data_quality`
writes occurred — all instrument price history for this feasibility check was fetched read-only into
a research-local cache.

## Cross-sectional momentum — pre-registered, tested, NOT adopted (2026-08-09)

**Tests a genuinely different strategy construction, not a parameter variant of the current
system** — motivated directly by the selection audit's Part 4 finding (rank-*within-day* predicts
trade outcome; raw time-series momentum does not) and Part 7 (momentum ranking beats random
admission decisively but concentrates drawdown). Jegadeesh-Titman cross-sectional momentum: stocks
ranked against *each other* on 12-minus-1-month return, monthly rebalance, top 40 held. Full
pre-registration and results: `research/cross_sectional_momentum_prereg_2026-08-09/PREREGISTRATION.md`
/ `REPORT.md`. Approved with three amendments before any run: a fourth arm (D — the existing
2%-momentum-floor layered on top of pure relative rank, to measure rather than argue whether the
floor's known defensive value transfers to this construction); the new `RANK_DROPPED` exit reason
placed inside `analysis/decision.py` as a pure function (`evaluate_rank_dropped_exit()`) ahead of any
live caller, per this project's "one implementation of any trading decision" invariant, rather than
left in research code; and a staleness-guard requirement on the new ranking computation, mirroring
`analysis/signals.py`'s `_MAX_ANCHOR_GAP_DAYS` for the identical failure mode.

**Verdict: NOT ADOPTED — all three arms (B: 30-day max-holding; C: unlimited; D: unlimited + floor)
fail decisively, in both pools, on the primary criterion alone.** Excess return over `^SP500TR`
missed the pre-registered +2.5pp-over-baseline bar by −0.19pp to −9.83pp across all six
arm/pool combinations — not close calls inside any measured noise floor. Peak-relative drawdown
improved for every arm (smaller than baseline's, never the binding constraint), which is itself the
tell: the deployment guard failed badly for B (mean deployment 22.5–23.0pp below baseline) and C
(50–51pp below) — capital freed by a mid-month exit sits idle until the next monthly rebalance, the
identical wait-for-rebalance mechanism the admission-redesign study's own deployment guard caught,
now reproduced independently in a categorically different strategy construction. C/D spend roughly
87% of the 19-year window out of the market entirely.

**A genuine tension surfaced and resolved, not glossed over**: the primary per-decision
corroboration instrument (month-level pairing, 3,000–4,800 shared ticker-months per comparison) shows
cross-sectional's own realized trades winning 52–66% of the time against baseline's on the same
ticker/month — nominally a portfolio-level-vs-per-decision disagreement, exactly the shape the
walk-away sentence was written to catch. Not a rescue: the instrument only measures outcomes
*conditional on both arms independently choosing to hold the same name* and says nothing about the
40–87% of the time the cross-sectional arm sat in cash instead — a highly selective portfolio's
realized trades outperforming per-trade is fully compatible with, and the more parsimonious
explanation for, a portfolio-level return that trails once near-permanent underinvestment is priced
in. The two instruments answer different questions; the disagreement diagnoses *why* the arms fail
rather than arguing against the failure.

**Arm D's floor confirmed doing exactly what it was built to do, without closing the gap.** Held-name
count at each of 236 rebalance dates: median 40 (floor essentially inert in a typical month), but
genuinely flat (zero held) at 15 dates (6.4%), concentrated in broad-decline periods — the mechanism
a "top-40 always" construction structurally cannot express. Real, measured risk reduction (D's
drawdown modestly better than C's) but excess-return gap nearly identical to C's, since the floor
only ever removes candidates from an already-underfilled slice — it never touches the wait-for-
rebalance idle-capital mechanism that is the actual cause of the failure.

**Standing observation for future redesigns, recorded because this is the second time this exact
shape has appeared independently.** Both this study and the admission-redesign study (monthly
shortlist rebalancing within the *existing* time-series construction) found their headline
portfolio-level result reduced almost entirely to a deployment-level artifact once checked against a
capacity/cascade-uncontaminated per-decision instrument — different strategies, identical failure
mechanism. Any future redesign that gates entries to a periodic rebalance grid, on this account's
existing capacity/sizing machinery, should budget for this failure mode from the design stage (e.g.
holding mean deployment parity as an explicit constraint) rather than re-discovering it after the
fact a third time.

**Engine verification, done without a degenerate-parity reduction (none exists for this
construction, stated honestly rather than skipped).** Four independent checks instead: a staleness-
guard regression (synthetic gap → NaN, mirroring `compute_momentum_series()`'s own 2026-08-01
incident) plus a real-data scan (zero fires across 4.76M ticker-dates, plausible not alarming);
exit-wiring (30/30 sampled trades matched `evaluate_exit_reason()`/`evaluate_rank_dropped_exit()`
called directly); sizing (an initial 21/30 spot-check "failure" traced to the verification script's
own balance-timing proxy, not an engine bug — corrected and re-run at 30/30 once the engine was
instrumented to emit its real pre-entry balance); and a ranking/rebalance hand-computation across 5
regime dates × 2 pools × 3 arms (30 checks, zero instances of the engine holding a ticker it wasn't
legitimately eligible for). One incidental finding: the true candidate universe's price history
starts almost exactly 2007-01-03, giving both this construction and the existing production baseline
an identical, structural cold start with zero trades possible before ~2008 — confirmed directly
against the existing production reference file (0 trades in 2007, first trade 2008-01-03), not a
defect specific to cross-sectional's own 273-trading-day lookback.

No code changed in `analysis/signals.py` or `config.yaml`. `analysis/decision.py` received exactly
the one authorized addition. Nothing merged to the live path.

## Sector concentration monitoring — reporting only (2026-08-09)

**Displays sector concentration of open positions on the Dashboard and in the weekly email; never
gates, blocks, sizes, or exits anything.** Motivated by the selection audit's own "sector tilt" Part
3 finding (momentum ranking naturally concentrates into higher-beta sectors — Technology and
Consumer Cyclical 1.5–2.9x overweight vs. either candidate pool) — that concentration is the ranking
mechanism working as designed, not a defect, but was previously only visible by eyeballing the open
position list.

**No existing production code stored or fetched sector data before this** — confirmed by direct
search before adding anything new, per the task's own instruction. Several research scripts (the
selection audit's `sector_tilt` analysis and others) had each independently reimplemented the same
`yf.Ticker(t).info.get("sector")` fetch-into-a-fresh-CSV pattern 3–4 times across different
`research/` directories, always CSV-only, never DB-backed, and each with the same real ~40%
unresolved-ticker gap (mostly delisted/historical names yfinance has nothing for). This is the first
time sector data is persisted anywhere durable.

**New table, `TickerSector` (`data/database.py`), follows the `TickerDataQuality` model pattern
exactly**: `ticker` (primary key), `sector` (nullable — `NULL` means "fetched, yfinance had nothing",
not "never attempted"), `source`, `fetched_at`, with paired `write_ticker_sector()`/
`get_ticker_sector()` upsert/read helpers. **A row is written after every fetch attempt, successful
or not** — the presence of a row, not the value of `sector`, is what signals "already tried, don't
re-fetch" — closing the obvious failure mode where an unresolvable ticker gets re-queried against
yfinance forever.

**Deliberately never routed through `verify_ticker_identity()`/`scan_unverified_tickers()`**, per
explicit instruction — those exist to catch recycled-ticker-symbol identity fraud in the
historical/delisted population (see "Recycled-ticker-symbol corruption" above), an unrelated concern
to sector classification, and conflating the two would repeat the exact domain-scoping mistake
"Data-quality backstop was silently broken" (above) already found and fixed once this month (a check
applied outside the population/purpose it was built for).

**The render path is a pure DB read — corrected 2026-08-09, same day as the initial build, before
deploy.** The first version had `output/performance.py:compute_sector_breakdown()` call
`get_or_fetch_sector()` inline, which fetches from yfinance on a cache miss — a genuine risk of
exactly the lazy-load stall this project has been bitten by before: a first load after deploy would
otherwise run ~20 sequential throttled fetches while a user waits on the Dashboard. Fixed before any
deploy happened, not discovered in production: `compute_sector_breakdown()` now calls
`data.database.get_ticker_sector()` directly — a plain `SELECT`, never yfinance, regardless of
whether the ticker has a row yet. A ticker with no row (never pre-warmed, or genuinely unresolved
when it was) simply displays `"Uncategorized"` immediately. Verified directly, not just reasoned
about: `test_sector_breakdown_render_path_never_calls_yfinance` patches `yf.Ticker` to raise if
called at all and confirms the aggregation still completes correctly.

**`data/ingestion.py:fetch_ticker_sector()`** (a new, independent, single yfinance `.info` call,
never raises, returns `None` on any failure or missing field) **and `get_or_fetch_sector()`** (the
cache-check-then-fetch orchestration) **still exist, but are called from exactly one place now:
`prewarm_sector_data.py`** (repo root) — never from the Dashboard or weekly-email render path.

**`prewarm_sector_data.py`** (repo root, mirrors `check_eod_job_in_progress.py`'s
callable-function-with-a-thin-`__main__`-guard convention) fetches and caches sectors for the full
**live candidate pool** (`analysis.universe.get_live_stock_candidates()`, ~500 tickers) — not just
today's ~20 holdings, so a ticker that isn't currently held is already covered by the time it's
first entered. Throttled at `config["data"]["rate_limit_seconds"]` (2.5s, the project standard)
**only between genuine fetches** — an already-cached ticker is skipped with no sleep spent, so a
re-run after the pool is mostly warm completes in seconds, not the full ~500-ticker runtime. Never
raises per-ticker (`fetch_ticker_sector()` already catches everything internally; the pre-warm loop
wraps the DB write too, belt-and-suspenders, matching `scan_unverified_tickers()`'s own convention).
A row is written for every genuine attempt, resolved or not, so a real "yfinance has nothing for
this ticker" result is cached exactly as durably as a resolved one and never re-queried on a future
re-run either.

**No scheduled/recurring backfill job was built** — this remains a deliberate, stated trade-off, not
an oversight, unchanged from the original build: `prewarm_sector_data.py` is a one-off (or
manually-rerun) operational script invoked over SSH, not wired into `data/scheduler.py`. If the
pre-warm-plus-lazy-cache-on-first-genuine-fetch design proves insufficient later, that is a separate
decision.

**`output/performance.py:compute_sector_breakdown(open_cards)`** — the single shared aggregation,
called identically by both surfaces, not two independently-rendered copies. Groups by sector, both
by position count and by capital deployed (`current_value_gbp`, the mark-to-market figure — the view
that matters for concentration risk, not historical entry cost), sorted descending by capital in
both views. An unresolved sector groups under the literal string **`"Uncategorized"`** (renamed from
an earlier `"Unknown"` before deploy — display string only, no behavioural change) and still counts
toward total position count and total capital — never silently dropped from the totals, per explicit
instruction. An empty `open_cards` list returns an all-zero breakdown, not an error.

**Dashboard**: an expander (expanded by default — the whole point is visibility without a click) in
the existing Open Positions section, two side-by-side `st.dataframe` tables (by count, by capital £
and %). Plain, neutral styling throughout — no colour coding by concentration level, no threshold
implying something needs attention, matching this codebase's own explicit, already-established
"Decision-relevant, tone-neutral context" convention for position display (see
`output/performance.py:position_context_style()`'s own docstring: a dashboard that makes a number
look urgent as it approaches a threshold "would quietly reintroduce exactly [the reactive,
emotionally-driven decision-making] this system exists to remove").

**Weekly email**: a compact addition to both the plain-text and HTML bodies, alongside the existing
Open Positions table — explicitly **not** a second competing table (verified directly:
`test_weekly_summary_html_sector_section_is_not_a_second_table` asserts exactly two `<table>`
elements survive, Open Positions and Closed This Week). The text version adds a few bullet lines
("Technology: 96.0% (£12,000.00, 2 positions)"); the HTML version adds one inline paragraph in the
same neutral colour as everything else on the page. Both sections are omitted entirely (not rendered
empty) when there are no open positions.

**Tests**: sector aggregation math (count and capital views, and that they can genuinely differ —
e.g. one large position dominating capital share while trailing on count), `"Uncategorized"` handling
(grouped correctly, still counted in totals, and shown to cover both "never cached" and "cached as
genuinely unresolved" identically), the empty-portfolio case (all-zero, not an error), the render
path's own no-network-call guarantee, weekly-email rendering both with sector data present and with
none, and the pre-warm script directly (already-cached tickers skipped with no sleep, a genuinely new
ticker fetched/written/rate-limited, per-ticker exceptions don't stop the batch, an unresolvable
ticker still writes a row). 30 new/changed tests across this feature; full suite (986 tests) passing.

**No changes to ranking, sizing, admission, entry, or exit logic anywhere** — `compute_sector_breakdown()`
is a pure read-only aggregation over already-open positions' cards (`output/performance.py:get_brief_cards()`,
the same source the Dashboard and weekly email already used before this), consulted by nothing else
in the system. No sector caps, no concentration limits, no alerts, no thresholds — exactly as scoped.

**Deploy note — this feature needs a real deploy plus a manual pre-warm run.** `TickerSector` is a
new table; `init_db()` creates it automatically on the first post-deploy service start, same as any
other new model. Immediately after deploy, run `python prewarm_sector_data.py` over SSH once so the
live candidate pool is warm before anyone loads the Dashboard — without this, every currently-open
position (and every live candidate) displays `"Uncategorized"` until either the pre-warm script runs
or a future scheduled pre-warm is added (deliberately not built now, see above).

## UI render-path blocking audit (2026-08-09)

**Investigation-only survey of every Streamlit page for network/heavy-computation calls reachable
from a render path** — motivated directly by the sector-concentration fix above finding one instance
of this bug class; the audit traced all five real pages (Dashboard, Instruments, Backtest, Status,
Users — there is no separate Performance page; removed 2026-08-03, content relocated) for the same
shape of problem. Full report: CLAUDE_HISTORY.md's "UI render-path blocking audit" entry (moved
there 2026-08-09 from a standalone root-level file once its two corrections, below, were folded
into this file's own current-state sections — see that entry for the full findings table, both
detail sections, and the caching/scheduled-jobs tables, not repeated here).
Three real findings, two fixed same-day as separate, standalone changes per explicit direction; one
left deliberately unfixed pending real-world measurement.

**Finding 1, fixed — `st.cache_data.clear()` removed from the Status page (own commit).**
`_render_status_page()` called the blanket `st.cache_data.clear()` as its first statement —
confirmed via `git blame` to be leftover cruft from the since-removed manual "trigger pipeline now"
buttons (which correctly scoped their own `clear()` calls inside their own button handlers, all
removed in commit `e1ad313`; this one was never cleaned up). `st.cache_data.clear()` is
process-wide, not page-scoped — it silently wiped every `@st.cache_data` cache in the app on every
single Status-page visit, not just Status's own, defeating the `ttl=` guard on two network-capable
functions elsewhere (findings 2 and 3). Confirmed neither of the page's two remaining buttons
("Clear" error, "Refresh Account") depends on it — both already achieve fresh state via `st.rerun()`
plus reading un-cached `system_metadata` directly. Two regression tests: a behavioral one that
navigates Dashboard → Status → Dashboard within one `AppTest` session and confirms a Dashboard-cached
function isn't re-invoked by the intervening Status visit, and a cheap structural guard against the
call reappearing — both verified to actually fail against the reintroduced bug before being trusted.

**Finding 3, fixed — Dashboard's crisis banner no longer calls yfinance from the render path (own,
separate commit).** `_render_crisis_banner()` → `get_crisis_status()` used to call
`brokers.trading212.is_crisis_mode_active_now()` directly — a real, unconditional `^GSPC` intraday
quote fetch on every Dashboard render, throttled only by the (partially-defeated-by-finding-1) 300s
cache. Classified as bucket (c) — genuinely live external state, with no existing job already storing
it on a useful cadence (unlike finding 2 below). Fix: `data/scheduler.py:_market_hours_check_job()`
(already hourly, already NYSE-extended-hours-gated) now also calls `is_crisis_mode_active_now()`
once per run and writes `crisis_mode_active`/`crisis_mode_checked_at` to `system_metadata` — but
**only on a confirmed True/False result**; a `None` (check failure) leaves the last known-good value
in place rather than overwriting good data with "unknown," mirroring how the order-submission path
itself already treats `None`. `output/performance.py:get_crisis_status()` now reads these two keys
directly (`data.database.get_metadata_str()`) instead of calling the live function at all — confirmed
by a direct test (`test_crisis_status_render_path_never_calls_the_live_check`) that patches the live
function to raise if called. The Dashboard banner shows the stored value's "as of" timestamp
explicitly, and states plainly that it is informational only.

**The separation this fix depends on was confirmed, not assumed, before building.** The
order-submission path's own crisis check (`brokers/trading212.py:4144`, inside the stock-submission
logic `_stock_order_submission_job()` calls) is a single, self-contained call to
`is_crisis_mode_active_now()` with no shared state, no caching, and no dependency on
`system_metadata`'s `crisis_mode_active`/`crisis_mode_checked_at` keys — it remains fully live, fully
independent, and fully authoritative for the real, order-blocking decision, in a completely separate
process (the scheduler daemon) from the Streamlit UI process this fix touches. Up to ~60 minutes of
banner staleness (bounded by the hourly job cadence, and only while an NYSE extended-hours session is
open) is accepted specifically because the banner is informational only; the correctness-critical
check happens fresh, every time, at the one place that actually matters.

**Finding 2, fixed 2026-08-09 — Status page's Candidate Queue admission projection now reads stored
state (own, separate commit, superseding the "deliberately left unfixed" verdict below).** Originally
left unfixed pending real-world measurement of finding 1's cache-clear fix alone — see the struck-through
reasoning preserved at the end of this entry. After measurement, the decision was to fix it too, on the
same pattern as finding 3: `project_pending_order_admission()` (`brokers/trading212.py:3963`) no longer
constructs a `Trading212Client` or calls `get_account_summary()` at all. It reads the same stored
snapshot the T212 Invest Account panel already displays — `t212_account_snapshot` /
`t212_snapshot_fetched_at` in `system_metadata`, written by `data/scheduler.py:_fetch_t212_snapshot()`
(hourly via `account_state_refresh`, or sooner via the page's existing "Refresh Account" button, which
already used the correct dispatcher-flag pattern — `pending_t212_fetch`, serviced by the 60-second
`_dispatch_pending_jobs()` — and needed no change). This was confirmed a rewiring job, not new
infrastructure, before any code was written: both preconditions (`account_state_refresh` already
writing everything the projection needs; the dispatcher already servicing UI-requested refreshes) were
true. Every returned row now carries `snapshot_checked_at`, the ISO timestamp the whole batch's single
account read was fetched at (`None` if unavailable) — the Candidate Queue's own caption shows it
explicitly (`"📸 Projection based on account snapshot as of ... UTC (Xm/Xh ago)"`), not only the account
panel further up the page, since the projection is consumed there and staleness must be visible where
it's consumed. On a failed refresh, `_fetch_t212_snapshot()` already only wrote `t212_invest_error` and
left `t212_account_snapshot`/`t212_snapshot_fetched_at` untouched (confirmed, not assumed, by reading
its `except` branch directly) — so a failed refresh already meant "keep the last good value with its
original timestamp," precisely the required behaviour, with no change needed to get it. Verified by a
direct test (`test_project_admission_render_path_never_calls_the_live_client`, mirroring finding 3's
own) that patches `Trading212Client` to raise if constructed at all — confirmed to actually fail
against the reintroduced live call before being trusted.

**Deliberate consequence, recorded rather than glossed over: this projection is now hourly-stale by
design, not live.** `project_pending_order_admission()`'s docstring previously argued the projection
needed current, not stale, account state to be meaningful — that argument is superseded, not merely
overridden, for three reasons stated explicitly in the function's own docstring now: (1) the projection
was already labelled a **projection, not a guarantee** — every caller was always told not to treat
`projected_status` as committed, so admitting it can also be stale-by-up-to-an-hour is a difference of
degree, not of kind; (2) the real admission decision is **re-validated fresh at actual submission time**
(`_submit_pending_orders()`, untouched by this change) against whatever the account's real state is at
that moment — an hourly-stale projection here cannot itself cause a wrong trade, only a wrong-looking
preview; (3) an hourly snapshot is, if anything, **closer to the balance the EOD job actually computed
the queued decisions against** than a live mid-day read would be — the queue was built once, at EOD,
against one account-state snapshot, not continuously re-derived through the day, so reading a similarly
time-anchored stored snapshot is arguably more consistent with what's being previewed, not less.

<details><summary>Original "deliberately left unfixed" reasoning (struck through, preserved for the record)</summary>

`project_pending_order_admission()` (`brokers/trading212.py:3963`) genuinely calls
`Trading212Client.get_account_summary()` live, by design — its own docstring explains the projection
needs current, not hourly-stale, account state to be a meaningful preview of what would actually
happen at submission time. Once finding 1's fix restores the `ttl=60` cache's real protection, this
becomes at most one T212 call per minute on a page visited deliberately, not one per click — judged
plausibly acceptable without further change. Per explicit instruction: measure real-world behaviour
with finding 1's fix alone in place before revisiting this one; do not fix it speculatively ahead of
evidence that it's still a problem.

</details>

**Finding 4, fixed same round — Status page's log-download buttons looked gated, weren't (own,
separate commit).** The "⬇ Last 24 h" and "⬇ Last 7 days" buttons (`main.py`, inside the Log Viewer
section) used to run a `subprocess.run(["journalctl", ..., "--since", "24 hours ago"/"7 days ago",
...])` call *before* the corresponding `st.download_button(...)`, to supply its `data=` argument.
Confirmed against the actual pinned Streamlit version's `st.download_button` signature directly
(`inspect.signature`): `data` has no callable/deferred form — it must be a fully-computed value at
the point the button is declared, so Streamlit evaluated it on **every** script run reaching that
line, not only when the button was clicked. Both subprocess calls (15s and 30s timeouts
respectively) therefore ran unconditionally on every Status-page render (or any rerun triggered by
an unrelated widget elsewhere on the page) whenever the Log Viewer section rendered at all — up to
45 seconds of combined local subprocess time per render, larger in raw wall-clock terms than either
network-call finding above, though local I/O, not a network stall. Judged worth fixing alongside
the other two specifically because it sits on the same page about to be used to measure finding 2's
real-world behaviour — leaving it in would have meant any "does Status feel fast now" read was
dominated by the thing left unfixed.

Fix: a two-step prepare-then-download flow. A plain `st.button("Prepare last 24h"/"Prepare last 7
days")` runs the `journalctl` call only inside its own click handler and stashes the result in
`st.session_state`; a separate `st.download_button` renders only once that session-state key exists,
reading the already-computed value — so the expensive call now only ever fires on a deliberate
click, never as a side effect of rendering anything else on the page. Five new tests (subprocess
call-tracking confirms exactly one journalctl call — the initial `-n 200` fetch needed for the
viewer itself — on a bare render, with zero `--since` calls until the matching Prepare button is
clicked; each Prepare button triggers only its own window's fetch, not the other's; a failed fetch
shows "(not available)" without crashing) — `st.download_button` isn't natively recognised by this
Streamlit version's `AppTest` harness (renders as an `UnknownElement`, no dedicated accessor), so
verification reads `st.session_state` directly instead — the same underlying data the button
consumes, an equally direct proof. Verified to actually fail against the reintroduced bug (via
`git stash` on just this file, confirmed failing, then restored) before being trusted.

## UI render-path blocking audit, finding 4 — Dashboard open-position cards, missed by the original audit (2026-08-09)

**A fourth render-path stall, in the same class as findings 1-3 above, that the original audit's own
"confirmed clean" verdict on `_get_open_cards()`/`get_brief_cards()` missed entirely — found and fixed
in a follow-up pass, own commit, on the `dashboard-open-cards` branch.** Not a network call (the
audit's own three risk categories were yfinance, T212, and long computations) but a genuine
per-position price-history read-and-recompute on every Dashboard render, scaling directly with
open-position count — the same class of bug the audit's finding 5 (`get_ticker_status()`'s dead
full-universe path) flagged as a *latent* risk, except this one was live and firing on every render,
not latent.

**Why the audit missed it.** The audit's own "Confirmed clean" line for this exact function read:
`get_brief_cards()` (confirmed non-blocking post-2026-08-04 fix, scoped to held tickers only)" — true
as far as it went (the 2026-08-04 fix genuinely closed the ~60s full-503-ticker-pool stall, and
confirmed no yfinance/T212 call exists anywhere in the chain), but the audit's own three-category
scope (yfinance calls, T212 calls, long full-universe/multi-year computations) had no category for
"a *scoped*, per-open-position computation that is still expensive per position and still runs on
every render." The 2026-08-04 fix was verified against the specific complaint it was investigating
("~60s, feels like a stuck full pull") and, once that number dropped to a few seconds, was read as
"fixed" without a fresh, direct timing measurement of the now-scoped result — a `~6s` stall on 22
open positions doesn't trigger the same "something is clearly broken" signal a `~60s` stall does, so
it went unnoticed until this task's own explicit instruction to measure directly rather than trust
the existing "confirmed clean" line.

**Measured, not estimated — against the real, copied-in production database, 22 genuinely open
positions (the account's real position count at measurement time).**
- `get_ticker_status(config, tickers=<22 held tickers>)` (called internally by `get_brief_cards()`,
  the function `_get_open_cards()` used to call directly): **6.057s**.
- The full `get_brief_cards(config)` call (what `_get_open_cards()` actually invoked): **6.081s**
  single call; three repeated calls (no caching between them) measured **5.908s / 6.493s / 5.916s**.
- Scaling confirmed directly (not assumed) by varying N: N=1 → 0.416s; N=5 → 0.969s (0.194s/ticker);
  N=10 → 1.720s (0.172s/ticker); N=22 → 3.941s (0.179s/ticker) — linear in open-position count, at
  **~0.18-0.25s per held ticker**.
- Broken down by component (per ticker): `get_price_df(ticker, days=8000)` — the dominant cost —
  **0.225s** (a ~4,930-row SQL read + pandas frame construction, not a network call);
  `signals.compute_signal_series()` **0.0021s**; `signals.compute_momentum_series()` **0.0024s**;
  `universe.is_member()` **~0.0000s**; `crisis.is_crisis_day()` + `crisis.is_entry_blocked()` combined
  **0.0026s**; one shared `get_price_df("^GSPC", days=8000)` call (paid once per `get_ticker_status()`
  call, not per ticker) **0.169s**. The actual momentum/signal computation is cheap — the stall is
  overwhelmingly the raw per-ticker price-history DB read, still squarely inside "per-position
  price-history computation," the class of cost this project's render-path standard prohibits
  regardless of whether the underlying I/O is local or remote.
- Of the fields `get_brief_cards()` produces, only `current_sig_on` (from `sig_series.iloc[-1]`) is
  actually consumed by the rendered card and depends on this per-ticker read; `current_is_member` and
  `crisis_now` are cheap once the one shared `^GSPC` series is loaded; `momentum_pct` from
  `get_ticker_status()` is computed but never used by `get_brief_cards()`'s card-building loop at all.
  Every other card field (`entry_price`, `quantity`, `live_price`, `current_value_gbp`,
  `unreal_pnl_gbp`, `unreal_pct`, `momentum_pct_at_entry`, `stop_price`, `days_held`) was already a
  cheap, batched DB read (`t212_positions_snapshot`, `invest_orders`) before this fix — never the
  bottleneck — but per the task's own verbatim decision, **all** of it now comes from a single stored
  snapshot at render time, not just the expensive field, so "compute nothing at render" holds without
  exception rather than as a field-by-field judgement call.

**The fix — compute nothing at render; read one stored snapshot.** `output/performance.py:
get_stored_open_position_cards()` is a pure `system_metadata` read (`open_position_cards_snapshot` —
JSON list of the exact card-dict shape `get_brief_cards()` already produced — plus
`open_position_cards_fetched_at`, an ISO timestamp), never calling `get_brief_cards()` itself.
`main.py:_get_open_cards()` now wraps this read (still `@st.cache_data(ttl=60)`, but the TTL now
exists only to avoid a redundant SQL read on a widget rerun, not to throttle an expensive
computation) instead of calling `get_brief_cards()` directly. Measured post-fix:
`get_stored_open_position_cards()` averaged **17.8ms/call** (50 calls, empty snapshot) — no longer
scaling with open-position count at all, since it is a single-row metadata read regardless of how
many positions are open.

**Where the stored value comes from.** `data/scheduler.py:_account_state_job()` (hourly, minute 5 —
already the job that refreshes `t212_positions_snapshot`, which `get_brief_cards()` depends on) now
also calls a new `_refresh_open_position_cards_snapshot(config)`, which calls **the same, unchanged
`get_brief_cards()`** — moving *where* (a background scheduler job, not the Streamlit render thread)
and *when* (hourly, not every render) this computation runs, never *what* it computes or *how*. This
only fires when `_fetch_t212_snapshot()` (now returning `bool`, previously always `None` — confirmed
no existing caller inspected the old return value before this change) confirms a genuinely fresh
`t212_positions_snapshot` was written *this* cycle — deriving cards from a stale positions snapshot
and stamping a brand-new `fetched_at` on the result would show a falsely-current "as of" time for
underlying data that didn't actually change. "Only overwrite stored state on a confirmed result"
(the same pattern as `_market_hours_check_job()`'s crisis-status write, see finding 3 above): a
raised exception inside `_refresh_open_position_cards_snapshot()` is caught in `_account_state_job()`
and logged, leaving the previous `open_position_cards_snapshot`/`_fetched_at` pair untouched — an
empty list, by contrast, **is** written on a confirmed clean cycle (not treated as "unknown"), since
on the real server trading is always enabled with credentials present when this job runs, so `[]`
genuinely means "zero open positions right now."

**The signal-field caching tension, named explicitly, per this project's own standing practice of
never letting a display cache silently become something the trading logic reads.** This project has
a deliberate standing decision (see "Current architecture" above and `analysis/signals.py`'s own
role) that momentum signals are computed live from stored price history at DECISION time, never
pre-computed into a table — that decision is unchanged and this fix does not touch it.
`open_position_cards_snapshot` **does** now hold a second, cached copy of `current_sig_on` (a
per-position momentum-signal read) for DISPLAY purposes, computed by the same shared
`get_brief_cards()` → `get_ticker_status()` → `analysis.signals.compute_signal_series()` call chain
the Dashboard always used, just relocated off the render thread. **Confirmed directly, not assumed**:
`analysis/decision.py`'s `evaluate_exit_reason()`/`passes_entry_gate()`, every backtest engine in
`analysis/backtest.py`, and live order placement/exit logic in `brokers/trading212.py` (`_ticker_state()`,
`maybe_place_orders()`, `maybe_exit_positions()`) read `system_metadata` keys of their own
(`t212_account_snapshot`, `crisis_mode_active`, etc.) but **never** read `open_position_cards_snapshot`
or `open_position_cards_fetched_at` — grepped directly across `analysis/` and `brokers/trading212.py`
before shipping this fix, zero matches outside `output/performance.py` and `data/scheduler.py`
(the two files this fix touches). The key name itself (`open_position_cards_*`, not a generic
`signal_*` key) and its home in `output/performance.py` (the module whose own docstring already
states "extracted out of main.py so it's actually testable," never imported by any decision-path
module) are the scoping mechanism — a future session adding a new decision-path read must reach into
a display-only module and key to create the exact hazard this section warns against, not stumble
into it by accident.

**Staleness display and fallback, matching the crisis-banner/T212-account-panel precedent exactly.**
The Dashboard's "Open Positions" section shows "As of `<UK-local-time>` (refreshed hourly)." beneath
the summary line whenever `open_position_cards_fetched_at` is set, and "No open-position refresh has
completed yet — this section will populate after the next hourly account-state refresh." when it
isn't (a fresh deploy, before the first successful cycle) — never a blank section, never an
exception, never a value with no accompanying timestamp. A failed refresh cycle leaves the prior
snapshot and its ORIGINAL `fetched_at` in place (confirmed directly:
`tests/test_scheduler.py::test_account_state_job_cards_snapshot_failure_leaves_prior_stored_values_untouched`
seeds an old snapshot, forces `_refresh_open_position_cards_snapshot()` to raise, and asserts both
stored values are byte-identical to what was seeded beforehand) — the cards keep showing the last
good values with their real age visible, never a silently-refreshed timestamp for stale data.

**Tests, and the discipline of confirming they actually fail without the fix.** 16 new tests plus 4
modified across three files: `tests/test_performance.py` (5 new — including the decisive proof that
`get_stored_open_position_cards()` never calls `get_brief_cards()`, and a second, stronger proof that
it triggers no price-history computation at all by patching
`analysis.signals.compute_signal_series()` to raise — the yfinance/T212-patch pattern alone would
**not** have caught this specific bug, since the original stall was DB-bound, not network-bound;
stated explicitly here so a future reader doesn't assume that pattern alone is sufficient for every
render-path finding); `tests/test_scheduler.py` (5 new — `_refresh_open_position_cards_snapshot()`'s
write behaviour including the confirmed-empty-list case, `_account_state_job()`'s new conditional
call and its leave-prior-values-untouched-on-failure behaviour — plus 3 existing
`_account_state_job()` tests modified to pin down `_fetch_t212_snapshot()`'s now-meaningful `bool`
return, which an unconfigured `MagicMock`'s truthiness would otherwise have made those 3 tests
silently exercise the new cards-refresh branch unpredictably); and `tests/test_main_ui.py` (5 new
AppTest-level cases — rendering from a seeded stored snapshot, the "as of" caption, the
never-refreshed caption, the stale-snapshot-keeps-showing-old-timestamp case, and a literal
T212-client/yfinance-patch test matching findings 1-3's own shape for defense-in-depth, though — per
the point above — that particular test does not by itself prove the price-history-computation guard,
only the direct `compute_signal_series()` patch in `test_performance.py` does — plus 1 existing test's
docstring updated to describe the new mechanism it was already, incidentally, passing under).
Verified via `git stash` on the
relevant files (mirroring finding 4's own already-established verification discipline above): with
`output/performance.py`'s fix reverted to call `get_brief_cards()` internally, 3 of the 5
`test_performance.py` cases failed (both "never calls" proofs, plus the stored-snapshot-read case);
with `main.py`'s render-path change reverted to call `get_brief_cards()` directly, 5 of the 7
`test_main_ui.py` cases failed. All rechecked green after restoring the real fix; full suite (1,015
tests) passing.

**Does this mean Users/Instruments need re-checking with a corrected method? No.** The audit's own
"Confirmed clean" verdict for the Users page rested on "pure DB reads + button-gated writes
throughout — verified directly, zero network imports anywhere in the page or in `auth.py`" — a
direct, exhaustive import/call-site check, not an inference from a *different* function's earlier fix
the way this finding's miss was. The Instruments page's verdict rested on the same kind of direct
check (`get_all_watchlist_entries()`/`load_all_instrument_resolutions()` read `instrument_resolutions`/
`universe_membership` tables directly; confirmed no `get_live_stock_candidates()` call and therefore
no per-ticker `is_member()` loop) — again a direct trace, not an inherited verdict from a prior,
differently-scoped incident. **The actual blind spot was narrower than "any indirectly-reached
function might hide a cost"**: it was specifically "a function's fix for one reported symptom (a
~60s full-universe stall) was trusted as closing the finding entirely, without a fresh, direct timing
measurement of the function's now-scoped-but-still-real cost." Neither Users nor Instruments has an
analogous unverified-fix-carried-forward in their own trace — both were checked fresh, directly,
against the audit's own three stated risk categories, with no intervening "this was already fixed for
a different reason" assumption standing in for a direct measurement. If either page's own verdict is
ever found to rest on that shape of inference rather than a direct check, it should be re-audited on
that basis specifically — not as a blanket consequence of this finding.

## Position-count (concurrent-holdings target) study — pre-registered, tested, NOT ADOPTED (2026-08-09)

**Tests the second of the two untested selection levers the gate-width study's own pre-registration
identified** (gate width narrows *candidacy*; this narrows *capacity* directly) — motivated by Part 7
of the selection audit (momentum ranking sits at the 99th percentile of random admission on return,
98th-worst on drawdown; concentration trades one for the other). Four `per_trade_pct` values derived
from `max_total_deployed(0.70) / N` for nominal targets N ∈ {5, 10, 40}, plus baseline unchanged
(`per_trade_pct=0.05`, informally "22" after live's current snapshot). Full pre-registration and
results: `research/position_count_prereg_2026-08-09/PREREGISTRATION.md` / `REPORT.md`.

**Verdict: NOT ADOPTED — no arm clears the pre-registered +1.5pp cost-adjusted excess-return bar over
baseline, in either pool** (best case Pool 2 CLEAN n40 at +1.400pp; Pool 1, direction-only, shows every
arm *worse* than baseline). The walk-away sentence's primary clause fires outright, on its own.

**The more consequential finding is Sec 1a's own concern, added on review before this ran, confirmed
dead-on: two of the four arms never built the portfolios their names describe.** `per_trade_pct` is a
ceiling fed into `_var_position_size_core()`, not a dial for concurrent-position count — VaR sizing is
never pulled *up* to that ceiling, only capped by it, and across a 19-year walk of momentum-selected
(inherently higher-volatility) candidates, realized sizing clusters in a band a loose enough ceiling
never binds against. **Realized mean position count, both pools: n5 (target 5) → 11.78/12.17; n10
(target 10) → 12.23/12.59; n22 baseline (never a target, kept as today's literal config value) →
13.98/14.23 — the historical average, not the ~22 live's current snapshot showed; n40 (target 40) →
35.38/35.47, cleanly realized.** Materiality test applied: is an arm's realized count closer to its own
target, or closer to baseline's own realized count? n5 and n10 both land closer to baseline than to
their own targets, in both pools — reported as not-tested for their nominal targets, not as results,
per the pre-registration's own Sec 1a rule. n40 lands overwhelmingly closer to its own target — a clean
result. The named risk at the *other* end (n40 sub-minimum rejections from an over-tight ceiling) never
materialized at all — `n_rejected_subminimum = 0` in every one of the eight arm/pool runs.

**The §2 deployment guard — checked independently, and confirmed insufficient on its own for this
study's own purposes, exactly as Sec 1a warned it could be.** Mean deployment tracks baseline within
0.45pp across all eight arm/pool combinations — the guard alone would have called n5 and n10 clean.
Only the realized-position-count instrument added specifically because an arm can hold the same
capital % deployed across a different number of, larger or smaller, positions catches that n5/n10 never
built what they claim: the same total capital, spread across roughly the same ~12–14 positions as
baseline, not concentrated into 5 or 10.

**Consequences that trace directly back to this**: the pre-registration expected the 15pp drawdown
ceiling to bind hardest at n5 ("5 positions will likely breach any reasonable ceiling") — it never
does, in either pool, because n5 never concentrated enough to produce that effect; n40 (the one arm
that did realize close to its target) shows a drawdown *improvement* of 12–15pp instead. The intended
5→10→22→40 curve also collapses to two real points, not four, for the same reason — curve-shape
discipline (monotonic vs. interior optimum) could not be meaningfully assessed on the intended range.

**What this closes and doesn't.** Closes: `per_trade_pct` alone, at the 0.07–0.14 range this study
tested, is not a usable lever for testing genuine concentration under this account's VaR sizing
mechanics — confirmed, not merely suspected, and worth remembering before any future study reaches for
this parameter to test tight position counts. Does not close: whether genuine 5–10-position
concentration would actually help or hurt remains untested — would need a design enforcing a hard slot
count directly (closer to the gate-width study's shortlist mechanism, narrowing capacity rather than
candidacy) rather than a `per_trade_pct` retune.

No changes to `analysis/signals.py`, `analysis/decision.py`, or the live path. `config.yaml` is not
modified — every arm's `per_trade_pct` was passed as a direct argument to the existing, unmodified
`simulate_portfolio_compounding_mtm()` logic (via an additively-instrumented copy,
`position_count_engine.py`, parity-verified byte-identical to production on both pools before use) in
the research harness only. Nothing adopted, nothing merged, nothing deployed.

## "Mean deployment" and "cap binding" are not the same measurement — standing note (2026-08-09)

**Why this exists.** The deployment-cap study's own pre-check (see next section) reported the 0.70
`max_total_deployed` cap rejecting a viable candidate on 99.7% of steady-state trading days — in
apparent direct contradiction with the position-count study's own headline figure, run the same day,
that mean deployment sat at 63.07–63.81% across all eight arm/pool combinations, roughly 7pp of
persistent unused headroom under the identical 0.70 cap. Investigated directly (no new backtest runs —
both figures came from data the position-count study's own `rejections_{pool}_{arm}.csv` and
`panel_{pool}_{arm}.csv` had already produced) rather than either accepted as contradicting the other or
quietly reconciled by picking one. **Resolved: both are correct, and are not contradictory — they are
two different measurements of two different things**, and the apparent conflict was purely a labelling
problem, not a data problem.

**"Mean deployment" is a time-average of a state variable, sampled across every trading day in the full
19-year window, including 2007 (zero trades, cold start) and 2008–09 (crisis-driven mass exits).**
"Cap binding" is a frequency count of a rejection *event* — the fraction of days on which at least one
candidate that had already cleared every other admission check (`passes_entry_gate`,
`passes_momentum_floor`, `is_reentry_blocked`, VaR sizing, the cash clamp, `is_position_size_viable` —
`would_deploy_pct > max_deployed_pct` is the last check in the sequence, so a logged
`REJECTED_CAPACITY` row is, by construction, a candidate that would otherwise have been admitted) was
turned away purely for breaching the cap. A system can be at a low time-averaged deployment level while
still rejecting a candidate on nearly every day it's actually trading, because rejection depends on the
*marginal* candidate's own position size pushing that specific day's balance over the cap, not on where
the account's deployment happens to sit on average across the whole history. Confirmed directly, not
just argued: on the 4,163 (of 4,174) steady-state days with ≥1 capacity rejection, `current_deployed_pct`
never fell below 65.02–65.03% in either pool (mean 68.81–68.91%, median 69.16–69.20%) — every binding
day sits well above the 63% full-sample average; the days pulling that average down (2007, 2008–09, and
isolated crisis-blocked days like 2020-03-09→03-23, when entries are blocked entirely by the crisis gate
before ever reaching the capacity check) are a near-disjoint subset of the timeline from the days
rejection actually occurs on.

**The concrete measurement, not just the argument — from the position-count study's n22 (baseline,
`per_trade_pct=0.05`) rejection logs, both pools, no new backtest runs.** `reason` breakdown is 100%
`REJECTED_CAPACITY`, 0% `REJECTED_SUBMINIMUM`, in both pools — the log recorded no other rejection
reason at all (insufficient free cash, price sanity, and re-entry block are upstream gate/sizing checks
that `continue` silently with no log entry if they fail; they are not distinguishable from a "cap did
not bind" case in this log, but with sub-minimum at zero, no competing constraint was ever the one that
actually turned a candidate away in these runs either). Total `REJECTED_CAPACITY` rows: **955,263 (Pool
1) / 1,059,124 (Pool 2 CLEAN)**, spread across **4,463 / 4,477 distinct trading days** (90.5% / 90.8% of
the full 4,930-day history; 99.7% of the 4,174 steady-state 2010-onward days) and **497 / 599 distinct
tickers** — near the entire usable candidate pool in each case, an average of ~214 / ~237 rejections per
binding day. This is a steady, near-daily phenomenon across most of the candidate universe, not a
handful of pileup days. "Otherwise viable" is not inferred from the volume — it follows from where the
check sits in the code (last in the admission sequence, as described above), confirmed by reading
`position_count_engine.py`'s (and the identical production logic's) own control flow, not assumed from
the rejection count alone.

**On record: the pre-check that first proposed this study might be moot made exactly the error this
note now documents.** It read "mean realised deployment ~63% against a 0.70 cap" as evidence the cap
"was not binding" — inferring an event-frequency conclusion from a time-averaged figure, without
checking the event frequency directly. The correction came from checking the actual rejection log
before treating the inference as settled, per this project's own repeated practice of preferring a
direct measurement over a plausible-sounding inference — worth stating plainly as the same class of
mistake, not a different one, from the "argument vs. measurement" gap this note otherwise describes.

**The rule.** "Mean deployment" and "cap binding" have been used interchangeably in this project's own
reasoning up to this point, and they are not interchangeable — a low full-sample mean does not imply a
cap rarely binds, and a high binding frequency does not imply the mean will be high, because one is a
time-average and the other is an event-frequency conditioned on the marginal candidate's own size. Any
future report characterizing a cap, ceiling, or limit as "binding" or "not binding" should state
explicitly which of the two it means, and should not infer one from the other without checking — the
same discipline this file already requires for the two backtest conventions, cost-basis vs.
mark-to-market deployment accounting, and every other pair of measurements this project has found look
similar but answer different questions. See `research/deployment_cap_prereg_2026-08-09/PREREGISTRATION.md`
§0 for the full investigation and the READING (a)-confirmed verdict this reconciliation supports.

## Deployment-cap (max_total_deployed) study — pre-registered, tested, NOT ADOPTED (2026-08-09)

**Re-tests a lever two prior looks already examined, specifically on data corrected since both of
them** (the 2026-08-06 deployment-cap sweep and the earnings-dampener finding — see the immediately
preceding standing note's own motivation) — the realistic intraday-touch stop-fill correction, the
recycled-ticker quarantine, and the current FX+idle-cash cost template all postdate both priors. Four
`max_total_deployed` arms (0.70 baseline, 0.80, 0.90, 0.95), `per_trade_pct=0.05` held fixed (the
position-count study's own NOT ADOPTED conclusion carried forward unchanged). Full pre-registration and
results: `research/deployment_cap_prereg_2026-08-09/PREREGISTRATION.md` / `REPORT.md`.

**§0's pre-check (see the preceding standing note) confirmed the cap genuinely binds — reading (a) —
before any arm was run, and the deployment guard confirms this held across the full range tested**:
median realized deployment tracks each arm's own nominal cap within ~1–1.5pp at every level (e.g. c95:
94.13–94.16% median vs. a 95% cap, both pools) — unlike the position-count study's `per_trade_pct`
arms, this lever cleanly does what it's named for throughout.

**Verdict: NOT ADOPTED — two arms (0.80, 0.95) nominally clear the pre-registered +1.5pp cost-adjusted
excess-return bar on Pool 2 CLEAN, but the paired per-decision instrument's own explicit disqualifying
clause fires for all three higher-cap arms, not just the ones that failed the primary criterion.**
c80 (+1.562pp) and c95 (+1.585pp) clear the bar; c90 (+0.521pp), sandwiched between them, does not — a
genuine non-monotonic dip, not a smooth ramp. Pool 1 shows a different, partly contradictory shape (c90
as the mild peak, c80/c95 both lower, c95 actually negative) — reported as an unreconciled cross-pool
disagreement per the dual-provenance standing requirement, not resolved toward whichever pool looks more
favourable. **The entire spread across the three non-baseline arms on Pool 2 CLEAN (+0.521pp to
+1.585pp, 1.06pp) is close to the measured ~1.08pp noise floor** — comparable in size to pure
measurement noise, which argues against trusting either nominal "clear" as a reproducible effect.

**The decisive finding: no arm's own unique admissions are distinguishably better than baseline's, in
either pool, for any of the three higher-cap arms — including the two that nominally cleared the
primary bar.** Win rate on each arm's own unique admissions is at or slightly *below* baseline's own
unique-admission win rate in every single pairing (e.g. Pool 2 CLEAN vs. c95: baseline-only 43.87%,
c95-only 43.42%). This is the walk-away sentence's own pre-registered disqualifying condition, and it
fires cleanly.

**A mechanistic reading recorded, explicitly not treated as grounds to override the pre-registered
rule.** Raising `max_total_deployed` at constant `per_trade_pct` doesn't change *which* candidates are
preferred, only how many can be held concurrently — reaching deeper into a ranked list the selection
audit's own Part 5 already found is "not obviously worse" on average than the admitted population.
Admitting more of a similarly-good population, funded by more capital rather than idle cash, is closer
to a leverage/exposure effect than a selection-quality effect — under that reading, "unique admissions
aren't better" is the expected signature of the mechanism, not evidence the mechanism failed. This is
recorded as a hypothesis for a future, differently-instrumented study, not as an excuse for this one:
the walk-away sentence was written in advance specifically so a plausible post-hoc explanation cannot
talk a study out of its own disqualifying condition.

**Costs modelled per-arm, confirmed material to the verdict, not just methodologically required.** FX
drag rises from 3.34%/yr (c70) to 4.61–4.63%/yr (c95) as turnover scales with available capacity; idle-
cash credit shrinks from ~0.55%/yr to ~0.20%/yr. The net swing (~1.5pp/yr) is comparable in size to the
entire primary-criterion effect being measured (~1.06pp spread) — the 0.30% FX round-trip assumption,
carried over unchanged from prior work, is doing real work in this verdict and would plausibly move it
under a materially different real-world FX cost.

**Converges with, rather than overturns, both priors this study was built to re-check** — on fully
corrected data, the same conclusion holds: raising `max_total_deployed` above 0.70 has not been shown to
deliver a robust, real improvement, across three independent looks spanning two different database
states. Block-pass count (Pool 2 CLEAN: baseline 7/16, all three higher-cap arms 5/16, within the
measured 3-block noise floor) does not discriminate the nominal winners (c80, c95) from the nominal
loser (c90) either — one more small piece of corroboration for reading the whole excess-return spread
as noise.

**Operational headroom, qualitative, not scored, as pre-registered**: at 90–95% median realized
deployment, the account routinely operates with only 5–12% cash — materially less room to act on a new
high-conviction candidate than today's 0.70 cap provides, for a return effect this study could not
confirm is real.

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or the live path. Caches
reused directly from the position-count study (reconfirmed unchanged before use), no new engine — every
arm's `max_total_deployed` passed as a direct argument to the existing, unmodified
`simulate_portfolio_compounding_mtm()` logic via the same parity-verified
`position_count_engine.py`. Nothing adopted, nothing merged, nothing deployed.

## Capital deployment is not a usable lever in this system, from either direction — synthesis of two studies, 2026-08-09

**Recorded as one conclusion, not left as two separate study results, because the two studies tested
opposite directions of the same underlying question and both came back the same way.** The
position-count study (`per_trade_pct`, narrowing concentration) and the deployment-cap study
(`max_total_deployed`, raising total exposure) are, together, a complete test of whether this account's
capital deployment — how much is committed, and how concentrated it is — can be tuned for more return.
Neither direction delivered one.

**Direction 1 — concentrating capital into fewer, larger positions.** The position-count study found
`per_trade_pct` is a ceiling VaR sizing is never pulled up to, not a dial: at the tight end (nominal 5
and 10 positions), realized position count collapsed to ~12–14 regardless of the target, landing closer
to baseline's own realized count than to the arm's own nominal one. **A second, independent fact from
the same study reinforces this from a different angle**: mean deployed % sat at 63.07–63.81% across
*all eight* arm/pool combinations — n5, n10, n22, and n40 alike — despite position count itself varying
by nearly 3x (11.78 to 35.47 realized positions). Deployment level and position count are close to
decoupled at this account's volatility/momentum-selection profile: changing how many positions are held
does not change how much capital ends up deployed in aggregate.

**Direction 2 — deploying more capital in total.** The deployment-cap study confirmed `max_total_deployed`
*does* track cleanly as a genuine dial (median realized deployment within ~1–1.5pp of every nominal cap
tested, 70–95%, both pools — unlike `per_trade_pct`'s failure at the low end) — but raising it delivered
no robust, real return benefit. Two arms nominally cleared the pre-registered bar, but the paired
per-decision instrument showed none of the three higher-cap arms' own unique admissions were
distinguishably better than baseline's — the walk-away sentence's own disqualifying clause, on its own
sufficient to not-adopt. Corroborated, not merely accompanied, by two more independent signals: the
whole spread of results across arms (~1.06pp) sat close to the measured noise floor (~1.08pp), and the
curve shape inverted between the two pools rather than replicating. Three independent reasons pointing
the same way, not one number that happened to miss a threshold.

**The honest combined summary**: this account cannot be meaningfully concentrated tighter than its own
natural ~12–14-position equilibrium by adjusting per-trade sizing, and deploying more of its capital in
total — even though the lever genuinely works mechanically — has not been shown to buy anything for it.
Today's `max_total_deployed=0.70` / `per_trade_pct=0.05` should be read as settled for capital-deployment
purposes specifically, not as an arbitrary historical default still worth revisiting from either side,
pending a genuinely new argument (not a re-run of either lever) for doing so. **This also converges
with, not merely coexists alongside, two older, pre-correction looks at the deployment-cap lever
specifically** (the 2026-08-06 sweep and the earnings-dampener finding) — three independent
investigations, spanning two different database states, all landing in the same place.

**What this synthesis does not close**: whether *which* candidates get admitted (selection quality,
independent of how much capital or how many slots) can be improved is a separate, still-open question —
the relative-ranking work motivated by Part 4 of the selection audit is exactly that different question,
not a re-test of either lever this section closes.

## Relative-ranking (cross-sectional rank, daily admission) study — pre-registered, tested, ARM B CLEARS ITS BAR (2026-08-09)

**The first arm in this project's entire recent research sequence — position-count, deployment-cap,
gate-width, cross-sectional-monthly, all NOT ADOPTED — to clear every pre-registered walk-away
condition.** Motivated by a real, unresolved tension left by the monthly cross-sectional study: that
study failed decisively on its primary criterion (severe monthly-rebalance underdeployment swamping
everything), but its own month-level per-decision instrument showed cross-sectional's realized trades
winning the per-decision comparison 52–66% of the time — cadence and selection-quality were bundled
together by that study's own design and never separated. This study removes the cadence confound
entirely: all arms admit on the existing *daily* clock. Full pre-registration and results:
`research/relative_ranking_prereg_2026-08-09/PREREGISTRATION.md` / `REPORT.md`. Built and run from a
dedicated worktree (`c:\GitHub\investment-app-relative-ranking`), not the shared main checkout — see the
concurrent-session collision note elsewhere in this file for why.

**Narrowed on review before running, and this narrowing is what made attribution possible.** The first
draft of Arm B swapped the cross-sectional rank signal into the entry gate, the 2% floor, and
`SIGNAL_OFF` as well as the admission sort key — which would have changed exits in B too, collapsing B
and C into two flavours of rank-based exit rather than a clean attribution arm plus one added variable.
Corrected: **B changes exactly one thing relative to production — the sort key `_admit_group()` ranks
gate-passing candidates by** (12-minus-1 cross-sectional relative rank, descending, instead of absolute
12-month momentum). Gate, 2% floor, and every exit reason (`CRISIS_EXIT`/`STOP_LOSS`/`SIGNAL_OFF`/
`MAX_HOLDING`) stay byte-identical to production for B *and* C — verified directly (30/30 sampled
`SIGNAL_OFF` exits confirmed to trace to absolute momentum, never rank), not merely asserted. **C is B
plus `evaluate_rank_dropped_exit()` (already in `analysis/decision.py`, added for the monthly study, not
modified here) checked every day** — a currently-held position exits if it falls out of the daily-
recomputed top-30 by cross-sectional rank (N=30 justified against baseline's own measured realized
occupancy of ~14, roughly 2.1x, not the monthly study's N=40 which was calibrated to a completely
different, occupancy-target construction).

**Verification, stronger than the pre-registration itself required.** The pre-registration stated (as
the monthly study had for its own construction) that no degenerate-case parity reduction exists for a
cross-sectional engine. On review, a test-only `_sort_key="momentum"` toggle was added to the new engine
specifically to create one: with it set, the new engine reproduced raw production
`simulate_portfolio_compounding_mtm()` **byte-identical on Pool 2 CLEAN — 4,919 trades, £103,926.50, 0
mismatches** — proving every part of the engine except the one sort-key line is a faithful copy of
production. Staleness guard, exit-wiring/signal-separation, and a 4-date ranking hand-check (plausible,
dated momentum leaders throughout — TSLA/ENPH/DXCM/MRNA/NVDA in the 2020 COVID rally, OXY/RRC/DVN/
TRGP/EQT in the 2022 energy rally) all passed cleanly before any real result was trusted.

**Headline result — Pool 2 CLEAN (decisive): B clears +1.993pp cost-adjusted excess return over
baseline, nearly 2x the measured ~1.08pp noise floor, without breaching the drawdown ceiling
(worsens only 1.52pp; actually improves 5.14pp on Pool 1).** Deployment guard passes cleanly for both B
and C (within 0.21pp of baseline, both pools) — the tightest parity this project's whole sequence has
measured, the direct, designed consequence of every day being an entry-attempt day for every arm (never
gated to a periodic rebalance) plus cross-sectional rank's own zero-sum property (a name can only drop
out of the top-N because another rose to displace it, and that displacer is, by construction, a live
candidate for the freed slot).

**The decisive corroboration — not a portfolio-level headline standing alone.** B's own unique
admissions (the trades B makes that baseline doesn't) show a real, distinguishable edge over baseline's
own unique admissions on *both* win rate and mean P&L%, in both pools (Pool 2 CLEAN: 44.30%/0.89% vs.
43.57%/0.63%) — the walk-away sentence's own fourth clause, built specifically to catch a portfolio-
level gain that doesn't trace to genuinely better individual decisions, does not fire. This is the
strongest evidence this project has produced that a selection-quality signal (not a deployment or
cash-holding artefact) is doing the work.

**The A-vs-B overlap prediction was wrong, in an unusually informative direction.** Predicted 45–65%
(already a downward correction from a naive same-signal argument); measured **21.8–23.1%**, both pools —
far below even the low end of the prediction, for a change that touches *only* the sort key on an
*otherwise-unchanged eligible set*, no gate change, no truncation. The largest capacity-cascade effect
this project has measured for the narrowest change it has ever tested — reported as a real, standalone
finding about this system's sensitivity to admission order, not folded quietly into the headline.

**C does not improve on B — the curve-discipline check this study was specifically built to run.** C
individually clears every one of its own walk-away conditions too, but the B→C delta (isolating what
the added `RANK_DROPPED` exit itself contributes) is **negative in both pools** (Pool 2 CLEAN −0.243pp;
Pool 1 −1.017pp, enough to push C's own Pool 1 delta negative outright). C's paired-instrument win-rate
edge is larger than B's, but its P&L-magnitude edge is mixed (positive Pool 2 CLEAN, negative Pool 1) —
consistent with more, smaller wins that don't net out ahead of B's fewer, better-sized ones. **B's
mechanism alone is the recommendation; C's added de-selection layer should not be adopted on top of it.**

**A research finding, not a production change — real promotion needs its own follow-up, not fast-
tracking on a strong backtest number.** No code changed on any live path in this study; `config.yaml`
untouched. If B's mechanism is ever built into the live system: the cross-sectional rank computation
would need to move from research-only code into a genuine, tested production module (not left as
research code a live caller reaches into, per this project's "one implementation of any given trading
decision" invariant), and this project's four live/backtest parity-enforcement layers
(`tests/test_decision_logic_placement.py`, `tests/test_comprehensive_replay_parity.py`,
`tests/test_live_backtest_parity.py`, `tests/test_one_sided_decision_rules.py`) would need genuine
coverage of the new ranking logic from the start, not retrofitted after the fact. Live trading remains
demo-only (`trading212_invest.live: false`) regardless, so nothing here is urgent from a real-money-risk
standpoint — but the research-to-production gap is real and should not be skipped because this result
looks strong.

**Robustness test, 2026-08-09 — CONFIRMED ROBUST, with one named caveat.** Before Arm B goes anywhere
near promotion, its result was stress-tested against the measurement toolkit's own perturbation method
(drop one random ticker, shift the walk start date, reshuffle admission tie-breaks) — 20 paired draws,
Arm A and Arm B run against the *identical* perturbed pool/date/tie-break state each time so the
quantity of interest is the B-minus-A delta's own distribution, not two independent spreads. Full
detail: `research/relranking_robustness_2026-08-09/REPORT.md` (dedicated worktree,
`c:\GitHub\investment-app-relranking-robustness`). **20 of 20 draws favour B — no exceptions, no sign
flips, no near-zero draws.** The unperturbed +1.993pp sits exactly at the distribution's median and is
its single most common value (9/20 draws land on it exactly). Not dominated by one ticker the way the
original noise-floor study's DHI finding was (most individual ticker drops are complete no-ops; nothing
comes close to erasing the result) — **but dropping AMAT alone compresses the delta to +0.427pp, ~21%
of typical, the one real named partial dependency for the result's *magnitude* (not its existence or
direction)** — stated plainly since that single-draw figure sits below this project's own ~1.08pp
noise-floor bar on its own, even though 19 of the other 20 draws clear it comfortably. A second,
distinct, ticker-independent mechanism (shifting the walk's start date ≥3 days) reliably pushes the
delta *higher* (+2.33pp to +2.65pp), via the same capacity-cascade sensitivity this project has
repeatedly measured elsewhere. Deployment parity holds under every perturbation (gap −0.05pp to
+0.02pp) and — the strongest single result in this test — **B-only admissions beat A-only admissions on
mean P&L% in all 20/20 draws**, corroborating the original study's central claim (B's edge traces to
genuinely better decisions, not a cash-holding or cascade artefact) across twenty independently-
perturbed configurations, not just the one original run.

**RETROSPECTIVE CORRECTION, 2026-08-12 — the draw count above overstates what was actually tested.
"20 paired draws" is arithmetically right and evidentially wrong: 4 of the 20 were
`shuffle_tiebreak` draws, and that dimension is INERT BY CONSTRUCTION in this system.** Admission
sorts on `(-rank_value, insertion_idx)` where the rank value is a continuous float, so exact ties
essentially never occur and the tie-break never binds — reshuffling insertion order changes nothing,
and those four draws return the unperturbed value byte-identically rather than coincidentally. Found
while running the same dimension in the exit-mechanics sweep's own perturbation test (below), where
three shuffle draws returned exactly `+6.443pp` three times and the mechanism became obvious; the
relative-rank report's own "9 of 20 draws land on it exactly" already disclosed the *symptom* and
attributed it to no-op ticker drops and to the universe starting ~2007-01-03 regardless, but did not
identify that the four tie-break draws test nothing at all. **Corrected count: roughly 11 informative
draws of 20** (4 inert tie-breaks, 3 ticker drops the report itself flagged as complete no-ops, and 2
start-date shifts of 1–2 days it flagged as reproducing baseline exactly).

**This does NOT change Arm B's verdict or its adoption** — the direction held in every informative
draw, AMAT remains the one named magnitude dependency, and no draw reversed. It is recorded because
the alternative is a documented figure that overstates the evidence behind an already-adopted change,
and because the general lesson is a member of the standing "check that reports clean for a reason
unrelated to the thing it was checking" class: **a perturbation dimension that cannot bind is not a
weak test, it is not a test, and counting its draws as confirmations inflates the apparent evidence.**
Any future perturbation study in this project should verify each dimension actually moves the result
before counting its draws — cheap to check (do the draws return byte-identical values?), and both
studies that have used this dimension would have caught it immediately.

## Relative-rank admission ordering ADOPTED — `admission_ranking_mode: "relative_rank"` (2026-08-11)

**Flipped on 2026-08-11.** Arm B's mechanism is now the ACTIVE admission ordering; absolute momentum
becomes the shadowed alternative, still recorded in `admission_ranking_shadow` for comparison but no
longer the ordering that trades. The section below documents the 2026-08-09 code promotion and remains
accurate as history; this header supersedes its "OPEN CANDIDATE, not adopted" framing.

**Nothing else changed.** `per_trade_pct`, `max_total_deployed`, `atr_stop_multiplier`,
`stock_max_holding_days`, `min_momentum_pct` all unchanged, and **`trading212_invest.live` remains
`false`** — this adoption carries no real-money risk. Arm C's `RANK_DROPPED` de-selection exit is
deliberately NOT adopted; it underperformed Arm B in both pools.

**Pre-flight checks, all four passed before the flip** (re-run 2026-08-11 after the 2026-08-10 EOD
completed; two had failed earlier purely because that EOD had not yet run):
1. `^SP500TR` — 4,930 rows, 2007-01-03 → 2026-08-07, **no `ticker_data_quality` row**, zero index
   tickers quarantined table-wide. This was the only silent failure mode of the four (nothing on the
   display path reads quarantine status), and it confirms the 2026-08-10 ingestion-screen scoping fix
   works in production, not just in tests.
2. `admission_ranking_shadow` — **308 rows** from the 2026-08-10 14:30 submission, against **308**
   `pending_orders` rows for that decision date (2026-08-07); both `admitted_under_*` sums **0**, correct
   for a fully-deployed account where neither ranking admitted anything.
3. `benchmark_comparison` — 7 rows, anchored 2026-08-02 at £50,000.
4. Config confirmed `absolute_momentum` / `live: false` immediately before the edit.

**Forward validation is now a long-run background process, not pending work — this is a deliberate
reframing, stated plainly rather than left ambiguous.** The forward shadow comparison keeps accruing and
its review trigger (90 days AND 50 paired outcomes AND no single ticker above 40% of the measured
effect) is unchanged. But with ~22 concurrent positions and admissions gated on max-holding exits, **50
active-mode-unique closed outcomes is plausibly a multi-year proposition, not a multi-month one.**
Waiting for it before adopting would mean deferring indefinitely a result that already cleared a
pre-registered bar and a purpose-built robustness test. **Backtesting plus robustness testing is the
standard this adoption rests on**; the forward record is corroboration that will arrive slowly, and
should be read that way rather than as a gate that was skipped.

**Multiple-comparisons scepticism is warranted, and is recorded rather than argued away.** This is one
clearing result out of roughly 60 experiments across this project's research history — at that count,
some false positives are expected on chance alone. Two things partially answer it, neither completely:
(a) Arm B is a **construction change with a prior rationale** (Part 4 of the selection audit found
rank-within-day predicts outcomes where raw momentum magnitude does not), not a parameter swept until
something cleared; and (b) the 20-draw paired perturbation test was designed specifically to catch
"got lucky on one configuration" — 20/20 draws favourable, with the unperturbed value at the
distribution's median and mode, is not the signature of a chance finding. **The honest position: this is
the best-evidenced change this project has made, and it is still one result among many.** The AMAT
magnitude dependency (+1.99pp → +0.427pp when that single ticker is dropped) is the concrete reason to
rely on the direction rather than the point estimate.

**Flip date recorded for the shadow record: 2026-08-11.** The shadow comparison's own accrual under
`relative_rank` starts from the first decision made after this deploy — every row before it carries
`active_mode = "absolute_momentum"` and is classified against that, which is why
`_classify_shadow_row_admission()` evaluates each row against its OWN recorded mode rather than today's
config.

**CONFIRMED IN RUNNING CODE, 2026-08-13 — the adoption had never actually been verified against
production data until this check, and the only prior evidence pointed the other way.** Until now the
adoption rested on the config file reading `relative_rank`, which proves the file deployed and
nothing about what the decide phase computed — the same worthless-check trap as reading
`atr_stop_multiplier` to verify a stop (see "Stop config changes carry a ONE-DAY LAG" above). The
one concrete data point on record actively looked like a contradiction: ECHO's row showed
`admission_rank_value` and `momentum_pct` **identical** at 2.2559. Verified directly against
`pending_orders`, per decide date:

| decide date | n | `admission_rank_value == momentum_pct` | `== relative_rank_pct` |
|---|---|---|---|
| 2026-08-12 | 276 | 0 | **276** |
| 2026-08-11 | 299 | 0 | **299** |
| 2026-08-10 | 301 | **301** | 1 |
| 2026-08-07 | 308 | **308** | 0 |

A clean single-day transition landing exactly on the documented flip date, and ECHO's identical
values are fully explained by its **2026-08-07** decide date — pre-flip, so the two columns coincided
by construction rather than contradicting the adoption. (The lone `== relative_rank_pct` row on
2026-08-10 is coincidental numeric equality on one ticker in a 301/301 momentum batch, not a partial
mode change.) The orderings also differ in substance, not just in which column is copied: on
2026-08-12 the top 5 by `momentum_pct` (`NUE, CRL, NTAP, PSX, FDX`) and by `admission_rank_value`
(`ADI, CMI, FDX, HAL, MPWR`) share only FDX, at different ranks — e.g. `CHRW` ranks 7th on relative
rank off a momentum of just 0.262. **Worth generalising: a config value and the behaviour it is
supposed to produce are two separate claims, and this project has now been caught assuming the second
from the first twice in two days.**

## Relative-rank admission ordering promoted to production code (2026-08-09) — the code promotion

**Superseded framing, kept as history**: when written, this section described an OPEN CANDIDATE whose
config default still pointed at the old behaviour. That flip happened 2026-08-11 — see the section
immediately above. The promotion detail below is unchanged and still accurate.

**The relative-ranking study's own mechanism (admission ordered by cross-sectional relative rank instead
of absolute momentum) is now a real, tested option in the production codebase — but the config default
still points at the OLD behaviour, and nothing about live trading changed.** This section documents the
promotion work the study's own "What this doesn't close" and the 20-draw robustness test both flagged as
required before the finding could even be considered for real use. Promoting the CODE is deliberately
separated from adopting the BEHAVIOUR: `trading212_invest.admission_ranking_mode` defaults to
`"absolute_momentum"` (today's real, current behaviour, byte-for-byte unchanged), and switching it to
`"relative_rank"` is a distinct, later, deliberate decision — not a consequence of this work landing.

**What actually changed, precisely, matching the study's own scope statement exactly.** Admission
ordering only — which gate-passing candidate wins a scarce capacity slot when more qualify than there is
room for. The entry gate (momentum > 0 + the 2% floor), `SIGNAL_OFF`, every other exit reason, sizing, and
capacity accounting are all unaffected and stay keyed to absolute momentum regardless of this setting.
Arm C (the added `RANK_DROPPED` de-selection exit) was explicitly excluded from this promotion, exactly as
the study itself recommended (C did not beat B in either pool).

**The two new production pieces, mirroring the shared-implementation pattern this project already uses
throughout — see "There is exactly one implementation of any given trading decision" above.**
- `analysis/signals.py:compute_relative_rank_series()` — the 12-minus-1 cross-sectional signal
  (`close(t-21)/close(t-252) - 1`), promoted verbatim from the study's own research-only
  `cross_sectional_signal.py`. Carries the identical staleness-guard mechanism as
  `compute_momentum_series()`'s own `_MAX_ANCHOR_GAP_DAYS`, tested with the same synthetic-gap fixture
  style already established in `tests/test_signals.py` (a real gap in either the 1-month or 12-month
  anchor correctly resolves to `NaN`, recovers once a full gapless window exists past the gap).
- `analysis/decision.py:resolve_admission_ranking_mode(config)` / `resolve_admission_rank_value(*,
  momentum, relative_rank, mode)` — the ONE function both `analysis/backtest.py`'s `_admit_group()` and
  `brokers/trading212.py`'s `maybe_place_orders()` call to resolve the sort key used for capacity-
  constrained admission. `_load_pending_orders()`'s SQL `ORDER BY` and `output/performance.py`'s two
  display-side candidate-queue queries were all renamed from `momentum_pct DESC` to
  `admission_rank_value DESC` (a new `pending_orders.admission_rank_value` column, populated identically
  by live's decide phase and read by the same submission-priority ordering that already existed) —
  behaviour-preserving in the default mode, since `admission_rank_value` equals `momentum_pct` whenever
  `admission_ranking_mode` is `"absolute_momentum"`.

**Verified directly, not just argued, that `resolve_admission_rank_value()` is the ONLY place admission
ordering happens on either side that this comparison could ever be run against — a targeted grep across
`brokers/trading212.py` and `analysis/backtest.py` for every sort/`ORDER BY` involving momentum or rank,
checked before merging this work, not after.** Two more hits turned up, both pre-existing and already
outside the live-comparable scope, matching this project's own established frozen-engine exemption
pattern (see "Backtest engines" above): `simulate_portfolio()`'s `priority_rank` predates momentum-based
admission entirely (the old `(is_etf, config-order)` rule momentum priority itself superseded, unrelated
to this promotion); `simulate_portfolio_compounding()` (the non-MTM variant) has its own internal
momentum-sorted `_admit_group()`-shaped closure that genuinely bypasses the resolver — but this engine is
one of the three explicitly FROZEN, historical-reproducibility-only engines already documented as exempt
from every post-freeze fix (the 2026-08-03 re-entry-cooldown fix, the 2026-08-06 realistic-stop-fill fix,
and now this one, by the same standing reasoning), never called by `run_full_validation()` or exercised by
any live/backtest parity test — `simulate_portfolio_compounding_mtm()` is the one engine actually compared
against live, and its own single admission sort is confirmed resolver-routed. Recorded here explicitly so
a future reader auditing this promotion doesn't flag `simulate_portfolio_compounding()`'s own momentum
sort as an overlooked gap — it was checked, and is accepted, not missed.

**All four structural live/backtest parity layers got genuine new coverage, not a retrofit, per the
promotion's own requirement.** Layer 1 (`tests/test_decision_logic_placement.py`)'s AST scanner keyword
list now includes `admission_rank`/`admission_ranking_mode`/`relative_rank` — a defensive, forward-looking
addition, since both real call sites already correctly route through the shared function. Layer 4
(`tests/test_one_sided_decision_rules.py`) confirms both new `analysis/decision.py` functions are called
by both live and backtest with no stale exemption. **A new file,
`tests/test_admission_ranking_parity.py`, implements Layer 3's own requirement directly**: two synthetic
tickers engineered with a genuine momentum/relative-rank crossover (A has higher absolute momentum, B has
higher relative rank — verified numerically, not assumed) race for one scarce capacity slot; live order
submission and the backtest engine must agree on the same winner under each mode, and do. **Verified to
actually fail, not just pass**: `analysis.backtest.resolve_admission_rank_value` was monkeypatched to
silently ignore `mode` and always return momentum (simulating exactly the "a call site quietly stops
calling the shared function correctly" regression this test exists to catch) — the broken backtest then
falls back to the momentum winner while live (unbroken) still picks the relative-rank winner, and the two
sides correctly disagree, exactly as a real regression should be caught. **Layer 2
(`tests/test_comprehensive_replay_parity.py`) gained a full re-run of its own existing ~320-day,
multi-ticker, capacity-sharing walk under `admission_ranking_mode="relative_rank"`** — a genuinely
different kind of check from the targeted single-day race above (a long, un-cherry-picked scenario
nobody engineered in advance to expose one specific difference), confirming the mode switch doesn't
silently break anything else. Building this exposed one real gap worth recording: the two synthetic-price
fixture functions had to be split into separate backtest-length (253 rows — sized so the frame's only row
is also the first day either signal is non-`NaN`, since the backtest engine admits on a ticker's first
eligible day, not its last) and live-length (305 rows — clearing `_ticker_state()`'s own 300-row minimum-
history floor, since live only ever evaluates the frame's latest row) variants; an earlier single-frame
version silently produced a scenario with no real crossover on the day that actually mattered, caught
only because the resulting admitted set didn't match either arm's expected winner.

**Reproduced Arm B's own study figures via the real production code path — not the disposable research
engine — before any of the above was trusted as sufficient.** `research/relative_ranking_promotion_verify_2026-08-09/`
re-ran the study's own Pool 2 CLEAN comparison (612 CLEAN tickers, price data through 2026-08-07 — DB
state reconfirmed fresh, not assumed) using the real, unmodified `analysis/backtest.py:
simulate_portfolio_compounding_mtm()`, switching only the new `admission_ranking_mode` config key.
**Arm A: 4,919 trades, £103,926.50 total P&L. Arm B: 4,876 trades, £166,830.11 total P&L — both figures
match the study's own `trades_pool2_clean_{A,B}.csv` exactly, 0 row-level mismatches after sorting both
frames onto a canonical `(ticker, entry_date, exit_date)` order** (a first comparison pass showed hundreds
of apparent "mismatches" despite identical trade counts and identical total P&L to the penny — investigated
before being trusted, traced to comparing raw insertion order rather than trade identity; the engine's
day-by-day event walk doesn't guarantee two separate runs return trades in the same row order even when
the trade sets are identical). Arm A's own figures additionally match the study's separately-reported
degenerate-case parity check (`_sort_key="momentum"` vs. raw production, byte-identical, same two numbers)
— three independent runs now agree exactly. The promotion changed no backtest behaviour relative to the
research finding it promotes.

**The caveat that must travel with this result, everywhere it's cited — stated here and in `config.yaml`'s
own comment on the key, not left implicit in either place.** The robustness test
(`research/relranking_robustness_2026-08-09/REPORT.md`, on the branch
`research/relranking-robustness-2026-08-09` — pushed to its own remote branch, not yet merged to `main` as
of this promotion) ran 20 paired perturbation draws (10 single-ticker drops, 6 start-date shifts, 4
tie-break reshuffles) and found the DIRECTION of B's advantage robust in every single draw — 20/20
positive, the unperturbed +1.993pp sitting exactly at the distribution's median and mode, B-only
admissions beating A-only admissions on mean P&L% in all 20 draws with no exception. **(Corrected
2026-08-12: ~11 of those 20 draws are informative — the 4 `shuffle_tiebreak` draws are inert by
construction, since admission sorts on a continuous float and exact ties never bind. See the
RETROSPECTIVE CORRECTION under the robustness-test section above. The verdict is unchanged; the draw
count was overstated.)** **But the MAGNITUDE
is partly dependent on one ticker**: dropping AMAT alone compresses the measured advantage from +1.993pp
to +0.427pp — about 21% of the typical value, by far the largest compression any single perturbation
produced, and on its own (if it were the only data point available) below this project's own ~1.08pp
noise-floor bar for a "decisive" result. It does not reverse or come close to reversing the finding, and
19 of the other 20 draws clear the noise floor comfortably — but "relative-rank ordering admits genuinely
better trades" is the finding to rely on going forward, not the specific +1.99pp point estimate.

**This is an OPEN CANDIDATE pending forward validation, not a closed study, and not a production
decision.** The code is promoted, tested, and verified to reproduce the research finding exactly; the
BEHAVIOUR is not adopted — `admission_ranking_mode` stays at its default (`"absolute_momentum"`) until the
forward shadow-comparison (see next section) provides real, live-operation evidence, not merely a stronger
backtest. `trading212_invest.live` remains `false`, untouched by this work, so nothing here carries any
real-money risk regardless of the config default. Full suite (1,059 tests) green. Committed locally on the
`feature/relative-rank-admission-2026-08-09` branch (worktree
`c:\GitHub\investment-app-relranking-promotion`) — not pushed, not deployed, pending explicit review.

## Forward shadow comparison for the relative-rank admission promotion — recording only, OPEN CANDIDATE unchanged (2026-08-09)

**The only forward evidence stream for the relative-ranking promotion's OPEN CANDIDATE decision — built
immediately after the promotion above landed, per its own stated dependency (the production ranking has
to exist in `analysis/signals.py` before anything can shadow-compare against it).** A backtest, however
carefully validated (the study itself, plus a 20-draw robustness test — see the two sections above), is
still historical; this is what actually answers whether relative-rank ordering beats absolute-momentum
ordering in live operation, going forward. Built in its own dedicated worktree
(`c:\GitHub\investment-app-relranking-shadow`, branch `feature/relranking-shadow-comparison-2026-08-09`,
branched from the promotion branch) per this project's absolute git-worktree rule. **Recording and display
only — no trading-behaviour change, no sizing/exit change, `trading212_invest.live` untouched.**
`admission_ranking_mode` still defaults to `absolute_momentum`; whichever mode is actually configured is
the one that trades for real, the other is recorded only.

**The structural limitation this whole feature exists inside of, stated here first because it shapes
every other design choice below.** Only ONE ranking mode is ever genuinely live-traded at a time. A real,
realised outcome can only ever exist for a candidate the currently-ACTIVE mode actually admitted and
bought — a candidate the INACTIVE mode would have uniquely admitted was never bought under real (or demo)
money and NEVER WILL have a realised outcome for that specific decision. This is not a "small sample, wait
longer" gap the way most of this project's other statistical gates are — it is permanent for any row where
the two rankings disagree, for as long as that mode stays inactive. The whole design below (recording,
reconciliation, and especially the display) is built to keep this asymmetry explicit rather than blend it
into one misleading number.

**What this means for the 90-day review specifically, stated plainly rather than left for someone to
discover in November: the review will NOT be able to answer "does B beat A," the symmetric comparison the
backtest's own paired per-decision instrument could make (both arms fully simulated, so both sides' unique
admissions had real, comparable outcomes). Live, only one side is ever observed.** The review can answer
"did the active mode's own unique admissions perform well" — a real, meaningful, but one-sided question —
never "would the inactive mode's unique admissions have performed better or worse than the active mode's
own." The inactive mode's counterfactual is permanently unobserved for as long as it stays inactive,
regardless of how long the comparison runs or how large `active_unique["count"]` grows. Treat any review-
time conclusion as bounded by this from the start, not as a limitation to explain away after the fact.

**A partial workaround exists, worth running as a supplementary analysis at review time, not built now.**
The inactive mode's own unique admissions (`inactive_unique` rows) were never bought, but the ticker's real
subsequent price history exists regardless — the same `price_data` this system ingests for every candidate
whether or not it was ever traded. A research script, run once at review time (not a live/scheduled
mechanism — this is retrospective analysis, not a second trading path), could walk each `inactive_unique`
row's own `decided_at`/`ticker` forward through real subsequent prices using the SAME shared decision
functions the backtest already uses (`analysis/decision.py:evaluate_exit_reason()` for the exit, the
already-recorded `position_gbp`/`momentum_pct` for entry sizing) and compute a counterfactual P&L exactly
as the backtest would have realised it — not a real fill, but the same fill assumptions this project's own
backtest figures are already built on and already understood to carry (same-day-close entries, the
realistic intraday-touch `STOP_LOSS` model, etc. — see "What every backtest figure in this project does and
does not model" above). This does not produce a second real outcome — it produces a backtest-quality
estimate for the one side live trading structurally cannot observe, which is the only way to get anything
resembling a symmetric comparison out of a single live account. Not implemented as part of this feature;
recorded here as the intended review-time analysis so it isn't reinvented from scratch, or skipped, in
November.

**RECORDING — `data/database.py:AdmissionRankingShadow`, a new table, written from inside the real
submission path, never a parallel computation.** For every gate-passing candidate the EOD decide phase
queues, `brokers/trading212.py:_submit_pending_orders()` — right after fetching the account's real, live
`total_val`/`invested`/`free_cash` and the real eligible `pending` list, BEFORE the real order-placement
loop mutates any of it — calls `record_admission_ranking_shadow()`, which runs
`_simulate_dual_ranking_admission()`: the identical skip-and-continue capacity walk twice, once sorted by
momentum, once by relative rank, both reusing the SAME shared `_capacity_fit_reason()` function the real
submission loop and the Dashboard's own admission projection already use — never an independently
re-derived approximation of that rule. Each candidate's own rank position and admit/reject verdict under
BOTH rankings, plus `momentum_pct`/`relative_rank_pct`/`position_gbp`/`decided_at`/`ticker`, are written
self-contained into the new table (never joined back to `pending_orders`, which genuinely deletes resolved
rows after `pending_orders_retention_days` — this table has no such retention limit). A new
`pending_orders.relative_rank_pct` column (populated at decide time, alongside the already-existing
`momentum_pct`, regardless of which mode is active) supplies the "other" ranking's own raw value the
shadow simulation needs but the live decision itself never uses.

**Wrapped so a failure can never affect the real order-placement loop — verified, not just designed.**
`record_admission_ranking_shadow()`'s own call is wrapped in the caller's try/except; the function itself
also never raises internally (every DB write is individually best-effort). Two dedicated tests
(`tests/test_admission_ranking_shadow.py`) deliberately inject a `RuntimeError` — once into the dual-
ranking simulation itself, once into the later buy-order-linking step inside the real submission loop's
own per-row try/except — and confirm the real BUY still places successfully and the real `pending_orders`
row still resolves to `SUBMITTED`, exactly as if the shadow code did not exist. This mirrors the "verified
to actually fail" discipline this project already applies to its live/backtest parity tests, turned around
to prove a fail-SAFE property instead of a fail-LOUD one.

**OUTCOME RECONCILIATION reuses the existing `pnl` table directly — never reimplements
`_reconstruct_pnl()`'s own FIFO BUY/SELL pairing.** When the real submission loop places a genuine BUY for
a candidate the active mode admitted, `_log_order()` (now returning the new `invest_orders.id` — a small,
additive change every existing call site already ignored the return value of, so nothing else changed)
lets the shadow row record `real_buy_order_id`. `reconcile_admission_ranking_shadow_outcomes()` — called
from the hourly `market_hours_check` job, right after the exit check that is what actually causes new
`pnl` rows to appear — finds shadow rows with a `real_buy_order_id` set but no outcome yet, and does a
plain `SELECT ... FROM pnl WHERE buy_order_id = :id`. Never raises; a position still open just isn't
picked up yet and is retried automatically next cycle.

**THE DISPLAY keeps the structural asymmetry explicit, never blended into one number.**
`output/performance.py:compute_admission_ranking_shadow_summary()` classifies every row, using its OWN
recorded `active_mode` (not today's config — a row stays correctly classified even across a future
config flip), into three buckets: **shared** (both rankings would admit it — real trades exist, but carry
NO discriminating signal, since the inactive ranking would have made the identical trade); **active-mode-
only** (the real, discriminating signal — real trades the active ranking chose that the alternative
wouldn't have; win rate/mean P&L% shown once `closed_count` clears 5, the same house threshold the
existing Performance Metrics section already uses); **inactive-mode-only** (never actually traded — shown
as a COUNT ONLY, explicitly labelled as having no live outcome and never will for these decisions, never
shown as a fabricated 0% win rate). Dashboard: a new "🔀 Relative-Rank Admission — Forward Shadow
Comparison" expander, pure DB reads (`get_admission_ranking_shadow_rows()`), confirmed by a dedicated
render-path test that patches both `Trading212Client` and `yfinance.Ticker` to raise if invoked at all.

**THE REVIEW TRIGGER — derived from the first recorded decision, never hardcoded, because a CLAUDE.md note
alone will not reach a human in 90 days.** Fires on ALL THREE of (i) 90 days from
`data.database.get_admission_ranking_shadow_first_decided_at()` (the actual earliest recorded row, not a
build-time constant — a delay in shipping this feature does not silently shorten the window), (ii)
`ADMISSION_SHADOW_REVIEW_MIN_PAIRED_OUTCOMES = 50` active-mode-only closed outcomes (the only population
that can ever carry real signal — see the structural-limitation paragraph above), and (iii) no single
ticker accounting for more than `ADMISSION_SHADOW_REVIEW_MAX_SINGLE_TICKER_SHARE = 0.40` of the measured
effect. **The sample floor, not the display's own smaller 5-closed gate, is the number that decides when
the comparison is trustworthy enough to act on, not merely worth showing.**

**Raised 30 → 50, and the concentration ceiling added, on 2026-08-10 — BEFORE the flip, before any
forward data existed, and that timing is the whole point.** Revising a review threshold once results are
partly in is precisely what pre-registration discipline exists to prevent, so the only legitimate moment
to change it is before the clock starts. The original 30 was reasoned as roughly 6x the house 5-closed
gate — enough that one closed trade moves the win rate only ±3.3pp — which is sound for SAMPLING noise
and misses the failure mode this promotion has a documented precedent for: **CONCENTRATION**. The
robustness test (`research/relranking_robustness_2026-08-09/REPORT.md`) found B's advantage robust in
direction across 20/20 perturbation draws but partly carried in magnitude by one name — dropping AMAT
alone compressed it from +1.993pp to +0.427pp (~21% of typical), out of a **607-ticker** pool. At n=30
forward outcomes a single position could plausibly carry the entire read, a different and larger risk
than the per-trade swing 30 was sized against.

**Condition (iii) is different in kind from (i) and (ii), and the code says so explicitly.** The first
two are "has enough accumulated" — monotonic, satisfied by waiting. Concentration is a property of
whatever sample exists and can flip back and forth as outcomes land. A concentrated sample does not mean
"not yet" in the same sense; it means the sample that exists cannot answer the question, so the review
**reports the concentration and waits** rather than concluding. **The metric is stated exactly rather
than left to a reader's inference** (per this file's own research-reproducibility standard): each
ticker's share of summed **absolute** `pnl_pct` across active-mode-unique closed rows —
`sum(|pnl_pct|) for that ticker / sum(|pnl_pct|) for all of them`, grouped by ticker so repeat entries of
one name count once. Absolute, not signed, because a large loser dominates the read exactly as much as a
large winner, and a signed share would let a +40% and a −40% name cancel to zero and report a
concentrated sample as diverse. Two alternatives were considered and rejected: share of trade **count**
(misses the case entirely — AMAT carried magnitude, not frequency) and leave-one-out recomputation of
`mean_pnl_pct` (the most literal analogue, but it measures sensitivity of a mean rather than
concentration of contribution, and becomes unstable when the full-sample mean sits near zero — a state
this comparison could genuinely occupy if the two rankings perform similarly, which is a live possible
outcome, not a remote edge case).

**0.40 is a judgement, not a derived figure, and is deliberately far stricter than the AMAT precedent it
is calibrated against** (AMAT carried ~79%). The asymmetry is intentional: the backtest had 607 tickers
and thousands of trades to dilute one name, where 50 forward outcomes do not, so the forward guard must
trip earlier to catch the same shape of problem. A ticker sitting between 40% and 79% would have passed
unremarked in the backtest and is exactly the case worth pausing on here. **A load-bearing consequence,
pinned by its own test so nobody special-cases it away later**: with fewer than three contributing names
some ticker necessarily holds >40% arithmetically, so the guard always trips on a very small sample. That
is the correct answer, not an artefact — a two-name sample cannot distinguish a ranking rule from one
lucky position — and it means this guard doubles as a diversity floor alongside the raw outcome count.
**"Cannot measure" is likewise never treated as "no concentration problem"**: an unmeasurable sample (no
closed rows, none carrying `pnl_pct`, or every outcome exactly flat) blocks the trigger, as does omitting
the concentration argument entirely — the same degenerate-case discipline required by this file's
"A check that reports clean for a reason unrelated to the thing it was checking" entry (no directional
"above"/"below" here on purpose: these two sections arrived on separate branches and their relative
order depends on merge sequence, which is exactly how a cross-reference goes stale).

**The cost is time, and it is real and accepted.** With the account fully deployed and the next admission
wave gated on max-holding exits from early September, 50 paired outcomes is plausibly a six-month
proposition rather than three — which also means **the sample floor, not the 90-day date, is the binding
constraint**, and the date is close to irrelevant in practice. Accepted deliberately: an honest answer
arriving slowly is worth more than a fast one a single name could have produced.

**One implementation, not two** — `_classify_shadow_row_admission()` was extracted so
`compute_admission_ranking_shadow_summary()` and `compute_admission_ranking_shadow_concentration()`
cannot drift apart on what "active-mode-unique" means. They must agree exactly, since the guard measures
precisely the population the summary reports stats for, and the concentration function takes the FULL row
list and filters internally rather than accepting a pre-filtered list — a caller-side filter would let
every call site re-implement the classification, which is the duplication this project's own standing
invariant forbids.

**The weekly summary email carries this forward, since that's the mechanism actually intended to reach a
reader within the window — a CLAUDE.md entry alone is not.** A single compact line, in both the plain-text
and HTML bodies: `"Forward comparison: 47 paired outcomes (floor: 50) — next check from 2026-11-07.
Largest single contributor NVDA at 22% of the measured difference (ceiling 40%)."` before the trigger
fires, changing to `"Forward comparison: trigger conditions met — 62 paired outcomes (floor 50 cleared),
2026-11-07 or later reached. …"` once all three conditions clear. When concentration exceeds the ceiling
the suffix instead reads `"AMAT accounts for 62% of the measured difference, above the 40% ceiling, so
the sample cannot yet separate the two rankings."` — **the concentration figure is shown whenever it is
measurable, not only when it blocks**, so a reader watches one name's share build toward the limit rather
than being surprised by a blocked trigger at the end. **Deliberately avoids the
word "review"** (and every other word in this project's own established, tested weekly-email tone
standard's forbidden-word list — `tests/test_briefing.py`'s `_FORBIDDEN_WORDS`, which already bans
"review" as call-to-action language) — the MEANING the review-trigger needs to convey is expressed in
neutral, factual wording instead, matching this email's existing calm/no-call-to-action design rather than
being treated as an exception to it.

**The AMAT-dependency caveat travels with this feature too, so a forward number is never read as
confirming or refuting the specific +1.99pp backtest figure.** The backtest robustness test found B's
DIRECTION robust in 20/20 perturbation draws but its MAGNITUDE partly dependent on one ticker (AMAT —
dropping it alone compressed the measured advantage from +1.993pp to +0.427pp). A forward shadow result
— however many paired outcomes accumulate — answers "does relative-rank ordering admit genuinely better
trades in live operation," not "does it beat baseline by exactly 1.99 percentage points" — the same
qualitative-finding-not-point-estimate framing the robustness test itself already established.

**Tests**: 32 new tests in `tests/test_admission_ranking_shadow.py` (the dual-ranking simulation's
correctness including a missing-value NULL-sorts-last case, idempotent first-write-wins recording, both
fail-safe injection proofs, reconciliation against real seeded `pnl` rows, the summary aggregation's own
structural-asymmetry behaviour, and the review-trigger's date-derivation), 8 new Dashboard render tests in
`tests/test_main_ui.py` (including the render-path-never-calls-network proof), 5 new weekly-email tests in
`tests/test_briefing.py` (including the forbidden-tone-word check on both the pre- and post-trigger
wording). Full suite green. Committed locally on the `feature/relranking-shadow-comparison-2026-08-09`
branch (worktree `c:\GitHub\investment-app-relranking-shadow`) — not pushed, not deployed, pending explicit
review.

## Candidate Queue diagnosed and narrowed from a 2,197-row full-history dump to a ~25-row operational preview (2026-08-09)

**Raised as a symptom, diagnosed before any UI change was made.** The Status page's Candidate Queue was showing 2,197 rows and was slow to load — investigated first, per this project's own standing practice of diagnosing before treating a symptom, rather than reaching straight for paging.

**Part 1 — diagnosis: legitimate volume, no pipeline defect.** Queried production directly rather than inferring from code: 1,480 `REJECTED_CAPACITY` + 331 `SUPERSEDED` + 308 `PENDING` + 56 `SKIPPED_PRICE_MOVED` + 22 `SUBMITTED`, every row dated 2026-07-31 → 2026-08-07 — well inside the 30-day `pending_orders_retention_days` window, nothing eligible to purge yet. All 308 `PENDING` rows were from the immediately-preceding trading day (Friday), zero trading days elapsed without resolution — not a stuck pipeline. `purge_old_pending_orders()` confirmed genuinely running (not merely wired up): the most recent `eod_completion` metadata record carried `"pending_orders_purged": 0`, and the *presence* of that key — this project's own established backstop-verification pattern, see "Data-quality backstop was silently broken since deploy" above — proved the step executed without raising; `0` was correct given nothing had aged out yet. Nightly volume genuinely runs ~273–308 `REJECTED_CAPACITY` rows/trading night, matching this file's own long-documented "318 queued, 17 admitted" baseline. The 331 `SUPERSEDED` rows traced to a one-time, already-documented 2026-08-02 manual queue clear-and-regenerate (see "Urgent pre-submission verification" in CLAUDE_HISTORY.md) — nothing currently writes that status; not an ongoing mechanism.

**Part 2 — the render cost, measured, not assumed. The initial hypothesis (a per-row capacity walk) was wrong, and the measurement caught it before any code changed on that basis.** `project_pending_order_admission()` was suspected of recomputing the momentum-ranked capacity walk once per displayed row (308 walks/render). Reading the code first showed this wasn't true — it already walks the `PENDING` list exactly once, mutating running `invested`/`free_cash` state as it goes, called once per render via a `@st.cache_data` wrapper — and a direct isolated timing measurement against a real, freshly-copied local production DB confirmed it: ~0.075s for the single 308-row walk, vs. ~0.049s for `get_pending_candidates()`'s own SQL query alone. The dominant cost was neither query — it was `get_pending_candidates()` pulling and pandas-processing (`.apply()`, `.groupby().transform()`, two `.map()` calls) the **full 2,197-row table**, every resolved status included, on every cache-miss render. Measured end-to-end data-prep time, 5 runs each, real production-sized data: **0.301s mean (before) → 0.110s mean (after)**, driven entirely by narrowing query scope, not by hoisting an already-correct walk. Worth having fixed now rather than later: `pending_orders`' own steady-state size (per "Bounded retention for pending_orders" in CLAUDE_HISTORY.md) is expected to plateau around 6,000–7,000 rows under 30-day retention — the old full-table approach would only have gotten slower approaching that ceiling.

**The fix — new, narrower functions in `output/performance.py`, main.py reduced to a thin renderer.** `get_pending_candidates_ranked()` — `PENDING`-only, `ORDER BY admission_rank_value DESC, decided_at ASC` (**corrected 2026-08-10** — written as `momentum_pct DESC` here, but the shipped code was renamed to `admission_rank_value` by the 2026-08-09 promotion), the exact order `brokers/trading212.py:_load_pending_orders()` (the real submission walk) uses. `compute_queue_preview_cutoff()` — pure function, projected-`ADMIT` count + a fixed margin (8), clamped to `[20, 25]` (never more than the row count); falls back to a plain top-25 slice when no row carries a real projection at all (e.g. no stored snapshot yet). `compute_momentum_strength_bands()`/`momentum_strength_label()` — the qualitative Weak/Moderate/Strong/Very Strong label (originally built 2026-08-02, briefly dropped during the initial narrowing pass, restored the same day once corrected: a qualitative read on how a candidate ranks against today's pool is directly relevant to "what's likely to trade tomorrow," not investigation detail to be trimmed away) — moved here from `main.py` for the same testability reason every other display computation in this module already lives here. `get_candidate_queue_preview(config)` ties all of this together: reads the ranked `PENDING` pool, merges in `project_pending_order_admission()`'s projection (defaulting to `PROJECTION_UNAVAILABLE` per ticker exactly as before), computes `strength` bands over the **full** pool (not just the shown slice — a candidate near the cutoff boundary would otherwise be judged "Weak" against an already-truncated, high-momentum-only sample rather than the real day's distribution), and returns a `fully_deployed` flag (true only when every row carries a real, non-`PROJECTION_UNAVAILABLE` projection and none of them is `PROJECTED_ADMIT`).

**What the Status page shows now.** Only still-`PENDING` decisions, ranked top-down in real submission-priority order with an explicit Rank column, cut off with a visible "showing top N of M pending" caption so a truncated view is never mistaken for an empty tail. Columns: Rank, Ticker, Momentum, Strength, Sized Position, Status. Resolved decisions (`REJECTED_CAPACITY`/`SUBMITTED`/`SKIPPED_*`/`SUPERSEDED`) are no longer queried or shown on this page at all — untouched in the database, still fully available for research via the unchanged `get_pending_candidates()` (e.g. the selection audit's own use of `REJECTED_CAPACITY` volume, see "Descriptive selection audit" above). The status-filter multiselect is gone (nothing left to filter, since only one status population is ever shown). **A real production edge case surfaced and handled explicitly, not just correctly computed**: this account is currently fully deployed, so every `PENDING` candidate projects `PROJECTED_REJECT_CAPACITY` and none `PROJECTED_ADMIT` — without the `fully_deployed` note, a wall of "reject" rows reads indistinguishably from a broken projection; the caption now states plainly that the account is full.

**Tests and deploy.** 36 new/updated tests across `output/performance.py`'s and `main.py`'s test files, including a render-path test proving no T212 or yfinance call happens (patches both to raise if invoked at all), matching the sector/crisis/T212-snapshot pattern this project already established. No changes to `analysis/decision.py`, the submission path, or `config.yaml` — display-side only. Deployed via the standard GitHub Actions → signed webhook pipeline; independently re-verified post-deploy (not just trusted from the deploy log) via a direct SSH check that all three services were active and the live `main.py` on the server contained the new render code, not merely that `git rev-parse HEAD` had moved.

## `get_live_stock_candidates()` cold-start cost — root-caused and fixed at the source (2026-08-09)

**Symptom: the Status page took 20-30 seconds on first load, fine on every subsequent load.** Profiled directly (a fresh worktree, a cold in-process cache, real production-sized data) rather than guessed at — see the profiling task's own report for the full section-by-section breakdown. Two specific suspicions raised alongside the symptom (Scheduler Health's Wikipedia cross-check hitting the network at render time; the T212 instrument-mapping expander resolving instrument IDs per ticker) were both **ruled out directly**: the Wikipedia check reads a stored `system_metadata` value written only by the scheduled `membership_refresh` job, never called live from any render path; the instrument-mapping expander doesn't exist on the Status page at all — it was relocated to the Instruments page back on 2026-08-03, and where it lives now (`load_all_instrument_resolutions()`) is a single flat table read, no per-ticker resolution.

**Root cause, measured precisely.** `analysis/universe.py:get_live_stock_candidates()` → `get_all_known_stock_tickers()` (cheap, one `SELECT DISTINCT`, ~0.06s, 1,205 tickers) → a loop calling `is_member()` → `get_membership_windows()` for every one of those tickers. `get_membership_windows()`'s own in-process cache (`_membership_cache`, a module-level dict) only ever gained an entry *after* that specific ticker's own individual query — meaning a cold process paid ~1,205 sequential `SELECT start_date, end_date FROM universe_membership WHERE ticker = :t` round trips, measured directly at ~17ms each, ~20-30s total. Warm (the cache persists for the process's entire life, cleared only by the daily 21:15 UTC `membership_refresh` job's `clear_membership_cache()` call): 0.025-0.05s — an 800-1000x difference, exactly matching the reported first-load/fine-thereafter shape. **Confirmed hitting four independent render-path call sites, not one**: the Status page's `_price_coverage()` (Data Coverage card), the Status page's T212 Invest Account section (a redundant second call — free only because the first one already warmed the cache in the same render), the Backtest page's `live_universe_snapshot()` (unconditional, before its own gating button — independently measured at 26.33s cold in its own fresh process), and `output/performance.py:get_ticker_status()`'s default `tickers=None` path (already bypassed for its one caller, `get_brief_cards()`, by an earlier, narrower 2026-08-04 fix — see below).

**Not a new bug — the same root cause resurfacing at un-bypassed call sites.** `get_ticker_status()`'s own docstring already documents this exact function measured at ~11s on production back on 2026-08-04, and the fix at the time added a `tickers` parameter so its one real caller (Dashboard's `get_brief_cards()`) could skip the call entirely. That fix never touched `get_live_stock_candidates()`/`get_membership_windows()` themselves — only the one call site it was scoped to. The other three call sites were untouched, and the cost grew from ~11s then to ~23-30s now as `universe_membership` grew under the "Universe coverage remediation" broadening (see above). Per explicit instruction, this fix does not repeat that pattern: no new bypass parameter was added anywhere; the fix is at the shared root, so all four call sites (and any future one) benefit at once.

**The fix — bulk-load `_membership_cache` in one query, not one query per ticker.** `analysis/universe.py:_bulk_load_membership_cache()` runs `SELECT ticker, start_date, end_date FROM universe_membership ORDER BY ticker, start_date` once, groups rows by ticker in Python, and populates `_membership_cache` for every ticker the table has at least one row for — triggered lazily on `get_membership_windows()`'s first call in a process (a new `_membership_cache_bulk_loaded` module-level flag), not eagerly at import time. `get_membership_windows()`'s own signature, return type, and STOCK_MEMBERSHIP-fallback/ETF-empty-list semantics are all unchanged — a ticker with zero DB rows still falls through to the STOCK_MEMBERSHIP baseline or `[]` exactly as before, since the bulk load only ever populates entries for tickers the query actually returned. `clear_membership_cache()` now resets both the dict and the bulk-load flag, so a post-refresh call correctly re-triggers one fresh query rather than silently serving a now-cleared-but-still-"loaded" empty cache.

**Verified, not assumed — every one of the task's own required checks, before this was reported ready:**
- **Correctness (the stated priority over speed)**: `get_live_stock_candidates()`'s full output, sorted, captured against the same real production DB copy both before (`git stash` back to the pre-fix code) and after the change — **byte-for-byte identical, 503 tickers, zero diff.** Not a count comparison.
- **Timing, both affected pages, fresh processes**: Status page's `_price_coverage()` 30.40s → **0.99s**; Backtest page's `live_universe_snapshot()` 26.33s → **0.056s**.
- **`clear_membership_cache()` + repopulation**: verified directly against the real DB — clears both the dict and the flag, a subsequent call re-triggers exactly one fresh query, and the repopulated output matches the original population exactly.
- **A genuine test-isolation gap, found by the full suite and fixed, not worked around**: `tests/test_execution.py::test_get_all_watchlist_entries_retains_ticker_after_membership_ends` failed on the first full-suite run (passed in isolation) — the bulk load's module-level `_membership_cache_bulk_loaded` flag, once set `True` by an earlier test's own fresh in-memory DB, silently persisted into a later test using a *different* fresh DB (each `db_engine` fixture instance is its own isolated in-memory database), serving stale cached windows against the wrong DB. `test_universe.py` already had its own narrower autouse fixture clearing this cache, but no other test file did — the old per-ticker cache had the identical latent risk, just far less likely to collide since it only ever cached tickers a test explicitly named, where the new bulk load pulls in every ticker present in whichever DB triggered it. Fixed at the shared fixture level: `tests/conftest.py:db_engine` now calls `clear_membership_cache()` on both entry and exit, closing the gap for every test file that uses it, not just the one that happened to need it before. Full suite: **1,041 passed**, confirmed twice.

**A function that reads as one query is not proof it only issues one query — this is the SECOND time this exact blind spot has produced a real, measured render-path cost, not a one-off.** The original UI-blocking audit (see CLAUDE_HISTORY.md's "UI render-path blocking audit" entry) scored both Backtest's `live_universe_snapshot()` (Finding 8: "bounded local DB work... currently just two `SELECT DISTINCT` queries") and the Status page's duplicate `get_live_stock_candidates()` call (Finding 9: "local DB only, pure inefficiency") as cheap — both corrected in that same history entry (see its "Correction to Findings 8 and 9" section, added the same day) once cold measurement showed one of those "two `SELECT DISTINCT` queries" was actually ~1,205 of them. This is the identical shape as "UI render-path blocking audit, finding 4 — Dashboard open-position cards, missed by the original audit" above: that audit's risk categories (yfinance calls, T212 calls, long full-universe *computations*) had no slot for "a call that reads as one database query in isolation but internally fans out into hundreds of sequential per-ticker round trips, cheap only once an in-process cache happens to already be warm." Neither miss was catchable by reading a one-line description of what a function does, or by re-checking against the specific symptom an earlier fix already closed (the same gap "Data-quality backstop was silently broken since deploy" and other sections above have already named for different mechanisms) — only by tracing into the callee's own internals or measuring cold. **Standing lesson for any future audit or performance claim in this codebase**: "it's just a DB read" is a claim about what a function returns, not about how many round trips it costs to compute it — verify the latter by measuring, not by reading a call site in isolation, whenever a function's own internals aren't already known to be a single query.

## A check that reports clean for a reason unrelated to the thing it was checking — a class, not ten incidents (standing requirement, 2026-08-10, extended 2026-08-11, 2026-08-12, 2026-08-22 and 2026-08-23)

**Five separate findings this week share one shape, and naming the shape is more useful than
recording them separately.** In each, a check was reasoned about — correctly — for the case its
author had in mind, shipped with that reasoning written down as a justification, and then reported
a clean, confident result in a case where the reasoning inverts rather than merely weakens. None was
caught by a test, because each one's tests exercised the expected case the reasoning described.
The class is broader than code guards: instances 4 and 5 are a documentation record and an
operational poll loop, and they fail identically.

1. **NULL-ordering collapse** (this file's "Candidate Queue ordering" entry below). *"SQLite sorts
   NULLs last in DESC, so an unknown-strength candidate is deprioritised."* True while SOME row
   carries a value. When EVERY row is NULL the key contributes no ordering at all and the sort
   collapses onto whatever tiebreak exists — here, insertion order, i.e. alphabetical. The guard
   didn't weaken at the edge; it reversed, from "deprioritise the unknown" to "ignore the signal
   entirely."
2. **The deployment guard passing n5/n10** (see "Position-count study" above). Mean deployment
   tracked baseline within 0.45pp across every arm, so the guard reported clean — but two arms had
   never built the portfolios their names described (realized ~12 positions against nominal targets
   of 5 and 10). The guard measures capital deployed, which is genuinely invariant to how many
   positions that capital is spread across, so it is structurally incapable of detecting the one
   failure those arms actually had.
3. **The drawdown ceiling comparing a gap rather than a direction** (same study). The ceiling was
   expected to bind hardest at n5; it never bound at all, because n5 never concentrated enough to
   produce the effect the ceiling existed to catch. A threshold on a magnitude cannot fire when the
   mechanism that would generate the magnitude never engaged.
4. **A rename accurately recorded in one place and stale in two others** (found while fixing
   incident 1). The 2026-08-09 promotion's own CLAUDE.md entry correctly documented renaming the
   sort column to `admission_rank_value`, while two older sections continued to describe the same
   line as `ORDER BY momentum_pct DESC`. **This is worse than uniformly stale documentation, not
   better**: the accurate entry is what supplies the confidence that stops anyone checking the
   others. A reader who verifies the rename was handled finds the correct record, concludes yes,
   and never reaches the two that still describe the old column. Uniformly wrong docs get
   distrusted as a set; a correct record adjacent to stale ones vouches for them.
5. **A poll loop that could only ever produce one answer** (during this incident's own deploy). A
   background watcher was armed to wait for the server's `HEAD` to reach the newly pushed commit,
   comparing against a **fabricated full SHA** — only the short `5ed6ef8` was known, and the
   remaining characters were filled in rather than looked up. It would have polled for 15 minutes,
   matched nothing, and reported a timeout that reads as *"the deploy failed"* when nothing had
   failed. The purest instance in this list: not a check whose domain was too narrow, but a check
   with no true-branch at all, whose single reachable output is indistinguishable from a real
   finding. Caught before it reported, by noticing the SHA had been invented; the correct fix was
   `git rev-parse HEAD`, which costs one command.
6. **A circuit breaker that fired correctly in the exact episode it was designed for, and was
   structurally incapable of seeing another episode of the same kind** (the long/short study's rally
   breaker, 2026-08-11 — see that study's own section below). It was built to catch momentum crashes
   (a violent rally off a bottom, when the worst-performing names rebound hardest) and deliberately
   gated on the index already being ≥20% below its trailing 252-day peak, because an ungated "exit
   shorts on any strong up day" would fire constantly in ordinary bull markets. It fired exactly as
   intended in the canonical case: 2009-03-13, four trading days after the 2009-03-09 bottom. **And
   the single worst episode in the whole sample — 2020-06→2021-09, responsible for 26.8% of the short
   sleeve's entire loss — produced ZERO firings**, because across those 310 trading days `^GSPC`'s
   drawdown from its trailing peak reached a minimum of only −11.1% and was at or below the −20% gate
   on **0 of 310 days**. The gating condition that made the breaker safe is precisely what made it
   blind: a momentum crash occurring while the index is at all-time highs (beaten-down names ripping
   upward) cannot satisfy a drawdown precondition, by construction. **The pre-registration explicitly
   anticipated this class and guarded the wrong case** — it pre-committed to reading "0 fires" as
   "untested, not safe", which would have caught a breaker that never fired at all, and had nothing to
   say about one that fires correctly *somewhere* and is blind *elsewhere*.
7. **A comparison whose measuring instrument was structurally incapable of seeing the thing that
   differed** (exit-mechanics promotion, 2026-08-12). A Layer 2 test asserted that
   `stock_max_holding_days: 0` (the new disable sentinel) and `9999` (a cap longer than the test
   window) were behaviourally identical — both should mean "never exit on age". The **backtest half
   of the assertion passed**; the **live half failed**. The backtest comparison is a `Counter` of
   `(ticker, direction)` events, which is **size-blind by construction** — and size was precisely
   what differed: a finite 9999 feeds VaR a 9999-day sizing horizon, scaling positions by
   `sqrt(9999)≈100` instead of `sqrt(30)≈5.5`, roughly **18× smaller**, with some then falling below
   `min_position_gbp` and rejected outright. **Had only the backtest been checked, a false premise
   would have been confirmed** — the instrument would have reported "identical" not because the arms
   were identical but because it could not represent the dimension on which they differed. This is
   also the standing argument for Layer 2 driving BOTH sides rather than trusting either alone.

   **The practical trap this leaves behind, worth knowing before touching the config**: the obvious
   way to express "no holding cap" — set a large number — **silently shrinks every position ~18×**,
   producing a configuration that *looks* like the tested one and behaves nothing like it. Use the
   `0` sentinel, which disables the age EXIT while leaving SIZING on a real horizon. Warned about
   directly in `config.yaml` at the key itself, where the edit would actually be made.

8. **A result PRE-AUTHORISED as benign, which then occurred for a completely different reason**
   (post-EOD verification pass, 2026-08-13). The point-in-time fundamentals recorder shipped
   2026-08-12; the verification pass written for its first cycle stated in advance that
   `candidate_fundamentals_snapshot` "may legitimately be LOW OR ZERO — the forward cycle covered ~25
   tickers against ~300 candidates, so overlap on night one could be small. That is correct
   behaviour, not a failure." The table was **zero**. It was zero because
   `snapshot_candidate_fundamentals()` was **never reached**: a `NameError` on an undefined `run_date`
   (the in-scope name in `_eod_job()` is `today_utc`) fired on the line building its argument list.
   **The pre-authorised explanation was also quantitatively plausible** — had the code run, 15 of 276
   candidates would have linked and 261 would have been unlinked, a genuinely sparse night-one
   result. A deterministic crash that would have recurred **every night, silently producing no
   dataset ever**, was one accepted assumption away from being invisible.

   **What makes this instance distinct from 1-7: the blind spot was installed deliberately, in
   advance, by the person doing the checking.** Instances 1-7 are guards whose domain turned out
   narrower than their author realised. This one is a guard that was **explicitly told not to fire**.
   Pre-authorising a result as benign removes exactly the scrutiny that would have caught it being
   non-benign — and it is more dangerous than an ordinary blind spot, because it comes with a written
   justification that suppresses the follow-up question at the moment it would otherwise be asked.

   **The rule this yields, and it is the most portable thing in this entry: pre-register the
   DISTINGUISHING CHECK, not the ACCEPTABLE VALUE.** "Zero is acceptable" is a statement about a
   number and cannot separate the two ways of reaching it. "If zero, confirm the function was
   actually reached" is a statement about a mechanism and separates them completely. Both cost the
   same to write in advance; only the second survives contact with a benign-looking failure. Applied
   here as `tests/test_fundamentals_recorder.py::test_the_eod_fundamentals_block_actually_succeeds_end_to_end`,
   which asserts **key PRESENCE** (`fundamentals_snapshot` in the completion record) rather than
   absence-of-error — the same discriminator, and for the same reason, as the data-quality backstop
   fix that this project already made once (see "Data-quality backstop was silently broken since
   deploy" above; that entry's own meta-lesson 2 — "a backstop that has not been *observed*
   completing successfully isn't a backstop" — is this bug exactly, recurring in new code nine
   months of project-time later).

   **A second, independent cause worth separating out, because it generalises past this bug: the
   existing test RE-IMPLEMENTED the code under test instead of executing it.**
   `test_a_failure_in_fundamentals_capture_cannot_reach_order_decisions` — deliberately written as
   "the single most important test here" — builds its own `details` dict and its own `try/except`
   mirroring the scheduler's, then injects a failure into that. It passes whether or not the real
   block works, and it passed throughout this incident. **A test that mirrors the code under test can
   only ever confirm the mirror.** It proved the block is HARMLESS; nothing tested that the block
   WORKS, and those are different claims. When a test's setup starts to look like a copy of the
   production code path, that is the signal to drive the real path instead — verified here by
   reintroducing the `NameError` and confirming the new test fails with the exact production error
   message while the isolation test still passes.

9. **A RATIO guard whose denominator is inflated by the very condition it exists to detect**
   (slot-count study, 2026-08-22). The single-ticker dominance ceiling —
   `max_t |Δ_t| / Σ_t |Δ_t|` over per-ticker P&L differences from baseline, ceiling 0.40 — was
   pre-registered to catch "this arm's advantage is really one name". It **PASSED at 0.244** for an
   arm whose own P&L was **59.99% a single ticker (NVDA)**. The denominator sums `|Δ_t|` across
   **227 contributing tickers**, overwhelmingly names *baseline held and the arm did not* — so the
   more concentrated the arm, the more disjoint the two trade populations, the larger the
   denominator, and the *smaller* the guard reads. **Concentration inflates the denominator faster
   than the numerator, so the guard is anti-correlated with the thing it measures over exactly the
   range where it matters.**

   **What makes this distinct from 1-8: nothing about the guard's domain is wrong, and no
   precondition gates it.** It computes what it says it computes, on every input, always. The defect
   is that the *statistic* stops tracking the concept once the two populations being compared differ
   enough in size — and it was calibrated on a case (the relative-rank/AMAT precedent) where they had
   thousands of trades in common. A guard can be well-defined, universally applicable, and still
   silently stop measuring its concept when the comparison's shape changes.

   **The rule: for any guard expressed as a RATIO, ask what moves the denominator, and whether the
   condition being detected moves it.** If detecting the condition also grows the denominator, the
   ratio under-reports exactly when it should fire. The countermeasure used here was cheap — pair it
   with a *within-arm* measure that has no cross-arm denominator at all (top-3 and top-ticker share
   of that arm's own P&L), which read **71.25%** and **59.99%** where the ratio guard read 0.244.

10. **A detector scoped to the wrong universe entirely** (EA / membership staleness,
   2026-08-23). `check_wikipedia_lag()` exists to detect "our membership copy has fallen behind its
   upstream". It iterated **`STOCK_MEMBERSHIP` — the frozen 40-ticker curated research universe —
   not the 503-ticker live pool**, so it was structurally blind to **463 of 503 live candidates
   (92%)** and reported **"0 disagreements"** while Wikipedia had already removed EA, a delisted
   company that then ranked on a frozen momentum figure for ten days and sat `PENDING` at £2,611.90.
   A correctly-scoped detector flags four stale members, not zero.

   **This is the same mechanism as instances already recorded for `scan_unverified_tickers()` and
   `detect_price_cliffs()` — a check applied to a different population than the one it is protecting
   — and it is now the third occurrence, which makes it the most-repeated shape in this class.**
   Worth stating as a standing question rather than a case: **for any detector, name the population
   it protects and the population it iterates, and confirm they are the same set.** Those are two
   different things and this codebase has now conflated them three times.

   **The tell was in the stored output the whole time and was read past: `"checked": 40`.** A count
   is only interpretable against its denominator; 40 looks like a healthy number until you know the
   pool is 503. Any check that reports a coverage count should report it as a fraction of the
   population it claims to cover, not as a bare integer. **Better still, a detector should report WHAT
   it iterated over, not merely how many** — `check_wikipedia_lag()` now records `our_pool_size` and
   `wikipedia_pool_size` alongside `checked`, so the scope is legible at a glance instead of being an
   integer a reader has to know the denominator to interpret. Pair this with the standing question
   above: naming the two populations makes the mismatch checkable AT REVIEW TIME rather than
   discoverable afterwards.

**The common structure**: each check is valid over a domain, the domain is narrower than the check's
stated justification implies (or, in instance 5, empty), and the out-of-domain case produces a
*confident, plausible-looking result* rather than an error or an obvious anomaly. That last property
is what makes this class expensive — a crash or an implausible number gets investigated; a plausible
pass does not. Note the two directions it runs in: instances 1-4 report **clean when something is
wrong**, instance 5 would have reported **failure when nothing was wrong**. Both are the same defect
— an output uncorrelated with the state being checked — and the second is not the safer one, since
a false alarm during a live deploy invites exactly the kind of unnecessary intervention that causes
real damage. **Instance 6 adds a third direction, and it is the most dangerous of the three: a check
that reports correctly in one case and is silent in another of the same kind — where the correct
report is what supplies the confidence that stops anyone checking the silence.**
**Instance 8 adds a fourth, and it is the only one that is self-inflicted at the point of checking:
the check was told IN ADVANCE not to fire, by a written pre-authorisation that was itself plausible.
The other seven are blind spots discovered after the fact; this one was installed on purpose, which
is why the countermeasure is procedural (pre-register the distinguishing check, not the acceptable
value) rather than a better guard.**
**Instance 9 belongs to the first direction but by a mechanism none of the others share: no domain is
exceeded and no precondition gates it — the guard's own STATISTIC stops tracking its concept as the
comparison's shape changes, and it does so monotonically in the direction of the thing being
detected.** Instances 1-8 are answered by asking "what is this check's domain, and is this input
inside it"; instance 9 is not, because the input is always inside the domain. It is answered only by
asking what makes the *denominator* move.
**Instance 10 is the first direction again, by the most repeated mechanism in this class: a detector
pointed at the wrong POPULATION.** It is answered by neither of the questions above — the domain is
fine and the statistic is fine; what is wrong is the set being iterated. The question that catches it
is a third one: **name the population the check protects and the population it iterates, and confirm
they are the same set.**

**A SUB-SHAPE OF THIS CLASS, IN THE TESTS RATHER THAN THE CHECK: A TEST THAT MOCKS THE MECHANISM IT
CLAIMS TO VERIFY CAN ONLY CONFIRM THE MOCK (2026-08-23).** The price-staleness gate's safety rests
entirely on ONE property — the staleness anchor is the pool's own high-water mark, never wall clock,
so an ingestion outage cannot read as 503 simultaneous delistings. Five live tests were written
naming that property, including one called
`test_stale_gate_does_not_fire_when_the_whole_pool_is_equally_stale`. **Replacing the anchor
function's body with `date.today()` — the exact defect they exist to prevent — passed all five**,
because every one of them mocked `_pool_max_price_date()` and supplied the anchor itself. The tests
verified the *consumer's arithmetic* and never the *anchor*, while reading as though they covered
both. Found only by injecting the defect; two direct tests of the anchor were then added and the
injection fails them.

This is the sibling of instance 8's finding — *a test that mirrors the code under test can only
confirm the mirror* — with mocking in place of re-implementation, and it is the more common of the
two because mocking is normal, correct practice everywhere else. **The rule: when a test's name
asserts a property, check which component actually decides that property, and confirm that component
is NOT the one being mocked.** If the mock supplies the answer the test claims to check, the test is
scaffolding, not coverage. Cheap to detect — inject the defect the test is named after and confirm it
fails — and this project's standing "verify a new test genuinely fails against the reintroduced bug"
discipline already prescribes exactly that; the gap here was running it against the consumer's logic
and not against the anchor.

**THE GENERALISATION — passing the case a guard was designed for is not evidence it covers the class
that case belongs to, and the pass is exactly what stops anyone looking further.** This is the
sharpest lesson this class has produced, it generalises well beyond any one study, and it is the same
confidence-supplying-correctness pattern as instance 4 (an accurate record adjacent to stale ones
vouches for them) applied to behaviour rather than documentation. A guard is nearly always designed
against a *motivating example* — the incident that prompted it, the canonical historical case, the
failure someone actually saw. Validating it against that example feels like validation and is not:
the motivating example is the one case the guard is guaranteed to handle, because it was built by
looking at it.

**The mechanism to watch for is a CONDITIONAL guard — one that fires only when some precondition
holds.** The precondition is the guard's real domain, and it is almost never coextensive with the
phenomenon the guard claims to cover. Instance 6 is the clean illustration: "momentum crash" is the
phenomenon; "index ≥20% below its trailing peak" is the precondition; and the two coincide in 2009
and come apart completely in 2020-21. Any precondition added to stop a guard from over-firing is
simultaneously a restriction on what it can ever see, and those are the same edit.

**The rule.** For any conditional guard, threshold, or breaker: **enumerate the full population of
events it claims to cover, and measure the gating condition on every one of them independently of
whether the guard fired.** Not the motivating example — all of them. If the guard's precondition is
absent on events that plainly belong to the phenomenon, the guard's real coverage is narrower than
its name, and the write-up must say so in the same breath as reporting that it works. A guard
validated on one episode should be described as "validated on <that episode>", never as "validated" —
the same discipline this file already requires for a database claim ("confirmed on production via
SSH, <date>", never a bare count). Cheap to run: instance 6's blind spot was found by grouping one
already-computed P&L column by block and noticing the worst block had a zero firing count, which cost
one query against output that already existed.

**The rule.** When writing or reviewing a guard, threshold, ordering rule, invariant, or any check
whose output will be believed, state explicitly what happens in the degenerate case — all values
NULL, all values identical, zero rows, the mechanism never engaging, the compared-against value
never obtainable — and confirm the check still means what its justification claims there. The
sharpest single question, which would have caught instances 1 and 5 outright: **can this check
produce the other answer at all, and what specifically would make it?** If the honest answer is "it
silently reports clean," either the check needs a companion covering the degenerate case (as
`backfill_admission_rank_value` now does for the NULL-ordering one, and as the realized-position-
count instrument did for the deployment guard) or the justification must be rewritten to state the
limit rather than imply coverage it doesn't have. **A comment asserting a safety property is a claim
requiring the same evidence as any other claim in this project** — the NULL-ordering one had been
read approvingly by multiple reviews before it caused a live defect, precisely because it reads as
obviously true. This is the same discipline this file already requires for tests (verify a new test
genuinely fails against the reintroduced bug, never merely that it passes) applied to every other
kind of check: **a check that has not been observed producing both of its answers has been observed
producing neither.**

**A specific sub-case worth naming, since it is the sharpest instance so far: do not conflate
*retention* with *resolution*.** The migration comment that caused incident 1 dismissed pre-existing
unresolved rows as something that "should not exist in practice — `pending_orders` has a bounded
retention window." Retention bounds how long a row is *kept*; it says nothing about whether a row is
*resolved*. Unresolved rows are in fact this queue's normal steady state (decided at 21:30, not
submitted until the next session), so the dismissed case was not rare — it was every row in the
queue at deploy time. The general form: when a comment dismisses a case as not occurring in
practice, check whether the property being cited actually excludes that case, or merely sounds like
it does.

## Candidate Queue ordering — a migration that added a sort column without backfilling it silently reordered the live submission walk (2026-08-10)

**Symptom**: the Status page's Candidate Queue displayed rows in alphabetical ticker order with the
Rank column reading 1-20. Reported as a suspected display bug; it was not one.

**Root cause.** The 2026-08-09 admission-ranking promotion (see "Relative-rank admission ordering
promoted to production code" above) renamed the sort key in both the display path
(`output/performance.py:get_pending_candidates_ranked()`) and the real live submission walk
(`brokers/trading212.py:_load_pending_orders()`) from `momentum_pct` to `admission_rank_value`. The
column was added by `data/database.py:_apply_migrations()`'s ALTER-only mechanism, which leaves
every pre-existing row NULL. All 308 unresolved `PENDING` rows had been decided 2026-08-07T22:09,
~46 hours before the promotion deployed, so **every** row's sort key was NULL. `decided_at` is
unique per row (308 distinct of 308, millisecond-stamped by the EOD insert loop), so with the
primary key contributing nothing the sort collapsed entirely onto that tiebreak — EOD insertion
order, which is the alphabetically-sorted candidate pool. Verified by direct production query:
the full 308-row order was exactly `sorted()`, and the reported screen positions reproduced to the
row (A=1, AAPL=2, ADI=6, ALB=14, AMP=20) with matching momentum values.

**This was a live behaviour defect, not cosmetic.** Both paths sort identically, so the submission
walk was affected the same way, and `_submit_pending_orders()` has no `decided_at` staleness guard —
that day's 14:30 UTC run would have processed candidates alphabetically rather than by momentum.
Blast radius was contained only by luck: the account sat at 69.65% deployed against a 0.70 cap
(£179.56 headroom against a £2,564.43 position size), so every candidate would have rejected on
capacity regardless of order. Roughly £2,385 of exits — one or two of 22 positions — would have
opened a slot for the alphabetically-first candidate instead of the highest-momentum one.

**Fixes, all shipped 2026-08-10.** (1) Immediate production backfill,
`UPDATE pending_orders SET admission_rank_value = momentum_pct WHERE admission_rank_value IS NULL`
(2,197 rows), applied over SSH and independently re-queried on a fresh connection per this file's
own standing requirement — zero NULLs remaining, `momentum_pct` checksum byte-identical before and
after, confirming no collateral edit. Backfilling from `momentum_pct` is unconditionally correct
regardless of configured mode: a NULL means the row predates the column, so it was decided by
pre-promotion code, which ranked on absolute momentum by definition. (2) The durable fix —
`backfill_admission_rank_value` in `data/database.py:_apply_data_fixes()`, deliberately NOT keyed
via `_migration_applied()` so it also repairs any future ALTER-only addition to a sort key, and a
no-op once no NULL remains. (3) `_load_pending_orders()`'s docstring safety claim corrected (see the
degenerate-guard class entry above). Three regression tests in `tests/test_execution.py`, including
one asserting the degenerate collapse *before* backfilling so it cannot pass for the wrong reason;
verified to genuinely fail against the reverted fix. Full suite 1,065 passing.

**A `COALESCE(admission_rank_value, momentum_pct)` in both `ORDER BY`s was considered and
rejected** — it would fix the default mode while silently mixing two incommensurable scales the day
`admission_ranking_mode` is switched to `relative_rank`, converting a loud, one-off migration gap
into a quiet, permanent correctness hazard.

**Incidental finding — SNDK's +2901.6% momentum is real, and the initial "data artifact"
speculation was wrong.** Once ordering was fixed, SNDK became rank 1, and was checked on suspicion
of being a `close`-vs-`adj_close` spinoff artifact of the class documented above. It is not: status
`CLEAN`, not among the 14 recorded price-cliff exceptions, series contiguous from the real
2025-02-13 Western Digital spinoff date with zero cliffs and zero gaps, largest single-day moves
1.286x/0.787x (nothing resembling an unadjusted split), and a yfinance re-fetch agreeing to the
penny (2025-08-05 = 41.93, 2026-08-06 = 1258.58, max 2335.0, `splits: none`). The decisive evidence
is corroboration rather than any single check: WDC (+495%) and STX (+452%) are the other two
storage names, all three large but differently sized — a coherent sector move, which no corruption
mechanism produces. Recorded because the retraction is the useful part: "extreme value therefore
suspect data" is a reasonable prior in this codebase given its quarantine history, and it was wrong
here.

**VaR sizing handled the extreme name correctly, contrary to the expectation that it would produce
an oversized position.** SNDK was sized at **£827.61 — the smallest position in the entire 308-row
queue** (255 of 308 sat at the £2,564.43 `per_trade_pct` ceiling; queue min/max £827.61/£2,564.43),
because its `var_pct` of −0.6197 is by far the largest in the pool. Its ATR stop is correspondingly
the widest at 29.9% below entry (vs. ALB 9.0%, STX 19.9%, WDC 24.7%). Worth recording as a concrete
worked example: the volatility that makes a name look dangerous is the same input that shrinks its
position, so "high-momentum volatile name wins the top slot" does not imply "large exposure to a
volatile name." No action taken and none warranted — overriding momentum ranking on a judgement
about one name would be exactly the discretionary curation this system was built to remove, and the
intuition behind it ("extended, already well off its peak") is precisely what Arm C's rank-based
de-selection exit tested and failed to improve on (see the relative-ranking study above).

## Benchmark-relative performance tracker — reporting only (2026-08-10)

**"Beat buy-and-hold" is this system's actual stated objective, and until now nothing in the interface
reported against it — every displayed figure was absolute.** The Dashboard and weekly email now show the
demo account against the same starting capital bought and held in the S&P 500, updated once daily.
Reporting only: no trading behaviour, sizing, admission, or exit logic changed, and
`trading212_invest.live` is untouched.

**^SP500TR, not ^GSPC — total return, deliberately.** `^GSPC` is price-only and excludes dividends,
which flatters this system by roughly 1.5–2%/yr. Every research run in this project already benchmarks
against `^SP500TR` (see the measurement-toolkit entry above), so using the same series keeps the
dashboard figure directly comparable to the backtest figures instead of introducing a second,
quietly-different baseline. **`^GSPC` remains and is NOT interchangeable** — it is the crisis-trigger
reference series (`analysis/crisis.py`). Coverage confirmed by direct fetch before being relied on, not
assumed: **5,433 rows, 2005-01-03 → 2026-08-07**. Added to `config.yaml`'s `reference_tickers`, so the
existing EOD price pull ingests it; a test pins its presence there, because if it silently dropped out
the comparison would stop updating rather than fail loudly.

**A real production defect was found and fixed as a prerequisite — the index would have been
quarantined on first ingestion.** The task flagged the risk from the known `^GSPC`/ETF incident; the
scoping turned out **not** to be safe, and this was confirmed by direct call rather than inferred:
`verify_ticker_identity('^SP500TR')` returns `QUARANTINED`, reason `quote_type='INDEX'`. The 2026-08-08
fix scoped `scan_unverified_tickers()` (the periodic backstop) but left `_screen_newly_backfilled_
ticker()` — the **ingestion-time** screen, called from `pull_incremental()`'s and
`backfill_price_history()`'s first-time-download branches — completely unscoped. Since
`get_all_watchlist_entries()` merges `reference_tickers` first, simply adding the benchmark to
`config.yaml` would have quarantined it the moment EOD first pulled it. Now scoped to
`analysis.universe.get_all_known_stock_tickers()`, the same definition the backstop uses — one notion of
"is this a stock", not two. **A lookup failure skips the screen rather than running it**: leaving a
genuine stock unscreened merely defers it to the next backstop scan, whereas screening a non-stock
writes a wrong, persistent `QUARANTINED` row no later pass would revisit. This is a third instance of
the domain-scoping error already documented twice above — a check applied outside the population it was
built for — and the fix is the same shape both previous ones took.

**The recurrence is the actual problem, and scoping each caller does not solve it — the next caller
added inherits the same trap.** Three instances now (the backstop scan, this ingestion-time screen, and
the original one-off remediation), each fixed at its own call site. The durable fix is to move the guard
INTO `verify_ticker_identity()` itself, returning a distinct outcome — `NOT_APPLICABLE`, or similar —
so no caller can get it wrong by omission rather than by commission. **Not yet done**; recorded here as
the recommended follow-up, deliberately kept out of the benchmark-tracker change so a verified changeset
did not grow an unrelated refactor.

**A trap in the obvious implementation, worth stating before anyone writes it.** The naive version —
"return `NOT_APPLICABLE` whenever the resolved `quote_type` is not `EQUITY`" — would **silently break the
detection this whole mechanism exists for**. A *stock* ticker resolving to a non-equity instrument is
not an out-of-domain input; it is precisely the corruption signal: `MER`/`CAM`/`PCL`/`SPLS` all resolve
to real but non-equity replacement products (a fund or an ETF) and are **correctly** `QUARANTINED` today
as genuine recycled-symbol cases (see the recycled-ticker section above). Refusing on resolved type
would turn all four into silent passes. **The discriminator has to be the ticker's EXPECTED class, not
its resolved one** — i.e. the function must itself check membership in
`analysis.universe.get_all_known_stock_tickers()` and return `NOT_APPLICABLE` for anything outside it,
which is exactly the check the three call sites have each been applying separately. That preserves every
existing quarantine (all four of those tickers are known stocks, so they stay in scope and still fail
Check A on their resolved type) while making the out-of-domain case impossible to reach by accident.
Any follow-up must be validated against those four tickers plus `FNMA` and `PALM` as controls before
being trusted — the same "validate a classifier against known controls BEFORE applying it" discipline
the contiguity work already established, and which caught the price-scale signal that would have
false-positived `FNMA`.

**Storage: `data/database.py:BenchmarkComparison`, written by the EOD job, read-only at render.**
One row per day: `date`, `account_value_gbp`, `benchmark_value_gbp`, `benchmark_close` (the raw input,
for auditability), `anchor_date`, `anchor_balance_gbp`. `data/scheduler.py:_refresh_benchmark_
comparison()` runs inside `_eod_job()` immediately after `_fetch_t212_snapshot(record_history=True)`
writes that day's `AccountValueHistory` row — **no new scheduled job**, since the EOD cycle already
produces both inputs. It records `benchmark_rows_written` in the EOD completion record either way, so
"did this step run" is answerable from the record rather than from the absence of an error — the gap
that let the data-quality backstop fail silently for two days.

**Anchored, never backfilled.** `benchmark_value = anchor_balance × close(date) / close(anchor_date)`,
where the anchor is the **earliest real `AccountValueHistory` row**. No synthetic history is created for
dates before the account existed: if the account's history is short, the comparison is short, and the
display says so. A missing close falls back to the most recent **earlier** close, never a later one —
a later close would leak future information into a historical row, and that guard has its own test
rather than being left implicit in the fallback test. A date with no earlier close at all is skipped
rather than written with a guessed value; if the *anchor itself* has no usable close, nothing is
computed at all rather than silently re-anchoring on a later date. The whole series is recomputed each
run (tens to hundreds of rows), which makes it self-healing: a day the EOD job missed, or ran before
`^SP500TR` had that date's close, fills in on the next successful run instead of leaving a permanent
hole.

**The comparison is currently 7 days long, and that is the honest answer rather than a limitation to
engineer around.** `account_value_history` begins 2026-08-02 at £50,000 — the account's real reset date.
Verified end-to-end against a copy of the production database with `^SP500TR` seeded: account
£51,362.06 vs benchmark £51,794.74, **−£432.68 (−0.84%) over 7 days** — precisely the shape predicted
for a ~70%-deployed account in a rising market, and precisely why the caveat below is mandatory.

**MANDATORY CAVEAT, one sentence, shared by both surfaces.** `output/performance.py:
benchmark_spread_caveat()` is the single wording used by the Dashboard and the weekly email, so a reader
cannot get a differently-hedged version of the same number depending on where they saw it. It states the
elapsed period and the structural reason a short reading is uninformative: the account holds ~63%
deployed on average, so it trails a rising index and leads a falling one substantially **regardless of
selection quality** — a property of the strategy, not evidence about it. **Deliberately symmetric**,
hedging a favourable spread exactly as firmly as an unfavourable one, with a test asserting the text
does not vary with the sign. Below `BENCHMARK_MEANINGFUL_MIN_DAYS = 180` it opens "Only N days elapsed —
too short to mean anything yet, in either direction"; past that the structural sentence remains and only
the "too short" clause drops. Nothing is gated or hidden on that threshold — it only decides how the
number is described. Kept to one sentence on purpose: a paragraph-length disclaimer beside a headline
number gets skipped, which would defeat it.

**Display.** Dashboard: the benchmark overlays the existing account-value chart as a dashed line with
**no fill** (two filled areas on one axis read as stacked rather than compared), plotted on its own
dates rather than reindexed onto the account's, since it genuinely starts later. Below it, three
metrics — account, buy-and-hold, and the difference in £ and % — with the percentage measured against
the **benchmark's** value, not the starting balance, since "how far ahead or behind buy-and-hold is this
account" is the question the objective is stated against. `delta_color="off"`: no red/green, matching
this project's established tone-neutral position-display convention. Weekly email: one compact line in
both the plain-text and HTML bodies, carrying the same caveat, and tested against the email's existing
forbidden-word tone standard for every wording branch. Both carry an explicit "as of" date. **When no
row exists yet, both surfaces say "not computed yet" — never a £0.00 spread**, which would read as the
account exactly matching the index.

**Deploy note.** `benchmark_comparison` is a new table, created by `init_db()` on the first post-deploy
service start (confirmed directly: a raw copy of the production database without `init_db()` having run
makes `_refresh_benchmark_comparison()` return 0 and log, rather than raise). The comparison populates
after the first EOD run that follows both the table's creation and `^SP500TR`'s first successful price
pull — expect "not computed yet" on the Dashboard until then, which is the correct display, not a
failure.

## Momentum-smoothness candidacy filter — pre-registered, tested, NOT ADOPTED (2026-08-10)

**Motivated by Part 2 of the selection audit** (Spearman(momentum-at-entry, `pnl_pct`) = −0.033), which
is a finding about one signal family rather than about entry information generally — nearly every
candidate tested to date is price-derived momentum in some form. Momentum *smoothness* (was a trailing
return accumulated steadily or in a few jumps) is a different construction over the same stored data,
with published support (Da/Gurun/Warachka's information discreteness), and it refines **ranking** — the
only mechanism this project has shown to work. Full detail:
`research/momentum_smoothness_prereg_2026-08-10/`. Baseline was **Arm B, not production**, since that
is the ranking that will be adopted.

**The metric choice was the most valuable part of the pre-registration, and it inverted the obvious
answer.** Two candidates: (i) signed positive-day fraction,
`sign(momentum) × (n_pos − n_neg) / 252`, and (ii) path efficiency (`net / Σ|daily|`, Kaufman's
Efficiency Ratio). **(ii) is not merely weaker — it is inverted for this hypothesis.** A single +250%
jump with flat other days scores **ER ≈ 1.0, the maximum**, because one jump is the most direct possible
path; a steady climb to the same total also scores ≈1.0. ER measures *directness*, not *smoothness*, and
cannot separate them. It would have scored the jumpiest possible candidate as maximally smooth — a check
reporting clean for a reason unrelated to what it is checking, this project's named recurring failure
class — and produced a null that looked like evidence against the hypothesis rather than against the
measurement. Confirmed empirically before any arm ran: single jump **+0.0040**, steady climb **+1.0000**.

**Filter form, one threshold, no weight, deliberately.** A blended rank/smoothness score introduces a
weighting parameter, and V1's confluence scoring was abandoned precisely because that makes a result
untestable. The blend was declined outright rather than held as a fallback, and the null did not rescue
it. **Thresholds calibrated from the independent variable only** — smoothness across 2,967,281
gate-passing candidate-days, no backtest, no outcome data — then frozen as absolute numbers before any
arm ran: **T1 = −0.004 (p10), T2 = +0.032 (p25), T3 = +0.052 (p40)**. The distribution is tighter than
the hypothesis implicitly assumes (mean +0.068, std 0.056, range [−0.167, +0.302]).

**Verdict: NOT ADOPTED.** B1 clears the +1.5pp cost-adjusted excess bar on Pool 2 CLEAN at **+1.551pp —
a margin of 0.051pp, 3.4% of the bar** — and is **−0.420pp on Pool 1**. No walk-away clause fires, and
the arm is still not adopted: **the sentence is a veto, not an adoption trigger.** Three reasons — the
margin is effectively *at* a bar deliberately set at 1.4× the ~1.08pp noise floor; the pools disagree in
**sign** on the primary metric; and the paired per-decision instrument **reproduces the disagreement
rather than resolving it** (B1-only beats A-only on Pool 2, loses on Pool 1), which rules out a
cash/cascade artefact and confirms the pools genuinely differ. A fourth signal specific to B1: it is the
only arm in either pool where the two drawdown measures move in opposite directions (peak-relative
improves 6.11pp, fixed-denominator worsens 16.55pp). Both pools show interior peaks rather than
monotonic curves, but at **different thresholds** (T1 vs T2) — a bracketed optimum whose location is
unstable across independently-provenanced pools is not a located optimum.

**§6 is the reusable lesson.** Candidate-day exclusion exceeded admission displacement by ~1.5× at every
threshold (9.5%→6.9%, 28.4%→17.9%, 41.7%→27.3%) — exactly what Part 4's "92–93% of admissions are
capacity-timed" predicts — but never fell below the 5% floor, so **the null is about the hypothesis, not
the instrument**. Without that second floor the two would have been indistinguishable. The deployment
guard passed comfortably (≤1.74pp), the first candidacy-narrowing mechanism in the sequence to do so.

**A separate finding, recorded as an observation and deliberately NOT promoted**: peak-relative drawdown
improves **monotonically with filter strength in both pools** — the only quantity in the study agreeing
in direction everywhere — reaching **+15.07pp** (Pool 2) and **+12.24pp** (Pool 1) at B3. Not a cash
artefact (deployment moves ≤1.74pp). But B3 has the *worst* return in both pools, so it is a
return-for-risk trade, and the pre-registration made drawdown a one-sided ceiling rather than a
criterion. Promoting it now would be the metric-switching pre-registration exists to prevent; it
motivates a separately pre-registered follow-up instead.

## Displacement-rate analysis — no usable operating point, NOT BUILT (2026-08-10)

**Read-only prep, not a mechanism.** Five studies now point at the same constraint — gate width,
position count, the two rebalancing studies, and smoothness all found that changing the *candidate pool*
barely reaches admissions, because 92–93% of admissions happen only when that day's exits free capacity.
Displacement (letting a strongly-ranked candidate replace a weaker holding rather than waiting for an
exit) is the only structural alternative attacking the timing constraint directly. Measured on Arm B's
preserved trade history before building anything. Full detail: `research/displacement_rate_2026-08-10/`.

**Recommendation: do not build it — and not at a different threshold, because the threshold is not the
problem.**

**The pre-specified 5/10/20/30% margin grid sat below the 25th percentile of the actual gap
distribution** (p25 = 0.372, p50 = 0.602), so it could not locate an operating point; extended to 5.00.
**No margin works.** Arm B's entire cost-adjusted CAGR is 5.433%; margins from 0.05 to 0.30 cost
**9.9–21.8%/yr** in turnover — two to four times the whole return. It only becomes affordable (<1%/yr)
at margins ≥1.50, requiring a candidate to out-rank a holding by **150+ percentage points of trailing
return**, where it fires a median of 18/yr (0/yr at 3.00) against maxima of 78–191 — a
crisis-concentrated burst at every affordable setting, never a steady trickle. Near-full-deployment days
are indistinguishable from all days (99.44 vs 99.40 at the tightest margin), so the days displacement
exists to serve are not special.

**Two findings say the signal is wrong rather than the threshold.** (1) The gap is positive on **99.87%**
of days, median **0.602** — it compares the **max of ~216 daily candidates against one incumbent**, a
winner's-curse order statistic biased upward by construction, so widening the margin samples further
into the tail of the same biased quantity rather than buying discrimination. (2) **61–78% of positions
become displaceable within one day of admission** (median 0–1 days) — the trigger would reverse its own
admission decisions almost immediately at two round trips a time. Churn, not selection, and the typical
case rather than a tail.

**Closed for the right reason.** A trigger keyed on a held position's own rank *percentile* rather than a
gap against the day's extreme would avoid the order-statistic bias — but that is close to Arm C's
`RANK_DROPPED`, tested and underperforming Arm B in both pools. A **minimum holding period before
displaceability** is the one variant these numbers leave genuinely open, though it converts displacement
into scheduled re-evaluation, which the two rebalancing studies already failed on deployment grounds.

**Caveat, and what it does not weaken.** This measures OPPORTUNITY, not outcome — displacement never
happened in the source backtest, and firing it changes everything downstream via capacity cascade. The
frequency and cost figures are robust to that (cost is incurred per firing regardless of outcome), as is
the churn result. What remains unknowable is whether the specific trades would be good ones — which the
cost makes moot at every frequency where it would matter.

## Long/short momentum (short sleeve) — pre-registered, tested, NOT ADOPTED (2026-08-11)

**The first test in this project of a genuinely different return profile rather than another tuning of
the long-only construction.** Motivated by the fact that ~60 experiments of incremental tuning had
produced exactly one adopted improvement (relative-rank admission ordering) while the system still
trails buy-and-hold `^SP500TR` after costs. Two allocations sharing ONE short signal so that
allocation was the only variable between them: **B** long-biased (long ≤0.70 / short ≤0.30) and **C**
market-neutral (0.50/0.50 with a `|net| ≤ 0.10` admission band). The short signal is the symmetric
mirror of the existing gate — momentum < 0, a borrow-adjusted mirror of the 2% floor, ranked by
cross-sectional relative rank ascending (mirroring the adopted live ordering). Full detail:
`research/long_short_prereg_2026-08-11/PREREGISTRATION.md` / `REPORT.md`.

**Verdict: NOT ADOPTED, decisively, in both pools.** Against a +1.5pp bar, Arm B lands **−7.17pp**
(Pool 2 CLEAN) / **−9.17pp** (Pool 1) and Arm C **−9.96pp** / **−11.66pp** on cost-adjusted excess
return over `^SP500TR` — five to nine times the measured ~1.08pp noise floor, wrong direction, at both
the modelled and 2× stress borrow rates, monotonically worse under stress. Three walk-away clauses
fire. **Nothing here was ever adoptable regardless: T212 Invest does not support shorting**, so venue
was out of scope by design and a positive result would only have made "which venue" a real question.

**The verdict does not rest on the modelled borrow assumption — the design's weakest input.** The
short sleeve loses **£33,093 GROSS, before a penny of borrow cost** (borrow is 13% of the loss). The
failure is in the short momentum signal itself, not in a cost nobody can measure, which makes the
negative far more durable than a cost-sensitive result would have been. Short win rate 36.3% against
the long sleeve's ~44%; over half of all shorts exit via `STOP_LOSS` (the squeeze signature).

**The rally breaker's blind spot is the most transferable finding and is recorded as instance 6 of
the "check that reports clean for an unrelated reason" class above — see that section for the
generalisation, which is the part worth carrying forward.** In brief: it fired correctly in the
canonical 2009 momentum crash (four days after the bottom) and the arms still lost 27–33% there; the
worst episode was **not** 2009 but 2020-06→2021-09 (26.8% of the entire short-sleeve loss), where it
fired **zero** times because `^GSPC` was never below the −20% drawdown gate (min −11.1%, 0 of 310
days). The gate that stops it over-firing in bull markets is what blinds it to a momentum crash at
index highs.

**Arm C achieved genuine market neutrality and it was worthless — the cleanest possible answer to a
criterion committed in advance.** Net exposure **0.8%**, monthly correlation to `^SP500TR` **−0.127**,
at **90.3% gross** — so the pre-registered guard built to catch fake neutrality (correlation bought by
sitting in cash, which would have shown as low gross) instead *certified the book as real*. It then
failed 3 of the 4 pre-registered QUALIFIED POSITIVE conditions: cost-adjusted CAGR −4.96% (needed
≥+3.0%), max drawdown −58.66% (needed ≤25%), and a catastrophic 2009 window. A neutral book that loses
money with a 59% drawdown is a worse asset with a prettier correlation coefficient. **Recorded because
the low-correlation result arrived exactly as hoped and was still worth nothing** — which is only
legible because condition (i) was fixed in advance rather than argued afterwards.

**Two priors confirmed and quantified, one refuted.** (4) The best short candidates really are the
hardest to borrow: shorts actually taken were HTB at **26.59%** (Pool 2 CLEAN) / **22.94%** (Pool 1)
against candidate-day base rates of 7.21% / 6.58% — a **3.5–3.7× concentration**, near-identical in two
independently-built pools. (5) Pool 1, survivors-only by construction, structurally cannot contain a
terminal-decline short winner, and its short sleeve accordingly loses **1.8× as much** (−£59,753 vs
−£33,093 gross, 33.6% vs 36.3% win rate) — **so Pool 2 CLEAN is the pool most FAVOURABLE to the
hypothesis, and the hypothesis loses there anyway.** (7) Refuted: Arm C was expected to be starved of
short candidates in bull markets; supply was ample (mean 128/day), so C failed on signal, not
starvation.

**A mechanism worth remembering for any future two-sleeve design: the short sleeve does not merely
lose its own money, it degrades the long book.** Arm B's long rules are byte-identical to baseline's,
yet its long P&L collapses from £166,830 to £58,107 — short losses shrink account equity,
`per_trade_pct` sizes every subsequent long position off that smaller equity, and the damage compounds
across 19 years through the shared equity path.

**Verification was unusually heavy because one production invariant could not be satisfied directly.**
`analysis/decision.py:evaluate_exit_reason()` hardcodes the long stop comparison (`price <= stop_price`)
and cannot be called for a short, and a live decision module must not grow a research-only branch — so
the short evaluator is a research-side mirror, and the mirror is proven **mechanically** rather than by
inspection via a **reflection test**: for a short at entry `E` with stop `E+d`, `P >= E+d` is
algebraically identical to `2E-P <= E-d`, so the short evaluator must return byte-identical results to
the real production function on reflected inputs. **200,360 cases (200,000 randomized + boundary +
None-contract), 0 mismatches, all five exit outcomes exercised well above a pre-set coverage floor.**
Also passed: degenerate parity byte-identical in **both** admission modes (4,876/£166,830.11 and
4,919/£103,926.50, 0 row-level mismatches on a canonical sort), both sanity gates reproducing the
relative-ranking study's published references **to the penny**, 40/40 short exit reasons recomputed,
40/40 sizing checked against the engine's **own emitted pre-entry balance** (never a reconstructed
proxy — the error that produced the cross-sectional study's false 21/30 failure), 20/20 borrow
accruals recomputed by hand, and **0 short trades exiting via `CRISIS_EXIT`** (asserted directly, since
the design blocks new short entries during a crisis but never force-exits existing shorts). Every
§14 calibration threshold survived unchanged — nothing was re-tuned.

**One methodological gap this study had to close before it could run at all, worth knowing about for
any future short-side work:** `analysis/backtest.py:precompute_ticker()` returns `{close, atr, signal,
momentum, relative_rank, open, low}` — it consumes `high` to compute ATR and then **discards** it. The
short-side mirror of the realistic intraday stop fill needs `high` (fire off the day's high, fill at
`max(stop, open)`); without it short stops could only fill at the close, reintroducing exactly the
optimism the 2026-08-06 correction removed for longs at a cost of 73.75% of total P&L. The research
cache adds `high` and was then verified cell-by-cell (2.6M cells, 80 tickers) against production rather
than trusted.

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path. No
database writes of any kind (`^SP500TR`/`^IRX` were fetched read-only into a research-local cache,
since the research DB copy predates `^SP500TR`'s addition to `reference_tickers`). Nothing adopted,
nothing deployed.

## "Stale peak" admission pattern — read-only diagnostic, NOTHING ADOPTED (2026-08-11)

**Prompted by a real live admission (ECHO, entered 2026-08-07); confirms the system worked as
designed.** The mechanism is real and correctly identified:
`analysis/signals.py:compute_momentum_series()` is a TWO-POINT calculation, `close(t)/close(t−252)−1`,
so the entire path between its anchors is invisible — a steady climb and a spike-then-30%-decline
produce an identical reading, and a name a third of the way down from its own peak can still rank near
the top. Full detail: `research/stale_peak_diagnostic_2026-08-11/REPORT.md`. Read-only: no backtest
re-run, no arms, no engine, no fix designed, no database writes. Metric defined as
`below_peak_pct = (max(close) over rows [t−252, t] − close(t)) / max(close) × 100`, the window bounded
by the momentum calculation's own two anchors.

**The pattern is ubiquitous rather than exceptional, and is associated with BETTER outcomes, not
worse.** **92.2%** of admitted positions were below their trailing-window peak at entry (median
**8.6%** below) — entering at the peak is the rare case, so the bare fact distinguishes nothing.
Outcomes then improve **monotonically across all five buckets**: win rate **41.4% → 47.7%**, mean P&L
**+0.38% → +2.51%**, median **−3.30% → −0.57%** (0–5% through ≥30% below peak). **The decisive number
is the P&L share**: positions ≥10% below peak are 43.6% of trades and take **50.8% of all gains against
45.7% of all losses — net +£116,280, or 69.7% of total realised P&L**, a larger share of gains than
losses at every cut point tested.

**Robustness, because Part 1 established the top decile carries 257% of P&L and a difference in means
can be one trade**: the monotonic ordering survives dropping each bucket's single largest winner,
**strengthens** when 2008 is excluded (win 42.1%→51.9%, median turns positive), persists 2015-onward,
and is **not a momentum proxy** — Spearman(below_peak, momentum) = **−0.269** (deeper-below-peak names
have *lower* momentum), with the effect positive within every momentum quartile and largest in the
lowest. Direction replicates in the independent absolute-momentum trade set, though that arm's extreme
≥30% tail is single-trade-sensitive (mean turns −0.076% once its top winner is removed) — recorded
rather than smoothed.

**The correct reading is a third independent confirmation of a standing result, not a new
opportunity.** Trade-level Spearman(below_peak_pct, pnl_pct) = **−0.028** — essentially zero, exactly
as the selection audit's Part 2 predicted for entry information generally. **Three separate entry
features have now been tested against outcome and all three came back the same way**: momentum
magnitude (Part 2, Spearman −0.033), path smoothness (the momentum-smoothness study, NOT ADOPTED), and
now peak-drawdown-at-entry. **The generalisation worth carrying forward: entry information does not
predict individual trade outcome in this system, and the weak bucket-level signals that do exist have
twice now pointed the OPPOSITE way from the intuition that motivated looking.** Absent a strong prior
reason, a fourth entry feature should be expected to behave the same way.

**The only negative sub-pattern found anywhere in the diagnostic**: positions whose trailing peak was
more than 126 trading days ago (half the lookback) — 278 trades, win rate **37.05%**, mean −0.203%, net
**−£292** against a £166,830 total. Real, consistent, and immaterial.

**ECHO specifically — the system working as designed, plus one correction to the framing that prompted
it.** Production `pending_orders` (read-only SSH query, 2026-08-11): `momentum_pct` =
`admission_rank_value` = **2.2559** (+225.59%), **ranked 5th of 308** candidates in its own decision
batch — admitted from the very top of the ranking, not from deep in it. `admission_rank_value` being
*identical* to `momentum_pct` confirms it was admitted under **`absolute_momentum`** ordering (the
relative-rank flip was 2026-08-11, five days later), so the motivating example comes from the
**previous** ranking regime. On real stored data ECHO's trailing-window peak was **$141.80** (not the
~$125 assumed), making the entry **36.5% below peak — the 97.2nd percentile** of all admitted
positions, in the ≥30% bucket, which is the **best**-performing of the five.

**One forward-looking observation, stated as an observation and not a concern**: relative-rank ordering
admits *deeper*-below-peak positions than absolute momentum did (92.2% vs 87.6% below peak by any
amount; 43.6% vs 33.8% at the ≥10% cut), so this pattern is becoming **more** common under the newly
adopted live ordering — in the population whose historical record is the strongest of the five buckets.

**On the capacity wall**: unlike a candidacy filter (see the smoothness study's §6 and Part 4's
"92–93% of admissions are capacity-timed"), affected positions here are **top-of-ranking admissions**
(median rank-within-day 3.9–5.2%), so a filter targeting them would genuinely displace admissions
rather than being absorbed by capacity timing. That makes the capacity wall the wrong objection; the
binding one is that such a filter would remove the population producing 69.7% of total P&L.

## Exit-mechanics sweep — pre-registered, tested, ROBUST, NOT ADOPTED (2026-08-11/12)

**The largest research result this project has produced, and the first to find a lever that clearly
works — but NOT adopted, and three qualifications travel with it permanently.** Swept the three dials
that govern when a position closes: `atr_stop_multiplier` (2.0/3.0/none), `stock_max_holding_days`
(30/60/90/unlimited), and a trailing stop (activation none/+5%/+10%, distance fixed at the cell's own
ATR multiplier). Full detail: `research/exit_mechanics_prereg_2026-08-11/PREREGISTRATION.md` /
`REPORT.md` / `ROBUSTNESS.md`.

**Winner: `stop 3.0× ATR + unlimited max-holding + no trailing stop`, at +6.443pp (Pool 2 CLEAN) /
+5.381pp (Pool 1) cost-adjusted excess return over the current live baseline.** Both pools produce the
identical ranking. No walk-away clause fires.

**Baseline was the CURRENT LIVE system (relative-rank, adopted 2026-08-11), and re-testing rather than
inheriting the 2026-08-07 stop study was vindicated**: that study's ranking (no-stop > 3.0× > 2.0×)
**INVERTS here to 3.0× > none > 2.0×**, in both pools. The stop's optimum depends on the holding regime
it sits in — which is exactly why the three dials were swept as one mechanism rather than separately.

**STAGE 0 — a read-only measurement run before any sweep cell, and the finding that sized the whole
study.** `MAX_HOLDING` is **48.9% of all exits**; **78.6% are re-entered into the same ticker within 30
trading days and 37.4% within ONE day** (median gap 2 days), paying **£38,236** in round-trip FX ≈ 23%
of the strategy's entire realised P&L. **The asymmetry against the stop is the point**: re-running the
2026-08-07 study's whipsaw method on the same trade set, `STOP_LOSS` re-entry pairs combine to
**−£399,571** (that study's result, replicated) while `MAX_HOLDING` pairs combine to **+£710,791**. The
stop fires on positions that are genuinely deteriorating; the holding cap fires on positions that are
still working, and a third of them are re-bought the next day.

**The trailing stop FAILS decisively, exactly as its pre-registered honest prior predicted** — costs
~5.4pp at both activation thresholds, collapsing the winning construction to barely above baseline.
**Win rate RISES (28.85% → 42.46%) while returns fall**, replicating Arm C's finding precisely: cutting
winners short produces more frequent, smaller wins in a strategy that lives on the right tail.

**ROBUSTNESS: 70 of 70 (draw × end-date) combinations positive, every one above the ~1.08pp noise
floor**, worst value +3.400pp (3.1× the floor), unperturbed +6.443 sitting just above the distribution
median rather than at an extreme. Two dimensions were added beyond the standard method and both earned
their place: **end-date as a formal axis** (5 windows per draw, exact rather than approximate — a
sequential walk truncated at D yields trades identical to the full run up to D), and **single-ticker
dominance TARGETED at the top-10 P&L contributors rather than randomised**, which was necessary
because 73.6% of P&L sits in 10 of 238 tickers and random drops would have been mostly no-ops.
**NVDA is the largest named dependency at 67% retained** — materially stronger than the relative-rank
study's AMAT precedent (~21%, below the floor).

**TWO QUALIFICATIONS THAT MUST TRAVEL WITH ANY ADOPTION** (a third was withdrawn 2026-08-12 — see
immediately below):
1. **The absolute claim is NOT supported.** The cell clears `^SP500TR` at exactly one end date of seven
   and trails at the other six. "Beats buy-and-hold" is not a supportable statement; the robust claim
   is the relative one.
2. **Concentration is real**: 565 positions over 19 years at 149-day mean holds, 73.6% of P&L in 10 of
   238 names, NVDA alone 17.0%. A robust *comparison* does not diversify the *portfolio*.

**WITHDRAWN QUALIFICATION — "realised P&L is worse than baseline" (2026-08-12). A future session
reading the original wording would reasonably decline to promote this result; it was wrong, and the
error was in the comparison rather than in the strategy.** Full detail:
`research/force_close_2026-08-12/REPORT.md`.

<details><summary>Original qualification 1 (struck through, preserved for the record)</summary>

> ~~1. **Realised P&L is WORSE than baseline.** 59.9% of the winning cell's P&L is unrealised
> mark-to-market on 34 positions still open when the walk ends (baseline: 7.6%); on realised P&L alone
> it is **£139,863 vs baseline's £154,234**.~~

</details>

**Why it was wrong.** `simulate_portfolio_compounding_mtm()` — and every engine derived from it —
**already force-closes every open position at the walk's end** via `END_OF_DATA` at the final close,
and the sweep's headline cost-adjusted CAGR is computed from that equity curve. Verified to the penny,
both arms: `final equity − £50,000 = Σ pnl_gbp`, **diff +0.00**. **So +6.443pp was already the
fully-realised comparison — there was no unrealised overhang in it.** The 59.9% figure describes
**which positions the strategy itself chose to exit**, not **whether the gain was booked**; those are
different concerns and conflating them produced the caveat. Worse, the comparison was **structurally
unfair rather than conservative**: excluding `END_OF_DATA` removed **59.9% of the winner's P&L and only
7.6% of baseline's**, deleting the winner's largest positions, keeping nearly all of baseline's, then
reporting the winner as behind.

**What the force-close test found.** Force-closing both arms identically at five cutoffs
(2022-08-05 → 2026-08-07), fully-realised: the winner leads **5/5 in both pools** (Pool 2 CLEAN
+5.606 to +6.673pp; Pool 1 +3.868 to +5.677pp) with total P&L **2.0–2.3× baseline's** at every cutoff.
**Decisively, its RULE-CLOSED P&L alone leads baseline at every intermediate cutoff** — 1.97×, 1.76×,
1.17×, 1.08× — **and by the widest margin exactly where the force-closed block is smallest** (16.0%,
15.1%). An unrealised-marks artefact predicts the opposite, so this falsifies the hypothesis rather
than merely failing to confirm it. The method was validated rather than trusted: two genuine
truncated-cache re-runs × two arms reproduce the equity-truncation totals **4/4 penny-exact** (the
rule-closed/force-closed *split* carries ~±0.3% boundary uncertainty; totals do not).

**The mechanism behind the lower strategy-chosen total, confirmed size-neutrally**: strategy-chosen
exits are **4,836 at +0.827% mean (baseline) vs 531 at +8.060% (winner)** — a **trade-count ratio of
9.1× against a per-trade quality ratio of 9.7×**, which nearly cancel. The winner's mean position is
*smaller* (£2,770 vs £3,347), so sizing understates rather than inflates its per-trade advantage.
Baseline reaches a marginally higher strategy-chosen total by making nine times as many decisions at a
tenth the per-decision return — **a restatement of what the change does, not a caveat about it.**

**Standing lesson, of general relevance beyond this study**: when comparing two arms that differ in how
often they exit, any metric that excludes a category of exit will hit them asymmetrically. Check the
exclusion's share of EACH arm before treating the comparison as conservative — here it was 59.9%
against 7.6%, and the asymmetry alone produced the wrong sign.

**The end-date axis did settle one open concern on evidence**: the advantage is **weakest at the
earliest windows** (2022–2023) and strongest at 2025-08-01, consistently across all 14 draws — the
opposite of what an artefact of ending the walk with AI-rally names near highs would look like.

**Method notes worth carrying forward.** The paired instrument initially looked damning (shared-key
win rate 11.96%) and two walk-away clauses appeared live; both were **tested rather than argued**. A
randomization null showed the observed overlap (326) is ~4× chance (81.8), so low overlap is forced by
construction; and the shared set proved a **biased slice** — baseline's short, failing positions
(median hold 9d vs 30d, `MAX_HOLDING` share 27.9% vs 50.4%), i.e. structurally where longer holding has
nothing to offer. Of 326 shared keys **235 are byte-identical trades**; only 91 genuinely diverged, and
on those P&L goes £20,669 → £85,379. The 11.96% headline was scoring identical trades as "not better".
**The 91-decision subset is small and was deliberately NOT promoted to strong evidence.**

**The §5.4 back-check passed**: the max-holding ranking at final settings is identical to Stage 1
(unlimited > h60 > h90 > h30), so the staged design's own truncation did not bias the outcome — the
guard that makes a staged sweep honest, and the reason a 20-cell staged design was run instead of a
72-cell grid.

**A verification failure worth recording, because it is the standing class occurring inside the
verification itself**: the first trailing-stop unit-test run **passed vacuously on empty trade lists**
(`is_member()` rejected the synthetic tickers), surfacing only because a later test indexed `[0]` and
raised. Fixed at the cause and at the class — a `_require()` guard now asserts every test produced a
trade. **A test that has not been observed producing a non-empty result has not been observed at all.**

**The broadest result.** Four independent investigations found that **entry** information does not
predict individual trade outcome. This study finds **exit mechanics matter enormously** — a 6.4pp
cost-adjusted swing from two parameters. **The edge in this system is not in choosing better positions;
it is in not churning the ones it already holds.**

**Status: research finding only.** No changes to `analysis/signals.py`, `analysis/decision.py`,
`config.yaml`, or any live path; no database writes. Promotion would need the same production-module
and four-parity-layer work the relative-rank promotion required. **The force-close variant that was
listed here as a prerequisite has now been run (2026-08-12) and passed — see the WITHDRAWN
QUALIFICATION above; it removed a caveat rather than adding one.**

## Exit-mechanics promoted to production code — CAPABILITY ONLY, NOT FLIPPED (2026-08-12)

**The exit-mechanics winner is now expressible through config, and the config still ships the CURRENT
behaviour.** `atr_stop_multiplier: 2.0` and `stock_max_holding_days: 30` are unchanged; flipping them
to `3.0` / `0` is a separate, deliberate decision. A test
(`tests/test_max_holding_disabled.py::test_shipped_config_still_has_a_finite_stock_holding_cap`)
asserts the shipped config keeps a finite cap, so the promotion cannot silently become a flip.

**`atr_stop_multiplier` needed no code at all** — it was already a config value read by the shared
`_resolve_entry_stop()`. All the work was for the holding cap.

**THE ZERO-MEANS-DISABLED CONVENTION.** `stock_max_holding_days: 0` (or `max_holding_days: 0` for
ETFs) disables the age-based holding cap: `analysis/universe.py:get_max_holding_days()` returns
**`None`**, which `evaluate_exit_reason()` already treats as "unknown, don't trigger this check" under
its standing None-tolerance contract, so no age exit fires and positions are governed only by
`CRISIS_EXIT`/`STOP_LOSS`/`SIGNAL_OFF`. Zero is the switch because it is the one value for which a cap
has no coherent meaning (a 0-day cap would exit every position on its own entry day), so one integer
key carries both settings with no second key that could contradict it.

**THE NON-OBVIOUS PART, and the whole reason this needed real code: ONE config value feeds TWO
consumers whose semantics diverge the moment it is disabled.**
1. **`evaluate_exit_reason(max_holding_days=...)` — an EXIT GATE.** `None` is meaningful and already
   handled.
2. **`_var_position_size_core(..., holding_days=...)` — a SIZING HORIZON**, at **five call sites** (4
   in `analysis/backtest.py`, 1 in `brokers/trading212.py`). It scales daily VaR by
   `sqrt(holding_days)`. **`None` is not meaningful here** — `sqrt(None)` is not a number, and "size
   for an infinite horizon" would drive every position to the minimum.

So a disabled cap needs **no** horizon for exits and a **real** one for sizing.
**`analysis/decision.py:resolve_var_horizon_days()`** supplies it and is the single shared
implementation both live and every backtest engine call — never re-derived inline, per the "exactly
one implementation of any given trading decision" invariant. `trading212_invest.var_horizon_days`
(default **30**) overrides it.

**The default 30 is LOAD-BEARING for reproducibility, not arbitrary**: the sweep measured its
unlimited-holding arms with a sizing horizon of exactly 30 (the then-current
`stock_max_holding_days`). Any other fallback would produce different position sizes and would NOT
reproduce the study. Pinned by its own test with that reasoning attached.

**REPRODUCTION VERIFIED END-TO-END — the decisive promotion check.** Raw, unmodified production
`simulate_portfolio_compounding_mtm()`, driven **only by config**, against Pool 2 CLEAN
(`research/exit_mechanics_promotion_verify_2026-08-12/`):

| arm | promoted production code | study reference | |
|---|---|---|---|
| baseline (2.0×, 30d) | 4,876 trades, £166,830.11 | 4,876, £166,830.11 | **MATCH** |
| winner (3.0×, cap 0) | 565 trades, £348,548.74 | 565, £348,548.74 | **MATCH** |

The winner's exit mix contains **zero `MAX_HOLDING`**. **The baseline match is the more important of
the two**: rerouting five VaR call sites is exactly where a fractional sizing slip would compound
invisibly across 19 years rather than failing loudly, and it reproduces to the penny — so the
promotion is **behaviour-preserving at defaults**, which is what makes it safe to merge independently
of any flip.

**ALL FOUR PARITY LAYERS COVERED, and the two that mattered were added deliberately:**
- **Layers 1 and 4** (structural) — pass; the new resolver is genuinely called by both live and
  backtest, so no one-sided-decision exemption is needed.
- **Layer 3** (`tests/test_live_backtest_parity.py`) — two new scenarios. One asserts a position held
  **250 days** with the cap disabled stays open on **both** sides; its **companion** asserts
  `SIGNAL_OFF` still fires with the cap disabled. **The companion is the point**: a bug suppressing
  *every* exit would sail through a test that only asserts nothing fires — the vacuous-pass shape this
  session already hit once, anticipated this time rather than discovered.
- **Layer 2** (`tests/test_comprehensive_replay_parity.py`) — the full ~320-day, two-ticker,
  capacity-sharing walk re-run with the cap disabled. **A genuinely different question from Layer 3**:
  with the cap off, positions are held far longer, capacity frees rarely, and admission ordering
  interacts with a much smaller stream of exits. A divergence of that kind cannot be written as a
  targeted scenario, because nobody knows in advance which day it would appear on.

**Every new test was verified to FAIL against the reintroduced bug** (sentinel ignored, raw `0`
returned) and to pass again after restoring — 2 Layer 3 scenarios, 2 Layer 2 scenarios, 4 unit tests.
Full suite **1,165 passing**.

**A false premise caught by Layer 2, recorded because it argues FOR the design.** An initial test
asserted `0` (disabled) and `9999` (a cap longer than the window) were behaviourally identical.
**They are not.** A finite 9999 feeds VaR a 9999-day horizon, scaling positions by `sqrt(9999)≈100`
instead of `sqrt(30)≈5.5` — **roughly 18× smaller**, with some then falling below `min_position_gbp`
and rejected outright. So the obvious pre-promotion workaround for "no holding cap" — set a huge
number — **silently shrinks every position**, while the sentinel disables the exit and leaves sizing on
a real horizon. The test now asserts the true property (they agree on exits, deliberately differ on
sizing) rather than the false one.

**Not yet done, and required before any flip is trusted in production**: nothing about live
*operation* under a disabled cap has been observed — only simulated. Unlimited holding interacts with
the live re-entry, membership and reconciliation paths in ways no backtest exercises.

## High-threshold trailing stop — pre-registered, tested, NOT ADOPTED (2026-08-22)

**Closes the one gap the exit-mechanics sweep left in the trailing-stop dimension.** That sweep
tested activation at **+5%** and **+10%**; both cost ~5.4pp. The untested case was structurally
different: **a stop fixed at entry goes inert exactly when a position has most to give back** — the
live MRNA position sat at +136% with its stop 176% below price, i.e. no stop at all in practice. So
the system has protection on every position *except* the ones that have run furthest. A high
activation threshold touches only that handful. Arms: baseline (no trail) plus activation at
**+30/+50/+100/+200%**, trail distance fixed at the live 3.0× ATR, ratchet up only. Full detail:
`research/trailing_high_threshold_prereg_2026-08-22/`.

**VERDICT: NOT ADOPTED. Cost-adjusted excess return over `^SP500TR` vs baseline: B30 −5.087pp,
B50 −3.999pp, B100 −1.759pp (Pool 2 CLEAN); −2.511 / −2.069 / −1.435 (Pool 1).** Every arm negative,
every one outside the ~1.08pp noise floor, both pools agreeing on sign and ordering. Monotone
attenuation toward zero as the threshold rises, never a sign change — **there is no interior optimum,
and the window between "fires enough to measure" and "fires little enough not to damage the tail" is
empty.**

**THE HEADLINE MECHANISM, and it is the most transferable thing here: the trail wins the count and
loses the money.** It genuinely does protect gains — protection is simply worth an order of magnitude
less than it costs. Mean **+£389 to +£765 per protected position against −£2,718 to −£3,092 per
forgone one**. At B100 on Pool 2 it protects **exactly as many positions as it forgoes (14:14)** and
still nets **−£27,488**; on Pool 1 it protects **more often than it forgoes (12:10)** and still nets
−£18,403, with a *positive* median (+2.53pp) against a mean of −30.71pp. Portfolio win rate
correspondingly **rises 28.85% → 34.64%** while cost-adjusted CAGR falls 11.44 → 6.35. **A win-rate
read of any exit mechanism in this system is not merely uninformative, it is inverted** — this is
Arm C's finding and the sweep's +5%/+10% finding replicated a third time, at thresholds nobody had
tested.

**A protective exit inside an always-redeploying portfolio buys turnover, not risk reduction — the
finding worth carrying past this study.** Drawdown was pre-registered as a one-sided ceiling with
improvement *expected* (protecting run-up gains is the mechanism's entire purpose) and explicitly
barred from counting as evidence in favour. **It did not even improve**: −43.88% baseline against
−45.15 / −44.74 / −43.88 (Pool 2), flat to slightly worse. The reason is that **mean deployment is
essentially identical across every arm (63.9–65.1%, a 1.2pp spread)** — capital protected out of one
position is immediately redeployed into another, so **position-level protection never becomes
portfolio-level protection, because the book refills.** Any future protective-exit mechanism in this
system should be expected to behave the same way unless it is *paired* with a rule keeping the freed
capital out of the market — a pairing neither this study nor the sweep has tested, and one the two
rebalancing studies' deployment findings argue against.

**55–80% of the damage is CAPACITY TURNOVER, not truncation — the opposite of what was anticipated.**
Total P&L Δ decomposes exactly into direct effect plus capacity residual: B30 −£221,843 = −£81,901
direct + **−£139,942 capacity**; B100 −£95,363 = −£27,488 + **−£67,875**. The arms admit 421–1,000
positions baseline never held, carrying real positive P&L (£110,893–£281,932) — yet the residual is
strongly negative, because the capital was recovered from far better positions than it was redeployed
into. **Freeing capacity locked since 2009 did not rescue the arms; it hurt more than cutting the
winners short did.** The §5.3 walk-away condition (effect tracing mainly to capacity turnover) is met,
though moot for adoption since the primary criterion already fires.

**Top-decile Jaccard 0.057–0.153** against overall Jaccard 0.19–0.34 — the *tail* is disrupted about
three times as much as the book as a whole, exactly as a mechanism acting only on run-up positions
must, and exactly why it is fatal. 18–21 of baseline's ~56 top-decile positions are directly
truncated.

**B200 was NOT TESTED and its return figure is deliberately not estimated or interpreted.**
`n_reached` = 29 (Pool 2) / 25 (Pool 1) against a materiality floor of **30 fixed before any number
was seen** — a one-position miss on the decisive pool, and a clearer one on Pool 1. Per the
position-count study's `n5`/`n10` precedent this is reported as NOT TESTED, never as a null. **The
floor was not moved, and how tempting that was is precisely why it was pre-registered.** A read-only
per-position path check showed `n_fired` would have been 29 of 29, so the failure is on the population
half of the gate alone. **"NOT TESTED" is not "SAFE"**: B200 is simultaneously too small to measure
and acting on **95.7% of total P&L**, which argues against deploying such a rule on thin evidence
rather than for relaxing the gate.

**THE STRUCTURAL ODDITY IS NOT DISSOLVED — a trailing stop is the wrong remedy, the giveback is
real.** Stage 0 measured it precisely: **the median position reaching +30% surrenders 48.5pp, keeping
only 45.5% of its peak** (79.3pp / 56.8% at +100%), with the trail armed for **78% of the holding
period** at B30. But `SIGNAL_OFF` is **80–85% of exits in that population**, and a 252-day momentum
reading *cannot* turn negative without a large fall from peak — so **the giveback is substantially
the definitional price of admission to the right tail**, not carelessness. Pay it and you hold the
winners carrying 124% of total P&L; refuse it and, as measured here, you lose far more than you save.

**Two methodological notes worth keeping.** (1) **A specification gap was caught before the run that
would have produced a decisive negative for a reason unrelated to the hypothesis**: the sweep's
engine trails at `hwm − k × ATR_at_entry`, and with ATR frozen at entry the trail narrows by exactly
the activation multiple (1.3× at +30%, 3.0× at +200%, 8.2× at NVDA's real +722%). At B200 that
applies an effective **1.0×-equivalent** width — a multiplier the sweep already established is
materially worse than 3.0× — so the arm would have tested stop *width* while claiming to test
activation *threshold*. Ruled to **recompute ATR at the current date** before any cell ran; the
frozen convention is retained in `trail_engine.py` only so the sweep's own cells stay reproducible.
(2) **A verification failure that was the harness, not the engine**: V1 reported 90 mismatched rows
on identical trade sets, traced to comparing in-memory `float64` against a CSV-round-tripped
reference with exact `!=`. Measured directly, **the default `to_csv`/`read_csv` round trip is not
bit-exact and alters 16.2% of float64 values**; V1 observed 15.9% (90/565). Like-for-like re-run:
**0 mismatches across all 12 columns, both pools**. Standing lesson: **a float that has been through
a text format and one that has not are not the same number, and comparing them with `==` tests the
serializer, not the computation** — the neighbouring version of this (row *ordering*) already bit the
relative-rank promotion once.

**Research only. No change to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any
live path. No database writes. Nothing adopted, nothing merged into the trading logic, nothing
deployed.** Perturbation robustness was not run and was not required — it is earned by an arm that
fires no walk-away clause, and none qualified.


## Hard slot-count concentration — pre-registered, tested, NOT ADOPTED (2026-08-22)

**Closes the question the position-count study (2026-08-09) explicitly left open and named the design
for.** That study tried to reach 5 and 10 concurrent positions by raising `per_trade_pct` to 0.14 and
0.07, realised ~12 against a baseline of ~14 in both arms, and was correctly reported NOT TESTED; its
own conclusion was that genuine concentration "would need a design that enforces a hard slot count
directly ... a different engine design, not a retune". That design has now been built and run. Full
detail: `research/slot_count_prereg_2026-08-22/PREREGISTRATION.md` / `GATE_REPORT.md` / `REPORT.md`.

**Baseline is the CURRENT LIVE CONFIGURATION** (relative-rank admission, `atr_stop_multiplier 3.0`,
`stock_max_holding_days 0`, `per_trade_pct 0.05`, `max_total_deployed 0.70`), uncapped; arms cap
concurrent positions at N in {14, 8, 5, 3}.

**VERDICT: NOT ADOPTED. Cost-adjusted excess return over `^SP500TR` vs baseline, Pool 2 CLEAN: n8
−2.850pp, n5 −1.582pp, n3 +1.004pp** — against a +1.5pp bar and the measured ~1.08pp noise floor. Two
arms are worse than baseline outright; the best misses the bar by a third and does not clear the
floor. Pool 1 agrees on the endpoints (n3 best at +1.244pp, n8/n5 below baseline) and **disagrees on
the interior ordering**, so no interior optimum is located.

**THE SIZING PROBLEM WAS SOLVED, AND THAT IS THE REUSABLE PART.** A slot cap alone would have
reproduced the position-count failure through a different mechanism. Three constants were re-derived
from N, all reducing EXACTLY to the live values at N=14: `per_trade_pct_N = max_total_deployed / N`;
`max_risk_pct_N = max_risk_pct × (14 / N)` (holding TOTAL portfolio risk budget constant at 0.14);
and `cash_fraction_N = max(0.25, (max_total_deployed/N) / (1 − max_total_deployed))`.

- **`max_risk_pct` MUST scale too, and this is not obvious.** VaR-implied size is
  `(balance × max_risk_pct) / |q05 × sqrt(30)|` and **does not depend on N at all** — raising only the
  ceiling leaves every VaR-bound candidate baseline-sized, so an N=3 arm would deploy ~11%. That is a
  de-risking test wearing a concentration label. Scaling both by `1/N` also makes the
  pinned-vs-VaR-bound partition **exactly N-invariant** (a candidate pins iff
  `|q05| <= 0.14/(0.70*sqrt(30)) = 0.03652` — N cancels), so the arms differ in position COUNT and in
  nothing else about how a position is sized. Confirmed empirically: pinned share 33.1 -> 36.4 -> 33.6
  -> 29.8 -> 29.8% across arms.
- **The `cash × 0.25` clamp, not VaR, was the real hidden concentration limit** — and this appears to
  be the larger part of why the position-count study failed, which that study did not isolate. The
  clamp binds iff `d > 1 − 4 × per_trade_pct_N`: at live settings that is `d > 0.80`, unreachable
  under a 0.70 cap, so it is **provably inert at baseline**; at N=3 it binds above `d > 0.067`, i.e.
  always. Its equilibrium has a closed form — **`d* = N/(N+3)`**, so 50.0% at N=3 and 62.5% at N=5,
  against baseline's 65.1%. Left untouched it, not the slot count, would have set the concentration.
  Relaxing it to the smallest inert value is **not a second treatment variable**: it removes a
  constraint provably inert in the control so it is inert everywhere. Verified, not assumed:
  **zero CASH-bound admissions in every arm, both pools**, including N=3 at `cash_fraction 0.7778`.

**Result: deployment parity held at three concurrent positions — 63.50% vs baseline's 65.10%, worst
divergence anywhere −1.92pp, every arm inside the 2pp "clean" tier.** This is what the earlier attempt
could not achieve, and it is why the arms genuinely test concentration rather than cash-holding.

**THE DRAWDOWN PRIOR WAS WRONG, and the study's own pre-registration said to expect the opposite.**
Part 7 of the selection audit established that ranking sits at the 99th percentile of random admission
on return and the **98th-worst on drawdown**, so a drawdown-driven not-adopted was the predicted
outcome and Clause B (−60% absolute) was written precisely because Clause A alone would tolerate
−58.88%. **Neither clause fires anywhere.** Worst is n8 at −48.86%; **n5 and n3 are BETTER than
baseline in both pools** (+4.40 / +3.03pp on Pool 2). Concentration did not deepen drawdown here.
Recorded as a genuine refutation, with the reconciliation stated rather than assumed: Part 7 varied
*which* candidates fill roughly fixed slots, this varied *how many* slots at fixed deployment — two
different manipulations, and only the first spent drawdown. A low-turnover mechanism (84 vs 565
positions, riding through drawdowns baseline stops out of) is offered as an untested hypothesis only.

**n3's +1.004pp is not a near-miss: 71.25% of its P&L is three trades and 59.99% is one ticker
(NVDA), on 84 positions.** n5 also exceeds the 50% top-3 threshold (62.12%). A lottery ticket, not a
strategy — and the reason the reading should not be chased with a larger sweep.

**"A book capped at N runs at ~0.8N" — a mechanism the pre-registration missed, and it must travel
with every figure.** Realised means are 6.23 of 8, 4.11 of 5, **2.43 of 3**. Because
`per_trade_pct_N = max_total_deployed / N` makes both constraints bind at the same nominal point,
mark-to-market appreciation reaches 70% deployed before the Nth slot fills, so the deployment cap —
not the slot cap — holds the count below N (measured directly: at n8, 3,360 days blocked by capacity
only vs 1,183 by the slot cap; only at n3 does the slot cap dominate). The materiality verdicts stand
(6.23 is far nearer 8 than baseline's 11.78), but "n3" is 2.43 positions, not three.

**n14 is NOT TESTED, and the reason is a corrected arithmetic error worth recording.** The
pre-registration estimated baseline's realised concurrent count at ~17.1 from
`565 × 149 / 4,930` — **`holding_days` in this codebase is CALENDAR days, not trading days**, and the
true figure is **11.78** (confirmed two independent ways: the engine's panel counter and a trading-day
occupancy derivation from the trades file, agreeing to <=0.005 positions in all ten arms). Baseline
therefore sits *below* 14, so capping at 14 realises 10.90 — 0.87 from baseline, 3.10 from its own
target — and cannot test anything. The §2 gate caught this before any return figure existed, which is
what the gate is for. **Generalisable: deriving mean concurrent positions as
`trades × mean_hold / trading_days` overstates it by ~45% in this codebase.**

**Verification (all passed before any arm was trusted).** Degenerate parity: with the slot cap
disabled and live constants, the research engine reproduced raw production
`simulate_portfolio_compounding_mtm()` **byte-identical on both pools** (Pool 2 CLEAN 565 /
£348,548.74; Pool 1 522 / £429,108.58), compared on a canonical sort **in memory**, never across a CSV
round trip. Instrumentation confirmed purely additive. Decision-engine parity per this project's
standing research-script requirement: **300 sampled decisions × 5 checks, all exact.** The §8 sanity
gate reproduced the preserved exit-mechanics reference with 0 trade-SET mismatches.

**A verification near-miss handled by TIGHTENING rather than widening.** The sizing check initially
reported 26–30/30 across five arms, every discrepancy exactly £0.01. Diagnosed as a harness artefact,
not an engine divergence — the engine LOGS balance rounded to 2dp and the sizing function ROUNDS its
output to 2dp, so a half-penny boundary flips the last penny — and **proven** by reproducing every
case exactly at a balance inside its own ±£0.005 rounding interval. The fix demands an EXACT match
allowing only the known input imprecision, rather than a 1p tolerance that would have left the check
permanently blind to a real 1p sizing error.

**TWO METHOD FINDINGS OF STANDING RELEVANCE:**

1. **The exact-key paired per-decision instrument is INAPPLICABLE IN PRINCIPLE to any pure
   admission-count or admission-ordering change** — not merely underpowered. **Every shared trade is
   byte-identical: 133/133, 57/57, 27/27 (Pool 2); 139/139, 75/75, 28/28 (Pool 1).** A slot cap changes
   *which* positions are held, never how a held position behaves, so two arms admitting the same
   ticker on the same date compute the same stop and realise the same `pnl_pct` by construction. The
   instrument can only ever return "no difference". (Contrast the exit-mechanics sweep, where the
   max-holding dial moved exit dates on shared keys and the instrument had something to measure.) Any
   future study of this shape should plan the **disagreement-population comparison** as primary from
   the start, plus a decision-level instrument valid at any trade count — **rank-at-admission** worked
   well here and was monotone in both pools (median within-day percentile 4.65 -> 2.04 -> 1.52 ->
   1.49%), confirming the arms expressed the intended behaviour and failed on outcome, not mechanism.
2. **The single-ticker dominance guard PASSED for a reason unrelated to what it checks** — recorded as
   instance 9 of the standing "check that reports clean for a reason unrelated to the thing it was
   checking" class above. See that entry for the generalisation (ratio guards whose denominator is
   inflated by the condition being detected).

**Combined with the position-count study, capital deployment AND concentration are now both settled
levers**: `per_trade_pct` cannot express concentration at all, and a hard slot count expresses it
cleanly (deployment parity, monotone rank-at-admission, caps enforced exactly) and buys nothing
detectable. This extends the existing "Capital deployment is not a usable lever in this system"
synthesis rather than merely sitting alongside it.

**Left open, deliberately.** (a) The drawdown *improvement* at n5/n3 is untested — it was a one-sided
ceiling here, never a criterion, and promoting it now would be exactly the metric-switching
pre-registration exists to prevent; a separately pre-registered study would need to solve the
84-trade sample problem at the design stage. (b) No arm tested a book of *exactly* N positions (see
the ~0.8N mechanism). (c) Nothing here speaks to N between 8 and baseline's 11.78, where n14 was
uninformative.

Per the walk-away sentence, the perturbation robustness test is earned only by an arm firing no
clause; two clauses fire, so it was not run. No changes to `analysis/signals.py`,
`analysis/decision.py`, `config.yaml`, or any live path. No database writes. Nothing adopted, nothing
deployed.

## Share-class exclusion at admission — CAPABILITY ONLY, NOT FLIPPED (2026-08-22)

**ADOPTED ON RISK GROUNDS, NOT RETURN EVIDENCE, AND NO STUDY IS CLAIMED.** The affected population
is a handful of dual-class pairs across 19 years — far below every materiality floor this project
uses (the ~1.08pp cost-adjusted CAGR noise floor, the 3-block resolution floor, the 50-position
evaluability floor). **This is NOT backtest-testable in any meaningful way**, no arm was run, no
pre-registration was written, and nothing here should be read as evidence that it improves returns.
It is a correction to what the sizing model believes it is doing, adopted because the belief was
wrong, not because a number moved.

**The risk is UNINTENDED concentration specifically.** This strategy is deliberately concentrated —
the selection audit's Part 7 established that momentum ranking's whole edge comes from
concentrating, at a real drawdown cost, and the slot-count study found its natural equilibrium is
~12 concurrent positions. None of that is in question. What is wrong is the sizing model holding
double exposure to one company while believing it holds two independent positions: the concentration
this system chooses is fine; concentration it cannot see is not.

**Config-gated, defaulting to CURRENT behaviour.** `trading212_invest.share_class_exclusion: false`.
Shipping the CODE is separate from adopting the BEHAVIOUR, the same pattern as
`admission_ranking_mode` and the `stock_max_holding_days` sentinel. **Not flipped.**

### The live instance that motivated it

**GOOG and GOOGL were both held, both entered 2026-08-12, at ~£2,531 each — roughly £5,100, about
10% of the account, in one company, sized as two independent 5% positions.** Their momentum is
near-identical by construction (+69.7% / +70.7%), so they rank adjacently and are admitted together
whenever either qualifies. The slot cost is real too, not just the concentration: one of the pair
displaced a different candidate that may have been better.

The universe treats them as distinct because the S&P 500 index does; VaR sizes each independently;
nothing checked whether two tickers shared an underlying.

**The currently-open GOOG/GOOGL positions were deliberately NOT touched.** Both ride to their normal
exits, consistent with the standing decision not to force-exit for a configuration change (see "Stop
config changes carry a ONE-DAY LAG"). The exclusion applies to future admissions only, and the
function is never consulted on the exit path at all.

### What it does, and what it explicitly does not

A **candidacy filter only**: admission skips a candidate whose share-class sibling is already held.
Sizing, ranking, the momentum gate, the entry gate and every exit reason are untouched, and a held
position is never force-exited because a sibling exists.

**"HELD" INCLUDES POSITIONS ADMITTED EARLIER IN THE SAME WALK, and that is load-bearing rather than
a detail: the live incident was a SAME-DAY admission, so neither was held when the other was
evaluated, and a check against only previously-held positions would not have prevented it.**

### The map was derived from the data, not from recollection

`analysis/universe.py:SHARE_CLASS_GROUPS` + `get_share_class_siblings()`. Candidates were generated
**structurally** from the ticker strings actually present in `price_data` and `universe_membership`
(dotted class forms `X` / `X.A` / `X.B`, and single-letter class suffixes), then each was
**confirmed by daily log-return correlation** over its overlapping history — two share classes of
one company track each other almost exactly; unrelated companies sharing a prefix do not.
Threshold 0.85. Full detail: `research/share_class_map_2026-08-22/`.

**Confirmed, 4 groups:** GOOGL/GOOG (corr **0.9968**, 4,750 overlapping days), FOXA/FOX (**0.9874**,
1,793), UAA/UA (**0.9776**, 2,512), NWSA/NWS (**0.9705**, 3,181). Every other structural candidate
that could be tested scored **≤ 0.72**.

**Controls, checked in both directions before the list was trusted** (the same discipline the
contiguity adjudication used): the four above were required to be found — all were — and A/AA, C/CA,
K/KO, V/VZ, T/TA were required to be rejected — all were.

**Deliberately excluded, each with a reason, because a false entry blocks a genuinely independent
candidate:**
- **BRK.B and BF.B have full history (4,930 rows each) but NO A-class counterpart exists anywhere in
  this system** — there is no `BRK` and no `BF` row in either table. A group of one blocks nothing,
  so no entry is needed. **The form is the dot, not the dash**: this system stores `BRK.B` / `BF.B`,
  while yfinance expects `BRK-B` / `BF-B`, which is exactly why the earlier sector work could not
  resolve them. Only 11 dotted tickers exist in total.
- **HON/HONA** — structurally a candidate, return correlation **0.234** over 36 overlapping days.
  HONA is a 2026 spin-off, a separate company. Correctly rejected by the method.
- **PH/PHB** — PHB has one price row and zero overlap.
- **CMCSA/CMCSK, DISCA/DISCK, VIAB/VIAC** and similar historical pairs — the second symbol has no
  price data at all, so correlation cannot confirm them and they can never be admitted in any case.
  Left out rather than added on recollection.

**KNOWN LIMIT, stated rather than implied away:** the structural generator only finds pairs whose
symbols share a root. A dual-class pair listed under two unrelated symbols would be missed. All four
pairs live in this universe were found; that is not a proof of completeness.

### One implementation, and Layer 4 forced it to stay that way

`analysis/decision.py:blocking_share_class_siblings()` is the single shared check, called identically
by live order placement (`brokers/trading212.py:_submit_pending_orders()`) and the backtest engine
(`analysis/backtest.py:simulate_portfolio_compounding_mtm()`'s `_admit_group()`).

**It returns the blocking siblings rather than a bool, and both structural layers are why.** The
first version was a bool plus an inline `s in held_share_class_tickers` in live to name the blocker
in its log/note — **Layer 1's AST scanner flagged that re-derivation immediately**. Splitting it into
a bool wrapper plus a detail function then left the detail function live-only — **Layer 4 flagged
that**. Collapsing to one function that returns the tuple satisfies both, with no exemption added and
no dead code. Both scanners did exactly the job they exist for, on a change small enough that it
would have been easy to wave through.

### A real defect in this change, caught by Layer 2

The live held-set was first derived from `get_held_research_tickers()`, which requires
`status='FILLED'`. **A BUY is recorded `PLACED` at submission and only becomes `FILLED` when
`poll_placed_orders()` next runs (hourly, at :00)** — so a just-placed sibling was invisible, and
live and the backtest disagreed the moment the exclusion actually bound. The comprehensive replay's
binding variant caught it; the targeted scenario test did not, because a targeted test only exercises
the case someone thought to write.

Fixed with `brokers/trading212.py:_intended_holding_tickers()` — FILLED open positions **plus** any
BUY still at `PLACED` with no later FILLED SELL. Kept as its own function rather than widening
`get_held_research_tickers()`, whose existing callers want the narrower "actually held" meaning. This
is a genuine production gap, not only a test artefact: between a 14:30 submission and the following
poll, a real committed position is invisible to the FILLED-only view.

### Coverage

All four parity layers, none retrofitted:
- **Layer 1** — scanner keywords `share_class` / `siblings` added (defensive; both call sites
  correctly route through the shared function).
- **Layer 2** (`tests/test_comprehensive_replay_parity.py`) — the full ~320-day walk re-run **twice**:
  once with the flag on and no sibling present (sequence must be unchanged — proves flipping the key
  breaks nothing else), and once with the two replay tickers declared siblings so the exclusion
  **actually binds** across a long capacity-sharing walk. The binding variant asserts the sequence
  genuinely DIFFERS from baseline, so a silent no-op cannot pass as agreement — and it is what caught
  the `PLACED`/`FILLED` defect above.
- **Layer 3** (`tests/test_share_class_parity.py`, new) — drives both real paths through the actual
  incident: two siblings gate-passing on the SAME day with capacity for both. Uses the **real shipped
  map** with the real GOOGL/GOOG tickers rather than synthetic pairs, so a mistake in the map itself
  surfaces here. Also covers the sibling-held-from-an-earlier-day case, the `SKIPPED_SHARE_CLASS`
  resolution note, an unmapped ticker being unaffected, and that **exits are untouched** (both
  siblings held, exclusion on, zero SELLs).
- **Layer 4** — the shared function is called by both sides, no exemption.

**Verified to actually fail, two independent ways.** The parity file contains a permanent
broken-version proof (the backtest's reference to the shared function patched to return "nothing
blocks", asserting the two sides then disagree). Separately, a defect was injected into the **live**
side (`if False and held_siblings`) and the suite re-run: **5 tests failed, all of them the
exclusion-on and parity cases, with the exclusion-off cases still passing** — a localized,
diagnosable failure rather than a blanket break. Restored, all green.

**Non-vacuity is asserted, not assumed**, per the standing rule: the parity module opens with a guard
that the pair it relies on really is in the shipped map (without it every assertion could pass by
admitting both everywhere); the exclusion-off tests are the explicit anchor proving the scenario
admits both siblings; the Layer 2 binding variant asserts a real behavioural difference; and the unit
tests assert the shipped map is non-empty, symmetric and self-excluding.

### Deploy note

`share_class_exclusion: false` ships inert — no behaviour changes on deploy. Flipping it to `true` is
a separate, deliberate decision. There is no one-day lag concern of the kind stop-config changes have
(see "Stop config changes carry a ONE-DAY LAG"): the check runs at **submission** time against the
account's real held set, not at decide time, so it takes effect on the very next submission rather
than the one after.
## TPR instrument mapping — one hand-verified row, resolver deliberately unchanged (2026-08-22)

**`TPR` (Tapestry) was the only FAILED row in `instrument_resolutions` — 1 of 503 — and it was a
resolver matching failure, not an availability problem.** Tapestry is tradeable on T212 and was
actively traded within the week. Full investigation: `research/tpr_mapping_2026-08-22/`.

**ROOT CAUSE: T212's instrument id has the suffix segments TRANSPOSED — `TPR_EQ_US`, not
`TPR_US_EQ`.** Confirmed against a live `get_instruments()` pull from the production box on
2026-08-22, and independently against the cached 2026-08-08 pull; both agree exactly.

```
ticker TPR_EQ_US | shortName TPR | name Tapestry | currencyCode USD | type STOCK
isin US8760301072 | addedOn 2019-06-26
```

Both of `resolve_instrument_candidate()`'s steps miss it: the convention guess `TPR_US_EQ` **does
not exist**, and the fallback search filters on `_tid(i).endswith("_US_EQ")` **before** comparing
shortName, so the one instrument whose shortName really is `TPR` never enters the candidate set.
**This was a zero-match failure, not an ambiguity the resolver correctly declined to guess between.**

### THE NOTE TEXT DESCRIBED THE SEARCH, NOT THE WORLD — worth recording separately from the fix

The stored note read *"no US-listed equity instrument found with shortName == TPR"*. That is
**accurate about what the resolver examined and false about reality**: an instrument with
shortName `TPR` does exist, and was filtered out by the suffix test before the shortName test ran.
A reader would reasonably conclude Tapestry is not on T212 — the opposite of the truth — and go
looking for a listing problem instead of a matching one.

**The generalisable point: a diagnostic message written from inside a filtered candidate set
describes the SEARCH, not the WORLD, and the two diverge exactly when the filter is the thing at
fault.** This is the same shape as the standing "a check that reports clean for a reason unrelated
to the thing it was checking" class, in its message rather than its verdict: the verdict (FAILED)
was correct and useful; the explanation attached to it pointed away from the cause. When a failure
note names a criterion, it should say which candidates were considered, not imply the criterion was
tested against everything.

### The systematic question is closed: 3 of 17,403, one batch, not a pattern

| suffix | count |
|---|---|
| `_US_EQ` | **6,926** |
| `_EQ_US` (transposed) | **3** |

The three are **TPR** (Tapestry), **ELF** (elf Beauty) and **LFUS** (Littelfuse) — all added within
45 minutes of each other on **2019-06-26**. A single T212 data-entry batch artefact, not a naming
variant the resolver should learn.

**`resolve_instrument_candidate()` was therefore NOT modified**, and that is the deliberate call: at
1 failure in 503, a stricter rule that fails visibly beats a looser one that maps wrongly. The
XLF/XLK `MAPPING_CORRECTION` sitting in the closed-positions table at £0.00 is what the looser
version costs. Widening the suffix test to catch three 2019 rows would trade a loud, correct,
once-ever failure for a permanently weaker check.

**FORWARD RISK, recorded so the next person meets a known case rather than a mystery: only TPR is in
the current 503-ticker live pool. ELF and LFUS are not S&P 500 members today, and if either ever
joins the index it will fail in exactly this way** — a visible FAILED row, correctly excluded from
trading, needing the same one-row manual fix and no code change.

### Unambiguous, and the same-ISIN duplicate was checked rather than ignored

`shortName == TPR` returns **exactly one** instrument. A second Tapestry listing shares the ISIN
`US8760301072` — **`COYd_EQ`, EUR, shortName `COY`, added 2025-01-24** — a European line, not a
competing match: different shortName, different currency, not the US listing.

It is flagged rather than passed over because **same-ISIN duplicates are precisely the shape the
XLF/XLK incident had**. Here the discriminator is unambiguous; the point is that it was checked.

### The fix, and how it was verified

One row, hand-verified, values taken from T212's own metadata:
`ticker TPR`, `t212_instrument_id TPR_EQ_US`, `currency USD`, `status VERIFIED`, with a note
recording the manual resolution, the date, and why the automatic match failed.

**`currency` is read from the instrument's own `currencyCode` field, not assumed** — the sleeve
study established that instrument-level currency and account currency differ, and this account is
GBP while the instrument is USD.

**Applied and verified as two separate steps**, per the standing requirement that a DB-mutating
script has a side effect rather than a result: `step2_apply_production.py` writes (refusing unless
the existing row is exactly the expected FAILED row, so a re-run reports instead of overwriting),
and `step3_verify_production.py` **re-queries on a fresh read-only connection** rather than trusting
the apply script's own output. Verified: **503 VERIFIED, 0 FAILED, 503 total**; the TPR row carries
`TPR_EQ_US`/`USD` specifically (asserted NOT to be `TPR_US_EQ` and NOT to be `GBP`); every other row
still resolves via the `_US_EQ` convention; no row left with a NULL id or currency.

No change to `resolve_instrument_candidate()`, no config change, no trading-logic change.

### Operational note

`get_instruments()` returns **17,403 rows** and is rate-limited — two calls in quick succession
during this investigation triggered three back-offs and then a failure. **Cache-first is the right
default**; the 2026-08-08 cached pull
(`research/sleeve_allocation_prereg_2026-08-08/t212_instruments_2026-08-08.json`) answered the whole
investigation, with the live pull used once at the end to confirm it still held.

## EA / membership staleness — check_wikipedia_lag() was scoped to the frozen 40 (2026-08-23)

**A delisted company stayed a live candidate for ten days, ranking on a frozen momentum figure, and
the detector built to notice reported "0 disagreements" the whole time — because it was iterating the
wrong universe.** Full investigation: `research/ea_membership_2026-08-23/`.

### What happened

EA (Electronic Arts) completed a take-private. yfinance legitimately has nothing new to return, so
its price series froze at **2026-08-10** while the rest of the pool moved on. Its trailing 12-month
momentum therefore stayed **fixed at 0.28848** while every other candidate's changed, and the
submission-time price-sanity check compares against that *same* stale close, so it structurally
cannot catch this.

**This was not theoretical. EA produced eight queued decisions between 2026-08-12 and 2026-08-21, all
at an identical frozen momentum and a frozen price of 209.70 — six `REJECTED_CAPACITY`, and one still
`PENDING` at £2,611.90.** It survived the ranking cut for ten days and escaped admission only because
the book was full.

**Frozen is not inert, and this is the sharpest detail:** as other candidates' 12-month windows roll
forward, a frozen ticker *drifts* in relative rank rather than holding a fixed position. It is not
even stably wrong.

### THE REAL FINDING — instance 8 of the standing degenerate-check class

`analysis/universe.py:check_wikipedia_lag()` iterated **`STOCK_MEMBERSHIP` — the frozen 40-ticker
curated research universe — not the 503-ticker live pool.** It was therefore structurally blind to
**463 of 503 live candidates (92%)**, and it reported clean on exactly the case it exists to catch:
**Wikipedia had already removed EA.** A correctly-scoped detector would have flagged it, and would
have flagged three more besides.

Same shape as `scan_unverified_tickers()` being scoped wrongly, and as `detect_price_cliffs()` scoping
to live tickers while the historical population went unchecked. The tell was visible in the stored
output the whole time — `"checked": 40` — and nobody read 40 as "of 503".

**Rescoped to the live pool in both directions.** `checked` went **40 → 506**. The two directions are
not symmetric and must not be conflated: `ours − theirs` are tickers we may still ADMIT, `theirs −
ours` are tickers we do not carry at all (no price data, cannot be candidates).

### The four-stale / three-untracked split, and why it decided the fix

| | fja05680 (our source, snapshot **2026-06-30**, 53 days old) | Wikipedia (current) |
|---|---|---|
| constituents | 503 | 502 |
| stale — we treat as eligible, Wikipedia has removed | **AVB, CBOE, EA, EQR** | — |
| entrants we do not track at all | — | **FERG, RDDT, VMRK** |

**Only EA is dangerous, and the distinction is the whole argument.** AVB, CBOE and EQR still have
current price data — removed from the *index*, still *trading*, momentum live and correct. They need
the membership path and would be **wrongly blocked** by a staleness gate. EA needs the staleness gate,
and the membership path would have arrived weeks late.

**A membership fix depends on the source catching up; a price-staleness gate does not.** These are
complementary, not alternatives.

### What was built

**1. `check_wikipedia_lag()` rescoped** to `get_live_stock_candidates()`, reporting `stale_members`
and `untracked_entrants` separately.

**2. `compute_membership_health()`** — three categories by *consequence*, computed by the
`membership_refresh` job and stored; nothing recomputes at render.
- `dangerous` — live candidate whose PRICE DATA stopped updating. The risk category.
- `source_lag` — source still lists, Wikipedia removed, **still trading**. Informational.
- `untracked` — Wikipedia lists, our source never carried. Missed opportunity, not risk.

**`dangerous` is deliberately NOT a subset of `source_lag`**: a ticker can be price-frozen while both
sources still list it, which is precisely the case a source-dependent fix cannot reach. A ticker
appears in at most one category, `dangerous` taking precedence.

**3. Weekly email section**, one shared wording for the text and HTML bodies, compact, not a second
table (asserted: exactly 2 `<table>` elements survive).

**4. `stale_price_gate_trading_days` — a candidacy gate, shipped OFF (`0`).** A candidate more than N
trading days behind is skipped at admission. Decide path only; `maybe_exit_positions()` calls
`_ticker_state()` too and is deliberately **not** gated — a held position whose data goes quiet rides
to its normal exit.

### ONE THRESHOLD, TWO ANCHORS — do not harmonise them

The threshold is **3 trading days**, shared with `main.py`'s Data Coverage panel. The **anchor
differs, deliberately**:

- The **panel** measures against **wall clock**, because its job is to tell a human that ingestion has
  stopped. **It should shout during an outage.**
- The **gate** measures against **the pool's own high-water mark**, because its job is to keep trading
  correct. **It must not fire during one.**

Anchored to wall clock, an ingestion outage would make all 503 candidates stale simultaneously and
halt trading — 503 apparent delistings. Anchored to the pool, an outage leaves every ticker equally
stale (gap 0) and blocks nothing, while a single ticker going quiet stands out immediately.

**One notion of staleness, one number, two reference points because the two consumers have opposite
requirements during the same event.** A test pins both the shared constant and the panel's own
`_FRESH_TD = 3`, so they cannot drift apart — and this paragraph exists so nobody later "fixes" the
apparent inconsistency and reintroduces the failure mode.

### Regression check — the stopping condition was not met

The agreed condition: if enabling the gate materially moves a 19-year backtest, it is firing on
ordinary data gaps and the change stops.

**A literal backtest diff cannot be run, and that is a design property, not an omission.** The gate is
live-only because `simulate_portfolio_compounding_mtm()` builds its event stream from each ticker's
OWN rows — a ticker whose data stops simply stops being a candidate there. Enabling the gate cannot
change a backtest by construction (verified directly, and recorded as the `EXEMPT_LIVE_ONLY` reason).

Measured directly instead, on the population the gate applies to:

| | |
|---|---|
| window | 2024-01-01 → 2026-08-21, 688 sessions |
| (candidate, day) pairs evaluated | **341,432** |
| pairs the gate would have fired | **6 (0.0018%)** |
| distinct live candidates ever gated | **1 of 503 — EA** |

It fires on the one ticker it was built for and nothing else in two and a half years. Across all 798
tickers in `price_data` (including the delisted population that cannot trade) it fires 1,722 times on
45 symbols — the gate correctly identifying frozen series in a population live admission never draws
from.

### Two process findings worth more than the fix

**The report nearly published a reassuring blank from stale state.** The first health run used the
local DB copy, whose `price_data` ends **2026-08-07 — earlier than EA's last bar of 2026-08-10**. The
staleness gap computed to **zero** and `dangerous` rendered **empty**: a clean-looking result caused
entirely by stale derived state, which is the same failure shape being fixed. Caught only because
`pool_max_date` was visible in the output. **That is why the anchor date is printed in the email and
tested as load-bearing** — if the section ever renders against stale state, the date makes it visible
instead of reporting nothing.

**Injection testing found a real hole in the new tests.** Two defects were injected: ignoring the
gate's result (**caught** — one specific failure) and replacing the pool anchor with `date.today()`
(**NOT caught** — all five live tests passed). Every live test *mocked* `_pool_max_price_date()`, so
nothing exercised the anchor itself — the single property that makes the gate safe to enable. Two
direct tests were added; the wall-clock injection now fails both. Had the injection not been run, a
wall-clock anchor could have shipped behind a green suite.

### Status

Shipped **inert**: `stale_price_gate_trading_days: 0`. The rescope and the email section are active
(visibility, not behaviour). Flipping the gate to `3` is a separate, deliberate decision.

**Ordering constraint, honoured and worth restating:** the email section must never ship ahead of the
rescope. A section fed by the blind detector would have printed "no disagreements" every week —
false reassurance, worse than no reporting, because it looks like the question is being monitored.

Full suite 1,248 passing.

## Stop config changes carry a ONE-DAY LAG — a known architectural property, not a bug (2026-08-12)

**`pending_orders.stop_price` is computed at DECIDE time (21:30 EOD) and used verbatim at SUBMIT time
(14:30 the next day). Any change to `atr_stop_multiplier` landing between those two points has no
effect on the orders already queued — they are placed at the OLD multiplier.** This is the
decide-then-submit architecture working as designed, not a defect: recomputing the stop at submission
would mean the position is sized and stopped against different information than the decision was made
on, and would introduce its own class of live/backtest divergence.

**Found the hard way on the exit-mechanics flip.** `atr_stop_multiplier: 3.0` deployed 12:15; the
book was flushed; the 14:30 submission then rebuilt it from decisions made at **21:05–21:06 the
previous night**, so all 18 new positions were stopped at **exactly 2.000× ATR**. Confirmed by
recomputing ATR from `price_data` per ticker and dividing — the implied multiplier was 2.000 for all
18, min/median/max identical.

**THE CHECK THAT CAUGHT IT, and the one that would not have.** Reading `config.yaml` on the server
returns `3.0` and looks like proof; it only proves the file deployed. **The stops sitting at 3.0×
is a different claim**, and the only way to test it is to recompute ATR from stored prices and divide
`(entry − stop) / ATR`. Stop distance as a raw percentage cannot distinguish the two either — it is
`multiplier × ATR/price`, and ATR varies enough across tickers that a 2.0× book and a 3.0× book
overlap heavily in percentage terms (here 6.05%–22.78%, entirely plausible under either). **This is
another instance of the standing "check that reports clean for a reason unrelated to the thing it
was checking" class** — the config read is a valid check of deployment and a worthless check of
behaviour.

**HOW TO RE-RUN THIS CHECK — the two config keys live in DIFFERENT sections, and reading both from
one section silently produces garbage (recorded 2026-08-13).** The multiplier is
**`config.backtest.atr_stop_multiplier`**; the ATR period is **`config.signals.atr_period`** (14).
**`config.backtest.atr_period` exists as a lookup but is `None`** — so the natural-looking
`config["backtest"]` for both would pass `None` as the period into `_atr()` and yield nonsense rather
than failing loudly. The live decide phase itself reads them from the two separate places
(`brokers/trading212.py:1834` takes the period from `signals`, `analysis/volatility.py:36` takes the
multiplier from `backtest`), so any re-check must do the same or it is not reproducing what
production actually did.

**Worked confirmation, 2026-08-13 — the lag cleared exactly as this section predicts.** The
2026-08-12 EOD's 276 queued decisions recomputed to **min = median = max = 3.0000×** across all 276
rows (single bucket at 3dp), while the 18 positions actually open that morning — decided 2026-08-11,
before the flip — recomputed to **2.0000×** against ATR truncated to their own decide date. Both
halves matter: the second is what identifies the flush target unambiguously, and it requires
truncating the price series to the decide date rather than using today's ATR (against today's ATR the
same 18 positions scatter across 1.75–2.97 and look like neither multiplier). Alignment was confirmed
rather than assumed by checking `decided_price` equals the last stored close for every row.

**The operational implication, stated so a future session does not rediscover it:** change any
stop-related config, then **expect one day of stale-stop admissions**, and flush the affected
positions if it matters. It matters more now than it used to: with `stock_max_holding_days: 0` a
mis-stopped position **no longer ages out** — under the previous 30-day cap it self-corrected within
a month, whereas now it exits only on stop/signal/crisis, and the winning arm's own mean hold was
149 days. So a one-day configuration lag can occupy account capacity for months.

**The remedy that was used, and the one that was rejected.** Used: flush the affected positions
through the system's own exit path (temporarily set `stock_max_holding_days: 1`, let `MAX_HOLDING`
close them, restore), which cancels each broker-side stop before selling. **Rejected: hand-editing
the stored `stop_price` values in `pending_orders`/`invest_orders` before the broker-side stops are
placed.** It is technically possible in the window before `poll_placed_orders()` runs, and it is the
kind of fix that looks fine and is not — it puts hand-written values where the engine's own
computation belongs, in production, with no test covering the result.

**THE FLUSH REMEDY HAS TWO TRAPS, BOTH FOUND 2026-08-13 BEFORE USING IT A SECOND TIME. Read this
before reaching for `stock_max_holding_days: 1` again — the paragraph above describes the remedy as
though it is simply available, and it is conditionally available at best.**

**Trap 1 — it cannot flush a position less than ~24h old, and fails SILENTLY by doing nothing.**
`brokers/trading212.py:2739` computes `holding_days = (datetime.utcnow() - entry_date).days` —
**integer truncation** — and `evaluate_exit_reason()` fires on `holding_days >= max_holding_days`. A
position filled at 14:30:29 yesterday is still `.days == 0` at 14:00 today and only becomes `1` at
14:30:29, so with the hourly exit check running at `:00` the first check that can fire is **15:00**.
Measured directly on the 18 positions this was considered for: filled 2026-08-12 14:30:29–14:36, so
13:00 → 0 days, 14:00 → 0 days, 15:00 → 1 day. **The consequence is that the flush lands AFTER the
14:30 submission, so capacity is not free when the submission runs and the book is not rebuilt until
the following day** — the opposite of the 2026-08-12 flush, which rebuilt same-day only because its
target positions were already several days old and fired at the very next hourly check. Same
mechanism, opposite outcome, entirely determined by target age. **A flush that fires zero exits is
indistinguishable from a flush that has not run yet**, which is this file's own recurring
degenerate-check class — verify by checking `.days` against the real `filled_at` values first, not by
waiting to see whether anything closes.

**Trap 2 — `stock_max_holding_days: 1` silently MIS-SIZES anything decided while it is set, and this
is the more dangerous of the two.** The cap's second consumer is
`resolve_var_horizon_days()`, which returns the cap verbatim when it is not the `0` sentinel — so `1`
gives VaR a **1-day** sizing horizon, scaling by `sqrt(1)` instead of `sqrt(30)≈5.48`, i.e. **~5.5×
LARGER** positions before the `per_trade_pct` ceiling clamps them. Names already at the ceiling are
unaffected; names below it (the volatile ones — e.g. SNDK sized at £827 against a £2,564 ceiling) are
not. **This is instance 7's `9999` trap arriving from the opposite direction** — the same two-consumer
divergence that made the sentinel necessary, and the reason `0` is not merely a tidier way of writing
a large number. **Operationally: the restore to `0` is not housekeeping, it is a hard deadline before
the 21:30 EOD decide phase**, or that night's entire batch is sized on the wrong horizon. Confirmed
the 2026-08-12 flush did not hit this — it ran 11:15→12:10 UTC, comfortably clear — so the decisions
made that night are clean.

**And the harm this remedy exists to fix is smaller than the paragraph above implies — corrected
2026-08-13.** The stated worry is that a mis-stopped position "no longer ages out" and so "can occupy
account capacity for months". That reasoning holds for a stop that is too WIDE. The actual stale case
is a stop left at the OLD, **tighter** 2.0× after a flip to 3.0× — and a tighter stop fires *more*
readily, so those positions exit *sooner*, not later. They are self-clearing through ordinary
stop-outs. The real cost is running the configuration the exit-mechanics sweep found inferior
(premature exits and churn, which that study identifies as the single largest drag) — not indefinite
capacity occupation.

**Decision taken 2026-08-13, recorded with its reasoning because the conclusion inverts the
2026-08-12 precedent**: 18 positions carrying stale 2.0× stops were **deliberately left to ride**
rather than flushed. Weighing a self-clearing suboptimal configuration against a timed two-deploy
sequence carrying a 5.5× sizing landmine with a hard pre-21:30 restore deadline — where a missed or
failed restore deploy mis-sizes an entire night's batch — the flush was judged the riskier operation
for the smaller problem. **This is not a reversal of the 2026-08-12 flush being correct**: that one
targeted older positions (so it worked same-day) and ran nowhere near the EOD window. The lesson is
that the remedy's cost/benefit depends on target age and proximity to 21:30, and must be re-derived
each time rather than inherited from the precedent.

## Point-in-time fundamentals recorder — DATA CAPTURE ONLY, nothing reads it (2026-08-12)

**Records raw quarterly fundamentals for gate-passing candidates, going forward. It changes no
trading decision, no sizing, no exit, and nothing reads it back into the decision path.** This is an
option bought cheaply, not a study.

**Why it exists.** Four investigations have now found that price-derived entry information does not
predict individual trade outcome (momentum magnitude, path smoothness, peak drawdown, and a
16-feature screen). Fundamentals are genuinely different data — not contained in the price series —
and the quality factor has real published support. But **point-in-time fundamentals are not
obtainable free**, which makes the idea untestable retrospectively. The only way to get a
vintage-correct dataset for this universe is to start recording one.

**THE CONSTRAINT, stated in the table's own docstring and here: nothing reads this into candidacy,
ranking, sizing or exits.** If fundamentals are ever used in a decision it will be after a
pre-registered study, not by accretion. `tests/test_fundamentals_recorder.py` enforces this
structurally — it greps every decision-path module for any reference to the tables or capture
functions and fails if one appears. That test failing is the intended alarm, not an inconvenience.

### Coverage was measured first, and it decided a design question

`research/fundamentals_coverage_2026-08-12/` probed 50 live candidates before anything was built.
**50/50 returned usable data, zero hard errors, zero empty responses**, with all eight raw fields at
92–100% (quarterly) / 98–100% (annual). The "coverage may be too sparse to bother" risk did not
materialise for this universe — though that says nothing about delisted names, where this project's
price coverage has repeatedly failed. Capture is at candidate-list time, so only live candidates are
ever fetched and the measured population is the relevant one.

**The decisive measurement was staleness, not presence.** Median reporting lag: **quarterly 43 days,
annual 224 days**. An annual figure typically describes a fiscal year that ended over seven months
before the decision beside it — self-defeating for a point-in-time dataset. **Quarterly only.** The
6–8pp coverage cost on `revenue`/`net_income` self-heals at the next quarterly fetch; a stale vintage
never does.

**The consequence for any eventual study, so nobody later mistakes this for current-condition data**:
even quarterly means the fundamentals are always ~6 weeks behind the decision. Any study can only ever
test whether **last quarter's** financial position predicts outcome. That is the real question anyone
could act on — you cannot trade on figures that do not exist yet — but it is not "the company's
condition today."

**The 43-day probe figure is the REPORTING lag alone and is NOT this dataset's real staleness — quote
the combined figure instead.** Forward capture cycles the pool at 25 tickers/night, so a newly-filed
quarter waits up to **~20 further days** for its ticker's turn. **The honest number is therefore
roughly 43 + 20 ≈ two months behind the decision at worst**, not 43 days. Acceptable for a dataset
whose entire purpose is being honestly point-in-time, and it is the figure any eventual study must
quote. **The ~20-night cycle is the operational number to watch**: if the candidate pool grows
materially, or `FUNDAMENTALS_CHUNK_SIZE` is ever reduced, capture lag grows with it and this combined
figure moves — the reporting lag will not.

**Fiscal calendars genuinely differ** (AAPL quarter-end 2026-06-30, NVDA 2026-04-30), so the reporting
period is stored **per ticker, per fetch**. A job-level "as of" date would silently mix vintages.

### PROVENANCE is the load-bearing column

Every row is flagged `point_in_time` or `restated`, and **the two must never be pooled in an
analysis**:

- **`point_in_time`** — captured forward, at candidate-list time, before the outcome existed. **The
  only rows that can ever test whether fundamentals predict outcome.**
- **`restated`** — backfilled historical quarters fetched today. These are the *current* version of a
  past quarter, not what was published and knowable then. Companies revise prior periods, and
  acquisitive companies revise them retrospectively.

Pooling them would reproduce the same lookahead contamination that closed the FTSE and curated-list
ideas — and **restated and original figures look identical in the data**, so the contamination would
be undetectable after the fact. The distinction is unrecoverable unless recorded at write time.
The nightly snapshot therefore links **only `point_in_time` rows**, keeping the forward sample clean
by construction rather than by discipline.

**What each kind is good for:** `restated` characterises the DATA (coverage depth per ticker, label
stability over time, field-presence rates) and is **not usable for any claim about what was knowable
on a historical date**. `point_in_time` is the only outcome-study sample.

### Re-fetch APPENDS, never overwrites

If the same ticker/quarter/provenance is fetched again and **any** value differs, a **new row** is
written rather than the existing one updated — flag-don't-delete applied to values rather than rows.
That makes a restatement observable data instead of a silent change. An identical re-fetch writes
nothing and costs nothing, so rows accumulate only on genuine change.

### Volume and isolation

**Forward capture**: `FUNDAMENTALS_CHUNK_SIZE = 25` tickers/night from the existing EOD job — **no new
scheduled job** — at a measured ~3.66s/ticker, so **~90 seconds**. Self-resuming with no progress
table (never-fetched first, then oldest `fetched_at`); a full 503-pool cycle every ~20 nights, so each
quarterly filing is captured within ~20 days of appearing. Bounded by chunk size regardless of pool
growth. **The data-quality backstop's ~19-minute unbroken loop, which silently never completed for two
days, is the precedent deliberately avoided.** A completion record (`fundamentals_fetch` /
`fundamentals_snapshot` / `fundamentals_error`) is written either way, so "did this step run" is
answerable from the record rather than only from the absence of an error.

**Backfill (restated)**: operator-driven like `prewarm_sector_data.py`, not scheduled. Same 3
calls/ticker, so a 25-ticker run is also ~90s and writes ~125 rows.

### The first production cycle half-failed — and how to verify a cycle actually worked (2026-08-13)

**The recorder's very first EOD (2026-08-12) fetched 25/25 tickers and wrote ZERO snapshot rows.**
`_eod_job()`'s fundamentals block referenced `run_date`, which does not exist in that scope (the
in-scope name is `today_utc`; `run_date` is a parameter of `_record_eod_completion()`). A
deterministic `NameError`, firing on the line that builds the snapshot's own argument list — after
the fetch had already written its key. It would have recurred every night. Fixed and deployed
2026-08-13; see the standing "check that reports clean for a reason unrelated to the thing it was
checking" entry, instance 8, for the full account of why it survived.

**The completion-record design above is what localised it, and it is the one thing that worked as
intended** — `fundamentals_fetch` present, `fundamentals_snapshot` **absent**, `fundamentals_error`
populated, pinpointing the failure to between those two calls without needing a log. Two things did
NOT catch it, both worth knowing before trusting this path again: `tests/test_fundamentals_recorder.py`'s
isolation test **re-implements** the scheduler's `try/except` inline rather than executing it, so it
passes whether or not the real block works (a test that mirrors the code under test can only confirm
the mirror); and an empty `candidate_fundamentals_snapshot` had been **pre-authorised as benign**
("low overlap on night one"), an explanation that was also quantitatively plausible — 15 of 276 would
have linked — so the zero read as expected. `test_the_eod_fundamentals_block_actually_succeeds_end_to_end`
now drives the real `_eod_job()` and asserts the `fundamentals_snapshot` key is PRESENT.

**THE VERIFICATION TRAP, and it will catch anyone checking this the obvious way.** Within the same
EOD block, `refresh_fundamentals_chunk()` runs **BEFORE** `snapshot_candidate_fundamentals()` — so
**tonight's rotation extends the fetched prefix before tonight's snapshot reads it.** Checking that
linked tickers fall within the prefix observed *yesterday* therefore reports a failure on a perfectly
healthy system: after the 2026-08-12 cycle the prefix was `A`→`AMAT` (25), and the very next cycle
legitimately links names out to `AXP` (50). **Read the boundary from `ticker_fundamentals` itself,
never from the previous night's figure or from memory.**

**The four checks that actually verify a cycle, in order of what they can and cannot prove:**
1. `fundamentals_snapshot` **present** in the completion record and `fundamentals_error` **absent** —
   presence, not absence-of-error, since an absent error is equally consistent with "succeeded" and
   "never reached".
2. `candidate_fundamentals_snapshot` row count **non-zero** for that `decision_date`. Sparse is
   correct and expected for many cycles; **zero now means something new**, because the path that
   produced zero on night one is the one that was fixed.
3. `SELECT COUNT(DISTINCT ticker), MIN(ticker), MAX(ticker) FROM ticker_fundamentals` must equal the
   alphabetical prefix `sorted(get_live_stock_candidates())[:N]` for the number of cycles run — no
   gaps, no strays.
4. **The join-integrity check, the only one no row count can substitute for:**
   ```sql
   SELECT s.ticker FROM candidate_fundamentals_snapshot s
     JOIN ticker_fundamentals f ON f.id = s.fundamentals_id
    WHERE f.ticker != s.ticker;          -- must return zero rows
   ```
   A plausible count proves a number was produced, not that the right mechanism produced it — this
   asserts each linked row points at **its own** ticker's fundamentals row.

**yfinance's history depth is ~5 quarters per ticker, NOT the 12 the `max_quarters` ceiling allows —
measured, and it changes what the backfill is.** Roughly **15 months** of restated data is available
per ticker, so the backfill is a **shallow one-pass job, not a deep history pull and not an ongoing
project**: once a ticker has been backfilled there is no further history to go back for, and
re-running it later yields the same ~5 quarters (plus whatever has since been filed, which forward
capture already has). **Recorded explicitly so nobody later assumes more history is available than
there is** — the `max_quarters=12` parameter is a ceiling the source never reaches, not a target. The
practical consequence: `restated` is thin enough that its stated purpose (characterising the DATA —
coverage depth, label stability, field-presence rates) is roughly the *most* it can support, which is
less than hoped for even that.

**Isolation is fail-verified, not assumed.** Both entry points never raise internally, and the EOD
call site wraps them again. Five injected-defect runs confirmed the tests actually fail: raising
instead of capturing, referencing the table from `decision.py`, skipping unlinked candidates,
overwriting instead of appending, and letting the snapshot link a restated row. All green after
restore.

**Joinability confirmed by test**: the snapshot keys on `(ticker, decision_date)` and `price_data` on
`(ticker, date)`, so candidates — **including the rejected control population** — join to their
subsequent price paths with no migration.

### The honest timeline

**This will not support a study for years.** At ~30 admissions a year under the current configuration,
with multi-month holds, the forward sample accumulates slowly — and when it is eventually large
enough it will represent **one market regime with a few hundred decisions**, not a 16-block
validation. Recording all ~300 gate-passing candidates nightly (rather than only admissions) is what
gives it both a treated and a control population, and is the only reason the timeline is years rather
than decades. **It is recorded so the option exists, not because it is close to usable.**

## Interactive Brokers unattended authentication — investigated, NOT FEASIBLE as documented (2026-08-11)

**Investigation only — no code, no broker abstraction, no credential handling, no connection attempted,
nothing touching the T212 path.** Full detail: `research/ib_unattended_auth_2026-08-11/REPORT.md`.

**Motivation**: IB is of interest for instrument breadth (US-domiciled ETFs, futures, bonds), which
would unblock the shelved sleeve study — that study died on **UCITS substitution problems** (no
USD-denominated *distributing* sector equivalents; sector legs with no history before 2015; a 1.42–2.86
pp/yr Acc/Dist accounting divergence larger than the effect being measured), **not on its hypothesis**.
See "Sleeve-allocation study" above.

**Verdict: unattended operation is NOT achievable for an individual account by any documented route.**
IB's own Web API Authentication documentation lists eligibility by account type, and the split is
decisive:

| Method | Account types listed as eligible |
|---|---|
| **Client Portal Gateway** (session, username/password) | **Individual Accounts** |
| **OAuth 1.0a** | Advisor · Broker & FCM · Proprietary Trading Group · Hedge & Mutual Fund · Institutional Hedge Fund Investors · Third Party Software Developers |
| **OAuth 2.0** | The same organisational/institutional set |

**Individual Accounts appear against the gateway and nowhere else.** Session auth requires an
interactive login at least weekly — **security tokens invalidate every Sunday 01:00 ET** — and this
account's login sends an SMS code, which cannot be automated by any legitimate means. Ceiling: **~6.5
days unattended, then a human**. Poor fit for a system whose premise is removing discretionary human
involvement from a daily cycle, and the failure mode is **silent**: a missed Sunday login simply stops
trading, with no error.

**Funding is NOT the blocker, which is the practically useful part.** An IBKR Pro live account plus
funding does not grant OAuth to an Individual account. Funding on the assumption that unattended access
follows would be paying to discover something a support ticket answers for free. The docs also state
**no funding figure** — only "fully open and funded" and IBKR Pro type, applying *"whether accessing a
live account or its associated simulated paper account"* — so the threshold needs confirming with IB
rather than guessed at.

**The one open question that would flip this, deliberately left open rather than assumed either way.**
Community sources (notably the `ibind` project's OAuth guide) state individuals *can* register OAuth
1.0a self-service for both live and paper, explicitly noting this *"contradicts the website and some
IBKR support agents."* Not acted on: it contradicts IB's own eligibility list, undocumented paths can be
withdrawn without notice, and this one would sit underneath live order placement. **The action is one
support ticket** — may an Individual IBKR Pro account register OAuth 1.0a for first-party access to its
own and associated paper account, and is that supported or merely tolerated? Ask the funding threshold
in the same ticket. If IB confirms it, this reopens immediately and favourably; the OAuth mechanism
itself is sound (permanent access token, ~24h live session token derived by RSA-SHA256 signed request
plus Diffie-Hellman exchange, `/iserver/auth/ssodh/init` re-callable, no interactive login and no 2FA
because the RSA key IS the factor — IB's own material calls the daily re-auth automatable).

**A methodological note worth keeping, since it generalises beyond IB.** The first pass of this
investigation leaned on the community guide and concluded OAuth *was* available to individuals; reading
IB's actual Authentication documentation reversed it. **"Several practitioners report it works" and
"the vendor documents it as available for your account type" are different classes of evidence, and
only the second is a sound basis for an authentication mechanism sitting underneath live order
placement.** Same shape as this file's other evidence-quality lessons — a plausible secondary source is
not a substitute for the primary one.

**Two findings retained regardless of the verdict:**
- **OAuth 1.0a covers trading features only** — which would be sufficient here: order placement,
  positions and account state are all Trading endpoints. IB's separate *Account Management* API
  (onboarding, funding, statements) requires OAuth 2.0 and this system uses none of it.
- **IB as a PRICE source is a different decision from IB as an execution venue, and a worse one.**
  Pacing is adequate (50 req/s direct, but only 10 req/s via the gateway; `/iserver/marketdata/history`
  capped at 5 concurrent; 429 → 10-minute IP penalty box, permanent block for repeat offenders). The
  real objection is that **switching the price source from yfinance would invalidate every backtest
  figure in this project** — the 19-year history, the ~1.08pp noise floor, the 16-block baseline, Arm
  B's +1.993pp are all computed on yfinance `close`. Execution-venue adoption is contained; price-source
  adoption silently re-bases everything the system has ever been validated against. Never bundle them.

**On terms of service, for the record**: using OAuth would be IB's own sanctioned key-pair credential
used as designed — the SMS challenge is never reached — which is categorically different from
automating a response to one. The approach to avoid is third-party tooling that drives the gateway
login form to inject a 2FA code; its own maintainers describe it as *"not using an officially supported
automatic interface"*, best-effort, with account lock-out risk. **IBC cannot get past an SMS
challenge** — it supports only IBKR Mobile push 2FA and still requires a human to acknowledge the
alert. Nor can 2FA be reduced: it is **mandatory on all IB accounts including paper**, IB *"does not
allow the ability to disable the Secure Login System"*, and the old second-user-without-2FA workaround
has been withdrawn.

**Integration scoping deliberately NOT done** — it was conditional on unattended operation being
feasible, and on the documented evidence it is not. Reopen only if IB's answer to the support ticket
is favourable.

## Current architecture (as of 2026-08-04)

**What this is.** A live, automated trading system ("SP500Invest") that runs a single strategy — 12-month trailing momentum, long-only, on a dynamically-determined, point-in-time-correct S&P 500 universe — against a real Trading212 Invest account. Currently runs against the **demo** account only: `trading212_invest.live` is `false` in config, and `Trading212Client.place_market_order()`/`place_stop_order()` both raise if `live=True` is ever passed to the client constructor — a config change alone cannot enable real-money trading. No regime classifier, no multi-module confluence score, no manual watchlist curation. This is the result of a full V1→V2 rebuild (see `CLAUDE_HISTORY.md`'s "V2 architecture — phased rebuild, Stage 1" onward) — earlier sections of the history file describe the original six-module confluence/regime system; none of that exists in the current codebase (`analysis/confluence.py`, `analysis/engine.py`, `analysis/regime.py` are all gone).

**The live universe is fully dynamic — there is no watchlist to maintain.** `analysis/universe.py:get_live_stock_candidates(as_of=None)` returns every ticker that has ever been discovered as a genuine S&P 500 constituent, filtered to real membership as of the given date via `is_member()`. `refresh_membership_from_source()` re-fetches a public, point-in-time membership dataset once a day (the `membership_refresh` job, 21:15 UTC). Adding/removing a real index constituent requires no code or config change — the candidate pool updates itself the next day. There are no discretionary exclusions (TSLA/META/GOOGL are included). A separate, deliberately *frozen* research universe (`analysis/universe.py:ALL_TICKERS`/`ETF_TICKERS`/`STOCK_MEMBERSHIP`, 33 ETFs + 40 hand-picked stocks) exists only so historical backtest findings in `CLAUDE_HISTORY.md` stay exactly reproducible — it is never used for anything live and should not be treated as "the current universe."

**The ETF sleeve was removed from live trading entirely** (2026-08-02, evidence-based — see history). Every live candidate today is a US-listed stock; `config.yaml`'s `instruments:` section holds no ETF entries.

**The shared decision engine — `analysis/decision.py` — is the single source of truth for every entry/exit/sizing decision, called identically by live order placement and every current backtest engine.** This is a protected architectural invariant (see "There is exactly one implementation of any given trading decision" below). Key functions:

- `evaluate_exit_reason(...)` — exit priority: **CRISIS_EXIT → STOP_LOSS → SIGNAL_OFF → MAX_HOLDING**. (`INDEX_REMOVAL` was removed from this priority chain entirely — losing S&P 500 membership no longer forces an exit; membership only gates new entries.)
- `passes_entry_gate(...)` — signal on AND currently a genuine index member AND not crisis-blocked.
- `resolve_min_momentum_floor(config)` / `passes_momentum_floor(momentum, floor)` — an absolute minimum-momentum requirement on top of the plain `momentum > 0` signal (`min_momentum_pct: 0.02` in config — evidence-based, see history's "Does the entry gate need an absolute minimum momentum floor?").
- `resolve_reentry_cooldown_days(config)` / `is_reentry_blocked(...)` — calendar-day cooldown before re-entering a ticker after any prior order (`reentry_cooldown_days: 0` in config — deliberately the pipeline's own natural floor, not a chosen delay; see history's re-entry cooldown investigations for why 0 beat 30 and why intermediate values were rejected as not robustly better).
- `resolve_dynamic_max_position_gbp(...)` / `resolve_cash_constrained_position_gbp(...)` / `is_position_size_viable(...)` — VaR-based position sizing as a percentage of the account's real, current balance (`per_trade_pct: 0.05`, `max_total_deployed: 0.7`), with an `absolute_max_position_gbp` backstop (rarely binding) and a minimum-viable-order floor. `max_total_deployed`'s own "already deployed" figure is genuine mark-to-market (T212's `investments.currentValue`, unrealized gains/losses included) — confirmed, evidence-based (not switched to cost-basis, which real block-validated history shows is measurably worse), see history's "Deployment-cap accounting — mark-to-market vs cost-basis". **Raised from 0.5 to 0.8 on 2026-08-06 (a deliberate user decision, not a research-backed one), then reset back to 0.7 on 2026-08-07, also a deliberate user decision.** The deployment-cap sweep (`research/deployment_cap_sweep_2026-08-06`, "A cost is safe to defer only while it's constant across the arms being compared" above) tested only 0.50/0.70/0.95 and found cost-adjusted CAGR at every tested level *underperformed* the 0.50 baseline once FX/turnover costs (which scale with the higher trade count a bigger cap enables) were modelled — 0.7 was one of the tested levels, not an untested midpoint. Still safe to run as-is only because live trading remains demo-only (`live: false`); re-run `run_full_validation()` with this value before ever considering enabling real-money trading.
- `is_asset_class_enabled(...)` — the ETF/stock trading-enabled switches (ETF path is permanently off; stock path is `stock_trading_enabled`).
- `is_submission_price_move_blocked(...)` — the submission-time price-sanity check (see below).
- `resolve_admission_ranking_mode(config)` / `resolve_admission_rank_value(...)` (2026-08-09) — the sort key used to order gate-passing candidates for capacity-constrained admission when more qualify than there is room for; does NOT touch the entry gate, `SIGNAL_OFF`, or any other exit reason, which stay keyed to absolute momentum regardless. `admission_ranking_mode` is **`"relative_rank"` (ADOPTED 2026-08-11)** — the cross-sectional 12-minus-1 rank from `analysis/signals.py:compute_relative_rank_series()`. `"absolute_momentum"` (the trailing-12-month value the entry gate itself uses) was production behaviour until that date and is now the shadowed alternative, still recorded in `admission_ranking_shadow` but no longer the ordering that trades. See "Relative-rank admission ordering ADOPTED" below for the adoption record, the evidence, and the AMAT magnitude caveat.
- `resolve_share_class_exclusion_enabled(config)` / `blocking_share_class_siblings(*, siblings, held_tickers, enabled)` (2026-08-22) — a CANDIDACY filter only: admission skips a candidate whose share-class sibling (`analysis/universe.py:SHARE_CLASS_GROUPS`/`get_share_class_siblings()` — GOOGL/GOOG, FOXA/FOX, UAA/UA, NWSA/NWS) is already held, INCLUDING one admitted earlier in the same submission walk. Never touches sizing, ranking, the momentum gate or any exit, and never force-exits a held position. **`share_class_exclusion` defaults to `false` — the code ships inert and flipping it is a separate decision.** ADOPTED ON RISK GROUNDS, NOT RETURN EVIDENCE, with no study claimed — see "Share-class exclusion at admission" below.

**Position count is emergent, not a separately-enforced cap** — it falls out of `per_trade_pct` and `max_total_deployed` together. There is no `max_open_positions` or flat `max_position_gbp` any more (both were removed in favour of the percentage-of-current-balance scheme — see history's "Live execution switched to the compounding two-parameter scheme").

**Execution is decide-then-submit, not decide-and-place.** The EOD job (`scheduled_eod`, 21:30 UTC) computes signals and queues qualifying entries into a `pending_orders` table — nothing is submitted to the broker at decide time. `stock_order_submission` (14:30 UTC, 60 minutes into NYSE's regular session, not right at the open) is what actually places BUY orders, re-checking each queued decision against: `is_submission_price_move_blocked()` (asymmetric — blocks only on a **drop** past `price_sanity_threshold_pct`, 3%; a rise never blocks, since a rise only confirms the momentum thesis), a fresh `is_crisis_mode_active_now()` re-check (a genuinely intraday crisis check, distinct from the daily-bar-only `analysis/crisis.py` functions — see history's "Submission-time crisis re-check"), and real-time capacity (`max_total_deployed`, momentum-ranked skip-and-continue admission — a lower-momentum candidate never blocks a higher one from being tried).

**Exits run on two independent, overlapping layers — kept permanently, by deliberate design, not a temporary state pending consolidation:**
1. **Local evaluation** — `brokers/trading212.py:maybe_exit_positions()`, called from the hourly `market_hours_check` job ("Open Trade Monitor"), evaluates `evaluate_exit_reason()` for every open position using a genuinely live price (not a stale daily close).
2. **Broker-side stop orders** (2026-08-04) — the instant a BUY fills, `poll_placed_orders()` places a real `POST /equity/orders/stop` at that entry's already-computed ATR stop price. T212 monitors and can trigger this continuously, independent of whether this system's own scheduler is even running — a materially stronger guarantee than the hourly local check alone. `check_stop_health()` (folded into the same hourly job, after the exit check) cross-references every position believed to have an active stop against T212's real, live order list (one batched `GET /equity/orders` call, not one call per position) and alerts — never auto-remediates — if a stop has silently gone missing/rejected.

**Both stop-loss layers kept permanently (2026-08-04) — a deliberate reversal of the original plan.** `CLAUDE_HISTORY.md`'s own "Broker-side stop order" section (2026-08-04) originally scoped removing the local `STOP_LOSS` check as a later, separate step once the broker-side mechanism was confirmed working — that plan has been reconsidered and reversed. Both layers now run indefinitely, as intentional defense-in-depth, not a single check with an announced-but-unbuilt backup. Two reasons, both concrete:
1. **The local check is materially stronger than when the original removal plan was written.** It was upgraded (2026-08-03) to read a genuinely live price for the `STOP_LOSS` comparison, closing the once-daily staleness gap that existed when "eventually remove it" was first proposed — it is no longer the weaker of the two layers by default.
2. **A concrete failure scenario only the local check can act on, not just detect.** Order placement is two-stage: BUY, then a separate stop order once the BUY fills (`poll_placed_orders()` → `_place_broker_stop_for_filled_buy()`). If the BUY fills but that second stop-placement call genuinely fails (a transient error, anything), the position sits with **no** broker-side stop at all. `check_stop_health()` would eventually catch this — but it is deliberately alert-only, by design, given the stop endpoint's documented non-idempotency (see its own docstring's "Auto-remediation was deliberately NOT built" reasoning). Alerting is not protecting: until a human sees and acts on that alert (potentially the better part of an hour after the gap opens), the local hourly check is the only thing that can actually close the position without waiting for a person.

**The two layers racing each other is expected, not a bug — verified, not assumed, that this can't produce anything worse than a clean no-op.** Both layers independently evaluate the identical `STOP_LOSS` condition against the identical `stop_price`, so it is normal for the broker's own standing stop to fire first, closing the position before the local hourly check ever gets a chance to (this is expected to be the *common* case going forward, not an edge case). `maybe_exit_positions()` re-reads the position fresh via `client.get_position()` immediately before firing every exit SELL (not just on the pre-existing cancel-404 path, which only ever ran for a non-`STOP_LOSS` exit reason and therefore never covered the `STOP_LOSS`-vs-`STOP_LOSS` race specifically) — if the position is already gone, it skips silently, with no SELL attempt and no alert. For the narrower residual window this re-check cannot close (the broker's stop fires in the gap between that fresh read and the actual `place_market_order()` call), T212 itself rejects a market SELL for a quantity the account no longer holds — confirmed directly, not just reasoned about, that `place_market_order()` raising here is caught cleanly by the existing per-position exception isolation: no crash, no duplicate order, no short position, and every other open position in the same run is unaffected (`tests/test_execution.py::test_exit_race_sell_rejected_cleanly_when_broker_closes_position_after_fresh_check`, `test_exit_race_fresh_check_skips_when_broker_stop_already_closed_position`).

**`reconcile_positions_with_broker()` labels a broker-stop-fired closure precisely as `STOP_LOSS`, not the generic `BROKER_CLOSED_NO_SELL` bucket — and closes a real P&L gap in the process, not just a labeling one.** A broker-side stop firing produces no `invest_orders` SELL row of its own (T212 executes it directly; this system never calls `place_market_order()` for it) — so without this, the closure would never produce a `pnl` row at all once both layers are permanent and the broker-side layer is expected to win most races, not merely be mislabeled. `_record_broker_stop_closure()` (`brokers/trading212.py`) checks, for every locally-believed-open position T212 no longer shows held with no SELL order of our own on record, whether its BUY row has a broker-side stop recorded `PLACED`, and confirms via T212's own order-history endpoint (`_historical_fill()`, reused from `poll_placed_orders()`) whether that specific stop order actually reached `FILLED`. On a confirmed match: synthesizes the missing `FILLED` SELL row (`exit_reason='STOP_LOSS'`, `order_id`=the stop's own id) so `_reconstruct_pnl()` pairs it correctly — this is what makes the Dashboard's "Performance by Exit Reason" section reflect the trade at all, not merely with the right label — marks the BUY row's `stop_order_status` `FILLED` (was `PLACED`), and records a `position_reconciliations` row with `event_type='STOP_LOSS'`, written already `status='ACKNOWLEDGED'` (this is expected, healthy behaviour, not a discrepancy needing manual review the way `BROKER_CLOSED_NO_SELL` is) and deliberately excluded from `send_position_reconciliation_alert()`'s trigger condition — routine, healthy exits should never generate a "needs review" email. If the standing stop is not confirmed `FILLED` (no matching history entry, or a different terminal status), the closure falls through to the existing `BROKER_CLOSED_NO_SELL` handling unchanged, exactly as before this existed.

**Buy-then-stop sequencing — closed out (2026-08-04).** The stop-order call in `poll_placed_orders()` fires only once a BUY row's own status genuinely transitions to `FILLED`, sized to the *real* filled quantity — confirmed directly, including for a genuine partial fill (`tests/test_execution.py::test_poll_stop_placement_sizes_to_the_real_filled_quantity_not_the_intended_one`), and confirmed a BUY that never fills (`CANCELLED`/`REJECTED`) never triggers a placement attempt at all (`test_poll_does_not_place_a_stop_when_buy_resolves_to_cancelled`/`_rejected`). One genuine gap was found and closed, not merely reasoned about as safe: that placement is a single, in-process step with no retry queue of its own — `poll_placed_orders()` commits the BUY row's `FILLED` status, then calls the stop-placement function inline in the same loop iteration. If the process crashes or restarts (a deploy, an OOM kill, a host reboot) in the narrow window between those two steps, the row is left permanently `FILLED` with `stop_order_status` still `NULL` — and *nothing* would ever retry it: `poll_placed_orders()`'s own query only ever selects `status='PLACED'` rows (this row has already moved past that), and `check_stop_health()` is deliberately scoped to `stop_order_status == 'PLACED'` only, explicitly excluding a never-attempted placement (confirmed directly via `tests/test_execution.py::test_stop_health_non_placed_local_status_is_not_checked`, whose own docstring's claim that every non-`PLACED` state is "already-alerted-elsewhere" does not actually hold for `NULL` — a placement that never ran triggers no alert anywhere). `brokers/trading212.py:retry_missing_broker_stops()` closes this: run from `_market_hours_check_job()` right after `poll_placed_orders()`, it finds any locally-believed-open position with `stop_order_status IS NULL` and a real `stop_price`, re-verifies the position is still genuinely held (a fresh `client.get_position()` call — same defensive pattern as the exit-race fix above), and places the stop. Unlike `check_stop_health()`'s deliberate alert-only design, this one auto-retries — a materially different decision, not an inconsistency: a `NULL` status has no ambiguity about whether an order might already be resting at T212 under some other view (the risk that makes blind re-placement dangerous elsewhere), since `place_stop_order()` was provably never called for this position at all. A row with any other non-`NULL` status (`PLACED`, `FAILED`, ...) is left untouched — a genuine attempt was already made and, for `FAILED`, already alerted at the time.

**`extendedHours` on sell orders is set only for `CRISIS_EXIT`.** `Trading212Client.place_market_order(..., extended_hours=False)` only adds the `extendedHours` key to the request payload when explicitly `True`; `maybe_exit_positions()` passes `extended_hours=(exit_reason == "CRISIS_EXIT")`. Every BUY and every non-crisis SELL sends the identical payload as before this existed. The stop-order endpoint (`POST /equity/orders/stop`) has no `extendedHours`-equivalent field at all — confirmed two independent ways, see history.

**Backtest engines — `analysis/backtest.py`.** Several engines coexist deliberately, each frozen for a specific reproducibility reason (see "Two backtest conventions" below and history for the full rationale): `simulate_trades()`/`simulate_portfolio()`/`simulate_portfolio_compounding()` are historical/frozen, kept only so already-published figures stay exactly reproducible. **`simulate_portfolio_compounding_mtm()` is the one engine confirmed to genuinely match live execution** (mark-to-market capacity tracking, momentum-ranked priority admission, the shared re-entry-cooldown/momentum-floor/price-sanity/realistic-intraday-stop-fill rules) — `analysis/validation.py:run_full_validation()` defaults to it. The Backtest page (`main.py`) calls it with `live_universe=True, mark_to_market=True`; `analysis/dd_report.py` generates its PDF from that same result dict rather than calling `run_full_validation()` a second time.

**What every backtest figure in this project does and does not model — audited directly 2026-08-06, standing facts, not assumptions.** State these alongside any reported figure the same way the two backtest conventions must be stated:
- **Prices are split-adjusted but NOT dividend-adjusted.** `data/ingestion.py` pulls with `auto_adjust=False` and stores both `close` (raw) and `adj_close` (dividend+split adjusted); every engine reads `close`, and **`adj_close` is read by nothing in the codebase**. Measured effect on the 3,161-trade continuous-walk run: returns understated by **£11,230.50 (2.32%)**, and 24 of 1,169 `STOP_LOSS` exits (2.05%) fired on an ex-dividend price gap rather than a real decline. Small, and conservative — it understates.
- **No transaction costs.** The engine is frictionless. `compute_cost_sensitivity()` re-reports metrics at 0/10/25 bps round-trip and `run_full_validation()` calls it, but **every headline figure is the 0-bps one**.
- **Fills are same-day close** — verified 3,161/3,161 for entries. Exits too, except `STOP_LOSS`, which — since 2026-08-06 — fires off that day's `low` (not close) and fills at `min(stop_price, that day's open)`, a realistic intraday-touch model (see the next bullet), no longer same-day-close-and-exact-price. Live differs in one remaining respect: it submits entries the *next* day at 14:30 UTC, not the decision day's close.
- **The `STOP_LOSS` fill assumption was known-optimistic and has been corrected — the permanent default in `simulate_portfolio_compounding_mtm()` since 2026-08-06, not an optional variant.** Fully re-simulated (not first-order — an earlier exit frees capacity, which changes subsequent admission/sizing/compounding for the rest of the run): on the full 2007-2026 continuous walk, total P&L drops from £484,476.39 to **£127,167.80 (−73.75%)**, CAGR from 13.60% to **7.05%**, peak-relative max drawdown worsens from −28.53% to **−34.48%**, and 16-block pass rate drops from 10/16 to **9/16** — nearly **1.7×** the audit's own first-order £212,267.47 (43.81%) estimate, the compounding-amplification a per-trade delta cannot capture. Applies only to `simulate_portfolio_compounding_mtm()`; `simulate_trades()`/`simulate_portfolio()`/`simulate_portfolio_compounding()` are deliberately, explicitly exempt — same frozen-reproducibility reasoning as the 2026-08-03 re-entry-cooldown fix, a correctness fix doesn't change whether an engine's own already-published figures need to stay reproducible. **Honest limit, stated plainly: this closes the gap much further, it does not make backtest and live byte-for-byte identical.** A daily `low` records only that the stop was touched at *some* point that day, never when or via what path — a morning dip that fully recovers by the close is indistinguishable in this data from the identical low reached late in an otherwise calm session, even though a real, continuously-monitored broker-side stop fires at genuinely different moments with genuinely different context in each case. True intraday/tick history was never captured historically and cannot be reconstructed now — closeable only going forward, never for the past. See history's "Realistic (intraday-touch) STOP_LOSS fill made the permanent default" and `research/realistic_stop_fill_resim_2026-08-06/` / `research/realistic_stop_fill_permanent_2026-08-06/`.
- **The historical universe carried a permanent survivorship bias for the population that failed; the population that merely left the index was recovered — audited and pre-warmed 2026-08-06.** `data/ingestion.py:get_all_watchlist_entries()` was the root cause: it scoped daily ingestion to *today's* live candidates only, so any ticker that lost membership stopped being ingested forever. Fixed (now sources from `analysis.universe.get_all_known_stock_tickers()`, every ticker ever discovered as a genuine constituent — trading eligibility via `is_member()` is unaffected, only ingestion scope changed) and deployed the same day, followed by a supervised one-off pre-warm backfill: `price_data` grew from 540 to **871 distinct tickers (+331, 1,005,810 new rows)**, missing-from-`price_data` fell from 701 to **370** (145 true-survivorship, correctly irrelevant to any block; 225 "other reason," all now confirmed-dead via a real fetch attempt rather than merely untested). **Dominant-era (blocks 3-9) mean usable coverage rose from 64.3% to 76.9%** — largest gains in the worst-covered early blocks, smallest in the already-good recent ones. Coverage is still far from flat across eras, so the era/coverage collinearity this file already flags is reduced, not eliminated. The remaining 370-ticker gap is the genuinely unrecoverable population: recoverability stayed anti-correlated with the crisis-casualty bias specifically (2/9 on a direct test of the textbook 2008-2009 casualties, `BSC`/`WAMUQ`/`LEHMQ`/`CITGQ` still return nothing, `CFC`/`NCC`/`WB` are still confirmed reused symbols) even though the *general* recoverable slice was much larger than that sample implied, since most of the 331 recovered were simply survivors nobody had ever asked for. With real data now present, the controlled-depletion experiment's Arm 3 (blocks 13-15) went from fully degenerate to measurable for 2 of 3 blocks: block 13 **Arm1−Arm3 = −1.41pp CAGR** (real, negative, direction-confirming), block 14 **0.00pp** (a genuine null), block 15 still unmeasurable but now for an unrelated, benign reason (too recent for any real departure to exist). See history's "Universe coverage remediation" thread and its "DISCONTINUITY MARKER" entry for the full record — every coverage or backtest figure in this file computed before 2026-08-06 ~19:00 UTC used the smaller, 701-gap universe; every one after used the 370-gap universe.

**Live/backtest parity is enforced structurally, not just by convention** — four layers (see history's "Structural enforcement for live/backtest parity" and "Layer 4 — one-sided decision rule detection"):
- **Layer 1** (`tests/test_decision_logic_placement.py`) — an AST scanner that fails if a decision-shaped comparison (a threshold, a priority check) appears anywhere outside `analysis/decision.py`, with a reviewed whitelist for genuinely execution-only duplication (e.g. SQL `ORDER BY` vs. an in-memory Python sort expressing the identical rule).
- **Layer 2** (`tests/test_comprehensive_replay_parity.py`) — a multi-day, multi-ticker replay that drives live's *real* code (`maybe_place_orders()`/`maybe_exit_positions()`, only the HTTP layer mocked via a synthetic account-state harness) against the backtest engine over identical price history and asserts the trade sequences match.
- **Layer 3** — `tests/test_live_backtest_parity.py`, scenario-based (4 entry + 5 exit scenarios), the original mechanism.
- **Layer 4** (`tests/test_one_sided_decision_rules.py`) — derives, from `analysis/decision.py` itself, every function's real call sites in both `brokers/trading212.py` and `analysis/backtest.py`, and fails if a decision function is called by only one side without a recorded, reviewed exemption.

**UI — `main.py`, 5 pages, behind per-user login (2026-08-04)**: Dashboard (default landing page — account balance/value history, open positions, closed positions, performance metrics, candidate-queue-adjacent content), Instruments (a searchable list of the live candidate pool with Yahoo Finance links and T212 instrument-resolution status), Backtest (runs `run_full_validation()`, generates the DD report PDF), Status (scheduler health, T212 account detail, candidate queue, unconfirmed exits, log viewer, danger-zone manual controls), Users (admin account management — see below). Several older pages (Daily Briefing, Signals, Charts, Relationships, Robustness, Performance-as-a-separate-page) were removed as V1 concepts or consolidated into Dashboard/Status — see history for each removal's own reasoning.

**Authentication — `auth.py`, per-user accounts (2026-08-04), replacing a single shared Caddy `basic_auth` credential.** The app was previously fronted by Caddy's `basic_auth` (one hard-coded `admin`/bcrypt-hash pair in `/etc/caddy/Caddyfile`, identical for every visitor — see `Caddyfile` in this repo for the template). That directive has been removed from the server's real Caddyfile; Caddy now only terminates TLS and reverse-proxies to Streamlit. Login is enforced entirely inside the app: `data/database.py`'s `User` table (`id`, `username`, `password_hash` — bcrypt, never plaintext — `role`, `created_at`) is the source of truth, and `auth.py` is the *only* code that reads or writes it (`hash_password`/`verify_password`/`authenticate`/`create_user`/`list_users`/`update_user_role`/`update_user_password`/`delete_user`) — nothing else should touch the table directly. `main()` renders a login form (`_render_login_page()`) whenever `st.session_state["auth_user"]` is unset; a successful `auth.authenticate()` call sets it to `{"id", "username", "role"}` for the rest of that browser session.

**Roles and page access:** `admin` sees all 5 pages, including Users (grant/revoke access, reset passwords, change roles — `_render_users_page()`); `demo` sees Dashboard only (the read-only investor-facing "figures" view — the whole app has been read-only, no manual controls, since the Danger Zone removal). The sidebar's page list is filtered per role, and the main-content router independently re-checks `st.session_state["page"]` against the role (`DEMO_ALLOWED_PAGES = {"dashboard"}`) before rendering — so a stale/foreign page key (e.g. inherited from before a mid-session role change) renders a polite "isn't available in your view" notice (`_render_access_denied()`, with a "Return to Dashboard" button) rather than the restricted page or an error. `auth.py` itself refuses to demote or delete the last remaining `admin` account (`ValueError` from `update_user_role()`/`delete_user()`), and the Users page additionally blocks an admin from deleting their own currently-signed-in account, so a session can never lock itself out of the Users page.

**Bootstrapping the first admin account — migrating the pre-existing shared credential.** `data/database.py:_seed_default_admin()` runs once, from `init_db()`, only when the `users` table is empty. It reads the `ADMIN_PASSWORD_HASH` env var — set on the server, in the same systemd secrets drop-in as `T212_API_KEY`/etc. (see "Managing server credentials" below) — as a bcrypt hash to seed the initial `admin` account's `password_hash` directly. Bcrypt's hash format is implementation-agnostic, so the exact hash Caddy's own `basic_auth` was using is reused verbatim: the real password nobody had to re-enter or even know keeps working unchanged, now as the admin account, with the plaintext never touched. Local dev / any environment with no `ADMIN_PASSWORD_HASH` set falls back to a freshly generated random password, logged once at first startup via `logger.warning()` (never persisted anywhere else) — this path is not what production uses.

**Scheduler — `data/scheduler.py:start_scheduler()`, 7 real jobs, all APScheduler `CronTrigger`/`IntervalTrigger`, UTC unless noted:**

| Job id | Trigger | Gating | What it does |
|---|---|---|---|
| `job_dispatcher` | every 60s | none | Executes pending UI-requested jobs (e.g. a T212 snapshot refresh) |
| `scheduled_eod` | daily 21:30 | `_is_scheduled_trading_day()` (day-level NYSE calendar) | The only job that pulls prices; computes signals, queues BUY decisions into `pending_orders`, runs the exit check, purges old resolved `pending_orders` rows, runs `detect_price_cliffs()` and `scan_unverified_tickers()` (recurring backstop for recycled-ticker-symbol corruption — see "Recycled-ticker-symbol corruption" above), and `_refresh_benchmark_comparison()` (the account-vs-buy-and-hold series, recorded as `benchmark_rows_written` in the completion record — see "Benchmark-relative performance tracker" above) |
| `membership_refresh` | daily 21:15 | day-level NYSE calendar | Re-fetches point-in-time S&P 500 membership |
| `stock_order_submission` | daily 14:30 | `_is_nyse_open_now()` (live session, stricter than day-level) | Submits queued BUY decisions, with the price-sanity/crisis/capacity re-checks described above |
| `market_hours_check` | hourly, minute 0 | `_is_nyse_extended_hours_open()` | `poll_placed_orders()` (also places fresh broker-side stops on new fills), `maybe_exit_positions()`, `check_stop_health()` — "Open Trade Monitor" |
| `account_state_refresh` | hourly, minute 5 (offset from the job above specifically to avoid a T212 rate-limit collision — a real incident, see history) | unconditional | `reconcile_positions_with_broker()`, refreshes the T212 account/positions snapshot |
| `weekly_summary` | Sunday 09:00 **Europe/London** (DST-correct, not a fixed UTC offset) | day-of-week only | Sends the weekly status email |

No `monday_premarket` job exists any more (removed 2026-08-03 as confirmed-redundant with the decide-then-submit architecture — see history). Every job with an internal gate fires unconditionally on its trigger and decides for itself whether there's real work to do, rather than approximating the condition with a hand-maintained cron field — this is why none of these jobs carry a `day_of_week="mon-fri"` filter even though most only ever do anything on a weekday.

**Position reconciliation and alerting.** `reconcile_positions_with_broker()` compares what this system's own order history implies is open against T212's real, live positions, flagging (never auto-remediating) three genuine-discrepancy shapes needing manual review — `BROKER_CLOSED_NO_SELL`, `UNRECOGNIZED_POSITION`, `REPEATED_SELL_REJECTION` — plus one non-discrepancy, `STOP_LOSS` (see "Both stop-loss layers kept permanently" above), recorded already-`ACKNOWLEDGED` for the audit trail and never alert-worthy, since it represents the broker-side stop protection working exactly as designed. Every alert in this system (`output/briefing.py`) uses **content-based deduplication**, not a time-based cooldown — compares the current set of affected items against what was last actually emailed, sending whenever that set genuinely changes, never spamming an unchanged, persistent problem repeatedly. (An earlier, simpler time-cooldown design was found to silently suppress a second, different failure landing inside an earlier failure's cooldown window — see history's "Instrument-resolution failure alert" section for the original finding this pattern now follows throughout.)

**Price-cliff monitoring and the Status page's Data Coverage card are scoped to live-tradeable + currently-held tickers, not the full ingestion universe (2026-08-07).** `data/ingestion.py:detect_price_cliffs()` (added 2026-08-06, see history's "Split-adjustment corruption") scans the *entire* `price_data` table for consecutive-trading-day close-ratio breaks outside `[0.5, 2.0]` — including the ~700 historical/delisted tickers ingestion was deliberately broadened to cover the same day (see "Universe coverage remediation" above), most of which can never be traded or held again. Confirmed directly the day this was found: of 4,401 raw hits, only **14** touched a ticker in `get_live_stock_candidates()` or `get_held_research_tickers()`, and every one of those 14 corresponds to a real, dated market event, not corruption — e.g. AIG/HIG/STT (the Sept 2008–Jan 2009 banking crisis), APA/OXY/TRGP all breaking on the identical date 2020-03-09 (the COVID/Saudi-Russia oil price war), MO's real 2008-03-28 Philip Morris International spinoff, KDP's real 2018-07-10 Keurig/Dr Pepper Snapple merger, and PCG's real 2019-01-14 wildfire bankruptcy filing. The Status page's Data Coverage card showed the same pattern for the identical reason: `_price_coverage()` in `main.py` used to report freshness against the full ~1,208-ticker watchlist, so hundreds of permanently-delisted companies (AABA, AAMRQ, AET, ANDV, ABMD, etc. — genuinely gone, never fresh again) showed up as "stale"/"missing" even though none of them affect a live decision. Both were narrowed to the same live-relevant scope (`get_live_stock_candidates() | get_held_research_tickers()`, plus `reference_tickers` for the coverage card) without touching ingestion scope, `price_data`, or anything upstream — the full-table backstop scan and the broadened historical coverage that made it noisy are both still exactly as they were; only what gets *surfaced* to a human (the daily cliff-alert email, `output/briefing.py:send_price_cliff_alert()`, and the Status page card) changed. `data/scheduler.py`'s EOD job now records both `price_cliff_hits` (the alert-worthy, live-relevant count) and `price_cliff_hits_total` (the raw full-table count) in its completion record, so the discarded noise stays visible for anyone who goes looking without being emailed about it daily.

**`close` vs `adj_close` smoothing — a standing fact about this project's data, not specific to any one investigation (2026-08-08).** `yfinance` retroactively rebases `close` (even under `auto_adjust=False`) for **ordinary stock splits only** — confirmed both by the 2026-08-06 audit (296 real splits checked, all showing a smoothly-adjusted series) and directly here: a **fresh pull from yfinance today reproduces the identical cliff** for a merger (`KDP`), a spinoff (`MO`), and a special dividend (`WY`) discovered during the 14 live/held price-cliff hits' classification (`research/split_cliff_remediation_2026-08-08/`). **Mergers, spinoffs, and special dividends are never retroactively smoothed in `close` — only in `adj_close`, which `analysis/signals.py` stores but reads from nothing.** This means `_find_retroactive_restatement()`'s re-fetch-and-replace repair (built and validated for the 2026-08-06 incident, which found zero actual cases to repair) **cannot fix this class of cliff** — a re-fetch reproduces the exact same discontinuity, not a corrected one. Discovered because the obvious next step ("just re-fetch it") was checked empirically before being trusted, not assumed to work because it worked for the split case. **Deliberately not repaired by any other mechanism either** (a computed price-continuity splice was considered and explicitly rejected — see `research/split_cliff_remediation_2026-08-08/REPORT.md`'s "Decision" section: it would fabricate prices no exchange ever printed and store them as observed values on the live path, the corrected series already exists unused in `adj_close`, and every affected event (2007–2018) is confirmed, not assumed, to fall outside every momentum window this system has ever actually evaluated a trade against). The 14 originally-found live/held cliffs are recorded as classified exceptions (`data/ingestion.py:_KNOWN_PRICE_CLIFF_EXCEPTIONS`, filtered from alerting via `filter_known_price_cliff_exceptions()` — called by the EOD job strictly after live-scoping, `detect_price_cliffs()` itself and its live scoping are unchanged) rather than repaired: 9 genuine market moves (including `GL`'s 2024-04-11 Fuzzy Panda short-report crash, confirmed directly and contradicting an initial assumption that it was a corporate action), 3 confirmed-but-unrepairable corporate actions (`KDP`/`MO`/`WY`), 1 already-correctly-reflected corporate action (`EXPE`), and 1 genuinely unresolved case (`JCI` — a real split event exists in `yfinance`'s data but its direction contradicts the observed move, and the underlying cause was not identified). **Flagged as a future research item, not implemented**: whether the momentum signal should read `adj_close` instead of `close` — would address this artefact class and the existing dividend-exclusion bias in one variable change, but needs both pools (per "Dual-provenance validation" above) and a criterion that doesn't hinge on a small block-pass delta (per "The 16-block pass rate has a measured resolution floor" above) to evaluate correctly.

**The exception registry gained its first CURRENT-WINDOW, actively-held entry — `MRNA` 2026-08-19 (2026-08-22).** `_KNOWN_PRICE_CLIFF_EXCEPTIONS` now holds **15** pairs, not the 14 the paragraph above describes — that paragraph remains an accurate record of the 2026-08-08 batch and is not restated here; this is the current total. The new entry is a confirmed `GENUINE_MARKET_MOVE`: the Moderna/Merck Phase 3 INTerpath-001 melanoma readout met its primary endpoint pre-market on 2026-08-19, MRNA closed **+176.97%** (62.96 → 174.38, ratio 2.7697) and market cap moved $25B → $69B. Full detail: `research/mrna_cliff_2026-08-22/REPORT.md`.

**Why this one is different from the other 14, and why it was still only an alert change.** Every prior entry is historical (2007–2024) and was already cosmetic when classified — outside every momentum window the system had ever evaluated a trade against. This one sits **inside every live 252-day window**, and MRNA was an **open position** when it fired (`invest_orders` id 74, entered 2026-08-12 at $61.81). It was therefore recorded as a deliberate decision rather than routine list maintenance. **What is suppressed is the ALERT, not the signal**: MRNA's momentum (+439.72% as of 2026-08-21) is real and keeps feeding admission ranking unchanged, and the registry's `(ticker, date)` key means a future MRNA cliff on any other date still alerts normally — asserted directly by its own test rather than left implied.

**The classification method that closed it, worth reusing — three checks independent of `.splits` metadata, plus a direction argument.** `.splits` was empty for MRNA's entire history, and the standing instruction not to default to GENUINE on an empty `.splits` is correct precisely because an unadjusted spinoff or special dividend may not appear there at all. The verdict rests instead on: (1) `close` and `adj_close` moving in **exact lockstep** (2.7697 both) — the genuine-move signature, versus the `adj_close`-smooth-while-`close`-jumps corporate-action one; (2) **intraday shape** — the open at 116.02 sat *partway between* the two closes and the day's range was 1.5434x (54.3%) against a 1.0624x median, where a corporate-action rebasing scales O/H/L/C by **one constant factor** and leaves the intraday range untouched; (3) **volume 32.3x UP** (199.3M vs a 6.18M median) where a 1-for-2.77 reverse split divides share count and predicts **~0.36x** — roughly 90x in the wrong direction, and the least judgement-dependent of the three. **The remaining gap is closed by DIRECTION rather than by absence of evidence**: spinoffs and special dividends remove value and can only push price DOWN — every un-smoothed case in the 2026-08-08 study was a fall (KDP 0.179, MO 0.301, WY 0.381) — so a **2.7697x RISE** leaves only a reverse split, which *does* appear in `.splits` and *does* retroactively rebase `close`. No third mechanism exists. **Generalisable: for an UPWARD cliff the un-smoothable class is excluded a priori, which is a much stronger position than the downward case this project has usually faced.**

**A gap confirmed to be real before it was needed, not under pressure.** Had this classified the other way, there would have been **no mechanism to exclude a ticker for a price artefact**. `ticker_data_quality` is scoped to identity verification (`verify_ticker_identity`: `quote_type`/exchange/name) and MRNA is correctly `CLEAN` there — quarantining it would have been the same domain-scoping error this file already records three times (see "Data-quality backstop was silently broken since deploy"). Recorded here so the absence is known rather than discovered mid-incident.

**One incidental confirmation of the 2026-08-13 leave-the-stale-stops-ride decision.** MRNA was decided 2026-08-11, the day before the `atr_stop_multiplier` 2.0→3.0 flip, so it carries a stale **2.0×** stop (recomputed directly: implied multiplier exactly 2.0000, ATR truncated to the decide date, multiplier from `[backtest]` and period from `[signals]` per the two-section warning). It is one of the 18 positions left to ride, and it is entirely inert — the stop sits **63.6% below** a position up ~145%. Concrete support for that decision's own reasoning: a stale stop after a widening flip is **tighter, not looser**, so it self-clears rather than occupying capacity indefinitely.


**Recycled-ticker-symbol corruption — identity verification is the research-candidacy admission gate (2026-08-07).** The 2026-08-06 survivorship-bias remediation (see "Universe coverage remediation" above) broadened price ingestion to ~700 historical/delisted tickers, but only spot-checked 28 of the 331 newly backfilled — enough to miss a real, systemic problem: an unrelated entity's price history landing under a delisted company's old ticker symbol (the same mechanism already documented for `CFC`/`NCC`/`WB`, now confirmed to recur at scale, not a one-off). Found via a £2.21M P&L anomaly at `max_total_deployed=0.70` traced to a single corrupted `TIE` trade with an ~819x fake "return"; a full-table detector run across all 871 tickers (`research/recycled_ticker_investigation_2026-08-07/`) initially used reversal-shape classification (does the price series revisit the same regime it left, implying two different underlying entities spliced together) and flagged 22 `LIKELY_RECYCLED` + 17 `LIKELY_CORPORATE_ACTION` of the 408 remediation-added tickers — but a spot-check on `PALM` (labelled "no oscillation, likely genuine" despite its entire stored history postdating the real Palm Inc.'s 2010 delisting by 4+ years) proved reversal-shape alone has a structural blind spot: a monotone recycled series is indistinguishable from a genuine one-time event by a reversal test.

**The fix replaces reversal classification with identity verification as the primary, decisive gate.** `data/ingestion.py:verify_ticker_identity(ticker, config=None)` runs two checks, and **they are not symmetric evidence** — stated plainly here so a future reader doesn't over-trust the mechanism as two independent signals: **Check B (date-coverage overrun) is the primary, discovering signal.** It compares `price_data`'s stored `MAX(date)` against the ticker's last real `universe.get_membership_windows()` end_date (excluding the `_SENTINEL_END`/2099-12-31 "still active" case) — data existing years past a company's real index departure is only *suspicious* on its own, since plenty of real companies leave the S&P 500 and keep trading for years (the case that follows is exactly this). **Check A (`yf.Ticker().info` quoteType/exchange/long_name) is what actually decides whenever Check B fires** — `QUARANTINED` only if Check A does *not* affirmatively confirm a real, named equity; if it does affirm one, the verdict is `NEEDS_REVIEW`, not `QUARANTINED` (the FNMA fix — the first version of this gate wrongly quarantined `FNMA`, a ticker the investigation had already confirmed genuine, because Check B alone conflates "left the index" with "stopped trading," which FNMA's own real 2008 conservatorship-and-delisting-from-major-exchanges-but-continued-OTC-trading history contradicts). Both checks pass → `CLEAN`; Check B not applicable (the `_SENTINEL_END` case) → decided by Check A alone. Never raises per-ticker; rate-limited the same way as `_fetch_ticker()`.

Run across all 408 remediation-added tickers (`research/data_quality_remediation_2026-08-07/`, ~1,123s runtime): **83 QUARANTINED, 209 NEEDS_REVIEW, 116 CLEAN** — confirmed on both local and production (the latter via the SSH pre-warm step, independently re-queried directly against the live DB). 112/408 (27.5%) have a membership window that never closed, so Check B could not apply and their verdict rests on Check A alone — a stated, real coverage limit, not swept into the headline count. Against the superseded reversal classification: 19 of the original 22 `LIKELY_RECYCLED` were confirmed `QUARANTINED`, but **59 `QUARANTINED` tickers were never flagged by reversal classification at all** — the PALM blind spot, generalized, and the actual justification for this gate replacing that classifier rather than merely supplementing it.

**Quarantine, not repair — `ticker_data_quality` table, flag-and-exclude, never delete.** `data/database.py:TickerDataQuality` (`ticker`, `status` — `QUARANTINED`/`CLEAN`/`NEEDS_REVIEW` — `reason`, `evidence` JSON, `detected_at`), written via `write_data_quality_status()` (upsert, never deletes a row — a re-check overwrites status for the same ticker). No `price_data` row is ever modified or truncated by this mechanism. **`analysis/universe.py:get_research_stock_candidates()`** (`get_all_known_stock_tickers() - get_quarantined_tickers()`) is the single admission gate for research/backtest candidate-pool construction — **deliberately not wired into `get_all_known_stock_tickers()` or `get_live_stock_candidates()` themselves**, both confirmed byte-for-byte unaffected, since no quarantined ticker is (or can be) reachable via the live path today. `NEEDS_REVIEW` tickers are **not** excluded by this gate — they remain in `get_research_stock_candidates()`'s pool, unresolved, pending human adjudication.

**NEEDS_REVIEW adjudication, phase 2 — contiguity (Check C), 2026-08-07.** The 209-ticker `NEEDS_REVIEW` pile is mostly the FNMA pattern (Check B fired, Check A affirmed a real equity) — it contains two genuinely different populations that identity verification alone cannot separate: a real company that left the index but kept trading (should stay `CLEAN`-adjacent) versus a recycled symbol Check A couldn't condemn (should be `QUARANTINED`). `data/ingestion.py:check_boundary_contiguity(ticker, last_membership_end=None)` tests the discriminating signal directly: does `price_data` connect continuously across the real membership end_date, searched **unbounded** across the ticker's entire stored history (not a fixed window — an early version capped the search to `[end_date-90d, end_date+365d]` and silently mis-scored `PALM` and confirmed-recycled control `NCC` as "insufficient data," since their real gaps, ~12 years and ~3.3 years respectively, are simply bigger than any reasonable window). Gap measured in real NYSE trading days (`pandas_market_calendars`), not raw calendar days. **`_CONTIGUITY_GAP_THRESHOLD_TRADING_DAYS = 100`** — read off a sharply bimodal observed distribution (129/209 tickers at exactly 0 gap, one at 37, then a real empty gap in the data itself until the next value at 107) rather than fitted; anywhere in the empty [38,106] range produces an identical split.

**Price-scale continuity (median close before/after the boundary) was tested as a second candidate signal and explicitly rejected before being used to decide anything** — `FNMA`'s own scale_ratio is 0.134 (a real ~7.5x apparent "collapse," driven by the genuine 2008 conservatorship price decline, not corruption). A threshold on this signal would have false-positived the one control that mattered most in the whole exercise — the third time in this sequence a plausible-sounding signal misfired specifically on the case that mattered, and the only reason it didn't ship is that every signal here was validated against controls (FNMA, `PALM`, all 19 confirmed `LIKELY_RECYCLED` quarantines) *before* being applied, not after. See "Research scripts must verify decision-engine parity" above for the general form of this discipline — this is the same discipline applied to a research classifier rather than a production decision function.

**Contiguity is a real, honest partial resolution, not a full one — stated plainly rather than glossed over.** Validated with zero false positives against `FNMA` (gap=0, correctly excluded) and `PALM` (gap=3,114, correctly caught), but of the 19 confirmed `LIKELY_RECYCLED` quarantines, **only 10/19 show a gap ≥ 100 trading days** — the other 9 (`BMC`, `BOL`, `CBE`, `CFC`, `GDW`, `MEE`, `PBG`, `PTV`, `TIE`) are confirmed-corrupt cases where an unrelated entity started trading under the recycled symbol almost immediately, leaving **no detectable trace in price data, metadata, or contiguity** — a genuine property of this class of corruption, not a gap in the method that a fourth signal would close. Applied to the full 209: **79 moved to `QUARANTINED`**, **130 stay `NEEDS_REVIEW`** — contiguity's silence is not evidence of cleanliness (9 of the 19 known-corrupt controls are silent by this signal too), so no ticker was promoted to `CLEAN` on this basis. One reason-code distinction was applied on explicit, specific review, not generalized: `CAR` (long_name Avis Budget Group, Inc.) is tagged `ENTITY_DISCONTINUITY_CORPORATE` rather than the default `RECYCLED_SYMBOL`, since its gap timing lines up with Avis Budget's real 2006 spinoff from Cendant — a legitimate corporate event, still two different entities under one symbol (momentum computed across that seam is still meaningless), but potentially salvageable by truncation if repair work is ever undertaken, unlike a squatter series. Five other quarantined tickers (`DEC`, `DIGI`, `EQ`, `MI`, `USBC`) were flagged `LIKELY_CORPORATE_ACTION` by the prior reversal classifier too, but their gaps run 1,107–5,681 trading days (4–22+ years) — nothing like `CAR`'s clean ~107-day spinoff-transition profile — and were deliberately left tagged `RECYCLED_SYMBOL`, not generalized to the corporate-action tag without the same kind of specific, external verification `CAR` got. Final counts: **116 CLEAN, 162 QUARANTINED, 130 NEEDS_REVIEW** — confirmed on both local and production (the latter via the SSH pre-warm steps, independently re-queried directly against the live DB, not the script's own summary) (`research/data_quality_remediation_2026-08-07/PHASE1_CONTIGUITY_REPORT.md` for the full distribution and control table, `step6_quarantined_via_contiguity.csv`/`step6_stayed_needs_review.csv` for the per-ticker outcome).

**The remaining 130-ticker `NEEDS_REVIEW` pile was NOT the final resting state — an independent source found further real signal in it (2026-08-07/08).** Contiguity alone was correctly treated as exhausted (immediate-reuse recycling leaves nothing left in price data/metadata/contiguity to detect it with), but "no signal left in this project's own data" did not mean "no signal available anywhere." Wikipedia's "Selected changes to the list of S&P 500 components" table (407 rows, 1976–2026, real dated company names — something `universe_membership`'s own ticker-only source never had) was cross-referenced against all 130, with a strict asymmetric rule: a name match promotes to `CLEAN` (real two-source corroboration); a name **mismatch is not sufficient evidence for `QUARANTINED` on its own** — a raw first pass found a third of mismatches were matcher artifacts (corporate renames the string comparison couldn't see: Allegheny Technologies→`ATI`, Cliffs Natural Resources→`CLF`, Owens-Illinois→`OI`) — mismatches were promoted to `QUARANTINED` only when corroborated by a second, independent signal (the original investigation's `LIKELY_RECYCLED` reversal-classifier flag; `CPWR` was the only ticker meeting that bar). Result: **104 promoted to `CLEAN`, 1 (`CPWR`) promoted to `QUARANTINED`, 25 genuinely still unresolved** — down from 130, and small enough to be a footnote rather than a blocker. Checked whether the 17 tickers with no Wikipedia coverage cluster in a particular era: **12 of 17 (71%) departed in 2008 specifically** — a real, structural gap in a community-maintained source's coverage of the crisis-year reconstitution wave, not random, and the same crisis era already implicated below in the drawdown-comparison finding. **Final counts: 220 CLEAN, 163 QUARANTINED, 25 NEEDS_REVIEW — confirmed on BOTH the local research DB and production, the latter via a direct SSH `SELECT` re-query on 2026-08-08, not the applying script's own printed summary.** This state was originally computed and written to the *local* database only (the commit that documented it, `c4450b7`, shipped no production code, so nothing about pushing it deployed this mutation) — CLAUDE.md asserted it as current fact for a full day before an audit caught that production still held the pre-refinement 116/130/162 state. Closed 2026-08-08: the same 105 `write_data_quality_status()` calls re-run directly against the server's database over SSH, independently re-confirmed. See "A research script whose output is a database mutation needs an explicit, separate production-apply step" above for the standing fix, and `research/needs_review_adjudication_2026-08-07/REPORT.md` for the adjudication detail.

**Phase 2 re-validation — same continuous-walk, baseline-only, caps 0.50/0.70 methodology, single variable changed again (candidate pool: 83→162 quarantined) — genuinely unchanged, not a rounding-to-zero effect.** `research/data_quality_remediation_2026-08-07/trades_cap0{5,7}_phase2.csv` are **byte-identical** to the Phase 1 `_after` trade files (`diff` confirms it) — block-pass rate stays 11/16 at 0.50 and 9/16 at 0.70, total P&L unchanged (£52,841.47 / £116,748.91). None of the 79 newly-quarantined tickers ever won a momentum-ranked capacity slot in either of these two specific historical cap runs, so removing them changed nothing *realized* here. **This does not make Phase 2 pointless** — those 79 tickers' distorting potential was latent, not zero: a different cap, a different date range, or a future re-run could have let one of them win a slot the way `TIE` did in the original £2.21M anomaly. Removing them closes that latent exposure for every future run, even though this specific validation pass can't show a delta for it.

**Re-baselined 2026-08-08 — the standard `get_research_stock_candidates()` validation moved from 9/16 to 7/16 after the Wikipedia refinement, for an understood, benign reason, not a regression.** The only pool change from the last confirmed-9/16 run was `CPWR`'s removal (promoted to `QUARANTINED`, corroborated). `CPWR`'s own 18 trades (all 2008-2011) sit in a block whose pass/fail didn't change at all — the two blocks that actually flipped (2013-2015) are years later, via the capacity-cascade mechanism now documented in "The 16-block pass rate has a measured resolution floor" above. **7/16 is the current reference point for this pool going forward; a future session seeing 7/16 rather than 9/16 should read this paragraph, not assume a regression occurred.**

**Ingestion-time screen (root-cause fix) plus a recurring backstop scan.** `verify_ticker_identity()` now runs automatically the first time a genuinely new ticker's history is stored — both in `data/ingestion.py:pull_incremental()`'s first-time-download branch (the real daily-automatic path) and `backfill_price_history()`'s no-prior-data branch — via `_screen_newly_backfilled_ticker()`, so this class of corruption gets caught at the moment it would first enter `price_data`, not discovered later by accident. `scan_unverified_tickers()` (modelled on `detect_price_cliffs()`'s real-backstop-scan pattern, not the email-only weekly summary) is folded into `_eod_job()` right after the existing price-cliff scan: finds any ticker with `price_data` rows but no `ticker_data_quality` row yet (the pre-remediation backlog, or anything that slipped in outside the screened paths), screens it, and content-deduplicates a `send_data_quality_scan_alert()` email on newly-quarantined/needs-review tickers — flag-only, same as every other alert in this system.

**Validation — before/after 16-block comparison, single variable changed (candidate pool), continuous-walk convention, no earnings dampener (out of scope):** at `max_total_deployed=0.70`, block-pass rate rose from a badly-failing **1/16 to 9/16** and total P&L fell from £2,211,986.14 to £116,748.91 (−94.7%) — the corruption's own headline anomaly, confirmed and corrected, landing almost exactly on this project's long-documented ~9/16 realistic-stop-fill baseline. At 0.50, 10/16 → 11/16, P&L £33,203.17 → £52,841.47 (rose, not fell — capacity freed by removing corrupted candidates at the lower cap was absorbed by otherwise-crowded-out genuine trades, the capacity-turnover effect this project's own standing requirement asks to be reported separately from the direct effect). Full detail, including that the after-arm's pool still contains all 209 unresolved `NEEDS_REVIEW` tickers (so 9/16 should be read as "corruption removed, adjudication still pending," not "fully clean"): `research/data_quality_remediation_2026-08-07/REPORT.md`.

**Database.** SQLite, `data/database.py`. All tables, verified directly against the current schema: `price_data`, `universe_membership`, `invest_orders` (every order attempt, BUY and SELL, including `stop_order_id`/`stop_order_status` for the broker-side stop), `pnl` (reconstructed realised P&L, FIFO-paired from `invest_orders`), `pending_orders` (decide-then-submit queue, bounded retention), `instrument_resolutions`, `position_reconciliations`, `account_value_history`, `system_metadata`, `schema_migrations`, `ticker_data_quality` (identity-verification status per ticker — see "Recycled-ticker-symbol corruption" above), `ticker_sector` (per-ticker sector classification cache), `admission_ranking_shadow` (the relative-rank promotion's forward shadow comparison — see "Forward shadow comparison" above), `benchmark_comparison` (the daily account-vs-buy-and-hold pair — see "Benchmark-relative performance tracker" above). **There is no persisted backtest-run table** — `run_full_validation()` computes a full result in memory on every call; nothing about a backtest run is written to the database.

**Key `config.yaml` live-trading keys** (`trading212_invest:`, extensively commented in the file itself — treat the file's own comments as more current than any narrative description, including this one): `enabled`, `live` (hard-blocked `true` in code), `max_total_deployed`, `per_trade_pct`, `absolute_max_position_gbp`, `var_confidence`/`var_lookback_days`/`max_risk_pct`/`min_position_gbp`, `stock_max_holding_days`, `stock_trading_enabled`/`stock_submission_enabled`, `price_sanity_threshold_pct`, `pending_orders_retention_days`, `reentry_cooldown_days`, `min_momentum_pct`, `broker_side_stop_enabled`.

## Running tests

```powershell
pytest                        # run the full suite from the project root
pytest tests/test_execution.py -k "crisis_exit"   # run a targeted subset
```

The suite lives in `tests/`, is fully self-contained (no test touches the real project database, calls yfinance, or calls the live Trading212 API), and runs identically on Windows (local dev) and Ubuntu (server). DB-touching tests use the `db_engine` fixture (`tests/conftest.py`) — a fresh in-memory SQLite database per test, with every module-level `get_engine` binding the code under test actually resolves at runtime patched to it. External services (`Trading212Client`, yfinance) are mocked with `unittest.mock`/`pytest-mock`, never called for real.

**The GitHub Actions workflow (`.github/workflows/test-and-deploy.yml`) runs this suite automatically on every push to `main`, on GitHub's own infrastructure** — see "GitHub webhook deploy" below for why this replaced a local pre-push hook as the actual deploy gate. There is no local test-gating step any more; running the suite locally before pushing is still good practice but is no longer enforced by anything client-side.

**Layer 1 (`tests/test_decision_logic_placement.py`) and Layer 4 (`tests/test_one_sided_decision_rules.py`) are structural safeguards, part of the standard suite, not optional checks** — see "Current architecture" above. Any change to entry/exit/sizing/priority logic in `brokers/trading212.py` or `analysis/backtest.py` should be checked against these two directly if either fails.

## Deployment (Ubuntu 22.04 / cloud VM)

The app runs as three systemd services behind a Caddy HTTPS reverse proxy: `investment-research` (the Streamlit web UI), `investment-research-scheduler` (the standalone daemon that actually fires scheduled jobs and places orders, `scheduler_daemon.py`), and `investment-research-webhook` (the GitHub webhook deploy listener, `webhook_listener.py` — see below). **All three must be restarted on every deploy** — each keeps its own in-memory copy of the code. `deploy.sh`'s `deploy_steps()` function always restarts all three.

**Ongoing deploys — GitHub Actions calling a signed webhook listener (2026-08-05), replacing both the earlier push-triggered local SSH mechanism AND GitHub's own native webhook delivery.** `git push origin main` no longer deploys anything by itself. `.github/workflows/test-and-deploy.yml` runs the full test suite on GitHub's own infrastructure, and only on genuine success (a real push to `main`, tests passing) does it compute an `X-Hub-Signature-256` HMAC-SHA256 signature and POST to `https://<domain>/webhook/deploy` itself — mimicking GitHub's own native webhook payload/signature format exactly. Caddy reverse-proxies that request to `investment-research-webhook` (loopback-only, port 8600 by default). `webhook_listener.py` verifies the signature against `GITHUB_WEBHOOK_SECRET` (constant-time comparison, `hmac.compare_digest`) before acting on anything — an unsigned or wrongly-signed request gets a 401 and **no deploy is triggered**; the listener contains no deploy logic of its own beyond that check, and (deliberately) no knowledge of test results either — a verified push to `refs/heads/main` spawns `bash deploy.sh` and returns immediately (a full deploy can take longer than GitHub's ~10s webhook timeout). See `tests/test_webhook_listener.py` for the signature-rejection/acceptance behavior verified directly, not just asserted.

**GitHub's own native webhook delivery (Settings → Webhooks) was used during the staged rollout of the Actions workflow above, then deleted entirely once that workflow was proven working end to end** (see "GitHub Actions as the real test gate" below for the full incident history from that rollout). The native webhook fired on every push regardless of test outcome — exactly the gap Actions closes — so once Actions could be trusted to reliably reach the listener on its own, keeping the native webhook running in parallel would have meant the untested-deploy gap it introduced was never actually closed, just usually masked by Actions also firing. There is now exactly one thing that can ever call `/webhook/deploy` with a valid signature: this repository's own Actions workflow.

**Why a webhook, not the old pre-push-hook SSH mechanism.** The git pre-push hook used to run the test suite, then background-SSH into the VM and run its own copy of the git-pull/pip-install/systemd-restart command chain — a second, independent implementation of "deploy this app" alongside `deploy.sh`'s own copy. The two kept drifting apart in practice (see "A real incident..." below and history's "Deploy pipeline systemd parity audit" for two more, including a real, confirmed shell-precedence bug in `deploy.ps1` and a bug in the hook-installer's worktree handling). The webhook removes the duplication structurally rather than patching another instance of it: **`deploy.sh`'s `deploy_steps()` function is now the single, only place any deploy step lives**, and `deploy.sh` itself always runs it directly and unconditionally — the only thing that ever calls it is the webhook listener (locally on the VM), the GitHub Actions workflow's own signed call to that same listener (see below), or a human running it manually over SSH.

**There is no local/dev deploy mode any more.** An earlier design here had `deploy.sh` push from a developer's own machine and then SSH in to invoke itself remotely, prompting interactively on a blackout window first; a PowerShell twin, `deploy.ps1`, did the same for Windows. Both were removed 2026-08-05 once the webhook made them redundant for routine deploys (any `git push origin main` already triggers one) — see "Blackout-window handling" below for why the interactive prompt they offered wasn't pulling its weight either, once the webhook path already handled the same problem. **Manual/break-glass deploy** (only needed if the webhook path itself is down — listener crashed, GitHub delivery failing, etc.): SSH in and run `bash deploy.sh` directly on the VM — it always deploys immediately, running the same non-interactive blackout handling described below.

**GitHub Actions as the real test gate, and the removal of the local pre-push hook entirely (2026-08-05).** Before this, GitHub's own native webhook deployed on *every* push to `main` regardless of test status — the only thing ever stopping a failing-tests push from deploying was the local pre-push hook, a client-side safeguard bypassable via `git push --no-verify`, a machine without the hook installed, or any git client that skips it. `.github/workflows/test-and-deploy.yml` closes this gap for real: it runs the full suite on GitHub's own infrastructure, independent of whichever machine pushed, and only on genuine success computes the same `X-Hub-Signature-256` HMAC-SHA256 signature (over a minimal `{"ref": ..., "after": ...}` payload, `ref` set to the actual triggering `github.ref` rather than hardcoded — a deliberate hardening so the payload can never falsely claim "this was main" if the step's own `if: github.ref == 'refs/heads/main'` condition were ever accidentally weakened) and calls the listener directly, mimicking GitHub's own webhook format exactly. **`webhook_listener.py` needed zero changes for this** — it stays a thin "verify signature, then deploy" component; the test gate lives entirely upstream of it, in a completely separate system. Once this workflow was proven working (see below), the local pre-push hook and its generator (`setup-hooks.sh`) were removed entirely, not just emptied out — there is no local test-gating step left at all; `tests/test_deploy_hook_systemd_parity.py::test_no_pre_push_hook_infrastructure_exists` guards against either coming back. GitHub's own native webhook was *also* removed entirely at this point (see "GitHub's own native webhook delivery..." above) — it is not left running alongside Actions as a backup, since doing so would have kept the exact untested-deploy gap this section exists to close.

**A genuinely new failure mode the staged rollout introduced, and how it's made safe: two independent deploy triggers for the same push.** During the (now-closed) staged rollout, the native webhook and the Actions-triggered call could both be live for the same commit at once — `deploy.sh` has no other mutual exclusion, and two overlapping `deploy_steps()` runs racing `systemctl restart`/`daemon-reload` calls for the same units is not something to rely on being harmless. This scenario no longer arises now that the native webhook is gone — Actions is the only thing that ever calls `/webhook/deploy` with a valid signature — but the same overlap risk still exists between an Actions-triggered deploy and a manual break-glass SSH deploy landing at the same time, so the fix was kept permanently rather than treated as rollout-only scaffolding: `deploy.sh` takes a lock (`${APP_DIR}/.deploy.lock.d`, held for the whole script including any blackout wait) before doing anything — mkdir-based, not `flock` (confirmed directly: `flock` doesn't exist in Git Bash, where this project's own tests must also run per its "runs identically on Windows and Ubuntu" standard — an mkdir-succeeds-or-fails-atomically loop is portable to both). A second trigger waits for the first to finish rather than racing it; verified directly via `tests/test_deploy_server_side_blackout.py::test_concurrent_invocations_serialize_rather_than_race`, which makes a single deploy run measurably slow and confirms two concurrent invocations take roughly 2x that, not 1x. **That test alone did not catch the third bug below** — it only exercised a short, fast lock hold, never a hold anywhere near the blackout defer loop's own multi-hundred-second range, which is exactly the gap the real incident fell through; a regression test proving "the lock serializes" is not the same claim as "the lock's own stale-timeout is long enough," and this project now has a separate, explicit test for the second claim (see bug 3 below). The staged rollout itself produced direct, real evidence the lock works even under sustained, multi-window contention: a real push landed inside back-to-back blackout windows (EOD then the immediately-following hourly window) and held the lock uninterrupted for over 18 minutes — well past the old, buggy 600s threshold — with zero premature steals once bug 3's fix was live.

**Three real bugs the Actions rollout's own staged verification caught — not found any other way.** All three are worth recording precisely, since the whole reason this rollout was staged (prove it working before removing the old path) is exactly so a real problem gets caught while both paths are still live, not after the only path is silently broken:
1. **Cloudflare's Bot Fight Mode blocked the Actions runner's IP, returning a "Just a moment..." JS-challenge page as an HTTP 403** — `platform.example.com` sits behind Cloudflare (required: `ufw` on the VM only allows 80/443 from Cloudflare's published ranges, so bypassing Cloudflare for this one path was never an option). The native webhook worked reliably throughout because GitHub's own webhook-delivery IPs apparently carry a better reputation than GitHub Actions' shared, heavily-trafficked runner IPs — plausibly also the explanation for occasional transient 403s seen from other callers over the course of this rollout. Cloudflare's Custom Rules "Skip" action can bypass *Super* Bot Fight Mode for a specific path, but **not** the base "Bot fight mode" product (a separate, non-path-scopable toggle on the Free tier) — confirmed directly by checking the rule editor's own component list, not assumed. Fixed by disabling Bot Fight Mode site-wide (`Security → Settings → Bot fight mode`), accepted as reasonable given this site's actual risk profile: the real security boundary for `/webhook/deploy` was always the HMAC signature check (a bot without a valid signature already gets `401`'d regardless), and the dashboard's real access control is the app's own per-user login (see "Authentication" above), not Cloudflare's challenge page. The alternative (allow-listing GitHub Actions' published IP ranges in Cloudflare's IP Access Rules) was considered and rejected — those ranges are large and change often enough that keeping the list current would be an ongoing maintenance burden disproportionate to what it protects.
2. **`AppTest.from_file("main.py")` failed in CI with `FileNotFoundError: .../tests/main.py`** (in `tests/test_auth_ui.py` and 5 call sites in `tests/test_main_ui.py`), despite passing locally every time. Root cause, confirmed not assumed: `AppTest.from_file()` resolves a relative path by walking the Python call stack to find its caller's file (per Streamlit's own docstring) — fragile to anything that shifts stack depth between the test file and the `from_file()` call. GitHub Actions installed a different `anyio` version (`4.14.2`) than was locally installed (`4.13.0`), both satisfying `requirements.txt`'s unpinned `>=` constraint — enough of a difference to shift the stack walk and break the resolution. This is exactly the class of environment drift an unpinned `requirements.txt` allows to go unnoticed indefinitely until a genuinely independent environment (this new CI gate, running fresh dependency installs on infrastructure this project doesn't control) actually exercises it — the local dev environment's accumulated package versions had been masking it. Fixed generally, not as a CI-specific workaround: both files now pass an absolute path (`Path(__file__).resolve().parent.parent / "main.py"`) instead of the bare string `"main.py"`, sidestepping the stack-walking mechanism entirely — this is more robust regardless of pytest/plugin versions, OS, or where pytest is invoked from, not merely a fix for the one environment that happened to expose it.
3. **The deploy lock's own stale-break timeout (`DEPLOY_LOCK_MAX_WAIT_SECONDS`) defaulted to 600s — shorter than the blackout defer loop's own 1800s cap.** Found live in production, not in a test: once both the native webhook and the Actions-triggered call were genuinely live for the same push (bug 1 and bug 2 above were finally both fixed), a real push landed inside the EOD blackout window and both triggers queued behind the lock as designed — but a *second* process waiting to acquire the lock hit its own 600s patience limit before the *first* process's legitimate, still-in-progress 1800s blackout wait had finished, wrongly concluded the lock was stale (as if from a crashed prior run), forcibly broke it, and started running concurrently with the still-very-much-alive first process. Compounded by every subsequent trigger (native webhook retries, Actions retries, manual verification attempts) doing the same thing to whichever process currently held the lock — five separate `deploy.sh` processes were observed running at once. Manually killed the redundant processes and cleaned the stale lock directory as immediate remediation, then fixed the actual bug: `DEPLOY_LOCK_MAX_WAIT_SECONDS` raised to 3600s, comfortably longer than the blackout cap it must always outlast. Two regression tests added: a fast static check (`tests/test_deploy_server_side_blackout.py::test_lock_max_wait_default_exceeds_blackout_max_wait_default`) that the two defaults stay correctly ordered, and a dynamic proof (`test_lock_waiter_does_not_steal_a_lock_still_legitimately_held`) that a lock waiter genuinely waits rather than stealing a lock still legitimately held through a real (scaled-down) blackout wait — deliberately a *different* scenario from `test_concurrent_invocations_serialize_rather_than_race` (bug-adjacent paragraph above), which used a lock hold far too short to have ever exposed this specific timing relationship.

**Deploy blackout windows** (weekdays unless noted) — derived directly from the real scheduler registrations and guarded by a drift-detection test (`tests/test_deploy_blackout_windows.py`, run on every push) that starts the real scheduler and cross-checks these bounds against it, so they can't silently go stale the way an earlier, hand-maintained version once did:
- **14:15–14:45 UTC** — `stock_order_submission` (14:30 UTC) window.
- **21:00–22:00 UTC** — `membership_refresh` (21:15) and `scheduled_eod` (21:30, the job that places T212 orders) window.
- **07:45–09:15 UTC, Sundays only** — `weekly_summary` (09:00 Europe/London, which is 07:00–08:00 UTC depending on BST/GMT) window.
- **Minutes :55 through :10 of every hour, every day** — `market_hours_check` (:00) and `account_state_refresh` (:05) both make real T212 API calls.

**Blackout-window handling — resolved explicitly, not left open, and the same for every deploy path.** `deploy.sh` never has a human guaranteed to be present to answer a y/N prompt (the webhook listener certainly doesn't, and even a manual break-glass SSH run shouldn't require standing at a terminal for up to 30 minutes), so `deploy.sh` (when NOT invoked with `DEPLOY_SKIP_BLACKOUT_CHECK=1`) instead **defers**: it polls the blackout-window check every `DEPLOY_BLACKOUT_POLL_INTERVAL_SECONDS` (default 60s) and, if still inside a window, waits and rechecks — up to a `DEPLOY_MAX_BLACKOUT_WAIT_SECONDS` cap (default 1800s / 30 minutes), after which it proceeds with the deploy regardless, logging that the cap was hit. This guarantees a real push is never silently lost or queued forever just because it happened to land inside a blackout window, while still giving a window that's about to end (e.g. a push landing 3 minutes into the 60-minute EOD window) a real chance to clear first. `DEPLOY_SKIP_BLACKOUT_CHECK=1` remains available as a deliberate manual override for a genuinely urgent break-glass deploy, but nothing sets it automatically — an earlier design had an interactive local/dev mode that prompted first and set this automatically once confirmed; both were removed 2026-08-05 as redundant once the webhook path already handled the same problem for the deploys that actually matter (routine ones, triggered by a push). Verified directly, not just described: `tests/test_deploy_server_side_blackout.py` drives the actual script end-to-end (via `DEPLOY_TEST_NOW_HM`/`_MIN`/`_DOW` clock overrides and a short poll/cap for test speed) and confirms (a) a deploy outside any window runs immediately, (b) a deploy inside a window that never clears still completes once the cap is hit, with real elapsed wall-clock time proving the wait actually happened, (c) the defer loop genuinely rechecks more than once rather than sleeping once, and (d) `DEPLOY_SKIP_BLACKOUT_CHECK=1` bypasses the wait entirely when a human deliberately sets it.

**A real incident hit during the webhook migration's own staged rollout (2026-08-05), and the fix.** The first genuine GitHub-triggered deploy through the new listener was silently truncated: `deploy_steps()` restarts `investment-research-webhook` as its own last restart step, and `webhook_listener.py`'s `trigger_deploy()` spawns `bash deploy.sh` as a direct child process of that same systemd unit. Under systemd's default `KillMode=control-group`, the unit restarting itself kills its entire cgroup — including the still-running `deploy.sh` process that issued the restart — so the deploy never reached its `systemctl status` output or, critically, `verify_deploy.sh`, even though the underlying git pull and service restarts had genuinely succeeded. Caught directly from `journalctl` (the log stopped mid-`"Restarting services..."` with no `"Server-side deploy complete"` line), not inferred. Fixed with `KillMode=process` on `investment-research-webhook.service` (targets only the unit's own tracked PID, leaving the child `deploy.sh` — now reparented — free to finish; accepted trade-off: a crashed listener's stray children are no longer auto-reaped by systemd, acceptable since this listener only ever spawns the one `deploy.sh` child). Confirmed working via a second real GitHub-triggered deploy immediately after: systemd logged `"Found left-over process ... in control group while starting unit. Ignoring."` (direct proof the child survived) and the run completed end-to-end, all 7 `verify_deploy.sh` checks passing. `tests/test_deploy_hook_systemd_parity.py::test_webhook_service_uses_kill_mode_process` guards this going forward.

**Managing server credentials.** API keys live in a systemd drop-in override, never in the tracked `investment-research.service` file: `/etc/systemd/system/investment-research.service.d/secrets.conf` (and identical drop-ins, or symlinks to it, for the `-scheduler` and `-webhook` services — `deploy_steps()` creates the `-webhook` symlink automatically, same as the pre-existing `-scheduler` one). After editing: `systemctl daemon-reload && systemctl restart investment-research investment-research-scheduler investment-research-webhook`. `GITHUB_WEBHOOK_SECRET` (a genuinely random secret, e.g. `openssl rand -hex 32`) lives in this same drop-in — `webhook_listener.py` refuses to start without it, so an unauthenticated deploy trigger reachable from the internet is never a possible fallback state.

**Configuring the GitHub side of the webhook (one-time, manual, account-gated — cannot be done from this repo or by an agent).** In the GitHub repo → **Settings → Webhooks → Add webhook**:
- **Payload URL**: `https://<domain>/webhook/deploy`
- **Content type**: `application/json`
- **Secret**: the same value placed in `GITHUB_WEBHOOK_SECRET` on the server
- **Which events**: "Just the `push` event" (not "Send me everything") — the listener ignores every other event type anyway, but scoping it on GitHub's side too means GitHub doesn't bother sending payloads this system will just discard
- Leave "Active" checked. Save, then use GitHub's own "Recent Deliveries" tab to confirm a `ping` delivery got a `200 pong` response (`webhook_listener.py` handles `ping` explicitly, verifying the signature but triggering no deploy) before trusting a real push to it.

**A real incident that exposed why the `daemon-reload` step above matters (2026-08-04, historical — the mechanism it happened in no longer exists).** Adding `ADMIN_PASSWORD_HASH` to `secrets.conf` ahead of the per-user-accounts rollout, then pushing to `main`, hit a real gap in the *old* pre-push-hook SSH deploy mechanism: it restarted services with a bare `systemctl restart`, no `daemon-reload` first, so the restart came up with the stale cached environment. That specific mechanism (and the parity-patching cycle it required — see history's "Deploy pipeline systemd parity audit") is gone entirely as of the webhook migration, not merely fixed again; see "Why a webhook, not the old pre-push-hook SSH mechanism" above. Every deploy path ends by running `verify_deploy.sh` — a shared, direct post-deploy check (service state, installed unit-file content, HEAD matches `origin/main`) that doesn't just trust the command chain's own exit code.

**Server access**: `ssh -i ssh-keys/deploy_key root@203.0.113.10`, working directory `/root/investment-research`. Secrets for a one-off script: `set -a; source <(grep '^Environment=' /etc/systemd/system/investment-research.service.d/secrets.conf | sed 's/^Environment=//'); set +a`.

**Useful server commands**:
```bash
journalctl -u investment-research -f              # web UI live logs
journalctl -u investment-research-scheduler -f    # scheduler daemon live logs (the one placing orders)
journalctl -u investment-research-webhook -f      # webhook listener + triggered deploy output
systemctl status investment-research investment-research-scheduler investment-research-webhook
systemctl restart investment-research-scheduler   # after a config-only change, e.g.
```

**Before any local research/backtest sweep**, verify the local environment is current — don't assume it (a fresh, coverage-checked copy of the production database; current code; the DB file confirmed gitignored). See "Experimental/research work runs locally, not on the server" below for the full standing practice, including the multi-session git-worktree requirement.

`config.yaml` is git-tracked (not gitignored) — secret fields (`notifications.email.smtp_password`) are committed as blank placeholders; real values come only from the server's systemd secrets drop-in or the `T212_API_KEY`/`T212_API_SECRET`/`SMTP_PASSWORD`/`ADMIN_PASSWORD_HASH` env vars. `ADMIN_PASSWORD_HASH` (see "Authentication" above) is a bcrypt hash, not a secret in the traditional sense — it is only ever read once, by `_seed_default_admin()`, to bootstrap the first `admin` account on an empty `users` table; it is safe to leave in the secrets drop-in indefinitely (later runs are a no-op once the table is non-empty), but it plays no role in day-to-day authentication after that first run.

