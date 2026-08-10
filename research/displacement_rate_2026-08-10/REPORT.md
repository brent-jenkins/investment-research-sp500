# Displacement-rate analysis — prep, not a mechanism (2026-08-10)

**Recommendation: a displacement mechanism keyed on rank gap does NOT look worth building. There is
no usable operating point.** At every margin where it fires often enough to matter, the turnover cost
exceeds the entire cost-adjusted return of the strategy it would modify. At every margin where the
cost is tolerable, it fires a median of 0–18 times a year with crisis-concentrated maxima 10–50×
that. Two further findings — a structural bias in the gap itself, and positions becoming
"displaceable" within a day of entry — suggest the trigger would churn rather than select, and that
the problem is the *signal*, not the *threshold*.

Read-only. No engine, no arms, no production file touched, nothing adopted.

---

## Method and sources

| quantity | source |
|---|---|
| Held positions per day | Arm B preserved trades (4,876), entry/exit spans — used directly |
| Deployment, balance per day | Arm B preserved panel (4,930 days) — used directly |
| Rank per ticker per day | **Genuinely absent from preserved output** — recomputed |
| Gate-passing per ticker per day | **Genuinely absent** — recomputed |

The rank and gate matrices had to be rebuilt because the trade files record only admitted positions,
not the daily eligible set or its rank values. This is deterministic recomputation via the unmodified
`cross_sectional_signal.py` and the same gate conditions the engine uses — **no admission, sizing,
capacity or exit logic runs.** Not a backtest.

**Margin units, stated explicitly**: the ranking signal is the 12-minus-1 return (0.45 = +45%), so a
"30% margin" means the candidate out-ranks the holding by more than **0.30 in absolute signal terms —
percentage points of trailing return**, the same units as the existing 2% momentum floor. Not a
relative ratio, not a rank-position difference.

Baseline context: mean balance £75,802, mean position £3,381, FX round-trip 0.30%. Arm B's own
cost-adjusted CAGR is **5.433%** (excess vs `^SP500TR`: −5.730pp). 4,484 of 4,930 days are at or near
full deployment (≥65%).

---

## Q1 + Q2 + Q3 — the full margin sweep

The pre-specified 5/10/20/30% grid turned out to sit **below the 25th percentile** of the observed
gap distribution, so it could not locate an operating point. Extended as the distribution required:

| margin | % days firing | % near-full days | displacements/yr | median/yr | max yr | **cost drag %/yr** |
|---|---|---|---|---|---|---|
| 0.05 | 99.40 | 99.44 | 813.0 | 818 | 1,334 | **21.761** |
| 0.10 | 98.25 | 98.39 | 682.8 | 668 | 1,215 | **18.273** |
| 0.20 | 92.17 | 92.52 | 498.2 | 470 | 1,020 | **13.335** |
| 0.30 | 82.75 | 83.17 | 369.9 | 331 | 874 | **9.899** |
| 0.50 | 61.26 | 61.67 | 217.6 | 177 | 623 | 5.823 |
| 0.75 | 37.47 | 37.72 | 119.2 | 88 | 387 | 3.189 |
| 1.00 | 24.01 | 24.17 | 73.5 | 41 | 265 | 1.966 |
| 1.50 | 11.91 | 11.99 | 34.5 | 18 | 191 | 0.923 |
| 2.00 | 7.41 | 7.46 | 20.2 | 4 | 133 | 0.542 |
| 3.00 | 3.99 | 4.02 | 10.6 | 0 | 78 | 0.282 |
| 5.00 | 1.26 | 1.27 | 3.6 | 0 | 53 | 0.098 |

**Cost is two round trips per displacement at the established 0.30% FX template, computed from real
mean position size — not estimated.**

**Near-full-deployment days are indistinguishable from all days** (99.44 vs 99.40, 83.17 vs 82.75 —
every row agrees within 0.5pp). Displacement exists to address capacity-bound days, and those days
are not special: the opportunity is present essentially always, so restricting to them changes
nothing.

**The cost comparison that settles it.** Arm B's entire cost-adjusted CAGR is **5.433%**. At every
margin from 0.05 to 0.30 the drag is **9.9%–21.8%/yr — two to four times the whole return.** Even at
0.50 the 5.82% drag consumes it entirely. The mechanism only becomes affordable (<1%/yr) at margins
≥1.50, i.e. requiring a candidate to out-rank a holding by **150+ percentage points of trailing
return.**

**Q2's year distribution is the "different animal" case the brief anticipated.** At margin 1.50:
median 18/yr, max 191 (2021). At 2.00: median **4**/yr, max 133. At 3.00 the median is **zero** —
more than half of all years would see the mechanism never fire, while its worst year sees 78. This is
not a steady trickle at any affordable margin; it is a crisis/melt-up-concentrated burst.

---

## Q4 — the rank gap distribution, and why it is structurally biased

Best unadmitted candidate minus **worst** held position, per day:

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | mean |
|---|---|---|---|---|---|---|---|---|---|
| 0.073 | 0.169 | 0.223 | 0.372 | **0.602** | 0.979 | 1.648 | 2.659 | 5.594 | 0.960 |

**Positive on 99.87% of days.** The median gap is 0.602 — the best available candidate typically
out-ranks the weakest holding by 60 percentage points of trailing return. Against each held position
(not just the worst), the gap is positive 74.28% of the time, median +0.247.

**This near-universality is the finding, and it argues against the mechanism rather than for it.** A
signal that says "your weakest holding is much worse than the best thing available" on 99.87% of
days is not identifying opportunities; it is describing a structural property of the comparison.
Mean held positions: **14.3**. Mean eligible unadmitted candidates: **215.7**.

**The comparison is max-of-216 against a specific incumbent — a winner's-curse shape.** The best of
~216 daily draws is an extreme order statistic; a held position is one name whose own 12-minus-1
window now includes its post-entry path. The gap is therefore biased upward by construction, and
mostly measures sample size, not relative quality. Any mechanism keyed on it would systematically
chase the extreme of a large sample — precisely the population mean reversion punishes, and
consistent with the selection audit's Part 2 finding that raw momentum magnitude carries no
trade-level predictive power.

---

## Q5 — how long a holding stays competitive: it does not

Days from entry until the position first becomes displaceable:

| margin | never displaceable | median days | **displaceable within 1 day** | within 5 days |
|---|---|---|---|---|
| 5% | 11.96% | 0 | **77.8%** | 91.5% |
| 10% | 13.72% | 0 | **74.0%** | 89.5% |
| 20% | 18.21% | 0 | **67.1%** | 85.6% |
| 30% | 24.04% | 1 | **60.8%** | 81.6% |

**This is the decisive practical result.** Between 61% and 78% of positions become displaceable
**within one day of being admitted** — at a 30% margin, a position bought today is displaceable
tomorrow six times out of ten. A mechanism triggering on this would not be upgrading a stale holding
for a better one; it would be reversing its own admission decision almost immediately, then reversing
that, at two round trips a time.

The median of 0–1 days is not a tail effect: it is the typical case. Nothing here supports the
premise that holdings stay strong for weeks and displacement would fire rarely and meaningfully.

---

## Recommendation

**Do not build it — not at a different threshold, because the threshold is not the problem.**

1. **No usable operating point exists.** Affordable margins (≥1.50) fire a median of 0–18 times/year
   with maxima 10–50× that; margins that fire usefully cost 2–4× the strategy's entire return. There
   is no region where frequency and cost are simultaneously acceptable.
2. **The trigger fires on 61–78% of positions within a day of entry** — it would churn, not select.
3. **The gap it keys on is structurally biased** (max-of-216 vs one incumbent), so widening the
   margin does not buy discrimination, it just samples further into the tail of the same biased
   statistic.

**What would need to be different for this to be worth revisiting** — recorded so the idea is closed
for the right reason, not abandoned wholesale:

- A trigger keyed on the **held position's own deterioration** (its rank percentile within the day's
  eligible set falling below some level) rather than on a gap against the day's extreme. That is
  scale-free and not a max-of-N statistic. Note this is close to Arm C's `RANK_DROPPED`, which was
  tested and **underperformed Arm B in both pools** — so the prior is unfavourable and any such study
  would need to explain why its construction differs materially from C's.
- A **minimum holding period** before a position becomes displaceable, which would directly attack
  the Q5 churn result. This is the only variant these numbers leave genuinely open, and it converts
  displacement into something closer to a scheduled re-evaluation — which the two rebalancing studies
  already found fails on deployment grounds.

---

## Caveat — this measures OPPORTUNITY, not OUTCOME

These numbers come from a backtest in which displacement never happened. They bound the mechanism's
**frequency and cost**; they do **not** predict its benefit. A displacement that actually fires
changes every subsequent admission via capacity cascade, and this project has measured 22–78%
trade-set reshuffling from far smaller changes — the relative-ranking study found only 21.8–23.1%
exact-date overlap from changing nothing but a sort key.

**What that caveat does and does not weaken.** The frequency and cost figures are robust to it: they
are computed from real rank values on real days, and a mechanism firing 813 times a year at 21.8%
drag cannot be rescued by cascade effects, because the cost is incurred per firing regardless of
outcome. The Q5 churn result is likewise structural. What the caveat genuinely leaves open is whether
the *specific trades* a displacement mechanism would make are good ones — which is unknowable without
building it, and which the cost figures make moot at every frequency where it would matter.
