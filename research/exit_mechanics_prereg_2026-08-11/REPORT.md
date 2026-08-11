# Exit-mechanics sweep — results (2026-08-12)

**Verdict: the winning cell is `stop 3.0× ATR + unlimited max-holding + no trailing stop`, at
+6.443pp (Pool 2 CLEAN) / +5.381pp (Pool 1) cost-adjusted excess return over baseline. No walk-away
clause fires. The trailing stop fails decisively, exactly as its honest prior predicted.**

**This is a research finding, not an adoption.** Nothing changed in `analysis/signals.py`,
`analysis/decision.py`, `config.yaml`, or any live path. Per the walk-away sentence, a cell firing no
clause has earned a 20-draw paired perturbation robustness test — not adoption — and a real promotion
would additionally need the production-module and four-parity-layer work the relative-rank promotion
required.

**Two findings qualify the headline and must travel with it** (§6 and §7): the winning cell holds
**565 positions over 19 years at a mean 149 days**, and **59.9% of its P&L is unrealised
mark-to-market on 34 positions still open when the walk ends**. On realised P&L alone it is *worse*
than baseline. The advantage over baseline is robust to end date; the claim to beat the index outright
is not.

---

## 1. Baseline, and why the earlier stop study was re-tested rather than inherited

Baseline is the **current live system**: relative-rank admission (Arm B, adopted 2026-08-11),
`per_trade_pct=0.05`, `max_total_deployed=0.70`, `atr_stop_multiplier=2.0`,
`stock_max_holding_days=30`. Preserved sets: **Pool 2 CLEAN 4,876 trades / £166,830.11**, **Pool 1
4,757 / £267,685.07**.

The stop-loss reconfiguration study (2026-08-07) ran against absolute-momentum ordering, a
1,043-ticker universe, and a pre-adjudication quarantine state. Its ranking was **no-stop > 3.0× >
2.0×**. **This study finds 3.0× beats BOTH, in both pools** (§4) — the ordering inverts. That is a
direct vindication of not inheriting it, and of §0's argument for sweeping the dials together: the
stop's optimum depends on the holding regime it sits in.

---

## 2. Verification — all gates passed before any cell was trusted

| Check | Pool 2 CLEAN | Pool 1 |
|---|---|---|
| §7.1 Degenerate parity vs raw production | **byte-identical, 0 mismatches** (4,876 / £166,830.11) | **byte-identical, 0 mismatches** (4,757 / £267,685.07) |
| §7.5 Zero `TRAILING_STOP` when activation off | 0 ✓ | 0 ✓ |
| §8 Sanity gate, **trade SETS** | **identical** | **identical** |
| §7.3 Trailing unit tests | 4/4 PASS, non-vacuous | — |

**§7.3 (d), the anti-lookahead demonstration, passed**: a day-3 low of 114 against yesterday's trail
of 113 correctly does **not** exit, where the wrong ordering would have raised the trail to 119 first
and exited.

**A verification failure worth recording, because it is the standing class occurring inside the
verification itself.** The first unit-test run had `is_member()` rejecting every synthetic ticker, so
**test (a) passed vacuously on two empty trade lists**. It surfaced only because test (b) indexed
`[0]` and raised. Fixed at the cause (patch `is_member`) and at the class: `_require()` now asserts
every test produced a trade, so a vacuous pass cannot recur. **A test that has not been observed
producing a non-empty result has not been observed at all.**

---

## 3. STAGE 0 — the `MAX_HOLDING` round trip, measured before any cell ran

Read-only, from the preserved baseline sets. Method reused verbatim from the earlier study's whipsaw
quantification, with the exit reason swapped.

| | Pool 2 CLEAN | Pool 1 |
|---|---|---|
| `MAX_HOLDING` exits | **2,383 (48.9% of all exits)** | 2,361 (49.6%) |
| P&L on those exits | +£729,826 | +£993,919 |
| Re-entered same ticker within **1 trading day** | **37.4%** | 37.4% |
| within 30 td | **78.6%** | 78.0% |
| Median gap to re-entry | **2 trading days** | 2 |
| Combined pair P&L | **+£710,791** | +£963,738 |
| Net-positive : net-negative pairs | 1,423 : 449 | 1,409 : 432 |
| **FX paid on the round trips** | **£38,236** | £52,205 |

**The asymmetry against the stop-loss reference is the finding.** On the same trade set,
`STOP_LOSS` re-entry pairs combine to **−£399,571** — the earlier study's result, replicated — while
`MAX_HOLDING` re-entry pairs combine to **+£710,791**. Opposite signs. A stop fires on positions
that are genuinely deteriorating; `MAX_HOLDING` fires on positions that are still working, and 37%
are re-bought the next day. The £38,236 of FX is ~23% of the strategy's entire realised P&L, spent
re-acquiring positions it had just been forced to sell.

As pre-registered, this **sizes** an opportunity and does not price it — and the sweep then priced
it at roughly ten times this figure, because the round-trip FX is only part of what the cap costs.

---

## 4. Stages 1–3 — the full curves, both pools

Cost-adjusted excess return over `^SP500TR`, **Δ vs baseline** (the primary criterion; bar +1.5pp,
carry-forward floor 1.08pp):

| cell | stop | hold | trail | **Pool 2 CLEAN Δ** | **Pool 1 Δ** | trades (P2) | maxDD (P2) | FX pp/yr (P2) |
|---|---|---|---|---|---|---|---|---|
| h30 *(baseline)* | 2.0× | 30 | none | 0.000 | 0.000 | 4,876 | −47.51 | 3.331 |
| h60 | 2.0× | 60 | none | +0.938 | +0.441 | 3,694 | −40.78 | 2.030 |
| h90 | 2.0× | 90 | none | +0.519 | +0.659 | 3,424 | −38.41 | 1.581 |
| **hunlimited** | 2.0× | **unlim** | none | **+3.634** | **+3.413** | 712 | −48.26 | 0.350 |
| **s3.0** ← winner | **3.0×** | **unlim** | none | **+6.443** | **+5.381** | **565** | −43.88 | **0.268** |
| snone | none | unlim | none | +4.577 | +3.944 | 449 | −43.18 | 0.215 |
| t05 | 3.0× | unlim | **+5%** | +1.004 | −0.016 | 2,388 | −43.58 | 1.591 |
| t10 | 3.0× | unlim | **+10%** | +1.062 | +1.418 | 2,063 | −44.76 | 1.372 |

**Both pools produce the identical ranking**: s3.0 > snone > hunlimited > h60/h90 > h30. Cross-pool
agreement this complete is unusual in this project's record.

**Stage 1 — max-holding.** `unlimited` clears the carry-forward floor by a wide margin and is
carried. The mechanism is exactly Stage 0's: FX drag collapses 3.331 → 0.350 pp/yr as trade count
falls 4,876 → 712.

**Stage 2 — the stop dimension is NON-MONOTONIC, in both pools.** 2.0× (+3.634) < **3.0× (+6.443)**
> none (+4.577). An interior optimum, and the 2.8pp gap between 3.0× and 2.0× is well outside the
~1.08pp floor. A bounded stop still adds value once positions are held indefinitely — but only at
3.0×, not at the 2.0× the live system uses.

**Stage 3 — the trailing stop fails, and the honest prior is confirmed.** It costs **~5.4pp**,
collapsing the winning construction to barely-above-baseline. `TRAILING_STOP` accounts for 1,420 of
2,388 exits at +5% activation; trade count rises 565 → 2,388; mean hold falls 149 → 42 days. **Win
rate RISES (28.85% → 42.46%) while returns fall** — Arm C's finding, replicated precisely. Cutting
winners short produces more frequent, smaller wins in a strategy that lives on the right tail.
Neither activation threshold approaches the floor, so **"none" carries forward**.

### Exit composition — the interaction is visible, as §6 required

| cell | MAX_HOLDING | STOP_LOSS | TRAILING_STOP | SIGNAL_OFF |
|---|---|---|---|---|
| h30 | **48.87%** | 44.89% | — | 4.45% |
| h60 | 36.57% | 53.17% | — | 7.44% |
| h90 | 29.09% | 57.56% | — | 10.05% |
| hunlimited | **0%** | 49.58% | — | **39.47%** |
| s3.0 | 0% | 33.10% | — | **52.74%** |
| t05 | 0% | 27.18% | **59.46%** | 9.72% |

Every dial moves the composition in the direction it predicts: longer holding converts `MAX_HOLDING`
into `SIGNAL_OFF`; the trailing stop converts `SIGNAL_OFF` winners into `TRAILING_STOP`. **No cell's
headline moved without its own composition shift**, so no result is treated as a cascade artefact.

---

## 5. §5.4 BACK-CHECK — staging did not bias the result

The non-carried max-holding values re-run at **final settings** (stop 3.0×, no trail), Pool 2 CLEAN:

| | Stage 1 (stop 2.0×) | Back-check (stop 3.0×) |
|---|---|---|
| h30 | 0.000 | +0.787 |
| h60 | +0.938 | **+2.001** |
| h90 | +0.519 | +0.805 |
| unlimited | **+3.634** | **+6.443** |
| **ranking** | **unlimited > h60 > h90 > h30** | **unlimited > h60 > h90 > h30** |

**Identical ranking.** The staged design's own truncation did not change which max-holding value
wins, and `unlimited` leads by a wider margin at final settings than at Stage 1 settings. Per §5.4
this is reported as staging **validated for this sweep specifically** — not in general.

---

## 6. The paired per-decision instrument — PRIMARY evidence, and it required two extra tests

The raw instrument looked damning: on 326 shared `(ticker, entry_date)` keys, `unlimited` produced a
better outcome only **11.96%** of the time, with top-decile Jaccard 0.022 and 69.8% of P&L from
non-shared trades. Two clauses appeared live. Both were tested rather than argued.

**Test 1 — expected overlap by construction.** A randomization null conditioned on the 418 dates the
sparse arm admitted on, drawing from each date's real gate-passing eligible set (median 162
candidates), 1,000 draws:

| | shared keys |
|---|---|
| Null expectation | **81.8** (sd 5.8; p5 73, p95 92) |
| **Observed** | **326 — the 100th percentile, ~4× chance** |

Low overlap is **forced by construction** (712 vs 4,876 positions cannot overlap much) and is in fact
far *higher* than random assignment produces. **The clause does not fire.**

**Test 2 — the shared set is a biased slice, and in the predicted direction:**

| baseline trades | n | median hold | win% | mean P&L% | `MAX_HOLDING` share |
|---|---|---|---|---|---|
| **Shared** | 326 | **9 d** | **26.69** | **−1.945** | **27.9%** |
| Not shared | 4,550 | **30 d** | 44.95 | +1.063 | **50.4%** |

The shared population is baseline's *short, failing* positions — the ones the cap never bound on,
where holding longer has nothing to offer.

**The decomposition is the crux.** Of 326 shared keys, **235 (72.1%) are byte-identical trades** —
the arms never diverged. Only **91 diverged, all with baseline exit reason `MAX_HOLDING`**, which is
the only mechanically possible divergence. On those 91: unlimited better 42.9% of the time, **mean
delta +27.1pp**, p90 **+107.8pp**, and P&L on the *same entries* goes **£20,669 → £85,379 (4.1×)**.
The 11.96% figure was scoring 235 identical trades as "not better".

**The limit, held to deliberately**: 91 comparable decisions is a small sample. I pre-registered a
100-trade floor for *differing* trades (4,550/386 clear it) but not for this subset, and I will not
promote 91 to strong evidence having now seen which way it points. **The instrument corroborates
weakly; the portfolio result stands substantially on its own.**

---

## 7. The qualification that must travel with the headline

**59.9% of the winning cell's P&L is unrealised mark-to-market on 34 positions still open when the
walk ends** (baseline: 7.6%).

| cell | total P&L | `END_OF_DATA` share | **realised P&L** |
|---|---|---|---|
| h30 baseline | £166,830 | 7.6% | **£154,234** |
| hunlimited | £194,790 | 62.1% | £73,844 |
| **s3.0** | £348,549 | **59.9%** | **£139,863** |

**On realised P&L alone, s3.0 (£139,863) is worse than baseline (£154,234).** Its top 10 trades are
64% of total P&L; most exit on 2026-08-07, the last day of data (NVDA held 1,198 days at +722%, still
open). This is not an engine error — production marks open positions identically, and a real account
holding NVDA up 722% genuinely would be worth that — but it is a real concentration and
realisation-risk property.

**End-date sensitivity, run to test whether the advantage is a window artefact.** Each arm's own
equity curve truncated at seven successively earlier end dates, Δ vs baseline at the same cutoff:

| end date | h60 | h90 | hunlimited | **s3.0** | snone |
|---|---|---|---|---|---|
| 2022-08-05 | +1.678 | +1.414 | +3.515 | **+5.852** | +4.813 |
| 2023-08-04 | +1.500 | +1.412 | +3.341 | **+5.606** | +4.421 |
| 2024-08-02 | +1.300 | +1.273 | +3.670 | **+6.148** | +4.839 |
| 2025-02-07 | +1.245 | +1.031 | +3.370 | **+6.144** | +4.504 |
| 2025-08-01 | +1.019 | +1.032 | +3.969 | **+6.673** | +5.159 |
| 2026-02-06 | +0.956 | +0.916 | +3.743 | **+6.338** | +4.750 |
| 2026-08-07 | +0.938 | +0.519 | +3.634 | **+6.443** | +4.577 |

**s3.0 is best at every cutoff, in a tight +5.6 to +6.7pp band — including 2022-08-05, before the AI
rally that produced its largest open positions.** The advantage over baseline is not a window
artefact.

**But the absolute claim is.** s3.0's own excess vs `^SP500TR` across those cutoffs: **−2.424,
−2.208, −1.441, −0.738, −0.270, −0.280, +0.278.** It clears the index at **exactly one end date, the
last**, and trails at all six others. **"Beats buy-and-hold" is not a supportable claim and is not
made here.** The supportable claim is the relative one.

---

## 8. The walk-away sentence, clause by clause (s3.0)

1. **Clears +1.5pp on Pool 2 CLEAN?** +6.443pp. **Does not fire.**
2. **Drawdown >15pp worse than baseline?** −43.88% vs −47.51% — *better* by 3.63pp. **Does not fire.**
3. **Shared-key outcomes not distinguishably better?** On the 91 genuinely diverged decisions,
   £20,669 → £85,379. Weak sample, right direction. **Does not fire** (but see §6's stated limit).
4. **Headline traces mainly to non-shared top-decile trades?** Overlap is 4× the random null (§6
   Test 1), so low overlap is structural, not luck. **Does not fire.**
5. **Exit composition doesn't shift as the dial predicts?** `MAX_HOLDING` 48.87% → 0%, `SIGNAL_OFF`
   4.45% → 52.74%. **Does not fire.**
6. **Back-check shows the max-holding ranking changes at final settings?** Ranking identical.
   **Does not fire.**

**No clause fires. Per the sentence, s3.0 has earned a 20-draw paired perturbation robustness test —
not adoption.**

---

## 9. Priors — how they fared

| # | Prior | Outcome |
|---|---|---|
| 1 | Trailing stop's prior is unfavourable | **Confirmed decisively** — costs ~5.4pp |
| 2 | Activation threshold might rescue it | **It did not.** Both +5% and +10% fail by similar margins |
| 3 | Entry information doesn't predict outcome | Untouched — this study is about exits, and exits **do** matter, unlike every entry feature tested |
| 4 | Turnover is the likely mechanism, in either direction | **Confirmed.** FX drag 3.331 → 0.268 pp/yr is the single largest driver |
| 5 | The earlier study's headline is itself unconfirmed | **Justified** — its stop ordering inverts here |
| 6 | Multiple comparisons | 20 cells run vs 72 for a full grid; back-check clean |

**Prior 3 deserves emphasis as the study's broadest result.** Four investigations found entry
information does not predict trade outcome. **This one finds exit mechanics matter enormously** — a
6.4pp cost-adjusted swing from two parameter changes. The edge in this system is not in choosing
better positions; it is in not churning the ones it has.

---

## 10. What this does not close

1. **Robustness.** The 20-draw perturbation test the walk-away sentence requires has not been run.
2. **Concentration.** 565 positions over 19 years at 149-day mean holds, with 60% of P&L unrealised,
   is a materially different risk profile from baseline's — not captured by the drawdown ceiling.
3. **The 2008–09 front-loading.** s3.0 enters 144 positions in 2008 and 131 in 2009, then 6 in 2010
   and single digits in several later years — capacity locks up early. Whether that is robust to a
   different starting point is untested (the measurement toolkit's start-date perturbation would test
   it).
4. **Live feasibility.** Unlimited holding interacts with the live re-entry, membership and
   reconciliation paths in ways no backtest exercises.

---

## 11. Files

`PREREGISTRATION.md`, `sweep_engine.py`, `step0_maxholding_roundtrip.py`, `step1_build_caches.py`,
`step2_verify.py`, `step3_run_cell.py`, `step4_instruments.py`, `step5_overlap_null_and_slice.py`,
`step6_enddate_sensitivity.py`, `trades_{pool}_{cell}.csv`, `panel_{pool}_{cell}.csv`,
`summary_{pool}_{cell}.json`, `instruments_{pool}.json`, `stage0_results.json`,
`overlap_null_pool2_clean_hunlimited.json`, `enddate_sensitivity_pool2_clean.json`.

**Backtest convention**: continuous walk, 2007-01-03 → 2026-08-07, all cells from the identical
£50,000 start — no train/test boundary exists, so reset-at-boundary is not applicable.
**Database state**: local research copy, `price_data` through **2026-08-07**, 612 CLEAN tickers, 608
usable Pool 2 CLEAN / 500 Pool 1, reconfirmed by direct query at run time. `^SP500TR`/`^IRX` fetched
read-only into a research-local cache. **No database writes.** Nothing adopted, nothing merged,
nothing deployed.
