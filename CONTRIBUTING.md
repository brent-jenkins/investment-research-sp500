# Restarting and extending this project

The project was shut down on 2026-08-30. Its main finding is that a
long-only 12-month momentum strategy on the S&P 500 did not beat buying and
holding the index, and that its smaller drawdowns could be bought more
cheaply by holding cash. The machinery is sound: the backtest shares every
decision with the live code, and the method is written down. If you want to
point it at a new idea, this is where to start.

Read [FINDINGS.md](FINDINGS.md) first. It lists what has already been tried,
so you don't spend a month re-running it. The work continued in
[investment-research-cfd](https://github.com/brent-jenkins/investment-research-cfd), a short-horizon CFD strategy that
also found no edge. Read its findings too before starting on anything
short-horizon.

---

## Getting it running

```bash
pip install -r requirements.txt
pytest                       # offline: no database, network or keys needed
python -m streamlit run main.py
```

The data is **not** in the repository. The runtime database,
`database/investment_research.db`, is git-ignored, and the original was not
kept. You rebuild it yourself, in this order:

```bash
python -c "from data.database import init_db; init_db()"
python -c "from analysis.universe import refresh_membership_from_source as r; print(r())"
python -c "from data.ingestion import pull_incremental; pull_incremental()"
```

- **The first `init_db()` creates an `admin` login** and logs a random
  password for it once. Set `ADMIN_PASSWORD_HASH` to a bcrypt hash first if
  you want to choose it.
- **Index membership** comes from a public point-in-time S&P 500 dataset on
  GitHub, cross-checked against Wikipedia. No key is needed.
- **Prices come from yfinance**, from 2007 onwards. The first pull downloads
  full history for every ticker ever known to the index, about 1,200 of them,
  at the configured 2.5-second rate limit. Expect it to take an hour or more.
- **Delisted companies are the weak spot.** yfinance stops serving a symbol
  once a company is acquired or delisted, and some old symbols now carry a
  different company's prices. `scan_unverified_tickers()` in
  `data/ingestion.py` screens for that, and research should use
  `get_research_stock_candidates()`, which excludes quarantined tickers.

Secrets go in the environment, never in `config.yaml`: `T212_API_KEY`,
`T212_API_SECRET`, `SMTP_PASSWORD`, `ADMIN_PASSWORD_HASH` and
`GITHUB_WEBHOOK_SECRET`. `tests/test_no_committed_secrets.py` fails the build
if a credential-shaped value appears in any text file in the repository.

## Rules that keep the results honest

These are not style preferences. Each one is there because breaking it
produced a wrong number that was believed for a while. `CLAUDE.md` tells
each story.

1. **Pre-register.** Before you read any outcome data, write down the
   question, the test, the pass mark and what you will do on each result.
   The studies under `research/` from 2026-08-08 onwards show the format.
2. **One implementation per decision.** Anything that decides an entry, exit,
   size or priority belongs in `analysis/decision.py`, called by both
   `brokers/trading212.py` and `analysis/backtest.py`. The four parity test
   layers fail if either side re-derives a rule inline.
3. **Use the engine that matches live.** `simulate_portfolio_compounding_mtm()`
   is the one engine confirmed to match live execution. The others are frozen
   so old figures stay reproducible.
4. **State the backtest convention.** Compare variants with every arm reset
   to the same balance at the test boundary. Report a single strategy's
   realistic record as one continuous walk. Say which one you used.
5. **Model any cost that varies across the arms.** FX cost scales with
   turnover, so a change that trades more must be charged for it.
6. **Check against both pools, and against the noise floor.** Run a result on
   Pool 1 and Pool 2 and report whether they agree. A gap smaller than about
   1.08pp of cost-adjusted CAGR, or two of the 16 blocks, is noise.
7. **Keep the trades.** Before deleting any scratch code behind a figure you
   cite, commit its trade-level output alongside the write-up.
8. **Mutation-check every test.** Reintroduce the bug a test guards against
   and confirm the test fails. A test that mocks the component deciding the
   property it names can only confirm the mock.
9. **Demo before live.** `trading212_invest.live` is `false`, and
   `Trading212Client` raises on `live=True` whatever the config says. Real
   money needs a code change, not a config change.

## Before re-enabling the scheduler

The scheduler daemon, `scheduler_daemon.py`, places real orders on whatever
Trading 212 account its keys belong to. Its jobs are listed in `CLAUDE.md`
under "Current architecture".

- **Point it at a demo account first**, and check that both the BUY path and
  the broker-side stop path work before trusting either.
- **The deployment files are templates.** `setup.sh`, `deploy.sh`, the three
  systemd units and the Caddyfile use example.com and generic paths. The
  GitHub Actions deploy workflow was removed at shutdown and is in git history.
- **Stop config changes take a day to apply.** A stop is computed when a
  decision is queued at 21:30 UTC and used unchanged at submission the next
  day. See "Stop config changes carry a ONE-DAY LAG" in `CLAUDE.md`.
- **One capability ships switched off.** `share_class_exclusion` stops GOOG
  and GOOGL, for example, being held together. It defaults to `false`.

## Where there might still be something

None of these is a strong lead. They are the edges of what was tested, not
results.

- **Idle cash.** On average over a third of the account sat in cash earning
  nothing. A short-duration Treasury fund held in USD, `IBTSs_EQ` on
  Trading 212, tracked its US original closely from 2009. It is a cheap,
  untested fix.
- **Leaving a slot empty.** The system always filled free capacity with the
  best available candidate. Holding cash when nothing clears a quality bar was
  never tested, and a test would need to hold deployment level fixed so it
  measures selection rather than cash.
- **Recently added index members.** They did worse, and the effect survived a
  calendar control. It needs its own pre-registration before it is a rule.
- **Point-in-time fundamentals.** Free sources only give today's restated
  figures. The recorder in `data/ingestion.py` captured them forward from
  2026-08-12, and its data was lost with the database, so it starts again
  from zero.
- **Drawdown, as its own objective.** Several arms cut drawdown while losing
  return. None was tested with drawdown as the criterion set in advance.
- **US-listed ETFs through Interactive Brokers.** Its documentation offers
  unattended OAuth only to institutional account types. Community reports say
  individuals can register it too. One support ticket would settle it.
- **A universe without survivorship bias.** About 370 historical members have
  no free price history at all. A paid point-in-time source would remove the
  bias from every early block.

## Contributing back

Fork it, and open a pull request if you want to share something. A null
result is welcome as long as it was registered in advance: it saves the
next person the month. Please keep the house style. Comments explain *why*,
every figure names the database and convention it came from, and a
retracted figure is struck through with its reason rather than deleted.
