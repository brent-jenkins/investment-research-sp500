# Exit-mechanics sweep — pre-registration (2026-08-11)

**PRE-REGISTRATION ONLY. Nothing has been run, no engine has been built, no cache generated.**
Dedicated worktree (`c:\GitHub\investment-app-exitmech`, branch `research/exit-mechanics-2026-08-11`).

---

## 0. Why one study, not three

Stop multiplier, max-holding days, and a trailing stop all answer the same question — **when does a
position close** — and they compete for the same exits. A longer max-holding converts `MAX_HOLDING`
exits into `SIGNAL_OFF` or `STOP_LOSS`; a trailing stop converts what would have been `SIGNAL_OFF`
or `MAX_HOLDING` winners into `STOP_LOSS`; a wider (or absent) ATR stop removes `STOP_LOSS` events
that the other two then have to absorb. **Testing any one against a value of another that is itself
suspect would produce a result that needs redoing the moment the other moves.** This is one
mechanism with three dials, and it is swept as one.

---

## 1. THE BASELINE IS THE CURRENT LIVE SYSTEM, AND THAT IS A CHANGE FROM THE EARLIER STUDY

**Baseline = relative-rank admission (Arm B), adopted 2026-08-11**, `per_trade_pct=0.05`,
`max_total_deployed=0.70`, `atr_stop_multiplier=2.0`, `stock_max_holding_days=30`,
`reentry_cooldown_days=0`, `min_momentum_pct=0.02`. Preserved trade sets:
`research/relative_ranking_prereg_2026-08-09/trades_pool2_clean_B.csv` (**4,876 trades,
£166,830.11**) and `trades_pool1_B.csv` (**4,757 trades**).

**The stop-loss reconfiguration study (2026-08-07) was run against absolute-momentum ordering, and
its conclusions are NOT inherited here.** Three things differ, each independently sufficient to
require re-testing rather than assuming carry-over:

1. **Different admission ordering.** That study predates the relative-rank adoption entirely. The
   relative-ranking study measured only ~22% exact-date admission overlap between the two orderings
   — a *sort-key change alone* reshuffled the majority of the 19-year trade sequence. A conclusion
   about exit mechanics measured on one admission sequence has no automatic claim on a materially
   different one.
2. **Different universe.** Its "Pool 2" was `get_research_stock_candidates()` at **1,043 tickers**,
   before the Wikipedia adjudication settled. This study's decisive pool is **Pool 2 CLEAN** (612
   tickers, 608 usable), a different population.
3. **Different data state.** Quarantine counts, and therefore the candidate pool, moved between then
   and now.

**What that study found, recorded here as the prior being re-tested rather than as an input:**
cost-adjusted CAGR ranked **B (no stop) > C1 (3.0×) > A (2.0×)** in both pools, with B's advantage
driven substantially by ~26% lower turnover and therefore lower FX drag. Two caveats travel with it:
Pool 2's C2 (4.0×) **broke the monotonic trend** (3.14%, worse than baseline), showing wider-is-better
does not extrapolate; and the measurement toolkit's later paired instrument found **B's advantage far
smaller than the portfolio headline** — 51.4% on 2,444 differing decisions, i.e. close to a coin
flip. **The portfolio-level ranking is therefore treated as unconfirmed, not as established.**

**One finding from that study IS carried forward as method, not as conclusion**: its whipsaw
quantification (68% of `STOP_LOSS` exits re-entered within 30 days; combined pair P&L heavily
negative, −£360k/−£298k) is the exact template for §3's `MAX_HOLDING` round-trip measurement.

---

## 2. The three variables

| Dial | Values | Notes |
|---|---|---|
| `atr_stop_multiplier` | **2.0** (current), 3.0, **none** | "None" is natively supported by production: `simulate_portfolio_compounding_mtm(..., disable_stop_loss=True)`. 4.0× is deliberately excluded — the earlier study found it non-monotonic and worse than baseline in one pool, so it is a known dead end, not untested ground. |
| `stock_max_holding_days` | **30** (current), 60, 90, **unlimited** | "Unlimited" is implemented by passing `max_holding_days=None` into the existing `evaluate_exit_reason()`, using that function's already-documented `None`-tolerance ("unknown, don't trigger this check"). **No change to `analysis/decision.py`.** |
| Trailing-stop activation | **none** (current), +5%, +10% | Activation threshold only. **Trail distance is NOT swept** — it is fixed at the ATR multiplier of the cell it runs in (§4). |

---

## 3. STAGE 0 — a read-only measurement, run before any sweep cell

**No backtest. Computed from the preserved baseline trade sets alone**, exactly as the stale-peak and
feature-screen diagnostics were.

**The question, from the motivation:** with `reentry_cooldown_days = 0`, a position forced out by
`MAX_HOLDING` can be re-admitted into the same ticker immediately, paying FX both ways for a round
trip nothing asked for. **How often, and at what cost?**

Reusing the stop-loss study's whipsaw methodology verbatim, substituting the exit reason:

```
for each exit with exit_reason == "MAX_HOLDING":
    find the next entry in the SAME ticker
    record: gap in trading days, the MAX_HOLDING leg's P&L, the re-entry leg's P&L
report: count and % re-entered within N in {1, 5, 10, 21, 30} days
        combined P&L of those pairs; net-positive vs net-negative pair counts
        total FX cost paid on the round trip (0.30% x the two position_gbp values)
```

**Why this runs first**: it sizes the problem the max-holding dial is meant to address, using
decisions already made, before any parameter is swept. If `MAX_HOLDING`-then-reenter is rare or
cheap, the max-holding dial's plausible upside is bounded before a single cell runs — and that bound
is worth knowing in advance rather than inferred afterwards from whichever cell wins.

**Stated in advance so it cannot be read selectively**: a large, expensive round-trip population
makes longer max-holding *plausible*, not proven — the counterfactual "just held it" is **not**
observable from this measurement, because the re-entry leg's own outcome was produced under a
different capacity state. Stage 0 sizes an opportunity; only the sweep can price it.

---

## 4. The trailing stop — specified exactly, including the anti-lookahead ordering

**Not in `analysis/decision.py` and not added there** (§10). Research-only, in the sweep engine.

```
Let E        = entry price
    ATR_e    = ATR at entry (the same value _resolve_entry_stop() already uses)
    k        = the cell's ATR multiplier (2.0 or 3.0; see the no-stop case below)
    A        = activation threshold (0.05 or 0.10)
    S_0      = the initial stop from _resolve_entry_stop("LONG", E, ATR_e, config)
    HWM      = highest CLOSE since entry (initialised to E)
    S_t      = the position's live stop

daily, in this exact order:
  1. CHECK the stop against today's LOW using S_t as it stood at yesterday's close
  2. if not stopped: if close(t)/E - 1 >= A, the trail is ACTIVE (latches on, never off)
  3. HWM = max(HWM, close(t))
  4. if active: S_t = max(S_t, HWM - k * ATR_e)      # ratchet UP only, never down
```

**Step 1 preceding steps 2–4 is load-bearing, and is the specific degenerate trap this design has to
avoid.** If the high-water mark were updated from today's bar *before* the stop were checked against
today's low, a single day's range could both tighten the stop and then trigger it — manufacturing
exits that no real, sequentially-executed trailing stop could produce, and doing so *invisibly*,
since the result would look like an ordinary `STOP_LOSS`. Checking against yesterday's trail level
first makes the mechanism causal. **This is exactly the class of error the standing "checks that
report clean for an unrelated reason" entry describes** — it would not crash, and it would produce
plausible numbers.

**HWM uses the CLOSE, not the daily high** — deliberately, and stated as a choice with its
alternative named. Using the high would tighten the trail using an intraday extreme that the position
never actually closed at, compounding the same optimism the 2026-08-06 realistic-fill correction
removed. Close-based is the conservative direction.

**ATR is fixed at entry, not recomputed daily** — consistent with how the initial stop already works,
and one fewer moving part. The adaptive alternative (recompute ATR each day) is named here and not
tested; sweeping it would be a fourth dial.

**Fill convention on a trailing-stop trigger is identical to the existing `STOP_LOSS`**: fires off the
day's `low`, fills at `min(S_t, open)`. Exit reason is recorded as **`TRAILING_STOP`**, a distinct
label from `STOP_LOSS`, so §7's exit-composition table can separate them. It is evaluated at the same
priority position as `STOP_LOSS` (after `CRISIS_EXIT`, before `SIGNAL_OFF`).

**The no-ATR-stop × trailing interaction, resolved in advance rather than discovered mid-run.** If
Stage 2 selects "no stop", `k` is undefined for Stage 3. **Pre-committed: `k = 2.0` (production's own
current value) in that case**, and the resulting cell is named for what it actually is — **no initial
stop, with a 2.0×ATR trail engaging only after the position has gained A%**. That is a coherent and
genuinely distinct construction (no downside protection until the position proves itself, then a
ratcheting floor), not a patch, and it is labelled as such wherever reported.

---

## 5. The sweep design — staged, with the staging bias guarded explicitly

### 5.1 Why not the full grid

A full grid is 3 × 4 × 3 = **36 cells per pool, 72 runs**. Rejected, for a reason specific to this
project's measured resolution rather than a general preference for small designs: the noise floor is
**~1.08pp cost-adjusted CAGR** and **3 blocks**, and the adoption bar is **+1.5pp**. With 72 cells
drawn against a floor that size, cells clearing +1.5pp by chance alone stop being unlikely and become
expected — and there is no post-hoc correction that restores interpretability once that has happened,
because this project's own precedent (the deployment-cap study: a 1.06pp spread across arms against a
1.08pp floor) shows arm-to-arm spreads at this scale are frequently indistinguishable from noise.
**A design that is likely to produce a spurious winner is not rescued by careful reporting of the
winner.**

Compute cost is a secondary reason and is stated as secondary: at ~25 min/run measured, 72 runs is
~30 hours serial.

### 5.2 The staged design

| Stage | What varies | Held at | New runs/pool |
|---|---|---|---|
| **0** | nothing (read-only) | — | 0 |
| **1** | max-holding: 30, 60, 90, unlimited | stop 2.0×, no trailing | 3 (30 = baseline, exists) |
| **2** | stop: 2.0×, 3.0×, none | Stage-1 carried value, no trailing | 2 |
| **3** | trailing activation: none, +5%, +10% | Stage-1 and Stage-2 carried values | 2 |
| **B** | back-check (§5.4) | final settings | 3 (Pool 2 CLEAN only) |

**Total: 7 new runs/pool × 2 pools + 3 back-check = 17 runs**, versus 72 for the full grid.

**Order justified, not arbitrary.** Max-holding goes first because it is the only dial with a
*pre-existing directional suspicion backed by a measurable quantity* (Stage 0's round-trip cost), and
because it is the dial that most changes how many exits the other two dials even get to act on.
Trailing goes last because it is the only dial that *adds* exits, and its cost is best measured
against an otherwise-settled exit regime.

### 5.3 The carry-forward rule — conservative, fixed now

**A stage carries forward a non-production value ONLY if it beats the production value by ≥1.08pp
cost-adjusted excess return on Pool 2 CLEAN (the decisive pool). Otherwise the PRODUCTION value is
carried forward, regardless of nominal ranking.**

This is deliberately stricter than "carry the best cell". A nominal winner inside the noise floor is
not evidence, and conditioning two later stages on it would propagate a coin flip through the whole
design. **The production value is the default that must be beaten, not one option among equals.**
Full curves are reported at every stage, not just the carried value.

### 5.4 The back-check — the direct test of whether staging biased the result

**After Stage 3, re-run Stage 1's non-carried max-holding values at the FINAL stop and trailing
settings** (Pool 2 CLEAN only, 3 cells).

**The pre-committed reading**: if the max-holding ranking at final settings differs from the ranking
at Stage 1 settings, **the result is path-dependent and is reported as path-dependent** — meaning the
staged design found a local combination, not a best one, and no cell is described as a winner. If
the ranking holds, the staging is reported as validated for this sweep specifically (not in general).

**This is the guard that makes the staged design honest**, and it is why the design is staged rather
than merely truncated: a truncated grid cannot tell you whether its own truncation mattered.

---

## 6. Criteria

**PRIMARY — cost-adjusted excess return over `^SP500TR`**, continuous-walk convention, computed as
each cell's own excess minus baseline's own excess. **Required margin: ≥ +1.5pp**, unchanged from the
standing bar used by every study in this sequence (~1.4× the measured ~1.08pp floor). Not re-derived
per study, deliberately.

**Costs modelled per cell, never deferred** — and this is not a formality here, it is the crux.
Turnover is the *treatment*, not a nuisance: **longer max-holding removes exits; a trailing stop adds
them; a wider/absent ATR stop removes them.** These pull in opposite directions and the net cannot be
argued, only measured. FX at 0.30% round-trip on each cell's real `position_gbp` values; idle-cash
credit at mean `^IRX` on realised idle fraction. Reported per cell as its own line, not folded into a
single headline.

**Turnover reported per cell** as: trade count, mean holding days, total notional traded, FX drag
pp/yr, and the delta of each against baseline.

**Drawdown — one-sided against WORSENING only.** A cell's peak-relative max drawdown must not exceed
baseline's by more than **15 percentage points**. A cell whose drawdown equals or improves on
baseline's passes this condition regardless of return. (Stated directionally because the
cross-sectional study's gap-based wording produced a backwards-reading failure on an arm whose
drawdown had improved.)

**Paired per-decision instrument — PRIMARY evidence, not corroboration.** Every cell shares the
baseline's entry logic exactly (only exits change), so exact `(ticker, entry_date)` pairing is
directly constructible and the shared-key population should be large. **A shared key is
capacity-cascade-uncontaminated by construction**, which is precisely why the toolkit built it. For
each cell: outcome comparison on shared keys (same position, different exit — the cleanest possible
comparison), plus each arm's unique-admission win rate and mean P&L%. **Minimum-sample floor: 100
differing trades per pairing**; below that, reported as a bare count and not interpreted.

**Weight, stated in advance**: this instrument has now deflated three portfolio-level results
(admission redesign, cross-sectional monthly, and the stop study's own Arm B) and confirmed one
(relative-rank Arm B). **Where the paired instrument and the portfolio headline disagree, the paired
instrument governs the verdict**, and the disagreement itself is reported as the finding.

**Top-decile trade overlap alongside every headline gap** — exact `(ticker, entry_date)` Jaccard and
ticker-only Jaccard, plus the share of each cell's total P&L contributed by trades *not* present in
baseline. The strategy is right-tail driven (top decile carries **257%** of P&L), so two cells can
differ almost entirely by which handful of trades they happened to hold, and a headline gap that
traces to non-shared top-decile trades is a different claim from one that survives on shared ones.

**Exits by reason, per cell** — full composition (`CRISIS_EXIT`, `STOP_LOSS`, `TRAILING_STOP`,
`SIGNAL_OFF`, `MAX_HOLDING`, `END_OF_DATA`), as counts, as % of exits, and as P&L contribution.
**This is where the interaction becomes visible**: longer max-holding should convert `MAX_HOLDING`
into `SIGNAL_OFF`/`STOP_LOSS`; a trailing stop should convert `SIGNAL_OFF`/`MAX_HOLDING` winners into
`TRAILING_STOP`. **If a cell's headline moves without a corresponding composition shift, the dial did
not do what it claims** and the result is treated as a cascade artefact until explained.

**Capacity-turnover effect, reported SEPARATELY from the direct effect**, per the standing
requirement — which exists for exactly this class of change (exit timing). Two numbers, never
blended: (a) the direct effect on positions whose exit actually changed, and (b) the change in total
trade count / distinct opportunities taken, with an accounting of what filled the freed or consumed
capacity.

**Block-pass count: corroborating only**, per the measured 3-block floor. It will not discriminate at
this arm-to-arm distance and decides nothing.

---

## 7. Verification, before any cell result is trusted

1. **Degenerate parity — the trailing engine must vanish.** With activation threshold set to
   unreachable (`+inf`) and max-holding at 30 and stop at 2.0×, the sweep engine must reproduce raw
   production `simulate_portfolio_compounding_mtm()` **byte-identical** on a canonical
   `(ticker, entry_date, exit_date)` sort — same trade count, same total P&L, 0 row-level mismatches.
   This proves every part of the engine except the trailing logic is a faithful copy.
2. **Second degenerate reduction**: `disable_stop_loss=True` and `max_holding_days=None` are both
   production-supported paths, so the "no stop" and "unlimited" cells must be produced by production's
   own parameters, not by new code. Confirmed by reading, then by the parity run above.
3. **Trailing-stop unit behaviour** on synthetic series with hand-computed expected exits: a position
   that never reaches activation must behave identically to baseline; one that activates and then
   retraces exactly to the trail must exit at the trail; the ratchet must never lower a stop; and the
   anti-lookahead ordering must be demonstrated by a constructed bar whose high would have triggered
   a same-day exit under the wrong ordering and does not under the correct one.
4. **Exit-wiring**: a sample of ≥30 real trades per new exit reason, recomputed independently from
   the trade's own reconstructed inputs.
5. **No `TRAILING_STOP` may ever appear in a cell with activation = none** — asserted directly, the
   same way the long/short study asserted zero `CRISIS_EXIT` on shorts.

**Evidence from this study's own runs, not inherited from "the production codebase is consolidated"**
— that is a fact about production, and the sweep engine is not production.

---

## 8. Sanity gate

**Baseline must reproduce the current live configuration's known figures, compared as trade SETS**
`(ticker, entry_date, exit_date, exit_reason, pnl)` on a canonical sort — never totals alone:
**Pool 2 CLEAN 4,876 trades / £166,830.11**, **Pool 1 4,757 trades**. Any discrepancy is row-diffed
and explained, never assumed benign. (This exact reproduction was confirmed achievable earlier today
in the long/short study, byte-identical, so a failure here is a real signal rather than an expected
tolerance.)

---

## 9. Pools

**Pool 1** (`get_live_stock_candidates()`) — **direction only**. **Pool 2 CLEAN**
(`get_research_stock_candidates() ∩ status == 'CLEAN'`) — **decisive**. Both **reconfirmed fresh by
direct query at run time**, never carried from an earlier session's count.

Per the dual-provenance standing requirement, a cross-pool disagreement is reported as a
disagreement, not resolved by picking the more favourable pool. **Relevant structural note**: Pool 1
is survivors-only by construction, so it cannot contain a terminal decline — which bears directly on
the *stop* dial specifically, since a stop's value is largely in truncating exactly that. **A cell
that looks better on Pool 1 than Pool 2 CLEAN on the stop dimension should be read with that
asymmetry in mind**, and Pool 2 CLEAN is decisive here partly for that reason.

---

## 10. Honest priors, stated before any result

1. **The trailing stop's prior is unfavourable, and the direction of the evidence is against it.** It
   tightens the effective stop as a position runs — the opposite of what the earlier stop study
   favoured (wider, or none). Arm C's finding that faster exits raise win rate and lower returns
   points the same way, and the strategy is right-tail driven, so anything truncating winners starts
   at a disadvantage.
2. **The activation threshold is what makes this different from a naive profit target** — it only
   trails positions that have already proven themselves, leaving losers governed by the ordinary stop
   — but that is a reason it *might* not fail, not a reason to expect it to succeed.
3. **Entry information does not predict individual trade outcome** — four independent investigations
   (momentum magnitude, path smoothness, peak drawdown, and a 16-feature screen). **This study is
   about EXIT mechanics, which those findings do not speak to**, and that is precisely why this is
   the remaining untested ground. But it also means there is no basis for expecting exit rules to work
   better when conditioned on entry characteristics, and no such conditioning is proposed.
4. **Turnover is the most likely mechanism for any real effect, in either direction.** Every prior
   negative result in this project traces to it — Arm C's added exits, the deployment cap's scaling
   FX, displacement at every viable margin. The max-holding dial and the trailing dial move turnover
   in *opposite* directions, so a combined cell can net to nothing while both components move.
5. **The earlier stop study's own headline is itself unconfirmed** (its Arm B advantage measured 51.4%
   on the paired instrument — a coin flip). The prior for "no stop beats 2.0×" is therefore weaker
   than that study's portfolio table alone suggests.
6. **Multiple comparisons**: even the staged design tests ~8 cells per pool. The +1.5pp bar, the
   conservative carry-forward, and the back-check exist because of that, not despite it.

---

## 11. The walk-away sentence

> **I will not treat any cell as an improvement if: it fails to clear +1.5pp cost-adjusted excess
> return over baseline's own excess on Pool 2 CLEAN; or if it clears that bar while exceeding
> baseline's peak-relative drawdown by more than 15pp; or if its paired per-decision instrument shows
> its shared-key outcomes are not distinguishably better than baseline's on a usable sample; or if its
> headline gap traces mainly to top-decile trades absent from baseline rather than surviving on shared
> ones; or if its exit-reason composition does not shift in the direction its own dial predicts; or if
> the §5.4 back-check shows the max-holding ranking changes at final settings, in which case NO cell
> is reported as a winner and the result is reported as path-dependent.**

**This sentence is a VETO, not an adoption trigger.** A cell firing no clause has earned the right to
be described as a genuine finding and to proceed to a 20-draw paired perturbation robustness test —
**not** adoption. The momentum-smoothness study's B1 fired no clause, cleared its bar by 0.051pp, and
was correctly not adopted; the same standard applies.

---

## 12. Constraints re-confirmed

No changes to `analysis/signals.py`, `analysis/decision.py`, `config.yaml`, or any live path. The
"no stop" and "unlimited holding" cells use production's own existing parameters
(`disable_stop_loss=True`; `max_holding_days=None` via `evaluate_exit_reason()`'s existing
`None`-tolerance). The trailing stop lives only in the research sweep engine and is **not** added to
`analysis/decision.py`. Config variations are passed as copied dicts, never by editing `config.yaml`.
Research only, in the dedicated worktree. **Nothing adopted, nothing merged, nothing deployed** —
and a clearing cell would still require the promotion work (a genuine production module plus coverage
in all four live/backtest parity layers) that the relative-rank promotion needed, before it could even
be considered.
