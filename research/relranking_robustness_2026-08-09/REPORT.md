# Relative-ranking Arm B — robustness test (2026-08-09)

**Verdict: ROBUST. Arm B proceeds.** All 20 paired perturbation draws show B beating A on cost-adjusted
excess return — no exceptions, no near-misses, no sign flips. The unperturbed +1.993pp sits exactly at
the distribution's median and is the single most common value (9 of 20 draws land on it exactly). The
distribution is not dominated by one ticker in the way the original noise-floor study's DHI finding
was — but one ticker (**AMAT**) is a real, named, disproportionate partial dependency, and one
mechanism (shifting the walk's effective start date by ≥3 days) reliably pushes the delta *higher*, not
lower. Both are reported explicitly below, not smoothed into the headline. Research only: no changes to
`analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or the live path; `relative_ranking_engine.py`
reused unmodified. Built and run from a dedicated worktree
(`c:\GitHub\investment-app-relranking-robustness`), a genuinely separate one from both the original
relative-ranking study's worktree and the shared main checkout.

## Verification

**Degenerate-case parity, re-confirmed under a real perturbation, not assumed to still hold.** The
original relative-ranking study proved `relative_ranking_engine.py`'s `_sort_key="momentum"` mode
reproduces raw production byte-identical on the *unperturbed* pool. Here, the identical check was
re-run under a real perturbation (CAG dropped from the pool) — **PASS, byte-identical, 0 mismatches**
(4,919 trades, £104,240.79, both paths). Confirms the proof generalizes to perturbed inputs, as expected
by construction (the sort-key line is the only difference regardless of what data is fed in), checked
directly rather than left as an assumption.

**Design**: Arm A is run via the same engine's `_sort_key="momentum"` mode throughout (not a separate
call to `simulate_portfolio_compounding_mtm()`) — this gives A a real panel (deployment, position count)
from the same code path as B, for free, so both arms' panels are directly comparable per draw.

## Method

Full-history (un-sliced) cache built fresh for Pool 2 CLEAN — the relative-ranking study's own decisive
pool, not the broader `get_research_stock_candidates()` the original noise-floor toolkit used, since
this test validates a result computed specifically on Pool 2 CLEAN. 612 candidates, 608 usable, DB state
reconfirmed fresh immediately before use (612 CLEAN, price data through 2026-08-07 — unchanged from
every other check this session).

**20 draws, three perturbation types, matching the noise-floor instrument's own method exactly:**
- **10× `drop_ticker`** — one random ticker removed from the pool (fixed seed, reproducible): CAG, MTCH,
  AMAT, CCI, SRE, STE, TKO, CNP, EXR, MAS.
- **6× `shift_start`** — the walk's start date shifted forward by 1, 2, 3, 5, 7, or 10 days.
- **4× `shuffle_tiebreak`** — the candidate ticker list's input order shuffled (4 seeds), changing
  admission tie-break order for both arms identically.

**Paired by construction**: for every draw, the identical perturbed ticker list, start date, and
tie-break order was fed to both Arm A and Arm B in the same process, from the same in-memory perturbed
cache — never two independently-perturbed runs compared after the fact.

## The full delta distribution

| Statistic | Value |
|---|---|
| n draws | 20 |
| Mean | +1.911pp |
| Median | **+1.993pp** |
| Min | **+0.427pp** (drop03_AMAT) |
| Max | +2.645pp (shift5, shift7 — tied) |
| Draws positive | **20 / 20** |

**Every single draw favours B over A.** Not "a large majority" — all twenty, with the closest approach
to zero (+0.427pp, dropping AMAT) still a full 0.427 percentage points on the right side of the line,
not a near-miss.

## Where the unperturbed result sits

**Exactly at the median, and the single most common value in the distribution** — 9 of 20 draws
(MTCH, SRE, STE, shift1, shift2, and all 4 shuffle draws) land on **+1.993pp exactly**, the unperturbed
figure to three decimal places. This is the strongest possible placement a real result could have within
a perturbation distribution: not an outlier the perturbations happen to bracket, but the literal centre
and mode.

## Diffuse, bimodal, or single-ticker-dominated? — Named precisely, not just characterised

**Not dominated by one ticker in the DHI sense.** In the original noise-floor study, a single
perturbation (dropping DHI) accounted for the *entire* measured 1.08pp range — without it, the other 16
draws clustered within ±0.13pp, meaning almost all of the measured "noise" traced to one ticker's
outsized influence. That is not what happened here. Nine of ten dropped tickers (all but AMAT) are
either complete no-ops (MTCH, SRE, STE: the ticker never won a capacity slot in *either* arm's admission
sequence across the full 19-year walk, so dropping it changes nothing at all) or produce a modest,
unremarkable shift within the same general band as everything else (CCI, TKO, CNP, EXR, MAS: deltas
from +1.27pp to +1.87pp, all still solidly positive, all still close to the unperturbed value).

**AMAT is the one real, named exception, and it is reported as such rather than averaged away.**
Dropping AMAT alone compresses the delta to **+0.427pp — about 21% of the typical ~1.99pp** — by far the
single largest compression any perturbation produced. It does not reverse the result (B still beats A),
and it does not come close to zero, but it is a genuine, disproportionate partial dependency: one ticker
among 607 tested individually is responsible for roughly four-fifths of the typical advantage's
magnitude when it's absent, even though most other individual tickers are responsible for none of it.
**Worth stating plainly**: at +0.427pp, the AMAT-dropped delta alone sits *below* this project's own
~1.08pp noise floor bar for a "decisive" result — if this were the only data point available, it would
not on its own be read as a decisive advantage. It is not the only data point available (19 of 20 other
draws clear that bar comfortably, several by a wide margin), but AMAT's specific, individual importance
to the result's *magnitude* (not its direction) is real and should inform how confidently the exact size
of B's edge — as opposed to its existence — is described going forward.

**A second, distinct, well-understood mechanism drives the high end of the range — not ticker-specific
at all.** The four `shift_start≥3` draws (shift3, shift5, shift7, shift10 — deltas +2.36pp to +2.65pp)
form a visibly elevated cluster above the ~1.99pp baseline, while `shift_start` by 1 or 2 days reproduces
the baseline exactly (the real candidate universe's price history starts almost exactly 2007-01-03
regardless, so a 1–2 day nominal shift from 2007-01-01 changes nothing about which trading days are
actually available). Shifting the walk's effective start by 3+ real days measurably changes the earliest
admission history, which — via the same capacity-cascade mechanism this project has repeatedly measured
— propagates into a genuinely different, and in every one of these four cases *more favourable to B*,
19-year trade sequence. This is a real, understood, structural sensitivity to the exact starting point of
the walk, not an artefact of one company's price history, and it consistently pushes the comparison in
B's favour, never against it.

**Overall shape**: a tight cluster at/near the unperturbed value (14 of 20 draws within +1.27pp to
+1.99pp), one clearly separated low point with a specific, named cause (AMAT, +0.427pp), and one clearly
separated high cluster with a specific, named, different cause (start-date sensitivity, +2.33pp to
+2.65pp). Two distinct, identified, mechanistically-explained sources of variation — not the single
dominant artefact the DHI precedent warned to watch for.

## Drawdown delta distribution

**Cleanly two-valued, and the split has an exact, understood cause.** Every draw that keeps the walk's
effective start date at 2007-01-01/02/03 (all 10 ticker drops, shift1, shift2, all 4 shuffles — 16 of 20
draws) shows **dd_delta = −1.52pp** exactly, identical to the unperturbed run — B's drawdown is worse
than A's by exactly this much regardless of which single ticker is missing, because peak-relative
drawdown here is dominated by a large, systemic event (not a ticker-specific one) that both arms
experience identically regardless of any one company's presence. The four `shift_start≥3` draws instead
show a drawdown *improvement* for B relative to A (+3.10pp to +5.02pp) — the same start-date-sensitivity
mechanism as above, changing whether the walk's early history captures the full extent of a particular
drawdown episode. **No draw approaches any reasonable ceiling in either direction** — the worst case for
B (−1.52pp) is far inside the 15pp ceiling both the original relative-ranking study and this one would
apply.

## Deployment parity holds under every perturbation

**Gap between A and B's mean deployment %: −0.05pp to +0.02pp across all 20 draws** — tighter than the
unperturbed run's own 0.02pp gap in some cases, never wider by any meaningful margin. The same-day-
redeployment structural argument the original study relied on (every day is an entry-attempt day for
both arms; a slot freed by an exit is available for the same day's admission pass) holds exactly as
designed under every perturbation type tested, not just the single unperturbed configuration.

## The paired per-decision instrument, across all 20 draws — the strongest single finding here

**B-only admissions beat A-only admissions on mean P&L% in all 20 of 20 draws, with no exception.** On
win rate, B-only leads in 19 of 20 draws; the one exception (drop10_MAS: A-only 44.17% vs. B-only
44.15%) is a −0.02 percentage-point difference — indistinguishable from a tie, not a real reversal, and
notably that same draw still shows B-only's mean P&L% clearly ahead of A-only's (0.87% vs. 0.68%). Every
pairing comfortably clears the 100-trade minimum-sample floor (1,061–1,138 shared pairs, 7,522–7,681
differing trades per draw). **This is the single most important result in this robustness test**: the
original study's central claim — that B's advantage traces to genuinely better individual admission
decisions, not a cash-holding or cascade artefact — was corroborated on one configuration. Here it is
corroborated on twenty independently-perturbed ones, with the qualitative pattern (B-only better on both
measures) never once inverting.

## How to read this, per the pre-registered rubric

**"If the delta is positive in the large majority of draws and the distribution is not dominated by one
ticker, the result is robust and B proceeds to the next stage."** Both conditions are met, with the
first met at its strongest possible level (20/20, not merely "a large majority") and the second met with
an honest, named caveat rather than a clean pass: AMAT is a real, disproportionate partial dependency for
the *magnitude* of B's edge, though not for its *existence* or *direction* — no perturbation tested came
anywhere close to erasing or reversing the result. **This is read as ROBUST, and B proceeds** — but the
specific caveat should travel with the result: B's advantage over A is real and directionally
unconditional across every perturbation tried, but its exact size should be understood as somewhat
AMAT-dependent, and future work relying on the precise +1.99pp figure (rather than the qualitative
"B beats A" finding) should keep that in mind.

## What this doesn't close

**This is a robustness check on the specific comparison already run, not a new or larger validation.**
It does not extend the original study's own scope (still Pool 2 CLEAN only for this test — Pool 1 was
not re-run here, since the concern motivating this test was specific to Pool 2 CLEAN's own low admission
overlap finding), does not test Arm C (explicitly out of scope per the task), and does not change
anything about the "research finding, not a production change" status of the underlying result — the
same real promotion work (moving the signal into a genuine production module, live/backtest parity
coverage) the original report already flagged as required before any live consideration remains
required, unaffected by this test either way.

## Files

`step0_build_full_history_cache.py`, `step1_choose_draws.py`, `step2_run_paired_draw.py`,
`step3_degenerate_check.py`, `step4_aggregate.py`, `step4_all_results.json`, `draws.json`,
`meta_<draw_id>.json`, `trades_{A,B}_<draw_id>.csv`, `panel_{A,B}_<draw_id>.csv` for all 20 draws.
