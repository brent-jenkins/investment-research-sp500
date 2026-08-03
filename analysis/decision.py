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
"""Calendar days after a ticker's last order (PLACED/FILLED, either
direction) during which a new entry into that same ticker is blocked.

Found missing from the backtest engine entirely on 2026-08-03: live
order placement has enforced this since brokers/trading212.py's
_load_open_order_tickers() was first written, but no simulation engine
had an equivalent -- meaning every backtest run could freely re-enter
a ticker the instant it exited, a genuine extra degree of freedom live
has never had. See CLAUDE.md's "30-day re-entry cooldown -- live/
backtest parity gap closed" section for the investigation, the fix,
and the direct headline-figure sensitivity check that followed.

Expressed as accepted, reviewed duplication (the same shape as
candidate-priority-ranking above): live's own check
(brokers/trading212.py:_load_open_order_tickers()) runs as a single
bulk SQL query over every ticker at once, since re-deriving ~500
tickers' own order history in Python per decide-run would be needless
overhead an efficient batch query already avoids; the backtest
engine's own check runs per-ticker, inline, inside its own day-by-day
walk, since it has no equivalent bulk-query mechanism to reuse. These
cannot literally share one function call for that reason -- but both
sides read this ONE constant rather than each hardcoding their own
"30", and both are independently tested to enforce the identical
30-calendar-day boundary against the same synthetic scenario.

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
