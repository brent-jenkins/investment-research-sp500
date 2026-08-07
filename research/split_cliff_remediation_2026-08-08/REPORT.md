# Split-adjustment artefact remediation — Phase 1 classification (2026-08-08)

**Investigation only. No `price_data` rows touched.** Full evidence: `step1_classification_evidence.csv`.

## Methodology

For each of the 14 tickers, found the exact consecutive-trading-day pair where `close` breaks
outside a normal ratio, then checked: (a) does `adj_close` move smoothly (~1.0) across the same
transition while `close` jumps — the classic signature of a correctly-adjusted corporate action
where this project's `close` (raw, `auto_adjust=False`) legitimately shows the real historical
price while `adj_close` has already absorbed it; (b) a matching `yfinance .splits` event near the
date; (c) for the corporate-action candidates, a **fresh pull straight from yfinance today** — not
this project's stored data at all — to test directly whether the cliff is a local, stale-data
artifact (fixable by re-fetching) or baked into yfinance's own current data (not fixable that way).

## Result: 9 genuine market moves, 3 confirmed corporate actions, 1 corporate action of an
unidentified type, 1 needing your read on repair feasibility (not classification)

| Ticker | Date | close ratio | adj_close ratio | adj_close smooth? | Split match? | Classification |
|---|---|---|---|---|---|---|
| AIG | 2008-09-15 | 0.392 | 0.392 | No | No | **GENUINE_MARKET_MOVE** |
| APA | 2020-03-09 | 0.461 | 0.461 | No | No | **GENUINE_MARKET_MOVE** |
| GL | 2024-04-11 | 0.469 | 0.469 | No | No | **GENUINE_MARKET_MOVE** |
| HIG | 2008-10-30 | 0.484 | 0.484 | No | No | **GENUINE_MARKET_MOVE** |
| HIG | 2008-12-05 | 2.024 | 2.024 | No | No | **GENUINE_MARKET_MOVE** |
| OXY | 2020-03-09 | 0.466 | 0.480 | No | No | **GENUINE_MARKET_MOVE** |
| PCG | 2019-01-14 | 0.476 | 0.476 | No | No | **GENUINE_MARKET_MOVE** |
| STT | 2009-01-20 | 0.410 | 0.410 | No | No | **GENUINE_MARKET_MOVE** |
| TRGP | 2020-03-09 | 0.471 | 0.471 | No | No | **GENUINE_MARKET_MOVE** |
| KDP | 2018-07-10 | 0.179 | 1.115 | **Yes** | No | **CORPORATE_ACTION** — merger |
| MO | 2008-03-31 | 0.301 | 0.975 | **Yes** | No | **CORPORATE_ACTION** — spinoff |
| WY | 2010-07-20 | 0.381 | 1.034 | **Yes** | No | **CORPORATE_ACTION** — special dividend |
| EXPE | 2011-12-21 | 0.488 | 0.664 | No (partial) | Yes (0.5, same day) | **CORPORATE_ACTION** — spinoff + split, correctly reflected in `close` |
| JCI | 2007-07-02 | 0.395 | 0.466 | No (partial) | Yes (0.25, same day) | **CORPORATE_ACTION, mechanism unidentified** |

## Nine genuine market moves — matches your priors exactly

`AIG` (Lehman weekend), `APA`/`OXY`/`TRGP` (all 2020-03-09, the oil price war), `PCG` (bankruptcy
filing), `HIG`×2 and `STT` (2008-09 banking crisis) all show `close` and `adj_close` moving in
**lockstep** (ratios equal or within rounding), with no split anywhere near the date. `adj_close`
tracking `close` exactly is itself the evidence: there's no adjustment event to smooth over — the
price genuinely moved.

**`GL` 2024-04-11 breaks from your stated prior ("likely corporate action") — confirmed genuine
market move, not assumed.** Verified directly: Fuzzy Panda Research published a short report on
2024-04-11 alleging insurance fraud at Globe Life; shares closed down ~53% that session, the lowest
in over a decade. Real, dated, confirmed — not a data artifact. [CNBC](https://www.cnbc.com/2024/04/11/globe-life-shares-plummet-50percent-after-short-seller-accuses-company-of-insurance-fraud.html),
[Hedgeweek](https://www.hedgeweek.com/globe-life-shares-drop-54-following-short-seller-allegations/).

**`HIG` 2008-12-05's 2.024x overnight jump was checked with particular care given the magnitude** —
`adj_close` moves in the same lockstep (2.024 vs 2.024), which is strong evidence against an
adjustment artifact regardless of how extreme the move looks. Hartford Financial had severe,
well-documented volatility through Q4 2008 around capital-raising and bailout-adjacent news; an
overnight relief-rally-scale move is consistent with that period, though the specific catalyst for
this exact date wasn't independently pinned down. Flagging the magnitude explicitly rather than
treating "adj_close agrees" as fully dispositive on its own for a move this large.

## Three clean, confirmed corporate actions — but the planned repair mechanism does not apply to them

`KDP`, `MO`, `WY` all show the textbook signature you described: `adj_close` moves smoothly (0.975–
1.115, i.e. ~1.0) while `close` jumps hard. Real, dated events: KDP's 2018-07-10 Keurig/Dr Pepper
Snapple merger, MO's 2008-03-31 Philip Morris International spinoff, WY's 2010-07-20 REIT-conversion
special dividend — all match your own prior identification.

**Critical finding, checked before assuming repair would work: a fresh pull straight from yfinance
today reproduces the identical cliff for all three.** This is not this project's stale local data —
it is yfinance's own **current** `close` series. Cross-checked against the 2026-08-06 split-
adjustment audit's own finding: that investigation confirmed yfinance *does* retroactively rebase
`close` for ordinary stock splits (296 real splits checked, all showing a smooth, already-adjusted
series) — which is exactly why that incident's own repair mechanism (`_find_retroactive_
restatement()`, full re-fetch on divergence) works for splits. **Mergers, spinoffs, and special
dividends are a different class of event that yfinance's `close` field does not retroactively
smooth, no matter how fresh the pull.** `_find_retroactive_restatement()`'s mechanism was built and
validated against the split case specifically (that audit's own repair scope was empty — no split
artefact was ever found to repair) — it has never been tested against this different failure mode,
because this is the first time this project has looked at merger/spinoff-driven cliffs specifically.

**Practical consequence: the Phase 2 plan as written ("re-fetch that ticker's full history and
replace its price_data rows") will not fix `KDP`, `MO`, or `WY`.** A full re-fetch reproduces the
exact same discontinuity, confirmed directly, not assumed. Fixing these (if fixing is wanted) would
require a genuinely different mechanism this project has never built — a computed price-continuity
splice (scaling the pre-event segment by the real corporate-action ratio so it joins smoothly with
the post-event segment), not a re-fetch. That is a materially bigger, riskier piece of work than the
one-line "replace price_data rows from a fresh pull" Phase 2 described, and needs its own explicit
scoping and approval before any code is written for it — flagging this now rather than discovering
it mid-repair.

## `EXPE` — a genuine corporate action, but `close` may already be correctly reflecting it

Confirmed directly: Expedia completed a 1-for-2 stock split immediately before spinning off
TripAdvisor on 2011-12-20/21, with Expedia's post-spinoff price down ~50% — matching the observed
`close_ratio` of 0.488 closely. [TechCrunch](https://techcrunch.com/2011/12/21/tripadvisor-spins-off-from-expedia-takes-flight-on-the-nasdaq-as-a-public-company/).
Unlike `KDP`/`MO`/`WY`, `adj_close` here is **not** smooth (0.664, not ~1.0) — but that does not
mean `close` is wrong. A combined split-plus-spinoff is a case yfinance's own `adj_close`
methodology handles inconsistently (the spinoff's value transfer isn't a clean multiplicative
adjustment the way a plain split is), and the observed `close_ratio` already closely matches the
real, reported price action. **Best read: `close` is likely already a correct, if unadjusted for
the spinoff's economic effect, record of what actually happened — not an artefact needing repair,
and not cleanly the same as either of your two given categories.** Recommend leaving as-is pending
your view; this is not equivalent to the genuine-market-move bucket (a real corporate action did
happen) but also isn't the `KDP`/`MO`/`WY`-style clean repair candidate.

## `JCI` — a real split event confirmed, but the direction doesn't fit, and I could not identify the actual mechanism

`yfinance.splits` confirms a real event on 2007-07-02 exactly (factor 0.25) — corroborated by an
independent source describing "a 1:4 split." **But under yfinance's own documented convention
(factor = new shares per old share), a factor of 0.25 is a 1-for-4 *reverse* split, which should
raise the price ~4x — the observed `close_ratio` is 0.395, a *drop*, the opposite direction.** I
could not resolve this discrepancy via search — no specific news event, spinoff, or special dividend
for Johnson Controls around this date turned up. What I did confirm directly: a **fresh pull from
yfinance today reproduces the identical cliff** (same as `KDP`/`MO`/`WY`/`EXPE`), which rules out a
stale-local-data explanation and puts this in the same general class as the other confirmed
corporate actions (something yfinance's `close` never smooths) rather than a simple market move or
a project-side ingestion bug. **Reported honestly as unresolved rather than forced into either
bucket** — per your own instruction not to default to genuine just because the mechanism isn't
obvious. If it is a genuine corporate action of this same un-smoothed class, the same "repair
mechanism doesn't apply as planned" finding above would apply to it too.

## What this means for Phase 2, stated plainly before you decide anything

- **9 genuine market moves** (`AIG`, `APA`, `GL`, `HIG`×2, `OXY`, `PCG`, `STT`, `TRGP`): per your
  own Phase 2 plan, record as known-genuine exceptions so `detect_price_cliffs()` stops re-firing on
  them — no data change. Ready to implement as originally scoped.
- **`KDP`, `MO`, `WY`**: confirmed real corporate actions, but **the planned re-fetch repair will not
  work** — a materially different (and bigger) fix would be needed if repair is still wanted, not
  built or scoped here. Your call: leave as a known-genuine (if distorting) exception like the
  9 above, pending a properly-scoped splice-repair design, or treat differently.
- **`EXPE`**: likely already correctly reflecting a real, if complex, corporate action — probably
  belongs with the exception list, not a repair candidate, but flagged for your view rather than
  decided here.
- **`JCI`**: genuinely unresolved. Same "fresh pull reproduces the cliff" pattern as the confirmed
  corporate actions, but the specific mechanism was not identified. Needs either further research
  (SEC filings from the period) or your own judgement before it goes in any bucket.

**Phase 3 (live impact assessment) has not been started** — holding for your direction on the above,
since whether/how anything gets "fixed" changes what Phase 3 would even be measuring. `GL` was
flagged in your brief as the one to check first (recent enough to still sit inside current momentum
windows) — given it's now confirmed a genuine market move, not a data artefact, it's real signal, not
noise, and Phase 3 for it would be about whether that real move produced a real, correct decision
(a SIGNAL_OFF/STOP_LOSS makes sense on real fraud allegations), not about erroneous data.

## Decision — no repairs, recorded exceptions instead (2026-08-08)

Per explicit direction: no price-continuity splice built or attempted. Reasoning, recorded here as
the actual justification, not just the outcome:

1. **A splice would synthesise prices no exchange ever printed and store them in `price_data` as
   observed values, for live S&P 500 members.** Every prior fix this week (quarantine, contiguity,
   the Wikipedia cross-reference) was exclusion or restoration of a real value already established
   elsewhere — never fabrication of a new one. A splice would be the first, on the live path, and a
   splice-factor error would be invisible in exactly the way this incident's own detection wasn't:
   the output would look smooth, which is precisely the signature this project's own detector
   treats as evidence of correctness.
2. **The corrected series already exists and is simply unused.** `adj_close` is smooth across all
   three confirmed corporate actions (`KDP`, `MO`, `WY`) — stored by ingestion, read by nothing
   (`analysis/signals.py`'s own docstring). The continuity this task set out to build isn't missing
   from the data; it's present and unconsumed.
3. **Every affected event is 2007–2018** — confirmed via Phase 3 below to be genuinely, not just
   presumptively, outside every current and historical momentum window this system has ever
   actually evaluated a trade against.

**Phase 2 — recorded, not repaired.** `data/ingestion.py:_KNOWN_PRICE_CLIFF_EXCEPTIONS` (14 entries,
classification + evidence attached to each) and `filter_known_price_cliff_exceptions()`, called from
`data/scheduler.py`'s EOD job strictly after live-scoping and before `send_price_cliff_alert()` —
`detect_price_cliffs()` itself and the live-scoping filter are both byte-for-byte unchanged.
`price_cliff_hits_known_exceptions` recorded alongside the existing `price_cliff_hits`/
`price_cliff_hits_total` fields, matching the project's "discarded noise stays visible" convention.
5 new tests (`tests/test_ingestion.py`, `tests/test_scheduler.py`), all passing.

## Phase 3 — live impact assessment: confirmed benign, not assumed

**Live tables (`invest_orders`, `pending_orders`, `pnl`): zero overlap with any affected window,
confirmed directly.** `invest_orders`/`pnl` have zero rows for any of the 5 tickers at all.
`pending_orders` has 19 rows for `EXPE`/`JCI`/`MO`/`WY` — every one dated 2026-08-02 through
2026-08-06, current live-system activity entirely unrelated to the 2007–2018 affected windows.

**Backtest impact, checked against the standard baseline (`stop_loss_reconfiguration_2026-08-07/
trades_pool2_A.csv`, the full 2007–2026 continuous walk over the comprehensive candidate pool) —
zero trades overlap any affected window for any of the 5 tickers,** checked both ways: no entry
falling inside a window, and no position entered before a window that stayed open into it.

**Momentum distortion during the affected windows was real and large, but never reached a decision
— quantified directly, not inferred.** Computed the actual (uncorrected) `compute_momentum_series()`
output for each ticker's own affected window:

| Ticker | Momentum range during window | Days with positive (would-be-admissible) momentum |
|---|---|---|
| KDP | −0.78 to −0.67 | 0 / 252 |
| MO | −0.81 to −0.26 | 0 / 254 |
| WY | −0.63 to +0.41 | 2 / 254 |
| JCI | −0.72 to −0.25 | 0 / 126 |

For three of the four, momentum was **never** positive during the entire affected window — the
cliff makes the stock look like it crashed 65–80%, which structurally excludes entry candidacy
(positive momentum is required) regardless of the underlying artefact. `WY` shows 2 of 254 days
with technically-positive (corrupted) momentum — but the backtest overlap check above already
confirmed zero actual trades occurred there, empirically, not just by this theoretical argument.

**`EXPE`'s one nearby real trade (entry 2012-12-26) was checked precisely, not estimated — confirmed
unaffected.** `compute_momentum_series()`'s 252-trading-row lookback for that entry anchors at
2011-12-22 — one trading day *after* the 2011-12-21 cliff. The lookback window sits entirely on the
post-event side; the cliff was never inside it. This trade's momentum value (0.987) is genuine and
uncorrupted.

**Conclusion: confirmed, not assumed, that no real decision — live or backtested — was ever affected
by any of the 14 cliffs.** Matches the stated expectation exactly, with direct evidence rather than
inference from the dates alone.

## Future research item — flagged, not implemented

**Whether the momentum signal should read `adj_close` instead of `close`.** Would address this
artefact class (mergers/spinoffs/special dividends distorting `close`) and the already-documented
dividend-exclusion bias in `close`-based momentum in one variable change. Explicitly not scoped or
attempted here. Needs, when it is eventually researched: both pools (per the dual-provenance
standing requirement), and a criterion that does not hinge on a ±2-block delta (per the 16-block
resolution-floor standing requirement) — this is exactly the kind of single-variable universe-wide
change those two requirements exist to protect against being mis-evaluated.

