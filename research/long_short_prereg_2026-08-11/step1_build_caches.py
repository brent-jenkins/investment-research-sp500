"""Build Pool 1 and Pool 2 CLEAN caches for the long/short study.

Differs from every prior study's cache builder in exactly one way: it adds the
`high` column, which `analysis/backtest.py:precompute_ticker()` consumes to
compute ATR and then DISCARDS. `high` is required for the short-side mirror of
the realistic intraday stop fill (fire off the day's high, fill at
max(stop, open)) -- see PREREGISTRATION.md Sec 4. Without it short stops could
only fill at the close, the optimistic assumption the 2026-08-06 correction
removed for longs at a cost of 73.75% of total P&L.

Also precomputes the two trailing inputs the HTB borrow-tier flag needs
(Sec 5.2): drawdown from the trailing 252-trading-day high, and annualised
60-trading-day realised volatility. Both are computed on FULL history before
slicing, so a ticker's first in-window day carries a real trailing value rather
than a cold-start NaN. The flag itself is NOT baked in here -- only its raw
inputs -- so Sec 14.1's calibration can freeze thresholds without a cache rebuild.

Every shared column is computed by calling the SAME production functions
precompute_ticker() itself calls. step2_verify_cache_parity.py proves the
result is byte-identical to production's own frame on the shared columns.
"""
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import text

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis import signals, universe                      # noqa: E402
from analysis.volatility import _atr                        # noqa: E402
from data.database import get_engine                        # noqa: E402
from data.ingestion import get_price_df                     # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

START = pd.Timestamp("2007-01-01")
VOL_WINDOW = 60
DD_WINDOW = 252


def precompute_ticker_ls(ticker: str, config: dict):
    """Mirror of analysis.backtest.precompute_ticker() with `high` retained and
    the two HTB inputs added. Every shared column is produced by the identical
    production call precompute_ticker() makes."""
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)

    atr_s = _atr(high, low, close, config["signals"]["atr_period"])
    momentum_s = signals.compute_momentum_series(close)
    sig = signals.compute_signal_series(close)
    relative_rank_s = signals.compute_relative_rank_series(close)

    # HTB inputs (Sec 5.2), computed on full history before slicing.
    peak252 = close.rolling(DD_WINDOW, min_periods=DD_WINDOW).max()
    dd252 = close / peak252 - 1.0
    log_ret = np.log(close / close.shift(1))
    vol60 = log_ret.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std() * np.sqrt(252.0)

    frame = pd.DataFrame({
        "close": close, "atr": atr_s, "signal": sig, "momentum": momentum_s,
        "relative_rank": relative_rank_s, "open": open_, "low": low,
        "high": high, "dd252": dd252, "vol60": vol60,
    })
    return {"ticker": ticker, "frame": frame}


def slice_ticker(cached, ts_start, ts_end):
    if cached is None:
        return None
    frame = cached["frame"]
    in_range = frame[(frame.index >= ts_start) & (frame.index <= ts_end)].dropna(subset=["close"])
    if in_range.empty:
        return None
    return {"ticker": cached["ticker"], "frame": in_range}


def _latest_price_date(engine) -> pd.Timestamp:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT MAX(date) FROM price_data")).fetchone()
    return pd.Timestamp(row[0])


def _build(candidate_tickers, label, end):
    t0 = time.time()
    print(f"{label}: {len(candidate_tickers)} candidates, end={end.date()}", flush=True)
    caches, n_ok = [], 0
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker_ls(ticker, CONFIG)
        if c is not None:
            c = slice_ticker(c, START, end)
        if c is not None:
            n_ok += 1
        caches.append(c)
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(candidate_tickers)} ({time.time()-t0:.0f}s, {n_ok} usable)", flush=True)
    print(f"  {n_ok}/{len(candidate_tickers)} usable ({time.time()-t0:.0f}s)", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= end)]

    with open(HERE / f"_cache_{label}.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "candidate_tickers": candidate_tickers, "end": end,
        }, fh)
    print(f"Pickled {label} ({time.time()-t0:.0f}s total)\n", flush=True)


def main() -> None:
    engine = get_engine()
    end = _latest_price_date(engine)
    print(f"Latest price_data date (fresh query): {end.date()}", flush=True)

    with engine.connect() as conn:
        clean = {r[0] for r in conn.execute(
            text("SELECT ticker FROM ticker_data_quality WHERE status = 'CLEAN'"))}
    print(f"CLEAN tickers in DB (fresh query): {len(clean)}", flush=True)

    pool2_clean = sorted(set(universe.get_research_stock_candidates()) & clean)
    print(f"Pool 2 CLEAN candidate list: {len(pool2_clean)}", flush=True)
    _build(pool2_clean, "pool2_clean", end)

    pool1 = sorted(universe.get_live_stock_candidates())
    print(f"Pool 1 candidate list: {len(pool1)}", flush=True)
    _build(pool1, "pool1", end)


if __name__ == "__main__":
    main()
