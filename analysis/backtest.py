"""Vectorized backtesting engine.

Replays the full signal-generation pipeline over historical price data using
pandas rolling/ewm operations, which are inherently look-ahead free — each
computation at date T only uses data up to and including T.

Entry point: run_backtest()  — writes results to backtest_runs / backtest_signals
                                / backtest_trades tables and returns the run_id.

The engine replicates the scoring logic of the five main analysis modules
(trend, mean_reversion, volatility, volume, macro) as time-series operations
rather than per-date scalar computations.  The correlation module is omitted
because it requires pair-level data and contributes a fixed 0.10 weight; its
absence is compensated by normalising confluence over the five present modules.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import (
    BacktestRun,
    BacktestSignal,
    BacktestTrade,
    get_engine,
)
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)

try:
    import talib as _talib
    _TALIB = True
except ImportError:
    _TALIB = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    p = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(p) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Vectorized indicator series
# ---------------------------------------------------------------------------

def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series, fast: int, slow: int, signal: int):
    ema_fast   = close.ewm(span=fast, adjust=False).mean()
    ema_slow   = close.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close  = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    alpha      = 1.0 / period
    smooth_tr  = pd.Series(tr,       index=close.index).ewm(alpha=alpha, adjust=False).mean() * period
    smooth_p   = pd.Series(plus_dm,  index=close.index).ewm(alpha=alpha, adjust=False).mean() * period
    smooth_m   = pd.Series(minus_dm, index=close.index).ewm(alpha=alpha, adjust=False).mean() * period
    pdi  = 100 * smooth_p / smooth_tr.replace(0, np.nan)
    mdi  = 100 * smooth_m / smooth_tr.replace(0, np.nan)
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Vectorized module scoring — returns integer Series (-1 / 0 / 1) per date
# ---------------------------------------------------------------------------

def _ma_cross_vote(sma_50: pd.Series, sma_200: pd.Series, slope_window: int, near_pct: float) -> pd.Series:
    """Vectorized version of analysis/trend.py:_ma_cross_vote — see there for rationale.

    A pure positional SMA50-vs-SMA200 check treats a golden cross as bullish
    even while SMA50 is rolling over toward a death cross. This votes with
    the cross direction only while SMA50's slope agrees with it; otherwise
    the vote goes neutral, or flips if the lines have converged to within
    `near_pct` of each other (an imminent cross).
    """
    positional = pd.Series(np.where(sma_50 > sma_200, 1, -1), index=sma_50.index)
    prev_sma50 = sma_50.shift(slope_window)
    slope_agrees = ((sma_50 - prev_sma50) * positional) >= 0
    gap_pct = (sma_50 - sma_200).abs() / sma_200.replace(0, np.nan)
    imminent = gap_pct < near_pct

    vote = pd.Series(
        np.where(slope_agrees, positional, np.where(imminent, -positional, 0)),
        index=sma_50.index,
    )
    return vote.where(prev_sma50.notna(), positional).astype(int)


def _trend_scores(df: pd.DataFrame, config: dict) -> pd.Series:
    sig      = config["signals"]
    adx_thr  = sig["adx_trend_threshold"]
    slope_w  = int(sig.get("ma_cross_slope_window", 10))
    near_pct = float(sig.get("ma_cross_near_pct", 0.02))
    close    = df["close"].astype(float)
    high     = df["high"].astype(float)
    low      = df["low"].astype(float)

    sma_50  = close.rolling(50).mean()
    sma_200 = close.rolling(200).mean()
    macd_l, macd_s = _macd(close, sig["macd_fast"], sig["macd_slow"], sig["macd_signal"])
    adx_s   = _adx(high, low, close, sig["adx_period"])

    # Three votes
    v1 = pd.Series(np.where(close > sma_200, 1, -1), index=close.index)
    v2 = _ma_cross_vote(sma_50, sma_200, slope_w, near_pct)
    macd_vote = np.where(macd_l > macd_s, 1, -1)
    v3 = pd.Series(np.where(adx_s >= adx_thr, macd_vote, 0), index=close.index)

    valid   = sma_200.notna()
    bull    = (v1 == 1).astype(int) + (v2 == 1).astype(int) + (v3 == 1).astype(int)
    bear    = (v1 == -1).astype(int) + (v2 == -1).astype(int) + (v3 == -1).astype(int)
    raw     = pd.Series(np.where(bull >= 2, 1, np.where(bear >= 2, -1, 0)), index=close.index)
    return raw.where(valid, 0).astype(int)


def _mean_rev_scores(df: pd.DataFrame, config: dict) -> pd.Series:
    """50% RSI + 50% 52-week Z-score — mirrors compute_mean_reversion()'s live
    scoring. Bollinger %B is deliberately not computed here: a production
    correlation analysis (200 rows, 49 tickers, 2026-07-05) measured RSI vs
    Bollinger %B at 0.927, so it was dropped from the score in both the live
    and backtest paths — see analysis/mean_reversion.py's docstring."""
    sig     = config["signals"]
    close   = df["close"].astype(float)

    rsi_s   = _rsi(close, sig["rsi_period"])
    roll252 = close.rolling(252)
    z_score = (close - roll252.mean()) / roll252.std().replace(0, np.nan)

    rsi_sig = pd.Series(np.where(rsi_s < 30, 1, np.where(rsi_s > 70, -1, 0)), index=close.index)
    z_sig   = pd.Series(np.where(z_score < -1.5, 1, np.where(z_score > 1.5, -1, 0)), index=close.index)

    raw  = pd.Series(
        np.where((rsi_sig == 1) & (z_sig == 1), 1,
        np.where((rsi_sig == -1) & (z_sig == -1), -1, 0)),
        index=close.index,
    )
    return raw.astype(int)


def _vol_scores(df: pd.DataFrame, vix_s: Optional[pd.Series], config: dict) -> pd.Series:
    """ATR% percentile + VIX regime (equal-weight combination, mirroring
    compute_volatility()'s live scoring minus its change-point adjustment —
    see the "Scope note" in CLAUDE.md's Volatility module section for why
    CPD isn't vectorised into the backtest). hist_vol_20 was never a scoring
    input here to begin with (only ATR% and VIX ever were) — the production
    correlation analysis that dropped hist_vol_20 from the live score
    (ATR% vs hist_vol_20: 0.929, see analysis/volatility.py's docstring)
    changes nothing on this path."""
    sig     = config["signals"]
    close   = df["close"].astype(float)
    high    = df["high"].astype(float)
    low     = df["low"].astype(float)

    atr_s   = _atr(high, low, close, sig["atr_period"])
    atr_pct = atr_s / close.replace(0, np.nan)

    # Approximate 25th/75th percentile via rolling mean ± 0.674σ
    rm = atr_pct.rolling(252).mean()
    rs = atr_pct.rolling(252).std()
    lo_thr = rm - 0.674 * rs
    hi_thr = rm + 0.674 * rs
    atr_sc  = pd.Series(
        np.where(atr_pct <= lo_thr, 1, np.where(atr_pct >= hi_thr, -1, 0)),
        index=close.index,
    )

    vix_sc      = pd.Series(0, index=close.index, dtype=int)
    extreme_vix = pd.Series(False, index=close.index)
    if vix_s is not None:
        va = vix_s.reindex(close.index).ffill()
        vix_sc      = pd.Series(np.where(va < 12, 1, np.where(va >= 20, -1, 0)), index=close.index)
        extreme_vix = va >= 30

    raw = pd.Series(
        np.where(extreme_vix, -1,
        np.where((atr_sc == 1) & (vix_sc >= 0), 1,
        np.where((atr_sc == -1) | (vix_sc == -1), -1, 0))),
        index=close.index,
    )
    return raw.astype(int)


def _volume_scores(df: pd.DataFrame, config: dict) -> pd.Series:
    period  = config["signals"]["volume_avg_period"]
    close   = df["close"].astype(float)
    volume  = df["volume"].astype(float)

    direction = np.sign(close.diff().fillna(0))
    obv       = (direction * volume).cumsum()
    obv_trend = obv - obv.shift(period)
    vol_ratio = volume / volume.rolling(period).mean().replace(0, np.nan)
    price_ch  = close.diff()

    elevated = vol_ratio >= 1.5
    raw = pd.Series(
        np.where(elevated & (price_ch > 0) & (obv_trend > 0), 1,
        np.where(elevated & (price_ch < 0) & (obv_trend < 0), -1, 0)),
        index=close.index,
    )
    return raw.astype(int)


def _macro_scores(
    df: pd.DataFrame,
    ticker: str,
    vix_s: Optional[pd.Series],
    macro_df: Optional[pd.DataFrame],
    config: dict,
) -> pd.Series:
    """Vectorized mirror of analysis/macro.py:compute_macro()'s regional
    weighting (2026-07-07) — grouped by region, blended via
    signals.macro_regional_weights, with per-date graceful degradation when a
    region has no data yet, mirroring the live _regional_macro_score(). This
    mirrors analysis/macro.py's own per-series region tags (imported
    directly, not re-hardcoded) — the same "shared core, no re-implementation
    of the mapping" pattern already used for VaR sizing elsewhere in this
    file.

    Reverted to latest-observation-only (2026-07-08): each FRED series'
    single most recent stored value is broadcast as a CONSTANT across the
    whole backtest date range, rather than read as a genuinely time-varying
    history from macro_indicators. This deliberately matches what the live
    system has always done — analysis/macro.py:compute_macro() only ever
    reads the latest row per series, never a historical lookup for a given
    past date — and is a regression from feeding this function the full,
    now-backfilled history: doing so exposed real historical macro variation
    to the backtest for the first time, and every TRENDING_BULL macro weight
    tried against it (0.30 down to 0.00, redistributed several different
    ways) produced materially worse results than the documented 253-trade
    baseline — see CLAUDE.md's "Macro module regime-weight rebalance
    (2026-07-08)" section for the full investigation and all four weight
    variants' results. Until a full six-weight (and possibly threshold)
    re-tuning study is done — deferred, out of scope here — this reverts to
    reproducing that baseline. analysis/macro.py:backfill_macro_indicators()
    and the historical depth it stores are kept in place regardless: they're
    the necessary groundwork for that deferred study, even though this
    function doesn't consume the historical depth yet.
    """
    from analysis.macro import _regional_weights, _series_region

    _MACRO_SKIP = frozenset({"^VIX", "TLT", "IEF", "HYG"})
    close = df["close"].astype(float)

    if ticker in _MACRO_SKIP:
        return pd.Series(0, index=close.index, dtype=int)

    region_series: dict[str, list[pd.Series]] = {}

    if vix_s is not None:
        va = vix_s.reindex(close.index).ffill()
        vix_sc = pd.Series(np.where(va < 12, 1.0, np.where(va < 20, 0.0, -1.0)), index=close.index)
        region_series.setdefault("us", []).append(vix_sc)  # VIX is a US-sourced index

    if macro_df is not None and not macro_df.empty:
        latest = macro_df.ffill().iloc[-1]  # each series' single most-recent observation
        for col in macro_df.columns:
            val = latest[col]
            if pd.isna(val):
                continue
            const_series = pd.Series(float(val), index=close.index)
            region_series.setdefault(_series_region(col, config), []).append(const_series)

    if not region_series:
        return pd.Series(0, index=close.index, dtype=int)

    # Per-region average — NaN on any date before that region's first series
    # has reported at all (nothing yet to ffill from).
    region_avg = pd.concat(
        {region: pd.concat(series_list, axis=1).mean(axis=1) for region, series_list in region_series.items()},
        axis=1,
    )

    weights_cfg = _regional_weights(config)
    weight_row  = pd.Series({r: weights_cfg.get(r, 0.0) for r in region_avg.columns})
    avail       = region_avg.notna()
    applied_w   = avail.mul(weight_row, axis=1)
    weight_sum  = applied_w.sum(axis=1)

    # Graceful degradation, vectorized: dates where the configured weights
    # available sum to 0 (e.g. every region with data has a 0 configured
    # weight) fall back to a flat average across whatever regions do have
    # data that date, mirroring _regional_macro_score()'s own fallback.
    flat_fallback = region_avg.mean(axis=1)
    weighted = (region_avg.fillna(0.0) * applied_w).sum(axis=1) / weight_sum.replace(0, np.nan)
    avg = weighted.fillna(flat_fallback).fillna(0.0)

    raw = pd.Series(np.where(avg >= 0.25, 1, np.where(avg <= -0.25, -1, 0)), index=close.index)
    return raw.astype(int)


# ---------------------------------------------------------------------------
# Regime history (vectorized, based on ^GSPC + VIX)
# ---------------------------------------------------------------------------

def _regime_history(gspc_df: pd.DataFrame, vix_s: Optional[pd.Series], config: dict) -> pd.DataFrame:
    """Return a date-indexed DataFrame with a 'regime' column — the
    rule-based ADX/VIX/SMA200 classification.

    An HMM confidence gate (validating, never replacing, this rule-based
    call) was implemented, backtested both alone and combined with a
    MIXED -> TRENDING_BULL upgrade path, and found to deteriorate every
    metric versus the rule-based-only baseline — see CLAUDE.md's "HMM
    confidence gate — why it's disabled" section. Since this is computed
    ONCE per backtest run from the single shared ^GSPC series (not once per
    ticker — every ticker's compute_signal_history() call reuses this same
    DataFrame), the cost of this classification is not multiplied by the
    ticker count either way.
    """
    sig     = config["signals"]
    close   = gspc_df["close"].astype(float)
    high    = gspc_df["high"].astype(float)
    low     = gspc_df["low"].astype(float)

    sma_200 = close.rolling(200).mean()
    adx_s   = _adx(high, low, close, sig["adx_period"])

    vix = pd.Series(np.nan, index=close.index)
    if vix_s is not None:
        vix = vix_s.reindex(close.index).ffill()

    regime = np.where(
        vix > 30, "HIGH_VOLATILITY",
        np.where(
            (close > sma_200) & (adx_s > 25) & (vix < 20), "TRENDING_BULL",
            np.where(
                (close < sma_200) & (adx_s > 25) & (vix > 20), "TRENDING_BEAR",
                np.where(
                    (adx_s < 20) & (vix < 15), "RANGING_LOW_VOL",
                    "MIXED",
                ),
            ),
        ),
    )
    regime = np.where(sma_200.isna(), "MIXED", regime)

    return pd.DataFrame({"regime": regime}, index=close.index)


# ---------------------------------------------------------------------------
# Macro history from DB  (all stored rows, not just latest)
# ---------------------------------------------------------------------------

def _load_macro_history() -> Optional[pd.DataFrame]:
    """Return all stored macro_indicator scores as a wide date×series DataFrame."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT series_id, date, score FROM macro_indicators ORDER BY date")
            ).mappings().all()
    except Exception:
        return None

    if not rows:
        return None

    df = pd.DataFrame([dict(r) for r in rows])
    # format="mixed": macro_indicators can hold rows written through more than
    # one code path with different literal datetime string formats (ORM
    # inserts always include a ".000000" suffix; some bulk/raw-SQL paths
    # historically didn't — see analysis/macro.py:_scored_df_to_rows()'s
    # docstring for the exact mechanism). A plain pd.to_datetime() caches a
    # format from the first values in the column and raises the moment a
    # differently-formatted value appears once rows from both are sorted
    # together by date; format="mixed" parses each value independently instead.
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    try:
        wide = df.pivot(index="date", columns="series_id", values="score")
        return wide.astype(float)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Signal history for one ticker
# ---------------------------------------------------------------------------

def compute_signal_history(
    ticker: str,
    df: pd.DataFrame,
    vix_s: Optional[pd.Series],
    regime_hist: pd.DataFrame,
    macro_df: Optional[pd.DataFrame],
    config: dict,
) -> pd.DataFrame:
    """Compute a daily signal series for *ticker* over the full price history.

    Uses vectorized pandas rolling/ewm — inherently look-ahead free.

    Returns a DataFrame indexed by date with columns:
        close, atr, regime, confluence_score, signal,
        trend_score, mean_reversion_score, volatility_score,
        volume_score, macro_score
    """
    from analysis.regime import REGIME_WEIGHTS, Regime          # local to avoid circular
    from analysis.confluence import get_regime_thresholds       # regime-aware thresholds

    if df.empty or len(df) < 210:
        return pd.DataFrame()

    df = df.copy()
    if "date" in df.columns:
        df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)

    atr_s       = _atr(high, low, close, config["signals"]["atr_period"])
    trend_sc    = _trend_scores(df, config)
    mean_rev_sc = _mean_rev_scores(df, config)
    vol_sc      = _vol_scores(df, vix_s, config)
    volume_sc   = _volume_scores(df, config)
    macro_sc    = _macro_scores(df, ticker, vix_s, macro_df, config)

    regime_aligned = regime_hist["regime"].reindex(df.index).ffill().fillna("MIXED")

    # Build per-date weight series for the five modules included in backtest.
    # Correlation module (weight=0.10) is excluded; remaining weights are
    # re-normalised so they still sum to 1.0 per date.
    WEIGHT_KEYS = ["trend", "mean_reversion", "volatility", "volume", "macro"]
    regime_map  = {r.value: r for r in Regime}

    w = {k: pd.Series(np.nan, index=df.index) for k in WEIGHT_KEYS}
    for rname in regime_aligned.unique():
        mask = regime_aligned == rname
        r    = regime_map.get(rname, Regime.MIXED)
        rw   = REGIME_WEIGHTS[r]
        total = sum(rw[k] for k in WEIGHT_KEYS)
        for k in WEIGHT_KEYS:
            w[k][mask] = rw[k] / total  # normalise to 1.0 without correlation

    confluence = (
        trend_sc.astype(float) * w["trend"] +
        mean_rev_sc.astype(float) * w["mean_reversion"] +
        vol_sc.astype(float) * w["volatility"] +
        volume_sc.astype(float) * w["volume"] +
        macro_sc.astype(float) * w["macro"]
    )

    # Build per-date threshold series from regime — mirrors the weight computation above
    buy_thresh_s  = pd.Series(0.55, index=df.index, dtype=float)
    sell_thresh_s = pd.Series(0.55, index=df.index, dtype=float)
    for _rname in regime_aligned.unique():
        _mask = regime_aligned == _rname
        _bt, _st = get_regime_thresholds(_rname, config)
        buy_thresh_s[_mask]  = _bt
        sell_thresh_s[_mask] = _st

    def _sig(score: float, buy_th: float, sell_th: float) -> str:
        if score >= buy_th:   return "STRONG_BUY"
        if score >= 0.3:      return "BUY"
        if score <= -sell_th: return "STRONG_SELL"
        if score <= -0.3:     return "SELL"
        return "NEUTRAL"

    signal = pd.Series(
        [_sig(c, b, s) for c, b, s in zip(confluence, buy_thresh_s, sell_thresh_s)],
        index=confluence.index,
    )

    result = pd.DataFrame({
        "close":                close,
        "atr":                  atr_s,
        "regime":               regime_aligned,
        "confluence_score":     confluence,
        "signal":               signal,
        "trend_score":          trend_sc,
        "mean_reversion_score": mean_rev_sc,
        "volatility_score":     vol_sc,
        "volume_score":         volume_sc,
        "macro_score":          macro_sc,
    })
    result.index.name = "date"
    return result.dropna(subset=["close", "confluence_score"])


def _resolve_entry_stop(
    direction: str,
    price: float,
    atr: float,
    config: dict,
) -> tuple[float, float]:
    """Return (stop_price, stop_distance_pct) for a new entry, sized via the
    pre-existing entry ± backtest.atr_stop_multiplier x ATR formula.

    A GARCH(1,1)-based dynamic stop was implemented, backtested (via a
    monthly-granularity calibration cache mirroring the live GARCH stop),
    and found to underperform this ATR stop on every risk-adjusted metric —
    see CLAUDE.md's "GARCH/CPD stop loss in the backtest" section.
    """
    atr_mult = float(config.get("backtest", {}).get("atr_stop_multiplier", 2.0))
    distance_frac = (atr_mult * atr / price) if price else 0.0

    stop_distance = distance_frac * price
    stop_price = price - stop_distance if direction == "LONG" else price + stop_distance
    return stop_price, round(distance_frac * 100, 4)


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def simulate_trades(signals: pd.DataFrame, ticker: str, config: dict) -> list[dict]:
    """Walk forward through *signals* and generate simulated trades.

    Entry: gated per-date in two steps against config["backtest"]'s own
    entry-gating keys (entry_signal, entry_signal_overrides,
    allowed_entry_regimes) — kept separate from trading212_invest /
    trading212_stocks because this backtest, unlike those two long-only
    cash-account automation paths, can open both LONG and SHORT positions:
      1. analysis.regime.is_entry_allowed() — a regime not in
         backtest.allowed_entry_regimes blocks entry entirely for that
         date, before the signal is even considered (e.g. MIXED has no
         demonstrable edge, so no position is opened regardless of signal
         strength).
      2. analysis.regime.resolve_entry_signal() — for an allowed regime, the
         resolved entry_signal_overrides threshold ("STRONG_BUY" only enters
         on STRONG_BUY (LONG) / STRONG_SELL (SHORT); "BUY" also enters on
         plain BUY / SELL) gates the signal itself.
    Exit (in priority order, unaffected by either entry gate — an open
    position always continues to be monitored and closed regardless of what
    the regime does next):
      1. Stop loss — entry ± backtest.atr_stop_multiplier × ATR (see
         _resolve_entry_stop() above).
      2. Signal reversal, gated by trading212_invest.exit_signal — "SELL"
         (default) reverses on SELL/STRONG_SELL for LONG (BUY/STRONG_BUY for
         SHORT); "STRONG_SELL" requires the strong variant only. Mirrors the
         live exit_signal threshold used by maybe_place_t212_orders() so the
         backtest's exit behaviour matches production.
      3. Maximum holding period — per-ticker via
         analysis.regime.get_max_holding_days() (ETF/stock/bond/forex/crypto
         asset-class limits read from config).
    """
    from analysis.regime import get_max_holding_days, is_entry_allowed, resolve_entry_signal  # noqa: PLC0415  local import — mirrors compute_signal_history()'s pattern above
    from analysis.risk import _drawdown_tier_from_pct  # noqa: PLC0415  shared drawdown-tier math — see that module's docstring
    from analysis.volatility import _MIN_VAR_RETURNS, _var_position_size_core  # noqa: PLC0415  shared VaR math — see that module's docstring for why this is imported rather than reimplemented

    bt_cfg   = config.get("backtest", {})
    max_hold = get_max_holding_days(ticker, config)

    # VaR position sizing — reads risk parameters from trading212_invest (the
    # backtest has no config block of its own for these; entry-gating alone
    # is kept separate under backtest: because it can go LONG or SHORT, but
    # risk tolerance is not direction-specific, so trading212_invest's
    # values are reused directly here). starting_capital_gbp is a notional
    # account size, not a real compounding balance: simulate_trades() runs
    # one ticker at a time with no shared cross-ticker portfolio, so this
    # value is constant for every entry across the whole run rather than
    # growing/shrinking as a real account would — see CLAUDE.md's "VaR
    # position sizing" section for the full rationale.
    var_cfg           = config.get("trading212_invest", {})
    starting_capital  = float(bt_cfg.get("starting_capital_gbp", 10000))
    var_confidence    = float(var_cfg.get("var_confidence", 0.95))
    var_lookback      = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct      = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp  = float(var_cfg.get("min_position_gbp", 50))
    max_position_gbp  = float(var_cfg.get("max_position_gbp", 500))
    close_series      = signals["close"].astype(float)

    # Drawdown-based position scaling — mirrors the live mechanism (see
    # analysis/risk.py:compute_drawdown_scale_factor()), reusing
    # trading212_invest.drawdown_scaling for the same reason var_cfg above
    # reuses trading212_invest for VaR risk parameters: entry-gating is kept
    # separate under backtest: because it can go LONG or SHORT, but risk
    # tolerance is not direction-specific. Unlike the live path (one shared
    # T212 account), simulate_trades() runs one ticker at a time with no
    # cross-ticker portfolio (see the VaR position sizing note above) — so
    # "drawdown" here is tracked against a running per-ticker notional
    # equity curve (starting_capital + this ticker's own realised pnl_gbp so
    # far in this run), not a real shared account balance. This is the same
    # scoping limitation starting_capital_gbp already has; it lets the
    # backtest show whether the mechanism would have helped or hurt on a
    # per-ticker basis, not a true multi-asset portfolio effect.
    dd_cfg      = var_cfg.get("drawdown_scaling", {})
    dd_enabled  = bool(dd_cfg.get("enabled", False))
    dd_equity   = starting_capital
    dd_peak     = starting_capital
    dd_tier     = "NORMAL"

    _BULL   = frozenset({"BUY", "STRONG_BUY"})
    _BEAR   = frozenset({"SELL", "STRONG_SELL"})
    _STRONG = frozenset({"STRONG_BUY", "STRONG_SELL"})
    _ALL    = _BULL | _BEAR

    exit_threshold = config.get("trading212_invest", {}).get("exit_signal", "SELL")
    _EXIT_BEAR = _BEAR if exit_threshold == "SELL" else frozenset({"STRONG_SELL"})
    _EXIT_BULL = _BULL if exit_threshold == "SELL" else frozenset({"STRONG_BUY"})

    trades: list[dict] = []
    open_trade: Optional[dict] = None

    for dt, row in signals.iterrows():
        price  = float(row["close"])
        sig    = str(row["signal"])
        atr    = float(row["atr"]) if pd.notna(row["atr"]) else 0.0

        if open_trade is None:
            regime_at_row = str(row["regime"])
            if not is_entry_allowed(config, regime_at_row, "backtest"):
                continue
            entry_threshold = resolve_entry_signal(bt_cfg, regime_at_row)
            entry_signals   = _ALL if entry_threshold == "BUY" else _STRONG
            if sig not in entry_signals:
                continue
            direction = "LONG" if sig in _BULL else "SHORT"

            stop, stop_distance_pct = _resolve_entry_stop(direction, price, atr, config)

            # VaR position sizing — uses only prices strictly BEFORE dt (no
            # look-ahead). Falls back to max_position_gbp * 0.5 when fewer
            # than _MIN_VAR_RETURNS (60) prior daily returns are available
            # within the signals range passed to this call — the same
            # fallback threshold and value analysis/volatility.py:
            # compute_var_position_size() uses live, so an entry early in a
            # short requested backtest window degrades safely rather than
            # erroring or silently reusing future data.
            prior_closes = close_series.loc[close_series.index < dt].tail(var_lookback + 1)
            log_returns  = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _daily_var, _holding_var = _var_position_size_core(
                    log_returns, starting_capital, max_position_gbp, max_hold,
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(max_position_gbp * 0.5, 2)

            dd_pct_at_entry = max(0.0, (dd_peak - dd_equity) / dd_peak) if dd_peak > 0 else 0.0
            if dd_enabled:
                dd_tier, dd_scale = _drawdown_tier_from_pct(dd_pct_at_entry, dd_cfg, dd_tier)
                if dd_scale < 1.0:
                    position_gbp = max(position_gbp * dd_scale, min_position_gbp)
            else:
                dd_scale = 1.0

            open_trade = {
                "ticker":                   ticker,
                "direction":                direction,
                "entry_date":               dt,
                "entry_price":              price,
                "entry_signal":             sig,
                "stop_price":               stop,
                "stop_distance_pct":        stop_distance_pct,
                "position_gbp":             position_gbp,
                "drawdown_scale_factor":    dd_scale,
                "drawdown_pct_at_entry":    dd_pct_at_entry,
                "regime_at_entry":          str(row["regime"]),
                "trend_at_entry":           int(row["trend_score"]),
                "mean_reversion_at_entry":  int(row["mean_reversion_score"]),
                "volatility_at_entry":      int(row["volatility_score"]),
                "volume_at_entry":          int(row["volume_score"]),
                "macro_at_entry":           int(row["macro_score"]),
            }
            continue

        direction    = open_trade["direction"]
        entry_price  = open_trade["entry_price"]
        stop_price   = open_trade["stop_price"]
        holding_days = (dt - open_trade["entry_date"]).days

        exit_reason: Optional[str] = None
        exit_price = price

        # 1. Stop loss
        if direction == "LONG"  and price <= stop_price:
            exit_reason = "STOP_LOSS"
            exit_price  = stop_price  # assume filled at stop
        elif direction == "SHORT" and price >= stop_price:
            exit_reason = "STOP_LOSS"
            exit_price  = stop_price
        # 2. Signal reversal (gated by trading212_invest.exit_signal)
        elif direction == "LONG"  and sig in _EXIT_BEAR:
            exit_reason = "SIGNAL_REVERSAL"
        elif direction == "SHORT" and sig in _EXIT_BULL:
            exit_reason = "SIGNAL_REVERSAL"
        # 3. Max holding period
        elif holding_days >= max_hold:
            exit_reason = "MAX_HOLDING"

        if exit_reason:
            pnl_pct = (exit_price - entry_price) / entry_price
            if direction == "SHORT":
                pnl_pct = -pnl_pct

            trades.append({
                "ticker":                   ticker,
                "direction":                direction,
                "entry_date":               open_trade["entry_date"].date() if hasattr(open_trade["entry_date"], "date") else open_trade["entry_date"],
                "exit_date":                dt.date() if hasattr(dt, "date") else dt,
                "entry_price":              entry_price,
                "exit_price":               float(exit_price),
                "entry_signal":             open_trade["entry_signal"],
                "exit_reason":              exit_reason,
                "pnl_pct":                  float(pnl_pct),
                "position_gbp":             open_trade["position_gbp"],
                "pnl_gbp":                  open_trade["position_gbp"] * float(pnl_pct),
                "stop_distance_pct":        open_trade["stop_distance_pct"],
                "drawdown_scale_factor":    open_trade["drawdown_scale_factor"],
                "drawdown_pct_at_entry":    open_trade["drawdown_pct_at_entry"],
                "regime_at_entry":          open_trade["regime_at_entry"],
                "trend_at_entry":           open_trade["trend_at_entry"],
                "mean_reversion_at_entry":  open_trade["mean_reversion_at_entry"],
                "volatility_at_entry":      open_trade["volatility_at_entry"],
                "volume_at_entry":          open_trade["volume_at_entry"],
                "macro_at_entry":           open_trade["macro_at_entry"],
                "holding_days":             holding_days,
                "is_win":                   pnl_pct > 0,
            })
            dd_equity += open_trade["position_gbp"] * float(pnl_pct)
            dd_peak    = max(dd_peak, dd_equity)
            open_trade = None

    # Close any still-open position at end of data
    if open_trade is not None:
        last      = signals.iloc[-1]
        exit_p    = float(last["close"])
        pnl_pct   = (exit_p - open_trade["entry_price"]) / open_trade["entry_price"]
        if open_trade["direction"] == "SHORT":
            pnl_pct = -pnl_pct
        hd = (signals.index[-1] - open_trade["entry_date"]).days
        trades.append({
            "ticker":                   ticker,
            "direction":                open_trade["direction"],
            "entry_date":               open_trade["entry_date"].date() if hasattr(open_trade["entry_date"], "date") else open_trade["entry_date"],
            "exit_date":                signals.index[-1].date(),
            "entry_price":              open_trade["entry_price"],
            "exit_price":               exit_p,
            "entry_signal":             open_trade["entry_signal"],
            "exit_reason":              "END_OF_DATA",
            "pnl_pct":                  float(pnl_pct),
            "position_gbp":             open_trade["position_gbp"],
            "pnl_gbp":                  open_trade["position_gbp"] * float(pnl_pct),
            "stop_distance_pct":        open_trade["stop_distance_pct"],
            "drawdown_scale_factor":    open_trade["drawdown_scale_factor"],
            "drawdown_pct_at_entry":    open_trade["drawdown_pct_at_entry"],
            "regime_at_entry":          open_trade["regime_at_entry"],
            "trend_at_entry":           open_trade["trend_at_entry"],
            "mean_reversion_at_entry":  open_trade["mean_reversion_at_entry"],
            "volatility_at_entry":      open_trade["volatility_at_entry"],
            "volume_at_entry":          open_trade["volume_at_entry"],
            "macro_at_entry":           open_trade["macro_at_entry"],
            "holding_days":             hd,
            "is_win":                   pnl_pct > 0,
        })

    return trades


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _create_run(engine, name: str, start: datetime, end: datetime,
                tickers: list[str], config: dict) -> int:
    bt_cfg = config.get("backtest", {})
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO backtest_runs "
                "(name, created_at, start_date, end_date, tickers_json, params_json, status) "
                "VALUES (:name, :ts, :sd, :ed, :tj, :pj, 'pending')"
            ),
            {
                "name": name or f"Backtest {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
                "ts":   datetime.utcnow(),
                "sd":   start.date() if isinstance(start, datetime) else start,
                "ed":   end.date()   if isinstance(end,   datetime) else end,
                "tj":   json.dumps(tickers),
                "pj":   json.dumps(bt_cfg),
            },
        )
        return result.lastrowid


def _update_run_status(engine, run_id: int, status: str, error: str = "") -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE backtest_runs SET status = :s, error_message = :e WHERE id = :id"),
            {"s": status, "e": error, "id": run_id},
        )


_SIGNALS_UPSERT_SQL = text("""
    INSERT INTO backtest_signals
        (run_id, ticker, date, regime, confluence_score, signal, trend_score,
         mean_reversion_score, volatility_score, volume_score, macro_score, close_price, atr)
    VALUES
        (:run_id, :ticker, :date, :regime, :confluence_score, :signal, :trend_score,
         :mean_reversion_score, :volatility_score, :volume_score, :macro_score, :close_price, :atr)
    ON CONFLICT(run_id, ticker, date) DO UPDATE SET
        confluence_score = excluded.confluence_score,
        signal            = excluded.signal
""")


def _store_signals_batch(engine, run_id: int, ticker: str, signals: pd.DataFrame) -> None:
    """Upsert one backtest_signals row per date in *signals*.

    Uses raw SQL with a list-of-dicts parameter set (conn.execute(text(...),
    rows)) rather than sqlite_insert(BacktestSignal).values(chunk) — the
    original ORM-expression-builder approach measured at ~0.6s/ticker (~38s
    across a 64-ticker run) purely from SQLAlchemy's Core/ORM query-building
    overhead for a multi-row VALUES + ON CONFLICT construct, unrelated to
    the actual row count or SQLite itself (a Core-Table-object variant of
    the same `.values([...])` pattern measured identically slow). Passing a
    list of dicts to conn.execute() here instead dispatches through the
    DBAPI's executemany() with a single prepared statement reused across
    all rows — measured ~12x faster for the same data (0.43s vs 5.2s for 12
    tickers' worth of rows in isolation). Discovered while adding GARCH/CPD
    support to the backtest (see CLAUDE.md's "Backtesting engine
    performance" note) — this fix is unrelated to that feature but was
    necessary to keep a full 64-ticker run within its performance budget.
    """
    rows = []
    for dt, row in signals.iterrows():
        d = dt.date() if hasattr(dt, "date") else dt
        rows.append({
            "run_id":               run_id,
            "ticker":               ticker,
            "date":                 d,
            "regime":               str(row["regime"]),
            "confluence_score":     float(row["confluence_score"]),
            "signal":               str(row["signal"]),
            "trend_score":          int(row["trend_score"]),
            "mean_reversion_score": int(row["mean_reversion_score"]),
            "volatility_score":     int(row["volatility_score"]),
            "volume_score":         int(row["volume_score"]),
            "macro_score":          int(row["macro_score"]),
            "close_price":          float(row["close"]),
            "atr":                  float(row["atr"]) if pd.notna(row["atr"]) else None,
        })
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(_SIGNALS_UPSERT_SQL, rows)


def _store_trades_batch(engine, run_id: int, trades: list[dict]) -> None:
    if not trades:
        return
    rows = [{"run_id": run_id, **t} for t in trades]
    _BATCH = 200
    with engine.begin() as conn:
        for i in range(0, len(rows), _BATCH):
            conn.execute(sqlite_insert(BacktestTrade).values(rows[i : i + _BATCH]))


def _update_run_complete(engine, run_id: int, metrics: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE backtest_runs SET "
                "status='complete', total_trades=:tt, win_rate=:wr, "
                "avg_return_pct=:ar, sharpe_ratio=:sr, max_drawdown_pct=:md, "
                "total_return_pct=:tr, avg_holding_days=:ah "
                "WHERE id=:id"
            ),
            {
                "tt": metrics.get("total_trades", 0),
                "wr": metrics.get("win_rate"),
                "ar": metrics.get("avg_return_pct"),
                "sr": metrics.get("sharpe_ratio"),
                "md": metrics.get("max_drawdown_pct"),
                "tr": metrics.get("total_return_pct"),
                "ah": metrics.get("avg_holding_days"),
                "id": run_id,
            },
        )


# ---------------------------------------------------------------------------
# Quick metrics for run summary (full metrics in backtest_metrics.py)
# ---------------------------------------------------------------------------

def _quick_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"total_trades": 0}
    df = pd.DataFrame(trades)
    n  = len(df)
    wr = df["is_win"].mean()
    ar = df["pnl_pct"].mean() * 100
    tr = df["pnl_pct"].sum() * 100  # simple sum (not compounded)
    ah = df["holding_days"].mean()

    # Sharpe: annualised (assume each trade = independent daily observation)
    if n > 1 and df["pnl_pct"].std() > 0:
        sr = (df["pnl_pct"].mean() / df["pnl_pct"].std()) * np.sqrt(252 / ah if ah > 0 else 1)
    else:
        sr = 0.0

    # Max drawdown on cumulative equity curve
    cum = (1 + df["pnl_pct"]).cumprod()
    peak = cum.cummax()
    dd   = ((cum - peak) / peak).min() * 100

    return {
        "total_trades":    n,
        "win_rate":        round(float(wr), 4),
        "avg_return_pct":  round(float(ar), 4),
        "total_return_pct": round(float(tr), 4),
        "sharpe_ratio":    round(float(sr), 4),
        "max_drawdown_pct": round(float(dd), 4),
        "avg_holding_days": round(float(ah), 1),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backtest(
    tickers: list[str],
    start_date: datetime,
    end_date: datetime,
    config: Optional[dict] = None,
    name: str = "",
) -> int:
    """Run the full vectorized backtest and persist results.

    Returns:
        run_id — integer primary key of the BacktestRun row.
    """
    if config is None:
        config = _load_config()

    engine = get_engine()
    run_id = _create_run(engine, name, start_date, end_date, tickers, config)

    try:
        _update_run_status(engine, run_id, "running")
        logger.info("Backtest run %d starting: %d tickers, %s → %s",
                    run_id, len(tickers), start_date, end_date)

        # --- Shared series (loaded once for all tickers) ---------------------
        vix_df = get_price_df("^VIX", days=4000)
        if not vix_df.empty:
            vix_df = vix_df.set_index("date")
            vix_df.index = pd.to_datetime(vix_df.index)
            vix_s = vix_df["close"].astype(float)
        else:
            vix_s = None

        gspc_df = get_price_df("^GSPC", days=4000)
        if not gspc_df.empty:
            gspc_df = gspc_df.set_index("date")
            gspc_df.index = pd.to_datetime(gspc_df.index)
            reg_hist = _regime_history(gspc_df, vix_s, config)
        else:
            reg_hist = pd.DataFrame({"regime": pd.Series(dtype=str)})

        macro_df = _load_macro_history()

        # Date bounds as Timestamps for filtering
        ts_start = pd.Timestamp(start_date)
        ts_end   = pd.Timestamp(end_date)

        # --- Per-ticker signal computation -----------------------------------
        all_trades: list[dict] = []
        for ticker in tickers:
            try:
                df = get_price_df(ticker, days=4000)
                if df.empty or len(df) < 250:
                    logger.debug("Skipping %s — insufficient price data (%d rows).", ticker, len(df))
                    continue

                df = df.set_index("date")
                df.index = pd.to_datetime(df.index)

                signals = compute_signal_history(ticker, df, vix_s, reg_hist, macro_df, config)
                if signals.empty:
                    continue

                # Store full signal history for attribution / decay analysis
                _store_signals_batch(engine, run_id, ticker, signals)

                # Filter to requested date range for trade simulation
                in_range = signals[(signals.index >= ts_start) & (signals.index <= ts_end)]
                if in_range.empty:
                    continue

                ticker_trades = simulate_trades(in_range, ticker, config)
                all_trades.extend(ticker_trades)
                logger.debug("%s: %d trades simulated.", ticker, len(ticker_trades))

            except Exception as exc:
                logger.warning("Backtest: error processing %s: %s", ticker, exc)
                continue

        # --- Store trades and finalize ---------------------------------------
        _store_trades_batch(engine, run_id, all_trades)
        metrics = _quick_metrics(all_trades)
        _update_run_complete(engine, run_id, metrics)

        logger.info(
            "Backtest run %d complete — %d trades, win_rate=%.1f%%, return=%.1f%%",
            run_id, metrics.get("total_trades", 0),
            (metrics.get("win_rate") or 0) * 100,
            metrics.get("total_return_pct") or 0,
        )
        return run_id

    except Exception as exc:
        logger.error("Backtest run %d failed: %s", run_id, exc, exc_info=True)
        _update_run_status(engine, run_id, "error", error=str(exc))
        raise


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_all_runs() -> list[dict]:
    """Return all backtest runs ordered newest first."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM backtest_runs ORDER BY created_at DESC")
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_run(run_id: int) -> Optional[dict]:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM backtest_runs WHERE id = :id"), {"id": run_id}
            ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def get_run_trades(run_id: int) -> pd.DataFrame:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM backtest_trades WHERE run_id = :id ORDER BY entry_date"),
                {"id": run_id},
            ).mappings().all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["entry_date"] = pd.to_datetime(df["entry_date"])
        df["exit_date"]  = pd.to_datetime(df["exit_date"])
        return df
    except Exception:
        return pd.DataFrame()


def get_run_signals(run_id: int, ticker: Optional[str] = None) -> pd.DataFrame:
    engine = get_engine()
    sql = "SELECT * FROM backtest_signals WHERE run_id = :id"
    params: dict = {"id": run_id}
    if ticker:
        sql += " AND ticker = :t"
        params["t"] = ticker
    sql += " ORDER BY ticker, date"
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return pd.DataFrame()


def delete_run(run_id: int) -> None:
    """Delete a backtest run and all its associated signals and trades.

    Runs VACUUM after the delete transaction commits to reclaim the freed
    page space on disk — backtest_signals in particular can accumulate a
    large number of rows per run (one per ticker per date), so repeated
    deletes without a VACUUM leave the .db file bloated with empty pages.
    """
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM backtest_signals WHERE run_id = :id"), {"id": run_id})
        conn.execute(text("DELETE FROM backtest_trades  WHERE run_id = :id"), {"id": run_id})
        conn.execute(text("DELETE FROM backtest_runs    WHERE id     = :id"), {"id": run_id})

    # VACUUM cannot run inside a transaction — must be on a fresh connection
    # after the DELETE transaction above has already committed.
    with get_engine().connect() as conn:
        conn.execute(text("VACUUM"))
        conn.commit()
    logger.info("VACUUM complete after deleting backtest run %s", run_id)
