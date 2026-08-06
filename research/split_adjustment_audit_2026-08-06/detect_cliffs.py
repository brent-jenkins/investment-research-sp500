"""A1 -- read-only detection of split-adjustment cliff artefacts in price_data.

Reads a local COPY of the production database (database/investment_research.db
in this worktree, scp'd down 2026-08-06 19:03 UTC, max date 2026-08-05,
540 distinct tickers -- confirmed fresh before running this). Writes nothing
back to that database. Outputs CSVs into this same research directory.

Step 1: for every ticker, compute consecutive-day close ratios (close[t] /
        close[t-1] using each ticker's own trading-day sequence, not
        calendar days). Flag any ratio outside [0.5, 2.0].
Step 2: for every flagged ticker, pull yfinance .splits and classify each
        hit as: real market move, genuine split correctly reflected, or
        cliff artefact (ratio ~matches a split factor but the split date
        doesn't line up with the discontinuity, or sits ~10 trading days
        off it -- the rolling pull_incremental() window is 10 calendar
        days, so a cliff should appear at the boundary between "old basis"
        and "new basis" rows, roughly 10 calendar days after the real
        split date, on the row where the incremental refresh first pulled
        post-split data over a ticker that already had pre-split history
        on disk).
Step 3: independently, walk every ticker's yfinance .splits regardless of
        whether step 1 flagged it, and check the database's own close
        series directly across that specific date -- catches a split that
        produced a ratio just inside [0.5, 2.0] (e.g. a 3:2 split, ratio
        0.667) that step 1's blunter threshold would miss.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import sqlite3
import time
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

DB_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "investment_research.db"
OUT_DIR = Path(__file__).resolve().parent

RATIO_LOW = 0.5
RATIO_HIGH = 2.0


def load_price_data() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT ticker, date, close FROM price_data ORDER BY ticker, date", conn
    )
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df


def find_ratio_breaks(df: pd.DataFrame) -> pd.DataFrame:
    hits = []
    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("date")
        close = g["close"].to_numpy()
        dates = g["date"].to_numpy()
        if len(close) < 2:
            continue
        ratio = close[1:] / close[:-1]
        bad = (ratio < RATIO_LOW) | (ratio > RATIO_HIGH)
        idx = bad.nonzero()[0]
        for i in idx:
            hits.append(
                {
                    "ticker": ticker,
                    "date_prev": pd.Timestamp(dates[i]).date().isoformat(),
                    "date": pd.Timestamp(dates[i + 1]).date().isoformat(),
                    "close_prev": float(close[i]),
                    "close": float(close[i + 1]),
                    "ratio": float(ratio[i]),
                }
            )
    return pd.DataFrame(hits)


def _yf_symbol(ticker: str) -> str:
    return ticker.replace(".", "-")



# A cliff artefact is caused by pull_incremental()'s rolling 10-calendar-day
# window: the discontinuity should land within roughly that window of the
# real split date, generously padded for retries/weekends/holidays. A split
# ratio match against a split from years (or decades) earlier or later is
# coincidence, not causation -- e.g. a ratio of ~0.47 during the March 2020
# COVID oil-price crash coincidentally resembles a 2:1 split ratio, but the
# ticker's actual 2:1 split happened in 2004, sixteen years earlier. Require
# BOTH a ratio match AND the closest such split to be within this window
# before ever calling something a cliff artefact.
_PROXIMITY_WINDOW_DAYS = 30


def classify_hits(hits: pd.DataFrame) -> pd.DataFrame:
    """For every flagged ticker, pull yfinance .splits and classify each hit."""
    results = []
    tickers = sorted(hits["ticker"].unique()) if not hits.empty else []
    splits_cache: dict[str, pd.Series] = {}

    for ticker in tickers:
        try:
            tk = yf.Ticker(_yf_symbol(ticker))
            splits = tk.splits  # DatetimeIndex -> split ratio (e.g. 4.0 for 4:1)
            if splits is None:
                splits = pd.Series(dtype=float)
            splits_cache[ticker] = splits
        except Exception as exc:
            print(f"  WARN: could not fetch splits for {ticker}: {exc}", file=sys.stderr)
            splits_cache[ticker] = pd.Series(dtype=float)
        time.sleep(0.3)

    for _, row in hits.iterrows():
        ticker = row["ticker"]
        splits = splits_cache.get(ticker, pd.Series(dtype=float))
        hit_date = pd.Timestamp(row["date"])
        ratio = row["ratio"]
        # A split of factor F (e.g. 4-for-1) should make close[t]/close[t-1]
        # ~= 1/F on the day the adjustment basis actually shifts.
        classification = "real_market_move"
        matched_split_date = None
        matched_split_factor = None
        days_off = None

        if not splits.empty:
            # Among splits whose ratio plausibly matches this hit, keep only
            # the one closest in time to the hit date.
            candidates = []
            for split_date, factor in splits.items():
                split_date = pd.Timestamp(split_date).tz_localize(None)
                expected_ratio = 1.0 / float(factor) if factor != 0 else None
                if expected_ratio is None:
                    continue
                if expected_ratio * 0.85 <= ratio <= expected_ratio * 1.15 or (
                    (1 / expected_ratio) * 0.85 <= ratio <= (1 / expected_ratio) * 1.15
                ):
                    delta_days = (hit_date - split_date).days
                    candidates.append((abs(delta_days), delta_days, split_date, factor))

            if candidates:
                candidates.sort(key=lambda c: c[0])
                _, delta_days, split_date, factor = candidates[0]
                if abs(delta_days) <= _PROXIMITY_WINDOW_DAYS:
                    matched_split_date = split_date.date().isoformat()
                    matched_split_factor = float(factor)
                    days_off = int(delta_days)
                    classification = (
                        "genuine_split_correctly_reflected" if abs(delta_days) <= 2 else "cliff_artefact"
                    )
                # else: a ratio-matching split exists somewhere in history but
                # nowhere near this hit -- coincidence, leave as real_market_move.

        results.append(
            {
                **row.to_dict(),
                "classification": classification,
                "matched_split_date": matched_split_date,
                "matched_split_factor": matched_split_factor,
                "hit_days_after_split": days_off,
            }
        )

    return pd.DataFrame(results)


def check_all_known_splits(df: pd.DataFrame) -> pd.DataFrame:
    """Step 3: independently walk every S&P500-ever-known ticker's real
    yfinance splits (regardless of step 1's ratio threshold) and check the
    DB's own close series directly across that date.
    """
    from analysis.universe import get_all_known_stock_tickers

    tickers = sorted(get_all_known_stock_tickers())
    db_min_date = df["date"].min()
    db_max_date = df["date"].max()

    rows = []
    for i, ticker in enumerate(tickers):
        try:
            tk = yf.Ticker(_yf_symbol(ticker))
            splits = tk.splits
        except Exception as exc:
            print(f"  WARN: could not fetch splits for {ticker}: {exc}", file=sys.stderr)
            time.sleep(0.3)
            continue
        time.sleep(0.3)

        if splits is None or splits.empty:
            continue

        g = df[df["ticker"] == ticker].sort_values("date")
        if g.empty:
            continue
        g_dates = g["date"].to_numpy()
        g_close = g["close"].to_numpy()

        for split_date, factor in splits.items():
            split_date = pd.Timestamp(split_date).tz_localize(None)
            if split_date < db_min_date or split_date > db_max_date:
                continue  # split predates or postdates this ticker's DB coverage window

            # Find DB rows closest to the split date
            after_mask = g_dates >= split_date.to_datetime64()
            before_mask = g_dates < split_date.to_datetime64()
            if not after_mask.any() or not before_mask.any():
                continue
            close_before = g_close[before_mask][-1]
            date_before = pd.Timestamp(g_dates[before_mask][-1]).date().isoformat()
            close_after = g_close[after_mask][0]
            date_after = pd.Timestamp(g_dates[after_mask][0]).date().isoformat()
            observed_ratio = close_after / close_before if close_before else None
            expected_ratio = 1.0 / float(factor) if factor else None

            # CORRECTED (was inverted in the first run of this script): a
            # PROPERLY split-adjusted series shows observed_ratio ~= 1.0 at
            # the split date (yfinance has already retroactively rebased
            # every prior Close onto the new post-split basis, so there is
            # no discontinuity at all in a correctly-ingested series). A
            # CLIFF ARTEFACT is the opposite: observed_ratio matches the
            # RAW, unadjusted split factor's inverse (expected_ratio)
            # instead of 1.0 -- that is the signature of stale pre-split
            # rows sitting next to fresh post-split rows.
            is_smooth = observed_ratio is not None and abs(observed_ratio - 1.0) <= 0.15
            is_cliff_artefact = (
                not is_smooth
                and observed_ratio is not None
                and expected_ratio is not None
                and expected_ratio != 0
                and abs(observed_ratio - expected_ratio) / abs(expected_ratio) <= 0.15
            )
            correctly_reflected = is_smooth

            rows.append(
                {
                    "ticker": ticker,
                    "split_date": split_date.date().isoformat(),
                    "split_factor": float(factor),
                    "expected_ratio": expected_ratio,
                    "date_before": date_before,
                    "close_before": float(close_before),
                    "date_after": date_after,
                    "close_after": float(close_after),
                    "observed_ratio": observed_ratio,
                    "correctly_reflected": correctly_reflected,
                    "is_cliff_artefact": is_cliff_artefact,
                }
            )
        if (i + 1) % 50 == 0:
            print(f"  ...checked {i + 1}/{len(tickers)} tickers", file=sys.stderr)

    return pd.DataFrame(rows)


def main():
    print(f"Loading price_data from {DB_PATH} (read-only)...")
    df = load_price_data()
    print(f"  {len(df):,} rows, {df['ticker'].nunique()} tickers, "
          f"{df['date'].min().date()} -> {df['date'].max().date()}")

    print("\nStep 1: consecutive-day close ratio scan...")
    hits = find_ratio_breaks(df)
    print(f"  {len(hits)} ratio breaks outside [{RATIO_LOW}, {RATIO_HIGH}] "
          f"across {hits['ticker'].nunique() if not hits.empty else 0} tickers")
    hits.to_csv(OUT_DIR / "step1_ratio_breaks_raw.csv", index=False)

    print("\nStep 2: classifying against yfinance .splits...")
    classified = classify_hits(hits)
    classified.to_csv(OUT_DIR / "step2_classified_ratio_breaks.csv", index=False)
    if not classified.empty:
        print(classified["classification"].value_counts().to_string())

    print("\nStep 3: independent walk of every known S&P500 ticker's real splits...")
    split_check = check_all_known_splits(df)
    split_check.to_csv(OUT_DIR / "step3_all_known_splits_checked.csv", index=False)
    if not split_check.empty:
        n_artefact = split_check["is_cliff_artefact"].sum()
        print(f"  {len(split_check)} split events checked across "
              f"{split_check['ticker'].nunique()} tickers; "
              f"{n_artefact} are CLIFF ARTEFACTS (observed_ratio matches the raw, "
              f"unadjusted split factor instead of ~1.0)")
        print(split_check[split_check["is_cliff_artefact"]].to_string())
    else:
        print("  No known splits fall within any ticker's DB coverage window.")

    print("\nDone. CSVs written to", OUT_DIR)


if __name__ == "__main__":
    main()
