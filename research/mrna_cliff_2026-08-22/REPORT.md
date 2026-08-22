# MRNA 2026-08-19 price cliff — classification and live impact (2026-08-22)

**Investigation only. No `price_data` modified, no config changed, nothing deployed.** All
production access was read-only SQL/diagnostic scripts over SSH.

**Alert:** MRNA, 2026-08-18 close 62.96 → 2026-08-19 close 174.38, ratio **2.7697**.

---

## VERDICT: `GENUINE_MARKET_MOVE`

Moderna and Merck announced on the morning of **2026-08-19** (pre-market, ~07:00 ET) that their
individualised mRNA cancer therapy **intismeran autogene**, combined with Merck's Keytruda, met the
primary recurrence-free-survival endpoint and a key distant-metastasis-free-survival endpoint in the
**Phase 3 INTerpath-001 melanoma trial**. MRNA closed **+176.97%**. Market cap moved **$25B → $69B**;
short sellers took a reported **$5.5B** loss.

The classification does **not** rest on `.splits` being empty. Five independent lines of evidence,
three of which are self-contained in the price data and independent of yfinance's action metadata:

### 1. `adj_close` moves in exact lockstep with `close` — the genuine-market-move signature

| date | close | close ratio | adj_close | adj_close ratio |
|---|---|---|---|---|
| 2026-08-17 | 64.46 | 1.0180 | 64.46 | 1.0180 |
| 2026-08-18 | 62.96 | 0.9767 | 62.96 | 0.9767 |
| **2026-08-19** | **174.38** | **2.7697** | **174.38** | **2.7697** |
| 2026-08-20 | 133.32 | 0.7645 | 133.32 | 0.7645 |
| 2026-08-21 | 145.13 | 1.0886 | 145.13 | 1.0886 |

This is the `AIG`/`GL`/`PCG` pattern from the prior study, not the `KDP`/`MO`/`WY` pattern. A
corporate action that `close` has failed to absorb shows `adj_close` moving **smoothly (~1.0)** while
`close` jumps. Here the two are identical to 4dp — yfinance recognises no distribution event, and
none is hiding in the adjusted series.

### 2. Intraday shape is price discovery, not arithmetic rebasing — the decisive mechanical evidence

| | value |
|---|---|
| prev close (08-18) | 62.96 |
| **open** (08-19) | **116.02** — a 1.8428x gap, *partway between* old and new close |
| low | 114.46 |
| high | 176.66 |
| close | 174.38 |
| **intraday range** | **1.5434x (54.3%)** |
| normal MRNA intraday range (median, 2026-06-01..08-18) | 1.0624x (6.2%) |

A corporate-action rebasing scales open/high/low/close by **one constant factor**, leaving the
intraday range untouched. Here the range **expanded 8.7x**, and the open sits partway between the two
closes rather than at the rebased level. No share-count arithmetic produces that shape.

### 3. Volume moved in the direction *opposite* to a reverse split, by ~90x

- median daily volume 2026-06-01..08-18: **6,177,800**
- 2026-08-19: **199,252,300** — **32.3x up**
- a 1-for-2.77 reverse split divides share count, so volume should fall to **~0.36x**

Follow-on days confirm sustained repricing rather than a one-off step: 08-20 −23.6% on 99.5M,
08-21 +8.9% on 86.7M.

### 4. The direction of the move structurally excludes the un-smoothable class

The brief correctly warns that an unadjusted **spinoff or special dividend may not appear in
`.splits` at all** — and that is the exact class the prior study found yfinance never smooths in
`close`. **Stated explicitly rather than assumed away: that class cannot produce this cliff, because
of its sign.** A spinoff or special dividend removes value from the company and can only push the
price **down**. Every one of the prior study's un-smoothed corporate actions was a *fall*
(KDP 0.179, MO 0.301, WY 0.381). This is a **2.7697x rise**.

The only corporate action that raises a price is a **reverse split** — and that class *is* recorded
in `.splits` and *is* retroactively rebased in `close` (the 2026-08-06 audit checked 296 real splits,
all smooth). So the smoothable class is excluded by its absence plus a non-rebased history, and the
un-smoothable class is excluded by direction. There is no third corporate-action mechanism left.

### 5. Metadata and market-cap corroboration

- `.splits`: **empty for MRNA's entire history** — not merely near the date. `actions`, `dividends`,
  `capital_gains` all empty too. *(Supporting, not load-bearing — see line 4.)*
- Implied market cap at the current 399,235,889 shares: **$25.14B** at 62.96 → **$69.62B** at 174.38.
  These match the independently reported **$25B → $69B** exactly, confirming the **share count did not
  change**. (Noted honestly: market-cap conservation alone is *not* a discriminator, since a reverse
  split conserves it by construction — it is the external confirmation that the cap genuinely
  *tripled* that closes this.)
- **(d) fresh pull:** reproduces the cliff identically. Per the prior study this is *not* on its own a
  discriminator — genuine moves and un-smoothable corporate actions both reproduce. It only rules out
  stale local data, and it does: **production `price_data` matches the fresh pull to the cent** on
  every OHLC field across the transition.

---

## PART 2 — LIVE IMPACT

### The premise is inverted twice over: MRNA was already admitted, and the admission predates the cliff by seven days

**MRNA is an open position, entered 2026-08-12 — seven days before the 2026-08-19 move.**

`invest_orders` id **74**: BUY 30 shares, placed 2026-08-12T14:31:03, **filled at $61.81**,
£1,372.92, stop 52.7991, `stop_order_status=PLACED`. No SELL row, no `pnl` row → **still open**.
Confirmed in the live T212 snapshot (fetched 2026-08-22T12:05): `MRNA_US_EQ`, qty 30, avg 61.81,
current 145.80, **ppl +£1,830.08** (fxPpl −£14.48), of 20 open positions.

### The "+131.9% at rank 4" figure is real, pre-cliff, and already spent

That figure is `pending_orders` id **2690**, decided **2026-08-11T22:06**, `momentum_pct=1.318913`,
`relative_rank_pct=1.565467`, `admission_rank_value=1.565467`, status **SUBMITTED**. Verified by
direct recomputation: on 2026-08-11 close 60.57 against the t−252 anchor 2025-08-08 at 26.12 gives
**1.318913** — the cliff had not happened and was not in that window. **The signal that admitted MRNA
was never touched by the cliff.**

### Momentum distortion, quantified

| | momentum | source |
|---|---|---|
| current, as stored (2026-08-21) | **+439.72%** | close 145.13 / anchor 2025-08-20 26.89 |
| counterfactual, 08-19 move removed | **+94.87%** | post-cliff segment ÷ 2.7697 |
| **attributable to the 08-19 move** | **344.85pp** | |
| on the actual admission date (2026-08-11) | **+131.89%** | pre-cliff, cliff not in window |

**Because the move is genuine, the +439.72% is a real momentum figure, not a fabricated one.** The
344.85pp is what the distortion *would have been* had the cliff been an artefact — it is the size of
the averted problem, not a present error.

### Not in tonight's queue

MRNA has **no `PENDING` row at all**. It is held, so it is not a candidate. Tonight's batch
(decided 2026-08-21, 284 PENDING) ranks: SNDK 33.92, AMAT 2.50, DELL 2.42, TER 2.41, ECHO 2.31,
AMD 2.27 — no MRNA. The only BUYs since 2026-08-18 are MU, LITE, INTC (08-20) and CIEN, COHR
(08-21). **No position was opened on a phantom signal; none could have been.**

### Exit path, worth knowing for a position now 2.75x its stop

MRNA was decided **2026-08-11**, the day *before* the `atr_stop_multiplier` 2.0→3.0 flip, so it
carries a **stale 2.0× stop** — recomputed directly (ATR truncated to the decide date, multiplier
from `[backtest]`, period 14 from `[signals]`, per the two-section warning): implied multiplier
**2.0000**. It is one of the 18 positions CLAUDE.md records as deliberately left to ride. Benign
here — the stop sits **63.6% below** the current price. With `stock_max_holding_days: 0` live, this
position **will not age out**; it exits only on `STOP_LOSS`, `SIGNAL_OFF`, or `CRISIS_EXIT`.

---

## PART 3 — RECOMMENDATION (no action taken; awaiting confirmation)

Classification is `GENUINE_MARKET_MOVE`, so the third branch applies: **record as a known exception
alongside the existing 14, and change nothing else.**

- **Do not re-fetch or replace MRNA's history.** The stored series is correct and already matches a
  fresh pull to the cent. There is nothing to repair.
- **Do not exclude MRNA from candidacy.** Its momentum is real. Excluding it would suppress genuine
  signal, and it is moot in any case — MRNA is held, not a candidate. (For the record, the route
  would *not* have been `ticker_data_quality`: that table is scoped to identity verification
  (`verify_ticker_identity`, `quote_type`/exchange/name), and MRNA is correctly `CLEAN` there —
  `quote_type='EQUITY'`, `exchange='NMS'`, `long_name='Moderna, Inc.'`. A price-artefact exclusion
  would have needed a mechanism this project has not built.)
- **Proposed change, one entry** in `data/ingestion.py:_KNOWN_PRICE_CLIFF_EXCEPTIONS`, keyed
  `("MRNA", "2026-08-19")`, classification `GENUINE_MARKET_MOVE`, evidence citing the Phase 3
  INTerpath-001 readout, the lockstep ratios, the 32.3x volume and the 54.3% intraday range. This is
  a **(ticker, date)** key, so it silences only this pair — a future MRNA cliff on any other date
  still alerts normally.

**One precedent worth your explicit sign-off:** all 14 existing exceptions are historical (2007–2024)
and were cosmetic. This would be the first **current-window, actively-held** entry. That is
defensible — the mechanism is date-scoped and the alert is purely informational — but it means the
suppression is being applied to a ticker whose momentum is actively feeding live ranking, so it
should be a deliberate decision rather than routine list maintenance.

**Confirmation of the alert's own accounting:** the 2026-08-21 EOD record shows
`price_cliff_hits_total: 4406`, `price_cliff_hits: 15`, `price_cliff_hits_known_exceptions: 14` —
i.e. exactly **one** unrecognised live-scoped hit, MRNA. Adding this entry returns the alert to
silent.

---

## Sources

- [Motley Fool — Moderna Skyrockets 177% on Positive Phase 3 Melanoma Data](https://www.fool.com/coverage/stock-market-today/2026/08/19/stock-market-today-aug-19-moderna-skyrockets-177-on-positive-phase-3-melanoma-data/)
- [Bloomberg — Moderna's 177% Surge Burns Shorts in a "Painful" $5.5 Billion Loss](https://www.bloomberg.com/news/articles/2026-08-19/moderna-s-160-surge-burns-shorts-in-painful-5-billion-loss)
- [CNBC — Moderna and Merck shares surge](https://www.cnbc.com/video/2026/08/19/moderna-and-merck-shares-surge.html)

## Reproduction

- `step1_yfinance_probe.py` — `.splits`/`actions`/`dividends`, fresh `auto_adjust=False` pull, ratios
- `step2_reverse_split_test.py` — market cap, volume and OHLC-shape tests of the reverse-split hypothesis

Production-side diagnostics were read-only scripts run over SSH (`python3.11`); their queries are
reproduced inline above.
