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


class UniverseMembership(Base):
    """Point-in-time index-membership windows for the tracked stock universe
    (see analysis/universe.py).

    One row per contiguous membership window — a ticker that was added,
    removed, and later re-added to its index (e.g. AMD's 2013-2017 S&P 500
    gap) gets two rows, not one row with a hole. `source` and `fetched_at`
    record where and when each window was last confirmed, so a stale row
    left behind by a source that later changes its mind is identifiable
    rather than silently trusted forever. Refreshed by
    analysis/universe.py:refresh_membership_from_source() — see that module
    for the mechanism that keeps this from becoming a static, decaying
    snapshot.
    """

    __tablename__ = "universe_membership"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    ticker       = Column(String(20), nullable=False, index=True)
    start_date   = Column(Date, nullable=False)
    end_date     = Column(Date, nullable=False)
    source       = Column(String(120), nullable=False)   # e.g. "fja05680/sp500"
    fetched_at   = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "start_date", name="uq_membership_ticker_start"),
    )


class InvestOrder(Base):
    """Record of every order attempt on the T212 Invest account (demo and
    live) — brokers/trading212.py:_log_order() writes one row per attempt,
    success or failure, giving a full audit trail.
    """

    __tablename__ = "invest_orders"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    order_id         = Column(String(64))           # T212 API order ID; null if failed before placement
    ticker           = Column(String(20), nullable=False, index=True)  # internal research ticker
    t212_ticker      = Column(String(64))           # T212 internal instrument ticker
    direction        = Column(String(5))            # BUY / SELL
    quantity         = Column(Float)
    estimated_price  = Column(Float)
    estimated_value_gbp = Column(Float)
    fill_price       = Column(Float)
    filled_quantity  = Column(Float)
    status           = Column(String(20))           # PLACED / FILLED / FAILED / CANCELLED / REJECTED
    is_etf           = Column(Boolean)               # True for ETF instruments, False for individual stocks
    momentum_pct     = Column(Float)                 # trailing 12m return that triggered this BUY; null for SELL
    stop_price       = Column(Float)                 # ATR-based stop fixed at entry; null for SELL orders
    account_type     = Column(String(10))            # DEMO / LIVE
    currency_code    = Column(String(10))
    exit_reason      = Column(String(20))            # CRISIS_EXIT / INDEX_REMOVAL / STOP_LOSS / SIGNAL_OFF / MAX_HOLDING; null for BUY
    var_pct          = Column(Float)                 # holding-period VaR used to size this BUY; null for SELL
    error_message    = Column(Text)
    # Broker-side stop-loss tracking (2026-08-04, see CLAUDE.md's
    # "Broker-side stop order" section) -- only ever populated on a BUY
    # row, once that row's own status genuinely transitions to FILLED
    # (brokers/trading212.py:poll_placed_orders() places a real T212
    # POST /equity/orders/stop at stop_price, sized to this row's own
    # filled quantity). stop_order_status: PLACED (a real T212 order_id
    # was obtained) / FAILED (placement attempt failed) / CANCELLED (the
    # standing stop was cancelled before a non-STOP_LOSS exit -- see
    # maybe_exit_positions()'s cancel-fix) / ALREADY_RESOLVED (a cancel
    # attempt 404'd -- the stop had likely already fired) / CANCEL_FAILED
    # (a cancel attempt failed for a reason other than "already
    # resolved"). Null on every SELL row and on any BUY row placed
    # before this feature existed (see the backfill task in CLAUDE.md
    # for pre-existing open positions).
    stop_order_id     = Column(String(64))
    stop_order_status = Column(String(20))
    stop_order_note   = Column(Text)
    placed_at        = Column(DateTime, default=datetime.utcnow)
    filled_at        = Column(DateTime)
    cancelled_at     = Column(DateTime)
    created_at       = Column(DateTime, default=datetime.utcnow)


class Pnl(Base):
    """Reconstructed realised P&L for closed T212 Invest positions.

    Built by brokers/trading212.py:_reconstruct_pnl(), which FIFO-pairs each
    FILLED BUY order in invest_orders with the next FILLED SELL order for
    the same ticker (this codebase only ever holds one open position per
    ticker at a time, so BUY/SELL pairs never interleave). One row per
    closed position; never updated once written.
    """

    __tablename__ = "pnl"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    ticker           = Column(String(20), nullable=False, index=True)
    t212_ticker      = Column(String(64))
    buy_order_id     = Column(Integer, nullable=False)   # invest_orders.id of the FILLED BUY row
    sell_order_id    = Column(Integer, nullable=False)   # invest_orders.id of the FILLED SELL row
    entry_date       = Column(DateTime)
    exit_date        = Column(DateTime)
    entry_price      = Column(Float)
    exit_price       = Column(Float)
    quantity         = Column(Float)
    currency         = Column(String(10))
    pnl_gbp          = Column(Float)
    pnl_pct          = Column(Float)
    holding_days     = Column(Integer)
    is_etf           = Column(Boolean)
    momentum_pct_at_entry = Column(Float)            # trailing 12m return on the BUY order
    exit_reason      = Column(String(20))            # CRISIS_EXIT / INDEX_REMOVAL / STOP_LOSS / SIGNAL_OFF / MAX_HOLDING
    created_at       = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("buy_order_id", "sell_order_id", name="uq_pnl_buy_sell"),
    )


class PendingOrder(Base):
    """A queued BUY decision awaiting exchange-timed submission.

    Decouples *when the signal is computed* (still EOD, unchanged) from
    *when the resulting order is actually submitted* — brokers/trading212.py
    :maybe_place_orders() writes a PENDING row per qualifying entry instead
    of placing the order immediately; submit_pending_etf_orders() /
    submit_pending_stock_orders() (each on its own instrument's real trading-
    session schedule) later read these rows and submit them, after a
    price-sanity check against `decided_price`. See CLAUDE.md's
    "Exchange-aware order submission" section.
    """

    __tablename__ = "pending_orders"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    ticker           = Column(String(20), nullable=False, index=True)
    t212_ticker      = Column(String(64))
    is_etf           = Column(Boolean, nullable=False)
    currency         = Column(String(10))
    decided_at       = Column(DateTime, default=datetime.utcnow)
    decided_price    = Column(Float)             # native-currency close used to size/sanity-check against
    position_gbp     = Column(Float)
    momentum_pct     = Column(Float)
    stop_price       = Column(Float)
    var_pct          = Column(Float)
    status           = Column(String(24), default="PENDING")  # PENDING / SUBMITTED / FAILED / SKIPPED_PRICE_MOVED / SKIPPED_ALREADY_HELD / REJECTED_CAPACITY / SKIPPED_CRISIS_BLOCKED / SUPERSEDED
    resolution_note  = Column(Text)
    resolved_at      = Column(DateTime)
    created_at       = Column(DateTime, default=datetime.utcnow)


class InstrumentResolution(Base):
    """T212 instrument-ID mapping for a live-trading candidate ticker,
    verified against T212's own get_instruments() metadata before the
    ticker is ever allowed to trade — see
    brokers/trading212.py:resolve_and_cache_instrument() and
    resolve_instrument_candidate() for the mechanism. Replaces the old
    hand-curated config.yaml `instruments:` mapping (verified manually,
    once, for a fixed 40 tickers) now that the live candidate pool is the
    full, dynamically-refreshed S&P 500 membership
    (analysis/universe.py:get_live_stock_candidates()) — new index
    entrants need this resolved automatically, not by another manual pass.

    status:
      VERIFIED — t212_instrument_id/currency confirmed against T212's own
                 metadata (a shortName match on a real US-listed equity
                 instrument, the same check that would have caught the
                 XLF/XLK wrong-instrument bug immediately had it existed
                 then — see CLAUDE.md's ETF mapping audit section).
                 Safe to trade.
      FAILED   — checked against real T212 metadata and no confident,
                 unambiguous match was found. Excluded from trading,
                 flagged for manual review via `note`. Never
                 auto-retried — a human needs to look at it (delete the
                 row, or a future admin action, to force re-resolution).
      PENDING  — resolution has not yet succeeded for an OPERATIONAL
                 reason (get_instruments() call failed/errored), not a
                 genuine mismatch — retried automatically on the next
                 attempt. Excluded from trading until it resolves.
    """

    __tablename__ = "instrument_resolutions"

    ticker              = Column(String(20), primary_key=True)
    t212_instrument_id  = Column(String(64))
    currency            = Column(String(10))
    status              = Column(String(16), nullable=False, default="PENDING")
    note                = Column(Text)
    attempted_at        = Column(DateTime, default=datetime.utcnow)
    resolved_at         = Column(DateTime)


class PositionReconciliation(Base):
    """A discrepancy found between what this system's own order history
    implies is open and what T212's real, live positions list actually
    shows -- see brokers/trading212.py:reconcile_positions_with_broker()
    and CLAUDE.md's "Live position reconciliation" section (2026-08-02).

    Added once the live candidate pool became the full, dynamically-
    tracked S&P 500 (~500 tickers) rather than a fixed 40 -- at that
    scale, delistings/acquisitions/corporate actions on a held position
    are a routine, ongoing occurrence, not a rare edge case, and
    _reconstruct_pnl() can only ever record a closed position when THIS
    system placed a FILLED SELL for it -- it has no way to notice a
    position the broker closed on its own (a cash-out acquisition) or
    converted into a different instrument (a stock-swap acquisition).

    event_type:
      BROKER_CLOSED_NO_SELL -- our own order history's latest FILLED BUY
        for a t212_ticker has no matching FILLED SELL (we believe the
        position is still open), but T212's real positions list does not
        show it held, and this system never placed a SELL for it. Most
        likely a cash-out acquisition the broker processed automatically.
        Exact proceeds are NOT known -- estimated_value_gbp is left null
        rather than fabricated; note explains what's known.
      UNRECOGNIZED_POSITION -- T212 shows a currently-held instrument
        with no VERIFIED instrument_resolutions mapping. Most likely a
        stock-swap acquisition that converted a previously-tracked
        position into a different, unmapped instrument.
      REPEATED_SELL_REJECTION -- several consecutive SELL attempts for a
        still-held position have failed or been rejected -- likely
        delisted/bankrupt with no real market to sell into. Auto-retry
        is stopped once this fires (see _has_recent_sell_order()) so the
        position isn't hammered with a fresh doomed sell attempt every
        cycle; flagged here instead for manual review/write-off.
      STOP_LOSS (2026-08-04) -- a locally-believed-open position closed,
        and this system's own broker-side stop order (recorded PLACED on
        the BUY row) is confirmed FILLED via T212's order history --
        i.e. the broker-side stop fired and closed it, exactly as
        designed. See brokers\trading212.py:_record_broker_stop_closure().
        Recorded for the audit trail with status=ACKNOWLEDGED
        immediately (never OPEN) -- this is expected, healthy behaviour,
        not a discrepancy needing review, and is deliberately excluded
        from send_position_reconciliation_alert()'s trigger condition.
        Unlike every other event_type here, this one also causes a
        synthetic FILLED SELL row (exit_reason=STOP_LOSS) to be written
        to invest_orders, so realised P&L and the Dashboard's
        "Performance by Exit Reason" section reflect the trade
        correctly -- without it, a broker-stop-fired closure would never
        appear in the pnl table at all (no SELL was ever placed BY this
        system to pair against the BUY), not merely be mislabeled.

    status: OPEN (needs manual review) / ACKNOWLEDGED. For
    BROKER_CLOSED_NO_SELL/UNRECOGNIZED_POSITION/REPEATED_SELL_REJECTION,
    nothing in code currently transitions OPEN -> ACKNOWLEDGED
    automatically -- that is a manual action (e.g. a future Status page
    control), matching InstrumentResolution's FAILED status, which is
    the same "flag for a human, never auto-resolve a genuine mismatch"
    contract. STOP_LOSS is the one exception: it is written already
    ACKNOWLEDGED, because it is self-explained at the moment it's
    recorded, not a genuine mismatch awaiting a human decision.

    Upserted on (event_type, t212_ticker) while status='OPEN' so a
    recurring discrepancy refreshes its own note/detected_at on every
    reconciliation run rather than accumulating duplicate rows.
    """

    __tablename__ = "position_reconciliations"

    id                   = Column(Integer, primary_key=True, autoincrement=True)
    event_type           = Column(String(32), nullable=False)
    ticker               = Column(String(20))          # our research ticker, if known
    t212_ticker          = Column(String(64), nullable=False)
    quantity             = Column(Float)
    estimated_value_gbp  = Column(Float)
    note                 = Column(Text)
    status               = Column(String(16), nullable=False, default="OPEN")
    detected_at          = Column(DateTime, default=datetime.utcnow)
    last_seen_at         = Column(DateTime, default=datetime.utcnow)
    resolved_at          = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("event_type", "t212_ticker", name="uq_reconciliation_event_ticker"),
    )


class AccountValueHistory(Base):
    """One row per calendar day of the T212 account's total value, cash,
    and invested amount — the real, historical time series the
    dashboard's week/month/year account-value chart is built from.

    Captured as part of the EOD job's own existing daily account-snapshot
    fetch (brokers/trading212.py-adjacent data/scheduler.py:_fetch_t212_
    snapshot(config, record_history=True) — called with that flag ONLY
    from _eod_job(), never from the 60-second dispatcher's or the hourly
    job's own calls to the same function, so this stays a genuine
    one-row-per-day series rather than accumulating many rows a day).
    Essentially free: no new T212 API call — one more DB write right
    after a fetch that already happens daily, using the exact same
    already-fetched summary dict `t212_account_snapshot` (system_metadata)
    is built from.

    Upserted on `date` (not append-only) — a manual same-day "Refresh
    Data" re-run must refresh that day's row, not create a duplicate.
    """

    __tablename__ = "account_value_history"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    date             = Column(Date, nullable=False, unique=True)
    total_value_gbp  = Column(Float)
    cash_gbp         = Column(Float)
    invested_gbp     = Column(Float)
    captured_at      = Column(DateTime, default=datetime.utcnow)


class User(Base):
    """A per-user web UI account — replaces the single shared Caddy
    basic-auth credential (see CLAUDE.md's per-user-accounts section).
    Passwords are never stored in plaintext; password_hash is a bcrypt
    hash (see auth.py:hash_password()/verify_password()), and every
    write to this table goes through auth.py rather than raw SQL/ORM
    calls elsewhere, so hashing and role validation can't be bypassed.

    role: "admin" (full access, including this table's own management UI
    on the Users page) / "demo" (Dashboard only — a read-only investor
    view, page-gated in main.py).
    """

    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(64), nullable=False, unique=True, index=True)
    password_hash = Column(String(120), nullable=False)
    role          = Column(String(16), nullable=False, default="demo")
    created_at    = Column(DateTime, default=datetime.utcnow)


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
    new_columns: list[tuple[str, str, str]] = [
        # Broker-side stop-loss tracking (2026-08-04) -- see InvestOrder's
        # own column comments above for what each column means.
        ("invest_orders", "stop_order_id", "TEXT"),
        ("invest_orders", "stop_order_status", "TEXT"),
        ("invest_orders", "stop_order_note", "TEXT"),
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

    Only the unconditional price_data guard survives here — every
    migration-keyed fix that used to run in this function targeted the
    `trades` table, removed in the 2026-07-31 V1 removal.
    """
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


def _seed_default_admin(engine) -> None:
    """Create the initial admin account on first run, if the `users` table
    is empty — nothing to do on every later startup.

    Prefers the ADMIN_PASSWORD_HASH env var: a portable bcrypt hash lifted
    verbatim from the pre-existing Caddy basic_auth credential this table
    replaces (bcrypt's hash format is implementation-agnostic — a hash
    Caddy generated verifies identically here), so the existing password
    keeps working unchanged as the new admin account, with the real
    plaintext never having to be known or re-entered. Falls back to a
    freshly generated random password, logged once, for a brand-new
    environment with no prior credential to migrate (e.g. local dev).
    """
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    if count:
        return

    password_hash = os.environ.get("ADMIN_PASSWORD_HASH")
    generated_password = None
    if not password_hash:
        import secrets as _secrets

        import bcrypt

        generated_password = _secrets.token_urlsafe(16)
        password_hash = bcrypt.hashpw(
            generated_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (username, password_hash, role, created_at) "
                "VALUES (:u, :h, 'admin', :t)"
            ),
            {"u": "admin", "h": password_hash, "t": datetime.utcnow()},
        )

    if generated_password:
        import logging

        logging.getLogger(__name__).warning(
            "No ADMIN_PASSWORD_HASH set — created default admin account "
            "'admin' with a freshly generated password: %s "
            "(change this immediately via the Users page; this password is "
            "logged only once, at first startup)",
            generated_password,
        )


def init_db() -> None:
    """Create all tables in the database if they do not already exist.

    Explicitly references every model class so Base.metadata is fully
    populated before create_all() runs.  SQLAlchemy's declarative system
    registers classes automatically at definition time when they live in
    the same module, but the explicit list below makes the dependency
    visible and keeps things correct if models are ever split into separate
    files (where they would need to be imported first).
    """
    _register = (
        PriceData,
        UniverseMembership,
        InvestOrder,
        Pnl,
        PendingOrder,
        InstrumentResolution,
        AccountValueHistory,
        User,
        SystemMetadata,
        SchemaMigration,
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
    _seed_default_admin(engine)


def get_session() -> Session:
    """Return a new SQLAlchemy Session bound to the project database."""
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionLocal()
