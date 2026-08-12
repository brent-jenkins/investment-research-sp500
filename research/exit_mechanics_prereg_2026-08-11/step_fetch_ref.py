"""Fetch ^SP500TR (primary criterion benchmark) and ^IRX (idle-cash rate leg
of the cost template) READ-ONLY into a research-local cache.

Why this exists: the local research DB copy (taken 2026-08-09, price data
through 2026-08-07) has ZERO rows for both series. ^SP500TR was only added to
config.yaml's reference_tickers on 2026-08-10, after this copy was taken.

Deliberately does NOT write to price_data or ticker_data_quality -- this
mirrors the sleeve-allocation feasibility study's own read-only-research-cache
pattern. Nothing about this study should mutate the database.

Sliced to <= 2026-08-07 so every series in this study shares one as-of date
with the price_data copy, keeping the Arm A sanity gate an exact reproduction
of the published reference rather than a shifted one.
"""
import pickle
from pathlib import Path

import pandas as pd
import yfinance as yf

HERE = Path(__file__).parent
AS_OF = pd.Timestamp("2026-08-07")
START = pd.Timestamp("2005-01-01")
TICKERS = ["^SP500TR", "^IRX"]


def main() -> None:
    out = {}
    for ticker in TICKERS:
        df = yf.Ticker(ticker).history(start=START, end=AS_OF + pd.Timedelta(days=1),
                                       auto_adjust=False)
        if df.empty:
            raise SystemExit(f"FATAL: {ticker} returned no data -- cannot proceed.")
        close = df["Close"].astype(float)
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        close = close[(close.index >= START) & (close.index <= AS_OF)].sort_index()
        out[ticker] = close
        print(f"{ticker}: {len(close)} rows, {close.index[0].date()} -> {close.index[-1].date()}, "
              f"first={close.iloc[0]:.4f}, last={close.iloc[-1]:.4f}")

    with open(HERE / "_reference_series.pkl", "wb") as fh:
        pickle.dump(out, fh)
    print(f"\nWrote {HERE/'_reference_series.pkl'} (research-local, not the DB)")


if __name__ == "__main__":
    main()
