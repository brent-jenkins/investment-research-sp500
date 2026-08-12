# Force-close variant — does the exit-mechanics result survive crystallisation? (2026-08-12)

**Verdict: YES, at 5 of 5 cutoffs in both pools. The advantage is not an artefact of holding
positions open at favourable marks — and the "realised P&L is worse than baseline" caveat that
prompted this test was a MEASUREMENT ARTEFACT of my own framing, not a property of the strategy.**

**Read-only research. No promotion, no config change, no production code, no database writes.**

---

## 0. The headline correction, stated first

**The engine already force-closes every open position at the walk's end** (`END_OF_DATA` at the final
close), and the sweep's headline cost-adjusted CAGR is computed from that equity curve. Verified to
the penny, not argued:

| arm | final equity − £50,000 | Σ `pnl_gbp` | diff |
|---|---|---|---|
| h30 baseline | £166,830.11 | £166,830.11 | **+0.00** |
| s3.0 winner | £348,548.74 | £348,548.74 | **+0.00** |

**So +6.443pp was already the fully-realised comparison. There was no unrealised overhang in the
headline.**

**What the 59.9% figure actually described** was the share of P&L attributable to positions the
strategy never *chose* to exit — a statement about **who decided the exit**, not about whether the
gain was **booked**. Both positions were closed and both gains were counted. Conflating those two is
what produced the concern, and the conflation was mine: the sweep report, the robustness report and
the CLAUDE.md entry all carried "realised P&L is worse than baseline" as a qualification without
interrogating what the two quantities measured.

**Worse, that comparison was structurally unfair.** Excluding `END_OF_DATA` trades removed **59.9% of
s3.0's P&L but only 7.6% of baseline's** — it deleted the winner's largest positions and almost
nothing of the baseline's, then reported the winner as behind.

---

## 1. Method, and why two genuine re-runs were run anyway

Force-closing means every position still open at a cutoff is closed at that date's close.
`equity(D) − starting_capital` is exactly that total, by the sequential-walk argument. The argument is
sound — and is exactly the kind of sound argument that can hide an off-by-one in exit handling at the
boundary, so it was **validated empirically** rather than trusted.

**Two genuine re-runs**, cache truncated so the engine closes positions at the cutoff as real
simulated exits:

| cutoff | genuine re-run total | truncation shortcut | closed-by-rule trades |
|---|---|---|---|
| 2022-08-05 | **£42,075.71** | **£42,075.71** | 3,763 both |
| 2024-08-02 | **£72,083** | **£72,083** | match |

**A real boundary artefact was found, and it is economically inert.** The re-run reports 28
force-closed positions where the full run shows 17 open at that date. Decomposed: **14 entered before
the cutoff** (£3,058.91 — matching the full run's 14 exactly) and **14 entered ON the cutoff day and
were force-closed the same day at exactly £0.00**, because the truncated walk's final day frees
capacity in Phase A and then admits in Phase B at a price identical to the exit. Real, fully
explained, moves nothing. Totals match to the penny.

The shortcut is therefore validated, and the remaining cutoffs use it.

---

## 2. Force-close comparison — Pool 2 CLEAN (decisive)

Fully-realised, both arms treated identically.

| cutoff | arm | closed n | **total P&L** | cost-adj CAGR | excess vs `^SP500TR` | maxDD | FX pp |
|---|---|---|---|---|---|---|---|
| 2022-08-05 | h30 | 3,763 | £42,076 | 1.049 | −8.276 | −47.51 | 3.242 |
| | **s3.0** | 472 | **£91,742** | 6.900 | −2.424 | −43.88 | 0.302 |
| | | | | | **Δ +5.852pp**, total **2.18×** | | |
| 2023-08-04 | h30 | 4,026 | £52,437 | 1.546 | −7.814 | −47.51 | 3.246 |
| | **s3.0** | 505 | **£105,905** | 7.152 | −2.208 | −43.88 | 0.309 |
| | | | | | **Δ +5.606pp**, total **2.02×** | | |
| 2024-08-02 | h30 | 4,258 | £72,083 | 2.413 | −7.589 | −47.51 | 3.253 |
| | **s3.0** | 518 | **£156,500** | 8.561 | −1.441 | −43.88 | 0.285 |
| | | | | | **Δ +6.148pp**, total **2.17×** | | |
| 2025-08-01 | h30 | 4,525 | £104,522 | 3.488 | −6.943 | −47.51 | 3.288 |
| | **s3.0** | 522 | **£239,580** | 10.162 | −0.270 | −43.88 | 0.251 |
| | | | | | **Δ +6.673pp**, total **2.29×** | | |
| 2026-08-07 | h30 | 4,876 | £166,830 | 4.998 | −6.165 | −47.51 | 3.331 |
| | **s3.0** | 565 | **£348,549** | 11.441 | +0.278 | −43.88 | 0.268 |
| | | | | | **Δ +6.443pp**, total **2.09×** | | |

**Δ across cutoffs: min +5.606, median +6.148, max +6.673 — 5/5 positive.** The winner's total
force-closed P&L is **2.0–2.3× baseline's at every cutoff**.

**Pool 1 (direction only)** agrees: Δ +4.128 / +3.868 / +5.028 / +5.677 / +5.381 — **5/5 positive**,
totals 1.2–1.7× baseline's.

---

## 3. The decisive decomposition — the advantage is LARGEST where the unrealised block is SMALLEST

Splitting s3.0's total into what it closed **by rule** before the cutoff and what the device
crystallised:

| cutoff | s3.0 rule-closed | h30 rule-closed | **ratio** | s3.0 force-closed | force-closed share |
|---|---|---|---|---|---|
| 2022-08-05 | £77,030 | £39,017 | **1.97×** | £14,712 | **16.0%** |
| 2023-08-04 | £89,923 | £51,188 | **1.76×** | £15,983 | **15.1%** |
| 2024-08-02 | £84,295 | £71,994 | 1.17× | £72,205 | 46.1% |
| 2025-08-01 | £108,209 | £100,490 | 1.08× | £131,371 | 54.8% |

*(At 2026-08-07 the engine has already closed everything, so rule-closed ≡ total and the
decomposition is degenerate — which is precisely the fact §0 corrects.)*

**s3.0's rule-closed P&L alone exceeds baseline's at every intermediate cutoff — and by the widest
margin (1.97×, 1.76×) exactly where the force-closed block is smallest (16.0%, 15.1%).** That is the
opposite of what the artefact hypothesis predicts. If the advantage were manufactured by holding
positions open at favourable marks, it would be largest where the unrealised share is largest; it is
smallest there.

---

## 4. Accounting for "realised worse but total better", as instructed

The combination is fully explained and needs no special pleading.

**(a) It is a trade-count artefact, confirmed size-neutrally.** Strategy-chosen exits only, Pool 2
CLEAN, full window:

| | n | total | £/trade | **mean pnl%** | median pnl% | win% |
|---|---|---|---|---|---|---|
| baseline h30 | 4,836 | £154,234 | £31.9 | **+0.827%** | −3.067% | 43.69 |
| **s3.0** | **531** | £139,863 | **£263.4** | **+8.060%** | −5.280% | 28.25 |

**Trade-count ratio 9.1× against a per-trade quality ratio of 9.7×** — they nearly cancel, which is
why the totals land close. s3.0's mean position is *smaller* (£2,770 vs £3,347), so the per-trade
advantage is understated, not inflated, by sizing. Pool 1 replicates it (9.6× count, 9.9× quality).

Baseline reaches a marginally higher total on strategy-chosen exits by making **nine times as many
decisions at a tenth the per-decision return**. That is not a caveat about the winning cell — it is a
restatement of what the change does.

**(b) The full-window "realised worse" reading is an artefact of the exclusion, not a real reversal.**
At every intermediate cutoff s3.0's rule-closed P&L is *ahead* (§3). The direction only inverts at the
final date, where excluding `END_OF_DATA` deletes 59.9% of s3.0's P&L and 7.6% of baseline's — an
asymmetric exclusion, not a like-for-like comparison.

**(c) The shape underneath.** s3.0 wins *less* often (28.25% vs 43.69%) with a *worse* median trade
(−5.28% vs −3.07%) and a mean 9.7× higher. It loses more often and more deeply, and wins enormously —
consistent with everything else this system has shown about right-tail dependence.

---

## 5. Pre-committed interpretation, applied

> *"If the advantage survives force-close at most or all cutoffs, the unrealised-marks caveat is
> answered and the result stands as measured."*

**It survives at 5 of 5 cutoffs in both pools, with the winner's total P&L 2.0–2.3× baseline's
(Pool 2) throughout, and its rule-closed P&L alone ahead at every intermediate cutoff. The caveat is
answered.** It was not a fragility; it was a measurement misunderstanding, and one I introduced.

**Force-closing is artificial.** A real system would not liquidate on a date. It is a comparison
device to isolate realisation, not a proposed behaviour, and nothing here recommends it.

---

## 6. What this changes, and what it does not

**Changes:** the "realised P&L is worse than baseline" qualification should be **withdrawn** from the
sweep report, the robustness report and the CLAUDE.md entry, and replaced with the correct statement:
*s3.0 books a marginally lower total across strategy-chosen exits because it makes ~9× fewer of them,
at ~9.7× the per-trade return; on fully-realised P&L at any cutoff it is 2.0–2.3× ahead.*

**Does not change — both remain live and untouched by this test:**

1. **The absolute index claim is still unsupported.** s3.0 clears `^SP500TR` at one end date of seven
   (+0.278pp at 2026-08-07, Pool 2) and trails at the other six. Pool 1 clears at two of five. The
   robust claim remains the relative one.
2. **Concentration is still real.** 565 positions over 19 years, 73.6% of P&L in 10 of 238 names,
   NVDA alone 17.0%. A robust comparison does not diversify a portfolio.
3. **Live feasibility is still untested.** Unlimited holding interacts with the live re-entry,
   membership and reconciliation paths in ways no backtest exercises.

**So the result now carries two standing qualifications rather than three** — and the one removed was
the only one that questioned whether the measured advantage was real.

---

## 7. Files

`step1_forceclose_rerun.py`, `step2_forceclose_compare.py`, `build_caches.py`,
`fc_trades_*/fc_panel_*/fc_rerun_*.json` (genuine re-runs at 2022-08-05 and 2024-08-02),
`forceclose_comparison.json`.

**Database state**: local research copy, `price_data` through 2026-08-07, 612 CLEAN / 608 usable
Pool 2 CLEAN, 500 Pool 1 — reconfirmed by direct query at cache-build time. `^SP500TR`/`^IRX` reused
read-only from the sweep's research-local cache. Same engine, pools, caches and cost template as the
sweep. **No database writes. Nothing adopted, nothing deployed.**
