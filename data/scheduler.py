"""APScheduler-based scheduled jobs: three daily runs with per-exchange holiday awareness.

Run schedule (weekdays only, UTC):
  07:00  Pre-market  — pull fresh data for exchanges that just closed (Asian markets),
                       then run analysis; open virtual trades for those tickers
  13:30  US open     — analysis only, no data pull, no virtual trade opens
  21:30  End of day  — full data refresh then analysis (all exchanges); opens for all
  00-21,23  Hourly   — open-trade health check; email alert on STOP LOSS / EXIT SIGNAL
                       only fires for trades whose home exchange is currently in session

All three analysis runs execute the complete analysis pipeline:
  run_analysis()       → indicators table
  generate_signals()   → signals table  (also stamps last_analysis_run)
  compute_all_pairs()  → correlations table

Metadata keys written to system_metadata:
  last_full_refresh         — UTC timestamp of the last successful data pull (EOD run or manual)
  last_analysis_run         — UTC timestamp of the last completed analysis (any run type)
  last_run_type             — 'full' or 'analysis_only'
  trade_alert_status:{id}   — last health status for which an alert email was sent per trade
"""

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas_market_calendars as mcal
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from analysis.confluence import generate_signals
from analysis.correlations import compute_all_pairs
from analysis.engine import run_analysis
from analysis.macro import fetch_and_store_macro_indicators
from data.database import clear_metadata, get_metadata_str, set_metadata
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

# Native pricing currency for each exchange (used when opening virtual trades
# to compute units = investment_GBP × fx_rate / entry_price).
_EXCHANGE_CURRENCY: dict[str, str] = {
    "NYSE":   "USD",
    "NASDAQ": "USD",
    "CBOE":   "USD",
    "LSE":    "GBP",
    "TSE":    "JPY",
    "JPX":    "JPY",
    "XETR":   "EUR",
    "EUREX":  "EUR",
    "HKEX":   "HKD",
    "Crypto": "USD",
}

# Quote currency for FX ticker pairs (the currency the rate is priced in).
_FX_QUOTE_CCY: dict[str, str] = {
    "GBPUSD=X": "USD",
    "EURUSD=X": "USD",
    "AUDUSD=X": "USD",
    "USDJPY=X": "JPY",
    "USDCHF=X": "CHF",
    "USDCAD=X": "CAD",
    "EURGBP=X": "GBP",
}


def _instrument_ccy(ticker: str, exchange: str) -> str:
    """Return the native pricing currency for a ticker."""
    if exchange == "FX":
        return _FX_QUOTE_CCY.get(ticker, "USD")
    return _EXCHANGE_CURRENCY.get(exchange, "USD")


def _gbp_to_ccy_rate(ccy: str, prices: dict[str, float]) -> float:
    """Units of *ccy* per GBP, derived from prices already loaded from the DB."""
    if ccy == "GBP":
        return 1.0
    gbpusd = prices.get("GBPUSD=X", 1.27)
    if ccy == "USD":
        return gbpusd
    if ccy == "JPY":
        return gbpusd * prices.get("USDJPY=X", 150.0)
    if ccy == "EUR":
        eurusd = prices.get("EURUSD=X", 1.10)
        return gbpusd / eurusd if eurusd else gbpusd
    if ccy == "AUD":
        audusd = prices.get("AUDUSD=X", 0.64)
        return gbpusd / audusd if audusd else gbpusd
    if ccy == "HKD":
        return gbpusd * 7.8  # approximate; GBPHKD not in watchlist
    return gbpusd  # fallback: treat as USD-denominated


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


def _process_virtual_trades(
    config: dict,
    run_type: str = RUN_FULL,
    eligible_exchanges: set[str] | None = None,
) -> None:
    """Auto-open/close virtual positions based on current signals.

    Called at the end of every analysis pipeline run.  Has no effect unless
    virtual_trading.enabled: true in config.yaml.

    Close conditions (checked on every run):
      - Current price has crossed the stored stop_loss_price, or
      - Signal has reversed direction.

    Open conditions:
      - Either run_type == RUN_FULL (EOD job with fresh data for all exchanges),
        OR eligible_exchanges is explicitly provided (pre-market partial pull),
      - The ticker's exchange is in eligible_exchanges (or tradeable_open_today
        when eligible_exchanges is None and run_type == RUN_FULL),
      - Signal is STRONG_BUY or STRONG_SELL (or BUY/SELL if entry_signal: "BUY"),
      - Ticker is tradeable with no existing virtual position,
      - Total open virtual positions < max_open_positions.
    """
    vt = config.get("virtual_trading", {})
    if not vt.get("enabled", False):
        return

    from sqlalchemy import text as sa_text  # noqa: PLC0415
    from data.database import get_engine    # noqa: PLC0415

    entry_threshold = vt.get("entry_signal", "STRONG_BUY")
    max_positions   = int(vt.get("max_open_positions", 5))
    position_pct    = float(vt.get("position_pct", 0.05))
    portfolio_gbp   = float(vt.get("portfolio_size_gbp", 10000))
    atr_mult        = float(config.get("backtest", {}).get("atr_stop_multiplier", 2.0))
    max_hold_days   = int(config.get("backtest", {}).get("max_holding_days", 30))

    tradeable: set[str] = {
        e["ticker"]
        for g in config.get("watchlist", {}).values()
        if isinstance(g, list)
        for e in g
        if isinstance(e, dict) and e.get("tradeable")
    }

    if not tradeable:
        return

    ticker_exchange = _build_ticker_exchange_map(config)
    today_utc = datetime.now(timezone.utc).date()

    # Determine which tickers are eligible to have new virtual positions opened.
    # Priority: explicit eligible_exchanges > run_type check > skip
    if eligible_exchanges is not None:
        # Pre-market or partial run: only open for the specified exchanges
        can_open: set[str] = {
            t for t in tradeable
            if ticker_exchange.get(t, "NYSE") in eligible_exchanges
            or ticker_exchange.get(t, "NYSE") in _ALWAYS_OPEN_EXCHANGES
        }
    elif run_type == RUN_FULL:
        # EOD run: open for all tradeable tickers whose exchange had a session today
        exchange_open_today: dict[str, bool] = {}
        for ticker in tradeable:
            exch = ticker_exchange.get(ticker, "NYSE")
            if exch not in exchange_open_today:
                exchange_open_today[exch] = _is_exchange_open(exch, today_utc)
        can_open = {
            t for t in tradeable
            if exchange_open_today.get(ticker_exchange.get(t, "NYSE"), True)
        }
    else:
        # Analysis-only run with no explicit eligible exchanges: skip opens
        logger.info(
            "Virtual trading: skipping open phase on analysis-only run "
            "(run_type=%s) — new positions require fresh price data.", run_type,
        )
        can_open = set()

    eng = get_engine()
    now = datetime.utcnow()

    # ── Load data ─────────────────────────────────────────────────────────────
    with eng.connect() as conn:
        open_rows = conn.execute(sa_text(
            "SELECT * FROM trades WHERE account_type='VIRTUAL' AND is_open=1"
        )).mappings().all()

        sig_rows = conn.execute(sa_text("""
            SELECT s.ticker, s.signal, s.confluence_score, s.regime,
                   s.trend_score, s.mean_reversion_score, s.volatility_score,
                   s.volume_score, s.macro_score
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

        atr_rows = conn.execute(sa_text("""
            SELECT i.ticker, i.atr
            FROM indicators i
            INNER JOIN (
                SELECT ticker, MAX(date) AS md FROM indicators GROUP BY ticker
            ) latest ON i.ticker = latest.ticker AND i.date = latest.md
        """)).mappings().all()

    open_trades    = [dict(r) for r in open_rows]
    open_by_ticker = {t["ticker"]: t for t in open_trades}
    signals        = {r["ticker"]: dict(r) for r in sig_rows if r["ticker"] in tradeable}
    prices         = {r["ticker"]: float(r["close"]) for r in price_rows if r["close"] is not None}
    atrs           = {r["ticker"]: float(r["atr"])   for r in atr_rows   if r["atr"]   is not None}

    # ── Close phase (runs on every call regardless of run_type / eligible_exchanges) ──
    to_close: list[tuple] = []
    for ticker, trade in list(open_by_ticker.items()):
        live_price = prices.get(ticker)
        if live_price is None:
            continue

        direction   = trade.get("direction", "LONG")
        entry_price = float(trade.get("entry_price") or 0)
        sl_price    = trade.get("stop_loss_price")
        trade_id    = trade["id"]

        if sl_price is None and entry_price and ticker in atrs:
            sl_price = (entry_price - atr_mult * atrs[ticker] if direction == "LONG"
                        else entry_price + atr_mult * atrs[ticker])

        stop_hit = (
            (direction == "LONG"  and sl_price is not None and live_price <= sl_price) or
            (direction == "SHORT" and sl_price is not None and live_price >= sl_price)
        )
        sig_now = signals.get(ticker, {}).get("signal", "NEUTRAL")
        signal_reversal = (
            (direction == "LONG"  and sig_now in ("SELL", "STRONG_SELL")) or
            (direction == "SHORT" and sig_now in ("BUY",  "STRONG_BUY"))
        )

        hold_days = 0
        entry_dt_raw = trade.get("entry_date")
        if entry_dt_raw:
            try:
                entry_dt_parsed = (
                    entry_dt_raw if isinstance(entry_dt_raw, datetime)
                    else datetime.fromisoformat(str(entry_dt_raw).split(".")[0])
                )
                hold_days = (now - entry_dt_parsed).days
            except Exception:
                pass
        max_hold_exceeded = hold_days >= max_hold_days

        reversal_meta_key = f"weakening_since:{trade_id}"
        if stop_hit or max_hold_exceeded:
            # Hard exits: close immediately regardless of confirmation
            reason = "STOP LOSS" if stop_hit else "MAX HOLDING"
            to_close.append((trade_id, live_price, reason, direction, entry_price, trade.get("entry_date")))
            del open_by_ticker[ticker]
            clear_metadata(reversal_meta_key)
        elif signal_reversal:
            # Reversal confirmation: require the reversal to persist for N runs
            conf_runs = int(config.get("signals", {}).get("reversal_confirmation_runs", 2))
            min_hours = conf_runs * 3
            if min_hours <= 0:
                to_close.append((trade_id, live_price, "SIGNAL REVERSAL", direction, entry_price, trade.get("entry_date")))
                del open_by_ticker[ticker]
            else:
                first_weak = get_metadata_str(reversal_meta_key)
                if first_weak is None:
                    set_metadata(reversal_meta_key, now.isoformat())
                    logger.info("Virtual trade %s (%s): reversal detected — waiting for confirmation.", trade_id, ticker)
                else:
                    try:
                        hours_weak = (now - datetime.fromisoformat(first_weak)).total_seconds() / 3600
                    except Exception:
                        hours_weak = 0.0
                    if hours_weak >= min_hours:
                        to_close.append((trade_id, live_price, "SIGNAL REVERSAL", direction, entry_price, trade.get("entry_date")))
                        del open_by_ticker[ticker]
                        clear_metadata(reversal_meta_key)
                    else:
                        logger.info(
                            "Virtual trade %s (%s): reversal pending confirmation (%.1fh / %.0fh).",
                            trade_id, ticker, hours_weak, min_hours,
                        )
        else:
            # No close condition — clear reversal timer if it was running
            if get_metadata_str(reversal_meta_key) is not None:
                clear_metadata(reversal_meta_key)

    if to_close:
        with eng.begin() as conn:
            for trade_id, live_price, reason, direction, entry_price, entry_dt in to_close:
                pnl_pct = 0.0
                if entry_price:
                    pnl_pct = ((live_price - entry_price) / entry_price
                               if direction == "LONG"
                               else (entry_price - live_price) / entry_price)
                hold_days = 0
                if entry_dt:
                    try:
                        entry_dt_parsed = (
                            entry_dt if isinstance(entry_dt, datetime)
                            else datetime.fromisoformat(str(entry_dt).split(".")[0])
                        )
                        hold_days = max(0, (now - entry_dt_parsed).days)
                    except Exception:
                        pass
                conn.execute(sa_text("""
                    UPDATE trades
                    SET is_open=0, exit_date=:exit_date, exit_price=:exit_price,
                        pnl_pct=:pnl_pct, holding_days=:hold_days
                    WHERE id=:trade_id
                """), {
                    "exit_date":  now.isoformat(),
                    "exit_price": live_price,
                    "pnl_pct":    round(pnl_pct, 6),
                    "hold_days":  hold_days,
                    "trade_id":   trade_id,
                })
                logger.info(
                    "Virtual trade closed: %s at %.4f (P&L %.2f%%) — %s",
                    trade_id, live_price, pnl_pct * 100, reason,
                )

    # ── Open phase ────────────────────────────────────────────────────────────
    open_count = len(open_by_ticker)

    if not can_open:
        logger.info(
            "Virtual trading: closed %d, opened 0, total open %d/%d.",
            len(to_close), open_count, max_positions,
        )
        return

    if open_count >= max_positions:
        logger.info("Virtual trading: at max positions (%d/%d) — no new entries.", open_count, max_positions)
        return

    entry_signal_set = (
        {"STRONG_BUY", "BUY", "STRONG_SELL", "SELL"}
        if entry_threshold == "BUY"
        else {"STRONG_BUY", "STRONG_SELL"}
    )

    to_open: list[dict] = []
    for ticker, sig in signals.items():
        if open_count + len(to_open) >= max_positions:
            break
        if ticker in open_by_ticker:
            continue
        if ticker not in can_open:
            logger.debug("Virtual trading: skipping %s — exchange not in eligible set.", ticker)
            continue
        sig_type = sig.get("signal", "NEUTRAL")
        if sig_type not in entry_signal_set:
            continue
        live_price = prices.get(ticker)
        if not live_price:
            continue

        direction = "LONG" if sig_type in ("STRONG_BUY", "BUY") else "SHORT"
        atr       = atrs.get(ticker)
        sl_price  = None
        if atr:
            sl_price = (live_price - atr_mult * atr if direction == "LONG"
                        else live_price + atr_mult * atr)

        # Position sizing: convert GBP investment to native currency, then to units.
        exchange       = ticker_exchange.get(ticker, "NYSE")
        instr_ccy      = _instrument_ccy(ticker, exchange)
        fx_rate        = _gbp_to_ccy_rate(instr_ccy, prices)
        investment_gbp = round(portfolio_gbp * position_pct, 2)
        pos_value_native = round(investment_gbp * fx_rate, 4)
        quantity       = round(pos_value_native / live_price, 6) if live_price else None

        to_open.append({
            "ticker":               ticker,
            "direction":            direction,
            "entry_date":           now.isoformat(),
            "entry_price":          live_price,
            "signal_type":          sig_type,
            "confluence_score":     sig.get("confluence_score"),
            "regime":               sig.get("regime"),
            "trend_score":          sig.get("trend_score"),
            "mean_reversion_score": sig.get("mean_reversion_score"),
            "volatility_score":     sig.get("volatility_score"),
            "volume_score":         sig.get("volume_score"),
            "macro_score":          sig.get("macro_score"),
            "investment":           investment_gbp,
            "sl":                   round(sl_price, 6) if sl_price else None,
            "currency":             instr_ccy,
            "fx_rate":              round(fx_rate, 6),
            "quantity":             quantity,
            "pos_value":            pos_value_native,
        })

    if to_open:
        with eng.begin() as conn:
            for row in to_open:
                conn.execute(sa_text("""
                    INSERT INTO trades (
                        ticker, account_type, account_name, account_id,
                        direction, entry_date, entry_price, signal_type,
                        confluence_score, regime, trend_score, mean_reversion_score,
                        volatility_score, volume_score, macro_score,
                        investment_amount, stop_loss_price,
                        quantity, currency, fx_rate_at_entry, position_value,
                        is_open, created_at
                    ) VALUES (
                        :ticker, 'VIRTUAL', 'Virtual Portfolio', 'virtual',
                        :direction, :entry_date, :entry_price, :signal_type,
                        :confluence_score, :regime, :trend_score, :mean_reversion_score,
                        :volatility_score, :volume_score, :macro_score,
                        :investment, :sl,
                        :quantity, :currency, :fx_rate, :pos_value,
                        1, :now
                    )
                """), {**row, "now": now.isoformat()})
                logger.info(
                    "Virtual trade opened: %s %s at %.4f (signal=%s, regime=%s)",
                    row["direction"], row["ticker"], row["entry_price"],
                    row["signal_type"], row["regime"],
                )

    logger.info(
        "Virtual trading: closed %d, opened %d, total open %d/%d.",
        len(to_close), len(to_open), open_count + len(to_open), max_positions,
    )


def _run_analysis_pipeline(
    config: dict,
    all_tickers: list[str],
    run_type: str,
    eligible_exchanges: set[str] | None = None,
) -> None:
    """Execute the full analysis pipeline and stamp metadata.

    eligible_exchanges: when provided, only those exchanges are eligible for
    virtual trade openings (used by the pre-market run for Asian markets).
    When None the standard run_type logic applies inside _process_virtual_trades.
    """
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
        _process_virtual_trades(config, run_type, eligible_exchanges)
    except Exception as exc:
        logger.error("Virtual trading automation failed: %s", exc, exc_info=True)

    logger.info("Analysis pipeline complete (run_type=%s).", run_type)


def _pre_market_job() -> None:
    """07:00 UTC — pull fresh data for exchanges that just closed, then run analysis.

    Asian markets (JPX/Tokyo ~06:30, HKEX ~08:00) close before this run fires.
    For any exchange whose session ended before 07:30 UTC today, we pull
    incremental price data and allow virtual trade openings for those tickers.
    The 13:30 and 21:30 runs handle European and US markets respectively.
    """
    logger.info("Scheduled pre-market analysis starting (07:00 UTC)…")
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
            recently_closed = set()  # don't open virtual trades if pull failed

    if recently_closed:
        logger.info(
            "Pre-market: opening virtual trades for recently-closed exchanges: %s",
            recently_closed,
        )

    _run_analysis_pipeline(
        config,
        get_all_tickers(config),
        run_type=RUN_ANALYSIS_ONLY,
        eligible_exchanges=recently_closed if recently_closed else None,
    )


def _us_open_job() -> None:
    """13:30 UTC — analysis only using most recently stored price data.

    European markets are mid-session; US is about to open.  No data pull and
    no virtual trade openings — both require a complete session close.
    """
    logger.info("Scheduled US-open analysis starting (13:30 UTC)…")
    config = _load_config()
    _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)


def _eod_job() -> None:
    """21:30 UTC — pull incremental data for open exchanges, then run full analysis."""
    logger.info("Scheduled end-of-day refresh starting (21:30 UTC)…")
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
        logger.info("Fetching FRED macro indicators…")
        fetch_and_store_macro_indicators(config)
        set_metadata("last_fred_run", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.error("FRED macro indicator fetch failed: %s", exc, exc_info=True)

    _run_analysis_pipeline(config, get_all_tickers(config), RUN_FULL)


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
    _max_hold_days = int(config.get("backtest", {}).get("max_holding_days", 30))

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
            enriched = enrich_open_trade(trade, live_price, _max_hold_days)
        except Exception as exc:
            logger.warning("enrich_open_trade failed for %s: %s", ticker, exc)
            continue

        status = enriched.get("status", "ON TRACK")
        if status not in _ALERT_STATUSES:
            continue

        # Close virtual trades immediately on STOP LOSS.  The exchange-session
        # check earlier in this loop already guarantees the market is open, so
        # the market-hours constraint is satisfied.  EXIT SIGNAL is left to the
        # scheduled analysis runs (07:00 / 13:30 / 21:30) where the reversal-
        # confirmation logic runs.
        if status == "STOP LOSS" and trade.get("account_type") == "VIRTUAL":
            try:
                from data.database import get_engine as _get_engine  # noqa: PLC0415
                from sqlalchemy import text as _sa_text              # noqa: PLC0415
                _entry = float(trade.get("entry_price") or 0)
                _dir   = trade.get("direction", "LONG")
                _live  = live_price or 0.0
                _pnl   = 0.0
                if _entry:
                    _pnl = ((_live - _entry) / _entry if _dir == "LONG"
                            else (_entry - _live) / _entry)
                _hold = 0
                _edt  = trade.get("entry_date")
                if _edt:
                    try:
                        _ep = (_edt if isinstance(_edt, datetime)
                               else datetime.fromisoformat(str(_edt).split(".")[0]))
                        _hold = max(0, (now_utc.replace(tzinfo=None) - _ep).days)
                    except Exception:
                        pass
                with _get_engine().begin() as _conn:
                    _conn.execute(_sa_text("""
                        UPDATE trades
                        SET is_open=0, exit_date=:ts, exit_price=:price,
                            pnl_pct=:pnl, holding_days=:hold
                        WHERE id=:tid AND is_open=1
                    """), {
                        "ts":    now_utc.replace(tzinfo=None).isoformat(),
                        "price": _live,
                        "pnl":   round(_pnl, 6),
                        "hold":  _hold,
                        "tid":   trade_id,
                    })
                logger.info(
                    "Virtual trade %s (%s) closed: STOP LOSS at %.4f (P&L %.2f%%)",
                    trade_id, ticker, _live, _pnl * 100,
                )
                # Clear the weakening timer now the trade is closed.
                clear_metadata(f"weakening_since:{trade_id}")
            except Exception as _exc:
                logger.error(
                    "Failed to close virtual trade %s on STOP LOSS: %s", trade_id, _exc
                )

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
        for enriched in pending:
            meta_key = f"trade_alert_status:{enriched.get('id')}"
            try:
                set_metadata(meta_key, enriched.get("status", ""))
            except Exception as exc:
                logger.warning(
                    "Could not stamp alert status for trade %s: %s",
                    enriched.get("id"), exc,
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


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler with all three daily jobs if not already running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    config    = _load_config()
    sched_cfg = config["scheduler"]
    tz        = sched_cfg["timezone"]

    _scheduler = BackgroundScheduler(timezone=tz)

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
    return _scheduler


def stop_scheduler() -> None:
    """Shut down the background scheduler gracefully."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    _scheduler = None
