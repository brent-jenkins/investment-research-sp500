"""Shared setup for every re-run script in this investigation. Points every
production module this backtest re-run touches at THIS investigation's own
isolated working copy of the production DB
(research/curated40_rolling40_dax_reverify_2026-08-05/work.db) rather than
the real production database -- the standing "isolated clone, own copied
DB, never production" research discipline this project applies throughout
CLAUDE_HISTORY.md. No production file (config.yaml, data/database.py, or
any file under analysis/ or brokers/) is ever modified by this or any
sibling script -- only monkeypatched in-process, for this process's own
lifetime.

Only two modules touched by analysis.backtest's own import graph bind
get_engine() into their own namespace at import time (`from data.database
import get_engine`): analysis.universe and data.ingestion. Both are
patched here, explicitly, rather than patching data.database.get_engine
itself (which would have no effect on either module's already-bound
reference).
"""
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(r"c:\GitHub\investment-research-sp500")
_WORK_DB = Path(__file__).resolve().parent.parent / "work.db"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

_engine = None


def get_research_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            f"sqlite:///{_WORK_DB}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
    return _engine


def patch_all():
    import data.ingestion as ingestion
    import analysis.universe as universe
    import data.database as database

    ingestion.get_engine = get_research_engine
    universe.get_engine = get_research_engine
    database.get_engine = get_research_engine  # defensive, for completeness
    return {
        "work_db_path": str(_WORK_DB),
        "work_db_exists": _WORK_DB.exists(),
        "work_db_size_bytes": _WORK_DB.stat().st_size if _WORK_DB.exists() else None,
    }


def load_config():
    import yaml

    with open(_REPO_ROOT / "config.yaml", "r") as f:
        return yaml.safe_load(f)
