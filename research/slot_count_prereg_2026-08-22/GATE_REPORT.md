# Hard slot-count study — §7 VERIFICATION + §2 GATE (2026-08-22)

**Stopped here for review, as instructed. NO return figure has been computed, and none appears
below** — no CAGR, no excess return over `^SP500TR`, no cost adjustment, no arm-level total P&L.
The only P&L quantities reported are the two *composition shares* §2.4 explicitly requires. `^SP500TR`
and `^IRX` have deliberately not been fetched into this study's cache yet.

**Every §7 check and the §8 sanity gate PASS on both pools. All four treated arms clear the §2.2
deployment guard at the "clean" tier. Three of four clear the §2.1 materiality test; `n14` is
reported NOT TESTED.**

---

## 0. Environment (verified fresh in the place it is used, per CLAUDE.md's derived-state rule)

Dedicated worktree `C:\GitHub\investment-app-slotcount`, branch `research/slot-count-2026-08-22`
(another session holds `investment-app-fxfee`, so the unconditional worktree rule mattered here).
Database confirmed gitignored and **not** staged; the 276 MB of ticker caches are excluded by a
directory-local `.gitignore`.

| | value |
|---|---|
| DB read from | `C:\GitHub\investment-app-slotcount\database\investment_research.db` |
| `price_data` | 3,462,643 rows, 871 tickers, through **2026-08-07** |
| `ticker_data_quality` | 612 CLEAN / 35 NEEDS_REVIEW / 188 QUARANTINED |
| Pool 2 CLEAN | 612 tickers, **608 usable** after `precompute_ticker()` |
| Pool 1 | 503 tickers, **500 usable** |

608/500 usable matches the exit-mechanics study exactly. `^SP500TR` and `^IRX` have **0 rows** in
this local DB — expected, and irrelevant until the return stage.

---

## 1. §7 verification — all checks pass, both pools

| check | Pool 2 CLEAN | Pool 1 |
|---|---|---|
| **7.1** degenerate parity vs raw production `simulate_portfolio_compounding_mtm()` | **0 mismatches** (565 / £348,548.74 both sides) | **0 mismatches** (522 / £429,108.58 both sides) |
| **7.1b** instrumentation purely additive (`instrument=True` vs `False`) | **0 mismatches** | **0 mismatches** |
| **7.2** cash-clamp inert at baseline (`CASH`-bound admissions) | **0** | **0** |
| **7.3** slot cap actually binds (every treated arm) | YES, all four | YES, all four |
| **7.4** ceiling-pinned share vs §3.2's N-invariance | measured, see §4 | measured, see §4 |
| **7.5** decision-engine parity, 30 sampled trades × 10 arms | **300/300 on every check** | included in the 300 |
| **7.6** non-vacuity | every population non-empty; `_require()` guards throughout | ditto |
| **§8** sanity gate, trade **SETS** | **0 mismatches** vs preserved `s3.0` | **0 mismatches** |

Parity is compared on a canonical `(ticker, entry_date, exit_date)` sort and **in memory** — never
insertion order, never across a CSV round trip. The §8 comparison against the preserved reference
file is the one place a 1p tolerance is used, precisely because that reference *is* a CSV round
trip; §7.1 is the exact check.

### 1a. §7.5 needed a fix — to the harness, not the engine

The first run reported `size` at 26–30 of 30 across five arms. Nine sampled decisions differed from
the engine by **exactly £0.01**, never more.

**Diagnosed rather than tolerated.** The engine logs `balance_at_entry_gbp` rounded to 2dp, so the
recomputation's input is known only to ±£0.005; `_var_position_size_core()` then rounds its *output*
to 2dp, which flips the last penny whenever the true value sits near a half-penny boundary. Proven,
not asserted: for every failing case, sweeping the balance across its own ±£0.005 rounding interval
**reproduces the engine's value exactly** (e.g. `AZO@2009-02-19`, VaR-bound, exact at
balance ∈ [30723.815, 30723.818] inside the interval [30723.815, 30723.825]).

The fix **tightens** the test rather than widening a tolerance — a 1p tolerance would also accept a
genuine 1p divergence. The size check now demands an **exact** match, permitting only the known input
imprecision: exact from the rounded balance, or exact at some balance inside that balance's own
rounding interval. Re-run:

| | count |
|---|---|
| sampled decisions | **300** |
| exit reason / entry gate / horizon / entry stop | **300 / 300** each |
| size — exact from the rounded balance | 288 |
| size — exact within the balance's rounding interval | 12 |
| size — **not reproducible** | **0** |

**Known limitation, recorded rather than left implicit:** the engine should emit unrounded
balance/cash so this interval search is unnecessary. Fixing that now would require re-running all ten
arms (~1.5 h) for no change in verdict, so it is left as a noted improvement.

---

## 2. §2 THE MANDATORY GATE

### Pool 2 CLEAN (decisive)

| arm | mean N | max N | occupancy-derived N | positions | mean depl % | Δ depl pp | materiality | deployment |
|---|---|---|---|---|---|---|---|---|
| baseline (uncapped) | **11.78** | 27 | 11.774 | 565 | 65.10 | — | *control* | — |
| n14 | 10.90 | 14 | 10.902 | 505 | 63.44 | −1.66 | **NOT TESTED** | clean |
| n8 | 6.22 | 8 | 6.223 | 298 | 64.16 | −0.94 | **TESTED** | clean |
| n5 | 4.11 | 5 | 4.112 | 178 | 63.78 | −1.31 | **TESTED** | clean |
| n3 | 2.43 | 3 | 2.430 | 84 | 63.50 | −1.60 | **TESTED** | clean |

### Pool 1 (direction only)

| arm | mean N | max N | occupancy-derived N | positions | mean depl % | Δ depl pp | materiality | deployment |
|---|---|---|---|---|---|---|---|---|
| baseline (uncapped) | **11.72** | 26 | 11.714 | 522 | 65.06 | — | *control* | — |
| n14 | 10.93 | 14 | 10.921 | 465 | 63.76 | −1.30 | **NOT TESTED** | clean |
| n8 | 6.23 | 8 | 6.225 | 301 | 64.16 | −0.90 | **TESTED** | clean |
| n5 | 4.06 | 5 | 4.056 | 180 | 63.47 | −1.59 | **TESTED** | clean |
| n3 | 2.43 | 3 | 2.432 | 85 | 63.14 | −1.92 | **TESTED** | clean |

The two pools agree to within 0.06 positions and 0.4pp of deployment on every arm.

**§2.1 counter cross-check.** Mean concurrent positions derived two independent ways — the engine's
added `n_open_positions` counter, and trading-day occupancy computed from the trades file alone —
agree to **≤0.005 positions in all ten arms**. No parity check on *trades* could have caught an error
in the added counter, so this was checked separately.

**Max concurrent equals the cap exactly** in every treated arm (14 / 8 / 5 / 3) — the cap is enforced.

### §2.2 — the deployment guard: the study's central design claim, and it holds

**Every arm sits inside the "clean" tier (≤2pp). The worst divergence anywhere is −1.92pp; the
tightest arm, n3, is −1.60pp on the decisive pool.** No arm is flagged, none excluded.

This is what the position-count study could not achieve and what §3's parameterisation was built
for: **at three concurrent positions this book deploys 63.50% against baseline's 65.10%.** The
concentrated arms are testing concentration, not cash-holding.

Two supporting confirmations:

- **`CASH`-bound admissions: 0 in every arm, both pools** — including n3, where `cash_fraction` was
  raised to 0.7778. The §3.3 relaxation did exactly what it was specified to do (be inert
  everywhere), so it is confirmed *not* a second treatment variable. Falsifiable by construction:
  the binding clamp is recorded per admission, independently of the §3.3 proof.
- **Minimum cash never approached zero**: 16.68%–22.04% of account across all arms. The
  no-negative-cash property holds.

### §2.3 — evaluability

All arms clear the 50-position floor: 565 / 505 / 298 / 178 / **84** (Pool 2 CLEAN) and
522 / 465 / 301 / 180 / **85** (Pool 1). n3 clears it, but not comfortably — 84 positions over 19
years, and that thinness must travel with anything the return stage says about that arm.

Distinct tickers ever held: 238 / 213 / 146 / 97 / **62** (Pool 2 CLEAN).

### §2.4 — capacity lock-up: the feared failure did NOT materialise

The 60%-of-P&L-from-pre-2010 threshold **is not tripped by any arm in either pool**:

| arm | pre-2010 entries | P&L share from pre-2010 entries | verdict |
|---|---|---|---|
| baseline | 275 of 565 | **−0.1%** | ok |
| n14 | 231 of 505 | −1.5% | ok |
| n8 | 138 of 298 | −0.9% | ok |
| n5 | 71 of 178 | +6.4% | ok |
| n3 | 38 of 84 | **+6.9%** | ok |

So the "three names bought in 2008 and held for nineteen years" scenario did not happen. n3's entry
histogram is front-loaded in *count* (2008: 28, 2009: 10 of 84) but those entries contribute
essentially none of the P&L, and the arm keeps entering throughout — 2018: 8, 2019: 9, 2023: 6,
2026: 6. **No arm requires the §9 start-date perturbation as a precondition.**

A striking incidental fact, true of the *baseline* and not caused by this study: 275 of baseline's
565 positions were entered before 2010 and contribute approximately **zero** net P&L in aggregate.

Mean holding period lengthens with concentration: 149.3 → 154.6 → 149.6 → 165.4 → **207.2** days.

---

## 3. A mechanism the gate surfaced that the pre-registration did not anticipate

**The realised mean sits ~78–81% of each nominal cap (6.22 of 8, 4.11 of 5, 2.43 of 3), and the
deployment cap — not the slot cap — is what holds it there.**

Because `per_trade_pct_N = max_total_deployed / N`, the two constraints bind at the same nominal
point by construction; mark-to-market appreciation then pushes deployment to 70% with *fewer* than N
positions open, so capacity blocks the next admission before the slot is filled. Counting days on
which each constraint turned away an otherwise-viable candidate (Pool 2 CLEAN):

| arm | days blocked by capacity only | days blocked by slot cap only | dominant |
|---|---|---|---|
| n14 | 3,286 | 1,188 | deployment cap |
| n8 | 3,360 | 1,183 | deployment cap |
| n5 | 2,338 | 2,240 | deployment cap (marginal) |
| n3 | 1,947 | 2,675 | **slot count** |

**This does not undermine the TESTED verdicts.** The materiality test asks whether an arm built a
portfolio near its own target rather than near baseline's, and 6.22 is far nearer 8 than 11.78. The
slot cap is unambiguously what sets the count — without it the book runs at 11.78. But the honest
statement of what these arms are is *"a book capped at N slots that in practice runs at ~0.8N because
capacity binds first"*, not *"a book of exactly N positions"*, and that phrasing should travel with
any return figure.

---

## 4. Two pre-registration figures were wrong, both corrected here

**(a) §1a's baseline concurrent-position estimate.** I estimated ~17.1 from
`565 × 149 ÷ 4,930`. `holding_days` is **calendar** days, not trading days. Measured: **11.78**
(Pool 2 CLEAN) / 11.72 (Pool 1), confirmed by the independent occupancy derivation.

Consequence, and it is not cosmetic: **baseline sits *below* 14, so `n14` caps a book that already
averages 11.78, and it comes back NOT TESTED in both pools** — realised 10.90 is 0.87 from baseline's
11.78 and 3.10 from its own target of 14. §1a promoted `n14` to a treated arm calling it "the
cleanest attribution point in the study"; that reasoning was built on the wrong number, and the arm
is immaterial rather than clean. It is not inert (max concurrent reaches 14, and the cap turned
candidates away on 1,188 days) — it simply does not build a portfolio distinguishable from baseline's,
which is the correct reading of NOT TESTED.

The §8 sanity gate independently forced the right structural call — an uncapped control — so the arm
design stands; only the stated rationale for `n14` was wrong.

**(b) §3.2's ceiling-pinned prediction.** I predicted the arms would inherit baseline's "~83%"
pinned rate, citing the live queue snapshot. Measured baseline pinned share is **33.1%** (Pool 2
CLEAN) / 37.2% (Pool 1). The 83% figure describes *queued candidates*; *admitted* positions are the
rank leaders, which are higher-volatility and therefore more often VaR-bound.

§3.2's actual invariance claim survives, and was worth making: the threshold `|q05| ≤ 0.03652` is
N-independent, and the measured pinned share drifts only modestly with N —
33.1 → 36.4 → 33.6 → 29.8 → 29.8% (Pool 2 CLEAN), 37.2 → 41.7 → 34.2 → 30.6 → 28.2% (Pool 1). A ~3–9pp
drift, not a regime change. VaR sizing has **not** collapsed into equal-weighting at 3 slots: 70% of
n3's admissions are still VaR-bound below the ceiling.

---

## 5. One untested path, stated rather than left implicit

Both pools are stocks only, so `_admit_group()`'s ETF group is always empty and the shared slot
counter is never exercised **across** the two group passes within a day. The counter is
portfolio-wide by construction and correct by inspection, but that specific interaction has no
empirical coverage here. It cannot affect any result in this study.

---

## 6. Status

Verification and the mandatory gate are complete. **Nothing about returns has been computed.**

The next step, if approved, is §4: fetch `^SP500TR`/`^IRX` read-only, model FX and idle-cash costs
per arm, and compute cost-adjusted excess return, drawdown, outcome concentration, single-ticker
dominance, overlap with its randomisation null, and the §4.7 instruments — for the three TESTED arms
(n8, n5, n3), with n14 reported as NOT TESTED rather than as a null.

**Files**: `PREREGISTRATION.md`, `slot_engine.py`, `step0_env_check.py`, `step1_build_caches.py`,
`step2_verify.py`, `step2b_decision_parity.py`, `step3_run_arms.py`, `step4_gate.py`,
`step2_verification.json`, `step2b_decision_parity.json`, `step3_summary.json`, `step4_gate.json`,
`trades_{pool}_{arm}.csv`, `panel_{pool}_{arm}.csv`, `admissions_{pool}_{arm}.csv`,
`slotfull_{pool}_{arm}.csv`, `rejections_{pool}_{arm}.csv`.

**Backtest convention**: continuous walk, 2007-01-03 → 2026-08-07, every arm from an identical
£50,000 start; mark-to-market deployment accounting throughout. **No database writes of any kind.**
No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path.
