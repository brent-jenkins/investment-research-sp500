"""Realised P&L reconstruction for closed T212 Invest positions.

Pairs each FILLED BUY order in `t212_invest_orders` with the next FILLED SELL
order for the same ticker and writes one row per closed position to the
`t212_pnl` table (see `data/database.py:T212Pnl`). This is the execution-cost
data source a later stage will wire the feedback-learning system to, replacing
the now-defunct virtual-trading and manual-CFD-trade sources — see CLAUDE.md
"Removed: virtual trading" / "Removed: manual CFD trade logging".

Called from `brokers/trading212.py:maybe_exit_t212_positions()` and
`maybe_exit_stock_positions()`, so it runs on the same hourly/EOD cadence as
the exit checks. It only ever reconstructs pairs where BOTH the BUY and SELL
have already reached FILLED status via `poll_placed_orders()` — a SELL that
was just placed this run is still PLACED, so its pair is picked up on a later
call once the fill is confirmed, not immediately.
"""

import logging
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _fx_rate_as_of(conn, series_ticker: str, as_of: datetime, default: float) -> float:
    """Return the *series_ticker* close price on or before *as_of* (falls back to *default*).

    Compares via SQLite's date() function rather than a raw string comparison
    on the `date` column — a Python datetime with microsecond=0 stringifies
    without a fractional-seconds component (e.g. "2026-06-20 00:00:00"), while
    SQLAlchemy's DateTime column always stores one (".000000"), so a raw
    `date <= :d` comparison silently excludes same-day rows more often than
    not. date() normalises both sides to day granularity, which is all that
    matters anyway since price_data is daily bars.
    """
    from sqlalchemy import text as sa_text
    row = conn.execute(sa_text("""
        SELECT close FROM price_data
        WHERE ticker = :t AND date(date) <= date(:d)
        ORDER BY date DESC LIMIT 1
    """), {"t": series_ticker, "d": as_of}).fetchone()
    return float(row[0]) if row and row[0] else default


def _native_to_gbp(amount: float, currency: Optional[str], gbpusd: float, eurusd: float) -> float:
    """Convert *amount* in instrument-native currency to GBP.

    Mirrors brokers/trading212.py:poll_placed_orders()'s inline _native_to_gbp()
    — kept as its own copy here since this module's scope is intentionally
    limited to P&L reconstruction (see CLAUDE.md "T212 P&L reconstruction").
    """
    ccy = (currency or "").upper()
    if not ccy or ccy == "GBP":
        return amount
    if ccy == "GBX":  # LSE pence quoting
        return amount / 100.0
    if ccy == "USD":
        return amount / gbpusd if gbpusd else amount
    if ccy == "EUR":
        return amount * eurusd / gbpusd if gbpusd else amount
    return amount  # unknown currency — return unconverted


def _module_scores_as_of(conn, ticker: str, as_of: datetime) -> dict:
    """Return the five module scores from the nearest signals row on or before *as_of*.

    t212_invest_orders does not store per-module scores (only confluence_score),
    so these are looked up retroactively from the signals table instead of
    requiring a change to order placement. Uses date() rather than a raw `<=`
    comparison — see _fx_rate_as_of()'s docstring for why.
    """
    from sqlalchemy import text as sa_text
    row = conn.execute(sa_text("""
        SELECT trend_score, mean_reversion_score, volatility_score,
               volume_score, macro_score
        FROM signals
        WHERE ticker = :t AND date(date) <= date(:d)
        ORDER BY date DESC LIMIT 1
    """), {"t": ticker, "d": as_of}).mappings().first()
    if not row:
        return {
            "trend_score": None, "mean_reversion_score": None,
            "volatility_score": None, "volume_score": None, "macro_score": None,
        }
    return dict(row)


def _reconstruct_t212_pnl(config: dict) -> int:
    """Reconstruct realised P&L for every newly-closed T212 Invest position.

    Reads all FILLED BUY/SELL orders from t212_invest_orders and pairs each
    BUY with the next FILLED SELL for the same ticker (FIFO — this codebase
    never holds more than one open T212 position per ticker at a time, so
    BUY/SELL pairs never interleave). Idempotent: (buy_order_id, sell_order_id)
    is a unique constraint on t212_pnl, and already-written pairs are also
    filtered out before insert, so calling this repeatedly (e.g. every hour)
    only ever writes rows for positions closed since the last call.

    P&L: (sell_fill_price - buy_fill_price) x quantity, in the instrument's
    native currency, converted to GBP using GBPUSD=X / EURUSD=X price_data
    rows as of the sell's fill date (not necessarily today's rate — this may
    be reconstructing a backlog of older closes).

    Returns the number of newly-written rows. Never raises — any DB error is
    logged and swallowed, matching the rest of this package's automation
    functions.
    """
    from sqlalchemy import text as sa_text
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from data.database import get_engine, T212Pnl

    engine = get_engine()

    try:
        with engine.connect() as conn:
            order_rows = conn.execute(sa_text("""
                SELECT id, ticker, t212_ticker, direction, filled_quantity, quantity,
                       fill_price, currency_code, signal_type, confluence_score,
                       regime, exit_reason, filled_at
                FROM t212_invest_orders
                WHERE status = 'FILLED' AND filled_at IS NOT NULL AND fill_price IS NOT NULL
                ORDER BY ticker, filled_at ASC
            """)).mappings().all()
    except Exception as exc:
        logger.warning("_reconstruct_t212_pnl: could not read t212_invest_orders — %s", exc)
        return 0

    if not order_rows:
        return 0

    # FIFO-pair BUY/SELL orders per ticker.
    open_buys: dict[str, deque] = defaultdict(deque)
    pairs: list[tuple[dict, dict]] = []
    for row in order_rows:
        r = dict(row)
        if r["direction"] == "BUY":
            open_buys[r["ticker"]].append(r)
        elif r["direction"] == "SELL":
            queue = open_buys.get(r["ticker"])
            if queue:
                pairs.append((queue.popleft(), r))
            # else: SELL with no unmatched prior BUY (e.g. legacy/manual data) — skip.

    if not pairs:
        return 0

    # Skip pairs already reconstructed — cheap Python-side filter ahead of the
    # DB-level unique constraint (which stays in place as a defensive backstop).
    try:
        with engine.connect() as conn:
            existing_rows = conn.execute(
                sa_text("SELECT buy_order_id, sell_order_id FROM t212_pnl")
            ).fetchall()
        existing_keys = {(r[0], r[1]) for r in existing_rows}
    except Exception as exc:
        logger.warning("_reconstruct_t212_pnl: could not read existing t212_pnl rows — %s", exc)
        existing_keys = set()

    new_pairs = [(b, s) for b, s in pairs if (b["id"], s["id"]) not in existing_keys]
    if not new_pairs:
        return 0

    inserted = 0
    for buy, sell in new_pairs:
        buy_price  = buy.get("fill_price")
        sell_price = sell.get("fill_price")
        if not buy_price or sell_price is None:
            continue

        quantity = sell.get("filled_quantity") or sell.get("quantity") or 0
        if not quantity:
            continue

        entry_dt = buy.get("filled_at")
        exit_dt  = sell.get("filled_at")
        if isinstance(entry_dt, str):
            entry_dt = datetime.fromisoformat(entry_dt)
        if isinstance(exit_dt, str):
            exit_dt = datetime.fromisoformat(exit_dt)

        try:
            with engine.begin() as conn:
                currency = buy.get("currency_code") or sell.get("currency_code") or "USD"
                gbpusd = _fx_rate_as_of(conn, "GBPUSD=X", exit_dt or datetime.utcnow(), 1.27)
                eurusd = _fx_rate_as_of(conn, "EURUSD=X", exit_dt or datetime.utcnow(), 1.08)
                module_scores = _module_scores_as_of(
                    conn, buy["ticker"], entry_dt or datetime.utcnow()
                )

                pnl_native = (sell_price - buy_price) * quantity
                pnl_gbp = round(_native_to_gbp(pnl_native, currency, gbpusd, eurusd), 4)
                pnl_pct = round((sell_price - buy_price) / buy_price, 6)
                holding_days = (exit_dt - entry_dt).days if (exit_dt and entry_dt) else None

                row = {
                    "ticker":            buy["ticker"],
                    "t212_ticker":       buy.get("t212_ticker"),
                    "buy_order_id":      buy["id"],
                    "sell_order_id":     sell["id"],
                    "entry_date":        entry_dt,
                    "exit_date":         exit_dt,
                    "entry_price":       buy_price,
                    "exit_price":        sell_price,
                    "quantity":          quantity,
                    "currency":          currency,
                    "pnl_gbp":           pnl_gbp,
                    "pnl_pct":           pnl_pct,
                    "holding_days":      holding_days,
                    "signal_type":       buy.get("signal_type"),
                    "confluence_score":  buy.get("confluence_score"),
                    "regime":            buy.get("regime"),
                    "exit_reason":       sell.get("exit_reason"),
                    **module_scores,
                }

                stmt = sqlite_insert(T212Pnl).values([row])
                stmt = stmt.on_conflict_do_nothing(index_elements=["buy_order_id", "sell_order_id"])
                conn.execute(stmt)
                inserted += 1
        except Exception as exc:
            logger.warning(
                "_reconstruct_t212_pnl: failed to write pnl row for %s (buy=%s, sell=%s): %s",
                buy.get("ticker"), buy.get("id"), sell.get("id"), exc,
            )
            continue

    if inserted:
        logger.info("_reconstruct_t212_pnl: reconstructed %d newly-closed T212 position(s).", inserted)
    return inserted
