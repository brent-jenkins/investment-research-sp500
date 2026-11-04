# Findings

What eleven weeks of building and testing a long-only S&P 500 momentum
strategy found, from 2026-06-14 to 2026-08-30. The short version: **the
strategy did not beat buying and holding the index, and its apparent
defensiveness was something cash could buy more cheaply.** Two changes made
it clearly better than its own earlier versions, and neither closed the gap.
This file is the summary. `CLAUDE.md` holds the current-state record and
`CLAUDE_HISTORY.md` the full chronological one, with every figure, every
pre-registration and every retraction.

The project was closed after these experiments and superseded by
[investment-research-cfd](https://github.com/brent-jenkins/investment-research-cfd), which tried a short-horizon CFD
strategy instead. Its own findings, also a null result, are in that
repository.

None of this is financial advice. Every trade was placed on a demo account.

---

## What was built

- **A live trading system** on a Trading 212 Invest demo account. It decided
  after the US close, at 21:30 UTC, and submitted orders at 14:30 UTC the
  next day, after re-checking each one's price, crisis state and capacity.
  Every filled position also got a stop order held at the broker.
- **A point-in-time universe.** Candidates were whatever was genuinely in the
  S&P 500 on the day, refreshed daily from a public membership dataset. There
  was no hand-picked watchlist.
- **A backtest engine that shares every decision with the live code.** Entry,
  exit, sizing and priority rules live once, in `analysis/decision.py`, and
  four layers of tests fail if live and backtest ever disagree.
- **A research record** of roughly 60 experiments, most of them registered in
  writing before their data was read. Each lives in `research/` with its
  pre-registration, report and trade-level CSVs.

The strategy was 12-month trailing momentum, long only. A stock qualified if
its momentum was at least +2%, it was a current index member, and no crisis
block was in force. Candidates were ranked, sized by value-at-risk at up to
5% of the balance each, and admitted until 70% of the account was deployed.
A position closed on a crisis, its ATR stop, or its momentum turning negative.

Most experiments were run on two candidate pools built independently.
Pool 1 is today's index members projected backwards, so it holds only
survivors. Pool 2 is every company ever recorded as a member, minus the
tickers found to carry another company's price history.

## The result

**The live record is short and was not kept.** The demo account was reset to
£50,000 on 2026-08-02 and made 98 orders and 26 closed trades before the
system was shut down. The runtime database was deliberately not preserved,
so there is no final account value. The only recorded comparison came after
seven days: £51,362.06 against £51,794.74 for buy-and-hold, or −0.84%, which
is far too short to mean anything.

**The backtest is the real evidence.** Over 2007–2026, with realistic stop
fills and before costs, the strategy as it stood before the two improvements
below made:

    7.05% a year, against 11.13% for the S&P 500 total return index
    a −50.38% worst drawdown, against −55% to −58% for four index benchmarks

After FX costs it trailed the S&P 500 total return index by 7.75 percentage
points a year. With both improvements in place, it beat that index at only
one of seven end dates tested.

**The defensiveness was beta reduction, not skill.** Across 16 independent
historical blocks the strategy was compared with an index-plus-cash mix
holding the same drawdown. It won 8 of 16 blocks, a coin flip, and lost on
the chained aggregate (7.20% a year against 7.54–8.09%). A plain 60/40
portfolio beat it in 10 of 16 blocks. The NASDAQ-100 beat it in 15 of 16,
which says the strategy was largely a tech-momentum vehicle.

## Two changes that worked, relative to the system itself

| Change | Adopted | Effect on cost-adjusted excess return | What qualifies it |
| --- | --- | --- | --- |
| Rank candidates against each other each day, not by raw momentum | 2026-08-11 | +1.993pp | Dropping one ticker, AMAT, cut it to +0.427pp. The direction held in every informative perturbation. |
| A 3.0× ATR stop with no holding-period cap | 2026-08-12 | +6.443pp (Pool 2), +5.381pp (Pool 1) | 70 of 70 perturbations positive, but 73.6% of P&L came from 10 of 238 names. |

The second finding is the most useful one in the project. The old 30-day
holding cap caused 48.9% of all exits, and 37.4% of those positions were
bought back within one day. The cap was closing winners and paying to
re-enter them. **The edge in this system was not in choosing better positions.
It was in not churning the ones it already held.**

## Everything else tried did not help

| Idea | Result |
| --- | --- |
| Monthly top-50 shortlist instead of daily admission | Halved drawdown by holding ~24pp more cash; per-decision win rate 50.1–50.2% |
| Narrower candidate gate (top 100, 50 or 25) | Best +0.65pp, inside the noise floor |
| Cross-sectional momentum, rebalanced monthly | Missed its bar by 0.19 to 9.83pp; capital sat idle between rebalances |
| Concentration through larger per-trade sizes | Targets of 5 and 10 positions still produced about 12 |
| A hard cap of 8, 5 or 3 positions | −2.850, −1.582 and +1.004pp; the last was 60% one ticker |
| A higher deployment cap, 80–95% | The extra admissions were no better than baseline's |
| A short sleeve, long-biased or market-neutral | −7.17 and −9.96pp; the shorts lost £33,093 before borrow costs |
| Filtering for smoothly accumulated momentum | +1.551pp on one pool, −0.420pp on the other |
| Replacing weak holdings with stronger candidates | Turnover cost 9.9–21.8% a year against a 5.43% total return |
| A trailing stop armed at +30% to +100% | −5.087 to −1.759pp; it won the count and lost the money |
| Ordering by correlation with the book, either way | All 12 arms worse, −1.348 to −4.795pp |
| Pausing after a losing run | Forward returns after a 20% drawdown were above average, not below |
| A defensive sleeve of Treasuries and defensive sectors | Shelved: the tradeable UCITS versions had no history before 2015 |
| Interactive Brokers, for US-listed ETFs | Individual accounts cannot authenticate unattended by any documented route |

## Entry information did not predict trade outcome

Four separate features were tested against individual trade outcomes, and
none predicted them:

- **Momentum at entry:** rank correlation −0.033.
- **How smoothly the momentum built up:** not adopted; the two pools disagreed
  in sign.
- **Distance below the trailing peak at entry:** −0.028. Deeper-below-peak
  entries did slightly *better*, and those at least 10% below carried 69.7% of
  total P&L.
- **Nine proxies for corporate distress:** six were null. Index removal within a
  year touched only 2.57% of losses.

The reason is structural. Entry-time price behaviour is the input to the
ranking that chose these stocks, so the admitted population has already been
filtered on it. Two related results run against intuition. The most volatile
fifth of entries carried 25.5% of all gains and 13.6% of losses, because
value-at-risk sizing had already made those positions small. And recently
added index members did genuinely worse, a real effect that was never
turned into a rule.

## Descriptive results worth knowing

- **Ranking beats random selection on return and loses on drawdown.** The real
  ranking beat 99 of 100 random admission orders on return, and had a deeper
  drawdown than 98 of them.
- **P&L is concentrated.** The top tenth of trades made 257% of total P&L. The
  median trade lost £50.29.
- **Admission is timing, not queueing.** 92–93% of admissions happened only
  because a position exited that same day and freed the slot.

## Lessons about method, which outlive the strategy

1. **Model stop fills from the intraday low.** Firing a stop off the day's low
   and filling at the lower of the stop and the open cut total P&L by 73.75%,
   and CAGR from 13.60% to 7.05%. That was 1.7 times the first-order estimate,
   because exiting earlier changed every later admission.
2. **One implementation per decision.** On 2026-08-01, five places were found
   where live and backtest had quietly drifted apart. Every rule now lives in
   one function, and tests drive both real code paths against each other.
3. **Hindsight hides in the universe and in recycled tickers.** Today's index
   projected backwards cannot contain a 2008 casualty. Worse, delisted tickers
   get reused: one trade on another company's price history showed an ~819x
   return and a £2.21M P&L anomaly. 188 tickers ended up quarantined.
4. **A cost that varies with the thing being tested must be modelled.** A
   deployment-cap sweep showed higher caps overtaking a passive comparator.
   Once FX cost was charged per trade, the reversal disappeared.
5. **Know the noise floor before reading a result.** Cost-adjusted CAGR moved
   by about 1.08pp from trivial perturbations. Removing one ticker of 1,043
   moved the 16-block pass count by two blocks.
6. **Ask what would make a check report clean.** About a dozen checks in this project
   gave confident clean results for reasons unrelated to what they checked.
   One delisting detector scanned a 40-name research list instead of the
   503-name live pool, and reported nothing while a delisted company stayed a
   candidate for ten days.
7. **Keep the trades, not just the number.** A headline £442,558.70 could never
   be reconciled with a reproduction of £288,323.01, because the original trade
   list had been deleted.
8. **Say which backtest convention you used.** Carrying a variant's training
   balance into its test period gave it 1.49x the capital, which accounted for
   roughly 73% of its apparent advantage.

## The repository

The code is kept as it stood at the shutdown, under the MIT licence. Its
tests run offline with no data and no keys. `CLAUDE.md` explains the
system's standing rules and current state, `CLAUDE_HISTORY.md` records the
build in order, and `research/` holds every study's own files. This is the
public copy: its history was rewritten to remove credentials, server details
and a former brand name, so example.com addresses stand in for the originals.
Nothing in the repository runs or trades any more.
