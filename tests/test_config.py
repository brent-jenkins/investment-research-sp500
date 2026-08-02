"""Structural validation for config.yaml's unified instrument structure.

Consolidated 2026-07-31: watchlist.trading (ticker/name/exchange) and
trading212_invest.instrument_mapping (nested ticker/currency/is_etf) used
to be two separately-maintained structures describing the same 73
instruments, with nothing enforcing they stayed in sync -- XLF's and
XLK's wrong T212 mappings were only ever found by accident. They're now
one top-level `instruments:` section, one record per tradeable
instrument. These tests load the real config.yaml (not a synthetic
fixture) so a future incomplete entry is loudly caught here rather than
discovered live, the same way the original mapping errors were.

Emptied entirely 2026-08-02 (see CLAUDE.md's "wire the momentum-only,
full-S&P-500 selection into live execution" section) -- the live
candidate pool and each ticker's T212 instrument mapping are both sourced
dynamically now (analysis/universe.py:get_live_stock_candidates(),
brokers/trading212.py:resolve_and_cache_instrument()), not from a
hand-curated list here. The structural-validation tests below
(required-fields/sane-values/no-duplicate-IDs) are kept, not deleted --
`instruments:` can still legitimately hold a manual, ad-hoc override
entry in future without a schema change, and these tests would catch an
incomplete one exactly as before; they simply have nothing to check
against on an empty dict today.
"""
from pathlib import Path

import pytest
import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
_REQUIRED_FIELDS = {"name", "exchange", "is_etf", "t212_instrument_id", "currency"}
_VALID_CURRENCIES = {"USD", "GBP", "EUR", "GBX"}


@pytest.fixture(scope="module")
def config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def test_old_duplicated_structures_are_gone(config):
    """watchlist.trading and trading212_invest.instrument_mapping must not
    come back -- their reintroduction would recreate the exact two-lists-
    that-can-drift-apart problem this consolidation removed."""
    assert "trading" not in config["watchlist"]
    assert "instrument_mapping" not in config["trading212_invest"]


def test_instruments_section_exists_as_an_empty_dict(config):
    """`instruments:` is intentionally empty now (2026-08-02) -- the live
    candidate pool is dynamic (see this file's own module docstring). The
    key itself is kept present, not deleted, so config.get("instruments",
    {}) call sites behave identically whether it's present-and-empty or
    absent."""
    assert "instruments" in config
    assert config["instruments"] == {}


def test_every_instrument_has_all_required_fields(config):
    """Structurally impossible to have partial execution fields -- an
    instrument missing any of name/exchange/is_etf/t212_instrument_id/
    currency is loudly flagged here rather than silently sized to 0 or
    mis-priced live (the exact failure mode XLF's old mapping error hit)."""
    missing_by_ticker = {}
    for ticker, entry in config["instruments"].items():
        assert isinstance(entry, dict), f"{ticker}: instruments entry must be a dict, got {type(entry)}"
        missing = _REQUIRED_FIELDS - entry.keys()
        if missing:
            missing_by_ticker[ticker] = missing
    assert not missing_by_ticker, f"instruments entries missing required fields: {missing_by_ticker}"


def test_every_instrument_field_has_a_sane_value(config):
    bad = []
    for ticker, entry in config["instruments"].items():
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            bad.append((ticker, "name"))
        if not isinstance(entry.get("exchange"), str) or not entry["exchange"].strip():
            bad.append((ticker, "exchange"))
        if not isinstance(entry.get("is_etf"), bool):
            bad.append((ticker, "is_etf"))
        if not isinstance(entry.get("t212_instrument_id"), str) or not entry["t212_instrument_id"].strip():
            bad.append((ticker, "t212_instrument_id"))
        if entry.get("currency") not in _VALID_CURRENCIES:
            bad.append((ticker, "currency"))
    assert not bad, f"instruments entries with invalid field values: {bad}"


def test_no_duplicate_t212_instrument_ids(config):
    """Two research tickers pointing at the same T212 instrument would be
    a real, distinct bug (double-counted exposure) -- worth catching here
    even though it's a different failure mode than a missing field."""
    ids = [e["t212_instrument_id"] for e in config["instruments"].values()]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate t212_instrument_id values: {dupes}"


def test_gold_preserved_as_permanently_ineligible():
    """GOLD (Barrick Mining) is a real, deliberate exception -- verified
    live but permanently zero-eligible. It no longer needs a manual
    config.yaml `instruments:` entry to stay "kept, not removed" (that
    override mechanism no longer exists here at all, see this file's own
    module docstring) -- it stays fully documented and excluded from the
    live candidate pool automatically via its empty STOCK_MEMBERSHIP
    window (analysis/universe.py), which is what actually keeps is_member()
    False for it. Also covered directly in tests/test_universe.py; kept
    here too as a config-adjacent sanity check since GOLD's exclusion used
    to live in this file's own structure."""
    from analysis.universe import STOCK_MEMBERSHIP
    assert STOCK_MEMBERSHIP["GOLD"] == []


def test_watchlist_core_never_has_execution_fields(config):
    """core (^GSPC, GBPUSD=X, EURUSD=X) is monitoring-only and must never
    gain an execution mapping -- confirmed structurally, not just by
    convention."""
    execution_fields = {"is_etf", "t212_instrument_id", "currency"}
    for entry in config["watchlist"]["core"]:
        assert not (execution_fields & entry.keys()), (
            f"watchlist.core entry {entry.get('ticker')} has execution fields — "
            "core must stay monitoring-only"
        )


def test_watchlist_core_tickers_never_appear_in_instruments(config):
    core_tickers = {e["ticker"] for e in config["watchlist"]["core"]}
    assert not (core_tickers & config["instruments"].keys())


def test_get_all_watchlist_entries_combines_core_and_dynamic_candidates(config, db_engine, mocker):
    """`instruments:` is empty now (2026-08-02) -- get_all_watchlist_entries()
    merges watchlist.core with the dynamic live candidate pool
    (analysis.universe.get_live_stock_candidates()) instead. Mocked
    directly here rather than relying on real universe_membership content,
    since this file's own job is validating config.yaml's structure, not
    re-testing universe discovery (already covered in
    tests/test_universe.py)."""
    from data.ingestion import get_all_watchlist_entries

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAPL", "MSFT"])

    entries = get_all_watchlist_entries(config)
    tickers = [e["ticker"] for e in entries]

    assert len(tickers) == len(set(tickers)), "duplicate tickers across watchlist.core + dynamic candidates"
    assert len(entries) == len(config["watchlist"]["core"]) + 2
    assert "^GSPC" in tickers
    assert "GBPUSD=X" in tickers
    assert "AAPL" in tickers
    assert "MSFT" in tickers


def test_get_all_watchlist_entries_no_dynamic_candidates_falls_back_to_core_only(config, db_engine, mocker):
    """A fresh install (no universe_membership rows yet, `instruments:`
    empty) must not crash -- just returns watchlist.core alone."""
    from data.ingestion import get_all_watchlist_entries

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=[])

    entries = get_all_watchlist_entries(config)
    tickers = [e["ticker"] for e in entries]

    assert tickers == ["^GSPC", "GBPUSD=X", "EURUSD=X"]


def test_get_all_tickers_matches_get_all_watchlist_entries(config):
    from data.ingestion import get_all_tickers, get_all_watchlist_entries

    assert get_all_tickers(config) == [e["ticker"] for e in get_all_watchlist_entries(config)]
