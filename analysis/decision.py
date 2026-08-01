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
    is_member: Optional[bool],
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

        1. CRISIS_EXIT   — today is a raw (>=7%) S&P 500 crisis day
        2. INDEX_REMOVAL — no longer a genuine index member (never true
                            for an ETF — is_member already folds that in,
                            see analysis/universe.py:is_member()'s ETF
                            short-circuit)
        3. STOP_LOSS     — price has reached the ATR stop fixed at entry
        4. SIGNAL_OFF    — trailing 12-month momentum has turned non-positive
        5. MAX_HOLDING   — the per-ticker holding-days backstop is reached

    `is_member`/`sig_on`/`crisis_now` of False specifically (not merely
    falsy) is what triggers INDEX_REMOVAL/SIGNAL_OFF/(crisis is checked as
    `is True`) — None is treated as "unknown, don't trigger this check",
    never as an affirmative trigger.
    """
    if crisis_now is True:
        return "CRISIS_EXIT"
    if is_member is False:
        return "INDEX_REMOVAL"
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
