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
    top-level `instruments:` section (config.yaml, consolidated 2026-07-31
    from watchlist.trading + trading212_invest.instrument_mapping — see
    CLAUDE.md's "Unified instrument structure" section) — the same
    per-ticker cost brokers/trading212.py's own _ticker_state() pays once
    per scheduled run. Callers on a page render should cache this (see
    main.py:_get_ticker_status()'s st.cache_data wrapper).
    """
    from analysis import crisis, signals, universe
    from data.ingestion import get_price_df

    mapping = config.get("instruments", {})
    if not mapping:
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
    for ticker, raw in mapping.items():
        is_etf = bool(raw.get("is_etf", False)) if isinstance(raw, dict) else False
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
    account) filtered to the top-level `instruments:` section (config.yaml,
    consolidated 2026-07-31 from watchlist.trading + trading212_invest.
    instrument_mapping — see CLAUDE.md's "Unified instrument structure"
    section), plus entry context (momentum_pct, stop_price) from
    invest_orders. Status mirrors brokers/trading212.py's own exit
    precedence exactly: crisis > index-removal > stop-loss > signal-off >
    max-holding.

    ticker_status_rows lets a caller pass in an already-computed
    get_ticker_status() result (e.g. main.py's cached version) instead of
    recomputing it here; defaults to a fresh call when omitted.
    """
    import json
    import os

    from analysis.decision import evaluate_exit_reason
    from analysis.universe import get_max_holding_days

    if not (os.environ.get("T212_API_KEY", "") and os.environ.get("T212_API_SECRET", "")):
        return []

    t212_cfg = config.get("trading212_invest", {})
    mapping = config.get("instruments", {})
    if not mapping:
        return []

    reverse_map: dict[str, tuple[str, bool]] = {}
    for rt, raw in mapping.items():
        t212_ticker = raw.get("t212_instrument_id") if isinstance(raw, dict) else raw
        is_etf = bool(raw.get("is_etf", False)) if isinstance(raw, dict) else False
        reverse_map[t212_ticker] = (rt, is_etf)

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
        # don't trigger" handling applies.
        price_for_stop_check = current_price if current_price > 0 else None
        exit_reason = evaluate_exit_reason(
            is_member=is_member, crisis_now=crisis_now,
            price=price_for_stop_check, stop_price=stop_price, sig_on=sig_on,
            holding_days=days_held, max_holding_days=max_hold,
        )
        status = {
            "CRISIS_EXIT": "CRISIS EXIT", "INDEX_REMOVAL": "INDEX REMOVAL",
            "STOP_LOSS": "STOP LOSS", "SIGNAL_OFF": "SIGNAL OFF",
            "MAX_HOLDING": "MAX HOLDING",
        }.get(exit_reason, "ON TRACK")

        status_priority = {
            "CRISIS EXIT": 4, "INDEX REMOVAL": 3, "STOP LOSS": 3,
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
    flag. Returns {} if price data isn't available yet."""
    from analysis import crisis
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
    entry_blocked = crisis.is_entry_blocked(close, latest_dt)

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
