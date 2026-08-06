# Exploratory Hypothesis Pass — Candidate Ideas Only, Nothing Tested (2026-08-05/06)

**This is not a finding.** No reset-at-boundary run, no block validation, no p-value, no significance claim, no recommendation to adopt anything was made or computed. This is a read-only pass over already-computed fields in tonight's twelve preserved trade-level outputs, looking for plausible, structurally-motivated candidates worth a properly-scoped investigation later. Where a pattern was checked and found to be an artifact rather than a real candidate, that is reported too, since ruling something out is itself useful signal for whoever picks this up next.

## 0. Data actually loaded and used

All twelve investigations' write-ups were read (ten `REPORT.md` files, plus the two pre-`REPORT.md` investigations' sections in `CLAUDE_HISTORY.md`: "Deployment-cap accounting — mark-to-market vs cost-basis" at line 8264, and "Price-sanity threshold — momentum-skew investigation" at line 8011).

**Primary population used for the new computation in this pass**: the 3,161-trade continuous-walk trade list at `research/concentration_risk_2026-08-05/results/full_trades_continuous.json` (`simulate_portfolio_compounding_mtm()`, live 503-ticker universe, mark-to-market, 2007-01-01→2026-08-03, current `config.yaml`). This is confirmed the same underlying run four separate investigations independently reproduced to the exact trade/exit-reason count (`concentration_risk`, `momentum_horizon`'s `all_trades_full_run.json`, `dynamic_momentum_cap`, `stop_cluster_coordination` all report identical totals: 3,161 trades, MAX_HOLDING 1,796/STOP_LOSS 1,169/SIGNAL_OFF 131/CRISIS_EXIT 35/END_OF_DATA 30) — so treated as one population, not blended across configs. Every computation below is a transformation already licensed by the task (day-of-week/month derived from `entry_date`/`exit_date`, exit-reason breakdowns, ticker-list membership using tickers already named in tonight's own reports) — no new indicator, no new price series, nothing engineered.

**Not blended with variant-arm data**: `momentum_sizing_multiplier`'s aggressive/modest/whole-distribution arms, `momentum_lookback`'s non-12m arms, `risk_adjusted_momentum`'s risk-adjusted arm, `deployment_cap_tradeoff`'s 8 non-baseline grid cells, and `curated40_rolling40_dax_reverify`'s rolling-40/full-500/DAX populations are all genuinely different configs/universes/engines from the 3,161-trade baseline and from each other — used only for their own reports' own already-stated findings below, never pooled into the day-of-week/month computation.

**Two files used only for their own write-ups' findings, not re-mined for new computation**: `price_sanity_sweep_2026-08-03` (a different, 16-block reset-at-boundary convention from the continuous-walk baseline — its `headline_skips_full.csv` was checked once, for a cluster-ticker skip-share cross-reference that did not surface a standalone candidate) and `deployment_cap_mtm_vs_costbasis_2026-08-04`.

## Candidates, ranked by plausibility and support

### 1. December-entry positions survive to the 30-day cap at a materially higher rate than July-entry positions — a real seasonal split in exit-reason *mix*, not magnitude, that holds across two of the three market eras (strongest candidate)

**What was observed.** Splitting the 3,161-trade population by `entry_date` month and looking at exit-reason composition (not P&L magnitude):

| Entry month | n | MAX_HOLDING % | STOP_LOSS % |
|---|---:|---:|---:|
| December | 239 | **66.1%** | 29.3% |
| July | 277 | **45.8%** | 44.4% |

A ~20pp gap in the probability a December entry survives all the way to the 30-day cap (rather than getting stopped out first) versus a July entry. Critically, this is **not** a magnitude effect — within `MAX_HOLDING` trades only, mean `pnl_pct` is flat across every month (5.3%–9.7%, no clean seasonal gradient); within `STOP_LOSS` trades only, mean loss is flat too (-5.9% to -7.4%). The seasonal signal is specifically in *which* exit a position gets, not how good/bad that exit is once it happens.

**Sample size and regime check.** n=239 (Dec) / n=277 (Jul) — not a small-sample fluke on the scale of the Weak-band's 17 trades. Checked across the three market eras this project's culture requires, specifically to avoid the GFC-only-concentration trap that killed both the momentum-horizon Weak band and the dynamic-momentum-cap trend finding:

| Era | Dec MAX_HOLDING% (n) | Jul MAX_HOLDING% (n) |
|---|---|---|
| 2007-2009 (GFC) | 34.3% (n=35) | 39.5% (n=38) — reversed, small n |
| 2010-2019 (bull) | **76.5%** (n=115) | 47.7% (n=130) |
| 2020-2026 (recent) | **65.2%** (n=89) | 45.9% (n=109) |

The gap holds, strongly, in the two largest and most "normal" eras (204 and 198 Dec/Jul trades respectively) — the opposite robustness profile from the Weak-band and dynamic-cap findings, both of which lived almost entirely inside the one small, atypical GFC-era subset. The only era where the pattern doesn't hold is the smallest one (n=35/38) and the one this project's other investigations already treat as structurally atypical.

**Structural rationale.** Maps onto well-documented, independent equity-market seasonality (the "Santa Claus rally"/January effect and broader Q4-into-Q1 seasonal strength; "sell in May"/summer-liquidity weakness feeding into thin August trading). A position entered in December rides into a period markets have independently, structurally tended to be calmer and more upward-biased in; a July entry rides into the historically choppier, thinner-liquidity late-summer window — a mechanistic reason a momentum strategy's stop-out rate specifically (not win magnitude) would vary by entry month.

**What this is not**: no seasonal position-sizing or entry-gate variant was built or run. Worth a properly-scoped reset-at-boundary + 16-block investigation of a specific, narrow rule before any conclusion is drawn.

### 2. Recent-era (2026) correlated-cluster concentration is compounding-amplified — three independent investigations converge on the same mechanism, but none tested a *dynamic*, size-scaling response

**What was observed.** The five-ticker "storage/semiconductor" cluster already named independently in three separate places tonight — `concentration_risk`'s worst-episodes list (SNDK/WDC/MU/LITE/STX, 9 of the 25 worst days, all mid-2026), `momentum_horizon`'s "biggest post-cap continuation" named-trade list (same five tickers), and `stop_cluster_coordination`'s largest single cluster (2026-07-27, 11 members including this same core group) — checked directly against raw trade-frequency and position-size time series:

| Year | Cluster trades / total | Cluster % | Avg position size |
|---|---|---|---|
| 2017 (prior peak) | 20/149 | 13.4% | £5,717 |
| 2026 (partial, through Aug) | 32/139 | **23.0%** | **£17,070** |

Both dimensions — how often this correlated group gets traded, and how large each position is — are simultaneously at or near a 19-year high in the same partial year. Average position size has grown ~8x since 2008 (£2,070→£17,070) purely from compounding, a mechanical amplifier on the dollar impact of any correlated episode independent of any change in stock-picking behavior.

**Sample size.** 139 core-cluster trades across 19 years (32 in the partial 2026 year alone) — corroborated by two independently-computed lists (`concentration_risk`'s day-level worst-episode ranking and `stop_cluster_coordination`'s cluster-detection algorithm), built by different scripts/investigations, converging on the same tickers and window without either setting out to find the other's answer.

**Structural rationale.** `per_trade_pct` sizes every position as a fraction of the *current* (compounded) balance, so a correlated cluster's aggregate dollar exposure scales with account size even if selection behavior toward that cluster is unchanged. `deployment_cap_tradeoff` already tested raising the caps *uniformly*; this candidate is different — a rule that tightens a correlation threshold or cap specifically as the account grows past some multiple of starting capital, rather than a fixed threshold applied while the dollar stakes underneath it silently grow. That "tighten with account growth" framing wasn't tested by anything tonight.

### 3. A sector/ticker-scoped (not blanket-magnitude) concentration constraint might behave differently from the rejected correlation haircut — but real counter-evidence exists and should be weighed, not ignored

**What was observed.** `concentration_risk`'s correlation-haircut (block entries >0.7 correlated with an open position) did not dominate baseline — better drawdown, worse CAGR/PF/Sharpe. Checking the core storage/semi cluster directly: 139 trades, **win rate 52.5% vs. 49.0% for the rest of the population, mean pnl_pct 5.98% vs. 1.70%** — this recurring correlated cluster has historically been one of the strategy's *better*-performing pockets, not a risk-neutral one. Exit-reason mix for the cluster is close to the population average, so it isn't systematically safer either — genuinely both more correlated and higher-returning.

**Sample size.** 139 trades over 19 years; no single trade dominates the mean the way MU/NCLH dominated the Weak-band figure.

**Structural rationale for testing it anyway, stated honestly alongside the counter-evidence.** A blanket, whole-universe correlation threshold also penalizes correlated clusters that are *not* especially high-returning (`momentum_sizing_multiplier` found the 2020-2026 top-momentum book sometimes leaned toward consumer-staples/retail names — COST/DG/WMT/KR/GIS — a defensively-correlated, not cyclical, cluster). A rule scoped specifically to the small, recurring, structurally-high-beta cyclical/semiconductor-supply-chain group is a genuinely narrower intervention than what was tested and rejected — but **the raw win-rate/mean-pnl numbers above are a real reason to expect a targeted block might cost more than it saves, for the identical reason the blanket version did.** Reported as "worth a scoped test" because it hasn't been tried in this narrow form, not because tonight's data makes it look promising — it doesn't. Ranked below candidates 1 and 2 for exactly this reason.

### 4. A modest cross-market (DAX/European) sleeve as a genuinely different diversification lever — reasoned, not measured, the most speculative candidate here

**What was observed.** `curated40_rolling40_dax_reverify`'s DAX re-run showed a real, substantial peak-relative drawdown reduction versus its own benchmark (test max DD -18.56% vs. `^GDAXI`'s -38.78%; train max DD -14.45% vs. -54.77%), using the same 12-month-momentum mechanism against a different, non-overlapping index — a different macro/monetary regime, sector mix, and (unmeasured) currency exposure from the US live universe.

**No correlation between the DAX sleeve's trade sequence and the US live-universe sleeve's trade sequence was ever computed** in any of tonight's investigations — this candidate is explicitly reasoned, not evidenced, unlike candidates 1-3. `concentration_risk`'s whole finding is that the *existing* 500-ticker US universe's VaR sizing already assumes independence it doesn't have; a genuinely separate market, sized as its own small sleeve rather than folded into the same 500-name pool, is a structurally different lever than the correlation haircuts/risk-adjusted ranking tried tonight (both work *within* the single US pool). Ranked last, and flagged most explicitly as speculative, because no already-computed field in tonight's output actually measures the cross-market correlation this idea depends on.

## Checked and ruled out (reported for due diligence, not a candidate)

**Exit day-of-week.** A first look showed a striking spread — Monday exits: 59.9% win rate, +3.67% mean pnl; Tuesday exits: 22.0% win rate, -2.50% mean pnl. Decomposed by exit reason, it fully dissolves: `STOP_LOSS` trades are -6.4% to -6.7% and (mechanically, by construction) 0% win rate on *every single weekday*; `MAX_HOLDING` trades are +7.6% to +8.4% and 82-89% win rate on every weekday. The apparent day-of-week effect is entirely a composition artifact: entries cluster heavily on Monday/Wednesday/Friday (850/772/723 vs. 344/472 on Tuesday/Thursday), and because `MAX_HOLDING` exits land almost exactly 30 trading days after entry, the skewed entry-weekday distribution flows straight through into a skewed exit-weekday exit-reason mix. The exact same failure shape this project's culture is built to catch — recorded here so nobody re-discovers it as "new."

## Explicit confirmations

1. **All twelve investigations' data were loaded and read.** New computation in this pass was run against one population — the 3,161-trade continuous-walk baseline, cross-confirmed identical across four independent investigations' own reproductions — plus one targeted, read-only cross-reference against `price_sanity_sweep_2026-08-03/headline_skips_full.csv` that did not surface a standalone candidate.
2. **Nothing here was tested further, validated, or block-validated.** No reset-at-boundary run, no 16-block check, no p-value, no config/code touched, nothing committed or deployed. Every candidate above is an idea for a future, separately-scoped investigation — none is a finding, none is recommended for adoption.
3. **Ranking, restated**: (1) December-entry seasonal survival split — strongest, cross-era robust, real sample size, literature-grounded mechanism; (2) compounding-amplified recent-cluster concentration — strong convergent cross-investigation evidence, genuinely new "scale with account size" angle untested tonight; (3) sector-scoped concentration constraint — worth a narrow test, but real counter-evidence tempers it; (4) cross-market DAX sleeve — most speculative, reasoned rather than measured.

**Files/paths referenced**: `research/concentration_risk_2026-08-05/results/full_trades_continuous.json` (primary computation source), `research/momentum_horizon_2026-08-05/all_trades_full_run.json`, `research/stop_cluster_coordination_2026-08-05/REPORT.md` (cluster list), `research/momentum_sizing_multiplier_2026-08-05/REPORT.md` (sector-composition counter-example), `research/curated40_rolling40_dax_reverify_2026-08-05/REPORT.md` (DAX figures), `research/price_sanity_sweep_2026-08-03/headline_skips_full.csv`, `CLAUDE_HISTORY.md` lines 8011-8059 and 8264-8300.
