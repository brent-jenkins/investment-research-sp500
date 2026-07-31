"""Backtest engine — momentum signal + crisis override, validated across the
phased rebuild (see CLAUDE.md's "V2 architecture — phased rebuild" section
for the full validation history). This is the module the reproducibility
check ran against to confirm the production execution path
(brokers/trading212.py) matches what the research clone validated.

Also holds the metrics/equity-curve functions the engine itself needs
(compute_metrics, equity_curve_gbp) — these were originally in a separate
analysis/backtest_metrics.py alongside several V1-only analysis functions
(module attribution, signal decay, walk-forward by calendar window, per-
regime performance) that had no equivalent here; consolidated into this
file since these two are the only ones this engine (or live execution)
actually uses.
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

from analysis import crisis, signals
from analysis import universe
from analysis.universe import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _atr, _resolve_entry_stop, _var_position_size_core
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metrics / equity curve
# ---------------------------------------------------------------------------

def compute_metrics(df: pd.DataFrame) -> dict:
    """Compute full performance metrics from a trades DataFrame.

    Args:
        df: DataFrame with columns pnl_pct, is_win, holding_days, entry_date.
            An optional pnl_gbp column (VaR-scaled £ P&L) adds a parallel set
            of "_gbp"-suffixed keys to the returned dict below; all existing
            "_pct"-suffixed keys are always computed from pnl_pct.

    Returns:
        Dict of metrics suitable for display.
    """
    if df is None or df.empty:
        return {"total_trades": 0}

    n        = len(df)
    wins     = df[df["is_win"] == True]
    losses   = df[df["is_win"] == False]

    win_rate = len(wins) / n if n > 0 else 0.0
    avg_ret  = df["pnl_pct"].mean() * 100
    avg_win  = wins["pnl_pct"].mean() * 100  if not wins.empty  else 0.0
    avg_loss = losses["pnl_pct"].mean() * 100 if not losses.empty else 0.0

    profit_factor = (
        (wins["pnl_pct"].sum() / abs(losses["pnl_pct"].sum()))
        if not losses.empty and losses["pnl_pct"].sum() != 0
        else float("inf")
    )

    avg_hold = df["holding_days"].mean() if "holding_days" in df.columns else 0.0
    total_return = df["pnl_pct"].sum() * 100

    # Equity curve (compounded)
    cum = (1 + df["pnl_pct"]).cumprod()
    final_equity = float(cum.iloc[-1]) - 1 if not cum.empty else 0.0
    peak = cum.cummax()
    max_dd = float(((cum - peak) / peak).min() * 100) if not cum.empty else 0.0

    # Sharpe (annualised, treating each trade return as a period)
    if n > 1 and df["pnl_pct"].std() > 0 and avg_hold > 0:
        periods_per_year = 252.0 / avg_hold
        sr = (df["pnl_pct"].mean() / df["pnl_pct"].std()) * np.sqrt(periods_per_year)
    else:
        sr = 0.0

    # Calmar
    calmar = (final_equity * 100 / abs(max_dd)) if max_dd < 0 else float("inf")

    # Longest drawdown streak
    streak = max_loss_streak = 0
    for w in df["is_win"]:
        if not w:
            streak += 1
            max_loss_streak = max(max_loss_streak, streak)
        else:
            streak = 0

    result = {
        "total_trades":       n,
        "win_rate":           round(win_rate, 4),
        "avg_return_pct":     round(avg_ret, 3),
        "avg_win_pct":        round(avg_win, 3),
        "avg_loss_pct":       round(avg_loss, 3),
        "profit_factor":      round(min(profit_factor, 99.9), 2),
        "total_return_pct":   round(total_return, 2),
        "compounded_return_pct": round(final_equity * 100, 2),
        "max_drawdown_pct":   round(max_dd, 2),
        "sharpe_ratio":       round(sr, 3),
        "calmar_ratio":       round(min(calmar, 99.9), 2),
        "avg_holding_days":   round(avg_hold, 1),
        "max_loss_streak":    max_loss_streak,
        "has_pnl_gbp":        False,
    }

    if "pnl_gbp" in df.columns and df["pnl_gbp"].notna().any():
        gbp = df["pnl_gbp"].dropna().astype(float)
        gbp_wins   = df[(df["is_win"] == True)  & df["pnl_gbp"].notna()]["pnl_gbp"]
        gbp_losses = df[(df["is_win"] == False) & df["pnl_gbp"].notna()]["pnl_gbp"]

        profit_factor_gbp = (
            (gbp_wins.sum() / abs(gbp_losses.sum()))
            if not gbp_losses.empty and gbp_losses.sum() != 0
            else float("inf")
        )

        result.update({
            "has_pnl_gbp":       True,
            "total_pnl_gbp":     round(float(gbp.sum()), 2),
            "avg_pnl_gbp":       round(float(gbp.mean()), 2),
            "avg_win_gbp":       round(float(gbp_wins.mean()), 2)  if not gbp_wins.empty  else 0.0,
            "avg_loss_gbp":      round(float(gbp_losses.mean()), 2) if not gbp_losses.empty else 0.0,
            "profit_factor_gbp": round(min(profit_factor_gbp, 999999.0), 2),
        })

    return result


def equity_curve_gbp(df: pd.DataFrame, starting_capital_gbp: float = 10000.0) -> pd.DataFrame:
    """Return a daily £ portfolio equity curve DataFrame from VaR-sized trades.

    A true £ equity curve: with VaR position sizing, each trade only ever
    risks a small slice of the account (position_gbp), so £ P&L ADDS onto a
    starting capital baseline: equity_t = starting_capital_gbp +
    cumsum(pnl_gbp), realised in exit-date order (the order P&L actually
    lands in a real account, not entry-date order — entry-date order can
    produce backwards jumps in the time axis whenever trades of different
    holding periods overlap). Returns an empty-columns DataFrame (not a
    curve pinned at starting_capital_gbp) when df has no usable pnl_gbp, so
    callers can tell "no £-sized trades in this run" apart from "a flat
    £ curve".
    """
    if df is None or df.empty or "pnl_gbp" not in df.columns or df["pnl_gbp"].isna().all():
        return pd.DataFrame(columns=["date", "equity_gbp", "drawdown_gbp"])

    df = df.dropna(subset=["pnl_gbp"]).copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.normalize()
    exit_col = "exit_date" if "exit_date" in df.columns else "entry_date"
    df["exit_date"] = pd.to_datetime(df[exit_col]).dt.normalize()

    df = df.sort_values("exit_date").reset_index(drop=True)
    cum_at_exit = starting_capital_gbp + df["pnl_gbp"].cumsum()

    exit_equity = pd.Series(cum_at_exit.values, index=df["exit_date"])
    exit_equity = exit_equity.groupby(level=0).last()

    all_days = pd.date_range(df["entry_date"].min(), df["exit_date"].max(), freq="D")
    equity = exit_equity.reindex(all_days).ffill().fillna(starting_capital_gbp)

    peak = equity.cummax()
    dd   = equity - peak

    return pd.DataFrame({
        "date":           all_days,
        "equity_gbp":     equity.values,
        "drawdown_gbp":   dd.values,
    })


# ---------------------------------------------------------------------------
# Signal precomputation
# ---------------------------------------------------------------------------

def precompute_ticker(ticker: str, config: dict) -> Optional[dict]:
    """Load a ticker's full available price history and compute its ATR
    and momentum signal. Membership is looked up per-date at simulation
    time (not baked in here) so a live universe.py refresh is always
    reflected without needing to re-run this step."""
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    atr_s = _atr(high, low, close, config["signals"]["atr_period"])
    sig = signals.compute_signal_series(close)

    frame = pd.DataFrame({"close": close, "atr": atr_s, "signal": sig})
    return {"ticker": ticker, "frame": frame}


def slice_ticker(cached: Optional[dict], ts_start: pd.Timestamp, ts_end: pd.Timestamp) -> Optional[dict]:
    if cached is None:
        return None
    frame = cached["frame"]
    in_range = frame[(frame.index >= ts_start) & (frame.index <= ts_end)].dropna(subset=["close"])
    if in_range.empty:
        return None
    return {"ticker": cached["ticker"], "frame": in_range}


def make_blocks(start: pd.Timestamp, end: pd.Timestamp, n_blocks: int = 16) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    boundaries = pd.date_range(start, end, periods=n_blocks + 1)
    return [(boundaries[i], boundaries[i + 1]) for i in range(n_blocks)]


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def simulate_trades(
    frame: pd.DataFrame,
    ticker: str,
    config: dict,
    gspc_close: pd.Series,
    is_etf: bool,
) -> list[dict]:
    """Walk forward through *frame*, applying (in priority order): the
    crisis override (force-exit + entry block), index-membership gating
    (stocks only), the ATR stop, the momentum signal turning off, and the
    per-ticker max-holding backstop. Position sizing is the shared VaR
    formula every prior research path has used."""
    bt_cfg = config.get("backtest", {})
    max_hold = get_max_holding_days(ticker, config)

    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(bt_cfg.get("starting_capital_gbp", 10000))
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    max_position_gbp = float(var_cfg.get("max_position_gbp", 500))
    close_series = frame["close"].astype(float)

    raw_crisis = crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
    entry_blocked = crisis.extend_trigger(raw_crisis).reindex(frame.index).ffill().fillna(False)

    trades: list[dict] = []
    open_trade = None

    for dt, row in frame.iterrows():
        price = float(row["close"])
        atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
        sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
        is_member = is_etf or universe.is_member(ticker, dt.date() if hasattr(dt, "date") else dt)
        crisis_now = bool(raw_crisis.loc[dt])
        blocked_now = bool(entry_blocked.loc[dt])

        if open_trade is None:
            if not sig_on or not is_member or blocked_now:
                continue
            direction = "LONG"
            stop, stop_distance_pct = _resolve_entry_stop(direction, price, atr, config)

            prior_closes = close_series.loc[close_series.index < dt].tail(var_lookback + 1)
            log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, starting_capital, max_position_gbp, max_hold,
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(max_position_gbp * 0.5, 2)

            open_trade = {
                "ticker": ticker, "direction": direction, "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
            }
            continue

        entry_price = open_trade["entry_price"]
        stop_price = open_trade["stop_price"]
        holding_days = (dt - open_trade["entry_date"]).days

        exit_reason = None
        exit_price = price

        if crisis_now:
            exit_reason = "CRISIS_EXIT"
        elif not is_member:
            exit_reason = "INDEX_REMOVAL"
        elif price <= stop_price:
            exit_reason = "STOP_LOSS"
            exit_price = stop_price
        elif not sig_on:
            exit_reason = "SIGNAL_OFF"
        elif holding_days >= max_hold:
            exit_reason = "MAX_HOLDING"

        if exit_reason:
            pnl_pct = (exit_price - entry_price) / entry_price
            trades.append({
                "ticker": ticker, "direction": "LONG",
                "entry_date": open_trade["entry_date"].date() if hasattr(open_trade["entry_date"], "date") else open_trade["entry_date"],
                "exit_date": dt.date() if hasattr(dt, "date") else dt,
                "entry_price": entry_price, "exit_price": float(exit_price),
                "exit_reason": exit_reason, "pnl_pct": float(pnl_pct),
                "position_gbp": open_trade["position_gbp"], "pnl_gbp": open_trade["position_gbp"] * float(pnl_pct),
                "stop_distance_pct": open_trade["stop_distance_pct"],
                "holding_days": holding_days, "is_win": pnl_pct > 0,
            })
            open_trade = None

    if open_trade is not None:
        last = frame.iloc[-1]
        exit_p = float(last["close"])
        pnl_pct = (exit_p - open_trade["entry_price"]) / open_trade["entry_price"]
        hd = (frame.index[-1] - open_trade["entry_date"]).days
        trades.append({
            "ticker": ticker, "direction": "LONG",
            "entry_date": open_trade["entry_date"].date() if hasattr(open_trade["entry_date"], "date") else open_trade["entry_date"],
            "exit_date": frame.index[-1].date(),
            "entry_price": open_trade["entry_price"], "exit_price": exit_p,
            "exit_reason": "END_OF_DATA", "pnl_pct": float(pnl_pct),
            "position_gbp": open_trade["position_gbp"], "pnl_gbp": open_trade["position_gbp"] * float(pnl_pct),
            "stop_distance_pct": open_trade["stop_distance_pct"],
            "holding_days": hd, "is_win": pnl_pct > 0,
        })

    return trades


def run_candidate(tickers_cache: list, config: dict, gspc_close: pd.Series) -> list[dict]:
    all_trades: list[dict] = []
    for cached in tickers_cache:
        if cached is None:
            continue
        ticker = cached["ticker"]
        is_etf = ticker in universe.ETF_TICKERS
        all_trades.extend(simulate_trades(cached["frame"], ticker, config, gspc_close, is_etf))
    return all_trades


def ex_best_trades(trades: list[dict]) -> list[dict]:
    if not trades:
        return []
    df = pd.DataFrame(trades)
    if df.empty:
        return trades
    idx_best = df["pnl_pct"].idxmax()
    return df.drop(index=idx_best).to_dict("records")


def compute_metrics_fixed(trades: list[dict], starting_capital: float) -> dict:
    """Additive-GBP, exit-date-sorted drawdown/CAGR — the corrected
    methodology, the standard for every result reported since."""
    if not trades:
        return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0, "cagr_pct": 0.0,
                "total_return_pct": 0.0}
    df = pd.DataFrame(trades)
    base = compute_metrics(df)

    ec = equity_curve_gbp(df, starting_capital_gbp=starting_capital)
    if ec.empty or len(ec) < 2:
        base["max_drawdown_pct"] = 0.0
        base["cagr_pct"] = 0.0
        base["total_return_pct"] = 0.0
        return base

    peak = ec["equity_gbp"].cummax()
    dd_pct = (ec["equity_gbp"] - peak) / starting_capital * 100.0
    max_dd = float(dd_pct.min())

    start_eq = float(ec["equity_gbp"].iloc[0])
    end_eq = float(ec["equity_gbp"].iloc[-1])
    days = (ec["date"].iloc[-1] - ec["date"].iloc[0]).days
    years = days / 365.25 if days > 0 else 0.0

    total_return_pct = (end_eq - starting_capital) / starting_capital * 100.0
    cagr = ((end_eq / starting_capital) ** (1.0 / years) - 1.0) * 100.0 if years > 0 and end_eq > 0 else 0.0

    base["max_drawdown_pct"] = round(max_dd, 2)
    base["cagr_pct"] = round(cagr, 2)
    base["total_return_pct"] = round(total_return_pct, 2)
    return base


def full_metrics_fixed(trades: list[dict], starting_capital: float) -> dict:
    return {"raw": compute_metrics_fixed(trades, starting_capital),
            "ex_best": compute_metrics_fixed(ex_best_trades(trades), starting_capital)}


def walk_forward_pf(trades: list[dict], window_months: int = 6, step_months: int = 1) -> dict:
    if not trades:
        return {"n_total": 0, "n_positive": 0, "success_rate_pct": 0.0}
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df = df.sort_values("entry_date")

    min_date, max_date = df["entry_date"].min(), df["entry_date"].max()
    current = min_date.to_period("M")
    end_p = max_date.to_period("M")
    windows = []
    while current <= end_p:
        win_start = current.to_timestamp()
        win_end = (current + window_months).to_timestamp() - pd.Timedelta(days=1)
        sub = df[(df["entry_date"] >= win_start) & (df["entry_date"] <= win_end)]
        if len(sub) >= 3:
            m = compute_metrics(sub)
            windows.append(m.get("profit_factor", 0.0))
        current = current + step_months

    n_total = len(windows)
    n_positive = sum(1 for pf in windows if pf > 1.0)
    success_rate_pct = round(n_positive / n_total * 100, 1) if n_total else 0.0
    return {"n_total": n_total, "n_positive": n_positive, "success_rate_pct": success_rate_pct}
