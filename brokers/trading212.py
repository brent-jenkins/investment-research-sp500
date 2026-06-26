"""Trading212 Invest account API client and automated paper-order placement.

Two separate concerns live here:

1.  Trading212Client — thin HTTP wrapper around the T212 Invest REST API.
    Authentication: Basic Auth (base64 key:secret).
    All requests are retried up to 3 times with exponential back-off;
    HTTP 429 (rate-limited) is treated as a retryable condition.

2.  maybe_place_t212_orders(config, run_type) — called from the analysis
    pipeline after signals are generated.  Handles two phases:

    EXIT: closes any open T212 position whose underlying signal has moved to
          SELL or STRONG_SELL.
    BUY:  places new market orders for STRONG_BUY signals whose underlying
          ticker is listed in trading212_invest.ucits_mapping in config.yaml.

    Has no effect unless:
      - trading212_invest.enabled: true in config.yaml
      - run_type == "full"  (requires fresh price data from the EOD pull)
      - T212_API_KEY and T212_API_SECRET environment variables are set

Safety invariants:
  - live: false is the hard default.  Live trading is explicitly blocked:
    place_market_order() raises RuntimeError if self.live is True.
  - Only tickers listed in ucits_mapping are eligible for new orders.
  - Every order attempt (success or failure) is written to t212_invest_orders.
  - Duplicate positions are prevented by checking the DB and the live T212
    portfolio before placing.
  - Any API exception disables order placement for the current run, writes the
    error to system_metadata under "t212_invest_error", and sends an alert email.
"""

import base64
import logging
import math
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instruments cache (module-level, 24-hour TTL)
# ---------------------------------------------------------------------------

_instruments_cache: dict[str, tuple[datetime, list]] = {}
_INSTRUMENTS_TTL = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Trading212Client
# ---------------------------------------------------------------------------

class Trading212Client:
    def __init__(self, live: bool = False):
        key    = os.environ["T212_API_KEY"]
        secret = os.environ["T212_API_SECRET"]
        credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self.headers  = {"Authorization": f"Basic {credentials}"}
        self.base_url = (
            "https://live.trading212.com/api/v0"
            if live else
            "https://demo.trading212.com/api/v0"
        )
        self.live = live

    # ── low-level request with retry / back-off ─────────────────────────────

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Authenticated request with up to 3 retries and exponential back-off.

        HTTP 429 (rate limit) is handled as a retryable condition with a
        longer wait.  Other 4xx errors are raised immediately (no retry).
        """
        url     = f"{self.base_url}{path}"
        timeout = kwargs.pop("timeout", 10)
        last_exc: Exception | None = None

        for attempt in range(3):
            try:
                r = requests.request(
                    method, url,
                    headers=self.headers,
                    timeout=timeout,
                    **kwargs,
                )
                if r.status_code == 429:
                    wait = 2 ** (attempt + 2)   # 4 s, 8 s, 16 s
                    logger.warning(
                        "T212 rate limited — waiting %ds (attempt %d/3)", wait, attempt + 1,
                    )
                    time.sleep(wait)
                    continue
                if r.status_code >= 400:
                    try:
                        body = r.json()
                    except Exception:
                        body = r.text or "(no body)"
                    raise requests.exceptions.HTTPError(
                        f"{r.status_code} {r.reason} for url: {r.url} — T212: {body}",
                        response=r,
                    )
                return r
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt < 2:
                    wait = 2 ** attempt   # 1 s, 2 s
                    logger.warning(
                        "T212 request failed (%s) — retrying in %ds", exc, wait,
                    )
                    time.sleep(wait)

        raise last_exc or RuntimeError("T212 request failed after 3 attempts")

    # ── public API methods ───────────────────────────────────────────────────

    def get_account_summary(self) -> dict:
        r = self._request("GET", "/equity/account/summary")
        return r.json()

    def get_positions(self) -> list:
        r = self._request("GET", "/equity/portfolio")
        return r.json()

    def get_position(self, ticker: str) -> Optional[dict]:
        """Return the open position dict for the given T212 ticker, or None."""
        for pos in (self.get_positions() or []):
            if pos.get("ticker") == ticker:
                return pos
        return None

    def get_instruments(self) -> list:
        """Return the full instruments list, refreshed at most once per 24 hours."""
        global _instruments_cache
        cached = _instruments_cache.get(self.base_url)
        if cached:
            fetched_at, instruments = cached
            if datetime.utcnow() - fetched_at < _INSTRUMENTS_TTL:
                return instruments
        r = self._request("GET", "/equity/metadata/instruments")
        instruments = r.json()
        _instruments_cache[self.base_url] = (datetime.utcnow(), instruments)
        return instruments

    def place_market_order(self, ticker: str, quantity: float) -> dict:
        """Place a market order.  Positive quantity = buy, negative = sell.

        Raises RuntimeError if self.live is True — live trading is gated
        behind an explicit opt-in that must be set in the constructor.
        Raises ValueError if quantity is zero.
        """
        if self.live:
            raise RuntimeError(
                "Live trading not yet enabled — instantiate Trading212Client "
                "with live=True only after thorough demo validation."
            )
        if quantity == 0:
            raise ValueError("quantity must be non-zero")
        payload = {"ticker": ticker, "quantity": quantity}
        r = self._request("POST", "/equity/orders/market", json=payload)
        return r.json()

    def cancel_order(self, order_id: str) -> dict:
        r = self._request("DELETE", f"/equity/orders/{order_id}")
        return r.json()

    def get_order(self, order_id: str) -> dict:
        """Fetch a single order by ID from the T212 API."""
        r = self._request("GET", f"/equity/orders/{order_id}")
        return r.json()

    def get_quantity_for_gbp(self, ticker: str, gbp_amount: float) -> float:
        """Calculate share quantity for a given GBP order size.

        Looks up the latest close price from our local price_data DB for the
        underlying US ticker, then converts GBP to USD using the stored GBPUSD
        rate.  Returns 0.0 if the price is unavailable.

        Most T212 UCITS instruments are USD-denominated and price-track their
        US counterpart closely enough for order-sizing purposes.
        """
        try:
            from sqlalchemy import text as sa_text
            from data.database import get_engine
            eng = get_engine()
            with eng.connect() as conn:
                row = conn.execute(sa_text("""
                    SELECT
                        (SELECT close FROM price_data
                         WHERE ticker = :ticker ORDER BY date DESC LIMIT 1) AS price,
                        (SELECT close FROM price_data
                         WHERE ticker = 'GBPUSD=X' ORDER BY date DESC LIMIT 1) AS gbpusd
                """), {"ticker": ticker}).fetchone()

            if not row or row[0] is None:
                logger.warning("get_quantity_for_gbp: no price for %s", ticker)
                return 0.0

            price_usd = float(row[0])
            gbpusd    = float(row[1]) if row[1] else 1.27
            quantity  = round((gbp_amount * gbpusd) / price_usd, 4)
            return max(0.0, quantity)

        except Exception as exc:
            logger.warning("get_quantity_for_gbp failed for %s: %s", ticker, exc)
            return 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _set_t212_error(msg: str) -> None:
    """Write an error message to system_metadata so the Status page can show it."""
    try:
        from data.database import set_metadata
        set_metadata("t212_invest_error", msg)
    except Exception:
        pass


def _clear_t212_error() -> None:
    try:
        from data.database import clear_metadata
        clear_metadata("t212_invest_error")
    except Exception:
        pass


def _send_t212_error_email(error_msg: str, config: dict) -> None:
    """Send an alert email when T212 order placement fails."""
    try:
        from output.briefing import _smtp_send
        subject   = "T212 Invest: Order Placement Error"
        text_body = f"T212 Invest order placement failed:\n\n{error_msg}"
        html_body = (
            '<p style="color:#dc2626;font-weight:bold">T212 Invest order placement failed</p>'
            f'<pre style="background:#f1f5f9;padding:12px;border-radius:4px">{error_msg}</pre>'
        )
        _smtp_send(subject, text_body, html_body, config)
    except Exception as exc:
        logger.warning("Failed to send T212 error email: %s", exc)


def _ucits_entry(raw) -> "tuple[str, str]":
    """Extract (ucits_ticker, currency) from a ucits_mapping value.

    Handles both the legacy flat-string format ("I50Dl_EQ") and the new
    nested-dict format ({"ticker": "I50Dl_EQ", "currency": "USD"}).
    Defaults currency to "USD" for backward-compat with old configs.
    """
    if isinstance(raw, dict):
        return raw.get("ticker", ""), raw.get("currency", "USD")
    return str(raw), "USD"


def _log_t212_order(
    *,
    ticker: str,
    t212_ticker: str,
    direction: str,
    quantity: float,
    estimated_price: float,
    estimated_value_gbp: Optional[float],
    status: str,
    order_id: Optional[str],
    signal_type: str,
    confluence_score: Optional[float],
    regime: Optional[str],
    account_type: str,
    currency_code: Optional[str] = None,
    error_message: Optional[str],
) -> None:
    """Insert one row into t212_invest_orders (best-effort; never raises)."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(sa_text("""
                INSERT INTO t212_invest_orders (
                    order_id, ticker, t212_ticker, direction,
                    quantity, estimated_price, estimated_value_gbp,
                    status, signal_type, confluence_score, regime,
                    account_type, currency_code, error_message, placed_at, created_at
                ) VALUES (
                    :order_id, :ticker, :t212_ticker, :direction,
                    :quantity, :estimated_price, :estimated_value_gbp,
                    :status, :signal_type, :confluence_score, :regime,
                    :account_type, :currency_code, :error_message, :now, :now
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
                "signal_type":         signal_type,
                "confluence_score":    confluence_score,
                "regime":              regime,
                "account_type":        account_type,
                "currency_code":       currency_code,
                "error_message":       error_message,
                "now":                 datetime.utcnow().isoformat(),
            })
    except Exception as exc:
        logger.warning("Failed to log T212 order to DB: %s", exc)


# ---------------------------------------------------------------------------
# Fill-status polling — called hourly and on Refresh Account
# ---------------------------------------------------------------------------

def poll_placed_orders(config: dict) -> int:
    """Poll T212 API to update status of PLACED orders.

    Reads all rows with status='PLACED' from t212_invest_orders, queries
    GET /equity/orders/{id} for each, and writes the terminal status
    (FILLED / CANCELLED / REJECTED) plus fill price back to the DB.

    Called on every Refresh Account click and from the hourly trade alert job.
    Returns the count of rows updated.
    """
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return 0

    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")
    if not api_key or not api_secret:
        return 0

    # Read PLACED orders that have a real T212 order ID
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT id, order_id, ticker, t212_ticker, direction, quantity
                FROM t212_invest_orders
                WHERE status = 'PLACED'
                  AND order_id IS NOT NULL
                  AND order_id != ''
                ORDER BY placed_at DESC
                LIMIT 50
            """)).mappings().all()
        placed = [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("poll_placed_orders: DB read failed — %s", exc)
        return 0

    if not placed:
        return 0

    live = t212_cfg.get("live", False)
    try:
        client = Trading212Client(live=live)
    except Exception as exc:
        logger.warning("poll_placed_orders: cannot connect — %s", exc)
        return 0

    # T212 terminal status values → our internal status
    _TERMINAL: dict[str, str] = {
        "FILLED":    "FILLED",
        "CANCELLED": "CANCELLED",
        "REJECTED":  "REJECTED",
    }

    updated = 0

    for row in placed:
        order_id = row["order_id"]
        row_id   = row["id"]

        try:
            resp = client.get_order(order_id)
        except Exception as exc:
            http_status = getattr(getattr(exc, "response", None), "status_code", None)
            if http_status == 404:
                # T212 removes filled market orders from the orders endpoint once
                # they become positions — 404 does NOT necessarily mean cancelled.
                # Check for an active position before deciding the terminal status.
                t212_ticker = row.get("t212_ticker") or ""
                position = None
                if t212_ticker:
                    try:
                        position = client.get_position(t212_ticker)
                    except Exception:
                        pass
                if position:
                    logger.debug(
                        "poll_placed_orders: position dict for %s: %s", t212_ticker, position,
                    )
                    # Primary: T212 averagePrice (cost basis per unit)
                    avg_price: Optional[float] = None
                    for _ap_key in ("averagePrice", "avgPrice", "avg_price"):
                        _v = position.get(_ap_key)
                        if _v is not None and float(_v) != 0:
                            avg_price = float(_v)
                            break
                    # Fallback: derive from currentPrice − ppl/quantity
                    if avg_price is None:
                        _cp  = position.get("currentPrice")
                        _ppl = position.get("ppl")
                        _qty = position.get("quantity")
                        if _cp is not None and _ppl is not None and _qty:
                            try:
                                avg_price = float(_cp) - float(_ppl) / float(_qty)
                            except (TypeError, ZeroDivisionError, ValueError):
                                pass
                    # Last resort: current price
                    if avg_price is None:
                        _cp = position.get("currentPrice")
                        if _cp is not None:
                            avg_price = float(_cp)
                    logger.info(
                        "poll_placed_orders: order %s not found (404) but position exists"
                        " for %s — marking FILLED at %s.",
                        order_id, t212_ticker,
                        f"{avg_price:.4f}" if avg_price is not None else "unknown",
                    )
                    resp = {
                        "status":         "FILLED",
                        "fillPrice":      avg_price,
                        "filledQuantity": position.get("quantity"),
                    }
                else:
                    logger.warning(
                        "poll_placed_orders: order %s not found (404) and no position"
                        " for %s — marking CANCELLED.",
                        order_id, t212_ticker or row.get("ticker", "?"),
                    )
                    resp = {"status": "CANCELLED"}
            else:
                logger.debug(
                    "poll_placed_orders: could not fetch order %s — %s", order_id, exc,
                )
                continue

        t212_status = (resp.get("status") or "").upper()
        new_status  = _TERMINAL.get(t212_status)
        if not new_status:
            continue   # still pending — nothing to update yet

        # Extract fill price — T212 may use different field names across API versions
        fill_price: Optional[float] = None
        for _fp_key in ("fillPrice", "fill_price"):
            _v = resp.get(_fp_key)
            if _v is not None:
                try:
                    fill_price = float(_v)
                except (TypeError, ValueError):
                    pass
                break
        if fill_price is None:
            for _nested in ("executionDetails", "fillResult"):
                _obj = resp.get(_nested)
                if isinstance(_obj, dict) and "price" in _obj:
                    try:
                        fill_price = float(_obj["price"])
                    except (TypeError, ValueError):
                        pass
                    break

        # Extract filled quantity (may differ from ordered quantity on partial fills)
        filled_qty: Optional[float] = None
        for _fq_key in ("filledQuantity", "filled_quantity", "executedQuantity"):
            _v = resp.get(_fq_key)
            if _v is not None:
                try:
                    filled_qty = float(_v)
                except (TypeError, ValueError):
                    pass
                break

        now_iso  = datetime.utcnow().isoformat()
        time_mod = resp.get("timeModified") or resp.get("filledAt") or now_iso

        # Compute actual position value (fill price × qty) so estimated_value_gbp
        # reflects the real cost basis rather than the intended position size.
        # Use filled_qty first; fall back to the ordered quantity stored in the DB.
        effective_qty = filled_qty or (float(row.get("quantity") or 0) or None)
        actual_value: Optional[float] = None
        if new_status == "FILLED" and fill_price and effective_qty:
            actual_value = round(fill_price * effective_qty, 2)

        try:
            from sqlalchemy import text as sa_text
            from data.database import get_engine
            with get_engine().begin() as conn:
                conn.execute(sa_text("""
                    UPDATE t212_invest_orders
                    SET status              = :status,
                        fill_price          = COALESCE(:fill_price, fill_price),
                        filled_quantity     = COALESCE(:filled_qty, filled_quantity),
                        estimated_value_gbp = COALESCE(:actual_value, estimated_value_gbp),
                        filled_at           = CASE WHEN :status = 'FILLED'
                                              THEN :time_mod ELSE filled_at END,
                        cancelled_at        = CASE WHEN :status IN ('CANCELLED', 'REJECTED')
                                              THEN :now ELSE cancelled_at END
                    WHERE id = :row_id
                """), {
                    "status":       new_status,
                    "fill_price":   fill_price,
                    "filled_qty":   filled_qty,
                    "actual_value": actual_value,
                    "time_mod":     time_mod,
                    "now":          now_iso,
                    "row_id":       row_id,
                })
            updated += 1
            logger.info(
                "poll_placed_orders: order %s (%s %s) → %s  fill=%.4f  qty=%s",
                order_id, row.get("direction", "?"), row.get("ticker", "?"),
                new_status, fill_price or 0.0,
                f"{filled_qty:.4f}" if filled_qty is not None else "?",
            )
        except Exception as exc:
            logger.warning(
                "poll_placed_orders: DB update failed for order %s — %s", order_id, exc,
            )

        time.sleep(0.2)

    logger.info("poll_placed_orders: %d of %d order(s) updated.", updated, len(placed))
    return updated


# ---------------------------------------------------------------------------
# Automated order placement — called from EOD pipeline
# ---------------------------------------------------------------------------

def maybe_place_t212_orders(config: dict, run_type: str = "full") -> None:
    """Place T212 Invest DEMO market orders based on current signals.

    Phase 1 — EXIT: closes any open T212 position whose underlying research
    signal has moved to SELL or STRONG_SELL.

    Phase 2 — BUY: places new market orders for STRONG_BUY signals on tickers
    listed in trading212_invest.ucits_mapping in config.yaml.

    Entry conditions (all must be true):
      1. trading212_invest.enabled: true in config.yaml
      2. run_type == "full"  — only on EOD runs with fresh price data
      3. T212_API_KEY and T212_API_SECRET environment variables are set
      4. ucits_mapping section present in trading212_invest config

    Per-BUY-order conditions:
      - Ticker has a ucits_mapping entry
      - No existing T212 position for this UCITS instrument
      - No recent order in t212_invest_orders (last 30 days, PLACED or FILLED)
      - Free cash covers at least £10
      - Total deployed remains below max_total_deployed x account value

    All outcomes (success and failure) are written to t212_invest_orders.
    API errors set "t212_invest_error" in system_metadata, stop placement for
    the current run, and trigger an alert email.
    """
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return

    if run_type != "full":
        logger.debug("T212 Invest: skipping order placement on %s run (full only).", run_type)
        return

    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")
    if not api_key or not api_secret:
        logger.warning("T212 Invest: T212_API_KEY or T212_API_SECRET not set — skipping.")
        _set_t212_error("API credentials not configured (T212_API_KEY / T212_API_SECRET)")
        return

    ucits_mapping: dict = t212_cfg.get("ucits_mapping", {})
    if not ucits_mapping:
        logger.info("T212 Invest: no ucits_mapping in config — nothing to do.")
        return

    # Build reverse map (ucits_ticker → research_ticker) and currency lookup,
    # handling both the legacy flat-string format and the new nested-dict format.
    reverse_ucits: dict[str, str] = {}
    ucits_currencies: dict[str, str] = {}  # ucits_ticker → instrument currency
    for _rt, _raw in ucits_mapping.items():
        _ut, _ccy = _ucits_entry(_raw)
        reverse_ucits[_ut] = _rt
        ucits_currencies[_ut] = _ccy

    live             = t212_cfg.get("live", False)
    long_only        = bool(t212_cfg.get("long_only", True))  # T212 Invest is cash-only
    max_position_gbp = float(t212_cfg.get("max_position_gbp", 500))
    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.5))

    _entry_cfg        = t212_cfg.get("entry_signal", "STRONG_BUY")
    _exit_cfg         = t212_cfg.get("exit_signal",  "SELL")
    entry_signals     = ("BUY", "STRONG_BUY") if _entry_cfg == "BUY" else ("STRONG_BUY",)
    immediate_exits   = ("SELL", "STRONG_SELL") if _exit_cfg == "SELL" else ("STRONG_SELL",)
    exit_on_signal    = bool(t212_cfg.get("exit_on_signal", True))
    time_limit_backstop = bool(t212_cfg.get("time_limit_as_backstop", True))
    max_holding_days  = int(t212_cfg.get("max_holding_days", 90))
    confirmation_runs = int(config.get("signals", {}).get("reversal_confirmation_runs", 2))
    confirmation_hours = confirmation_runs * 3

    # ── Connect and fetch account state ─────────────────────────────────────
    try:
        client = Trading212Client(live=live)

        summary   = client.get_account_summary()
        _s_cash   = summary.get("cash", {}) if isinstance(summary.get("cash"), dict) else {}
        _s_inv    = summary.get("investments", {}) if isinstance(summary.get("investments"), dict) else {}
        total_val = float(summary.get("totalValue", 0) or 0)
        invested  = float(_s_inv.get("currentValue", 0) or 0)
        free_cash = float(_s_cash.get("availableToTrade", 0) or 0)

        positions = client.get_positions()
        positions_by_ucits: dict[str, dict] = {
            p.get("ticker", ""): p for p in (positions or [])
        }

        instruments = client.get_instruments()
        instrument_meta: dict[str, dict] = {
            inst["ticker"]: inst
            for inst in (instruments or [])
            if "ticker" in inst
        }

    except Exception as exc:
        logger.error("T212 Invest: API error during setup — %s", exc, exc_info=True)
        _set_t212_error(str(exc))
        _send_t212_error_email(str(exc), config)
        return

    # ── Read all current signals and latest prices from DB ───────────────────
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            sig_rows = conn.execute(sa_text("""
                SELECT s.ticker, s.signal, s.confluence_score,
                       COALESCE(s.regime, s.notes) AS regime
                FROM signals s
                INNER JOIN (
                    SELECT ticker, MAX(date) AS md FROM signals GROUP BY ticker
                ) latest ON s.ticker = latest.ticker AND s.date = latest.md
            """)).mappings().all()

            price_rows = conn.execute(sa_text("""
                SELECT p.ticker, p.close
                FROM price_data p
                INNER JOIN (
                    SELECT ticker, MAX(date) AS md FROM price_data GROUP BY ticker
                ) latest ON p.ticker = latest.ticker AND p.date = latest.md
            """)).mappings().all()

            try:
                order_rows = conn.execute(sa_text("""
                    SELECT DISTINCT ticker FROM t212_invest_orders
                    WHERE status IN ('PLACED', 'FILLED')
                    AND placed_at > datetime('now', '-30 days')
                """)).mappings().all()
                open_order_tickers: set[str] = {r["ticker"] for r in order_rows}
            except Exception:
                open_order_tickers = set()

            try:
                fill_rows = conn.execute(sa_text("""
                    SELECT t212_ticker, MIN(filled_at) AS first_fill
                    FROM t212_invest_orders
                    WHERE direction = 'BUY' AND status = 'FILLED'
                    GROUP BY t212_ticker
                """)).mappings().all()
                first_fill_dates: dict[str, datetime] = {}
                for _fr in fill_rows:
                    if _fr["first_fill"]:
                        try:
                            first_fill_dates[_fr["t212_ticker"]] = datetime.fromisoformat(
                                str(_fr["first_fill"])
                            )
                        except Exception:
                            pass
            except Exception:
                first_fill_dates = {}

    except Exception as exc:
        logger.error("T212 Invest: DB read error — %s", exc, exc_info=True)
        _set_t212_error(f"DB read failed: {exc}")
        return

    signals: dict[str, dict] = {r["ticker"]: dict(r) for r in sig_rows}
    prices:  dict[str, float] = {
        r["ticker"]: float(r["close"])
        for r in price_rows if r["close"] is not None
    }
    gbpusd        = prices.get("GBPUSD=X", 1.27)
    account_label = "LIVE" if live else "DEMO"

    # ────────────────────────────────────────────────────────────────────────
    # Phase 1 — EXIT (close existing long positions only)
    #
    # T212 Invest is a cash account — no shorting.  This loop iterates ONLY
    # over positions we currently hold (positions_by_ucits), so every sell
    # order is a position close, never a naked short.
    #
    # Trigger priority:
    #   1. Immediate: SELL or STRONG_SELL signal
    #   2. Confirmed: signal drops to NEUTRAL for confirmation_runs × 3 h
    #      (tracked per position in system_metadata as t212_neutral_since:<ucits>)
    #   3. Backstop: position held > max_holding_days (only when
    #      time_limit_as_backstop: true; overrides confirmed-only logic)
    # ────────────────────────────────────────────────────────────────────────
    from data.database import (  # noqa: PLC0415
        set_metadata as _sm_set,
        get_metadata_str as _sm_get,
        clear_metadata as _sm_clear,
    )

    for ucits_ticker, position in positions_by_ucits.items():
        us_ticker = reverse_ucits.get(ucits_ticker)
        if not us_ticker:
            continue   # not a ticker we manage — leave it alone

        sig         = signals.get(us_ticker, {})
        sig_type    = sig.get("signal", "NEUTRAL")
        current_qty = float(position.get("quantity", 0) or 0)

        if current_qty <= 0:
            continue

        should_exit  = False
        exit_reason  = ""
        neutral_key  = f"t212_neutral_since:{ucits_ticker}"

        # 1. Immediate exit on strong sell signals
        if sig_type in immediate_exits:
            should_exit = True
            exit_reason = f"signal={sig_type}"
            _sm_clear(neutral_key)  # reset any NEUTRAL timer

        # 2. Confirmed NEUTRAL exit (primary signal-driven exit)
        elif exit_on_signal and sig_type not in ("BUY", "STRONG_BUY"):
            first_neutral_str = _sm_get(neutral_key)
            if first_neutral_str is None:
                _sm_set(neutral_key, datetime.utcnow().isoformat())
                logger.info(
                    "T212 Invest: %s signal is %s — started NEUTRAL confirmation timer",
                    us_ticker, sig_type,
                )
            else:
                try:
                    hours_neutral = (
                        datetime.utcnow() - datetime.fromisoformat(first_neutral_str)
                    ).total_seconds() / 3600
                    if hours_neutral >= confirmation_hours:
                        should_exit = True
                        exit_reason = (
                            f"signal={sig_type} confirmed after "
                            f"{hours_neutral:.1f}h ({confirmation_runs} runs)"
                        )
                except Exception:
                    pass

        else:
            # Signal is BUY/STRONG_BUY — clear any pending NEUTRAL timer
            _sm_clear(neutral_key)

        # 3. Backstop time limit (fires regardless of signal, if enabled)
        if not should_exit and time_limit_backstop and max_holding_days > 0:
            first_fill = first_fill_dates.get(ucits_ticker)
            if first_fill is not None:
                days_held = (datetime.utcnow() - first_fill).days
                if days_held >= max_holding_days:
                    should_exit = True
                    exit_reason = (
                        f"backstop time limit: {days_held}d >= {max_holding_days}d"
                    )

        if not should_exit:
            continue

        # Long-only safety: current_qty must be positive (we hold shares).
        # current_qty <= 0 is caught above, but re-check before sending the
        # close order so we never accidentally place a naked short.
        close_qty = abs(current_qty)
        if close_qty <= 0:
            logger.warning(
                "T212 Invest: %s close skipped — quantity %.4f is not positive.",
                us_ticker, current_qty,
            )
            continue

        logger.info(
            "T212 Invest: exiting %s (%s) — %s, qty=%.4f",
            us_ticker, ucits_ticker, exit_reason, close_qty,
        )

        status     = "FAILED"
        order_id   = None
        error_msg  = None
        live_price = prices.get(us_ticker, 0.0)

        try:
            response = client.place_market_order(ucits_ticker, -close_qty)
            order_id = str(response.get("id", ""))
            status   = "PLACED"
            logger.info(
                "T212 Invest: SELL %s qty=%.4f id=%s", ucits_ticker, current_qty, order_id,
            )
        except Exception as exc:
            error_msg = str(exc)
            logger.error("T212 Invest: sell order failed for %s: %s", us_ticker, exc)
            _set_t212_error(f"Sell order failed for {us_ticker}: {error_msg}")
            _send_t212_error_email(error_msg, config)

        est_gbp = (live_price / gbpusd) * close_qty if live_price and gbpusd else None
        _log_t212_order(
            ticker=us_ticker,
            t212_ticker=ucits_ticker,
            direction="SELL",
            quantity=close_qty,
            estimated_price=live_price,
            estimated_value_gbp=est_gbp,
            status=status,
            order_id=order_id,
            signal_type=sig_type,
            confluence_score=sig.get("confluence_score"),
            regime=sig.get("regime"),
            account_type=account_label,
            currency_code=ucits_currencies.get(ucits_ticker),
            error_message=error_msg,
        )

        if status == "FAILED":
            logger.warning("T212 Invest: halting after sell-order failure.")
            return

        time.sleep(0.5)

    # ────────────────────────────────────────────────────────────────────────
    # Phase 2 — BUY: open new long positions for BUY/STRONG_BUY signals
    #
    # T212 Invest is a cash account — no shorting.  Tickers with SELL or
    # STRONG_SELL signals are skipped here; the account simply holds cash
    # during bearish regimes rather than going net short.  Short-side signal
    # validation is handled by the virtual trading system instead.
    # ────────────────────────────────────────────────────────────────────────
    if total_val > 0 and (invested / total_val) >= max_deployed_pct:
        logger.info(
            "T212 Invest: %.0f%% deployed (limit %.0f%%) — no new orders.",
            (invested / total_val) * 100, max_deployed_pct * 100,
        )
        return

    orders_placed = 0

    for us_ticker, ucits_raw in ucits_mapping.items():
        # Forex pairs (yfinance tickers ending in =X) are CFD-only instruments
        # and cannot be held in a T212 Invest cash account.
        if us_ticker.endswith("=X"):
            logger.debug(
                "T212 Invest: %s is a forex pair (=X suffix) — skipping (CFD-only).",
                us_ticker,
            )
            continue

        ucits_ticker, instrument_currency = _ucits_entry(ucits_raw)
        sig      = signals.get(us_ticker, {})
        sig_type = sig.get("signal", "NEUTRAL")

        if sig_type not in entry_signals:
            continue

        # Duplicate guards
        if ucits_ticker in positions_by_ucits:
            logger.debug("T212 Invest: %s already held — skipping.", us_ticker)
            continue
        if us_ticker in open_order_tickers:
            logger.debug("T212 Invest: %s has a recent order in DB — skipping.", us_ticker)
            continue

        # Position sizing: cap at max_position_gbp and 25% of available cash per order
        position_gbp = min(max_position_gbp, free_cash * 0.25)
        if position_gbp < 10:
            logger.warning(
                "T212 Invest: insufficient free cash (£%.2f) — stopping new orders.", free_cash,
            )
            break

        quantity = client.get_quantity_for_gbp(us_ticker, position_gbp)
        if quantity <= 0:
            logger.warning(
                "T212 Invest: could not calculate quantity for %s — skipping.", us_ticker,
            )
            continue

        # All instruments in ucits_mapping are EU/UK-listed ETFs, which require
        # whole-share quantities. T212's maxQuantityPrecision metadata is
        # unreliable for these instruments, so always floor to an integer.
        meta    = instrument_meta.get(ucits_ticker, {})
        min_qty = float(meta.get("minTradeQuantity", 1.0))
        quantity = math.floor(quantity)
        if quantity < min_qty:
            logger.warning(
                "T212 Invest: %s (%s) qty %.4f is below instrument minimum %.4f — skipping.",
                us_ticker, ucits_ticker, quantity, min_qty,
            )
            continue

        # Long-only safety: quantity must be strictly positive for a BUY order.
        if quantity <= 0:
            logger.warning(
                "T212 Invest: %s BUY skipped — quantity %.4f is not positive.",
                us_ticker, quantity,
            )
            continue

        live_price = prices.get(us_ticker, 0.0)
        status     = "FAILED"
        order_id   = None
        error_msg  = None

        try:
            response  = client.place_market_order(ucits_ticker, quantity)
            order_id  = str(response.get("id", ""))
            status    = "PLACED"
            free_cash -= position_gbp
            orders_placed += 1
            logger.info(
                "T212 Invest: BUY %s (%s) qty=%.4f approx £%.0f  id=%s",
                us_ticker, ucits_ticker, quantity, position_gbp, order_id,
            )
        except Exception as order_exc:
            error_msg = str(order_exc)
            logger.error("T212 Invest: buy order failed for %s: %s", us_ticker, order_exc)
            _set_t212_error(f"Buy order failed for {us_ticker}: {error_msg}")
            _send_t212_error_email(error_msg, config)

        # Prefer the live currencyCode from T212 instruments API; fall back to config.
        _api_ccy = instrument_meta.get(ucits_ticker, {}).get("currencyCode")
        _ccy_to_log = _api_ccy or instrument_currency or None
        _log_t212_order(
            ticker=us_ticker,
            t212_ticker=ucits_ticker,
            direction="BUY",
            quantity=quantity,
            estimated_price=live_price,
            estimated_value_gbp=position_gbp,
            status=status,
            order_id=order_id,
            signal_type=sig_type,
            confluence_score=sig.get("confluence_score"),
            regime=sig.get("regime"),
            account_type=account_label,
            currency_code=_ccy_to_log,
            error_message=error_msg,
        )

        if status == "FAILED":
            logger.warning("T212 Invest: halting placement after buy-order failure.")
            break

        time.sleep(0.5)

    if orders_placed > 0:
        _clear_t212_error()

    logger.info(
        "T212 Invest: pipeline complete — %d order(s) placed, free cash approx £%.0f.",
        orders_placed, free_cash,
    )
