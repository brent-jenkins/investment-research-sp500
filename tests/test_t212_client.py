"""Tests for Trading212Client's HTTP-response-body handling -- specifically
Trading212Client._parse_json(), added 2026-08-01 to close Item 3 of the
"Live and backtest consolidation audit" (see CLAUDE.md).

Before this fix, every one of Trading212Client's seven public methods
called bare r.json() directly -- a malformed or empty response body on a
genuinely successful (< 400) HTTP status raised an unguarded exception
indistinguishable, to any caller's own `except Exception`, from a real
request failure. The concrete, already-observed consequence (historical --
the call site that surfaced this, data/scheduler.py:_cancel_stale_weekend_
orders(), was removed entirely 2026-08-03, see CLAUDE.md's "Monday
Pre-Market removed" section; the underlying cancel_order() fix itself is
unaffected and still real): Trading212Client.cancel_order()'s DELETE
endpoint commonly returns 200/204 with NO body at all (standard REST
convention) -- a genuinely successful cancellation would raise on the
empty body, get caught by a caller's own `except Exception`, and be
logged/counted as a FAILED cancellation even though T212 had already
cancelled the order.

No existing test in this suite exercised Trading212Client's real HTTP
layer at all before this file -- every prior test mocked the client's
methods directly (see tests/test_execution.py), never Trading212Client's
own _request()/.json() parsing. These tests mock at the requests.request()
level specifically so the real client code (construction, _request(),
_parse_json()) all actually runs.
"""
import json as _json

import pytest

from brokers.trading212 import Trading212Client


class _FakeResponse:
    """Minimal stand-in for requests.Response -- only the surface
    Trading212Client._request()/_parse_json() actually touches."""

    def __init__(self, status_code=200, json_data=None, raise_on_json=False, text="", url=None):
        self.status_code = status_code
        self.reason = "OK" if status_code < 400 else "Error"
        self.url = url or "https://demo.trading212.com/api/v0/x"
        self.text = text
        self._json_data = json_data
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise _json.JSONDecodeError("Expecting value", "", 0)
        return self._json_data


def _client(monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    return Trading212Client(live=False)


# ---------------------------------------------------------------------------
# Baseline -- normal, well-formed JSON still works identically on every
# method (no regression from routing through _parse_json()).
# ---------------------------------------------------------------------------

def test_get_account_summary_parses_normal_json(monkeypatch, mocker):
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(json_data={"totalValue": 50000.0}),
    )
    assert client.get_account_summary() == {"totalValue": 50000.0}


def test_place_market_order_parses_normal_json(monkeypatch, mocker):
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(json_data={"id": "abc123"}),
    )
    assert client.place_market_order("AAPL_US_EQ", 1.0) == {"id": "abc123"}


def test_cancel_order_parses_normal_json(monkeypatch, mocker):
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(json_data={"status": "CANCELLED"}),
    )
    assert client.cancel_order("order-1") == {"status": "CANCELLED"}


# ---------------------------------------------------------------------------
# The core fix -- action endpoints (place_market_order, cancel_order) never
# treat a malformed/empty body on a successful status as a failure.
# ---------------------------------------------------------------------------

def test_cancel_order_with_empty_body_on_success_does_not_raise(monkeypatch, mocker):
    """The exact real bug: T212's DELETE returning 200/204 with no body
    must resolve as a successful cancellation with no data, not an
    exception a caller's `except Exception` would treat as a failure."""
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(status_code=204, raise_on_json=True, text=""),
    )
    result = client.cancel_order("order-1")
    assert result == {}


def test_place_market_order_with_malformed_body_on_success_does_not_raise(monkeypatch, mocker):
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(status_code=200, raise_on_json=True, text="not json"),
    )
    result = client.place_market_order("AAPL_US_EQ", 1.0)
    assert result == {}


def test_place_market_order_empty_dict_result_has_no_order_id(monkeypatch, mocker):
    """Confirms the shape callers see: response.get("id", "") on the {}
    fallback yields an empty string, not a crash -- callers already handle
    this (see brokers/trading212.py's SELL-order call site, which sets
    order_id = str(response.get("id", "")))."""
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(status_code=200, raise_on_json=True),
    )
    response = client.place_market_order("AAPL_US_EQ", 1.0)
    assert str(response.get("id", "")) == ""


# ---------------------------------------------------------------------------
# Read endpoints -- a malformed body on a successful status must still
# raise loudly (the body IS the payload; silently returning {} would make
# a real account look like it holds nothing).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method_name, args, path",
    [
        ("get_account_summary", (), "/equity/account/summary"),
        ("get_positions", (), "/equity/portfolio"),
        ("get_order", ("order-1",), "/equity/orders/order-1"),
    ],
)
def test_read_endpoints_still_raise_on_malformed_body(monkeypatch, mocker, method_name, args, path):
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(status_code=200, raise_on_json=True, text="<html>error</html>"),
    )
    with pytest.raises(ValueError):
        getattr(client, method_name)(*args)


def test_get_instruments_still_raises_on_malformed_body_and_does_not_cache_it(monkeypatch, mocker):
    import brokers.trading212 as t212mod
    t212mod._instruments_cache.clear()
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(status_code=200, raise_on_json=True),
    )
    with pytest.raises(ValueError):
        client.get_instruments()
    # A malformed response must never poison the cache with garbage.
    assert client.base_url not in t212mod._instruments_cache


def test_get_order_history_still_raises_on_malformed_body(monkeypatch, mocker):
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(status_code=200, raise_on_json=True),
    )
    with pytest.raises(ValueError):
        client.get_order_history()


def test_get_order_history_parses_normal_response_shape(monkeypatch, mocker):
    client = _client(monkeypatch)
    mocker.patch(
        "brokers.trading212.requests.request",
        return_value=_FakeResponse(json_data={"items": [{"order": {"id": "1"}}]}),
    )
    assert client.get_order_history() == [{"order": {"id": "1"}}]
