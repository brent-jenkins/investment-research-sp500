# Exit-mechanics winner — paired perturbation test (2026-08-12)

**Verdict: ROBUST. 70 of 70 (draw × end-date) combinations favour the winning cell, every one of them
above the ~1.08pp noise floor.** The worst value anywhere in the test is **+3.400pp — 3.1× the
floor**. The largest single-name dependency (NVDA, which carries 17.0% of the cell's P&L) compresses
the advantage to **67% of typical without coming close to erasing it**.

Tests `s3.0` (stop 3.0× ATR, unlimited max-holding, no trailing) against the live baseline `h30`
(stop 2.0×, 30-day cap), Pool 2 CLEAN. Unperturbed full-window delta: **+6.443pp**.

**Research only. Nothing adopted, nothing merged, no live-path or config change, no database writes.**

---

## 1. Design

**Paired by construction**: every draw feeds the identical perturbed ticker list, start date and
tie-break order to **both** arms in the same process, from the same in-memory perturbed cache. The
quantity of interest is the delta's own distribution — never two independently-perturbed runs
compared after the fact.

**14 draws × 5 end dates = 70 combinations**, across four dimensions. Two were added at the user's
direction and both changed what the test could show:

| dimension | draws | why |
|---|---|---|
| `drop_ticker` | 8 | **Targeted at the top-10 P&L contributors, not random** (§3) |
| `shift_start` | 3 | walk start moved +3/+7/+14 days |
| `shuffle_tiebreak` | 3 | candidate insertion order reshuffled |
| **`end_date`** | ×5 per draw | **New axis, applied to every draw** (§4) |

**Why end-date truncation is exact rather than an approximation.** The simulation is a strictly
sequential day-by-day walk, so truncating the input data at date D produces trades identical to the
full run's up to D, plus `END_OF_DATA` exits at D for whatever is still open. Those exits realise at
the same close the panel already marked the position at, so the equity curve up to D is
byte-identical either way. Re-running each window would have cost ~15 min/cell to reproduce a series
already in hand. The one thing truncation does not provide is a per-window trades file, so per-window
ticker attribution is done on the full-window trades.

---

## 2. The distribution

**Over all 70 (draw × end-date) combinations:**

| min | p10 | median | p90 | max |
|---|---|---|---|---|
| **+3.400** | +4.858 | **+6.048** | +6.706 | +6.949 |

**Positive: 70/70. Above the 1.08pp noise floor: 70/70.**

**Full-window deltas only (14 draws):** min **+4.316**, median **+6.286**, max +6.706, **14/14
positive**. The unperturbed +6.443 sits just above the median — not an outlier the perturbations
happen to bracket.

---

## 3. Single-ticker dominance — the explicit check

The concentration made this necessary rather than optional: **NVDA alone carries 17.0% of the cell's
£348,549, the top 3 carry 37.0%, and the top 10 of 238 tickers carry 73.6%.** Random single-ticker
drops — the method the relative-rank robustness test used — would have been **mostly no-ops here**
and would not have tested dominance at all. The 8 drop draws therefore target the top contributors
directly.

| dropped | full-window Δ | % of unperturbed | worst window |
|---|---|---|---|
| **NVDA** | **+4.316** | **67.0%** | **+3.400** |
| **AVGO** | +4.369 | 67.8% | +4.369 |
| WDC | +5.525 | 85.8% | +4.978 |
| MU | +6.045 | 93.8% | +5.606 |
| STX | +6.051 | 93.9% | +5.156 |
| GEV | +6.083 | 94.4% | +5.606 |
| GE | +6.128 | 95.1% | +5.480 |
| CAH | +6.486 | **100.7%** | +5.838 |

**NVDA and AVGO are real, named dependencies** — each removes roughly a third of the edge. Nothing
else moves it more than ~14%, and dropping CAH slightly *improves* it.

**Against the precedent this project already has**: the relative-rank study's AMAT dependency
compressed that result to **~21% of typical and below the ~1.08pp noise floor**, and was correctly
reported as a magnitude dependency that made the direction, not the point estimate, the trustworthy
claim. **This result is materially stronger**: its worst single-name case retains **67%** and its
worst value in any window (+3.400pp) is **3.1× the floor**. No single name in the top-10 contributor
set is capable of erasing the advantage.

---

## 4. The end-date axis — systematic, not noisy

| cutoff | min | median | max | positive |
|---|---|---|---|---|
| 2022-08-05 | +3.917 | +5.851 | +6.176 | 14/14 |
| 2023-08-04 | **+3.400** | +5.606 | +5.911 | 14/14 |
| 2024-08-02 | +4.321 | +6.148 | +6.706 | 14/14 |
| 2025-08-01 | +4.884 | **+6.673** | **+6.949** | 14/14 |
| 2026-08-07 | +4.316 | +6.286 | +6.706 | 14/14 |

**The pattern is consistent across all 14 draws** — weakest at the 2022–2023 cutoffs, strongest at
2025-08-01 — which means it reflects the window itself rather than any particular name. **The
advantage is smallest, not largest, at the earliest windows**, i.e. it is not an artefact of ending
the walk with the AI-rally names near highs. That was the specific concern the §7 unrealised-marks
finding raised in the main report, and this axis settles it: the relative advantage predates those
positions entirely.

---

## 5. A dimension that tested nothing, reported as such

**All three `shuffle_tiebreak` draws returned +6.443pp — identical to the unperturbed run, to three
decimals, and identical to each other.** The shuffle changed neither arm's trade count (baseline
stayed at exactly 4,876).

The cause is mechanical: admission sorts on `(-rank_value, insertion_idx)`, and `relative_rank` is a
continuous float, so **exact ties essentially never occur and the tie-break never binds**. The
dimension is inert for this system.

**This is worth recording rather than quietly counting three draws as three successes.** It is a mild
instance of the standing "check that reports clean for a reason unrelated to what it was checking"
class: three of fourteen draws contribute no information about robustness, and treating 14/14 as
fourteen independent confirmations would overstate the evidence. **The honest count is 11 informative
draws, not 14.** Notably, the relative-rank robustness study used the same dimension and reported its
four shuffle draws landing on exactly +1.993pp — the same phenomenon, apparently not noticed there.

`shift_start` is nearly inert too, though for a different and benign reason: the candidate universe's
price history begins ~2007-01-03 regardless, so shifting the nominal start by 3–14 days changes little
about which trading days are actually available. Its three draws (+6.698/+6.700/+6.706) are tightly
clustered and marginally *above* unperturbed.

**So the test's real discriminating power comes from the 8 targeted ticker drops and the 5 end-date
windows** — which is exactly where the two requested additions were aimed.

---

## 6. What this does and does not establish

**Establishes**: the winning cell's advantage over the live baseline is not attributable to any single
name in its top-10 contributor set, to the choice of window end date, to the walk's start date, or to
admission tie-break order. Across 70 combinations it never goes negative and never falls inside the
noise floor.

**Does not establish**, and these are unchanged by any amount of perturbation testing:

1. ~~**The unrealised-marks qualification stands.** 59.9% of the cell's P&L is still mark-to-market on
   34 positions open at the walk's end; on realised P&L alone it remains worse than baseline
   (£139,863 vs £154,234). Perturbation testing measures stability, not realisation.~~
   **WITHDRAWN 2026-08-12** — correct on its own terms (perturbation testing does not measure
   realisation) but the underlying caveat was wrong. The engine already force-closes at the walk's
   end and the headline derives from that equity curve, so +6.443pp was already fully realised; the
   "realised" figure excluded `END_OF_DATA` trades, removing 59.9% of the winner's P&L against 7.6%
   of baseline's. A dedicated force-close test (`research/force_close_2026-08-12/REPORT.md`) leaves
   the winner ahead **5/5 cutoffs in both pools**, total P&L 2.0–2.3× baseline's, rule-closed P&L
   alone ahead at every intermediate cutoff. Items 2–5 below are unaffected.
2. **The absolute claim remains unsupportable.** s3.0 clears `^SP500TR` at exactly one end date and
   trails at the other six. The robust claim is relative, not absolute.
3. **Concentration is a real risk property**, not a measurement artefact: 565 positions over 19 years
   at 149-day mean holds, 73.6% of P&L in 10 of 238 tickers. That the *comparison* is robust does not
   make the *portfolio* diversified.
4. **Live feasibility is untested.** Unlimited holding interacts with the live re-entry, membership
   and reconciliation paths in ways no backtest exercises.
5. **Pool 1 was not perturbed** (Pool 2 CLEAN only, matching the relative-rank robustness precedent).
   Pool 1's unperturbed delta is +5.381pp, agreeing in direction and magnitude.

---

## 7. Files

`step7_perturbation.py`, `step8_aggregate_perturbation.py`, `pert_d01..d14.json`,
`perturbation_summary.json`, `_log_pert_*.txt`.

**Database state**: local research copy, `price_data` through 2026-08-07, 612 CLEAN / 608 usable,
reconfirmed at run time. `^SP500TR`/`^IRX` read-only from a research-local cache. No database writes.
