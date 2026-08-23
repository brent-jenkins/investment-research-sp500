# Correlation-aware admission ordering — PRE-REGISTRATION (2026-08-23)

**Status: pre-registration only. Nothing has been run, no engine built, no cache constructed, no
database touched.** Submitted for approval before any arm executes. No changes to
`analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path are proposed at any
point, including on a positive result.

---

## 0. What this tests, and the dimension it addresses

**Every admission mechanism this project has tested evaluates candidates IN ISOLATION** — absolute
momentum, cross-sectional relative rank, the entry gate, the 2% floor, the slot count. None of them
references what the book already holds. Two candidates with identical rank are treated as
interchangeable even if one correlates 0.8 with the existing positions and the other 0.2.

The live book currently holds five semiconductor-adjacent names among 20, and nothing in the
admission path knows that.

**The share-class exclusion (2026-08-22) addressed the degenerate extreme of exactly this** —
GOOGL/GOOG at a return correlation of **0.9968** — as a hard candidacy filter over a hand-verified
map of four pairs. **This study is the general, continuous form of the same concern**, and it should
be read as such: if it produces nothing, the share-class exclusion remains justified on its own
narrow terms (a risk fix for an exactly-identified degenerate case), because a null here is a
statement about the *gradient*, not about the *twin*.

**Part 5 is the reason this dimension is genuinely open rather than already answered.** It found
capacity-rejected candidates perform close to indistinguishably from admitted ones (win rate
43.9–44.7% vs 43.1–44.4%; mean P&L +0.34–0.54% vs +0.70–1.02%). That is a statement about
**individual merit** and says nothing about **portfolio fit** — its own stated limits say so
explicitly: *"this measures price-path outcome only — no portfolio interaction."* The rejected
population being as good individually is precisely what makes "which of two equally-good candidates
fits the book better" a question with something left in it.

**Standing hazards read before writing this**: the measured noise floor (~**1.08pp** cost-adjusted
CAGR; block-pass range **3**); capacity-cascade amplification (a narrow change produces a
trade-level reshuffle far larger than the aggregate effect, so overlap figures contextualise rather
than conclude); the V1 confluence-scoring shape, abandoned for being unattributable, which §2
addresses directly; and the class-synthesis note's open-ended review question — **what would have to
be true for this check to report clean, and is that the same as the thing being clean?** — applied
to every instrument in §5.

---

## 1. Baseline

**The CURRENT LIVE CONFIGURATION**: `admission_ranking_mode: relative_rank`, `atr_stop_multiplier:
3.0`, `stock_max_holding_days: 0`, `per_trade_pct: 0.05`, `max_total_deployed: 0.70`,
`max_risk_pct: 0.01`, `min_momentum_pct: 0.02`, `reentry_cooldown_days: 0`.

**Preserved reference, Pool 2 CLEAN: 565 trades / £348,548.74.**

`share_class_exclusion` is left at its shipped `false` for every arm including baseline, so this
study measures the correlation gradient against today's real behaviour and never against a
part-adopted variant. Noted because the two mechanisms are related and it would be easy to conflate
them.

---

## 2. THE MECHANISM — one measure, one parameter, and k = 0 is baseline exactly

### 2.1 The correlation measure: MEAN pairwise correlation to held positions

For candidate `c` on day `t`, with held set `H(t)`:

```
corr_raw(c, t) = mean over h in H(t) of  pearson( r_c , r_h )   over the trailing 120 trading days
                 (r = daily LOG returns, the same series compute_momentum_series() reads)
corr_raw(c, t) = undefined when H(t) is empty            -> see 2.4
```

**Why the mean, argued against the alternatives rather than asserted:**

- **vs. MAX.** Max is the natural generalisation of the *twin* case, and the twin case is already
  handled — the share-class map catches the 0.99 end as a hard exclusion. What is left is the
  gradient, and max answers "is there one near-duplicate", not "how does this fit the book". Max is
  also an **order statistic and therefore biased upward** by construction, the same mechanism that
  closed the displacement study (a max over ~20 held names is far less biased than that study's max
  over ~216 candidates, but the bias is one-directional and grows with book size, which is exactly
  the regime the mechanism is meant to act in).
- **vs. a portfolio-variance measure** (marginal contribution to portfolio variance). Theoretically
  the right quantity, and rejected on estimation grounds: it needs a full covariance matrix over the
  held set, estimated from the same short window, and matrix estimation error at ~20 assets on 120
  observations is large and structured. It would also make the measure depend on position *weights*,
  coupling admission ordering to VaR sizing — a second channel this study is not equipped to
  attribute.
- The mean does dilute the motivating case (a sixth semiconductor among 20 lifts the mean less than
  it lifts the max), and that is a **stated cost, not an oversight**. If the study is inert, §4's
  gate cannot distinguish "correlation does not matter" from "the mean was too blunt to express it",
  and the report must say so rather than reporting a null.

**Window: 120 trading days.** Long enough for a stable Pearson estimate at ~20 pairs, short enough to
reflect the book's *current* composition rather than a year-old one. Deliberately NOT 252: the
momentum signal already spans a year, and reusing that window would make the correlation term
largely a restatement of co-momentum. **Fixed, not swept** — see §2.3.

### 2.2 It adjusts the RANK, not candidacy

**Rank adjustment.** The gate-width study established that candidacy filtering barely reaches
admissions — **92–93% of admissions happen only because that day's own exits freed a slot** — so a
filter is mostly absorbed by capacity timing. The relative-ranking study established the opposite for
ordering: a change to the sort key *alone*, with the eligible set untouched, moved the headline by
+1.993pp and changed 78% of admissions. **Ordering is the channel that bites in this system**, so
that is the channel this study uses.

### 2.3 How it enters — a single parameter in rank space, with baseline as k = 0

Both quantities are converted to **within-day percentiles among that day's gate-passing candidates**
(0 = worst, 1 = best), then combined:

```
rank_pct(c,t)  = percentile of relative_rank(c,t)   among that day's gate-passers   [0,1]
corr_pct(c,t)  = percentile of corr_raw(c,t)        among that day's gate-passers   [0,1]

adjusted(c,t)  = rank_pct(c,t)  −  k × corr_pct(c,t)
```

Candidates are admitted in descending `adjusted`, with the existing insertion-order tie-break
unchanged. Everything else — gate, 2% floor, re-entry cooldown, sizing, capacity, every exit — is
byte-identical to baseline.

**Why percentiles and not raw values.** `relative_rank` is a return (unbounded, fat-tailed) and
correlation is bounded in [−1, 1]; combining them raw makes `k` carry units and an arbitrary scale,
so the sweep range would be meaningless. In rank space `k` is interpretable directly: **k = 1 means
correlation matters exactly as much as momentum rank.**

**Why this is one variable and not the V1 confluence shape.** CLAUDE.md records confluence scoring as
abandoned because a blended score with several tunable weights is unattributable. This has **one
measure, fixed in advance and not swept, and one parameter**. The measure is not a free choice made
after seeing results; §2.1 commits to it with its reasoning, and if it is the wrong measure the
correct report is "this measure showed nothing", never "we tried another".

**k = 0 reproduces baseline EXACTLY**, which is a deliberate design property and not a coincidence:
it gives a built-in degenerate-parity check (§6.1) that exercises the entire new code path —
correlation computation, percentile conversion, the adjusted sort — and must still return the
preserved 565 / £348,548.74 trade set byte-identically.

### 2.4 Arms — both directions, because neither is the obvious answer

| arm | k | direction |
|---|---|---|
| baseline | **0.00** | today's behaviour, exactly |
| D1 | **+0.25** | diversifying, mild |
| D2 | **+0.50** | diversifying |
| D3 | **+1.00** | diversifying, correlation as strong as rank |
| R1 | **−0.25** | reinforcing, mild |
| R2 | **−0.50** | reinforcing |
| R3 | **−1.00** | reinforcing, correlation as strong as rank |

Symmetric by construction so the two directions are directly comparable and neither gets a finer grid
than the other. **Seven arms × two pools = 14 runs.**

**Undefined-correlation handling, specified now because the naive choice is biased.** A candidate
with insufficient overlapping history against the held set (a recent listing, a data gap, or an empty
book early in the walk) has no computable `corr_raw`. Assigning it **0 correlation** would make every
such candidate maximally attractive under diversifying and maximally unattractive under reinforcing —
a systematic bias toward new listings that would be indistinguishable from a real effect. **Undefined
is therefore assigned `corr_pct = 0.5`** (the day's median, i.e. neutral: the adjustment neither helps
nor hurts it relative to the median candidate), and **the count and share of undefined candidates is a
required output per arm.** If that share is large the arms are partly testing the fallback rather than
the mechanism, and the report must say so.

### 2.5 Staleness protection

Any trailing-window computation in this codebase needs the calendar-gap guard
`analysis/signals.py:_MAX_ANCHOR_GAP_DAYS` (**400 days**) — **a correlation computed across a genuine
data gap returns a real, wrong number rather than NaN**, which is strictly worse than missing.

The correlation computation carries the same guard: if either series has a calendar gap exceeding the
threshold anywhere inside its 120-trading-day window, `corr_raw` is **undefined** for that pair and
handled per §2.4. Additionally, a pair with **fewer than 60 overlapping observations** in the window
is undefined (the same `_MIN_VAR_RETURNS` floor the VaR path already uses, reused rather than a second
number invented).

**Tested with a synthetic-gap fixture in the same style as `tests/test_signals.py`'s own
momentum-staleness test** — a real gap must resolve to undefined, and recover once a full gapless
window exists past the gap. Plus a real-data scan reporting how often the guard fires across the walk.

---

## 3. MANDATORY GATE — realised effect size, before any return figure

**No return number for any arm appears anywhere in the results document until this section is
complete for every arm.**

**The specific reason this could be inert, stated in advance:** the gate-width study found admission
reaches only a few candidates past the top of the ranked list on a typical day, and the slot-count
study measured median within-day admission percentile at **4.65%**. A rank perturbation only matters
if it moves a candidate **across the admission boundary**. A small `k` may reorder the list
extensively while changing nothing that is actually admitted.

Required per arm:

1. **Admissions changed versus baseline** — count and share of `(ticker, entry_date)` admissions that
   differ. **The gating measure.**
2. **Same-day substitutions** — dates where one candidate genuinely displaced another, distinguished
   from admissions that merely shifted in time.
3. **Mean absolute rank movement** the adjustment induces among gate-passers, and separately among
   the top decile — how much the list moves versus how much of that reaches the boundary.
4. **Undefined-correlation share** (§2.4).

**Materiality rule, following the n5/n10 and B200 precedent verbatim: an arm that changes fewer than
5% of admissions is reported NOT TESTED, never as a null.** 5% is the same floor the
momentum-smoothness study used for its own displacement measure, reused rather than re-invented. An
arm below it has not expressed the mechanism, and its return figure says nothing about correlation.

---

## 4. Criteria

**4.1 PRIMARY — cost-adjusted excess return over `^SP500TR` vs baseline's own excess. Required margin
+1.5pp** on Pool 2 CLEAN. `^SP500TR` is identical across arms so Δ excess = Δ cost-adjusted CAGR, and
the ~1.08pp noise floor applies directly. Frictionless CAGR, FX drag and idle-cash credit reported as
three separate lines: turnover may differ between arms, so costs are modelled per arm, not deferred.

**4.2 DRAWDOWN — one-sided ceiling against worsening: no arm may exceed baseline's peak-relative max
drawdown by more than 15pp.** Baseline is −43.88%; a second absolute floor of **−60%** applies, as in
the slot-count study.

**THE ASYMMETRY IS PRE-REGISTERED AND BINDING.** The diversifying arms are *expected* to improve
drawdown — that is what diversification does, and it is not evidence of anything. **A drawdown
improvement in D1/D2/D3 must NOT be promoted to a criterion after the fact.** This is the
momentum-smoothness precedent stated in advance: that study found a monotone drawdown improvement of
up to +15.07pp, declined to promote it because drawdown had been set as a one-sided ceiling and not a
criterion, and said so. The same rule binds here. The reinforcing arms are expected to worsen
drawdown, and the ceiling is where that gets tested.

**4.3 REALISED BOOK CORRELATION per arm** — mean pairwise correlation among *held* positions,
averaged over trading days. **Does the mechanism change what the portfolio IS, or only the order it
considered things in?** An arm that changes admissions (§3) but leaves realised book correlation flat
has reordered without diversifying, which is a different finding from either success or failure and
must be reported as such.

**4.4 SECTOR COMPOSITION per arm** — Herfindahl concentration over `ticker_sector`, plus the top-three
sector shares, against baseline's own. Correlation and sector overlap are related but not identical:
an arm could reduce measured correlation while leaving sector concentration untouched (correlation
picks up common factor exposure that crosses sector labels), or vice versa. **Reported for both
directions**, since it is the most legible description of what the mechanism did.

**4.5 DEPLOYMENT PARITY AS A DESIGN CONSTRAINT, not a post-hoc guard.** Every arm keeps the daily
admission clock and changes only the sort key, which is structurally the same situation as the
relative-ranking study — that study measured every arm within **0.21pp** of baseline, the tightest
parity in this project's record. **Expected here: within 1pp.** An arm diverging by more than **4pp**
is excluded from the return comparison entirely (the slot-count threshold, with its arithmetic: 4pp
against an 11.13%/yr benchmark is ~0.45pp/yr, ~30% of the bar). Divergence between 2pp and 4pp is
flagged and its idle-cash contribution quantified before its return is discussed.

**4.6 OVERLAP — top-decile trade overlap alongside every headline gap**, plus overall exact-key and
ticker Jaccard. **Predicted low and to be read as structural, not as an effect size**: the
relative-ranking study predicted 45–65% for a pure sort-key change and measured **21.8–23.1%**. A
randomisation null (conditioned on each arm's own admission dates, drawing from each date's real
gate-passing set, 1,000 draws) is pre-registered so a low number can be compared against chance rather
than interpreted directly.

**4.7 Block-pass count — corroborating only.** Resolution floor 3 blocks; any delta ≤3 is read as
neither support nor evidence against.

---

## 5. Per-decision instrument — constructibility stated in advance

**The exact-key paired instrument is INAPPLICABLE IN PRINCIPLE here, and this is known before running
rather than discovered after.** The slot-count study established it for exactly this shape: a pure
admission-ordering change alters *which* positions are held, never *how* a held position behaves, so
any `(ticker, entry_date)` both arms admit computes the same stop and realises the same `pnl_pct` by
construction. That study measured **133/133, 57/57, 27/27 byte-identical** shared trades. Reporting a
shared-key win rate here would be reporting an arithmetic identity.

**Primary per-decision instrument: the disagreement-population comparison** — win rate and mean/median
P&L% of each arm's *unique* admissions against baseline's *unique* admissions. This is precisely the
instrument that carried the relative-ranking study's central claim (B-only 44.30%/0.89% vs A-only
43.57%/0.63% on Pool 2 CLEAN), and it is the right tool when the populations are near-disjoint by
construction. **Floor: 100 unique admissions per side**, below which it is reported as a bare count
and not interpreted in either direction.

**Secondary, valid at any trade count: rank-at-admission distribution** — median within-day percentile
of admitted positions, per arm. Under diversifying, admissions should come from *further down* the raw
momentum-rank list (the mechanism is spending rank to buy diversification); under reinforcing, from
further up. **If that does not move monotonically with k, the arms are not expressing the intended
behaviour** and the mechanism has failed before any outcome question arises.

---

## 6. Verification, before any arm result is trusted

Each check states what its degenerate case is and whether it can produce both answers, per the class
synthesis note.

1. **Degenerate parity at k = 0.** The new engine at k = 0 must reproduce raw, unmodified
   `simulate_portfolio_compounding_mtm()` **byte-identically** on both pools, on a canonical
   `(ticker, entry_date, exit_date)` sort, compared **in memory** (never across a CSV round trip — the
   trailing study measured that altering 16.2% of float64 values). **Unusually strong here because k =
   0 still runs the whole new path** — correlations computed, percentiles taken, adjusted sort applied
   — so this exercises the machinery rather than bypassing it. *Can it produce the other answer?* Yes:
   any error in the percentile conversion or the sort would surface as a mismatch at k = 0.
2. **The adjustment actually moves the order.** For a sample of days, confirm the adjusted ordering
   differs from the raw ordering at k ≠ 0 and is identical at k = 0. *Degenerate case:* an arm whose
   ordering never changes is testing nothing and is reported as such.
3. **Correlation values are sane.** Distribution of `corr_raw` across the walk; known-pair spot checks
   (GOOGL/GOOG must be ≈0.99; two unrelated sectors materially lower). **Deliberately validated
   against the share-class map as a control** — if the measure cannot recover the pairs already known
   to be near-identical, it is not measuring correlation.
4. **Staleness guard** (§2.5) — synthetic-gap regression plus a real-data fire-rate scan.
5. **Decision-engine parity for the research script**, per the standing requirement, from this
   script's own run: 30 sampled trades per pool with exit reason, entry gate, sizing horizon and
   position size each recomputed by calling the shared functions directly, sizing driven by the
   engine's **own emitted pre-entry balance** (never a reconstructed proxy — the error that produced
   the cross-sectional study's false 21/30).
6. **Non-vacuity.** Every check must be shown to have operated on a non-empty population. **And,
   per the mocked-anchor sub-shape recorded 2026-08-23: no test of the correlation computation may
   mock the correlation computation.** The component that decides the property under test is the one
   component that must be real.

---

## 7. Sanity gate

Baseline must reproduce **Pool 2 CLEAN 565 trades / £348,548.74**, compared as **trade SETS**
(`ticker`, `entry_date`, `exit_date`, `exit_reason`, `pnl_gbp`) — never by total or count alone. Pool 1
compared the same way against its own preserved `s3.0` set (522 / £429,108.58). A small, fully
row-diagnosed discrepancy from ingestion moving on is expected and explained before anything else is
discussed; an unexplained one halts the study.

---

## 8. Pools

**Pool 2 CLEAN** (`get_research_stock_candidates() ∩ CLEAN`) — decisive. **Pool 1**
(`get_live_stock_candidates()`) — direction only, never decisive. Both reconfirmed by **direct query
at run time**, and — per the derived-state lesson that has now cost this project twice, most recently
the membership-health report rendering an empty DANGEROUS category off a stale local copy — the
specific database file this study reads from is verified current in place, not assumed.

A cross-pool sign disagreement on the primary criterion is reported as an unreconciled disagreement,
not resolved toward the convenient pool.

---

## 9. HONEST PRIORS — which way the evidence falls, not which way is comfortable

**The evidence leans toward REINFORCING being the better direction, and that is the direction nobody
intuitively wants.**

- **Part 7 is the positive prior for reinforcing.** Momentum ranking sits at the **99th percentile** of
  100 random-admission runs on both frictionless and cost-adjusted CAGR, and at the **98th-worst** on
  drawdown — it wins *because* it concentrates. **Diversifying moves the book toward the random
  distribution that baseline beats 99 times in 100.**
- **Every protective mechanism this project has tested has lost.** Trailing stops at +5%/+10% (−5.4pp),
  the high-threshold trailing stop (−1.4 to −5.1pp), Arm C's `RANK_DROPPED` de-selection, the monthly
  rebalance. The diversifying direction is another protective mechanism, and the base rate is poor.
- **The counter-argument, stated fairly:** Part 7 varied *which* candidates fill roughly fixed slots,
  not their correlation structure. Concentration-by-momentum-rank and concentration-by-correlation are
  not the same manipulation, and the slot-count study found that concentrating by *count* bought
  nothing detectable while, contrary to its own prior, *improving* drawdown at n5/n3. So the Part 7
  read is suggestive, not direct, and the project has already been wrong once about a concentration
  prior.
- **The strongest prior of all is that neither direction clears +1.5pp.** This is roughly the 62nd
  experiment in this project's record and one has been adopted. Nothing here is a construction change
  with an independent rationale of the kind that distinguished relative-rank; it is a second signal
  layered on the admission sort.

**Stated plainly: I expect NOT ADOPTED, and I expect the reinforcing arms to look better than the
diversifying ones. If the diversifying arms show better drawdown, that is the expected behaviour of
diversification and §4.2 forbids promoting it.**

---

## 10. THE WALK-AWAY SENTENCE

> **I will not adopt any correlation adjustment if: any arm changes fewer than 5% of admissions versus
> baseline (that arm is reported NOT TESTED, never as a null); or if any arm's mean deployment diverges
> from baseline's by more than 4pp (excluded from the return comparison entirely, not caveated); or if
> no arm clears +1.5pp of cost-adjusted excess return over baseline's own excess on Pool 2 CLEAN; or if
> an otherwise-clearing arm exceeds baseline's peak-relative max drawdown by more than 15pp or posts one
> worse than −60% absolute; or if a clearing arm's advantage rests on a drawdown improvement rather
> than the primary criterion (§4.2 — diversification improving drawdown is the expected behaviour of
> diversification, not evidence for it); or if the disagreement-population instrument shows a clearing
> arm's own unique admissions are not distinguishably better than baseline's on a sample clearing the
> 100-admission floor; or if the rank-at-admission distribution does not move monotonically with k
> (the arms are not expressing the mechanism); or if a clearing arm changes admissions without
> changing realised book correlation (§4.3 — it reordered without diversifying, so whatever it found
> is not the correlation effect it claims).** A correlation adjustment that wins by holding different
> cash, by a cascade-driven reshuffle, by a drawdown improvement promoted after the fact, or by an
> effect that does not trace to the portfolio actually becoming less or more correlated, has not shown
> that portfolio fit is an exploitable dimension — only that perturbing the sort key changes outcomes,
> which this project already knows.

**Asymmetry, stated explicitly: this sentence is a VETO, not an adoption trigger.** An arm firing no
clause has earned a 20-draw paired perturbation robustness test — **single-ticker drop and start-date
shift only; the `shuffle_tiebreak` dimension is inert by construction in this system and its draws must
not be counted** (corrected 2026-08-12) — not adoption.

---

## 11. What a positive result would and would not license

Nothing here is an adoption path. A clearing, robustness-surviving arm would require, before any
production change: promotion of the correlation computation into a real, tested production module
(never research code a live caller reaches into); genuine coverage in all four live/backtest parity
layers from the start; and a decision about **computational cost on the live path** — a correlation
against every held position for every gate-passing candidate, every night, is materially more work
than the current sort key and would need its own render/decide-path budget, in a system that has
already been bitten four times by per-ticker fan-out costs.

`trading212_invest.live` remains `false` regardless.

---

## 12. Feasibility and constraints

**Compute**: ~200 gate-passers × ~14 held × 4,930 days is ~14M pairwise correlations per arm if done
naively. Implemented instead as one trailing returns matrix per day with a vectorised
candidate-vs-held correlation (a single matrix product per day), which is tractable; the 14 runs reuse
one cache and one precomputed returns matrix.

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path. No
database writes; `^SP500TR`/`^IRX` reused read-only from the existing committed cache. Own dedicated
git worktree. Nothing adopted, nothing merged, nothing deployed.

**Backtest convention**: continuous walk, 2007-01-03 → 2026-08-07, every arm from an identical
£50,000 start; no train/test boundary, so reset-at-boundary is not applicable. **Deployment-cap
accounting**: mark-to-market throughout.
