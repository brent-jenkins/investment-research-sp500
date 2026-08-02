"""Structural validation for config.yaml.

The live trading candidate pool and each ticker's T212 instrument mapping
are both sourced dynamically (analysis/universe.py:
get_live_stock_candidates(), brokers/trading212.py:
resolve_and_cache_instrument()) -- see CLAUDE.md's "wire the momentum-only,
full-S&P-500 selection into live execution" section. config.yaml itself
holds only reference_tickers (monitoring-only, never an execution mapping)
and runtime parameters. These tests load the real config.yaml (not a
synthetic fixture) so a structural regression is caught here rather than
discovered live.
"""
from pathlib import Path

import pytest
import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture(scope="module")
def config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def test_old_structures_are_gone(config):
    """watchlist:/instruments: (the old two-structure, then one-structure,
    hand-curated instrument mapping) must not come back -- the live
    candidate pool and T212 mapping are dynamic now, and reintroducing a
    parallel static list would recreate the exact "two sources of truth
    that can drift apart" problem both those structures caused in turn."""
    assert "watchlist" not in config
    assert "instruments" not in config
    assert "instrument_mapping" not in config["trading212_invest"]


def test_reference_tickers_section_exists_and_is_nonempty(config):
    assert "reference_tickers" in config
    assert isinstance(config["reference_tickers"], list)
    assert len(config["reference_tickers"]) > 0


def test_reference_tickers_never_have_execution_fields(config):
    """reference_tickers (^GSPC, GBPUSD=X, EURUSD=X) are monitoring-only
    and must never gain an execution mapping -- confirmed structurally,
    not just by convention."""
    execution_fields = {"is_etf", "t212_instrument_id", "currency"}
    for entry in config["reference_tickers"]:
        assert not (execution_fields & entry.keys()), (
            f"reference_tickers entry {entry.get('ticker')} has execution fields — "
            "reference_tickers must stay monitoring-only"
        )


def test_gold_preserved_as_permanently_ineligible():
    """GOLD (Barrick Mining) is a real, deliberate exception -- verified
    live but permanently zero-eligible. Fully documented and excluded from
    the live candidate pool automatically via its empty STOCK_MEMBERSHIP
    window (analysis/universe.py), which is what actually keeps
    is_member() False for it -- no config.yaml entry is needed to keep it
    "kept, not silently removed". Also covered directly in
    tests/test_universe.py; kept here too as a config-adjacent sanity
    check."""
    from analysis.universe import STOCK_MEMBERSHIP
    assert STOCK_MEMBERSHIP["GOLD"] == []


def test_get_all_watchlist_entries_combines_reference_tickers_and_dynamic_candidates(config, db_engine, mocker):
    """get_all_watchlist_entries() merges reference_tickers with the
    dynamic live candidate pool (analysis.universe.get_live_stock_
    candidates()). Mocked directly here rather than relying on real
    universe_membership content, since this file's own job is validating
    config.yaml's structure, not re-testing universe discovery (already
    covered in tests/test_universe.py)."""
    from data.ingestion import get_all_watchlist_entries

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAPL", "MSFT"])

    entries = get_all_watchlist_entries(config)
    tickers = [e["ticker"] for e in entries]

    assert len(tickers) == len(set(tickers)), "duplicate tickers across reference_tickers + dynamic candidates"
    assert len(entries) == len(config["reference_tickers"]) + 2
    assert "^GSPC" in tickers
    assert "GBPUSD=X" in tickers
    assert "AAPL" in tickers
    assert "MSFT" in tickers


def test_get_all_watchlist_entries_no_dynamic_candidates_falls_back_to_reference_tickers_only(config, db_engine, mocker):
    """A fresh install (no universe_membership rows yet) must not crash --
    just returns reference_tickers alone."""
    from data.ingestion import get_all_watchlist_entries

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=[])

    entries = get_all_watchlist_entries(config)
    tickers = [e["ticker"] for e in entries]

    assert tickers == ["^GSPC", "GBPUSD=X", "EURUSD=X"]


def test_get_all_tickers_matches_get_all_watchlist_entries(config):
    from data.ingestion import get_all_tickers, get_all_watchlist_entries

    assert get_all_tickers(config) == [e["ticker"] for e in get_all_watchlist_entries(config)]
