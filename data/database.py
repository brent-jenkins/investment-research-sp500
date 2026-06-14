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

    # Aggregate
    confluence_score = Column(Float)   # mean of module scores
    signal = Column(String(10))        # BUY / SELL / NEUTRAL
    strength = Column(Float)           # 0-1 confidence

    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_signal_ticker_date"),
    )


class Trade(Base):
    """Hypothetical trades triggered by signals (for back-testing / journaling)."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    signal_id = Column(Integer, index=True)

    direction = Column(String(5), nullable=False)   # LONG / SHORT
    entry_date = Column(DateTime)
    entry_price = Column(Float)
    exit_date = Column(DateTime)
    exit_price = Column(Float)
    quantity = Column(Float)
    pnl = Column(Float)
    pnl_pct = Column(Float)
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


def init_db() -> None:
    """Create all tables in the database if they do not already exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)


def get_session() -> Session:
    """Return a new SQLAlchemy Session bound to the project database."""
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionLocal()
