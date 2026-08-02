"""Trading212 Invest account API client and automated order placement.

Two concerns live here:

1.  Trading212Client — thin HTTP wrapper around the T212 Invest REST API.
    Authentication: Basic Auth (base64 key:secret).
    All requests are retried up to 3 times with exponential back-off;
    HTTP 429 (rate-limited) is treated as a retryable condition.

2.  maybe_place_orders(config, run_type) / maybe_exit_positions(config) —
    order placement for the momentum + crisis-override strategy (see
    analysis/backtest.py and analysis/signals.py / analysis/crisis.py for
    the validated signal logic this mirrors exactly):

    Entry (all must hold): momentum signal on, ticker currently a genuine
    index member (or an ETF — no membership concept), not in the crisis
    entry-block window.

    Exit priority (checked in this exact order, matching
    analysis/backtest.py:simulate_trades()):
      1. CRISIS_EXIT   — today is a raw (>=7%) crisis day
      2. INDEX_REMOVAL — ticker is no longer a genuine index member (stocks only)
      3. STOP_LOSS     — latest close has reached the ATR stop fixed at entry
      4. SIGNAL_OFF    — trailing 12m momentum has turned non-positive
      5. MAX_HOLDING   — get_max_holding_days() backstop reached

Safety invariants (both order-placement functions):
  - live: false is the hard default. Live trading is explicitly blocked:
    place_market_order() raises RuntimeError if self.live is True.
  - Only tickers listed in the top-level `instruments:` config.yaml
    section are eligible for new orders.
  - Every order attempt (success or failure) is written to invest_orders.
  - Duplicate positions are prevented by checking the DB and the live T212
    portfolio before placing.
  - Any API exception disables order placement for the current run, writes
    the error to system_metadata under "invest_error", and sends an alert
    email.
  - Position sizing and the deployment cap (2026-08-01, see CLAUDE.md's
    "Live execution switched to the compounding two-parameter scheme"
    section) are both percentages of the account's own CURRENT total value
    (client.get_account_summary()["totalValue"], fetched fresh on every
    decide/submit call — a genuinely compounding balance) rather than a
    fixed £ figure or a fixed notional reference: trading212_invest.
    per_trade_pct ceilings a single new entry's VaR-sized position;
    max_total_deployed stops further entries once that fraction of the
    account's current value is committed. Re-checked per-iteration inside
    the submission loop (not just once before it starts) via a running
    invested total updated after each successful order — a cold start (0%
    deployed) would otherwise be able to walk through every qualifying
    ticker in one pass before the cap ever binds. See CLAUDE.md's "V2
    first-cycle deployment cap overshoot" section for the incident this
    per-iteration check originally fixed. There is deliberately no
    separate cap on the NUMBER of concurrently open positions — count is
    an emergent consequence of per_trade_pct and max_total_deployed
    together, not a third, independently-enforced rule (validated via a
    20-combination sweep against real 2007-2026 production data — see
    CLAUDE.md's "Compounding portfolio backtest" section).

"Never raises" contract: every DB write and every T212 API call site is
wrapped so a single ticker's failure never blocks the rest of the run, and
this module's own failure never blocks the analysis pipeline it's called
from.
"""
import base64
import logging
import math
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests

from analysis import crisis, signals, universe
from analysis.decision import (
    evaluate_exit_reason,
    is_asset_class_enabled,
    is_position_size_viable,
    passes_entry_gate,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
)
from analysis.universe import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _atr, _resolve_entry_stop, _var_position_size_core
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)

_ERROR_KEY = "invest_error"

# ---------------------------------------------------------------------------
# Instruments cache (module-level, 1-hour TTL)
# ---------------------------------------------------------------------------

_instruments_cache: dict[str, tuple[datetime, list]] = {}
# Instrument prices (read from this same cache via get_instrument_price_gbp)
# are used directly for order sizing, so a 24h TTL was too stale for that
# purpose even though it was fine for the mostly-static metadata fields
# (currencyCode, minTradeQuantity) this cache originally existed for.
_INSTRUMENTS_TTL = timedelta(hours=1)

# ---------------------------------------------------------------------------
# Last-known real price cache (persisted in system_metadata) — 2026-07-31.
#
# get_instrument_price_gbp()'s primary path (get_positions()'s live
# currentPrice) only has data while a position is actually open. Without
# this cache, fully exiting any ETF position would make that same T212
# instrument permanently unpriceable for a future re-entry (T212's API has
# no other quote source for it — see that method's docstring), silently
# freezing the whole ETF sleeve out of ever trading again after its first
# exit. Every successful live-position price read refreshes this cache, so
# a later re-entry within _LAST_PRICE_CACHE_MAX_AGE_DAYS still has a real,
# T212-confirmed reference price to size against.
# ---------------------------------------------------------------------------
_LAST_PRICE_CACHE_MAX_AGE_DAYS = 30


def _cache_last_known_price_gbp(t212_ticker: str, price_gbp: float) -> None:
    try:
        from data.database import set_metadata
        import json as _json
        set_metadata(
            f"last_known_price_gbp:{t212_ticker}",
            _json.dumps({"price_gbp": price_gbp, "as_of": datetime.utcnow().isoformat()}),
        )
    except Exception as exc:
        logger.warning("_cache_last_known_price_gbp: failed to cache %s: %s", t212_ticker, exc)


def _get_cached_price_gbp(t212_ticker: str) -> Optional[float]:
    try:
        from data.database import get_metadata_str
        import json as _json
        raw = get_metadata_str(f"last_known_price_gbp:{t212_ticker}")
        if not raw:
            return None
        data = _json.loads(raw)
        as_of = datetime.fromisoformat(data["as_of"])
        age_days = (datetime.utcnow() - as_of).total_seconds() / 86400
        if age_days > _LAST_PRICE_CACHE_MAX_AGE_DAYS:
            return None
        price_gbp = float(data["price_gbp"])
        return price_gbp if price_gbp > 0 else None
    except Exception as exc:
        logger.warning("_get_cached_price_gbp: failed to read cache for %s: %s", t212_ticker, exc)
        return None


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

    def _parse_json(self, r: requests.Response, *, allow_empty: bool = False):
        """Parse *r*'s JSON body. By the time this is ever called, _request()
        has already confirmed a successful (< 400) HTTP status -- so an
        unparseable body here is never itself evidence the underlying
        action failed. Every one of Trading212Client's public methods
        routes through this instead of a bare r.json() (2026-08-01 --
        found systemic across the whole client during the "Live and
        backtest consolidation audit", not unique to the one call site
        originally flagged; see CLAUDE.md's audit section for the full
        finding and the concrete bug this closes: _cancel_stale_weekend_
        orders() treating a genuinely successful T212 cancellation as
        FAILED purely because the DELETE response had an empty body).

        allow_empty=True -- for ACTION endpoints (place_market_order(),
        cancel_order()) where the HTTP status IS the success signal and
        any JSON body is supplementary detail. T212's DELETE
        /equity/orders/{id} commonly returns 200/204 with no body at all
        (standard REST convention for DELETE) -- an empty or malformed
        body here must never be treated as the action having failed. Falls
        back to {} and logs at WARNING (loud, not silent -- the ambiguity
        that no order/response detail such as an id could be captured is
        real and worth an operator's attention, even though the action
        itself is not treated as failed).

        allow_empty=False (the default, every read endpoint -- account
        summary, positions, instruments, order status, order history):
        the JSON body IS the payload a caller actually needs, so an
        unparseable body on a nominally-successful status is a genuine
        anomaly and still raises -- but with a clear WARNING logged first
        (status code + truncated raw body) so it's diagnosable as "T212
        returned a malformed response", not an opaque, contextless
        JSONDecodeError several call frames away from where it happened.
        """
        try:
            return r.json()
        except ValueError as exc:
            raw = (r.text or "")[:200]
            if allow_empty:
                logger.warning(
                    "T212 returned a successful (%s) response for %s with no "
                    "parseable body -- treating the action itself as having "
                    "succeeded (the HTTP status already confirms that), not "
                    "as a failure, but no response detail (e.g. an order id) "
                    "could be captured from this call. Body: %r",
                    r.status_code, r.url, raw,
                )
                return {}
            logger.warning(
                "T212 returned an unparseable body on a successful (%s) "
                "response from %s: %s. Body: %r",
                r.status_code, r.url, exc, raw,
            )
            raise

    # ── public API methods ───────────────────────────────────────────────────

    def get_account_summary(self) -> dict:
        r = self._request("GET", "/equity/account/summary")
        return self._parse_json(r)

    def get_positions(self) -> list:
        r = self._request("GET", "/equity/portfolio")
        return self._parse_json(r)

    def get_position(self, ticker: str) -> Optional[dict]:
        """Return the open position dict for the given T212 ticker, or None."""
        for pos in (self.get_positions() or []):
            if pos.get("ticker") == ticker:
                return pos
        return None

    def get_instruments(self) -> list:
        """Return the full instruments list, refreshed at most once per hour."""
        global _instruments_cache
        cached = _instruments_cache.get(self.base_url)
        if cached:
            fetched_at, instruments = cached
            if datetime.utcnow() - fetched_at < _INSTRUMENTS_TTL:
                return instruments
        r = self._request("GET", "/equity/metadata/instruments")
        instruments = self._parse_json(r)
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
        # allow_empty=True -- the order was already accepted (HTTP status
        # confirmed by _request() above) by the time we're parsing the
        # body; a malformed/empty response here must never be reported as
        # the order having failed. See _parse_json()'s own docstring.
        return self._parse_json(r, allow_empty=True)

    def cancel_order(self, order_id: str) -> dict:
        r = self._request("DELETE", f"/equity/orders/{order_id}")
        # allow_empty=True -- T212's DELETE commonly returns 200/204 with
        # no body at all (standard REST convention). This is the exact
        # call site that produced a real bug (see
        # data/scheduler.py:_cancel_stale_weekend_orders(), which
        # previously treated a genuinely successful cancellation as
        # FAILED purely because this raised on the empty body).
        return self._parse_json(r, allow_empty=True)

    def get_order(self, order_id: str) -> dict:
        """Fetch a single order by ID from the T212 API."""
        r = self._request("GET", f"/equity/orders/{order_id}")
        return self._parse_json(r)

    def get_order_history(self, limit: int = 50) -> list:
        """Return the most recent resolved orders (with real fill data) from T212's history endpoint.

        Unlike get_order()/get_positions() — which only reflect currently-active
        orders/positions and 404/disappear once an order is fully resolved and
        the position (if any) is closed — this endpoint retains the true fill
        price, quantity, and timestamp indefinitely. It's the authoritative
        source for backfilling a fill once GET /equity/orders/{id} 404s and no
        live position remains to estimate a price from (see poll_placed_orders()).
        """
        r = self._request("GET", "/equity/history/orders", params={"limit": limit})
        data = self._parse_json(r)
        return data.get("items", []) if isinstance(data, dict) else (data or [])

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
        is_etf: Optional[bool] = None,
    ) -> float:
        """Return the T212 instrument's own per-share price, converted to GBP.

        T212's public API has no endpoint that returns a live price/quote for
        an instrument that is not currently held (confirmed directly against
        T212's own API docs — /equity/account/summary, /equity/orders*,
        /equity/positions, /equity/metadata/instruments, /equity/metadata/exchanges,
        and /equity/history/* are the complete endpoint set; no quotes/prices
        endpoint exists, and /equity/orders/market only accepts a share
        quantity, never a notional value). get_instruments()'s response
        objects never carry a currentPrice/price field at all — an earlier
        version of this method tried to read one from there on the mistaken
        assumption that endpoint carried live pricing; it never did, for any
        instrument, stock or ETF (confirmed live: AAPL_US_EQ, CAT_US_EQ, and
        I50Dl_EQ's raw records all lack the field entirely).

        Primary path (genuinely live): if we already hold t212_ticker,
        get_positions() DOES return a real live currentPrice for it —
        converted to GBP via the instrument's own currencyCode. Every time
        this path succeeds it also refreshes a persisted last-known-price
        cache (system_metadata) so a LATER re-entry, after this position has
        since been fully exited (and therefore no longer appears in
        get_positions() at all), doesn't fall all the way back to refusing —
        see the cache path below.

        Cache path (persisted last-known real price, up to
        _LAST_PRICE_CACHE_MAX_AGE_DAYS old): covers the gap the primary path
        can't — a ticker that was held (and priced) at some point but is
        fully exited right now. Still a real, T212-confirmed price, just not
        necessarily today's.

        Fallback path (research_ticker's own close from price_data, assumed
        USD): safe ONLY for a direct single-stock mapping (is_etf=False) —
        the T212 instrument and the research ticker are the same underlying
        share, so their prices should track closely. It is NOT safe for an
        ETF/UCITS wrapper (is_etf=True): wrapper providers pick an arbitrary
        NAV-per-share convention independent of any US-listed equivalent —
        confirmed live, e.g. VOO ($681ish) vs its own I50Dl_EQ wrapper
        (£6.26ish), a ~109x difference that has nothing to do with currency
        conversion. For an is_etf=True instrument with no live position and
        no usable cache entry, this returns 0.0 (refuses to guess) rather
        than silently using a proxy price that could be wrong by orders of
        magnitude — callers must treat 0.0 as "skip this trade", not "no
        data, assume something reasonable". This is the expected, permanent
        state for a brand-new ETF mapping that has never once been filled —
        T212's API gives no way to price it in advance; a deliberate,
        manually-verified seed trade is the only way to bootstrap the cache
        for a genuinely new instrument.

        Returns 0.0 if no safe price could be determined.
        """
        gbpusd, eurusd = self._fx_rates()

        # Primary: a genuinely live quote, if T212 already shows us holding it.
        try:
            positions = self.get_positions()
            pos = next(
                (p for p in (positions or []) if p.get("ticker") == t212_ticker), None,
            )
            if pos and pos.get("currentPrice"):
                instruments = self.get_instruments()
                inst = next(
                    (i for i in (instruments or []) if i.get("ticker") == t212_ticker), None,
                )
                currency = (inst or {}).get("currencyCode") or ""
                price_gbp = self._to_gbp(float(pos["currentPrice"]), currency, gbpusd, eurusd)
                if price_gbp and price_gbp > 0:
                    _cache_last_known_price_gbp(t212_ticker, price_gbp)
                    return price_gbp
        except Exception as exc:
            logger.warning(
                "get_instrument_price_gbp: live position price lookup failed for %s: %s",
                t212_ticker, exc,
            )

        cached = _get_cached_price_gbp(t212_ticker)
        if cached is not None:
            logger.info(
                "get_instrument_price_gbp: using cached last-known price for %s "
                "(not currently held) — £%.4f.", t212_ticker, cached,
            )
            return cached

        if is_etf:
            logger.error(
                "get_instrument_price_gbp: no live price for ETF instrument %s "
                "(not currently held, no cached price) — refusing to size "
                "off the research-ticker proxy, which is unsafe for wrapper "
                "instruments (arbitrary NAV-per-share convention). Skipping.",
                t212_ticker,
            )
            return 0.0

        if not research_ticker:
            return 0.0

        logger.warning(
            "Using research ticker price for %s quantity estimation — "
            "T212 has no live quote for an unheld instrument. Safe only because "
            "this is a direct single-stock mapping (is_etf=False).",
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
        is_etf: Optional[bool] = None,
    ) -> float:
        """Calculate share quantity for a given GBP order size.

        Sizes against the T212 instrument's own price (via
        get_instrument_price_gbp), not the research ticker's price — see
        that method's docstring for why the research-ticker fallback it
        falls back to is only safe when is_etf=False. floor_to_whole_share
        defaults to True (whole-share ETF orders); callers placing
        fractional share orders should pass False and apply their own
        precision rounding to the raw quantity.
        """
        price_gbp = self.get_instrument_price_gbp(
            t212_ticker, research_ticker=research_ticker, is_etf=is_etf,
        )
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
# Error-state helpers
# ---------------------------------------------------------------------------

def _set_t212_error(msg: str) -> None:
    try:
        from data.database import set_metadata
        set_metadata(_ERROR_KEY, msg)
    except Exception:
        pass


def _clear_t212_error() -> None:
    try:
        from data.database import clear_metadata
        clear_metadata(_ERROR_KEY)
    except Exception:
        pass


_T212_ERROR_EMAIL_LAST_SENT_KEY = "t212_error_email_last_sent"


def _send_t212_error_email(error_msg: str, config: dict) -> None:
    """Send a T212 order-placement/API error alert.

    Gated two ways (2026-07-31): notifications.email.enabled (checked
    inside _smtp_send() itself, so this and every other current/future
    caller gets it automatically), and a cooldown
    (notifications.email.alert_cooldown_hours, default 8h) tracked here.

    The cooldown is deliberately GLOBAL across all four call sites in this
    module (a setup-time API error in the BUY loop, a failed BUY order, a
    setup-time API error in the EXIT loop, a failed SELL order) rather than
    per-call-site or per-error-message. A persistent underlying issue (a
    rate limit exceeding the retry budget, an auth failure) is one real
    problem, not four independent ones, and is exactly the kind of thing
    likely to surface through more than one of these call sites in the
    same run — a per-call-site cooldown would still let each site send its
    own email every cycle, which does nothing to stop the flood this
    cooldown exists to prevent. The timestamp is only updated on a
    genuinely successful send — a disabled config or a transient SMTP
    failure never "uses up" the cooldown window, so a real alert can still
    go out once email is re-enabled or the SMTP issue clears.
    """
    try:
        from data.database import get_metadata_str, set_metadata

        cooldown_hours = float(config.get("notifications", {}).get("email", {}).get("alert_cooldown_hours", 8))
        last_sent_str = get_metadata_str(_T212_ERROR_EMAIL_LAST_SENT_KEY)
        if last_sent_str:
            try:
                hours_since = (datetime.utcnow() - datetime.fromisoformat(last_sent_str)).total_seconds() / 3600
                if hours_since < cooldown_hours:
                    logger.info(
                        "T212 error email skipped — within %.1fh cooldown (last sent %.1fh ago): %s",
                        cooldown_hours, hours_since, error_msg,
                    )
                    return
            except ValueError:
                pass  # corrupt/unexpected stored value -- treat as no prior send

        from output.briefing import _smtp_send
        subject   = "T212 Invest: Order Placement Error"
        text_body = f"Order placement failed:\n\n{error_msg}"
        html_body = (
            '<p style="color:#dc2626;font-weight:bold">Order placement failed</p>'
            f'<pre style="background:#f1f5f9;padding:12px;border-radius:4px">{error_msg}</pre>'
        )
        sent, _detail = _smtp_send(subject, text_body, html_body, config)
        if sent:
            set_metadata(_T212_ERROR_EMAIL_LAST_SENT_KEY, datetime.utcnow().isoformat())
    except Exception as exc:
        logger.warning("Failed to send T212 error email: %s", exc)


def _log_order(
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
    """Insert one row into invest_orders (best-effort; never raises)."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(sa_text("""
                INSERT INTO invest_orders (
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
        logger.warning("Failed to log T212 order to DB: %s", exc)


def _reconstruct_pnl(config: dict) -> None:
    """FIFO-pair FILLED BUY/SELL rows in invest_orders into pnl.

    This codebase never holds more than one open position per ticker at a
    time, so BUY/SELL pairs never interleave — a plain FIFO pairing per
    ticker is always correct.
    """
    try:
        from collections import deque
        from sqlalchemy import text as sa_text
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from data.database import Pnl, get_engine

        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT id, ticker, t212_ticker, direction, fill_price, filled_quantity,
                       is_etf, momentum_pct, exit_reason, filled_at, currency_code
                FROM invest_orders
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

                    stmt = sqlite_insert(Pnl).values(
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
        logger.warning("P&L reconstruction failed: %s", exc)


def _load_open_order_tickers(days: int = 30) -> set:
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT DISTINCT ticker FROM invest_orders
                WHERE status IN ('PLACED', 'FILLED')
                AND placed_at > datetime('now', :window)
            """), {"window": f"-{days} days"}).mappings().all()
        return {r["ticker"] for r in rows}
    except Exception:
        return set()


def _load_open_position_context() -> dict:
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
                FROM invest_orders
                WHERE direction = 'BUY' AND status = 'FILLED'
                AND id IN (
                    SELECT MAX(id) FROM invest_orders
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
        logger.warning("Could not load open position context: %s", exc)
        return {}


def _has_recent_sell_order(ticker: str, hours: int = 24) -> bool:
    """True if *ticker* has an unresolved SELL, or a resolved one within *hours*.

    Two different conditions block a duplicate, on purpose:
      - status = 'PLACED', regardless of age. A PLACED order is a real
        still-open order at T212 until it resolves to FILLED/CANCELLED/
        REJECTED — T212's demo account has been observed leaving a market
        SELL sitting in PLACED for 60+ hours before poll_placed_orders()
        ever sees it resolve.
      - status IN ('FILLED', 'FAILED', 'REJECTED') AND placed_at within
        `hours`. Once an order has actually resolved, only a short
        cooldown is needed — to stop the hourly job and a same-window EOD
        call from both selling the same position, or a single FAILED
        attempt from retrying every hour instead of at most once per
        `hours`.

        REJECTED was missing here until 2026-08-02 — a real gap, not a
        deliberate omission: a synchronous placement failure sets local
        status FAILED directly (covered), but a sell that T212 initially
        ACCEPTS (status PLACED, gets an order_id) and only later resolves
        to REJECTED via poll_placed_orders() was never in this list at
        all — the unconditional PLACED check no longer matched once it
        resolved, and REJECTED wasn't in the cooldown set either, so
        nothing blocked an immediate retry on the very next exit check
        (as often as hourly). Exactly the shape a genuinely delisted/
        no-market ticker would hit: T212 accepts the order, later rejects
        it (no market to fill into), and the old code would then retry
        every single hour, indefinitely, with nothing recognizing the
        position couldn't actually be sold. See CLAUDE.md's "Live
        position reconciliation" section — REPEATED_SELL_REJECTION is the
        companion fix that stops retrying altogether after several such
        failures, rather than merely slowing the retry rate.
    """
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            row = conn.execute(sa_text("""
                SELECT 1 FROM invest_orders
                WHERE ticker = :ticker AND direction = 'SELL'
                AND (
                    status = 'PLACED'
                    OR (status IN ('FILLED', 'FAILED', 'REJECTED') AND placed_at > datetime('now', :window))
                )
                LIMIT 1
            """), {"ticker": ticker, "window": f"-{hours} hours"}).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("_has_recent_sell_order: check failed for %s: %s", ticker, exc)
        return False


_SELL_REJECTION_GIVE_UP_THRESHOLD = 3


def _count_recent_sell_failures(ticker: str) -> int:
    """Count consecutive FAILED/REJECTED SELL attempts for *ticker* since
    its most recent FILLED BUY (i.e. since the position now open — or
    believed open — was entered). A FILLED SELL since that BUY would mean
    the position already closed successfully, so this only ever counts
    failures that happened while still trying to close the CURRENT
    holding, never carries over from a previous, already-closed cycle.

    Used by maybe_exit_positions() to recognize a position that genuinely
    cannot be sold (no real market — bankruptcy/delisting) rather than
    retrying forever; see _SELL_REJECTION_GIVE_UP_THRESHOLD and
    CLAUDE.md's "Live position reconciliation" section.
    """
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            last_buy = conn.execute(sa_text("""
                SELECT filled_at FROM invest_orders
                WHERE ticker = :ticker AND direction = 'BUY' AND status = 'FILLED'
                ORDER BY filled_at DESC LIMIT 1
            """), {"ticker": ticker}).scalar()
            if not last_buy:
                return 0
            row = conn.execute(sa_text("""
                SELECT COUNT(*) FROM invest_orders
                WHERE ticker = :ticker AND direction = 'SELL'
                AND status IN ('FAILED', 'REJECTED')
                AND placed_at > :last_buy
            """), {"ticker": ticker, "last_buy": last_buy}).scalar()
        return int(row or 0)
    except Exception as exc:
        logger.warning("_count_recent_sell_failures: check failed for %s: %s", ticker, exc)
        return 0


def _record_reconciliation_event(
    *, event_type: str, t212_ticker: str, ticker: Optional[str] = None,
    quantity: Optional[float] = None, estimated_value_gbp: Optional[float] = None,
    note: str = "",
) -> None:
    """Upsert one position_reconciliations row — refreshes note/last_seen_at
    on a recurring discrepancy rather than accumulating duplicates. Never
    raises; matches this module's DB-write contract."""
    try:
        from sqlalchemy import text as sa_text
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from data.database import PositionReconciliation, get_engine
        now = datetime.utcnow()
        stmt = sqlite_insert(PositionReconciliation).values(
            event_type=event_type, ticker=ticker, t212_ticker=t212_ticker,
            quantity=quantity, estimated_value_gbp=estimated_value_gbp,
            note=note, status="OPEN", detected_at=now, last_seen_at=now,
        )
        # Deliberately does NOT include "status" in the update -- a human
        # who has already set a row to ACKNOWLEDGED must not have it
        # silently flipped back to OPEN just because the underlying
        # discrepancy is still detected on the next hourly run (which it
        # normally will be, since nothing about "the broker closed this
        # with no sell from us" resolves itself over time). note/quantity/
        # last_seen_at still refresh either way, so the row stays a live,
        # accurate record even once acknowledged.
        stmt = stmt.on_conflict_do_update(
            index_elements=["event_type", "t212_ticker"],
            set_={"note": note, "quantity": quantity, "last_seen_at": now},
        )
        with get_engine().begin() as conn:
            conn.execute(stmt)
    except Exception as exc:
        logger.warning(
            "_record_reconciliation_event: failed to record %s for %s: %s",
            event_type, t212_ticker, exc,
        )


def _queue_pending_order(
    *,
    ticker: str,
    t212_ticker: str,
    is_etf: bool,
    currency: str,
    decided_price: float,
    position_gbp: float,
    momentum_pct: Optional[float],
    stop_price: Optional[float],
    var_pct: Optional[float],
) -> None:
    """Insert one PENDING row into pending_orders (best-effort; never raises).

    Decide-phase (maybe_place_orders()) writes here instead of calling
    place_market_order() directly — see "Exchange-aware order submission"
    in CLAUDE.md. A separate, exchange-timed job submits this later.
    """
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        engine = get_engine()
        now = datetime.utcnow().isoformat()
        with engine.begin() as conn:
            conn.execute(sa_text("""
                INSERT INTO pending_orders (
                    ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                    position_gbp, momentum_pct, stop_price, var_pct, status, created_at
                ) VALUES (
                    :ticker, :t212_ticker, :is_etf, :currency, :now, :decided_price,
                    :position_gbp, :momentum_pct, :stop_price, :var_pct, 'PENDING', :now
                )
            """), {
                "ticker": ticker, "t212_ticker": t212_ticker, "is_etf": is_etf,
                "currency": currency, "now": now, "decided_price": decided_price,
                "position_gbp": position_gbp, "momentum_pct": momentum_pct,
                "stop_price": stop_price, "var_pct": var_pct,
            })
    except Exception as exc:
        logger.warning("Failed to queue pending order for %s: %s", ticker, exc)


def _has_pending_order(ticker: str) -> bool:
    """True if *ticker* already has an unresolved (PENDING) queued decision —
    dedup guard so a decide-phase run doesn't queue a duplicate entry for a
    ticker whose signal has stayed on across multiple EOD runs while
    submission remains paused/behind (e.g. ETF submission paused — see
    CLAUDE.md)."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            row = conn.execute(sa_text(
                "SELECT 1 FROM pending_orders WHERE ticker = :t AND status = 'PENDING' LIMIT 1"
            ), {"t": ticker}).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("_has_pending_order check failed for %s: %s", ticker, exc)
        return False


def _load_pending_orders(is_etf: bool) -> list:
    """PENDING rows for one asset class, ranked by signal strength — strongest
    trailing-12-month momentum first (2026-08-01, replaces the confirmed-
    arbitrary decided_at-then-config-order priority rule — see CLAUDE.md's
    "Trade prioritization and capacity-allocation engine" section). Priority
    ("is this worth doing") and position size ("can we fit it") are
    deliberately independent questions: this function only ever answers the
    first — the caller (_submit_pending_orders()) walks rows in this order
    and decides fit separately, per candidate, against live capacity.

    Only ever called separately per asset class (ETF submission and stock
    submission are two distinct scheduled jobs, LSE-hours vs NYSE-hours) —
    so ETF and stock candidates are never ranked against each other in one
    list; each job ranks only its own asset class's candidates. This
    mirrors the live submission timing exactly (ETFs always submit before
    stocks, since the ETF job's own 07:00 UTC window is earlier in the day
    than the stock job's 13:30 UTC window), not a priority judgement that
    one asset class matters more — see the backtest engine's identical
    two-pass-per-day structure in analysis/backtest.py:
    simulate_portfolio_compounding() for the same reasoning applied there.

    SQLite's default NULL ordering puts a NULL momentum_pct (should not
    happen for a real queued entry, but defensively) last in a DESC sort —
    an unknown-strength candidate is deprioritised, never crashes or wins
    priority by accident.
    """
    from sqlalchemy import text as sa_text
    from data.database import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(sa_text("""
            SELECT id, ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                   position_gbp, momentum_pct, stop_price, var_pct
            FROM pending_orders
            WHERE status = 'PENDING' AND is_etf = :is_etf
            ORDER BY momentum_pct DESC, decided_at ASC
        """), {"is_etf": is_etf}).mappings().all()
    return [dict(r) for r in rows]


def _resolve_pending(row_id: int, status: str, note: Optional[str] = None) -> None:
    """Mark a pending_orders row resolved (best-effort; never raises)."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.begin() as conn:
            conn.execute(sa_text("""
                UPDATE pending_orders SET status = :status, resolution_note = :note, resolved_at = :now
                WHERE id = :id
            """), {"status": status, "note": note, "now": datetime.utcnow().isoformat(), "id": row_id})
    except Exception as exc:
        logger.warning("Failed to resolve pending_orders row %s: %s", row_id, exc)


def _live_current_price(ticker: str) -> Optional[float]:
    """A genuinely live/current price for *ticker* — used by the
    submission-time price-sanity check.

    Fixed 2026-08-02, replacing the original `_latest_close()`, which read
    `get_price_df()` — the STORED `price_data` table — under the name
    "latest_close". That table is only ever written by `pull_incremental()`,
    which is only ever called from `_eod_job()`, once daily at 21:30 UTC.
    Stock submission runs at 13:30 UTC the *next* day — hours *before* that
    day's own EOD pull — so the old function could only ever return the
    exact same close `decided_price` was already built from the night
    before. The sanity check was comparing a value against itself: it
    could not detect a real intraday price move at 13:30 UTC, at any later
    time the same day, or under any submission-timing change at all,
    since there was no fresher price source feeding it. Found investigating
    whether delaying submission 30-60 minutes would let this check see a
    "calmer, more settled" price — it wouldn't have, regardless of timing,
    because the check was structurally inert to begin with. See CLAUDE.md's
    dedicated section for the full investigation.

    Uses yfinance's `fast_info.last_price` — a real, current quote (subject
    to Yahoo's standard free-tier delay, typically ~15-20 minutes, still a
    genuine intraday read rather than a value that's already up to a full
    day stale) — confirmed live before adopting it: ~0.2-0.4s per ticker,
    no rate-limit errors observed across repeated calls. T212's own API was
    considered and rejected, same as the original function's reasoning:
    get_instruments() carries no price field for any instrument (confirmed
    live — see the 2026-07-31 ETF pricing-bug fix), and get_positions()'s
    live currentPrice only exists for an instrument already held, which a
    not-yet-submitted PENDING order by definition isn't.

    "Fail loud, don't guess" is preserved exactly as before — returns None
    on any failure (network error, no data, non-positive price), which the
    caller already treats as "leave pending, retry next window" rather than
    falling back to a stale value that would silently reproduce the exact
    staleness bug this function exists to fix.
    """
    try:
        import yfinance as yf  # noqa: PLC0415
        price = yf.Ticker(ticker).fast_info.last_price
        if price is None or price <= 0:
            return None
        return float(price)
    except Exception as exc:
        logger.warning("_live_current_price failed for %s: %s", ticker, exc)
        return None


def _ticker_state(ticker: str, config: dict, gspc_close: pd.Series) -> Optional[dict]:
    """Compute today's momentum signal, ATR, membership, and crisis flags for
    one ticker — the live equivalent of one row of
    analysis/backtest.py:simulate_trades()'s walk-forward loop, evaluated
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
    sig_series = signals.compute_signal_series(close)

    latest_dt = close.index[-1]
    is_etf = ticker in universe.ETF_TICKERS
    is_member = is_etf or universe.is_member(ticker, latest_dt.date())

    return {
        "ticker": ticker,
        "is_etf": is_etf,
        "price": float(close.iloc[-1]),
        "atr": float(atr_s.iloc[-1]) if pd.notna(atr_s.iloc[-1]) else 0.0,
        "sig_on": bool(sig_series.iloc[-1]),
        "momentum_pct": float(signals.compute_momentum_series(close).iloc[-1])
                        if pd.notna(signals.compute_momentum_series(close).iloc[-1]) else None,
        "is_member": is_member,
        "crisis_now": crisis.is_crisis_day(gspc_close, latest_dt),
        "entry_blocked": crisis.is_entry_blocked(gspc_close, latest_dt),
        "close_series": close,
    }


def _load_gspc_close() -> pd.Series:
    df = get_price_df("^GSPC", days=8000)
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    return df["close"].astype(float)


# ---------------------------------------------------------------------------
# T212 instrument-ID resolution (2026-08-02) — the live candidate pool is
# now the full, dynamically-refreshed S&P 500 (analysis/universe.py:
# get_live_stock_candidates()), not the old fixed 40-ticker config.yaml
# `instruments:` mapping — see CLAUDE.md's "wire the momentum-only,
# full-S&P-500 selection into live execution" section. That old mapping's
# t212_instrument_id/currency fields were verified manually, once, for 40
# tickers; a genuinely dynamic candidate pool needs this done
# automatically, for any ticker, the first time it ever becomes a live
# candidate — and needs it VERIFIED against T212's real metadata, not
# guessed, since the naming convention doesn't always hold (META resolves
# to FB_US_EQ, RTX to UTX_US_EQ) and a wrong guess has already caused a
# real live bug once (XLF/XLK, see CLAUDE.md's ETF mapping audit section).
# ---------------------------------------------------------------------------

def resolve_instrument_candidate(ticker: str, instruments: list[dict]) -> dict:
    """Pure matching logic — no API/DB access. Given a research *ticker*
    and an already-fetched T212 `get_instruments()` list, return a
    resolution result: {"status": "VERIFIED"|"FAILED",
    "t212_instrument_id": str|None, "currency": str|None, "note": str}.

    T212's own `shortName` field for a US-listed equity instrument
    reliably tracks the company's REAL, current ticker symbol even when
    the underlying instrument ID (the `ticker` field, which T212 never
    renames post-listing) is a legacy/pre-merger name — confirmed against
    both of this project's known real exceptions: META resolves via
    FB_US_EQ (shortName "META"), RTX via UTX_US_EQ (shortName "RTX"). This
    is the single decisive check both steps below rely on — the same
    check that would have caught XLF's/XLK's wrong-instrument ETF
    mappings immediately had it existed then, since it verifies what the
    metadata actually MEANS rather than trusting a naming convention or
    an existing mapping at face value.

    1. Guess `{TICKER}_US_EQ` — the convention that already held for 38 of
       the 40 previously hand-curated tickers. If that exact instrument
       exists AND its shortName equals *ticker*, verified immediately —
       the common case, one lookup.
    2. Otherwise (the guess doesn't exist, OR it exists but its shortName
       does NOT match — a genuinely wrong guess, never silently accepted),
       search every US-listed equity instrument for one whose shortName
       equals *ticker*. Exactly one match → verified via that instrument
       instead. Zero matches → FAILED (not found). More than one match →
       FAILED (ambiguous) — never guess between duplicates.

    Never raises — a malformed instrument dict (missing keys) is treated
    as simply not matching, not as an error the caller must handle.
    """
    ticker_u = ticker.strip().upper()
    guess_id = f"{ticker_u}_US_EQ"

    def _short(inst: dict) -> str:
        return str(inst.get("shortName") or "").strip().upper()

    def _tid(inst: dict) -> str:
        return str(inst.get("ticker") or "")

    guess = next((i for i in instruments if _tid(i) == guess_id), None)
    if guess is not None and _short(guess) == ticker_u:
        return {
            "status": "VERIFIED",
            "t212_instrument_id": guess_id,
            "currency": guess.get("currencyCode") or "USD",
            "note": f"matched {guess_id} via naming convention, shortName confirms",
        }

    candidates = [
        i for i in instruments
        if _tid(i).endswith("_US_EQ") and _short(i) == ticker_u
    ]
    if len(candidates) == 1:
        match = candidates[0]
        return {
            "status": "VERIFIED",
            "t212_instrument_id": _tid(match),
            "currency": match.get("currencyCode") or "USD",
            "note": f"matched {_tid(match)} via shortName search (naming convention did not apply)",
        }
    if len(candidates) == 0:
        return {
            "status": "FAILED",
            "t212_instrument_id": None,
            "currency": None,
            "note": f"no US-listed equity instrument found with shortName == {ticker_u}",
        }
    return {
        "status": "FAILED",
        "t212_instrument_id": None,
        "currency": None,
        "note": (
            f"ambiguous — {len(candidates)} US-listed equity instruments matched "
            f"shortName {ticker_u}: {[_tid(c) for c in candidates]}"
        ),
    }


def _load_instrument_resolution(ticker: str) -> Optional[dict]:
    """Read the cached resolution row for *ticker*, or None if it has
    never been attempted."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine as _get_engine
        with _get_engine().connect() as conn:
            row = conn.execute(sa_text(
                "SELECT status, t212_instrument_id, currency, note "
                "FROM instrument_resolutions WHERE ticker = :t"
            ), {"t": ticker}).mappings().first()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("Could not read instrument_resolutions for %s: %s", ticker, exc)
        return None


def _store_instrument_resolution(
    ticker: str, *, status: str, t212_instrument_id: Optional[str],
    currency: Optional[str], note: Optional[str],
) -> None:
    """Upsert one row into instrument_resolutions (best-effort; never
    raises — the same contract every other DB write in this module
    follows)."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine as _get_engine
        now = datetime.utcnow().isoformat()
        resolved_at = now if status in ("VERIFIED", "FAILED") else None
        with _get_engine().begin() as conn:
            conn.execute(sa_text("""
                INSERT INTO instrument_resolutions
                    (ticker, t212_instrument_id, currency, status, note, attempted_at, resolved_at)
                VALUES (:t, :tid, :ccy, :status, :note, :now, :resolved_at)
                ON CONFLICT(ticker) DO UPDATE SET
                    t212_instrument_id = :tid, currency = :ccy, status = :status,
                    note = :note, attempted_at = :now,
                    resolved_at = COALESCE(:resolved_at, instrument_resolutions.resolved_at)
            """), {
                "t": ticker, "tid": t212_instrument_id, "ccy": currency,
                "status": status, "note": note, "now": now, "resolved_at": resolved_at,
            })
    except Exception as exc:
        logger.warning("Could not store instrument resolution for %s: %s", ticker, exc)


def resolve_and_cache_instrument(ticker: str, client: "Trading212Client") -> Optional[dict]:
    """Return {"t212_instrument_id": str, "currency": str} for *ticker* if
    it is verified safe to trade, or None if it must be excluded.

    Durable, DB-backed cache (instrument_resolutions) — a given ticker's
    real T212 metadata is checked at most once per status transition, not
    on every single decide-phase run:
      - Already VERIFIED → return the cached mapping, no API call.
      - Already FAILED → return None. A genuine mismatch was already
        found and flagged for manual review; never silently re-guessed on
        a later run — same "flag, don't silently retry a bad guess"
        standard as REJECTED_CAPACITY/SKIPPED_PRICE_MOVED.
      - Missing, or previously PENDING (an earlier attempt failed for an
        OPERATIONAL reason — get_instruments() itself errored, not a
        genuine name mismatch) → attempt resolution now. If
        get_instruments() fails again, stays PENDING and this returns
        None (excluded from trading) — NEVER falls back to trading an
        unverified guess just because the API call didn't succeed.
    """
    cached = _load_instrument_resolution(ticker)
    if cached is not None:
        if cached["status"] == "VERIFIED":
            return {"t212_instrument_id": cached["t212_instrument_id"], "currency": cached["currency"]}
        if cached["status"] == "FAILED":
            return None

    try:
        instruments = client.get_instruments()
        if not instruments:
            raise RuntimeError("get_instruments() returned no data")
    except Exception as exc:
        logger.warning(
            "Instrument resolution: could not fetch T212 instruments metadata for %s — %s. "
            "Leaving PENDING, excluded from trading until resolution succeeds.",
            ticker, exc,
        )
        _store_instrument_resolution(
            ticker, status="PENDING", t212_instrument_id=None, currency=None,
            note=f"get_instruments() failed: {exc}",
        )
        return None

    result = resolve_instrument_candidate(ticker, instruments)
    _store_instrument_resolution(
        ticker, status=result["status"],
        t212_instrument_id=result["t212_instrument_id"], currency=result["currency"],
        note=result["note"],
    )
    if result["status"] == "VERIFIED":
        logger.info("Instrument resolution: %s -> %s (%s)", ticker, result["t212_instrument_id"], result["note"])
        return {"t212_instrument_id": result["t212_instrument_id"], "currency": result["currency"]}

    logger.warning(
        "Instrument resolution FAILED for %s — %s. Excluded from trading, flagged for review.",
        ticker, result["note"],
    )
    return None


def _load_all_verified_instrument_resolutions() -> dict[str, dict]:
    """Return {t212_instrument_id: {"ticker": ..., "currency": ...}} for
    every VERIFIED resolution — used by maybe_exit_positions() to match a
    currently-held T212 position back to its research ticker.

    Deliberately NOT filtered to today's live candidate pool
    (analysis.universe.get_live_stock_candidates()): a position can still
    be open for a ticker that has since been removed from the index (or
    otherwise fallen out of today's candidate list) and must still be
    correctly matched and evaluated for exit (is_member() turning False is
    itself one of the exit triggers — INDEX_REMOVAL) — the resolution
    itself, once verified, never expires."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine as _get_engine
        with _get_engine().connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT ticker, t212_instrument_id, currency "
                "FROM instrument_resolutions WHERE status = 'VERIFIED'"
            )).mappings().all()
        return {
            r["t212_instrument_id"]: {"ticker": r["ticker"], "currency": r["currency"]}
            for r in rows if r["t212_instrument_id"]
        }
    except Exception as exc:
        logger.warning("Could not read verified instrument resolutions: %s", exc)
        return {}


def load_all_instrument_resolutions() -> list[dict]:
    """Return every instrument_resolutions row (any status), sorted by
    ticker — for operational/UI visibility (e.g. the Status page's
    instrument-mapping display), not used by any decision-making code
    path. VERIFIED rows show what's safe to trade and how it resolved;
    FAILED rows are exactly what needs manual review (see
    resolve_and_cache_instrument()'s own docstring); PENDING rows show
    what's still waiting on an operational retry."""
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine as _get_engine
        with _get_engine().connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT ticker, t212_instrument_id, currency, status, note, attempted_at, resolved_at "
                "FROM instrument_resolutions ORDER BY ticker"
            )).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Could not read instrument resolutions: %s", exc)
        return []


def get_failed_instrument_resolutions() -> list[dict]:
    """Every FAILED instrument_resolutions row — used by
    data/scheduler.py's post-decide-phase check to alert (see
    output/briefing.py:send_instrument_resolution_failure_alert()) rather
    than let a genuinely excluded, otherwise-tradeable candidate sit
    unnoticed in the table until someone happens to open the Status page."""
    return [r for r in load_all_instrument_resolutions() if r["status"] == "FAILED"]


# ---------------------------------------------------------------------------
# Fill-status polling — called hourly and on Refresh Account
# ---------------------------------------------------------------------------

def _historical_fill(client: "Trading212Client", order_id: str) -> Optional[dict]:
    """Look up *order_id* in T212's order-history endpoint.

    Returns a dict shaped like a live GET /equity/orders/{id} response
    (status / fillPrice / filledQuantity / filledAt), built from the real
    fill record — or None if the order isn't in the most recent page (or the
    history call itself fails), in which case the caller falls back to
    position/instrument-based estimation.
    """
    try:
        items = client.get_order_history(limit=50)
    except Exception as exc:
        logger.debug("poll_placed_orders: order-history lookup failed: %s", exc)
        return None
    for item in items:
        order = item.get("order") or {}
        if str(order.get("id")) != str(order_id):
            continue
        fill = item.get("fill") or {}
        hist_status = (order.get("status") or "").upper()
        qty = fill.get("quantity")
        # T212's history endpoint returns filledAt as "...T...Z" (e.g.
        # "2026-07-06T07:04:54.000Z"). Python 3.11+'s datetime.fromisoformat()
        # parses a trailing "Z" into a timezone-AWARE datetime, while every
        # other filled_at in this DB is naive (datetime.utcnow().isoformat()).
        # _reconstruct_pnl()'s `(exit_dt - entry_dt).days` raises "can't
        # subtract offset-naive and offset-aware datetimes" if the two sides
        # don't match — normalise to the same space-separated, no-"Z"
        # naive-UTC string the rest of the codebase already uses before this
        # ever reaches the DB.
        filled_at_raw = fill.get("filledAt")
        filled_at = (
            filled_at_raw.replace("T", " ").replace("Z", "")
            if filled_at_raw else None
        )
        return {
            "status":         hist_status if hist_status in ("FILLED", "CANCELLED", "REJECTED") else "FILLED",
            "fillPrice":      fill.get("price"),
            "filledQuantity": abs(qty) if qty is not None else None,
            "filledAt":       filled_at,
        }
    return None


def poll_placed_orders(config: dict) -> int:
    """Poll T212 for the terminal status of every PLACED invest_orders row.

    T212 removes a filled market order from GET /equity/orders/{id} once it
    becomes a position — a 404 does not mean cancelled, so _historical_fill()
    is tried first as the authoritative source; falls back to a live
    position lookup, then to the instrument's current quote. Returns the
    count of rows updated.
    """
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
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
                FROM invest_orders
                WHERE status = 'PLACED' AND order_id IS NOT NULL AND order_id != ''
                ORDER BY placed_at DESC
                LIMIT 50
            """)).mappings().all()
            placed = [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("poll_placed_orders: DB read failed — %s", exc)
        return 0

    if not placed:
        return 0

    gbpusd, eurusd = Trading212Client._fx_rates()
    live = t212_cfg.get("live", False)
    try:
        client = Trading212Client(live=live)
    except Exception as exc:
        logger.warning("poll_placed_orders: cannot connect — %s", exc)
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
                    UPDATE invest_orders
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
            logger.warning("poll_placed_orders: DB update failed for order %s — %s", order_id, exc)

        time.sleep(0.2)

    logger.info("poll_placed_orders: %d of %d order(s) updated.", updated, len(placed))
    return updated


# ---------------------------------------------------------------------------
# EXIT — unconditional, called every hour and again before the BUY phase
# ---------------------------------------------------------------------------

def maybe_exit_positions(config: dict) -> None:
    """Close any open position whose exit priority (see module docstring)
    calls for exit today. Runs unconditionally — no run_type gate."""
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return

    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")
    if not api_key or not api_secret:
        return

    _reconstruct_pnl(config)

    # Sourced from every VERIFIED T212 instrument resolution ever recorded
    # (not just today's live candidate pool — see
    # _load_all_verified_instrument_resolutions()'s own docstring for why
    # a formerly-eligible, currently-removed ticker still needs to match
    # correctly here) — replaces the old static config.yaml `instruments:`
    # dict now that the candidate pool is the full, dynamic S&P 500. See
    # CLAUDE.md's "wire the momentum-only, full-S&P-500 selection into
    # live execution" section.
    resolved = _load_all_verified_instrument_resolutions()
    if not resolved:
        return

    reverse_map: dict[str, str] = {t212_id: r["ticker"] for t212_id, r in resolved.items()}
    ccy_map: dict[str, str] = {t212_id: (r["currency"] or "USD") for t212_id, r in resolved.items()}

    live = t212_cfg.get("live", False)
    try:
        client = Trading212Client(live=live)
        positions = client.get_positions()
        positions_by_t212 = {p.get("ticker", ""): p for p in (positions or [])}
    except Exception as exc:
        logger.error("API error during exit check — %s", exc, exc_info=True)
        _set_t212_error(str(exc))
        _send_t212_error_email(str(exc), config)
        return

    if not positions_by_t212:
        return

    logger.info("EXIT phase — checking open positions")

    try:
        gspc_close = _load_gspc_close()
        context = _load_open_position_context()
    except Exception as exc:
        logger.error("DB/price read error during exit check — %s", exc, exc_info=True)
        _set_t212_error(f"read failed: {exc}")
        return

    account_label = "LIVE" if live else "DEMO"

    for t212_ticker, position in positions_by_t212.items():
        ticker = reverse_map.get(t212_ticker)
        if not ticker:
            continue  # not a managed instrument — leave it alone

        current_qty = float(position.get("quantity", 0) or 0)
        if current_qty <= 0:
            continue

        # Per-position exception isolation (2026-07-31) — same fix as
        # maybe_place_orders()'s BUY loop, and more important here: this is
        # the safety-critical exit path, so one position's unhandled
        # exception must never silently stop every other open position from
        # being checked for exit. `return` (a hard order-failure halt) is
        # control flow, not an exception, so it still propagates through
        # this try/except exactly as before.
        try:
            state = _ticker_state(ticker, config, gspc_close)
            if state is None:
                continue

            ctx = context.get(t212_ticker, {})
            entry_date = ctx.get("entry_date")
            stop_price = ctx.get("stop_price")
            max_hold = get_max_holding_days(ticker, config)
            holding_days = (datetime.utcnow() - entry_date).days if entry_date else 0

            # Exit priority — the single shared implementation
            # (analysis/decision.py:evaluate_exit_reason()), also called by
            # every backtest engine in analysis/backtest.py and by
            # output/performance.py's display path. See CLAUDE.md's
            # "Live/backtest decision-logic parity" section for why this is
            # a shared function rather than an independently hand-written
            # copy of the same if/elif chain.
            exit_reason = evaluate_exit_reason(
                is_member=state["is_member"], crisis_now=state["crisis_now"],
                price=state["price"], stop_price=stop_price, sig_on=state["sig_on"],
                holding_days=holding_days, max_holding_days=max_hold,
            )

            if exit_reason is None:
                continue

            if _has_recent_sell_order(ticker):
                logger.info("%s already has a recent SELL order — skipping duplicate.", ticker)
                continue

            # Give up after repeated failures rather than retry forever
            # (2026-08-02) — a genuinely delisted/bankrupt ticker with no
            # real market to sell into will keep failing/getting rejected
            # for as long as that stays true, and the old code had nothing
            # recognizing that: it would retry on every exit check
            # (as often as hourly once REJECTED was added to
            # _has_recent_sell_order()'s cooldown — see that function's
            # docstring — before that fix it retried even more often).
            # Flag it for manual review/write-off instead of hammering
            # T212 with a fresh doomed sell attempt every cycle. Checked
            # before attempting a sell, not after, so the doomed attempt
            # itself is skipped once the threshold is reached.
            recent_failures = _count_recent_sell_failures(ticker)
            if recent_failures >= _SELL_REJECTION_GIVE_UP_THRESHOLD:
                _record_reconciliation_event(
                    event_type="REPEATED_SELL_REJECTION", t212_ticker=t212_ticker,
                    ticker=ticker, quantity=current_qty,
                    note=(
                        f"{recent_failures} consecutive SELL attempts have failed/been "
                        f"rejected for {ticker} ({t212_ticker}) — likely no real market "
                        "to sell into (delisted/bankrupt). Auto-retry stopped; needs "
                        "manual review (write-off, or a fresh attempt once conditions "
                        "change). exit_reason at last check: " + exit_reason
                    ),
                )
                logger.warning(
                    "%s: %d consecutive SELL failures — giving up auto-retry, "
                    "flagged for manual review (position_reconciliations).",
                    ticker, recent_failures,
                )
                continue

            close_qty = abs(current_qty)
            logger.info("Exiting %s (%s) — %s, qty=%.4f", ticker, t212_ticker, exit_reason, close_qty)

            status, order_id, error_msg = "FAILED", None, None
            try:
                response = client.place_market_order(t212_ticker, -close_qty)
                order_id = str(response.get("id", ""))
                status = "PLACED"
                logger.info("SELL %s qty=%.4f id=%s", t212_ticker, close_qty, order_id)
            except Exception as exc:
                error_msg = str(exc)
                logger.error("Sell order failed for %s: %s", ticker, exc)
                _set_t212_error(f"Sell order failed for {ticker}: {error_msg}")
                _send_t212_error_email(error_msg, config)

            _log_order(
                ticker=ticker, t212_ticker=t212_ticker, direction="SELL",
                quantity=close_qty, estimated_price=state["price"], estimated_value_gbp=None,
                status=status, order_id=order_id, is_etf=state["is_etf"],
                account_type=account_label, currency_code=ccy_map.get(t212_ticker),
                exit_reason=exit_reason, error_message=error_msg,
            )

            if status == "FAILED":
                # Per-position isolation (2026-08-02 fix) — this used to
                # `return`, halting the ENTIRE exit phase for every OTHER
                # open position in the same run just because one sell
                # order failed synchronously. That directly contradicted
                # this same function's own stated intent (see the
                # try/except this sits inside: "one position's unhandled
                # exception must never silently stop every other open
                # position from being checked for exit") — a FAILED order
                # status is exactly the kind of single-position problem
                # that principle was meant to cover, and a bankrupt/
                # delisted ticker is precisely the case most likely to
                # trigger it. `continue` to the next position instead.
                logger.warning("Sell order failed for %s — continuing with remaining positions.", ticker)
                continue

            time.sleep(0.5)
        except Exception as position_exc:
            logger.error(
                "Unhandled error checking exit for %s (%s) — skipping this "
                "position and continuing with the rest of the run: %s",
                ticker, t212_ticker, position_exc, exc_info=True,
            )
            continue


# ---------------------------------------------------------------------------
# Live position reconciliation (2026-08-02)
#
# maybe_exit_positions() above only ever iterates T212's REAL, live
# positions list and only ever notices a position closing when THIS
# system placed the SELL that closed it (via _reconstruct_pnl()'s FIFO
# BUY/SELL pairing). At the ~40-hand-curated-ticker scale that gap rarely
# mattered; at the full, dynamically-tracked ~500-ticker S&P 500 scale,
# delistings/acquisitions/corporate actions on a held position are a
# routine, ongoing occurrence, not a rare edge case. Two shapes this
# system cannot notice on its own, found by investigation rather than
# assumed already covered:
#
#   - A cash-out acquisition: the broker converts the position to cash
#     on its own, without ever needing or accepting a SELL order from
#     this system. The position simply stops appearing in
#     client.get_positions() — maybe_exit_positions()'s loop only ever
#     iterates positions T212 currently reports, so it never even looks
#     at a ticker that's already gone, and never records anything.
#   - A stock-swap acquisition: the position converts into a DIFFERENT
#     T212 instrument entirely. maybe_exit_positions()'s reverse_map
#     lookup only recognizes instruments with a VERIFIED
#     instrument_resolutions row — an unmapped instrument (the swap
#     result) hits `if not ticker: continue  # not a managed instrument
#     — leave it alone` and is silently ignored forever.
#
# reconcile_positions_with_broker() closes both gaps by comparing what
# this system's OWN order history implies is open against T212's real,
# live state, flagging (never guessing/fabricating) anything it cannot
# explain from its own order records alone. See CLAUDE.md's "Live
# position reconciliation" section.
# ---------------------------------------------------------------------------

def _locally_believed_open_positions() -> dict[str, dict]:
    """{t212_ticker: {"ticker": ..., "buy_id": ..., "entry_price": ...,
    "quantity": ..., "buy_filled_at": ...}} for every t212_ticker whose
    latest FILLED BUY has no later FILLED SELL — i.e. what THIS system's
    own order history implies is still an open position, independent of
    what T212 currently shows. buy_filled_at is the anchor
    _most_recent_sell_order_since() uses to find any SELL attempt (of any
    status, not just FILLED) tied to the current holding.
    """
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT io.t212_ticker, io.ticker, io.id AS buy_id,
                       io.fill_price AS entry_price, io.filled_quantity AS quantity,
                       io.filled_at AS buy_filled_at
                FROM invest_orders io
                WHERE io.direction = 'BUY' AND io.status = 'FILLED'
                AND io.id = (
                    SELECT MAX(id) FROM invest_orders
                    WHERE direction = 'BUY' AND status = 'FILLED'
                    AND t212_ticker = io.t212_ticker
                )
                AND NOT EXISTS (
                    SELECT 1 FROM invest_orders s
                    WHERE s.t212_ticker = io.t212_ticker
                    AND s.direction = 'SELL' AND s.status = 'FILLED'
                    AND s.filled_at > io.filled_at
                )
            """)).mappings().all()
        return {r["t212_ticker"]: dict(r) for r in rows}
    except Exception as exc:
        logger.warning("_locally_believed_open_positions: query failed: %s", exc)
        return {}


def _most_recent_sell_order_since(t212_ticker: str, since: Optional[str]) -> Optional[dict]:
    """The most recent SELL order (ANY status — PLACED/FILLED/FAILED/
    REJECTED/CANCELLED) for *t212_ticker* placed since *since* (the
    current holding's BUY fill time), or None if this system has no SELL
    attempt on record for it at all.

    This is the direct order-ID check BROKER_CLOSED_NO_SELL uses (2026-08-02)
    to distinguish two genuinely different situations that a position
    simply being absent from T212's live positions list cannot tell apart
    on its own:
      - We placed a SELL (it has a real T212 order_id) that just hasn't
        resolved to FILLED in our own records yet — T212's positions list
        is a real-time read, but poll_placed_orders()'s order-status
        lookup has a documented lag on this account (see "Broker
        integration" above: "T212's demo account has been observed
        leaving a market SELL sitting in PLACED for 60+ hours before
        poll_placed_orders() ever sees it resolve"). The position being
        gone from get_positions() while our own SELL is still PLACED is
        exactly that lag, not a mystery.
      - No SELL order — no order_id — exists anywhere in this system's
        history for the position. There is nothing here that could
        explain the closure; the broker acted without us.
    """
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            row = conn.execute(sa_text("""
                SELECT order_id, status, placed_at FROM invest_orders
                WHERE t212_ticker = :t AND direction = 'SELL'
                AND (:since IS NULL OR placed_at > :since)
                ORDER BY placed_at DESC LIMIT 1
            """), {"t": t212_ticker, "since": since}).mappings().first()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("_most_recent_sell_order_since: query failed for %s: %s", t212_ticker, exc)
        return None


def invalidate_stale_instrument_resolutions(
    live_candidate_tickers: set[str], held_t212_tickers: set[str],
) -> int:
    """Delete any VERIFIED instrument_resolutions row for a ticker that is
    no longer a live candidate (dropped out of get_live_stock_candidates()
    — genuinely removed from the index, not merely absent from today's
    universe fetch) AND has no currently-open position.

    Why this matters: resolve_and_cache_instrument() trusts a VERIFIED row
    forever once cached, never re-checking it. Ticker symbols get reused
    after a delisting/bankruptcy/rename — if a brand-new, unrelated
    company later takes the same symbol and becomes index-eligible again,
    resolve_and_cache_instrument() would return the OLD company's stale
    T212 instrument mapping (and currency) without ever re-verifying,
    since a VERIFIED status short-circuits any fresh lookup. Deleting the
    row here forces a genuine, shortName-verified re-resolution the next
    time that symbol is ever considered a candidate again.

    Must NOT touch a resolution for a ticker with a currently-open
    position — _load_all_verified_instrument_resolutions() deliberately
    reads every VERIFIED row (not just today's candidate pool) specifically
    so a formerly-eligible, now-removed ticker still matches correctly for
    EXIT purposes (that removal is exactly what triggers INDEX_REMOVAL in
    the first place). held_t212_tickers must be checked, not just
    candidate membership, or this would delete the mapping a still-open
    position's own exit check needs on its very next run.

    Returns the number of rows invalidated. Never raises.
    """
    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT ticker, t212_instrument_id FROM instrument_resolutions WHERE status = 'VERIFIED'"
            )).mappings().all()
        to_delete = [
            r["ticker"] for r in rows
            if r["ticker"] not in live_candidate_tickers
            and r["t212_instrument_id"] not in held_t212_tickers
        ]
        if not to_delete:
            return 0
        with eng.begin() as conn:
            for t in to_delete:
                conn.execute(sa_text("DELETE FROM instrument_resolutions WHERE ticker = :t"), {"t": t})
        logger.info(
            "Invalidated %d stale VERIFIED instrument resolution(s) (no longer a "
            "candidate, no open position): %s", len(to_delete), sorted(to_delete),
        )
        return len(to_delete)
    except Exception as exc:
        logger.warning("invalidate_stale_instrument_resolutions: failed: %s", exc)
        return 0


def reconcile_positions_with_broker(config: dict) -> dict:
    """Compare what this system's own order history implies is open
    against T212's real, live positions — flag (never guess) anything
    that doesn't reconcile. Also invalidates stale instrument-resolution
    mappings for tickers that are no longer candidates and no longer held
    (see invalidate_stale_instrument_resolutions()), reusing the same
    live positions read rather than a second T212 API call.

    Never raises — matches this module's "never block the safety-critical
    path" contract; a reconciliation-check failure must not prevent
    maybe_exit_positions()/poll_placed_orders() (its usual neighbours in
    the hourly job) from running. Returns a summary dict for logging/tests.
    """
    summary = {"broker_closed_no_sell": [], "unrecognized_position": [], "invalidated_resolutions": 0}

    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return summary
    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")
    if not api_key or not api_secret:
        return summary

    try:
        live = t212_cfg.get("live", False)
        client = Trading212Client(live=live)
        positions = client.get_positions()
        positions_by_t212 = {p.get("ticker", ""): p for p in (positions or [])}
    except Exception as exc:
        logger.warning("reconcile_positions_with_broker: could not read live positions — %s", exc)
        return summary

    try:
        resolved = _load_all_verified_instrument_resolutions()
    except Exception as exc:
        logger.warning("reconcile_positions_with_broker: could not read instrument resolutions — %s", exc)
        return summary

    believed_open = _locally_believed_open_positions()

    # BROKER_CLOSED_NO_SELL — we believe it's open (no FILLED SELL on
    # record), T212 doesn't show it held. Direct order-ID check (2026-08-02)
    # before flagging: does ANY SELL order — of any status, not just
    # FILLED — exist for this position since its BUY? This is the
    # difference between "T212 acted with no order from us at all" (a
    # genuine broker-side event, no order_id anywhere) and "we placed a
    # SELL, it just hasn't resolved to FILLED locally yet" (a real T212
    # order_id exists, PLACED, awaiting poll_placed_orders() — routine
    # lag, not a mystery). See _most_recent_sell_order_since()'s own
    # docstring for the documented lag this distinguishes against.
    for t212_ticker, info in believed_open.items():
        if t212_ticker in positions_by_t212:
            continue  # genuinely still held — no discrepancy

        recent_sell = _most_recent_sell_order_since(t212_ticker, info.get("buy_filled_at"))

        if recent_sell and recent_sell["status"] == "PLACED":
            # We have a real T212 order_id for this — the position being
            # gone is consistent with that order having actually filled;
            # poll_placed_orders() (which runs earlier in the same hourly
            # cycle) just hasn't caught up to the terminal status yet.
            # Not a discrepancy — never flag this as a broker-side mystery.
            logger.info(
                "%s (%s): believed open with no FILLED SELL, but a PLACED "
                "SELL order_id=%s already exists — likely resolving via "
                "poll_placed_orders(), not a broker-side closure. Skipping.",
                info["ticker"], t212_ticker, recent_sell["order_id"],
            )
            continue

        if recent_sell:
            # A SELL order_id exists but did NOT resolve to FILLED in our
            # records (FAILED/REJECTED/CANCELLED) — genuinely worth
            # flagging, but this is a materially different, more specific
            # situation than "no order at all": either that order actually
            # filled on T212's side despite our recorded status (a
            # reconciliation mismatch worth investigating directly), or a
            # broker-side event coincided with a failed attempt. Named
            # explicitly rather than folded into the generic case.
            note = (
                f"{info['ticker']} ({t212_ticker}): our order history's latest FILLED BUY "
                f"(qty {info.get('quantity')}, entry price {info.get('entry_price')}) has no "
                "matching FILLED SELL, and T212's live positions list does not show it held. "
                f"A SELL order WAS attempted (order_id={recent_sell['order_id']}, recorded "
                f"status={recent_sell['status']}) but did not resolve to FILLED in our records "
                "— verify directly whether that order actually filled on T212's side despite "
                "the recorded status, before assuming this was a broker-side event (e.g. a "
                "cash-out acquisition) unrelated to our own attempt."
            )
        else:
            note = (
                f"{info['ticker']} ({t212_ticker}): our order history's latest FILLED BUY "
                f"(qty {info.get('quantity')}, entry price {info.get('entry_price')}) has no "
                "matching FILLED SELL, but T212's live positions list does not show it held. "
                "No SELL order of ANY status exists anywhere in this system's history for it "
                "— no order_id to explain the closure. Most likely a cash-out acquisition the "
                "broker processed automatically. Exact proceeds are NOT known from any T212 "
                "endpoint this system calls — verify manually against T212's own transaction "
                "history and reconcile pnl by hand if needed."
            )

        _record_reconciliation_event(
            event_type="BROKER_CLOSED_NO_SELL", t212_ticker=t212_ticker,
            ticker=info["ticker"], quantity=info.get("quantity"), note=note,
        )
        summary["broker_closed_no_sell"].append(info["ticker"])
        logger.warning("Position reconciliation: BROKER_CLOSED_NO_SELL for %s — %s", info["ticker"], note)

    # UNRECOGNIZED_POSITION — T212 shows it held, we have no VERIFIED
    # mapping for it at all.
    for t212_ticker, position in positions_by_t212.items():
        if t212_ticker in resolved:
            continue  # recognized — no discrepancy
        qty = float(position.get("quantity", 0) or 0)
        if qty <= 0:
            continue
        note = (
            f"T212 shows {t212_ticker} held (qty {qty}) but this system has no VERIFIED "
            "instrument_resolutions mapping for it. Most likely a stock-swap acquisition "
            "that converted a previously-tracked position into this different, unmapped "
            "instrument — check T212's own transaction history for the corporate action "
            "and, once identified, verify/add the correct research-ticker mapping."
        )
        _record_reconciliation_event(
            event_type="UNRECOGNIZED_POSITION", t212_ticker=t212_ticker,
            quantity=qty, note=note,
        )
        summary["unrecognized_position"].append(t212_ticker)
        logger.warning("Position reconciliation: UNRECOGNIZED_POSITION for %s — %s", t212_ticker, note)

    if summary["broker_closed_no_sell"] or summary["unrecognized_position"]:
        try:
            from output.briefing import send_position_reconciliation_alert
            send_position_reconciliation_alert(summary, config)
        except Exception as exc:
            logger.warning("reconcile_positions_with_broker: alert send failed: %s", exc)

    try:
        candidates = set(universe.get_live_stock_candidates())
        held_t212 = set(positions_by_t212.keys())
        summary["invalidated_resolutions"] = invalidate_stale_instrument_resolutions(candidates, held_t212)
    except Exception as exc:
        logger.warning("reconcile_positions_with_broker: resolution invalidation failed: %s", exc)

    return summary


# ---------------------------------------------------------------------------
# BUY — only on run_type == "full"
# ---------------------------------------------------------------------------

def maybe_place_orders(config: dict, run_type: str = "full") -> None:
    """Exit stale positions, then QUEUE entry decisions for tickers whose
    momentum signal is on, that are currently index-eligible, and that
    aren't inside the crisis entry-block window.

    Signal computation stays here, at EOD, exactly as before (unchanged and
    validated) — but a qualifying entry is written to `pending_orders` as a
    PENDING row instead of being submitted immediately. Two separate,
    exchange-timed jobs (submit_pending_etf_orders() / submit_pending_stock_
    orders(), further below in this module) later read their own asset
    class's PENDING rows and submit them during that instrument's own real
    trading session, after a price-sanity check against the decided-at
    price. See CLAUDE.md's "Exchange-aware order submission" section for
    why: submitting every order at one fixed EOD time landed ETF orders
    (LSE-listed UCITS wrappers) in a dead window hours after that exchange
    closed, and stock orders in an unconfirmed after-hours window.

    This does NOT close the calendar gap between decision and execution — a
    Friday-computed decision still can't submit before Monday's relevant
    open, since neither exchange is open at decide time either. It only
    removes submission-timing *ambiguity*: predictable, session-correct
    submission instead of firing into a closed or uncertain window.

    Exits are unaffected by this split — maybe_exit_positions() already runs
    on an hourly cadence independent of this EOD-only decide phase, and a
    stop-loss/signal-off/crisis exit is safety-critical enough that it
    should not wait for a session-open submission window.
    """
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return

    # Temporary safety switch (2026-07-31) — a real pricing bug
    # (get_instrument_price_gbp() never getting a live T212 quote, silently
    # falling back to an unrelated proxy price) and a structural exception-
    # isolation gap in this loop are under investigation/fix. Exits must
    # keep running (a stop-loss/signal-off/crisis exit is safety-critical),
    # only new entries are paused. Also blocks decide-phase queuing entirely
    # (not just submission) — a global pause should stop new decisions from
    # even being recorded, not just from being acted on later. Remove this
    # flag from config.yaml once the fix is deployed and verified.
    if t212_cfg.get("pause_new_entries", False):
        maybe_exit_positions(config)
        logger.warning("New order placement PAUSED (trading212_invest.pause_new_entries) — exit-only.")
        return

    if run_type != "full":
        maybe_exit_positions(config)
        logger.debug("Skipping BUY-phase decision queuing on %s run (full only).", run_type)
        return

    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")
    if not api_key or not api_secret:
        logger.warning("T212_API_KEY or T212_API_SECRET not set — skipping.")
        _set_t212_error("API credentials not configured (T212_API_KEY / T212_API_SECRET)")
        return

    # Full, dynamically-refreshed S&P 500 stock candidate pool — replaces
    # the old static config.yaml `instruments:` list. See CLAUDE.md's
    # "wire the momentum-only, full-S&P-500 selection into live execution"
    # section.
    candidate_tickers = universe.get_live_stock_candidates()
    if not candidate_tickers:
        logger.info("No live stock candidates found (universe_membership empty/stale) — nothing to do.")
        return

    live = t212_cfg.get("live", False)
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))

    try:
        client = Trading212Client(live=live)
        summary = client.get_account_summary()
        _s_cash = summary.get("cash", {}) if isinstance(summary.get("cash"), dict) else {}
        total_val = float(summary.get("totalValue", 0) or 0)
        free_cash = float(_s_cash.get("availableToTrade", 0) or 0)

        positions = client.get_positions()
        positions_by_t212 = {p.get("ticker", ""): p for p in (positions or [])}
    except Exception as exc:
        logger.error("API error during setup — %s", exc, exc_info=True)
        _set_t212_error(str(exc))
        _send_t212_error_email(str(exc), config)
        return

    # Fetched account state BEFORE the delegated exit call, so a ticker sold
    # during this call's own exit phase still reads as held in
    # positions_by_t212 for the rest of this run rather than immediately
    # re-queued.
    maybe_exit_positions(config)

    try:
        gspc_close = _load_gspc_close()
        open_order_tickers = _load_open_order_tickers()
    except Exception as exc:
        logger.error("Read error — %s", exc, exc_info=True)
        _set_t212_error(f"read failed: {exc}")
        return

    orders_queued = 0

    # No max_total_deployed enforcement in this loop — unlike the old
    # immediate-submission design, queuing a decision commits no cash and
    # opens no position, so there is nothing yet for that cap to protect
    # against. It is enforced for real in _submit_pending_orders(), against
    # the account's live state *at submission time*, which can genuinely
    # differ from right now (an overnight exit can free up room between
    # this EOD decide and the next session's submission).
    for ticker in candidate_tickers:
        t212_ticker: Optional[str] = None
        instrument_currency = "USD"

        # Per-ticker exception isolation (2026-07-31) — see CLAUDE.md's "V2
        # first-cycle deployment cap overshoot" / "Live pricing bug,
        # exception isolation" sections for the incident this originally
        # fixed. `continue` (a ticker cleanly skipped by its own gating
        # logic) is control flow, not an exception, so it still propagates
        # through this try/except exactly as before; only a genuine
        # unhandled exception is caught here.
        try:
            # Asset-class candidacy gate — the single shared implementation
            # (analysis/decision.py:is_asset_class_enabled()), also read by
            # analysis/validation.py:run_full_validation() when it builds
            # the backtest engine's own ticker universe. Checked BEFORE
            # _ticker_state() (no signal computed, no DB price read) so a
            # disabled asset class is genuinely never considered a
            # candidate at all — not merely filtered out afterward. See
            # CLAUDE.md's "Clean, independent ETF/stock trading switches"
            # section.
            if not is_asset_class_enabled(is_etf=ticker in universe.ETF_TICKERS, config=config):
                continue

            state = _ticker_state(ticker, config, gspc_close)
            if state is None:
                continue

            # Entry gate — the single shared implementation
            # (analysis/decision.py:passes_entry_gate()), also called by
            # every backtest engine's open_trade-is-None branch. See
            # CLAUDE.md's "Live/backtest decision-logic parity" section.
            if not passes_entry_gate(
                sig_on=state["sig_on"], is_member=state["is_member"],
                entry_blocked=state["entry_blocked"],
            ):
                continue

            # T212 instrument-ID resolution — deferred until every cheaper
            # check above (candidacy, signal presence, entry gate) has
            # already passed, so a ticker that wouldn't qualify to trade
            # today never burns a resolution attempt. Never trades on an
            # unverified guess — see resolve_and_cache_instrument()'s own
            # docstring for the VERIFIED/FAILED/PENDING cache semantics.
            resolution = resolve_and_cache_instrument(ticker, client)
            if resolution is None:
                logger.debug(
                    "%s: no verified T212 instrument mapping — excluded from trading.", ticker,
                )
                continue
            t212_ticker = resolution["t212_instrument_id"]
            instrument_currency = resolution["currency"] or "USD"

            if t212_ticker in positions_by_t212:
                logger.debug("%s already held — skipping.", ticker)
                continue
            if ticker in open_order_tickers:
                logger.debug("%s has a recent order in DB — skipping.", ticker)
                continue
            if _has_pending_order(ticker):
                logger.debug("%s already has an unresolved pending decision — skipping.", ticker)
                continue

            max_hold = get_max_holding_days(ticker, config)
            entry_price = state["price"]
            atr = state["atr"]
            stop_price, _stop_pct = _resolve_entry_stop("LONG", entry_price, atr, config)

            # VaR position sizing — same formula/inputs the validated backtest
            # itself uses (analysis/volatility.py:_var_position_size_core(),
            # via analysis/backtest.py:simulate_portfolio_compounding() —
            # the same engine analysis/validation.py:run_full_validation()
            # now defaults to, see CLAUDE.md's "Live execution switched to
            # the compounding two-parameter scheme" section), sourced from
            # live account value and live prior-close history instead of a
            # notional backtest constant. The per-trade ceiling is dynamic —
            # per_trade_pct of the account's own CURRENT total value, not a
            # fixed £ figure — so it scales automatically as the account
            # itself compounds.
            close_series = state["close_series"]
            var_confidence = float(t212_cfg.get("var_confidence", 0.95))
            var_lookback = int(t212_cfg.get("var_lookback_days", 252))
            max_risk_pct = float(t212_cfg.get("max_risk_pct", 0.01))
            min_position_gbp = float(t212_cfg.get("min_position_gbp", 50))
            absolute_max_position_gbp = float(t212_cfg.get("absolute_max_position_gbp", 1_000_000))
            # Shared with both compounding backtest engines
            # (analysis/backtest.py) via analysis/decision.py -- see
            # CLAUDE.md's "Mark-to-market unbounded compounding defect"
            # section for why total_val alone (mark-to-market, includes
            # unrealized gains on OTHER open positions) needed a hard,
            # independent ceiling here.
            dynamic_max_position_gbp = resolve_dynamic_max_position_gbp(
                total_val, per_trade_pct, absolute_max_position_gbp,
            )

            prior_closes = close_series.tail(var_lookback + 1)
            log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _dv, holding_var = _var_position_size_core(
                    log_returns, total_val, dynamic_max_position_gbp, max_hold,
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(dynamic_max_position_gbp * 0.5, 2)
                holding_var = None
            # Real, independently-tracked available cash -- never inflated
            # by unrealized gains on other positions the way total_val can
            # be (see analysis/decision.py:resolve_cash_constrained_
            # position_gbp()'s own docstring). This clamp already existed
            # here before the mark-to-market defect was found; now routed
            # through the same shared function the backtest engine also
            # calls, rather than staying an independent inline min().
            position_gbp = resolve_cash_constrained_position_gbp(position_gbp, free_cash)

            if not is_position_size_viable(position_gbp):
                logger.debug("%s: sized position (£%.2f) below minimum viable order — skipping.",
                             ticker, position_gbp)
                continue

            _queue_pending_order(
                ticker=ticker, t212_ticker=t212_ticker, is_etf=state["is_etf"],
                currency=instrument_currency, decided_price=entry_price,
                position_gbp=position_gbp, momentum_pct=state["momentum_pct"],
                stop_price=stop_price, var_pct=holding_var,
            )
            orders_queued += 1
            logger.info(
                "Queued %s entry for %s (%s) — decided price %.2f %s, target £%.0f, "
                "awaiting %s submission window.",
                "ETF" if state["is_etf"] else "stock", ticker, t212_ticker,
                entry_price, instrument_currency, position_gbp,
                "LSE 07:00 UTC" if state["is_etf"] else "NYSE 13:30 UTC",
            )
        except Exception as ticker_exc:
            logger.error(
                "Unhandled error evaluating entry for %s (%s) — skipping this "
                "ticker and continuing with the rest of the run: %s",
                ticker, t212_ticker, ticker_exc, exc_info=True,
            )
            continue

    if orders_queued > 0:
        _clear_t212_error()

    logger.info("Decide phase complete — %d entry decision(s) queued for submission.", orders_queued)


# ---------------------------------------------------------------------------
# Exchange-aware submission — reads pending_orders and actually places the
# order, during each asset class's own real trading session. See
# maybe_place_orders()'s docstring above for the full rationale.
# ---------------------------------------------------------------------------

def _submit_pending_orders(config: dict, *, is_etf: bool) -> int:
    """Submit every still-PENDING decision for one asset class.

    Applies, in order: an enabled/paused gate specific to this asset class
    (etf_submission_enabled / stock_submission_enabled — see CLAUDE.md for
    why ETF submission ships paused), a price-sanity check per row against
    the price recorded at decide time, then a capacity fit check against
    max_total_deployed / free cash. No position-count cap (2026-08-01) —
    see CLAUDE.md's "Live execution switched to the compounding
    two-parameter scheme" section for why max_open_positions was retired.

    Capacity is skip-and-continue, not resize-to-fit (2026-08-01, see
    CLAUDE.md's "Trade prioritization and capacity-allocation engine"
    section): a candidate that doesn't fit remaining capacity — at its
    FULL, VaR-calibrated position_gbp, never shrunk — is marked
    REJECTED_CAPACITY and the loop moves on to the next candidate in
    priority (momentum) order, since a smaller lower-priority trade may
    still fit even where a larger higher-priority one didn't. This
    replaces two things the pre-2026-08-01 version did that both
    contradicted this principle: a hard `break` the instant deployment hit
    its cap (stranding every remaining candidate regardless of size), and
    silently shrinking position_gbp to `free_cash * 0.25` — a real
    resize-to-fit that quietly broke the VaR-calibrated size sizing was
    supposed to guarantee.

    total_val/invested/free_cash are read ONCE, from the account's real,
    live state, at the top of this run — never a cross-job tracked or
    projected total. This is what makes the ETF-then-stock timing question
    resolve itself: the stock job (13:30 UTC) reads T212's actual current
    state, which already reflects whatever the ETF job (07:00 UTC) placed
    earlier that day, the same way VaR sizing already reads live balance
    rather than a stale figure.

    Never raises. Returns the number of orders actually placed.
    """
    label = "ETF" if is_etf else "stock"
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return 0

    # Candidacy master override — the same shared
    # analysis.decision.is_asset_class_enabled() the decide phase
    # (maybe_place_orders()) checks before ever queuing a decision. This is
    # a STRONGER, upstream cut than the submission-only flag_key check
    # immediately below: *_trading_enabled=False means "as if this asset
    # class didn't exist in the universe at all" — including any decision
    # that was already queued under an earlier config (left untouched,
    # neither submitted nor resolved, so nothing is lost if trading is
    # re-enabled later). In steady state this is moot (the decide phase
    # already stops queuing new rows once its asset class is disabled), but
    # it matters for any row queued before the switch was flipped off.
    if not is_asset_class_enabled(is_etf=is_etf, config=config):
        trading_key = "etf_trading_enabled" if is_etf else "stock_trading_enabled"
        logger.info(
            "%s trading disabled entirely (trading212_invest.%s) — skipping submission "
            "(any pending %s decisions are left untouched, not resolved).",
            label, trading_key, label.lower(),
        )
        return 0

    flag_key = "etf_submission_enabled" if is_etf else "stock_submission_enabled"
    # ETF submission defaults OFF (paused) if the key is absent; stock
    # submission defaults ON ("continue as today") — see CLAUDE.md's
    # "Exchange-aware order submission" section for why the two differ.
    default_enabled = not is_etf
    if not t212_cfg.get(flag_key, default_enabled):
        logger.info("%s order submission disabled (trading212_invest.%s) — skipping.", label, flag_key)
        return 0

    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")
    if not api_key or not api_secret:
        logger.warning("T212_API_KEY or T212_API_SECRET not set — skipping %s submission.", label)
        return 0

    try:
        pending = _load_pending_orders(is_etf)
    except Exception as exc:
        logger.error("Could not read pending %s orders — %s", label, exc, exc_info=True)
        return 0

    if not pending:
        logger.debug("No pending %s orders to submit.", label)
        return 0

    threshold_pct = float(t212_cfg.get("price_sanity_threshold_pct", 0.03))
    live = t212_cfg.get("live", False)
    max_deployed_pct  = float(t212_cfg.get("max_total_deployed", 0.5))

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
        logger.error("API error during %s submission setup — %s", label, exc, exc_info=True)
        _set_t212_error(str(exc))
        _send_t212_error_email(str(exc), config)
        return 0

    try:
        open_order_tickers = _load_open_order_tickers()
    except Exception as exc:
        logger.error("Read error during %s submission — %s", label, exc, exc_info=True)
        return 0

    account_label = "LIVE" if live else "DEMO"
    submitted = 0

    for row in pending:
        ticker = row["ticker"]
        t212_ticker = row["t212_ticker"]

        try:
            if t212_ticker in positions_by_t212:
                logger.info("%s (%s) already held at submission time — skipping.", ticker, t212_ticker)
                _resolve_pending(row["id"], "SKIPPED_ALREADY_HELD", note="already held at submission time")
                continue
            if ticker in open_order_tickers:
                logger.info("%s already has a recent order in DB — skipping.", ticker)
                _resolve_pending(row["id"], "SKIPPED_ALREADY_HELD", note="recent order already exists in DB")
                continue

            # Price-sanity check — compare the price at decide time against
            # a genuinely live price now (2026-08-02 fix — see
            # _live_current_price()'s own docstring: the check used to
            # compare against the same stale value decided_price was
            # already built from, structurally unable to detect any real
            # intraday move). Fail loud, don't guess: if the ticker has
            # moved beyond threshold_pct since the decision was made, skip
            # and log clearly rather than execute on a stale decision (same
            # principle as the 2026-07-31 pricing-bug fix — see CLAUDE.md).
            decided_price = row["decided_price"]
            current_price = _live_current_price(ticker)
            if current_price is None or current_price <= 0:
                logger.warning(
                    "%s: no current price available at submission time — leaving pending, will retry.",
                    ticker,
                )
                continue
            pct_move = ((current_price - decided_price) / decided_price) if decided_price else 0.0
            if abs(pct_move) > threshold_pct:
                note = (
                    f"price moved {pct_move:+.2%} since decision "
                    f"(decided {decided_price:.2f} -> now {current_price:.2f}), "
                    f"exceeds {threshold_pct:.0%} sanity threshold"
                )
                logger.warning("Skipping %s (%s) submission — %s", ticker, t212_ticker, note)
                _resolve_pending(row["id"], "SKIPPED_PRICE_MOVED", note=note)
                continue

            # Full, VaR-calibrated position_gbp -- never shrunk to fit
            # whatever capacity happens to remain (2026-08-01; the old
            # `min(position_gbp, free_cash * 0.25)` line this replaces was
            # itself a resize-to-fit, exactly the pattern this design
            # explicitly rejects -- see CLAUDE.md's "Trade prioritization
            # and capacity-allocation engine" section).
            position_gbp = row["position_gbp"] or 0.0
            if not is_position_size_viable(position_gbp):
                logger.warning("%s (%s): degenerate position size (£%.2f) — leaving pending.",
                                ticker, t212_ticker, position_gbp)
                continue

            # Capacity fit check -- skip-and-continue, not resize-to-fit.
            # Checked against this candidate's FULL size; a miss here marks
            # REJECTED_CAPACITY and moves on to the next (lower-priority,
            # momentum-ranked) candidate rather than stopping the run --
            # see this function's own docstring for the full rationale.
            deployed_after_pct = (invested + position_gbp) / total_val if total_val > 0 else 0.0
            if total_val > 0 and deployed_after_pct > max_deployed_pct:
                note = (
                    f"would deploy {deployed_after_pct:.1%} of account (limit {max_deployed_pct:.0%}) -- "
                    f"position £{position_gbp:.2f}, already invested £{invested:.2f} of £{total_val:.2f}"
                )
                logger.info("Skipping %s (%s) — %s", ticker, t212_ticker, note)
                _resolve_pending(row["id"], "REJECTED_CAPACITY", note=note)
                continue
            if position_gbp > free_cash:
                note = f"position £{position_gbp:.2f} exceeds available free cash £{free_cash:.2f}"
                logger.info("Skipping %s (%s) — %s", ticker, t212_ticker, note)
                _resolve_pending(row["id"], "REJECTED_CAPACITY", note=note)
                continue

            quantity = client.get_quantity_for_gbp(
                t212_ticker, position_gbp, research_ticker=ticker,
                floor_to_whole_share=not row["is_etf"], is_etf=row["is_etf"],
            )
            if quantity <= 0:
                if row["is_etf"]:
                    # Structurally unreachable in production -- the ETF sleeve was
                    # removed from live trading entirely, so no is_etf=True row can
                    # ever be queued (see CLAUDE.md's "ETF sleeve removed from live
                    # trading" section). Left as a defensive branch only because
                    # submit_pending_etf_orders() itself is kept as documented dead
                    # code for existing test coverage of the shared logic below.
                    logger.warning(
                        "%s (%s): entry-eligible but no viable price is available "
                        "(not currently held, no cached price) — leaving pending. "
                        "This should never occur in production.",
                        ticker, t212_ticker,
                    )
                else:
                    logger.warning("Could not calculate quantity for %s (%s) — leaving pending.", ticker, t212_ticker)
                continue

            meta = instrument_meta.get(t212_ticker, {})
            min_qty = float(meta.get("minTradeQuantity", 1.0))
            precision = int(meta.get("maxQuantityPrecision", 0) or 0)

            if row["is_etf"]:
                quantity = math.floor(quantity)  # ETFs: whole shares only
            elif precision > 0:
                quantity = round(quantity, precision)  # fractional-capable stock
            else:
                quantity = math.floor(quantity)

            if quantity < min_qty or quantity <= 0:
                logger.warning("%s (%s) qty %.4f below instrument minimum %.4f — leaving pending.",
                                ticker, t212_ticker, quantity, min_qty)
                continue

            price_gbp = client.get_instrument_price_gbp(
                t212_ticker, research_ticker=ticker, is_etf=row["is_etf"],
            )
            est_value_gbp = quantity * price_gbp if price_gbp else position_gbp

            status, order_id, error_msg = "FAILED", None, None
            try:
                response = client.place_market_order(t212_ticker, quantity)
                order_id = str(response.get("id", ""))
                status = "PLACED"
                free_cash -= position_gbp
                invested += est_value_gbp
                submitted += 1
                logger.info("BUY %s (%s) qty=%s approx £%.0f id=%s",
                            ticker, t212_ticker, quantity, position_gbp, order_id)
            except Exception as order_exc:
                error_msg = str(order_exc)
                logger.error("Buy order failed for %s: %s", ticker, order_exc)
                _set_t212_error(f"Buy order failed for {ticker}: {error_msg}")
                _send_t212_error_email(error_msg, config)

            _api_ccy = meta.get("currencyCode")
            _log_order(
                ticker=ticker, t212_ticker=t212_ticker, direction="BUY",
                quantity=quantity, estimated_price=decided_price, estimated_value_gbp=est_value_gbp,
                status=status, order_id=order_id, is_etf=row["is_etf"],
                account_type=account_label, currency_code=_api_ccy or row.get("currency") or "USD",
                momentum_pct=row["momentum_pct"], stop_price=row["stop_price"],
                var_pct=row["var_pct"], error_message=error_msg,
            )
            _resolve_pending(
                row["id"], "SUBMITTED" if status == "PLACED" else "FAILED",
                note=(f"order_id={order_id}" if order_id else error_msg),
            )

            if status == "FAILED":
                logger.warning("Halting %s submission after buy-order failure.", label)
                break

            time.sleep(0.5)
        except Exception as row_exc:
            logger.error(
                "Unhandled error submitting pending order for %s (%s) — skipping this "
                "row and continuing with the rest of the run: %s",
                ticker, t212_ticker, row_exc, exc_info=True,
            )
            continue

    if submitted > 0:
        _clear_t212_error()

    logger.info("%s submission complete — %d order(s) placed, free cash approx £%.0f.",
                label, submitted, free_cash)
    return submitted


def submit_pending_etf_orders(config: dict) -> int:
    """No scheduler job calls this any more (2026-08-02) — see CLAUDE.md's
    "ETF sleeve removed from live trading" section. `instruments:`
    (config.yaml) has no ETF entry left, so the EOD decide phase can never
    queue an is_etf=True pending_orders row for this to submit — in
    production this is permanently a no-op (`_load_pending_orders(True)`
    returns `[]`).

    Kept, rather than deleted outright, as a thin `is_etf=True` entry point
    into the shared `_submit_pending_orders()` machinery — the same
    function `submit_pending_stock_orders()` calls with `is_etf=False` —
    because the existing test suite (tests/test_execution.py,
    tests/test_scheduler.py, tests/test_comprehensive_replay_parity.py)
    exercises `_submit_pending_orders()`'s generic capacity/price-sanity/
    quantity-resolution behaviour through this exact call shape, and
    rewriting that coverage onto the stock-only entry point would be a
    large, purely mechanical change for no behavioural benefit. This is
    dead code in production, not disabled functionality — it is never
    reached by any live code path.
    """
    return _submit_pending_orders(config, is_etf=True)


def submit_pending_stock_orders(config: dict) -> int:
    """13:30 UTC weekdays (NYSE regular open) — submit queued stock entry
    decisions. Deliberately not timed to any after-hours window, since
    after-hours order behaviour on this account is unconfirmed."""
    return _submit_pending_orders(config, is_etf=False)
