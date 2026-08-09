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

**If more than one local Claude Code session is running against this repository at the same time, each must use its own `git worktree` (`git worktree add <path> <branch>`), not share a single working directory.** Two genuinely separate sessions (UI work, research experiments) have already had their git staging briefly interleave twice — one session's uncommitted edits or in-flight commit sequence became visible to, and in one case momentarily reordered by, the other, both times on this same shared checkout. Both incidents were caught and separated cleanly before anything was lost (via careful `git status`/`git diff --cached`/`git reflog` checks before and after each commit), but relying on catching this after the fact is avoidable entirely with separate worktrees, which give each session its own working directory and index while still sharing the one underlying repository and object store.

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
shape of problem. Full report: `investment-app-blocking-audit/UI_BLOCKING_AUDIT_2026-08-09.md`.
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
| `scheduled_eod` | daily 21:30 | `_is_scheduled_trading_day()` (day-level NYSE calendar) | The only job that pulls prices; computes signals, queues BUY decisions into `pending_orders`, runs the exit check, purges old resolved `pending_orders` rows, runs `detect_price_cliffs()` and `scan_unverified_tickers()` (recurring backstop for recycled-ticker-symbol corruption — see "Recycled-ticker-symbol corruption" above) |
| `membership_refresh` | daily 21:15 | day-level NYSE calendar | Re-fetches point-in-time S&P 500 membership |
| `stock_order_submission` | daily 14:30 | `_is_nyse_open_now()` (live session, stricter than day-level) | Submits queued BUY decisions, with the price-sanity/crisis/capacity re-checks described above |
| `market_hours_check` | hourly, minute 0 | `_is_nyse_extended_hours_open()` | `poll_placed_orders()` (also places fresh broker-side stops on new fills), `maybe_exit_positions()`, `check_stop_health()` — "Open Trade Monitor" |
| `account_state_refresh` | hourly, minute 5 (offset from the job above specifically to avoid a T212 rate-limit collision — a real incident, see history) | unconditional | `reconcile_positions_with_broker()`, refreshes the T212 account/positions snapshot |
| `weekly_summary` | Sunday 09:00 **Europe/London** (DST-correct, not a fixed UTC offset) | day-of-week only | Sends the weekly status email |

No `monday_premarket` job exists any more (removed 2026-08-03 as confirmed-redundant with the decide-then-submit architecture — see history). Every job with an internal gate fires unconditionally on its trigger and decides for itself whether there's real work to do, rather than approximating the condition with a hand-maintained cron field — this is why none of these jobs carry a `day_of_week="mon-fri"` filter even though most only ever do anything on a weekday.

**Position reconciliation and alerting.** `reconcile_positions_with_broker()` compares what this system's own order history implies is open against T212's real, live positions, flagging (never auto-remediating) three genuine-discrepancy shapes needing manual review — `BROKER_CLOSED_NO_SELL`, `UNRECOGNIZED_POSITION`, `REPEATED_SELL_REJECTION` — plus one non-discrepancy, `STOP_LOSS` (see "Both stop-loss layers kept permanently" above), recorded already-`ACKNOWLEDGED` for the audit trail and never alert-worthy, since it represents the broker-side stop protection working exactly as designed. Every alert in this system (`output/briefing.py`) uses **content-based deduplication**, not a time-based cooldown — compares the current set of affected items against what was last actually emailed, sending whenever that set genuinely changes, never spamming an unchanged, persistent problem repeatedly. (An earlier, simpler time-cooldown design was found to silently suppress a second, different failure landing inside an earlier failure's cooldown window — see history's "Instrument-resolution failure alert" section for the original finding this pattern now follows throughout.)

**Price-cliff monitoring and the Status page's Data Coverage card are scoped to live-tradeable + currently-held tickers, not the full ingestion universe (2026-08-07).** `data/ingestion.py:detect_price_cliffs()` (added 2026-08-06, see history's "Split-adjustment corruption") scans the *entire* `price_data` table for consecutive-trading-day close-ratio breaks outside `[0.5, 2.0]` — including the ~700 historical/delisted tickers ingestion was deliberately broadened to cover the same day (see "Universe coverage remediation" above), most of which can never be traded or held again. Confirmed directly the day this was found: of 4,401 raw hits, only **14** touched a ticker in `get_live_stock_candidates()` or `get_held_research_tickers()`, and every one of those 14 corresponds to a real, dated market event, not corruption — e.g. AIG/HIG/STT (the Sept 2008–Jan 2009 banking crisis), APA/OXY/TRGP all breaking on the identical date 2020-03-09 (the COVID/Saudi-Russia oil price war), MO's real 2008-03-28 Philip Morris International spinoff, KDP's real 2018-07-10 Keurig/Dr Pepper Snapple merger, and PCG's real 2019-01-14 wildfire bankruptcy filing. The Status page's Data Coverage card showed the same pattern for the identical reason: `_price_coverage()` in `main.py` used to report freshness against the full ~1,208-ticker watchlist, so hundreds of permanently-delisted companies (AABA, AAMRQ, AET, ANDV, ABMD, etc. — genuinely gone, never fresh again) showed up as "stale"/"missing" even though none of them affect a live decision. Both were narrowed to the same live-relevant scope (`get_live_stock_candidates() | get_held_research_tickers()`, plus `reference_tickers` for the coverage card) without touching ingestion scope, `price_data`, or anything upstream — the full-table backstop scan and the broadened historical coverage that made it noisy are both still exactly as they were; only what gets *surfaced* to a human (the daily cliff-alert email, `output/briefing.py:send_price_cliff_alert()`, and the Status page card) changed. `data/scheduler.py`'s EOD job now records both `price_cliff_hits` (the alert-worthy, live-relevant count) and `price_cliff_hits_total` (the raw full-table count) in its completion record, so the discarded noise stays visible for anyone who goes looking without being emailed about it daily.

**`close` vs `adj_close` smoothing — a standing fact about this project's data, not specific to any one investigation (2026-08-08).** `yfinance` retroactively rebases `close` (even under `auto_adjust=False`) for **ordinary stock splits only** — confirmed both by the 2026-08-06 audit (296 real splits checked, all showing a smoothly-adjusted series) and directly here: a **fresh pull from yfinance today reproduces the identical cliff** for a merger (`KDP`), a spinoff (`MO`), and a special dividend (`WY`) discovered during the 14 live/held price-cliff hits' classification (`research/split_cliff_remediation_2026-08-08/`). **Mergers, spinoffs, and special dividends are never retroactively smoothed in `close` — only in `adj_close`, which `analysis/signals.py` stores but reads from nothing.** This means `_find_retroactive_restatement()`'s re-fetch-and-replace repair (built and validated for the 2026-08-06 incident, which found zero actual cases to repair) **cannot fix this class of cliff** — a re-fetch reproduces the exact same discontinuity, not a corrected one. Discovered because the obvious next step ("just re-fetch it") was checked empirically before being trusted, not assumed to work because it worked for the split case. **Deliberately not repaired by any other mechanism either** (a computed price-continuity splice was considered and explicitly rejected — see `research/split_cliff_remediation_2026-08-08/REPORT.md`'s "Decision" section: it would fabricate prices no exchange ever printed and store them as observed values on the live path, the corrected series already exists unused in `adj_close`, and every affected event (2007–2018) is confirmed, not assumed, to fall outside every momentum window this system has ever actually evaluated a trade against). The 14 originally-found live/held cliffs are recorded as classified exceptions (`data/ingestion.py:_KNOWN_PRICE_CLIFF_EXCEPTIONS`, filtered from alerting via `filter_known_price_cliff_exceptions()` — called by the EOD job strictly after live-scoping, `detect_price_cliffs()` itself and its live scoping are unchanged) rather than repaired: 9 genuine market moves (including `GL`'s 2024-04-11 Fuzzy Panda short-report crash, confirmed directly and contradicting an initial assumption that it was a corporate action), 3 confirmed-but-unrepairable corporate actions (`KDP`/`MO`/`WY`), 1 already-correctly-reflected corporate action (`EXPE`), and 1 genuinely unresolved case (`JCI` — a real split event exists in `yfinance`'s data but its direction contradicts the observed move, and the underlying cause was not identified). **Flagged as a future research item, not implemented**: whether the momentum signal should read `adj_close` instead of `close` — would address this artefact class and the existing dividend-exclusion bias in one variable change, but needs both pools (per "Dual-provenance validation" above) and a criterion that doesn't hinge on a small block-pass delta (per "The 16-block pass rate has a measured resolution floor" above) to evaluate correctly.

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

**Database.** SQLite, `data/database.py`. All tables, verified directly against the current schema: `price_data`, `universe_membership`, `invest_orders` (every order attempt, BUY and SELL, including `stop_order_id`/`stop_order_status` for the broker-side stop), `pnl` (reconstructed realised P&L, FIFO-paired from `invest_orders`), `pending_orders` (decide-then-submit queue, bounded retention), `instrument_resolutions`, `position_reconciliations`, `account_value_history`, `system_metadata`, `schema_migrations`, `ticker_data_quality` (identity-verification status per ticker — see "Recycled-ticker-symbol corruption" above). **There is no persisted backtest-run table** — `run_full_validation()` computes a full result in memory on every call; nothing about a backtest run is written to the database.

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

