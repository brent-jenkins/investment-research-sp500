"""Trading212 Invest account API client and automated paper-order placement.

Two separate concerns live here:

1.  Trading212Client — thin HTTP wrapper around the T212 Invest REST API.
    Authentication: Basic Auth (base64 key:secret).
    All requests are retried up to 3 times with exponential back-off;
    HTTP 429 (rate-limited) is treated as a retryable condition.

2.  maybe_place_t212_orders(config, run_type) — called from the analysis
    pipeline after signals are generated.  Places DEMO market orders for
    STRONG_BUY signals on tickers that have a trading212_instrument mapping
    in config.yaml.  Has no effect unless:
      - trading212_invest.enabled: true in config.yaml
      - run_type == "full"  (requires fresh price data from the EOD pull)
      - T212_API_KEY and T212_API_SECRET environment variables are set

Safety invariants:
  - live: false is the hard default.  Live trading is explicitly blocked:
    place_market_order() raises RuntimeError if self.live is True.
  - Every order attempt (success or failure) is written to t212_invest_orders.
  - Duplicate positions are prevented by checking the DB and the live T212
    portfolio before placing.
  - Any API exception disables order placement for the current run and logs
    the error to system_metadata under "t212_invest_error".
"""

import base64
import logging
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
        url      = f"{self.base_url}{path}"
        timeout  = kwargs.pop("timeout", 10)
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
                    r.raise_for_status()  # raises immediately, no retry on 4xx
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
        """Positive quantity = buy, negative = sell.

        Raises RuntimeError if self.live is True — live trading is gated
        behind an explicit opt-in that must be set in the constructor.
        """
        if self.live:
            raise RuntimeError(
                "Live trading not yet enabled — instantiate Trading212Client "
                "with live=True only after thorough demo validation."
            )
        payload = {"ticker": ticker, "quantity": quantity}
        r = self._request("POST", "/equity/orders/market", json=payload)
        return r.json()

    def cancel_order(self, order_id: str) -> dict:
        r = self._request("DELETE", f"/equity/orders/{order_id}")
        return r.json()


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


def _log_t212_order(
    *,
    ticker: str,
    t212_ticker: str,
    direction: str,
    quantity: float,
    estimated_price: float,
    status: str,
    order_id: Optional[str],
    signal_type: str,
    confluence_score: Optional[float],
    regime: Optional[str],
    account_type: str,
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
                    quantity, estimated_price,
                    status, signal_type, confluence_score, regime,
                    account_type, error_message, placed_at, created_at
                ) VALUES (
                    :order_id, :ticker, :t212_ticker, :direction,
                    :quantity, :estimated_price,
                    :status, :signal_type, :confluence_score, :regime,
                    :account_type, :error_message, :now, :now
                )
            """), {
                "order_id":        order_id,
                "ticker":          ticker,
                "t212_ticker":     t212_ticker,
                "direction":       direction,
                "quantity":        quantity,
                "estimated_price": estimated_price,
                "status":          status,
                "signal_type":     signal_type,
                "confluence_score": confluence_score,
                "regime":          regime,
                "account_type":    account_type,
                "error_message":   error_message,
                "now":             datetime.utcnow().isoformat(),
            })
    except Exception as exc:
        logger.warning("Failed to log T212 order to DB: %s", exc)


# ---------------------------------------------------------------------------
# Phase 2 — automated paper-order placement
# ---------------------------------------------------------------------------

def maybe_place_t212_orders(config: dict, run_type: str = "full") -> None:
    """Place T212 Invest DEMO market orders for STRONG_BUY signals.

    Entry conditions (all must be true):
      1. trading212_invest.enabled: true in config.yaml
      2. run_type == "full"  — only on EOD runs with fresh price data
      3. T212_API_KEY and T212_API_SECRET environment variables are set

    Per-order conditions:
      - Ticker has a trading212_instrument mapping in config.yaml
      - T212 instrument exists in the live instruments list
      - No existing T212 position for this instrument
      - No recent order in t212_invest_orders (last 30 days, PLACED or FILLED)
      - Free cash covers at least one minimum order (£10)
      - Total deployed remains below max_total_deployed × account value

    All outcomes (success and failure) are written to t212_invest_orders.
    API errors set "t212_invest_error" in system_metadata and stop placement
    for the current run.
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

    live             = t212_cfg.get("live", False)
    max_position_gbp = float(t212_cfg.get("max_position_gbp", 500))
    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.5))

    # Build ticker → T212 instrument name mapping
    t212_map: dict[str, str] = {}
    for group in config.get("watchlist", {}).values():
        if not isinstance(group, list):
            continue
        for entry in group:
            if isinstance(entry, dict) and "ticker" in entry and "trading212_instrument" in entry:
                t212_map[entry["ticker"]] = entry["trading212_instrument"]

    if not t212_map:
        logger.info("T212 Invest: no trading212_instrument mappings in config — nothing to do.")
        return

    try:
        client = Trading212Client(live=live)

        # ── Account safety checks ────────────────────────────────────────────
        summary    = client.get_account_summary()
        total_val  = float(summary.get("total",    summary.get("cash", 0)) or 0)
        invested   = float(summary.get("invested", 0) or 0)
        free_cash  = float(summary.get("free",     summary.get("cash", 0)) or 0)

        if total_val > 0 and (invested / total_val) >= max_deployed_pct:
            logger.info(
                "T212 Invest: %.0f%% deployed (limit %.0f%%) — no new orders.",
                (invested / total_val) * 100, max_deployed_pct * 100,
            )
            return

        # ── Existing T212 positions (prevent duplicates) ─────────────────────
        positions         = client.get_positions()
        open_t212_tickers = {p.get("ticker", "") for p in positions}

        # ── Instrument validation lookup (shortName or ticker → internal ticker) ─
        instruments       = client.get_instruments()
        instr_by_short: dict[str, str] = {}
        instr_by_ticker: dict[str, str] = {}
        for instr in instruments:
            tk = instr.get("ticker",    "")
            sn = instr.get("shortName", "")
            nm = instr.get("name",      "")
            if tk:
                instr_by_ticker[tk] = tk
                instr_by_short[tk]  = tk   # also index by own ticker
            if sn:
                instr_by_short[sn] = tk
            if nm:
                instr_by_short[nm] = tk
        # Combined lookup: shortName → internal ticker
        instrument_lookup = {**instr_by_short, **instr_by_ticker}

    except Exception as exc:
        logger.error("T212 Invest: API error during setup — %s", exc, exc_info=True)
        _set_t212_error(str(exc))
        return

    # ── Read signals and prices from DB ─────────────────────────────────────
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            sig_rows = conn.execute(sa_text("""
                SELECT s.ticker, s.signal, s.confluence_score, s.regime
                FROM signals s
                INNER JOIN (
                    SELECT ticker, MAX(date) AS md FROM signals GROUP BY ticker
                ) latest ON s.ticker = latest.ticker AND s.date = latest.md
                WHERE s.signal = 'STRONG_BUY'
            """)).mappings().all()

            price_rows = conn.execute(sa_text("""
                SELECT p.ticker, p.close
                FROM price_data p
                INNER JOIN (
                    SELECT ticker, MAX(date) AS md FROM price_data GROUP BY ticker
                ) latest ON p.ticker = latest.ticker AND p.date = latest.md
            """)).mappings().all()

            # Recent open/filled orders in our DB (duplicate guard)
            try:
                order_rows = conn.execute(sa_text("""
                    SELECT DISTINCT ticker FROM t212_invest_orders
                    WHERE status IN ('PLACED', 'FILLED')
                    AND placed_at > datetime('now', '-30 days')
                """)).mappings().all()
                open_order_tickers: set[str] = {r["ticker"] for r in order_rows}
            except Exception:
                open_order_tickers = set()

    except Exception as exc:
        logger.error("T212 Invest: DB read error — %s", exc, exc_info=True)
        _set_t212_error(f"DB read failed: {exc}")
        return

    signals = [dict(r) for r in sig_rows]
    prices  = {r["ticker"]: float(r["close"]) for r in price_rows if r["close"] is not None}

    # Approximate GBP/USD rate for position sizing (from watchlist price data)
    gbpusd = prices.get("GBPUSD=X", 1.27)

    orders_placed = 0

    for sig in signals:
        ticker    = sig["ticker"]
        t212_name = t212_map.get(ticker)
        if not t212_name:
            continue  # no T212 mapping

        # Resolve to T212 internal ticker
        t212_internal = instrument_lookup.get(t212_name)
        if not t212_internal:
            logger.warning(
                "T212 Invest: '%s' (for %s) not found in instruments — check trading212_instrument value.",
                t212_name, ticker,
            )
            continue

        # Duplicate guards
        if t212_internal in open_t212_tickers:
            logger.debug("T212 Invest: %s already has an open T212 position — skipping.", ticker)
            continue
        if ticker in open_order_tickers:
            logger.debug("T212 Invest: %s has a recent order in DB — skipping.", ticker)
            continue

        # Position sizing
        live_price = prices.get(ticker)
        if not live_price or live_price <= 0:
            logger.warning("T212 Invest: no price for %s — skipping.", ticker)
            continue

        position_gbp = min(max_position_gbp, free_cash * 0.25)
        if position_gbp < 10:
            logger.warning(
                "T212 Invest: insufficient free cash (£%.2f) — stopping.", free_cash,
            )
            break

        # Convert GBP to native currency to get quantity
        # Simple: if the price is in USD (most US ETFs), convert via GBPUSD
        # T212 Invest handles FX internally; we pass the share quantity.
        price_in_usd_equiv = live_price  # assume USD-denominated for sizing
        native_amount = position_gbp * gbpusd
        quantity = round(native_amount / price_in_usd_equiv, 4)
        if quantity <= 0:
            continue

        # Place order
        status    = "FAILED"
        order_id  = None
        error_msg = None

        try:
            response  = client.place_market_order(t212_internal, quantity)
            order_id  = str(response.get("id", ""))
            status    = "PLACED"
            free_cash -= position_gbp
            orders_placed += 1
            logger.info(
                "T212 Invest: placed BUY %s (%s) qty=%.4f ≈£%.0f  id=%s",
                ticker, t212_internal, quantity, position_gbp, order_id,
            )
        except Exception as order_exc:
            error_msg = str(order_exc)
            logger.error("T212 Invest: order failed for %s: %s", ticker, order_exc)
            _set_t212_error(f"Order failed for {ticker}: {error_msg}")

        _log_t212_order(
            ticker=ticker,
            t212_ticker=t212_internal,
            direction="BUY",
            quantity=quantity,
            estimated_price=live_price,
            status=status,
            order_id=order_id,
            signal_type=sig["signal"],
            confluence_score=sig.get("confluence_score"),
            regime=sig.get("regime"),
            account_type="LIVE" if live else "DEMO",
            error_message=error_msg,
        )

        if status == "FAILED":
            logger.warning("T212 Invest: halting placement after order failure.")
            break

        # Courtesy delay between orders to respect rate limits
        time.sleep(0.5)

    if orders_placed > 0:
        _clear_t212_error()

    logger.info(
        "T212 Invest: pipeline complete — %d order(s) placed, free cash ≈ £%.0f.",
        orders_placed, free_cash,
    )
