"""Coverage for the zero-means-disabled holding cap and the separately-resolved
VaR sizing horizon (promoted 2026-08-12 from the exit-mechanics sweep).

The promotion's whole risk is that ONE config value feeds TWO consumers with
different semantics once it is disabled:
  * evaluate_exit_reason() -- None must mean "no age-based exit"
  * _var_position_size_core() -- needs a REAL horizon; None would be sqrt(None)

These tests pin both halves, and pin that the DEFAULT config is unchanged.
"""
import yaml

from analysis import universe
from analysis.decision import (
    DEFAULT_VAR_HORIZON_DAYS, MAX_HOLDING_DISABLED_SENTINEL, evaluate_exit_reason,
    resolve_var_horizon_days,
)


def _cfg(**t212):
    base = {"trading212_invest": {"stock_max_holding_days": 30, "max_holding_days": 90}}
    base["trading212_invest"].update(t212)
    return base


# --------------------------------------------------------------------------
# The default must not move. This is the guard that makes the promotion safe
# to merge before any flip.
# --------------------------------------------------------------------------

def test_shipped_config_still_has_a_finite_stock_holding_cap():
    """config.yaml must keep the current behaviour until a deliberate flip."""
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cap = cfg["trading212_invest"]["stock_max_holding_days"]
    # TRANSIENT (2026-08-12): 1 is a deliberate, short-lived MECHANISM, not a
    # setting. Every open position is 4-8 days old, so a 1-day cap flushes the
    # whole book through the system's own tested exit path -- which cancels each
    # resting broker-side stop before selling, unlike a raw SELL, which would
    # leave 22 orphaned stops that could later fire against a re-entered
    # position. The very next commit sets the adopted value. If this value is
    # still 1 when you read it, the flip did not complete -- see CLAUDE.md's
    # exit-mechanics promotion entry.
    assert cap != MAX_HOLDING_DISABLED_SENTINEL, (
        "stock_max_holding_days is 0 -- that is the ADOPTED state, not the "
        "transient flush state this assertion is pinned to."
    )
    assert cap == 1


def test_default_config_resolves_to_the_unchanged_finite_cap():
    assert universe.get_max_holding_days("AAPL", _cfg()) == 30
    assert resolve_var_horizon_days(30, _cfg()) == 30


# --------------------------------------------------------------------------
# Zero disables the cap
# --------------------------------------------------------------------------

def test_zero_stock_cap_disables_the_holding_limit():
    assert universe.get_max_holding_days("AAPL", _cfg(stock_max_holding_days=0)) is None


def test_zero_etf_cap_disables_the_holding_limit_for_etfs():
    etf = next(iter(universe.ETF_TICKERS))
    assert universe.get_max_holding_days(etf, _cfg(max_holding_days=0)) is None


def test_disabling_the_stock_cap_does_not_disable_the_etf_cap():
    """The two keys are independent -- a disabled stock cap must not leak."""
    etf = next(iter(universe.ETF_TICKERS))
    cfg = _cfg(stock_max_holding_days=0, max_holding_days=90)
    assert universe.get_max_holding_days("AAPL", cfg) is None
    assert universe.get_max_holding_days(etf, cfg) == 90


def test_nonzero_caps_are_unaffected():
    assert universe.get_max_holding_days("AAPL", _cfg(stock_max_holding_days=60)) == 60


# --------------------------------------------------------------------------
# A disabled cap must produce NO age-based exit
# --------------------------------------------------------------------------

def test_disabled_cap_never_fires_max_holding_however_long_the_hold():
    """None must flow through evaluate_exit_reason()'s None-tolerance."""
    for days in (1, 30, 100, 5000):
        assert evaluate_exit_reason(
            crisis_now=False, price=100.0, stop_price=90.0, sig_on=True,
            holding_days=days, max_holding_days=None,
        ) is None


def test_finite_cap_still_fires_max_holding():
    assert evaluate_exit_reason(
        crisis_now=False, price=100.0, stop_price=90.0, sig_on=True,
        holding_days=30, max_holding_days=30,
    ) == "MAX_HOLDING"


def test_disabled_cap_does_not_suppress_the_other_exit_reasons():
    """Disabling the age cap must not disable stops, signal-off or crisis."""
    common = dict(holding_days=9999, max_holding_days=None)
    assert evaluate_exit_reason(crisis_now=True, price=100.0, stop_price=90.0,
                                sig_on=True, **common) == "CRISIS_EXIT"
    assert evaluate_exit_reason(crisis_now=False, price=89.0, stop_price=90.0,
                                sig_on=True, **common) == "STOP_LOSS"
    assert evaluate_exit_reason(crisis_now=False, price=100.0, stop_price=90.0,
                                sig_on=False, **common) == "SIGNAL_OFF"


# --------------------------------------------------------------------------
# The VaR horizon must stay a real number when the cap is disabled
# --------------------------------------------------------------------------

def test_var_horizon_falls_back_when_the_cap_is_disabled():
    assert resolve_var_horizon_days(None, _cfg()) == DEFAULT_VAR_HORIZON_DAYS == 30


def test_var_horizon_fallback_matches_what_the_research_measured():
    """LOAD-BEARING: the sweep measured its unlimited-holding arms with a sizing
    horizon of exactly 30. A different fallback would not reproduce it."""
    assert DEFAULT_VAR_HORIZON_DAYS == 30


def test_var_horizon_is_configurable_without_touching_the_exit_cap():
    cfg = _cfg(stock_max_holding_days=0, var_horizon_days=45)
    assert universe.get_max_holding_days("AAPL", cfg) is None      # exits: disabled
    assert resolve_var_horizon_days(None, cfg) == 45               # sizing: 45


def test_var_horizon_passes_through_a_finite_cap_unchanged():
    assert resolve_var_horizon_days(60, _cfg(var_horizon_days=45)) == 60


def test_var_horizon_never_returns_none():
    """sqrt(None) is the failure this function exists to prevent."""
    for cap in (None, 1, 30, 90):
        got = resolve_var_horizon_days(cap, _cfg())
        assert isinstance(got, int) and got > 0
