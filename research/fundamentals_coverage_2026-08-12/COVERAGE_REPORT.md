# yfinance fundamentals coverage probe (2026-08-12)

**The gating deliverable, reported before any table, capture path or scheduler change was written.**
Read-only: no database writes, no schema, no scheduler change, and deliberately no contact with
`verify_ticker_identity()` / `scan_unverified_tickers()`.

**Verdict: coverage is good enough to proceed. 50/50 sampled candidates returned usable data, with
zero hard errors and zero empty responses, on both quarterly and annual statements.** The design
question the probe actually settles is not *whether* to record but *which statement* to record —
and the answer is **quarterly**, for a reason the probe measures directly.

Sample: 50 of 503 `get_live_stock_candidates()`, seed 20260812, 2.5s rate limit (the project
standard). Runtime 183s quarterly + 171s annual.

---

## 1. Availability

| | quarterly | annual |
|---|---|---|
| Hard errors | **0 (0%)** | **0 (0%)** |
| Returned nothing | **0 (0%)** | **0 (0%)** |
| Usable | **50 (100%)** | **50 (100%)** |

No ticker in the live candidate pool failed to return fundamentals. This is a materially better
starting point than the brief allowed for, and it is worth stating plainly: **the "coverage might be
too sparse to bother" risk did not materialise for this universe.**

Worth noting *why* it is this good, so the result isn't over-generalised: `get_live_stock_candidates()`
is today's real S&P 500 membership — large, actively-covered US listings. **This says nothing about
coverage for delisted or historical tickers**, which is a different population (and the one this
project's own price data has repeatedly found gaps in). Since capture is at candidate-list time, only
live candidates are ever fetched, so the measured population is the relevant one.

## 2. Field presence

| field | quarterly | annual | row label matched |
|---|---|---|---|
| `operating_cash_flow` | **100%** | 100% | `Operating Cash Flow` |
| `shares_outstanding` | **100%** | 100% | `Ordinary Shares Number` |
| `total_equity` | 98% | 100% | `Stockholders Equity` |
| `total_assets` | 98% | 100% | `Total Assets` |
| `free_cash_flow` | 98% | 100% | `Free Cash Flow` |
| `total_debt` | 96% | 98% | `Total Debt` |
| `revenue` | 92% | 100% | `Total Revenue` |
| `net_income` | 92% | 100% | `Net Income` |

All eight requested raw fields are obtainable. Quarterly is slightly less complete than annual
(92–100% vs 98–100%), concentrated in `revenue`/`net_income`.

**Row labels vary and must not be hardcoded.** The probe tried several aliases per concept and
recorded which matched; yfinance has changed these between versions (`Total Stockholder Equity` →
`Stockholders Equity`, and so on). The recorder stores which alias produced each value, so a future
label change shows up as a coverage drop against a known baseline rather than as silent nulls.

## 3. Reporting-period staleness — the finding that decides the design

Days from 2026-08-12 to the most recent reporting period end:

| statement | min | median | p90 | max |
|---|---|---|---|---|
| **quarterly** | 43 | **43** | 104 | 134 |
| **annual** | 73 | **224** | 224 | 377 |

**Quarterly data is ~5× fresher in the median (43 days vs 224).** A 43-day lag is the normal
reporting delay — a quarter ending 2026-06-30 filed in early August. The annual median of 224 days
means the typical annual figure describes a fiscal year that ended over seven months ago.

**This is decisive for a point-in-time recorder.** The entire purpose is capturing *what was
knowable on the date*, and annual statements are knowable-but-stale by construction. Recording
annual would produce a dataset whose median observation is more than half a year behind the decision
it sits next to.

**Decision: capture QUARTERLY only.** The 6–8pp coverage cost on `revenue`/`net_income` is a far
better trade than a 181-day median staleness penalty, and a missing field is recoverable later
(the next quarter's fetch fills it) whereas a stale vintage is not.

## 4. Fiscal calendars genuinely differ — the reporting period must be per-ticker

AAPL's latest quarter ends **2026-06-30**; NVDA's ends **2026-04-30**. The p90/max staleness spread
(104/134 days) is mostly this effect, not missing data — tickers on off-calendar fiscal years simply
have older "most recent" quarters at any given moment.

**Consequence for the schema**: a single job-level "as of" date would silently mix vintages across
tickers. The reporting period has to be stored **per field, per ticker, per fetch** — which is what
makes the dataset point-in-time rather than merely dated. The probe confirms all three statements
(balance/income/cash) generally share a period end per ticker, but they are recorded separately
rather than assumed equal, because that assumption costs nothing to avoid and would be invisible if
wrong.

## 5. What this does NOT establish

1. **Vintage correctness of the values themselves.** yfinance returns *today's* view of a past
   period — if a figure is later restated, this fetch captured the pre-restatement number only
   because it was fetched today. That is exactly the property being bought, and it accrues going
   forward; it cannot be verified retrospectively.
2. **Coverage for anything but live S&P 500 candidates** (see §1).
3. **Stability of yfinance's schema over time.** Mitigated by recording the matched alias, not by
   being solved.
4. **That any of this predicts anything.** Nothing here is analysis.

---

## Files

`probe_coverage.py`, `coverage_results.json` (full per-ticker output, both statement types),
`_log_probe.txt`.
