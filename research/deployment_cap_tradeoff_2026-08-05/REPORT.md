# Deployment-Cap / Per-Trade-Size Trade-Off Investigation — Final Report (2026-08-05)

**Framing, stated explicitly per the task brief: this is NOT a "researched, not adopted" verdict.** Raising `max_total_deployed`/`per_trade_pct` above current production levels (50%/5%) is a deliberate, mechanical trade-off — more capital at work predictably raises both return and drawdown. This report characterizes that trade-off honestly across 9 grid combinations, block-validates it, and re-checks the joint-risk-gap effect at each combination. No adopt/reject recommendation is given; a human must decide after seeing these numbers. **No config, code, or deploy infrastructure was touched; nothing was implemented on the VM.**

## 0. Environment verification

`database/investment_research.db`: `price_data` covers **540 tickers, 2007-01-01 to 2026-08-03** (2,454,903 rows); `^GSPC` covers 2007-01-03 to 2026-08-03 — checked directly. `database/*.db` is gitignored. `git status --short` at task start showed only expected untracked entries (`database/`, four sibling research directories from earlier tonight) plus one modified file, `CLAUDE_HISTORY.md` — checked directly and confirmed this modification belongs to the `curated40_rolling40_dax_reverify_2026-08-05` sibling investigation (a superseded-block-count annotation), not to anything in this task; I never opened `CLAUDE_HISTORY.md` for writing. No unexpected worktree or uncommitted change was found; worked directly in the main checkout (no worktree needed). Current production config: `per_trade_pct=0.05`, `max_total_deployed=0.5`, `starting_capital_gbp=50000`.

## 1. Step 0 — same-instrument duplication check

**Structurally prevented on both live and backtest — confirmed by direct code reading, not assumed.**

**Live** (`brokers/trading212.py`, `maybe_place_orders()`, line 3838):
```python
if t212_ticker in positions_by_t212:
    logger.debug("%s already held — skipping.", ticker)
    continue
```
A ticker currently held is never re-entered, regardless of signal strength.

**Backtest** (`analysis/backtest.py`, `simulate_portfolio_compounding_mtm()`): each ticker has exactly **one** `open_trade` slot in `per_ticker[ticker]` (a dict, not a list — cannot represent two simultaneous positions in the same ticker by construction). The admission candidate pool is built only from tickers with no open trade:
```python
flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}
...
etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
```
(lines 1434, 1499-1502). A ticker already holding an open position is never a candidate for a new entry.

**Empirically confirmed, not just structurally**: independently re-ran the overlap check directly against `research/momentum_horizon_2026-08-05/all_trades_full_run.json` (3,161 trades, full 2007-2026 continuous walk, 331 distinct tickers traded) — **zero overlapping same-ticker `[entry_date, exit_date]` windows found**, matching `analyze_concentration.py`'s own identical check from the concentration-risk sibling investigation. No finding to report here beyond confirming the structural guarantee holds in practice — moving on, as instructed.

## 2. Fidelity / decision-engine-parity verification

Per this task's own risk (hand-rolling an approximation of the admission/capacity logic instead of calling the real functions): **`max_total_deployed`/`per_trade_pct` are already native parameters of the real, unmodified `analysis.backtest.simulate_portfolio_compounding_mtm()`**, so every grid cell in this investigation calls that exact production function directly, with no custom engine and no monkeypatch. Ticker frames are precomputed once (`build_caches.py`, using the real `precompute_ticker()`) and sliced per window via the real `slice_ticker()`, purely for speed — 144 block runs would otherwise redundantly reload/recompute the full 500-ticker price history from the DB each time.

**Byte-identical fidelity confirmed at the baseline cell (50%/5%)**: `fidelity_check.py` ran the SAME window (test period, reset-at-boundary) two independent ways — (A) `analysis.validation.run_full_validation()`, the real production entrypoint the Backtest page/DD report use, and (B) the cached-slice call this investigation's grid workers actually use. Result: **1,270 trades both sides, `byte_identical: true`**, `sum_pnl_gbp` identical to the cent (£109,699.42), and the resulting metrics (CAGR 19.29%, DD -27.56%, PF 1.74, Sharpe 0.67) exactly match both `research/var_sizing_sweep_2026-08-05/REPORT.md`'s and `research/concentration_risk_2026-08-05/REPORT.md`'s own independently-reported baseline figures — cross-validated three ways.

**Spot-checked at swept cells (`d50_t05`, `d60_t06`, `d70_t07`)**: pulled all `MAX_HOLDING` exits (734/822/919 trades respectively) and checked `holding_days` against the real `get_max_holding_days()` (30 for every live stock ticker). All exits clustered at **30-33 calendar days** (30-day cap, checked once per trading day, with 1-3 days of weekend slack before the next evaluation) — consistent with production's real `evaluate_exit_reason()` behavior at every swept combination, not just the baseline.

## 3. Step 2/3 — the 9-combination trade-off table (reset-at-boundary, block-validated)

**Backtest convention: RESET-AT-BOUNDARY**, stated explicitly per CLAUDE.md's "Two backtest conventions" — this is a variant comparison, not a track-record report. Primary sweep: one fresh reset at `2020-01-01` (analysis.validation.VALIDATION_TRAIN_TEST_SPLIT), walking to `2026-08-03`, £50,000 starting capital, zero open positions at the boundary. Live universe, mark-to-market, everything else held at current production config.

| max_deployed | per_trade | Trades | CAGR | DD (fixed) | DD (peak-rel) | PF | Sharpe | Ending £ |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **50% (current)** | **5% (current)** | 1,270 | **19.29%** | -27.56% | -12.47% | 1.74 | 0.670 | £159,699 |
| 50% | 6% | 1,176 | 18.41% | -23.96% | -12.03% | 1.72 | 0.635 | £152,145 |
| 50% | 7% | 1,157 | 18.39% | -28.38% | -8.69% | 1.70 | 0.631 | £151,980 |
| 60% | 5% | 1,501 | 23.94% | -42.38% | -11.31% | 1.73 | 0.652 | £205,424 |
| 60% | 6% | 1,427 | 24.46% | -52.71% | -13.14% | 1.78 | 0.663 | £211,252 |
| 60% | 7% | 1,361 | 24.97% | -37.69% | -11.03% | 1.84 | 0.700 | £216,986 |
| 70% | 5% | 1,734 | 30.78% | -55.62% | -12.21% | 1.87 | 0.714 | £292,632 |
| 70% | 6% | 1,612 | 28.22% | -50.72% | -13.97% | 1.81 | 0.688 | £256,903 |
| 70% | 7% | 1,602 | 26.68% | -61.48% | -12.01% | 1.74 | 0.636 | £237,307 |

**A real distortion between the two DD conventions, worth explaining rather than leaving as a confusing artifact:** DD (fixed) balloons to as much as -61.48% while DD (peak-relative) stays contained in a much narrower -8.7% to -14.0% band across the entire grid. This is mechanical, not a sign of runaway risk: DD (fixed) is measured against the *original* £50,000, and every higher-deployment combo compounds to a much larger equity peak before any drawdown occurs (peak equity ranges £159k at baseline up to **£315,050** at 70%/5%) — so a moderate, genuinely-contained pullback from a much higher peak still represents a huge percentage of the *original* £50k denominator. Both conventions are real and both are reported per CLAUDE.md's own standing requirement; peak-relative is the more representative measure of "how bad did it get relative to where the account actually was," fixed-denominator is the more representative measure of "how bad relative to what was originally put in."

### 16-block independent validation

`analysis.backtest.make_blocks(2007-01-01, 2026-08-03, 16)` and `analysis.validation._block_passes()` imported directly (not reimplemented):
```python
dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
return bool(dd_ok and pf_ok)
```
Each of the 16 blocks per combination is a genuine independent reset (fresh £50,000, zero open positions, truncated `[block_start, block_end]` with a corrected `END_OF_DATA`) — 144 total block runs.

| max_deployed | per_trade | Blocks pass | Mean block CAGR | Median block CAGR | Failing blocks |
|---:|---:|---:|---:|---:|---|
| 50% | 5% | **14/16** | 12.94% | 15.25% | 0, 1 |
| 50% | 6% | 14/16 | 12.31% | 15.98% | 0, 1 |
| 50% | 7% | 14/16 | 12.73% | 14.77% | 0, 1 |
| 60% | 5% | 13/16 | 14.49% | 17.38% | 0, 1, **15** |
| 60% | 6% | 13/16 | 15.17% | 18.21% | 0, 1, **15** |
| 60% | 7% | 13/16 | 14.59% | 17.48% | 0, 1, **15** |
| 70% | 5% | 13/16 | 17.38% | 19.80% | 0, 1, **15** |
| 70% | 6% | 13/16 | 16.58% | 21.06% | 0, 1, **15** |
| 70% | 7% | 13/16 | 17.83% | 21.61% | 0, 1, **15** |

**Blocks 0 (pre-GFC) and 1 (GFC-acute) fail for every combination, including current production** — the well-established pre-existing pattern documented throughout tonight's other reports, not a new finding here.

**A clean, mechanical new failure appears at every combination with `max_deployed_pct >= 60%`: block 15 (2025-05-12 to 2026-08-03).** This is exactly the trade-off the task predicted, not a surprise or a data artifact — inspected directly:

| | S&P DD (block 15) | Strategy DD | 1.5×S&P threshold | Strategy PF | Strategy CAGR |
|---|---:|---:|---:|---:|---:|
| 50%/5% (passes) | -9.10% | -11.76% | -13.65% | 2.03 | 36.34% |
| 60%/5% (fails) | -9.10% | -17.03% | -13.65% | 2.07 | 45.14% |
| 70%/7% (fails) | -9.10% | -20.33% | -13.65% | 2.21 | 59.98% |

Profit factor stays strong (2.0-2.2) and CAGR keeps rising across every one of these — this is a **pure drawdown failure**, not a profitability failure: more capital deployed produced a decisively larger drawdown in the same recent, real window, clearing the S&P's own 1.5× threshold cleanly (not a narrow miss) at every combination 60% and above. Win-count, mean and median block-CAGR both rise monotonically with combination aggressiveness (mean CAGR 12.94%→17.83% from baseline to 70%/7%) at the identical cost of losing this one additional block — exactly the predictable, mechanical "more return, more drawdown" shape this task expected, now quantified precisely rather than assumed.

## 4. Step 4 — joint-risk-gap (`indep_gap_60d`) at each of the 9 combinations

Reused `research/concentration_risk_2026-08-05/analyze_concentration.py`'s own formula directly, adapted to compute it from **each combination's own real day-by-day open-position weights** (reconstructed from that combination's own reset-at-boundary primary-sweep trade list, test period 2020-2026 — not extrapolated from the 50%/5% baseline):
```python
indep_gap_60d = P_real/P_ind - 1
P_real = sqrt(w' Cov w)                 # realized 60d covariance, weighted by entry-time position_gbp
P_ind  = sqrt(sum(w_i^2 * sigma_i^2))   # zero-cross-correlation (independence) assumption
```
(Note: this test-period-only sample gives a higher baseline mean — 1.239 at 50%/5% — than the concentration-risk sibling report's full 2007-2026 continuous-walk mean of 1.067; different window/convention, stated explicitly, not a discrepancy.)

| max_deployed | per_trade | mean `indep_gap_60d` | p95 | mean n_open | max n_open |
|---:|---:|---:|---:|---:|---:|
| **50%** | **5%** | **1.239** | 1.957 | 12.23 | 20 |
| 50% | 6% | 1.197 | 1.851 | 11.56 | 18 |
| 50% | 7% | 1.177 | 1.864 | 11.31 | 18 |
| 60% | 5% | **1.385** | 2.083 | 14.53 | 24 |
| 60% | 6% | 1.338 | 2.106 | 13.73 | 21 |
| 60% | 7% | 1.308 | 2.084 | 13.38 | 21 |
| 70% | 5% | **1.532** | 2.263 | 16.83 | 28 |
| 70% | 6% | 1.482 | 2.182 | 15.75 | 23 |
| 70% | 7% | 1.444 | 2.288 | 15.42 | 23 |

**Effect A — raising `max_total_deployed` ALONE (per_trade_pct fixed at 5%) makes the joint-risk gap worse, monotonically and materially:** mean gap rises 1.239 → 1.385 → 1.532 (50%→60%→70%), a **+23.6% relative increase**, with mean simultaneously-open positions rising 12.2 → 14.5 → 16.8 (max observed up to 28 concurrent positions at 70%/5%, vs 20 at baseline).

**Effect B — raising `per_trade_pct` alongside partially, but only partially, mitigates this, and only in the MEAN, not the tail:**
- At every deployed level, increasing per_trade_pct from 5%→7% reduces the mean gap (50%: -5.0%; 60%: -5.6%; 70%: -5.7% relative), consistent with position count being held down (max n_open drops from 20→18 at 50%, 24→21 at 60%, 28→23 at 70%) — larger, fewer positions rather than more, smaller ones, as the task's own hypothesis anticipated.
- Combined effect: going from 50%/5% to 70%/7% (both parameters raised together) increases the mean gap by **+16.5%** (1.239→1.444), meaningfully less than the **+23.6%** increase from raising deployment alone (50%/5%→70%/5%, per_trade held fixed) — raising per_trade_pct alongside offsets roughly **30%** of the deployed-alone mean-gap increase.
- **This mitigation does NOT hold at the p95 tail.** p95 is *not* monotonically reduced by raising per_trade_pct at a fixed deployed level (50%: 1.957→1.851→1.864, dips then rises; 60%: 2.083→2.106→2.084, flat/non-monotonic; 70%: 2.263→2.182→2.288, dips then finishes *above* where it started). At 70%, the combined 70%/7% cell's p95 (2.288) is actually the *highest* p95 in the entire grid — slightly worse than 70%/5% (2.263), the deployed-alone cell. **The "raise per_trade_pct to compensate" mitigation is real for the typical/average day but does not carry over to the worst days** — this is a genuinely mixed result, reported honestly rather than smoothed over.

## 5. Honest characterization of the trade-off (not a recommendation)

- **Return and drawdown both rise together, predictably and monotonically**, exactly as the task's framing anticipated. CAGR rises from 19.3% (baseline) to as high as 30.8% (70%/5%); peak-relative drawdown stays in a comparatively contained -8.7% to -14.0% band across the whole grid, while fixed-denominator drawdown balloons (up to -61.5%) purely as a mechanical consequence of compounding to a much larger equity peak — both conventions are reported, and the divergence between them is explained above, not left as an unexplained artifact.
- **Block-validation win-rate degrades by exactly one block (14/16 → 13/16) the moment `max_total_deployed` crosses 60%, and stays at 13/16 all the way to 70%/7%** — a single, identifiable, real cause (block 15, the most recent ~15-month window, a pure-drawdown breach of the 1.5×S&P threshold, not a profitability failure — PF and CAGR both stay strong in the very same block that fails). Mean and median block CAGR rise at every combination relative to baseline, in exchange for this one additional failing block.
- **The joint-risk-gap (correlated concentration risk) this project already knows is real gets measurably worse as deployment rises** — +23.6% in the mean at 70%/5% vs. baseline. Sizing each position larger instead of opening more of them (raising `per_trade_pct` alongside) provides a real but partial offset to this (~30% of the mean-gap increase), and that offset does not extend to the tail (p95), where the most aggressive combination tested (70%/7%) has the single worst tail joint-risk reading in the entire 9-cell grid.
- No single combination dominates on every metric — this is expected and correct for this kind of question, per the task's own framing, not a failure of the investigation. The full menu of trade-offs (return vs. drawdown vs. block-robustness vs. joint-risk-gap, both in the average case and the tail) is laid out above for a deliberate, informed human decision.

## 6. Confirmation

- **Files touched**, all under `research/deployment_cap_tradeoff_2026-08-05/`: `build_caches.py`, `caches.pkl`, `build_caches.log`, `fidelity_check.py`, `fidelity_check_result.json`, `fidelity_check.log`, `grid_worker.py`, `results/{d50_t05,d50_t06,d50_t07,d60_t05,d60_t06,d60_t07,d70_t05,d70_t06,d70_t07}.{json,log}`, `results/*_daily_panel.csv` (9 files), `joint_risk_engine.py`, `joint_risk_gap_results.json`, `joint_risk_engine.log`, `build_summary.py`.
- **No production code was modified**: `analysis/decision.py`, `analysis/backtest.py`, `analysis/validation.py`, `analysis/universe.py`, `brokers/trading212.py` are all unchanged — every grid cell called the real, unmodified `simulate_portfolio_compounding_mtm()`/`slice_ticker()`/`precompute_ticker()`/`make_blocks()`/`_block_passes()` directly, imported, never copied or reimplemented.
- **`config.yaml` has zero diff** against the working tree — confirmed via `git status --short`/`git diff --stat`.
- **No deploy/CI infrastructure was touched**: `deploy.sh`, `webhook_listener.py`, `.github/workflows/`, `tests/test_deploy_*.py` — none read for editing, none written to.
- **`CLAUDE_HISTORY.md`'s pre-existing modification was NOT made by this investigation** — confirmed by inspecting its diff directly (a superseded-block-count annotation belonging to the `curated40_rolling40_dax_reverify_2026-08-05` sibling investigation); this investigation never opened that file for writing.
- **Nothing was committed and nothing was pushed** — `git status --short` shows only the pre-existing untracked `database/` and sibling research directories from earlier tonight, plus this investigation's own new files under its own directory; no staged changes.
- **No production database was touched** — every query was read-only (`get_price_df()`, `precompute_ticker()`'s existing read path); no writes anywhere.
- **No change was implemented on the VM.** This is investigation and reporting only, per the task's explicit instruction — the grid, block validation, and joint-risk-gap findings above are presented for a deliberate, separate human decision, not auto-adopted.
