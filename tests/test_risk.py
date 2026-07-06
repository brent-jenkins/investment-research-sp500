"""Tests for analysis/risk.py: drawdown-based position scaling.

compute_drawdown_scale_factor() reduces new T212 BUY position sizes while
the account is in drawdown from its own high water mark — a mechanical,
non-predictive risk control (no new signal or regime call involved). See
that module's docstrings for the full tier/hysteresis mechanism.

Tests here use the db_engine fixture (in-memory SQLite — tests/conftest.py)
since compute_drawdown_scale_factor()/update_peak_portfolio_value() persist
the peak value and current tier via data.database.get_metadata_str()/
set_metadata(), which resolve get_engine() through data.database itself
(already patched by the fixture).
"""

_CONFIG = {
    "trading212_invest": {
        "min_position_gbp": 50,
        "drawdown_scaling": {
            "enabled": True,
            "threshold_1": 0.05,
            "scale_factor_1": 0.75,
            "threshold_2": 0.10,
            "scale_factor_2": 0.50,
            "threshold_3": 0.15,
            "scale_factor_3": 0.25,
            "recovery_buffer": 0.02,
        },
    },
}

_CONFIG_DISABLED = {
    "trading212_invest": {
        "min_position_gbp": 50,
        "drawdown_scaling": {**_CONFIG["trading212_invest"]["drawdown_scaling"], "enabled": False},
    },
}


def test_scale_factor_normal_when_drawdown_below_threshold_1(db_engine):
    from analysis.risk import compute_drawdown_scale_factor

    compute_drawdown_scale_factor(1000, _CONFIG)  # establishes peak = 1000
    scale, drawdown, status = compute_drawdown_scale_factor(970, _CONFIG)  # 3% drawdown

    assert scale == 1.0
    assert status == "NORMAL"
    assert abs(drawdown - 0.03) < 1e-9


def test_scale_factor_caution_between_threshold_1_and_2(db_engine):
    from analysis.risk import compute_drawdown_scale_factor

    compute_drawdown_scale_factor(1000, _CONFIG)
    scale, drawdown, status = compute_drawdown_scale_factor(930, _CONFIG)  # 7% drawdown

    assert scale == 0.75
    assert status == "CAUTION"


def test_scale_factor_reduced_between_threshold_2_and_3(db_engine):
    from analysis.risk import compute_drawdown_scale_factor

    compute_drawdown_scale_factor(1000, _CONFIG)
    scale, drawdown, status = compute_drawdown_scale_factor(880, _CONFIG)  # 12% drawdown

    assert scale == 0.50
    assert status == "REDUCED"


def test_scale_factor_minimal_at_or_above_threshold_3(db_engine):
    from analysis.risk import compute_drawdown_scale_factor

    compute_drawdown_scale_factor(1000, _CONFIG)
    scale, drawdown, status = compute_drawdown_scale_factor(800, _CONFIG)  # 20% drawdown

    assert scale == 0.25
    assert status == "MINIMAL"


def test_scale_factor_one_when_drawdown_scaling_disabled(db_engine):
    """drawdown_scaling.enabled: false must always return scale_factor 1.0,
    even at a severe drawdown — the drawdown itself is still reported for
    informational display."""
    from analysis.risk import compute_drawdown_scale_factor

    compute_drawdown_scale_factor(1000, _CONFIG_DISABLED)
    scale, drawdown, status = compute_drawdown_scale_factor(800, _CONFIG_DISABLED)  # 20% drawdown

    assert scale == 1.0
    assert status == "NORMAL"
    assert drawdown > 0.15


def test_floor_still_applies_after_scaling():
    """Position sizing callers apply max(position_gbp * scale_factor,
    min_position_gbp) — verify the floor still wins even at the most severe
    scale factor (0.25) when the pre-floor scaled amount would fall below
    it."""
    from analysis.risk import _drawdown_tier_from_pct

    dd_cfg = _CONFIG["trading212_invest"]["drawdown_scaling"]
    min_position_gbp = _CONFIG["trading212_invest"]["min_position_gbp"]

    tier, scale = _drawdown_tier_from_pct(0.20, dd_cfg, "NORMAL")
    assert tier == "MINIMAL"
    assert scale == 0.25

    position_gbp = 60.0
    pre_floor = position_gbp * scale
    assert pre_floor == 15.0  # would fall below the floor if not clamped

    scaled_position = max(pre_floor, min_position_gbp)
    assert scaled_position == min_position_gbp


def test_hysteresis_caution_does_not_reset_to_normal_until_recovery_buffer_met(db_engine):
    """Once CAUTION (drawdown >= 5%) is active, recovery back to NORMAL
    requires drawdown to fall below threshold_1 - recovery_buffer (3%), not
    just below threshold_1 (5%) itself."""
    from analysis.risk import compute_drawdown_scale_factor

    compute_drawdown_scale_factor(1000, _CONFIG)  # establish peak = 1000

    _, _, status_caution = compute_drawdown_scale_factor(930, _CONFIG)  # 7% drawdown
    assert status_caution == "CAUTION"

    # Recovers to 4% drawdown — below threshold_1 (5%) but not below the
    # hysteresis exit boundary of 3% (threshold_1 - recovery_buffer).
    scale_partial, _, status_partial = compute_drawdown_scale_factor(960, _CONFIG)
    assert status_partial == "CAUTION"
    assert scale_partial == 0.75

    # Recovers further to 2% drawdown — clears the 3% exit boundary.
    scale_recovered, _, status_recovered = compute_drawdown_scale_factor(980, _CONFIG)
    assert status_recovered == "NORMAL"
    assert scale_recovered == 1.0


def test_hysteresis_escalates_immediately_on_worsening_drawdown(db_engine):
    """Escalating to a more severe tier is never delayed by hysteresis —
    only recovery to a less severe tier is."""
    from analysis.risk import compute_drawdown_scale_factor

    compute_drawdown_scale_factor(1000, _CONFIG)
    _, _, status_caution = compute_drawdown_scale_factor(930, _CONFIG)  # 7% -> CAUTION
    assert status_caution == "CAUTION"

    _, _, status_minimal = compute_drawdown_scale_factor(800, _CONFIG)  # 20% -> MINIMAL immediately
    assert status_minimal == "MINIMAL"


def test_peak_updates_correctly_when_new_account_high_is_reached(db_engine):
    from analysis.risk import update_peak_portfolio_value
    from data.database import get_metadata_str

    update_peak_portfolio_value(1000, _CONFIG)
    assert float(get_metadata_str("t212_peak_value_gbp")) == 1000

    update_peak_portfolio_value(900, _CONFIG)  # lower — must not move the peak down
    assert float(get_metadata_str("t212_peak_value_gbp")) == 1000

    update_peak_portfolio_value(1100, _CONFIG)  # new high
    assert float(get_metadata_str("t212_peak_value_gbp")) == 1100


def test_compute_drawdown_scale_factor_updates_peak_as_a_side_effect(db_engine):
    """compute_drawdown_scale_factor() must also advance the high water mark
    when handed a new account high, not just read it."""
    from analysis.risk import compute_drawdown_scale_factor
    from data.database import get_metadata_str

    compute_drawdown_scale_factor(1000, _CONFIG)
    assert float(get_metadata_str("t212_peak_value_gbp")) == 1000

    scale, drawdown, status = compute_drawdown_scale_factor(1200, _CONFIG)  # new high
    assert float(get_metadata_str("t212_peak_value_gbp")) == 1200
    assert drawdown == 0.0
    assert scale == 1.0
    assert status == "NORMAL"
