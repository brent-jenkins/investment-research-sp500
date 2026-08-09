# Defensive-sleeve allocation — pre-registration (2026-08-08)

**PRE-REGISTRATION ONLY. Nothing has been run, nothing has been built.** No cache, no engine, no
production file touched. Largest change proposed to date — a new asset class and a sleeve-
allocation mechanism — so this gets the full version of the process: criteria fixed, ambiguities
resolved and stated, data-acquisition plan specified, before anything exists that could tempt a
rationalization after the fact.

## 0. Motivation and the relevant prior art (not re-litigated, cited precisely)

**The gap this tests.** The live system's only alternative to equities is cash. When the momentum
signal turns negative it goes flat and earns nothing. This tests whether routing that capital into
a defensive sleeve — rather than to cash — beats buy-and-hold, which tuning the equity signal
itself has not (CLAUDE.md: the strategy trails all four broad-index benchmarks on raw CAGR over the
full window; cost-adjusted CAGR trails `^SP500TR` by −7.75pp per the measurement toolkit).

**Two specific, directly-relevant prior results, both read before writing this — cited precisely,
not gestured at:**

1. **V2 Phase 2 tested a structurally similar idea and rejected it, hard.** From
   `CLAUDE_HISTORY.md`'s "V2 architecture — phased rebuild, Stage 1": *"Phase 2 (rejected): a
   separate, multiplicative danger/exposure gate (S&P below its own 200-day average, and/or VIX ≥
   30) layered on top of Phase 1. Every variant underperformed the ungated Phase 1 baseline — the
   same 'a broad, frequently-firing overlay suppresses genuine opportunity more than it protects'
   lesson found repeatedly in V1's own research history."* **The material difference this study
   tests**: Phase 2's gate reduced exposure to cash (or blocked entries outright) when triggered.
   This proposal reduces exposure to a *return-bearing* defensive sleeve instead. That is a genuine,
   untested difference, not a re-run of Phase 2 under a new name — but Phase 2's result is the
   correct prior to be skeptical against, and this document does not pretend otherwise.
2. **The admission-redesign study (`research/admission_redesign_prereg_2026-08-08/REPORT.md`,
   same day) found an attractive portfolio-level result — better ratio, better CAGR, much better
   drawdown — that failed on exactly two mechanisms**: a deployment guard (the win was substantially
   attributable to holding more cash, not better decisions) and a per-decision paired instrument
   (a coin flip, 50.1–50.2% in both pools, once compounding/cascade effects were controlled for).
   **The exact same failure mode is live here**: a sleeve-switching mechanism that reduces equity
   exposure during a drawdown will *mechanically* show a better drawdown number than an always-
   invested baseline, independent of whether the defensive sleeve itself does anything useful. This
   pre-registration's deployment/exposure guard (§3) and paired instrument (§4) are built
   specifically to not let that happen unnoticed a second time this week.

**Also read**: the measured noise floors (cost-adjusted CAGR ~1.08pp range from 17 trivial
perturbations, 16/17 within ±0.13pp; 16-block pass count 3-block range) and the capacity-cascade
note (a small, targeted change produces a much larger change in the resulting trade sequence than
its own size suggests, and portfolio-level comparisons systematically inflate small effects versus
a per-decision view) — both inform the margin and instrument choices in §3–§4 directly, not just as
background.

---

## 1. Structure

### 1a. The two sleeves

**EQUITY — unchanged, not tuned here.** Same signal (12-month trailing momentum), same gate
(`passes_entry_gate`/`passes_momentum_floor`/`is_reentry_blocked`), same momentum-descending
ranking, same ATR stop/`SIGNAL_OFF`/`MAX_HOLDING` exits, same VaR sizing, same live-candidate
universe. The only change from today's production behaviour is **when** new equity entries are
evaluated — see §1c, this is a real, stated behavioural change and is not glossed over as "unchanged."

**DEFENSIVE — equal-weight thirds of three components. Proposed, T212-tradeable instruments,
coverage confirmed directly (not assumed) before proposing them:**

| Component | Instrument | Rationale | Coverage confirmed (yfinance, fresh check 2026-08-08) |
|---|---|---|---|
| Short-duration Treasuries | **SHY** (iShares 1-3 Year Treasury Bond ETF) | Standard, highly liquid short-duration Treasury proxy | 6,045 rows, 2002-07-30 → 2026-08-07 — full 2007-2026 coverage |
| Long-duration Treasuries | **TLT** (iShares 20+ Year Treasury Bond ETF) | Standard, most liquid long-duration Treasury ETF; genuine duration barbell against SHY | 6,045 rows, 2002-07-30 → 2026-08-07 — full coverage |
| Defensive equity sectors | **XLU + XLP + XLV**, equal-weighted (Utilities / Consumer Staples / Health Care Select Sector SPDRs) | The three named sectors, as an equal internal sub-blend — see §1b for why this is a sub-blend, not three separate top-level components | XLU/XLP/XLV: 6,689 rows each, 2000-01-03 → 2026-08-07 — full coverage, predates the study window by 7 years |

**Rejected alternative, stated for the record**: **BIL** (SPDR Bloomberg 1-3 Month T-Bill ETF) was
considered for the short-duration leg instead of SHY — closer to a true cash-equivalent — but its
own history only starts **2007-05-30**, not 2007-01-01, which would have silently truncated the
earliest ~5 months of the study window. SHY's coverage is clean back to 2002; chosen for that reason
specifically, not merely as a default.

**T212 tradeability — not yet confirmed, flagged as a required pre-run step, not assumed.**
`instrument_resolutions` currently has zero rows for any of SHY/TLT/XLU/XLP/XLV/IEF/BIL (checked
directly). All five proposed instruments are large, liquid, US-listed ETFs and T212 Invest is known
to carry broad US ETF coverage, which makes support likely — but "likely" is exactly the kind of
assumption this project's own standing practice (instrument resolution, never silently assumed) 
exists to catch. **Before any real run, each proposed ticker must be resolved through the same
`Trading212Client` instrument-search mechanism stocks already use, and a `FAILED` resolution for
any of the five must be resolved (a substitute instrument chosen and re-coverage-checked) before
that arm is trusted.**

### 1b. Within-sleeve weights — interpretation stated explicitly

The instruction specifies "equal-weight blend of [A] short Treasuries, [B] long Treasuries, and [C]
defensive equity sectors (utilities/staples/healthcare)" — three named top-level items, with C
itself naming three sectors. **Interpreted as: three top-level components at 1/3 each, with C's own
1/3 split equally across XLU/XLP/XLV (so each individual sector ETF receives 1/9 of the total
defensive sleeve, XLU+XLP+XLV summing to 1/3).** This is a stated interpretation, not the only
possible one (a flat five-way equal split — SHY/TLT/XLU/XLP/XLV each at 1/5 — is the visible
alternative) — flagged here so it can be corrected before anything is built, not discovered after.
**Weights are fixed; the switching signal decides how much defensive, never which defensive** — per
instruction, this is deliberately not adaptive, and no sweep of alternative internal weightings is
planned in this pass.

### 1c. Three clocks, and what "equity unchanged" actually means once they interact

- **Sleeve allocation: monthly rebalance only.** The switching signal (§1d) is evaluated once a
  month; the target equity/defensive split for the month is set at that point and does not change
  until the next rebalance, regardless of what the signal does intra-month.
- **Crisis breaker (≥7% single-day S&P fall): immediate, unchanged.** Continues to force-exit open
  equity positions and block new equity entries for the crisis window + cooldown, exactly as today —
  scoped to the equity sleeve only, not a new mechanism, not a second, redundant defensive trigger.
- **Stock-level exits (`STOP_LOSS`/`SIGNAL_OFF`/`MAX_HOLDING`): continuous, unchanged.** Fires any
  day, exactly as today.

**The stated, load-bearing interpretation, worked through explicitly because the instruction's own
two clauses are in tension until this is resolved**: *"EQUITY... UNCHANGED... same gate, same
ranking"* vs. *"Capital freed mid-month parks in the DEFENSIVE sleeve... does not buy a new equity
position."* Read together, and cross-checked against the admission-redesign study's own skeleton
(explicitly named as this study's forerunner): **new equity admissions are evaluated only at the
monthly sleeve-rebalance point, using the exact same gate/ranking/sizing logic as today (nothing
about *which* stocks or *how they're sized* changes) — but the *timing* of new entries is now
monthly, not continuous.** Stock-level exits remain continuous and can fire on any day, in either
regime — an individual stock's own trailing-12-month signal can turn off even while `^GSPC`'s
index-level momentum is still positive, since these are different signals computed over different
instruments. Any capital freed by such a mid-month exit is swept into the defensive sleeve
(top-up purchase of the fixed SHY/TLT/XLU-XLP-XLV weights) rather than sitting idle or re-entering
equity immediately — this is the direct fix for the exact failure mode the admission-redesign study
flagged, and is why this design routes freed capital to defensive rather than repeating that
study's own idle-cash mechanic.

**Because allocation is binary (§1d), this resolves cleanly in practice, not just in theory**: during
an equity-favoured month, the equity sleeve behaves exactly as today's continuous system *except*
that a position closed mid-month is not replaced until the next rebalance; during a defensive month,
the proportional sell-down (§1e) has already brought equity to zero, so there is nothing left for
the continuous exit checks to act on until equity is re-entered at a future rebalance.

### 1d. Switching signal

`^GSPC` trailing 12-month momentum — already in `price_data`, daily, unrevised, no new data source.
**Binary for this first pass**: full target-equity when positive, full target-defensive when
negative. A graduated curve (e.g. a continuous function of momentum magnitude) is **flagged as a
later variant, not implemented** — it adds a free parameter, and per the noise-floor finding this
project has already measured, a comparison with an extra free parameter needs a correspondingly
larger effect to be trusted; not attempted in this pass.

### 1e. De-risking / re-risking method

**Equity → defensive (de-risking): proportional sell-down across holdings to hit the target.**
Stated plainly: **under binary allocation, this is a stated but currently vacuous distinction** —
target equity weight at a defensive flip is exactly 0%, so every open equity position is closed
regardless of any "proportional vs. weakest-momentum-first" ordering rule; there is no partial
target for an ordering choice to act on. The instruction's own reasoning (Part 2 found entry
momentum barely predicts individual outcomes, so selective liquidation assumes predictive power not
found to exist) is preserved and will matter for a graduated-allocation variant, where a *partial*
sell-down would need an ordering rule — recorded here for that future pre-registration, not acted on
in this one.

**Defensive → equity (re-risking), not explicitly specified in the instruction — stated as the
natural extension, flagged for confirmation.** Proposed: liquidate the defensive sleeve in full,
then run the standard equity admission mechanism (gate → momentum rank → capacity-constrained
greedy fill) with the freed capital, exactly as a normal monthly-rebalance admission round. No new
mechanism invented.

---

## 2. Arms — a real discrepancy in the instruction, flagged rather than silently resolved

**The instruction says "four runs" but then enumerates Baseline + Blend + three diagnostic singles
— five arms, not four.** Read literally, the explicit list is more specific than the summary count,
so **five arms are pre-registered**, with this discrepancy flagged for correction rather than
guessed past silently:

| Arm | Defensive sleeve composition |
|---|---|
| **Baseline** | Current production system, entirely unchanged (no sleeve mechanism at all) |
| **Blend** — the arm being tested | SHY 1/3, TLT 1/3, (XLU+XLP+XLV equal) 1/3 |
| Single — short Treasuries | 100% SHY whenever defensive |
| Single — long Treasuries | 100% TLT whenever defensive |
| Single — defensive equity sectors | 100% (XLU+XLP+XLV equal) whenever defensive |

The three singles are explicitly **not** competing entries — they exist to make the Blend's result
attributable. If Blend beats all three singles on the primary metric, that is a genuine
diversification benefit. If any single matches or beats Blend, the other two components are ballast
— **this comparison will be reported explicitly as its own table, not left implicit in the headline
numbers.**

---

## 3. Criterion — specified before any run

**Primary metric: excess return over the dividend-adjusted S&P 500, per block and cumulatively —
not absolute CAGR.** Benchmark: `^SP500TR` (S&P 500 Total Return, dividends reinvested) — the same
series already used and coverage-confirmed twice this project (selection audit: 4,930 rows,
2007-01-03 → 2026-08-07; measurement toolkit: 5,181 rows, 2006-01-03 → 2026-08-07). Coverage
re-confirmed sufficient for the full 2007-2026 window without a fresh check, since both prior
confirmations already cover it completely and the series doesn't revise.

**Required margin: +2.0pp cumulative excess CAGR over `^SP500TR`, sustained.** Adopting the
instruction's own proposed number, justified explicitly rather than accepted on authority: the
measured noise floor for cost-adjusted CAGR under *trivial* single-perturbation draws is ~1.08pp
(worst single draw), typically ±0.13pp. A sleeve-allocation mechanism is a far larger structural
change than a trivial perturbation, and this metric (excess return over an external benchmark) is a
different measurement from the internal noise-floor's own cost-adjusted CAGR — so 2.0pp is roughly
1.85x the single largest internal-perturbation deviation ever observed, applied here as a margin
against a different, external yardstick, deliberately conservative given the metrics aren't the same
one. **"Sustained" operationalized concretely, not left as a qualitative word**: cumulative excess
CAGR ≥ +2.0pp over the full window, **AND** excess return positive in at least 10 of 16 blocks (a
majority, not a result carried by one or two outlier blocks) — proposed, open to revision, but a
real number rather than an unstated intuition.

**Deployment/exposure guard — mandatory, threshold stated in advance, reasoned from first
principles since no prior number exists for this specific design (unlike the admission-redesign
guard, which could anchor to an already-observed baseline figure).**
- **Mean equity exposure (% of account in equity positions) — reported prominently for every arm,
  not gated.** Varying equity exposure across regimes is the mechanism under test, not a bug; gating
  on it directly would penalize the study for doing what it's designed to do.
- **Mean total deployment (equity + defensive combined, % of account not sitting as literal idle
  cash) — gated.** Proposed threshold: **any tested arm (Blend or a single) whose mean total
  deployment falls more than 10 percentage points below Baseline's own mean deployment (Baseline's
  own figure, established fresh in this run, not assumed — the admission-redesign study's own
  Pool 2 CLEAN baseline measured 63.72%, offered only as a rough prior, not the number this guard
  will actually use) fails this condition, regardless of its return numbers.** Reasoning: unlike the
  admission-redesign's wait-for-rebalance design (where *some* deployment reduction was structurally
  expected and tolerated at a looser 15pp), this design's entire purpose is to replace idle cash with
  a productive defensive holding — a materially lower total-deployment figure than baseline would
  mean capital is still sitting idle somewhere despite the redesign's own stated goal, which is a
  more surprising and more disqualifying finding here than in that study, hence the tighter margin.

**Drawdown: first-class criterion, not a check.** Peak-relative max drawdown reported for every arm
alongside excess return, not subordinated to it. No arm is treated as passing on excess return alone
if its drawdown is materially worse than Baseline's own — "materially worse" will be stated as an
exact number once Baseline's real figure exists to set the comparison against (not fabricated here
in the absence of any run); flagged as a genuine open item rather than a filled-in placeholder.

**Block-pass count: corroborating only.** Measured resolution floor is 3 blocks (6–9 range from the
17-draw perturbation study) — any block-pass delta of ≤3 blocks will not be read as evidence either
way, exactly as established for the admission-redesign study.

## The walk-away sentence

**I will not adopt this redesign if Blend's cumulative excess return over `^SP500TR` fails to clear
+2.0pp with positive excess return in at least 10 of 16 blocks, or if it wins that margin mainly by
sitting out of both sleeves (mean total deployment falling more than 10pp below Baseline's), or if
its drawdown is materially worse than Baseline's own, or if the per-decision instrument (§4) shows
the defensive sleeve's own regime-period returns are not distinguishable from a coin-flip against
whichever single component the data suggests is doing the real work — the same failure mode already
found twice this week, in the V1 Phase-2 gate and in the admission-redesign's own deployment guard.**

---

## 4. The paired per-decision instrument — addressed directly, not deferred

**Exact `(ticker, entry_date)` pairing on the equity side is expected to have HIGH overlap here,
unlike the admission-redesign study — stated as a prediction to be checked, not assumed true.**
All five arms share the identical switching signal, the identical monthly equity-entry clock, and
the identical gate/ranking logic (§1a) — the only structural difference between them is *what the
defensive sleeve holds*, which has no mechanical path to changing which stock gets admitted on which
date. The one channel that *could* still cause equity-side divergence is compounding-path sizing
(each arm's account balance diverges based on its own defensive-sleeve performance, so a stock's
dollar position size — and, at the margin, whether it clears `is_position_size_viable()` or a
capacity threshold — can differ even when the decision "admit this ticker this month" is identical).
**Planned check (an engine-correctness verification, not the comparative instrument): confirm
equity-side `(ticker, entry_date)` overlap across all five arms is high (a specific numeric floor —
proposed ≥90% — to be set once real trade counts exist); a low overlap would indicate a bug in the
new engine, not a finding about defensive-sleeve quality, and would block trusting any of the results
above until understood.**

**The actual comparative instrument for defensive-sleeve quality is a period-level, not a trade-level,
pairing — proposed because it is more natural here than porting the admission-redesign's month-level
ticker adaptation.** Since all four non-baseline arms share one switching signal, they are in
"defensive mode" over the exact same set of calendar date ranges. **Proposed instrument: identify
every distinct, contiguous defensive-regime episode (a maximal run of consecutive defensive months,
shared identically across arms), and for each episode, compare each arm's own defensive-sleeve
percentage return over that exact window.** This is decision-level and capacity/cascade-free by
construction — it compares realized *returns* over an identical, shared time window, not $ P&L,
which sidesteps compounding-path contamination entirely rather than needing to control for it after
the fact.

**Minimum-sample threshold, stated in advance rather than improvised**: the number of distinct
defensive episodes across 2007-2026 is not yet known (this is itself a first, cheap output of any
real run) but is expected to be small — plausibly single digits to low teens, given this is
governed by multi-month macro regime shifts, not high-frequency events. **Proposed floor: fewer than
5 distinct episodes will be reported as a bare count and explicitly NOT interpreted as evidence
either way** — the same "report the count, don't force an interpretation" discipline already
established for the admission-redesign study's exact-pairing fallback, applied here to the
structurally-different reason this comparison might be underpowered (few macro regimes in 19 years,
not a cadence mismatch).

---

## 5. Data acquisition — specified now, not improvised mid-run

**The core problem, stated precisely per the instruction's own framing**: ingestion scope
(`backfill_price_history()`/`pull_incremental()`) is stocks-only by design, and both functions'
first-time-download paths call `_screen_newly_backfilled_ticker()` unconditionally — which calls
`verify_ticker_identity()`, whose Check A can only ever affirm `quote_type == "EQUITY"`. Today's fix
(see CLAUDE.md's "Data-quality backstop" entry) scoped `scan_unverified_tickers()` — the *ongoing
backstop* — to stocks only, but deliberately did **not** touch the *ingestion-time* screen itself,
since that would have been a live-path change beyond that fix's own explicit scope. **Using
`backfill_price_history()` for these five ETF tickers would reproduce the exact spurious-quarantine
bug found and fixed today** (confirmed directly: `SPY`/`QQQ`/`MDY`/`IJR`/`^IRX`, backfilled earlier
this session via that exact path, were all auto-flagged `QUARANTINED` at ingestion time — harmless
only because nothing reads their quarantine status, the same "inert today, not by design" property
CLAUDE.md's new entry warns against relying on).

**Proposed plan: do not use `backfill_price_history()`/`pull_incremental()` for these five tickers
at all.** Fetch price history via a direct, read-only `yfinance` call in a dedicated research
script — the same technique already used to confirm coverage for this document (§1a) — and hold the
resulting frames in a research-local cache (pickle), never writing them into the shared `price_data`
table via any ingestion code path. This is sufficient for the study's own needs: none of these five
tickers will ever be part of `get_research_stock_candidates()`/`get_live_stock_candidates()` (they
are not stocks, not part of the known-stock universe), so there is no functional requirement to have
them in `price_data` at all for a research run, and keeping them out of it is what avoids the
ingestion-time screen entirely, correctly, without needing to modify `data/ingestion.py` a second
time in one day for a second, narrower reason.

**Explicitly out of scope for this pre-registration, flagged as a real prerequisite for any future
adoption path (not resolved here)**: if this mechanism were ever adopted, defensive instruments would
need a genuine, non-spurious *production* ingestion path — either a scoped exemption in
`_screen_newly_backfilled_ticker()` analogous to today's `scan_unverified_tickers()` fix, or a
separate, asset-class-aware verification concept entirely. Not designed here, and not needed for a
research-only run.

**Signal computation note**: the defensive sleeve does not need `precompute_ticker()`'s stock-signal
machinery (momentum gate, `is_member()`, ATR stop) at all — per §1c, defensive holdings are not
individually momentum-gated; they are held at fixed proportional weights whenever the sleeve-level
switching signal is negative. The research cache needs only daily close (and open, for realistic
fill modelling consistent with the rest of this project's own stop-fill conventions) — a materially
simpler data requirement than the equity side.

---

## 6. Sanity gate

Before any Blend/single arm is trusted: the **new** engine, run in its exact production-equivalent
degenerate configuration (monthly equity clock disabled — i.e. daily, matching today; defensive
sleeve weight forced to zero) must reproduce a real, already-published baseline trade set
byte-identical — the same discipline `redesign_engine.py`'s own degenerate-case check already
established and passed for the admission-redesign study. The specific reference figure will be
whatever the current, freshly-re-confirmed Pool 2 CLEAN baseline is at run time (per this project's
own "a P&L reference figure decays with database state" lesson, recorded today — the figure will be
re-established fresh at run time, not assumed from this document).

---

## 7. Under-determined parameters — flagged now, not swept in this run

- **§1b's equal-thirds-with-a-sub-blend interpretation** (vs. a flat five-way split) — stated, not
  the only reading, flagged for confirmation before building anything.
- **The +2.0pp / 10-of-16-blocks "sustained" operationalization** (§3) — a reasoned first-pass
  number, not derived from a noise-floor measurement of this exact metric (none exists yet).
- **The 10pp deployment-guard margin** (§3) — reasoned from first principles (tighter than the
  admission-redesign's 15pp, for a stated reason), not calibrated against any real observed number
  for this specific design.
- **The ≥90% equity-side overlap floor and the <5-episode paired-instrument floor** (§4) — both
  provisional, to be sanity-checked against real trade/episode counts once they exist.
- **Graduated (non-binary) allocation and adaptive within-sleeve composition** — both explicitly
  deferred per the instruction, not implemented or swept here.
- **Weakest-momentum-first de-risking for a graduated variant** — recorded as the flagged
  alternative for that future pre-registration, not built or tested here (currently vacuous under
  binary allocation, §1e).

---

## What gets built if approved (not done yet)

1. A new, additive engine (working name `simulate_sleeve_allocation()`), reusing
   `analysis.decision`/`analysis.volatility` functions unmodified for the equity sleeve exactly as
   `redesign_engine.py` did, plus new sleeve-allocation/rebalance/de-risking logic for the defensive
   side — with the degenerate-case parity check (§6) required before any real run is trusted.
2. A research-local price cache for SHY/TLT/XLU/XLP/XLV, built via direct `yfinance` calls, never
   touching `price_data` or `ticker_data_quality` (§5).
3. T212 instrument-resolution check for all five tickers (§1a) — a required pre-run step, not
   assumed.
4. The period-level defensive-episode comparison (§4) and the equity-side overlap engine-correctness
   check, as their own small functions.
5. `research/sleeve_allocation_prereg_2026-08-08/REPORT.md` (or the actual run date), reporting: excess
   return per block and cumulatively for all five arms vs. `^SP500TR`; the walk-away sentence checked
   explicitly; mean equity exposure and mean total deployment per arm; drawdown per arm; the
   Blend-vs-three-singles attribution table; the defensive-episode paired comparison (or its bare
   count, if under the 5-episode floor); block-pass count as corroboration only.

**Nothing above has been executed. No cache, no engine, no run.**

## Constraints re-confirmed

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path,
including `data/ingestion.py`'s ingestion-time screen (§5 explicitly routes around needing to touch
it, rather than touching it). Every new function this study needs is additive and research-only.
Nothing adopted, nothing merged, nothing deployed — this document is a plan for review.

---

## VERDICT: SHELVED (2026-08-09) — not run, not adopted

The pre-run instrument feasibility check (`INSTRUMENT_FEASIBILITY_REPORT.md` in this directory)
found two separate, each-independently-sufficient reasons not to run this study as designed:

1. **The 2008 block is untestable with real tradeable instruments.** The earliest date at which all
   five components (SHY/TLT/XLU/XLP/XLV UCITS equivalents) have genuine history is 2015-07-07,
   bounded by the sector legs — over six years after the 2008-03-23→2009-06-13 crisis block ends. A
   defensive sleeve's entire rationale is protection against exactly this kind of event; a study that
   can only test from 2015 covers one genuine equity drawdown (2022) and one fast V-shaped one (2020),
   neither of which is the scenario the sleeve exists for. Testing the mechanism in a window
   containing no crisis is not a smaller version of the intended test — it doesn't test the thing.

2. **The Acc/Dist accounting mismatch on all three sector legs is not confined to the untested
   window.** No sector-matched, USD-denominated, distributing UCITS equivalent of XLU/XLP/XLV exists
   on T212 — only accumulating share classes. Measured against the real US series: +2.86pp/yr (XLU),
   +2.53pp/yr (XLP), +1.42pp/yr (XLV), compounding to 35–69% cumulative divergence over 11 years.
   This is larger than any effect size this study was ever likely to detect — backtesting the US
   series and holding the Acc UCITS version means simulated and achievable returns would differ by
   more than the margin needed to declare a win. Not a stated limitation; a measurement error bigger
   than the signal.

Combined with priors already on record from the structurally similar gate-width study (Phase 2:
every variant underperformed) and the fact that this design's plausible upside was always bounded by
"Treasury yield during defensive periods minus mistiming cost," the study is shelved rather than run
in a degraded form. The idea is not judged wrong — the instruments actually available to hold it
don't support testing it honestly.

**Salvaged from this investigation:**
- **Price-data artifact, catalogued in CLAUDE.md**: the GBP-denominated share class of the SHY UCITS
  equivalent (`IBTSl_EQ`, ISIN `IE00B14X4S71`) has an unexplained ~145x price cliff on 2009-01-02 —
  the same artifact class this project has spent real effort cataloguing for US tickers. Recorded so
  nobody trips over it if this ISIN is ever revisited.
- **The underlying problem this study was trying to solve — idle cash between equity positions has
  no yield — remains real and unaddressed.** The cheapest honest fix isn't a sleeve: it's parking
  flat capital in a single short-duration instrument. SHY's UCITS equivalent (`IBTSs_EQ`, the USD
  share class) supports this cleanly from 2009-01-05 — one instrument, one accounting treatment, no
  blend, no switching signal. Not pursued now; worth considering later as a much smaller, separately
  pre-registered change.

No code changed on the live path. No study arm was run. Worktree retained for reference; branch not
merged into a new engine or config, only this documentation.
