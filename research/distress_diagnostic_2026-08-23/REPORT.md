# DIAGNOSTIC A — do historical losing trades contain identifiable distress at entry? (2026-08-23)

**ANSWER: no — and the reason is more decisive than a weak correlation. Losing trades are almost
never in distressed names in the first place.** A ticker removed from the index within 12 months of
its exit accounts for **1.6% of trades and 2.57% of total losses** (Pool 1: 0.4% / 0.28%); a ticker
whose price series stops within 12 months accounts for **0.2% and 0.24%** (Pool 1: zero trades). The
losses in this system are ordinary momentum reversals in companies that went on existing and went on
being index members. **There is no distress population in the loss book to detect.**

Of the nine entry-time proxies, **six are NULL at |Spearman| < 0.05** and were not sliced further,
per the pre-committed rule. Two clear the threshold marginally and fail their robustness checks or
point the wrong way. **One clears everything — membership tenure at entry — and it is not a distress
measure**; it is reported as an incidental finding with its confounds quantified, and no filter is
proposed from it.

Read-only. No arms, no engine, no adoption, no fix proposed, no database writes, no config or code
change, nothing deployed.

---

## THE HONEST LIMIT, stated first because it bounds everything below

**No historical filings data exists in this project.** There is no leverage, no interest coverage, no
going-concern qualification, no credit rating, no debt maturity wall, no auditor opinion — nothing a
credit analyst would actually use. Every measure below is **PROXIED from stored price and membership
data**, which is the only historical record this system has.

**A null therefore does not close the question. It closes it for these proxies.** A company can be in
genuine financial distress while its share price is doing nothing unusual, and this diagnostic would
never see it. What it can say is: *distress that is visible in price behaviour or index membership
does not explain this system's losses* — and, from Group I, *the losses are overwhelmingly not in
names that subsequently failed*, which is a stronger and more direct statement than any of the
correlations.

---

## Method

**Dataset**: the preserved trade sets for the **current live configuration** —
`research/exit_mechanics_prereg_2026-08-11/trades_{pool}_s3.0.csv`. **Pool 2 CLEAN decisive: 565
trades, 163 winners / 402 losers, £449,405 total gains against £100,856 total losses.** **Pool 1
alongside: 522 trades, 154/368, £526,285 / £97,177.** Used exactly as preserved; **no backtest was
re-run.** Every proxy is a read-only derivation from `price_data` and `universe_membership`
(`price_data` through 2026-08-07). Momentum-at-entry, used only in robustness check 4, is recomputed
with the production function the engine itself calls (`analysis.signals.compute_momentum_series`).

**All nine proxies were defined in `diagnostic_a.py`'s module docstring before any of them was
computed**, including the five additional ones the instruction asked to be stated in advance. They
split into two groups, and the split matters:

**Group I — DIAGNOSTIC ONLY.** Uses information from after the entry, so it can say whether a trade
was in a name heading for trouble but can never support an entry filter.

| | definition |
|---|---|
| `removal_3m` / `_6m` / `_12m` | the ticker's last real membership `end_date` (excluding the 2099-12-31 still-active sentinel) falls within 3/6/12 calendar months **after** the exit |
| `data_stop_12m` | last `price_data` row within 12 months after the exit **and** ≥30 days before the table's global max — i.e. the series genuinely stopped rather than the sample ending |

**Group II — ACTIONABLE.** Computable at entry.

| | definition |
|---|---|
| `vol20_ann`, `vol60_ann` | annualised stdev of daily log returns over the 20 / 60 rows ending at entry |
| `vol_change` | `vol20/vol60 − 1` — volatility rising into the entry |
| `below_peak_pct` | `(max close over [t−252, t] − close(t)) / max × 100` — **exactly** the stale-peak diagnostic's definition, window bounded by the momentum calculation's own two anchors |
| `worst_day_60` | most negative single-day return in the 60 rows ending at entry — jump risk a volatility average hides |
| `down_up_vol_ratio` | semi-deviation of negative returns ÷ semi-deviation of positive returns, 60 rows |
| `entry_price_usd` | raw close at entry — the crudest possible distress proxy, included deliberately as a control: if it beats the constructed measures, the constructed measures add nothing |
| `volume_ratio` | mean volume last 20 rows ÷ mean last 252 — attention/news spike |
| `membership_tenure_td` | trading rows between the start of the membership window covering the entry and the entry itself |

**Flag rule for (c) and (d)**, fixed with the definitions: binary proxies flag on themselves;
continuous proxies flag on the **quintile in the hypothesised distress direction** (top for
`vol*`/`below_peak`/`down_up`/`volume`, bottom for `worst_day`/`price`/`tenure`), so every continuous
proxy fires on ~20% of trades by construction and the loss/gain shares are directly comparable.

---

## (a) and (b) — distributions and trade-level correlation, Pool 2 CLEAN

| proxy | winners median | losers median | **Spearman vs pnl_pct** | verdict |
|---|---|---|---|---|
| `vol20_ann` | 49.38 | 46.12 | +0.0127 | **NULL** |
| `vol60_ann` | 48.54 | 48.25 | −0.0108 | **NULL** |
| `vol_change` | +0.020 | −0.010 | +0.0366 | **NULL** |
| `below_peak_pct` | 17.45 | 13.67 | **+0.0538** | clears — *wrong direction* |
| `worst_day_60` | −8.06 | −7.59 | +0.0229 | **NULL** |
| `down_up_vol_ratio` | 1.011 | 1.002 | **−0.0646** | clears |
| `entry_price_usd` | 42.21 | 38.00 | −0.0363 | **NULL** |
| `volume_ratio` | 1.014 | 0.988 | +0.0174 | **NULL** |
| `membership_tenure_td` | 661 | 504 | **+0.2418** | clears |

Pool 1 agrees on every NULL and reproduces the sign on `down_up_vol_ratio` (−0.0651) and
`membership_tenure_td` (+0.2822); `below_peak_pct` falls to +0.0331 there, i.e. does not clear on
Pool 1. `worst_day_60` clears on Pool 1 only (+0.0593) against +0.0229 on the decisive pool — a
one-pool marginal crossing in a proxy that is NULL where it counts, so it is reported as NULL and not
sliced.

**Six of nine are NULL and were not sliced for structure.** The winners-vs-losers medians make the
same point without a correlation: the distributions essentially coincide. Losers are **not** more
volatile at entry (48.25 vs 48.54 annualised — a 0.3-point gap on a 48-point base), **not** cheaper
(38.00 vs 42.21), **not** carrying worse recent single-day shocks (−7.59 vs −8.06 — the *winners* are
marginally worse), and **not** entered on a volume spike (0.988 vs 1.014).

---

## (c) and (d) — how often each fires, and what share of gains and losses it catches

Pool 2 CLEAN. A useful proxy would show a loss share well above its gain share.

| proxy | fires | % of trades | **% of total losses** | **% of total gains** | net P&L of flagged |
|---|---|---|---|---|---|
| `removal_12m` | 9 | 1.6% | **2.57%** | 0.01% | −£2,535 |
| `removal_6m` | 3 | 0.5% | 0.30% | 0.00% | −£298 |
| `removal_3m` | 1 | 0.2% | 0.00% | 0.00% | +£5 |
| `data_stop_12m` | 1 | 0.2% | 0.24% | 0.00% | −£246 |
| `vol20_ann` (top 20%) | 113 | 20.0% | 13.63% | 25.52% | +£100,954 |
| `vol60_ann` | 113 | 20.0% | 13.54% | 26.17% | +£103,971 |
| `vol_change` | 113 | 20.0% | 18.98% | 22.32% | +£81,169 |
| `below_peak_pct` | 113 | 20.0% | 16.20% | 18.05% | +£64,794 |
| `worst_day_60` | 115 | 20.4% | 17.52% | 19.26% | +£68,896 |
| `down_up_vol_ratio` | 113 | 20.0% | **27.60%** | 17.79% | +£52,126 |
| `entry_price_usd` | 113 | 20.0% | 12.09% | 14.31% | +£52,129 |
| `volume_ratio` | 113 | 20.0% | 18.37% | 19.18% | +£67,671 |
| `membership_tenure_td` | 114 | 20.2% | **33.75%** | 16.93% | **+£42,025** |

**The volatility proxies are inverted and it is not marginal**: the top-volatility quintile carries
**25.5% of gains against 13.6% of losses**, and flagging it out would remove **£100,954 of net
profit**. This is the VaR sizing mechanism visible in the outcome data — a volatile name is sized
smaller (SNDK's £828 against a £2,564 ceiling is the worked example in CLAUDE.md), so high volatility
does not translate into large losses, while its right tail still pays.

**Every single continuous proxy's flagged bucket is net profitable, including the two that clear the
correlation threshold.** `membership_tenure_td`, the best-discriminating proxy in the study at
2.0× loss-to-gain share, still nets **+£42,025**. **Nothing here identifies a subset worth removing**,
which is a separate and stronger statement than "nothing here correlates."

---

## The three that cleared 0.05, and what the robustness checks did to them

Per the pre-committed rule, only these three were sliced.

### `below_peak_pct` — clears on one pool, points the OPPOSITE way, and is the third replication of a standing result

+0.0538 (Pool 2) / +0.0331 (Pool 1), and the sign means **deeper below its trailing peak at entry →
BETTER outcome**, the reverse of the distress hypothesis. Strengthens excluding 2008 (+0.0751) and
from 2015 (+0.0798); Spearman against momentum-at-entry −0.2141, so not a momentum proxy. Bucket
outcomes improve from Q2 onward but are **not monotone across all four** — medians −5.51 → −6.99 →
−3.23 → −2.65, win rate 28.9% → 19.2% → 32.6% → 34.8%, both dipping at Q2 before rising. Reported as
measured rather than described as monotone.

**This is the stale-peak diagnostic's finding reproduced on a completely different trade set** — that
study measured the 4,876-trade relative-rank set, this measures the 565-trade current-live set — and
it now stands as the **third** independent confirmation of the standing result that entry information
does not predict outcome and, where it weakly does, points against the intuition that motivated
looking. It is not evidence of distress; it is evidence against it.

### `down_up_vol_ratio` — clears on both pools and is a 2008 artefact

−0.0646 / −0.0651, consistent sign across pools, which initially looks like the one genuine
actionable signal. It does not survive:

| | full | excl. 2008 | 2015 onwards |
|---|---|---|---|
| Pool 2 CLEAN | −0.0646 | −0.0466 | **−0.0190** |
| Pool 1 | −0.0651 | **+0.0020** | **+0.0216** |

**Removing one year takes Pool 1 to exactly zero and flips its sign; restricting to 2015+ leaves both
pools inside the null band.** Within-year Spearman never exceeds ±0.11 in any year with n ≥ 30. A
downside-skewed return distribution at entry predicted worse outcomes **in 2008**, which is close to
tautological in a year when almost everything did, and predicts nothing outside it.

### `membership_tenure_td` — clears everything, and is not a distress measure

+0.2418 (Pool 2) / +0.2822 (Pool 1), by far the largest effect in the study, in a consistent
direction: **shorter membership tenure at entry → worse outcome.** The bucket **medians are monotone
across all four quartiles** (−8.28 → −5.25 → −2.42 → −1.14); **win rate is not** — 25.9% → 14.2% →
34.3% → 41.1%, dipping at Q2 exactly as `below_peak_pct` does, so the monotone claim holds for the
median only. It survives dropping each bucket's largest winner (means ex-top-1: +4.52 / −2.97 / +5.63 /
+30.87), survives 2015-onwards unchanged (+0.2417), and is **not** a momentum proxy (Spearman against
momentum-at-entry +0.0564).

**A fifth check was added, stated as an addition rather than folded in with the four**, because
tenure is mechanically bounded by how long the dataset has existed and is therefore a candidate
calendar variable. It was needed:

- **Spearman(tenure, entry date) = +0.4593 / +0.5053.**
- **63.7% of trades (Pool 1: 66.3%) have their membership window starting at or before the ticker's
  first price row**, so their "tenure" is not tenure at all — it is truncated at the data start and
  reads as *trading days since 2007-01-03*.
- The shortest-tenure quartile is **40% 2008 entries and 49% 2008–09**; the longest-tenure quartile
  contains **zero** trades from 2008–2010.
- Excluding 2008 roughly halves it: +0.2418 → **+0.1263** (Pool 1: +0.2822 → +0.1327).
- The bare calendar control — days since 2007-01-03 against `pnl_pct` — is itself **+0.1839 / +0.2259**.

**But it is not purely calendar, and the split is decisive.** Restricted to the trades where tenure is
genuine (membership window starting strictly after the ticker's first price row), tenure's correlation
with the calendar collapses to +0.266 while its correlation with outcome holds:

| subset | n | Spearman vs `pnl_pct` | Spearman vs entry date |
|---|---|---|---|
| Pool 2, truncated (tenure = calendar clock) | 360 | +0.2759 | **+0.8937** |
| **Pool 2, genuine tenure** | **205** | **+0.2702** | +0.2660 |
| Pool 1, truncated | 346 | +0.3531 | +0.8924 |
| **Pool 1, genuine tenure** | **176** | **+0.2396** | +0.2631 |

**So there is a real effect: recently-added index members perform worse in this system, and it is not
an artefact of the crisis era.** It replicates in both pools, in the subset where the variable means
what it says, and within individual years (2008 +0.227, 2009 +0.117, 2022 +0.190, 2023 +0.300 on Pool
2).

**It is nevertheless outside this diagnostic's question, and the mechanism is why.** A company
recently added to the S&P 500 is not a distressed company — it is typically one that has just grown
into the index, and it is by construction among the smallest and newest constituents. The plausible
mechanisms are size, index-inclusion crowding, or a shorter operating record; **none of them is
distress**, and this diagnostic has no data that could separate them. **No filter is proposed**: the
flagged quintile nets **+£42,025** despite carrying 2× its share of losses, so removing it removes
profit, and a proxy whose best available explanation is "small, new index constituents" is the
beginning of a different study, pre-registered on its own terms, not a conclusion from this one.

---

## Conclusion

**On the question asked: no.** Group I settles it more directly than any correlation could —
**index removal within 12 months of exit touches 1.6% of trades and 2.57% of losses; price-data
cessation touches 0.2%.** The loss book is not made of failing companies, so there is no distress
population at entry for a proxy to find. Six of nine entry-time proxies are NULL and were not sliced;
the volatility family is inverted, and every flagged bucket in the study is net profitable.

**A fourth entry feature joins the standing list.** Momentum magnitude (selection audit Part 2,
Spearman −0.033), path smoothness (NOT ADOPTED), peak drawdown at entry (Spearman −0.028, replicated
here at +0.054 on a different trade set) and now distress proxies all come back the same way. **The
prior for a fifth should be set accordingly**, and this diagnostic adds a reason rather than just
another data point: entry-time price behaviour is the *input* to the ranking that already selected
these names, so the admitted population has been filtered on it by construction — the interesting
variation has largely been removed before the trade exists.

**What remains genuinely open, and it is not rhetorical.** Real distress data — leverage, coverage,
going-concern, ratings — has never been tested here and could not be, retrospectively. The
point-in-time fundamentals recorder shipped 2026-08-12 begins accumulating exactly this class of
data forward; on its own stated timeline that is a multi-year proposition, and its combined
reporting-plus-capture lag of roughly two months is itself a constraint on what it could ever answer.
**This diagnostic should be read as closing the price-and-membership-derived version of the question,
and as one more reason the fundamentals recorder was worth starting.**

---

## Files

`diagnostic_a.py` (proxy definitions and computation), `step2_robustness.py` (the four checks plus the
added calendar check), `diagnostic_a.json`, `step2_robustness.json`, `step3_tenure_confound.json`,
`proxies_{pool}.csv`, `proxies_{pool}_with_momentum.csv`.

Read-only throughout: no database writes, no config or code change, nothing adopted, nothing deployed.
