# Momentum Strength vs. 30-Day MAX_HOLDING Cap — Research Report (2026-08-05)

**Location of artifacts:** `research/momentum_horizon_2026-08-05/` — `step1_descriptive.py` (script, source of truth), `step1_run.log` (full run log), `all_trades_full_run.json` (all 3,161 trades from the real backtest run, full trade-level output preserved per this project's reproducibility standard), `max_holding_trades_annotated.csv` (all 1,796 `MAX_HOLDING` trades, one row each, every field described below), `band_summary.csv` (the aggregate table in section 3).

**Scope reminder:** this report covers Step 1 (the descriptive gate) and the Step 1 gate decision only, per the coordinator's correction — no "next steps for further investigation" section, since that discussion is being scoped as a separate follow-up task.

## 1. Environment and methodology confirmation

- Local DB (`database/investment_research.db`) confirmed current before running anything: `price_data` spans 2007-01-01 → 2026-08-03 across 540 tickers (2,454,903 rows), `^GSPC` spans 2007-01-03 → 2026-08-03, `universe_membership` has 1,257 rows. Not inherited from a sibling investigation's earlier check — checked directly, fresh, for this task.
- Code confirmed current: no local modifications pending against `main`, ran directly against the checked-out `analysis/`, `brokers/`, `data/` modules as they exist on disk right now.
- **Backtest convention (stated explicitly, per CLAUDE.md's "Two backtest conventions" standing requirement): Step 1 is a CONTINUOUS WALK.** This is deliberate and correct for Step 1's question — it is not a variant comparison, it is "pull one real, already-fidelity-established trade set and look at what happened next," the same purely-retrospective, no-look-ahead-risk framing the task specified. `analysis.validation.run_full_validation()` is *always* a continuous walk regardless of any flag (confirmed directly from its own docstring and `"backtest_convention": "continuous_walk"` key in its return dict) — Step 2, had it run, would have used reset-at-boundary instead, per the task's own instruction for that step.
- **Engine: `analysis.backtest.simulate_portfolio_compounding_mtm()`**, invoked via `analysis.validation.run_full_validation(CONFIG, start=2007-01-01, end=2026-08-03, mark_to_market=True, live_universe=True)` — the one engine CLAUDE.md confirms genuinely matches live execution (momentum-ranked skip-and-continue admission, mark-to-market capacity tracking, the shared re-entry-cooldown/momentum-floor/price-sanity rules). No custom harness was built for trade generation — the real, unmodified engine was used directly, so no separate decision-engine-parity check was needed for that part (the standing requirement in CLAUDE.md's "Research scripts must verify decision-engine parity" applies to scripts that construct a custom candidate pool or monkeypatch `is_member()`; this script does neither for trade generation).
- **Momentum reconstruction is not a custom harness re-deriving decision logic** — it independently recomputes a *display-only* label (`main.py`'s own momentum-strength bands) directly from the same `analysis.backtest.precompute_ticker()` frames, `analysis.universe.is_member()`, `analysis.decision.passes_entry_gate()`, and `analysis.decision.passes_momentum_floor()` the real engine itself used — not a re-derived approximation of any of those functions. No production trading decision depends on this reconstruction; it exists solely to label already-realized historical trades for this retrospective check.

Real run stats (from `step1_run.log`): `engine_used=compounding_default`, `deployment_basis=mark_to_market`, `total_trades=3161`, `n_tickers_with_data=500/503`. Exit-reason breakdown:

| exit_reason | count |
|---|---|
| MAX_HOLDING | 1796 (56.8%) |
| STOP_LOSS | 1169 |
| SIGNAL_OFF | 131 |
| CRISIS_EXIT | 35 |
| END_OF_DATA | 30 |

This matches the motivating finding almost exactly (56.8% MAX_HOLDING share, as stated in the task brief) — same live universe, same engine, confirming this run reproduces the sibling investigations' own baseline before anything new was computed.

## 2. Exact definitions used (code, not paraphrase)

**Momentum-strength bands — byte-identical to `main.py`'s own functions:**

```python
def _bands(momentum_values: pd.Series) -> dict:
    vals = momentum_values.dropna()
    if len(vals) < 4:
        return {}
    return {"q25": vals.quantile(0.25), "q75": vals.quantile(0.75), "q90": vals.quantile(0.90)}

def _label(momentum_pct, bands: dict) -> str:
    if not bands or pd.isna(momentum_pct):
        return "-"
    if momentum_pct >= bands["q90"]:  return "Very Strong"
    if momentum_pct >= bands["q75"]:  return "Strong"
    if momentum_pct >= bands["q25"]:  return "Moderate"
    return "Weak"
```

**Eligible candidate pool, per entry date** (the population the bands above are computed against — the same population the live app's Candidate Queue page bands against, i.e. everything that would be queued into `pending_orders` that day): every live candidate ticker whose row on that date passes

```python
passes_entry_gate(sig_on=sig_on, is_member=is_member, entry_blocked=entry_blocked)  # analysis/decision.py
and
passes_momentum_floor(momentum, resolve_min_momentum_floor(CONFIG))  # floor = 0.02, production value
```

Re-entry cooldown is **not** applied to the pool definition — `resolve_reentry_cooldown_days(CONFIG)` is 0 in production (the pipeline's own natural floor), a no-op for every already-flat candidate by construction, so this is stated as a scope decision, not an oversight.

**Post-exit measures** (all computed from real, stored `price_data`, purely retrospective): `fwd_ret_{10,20,30}d` = `close[exit_loc+N]/exit_price - 1` using each ticker's own trading-day index; `during_hold_max_price` = max close from entry date to exit date inclusive; `already_peaked_at_exit` = `exit_price < during_hold_max_price * 0.999`; `new_high_post_exit`/`new_high_over_hold_max` = whether the max close in the 30 trading days after exit exceeded the exit price / the during-hold max; `momentum_decaying_at_exit` = whether trailing-252-day momentum at exit was lower than its own value 20 trading days earlier.

## 3. Step 1 results — band summary

| Band | n | % of all MAX_HOLDING | mean momentum at entry | mean pnl_pct at cap | mean fwd_ret_30d | median fwd_ret_30d | % fwd_ret_30d > 0 | % already peaked at exit | % new high post-exit (30d) |
|---|---|---|---|---|---|---|---|---|---|
| Weak | 17 | 0.9% | 3.69% | 8.22% | **5.33%** | 2.05% | 58.8% | 70.6% | 100.0% |
| Moderate | 89 | 5.0% | 19.30% | 9.32% | 1.06% | 1.89% | 53.9% | 80.9% | 88.8% |
| Strong | 111 | 6.2% | 41.12% | 6.86% | 3.43% | 1.89% | 62.2% | 83.8% | 92.8% |
| Very Strong | 1573 | 87.6% | 116.21% | 7.90% | 2.23% | 1.54% | 54.8% | 78.2% | 91.3% |
| "-" (pool <4 candidates that day) | 6 | 0.3% | 20.67% | 2.22% | -3.94% | -5.07% | 33.3% | 83.3% | 50.0% |

(Full per-trade data: `max_holding_trades_annotated.csv`; full aggregate: `band_summary.csv`.)

**First observation — the sample is structurally lopsided.** `Very Strong` alone accounts for 87.6% of every MAX_HOLDING trade this system has ever generated. This is not a labeling artifact but a direct, mechanical consequence of momentum-ranked, capacity-constrained admission (`_admit_group()`'s skip-and-continue): on most days capacity is scarce enough that only the strongest end of that day's pool ever gets admitted at all, so the trades that go the full distance to the 30-day cap are disproportionately top-decile-momentum entries by construction. The three "signal" bands (Weak/Moderate/Strong) combined are 217 trades — 12.1% of the population — and, as shown next, are themselves badly regime-confounded.

**Second observation — no monotonic gradient.** Mean `fwd_ret_30d` does not rise or fall consistently with band strength (5.33% → 1.06% → 3.43% → 2.23%, Weak→Moderate→Strong→Very Strong) — it bounces. Median `fwd_ret_30d` is essentially flat across all four real bands (2.05%, 1.89%, 1.89%, 1.54%) — genuinely no discernible gradient once outliers are set aside. `% already peaked at exit` and `% new high post-exit` show the same non-monotonic bouncing. None of this looks like a genuine, structural momentum-strength effect on post-cap continuation.

## 4. Named-trade-level evidence — the Weak band in full (n=17)

Per the task's own required rigor (matching the "Early-stage-winner exclusion check" style), every single Weak-band trade, not a sample:

| Ticker | Entry date | Exit date | Momentum at entry | pnl_pct at cap | fwd_ret_30d | Already peaked at exit |
|---|---|---|---|---|---|---|
| PSA | 2008-07-16 | 2008-08-15 | 4.75% | +8.32% | +4.59% | No |
| GIS | 2008-12-10 | 2009-01-09 | 2.49% | −4.64% | −4.52% | Yes |
| GILD | 2009-01-20 | 2009-02-19 | 2.23% | +3.90% | −8.85% | Yes |
| DGX | 2009-03-25 | 2009-04-24 | 3.79% | +7.40% | +2.05% | Yes |
| DGX | 2009-05-26 | 2009-06-25 | 2.58% | +8.23% | −4.65% | No |
| SHW | 2009-06-04 | 2009-07-06 | 3.50% | −0.13% | +11.71% | Yes |
| CLX | 2009-06-11 | 2009-07-13 | 2.50% | +6.93% | +1.95% | No |
| HSY | 2009-06-22 | 2009-07-22 | 2.81% | +12.25% | −1.23% | No |
| CL | 2009-06-24 | 2009-07-24 | 2.56% | +7.00% | −5.35% | No |
| MU | 2009-07-16 | 2009-08-17 | 4.36% | +14.26% | **+27.85%** | Yes |
| TRV | 2009-08-06 | 2009-09-08 | 5.22% | +4.68% | −1.04% | Yes |
| LEN | 2009-08-10 | 2009-09-09 | 3.64% | +21.68% | −12.31% | Yes |
| CHRW | 2009-09-01 | 2009-10-01 | 6.40% | +1.39% | +2.76% | Yes |
| FITB | 2009-11-27 | 2009-12-28 | 3.13% | +4.18% | +17.25% | Yes |
| WDC | 2019-11-27 | 2019-12-27 | 3.55% | +23.25% | +10.93% | Yes |
| TPR | 2020-11-18 | 2020-12-18 | 3.82% | +3.15% | +19.89% | Yes |
| NCLH | 2024-09-04 | 2024-10-04 | 5.43% | +17.93% | **+29.57%** | Yes |

**The confound this table exposes directly: 14 of 17 Weak-band trades (82.4%) entered inside a single 17-month window, 2008-07 to 2009-11 — the GFC-recovery era.** Restricted to that regime, mean `fwd_ret_30d` is +2.16%, materially lower than the headline +5.33% for the full band — the band's apparent "best-performing" figure is almost entirely a small handful of extreme outliers (MU +27.85%, NCLH +29.57%, both individually large enough to swing a 17-trade mean substantially) sitting inside a market environment that was, independent of any individual stock's momentum reading, in the middle of one of the largest rallies in market history. Regime concentration by band, computed directly:

| Band | n | Entries before 2010-01-01 | % pre-2010 |
|---|---|---|---|
| Weak | 17 | 14 | **82.4%** |
| Moderate | 89 | 38 | 42.7% |
| Strong | 111 | 31 | 27.9% |
| Very Strong | 1573 | 71 | **4.5%** |

This is exactly the "naive co-occurrence looks like a real effect, but is actually a regime artifact" trap this project's own culture (see CLAUDE_HISTORY.md's concentration-risk investigation, same night) is built to catch — the thin, "interesting-looking" bands are disproportionately drawn from one historical regime the dominant band is almost entirely absent from, so a band-level comparison conflates "entry momentum strength" with "which market era this trade happened to fall in."

## 5. Statistical check — does momentum at entry predict post-cap continuation at all?

Rather than rely on the coarse 4-band split alone, checked the continuous relationship directly:

```
Spearman(momentum_at_entry, fwd_ret_30d), all bands:        rho = 0.0165, p = 0.4863, n = 1782
Spearman(momentum_at_entry, fwd_ret_30d), Very Strong only:  rho = 0.0279, p = 0.2707, n = 1559
Spearman(momentum_at_entry, already_peaked_at_exit):         rho = -0.0028, p = 0.9054, n = 1796
```

OLS controlling for regime (`era_pre2010` = 1 if entry_date < 2010-01-01):

```
fwd_ret_30d = 0.0202 + 0.0046*momentum_at_entry - 0.0264*era_pre2010
              momentum_at_entry: coef=0.0046, p=0.094 (not significant at 0.05)
              era_pre2010:       coef=-0.0264, p=0.024 (significant)
              R² = 0.005
```

**This is decisive, not marginal.** Entry momentum shows essentially zero correlation with post-cap continuation across the full population (rho≈0.02, p=0.49) *and* within the dominant Very Strong band alone (rho≈0.03, p=0.27, n=1559 — a large enough sample that a real, even modest effect would show up here if it existed). The regression's own R² of 0.005 means momentum at entry explains essentially none of the variance in what happens after the cap. What weak signal exists in the data belongs to *era*, not momentum strength, and even that effect is small.

## 6. Additional sanity check — winners and losers are not band-separated

Top 8 and bottom 8 trades by `fwd_ret_30d` across the whole population, irrespective of band:

**Biggest post-cap continuation:** MU (2026-03), PLTR (2024-10, 2024-12), STX (2026-03), AMD (2020-05), WDC (2025-12), SNDK (2026-03, 2026-02) — **all seven Very Strong band**, momentum at entry ranging 1.01 to 15.4 (101%–1540%).

**Biggest post-cap reversal:** SMCI (2024-09, 2024-06), TDG (2020-01), WDC (2020-01), AAPL (2008-08, Moderate band), TMUS (2011-06), ALGN (2018-08), BBY (2013-11) — again almost entirely Very Strong.

The same band (and in several cases the same tickers — SNDK/WDC/STX/MU, the semiconductor/storage cluster already documented in the same-night concentration-risk investigation) produces both the largest continuations and the largest reversals. There is no directional tilt visible: strong entry momentum precedes both outcomes with roughly equal frequency, exactly consistent with the near-zero correlation in Section 5.

## 7. Step 1 gate decision: NOT WARRANTED — no Step 2 run

Per the task's own explicit gating rule ("If Step 1 does not show a real, promising, and reasonably-sized pattern... STOP HERE"), stated honestly per this project's standing culture where "no meaningful difference found" is a fully valid outcome:

**Step 1 does not show a real, usable signal.** Specifically:

1. No monotonic relationship between entry-time momentum-strength band and any post-cap continuation measure (mean/median forward return, % positive, % already-peaked, % new-high-post-exit) — the figures bounce non-monotonically across bands.
2. The three non-dominant bands (Weak/Moderate/Strong, 217 trades / 12.1% of the population) are the only bands showing any apparent divergence from the aggregate, and are themselves confounded by market regime nearly 3:1 relative to the dominant band (82.4% pre-2010 concentration in Weak vs. 4.5% in Very Strong) — an apparent band effect cannot be cleanly separated from an era effect on this data.
3. The headline-looking Weak-band figure (+5.33% mean 30-day forward return) collapses to +2.16% once restricted to its own dominant regime and is materially driven by two individual outlier trades (MU +27.85%, NCLH +29.57%) in a 17-trade sample — not a robust population-level effect.
4. Direct statistical tests (Spearman correlation, both across the whole population and within the dominant Very Strong band alone; OLS with an explicit regime control) all show essentially zero relationship between entry momentum and post-cap continuation (rho ≈ 0.02–0.03, none statistically significant; regression R² = 0.005).
5. The dominant band (Very Strong, 87.6% of all MAX_HOLDING trades) shows continuation statistics nearly identical to the aggregate population — meaning even a well-supported band-level rule would only ever apply meaningfully to a small residual (12.4%) of trades, most of which sit in one confounded historical window.

**Because Step 1 does not clear its own gate, Step 2 was not built or run.** No engine modification was made, no reset-at-boundary sweep was run, no 16-block validation was performed, and the max_hold/VaR-sizing coupling described in the task brief was not exercised — there was nothing here worth the cost of that additional work. This is consistent with, not a shortcut around, the task's own instruction: "Do not build a backtest engine modification or run block validation on a hypothesis Step 1 itself doesn't support."

## 8. Recommendation

**Researched, not adopted.** A fixed 30-day `stock_max_holding_days` cap for every live candidate, independent of entry-time momentum strength, remains the best-supported choice given this evidence — there is no demonstrated relationship between how strong a position's momentum was at entry and whether it was "still genuinely worth holding" past the 30-day cap. `config.yaml`, `analysis/decision.py`, `analysis/backtest.py`, and `brokers/trading212.py` are all unchanged.

**Caveat, stated plainly rather than hidden:** this only tests the specific, coarse Weak/Moderate/Strong/Very Strong percentile labeling scheme against post-cap price continuation, momentum trend, and peak/new-high behavior. It does not rule out every conceivable momentum-related refinement to the holding-period rule — a genuinely different framing or feature set was raised in a separate discussion and is being scoped as its own follow-up task rather than folded into this report, per the coordinator's direction.

## 9. Confirmation

- No production files were modified: `git status --short` shows only the new `research/momentum_horizon_2026-08-05/` directory and the pre-existing, already-untracked `database/` directory (present before this task began, not created by it).
- `config.yaml` has zero diff against the working tree.
- No deploy/CI infrastructure was touched (`deploy.sh`, `webhook_listener.py`, `.github/workflows/`, `tests/test_deploy_*.py`, `CLAUDE.md`, `CLAUDE_HISTORY.md` — none read for editing, none written to).
- Nothing was committed and nothing was pushed — `git status` shows no staged changes.
- No production database was touched — the local `database/investment_research.db` was read-only for every query in this investigation.
