"""Unit tests for analysis/decision.py — the single shared implementation of
the entry-gate and exit-priority decisions. See tests/test_live_backtest_parity.py
for the integration-level test that confirms every call site (live order
placement, all three backtest engines, and the display-only Daily Briefing
path) is actually wired to these functions, not just that the functions
themselves are correct in isolation.
"""
from analysis.decision import (
    REENTRY_COOLDOWN_DAYS,
    evaluate_exit_reason,
    is_asset_class_enabled,
    is_position_size_viable,
    is_reentry_blocked,
    passes_entry_gate,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
)


def _exit(**overrides):
    base = dict(
        crisis_now=False, price=100.0, stop_price=90.0,
        sig_on=True, holding_days=1, max_holding_days=30,
    )
    base.update(overrides)
    return evaluate_exit_reason(**base)


def test_crisis_wins_over_everything():
    assert _exit(crisis_now=True, price=50.0, holding_days=999) == "CRISIS_EXIT"


def test_evaluate_exit_reason_has_no_is_member_parameter():
    """Index membership was removed from the exit-priority chain entirely
    (2026-08-02, see this function's own docstring) -- membership now
    ONLY gates new entries (passes_entry_gate() below), never an
    already-open position's exit. Confirmed structurally, not just by
    absence of behaviour: passing is_member at all must raise, so a
    future accidental re-introduction of the parameter (with a default
    that silently does nothing) can't quietly slip back in unnoticed."""
    import pytest
    with pytest.raises(TypeError):
        evaluate_exit_reason(
            is_member=False, crisis_now=False, price=100.0, stop_price=90.0,
            sig_on=True, holding_days=1, max_holding_days=30,
        )


def test_no_exit_regardless_of_membership_when_momentum_stop_and_holding_are_all_fine():
    """The direct behavioural confirmation of the change: a position with
    positive momentum, price above its stop, and holding days under the
    limit produces NO exit -- there is no way to even express "this
    ticker just lost index membership" to evaluate_exit_reason() any
    more, so a real-world removal with the position otherwise healthy
    can never force a close. This is the exact scenario the 2026-08-02
    historical investigation found in 91% of past INDEX_REMOVAL exits."""
    assert _exit() is None


def test_stop_loss_before_signal_off_and_max_holding():
    assert _exit(price=80.0, stop_price=90.0, sig_on=False, holding_days=999) == "STOP_LOSS"


def test_signal_off_before_max_holding():
    assert _exit(sig_on=False, holding_days=999, max_holding_days=30) == "SIGNAL_OFF"


def test_max_holding_when_nothing_else_fires():
    assert _exit(holding_days=30, max_holding_days=30) == "MAX_HOLDING"


def test_no_exit_when_nothing_fires():
    assert _exit() is None


def test_stop_loss_boundary_is_inclusive():
    assert _exit(price=90.0, stop_price=90.0) == "STOP_LOSS"


def test_none_price_or_stop_never_triggers_stop_loss():
    assert _exit(price=None, stop_price=90.0) is None
    assert _exit(price=50.0, stop_price=None) is None


def test_none_holding_days_or_max_never_triggers_max_holding():
    assert _exit(holding_days=None, max_holding_days=30) is None
    assert _exit(holding_days=999, max_holding_days=None) is None


def test_unknown_sig_on_none_does_not_trigger_signal_off():
    assert _exit(sig_on=None) is None


def test_unknown_crisis_none_does_not_trigger_crisis_exit():
    assert _exit(crisis_now=None) is None


# ---------------------------------------------------------------------------
# passes_entry_gate
# ---------------------------------------------------------------------------

def test_entry_gate_passes_only_when_all_three_confirmed():
    assert passes_entry_gate(sig_on=True, is_member=True, entry_blocked=False) is True


def test_entry_gate_fails_on_signal_off():
    assert passes_entry_gate(sig_on=False, is_member=True, entry_blocked=False) is False


def test_entry_gate_fails_on_not_a_member():
    assert passes_entry_gate(sig_on=True, is_member=False, entry_blocked=False) is False


def test_entry_gate_fails_when_entry_blocked():
    assert passes_entry_gate(sig_on=True, is_member=True, entry_blocked=True) is False


def test_entry_gate_fails_on_unknown_inputs():
    """None (unknown / not yet computed) never passes the gate on any of
    the three conditions -- an entry decision should never be made on
    incomplete data."""
    assert passes_entry_gate(sig_on=None, is_member=True, entry_blocked=False) is False
    assert passes_entry_gate(sig_on=True, is_member=None, entry_blocked=False) is False
    assert passes_entry_gate(sig_on=True, is_member=True, entry_blocked=None) is False


# ---------------------------------------------------------------------------
# is_asset_class_enabled
# ---------------------------------------------------------------------------

def test_asset_class_enabled_defaults_true_with_no_config_keys():
    """A config predating this feature (no etf_trading_enabled /
    stock_trading_enabled keys at all) must behave exactly as before --
    both asset classes enabled by default."""
    cfg = {"trading212_invest": {}}
    assert is_asset_class_enabled(is_etf=True, config=cfg) is True
    assert is_asset_class_enabled(is_etf=False, config=cfg) is True


def test_asset_class_enabled_defaults_true_with_no_trading212_invest_section_at_all():
    assert is_asset_class_enabled(is_etf=True, config={}) is True
    assert is_asset_class_enabled(is_etf=False, config={}) is True


def test_etf_trading_disabled_does_not_affect_stock_trading():
    cfg = {"trading212_invest": {"etf_trading_enabled": False}}
    assert is_asset_class_enabled(is_etf=True, config=cfg) is False
    assert is_asset_class_enabled(is_etf=False, config=cfg) is True  # unaffected -- no key set for stocks


def test_stock_trading_disabled_does_not_affect_etf_trading():
    cfg = {"trading212_invest": {"stock_trading_enabled": False}}
    assert is_asset_class_enabled(is_etf=False, config=cfg) is False
    assert is_asset_class_enabled(is_etf=True, config=cfg) is True  # unaffected -- no key set for ETFs


def test_both_asset_classes_can_be_independently_disabled_at_once():
    cfg = {"trading212_invest": {"etf_trading_enabled": False, "stock_trading_enabled": False}}
    assert is_asset_class_enabled(is_etf=True, config=cfg) is False
    assert is_asset_class_enabled(is_etf=False, config=cfg) is False


def test_both_asset_classes_explicitly_true_is_the_same_as_absent():
    cfg = {"trading212_invest": {"etf_trading_enabled": True, "stock_trading_enabled": True}}
    assert is_asset_class_enabled(is_etf=True, config=cfg) is True
    assert is_asset_class_enabled(is_etf=False, config=cfg) is True


# ---------------------------------------------------------------------------
# resolve_dynamic_max_position_gbp() / resolve_cash_constrained_position_gbp()
# -- the shared sizing-ceiling fix for the mark-to-market unbounded
# compounding defect (2026-08-01, see CLAUDE.md's "Mark-to-market unbounded
# compounding defect" section). Direct, deterministic tests on the actual
# mechanism -- a synthetic multi-year backtest scenario (see
# tests/test_portfolio_backtest.py's own scale test) genuinely cannot
# reproduce the full magnitude of the original production-scale defect
# (which involved dozens of simultaneously-appreciating tickers compounding
# against each other over thousands of trades) within a fast unit test, so
# these direct tests on the formula itself are the reliable, unambiguous
# regression guard for the actual fix -- not a substitute for the
# production-scale re-verification this fix was built to unblock.
# ---------------------------------------------------------------------------

def test_resolve_dynamic_max_position_gbp_uses_percentage_when_under_ceiling():
    assert resolve_dynamic_max_position_gbp(10_000, 0.05, 1_000_000) == 500.0


def test_resolve_dynamic_max_position_gbp_caps_at_absolute_ceiling_when_account_value_is_inflated():
    """The exact failure mode found 2026-08-01: account_value_gbp inflated
    by unrealized appreciation on an unrelated open position must never
    translate into an uncapped sizing ceiling for a brand new entry."""
    absurdly_inflated_account_value = 1e60
    assert resolve_dynamic_max_position_gbp(absurdly_inflated_account_value, 0.05, 1_000_000) == 1_000_000


def test_resolve_dynamic_max_position_gbp_boundary_exactly_at_ceiling():
    # per_trade_pct * account_value lands EXACTLY on the ceiling -- must
    # not be pushed either side of it by a boundary/off-by-one error.
    assert resolve_dynamic_max_position_gbp(20_000_000, 0.05, 1_000_000) == 1_000_000


def test_resolve_cash_constrained_position_gbp_binds_when_position_exceeds_cash_fraction():
    assert resolve_cash_constrained_position_gbp(10_000, 5_000, cash_fraction=0.25) == 1_250.0


def test_resolve_cash_constrained_position_gbp_no_effect_when_comfortably_under_cash_fraction():
    assert resolve_cash_constrained_position_gbp(500, 5_000, cash_fraction=0.25) == 500


def test_is_position_size_viable_rejects_below_threshold_accepts_at_or_above():
    assert is_position_size_viable(9.99) is False
    assert is_position_size_viable(10.0) is True
    assert is_position_size_viable(10.01) is True
    assert is_position_size_viable(0.5) is False


def test_resolve_cash_constrained_position_gbp_is_never_inflated_by_unrealized_gains():
    """The specific structural reason this clamp closes most of the
    exposure even before the absolute ceiling above does: available_cash_gbp
    must reflect only genuine, realised, spendable cash -- confirmed here
    simply by the function never looking at anything BUT the two arguments
    it's given, so a caller passing a real, unrealized-gains-free cash
    figure (as both live and the mark-to-market backtest engine now do)
    gets a clamp that's structurally immune to this defect's root cause,
    regardless of how inflated a separate account_value_gbp figure
    elsewhere becomes."""
    assert resolve_cash_constrained_position_gbp(1e60, 1_000, cash_fraction=0.25) == 250.0


# ---------------------------------------------------------------------------
# is_reentry_blocked() (2026-08-03) -- the shared 30-day re-entry cooldown
# threshold. See tests/test_portfolio_backtest.py for the MTM-engine-level
# proof this is genuinely wired in, and tests/test_execution.py for live's
# own _load_open_order_tickers() boundary confirmation.
# ---------------------------------------------------------------------------

def test_reentry_cooldown_default_is_30_days():
    assert REENTRY_COOLDOWN_DAYS == 30


def test_is_reentry_blocked_true_within_window_false_at_and_past_boundary():
    assert is_reentry_blocked(0) is True
    assert is_reentry_blocked(29) is True
    assert is_reentry_blocked(29.9) is True
    assert is_reentry_blocked(30) is False  # exactly 30 days -- no longer blocked
    assert is_reentry_blocked(31) is False


def test_is_reentry_blocked_none_never_blocks():
    """A ticker with no order/trade history at all has nothing to cool
    down from -- must never be blocked."""
    assert is_reentry_blocked(None) is False


def test_is_reentry_blocked_respects_a_custom_cooldown_period():
    assert is_reentry_blocked(10, cooldown_days=14) is True
    assert is_reentry_blocked(14, cooldown_days=14) is False
