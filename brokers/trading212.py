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
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests

from analysis import crisis, signals, universe
from analysis.decision import (
    REENTRY_COOLDOWN_DAYS,
    evaluate_exit_reason,
    is_asset_class_enabled,
    is_position_size_viable,
    is_submission_price_move_blocked,
    passes_entry_gate,
    passes_momentum_floor,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
    resolve_min_momentum_floor,
    resolve_reentry_cooldown_days,
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
# Shared, cross-job request pacing (2026-08-03) — see CLAUDE.md's
# "structural safeguards against future T212 API scheduling collisions"
# section, Part B, for the full investigation this closes.
#
# The 2026-08-03 rate-limit incident (market_hours_check and
# account_state_refresh both registered at the identical cron minute)
# was fixed at the specific-instance level by offsetting the two jobs'
# minutes, and the general CLASS of "two T212-calling scheduler jobs
# land on the same declared cron time" is now caught mechanically by
# tests/test_scheduler_t212_collision.py before it can ship. Neither of
# those closes a DIFFERENT, narrower residual risk: two jobs that do NOT
# share a declared cron time, but happen to overlap in real, live
# execution anyway — one job running long, a 429 retry backing into
# another job's own start — since a static schedule scanner only ever
# checks DECLARED triggers, never actual runtime timing.
#
# This throttle closes that residual gap directly, at the one place
# every real T212 HTTP request in this codebase already funnels through
# regardless of which job or code path made it — Trading212Client._
# request(). It is a genuinely shared, cross-CALLER (not just
# cross-job) pacing floor: every Trading212Client instance, in every
# thread within this process, is paced against the SAME module-level
# _t212_last_request_at, so two jobs racing each other via APScheduler's
# thread pool are naturally serialised to at least
# _T212_MIN_REQUEST_INTERVAL_SECONDS apart, rather than firing their
# first HTTP call simultaneously.
#
# Deliberately a simple minimum-interval pacer, NOT a precise
# token-bucket calibrated to T212's own documented limit — T212's real
# rate limit is confirmed to differ meaningfully by ENDPOINT (50
# requests/minute for market orders, 1 request/2s for stop orders — see
# CLAUDE.md's stop-order investigation), and this project has no
# authoritative, endpoint-by-endpoint number for every read endpoint
# (account summary, positions) this throttle also covers. Building a
# precise token-bucket around an incompletely-known limit would risk
# being confidently wrong in either direction (too loose to help, or
# too strict for routine bulk operations like a stock-submission run
# placing 15-20+ orders in one pass). 150ms is a conservative,
# clearly-justified floor instead: negligible overhead for a single
# job's own normal sequential calls (a 20-call run adds at most ~3s
# total, against a job that already runs for seconds to minutes), while
# still meaningfully serialising two jobs' concurrent bursts rather
# than letting them collide at the exact same instant.
#
# What this does NOT cover, stated plainly rather than overclaimed: it
# is in-process, module-level state — it coordinates every job running
# inside the ONE scheduler process (either the standalone daemon or,
# when that's not running, the Streamlit UI's in-process fallback —
# CLAUDE.md's own heartbeat mechanism already guarantees only one of
# those two is ever active at a time). At the time this shipped, the
# Status page's "Danger Zone" buttons ("Refresh Data + Orders Now" /
# "Order Logic Only") called maybe_place_orders() directly from the
# STREAMLIT UI PROCESS — a genuinely different OS process from the
# scheduler daemon, with no shared Python memory to pace against — so a
# cross-process (e.g. DB-backed) version of this mechanism was
# investigated and explicitly NOT built, on the reasoning that a human
# manually clicking one of those buttons at the exact moment a
# scheduled job was also mid-run was a real, but rare, one-off
# coincidence, not the every-hour-forever structural risk two
# always-firing scheduled jobs sharing a cron minute was. Those buttons
# were removed entirely the same day (see CLAUDE.md's "remove the
# manual 'trigger pipeline now' buttons entirely" section) — main.py no
# longer has ANY code path that calls maybe_place_orders() or
# constructs a Trading212Client, so the cross-process gap this
# paragraph originally justified not closing is now moot by
# construction, not merely accepted risk. Recorded here as historical
# reasoning for why this throttle is in-process-only, not as a
# description of a residual risk that still exists today.
# ---------------------------------------------------------------------------
_T212_MIN_REQUEST_INTERVAL_SECONDS = 0.15
_t212_pacing_lock = threading.Lock()
_t212_last_request_at = 0.0


def _throttle_t212_request() -> None:
    """Blocks the calling thread, if needed, so this call lands at least
    _T212_MIN_REQUEST_INTERVAL_SECONDS after the previous real T212 HTTP
    request made by ANY Trading212Client instance in ANY thread of this
    process. Lock-protected so two threads racing to check/update
    _t212_last_request_at concurrently can't both read a stale value and
    both proceed unthrottled — the exact race this mechanism exists to
    prevent, applied to its own internal bookkeeping too."""
    global _t212_last_request_at
    with _t212_pacing_lock:
        now = time.monotonic()
        elapsed = now - _t212_last_request_at
        if elapsed < _T212_MIN_REQUEST_INTERVAL_SECONDS:
            wait = _T212_MIN_REQUEST_INTERVAL_SECONDS - elapsed
            time.sleep(wait)
            _t212_last_request_at = now + wait
        else:
            _t212_last_request_at = now


# Separate pacing state for yfinance-backed live price fetches
# (_live_current_price(), below) — deliberately its OWN lock/timestamp
# pair, not sharing _t212_pacing_lock/_t212_last_request_at above (2026-08-03,
# built for the exit-check live-pricing fix — see CLAUDE.md's "live pricing
# for the exit check" section). yfinance and T212 are two independent
# external services with their own, unrelated limits: recording "a T212
# call landed 0.1s ago" would not actually protect yfinance from a burst
# of its own calls, and vice versa — conflating the two into one shared
# timestamp would make the throttle less meaningful for BOTH services, not
# more. Same interval as the T212 throttle, for simplicity and because
# it's comfortably below _live_current_price()'s own observed per-call
# latency (~0.2-0.4s, see that function's own docstring) — a normal
# sequential loop over open positions essentially never needs this to
# actually sleep; it exists to bound a genuine burst (e.g. many positions
# whose T212 currentPrice is simultaneously unavailable, all falling
# through to the yfinance tier in quick succession) rather than to slow
# down the common case.
_YFINANCE_MIN_REQUEST_INTERVAL_SECONDS = 0.15
_yfinance_pacing_lock = threading.Lock()
_yfinance_last_request_at = 0.0


def _throttle_yfinance_request() -> None:
    """Same mechanism as _throttle_t212_request() above, applied to
    yfinance calls instead of T212 ones — see the module-level comment
    immediately above _YFINANCE_MIN_REQUEST_INTERVAL_SECONDS for why this
    is a separate instance rather than reusing that one's state."""
    global _yfinance_last_request_at
    with _yfinance_pacing_lock:
        now = time.monotonic()
        elapsed = now - _yfinance_last_request_at
        if elapsed < _YFINANCE_MIN_REQUEST_INTERVAL_SECONDS:
            wait = _YFINANCE_MIN_REQUEST_INTERVAL_SECONDS - elapsed
            time.sleep(wait)
            _yfinance_last_request_at = now + wait
        else:
            _yfinance_last_request_at = now


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

        Every physical HTTP attempt is paced via _throttle_t212_request()
        first (2026-08-03) — a shared, cross-job/cross-thread minimum
        interval enforced regardless of which caller is making the
        request, closing the residual "two jobs don't share a cron
        minute but happen to overlap in real, live execution anyway"
        risk a static schedule scanner can't catch. See that function's
        own docstring and CLAUDE.md's "structural safeguards against
        future T212 API scheduling collisions" section, Part B.
        """
        url     = f"{self.base_url}{path}"
        timeout = kwargs.pop("timeout", 10)
        last_exc: Exception | None = None

        for attempt in range(3):
            try:
                _throttle_t212_request()
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


# ---------------------------------------------------------------------------
# Exit-check positions-fetch status (2026-08-03) — see CLAUDE.md's
# "structural safeguards against future T212 API scheduling collisions"
# section, Part C, for the full investigation this closes.
#
# maybe_exit_positions() can only evaluate exit conditions for open
# positions it actually knows about — if client.get_positions() itself
# fails (a genuine T212-side outage, a network blip, or, before Part
# B's shared pacing throttle above, a rate-limit collision), the WHOLE
# exit check for that hour has to abort before any per-ticker exit
# logic runs at all: there is no per-ticker exception isolation that
# could apply to a fetch that hasn't produced a ticker list yet. This
# is expected, structural behaviour — not itself a bug to "fix" by
# somehow evaluating exits for positions we don't know exist — but it
# means a genuine outage silently skips a full hour of stop-loss/
# signal-off/crisis-exit/max-holding protection, and the ONLY existing
# signal for this (_set_t212_error()'s generic "invest_error" key, plus
# an alert email gated by an 8h cooldown SHARED across every other T212
# error type) is neither distinct to this specific failure mode nor
# even displayed anywhere in the UI today (main.py's Status page only
# ever reads the separate "t212_invest_error" key written by
# _fetch_t212_snapshot() — a pre-existing, documented key duplication,
# see output/briefing.py's own dual-key check — meaning a real
# get_positions() failure during the exit check was, until this fix,
# invisible on the UI entirely and only discoverable from raw logs).
#
# _record_exit_check_fetch_status()/get_exit_check_fetch_status() close
# that gap directly: a dedicated, always-current record of the MOST
# RECENT exit-check positions-fetch outcome (OK or FETCH_FAILED),
# written on every single attempt regardless of the shared email
# cooldown, so a persistent recurring failure is visible on the very
# next check rather than only on the first (whichever alert email the
# cooldown happened to let through). Deliberately additive, not a
# replacement for the existing _set_t212_error()/_send_t212_error_email()
# calls at this same call site — those remain unchanged.
# ---------------------------------------------------------------------------
_EXIT_CHECK_FETCH_STATUS_KEY = "exit_check_fetch_status"


def _record_exit_check_fetch_status(status: str, **details) -> None:
    """Write a definitive, directly-checkable verdict for the MOST
    RECENT exit check's own positions fetch — status is "OK" or
    "FETCH_FAILED". Mirrors data/scheduler.py:_record_eod_completion()'s
    exact shape (one dedicated metadata key, a JSON payload with a
    recorded_at timestamp, never raises). Called on every real fetch
    attempt (success or failure) inside maybe_exit_positions() — never
    gated by the shared T212 error-email cooldown, so this stays an
    accurate, always-current signal even when a recurring failure's own
    alert email is being suppressed by that cooldown."""
    try:
        from data.database import set_metadata
        import json as _json
        payload = {
            "status": status,
            "recorded_at": datetime.utcnow().isoformat(),
            **details,
        }
        set_metadata(_EXIT_CHECK_FETCH_STATUS_KEY, _json.dumps(payload))
    except Exception as exc:
        logger.warning("Could not record exit-check fetch status: %s", exc)


def get_exit_check_fetch_status() -> "dict | None":
    """Read back the most recent _record_exit_check_fetch_status()
    payload, or None if no exit check has ever recorded one since this
    feature shipped. Never raises."""
    try:
        from data.database import get_metadata_str
        import json as _json
        raw = get_metadata_str(_EXIT_CHECK_FETCH_STATUS_KEY)
        return _json.loads(raw) if raw else None
    except Exception:
        return None


_T212_ERROR_EMAIL_LAST_SENT_KEY = "t212_error_email_last_sent"


def _send_t212_error_email(
    error_msg: str,
    config: dict,
    *,
    context: str = "",
    ticker: Optional[str] = None,
    direction: Optional[str] = None,
    next_steps: str = "",
) -> None:
    """Send a T212 order-placement/API error alert.

    Gated two ways (2026-07-31): notifications.email.enabled (checked
    inside _smtp_send() itself, so this and every other current/future
    caller gets it automatically), and a cooldown
    (notifications.email.alert_cooldown_hours, default 8h) tracked here.

    The cooldown is deliberately GLOBAL across all five call sites in this
    module (a setup-time API error in the EXIT loop, a failed SELL order, a
    setup-time API error in the decide-phase BUY loop, a setup-time API
    error in submission, and a failed BUY order) rather than per-call-site
    or per-error-message. A persistent underlying issue (a rate limit
    exceeding the retry budget, an auth failure) is one real problem, not
    five independent ones, and is exactly the kind of thing likely to
    surface through more than one of these call sites in the same run — a
    per-call-site cooldown would still let each site send its own email
    every cycle, which does nothing to stop the flood this cooldown exists
    to prevent. The timestamp is only updated on a genuinely successful
    send — a disabled config or a transient SMTP failure never "uses up"
    the cooldown window, so a real alert can still go out once email is
    re-enabled or the SMTP issue clears.

    context/ticker/direction/next_steps (all optional, 2026-08-03) make the
    alert actionable rather than a bare error string — found necessary
    after a real production alert ("T212 request failed after 3 attempts")
    gave no ticker, no direction, no indication of what phase failed, and
    no indication of current state. `context` names the specific step that
    failed (e.g. "Exit check setup" or "BUY order failed"); `ticker`/
    `direction` identify the specific instrument/side when the failure is
    per-candidate rather than a whole-run setup failure; `next_steps`
    states plainly what happens now — retried automatically next cycle,
    or flagged for manual review — since that is exactly what a real,
    time-pressured reader needs to judge urgency without reading source
    code. All four are optional so every call site can omit what doesn't
    apply (a setup-time failure has no single ticker/direction) without
    the email looking broken.
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

        subject = "T212 Invest: Order Placement Error"
        if ticker and direction:
            subject += f" — {direction} {ticker}"
        elif context:
            subject += f" — {context}"

        text_lines = []
        if context:
            text_lines.append(f"Where: {context}")
        if ticker:
            text_lines.append(f"Ticker: {ticker}")
        if direction:
            text_lines.append(f"Direction: {direction}")
        text_lines += ["", "Underlying T212 error:", error_msg]
        if next_steps:
            text_lines += ["", "What happens next:", next_steps]
        text_body = "\n".join(text_lines)

        def _row(label: str, value: str) -> str:
            return (
                f'<tr><td style="padding:2px 8px 2px 0;color:#64748b;font-weight:bold">{label}</td>'
                f'<td style="padding:2px 0">{value}</td></tr>'
            )

        html_rows = ""
        if context:
            html_rows += _row("Where", context)
        if ticker:
            html_rows += _row("Ticker", ticker)
        if direction:
            html_rows += _row("Direction", direction)
        html_body = (
            '<p style="color:#dc2626;font-weight:bold">Order placement failed</p>'
            + (f'<table>{html_rows}</table>' if html_rows else "")
            + '<p style="margin-top:12px;color:#64748b;font-weight:bold">Underlying T212 error</p>'
            + f'<pre style="background:#f1f5f9;padding:12px;border-radius:4px">{error_msg}</pre>'
            + (
                '<p style="margin-top:12px;color:#64748b;font-weight:bold">What happens next</p>'
                f'<p>{next_steps}</p>'
                if next_steps else ""
            )
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


def _load_open_order_tickers(days: int = REENTRY_COOLDOWN_DAYS) -> set:
    """Every ticker with a PLACED/FILLED order (either direction) within
    the last *days* calendar days -- a new entry into any of these is
    skipped by the decide phase. `days` defaults to the single shared
    REENTRY_COOLDOWN_DAYS fallback constant (analysis/decision.py) for
    direct/test callers, but both real call sites in this module pass it
    explicitly, resolved from config via resolve_reentry_cooldown_days()
    -- see that function's own docstring for why this bulk SQL query and
    the backtest engine's per-ticker Python check
    (analysis/backtest.py:simulate_portfolio_compounding_mtm()) can't
    literally share one function call, but both resolve the same
    config-driven threshold.
    """
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


_DEFAULT_PENDING_ORDERS_RETENTION_DAYS = 30


def purge_old_pending_orders(config: Optional[dict] = None, retention_days: Optional[int] = None) -> int:
    """Genuinely delete RESOLVED pending_orders rows older than the
    retention window — a deliberate, scoped exception to this project's
    "flag, don't delete" default (2026-08-02).

    Why this table is the exception, not a reversal of the principle:
    "flag, don't delete" protects genuinely meaningful HISTORY (a real
    trade, a detected discrepancy, a decision that was actually acted
    on) — pending_orders is structurally different. With a ~500-ticker
    live candidate pool and capacity for roughly 10 positions (confirmed
    2026-08-02: 318 candidates queued, 17 admitted), the large majority
    of every day's rows are routine, high-volume noise that was never
    going to become a real trade — REJECTED_CAPACITY and SUPERSEDED rows
    especially. Left permanent, this table would grow by hundreds of
    rows every single trading day, indefinitely, eventually dwarfing the
    genuinely meaningful trade history in invest_orders — the same
    unbounded-growth/explainability concern that justified fully
    removing the EDGAR market-cap pipeline earlier tonight rather than
    leaving it dormant.

    Scope, precisely: ONLY rows with status != 'PENDING' (i.e. already
    resolved one way or another — SUBMITTED/FAILED/SKIPPED_PRICE_MOVED/
    SKIPPED_ALREADY_HELD/REJECTED_CAPACITY/SUPERSEDED) AND resolved_at
    older than the retention window. A still-PENDING row — a live,
    unresolved decision genuinely awaiting its submission window — is
    NEVER purged, regardless of age; deleting one would silently lose a
    real decision the system still intends to act on, not routine noise.

    30 days is the default retention (configurable via config.yaml's
    trading212_invest.pending_orders_retention_days) — long enough for
    near-term debugging/review of a specific day's admission decisions
    (the exact use case that came up tonight), short enough to keep the
    table's steady-state size bounded to roughly one month of daily
    churn rather than growing across the whole life of the account. At
    ~300 resolved rows/trading day (tonight's real ratio) x ~21 trading
    days/month, steady-state is roughly 6,000-7,000 rows, not the tens
    of thousands per year unbounded retention would accumulate.

    Never raises — a purge failure must not block whatever scheduled job
    calls this. Returns the number of rows actually deleted.
    """
    if config is not None:
        retention_days = retention_days or int(
            config.get("trading212_invest", {}).get(
                "pending_orders_retention_days", _DEFAULT_PENDING_ORDERS_RETENTION_DAYS,
            )
        )
    else:
        retention_days = retention_days or _DEFAULT_PENDING_ORDERS_RETENTION_DAYS

    try:
        from sqlalchemy import text as sa_text
        from data.database import get_engine
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
        eng = get_engine()
        with eng.begin() as conn:
            result = conn.execute(sa_text("""
                DELETE FROM pending_orders
                WHERE status != 'PENDING' AND resolved_at IS NOT NULL AND resolved_at < :cutoff
            """), {"cutoff": cutoff})
            deleted = result.rowcount or 0
        if deleted:
            logger.info(
                "purge_old_pending_orders: deleted %d resolved row(s) older than %dd.",
                deleted, retention_days,
            )
        return deleted
    except Exception as exc:
        logger.warning("purge_old_pending_orders failed: %s", exc)
        return 0


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

    Paced via _throttle_yfinance_request() (2026-08-03, added for the
    exit-check live-pricing fix) — embedded HERE, at the true chokepoint,
    rather than left for each caller to remember, mirroring exactly how
    _throttle_t212_request() lives inside Trading212Client._request()
    rather than at each of ITS call sites. This means every existing
    caller (the crisis check, the submission-time price-sanity check) gets
    the same protection automatically, with no risk of a future new call
    site forgetting to pace itself.
    """
    _throttle_yfinance_request()
    try:
        import yfinance as yf  # noqa: PLC0415
        price = yf.Ticker(ticker).fast_info.last_price
        if price is None or price <= 0:
            return None
        return float(price)
    except Exception as exc:
        logger.warning("_live_current_price failed for %s: %s", ticker, exc)
        return None


def _live_exit_check_price(ticker: str, t212_position: dict, currency: str) -> Optional[float]:
    """A genuinely live price for an ALREADY-HELD position, for the exit
    check's STOP_LOSS comparison (2026-08-03) — replaces the once-daily
    stored close that used to flow into evaluate_exit_reason()'s price=
    parameter here. See CLAUDE.md's "live pricing for the exit check"
    section for the investigation this closes: _ticker_state()'s "price"
    field is the last row of price_data, written once daily by
    pull_incremental() (only ever called from _eod_job(), 21:30 UTC) — the
    hourly market_hours_check_job() was re-evaluating that same unchanged
    value on every firing throughout a trading day, providing no intraday
    responsiveness at all despite being correctly gated to NYSE hours.

    _ticker_state() itself is deliberately left untouched — it is also
    called by maybe_place_orders()'s decide phase, where the stored EOD
    close IS the correct price (entry decisions are made once daily, right
    after that close is captured — there's nothing stale about it there).
    This function only replaces the price fed into evaluate_exit_reason()
    for the EXIT check specifically, leaving entry-decision pricing, and
    every other field _ticker_state() returns (is_etf, sig_on, momentum,
    crisis_now, close_series — all correctly still EOD-based, none of them
    consume a live tick), completely unaffected.

    Two tiers, in order:

    1. T212's own `currentPrice` from get_positions() — the SAME API call
       maybe_exit_positions() already makes for every open position in
       ONE request, so this tier costs zero additional network calls
       regardless of how many positions are open (the real scale concern
       here — 16 positions today, growing). Already trusted elsewhere in
       this module as genuinely live for a currently-held instrument (see
       get_instrument_price_gbp()'s own "Primary path" docstring). Only
       used when `currency` is USD — the only currency this system's
       stop_price/state["price"] values are ever expressed in (the
       research ticker's own close from price_data, always native-currency
       for this system's US-equity-only universe) — a non-USD instrument
       currency would need FX conversion this function deliberately
       doesn't attempt (shouldn't occur in practice for this universe, but
       handled defensively rather than assumed away); it falls through to
       tier 2 instead, which is already in the ticker's own native
       currency by construction.
    2. _live_current_price() (yfinance) — the IDENTICAL mechanism the
       crisis check and the submission-time price-sanity check already
       use, called directly rather than a second, parallel implementation.
       Paced by that function's own embedded throttle (see its docstring)
       so a partial T212 outage across many positions at once can't turn
       into an unpaced burst of yfinance calls.

    "Fail loud, don't guess" — returns None if both tiers fail (missing/
    non-positive T212 currentPrice AND a non-USD currency or a yfinance
    failure). The caller passes this straight through to
    evaluate_exit_reason()'s price= parameter, which already tolerates
    None: STOP_LOSS simply doesn't fire this cycle for that one ticker —
    CRISIS_EXIT/SIGNAL_OFF/MAX_HOLDING are entirely unaffected, since none
    of them consume price at all.
    """
    if currency == "USD":
        cp = t212_position.get("currentPrice")
        if cp is not None:
            try:
                cp = float(cp)
                if cp > 0:
                    return cp
            except (TypeError, ValueError):
                pass
    return _live_current_price(ticker)


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


def is_crisis_mode_active_now() -> Optional[bool]:
    """The single, authoritative "is the crisis override blocking new
    entries right now" check (2026-08-03) — combines the daily-bar
    cooldown-window check (crisis.is_entry_blocked(), which can only ever
    reflect the most recent STORED close) with the genuinely live
    intraday check (crisis.is_intraday_crisis(), a real yfinance quote
    compared against that same stored close) — see analysis/crisis.py:
    is_intraday_crisis()'s own docstring and CLAUDE.md's "Submission-time
    crisis re-check" section for why both are needed together and why
    the daily-bar check alone is not enough.

    Every caller that needs to know "is crisis mode active right now" —
    order submission (_submit_pending_orders(), below) and the
    Dashboard's crisis warning banner (output/performance.py:
    get_crisis_status(), main.py's _render_crisis_banner()) — calls this
    ONE function, not an independently re-derived copy of the same OR
    combination; see CLAUDE.md's "Crisis-mode dashboard banner" section
    for the second call site.

    Returns True (confirmed active), False (confirmed not active), or
    None (could not be determined — a data-read failure, e.g. the DB or
    yfinance is unreachable). None is deliberately distinct from False:
    a caller GATING an action (order submission) must treat None as
    "don't proceed" — the same "fail loud, don't guess" principle
    _live_current_price() already follows for a single ticker's price —
    while a caller only DISPLAYING status (the dashboard banner) can
    reasonably collapse None to "no banner shown," since the real safety
    mechanism lives entirely in the gating caller's own handling of
    None, never in whether a display banner happens to render.
    """
    try:
        gspc_close = _load_gspc_close()
        live_gspc = _live_current_price("^GSPC")
        latest_stored_date = gspc_close.index[-1] if not gspc_close.empty else None
        return bool(
            (latest_stored_date is not None and crisis.is_entry_blocked(gspc_close, latest_stored_date))
            or crisis.is_intraday_crisis(gspc_close, live_gspc)
        )
    except Exception as exc:
        logger.error("is_crisis_mode_active_now(): could not determine crisis status — %s", exc, exc_info=True)
        return None


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
    ticker — for operational/UI visibility (the Instruments page's
    per-ticker Resolution Status column — moved there 2026-08-03 from a
    disconnected expander on the Status page, see CLAUDE.md's
    Status-page-restructuring section), not used by any decision-making
    code path. VERIFIED rows show what's safe to trade and how it resolved;
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
        # Distinct, always-current signal for THIS specific failure mode
        # (2026-08-03) — never gated by _send_t212_error_email()'s own
        # shared cooldown, so a recurring failure stays visible on every
        # check even while that email is being suppressed. See
        # _record_exit_check_fetch_status()'s own docstring.
        _record_exit_check_fetch_status("FETCH_FAILED", error=str(exc))
        _send_t212_error_email(
            str(exc), config,
            context="Exit check setup — could not fetch open positions from T212",
            next_steps=(
                "No positions were evaluated for exit this cycle (stop-loss / "
                "signal-off / crisis-exit / max-holding checks were all skipped "
                "for every open position). This is not a stuck order — it will "
                "retry automatically on the next scheduled check (hourly during "
                "NYSE hours). No manual action needed unless this recurs on "
                "consecutive checks."
            ),
        )
        return

    _record_exit_check_fetch_status("OK")

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

            # Live price for the STOP_LOSS comparison (2026-08-03) — see
            # _live_exit_check_price()'s own docstring for the full
            # investigation and mechanism. state["price"] (the once-daily
            # stored close _ticker_state() returns) is deliberately NOT
            # used here any more — it remains correct and unchanged for
            # maybe_place_orders()'s entry-decision use of _ticker_state().
            # None on failure is a genuine, tolerated outcome, not an
            # error to swallow silently: evaluate_exit_reason() already
            # treats a None price as "STOP_LOSS contributes nothing this
            # check" — CRISIS_EXIT/SIGNAL_OFF/MAX_HOLDING (none of which
            # consume price) are entirely unaffected.
            live_price = _live_exit_check_price(ticker, position, ccy_map.get(t212_ticker, "USD"))
            if live_price is None:
                logger.warning(
                    "%s: no live price available for this exit check — "
                    "STOP_LOSS skipped this cycle (SIGNAL_OFF/MAX_HOLDING/"
                    "CRISIS_EXIT unaffected, will retry live pricing next hour).",
                    ticker,
                )

            # Exit priority — the single shared implementation
            # (analysis/decision.py:evaluate_exit_reason()), also called by
            # every backtest engine in analysis/backtest.py and by
            # output/performance.py's display path. See CLAUDE.md's
            # "Live/backtest decision-logic parity" section for why this is
            # a shared function rather than an independently hand-written
            # copy of the same if/elif chain. Membership (state["is_member"])
            # is deliberately not passed here — it no longer participates in
            # exit decisions (see evaluate_exit_reason()'s own docstring);
            # it still gates NEW entries in maybe_place_orders() below.
            exit_reason = evaluate_exit_reason(
                crisis_now=state["crisis_now"],
                price=live_price, stop_price=stop_price, sig_on=state["sig_on"],
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
                _send_t212_error_email(
                    error_msg, config,
                    context=f"SELL order failed for {ticker} (exit reason: {exit_reason})",
                    ticker=ticker, direction="SELL",
                    next_steps=(
                        f"The position in {ticker} remains open. It will be "
                        f"re-evaluated for exit automatically on the next hourly "
                        f"check — not abandoned. After "
                        f"{_SELL_REJECTION_GIVE_UP_THRESHOLD} consecutive SELL "
                        f"failures for this ticker, auto-retry stops and it is "
                        f"flagged in position_reconciliations for manual review "
                        f"(write-off, or a fresh attempt once conditions change)."
                    ),
                )

            _log_order(
                ticker=ticker, t212_ticker=t212_ticker, direction="SELL",
                # live_price when the fetch succeeded (the price this
                # decision was actually made against) — falls back to the
                # stored close only in the rare case live pricing failed
                # for this ticker this cycle; this is a logged/audit field,
                # not a gating decision, so a stale-but-real value here is
                # strictly better than logging nothing.
                quantity=close_qty,
                estimated_price=(live_price if live_price is not None else state["price"]),
                estimated_value_gbp=None,
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


def get_held_research_tickers() -> set[str]:
    """Every research ticker (not t212_ticker) this system's own order
    history implies is currently held — a thin, public wrapper around
    _locally_believed_open_positions() so other modules (data/ingestion.py's
    price-pull ticker list) can reuse the exact same "what do we believe is
    open" query rather than re-deriving it. Never raises (the underlying
    query already isn't); returns an empty set on any failure."""
    return {v["ticker"] for v in _locally_believed_open_positions().values()}


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
        _send_t212_error_email(
            str(exc), config,
            context="Decide phase setup — could not fetch account state from T212 (EOD candidate queuing)",
            next_steps=(
                "No new BUY candidates were queued this run. This is the "
                "decision-queuing step only — no live order was at risk, and no "
                "existing position was affected. It will retry automatically at "
                "the next scheduled EOD run."
            ),
        )
        return

    # Fetched account state BEFORE the delegated exit call, so a ticker sold
    # during this call's own exit phase still reads as held in
    # positions_by_t212 for the rest of this run rather than immediately
    # re-queued.
    maybe_exit_positions(config)

    try:
        gspc_close = _load_gspc_close()
        open_order_tickers = _load_open_order_tickers(days=resolve_reentry_cooldown_days(config))
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

            # Minimum-momentum floor (2026-08-03) — a genuinely trending
            # candidate must clear a real, config-driven bar, not just
            # momentum > 0.0. Resolved from config the same way as every
            # other tunable parameter (per_trade_pct, the re-entry
            # cooldown) — see analysis/decision.py:resolve_min_momentum_
            # floor()'s own docstring for the investigation this closes.
            if not passes_momentum_floor(state["momentum_pct"], resolve_min_momentum_floor(config)):
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

def _capacity_fit_reason(
    position_gbp: float, invested: float, total_val: float, free_cash: float, max_deployed_pct: float,
) -> Optional[str]:
    """The genuine capacity-fit decision — None if *position_gbp* fits
    within *max_deployed_pct* of the account and available *free_cash*,
    otherwise a human-readable reason it doesn't. Pure, no I/O — extracted
    2026-08-02 so this exact rule has exactly one implementation, shared
    between _submit_pending_orders() (the real, order-placing walk) and
    project_pending_order_admission() (the Dashboard's read-only
    projection of the same walk) — the same "one shared function, not two
    that could independently drift" discipline this project applies
    throughout. Checked in the same order the real submission loop always
    has: deployment-cap first, then free cash.
    """
    deployed_after_pct = (invested + position_gbp) / total_val if total_val > 0 else 0.0
    if total_val > 0 and deployed_after_pct > max_deployed_pct:
        return (
            f"would deploy {deployed_after_pct:.1%} of account (limit {max_deployed_pct:.0%}) -- "
            f"position £{position_gbp:.2f}, already invested £{invested:.2f} of £{total_val:.2f}"
        )
    if position_gbp > free_cash:
        return f"position £{position_gbp:.2f} exceeds available free cash £{free_cash:.2f}"
    return None


def project_pending_order_admission(config: dict) -> list[dict]:
    """Read-only projection of tomorrow's stock submission — reuses the
    EXACT capacity-fit rule (_capacity_fit_reason()) and the exact
    momentum-descending priority order (_load_pending_orders()) the real
    _submit_pending_orders() walk uses, against a fresh, live read of the
    account's real current state. Never places an order, never resolves a
    pending_orders row, never calls the price-sanity/already-held/
    quantity-viability checks _submit_pending_orders() also applies at
    genuine submission time.

    THIS IS A PROJECTION, NOT A GUARANTEE (2026-08-02, built for the
    Dashboard's candidate-queue Status column after the 2026-08-02 urgent
    verification needed to replay this same logic ad hoc to answer "which
    of these 318 will actually trade"). The real admission decision only
    happens at actual submission time, against whatever the account's
    real state is AT THAT MOMENT — which can genuinely differ from right
    now (a position closing, an emergency exit, another candidate's order
    filling first and consuming capacity this projection assumed was
    still free). Every caller must treat the returned "projected_status"
    as exactly that: a projection, current as of this read, not a
    committed outcome.

    Returns one dict per PENDING stock row: {ticker, momentum_pct,
    position_gbp, projected_status}, where projected_status is
    "PROJECTED_ADMIT" or "PROJECTED_REJECT_CAPACITY". Never raises —
    returns [] on any failure (a missing/unreadable account state means
    no projection can honestly be made at all, not a guess).
    """
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return []
    if not (os.environ.get("T212_API_KEY") and os.environ.get("T212_API_SECRET")):
        return []

    try:
        pending = _load_pending_orders(is_etf=False)
    except Exception as exc:
        logger.warning("project_pending_order_admission: could not read pending orders — %s", exc)
        return []
    if not pending:
        return []

    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.5))
    live = t212_cfg.get("live", False)
    try:
        client = Trading212Client(live=live)
        summary = client.get_account_summary()
        _s_cash = summary.get("cash", {}) if isinstance(summary.get("cash"), dict) else {}
        _s_inv  = summary.get("investments", {}) if isinstance(summary.get("investments"), dict) else {}
        total_val = float(summary.get("totalValue", 0) or 0)
        invested  = float(_s_inv.get("currentValue", 0) or 0)
        free_cash = float(_s_cash.get("availableToTrade", 0) or 0)
    except Exception as exc:
        logger.warning("project_pending_order_admission: could not read account state — %s", exc)
        return []

    projected = []
    for row in pending:
        position_gbp = row["position_gbp"] or 0.0
        fit_reason = _capacity_fit_reason(position_gbp, invested, total_val, free_cash, max_deployed_pct)
        if fit_reason is None:
            invested += position_gbp
            free_cash -= position_gbp
            status = "PROJECTED_ADMIT"
        else:
            status = "PROJECTED_REJECT_CAPACITY"
        projected.append({
            "ticker": row["ticker"], "momentum_pct": row["momentum_pct"],
            "position_gbp": position_gbp, "projected_status": status,
        })
    return projected


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

    # Fresh crisis re-check (2026-08-03) -- passes_entry_gate()'s crisis
    # block (analysis/decision.py) is only ever evaluated once, at EOD
    # decide time (21:30 UTC). Stock submission fires up to ~17 hours
    # later, hours before that day's own close is even pulled -- without
    # this, a genuine, severe decline developing overnight or that
    # morning would never stop yesterday's now-stale BUY decisions from
    # still going out, exactly defeating the crisis override's own
    # purpose. is_crisis_mode_active_now() (above) is the single shared
    # implementation -- see its own docstring for why the daily-bar check
    # alone would be a no-op at this point and why a genuinely live quote
    # is what actually closes the gap. Checked once per run (market-wide,
    # not per-ticker) -- blocks or defers every currently-pending entry
    # for this asset class together, not just whichever candidate happens
    # to be evaluated first.
    #
    # "Fail loud, don't guess": a genuinely CONFIRMED block (True) resolves
    # every pending row to a terminal SKIPPED_CRISIS_BLOCKED (tomorrow's
    # EOD decide cycle will naturally re-queue anything still valid once
    # the block clears -- nothing is permanently lost). A check FAILURE
    # (None) is treated as "unknown, don't act" -- rows are left untouched
    # (still PENDING) for the next submission window to retry, mirroring
    # how a single ticker's own missing live price already leaves that
    # row pending rather than assuming it's safe.
    crisis_blocked = is_crisis_mode_active_now()
    if crisis_blocked is None:
        logger.error(
            "Crisis re-check failed during %s submission -- leaving all %d pending "
            "row(s) untouched for retry rather than guessing it's safe to proceed.",
            label, len(pending),
        )
        return 0

    if crisis_blocked:
        logger.warning(
            "%s submission blocked -- crisis override active at submission time (S&P "
            ">= 7%% single-day decline, live or within the daily-bar cooldown window) "
            "-- skipping all %d pending %s entr%s this run.",
            label, len(pending), label.lower(), "y" if len(pending) == 1 else "ies",
        )
        for row in pending:
            _resolve_pending(
                row["id"], "SKIPPED_CRISIS_BLOCKED",
                note="crisis override active at submission time -- re-evaluated fresh "
                     "against live data, not inherited from decide time",
            )
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
        _send_t212_error_email(
            str(exc), config,
            context=f"{label} order submission setup — could not fetch account state from T212",
            next_steps=(
                f"None of the {len(pending)} pending {label} candidate(s) queued "
                f"for today were submitted. They remain PENDING and will be "
                f"retried at the next scheduled {label} submission window."
            ),
        )
        return 0

    try:
        open_order_tickers = _load_open_order_tickers(days=resolve_reentry_cooldown_days(config))
    except Exception as exc:
        logger.error("Read error during %s submission — %s", label, exc, exc_info=True)
        return 0

    account_label = "LIVE" if live else "DEMO"
    submitted = 0

    for idx, row in enumerate(pending):
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
            # intraday move). ASYMMETRIC (2026-08-03) — blocks only on a
            # DROP past threshold_pct since the decision, never on a rise
            # of any size (a rise confirms the momentum thesis rather than
            # invalidating it — see analysis.decision.
            # is_submission_price_move_blocked()'s own docstring and
            # CLAUDE.md's "Price-sanity threshold — momentum-skew
            # investigation" section for the 176-run block-based evidence
            # this is based on). Fail loud, don't guess: if the ticker has
            # genuinely dropped beyond threshold_pct since the decision was
            # made, skip and log clearly rather than execute on a stale
            # decision (same principle as the 2026-07-31 pricing-bug fix —
            # see CLAUDE.md).
            decided_price = row["decided_price"]
            current_price = _live_current_price(ticker)
            if current_price is None or current_price <= 0:
                logger.warning(
                    "%s: no current price available at submission time — leaving pending, will retry.",
                    ticker,
                )
                continue
            pct_move = ((current_price - decided_price) / decided_price) if decided_price else 0.0
            if is_submission_price_move_blocked(decided_price, current_price, threshold_pct):
                note = (
                    f"price dropped {pct_move:+.2%} since decision "
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
            # Shared with project_pending_order_admission() (the Dashboard's
            # read-only projection) via _capacity_fit_reason() below, so the
            # two can never independently drift on what "fits" means.
            fit_reason = _capacity_fit_reason(position_gbp, invested, total_val, free_cash, max_deployed_pct)
            if fit_reason is not None:
                logger.info("Skipping %s (%s) — %s", ticker, t212_ticker, fit_reason)
                _resolve_pending(row["id"], "REJECTED_CAPACITY", note=fit_reason)
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
                _remaining = len(pending) - idx - 1
                _send_t212_error_email(
                    error_msg, config,
                    context=f"BUY order failed for {ticker} ({label})",
                    ticker=ticker, direction="BUY",
                    next_steps=(
                        f"{label} submission has been halted for this run — "
                        f"{_remaining} other pending candidate(s) in today's queue "
                        f"were not attempted and remain PENDING; they will be "
                        f"retried at the next scheduled submission window. "
                        f"{ticker} itself is now marked FAILED and will NOT be "
                        f"automatically retried as-is — it needs a fresh signal "
                        f"on a future run to be re-queued."
                    ),
                )

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
