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

    return {
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
    }


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------

def equity_curve(df: pd.DataFrame, start_value: float = 1.0) -> pd.DataFrame:
    """Return a dated equity curve DataFrame.

    Args:
        df:           Trades DataFrame (must have entry_date and pnl_pct).
        start_value:  Starting portfolio value (default 1.0 = normalised).

    Returns:
        DataFrame with columns: date, equity, drawdown_pct.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "equity", "drawdown_pct"])

    df = df.sort_values("entry_date").reset_index(drop=True)
    cum = (1 + df["pnl_pct"]).cumprod() * start_value
    peak = cum.cummax()
    dd   = (cum - peak) / peak * 100

    return pd.DataFrame({
        "date":         df["exit_date"] if "exit_date" in df.columns else df["entry_date"],
        "equity":       cum.values,
        "drawdown_pct": dd.values,
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
