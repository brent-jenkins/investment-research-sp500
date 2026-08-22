# Hard slot-count concentration study — PRE-REGISTRATION (2026-08-22)

**Status: pre-registration only. Nothing has been run, no engine has been built, no cache has been
constructed, no database has been touched.** Submitted for approval before any arm executes. No
changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path are
proposed at any point, including on a positive result.

---

## 0. What this tests, and why it is not a repeat of the position-count study

`research/position_count_prereg_2026-08-09/` tried to reach 5 and 10 concurrent positions by raising
`per_trade_pct` to 0.14 and 0.07. Both arms realised **~12 concurrent positions against a baseline
of ~14** — closer to baseline than to their own targets — and were correctly reported **NOT TESTED**,
never as a null. That study's own closing verdict is this study's mandate:

> *"A future study wanting to test genuine concentration would need a design that enforces a hard
> slot count directly ... a different engine design, not a retune of the value tested here."*

**This study enforces the slot count directly.** Admission stops when the concurrent-position count
is full, regardless of remaining deployment capacity. `per_trade_pct` stops being the concentration
lever and becomes one of three *derived* sizing constants (§3).

**Why the earlier attempt failed is mechanically identifiable, and only one of its two stated causes
is the main one.** The position-count report attributes the failure to VaR sizing declining to fill a
raised ceiling. That is part of it. The larger part, which that study did not isolate, is the
**cash-availability clamp** — `resolve_cash_constrained_position_gbp(position_gbp, cash,
cash_fraction=0.25)`, an independent bound of 25% of *free cash* applied after the VaR ceiling. It is
a hard second concentration limit and it binds long before the `per_trade_pct` ceiling does at any
tight position count. §3.3 derives it exactly, shows it is **provably inert at baseline**, and shows
that left untouched it would cap the 3-slot arm's deployment at **50%** — reproducing the
position-count failure through a different mechanism. **Deciding this before running is the whole
point of §3.**

**Priors, stated honestly and expected to be tested rather than confirmed.** Part 7 of the selection
audit found momentum ranking sits at the **99th percentile** of 100 random-admission runs on both
frictionless and cost-adjusted CAGR — ranking works *because* it concentrates — and that baseline's
drawdown is worse than **98 of those 100 runs**. The exit-mechanics result (value comes from holding
fewer things longer) points the same way. **The likely outcome is higher returns, materially worse
drawdown, and a verdict that turns on the drawdown ceiling rather than the primary criterion.** That
is a legitimate not-adopted, not a near-miss, and it is written here so it cannot be argued around
afterwards.

**Standing hazards this design must not re-trip, read from CLAUDE.md before anything below was
written**: the measured noise floor (~**1.08pp** cost-adjusted CAGR; block-pass range **3**);
capacity-cascade amplification (a narrow change produces a trade-level reshuffle far larger than the
aggregate effect, so low overlap is not itself evidence of anything); the two prior redesigns that
each reduced to a deployment-level artefact; and the "check that reports clean for a reason unrelated
to the thing it was checking" class — §7 states, for every guard here, what its degenerate case is
and whether it can produce both answers.

---

## 1. Baseline, and a correction to the arm list before it is fixed

**Baseline is the CURRENT LIVE CONFIGURATION, uncapped**: `admission_ranking_mode: relative_rank`,
`atr_stop_multiplier: 3.0`, `stock_max_holding_days: 0` (disabled, so `resolve_var_horizon_days()`
supplies the `var_horizon_days: 30` sizing horizon), `per_trade_pct: 0.05`,
`max_total_deployed: 0.70`, `max_risk_pct: 0.01`, `min_momentum_pct: 0.02`,
`reentry_cooldown_days: 0`, `absolute_max_position_gbp: 1,000,000`.

**Preserved reference (Pool 2 CLEAN): 565 trades / £348,548.74**, peak-relative max drawdown
**−43.88%**, mean deployment **63.9–65.1%** (`research/exit_mechanics_prereg_2026-08-11/`, cell
`s3.0`; the deployment figure cross-confirmed by
`research/trailing_high_threshold_prereg_2026-08-22/`).

### 1a. "14 (baseline)" is not baseline under this configuration — an amendment requiring approval

The instruction lists arms **3, 5, 8, and 14 (baseline)**. The `14` label is inherited from the
position-count study, which ran the **previous** configuration (stop 2.0×, 30-day holding cap) and
measured a realised mean of **13.98–14.23** concurrent positions.

Under the **current** configuration that number is different. From the preserved baseline set:
565 positions × 149-day mean hold ÷ 4,930 trading days ≈ **17.1 concurrent positions** — an arithmetic
estimate, to be replaced by the direct measurement in §2. Unlimited holding raises the concurrent
count even as it collapses the trade count.

**Two consequences, resolved here rather than discovered later:**

1. **Baseline must be the UNCAPPED current configuration, and this is forced by the sanity gate, not
   chosen.** §8 requires baseline to reproduce 565 trades / £348,548.74 as a trade SET. A hard cap at
   14 against a book that naturally runs ~17 would bind, change the trade set, and break that gate.
   The control is therefore `slot_cap = ∞`.
2. **`N = 14` is promoted to a genuine fourth TREATED arm**, not the control. It is the most
   interesting arm structurally: under §3's parameterisation it reduces **exactly** to the live
   config (`per_trade_pct` 0.05, `max_risk_pct` 0.01, cash fraction 0.25 — all unchanged), so
   `N=14 minus uncapped baseline` isolates the pure effect of capping the count at the config's own
   nominal value with nothing else altered. It is the cleanest attribution point in the study.

**Five runs per pool: uncapped baseline, N=14, N=8, N=5, N=3.** Ten runs total.

---

## 2. THE MANDATORY GATE — reported before any return figure appears

**No return number for any arm is reported, stated, or referenced anywhere in the results document
until this section is complete for every arm.** The report template places it first physically, not
merely first logically.

### 2.1 Realised mean concurrent positions

**Definition, exact**: the arithmetic mean of `n_open_positions` over every trading day in the walk
(2007-01-03 → 2026-08-07, 4,930 days), read from the engine's daily panel — the same `panel_*.csv`
column the exit-mechanics engine already emits. Reported full-sample (the **gating** measure, for
comparability with the position-count study) and, as context only, steady-state (2010-01-01 onward,
excluding the structural cold start in which no trade is possible).

**Materiality test, applied verbatim from the position-count study**: an arm is **TESTED** only if its
realised full-sample mean is closer to its own nominal target N than to the uncapped baseline's own
realised mean. Otherwise it is reported **NOT TESTED** — described and explained, never folded into
the return comparison and never reported as a null.

**Expected to pass, and why the mechanism differs from the failed study**: a hard count cap has no
undershoot mode. `per_trade_pct` is a ceiling VaR may decline to reach; a slot count is a wall. Slots
can only sit empty if no candidate is eligible, and the gate passes ~200+ candidates on a typical day.
The realistic residual is `realised mean slightly below N`, from the early walk before enough history
exists and from the few genuinely candidate-free days. **If this expectation is wrong — if an arm
realises materially below its cap — that is a real finding about slot availability and is reported as
such, not smoothed over.**

### 2.2 Realised mean deployment, and the two-tier guard

**Definition, exact**: mean of `current_deployed_pct` (mark-to-market position value ÷ current
balance) over every trading day, from the same panel. Reported alongside baseline's.

| divergence from baseline (absolute, either direction) | treatment |
|---|---|
| ≤ 2.0 pp | clean |
| > 2.0 pp and ≤ 4.0 pp | **flagged**: the report must quantify how much of that arm's cost-adjusted result is attributable to the idle-cash credit and to changed market exposure, *before* its return is discussed |
| > 4.0 pp | **hard exclusion**: the arm is reported as testing cash-holding, not concentration, and is excluded from the return comparison entirely — not caveated alongside a return figure |

**Why 4pp, arithmetically rather than by feel.** `^SP500TR` compounds at 11.13%/yr over this window,
so being 4pp less invested is worth roughly `0.04 × 11.13 ≈ 0.45pp/yr` of foregone return — about 30%
of the +1.5pp bar. Beyond that, an arm's return gap is materially a statement about its market
exposure rather than its selection. The 2pp flag tier exists because the two prior studies that failed
this way (−22 to −24pp, and −50pp) were caught by a single hard threshold, and a divergence one third
of the way to the bar deserves quantification rather than a binary pass.

**Both directions.** Over-deployment flatters a concentrated arm in a rising market exactly as
under-deployment penalises it, and this parameterisation could produce either.

### 2.3 Trade-count floor — an evaluability gate, not a criterion

**Definition**: total positions opened over the full walk.

**An arm producing fewer than 50 positions is reported NOT EVALUABLE on the primary criterion** and is
described only (composition, entry years, per-position outcomes). Justification: baseline already
produces just 565 positions over 19 years at 149-day mean holds; a 3-slot arm mechanically produces on
the order of `3 × 4930 / mean_hold` positions, and if holds lengthen — which unlimited holding plus 3
slots actively invites, since winners never exit — the count could fall to a few dozen. Below ~50
positions a 19-year return figure is a statement about a handful of admission-date coincidences; no
robustness test can rescue it, and reporting it as a return result would be exactly the "lottery
ticket" outcome the instruction names.

### 2.4 Capacity lock-up and entry front-loading — the specific failure this design invites

The exit-mechanics report's §10.3 records that its winning cell "enters 144 positions in 2008 and 131
in 2009, then 6 in 2010 and single digits in several later years — capacity locks up early." **With 3
slots and no holding cap this is not a tail risk, it is the expected dynamic.** Required outputs per
arm, reported inside this gate section:

- entries per calendar year (full histogram);
- count of distinct tickers ever held;
- **share of the arm's total P&L contributed by positions entered before 2010-01-01**;
- longest single holding, and the number of trading days on which the arm held a position entered
  before 2010.

**If more than 60% of an arm's total P&L comes from positions entered before 2010, that arm's result is
reported as start-date-conditional and cannot clear the primary criterion without the start-date
perturbation of §9 having already been run and having held.** This is the one place a robustness test
is required *before* a positive verdict rather than earned by one.

---

## 3. SIZING — the crux, decided in advance

The instruction requires one answer, justified. Here it is, then the alternatives and why they were
rejected.

> **ANSWER. VaR sizing still applies; the slot count limits COUNT only. But VaR's two scale constants
> are re-derived from N, and the cash clamp is made inert, so that the slot count — not a constant
> inherited from a 14-nominal-slot configuration — is the thing that actually binds.**

Three derived per-arm parameters, all reducing **exactly** to today's live values at N = 14:

| | formula | N=3 | N=5 | N=8 | N=14 | baseline (uncapped) |
|---|---|---|---|---|---|---|
| slot cap | N | 3 | 5 | 8 | 14 | ∞ |
| `per_trade_pct_N` | `max_total_deployed / N` | 0.2333 | 0.1400 | 0.0875 | **0.0500** | 0.0500 |
| `max_risk_pct_N` | `max_risk_pct × (N_nom / N)`, `N_nom = max_total_deployed / per_trade_pct = 14` | 0.04667 | 0.02800 | 0.01750 | **0.01000** | 0.01000 |
| `cash_fraction_N` | `max(0.25, (max_total_deployed/N) / (1 − max_total_deployed))` | 0.7778 | 0.4667 | 0.2917 | **0.2500** | 0.2500 |

Everything else is held byte-identical: universe, gate, 2% momentum floor, re-entry cooldown,
`relative_rank` admission ordering, `atr_stop_multiplier 3.0`, holding cap disabled,
`var_horizon_days 30`, `var_confidence 0.95`, `var_lookback_days 252`, `min_position_gbp 50`,
`absolute_max_position_gbp 1,000,000`, `max_total_deployed 0.70`, every exit reason and its priority,
the realistic intraday-touch `STOP_LOSS` fill, daily admission cadence.

### 3.1 Why the `per_trade_pct` ceiling must be re-derived

Left at 0.05, a 3-slot arm could deploy at most 15% of the account and would be a cash study, not a
concentration study. The slot's fair share of the deployment budget is `max_total_deployed / N` by
definition of the budget. At N=14 this is exactly 0.05 — the live value — which is why the
parameterisation is config-derived rather than chosen.

### 3.2 Why `max_risk_pct` must ALSO be re-derived — the decisive argument

VaR-implied size is `(balance × max_risk_pct) / |q05 × √30|` and **does not depend on N at all**.
Raising only the ceiling therefore does nothing for any candidate whose VaR-implied size already sits
below it: those positions stay exactly baseline-sized, and the arm deploys roughly `N/17` of
baseline's capital. At N=3 that is ~11% deployment. Raising the ceiling alone is not a concentration
test; it is a de-risking test wearing a concentration label.

Scaling the risk budget by `N_nom / N` holds **total portfolio risk budget constant**:
`N × max_risk_pct_N = 14 × 0.01 = 0.14` at every arm. That is precisely the sentence "the same
capital, at the same total risk, in fewer names" expressed as arithmetic.

**The property that makes this the right choice rather than merely a workable one: the
pinned-versus-VaR-bound partition of candidates is EXACTLY invariant to N.** A candidate sizes at its
arm's ceiling iff

```
max_risk_pct_N / (|q05| × sqrt(30))   >=   max_total_deployed / N
(0.14 / N) / (|q05| × sqrt(30))       >=   0.70 / N
                          |q05|       <=   0.14 / (0.70 × sqrt(30))   =   0.03652
```

— **N cancels.** The same candidates are ceiling-pinned in every arm; the same candidates are
VaR-bound in every arm; every position is the same multiple of its own arm's slot budget as it is of
baseline's. **The arms therefore differ in position COUNT and in nothing else about how a position is
sized.** No other parameterisation has this property, and it is what lets a difference be attributed
to concentration rather than to a changed sizing regime.

**A consequence to report, not hide.** CLAUDE.md records 255 of 308 live queue rows (~83%) sized at
the `per_trade_pct` ceiling, so baseline is already largely ceiling-pinned; the invariance above means
the concentrated arms are pinned at the same rate, not a higher one. **§7 requires the ceiling-pinned
share to be measured per arm and reported.** It will not be exactly invariant in practice — the
volatility composition of admitted names differs between arms — and any deviation is reported as a
measured second-order effect rather than assumed away.

### 3.3 Why the cash clamp must be made inert — with the exact arithmetic

`resolve_cash_constrained_position_gbp()` bounds a position at `0.25 × cash`. In this engine
`cash = (1 − d) × balance` exactly, where `d` is the mark-to-market deployed fraction. So the clamp
binds iff

```
0.25 × (1 − d)  <  per_trade_pct_N        <=>        d  >  1 − 4 × per_trade_pct_N
```

| arm | `per_trade_pct_N` | clamp binds when deployed exceeds | reachable? (cap is 0.70) |
|---|---|---|---|
| N=14 / baseline | 0.0500 | 80.0% | **No — provably unreachable** |
| N=8 | 0.0875 | 65.0% | yes, near full deployment |
| N=5 | 0.1400 | 44.0% | yes, most of the time |
| N=3 | 0.2333 | 6.7% | yes, essentially always |

**At baseline the clamp cannot alter any admitted position, and this is provable rather than
empirical.** If `d ≤ 0.80` the clamp value exceeds the ceiling and is inert; if `d > 0.80` the
capacity check rejects the candidate anyway (`would_deploy_pct > 0.70`). There is no reachable state
in which the clamp changes a baseline admission. §7 requires this to be **verified empirically as zero
clamp-binding admissions in the baseline run**, not merely argued.

**Left untouched, the clamp — not the slot count — would be the binding concentration limit in the
treated arms.** Its equilibrium is a closed form: with N equal positions of size `p` (fraction of
balance), one slot freeing gives `p = 0.25 × (1 − (N−1)p)`, so

```
p* = 1 / (N + 3)            deployed*   d* = N / (N + 3)
```

| arm | `d* = N/(N+3)` | vs the 0.70 cap |
|---|---|---|
| N=3 | **50.0%** | clamp binds — ~14pp below baseline |
| N=5 | **62.5%** | clamp binds — ~2pp below baseline |
| N=8 | 72.7% | cap binds first; clamp irrelevant |
| N=14 | 82.4% | cap binds first; clamp irrelevant |

The 3-slot arm would land ~14pp under baseline's deployment, trip §2.2's hard exclusion, and be
reported NOT TESTED — **the position-count failure reproduced exactly, via a different clamp.**
Predicting this before running is the entire reason this section exists.

**The relaxation, and its bound.** `cash_fraction_N = max(0.25, (0.70/N) / 0.30) = max(0.25, 2.333/N)`
is the smallest value that keeps the clamp inert for every reachable `d ≤ 0.70`, and it is exactly
0.25 at N=14 and at baseline. **This is not a second treatment variable**: it removes a constraint
that is provably inert in the control, so that it is inert everywhere, rather than letting it silently
become the real binding limit in the treated arms only.

**Cash can still never go negative, provably.** At the instant of any admission `d ≤ 0.70`, so
`cash ≥ 0.30 × balance`, and the position is at most `0.2333 × balance = 0.78 × cash`. The 70%
deployment cap already supplies the protection the 0.25 constant was providing. What is genuinely
given up is the clamp's *stated* purpose — a bound expressed in **realised** cash rather than
**mark-to-market** balance, as a hedge against mtm inflation. **Recorded as an accepted, named change
with its own reporting requirement**: every arm reports the number of admissions at which the relaxed
clamp was the binding constraint (expected: zero, by construction) and the minimum cash balance as a
fraction of the account across the walk. **The live clamp is untouched; any promotion of a
concentrated configuration would have to re-derive it, and that is recorded in §11 as a promotion
prerequisite rather than left implicit.**

### 3.4 Alternatives considered and rejected

- **Equal-weight slots (each position sized at exactly `max_total_deployed / N`, VaR removed).**
  Rejected: changes two things at once (count *and* the sizing rule), abandons the risk control that
  is the system's actual design, and would size the most volatile candidate identically to the
  calmest — the SNDK precedent in CLAUDE.md (£827.61 against a £2,564.43 ceiling, because
  `var_pct = −0.6197`) shows exactly what that protection is for. **The arms may nonetheless converge
  toward equal weight through ceiling-pinning** — §3.2's invariance says they pin at baseline's own
  rate, not a higher one, but the pinned share is measured per arm, and if a concentrated arm turns
  out to be near-fully pinned the report states that VaR discrimination effectively collapses at that
  concentration. That is an emergent, measured finding; imposing it would have been an assumption.
- **Slot cap with `per_trade_pct` and `max_risk_pct` both left at live values.** Rejected in §3.2:
  ~11% deployment at N=3; tests de-risking.
- **Slot cap with the ceiling raised but `max_risk_pct` unchanged.** Rejected in §3.2: identical
  problem, since VaR-implied size is N-independent.
- **Accept the cash clamp as-is and report the tight arms as NOT TESTED.** Rejected: §3.3 shows the
  outcome is derivable in advance, so running it would spend ten backtests confirming arithmetic.

---

## 4. Criteria

### 4.1 PRIMARY — cost-adjusted excess return over `^SP500TR`, versus baseline's own excess

Computed identically for every arm via
`research/measurement_toolkit_2026-08-08/step4_benchmark_relative.py` (`^SP500TR` coverage confirmed
2006-01-03 → 2026-08-07), continuous-walk convention throughout — one already-decided system's
realistic behaviour at each N, no train/test boundary, so reset-at-boundary is not applicable.
`^SP500TR`'s own return is identical across arms, so **Δ excess = Δ cost-adjusted CAGR**, and the
measured ~1.08pp noise floor applies directly.

**Required margin: ≥ +1.5pp** over baseline's own excess, on Pool 2 CLEAN. ~1.4× the largest
noise-floor draw this project has measured, and the same bar every recent study has used.

Frictionless CAGR, cost-adjusted CAGR, and the cost components are reported **separately** — see §4.6
for why the components matter here specifically.

### 4.2 DRAWDOWN — one-sided against worsening, expected to bind

**Metric**: peak-relative maximum drawdown (`compute_peak_relative_drawdown_pct`), the measure
baseline's −43.88% is quoted in.

**Clause A (relative)**: an arm's peak-relative max drawdown must not exceed baseline's by more than
**15 percentage points**. An arm equal to or better than baseline passes regardless of return. 15pp is
the position-count study's own calibration, retained for direct comparability on the same lever: Part
7 measured baseline sitting ~13–17pp worse on drawdown than the median of 100 random-admission draws,
for a *far smaller* change in concentration than 17 → 3.

**Clause B (absolute)**: an arm whose peak-relative max drawdown is worse than **−60%** is not
adoptable regardless of its relative position. Stated in advance because 15pp on top of −43.88% is
−58.88% — Clause A alone would tolerate a portfolio losing three fifths of its value peak-to-trough.
Marked explicitly as a judgement, not a derived figure.

**Expectation, recorded so it cannot later be re-read as a near-miss**: this is the clause most likely
to decide the study, and a breach at N=3 or N=5 is a legitimate, informative NOT ADOPTED.

### 4.3 OUTCOME CONCENTRATION — central here, not a footnote

Required for every arm:

- **Total positions opened** (also the §2.3 evaluability gate).
- **Top-3 trades by P&L as a share of that arm's own total P&L.** If it exceeds **50%**, the report
  states plainly that the arm's result is a small number of trades rather than a selection process —
  *a lottery ticket, not a strategy* — and any pass is downgraded to "not robust". Motivated directly
  by Part 1: the top decile already contributes **257%** of total P&L at ~3,400 trades; at ~100 trades
  the exposure is far worse.
- **Top-10 trades' share, and share of P&L by ticker**, for comparability with the exit-mechanics
  finding (73.6% of that cell's P&L in 10 of 238 tickers).
- **Full decile curve of trade P&L**, matching Part 1's own table shape.

### 4.4 SINGLE-TICKER DOMINANCE — ceiling 40%, absolute not signed

**Definition, exact.** For arm X against baseline B, aggregate realised P&L by ticker within each arm
and take `Δ_t = pnl_X(t) − pnl_B(t)` over the union of tickers. Then

```
dominance(X) = max_t |Δ_t|  /  sum_t |Δ_t|
```

Absolute on both sides, so a large winner and a large loser cannot cancel and report a concentrated
difference as diverse. Grouped by **ticker**, so repeat entries of one name count once — matching the
concentration metric CLAUDE.md already specifies for the forward shadow comparison. The same statistic
is also reported at **position** level as context; the ticker-level figure is the gating one.

**Ceiling 0.40.** An arm above it has its difference from baseline explained mainly by one name and
cannot clear the primary criterion. Calibrated against the relative-rank precedent (AMAT carried ~79%
of that finding's magnitude, in a 607-ticker pool with thousands of trades) and deliberately stricter,
because ~100 positions cannot dilute one name the way thousands can.

**Degenerate case, per §7**: with fewer than three contributing tickers some ticker necessarily exceeds
40% arithmetically. That is the correct answer, not an artefact — a two-name difference cannot
distinguish a slot count from one lucky position — so the guard doubles as a diversity floor. An
unmeasurable case (no differing tickers, or all `Δ_t` exactly zero) **blocks** the arm rather than
passing it.

### 4.5 OVERLAP — reported alongside every headline gap, and read as structural

For each arm vs baseline: exact `(ticker, entry_date)` Jaccard, ticker-only Jaccard, and both again
restricted to each arm's **top decile by P&L**.

**Predicted in advance: overlap will be very low, and low overlap is NOT evidence of a large effect.**
It is forced by construction — ~100 positions cannot overlap much with 565 — and this project has
measured capacity-cascade amplification producing 21.8–23.1% overlap from a change touching only the
admission sort key. **A randomisation null is therefore pre-registered, following the exit-mechanics
method verbatim**: condition on the concentrated arm's own admission dates, draw the same number of
admissions from each date's real gate-passing eligible set, 1,000 draws, and report observed shared
keys against that null's distribution. Only overlap *below* the null is informative; overlap far above
it (exit-mechanics: 326 observed vs 81.8 expected) confirms the arms select the same names and the low
raw number is a count artefact.

### 4.6 COSTS — modelled per arm, and the reason is specific to this study

Per CLAUDE.md's standing rule, a cost may be deferred only while it is constant across arms. **Here two
costs move in the same direction and both favour concentration**, so both are modelled:

- **FX drag**: `0.30% × Σ(position_gbp)` round-trip, annualised as a percentage of mean account
  balance. Falls sharply with trade count (exit-mechanics measured 3.331 → 0.268 pp/yr going from
  4,876 to 565 trades); a 3-slot arm with ~100 trades will show less again.
- **Idle-cash credit**: realised idle fraction × mean `^IRX` over the window, annualised the same way.
  **Rises** for any arm that under-deploys.

**The interaction is the hazard and is called out in advance: an under-deploying arm is paid twice in
cost-adjusted CAGR — once by trading less, once by holding more interest-bearing cash — so
cost-adjusted CAGR is the metric most likely to flatter exactly the failure mode §2.2 exists to
catch.** Frictionless CAGR, the FX component, and the idle-cash component are therefore reported as
three separate lines for every arm, and §2.2's flag tier requires the idle-cash contribution to be
quantified before any flagged arm's return is discussed.

### 4.7 PAIRED PER-DECISION INSTRUMENT — constructibility stated in advance

**Statement, made before running: exact `(ticker, entry_date)` pairing is expected to fall below the
usable floor for N=3 and N=5, and may do so for N=8.** All arms share the same daily admission clock,
so pairing is *mechanically* constructible — but between ~100 and 565 positions the shared set will be
small. Exit-mechanics got 326 shared keys from 712 vs 4,876; scaling suggests tens, not hundreds, here.

**Pre-registered floor: 100 shared keys.** Below it, the shared-key comparison is reported as a bare
count and is **not interpreted**, in either direction — including if it happens to favour the arm.
Stated now because the exit-mechanics study reached a 91-decision subset that pointed the right way and
deliberately declined to promote it; the same discipline applies here in advance.

**Pre-registered fallbacks, used regardless of whether the shared set clears the floor:**

1. **`disagreement_population_comparison()`** from the measurement toolkit — compares the **outcome
   distributions** of each arm's unique admissions (win rate, mean and median P&L%), the right
   instrument when the populations are near-disjoint by construction.
2. **Rank-at-admission distribution** — each admitted position's within-day rank among that day's
   gate-passing eligible set, per arm. A tighter slot count should admit from strictly further up the
   same ranked list; if it does not, the mechanism is not doing what the design claims. **This is the
   primary corroborating instrument for this study**, because it is fully valid at any trade count: it
   measures the *decision* rather than the *outcome*.
3. **Counterfactual outcome of displaced admissions** — for every date on which the slot cap turned
   away an otherwise-viable candidate that baseline admitted, the baseline trade's realised outcome is
   already known. Comparing the outcome distribution of *displaced* baseline positions against
   *retained* ones answers "did the cap turn away better or worse positions than it kept" with zero
   cascade contamination, because both outcomes come from the same baseline run.

### 4.8 Block-pass count — corroborating only

Measured resolution floor is **3 blocks**. Any delta ≤3 is read as neither support nor evidence
against. Reported for completeness, never as a discriminator.

---

## 5. Pools

- **Pool 2 CLEAN** — `get_research_stock_candidates() ∩ status == 'CLEAN'`. **Decisive.**
- **Pool 1** — `get_live_stock_candidates(as_of=today)`. **Direction only**, never decisive (maximal
  survivorship bias by construction — every constituent survived to today, so the pool structurally
  cannot contain a terminal decline, which matters more at 3 slots than at 17).

Both **reconfirmed fresh by direct query at run time** — ticker counts, `price_data` max date, and the
CLEAN / QUARANTINED / NEEDS_REVIEW split — never inherited from a prior session's figure, and never
from an assumption that this checkout's `database/investment_research.db` is current. Per CLAUDE.md's
derived-state rule, the specific file this study will read from is verified in place.

A cross-pool disagreement in **sign** on the primary criterion is reported as an unreconciled
disagreement, per the dual-provenance standing requirement, not resolved toward whichever pool is more
convenient.

---

## 6. Engine

**`slot_engine.py`, a research-only additive copy of
`analysis/backtest.py:simulate_portfolio_compounding_mtm()`.** No shared module is modified. Four
additions, all parameterised so the live values are recoverable exactly:

1. **`slot_cap: int | None`** — a portfolio-wide concurrent-position limit, checked inside
   `_admit_group()` **before** sizing is computed. A count limit cannot be relieved by a smaller
   position, so once the count is full the ranked loop `break`s rather than `continue`s. Applies
   across both the ETF and stock group passes via one shared counter. `None` = uncapped.
2. **`cash_fraction`** — passed explicitly to the existing
   `resolve_cash_constrained_position_gbp(position_gbp, cash, cash_fraction=...)`. **That parameter
   already exists in `analysis/decision.py`; no change to that module is required or proposed.**
3. **`max_risk_pct` override** — supplied via a **copied config dict**, never a code edit, following
   the measurement toolkit's uncapped-arm precedent verbatim.
4. **Additive instrumentation only** — the daily panel (`n_open_positions`, `cash`, `mtm_value`,
   `current_balance`, `current_deployed_pct`, already emitted by the exit-mechanics engine); plus, per
   admission: raw VaR-implied size before any clamp, which clamp bound
   (`VAR` / `VAR_FLOOR` / `CEILING` / `CASH` / `NONE`), within-day rank among gate-passers, and the
   eligible-candidate count; plus per-date `REJECTED_SLOT_FULL` summary rows (date, count of
   still-ranked eligible candidates turned away, highest-ranked one) rather than one row per candidate.

**`per_trade_pct` is already a direct call argument** and needs nothing new.

---

## 7. Verification, before any arm result is trusted

Every check states its degenerate case and whether it can produce both answers — per the standing
"check that reports clean for a reason unrelated to the thing it was checking" class.

1. **Degenerate parity.** With `slot_cap=None` and live `per_trade_pct` / `max_risk_pct` /
   `cash_fraction`, `slot_engine.py` must reproduce raw, unmodified
   `simulate_portfolio_compounding_mtm()` **byte-identical** on both pools — compared on a canonical
   `(ticker, entry_date, exit_date)` sort, never insertion order (which the relative-rank promotion
   showed produces hundreds of phantom mismatches on identical trade sets), and with float comparison
   done in memory, never against a CSV round trip (which the trailing study measured altering 16.2% of
   float64 values). **This check exists here where the cross-sectional study had none available, and it
   is the strongest single guarantee that only the intended lines differ.**
2. **Cash-clamp inertness at baseline, measured not argued.** The baseline run must report **zero**
   admissions whose binding clamp was `CASH`. *Can it produce the other answer?* Yes — the
   instrumentation records the binding clamp for every admission independently of §3.3's proof, so a
   non-zero count would falsify that proof rather than being unreachable.
3. **The slot cap actually binds.** Each treated arm must report a non-zero `REJECTED_SLOT_FULL` count
   on a substantial fraction of trading days. *Degenerate case*: an arm whose cap never binds is
   testing nothing and is reported as such, not as a null result.
4. **Ceiling-pinned share per arm**, tested against §3.2's invariance prediction. Any large deviation
   is reported as a measured second-order effect, not suppressed.
5. **Decision-engine parity for the research script**, per the standing requirement — evidence from
   this script's own run, not inherited from "production is consolidated". 30 sampled trades per pool:
   exit reason recomputed by calling `evaluate_exit_reason()` directly; position size recomputed from
   the engine's **own emitted pre-entry balance** (never a reconstructed proxy — the error that
   produced the cross-sectional study's false 21/30); holding horizon via `resolve_var_horizon_days()`;
   entry gate via `passes_entry_gate()` and `passes_momentum_floor()`.
6. **Non-vacuity.** Every verification assertion must be shown to have operated on a non-empty
   population. The exit-mechanics study's first trailing unit-test run passed vacuously on two empty
   trade lists; a `_require()`-style guard asserting non-empty inputs is mandatory on every check
   above. **A check that has not been observed producing a non-empty result has not been observed.**

---

## 8. Sanity gate

**Baseline (uncapped) must reproduce Pool 2 CLEAN 565 trades / £348,548.74**, compared as **trade
SETS** — `(ticker, entry_date, exit_date, exit_reason, pnl_gbp)` — never by total or count alone. The
Pool 1 baseline is compared the same way against its own preserved `s3.0` set.

**A small, fully row-diagnosed discrepancy is expected and is not a failure**, per this project's
repeated experience that a reference figure decays as ingestion moves on: boundary-date price revisions
altering `END_OF_DATA` trades, or one ticker's admission outcome shifting with an extra day of data.
Any discrepancy is row-diffed and explained in the report **before** any arm result is discussed. An
unexplained discrepancy halts the study.

---

## 9. Robustness — earned, not assumed, with one exception

Per project norm, a 20-draw paired perturbation test is **earned by an arm that fires no walk-away
clause**, not run upfront. Two conditions on it, both fixed now:

- **The `shuffle_tiebreak` dimension is INERT BY CONSTRUCTION in this system** and must not be used or
  counted. Admission sorts on `(-rank_value, insertion_idx)` with a continuous float, so exact ties
  essentially never occur. The relative-rank robustness study's "20 draws" was corrected on 2026-08-12
  to ~11 informative draws for exactly this reason. Draws here are **single-ticker drop** and **walk
  start-date shift** only, and each dimension must be shown to move the result before its draws are
  counted.
- **The start-date dimension is REQUIRED IN ADVANCE, not earned, for any arm tripping §2.4's
  60%-pre-2010 threshold.** With 3 slots and no holding cap, "which three names happened to be admitted
  in early 2008" plausibly determines the entire 19-year result, and that is a start-date artefact
  rather than a strategy. Single-ticker drops must additionally be **targeted at the arm's own top P&L
  contributors**, not randomised, since with ~100 positions a random drop is usually a no-op — the same
  correction the exit-mechanics robustness test already had to make.

---

## 10. THE WALK-AWAY SENTENCE

> **I will not adopt any hard slot count if: any arm's realised mean concurrent position count is closer
> to the uncapped baseline's own realised mean than to its own nominal target (that arm is reported NOT
> TESTED, never as a null); or if any arm's mean deployment diverges from baseline's by more than 4pp
> (that arm is excluded from the return comparison entirely, not caveated alongside a return figure); or
> if any arm opens fewer than 50 positions across the full walk (NOT EVALUABLE on return); or if no arm
> clears +1.5pp of cost-adjusted excess return over baseline's own excess on Pool 2 CLEAN; or if an
> otherwise-clearing arm exceeds baseline's peak-relative max drawdown by more than 15pp, or posts a
> peak-relative max drawdown worse than −60% in absolute terms; or if an otherwise-clearing arm has more
> than 50% of its own total P&L in its own top 3 trades, or more than 40% of its absolute per-ticker P&L
> difference from baseline in a single name, or draws more than 60% of its P&L from positions entered
> before 2010 without the start-date perturbation of §9 having already been run and held; or if the
> rank-at-admission instrument shows a clearing arm is not in fact admitting from further up the same
> ranked list than baseline.** A tighter slot count that wins by holding more cash, by being paid twice
> in idle-cash credit for under-deploying, by two or three positions bought in 2008 and never sold, by a
> single dominant name, or by a cascade-driven reshuffle rather than by genuinely better concentration,
> has not shown that position count is an under-exploited lever — only that Part 7's already-known
> trade-off (more return, more drawdown) looks however it happens to look at whichever N was tested.

**Note on asymmetry, stated explicitly**: this sentence is a **veto, not an adoption trigger**. An arm
firing no clause has earned §9's robustness test, not adoption — the same standard the exit-mechanics
study held itself to, and the same one the momentum-smoothness study invoked when an arm cleared the bar
by 0.051pp and was still not adopted.

---

## 11. What a positive result would and would not license

**Nothing here is an adoption path.** A clearing, robustness-surviving arm would be a research finding
requiring, before any production change: promotion of the slot-cap mechanism into a real production
module with the shared-function treatment (`analysis/decision.py`); genuine coverage in all four
live/backtest parity layers from the start rather than retrofitted; **re-derivation of
`resolve_cash_constrained_position_gbp()`'s 0.25 constant for a slot-capped book** (§3.3 — this study
relaxes it in research only, and the live clamp is untouched); and a decision about how a hard slot count
interacts with the live re-entry, membership and reconciliation paths that no backtest exercises.
`trading212_invest.live` remains `false` regardless, so nothing here carries real-money risk.

**Multiple comparisons, recorded rather than argued away**: this is roughly the 60th experiment in this
project's research record, of which one has been adopted. A clearing result should be read against that
base rate. What partially answers it — and only partially — is that this is a **structural design change
with a prior rationale** (Part 7's ranking-vs-random result, plus the position-count study's explicit
recommendation), not a parameter swept until something cleared.

---

## 12. Constraints re-confirmed

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path. No database
writes of any kind; `^SP500TR` and `^IRX` are fetched read-only into a research-local cache if not already
present. The study runs in its **own dedicated git worktree**, per CLAUDE.md's unconditional rule, never
the shared main checkout. Nothing adopted, nothing merged, nothing deployed.

**Backtest convention**: continuous walk, 2007-01-03 → 2026-08-07, every arm from the identical £50,000
start. No train/test boundary exists, so reset-at-boundary is not applicable. **Deployment-cap
accounting**: mark-to-market throughout (`simulate_portfolio_compounding_mtm()` semantics), unchanged
from baseline.
