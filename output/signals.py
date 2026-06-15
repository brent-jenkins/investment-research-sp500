"""Signal accuracy analysis: win rates, profit factor, and breakdowns."""

from typing import Optional

import pandas as pd


def _closed(trades: list[dict]) -> list[dict]:
    return [t for t in trades if not t.get("is_open") and t.get("pnl") is not None]


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def accuracy_summary(trades: list[dict]) -> dict:
    """Overall performance statistics across all closed trades."""
    closed = _closed(trades)
    if not closed:
        return {}

    pnls     = [float(t["pnl"]) for t in closed]
    pnl_pcts = [float(t["pnl_pct"]) for t in closed if t.get("pnl_pct") is not None]
    winners  = [p for p in pnls if p > 0]
    losers   = [p for p in pnls if p < 0]

    gross_profit  = sum(winners)
    gross_loss    = abs(sum(losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "total_trades":  len(closed),
        "winners":       len(winners),
        "losers":        len(losers),
        "win_rate":      len(winners) / len(closed),
        "avg_win_pct":   sum(p for p in pnl_pcts if p > 0) / len([p for p in pnl_pcts if p > 0]) if any(p > 0 for p in pnl_pcts) else 0.0,
        "avg_loss_pct":  sum(p for p in pnl_pcts if p < 0) / len([p for p in pnl_pcts if p < 0]) if any(p < 0 for p in pnl_pcts) else 0.0,
        "gross_profit":  gross_profit,
        "gross_loss":    gross_loss,
        "profit_factor": profit_factor,
        "best_trade":    max(pnls),
        "worst_trade":   min(pnls),
        "total_pnl":     sum(pnls),
    }


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------

_SIGNAL_ORDER = ["STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"]


def accuracy_by_signal_type(trades: list[dict]) -> pd.DataFrame:
    """Win rate and average P&L broken down by signal classification."""
    closed = _closed(trades)
    if not closed:
        return pd.DataFrame()

    df = pd.DataFrame(closed)
    if "signal_type" not in df.columns or df["signal_type"].isna().all():
        return pd.DataFrame()

    rows = []
    for sig_type, group in df.groupby("signal_type", dropna=True):
        pnls = group["pnl"].dropna().astype(float)
        wins = int((pnls > 0).sum())
        rows.append({
            "Signal":    str(sig_type).replace("_", " "),
            "_key":      str(sig_type),
            "Trades":    len(group),
            "Wins":      wins,
            "Win Rate":  wins / len(group) if group.shape[0] else 0.0,
            "Avg P&L":   float(pnls.mean()) if len(pnls) else 0.0,
            "Total P&L": float(pnls.sum())  if len(pnls) else 0.0,
        })

    result = pd.DataFrame(rows)
    result["_ord"] = result["_key"].map(
        lambda x: _SIGNAL_ORDER.index(x) if x in _SIGNAL_ORDER else 99
    )
    return (
        result.sort_values("_ord")
        .drop(columns=["_key", "_ord"])
        .reset_index(drop=True)
    )


def accuracy_by_regime(trades: list[dict]) -> pd.DataFrame:
    """Win rate and average P&L broken down by market regime."""
    closed = _closed(trades)
    if not closed:
        return pd.DataFrame()

    df = pd.DataFrame(closed)
    if "regime" not in df.columns or df["regime"].isna().all():
        return pd.DataFrame()

    rows = []
    for regime, group in df.groupby("regime", dropna=True):
        pnls = group["pnl"].dropna().astype(float)
        wins = int((pnls > 0).sum())
        rows.append({
            "Regime":    str(regime).replace("_", " "),
            "Trades":    len(group),
            "Wins":      wins,
            "Win Rate":  wins / len(group) if group.shape[0] else 0.0,
            "Avg P&L":   float(pnls.mean()) if len(pnls) else 0.0,
            "Total P&L": float(pnls.sum())  if len(pnls) else 0.0,
        })

    return (
        pd.DataFrame(rows)
        .sort_values("Win Rate", ascending=False)
        .reset_index(drop=True)
    )


def accuracy_by_indicator(trades: list[dict]) -> pd.DataFrame:
    """Per-module hit rate: how often each indicator's directional call was correct."""
    closed = _closed(trades)
    if not closed:
        return pd.DataFrame()

    modules = {
        "trend":          "Trend",
        "mean_reversion": "Mean Reversion",
        "volatility":     "Volatility",
        "volume":         "Volume",
        "macro":          "Macro",
    }

    rows = []
    for key, label in modules.items():
        score_col = f"{key}_score"
        relevant  = [t for t in closed if t.get(score_col) is not None]
        if not relevant:
            continue

        bullish = [t for t in relevant if int(t.get(score_col, 0)) > 0]
        bearish = [t for t in relevant if int(t.get(score_col, 0)) < 0]
        neutral = [t for t in relevant if int(t.get(score_col, 0)) == 0]

        b_correct = [t for t in bullish if float(t.get("pnl", 0)) > 0]
        s_correct = [t for t in bearish if float(t.get("pnl", 0)) < 0]

        total   = len(bullish) + len(bearish)
        correct = len(b_correct) + len(s_correct)

        rows.append({
            "Indicator":       label,
            "Bullish Calls":   len(bullish),
            "Bullish Correct": len(b_correct),
            "Bearish Calls":   len(bearish),
            "Bearish Correct": len(s_correct),
            "Neutral":         len(neutral),
            "Hit Rate":        correct / total if total > 0 else None,
        })

    return pd.DataFrame(rows)


def accuracy_by_confluence_band(trades: list[dict]) -> pd.DataFrame:
    """Win rate broken down by |confluence_score| band at entry (0.30–0.45, 0.45–0.60, 0.60+)."""
    closed = _closed(trades)
    if not closed:
        return pd.DataFrame()

    bands = [
        (0.30, 0.45, "Weak (0.30–0.45)"),
        (0.45, 0.60, "Moderate (0.45–0.60)"),
        (0.60, 1.00, "Strong (0.60+)"),
    ]

    rows = []
    for lo, hi, label in bands:
        group = [
            t for t in closed
            if t.get("confluence_score") is not None
            and lo <= abs(float(t["confluence_score"])) < hi
        ]
        # Include the upper boundary for the top band
        if hi == 1.00:
            group = [
                t for t in closed
                if t.get("confluence_score") is not None
                and lo <= abs(float(t["confluence_score"])) <= hi
            ]
        if not group:
            continue
        pnls = [float(t["pnl"]) for t in group]
        wins = sum(1 for p in pnls if p > 0)
        rows.append({
            "Band":      label,
            "Trades":    len(group),
            "Wins":      wins,
            "Win Rate":  wins / len(group),
            "Avg P&L":   sum(pnls) / len(pnls),
            "Total P&L": sum(pnls),
        })

    return pd.DataFrame(rows).reset_index(drop=True)


def accuracy_by_asset_class(trades: list[dict], ticker_to_group: dict[str, str]) -> pd.DataFrame:
    """Win rate broken down by watchlist asset class group."""
    closed = _closed(trades)
    if not closed:
        return pd.DataFrame()

    from collections import defaultdict
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in closed:
        group = ticker_to_group.get(t.get("ticker", ""), "Other")
        groups[group].append(t)

    rows = []
    for group, group_trades in sorted(groups.items()):
        pnls = [float(t["pnl"]) for t in group_trades]
        wins = sum(1 for p in pnls if p > 0)
        rows.append({
            "Asset Class": group.replace("_", " ").title(),
            "_group_key":  group,
            "Trades":      len(group_trades),
            "Wins":        wins,
            "Win Rate":    wins / len(group_trades),
            "Avg P&L":     sum(pnls) / len(pnls),
            "Total P&L":   sum(pnls),
        })

    return (
        pd.DataFrame(rows)
        .sort_values("Win Rate", ascending=False)
        .drop(columns=["_group_key"])
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Signal validation (practice → live readiness)
# ---------------------------------------------------------------------------

def signal_validation(practice_trades: list[dict]) -> dict:
    """Analyse how well confluence signals predicted outcomes in practice trades.

    Args:
        practice_trades: All practice trades (open and closed); the function
                         filters to closed directional ones internally.

    Returns:
        Dict with accuracy stats and a confidence level + recommendation string.
        ``confidence_level`` is one of: ``insufficient_data``, ``strong``,
        ``moderate``, ``weak``.  ``accuracy`` is None when there is
        insufficient data.
    """
    MIN_TRADES = 20
    closed = _closed(practice_trades)
    directional = [
        t for t in closed
        if t.get("signal_type") and t.get("signal_type") not in ("NEUTRAL", "")
    ]

    if not directional:
        return {
            "total_closed":     len(closed),
            "directional":      0,
            "correct":          0,
            "accuracy":         None,
            "confidence_level": "insufficient_data",
            "recommendation":   None,
            "min_trades":       MIN_TRADES,
        }

    correct  = [t for t in directional if float(t.get("pnl", 0)) > 0]
    accuracy = len(correct) / len(directional)

    if len(directional) < MIN_TRADES:
        confidence_level = "insufficient_data"
        recommendation   = None
    elif accuracy >= 0.60:
        confidence_level = "strong"
        recommendation   = (
            "Signal accuracy exceeds 60% across practice trades — "
            "consider opening a live position."
        )
    elif accuracy >= 0.50:
        confidence_level = "moderate"
        recommendation   = (
            "Accuracy is above 50% — continue building the practice track record "
            "before committing live capital."
        )
    else:
        confidence_level = "weak"
        recommendation   = (
            "Signals are underperforming vs random — review strategy before going live."
        )

    return {
        "total_closed":     len(closed),
        "directional":      len(directional),
        "correct":          len(correct),
        "accuracy":         accuracy,
        "confidence_level": confidence_level,
        "recommendation":   recommendation,
        "min_trades":       MIN_TRADES,
    }


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------

def equity_curve(trades: list[dict]) -> pd.DataFrame:
    """Cumulative P&L over time from closed trades, sorted by exit date."""
    closed = [
        t for t in trades
        if not t.get("is_open")
        and t.get("exit_date") is not None
        and t.get("pnl") is not None
    ]
    if not closed:
        return pd.DataFrame()

    df = pd.DataFrame(closed)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df = df.sort_values("exit_date").reset_index(drop=True)
    df["cumulative_pnl"] = df["pnl"].astype(float).cumsum()
    return df[["exit_date", "ticker", "pnl", "pnl_pct", "cumulative_pnl"]]
