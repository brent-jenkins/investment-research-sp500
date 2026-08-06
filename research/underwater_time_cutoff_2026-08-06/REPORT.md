# Underwater Time-Cutoff (Day-15) — Research Report (2026-08-06)

**Location of artifacts:** `research/underwater_time_cutoff_2026-08-06/` — `step1_descriptive.py` (script, source of truth), `step1_run.log` (full run log), `day15_checkpoint_trades.csv` (all 2,318 trades that reached the day-15 checkpoint, one row each), `check1_recovery_rate.csv`, `net_pnl_impact.csv`, `slow_bleed_284_stop_proximity.csv`, `check2_stop_proximity_summary.csv`, `regime_decomposition.csv`.

## 0. Framing — an external, no-system-context claim, treated as an unverified hypothesis

The motivating claim — a day-15 underwater cutoff "cuts dead money without touching the winners," worth ~£127k — came from a review with no access to this system's real mechanics. Per the task's explicit instruction, **neither part of that claim was assumed true**; both were checked directly against the real trade population and real daily price data.

## 1. Environment re-verification

`git status`, latest commit, and `price_data` coverage all re-confirmed unchanged since the enriched export ran earlier this session. Trade population reused from that export's own direct, already-fidelity-verified engine fields (ticker, entry_date, exit_date, entry_price, exit_price, pnl_pct, pnl_gbp, exit_reason, holding_days, stop_distance_pct, position_gbp_at_entry) — no re-run of the backtest needed. **Fresh, independent data**: each trade's own daily closing-price path from entry to the day-15 checkpoint, built directly from stored `price_data` via `analysis.backtest.precompute_ticker()` (the real, unmodified production function), for all 331 unique traded tickers.

Baseline reproduction confirmed exact before anything else: 3,161 trades, 1,796 MAX_HOLDING (56.8%).

**First discrepancy, reported honestly**: the motivating claim's own headline count — "284 trades sat 20+ days before eventually being stopped out" — reproduces here as **314** (`STOP_LOSS` trades with `holding_days >= 20`), about 10.6% higher than cited. Not reconciled further (the external review's exact methodology isn't available to compare against), but flagged plainly per this project's standing culture of stating exact figures rather than assuming an external number is correct.

## 2. Methodology

`THRESHOLD_DAY = 15` calendar days, matching `holding_days`' own definition in the real engine (`(dt - entry_date).days`). For every trade with `holding_days >= 15` (i.e. genuinely still open, under the real system, when day 15 arrived — a trade that resolved earlier never gives this rule a chance to fire, and is excluded), the "day-15 checkpoint" is the first trading day where the calendar gap from entry reaches 15; that day's close price determines whether the position was actually underwater at that point. `stop_price = entry_price × (1 − stop_distance_pct/100)` — the exact inverse of `analysis/volatility.py:_resolve_entry_stop()`'s own formula, using each trade's real, already-computed `stop_distance_pct`.

**2,318 of 3,161 trades reached the day-15 checkpoint** (843 resolved before day 15 — mostly fast `STOP_LOSS`/`SIGNAL_OFF` trades this rule could never touch). Of those 2,318: **690 (29.8%) were genuinely underwater at day 15** — this is the exact population the proposed rule would act on — and 1,628 (70.2%) were already at or above breakeven, unaffected either way.

## 3. CHECK 1 — does the rule actually spare winners? **No — the claim is false, and by a substantial margin.**

Real, eventual outcome of the 690 underwater-at-day-15 trades, under the actual system:

| Outcome | n | % of underwater-at-day15 |
|---|---|---|
| **Eventually WIN anyway** | **216** | **31.3%** |
| Eventually lose anyway | 474 | 68.7% |

Every single one of the 216 recoveries came via `MAX_HOLDING` (not `SIGNAL_OFF`) — real positions that were underwater at day 15 and went on to survive all the way to the 30-day cap as winners. **Nearly one in three of the positions this rule would force-exit are real future winners under the current system.** The "without touching the winners" framing does not hold.

Full breakdown of the 474 eventual losers, for completeness: `STOP_LOSS` 329, `MAX_HOLDING` (still negative at cap) 130, `SIGNAL_OFF` 12, `END_OF_DATA` 2, `CRISIS_EXIT` 1.

## 4. The decisive test — NET £ impact of the rule as actually proposed

Counting recoveries is necessary but not sufficient — the real question is whether the £ saved by cutting the 474 real losers early outweighs the £ cost of cutting the 216 real winners early. Computed directly, trade by trade: `delta_gbp = (position_gbp_at_entry × day15_pnl_pct) − real_pnl_gbp` (positive = the day-15 cutoff would have been better than what actually happened).

```
Total NET delta across all 690 underwater-at-day15 trades:  -£41,365.17
  Cost inflicted on the 216 eventual winners (cut early):  -£110,353.26
  Benefit from the 474 eventual losers (cut earlier):       +£68,988.09
```

**The rule, applied as literally proposed to every underwater-at-day-15 position, would have made the system NET WORSE by £41,365 over the full 19-year history** — the harm to real future winners (£110k in forgone gains) more than outweighs the benefit of cutting real future losers early (£69k saved). This is not a marginal or ambiguous result; the two components differ by more than 1.5×.

## 5. CHECK 2 — is this mostly redundant with the existing stop-loss, just slower?

For the 314 slow-bleed trades specifically (`STOP_LOSS`, `holding_days >= 20`), stop-proximity at their own day-15 checkpoint:

```
stop_proximity_pct_at_day15 (% of room remaining above the stop level at day 15):
  mean=7.12%  median=5.98%  25th pct=3.52%  75th pct=9.07%  max=28.59%

Already within 5% of their own stop at day 15:  125/314 (39.8%)
Already within 10% of their own stop at day 15: 249/314 (79.3%)

Days from the day-15 checkpoint to the REAL eventual STOP_LOSS exit:
  mean=9.35 days  median=9 days  25th pct=7 days  75th pct=13 days
```

**Confirmed: mostly redundant, not a new protection.** The large majority (79.3%) of this specific slow-bleed population was already within 10% of its own existing ATR-based stop by day 15, and the existing mechanism would have caught them anyway — a median of just 9 more days later. The genuine incremental value of a new rule, for this specific population, is "catch it about 9 days sooner," not "catch something the existing system misses."

**Reconciling the cited ~£127k figure.** Computed narrowly — the incremental £ lost *between* the day-15 checkpoint and the real eventual `STOP_LOSS` exit, for the slow-bleed population only (excluding every other underwater trade): **£139,096.32** — in the right ballpark of the cited £127k (the gap plausibly tracks the 314-vs-284 population-count discrepancy noted in Section 1). **This reconciliation itself exposes the flaw in the original claim**: £127k-ish is a real number, but it is the *gross* saving for one narrow subgroup only — it silently excludes the £110,353 cost the *same* rule inflicts on real winners elsewhere in the underwater population (Section 4). The headline figure was never actually netted against its own side effect.

## 6. Regime confounding — checked explicitly, same standard as every investigation tonight

| Era | n underwater | recover-to-win % | net £ delta |
|---|---|---|---|
| 2007-2009 GFC | 90 | 20.0% | **+£1,413.86** (near breakeven) |
| 2010-2019 bull (dominant) | 349 | **34.4%** | **−£11,222.02** |
| 2020-2026 recent | 251 | 31.1% | **−£31,557.01** |

**No dominant-regime rescue story here, unlike the MA-extension investigation.** The rule is net-negative in both of the two largest, most relevant eras (2010-2019 and 2020-2026, 600 of 690 underwater trades combined), and only marginally non-negative — not genuinely positive — in the smallest, most atypical GFC window. If anything, the dominant 2010-2019 era shows the *highest* recovery rate (34.4%), making the "spares winners" problem worse, not better, exactly where it matters most.

## 7. Step 1 gate decision: NOT WARRANTED — no Step 2 run

Per the task's own explicit gating rule, and unusually decisively for tonight's session — most other rejections tonight required several layers of checking (outlier trimming, within-band decomposition) before the picture became clear; here the very first, most direct calculation (net £ impact on the exact proposed rule) already settles it:

1. **The "without touching the winners" claim is directly false**: 31.3% of the population this rule targets are real future winners under the actual system, all via `MAX_HOLDING`.
2. **The rule's own net £ impact, computed honestly (benefit to real losers minus cost to real winners), is negative**: −£41,365 over 19 years, not the positive figure the original framing implied.
3. **The specific slow-bleed population the £127k figure was built from is mostly redundant with the existing ATR stop-loss**: 79.3% were already within 10% of their own stop by day 15, and the real incremental benefit is "catch it ~9 days sooner," not new protection — and even that narrower, real ~£139k gross figure was never netted against the £110k it costs elsewhere.
4. **No regime rescues this**: net-negative in both eras that matter (2010-2019, 2020-2026), barely non-negative in the smallest, most atypical one.

**Because Step 1 does not clear its own gate, Step 2 was not built or run.** No exit-priority-chain modification was made, no reset-at-boundary sweep was run, no 16-block validation was performed.

## 8. Recommendation

**Researched, not adopted.** A blanket day-15 underwater cutoff, exactly as proposed, would have made this system's historical results worse, not better, once properly netted against its cost to real future winners — the opposite of the claimed benefit. `config.yaml`, `analysis/decision.py`, `analysis/backtest.py`, and `brokers/trading212.py` are all unchanged.

**Caveat, stated plainly:** this tests exactly the proposed rule (blanket exit for any position underwater at day 15, no further conditions) at exactly the proposed threshold (15 days). It does not rule out every conceivable refinement — a later threshold, a rule that also requires proximity to the existing stop (which Section 5 shows would target a population close to redundant with the current mechanism anyway), or a rule combined with a momentum-decay signal could behave differently. None of those were in scope here, and per this project's standing discipline, a rejected proposal is not grounds to keep tuning parameters until something clears the bar without a fresh, explicitly-scoped reason to do so.

## 9. Confirmation

- No production files were modified: `analysis/backtest.py`, `analysis/decision.py`, `analysis/volatility.py`, `brokers/trading212.py`, `config.yaml` were all read-only throughout.
- No custom candidate pool or monkeypatched `is_member()` — trade generation reused already-verified engine fields; the day-15 checkpoint computation is a purely retrospective, read-only price-history lookup with no bearing on any trading decision.
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only queries.
