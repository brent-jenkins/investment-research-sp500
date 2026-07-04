"""SQLAlchemy SQLite schema and session management for investment research."""

import os
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool


def _load_config() -> dict:
    """Load config.yaml from the project root (two levels up from this file)."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_db_path() -> Path:
    """Return the absolute path to the SQLite database file."""
    config = _load_config()
    project_root = Path(__file__).resolve().parent.parent
    db_path = project_root / config["database"]["path"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_engine():
    """Create and return a SQLAlchemy engine connected to the SQLite database."""
    db_path = get_db_path()
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


class Base(DeclarativeBase):
    pass


class PriceData(Base):
    """Daily OHLCV price data for each tracked asset."""

    __tablename__ = "price_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    adj_close = Column(Float)
    volume = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_price_ticker_date"),
    )


class Indicator(Base):
    """Computed technical indicators for each asset on each date."""

    __tablename__ = "indicators"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)

    # Trend
    sma_20 = Column(Float)
    sma_50 = Column(Float)
    sma_200 = Column(Float)
    ema_20 = Column(Float)
    macd = Column(Float)
    macd_signal = Column(Float)
    macd_hist = Column(Float)
    adx = Column(Float)
    plus_di = Column(Float)
    minus_di = Column(Float)

    # Mean reversion
    rsi = Column(Float)
    bb_upper = Column(Float)
    bb_middle = Column(Float)
    bb_lower = Column(Float)
    bb_pct_b = Column(Float)   # (close - lower) / (upper - lower)

    # Volatility
    atr = Column(Float)
    atr_pct = Column(Float)    # ATR / close
    hist_vol_20 = Column(Float)  # 20-day realised volatility (annualised)

    # Change point detection (analysis/volatility.py:detect_change_points())
    vol_regime = Column(String(10))            # LOW / NORMAL / HIGH / ELEVATED, relative to history
    structural_break = Column(Integer)         # 0 or 1 — a change point within cpd_recent_window days
    days_since_change_point = Column(Integer)  # NULL if no change point detected

    # Volume
    volume_sma_20 = Column(Float)
    volume_ratio = Column(Float)  # volume / volume_sma_20
    obv = Column(Float)
    obv_trend = Column(Float)     # OBV change over 20 days

    # Mean reversion (extra)
    bb_width = Column(Float)      # bb_upper - bb_lower
    z_score_52w = Column(Float)   # (close - 52w mean) / 52w std

    # Macro
    vix_level = Column(Float)
    vix_regime = Column(String(10))        # low / normal / high / extreme
    yield_curve_spread = Column(Float)     # 10Y - 2Y (FRED T10Y2Y)
    yield_curve_regime = Column(String(10))  # normal / flat / inverted

    # Cross-module
    golden_cross = Column(Float)   # 1 = golden, -1 = death, 0 = neither (positional only)
    ma_cross_score = Column(Float)  # direction-aware SMA50/SMA200 vote used in trend scoring; see analysis/trend.py:_ma_cross_vote

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_indicator_ticker_date"),
    )


class Signal(Base):
    """Confluence buy/sell signals generated for each asset on each date."""

    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)

    # Individual module scores (-1 = bearish, 0 = neutral, 1 = bullish)
    regime_score = Column(Integer)
    trend_score = Column(Integer)
    mean_reversion_score = Column(Integer)
    volatility_score = Column(Integer)
    volume_score = Column(Integer)
    macro_score = Column(Integer)
    correlation_score = Column(Integer)

    # Aggregate
    confluence_score = Column(Float)      # weighted score ∈ [-1, +1]
    signal = Column(String(15))           # STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    strength = Column(Float)              # |confluence_score|
    regime = Column(String(20))           # TRENDING_BULL / TRENDING_BEAR / etc.

    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_signal_ticker_date"),
    )


class Trade(Base):
    """Trades logged manually after replicating confluence signals, across multiple accounts."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    signal_id = Column(Integer, index=True)

    # Account context
    account_type = Column(String(10), default="PRACTICE")  # PRACTICE / LIVE
    account_name = Column(String(64), default="")
    account_id   = Column(String(32), default="")

    # Signal context at time of entry
    signal_type = Column(String(15))       # STRONG_BUY / BUY / SELL / STRONG_SELL
    confluence_score = Column(Float)
    regime = Column(String(20))
    trend_score = Column(Integer)
    mean_reversion_score = Column(Integer)
    volatility_score = Column(Integer)
    volume_score = Column(Integer)
    macro_score = Column(Integer)

    # Trade details
    direction = Column(String(5), nullable=False)   # LONG / SHORT
    entry_date = Column(DateTime)
    entry_price = Column(Float)
    exit_date = Column(DateTime)
    exit_price = Column(Float)
    quantity = Column(Float)

    # CFD / leverage details
    currency = Column(String(3), default="GBP")          # instrument currency (e.g. USD for US ETFs)
    account_currency = Column(String(3), default="GBP")  # account currency (e.g. GBP)
    fx_rate_at_entry = Column(Float, default=1.0)        # 1 account_ccy = X instrument_ccy at entry
    fx_rate_pair = Column(String(16))                    # canonical pair used e.g. 'GBPUSD'
    leverage = Column(Integer, default=1)
    investment_amount = Column(Float)                     # cash invested in account currency
    margin_required = Column(Float)                       # investment_amount / leverage
    overnight_rate = Column(Float, default=0.0)           # daily swap as % of basis amount
    swap_basis = Column(String(16), default="investment") # 'investment', 'margin', or 'position'
    position_value = Column(Float)                        # investment_amount × leverage
    spread_cost = Column(Float)                           # opening spread cost in account currency
    fx_fee_pct = Column(Float, default=0.0)              # T212 currency conversion fee % (0.50 for forex)
    platform_instrument = Column(String(32))              # T212 instrument name if different from ticker

    # Trade plan (set at entry, used for risk management)
    stop_loss_price = Column(Float)       # planned stop loss price
    take_profit_price = Column(Float)     # planned take profit price (preferred R:R target)
    risk_reward_ratio = Column(Float)     # preferred R:R at entry (e.g. 2.0)
    planned_risk_gbp = Column(Float)      # estimated GBP risk if SL is hit

    # Outcome (populated on close)
    pnl = Column(Float)
    pnl_pct = Column(Float)
    holding_days = Column(Integer)
    is_open = Column(Boolean, default=True)

    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Performance(Base):
    """Daily rolling performance metrics computed per asset."""

    __tablename__ = "performance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)

    # Returns
    daily_return = Column(Float)
    cumulative_return = Column(Float)
    drawdown = Column(Float)
    max_drawdown = Column(Float)

    # Risk-adjusted
    sharpe_ratio = Column(Float)    # rolling 252-day
    sortino_ratio = Column(Float)
    calmar_ratio = Column(Float)

    # Signal accuracy (if closed trades exist for the period)
    win_rate = Column(Float)
    avg_win = Column(Float)
    avg_loss = Column(Float)
    profit_factor = Column(Float)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_perf_ticker_date"),
    )


class CorrelationData(Base):
    """Computed cross-asset pair correlations stored once per day."""

    __tablename__ = "correlations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker_a = Column(String(20), nullable=False, index=True)
    ticker_b = Column(String(20), nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)

    corr_30d  = Column(Float)   # 30-day rolling correlation at latest date
    corr_90d  = Column(Float)   # 90-day rolling correlation at latest date
    hist_mean = Column(Float)   # historical mean of 30d rolling correlation
    hist_std  = Column(Float)   # historical std  of 30d rolling correlation
    z_score   = Column(Float)   # (corr_30d - hist_mean) / hist_std
    divergence = Column(String(12))  # CONFIRMING / DIVERGING / NEUTRAL
    pair_score = Column(Integer)     # -1 / 0 / 1

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker_a", "ticker_b", "date", name="uq_corr_pair_date"),
    )


class MacroIndicator(Base):
    """FRED macro indicator time series — one row per series per date."""

    __tablename__ = "macro_indicators"

    id = Column(Integer, primary_key=True, autoincrement=True)
    series_id = Column(String(40), nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)
    value = Column(Float)
    pct_change_30d = Column(Float)   # ~30-day pct change (M-o-M for monthly series)
    z_score_2y = Column(Float)       # (value − 2yr mean) / 2yr std
    score = Column(Integer)          # -1 / 0 / 1 for risk-asset scoring

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("series_id", "date", name="uq_macro_series_date"),
    )


class OpportunityFlag(Base):
    """Catch-up and regime-overlay opportunity flags detected during correlation analysis."""

    __tablename__ = "opportunity_flags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, nullable=False, index=True)       # analysis date flag was detected
    ticker = Column(String(20), nullable=False, index=True)   # asset with the opportunity
    flag_type = Column(String(30), nullable=False)            # CATCH_UP_OPPORTUNITY
    flag_source = Column(String(60))                          # e.g. "USO/XLE divergence"
    description = Column(Text)
    z_score = Column(Float)
    pair_corr_30d = Column(Float)
    price_at_flag = Column(Float)     # price when detected — baseline for confirmation
    return_5d = Column(Float)         # 5-day return after flag (populated on later runs)
    return_20d = Column(Float)        # 20-day return after flag (populated on later runs)
    confirmed = Column(Boolean)       # True when return confirmed the flag's direction
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "flag_type", "date", name="uq_flag_ticker_type_date"),
    )


class BacktestRun(Base):
    """Metadata for a single backtest run."""

    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    start_date = Column(Date)
    end_date = Column(Date)
    tickers_json = Column(Text)   # JSON list of tickers tested
    params_json = Column(Text)    # JSON snapshot of backtest config
    status = Column(String(20), default="pending")  # pending/running/complete/error
    error_message = Column(Text)

    # Summary metrics (populated on completion)
    total_trades = Column(Integer)
    win_rate = Column(Float)
    avg_return_pct = Column(Float)
    sharpe_ratio = Column(Float)
    max_drawdown_pct = Column(Float)
    total_return_pct = Column(Float)
    avg_holding_days = Column(Float)


class BacktestSignal(Base):
    """Daily signal for each ticker within a backtest run."""

    __tablename__ = "backtest_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False, index=True)
    ticker = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    regime = Column(String(30))
    confluence_score = Column(Float)
    signal = Column(String(20))
    trend_score = Column(Integer)
    mean_reversion_score = Column(Integer)
    volatility_score = Column(Integer)
    volume_score = Column(Integer)
    macro_score = Column(Integer)
    close_price = Column(Float)
    atr = Column(Float)
    hmm_bull_probability = Column(Float)  # HMM P(bull state) as of this date; NULL unless the rule-based call was TRENDING_BULL or MIXED — see analysis/hmm_regime.py
    hmm_downgraded = Column(Integer)      # 0/1 — 1 if the rule-based TRENDING_BULL call this date was downgraded to MIXED by the HMM confidence gate
    hmm_upgraded = Column(Integer)        # 0/1 — 1 if the rule-based MIXED call this date was upgraded to TRENDING_BULL by the HMM confidence gate

    __table_args__ = (
        UniqueConstraint("run_id", "ticker", "date", name="uq_bt_signal_run_ticker_date"),
    )


class BacktestTrade(Base):
    """Simulated trade produced by the backtest engine."""

    __tablename__ = "backtest_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False, index=True)
    ticker = Column(String(20), nullable=False, index=True)
    direction = Column(String(10))   # LONG / SHORT
    entry_date = Column(Date)
    exit_date = Column(Date)
    entry_price = Column(Float)
    exit_price = Column(Float)
    entry_signal = Column(String(20))
    exit_reason = Column(String(30))  # SIGNAL_REVERSAL / STOP_LOSS / MAX_HOLDING / END_OF_DATA
    pnl_pct = Column(Float)
    position_gbp = Column(Float)  # VaR-scaled position size used to size this simulated trade — see analysis/volatility.py:compute_var_position_size()
    pnl_gbp = Column(Float)       # position_gbp * pnl_pct — realised £ P&L for this simulated trade
    stop_distance_pct = Column(Float)  # stop distance used at entry, as % of entry price — GARCH or ATR, see analysis/backtest.py:_resolve_entry_stop()
    vol_source = Column(String(15))    # "garch" or "atr_fallback" — which formula sized stop_distance_pct
    regime_at_entry = Column(String(30))
    trend_at_entry = Column(Integer)
    mean_reversion_at_entry = Column(Integer)
    volatility_at_entry = Column(Integer)
    volume_at_entry = Column(Integer)
    macro_at_entry = Column(Integer)
    holding_days = Column(Integer)
    is_win = Column(Boolean)


class T212InvestOrder(Base):
    """Record of every T212 Invest API order attempt (demo and live).

    Written by brokers/trading212.py:_log_t212_order() regardless of whether
    the order succeeded or failed — provides a full audit trail.
    """

    __tablename__ = "t212_invest_orders"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    order_id         = Column(String(64))           # T212 API order ID; null if failed before placement
    ticker           = Column(String(20), nullable=False, index=True)  # internal ticker
    t212_ticker      = Column(String(64))           # T212 internal instrument ticker
    direction        = Column(String(5))            # BUY / SELL
    quantity         = Column(Float)                # shares ordered
    estimated_price  = Column(Float)                # price at time of order (from our DB)
    estimated_value_gbp = Column(Float)             # intended notional at order time; overwritten with true GBP cost basis on fill
    fill_price       = Column(Float)                # actual fill price (if later confirmed)
    filled_quantity  = Column(Float)               # actual quantity filled (may differ from ordered)
    status           = Column(String(20))           # PLACED / FILLED / FAILED / CANCELLED
    signal_type      = Column(String(15))           # STRONG_BUY / STRONG_SELL
    confluence_score = Column(Float)
    regime           = Column(String(20))
    account_type     = Column(String(10))           # DEMO / LIVE
    currency_code    = Column(String(10))           # instrument currency (USD, EUR, GBX, …)
    exit_reason      = Column(String(20))           # SELL_SIGNAL / STRONG_SELL_SIGNAL / NEUTRAL_CONFIRMED / TIME_LIMIT / STOP_LOSS; null for BUY orders
    var_pct          = Column(Float)                # holding-period VaR (as a fraction, e.g. -0.08 = -8%) used to size this BUY order; null for SELL orders and for orders placed under position_sizing: fixed
    stop_price       = Column(Float)                # GARCH (or ATR-fallback) stop loss price, instrument-native currency; null for SELL orders and for BUY orders placed before this feature / with use_garch_stops: false
    stop_distance_pct = Column(Float)               # stop distance as % of fill price (a percentage number, e.g. 3.5 = 3.5%); null alongside stop_price
    vol_source       = Column(String(15))           # "garch" or "atr_fallback" — which method produced stop_price; null alongside stop_price
    error_message    = Column(Text)
    placed_at        = Column(DateTime, default=datetime.utcnow)
    filled_at        = Column(DateTime)
    cancelled_at     = Column(DateTime)
    created_at       = Column(DateTime, default=datetime.utcnow)


class T212Pnl(Base):
    """Reconstructed realised P&L for closed T212 Invest positions.

    Built by brokers/t212_learning.py:_reconstruct_t212_pnl(), which pairs
    each FILLED BUY order in t212_invest_orders with the next FILLED SELL
    order for the same ticker (FIFO — this codebase only ever holds one open
    T212 position per ticker at a time, so BUY/SELL pairs never interleave).
    One row per closed position; never updated once written.
    """

    __tablename__ = "t212_pnl"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    ticker           = Column(String(20), nullable=False, index=True)  # internal research ticker
    t212_ticker      = Column(String(64))            # T212 instrument ticker
    buy_order_id     = Column(Integer, nullable=False)   # t212_invest_orders.id of the FILLED BUY row
    sell_order_id    = Column(Integer, nullable=False)   # t212_invest_orders.id of the FILLED SELL row
    entry_date       = Column(DateTime)               # BUY fill timestamp
    exit_date        = Column(DateTime)                # SELL fill timestamp
    entry_price      = Column(Float)                   # BUY fill_price, instrument-native currency
    exit_price       = Column(Float)                   # SELL fill_price, instrument-native currency
    quantity         = Column(Float)
    currency         = Column(String(10))               # instrument native currency (USD, EUR, GBX, …)
    pnl_gbp          = Column(Float)                    # realised P&L converted to GBP
    pnl_pct          = Column(Float)                    # fraction (0.02 = 2%); native-currency price return, no FX
    holding_days     = Column(Integer)
    signal_type      = Column(String(15))               # signal on the BUY order (e.g. STRONG_BUY)
    confluence_score = Column(Float)                    # confluence score on the BUY order
    regime           = Column(String(20))               # regime on the BUY order
    exit_reason      = Column(String(20))               # SELL_SIGNAL / STRONG_SELL_SIGNAL / NEUTRAL_CONFIRMED / TIME_LIMIT / STOP_LOSS
    stop_distance_pct = Column(Float)                    # stop distance (% of entry price) read from the BUY order row; null if opened before the GARCH-stop feature
    vol_source       = Column(String(15))                # "garch" or "atr_fallback" read from the BUY order row; null alongside stop_distance_pct
    # Module scores as of the BUY fill date — not stored on t212_invest_orders
    # itself, so looked up from the nearest signals row for (ticker, date).
    trend_score           = Column(Integer)
    mean_reversion_score  = Column(Integer)
    volatility_score      = Column(Integer)
    volume_score          = Column(Integer)
    macro_score           = Column(Integer)
    created_at       = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("buy_order_id", "sell_order_id", name="uq_t212_pnl_buy_sell"),
    )


class SystemMetadata(Base):
    """Key-value store for operational state (last refresh time, last analysis time, etc.)."""

    __tablename__ = "system_metadata"

    key = Column(String(64), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)


class SchemaMigration(Base):
    """Tracks named data fixes that have already been applied.

    Each row records a fix by a stable string name so _apply_data_fixes()
    can skip it on every subsequent startup without re-evaluating the guard
    condition or issuing the UPDATE at all.
    """

    __tablename__ = "schema_migrations"

    migration_name = Column(String(120), primary_key=True)
    applied_at = Column(DateTime, default=datetime.utcnow)


def set_metadata(key: str, value: str) -> None:
    """Upsert a key-value pair in system_metadata, recording the current UTC time.

    Creates the table with raw DDL if it does not yet exist so this function
    is safe to call even before init_db() has run (e.g. from background jobs
    that start before the first Streamlit render).
    """
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS system_metadata "
            "(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
        ))
        conn.execute(
            text(
                "INSERT OR REPLACE INTO system_metadata (key, value, updated_at) "
                "VALUES (:key, :value, :ts)"
            ),
            {"key": key, "value": value, "ts": datetime.utcnow().isoformat()},
        )


def get_metadata_str(key: str) -> "str | None":
    """Return the raw string value stored for *key*, or None if not set."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM system_metadata WHERE key = :key"),
                {"key": key},
            ).fetchone()
    except Exception:
        return None
    return str(row[0]) if row and row[0] is not None else None


def clear_metadata(key: str) -> None:
    """Delete *key* from system_metadata (no-op if the key does not exist)."""
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM system_metadata WHERE key = :key"), {"key": key})
    except Exception:
        pass


def get_metadata(key: str) -> "datetime | None":
    """Return the value stored for *key* as a datetime, or None if not set."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM system_metadata WHERE key = :key"),
                {"key": key},
            ).fetchone()
    except Exception:
        # Table does not exist yet — treat as no value recorded.
        return None
    if row is None:
        return None
    try:
        return datetime.fromisoformat(str(row[0]))
    except Exception:
        return None


def _apply_migrations(engine) -> None:
    """Add columns that were introduced after initial schema creation.

    SQLite does not support ALTER TABLE ADD COLUMN IF NOT EXISTS, so we attempt
    each ALTER and silently skip columns that already exist.
    """
    new_columns = [
        ("indicators", "obv_trend",           "FLOAT"),
        ("indicators", "bb_width",            "FLOAT"),
        ("indicators", "z_score_52w",         "FLOAT"),
        ("indicators", "vix_level",           "FLOAT"),
        ("indicators", "vix_regime",          "VARCHAR(10)"),
        ("indicators", "yield_curve_spread",  "FLOAT"),
        ("indicators", "yield_curve_regime",  "VARCHAR(10)"),
        ("indicators", "golden_cross",        "FLOAT"),
        ("indicators", "ma_cross_score",      "FLOAT"),
        ("indicators", "vol_regime",             "VARCHAR(10)"),
        ("indicators", "structural_break",       "INTEGER"),
        ("indicators", "days_since_change_point","INTEGER"),
        ("signals",    "regime",              "VARCHAR(20)"),
        ("signals",    "correlation_score",   "INTEGER"),
        ("trades",     "signal_type",         "VARCHAR(15)"),
        ("trades",     "confluence_score",    "FLOAT"),
        ("trades",     "regime",              "VARCHAR(20)"),
        ("trades",     "trend_score",         "INTEGER"),
        ("trades",     "mean_reversion_score","INTEGER"),
        ("trades",     "volatility_score",    "INTEGER"),
        ("trades",     "volume_score",        "INTEGER"),
        ("trades",     "macro_score",         "INTEGER"),
        ("trades",     "holding_days",        "INTEGER"),
        ("trades",     "account_type",        "VARCHAR(10)  DEFAULT 'PRACTICE'"),
        ("trades",     "account_name",        "VARCHAR(64)  DEFAULT 'Trading212 Practice'"),
        ("trades",     "account_id",          "VARCHAR(32)  DEFAULT 't212_practice'"),
        ("trades",     "currency",            "VARCHAR(3)   DEFAULT 'GBP'"),
        ("trades",     "account_currency",    "VARCHAR(3)   DEFAULT 'GBP'"),
        ("trades",     "fx_rate_at_entry",    "FLOAT        DEFAULT 1.0"),
        ("trades",     "fx_rate_pair",        "VARCHAR(16)"),
        ("trades",     "leverage",            "INTEGER      DEFAULT 1"),
        ("trades",     "investment_amount",   "FLOAT"),
        ("trades",     "margin_required",     "FLOAT"),
        ("trades",     "overnight_rate",      "FLOAT        DEFAULT 0.0"),
        ("trades",     "swap_basis",          "VARCHAR(16)  DEFAULT 'investment'"),
        ("trades",     "position_value",      "FLOAT"),
        ("trades",     "spread_cost",         "FLOAT"),
        ("trades",     "fx_fee_pct",          "FLOAT        DEFAULT 0.0"),
        ("trades",     "platform_instrument", "VARCHAR(32)"),
        ("trades",     "stop_loss_price",     "FLOAT"),
        ("trades",     "take_profit_price",   "FLOAT"),
        ("trades",     "risk_reward_ratio",   "FLOAT"),
        ("trades",          "planned_risk_gbp",      "FLOAT"),
        ("t212_invest_orders", "estimated_value_gbp", "FLOAT"),
        ("t212_invest_orders", "filled_quantity",     "FLOAT"),
        ("t212_invest_orders", "currency_code",       "TEXT"),
        ("t212_invest_orders", "exit_reason",         "TEXT"),
        ("t212_invest_orders", "var_pct",             "FLOAT"),
        ("t212_invest_orders", "stop_price",          "FLOAT"),
        ("t212_invest_orders", "stop_distance_pct",   "FLOAT"),
        ("t212_invest_orders", "vol_source",          "VARCHAR(15)"),
        ("t212_pnl",           "stop_distance_pct",   "FLOAT"),
        ("t212_pnl",           "vol_source",          "VARCHAR(15)"),
        ("backtest_trades",    "position_gbp",        "FLOAT"),
        ("backtest_trades",    "pnl_gbp",              "FLOAT"),
        ("backtest_trades",    "stop_distance_pct",   "FLOAT"),
        ("backtest_trades",    "vol_source",          "VARCHAR(15)"),
        ("backtest_signals",   "hmm_bull_probability","FLOAT"),
        ("backtest_signals",   "hmm_downgraded",      "INTEGER"),
        ("backtest_signals",   "hmm_upgraded",        "INTEGER"),
    ]

    for table, column, col_type in new_columns:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        except Exception:
            pass  # column already exists — each ALTER TABLE gets its own transaction
                  # so a duplicate-column error never poisons subsequent statements


def _migration_applied(engine, name: str) -> bool:
    """Return True if *name* has already been recorded in schema_migrations."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM schema_migrations WHERE migration_name = :n"),
                {"n": name},
            ).first()
        return row is not None
    except Exception:
        return False


def _record_migration(engine, name: str) -> None:
    """Insert *name* into schema_migrations so it is skipped on future startups."""
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO schema_migrations "
                    "(migration_name, applied_at) VALUES (:n, :t)"
                ),
                {"n": name, "t": datetime.utcnow()},
            )
    except Exception:
        pass


def _apply_data_fixes(engine) -> None:
    """One-time idempotent data corrections applied after migrations.

    Each fix is keyed by a stable name in schema_migrations.  Once recorded,
    the fix is skipped entirely on subsequent startups.  A value-based guard
    inside each UPDATE provides a safe fallback for DBs that predate the
    schema_migrations table.
    """
    # --- fix_inda_entry_price ---
    if not _migration_applied(engine, "fix_inda_entry_price"):
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE trades SET
                        entry_price         = 49.39,
                        quantity            = 4.077272,
                        direction           = 'SHORT',
                        currency            = 'USD',
                        account_currency    = 'GBP',
                        leverage            = 5,
                        investment_amount   = 150.0,
                        margin_required     = 29.96,
                        position_value      = 750.0,
                        overnight_rate      = 0.0251,
                        platform_instrument = 'INDA',
                        entry_date          = '2026-06-15 00:00:00'
                    WHERE ticker = 'INDA'
                      AND is_open = 1
                      AND ABS(COALESCE(entry_price, 0) - 49.39) > 0.001
                """))
            _record_migration(engine, "fix_inda_entry_price")
        except Exception:
            pass

    # --- batch: open-trade field corrections (fx_fee_pct + misc) ---
    _open_trade_fixes = [
        (
            "fix_INDA_fx_fee_pct",
            "INDA",
            "margin_required = 29.96, fx_fee_pct = 0.50",
        ),
        (
            "fix_KBE_fx_fee_pct",
            "KBE",
            "margin_required = 29.90, leverage = 5, overnight_rate = 0.0694, fx_fee_pct = 0.50",
        ),
        (
            "fix_GBPUSD_fx_fee_pct",
            "GBPUSD=X",
            "margin_required = 5.00, leverage = 30, overnight_rate = 0.0, fx_fee_pct = 0.50",
        ),
        (
            "fix_USDJPY_fx_fee_pct",
            "USDJPY=X",
            "quantity = 201.0075, entry_price = 160.221, margin_required = 4.99, "
            "leverage = 30, overnight_rate = 0.0, fx_fee_pct = 0.50",
        ),
    ]
    for _mig_name, _ticker, _set_clause in _open_trade_fixes:
        if _migration_applied(engine, _mig_name):
            continue
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"UPDATE trades SET {_set_clause} "  # noqa: S608
                        f"WHERE ticker = :t AND is_open = 1 "
                        f"AND (fx_fee_pct IS NULL OR fx_fee_pct < 0.1)"
                    ),
                    {"t": _ticker},
                )
            _record_migration(engine, _mig_name)
        except Exception:
            pass

    # --- fix_pnl_pct_fraction_convention ---
    # Recalculate pnl_pct for all non-VIRTUAL closed trades as a fraction
    # (exit_price - entry_price) / entry_price × direction sign.
    # Previously leveraged trades stored return-on-margin; non-leveraged stored
    # the percentage (* 100).  All consumers (briefing, backtest, virtual) expect
    # a fraction, so normalise everything to that convention.
    if not _migration_applied(engine, "fix_pnl_pct_fraction_convention"):
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE trades
                    SET pnl_pct = CASE
                        WHEN direction = 'LONG' THEN (exit_price - entry_price) / entry_price
                        ELSE (entry_price - exit_price) / entry_price
                    END
                    WHERE is_open = 0
                      AND COALESCE(account_type, 'PRACTICE') != 'VIRTUAL'
                      AND entry_price > 0
                      AND exit_price IS NOT NULL
                """))
            _record_migration(engine, "fix_pnl_pct_fraction_convention")
        except Exception:
            pass

    # --- purge_zero_close_price_rows ---
    # Delete price_data rows where close is NULL or 0.  These are incomplete
    # intraday bars stored by an earlier version of _upsert_price_rows that
    # did not filter them out.  A single bad row in the rolling window breaks
    # the SMA line on the chart for all subsequent dates in the window.
    # Not keyed via _migration_applied because new bad rows can appear after
    # any incremental pull; the DELETE is a no-op when no bad rows exist so
    # it is safe to run on every startup.
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM price_data WHERE close IS NULL OR close <= 0"
            ))
    except Exception:
        pass


def _purge_unlisted_tickers(engine) -> None:
    """Delete rows for tickers no longer present in config.yaml watchlist.

    Reads the current watchlist to build the allowed set, then removes any
    row from price_data, indicators, signals, and correlations whose ticker
    is not in that set.  Runs on every startup; DELETE on absent rows is a
    no-op so this is always safe.

    Correlations pairs are pruned when EITHER ticker_a or ticker_b has been
    removed from the watchlist.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Top-level config.yaml section names — never valid ticker symbols.
    # Excluded from stale-ticker detection so that rows erroneously written
    # under a config key name (from any past mis-parse) are silently skipped
    # rather than being logged as purge targets on every startup.
    _CONFIG_SECTIONS: frozenset[str] = frozenset({
        "watchlist", "virtual_trading", "signals", "scheduler",
        "notifications", "macro", "learning", "database", "data",
        "confluence", "backtest", "accounts", "trading212_invest",
    })

    try:
        config = _load_config()
    except Exception as exc:
        logger.warning("_purge_unlisted_tickers: could not load config — skipping. %s", exc)
        return

    allowed: set[str] = set()
    for assets in config.get("watchlist", {}).values():
        if not isinstance(assets, list):
            continue
        for entry in assets:
            if isinstance(entry, dict) and "ticker" in entry:
                allowed.add(entry["ticker"])

    if not allowed:
        logger.warning("_purge_unlisted_tickers: watchlist is empty — skipping to avoid data loss.")
        return

    tables = ("price_data", "indicators", "signals")
    for table in tables:
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text(f"SELECT DISTINCT ticker FROM {table}")  # noqa: S608
                )
                stale = [
                    row[0] for row in result
                    if row[0] not in allowed and row[0] not in _CONFIG_SECTIONS
                ]
            for ticker in stale:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"DELETE FROM {table} WHERE ticker = :t"),  # noqa: S608
                        {"t": ticker},
                    )
                logger.info("Purged %s from %s (no longer in watchlist).", ticker, table)
        except Exception as exc:
            logger.warning("_purge_unlisted_tickers: error cleaning %s: %s", table, exc)

    # Correlations table uses ticker_a / ticker_b columns.
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT DISTINCT ticker_a, ticker_b FROM correlations")
            )
            stale_pairs = [
                (a, b) for a, b in result
                if (a not in allowed or b not in allowed)
                and a not in _CONFIG_SECTIONS and b not in _CONFIG_SECTIONS
            ]
        for ticker_a, ticker_b in stale_pairs:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "DELETE FROM correlations WHERE ticker_a = :a AND ticker_b = :b"
                    ),
                    {"a": ticker_a, "b": ticker_b},
                )
            logger.info(
                "Purged correlation pair (%s, %s) from correlations.", ticker_a, ticker_b
            )
    except Exception as exc:
        logger.warning("_purge_unlisted_tickers: error cleaning correlations: %s", exc)


def init_db() -> None:
    """Create all tables in the database if they do not already exist.

    Explicitly references every model class so Base.metadata is fully
    populated before create_all() runs.  SQLAlchemy's declarative system
    registers classes automatically at definition time when they live in
    the same module, but the explicit list below makes the dependency
    visible and keeps things correct if models are ever split into separate
    files (where they would need to be imported first).
    """
    # Importing Base from this module already pulls in all six classes, but
    # listing them here makes the requirement unambiguous and enforced.
    _register = (
        PriceData,
        Indicator,
        Signal,
        Trade,
        Performance,
        CorrelationData,
        MacroIndicator,
        OpportunityFlag,
        SystemMetadata,
        SchemaMigration,
        BacktestRun,
        BacktestSignal,
        BacktestTrade,
    )
    if not all(c.__tablename__ in Base.metadata.tables for c in _register):
        raise RuntimeError(
            "One or more model classes are not registered with Base.metadata — "
            "check that all models inherit from the same Base instance."
        )

    engine = get_engine()
    Base.metadata.create_all(engine)
    _apply_migrations(engine)
    _apply_data_fixes(engine)
    _purge_unlisted_tickers(engine)


def get_session() -> Session:
    """Return a new SQLAlchemy Session bound to the project database."""
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionLocal()
