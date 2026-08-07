# NEEDS_REVIEW adjudication — Phase 1: contiguity analysis (2026-08-07)

**ANALYSIS ONLY. No `ticker_data_quality` rows were changed. No `price_data` rows were touched.**

Script: `step5_contiguity_analysis.py`. Output: `step5_contiguity_features.csv` (229 rows —
209 NEEDS_REVIEW + 20 controls: PALM + all 19 confirmed `LIKELY_RECYCLED` `QUARANTINED` tickers).

## Methodology (items a–f)

For each ticker, anchored on its last real `universe.get_membership_windows()` end_date:

- **(a)/(b) Largest gap** — computed TWO ways, not one, after an early version silently hid the
  real signal. A version that only searched *inside* `[end_date-90d, end_date+365d]` scored 76/209
  as "insufficient data" (fewer than 2 rows in the window) — including `PALM` and confirmed-corrupt
  control `NCC`, whose real gaps (PALM: ~12 years; NCC: ~3.3 years) are simply bigger than any
  fixed window. Fixed by also computing an **unbounded boundary gap**: the nearest `price_data` row
  at/before `end_date` and the nearest row after it, searched across the ticker's *entire* stored
  history, not capped to the window. The reported "largest gap" is the max of the two. Missing
  trading days computed against the real NYSE calendar (`pandas_market_calendars`), not raw
  calendar days, so ordinary weekends/holidays never register as a gap.
- **(c) `date_overrun_days`** — carried through from the existing evidence JSON.
- **(d) Price-scale continuity** — median close of the 30 rows immediately before vs. after the
  larger of the two gaps (or before/after `end_date` if no gap at all).
- **(e) `long_name` vs. historical constituent name** — **`long_name` is available** (from the
  already-cached `identity_verification_results.csv`, no new yfinance calls). **The "historical
  constituent name" side is NOT available anywhere in this project's data** — `universe_membership`
  has no company-name column, and the `fja05680/sp500` membership source is ticker-only. Rather
  than fabricate this via a new external fetch (Wikipedia's changes table, etc. — a new data
  dependency this task didn't ask for and that risks its own gaps for older delistings), this is
  reported honestly as unavailable for all 209. `long_name` alone is shown in the worked examples
  below for human judgment.
- **(f) Prior reversal classification** — carried through as context only, never used to decide.

## Distribution — reported before any threshold was chosen

`max_gap_missing_trading_days` across the 209 NEEDS_REVIEW tickers is **sharply bimodal**, not a
smooth curve needing an arbitrary cut point:

| range | count |
|---|---|
| exactly 0 | 129 |
| 0 < gap < 100 | **1** (`LPX`, gap=37) |
| gap ≥ 100 | 79 |

There is a real, near-total void between 37 and 107 — no ticker in the 209 falls in that range
except the single `LPX` case. This is the natural break the rule below is built on, not a chosen
number fitted after the fact.

`scale_ratio` (median close after ÷ before), by contrast, is a **smooth, non-discriminating**
distribution (10th pct 0.75, median 1.00, 90th pct 1.14) with no natural break — and, critically,
**it actively misleads on the one control that matters most**: `FNMA`'s scale_ratio is **0.134**
(a apparent 7.5x "collapse") despite `FNMA` being the must-stay-genuine control — this is Fannie
Mae's real 2008 price collapse during conservatorship, not corruption. Scale-ratio is reported per
item (d) but is **not used to decide anything** below; a threshold on it would have false-positived
the most important control in this exercise.

## Control validation — before proposing anything

| control | expected bucket | max_gap_missing_trading_days | landed in |
|---|---|---|---|
| **FNMA** | must be CLEAN/contiguous | 0 | ✅ contiguous (correctly excluded from quarantine) |
| **PALM** | must be corrupt | 3,114 | ✅ large gap |
| 19 confirmed `LIKELY_RECYCLED` `QUARANTINED` | must be corrupt | — | **10/19 caught**, **9/19 missed** |

**Caught (large gap, ≥100 missing trading days):** CIN, CNG, HM, HPC, KRI, MII, NCC, SRR, TNB, UCM.

**Missed — indistinguishable from genuine continuity by this signal:** BMC, BOL, CBE, CFC, GDW,
MEE, PBG, PTV, TIE. All nine show `max_gap_missing_trading_days` of 0–65 (BMC/CFC/TIE literally 0
or 1), i.e. they look exactly as contiguous as FNMA does. Scale-ratio doesn't rescue most of these
either — of the nine, only `CBE` (0.0009) shows a dramatic scale break; BMC (1.00), CFC (0.94),
TIE (1.07), PBG (1.05), MEE (0.93) all sit inside the same "looks normal" band FNMA's *own* range
would need to exclude them from, and BOL (1.40) is closer to 1 than FNMA's own 0.134.

**Honest conclusion, stated plainly per your instruction: contiguity does NOT cleanly separate the
full population.** It cleanly resolves one real subgroup (structural, multi-month-to-decades gaps)
with zero contradiction against either control (FNMA never falls in this bucket; every gap≥100
control is a confirmed corrupt case) — but it is **structurally blind to immediate-reuse
recycling** (a new entity starts trading under the old symbol with no temporal gap at all), which
is roughly **47% of the known-corrupt control population**. No second signal tested here (scale
continuity) reliably rescues that blind spot without also risking a false positive against FNMA.
This is not a rule that "fits every control perfectly" — it fails 9 of 19 controls outright, and
that failure is reported here rather than papered over.

## Proposed rule

```
IF max_gap_missing_trading_days >= 100:
    propose QUARANTINE  (79 of 209)
ELSE:
    STAYS NEEDS_REVIEW  (130 of 209)  -- contiguity cannot resolve this subgroup;
                                          no promotion to CLEAN is proposed on this
                                          basis, since gap=0 is equally consistent
                                          with "genuine" and "immediate-reuse corrupt"
```

No ticker is proposed for CLEAN. Absence of a gap is not evidence of genuineness (9 confirmed
corrupt controls also show no gap) — it only means this particular signal has nothing to say about
that ticker, so it should stay exactly where it is: NEEDS_REVIEW.

100 is not an arbitrary fitted number — it sits in the ~70-trading-day-wide dead zone between the
observed cluster at 0–37 and the next real value at 107; anywhere in [38, 106] produces the
identical 79/130 split.

### Worked examples — QUARANTINE-candidate bucket (79), spanning the range

| ticker | gap (missing trading days) | gap (calendar days) | date_overrun_days | long_name | prior reversal class |
|---|---|---|---|---|---|
| CAR | 107 | 156 | 7,311 | Avis Budget Group, Inc. | none |
| TKR | 1,358 | 1,976 | 9,131 | The Timken Company | none |
| USBC | 2,369 | 3,442 | 10,597 | USBC, Inc. | LIKELY_CORPORATE_ACTION |
| ASND | 3,923 | 5,697 | 9,905 | Ascendis Pharma A/S | none |
| ECO | 6,435 | 9,342 | 10,430 | Okeanis Eco Tankers Corp. | none |

Caveat worth flagging on `CAR` specifically: Avis Budget Group's real 2006 spinoff from Cendant
happened close to this gap's start — this may be a genuine corporate restructuring under a reused
ticker rather than an unrelated company squatting on a dead symbol years later. Either way the
price series is **not continuous with whatever S&P constituent held `CAR` before 2006**, so
quarantine is still the defensible call for this ticker's *pre-2026-08-06-remediation research
candidacy* purpose — but it's a different flavor of "corrupt" than, e.g., `ECO`'s 25-year gap, and
worth knowing that nuance exists in this bucket.

### Worked examples — STAYS NEEDS_REVIEW bucket (130)

| ticker | gap (missing trading days) | scale_ratio | date_overrun_days | long_name | prior reversal class |
|---|---|---|---|---|---|
| FNMA | 0 | 0.134 | 6,541 | Federal National Mortgage Association | LIKELY_CORPORATE_ACTION |
| LPX | 37 | — | 7,212 | Louisiana-Pacific Corporation | none |
| SSP | 0 | 0.062 | 6,611 | The E.W. Scripps Company | none |
| CPWR | 0 | 1.935 | 5,332 | Ocean Thermal Energy Corporation | LIKELY_RECYCLED |
| MOH | 0 | 0.803 | 178 | Molina Healthcare, Inc. | none |

`SSP` is a good second illustration of the FNMA pattern: E.W. Scripps is a real, still-trading
company under its long-standing `SSP` ticker; its dramatic scale_ratio is the real 2008 crisis, not
corruption — exactly why scale-ratio was not used to decide. `CPWR` is the most genuinely
unresolved case in this bucket: the *prior* reversal classifier flagged it `LIKELY_RECYCLED`,
identity verification downgraded it to `NEEDS_REVIEW` (FNMA-shaped override), and contiguity now
also finds no gap — three different signals in tension, correctly left for a human rather than
forced either way. `LPX` is the sole middle-zone case (37 missing trading days, ~2 months) — small
enough that a genuine trading halt (bankruptcy proceeding, exchange suspension) can't be ruled out;
deliberately left unresolved rather than guessed.

## Bottom line

- **79/209** would move to QUARANTINED under this rule — validated against controls with zero
  contradiction (no genuine control ever lands here; every gap≥100 control is confirmed corrupt).
- **130/209** would stay NEEDS_REVIEW — this is not a failure of the exercise, it's the honest
  result: contiguity has nothing useful to say about roughly 47% of even the *known*-corrupt
  population, so forcing a verdict here would be guessing, not adjudicating.
- If applied, `get_research_stock_candidates()`'s NEEDS_REVIEW-inclusive pool shrinks from 209 to
  130 unresolved tickers (plus 83+79=162 excluded, 116 confirmed clean) — a real reduction, not a
  full resolution.

**Awaiting your approval before Phase 2.** Specifically: (1) do you approve the rule and threshold
as stated, (2) should `CAR`-shaped "gap coincides with a real corporate action" cases be treated
differently from `ECO`-shaped "decades-long unrelated-entity" cases, or is quarantine correct for
both, and (3) is leaving 130/209 unresolved an acceptable outcome, or would you rather see a
different signal explored for that specific subgroup before moving to Phase 2.
