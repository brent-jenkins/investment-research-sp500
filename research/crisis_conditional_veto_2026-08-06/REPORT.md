# Crisis-Conditional Volatility Veto — Research Report (2026-08-06)

**Location of artifacts:** `research/crisis_conditional_veto_2026-08-06/` — `step1_descriptive.py` (script, source of truth), `step1_run.log` (full run log), `trades_with_stress_flag.csv` (all 3,424 trades with the stress flag and both veto variants attached), `stress_conditional_comparison.csv`.

## 0. Framing — testing the narrower pattern on its own, honest terms

The prior investigation (`research/submission_risk_veto_2026-08-06`) rejected a blanket submission-time volatility veto — wrong-direction in aggregate, with the one real, significant exception confined to the 2007-2009 GFC era (n=71, p=0.0005). **This investigation does not assume that finding is real and usable — it tests directly whether it holds under a condition a live system could actually compute in real time**, since "was this the GFC era" is a hindsight label, not something a live system knows.

## 1. Stress indicator — defined explicitly, one reasoned choice, not an open search

```
rolling_60d_high(t) = gspc_close.rolling(60).max()          -- no lookahead, genuinely live-computable
drawdown_pct(t)      = (gspc_close(t) / rolling_60d_high(t) - 1) * 100
STRESS(t)             = drawdown_pct(t) <= -10.0             -- >=10% below the trailing 60-day high
```

**Reasoning**: directly related to the existing crisis override's own S&P-decline logic (`analysis/crisis.py`: a single-day ≥7% drop) but captures a *sustained* stressed period rather than requiring one extreme day — exactly the gap between "the market crashed today" and "the market has been under real stress for a while," which a single-day trigger cannot see by construction. 60 trading days ≈ one calendar quarter, a standard drawdown-measurement window; −10% is the conventional "correction" threshold, distinct from a full bear market (−20%). One pre-specified definition, per the task's own instruction — no threshold-tuning or alternative-indicator search was run.

## 2. Redundancy check against the existing crisis override — genuinely new ground, not redundant

```
Stress-flagged trading days (2007-2026):        480 / 4,925 (9.7%)
Existing crisis-override blocked days:           33 / 4,925 (0.67%; raw trigger fires on 7 days)

Days flagged by BOTH:                            33
Days flagged by stress indicator ONLY:           447 (93.1% of all stress-flagged days)
Days blocked by crisis override but NOT stress:   0
```

**100% of the crisis override's own blocked days are also stress-flagged** (expected by construction — a 7% single-day crash implies the market is already well below its own 60-day high). But **93.1% of the new stress window falls entirely outside what the crisis override already covers** — this is genuinely new ground, not a redundant re-statement of the existing mechanism. The redundancy concern the task raised is answered directly: this is not redundant.

## 3. Candidate-specificity — confirmed structurally, not just by description

```
Trades with next-trading-day inside a stress window:  419 / 3,424 (12.2%)
Blanket veto (stop-distance alone, prior investigation): 343 / 3,424 (10.0%)
Stress-CONDITIONAL veto (stress AND excessive stop distance): 113 / 3,424 (3.3%)

Of the 419 stress-window trades, 306 (73.0%) had a NORMAL stop distance and would NOT be touched.
```

Confirmed directly in the actual computed data, not just in how the mechanism is described: the stress-conditional veto (113 trades) is a genuinely narrower subset of the blanket veto (343 trades) — the large majority of trades placed during a stress period (73.0%) are structurally untouched, because their own stop distance is unremarkable. This is a real candidate-specific veto, not a disguised blanket gate — the distinction the task required be confirmed structurally holds.

## 4. Does the pattern hold under the genuinely computable condition? No.

**Within the stress-flagged subpopulation (n=419) — the actual test this investigation exists to run:**

| Group | n | mean pnl% | median pnl% | win rate |
|---|---|---|---|---|
| Would-be-vetoed (excessive stop distance) | 113 | **+2.40%** | −3.18% | 41.6% |
| Rest (normal stop distance) | 306 | **−0.10%** | −4.61% | 37.3% |

**Mann-Whitney U=18,126.0, p=0.447 — not significant, and the point estimate again points the wrong way** (the would-be-vetoed group has a higher mean and higher win rate). This directly contradicts what the prior investigation's hindsight-labeled GFC-era finding (p=0.0005) would have predicted for a genuinely-computable stress condition.

**Outlier robustness** (trim top/bottom 1% of `pnl_pct` within the stress-flagged population): `n_vetoed=108, mean=+1.72%` vs. `n_rest=301, mean=−0.25%`, **p=0.548 — still not significant, same wrong direction.** The null result is not an artifact of a handful of extreme trades.

**For context, outside stress periods** (n=3,005): vetoed mean +1.26% vs. rest mean +1.07%, **p=0.00068 — "significant," still wrong direction.** This is where the bulk of the prior investigation's own wrong-direction aggregate result actually comes from (87.8% of all trades fall outside the stress window).

## 5. Why the earlier GFC-era finding doesn't survive this test — a direct, evidenced explanation, not speculation

**Era distribution of the stress-flagged population**: 38.7% GFC, 32.5% 2010-2019, 28.9% 2020-2026 — genuinely spread across all three eras, not "the GFC era under another name" (for comparison, the whole trade population is only 13.1% GFC). This confirms the stress indicator is doing real, broad work, not silently collapsing back into an era proxy.

**The most direct explanation, evidenced by the numbers above**: the prior investigation's significant GFC finding (n=71, drawn from trades whose *entry_date* fell in calendar-year 2007-2009) was very likely driven by a small number of extreme, narrow-window days — clustered tightly around September–December 2008, essentially adjacent to the crisis override's own trigger days — not by the broader ~14-month "elevated volatility" concept a sustained-drawdown indicator captures. A 60-day-trailing-drawdown stress flag, by design, stays elevated for extended stretches surrounding those extreme days and pulls in many calmer trades from the same broad window and from two other eras entirely — diluting whatever narrow, extreme-event-specific signal existed. The earlier finding was real for its own narrow slice; it does not generalize to a condition a live system could actually compute and act on continuously.

## 6. Step 1 gate decision: NOT WARRANTED — no Step 2 run

Per the task's own explicit gating rule, and more decisively than the original blanket-veto investigation:

1. **The redundancy check is favorable** (93.1% new ground) and **candidate-specificity is structurally confirmed** — but neither matters if the underlying pattern itself doesn't hold.
2. **The core pattern does not survive the genuinely-computable stress condition**: p=0.447 within the stress-flagged population, wrong direction, and this holds after outlier trimming (p=0.548, still wrong direction).
3. **No sample-size excuse**: n=113 vs. 306 within the stress population is a reasonable size for this comparison, not a thin slice that merely lacks power.

**Because Step 1 does not clear its own gate, Step 2 was not built or run.** No submission-time code was added to `brokers/trading212.py`, no reset-at-boundary sweep, no 16-block validation, and the direct/capacity-turnover effect split required by the standing requirement was not exercised on a mechanism Step 1 itself doesn't support building.

## 7. Recommendation

**Researched, not adopted.** A crisis-conditional volatility veto, using a real-time-computable sustained-drawdown stress indicator combined with the already-established excessive-stop-distance threshold, does not show a real, usable pattern once tested on genuinely computable terms rather than a hindsight era label. The prior investigation's narrower GFC-specific finding appears to reflect a small number of extreme, tightly-clustered days rather than a generalizable "elevated stress → excessive volatility predicts bad outcomes" relationship. `config.yaml`, `analysis/decision.py`, `analysis/crisis.py`, and `brokers/trading212.py` are all unchanged.

**Caveat, stated plainly:** this tests exactly the one pre-specified stress indicator (60-day, −10% trailing drawdown) combined with the one pre-specified stop-distance threshold (90th percentile, 10.95%, reused unchanged from the prior investigation). It does not rule out every conceivable stress definition — a narrower, more extreme threshold (e.g. −20%, or a shorter window) might isolate something closer to the earlier GFC-specific finding, or trailing realized volatility (the task's own suggested alternative) might behave differently — but per the task's own explicit instruction, an open search across stress indicators was not run, and per this project's standing discipline, a rejected proposal is not grounds to keep tuning parameters without a fresh, explicitly-scoped reason to do so.

## 8. Confirmation

- No production files were modified: `analysis/crisis.py`, `analysis/decision.py`, `brokers/trading212.py`, `config.yaml` were all read-only throughout.
- No custom candidate pool or monkeypatched `is_member()` — the population and next-day stop-distance recomputation are reused unchanged from the prior, already-validated investigation; the stress flag is a purely retrospective, read-only S&P 500 price-history computation with no bearing on any trading decision.
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only queries.
