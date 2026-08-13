# Entry-time feature correlation screen — pre-registration (2026-08-11)

**WRITTEN BEFORE ANY FEATURE WAS COMPUTED.** No correlation, distribution, or bucket table existed
when this file was committed. The thresholds below are applied mechanically afterwards; nothing in
them is adjustable once results exist.

---

## 0. Motivation, and the honest prior

**Three prior investigations of entry-time information have found essentially nothing:**

1. **Selection audit Part 2** — Spearman(momentum-at-entry, `pnl_pct`) = **−0.033** across 3,436
   trades. No monotonic ordering above the bottom decile.
2. **Momentum-smoothness study** — path quality as a candidacy filter, **NOT ADOPTED**. The one arm
   that nominally cleared its bar did so by 0.051pp against a 1.5pp threshold, with the two pools
   disagreeing in sign.
3. **Stale-peak diagnostic** — Spearman(below_peak_pct, `pnl_pct`) = **−0.028**, and what weak
   bucket-level signal existed pointed **opposite** to the hypothesis that motivated the search.

**The consistent finding is that entry information does not predict individual trade outcome**, which
is what one expects if momentum works **distributionally** — as a property of the population of
positions held over time — rather than by identifying which specific trades will succeed.

**This screen's justification is coverage, not expectation**: it examines features not previously
tested, so that "we checked" is a statement about a defined feature set rather than about the three
features that happened to get looked at. **The expected result is another null**, and a screen where
everything returns null is a successful screen.

---

## 1. The interpretation rule, fixed in advance

Applied mechanically, in this order, per feature:

**NULL** if trade-level `|Spearman(feature, pnl_pct)| < 0.05`.

> **A feature reported NULL is NOT then sliced into buckets hunting for structure.** That is
> precisely how an open search manufactures findings: with enough cut points, any feature produces
> some bucket that looks separating. Distribution and correlation are reported for a NULL feature;
> bucket tables are not computed for it.

**WORTH A PROPER STUDY** only if **all** of:

1. `|Spearman| >= 0.05` at the trade level, **and**
2. **monotonic ordering across quintiles** (mean `pnl_pct` strictly ordered, in either direction),
   **and**
3. survives **all four** robustness checks used by the stale-peak diagnostic:
   - dropping each quintile's single largest winner,
   - excluding 2008,
   - restricting to 2015 onwards,
   - conditioning on momentum quartile (effect present within quartiles, not a momentum proxy).

Anything clearing (1) but failing (2) or (3) is reported as **INCONCLUSIVE**, explicitly not as a
finding.

---

## 2. The held-out period, declared before computing

**All screening is computed on PRE-2022 trades only** (`entry_date < 2022-01-01`). **2022-01-01
onwards is held out** and is not examined until a feature has already cleared §1 on the pre-2022
data.

**A feature that appears pre-2022 and vanishes in the held-out period is noise, and is reported as
noise.** The held-out check is confirmatory only — it cannot promote a feature that failed §1, and a
feature that fails §1 pre-2022 is not rescued by looking better post-2022.

Approximate split (stated now, exact counts reported with results): the primary trade set spans
2008–2026, so the hold-out is roughly the final quarter of the sample.

---

## 3. Multiple comparisons — stated as the reason the thresholds exist

With ~4,900 trades and ~15 candidate features, **something will appear to separate winners from
losers by chance alone**. At |Spearman| ≥ 0.05 with n≈3,600 (pre-2022), the two-sided p-value is
roughly 0.003 for a single test — but across 15 features the probability that at least one crosses
that line by chance is substantial. **The 0.05 threshold is therefore a screening floor, not a
significance claim**, and conditions (2) and (3) plus the hold-out exist specifically so that a
chance crossing does not get reported as a discovery. **p-values are reported for completeness and
are NOT used as the decision rule.**

---

## 4. Features to compute

All derived read-only from stored OHLCV, at the entry date, using production functions where they
exist (`analysis.volatility._atr`, `analysis.signals.*`).

**Volume (4):**
- `adv20`, `adv60` — mean daily volume over trailing 20 / 60 trading days
- `vol_ratio_entry` — entry-date volume ÷ `adv20` (is the entry day itself unusual?)
- `vol_trend` — `adv20 ÷ adv60` (is volume expanding or contracting within the lookback?)
- `dollar_volume20` — `close × adv20`, **a LIQUIDITY PROXY, not a substitute for market
  capitalisation or index weight**, neither of which is stored and neither of which is obtainable
  point-in-time for free. This limitation is stated in the report, not worked around.

**RSI (1):**
- `rsi14` — standard 14-day Wilder RSI at entry.
  **Expected problem, stated in advance**: candidates are selected for large trailing gains, so
  nearly everything should read overbought, and RSI is mechanically related to the momentum already
  being ranked on. **The distribution is reported FIRST. If spread is small (pre-registered as
  interquartile range < 15 RSI points, or >80% of trades inside a 20-point band), the feature is
  declared UNINFORMATIVE BY CONSTRUCTION and no correlation is interpreted from it** — reporting a
  weak correlation on a degenerate variable would be misleading.

**Volatility (2):**
- `atr_pct` — ATR ÷ close at entry (the same ATR the production stop uses)
- `realised_vol60` — annualised stdev of daily log returns over trailing 60 days

**Price level (1):**
- `price_at_entry` — absolute share price (crude size proxy, acknowledged as crude)

**Additional features, stated with justification before computing** (per the task's invitation):
- `gap_to_ma50`, `gap_to_ma200` — `close ÷ MA − 1`. Justification: extension above a moving average
  is the most common practitioner "overextended" measure and is not implied by the two-point momentum
  value; the MA-extension-ceiling investigation touched this at the *gate* level but never as a
  trade-outcome correlation.
- `ma50_above_ma200` — boolean trend-structure flag ("golden cross" state). Justification: a
  structural regime descriptor orthogonal to trailing return magnitude.
- `up_day_fraction60` — share of up days in the trailing 60 days. Justification: a *short-horizon*
  consistency measure; the smoothness study tested consistency over the full 252-day window, never
  over the recent window alone, so this is genuinely untested.
- `return_5d`, `return_21d` — trailing 5- and 21-day return at entry. Justification: short-horizon
  reversal is the best-documented effect adjacent to momentum, and the 21-day leg is precisely what
  the adopted relative-rank signal *excludes* by construction — so its relationship to outcome is
  both untested here and directly relevant to a live design choice.
- `dist_from_52w_low` — `close ÷ min(close, 252d) − 1`. Justification: the stale-peak diagnostic
  measured distance from the window *high*; distance from the *low* is its complement and was not
  measured.

**16 features total.**

---

## 5. Data and constraints

**Primary**: `research/relative_ranking_prereg_2026-08-09/trades_pool2_clean_B.csv` (4,876 trades,
the currently-live relative-rank ordering). **Reported alongside**:
`trades_pool2_clean_A.csv` (4,919, absolute momentum). Both used exactly as preserved.

**No backtest is re-run.** Features are derived read-only from `price_data`, exactly as the
stale-peak diagnostic did, and the same anchoring check is applied (`entry_price` must equal the
stored close on the entry date for every trade, or the derivation is invalid).

**NOT AVAILABLE and not attempted**: market capitalisation and S&P 500 index weight are not stored,
and point-in-time versions are not obtainable free — the same obstacle that closed the market-cap
filter idea. `dollar_volume20` is a **proxy** for liquidity/size and is labelled as such wherever it
appears.

No arms, no engine, no adoption, no fix proposed, no study designed. Read-only. Dedicated worktree
(`c:\GitHub\investment-app-featscreen`, branch `research/feature-screen-2026-08-11`).
