# Momentum-strength-conditioned sizing multiplier — Final Report (2026-08-05)

**Bottom line: researched, not adopted.** A momentum-strength-conditioned sizing multiplier, layered on top of the existing volatility-only VaR sizing (never replacing it), does what it was designed to do mechanically — it sizes "Strong"/"Very Strong" candidates larger — and in a single reset-at-boundary comparison every one of the three schedules tested beat or roughly matched baseline on at least some metrics, with "modest" looking like a clean single-window win (better CAGR, better drawdown both conventions, better PF, tied Sharpe). **None of the three survived 16-block independent validation**: every schedule wins fewer than half its head-to-head blocks against baseline on CAGR, and none improves mean CAGR, mean profit factor, and mean drawdown simultaneously across the 16 independent windows. This is the same "clean single-window win, evaporates under block validation" pattern found in five other investigations tonight (var_confidence=0.925, the 5-day cooldown, ADX<20, risk-adjusted momentum ranking, the 18-month lookback). `config.yaml` was not touched; no production code was modified; nothing was committed or pushed.

A genuine, unanticipated counter-finding also emerged: contrary to the documented concentration-risk concern, all three schedules *reduced* the joint-risk-gap (`indep_gap_60d`) relative to baseline in this test period, not worsened it — reported honestly below with the caveat that this is period-specific, not a general guarantee.

## 0. Environment verification

`database/investment_research.db`: `price_data` covers 540 tickers, 2007-01-01 to 2026-08-03 (`MAX(date)`/`MIN(date)` checked directly). `analysis.universe.get_live_stock_candidates()` returns 503 live candidates as of 2026-08-03 (`build_caches.py` output: "live candidate pool: 503 tickers", 500/503 cached successfully — the same small shortfall other sibling investigations tonight also saw). `database/` is gitignored (`database/*.db` in `.gitignore`) — confirmed. `git status --short` was clean except tonight's already-committed sibling `research/` directories and the untracked `database/` at task start; worked directly in the main checkout (no separate worktree needed, since this investigation makes no `git add`/`git commit` of any kind — see the concurrent-session note in the Confirmation section below for why this mattered partway through).

**A genuinely concurrent, unrelated local session started partway through this investigation** (first observed ~23:06, a `python -m pytest -q` process plus modifications to `deploy.sh`, `data/scheduler.py`, `tests/test_deploy_server_side_blackout.py`, and `.backup` files for those). It never touched anything under `research/momentum_sizing_multiplier_2026-08-05/`, and this investigation never ran `git add`/`git commit`/`git checkout`, so there was no interleaving risk to this task's own output — confirmed directly via `git status --short` at the end of the run, showing only that other session's own untouched files plus this investigation's own new, isolated directory.

## Step 1 — momentum plays no role in current sizing (confirmed, exact call chain traced)

**Backtest** (`analysis/backtest.py:simulate_portfolio_compounding_mtm()`, `_admit_group()` closure, lines ~1296–1420): `momentum_val` is extracted from the row (line 1306–1307) and used *only* for: the `passes_momentum_floor()` gate (1314), the sort key for admission ranking (1332/1334: `candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))`), and the rejection-log dict (1391). The actual sizing chain:

```python
dynamic_max_position_gbp = resolve_dynamic_max_position_gbp(
    current_balance, per_trade_pct, absolute_max_position_gbp,
)                                                    # (account_value, pct, ceiling) -- no momentum
position_gbp, _dv, _hv = _var_position_size_core(
    log_returns, current_balance, dynamic_max_position_gbp, st["max_hold"],
    var_confidence, max_risk_pct, min_position_gbp,
)                                                    # 7 args: price history, balance, ceiling,
                                                      # holding_days (a config constant, 30 for every
                                                      # live stock), var_confidence, max_risk_pct,
                                                      # min_position_gbp -- no momentum anywhere
position_gbp = resolve_cash_constrained_position_gbp(position_gbp, cash)  # position, cash -- no momentum
is_position_size_viable(position_gbp)                                    # position only
```

**Live** (`brokers/trading212.py:maybe_place_orders()`, lines ~3876–3909): identical pattern — `resolve_dynamic_max_position_gbp(total_val, per_trade_pct, absolute_max_position_gbp)` → `_var_position_size_core(log_returns, total_val, dynamic_max_position_gbp, max_hold, var_confidence, max_risk_pct, min_position_gbp)` → `resolve_cash_constrained_position_gbp(position_gbp, free_cash)` → `is_position_size_viable(position_gbp)`. `momentum_pct` is passed to `_queue_pending_order(..., momentum_pct=state["momentum_pct"], ...)` purely for storage/display and later `ORDER BY momentum_pct DESC` ranking at submission (`_load_pending_orders()`, line 1543) — never into any sizing call above it.

**Confirmed with certainty**: momentum (raw or derived) never appears as an argument to `_var_position_size_core()`, `resolve_dynamic_max_position_gbp()`, or `resolve_cash_constrained_position_gbp()`, in either live or backtest. Sizing is purely volatility-based. This directly confirms the task brief's premise from code-level evidence, not assumption.

## Step 2 — isolated multiplier engine and fidelity

`research/momentum_sizing_multiplier_2026-08-05/multiplier_engine.py` is a verbatim copy of `simulate_portfolio_compounding_mtm()`, with one isolated, marked change inside `_admit_group()`, applied **after** the real, unmodified `_var_position_size_core()` result:

```python
_position_gbp_var = position_gbp                       # pre-multiplier, unmodified VaR result
_strength_label = _momentum_strength_label(momentum_val, _bands)   # ported verbatim from main.py
_multiplier = band_multiplier.get(_strength_label, 1.0)
position_gbp = position_gbp * _multiplier
_backstop_bound = position_gbp > dynamic_max_position_gbp
position_gbp = min(position_gbp, dynamic_max_position_gbp)   # re-apply ceiling AFTER the multiplier
position_gbp = round(position_gbp, 2)
# ... UNCHANGED below: resolve_cash_constrained_position_gbp(), is_position_size_viable()
```

**Ordering, stated explicitly per the task's requirement**: `_var_position_size_core()`'s own internal clamp already ran on the *pre-multiplier* value, so a multiplier > 1.0 can push the result back above `dynamic_max_position_gbp` (which already incorporates the `absolute_max_position_gbp` backstop via `resolve_dynamic_max_position_gbp()`) — this is exactly why the ceiling is explicitly re-applied immediately after the multiplier, before anything else runs. `resolve_cash_constrained_position_gbp()` and `is_position_size_viable()` are completely unchanged and run *after* the multiplier+reclamp, so the cash clamp always sees the final, post-multiplier, post-backstop figure — both existing safety limits genuinely still apply to the (possibly larger) multiplied position.

**Momentum-strength bands**: `_compute_momentum_strength_bands()`/`_momentum_strength_label()` are a byte-for-byte port of `main.py`'s functions of the same name — diffed directly against the current `main.py` source, not approximated (bottom 25%=Weak, 25–75th=Moderate, 75–90th=Strong, top 10%=Very Strong). Population used: the day's real gate-passing candidate pool for that admission group, built *before* sort and *before* capacity check — the same population `brokers/trading212.py` writes into `pending_orders` for that `decided_date`, which is exactly what `main.py`'s `cdf.groupby("decided_date")["momentum_pct"].transform(_label_group)` uses. Since ETF trading is disabled in production, only `stock_group` ever populates bands in this live-universe sweep — matching production exactly.

**Fidelity self-check** (`self_check.py`, 60-ticker/18-month subset, the same convention every sibling investigation tonight used):
| Check | Result |
|---|---|
| `band_multiplier=None` vs real function | 306 vs 306 trades, **byte-identical: True** |
| `band_multiplier=`explicit all-1.0 vs real function | 306 vs 306 trades, **byte-identical: True** |
| Aggressive schedule actually changes output | 306 vs 287 trades, **differs: True** (not an inert no-op) |
| Decision-engine parity (from this run's real trades) | 0/306 holding-period violations vs `get_max_holding_days()`; exit reasons `{END_OF_DATA, MAX_HOLDING, SIGNAL_OFF, STOP_LOSS}`, all within `evaluate_exit_reason()`'s vocabulary |

**Independent cross-validation of the whole harness**: `joint_risk_engine.py`'s baseline-arm mean `indep_gap_60d` on the full live-universe reset-at-boundary run = **1.239192**, matching `research/deployment_cap_tradeoff_2026-08-05`'s own independently-reported baseline (50%/5%) figure of **1.239** — cross-validated against a sibling investigation's independently-produced number, not just an internal self-check. Baseline block-by-block figures from my own run (below) also reproduce the sibling's exact published per-block numbers to the decimal (e.g. block 0 CAGR=-20.61, block 15 CAGR=36.34).

**A real methodological artifact found and fixed mid-investigation** (matching the discipline `risk_adjusted_momentum_2026-08-05` used): the raw `backstop_bound` diagnostic field I recorded during the run has a float-epsilon false positive at `multiplier==1.0` — comparing a value rounded to 2dp against an unrounded `dynamic_max_position_gbp` occasionally compares as fractionally greater by ~1e-10, even when the two are equal to the penny. Confirmed directly: every such flagged case has exactly **0.0** £ overshoot — harmless to actual trading behaviour (`min()` with an equal value is a no-op regardless), but it would have inflated my own "how often does the backstop bind" count if trusted naively. Corrected in `step4_checks.py` using an explicit 1p tolerance, restricted to genuinely multiplied candidates.

## Step 3 — reset-at-boundary sweep (full results)

**Schedules tested** (exact code):
```python
SCHEDULE_MODEST      = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.15, "Very Strong": 1.3}
SCHEDULE_AGGRESSIVE  = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.3,  "Very Strong": 1.6}
SCHEDULE_WHOLE_DIST  = {"Weak": 1.0, "Moderate": 1.1, "Strong": 1.2,  "Very Strong": 1.35}
```

**Convention: RESET-AT-BOUNDARY** (a variant comparison against the current unmultiplied baseline, per CLAUDE.md's standing requirement — not a track-record report). Test period 2020-01-01 to 2026-08-03, live universe (503 candidates, 500 cached), mark-to-market, `per_trade_pct=0.05`, `max_total_deployed=0.5` (current production config), everything else at production defaults.

| Variant | Trades | CAGR | DD (fixed) | DD (peak-rel) | PF | Sharpe | Ending £ |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Baseline** (real, unmodified) | 1,270 | 19.29% | -27.56% | -12.47% | 1.74 | 0.670 | £159,699 |
| Modest | 1,122 | **20.71%** | **-25.04%** | **-9.96%** | **1.76** | 0.670 | £172,658 |
| Aggressive | 1,075 | 19.04% | -29.23% | -11.27% | 1.68 | 0.615 | £157,557 |
| Whole-distribution | 1,097 | **21.49%** | -38.85% | **-11.02%** | **1.79** | 0.666 | £180,170 |

Baseline reproduced exactly: 1,270 trades, CAGR 19.29%, DD -27.56%, matching every sibling report tonight to the decimal.

**Modest dominates baseline on 4 of 5 metrics** (better CAGR, both DD conventions, and PF; Sharpe tied) — the cleanest single-window "win" of the three, and the one flagged as most promising going into Step 5. **Aggressive is worse than baseline on every metric except peak-relative DD** — more concentration did not pay off in the single-window comparison. **Whole-distribution has the best CAGR and PF of any arm but a materially worse fixed-denominator drawdown** (-38.85% vs -27.56%) — a real number, but largely a mechanical artifact of compounding to a much higher equity peak (£180k vs £160k ending balance) before any real pullback, the same effect `deployment_cap_tradeoff_2026-08-05` documented for its own grid; its peak-relative DD (-11.02%) is actually better than baseline's.

## Step 4 — three explicit checks

### Check 1 — `absolute_max_position_gbp` / `dynamic_max_position_gbp` backstop interaction

Quantified from real per-candidate sizing records (`results/reset_<arm>_stats.json`), corrected for the float-epsilon artifact described above:

| Schedule | Sized candidates | Multiplied (mult≠1.0) | Genuinely backstop-bound | Fully capped at ceiling |
|---|---:|---:|---:|---:|
| Modest | 416,585 | 104,750 | 86,722 (**82.8%**) | 86,722 (82.8%) |
| Aggressive | 417,475 | 104,977 | 94,149 (**89.7%**) | 94,150 (89.7%) |
| Whole-distribution | 417,122 | 312,633 | 263,029 (**84.1%**) | 263,036 (84.1%) |

**The backstop works correctly and binds very frequently** — real trade-level evidence, e.g. COST 2020-04-01 (modest): VaR-sized £2,062.36 → 1.3× multiplier would give £2,681.07 → correctly re-clamped to the £2,369.63 ceiling. This is a significant, underappreciated finding in its own right: **for the large majority of "Strong"/"Very Strong" candidates, the nominal schedule multiplier is completely absorbed by the pre-existing ceiling** — the realized boost is `ceiling / var_sized_value`, not the schedule's nominal 1.15–1.6×. The multiplier still increases position size relative to baseline whenever `var_sized_value < ceiling` (as in the COST example: £2,062→£2,370, a genuine but smaller-than-nominal ~15% increase, not the intended 30%), but the "safety limit still applies" claim is not just theoretically true — it is the dominant, majority-case behaviour of the variant as tested.

### Check 2 — trade-count / capacity displacement

| Schedule | Trades | Distinct tickers | Avg position | Total capital-days | Overlap with baseline entries |
|---|---:|---:|---:|---:|---:|
| Baseline | 1,270 | 210 | £3,314 | £95,964,237 | — |
| Modest | 1,122 | 185 | £3,806 | £97,938,510 | 27.8% (312/1122) |
| Aggressive | 1,075 | 183 | £3,888 | £95,037,539 | 24.8% (267/1075) |
| Whole-distribution | 1,097 | 194 | £3,963 | £98,551,410 | 32.4% (355/1097) |

**Real, evidenced characterization — a genuine cost, not a neutral reallocation.** Total capital-days deployed (`position_gbp × holding_days`, summed) stays roughly flat or slightly higher (+2.1% modest, -0.9% aggressive, +2.7% whole-dist) — capital utilization itself is preserved. But entry overlap with baseline is low (only 25–32% of trades share the same ticker+entry-date as baseline), and distinct tickers traded drops materially (210→183–194, an 8–13% reduction). This is not "the same trades, just resized" — sizing up early admissions changes remaining capacity for the rest of that day and cascades into a genuinely different admission sequence over subsequent days (a path-dependence effect inherent to the skip-and-continue capacity mechanism). The net effect is real: fewer distinct opportunities taken, concentrated into larger positions, even though aggregate capital-days deployed is preserved.

### Check 3 — joint-risk-gap (`indep_gap_60d`), reusing `concentration_risk_2026-08-05`'s formula directly

```python
indep_gap_60d = P_real/P_ind - 1
P_real = sqrt(w' Cov w)                 # realized 60d covariance, weighted by entry-time position_gbp
P_ind  = sqrt(sum(w_i^2 * sigma_i^2))   # zero-cross-correlation assumption
```
Computed from each schedule's own real day-by-day open-position weights (test period 2020–2026, `n=1,643` days with ≥2 open positions each arm):

| Arm | mean | p95 | Δ mean vs baseline |
|---|---:|---:|---:|
| Baseline | 1.239 | 1.957 | — |
| Modest | 1.100 | 1.704 | **-11.3%** |
| Aggressive | 1.049 | 1.644 | **-15.4%** |
| Whole-distribution | 1.081 | 1.634 | **-12.8%** |

**Direct answer to the anticipated risk: no, sizing up high-momentum names did NOT worsen the joint-risk gap in this test period — it consistently reduced it, for all three schedules, both in the mean and the tail (p95).** This was checked directly from each schedule's own real open-position weights, not extrapolated from baseline, per the task's requirement. Trade-level inspection of the backstop examples above shows why this is plausible rather than paradoxical: the "Very Strong"/"Strong" candidates actually boosted in this 2020–2026 test period (e.g. COST, DG, WMT, KR, GIS, EQIX, CHTR, BMY) are dominated by consumer-staples/retail/telecom names, not the semiconductor/storage cluster (SNDK, WDC, MU, LITE, STX) that drove the documented worst episodes — so the concern's specific mechanism (sizing up the correlated cluster) did not bind during this particular window. **This should not be read as a general guarantee** — which names land in the top momentum bands is time-varying, and a different test period could plausibly show the opposite; it is reported as an honest, evidenced, single-window finding, not extrapolated.

## Step 5 — 16-block independent validation

**Warranted and run, not skipped**: modest showed a genuine single-window win (dominant on 4/5 metrics), and whole-distribution showed a plausible single-window win on CAGR/PF — per the task's own instruction and the repeated lesson from five sibling investigations tonight, neither was trusted without block validation. `analysis.backtest.make_blocks()` and `analysis.validation._block_passes()` imported directly, not reimplemented:

```python
dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
```

All four arms (baseline + all 3 schedules) were block-validated, not just the promising ones — aggressive included, for a complete picture despite its poor single-window showing. Each of the 16 blocks is a genuine independent reset (fresh £50,000, zero open positions, truncated `[block_start, block_end]`).

| | Baseline | Modest | Aggressive | Whole-dist |
|---|---:|---:|---:|---:|
| Blocks passing | **14/16** | 14/16 | 14/16 | 14/16 |
| Failing blocks | 0, 1 | 0, 1 | 0, 1 | 0, 1 |
| Mean block CAGR | **12.94%** | 11.83% | 13.16% | 12.06% |
| Median block CAGR | **15.25%** | 14.21% | 16.31% | 14.41% |
| Mean block PF | **1.778** | 1.691 | 1.761 | 1.714 |
| Mean block \|DD\| | **8.27%** | 8.73% | 8.68% | 8.54% |
| Blocks won head-to-head vs baseline (CAGR) | — | 5/16 | 7/16 | 6/16 |

**None survives.** Baseline's own block-by-block figures reproduce `deployment_cap_tradeoff_2026-08-05`'s exact published numbers to the decimal (block 0: CAGR=-20.61, block 15: CAGR=36.34, etc.) — cross-validating this run's methodology, not just its internal consistency.

- **Modest** — the schedule that looked cleanest in Step 3 (dominant on 4/5 metrics) — performs *worse* than baseline across the 16 blocks on every measure: lower mean and median CAGR, lower mean PF, higher mean drawdown, and it wins only **5 of 16** blocks head-to-head. This is the textbook single-window false positive this file's own precedent (18-month lookback, var_confidence=0.925) describes.
- **Aggressive**, despite being the worst performer in the single-window comparison, has a slightly *better* mean/median block CAGR than baseline (13.16%/16.31% vs 12.94%/15.25%) — but still worse mean PF (1.761 vs 1.778), worse mean drawdown (8.68% vs 8.27%), and wins only 7/16 head-to-head — not a robust win either, just a less-bad loser than modest.
- **Whole-distribution** shows the identical pattern to modest: worse mean/median CAGR, worse PF, worse drawdown, 6/16 head-to-head wins, despite the best single-window CAGR/PF of any arm.

**No schedule wins more than half its head-to-head blocks, and none improves mean CAGR, mean PF, and mean drawdown simultaneously relative to baseline.** The single-window story and the block-validated story disagree for every one of the three schedules, in the same direction every other sibling investigation tonight found: single-window results in this project are not trustworthy without independent block confirmation.

## Decision-engine parity (all arms, real evidence)

Per CLAUDE.md's standing requirement, verified from each arm's own real trade output, not inferred:

| Arm | Trades | Exit reasons observed | Within vocabulary | Holding-period violations |
|---|---:|---|---|---:|
| Baseline | 1,270 | CRISIS_EXIT, END_OF_DATA, MAX_HOLDING, SIGNAL_OFF, STOP_LOSS | True | 0 |
| Modest | 1,122 | (same set) | True | 0 |
| Aggressive | 1,075 | (same set) | True | 0 |
| Whole-distribution | 1,097 | (same set) | True | 0 |

Plus the fidelity self-check's own parity evidence (Step 2), and the backstop-interaction evidence (Check 1) confirming `resolve_dynamic_max_position_gbp()`'s ceiling and `resolve_cash_constrained_position_gbp()` both still genuinely apply after the multiplier, with real trade-level proof, not just code inspection.

## Recommendation

**Researched, not adopted.** No schedule clears the "clearly, exceptionally strong improvement" bar for implementation:
- Modest — the most promising single-window result — is worse than baseline on every metric across 16 independent blocks and wins only 5/16 head-to-head.
- Aggressive and whole-distribution both show the identical single-window-vs-block-validated reversal pattern.
- The one genuinely positive, well-evidenced finding — that the tested multiplier schedules did not worsen (and in this period, reduced) the joint-risk-gap — is real but insufficient on its own to justify adoption, since the primary hypothesis (does sizing up conviction improve outcomes) did not survive block validation.
- A secondary, worthwhile finding for anyone revisiting this idea later: the existing `absolute_max_position_gbp`/`dynamic_max_position_gbp` ceiling absorbs 83–90% of the nominal multiplier's intended effect for "Strong"/"Very Strong" candidates — any future variant of this idea would need to either raise `per_trade_pct` itself (a different, already-explored trade-off — see `deployment_cap_tradeoff_2026-08-05`) or accept that the realized effect is much smaller than the nominal schedule suggests.

## Confirmation

**Files touched, all under `research/momentum_sizing_multiplier_2026-08-05/`:** `multiplier_engine.py`, `self_check.py`, `build_caches.py`, `caches.pkl`, `build_caches.log`, `reset_sweep_worker.py`, `reset_sweep.log`, `joint_risk_engine.py`, `joint_risk_gap_results.json`, `step4_checks.py`, `step4_check1_check2.json`, `parity_check.py`, `parity_check_results.json`, `block_validate_worker.py`, `block_validate.log`, `block_validate2.log`, `block_validation_results.json`, `results/reset_{baseline,modest,aggressive,whole_dist}.json` (full trade lists), `results/reset_{modest,aggressive,whole_dist}_stats.json` (full per-candidate sizing records), `results/{baseline,modest,aggressive,whole_dist}_daily_panel.csv`, `results/blocks_{baseline,modest,aggressive,whole_dist}.json` + `_trades.json` (per-block metrics and full trade lists).

**No production code was modified**: `analysis/decision.py`, `analysis/volatility.py`, `analysis/backtest.py`, `brokers/trading212.py`, `main.py` are all unchanged — confirmed via `git status --short` showing no diff against any of these files. Every real function (`_var_position_size_core()`, `resolve_dynamic_max_position_gbp()`, `resolve_cash_constrained_position_gbp()`, `is_position_size_viable()`, `evaluate_exit_reason()`, `passes_entry_gate()`, `get_max_holding_days()`, `make_blocks()`, `_block_passes()`) was imported directly, never copied or reimplemented, except for the one isolated, marked multiplier addition inside the copied `_admit_group()` closure.

**`config.yaml` has zero diff** — confirmed via `git status --short`. **No deploy/CI infrastructure was touched**: `deploy.sh`, `webhook_listener.py`, `.github/workflows/`, `tests/test_deploy_*.py` were never opened by this investigation (the diffs visible in `git status` against `deploy.sh`/`data/scheduler.py`/`tests/test_deploy_server_side_blackout.py` belong to the unrelated concurrent session described in Section 0, confirmed by their timing — first appearing at 23:06, well after this investigation's own environment-verification `git status` check — and by their content having nothing to do with momentum sizing).

**Nothing was committed and nothing was pushed** — this investigation never ran `git add` or `git commit`. **No production database was touched** — every query was read-only (`get_price_df()`, `precompute_ticker()`'s existing read path); no writes anywhere. **Nothing was deployed anywhere.** This is investigation and reporting only, per the task's explicit instruction.
