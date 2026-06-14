"""Compute and persist rolling performance metrics per asset."""

import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import Performance, get_engine, get_session, init_db
from data.ingestion import get_all_tickers, get_price_df


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _sharpe(returns: pd.Series, periods: int = 252) -> float:
    """Annualised Sharpe ratio (assumes risk-free rate ≈ 0 for simplicity)."""
    if returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods))


def _sortino(returns: pd.Series, periods: int = 252) -> float:
    """Annualised Sortino ratio."""
    downside = returns[returns < 0]
    if downside.std() == 0:
        return 0.0
    return float(returns.mean() / downside.std() * np.sqrt(periods))


def _max_drawdown(cum_returns: pd.Series) -> float:
    """Maximum drawdown from peak as a negative fraction."""
    rolling_max = cum_returns.cummax()
    drawdown = (cum_returns - rolling_max) / rolling_max.replace(0, np.nan)
    return float(drawdown.min())


def compute_performance(ticker: str, days: int = 365) -> Optional[pd.DataFrame]:
    """Compute daily rolling performance metrics for a single ticker.

    Args:
        ticker: Asset to compute metrics for.
        days: Calendar days of history to use.

    Returns:
        DataFrame with performance columns or None if insufficient data.
    """
    df = get_price_df(ticker, days=days)
    if df.empty or len(df) < 30:
        return None

    df = df.sort_values("date").reset_index(drop=True)
    df["daily_return"] = df["adj_close"].pct_change()
    df["cumulative_return"] = (1 + df["daily_return"]).cumprod() - 1

    rolling_max = (1 + df["daily_return"]).cumprod().cummax()
    cum_value = (1 + df["daily_return"]).cumprod()
    df["drawdown"] = (cum_value - rolling_max) / rolling_max

    results = []
    window = 252
    for i in range(len(df)):
        start_i = max(0, i - window + 1)
        window_returns = df["daily_return"].iloc[start_i : i + 1].dropna()
        cum_slice = (1 + window_returns).cumprod()

        sharpe = _sharpe(window_returns) if len(window_returns) > 20 else None
        sortino = _sortino(window_returns) if len(window_returns) > 20 else None
        mdd = _max_drawdown(cum_slice) if len(cum_slice) > 1 else None
        calmar = (
            float(window_returns.mean() * 252 / abs(mdd))
            if mdd and mdd != 0
            else None
        )

        results.append(
            {
                "ticker": ticker,
                "date": df["date"].iloc[i],
                "daily_return": df["daily_return"].iloc[i],
                "cumulative_return": df["cumulative_return"].iloc[i],
                "drawdown": df["drawdown"].iloc[i],
                "max_drawdown": mdd,
                "sharpe_ratio": sharpe,
                "sortino_ratio": sortino,
                "calmar_ratio": calmar,
                "win_rate": None,
                "avg_win": None,
                "avg_loss": None,
                "profit_factor": None,
            }
        )

    return pd.DataFrame(results)


def persist_performance(ticker: str, days: int = 365) -> int:
    """Compute and upsert performance metrics for a ticker into the database.

    Returns the number of rows written.
    """
    init_db()
    perf_df = compute_performance(ticker, days=days)
    if perf_df is None or perf_df.empty:
        return 0

    engine = get_engine()
    rows = perf_df.where(pd.notnull(perf_df), None).to_dict(orient="records")

    stmt = sqlite_insert(Performance).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "date"],
        set_={col: getattr(stmt.excluded, col) for col in perf_df.columns if col not in ("ticker", "date")},
    )
    with engine.begin() as conn:
        conn.execute(stmt)

    return len(rows)


def get_performance_summary(tickers: Optional[list[str]] = None) -> pd.DataFrame:
    """Return the most recent performance row for each ticker as a summary table."""
    config = _load_config()
    if tickers is None:
        tickers = get_all_tickers(config)

    session = get_session()
    try:
        rows = []
        for ticker in tickers:
            row = (
                session.query(Performance)
                .filter(Performance.ticker == ticker)
                .order_by(Performance.date.desc())
                .first()
            )
            if row:
                rows.append(
                    {
                        "ticker": ticker,
                        "date": row.date,
                        "cumulative_return": row.cumulative_return,
                        "max_drawdown": row.max_drawdown,
                        "sharpe_ratio": row.sharpe_ratio,
                        "sortino_ratio": row.sortino_ratio,
                    }
                )
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    finally:
        session.close()
