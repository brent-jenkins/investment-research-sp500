"""Long/short primitives: the borrow-cost model (Sec 5), the short-side exit
evaluator (Sec 4), and the rally breaker (Sec 6).

RESEARCH ONLY. Deliberately NOT added to analysis/decision.py -- a live-path
decision module must not carry a branch no live caller uses. See
PREREGISTRATION.md Sec 16.

The short exit evaluator is an EXACT REFLECTION of the production
analysis/decision.py:evaluate_exit_reason(), not a re-implementation. The claim
is verified mechanically in step4_reflection_test.py over >=100,000 randomized
tuples plus boundary cases, using the algebraic identity

    P >= E+d   <=>   2E-P <= E-d

so a short stop at E+d on price P is identically a long stop at E-d on the
reflected price 2E-P. That test is what makes this module trustworthy; this
docstring is not.
"""
from typing import Optional

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Sec 5 -- borrow cost model. MODELLED ASSUMPTION, not measured data.
# --------------------------------------------------------------------------

BASE_BORROW_RATE_ANNUAL = 0.0050
"""General-collateral borrow, applied to every short every day. Deliberately
NOT eToro's headline 0% easy-to-borrow figure: a retail broker quoting zero is
recovering the cost in spread/financing, and modelling it as literally zero
would import a marketing structure as an economic fact. 0.50%/yr is a
mid-range honest GC figure for liquid US large caps."""

HTB_BORROW_RATE_ANNUAL = 0.1050
"""Hard-to-borrow rate, REPLACING (not added to) the GC rate when flagged.
Adopted from eToro's published >10% hard-to-borrow schedule."""

HTB_PRICE_THRESHOLD = 5.00
HTB_DRAWDOWN_THRESHOLD = -0.70
HTB_VOL_THRESHOLD = 0.80
BORROW_HORIZON_DAYS = 30
"""= stock_max_holding_days. The KNOWN holding-period cap, used as the horizon
for the entry-decision borrow charge -- available at decision time, requires no
forecast, no look-ahead."""


def is_hard_to_borrow(close, dd252, vol60) -> bool:
    """Sec 5.2. HTB iff ANY of: sub-$5 price, >=70% drawdown from the trailing
    252-day high, or >=80% annualised 60-day realised vol. All trailing, all
    available at decision time. NaN in an input means that leg cannot fire --
    never treated as an affirmative flag."""
    if close is not None and not pd.isna(close) and close < HTB_PRICE_THRESHOLD:
        return True
    if dd252 is not None and not pd.isna(dd252) and dd252 <= HTB_DRAWDOWN_THRESHOLD:
        return True
    if vol60 is not None and not pd.isna(vol60) and vol60 >= HTB_VOL_THRESHOLD:
        return True
    return False


def borrow_rate_annual(close, dd252, vol60, stress_multiplier: float = 1.0) -> float:
    """Annual borrow rate for a short in this ticker on this date."""
    base = HTB_BORROW_RATE_ANNUAL if is_hard_to_borrow(close, dd252, vol60) else BASE_BORROW_RATE_ANNUAL
    return base * stress_multiplier


def required_short_floor(min_momentum_pct: float, rate_annual: float) -> float:
    """Sec 5.3 -- borrow cost enters the ENTRY DECISION, not just as a
    post-hoc drag. The short entry floor is raised by the borrow cost expected
    over the known holding-period cap, so an expensive borrow demands a
    demonstrably stronger signal.

    Returns a POSITIVE magnitude; a candidate qualifies when
    momentum <= -required_short_floor(...).
    """
    return min_momentum_pct + rate_annual * BORROW_HORIZON_DAYS / 365.0


def accrue_borrow_gbp(notional_mtm_gbp: float, rate_annual: float, calendar_days: float) -> float:
    """Daily borrow accrual on the short's CURRENT mark-to-market notional --
    what a real borrow fee accrues on (the market value of the borrowed stock,
    marked daily), not the entry notional. Calendar-day basis, so weekends and
    holidays accrue, as real borrow does."""
    if notional_mtm_gbp <= 0 or calendar_days <= 0:
        return 0.0
    return notional_mtm_gbp * rate_annual * calendar_days / 365.0


# --------------------------------------------------------------------------
# Sec 4 -- short exit evaluator, an exact reflection of the production
# long-side evaluate_exit_reason().
# --------------------------------------------------------------------------

def evaluate_short_exit_reason(
    *,
    crisis_now: Optional[bool],
    price: Optional[float],
    stop_price: Optional[float],
    sig_on: Optional[bool],
    holding_days: Optional[int],
    max_holding_days: Optional[int],
) -> Optional[str]:
    """Exact mirror of analysis.decision.evaluate_exit_reason() for a SHORT.

    The ONLY difference from the production function is the stop comparison
    direction: a short's stop sits ABOVE entry and triggers on
    `price >= stop_price`, where the long's sits below and triggers on
    `price <= stop_price`. Priority order, None-tolerance semantics, and
    boundary handling are identical and are verified as identical, not
    asserted.

    `sig_on` for a short means "the short signal is still on", i.e. momentum
    is still negative. The caller supplies it; this function only passes it
    through, exactly as the long evaluator does.

    NOTE ON crisis_now: this parameter exists so the function is a TOTAL
    mirror of the production evaluator (which is what makes the reflection
    test meaningful). The ENGINE deliberately passes crisis_now=False for
    every short, because per PREREGISTRATION.md Sec 3 the crisis breaker
    blocks new short ENTRIES but does not force-exit existing shorts -- a
    crash is when the short book performs best, so exiting shorts there would
    crystallise the wrong side. The RALLY breaker (Sec 6), not the crisis
    breaker, is what force-exits shorts. step6_verify_engine.py asserts
    directly that zero short trades in any arm exit via CRISIS_EXIT.
    """
    if crisis_now is True:
        return "CRISIS_EXIT"
    if stop_price is not None and price is not None and price >= stop_price:
        return "STOP_LOSS"
    if sig_on is False:
        return "SIGNAL_OFF"
    if holding_days is not None and max_holding_days is not None and holding_days >= max_holding_days:
        return "MAX_HOLDING"
    return None


# --------------------------------------------------------------------------
# Sec 6 -- the rally breaker (momentum-crash circuit breaker for shorts).
# --------------------------------------------------------------------------

RALLY_DD_GATE = -0.20
RALLY_R5_TRIGGER = 0.10
RALLY_COOLDOWN_DAYS = 10
RALLY_DD_WINDOW = 252
RALLY_R5_WINDOW = 5

PRIMARY_CONFIG = {"dd_gate": RALLY_DD_GATE, "r5": RALLY_R5_TRIGGER, "cooldown": RALLY_COOLDOWN_DAYS}
SENSITIVITY_CONFIGS = {
    "PRIMARY": {"dd_gate": -0.20, "r5": 0.10, "cooldown": 10},
    "loose": {"dd_gate": -0.15, "r5": 0.08, "cooldown": 10},
    "tight": {"dd_gate": -0.25, "r5": 0.12, "cooldown": 10},
    "cd-short": {"dd_gate": -0.20, "r5": 0.10, "cooldown": 5},
    "cd-long": {"dd_gate": -0.20, "r5": 0.10, "cooldown": 20},
}


def compute_rally_raw_trigger(
    gspc_close: pd.Series, dd_gate: float = RALLY_DD_GATE, r5_trigger: float = RALLY_R5_TRIGGER,
) -> pd.Series:
    """True on any day a strong multi-day rally occurs WHILE the index was
    already materially below its trailing peak going into that rally.

        peak252(t) = max(close) over trailing 252 trading days, inclusive
        dd(t)      = close(t)/peak252(t) - 1
        r5(t)      = close(t)/close(t-5) - 1
        RAW(t)     = dd(t-1) <= dd_gate  AND  r5(t) >= r5_trigger

    dd IS MEASURED AT t-1, NOT t, AND THAT IS LOAD-BEARING (Sec 6.4). A +10%
    five-day rally can itself lift the index out of a -20% drawdown by day t,
    so measuring dd(t) would let the very rally this breaker exists to catch
    mechanically suppress its own trigger -- reporting "no fire" for a reason
    entirely unrelated to whether a momentum crash occurred. Measured at t-1,
    the drawdown state is the one that existed going INTO the rally.

    The drawdown gate is what prevents the named failure mode of a naive
    "exit shorts on any strong up day", which would fire constantly in
    ordinary bull markets. The 5-day (rather than single-day) rally window is
    what prevents firing on the isolated mid-decline bounces that punctuate a
    bear market (2008-10-13 at +11.6%, 2008-10-28 at +10.8% -- neither a turn).
    """
    peak252 = gspc_close.rolling(RALLY_DD_WINDOW, min_periods=RALLY_DD_WINDOW).max()
    dd = gspc_close / peak252 - 1.0
    r5 = gspc_close / gspc_close.shift(RALLY_R5_WINDOW) - 1.0
    dd_prev = dd.shift(1)
    raw = (dd_prev <= dd_gate) & (r5 >= r5_trigger)
    return raw.fillna(False)


def extend_rally_trigger(raw_trigger: pd.Series, cooldown_days: int = RALLY_COOLDOWN_DAYS) -> pd.Series:
    """Short-entry-blocking window: True on the trigger day itself and for
    *cooldown_days* trading days after the most recent trigger day. Same
    mechanism as analysis.crisis.extend_trigger(), different cooldown (10 vs
    5) because momentum-crash rebounds run for weeks to months, not days."""
    counter = np.inf
    vals = raw_trigger.values
    out = np.empty(len(vals), dtype=bool)
    for i, v in enumerate(vals):
        counter = 0 if v else counter + 1
        out[i] = counter <= cooldown_days
    return pd.Series(out, index=raw_trigger.index)
