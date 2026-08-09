"""Shared pytest fixtures for the investment-research test suite.

No test in this suite touches the real project database, calls yfinance, or
calls the live Trading212 API. DB-touching tests use the ``db_engine``
fixture below, which creates a fresh in-memory SQLite database (schema from
data.database.Base) and patches every module-level ``get_engine`` binding
that production code actually calls at runtime.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

# Ensure the project root (parent of tests/) is importable as `analysis`,
# `data`, `brokers`, etc. regardless of the directory pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.universe import clear_membership_cache  # noqa: E402
from data.database import Base  # noqa: E402

# Every module that does `from data.database import get_engine` at module
# level (or inside a function, which re-resolves at call time) binds its own
# copy of the name — patching data.database.get_engine alone does not affect
# module-level imports elsewhere. Listed here once so every test file that
# needs a DB just depends on the db_engine fixture instead of repeating this.
_GET_ENGINE_BINDINGS = (
    "data.database.get_engine",
    "data.ingestion.get_engine",
    "output.performance.get_engine",
    "analysis.universe.get_engine",
    "auth.get_engine",
)


@pytest.fixture
def db_engine(monkeypatch):
    """A fresh in-memory SQLite engine with the full production schema.

    Uses StaticPool so the single underlying connection — and therefore the
    in-memory database — survives across the many separate `get_engine()`
    calls production code makes (each normally opens a brand-new connection
    via NullPool against a real file; StaticPool keeps one connection alive
    so an in-memory database behaves the same way across those calls).

    Also clears analysis.universe's in-process membership cache on both
    entry and exit (2026-08-09) -- that cache is now bulk-populated from
    whichever engine get_engine() resolves to at first access (see
    analysis/universe.py:_bulk_load_membership_cache()), so a value left
    over from an earlier test's engine (this fixture hands out a brand new
    one per test) would otherwise silently outlive its own DB and leak into
    a later test that expects a genuinely fresh one -- exactly the failure
    mode test_universe.py's own narrower autouse fixture already guarded
    against for that one file; this closes the same gap for every other
    test file that uses db_engine.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    for binding in _GET_ENGINE_BINDINGS:
        monkeypatch.setattr(binding, lambda: engine)

    clear_membership_cache()
    yield engine
    clear_membership_cache()
    engine.dispose()
