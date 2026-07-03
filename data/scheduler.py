"""APScheduler-based scheduled jobs: three daily runs with per-exchange holiday awareness.

Run schedule (weekdays only, UTC):
  07:00  Pre-market  — pull fresh data for exchanges that just closed (Asian markets),
                       then run analysis
  13:30  US open     — analysis only, no data pull
  21:30  End of day  — full data refresh then analysis (all exchanges); places T212 orders
  00-21,23  Hourly   — open-trade health check; email alert on STOP LOSS / EXIT SIGNAL
                       only fires for trades whose home exchange is currently in session

All three analysis runs execute the complete analysis pipeline:
  run_analysis()       → indicators table
  generate_signals()   → signals table  (also stamps last_analysis_run)
  compute_all_pairs()  → correlations table

Metadata keys written to system_metadata:
  last_full_refresh          — UTC timestamp of the last successful data pull (EOD run or manual)
  last_analysis_run          — UTC timestamp of the last completed analysis (any run type)
  last_run_type              — 'full' or 'analysis_only'
  trade_alert_status:{id}    — last health status for which an alert email was sent per trade
  t212_account_snapshot      — JSON of latest T212 account summary (written by _fetch_t212_snapshot)
  t212_positions_snapshot    — JSON of latest T212 positions list (written by _fetch_t212_snapshot)
  t212_snapshot_fetched_at   — UTC ISO timestamp of last successful T212 fetch
  pending_t212_fetch         — "1" when the UI has requested a refresh; cleared by _dispatch_pending_jobs
"""

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas_market_calendars as mcal
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from analysis.confluence import generate_signals
from analysis.correlations import compute_all_pairs
from analysis.engine import run_analysis
from analysis.macro import fetch_and_store_macro_indicators
from analysis.regime import get_latest_regime_name, get_max_holding_days, is_entry_allowed, resolve_entry_signal
from data.database import clear_metadata, get_metadata, get_metadata_str, set_metadata
from data.ingestion import get_all_tickers, pull_incremental

logger = logging.getLogger(__name__)

RUN_FULL          = "full"
RUN_ANALYSIS_ONLY = "analysis_only"

_scheduler: BackgroundScheduler | None = None

# pandas_market_calendars does not recognise all exchange shorthands used in
# config.yaml.  Map the config name → the registered calendar name.
_CALENDAR_ALIASES: dict[str, str] = {
    "TSE": "JPX",   # Tokyo Stock Exchange → Japan Exchange Group
}

# Tickers that must always be pulled regardless of calendar status.  Includes
# European/Asian indices that close before the 21:30 UTC EOD run, and ^VIX
# which is on NYSE (closed for US federal holidays like Juneteenth).
_ALWAYS_PULL: frozenset[str] = frozenset({
    "^FTSE", "^GDAXI", "^HSI", "^N225", "^STOXX50E", "^VIX",
})

# Exchange types with no pandas_market_calendars mapping that trade continuously
# on weekdays.  The EOD job must not rely solely on _is_exchange_open() for
# these — an unrecognised calendar name could cause them to be skipped.
_ALWAYS_OPEN_EXCHANGES: frozenset[str] = frozenset({"FX", "Crypto"})

def _resolve_calendar(name: str) -> str:
    return _CALENDAR_ALIASES.get(name, name)


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _build_ticker_exchange_map(config: dict) -> dict[str, str]:
    """Return {ticker: exchange_calendar_name} for every watchlist entry."""
    mapping: dict[str, str] = {}
    for assets in config.get("watchlist", {}).values():
        if not isinstance(assets, list):
            continue
        for entry in assets:
            if isinstance(entry, dict) and "ticker" in entry:
                mapping[entry["ticker"]] = entry.get("exchange", "NYSE")
    return mapping


def _is_exchange_open(calendar_name: str, check_date: date) -> bool:
    """Return True if *calendar_name* has a trading session on *check_date*.

    Crypto and FX markets are treated as always open on weekdays.
    Fails open (returns True) on any calendar lookup error so assets are
    never silently skipped due to a misconfigured calendar name.
    """
    if calendar_name in ("Crypto", "FX"):
        return True
    name = _resolve_calendar(calendar_name)
    try:
        cal = mcal.get_calendar(name)
        schedule = cal.schedule(
            start_date=check_date.isoformat(),
            end_date=check_date.isoformat(),
        )
        return not schedule.empty
    except Exception as exc:
        logger.warning(
            "Calendar check failed for %s on %s: %s — assuming open.",
            calendar_name, check_date, exc,
        )
        return True


def _get_calendar_session(
    calendar_name: str, check_date: date
) -> tuple[datetime, datetime] | None:
    """Return (open_utc, close_utc) for the exchange on check_date, or None if closed.

    Crypto and FX are treated as open 24 h — returns None (callers handle the
    special case by treating None as 'always eligible').
    """
    if calendar_name in ("Crypto", "FX"):
        return None  # always open; caller treats None as unconditionally eligible
    name = _resolve_calendar(calendar_name)
    try:
        cal = mcal.get_calendar(name)
        schedule = cal.schedule(
            start_date=check_date.isoformat(),
            end_date=check_date.isoformat(),
        )
        if schedule.empty:
            return None
        row = schedule.iloc[0]
        open_dt  = row["market_open"].to_pydatetime()
        close_dt = row["market_close"].to_pydatetime()
        # Ensure UTC-aware
        if open_dt.tzinfo is None:
            open_dt  = open_dt.replace(tzinfo=timezone.utc)
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=timezone.utc)
        return open_dt, close_dt
    except Exception as exc:
        logger.warning("Session lookup failed for %s on %s: %s", calendar_name, check_date, exc)
        return None


def _is_exchange_in_session(calendar_name: str, now_utc: datetime) -> bool:
    """Return True if *now_utc* falls within the exchange's active trading session.

    Crypto and FX markets are always considered in-session on weekdays.
    Fails open (returns True) on lookup errors.
    """
    if calendar_name in ("Crypto", "FX"):
        return True
    session = _get_calendar_session(calendar_name, now_utc.date())
    if session is None:
        return False  # market closed today
    open_dt, close_dt = session
    return open_dt <= now_utc <= close_dt


def _exchanges_closed_before(
    exchanges: set[str], cutoff_utc: datetime
) -> set[str]:
    """Return exchanges whose session ENDED today before *cutoff_utc*.

    Used by the pre-market (07:00 UTC) run to identify which exchanges just
    closed and therefore have fresh end-of-day data ready to pull.
    Crypto and FX are excluded (they never 'close').
    """
    closed: set[str] = set()
    check_date = cutoff_utc.date()
    for exch in exchanges:
        if exch in ("Crypto", "FX"):
            continue
        session = _get_calendar_session(exch, check_date)
        if session is None:
            continue  # market was closed all day — nothing to pull
        _, close_dt = session
        if close_dt < cutoff_utc:
            closed.add(exch)
    return closed


def _fetch_t212_snapshot(config: dict) -> None:
    """Fetch T212 account summary + positions and persist as JSON in system_metadata.

    Called by the 60-second dispatcher (on-demand UI requests) and by the EOD job
    so the snapshot stays reasonably fresh without manual refresh.  All T212 API
    calls are centralised here — the UI only reads from the DB.
    """
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return
    if not (os.environ.get("T212_API_KEY") and os.environ.get("T212_API_SECRET")):
        return

    live = t212_cfg.get("live", False)
    try:
        from brokers.trading212 import (  # noqa: PLC0415
            Trading212Client,
            poll_placed_orders as _poll,
        )
        client    = Trading212Client(live=live)
        summary   = client.get_account_summary()
        positions = client.get_positions()
        set_metadata("t212_account_snapshot",  json.dumps(summary))
        set_metadata("t212_positions_snapshot", json.dumps(positions or []))
        set_metadata("t212_snapshot_fetched_at", datetime.utcnow().isoformat())
        n_pos = len(positions) if isinstance(positions, list) else 0
        logger.info("T212 account snapshot updated (%d position(s)).", n_pos)
    except Exception as exc:
        logger.error("T212 snapshot fetch failed: %s", exc)
        set_metadata("t212_invest_error", str(exc))
        return

    try:
        _poll(config)
    except Exception as exc:
        logger.warning("T212 order poll failed after snapshot: %s", exc)


def _dispatch_pending_jobs() -> None:
    """60-second job: execute any pending tasks queued by the UI.

    Currently handles a single flag: ``pending_t212_fetch``.  The flag is
    cleared *before* the fetch so a second UI click while a fetch is in
    progress queues another one rather than being silently swallowed.

    Also writes a heartbeat timestamp so the Streamlit UI can detect whether
    the standalone scheduler daemon is running (vs. the in-process scheduler).
    """
    try:
        set_metadata("scheduler_heartbeat", datetime.utcnow().isoformat())
    except Exception:
        pass

    config = _load_config()

    if get_metadata_str("pending_t212_fetch"):
        set_metadata("pending_t212_fetch", "")
        logger.info("Dispatching pending T212 snapshot fetch (UI-requested)…")
        _fetch_t212_snapshot(config)


def _run_analysis_pipeline(
    config: dict,
    all_tickers: list[str],
    run_type: str,
) -> None:
    """Execute the full analysis pipeline and stamp metadata."""
    try:
        run_analysis(tickers=all_tickers, config=config)
    except Exception as exc:
        logger.error("run_analysis failed: %s", exc, exc_info=True)

    try:
        generate_signals(tickers=all_tickers, config=config)
    except Exception as exc:
        logger.error("generate_signals failed: %s", exc, exc_info=True)

    try:
        set_metadata("last_run_type", run_type)
    except Exception as exc:
        logger.warning("Could not stamp last_run_type: %s", exc)

    try:
        from brokers.trading212 import maybe_place_t212_orders  # noqa: PLC0415
        maybe_place_t212_orders(config, run_type)
    except Exception as exc:
        logger.error("T212 Invest order placement failed: %s", exc, exc_info=True)

    try:
        from brokers.trading212 import maybe_place_stock_orders  # noqa: PLC0415
        maybe_place_stock_orders(config, run_type)
    except Exception as exc:
        logger.error("T212 Stocks order placement failed: %s", exc, exc_info=True)

    logger.info("Analysis pipeline complete (run_type=%s).", run_type)


def _pre_market_job() -> None:
    """07:00 UTC — pull fresh data for exchanges that just closed, then run analysis.

    Asian markets (JPX/Tokyo ~06:30, HKEX ~08:00) close before this run fires.
    For any exchange whose session ended before 07:30 UTC today, we pull
    incremental price data for those tickers. The 13:30 and 21:30 runs
    handle European and US markets respectively.
    """
    logger.info("Scheduled pre-market analysis starting (07:00 UTC)…")
    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "Pre-Market")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)
    config = _load_config()
    ticker_exchange = _build_ticker_exchange_map(config)

    now_utc = datetime.now(timezone.utc)
    # Allow a 30-min grace window — catches exchanges that close at/around run time
    cutoff_utc = now_utc

    unique_exchanges = set(ticker_exchange.values())
    recently_closed = _exchanges_closed_before(unique_exchanges, cutoff_utc)

    if recently_closed:
        closed_tickers = [t for t, e in ticker_exchange.items() if e in recently_closed]
        logger.info(
            "Pre-market: exchanges closed before %s UTC — %s (%d tickers). Pulling fresh data.",
            now_utc.strftime("%H:%M"), recently_closed, len(closed_tickers),
        )
        try:
            results = pull_incremental(tickers=closed_tickers)
            logger.info(
                "Pre-market data pull complete — %d new rows.", sum(results.values())
            )
        except Exception as exc:
            logger.error("Pre-market incremental pull failed: %s", exc, exc_info=True)

    _run_analysis_pipeline(
        config,
        get_all_tickers(config),
        run_type=RUN_ANALYSIS_ONLY,
    )


def _us_open_job() -> None:
    """13:30 UTC — analysis only using most recently stored price data.

    European markets are mid-session; US is about to open. No data pull —
    requires a complete session close.
    """
    logger.info("Scheduled US-open analysis starting (13:30 UTC)…")
    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "US Open")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)
    config = _load_config()
    _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)


def _eod_job() -> None:
    """21:30 UTC — pull incremental data for open exchanges, then run full analysis."""
    logger.info("Scheduled end-of-day refresh starting (21:30 UTC)…")
    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "End of Day")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)
    config = _load_config()
    ticker_exchange = _build_ticker_exchange_map(config)

    today_utc: date = datetime.now(timezone.utc).date()
    unique_exchanges = set(ticker_exchange.values())
    open_exchanges: dict[str, bool] = {
        exch: _is_exchange_open(exch, today_utc)
        for exch in unique_exchanges
    }

    open_tickers:    list[str] = []
    skipped_tickers: list[str] = []
    for ticker, exchange in ticker_exchange.items():
        if ticker in _ALWAYS_PULL or exchange in _ALWAYS_OPEN_EXCHANGES or open_exchanges[exchange]:
            open_tickers.append(ticker)
        else:
            logger.info("Skipping %s — %s closed today (%s).", ticker, exchange, today_utc)
            skipped_tickers.append(ticker)

    if not open_tickers:
        logger.info("All markets closed today (%s) — skipping data pull.", today_utc)
        _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)
        return

    logger.info(
        "EOD refresh — pulling %d assets (%d skipped, market closed).",
        len(open_tickers), len(skipped_tickers),
    )
    results = pull_incremental(tickers=open_tickers)
    total_rows = sum(results.values())
    logger.info("Data pull complete — %d new rows across %d assets.", total_rows, len(open_tickers))

    try:
        set_metadata("last_full_refresh", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.warning("Could not stamp last_full_refresh: %s", exc)

    try:
        set_metadata("last_pull_rows", str(total_rows))
        set_metadata("last_pull_ticker_count", str(len([v for v in results.values() if v > 0])))
    except Exception as exc:
        logger.warning("Could not stamp last_pull_rows: %s", exc)

    try:
        logger.info("Fetching FRED macro indicators…")
        fetch_and_store_macro_indicators(config)
        set_metadata("last_fred_run", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.error("FRED macro indicator fetch failed: %s", exc, exc_info=True)

    _run_analysis_pipeline(config, get_all_tickers(config), RUN_FULL)

    try:
        _fetch_t212_snapshot(config)
    except Exception as exc:
        logger.error("T212 snapshot fetch after EOD failed: %s", exc)


def _trade_alert_job() -> None:
    """Hourly (00:00–21:00 + 23:00 UTC, weekdays) — check open trade health and send alerts.

    Fetches a live price for every open trade whose home exchange is currently
    in session, enriches it with current signal and stop-loss status, then emails
    an alert when any trade reaches STOP LOSS or EXIT SIGNAL.  Deduplicates per
    trade: the same status for the same trade only triggers one email until the
    status changes.

    Per-exchange session filtering ensures that:
      - UK/EU trades are only checked during LSE/XETR hours (08:00–16:30 UTC)
      - Asian trades are checked during JPX/HKEX hours (00:00–08:00 UTC)
      - US trades are checked during NYSE hours (14:30–21:00 UTC)
      - Crypto/FX trades are always checked

    The global alert_cooldown_hours setting in config.yaml still applies.

    Also runs (unconditionally, regardless of open-trade state above):
    T212 order-status polling, account snapshot refresh, and — via
    brokers.trading212.maybe_exit_t212_positions() /
    maybe_exit_stock_positions() — the T212 Invest exit check, so a
    confirmed EXIT SIGNAL or backstop time-limit exit closes within about an
    hour rather than waiting for the next EOD run (see "Broker integration
    — Trading212 Invest" in CLAUDE.md for the exit trigger priority and the
    24h duplicate-SELL guard shared with the EOD run's own delegated call to
    the same functions).
    """
    logger.info("Trade alert check starting…")
    config = _load_config()

    # ── VIX spike check ────────────────────────────────────────────────────
    # Fire once per calendar day when VIX rises >20% vs the previous close.
    # Triggers an immediate analysis pipeline run and sends a spike alert email.
    try:
        from data.ingestion import get_price_df as _gpdf  # noqa: PLC0415
        _vix_df = _gpdf("^VIX", days=5)
        if _vix_df is not None and len(_vix_df) >= 2:
            _vix_prev  = float(_vix_df["close"].iloc[-2])
            _vix_curr  = float(_vix_df["close"].iloc[-1])
            if _vix_prev > 0:
                _vix_chg_pct = (_vix_curr - _vix_prev) / _vix_prev * 100
                if _vix_chg_pct >= 20.0:
                    _today_str = date.today().isoformat()
                    _spike_key = "last_vix_spike_alert"
                    _last_spike = get_metadata_str(_spike_key) or ""
                    if _last_spike != _today_str:
                        logger.warning(
                            "VIX spike detected: %.2f → %.2f (+%.1f%%) — "
                            "running immediate analysis.",
                            _vix_prev, _vix_curr, _vix_chg_pct,
                        )
                        # Stamp immediately so repeated hourly firings don't
                        # trigger another analysis run even if the email fails.
                        set_metadata(_spike_key, _today_str)
                        _run_analysis_pipeline(
                            config, get_all_tickers(config), RUN_ANALYSIS_ONLY
                        )
                        from output.briefing import send_vix_spike_alert  # noqa: PLC0415
                        _ok, _msg = send_vix_spike_alert(
                            _vix_chg_pct, _vix_curr, _vix_prev, config
                        )
                        logger.info("VIX spike alert email: %s — %s", _ok, _msg)
    except Exception as _vix_exc:
        logger.warning("VIX spike check failed: %s", _vix_exc)
    # ── End VIX spike check ────────────────────────────────────────────────

    # ── T212 Invest: poll orders + refresh account snapshot ───────────────
    # These run unconditionally every hour regardless of alert state, so the
    # Status page snapshot stays current even when there are no new alerts.
    try:
        from brokers.trading212 import poll_placed_orders as _poll_t212  # noqa: PLC0415
        _n_updated = _poll_t212(config)
        if _n_updated:
            logger.info("T212 order poll: %d order(s) updated to terminal status.", _n_updated)
    except Exception as _poll_exc:
        logger.warning("T212 order poll failed: %s", _poll_exc)

    # Exit checks run hourly so a confirmed EXIT SIGNAL closes within ~1h
    # rather than waiting up to 24h for the next EOD run. The EOD pipeline
    # calls the same two functions via maybe_place_t212_orders() /
    # maybe_place_stock_orders() — the 24h duplicate-SELL guard inside each
    # (_has_recent_sell_order()) keeps the two calls from both selling the
    # same position.
    try:
        from brokers.trading212 import maybe_exit_t212_positions  # noqa: PLC0415
        maybe_exit_t212_positions(config)
    except Exception as exc:
        logger.error("T212 Invest hourly exit check failed: %s", exc, exc_info=True)

    try:
        from brokers.trading212 import maybe_exit_stock_positions  # noqa: PLC0415
        maybe_exit_stock_positions(config)
    except Exception as exc:
        logger.error("T212 Stocks hourly exit check failed: %s", exc, exc_info=True)

    try:
        _fetch_t212_snapshot(config)
    except Exception as exc:
        logger.error("Hourly T212 snapshot refresh failed: %s", exc)
    # ── End T212 ───────────────────────────────────────────────────────────

    from output.performance import get_latest_price, get_open_trades  # noqa: PLC0415
    from output.briefing import (  # noqa: PLC0415
        enrich_open_trade,
        send_alert_email,
        should_send_alert,
    )

    trades = get_open_trades()
    if not trades:
        logger.info("Trade alert check: no open trades.")
        return

    now_utc        = datetime.now(timezone.utc)
    ticker_exchange = _build_ticker_exchange_map(config)

    _ALERT_STATUSES: frozenset[str] = frozenset({"STOP LOSS", "EXIT SIGNAL"})
    pending: list[dict] = []
    skipped_closed_exchange = 0

    for trade in trades:
        ticker   = trade.get("ticker", "")
        trade_id = trade.get("id")

        # Only alert on trades where the asset's home exchange is currently in session.
        exchange = ticker_exchange.get(ticker, "NYSE")
        if not _is_exchange_in_session(exchange, now_utc):
            skipped_closed_exchange += 1
            logger.debug(
                "Trade alert: skipping %s — %s not in session at %s UTC.",
                ticker, exchange, now_utc.strftime("%H:%M"),
            )
            continue

        try:
            live_price = get_latest_price(ticker)
        except Exception as exc:
            logger.warning("Price fetch failed for %s: %s", ticker, exc)
            live_price = None

        try:
            enriched = enrich_open_trade(trade, live_price, get_max_holding_days(ticker, config))
        except Exception as exc:
            logger.warning("enrich_open_trade failed for %s: %s", ticker, exc)
            continue

        status = enriched.get("status", "ON TRACK")
        if status not in _ALERT_STATUSES:
            continue

        meta_key = f"trade_alert_status:{trade_id}"
        if get_metadata_str(meta_key) == status:
            logger.info(
                "Trade %s (%s): already alerted for %s — skipping.",
                trade_id, ticker, status,
            )
            continue

        pending.append(enriched)

    if skipped_closed_exchange:
        logger.info(
            "Trade alert check: %d trade(s) skipped — exchange not in session.",
            skipped_closed_exchange,
        )

    # ── Price-level SL/TP proximity alerts (live prices via yfinance fast_info) ──
    # Runs independently of the enrich-based check above so you're alerted within
    # one hour if a price approaches or crosses a stored SL/TP level.
    _price_pending: list[dict] = []
    try:
        import yfinance as yf  # noqa: PLC0415

        for _pt in trades:
            _pt_ticker   = _pt.get("ticker", "")
            _pt_id       = _pt.get("id")
            _pt_dir      = _pt.get("direction", "LONG")
            _pt_entry    = float(_pt.get("entry_price") or 0)
            _pt_sl       = _pt.get("stop_loss_price")
            _pt_tp       = _pt.get("take_profit_price")

            if _pt_sl is None and _pt_tp is None:
                continue

            _pt_exch = ticker_exchange.get(_pt_ticker, "NYSE")
            if not _is_exchange_in_session(_pt_exch, now_utc):
                continue

            try:
                _pt_live = float(yf.Ticker(_pt_ticker).fast_info["last_price"])
                if not _pt_live or _pt_live <= 0:
                    continue
            except Exception as _fe:
                logger.debug("fast_info fetch failed for %s: %s", _pt_ticker, _fe)
                continue

            _pt_sl = float(_pt_sl) if _pt_sl is not None else None
            _pt_tp = float(_pt_tp) if _pt_tp is not None else None

            # Determine triggered condition — priority: SL HIT > APPROACHING > TP HIT
            _pt_alert: str | None = None
            if _pt_sl is not None:
                _sl_hit = (
                    (_pt_dir == "LONG"  and _pt_live <= _pt_sl) or
                    (_pt_dir == "SHORT" and _pt_live >= _pt_sl)
                )
                _approaching = not _sl_hit and (
                    (_pt_dir == "LONG"  and _pt_live <= _pt_sl * 1.01) or
                    (_pt_dir == "SHORT" and _pt_live >= _pt_sl * 0.99)
                )
                if _sl_hit:
                    _pt_alert = "STOP LOSS HIT"
                elif _approaching:
                    _pt_alert = "APPROACHING STOP"

            if _pt_alert is None and _pt_tp is not None and _pt_tp > 0:
                _tp_hit = (
                    (_pt_dir == "LONG"  and _pt_live >= _pt_tp) or
                    (_pt_dir == "SHORT" and _pt_live <= _pt_tp)
                )
                if _tp_hit:
                    _pt_alert = "TAKE PROFIT REACHED"

            _pt_meta_key = f"trade_price_alert:{_pt_id}"

            if _pt_alert is None:
                # Clear APPROACHING STOP dedup when price recovers so re-approach re-alerts.
                if get_metadata_str(_pt_meta_key) == "APPROACHING STOP":
                    clear_metadata(_pt_meta_key)
                continue

            # Dedup: only alert on state transitions
            if get_metadata_str(_pt_meta_key) == _pt_alert:
                logger.info(
                    "Trade %s (%s): already alerted for %s — skipping.",
                    _pt_id, _pt_ticker, _pt_alert,
                )
                continue

            # Compute fields for the alert card
            _pt_unreal = None
            if _pt_entry:
                _raw = (_pt_live - _pt_entry) if _pt_dir == "LONG" else (_pt_entry - _pt_live)
                _pt_unreal = round(_raw / _pt_entry * 100, 2)

            _pt_days = 0
            _pt_edt = _pt.get("entry_date")
            if _pt_edt:
                try:
                    _pt_ep = (
                        _pt_edt if isinstance(_pt_edt, datetime)
                        else datetime.fromisoformat(str(_pt_edt).split(".")[0])
                    )
                    _pt_days = max(0, (datetime.utcnow() - _pt_ep).days)
                except Exception:
                    pass

            # Fetch current signal so the options-guidance section has up-to-date context.
            _current_sig = "NEUTRAL"
            _current_sc  = 0.0
            try:
                from output.performance import get_current_signal as _gcs  # noqa: PLC0415
                _sig_row = _gcs(_pt_ticker)
                if _sig_row:
                    _current_sig = _sig_row.get("signal") or "NEUTRAL"
                    _current_sc  = float(_sig_row.get("confluence_score") or 0)
            except Exception:
                pass

            _price_pending.append({
                **_pt,                             # carry all original trade fields (incl. planned_risk_gbp, fx_rate_at_entry, etc.)
                "id":               _pt_id,
                "ticker":           _pt_ticker,
                "direction":        _pt_dir,
                "entry_price":      _pt_entry,
                "live_price":       _pt_live,
                "unreal_pct":       _pt_unreal,
                "entry_signal":     _pt.get("signal_type", "?"),
                "current_signal":   _current_sig,
                "entry_score":      float(_pt.get("confluence_score") or 0),
                "current_score":    _current_sc,
                "stop_loss":        _pt_sl,
                "take_profit_price": _pt_tp,
                "days_held":        _pt_days,
                "days_remaining":   None,
                "max_holding_days": get_max_holding_days(_pt_ticker, config),
                "near_max_hold":    False,
                "status":           _pt_alert,
            })

    except Exception as _pe:
        logger.warning("Price-level SL/TP alert check failed: %s", _pe)

    # Merge price alerts — skip STOP LOSS HIT duplicates if enrich already caught them
    if _price_pending:
        _enriched_ids = {e.get("id") for e in pending}
        for _pa in _price_pending:
            if _pa["status"] == "STOP LOSS HIT" and _pa["id"] in _enriched_ids:
                continue
            pending.append(_pa)
        logger.info(
            "Price-level check: %d new alert(s) added to pending.", len(_price_pending)
        )

    if not pending:
        logger.info(
            "Trade alert check: no new conditions across %d open trade(s).", len(trades)
        )
        return

    logger.info("Trade alert check: %d new alert(s) pending.", len(pending))

    if not should_send_alert(config):
        logger.info("Alert email suppressed by cooldown or disabled config.")
        return

    ok, msg = send_alert_email(pending, config)
    logger.info("Alert email result: %s — %s", ok, msg)

    if ok:
        _price_alert_statuses = frozenset({"APPROACHING STOP", "STOP LOSS HIT", "TAKE PROFIT REACHED"})
        for enriched in pending:
            _eid = enriched.get("id")
            _est = enriched.get("status", "")
            _mk  = (
                f"trade_price_alert:{_eid}"
                if _est in _price_alert_statuses
                else f"trade_alert_status:{_eid}"
            )
            try:
                set_metadata(_mk, _est)
            except Exception as exc:
                logger.warning(
                    "Could not stamp alert status for trade %s: %s", _eid, exc,
                )



def _weekly_summary_job() -> None:
    """Every Sunday at 10:00 UTC — send the weekly performance summary email."""
    logger.info("Weekly summary job starting…")
    config = _load_config()
    from output.briefing import send_weekly_summary_email  # noqa: PLC0415
    ok, msg = send_weekly_summary_email(config)
    logger.info("Weekly summary: %s — %s", "sent" if ok else "failed", msg)


def _fomc_reaction_job() -> None:
    """19:05 UTC weekdays — run analysis-only if today is an FOMC decision day.

    FOMC statements are released at 2:00 PM US/Eastern (18:00–19:00 UTC).
    This job fires at 19:05 UTC, captures the immediate post-decision market
    reaction before the regular 21:30 EOD run, and refreshes signals so the
    Daily Briefing reflects any regime shift triggered by the Fed announcement.
    Silently exits on non-FOMC days so it adds no cost on normal weekdays.
    """
    from analysis.fomc import is_fomc_decision_day  # noqa: PLC0415

    if not is_fomc_decision_day():
        logger.debug("FOMC reaction check: not an FOMC decision day — skipping.")
        return

    logger.info("FOMC decision day — running post-decision analysis (19:05 UTC)…")
    config = _load_config()
    _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)
    logger.info("FOMC reaction analysis complete.")


def _check_missed_eod_run(config: dict) -> None:
    """Trigger an immediate EOD catch-up if today's 21:30 UTC EOD run was missed.

    Called at scheduler startup, after all jobs are registered.  Both
    conditions below must hold before a catch-up fires:

    1. last_full_refresh is more than 20 hours old. A run from earlier the
       same day (e.g. a manual "Refresh Data" click, or the real EOD job
       having already fired minutes ago) does not count as missed — only a
       genuinely stale refresh does.
    2. Current UTC time is between 21:45 and 23:59 — the post-EOD window.
       21:45 gives the real 21:30 cron trigger a 15-minute grace period
       before concluding it didn't fire. Outside this window we skip
       silently: a restart at, say, 08:00 UTC has no reason to believe the
       EOD job was missed, and the next scheduled job (07:00 pre-market or
       tomorrow's 21:30 EOD) will naturally pick up stale data anyway.

    If last_full_refresh has never been recorded (fresh install, no data
    yet), skip silently — there's nothing to catch up on.

    The Saturday/Sunday guard is preserved from the previous version: no EOD
    job is ever scheduled on a weekend, so a stale last_full_refresh there
    is expected, not missed.

    NOTE: this replaces the previous _maybe_catch_up_missed_eod, which used a
    26-hour staleness threshold OR'd with an unbounded "past today's EOD
    time" check (anywhere from 21:30 UTC through the next morning could
    re-trigger it) and re-ran the full _eod_job (incremental data pull +
    FRED fetch + T212 snapshot). Deployments were landing inside that same
    window and racing the live EOD job — see "Deployment timing" in
    CLAUDE.md. The narrower 20h/21:45-23:59 AND-gate makes catch-up fire
    deterministically within one bounded window instead of however long the
    service happens to stay down, and it now runs _run_analysis_pipeline()
    directly (analysis + signals + order logic) rather than re-pulling
    data — a same-day restart in this window means the pipeline ran on a
    transitional deploy, not that prices are missing.

    The catch-up runs in a daemon thread so start_scheduler() returns promptly.
    """
    now_utc = datetime.now(timezone.utc)

    if now_utc.weekday() >= 5:  # Saturday (5) or Sunday (6) — no scheduled EOD
        return

    last_refresh_str = get_metadata_str("last_full_refresh")
    if last_refresh_str is None:
        logger.debug(
            "Missed-EOD check: no last_full_refresh recorded yet — "
            "startup init handles the first analysis run."
        )
        return

    try:
        last_dt = datetime.fromisoformat(last_refresh_str)
    except ValueError:
        logger.warning(
            "Missed-EOD check: could not parse last_full_refresh '%s' — skipping.",
            last_refresh_str,
        )
        return

    now_naive  = now_utc.replace(tzinfo=None)
    last_naive = last_dt.replace(tzinfo=None) if last_dt.tzinfo else last_dt
    hours_since = (now_naive - last_naive).total_seconds() / 3600

    window_start = now_naive.replace(hour=21, minute=45, second=0, microsecond=0)
    window_end   = now_naive.replace(hour=23, minute=59, second=59, microsecond=999999)
    if not (window_start <= now_naive <= window_end):
        logger.debug(
            "Missed-EOD check: outside 21:45-23:59 UTC catch-up window (now %s UTC) — "
            "next scheduled job will catch up naturally.",
            now_naive.strftime("%H:%M"),
        )
        return

    if hours_since <= 20:
        logger.debug(
            "Missed-EOD check: last refresh %.1fh ago (<=20h threshold) — "
            "no catch-up needed.", hours_since,
        )
        return

    logger.warning("Missed EOD run detected — running catch-up pipeline now")
    set_metadata("missed_eod_catch_up", now_utc.isoformat())

    import threading  # noqa: PLC0415
    t = threading.Thread(
        target=_run_analysis_pipeline,
        args=(config, get_all_tickers(config), RUN_FULL),
        name="eod-catch-up",
        daemon=True,
    )
    t.start()


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler with all three daily jobs if not already running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    config    = _load_config()
    sched_cfg = config["scheduler"]
    tz        = sched_cfg["timezone"]

    _scheduler = BackgroundScheduler(timezone=tz)

    # Suppress APScheduler's per-job "Running job / executed successfully" INFO spam.
    # Errors and warnings from job functions still surface via the root logger.
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)

    _JOB_FNS = {
        "pre_market": _pre_market_job,
        "us_open":    _us_open_job,
        "eod":        _eod_job,
    }

    for run_name, run_cfg in sched_cfg["runs"].items():
        job_fn = _JOB_FNS.get(run_name)
        if job_fn is None:
            logger.warning("Unknown scheduler run name '%s' — skipping.", run_name)
            continue
        _scheduler.add_job(
            job_fn,
            trigger=CronTrigger(
                hour=run_cfg["hour"],
                minute=run_cfg["minute"],
                day_of_week="mon-fri",
                timezone=tz,
            ),
            id=f"scheduled_{run_name}",
            replace_existing=True,
        )
        logger.info(
            "Scheduled '%s' job at %02d:%02d %s on weekdays.",
            run_cfg.get("label", run_name), run_cfg["hour"], run_cfg["minute"], tz,
        )

    # 60-second dispatcher: executes pending UI-requested jobs (e.g. T212 refresh).
    _scheduler.add_job(
        _dispatch_pending_jobs,
        trigger=IntervalTrigger(seconds=60),
        id="job_dispatcher",
        replace_existing=True,
    )
    logger.info("Scheduled 60-second job dispatcher.")

    # Hourly trade alert check across all major market sessions (weekdays only).
    # Hour range covers: Asian (00-08 UTC), European (08-17 UTC), US (14-21 UTC),
    # plus 23:00 UTC for Sydney/Auckland pre-market.
    # The job itself filters per-trade by exchange session, so only relevant alerts
    # fire at each hour — no spurious alerts outside market hours.
    _scheduler.add_job(
        _trade_alert_job,
        trigger=CronTrigger(
            hour="0-21,23",
            minute=0,
            day_of_week="mon-fri",
            timezone=tz,
        ),
        id="trade_alert_check",
        replace_existing=True,
    )
    logger.info(
        "Scheduled trade alert check hourly 00:00–21:00 + 23:00 %s on weekdays "
        "(filtered per-trade by home exchange session).", tz,
    )

    _scheduler.add_job(
        _weekly_summary_job,
        trigger=CronTrigger(
            day_of_week="sun",
            hour=10,
            minute=0,
            timezone=tz,
        ),
        id="weekly_summary",
        replace_existing=True,
    )
    logger.info("Scheduled weekly summary email every Sunday 10:00 %s.", tz)

    # FOMC reaction run: fires every weekday at 19:05 UTC but executes the
    # analysis pipeline only on days matching the hardcoded FOMC decision schedule.
    _scheduler.add_job(
        _fomc_reaction_job,
        trigger=CronTrigger(
            hour=19,
            minute=5,
            day_of_week="mon-fri",
            timezone=tz,
        ),
        id="fomc_reaction",
        replace_existing=True,
    )
    logger.info(
        "Scheduled FOMC reaction analysis at 19:05 %s weekdays "
        "(active on FOMC decision days only).", tz,
    )

    _scheduler.start()
    logger.info("Scheduler started with %d jobs.", len(_scheduler.get_jobs()))

    # Write an immediate heartbeat so the Streamlit UI detects a running
    # scheduler straight away (the dispatcher job writes subsequent heartbeats
    # every 60 seconds, but takes up to 60s to fire for the first time).
    try:
        set_metadata("scheduler_heartbeat", datetime.utcnow().isoformat())
    except Exception:
        pass

    # Check for missed EOD runs after starting, so the scheduler is already
    # accepting jobs before we potentially queue a catch-up run.
    _check_missed_eod_run(config)

    return _scheduler


def stop_scheduler() -> None:
    """Shut down the background scheduler gracefully."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    _scheduler = None
