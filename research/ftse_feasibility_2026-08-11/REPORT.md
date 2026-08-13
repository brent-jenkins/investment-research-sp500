# FTSE universe — feasibility check (2026-08-11)

**Verdict: an honest FTSE backtest is NOT currently possible. Stopped at check 2.**

Point-in-time membership (check 1) is available with excellent provenance, subject to a licence
problem. **Check 2 fails outright**: of 15 genuinely delisted FTSE 100 names spanning 2007–2023,
**zero** have price history in yfinance. A backtest built on that is pure survivorship bias — the exact
defect this project quarantined 163 tickers over.

Investigation only. **Nothing was ingested into `price_data`, nothing written to
`ticker_data_quality`**, no production file touched, no study designed.

---

## Check 1 — Point-in-time membership: AVAILABLE, with a licence problem

**The authoritative source exists and is genuinely deep.** FTSE Russell (LSEG) publishes
*"FTSE 100 Historic Additions and Deletions"* as a maintained PDF — cover dated **April 2026**, so it
is current. Extracted and quantified directly rather than assumed:

- **540 dated event rows, 1984-01-19 → 2026-03-23**
- **212 rows inside this project's 2007–2026 backtest window**, continuous (2007: 19, 2008: 19,
  2009: 13, 2010: 7 … 2024: 11, 2025: 7)
- Columns: **Date · Added · Deleted · Notes**, with corporate-event reasons
  (*"Corporate Event - Acquisition of Imperial Group by Hanson Trust"*)

This is a **better provenance than the S&P source currently in use** — `fja05680/sp500` is a
community-maintained GitHub dataset; this is the index provider's own record. A FTSE 250 equivalent
(`ftse-250-constituent-history.pdf`) exists on the same path.

**Two real obstacles, neither fatal on its own:**

**(a) The licence prohibits exactly the use required.** Verbatim from the document:

> *"No part of this information may be reproduced, **stored in a retrieval system** or transmitted in
> any form or by any means, electronic, mechanical, photocopying, recording or otherwise, without prior
> written permission of the applicable member of LSEG. **Use and distribution of LSEG data requires a
> licence from LSEG** and/or its licensors."*

Ingesting this into `universe_membership` is "stored in a retrieval system" on any plain reading. This
is not a technical barrier that can be engineered around — it needs either LSEG's permission or a
different source.

**(b) It identifies companies by NAME, not ticker.** Every row reads *"Coats Viyella"*, *"Ranks Hovis
McDougall"* — no EPIC, no symbol. Using it requires a name→ticker mapping for several hundred
historical companies, many long dead. **Check 2 shows why that mapping is more dangerous than it
sounds.**

**The licence-clean alternative is far too shallow.** `yfiua/index-constituents` (Wikipedia + Wayback
derived) covers FTSE 100 only **from July 2023** — roughly three years, as monthly snapshots rather
than join/leave events, and **no FTSE 250**. Against a 19-year window and 16-block validation, that is
not usable. Reconstructing further back from Wikipedia revision history is possible in principle — the
same method the project already used for the `NEEDS_REVIEW` adjudication — but that is a construction
project with its own accuracy burden, not an available dataset.

---

## Check 2 — Price coverage: FAILS

Tested read-only against yfinance, current names and genuinely delisted names separately, because the
delisted population is where the US problems concentrated.

**Current constituents — excellent, better than the US book:**

| | result |
|---|---|
| Returned data | **14 / 14** |
| History depth | **6,772 rows, from 2000-01-03** |
| Price cliffs (outside the production `[0.5, 2.0]` band) | **0 across all 14** |
| Max calendar gap | **5 days** (weekends) |

SHEL, AZN, HSBA, BP, GSK, RIO, ULVR, BATS, VOD, LLOY, BARC, TSCO, REL all clean; GLEN starts 2011 at
its real IPO. No sign of the unadjusted-corporate-action problem documented for US tickers.

**Delisted constituents — total failure:**

| ticker | company | removed | result |
|---|---|---|---|
| CBRY.L | Cadbury | 2010 (Kraft) | NO DATA |
| SCTN.L | Scottish & Newcastle | 2008 | NO DATA |
| NRK.L | Northern Rock | 2008 (nationalised) | NO DATA |
| BB.L | Bradford & Bingley | 2008 | NO DATA |
| HBOS.L | HBOS | 2009 (Lloyds) | NO DATA |
| BGY.L | British Energy | 2009 (EDF) | NO DATA |
| CW.L | Cable & Wireless | — | NO DATA |
| AMFW.L | Amec Foster Wheeler | 2017 | NO DATA |
| MRW.L | Wm Morrison | 2021 | NO DATA |
| RSA.L | RSA Insurance | 2021 | NO DATA |
| AGK.L | Aggreko | 2021 | NO DATA |
| GFS.L | G4S | 2021 | NO DATA |
| MGGT.L | Meggitt | 2022 | NO DATA |
| MCRO.L | Micro Focus | 2023 | NO DATA |

**0 of 15 recovered.** The failures are not confined to the distant past — 2021, 2022 and 2023
removals are as absent as 2008 ones, so this is not a "history fades" problem that a recent-only window
would dodge.

**This is materially worse than the US case.** The 2026-08-06 survivorship remediation recovered **331
of ~700** missing US tickers, taking dominant-era coverage from 64.3% to 76.9%. Here the recovery rate
on the equivalent population is **zero**, and the crisis-era names (Northern Rock, HBOS, B&B) — the
exact population whose absence biases a 2008-09 block — are all missing.

### The one apparent recovery was a false positive, and that is its own finding

`BOOT.L` returned a perfect-looking series: 6,772 rows to 2026-08-11, **0 cliffs, no gaps**. It is
**Henry Boot PLC** — a different, still-listed company — not Alliance Boots. My name→ticker guess was
simply wrong.

**That failure mode is the serious one.** A mis-mapped name returns a complete, clean, plausible price
series for the wrong company, and it passes every automated quality check this project has: no cliff,
no gap, and `verify_ticker_identity()` would score it **CLEAN** (a real named equity, `quoteType`
EQUITY, exchange LSE, no membership-window overrun to trigger Check B). The existing quarantine
machinery — built for recycled *symbols* — does not catch a wrong *name→symbol mapping*, because
nothing about the resulting data is anomalous.

Given check 1's source is name-only, every one of several hundred historical constituents would need
this mapping done correctly, with no automated way to detect an error. That compounds check 2's failure
rather than offering a route around it.

---

## Checks 3 and 4 — not reached (stopped per instruction), but two findings worth keeping

**Check 3 (T212 tradability) — not tested.** Flagging one known obstacle: the sleeve study already
established that `resolve_instrument_candidate()`'s `{TICKER}_US_EQ` / shortName-match convention is
**US-stock-specific and returns zero matches for non-US instruments**, requiring a name-keyword search
against a live `get_instruments()` pull instead. So this is real work, but it was never the blocker.

**Check 4 (currency) — partially examined, one latent trap found.**

The production path already handles pence correctly: `Trading212Client._to_gbp()` has an explicit
`if currency == "GBX": return price / 100.0` branch for LSE pence quoting.

**But yfinance reports the currency as `"GBp"`, not `"GBX"`** — confirmed live on SHEL.L, AZN.L,
BOOT.L, PSN.L. `_to_gbp()` does `currency.upper()`, which turns `"GBp"` into `"GBP"` and matches the
**pounds** branch, returning the price unchanged — a silent **100×** overstatement. This is **not a
live bug today** (the function is fed T212's own `currencyCode`, which uses `GBX`), but it would become
one the moment a yfinance-derived currency string reached it.

The deeper issue is unit mixing in stored data: `price_data` holds raw yfinance closes, so FTSE names
would sit in **pence** (SHEL.L = 3345.5, AZN.L = 11914.0) beside US names in **dollars**. Consequences
split cleanly:

- **Signal path is safe.** Momentum, relative rank, and ATR-as-%-of-price are all *ratios* — scale
  invariant, unaffected by pence-vs-dollars.
- **Sizing and P&L are not.** `quantity = position_gbp / price` is wrong by 100× if a pence price is
  treated as pounds, as are entry/exit values and stop levels.

So the currency work is real but tractable and well-localised — it was never going to be the thing that
killed this.

---

## Recommendation

**Do not proceed. The blocker is check 2 and it is not engineerable.**

A FTSE backtest built on currently-available data would include only companies that survived to today —
no Northern Rock, no HBOS, no Bradford & Bingley through 2008-09, no Cadbury, no Morrisons, no Micro
Focus. That is textbook survivorship bias, in the strongest possible form, and it would be **worse than
the US universe was before remediation** rather than better. This project has already established what
that does to results: the pre-remediation universe produced a **£2.21M P&L anomaly** traced to a single
corrupted ticker, and 163 tickers were quarantined to stop it.

**The motivating premise remains sound, which is the frustrating part.** GBP-denominated FTSE names
genuinely would remove the 0.15%-each-way FX drag, and turnover cost has been the most consistent
killer in this project's results — the deployment-cap sweep's entire "leg C reversal" vanished once FX
was modelled, and the displacement analysis died on turnover at every usable margin. The sector-inverse
argument is also real. **The idea is not wrong; the data to test it honestly does not exist in the free
tier.**

**What would change the answer, in rough order of cost:**

1. **A price source with delisted UK coverage.** This is the binding constraint and everything else is
   downstream of it. Sharadar, Norgate, Refinitiv and similar carry delisted LSE history; all are paid.
   Without this, nothing else matters.
2. **Licence clarity from LSEG** on using the constituent-history document as a membership source, or
   a Wikipedia-revision reconstruction accepted as licence-clean but requiring its own validation.
3. **A validated name→ticker mapping** for historical constituents, with the explicit understanding
   that a wrong mapping is silent and undetectable by existing checks — this needs controls in the way
   the contiguity classifier was validated against `FNMA`/`PALM` before being trusted.

**A narrower thing that IS possible now, and is honest, if the appetite exists:** a **current-constituent,
forward-only** FTSE track — no backtest, no historical claims, just live paper trading of today's FTSE
100 with the existing signal logic, accumulating a forward record. Current-name price data is genuinely
excellent (6,772 clean rows back to 2000). That sidesteps survivorship entirely by never making a
historical claim — but it also means years before it says anything, and it should not be dressed up as
validation. Recorded as an option, not a recommendation.

---

## Sources

- [FTSE Russell — FTSE 100 Historic Additions and Deletions (PDF, April 2026)](https://www.lseg.com/content/dam/ftse-russell/en_us/documents/policy-documents/ftse-100-constituent-history.pdf)
- [FTSE Russell — FTSE 250 Constituent History (PDF)](https://www.lseg.com/content/dam/ftse-russell/en_us/documents/policy-documents/ftse-250-constituent-history.pdf)
- [yfiua/index-constituents](https://github.com/yfiua/index-constituents)
- [London Stock Exchange — FTSE 100 constituents (current)](https://www.londonstockexchange.com/indices/ftse-100/constituents/table)
- [London Business School — historical index constituents FAQ](https://libanswers.london.edu/faq/175663)

Raw coverage results: `check2_coverage.csv`; test script: `check2_price_coverage.py` (read-only).
