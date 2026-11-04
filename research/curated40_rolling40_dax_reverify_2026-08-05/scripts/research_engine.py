"""Standalone DB shim for the resurrected analysis/market_cap.py (deleted
from production 2026-08-02, commit 0753cc6 "Remove market_cap.py/ETF-audit
dead code"). Points market_cap_research.py's get_engine()/MarketCapHistory
at this investigation's OWN isolated working copy of the production DB
(research/curated40_rolling40_dax_reverify_2026-08-05/work.db), never
production's real database — matching this project's standing "isolated
clone with its own copied DB" research discipline. price_data and
universe_membership already exist in work.db (copied from production
2026-08-05); market_cap_history is created fresh here since it was dropped
from production before this research was undertaken.
"""
import os
from datetime import date, datetime

from sqlalchemy import Column, Integer, String, Float, Date, DateTime, create_engine, UniqueConstraint
from sqlalchemy.orm import declarative_base

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work.db")

Base = declarative_base()


class MarketCapHistory(Base):
    """Reconstructed schema, matching the deleted production table exactly
    (see git show 140ca37~1:data/database.py) -- one row per
    (ticker, period_end_date), sourced fresh from SEC EDGAR XBRL for this
    investigation."""

    __tablename__ = "market_cap_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    period_end_date = Column(Date, nullable=False)
    shares_outstanding = Column(Float, nullable=False)
    source = Column(String(80), nullable=False)
    cik = Column(String(12))
    filed_date = Column(Date)
    fetched_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("ticker", "period_end_date", name="uq_ticker_period_end"),)


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{_DB_PATH}", future=True)
        Base.metadata.create_all(_engine, tables=[MarketCapHistory.__table__])
    return _engine
