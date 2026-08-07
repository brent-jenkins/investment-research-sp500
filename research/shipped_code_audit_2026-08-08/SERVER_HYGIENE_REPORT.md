# Server hygiene report (2026-08-08)

**Investigation only. Nothing deleted.** `database_backups/` archived before any removal proposal,
per instruction. All findings below confirmed by direct inspection over SSH, not inferred.

## 1. `data/investment_research.db` — a real decoy, worth removing

```
-rw-r--r-- 1 root root 0 Aug  5 18:14 /root/investment-research/data/investment_research.db
```

**0 bytes, dated 2026-08-05.** This is a plausible-looking but wrong path — the real database lives
at `database/investment_research.db` (matching `config.yaml`), not `data/investment_research.db`
(`data/` is this project's Python package directory, `ingestion.py` etc.). This is exactly the shape
of risk this week's own audit incident represents: a script with a slightly-wrong relative path
would silently create/write to this file, print success, and leave nothing where a reader would
expect it — indistinguishable from working unless someone re-queries the *real* database directly.
It has sat here for 3 days already, untouched, apparently never actually hit by a wrong-pathed
script (0 bytes, no evidence of a write ever landing) — but it is a live hazard, not a historical one.

**Proposed: delete.** No plausible legitimate purpose for this file at this path.

## 2. `database/research.db` — a second, older decoy of the same shape

```
-rw-r--r-- 1 root root         0 Jun 14 16:45 research.db
```

Found while inspecting `database/` for item 3 below — not explicitly named in the original ask, but
the same shape of risk (0 bytes, plausible-looking path, sitting inside the real `database/`
directory alongside the genuine `investment_research.db`). Dated June 14 — much older than anything
else investigated here, predates most of this project's V2 rebuild history.

**Proposed: delete**, for the same reason as item 1.

## 3. `database_backups/` — superseded by `database/`'s own backup collection, archived

```
database_backups/
  investment_research_pre_etf_removal_20260802_134743.db          472M
  investment_research_pre_marketcap_removal_20260802_144011.db    472M
  investment_research_pre_membership_refresh_20260802_150126.db   472M
```

Three files, ~1.4GB total, all dated 2026-08-02, last touched that day. **`database/` itself
already contains a much larger, more current, and more consistently-named collection of the same
kind of pre-change backup** (`investment_research.db.pre-<description>-backup` /
`investment_research_pre_<description>_<timestamp>.db`, ~45 files spanning 2026-07-30 through
2026-08-07 — see item 4). `database_backups/` looks like an earlier, now-abandoned backup
convention/location that stopped being used once backups started landing directly in `database/`
instead — nothing has been added to it since 2026-08-02, while `database/`'s own collection kept
growing through at least 2026-08-07.

**Archived before proposing anything**, per instruction:
`/root/archived_server_hygiene_2026-08-08/database_backups_archive.tar.gz` (326MB compressed).

**Proposed: delete the live directory** once the archive above is confirmed sufficient — the backups
themselves aren't uniquely valuable (each one's `database/` counterpart from the same or a nearby
date likely already exists), but nothing is lost either way now that the archive exists.

## 4. `database/`'s own backup collection — not explicitly asked about, flagged for awareness only

Not part of the original ask, and **not proposed for any action** — flagged because it's large and
directly adjacent to what was investigated. ~45 files, `investment_research.db.backup-*` and
`investment_research.db.pre-*-backup`/`investment_research_pre_*.db`, ranging from 74MB (July 30-31,
early in the project) to 742MB (August, current-size era), roughly 18GB total. This looks like a
genuine, deliberate, actively-used "back up before a risky change" convention — matches
recognizable, real past changes (`pre-v2-cutover`, `pre-v1-removal`, `pre-instrument-consolidation`,
`pre-broker-stop-order`, `pre-claudemd-split`, and so on through 2026-08-06) — not clutter in the
same sense as items 1-3. Two things worth your awareness, not action:
- None of these are covered by `.gitignore`'s `database/*.db` rule (they don't end in literal
  `.db` — most end in a timestamp/description suffix), so they show as untracked in `git status`,
  same reason `database/` itself appeared in the original audit's clutter list.
- 18GB is a real amount of disk on a 2-vCPU/3.7GB-RAM server — worth a retention policy at some
  point (e.g. keep the last N, or last 30 days) if it keeps growing, but that's a separate decision
  from today's cleanup and not proposed here.

## 5. `diagnose_backtest_regime.py` / `verify_uniform_strong_buy.py` — confirmed dead V1-era scripts

Both read directly, not assumed from the filename:
- `diagnose_backtest_regime.py` — a "one-off diagnostic for the entry_signal_overrides trade-count
  investigation," imports/uses a full-history `run_backtest()`/`get_run()`/`get_run_trades()`/
  `delete_run()` API against `backtest_runs`/`backtest_signals`/`backtest_trades` tables.
- `verify_uniform_strong_buy.py` — a post-fix verification script referencing
  `config["virtual_trading"]["entry_signal_overrides"]` and the same `run_backtest()`/`delete_run()`
  API, comparing against "757 trades pre this whole feature" and "3,231 trades with the buggy...BUY
  override."

**Confirmed dead**: `virtual_trading.entry_signal_overrides`, the regime/confluence system, and the
`run_backtest()`/`get_run()`/`delete_run()` API are all V1-era concepts this project's V2 rebuild
removed entirely (see CLAUDE.md's "Current architecture" — `analysis/confluence.py`,
`analysis/engine.py`, `analysis/regime.py` are gone; the current backtest API is
`simulate_trades()`/`simulate_portfolio()`/`simulate_portfolio_compounding()`/
`simulate_portfolio_compounding_mtm()`, a different surface). Neither script would import
successfully against the current codebase, let alone run.

**Proposed: delete both.** No plausible use against the current system; the tables they'd need
(`backtest_runs`/`backtest_signals`/`backtest_trades`) may not even exist anymore.

## Summary

| Item | Proposed | Reasoning |
|---|---|---|
| `data/investment_research.db` (0 bytes) | Delete | Live decoy-shaped risk, no legitimate purpose |
| `database/research.db` (0 bytes) | Delete | Same shape of risk, older |
| `database_backups/` (3 files, 1.4GB) | Delete | Archived first; superseded by `database/`'s own collection |
| `database/`'s own ~45-file backup collection | No action proposed | Genuine, actively-used convention — flagged for awareness (untracked, ~18GB) only |
| `diagnose_backtest_regime.py` | Delete | Confirmed dead V1-era script, cannot run against current code |
| `verify_uniform_strong_buy.py` | Delete | Same |

**Awaiting your confirmation before deleting anything.**
