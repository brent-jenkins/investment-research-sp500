"""The 2x2 momentum-construction arm definitions.

FOUR ARMS (pre-specified; no arms added, no parameter tuned):

    arm | basis      | lookback
    ----+------------+-------------------------------------------------
    1   | close      | 252-day, no skip   (TODAY'S PRODUCTION CONSTRUCTION)
    2   | adj_close  | 252-day, no skip   (dividend / total-return hypothesis)
    3   | close      | 12-1: t-252 -> t-21 (skip-month hypothesis)
    4   | adj_close  | 12-1                (interaction; reported, never
        |            |                      grounds for adoption on its own)

EXACT FORMULAS (stated as literal code per CLAUDE.md's "Research
reproducibility standard" -- prose is for WHY, never for WHAT):

    arm 1/2, basis series b:
        momentum[t] = b[t] / b[t-252] - 1.0
    arm 3/4, basis series b:
        momentum[t] = b[t-21] / b[t-252] - 1.0

    (t-N means shift by N ROWS, not calendar days -- identical to
     production's signals.compute_momentum_series().)

    staleness guard, ALL FOUR ARMS, unchanged from production:
        stale[t] = (date[t] - date[t-252]).days > 400   -> momentum = NaN

    signal, ALL FOUR ARMS:
        signal[t] = (momentum[t] > 0.0), NaN -> False

    SKIP_DAYS = 21 is FIXED, never tuned. LOOKBACK = 252 = production's
    signals.MOMENTUM_LOOKBACK_DAYS. MAX_ANCHOR_GAP = 400 =
    production's signals._MAX_ANCHOR_GAP_DAYS.

WHY THE STALENESS GUARD NEEDS NO CHANGE FOR THE 12-1 ARMS (checked, not
assumed). The guard exists to catch a genuine multi-month GAP anywhere in
the trailing window, which a pure row-count shift() cannot see. For
arms 3/4 the window's OUTERMOST anchor is still exactly 252 rows back --
the numerator simply moves from t to t-21, i.e. INSIDE the same window.
So `(date[t] - date[t-252]).days > 400` still spans the entire window
including both anchors: a gap between t-252 and t-21 inflates that span,
and so does a gap between t-21 and t. A narrower guard measured only
across t-252..t-21 would MISS a gap in the final month, which would make
b[t-21] a stale price. The production guard is therefore the correct
(and strictly safer) choice for the shifted anchor, not merely a
convenient reuse. NaN counts are reported per arm in the run output.

CRITICAL ISOLATION CONSTRAINT: adj_close is a SYNTHETIC series for
MEASURING return -- you cannot trade at it. Only the momentum/signal
columns are built from the arm's basis. `close` (entry/exit fills),
`atr` (from raw high/low/close), `open` and `low` (the realistic
intraday-touch STOP_LOSS fill) are ALWAYS the raw, unadjusted bars, in
every arm. VaR position sizing reads simulate_portfolio_compounding_mtm()'s
own `close_series` = frame["close"] -- raw close, untouched by this study
(confirmed by reading analysis/backtest.py:1460-1467 and
analysis/volatility.py:_var_position_size_core(), which takes log returns
of whatever series the caller passes and has no price-basis concept of
its own).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from analysis import signals as prod_signals
from analysis.volatility import _atr
from data.ingestion import get_price_df

LOOKBACK = prod_signals.MOMENTUM_LOOKBACK_DAYS          # 252
MAX_ANCHOR_GAP = prod_signals._MAX_ANCHOR_GAP_DAYS      # 400
SKIP_DAYS = 21                                          # FIXED. Never tuned.

ARMS = {
    1: {"basis": "close", "skip": 0, "label": "arm1_close_12_0_BASELINE"},
    2: {"basis": "adj_close", "skip": 0, "label": "arm2_adjclose_12_0"},
    3: {"basis": "close", "skip": SKIP_DAYS, "label": "arm3_close_12_1"},
    4: {"basis": "adj_close", "skip": SKIP_DAYS, "label": "arm4_adjclose_12_1"},
}


def compute_momentum_series_arm(basis: pd.Series, skip: int) -> pd.Series:
    """Arm-parameterised momentum. skip=0 reproduces production's
    signals.compute_momentum_series() exactly (asserted in
    verify_arm1_identity() below and again in parity_check.py against the
    real production function on real tickers)."""
    if skip == 0:
        momentum = basis / basis.shift(LOOKBACK) - 1.0
    else:
        momentum = basis.shift(skip) / basis.shift(LOOKBACK) - 1.0

    if basis.empty or not isinstance(basis.index, pd.DatetimeIndex):
        return momentum

    anchor_dates = basis.index.to_series().shift(LOOKBACK)
    gap_days = (basis.index.to_series() - anchor_dates).dt.days
    stale = gap_days > MAX_ANCHOR_GAP
    momentum[stale.fillna(False).to_numpy()] = float("nan")
    return momentum


def compute_signal_series_arm(basis: pd.Series, skip: int) -> pd.Series:
    momentum = compute_momentum_series_arm(basis, skip)
    return (momentum > 0.0).fillna(False)


def precompute_ticker_arm(ticker: str, config: dict, arm: int) -> Optional[dict]:
    """Arm-parameterised twin of analysis/backtest.py:precompute_ticker().

    IDENTICAL to production in every respect except the two columns
    `momentum` and `signal`, which are computed from the arm's own basis
    series and skip. `close`, `atr`, `open`, `low` are the raw,
    split-adjusted-but-not-dividend-adjusted bars in EVERY arm --
    byte-identical across arms for the same ticker (asserted in
    parity_check.py).

    Kept here, in this research script's own scope, rather than by
    modifying or monkeypatching analysis/signals.py -- that production
    file must stay exactly as Task B left it.
    """
    spec = ARMS[arm]
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    basis = df[spec["basis"]].astype(float)

    atr_s = _atr(high, low, close, config["signals"]["atr_period"])
    momentum_s = compute_momentum_series_arm(basis, spec["skip"])
    sig = compute_signal_series_arm(basis, spec["skip"])

    frame = pd.DataFrame({
        "close": close, "atr": atr_s, "signal": sig, "momentum": momentum_s,
        "open": open_, "low": low,
    })
    return {"ticker": ticker, "frame": frame}


def verify_arm1_identity(ticker: str, config: dict) -> tuple[bool, str]:
    """Arm 1 must be BYTE-IDENTICAL to production precompute_ticker().
    Checked on real tickers, not asserted."""
    from analysis.backtest import precompute_ticker as prod_precompute

    a = precompute_ticker_arm(ticker, config, 1)
    b = prod_precompute(ticker, config)
    if a is None and b is None:
        return True, "both None"
    if (a is None) != (b is None):
        return False, "one None, other not"
    fa, fb = a["frame"], b["frame"]
    if list(fa.columns) != list(fb.columns) or not fa.index.equals(fb.index):
        return False, "column/index mismatch"
    for c in fa.columns:
        if not fa[c].equals(fb[c]):
            return False, f"column {c} differs"
    return True, "identical"
