"""Shared entry/exit decision logic — the single implementation of "should a
new position open" and "why (if at all) should an open position close,
checked in what order" for the momentum + crisis-override strategy.

This module exists specifically to close a recurring failure mode found
2026-08-01 (see CLAUDE.md's "Live/backtest decision-logic parity" section):
live order placement (brokers/trading212.py) and the backtest engine
(analysis/backtest.py) each grew their own hand-written copy of these two
decisions — portfolio caps, compounding, and trade-priority ranking all
independently drifted apart before anyone was looking for it, three separate
times in one day. `evaluate_exit_reason()` and `passes_entry_gate()` were
previously written out as an inline if/elif chain (and a plain `and`
expression) independently in five places: brokers/trading212.py's
maybe_exit_positions()/maybe_place_orders(), analysis/backtest.py's
simulate_trades()/simulate_portfolio()/simulate_portfolio_compounding(), and
output/performance.py's get_brief_cards() (display only, but computing the
identical classification). All five now call the functions below instead —
there is exactly one implementation of each decision, not five that are
supposed to agree.

Every input tolerates None ("unknown / not yet computed — this specific
check contributes nothing, fall through") rather than relying on Python's
`not None == True` coercion, which would silently misclassify unknown data
as a definite trigger. Live order placement and the backtest engine never
actually pass None for any of these (both only ever call these functions
once every input has been confirmed a real value) — the None-tolerance
exists for output/performance.py's display path, which can have a ticker
with insufficient history or a stale API read.
"""
from typing import Optional


def evaluate_exit_reason(
    *,
    crisis_now: Optional[bool],
    price: Optional[float],
    stop_price: Optional[float],
    sig_on: Optional[bool],
    holding_days: Optional[int],
    max_holding_days: Optional[int],
) -> Optional[str]:
    """Return the exit reason for an open position, or None if nothing
    currently calls for exit. Priority order (first match wins), matching
    the validated backtest exactly:

        1. CRISIS_EXIT — today is a raw (>=7%) S&P 500 crisis day
        2. STOP_LOSS   — price has reached the ATR stop fixed at entry
        3. SIGNAL_OFF  — trailing 12-month momentum has turned non-positive
        4. MAX_HOLDING — the per-ticker holding-days backstop is reached

    `sig_on`/`crisis_now` of False specifically (not merely falsy) is what
    triggers SIGNAL_OFF/(crisis is checked as `is True`) — None is treated
    as "unknown, don't trigger this check", never as an affirmative trigger.

    Index membership is deliberately NOT an exit trigger here (removed
    2026-08-02 — see CLAUDE.md's "let momentum, not index removal, govern
    already-open positions" section). Membership still gates what can be
    newly OPENED (passes_entry_gate() below, unaffected by this change) —
    but once a position is open, a ticker leaving the S&P 500 is a fact
    about the INDEX, not about the ticker's own trading behaviour, and is
    no different in kind from every other reason this system deliberately
    doesn't act on (no take-profit, no position-size cap): closing a
    genuinely winning position for a reason unrelated to its own momentum,
    stop, or holding period contradicts the "let winners run" principle
    every other exit rule here already follows. An already-open position
    is now governed purely by its own trading behaviour — crisis, stop,
    signal, holding period — never by whether the index itself still lists
    it. A real 2026-08-02 investigation of every historical INDEX_REMOVAL
    exit (2007-2026, real membership history + real price data) found this
    was not a rare edge case: 91% of those exits had genuinely positive —
    often strongly rising — momentum at the moment of forced closure, i.e.
    SIGNAL_OFF would NOT have already produced the same outcome. Genuine
    delisting/bankruptcy/acquisition is a different situation, handled by
    reconcile_positions_with_broker() (brokers/trading212.py) regardless of
    index membership status — untouched by this change.
    """
    if crisis_now is True:
        return "CRISIS_EXIT"
    if stop_price is not None and price is not None and price <= stop_price:
        return "STOP_LOSS"
    if sig_on is False:
        return "SIGNAL_OFF"
    if holding_days is not None and max_holding_days is not None and holding_days >= max_holding_days:
        return "MAX_HOLDING"
    return None


def passes_entry_gate(
    *,
    sig_on: Optional[bool],
    is_member: Optional[bool],
    entry_blocked: Optional[bool],
) -> bool:
    """True only when all three hold: momentum signal on, currently a
    genuine index member (or an ETF — is_member already folds that in),
    and not inside the crisis entry-block window.

    Every condition requires an explicit, confirmed value — sig_on/is_member
    must be True (not merely truthy) and entry_blocked must be False (not
    merely falsy); None (unknown) fails the gate on any of the three,
    matching how a real entry decision should never be made on incomplete
    data.
    """
    return sig_on is True and is_member is True and entry_blocked is False


MIN_MOMENTUM_FLOOR = 0.0
"""The FALLBACK default minimum-momentum floor, used only when
trading212_invest.min_momentum_pct is absent from config entirely
(backwards compatible with a config predating this parameter -- 0.0
reproduces the exact pre-2026-08-03 behaviour, where the entry gate's
only momentum requirement was momentum > 0.0, no additional floor).
See resolve_min_momentum_floor() immediately below for the real, live
value.

Investigated 2026-08-03: the entry gate required only momentum > 0, no
distinction between a genuinely trending candidate and one that is
barely, statistically-indistinguishably positive. A historical
frequency check found every real candidate being only marginally
positive while account capacity sat idle is genuinely rare but not
zero (0.9% of capacity-available days, ~3% of all admitted trades at a
2% threshold). A reset-at-boundary sweep (0/2/5/10/15%) found 2% close
to free (one excluded trade in the six-year test period, already a
loser) while 15% had a demonstrated cost (excluding two real winners,
TSLA and CEG). A follow-up block-independent confirmation across the
full 19-year validated window found the excluded population at 2% is a
clear net loser overall (24.5% win rate, -1.51% mean P&L), concentrated
mostly in the GFC block -- exactly the "best of a bad bunch" scenario
this floor exists to guard against -- with only one genuine winner
(LEN, +22.0%) sacrificed across the entire window. See CLAUDE.md's
"Does the entry gate need an absolute minimum momentum floor?" and
"Block-based confirmation of the 2% momentum floor" sections for the
full investigation. Production runs at 0.02 (2%), not this 0.0
fallback.

Only simulate_portfolio_compounding_mtm() (analysis/backtest.py) --
the engine analysis/validation.py:run_full_validation() actually
defaults to, the one confirmed to genuinely match live order placement
-- enforces this, mirroring the exact same deliberate exemption
REENTRY_COOLDOWN_DAYS/is_reentry_blocked() already established for
simulate_trades()/simulate_portfolio()/simulate_portfolio_compounding():
each of those three is an intentionally frozen engine, kept alongside
the corrected one specifically so an already-published historical
figure that cites it stays exactly reproducible on demand. Adding a
new behavioural change to a frozen engine would break that
reproducibility guarantee, not fix a parity gap that matters for
anything live currently does.
"""


def resolve_min_momentum_floor(config: dict) -> float:
    """The real, currently-configured minimum-momentum floor -- reads
    trading212_invest.min_momentum_pct, falling back to MIN_MOMENTUM_FLOOR
    (0.0, i.e. no additional floor) only when the key is absent entirely.

    Both live order placement (brokers/trading212.py:maybe_place_orders())
    and the backtest engine (simulate_portfolio_compounding_mtm()) call
    this ONE function to resolve the value they then pass to
    passes_momentum_floor() -- so neither can silently resolve a
    different floor for the same config. This is the exact same
    config-driven-parameter pattern already used for
    resolve_reentry_cooldown_days() immediately below in this file's own
    history, and for per_trade_pct / max_total_deployed elsewhere.
    """
    t212_cfg = config.get("trading212_invest", {}) or {}
    return float(t212_cfg.get("min_momentum_pct", MIN_MOMENTUM_FLOOR))


def passes_momentum_floor(momentum: Optional[float], floor: float = MIN_MOMENTUM_FLOOR) -> bool:
    """True when floor is a no-op (<=0.0 -- the backwards-compatible
    default, meaning "no additional requirement beyond momentum > 0.0,
    already enforced by passes_entry_gate()'s sig_on"), regardless of
    whether momentum itself is known. This is deliberate, not an
    oversight: before this feature existed, momentum's exact value was
    never part of entry-gate logic at all (only used for ranking), so a
    caller/test fixture with sig_on=True but no separately-tracked
    momentum value must keep working identically when the floor is
    unconfigured -- exactly the same "a config/fixture predating this
    parameter behaves exactly as before" guarantee every other
    config-driven parameter in this file provides.

    Once floor is a genuine, configured requirement (>0.0), an unknown
    momentum (None) fails closed -- matching this module's own "None
    (unknown) never passes" convention elsewhere: an entry decision
    should never be made on incomplete data once there is a real
    requirement to check it against. In practice a candidate only ever
    reaches this check after already passing passes_entry_gate()
    (sig_on=True, which itself requires momentum > 0.0), so momentum=None
    with floor>0 should never actually occur at either real call site --
    checked explicitly anyway, for the same reason.
    """
    if floor <= 0.0:
        return True
    return momentum is not None and momentum >= floor


def is_asset_class_enabled(*, is_etf: bool, config: dict) -> bool:
    """True if NEW-ENTRY CANDIDACY is enabled for this asset class (ETF vs
    stock) — read identically by live order placement's decide phase
    (brokers/trading212.py:maybe_place_orders()) and by the backtest
    engine's candidate-universe construction
    (analysis/validation.py:run_full_validation()), so neither can silently
    diverge on which asset classes are even considered. See CLAUDE.md's
    "Clean, independent ETF/stock trading switches" section for the
    incident this closes — every prior comparison isolating ETF vs. stock
    performance needed a bespoke, hand-rolled research-clone setup each
    time, and at least one such comparison was run inconsistently without
    anyone noticing until it was directly checked.

    Reads two config keys from `config["trading212_invest"]`, both
    defaulting True (backwards compatible — a config predating this
    feature behaves exactly as before):
      - etf_trading_enabled
      - stock_trading_enabled

    Candidacy only — never exits. A ticker whose asset class is disabled
    here is never even evaluated as an entry candidate (no signal
    computed, no VaR sizing done, no pending_orders row ever written,
    and — for the backtest — never included in the ticker universe passed
    to any simulation engine at all), so the OTHER asset class's momentum
    ranking and capacity allocation are completely unaffected by it, the
    same as if the disabled class had been removed from the tradeable
    universe entirely. An already-open position in a disabled asset class
    must still be monitored for its normal exit conditions
    (stop-loss/signal-off/crisis/max-holding) exactly as before — this
    mirrors the existing "only ever gate entries, never exits" principle
    already followed by allowed_entry_regimes (analysis/regime.py, V1) and
    trading212_invest.pause_new_entries (brokers/trading212.py) — so
    neither maybe_exit_positions() nor any engine's own exit/Phase-A logic
    ever calls this function.

    Deliberately a strictly earlier, broader cut than the existing
    etf_submission_enabled / stock_submission_enabled flags
    (brokers/trading212.py's own submission-stage gate): those two only
    ever gate the final "place the order with the broker" step for a
    decision that has ALREADY been computed and queued into
    pending_orders. Setting an asset class's *_trading_enabled to False
    means it is never even considered a candidate in the first place.
    Setting *_trading_enabled True while leaving the matching
    *_submission_enabled False is a deliberate, coherent state, not a
    contradiction: decisions are still computed, momentum-ranked, and
    queued (visible in pending_orders for audit/research) but never reach
    the broker — this is the "shadow / dry-run" state ETF trading has
    been in since 2026-07-31, pending live verification of the
    exchange-timed submission mechanism (see CLAUDE.md's "Exchange-aware
    order submission" section). *_trading_enabled False is the stronger,
    upstream cut and takes priority — brokers/trading212.py's submission
    functions treat it as a master override that skips submission
    regardless of the matching *_submission_enabled value.
    """
    t212_cfg = config.get("trading212_invest", {})
    key = "etf_trading_enabled" if is_etf else "stock_trading_enabled"
    return bool(t212_cfg.get(key, True))


REENTRY_COOLDOWN_DAYS = 30
"""The FALLBACK default re-entry cooldown, used only when
trading212_invest.reentry_cooldown_days is absent from config entirely
(backwards compatible with a config predating this parameter) -- see
resolve_reentry_cooldown_days() immediately below for the real, live
value. Calendar days after a ticker's last order (PLACED/FILLED, either
direction) during which a new entry into that same ticker is blocked.

Found missing from the backtest engine entirely on 2026-08-03: live
order placement has enforced this since brokers/trading212.py's
_load_open_order_tickers() was first written, but no simulation engine
had an equivalent -- meaning every backtest run could freely re-enter
a ticker the instant it exited, a genuine extra degree of freedom live
has never had. See CLAUDE.md's "30-day re-entry cooldown -- live/
backtest parity gap closed" section for the investigation, the fix,
and the direct headline-figure sensitivity check that followed --
and "Re-entry cooldown set to 0 (config-driven)" for why production
now runs at 0, not this 30-day fallback.

Expressed as accepted, reviewed duplication (the same shape as
candidate-priority-ranking above): live's own check
(brokers/trading212.py:_load_open_order_tickers()) runs as a single
bulk SQL query over every ticker at once, since re-deriving ~500
tickers' own order history in Python per decide-run would be needless
overhead an efficient batch query already avoids; the backtest
engine's own check runs per-ticker, inline, inside its own day-by-day
walk, since it has no equivalent bulk-query mechanism to reuse. These
cannot literally share one function call for that reason -- but both
sides resolve the SAME cooldown value via resolve_reentry_cooldown_days()
below rather than each hardcoding their own number, and both are
independently tested to enforce the identical boundary against the
same synthetic scenario.

Only simulate_portfolio_compounding_mtm() (analysis/backtest.py) --
the engine analysis/validation.py:run_full_validation() actually
defaults to, the one confirmed to genuinely match what live order
placement does -- enforces this. simulate_trades()/simulate_portfolio()/
simulate_portfolio_compounding() are deliberately left unchanged: each
is an intentionally frozen engine, kept alongside the corrected one
specifically so an already-published historical figure that cites it
stays exactly reproducible on demand (the same "frozen engine, new one
added alongside it" pattern this file's own history follows repeatedly
-- see each function's own docstring for its specific frozen-figure
citation). Adding a new behavioural change to a frozen engine would
break that reproducibility guarantee, not fix a parity gap that
matters for anything live currently does.
"""


def is_reentry_blocked(
    days_since_last_order: Optional[float], cooldown_days: int = REENTRY_COOLDOWN_DAYS,
) -> bool:
    """True if a ticker's most recent order (of either direction) was
    fewer than cooldown_days CALENDAR days ago, and a new entry must not
    be placed into it yet. days_since_last_order=None (no order/trade
    history at all for this ticker) is never blocked -- a ticker that
    has literally never been traded has nothing to cool down from."""
    if days_since_last_order is None:
        return False
    return days_since_last_order < cooldown_days


def resolve_reentry_cooldown_days(config: dict) -> int:
    """The real, currently-configured re-entry cooldown -- reads
    trading212_invest.reentry_cooldown_days, falling back to
    REENTRY_COOLDOWN_DAYS (30) only when the key is absent entirely.

    Both live order placement (brokers/trading212.py's two
    _load_open_order_tickers() call sites) and the backtest engine
    (simulate_portfolio_compounding_mtm()) call this ONE function to
    resolve the value they then pass to is_reentry_blocked() -- so
    neither can silently resolve a different cooldown for the same
    config, closing the exact class of gap this parameter's own
    docstring above was written to document. Production runs at 0
    (2026-08-03) -- see CLAUDE.md's "Re-entry cooldown set to 0
    (config-driven)" section for why, and for the sweep across
    intermediate values it was set ahead of.
    """
    t212_cfg = config.get("trading212_invest", {}) or {}
    return int(t212_cfg.get("reentry_cooldown_days", REENTRY_COOLDOWN_DAYS))


MAX_HOLDING_DISABLED_SENTINEL = 0
"""`stock_max_holding_days: 0` (or `max_holding_days: 0` for ETFs) means the
per-ticker holding cap is DISABLED -- positions are then governed only by
CRISIS_EXIT/STOP_LOSS/SIGNAL_OFF and are never closed for age alone.

Zero, rather than a magic string or a separate boolean, because it is the one
value for which the cap has no coherent meaning: a 0-day cap would exit every
position on its own entry day. Reusing it as the disable switch keeps a single
integer config key rather than adding a second key that could contradict it.

Promoted 2026-08-12 from the exit-mechanics sweep, where unlimited holding was
the single largest contributor to the measured result. THE DEFAULT IS UNCHANGED
-- config.yaml still ships stock_max_holding_days: 30, and enabling this is a
separate, deliberate flip.
"""

DEFAULT_VAR_HORIZON_DAYS = 30
"""Fallback holding horizon for VaR position sizing when the holding cap is
disabled. See resolve_var_horizon_days() for why this is needed at all."""


def resolve_var_horizon_days(max_holding_days: Optional[int], config: dict) -> int:
    """Return the holding horizon (in days) VaR position sizing should scale by,
    given a possibly-disabled holding cap.

    WHY THIS EXISTS, and why it is NOT simply max_holding_days: the per-ticker
    holding cap has TWO consumers with genuinely different semantics, and they
    diverge the moment the cap is disabled.

      1. evaluate_exit_reason(max_holding_days=...) -- an EXIT GATE. None means
         "no age-based exit", which that function already handles through its
         standing None-tolerance contract. Nothing further is needed there.
      2. _var_position_size_core(..., holding_days=...) -- a SIZING HORIZON. It
         scales daily VaR by sqrt(holding_days) so the position is sized such
         that a holding-period adverse move loses at most max_risk_pct. None is
         not meaningful here: sqrt(None) is not a number, and "size for an
         infinite horizon" would drive every position to the minimum.

    So a disabled cap needs a real horizon for SIZING while having none for
    EXITS. This function supplies it, and is the single shared implementation
    both live order placement (brokers/trading212.py) and every backtest engine
    (analysis/backtest.py) call -- never re-derived inline, per this project's
    "exactly one implementation of any given trading decision" invariant.

    THE FALLBACK VALUE IS LOAD-BEARING FOR REPRODUCIBILITY, not arbitrary: the
    exit-mechanics sweep measured its unlimited-holding arms with a sizing
    horizon of exactly 30 days (the then-current stock_max_holding_days). A
    different fallback would produce different position sizes and would NOT
    reproduce that study's published figures. trading212_invest.var_horizon_days
    overrides it; the default reproduces the research exactly.
    """
    if max_holding_days is not None:
        return int(max_holding_days)
    t212_cfg = config.get("trading212_invest", {}) or {}
    return int(t212_cfg.get("var_horizon_days", DEFAULT_VAR_HORIZON_DAYS))


def resolve_dynamic_max_position_gbp(
    account_value_gbp: float, per_trade_pct: float, absolute_max_position_gbp: float,
) -> float:
    """The per-trade sizing ceiling fed into _var_position_size_core() as
    its own max_position_gbp cap — per_trade_pct of the account's current
    value, hard-bounded by absolute_max_position_gbp regardless of how
    large account_value_gbp becomes.

    Added 2026-08-01 to close a genuine, severe defect found the same day
    (see CLAUDE.md's "Mark-to-market unbounded compounding defect"
    section): `account_value_gbp * per_trade_pct` alone has no ceiling
    independent of account_value_gbp itself, and account_value_gbp is
    mark-to-market — it includes UNREALIZED appreciation on still-open
    positions. A single position that appreciates enormously while never
    triggering an exit condition inflates account_value_gbp, which
    inflates the sizing ceiling for every OTHER, unrelated new entry,
    which (if those also appreciate while open) inflates account_value_gbp
    further still — an unbounded feedback loop with no natural limit
    across a long enough backtest. Confirmed via a full 19-year production
    backtest run: position_gbp reached 10^60-10^76 before this fix,
    every historical validation block failing, CAGR in physically
    impossible ranges.

    This is deliberately NOT a reintroduction of the retired (2026-08-01,
    same day) max_position_gbp / max_open_positions config keys — those
    were rejected as the PRIMARY sizing driver because a flat ceiling
    saturates VaR's own volatility-differentiation for low-volatility
    names (see CLAUDE.md's "Compounding portfolio backtest" section).
    absolute_max_position_gbp is a rarely-binding SAFETY backstop, set
    high enough (config.yaml default: £1,000,000, 20x+ a realistic
    account size) that it is inert for any ordinary compounding
    trajectory — per_trade_pct still governs sizing in every normal case
    — while still decisively breaking the exponential shape of the
    specific defect this function exists to close.

    Called identically by live order placement
    (brokers/trading212.py:maybe_place_orders()) and both compounding
    backtest engines (analysis/backtest.py:simulate_portfolio_compounding()
    and simulate_portfolio_compounding_mtm()) — one shared function, not
    three independently-written copies of the same `min()` that could
    silently diverge again, exactly the failure mode this project has
    already found and fixed five separate times in one day (see this
    module's own docstring for the first three).
    """
    return min(account_value_gbp * per_trade_pct, absolute_max_position_gbp)


def is_position_size_viable(position_gbp: float, min_viable_order_gbp: float = 10.0) -> bool:
    """True if position_gbp is large enough to be worth actually placing,
    after every other sizing clamp (VaR's own min_position_gbp floor,
    resolve_dynamic_max_position_gbp()'s ceiling,
    resolve_cash_constrained_position_gbp()'s cash clamp) has already run.

    A genuinely distinct concept from min_position_gbp (the VaR formula's
    own internal floor, default £50, enforced BEFORE any cash clamp):
    min_position_gbp guarantees the *risk-based* sizing never asks for
    less than a sensible amount, but resolve_cash_constrained_position_gbp()
    can still shrink that down further, AFTER the VaR floor already ran,
    if the account's real available cash is thin — e.g. a £75 VaR-sized
    entry against only £20 of free cash clamps to £5. This is the final
    backstop that catches a resulting order too small to be worth
    submitting at all.

    Found missing from the mark-to-market backtest engine 2026-08-01,
    during the live/backtest consolidation audit that followed the same
    day's deployment-cap parity fix: live order placement
    (brokers/trading212.py:maybe_place_orders()) already had this exact
    check (a hardcoded `if position_gbp < 10: skip`, predating today), but
    when the cash clamp was ported into
    simulate_portfolio_compounding_mtm() as part of that fix, this
    corresponding backstop was not — meaning the backtest engine could
    silently open a degenerate, near-worthless position (e.g. a few
    pence) in a thin-cash scenario that live would correctly skip
    entirely. Exactly the class of divergence this project's structural
    parity work exists to catch; found by manual audit rather than
    Layer 1's mechanical scanner, since a MISSING check (nothing to flag
    as duplicated) is a different failure shape than a duplicated one —
    see CLAUDE.md's "Live and backtest consolidated into one decision
    engine" section for the fuller discussion of what Layer 1 can and
    cannot catch.
    """
    return position_gbp >= min_viable_order_gbp


def resolve_cash_constrained_position_gbp(position_gbp: float, available_cash_gbp: float, cash_fraction: float = 0.25) -> float:
    """A second, independent bound on a single entry's size: never more
    than cash_fraction of REAL, currently available (uninvested) cash —
    matching brokers/trading212.py's own pre-existing
    `min(position_gbp, free_cash * 0.25)` clamp, which live order
    placement already had before the mark-to-market defect was found.

    This matters specifically because available_cash_gbp — T212's
    `cash.availableToTrade`, or this codebase's own equivalent
    running `cash` tracking in the mark-to-market backtest engine — is
    NEVER inflated by unrealized appreciation on other open positions the
    way account_value_gbp (used by resolve_dynamic_max_position_gbp
    above) can be: cash only ever moves on a genuine realised transaction
    (a real deposit, or the real proceeds of a real sale). So even before
    the absolute_max_position_gbp fix above existed, live order placement
    already had a real, independent, structural defence against the worst
    of this exact defect that the backtest engine was simply missing —
    confirmed directly by reading brokers/trading212.py's own code (see
    CLAUDE.md's "Mark-to-market unbounded compounding defect" section for
    the full investigation, including why this made live's actual
    real-world exposure narrower than the backtest's clean numerical
    runaway suggested, without making it zero).

    Only applied where the caller has a genuine, independently-tracked
    cash figure distinct from account/portfolio value — live order
    placement and simulate_portfolio_compounding_mtm() both do;
    simulate_portfolio_compounding() (the original cost-basis engine) does
    not track cash as a separate concept from its own current_balance and
    is deliberately left unchanged/frozen, so this function is not called
    from there — see that engine's own reproducibility guarantee.
    """
    return min(position_gbp, available_cash_gbp * cash_fraction)


def is_submission_price_move_blocked(
    decided_price: float, current_price: float, threshold_pct: float,
) -> bool:
    """True if a queued BUY decision should be blocked at submission time
    because the ticker's price has moved too far from decided_price (the
    EOD close the decision was made against) by the time current_price
    (a genuinely live quote) is checked.

    ASYMMETRIC (2026-08-03) — blocks only on a DROP past threshold_pct,
    never on a rise of any size: `(current_price - decided_price) /
    decided_price < -threshold_pct`. A rise since the decision was made
    confirms the momentum thesis the decision was already based on rather
    than invalidating it; blocking on it contradicted this system's own
    "let winners run" principle and, per a 176-run block-based sweep (see
    CLAUDE.md's "Price-sanity threshold — momentum-skew investigation"
    section), was found to disproportionately filter out exactly the
    highest-momentum candidates this strategy is designed to chase
    hardest (a genuine, measured 1.91x skew under the old symmetric
    check). Was previously symmetric — `abs(pct_move) > threshold_pct` —
    before this fix. The threshold WIDTH is unchanged (still whatever
    trading212_invest.price_sanity_threshold_pct resolves to, 3% in
    production) — this is a single-variable direction change, not also a
    width change, so the two are never conflated in the same commit.

    decided_price<=0 (should not occur in practice — a queued decision
    always has a real recorded price) is treated as "no basis for
    comparison" and never blocks, rather than dividing by zero or
    guessing.
    """
    if decided_price <= 0:
        return False
    pct_move = (current_price - decided_price) / decided_price
    return pct_move < -threshold_pct


# Layer 4 (tests/test_one_sided_decision_rules.py) EXEMPT_LIVE_ONLY --
# is_submission_price_move_blocked() is genuinely live-only, verified, not
# assumed: the backtest engine (analysis/backtest.py) has no
# decide-then-submit time gap at all in its current architecture -- every
# engine enters a position at the SAME moment the decision is made (the
# signal day's own close is both decided_price and entry_price in one
# step), so there is no later "has price moved since the decision"
# submission-time state to check against. This is the identical
# structural reason brokers/trading212.py:is_crisis_mode_active_now()
# (the submission-time crisis re-check) has never needed a backtest
# counterpart either -- see CLAUDE.md's "Submission-time crisis re-check"
# section for that established precedent. Modeling a genuine decide-to-
# submit gap in the backtest (the technique the momentum-skew sweep above
# used, on a disposable research engine, to test this exact question) was
# deliberately out of scope for this change -- see CLAUDE.md's own write-up
# for why folding it into the frozen, validated production backtest engine
# would be a materially larger change than the single-variable direction
# flip this task actually scoped.


def evaluate_rank_dropped_exit(*, is_in_current_top_n: Optional[bool]) -> Optional[str]:
    """RANK_DROPPED — the exit reason for cross-sectional (relative-rank)
    construction, checked only on a rebalance date: a currently-held
    position exits when it is no longer within that date's freshly
    re-computed top-N ranking.

    This has no analog in the current live system or in any of
    analysis/backtest.py's engines — none of them rank candidates against
    each other or rebalance on a fixed calendar grid, so there was nothing
    existing for this rule to converge with or diverge from. It is placed
    here anyway, ahead of any live caller, rather than living inline in a
    research engine: see CLAUDE.md's cross-sectional-momentum
    pre-registration (2026-08-09 amendment) for the reasoning — if
    cross-sectional construction is ever adopted for real, the live path
    calls this exact function, not a second, independently-written copy of
    the same rule invented after the fact. This mirrors the standing "one
    implementation of any given trading decision" invariant this module's
    own docstring describes, applied proactively rather than retroactively.

    `is_in_current_top_n` follows this module's own None-tolerance
    convention: True/False must be an explicit, confirmed ranking result
    for today; None ("not a rebalance date, or ranking not computed today")
    never triggers an exit — matching how every other function here treats
    unknown/not-yet-computed state as "this check contributes nothing,"
    never as an affirmative trigger.

    Priority relative to the shared evaluate_exit_reason() above: callers
    are expected to check evaluate_exit_reason() (CRISIS_EXIT / STOP_LOSS /
    MAX_HOLDING) FIRST, every day, and only call this function on rebalance
    dates for positions that check left un-exited — CRISIS_EXIT/STOP_LOSS/
    MAX_HOLDING take priority over RANK_DROPPED, not the reverse, since a
    position that would already be stopped out or crisis-exited mid-month
    should never survive to be evaluated for a rank-based exit at all.

    Layer 4 (tests/test_one_sided_decision_rules.py) note: this function is
    called by neither brokers/trading212.py nor analysis/backtest.py today
    (its one real caller is the cross-sectional research engine in
    research/cross_sectional_momentum_prereg_2026-08-09/). Verified
    directly against that test's own set-difference logic before relying on
    this: the check only flags a function called by EXACTLY ONE of the two
    production files with no recorded exemption — a function called by
    NEITHER is a different, unflagged state (research-only, pending a live
    path that does not exist yet), so no EXEMPT_LIVE_ONLY/EXEMPT_BACKTEST_ONLY
    entry is needed or added for this function while that remains true.
    """
    if is_in_current_top_n is None:
        return None
    if is_in_current_top_n is False:
        return "RANK_DROPPED"
    return None


ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM = "absolute_momentum"
ADMISSION_RANKING_MODE_RELATIVE_RANK = "relative_rank"


def resolve_admission_ranking_mode(config: dict) -> str:
    """The real, currently-configured admission-ranking mode -- reads
    trading212_invest.admission_ranking_mode, falling back to
    ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM (today's actual production
    behaviour) only when the key is absent entirely. Same config-driven-
    parameter pattern as resolve_reentry_cooldown_days()/resolve_min_
    momentum_floor() above: both live order placement
    (brokers/trading212.py:maybe_place_orders()) and the backtest engine
    (simulate_portfolio_compounding_mtm()) call this ONE function, so
    neither can silently resolve a different mode for the same config.

    Promoted 2026-08-09 from the relative-ranking study's own research
    engine (research/relative_ranking_prereg_2026-08-09/) after it cleared
    its pre-registered bar and a 20-draw paired robustness test (see
    CLAUDE.md's "Relative-ranking (cross-sectional rank, daily admission)
    study" and the robustness-test addendum for the full record, including
    the AMAT-dependency caveat on the point estimate). Defaults to
    ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM deliberately — promoting the
    CODE is separable from adopting the BEHAVIOUR; flipping this value in
    config.yaml is a distinct, deliberate decision made after review, not a
    side effect of this function existing.
    """
    t212_cfg = config.get("trading212_invest", {}) or {}
    return str(t212_cfg.get("admission_ranking_mode", ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM))


def resolve_admission_rank_value(
    *, momentum: Optional[float], relative_rank: Optional[float], mode: str,
) -> Optional[float]:
    """The single value gate-passing candidates are sorted by (descending)
    for capacity-constrained admission — the ONLY thing
    admission_ranking_mode changes. Called identically by live order
    placement (brokers/trading212.py:maybe_place_orders(), which stores the
    result as pending_orders.admission_rank_value and sorts
    `ORDER BY admission_rank_value DESC` at submission time) and the
    backtest engine (simulate_portfolio_compounding_mtm()'s _admit_group()),
    so neither can silently rank candidates by a different value for the
    same config.

    Deliberately narrow in scope, matching the relative-ranking study's own
    "narrowed on review" Arm B design (see CLAUDE.md): this function is the
    ENTIRE promoted behaviour change. The entry gate (passes_entry_gate()),
    the 2% momentum floor (passes_momentum_floor()), and every exit reason
    (evaluate_exit_reason()'s sig_on input, including SIGNAL_OFF) all
    continue to read the real, unchanged absolute momentum value regardless
    of this function's return — never relative_rank. The original, broader
    design (relative rank driving the gate and SIGNAL_OFF too) was reviewed
    and rejected before the research study even ran, specifically because
    it would have made B and C two flavours of rank-based exit rather than
    a clean attribution arm plus one added variable — see the relative-
    ranking study's own PREREGISTRATION.md §1 for that correction.

    momentum is returned unchanged for the (default, current production)
    ADMISSION_RANKING_MODE_ABSOLUTE_MOMENTUM mode, so an unconfigured
    system behaves byte-identically to before this function existed —
    same "config predating this parameter behaves exactly as before"
    guarantee every other config-driven parameter in this module provides.
    An unrecognised mode string also falls back to momentum (fails safe to
    today's validated behaviour, never to an unvalidated one).
    """
    if mode == ADMISSION_RANKING_MODE_RELATIVE_RANK:
        return relative_rank
    return momentum


def resolve_share_class_exclusion_enabled(config: dict) -> bool:
    """Whether admission excludes a candidate whose share-class sibling is
    already held -- reads trading212_invest.share_class_exclusion, defaulting
    to False, which is today's actual production behaviour (no exclusion).

    Same config-driven-parameter pattern as resolve_admission_ranking_mode()
    and the stock_max_holding_days sentinel: shipping the CODE is separable
    from adopting the BEHAVIOUR. A config written before this key existed
    behaves byte-identically to before, and flipping it is a distinct,
    deliberate decision.

    ADOPTED ON RISK GROUNDS, NOT RETURN EVIDENCE. The affected population is
    a handful of dual-class pairs across 19 years, far below any materiality
    floor this project uses, so this is NOT backtest-testable in any
    meaningful way and no study is claimed for it -- see CLAUDE.md's
    "Share-class exclusion at admission" entry.
    """
    t212_cfg = config.get("trading212_invest", {}) or {}
    return bool(t212_cfg.get("share_class_exclusion", False))


def blocking_share_class_siblings(
    *,
    siblings,
    held_tickers,
    enabled: bool,
) -> tuple:
    """The already-held share-class siblings of a candidate, sorted -- empty
    when nothing blocks it, so it reads directly as a predicate at both call
    sites (`if blocking_share_class_siblings(...): continue`).

    THE single shared implementation, called identically by live order
    placement (brokers/trading212.py:_submit_pending_orders()) and the
    backtest engine (analysis/backtest.py:simulate_portfolio_compounding_mtm()
    's _admit_group()), per CLAUDE.md's "exactly one implementation of any
    given trading decision" invariant.

    Returns the blocking siblings rather than a bare bool deliberately: live
    records WHICH sibling blocked an admission in the pending_orders note, and
    a separate bool wrapper would either have made that caller re-derive the
    membership test inline (caught during development by
    tests/test_decision_logic_placement.py's scanner) or added a second
    decision function only one side calls (caught by
    tests/test_one_sided_decision_rules.py). One function avoids both.

    A CANDIDACY FILTER ONLY. Nothing here changes sizing, ranking, the
    momentum gate, or any exit: a position already open is never force-exited
    because a sibling exists, and this is never consulted on the exit path.
    Its only effect is skipping one candidate at admission, exactly as the
    re-entry cooldown and the momentum floor already do.

    WHY: the S&P 500 lists dual-class companies as separate constituents, so
    the universe treats GOOGL and GOOG as independent candidates. Their
    momentum is near-identical by construction, so they rank adjacently and
    are admitted together whenever either qualifies -- leaving the account
    holding roughly double the intended exposure to one company while the
    sizing model believes it holds two independent positions. Confirmed live
    (GOOG + GOOGL, both entered 2026-08-12, ~GBP 2,531 each), not
    hypothetical. ADOPTED ON RISK GROUNDS, NOT RETURN EVIDENCE -- see
    CLAUDE.md's "Share-class exclusion at admission" entry.

    "HELD" DELIBERATELY INCLUDES POSITIONS OPENED EARLIER IN THE SAME
    ADMISSION WALK, and this is load-bearing rather than a detail: the live
    instance that motivated this had both classes admitted on the SAME day, so
    neither was held when the other was evaluated. A check against only the
    previous day's holdings would not have prevented it. The backtest engine
    gets this for free (a position opened earlier in _admit_group()'s ranked
    loop is already in per_ticker[t]["open_trade"]); live's submission walk
    adds each ticker to its own held set as it is admitted.

    Argument contract, following this module's standing None-tolerance rule:
    every input tolerates None as "unknown, don't trigger this check" rather
    than relying on falsy coercion. `siblings` is whatever
    analysis/universe.py:get_share_class_siblings() returned for the
    candidate (empty for the overwhelming majority of tickers -- the fast
    path). `held_tickers` is the set of research tickers currently held.
    `enabled` is resolve_share_class_exclusion_enabled(config).
    """
    if not enabled:
        return ()
    if not siblings or not held_tickers:
        return ()
    return tuple(sorted(s for s in siblings if s in held_tickers))
