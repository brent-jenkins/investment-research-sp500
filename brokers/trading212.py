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
  - max_total_deployed / max_open_positions are enforced per-iteration
    inside the BUY loop (not just once before it starts) via running
    current_open/invested totals updated after each successful BUY — a
    cold start (0 pre-existing positions/deployment) would otherwise be
    able to walk through every qualifying ticker in one pass before either
    cap ever binds. See CLAUDE.md's "V2 first-cycle deployment cap
    overshoot" section for the incident this fixed.

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


def get_cached_price_entry(t212_ticker: str) -> Optional[dict]:
    """Return the raw cache entry for t212_ticker (price_gbp, as_of, source),
    or None if absent/expired/corrupt — used by the pricing-path audit
    (which ETFs have no live position and no viable cache entry) and by the
    Status page to show a seeded price's provenance. Deliberately separate
    from _get_cached_price_gbp(), which only ever returns the bare float
    get_instrument_price_gbp() needs — callers that want the "source" field
    (e.g. to distinguish a manual seed from a price cached off a real fill)
    need the full entry, not just the number."""
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
        if not (float(data.get("price_gbp") or 0) > 0):
            return None
        return data
    except Exception:
        return None


def seed_instrument_price_gbp(
    t212_ticker: str, price: float, currency: str,
) -> float:
    """Manually bootstrap the last-known-price cache for a T212 instrument
    that has no live position and no existing cache entry, from a price the
    operator has read directly off T212's own displayed price for that
    instrument — never from get_price_df() or any other automated proxy.

    This exists because get_instrument_price_gbp()'s three-tier pricing
    (live position -> cache -> research-ticker fallback for stocks only)
    correctly refuses to price an unheld ETF off the research-ticker
    fallback (that fallback assumes the T212 wrapper and the US-listed
    research ticker trade at comparable per-share prices, which is false
    for a UCITS/ETF wrapper — see get_instrument_price_gbp()'s own
    docstring, and the XLF mis-sizing incident that fallback originally
    caused). The one-time cost of that correctness is that a brand-new ETF
    mapping with no live position and no cache entry has NO automated path
    to a first price at all — T212's API has no quote endpoint for an
    unheld instrument (confirmed directly; see get_instrument_price_gbp()).
    A deliberate, manually-verified seed is the only way to bootstrap it.

    *price* is in *currency* — the instrument's own native currency exactly
    as T212 displays it (matching the top-level `instruments:` section's
    own `currency` field) — converted to GBP here via the same Trading212Client._to_gbp()
    every other price in this module uses, then written into the identical
    last_known_price_gbp:{t212_ticker} cache key get_instrument_price_gbp()'s
    cache tier already reads. This means a freshly-seeded ETF is priced
    exactly like any other cached ETF with no special-casing anywhere else
    in the pricing chain — and the moment it's actually bought and held,
    the next live-position price read (tier 1, inside
    get_instrument_price_gbp()) overwrites this entry the same way it would
    overwrite any other cached value. A manual seed is a one-time
    bootstrap, never a standing override.

    Raises ValueError for a non-positive price or a currency _to_gbp()
    can't convert — a seed is a rare, manual, operator-initiated action, so
    failing loudly on bad input is preferable to silently caching a wrong
    value that would then look exactly as trustworthy as a genuine one.
    """
    if price is None or price <= 0:
        raise ValueError(f"seed price must be positive, got {price!r}")

    gbpusd, eurusd = Trading212Client._fx_rates()
    price_gbp = Trading212Client._to_gbp(price, currency, gbpusd, eurusd)
    if not price_gbp or price_gbp <= 0:
        raise ValueError(
            f"could not convert seed price {price} {currency} to GBP for {t212_ticker} "
            f"(unrecognised currency code?)"
        )

    now_iso = datetime.utcnow().isoformat()
    from data.database import set_metadata
    import json as _json
    set_metadata(
        f"last_known_price_gbp:{t212_ticker}",
        _json.dumps({
            "price_gbp": price_gbp,
            "as_of": now_iso,
            "source": "manual_seed",
        }),
    )

    logger.warning(
        "MANUAL PRICE SEED: %s seeded at %.4f %s (= £%.4f GBP) at %s — "
        "operator-verified against T212's own displayed price, not derived "
        "from any automated source.",
        t212_ticker, price, currency, price_gbp, now_iso,
    )
    return price_gbp


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
        """Return the full instruments list, refreshed at most once per hour."""
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
        data = r.json()
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
# ETF pricing-path audit
# ---------------------------------------------------------------------------

def audit_etf_pricing_paths(config: dict, client: Optional["Trading212Client"] = None) -> list[dict]:
    """Report, per ETF in the top-level `instruments:` section (config.yaml,
    consolidated 2026-07-31 from watchlist.trading + trading212_invest.
    instrument_mapping — see CLAUDE.md's "Unified instrument structure"
    section), whether it currently has a viable pricing path: held (live
    position -> tier 1), cached (a prior live-position read or a manual
    seed still within _LAST_PRICE_CACHE_MAX_AGE_DAYS -> tier 2), or neither
    -> "locked out" (get_instrument_price_gbp() would return 0.0 and refuse
    to size a new entry, per that method's is_etf=True refusal — see its
    docstring).

    Read-only — never places an order, never writes anything. *client* lets
    a caller reuse an already-constructed Trading212Client (e.g. the Status
    page, which already has one); a fresh one is created if omitted.
    """
    t212_cfg = config.get("trading212_invest", {})
    mapping = config.get("instruments", {})
    etf_entries = {
        rt: raw for rt, raw in mapping.items()
        if isinstance(raw, dict) and raw.get("is_etf")
    }

    if client is None:
        client = Trading212Client(live=t212_cfg.get("live", False))

    try:
        positions = client.get_positions() or []
    except Exception as exc:
        logger.warning("audit_etf_pricing_paths: could not fetch live positions: %s", exc)
        positions = []
    held_t212_tickers = {p.get("ticker") for p in positions}

    results = []
    for rt, raw in etf_entries.items():
        t212_ticker = raw.get("t212_instrument_id")
        held = t212_ticker in held_t212_tickers
        cache_entry = None if held else get_cached_price_entry(t212_ticker)
        locked_out = not held and cache_entry is None
        results.append({
            "ticker": rt,
            "t212_ticker": t212_ticker,
            "held": held,
            "cached": cache_entry is not None,
            "cache_source": (cache_entry or {}).get("source") if cache_entry else None,
            "locked_out": locked_out,
        })
    return results


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
      - status IN ('FILLED', 'FAILED') AND placed_at within `hours`. Once
        an order has actually resolved, only a short cooldown is needed —
        to stop the hourly job and a same-window EOD call from both
        selling the same position, or a single FAILED attempt from
        retrying every hour instead of at most once per `hours`.
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
                    OR (status IN ('FILLED', 'FAILED') AND placed_at > datetime('now', :window))
                )
                LIMIT 1
            """), {"ticker": ticker, "window": f"-{hours} hours"}).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("_has_recent_sell_order: check failed for %s: %s", ticker, exc)
        return False


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
    """PENDING rows for one asset class, oldest decision first."""
    from sqlalchemy import text as sa_text
    from data.database import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(sa_text("""
            SELECT id, ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                   position_gbp, momentum_pct, stop_price, var_pct
            FROM pending_orders
            WHERE status = 'PENDING' AND is_etf = :is_etf
            ORDER BY decided_at ASC
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


def _latest_close(ticker: str) -> Optional[float]:
    """Most recently stored close for *ticker* — used by the submission-time
    price-sanity check. Deliberately reuses the same price_data close series
    the momentum signal itself is built from (not a T212 quote): T212 has no
    live quote for an unheld UCITS ETF at all (see get_instrument_price_gbp's
    docstring), and the UCITS wrapper's own price is a different, unrelated
    number from the underlying US ticker's price anyway (see the 2026-07-31
    ETF pricing-bug fix) — so a T212-side comparison would either be
    unavailable or comparing the wrong thing. The research ticker's own
    close is what decided_price was built from, so it's the correct
    apples-to-apples comparison for "has this moved a lot since the decision
    was made."
    """
    try:
        df = get_price_df(ticker, days=5)
        if df is None or df.empty:
            return None
        return float(df["close"].astype(float).iloc[-1])
    except Exception as exc:
        logger.warning("_latest_close failed for %s: %s", ticker, exc)
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

    mapping: dict = config.get("instruments", {})
    if not mapping:
        return

    reverse_map: dict[str, str] = {}
    ccy_map: dict[str, str] = {}
    for rt, raw in mapping.items():
        t212_id = raw.get("t212_instrument_id") if isinstance(raw, dict) else raw
        reverse_map[t212_id] = rt
        ccy_map[t212_id] = raw.get("currency", "USD") if isinstance(raw, dict) else "USD"

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

            # Exit priority — must match analysis/backtest.py:simulate_trades()
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

            if _has_recent_sell_order(ticker):
                logger.info("%s already has a recent SELL order — skipping duplicate.", ticker)
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
                logger.warning("Halting exit phase after sell-order failure.")
                return

            time.sleep(0.5)
        except Exception as position_exc:
            logger.error(
                "Unhandled error checking exit for %s (%s) — skipping this "
                "position and continuing with the rest of the run: %s",
                ticker, t212_ticker, position_exc, exc_info=True,
            )
            continue


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

    mapping: dict = config.get("instruments", {})
    if not mapping:
        logger.info("No instruments configured — nothing to do.")
        return

    live = t212_cfg.get("live", False)
    max_position_gbp = float(t212_cfg.get("max_position_gbp", 500))

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

    # No max_total_deployed / max_open_positions enforcement in this loop —
    # unlike the old immediate-submission design, queuing a decision commits
    # no cash and opens no position, so there is nothing yet for those caps
    # to protect against. They are enforced for real in
    # _submit_pending_orders(), against the account's live state *at
    # submission time*, which can genuinely differ from right now (an
    # overnight exit can free up room between this EOD decide and the next
    # session's submission).
    for ticker, raw in mapping.items():
        t212_ticker = raw.get("t212_instrument_id") if isinstance(raw, dict) else raw
        instrument_currency = raw.get("currency", "USD") if isinstance(raw, dict) else "USD"

        # Per-ticker exception isolation (2026-07-31) — see CLAUDE.md's "V2
        # first-cycle deployment cap overshoot" / "Live pricing bug,
        # exception isolation" sections for the incident this originally
        # fixed. `continue` (a ticker cleanly skipped by its own gating
        # logic) is control flow, not an exception, so it still propagates
        # through this try/except exactly as before; only a genuine
        # unhandled exception is caught here.
        try:
            state = _ticker_state(ticker, config, gspc_close)
            if state is None:
                continue

            # Entry gate — must match simulate_trades()'s open_trade-is-None
            # branch exactly: sig_on AND is_member AND not blocked_now.
            if not state["sig_on"] or not state["is_member"] or state["entry_blocked"]:
                continue

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
            # itself uses (analysis/volatility.py:_var_position_size_core(), via
            # analysis/backtest.py:simulate_trades()), except sourced from live
            # account value and live prior-close history instead of a notional
            # backtest constant.
            close_series = state["close_series"]
            var_confidence = float(t212_cfg.get("var_confidence", 0.95))
            var_lookback = int(t212_cfg.get("var_lookback_days", 252))
            max_risk_pct = float(t212_cfg.get("max_risk_pct", 0.01))
            min_position_gbp = float(t212_cfg.get("min_position_gbp", 50))

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
    the price recorded at decide time, then the same max_total_deployed /
    max_open_positions / free-cash caps the old immediate-submission design
    enforced — just applied here, against the account's live state at
    submission time, instead of at decide time.

    Never raises. Returns the number of orders actually placed.
    """
    label = "ETF" if is_etf else "stock"
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
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
    max_position_gbp  = float(t212_cfg.get("max_position_gbp", 500))
    max_open          = t212_cfg.get("max_open_positions", 999)

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
    current_open = len(positions_by_t212)
    submitted = 0

    for row in pending:
        # Cap enforcement — same shape as the BUY loop used before this
        # change, just against submission-time account state. A cap binding
        # here stops the run entirely; every row not yet reached stays
        # PENDING and is retried at the next submission window rather than
        # being marked skipped or lost.
        if current_open >= max_open:
            logger.info("%d/%d positions open — max_open_positions reached, stopping %s submission.",
                        current_open, max_open, label)
            break
        if total_val > 0 and (invested / total_val) >= max_deployed_pct:
            logger.info("%.0f%% deployed (limit %.0f%%) — stopping %s submission.",
                        (invested / total_val) * 100, max_deployed_pct * 100, label)
            break

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
            # the latest available price now. Fail loud, don't guess: if the
            # ticker has moved beyond threshold_pct since the decision was
            # made, skip and log clearly rather than execute on a stale
            # decision (same principle as the 2026-07-31 pricing-bug fix —
            # see CLAUDE.md).
            decided_price = row["decided_price"]
            current_price = _latest_close(ticker)
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

            position_gbp = min(row["position_gbp"] or 0.0, free_cash * 0.25)
            if position_gbp < 10:
                logger.warning("Insufficient free cash (£%.2f) — stopping %s submission.", free_cash, label)
                break

            quantity = client.get_quantity_for_gbp(
                t212_ticker, position_gbp, research_ticker=ticker,
                floor_to_whole_share=not row["is_etf"], is_etf=row["is_etf"],
            )
            if quantity <= 0:
                if row["is_etf"]:
                    logger.warning(
                        "%s (%s): entry-eligible but no viable price is available (not "
                        "currently held, no cached price) — leaving pending. Needs a manual "
                        "price seed (Status page -> T212 Invest Account -> ETF pricing-path "
                        "audit -> Seed a manual price) before this ticker can ever submit.",
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
                current_open += 1
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
    """07:00 UTC weekdays (LSE open) — submit queued ETF entry decisions.

    ETF submission ships disabled by default (trading212_invest.
    etf_submission_enabled must be explicitly set true) — see CLAUDE.md's
    "Exchange-aware order submission" section for why this stays paused
    until manually verified.
    """
    return _submit_pending_orders(config, is_etf=True)


def submit_pending_stock_orders(config: dict) -> int:
    """13:30 UTC weekdays (NYSE regular open) — submit queued stock entry
    decisions. Deliberately not timed to any after-hours window, since
    after-hours order behaviour on this account is unconfirmed."""
    return _submit_pending_orders(config, is_etf=False)
