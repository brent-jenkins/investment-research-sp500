# Stop-loss reconfiguration study — pre-registration (2026-08-07)

**Approved for Arms A and B only, both pools (4 runs) — running now.** C/D/E are deferred pending
review of A/B's results, per explicit instruction: "run A and B on both pools first... and stop
there for my read." Approvals recorded:
- Pass/fail criterion (§1): approved as stated, unchanged house standard, trade-off table not a
  declared winner.
- Pool 1/Pool 2 definitions (§2): approved, with the Pool 1 survivorship-bias caveat now made
  explicit (added below).
- Trailing-stop design (§3a): approved as proposed (entry-fixed ATR, close-based anchor) — **not
  needed for this A/B run**, retained here for when C/D is approved. Rationale confirmed independently:
  a high-based anchor combined with the intraday-touch fill model would let a stop be breached by the
  same intraday move that set it (a look-ahead-shaped artifact); a daily-recomputed ATR would widen
  the stop exactly when volatility spikes, confounding attribution. Fixed ATR + close anchor avoids
  both.
- Arm E: dropped for now, to be re-specified (if at all) once A/B settle whether stops help at all.
- Sanity gate: kept hard — Arm A / Pool 2 must land at/near 9/16 or this study stops and reports
  before running anything else.

## 0. Environment check (done, before writing this)

Local DB copy refreshed from the just-merged `data-quality` work (`ticker_data_quality` table
present, 116 CLEAN / 162 QUARANTINED / 130 NEEDS_REVIEW, matching the merged commit exactly).
Code is current — `data-quality` merged into `main` locally (commit `de7f167`), nothing pushed.
`database/investment_research.db` confirmed gitignored.

## 1. Pass/fail criterion — unchanged from house standard, not invented for this study

Per-block: `PF > 1.0 AND abs(strategy_max_drawdown_pct) <= abs(sp500_max_drawdown_pct) * 1.5`
(`analysis/validation.py:_block_passes()`), applied via `compute_metrics_fixed()`/`make_blocks()`/
`_cagr_dd_for_series()` — the exact same formula and functions every other block-validation in this
project uses, not a new criterion. Reported as blocks-passed / 16.

**This is the primary criterion. No arm is declared "the winner" on CAGR alone** — CAGR, max
drawdown (peak-relative), ending balance, and block-pass count are all reported side by side per
arm per pool; the trade-off is presented for you to weigh, per your own explicit instruction.

**Baseline sanity check (must pass before any other arm is trusted):** Arm A / Pool 2 (the
closest match to this project's own most recent baseline) must land at or near **9/16** — the
project's long-documented realistic-stop-fill baseline. If it doesn't, I stop and report the
discrepancy before running anything else, rather than let an unexplained baseline propagate the
way the last two contaminated results did.

## 2. Universe — two pools, both defined precisely, both run against every arm

- **Pool 1**: `universe.get_live_stock_candidates(as_of=date(2026, 8, 7))` — the pre-remediation,
  survivorship-biased live-candidate pool (**503 tickers**, confirmed today). This is the exact
  mechanism `research/vix_ma_giveback_2026-08-07/`'s own baseline used; that run's headline
  "388-ticker" figure is the count of tickers that actually *traded* out of this pool over the full
  backtest, not a separate, smaller input pool — Pool 1 here is the full 503-ticker candidate list,
  matching that precedent's actual mechanism rather than its output statistic. Zero confirmed
  corruption (no ticker reachable via this path was ever in the recycled-symbol investigation's
  flagged population).

  **What Pool 1 actually is, stated precisely so it isn't misread later: today's real S&P 500
  membership, projected backwards across the full 2007–2026 window.** Every ticker in it survived
  to 2026-08-07 by construction — a company that was a genuine, tradeable index member for years but
  had since been delisted, acquired, or gone bankrupt cannot appear in Pool 1 at all, for any date,
  even the years it was real. This is **maximal** survivorship bias, not a mild version of it. It is
  an acceptable comparator for *this specific study* because survivorship bias inflates the whole
  population's realized returns roughly uniformly — it doesn't have a mechanism to favour one stop
  configuration over another, so the *between-arm ranking* within Pool 1 should still be informative.
  But it means **Pool 1's absolute numbers (CAGR, ending balance, total P&L) are not comparable to
  anything historical** — not to Pool 2, not to any other backtest in this project's history, not to
  a real achievable return. Only the relative ordering of arms *within* Pool 1 carries information;
  the absolute figures are reported for completeness, not as a standalone claim about what the
  strategy would have returned.
- **Pool 2**: `universe.get_research_stock_candidates()` — the post-Phase-2 quarantine-excluded
  expanded pool (**1,043 tickers**, confirmed today: 116 CLEAN + 130 NEEDS_REVIEW admitted, 162
  QUARANTINED excluded). **Labelled provisional on every figure it produces** — contiguity analysis
  found ~47% of confirmed-corrupt cases leave no detectable trace, so some unknown fraction of the
  130 NEEDS_REVIEW tickers may still be corrupted data this pool cannot currently screen out.
- `universe.get_all_known_stock_tickers()` (unrestricted, no quarantine applied) is **not used
  anywhere** in this study, per instruction.
- **If the two pools disagree materially about which stop configuration wins** (a different arm's
  ranking, not just a magnitude shift), that disagreement is reported as a first-class finding — it
  is itself evidence about how much residual contamination is still distorting Pool 2, exactly per
  your framing. Not buried under a single blended verdict.

## 3. Arms — single variable (stop configuration), everything else held constant

All arms use `simulate_portfolio_compounding_mtm()` with the realistic intraday-touch fill model
(permanent default since 2026-08-06), `max_total_deployed` **held fixed** at the current production
value (0.70) across every arm — no cap variation bundled into this comparison.

| Arm | Mechanism | Implementation |
|---|---|---|
| **A — Baseline** | Current production: fixed 2.0x ATR stop, set once at entry, never moves. | No code change — `config["backtest"]["atr_stop_multiplier"]` already defaults to 2.0. Run as-is. |
| **B — No stop** | Exits via `CRISIS_EXIT` → `SIGNAL_OFF` → `MAX_HOLDING` only; `STOP_LOSS` never fires. | Reuses the exact, already-proven mechanism from `research/no_stop_loss_2026-08-06/` (`disable_stop_loss=True` passes `stop_price=None` into `evaluate_exit_reason()`) — not reimplemented. |
| **C1 — Wide 3.0x** | Fixed ATR stop at entry, 3.0x instead of 2.0x. | `config["backtest"]["atr_stop_multiplier"] = 3.0`. No code change. |
| **C2 — Wide 4.0x** | Same, 4.0x. | `atr_stop_multiplier = 4.0`. |
| **D1 — Trailing 2.0x** | Stop starts at entry −2.0×ATR, then ratchets up (never down) as price makes new highs since entry. | New, additive, temporary parameter on `simulate_portfolio_compounding_mtm()` (see 3a below); reverted after data collection, same pattern as Arm B's precedent. |
| **D2 — Trailing 3.0x** | Same mechanism, 3.0x. | |
| **E — Volatility-conditional (if time permits, run last)** | Stop active only when `ATR/price` at entry exceeds the cross-sectional median for that day's admitted candidates; otherwise no stop (falls through to B's logic for that position). | New, additive; **lowest priority — will be skipped without asking again if A–D alone exceed a reasonable time budget, and reported explicitly as skipped, not silently dropped.** |

### 3a. Trailing-stop mechanics — two specific design choices needing your sign-off, not assumed

`evaluate_exit_reason()` itself is untouched — same priority chain, same function, every arm. Only
the `stop_price` **value** fed into it changes over an open position's life for arms D1/D2:

1. **ATR is fixed at entry, not recomputed daily.** Only the anchor *price* the stop trails ratchets
   up; the ATR distance multiplied against it stays the entry-day ATR throughout the hold. Avoids
   introducing a second new variable (daily ATR recomputation) into an already-new mechanism.
2. **Trailing anchor is the daily CLOSE, not the daily HIGH.** `running_high_close = max(close since
   entry)`; `candidate_stop = running_high_close − atr_mult × atr_at_entry`;
   `ot["stop_price"] = max(ot["stop_price"], candidate_stop)` — recomputed once per open position per
   day, before that day's exit check. Chosen for consistency with the rest of this engine (which is
   close-based everywhere except the `STOP_LOSS` trigger check itself, which deliberately uses intraday
   `low` for realism) — happy to switch to `high` if you'd rather the trail follow the intraday peak
   instead; flagging the choice rather than assuming it.

## 4. Cost modelling — per-arm, not deferred (this project's own 2026-08-06 standing rule applies directly)

Turnover differs materially by construction across these arms (that's the entire point of testing
B/D against A), so FX cost and idle-cash credit are **treatment effects**, not constants safe to
defer. Reused exactly, not re-derived: `research/deployment_cap_sweep_2026-08-06/cost_adjustment.py`'s
template —

- **FX**: Σ(each arm's own real `position_gbp`) × **0.30%** round-trip (T212's confirmed rate).
- **Idle-cash credit**: mean idle-cash % (from a per-arm daily deployment panel, instrumented the
  same way `deployment_and_risk.py` did for the cap sweep) × mean **^IRX** (13-week T-bill) rate over
  2007-01-01 → 2026-08-06.
- Netted against each arm's own reported CAGR, both **frictionless and cost-adjusted block-pass
  counts reported side by side**, matching this project's established dual-reporting convention —
  never only the frictionless number.

## 5. Two backtest conventions — both reported, explicitly labelled, per this project's own standing requirement

- **Reset-at-boundary**: the standard 16-block validation itself (`compute_metrics_fixed()` computes
  each block's metrics independently against `starting_capital_gbp`) — this is the primary
  variant-comparison mechanism, used for the pass/fail criterion above.
- **Continuous walk**: one uninterrupted 2007→2026 compounding run per arm per pool, reporting CAGR,
  ending balance, and peak-relative max drawdown — the "what would one real account actually have
  shown" view, reported alongside, never conflated with the block-by-block comparison.

## 6. Capacity-turnover reporting — separate from the direct effect, per the 2026-08-06 standing requirement

Per arm per pool: total trade count, and (matching `no_stop_loss_2026-08-06`'s own precedent
directly) which candidates got admitted under one arm but rejected-for-capacity under another,
plus those foregone candidates' mean/median forward return — so a longer-holding arm's lower trade
count is explained, not just reported as a smaller number.

## 7. Additional reporting

- Per arm per pool: CAGR (both conventions), ending balance, max DD (peak-relative), block-pass /16
  (frictionless and cost-adjusted), total trades, exits by reason, mean/median holding days.
- **Baseline arm (A) only, both pools**: of all `STOP_LOSS` exits, how many saw the *same ticker*
  re-entered within 30 calendar days, and the aggregate P&L of those round-trip pairs — the direct
  whipsaw-hypothesis quantification requested.
- Any arm producing an outlier single-trade P&L gets its top trades checked against the recycled-
  ticker quarantine list and raw price data **before** being reported, not after — the discipline
  this project's own £2.21M anomaly forced into being.

## 8. Explicitly out of scope, unchanged from instruction

`analysis/signals.py`, the universe/membership mechanism, the crisis breaker, the earnings-dampener
research, and the quarantine work are not touched. `config.yaml` and every production file are
read-only throughout — any arm's parameters are passed as function arguments / temporary in-memory
config overrides in a research script, never written back. `broker_side_stop_enabled` and
`place_stop_order()`: if an arm without the current fixed ATR stop wins, this is flagged as a
required production follow-up in the final report, **not implemented**.

## 9. Estimated runtime, stated plainly before starting

7 arms × 2 pools = 14 backtest runs. Pool 2 (~1,000 usable tickers) took ~750s per run during the
just-completed remediation; Pool 1 (503 raw candidates, likely ~350-450 usable) should be somewhat
faster. Rough estimate: **2.5-3.5 hours of backtest wall-clock time**, run in parallel where the
local machine's 4 physical cores allow, plus cost-adjustment/block-validation passes (fast, seconds
each). Arm E, if included, adds roughly another 25 minutes at the low end. Flagging this so the
scope is clear before committing to it — happy to cut Arm E, or run only one pool first and the
second on confirmation, if you'd rather bound the time spent before seeing Arm A/B's first results.

---

**Awaiting explicit approval before running anything**, per instruction. Specifically:
1. The pass/fail criterion and dual-convention reporting as stated.
2. The two pools and their exact definitions (Pool 1 = 503, Pool 2 = 1,043).
3. The arm list and the two trailing-stop design choices in §3a (entry-fixed ATR, close-based trail).
4. Whether to include Arm E now, defer it, or drop it.
5. Whether to run all 14 combinations up front, or Pool 1 + Pool 2 for Arms A/B first (fastest path
   to the highest-value "does a stop help at all" question) before committing to the full C/D sweep.
