# Long/short momentum — pre-registration (2026-08-11)

**PRE-REGISTRATION ONLY. Nothing has been run, no engine has been built, no cache has been
generated.** Written from a dedicated worktree (`c:\GitHub\investment-app-longshort`, branch
`research/long-short-2026-08-11`) per this project's absolute worktree rule — two other worktrees
(`-ftse`, `-ibauth`) are already active today, so this is not hypothetical isolation.

---

## 0. What this is, and what makes it different from the last ~60 experiments

Every prior study in this project has tuned one construction: long-only, 12-month trailing momentum,
capacity-constrained daily admission. Across roughly 60 experiments that programme has produced
exactly one adopted improvement (relative-rank admission ordering, Arm B, live since 2026-08-11),
and the system still trails buy-and-hold `^SP500TR` after costs (selection audit: 7.05% vs 11.13%
CAGR over 2007–2026).

**Adding a short side is not a refinement of that construction — it is a different return profile.**
A long-only book's return is dominated by market direction; the only lever left is which names it
picks, and this project has now measured that lever's ceiling fairly thoroughly. A long/short book
can, in principle, earn from *dispersion* rather than direction. Whether it actually does, on this
data, with honest costs, is untested here and is what this study asks.

**Execution venue is deliberately out of scope**, per the task. T212 Invest does not support
shorting, so a positive result here is **informational, not actionable** — it would make "which
venue" a real question, and that question is a separate piece of work with its own feasibility
constraints (the shelved sleeve-allocation study is the cautionary precedent: a design can be sound
and still be untestable/untradeable on the instruments actually available). **Nothing in this study
can be adopted regardless of outcome.**

---

## 1. Honest priors, stated before any result exists

The task named four; I am adding four more that fall out of this codebase's own specifics. All eight
are stated now so a favourable result has to survive them rather than be read against a blank slate.

**Stated in the task:**
1. **Short-side momentum has a weaker published record than long-side.** Momentum's premium is
   asymmetric; a substantial share of the academic long/short spread comes from the long leg, and
   the short leg's contribution is smaller, more concentrated, and more fragile.
2. **Momentum crashes are severe and concentrated.** Daniel–Moskowitz: the short leg's losses arrive
   in violent, short, panic-state rebounds, not as steady bleed. April 2009 is the canonical case.
3. **Borrow costs are modelled, not measured.** No historical borrow-cost data exists in this
   database or anywhere this project can reach. §5 is an assumption throughout, labelled as such.
4. **The strongest short candidates are disproportionately the hardest to borrow**, so the cost model
   is likely optimistic exactly where it matters most — a deeply distressed name is both the best
   short signal and the most expensive/impossible borrow. §5's stress case partially addresses this;
   it does not eliminate it.

**Added here:**
5. **Pool 1 structurally cannot contain a short winner of the kind that matters most.** Pool 1
   (`get_live_stock_candidates()`) is today's real S&P 500 membership projected backwards — every
   ticker in it survived to today *by construction*, so it cannot contain a bankruptcy, a
   delisting-to-zero, or a terminal decline. Pool 2 CLEAN can. This project already documented this
   asymmetry as a structural property rather than a defect (stop-loss reconfiguration study,
   corrected 2026-08-08) — but every prior study met it on the *long* side, where it is a second-order
   effect. **On the short side it is first-order**: the short sleeve's best outcomes are precisely the
   population Pool 1 excludes. **Prediction: the two pools will disagree on the short sleeve more
   sharply than on anything this project has measured before, and a Pool 2 CLEAN short result that
   Pool 1 contradicts is the expected shape, not a red flag to be reconciled away.**
6. **Arm B's long sleeve will not reproduce baseline's trades**, even though its long-side rules are
   identical. Short P&L flows into account equity, equity drives `per_trade_pct` sizing, and sizing
   drives the capacity-constrained admission walk — the same capacity-cascade amplification that made
   a pure sort-key change produce only 21.8–23.1% exact-date overlap. Expect *lower* here, and do not
   read a low overlap as evidence the long sleeve was altered.
7. **Arm C will likely be structurally underdeployed in bull markets.** A market-neutral book needs
   short candidates to exist; in a strong bull market, few S&P 500 names carry momentum ≤ −2%. The
   net-exposure band will then throttle the long side to maintain neutrality, parking capital in
   cash. This is a genuine property of the construction, not a defect — but it means Arm C can post
   near-zero correlation by *holding cash*, which is not market neutrality. §9 and §14 build the
   instruments to tell those two apart.
8. **Multiple comparisons.** This is roughly the 61st experiment. A clearing result at a ~1.5pp bar
   is expected on chance alone at that count. Two things partially answer it: this is a *construction
   change with a prior rationale* rather than a swept parameter, and §12 makes a paired perturbation
   robustness test mandatory before any clearing arm is described as more than provisional. Neither
   fully answers it.

---

## 2. The arms

Three arms, one shared short signal, two allocation models. **The short signal is identical between
B and C by design, so allocation is the only variable between them.**

| | Arm A — Baseline | Arm B — Long-biased | Arm C — Market-neutral |
|---|---|---|---|
| Long entry gate | Production, unchanged | *Identical to A* | *Identical to A* |
| Long admission sort | `relative_rank` desc (current live) | *Identical to A* | *Identical to A* |
| Long exits | Production, unchanged | *Identical to A* | *Identical to A* |
| Crisis breaker (longs) | Production, unchanged | *Identical to A* | *Identical to A* |
| Short sleeve | **None** | §3 signal | §3 signal, identical |
| Long exposure cap | 0.70 | 0.70 | 0.50 |
| Short exposure cap | — | 0.30 | 0.50 |
| Net exposure band | — | none (emergent ≈ +0.40 to +0.70) | **\|net\| ≤ 0.10, enforced at admission** |
| Rally breaker (shorts) | — | §6 | §6, identical |

**Arm A is not a new construction.** It is `analysis.backtest.simulate_portfolio_compounding_mtm()`
called unmodified against the current config (`admission_ranking_mode: "relative_rank"`,
`per_trade_pct: 0.05`, `max_total_deployed: 0.70`) — i.e. the system as it actually trades today.

**Held fixed across all three arms:** `per_trade_pct=0.05`, `atr_stop_multiplier=2.0`,
`stock_max_holding_days=30`, `min_momentum_pct=0.02`, `reentry_cooldown_days=0`, `var_confidence=0.95`,
`max_risk_pct=0.01`, `min_position_gbp=50`, daily admission cadence, the same universe, the same cost
template (§8).

---

## 3. The short signal — the symmetric mirror of the existing gate

**One signal, specified once, used identically by B and C.**

A ticker is a **short candidate** on date *t* iff all of:

```
(a)  momentum(t)  <  0                                  # mirror of the long signal's momentum > 0
(b)  momentum(t)  <= -required_short_floor(t, ticker)   # mirror of the +2% floor, borrow-adjusted (§5)
(c)  is_member(ticker, t) is True                       # identical to the long gate
(d)  crisis_entry_blocked(t) is False                   # identical to the long gate — see below
(e)  rally_entry_blocked(t) is False                    # NEW, short-only (§6)
```

where `momentum` is the same trailing-12-month value `analysis/signals.py:compute_momentum_series()`
produces and the long gate already uses — **not** a new signal, and **not** the relative-rank value.

**Ranking: cross-sectional relative rank, ASCENDING** — `analysis/signals.py:compute_relative_rank_series()`,
the same production function Arm B's long admission already sorts by, sorted the other way so the
weakest relative performers are offered scarce short capacity first. This is the exact mirror of the
adopted live ordering, which is the point: B and C inherit the one mechanism this project has
actually validated, rather than introducing a second untested ranking choice alongside the short side.

**Short `SIGNAL_OFF` fires when `momentum(t) >= 0`** — the exact mirror of the long side's
`momentum <= 0`. Note the deliberate asymmetry between entry and exit, which is inherited rather than
invented: entry requires clearing a floor (−2% or worse), exit requires only crossing zero. The long
side already works this way.

**Why the crisis entry-block (d) also blocks NEW shorts — a decision, not an oversight.** The
existing crisis breaker fires on a ≥7% single-day `^GSPC` decline and blocks new long entries for the
trigger day plus 5 trading days. It would be defensible to argue shorts should be *unaffected* (a
crash is when shorts work). I am blocking them anyway, and the reason is specific: the 5-day
post-crash window is the single highest-probability window for the momentum-crash rebound this
strategy's short sleeve is most exposed to. Opening a *new* short into it means entering after the
move and directly into the rebound risk. **Existing shorts are not touched by the crisis breaker** —
only new entries are blocked. The alternative (crisis irrelevant to shorts) is named here so that if
the short sleeve's results are marginal, this is a known, pre-identified knob and not a post-hoc
rationalisation.

---

## 4. What the short side CANNOT reuse from production — and the verification that makes the mirror trustworthy

**This is the single largest correctness risk in the study, and it is a genuine architectural
constraint, not a shortcut.**

`analysis/decision.py:evaluate_exit_reason()` hardcodes the long-side stop comparison:

```python
if stop_price is not None and price is not None and price <= stop_price:
    return "STOP_LOSS"
```

A short position's stop is **above** entry and triggers on `price >= stop_price`. The function
therefore **cannot** be called for shorts. §16 forbids modifying `analysis/decision.py`, and that
constraint is correct — this is research, and a live-path decision function must not grow a
research-only branch. So the short side needs its own evaluator, which lands this study directly
against this project's "there is exactly one implementation of any given trading decision"
invariant.

**How that is resolved rather than waved past:** the short evaluator is written as an *exact
reflection* of the production long evaluator, and that claim is **verified mechanically, not by
inspection** — inspection is precisely what failed in the deployment-cap parity incident, where two
call sites were reviewed as "functionally identical" because they shared an operator and threshold
while their inputs meant different things.

**The reflection test.** For a short at entry price `E` with stop `S = E + d`, reflect the price
about the entry: `P' = 2E − P`, and reflect the stop: `S' = E − d`. Then

```
P >= S   <=>   2E - P <= 2E - S   <=>   P' <= S'
```

so the short stop condition is *identically* the long stop condition on the reflected series. The
required property, checked over a randomized sweep of ≥100,000 input tuples plus explicit boundary
cases (`P` exactly at the stop, every `None` combination, every exit-priority ordering):

```
evaluate_short_exit_reason(crisis_now=c, price=P,      stop_price=E+d, sig_on=s, holding_days=h, max_holding_days=m)
                        == evaluate_exit_reason(crisis_now=c, price=2E-P, stop_price=E-d, sig_on=s, holding_days=h, max_holding_days=m)
```

**for every input, with no exceptions.** This proves the short evaluator reproduces the production
function's *entire* behaviour — priority order (`CRISIS_EXIT` → `STOP_LOSS` → `SIGNAL_OFF` →
`MAX_HOLDING`), `None`-tolerance semantics, and boundary handling — rather than being a hand-written
lookalike. A single mismatch anywhere in the sweep blocks the study until resolved.

**Two more long-only mechanics that need explicit mirrors:**

- **Realistic intraday stop fill.** Production fires `STOP_LOSS` off the day's `low` and fills at
  `min(stop_price, open)`. The short mirror fires off the day's **`high`** and fills at
  **`max(stop_price, open)`**.
- **`precompute_ticker()` does not return `high`.** Confirmed by direct read: the frame is
  `{close, atr, signal, momentum, relative_rank, open, low}` — `high` is consumed to compute ATR and
  then discarded. **The research cache builder must add it.** This is not cosmetic: without `high`,
  short stops could only fill at the close, which is exactly the optimistic assumption this project
  measured and corrected for longs in 2026-08-06 (that correction cut total P&L 73.75%). Filling
  short stops at the close would bias the short sleeve favourably by an unknown but demonstrably
  large amount. **If `high` cannot be plumbed through for any reason, the study does not run** — it
  does not run with close-fill shorts and a caveat.

Everything else routes through the existing shared functions unmodified, for both sleeves:
`_resolve_entry_stop()` (which **already** accepts `"SHORT"` and returns `price + stop_distance` —
verified by direct read, not assumed), `_var_position_size_core()`, `resolve_dynamic_max_position_gbp()`,
`is_position_size_viable()`, `is_reentry_blocked()`, `get_max_holding_days()`, and — for the long
sleeve in every arm — `evaluate_exit_reason()` and `passes_entry_gate()` themselves.

---

## 5. Borrow cost — a MODELLED ASSUMPTION, labelled as such throughout

**No historical borrow-cost data exists.** Every number in this section is an assumption. Every
report, table, and headline figure produced by this study must carry the label **"modelled borrow"**
next to any short-inclusive result. This is not a formality: it is the single weakest input in the
design, and §1's prior 4 says the model is likely optimistic exactly where it matters most.

### 5.1 The rate model

Two tiers, accrued daily on the short position's **current mark-to-market notional** (which is what a
real borrow fee accrues on — the market value of the borrowed stock, marked daily — not the entry
notional):

| Tier | Modelled rate | Stress rate (2×) |
|---|---|---|
| General collateral (GC) — every short, every day | **0.50%/yr** | 1.00%/yr |
| Hard-to-borrow (HTB) surcharge — replaces GC when flagged | **10.50%/yr** | 21.00%/yr |

```
borrow_cost_gbp(t) = short_notional_mtm(t) * rate(t, ticker) * (calendar_days_since_prev_bar / 365)
```

Calendar-day accrual, so weekends and holidays accrue — real borrow fees do.

**Why 0.50% GC and not eToro's 0%.** The task cites eToro's published schedule (easy-to-borrow at
zero, hard-to-borrow above 10%, under 2% of symbols). The >10% HTB figure is adopted directly.
**The 0% easy-to-borrow figure is deliberately NOT adopted**, and this is the most important
judgement in the section: a retail broker quoting 0% is not receiving stock for free — the cost is
recovered in spread, financing, and order flow. Modelling it as literally zero would import a
marketing structure into a cost model as though it were an economic fact. Institutional GC borrow on
liquid US large caps typically runs ~0.25–0.60%/yr. **0.50%/yr is a mid-range honest GC figure**, and
using it rather than 0% costs the short sleeve real return, which is the correct direction for an
assumption this uncertain.

### 5.2 The HTB flag — computable from OHLCV only, no look-ahead

No borrow data exists, so "hard to borrow" must be proxied from distress characteristics observable
in this database. All three inputs are trailing and available at decision time:

A ticker is **HTB on date *t*** iff **ANY** of:

```
(a)  close(t) < $5.00
(b)  close(t) / max(close over trailing 252 trading days, inclusive) - 1  <=  -0.70
(c)  annualised realised vol over trailing 60 trading days  >=  0.80
         where vol = std(daily log returns, 60d) * sqrt(252)
```

Justification: (a) the conventional sub-$5 threshold, which is also where index-deletion and
delisting risk concentrate and where borrow markets genuinely tighten; (b) a ≥70% drawdown from the
trailing annual high is a real distress marker, not a routine correction; (c) 80% annualised vol is
roughly 4× the S&P's long-run level and is the regime where lenders recall and re-rate.

**These thresholds are proposed, not final — see §14.1 for the mandatory pre-run calibration that
freezes them.**

### 5.3 Borrow cost in the ENTRY DECISION, not just as a post-hoc drag

Per the task: a short candidate must clear a *larger* expected edge to justify an expensive borrow.
Computed at decision time with no look-ahead, by making the short entry floor **dynamic**:

```
required_short_floor(t, ticker) = min_momentum_pct + rate(t, ticker) * H / 365
                                   where H = stock_max_holding_days = 30
```

`H` is the known holding-period cap, not an estimate of realised holding period — it is available at
decision time and requires no forecast. Resulting floors:

| Tier | Modelled | Stress (2×) |
|---|---|---|
| GC | 2.0411% | 2.0822% |
| HTB | 2.8630% | 3.7260% |

This is condition (b) of §3's short gate, and it is the mechanism the task asked for: an expensive
borrow demands a demonstrably stronger signal.

**Its bite is small at the GC rate, and that is stated plainly rather than engineered around.** At
0.50%/yr the floor moves 4 basis points — effectively inert. The mechanism's real effect is confined
to the HTB tier, where it moves the floor by 86bp (modelled) or 173bp (stress). **That is the honest
consequence of an honest base rate, not a reason to inflate the rate to make the mechanism look
consequential.** §14.1's calibration will report what fraction of short-eligible candidate-days sit
in the HTB tier at all, which determines whether this mechanism is doing anything measurable.

**Deliberately NOT done: borrow cost is not folded into the ranking sort key.** Ranking stays pure
relative rank (§3), mirroring the adopted live mechanism. Adjusting the sort key by expected borrow
cost is a defensible alternative and might well be better, but it would introduce a second
borrow-cost mechanism operating simultaneously with the floor, making neither attributable. Named
here as a follow-up, not smuggled in.

### 5.4 Reporting

Every short-inclusive result is reported at **both** the modelled rate and the 2× stress rate. If a
headline conclusion holds at modelled and reverses at stress, **the stress result governs the verdict**
and the arm is reported as borrow-cost-fragile.

---

## 6. The rally breaker — where this arm lives or dies

### 6.1 Why a symmetric breaker would be wrong

The existing crisis breaker fires on a ≥7% single-day `^GSPC` **decline** and force-exits longs. A
naive mirror (fire on a ≥7% single-day **rise**, exit shorts) is wrong twice over: a crash is when
the short book performs *best*, so a decline-triggered short exit crystallises the wrong side; and
big up-days cluster inside bear markets, so a rise-triggered breaker fires repeatedly in exactly the
regime the shorts are earning.

The real mirror risk for a short book is a **momentum crash** — a violent rally off a bottom, when
the most-beaten-down names rebound hardest. And a naive "exit shorts on any strong up day" fires
constantly in ordinary bull markets, where strong up days are common and carry no rebound risk at
all. **The breaker must therefore be conditional on the market being in a drawdown state.**

### 6.2 The formulation

Using the same `^GSPC` close series the crisis breaker already uses — reused, not a second reference
series:

```
peak252(t) = max(gspc_close) over the trailing 252 trading days ending at t (inclusive)
dd(t)      = gspc_close(t) / peak252(t) - 1
r5(t)      = gspc_close(t) / gspc_close(t-5) - 1

RALLY_RAW(t) = TRUE  iff   dd(t-1) <= -0.20   AND   r5(t) >= +0.10
```

**On `RALLY_RAW(t)`:**
- every open **short** is force-exited at that day's close, `exit_reason = "RALLY_EXIT"`;
- **longs are entirely unaffected** — the existing crisis breaker continues to govern longs,
  unchanged, in all three arms.

**Entry block:** `RALLY_BLOCK(t)` is TRUE on the trigger day and for **10 trading days** after the
most recent trigger — the same mechanism as `crisis.extend_trigger()`, with a different cooldown.
While TRUE, new **short** entries are blocked. Long entries are unaffected.

### 6.3 Justification of each threshold

- **`dd ≤ −20%`** — the conventional bear-market definition. This is the condition that prevents the
  named failure mode: in an ordinary bull market the index is rarely 20% below its trailing annual
  high, so a +10% five-day rally in a bull market does **not** fire the breaker. The gate exists to
  restrict firing to genuine drawdown states.
- **`r5 ≥ +10%`** — a five-day window rather than a single day, deliberately. Single-day triggers fire
  on the isolated bear-market up-days that punctuate a continuing decline (2008-10-13 at +11.6%,
  2008-10-28 at +10.8% — both mid-decline, neither a turn), producing repeated whipsaw exits and
  re-entries. A five-day cumulative threshold requires the rebound to *sustain*, which is closer to
  the momentum-crash signature and much less prone to firing on a one-day bounce.
- **10-day cooldown vs. the crisis breaker's 5** — momentum-crash rebounds run for weeks to months
  (March–June 2009), not days. A 5-day cooldown would re-admit shorts into the middle of the rebound.
  10 is a judgement, and it is one of the sensitivity axes in §6.5.

### 6.4 The degenerate case, named in advance

Per this project's standing class entry on checks that report clean for reasons unrelated to what
they were checking: **`dd` is measured at `t−1`, not `t`, and that is load-bearing.** A +10% five-day
rally can itself lift the index *out* of a −20% drawdown by day `t` — so measuring `dd(t)` would let
the very rally the breaker exists to catch mechanically suppress its own trigger, silently, with the
breaker reporting "no fire" for a reason entirely unrelated to whether a momentum crash occurred.
Measured at `t−1`, the drawdown state is the one that existed going *into* the rally.

**The sharper question, asked and answered in advance: can this check produce the other answer, and
what would make it?** Yes — §14.2 requires the breaker's firing dates to be enumerated on `^GSPC`
alone before any arm runs. Three outcomes and their pre-committed readings:
- **Fires 0 times** → the breaker is inert, the entire rally-breaker design element is **untested**,
  and the short sleeve's results must **not** be described as having survived momentum-crash risk.
  Silence is not safety.
- **Fires >25 times over 2007–2026** → it is not a rare breaker; it is a trading rule. Re-specify
  before running any arm.
- **Fires but not within the 2009 momentum-crash window** → the breaker failed at the one case it was
  explicitly designed for. That is a design failure to report prominently, not a detail.

### 6.5 Pre-registered sensitivity grid — robustness, not a threshold search

Five configurations, fixed now. **The PRIMARY decides the verdict; the other four are reported as
robustness.**

| Config | `dd` gate | `r5` trigger | cooldown |
|---|---|---|---|
| **PRIMARY** | **−0.20** | **+0.10** | **10** |
| loose | −0.15 | +0.08 | 10 |
| tight | −0.25 | +0.12 | 10 |
| cd-short | −0.20 | +0.10 | 5 |
| cd-long | −0.20 | +0.10 | 20 |

**Rule fixed in advance: if an arm clears its bar at PRIMARY but fails at ≥2 of the other four, the
result is reported as threshold-fragile and is NOT a clear result.** This is not an invitation to
pick the best cell — the PRIMARY is named before any run and cannot be swapped afterwards.

---

## 7. Allocation, exposure, and cash — the accounting stated explicitly

### 7.1 Short position accounting

```
entry:    notional N at price E;  cash += N;  liability L = N
each day: L(t) = N * price(t) / E
equity(t) = cash(t) + long_mtm(t) - sum(L(t))
unrealised short P&L = N - L(t) = N * (E - price(t)) / E     # profit when price falls
realised  short pnl_pct = (entry_price - exit_price) / entry_price
```

Opening a short leaves equity unchanged at entry (cash `+N`, liability `+N`), which is correct.

### 7.2 Margin — shorts are not free

**Reg-T initial margin: a short of notional `N` requires `0.50 * N` of free cash set aside as
collateral**, released on cover. A short is admitted only if free (uncommitted) cash ≥ `0.50 * N`.
Without this, shorting is costless in the model and the short sleeve becomes unrealistically
unconstrained — the sleeve would size itself purely on VaR with no capital constraint at all.

### 7.3 Exposure definitions — reported per arm, per the task

```
long_pct(t)  = long_mtm(t) / equity(t)
short_pct(t) = sum(L(t))   / equity(t)
gross_pct(t) = long_pct + short_pct
net_pct(t)   = long_pct - short_pct
```

Caps by arm: **B** — long ≤ 0.70, short ≤ 0.30 (gross ≤ 1.00, net emergent ≈ +0.40 to +0.70).
**C** — long ≤ 0.50, short ≤ 0.50, **and `|net_pct| ≤ 0.10` enforced at admission** (gross ≤ 1.00).
Gross ≤ 1.00 in both arms keeps the book inside Reg-T feasibility rather than assuming leverage a
retail account would not get.

### 7.4 Admission order within a day — a real choice, stated with its alternative

Both sleeves run the existing momentum-ranked, skip-and-continue capacity walk. Exits (Phase A,
both sleeves) always complete before entries (Phase B, both sleeves) on the same day, so freed
capacity is redeployable same-day — the structural property that kept every prior daily-clock study's
deployment parity intact.

- **Arm B: longs admitted first, then shorts.** Preserves "longs exactly as now" as far as the
  cascade allows, and the long cap (0.70) is the same binding constraint baseline has.
- **Arm C: shorts admitted first, then longs.** Deliberate and consequential. Short candidates are
  predicted scarce (prior 7); if longs went first they would hit the `net ≤ +0.10` ceiling almost
  immediately in any bull market and the short sleeve would rarely be reached at all, making the arm
  a throttled long book rather than a neutral one. Admitting shorts first lets the available short
  capacity set the day's neutral budget, which longs then match. The alternative is named so it is
  visibly a decision, not a default.

### 7.5 Idle cash

Idle-cash credit (§8) accrues on **free cash net of short margin collateral** — genuinely
uncommitted cash only. **Short sale proceeds earn no rebate** in this model. Retail short rebates are
typically zero or negative; crediting them would be the same category of error as adopting a 0% GC
borrow rate. Conservative by design, stated so.

---

## 8. Costs

The established template, applied **per arm** because turnover will differ materially across arms —
per the standing rule that a cost is safe to defer only while it is constant across the arms being
compared, which it explicitly is not here:

- **FX**: 0.30% round-trip (T212's confirmed 0.15%/leg), applied to each arm's **real** per-trade
  `position_gbp` summed across all trades, both sleeves. Shorts pay it on both legs like longs.
- **Idle cash**: mean free-cash fraction (§7.5) × mean `^IRX` over the window.
- **Borrow**: §5, at modelled and 2× stress. Arm A pays none (no shorts) — which is itself a real
  cost asymmetry favouring A, and is the correct treatment, not a handicap.

All three netted into a **cost-adjusted CAGR** per arm, the same construction every recent study in
this project has used.

---

## 9. Criteria

### 9.1 PRIMARY — excess return over `^SP500TR`

Cumulative and per-block excess return over `^SP500TR` (total return, dividends reinvested — coverage
confirmed 2007-01-03 → 2026-08-07), continuous-walk convention, computed identically to the last four
studies. Reported as each arm's own excess return and as **that arm's excess minus baseline's own**.

**Required margin: ≥ +1.5pp cumulative cost-adjusted excess-return improvement over Arm A**, against
the measured ~1.08pp noise floor (1.5pp ≈ 1.4× the largest single draw observed). Unchanged from the
standing bar across the last four studies rather than re-derived per study.

**A caveat that must travel with this bar: the ~1.08pp floor was measured on long-only perturbations**
(single-ticker drops, start-date shifts, tie-break reshuffles, all within a long-only capacity
cascade). **Its applicability to a long/short construction is unverified.** A long/short book has an
additional variance source — short-sleeve concentration in a small, volatile candidate population —
that the floor measurement never sampled. Consequence, fixed now: **any arm clearing +1.5pp is
"provisional pending robustness" and nothing more until §12's 20-draw paired perturbation test is
run.** This is exactly what Arm B required and got.

### 9.2 Correlation to `^SP500TR` — and how Arm C is weighed, stated in advance

**Reported for every arm**: Pearson correlation of **monthly** arm returns to `^SP500TR` monthly
returns (deciding measure — the standard for a strategy-vs-benchmark correlation claim, ~235 points
over the window), with **daily** correlation reported alongside.

**For Arm C this is arguably the point, and the task asks how that would be weighed. Committing now:**

Arm C is recorded as a **QUALIFIED POSITIVE** if **all four** hold:

```
(i)   cost-adjusted CAGR  >=  +3.0%/yr
(ii)  |monthly correlation to ^SP500TR|  <=  0.30
(iii) peak-relative max drawdown  <=  25%
(iv)  the 2009 momentum-crash window (§9.5) does not reverse the sign of the cumulative result
```

Justification of each: (i) mean `^IRX` over the window is roughly 1.4%, so +3% is a real premium over
cash rather than a rounding artifact — a neutral book returning cash-plus-nothing has delivered
nothing; (ii) 0.30 is a genuine decorrelation threshold, not a token one; (iii) a neutral book that
draws down like an equity book has not delivered the diversification that is its entire claim;
(iv) the arm must not be a 2009 casualty rescued by the rest of the window.

**A QUALIFIED POSITIVE is explicitly NOT adoption and NOT a recommendation to trade.** It means the
construction earns a dedicated follow-up study. This is committed in advance specifically so that a
genuinely valuable low-correlation result is not dismissed for losing a return race it was never
trying to win — and equally so that a soft criterion cannot become a back door to declaring success
after the primary bar is missed.

**If Arm C clears §9.1's primary bar as well, that is a strictly stronger result. If it clears
neither, it fails outright.**

**The degenerate case, named:** near-zero correlation achieved by sitting in cash is not market
neutrality. Condition (ii) is therefore **only readable alongside mean gross exposure** (§9.4). A
Pool-2 Arm C posting |corr| = 0.05 at mean gross exposure of 0.25 has not built a neutral book; it
has built a mostly-cash book. **Pre-committed rule: if Arm C's mean gross exposure is below 0.50,
condition (ii) is reported as not meaningfully satisfied**, regardless of the correlation value.

### 9.3 Drawdown ceiling — one-sided, against worsening only

**An arm's peak-relative max drawdown must not exceed Arm A's own by more than 15 percentage points.**
An arm whose drawdown equals or improves on baseline's passes this condition regardless of return —
the one-sided wording every study since the cross-sectional one has used, after that study's
gap-based version produced a backwards-reading "failure" on an arm whose drawdown had improved.

Arm C additionally faces the tighter absolute ceiling in §9.2(iii).

### 9.4 Exposure reporting — replacing the deployment guard, which does not apply here

**The ≤2pp deployment-parity guard used by every prior study CANNOT be applied as a pass/fail to
Arms B and C**, because they are *designed* to have different exposure. Applying it would fail both
arms for succeeding at their own construction — a check reporting a result for a reason unrelated to
what it was checking, precisely the class this project has named.

Replaced by, per arm:
- mean/median/p10/p90 **long_pct, short_pct, gross_pct, net_pct**;
- mean realised **position count**, long and short separately;
- **mean number of short candidates available per day** (prior 7's direct test — this is what
  distinguishes "Arm C is neutral" from "Arm C had nothing to short");
- **fraction of trading days on which the short sleeve held zero positions**;
- the deployment guard **retained in one narrow form only**: Arm B's *long sleeve* mean long_pct vs.
  baseline's mean deployment, ≤2pp, since Arm B's long cap is unchanged at 0.70 and a large
  divergence there would indicate the short sleeve is starving the long book rather than
  supplementing it.

### 9.5 The 2009 block — reported separately and prominently

`make_blocks()` divides the window into 16 equal-width blocks. At a 2007-01-01 → 2026-08-07 window
that puts the crisis block at approximately **2008-03-23 → 2009-06-13** (exact boundaries recomputed
at run time, not assumed from this arithmetic).

**The block boundary bisects the episode**, which matters: the March–June 2009 momentum crash sits at
the very *end* of block 2, and the rebound continues into block 3. Reporting block 2 alone would
split the single event this arm is most exposed to across two reporting units and understate it in
both.

**Therefore, reported separately and prominently, in addition to the standard 16-block table:**
- **the crisis block** (block 2) on its own;
- **an explicit momentum-crash window, 2009-03-09 → 2009-09-30**, spanning the bottom and the full
  rebound, reported as its own line: total P&L, short-sleeve P&L in isolation, max drawdown,
  `RALLY_EXIT` count, and the arm-vs-baseline delta.

**Pre-committed: if an arm's advantage disappears or reverses in the momentum-crash window, that is
stated plainly in the report's own opening summary, not buried in a block table.** An arm that wins
overall by avoiding the one episode its short sleeve is structurally most exposed to — because the
episode happened to fall where the breaker or the data was kind — has not demonstrated robustness.

**Block-pass count is corroborating only**, per the measured 3-block resolution floor. It will not
discriminate at this scale and is not used to decide anything.

### 9.6 The short sleeve's unrealizable-risk share

Because the short sleeve's best outcomes are the least trustworthy (prior 4, prior 5), reported as a
first-class line, not a footnote — the fraction of **short-sleeve gross profit** arising from
positions that:

```
(a) exited at a price below $1.00, or
(b) exited via END_OF_DATA, or
(c) sit in a ticker carrying a known price-cliff exception
```

**Pre-committed: if more than 30% of short-sleeve profit falls in this bucket, the short sleeve's
headline is reported as substantially dependent on outcomes that would not have been realisable in
practice** — borrow recall, trading halts, and delisting all bite hardest exactly there.

---

## 10. The paired per-decision instrument — what is constructible, and what is not

**Constructible for the long sleeve.** All arms share the daily clock, so exact `(ticker, entry_date)`
pairing of Arm B/C long admissions against Arm A's is directly available, and a shared key is
capacity-cascade-uncontaminated by construction. Reported: shared-key outcome comparison, plus
each arm's own unique-admission win rate and mean P&L% against baseline's own unique admissions —
the instrument that decided the relative-ranking study.

**NOT constructible for the short sleeve, and this is a hard limit, not a gap to be worked around.**
Arm A has no shorts. There is no baseline counterpart to pair a short against, and no amount of
sample accumulation creates one. Stating this now so it is not discovered mid-analysis and papered
over with a weaker proxy presented as equivalent.

**What replaces it for the short sleeve** — population-level reporting only, explicitly labelled as
weaker evidence than a paired comparison: trade count, win rate, mean/median P&L%, mean holding days,
exit-reason distribution (including `RALLY_EXIT` share), and §9.6's unrealizable-risk share.

**Expected overlap, predicted in advance** (per prior 6): Arm B long sleeve vs. baseline,
**10–25%** exact `(ticker, entry_date)`; Arm C lower still, **5–20%**, since C's long budget is 0.50
rather than 0.70. The relative-ranking study measured 21.8–23.1% for a change that touched *only* the
sort key; these arms additionally change the equity path that drives sizing, so lower is expected.
**A prediction miss is reported as a miss** — the relative-ranking study's own overlap prediction was
wrong by a wide margin and was reported as wrong rather than smoothed over.

**Minimum-sample floor: 100 differing trades per pairing.** Below that, reported as a bare count and
not interpreted.

---

## 11. Verification plan — before any real result is trusted

1. **Two-level degenerate parity.**
   (a) With the short sleeve disabled (short cap = 0, rally breaker inert), the new long/short engine
   must reproduce **Arm A byte-identical** — same trade count, same total P&L, 0 row-level mismatches
   on a canonical `(ticker, entry_date, exit_date)` sort.
   (b) Arm A itself must reproduce raw, unmodified `simulate_portfolio_compounding_mtm()` against the
   current config. Together these prove every part of the new engine except the short sleeve is a
   faithful copy of production.
2. **The reflection test** (§4) — ≥100,000 randomized tuples plus boundary cases, zero mismatches
   permitted.
3. **Short-sleeve exit wiring** — a sample of ≥30 real short trades' recorded exit reasons
   independently recomputed from the trade's own reconstructed inputs, including explicit confirmation
   that every short `STOP_LOSS` fired off the day's `high` (never `low`, never `close`) and filled at
   `max(stop_price, open)`.
4. **Sizing** — spot-checked against the engine's **own emitted pre-entry balance**, never a proxy
   reconstructed after the fact. The cross-sectional study's initial 21/30 "failure" traced to exactly
   that proxy error, not an engine bug; this study does not repeat it.
5. **Borrow accrual arithmetic** — a sample of ≥20 shorts' total accrued borrow recomputed by hand
   from entry/exit dates, daily MTM notional, and tier, and matched against the engine's own recorded
   figure.
6. **Rally-breaker firing dates hand-checked** against known market history (§14.2), not merely
   counted.
7. **Signal separation** — confirm the relative-rank value is used *only* as a sort key on both
   sleeves and never reaches any gate or exit evaluator, on either side.
8. **Short candidate set spot-check** — on a sample of dates, hand-compute the §3 gate and confirm the
   engine's short candidate set matches exactly.

**Per this project's standing requirement for research scripts that construct a custom candidate pool
or reach into the pipeline: this verification is evidence gathered from this study's own runs, not
inherited from "the production codebase is consolidated."** That is a fact about production, and this
engine is not production.

---

## 12. Sanity gate, and mandatory robustness for any clearing arm

**Sanity gate:** Arm A must reproduce a **fresh** reference run of unmodified
`simulate_portfolio_compounding_mtm()` at the current config. **Compared as trade SETS**
`(ticker, entry_date, exit_date, exit_reason, pnl)`, never totals alone, on a canonical sort — a
discrepancy is row-diffed and explained, never assumed benign. Prior studies' baseline trade files
may be used as an immediate cross-check but are **not** assumed current; the database has moved
before and produced exactly this class of decayed-reference failure twice in one week.

**Mandatory robustness:** any arm clearing §9.1's bar is **provisional** until a **20-draw paired
perturbation test** (10 single-ticker drops, 6 start-date shifts, 4 tie-break reshuffles, both arms
run against the *identical* perturbed state per draw) is completed, with the same reporting standard
Arm B's own robustness test met: the full delta distribution, the unperturbed value's placement
within it, and any single-ticker dependency **named** rather than averaged away. Given prior 5, the
short sleeve is a plausible venue for a single-name dependency considerably larger than Arm B's AMAT.

---

## 13. Pools

**Pool 1** (`get_live_stock_candidates()`) — **direction only**. **Pool 2 CLEAN**
(`get_research_stock_candidates() ∩ status == 'CLEAN'`) — **decisive**. Both **reconfirmed fresh at
run time** by direct query, never carried over from an earlier session's count.

**With the structural caveat from prior 5 elevated to a reporting requirement**: on the short side
these two pools are not "the same test on more or fewer names." Pool 1 contains only survivors and
therefore *cannot* produce the terminal-decline short winners that Pool 2 CLEAN can. **A cross-pool
disagreement on the short sleeve is the expected, structurally-predicted outcome and must be reported
as such** — not chased as a data-quality defect, and not resolved by picking whichever pool tells the
better story. Per the dual-provenance standing requirement, a single reconciliation attempt does not
close a disagreement.

---

## 14. Pre-run calibration — frozen before any arm runs

**All calibration is on independent variables only — no arm results, no outcome data.** This mirrors
the momentum-smoothness study, where calibrating thresholds from the independent variable's own
distribution and freezing them before running was the step that made the null interpretable.

### 14.1 HTB flag frequency

Measure the fraction of **short-eligible candidate-days** flagged HTB by §5.2, across both pools' full
history. Reported before any arm runs, then frozen.

- **Expected: 2–8%**, consistent with eToro's "<2% of symbols" adjusted upward for the fact that this
  measures candidate-*days* among *already momentum-negative* names, a deliberately distressed slice.
- **If >15%**: thresholds are mis-calibrated for an S&P 500 universe. Re-set them **on the flag's own
  distribution only**, re-freeze, and document the change before running.
- **If <0.5%**: the HTB tier is effectively inert. **Report plainly that the borrow-cost model was not
  meaningfully tested at the tier that matters**, rather than claiming borrow cost was modelled.

### 14.2 Rally-breaker firing dates

Enumerate every `RALLY_RAW` firing date on `^GSPC` alone, for **all five** §6.5 configurations, with
the readings pre-committed in §6.4 (0 fires → untested, not safe; >25 → not a breaker; no 2009 fire →
design failure). Expected for PRIMARY: roughly **3–12 distinct episodes** over 2007–2026 — the same
rare-firing philosophy as the crisis breaker's own 7 days.

### 14.3 Short candidate availability

Mean/median short candidates per day, and the fraction of trading days with **zero** short candidates,
per pool, per year. This is the direct test of prior 7 and it determines whether Arm C is testable at
all: **if Arm C has no short candidates on a large fraction of days, it is a throttled long book, not
a market-neutral one**, and must be reported that way rather than as a neutral arm that happened to
underperform.

---

## 15. The walk-away sentence

> **I will not treat this study as showing that a short sleeve helps if: no arm clears +1.5pp
> cumulative cost-adjusted excess return over Arm A's own excess return at the MODELLED borrow rate
> without exceeding Arm A's peak-relative drawdown by more than 15pp; or if a clearing arm's advantage
> disappears or reverses at the 2× stress borrow rate; or if a clearing arm's advantage disappears or
> reverses in the 2009-03-09 → 2009-09-30 momentum-crash window; or if a clearing arm fails at ≥2 of
> the four non-PRIMARY rally-breaker configurations; or if more than 30% of short-sleeve gross profit
> comes from the unrealizable-risk bucket (§9.6); or if Arm B's long-sleeve paired per-decision
> instrument shows its unique admissions are not distinguishable from baseline's on a usable sample;
> or if Arm C posts near-zero correlation at mean gross exposure below 0.50 (a cash book, not a
> neutral book).**

**This sentence is a VETO, not an adoption trigger.** An arm that fires no clause has not thereby
earned adoption — it has earned the right to be described as a genuine finding and to proceed to
§12's mandatory robustness test. The momentum-smoothness study's Arm B1 fired no clause, cleared its
bar by 0.051pp, and was correctly not adopted; the same standard applies here.

**And regardless of any outcome: nothing here is adoptable, because no venue exists.** T212 Invest
does not support shorting. A clearing result makes "which venue" a real question worth asking. It
does not make this a change to the live system.

---

## 16. Constraints re-confirmed

**No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path.**
The short-side exit evaluator lives in research code only (§4) — it is explicitly **not** added to
`analysis/decision.py`, because a live-path decision module must not carry a branch no live caller
uses. `_resolve_entry_stop("SHORT", ...)` is called as-is; it already supports the direction and is
not modified. The research cache builder adds the `high` column for its own use (§4); it does not
modify `analysis/backtest.py:precompute_ticker()`.

Research only, in the dedicated worktree `c:\GitHub\investment-app-longshort`. Nothing adopted,
nothing merged, nothing deployed.

---

## 17. What this study cannot close, whatever the result

1. **Whether it is tradeable.** No venue, no borrow desk, no real borrow quotes. Out of scope by
   instruction, and unaffected by any result here.
2. **Whether the borrow model is right.** It is an assumption at both tiers. The stress case bounds
   it crudely; it does not validate it.
3. **Whether short fills are achievable.** The backtest fills shorts at the close (or `max(stop, open)`
   on a stop) with no locate, no recall, no halt, and no uptick constraint. §9.6 measures the
   worst-affected slice; it does not model the mechanism.
4. **Whether the rally breaker generalises.** It is calibrated against a window containing roughly one
   canonical momentum crash. One episode is not a sample, and a breaker tuned to survive 2009 has an
   obvious in-sample flavour that §6.5's grid mitigates but cannot remove.
5. **Multiple comparisons** (prior 8). Robustness testing addresses configuration luck, not the
   selection effect of being the 61st experiment.
