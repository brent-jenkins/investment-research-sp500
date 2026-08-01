"""Unit tests for analysis/decision.py — the single shared implementation of
the entry-gate and exit-priority decisions. See tests/test_live_backtest_parity.py
for the integration-level test that confirms every call site (live order
placement, all three backtest engines, and the display-only Daily Briefing
path) is actually wired to these functions, not just that the functions
themselves are correct in isolation.
"""
from analysis.decision import evaluate_exit_reason, is_asset_class_enabled, passes_entry_gate


def _exit(**overrides):
    base = dict(
        is_member=True, crisis_now=False, price=100.0, stop_price=90.0,
        sig_on=True, holding_days=1, max_holding_days=30,
    )
    base.update(overrides)
    return evaluate_exit_reason(**base)


def test_crisis_wins_over_everything():
    assert _exit(crisis_now=True, is_member=False, price=50.0, holding_days=999) == "CRISIS_EXIT"


def test_index_removal_before_stop_and_signal():
    assert _exit(is_member=False, price=50.0, sig_on=False) == "INDEX_REMOVAL"


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


def test_unknown_is_member_none_does_not_trigger_index_removal():
    """None means "unknown", not "confirmed removed" -- only a definite
    False triggers INDEX_REMOVAL (matters for output/performance.py's
    display path, which can have incomplete data for a ticker)."""
    assert _exit(is_member=None) is None


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
