"""Extended precompute: production precompute_ticker() plus the two columns
the trailing variants need -- `high` (Chandelier's highest-high-since-entry)
and `ma14` (T3's trend-conditional cap release).

Deliberately built by CALLING the production precompute_ticker() and adding
columns to its result, never by reimplementing the ATR/signal/momentum
computation. The added columns are additive and ignored by every existing
reader, exactly as `momentum` (2026-08-01) and `open`/`low` (2026-08-06) were
added before them -- and the fidelity gate proves that inertness rather than
assuming it.
"""
from typing import Optional

import pandas as pd

from analysis.backtest import precompute_ticker
from data.ingestion import get_price_df

MA_WINDOW = 14


def precompute_ticker_extended(ticker: str, config: dict) -> Optional[dict]:
    cached = precompute_ticker(ticker, config)
    if cached is None:
        return None
    frame = cached["frame"]

    df = get_price_df(ticker, days=9000)
    if df.empty:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    high = df["high"].astype(float).reindex(frame.index)
    # MA_14 of CLOSE -- the same close series precompute_ticker() itself
    # built its signal from, reindexed onto the identical frame index.
    ma14 = df["close"].astype(float).rolling(MA_WINDOW).mean().reindex(frame.index)

    frame = frame.copy()
    frame["high"] = high
    frame["ma14"] = ma14
    return {"ticker": cached["ticker"], "frame": frame}
