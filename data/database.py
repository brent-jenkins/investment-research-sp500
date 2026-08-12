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
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
    admission_rank_value = Column(Float)  # the value _load_pending_orders() actually sorts by (2026-08-09) -- see that function's own docstring; equals momentum_pct in the default admission_ranking_mode, the 12-minus-1 relative-rank value when the mode is switched
    # relative_rank_pct (2026-08-09, forward shadow comparison) -- ALWAYS
    # computed and stored, regardless of admission_ranking_mode, so the
    # shadow simulation at submission time can evaluate BOTH rankings for
    # every candidate, not only whichever one is currently active.
    relative_rank_pct = Column(Float)
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


class TickerDataQuality(Base):
    """Per-ticker identity-verification verdict for the historical/delisted
    stock population (2026-08-07) -- see research/recycled_ticker_
    investigation_2026-08-07/REPORT.md for the finding this exists to
    remediate: 22 of the 408 tickers added by the 2026-08-06 survivorship-
    bias remediation are recycled-ticker-symbol corruption (an unrelated
    entity's price history under a delisted company's old symbol), with a
    further 17 flagged-but-unconfirmed by a reversal-based classifier later
    proven to have a real blind spot (PALM: monotone, so classified as a
    genuine corporate action, but its entire stored history postdates the
    real company's 2010 delisting).

    Written by analysis.ingestion.verify_ticker_identity() (the ONE shared
    identity check -- called by the one-off remediation script, the
    ingestion-time screen on any newly-backfilled ticker, and the periodic
    backstop scan; never re-implemented at a second call site) via
    write_data_quality_status() below.

    status:
      QUARANTINED  -- excluded from research/backtest candidacy via
                      analysis/universe.py:get_research_stock_candidates().
                      Fired whenever the date-coverage check alone fails
                      (stored price_data extends >90 days past the
                      ticker's last real index-membership end_date -- a
                      positive proof of impossibility, sufficient on its
                      own) or both checks fail together. Rows are NEVER
                      deleted -- flag, not repair; the bad rows are the
                      evidence base for validating the detector and
                      justifying each exclusion.
      CLEAN        -- both checks passed (or the quoteType check passed
                      and the date-coverage check does not apply, i.e. the
                      ticker's membership window never closed). Eligible
                      for research/backtest candidacy.
      NEEDS_REVIEW -- the date-coverage check passed (or does not apply)
                      but the quoteType check alone failed, or yfinance
                      .info was unavailable/errored -- corroborating-only
                      evidence, deliberately NOT sufficient on its own to
                      quarantine. Stays un-quarantined until a human
                      adjudicates.

    Does NOT affect get_live_stock_candidates()/get_all_known_stock_
    tickers() -- confirmed via the investigation that zero quarantined
    tickers are, or can be, reachable via the live candidate path today
    (every quarantined ticker is delisted-before-today by construction),
    so this table is consulted only by the research-only gate.
    """

    __tablename__ = "ticker_data_quality"

    ticker      = Column(String(20), primary_key=True)
    status      = Column(String(16), nullable=False)  # QUARANTINED | CLEAN | NEEDS_REVIEW
    reason      = Column(Text)
    evidence    = Column(Text)  # JSON: quote_type, exchange, long_name, date_overrun_days, prior reversal classification, regime summary
    detected_at = Column(DateTime, default=datetime.utcnow)


class TickerSector(Base):
    """Per-ticker sector classification, cached from yfinance's `.info`
    'sector' field — added 2026-08-09 for sector-concentration monitoring
    (Dashboard + weekly summary, reporting only; see CLAUDE.md's "Sector
    concentration monitoring" section). Deliberately separate from, and
    never routed through, TickerDataQuality/verify_ticker_identity() above
    — that mechanism exists to catch recycled-ticker-symbol identity
    fraud in the historical/delisted population; sector classification is
    an unrelated concern (what business a ticker is in, not whether its
    price history genuinely belongs to it), and conflating the two risks
    exactly the kind of domain-scoping mistake this project has already
    been bitten by once (see "Data-quality backstop" — a check applied
    outside the population/purpose it was built for).

    A row exists here after any fetch attempt, successful or not — the
    presence of a row (not the value of `sector`) is what signals "already
    attempted, don't re-fetch," so a ticker yfinance has no sector for is
    cached as genuinely unresolved (sector=NULL) rather than being
    silently re-queried on every subsequent lookup. Written by
    write_ticker_sector() below, called from exactly one place:
    prewarm_sector_data.py's bulk pre-warm script (repo root) — never from
    the Dashboard or weekly-email render path (2026-08-09 fix: those read
    via get_ticker_sector() only, a pure DB read, so a page render or email
    generation can never block on a live yfinance call).

    sector NULL (or no row at all — the render path treats both the same)
    means "no sector to show right now" — display layers render this as
    "Uncategorized", never crash, and it still counts toward aggregate
    position/capital totals (see output.performance.compute_sector_
    breakdown()'s own docstring) — a ticker with no resolvable sector is
    still a real open position and must not silently drop out of the
    totals.
    """

    __tablename__ = "ticker_sector"

    ticker     = Column(String(20), primary_key=True)
    sector     = Column(String(64))  # NULL = fetched, yfinance had no sector for this ticker
    source     = Column(String(32), default="yfinance")
    fetched_at = Column(DateTime, default=datetime.utcnow)


class TickerFundamentals(Base):
    """Point-in-time raw quarterly fundamentals, captured going forward.

    NOTHING READS THIS BACK INTO ANY TRADING DECISION. It is not consulted by
    candidacy, ranking, sizing, or exits, and must not be. If fundamentals are
    ever used in a decision it will be after a pre-registered study, not by
    accretion -- see CLAUDE.md's "Point-in-time fundamentals recorder" section.
    This is a data-collection option bought cheaply, not a study.

    WHY THIS TABLE EXISTS AT ALL: point-in-time fundamentals (what was actually
    knowable on a historical date, not today's restated figures) are not
    obtainable free, which makes the idea untestable retrospectively. The only
    way to get a vintage-correct dataset for this universe is to start
    recording one. Every row therefore carries BOTH `reporting_period` (the
    fiscal period the figures describe) and `fetched_at` (when we could first
    have known them). Without the reporting period the data is not
    point-in-time, it is merely undated.

    PROVENANCE IS THE MOST IMPORTANT COLUMN HERE, and the two kinds must NEVER
    be pooled in an analysis:

      'point_in_time' -- captured forward, at candidate-list time, before the
          outcome existed. The ONLY rows that can ever test whether
          fundamentals predict outcome.
      'restated'      -- backfilled historical quarters fetched today. These
          are the CURRENT version of a past quarter, not what was published and
          knowable at the time. Companies revise prior periods, and acquisitive
          companies revise them retrospectively.

    Pooling them would reproduce exactly the lookahead contamination that closed
    the FTSE and curated-list ideas -- and, the part that actually matters,
    **restated and original figures look identical in the data**, so the
    contamination would be undetectable after the fact. There is no way to
    recover the distinction later; it exists only because it is recorded at
    write time.

    What each kind is good for:
      * restated  -- characterising the DATA: coverage depth per ticker, label
        stability over time, field-presence rates. NOT usable for any claim
        about what was knowable on a historical decision date.
      * point_in_time -- the only sample that can support an outcome study.

    RAW FIELDS ONLY, NEVER DERIVED SCORES. Ratios are computable later from raw
    values; a stored "quality score" would permanently lock in whichever
    definition happened to be current the night it was written.

    QUARTERLY, not annual -- decided by measurement, not preference. The
    2026-08-12 coverage probe found annual statements carry a median reporting
    lag of 224 days against quarterly's 43, i.e. the typical annual figure
    describes a fiscal year that ended over seven months before the decision
    sitting beside it. For a point-in-time dataset that is self-defeating. The
    cost is 6-8pp lower field coverage on revenue/net_income, which self-heals
    at the next quarterly fetch, whereas a stale vintage never does.

    `field_aliases` records WHICH yfinance row label produced each value.
    Labels have changed between yfinance versions ("Total Stockholder Equity"
    -> "Stockholders Equity"), so a future rename shows up as a coverage drop
    against a known baseline rather than as silent nulls in a table nobody
    looks at for two years -- which is how several of this project's worst
    problems started.

    DELIBERATELY never routed through verify_ticker_identity() or
    scan_unverified_tickers(). That mechanism catches recycled-ticker-symbol
    identity fraud in the historical/delisted population; fundamentals capture
    is an unrelated concern on a live-candidate population, and conflating the
    two would repeat a domain-scoping error this project has now made three
    times.
    """

    __tablename__ = "ticker_fundamentals"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    ticker           = Column(String(20), nullable=False, index=True)
    reporting_period = Column(String(10), nullable=False)   # fiscal period END (YYYY-MM-DD)
    statement_type   = Column(String(16), nullable=False, default="quarterly")
    provenance       = Column(String(16), nullable=False, default="point_in_time", index=True)

    total_debt          = Column(Float)
    total_equity        = Column(Float)
    total_assets        = Column(Float)
    revenue             = Column(Float)
    net_income          = Column(Float)
    operating_cash_flow = Column(Float)
    free_cash_flow      = Column(Float)
    shares_outstanding  = Column(Float)

    field_aliases       = Column(Text)   # JSON: field -> yfinance row label that matched
    period_by_statement = Column(Text)   # JSON: balance/income/cash -> period end
    source              = Column(String(32), default="yfinance")
    fetched_at          = Column(DateTime, default=datetime.utcnow)

    # NO uniqueness on (ticker, reporting_period): a re-fetch whose values
    # DIFFER appends a new row rather than updating in place, so a restatement
    # becomes observable data instead of a silent change. Flag-don't-delete,
    # applied to values rather than rows. Costs rows and nothing else.


class CandidateFundamentalsSnapshot(Base):
    """Which fundamentals row was CURRENT for each gate-passing candidate on
    each decision night -- the point-in-time link.

    Captured at CANDIDATE-LIST time, not entry time, and deliberately so:
    recording only admitted positions would yield ~30 observations a year under
    the current configuration and give no control group at all. Recording every
    gate-passing candidate (~300/night) preserves both the admitted and the
    rejected populations, which is what makes any future comparison possible.

    Kept as a thin link rather than a copy of the figures: `pending_orders`
    already records the nightly candidate population, but it is PURGED on a
    30-day retention window, so it cannot serve as the durable candidate
    history this needs. These rows have no retention limit.

    `fundamentals_id` is NULLABLE on purpose -- a candidate whose fundamentals
    have not been fetched yet (the pool refreshes in nightly chunks) is recorded
    with NULL rather than skipped, so "we had no data for this candidate on this
    night" is a durable, queryable fact rather than an absence indistinguishable
    from the job never having run.

    READ BY NOTHING IN THE DECISION PATH. See TickerFundamentals above.
    """

    __tablename__ = "candidate_fundamentals_snapshot"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    decision_date   = Column(String(10), nullable=False, index=True)   # YYYY-MM-DD
    ticker          = Column(String(20), nullable=False, index=True)
    fundamentals_id = Column(Integer)      # NULL = no fundamentals known that night
    recorded_at     = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("decision_date", "ticker", name="uq_cand_fund_date_ticker"),
    )


class AdmissionRankingShadow(Base):
    """Forward-only evidence for the relative-ranking promotion's OPEN
    CANDIDATE decision (2026-08-09) — see CLAUDE.md's "Relative-rank
    admission ordering promoted to production code" and "Forward shadow
    comparison" sections. This is the only forward evidence stream for
    that decision; the backtest research (`research/relative_ranking_
    prereg_2026-08-09/`, `research/relranking_robustness_2026-08-09/`) is
    historical, not live.

    Records, for EVERY gate-passing candidate the EOD decide phase queues,
    what BOTH admission-ranking modes (absolute_momentum, relative_rank)
    would do with it — rank position and admit/reject verdict — computed
    ONCE, at the real submission step (brokers/trading212.py:
    _submit_pending_orders()), from the EXACT SAME eligible pool and
    capacity state (total_val/invested/free_cash) the real order-placement
    walk itself uses, via the one shared `_capacity_fit_reason()` function
    — never an independently re-derived approximation computed later from
    different state.

    Self-contained: every field needed for later reporting is copied here
    directly, never joined back to `pending_orders` — PENDING rows are
    genuinely DELETED after `pending_orders_retention_days` (30) once
    resolved (see `purge_old_pending_orders()`), so a row here that merely
    referenced `pending_orders` by id would silently lose its own display
    fields once that retention window passed. This table has no such
    retention limit; it is the durable record.

    **The central, structural limitation of a live single-account shadow,
    stated here so it is never mistaken for an oversight**: only ONE
    ranking mode is ever genuinely live-traded at a time (whichever
    `trading212_invest.admission_ranking_mode` says — see `active_mode`
    below). A REAL, realised outcome can only ever exist for the candidate
    the ACTIVE mode actually admitted and bought (`real_buy_order_id` gets
    set when that happens; outcome fields populate once that position
    closes, via `pnl`). A candidate the INACTIVE mode would have uniquely
    admitted was never bought under real (or demo) money and NEVER WILL
    have a realised outcome for THIS specific decision — this is not a
    "small sample, wait longer" gap the way the backtest's own gates were;
    it is permanent for any row where the inactive ranking's pick differs
    from the active one. `outcome_available` distinguishes "no outcome
    yet, because the position the active mode bought hasn't closed" (False,
    `real_buy_order_id` set) from "no outcome will ever exist, because
    this candidate was never actually traded" (False, `real_buy_order_id`
    NULL) — see `output/performance.py:compute_admission_ranking_shadow_
    summary()`'s own docstring for how the display keeps this distinction
    explicit rather than silently blending the two into one number.
    """

    __tablename__ = "admission_ranking_shadow"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    # pending_orders.id at write time -- NOT a DB-enforced foreign key
    # (pending_orders rows are genuinely deleted after their own retention
    # window; this column exists only to make the write idempotent -- see
    # write_admission_ranking_shadow_row()'s own docstring -- and is never
    # read back via a join once written).
    pending_order_id  = Column(Integer, nullable=False, unique=True)
    decided_at        = Column(DateTime, nullable=False, index=True)
    ticker            = Column(String(20), nullable=False)
    is_etf            = Column(Boolean, nullable=False, default=False)
    momentum_pct      = Column(Float)
    relative_rank_pct = Column(Float)
    position_gbp      = Column(Float)
    # 1-indexed priority position that day, within this candidate's own
    # asset class, under each ranking -- 1 is the strongest candidate.
    momentum_rank_position       = Column(Integer)
    relative_rank_rank_position  = Column(Integer)
    admitted_under_absolute_momentum = Column(Boolean, nullable=False)
    admitted_under_relative_rank     = Column(Boolean, nullable=False)
    # The admission_ranking_mode actually driving REAL submission for this
    # decision -- resolve_admission_ranking_mode(config), read once at
    # write time. Determines which of the two "admitted_under_*" columns
    # a real trade (if any) can ever be attributed to.
    active_mode       = Column(String(24), nullable=False)
    # invest_orders.id of the real BUY, set only if the active mode
    # actually admitted (and the real submission loop actually placed)
    # this candidate -- see set_admission_ranking_shadow_buy_order().
    real_buy_order_id = Column(Integer)
    outcome_available = Column(Boolean, nullable=False, default=False)
    exit_reason       = Column(String(20))
    pnl_gbp           = Column(Float)
    pnl_pct           = Column(Float)
    closed_at         = Column(DateTime)
    recorded_at       = Column(DateTime, default=datetime.utcnow)
    outcome_checked_at = Column(DateTime)


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


class BenchmarkComparison(Base):
    """One row per day pairing the account's real total value against what
    the SAME starting capital would be worth had it been bought and held in
    ^SP500TR from the account's own start date — the buy-and-hold
    comparison this system's actual objective is stated against (2026-08-10,
    reporting only; see CLAUDE.md's "Benchmark-relative performance tracker"
    section).

    ^SP500TR, not ^GSPC, deliberately: price-only excludes dividends and
    would flatter this system by roughly 1.5-2%/yr, and every research run
    in this project already benchmarks against the total-return series, so
    this keeps the dashboard figure comparable to the backtests rather than
    introducing a second, quietly-different baseline. ^GSPC remains the
    crisis-trigger series and the two are not interchangeable.

    Written by the EOD job (data/scheduler.py:_refresh_benchmark_
    comparison()), never computed at render time — the render path reads
    these stored rows only, matching the open-position-cards/sector/crisis
    pattern this project established after repeated render-path stalls. No
    new scheduled job: this piggybacks on the EOD cycle that already writes
    AccountValueHistory above.

    ANCHORED, NEVER BACKFILLED. benchmark_value_gbp is always
    `anchor_balance * (sp500tr_close(date) / sp500tr_close(anchor_date))`,
    where anchor_date/anchor_balance come from the EARLIEST real
    AccountValueHistory row. There is deliberately no synthetic history for
    dates before the account existed — if the account's own history is
    short, the comparison is short, and the display says so rather than
    extending it. anchor_date/anchor_balance_gbp are stored on every row
    rather than inferred at read time, so a row stays self-describing and a
    future anchor change is visible in the data instead of silently
    rewriting the whole series' meaning.

    Upserted on `date`, matching AccountValueHistory's own convention — a
    same-day re-run refreshes that day's row rather than duplicating it.
    """

    __tablename__ = "benchmark_comparison"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    date                = Column(Date, nullable=False, unique=True)
    account_value_gbp   = Column(Float)
    benchmark_value_gbp = Column(Float)
    # The raw ^SP500TR close used for this date, kept for auditability --
    # so a surprising spread can be traced to the exact input without
    # re-deriving it from price_data as it stands later.
    benchmark_close     = Column(Float)
    anchor_date         = Column(Date)
    anchor_balance_gbp  = Column(Float)
    computed_at         = Column(DateTime, default=datetime.utcnow)


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


def write_data_quality_status(ticker: str, status: str, reason: str, evidence: str) -> None:
    """Upsert a ticker_data_quality row -- same sqlite_insert(...).
    on_conflict_do_update(...) primitive _upsert_price_rows() uses. A
    re-check overwrites status/reason/evidence/detected_at for the same
    ticker; the row itself is never deleted (flag, not repair -- see
    TickerDataQuality's own docstring)."""
    engine = get_engine()
    stmt = sqlite_insert(TickerDataQuality).values(
        ticker=ticker, status=status, reason=reason, evidence=evidence,
        detected_at=datetime.utcnow(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker"],
        set_={
            "status": stmt.excluded.status,
            "reason": stmt.excluded.reason,
            "evidence": stmt.excluded.evidence,
            "detected_at": stmt.excluded.detected_at,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def get_data_quality_status(ticker: str) -> "dict | None":
    """Return {"status", "reason", "evidence", "detected_at"} for *ticker*,
    or None if it has never been checked."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, reason, evidence, detected_at FROM ticker_data_quality "
                "WHERE ticker = :t"
            ),
            {"t": ticker},
        ).fetchone()
    if row is None:
        return None
    return {"status": row[0], "reason": row[1], "evidence": row[2], "detected_at": row[3]}


def write_ticker_sector(ticker: str, sector: "str | None") -> None:
    """Upsert a ticker_sector row — same sqlite_insert(...).on_conflict_do_update()
    primitive write_data_quality_status() uses. sector=None is a valid,
    intentional write (see TickerSector's own docstring: "fetched, nothing
    found" is cached too, not left as a permanent per-lookup re-fetch)."""
    engine = get_engine()
    stmt = sqlite_insert(TickerSector).values(
        ticker=ticker, sector=sector, source="yfinance", fetched_at=datetime.utcnow(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker"],
        set_={"sector": stmt.excluded.sector, "fetched_at": stmt.excluded.fetched_at},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def get_ticker_sector(ticker: str) -> "dict | None":
    """Return {"sector", "fetched_at"} for *ticker*, or None if it has
    never been looked up (as opposed to looked up and found unresolved,
    which returns {"sector": None, ...})."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT sector, fetched_at FROM ticker_sector WHERE ticker = :t"),
            {"t": ticker},
        ).fetchone()
    if row is None:
        return None
    return {"sector": row[0], "fetched_at": row[1]}


def write_admission_ranking_shadow_row(
    *, pending_order_id: int, decided_at, ticker: str, is_etf: bool,
    momentum_pct: "float | None", relative_rank_pct: "float | None", position_gbp: "float | None",
    momentum_rank_position: "int | None", relative_rank_rank_position: "int | None",
    admitted_under_absolute_momentum: bool, admitted_under_relative_rank: bool, active_mode: str,
) -> None:
    """Insert one admission_ranking_shadow row — INSERT and silently do
    nothing on a (pending_order_id) conflict, never an upsert. This is
    computed exactly once, from the pristine pre-loop capacity state, at
    real submission time (see brokers/trading212.py:_submit_pending_
    orders()) — a retried submission attempt for the same pending row
    (e.g. after a transient failure earlier in the same run, or a
    subsequent run that still finds the row PENDING) must never overwrite
    an already-recorded shadow snapshot with a different, later, already-
    mutated capacity state.

    decided_at accepts either a datetime or an ISO-format string — the
    real caller (_load_pending_orders(), a raw SQL SELECT) returns
    pending_orders.decided_at as a plain string, unlike the ORM's own
    DateTime column type, which SQLAlchemy's Core insert().values()
    enforces strictly (unlike a raw text() INSERT, which never validates
    Python-side types at all -- confirmed directly: this is why
    PendingOrder's own decided_at column can be written as a string
    elsewhere in this codebase without incident, while this ORM-level
    insert cannot)."""
    if isinstance(decided_at, str):
        decided_at = datetime.fromisoformat(decided_at)
    engine = get_engine()
    stmt = sqlite_insert(AdmissionRankingShadow).values(
        pending_order_id=pending_order_id, decided_at=decided_at, ticker=ticker, is_etf=is_etf,
        momentum_pct=momentum_pct, relative_rank_pct=relative_rank_pct, position_gbp=position_gbp,
        momentum_rank_position=momentum_rank_position,
        relative_rank_rank_position=relative_rank_rank_position,
        admitted_under_absolute_momentum=admitted_under_absolute_momentum,
        admitted_under_relative_rank=admitted_under_relative_rank, active_mode=active_mode,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["pending_order_id"])
    with engine.begin() as conn:
        conn.execute(stmt)


def set_admission_ranking_shadow_buy_order(pending_order_id: int, invest_order_id: int) -> None:
    """Record which real invest_orders row (a BUY) resulted from this
    shadow decision, once the real submission loop actually places one —
    only ever called for a candidate the ACTIVE mode admitted. Never
    raises; a failure here must not affect the real order-placement loop
    it's called from (the caller wraps this in its own try/except too, per
    defense in depth, matching this project's established pattern for
    every other best-effort DB write in brokers/trading212.py)."""
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE admission_ranking_shadow SET real_buy_order_id = :oid "
                    "WHERE pending_order_id = :pid"
                ),
                {"oid": invest_order_id, "pid": pending_order_id},
            )
    except Exception:
        pass


def get_unreconciled_admission_ranking_shadow_rows() -> list[dict]:
    """Shadow rows with a real BUY attached but no outcome recorded yet —
    candidates for reconcile_admission_ranking_shadow_outcomes() to check
    against the (separately, already-existing) pnl table."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, real_buy_order_id FROM admission_ranking_shadow "
            "WHERE real_buy_order_id IS NOT NULL AND outcome_available = 0"
        )).mappings().all()
    return [dict(r) for r in rows]


def set_admission_ranking_shadow_outcome(
    shadow_id: int, *, exit_reason: str, pnl_gbp: float, pnl_pct: float, closed_at,
) -> None:
    """Populate a shadow row's realised outcome, read directly from the
    existing pnl table (never recomputed — see reconcile_admission_
    ranking_shadow_outcomes()'s own docstring for why this reuses pnl
    reconstruction rather than reimplementing it)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE admission_ranking_shadow
            SET outcome_available = 1, exit_reason = :er, pnl_gbp = :pg, pnl_pct = :pp,
                closed_at = :ca, outcome_checked_at = :now
            WHERE id = :id
        """), {
            "er": exit_reason, "pg": pnl_gbp, "pp": pnl_pct, "ca": closed_at,
            "now": datetime.utcnow().isoformat(), "id": shadow_id,
        })


def get_all_admission_ranking_shadow_rows() -> list[dict]:
    """Every recorded shadow row, oldest first — the raw material
    output/performance.py:compute_admission_ranking_shadow_summary()
    aggregates. A plain, unfiltered SELECT — small enough (one row per
    gate-passing EOD candidate) that filtering in Python is simpler than a
    family of narrower queries, and this is never called from a render
    path more than once per cache TTL."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT decided_at, ticker, is_etf, momentum_pct, relative_rank_pct, position_gbp, "
            "momentum_rank_position, relative_rank_rank_position, "
            "admitted_under_absolute_momentum, admitted_under_relative_rank, active_mode, "
            "real_buy_order_id, outcome_available, exit_reason, pnl_gbp, pnl_pct, closed_at "
            "FROM admission_ranking_shadow ORDER BY decided_at ASC"
        )).mappings().all()
    return [dict(r) for r in rows]


def get_admission_ranking_shadow_first_decided_at() -> "datetime | None":
    """The earliest decided_at recorded in admission_ranking_shadow, or
    None if the table is empty — the anchor the 90-day review-trigger date
    is derived from (see CLAUDE.md's "Forward shadow comparison" section
    for why this must be derived, not hardcoded at build time)."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT MIN(decided_at) FROM admission_ranking_shadow")).fetchone()
    if row is None or row[0] is None:
        return None
    val = row[0]
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(str(val))


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
        # Admission-ranking promotion (2026-08-09) -- see PendingOrder's own
        # column comment. ALTER TABLE ADD COLUMN leaves this NULL for every
        # pre-existing row, and that is NOT self-correcting: the paired
        # backfill in _apply_data_fixes() (backfill_admission_rank_value) is
        # what actually makes this column safe to sort by. See that fix's own
        # comment for the 2026-08-10 incident that proved an unbackfilled
        # sort key silently reorders the real live submission walk.
        ("pending_orders", "admission_rank_value", "REAL"),
        # Forward shadow comparison (2026-08-09) -- momentum_pct was already
        # stored; relative_rank_pct is the OTHER ranking's own value, needed
        # so the shadow simulation (brokers/trading212.py:_simulate_dual_
        # ranking_admission()) can compute BOTH rankings' verdicts at
        # submission time regardless of which mode is currently active.
        ("pending_orders", "relative_rank_pct", "REAL"),
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

    # --- backfill_admission_rank_value ---
    # Populate pending_orders.admission_rank_value for rows written before
    # that column existed (added by _apply_migrations() 2026-08-09, which
    # like every ALTER TABLE ADD COLUMN leaves pre-existing rows NULL).
    #
    # Why this is required and not merely tidy (2026-08-10 incident): both
    # the live submission walk (brokers/trading212.py:_load_pending_orders())
    # and the Status page's Candidate Queue sort
    # `ORDER BY admission_rank_value DESC, decided_at ASC`. The original
    # migration shipped with a comment reasoning that a NULL sorts last in
    # DESC and is therefore harmlessly deprioritised -- true only while SOME
    # row carries a value. On the first deploy every unresolved row was
    # pre-migration, so ALL 308 were NULL, the primary key contributed no
    # ordering at all, and the sort collapsed onto the unique `decided_at`
    # tiebreak -- i.e. EOD insertion order, which is the alphabetically
    # sorted candidate pool. The live 14:30 UTC submission walk would have
    # processed candidates in alphabetical order rather than by momentum.
    # That same comment also assumed pre-existing unresolved rows "should not
    # exist in practice"; they are in fact the normal steady state, since the
    # queue is decided at 21:30 and not submitted until the next session.
    #
    # Backfilling from momentum_pct is unconditionally correct regardless of
    # the configured admission_ranking_mode: a NULL means the row was decided
    # before the column existed, i.e. by pre-promotion code, which ranked on
    # absolute momentum by definition. It is NOT a stand-in for
    # resolve_admission_rank_value() on new rows -- those are written with a
    # real value at decide time by maybe_place_orders().
    #
    # Not keyed via _migration_applied(): this must also repair rows written
    # by any future ALTER-only column addition to a sort key, and the UPDATE
    # is a no-op once no NULL remains, so it is safe on every startup.
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE pending_orders SET admission_rank_value = momentum_pct "
                "WHERE admission_rank_value IS NULL AND momentum_pct IS NOT NULL"
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

    Races the identical check in another process on a genuine first
    deploy: the `investment-research` and `investment-research-scheduler`
    systemd services both call init_db() independently on startup, and
    could both see an empty `users` table if they start close together.
    `username` is UNIQUE, so the loser's INSERT raises IntegrityError,
    caught here and treated as "someone else already seeded it" rather
    than left to crash that service's startup. Only ever possible on the
    very first deploy after this table is introduced — every later
    startup finds a non-empty table and returns before either branch
    below runs.
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

    from sqlalchemy.exc import IntegrityError

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, password_hash, role, created_at) "
                    "VALUES (:u, :h, 'admin', :t)"
                ),
                {"u": "admin", "h": password_hash, "t": datetime.utcnow()},
            )
    except IntegrityError:
        # Another process won the race and already inserted 'admin' -- do
        # NOT log generated_password below; it was never actually stored.
        return

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
        TickerDataQuality,
        TickerSector,
        TickerFundamentals,
        CandidateFundamentalsSnapshot,
        AdmissionRankingShadow,
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
