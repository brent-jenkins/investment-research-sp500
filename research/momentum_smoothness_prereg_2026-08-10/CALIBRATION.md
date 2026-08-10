# Threshold calibration — FROZEN (2026-08-10)

Per PREREGISTRATION.md §3. Distribution of the independent variable only —
no backtest was run and no outcome, P&L, or trade data was touched to produce
these numbers. Frozen before any arm ran.

- Pool 2 CLEAN gate-passing candidate-days: **1,599,004** (NaN smoothness: 0, 0.000%)
- Pool 1 gate-passing candidate-days: **1,368,277** (NaN smoothness: 0, 0.000%)
- Pooled usable smoothness values: **2,967,281**

## Pooled distribution

| quantile | value |
|---|---|
| p1 | -0.0595 |
| p5 | -0.0238 |
| p10 | -0.0040 |
| p25 | +0.0317 |
| p40 | +0.0516 |
| p50 | +0.0675 |
| p60 | +0.0794 |
| p75 | +0.1032 |
| p90 | +0.1389 |
| p99 | +0.2063 |
| mean | +0.0678 |
| std | +0.0564 |

## Frozen thresholds (absolute from here on)

| arm | nominal percentile | threshold |
|---|---|---|
| B1 | p10 | **-0.004** |
| B2 | p25 | **+0.032** |
| B3 | p40 | **+0.052** |

A candidate is excluded from candidacy when `smoothness < T`. Realised
exclusion will drift from the nominal 10/25/40% across eras — that drift is a
reported outcome (§6), not a defect.

## Exclusion rate by year (pooled, at each frozen threshold)

| year | n | < T1 | < T2 | < T3 |
|---|---|---|---|---|
| 2008 | 59,872 | 23.2% | 48.7% | 62.4% |
| 2009 | 61,134 | 20.0% | 47.6% | 64.2% |
| 2010 | 194,965 | 10.7% | 32.9% | 48.5% |
| 2011 | 170,613 | 10.5% | 32.0% | 47.3% |
| 2012 | 153,836 | 16.7% | 40.6% | 56.2% |
| 2013 | 208,904 | 9.7% | 29.2% | 42.8% |
| 2014 | 207,719 | 7.1% | 23.7% | 36.4% |
| 2015 | 168,784 | 8.3% | 29.2% | 44.1% |
| 2016 | 137,946 | 8.1% | 27.6% | 40.9% |
| 2017 | 204,806 | 6.9% | 22.7% | 33.2% |
| 2018 | 172,270 | 3.0% | 13.6% | 23.3% |
| 2019 | 157,811 | 3.2% | 12.8% | 21.9% |
| 2020 | 139,689 | 4.0% | 16.1% | 26.2% |
| 2021 | 232,929 | 8.5% | 25.0% | 36.4% |
| 2022 | 116,590 | 11.3% | 32.8% | 47.3% |
| 2023 | 131,225 | 17.7% | 43.0% | 57.7% |
| 2024 | 193,138 | 7.2% | 22.1% | 33.3% |
| 2025 | 155,026 | 8.5% | 25.7% | 37.3% |
| 2026 | 100,024 | 13.9% | 33.6% | 44.2% |
