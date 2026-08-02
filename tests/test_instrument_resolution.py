"""Tests for T212 instrument-ID resolution (2026-08-02) -- the mechanism
that replaced the old hand-curated config.yaml `instruments:` mapping now
that the live candidate pool is the full, dynamically-refreshed S&P 500.
See CLAUDE.md's "wire the momentum-only, full-S&P-500 selection into live
execution" section.

Covers both layers: resolve_instrument_candidate() (pure matching logic,
no I/O) and resolve_and_cache_instrument() (the DB-backed cache wrapper
around it), plus an end-to-end integration check that a FAILED/PENDING
resolution genuinely excludes a ticker from maybe_place_orders()'s decide
phase rather than trading on an unverified guess.
"""
from datetime import datetime

import pytest
from sqlalchemy import text as sa_text

from brokers.trading212 import (
    _load_instrument_resolution,
    resolve_and_cache_instrument,
    resolve_instrument_candidate,
)


# ---------------------------------------------------------------------------
# resolve_instrument_candidate() -- pure matching logic
# ---------------------------------------------------------------------------

def test_convention_match_verifies_immediately():
    """The common case: {TICKER}_US_EQ exists and its shortName equals the
    ticker -- verified via the naming convention, no fallback search."""
    instruments = [
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
        {"ticker": "MSFT_US_EQ", "shortName": "MSFT", "currencyCode": "USD"},
    ]
    result = resolve_instrument_candidate("AAPL", instruments)
    assert result["status"] == "VERIFIED"
    assert result["t212_instrument_id"] == "AAPL_US_EQ"
    assert result["currency"] == "USD"
    assert "naming convention" in result["note"]


def test_exception_path_matches_via_shortname_when_convention_id_absent():
    """The real META case: META_US_EQ does not exist at all -- T212 kept
    the legacy FB_US_EQ instrument ID after the Facebook->Meta rename, but
    its shortName was updated to "META". Must resolve via the shortName
    fallback search, not fail just because the naming-convention guess
    doesn't exist."""
    instruments = [
        {"ticker": "FB_US_EQ", "shortName": "META", "name": "Meta Platforms", "currencyCode": "USD"},
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
    ]
    result = resolve_instrument_candidate("META", instruments)
    assert result["status"] == "VERIFIED"
    assert result["t212_instrument_id"] == "FB_US_EQ"
    assert result["currency"] == "USD"
    assert "shortName search" in result["note"]


def test_exception_path_matches_via_shortname_when_convention_guess_is_wrong():
    """The real RTX case: UTX_US_EQ (pre-merger United Technologies ID)
    carries shortName "RTX" -- but suppose a DIFFERENT, wrong instrument
    also happened to exist at the exact RTX_US_EQ guess (a genuinely wrong
    match, the XLF/XLK failure shape). The wrong guess must be rejected
    (its shortName doesn't match) and the correct instrument found via the
    fallback search instead -- never silently trusting a guessed ID whose
    metadata doesn't confirm it."""
    instruments = [
        {"ticker": "RTX_US_EQ", "shortName": "RAYTHEON_OLD", "currencyCode": "USD"},  # wrong match
        {"ticker": "UTX_US_EQ", "shortName": "RTX", "name": "RTX Corporation", "currencyCode": "USD"},
    ]
    result = resolve_instrument_candidate("RTX", instruments)
    assert result["status"] == "VERIFIED"
    assert result["t212_instrument_id"] == "UTX_US_EQ"


def test_no_match_anywhere_fails_cleanly():
    instruments = [
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
    ]
    result = resolve_instrument_candidate("ZZZZ", instruments)
    assert result["status"] == "FAILED"
    assert result["t212_instrument_id"] is None
    assert result["currency"] is None
    assert "no US-listed equity instrument found" in result["note"]


def test_ambiguous_multiple_matches_fails_rather_than_guessing():
    """Two distinct US-listed equity instruments both carry the same
    shortName -- must never silently pick one; a genuine ambiguity is
    exactly what "flag for review" exists for."""
    instruments = [
        {"ticker": "DUP1_US_EQ", "shortName": "DUPX", "currencyCode": "USD"},
        {"ticker": "DUP2_US_EQ", "shortName": "DUPX", "currencyCode": "USD"},
    ]
    result = resolve_instrument_candidate("DUPX", instruments)
    assert result["status"] == "FAILED"
    assert "ambiguous" in result["note"]


def test_convention_id_exists_but_shortname_mismatch_falls_through_to_search():
    """A guessed {TICKER}_US_EQ that genuinely exists but represents a
    DIFFERENT company (shortName doesn't match) must not be accepted just
    because the ID happened to exist -- and if no other instrument
    matches by shortName either, this must FAIL, not silently trade the
    wrong guess."""
    instruments = [
        {"ticker": "WRONG_US_EQ", "shortName": "SOMETHING_ELSE", "currencyCode": "USD"},
    ]
    result = resolve_instrument_candidate("WRONG", instruments)
    assert result["status"] == "FAILED"


def test_case_insensitive_ticker_matching():
    instruments = [{"ticker": "AAPL_US_EQ", "shortName": "aapl", "currencyCode": "USD"}]
    result = resolve_instrument_candidate("aapl", instruments)
    assert result["status"] == "VERIFIED"


def test_missing_currency_code_defaults_to_usd():
    instruments = [{"ticker": "AAPL_US_EQ", "shortName": "AAPL"}]  # no currencyCode key
    result = resolve_instrument_candidate("AAPL", instruments)
    assert result["status"] == "VERIFIED"
    assert result["currency"] == "USD"


def test_malformed_instrument_dicts_are_skipped_not_raised():
    """A missing ticker/shortName key on one instrument must not blow up
    the whole resolution attempt for every other candidate ticker."""
    instruments = [
        {},  # completely malformed
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
    ]
    result = resolve_instrument_candidate("AAPL", instruments)
    assert result["status"] == "VERIFIED"


# ---------------------------------------------------------------------------
# resolve_and_cache_instrument() -- DB-backed cache wrapper
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, instruments=None, raises=None):
        self._instruments = instruments
        self._raises = raises
        self.call_count = 0

    def get_instruments(self):
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return self._instruments


def test_first_resolution_calls_api_and_caches_verified(db_engine):
    client = _FakeClient(instruments=[
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
    ])
    result = resolve_and_cache_instrument("AAPL", client)
    assert result == {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD"}
    assert client.call_count == 1

    cached = _load_instrument_resolution("AAPL")
    assert cached["status"] == "VERIFIED"
    assert cached["t212_instrument_id"] == "AAPL_US_EQ"


def test_second_call_reads_cache_never_calls_api_again(db_engine):
    client = _FakeClient(instruments=[
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
    ])
    resolve_and_cache_instrument("AAPL", client)
    assert client.call_count == 1

    result = resolve_and_cache_instrument("AAPL", client)
    assert result == {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD"}
    assert client.call_count == 1  # still 1 -- served from cache, no second API call


def test_failed_resolution_excludes_and_is_never_auto_retried(db_engine):
    client = _FakeClient(instruments=[
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
    ])
    result = resolve_and_cache_instrument("ZZZZ", client)
    assert result is None
    assert client.call_count == 1

    cached = _load_instrument_resolution("ZZZZ")
    assert cached["status"] == "FAILED"

    # A second call must NOT retry the API -- a FAILED resolution is
    # flagged for manual review, never silently re-guessed.
    result_again = resolve_and_cache_instrument("ZZZZ", client)
    assert result_again is None
    assert client.call_count == 1


def test_api_failure_leaves_pending_and_excludes_never_trades_unverified(db_engine):
    """The critical safety property: get_instruments() itself erroring
    (an operational failure, not a genuine mismatch) must NEVER fall back
    to trading the ticker as if it were verified."""
    client = _FakeClient(raises=RuntimeError("T212 API is down"))
    result = resolve_and_cache_instrument("AAPL", client)
    assert result is None
    assert client.call_count == 1

    cached = _load_instrument_resolution("AAPL")
    assert cached["status"] == "PENDING"
    assert "get_instruments() failed" in cached["note"]


def test_pending_resolution_is_retried_on_next_call_unlike_failed(db_engine):
    """PENDING (operational failure) is retried automatically; FAILED
    (genuine mismatch) is not -- the two must behave differently."""
    failing_client = _FakeClient(raises=RuntimeError("T212 API is down"))
    result = resolve_and_cache_instrument("AAPL", failing_client)
    assert result is None
    assert _load_instrument_resolution("AAPL")["status"] == "PENDING"

    working_client = _FakeClient(instruments=[
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
    ])
    result = resolve_and_cache_instrument("AAPL", working_client)
    assert result == {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD"}
    assert working_client.call_count == 1  # retried, and this time succeeded
    assert _load_instrument_resolution("AAPL")["status"] == "VERIFIED"


def test_empty_instruments_list_treated_as_operational_failure(db_engine):
    """get_instruments() succeeding but returning nothing at all is
    treated the same as an API error (PENDING, retried later) -- not as
    "zero real US equities exist", which would be a nonsensical genuine
    FAILED verdict."""
    client = _FakeClient(instruments=[])
    result = resolve_and_cache_instrument("AAPL", client)
    assert result is None
    assert _load_instrument_resolution("AAPL")["status"] == "PENDING"


# ---------------------------------------------------------------------------
# End-to-end: a FAILED/PENDING resolution genuinely excludes a ticker from
# maybe_place_orders()'s decide phase, not just from the unit-level
# resolve_and_cache_instrument() call.
# ---------------------------------------------------------------------------

@pytest.fixture
def _decide_mocks(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    monkeypatch.setattr("brokers.trading212.time.sleep", lambda *_a, **_k: None)

    mock_client = mocker.MagicMock()
    mock_client.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 5_000.0},
        "investments": {"currentValue": 0.0},
    }
    mock_client.get_positions.return_value = []
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)
    return mock_client


def _bullish_frame():
    import numpy as np
    import pandas as pd
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=400)
    closes = [100.0]
    for _ in range(399):
        closes.append(closes[-1] * 1.0015)
    closes = np.array(closes)
    return pd.DataFrame({
        "date": dates, "open": closes, "high": closes * 1.01,
        "low": closes * 0.99, "close": closes,
    })


def _config():
    return {
        "trading212_invest": {
            "enabled": True, "live": False,
            "max_total_deployed": 0.5, "per_trade_pct": 0.05,
            "var_confidence": 0.95, "var_lookback_days": 252,
            "max_risk_pct": 0.01, "min_position_gbp": 50,
        },
        "backtest": {"atr_stop_multiplier": 2.0},
        "signals": {"atr_period": 14},
    }


def _pending_rows(engine):
    with engine.connect() as conn:
        rows = conn.execute(sa_text(
            "SELECT * FROM pending_orders WHERE status = 'PENDING'"
        )).mappings().all()
    return [dict(r) for r in rows]


def test_decide_phase_never_queues_a_ticker_with_no_verified_resolution(db_engine, _decide_mocks, mocker):
    """A signal-qualifying, otherwise entry-eligible ticker whose T212
    instrument mapping fails verification must never be queued -- this is
    the integration-level proof that resolve_and_cache_instrument()'s
    FAILED verdict actually stops a real order decision, not just the
    unit-level function call."""
    from brokers.trading212 import maybe_place_orders

    def _fake_get_price_df(ticker, days=8000):
        return _bullish_frame().copy()
    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["UNVERIFIABLE"])

    # No instrument in the metadata list matches "UNVERIFIABLE" at all --
    # resolution will FAIL.
    _decide_mocks.get_instruments.return_value = [
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
    ]

    maybe_place_orders(_config(), run_type="full")

    _decide_mocks.place_market_order.assert_not_called()
    assert _pending_rows(db_engine) == []

    from brokers.trading212 import _load_instrument_resolution
    assert _load_instrument_resolution("UNVERIFIABLE")["status"] == "FAILED"


def test_decide_phase_never_queues_a_ticker_while_resolution_is_pending_on_api_failure(db_engine, _decide_mocks, mocker):
    """get_instruments() itself erroring during the decide phase must
    leave the ticker unresolved and unqueued -- never a silent fallback
    to trading on an unverified guess."""
    from brokers.trading212 import maybe_place_orders

    def _fake_get_price_df(ticker, days=8000):
        return _bullish_frame().copy()
    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["NEWCO"])

    _decide_mocks.get_instruments.side_effect = RuntimeError("T212 metadata endpoint down")

    maybe_place_orders(_config(), run_type="full")

    _decide_mocks.place_market_order.assert_not_called()
    assert _pending_rows(db_engine) == []

    from brokers.trading212 import _load_instrument_resolution
    assert _load_instrument_resolution("NEWCO")["status"] == "PENDING"


def test_decide_phase_queues_once_resolution_succeeds(db_engine, _decide_mocks, mocker):
    """Sanity check on the same integration path: a ticker WITH a clean
    resolution is queued normally -- confirms the exclusion tests above
    are testing a real gate, not a scenario that would never have queued
    anything anyway."""
    from brokers.trading212 import maybe_place_orders

    def _fake_get_price_df(ticker, days=8000):
        return _bullish_frame().copy()
    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAPL"])

    _decide_mocks.get_instruments.return_value = [
        {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD"},
    ]

    maybe_place_orders(_config(), run_type="full")

    rows = _pending_rows(db_engine)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["t212_ticker"] == "AAPL_US_EQ"
