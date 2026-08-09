"""Momentum signal — trailing 12-month PRICE return (not total return), per
ticker, no blended score and no separate regime classifier. Long when
positive, flat otherwise. Validated across the phased rebuild (Phase 1) as
the stronger of two externally-grounded candidates tested (12-month
momentum vs. 200-day SMA); adopted as the system's sole entry/exit signal.

Ingestion fetches with auto_adjust=False (see data/ingestion.py), so the
`close` this module reads is split-adjusted but NOT dividend-adjusted --
the signal excludes dividends entirely. A dividend-paying stock's true
total return is therefore understated relative to a non-payer with the
same price path. `adj_close` (dividend+split adjusted) is stored by
ingestion but read by nothing in this module. See CLAUDE.md's momentum
construction investigation for whether this materially biases sector/stock
selection.
"""
import pandas as pd

MOMENTUM_LOOKBACK_DAYS = 252

# compute_momentum_series() shifts by ROW COUNT, not calendar time -- correct
# and necessary for an ordinary gapless trading-day series (weekends/holidays
# never have rows at all, so a 252-row shift lands ~365 calendar days back,
# which is exactly "12 months"). But a GENUINE gap in the trailing window --
# a ticker halted for an extended stretch, a past data-ingestion outage that
# left a hole mid-series, or (see analysis/universe.py's symbol-recycling
# note) an unrelated company's history silently following a delisted one's
# under a reused symbol -- means fewer than MOMENTUM_LOOKBACK_DAYS genuine
# trading days actually occurred between the anchor row and today. shift()
# has no way to know this: it silently reaches back to a stale row far
# outside any reasonable "12 months ago" window and returns a REAL, non-NaN
# number computed against that wrong anchor -- not the intended "insufficient
# contiguous history, no signal yet" NaN. Verified directly (2026-08-01): a
# synthetic ~16-month gap mid-series produced a real +170% momentum reading,
# silently treated as a valid signal, right at the seam.
#
# _MAX_ANCHOR_GAP_DAYS guards against this: a genuine, gapless
# MOMENTUM_LOOKBACK_DAYS-trading-day window spans roughly 365 calendar days
# (252 trading days/year). 400 gives generous slack for a heavy-holiday
# year or a handful of individually-missing days without over-tolerating a
# real multi-month gap.
_MAX_ANCHOR_GAP_DAYS = 400


def compute_momentum_series(close: pd.Series) -> pd.Series:
    """Trailing 12-month (252 trading day) PRICE return, per date -- computed
    from `close` (split-adjusted, not dividend-adjusted), so this excludes
    dividends. Not total return.

    NaN for the first MOMENTUM_LOOKBACK_DAYS rows (insufficient history) —
    callers must treat NaN as "no signal yet", not as False. Also NaN for
    any row whose 252-rows-back anchor is calendar-stale (more than
    _MAX_ANCHOR_GAP_DAYS away) — see _MAX_ANCHOR_GAP_DAYS's own docstring
    for why a pure row-count shift can silently misfire across a real gap
    in the trailing window.
    """
    momentum = close / close.shift(MOMENTUM_LOOKBACK_DAYS) - 1.0

    if close.empty or not isinstance(close.index, pd.DatetimeIndex):
        # Nothing to gap-check against (empty series, or a caller passing a
        # non-date-indexed series e.g. in an isolated unit test) -- the
        # row-count shift above is the only signal available either way.
        return momentum

    anchor_dates = close.index.to_series().shift(MOMENTUM_LOOKBACK_DAYS)
    gap_days = (close.index.to_series() - anchor_dates).dt.days
    stale = gap_days > _MAX_ANCHOR_GAP_DAYS
    momentum[stale.fillna(False).to_numpy()] = float("nan")
    return momentum


def compute_signal_series(close: pd.Series) -> pd.Series:
    """Boolean long/flat signal: True when trailing 12-month return > 0.

    NaN momentum rows resolve to False (flat) — matches the backtest
    convention used throughout the phased validation (no position taken
    until enough history exists to compute the signal at all).
    """
    momentum = compute_momentum_series(close)
    return (momentum > 0.0).fillna(False)


def latest_signal(close: pd.Series) -> bool:
    """Convenience wrapper: today's (last row's) signal value."""
    series = compute_signal_series(close)
    if series.empty:
        return False
    return bool(series.iloc[-1])


RANK_LOOKBACK_DAYS = 252
RANK_SKIP_DAYS = 21
"""12-minus-1 cross-sectional relative-rank construction (Jegadeesh &
Titman 1993) -- promoted 2026-08-09 from research/cross_sectional_
momentum_prereg_2026-08-09/cross_sectional_signal.py and
research/relative_ranking_prereg_2026-08-09/relative_ranking_engine.py's
own research-only signal, into this module, per the relative-ranking
study's own "the signal moves into production code" promotion requirement
(live code must never reach into research/). The formula and constants are
unchanged from the research version: rank_signal(t) = close(t-21) /
close(t-252) - 1 -- the same trailing return compute_momentum_series()
computes, but anchored one month earlier, excluding the most recent
month's price action from the ranking value (the standard construction's
short-horizon-reversal exclusion).

This is a RANKING input only, used exclusively to order gate-passing
candidates for capacity-constrained admission when
trading212_invest.admission_ranking_mode == "relative_rank" (see
analysis/decision.py:resolve_admission_rank_value()). It never touches the
entry gate, the 2% momentum floor, or SIGNAL_OFF -- all three stay
absolute-momentum-based regardless of ranking mode, exactly as validated
by the research study (Arm B: "admission sort key only" -- see CLAUDE.md's
"Relative-ranking (cross-sectional rank, daily admission) study" section
for why the entry/exit gate and the ranking key were deliberately kept
separate after an earlier, broader design was narrowed on review).
"""

_MAX_1MO_ANCHOR_GAP_DAYS = 60
"""Gap tolerance for the 21-trading-day ('1 month') leg -- a genuine,
gapless 21-trading-day window spans roughly 30 calendar days (21 trading
days / ~5 trading days per week * 7 calendar days); 60 gives 2x slack for
a holiday-heavy month or a handful of individually-missing days without
over-tolerating a genuine multi-week gap. Unchanged from the research
version's own already-validated constant."""


def compute_relative_rank_series(close: pd.Series) -> pd.Series:
    """Vectorized 12-minus-1 relative-rank value for every date in *close*'s
    own index: close(t-RANK_SKIP_DAYS) / close(t-RANK_LOOKBACK_DAYS) - 1.

    NaN wherever either anchor is unavailable (insufficient history) or
    calendar-stale in EITHER leg -- the 252-day leg reuses
    _MAX_ANCHOR_GAP_DAYS (the exact same guard, and the exact same failure
    mode, as compute_momentum_series() above: a row-count .shift() has no
    way to know whether the anchor it reached back to is genuinely ~12
    months ago in calendar time, or a stale row on the far side of a real
    gap -- see that function's own docstring). The 21-day leg uses the new
    _MAX_1MO_ANCHOR_GAP_DAYS for the identical reason, scaled to a shorter
    window. Callers must treat NaN as "not rankable this date", matching
    this module's own "NaN means no signal yet" convention throughout.
    """
    p1 = close.shift(RANK_SKIP_DAYS)
    p12 = close.shift(RANK_LOOKBACK_DAYS)
    rank_signal = p1 / p12 - 1.0

    if close.empty or not isinstance(close.index, pd.DatetimeIndex):
        return rank_signal

    dates = close.index.to_series()
    anchor_1mo = dates.shift(RANK_SKIP_DAYS)
    anchor_12mo = dates.shift(RANK_LOOKBACK_DAYS)
    gap_1mo = (dates - anchor_1mo).dt.days
    gap_12mo = (dates - anchor_12mo).dt.days
    stale = (gap_1mo > _MAX_1MO_ANCHOR_GAP_DAYS).fillna(False) | (gap_12mo > _MAX_ANCHOR_GAP_DAYS).fillna(False)
    rank_signal[stale.to_numpy()] = float("nan")
    return rank_signal
