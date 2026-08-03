"""Tests for analysis/crisis.py -- pure functions, no DB needed."""
import pandas as pd

from analysis import crisis as v2c


def _gspc_series():
    dates = pd.date_range("2020-01-01", periods=10, freq="D")
    # Day index 4 (2020-01-05) drops >7% from the prior close.
    closes = [100, 101, 102, 103, 95, 94, 96, 98, 99, 100]
    return pd.Series(closes, index=dates, dtype=float)


def test_raw_trigger_fires_only_on_the_actual_shock_day():
    gspc = _gspc_series()
    trigger = v2c.compute_raw_trigger(gspc)
    assert trigger.sum() == 1
    assert trigger.loc[pd.Timestamp("2020-01-05")]
    assert not trigger.loc[pd.Timestamp("2020-01-04")]


def test_raw_trigger_does_not_fire_below_threshold():
    dates = pd.date_range("2020-01-01", periods=5, freq="D")
    closes = [100, 99, 98, 97, 96]  # -1%/day, never >=7% in one day
    gspc = pd.Series(closes, index=dates, dtype=float)
    trigger = v2c.compute_raw_trigger(gspc)
    assert not trigger.any()


def test_extend_trigger_covers_cooldown_window():
    gspc = _gspc_series()
    raw = v2c.compute_raw_trigger(gspc)
    extended = v2c.extend_trigger(raw, cooldown_days=5)
    shock_day = pd.Timestamp("2020-01-05")
    assert extended.loc[shock_day]
    for i in range(1, 6):
        day = shock_day + pd.Timedelta(days=i)
        if day in extended.index:
            assert extended.loc[day]
    day_after_cooldown = shock_day + pd.Timedelta(days=6)
    if day_after_cooldown in extended.index:
        assert not extended.loc[day_after_cooldown]


def test_extend_trigger_clears_before_next_shock():
    gspc = _gspc_series()
    raw = v2c.compute_raw_trigger(gspc)
    extended = v2c.extend_trigger(raw, cooldown_days=1)
    # Day before the shock must not be blocked.
    assert not extended.loc[pd.Timestamp("2020-01-04")]


def test_is_crisis_day_true_only_on_raw_trigger_date():
    gspc = _gspc_series()
    assert v2c.is_crisis_day(gspc, pd.Timestamp("2020-01-05"))
    assert not v2c.is_crisis_day(gspc, pd.Timestamp("2020-01-06"))


def test_is_crisis_day_false_for_date_not_in_series():
    gspc = _gspc_series()
    assert not v2c.is_crisis_day(gspc, pd.Timestamp("1999-01-01"))


def test_is_entry_blocked_true_during_cooldown_false_after():
    gspc = _gspc_series()
    assert v2c.is_entry_blocked(gspc, pd.Timestamp("2020-01-05"))
    assert v2c.is_entry_blocked(gspc, pd.Timestamp("2020-01-08"))


# ---------------------------------------------------------------------------
# is_intraday_crisis() (2026-08-03) -- the submission-time companion to
# is_entry_blocked(): detects a >=7% decline from the last STORED close via
# a genuinely live quote, hours before that day's own close would ever be
# recorded and visible to the daily-bar-only functions above.
# ---------------------------------------------------------------------------

def test_is_intraday_crisis_true_when_live_price_already_down_seven_percent():
    gspc = pd.Series([100.0], index=[pd.Timestamp("2020-01-01")])
    assert v2c.is_intraday_crisis(gspc, live_price=93.0)  # exactly -7.0%


def test_is_intraday_crisis_false_for_a_smaller_decline():
    gspc = pd.Series([100.0], index=[pd.Timestamp("2020-01-01")])
    assert not v2c.is_intraday_crisis(gspc, live_price=95.0)  # -5%, below threshold


def test_is_intraday_crisis_false_when_price_is_flat_or_up():
    gspc = pd.Series([100.0], index=[pd.Timestamp("2020-01-01")])
    assert not v2c.is_intraday_crisis(gspc, live_price=100.0)
    assert not v2c.is_intraday_crisis(gspc, live_price=105.0)


def test_is_intraday_crisis_uses_the_most_recent_stored_close_not_an_earlier_one():
    gspc = pd.Series(
        [100.0, 50.0],  # last stored close is 50.0, not 100.0
        index=[pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02")],
    )
    # A live price of 93 is a real >=7% drop from 100 but NOT from 50 --
    # must compare against the series' last value, not an earlier one.
    assert not v2c.is_intraday_crisis(gspc, live_price=93.0)
    assert v2c.is_intraday_crisis(gspc, live_price=46.0)  # -8% from 50.0


def test_is_intraday_crisis_false_on_empty_series():
    assert not v2c.is_intraday_crisis(pd.Series([], dtype=float), live_price=50.0)


def test_is_intraday_crisis_false_on_missing_or_non_positive_live_price():
    gspc = pd.Series([100.0], index=[pd.Timestamp("2020-01-01")])
    assert not v2c.is_intraday_crisis(gspc, live_price=None)
    assert not v2c.is_intraday_crisis(gspc, live_price=0.0)
    assert not v2c.is_intraday_crisis(gspc, live_price=-5.0)
