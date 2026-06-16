"""SQLAlchemy SQLite schema and session management for investment research."""

import os
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import (
    Boolean,
    Column,
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
    )
    # Enable WAL mode for better concurrent read performance
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
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
    golden_cross = Column(Float)   # 1 = golden, -1 = death, 0 = neither

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
    platform_instrument = Column(String(32))              # T212 instrument name if different from ticker

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


class SystemMetadata(Base):
    """Key-value store for operational state (last refresh time, last analysis time, etc.)."""

    __tablename__ = "system_metadata"

    key = Column(String(64), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)


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
        ("trades",     "platform_instrument", "VARCHAR(32)"),
    ]
    for table, column, col_type in new_columns:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        except Exception:
            pass  # column already exists — each ALTER TABLE gets its own transaction
                  # so a duplicate-column error never poisons subsequent statements


def _apply_data_fixes(engine) -> None:
    """One-time idempotent data corrections applied after migrations.

    Each fix is guarded by a condition that is False once the fix has already
    been applied, so it is safe to call on every startup.
    """
    with engine.begin() as conn:
        # Fix: set all correct values for the initial INDA SHORT CFD trade.
        # Guard: only runs while entry_price is not already 49.39 (within 0.001),
        # so it is safe to call on every startup without overriding user edits.
        conn.execute(text("""
            UPDATE trades SET
                entry_price         = 49.39,
                quantity            = 4.077272,
                direction           = 'SHORT',
                currency            = 'USD',
                account_currency    = 'GBP',
                leverage            = 5,
                investment_amount   = 150.0,
                margin_required     = 30.0,
                position_value      = 750.0,
                overnight_rate      = 0.0251,
                platform_instrument = 'INDA',
                entry_date          = '2026-06-15 00:00:00'
            WHERE ticker = 'INDA'
              AND is_open = 1
              AND ABS(COALESCE(entry_price, 0) - 49.39) > 0.001
        """))

    # Remove stale tickers that are no longer in the watchlist from signals and
    # indicators tables.  DELETE on non-existent rows is a no-op, so this is safe
    # to run on every startup.
    _STALE_TICKERS = ("PALL", "PPLT", "WOOD", "UGA", "VUSA.L", "ISF.L")
    for ticker in _STALE_TICKERS:
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM signals    WHERE ticker = :t"), {"t": ticker})
                conn.execute(text("DELETE FROM indicators WHERE ticker = :t"), {"t": ticker})
        except Exception:
            pass


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


def get_session() -> Session:
    """Return a new SQLAlchemy Session bound to the project database."""
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionLocal()
