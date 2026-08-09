# Position-count (concurrent-holdings target) study — results (2026-08-09)

**Verdict: NOT ADOPTED.** No arm clears the pre-registered +1.5pp cost-adjusted excess-return bar over
baseline, in either pool — the walk-away sentence's first clause fires outright, on its own, before
any of the other conditions need to be invoked. **But the headline result of this study is not the
return comparison — it's Sec 1a's own concern, confirmed dead-on**: the 5-position and 10-position
arms never actually built the portfolios their names describe. VaR sizing structurally refuses to
concentrate that tight; both arms realized a mean position count within ~1–2 positions of baseline's
own, in both pools. `config.yaml` is not modified, no code changed on any live path, nothing adopted.
Full pre-registration: `PREREGISTRATION.md` in this directory (approved with the two Sec 1a amendments
before this run).

## Verification — engine parity and sanity gate, both clean

**Engine parity**: `position_count_engine.py` at `per_trade_pct=0.05` (this study's own "n22" arm)
reproduced the raw, unmodified `simulate_portfolio_compounding_mtm()` byte-identical on **both**
pools — Pool 1 (4,752 trades, £241,673.18, 0 row-level mismatches) and Pool 2 CLEAN (4,919 trades,
£103,926.50, 0 row-level mismatches). The panel logging and `REJECTED_SUBMINIMUM` instrumentation
added for this study are confirmed purely additive, not just assumed so from code review.

**Sanity gate**: the n22 arm's own trade output, generated in the full 8-arm run, diffed row-for-row
(ticker, entry_date — not just totals) against the fresh reference saved during the parity check
above. **0 differing trades, exact P&L match, both pools.** Pools reconfirmed fresh at run time: 612
CLEAN (matching production, confirmed via direct SQL query the same session), price data through
2026-08-07.

## Sec 1a's own finding, confirmed — the central result of this study

**Realized mean concurrent positions, both pools:**

| Arm | Nominal target | Pool 1 realized | Pool 2 CLEAN realized | Realized/nominal ratio |
|---|---|---|---|---|
| n5 | 5 | 11.78 | 12.17 | **2.36× / 2.43×** |
| n10 | 10 | 12.23 | 12.59 | **1.22× / 1.26×** |
| n22 (baseline) | — (label only, see below) | 13.98 | 14.23 | n/a |
| n40 | 40 | 35.38 | 35.47 | 0.885× / 0.887× |

**"n22" needs a correction stated plainly, not glossed over.** The arm's target was never derived
from 22 — it's `per_trade_pct=0.05` unchanged, per the pre-registration's own §1. The "22" label came
from today's live book, a current snapshot; the 19-year historical backtest average at that same
`per_trade_pct` is **14, not 22**. This isn't a failure of the baseline arm (nothing was ever supposed
to force it to 22) — it's a real, useful correction to the informal framing this study started from:
live's current ~22 reflects today's specific market conditions, not the long-run average this
`per_trade_pct` value actually produces.

**Materiality test applied, stated explicitly rather than picking an arbitrary percentage cutoff**: is
an arm's realized count closer to its own nominal target, or closer to baseline's own realized count?
A genuine test of "N positions" should land nearer N than it lands near baseline (~14) — landing nearer
baseline means the arm barely moved the needle from the thing it's supposed to be contrasted against.

- **n5**: distance to its own target (5) is 6.78–7.17 positions; distance to baseline (14) is 1.81–2.06
  positions. **Closer to baseline than to its own target, in both pools, by a wide margin.** This arm
  did not test 5-position concentration — it tested something much closer to baseline itself.
- **n10**: distance to its own target (10) is 2.23–2.59 positions; distance to baseline (14) is
  1.64–1.75 positions. **Also closer to baseline than to its own target, in both pools**, though less
  dramatically than n5.
- **n40**: distance to its own target (40) is 4.53–4.62 positions; distance to baseline (14) is
  21.24–21.40 positions. **Overwhelmingly closer to its own target.** This arm cleanly realized what it
  claims to, modulo an expected, explainable ~11–12% shortfall (operating at ~63% mean deployment
  rather than fully consuming the 70% cap — the same gap baseline and every other arm shows, see the
  deployment table below).

**Per the pre-registration's own walk-away sentence, n5 and n10 are both reported as not-tested for
their nominal targets, not as results — n40 and n22 are not.** Neither n5 nor n10 ever built a
genuinely concentrated portfolio; both are, in realized terms, minor variations on baseline itself.

**The named mechanism at each end played out asymmetrically, exactly as the materiality framing in
Sec 1a anticipated, but with n10 also affected — a wider reach than originally scoped.** `n_rejected_subminimum`
is **0 in every arm, both pools** — the 40-arm's named risk (a nominal position too small to clear
`is_position_size_viable()`'s £10 floor) never materialized at all; the ~11–12% shortfall there is
pure deployment-cap-vs-mean-operating-point arithmetic, not a rejection mechanism. The 5-arm's named
risk (VaR sizing declining to allocate a full 14% ceiling to a volatile name) did materialize, and
turned out to affect the 10-arm too, more severely than anticipated for either: sizing is capped
*above* by `per_trade_pct × balance` but is never pulled *up* to that ceiling, and across a
19-year walk of momentum-selected (inherently higher-volatility) candidates, VaR-implied sizes
cluster in a band that a `per_trade_pct` ceiling set well above that band simply never binds against
— capacity keeps admitting more, smaller positions instead of fewer, larger ones, until deployment
saturates at the same ~63–64% mean operating point every arm in this study converges to.

## Results — all four arms, both pools, together

| | Pool 1 (direction only) | | | | Pool 2 CLEAN (decisive) | | | |
|---|---|---|---|---|---|---|---|---|
| | n5 | n10 | n22 (baseline) | n40 | n5 | n10 | n22 (baseline) | n40 |
| Trades | 4,024 | 4,203 | 4,752 | 11,951 | 4,168 | 4,339 | 4,919 | 12,012 |
| Cost-adj. CAGR | 5.752% | 5.495% | 7.164% | 5.679% | 4.726% | 3.664% | 3.440% | 4.840% |
| Excess vs `^SP500TR` (cost-adj.) | −5.411 | −5.668 | −3.999 | −5.484 | −6.437 | −7.499 | −7.723 | −6.323 |
| Δ vs baseline | **−1.412pp** | **−1.669pp** | — | **−1.485pp** | +1.286pp | +0.224pp | — | +1.400pp |
| Peak-rel. DD | −42.20% | −43.54% | −41.94% | −29.49% | −48.07% | −48.92% | −46.28% | −31.67% |
| Δ DD vs baseline | −0.26pp | −1.60pp | — | **+12.45pp** | −1.79pp | −2.64pp | — | **+14.61pp** |
| Mean deployed % | 63.67 | 63.62 | 63.46 | 63.07 | 63.79 | 63.81 | 63.72 | 63.27 |
| Δ deployed vs baseline | +0.21pp | +0.16pp | — | −0.39pp | +0.07pp | +0.09pp | — | −0.45pp |
| Realized mean N | 11.78 | 12.23 | 13.98 | 35.38 | 12.17 | 12.59 | 14.23 | 35.47 |
| Block-pass (Pool 2 CLEAN only) | — | — | — | — | 7/16 | 9/16 | 7/16 | 10/16 |

**§2's deployment guard passes cleanly everywhere** — mean deployment moves by at most 0.45pp across
all eight arm/pool combinations, nowhere near the 2pp bar. This is the clean confirmation of the exact
point Sec 1a's amendment was written to make: **the deployment guard alone would have called n5 and
n10 fine.** Both stay within 0.1pp of baseline's own deployment %. Only the realized-position-count
instrument this study added on top of the guard catches that n5 and n10 never built the portfolios
they claim to.

## Primary criterion — no arm clears the bar, in either pool

**Pool 2 CLEAN (decisive): the largest gap is n40 at +1.400pp — short of the +1.5pp bar.** n5 (+1.286pp)
and n10 (+0.224pp) fall further short, and n5/n10 are disqualified anyway per Sec 1a above. **No arm
in the decisive pool clears the pre-registered margin.**

**Pool 1 tells a sharper, consistent story in the other direction**: all three non-baseline arms are
*worse* than baseline (−1.41pp to −1.67pp), not merely short of a positive bar. Pool 1 is
direction-only per the pre-registration and doesn't override Pool 2 CLEAN's own verdict, but it adds
no support for adoption either — if anything it argues more clearly against it than Pool 2 CLEAN's
near-miss numbers do.

## Drawdown ceiling — never the binding constraint, for a reason directly tied to Sec 1a

**No arm breaches the 15pp one-sided ceiling in either pool — the opposite of what the pre-registration
expected.** §3 of the pre-registration explicitly predicted the 5-position arm would "likely breach any
reasonable ceiling" — it doesn't, because it never concentrated to 5 positions in the first place. n5's
drawdown (−48.07% Pool 2 CLEAN, −42.20% Pool 1) sits close to baseline's own (−46.28% / −41.94%) for
the same reason its return sits close to baseline's: it's a minor variation on baseline, not a genuinely
concentrated portfolio. n40, the one arm that did realize close to its nominal target, shows the
*opposite* of a drawdown cost — drawdown improves by 12.45–14.61pp, consistent with Part 7 of the
selection audit (more names held, closer to random, lower drawdown) and confirming the ceiling's
15pp calibration was reasonable — it simply never got tested against a genuinely tight arm, since none
of the tight arms were genuinely tight.

## Curve discipline — cannot be read as pre-registered

**The intended 5→10→22→40 curve collapses to two real points, not four.** In realized terms, n5
(12.17–12.23), n10 (12.23–12.59), and n22 (13.98–14.23) all sit within a ~2-position band; only n40
(35.38–35.47) sits at a genuinely different point on the realized curve. Reading cost-adjusted excess
return "in nominal order" (Pool 2 CLEAN: +1.286, +0.224, 0 [ref], +1.400) shows a dip at n10 that looks
like non-monotonicity — but three of those four points are, in realized terms, minor variations on the
same ~12–14-position portfolio, so this is much more plausibly capacity-cascade noise among
near-identical realized states (see CLAUDE.md's own "capacity-cascade amplification" note — different
`per_trade_pct` values produce different admission/rejection sequences even when the resulting mean
position count barely differs) than a genuine interior optimum. **This study cannot bracket an
optimum across its intended range, because the intended low end (5, 10) was never actually reached.**
Testing genuine tight concentration would need a different sizing mechanism — a hard slot count
enforced directly, not a `per_trade_pct` ceiling VaR sizing is free to undershoot — a different design,
not a parameter retune of this one.

## Outcome variance

**Top-3-trade concentration (n5/n10 only, both pools) — well below the 50% flag threshold, so this
concern does not materialize, though it's moot given the position-count disqualification above:**

| | Pool 1 n5 | Pool 1 n10 | Pool 2 CLEAN n5 | Pool 2 CLEAN n10 |
|---|---|---|---|---|
| Top-3 trades / total P&L | 11.9% | 11.9% | 11.3% | 13.0% |

Consistent with n5/n10 never actually concentrating: a genuinely 5-position portfolio drawing from
this project's own documented top-decile skew (Part 1 of the selection audit: top decile = 257% of
total P&L) would be expected to show far higher top-3 concentration than this. The low figures here are
one more confirmation these arms stayed close to baseline's own, much larger, trade population (4,024–
4,339 trades, not the few-hundred-trade population a true 5–10-position portfolio over 19 years would
produce).

**Top-decile overlap vs. baseline** (exact `(ticker, entry_date)` Jaccard / ticker-only Jaccard):

| | n5 | n10 | n40 |
|---|---|---|---|
| Pool 1 | 0.168 / 0.699 | 0.139 / 0.674 | 0.069 / 0.495 |
| Pool 2 CLEAN | 0.158 / 0.681 | 0.145 / 0.716 | 0.064 / 0.494 |

Low exact-pair overlap, moderate-to-high ticker overlap — the same shape this project's other studies
(gate-width, adj_close) have repeatedly found: largely the same *names* producing the biggest winners,
rarely admitted on the exact same day, consistent with capacity-cascade reshuffling rather than a
genuine composition difference.

## Paired per-decision instrument

**Minimum-sample floor comfortably cleared everywhere** (6,272–14,789 differing trades per pairing,
against the 100-trade floor). **Unique-admission win rates and mean returns are close between baseline
and every arm, in both pools — no arm's own unique admissions look distinguishably better:**

| Pool 2 CLEAN | n22-only win / mean P&L% | armX-only win / mean P&L% |
|---|---|---|
| vs. n5 | 42.94% / 0.60% | 42.95% / 0.88% |
| vs. n10 | 42.86% / 0.62% | 41.86% / 0.66% |
| vs. n40 | 43.92% / 0.80% | 44.09% / 0.79% |

n10's own unique admissions are, if anything, slightly *worse* on win rate than baseline's own unique
admissions (41.86% vs. 42.86%) — no support anywhere in this table for a winning arm, consistent with
the primary criterion's own outright failure and with Sec 1a's finding that n5/n10 aren't really
distinct populations from baseline in the first place.

## Block count — corroborating only

Measured resolution floor is 3 blocks (6–9 range). Pool 2 CLEAN: baseline 7/16, n5 7/16 (identical,
unsurprising given n5's realized closeness to baseline), n10 9/16 (+2, inside the floor), n40 10/16
(+3, at the edge of the floor). None of this changes the verdict — reported per the pre-registration's
own instruction not to read block-count deltas this small as decisive on their own.

## Walk-away sentence, checked clause by clause

> *"I will not adopt any position-count target if: no arm clears +1.5pp cumulative excess return over
> baseline's own excess return without exceeding baseline's own peak-relative drawdown by more than
> 15pp; or if any arm's mean deployment diverges from baseline by more than 2pp; or if any arm's
> realized mean position count diverges materially from its nominal target...; or if the trend across
> 5→10→22→40 is monotonic; or if a concentrated arm (5 or 10) that would otherwise pass has more than
> 50% of its own total P&L explained by its own top 3 trades; or if the paired per-decision instrument
> shows a winning arm's own unique admissions are not distinguishable from baseline's on a usable
> sample."*

1. **Fires outright, on its own.** No arm, in the decisive pool, clears +1.5pp (best case n40 at
   +1.400pp).
2. Does not fire — deployment guard passes cleanly everywhere (≤0.45pp everywhere).
3. **Fires for n5 and n10** — both realize a mean position count materially closer to baseline than to
   their own nominal target, in both pools (materiality test above). Does not fire for n40.
4. Not cleanly assessable — see "Curve discipline" above; three of four intended points collapsed to a
   narrow realized band, so "monotonic or not" isn't a meaningful read on the intended 5→10→22→40 axis.
   Moot regardless, given clause 1 already fires.
5. Does not fire — top-3 concentration for n5/n10 is 11.3–13.0%, far below 50%.
6. Consistent with not firing cleanly in either direction — unique-admission populations are close to
   indistinguishable from baseline's own, which offers no support for adoption but doesn't add a
   distinct disqualifying signal beyond clause 1's own outright failure.

**Not adopted.** The primary criterion fails cleanly and by itself. Separately and just as importantly,
Sec 1a's own concern — raised specifically because two prior studies each won on a headline number that
turned out to be a deployment artifact — caught something this study didn't even get as far as
Section 3 for: two of its four arms never built the portfolios their names describe. `per_trade_pct` is
a ceiling, not a dial for concurrent-position count, and VaR sizing structurally resists concentrating
below roughly 12 positions at this account's volatility/momentum-selection profile — a fact about the
sizing mechanism, independent of whether concentration itself would have helped or hurt.

## What this doesn't close

**Whether genuine tight concentration (5–10 positions) helps or hurts remains untested** — this study
intended to answer that and, for two of its four arms, could not, because `per_trade_pct` alone cannot
force that outcome against VaR sizing's own preferences. A future study wanting to test genuine
concentration would need a design that enforces a hard slot count directly (e.g. admit only the top-N
gate-passing candidates by rank into N explicit slots, sized by however much capital that implies —
structurally closer to the gate-width study's shortlist mechanism than to this study's `per_trade_pct`
sweep, though gate-width narrowed *candidacy* while this would need to narrow *capacity* directly) —
a different engine design, not a retune of the value tested here. **What this study does close cleanly**:
`per_trade_pct` in the 0.07–0.14 range (nominally 10 and 5 positions) is not a usable lever for testing
concentration at all under this account's sizing mechanics — any future attempt at this question should
not reach for `per_trade_pct` alone to get there.

## Files

`PREREGISTRATION.md`, `position_count_engine.py`, `step0_build_caches.py`, `step1_verify_engine.py`,
`step2_run_arms.py`, `step3_instruments.py`, `step3_all_results.json`,
`trades_{pool1,pool2_clean}_{n5,n10,n22,n40}.csv`, `panel_{pool1,pool2_clean}_{n5,n10,n22,n40}.csv`,
`rejections_{pool1,pool2_clean}_{n5,n10,n22,n40}.csv`, `verify_{pool1,pool2_clean}_baseline.csv`.
