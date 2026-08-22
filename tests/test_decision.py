"""Unit tests for analysis/decision.py — the single shared implementation of
the entry-gate and exit-priority decisions. See tests/test_live_backtest_parity.py
for the integration-level test that confirms every call site (live order
placement, all three backtest engines, and the display-only Daily Briefing
path) is actually wired to these functions, not just that the functions
themselves are correct in isolation.
"""
from analysis.decision import (
    ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM,
    ADMISSION_RANKING_MODE_RELATIVE_RANK,
    MIN_MOMENTUM_FLOOR,
    REENTRY_COOLDOWN_DAYS,
    evaluate_exit_reason,
    evaluate_rank_dropped_exit,
    is_asset_class_enabled,
    is_position_size_viable,
    is_reentry_blocked,
    is_submission_price_move_blocked,
    passes_entry_gate,
    passes_momentum_floor,
    resolve_admission_rank_value,
    resolve_admission_ranking_mode,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
    resolve_min_momentum_floor,
    resolve_reentry_cooldown_days,
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


# ---------------------------------------------------------------------------
# resolve_reentry_cooldown_days() (2026-08-03) -- the shared config-lookup
# both live order placement and simulate_portfolio_compounding_mtm() call
# to resolve the real cooldown value, so neither can silently resolve a
# different one for the same config. Production sets
# trading212_invest.reentry_cooldown_days: 0 -- see CLAUDE.md's "Re-entry
# cooldown set to 0 (config-driven)" section.
# ---------------------------------------------------------------------------

def test_resolve_reentry_cooldown_days_reads_the_configured_value():
    assert resolve_reentry_cooldown_days({"trading212_invest": {"reentry_cooldown_days": 0}}) == 0
    assert resolve_reentry_cooldown_days({"trading212_invest": {"reentry_cooldown_days": 7}}) == 7


def test_resolve_reentry_cooldown_days_falls_back_to_30_when_key_absent():
    """A config predating this parameter (no reentry_cooldown_days key at
    all) must behave exactly as before this change shipped -- the
    original 30-day REENTRY_COOLDOWN_DAYS default."""
    assert resolve_reentry_cooldown_days({"trading212_invest": {}}) == 30
    assert resolve_reentry_cooldown_days({}) == REENTRY_COOLDOWN_DAYS


def test_resolve_reentry_cooldown_days_zero_means_no_cooldown_at_all():
    assert is_reentry_blocked(0, cooldown_days=resolve_reentry_cooldown_days(
        {"trading212_invest": {"reentry_cooldown_days": 0}}
    )) is False


# ---------------------------------------------------------------------------
# passes_momentum_floor() / resolve_min_momentum_floor() (2026-08-03) -- the
# shared minimum-momentum-floor threshold and config-lookup, both live order
# placement and simulate_portfolio_compounding_mtm() call to resolve the
# real floor value. Production sets trading212_invest.min_momentum_pct:
# 0.02 -- see CLAUDE.md's "Does the entry gate need an absolute minimum
# momentum floor?" and "Block-based confirmation of the 2% momentum floor"
# sections.
# ---------------------------------------------------------------------------

def test_min_momentum_floor_default_is_zero():
    assert MIN_MOMENTUM_FLOOR == 0.0


def test_passes_momentum_floor_true_at_and_above_floor_false_below():
    assert passes_momentum_floor(0.019, floor=0.02) is False
    assert passes_momentum_floor(0.02, floor=0.02) is True  # exactly at floor -- passes
    assert passes_momentum_floor(0.021, floor=0.02) is True
    assert passes_momentum_floor(0.50, floor=0.02) is True


def test_passes_momentum_floor_none_never_passes_a_genuine_configured_floor():
    """Unknown momentum must never be treated as clearing a REAL,
    configured (>0) floor -- matches this module's own "None never
    silently passes" convention."""
    assert passes_momentum_floor(None, floor=0.02) is False
    assert passes_momentum_floor(None, floor=0.001) is False


def test_passes_momentum_floor_zero_floor_is_a_true_no_op_even_with_unknown_momentum():
    """The critical backwards-compatibility guarantee: at the default,
    unconfigured floor (0.0), ANY candidate that already cleared
    passes_entry_gate()'s sig_on check must keep passing -- including one
    whose momentum value isn't separately known/tracked at all (e.g. a
    test fixture or call site that predates this feature and never set a
    momentum value, since momentum was never part of entry-gate logic
    before this change). A stricter "None never passes" rule applied
    unconditionally here would silently break every such caller."""
    assert passes_momentum_floor(None) is True
    assert passes_momentum_floor(None, floor=0.0) is True


def test_passes_momentum_floor_default_floor_is_zero_reproduces_old_behaviour():
    """With the fallback default (no floor beyond momentum > 0, already
    enforced by the signal itself), any positive momentum passes --
    reproducing the exact pre-2026-08-03 gate."""
    assert passes_momentum_floor(0.001) is True
    assert passes_momentum_floor(0.0) is True  # boundary: momentum==0 still "passes" this check specifically -- sig_on already requires strictly >0 upstream


def test_resolve_min_momentum_floor_reads_the_configured_value():
    assert resolve_min_momentum_floor({"trading212_invest": {"min_momentum_pct": 0.02}}) == 0.02
    assert resolve_min_momentum_floor({"trading212_invest": {"min_momentum_pct": 0.05}}) == 0.05


def test_resolve_min_momentum_floor_falls_back_to_zero_when_key_absent():
    """A config predating this parameter (no min_momentum_pct key at all)
    must behave exactly as before this change shipped -- no additional
    floor beyond momentum > 0."""
    assert resolve_min_momentum_floor({"trading212_invest": {}}) == 0.0
    assert resolve_min_momentum_floor({}) == MIN_MOMENTUM_FLOOR


def test_resolve_min_momentum_floor_two_percent_excludes_marginal_candidate():
    floor = resolve_min_momentum_floor({"trading212_invest": {"min_momentum_pct": 0.02}})
    assert passes_momentum_floor(0.005, floor) is False  # 0.5% momentum -- excluded
    assert passes_momentum_floor(0.03, floor) is True     # 3% momentum -- unaffected


# ---------------------------------------------------------------------------
# is_submission_price_move_blocked() -- asymmetric price-sanity check
# (2026-08-03). See CLAUDE.md's "Price-sanity threshold -- momentum-skew
# investigation" section for the 176-run block-based evidence this is
# based on, and brokers/trading212.py:_submit_pending_orders() for the
# real call site.
# ---------------------------------------------------------------------------

def test_price_move_blocked_rise_never_blocks_regardless_of_size():
    """The core behavioural change: any rise, however large, must never
    block -- a rise confirms the momentum thesis rather than
    invalidating it."""
    assert is_submission_price_move_blocked(100.0, 101.0, 0.03) is False   # +1%
    assert is_submission_price_move_blocked(100.0, 103.0, 0.03) is False   # +3%, at the threshold
    assert is_submission_price_move_blocked(100.0, 110.0, 0.03) is False   # +10%, far past
    assert is_submission_price_move_blocked(100.0, 200.0, 0.03) is False   # +100%, extreme


def test_price_move_blocked_drop_past_threshold_blocks():
    assert is_submission_price_move_blocked(100.0, 90.0, 0.03) is True     # -10%, well past
    assert is_submission_price_move_blocked(100.0, 96.0, 0.03) is True     # -4%, just past


def test_price_move_blocked_drop_under_threshold_does_not_block():
    assert is_submission_price_move_blocked(100.0, 97.5, 0.03) is False    # -2.5%, under 3%
    assert is_submission_price_move_blocked(100.0, 99.9, 0.03) is False    # -0.1%, trivial


def test_price_move_blocked_drop_exactly_at_threshold_does_not_block():
    """Strict inequality, matching the original symmetric check's own
    strict `>` convention exactly -- never inclusive at the boundary."""
    assert is_submission_price_move_blocked(100.0, 97.0, 0.03) is False    # exactly -3.00%


def test_price_move_blocked_drop_just_over_threshold_blocks():
    assert is_submission_price_move_blocked(100.0, 96.99, 0.03) is True    # -3.01%, just past


def test_price_move_blocked_no_price_change_never_blocks():
    assert is_submission_price_move_blocked(100.0, 100.0, 0.03) is False


def test_price_move_blocked_non_positive_decided_price_never_blocks():
    """No genuine basis for comparison -- fail toward not blocking rather
    than dividing by zero or guessing. Should not occur in practice (a
    queued decision always has a real recorded price)."""
    assert is_submission_price_move_blocked(0.0, 90.0, 0.03) is False
    assert is_submission_price_move_blocked(-5.0, 90.0, 0.03) is False


def test_rank_dropped_fires_when_not_in_current_top_n():
    assert evaluate_rank_dropped_exit(is_in_current_top_n=False) == "RANK_DROPPED"


def test_rank_dropped_does_not_fire_when_in_current_top_n():
    assert evaluate_rank_dropped_exit(is_in_current_top_n=True) is None


def test_rank_dropped_unknown_none_does_not_trigger():
    """None means 'not a rebalance date, or ranking not computed' --
    matches this module's own None-tolerance convention throughout:
    unknown is never treated as an affirmative trigger."""
    assert evaluate_rank_dropped_exit(is_in_current_top_n=None) is None


# ---------------------------------------------------------------------------
# resolve_admission_ranking_mode() / resolve_admission_rank_value() --
# promoted 2026-08-09 from the relative-ranking study's own research engine.
# See analysis/decision.py's own docstrings for the full record and the
# "narrowed on review" scope: this changes ONLY the admission sort key,
# never the entry gate / momentum floor / SIGNAL_OFF.
# ---------------------------------------------------------------------------

def test_resolve_admission_ranking_mode_defaults_to_absolute_momentum():
    """A config predating this parameter (no admission_ranking_mode key at
    all) must behave exactly as today's validated production system --
    same guarantee every other config-driven parameter in this module
    provides."""
    assert resolve_admission_ranking_mode({"trading212_invest": {}}) == ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM
    assert resolve_admission_ranking_mode({}) == ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM


def test_resolve_admission_ranking_mode_reads_the_configured_value():
    assert resolve_admission_ranking_mode(
        {"trading212_invest": {"admission_ranking_mode": "relative_rank"}}
    ) == ADMISSION_RANKING_MODE_RELATIVE_RANK


def test_resolve_admission_rank_value_absolute_momentum_mode_returns_momentum():
    assert resolve_admission_rank_value(
        momentum=0.15, relative_rank=0.40, mode=ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM,
    ) == 0.15


def test_resolve_admission_rank_value_relative_rank_mode_returns_relative_rank():
    assert resolve_admission_rank_value(
        momentum=0.15, relative_rank=0.40, mode=ADMISSION_RANKING_MODE_RELATIVE_RANK,
    ) == 0.40


def test_resolve_admission_rank_value_unrecognised_mode_falls_back_to_momentum():
    """Fails safe to today's validated behaviour, never to an unvalidated
    one -- an unrecognised mode string must never silently resolve to
    relative_rank."""
    assert resolve_admission_rank_value(
        momentum=0.15, relative_rank=0.40, mode="not_a_real_mode",
    ) == 0.15


def test_resolve_admission_rank_value_can_return_none_for_either_input():
    assert resolve_admission_rank_value(
        momentum=None, relative_rank=0.40, mode=ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM,
    ) is None
    assert resolve_admission_rank_value(
        momentum=0.15, relative_rank=None, mode=ADMISSION_RANKING_MODE_RELATIVE_RANK,
    ) is None


# ---------------------------------------------------------------------------
# Share-class exclusion (2026-08-22) -- unit coverage of the shared function
# and the shipped map. The live/backtest parity coverage lives in
# tests/test_share_class_parity.py; these are the "is the function itself
# right" tests that a parity test deliberately cannot substitute for.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
from analysis.decision import (  # noqa: E402
    blocking_share_class_siblings,
    resolve_share_class_exclusion_enabled,
)
from analysis import universe as _universe  # noqa: E402


def test_share_class_exclusion_defaults_to_off_so_shipping_changes_nothing():
    assert resolve_share_class_exclusion_enabled({}) is False
    assert resolve_share_class_exclusion_enabled({"trading212_invest": {}}) is False
    assert resolve_share_class_exclusion_enabled(
        {"trading212_invest": {"share_class_exclusion": True}}) is True


def test_a_held_sibling_blocks_and_names_the_blocker():
    blocked = blocking_share_class_siblings(
        siblings=frozenset({"GOOGL"}), held_tickers={"GOOGL", "AAPL"}, enabled=True)
    assert blocked == ("GOOGL",)


def test_no_sibling_held_means_no_change():
    assert blocking_share_class_siblings(
        siblings=frozenset({"GOOGL"}), held_tickers={"AAPL", "MSFT"}, enabled=True) == ()


def test_a_ticker_with_no_mapping_is_never_blocked():
    assert _universe.get_share_class_siblings("AAPL") == frozenset()
    assert blocking_share_class_siblings(
        siblings=_universe.get_share_class_siblings("AAPL"),
        held_tickers={"AAPL", "GOOGL", "GOOG"}, enabled=True) == ()


def test_disabled_never_blocks_even_when_a_sibling_is_held():
    assert blocking_share_class_siblings(
        siblings=frozenset({"GOOGL"}), held_tickers={"GOOGL"}, enabled=False) == ()


@pytest.mark.parametrize("siblings,held", [
    (None, {"GOOGL"}), (frozenset(), {"GOOGL"}),
    (frozenset({"GOOGL"}), None), (frozenset({"GOOGL"}), set()),
    (None, None),
])
def test_none_and_empty_inputs_mean_unknown_never_blocked(siblings, held):
    """This module's standing None-tolerance contract: unknown must never
    trigger a check, and must not rely on falsy coercion by accident."""
    assert blocking_share_class_siblings(
        siblings=siblings, held_tickers=held, enabled=True) == ()


def test_multiple_held_siblings_are_all_reported_sorted():
    blocked = blocking_share_class_siblings(
        siblings=frozenset({"B", "A"}), held_tickers={"A", "B", "C"}, enabled=True)
    assert blocked == ("A", "B")


def test_the_shipped_map_is_symmetric_and_self_excluding():
    """Every mapped ticker must see its group-mates and never itself --
    a ticker that returned itself would block its own admission outright."""
    assert _universe.SHARE_CLASS_GROUPS, "shipped map is empty — every test above is vacuous"
    seen = set()
    for group in _universe.SHARE_CLASS_GROUPS:
        assert len(group) >= 2, f"a group of one blocks nothing: {group}"
        assert not (group & seen), f"ticker appears in two groups: {group & seen}"
        seen |= set(group)
        for ticker in group:
            sibs = _universe.get_share_class_siblings(ticker)
            assert ticker not in sibs, f"{ticker} is its own sibling"
            assert sibs == group - {ticker}
            for other in sibs:
                assert ticker in _universe.get_share_class_siblings(other), "asymmetric map"


def test_the_shipped_map_contains_the_pair_that_motivated_it():
    assert _universe.get_share_class_siblings("GOOGL") == frozenset({"GOOG"})
