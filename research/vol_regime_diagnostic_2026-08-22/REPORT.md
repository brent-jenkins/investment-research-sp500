# Volatility / MA regime signal — READ-ONLY DIAGNOSTIC (2026-08-22)

**Question: does a volatility signal, alone or combined with an index moving average, separate
subsequent market outcomes well enough that a dynamic deployment cap keyed to it could work?**

**ANSWER: NO. There is no meaningful separation on forward RETURN, and outside a single episode the
sign is inverted — the signal marks periods with roughly three times the baseline forward return.
Forward DRAWDOWN does separate, robustly and by about 2×, but that is the one thing the existing
crisis override already targets, at one fifteenth the firing rate. On this evidence a dynamic
deployment cap keyed to volatility should not be designed, and the regime-gate family is closed.**

No arms, no engine, no backtest, no pre-registration, no adoption. Read-only: `^GSPC` from
`price_data`, opened through a read-only SQLite URI; no database writes; no config or code changes;
nothing deployed. This is a property of one index series, not a strategy test.

---

## 0. Data and conventions

`^GSPC`, **4,929 rows, 2007-01-03 → 2026-08-07**, extracted read-only from
`C:\GitHub\investment-app\database\investment_research.db` and integrity-checked before use: unique,
sorted, no NaN or non-positive closes, **zero** price-cliff breaks outside [0.5, 2.0], max calendar
gap 5 days (weekends). Largest single-day moves ×0.8802 / ×1.1158. 4,807 rows are evaluable (a
trailing 60-day window and a forward 90-day window must both exist).

- **Realised volatility** = std of daily **log** returns over the trailing window **inclusive of day
  t** (information available at t's close), annualised ×√252.
- **Forward return over H calendar days** = close at the last trading day at or before `t+H`, over
  close at `t`.
- **Forward max drawdown (90d)** = `min(price / running_peak − 1)` over `[t, t+90d]` with the peak
  **seeded at t's close** — the worst peak-to-trough an investor holding from t would have lived
  through.
- **Volatility deciles are full-sample**, which is hindsight bucketing. Fine for a property question,
  not tradable — so every headline is **also** recomputed with a point-in-time expanding percentile
  (§6), and that is the version any real rule would have to use.

**THE SAMPLE-SIZE CAVEAT, stated before any number below.** Forward windows overlap, so daily
observations are heavily serially dependent. 4,807 days is **not** a sample size; there are roughly
**76 non-overlapping 90-day windows** in this history, and the conditions studied fire in **10–24
contiguous episodes**. Episode counts are reported for every condition, and §7 re-does the headline
at episode level. Nothing here should be read as statistically significant.

**Baseline, all days:** forward 30d **+0.83%** mean / **+1.46%** median; forward 90d **+2.45%** /
**+3.65%**; forward 90d max drawdown **−7.66%** / **−5.76%**; forward 90d negative **28.3%** of the
time.

---

## 1. (a) Forward outcomes by trailing-volatility decile

### 20-day realised volatility

| decile | vol range % | n | fwd30 mean | fwd90 mean | fwd90 med | fwd90 DD mean | fwd90 DD med | fwd90 DD p10 | % fwd90 < 0 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 3.3–7.4 | 491 | +0.30 | +0.98 | +2.69 | −6.63 | −4.65 | −10.17 | 27.1 |
| 2 | 7.4–9.1 | 491 | +0.83 | +0.89 | +2.52 | −6.37 | −5.53 | −9.88 | 32.2 |
| 3 | 9.1–10.3 | 481 | +0.44 | +2.49 | +3.50 | −5.89 | −5.34 | −9.60 | 26.8 |
| 4 | 10.3–11.8 | 472 | +0.69 | +3.18 | +4.11 | −6.13 | −5.29 | −9.94 | 22.2 |
| 5 | 11.8–13.5 | 483 | +0.43 | +2.43 | +3.60 | −7.15 | −5.46 | −12.55 | 27.1 |
| 6 | 13.5–15.2 | 481 | +1.25 | +3.01 | +3.84 | −6.30 | −4.57 | −13.47 | 24.3 |
| 7 | 15.2–17.7 | 478 | +1.13 | +3.02 | +4.59 | −6.73 | −4.68 | −14.84 | 27.4 |
| 8 | 17.7–21.0 | 488 | +0.75 | +1.78 | +4.18 | −8.99 | −7.35 | −16.45 | 35.9 |
| 9 | 21.0–27.5 | 491 | +1.01 | +1.57 | +4.04 | −10.42 | −7.35 | −19.04 | 33.6 |
| **10** | **27.5–97.9** | 491 | **+1.42** | **+5.17** | **+6.41** | **−11.93** | **−9.19** | **−25.19** | **27.5** |

### 60-day realised volatility

| decile | vol range % | n | fwd30 mean | fwd90 mean | fwd90 med | fwd90 DD mean | fwd90 DD med | % fwd90 < 0 |
|---|---|---|---|---|---|---|---|---|
| 1 | 5.0–9.3 | 487 | +0.52 | +0.86 | +3.03 | −6.76 | −3.94 | 27.1 |
| 2 | 9.3–10.7 | 487 | +0.10 | +1.26 | +3.13 | −7.48 | −5.76 | 25.9 |
| 3 | 10.7–11.6 | 487 | +0.39 | +2.69 | +3.03 | −6.21 | −5.76 | 26.9 |
| 4 | 11.6–12.5 | 487 | +1.61 | +3.67 | +4.23 | −5.60 | −4.60 | 23.0 |
| 5 | 12.5–13.8 | 465 | +0.91 | +1.11 | +1.51 | −6.80 | −4.78 | 39.4 |
| 6 | 13.8–15.5 | 448 | +0.76 | +2.91 | +4.21 | −6.82 | −4.32 | 30.6 |
| 7 | 15.5–17.7 | 485 | +0.72 | +4.00 | +4.99 | −6.66 | −5.62 | 18.4 |
| 8 | 17.8–21.4 | 487 | +0.75 | −0.44 | +2.37 | −9.91 | −7.40 | 39.6 |
| 9 | 21.4–26.8 | 487 | +0.56 | +1.73 | +3.73 | −9.39 | −7.75 | 32.6 |
| **10** | **26.8–74.0** | 487 | **+1.93** | **+6.35** | **+8.20** | **−11.25** | **−7.20** | **24.0** |

**Reading.** Two different things happen, and they point opposite ways.

- **Drawdown separates, and materially.** Deciles 1–7 sit around −6 to −7% mean forward drawdown;
  deciles 8/9/10 step to −8.99 / −10.42 / −11.93. The top decile's p10 forward drawdown is −25.19%
  against a −10.17% p10 in the bottom decile. This is real, monotone at the top end, and consistent
  across both lookbacks.
- **Return does not separate in the direction a cap needs — it separates the wrong way.** The top
  volatility decile has the **best** forward 90-day return of any decile, on both lookbacks
  (+5.17% / +6.35% mean, +6.41% / +8.20% median, against a +2.45% / +3.65% baseline). The share of
  negative forward-90 outcomes in the top decile (27.5%) is **indistinguishable from baseline**
  (28.3%) and lower than deciles 8 and 9.

A deployment cap keyed to high volatility would cut exposure precisely during the decile with the
highest subsequent index return.

---

## 2. (b) Forward outcomes by moving-average position

| condition | fires % of days | episodes | n | fwd30 mean | fwd90 mean | fwd90 med | fwd90 DD mean | % fwd90 < 0 |
|---|---|---|---|---|---|---|---|---|
| above SMA7 | 60.5 | — | 2,938 | +0.69 | +2.19 | +3.47 | −7.17 | 27.7 |
| **below SMA7** | **39.5** | 538 | 1,923 | +1.03 | +2.84 | +4.04 | −8.43 | 29.4 |
| above SMA14 | 63.7 | — | 3,085 | +0.72 | +2.13 | +3.45 | −7.07 | 27.9 |
| **below SMA14** | **36.3** | 358 | 1,769 | +1.00 | +3.01 | +4.33 | −8.71 | 29.3 |
| above SMA30 | 66.5 | — | 3,207 | +0.65 | +2.11 | +3.40 | −6.96 | 27.9 |
| **below SMA30** | **33.5** | 230 | 1,631 | +1.20 | +3.09 | +4.45 | −9.07 | 29.6 |

**Same shape as volatility: below-MA days have deeper forward drawdowns and HIGHER forward returns.**
Being below the moving average is, in this sample, a mild positive return signal, not a negative one.

**The pre-committed suspicion check came back reassuring, not suspicious.** The concern was that if
the 7-day separated while the 30-day did not, the 7-day would be picking up the short-horizon
reversal the 12-minus-1 momentum construction deliberately skips. The opposite ordering holds: the
**30-day separates most** (return +3.09 vs +2.11, drawdown −9.07 vs −6.96) and the **7-day least**.
Monotone in the sensible direction, so no short-horizon reversal artefact is indicated. It does not
rescue anything — the separation is the wrong sign on return either way — but the reason to distrust
it is absent.

---

## 3. (c) The combination — does direction improve on volatility alone?

Volatility condition = top decile of 20-day realised vol (fires 10.0% of days, 24 episodes).

| SMA | cell | fires % | n | fwd30 mean | fwd90 mean | fwd90 med | fwd90 DD mean | % fwd90 < 0 |
|---|---|---|---|---|---|---|---|---|
| **30** | high vol AND **below** | 5.8 (27 eps) | 286 | +0.98 | **+4.34** | +4.59 | **−13.79** | 34.3 |
| | high vol AND above | — | 205 | +2.04 | +6.34 | +8.11 | −9.34 | 18.0 |
| | low vol AND below | — | 1,345 | +1.24 | +2.83 | +4.45 | −8.07 | 28.6 |
| | neither | — | 3,002 | +0.55 | +1.83 | +3.22 | −6.80 | 28.6 |
| **14** | high vol AND below | 4.8 (51 eps) | 238 | +0.98 | +5.62 | +6.21 | −13.88 | 30.3 |
| | high vol AND above | — | 253 | +1.84 | +4.75 | +6.82 | −10.10 | 24.9 |
| **7** | high vol AND below | 4.6 (65 eps) | 226 | +1.47 | +5.53 | +6.28 | −14.07 | 31.9 |
| | high vol AND above | — | 265 | +1.38 | +4.87 | +6.77 | −10.11 | 23.8 |

**Yes, direction adds separation — but only on drawdown, and only with the 30-day.** At SMA30, within
the high-volatility population, being below the average roughly halves the forward return
(+4.34 vs +6.34), nearly doubles the negative rate (34.3% vs 18.0%) and deepens drawdown
(−13.79 vs −9.34). At SMA7 and SMA14 the return ordering **inverts** (below-MA does *better* than
above-MA), so the directional condition is not stable across the three lookbacks.

**The decisive number in this table is that the worst cell is still good.** "High vol AND below
SMA30" — the cell a cap rule would target — has a forward 90-day return of **+4.34% mean / +4.59%
median, against an all-days baseline of +2.45% / +3.65%.** The condition intended to identify danger
identifies periods with **above-average** subsequent returns and roughly double the drawdown. Risk
and return both roughly double; there is no free reduction to harvest.

---

## 4. (d) Persistence — does sustained elevation improve on a single day's reading?

| condition | fires % | episodes | n | fwd30 mean | fwd90 mean | fwd90 med | fwd90 DD mean | % fwd90 < 0 |
|---|---|---|---|---|---|---|---|---|
| single day | 10.0 | 24 | 491 | +1.42 | +5.17 | +6.41 | −11.93 | 27.5 |
| sustained 5d | 8.3 | 19 | 407 | +1.66 | +5.15 | +6.40 | −12.16 | 30.0 |
| sustained 10d | 6.5 | 15 | 319 | +1.98 | +5.15 | +6.93 | −12.53 | 33.2 |
| sustained 20d | 4.0 | 10 | 196 | +2.59 | +5.24 | +7.46 | −13.13 | 35.2 |
| sustained 5d AND below SMA30 | 4.6 | 23 | 228 | +1.26 | +4.32 | +4.42 | −14.18 | 38.6 |
| sustained 10d AND below SMA30 | 3.4 | 21 | 166 | +1.64 | +4.12 | +2.29 | −14.82 | 44.6 |
| **sustained 20d AND below SMA30** | **1.7** | **11** | 84 | +2.92 | +3.73 | **−1.79** | **−16.26** | **53.6** |

**Persistence alone does NOT help.** Requiring 5, 10 or 20 consecutive days of elevated volatility
leaves forward returns essentially unchanged and *high* (mean +5.15 to +5.24, median +6.40 to +7.46)
while drawdowns deepen slightly. The hypothesis that transient bull-market spikes recover while
sustained elevation does not is **not supported**: sustained elevation recovers too.

**Persistence combined with direction is the only cell in the entire diagnostic where forward returns
look bad** — sustained-20d AND below SMA30 has a **negative median (−1.79%)** and a **majority
negative (53.6%)** rate. It fires on 1.7% of days across **11 episodes**.

**That cell does not survive §7.** See below: excluding 2008 it reverses to +5.10% mean / +6.01%
median on 7 episodes.

---

## 5. (e) Firing frequency, and overlap with what already exists

| condition | % of trading days | episodes | note |
|---|---|---|---|
| vol20 top decile | 10.0 | 24 | median episode length 14 days |
| below SMA30 | 34.0 | 231 | |
| high vol AND below SMA30 | 5.8 | 27 | |
| sustained 20d AND below SMA30 | 1.7 | 11 | |
| **existing crisis entry block** | **0.67** | **4** | 7 raw trigger days (S&P ≤ −7% in a day) |

**Overlap with the mechanism already in production:** **100% of existing crisis-block days are
high-volatility days**, but only **6.7% of high-volatility days are already blocked**. The existing
crisis override is a strict, tiny subset of the volatility condition.

**A volatility-keyed cap would therefore fire roughly 15× more often than the mechanism already in
place** (10.0% vs 0.67% of days). That ratio is not a coincidence worth passing over: CLAUDE_HISTORY
records the V2 Phase 3 **VIX-spike crisis-override variant as rejected because it "fired ~15× more
often, reproduced Phase 2's over-suppression problem."** This diagnostic reproduces that arithmetic
independently, from price data alone, without using VIX at all.

---

## 6. (f) Point-in-time version — what a real rule would actually see

Full-sample deciles use hindsight. Re-run with an **expanding** percentile (each day ranked only
against history available up to that day, minimum 252 observations):

| condition | fires % | episodes | n | fwd30 mean | fwd90 mean | fwd90 med | fwd90 DD mean | % fwd90 < 0 |
|---|---|---|---|---|---|---|---|---|
| baseline, all days | 100 | — | 4,807 | +0.83 | **+2.45** | +3.65 | −7.66 | 28.3 |
| PIT top decile | 5.7 | 20 | 280 | +1.08 | **+2.63** | +4.16 | −13.26 | 38.2 |
| PIT top decile AND below SMA30 | 4.0 | 21 | 196 | +0.24 | **+1.90** | +3.15 | −14.78 | 40.8 |

Agreement with the full-sample decile classification: 95.1% of days.

**This is the most favourable specification available, and it is still not enough.** Volatility alone
gives a forward-return difference of **+0.18pp over 90 days** — nothing. Adding direction takes it
**0.55pp below baseline** over 90 days, on 21 episodes, while forward drawdown roughly doubles
(−14.78% vs −7.66%).

---

## 7. THE DECISIVE PANEL — episode-level, and the 2008 concentration

Day counts hide that these conditions fire in a handful of clusters. Measured **once per contiguous
episode**, at each episode's first day:

| condition | episodes | mean fwd90 | median fwd90 | % negative |
|---|---|---|---|---|
| vol20 top decile (full-sample) | 24 | **+5.26%** | **+6.71%** | 21% |
| PIT top decile | 20 | **+1.37%** | **+3.99%** | 20% |
| PIT top decile AND below SMA30 | 21 | **+1.35%** | **+2.21%** | 33% |

Every specification is **positive on both mean and median** at episode level.

**And the negative outcomes are one episode.** For the PIT combined condition, the only materially
negative episodes are **2008-09-15 (−26.24%), 2008-12-11 (−17.43%), 2008-11-05 (−11.99%) and
2008-04-14 (−6.69%)**. **77 of its 196 days (39%) fall in 2008.** Every other cluster the condition
identifies was followed by positive returns, several of them strongly: 2011-08-10 **+13.84%**,
2018-12-26 **+14.21%**, 2015-09-04 **+6.68%**, 2025-04-04 **+23.75%**.

**Excluding 2008 entirely, the sign inverts and the magnitude is large:**

| | n days | fwd90 mean | fwd90 med | fwd90 DD mean |
|---|---|---|---|---|
| PIT top decile AND below SMA30, ex-2008 | 119 | **+9.56%** | **+7.50%** | −9.79 |
| all non-2008 days | 4,614 | +3.20% | +3.90% | −6.98 |

**Outside 2008 the "danger" condition marks periods with roughly three times the baseline forward
return.** The one cell in §4 that looked supportive — sustained-20d AND below SMA30, median −1.79%,
53.6% negative — is **43 of 84 days in 2008 and 24 in 2009**; excluding 2008 it reverses to
**+5.10% mean / +6.01% median, 29% negative on 7 episodes**.

**Even the second-most-obvious crisis fails to support the signal.** The condition fired on
**2020-03-02**, and the forward 90-day return from that day was **−1.49%** — essentially flat. The
COVID rebound was fast enough that a 90-day window captured it.

**This is the standing "a guard validated on its motivating example" pattern, arriving before the
guard was built rather than after.** 2008 is the motivating example for every regime gate this
project has tried. A volatility signal identifies it — and identifies buying opportunities in
2011, 2015, 2018, 2020, 2022 and 2025.

---

## 8. Interpretation against the pre-committed criteria

**"Some separation is expected and is not itself a finding; the question is whether it is large
enough to pay for the turnover a cap rule would incur."**

- **On return: there is no separation to pay for.** The tradable specification differs from baseline
  by +0.18pp (volatility alone) or −0.55pp (with direction) over 90 days, on ~20 episodes, and
  reverses to +6.4pp *in the signal's favour* once 2008 is excluded. There is nothing here that a cap
  could convert into return, and a real risk it would subtract.
- **On drawdown: the separation is real (~2×) but is the wrong instrument's job.** The trailing-stop
  study established that in this always-redeploying portfolio, position-level protection converts
  into turnover rather than portfolio-level risk reduction, because the book refills. A deployment
  cap that holds capital *out* would avoid that specific objection — but it would then be a
  cash-holding rule, and holding cash for 5.8–10% of all days during periods whose expected return is
  at or above average is the deployment-artefact failure that already sank the admission-redesign and
  cross-sectional-momentum studies.
- **Short-horizon MA suspicion: absent.** The 30-day separates most and the 7-day least — the
  reassuring ordering, not the suspicious one.

**"State plainly if the answer is no meaningful separation."** It is. Volatility, moving-average
position, their combination, and their persistence all fail to identify periods of materially worse
subsequent index returns. They do identify periods of deeper drawdown, at 15× the firing rate of the
crisis override that already exists and that fires only on the genuine tail.

---

## 9. Conclusion — the regime-gate family is closed

**A dynamic deployment cap keyed to volatility should not be designed, and this study should not be
written.** The signal it would rest on does not separate the outcome it would need to separate.

This completes a consistent record across the whole family rather than adding another null:

- **V2 Phase 2 (rejected)**: S&P below its 200-day average and/or VIX ≥ 30 — every variant
  underperformed the ungated baseline.
- **V2 Phase 3 VIX-spike variant (rejected)**: fired ~15× more often than the price-based override,
  reproducing Phase 2's over-suppression.
- **Submission-time risk veto (rejected)**: VIX > 25 gated 847 days across 49 episodes.
- **This diagnostic**: the price-only version of the same idea, measured directly on forward
  outcomes rather than through a backtest, fails at the signal level — before any rule, sizing or
  capacity is involved.

**What survives, and is already in production:** the rare-firing crisis override (S&P ≤ −7% in a
single day), 7 trigger days and 0.67% of days across 19 years. It is a strict subset of the
volatility condition and it is the only part of this signal space with a demonstrated case.

**What this does not close.** Forward drawdown *is* predictable from trailing volatility (~2×,
consistently, in every specification including point-in-time). Nothing here says that is unusable —
only that it does not come with a return penalty to harvest, so any use of it would have to be
justified as risk reduction accepted at a known return cost, tested against the deployment-parity
requirement, and not as an edge. That is a different proposition from the one this diagnostic was
asked to check, and it would need its own pre-registration.

---

## 10. Files and provenance

`step1_extract_gspc.py`, `step2_diagnostic.py`, `gspc.csv` (4,929 rows), `daily_panel.csv` (the full
per-day signal and forward-outcome panel), `diagnostic.json`, `run.log`.

**Read-only throughout**: `^GSPC` opened via a `mode=ro` SQLite URI from
`C:\GitHub\investment-app\database\investment_research.db`; the existing crisis trigger computed by
calling the shared `analysis/crisis.py` functions rather than reimplementing them. **No database
writes. No config or code changes. Nothing adopted, nothing deployed.** Dedicated worktree
`C:\GitHub\investment-app-volregime`, branch `research/vol-regime-diagnostic-2026-08-22`.
