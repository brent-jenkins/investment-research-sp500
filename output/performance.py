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


def get_ticker_status(config: dict) -> list[dict]:
    """Per-ticker live status for every tracked ticker: momentum signal
    (long/flat), current index-membership eligibility, and whether today is
    a raw crisis day / inside the crisis entry-block window.

    Recomputes momentum from full price history for every ticker in the
    live, dynamically-refreshed S&P 500 candidate pool
    (analysis/universe.py:get_live_stock_candidates() — replaces the old
    static config.yaml `instruments:` list, see CLAUDE.md's "wire the
    momentum-only, full-S&P-500 selection into live execution" section) —
    the same per-ticker cost brokers/trading212.py's own _ticker_state()
    pays once per scheduled run. Callers on a page render should cache
    this (see main.py:_get_ticker_status()'s st.cache_data wrapper).
    """
    from analysis import crisis, signals, universe
    from data.ingestion import get_price_df

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
    recomputing it here; defaults to a fresh call when omitted.
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
        ticker_status_rows = get_ticker_status(config)
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

    `entry_blocked` is sourced from brokers.trading212.is_crisis_mode_
    active_now() (2026-08-03) — the same single, authoritative check
    order submission itself re-confirms fresh before ever placing a new
    entry (see CLAUDE.md's "Submission-time crisis re-check" and
    "Crisis-mode dashboard banner" sections) — never a second,
    independently-derived daily-bar-only computation. This matters:
    is_crisis_mode_active_now() also checks a genuinely live intraday
    quote, which this function's own `today_return_pct`/
    `distance_to_threshold_pp` fields below (still computed from stored
    daily-bar data, since they're purely informational display context,
    not a gating decision) cannot see — a crisis visible only intraday
    would otherwise have shown here as "not blocked" while order
    submission was already correctly refusing every new entry.
    is_crisis_mode_active_now() returning None (a data-read failure) maps
    to entry_blocked=False here — a display-only function fails toward
    showing nothing rather than a stale or fabricated verdict; the real
    safety mechanism lives entirely in order submission's own None
    handling, not in this display value.
    """
    from analysis import crisis
    from brokers.trading212 import is_crisis_mode_active_now
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
    entry_blocked = bool(is_crisis_mode_active_now())

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
        "cooldown_days": crisis.ENTRY_BLOCK_COOLDOWN_DAYS,
        "last_trigger_date": last_trigger_date,
        "days_since_trigger": days_since_trigger,
    }


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
    """Every PENDING pending_orders row — decisions queued at EOD,
    awaiting their exchange-timed submission window (see CLAUDE.md's
    "Exchange-aware order submission" section). Ordered by momentum
    descending, matching the real admission-priority order live
    submission itself uses (brokers/trading212.py:_load_pending_orders())."""
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT ticker, is_etf, decided_at, decided_price, position_gbp, momentum_pct
                FROM pending_orders WHERE status = 'PENDING'
                ORDER BY momentum_pct DESC
            """)).mappings().all()
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
