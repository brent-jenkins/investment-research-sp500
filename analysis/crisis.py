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


def is_intraday_crisis(gspc_close: pd.Series, live_price: float) -> bool:
    """True if a genuinely live S&P 500 price already represents a
    single-day decline of PRICE_GAP_THRESHOLD or worse from the most
    recent STORED daily close — i.e. a crisis that compute_raw_trigger()
    would confirm once today's own close is finally recorded, detected
    NOW, hours before that close exists (2026-08-03).

    Why this exists, separately from is_entry_blocked() above:
    compute_raw_trigger()/is_entry_blocked() are daily-bar-only by
    design — they can only ever see a date once its close is stored in
    price_data, which happens once a day at the 21:30 UTC EOD pull.
    Entries decided at that same EOD run are queued into pending_orders
    and submitted up to ~17 hours later (07:00/14:30 UTC the *next*
    calendar day) — hours before that day's own close is known. A crisis
    developing overnight or during that morning would not be visible to
    is_entry_blocked() at submission time at all: the stored gspc_close
    series submission-time would read would be identical to what decide
    time already saw the night before, since no new daily bar has been
    written in between. See CLAUDE.md's "Submission-time crisis
    re-check" section for the full investigation this closes — including
    why merely re-calling is_entry_blocked() with the same stored data
    would NOT have been a real fix, only one that looked like one.

    Not a replacement for is_entry_blocked() — the two are complementary
    and both should be checked before submitting a new entry: this
    catches a crisis that has NOT yet been recorded as a daily bar;
    is_entry_blocked() catches one that has (including the multi-day
    cooldown window after a trigger day, which this function has no
    concept of on its own).
    """
    if gspc_close.empty or live_price is None or live_price <= 0:
        return False
    last_close = float(gspc_close.iloc[-1])
    if last_close <= 0:
        return False
    intraday_return = (live_price - last_close) / last_close
    return intraday_return <= PRICE_GAP_THRESHOLD
