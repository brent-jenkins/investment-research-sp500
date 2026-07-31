"""Crisis override — a rare-firing, externally-grounded circuit breaker,
independent of the per-ticker momentum signal and capable of overriding it.
S&P 500 single-day decline >= 7% matches the real NYSE Level 1 market-wide
circuit breaker level — chosen over a VIX-spike variant tested in the same
research phase (VIX >= 45): the price-gap trigger fired on only 7 days
across the full 2007-2026 backtest window and cost zero measured edge in
any block it didn't touch, while the VIX variant fired ~15x more often and
caused a real regression in one block for one signal variant. See
CLAUDE.md's Phase 3 findings for the full comparison.

On trigger: every open position is force-exited that day (CRISIS_EXIT),
and no new entries are permitted for the trigger's duration plus a 5
trading-day cooldown after it clears.
"""
import numpy as np
import pandas as pd

PRICE_GAP_THRESHOLD = -0.07
ENTRY_BLOCK_COOLDOWN_DAYS = 5


def compute_raw_trigger(gspc_close: pd.Series) -> pd.Series:
    """True on any day the S&P 500 fell >= 7% from the prior close."""
    daily_return = gspc_close.pct_change()
    return (daily_return <= PRICE_GAP_THRESHOLD).fillna(False)


def extend_trigger(raw_trigger: pd.Series, cooldown_days: int = ENTRY_BLOCK_COOLDOWN_DAYS) -> pd.Series:
    """Entry-blocking window: True on the trigger day itself and for
    *cooldown_days* trading days after the most recent trigger day."""
    counter = np.inf
    vals = raw_trigger.values
    out = np.empty(len(vals), dtype=bool)
    for i, v in enumerate(vals):
        counter = 0 if v else counter + 1
        out[i] = counter <= cooldown_days
    return pd.Series(out, index=raw_trigger.index)


def is_crisis_day(gspc_close: pd.Series, check_date) -> bool:
    """True if *check_date* itself is a raw (>=7% single-day decline) crisis
    day — used to decide whether to force-exit an open position today."""
    trigger = compute_raw_trigger(gspc_close)
    if check_date not in trigger.index:
        return False
    return bool(trigger.loc[check_date])


def is_entry_blocked(gspc_close: pd.Series, check_date) -> bool:
    """True if *check_date* falls within the trigger's duration or the
    post-trigger cooldown window — used to gate new entries."""
    raw = compute_raw_trigger(gspc_close)
    extended = extend_trigger(raw)
    if check_date not in extended.index:
        return False
    return bool(extended.loc[check_date])
