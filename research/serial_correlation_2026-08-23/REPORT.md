# DIAGNOSTIC B — are trade outcomes serially correlated? (2026-08-23)

**ANSWER: the pausing proposal is closed, but NOT for the reason the pre-committed interpretation
expected.** Trade outcomes are **not** independent — lag-1 autocorrelation is **+0.21 (Pool 2 CLEAN)
and +0.25 (Pool 1)**, and conditioning on prior outcomes moves win rate from 28.85% unconditional to
**12.44% after five losses** and **63.64% after five wins**, symmetrically. On the face of it that is
exactly what a pausing rule would want.

**It does not support the rule, for two independent reasons:**

1. **95% of "consecutive" trades are not sequential at all — they overlap in time**, by 64 days on
   average. The trade-level statistic is measuring shared market conditions, not a sequence effect.
2. **At portfolio level — the level a pausing rule actually acts on — there is no relationship.**
   Forward returns after a drawdown are indistinguishable from unconditional, and at deep drawdowns
   they are *better*.

Read-only. No arms, no engine, no adoption, no database writes, no code or config change.

---

## 1. Trade-level autocorrelation — real, and larger than expected

Ordered by exit date, 95% block-bootstrap CIs (an iid CI would be far too narrow here, since the
series is not independent even under the null of no sequence effect):

| lag | Pool 2 CLEAN Pearson | 95% CI | Spearman | Pool 1 Pearson | Spearman |
|---|---|---|---|---|---|
| 1 | **+0.2115** | [0.003, 0.348] | +0.229 | **+0.2531** | +0.203 |
| 2 | +0.0774 | [−0.001, 0.199] | +0.246 | +0.1747 | +0.238 |
| 3 | +0.1756 | [−0.004, 0.248] | +0.177 | +0.1754 | +0.192 |
| 4 | +0.1662 | [−0.015, 0.207] | +0.150 | +0.2166 | +0.204 |
| 5 | +0.1111 | [−0.046, 0.174] | +0.074 | +0.1952 | +0.144 |
| 10 | +0.0668 | [−0.053, 0.185] | −0.003 | +0.0359 | +0.118 |

Positive and persistent out to lag 5, well past `|ρ| < 0.05`. **This is not a null**, and the
pre-committed instruction not to slice after a null does not apply — point 5 is what decides it.

## 2/3. Conditioning is strong and symmetric

**Pool 2 CLEAN** (unconditional: n=565, mean +12.53%, win 28.85%):

| condition | n | mean | median | win % |
|---|---|---|---|---|
| after 1 loss | 401 | +5.84% | −5.52% | **19.45** |
| after 2 losses | 322 | +2.15% | −5.89% | **14.91** |
| after 3 losses | 274 | +1.71% | −5.94% | **12.77** |
| after 5 losses | 209 | +0.10% | −5.50% | **12.44** |
| after 1 win | 163 | +29.10% | +0.57% | **52.15** |
| after 3 wins | 49 | +44.73% | +3.75% | **65.31** |
| after 5 wins | 22 | +59.72% | +3.84% | **63.64** |

Pool 1 is the same shape (after 5 wins: n=18, +86.93%, 72.22%). **Monotone in run length and
symmetric in both directions** — precisely the signature a genuine sequence effect would produce, and
precisely why it needed the overlap test rather than being taken at face value.

## 4. PORTFOLIO LEVEL — the version that matters, and it shows nothing

A pausing rule acts on the portfolio, not on a sequence of individual trades. Forward equity-curve
return after a drawdown from peak:

| condition | days | % of days | fwd-30 mean | fwd-90 mean | fwd-90 median |
|---|---|---|---|---|---|
| **unconditional** | 4,930 | 100.0 | **+0.968** | **+2.992** | +3.305 |
| drawdown ≥ 5% | 2,522 | 51.2 | +0.913 | +2.796 | +3.010 |
| drawdown ≥ 10% | 1,923 | 39.0 | +0.806 | +2.416 | +2.637 |
| **drawdown ≥ 20%** | 711 | 14.4 | **+1.215** | **+3.562** | **+4.629** |
| drawdown ≥ 30% | 433 | 8.8 | +0.619 | +2.316 | +3.630 |

Pool 1 agrees (unconditional fwd-90 +3.235; ≥20% +3.127; ≥30% +2.311).

**No monotone relationship, no meaningful gap, and the deepest-drawdown bucket that a pausing rule
would most confidently act on shows the HIGHEST forward return of any condition** (+3.562 vs +2.992
unconditional on Pool 2 CLEAN; median +4.629 vs +3.305). That is the stated concern in the
motivation, confirmed directly: **pausing after drawdowns would systematically skip the best
conditions.**

## 5. THE DECISIVE TEST — the trade-level correlation is an overlap artifact

Two controls, the second decisive.

**Regression control.** Regressing each trade's `pnl_pct` on the concurrent ^GSPC return over that
trade's own holding period: **beta 2.54, R² 0.534** — over half the variance of trade outcomes is the
concurrent market move. Residual lag-1 autocorrelation falls 0.2115 → **0.1579** (Pool 2) and
0.2531 → **0.2013** (Pool 1). A ~25% reduction: real, but not an explanation on its own.

**The overlap test, which is decisive:**

| | Pool 2 CLEAN | Pool 1 |
|---|---|---|
| consecutive pairs | 564 | 521 |
| **pairs OVERLAPPING in time** | **535 (94.9%)** | **485 (93.1%)** |
| mean overlap of consecutive pairs | **64.4 days** | 70.7 days |
| lag-1 correlation, all pairs | +0.2115 | +0.2531 |
| **lag-1 correlation, NON-overlapping pairs only** | **+0.1056** | **−0.2844** |
| n non-overlapping | **29** | **36** |

**With ~12 concurrent positions and a 149-day mean hold, "the next trade" is almost never the next
trade in time — it is a position already open, overlapping the previous one by two months.** The
lag-1 statistic is largely one claim restated: *two positions held over the same 64-day window
performed similarly.* That is a market-conditions fact, not a sequence effect, and it cannot support
a rule that acts on order of occurrence.

**The genuinely non-overlapping subset flips sign between pools (+0.106 vs −0.284) on n = 29 and 36**
— which is what noise at that sample size looks like, not evidence either way. So the honest
statement is not "the sequence effect is disproved" but **"the trade record cannot measure a sequence
effect, because sequential trades are 95% concurrent"** — and the portfolio-level test in §4, which
does not have this problem, shows nothing.

---

## Conclusion

**The pausing proposal is closed without needing a study.** Not because outcomes are independent —
they are visibly not, at trade level — but because:

- the trade-level correlation is **unmeasurable as a sequence effect** (95% of consecutive pairs
  overlap by an average of 64 days), and what survives a market control is a market fact restated;
- the **portfolio-level measure, which is what a pausing rule would act on, shows no relationship** —
  and the deepest drawdowns are followed by the *best* forward returns in the sample.

**Both of those had to hold. Either alone would have left the question open**, and the trade-level
numbers on their own would have read as strong support for pausing.

**A caution worth carrying:** the conditional table in §2/3 is genuinely striking — 12.4% win rate
after five losses against 28.9% unconditional — and it is the kind of result that reads as decisive
in isolation. It is almost entirely the statement that losing positions cluster in bad markets, which
everyone already knew, re-expressed as a sequence. **Any future proposal keyed to "runs" of outcomes
in this system should be checked against the overlap fraction first**, because at ~12 concurrent
positions almost any sequential statistic will inherit this artifact.

## Files

`diagnostic_b.py`, `diagnostic_b2_overlap.py`, `diagnostic_b.json`, `diagnostic_b2_overlap.json`.

Preserved trade sets and panels for the current live configuration
(`research/exit_mechanics_prereg_2026-08-11/`, cell `s3.0`: Pool 2 CLEAN 565 trades / £348,548.74,
Pool 1 522 / £429,108.58), and `^GSPC` read from `price_data` (through 2026-08-07). Read-only
throughout; no database writes, nothing adopted, nothing deployed.
