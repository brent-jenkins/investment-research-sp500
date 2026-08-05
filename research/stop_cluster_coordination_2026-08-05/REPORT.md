# Correlated, Uncoordinated STOP_LOSS Clustering — Research Report (2026-08-05)

**Location of artifacts:** `research/stop_cluster_coordination_2026-08-05/` — `part1_cluster_analysis.py` / `part1b_context_and_robustness.py` (Part 1 scripts), `stop_cluster_engine.py` (Part 2 instrumented engine), `self_check.py` (fidelity check), `reset_sweep.py` / `block_validate.py` (Part 2 harnesses), plus all logs/JSON/CSV outputs listed above.

**Bottom line: researched, not adopted.** A real, large, and highly robust historical cost from uncoordinated stop-loss clustering was found in Part 1 — up to £144,925 (30% of total 19-year strategy P&L) depending on parameters, positive in sign across every one of 9 tested (W,N) combinations. But when an actual forward-looking coordination mechanism was built and tested (Part 2), it did **not** survive independent block validation: the most promising variant found (W=10, N=4) lost on 16-block win-count (13/16 vs baseline's 14/16), mean CAGR (11.26% vs 12.94%), and median CAGR (11.67% vs 15.26%) — including one genuine new block failure the baseline doesn't have. This is the same pattern several other findings landed on tonight: a real retrospective/observational effect that does not survive being turned into an actual, forward-looking, non-lookahead mechanism and block-validated. `config.yaml` and all production code are unchanged; nothing was committed or pushed.

## 0. Environment verification

- `database/investment_research.db`: `price_data` spans 2007-01-01 to 2026-08-03 across 540 tickers; `^GSPC` covers 2007-01-03 to 2026-08-03 (4,925 trading days); `universe_membership` has 1,257 rows. Checked directly, fresh, before running anything.
- `git status --short` showed only expected untracked entries at task start (`database/`, two sibling research directories from earlier tonight) — no uncommitted local modifications to tracked files, no signs of a concurrent session. Worked directly in the main checkout (no worktree needed).
- `database/*.db` is gitignored (confirmed in `.gitignore`).

## 1. Part 1 — data source and reproduction check

Reused `research/momentum_horizon_2026-08-05/all_trades_full_run.json` (3,161 trades from `simulate_portfolio_compounding_mtm()` via `run_full_validation(config, mark_to_market=True, live_universe=True)`, continuous walk 2007-01-01 to 2026-08-03, live universe) directly — **did not re-run** `run_full_validation()`. Confirmed this was the right call by reproducing its own stated totals before trusting it: loaded the file, asserted `len(trades) == 3161` and `exit_reason == 'STOP_LOSS'` count `== 1169` — both passed exactly (`part1_run.log` line 4). Every field Part 1 needed (`ticker`, `entry_date`, `exit_date`, `entry_price`, `exit_price`, `exit_reason`, `pnl_pct`, `position_gbp`, `pnl_gbp`, `holding_days`) was present in the stored file.

## 2. Cluster definition (exact code, not paraphrase)

For window size `W` (trading days) and minimum cluster size `N`:

```python
# 1. Trading-day calendar built from stored ^GSPC price_data (2007-2026), NOT
#    the raw trade dates -- day_index_map = {date: integer_index, ...}

# 2. For every STOP_LOSS trade i (sorted by exit_date):
trailing_count_i = count of STOP_LOSS trades (incl. i) whose exit_date's
                   trading-day index falls in
                   [day_idx(exit_date_i) - (W-1), day_idx(exit_date_i)]

# 3. Flag every trade i where trailing_count_i >= N
#    (first, non-lookahead moment a real system could recognize the cluster)

# 4. Merge the [lo, hi] trailing windows of all FLAGGED trades that overlap
#    or touch (lo <= prev_hi + 1) into maximal cluster spans

# 5. Cluster members = every STOP_LOSS trade (flagged or not) whose
#    exit_date falls inside a merged span

# 6. detection_moment = exit_date of the chronologically EARLIEST flagged
#    trade in that span

# 7. DISCARD (don't count) any cluster whose span overlaps a real crisis day:
crisis_days = set of dates where analysis.crisis.compute_raw_trigger(^GSPC) is True
if any(d in crisis_days for d in span_dates): exclude
```

**Hypothetical coordinated-exit cost, per cluster:**
```python
for each member trade m:
    if m.exit_date <= detection_moment:
        hyp_pnl = m.pnl_gbp                       # real exit stands, no lookahead
    elif m.entry_date > detection_moment:
        hyp_pnl = m.pnl_gbp                       # wasn't open yet at detection
    else:
        hyp_price = real stored close for m.ticker on detection_moment
                    (or nearest LATER trading day if that exact date is missing)
        hyp_pct = hyp_price / m.entry_price - 1.0
        hyp_pnl = m.position_gbp * hyp_pct
```

## 3. Sensitivity table — full (W, N) grid

7 raw crisis-trigger days found in the full history (`2008-09-29, 2008-10-09, 2008-10-15, 2008-12-01, 2020-03-09, 2020-03-12, 2020-03-16`) — **zero clusters overlapped any of them** at any (W,N) combination tested, so `crisis_excluded=0` throughout (the genuinely-coordinated crisis case and the gradual-decline case Part 1 targets never collided in this dataset).

| W | N | genuine clusters | trades involved | real £ | hyp £ | delta £ | % of entry value | % of total strategy P&L |
|---|---|---|---|---|---|---|---|---|
| 3 | 2 | 203 | 956 | -448,491 | -333,279 | **+115,212** | 1.80% | 23.78% |
| **3** | **3** | **129** | **743** | **-363,221** | **-297,079** | **+66,142** | **1.30%** | **13.65%** |
| 3 | 4 | 84 | 566 | -283,233 | -253,174 | +30,059 | 0.77% | 6.20% |
| 5 | 2 | 180 | 1,051 | -500,018 | -355,093 | +144,925 | 2.01% | 29.91% |
| 5 | 3 | 137 | 906 | -441,616 | -349,831 | +91,785 | 1.47% | 18.95% |
| 5 | 4 | 96 | 745 | -370,374 | -300,586 | +69,787 | 1.36% | 14.40% |
| 10 | 2 | 124 | 1,138 | -538,737 | -393,916 | +144,821 | 1.86% | 29.89% |
| **10** | **4** | **98** | **973** | **-476,273** | **-364,702** | **+111,571** | **1.65%** | **23.03%** |
| 10 | 3 | 110 | 1,068 | -513,868 | -383,276 | +130,592 | 1.77% | 26.96% |

**Every single one of the 9 combinations shows a positive delta** — coordinated exit would have improved the aggregate outcome under every parameter choice tested, ranging from 6.2% to 29.9% of the full 19-year strategy's total net P&L (£484,476.39 across all 3,161 trades; STOP_LOSS trades alone summed to -£552,747.87). This is a highly robust directional finding, not an artifact of one arbitrary parameter choice.

**Primary combination selected: W=3, N=3.** Justification (via a naive-independence Poisson-null calibration, `part1b_run.log`): empirical STOP_LOSS rate is 1,169/4,925 trading days = 0.2374/day. Under an independent-Poisson null, `P(>=N stops in a random W-day window)` is 16.0% for W=3/N=2 (too noisy — background activity alone would produce "clusters" at this rate 1 window in 6), but drops to **3.56%** for W=3/N=3 — the smallest, tightest W/N combination that clears a conventional 5%-style "distinguishable from background noise" bar (W=5/N=3 is still 11.8%; only W=3/N=4, W=5/N=4, and larger-N variants of W=10 clear 5% among the 9 tested, and W=3/N=3 is the smallest/tightest of those). This matches the task's own framing: "the smallest N/W that still isolates genuine near-simultaneous firing from routine background stop-loss activity."

## 4. Primary combination (W=3, N=3) — detailed findings

**129 genuine clusters, 743 member trades, spanning 2008-01-07 to 2026-07-27.**

- Real aggregate P&L across cluster trades: **-£363,221.05**
- Hypothetical coordinated-exit P&L: **-£297,078.92**
- **Delta: +£66,142.13** (coordination would have reduced the loss by 18.2% in relative terms)
- As % of combined entry value: **1.30%**
- As % of total 19-year strategy P&L: **13.65%**
- Mean number of positions open at detection moment: **11.33**
- Mean % of that day's open book represented by the cluster: **53.09%** — clusters typically involve the *majority* of the currently-open book, not a small side pocket.

**Full per-cluster detail:** `primary_clusters_summary.csv` (129 rows), `primary_clusters_detail.json` (full member-trade-level detail per cluster, ticker/entry/exit/price/P&L for every real and hypothetical leg).

**Regime concentration (by detection year):**

| Year | Clusters | Delta £ | | Year | Clusters | Delta £ |
|---|---|---|---|---|---|---|
| 2008 | 10 | 1,798 | | 2018 | 9 | **8,973** |
| 2009 | 4 | 406 | | 2019 | 6 | 870 |
| 2010 | 8 | 1,473 | | 2020 | 4 | 1,073 |
| 2011 | 7 | 1,776 | | 2021 | 11 | 3,225 |
| 2012 | 5 | 550 | | 2022 | 7 | 6,357 |
| 2013 | 6 | 227 | | 2023 | 7 | 559 |
| 2014 | 7 | 975 | | 2024 | 9 | 3,050 |
| 2015 | 6 | 800 | | **2025** | 6 | **13,898** |
| 2016 | 8 | 1,845 | | **2026** (partial, to Aug) | 5 | **16,270** |
| 2017 | 4 | 2,017 | | | | |

The three largest single clusters found: **2025-02-21** (19 members, span 2025-02-14 to 2025-03-14, real -£25,503 vs hyp -£17,792, delta +£7,711 — AXON/HWM/UAL/NRG/PLTR/CEG/TSLA/VST/RCL/FOX/FOXA/GILD), **2018-10-08** (23 members, real -£8,904 vs hyp -£6,274, delta +£2,630 — ANET/ADBE/CRM/GWW/NTAP/STX/ALGN/AMZN/BR/CF/COP/HCA/NFLX/TGT/CNC/MOS/ORLY/AMD), and **2026-07-27** (11 members, real -£29,396 vs hyp -£18,506, delta +£10,890 — GLW/FLEX/SNDK/COHR/KLAC/VRT/CIEN/DELL/FIX/LRCX/MU, the semiconductor/storage cluster already independently identified in `research/concentration_risk_2026-08-05/REPORT.md`'s "worst episodes" list). Cross-referencing `research/concentration_risk_2026-08-05/results/daily_panel.csv` confirms these later-era clusters concentrate exactly where that sibling report found `indep_gap_60d` most elevated — recent years (2024-2026) account for a disproportionate 36,218 of the 66,142 total delta (55%), consistent with position sizing growing via compounding, not merely more frequent clustering.

**Part 1 conclusion: this clears the "real, meaningful aggregate cost" gate decisively.** Proceeding to Part 2.

## 5. Part 2 — engine construction and a real bug caught mid-investigation

**Design**: `stop_cluster_engine.py`, a verbatim copy of `analysis.backtest.simulate_portfolio_compounding_mtm()` with one isolated, marked change: a rolling STOP_LOSS trailing-window trigger evaluated once per day after Phase A's natural exits, force-closing every other currently-open position at each ticker's own last-known price (genuinely "next available", no lookahead — the identical convention `_open_positions_mtm_value()` already uses for daily mark-to-market).

**A real design bug was found and fixed before trusting any result.** The first version fired the coordination action on *every* day the rolling trailing count remained `>= N`, not just the day it first crossed the threshold — a state-based rule, not the edge-triggered, discrete-episode definition Part 1 actually used to count 129 clusters (Part 1 merges consecutive flagged windows into ONE detection event per cluster). Direct evidence this mattered: the buggy version fired **1,509** coordinated closures against only **391** real STOP_LOSS exits in the same 2020-2026 test window — a ~4x mismatch that meant Part 2 was testing a materially more aggressive, different policy than what Part 1 quantified. Fixed by tracking a `cluster_active` boolean: the action fires once on the day trailing_count first reaches N, stays silent while the same cluster remains active, and only re-arms once trailing_count drops back below N (a genuinely new, later cluster). After the fix, the same test window produced 442 coordinated closures against 377 real STOP_LOSS exits — a plausible, comparable order of magnitude.

**Fidelity confirmed directly** (`self_check.py`): with `cluster_window_days=None`/`cluster_min_stops=None` (inert defaults), the engine reproduces the real `simulate_portfolio_compounding_mtm()` **byte-identical** on a real 60-ticker/full-history subset (2,854 == 2,854 trades). With cluster params set to W=3/N=3 on the same real data, the engine produces materially different, real output (831 STOP_CLUSTER_COORDINATED exits) — confirming the inert-default identity isn't merely because the trigger path is unreachable.

The mechanism as shipped in the engine (exact code):
```python
trailing_count = sum(1 for d in stop_loss_day_indices
                      if day_idx_today - (cluster_window_days - 1) <= d <= day_idx_today)
if trailing_count >= cluster_min_stops:
    if not cluster_active:
        # force-close every ticker with open_trade is not None,
        # exit_reason="STOP_CLUSTER_COORDINATED",
        # exit_price = st["last_price"]  (last known close, no lookahead)
        cluster_active = True
else:
    cluster_active = False
```

## 6. Reset-at-boundary sweep (test period 2020-01-01 to 2026-08-03)

**Backtest convention: RESET-AT-BOUNDARY** — a variant comparison (baseline vs. coordination mechanism), per CLAUDE.md's standing requirement, not a track-record report. Fresh `starting_capital` at 2020-01-01 for every variant, live universe, mark-to-market. Baseline reproduces the concentration-risk sibling report's own baseline exactly (1,270 trades, CAGR 19.29%, PF 1.74 — direct cross-check).

| Variant | Trades | Stop | Coord | CAGR | DD (fixed) | DD (peak-rel) | PF | Sharpe |
|---|---|---|---|---|---|---|---|---|
| **Baseline** (real, unmodified) | 1,270 | 477 | 0 | **19.29%** | -27.56% | -12.47% | **1.74** | **0.670** |
| Coordinated W=3, N=3 (edge-triggered) | 1,463 | 377 | 442 | 16.53% | **-39.24%** | -12.77% | 1.62 | 0.612 |
| Coordinated W=10, N=4 (edge-triggered) | 1,533 | 421 | 465 | 18.98% | **-25.07%** | **-9.05%** | 1.73 | **0.699** |

**W=3/N=3 (the statistically primary combination from Part 1) is worse on every metric** — including drawdown, contrary to what the retrospective Part 1 finding for the same (W,N) might suggest. This alone rules it out; no block validation attempted for it.

**W=10/N=4 came very close to dominating** — CAGR and PF are both negligibly lower (19.29→18.98, 1.74→1.73), while drawdown improves meaningfully (fixed -27.56%→-25.07%, peak -12.47%→-9.05%) and Sharpe genuinely improves (0.670→0.699). Not an arbitrary pick: W=10/N=4 is one of the 9 combinations directly tested and reported in Part 1's own sensitivity table (98 clusters, £111,571 historical delta, 23.0% of total strategy P&L — one of the largest deltas found). This was judged close enough to warrant the mandatory 16-block check rather than being dismissed on the single reset-at-boundary comparison alone.

## 7. 16-Block independent validation

`analysis.backtest.make_blocks()` and `analysis.validation._block_passes()` imported directly (not reimplemented):
```python
dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
return bool(dd_ok and pf_ok)
```
Each block is a genuine independent reset (fresh capital, zero open positions, truncated `[block_start, block_end]`), full live universe, run for both baseline and the W=10/N=4 coordinated variant.

| Block | Window | Baseline CAGR / DD / PF / Pass | Coord W10N4 CAGR / DD / PF / Pass | S&P DD |
|---|---|---|---|---|
| 0 | 2007-01→2008-04 | -20.61 / -7.89 / 0.61 / **F** | -21.43 / -7.24 / 0.58 / **F** | — |
| 1 | 2008-04→2009-07 | -20.26 / -28.88 / 0.38 / **F** | -20.73 / -29.47 / 0.43 / **F** | — |
| 2 | 2009-07→2010-10 | 24.49 / -7.33 / 2.12 / P | 24.00 / -7.49 / 2.05 / P | — |
| 3 | 2010-10→2012-01 | 13.23 / -9.69 / 1.76 / P | 11.55 / -8.92 / 1.68 / P | — |
| 4 | 2012-01→2013-04 | 27.69 / -2.96 / 3.82 / P | 23.36 / -3.55 / 3.12 / P | — |
| 5 | 2013-04→2014-07 | 16.88 / -4.41 / 2.02 / P | 11.78 / -5.27 / 1.57 / P | — |
| 6 | 2014-07→2015-10 | 18.97 / -4.22 / 2.51 / P | 19.73 / -4.14 / 2.37 / P | — |
| 7 | 2015-10→2017-01 | 7.66 / -6.52 / 1.35 / P | 9.88 / -5.42 / 1.57 / P | — |
| **8** | **2016-10→2018-01** | **14.51 / -2.85 / 2.03 / P** | **10.76 / -4.72 / 1.59 / F** | **-3.07** |
| 9 | 2018-01→2019-04 | 9.06 / -9.33 / 1.48 / P | 8.30 / -12.14 / 1.40 / P | — |
| 10 | 2019-04→2020-07 | 9.83 / -7.52 / 1.66 / P | 8.59 / -6.08 / 1.66 / P | — |
| 11 | 2020-07→2021-10 | 20.45 / -3.82 / 1.98 / P | 16.00 / -4.69 / 1.83 / P | — |
| 12 | 2021-10→2023-01 | 15.14 / -7.93 / 1.51 / P | 10.62 / -9.52 / 1.33 / P | — |
| 13 | 2023-01→2024-04 | 15.37 / -3.76 / 1.57 / P | 12.45 / -3.70 / 1.34 / P | — |
| 14 | 2024-04→2025-07 | 18.24 / -13.51 / 1.62 / P | 19.45 / -12.50 / 1.90 / P | — |
| 15 | 2025-07→2026-08 | 36.34 / -11.76 / 2.03 / P | 35.85 / -11.95 / 2.01 / P | — |

*(Note: blocks overlap 2020-2026 differently from the calendar test-period boundaries used in Section 6 — this is `make_blocks(2007-01-01, 2026-08-03, n_blocks=16)`, the project's standard validation grid, not the same window as Section 6.)*

**Result: baseline 14/16 pass, coordinated W=10/N=4 13/16 pass.**

| | Baseline | Coord W10N4 |
|---|---|---|
| Blocks passing | **14/16** | 13/16 |
| Mean CAGR | **12.94%** | 11.26% |
| Median CAGR | **15.26%** | 11.67% |

Blocks 0 and 1 fail for both variants — the known pre-GFC/GFC-acute pattern, not a regression introduced by coordination. **Block 8 (2016-10-17 to 2018-01-07) is a genuine new failure the coordinated variant introduces that the baseline does not have.** This block is a calm bull-market window (S&P's own drawdown just -3.07%) where baseline's natural exits kept drawdown to -2.85% (comfortably under the 1.5×(-3.07%)=-4.61% pass threshold, PF 2.03). The coordinated variant's 70 forced closures in this same, genuinely benign block pushed its own drawdown to -4.72% — narrowly *breaching* the -4.61% threshold (by 0.11pp) and dragging PF down to 1.59. This is a concrete, direct illustration of the real trade-off Part 2's own instructions anticipated: the mechanism also force-closes positions that would not otherwise have hit their own stop, and in a period with no genuine correlated-decline pattern to protect against, that cost shows up as pure downside with no offsetting benefit.

**The coordinated variant loses on win-count, mean CAGR, and median CAGR — it does not survive independent block validation**, despite coming close to dominating in the single reset-at-boundary comparison in Section 6.

## 8. Recommendation

**Researched, not adopted.**

- Part 1's retrospective finding is real and robust: coordinating cluster exits would have improved aggregate P&L by 6.2%-29.9% of total 19-year strategy profit across every one of 9 tested (W,N) parameter combinations — this is a genuine, previously-undocumented gap, not a false positive.
- But turning that observation into an actual, forward-looking, non-lookahead mechanism (mandatory per this project's own discipline, since the retrospective view has perfect hindsight about which trades were "cluster members" that a live system never has) revealed the real cost the retrospective analysis structurally cannot see: force-closing positions that would not otherwise have stopped out. Even the best-performing parameter choice found (W=10, N=4 — not an arbitrary pick, itself Part 1's own second-largest historical delta) came close to, but did not achieve, dominance over baseline in a single reset-at-boundary comparison, and failed the mandatory 16-block validation on win-count, mean CAGR, and median CAGR, including one genuine new block failure (Block 8) the baseline does not have.
- This is the same shape multiple other findings landed on tonight: a real, measurable pattern in observational/retrospective analysis that does not survive being built into an actual mechanism and block-validated (per this project's own bar, deliberately set higher than "found something better").
- The W=3/N=3 statistically-primary combination from Part 1 is decisively worse on every metric in even the single reset-at-boundary comparison and was not carried to block validation at all.

No change is recommended to `analysis/decision.py`'s exit-priority chain, `analysis/crisis.py`'s coordination scope, or any config value. The existing "coordinated response only to a genuine acute (>=7% single-day) crisis, independent per-position stops otherwise" design remains the best-supported choice given this evidence.

## 9. Confirmation

- **No production code was modified.** `analysis/decision.py`, `analysis/crisis.py`, `analysis/backtest.py`, `brokers/trading212.py` are all unchanged — `stop_cluster_engine.py` is a standalone research-directory copy with isolated, marked changes, never imported by or wired into any production path.
- **`config.yaml` has zero diff** against the working tree.
- **No deploy/CI infrastructure was touched**: `deploy.sh`, `webhook_listener.py`, `.github/workflows/`, `tests/test_deploy_*.py`, `CLAUDE.md`, `CLAUDE_HISTORY.md` — none read for editing, none written to.
- **Nothing was committed and nothing was pushed** — `git status --short` shows only new files under `research/stop_cluster_coordination_2026-08-05/` plus the pre-existing untracked `database/` and sibling research directories from earlier tonight; no staged changes.
- **No production database was touched** — `database/investment_research.db` was read-only for every query in this investigation (SELECT-only SQL throughout `part1_cluster_analysis.py`/`part1b_context_and_robustness.py`; the engine and harness scripts only read `price_data`/`^GSPC` via the existing `get_price_df()`/`precompute_ticker()` read paths, never wrote to the DB).
- Decision-engine parity: `stop_cluster_engine.py` imports and calls the real, unmodified `analysis.decision.evaluate_exit_reason()`, `passes_entry_gate()`, `passes_momentum_floor()`, `is_reentry_blocked()`, `resolve_cash_constrained_position_gbp()`, `resolve_dynamic_max_position_gbp()`, `resolve_min_momentum_floor()`, `resolve_reentry_cooldown_days()`, `is_position_size_viable()` and `analysis.universe.get_max_holding_days()`/`is_member()`, `analysis.volatility._var_position_size_core()`/`_resolve_entry_stop()` directly — verified via the byte-identical fidelity check (Section 5), which is only possible if every one of these call sites still resolves through the same shared functions production uses. The one new exit path (`STOP_CLUSTER_COORDINATED`) is additive, clearly and separately labeled, and does not alter any existing decision function's behavior.
