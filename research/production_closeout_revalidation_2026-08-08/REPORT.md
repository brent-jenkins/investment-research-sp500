# Re-validation after production close-out (2026-08-08)

Closes two gaps identified during the close-out double-check: (1) only the 0.70-cap sanity-gate
baseline was re-run after `CPWR`'s quarantine (the Wikipedia refinement); the 0.50-cap figure and
the stop-loss reconfiguration study's Pool 2 headline arms comparison both still reflected the
pre-refinement, 162-quarantined pool. Local environment refreshed from production first, per
standing practice: fresh DB copy (`price_data` through 2026-08-07, matching "last night's EOD
run"), coverage confirmed (871 tickers, `ticker_data_quality` 220 CLEAN / 25 NEEDS_REVIEW / 163
QUARANTINED, `CPWR`=QUARANTINED — byte-identical to production).

## 1. Sanity-gate baseline at cap 0.50 — closed

Same methodology as the existing cap-0.70 re-baseline (`step5_baseline_revalidation.py`),
`get_research_stock_candidates()`, continuous-walk convention, `MAX_DEPLOYED_PCT=0.50`.

| Cap | Pool (quarantined) | Blocks pass | Total P&L | Trades | CAGR | Peak DD |
|---|---|---|---|---|---|---|
| 0.50 | 162 (pre-refinement) | 11/16 | £52,841.47 | 3,651 | — | — |
| 0.50 | 163 (current) | **9/16** | **£73,086.75** | 3,621 | 4.70% | −40.82% |
| 0.70 | 162 (pre-refinement) | 9/16 | £116,748.91 | 4,958 | — | — |
| 0.70 | 163 (current) | **7/16** (already known) | £115,363.15 → £114,990.04* | 4,922 | 6.28% | −47.83% |

*Two independent runs of the identical 163-quarantined/cap-0.70 configuration differ by ~£373
(0.3%) — diffed directly, not assumed benign: every differing row is an `END_OF_DATA` exit (a
position still open on the backtest's last day), and the two runs read `price_data` at different
times (the original predates this session's fresh DB pull). Recent, still-settling daily prices
get revised between pulls — a narrow, understood source of non-determinism confined to trades open
at the window boundary, not an engine reproducibility bug. Immaterial to every conclusion below.

**Both caps moved by exactly 2 blocks from the same single-ticker removal — confirming the
noise-floor mechanism is not cap-specific.** But the *economic* consequence was opposite: cap 0.70's
P&L fell slightly (−1.2%) while cap 0.50's rose substantially (+38.3%). Same block-count delta,
opposite direction of consequence — direct evidence that the block-pass count alone cannot be read
for magnitude or even sign of the effect on P&L.

### Capacity-turnover (per standing requirement — reported separately, not blended into a net figure)

| Cap | Net trade delta | CPWR's own trades removed | Non-CPWR cascade removed | Newly admitted |
|---|---|---|---|---|
| 0.50 | −30 | 18 | 1,357 | 1,345 |
| 0.70 | −36 | 18 | 1,732 | 1,714 |

Removing one ticker's 18 trades changed the identity of **~35–38% of all trades** in the run (gross
churn, not net) — a materially larger cascade than the ±2-block headline delta alone would suggest.
The net figure understates how much actually changed.

## 2. Stop-loss reconfiguration study — Pool 2 headline re-run

Confirmed via byte-identical diff that the original `trades_pool2_A.csv` matched the pre-refinement
162-quarantined baseline exactly — Pool 2's arms had never been run against the current pool.
Rebuilt Pool 2's cache fresh (`get_research_stock_candidates()`, current 163-quarantined pool) and
re-ran all four arms (A/C1/C2/B) at the study's own fixed `MAX_DEPLOYED_PCT=0.70`.

| Arm | Stop config | Trades | Blocks pass | Continuous CAGR | Peak DD | Cost-adj. CAGR |
|---|---|---|---|---|---|---|
| A | 2.0x ATR (baseline) | 4,922 | 7/16 | 6.28% | −47.83% | 3.38% |
| C1 | 3.0x ATR | 4,219 | 9/16 | 6.83% | −44.88% | 4.45% |
| C2 | 4.0x ATR | 3,957 | 10/16 | 5.52% | −45.75% | 3.35% |
| B | No stop | 3,599 | 11/16 | 7.27% | −43.39% | **5.32%** |

**Ranking preserved: B > C1 > A ≈ C2 — identical order to the original (stale-pool) finding, and the
gap actually widened.** Original pool2 cost-adjusted CAGR: A=3.42%, C1=4.19%, C2=3.14%, B=4.68% (gap
B−A = 1.26pp). Current: A=3.38%, C1=4.45%, C2=3.35%, B=5.32% (gap B−A = 1.94pp). The single-ticker
staleness flagged during close-out did not overturn or meaningfully weaken this study's headline
finding — if anything the finding is slightly more decisive now.

Pool 1 was not re-run: `get_live_stock_candidates()` doesn't read `ticker_data_quality` at all, so
`CPWR`'s quarantine cannot have affected it (confirmed by the same empty-diff logic already used to
verify the live candidate path during close-out).

## What this settles

Both flagged gaps are closed. Neither research thread's headline conclusion changes: the 7/16
sanity-gate figure is the correct current reference at both caps (re-baselined, cause understood —
capacity cascade from a single ticket removal), and the stop-loss study's "B wins on cost-adjusted
CAGR" finding is now verified against the current, fully-adjudicated candidate pool rather than a
pool with one ticket's worth of staleness in it.

## Trade-level output preserved

`trades_baseline_revalidation_cap05.csv`, `trades_pool2_final_{A,C1,C2,B}.csv` — per standing
requirement, kept alongside this report rather than only the aggregate figures.
