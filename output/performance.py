"""Pure data-shaping logic for the Performance/Status/Daily-Briefing UI —
extracted out of main.py so it's actually testable.

main.py cannot be imported directly in tests: it runs _start_scheduler_at_startup()
(an @st.cache_resource call) at module level, which would start a real
scheduler process — including live T212 API calls — as a side effect of
merely importing the module. Every function here is a plain, side-effect-free
(besides DB reads) data-shaping function; main.py's own thin wrappers just
call into this module and add Streamlit caching/rendering on top.
"""
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from data.database import get_engine, get_metadata_str


def get_ticker_status(config: dict, tickers: Optional[list[str]] = None) -> list[dict]:
    """Per-ticker live status for every tracked ticker: momentum signal
    (long/flat), current index-membership eligibility, and whether today is
    a raw crisis day / inside the crisis entry-block window.

    Recomputes momentum from full price history for every ticker given.
    When *tickers* is omitted (the default), uses the live,
    dynamically-refreshed S&P 500 candidate pool
    (analysis/universe.py:get_live_stock_candidates() — replaces the old
    static config.yaml `instruments:` list, see CLAUDE.md's "wire the
    momentum-only, full-S&P-500 selection into live execution" section) —
    the same per-ticker cost brokers/trading212.py's own _ticker_state()
    pays once per scheduled run.

    `tickers` (2026-08-04) lets a caller restrict this to a specific
    subset instead of paying the full-universe cost — added after tracing
    a real, measured ~60s Dashboard render delay directly to this
    function on production data (503 live candidates, each its own
    get_price_df() call + signal/momentum computation), even though
    get_brief_cards() (the sole real caller) only ever consults the
    handful of tickers with an actual open position (~16 of 503 as of
    this fix) — the other ~487 rows were computed and then discarded on
    every single call. Explicitly bypasses get_live_stock_candidates()
    itself when tickers is given (that lookup alone measured ~11s on
    production — a separate cost on top of the per-ticker loop), not just
    the per-ticker loop. Defaults to None (full universe) so every other
    existing caller/test keeps its exact prior behaviour.
    """
    from analysis import crisis, signals, universe
    from data.ingestion import get_price_df

    if tickers is None:
        tickers = universe.get_live_stock_candidates()
    if not tickers:
        return []

    try:
        gspc_df = get_price_df("^GSPC", days=8000)
        if gspc_df.empty:
            return []
        gspc_df = gspc_df.set_index("date")
        gspc_df.index = pd.to_datetime(gspc_df.index)
        gspc_close = gspc_df["close"].astype(float)
    except Exception:
        return []

    rows: list[dict] = []
    for ticker in tickers:
        # The live candidate pool is stocks only — the ETF sleeve was
        # removed from live trading entirely 2026-08-02 (see CLAUDE.md's
        # "ETF sleeve removed from live trading" section). Kept as an
        # explicit field (rather than dropped) for shape compatibility
        # with existing consumers of this dict.
        is_etf = False
        try:
            df = get_price_df(ticker, days=8000)
            if df.empty or len(df) < 300:
                rows.append({
                    "ticker": ticker, "is_etf": is_etf, "sig_on": None,
                    "is_member": None, "crisis_now": None, "entry_blocked": None,
                    "momentum_pct": None, "insufficient_history": True,
                })
                continue
            df = df.set_index("date")
            df.index = pd.to_datetime(df.index)
            close = df["close"].astype(float)
            latest_dt = close.index[-1]

            sig_series = signals.compute_signal_series(close)
            mom_series = signals.compute_momentum_series(close)
            sig_on = bool(sig_series.iloc[-1])
            mom_pct = float(mom_series.iloc[-1]) if pd.notna(mom_series.iloc[-1]) else None
            is_member = is_etf or universe.is_member(ticker, latest_dt.date())
            crisis_now = crisis.is_crisis_day(gspc_close, latest_dt)
            entry_blocked = crisis.is_entry_blocked(gspc_close, latest_dt)

            rows.append({
                "ticker": ticker, "is_etf": is_etf, "sig_on": sig_on,
                "is_member": is_member, "crisis_now": crisis_now,
                "entry_blocked": entry_blocked, "momentum_pct": mom_pct,
                "insufficient_history": False,
            })
        except Exception:
            rows.append({
                "ticker": ticker, "is_etf": is_etf, "sig_on": None,
                "is_member": None, "crisis_now": None, "entry_blocked": None,
                "momentum_pct": None, "insufficient_history": True,
            })

    return rows


def get_brief_cards(
    config: dict,
    ticker_status_rows: Optional[list[dict]] = None,
) -> list[dict]:
    """Return enriched trade-card dicts for open positions.

    Reads the scheduler-maintained t212_positions_snapshot (one shared T212
    account) filtered to every VERIFIED T212 instrument resolution ever
    recorded (brokers/trading212.py:_load_all_verified_instrument_
    resolutions() — replaces the old static config.yaml `instruments:`
    dict now that the live candidate pool is the full, dynamic S&P 500;
    see CLAUDE.md's "wire the momentum-only, full-S&P-500 selection into
    live execution" section), plus entry context (momentum_pct,
    stop_price) from invest_orders. Status mirrors brokers/trading212.py's
    own exit precedence exactly: crisis > index-removal > stop-loss >
    signal-off > max-holding.

    ticker_status_rows lets a caller pass in an already-computed
    get_ticker_status() result (e.g. main.py's cached version) instead of
    recomputing it here; defaults to a fresh call when omitted — scoped
    (2026-08-04) to only the research tickers that actually have an open
    position (reverse_map filtered by pos_by_t212, both already computed
    below by the time this fires), not get_ticker_status()'s own default
    full ~503-ticker live candidate pool. This is what fixed a real,
    measured ~60s Dashboard render delay traced directly to this
    function computing full-universe status for every live candidate
    when only the small handful of currently-held tickers were ever
    consulted from status_by_ticker below.
    """
    import json
    import os

    from analysis.decision import evaluate_exit_reason
    from analysis.universe import get_max_holding_days
    from brokers.trading212 import _load_all_verified_instrument_resolutions

    if not (os.environ.get("T212_API_KEY", "") and os.environ.get("T212_API_SECRET", "")):
        return []

    t212_cfg = config.get("trading212_invest", {})
    resolved = _load_all_verified_instrument_resolutions()
    if not resolved:
        return []

    # ETFs were removed from live trading entirely (2026-08-02) — every
    # resolved ticker is a stock, so is_etf is always False here now.
    reverse_map: dict[str, tuple[str, bool]] = {
        t212_ticker: (r["ticker"], False) for t212_ticker, r in resolved.items()
    }

    pos_raw = get_metadata_str("t212_positions_snapshot")
    positions: Optional[list] = None
    if pos_raw:
        try:
            positions = json.loads(pos_raw) or []
        except Exception:
            pass
    if positions is None:
        return []

    pos_by_t212 = {p.get("ticker", ""): p for p in (positions or [])}

    try:
        from sqlalchemy import text as sa_text
        with get_engine().connect() as conn:
            entry_rows = conn.execute(sa_text("""
                SELECT t212_ticker, momentum_pct, stop_price, filled_at, currency_code
                FROM invest_orders
                WHERE direction = 'BUY' AND status = 'FILLED'
                ORDER BY filled_at ASC
            """)).mappings().all()
        entry_by_t212: dict[str, dict] = {}
        for r in entry_rows:
            u = r["t212_ticker"]
            if u not in entry_by_t212:
                entry_by_t212[u] = dict(r)
    except Exception:
        entry_by_t212 = {}

    if ticker_status_rows is None:
        held_tickers = sorted({
            research_ticker for t212_ticker, (research_ticker, _is_etf) in reverse_map.items()
            if t212_ticker in pos_by_t212
        })
        ticker_status_rows = get_ticker_status(config, tickers=held_tickers)
    status_by_ticker = {r["ticker"]: r for r in ticker_status_rows}

    # Reuse the exact same GBP conversion brokers/trading212.py itself uses
    # to compute estimated_value_gbp/pnl_gbp at order time — reads straight
    # from price_data (GBPUSD=X/EURUSD=X), no live yfinance call, no session
    # state.
    from brokers.trading212 import Trading212Client

    def _t212_ccy_to_gbp(amount: float, currency: str) -> float:
        gbpusd, eurusd = Trading212Client._fx_rates()
        return Trading212Client._to_gbp(amount, currency, gbpusd, eurusd) or amount

    cards: list[dict] = []
    for t212_ticker, (research_ticker, is_etf) in reverse_map.items():
        pos = pos_by_t212.get(t212_ticker)
        if not pos:
            continue

        avg_price     = float(pos.get("averagePrice") or 0)
        qty           = float(pos.get("quantity")     or 0)
        current_price = float(pos.get("currentPrice") or 0)
        ppl           = float(pos.get("ppl")          or 0)
        if qty <= 0 or avg_price <= 0:
            continue

        unreal_pct = round((current_price - avg_price) / avg_price * 100, 2) if avg_price > 0 else 0.0

        entry_info   = entry_by_t212.get(t212_ticker, {})
        stop_price   = entry_info.get("stop_price")
        momentum_pct = entry_info.get("momentum_pct")
        currency     = entry_info.get("currency_code") or "USD"

        filled_at_str = entry_info.get("filled_at")
        entry_dt: Optional[datetime] = None
        if filled_at_str:
            try:
                entry_dt = datetime.fromisoformat(str(filled_at_str))
            except Exception:
                pass
        days_held = max(0, (datetime.utcnow() - entry_dt).days) if entry_dt else 0

        max_hold = get_max_holding_days(research_ticker, config)
        live_status = status_by_ticker.get(research_ticker, {})
        sig_on = live_status.get("sig_on")
        is_member = live_status.get("is_member")
        crisis_now = live_status.get("crisis_now")

        # Exit precedence -- the single shared implementation
        # (analysis/decision.py:evaluate_exit_reason()), the same function
        # brokers/trading212.py's maybe_exit_positions() and every backtest
        # engine call. This is a display-only recomputation (no order is
        # placed here) but it must classify a position identically to the
        # live exit check, or the UI could show "ON TRACK" for a position
        # that's actually about to be closed. `current_price > 0` guards
        # against a zero/missing live quote incorrectly reading as a
        # STOP_LOSS breach (0 <= any positive stop_price) -- passed as
        # price=None in that case so the shared function's own "unknown,
        # don't trigger" handling applies. `is_member` is NOT passed to
        # evaluate_exit_reason() -- membership no longer affects an
        # already-open position's exit status (see that function's own
        # docstring) -- it is still surfaced on the card below as
        # "current_is_member" for informational display only.
        price_for_stop_check = current_price if current_price > 0 else None
        exit_reason = evaluate_exit_reason(
            crisis_now=crisis_now,
            price=price_for_stop_check, stop_price=stop_price, sig_on=sig_on,
            holding_days=days_held, max_holding_days=max_hold,
        )
        status = {
            "CRISIS_EXIT": "CRISIS EXIT",
            "STOP_LOSS": "STOP LOSS", "SIGNAL_OFF": "SIGNAL OFF",
            "MAX_HOLDING": "MAX HOLDING",
        }.get(exit_reason, "ON TRACK")

        status_priority = {
            "CRISIS EXIT": 4, "STOP LOSS": 3,
            "SIGNAL OFF": 2, "MAX HOLDING": 1, "ON TRACK": 0,
        }[status]

        current_value_gbp = round(_t212_ccy_to_gbp(qty * current_price, currency), 2)
        pnl_gbp = round(_t212_ccy_to_gbp(ppl, currency), 2)

        cards.append({
            "ticker": research_ticker, "t212_ticker": t212_ticker,
            "asset_type": "ETF" if is_etf else "STOCK",
            "entry_price": avg_price, "quantity": qty,
            "entry_date": entry_dt.isoformat() if entry_dt else None,
            "days_held": days_held, "max_holding_days": max_hold,
            "live_price": current_price, "currency": currency,
            "current_value_gbp": current_value_gbp,
            "unreal_pnl_gbp": pnl_gbp, "unreal_pct": unreal_pct,
            "momentum_pct_at_entry": momentum_pct, "stop_price": stop_price,
            "current_sig_on": sig_on, "current_is_member": is_member,
            "crisis_now": crisis_now, "status": status,
            "status_priority": status_priority,
        })

    return cards


def compute_position_context(card: dict) -> dict:
    """Neutral, descriptive context for one open position — how far the
    live price sits from its stop, and how many days remain before the
    max-holding backstop — computed entirely from data already on the
    card (live_price, stop_price, days_held, max_holding_days; see
    get_brief_cards() above), no new API calls or pricing pipeline.

    This exists specifically so the numbers are computed once, in one
    tested place, rather than re-derived inline in main.py's rendering —
    main.py cannot be unit-tested directly (see this module's own
    docstring), so anything worth pinning behaviour on lives here.
    """
    live_price = card.get("live_price")
    stop_price = card.get("stop_price")
    pct_above_stop = None
    if live_price is not None and stop_price is not None and stop_price > 0:
        pct_above_stop = (live_price - stop_price) / stop_price * 100.0

    days_held = card.get("days_held")
    max_holding_days = card.get("max_holding_days")
    days_remaining = None
    if days_held is not None and max_holding_days is not None:
        days_remaining = int(max_holding_days) - int(days_held)

    return {"pct_above_stop": pct_above_stop, "days_remaining": days_remaining}


def compute_sector_breakdown(open_cards: list[dict]) -> dict:
    """Sector concentration of currently open positions, by position count
    and by capital deployed (current_value_gbp — mark-to-market, the view
    that matters for concentration risk, not historical entry cost).

    Reporting only — this computes nothing that gates, blocks, or sizes
    anything; it exists purely so sector concentration (a real, expected
    consequence of momentum ranking — see the selection audit's Part 3
    "sector tilt" finding) is visible at a glance rather than only
    discoverable by eyeballing a ticker list. Called identically by the
    Dashboard (main.py) and the weekly summary (output/briefing.py) — one
    shared computation, not two independently-rendered copies.

    READ-ONLY against ticker_sector — deliberately never calls
    data.ingestion.get_or_fetch_sector() (which can fetch from yfinance on
    a cache miss). A page render or email generation must never block on a
    live network call; fetching happens only in the separate pre-warm path
    (prewarm_sector_data.py) or the first time a genuinely new ticker is
    processed there — never inline in this function. See
    tests/test_performance.py::test_sector_breakdown_render_path_never_calls_yfinance
    for the direct proof this holds.

    A ticker with no ticker_sector row yet (never pre-warmed, or genuinely
    unresolved when it was) is grouped under "Uncategorized" — it still
    counts toward total position count and total capital; it is never
    silently dropped from the totals. An empty open_cards list returns an
    all-zero breakdown, not an error.
    """
    from data.database import get_ticker_sector

    by_count: dict[str, int] = {}
    by_capital: dict[str, float] = {}
    total_capital_gbp = 0.0

    for card in open_cards:
        ticker = card.get("ticker", "")
        cached = get_ticker_sector(ticker)
        sector = (cached["sector"] if cached else None) or "Uncategorized"
        capital = float(card.get("current_value_gbp") or 0)
        by_count[sector] = by_count.get(sector, 0) + 1
        by_capital[sector] = round(by_capital.get(sector, 0.0) + capital, 2)
        total_capital_gbp += capital

    # Descending by capital in both views -- a consistent, informative
    # ordering (largest exposure first), not a ranking by "risk" or any
    # implied urgency (see position_context_style()'s own tone note above).
    order = sorted(by_capital, key=lambda s: -by_capital[s])
    by_count_sorted = {s: by_count[s] for s in order}
    by_capital_sorted = {s: by_capital[s] for s in order}
    by_capital_pct = {
        s: round(100.0 * v / total_capital_gbp, 1) if total_capital_gbp > 0 else 0.0
        for s, v in by_capital_sorted.items()
    }

    return {
        "by_count": by_count_sorted,
        "by_capital_gbp": by_capital_sorted,
        "by_capital_pct": by_capital_pct,
        "total_positions": len(open_cards),
        "total_capital_gbp": round(total_capital_gbp, 2),
    }


_NEUTRAL_POSITION_COLOUR = "#8899AA"


def position_context_style(pct_above_stop: Optional[float], days_remaining: Optional[int]) -> str:
    """Colour to use for the stop-distance / holding-countdown text on an
    open position card.

    Always returns the same neutral colour regardless of either value —
    there is deliberately no proximity-based styling (no red for "close
    to stop", no traffic-light gradient as a position nears its deadline).
    This system exists to remove reactive, emotionally-driven decision-
    making from investing; a dashboard that makes an individual trade look
    urgent as a number approaches a threshold would quietly reintroduce
    exactly that. Kept as its own function (rather than inlined where it's
    used) specifically so this invariant is directly testable — see
    tests/test_performance.py's proximity-sweep test — not just "true
    because nothing currently branches on it."
    """
    return _NEUTRAL_POSITION_COLOUR


def position_exit_rule_text(max_holding_days) -> str:
    """Plain, descriptive statement of how this position's exit rules
    work — not a prediction of which one will fire, not a warning.
    Every open position shares the same rule shape; only the holding-day
    number is position-specific."""
    if max_holding_days is None:
        return "Closes automatically on a stop-loss hit or momentum turning negative."
    return (
        f"Closes automatically on any of: stop-loss hit, momentum turning negative, "
        f"or day {int(max_holding_days)} of holding reached — whichever comes first."
    )


def positions_summary_text(n: int) -> str:
    """Calm, aggregate one-line summary shown above the per-position list
    — deliberately the first thing read on the page, so the page's first
    impression is composed rather than a wall of individual trade data."""
    if n == 0:
        return "No positions open."
    plural = "s" if n != 1 else ""
    return f"{n} position{plural} open — portfolio operating within its normal rules."


POSITIONS_RULES_EXPLANATION = (
    "Each position closes automatically when its stop is hit, momentum turns negative, "
    "or its maximum holding period is reached — whichever comes first. This describes how "
    "the system's exit rules work, not a signal that anything needs attention."
)


def get_crisis_status() -> dict:
    """Today's crisis-override state plus how close the day's S&P 500 move
    is to the -7% single-day trigger threshold — not just an active/inactive
    flag. Returns {} if price data isn't available yet.

    READ-ONLY against system_metadata — deliberately never calls
    brokers.trading212.is_crisis_mode_active_now() itself (2026-08-09 fix;
    see CLAUDE.md's "UI render-path blocking audit" section, finding 3).
    `entry_blocked`/`entry_blocked_checked_at` are sourced from
    `crisis_mode_active`/`crisis_mode_checked_at`, written hourly by
    data.scheduler._market_hours_check_job() — a DISPLAY-ONLY snapshot of
    the same underlying check, not a live call from this render-path
    function. is_crisis_mode_active_now() itself is untouched and remains
    fully live: brokers/trading212.py's stock-submission logic still calls
    it fresh, every single time, immediately before placing a new entry —
    that is the real, order-blocking, authoritative decision and this
    display value has no bearing on it whatsoever (confirmed: the two are
    separate function calls with no shared state, in separate processes —
    the Streamlit UI process reads this function's stored snapshot; the
    scheduler daemon process makes the real live call at submission time).

    Because this is now a stored value refreshed at most once an hour (only
    while an NYSE extended-hours session is open — see
    _market_hours_check_job()'s own gating), `entry_blocked` here can lag a
    genuine intraday crisis by up to ~60 minutes. This is judged acceptable
    BECAUSE the banner is informational only — the authoritative,
    correctness-critical check happens fresh at submission time, where it
    actually matters. `entry_blocked_checked_at` is always returned
    alongside so the display can show its own "as of" honestly rather than
    implying real-time freshness it doesn't have; None if the job has never
    successfully completed a check yet (e.g. immediately after a fresh
    deploy, before the next NYSE session opens).

    A None/failed check on a given run leaves the PREVIOUS stored value in
    place rather than overwriting good data with "unknown" (see
    _market_hours_check_job()'s own comment) — so a brief single-run read
    failure does not itself blank the banner; only a sustained outage,
    visible via a stale `entry_blocked_checked_at`, would.
    """
    from analysis import crisis
    from data.database import get_metadata_str
    from data.ingestion import get_price_df

    df = get_price_df("^GSPC", days=60)
    if df.empty:
        return {}
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    close = df["close"].astype(float)
    if len(close) < 2:
        return {}

    daily_return = close.pct_change()
    today_return = float(daily_return.iloc[-1]) if pd.notna(daily_return.iloc[-1]) else None
    latest_dt = close.index[-1]

    is_crisis_today = crisis.is_crisis_day(close, latest_dt)
    entry_blocked = get_metadata_str("crisis_mode_active") == "true"
    entry_blocked_checked_at = get_metadata_str("crisis_mode_checked_at")

    raw = crisis.compute_raw_trigger(close)
    trigger_dates = raw[raw].index
    last_trigger_date = trigger_dates[-1].isoformat() if len(trigger_dates) else None
    days_since_trigger = int((raw.index >= trigger_dates[-1]).sum() - 1) if len(trigger_dates) else None

    distance_to_threshold_pct = (
        (today_return - crisis.PRICE_GAP_THRESHOLD) * 100 if today_return is not None else None
    )

    return {
        "as_of": latest_dt.isoformat(),
        "today_return_pct": today_return * 100 if today_return is not None else None,
        "threshold_pct": crisis.PRICE_GAP_THRESHOLD * 100,
        "distance_to_threshold_pp": distance_to_threshold_pct,
        "is_crisis_today": is_crisis_today,
        "entry_blocked": entry_blocked,
        "entry_blocked_checked_at": entry_blocked_checked_at,
        "cooldown_days": crisis.ENTRY_BLOCK_COOLDOWN_DAYS,
        "last_trigger_date": last_trigger_date,
        "days_since_trigger": days_since_trigger,
    }


def get_stored_open_position_cards() -> dict:
    """The Dashboard's open-position card data — READ-ONLY against
    system_metadata (2026-08-09 fix; see CLAUDE.md's "UI render-path
    blocking audit" follow-up, finding 4: _get_open_cards() used to call
    get_brief_cards() directly from the Streamlit render path, which in
    turn called get_ticker_status() -> a real per-held-ticker price-history
    read (get_price_df(ticker, days=8000)) plus a momentum/signal
    recomputation (analysis.signals.compute_signal_series()) for every
    open position, every render — measured at ~6s total for the 22
    positions open in production at the time of the fix (~0.18-0.25s/
    ticker, scaling directly with open-position count). Not a network
    call (get_price_df() reads local price_data only) but exactly the
    "per-position price-history computation" this project's render-path
    standard prohibits just as firmly as a live API call.

    Deliberately never calls get_brief_cards() itself from this function.
    get_brief_cards() remains the one, shared computation entry point —
    unchanged, still calling analysis.signals.compute_signal_series(), the
    same shared signal function analysis/decision.py's entry/exit checks
    and every backtest engine use — but it now only ever runs inside
    data.scheduler._account_state_job() (hourly, minute 5, off the
    Streamlit render thread entirely), via
    data.scheduler._refresh_open_position_cards_snapshot(). This function
    only ever reads what that job already wrote.

    Returns {"cards": [...], "fetched_at": <ISO str or None>}. An empty
    list is a genuine, confirmed "no open positions right now" (see
    _refresh_open_position_cards_snapshot()'s own docstring for why an
    empty result is written, not treated as "unknown"); fetched_at is
    None only if the job has never completed a successful cycle yet (e.g.
    immediately after a fresh deploy, before the first hourly refresh).
    A stale/failed refresh cycle leaves the previous stored snapshot AND
    its original fetched_at in place — the caller must display fetched_at
    honestly rather than assume the cards are current.
    """
    import json as _json

    raw = get_metadata_str("open_position_cards_snapshot")
    cards: list[dict] = []
    if raw:
        try:
            cards = _json.loads(raw) or []
        except Exception:
            cards = []
    fetched_at = get_metadata_str("open_position_cards_fetched_at")
    return {"cards": cards, "fetched_at": fetched_at}


def get_pnl_rows() -> list[dict]:
    """All reconstructed closed positions, most recent first.

    Reads brokers/trading212.py:_reconstruct_pnl()'s output table directly.
    """
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                sa_text("SELECT * FROM pnl ORDER BY exit_date DESC")
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def pnl_metrics_df(pnl_rows: list[dict]) -> pd.DataFrame:
    """Adapt pnl rows to the column shape analysis/backtest.py's
    compute_metrics()/equity_curve_gbp() expect."""
    if not pnl_rows:
        return pd.DataFrame()
    df = pd.DataFrame(pnl_rows)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"]  = pd.to_datetime(df["exit_date"])
    df["pnl_pct"]    = df["pnl_pct"].astype(float)
    df["is_win"]     = df["pnl_gbp"] > 0
    df["direction"]  = "LONG"
    return df


# ---------------------------------------------------------------------------
# Dashboard (2026-08-02) — replaces the Daily Briefing page. Every function
# below is a plain DB read; no T212 API calls happen here (matches this
# module's own "UI only reads from the DB" convention — brokers/trading212.py
# and data/scheduler.py are the only code that talks to T212 directly).
# ---------------------------------------------------------------------------

def get_current_account_summary() -> dict:
    """The latest T212 account snapshot (system_metadata's
    t212_account_snapshot, written by data/scheduler.py:_fetch_t212_
    snapshot() — the UI never calls T212 directly). Returns
    {"total_value_gbp", "cash_gbp", "invested_gbp", "deployed_pct"}, all
    0.0 if no snapshot has ever been fetched yet."""
    import json as _json

    raw = get_metadata_str("t212_account_snapshot")
    if not raw:
        return {"total_value_gbp": 0.0, "cash_gbp": 0.0, "invested_gbp": 0.0, "deployed_pct": 0.0}
    try:
        summary = _json.loads(raw)
        cash_obj = summary.get("cash", {}) if isinstance(summary.get("cash"), dict) else {}
        inv_obj  = summary.get("investments", {}) if isinstance(summary.get("investments"), dict) else {}
        total_value = float(summary.get("totalValue", 0) or 0)
        cash        = float(cash_obj.get("availableToTrade", 0) or 0)
        invested    = float(inv_obj.get("currentValue", 0) or 0)
        deployed_pct = (invested / total_value * 100) if total_value else 0.0
        return {
            "total_value_gbp": total_value, "cash_gbp": cash,
            "invested_gbp": invested, "deployed_pct": deployed_pct,
        }
    except Exception:
        return {"total_value_gbp": 0.0, "cash_gbp": 0.0, "invested_gbp": 0.0, "deployed_pct": 0.0}


def get_account_value_history(days: int, starting_balance_gbp: float = 50_000.0) -> list[dict]:
    """Real daily account-value snapshots for the last *days* calendar
    days (account_value_history — see data/database.py:AccountValueHistory
    and data/scheduler.py:_record_account_value_history() for how this is
    populated, once daily, as part of the EOD job's own existing T212
    snapshot fetch).

    Anchored to start at *starting_balance_gbp* (the account's real
    initial T212 balance, £50,000 — not £0) rather than an empty/flat
    -looking chart: if the earliest real snapshot is later than
    (today - days) — which it currently always is, since the account was
    only just reset with no trading history yet — a synthetic point dated
    (today - days) at starting_balance_gbp is prepended, so the scale of
    any real gain or loss the account goes on to show is visible
    immediately rather than the chart looking like it started from
    nothing. This is a real, honest anchor (the account's genuine actual
    starting balance), not a fabricated data point standing in for a
    real one — it is exactly what the account was worth on that date.

    Returns a list of {"date": "YYYY-MM-DD", "total_value_gbp": float},
    sorted ascending by date. Never raises.
    """
    from datetime import date, timedelta
    from sqlalchemy import text as sa_text

    cutoff = date.today() - timedelta(days=days)
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT date, total_value_gbp FROM account_value_history
                WHERE date >= :cutoff ORDER BY date ASC
            """), {"cutoff": cutoff.isoformat()}).mappings().all()
    except Exception:
        rows = []

    result = [{"date": str(r["date"]), "total_value_gbp": float(r["total_value_gbp"] or 0.0)} for r in rows]
    if not result or result[0]["date"] > cutoff.isoformat():
        result.insert(0, {"date": cutoff.isoformat(), "total_value_gbp": starting_balance_gbp})
    return result


# Minimum elapsed days before the benchmark spread is worth reading as
# anything but noise. Nothing is hidden or gated on it — this only decides
# whether the displayed caveat calls the number uninformative. 180 days is a
# judgement: a shorter window is dominated by deployment level and market
# direction rather than by selection, which is the thing the comparison is
# nominally about.
BENCHMARK_MEANINGFUL_MIN_DAYS = 180


def get_benchmark_comparison_history(days: int = 3650) -> list[dict]:
    """Stored account-vs-benchmark pairs for the last *days* calendar days
    (benchmark_comparison — see data/database.py:BenchmarkComparison and
    data/scheduler.py:_refresh_benchmark_comparison() for how it is
    populated, once daily, from the EOD job).

    A PURE DB READ. No yfinance call, no T212 call, no recomputation of the
    benchmark ratio at render time — the render path reads stored state
    only, matching the open-position-cards/sector/crisis pattern this
    project adopted after repeated render-path stalls (see CLAUDE.md's UI
    render-path audit entries).

    Deliberately NOT anchored with a synthetic leading point the way
    get_account_value_history() is: that function's anchor is the account's
    own genuine starting balance on a known date, which is a real fact,
    whereas prepending a benchmark point for a date before the account
    existed would invent a comparison that never happened. If the account's
    history is short, this series is short.

    Returns [{"date", "account_value_gbp", "benchmark_value_gbp", ...}]
    ascending by date. Never raises; returns [] on any failure or before the
    first EOD run has computed anything.
    """
    from datetime import date as _date, timedelta as _timedelta
    from sqlalchemy import text as sa_text

    cutoff = _date.today() - _timedelta(days=days)
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT date, account_value_gbp, benchmark_value_gbp, anchor_date,
                       anchor_balance_gbp, computed_at
                FROM benchmark_comparison
                WHERE date >= :cutoff ORDER BY date ASC
            """), {"cutoff": cutoff.isoformat()}).mappings().all()
    except Exception:
        return []
    return [dict(r) for r in rows]


def _coerce_date(value):
    """str/datetime/date -> date, or None. Local to the benchmark readers,
    which see all three depending on whether a row came from SQLAlchemy's
    Date type or a raw text query."""
    from datetime import date as _date, datetime as _datetime
    if value is None:
        return None
    if isinstance(value, _datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    try:
        return _datetime.fromisoformat(str(value)).date()
    except Exception:
        return None


def compute_benchmark_spread(rows: list[dict]) -> dict:
    """Pure aggregation over already-fetched benchmark_comparison rows (see
    get_benchmark_comparison_history()) — no DB/network access itself,
    matching compute_sector_breakdown()'s own pure-function pattern.

    The spread is stated two ways, both from the SAME latest row:
    `spread_gbp` (account minus benchmark, in £) and `spread_pct`
    (account/benchmark − 1). The percentage is relative to the BENCHMARK's
    value, not to the starting balance — "how far ahead or behind
    buy-and-hold is this account right now" — which is the question this
    system's stated objective is actually about.

    Returns {"available", "as_of", "account_value_gbp",
    "benchmark_value_gbp", "spread_gbp", "spread_pct", "anchor_date",
    "anchor_balance_gbp", "elapsed_days", "is_meaningful_period"}.
    `available` is False when no usable row exists yet (before the first EOD
    run, or before ^SP500TR has been ingested) — a caller must render that
    as "not computed yet", never as a £0.00 spread, which would read as the
    account exactly matching the index.
    """
    empty = {
        "available": False, "as_of": None, "account_value_gbp": None,
        "benchmark_value_gbp": None, "spread_gbp": None, "spread_pct": None,
        "anchor_date": None, "anchor_balance_gbp": None, "elapsed_days": 0,
        "is_meaningful_period": False,
    }
    if not rows:
        return empty
    latest = rows[-1]
    account_value = latest.get("account_value_gbp")
    benchmark_value = latest.get("benchmark_value_gbp")
    if account_value is None or benchmark_value is None or float(benchmark_value) <= 0:
        return empty

    account_value = float(account_value)
    benchmark_value = float(benchmark_value)
    as_of = _coerce_date(latest.get("date"))
    anchor_date = _coerce_date(latest.get("anchor_date"))
    elapsed_days = (as_of - anchor_date).days if (as_of and anchor_date) else 0

    return {
        "available": True,
        "as_of": as_of,
        "account_value_gbp": account_value,
        "benchmark_value_gbp": benchmark_value,
        "spread_gbp": account_value - benchmark_value,
        "spread_pct": (account_value / benchmark_value) - 1.0,
        "anchor_date": anchor_date,
        "anchor_balance_gbp": (
            float(latest["anchor_balance_gbp"])
            if latest.get("anchor_balance_gbp") is not None else None
        ),
        "elapsed_days": elapsed_days,
        "is_meaningful_period": elapsed_days >= BENCHMARK_MEANINGFUL_MIN_DAYS,
    }


def benchmark_spread_caveat(spread: dict) -> str:
    """The MANDATORY caveat shown wherever the spread is — one sentence,
    shared by the Dashboard and the weekly email so the two cannot drift
    into describing the same number differently.

    States the elapsed period and the structural reason a short reading is
    uninformative: this system runs ~63% deployed on average, so it trails a
    rising index and leads a falling one substantially regardless of how
    well it selects. That is a property of the strategy, not evidence about
    it, and the wording must not invite reading an early number as a verdict
    in either direction — note it is deliberately symmetric, warning against
    over-reading a good number exactly as much as a bad one.

    Kept to one sentence on purpose: a paragraph-length disclaimer beside a
    headline number gets skipped, which would defeat the point.
    """
    if not spread.get("available"):
        return "No benchmark comparison computed yet."
    days = spread.get("elapsed_days") or 0
    if spread.get("is_meaningful_period"):
        return (
            f"{days} days elapsed. This account holds ~63% deployed on average, so it "
            f"structurally trails a rising index and leads a falling one — read this "
            f"against the market's own direction, not on its own."
        )
    return (
        f"Only {days} days elapsed — too short to mean anything yet, in either direction. "
        f"This account holds ~63% deployed on average, so over a short window the spread "
        f"mostly reflects market direction and deployment level, not selection quality."
    )


def get_top_trades(limit: int = 5) -> tuple[list[dict], list[dict]]:
    """Top *limit* winning and bottom *limit* losing closed T212 trades by
    realised pnl_gbp (the pnl table — see get_pnl_rows()). Empty lists are
    the correct, honest result until real trading accumulates closed
    positions — not an error state; no live trade has closed yet as of
    this feature shipping."""
    rows = get_pnl_rows()
    if not rows:
        return [], []
    winners = sorted(
        (r for r in rows if (r.get("pnl_gbp") or 0.0) > 0),
        key=lambda r: r.get("pnl_gbp") or 0.0, reverse=True,
    )[:limit]
    losers = sorted(
        (r for r in rows if (r.get("pnl_gbp") or 0.0) < 0),
        key=lambda r: r.get("pnl_gbp") or 0.0,
    )[:limit]
    return winners, losers


def get_pending_candidates() -> list[dict]:
    """Every pending_orders row currently retained by the 30-day retention
    policy (`brokers/trading212.py:purge_old_pending_orders()`) — every
    decision queued at EOD, whatever became of it since: still `PENDING`,
    or resolved to `SUBMITTED`/`FAILED`/`SKIPPED_PRICE_MOVED`/
    `SKIPPED_ALREADY_HELD`/`REJECTED_CAPACITY`/`SKIPPED_CRISIS_BLOCKED`/
    `SUPERSEDED` (see `data/database.py:PendingOrder.status`'s own
    documented value set). Includes `status`/`resolution_note` so a
    resolved row's own final outcome (and why) is directly visible.

    Fixed 2026-08-03 — this previously filtered to `WHERE status =
    'PENDING'` only, so the moment a candidate resolved (submitted,
    skipped on the price-sanity check, rejected on capacity, superseded by
    a fresh re-run, ...) its row silently disappeared from every consumer
    of this function even though the row itself was still sitting in the
    table, fully within its 30-day retention window. This is a query-scope
    fix only — `purge_old_pending_orders()`'s own 30-day window is
    completely unchanged; this function simply stops re-filtering out rows
    that policy has already decided to keep.

    Ordered by admission_rank_value descending (2026-08-09 — was
    momentum_pct directly; equals it byte-identically in the default
    admission_ranking_mode, see brokers/trading212.py:_load_pending_
    orders()'s own docstring), matching the real admission-priority order
    live submission itself uses — this also means a ticker's several
    historical rows (e.g. an earlier SUPERSEDED decision and the later,
    final one that replaced it) both surface at the same ranked position,
    rather than one silently masking the other.
    """
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT ticker, is_etf, decided_at, decided_price, position_gbp,
                       momentum_pct, admission_rank_value, status, resolution_note
                FROM pending_orders
                ORDER BY admission_rank_value DESC
            """)).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


_QUEUE_PREVIEW_MARGIN = 8
_QUEUE_PREVIEW_MIN_ROWS = 20
_QUEUE_PREVIEW_MAX_ROWS = 25


def get_pending_candidates_ranked() -> list[dict]:
    """Every still-`PENDING` pending_orders row, ordered exactly the way
    `brokers/trading212.py:_load_pending_orders()` (the real submission
    priority walk) orders them: admission_rank_value descending (2026-08-09
    — was momentum_pct directly; the two are byte-identical in the default
    admission_ranking_mode, only diverging if that mode is ever switched to
    relative_rank), `decided_at` ascending as the tie-break.
    `momentum_pct` is still selected/returned unchanged, since display
    (Strength bands, the Momentum column) always shows the real absolute
    momentum regardless of which value drove ranking priority. Deliberately
    excludes every resolved status (SUBMITTED/FAILED/SKIPPED_*/
    REJECTED_CAPACITY/SUPERSEDED) — unlike get_pending_candidates() (a
    research/audit-facing read of the FULL retained history), this backs
    the operational "what hasn't been decided on yet, in the order it'll be
    tried" view (2026-08-09, see CLAUDE.md's Candidate Queue narrowing).
    Resolved rows are untouched in the database either way — this is a
    query-scope choice, not a retention or write-path change.
    """
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT ticker, position_gbp, momentum_pct, admission_rank_value
                FROM pending_orders
                WHERE status = 'PENDING'
                ORDER BY admission_rank_value DESC, decided_at ASC
            """)).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def compute_momentum_strength_bands(momentum_values) -> dict:
    """Relative, percentile-based momentum-strength band boundaries,
    computed fresh from the CURRENT pending pool's own momentum
    distribution every render — not fixed thresholds (2026-08-02;
    restored to output/performance.py 2026-08-09 alongside the Candidate
    Queue narrowing — see get_candidate_queue_preview()'s own docstring
    for why the original per-decision-date cohort grouping is no longer
    needed).

    Deliberate design choice, stated explicitly rather than assumed:
    RELATIVE bands, recalculated against today's own pool each time, not
    absolute fixed percentages. Reasoning — the same "don't hardcode a
    threshold that goes stale as conditions shift" trap this codebase has
    already been burned by for max-holding-days and the old flat position
    caps (see CLAUDE.md), just applied to a display label instead of a
    trading parameter: the whole candidate pool's momentum distribution
    moves with the market regime (a raging bull run lifts every
    candidate's momentum; a flat/choppy market compresses it), so a fixed
    absolute cutoff would either mislabel most candidates most of the
    time or need periodic manual re-tuning to stay meaningful. A relative
    percentile split adapts automatically and always reflects real,
    current conditions. The trade-off, stated honestly: labels are not
    numerically stable day to day — "Strong" today and "Strong" a month
    ago do not necessarily mean the same underlying momentum percentage —
    which is an accepted cost given the label's job is to communicate
    today's relative standing at a glance, not to be a fixed benchmark.

    Bands: bottom 25% "Weak", middle 50% (25th-75th percentile)
    "Moderate", next 15% (75th-90th) "Strong", top 10% (90th+) "Very
    Strong" — the standard percentile-decile convention, top decile as
    "Very Strong". Every candidate on this list already has a positive
    signal by construction (long/flat only — pending_orders never queues
    a negative-momentum candidate), so these bands describe the STRENGTH
    of positive momentum, not a bullish/bearish spectrum.

    Returns {} (every candidate then labels "—") when fewer than 4 usable
    values exist — not enough data to form 4 meaningful bands.
    """
    vals = pd.Series(list(momentum_values)).dropna()
    if len(vals) < 4:
        return {}
    return {
        "q25": vals.quantile(0.25),
        "q75": vals.quantile(0.75),
        "q90": vals.quantile(0.90),
    }


def momentum_strength_label(momentum_pct, bands: dict) -> str:
    """Maps one candidate's raw momentum_pct to a qualitative strength
    label via the relative bands from compute_momentum_strength_bands()
    — see that function's docstring for the full reasoning. Displayed
    ALONGSIDE the raw percentage, never in place of it, same principle as
    the fixed-denominator/peak-relative drawdown pair shown together
    elsewhere in this app."""
    if not bands or momentum_pct is None or pd.isna(momentum_pct):
        return "—"
    if momentum_pct >= bands["q90"]:
        return "Very Strong"
    if momentum_pct >= bands["q75"]:
        return "Strong"
    if momentum_pct >= bands["q25"]:
        return "Moderate"
    return "Weak"


def compute_queue_preview_cutoff(
    rows: list[dict],
    margin: int = _QUEUE_PREVIEW_MARGIN,
    min_rows: int = _QUEUE_PREVIEW_MIN_ROWS,
    max_rows: int = _QUEUE_PREVIEW_MAX_ROWS,
) -> int:
    """How many of *rows* (already ranked, each carrying a
    `projected_status`) to actually show — derived from the projected
    admission line itself, not a bare hardcoded constant: the
    `PROJECTED_ADMIT` count plus a fixed margin, clamped to
    `[min_rows, max_rows]` (and never more than `len(rows)`). At the
    system's typical ~17 admitted candidates, this lands at the
    `max_rows` ceiling (25) — "realistic set plus buffer," per the
    2026-08-09 narrowing spec.

    Falls back to `max_rows` when no row carries a real projection
    (`PROJECTED_ADMIT`/`PROJECTED_REJECT_CAPACITY`) at all — e.g. no
    stored account snapshot yet — since there's no admitted-count signal
    to build a margin on top of in that case, and showing nothing would
    be worse than showing a plain, un-prioritised top slice.
    """
    if not rows:
        return 0
    n_admit = sum(1 for r in rows if r.get("projected_status") == "PROJECTED_ADMIT")
    has_signal = any(
        r.get("projected_status") in ("PROJECTED_ADMIT", "PROJECTED_REJECT_CAPACITY")
        for r in rows
    )
    if not has_signal:
        return min(max_rows, len(rows))
    return min(max(n_admit + margin, min_rows), max_rows, len(rows))


def get_candidate_queue_preview(config: dict) -> dict:
    """The operational Candidate Queue: 'what is likely to be bought
    tomorrow, in the order it will be attempted' — not a complete
    history (2026-08-09, narrowing the original 2026-08-02/08-03
    all-statuses Candidate Queue). `PENDING` rows only, in real
    submission-priority order, truncated to just past the projected
    admission line (see compute_queue_preview_cutoff()). Resolved rows
    (REJECTED_CAPACITY, SUBMITTED, SKIPPED_*, SUPERSEDED) are
    deliberately excluded from this view — they remain in
    `pending_orders` completely untouched, still fully queryable for
    research (e.g. the selection audit's own use of REJECTED_CAPACITY
    volume — see CLAUDE.md's "Descriptive selection audit" section),
    just no longer surfaced on this operational page.

    `strength` (Weak/Moderate/Strong/Very Strong) is computed from
    compute_momentum_strength_bands() over the FULL still-PENDING pool
    (before the cutoff slice), not just the shown rows — "relative to
    today's pool" means the whole day's genuine candidate pool, not an
    already-truncated top slice, which would understate how many
    candidates are genuinely "Weak" by construction. Restored 2026-08-09
    after the initial narrowing dropped it — no longer grouped by
    decision-date cohort (the pre-2026-08-09 UI's own mechanism, needed
    only because resolved rows from several different days used to be
    shown side by side): PENDING rows only ever come from one EOD batch,
    so there is exactly one cohort to compute bands against now.

    Never raises, never makes a network call — both
    get_pending_candidates_ranked() and
    brokers.trading212.project_pending_order_admission() are themselves
    exception-safe and network-free (the latter reads a STORED T212
    snapshot, not a live one — see its own docstring).

    Returns {"rows": [...], "total_pending": int, "shown": int,
    "snapshot_checked_at": str | None, "fully_deployed": bool}.
    `fully_deployed` is True when every row in the FULL pending pool
    carries a real projection (not PROJECTION_UNAVAILABLE) and none of
    them is PROJECTED_ADMIT — i.e. the account is genuinely full right
    now, not that the projection failed (2026-08-09, added after a real
    production account hit this exact state: 308 pending rows, every one
    PROJECTED_REJECT_CAPACITY, which without this flag reads
    indistinguishably from a broken projection). Each row: {rank, ticker,
    momentum_pct, position_gbp, projected_status, strength}, rank
    1-indexed in submission-priority order.
    """
    from brokers.trading212 import project_pending_order_admission

    ranked = get_pending_candidates_ranked()
    if not ranked:
        return {
            "rows": [], "total_pending": 0, "shown": 0,
            "snapshot_checked_at": None, "fully_deployed": False,
        }

    projected = project_pending_order_admission(config)
    projected_by_ticker = {p["ticker"]: p["projected_status"] for p in projected}
    snapshot_checked_at = next(
        (p["snapshot_checked_at"] for p in projected if p.get("snapshot_checked_at")), None,
    )

    bands = compute_momentum_strength_bands([r["momentum_pct"] for r in ranked])

    all_rows = [
        {
            "rank": i,
            "ticker": r["ticker"],
            "momentum_pct": r["momentum_pct"],
            "position_gbp": r["position_gbp"],
            "projected_status": projected_by_ticker.get(r["ticker"], "PROJECTION_UNAVAILABLE"),
            "strength": momentum_strength_label(r["momentum_pct"], bands),
        }
        for i, r in enumerate(ranked, start=1)
    ]

    n_admit = sum(1 for r in all_rows if r["projected_status"] == "PROJECTED_ADMIT")
    has_signal = any(
        r["projected_status"] in ("PROJECTED_ADMIT", "PROJECTED_REJECT_CAPACITY")
        for r in all_rows
    )
    fully_deployed = has_signal and n_admit == 0

    cutoff = compute_queue_preview_cutoff(all_rows)
    return {
        "rows": all_rows[:cutoff],
        "total_pending": len(all_rows),
        "shown": cutoff,
        "snapshot_checked_at": snapshot_checked_at,
        "fully_deployed": fully_deployed,
    }


def get_recent_orders(limit: int = 10) -> list[dict]:
    """The most recent *limit* invest_orders rows, any status/direction —
    real, permanent order history (placed/filled/failed/cancelled/
    rejected, BUY and SELL alike), not the transient pre-trade decision
    queue get_pending_candidates() reads from pending_orders. Moved here
    2026-08-03 from an inline raw-SQL block in main.py's Status page (see
    CLAUDE.md's Status-page-restructuring section) as part of relocating
    this table to the Dashboard, alongside get_pending_candidates()/
    get_unconfirmed_exits() — following this module's own established
    "one shared reader per invest_orders/pending_orders shape" pattern
    rather than leaving the query duplicated inline in the UI layer."""
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT ticker, t212_ticker, direction, quantity,
                       estimated_value_gbp, var_pct, status, is_etf,
                       momentum_pct, account_type, error_message, placed_at
                FROM invest_orders
                ORDER BY placed_at DESC
                LIMIT :limit
            """), {"limit": limit}).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_unconfirmed_exits() -> list[dict]:
    """SELL orders (invest_orders) sitting in PLACED status — accepted by
    T212 but not yet resolved to FILLED/CANCELLED/REJECTED via
    poll_placed_orders(). Deliberately built against this real, existing
    status rather than a "decided but not submitted" concept, which
    doesn't exist as a distinct state for exits — maybe_exit_positions()
    decides and submits an exit in one hourly operation, so the only
    genuinely observable "in-flight" exit state is a real order this
    system already placed that T212 hasn't confirmed yet. This account
    has a documented lag here — orders have been observed sitting PLACED
    for 60+ hours before resolving (see CLAUDE.md's "Broker integration"
    section) — so this is genuinely useful to surface, not a rare edge
    case."""
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT ticker, t212_ticker, quantity, exit_reason, placed_at
                FROM invest_orders WHERE direction = 'SELL' AND status = 'PLACED'
                ORDER BY placed_at ASC
            """)).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Forward shadow comparison (2026-08-09) — the only forward evidence stream
# for the relative-ranking promotion's OPEN CANDIDATE decision. See
# CLAUDE.md's "Forward shadow comparison" section and data/database.py's
# AdmissionRankingShadow model for the full record.
# ---------------------------------------------------------------------------

# House convention (main.py's Performance Metrics section): statistical
# metrics unlock at 5 closed positions. Applied here to the active-mode-
# unique bucket's own win-rate/mean-P&L display specifically — a smaller,
# display-level threshold, distinct from the larger review-trigger floor
# below (which decides when the whole comparison is trustworthy enough to
# actually review a config change against, not merely worth showing a
# number for).
ADMISSION_SHADOW_MIN_CLOSED_FOR_STATS = 5

# The review-trigger's own sample floor (2026-08-09) — see CLAUDE.md's
# "Forward shadow comparison" section for the full justification. Backtest
# evidence put admission-order disagreement between the two rankings at
# ~77-79% of all admissions (research/relative_ranking_prereg_2026-08-09/
# REPORT.md's own A-vs-B overlap finding, 21.8-23.1%) — meaning most real
# admissions ARE active-mode-unique, the population this floor counts.
#
# RAISED 30 -> 50 on 2026-08-10, BEFORE any forward data exists, and this
# timing is the point: revising a review threshold once results are partly
# in is exactly what pre-registration discipline exists to prevent, so the
# only legitimate moment to change it is before the clock starts.
#
# 30 was originally chosen as roughly 6x the house 5-closed-position "show
# something at all" gate — enough that one trade moves the win rate only
# ±3.3pp. That reasoning is sound for SAMPLING noise and misses the failure
# mode this promotion actually has a documented precedent for: CONCENTRATION.
# The robustness test (research/relranking_robustness_2026-08-09/REPORT.md)
# found the direction of Arm B's advantage robust in 20/20 perturbation draws,
# but its MAGNITUDE partly carried by one name — dropping AMAT alone
# compressed the measured advantage from +1.993pp to +0.427pp, ~21% of
# typical, out of a 607-ticker pool. At n=30 forward outcomes a single
# position could plausibly carry the entire read, which is a different and
# larger risk than the ±3.3pp per-trade swing the original figure was sized
# against. 50 is not a precise number — it is "enough that no single outcome
# is structurally capable of dominating," paired with the explicit
# concentration guard below, which is what actually detects the AMAT case
# rather than merely making it less likely.
#
# The cost of 50 over 30 is time, and it is real: with the account currently
# fully deployed and the next admission wave gated on max-holding exits from
# early September, 50 paired outcomes is plausibly a six-month proposition
# rather than three. Accepted deliberately — the honest answer arriving slowly
# is worth more than a fast answer a single name could have produced.
# Deliberately a REPORTING threshold, not a trading parameter — lives here,
# not in analysis/decision.py or config.yaml.
ADMISSION_SHADOW_REVIEW_MIN_PAIRED_OUTCOMES = 50

ADMISSION_SHADOW_REVIEW_MIN_DAYS = 90

# Single-ticker concentration ceiling for the review trigger (2026-08-10) —
# the forward equivalent of the robustness test's own AMAT finding described
# above. If one ticker accounts for more than this share of the measured
# effect, the review REPORTS THAT AND WAITS rather than concluding: a result
# one name could produce on its own is not evidence about a ranking rule.
#
# 0.40 is a judgement, not a derived figure, and is deliberately far stricter
# than the AMAT precedent it is calibrated against (AMAT carried ~79% of the
# backtest advantage). The asymmetry is intentional — the backtest had 607
# tickers and thousands of trades to dilute one name, where 50 forward
# outcomes do not, so the forward guard has to trip earlier to catch the same
# shape of problem. A ticker sitting between 40% and 79% would have passed
# unremarked in the backtest and is exactly the case worth pausing on here.
ADMISSION_SHADOW_REVIEW_MAX_SINGLE_TICKER_SHARE = 0.40


def get_admission_ranking_shadow_rows() -> list[dict]:
    """Every recorded forward-shadow-comparison decision, oldest first —
    thin wrapper around data.database.get_all_admission_ranking_shadow_
    rows() matching this module's own get_*()-does-the-DB-read
    convention. A pure DB read; no T212/yfinance call, matching every
    other function in this module."""
    from data.database import get_all_admission_ranking_shadow_rows
    try:
        return get_all_admission_ranking_shadow_rows()
    except Exception:
        return []


def _classify_shadow_row_admission(row: dict) -> tuple:
    """(own_admitted, other_admitted) for one shadow row, evaluated against
    that row's OWN recorded `active_mode` rather than today's config — so
    the classification stays correct across a mid-history
    admission_ranking_mode change (rows recorded before a switch keep their
    own original active mode).

    Extracted 2026-08-10 so compute_admission_ranking_shadow_summary() and
    compute_admission_ranking_shadow_concentration() cannot drift apart on
    what "active-mode-unique" means — the two must agree exactly, since the
    concentration guard is computed over precisely the population the
    summary reports stats for. Per this project's standing "one
    implementation of any given rule" invariant, this is one function rather
    than two matching if/else blocks that are merely supposed to agree.
    """
    if row.get("active_mode") == "relative_rank":
        return (bool(row.get("admitted_under_relative_rank")),
                bool(row.get("admitted_under_absolute_momentum")))
    # default / absolute_momentum / any unrecognised value
    return (bool(row.get("admitted_under_absolute_momentum")),
            bool(row.get("admitted_under_relative_rank")))


def compute_admission_ranking_shadow_summary(rows: list[dict]) -> dict:
    """Pure aggregation over already-fetched shadow rows (see
    get_admission_ranking_shadow_rows()) — no DB/network access itself,
    matching compute_sector_breakdown()'s own pure-function pattern.

    **The structural asymmetry this function's output must never blur,
    stated here so a caller can't accidentally present it as symmetric**:
    only ONE ranking mode is ever genuinely live-traded for any given
    row (that row's own recorded `active_mode`) — a REAL, realised
    outcome can only ever exist for a candidate the row's OWN active mode
    admitted. This means:
      - `shared["count"]` — both rankings would admit this candidate.
        Real trades exist for these (the active mode bought them), but
        they carry NO DISCRIMINATING SIGNAL — the inactive ranking would
        have made the identical trade, so its outcome says nothing about
        which ranking is better.
      - `active_unique` — admitted by this row's own active mode ONLY.
        THE signal: real trades the active ranking chose that the
        alternative wouldn't have. `closed_count`/`win_rate`/
        `mean_pnl_pct` are computed from real, realised outcomes once
        `closed_count` clears ADMISSION_SHADOW_MIN_CLOSED_FOR_STATS.
      - `inactive_unique` — admitted by the OTHER (inactive-for-that-row)
        mode ONLY. These were NEVER bought — no live evidence can ever
        exist for this specific decision, structurally, not merely
        because the sample is still small. `closed_count` is always 0,
        `win_rate`/`mean_pnl_pct` are always None, by construction — a
        caller must show this as a COUNT ONLY, explicitly labelled as
        having no live outcome, never as "0% win rate" or blank.

    Every row is classified by comparing its OWN `admitted_under_<active_
    mode>` against its OWN `admitted_under_<the other mode>` — evaluated
    per-row, not against today's config — so this remains correct even
    across a future admission_ranking_mode config change partway through
    the table's history (rows recorded before the switch keep their own
    original active_mode).
    """
    total_decisions = len(rows)
    shared_count = 0
    active_unique_rows: list[dict] = []
    inactive_unique_rows: list[dict] = []

    for r in rows:
        own_admitted, other_admitted = _classify_shadow_row_admission(r)

        if own_admitted and other_admitted:
            shared_count += 1
        elif own_admitted and not other_admitted:
            active_unique_rows.append(r)
        elif other_admitted and not own_admitted:
            inactive_unique_rows.append(r)
        # neither admitted -> not a signal either ranking would have acted on; excluded from all three buckets

    def _bucket(bucket_rows: list[dict], apply_min_sample: bool) -> dict:
        closed = [r for r in bucket_rows if r.get("outcome_available")]
        closed_count = len(closed)
        out = {"count": len(bucket_rows), "closed_count": closed_count, "win_rate": None, "mean_pnl_pct": None}
        if closed_count == 0:
            return out
        if apply_min_sample and closed_count < ADMISSION_SHADOW_MIN_CLOSED_FOR_STATS:
            return out
        wins = sum(1 for r in closed if (r.get("pnl_gbp") or 0) > 0)
        out["win_rate"] = wins / closed_count
        pnl_pcts = [r.get("pnl_pct") for r in closed if r.get("pnl_pct") is not None]
        out["mean_pnl_pct"] = (sum(pnl_pcts) / len(pnl_pcts)) if pnl_pcts else None
        return out

    active_unique = _bucket(active_unique_rows, apply_min_sample=True)
    # inactive_unique's own closed_count is always 0 by construction (see
    # this function's own docstring) -- min-sample gating is applied
    # anyway, for defensive symmetry, though it can never actually bind.
    inactive_unique = _bucket(inactive_unique_rows, apply_min_sample=True)

    return {
        "total_decisions": total_decisions,
        "shared": {"count": shared_count},
        "active_unique": active_unique,
        "inactive_unique": inactive_unique,
        "current_active_mode": rows[-1]["active_mode"] if rows else None,
    }


def compute_admission_ranking_shadow_concentration(rows: list[dict]) -> dict:
    """How much of the measured effect rests on any single ticker — the
    forward equivalent of the robustness test's own AMAT finding (see
    ADMISSION_SHADOW_REVIEW_MAX_SINGLE_TICKER_SHARE's comment). Pure
    aggregation over already-fetched shadow rows; no DB/network access.

    Takes the FULL row list and does its own active-unique filtering via the
    shared _classify_shadow_row_admission() — deliberately not a pre-filtered
    list from the caller, which would make every call site re-implement the
    classification and let the guard silently measure a different population
    than the summary reports on.

    Scope: ACTIVE-MODE-UNIQUE, CLOSED rows only — the same population
    compute_admission_ranking_shadow_summary()'s `active_unique` bucket
    reports on, and the only one carrying a discriminating signal (shared
    rows would have been admitted by either ranking; inactive-unique rows
    were never bought, so no outcome exists for them by construction).

    **Metric, stated explicitly because several defensible definitions
    exist and the choice changes what trips the guard**: each ticker's
    share of the summed ABSOLUTE `pnl_pct` across those rows —
    `sum(|pnl_pct|) for that ticker / sum(|pnl_pct|) for all of them`.
    Absolute, not signed, because the question is "how much of the
    measured movement does one name account for," and a large loser
    dominates the read exactly as much as a large winner does; a signed
    share would let a +40% and a −40% name cancel to zero and report a
    concentrated sample as diverse. Grouped by TICKER, not by row, so a
    name entered several times is correctly treated as one concentration
    risk rather than several independent ones.

    Two alternatives were considered and rejected. Share of trade COUNT
    misses the case this guard exists for entirely — the AMAT precedent was
    one name carrying the magnitude, not the frequency. Leave-one-out
    recomputation of `mean_pnl_pct` is the most literal analogue of the
    robustness test, but it measures sensitivity of a mean rather than
    concentration of the underlying contribution, and it becomes unstable
    when the full-sample mean sits near zero (the denominator collapses and
    the ratio explodes) — a state this comparison could genuinely occupy if
    the two rankings turn out to perform similarly, which is a live
    possible outcome, not a remote edge case.

    A load-bearing consequence worth stating rather than discovering later:
    with fewer than three contributing names, some ticker necessarily holds
    more than 40% arithmetically, so the guard always trips on a very small
    sample. That is the correct answer, not an artefact — a two-name sample
    cannot distinguish a ranking rule from one lucky position — and it means
    this guard doubles as a diversity floor alongside the raw outcome count.
    Do not special-case it away.

    Returns {"top_ticker", "top_share", "concentrated", "measurable",
    "ticker_count"}. `measurable` is False — and `concentrated` False, never
    None-as-falsy-by-accident — when there is nothing to compute a share
    from (no closed active-unique rows, no usable `pnl_pct`, or a total
    absolute P&L of zero). An unmeasurable sample must not read as a clean
    bill of health: callers gate on `sample_reached` separately, and the
    review trigger below treats "cannot measure" as "cannot conclude."
    """
    closed = []
    for r in rows:
        own_admitted, other_admitted = _classify_shadow_row_admission(r)
        if not (own_admitted and not other_admitted):
            continue  # not active-mode-unique
        if r.get("outcome_available") and r.get("pnl_pct") is not None:
            closed.append(r)
    if not closed:
        return {"top_ticker": None, "top_share": None, "concentrated": False,
                "measurable": False, "ticker_count": 0}

    by_ticker: dict[str, float] = {}
    for r in closed:
        ticker = r.get("ticker")
        if not ticker:
            continue
        by_ticker[ticker] = by_ticker.get(ticker, 0.0) + abs(float(r["pnl_pct"]))

    total_abs = sum(by_ticker.values())
    if not by_ticker or total_abs <= 0:
        # Every closed outcome was exactly flat — a real possibility, and
        # genuinely unmeasurable rather than perfectly diverse.
        return {"top_ticker": None, "top_share": None, "concentrated": False,
                "measurable": False, "ticker_count": len(by_ticker)}

    top_ticker = max(by_ticker, key=lambda t: by_ticker[t])
    top_share = by_ticker[top_ticker] / total_abs
    return {
        "top_ticker": top_ticker,
        "top_share": top_share,
        "concentrated": top_share > ADMISSION_SHADOW_REVIEW_MAX_SINGLE_TICKER_SHARE,
        "measurable": True,
        "ticker_count": len(by_ticker),
    }


def compute_admission_ranking_shadow_review_status(
    active_unique_closed_count: int, first_decided_at, concentration: dict = None,
) -> dict:
    """Whether the forward shadow comparison's review trigger has fired —
    ALL THREE of (i) ADMISSION_SHADOW_REVIEW_MIN_DAYS (90) days from the
    first recorded decision, (ii) ADMISSION_SHADOW_REVIEW_MIN_PAIRED_
    OUTCOMES (50) active-mode-unique closed outcomes, and (iii) no single
    ticker accounting for more than ADMISSION_SHADOW_REVIEW_MAX_SINGLE_
    TICKER_SHARE (40%) of the measured effect — see this module's own
    constant docstrings and CLAUDE.md's "Forward shadow comparison" section
    for the full justification. Deriving the review date from
    first_decided_at (never hardcoded at build time) means a delay in
    shipping this feature does not silently shorten the review window.

    Conditions (i) and (ii) are a WHICHEVER-COMES-LATER pair — both are
    "has enough accumulated." Condition (iii) is different in kind: it is
    not satisfied by waiting for a threshold to be crossed, it is a
    property of whatever sample has accumulated, and it can flip back and
    forth as outcomes land. A concentrated sample does not mean "not yet" in
    the way the other two do — it means the sample that exists cannot answer
    the question, and the honest response is to report the concentration and
    keep accumulating (2026-08-10; the forward equivalent of the robustness
    test's own AMAT finding, added before any forward data existed).

    active_unique_closed_count is the interpretable sample — the same
    figure compute_admission_ranking_shadow_summary()'s own
    active_unique["closed_count"] returns, not total_decisions (which
    includes shared/inactive-unique rows that carry no outcome signal).

    concentration is compute_admission_ranking_shadow_concentration()'s own
    return dict, or None when a caller genuinely has no rows to compute it
    from. **None is treated as "cannot conclude," never as "no concentration
    problem"** — an unmeasurable sample must not read as a clean bill of
    health, the same reasoning that function's own `measurable` flag exists
    for. This is a keyword-defaulted parameter only so pre-existing callers
    do not silently break; a caller that has the rows should always pass it.

    Returns {"review_date", "days_reached", "sample_reached",
    "concentration_ok", "review_due", "paired_outcomes", "floor",
    "top_ticker", "top_share", "max_single_ticker_share"}. review_date is
    None (and every *_reached flag False) if first_decided_at is None (no
    shadow data recorded yet)."""
    conc = concentration or {}
    conc_measurable = bool(conc.get("measurable"))
    # Only a MEASURED, non-concentrated sample clears this condition.
    # Unmeasurable (no closed rows yet, or none carrying pnl_pct) is
    # deliberately NOT ok -- see the docstring above.
    concentration_ok = conc_measurable and not conc.get("concentrated", False)
    base = {
        "paired_outcomes": active_unique_closed_count,
        "floor": ADMISSION_SHADOW_REVIEW_MIN_PAIRED_OUTCOMES,
        "concentration_ok": concentration_ok,
        "top_ticker": conc.get("top_ticker"),
        "top_share": conc.get("top_share"),
        "max_single_ticker_share": ADMISSION_SHADOW_REVIEW_MAX_SINGLE_TICKER_SHARE,
    }
    if first_decided_at is None:
        return {
            **base,
            "review_date": None, "days_reached": False, "sample_reached": False,
            "review_due": False,
        }
    review_date = first_decided_at + timedelta(days=ADMISSION_SHADOW_REVIEW_MIN_DAYS)
    days_reached = datetime.utcnow() >= review_date
    sample_reached = active_unique_closed_count >= ADMISSION_SHADOW_REVIEW_MIN_PAIRED_OUTCOMES
    return {
        **base,
        "review_date": review_date, "days_reached": days_reached,
        "sample_reached": sample_reached,
        "review_due": days_reached and sample_reached and concentration_ok,
    }
