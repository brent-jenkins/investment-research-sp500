"""Tests for the single yfinance-boundary ticker normalisation point.

data/ingestion.py:_yf_symbol() converts this codebase's canonical dotted
share-class form (BRK.B, BF.B -- what universe_membership and price_data are
keyed on) to the hyphen form yfinance expects. It already existed and was
already used on the price path; what was missing was that three other
yfinance-facing call sites bypassed it, so the dotted tickers silently returned
nothing from the sector lookup and the fundamentals recorder.

THE STRUCTURAL GUARD IS THE POINT OF THIS FILE. A per-call-site test proves
today's three call sites are routed; it says nothing about the fourth one
somebody adds next month, which is exactly how this problem reached three
independent diagnoses. test_no_production_yfinance_call_passes_a_raw_ticker
AST-scans production code instead, so a new unconverted call fails the suite
rather than waiting to be rediscovered.

Deliberately NOT the whole story: asserting that the string transform is
correct, or that a mocked yf.Ticker received "BRK-B", proves routing and not
outcome. The outcome proof -- that BF.B and BRK.B genuinely return data and
leave the never-fetched queue -- is a live yfinance call and is recorded in
research/ticker_format_2026-08-23/, not run here, because the suite must never
depend on the network.
"""
import ast
from pathlib import Path

import pytest

from data.ingestion import _yf_symbol

ROOT = Path(__file__).resolve().parent.parent
# Production modules only -- research/ and tests/ are deliberately out of scope.
_PRODUCTION_DIRS = ("analysis", "brokers", "data", "output")
_PRODUCTION_FILES = ("main.py", "scheduler_daemon.py", "prewarm_sector_data.py",
                     "catchup_fundamentals.py", "webhook_listener.py")


# --------------------------------------------------------------------------
# The transform itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("canonical,expected", [
    ("BRK.B", "BRK-B"), ("BF.B", "BF-B"), ("BRK.A", "BRK-A"),
    ("AAPL", "AAPL"), ("^GSPC", "^GSPC"), ("^SP500TR", "^SP500TR"),
])
def test_canonical_to_yfinance(canonical, expected):
    assert _yf_symbol(canonical) == expected


def test_the_transform_is_idempotent():
    """An already-converted symbol must survive a second pass unchanged, so a
    call site that is accidentally routed twice cannot corrupt the symbol."""
    assert _yf_symbol(_yf_symbol("BRK.B")) == "BRK-B"


# --------------------------------------------------------------------------
# THE STRUCTURAL GUARD
# --------------------------------------------------------------------------

def _yfinance_calls_in(path: Path):
    """Yield (lineno, first-arg-source) for every yf.Ticker()/yf.download()."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    src = path.read_text(encoding="utf-8").splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("Ticker", "download"):
            continue
        base = node.func.value
        if not (isinstance(base, ast.Name) and base.id == "yf"):
            continue
        if not node.args:
            continue
        yield node.lineno, ast.dump(node.args[0]), src[node.lineno - 1].strip()


def _production_files():
    files = [ROOT / f for f in _PRODUCTION_FILES if (ROOT / f).exists()]
    for d in _PRODUCTION_DIRS:
        files.extend(sorted((ROOT / d).rglob("*.py")))
    return files


def test_no_production_yfinance_call_passes_a_raw_ticker():
    """Every yf.Ticker()/yf.download() in production must wrap its symbol in
    _yf_symbol(). Scanned rather than remembered: three separate call sites
    were each added without it, months apart, by people who knew the helper
    existed."""
    offenders = []
    for path in _production_files():
        for lineno, arg_dump, line in _yfinance_calls_in(path):
            if "_yf_symbol" not in arg_dump:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}  {line}")
    assert not offenders, (
        "yfinance call(s) not routed through _yf_symbol() -- a dotted ticker "
        "(BRK.B/BF.B) will silently return no data:\n  " + "\n  ".join(offenders))


def test_the_guard_is_not_vacuous():
    """The scanner must actually be finding calls. A guard that reports clean
    because it matched nothing is this project's most-repeated failure shape."""
    found = sum(1 for p in _production_files() for _ in _yfinance_calls_in(p))
    assert found >= 5, f"expected the known production yfinance call sites, found {found}"


# --------------------------------------------------------------------------
# Routing at each call site that was previously bypassing the helper
# --------------------------------------------------------------------------

def test_fetch_ticker_sector_requests_the_converted_symbol(mocker):
    tk = mocker.patch("yfinance.Ticker")
    tk.return_value.info = {"sector": "Financial Services"}

    from data.ingestion import fetch_ticker_sector
    assert fetch_ticker_sector("BRK.B") == "Financial Services"
    tk.assert_called_once_with("BRK-B")


def test_fetch_ticker_fundamentals_requests_the_converted_symbol(mocker):
    import pandas as pd
    tk = mocker.patch("yfinance.Ticker")
    frame = pd.DataFrame({pd.Timestamp("2026-06-30"): {"Total Revenue": 1.0}})
    tk.return_value.quarterly_balance_sheet = frame
    tk.return_value.quarterly_financials = frame
    tk.return_value.quarterly_cashflow = frame

    from data.ingestion import fetch_ticker_fundamentals
    fetch_ticker_fundamentals("BF.B")
    tk.assert_called_once_with("BF-B")


def test_fetch_ticker_fundamentals_history_requests_the_converted_symbol(mocker):
    import pandas as pd
    tk = mocker.patch("yfinance.Ticker")
    frame = pd.DataFrame({pd.Timestamp("2026-06-30"): {"Total Revenue": 1.0}})
    tk.return_value.quarterly_balance_sheet = frame
    tk.return_value.quarterly_financials = frame
    tk.return_value.quarterly_cashflow = frame

    from data.ingestion import fetch_ticker_fundamentals_history
    fetch_ticker_fundamentals_history("BRK.B", max_quarters=1)
    tk.assert_called_once_with("BRK-B")


def test_a_plain_ticker_is_passed_through_untouched(mocker):
    """The conversion must be a no-op for the 501 tickers that have no dot --
    this change fixes failures and must not perturb anything already working."""
    tk = mocker.patch("yfinance.Ticker")
    tk.return_value.info = {"sector": "Technology"}

    from data.ingestion import fetch_ticker_sector
    fetch_ticker_sector("AAPL")
    tk.assert_called_once_with("AAPL")
