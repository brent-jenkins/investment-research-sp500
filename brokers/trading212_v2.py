"""analysis/v2 execution — automated order placement on the T212 Invest
DEMO account for the momentum + crisis-override strategy validated across
the phased V2 rebuild (see CLAUDE.md's "V2 architecture" section).

Reuses Trading212Client, VaR position sizing, and get_max_holding_days()
unchanged from brokers/trading212.py / analysis/volatility.py /
analysis/regime.py — only the entry/exit *decision* logic is new, and it is
built to mirror analysis/v2/backtest.py:simulate_trades() exactly, not a
redesigned approximation of it:

  Entry (all must hold): momentum signal on, ticker currently a genuine
  index member (or an ETF — no membership concept), not in the crisis
  entry-block window.

  Exit priority (checked in this exact order, matching simulate_trades()):
    1. CRISIS_EXIT   — today is a raw (>=7%) crisis day
    2. INDEX_REMOVAL — ticker is no longer a genuine index member (stocks only)
    3. STOP_LOSS     — latest close has reached the ATR stop fixed at entry
    4. SIGNAL_OFF    — trailing 12m momentum has turned non-positive
    5. MAX_HOLDING   — get_max_holding_days() backstop reached

Writes to v2_invest_orders / v2_pnl — separate tables from V1's
t212_invest_orders / t212_pnl (see data/database.py:V2InvestOrder's
docstring for why). Shares the same T212 demo account and the same
Trading212Client as V1; mutual exclusion between the two systems is
enforced entirely by trading212_invest.enabled / trading212_stocks.enabled
being false, not by anything in this module.

"Never raises" contract, same as brokers/trading212.py: every DB write and
every T212 API call site is wrapped so a single ticker's failure never
blocks the rest of the run, and this module's own failure never blocks the
analysis pipeline it's called from.
"""
import logging
import math
import os
import time
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from analysis.backtest import _atr, _resolve_entry_stop
from analysis.regime import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _var_position_size_core
from analysis.v2 import crisis as v2_crisis
from analysis.v2 import signals as v2_signals
from analysis.v2 import universe as v2_universe
from brokers.trading212 import Trading212Client, _historical_fill
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)

_V2_ERROR_KEY = "v2_invest_error"


# ---------------------------------------------------------------------------
# Error-state helpers — deliberately separate from brokers/trading212.py's
# own _set_t212_error/_clear_t212_error/_send_t212_error_email, writing to a
# distinct system_metadata key, so a V2 error can never be confused with a
# V1 one on the Status page — same "genuinely separate, no shared rows"
# principle as the DB tables themselves.
# ---------------------------------------------------------------------------

def _set_v2_error(msg: str) -> None:
    try:
        from data.database import set_metadata
        set_metadata(_V2_ERROR_KEY, msg)
    except Exception:
        pass


def _clear_v2_error() -> None:
    try:
        from data.database import clear_metadata
        clear_metadata(_V2_ERROR_KEY)
    except Exception:
        pass


def _send_v2_error_email(error_msg: str, config: dict) -> None:
    try:
        from output.briefing import _smtp_send
        subject   = "V2 Invest: Order Placement Error"
        text_body = f"analysis/v2 order placement failed:\n\n{error_msg}"
        html_body = (
            '<p style="color:#dc2626;font-weight:bold">V2 order placement failed</p>'
            f'<pre style="background:#f1f5f9;padding:12px;border-radius:4px">{error_msg}</pre>'
        )
        _smtp_send(subject, text_body, html_body, config)
    except Exception as exc:
        logger.warning("Failed to send V2 error email: %s", exc)


def _log_v2_order(
    *,
    ticker: str,
    t212_ticker: str,
    direction: str,
    quantity: float,
    estimated_price: float,
    estimated_value_gbp: Optional[float],
    status: str,
    order_id: Optional[str],
    is_etf: bool,
    account_type: str,
    currency_code: Optional[str] = None,
    momentum_pct: Optional[float] = None,
    stop_price: Optional[float] = None,
    exit_reason: Optional[str] = None,
    var_pct: Optional[float] = None,
    error_message: Optional[str],
) -> None:
    """Insert one row into v2_invest_orders (best-effort; never raises)."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(sa_text("""
                INSERT INTO v2_invest_orders (
                    order_id, ticker, t212_ticker, direction,
                    quantity, estimated_price, estimated_value_gbp,
                    status, is_etf, account_type, currency_code,
                    momentum_pct, stop_price, exit_reason, var_pct,
                    error_message, placed_at, created_at
                ) VALUES (
                    :order_id, :ticker, :t212_ticker, :direction,
                    :quantity, :estimated_price, :estimated_value_gbp,
                    :status, :is_etf, :account_type, :currency_code,
                    :momentum_pct, :stop_price, :exit_reason, :var_pct,
                    :error_message, :now, :now
                )
            """), {
                "order_id":            order_id,
                "ticker":              ticker,
                "t212_ticker":         t212_ticker,
                "direction":           direction,
                "quantity":            quantity,
                "estimated_price":     estimated_price,
                "estimated_value_gbp": estimated_value_gbp,
                "status":              status,
                "is_etf":              is_etf,
                "account_type":        account_type,
                "currency_code":       currency_code,
                "momentum_pct":        momentum_pct,
                "stop_price":          stop_price,
                "exit_reason":         exit_reason,
                "var_pct":             var_pct,
                "error_message":       error_message,
                "now":                 datetime.utcnow().isoformat(),
            })
    except Exception as exc:
        logger.warning("Failed to log V2 order to DB: %s", exc)


def _reconstruct_v2_pnl(config: dict) -> None:
    """FIFO-pair FILLED BUY/SELL rows in v2_invest_orders into v2_pnl.

    Mirrors brokers/t212_learning.py:_reconstruct_t212_pnl() exactly, just
    against V2's own tables — see that function for the FIFO/idempotency
    rationale (this codebase never holds more than one open position per
    ticker at a time, so BUY/SELL pairs never interleave).
    """
    try:
        from collections import deque
        from sqlalchemy import text as sa_text
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from data.database import get_engine, V2Pnl

        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT id, ticker, t212_ticker, direction, fill_price, filled_quantity,
                       is_etf, momentum_pct, exit_reason, filled_at, currency_code
                FROM v2_invest_orders
                WHERE status = 'FILLED' AND fill_price IS NOT NULL
                ORDER BY ticker, filled_at ASC
            """)).mappings().all()

        by_ticker: dict[str, deque] = {}
        for r in rows:
            by_ticker.setdefault(r["ticker"], deque()).append(dict(r))

        for ticker, dq in by_ticker.items():
            pending_buy = None
            for row in dq:
                if row["direction"] == "BUY":
                    pending_buy = row
                elif row["direction"] == "SELL" and pending_buy is not None:
                    buy, sell = pending_buy, row
                    pending_buy = None

                    try:
                        entry_dt = datetime.fromisoformat(str(buy["filled_at"]))
                        exit_dt  = datetime.fromisoformat(str(sell["filled_at"]))
                    except Exception:
                        continue

                    qty = buy.get("filled_quantity") or 0
                    entry_price = buy.get("fill_price") or 0
                    exit_price  = sell.get("fill_price") or 0
                    if not qty or not entry_price:
                        continue

                    pnl_pct = (exit_price - entry_price) / entry_price
                    ccy = buy.get("currency_code") or "USD"
                    gbpusd, eurusd = Trading212Client._fx_rates()
                    native_pnl = (exit_price - entry_price) * qty
                    pnl_gbp = Trading212Client._to_gbp(native_pnl, ccy, gbpusd, eurusd) or 0.0

                    stmt = sqlite_insert(V2Pnl).values(
                        ticker=ticker,
                        t212_ticker=buy["t212_ticker"],
                        buy_order_id=buy["id"],
                        sell_order_id=sell["id"],
                        entry_date=entry_dt,
                        exit_date=exit_dt,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=qty,
                        currency=ccy,
                        pnl_gbp=pnl_gbp,
                        pnl_pct=pnl_pct,
                        holding_days=(exit_dt - entry_dt).days,
                        is_etf=buy.get("is_etf"),
                        momentum_pct_at_entry=buy.get("momentum_pct"),
                        exit_reason=sell.get("exit_reason"),
                    )
                    stmt = stmt.on_conflict_do_nothing(index_elements=["buy_order_id", "sell_order_id"])
                    with engine.begin() as conn:
                        conn.execute(stmt)
    except Exception as exc:
        logger.warning("V2: P&L reconstruction failed: %s", exc)


def _load_v2_open_order_tickers(days: int = 30) -> set:
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT DISTINCT ticker FROM v2_invest_orders
                WHERE status IN ('PLACED', 'FILLED')
                AND placed_at > datetime('now', :window)
            """), {"window": f"-{days} days"}).mappings().all()
        return {r["ticker"] for r in rows}
    except Exception:
        return set()


def _load_v2_open_position_context() -> dict:
    """Per t212_ticker: {entry_date, stop_price, momentum_pct} from the
    latest FILLED BUY row — used by the exit check for holding_days and the
    fixed-at-entry stop comparison."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT t212_ticker, ticker, filled_at, stop_price, momentum_pct
                FROM v2_invest_orders
                WHERE direction = 'BUY' AND status = 'FILLED'
                AND id IN (
                    SELECT MAX(id) FROM v2_invest_orders
                    WHERE direction = 'BUY' AND status = 'FILLED'
                    GROUP BY t212_ticker
                )
            """)).mappings().all()
        out = {}
        for r in rows:
            try:
                entry_dt = datetime.fromisoformat(str(r["filled_at"]))
            except Exception:
                continue
            out[r["t212_ticker"]] = {
                "ticker": r["ticker"], "entry_date": entry_dt,
                "stop_price": r["stop_price"], "momentum_pct": r["momentum_pct"],
            }
        return out
    except Exception as exc:
        logger.warning("V2: could not load open position context: %s", exc)
        return {}


def _has_recent_v2_sell_order(ticker: str, hours: int = 24) -> bool:
    """Same duplicate-SELL guard as brokers/trading212.py:_has_recent_sell_order()
    — an unresolved PLACED order blocks regardless of age; a resolved
    FILLED/FAILED one only needs a short cooldown. See that function's
    docstring for the full production rationale (T212's demo account has
    been observed leaving a SELL PLACED for 60+ hours before resolving)."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            row = conn.execute(sa_text("""
                SELECT 1 FROM v2_invest_orders
                WHERE ticker = :ticker AND direction = 'SELL'
                AND (
                    status = 'PLACED'
                    OR (status IN ('FILLED', 'FAILED') AND placed_at > datetime('now', :window))
                )
                LIMIT 1
            """), {"ticker": ticker, "window": f"-{hours} hours"}).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("_has_recent_v2_sell_order: check failed for %s: %s", ticker, exc)
        return False


def _v2_ticker_state(ticker: str, config: dict, gspc_close: pd.Series) -> Optional[dict]:
    """Compute today's momentum signal, ATR, membership, and crisis flags for
    one ticker — the live equivalent of one row of
    analysis/v2/backtest.py:simulate_trades()'s walk-forward loop, evaluated
    only for the latest available date.

    Returns None if there isn't enough price history to compute a signal yet.
    """
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    atr_period = config.get("signals", {}).get("atr_period", 14)
    atr_s = _atr(high, low, close, atr_period)
    sig_series = v2_signals.compute_signal_series(close)

    latest_dt = close.index[-1]
    is_etf = ticker in v2_universe.ETF_TICKERS
    is_member = is_etf or v2_universe.is_member(ticker, latest_dt.date())

    return {
        "ticker": ticker,
        "is_etf": is_etf,
        "price": float(close.iloc[-1]),
        "atr": float(atr_s.iloc[-1]) if pd.notna(atr_s.iloc[-1]) else 0.0,
        "sig_on": bool(sig_series.iloc[-1]),
        "momentum_pct": float(v2_signals.compute_momentum_series(close).iloc[-1])
                        if pd.notna(v2_signals.compute_momentum_series(close).iloc[-1]) else None,
        "is_member": is_member,
        "crisis_now": v2_crisis.is_crisis_day(gspc_close, latest_dt),
        "entry_blocked": v2_crisis.is_entry_blocked(gspc_close, latest_dt),
        "close_series": close,
    }


def _load_gspc_close() -> pd.Series:
    df = get_price_df("^GSPC", days=8000)
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    return df["close"].astype(float)


# ---------------------------------------------------------------------------
# Fill-status polling — same mechanism as brokers/trading212.py:poll_placed_orders(),
# targeting v2_invest_orders instead. Without this, a PLACED row would never
# resolve to FILLED, and _reconstruct_v2_pnl() (which only ever pairs FILLED
# rows) would never see a closed V2 position at all.
# ---------------------------------------------------------------------------

def poll_v2_placed_orders(config: dict) -> int:
    """Poll T212 for the terminal status of every PLACED v2_invest_orders row.

    Reuses brokers.trading212._historical_fill() (generic — takes a client
    and order_id, no V1-table dependency) as the primary 404-resolution path,
    exactly as poll_placed_orders() does, since T212 removes a filled market
    order from GET /equity/orders/{id} once it becomes a position — a 404
    does not mean cancelled. Falls back to a live position lookup, then to
    the instrument's current quote, in the same priority order as V1's
    poller. Returns the count of rows updated.
    """
    v2_cfg = config.get("trading212_v2", {})
    if not v2_cfg.get("enabled", False):
        return 0
    if not (os.environ.get("T212_API_KEY") and os.environ.get("T212_API_SECRET")):
        return 0

    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT id, order_id, ticker, t212_ticker, direction, quantity, currency_code
                FROM v2_invest_orders
                WHERE status = 'PLACED' AND order_id IS NOT NULL AND order_id != ''
                ORDER BY placed_at DESC
                LIMIT 50
            """)).mappings().all()
            placed = [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("poll_v2_placed_orders: DB read failed — %s", exc)
        return 0

    if not placed:
        return 0

    gbpusd, eurusd = Trading212Client._fx_rates()
    live = v2_cfg.get("live", False)
    try:
        client = Trading212Client(live=live)
    except Exception as exc:
        logger.warning("poll_v2_placed_orders: cannot connect — %s", exc)
        return 0

    _TERMINAL = {"FILLED": "FILLED", "CANCELLED": "CANCELLED", "REJECTED": "REJECTED"}
    updated = 0

    for row in placed:
        order_id, row_id = row["order_id"], row["id"]
        try:
            resp = client.get_order(order_id)
        except Exception as exc:
            http_status = getattr(getattr(exc, "response", None), "status_code", None)
            if http_status != 404:
                continue

            t212_ticker = row.get("t212_ticker") or ""
            direction = (row.get("direction") or "").upper()
            hist_resp = _historical_fill(client, order_id)
            position = None
            if hist_resp is None and t212_ticker:
                try:
                    position = client.get_position(t212_ticker)
                except Exception:
                    pass

            if hist_resp is not None:
                resp = hist_resp
            elif direction == "SELL":
                if position:
                    resp = {"status": "CANCELLED"}
                else:
                    fill_price_est = None
                    try:
                        instruments = client.get_instruments()
                        inst = next((i for i in (instruments or []) if i.get("ticker") == t212_ticker), None)
                        if inst:
                            _raw = inst.get("currentPrice") or inst.get("price")
                            if _raw:
                                fill_price_est = float(_raw)
                    except Exception:
                        pass
                    resp = {"status": "FILLED", "fillPrice": fill_price_est, "filledQuantity": row.get("quantity")}
            elif position:
                avg_price = None
                for _k in ("averagePrice", "avgPrice", "avg_price"):
                    _v = position.get(_k)
                    if _v is not None and float(_v) != 0:
                        avg_price = float(_v)
                        break
                if avg_price is None:
                    _cp, _ppl, _qty = position.get("currentPrice"), position.get("ppl"), position.get("quantity")
                    if _cp is not None and _ppl is not None and _qty:
                        try:
                            avg_price = float(_cp) - float(_ppl) / float(_qty)
                        except (TypeError, ZeroDivisionError, ValueError):
                            pass
                if avg_price is None and position.get("currentPrice") is not None:
                    avg_price = float(position["currentPrice"])
                resp = {"status": "FILLED", "fillPrice": avg_price, "filledQuantity": position.get("quantity")}
            else:
                resp = {"status": "CANCELLED"}

        t212_status = (resp.get("status") or "").upper()
        new_status = _TERMINAL.get(t212_status)
        if not new_status:
            continue

        fill_price = None
        for _k in ("fillPrice", "fill_price"):
            _v = resp.get(_k)
            if _v is not None:
                try:
                    fill_price = float(_v)
                except (TypeError, ValueError):
                    pass
                break

        filled_qty = None
        for _k in ("filledQuantity", "filled_quantity"):
            _v = resp.get(_k)
            if _v is not None:
                try:
                    filled_qty = float(_v)
                except (TypeError, ValueError):
                    pass
                break

        now_iso = datetime.utcnow().isoformat()
        time_mod = resp.get("timeModified") or resp.get("filledAt") or now_iso

        effective_qty = filled_qty or (float(row.get("quantity") or 0) or None)
        actual_value = None
        if new_status == "FILLED" and fill_price and effective_qty:
            ccy = row.get("currency_code") or "USD"
            actual_value = round(Trading212Client._to_gbp(fill_price * effective_qty, ccy, gbpusd, eurusd) or 0.0, 2)

        try:
            from sqlalchemy import text as sa_text
            from data.database import get_engine
            with get_engine().begin() as conn:
                conn.execute(sa_text("""
                    UPDATE v2_invest_orders
                    SET status              = :status,
                        fill_price          = COALESCE(:fill_price, fill_price),
                        filled_quantity     = COALESCE(:filled_qty, filled_quantity),
                        estimated_value_gbp = COALESCE(:actual_value, estimated_value_gbp),
                        filled_at           = CASE WHEN :status = 'FILLED' THEN :time_mod ELSE filled_at END,
                        cancelled_at        = CASE WHEN :status IN ('CANCELLED', 'REJECTED') THEN :now ELSE cancelled_at END
                    WHERE id = :row_id
                """), {
                    "status": new_status, "fill_price": fill_price, "filled_qty": filled_qty,
                    "actual_value": actual_value, "time_mod": time_mod, "now": now_iso, "row_id": row_id,
                })
            updated += 1
        except Exception as exc:
            logger.warning("poll_v2_placed_orders: DB update failed for order %s — %s", order_id, exc)

        time.sleep(0.2)

    logger.info("poll_v2_placed_orders: %d of %d order(s) updated.", updated, len(placed))
    return updated


# ---------------------------------------------------------------------------
# EXIT — unconditional, called every hour and again before the BUY phase
# ---------------------------------------------------------------------------

def maybe_exit_v2_positions(config: dict) -> None:
    """Close any open V2 position whose exit priority (see module docstring)
    calls for exit today. Runs unconditionally — no run_type gate, same as
    brokers/trading212.py:maybe_exit_t212_positions()."""
    v2_cfg = config.get("trading212_v2", {})
    if not v2_cfg.get("enabled", False):
        return

    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")
    if not api_key or not api_secret:
        return

    _reconstruct_v2_pnl(config)

    mapping: dict = v2_cfg.get("instrument_mapping", {})
    if not mapping:
        return

    reverse_map: dict[str, str] = {}
    ccy_map: dict[str, str] = {}
    for rt, raw in mapping.items():
        t212_id = raw.get("ticker") if isinstance(raw, dict) else raw
        reverse_map[t212_id] = rt
        ccy_map[t212_id] = raw.get("currency", "USD") if isinstance(raw, dict) else "USD"

    live = v2_cfg.get("live", False)
    try:
        client = Trading212Client(live=live)
        positions = client.get_positions()
        positions_by_t212 = {p.get("ticker", ""): p for p in (positions or [])}
    except Exception as exc:
        logger.error("V2: API error during exit check — %s", exc, exc_info=True)
        _set_v2_error(str(exc))
        _send_v2_error_email(str(exc), config)
        return

    if not positions_by_t212:
        return

    logger.info("V2: EXIT phase — checking open positions")

    try:
        gspc_close = _load_gspc_close()
        context = _load_v2_open_position_context()
    except Exception as exc:
        logger.error("V2: DB/price read error during exit check — %s", exc, exc_info=True)
        _set_v2_error(f"read failed: {exc}")
        return

    account_label = "LIVE" if live else "DEMO"

    for t212_ticker, position in positions_by_t212.items():
        ticker = reverse_map.get(t212_ticker)
        if not ticker:
            continue  # not a V2-managed instrument — leave it alone

        current_qty = float(position.get("quantity", 0) or 0)
        if current_qty <= 0:
            continue

        state = _v2_ticker_state(ticker, config, gspc_close)
        if state is None:
            continue

        ctx = context.get(t212_ticker, {})
        entry_date = ctx.get("entry_date")
        stop_price = ctx.get("stop_price")
        max_hold = get_max_holding_days(ticker, config)
        holding_days = (datetime.utcnow() - entry_date).days if entry_date else 0

        # Exit priority — must match analysis/v2/backtest.py:simulate_trades()
        # exactly: crisis > index-removal > stop-loss > signal-off > max-holding.
        exit_reason = None
        if state["crisis_now"]:
            exit_reason = "CRISIS_EXIT"
        elif not state["is_etf"] and not state["is_member"]:
            exit_reason = "INDEX_REMOVAL"
        elif stop_price is not None and state["price"] <= stop_price:
            exit_reason = "STOP_LOSS"
        elif not state["sig_on"]:
            exit_reason = "SIGNAL_OFF"
        elif holding_days >= max_hold:
            exit_reason = "MAX_HOLDING"

        if exit_reason is None:
            continue

        if _has_recent_v2_sell_order(ticker):
            logger.info("V2: %s already has a recent SELL order — skipping duplicate.", ticker)
            continue

        close_qty = abs(current_qty)
        logger.info("V2: exiting %s (%s) — %s, qty=%.4f", ticker, t212_ticker, exit_reason, close_qty)

        status, order_id, error_msg = "FAILED", None, None
        try:
            response = client.place_market_order(t212_ticker, -close_qty)
            order_id = str(response.get("id", ""))
            status = "PLACED"
            logger.info("V2: SELL %s qty=%.4f id=%s", t212_ticker, close_qty, order_id)
        except Exception as exc:
            error_msg = str(exc)
            logger.error("V2: sell order failed for %s: %s", ticker, exc)
            _set_v2_error(f"Sell order failed for {ticker}: {error_msg}")
            _send_v2_error_email(error_msg, config)

        _log_v2_order(
            ticker=ticker, t212_ticker=t212_ticker, direction="SELL",
            quantity=close_qty, estimated_price=state["price"], estimated_value_gbp=None,
            status=status, order_id=order_id, is_etf=state["is_etf"],
            account_type=account_label, currency_code=ccy_map.get(t212_ticker),
            exit_reason=exit_reason, error_message=error_msg,
        )

        if status == "FAILED":
            logger.warning("V2: halting exit phase after sell-order failure.")
            return

        time.sleep(0.5)


# ---------------------------------------------------------------------------
# BUY — only on run_type == "full"
# ---------------------------------------------------------------------------

def maybe_place_v2_orders(config: dict, run_type: str = "full") -> None:
    """Exit stale V2 positions, then place new BUY orders for tickers whose
    momentum signal is on, that are currently index-eligible, and that
    aren't inside the crisis entry-block window.

    Mirrors brokers/trading212.py:maybe_place_t212_orders()'s two-phase
    EXIT/BUY structure and safety invariants exactly.
    """
    v2_cfg = config.get("trading212_v2", {})
    if not v2_cfg.get("enabled", False):
        return

    if run_type != "full":
        maybe_exit_v2_positions(config)
        logger.debug("V2: skipping BUY-phase order placement on %s run (full only).", run_type)
        return

    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")
    if not api_key or not api_secret:
        logger.warning("V2: T212_API_KEY or T212_API_SECRET not set — skipping.")
        _set_v2_error("API credentials not configured (T212_API_KEY / T212_API_SECRET)")
        return

    mapping: dict = v2_cfg.get("instrument_mapping", {})
    if not mapping:
        logger.info("V2: no instrument_mapping in config — nothing to do.")
        return

    live = v2_cfg.get("live", False)
    max_deployed_pct = float(v2_cfg.get("max_total_deployed", 0.5))

    # VaR sizing config reused verbatim from trading212_invest — see this
    # module's docstring and config.yaml's trading212_v2 comment for why.
    var_cfg = config.get("trading212_invest", {})
    max_position_gbp = float(var_cfg.get("max_position_gbp", 500))

    try:
        client = Trading212Client(live=live)
        summary = client.get_account_summary()
        _s_cash = summary.get("cash", {}) if isinstance(summary.get("cash"), dict) else {}
        _s_inv  = summary.get("investments", {}) if isinstance(summary.get("investments"), dict) else {}
        total_val = float(summary.get("totalValue", 0) or 0)
        invested  = float(_s_inv.get("currentValue", 0) or 0)
        free_cash = float(_s_cash.get("availableToTrade", 0) or 0)

        positions = client.get_positions()
        positions_by_t212 = {p.get("ticker", ""): p for p in (positions or [])}

        instruments = client.get_instruments()
        instrument_meta = {i["ticker"]: i for i in (instruments or []) if "ticker" in i}
    except Exception as exc:
        logger.error("V2: API error during setup — %s", exc, exc_info=True)
        _set_v2_error(str(exc))
        _send_v2_error_email(str(exc), config)
        return

    # Fetched account state BEFORE the delegated exit call, matching V1's
    # ordering rationale exactly — see maybe_place_t212_orders()'s comment.
    maybe_exit_v2_positions(config)

    try:
        gspc_close = _load_gspc_close()
        open_order_tickers = _load_v2_open_order_tickers()
    except Exception as exc:
        logger.error("V2: read error — %s", exc, exc_info=True)
        _set_v2_error(f"read failed: {exc}")
        return

    account_label = "LIVE" if live else "DEMO"

    if total_val > 0 and (invested / total_val) >= max_deployed_pct:
        logger.info("V2: %.0f%% deployed (limit %.0f%%) — no new orders.",
                    (invested / total_val) * 100, max_deployed_pct * 100)
        return

    max_open = v2_cfg.get("max_open_positions", 999)
    current_open = len(positions_by_t212)
    if current_open >= max_open:
        logger.info("V2: %d/%d positions open — max_open_positions reached, skipping new entries.",
                    current_open, max_open)
        return

    orders_placed = 0

    for ticker, raw in mapping.items():
        t212_ticker = raw.get("ticker") if isinstance(raw, dict) else raw
        instrument_currency = raw.get("currency", "USD") if isinstance(raw, dict) else "USD"

        state = _v2_ticker_state(ticker, config, gspc_close)
        if state is None:
            continue

        # Entry gate — must match simulate_trades()'s open_trade-is-None
        # branch exactly: sig_on AND is_member AND not blocked_now.
        if not state["sig_on"] or not state["is_member"] or state["entry_blocked"]:
            continue

        if t212_ticker in positions_by_t212:
            logger.debug("V2: %s already held — skipping.", ticker)
            continue
        if ticker in open_order_tickers:
            logger.debug("V2: %s has a recent order in DB — skipping.", ticker)
            continue

        max_hold = get_max_holding_days(ticker, config)
        entry_price = state["price"]
        atr = state["atr"]
        stop_price, _stop_pct = _resolve_entry_stop("LONG", entry_price, atr, config)

        # VaR position sizing — same formula/inputs the validated backtest
        # itself uses (analysis/volatility.py:_var_position_size_core(),
        # via analysis/v2/backtest.py:simulate_trades()), except sourced
        # from live account value and live prior-close history instead of a
        # notional backtest constant — the same live/backtest divergence
        # V1's own drawdown-scaling section documents as expected.
        close_series = state["close_series"]
        var_confidence = float(var_cfg.get("var_confidence", 0.95))
        var_lookback = int(var_cfg.get("var_lookback_days", 252))
        max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
        min_position_gbp = float(var_cfg.get("min_position_gbp", 50))

        prior_closes = close_series.tail(var_lookback + 1)
        log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
        if len(log_returns) >= _MIN_VAR_RETURNS:
            position_gbp, _dv, holding_var = _var_position_size_core(
                log_returns, total_val, max_position_gbp, max_hold,
                var_confidence, max_risk_pct, min_position_gbp,
            )
        else:
            position_gbp = round(max_position_gbp * 0.5, 2)
            holding_var = None
        position_gbp = min(position_gbp, free_cash * 0.25)

        if position_gbp < 10:
            logger.warning("V2: insufficient free cash (£%.2f) — stopping new orders.", free_cash)
            break

        quantity = client.get_quantity_for_gbp(
            t212_ticker, position_gbp, research_ticker=ticker,
            floor_to_whole_share=not state["is_etf"],
        )
        if quantity <= 0:
            logger.warning("V2: could not calculate quantity for %s (%s) — skipping.", ticker, t212_ticker)
            continue

        meta = instrument_meta.get(t212_ticker, {})
        min_qty = float(meta.get("minTradeQuantity", 1.0))
        precision = int(meta.get("maxQuantityPrecision", 0) or 0)

        if state["is_etf"]:
            quantity = math.floor(quantity)  # ETFs: whole shares only, same as V1's ucits_mapping path
        elif precision > 0:
            quantity = round(quantity, precision)  # fractional-capable stock
        else:
            quantity = math.floor(quantity)

        if quantity < min_qty or quantity <= 0:
            logger.warning("V2: %s (%s) qty %.4f below instrument minimum %.4f — skipping.",
                            ticker, t212_ticker, quantity, min_qty)
            continue

        price_gbp = client.get_instrument_price_gbp(t212_ticker, research_ticker=ticker)
        est_value_gbp = quantity * price_gbp if price_gbp else position_gbp

        status, order_id, error_msg = "FAILED", None, None
        try:
            response = client.place_market_order(t212_ticker, quantity)
            order_id = str(response.get("id", ""))
            status = "PLACED"
            free_cash -= position_gbp
            orders_placed += 1
            logger.info("V2: BUY %s (%s) qty=%s approx £%.0f id=%s",
                        ticker, t212_ticker, quantity, position_gbp, order_id)
        except Exception as order_exc:
            error_msg = str(order_exc)
            logger.error("V2: buy order failed for %s: %s", ticker, order_exc)
            _set_v2_error(f"Buy order failed for {ticker}: {error_msg}")
            _send_v2_error_email(error_msg, config)

        _api_ccy = meta.get("currencyCode")
        _log_v2_order(
            ticker=ticker, t212_ticker=t212_ticker, direction="BUY",
            quantity=quantity, estimated_price=entry_price, estimated_value_gbp=est_value_gbp,
            status=status, order_id=order_id, is_etf=state["is_etf"],
            account_type=account_label, currency_code=_api_ccy or instrument_currency,
            momentum_pct=state["momentum_pct"], stop_price=stop_price,
            var_pct=holding_var, error_message=error_msg,
        )

        if status == "FAILED":
            logger.warning("V2: halting placement after buy-order failure.")
            break

        time.sleep(0.5)

    if orders_placed > 0:
        _clear_v2_error()

    logger.info("V2: pipeline complete — %d order(s) placed, free cash approx £%.0f.",
                orders_placed, free_cash)
