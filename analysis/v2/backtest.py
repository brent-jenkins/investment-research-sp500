"""V2 backtest engine — reuses V1's already-proven execution mechanics
(ATR stop-loss, VaR position sizing, per-ticker max holding) unchanged;
only the entry/exit *decision* logic (signal, membership, crisis
override) is new. This is the module the reproducibility check runs
against to confirm the production implementation matches what the
research clone validated.
"""
import logging
from datetime import date as date_type
from typing import Optional

import numpy as np
import pandas as pd

from analysis.backtest import _atr, _resolve_entry_stop
from analysis.backtest_metrics import compute_metrics, equity_curve_gbp
from analysis.regime import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _var_position_size_core
from data.ingestion import get_price_df

from analysis.v2 import crisis as v2_crisis
from analysis.v2 import signals as v2_signals
from analysis.v2 import universe as v2_universe

logger = logging.getLogger(__name__)


def precompute_ticker(ticker: str, config: dict) -> Optional[dict]:
    """Load a ticker's full available price history and compute its ATR
    and V2 momentum signal. Membership is looked up per-date at simulation
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
    sig = v2_signals.compute_signal_series(close)

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
    per-ticker max-holding backstop. Position sizing is the shared,
    unmodified VaR formula every prior V1/research path has used."""
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

    raw_crisis = v2_crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
    entry_blocked = v2_crisis.extend_trigger(raw_crisis).reindex(frame.index).ffill().fillna(False)

    trades: list[dict] = []
    open_trade = None

    for dt, row in frame.iterrows():
        price = float(row["close"])
        atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
        sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
        is_member = is_etf or v2_universe.is_member(ticker, dt.date() if hasattr(dt, "date") else dt)
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
        is_etf = ticker in v2_universe.ETF_TICKERS
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
    """Additive-GBP, exit-date-sorted drawdown/CAGR — round 10's corrected
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
