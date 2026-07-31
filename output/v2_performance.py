"""Pure data-shaping logic for analysis/v2's UI display — extracted out of
main.py so it's actually testable.

main.py cannot be imported directly in tests: it runs _start_scheduler_at_startup()
(an @st.cache_resource call) at module level, which would start a real
scheduler process — including live T212 API calls — as a side effect of
merely importing the module. Every function here is a plain, side-effect-free
(besides DB reads) data-shaping function; main.py's own thin wrappers just
call into this module and add Streamlit caching/rendering on top.

Kept genuinely separate from output/performance.py (V1's equivalent helpers)
— same principle as V2InvestOrder/V2Pnl being separate tables from V1's.
"""
from datetime import datetime
from typing import Optional

import pandas as pd

from data.database import get_engine, get_metadata_str


def get_v2_ticker_status(config: dict) -> list[dict]:
    """Per-ticker live status for every analysis/v2 ticker: momentum signal
    (long/flat), current index-membership eligibility, and whether today is
    a raw crisis day / inside the crisis entry-block window.

    Recomputes momentum from full price history for every ticker in
    trading212_v2.instrument_mapping — the same per-ticker cost
    brokers/trading212_v2.py's own _v2_ticker_state() pays once per
    scheduled run. Callers on a page render should cache this (see
    main.py:_get_v2_ticker_status()'s st.cache_data wrapper).
    """
    from analysis.v2 import crisis as v2_crisis
    from analysis.v2 import signals as v2_signals
    from analysis.v2 import universe as v2_universe
    from data.ingestion import get_price_df

    mapping = config.get("trading212_v2", {}).get("instrument_mapping", {})
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

            sig_series = v2_signals.compute_signal_series(close)
            mom_series = v2_signals.compute_momentum_series(close)
            sig_on = bool(sig_series.iloc[-1])
            mom_pct = float(mom_series.iloc[-1]) if pd.notna(mom_series.iloc[-1]) else None
            is_member = is_etf or v2_universe.is_member(ticker, latest_dt.date())
            crisis_now = v2_crisis.is_crisis_day(gspc_close, latest_dt)
            entry_blocked = v2_crisis.is_entry_blocked(gspc_close, latest_dt)

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


def get_v2_brief_cards(
    config: dict,
    ticker_status_rows: Optional[list[dict]] = None,
) -> list[dict]:
    """Return enriched trade-card dicts for open analysis/v2 positions.

    Reads the same scheduler-maintained t212_positions_snapshot
    main.py:_get_t212_invest_brief_cards() reads (one shared T212 account)
    filtered to trading212_v2.instrument_mapping, plus entry context
    (momentum_pct, stop_price) from v2_invest_orders. Status mirrors
    brokers/trading212_v2.py's own exit precedence exactly: crisis >
    index-removal > stop-loss > signal-off > max-holding.

    ticker_status_rows lets a caller pass in an already-computed
    get_v2_ticker_status() result (e.g. main.py's cached version) instead of
    recomputing it here; defaults to a fresh call when omitted.
    """
    import json
    import os

    from analysis.regime import get_max_holding_days

    if not (os.environ.get("T212_API_KEY", "") and os.environ.get("T212_API_SECRET", "")):
        return []

    v2_cfg = config.get("trading212_v2", {})
    mapping = v2_cfg.get("instrument_mapping", {})
    if not mapping:
        return []

    reverse_map: dict[str, tuple[str, bool]] = {}
    for rt, raw in mapping.items():
        t212_ticker = raw.get("ticker") if isinstance(raw, dict) else raw
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
                FROM v2_invest_orders
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
        ticker_status_rows = get_v2_ticker_status(config)
    status_by_ticker = {r["ticker"]: r for r in ticker_status_rows}

    # Reuse the exact same GBP conversion brokers/trading212_v2.py itself used
    # to compute estimated_value_gbp/pnl_gbp at order time — reads straight
    # from price_data (GBPUSD=X/EURUSD=X), no live yfinance call, no session
    # state. Deliberately not main.py's own _get_fx_rate() (a heavier,
    # session-cached, live-yfinance-fetching helper built for V1's UI), for
    # consistency with what V2's execution path already used.
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

        # Exit precedence -- must exactly match brokers/trading212_v2.py's
        # maybe_exit_v2_positions(), which itself mirrors
        # analysis/v2/backtest.py:simulate_trades() exactly.
        if crisis_now:
            status = "CRISIS EXIT"
        elif not is_etf and is_member is False:
            status = "INDEX REMOVAL"
        elif stop_price is not None and current_price > 0 and current_price <= stop_price:
            status = "STOP LOSS"
        elif sig_on is False:
            status = "SIGNAL OFF"
        elif days_held >= max_hold:
            status = "MAX HOLDING"
        else:
            status = "ON TRACK"

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


def get_v2_pnl_rows() -> list[dict]:
    """All reconstructed closed analysis/v2 positions, most recent first.

    Reads brokers/trading212_v2.py:_reconstruct_v2_pnl()'s output table
    directly — the same pattern as output/performance.py's V1 equivalent,
    against v2_pnl instead of t212_pnl. Genuinely separate data, never
    merged with V1's.
    """
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                sa_text("SELECT * FROM v2_pnl ORDER BY exit_date DESC")
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def v2_pnl_metrics_df(pnl_rows: list[dict]) -> pd.DataFrame:
    """Adapt v2_pnl rows to the column shape analysis/backtest_metrics.py
    expects — same reuse-not-reimplement rationale as V1's
    _t212_pnl_metrics_df(). No regime_at_entry column: V2 has no regime
    classifier, so regime_performance() is never called against this frame.
    """
    if not pnl_rows:
        return pd.DataFrame()
    df = pd.DataFrame(pnl_rows)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"]  = pd.to_datetime(df["exit_date"])
    df["pnl_pct"]    = df["pnl_pct"].astype(float)
    df["is_win"]     = df["pnl_gbp"] > 0
    df["direction"]  = "LONG"
    return df
