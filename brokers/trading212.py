"""Trading212 Invest account API client and automated paper-order placement.

Three concerns live here:

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

3.  maybe_place_stock_orders(config, run_type) — a parallel path for direct
    US large-cap stocks (watchlist.us_large_cap), reading the separate
    trading212_stocks config block instead of trading212_invest. Same EXIT/
    BUY phase structure, but smaller positions, a tighter deployment cap, a
    shorter holding window, a stricter TRENDING_BULL-only regime gate, and
    support for fractional-share ordering where the T212 instruments API
    reports it. Shares the t212_invest_orders table with the ETF path.

Safety invariants (both order-placement functions):
  - live: false is the hard default.  Live trading is explicitly blocked:
    place_market_order() raises RuntimeError if self.live is True.
  - Only tickers listed in ucits_mapping (ETFs) or watchlist.us_large_cap
    (stocks) are eligible for new orders.
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

from analysis.regime import get_latest_regime_name, is_entry_allowed, resolve_entry_signal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instruments cache (module-level, 24-hour TTL)
# ---------------------------------------------------------------------------

_instruments_cache: dict[str, tuple[datetime, list]] = {}
# Instrument prices (read from this same cache via get_instrument_price_gbp)
# are used directly for order sizing, so a 24h TTL was too stale for that
# purpose even though it was fine for the mostly-static metadata fields
# (currencyCode, minTradeQuantity) this cache originally existed for.
_INSTRUMENTS_TTL = timedelta(hours=1)


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

    @staticmethod
    def _fx_rates() -> tuple[float, float]:
        """Return (gbpusd, eurusd) — latest close prices from price_data.

        GBPUSD=X and EURUSD=X are both quoted as USD-per-1-unit, e.g.
        GBPUSD=X ~= 1.27 means 1 GBP = 1.27 USD. Falls back to a fixed
        approximation if either series is missing from the DB.
        """
        try:
            from sqlalchemy import text as sa_text
            from data.database import get_engine
            eng = get_engine()
            with eng.connect() as conn:
                row = conn.execute(sa_text("""
                    SELECT
                        (SELECT close FROM price_data
                         WHERE ticker = 'GBPUSD=X' ORDER BY date DESC LIMIT 1) AS gbpusd,
                        (SELECT close FROM price_data
                         WHERE ticker = 'EURUSD=X' ORDER BY date DESC LIMIT 1) AS eurusd
                """)).fetchone()
            gbpusd = float(row[0]) if row and row[0] else 1.27
            eurusd = float(row[1]) if row and row[1] else 1.08
            return gbpusd, eurusd
        except Exception:
            return 1.27, 1.08

    @staticmethod
    def _to_gbp(price: float, currency: str, gbpusd: float, eurusd: float) -> Optional[float]:
        """Convert a native-currency instrument price to GBP. None if unknown currency."""
        currency = (currency or "").upper()
        if currency == "GBP":
            return price
        if currency == "GBX":   # LSE pence quoting
            return price / 100.0
        if currency == "USD":
            return price / gbpusd if gbpusd else None
        if currency == "EUR":
            return (price * eurusd) / gbpusd if gbpusd else None
        return None

    def get_instrument_price_gbp(
        self, t212_ticker: str, research_ticker: Optional[str] = None,
    ) -> float:
        """Return the T212 instrument's own per-share price, converted to GBP.

        Primary path: reads currentPrice + currencyCode for t212_ticker from
        get_instruments() metadata and converts to GBP via live FX rates.
        UCITS instrument prices can differ from the US research ticker's
        price by an order of magnitude in either direction (different
        listing venue, different share structure) — sizing must always
        prefer the instrument's own price when it is available.

        Fallback path: if the instruments API has no usable price for this
        instrument, falls back to research_ticker's close price from
        price_data (assumed USD) — the old, less accurate behaviour. Logs a
        clear warning since this may misestimate order size for UCITS
        instruments whose price diverges from the research ticker.

        Returns 0.0 if no price could be determined at all.
        """
        gbpusd, eurusd = self._fx_rates()

        try:
            instruments = self.get_instruments()
            inst = next(
                (i for i in (instruments or []) if i.get("ticker") == t212_ticker), None,
            )
            if inst:
                raw_price = inst.get("currentPrice") or inst.get("price")
                currency  = inst.get("currencyCode") or ""
                if raw_price:
                    price_gbp = self._to_gbp(float(raw_price), currency, gbpusd, eurusd)
                    if price_gbp and price_gbp > 0:
                        return price_gbp
        except Exception as exc:
            logger.warning(
                "get_instrument_price_gbp: instrument lookup failed for %s: %s",
                t212_ticker, exc,
            )

        if not research_ticker:
            return 0.0

        logger.warning(
            "WARNING: Using research ticker price for %s quantity estimation — "
            "T212 instrument price unavailable. Order size may be inaccurate.",
            research_ticker,
        )
        try:
            from sqlalchemy import text as sa_text
            from data.database import get_engine
            eng = get_engine()
            with eng.connect() as conn:
                row = conn.execute(sa_text("""
                    SELECT close FROM price_data
                    WHERE ticker = :ticker ORDER BY date DESC LIMIT 1
                """), {"ticker": research_ticker}).fetchone()
            if row and row[0]:
                return float(row[0]) / gbpusd if gbpusd else 0.0
        except Exception as exc:
            logger.warning(
                "get_instrument_price_gbp: fallback price lookup failed for %s: %s",
                research_ticker, exc,
            )
        return 0.0

    def get_quantity_for_gbp(
        self,
        t212_ticker: str,
        gbp_amount: float,
        research_ticker: Optional[str] = None,
        floor_to_whole_share: bool = True,
    ) -> float:
        """Calculate share quantity for a given GBP order size.

        Sizes against the T212 instrument's own price (via
        get_instrument_price_gbp), not the research ticker's price — see
        that method's docstring for why. floor_to_whole_share defaults to
        True (whole-share UCITS ETF orders); callers placing fractional
        share orders should pass False and apply their own precision
        rounding to the raw quantity.
        """
        price_gbp = self.get_instrument_price_gbp(t212_ticker, research_ticker=research_ticker)
        if not price_gbp or price_gbp <= 0:
            logger.warning("get_quantity_for_gbp: no usable price for %s — returning 0.", t212_ticker)
            return 0.0

        raw_quantity = gbp_amount / price_gbp
        quantity = math.floor(raw_quantity) if floor_to_whole_share else round(raw_quantity, 4)
        quantity = max(0.0, quantity)

        logger.info(
            "Sizing %s (%s): price=%.4f GBP, target=%.2f GBP, quantity=%s, "
            "estimated_value=%.2f GBP",
            research_ticker or t212_ticker, t212_ticker, price_gbp, gbp_amount,
            quantity, quantity * price_gbp,
        )
        return quantity


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
                SELECT id, order_id, ticker, t212_ticker, direction,
                       quantity, currency_code
                FROM t212_invest_orders
                WHERE status = 'PLACED'
                  AND order_id IS NOT NULL
                  AND order_id != ''
                ORDER BY placed_at DESC
                LIMIT 50
            """)).mappings().all()
            placed = [dict(r) for r in rows]

            # Fetch FX rates for converting instrument-currency fill values to GBP.
            fx_rows = conn.execute(sa_text("""
                SELECT p.ticker, p.close
                FROM price_data p
                INNER JOIN (
                    SELECT ticker, MAX(date) AS md FROM price_data
                    WHERE ticker IN ('GBPUSD=X', 'EURUSD=X')
                    GROUP BY ticker
                ) latest ON p.ticker = latest.ticker AND p.date = latest.md
            """)).mappings().all()
            _fx = {r["ticker"]: float(r["close"]) for r in fx_rows if r["close"]}
    except Exception as exc:
        logger.warning("poll_placed_orders: DB read failed — %s", exc)
        return 0

    if not placed:
        return 0

    _gbpusd = _fx.get("GBPUSD=X", 1.27)
    _eurusd = _fx.get("EURUSD=X", 1.08)

    def _native_to_gbp(amount: float, ccy: Optional[str]) -> float:
        """Convert *amount* in instrument-native currency to GBP."""
        if not ccy or ccy == "GBP":
            return amount
        if ccy == "GBX":          # pence
            return amount / 100.0
        if ccy == "USD":
            return amount / _gbpusd
        if ccy == "EUR":
            return amount * _eurusd / _gbpusd
        return amount              # unknown — return unconverted

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

        # Compute actual position value in GBP (fill price × qty converted from
        # the instrument's native currency) so estimated_value_gbp reflects the
        # real cost basis rather than the intended position size.
        # Use filled_qty first; fall back to the ordered quantity stored in the DB.
        effective_qty = filled_qty or (float(row.get("quantity") or 0) or None)
        actual_value: Optional[float] = None
        if new_status == "FILLED" and fill_price and effective_qty:
            ccy = row.get("currency_code") or "USD"
            actual_value = round(_native_to_gbp(fill_price * effective_qty, ccy), 2)

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

    regime_name       = get_latest_regime_name()
    _entry_cfg        = resolve_entry_signal(t212_cfg, regime_name)
    _exit_cfg         = t212_cfg.get("exit_signal",  "SELL")
    entry_signals     = ("BUY", "STRONG_BUY") if _entry_cfg == "BUY" else ("STRONG_BUY",)
    if regime_name and regime_name in t212_cfg.get("entry_signal_overrides", {}):
        logger.info(
            "T212 Invest: regime=%s — using entry_signal override %s (global default %s).",
            regime_name, _entry_cfg, t212_cfg.get("entry_signal", "STRONG_BUY"),
        )
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
    if not is_entry_allowed(config, regime_name, "trading212_invest"):
        logger.info(
            "Skipping T212 Invest BUY phase — regime is %s, entries blocked "
            "(allowed_entry_regimes: %s).",
            regime_name, t212_cfg.get("allowed_entry_regimes"),
        )
        return

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

        # Size using the UCITS instrument's own price, not the US research
        # ticker's price — UCITS instrument prices can differ from the
        # research ticker's price by an order of magnitude in either
        # direction (different listing, different share structure).
        quantity = client.get_quantity_for_gbp(
            ucits_ticker, position_gbp, research_ticker=us_ticker,
        )
        if quantity <= 0:
            logger.warning(
                "T212 Invest: could not calculate quantity for %s (%s) — skipping.",
                us_ticker, ucits_ticker,
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

        # Actual instrument price used for sizing, re-read for an accurate
        # estimated_value_gbp (quantity was floored above, so this differs
        # slightly from the pre-floor target position_gbp).
        price_gbp     = client.get_instrument_price_gbp(ucits_ticker, research_ticker=us_ticker)
        est_value_gbp = quantity * price_gbp if price_gbp else position_gbp

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
            estimated_value_gbp=est_value_gbp,
            status=status,
            order_id=order_id,
            signal_type=sig_type,
            confluence_score=sig.get("confluence_score"),
            regime=regime_name or sig.get("regime"),
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


# ---------------------------------------------------------------------------
# Automated stock order placement — parallel path for direct US large-cap stocks
# ---------------------------------------------------------------------------

def _stock_universe(config: dict) -> list[dict]:
    """Return watchlist.us_large_cap entries eligible for stock automation.

    Eligible = tradeable and not already claimed by trading212_invest's
    ucits_mapping — defensive dedup so a ticker can never be automated
    through both T212 paths at once.
    """
    ucits_mapping = config.get("trading212_invest", {}).get("ucits_mapping", {})
    entries = config.get("watchlist", {}).get("us_large_cap", [])
    return [
        e for e in entries
        if isinstance(e, dict) and e.get("tradeable") and e.get("ticker") not in ucits_mapping
    ]


def maybe_place_stock_orders(config: dict, run_type: str = "full") -> None:
    """Place T212 Invest DEMO market orders for direct US large-cap stocks.

    Parallel to maybe_place_t212_orders() (UCITS ETFs) but reads the
    trading212_stocks config block, sources its ticker universe from
    watchlist.us_large_cap instead of ucits_mapping, and supports fractional
    share ordering where the T212 instruments API reports it.

    Phase 1 — EXIT: closes any open stock position whose underlying research
    signal has moved to SELL/STRONG_SELL, or via the confirmed-NEUTRAL /
    backstop time-limit exits — identical trigger priority to
    maybe_place_t212_orders(). Always runs regardless of the current regime;
    only new entries are regime-gated.

    Phase 2 — BUY: places new market orders for STRONG_BUY signals on tickers
    in watchlist.us_large_cap, gated first by
    trading212_stocks.allowed_entry_regimes (TRENDING_BULL only by default —
    a materially stricter gate than the ETF path's TRENDING_BULL/BEAR/
    HIGH_VOLATILITY, since a single stock's downside in a bear or high-vol
    regime is far less diversified than an ETF's) and then by the resolved
    entry_signal_overrides threshold.

    Entry conditions (all must be true):
      1. trading212_stocks.enabled: true in config.yaml
      2. run_type == "full"  — only on EOD runs with fresh price data
      3. T212_API_KEY and T212_API_SECRET environment variables are set
      4. watchlist.us_large_cap has at least one tradeable, unclaimed ticker

    Per-BUY-order conditions:
      - Current regime is in allowed_entry_regimes
      - Signal meets the resolved entry_signal_overrides threshold
      - No existing T212 position for this instrument
      - No recent order in t212_invest_orders (last 30 days, PLACED or FILLED)
      - Free cash covers at least £10
      - Total deployed remains below max_total_deployed x account value

    Order sizing: when trading212_stocks.fractional_shares is true and the
    T212 instruments API reports maxQuantityPrecision > 0 for the ticker,
    the order quantity is a decimal (position_gbp converted to USD via the
    live GBPUSD rate, divided by price, rounded to that precision) rather
    than floored to a whole share. Falls back to the same
    floor-to-integer + minTradeQuantity guard used for UCITS ETFs when
    precision is 0/absent or fractional_shares is false. This reuses
    get_quantity_for_gbp()'s existing GBP->USD conversion and the same
    /equity/orders/market endpoint as whole-share orders — T212's market
    order endpoint accepts a decimal quantity directly for instruments that
    support fractional trading, so no separate "value-based" order call is
    needed or implemented here.

    Shares the t212_invest_orders table with the ETF path (account_type
    DEMO/LIVE, same schema, same 30-day duplicate-order window) — no
    separate table.

    EARNINGS GAP RISK: no earnings-calendar check is performed anywhere in
    this pipeline. A stop loss can be gapped through overnight on an
    earnings announcement — the next trade alert / EOD exit check will fill
    at whatever price is then available, not the intended stop price. This
    is a materially larger risk for individual stocks than for diversified
    ETFs and is not currently mitigated in code.
    """
    stock_cfg = config.get("trading212_stocks", {})
    if not stock_cfg.get("enabled", False):
        return

    if run_type != "full":
        logger.debug("T212 Stocks: skipping order placement on %s run (full only).", run_type)
        return

    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")
    if not api_key or not api_secret:
        logger.warning("T212 Stocks: T212_API_KEY or T212_API_SECRET not set — skipping.")
        _set_t212_error("API credentials not configured (T212_API_KEY / T212_API_SECRET)")
        return

    stock_entries = _stock_universe(config)
    if not stock_entries:
        logger.info("T212 Stocks: no tradeable us_large_cap tickers — nothing to do.")
        return

    # Map research ticker -> T212 instrument ticker (identity by default; overridden
    # per watchlist entry via trading212_instrument where the T212 name differs).
    research_to_t212: dict[str, str] = {
        e["ticker"]: e.get("trading212_instrument", e["ticker"]) for e in stock_entries
    }
    t212_to_research: dict[str, str] = {v: k for k, v in research_to_t212.items()}

    live                = stock_cfg.get("live", False)
    max_position_gbp    = float(stock_cfg.get("max_position_gbp", 200))
    max_deployed_pct     = float(stock_cfg.get("max_total_deployed", 0.3))
    fractional_enabled  = bool(stock_cfg.get("fractional_shares", True))

    regime_name         = get_latest_regime_name()
    _entry_cfg          = resolve_entry_signal(stock_cfg, regime_name)
    _exit_cfg           = stock_cfg.get("exit_signal", "SELL")
    entry_signals       = ("BUY", "STRONG_BUY") if _entry_cfg == "BUY" else ("STRONG_BUY",)
    if regime_name and regime_name in stock_cfg.get("entry_signal_overrides", {}):
        logger.info(
            "T212 Stocks: regime=%s — using entry_signal override %s (global default %s).",
            regime_name, _entry_cfg, stock_cfg.get("entry_signal", "STRONG_BUY"),
        )
    immediate_exits     = ("SELL", "STRONG_SELL") if _exit_cfg == "SELL" else ("STRONG_SELL",)
    exit_on_signal       = bool(stock_cfg.get("exit_on_signal", True))
    time_limit_backstop  = bool(stock_cfg.get("time_limit_as_backstop", True))
    max_holding_days     = int(stock_cfg.get("max_holding_days", 30))
    confirmation_runs    = int(config.get("signals", {}).get("reversal_confirmation_runs", 2))
    confirmation_hours   = confirmation_runs * 3

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
        positions_by_t212: dict[str, dict] = {
            p.get("ticker", ""): p for p in (positions or [])
        }

        instruments = client.get_instruments()
        instrument_meta: dict[str, dict] = {
            inst["ticker"]: inst
            for inst in (instruments or [])
            if "ticker" in inst
        }

    except Exception as exc:
        logger.error("T212 Stocks: API error during setup — %s", exc, exc_info=True)
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
        logger.error("T212 Stocks: DB read error — %s", exc, exc_info=True)
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
    # Phase 1 — EXIT (close existing long positions only). Always runs,
    # regardless of the current regime — only the BUY phase below is
    # regime-gated. Trigger priority is identical to maybe_place_t212_orders():
    #   1. Immediate: SELL or STRONG_SELL signal
    #   2. Confirmed: signal drops to NEUTRAL for confirmation_runs × 3 h
    #   3. Backstop: position held > max_holding_days (if time_limit_as_backstop)
    # ────────────────────────────────────────────────────────────────────────
    from data.database import (  # noqa: PLC0415
        set_metadata as _sm_set,
        get_metadata_str as _sm_get,
        clear_metadata as _sm_clear,
    )

    for t212_ticker, position in positions_by_t212.items():
        us_ticker = t212_to_research.get(t212_ticker)
        if not us_ticker:
            continue   # not a ticker we manage — leave it alone

        sig         = signals.get(us_ticker, {})
        sig_type    = sig.get("signal", "NEUTRAL")
        current_qty = float(position.get("quantity", 0) or 0)

        if current_qty <= 0:
            continue

        should_exit  = False
        exit_reason  = ""
        neutral_key  = f"t212_stock_neutral_since:{t212_ticker}"

        # 1. Immediate exit on strong sell signals
        if sig_type in immediate_exits:
            should_exit = True
            exit_reason = f"signal={sig_type}"
            _sm_clear(neutral_key)

        # 2. Confirmed NEUTRAL exit (primary signal-driven exit)
        elif exit_on_signal and sig_type not in ("BUY", "STRONG_BUY"):
            first_neutral_str = _sm_get(neutral_key)
            if first_neutral_str is None:
                _sm_set(neutral_key, datetime.utcnow().isoformat())
                logger.info(
                    "T212 Stocks: %s signal is %s — started NEUTRAL confirmation timer",
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
            first_fill = first_fill_dates.get(t212_ticker)
            if first_fill is not None:
                days_held = (datetime.utcnow() - first_fill).days
                if days_held >= max_holding_days:
                    should_exit = True
                    exit_reason = (
                        f"backstop time limit: {days_held}d >= {max_holding_days}d"
                    )

        if not should_exit:
            continue

        close_qty = abs(current_qty)
        if close_qty <= 0:
            logger.warning(
                "T212 Stocks: %s close skipped — quantity %.4f is not positive.",
                us_ticker, current_qty,
            )
            continue

        logger.info(
            "T212 Stocks: exiting %s (%s) — %s, qty=%.4f",
            us_ticker, t212_ticker, exit_reason, close_qty,
        )

        status     = "FAILED"
        order_id   = None
        error_msg  = None
        live_price = prices.get(us_ticker, 0.0)

        try:
            response = client.place_market_order(t212_ticker, -close_qty)
            order_id = str(response.get("id", ""))
            status   = "PLACED"
            logger.info(
                "T212 Stocks: SELL %s qty=%.4f id=%s", t212_ticker, current_qty, order_id,
            )
        except Exception as exc:
            error_msg = str(exc)
            logger.error("T212 Stocks: sell order failed for %s: %s", us_ticker, exc)
            _set_t212_error(f"Sell order failed for {us_ticker}: {error_msg}")
            _send_t212_error_email(error_msg, config)

        est_gbp = (live_price / gbpusd) * close_qty if live_price and gbpusd else None
        _log_t212_order(
            ticker=us_ticker,
            t212_ticker=t212_ticker,
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
            currency_code=instrument_meta.get(t212_ticker, {}).get("currencyCode") or "USD",
            error_message=error_msg,
        )

        if status == "FAILED":
            logger.warning("T212 Stocks: halting after sell-order failure.")
            return

        time.sleep(0.5)

    # ────────────────────────────────────────────────────────────────────────
    # Phase 2 — BUY: open new long positions for STRONG_BUY signals on
    # watchlist.us_large_cap tickers, gated first by allowed_entry_regimes.
    # ────────────────────────────────────────────────────────────────────────
    if not is_entry_allowed(config, regime_name, "trading212_stocks"):
        logger.info(
            "Skipping stock BUY phase — regime is %s, entries blocked for stocks "
            "(allowed_entry_regimes: %s).",
            regime_name, stock_cfg.get("allowed_entry_regimes"),
        )
        return

    if total_val > 0 and (invested / total_val) >= max_deployed_pct:
        logger.info(
            "T212 Stocks: %.0f%% deployed (limit %.0f%%) — no new orders.",
            (invested / total_val) * 100, max_deployed_pct * 100,
        )
        return

    orders_placed = 0

    for entry in stock_entries:
        us_ticker   = entry["ticker"]
        t212_ticker = research_to_t212[us_ticker]

        sig      = signals.get(us_ticker, {})
        sig_type = sig.get("signal", "NEUTRAL")

        if sig_type not in entry_signals:
            continue

        # Duplicate guards
        if t212_ticker in positions_by_t212:
            logger.debug("T212 Stocks: %s already held — skipping.", us_ticker)
            continue
        if us_ticker in open_order_tickers:
            logger.debug("T212 Stocks: %s has a recent order in DB — skipping.", us_ticker)
            continue

        # Position sizing: cap at max_position_gbp and 25% of available cash per order
        position_gbp = min(max_position_gbp, free_cash * 0.25)
        if position_gbp < 10:
            logger.warning(
                "T212 Stocks: insufficient free cash (£%.2f) — stopping new orders.", free_cash,
            )
            break

        # Size using the T212 instrument's own price, not the research
        # ticker's price — for most us_large_cap stocks the T212 instrument
        # ticker equals the research ticker so these usually match, but any
        # trading212_instrument override (a different T212 listing) can
        # diverge the same way UCITS ETF instruments do.
        raw_quantity = client.get_quantity_for_gbp(
            t212_ticker, position_gbp, research_ticker=us_ticker, floor_to_whole_share=False,
        )
        if raw_quantity <= 0:
            logger.warning(
                "T212 Stocks: could not calculate quantity for %s (%s) — skipping.",
                us_ticker, t212_ticker,
            )
            continue

        meta      = instrument_meta.get(t212_ticker, {})
        min_qty   = float(meta.get("minTradeQuantity", 1.0))
        precision = int(meta.get("maxQuantityPrecision", 0) or 0)

        if fractional_enabled and precision > 0:
            # Fractional order: the instrument supports decimal quantities —
            # keep the GBP-sized quantity, rounded to what it actually supports.
            quantity = round(raw_quantity, precision)
        else:
            # Whole shares only — same floor pattern as the UCITS ETF path.
            quantity = math.floor(raw_quantity)

        if quantity < min_qty:
            logger.warning(
                "T212 Stocks: %s (%s) qty %.4f is below instrument minimum %.4f — skipping.",
                us_ticker, t212_ticker, quantity, min_qty,
            )
            continue

        if quantity <= 0:
            logger.warning(
                "T212 Stocks: %s BUY skipped — quantity %.4f is not positive.",
                us_ticker, quantity,
            )
            continue

        price_gbp     = client.get_instrument_price_gbp(t212_ticker, research_ticker=us_ticker)
        est_value_gbp = quantity * price_gbp if price_gbp else position_gbp

        live_price = prices.get(us_ticker, 0.0)
        status     = "FAILED"
        order_id   = None
        error_msg  = None

        try:
            response  = client.place_market_order(t212_ticker, quantity)
            order_id  = str(response.get("id", ""))
            status    = "PLACED"
            free_cash -= position_gbp
            orders_placed += 1
            logger.info(
                "T212 Stocks: BUY %s (%s) qty=%s approx £%.0f  id=%s",
                us_ticker, t212_ticker, quantity, position_gbp, order_id,
            )
        except Exception as order_exc:
            error_msg = str(order_exc)
            logger.error("T212 Stocks: buy order failed for %s: %s", us_ticker, order_exc)
            _set_t212_error(f"Buy order failed for {us_ticker}: {error_msg}")
            _send_t212_error_email(error_msg, config)

        _ccy_to_log = meta.get("currencyCode") or "USD"
        _log_t212_order(
            ticker=us_ticker,
            t212_ticker=t212_ticker,
            direction="BUY",
            quantity=quantity,
            estimated_price=live_price,
            estimated_value_gbp=est_value_gbp,
            status=status,
            order_id=order_id,
            signal_type=sig_type,
            confluence_score=sig.get("confluence_score"),
            regime=regime_name or sig.get("regime"),
            account_type=account_label,
            currency_code=_ccy_to_log,
            error_message=error_msg,
        )

        if status == "FAILED":
            logger.warning("T212 Stocks: halting placement after buy-order failure.")
            break

        time.sleep(0.5)

    if orders_placed > 0:
        _clear_t212_error()

    logger.info(
        "T212 Stocks: pipeline complete — %d order(s) placed, free cash approx £%.0f.",
        orders_placed, free_cash,
    )
