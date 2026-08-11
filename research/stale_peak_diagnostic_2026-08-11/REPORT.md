# The "stale peak" admission pattern — diagnostic (2026-08-11)

**Read-only. No arms, no engine, no adoption, no production changes, no fix proposed.** This
describes decisions the system already made, using preserved trade output plus read-only
derivations from `price_data`.

---

## Headline

**The pattern is real, it is ubiquitous rather than exceptional — and it is associated with BETTER
outcomes, not worse. It does not explain a material share of losses; it produces a
disproportionate share of the gains.**

- **92.2%** of admitted positions were below their own trailing-window peak at entry. The median
  admitted position entered **8.6% below** its peak. Entering at the peak is the rare case, not the
  common one.
- Outcomes improve **monotonically** across every below-peak bucket: win rate **41.4% → 47.7%**,
  mean P&L **+0.38% → +2.51%**, median P&L **−3.30% → −0.57%**. The direction is the opposite of
  the hypothesis.
- The positions ≥10% below their peak are **43.6% of trades** and carry **50.8% of all gains
  against 45.7% of all losses — net +£116,280, or 69.7% of total P&L.**
- **ECHO is an extreme instance of the pattern (97.2nd percentile, 36.5% below peak) and its entry
  was the system working exactly as designed** — it ranked **5th of 308** candidates that night.

**The correct reading is not "this pattern is good" but "entry information does not predict outcome,
and where it weakly does, it points the other way."** Trade-level correlation is essentially zero
(Spearman −0.028), exactly as Part 2 of the selection audit found for momentum magnitude. The
bucket-level monotonicity is real and robust but weak per trade — the same shape Part 2 reported for
momentum deciles.

---

## Method and definitions

**Primary dataset**: `research/relative_ranking_prereg_2026-08-09/trades_pool2_clean_B.csv` —
**4,876 trades, £166,830.11**, the relative-rank (Arm B) trade set, i.e. **the ordering that is
currently live** since the 2026-08-11 adoption. **Baseline reported alongside**:
`trades_pool2_clean_A.csv` — 4,919 trades, £103,926.50, absolute-momentum ordering. Both used
exactly as preserved; **no backtest was re-run.**

**Four fields were genuinely absent from the preserved CSVs** (which carry only ticker, dates,
prices, exit reason, P&L, stop distance, holding days): peak-drawdown-at-entry, days-since-peak,
momentum-at-entry, and rank-within-day. All four are recomputed read-only from `price_data` using
the **same production functions the engine itself calls** (`compute_momentum_series`,
`compute_relative_rank_series`, `compute_signal_series`, `universe.is_member`, `analysis.crisis`).
This is a derivation over decisions already made, not a re-simulation.

**The metric, defined before measuring.** `compute_momentum_series()` is
`close(t) / close(t−252) − 1.0` — a two-point calculation whose invisible interval is exactly rows
`[t−252, t]`. So:

```
peak_close      = max(close) over rows [t-252, t] inclusive
below_peak_pct  = (peak_close - close(t)) / peak_close * 100     # 0 = entered at the peak
days_since_peak = trading rows between the peak row and t
```

The window is `[t−252, t]` inclusive — not "the trailing 252 rows" — because `t−252` is precisely
the momentum calculation's own back anchor, so the window is exactly the interval bounded by the two
points the signal reads. `below_peak_pct` is a positive magnitude with the **peak** as denominator,
so it reads as the drawdown a holder of the peak would have suffered.

**Anchoring verified, not assumed**: `entry_price` in the preserved trades matched the stored
`price_data` close on the entry date for **4,876 / 4,876** trades (and 4,919/4,919 in the baseline).

---

## Distributions — reported before any cut point was chosen

| | p10 | p25 | **p50** | p75 | p90 | p95 | p99 | max | mean | skew |
|---|---|---|---|---|---|---|---|---|---|---|
| **below_peak_pct** | 0.43 | 3.33 | **8.63** | 15.70 | 24.74 | 31.65 | 45.22 | 73.09 | 11.03 | +1.55 |
| **days_since_peak** | 1 | 6 | **17** | 45 | 91 | 133 | 211 | 247 | 34.2 | +2.19 |
| momentum_at_entry_pct | 23.1 | 46.8 | 70.1 | 105.4 | 165.4 | 236.9 | 477.0 | 4855 | 95.6 | +17.98 |
| rank_pct_within_day | 0.52 | 1.64 | 3.49 | 6.51 | 19.10 | 50.92 | 100 | 100 | 9.24 | +3.51 |

`below_peak_pct` histogram, 5pp bins from 0:

```
1625  1123   827   515   307   196   120    68    44    23    14     1     7     3     3
```

**The distribution is continuous and smoothly decaying, with no natural break.** It is a
right-skewed unimodal decay from zero. **Every cut point below is therefore a choice, not a
discovered structure**, and is stated as such. Percentile reference points: p50 = 8.6%, p75 = 15.7%,
p90 = 24.7%, p95 = 31.7%.

---

## Q1 — How common is the pattern?

| Below own trailing peak by ≥ | n | % of admitted |
|---|---|---|
| any amount (>0) | 4,495 | **92.19%** |
| 5% | 3,251 | 66.67% |
| 10% | 2,128 | **43.64%** |
| 15% | 1,301 | 26.68% |
| 20% | 786 | **16.12%** |
| 25% | 479 | 9.82% |
| 30% | 283 | **5.80%** |
| 40% | 95 | 1.95% |
| 50% | 28 | 0.57% |

**Entering below the trailing peak is the normal case (92.2%), not an anomaly.** This is a
structural consequence of the two-point signal combined with capacity-timed admission, exactly as
the motivation described — but it means "was this position below its peak at entry" cannot by itself
distinguish anything, because almost every position was.

Baseline (absolute momentum) is similar but milder: 87.58% below any amount, median 5.97%,
43.64% → 33.75% at the ≥10% cut. **Relative-rank ordering admits somewhat deeper-below-peak
positions than absolute momentum did** — a real difference between the two rankings, and one that
matters for reading ECHO (see Q5).

---

## Q2 — Do those positions perform worse? No — monotonically better.

**Primary (relative rank, current live ordering):**

| below_peak at entry | n | %n | win% | mean P&L% | median P&L% | p10 | p90 | total P&L | mean ex-top-1 |
|---|---|---|---|---|---|---|---|---|---|
| 0–5% | 1,625 | 33.3 | **41.42** | **+0.383** | −3.296 | −7.3 | +10.7 | £27,786 | +0.322 |
| 5–10% | 1,123 | 23.0 | 43.54 | +0.644 | −3.406 | −8.2 | +13.4 | £22,764 | +0.591 |
| 10–20% | 1,342 | 27.5 | 44.93 | +1.135 | −1.670 | −9.7 | +15.7 | £71,957 | +1.068 |
| 20–30% | 503 | 10.3 | 46.12 | +1.240 | −1.263 | −11.7 | +18.2 | £19,202 | +1.082 |
| **≥30%** | 283 | 5.8 | **47.70** | **+2.511** | **−0.573** | −14.8 | +24.3 | £25,121 | +2.101 |

Win rate, mean, and median all improve monotonically across all five buckets. The p10/p90 columns
show the mechanism: deeper-below-peak positions have **wider outcomes in both directions** (p10
−7.3% → −14.8%, p90 +10.7% → +24.3%) — higher variance, with the upside growing faster than the
downside.

**Robustness — four checks, because a difference in means can be one trade (Part 1: top decile
carries 257% of P&L):**

1. **Dropping each bucket's single largest winner** (`mean ex-top-1` column) leaves the monotonic
   ordering intact: +0.322 → +0.591 → +1.068 → +1.082 → +2.101.
2. **Excluding 2008** makes the pattern *stronger*, not weaker: win 42.09% → 51.87%, mean +0.508% →
   +3.531%, median −3.227% → **+0.762%**. It is not a crisis artefact.
3. **2015 onwards only**: still present (win 41.28% → 50.00%, mean +0.614% → +2.766%), with a small
   dip at the 20–30% bucket. Persistent across eras, not confined to one regime.
4. **It is not a momentum proxy.** Spearman(below_peak_pct, momentum_at_entry_pct) = **−0.269** —
   deeper-below-peak positions have *lower* momentum, so the effect is not the Part 2 D10 effect in
   disguise. Conditioning directly, mean P&L% by momentum quartile × below-peak bucket:

   | | <10% below | 10–20% | ≥20% |
   |---|---|---|---|
   | M1 (lowest momentum) | +0.10 | +0.10 | **+1.71** |
   | M2 | +0.60 | +1.39 | +1.31 |
   | M3 | +0.65 | +0.64 | **+2.69** |
   | M4 (highest momentum) | +0.44 | **+2.58** | +1.27 |

   The below-peak effect is positive within **every** momentum quartile, and is largest in the
   *lowest* momentum quartile (+0.10 → +1.71). A weak name deep below its peak outperforms a weak
   name near its peak.

**Baseline (absolute momentum) agrees in the 0–30% range and breaks at the extreme**: win 41.79% →
47.01% and mean +0.252% → +1.899% through the 20–30% bucket, but its ≥30% bucket (n=203) shows mean
+0.132% and **−0.076% once its single largest winner is removed**. **The monotonic direction
replicates across both rankings; the extreme tail is arm-dependent and single-trade-sensitive in the
baseline.** Stated rather than smoothed, since the primary arm's ≥30% bucket is the one ECHO sits in.

**Time since peak is a different and mostly flat story**, with one exception:

| days_since_peak | n | win% | mean P&L% | total P&L |
|---|---|---|---|---|
| 0–4 | 1,061 | 42.13 | +0.567 | £30,051 |
| 5–20 | 1,637 | 44.65 | +0.934 | £63,117 |
| 21–62 | 1,347 | 44.62 | +0.909 | £34,357 |
| 63–125 | 553 | 45.21 | +1.636 | £39,598 |
| **≥126** | 278 | **37.05** | **−0.203** | **−£292** |

**The one genuinely negative sub-pattern in this entire diagnostic**: positions whose peak was more
than 126 trading days ago — half the lookback window — have the lowest win rate and a slightly
negative mean. But it is **5.7% of trades and −£292 net, against a £166,830 total.** Immaterial.

**Correlations, per the Part 2 guardrail:** Spearman(below_peak_pct, pnl_pct) = **−0.028** (p=0.049),
Pearson +0.051; Spearman(days_since_peak, pnl_pct) = −0.014 (p=0.316). **Essentially zero at the
trade level** — the expected result, reported as such. The bucket-level monotonicity and the
near-zero rank correlation are not in conflict: this is the same shape Part 2 found for momentum
deciles (weak ordering across buckets, no trade-level predictive power).

---

## Q3 — Share of total losses vs total gains (the actual question)

Gross gains **£776,286.58**; gross losses **−£609,456.47**; net £166,830.11.

| Population | n | % of trades | **% of all gains** | **% of all losses** | net P&L | % of total P&L |
|---|---|---|---|---|---|---|
| ≥10% below peak | 2,128 | 43.64% | **50.83%** | **45.67%** | +£116,280 | **69.7%** |
| ≥20% below peak | 786 | 16.12% | 20.26% | 18.54% | +£44,323 | 26.6% |
| ≥30% below peak | 283 | 5.80% | 8.16% | 6.27% | +£25,121 | 15.1% |

**At every cut point the population takes a larger share of gains than of losses.** This is the
decisive answer: the pattern is not a loss generator that also happens to produce winners — it is
**net positive at every threshold**, and the ≥10% population alone accounts for **69.7% of the
strategy's entire realised P&L**.

Baseline agrees directionally (≥10%: 40.68% of gains vs 37.83% of losses, +£56,537 = 54.4% of total
P&L), with the ≥30% tail near-neutral (+£3,057, 2.9%).

---

## Q4 — Era and sector concentration

**By era: frequency is strongly regime-dependent, presence is universal.** Mean below-peak at entry
ranges from 5.99% (2012) to 16.65% (2008); the ≥20% share ranges from 4.4% (2012) to 34.8% (2008).
High-frequency years are drawdown/volatility years — 2008 (34.8%), 2022 (30.8%), 2026 (29.8%),
2025 (22.0%) — and low-frequency years are steady bull markets (2012, 2017, 2019, all ≤6%).
**The pattern occurs in every single year; only its prevalence varies.**

Outcomes for the pattern-heavy years are not uniformly bad: 2008 was the disaster (win 20.3%,
−£21,243), but 2024, 2025 and 2026 combine high prevalence with the three best P&L years in the
sample (+£34,210, +£31,660, +£38,711). **The pattern is currently at its most prevalent in a decade
and is currently profitable.**

**By sector** (83.0% coverage, from the preserved enriched export's sector map):

| Sector | n | mean below-peak | % ≥20% | win% | P&L |
|---|---|---|---|---|---|
| Technology | 1,097 | 11.45 | 17.78 | 48.04 | **+£124,976** |
| Consumer Cyclical | 719 | 9.79 | 12.10 | 42.00 | +£7,250 |
| Healthcare | 422 | 9.10 | 9.95 | 42.65 | −£3,028 |
| Industrials | 414 | 8.92 | 9.66 | 44.69 | +£19,114 |
| Energy | 316 | 12.44 | **20.57** | 40.51 | −£2,414 |
| Basic Materials | 221 | 12.74 | **23.98** | 39.37 | −£1,595 |

**The pattern is modestly concentrated in commodity-linked sectors** (Basic Materials 23.98% and
Energy 20.57% at ≥20%, both with the lowest win rates in the table) — consistent with mean-reverting
commodity price behaviour. But the bulk of the affected population sits in **Technology**, where it
is both common and highly profitable. **The pattern is not confined to a regime or a sector; the
losing concentration is a small tail of it.**

---

## Q5 — Where ECHO sits

**Production values, queried read-only over SSH (`pending_orders`, production database,
2026-08-11):**

| Field | Value |
|---|---|
| `decided_at` | 2026-08-06T22:10:14 |
| `status` | SUBMITTED (resolved 2026-08-07T14:30:39) |
| `momentum_pct` | **2.255892265983018** (i.e. **+225.59%**) |
| `admission_rank_value` | **2.255892265983018** — identical to `momentum_pct` |
| `relative_rank_pct` | **NULL** |
| Rank in its own decision batch | **5th of 308 candidates** |

**`admission_rank_value` equals `momentum_pct` exactly, which confirms ECHO was admitted under
`absolute_momentum` ordering — the relative-rank flip happened 2026-08-11, five days after this
decision.** The motivating example therefore comes from the *previous* ranking regime, not the one
now live. (`relative_rank_pct` is NULL for this row: the column exists but was not populated at
decide time for this batch.) Neither field exists in the local research DB copy, which predates
their deploy — stated explicitly rather than inferred.

**ECHO's entry context, computed on the identical definitions** (window 2025-08-06 → 2026-08-07,
253 rows):

| | value | percentile among the 4,876 admitted |
|---|---|---|
| close at entry (2026-08-07) | $90.04 | — |
| close 252 rows back | $26.99 | — |
| **peak in window** | **$141.80** on 2026-05-20 | — |
| **below_peak_pct** | **36.50%** | **97.17th** |
| **days_since_peak** | **54** trading days | 79.55th |
| **momentum_at_entry_pct** | **+233.6%** | 94.87th |

**One correction to the motivating description, from real stored data**: the trailing-window peak was
**$141.80**, not ~$125, so the drawdown at entry was **36.5%**, deeper than the ~30% described. The
run-up start (~$27 at exactly 252 rows back) and the ~$88–90 entry match.

**Was this the system working as designed? Yes, unambiguously.**
- It cleared the entry gate legitimately: momentum +225.6%, far above the 2% floor.
- It was **5th of 308** candidates — admitted from the very top of the ranking, not from deep in it.
- It sits in the **≥30% below-peak bucket**, which in the primary trade set is the **best-performing
  bucket of the five**: n=283, win rate 47.70%, mean P&L +2.511%.

**ECHO is an extreme instance of the pattern (top 3%) whose historical cohort is the strongest, not
the weakest.** No defect is present. Whether this particular position works out is a separate
question this diagnostic cannot answer and does not try to.

---

## Would a filter on this pattern hit the capacity wall?

The smoothness study's §6 found candidacy filters reach admissions at roughly two-thirds the rate
they exclude candidate-days, because Part 4 established 92–93% of admissions are capacity-timed.
**This pattern would face that wall less than a candidacy filter would, because the affected
positions are not marginal admissions:**

| Population | mean rank-within-day | median |
|---|---|---|
| all admitted | 9.24% | 3.49% |
| ≥10% below peak | 12.07% | 3.93% |
| ≥20% below peak | 15.08% | 4.59% |
| ≥30% below peak | 18.09% | 5.23% |

Affected positions are admitted from *slightly* deeper in the ranking, but the median remains inside
the **top 5%** of each day's eligible set. These are top-of-ranking admissions, so a filter targeting
them would displace real admissions rather than being absorbed by capacity timing.

**That makes the capacity wall the wrong concern here. The relevant concern is Q3: such a filter
would be removing the population that produces 69.7% of total P&L.**

---

## Guardrails, addressed explicitly

**Part 2's near-zero-correlation expectation held.** Spearman(below_peak_pct, pnl_pct) = −0.028 and
Spearman(days_since_peak, pnl_pct) = −0.014 are indistinguishable from zero. This is the expected
result and is reported as such, not explained away. The bucket-level monotonicity is a real but weak
effect of the same kind Part 2 found across momentum deciles.

**Effect size vs the ~1.08pp noise floor — a comparison I am deliberately not making.** That floor
was measured in cost-adjusted CAGR across perturbed full-walk runs; per-trade mean P&L% is a
different quantity in different units, and putting them side by side would imply a precision neither
supports. The materiality statement that *is* directly comparable is the P&L-share one: the ≥10%
population carries 69.7% of total realised P&L. That is not a marginal effect in any units.

**Multiple comparisons.** With ~4,900 trades and enough features something will always appear to
separate winners from losers, so the finding's weight rests on it being (a) in the **opposite**
direction to the hypothesis that motivated the search, (b) **monotonic across all five buckets**
rather than a single split, (c) robust to dropping each bucket's largest winner, to excluding 2008,
to restricting to 2015+, and to conditioning on momentum quartile, and (d) directionally replicated
in the independent absolute-momentum trade set. Those checks do not make it a strong per-trade
predictor — it is not — but they do rule out its being a one-trade or one-era artefact.

---

## Plain statement

**This pattern does not explain a material share of losses. It is another instance of "entry
information does not predict outcome" — with the additional finding that the weak signal that does
exist points in the opposite direction from the hypothesis.**

The mechanism described in the motivation is real and correctly identified: the two-point momentum
calculation is blind to the path between its anchors, and a position can be a third of the way down
from its own peak and still rank near the top. That is exactly what happened with ECHO. **What the
trade history shows is that this blindness has not been costly.** Positions bought well below their
trailing peak have historically won more often, returned more on average and in the median, and
contributed a larger share of gains than of losses — in both ranking regimes, in every momentum
quartile, and outside the 2008 crisis as strongly as within the full sample.

The one negative sub-pattern found anywhere in this diagnostic is positions whose peak was more than
126 trading days ago: 278 trades, win rate 37.05%, net **−£292**. That is the entire measurable cost
of the pattern in the primary trade set, and it is immaterial.

**No fix is designed and no study is proposed here, per the task's constraints.**

---

## Files

`step1_compute_metrics.py`, `step2_analyse.py`, `step3_echo.py`,
`metrics_{B_relative_rank,A_absolute_momentum}.csv` (full per-trade output with all derived fields),
`analysis_{B_relative_rank,A_absolute_momentum}.json`, `echo_placement.json`.

**Database state**: local research copy, `price_data` through **2026-08-07**, 612 CLEAN tickers,
608 usable — reconfirmed by direct query at run time. ECHO's `momentum_pct`/`admission_rank_value`
came from the **production** database via a read-only SSH `SELECT` on 2026-08-11, because those
columns postdate the local copy. No database was written to at any point.
