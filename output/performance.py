"""Pure data-shaping logic for the Performance/Status/Daily-Briefing UI —
extracted out of main.py so it's actually testable.

main.py cannot be imported directly in tests: it runs _start_scheduler_at_startup()
(an @st.cache_resource call) at module level, which would start a real
scheduler process — including live T212 API calls — as a side effect of
merely importing the module. Every function here is a plain, side-effect-free
(besides DB reads) data-shaping function; main.py's own thin wrappers just
call into this module and add Streamlit caching/rendering on top.
"""
from datetime import datetime
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

    Ordered by momentum descending, matching the real admission-priority
    order live submission itself uses
    (`brokers/trading212.py:_load_pending_orders()`) — this also means a
    ticker's several historical rows (e.g. an earlier SUPERSEDED decision
    and the later, final one that replaced it) both surface at the same
    momentum-ranked position, rather than one silently masking the other.
    """
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT ticker, is_etf, decided_at, decided_price, position_gbp,
                       momentum_pct, status, resolution_note
                FROM pending_orders
                ORDER BY momentum_pct DESC
            """)).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


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
