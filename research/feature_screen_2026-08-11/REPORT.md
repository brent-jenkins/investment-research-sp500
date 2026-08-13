# Entry-time feature correlation screen — results (2026-08-11)

**Read-only. No arms, no engine, no adoption, no fix proposed, no study designed.**
Pre-registration (`PREREGISTRATION.md`) was written and committed (`37a6d48`) **before any feature
was computed**; the thresholds below were applied mechanically.

---

## Verdict

**16 features screened. 14 NULL. 2 clear the screening floor, both INCONCLUSIVE. Nothing is WORTH A
PROPER STUDY.**

**This is a successful screen, not a failed one.** It is the fourth independent investigation of
entry-time information in this system, and it agrees with the previous three: **entry information
does not predict individual trade outcome**, consistent with momentum working distributionally rather
than by identifying which specific trades will succeed.

**The single most useful result is not a correlation but a mechanism**: the one feature that cleared
the floor most convincingly, `atr_pct`, turns out **not to be a new feature at all**. It is
`stop_distance_pct / 200` — correlation **1.000000**, ratio 200.0000 with sd 0.001 — i.e. it is
already a first-class production input, setting both the ATR stop and (via VaR) the position size.
Its negative rank correlation with `pnl_pct` is substantially **mechanical**, and its relationship to
the *mean* runs the opposite way to its relationship to the *rank*. Details in §4.

---

## 1. Method

**Primary**: `research/relative_ranking_prereg_2026-08-09/trades_pool2_clean_B.csv` — 4,876 trades,
the currently-live relative-rank ordering. **Reported alongside**: `trades_pool2_clean_A.csv` — 4,919
trades, absolute momentum. Both used exactly as preserved; **no backtest re-run**.

**Split, declared before computing**: screening on **pre-2022** trades (**3,602** primary /
**3,635** baseline); **2022-01-01 onward held out** (**1,274** / **1,284**) and not examined until a
feature had already cleared the floor.

**Anchoring verified**: `entry_price` equals the stored `price_data` close on the entry date for
**4,876/4,876** and **4,919/4,919** trades. **Feature coverage: 100% non-null on all 16 features**,
both trade sets.

---

## 2. The RSI degeneracy pre-check — the expected problem did not occur

Pre-registered: if RSI-at-entry had interquartile range < 15 points, or >80% of trades inside a
20-point band, it would be declared **uninformative by construction** and no correlation interpreted.

| | p25 | p50 | p75 | IQR | largest 20-pt band |
|---|---|---|---|---|---|
| Primary (B) | 40.47 | 49.90 | 59.27 | **18.80** | 53.3% |
| Baseline (A) | 45.33 | 54.95 | 64.14 | **18.81** | 53.2% |

**Neither trigger fired, so RSI was screened normally.** The anticipated problem — that names
selected for large trailing gains would nearly all read overbought — **did not materialise**: median
RSI at entry is **49.9**, essentially neutral, with a genuine ~19-point interquartile spread.

**This is consistent with the stale-peak diagnostic rather than surprising**: 92.2% of admitted
positions enter *below* their trailing-window peak, at a median 8.6% below. The system buys into
pullbacks within uptrends far more often than at highs, so entry RSI sits near the middle of its
range. Recorded because the prediction was explicit and wrong, and the reason it was wrong is
informative.

RSI's own correlation is then **−0.008** (primary) — comfortably NULL.

---

## 3. The screening table (pre-2022)

Primary trade set (B, relative rank), n = 3,602. `p(sp)` is reported for completeness and is **not**
the decision rule (§3 of the pre-registration).

| feature | p10 | p50 | p90 | **Spearman** | Pearson | p(sp) | verdict |
|---|---|---|---|---|---|---|---|
| adv20 | 1.02e6 | 4.76e6 | 4.85e7 | −0.0068 | +0.0131 | 0.685 | **NULL** |
| adv60 | 1.07e6 | 4.76e6 | 5.10e7 | −0.0043 | +0.0171 | 0.794 | **NULL** |
| vol_ratio_entry | 0.618 | 1.016 | 1.750 | −0.0413 | −0.0099 | 0.013 | **NULL** |
| vol_trend | 0.769 | 0.969 | 1.251 | −0.0363 | −0.0144 | 0.029 | **NULL** |
| dollar_volume20 *(liquidity proxy)* | 8.02e7 | 2.20e8 | 1.07e9 | −0.0472 | −0.0054 | 0.005 | **NULL** |
| rsi14 | 33.35 | 49.90 | 67.18 | −0.0080 | −0.0550 | 0.629 | **NULL** |
| **atr_pct** | 0.0190 | 0.0314 | 0.0521 | **−0.1444** | −0.0157 | 0.000 | clears floor |
| **realised_vol60** | 0.216 | 0.358 | 0.585 | **−0.1182** | −0.0119 | 0.000 | clears floor |
| price_at_entry | 11.97 | 43.28 | 183.0 | −0.0343 | −0.0399 | 0.040 | **NULL** |
| gap_to_ma50 | −0.100 | 0.016 | 0.128 | −0.0243 | −0.0631 | 0.144 | **NULL** |
| gap_to_ma200 | −0.028 | 0.176 | 0.406 | −0.0050 | −0.0262 | 0.764 | **NULL** |
| ma50_above_ma200 | 1 | 1 | 1 | +0.0289 | +0.0126 | 0.083 | **NULL** |
| up_day_fraction60 | 0.450 | 0.533 | 0.617 | +0.0165 | −0.0339 | 0.321 | **NULL** |
| return_5d | −0.095 | −0.012 | 0.051 | +0.0020 | −0.0412 | 0.904 | **NULL** |
| return_21d | −0.123 | 0.005 | 0.137 | −0.0235 | −0.0536 | 0.158 | **NULL** |
| dist_from_52w_low | 0.390 | 0.827 | 2.017 | −0.0125 | +0.0343 | 0.454 | **NULL** |

**14 of 16 NULL. No bucket tables were computed for any of them**, per the pre-registered rule — that
restriction is enforced in the code, not by discipline: the quintile/robustness routine is
unreachable for a feature that fails the floor.

**Baseline (A) agrees on 13 of 16.** Its differences: `dollar_volume20` marginally clears
(−0.0546 vs −0.0472) and `atr_pct` / `realised_vol60` clear more strongly (−0.159 / −0.140). No
feature NULL in the primary becomes materially predictive in the baseline.

**Two features worth noting as informative nulls rather than uninteresting ones:**
- **`ma50_above_ma200` has no spread** — p10 = p50 = p90 = 1. Essentially every admitted position is
  already in a golden-cross state, which is a mechanical consequence of requiring +2% trailing
  12-month momentum. It is uninformative for the same structural reason RSI was *expected* to be.
- **`return_21d` is the leg the adopted relative-rank signal deliberately excludes** (12-minus-1
  construction). Its correlation with outcome is **−0.0235**, i.e. nothing. The exclusion is not
  costing measurable information, and it is not obviously earning any either.

---

## 4. The two survivors — both INCONCLUSIVE, and why

Both cleared the |Spearman| ≥ 0.05 floor and both then **failed the monotonicity requirement**, which
is condition (2) of the pre-registered rule.

### `atr_pct` — primary, pre-2022 quintiles

| quintile | range | n | win% | mean P&L% | median P&L% | mean ex-top-1 |
|---|---|---|---|---|---|---|
| Q1 | 0.0099–0.0224 | 721 | 41.89 | +0.261 | −2.866 | +0.222 |
| Q2 | 0.0224–0.0283 | 720 | 46.81 | +0.961 | −1.615 | +0.916 |
| Q3 | 0.0283–0.0346 | 720 | 44.31 | +0.498 | −2.950 | +0.454 |
| Q4 | 0.0346–0.0435 | 720 | 42.50 | +0.656 | −3.394 | +0.565 |
| Q5 | 0.0435–0.1336 | 721 | 41.89 | +0.561 | −3.145 | +0.397 |

**Not monotonic — an inverted U, peaking at Q2.** Robustness: excluding 2008 non-monotonic
(Spearman −0.0918); 2015-onward monotonic (−0.0848); sign consistent within all four momentum
quartiles (−0.152, −0.080, −0.157, −0.175), so it is **not** a momentum proxy; **held-out 2022+
Spearman −0.1380, same sign, but non-monotonic.**

**Verdict: INCONCLUSIVE.** The sign is stable and replicates out-of-sample — but the ordering is not
monotonic in any period tested, which the pre-registration required.

### The mechanism — `atr_pct` is not a new feature

```
corr(atr_pct, stop_distance_pct) = 1.000000
stop_distance_pct / atr_pct      = 200.0000  (sd 0.001006)
corr(atr_pct, position_gbp)      = -0.1037  (Spearman)
```

`_resolve_entry_stop()` sets the stop at `2.0 × ATR` below entry, so `stop_distance_pct` is exactly
`200 × atr_pct`. **The feature that cleared the screening floor most convincingly is the
stop-distance variable itself** — already a first-class production input, governing both where the
stop sits and, through VaR sizing, how large the position is.

That makes its negative *rank* correlation substantially mechanical: a higher-ATR position is given a
proportionally **wider** stop, so it is permitted to lose a larger *percentage* before being stopped
out. The dispersion confirms it directly (all trades, primary):

| atr_pct quintile | n | sd of pnl_pct | p10 | p90 | **mean** | **median** |
|---|---|---|---|---|---|---|
| Q1 | 976 | 6.30 | −4.53 | +9.05 | **+0.396** | −2.944 |
| Q2 | 975 | 7.82 | −5.76 | +11.60 | +1.024 | −1.501 |
| Q3 | 975 | 9.27 | −7.15 | +13.82 | +0.746 | −2.230 |
| Q4 | 975 | 11.70 | −8.94 | +16.16 | +0.793 | −3.856 |
| Q5 | 975 | 16.29 | −13.35 | +22.60 | **+1.352** | −3.425 |

**The mean and the rank point in opposite directions.** Standard deviation rises 2.6× across
quintiles; the left tail widens (−4.53 → −13.35) and the right tail widens more (+9.05 → +22.60). The
median falls (which is what the negative Spearman detects) while the **mean rises** (+0.396 →
+1.352). Since this strategy's P&L is overwhelmingly right-tail driven — Part 1: the top decile
carries **257%** of total P&L — the rank statistic and the quantity that actually generates returns
disagree here.

Stated plainly and without proposing anything: **a rank-based reading of this feature says
"high-volatility entries are worse"; the mean-based reading says the opposite.** That is exactly why
the pre-registration required monotonicity rather than accepting a correlation coefficient, and it is
why this is reported as INCONCLUSIVE rather than as a finding in either direction.

### `realised_vol60` — the same variable in different clothing

Quintiles also form an inverted U (mean +0.224 → +0.504 → +0.838 → +1.083 → +0.288), non-monotonic;
sign consistent within momentum quartiles; held-out Spearman −0.1396, same sign, non-monotonic.
**Verdict: INCONCLUSIVE.** It measures the same underlying property as `atr_pct` and behaves the same
way. The two are not independent evidence.

### `dollar_volume20` (baseline arm only)

Cleared the floor at −0.0546 in the baseline (NULL at −0.0472 in the primary). Non-monotonic in every
period tested, held-out −0.0705. **Verdict: INCONCLUSIVE**, and **marginal in exactly the way the
multiple-comparisons argument predicted** — a feature straddling a 0.05 threshold in one arm and
falling below it in the other is the signature of noise near a cut point, not of an effect.

---

## 5. What was not available

**Market capitalisation and S&P 500 index weight are not stored, and point-in-time versions are not
obtainable free** — the same obstacle that closed the market-cap filter idea. `dollar_volume20` is a
**liquidity proxy and not a substitute**: it conflates size with turnover, is not point-in-time
corrected for anything, and moves with price as well as with float. Its null result is evidence about
dollar volume, not about market capitalisation, and should not be read as having tested the latter.

---

## 6. Honest prior, and how it fared

The pre-registration recorded the expectation of another null, on the strength of three prior
investigations:

| Investigation | Feature | Trade-level Spearman |
|---|---|---|
| Selection audit Part 2 | momentum magnitude | −0.033 |
| Momentum-smoothness study | path smoothness | null (NOT ADOPTED) |
| Stale-peak diagnostic | peak-drawdown at entry | −0.028 |
| **This screen** | **16 features** | **14 NULL, 2 INCONCLUSIVE** |

**The expectation held.** Of 16 previously-untested features spanning volume, liquidity, RSI,
volatility, price level, moving-average extension, trend structure, short-horizon reversal and
distance from the 52-week low, **none produced a result meeting the pre-registered bar**, and the two
that cleared the correlation floor did so on a variable the system already uses to size positions and
place stops.

**On multiple comparisons**: with 16 features and n≈3,600, at least one crossing |Spearman| = 0.05 by
chance was likely, and `dollar_volume20` behaved exactly like such a crossing (clears in one arm,
fails in the other, non-monotonic everywhere). The two volatility features are not chance crossings —
they are strong, sign-stable, and replicate out-of-sample — but they are one variable, already in the
system, whose rank and mean relationships to outcome point opposite ways.

---

## 7. Plain statement

**Fourteen of sixteen entry-time features are null against trade outcome. The two that are not are
the same volatility variable, which the system already uses to set stop distance and position size,
and whose apparent negative relationship reverses when measured on the mean rather than the rank.**

Nothing here supports adding an entry-time filter, and nothing here is proposed. The accumulated
result across four independent investigations is that **entry information does not predict individual
trade outcome in this system** — which is what one expects if the edge is distributional (a property
of holding a momentum-selected population over time) rather than selective (identifying which
particular trades will work). A fifth entry feature should be expected to behave the same way absent
a strong prior reason to think otherwise.

**No fix designed, no study proposed, per the task's constraints.**

---

## 8. Files

`PREREGISTRATION.md` (committed before computing), `step1_compute_features.py`, `step2_screen.py`,
`features_{B_relative_rank,A_absolute_momentum}.csv` (full per-trade output, all 16 features),
`screen_{B_relative_rank,A_absolute_momentum}.json`.

**Database state**: local research copy, `price_data` through **2026-08-07**, 612 CLEAN tickers, 608
usable — reconfirmed by direct query at run time. No database was written to.
