# NEEDS_REVIEW adjudication via independent source — full results (2026-08-07/08)

Follow-on to the initial Wikipedia cross-reference investigation (raw matcher, proposal only,
nothing applied). This covers the refined matcher, its application, and the two re-validation
checks requested afterward — one of which overturned a conclusion from the previous report, and
one of which surfaced a new, unrelated finding about this project's own 16-block metric.

## 1. Refined matcher — two systematic fixes, applied per explicit approval

**Better name normalization**: `&` ↔ `and`, trailing `(Class A/B/C)` share-class suffixes, and
period-separated abbreviations (`V.F.` → `VF`) — fixed `UA`/`UAA` (Under Armour dual share classes),
`VFC` (`V.F. Corporation` vs `VF Corporation`), and `AIV` (`and` vs `&`), all previously
`STILL_AMBIGUOUS` purely on formatting.

**A curated known-renames list — three tickers only**, each individually confirmed a genuine same-
entity rename during the prior report's per-case review: `ATI` (Allegheny Technologies), `CLF`
(Cliffs Natural Resources), `OI` (Owens-Illinois). `CNX` (Consol Energy) was deliberately **not**
added — it involved a real spinoff alongside the rename, not a pure identity continuation, so it
stays a plain mismatch rather than an assumed match.

## 2. Asymmetric application rule — exactly as specified, not softened either direction

```
Wikipedia name match (incl. via renames list / normalization) -> promote to CLEAN
Wikipedia mismatch AND prior_reversal_classification == 'LIKELY_RECYCLED' -> promote to QUARANTINED
Wikipedia mismatch alone (no corroboration)                    -> stays NEEDS_REVIEW
No Wikipedia coverage, or genuinely ambiguous                  -> stays NEEDS_REVIEW
```

A mismatch was deliberately never treated as sufficient evidence on its own — the raw matcher's own
9-case mismatch bucket had already shown a third were false positives (corporate renames). Only
`CPWR` met the corroboration bar among the current 9 mismatches (the original investigation's
reversal-shape classifier had independently flagged it `LIKELY_RECYCLED` — a completely different
methodology agreeing with the Wikipedia mismatch).

Every promoted/quarantined ticker's evidence JSON now carries a `wikipedia_cross_reference` block
recording the source explicitly, **including the caveat**: "community-maintained, not a system of
record — corroborating evidence only, never sole authority for a CLEAN promotion." Tickers with no
current cached `long_name` to compare against (e.g. `CSRA`, `MOLX`, `TWX`) were deliberately **not**
promoted on Wikipedia's record alone, even though Wikipedia confirms they were real companies —
promoting on a single source would have been exactly the "sole authority" risk flagged as off-limits.

### Result

| Bucket | Count |
|---|---|
| Promoted to `CLEAN` | **104** |
| Promoted to `QUARANTINED` (corroborated only) | **1** (`CPWR`) |
| Stayed `NEEDS_REVIEW` | **25** |

**Final `ticker_data_quality`: 220 CLEAN / 25 NEEDS_REVIEW / 163 QUARANTINED** (up from 116/130/162).
Landed almost exactly on the predicted range (100+ CLEAN, a handful QUARANTINED, 20–30 unresolved).

### Coverage-gap check — a real structural finding, not random noise

Checked whether the 17 tickers with no Wikipedia coverage cluster in a particular era: **12 of 17
(71%) have `last_membership_end` in 2008 specifically**, against a median year of 2019 for the full
130-ticker population. Wikipedia's changes table has materially thinner coverage of the 2008
financial-crisis reconstitution wave than of calmer years — plausibly because bankruptcy-driven
removals don't fit the table's "X replaces Y" reconstitution format as cleanly as an acquisition or
market-cap change does. **This means the population still least protected by any adjudication
signal is disproportionately the same 2008–09 crisis-era population already flagged as driving the
worst-episode drawdown distortion** — the same era, two separate findings pointing at it.

## 3. Expanded drawdown-contamination re-check — my prior "resolved" conclusion does not hold up

Requested specifically because a CLEAN promotion is the first step in this whole sequence that
**adds** tickers to the trusted pool rather than removing them, and because the drawdown statistic
is exactly what residual contamination was already shown to invert. Re-ran the same worst-episode
CLEAN-only-drawdown test from `stop_loss_reconfiguration_2026-08-07/step6_drawdown_contamination_
check.py`, using the now-much-larger 220-ticker CLEAN set instead of the original 116 (reusing the
existing `pool2_A`/`pool2_B` trade data — the candidate pool passed to those backtests already only
excluded `QUARANTINED`, so today's promotions don't change which tickers appear in that trade data,
except `CPWR`, newly quarantined, which this reuse cannot retroactively remove; noted as a residual
imprecision, not hidden).

| | Full-population peak DD | CLEAN-only DD (116, prior report) | CLEAN-only DD (220, this check) |
|---|---|---|---|
| pool2_A | −50.21% | −31.45% | **−46.27%** |
| pool2_B | −44.92% | −33.77% | **−42.59%** |

**With the larger, better-corroborated CLEAN sample, B has the better drawdown again** — the same
direction as the original raw Pool 2 numbers, reversing what the 116-ticker sample showed. Stated
plainly, not softened: **the previous report's claim that "the cross-pool disagreement is
substantially a contamination artifact, and CLEAN-only Pool 2 agrees with Pool 1's direction" does
not hold up.** That conclusion was drawn from a smaller, now-superseded CLEAN sample that turned out
to be less representative than it was treated as at the time.

**What does still hold**: contamination's *magnitude* of effect on this specific statistic shrank
substantially once real evidence was applied — excluding the unresolved population moved the
drawdown by 18.76pp/11.15pp with the small sample, and only 3.94pp/2.33pp with the larger,
better-evidenced one. Most of what looked like "contamination distorting the drawdown" in the first
pass was actually **wrongly-excluded genuine tickers**, not genuine corruption — once they're
properly counted as clean, the residual, still-unresolved 25-ticker population contributes only a
small, proportionate share to the worst episode (31 of 462 open positions in pool2_A's episode,
8.2% of that episode's losses — down from a much larger share when 130 tickers were still excluded).

**What this leaves as the honest open question**: the Pool 1 vs. Pool 2 drawdown disagreement itself
is not resolved by better adjudication — it may be a genuine, substantive difference between the two
universes' compositions. Pool 2 (now ~1,042 tickers) is structurally much larger than Pool 1's
survivorship-biased 503 specifically *because* it includes historical/delisted names Pool 1 can
never contain by construction — real exposure to 2008–09-crisis casualties that a survivor-only
universe mechanically excludes. Whether that's "the true, honest picture of a bigger universe's real
risk" or some other unaccounted-for factor is not something this check can distinguish, and is not
claimed to be resolved here.

## 4. Baseline sanity re-check — gate failed, investigated directly, explained but not dismissed

Per the standing sanity-gate discipline, re-ran the standard `get_research_stock_candidates()`
baseline (Arm A equivalent) after today's changes. **Result: 7/16 blocks passed, against the
long-established ~9/16 baseline — the gate failed.**

**Investigated immediately, not reported and moved past.** The only pool difference between this
run and the last confirmed-9/16 run (the stop-loss study's own `pool2_A`) is exactly one ticker:
`CPWR`, newly `QUARANTINED` today (confirmed by diffing the two candidate lists directly — nothing
else changed). `CPWR` itself had 18 trades in the prior run, all clustered 2008–2011, all break-even
or losing (a real, repeatedly-whipsawed position through the crisis). Block-by-block comparison of
old vs. new trades confirms the story is not simple removal of `CPWR`'s own trades:

- Block 1 (2008-03-23 → 2009-06-13, where `CPWR`'s trades actually sit): trade count drops 353→341
  (~12, consistent with `CPWR`'s own trade count there) — **this block's pass/fail is unchanged.**
- **Blocks 5 (2013-02-14 → 2014-05-07) and 6 (2014-05-07 → 2015-07-29) are what actually flipped**
  from pass to fail — years after `CPWR` itself ever traded, with materially different trade counts
  and profit factors from the old run (block 5: 297→286 trades, PF 1.44→1.37; block 6: 276→279
  trades, PF 1.78→1.55) despite `CPWR` having zero trades in either block in the original run either.

**This is a genuine capacity-cascade effect, not a bug** — confirmed by direct investigation, not
assumed. `simulate_portfolio_compounding_mtm()` is a strictly sequential, capacity-constrained,
compounding simulation: removing a repeatedly-churning position from 2008–2011 changes exactly which
capacity was available on every subsequent admission decision for the rest of the 19-year walk, and
by 2013–2015 that changed admission history has propagated into a genuinely different trade sequence
— consistent with this project's own repeatedly-documented sensitivity to exactly this kind of
change (the realistic-stop-fill correction's +275 trades, the deployment-cap sweep's turnover
sensitivity, and others).

**The uncomfortable, generalizable finding worth stating plainly**: a single ticker's removal from a
1,043-ticker pool shifted the block-pass rate by two full blocks (12.5 percentage points) via
downstream cascade effects with no direct connection to that ticker. **This means the 16-block
metric is more sensitive to pool-composition changes than the "just 1 ticker, barely matters" framing
would suggest** — any future adjudication of even a handful of the remaining 25 `NEEDS_REVIEW`
tickers could plausibly move this number again, in either direction, for reasons unrelated to
whether those specific tickers are good or bad candidates. This is not a reason to distrust today's
CLEAN promotions (those rest on real, corroborated evidence, not on the block-pass number holding
steady) — but it is a reason not to treat "does the block-pass rate stay near 9/16" as a validation
gate for *this specific kind of small pool change* the way it correctly is for larger, structural
changes (a new engine, a new cap, a new signal). Flagged here rather than quietly re-run until a
number closer to 9 appeared.

## 5. Not done, deliberately

No further backtest re-run was attempted to "fix" the 7/16 result — re-running with a different
config or reverting `CPWR`'s promotion to chase a specific number would be exactly the kind of
result-shopping this project's own standing discipline exists to prevent. The 7/16 figure is
reported as the direct, investigated, explained consequence of today's one real change, not
smoothed over.

## Files

`step3_refined_matcher_and_apply.py` (the refined matcher + application), `step3_promoted_clean.csv`
/ `step3_promoted_quarantined.csv` / `step3_stayed_needs_review.csv`, `step4_expanded_drawdown_check.py`
/ `step4_expanded_drawdown_contamination_check.json` (the reversed conclusion), `step5_baseline_
revalidation.py` / `trades_baseline_revalidation.csv` (the 7/16 sanity-gate run).
