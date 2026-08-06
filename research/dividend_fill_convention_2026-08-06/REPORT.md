# Dividend adjustment, commissions, and fill-price convention — audit (2026-08-06)

**Question.** Raised by an independent, no-system-context review of the trade
data: positions held up to ~33 days routinely cross ex-dividend dates. If the
backtest's price series is price-only, returns are systematically understated
and some stop-loss triggers may reflect an ex-dividend gap rather than a real
decline. Urgent because an unfavourable answer would affect the validity of
every figure reported on 2026-08-05.

**Answer: the dividend concern is real, small (2.32%), and conservative — it
understates. It does not invalidate any reported figure. A materially larger,
non-conservative problem was found in the adjacent stop-fill convention.**

Read-only investigation. No code or config changed.

---

## Scope and method

Post-hoc analysis of the already-preserved 3,161-trade run at
`research/concentration_risk_2026-08-05/results/full_trades_continuous.json`
— **`backtest_convention: continuous_walk`**, live 503-ticker universe (500
with data), real unmodified `simulate_portfolio_compounding_mtm()`, starting
capital £50,000, `per_trade_pct` 0.05, window end 2026-08-03. Reported
`total_pnl_gbp` £484,476.39, `avg_return_pct` 1.892%.

`analyse.py` does **not** run the backtest engine, construct a candidate pool,
or monkeypatch `analysis.universe.is_member()` — it reads an existing trade set
plus `price_data`. CLAUDE.md's "Research scripts must verify decision-engine
parity" requirement therefore does not apply: there is no pipeline
interception here to verify. What *is* verified is that the trade set is
genuinely the engine's own output, by reproducing every recorded
`entry_price` from `price_data` (below).

---

## Q1 — Price data is split-adjusted, NOT dividend-adjusted

`data/ingestion.py:143`:

```python
kwargs: dict = dict(auto_adjust=False, progress=False, threads=False)
```

Both columns are stored (`ingestion.py:191-192`): `close` (raw / price-only)
and `adj_close` (dividend + split adjusted).

Confirmed against the real data, not inferred from the yfinance default:

* **Splits ARE handled in `close`.** AAPL's 4:1 split (effective 2020-08-31)
  shows no 4x discontinuity: `close` 124.8075 (08-28) → 129.0400 (08-31).
* **Dividends are NOT.** `adj_close/close` drifts exactly as a dividend stream
  implies:

  | ticker | 2010 | 2015 | 2020 | 2024 | 2026 |
  |---|---|---|---|---|---|
  | KO  | 0.6033 | 0.6974 | 0.8209 | 0.9301 | 0.9868 |
  | JNJ | 0.6180 | 0.7282 | 0.8354 | 0.9302 | 0.9890 |
  | XOM | 0.5405 | 0.6156 | 0.7468 | 0.9204 | 0.9867 |

## Q2 — The engine reads `close`; `adj_close` is read by nothing

`grep -rn adj_close --include=*.py .` outside `tests/` returns **only**
`data/ingestion.py` (writes) and `data/database.py:78` (schema). Zero readers.

Every engine, including the live-matching `simulate_portfolio_compounding_mtm()`,
uses `price = float(row["close"])` (`backtest.py:1342`, `1447`, `1516`), and
P&L is computed with no dividend term at all (`backtest.py:1474`):

```python
pnl_pct = (exit_price - entry_price) / entry_price
pnl_gbp = ot["position_gbp"] * float(pnl_pct)
```

The column that would fix this is populated and sitting unused.

## Q3 — Commissions: frictionless by default

The engine models **no** transaction cost. `compute_cost_sensitivity()`
(`backtest.py:1619`) does exist and `run_full_validation()` calls it
(`validation.py:476`), re-reporting metrics at 0/10/25 bps round-trip:

```python
cost_frac = (bps / 10000.0) * 2   # entry + exit
df["pnl_pct"] = df["pnl_pct"] - cost_frac
```

So cost sensitivity is reported alongside — but **all headline figures are the
0-bps ones**.

## Q4 — Fill convention: same-day close

Verified empirically, not only read from the source. Of 3,161 trades:

| check | count | % |
|---|---|---|
| `entry_price == close(entry_date)` | **3,161** | **100.00** |
| `entry_price == open(entry_date)` | 14 | 0.44 (coincidental) |
| `exit_price == close(exit_date)` | 1,991 | 62.99 |

Entries fill at the signal day's close. Exits fill at the same-day close
**except** `STOP_LOSS`, which fills at exactly `stop_price`
(`backtest.py:1471`: `exit_price = stop_price if exit_reason == "STOP_LOSS" else price`).

Note this also differs from live, which submits the next day at 14:30 UTC
(60 min into the session), not at the decision day's close.

## Q5 — Quantified dividend impact

Because the DB holds both series, the dividend contribution is **exact, not
estimated**. For a hold t0 → t1:

```
PR      = close(t1)/close(t0)      # what the engine actually used
TR      = adj(t1)/adj(t0)          # price + reinvested dividends
div_ret = TR - PR                  # additive return the engine drops
impact  = position_gbp * div_ret
```

Ex-dividend detection, with the exact operator and threshold:

```
f(t) = adj_close(t) / close(t)
ex-dividend on date t  iff  f(t) > f(t-1) * (1 + 1e-5)
```

**The 1e-5 threshold was calibrated, not guessed.** For KO over its full
4,926-row history the steps separate cleanly into 78 real quarterly dividends
(every one > 1e-3) and floating-point rounding noise (every one < 1e-6), with
nothing in between; 78 == 4/yr × 19.6 yr of coverage. At 1e-9 the same scan
returns 2,443 "events" for KO alone — an early version of this analysis used
1e-9 and reported a noise-dominated 606,139 events / 70.3% crossing rate,
caught and corrected before any conclusion rested on it.

| metric | value |
|---|---|
| Trades crossing ≥1 ex-dividend date | **429 / 3,161 (13.57%)** |
| Mean dividend return, all trades | 0.0579% |
| Mean dividend return, crossing trades | 0.4265% (≈ one quarterly dividend) |
| **Aggregate P&L understatement** | **£11,230.50** |
| As % of reported £484,476.39 | **2.32%** |
| `avg_return_pct` 1.892% → | **1.950%** |

The 13.57% crossing rate is genuinely explained, not an artefact: median hold
is 30 days but 9.2% of trades exit within 5 days, and only 166 of the 331
traded tickers ever paid a dividend during a hold — momentum selection skews
toward non-payers. Crossing rate by holding bucket: 4.5% (0–5d), 6.8% (6–15d),
17.4% (16–30d), 14.4% (31d+).

### Stop-losses triggered by a price-only artefact

The engine fires `STOP_LOSS` iff `close(t) <= stop_price`, i.e.
`PR <= 1 - stop_distance_pct/100`. The total-return equivalent of that same
test is `TR <= 1 - stop_distance_pct/100`. A stop is contaminated iff:

```
PR <= (1 - stop_distance_pct/100) < TR
```

**24 of 1,169 stops (2.05%; 0.76% of all trades)**, combined recorded P&L
−£10,022.64. Most extreme is AIG 2011-01-19 → 2011-01-20 (the January 2011
warrant distribution): price **−15.367%**, total return **+2.032%** — a stop
fired on a position that, dividends and distributions included, had *risen*.
The rest are ordinary quarterly dividends nudging a marginal stop over the
line (RCL −8.649% vs −8.330% against an 8.38% stop, etc.).

---

## Verdict on the dividend question

**Real, small, and conservative.** ~2.3% understatement; correcting it moves
every reported figure slightly in the strategy's favour. No figure reported on
2026-08-05 needs revisiting on dividend grounds.

---

## The larger, non-conservative finding: the STOP_LOSS fill assumption

Found while verifying Q4. Both effects below run the *same* way — the backtest
**overstates** — and together they are ~20x the dividend effect.

**1. Gap-through: the assumed fill is often impossible.** The engine fills
`STOP_LOSS` at exactly `stop_price`. That cannot happen when the day already
opened below it:

```
gapped through  iff  open(exit_date) < stop_price
unachievable_gbp = position_gbp * (stop_price - open(exit_date)) / stop_price
```

**281 of 1,169 stops (24.04%)**, assuming **£45,799.68** of unachievable gain
— **9.45%** of reported P&L. Worst cases: TMUS 2011-08-02 (35.6%), ULTA
2019-08-30 (23.96%), PANW 2024-02-21 (20.29%).

**2. Late trigger: the daily-close test holds through intraday breaches.** A
real broker-side stop triggers the moment price *touches* the level. The
backtest only compares the close:

```
stop_price = entry_price * (1 - stop_distance_pct/100)
early touch iff  exists d in (entry_date, exit_date): low(d) <= stop_price
delta_gbp = position_gbp * ((stop_price - entry_price)/entry_price - pnl_pct)
delta_gbp < 0  =>  intraday stop yields LESS than recorded  =>  backtest OVERSTATES
```

**751 of 3,161 trades (23.76%)** touched their stop intraday before the
backtest's exit day. Had a real intraday stop fired at the touch, P&L would be
**£166,467.79 lower** — **34.36%** of reported P&L. These are overwhelmingly
non-`STOP_LOSS` trades that dipped to the stop and recovered (for a trade the
backtest already stopped out, exiting earlier at the same `stop_price` is
P&L-neutral by construction).

**Why this matters more now than it used to.** Since 2026-08-04 live places a
**real broker-side stop order** at the ATR stop price the instant a BUY fills
(`poll_placed_orders()` → `_place_broker_stop_for_filled_buy()`). Live will
genuinely exit intraday on touch. So this is no longer a modelling nicety — it
is a live/backtest divergence in exactly the class CLAUDE.md's "one
implementation of any given trading decision" invariant exists to prevent.

**Explicit caveat — first-order only.** The combined ~£212k is *not* a
corrected figure. Neither number re-simulates: an earlier exit frees capacity,
changes subsequent position sizing, and changes compounding. Treat these as
scale and direction, sufficient to justify a dedicated investigation, not as a
restated result.

**Recommendation.** The stop-fill convention, not the dividend gap, is what
warrants the follow-up decision. Not actioned here — no code or config
changed, pending an explicit separate decision.

---

## Reproducing

```bash
python3 research/dividend_fill_convention_2026-08-06/analyse.py
```

Full trade-level output preserved in `results/` per CLAUDE.md's "preserve full
trade-level output, not just the formula" standard, since the figures above
are cited in `CLAUDE_HISTORY.md`:

| file | rows | contents |
|---|---|---|
| `summary.json` | — | every headline figure above |
| `per_trade_dividend_impact.csv` | 3,161 | every trade + `PR`, `TR`, `dividend_return`, `dividend_impact_gbp`, `n_exdiv_crossed` |
| `stop_loss_contaminated_by_exdiv.csv` | 24 | the contaminated stops, price vs total return |
| `stop_loss_gapped_through_open.csv` | 281 | assumed fill vs actual open, unachievable £ |
| `stop_touched_intraday_before_exit.csv` | 751 | first intraday touch date, recorded vs intraday-stop P&L |
