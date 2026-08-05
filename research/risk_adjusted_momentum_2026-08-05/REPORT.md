# Risk-adjusted momentum ranking — researched, not adopted (2026-08-05)

**Question.** Does ranking admission candidates by momentum ÷ trailing
volatility, instead of raw momentum, rank better than raw momentum alone?

**Answer: no. Researched, not adopted. `config.yaml` untouched, nothing
deployed, no production code changed.**

Directly motivated by the concentration-risk investigation (same day): the
worst historical episodes were dominated by clusters of extreme-momentum,
highly-correlated names (the recurring semiconductor/storage cluster — SNDK,
WDC, MU, LITE, STX and others). An explicit correlation-aware *constraint*
was tested there and rejected. This tested a different mechanism for the same
concern — does risk-adjusted ranking naturally deprioritize the high-momentum/
high-volatility profile, without any explicit correlation rule?

It does deprioritize it, measurably. It also costs materially more return
than the risk reduction is worth, and — despite being a Sharpe-like ranking —
produces a **worse Sharpe ratio** than the raw-momentum baseline it replaces.

---

## What production ranks by today (traced, not assumed)

`analysis/backtest.py:_admit_group()`, line 1334 (and its identical twin in
`simulate_portfolio_compounding()`):

```python
candidates.append((ticker, momentum_val if momentum_val is not None else float("-inf")))
candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))
```

Raw momentum descending, tie-broken by insertion order. Live's own equivalent
is `brokers/trading212.py:1543`: `ORDER BY momentum_pct DESC, decided_at ASC`
— the reviewed, deliberate SQL/Python duplication CLAUDE.md already records.

Candidates reach the sort only after `passes_entry_gate()`,
`passes_momentum_floor()` and `is_reentry_blocked()`; the sort then feeds a
skip-and-continue loop where a candidate too large for remaining capacity is
skipped without blocking lower-ranked ones.

**Structural note that shaped the implementation:** volatility is computed
*after* the sort, not before it. `log_returns` and `_var_position_size_core()`
run at lines 1350–1356, inside the admission loop, once ranking is already
decided. Risk-adjusted ranking is therefore not a one-line sort-key change —
it requires computing volatility for every gate-passing candidate, including
ones today's ordering would never reach.

## The exact variant tested

One specific, reasoned hypothesis — not a search across risk-adjustment
formulas. The volatility measure is the one production VaR sizing already
computes (`analysis/volatility.py:_var_position_size_core()` line 109), not a
new invention:

```python
prior_closes = full_close_series[full_close_series.index < dt].tail(var_lookback + 1)
log_returns  = log(prior_closes / prior_closes.shift(1)).dropna()
if len(log_returns) >= _MIN_VAR_RETURNS:          # _MIN_VAR_RETURNS == 60
    daily_var = float(np.percentile(log_returns, (1 - var_confidence) * 100))
    score = momentum / abs(daily_var)   if daily_var != 0 else float("-inf")
else:
    score = float("-inf")
candidates.sort(key=lambda c: (-score, per_ticker[c[0]]["idx"]))
```

`var_lookback = 252`, `var_confidence = 0.95` (both from config), so
`daily_var` is the 5th percentile of the trailing 252-day log-return
distribution. The isolated change is **the sort key and nothing else** — not
entry gating, not exits, not sizing, not the capacity/cash clamps.

## Fidelity and parity verification

`self_check.py`, re-run after every engine change:

| Check | Result |
|---|---|
| Byte-identical to real `simulate_portfolio_compounding_mtm()` when disabled | **True** (2,852 trades, exact match) |
| Holding-period violations vs shared `get_max_holding_days()` | 0 / 2,852 |
| Exit reasons within shared `evaluate_exit_reason()` vocabulary | True |
| Shared exit priority chain | `CRISIS_EXIT → STOP_LOSS → SIGNAL_OFF → MAX_HOLDING → None` |
| Sizing above `per_trade_pct` ceiling | 0 |
| Variant actually changes output (not inert) | True (2,846 vs 2,852) |

Per CLAUDE.md's "Research scripts must verify decision-engine parity" rule,
parity is evidenced from this script's own run — real trades recomputed
against the shared functions — not inferred from production being consolidated.

The engine is a copy of `research/concentration_risk_2026-08-05/
concentration_constraint_engine.py` (itself already proven byte-identical to
the real engine), with its correlation-haircut parameters carried over unused
and always `None`, specifically to inherit that proof rather than re-derive it.

**Independent cross-check of the whole harness:** the raw-momentum baseline
reproduced the previously-published figures exactly — reset-at-boundary test
period **CAGR 19.29%, max DD -27.56%**, and **14/16 blocks passing with blocks
0 and 1 failing**, matching CLAUDE_HISTORY's canonical numbers.

## A real methodological artifact, found and fixed mid-investigation

The first block-validation run had the insufficient-history ranking fallback
bind on **18.08%** of scored candidates (174,776 / 966,417). Cause: block
truncation cuts `close_series` to `[block_start, block_end]`, so for the first
~60 sessions of *every* block no candidate has 60 prior returns — all scored
`-inf` and were ranked by insertion order, i.e. arbitrarily. This handicapped
the variant arm only; the raw arm still ranked by real momentum throughout.

Fixed by estimating the ranking volatility from the ticker's **untruncated**
close history (a live system genuinely has that history; the truncation is a
harness device for making blocks independent in trades and equity, not a claim
the ticker had no past). Sizing, gating, exits and events all still use the
truncated series, unchanged. Fallback incidence after the fix: **0.0000%**
(0 / 965,857). Byte-identical-when-disabled re-verified after the change.

**This mattered — it was not a cosmetic correction.** Most of the variant's
headline drawdown improvement was the artifact, not the hypothesis:

| Reset-at-boundary, test period | baseline | variant (artifact) | variant (corrected) |
|---|---|---|---|
| Max DD (fixed) | -27.56% | -16.31% | **-26.19%** |
| Profit factor | 1.74 | 1.78 (better) | **1.71 (worse)** |

The apparent 41% drawdown reduction was really ~5%, and the PF "win" reversed.
Anyone reading only the first run would have concluded the opposite.

## Reset-at-boundary comparison (corrected)

Convention: **reset-at-boundary** — this is a variant comparison, so per
CLAUDE.md's "Two backtest conventions" this is the correct convention, not
continuous walk. Test period 2020-01-01 → 2026-08-03, live universe (503
candidates), both arms from one identical set of precomputed caches.

| Metric | raw momentum (baseline) | risk-adjusted | verdict |
|---|---|---|---|
| Trades | 1,270 | 1,202 | — |
| CAGR | **19.29%** | 16.35% | **worse (-2.94pp)** |
| Max DD (fixed) | -27.56% | **-26.19%** | marginally better |
| Max DD (peak-relative) | -12.47% | **-8.89%** | better |
| Profit factor | **1.74** | 1.71 | worse |
| Sharpe | **0.670** | 0.635 | worse |

Does not dominate. Better on drawdown only; worse on CAGR, PF and Sharpe.

## 16-block validation

Run anyway, despite the non-dominant single-window result, because the
drawdown reduction was large enough to be worth testing for robustness and
because block validation is the check that actually decides. Formula imported
directly from `analysis/validation.py:_block_passes()`, quoted verbatim:

```python
dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
return bool(dd_ok and pf_ok)
```

| | raw momentum | risk-adjusted |
|---|---|---|
| Blocks passing | **14/16** | **14/16** (same failures: 0 pre-GFC, 1 GFC-acute) |
| Mean CAGR | **12.95%** | 9.24% |
| Blocks won on CAGR | **12/16** | 4/16 |
| Mean abs drawdown | 8.27% | **7.22%** (better in 12/16) |
| Mean profit factor | **1.779** | 1.680 (better in only 6/16) |
| Mean Sharpe | **0.539** | 0.465 |

The drawdown improvement **is** robust — better in 12 of 16 independent
windows. But it costs 3.71pp of mean CAGR, losing 12 of 16 blocks, and both
profit factor and Sharpe are worse across blocks. The single-window PF edge
(1.78 vs 1.74, pre-correction) did not survive: across blocks it inverts to
1.680 vs 1.779.

## Does it reduce the concentration/joint-risk gap?

Yes — measurably, in the hypothesised direction, but modestly. `indep_gap_60d`
formula reused verbatim from the concentration investigation
(`p_real/p_ind - 1`, 60-day window, MIN_OBS 35):

| Measure (days with ≥2 open, n=1,607) | raw | risk-adjusted | change |
|---|---|---|---|
| `indep_gap_60d` mean | 0.8986 | 0.8472 | **-5.72%** |
| `indep_gap_60d` median | 0.8837 | 0.8269 | -6.43% |
| `indep_gap_60d` p95 | 1.4186 | 1.3982 | -1.44% |
| Mean pairwise corr (60d) | 0.3843 | 0.3647 | -0.0196 |
| Mean simultaneous positions | 12.23 | 11.57 | -0.66 |

Paired day-by-day (same calendar, both arms): mean difference **-0.0514**,
median -0.0499, variant lower on **58.2%** of days.

So the mechanism works as theorised — ranking by risk-adjusted momentum does
deprioritize the high-momentum/high-volatility profile and does reduce
realized joint risk without any explicit correlation rule. But it moves the
gap from ~1.90x to ~1.85x of what independent sizing assumes. It does not
meaningfully close the ~2x gap the concentration investigation identified.

**Do not over-read the 58.2%.** A naive sign test would call that highly
significant, but these are overlapping 60-day windows on consecutive days —
massively autocorrelated, so the effective sample is far smaller than 1,607
and no p-value is quoted here.

## Recommendation

**Researched, not adopted.** Not remotely close to the "clearly, exceptionally
strong improvement" bar the brief set for implementation. It is worse on the
primary return metric in 12 of 16 independent windows, worse on profit factor
and Sharpe both single-window and across blocks, and buys a real but modest
drawdown reduction that raw momentum's higher return more than compensates for.

The irony is worth recording: a Sharpe-like ranking criterion produces a
**worse realized Sharpe ratio** than ranking on raw momentum. Dividing by
trailing volatility systematically demotes exactly the high-volatility,
high-momentum names that generate this strategy's returns — the risk it
removes and the return it removes come from the same source.

Worth noting it does beat the *rejected* correlation constraint from the
sibling investigation on both axes (CAGR 16.35% vs 16.27%, peak-relative DD
-8.89% vs -18.90%). Neither is adoptable; if the concentration concern is ever
revisited, this is the better of the two mechanisms tried so far.

## Universe caveat — effective 501, not 503

Every run above was executed **before** commit `d1a067a` ("Fix silent
BRK.B/BF.B ticker bug…") landed. At the time, `BRK.B` and `BF.B` had no price
data locally — yfinance needs hyphen notation (`BRK-B`) and the codebase
passed the dot form straight through — so both were silently absent from the
candidate pool. The effective universe was **501**, not 503, for both arms
equally.

This is stated rather than quietly ignored, but it does not change the
conclusion: the variant loses by 3.71pp of mean CAGR across 12 of 16
independent windows, a margin two mega-cap constituents cannot plausibly
reverse. It applies symmetrically to baseline and variant, and the baseline
still reproduced the published figures exactly (19.29% / -27.56% / 14-16
blocks), which suggests those published figures were themselves generated
without these two tickers — production had them frozen at 2026-07-31 per
`d1a067a`'s own commit message.

**Confirmation re-run completed** on the now-complete 503-ticker universe
(after `d1a067a` was merged and both tickers backfilled — 4,927 rows each,
exactly the NYSE session count). Recorded in `results/confirm_503/`:

| 16-block mean | 501: raw | 501: risk-adj | 503: raw | 503: risk-adj |
|---|---|---|---|---|
| Blocks passing | 14/16 | 14/16 | 14/16 | 14/16 |
| CAGR | 12.95% | 9.24% | **12.95%** | **9.20%** |
| abs drawdown | 8.27% | 7.22% | 8.27% | 7.21% |
| Profit factor | 1.779 | 1.680 | 1.779 | 1.672 |
| Sharpe | 0.539 | 0.465 | 0.539 | 0.461 |
| CAGR blocks won by variant | — | 4/16 | — | 4/16 |

The verdict is unchanged and marginally *worse* for the variant. The raw arm's
aggregates are identical to three decimals, indicating neither `BRK.B` nor
`BF.B` was ever admitted under raw-momentum ranking — unsurprising for two
comparatively low-momentum, low-volatility names. The caveat is therefore
closed, not merely acknowledged.

## Preserved output

Per CLAUDE.md's reproducibility standard (exact formulas inline above, and
full trade-level output, not just aggregates):

- `risk_adjusted_engine.py` — the variant engine, isolated change marked inline
- `self_check.py` — fidelity + decision-engine-parity evidence
- `reset_sweep_worker.py`, `block_validate_worker.py`, `joint_risk_compare.py`
- `results/reset_raw.json`, `results/reset_ra_v2.json` — full trade lists
- `results/blocks_raw_trades.json`, `results/blocks_ra_v2_trades.json` — per-block trades
- `results/joint_risk_v2.json`, `results/daily_panel_*.csv` — day-level panels
- `results/reset_ra.json`, `results/blocks_ra.json` — the **pre-correction**
  artifact runs, kept deliberately so the artifact's effect stays auditable
