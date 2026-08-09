# Pre-run instrument feasibility check — sleeve-allocation study

**Status: investigation and documentation only. No backtest arms run, no criterion changed, no adoption. Nothing written to `price_data` or `ticker_data_quality`.**

## Methodology

`brokers/trading212.py:resolve_instrument_candidate()` is stock-specific — it guesses `{TICKER}_US_EQ` and verifies via an exact `shortName` match. It returns **zero matches** for any of SHY/TLT/XLU/XLP/XLV, because T212's ETF universe is entirely UCITS-domiciled (EU/UK retail platforms cannot offer non-UCITS US-domiciled ETFs directly — no PRIIPs KID) and UCITS funds use unrelated tickers/shortNames with no fixed mapping to the US original (e.g. XLU's UCITS-equivalent shortName is `SXLU`, not `XLU`).

Since the existing resolver doesn't apply to ETFs, this check used the **same underlying data** — a real, live `Trading212Client(live=False).get_instruments()` pull, executed once over SSH against production credentials on 2026-08-08 22:53 UTC, cached to `t212_instruments_2026-08-08.json` (17,403 instruments; 6,212 of type `ETF`) — with a **name-keyword search** in place of the stock-specific shortName-match algorithm. This is stated explicitly per the task's own instruction to say so if the existing resolution path can't confirm availability directly.

Price history for every UCITS candidate was pulled read-only via `yfinance` into `research/sleeve_allocation_prereg_2026-08-08/instrument_feasibility/` (local cache only — no writes to `price_data` or `ticker_data_quality`).

## Findings by component

### 1. SHY → short-duration Treasury

| Field | Value |
|---|---|
| T212 ticker | `IBTSs_EQ` — iShares USD Treasury Bond 1-3yr (Dist), USD, ISIN `IE00B14X4S71` |
| Also on T212 (same ISIN) | `IBTSl_EQ` (GBP, LSE) — **not recommended**, see below |
| Inception / usable history | Raw data nominally reaches back to 2008-01-02, but only via the **GBP** share class, which contains an **unexplained ~145x price cliff on 2009-01-02** (verified: SHY itself has zero such breaks anywhere in its history; this is a UCITS-listing artifact, not a real market move — likely currency-redenomination or ticker-reuse, root cause not identified). The clean, artifact-free USD-denominated series (`IBTS.SW` on yfinance, same ISIN/fund) begins **2009-01-05**. |
| Currency | USD available. No FX-hedged share class exists for this fund in any currency (searched explicitly for "Hedged" in the name across all currency variants — zero results). |
| Acc/Dist | Dist — matches SHY's own treatment (clean, apples-to-apples). |
| Tracking vs SHY (USD leg, 2009-01-05→2026-08-07, 17.6y, artifact-free segment) | daily-return correlation **0.367**, CAGR diff **+0.10 pp/yr**, max divergence (normalized, 100-base) **2.85**, end-of-period divergence **+1.77**. The lowish daily correlation is consistent with a 1-3yr Treasury fund's low absolute volatility (NAV-timing noise dominates daily return correlation at this duration) rather than poor replication — the *cumulative* tracking is tight. |
| **Verdict** | **SUBSTITUTION SOUND** — but only using the USD share class (`IBTSs_EQ`), not the GBP one, and only from 2009-01-05 onward, not 2008. |

### 2. TLT → long-duration Treasury

| Field | Value |
|---|---|
| T212 ticker | `IDTLl_EQ` — iShares USD Treasury Bond 20+yr (Dist), USD, ISIN `IE00BSKRJZ44` |
| Inception | **2015-01-21** (checked every currency/Dist-Acc variant across all sibling ISINs in this family — this is the earliest). |
| Currency | USD available. No FX-hedged variant exists. |
| Acc/Dist | Dist — matches TLT. |
| Tracking vs TLT (2015-01-21→2026-08-07, 11.5y) | correlation **0.731**, CAGR diff **-0.11 pp/yr**, max divergence **6.29**, end divergence **-0.85**. Excellent tracking. |
| **Verdict** | **SUBSTITUTION SOUND**, from 2015-01-21. |

### 3. XLU → Utilities sector

| Field | Value |
|---|---|
| T212 ticker | `SXLUl_EQ` — **SPDR** S&P U.S. Utilities Select Sector (Acc), USD, ISIN `IE00BWBXMB69` — this is SPDR's own UCITS wrapper of the identical Select Sector index XLU itself tracks, not a proxy family. |
| Alternative found | iShares S&P 500 Utilities Sector (`IUSUl_EQ`/`2B7Ad_EQ`), inception 2017-03-22 — later, no better. No Dist share class exists for any S&P-500/Select-Sector-matched Utilities UCITS fund on T212 (checked explicitly). MSCI World / STOXX Europe 600 Utilities funds exist and do have Dist variants but track a **different, broader index** — excluded per the task's own instruction not to accept a mismatched broad proxy. |
| Inception (best available) | **2015-07-07** |
| Currency | USD available. No FX-hedged variant. |
| Acc/Dist | **Acc only** — no genuinely sector-matched Dist alternative exists. |
| Tracking vs XLU (2015-07-07→2026-08-07, 11.1y) | correlation **0.664**, CAGR diff **+2.86 pp/yr**, max divergence **75.19**, end divergence **+68.60**. |
| **Verdict** | **SUBSTITUTION UNSOUND AS PROPOSED.** A genuinely S&P-500-sector-matched instrument exists (contrary to the pre-registration's own stated worry) — but it is Acc-only, and the pre-registration's plan to backtest on the US (Dist) price series carries a real, compounding ~2.9 pp/yr bias against what the actual held Acc instrument would do. Not usable as a naive price-series drop-in without adjusting the backtest methodology for the accounting mismatch. |

### 4. XLP → Consumer Staples sector

| Field | Value |
|---|---|
| T212 ticker | `SXLPl_EQ` — SPDR S&P U.S. Consumer Staples Select Sector (Acc), USD, ISIN `IE00BWBXM385` |
| Alternative found | iShares S&P 500 Consumer Staples Sector (`ICSUl_EQ`/`IUCSl_EQ`), inception 2017-03-22 — later. No S&P-500-specific Dist variant exists; only MSCI World Consumer Staples (Dist) exists, a different, broader index — excluded, same reasoning as XLU. |
| Inception | **2015-07-07** |
| Currency | USD available. No FX-hedged variant. |
| Acc/Dist | **Acc only.** |
| Tracking vs XLP (2015-07-07→2026-08-07, 11.1y) | correlation **0.626**, CAGR diff **+2.53 pp/yr**, max divergence **59.22**, end divergence **+52.19**. |
| **Verdict** | **SUBSTITUTION UNSOUND AS PROPOSED** — identical reasoning to XLU. |

### 5. XLV → Health Care sector

| Field | Value |
|---|---|
| T212 ticker (best inception) | `SXLVl_EQ` — SPDR S&P U.S. Health Care Select Sector (Acc), USD, ISIN `IE00BWBXM617` |
| Dist alternative found | iShares S&P 500 Health Care Sector (Dist), `IUHEa_EQ`, EUR, ISIN `IE00BMBKBZ46` — genuinely S&P-500-sector-matched **and** Dist-matched, but inception **2020-06-15**, nearly 5 years later than the SPDR Acc option, and EUR- not USD-denominated. Using it would fix the accounting mismatch but push the all-components-available date to 2020. |
| Inception (best/earliest) | **2015-07-07** (SPDR Acc) |
| Currency | USD available for the SPDR Acc option. No FX-hedged variant found for either candidate. |
| Acc/Dist | Acc (best-inception option); a Dist option exists but trades 5 years of history for it. |
| Tracking vs XLV, SPDR Acc (2015-07-07→2026-08-07, 11.1y) | correlation **0.615**, CAGR diff **+1.42 pp/yr**, max divergence **38.60**, end divergence **+34.61** — smallest of the three sector wedges, still material. |
| **Verdict** | **SUBSTITUTION UNSOUND AS PROPOSED**, same reasoning as XLU/XLP. A materially better-matched (Dist) alternative exists at the cost of ~5 years of history — a tradeoff for you to decide, not made here. |

## Overall

**Earliest date at which all five best-available components have genuine, artifact-free tradeable history:**

```
max(SHY 2009-01-05, TLT 2015-01-21, XLU 2015-07-07, XLP 2015-07-07, XLV 2015-07-07)
= 2015-07-07
```

bounded by the SPDR sector-fund launch date, common to all three sector legs.

**2008 crisis block is NOT testable.** This project's own block convention places the crisis block at 2008-03-23 → 2009-06-13. All three sector-sleeve components have no tradeable UCITS equivalent until **2015-07-07** — over six years after that block ends — regardless of how the short-duration Treasury data-artifact question is resolved, since the sector legs are the binding constraint either way. The pre-registration's own flagged concern ("if the instrument we could actually hold has no 2008 history, the block that matters most for a defensive sleeve is untestable") is **confirmed true**.

**A second, independent problem, separate from history depth:** even within the window that *is* available (2015-07-07 onward), all three sector-sleeve legs have **no genuinely sector-matched, USD-denominated, Dist-treatment UCITS equivalent** — only Acc share classes exist at the best-available inception date. Backtesting the sleeve on the US (Dist) price series, as the pre-registration proposes, would carry a real, growing ~1.4–2.9 pp/yr bias per sector leg relative to what the actual held Acc instrument's price series would do — this affects the validity of *any* window tested with the sector legs, not just the 2008 question.

**Can the study run unchanged? No.** Two separate blocking issues, not one:
1. The full 2007–2026 window the pre-registration assumes is not achievable with real tradeable instruments — the earliest common start is 2015-07-07, and the single block the defensive sleeve exists to protect against (2008-09 crisis) cannot be included at all.
2. Even restricted to 2015-07-07 onward, using the US series as a backtest proxy for the sector legs (as currently planned) is not a clean substitution — the Acc/Dist mismatch is a real, quantified, non-cosmetic bias, not a rounding concern.

Neither Treasury leg (SHY-equiv, TLT-equiv) has a soundness problem — both are SUBSTITUTION SOUND as specified, once the correct (USD) share class is used for SHY.

## What this does not decide

Per the task's constraints, no component was silently substituted and no study arm was run. Two decisions are yours to make, not resolved here:
- Whether to proceed with the study restricted to 2015-07-07→present (dropping the 2008 test entirely), and if so, whether that is still worth running given the defensive sleeve's whole rationale is protection against exactly the kind of event the truncated window would exclude.
- For the sector legs: whether to accept the Acc/Dist bias as a stated, bounded limitation (in the spirit of this project's existing "state the deferred cost explicitly" discipline), switch to the later-inception Dist alternative for Health Care only (inconsistent treatment across the three sector legs), or treat the sector-sleeve component as NO-GO in its current form and reconsider that leg of the design entirely.
