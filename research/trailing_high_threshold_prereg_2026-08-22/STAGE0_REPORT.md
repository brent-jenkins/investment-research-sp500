# Stage 0 — realised effect size (2026-08-22)

**Read-only. No arm simulated, no engine invoked, no database written.** Measured entirely from the
preserved baseline trade sets plus stored close prices, per §3 of the pre-registration.

**Reported before any return figure, as pre-registered. No cell has been run.**

---

## 0. Rulings and state, confirmed before measuring

**Rulings fixed** (recorded in `PREREGISTRATION.md` before this ran): §4.3 ATR is **recomputed as of
the current date**; §4.4 effective stop is **`max(entry_stop, trail)`**. A new verification item **V7**
was added as a consequence — the four reused unit tests were written against the frozen-entry
convention and their carry-over must be *verified*, not assumed.

**Database state, reconfirmed by direct query at run time** (§13.2), never inherited:

| | value | sweep's record |
|---|---|---|
| `price_data` | 3,462,643 rows / 871 tickers, **2007-01-01 .. 2026-08-07** | through 2026-08-07 ✓ |
| `ticker_data_quality` | **612 CLEAN** / 188 QUARANTINED / 35 NEEDS_REVIEW | 612 CLEAN ✓ |
| Pool 2 CLEAN usable | **608 / 612** | 608 ✓ |
| Pool 1 usable | **500 / 503** | 500 ✓ |

**The 2026-08-07 snapshot is the correct state for this study, not a stale one.** The reference set
was built from it, and its `END_OF_DATA` exits are all dated 2026-08-07; running against production's
current 2026-08-21 data would revise every one of them and break the §7 sanity gate by construction.

**One discrepancy, diagnosed and closed — my own error, not a data finding.** The first state query
reported Pool 2 at 647 usable against the sweep's 608. Cause: it used
`get_research_stock_candidates()` alone, where `step1_build_caches.py` defines the roster as
`get_research_stock_candidates() ∩ {status='CLEAN'}`. Recomputed correctly, both pools match the
sweep exactly. Recorded rather than silently fixed, since "reconfirm, don't inherit" is worth nothing
if a mismatch gets quietly adjusted until it agrees.

**SANITY GATE (§7): PASSED.** Pool 2 CLEAN = **565 trades / £348,548.74**, exact. Consistency check
`peak_gain ≥ pnl_pct` holds for **565/565** and **522/522** — 0 violations, both pools.

---

## 1. THE HEADLINE — three arms are testable, one is not

**§3.2 materiality floor (`n_reached ≥ 30`), fixed before any number was seen:**

| arm | Pool 2 CLEAN `n_reached` | Pool 1 | verdict |
|---|---|---|---|
| **B30** | **127** (22.48%) | 116 (22.22%) | **TESTED** |
| **B50** | **92** (16.28%) | 87 (16.67%) | **TESTED** |
| **B100** | **55** (9.73%) | 54 (10.34%) | **TESTED** |
| **B200** | **29** (5.13%) | **25** (4.79%) | **NOT TESTED — fails the floor in both pools** |

**B200 lands at 29 against a floor of 30. One position short.**

I am not moving the floor, and the fact that it is tempting is the entire reason it was fixed in
advance. It fails more clearly on Pool 1 (25), so both pools agree on the verdict rather than the
decisive pool alone producing a marginal miss. Per §3.2 and walk-away clause (2), **B200's eventual
return figure will be reported as NOT TESTED and will not be interpreted as a null, in either
direction.** Note also that this is only the *first* half of the gate — `n_fired ≥ 10` is still
unknown and can only come from the run, and n_fired ≤ n_reached necessarily.

**Prior 3 was half right.** It predicted B200 would likely fail materiality and possibly B100. B200
fails; **B100 passes comfortably at 55**, so three of four arms are testable rather than two.

---

## 2. THE PRIZE IS LARGE — and this materially strengthens the hypothesis

The pre-registration called the prior "unfavourable." On the giveback evidence it is **less
unfavourable than assumed**, and this is the finding that most changes the picture.

**Pool 2 CLEAN — how much of the peak is surrendered before the position actually exits:**

| arm | median giveback (peak → exit) | mean | median fraction of peak KEPT |
|---|---|---|---|
| B30 | **48.51 pp** | 60.58 pp | **0.455** |
| B50 | **58.04 pp** | 70.73 pp | 0.503 |
| B100 | **79.34 pp** | 91.22 pp | 0.568 |
| B200 | **86.28 pp** | 108.36 pp | 0.655 |

Pool 1 agrees closely throughout (46.93 / 59.21 / 73.82 / 105.79 pp).

**The median position that reaches +30% surrenders roughly HALF its peak gain before exiting.** That
is a large, real prize — not a marginal one — and it is what a trailing stop exists to capture. There
is unambiguously something there to protect.

**And there is a long window in which to act.** Days from entry to first crossing, against total hold:

| arm | median days to cross | median total hold | **post-activation window** |
|---|---|---|---|
| B30 | 90 | 406 | **316 days (78% of the hold)** |
| B50 | 146.5 | 579.5 | 433 days (75%) |
| B100 | 356 | 757 | 401 days (53%) |
| B200 | 617 | 1,039 | 422 days (41%) |

The trail is armed for the majority of the holding period in every arm. This is not a mechanism that
would barely get a chance to fire.

---

## 3. THE COUNTERWEIGHT — the giveback is substantially DEFINITIONAL, not carelessness

**`SIGNAL_OFF` dominates the exits of the affected population at every threshold:**

| arm | SIGNAL_OFF | END_OF_DATA | CRISIS_EXIT | STOP_LOSS |
|---|---|---|---|---|
| B30 (n=127) | **102 (80.3%)** | 10 | 8 | 7 |
| B50 (n=92) | **78 (84.8%)** | 10 | 3 | 1 |
| B100 (n=55) | **45 (81.8%)** | 10 | 0 | 0 |
| B200 (n=29) | **19 (65.5%)** | 10 | 0 | 0 |

`SIGNAL_OFF` fires when 12-month momentum turns negative — which **requires** a large fall from a
recent peak. So a substantial part of that 48–86pp giveback is not the system being careless with
gains; **it is the mechanical price of exiting on a 252-day signal.** A position cannot turn its
12-month momentum negative without first giving back a great deal.

**This cuts both ways, and both directions are stated because the study has not yet run:**

- **For the hypothesis** — a trail exits on a *path* condition rather than a 252-day lookback, so it
  could plausibly capture much of that giveback. This is precisely the structural gap the study was
  built on: the giveback is a known, quantified cost of the current exit rule.
- **Against it** — the exit sweep already established that `SIGNAL_OFF` is the winning
  configuration's dominant exit (52.74%) and that converting those exits into earlier ones cost
  ~5.4pp at low thresholds. The giveback may be the *price of admission* to the right tail rather
  than a recoverable loss, and prior 7 anticipated exactly this.

Stage 0 cannot separate these. §5.2's protected-versus-forgone decomposition is what can, and it
remains the actual question.

---

## 4. THE CONCENTRATION FINDING — "not testable" does NOT mean "harmless"

**The affected population carries more than the strategy's entire P&L, at every threshold.**

| arm | their P&L (Pool 2 CLEAN) | **share of total £348,548.74** |
|---|---|---|
| B30 (n=127) | £435,208.30 | **124.86%** |
| B50 (n=92) | £432,877.68 | **124.19%** |
| B100 (n=55) | £395,809.22 | **113.56%** |
| B200 (n=29) | £333,576.05 | **95.70%** |

Shares above 100% are arithmetic, not error: the 438 positions that never reach +30% are **net
negative in aggregate (−£86,659.56)**. The positions these arms act on *are* the strategy's return.

**This produces a tension worth stating explicitly, because it is easy to misread.** B200 is
simultaneously **too small to test (n=29)** and **acting on 95.7% of all P&L**. Those are not in
conflict — they are the same concentration property seen from two sides — but it means **NOT TESTED
must not be read as SAFE**. B200 is the arm whose effect is least measurable and whose potential
impact is greatest. If anything, that is an argument for *not* deploying such a rule on evidence this
thin, not for relaxing the floor to get a number.

**Supporting distribution** (Pool 2 CLEAN peak_gain): p10 = p25 ≈ **0.0000** — at least a quarter of
positions never close above their entry price at all — median **+5.49%**, p75 +23.87%, p90 +92.28%,
p95 +200.90%, p99 +365.60%, max **+994.82%**, mean +35.70%. Extreme right skew, consistent with the
28.85% win rate.

---

## 5. What this does and does not settle

**Settles:** three arms are testable and one is not; the floor was fixed in advance and is applied as
written; the prize is large and the post-activation window is long; the affected population is the
strategy's entire return.

**Does not settle, and Stage 0 structurally cannot:**

1. **`n_fired`** — the second half of the materiality gate. §3.4 fixed this in advance: Stage 0 is an
   **upper bound on the affected population, never a prediction of firings.** A trail that arms and
   never fires changes nothing.
2. **Whether the giveback is recoverable.** §3's `SIGNAL_OFF` dominance says a large part of it is
   the definitional cost of a slow signal. Only the paired protected-versus-forgone decomposition can
   apportion it.
3. **Capacity turnover.** Unchanged and still expected to be large (§5.3) — under unlimited holding
   with capacity locked since 2009, an early exit opens a slot shut for a decade.
4. **§5.1's structural concern is now quantified rather than hypothetical.** B100 acts on 55
   positions and B200 on 29; whether a portfolio-level Δ from populations that size can clear the
   ~1.08pp floor at all remains open, and a null must still be reported as non-discriminating rather
   than as evidence of no effect.

---

## 6. THE PATH CHECK — `n_fired` estimated, and it settles the gate question

Run on direction. Per-position path replay only: **no portfolio, no admission, no capacity, no
cascade, no arm.** Each position's own daily bars are walked from entry to its *actual* baseline exit
under both rulings (current-date ATR, `max(entry_stop, trail)`) and the §4.2 anti-lookahead ordering,
asking whether the trail would have been touched first. Fill is `min(stop, open)`, matching the
production realistic-stop model.

| | | **Pool 2 CLEAN** | | | | **Pool 1** | | | |
|---|---|---|---|---|---|---|---|---|---|
| T | reached | **fired** | rate | better:worse | median Δ | reached | **fired** | rate | median Δ |
| +30% | 127 | **125** | 98.4% | 58:67 | −4.15 pp | 116 | **115** | 99.1% | −5.51 pp |
| +50% | 92 | **92** | 100% | 35:57 | −7.78 pp | 87 | **87** | 100% | −10.34 pp |
| +100% | 55 | **54** | 98.2% | 25:29 | −12.74 pp | 54 | **54** | 100% | −9.58 pp |
| +200% | 29 | **29** | 100% | 16:13 | **+2.73 pp** | 25 | **25** | 100% | **+2.73 pp** |

### 6.1 The gate: `n_fired ≥ 10` passes everywhere, so B200 fails on `n_reached` alone

**Once armed, the trail essentially always fires — 98–100% at every threshold, both pools.** This is
the expected consequence of §3: `SIGNAL_OFF` needs 12-month momentum to turn negative, a far larger
retracement than a 3.0× ATR trail tolerates, so the trail is virtually certain to be touched first.

**My own speculation was wrong and is corrected rather than quietly dropped.** §6 of the previous
draft suggested n_fired for B200 "might be, say, 6," which would have made it fail both halves
decisively. It is **29 — every position that reached the threshold**. B200's NOT TESTED verdict rests
**entirely and solely** on `n_reached = 29 < 30`, a single position, on the first half of the gate.
That is a marginal miss and should be described as one.

### 6.2 The direct effect is strongly negative — and the shape is the whole story

Aggregate Δ across all fired positions (sum of per-position pp differences vs the baseline exit):
**−5,461 / −4,771 / −2,934 / −1,077** pp at B30/B50/B100/B200 on Pool 2 CLEAN; **−5,705 / −4,769 /
−2,976 / −1,199** on Pool 1. Negative at every threshold in both pools. Mean per fired position:
**−37 to −55 pp**.

**The most informative number in this table is the sign disagreement between the median and the
mean at B200:**

| | B200, Pool 2 CLEAN |
|---|---|
| better : worse | **16 : 13** — the trail wins *more often* than it loses |
| median Δ | **+2.73 pp** — the typical firing is a small improvement |
| **mean Δ** | **−37.13 pp** — the average firing is a large loss |

**The trail wins slightly more often and loses catastrophically when it loses.** That is the
right-tail signature exactly, and it replicates the exit sweep's +5%/+10% finding — and Arm C's
before it — at a threshold nobody had tested. It is also why a win-rate-based read of this mechanism
is actively misleading: on counts alone B200 looks like a modest improvement.

### 6.3 What this does NOT settle — capacity turnover, and it could be large

Each firing releases capital a **median 260–400 days** before the position would otherwise have
closed. Across 54–125 firings under a regime where capacity has been locked since 2009 (§5.3), that
is a great deal of freed capital-time, and the path check **cannot see any of it** — it holds the
exit date fixed at baseline's and never redeploys a penny.

So the direct effect being decisively negative does **not** decide the study. It does sharpen a
pre-registered trap: if an arm nonetheless wins at portfolio level, the win must come from capacity
turnover rather than from better exits — and **walk-away clause (5) disqualifies exactly that**. The
pre-registration anticipated this case before any of these numbers existed.

**Stated limit**: this estimates the direct effect on the *baseline* population. In a real arm run
the population itself differs, because an early exit changes every subsequent admission.

---

## 7. Files

`step0_confirm_state.py`, `step0b_reconcile_pools.py`, `step1_stage0_effect_size.py`,
`stage0_results.json`, `stage0_peaks_pool_2_clean.csv`, `stage0_peaks_pool_1.csv`.

**No cell has been run. No `analysis/` or `config.yaml` change. No database writes. Nothing adopted.**
