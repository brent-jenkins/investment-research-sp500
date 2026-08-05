# Trailing-momentum lookback window — researched, not adopted (2026-08-05)

**Question.** Is 252 trading days (12 months) still the right
`MOMENTUM_LOOKBACK_DAYS`? It was chosen once during the V1→V2 rebuild,
compared only against a 200-day SMA, and never swept — the most structurally
central untested parameter in the system.

**Answer: 12 months is confirmed well-chosen. Researched, not adopted.**
`config.yaml` and `analysis/signals.py` untouched, nothing deployed.

Swept 3/6/9/12/15/18/24 months, reset-at-boundary, test period 2020-01-01 →
2026-08-03, live 503-ticker universe, everything else held fixed. Then
16-block validated **all seven arms**, not just the promising ones.

---

## How the variant was built — no engine fork

`analysis/backtest.py:precompute_ticker()` calls
`signals.compute_momentum_series()` / `compute_signal_series()`, and
`backtest.py:21` does `from analysis import signals` — so the module constant
is looked up **per call**. Setting it before precompute varies the signal with
zero change to any decision, sizing, exit or admission logic.

**`_MAX_ANCHOR_GAP_DAYS` had to scale with the lookback, and this was not
optional.** It is a calendar-staleness guard sized so a gapless 252-trading-day
window (~365 calendar days) is accepted with slack. Left at its hardcoded 400
while sweeping to 24 months (504 trading days ≈ 730 calendar days) it would
mark *every* row stale and NaN out the entire signal — silently producing
"this lookback generates no trades" rather than an error:

```python
_MAX_ANCHOR_GAP_DAYS = round(lookback_days * 400 / 252)
# 63->100, 126->200, 189->300, 252->400 (unchanged), 315->500, 378->600, 504->800
```

`precompute_ticker()`'s own `len(df) < 300` minimum was deliberately **not**
scaled, so every arm sees an identical candidate pool and only the signal varies.

Engine: the already-committed `risk_adjusted_momentum_2026-08-05/
risk_adjusted_engine.py` with `rank_mode="raw"`, imported rather than re-copied
so there is one verified copy of the block-truncation plumbing, not two.

**Verification** (`self_check.py`): byte-identical to the real unpatched
`simulate_portfolio_compounding_mtm()` at production lookback (2,856 trades,
exact); patch provably changes the signal (momentum σ 0.144 / 0.286 / 0.437 at
3/12/24m); decision-engine parity evidenced from the 24m arm's own trades
(0 holding-period violations, shared exit vocabulary and priority chain,
0 sizing violations); and no lookback is signal-dead (momentum computable
98.6%→88.6% across 3m→24m).

## Sweep — reset-at-boundary, test period

| Lookback | Trades | CAGR | Max DD | PF | Sharpe |
|---|---|---|---|---|---|
| 3m | 1,329 | 18.44% | -21.92% | 1.82 | 0.693 |
| 6m | 1,317 | 18.07% | -24.83% | 1.74 | 0.665 |
| 9m | 1,243 | 22.04% | -34.89% | 1.85 | 0.708 |
| **12m (current)** | 1,270 | 19.29% | -27.56% | 1.74 | 0.670 |
| 15m | 1,284 | 19.95% | -25.96% | 1.73 | 0.648 |
| **18m** | 1,255 | **24.68%** | -34.46% | **1.97** | **0.783** |
| 24m | 1,285 | 16.42% | **-20.72%** | 1.60 | 0.559 |

18m dominates the single-window comparison on all three of CAGR, profit factor
and Sharpe. The response curve is non-monotonic (3m good, 6m mediocre, 9m good,
12m mediocre, 18m great, 24m bad) — the shape of noise, not of a structural
effect, and reason enough on its own not to trust it before block validation.

## A methodological confound found in block 0, and why it matters

Block 0 (2007-01-01..2008-03-23) is **structurally unfair to long lookbacks**.
The dataset itself begins 2007-01-01, so a trailing-N-day return has no prior
history to anchor against:

| Lookback | 63d | 126d | 189d | 252d | 315d | 378d | 504d |
|---|---|---|---|---|---|---|---|
| Trades in block 0 | 176 | 130 | 92 | 62 | **0** | **0** | **0** |

15m/18m/24m produce **literally zero trades** and fail block 0 automatically
(`pf > 1.0` cannot hold with no trades). This is not a property of those
lookbacks — it is the dataset's left edge. Any lookback sweep validated on
blocks anchored at the start of available data inherits this bias, and it is
worth recording for future sweeps of any lookback-like parameter.

3m's apparent headline advantage — the only arm at **15/16 blocks** — comes
almost entirely from this. Excluding block 0, 3m and 12m both pass **14/15**.

## 16-block validation — all seven arms

Pass rule imported directly from `analysis/validation.py:_block_passes()`:

```python
dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
```

**All 16 blocks:**

| | 3m | 6m | 9m | **12m** | 15m | 18m | 24m |
|---|---|---|---|---|---|---|---|
| Blocks passing | 15/16 | 14/16 | 14/16 | **14/16** | 14/16 | 14/16 | 14/16 |
| Blocks won | 5 | 2 | 1 | **4** | 2 | **0** | 2 |
| Mean CAGR | 13.91% | 12.69% | 13.36% | **12.95%** | 12.87% | 13.07% | 12.68% |
| Median CAGR | 12.25% | 10.59% | 12.96% | **15.25%** | 13.68% | 14.00% | 14.26% |
| Mean PF | 1.858 | 1.693 | 1.751 | **1.779** | 1.641 | 1.674 | 1.678 |
| Mean Sharpe | 0.630 | 0.547 | 0.536 | **0.539** | 0.549 | **0.509** | 0.617 |
| Worst block CAGR | -8.55% | -14.49% | -17.39% | **-20.61%** | -18.54% | **-24.64%** | -12.13% |

**Excluding the block-0 confound (blocks 1–15):**

| | 3m | 6m | 9m | **12m** | 15m | 18m | 24m |
|---|---|---|---|---|---|---|---|
| Blocks passing (of 15) | 14 | 13 | 14 | **14** | 14 | 14 | 14 |
| Blocks won (of 15) | 5 | 1 | 1 | **4** | 2 | **0** | 2 |
| Mean CAGR | 14.58% | 13.10% | **15.34%** | **15.19%** | 13.72% | 13.94% | 13.52% |
| Median CAGR | 12.47% | 10.88% | 13.46% | **15.37%** | 13.71% | 15.48% | 14.30% |
| Mean PF | **1.905** | 1.721 | 1.829 | **1.857** | 1.750 | 1.786 | 1.790 |
| Mean Sharpe | **0.659** | 0.561 | 0.626 | **0.633** | 0.585 | 0.542 | **0.658** |
| Worst block CAGR | **-8.55%** | -14.49% | -17.39% | -20.26% | -18.54% | -24.64% | -12.13% |

**Head-to-head vs 12m (blocks 1–15), CAGR wins:**

| 3m | 6m | 9m | 15m | 18m | 24m |
|---|---|---|---|---|---|
| 5/15 | 6/15 | 7/15 | 7/15 | 7/15 | 7/15 |

**No alternative beats 12m in more than 7 of 15 blocks.** Every single one
loses its head-to-head against the incumbent.

### 18m: a textbook single-window false positive

The most spectacular single-window result in the sweep — best CAGR (24.68% vs
19.29%), best PF (1.97), best Sharpe (0.783) — **wins 0 of 16 blocks**, posts
the **worst mean Sharpe of all seven arms** (0.509), and the **worst
worst-block CAGR** (-24.64%). Its mean block CAGR (13.07%) is a rounding error
away from 12m's (12.95%), and it beats 12m in exactly 7/15 blocks — a coin flip.

Structurally identical to the ADX<20 and 5-day-cooldown false positives, and to
the risk-adjusted-ranking result earlier tonight. Three investigations in one
night have now produced a clean single-window "win" that block validation
erased. That is the check earning its cost, repeatedly.

## Downstream effects on candidate selection — real and substantive

Because this is the core signal, headline CAGR alone would have missed all of
the following. Measured over the test period, live universe:

| Lookback | Momentum computable | Signal on | **Floor blocks (% of signal-on)** | **Distinct tickers traded** |
|---|---|---|---|---|
| 3m | 99.9% | 58.2% | **9.72%** | **292** |
| 6m | 99.8% | 60.8% | 6.73% | 244 |
| 9m | 99.6% | 62.0% | 5.28% | 234 |
| **12m** | 99.4% | 63.2% | **4.37%** | **210** |
| 15m | 99.2% | 64.9% | 3.90% | 193 |
| 18m | 99.1% | 66.0% | 3.41% | 175 |
| 24m | 98.7% | 68.9% | **2.96%** | **165** |

**1. The `min_momentum_pct: 0.02` floor is not lookback-neutral — it is 3.3×
stronger at 3m than at 24m** (9.72% vs 2.96% of signal-on candidate-days
blocked). Shortening the lookback compresses momentum magnitudes, silently
turning an already-tuned parameter into a far more aggressive filter. Any
future lookback change would need `min_momentum_pct` re-swept alongside it;
they are not independent.

**2. Momentum-strength distribution shifts massively** (% of signal-on days):

| Lookback | 0-2% | 2-10% | 10-25% | 25-50% | 50-100% | >100% |
|---|---|---|---|---|---|---|
| 3m | 9.7% | 36.9% | 38.5% | 12.1% | 2.4% | 0.5% |
| **12m** | 4.4% | 17.6% | 28.7% | 27.7% | 16.3% | 5.4% |
| 24m | 3.0% | 12.0% | 20.5% | 26.6% | 24.4% | 13.5% |

Only 15% of 3m signal-on days exceed 25% momentum, against 64.5% at 24m. The
same 2% floor means something entirely different at each end.

**3. Longer lookbacks concentrate the portfolio into fewer names** — 292
distinct tickers traded at 3m vs 165 at 24m, a 44% reduction, monotonic across
the whole sweep. This interacts directly with tonight's concentration-risk
investigation: any move to a longer lookback would *worsen* the correlated-
concentration exposure that investigation documented, and any move shorter
would dilute it. Neither effect is visible in a CAGR number.

**4. `SIGNAL_OFF` is nearly a dead exit path at 12m and beyond** — 7.5% of
exits at 3m, 0.8% at 12m, 0.3% at 24m. At the current setting the third rung
of the exit priority chain (`CRISIS_EXIT → STOP_LOSS → SIGNAL_OFF →
MAX_HOLDING`) almost never fires; exits are essentially `MAX_HOLDING` (57.8%)
and `STOP_LOSS` (37.6%). Not a defect — but worth knowing that the momentum
signal's *exit* role is close to vestigial today, and that a shorter lookback
is the only tested way to make it meaningfully active.

**5. Trade frequency is remarkably stable** (1,243–1,329, ±3%). The lookback
changes *which* tickers are selected far more than *how many* trades occur.

## Recommendation

**Researched, not adopted — 12 months confirmed.** No production change.

Nothing clears the bar. 18m's spectacular single-window result collapses to
0/16 blocks won and the worst Sharpe in the sweep. 3m's 15/16 is a block-0
data-availability artifact that disappears on exclusion. No alternative wins
more than 7 of 15 head-to-head blocks against 12m, and 12m holds the best or
near-best median block CAGR (15.25% / 15.37%) of any arm.

The genuinely useful output is not the ranking but the four downstream findings
— particularly that `min_momentum_pct` is 3.3× more binding at 3m than 24m, and
that lookback length monotonically drives portfolio concentration. Both mean a
future lookback change could never be a one-line constant edit; it would require
re-sweeping `min_momentum_pct` and re-examining concentration exposure together.

## Preserved output

- `lookback_worker.py`, `self_check.py`, `downstream_analysis.py`
- `results/reset_{3,6,9,12,15,18,24}m.json` — full trade lists per arm
- `results/blocks_{...}m.json` + `_trades.json` — per-block metrics and trades
- `results/downstream.json` — composition/floor-binding/band-mix measurements
