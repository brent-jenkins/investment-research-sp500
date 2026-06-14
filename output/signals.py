"""Persist and retrieve generated signals from the database."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import Signal, get_engine, get_session, init_db
from data.ingestion import get_all_tickers
from analysis.confluence import generate_signal


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def run_all_signals(
    tickers: Optional[list[str]] = None,
    as_of: Optional[datetime] = None,
) -> list[dict]:
    """Generate and persist signals for all (or specified) tickers.

    Args:
        tickers: Symbols to score. Defaults to the full watchlist.
        as_of: Timestamp to record against each signal. Defaults to now.

    Returns:
        List of signal dicts, one per ticker.
    """
    init_db()
    config = _load_config()

    if tickers is None:
        tickers = get_all_tickers(config)
    if as_of is None:
        as_of = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    engine = get_engine()
    results = []

    for ticker in tickers:
        result = generate_signal(ticker, config=config)
        result["date"] = as_of

        stmt = sqlite_insert(Signal).values(
            ticker=result["ticker"],
            date=as_of,
            regime_score=result["regime_score"],
            trend_score=result["trend_score"],
            mean_reversion_score=result["mean_reversion_score"],
            volatility_score=result["volatility_score"],
            volume_score=result["volume_score"],
            macro_score=result["macro_score"],
            confluence_score=result["confluence_score"],
            signal=result["signal"],
            strength=result["strength"],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "date"],
            set_={
                "regime_score": stmt.excluded.regime_score,
                "trend_score": stmt.excluded.trend_score,
                "mean_reversion_score": stmt.excluded.mean_reversion_score,
                "volatility_score": stmt.excluded.volatility_score,
                "volume_score": stmt.excluded.volume_score,
                "macro_score": stmt.excluded.macro_score,
                "confluence_score": stmt.excluded.confluence_score,
                "signal": stmt.excluded.signal,
                "strength": stmt.excluded.strength,
            },
        )
        with engine.begin() as conn:
            conn.execute(stmt)

        results.append(result)

    return results


def get_latest_signals(tickers: Optional[list[str]] = None) -> pd.DataFrame:
    """Retrieve the most recent signal for each ticker.

    Returns a DataFrame with one row per ticker, sorted by confluence_score descending.
    """
    config = _load_config()
    if tickers is None:
        tickers = get_all_tickers(config)

    session = get_session()
    try:
        rows = []
        for ticker in tickers:
            row = (
                session.query(Signal)
                .filter(Signal.ticker == ticker)
                .order_by(Signal.date.desc())
                .first()
            )
            if row:
                rows.append(
                    {
                        "ticker": row.ticker,
                        "date": row.date,
                        "signal": row.signal,
                        "confluence_score": row.confluence_score,
                        "strength": row.strength,
                        "regime": row.regime_score,
                        "trend": row.trend_score,
                        "mean_reversion": row.mean_reversion_score,
                        "volatility": row.volatility_score,
                        "volume": row.volume_score,
                        "macro": row.macro_score,
                    }
                )
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        return df.sort_values("confluence_score", ascending=False).reset_index(drop=True)
    finally:
        session.close()
