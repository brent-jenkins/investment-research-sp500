# UI render-path blocking audit (2026-08-09)

**Investigation and proposal only — nothing changed, nothing fixed, no tables added, nothing deployed.** Full trace of every Streamlit page render path against three risk categories: yfinance calls, Trading212 API calls, and long computations (full-universe walks or multi-year multi-ticker history loads). Worktree: `investment-app-blocking-audit`.

**Scope note**: the task referenced "Dashboard, Instruments, Performance, Backtest, Status" as the five pages. The actual current page list (`main.py:2657`) is **Dashboard, Instruments, Backtest, Status, Users** — there is no separate Performance page; it was removed 2026-08-03 and its content relocated into Dashboard/Backtest (confirmed directly in a code comment at `main.py:2376-2386`). Audited the five pages that actually exist, including Users (not named in the task but a real page).

---

## Governing principle, restated

The render path should never block on a network call. Where genuinely live external state is needed, render the last stored value with an explicit "as of" timestamp and let a scheduled job refresh it — the pattern the EOD status badge already uses.

## Headline: three real findings, one of which is a structural multiplier for the other two

1. **`st.cache_data.clear()` runs unconditionally as the first line of the Status page** (`main.py:1497`) — wipes every `@st.cache_data` cache **app-wide**, not just this page's own. It is not itself a blocking call, but it defeats the TTL protection on findings 2 and 3 below (and any future cached function), converting "throttled to once per N seconds" into "fresh every time, if a user has visited Status recently."
2. **Status page's Candidate Queue makes a genuinely live Trading212 API call on every render** (`main.py:2023` → `project_pending_order_admission()` → `Trading212Client.get_account_summary()`), nominally cached at `ttl=60` but that cache is defeated by finding 1 on the same render.
3. **Dashboard's crisis banner makes a genuinely live yfinance call on every render** (`main.py:1226` → `get_crisis_status()` → `is_crisis_mode_active_now()` → `_live_current_price("^GSPC")` → `yf.Ticker(...).fast_info.last_price`), nominally cached at `ttl=300` but also collaterally defeated by finding 1.

These are the same class of bug the sector-breakdown fix closed, still present in two more places, and worse in one respect: the Status-page instance's own `ttl=60` guard is actively neutralized by code on the very same page, not merely absent.

---

## Full findings table, ordered by how often a user would actually hit it

| # | What | file:line (chain) | Trigger | Bucket | Proposed fix |
|---|---|---|---|---|---|
| 1 | `st.cache_data.clear()` defeats every cache app-wide | `main.py:1497` (first line of `_render_status_page`) | **Always**, every Status-page render | n/a — structural bug, not a data-fetch itself | Remove the blanket clear, or scope it to only this page's own genuinely-stale-sensitive state (if any reason for it exists at all — investigate why it was added; likely a leftover "make sure I see fresh data" habit that predates the app's cache-TTL discipline). This alone would restore the `ttl=60`/`ttl=300` guards already written for findings 2 and 3. |
| 2 | Candidate Queue admission projection — live T212 `get_account_summary()` | `main.py:2023` → `main.py:929 _get_projected_admission_cached()` (`@st.cache_data(ttl=60)`, defeated by #1) → `brokers/trading212.py:3963 project_pending_order_admission()` → `brokers/trading212.py:4008-4009 client.get_account_summary()` → `brokers/trading212.py:345 self._request("GET", "/equity/account/summary")` | Effectively **always**, on every Status render — gated internally on `enabled`+API keys+non-empty pending list, but pending rows persist 30 days (per the page's own caption) so the list is rarely empty on a live system | **(a)/(c) hybrid — read the nuance below, not a clean (a)** | See "Finding 2 in detail" below — this is not a simple drop-in DB read. |
| 3 | Crisis banner — live yfinance `^GSPC` quote | `main.py:1226 _render_crisis_banner()` → `main.py:1115/689 _get_crisis_status()` (`@st.cache_data(ttl=300)`, collaterally defeated by #1) → `output/performance.py:437,479 get_crisis_status()` → `brokers/trading212.py:1811,1843 is_crisis_mode_active_now()` → `brokers/trading212.py:1638,1687 _live_current_price("^GSPC")` → `yf.Ticker(...).fast_info.last_price` | **Always**, unconditional inside the function; only throttled by the (partially defeated) 300s cache — Dashboard is the default landing page | **(c)** | See "Finding 3 in detail" below. |
| 4 | Status page: two log-download subprocess calls run unconditionally, not only on click | `main.py:2136` (`journalctl -n 200`), `main.py:2247`/`2263` (`--since 24 hours ago` / `7 days ago`) | **Always** whenever journalctl is available — `st.download_button`'s `data=` argument is evaluated eagerly on every rerun regardless of whether the button is clicked; looks gated, isn't | n/a — local subprocess, not network, but real time cost (up to ~15s+30s combined worst case) | Compute the log data only inside an `if st.button(...)`-style two-step (a "prepare download" button that computes then offers `st.download_button`), or accept the cost as bounded/local and out of scope for a network-blocking audit specifically. |
| 5 | `get_ticker_status()`'s full-universe path is dead but reachable | `output/performance.py:19-45` | Only when called with `tickers=None` (the default) — currently **never** triggered in production because `get_brief_cards()` (`output/performance.py:195`) always passes `tickers=held_tickers`; but nothing prevents a future caller from omitting the scoping and reintroducing the ~60s delay this project already found and fixed once (2026-08-04) | n/a — latent risk, not a live bug | No fix needed today; consider making `tickers` a required (non-defaulted) parameter so a future caller can't silently reintroduce the full-universe walk, or add a code comment/assertion warning against calling it unscoped from a render path. |
| 6 | Instruments page: two DB-table scans run uncached on every keystroke | `main.py:850 load_all_instrument_resolutions()`, `main.py:852 get_all_watchlist_entries()` | Always, every rerun including every character typed in the search box | n/a — local DB only, not network | Wrap in `@st.cache_data(ttl=...)`, mirroring the pattern already used on the Dashboard page immediately below this one in the file. |
| 7 | Dashboard: `get_pnl_rows()` uncached | `main.py:683 _get_closed_rows()` → `output/performance.py:503 get_pnl_rows()` | Always, every Dashboard rerun | n/a — local DB only | Minor; add `@st.cache_data(ttl=...)` for consistency with every other Dashboard helper, which are all cached. |
| 8 | Backtest page: `live_universe_snapshot()` runs two uncached DB queries unconditionally | `main.py:2430` → `analysis/validation.py:194-225` → `analysis/universe.py:258-279`×2 | Always, every render, **before** the gating button — but bounded local DB work, not network | n/a — local DB only | ⚠️ **CORRECTED 2026-08-09 — see "Correction to Findings 8 and 9" below.** This was wrong: one of the "two `SELECT DISTINCT` queries" is `get_live_stock_candidates()`, measured at 26.33s cold, not a cheap query. |
| 9 | Status page: redundant duplicate `get_live_stock_candidates()` call for a count badge | `main.py:1919` (a second call; already called at `main.py:405` inside `_price_coverage()` earlier in the same render) | Always, when T212 is enabled | n/a — local DB only, pure inefficiency | ⚠️ **CORRECTED 2026-08-09 — see "Correction to Findings 8 and 9" below.** "Pure inefficiency" undersold it: the FIRST call this duplicates is the ~30s cold-start cost itself, not a cheap read the second call redundantly repeats. |
| 10 | Stale doc comment | `main.py:678` | n/a — documentation only | n/a | The comment on `_get_sector_breakdown()`'s wrapper still says "the only I/O this can do is a yfinance sector lookup," which was true before the render-path fix and is no longer accurate. Update the comment; no behavioural issue. |
| 11 | Stale UI string referencing a removed section | `main.py:1563` | n/a — documentation/UX only | n/a | "...use Manual Controls below to trigger an immediate pull" references a section that no longer exists on this page (relocated/consolidated 2026-08-03, per the in-code comment at `main.py:1998-2008`). Update or remove the string. |

**Confirmed clean, no action needed**: Users page (pure DB reads + button-gated writes throughout — verified directly, zero network imports anywhere in the page or in `auth.py`); Instruments page's actual instrument-resolution/watchlist data (reads `instrument_resolutions`/`universe_membership` tables directly, never re-resolves live, never walks the full universe — confirmed it deliberately uses `get_all_known_stock_tickers()`, not `get_live_stock_candidates()`, so no per-ticker `is_member()` loop either); Backtest page's actual ~12-minute battery run (**confirmed properly gated** behind `st.button("Run Full Validated Battery...")`, `main.py:2446` — nothing below the button check executes until clicked, and even when triggered it reads only the local database, zero yfinance/T212 imports anywhere in `analysis/backtest.py` or `analysis/validation.py`); Status page's Data Coverage card (reads stored `price_data` MAX(date) directly, never re-fetches — **this verdict was itself incomplete, see the correction below**: the card's own backing function, `_price_coverage()`, calls `get_live_stock_candidates()` before it ever reaches that `price_data` read); `get_brief_cards()`/`_get_open_cards()` (confirmed non-blocking post-2026-08-04 fix, scoped to held tickers only); `compute_sector_breakdown()` (confirmed fixed, pure DB read); `output/briefing.py` in its entirety (not imported by `main.py` at all — confirmed via repo-wide grep; its only callers are `data/scheduler.py`'s background jobs and two alert call sites in `brokers/trading212.py`, so nothing in it runs on any page-render path today regardless of its own internal correctness).

---

## Correction to Findings 8 and 9 (2026-08-09, same-day follow-up)

**Both findings mischaracterised `get_live_stock_candidates()` as cheap local DB work. It wasn't — measured cold, it was ~20-30 seconds, the actual cause of a real "Status page takes 20-30s on first load" symptom raised the same day.** Caught not by re-reading the code more carefully, but by direct cold-cache timing, per a follow-up task's own explicit instruction not to trust this audit's clean/cheap verdicts without measuring.

**What Finding 8 said**: "bounded local DB work, not network... currently just two `SELECT DISTINCT` queries." **What's actually true**: `live_universe_snapshot()` calls `get_all_known_stock_tickers()` (genuinely one cheap `SELECT DISTINCT`, ~0.06s) *and* `get_live_stock_candidates()` — measured cold, in its own fresh process, at **26.33s**. The second call is not a `SELECT DISTINCT` at all; internally it fanned out into ~1,205 sequential per-ticker queries (see below). This ran unconditionally on the Backtest page, before the gating button — so the button gated the ~12-minute battery run correctly, exactly as the audit said, but not the page's own ~26s stall on the way to showing that button.

**What Finding 9 said**: "local DB only, pure inefficiency" — correctly spotted the *duplication* (`get_live_stock_candidates()` called twice in one Status-page render, `_price_coverage()` then the T212 Invest Account section) but assumed the call itself was cheap, so the "fix" of removing the duplicate would have been real but marginal. In fact the *first* of the two calls was the entire ~30s cost; the second one measured 0.025s only because the first had already paid it and warmed the cache for the rest of the process.

**Root cause, measured directly (2026-08-09, see CLAUDE.md's "`get_live_stock_candidates()` cold-start cost" section for the full incident and fix)**: `get_live_stock_candidates()` → `get_all_known_stock_tickers()` (cheap) → a loop calling `is_member()` → `get_membership_windows()` for every one of ~1,205 known tickers, and `get_membership_windows()` only cached a ticker *after* its own individual query — one `SELECT ... WHERE ticker = :t` per ticker, ~17ms each, cold. Warm (after the first full population, cached for the rest of the process's life): 0.025-0.05s. Fixed at the source in `analysis/universe.py` by bulk-loading the whole table in one query on first access instead of one query per ticker — all four call sites (this one, the Status page's two, and `get_ticker_status()`'s already-scoped one) benefit from a single fix, with no bypass parameter added anywhere.

**The general lesson — this is the SECOND time this audit's method has missed this shape of cost, not a one-off**: see CLAUDE.md's "A function that reads as one query is not proof it only issues one query" section for the standing note this correction fed into, alongside the earlier "Dashboard open-position cards, missed by the original audit" finding.

---

## Finding 2 in detail — the live T212 account-summary call

`project_pending_order_admission()`'s own docstring (`brokers/trading212.py:3963-3990`) states plainly that it deliberately reads "a fresh, live read of the account's real current state" — this was not an oversight, it was a considered choice, made explicit in the docstring's own reasoning: *"the real admission decision only happens at actual submission time, against whatever the account's real state is AT THAT MOMENT — which can genuinely differ from right now."* The function exists specifically to project whether a queued candidate will actually be admitted, and a stale account balance could make that projection wrong in a way that matters (showing `PROJECTED_ADMIT` for something that's actually no longer affordable, or vice versa).

The data this function fetches live (`totalValue`, `investments.currentValue`, `cash.availableToTrade`) is **also already stored** in `system_metadata`'s `t212_account_snapshot` key, refreshed hourly by the existing `account_state_refresh` job (`data/scheduler.py:_account_state_job()`, hourly at minute 5) — the Dashboard's own `_get_account_summary()` reads exactly this stored snapshot and never calls T212 live (confirmed in the Dashboard trace).

So this is not a clean bucket-(a) case the way the sector bug was — it's a genuine trade-off between two real costs:
- **Read the DB (bucket a) instead**: eliminates the render-path blocking risk entirely, but the projection could be stale by up to ~55 minutes (worst case, just before the next hourly refresh) — a `PROJECTED_ADMIT`/`PROJECTED_REJECT_CAPACITY` label that's wrong for up to an hour, on a feature whose whole docstring-stated purpose is projecting near-term reality.
- **Keep the live call, move it off the render path**: have a scheduled job (see below) compute and store the projection itself, refreshed more often than hourly if an hour is judged too stale for this specific use case, and have the Status page just display it with an "as of" timestamp — this is the closer analogue to the bucket-(c) pattern (last known value + timestamp) since the underlying *quantity* genuinely needs to be fresher than most other display data.

**No recommendation given — this is presented as a trade-off for you to decide**, per the task's own instruction for a case that isn't clear-cut. Whichever direction is chosen, the fix is the same shape: stop calling `Trading212Client.get_account_summary()` from inside the Streamlit render thread.

## Finding 3 in detail — the crisis banner's live yfinance quote

`is_crisis_mode_active_now()` (`brokers/trading212.py:1811`) is documented elsewhere in this project (see CLAUDE.md's "Submission-time crisis re-check") as a genuinely intraday check, distinct from the daily-bar-only `analysis.crisis` functions, built specifically so a real intraday crisis can be caught before an order submits — that correctness need is real and this audit does not question it for the **order-submission path** (`stock_order_submission`, 14:30 UTC daily), which is a different call site not examined here since it isn't a render path.

The Dashboard's crisis **banner** is a different, display-only use of the same function. Unlike finding 2, there is **no existing scheduled job that already computes and stores this specific intraday check on a useful cadence** — `_market_hours_check_job()` (hourly) uses the daily-bar `analysis.crisis` functions for its own exit-check purposes, not `is_crisis_mode_active_now()`. This is genuine bucket (c) — live external state with no existing stored value to fall back on yet.

**Bucket (c) proposal**: have `market_hours_check` (already runs hourly, already touches related state) additionally call `is_crisis_mode_active_now()` once per run and write the result plus a timestamp to `system_metadata` (e.g. `crisis_mode_active`/`crisis_mode_checked_at`); have the Dashboard banner read that stored pair and display "Crisis mode: not active (as of HH:MM)" instead of calling yfinance itself. The trade-off to weigh: an hourly cadence means the banner could lag a genuine intraday crisis by up to ~60 minutes — worth exactly that concern, since the whole reason this check exists is to catch something a daily bar can't. If hourly is judged too infrequent for a banner whose purpose is early visibility, a higher-frequency dedicated job (not currently proposed, since none exists to reuse) would be a separate, larger addition — flagged, not designed here.

---

## Streamlit caching audit — where `@st.cache_data`/`@st.cache_resource` is already in use

| Wrapper | Location | TTL | Underlying cost on a genuine miss |
|---|---|---|---|
| `_get_open_cards` | `main.py:651` | 60s | DB-only (post-2026-08-04 fix, scoped to held tickers) |
| `_get_sector_breakdown` | `main.py:673` | 60s | DB-only (confirmed fixed) |
| `_get_crisis_status` | `main.py:689` | 300s | **Live yfinance quote** (finding 3) |
| `_get_account_summary` | `main.py:911` | 60s | DB-only (`system_metadata` read) |
| `_get_value_history` | `main.py:917` | 300s | DB-only |
| `_get_pending_candidates_cached` | `main.py:923` | 60s | DB-only |
| `_get_projected_admission_cached` | `main.py:929` | 60s | **Live T212 API call** (finding 2) |
| `_get_unconfirmed_exits_cached` | `main.py:940` | 60s | DB-only |
| `_get_recent_orders_cached` | `main.py:946` | 60s | DB-only |
| `_load_config` | `main.py:142` | 0 (effectively uncached) | Local YAML file read |

**A cache that expires still stalls the unlucky render — confirmed as a real, not merely theoretical, concern**: both `_get_crisis_status` (ttl=300) and `_get_projected_admission_cached` (ttl=60) demonstrate exactly this. Worse, in this specific app, `st.cache_data.clear()` on the Status page (finding 1) means these two caches don't even reliably get the TTL they were given — every Status visit forces both back to a guaranteed-miss state regardless of how recently they were last computed.

**Not masking anything additional beyond findings 2 and 3** — every other cached wrapper's underlying cost is a plain DB read, so an expired cache there just means a slightly-more-often SQL query, not a network stall.

---

## Existing scheduled jobs already producing data a display table could read

| Job | Cadence | Relevant `system_metadata` output already available |
|---|---|---|
| `account_state_refresh` (`_account_state_job`) | hourly, minute 5 | `t212_account_snapshot`, `t212_positions_snapshot`, `t212_snapshot_fetched_at` (an "as of" timestamp *already exists* for this data) |
| `market_hours_check` (`_market_hours_check_job`) | hourly, minute 0 | Exit-check/stop-health state; **does not currently compute `is_crisis_mode_active_now()`** — would need a small addition to serve finding 3's proposal |
| `scheduled_eod` (`_eod_job`) | daily 21:30 UTC | `last_full_refresh`, `eod_completion` (already the source for the existing EOD status badge — the house precedent this whole audit's governing principle points at) |
| `membership_refresh` | daily 21:15 UTC | membership staleness state |
| `stock_order_submission` | daily 14:30 UTC | Order placement outcomes; also where `is_crisis_mode_active_now()` is already called today, for the real (non-display) reason |
| `weekly_summary` | Sunday 09:00 Europe/London | Nothing render-path relevant (email-only) |
| `job_dispatcher` | every 60s | Dispatches UI-requested one-off jobs (e.g. the Status page's "Refresh Account" button already uses this pattern — sets a flag, the dispatcher does the real fetch out-of-band) |

**`account_state_refresh` is the job that already produces the exact data finding 2 needs** (`t212_account_snapshot`, already timestamped) — no new job required for the bucket-(a)-leaning option; only `market_hours_check` would need a small addition to support finding 3's proposal, and even that reuses an existing job rather than adding a new one.

---

## Files

This report only. No code changed, no table added, nothing deployed. Worktree retained for reference at `investment-app-blocking-audit` / branch `ui-blocking-audit` — not merged, not pushed.
