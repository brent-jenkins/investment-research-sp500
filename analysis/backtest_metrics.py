"""Backtest analysis: metrics, period breakdown, module attribution, signal decay,
walk-forward validation, and equity curve construction.

All functions accept a trades DataFrame as returned by backtest.get_run_trades().
"""

from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Predefined analysis periods
# ---------------------------------------------------------------------------

ANALYSIS_PERIODS = {
    "COVID Crash": {
        "start": datetime(2020, 2, 15),
        "end":   datetime(2020, 5, 1),
        "colour": "#ef4444",
        "desc":  "Feb–May 2020 — fastest bear market in history, −34% in 33 days",
    },
    "2022 Bear Market": {
        "start": datetime(2022, 1, 1),
        "end":   datetime(2022, 12, 31),
        "colour": "#f97316",
        "desc":  "Full-year 2022 — Fed rate hikes drove S&P 500 down −19.4%",
    },
    "2023–24 Recovery": {
        "start": datetime(2023, 1, 1),
        "end":   datetime(2024, 12, 31),
        "colour": "#22c55e",
        "desc":  "Two-year rally — S&P 500 up +53% from Jan 2023 trough",
    },
}

MODULE_LABELS = {
    "trend_at_entry":           "Trend",
    "mean_reversion_at_entry":  "Mean Reversion",
    "volatility_at_entry":      "Volatility",
    "volume_at_entry":          "Volume",
    "macro_at_entry":           "Macro",
}


# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------

def compute_metrics(df: pd.DataFrame) -> dict:
    """Compute full performance metrics from a trades DataFrame.

    Args:
        df: DataFrame with columns pnl_pct, is_win, holding_days, entry_date.
            An optional pnl_gbp column (VaR-scaled £ P&L — see
            analysis/backtest.py:simulate_trades() and
            analysis/volatility.py:compute_var_position_size()) adds a
            parallel set of "_gbp"-suffixed keys to the returned dict below;
            all existing "_pct"-suffixed keys are always computed from
            pnl_pct exactly as before, so callers that only know about the
            percentage-based keys (older backtest_trades rows have no
            pnl_gbp at all) see no change in behaviour.

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
        # Approximate: annualise based on average holding period
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

    # ── £ P&L metrics (additive — only when pnl_gbp is present and populated) ──
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


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------

def equity_curve(df: pd.DataFrame, start_value: float = 1.0) -> pd.DataFrame:
    """Return a daily portfolio equity curve DataFrame.

    A trade's P&L is realised on its exit_date. Sorting trades by
    entry_date (the old behaviour) while plotting exit_date produced
    backwards jumps in the time axis whenever trades of different holding
    periods overlapped — a trade entered first but held longest could
    still exit after a later-entered, shorter trade. Trades are now
    compounded in exit_date order (the order P&L actually lands in a real
    account), then expanded into one row per calendar day from the first
    trade's entry_date to the last trade's exit_date, so the resulting
    date axis is always monotonically increasing with no gaps. Days with
    no trade exit (including weekends) carry forward the previous day's
    equity value; days before any trade has exited hold at start_value.

    Args:
        df:           Trades DataFrame (must have entry_date, exit_date — or
                      entry_date alone as a fallback — and pnl_pct).
        start_value:  Starting portfolio value (default 1.0 = normalised).

    Returns:
        DataFrame with columns: date (daily, no gaps), equity, drawdown_pct.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "equity", "drawdown_pct"])

    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.normalize()
    exit_col = "exit_date" if "exit_date" in df.columns else "entry_date"
    df["exit_date"] = pd.to_datetime(df[exit_col]).dt.normalize()

    # Realise P&L in exit-date order — the order it actually compounds in a
    # real account — not entry-date order.
    df = df.sort_values("exit_date").reset_index(drop=True)
    cum_at_exit = (1 + df["pnl_pct"]).cumprod() * start_value

    # Multiple trades can exit on the same day; keep the last (fully
    # compounded) value for that date.
    exit_equity = pd.Series(cum_at_exit.values, index=df["exit_date"])
    exit_equity = exit_equity.groupby(level=0).last()

    all_days = pd.date_range(df["entry_date"].min(), df["exit_date"].max(), freq="D")
    equity = exit_equity.reindex(all_days).ffill().fillna(start_value)

    peak = equity.cummax()
    dd   = (equity - peak) / peak * 100

    return pd.DataFrame({
        "date":         all_days,
        "equity":       equity.values,
        "drawdown_pct": dd.values,
    })


def equity_curve_gbp(df: pd.DataFrame, starting_capital_gbp: float = 10000.0) -> pd.DataFrame:
    """Return a daily £ portfolio equity curve DataFrame from VaR-sized trades.

    A true £ equity curve, unlike equity_curve() above — that function
    compounds fractional pnl_pct returns multiplicatively ((1+r).cumprod()),
    which implicitly assumes 100% reinvestment of the whole account into
    every trade. With VaR position sizing, each trade only ever risks a
    small slice of the account (position_gbp), so £ P&L instead ADDS onto a
    starting capital baseline: equity_t = starting_capital_gbp +
    cumsum(pnl_gbp), realised in exit-date order exactly like equity_curve()
    above. Returns an empty-columns DataFrame (not a curve pinned at
    starting_capital_gbp) when df has no usable pnl_gbp, so callers can tell
    "no £-sized trades in this run" apart from "a flat £ curve".

    Args:
        df:                    Trades DataFrame — must have entry_date,
                               exit_date (or entry_date as a fallback), and
                               pnl_gbp.
        starting_capital_gbp:  Notional starting account value — see
                               backtest.starting_capital_gbp in config.yaml
                               and CLAUDE.md's "VaR position sizing" section.

    Returns:
        DataFrame with columns: date (daily, no gaps), equity_gbp, drawdown_gbp.
    """
    if df is None or df.empty or "pnl_gbp" not in df.columns or df["pnl_gbp"].isna().all():
        return pd.DataFrame(columns=["date", "equity_gbp", "drawdown_gbp"])

    df = df.dropna(subset=["pnl_gbp"]).copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.normalize()
    exit_col = "exit_date" if "exit_date" in df.columns else "entry_date"
    df["exit_date"] = pd.to_datetime(df[exit_col]).dt.normalize()

    # Realise P&L in exit-date order, same rationale as equity_curve() above.
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
# Period breakdown
# ---------------------------------------------------------------------------

def period_breakdown(df: pd.DataFrame, periods: Optional[dict] = None) -> dict:
    """Split trades by predefined time periods and compute metrics for each.

    Returns:
        Dict mapping period_name → metrics dict (plus 'n_trades').
    """
    if periods is None:
        periods = ANALYSIS_PERIODS

    if df is None or df.empty:
        return {name: {"total_trades": 0} for name in periods}

    results = {}
    for name, spec in periods.items():
        start = pd.Timestamp(spec["start"])
        end   = pd.Timestamp(spec["end"])
        mask  = (df["entry_date"] >= start) & (df["entry_date"] <= end)
        sub   = df[mask].copy()
        m     = compute_metrics(sub)
        m["colour"] = spec.get("colour", "#6b7280")
        m["desc"]   = spec.get("desc", "")
        results[name] = m

    return results


# ---------------------------------------------------------------------------
# Module attribution
# ---------------------------------------------------------------------------

def module_attribution(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-module hit rates: how often each module agreed with the winning direction.

    For each module score at entry (-1, 0, +1):
      - For LONG trades: score = +1 is "in agreement"
      - For SHORT trades: score = -1 is "in agreement"

    Returns:
        DataFrame with index = module label and columns:
            agreement_win_rate, avg_score_wins, avg_score_losses, n_non_neutral
    """
    if df is None or df.empty:
        return pd.DataFrame()

    rows = []
    for col, label in MODULE_LABELS.items():
        if col not in df.columns:
            continue
        sub = df.dropna(subset=[col]).copy()
        if sub.empty:
            continue

        # "Agreement" = score direction matches trade direction
        def agrees(row):
            score = row[col]
            if row["direction"] == "LONG":
                return score == 1
            else:
                return score == -1

        sub["agrees"] = sub.apply(agrees, axis=1)
        wins   = sub[sub["is_win"] == True]
        losses = sub[sub["is_win"] == False]

        rows.append({
            "module":                label,
            "win_rate_when_agree":   round(float(sub[sub["agrees"]]["is_win"].mean()) * 100, 1)  if sub["agrees"].any()  else 0.0,
            "win_rate_when_disagree":round(float(sub[~sub["agrees"]]["is_win"].mean()) * 100, 1) if (~sub["agrees"]).any() else 0.0,
            "avg_score_wins":        round(float(wins[col].mean()), 2)   if not wins.empty   else 0.0,
            "avg_score_losses":      round(float(losses[col].mean()), 2) if not losses.empty else 0.0,
            "agreement_pct":         round(float(sub["agrees"].mean()) * 100, 1),
            "n_trades":              len(sub),
        })

    return pd.DataFrame(rows).set_index("module") if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Signal decay (holding period analysis)
# ---------------------------------------------------------------------------

def signal_decay(df: pd.DataFrame) -> dict:
    """Analyse how long positions are held and how exit reason distributes.

    Returns:
        Dict with:
            median_hold, mean_hold, pct_stop_loss, pct_signal_reversal,
            pct_max_holding, hold_bins (for histogram), regime_holds
    """
    if df is None or df.empty:
        return {}

    holds = df["holding_days"].dropna()
    exits = df["exit_reason"].value_counts(normalize=True) * 100 if "exit_reason" in df.columns else pd.Series()

    # Holding period histogram (bins of 5 days)
    bins  = list(range(0, int(holds.max()) + 10, 5)) if not holds.empty else []
    hist, edges = np.histogram(holds, bins=bins) if bins else (np.array([]), np.array([]))
    hold_bins = [
        {"bin": f"{int(edges[i])}–{int(edges[i+1])}d", "count": int(hist[i])}
        for i in range(len(hist))
    ] if len(hist) > 0 else []

    # Average hold by regime
    regime_holds = {}
    if "regime_at_entry" in df.columns:
        rg = df.groupby("regime_at_entry")["holding_days"].mean().round(1)
        regime_holds = rg.to_dict()

    return {
        "median_hold":          float(holds.median()) if not holds.empty else 0,
        "mean_hold":            float(holds.mean())   if not holds.empty else 0,
        "pct_stop_loss":        round(float(exits.get("STOP_LOSS", 0)), 1),
        "pct_signal_reversal":  round(float(exits.get("SIGNAL_REVERSAL", 0)), 1),
        "pct_max_holding":      round(float(exits.get("MAX_HOLDING", 0)), 1),
        "pct_end_of_data":      round(float(exits.get("END_OF_DATA", 0)), 1),
        "hold_bins":            hold_bins,
        "regime_holds":         regime_holds,
    }


def exit_reason_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate trade performance by exit reason.

    Returns:
        DataFrame indexed by exit_reason with columns:
            trades, win_rate (pct, e.g. 62.5), avg_return (pct, e.g. 1.23),
            avg_hold (days), avg_stop_distance_pct (pct, e.g. 8.4 — only
            present when df has a stop_distance_pct column; older
            backtest_trades rows don't have one, so this key is simply
            omitted from each row dict rather than filled with a misleading
            0.0 — the same "additive, don't reinterpret missing data as
            zero" pattern compute_metrics() uses for its pnl_gbp-derived
            keys).

    Comparing avg_stop_distance_pct for STOP_LOSS rows against other exit
    reasons shows whether the ATR stop is calibrated sensibly — a STOP_LOSS
    average distance far wider or narrower than what MAX_HOLDING/
    SIGNAL_REVERSAL trades typically move by is a signal worth checking
    backtest.atr_stop_multiplier against.
    """
    if df is None or df.empty or "exit_reason" not in df.columns:
        return pd.DataFrame()

    has_stop_distance = "stop_distance_pct" in df.columns and df["stop_distance_pct"].notna().any()

    rows = []
    for reason, sub in df.groupby("exit_reason"):
        row = {
            "exit_reason": reason,
            "trades":      len(sub),
            "win_rate":    round(float(sub["is_win"].mean()) * 100, 1)   if "is_win" in sub.columns      else 0.0,
            "avg_return":  round(float(sub["pnl_pct"].mean()) * 100, 2)  if "pnl_pct" in sub.columns      else 0.0,
            "avg_hold":    round(float(sub["holding_days"].mean()), 1)   if "holding_days" in sub.columns else 0.0,
        }
        if has_stop_distance:
            row["avg_stop_distance_pct"] = round(float(sub["stop_distance_pct"].mean()), 2)
        rows.append(row)

    return pd.DataFrame(rows).set_index("exit_reason") if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Walk-forward validation
# ---------------------------------------------------------------------------

def walk_forward(df: pd.DataFrame, window_months: int = 6, step_months: int = 1) -> list[dict]:
    """Compute rolling performance metrics over time.

    Slides a window of *window_months* forward in *step_months* increments,
    computing win_rate, avg_return, and n_trades for each window.  This shows
    whether signal edge is consistent or concentrated in specific periods.

    Args:
        df:             Trades DataFrame.
        window_months:  Size of each window in months (default 6).
        step_months:    Step size between windows in months (default 1).

    Returns:
        List of dicts: {window_start, window_end, win_rate, avg_return_pct, n_trades,
                        sharpe_ratio, max_drawdown_pct}
    """
    if df is None or df.empty or "entry_date" not in df.columns:
        return []

    df = df.sort_values("entry_date").copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])

    min_date = df["entry_date"].min()
    max_date = df["entry_date"].max()

    results = []
    current = min_date.to_period("M")
    end_p   = max_date.to_period("M")

    while current <= end_p:
        win_start = current.to_timestamp()
        win_end   = (current + window_months).to_timestamp() - pd.Timedelta(days=1)
        mask = (df["entry_date"] >= win_start) & (df["entry_date"] <= win_end)
        sub  = df[mask]

        if len(sub) >= 3:
            m = compute_metrics(sub)
            results.append({
                "window_start":     win_start.strftime("%Y-%m"),
                "window_end":       win_end.strftime("%Y-%m"),
                "win_rate":         m["win_rate"],
                "avg_return_pct":   m["avg_return_pct"],
                "n_trades":         m["total_trades"],
                "sharpe_ratio":     m["sharpe_ratio"],
                "max_drawdown_pct": m["max_drawdown_pct"],
            })

        current = current + step_months

    return results


# ---------------------------------------------------------------------------
# Per-regime performance
# ---------------------------------------------------------------------------

def regime_performance(df: pd.DataFrame) -> pd.DataFrame:
    """Win rate and average return broken down by regime at entry."""
    if df is None or df.empty or "regime_at_entry" not in df.columns:
        return pd.DataFrame()

    rows = []
    for regime, sub in df.groupby("regime_at_entry"):
        m = compute_metrics(sub)
        rows.append({
            "regime":           regime,
            "n_trades":         m["total_trades"],
            "win_rate_pct":     round(m["win_rate"] * 100, 1),
            "avg_return_pct":   round(m["avg_return_pct"], 2),
            "max_drawdown_pct": round(m["max_drawdown_pct"], 2),
            "sharpe_ratio":     round(m["sharpe_ratio"], 3),
        })

    return pd.DataFrame(rows).set_index("regime") if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Forex holding-period sensitivity
# ---------------------------------------------------------------------------

def forex_holding_sensitivity(
    df: pd.DataFrame,
    thresholds: list[int] | None = None,
) -> list[dict]:
    """Compute profit factor and win rate for forex trades at different holding caps.

    Filters to trades whose ticker ends with '=X' (forex pairs), then for each
    *threshold* shows the performance of trades that closed within that many days.
    This approximates the effect of capping the max holding period: trades already
    exiting via stop-loss or signal reversal within the threshold are unaffected;
    max-hold trades that ran past the threshold are excluded (their intermediate
    P&L is unavailable without re-running the full backtest).

    Args:
        df:         Trades DataFrame from get_run_trades().
        thresholds: List of day caps to evaluate.  Defaults to [1, 3, 5, 7, 10, 14, 30].

    Returns:
        List of dicts: {threshold, n_trades, profit_factor, win_rate_pct, avg_return_pct}
    """
    if df is None or df.empty:
        return []

    if thresholds is None:
        thresholds = [1, 3, 5, 7, 10, 14, 30]

    forex = df[df["ticker"].str.endswith("=X", na=False)].copy()
    if forex.empty:
        return []

    results = []
    for t in thresholds:
        sub = forex[forex["holding_days"] <= t]
        n   = len(sub)
        if n == 0:
            results.append({
                "threshold":       t,
                "n_trades":        0,
                "profit_factor":   None,
                "win_rate_pct":    0.0,
                "avg_return_pct":  0.0,
            })
            continue

        wins   = sub[sub["is_win"] == True]["pnl_pct"]
        losses = sub[sub["is_win"] == False]["pnl_pct"]
        if not losses.empty and losses.sum() != 0:
            pf = round(float(wins.sum() / abs(losses.sum())), 2)
        elif wins.empty:
            pf = 0.0
        else:
            pf = None  # no losses → infinite profit factor

        results.append({
            "threshold":      t,
            "n_trades":       n,
            "profit_factor":  pf,
            "win_rate_pct":   round(float(len(wins) / n * 100), 1),
            "avg_return_pct": round(float(sub["pnl_pct"].mean() * 100), 2),
        })

    return results
