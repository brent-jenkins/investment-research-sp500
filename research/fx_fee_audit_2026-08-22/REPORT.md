# Is the 0.15% T212 FX fee actually being charged? (2026-08-22)

**VERDICT: UNANSWERABLE from stored order and P&L data — and for a stronger reason than "the demo
isn't charging FX."** The GBP amounts in `invest_orders` and `pnl` are **not broker figures at all**.
They are computed by this system from its own `GBPUSD=X` series, so an implied rate derived from them
recovers this project's own number and carries **zero** information about what T212 charged. No amount
of demo-versus-live difference would change that.

**Read-only. No config, code, database or deploy change. Production was queried read-only over SSH.**

> ## ANSWERED 2026-08-22 — the fee IS charged, and the cost model is CONFIRMED
>
> The verdict above remains exactly right **about stored order data**, and §1–§3's proof of that is
> unchanged — it is what forced the question to the only remaining source. That source has now been
> read: **T212's own order cost breakdown in the practice-mode Invest app.**
>
> **FX fee £6.40 on a £4,278.36 Moderna buy = 0.1496%, i.e. 0.15% to rounding, charged on BOTH legs
> (~£6.42 on exit). Round trip ≈ 0.30% of notional. `FX_ROUND_TRIP_FEE = 0.0030` is CONFIRMED in both
> rate and per-trade application.**
>
> **§8's open question is closed in the LESS favourable direction**: the account's default
> buying/selling currency is GBP, so the multi-currency-wallet escape does not apply, the models do
> **not** overstate FX cost, and no cost-adjusted figure improves. Full detail in §8's RESOLVED block;
> summary in §9; recorded in CLAUDE.md. **Two premises in the request were also corrected — see
> CORRECTION 1 (§7) and CORRECTION 2 (§6).**

---

## 1. The method is broken at the source — checked first, as instructed

`brokers/trading212.py:2377` (inside `poll_placed_orders()`), on a confirmed fill:

```python
actual_value = round(Trading212Client._to_gbp(
    fill_price * effective_qty, ccy, gbpusd, eurusd) or 0.0, 2)
...
estimated_value_gbp = COALESCE(:actual_value, estimated_value_gbp)
```

It **is** written at fill time and the variable is called `actual_value`, which makes it look like the
real debited amount. It is not. Following it through:

- `_to_gbp(price, "USD", gbpusd, …)` → **`price / gbpusd`** (line 696)
- `gbpusd` comes from `_fx_rates()` (line 661), whose docstring and SQL are explicit:
  **`SELECT close FROM price_data WHERE ticker = 'GBPUSD=X' ORDER BY date DESC LIMIT 1`**

So the stored GBP amount is `usd_value ÷ (a GBPUSD=X close from price_data)`. Recovering an implied
rate from it returns that close. **Comparing it to `GBPUSD=X` compares the series to itself.**

`pnl.pnl_gbp` is the same: line 1232 is `pnl_gbp = Trading212Client._to_gbp(native_pnl, ccy, gbpusd,
eurusd)`, via the identical `_fx_rates()` call. **Both tables the request nominated are circular.**

This is the caveat the request anticipated, in a sharper form: the field is not a stale pre-fill
estimate — it is a *post-fill, system-computed* conversion, which is why it looks authoritative.

---

## 2. Proven empirically, not just by reading the code

Population: 97 `invest_orders`, of which **67 FILLED USD legs — 45 BUY, 22 SELL** — plus 26 `pnl`
rows (25 USD; the 26th is a `MAPPING_CORRECTION` in CHF from the 2026-07-31 instrument incident, not
a real trade).

### TEST 1 — one rate per day, shared by every instrument

| trade date | legs | BUY | SELL | min | max | **within-day spread** |
|---|---|---|---|---|---|---|
| 2026-08-03 | 16 | 16 | 0 | 1.348194 | 1.348205 | **0.082 bp** |
| 2026-08-07 | 6 | 6 | 0 | 1.345311 | 1.345318 | **0.052 bp** |
| 2026-08-12 | 40 | 18 | 22 | 1.350616 | 1.350627 | **0.081 bp** |
| 2026-08-20 | 3 | 3 | 0 | 1.360851 | 1.360864 | **0.096 bp** |
| 2026-08-21 | 2 | 2 | 0 | 1.363045 | 1.363051 | **0.044 bp** |

**Worst within-day spread anywhere: 0.096 bp. A 15 bp fee is ~190× that.** Forty legs across forty
different instruments on 2026-08-12 share one rate to six decimal places. A per-order broker
conversion cannot do that; a single constant divided into each USD value can. The residual scatter is
fully explained by the 2 dp rounding of the stored GBP amount (±£0.005 on ~£1,200 ≈ ±0.04 bp).

### TEST 2 — BUY and SELL get the IDENTICAL rate on the same day (the sharpest disproof)

2026-08-12 is the one date with both directions:

| | | |
|---|---|---|
| BUY mean (n=18) | **1.350621** | −0.0271% vs market close |
| SELL mean (n=22) | **1.350621** | −0.0271% vs market close |
| difference | **+0.0018 bp** | a 0.15%/leg fee implies a **~30 bp** BUY/SELL gap |

**A conversion fee is directional.** Buying USD should cost *more* GBP and selling USD should yield
*less* GBP, so the two implied rates must **straddle** the market rate by roughly ±the fee. They do
not straddle it — they coincide, to two ten-thousandths of a basis point, and sit on the **same side**
of it. That is arithmetically impossible for a real fee and exactly what one shared constant produces.

### TEST 3 — magnitude and distribution (reported as requested)

Implied rate vs `GBPUSD=X` close on the trade date:

| population | n | mean | median | sd | min | max | neg : pos |
|---|---|---|---|---|---|---|---|
| **ALL legs** | 67 | −0.0356% | −0.0271% | 0.0332 | −0.1284% | +0.0643% | 64 : 3 |
| **BUY legs** | 45 | −0.0398% | −0.0271% | 0.0399 | −0.1284% | +0.0643% | 42 : 3 |
| **SELL legs** | 22 | −0.0271% | −0.0271% | 0.0002 | −0.0274% | −0.0266% | 22 : 0 |
| `pnl` legs | 25 | −0.0294% | −0.0270% | 0.0213 | −0.1281% | −0.0068% | 25 : 0 |

**≈3 bp, not 15 bp**, and it varies day to day (−0.128% to +0.064%) tracking GBPUSD's own drift
between the close the system used and the trade-date close — not a fixed wedge.

**The SELL row's sd of 0.0002 is not evidence of consistency**: all 22 SELL legs are from a single
date, so they share one rate by construction. Stated because it would otherwise read as a
suspiciously tight one-directional signature.

---

## 3. Direct evidence: T212 reports no fee field at all

Checked, since direct evidence beats derivation.

- **Order records**: `invest_orders` has no fee, commission, charge or conversion-rate column.
- **Raw broker response**: the only place T212's own numbers are stored verbatim is
  `system_metadata.t212_positions_snapshot`. Fields returned: `averagePrice`, `currentPrice`,
  `frontend`, **`fxPpl`**, `initialFillDate`, `maxBuy`, `maxSell`, `pieQuantity`, `ppl`, `quantity`,
  `ticker`. **No fee, commission, charge or rate field.**
- **Codebase-wide grep** for `fee|fees|commission|charge|conversion|fxFee`: zero hits outside
  comments and one unrelated CLAUDE.md string. **Nothing captures a fee anywhere.**
- **`fxPpl` is not a fee** and is read by no code (grep: zero hits). It is FX *translation* P&L on an
  open position — the mark-to-market effect of GBPUSD moving since entry. Across 20 open positions it
  sums to **−£321.69** against `ppl` of **+£1,171.91**, and is negative on **20/20**, consistent with
  sterling having strengthened (1.3487 → 1.3648 over the period) rather than with any per-trade charge.
- **`last_gbpusd_rate` / `last_gbpeur_rate` / `last_usdjpy_rate`** in `system_metadata` look like
  broker rates but **have no writer anywhere in the current codebase** — V1-era leftovers. The stored
  `last_gbpusd_rate = 1.3428` matches no recent close (latest is 1.3648), consistent with being stale.
  **Not usable evidence.**

---

## 4. Which of the three pre-stated readings applies

**Reading 2, in a strengthened form.** The request framed it as "differences near zero ⇒ the demo is
not charging FX, in which case this data cannot answer the question." The differences *are* near zero,
but the reason is one step further back: **the stored GBP amount never contained broker FX information
in the first place.** So this is not "the demo doesn't charge, so we can't tell" — it is "this field
is structurally incapable of answering the question, whether or not the demo charges."

Readings 1 and 3 are both ruled out: not clustered near 0.15% on each leg (Test 2 — BUY and SELL
coincide), and not contaminated by spread (Test 1 — the within-day spread is 0.096 bp, far too tight
for market microstructure).

---

## 5. Caveats, as required

- **Demo, not live.** Everything here is the demo account (`trading212_invest.live: false`). Even had
  the method worked, it would be evidence about the demo only. **The demo is confirmed to mirror
  Invest (§6)**, so it is at least the right product — but T212's practice mode is a simulation, and
  whether it applies FX charges identically to a funded live account is untested and untestable from
  here.
- **Daily close ≠ intraday fill rate.** `GBPUSD=X`'s close is not the rate at 14:30 fill time, so tens
  of basis points of genuine noise are expected. This does not affect the conclusion, which rests on
  within-day *cross-instrument* identity and on BUY/SELL coincidence — both immune to that noise.
- **`estimated_value_gbp` is unusable for this purpose**, per §1, and this is now confirmed rather
  than suspected.
- **Test 3's lag is not exactly recoverable.** The implied rate matches 2026-08-01's close to 0.002 bp
  for the 08-03 batch, but later batches sit 2.7–12.8 bp from any stored close. The likely reason is
  that `GBPUSD=X`'s daily bar is *revised* by later ingestion, so the value present when
  `poll_placed_orders()` ran no longer exists in `price_data`. **Unverifiable from this data**, and
  stated as a limit rather than asserted.

---

## 6. ACCOUNT TYPE — confirmed Invest (practice mode), not ISA and not CFD

Checked rather than assumed, since a demo mirroring a different product would carry a different fee
model and would be the wrong thing to attribute a discrepancy to.

**Confirmed Invest, from the API key's own provenance.** T212's trading API is available **only for
Invest accounts in practice mode**, and the key is obtained by logging into the **Invest account**,
switching to practice mode, and opening Settings → API (beta). The credential this system
authenticates with could therefore only have been generated from an Invest account.

**CFD is ruled out structurally.** Every endpoint the client uses sits under
`https://demo.trading212.com/api/v0/**equity**/…` (`brokers/trading212.py:339-342`). CFD is a separate
product with a different cost model entirely — spreads and overnight funding, no per-trade FX
conversion — so if the demo mirrored CFD, the whole cost model would be wrong in kind, not degree.
It does not.

**The API itself carries no account-type field**, so it cannot discriminate Invest from ISA directly —
verified by a read-only GET rather than inferred:

```
GET /equity/account/info  -> {"id":48964347,"currencyCode":"GBP"}
GET /equity/account/cash  -> {"free":16927.99,"total":52261.50,"ppl":1171.91,
                              "result":1267.44,"invested":34161.60,"pieCash":0,"blocked":0}
```

**This distinction is immaterial for FX in any case**: Invest and ISA carry the **same 0.15% FX fee**.

> **CORRECTION 2 (2026-08-22) — "the ISA carries a platform fee the Invest account does not".**
> Recorded as a correction rather than silently dropped, per house convention. **T212 charges no ISA
> platform fee either** — current published sources list no platform, account, inactivity or
> withdrawal fee on Invest *or* ISA. The instruction to verify against the **Invest** schedule
> specifically was nonetheless correct and was followed (§7); the stated reason for it was not. **The
> substantive conclusion is unaffected in either direction**, because the two products carry the
> identical 0.15% FX fee and no ISA figure is relied on anywhere in this report.

---

## 7. The INVEST fee schedule, verified specifically

**Trading 212 Invest, 2026 — confirmed current:**

| charge | Invest |
|---|---|
| Commission on stocks/ETFs | **£0** |
| Platform / account fee | **none** |
| Inactivity fee | **none** |
| Withdrawal fee | **none** |
| **FX conversion** | **0.15%**, applied to the interbank rate with no additional spread markup |

> **CORRECTION 1 (2026-08-22) — "the 0.15% figure recorded in config.yaml's comments and CLAUDE.md".**
> Recorded as a correction rather than silently edited, per house convention. **The figure did not
> originate there, and `config.yaml` does not contain it at all** — a direct grep of `config.yaml` for
> `0.15`, `fx fee`, `fx_fee` and `conversion` returns **no matches**; its only FX-related lines are the
> `GBPUSD=X` / `EURUSD=X` `reference_tickers` entries. The figure was established by the **2026-08-06
> FX denomination audit** (`research/fx_denomination_audit_2026-08-06/REPORT.md`), which verified it
> against T212's published terms and quotes the help centre verbatim — *"the value of the trade will
> be converted to the stock's currency using the spot exchange rate + 0.15% FX fee."* In code it lives
> only as `FX_ROUND_TRIP_FEE = 0.0030` in research scripts. **The premise's second half was right**:
> it had never been checked against order data — and §1–§3 show it cannot be.

**So the 0.15% rate is well-supported for this account type. It is the rate that was confirmed by
published terms; §8 asked whether it is *applied* the way the models assume — now RESOLVED, see the
closing note below.**

---

## 8. The question this audit actually opens — and it is SHARPER for Invest than for ISA

The fee is charged on a **currency conversion**, not on a trade. And **Trading 212 Invest offers a
multi-currency wallet holding balances in 13 currencies including USD** — so an Invest account funded
in USD can trade US stocks **without paying the FX fee at all**.

**This is more live for Invest than it would have been for an ISA**, which holds cash in sterling only
and therefore forces a conversion on every US trade. The correction to account type makes this
question more important, not less.

**Every cost model in this project charges `Σ(position_gbp) × 0.0030` — 0.15% each way on every
trade's notional — which assumes every trade round-trips GBP→USD→GBP.** With a USD wallet in use, that
would overstate FX cost substantially.

**Evidence for this specific account, stated as inference rather than proof:** it is GBP-denominated
(`currencyCode: "GBP"`) and `/equity/account/cash` returns a **single undifferentiated `free`
balance** with no per-currency breakdown — consistent with GBP-only, i.e. each US trade does convert
and 0.15%/leg does apply per trade. But that endpoint may simply not expose sub-balances, so it is
not conclusive. **This is the one open item worth resolving before any future study leans on the
absolute cost figure**, and it is checkable directly in the T212 web platform's currency settings.

> ### RESOLVED 2026-08-22 — CLOSED IN THE LESS FAVOURABLE DIRECTION
>
> Settled by direct observation of the T212 practice-mode **Invest** app's own order cost breakdown —
> the route §3 identified as the only one available, since the API exposes no fee field and §1–§3
> proved the stored GBP amounts are circular.
>
> **Observed** (Moderna market buy, 40 shares, shares value ~£4,278.36, displayed FX rate 0.7336,
> **account default buying/selling currency = GBP**):
>
> | | entry | exit |
> |---|---|---|
> | commission | £0.00 | £0.00 |
> | **FX fee** | **~£6.40** | **~£6.42** |
> | SEC fee | — | ~£0.09 |
> | FINRA fee | — | ~£0.01 |
> | custody / platform (3-yr) | £0.00 / £0.00 | — |
> | third-party inducements | £0.00 | — |
>
> **£6.40 / £4,278.36 = 0.1496% — 0.15% to rounding — charged on BOTH legs. Round trip ≈ 0.30% of
> notional. `FX_ROUND_TRIP_FEE = 0.0030` is CONFIRMED.**
>
> **The multi-currency-wallet hypothesis does NOT apply to this account.** The default
> buying/selling currency is GBP, so each trade converts and the fee applies per leg exactly as the
> models assume. **The models do not overstate FX cost.** Every cost-adjusted figure in the project
> stands as computed, and the "beats buy-and-hold" arithmetic does **not** improve — the exit sweep
> clearing `^SP500TR` at one of seven end dates stands as reported. **This is the unfavourable
> resolution of the two possibilities, and it is stated as such** rather than left ambiguous.
>
> **Two charges the models omit, deliberately**: SEC ~£0.09 and FINRA ~£0.01, exit legs only, ~£0.10
> combined = **~0.23 bp of notional, roughly 1/128th of the £12.82 round-trip FX charge**. **Judged
> immaterial and NOT added** — the effect on any cost-adjusted CAGR is far inside the ~1.08pp noise
> floor. A decision, not an oversight.
>
> **Confirmed zero**: commission, custody, platform, third-party inducements. **FX is effectively the
> entire cost structure for this account.**

---

## 9. Bottom line

| question | answer |
|---|---|
| Which account type? | **Invest, practice mode** — confirmed from the API key's provenance and the `/equity/` namespace. Not ISA, not CFD (§6). |
| Is the fee being charged, per stored order data? | **Unanswerable.** The data is circular by construction (§1–§3). |
| Is the fee being charged **at all**? | **YES — confirmed**, by direct observation of T212's own order cost breakdown (§8 RESOLVED). |
| Is the 0.15%/leg **rate** correct for Invest? | **Yes.** £6.40/£4,278.36 = **0.1496%**, charged on both legs. |
| Is the cost model **confirmed**? | **YES.** `FX_ROUND_TRIP_FEE = 0.0030` is correct in both rate and per-trade application. |
| Does this change any study's ranking? | **No.** Both arms in every comparison pay the same modelled cost. |
| Does it affect "beats buy-and-hold"? | **No — it does not improve.** The unfavourable branch resolved: the exit sweep clearing `^SP500TR` at one of seven end dates stands as reported. |

**Blast radius is bounded**: `FX_ROUND_TRIP_FEE = 0.0030` appears **only in research scripts** — never
in `config.yaml`, `analysis/`, or any live path. **It needed no edit, because it was already right.**

**The audit's own headline is superseded on the substantive question and stands on the methodological
one.** "Unanswerable" remains exactly right *about stored order data*, and §1–§3's proof of that is
unchanged and is what forced the question to the broker's UI. The fee question itself is now answered.

## 10. Files

`step1_export.py` (read-only production export), `step2_analyse.py`, `step3_decisive.py`,
`fx_orders.csv`, `fx_pnl.csv`, `fx_gbpusd.csv`, `fx_eurusd.csv`, `fx_positions_snapshot.json`.

**Sources**: [Trading 212 Help Centre — FX fee](https://helpcentre.trading212.com/hc/en-us/articles/360018909758-What-is-the-FX-fee-Invest-Stocks-ISA) ·
[Trading 212 API key (help centre)](https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key) ·
[T212 Community — Equity Trading API in Practice Mode](https://community.trading212.com/t/new-equity-trading-api-in-beta-try-it-out-in-practice-mode/61788) ·
[BrokerChooser — Trading 212 Fees 2026](https://brokerchooser.com/broker-reviews/trading-212-review/trading-212-fees) ·
[Trading 212 Fees (2026): 0.15% FX Fee & Real Costs](https://quantroutine.com/brokers/trading-212-fees-explained/)
