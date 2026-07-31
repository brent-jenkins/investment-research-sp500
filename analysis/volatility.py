"""Shared execution math: ATR, entry-stop resolution, and VaR position sizing.

Used by both the backtest engine (analysis/backtest.py) and live order
placement (brokers/trading212.py) so backtested and live behaviour can never
quietly drift apart — see each function's own docstring for the specific
callers and why the formula lives in exactly one place.
"""
import math

import numpy as np
import pandas as pd


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Average True Range via an exponential moving average of true range."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _resolve_entry_stop(
    direction: str,
    price: float,
    atr: float,
    config: dict,
) -> tuple[float, float]:
    """Return (stop_price, stop_distance_pct) for a new entry, sized via
    entry ± backtest.atr_stop_multiplier x ATR.

    A GARCH(1,1)-based dynamic stop was tried and found to underperform this
    ATR stop on every risk-adjusted metric during V1's development — see
    CLAUDE.md's "GARCH stop loss — why it was removed" section.
    """
    atr_mult = float(config.get("backtest", {}).get("atr_stop_multiplier", 2.0))
    distance_frac = (atr_mult * atr / price) if price else 0.0

    stop_distance = distance_frac * price
    stop_price = price - stop_distance if direction == "LONG" else price + stop_distance
    return stop_price, round(distance_frac * 100, 4)


_MIN_VAR_RETURNS = 60


def _var_position_size_core(
    log_returns: "pd.Series | np.ndarray",
    account_value_gbp: float,
    max_position_gbp: float,
    holding_days: int,
    var_confidence: float,
    max_risk_pct: float,
    min_position_gbp: float,
) -> tuple[float, float, float]:
    """Pure VaR position-sizing math — no DB access, no look-ahead risk.

    Historical VaR: the (1 - var_confidence) percentile of the daily
    log-return distribution (e.g. the 5th percentile at 95% confidence).
    Historical (percentile-based) rather than Parametric VaR because
    day-to-day equity/ETF returns are fat-tailed — a normal-distribution
    assumption understates tail risk. Not Monte Carlo VaR — needlessly
    expensive for a sizing calculation that has to run for ~73 tickers on
    every EOD pass.

    Scaled to the position's actual holding period via the square-root-of-time
    rule (holding_var = daily_var * sqrt(holding_days)) since a longer hold
    has more time to accumulate an adverse move. Position size is then set so
    that a holding-period move of that magnitude would lose at most
    max_risk_pct of account_value_gbp: position_gbp = (account_value_gbp *
    max_risk_pct) / abs(holding_var). Floored at min_position_gbp, capped at
    max_position_gbp — max_position_gbp remains a hard ceiling regardless of
    how low volatility gets.

    Shared by brokers/trading212.py (the live-trading path, which reads
    price_data via get_price_df() relative to today) and
    analysis/backtest.py:simulate_trades() (which cannot use get_price_df()
    directly, since that always fetches relative to *today* — a historical
    backtest instead passes in log returns computed from prices strictly
    before the entry date, to avoid look-ahead bias). Keeping the formula in
    one place means live and backtested position sizing can never quietly
    drift apart.

    Returns (position_gbp, daily_var, holding_var). daily_var and holding_var
    are returned so callers can log/display them without recomputing.
    """
    daily_var = float(np.percentile(log_returns, (1 - var_confidence) * 100))
    holding_var = daily_var * math.sqrt(max(holding_days, 1))

    if holding_var == 0:
        # No observed downside at all in the sample (e.g. a perfectly flat
        # series) — nothing to constrain against, so use the full ceiling.
        return max_position_gbp, daily_var, holding_var

    position_gbp = (account_value_gbp * max_risk_pct) / abs(holding_var)
    position_gbp = max(min_position_gbp, min(position_gbp, max_position_gbp))
    return round(position_gbp, 2), daily_var, holding_var
