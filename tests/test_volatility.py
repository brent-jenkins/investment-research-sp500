"""Direct, thorough unit tests for analysis/volatility.py:_var_position_size_core()
-- the single formula every sizing decision on both live order placement and
every backtest engine ultimately depends on.

Added 2026-08-01 as part of resolving Item 1 of the "Live and backtest
consolidation audit" (see CLAUDE.md): this function had ZERO direct tests
before this file -- only incidental coverage via higher-level engine calls
(e.g. tests/test_execution.py, tests/test_portfolio_backtest.py), which
never isolated the sizing arithmetic itself. That gap is the direct reason
the Item 2 clamp-ordering bug (the floor silently overriding the ceiling)
went uncaught for as long as it did.

Covers: normal sizing, the floor/ceiling clamp interaction specifically
(including the exact 2026-08-01 regression scenario -- a dynamic ceiling
below the floor must never let the floor win), the zero-variance
early-return branch, holding-period (sqrt-of-time) scaling, and confidence-
level sensitivity.
"""
import math

import numpy as np
import pytest

from analysis.volatility import _var_position_size_core

# A fixed, deterministic 100-point return sample spanning -5% to +4% --
# large enough to be realistic, evenly spaced so different percentiles
# land on genuinely different values (needed for the confidence-level
# sensitivity test below). daily_var is computed via np.percentile
# directly here (the same call the function itself makes internally) --
# these tests verify the SIZING ARITHMETIC built on top of the percentile,
# not numpy's own percentile algorithm.
_RETURNS = np.linspace(-0.05, 0.04, 100)
_DAILY_VAR_95 = float(np.percentile(_RETURNS, 5))


def test_normal_case_sizes_proportionally_to_risk_budget_when_ceiling_and_floor_dont_bind():
    account_value_gbp = 10_000.0
    max_risk_pct = 0.01
    holding_days = 30
    holding_var = _DAILY_VAR_95 * math.sqrt(holding_days)
    expected_raw = (account_value_gbp * max_risk_pct) / abs(holding_var)

    position_gbp, daily_var, holding_var_out = _var_position_size_core(
        _RETURNS, account_value_gbp, max_position_gbp=1_000_000, holding_days=holding_days,
        var_confidence=0.95, max_risk_pct=max_risk_pct, min_position_gbp=1.0,
    )

    assert daily_var == pytest.approx(_DAILY_VAR_95)
    assert holding_var_out == pytest.approx(holding_var)
    assert position_gbp == pytest.approx(round(expected_raw, 2))
    # Sanity: comfortably between the (very loose) floor/ceiling used here,
    # confirming neither clamp is what produced this value.
    assert 1.0 < position_gbp < 1_000_000


def test_ceiling_binds_when_risk_budget_implied_size_exceeds_it():
    # Short holding period against a large account produces a huge raw
    # size -- must clamp to the ceiling exactly, not merely "below" it.
    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp=10_000_000, max_position_gbp=500.0, holding_days=1,
        var_confidence=0.95, max_risk_pct=0.01, min_position_gbp=50.0,
    )
    assert position_gbp == 500.0


def test_floor_binds_when_ceiling_comfortably_above_it():
    # Long hold + extreme confidence + tiny account produces a raw size far
    # below the floor -- must clamp UP to the floor, since the ceiling here
    # (2500) is well above it and never in conflict.
    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp=100.0, max_position_gbp=2500.0, holding_days=252,
        var_confidence=0.99, max_risk_pct=0.001, min_position_gbp=50.0,
    )
    assert position_gbp == 50.0


def test_ceiling_wins_when_ceiling_falls_below_floor_2026_08_01_regression():
    """The exact bug fixed 2026-08-01 (see CLAUDE.md's "Live and backtest
    consolidation audit" Item 2): a caller's own dynamic ceiling
    (resolve_dynamic_max_position_gbp()'s output, fed by a severely
    depleted account balance) can legitimately fall below min_position_gbp.
    The result must never exceed max_position_gbp even though
    min_position_gbp is larger -- the ceiling is the hard, safety-critical
    guarantee this function's own docstring promises; the floor is only a
    "don't bother with a too-small order" convenience that must yield to it."""
    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp=500.0, max_position_gbp=20.0, holding_days=30,
        var_confidence=0.95, max_risk_pct=0.01, min_position_gbp=50.0,
    )
    assert position_gbp == 20.0
    assert position_gbp < 50.0  # explicitly confirms the floor did NOT win


def test_ceiling_wins_even_when_raw_size_would_far_exceed_both_floor_and_ceiling():
    # Same shape as above but with a raw risk-budget size that would
    # comfortably exceed BOTH the floor and the ceiling if unclamped --
    # confirms the ceiling wins regardless of how the raw size compares to
    # either bound, not just in the narrow band between them.
    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp=1_000_000, max_position_gbp=5.0, holding_days=1,
        var_confidence=0.95, max_risk_pct=0.01, min_position_gbp=50.0,
    )
    assert position_gbp == 5.0


def test_ceiling_and_floor_equal_resolves_to_that_exact_value():
    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp=1_000_000, max_position_gbp=50.0, holding_days=1,
        var_confidence=0.95, max_risk_pct=0.01, min_position_gbp=50.0,
    )
    assert position_gbp == 50.0


def test_boundary_raw_size_exactly_at_ceiling_not_pushed_either_side():
    account_value_gbp = 10_000.0
    holding_days = 30
    holding_var = _DAILY_VAR_95 * math.sqrt(holding_days)
    max_risk_pct = 0.01
    raw = (account_value_gbp * max_risk_pct) / abs(holding_var)
    ceiling = round(raw, 2)

    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp, max_position_gbp=ceiling, holding_days=holding_days,
        var_confidence=0.95, max_risk_pct=max_risk_pct, min_position_gbp=1.0,
    )
    assert position_gbp == pytest.approx(ceiling)


def test_zero_variance_series_uses_full_ceiling_ignoring_floor():
    """A perfectly flat return series (holding_var == 0) -- nothing to
    constrain against -- must return the FULL ceiling regardless of the
    floor, per the function's own early-return branch."""
    flat = np.zeros(100)
    position_gbp, daily_var, holding_var = _var_position_size_core(
        flat, account_value_gbp=10_000.0, max_position_gbp=2500.0, holding_days=30,
        var_confidence=0.95, max_risk_pct=0.01, min_position_gbp=50.0,
    )
    assert daily_var == 0.0
    assert holding_var == 0.0
    assert position_gbp == 2500.0  # the ceiling -- not min_position_gbp, not a mid-value


def test_zero_variance_series_still_never_exceeds_a_ceiling_below_the_floor():
    """Same zero-variance early-return path, but with a ceiling below the
    floor -- confirms the early-return branch is already consistent with
    the main path's ceiling-wins fix (it returns max_position_gbp
    unconditionally, with no floor applied at all)."""
    flat = np.zeros(100)
    position_gbp, _dv, _hv = _var_position_size_core(
        flat, account_value_gbp=10_000.0, max_position_gbp=5.0, holding_days=30,
        var_confidence=0.95, max_risk_pct=0.01, min_position_gbp=50.0,
    )
    assert position_gbp == 5.0


def test_severely_inflated_dynamic_ceiling_does_not_distort_a_normal_raw_size():
    """An absurdly large max_position_gbp (e.g. resolve_dynamic_max_position_gbp()
    fed a corrupted/inflated account value further up the call chain -- see
    CLAUDE.md's "Mark-to-market unbounded compounding defect" section) must
    never itself inflate the result -- the raw VaR-implied size is still
    what's returned, unaffected by how large the ceiling is."""
    account_value_gbp = 10_000.0
    holding_days = 30
    holding_var = _DAILY_VAR_95 * math.sqrt(holding_days)
    expected_raw = (account_value_gbp * 0.01) / abs(holding_var)

    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp, max_position_gbp=1e12, holding_days=holding_days,
        var_confidence=0.95, max_risk_pct=0.01, min_position_gbp=1.0,
    )
    assert position_gbp == pytest.approx(round(expected_raw, 2))
    assert position_gbp < 1e6  # nowhere near the inflated ceiling


def test_near_zero_floor_has_no_effect_when_raw_size_is_larger():
    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp=10_000.0, max_position_gbp=2500.0, holding_days=30,
        var_confidence=0.95, max_risk_pct=0.01, min_position_gbp=0.01,
    )
    assert position_gbp > 0.01


def test_near_zero_floor_and_near_zero_ceiling_resolve_to_the_ceiling():
    """Both bounds tiny and close together -- the ceiling (smaller of the
    two here) must still win, matching the general rule, not a special
    case that only holds for "normal-sized" ceilings."""
    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp=1_000_000, max_position_gbp=0.02, holding_days=1,
        var_confidence=0.95, max_risk_pct=0.01, min_position_gbp=0.05,
    )
    assert position_gbp == 0.02


def test_longer_holding_period_scales_holding_var_by_sqrt_time_and_shrinks_position():
    """Square-root-of-time rule: quadrupling holding_days (25 -> 100) must
    scale holding_var by exactly sqrt(4) = 2x, and (since neither clamp
    binds at these values) shrink position_gbp by exactly 1/2."""
    kwargs = dict(
        account_value_gbp=10_000.0, max_position_gbp=1_000_000, var_confidence=0.95,
        max_risk_pct=0.01, min_position_gbp=1.0,
    )
    pos_25, _dv1, hv_25 = _var_position_size_core(_RETURNS, holding_days=25, **kwargs)
    pos_100, _dv2, hv_100 = _var_position_size_core(_RETURNS, holding_days=100, **kwargs)

    assert hv_100 == pytest.approx(hv_25 * 2)
    assert pos_100 == pytest.approx(pos_25 / 2, rel=1e-3)


def test_holding_days_floored_at_one_even_if_passed_as_zero():
    """max(holding_days, 1) inside the function -- a holding_days of 0
    must not zero out or otherwise distort the sqrt-time scaling; it must
    behave identically to holding_days=1."""
    kwargs = dict(
        account_value_gbp=10_000.0, max_position_gbp=1_000_000, var_confidence=0.95,
        max_risk_pct=0.01, min_position_gbp=1.0,
    )
    pos_zero, _dv, hv_zero = _var_position_size_core(_RETURNS, holding_days=0, **kwargs)
    pos_one, _dv2, hv_one = _var_position_size_core(_RETURNS, holding_days=1, **kwargs)
    assert hv_zero == pytest.approx(hv_one)
    assert pos_zero == pytest.approx(pos_one)


def test_higher_confidence_reads_at_least_as_far_into_the_tail():
    """99% confidence reads further into the left tail than 95% for this
    evenly-spread sample, producing a more extreme (more negative)
    daily_var and therefore a smaller or equal position size, all else
    equal (neither clamp binds at these values)."""
    kwargs = dict(
        account_value_gbp=10_000.0, max_position_gbp=1_000_000, holding_days=30,
        max_risk_pct=0.01, min_position_gbp=1.0,
    )
    pos_95, dv_95, _hv95 = _var_position_size_core(_RETURNS, var_confidence=0.95, **kwargs)
    pos_99, dv_99, _hv99 = _var_position_size_core(_RETURNS, var_confidence=0.99, **kwargs)

    assert dv_99 <= dv_95
    assert pos_99 <= pos_95


def test_accepts_pandas_series_input_identically_to_ndarray():
    """Both real callers pass different container types (analysis/backtest.py
    passes a pandas Series sliced/dropna()'d from historical closes;
    brokers/trading212.py passes an equivalent Series built from live price
    history) -- both must produce identical results for the same values."""
    import pandas as pd
    series = pd.Series(_RETURNS)

    result_array = _var_position_size_core(_RETURNS, 10_000.0, 2500.0, 30, 0.95, 0.01, 50.0)
    result_series = _var_position_size_core(series, 10_000.0, 2500.0, 30, 0.95, 0.01, 50.0)
    assert result_array == result_series


def test_returns_position_gbp_rounded_to_two_decimal_places():
    position_gbp, _dv, _hv = _var_position_size_core(
        _RETURNS, account_value_gbp=10_037.777, max_position_gbp=1_000_000, holding_days=17,
        var_confidence=0.95, max_risk_pct=0.013, min_position_gbp=1.0,
    )
    assert position_gbp == round(position_gbp, 2)
