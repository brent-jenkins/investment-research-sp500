"""PHASE 1 -- CLASSIFY ONLY, no data changes. For each of the 14 live/held-
ticker price cliffs flagged by detect_price_cliffs(), determine whether the
cliff is a corporate-action-driven adjustment artefact (repairable) or a
genuine market move (leave alone).

For each (ticker, approximate date):
  1. Find the exact consecutive-trading-day pair in price_data straddling
     that date where the close ratio breaks outside [0.5, 2.0] (reproducing
     detect_price_cliffs()'s own signature, scoped to a window around the
     given date rather than the whole table).
  2. Check adj_close across the SAME two rows. adj_close is already fully
     split+dividend adjusted by yfinance at fetch time -- a genuine,
     correctly-ingested corporate-action adjustment should show adj_close
     moving SMOOTHLY (ratio ~1.0) across the transition even where close
     jumps (close is raw/unadjusted in this project's data model -- see
     CLAUDE.md's "auto_adjust=False" note). If BOTH close and adj_close
     jump together, that is the signature of a genuine market move (the
     price really did change that much), not an adjustment artefact.
  3. Query yfinance .splits for a factor near that date.
  4. Report evidence for each -- classification is NOT auto-decided here;
     this script produces the evidence table for human review.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import sqlite3
import time

import pandas as pd
import yfinance as yf

DB_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "investment_research.db"
OUT_DIR = Path(__file__).resolve().parent

# (ticker, approximate date as given in the alert)
CLIFFS = [
    ("AIG", "2008-09-15"), ("APA", "2020-03-09"), ("EXPE", "2011-12-21"),
    ("GL", "2024-04-11"), ("HIG", "2008-10-30"), ("HIG", "2008-12-05"),
    ("JCI", "2007-07-02"), ("KDP", "2018-07-10"), ("MO", "2008-03-31"),
    ("OXY", "2020-03-09"), ("PCG", "2019-01-14"), ("STT", "2009-01-20"),
    ("TRGP", "2020-03-09"), ("WY", "2010-07-20"),
]


def _yf_symbol(ticker: str) -> str:
    return ticker.replace(".", "-")


def load_window(conn, ticker: str, center_date: str, pad_days: int = 10) -> pd.DataFrame:
    center = pd.Timestamp(center_date)
    start = (center - pd.Timedelta(days=pad_days)).isoformat()
    end = (center + pd.Timedelta(days=pad_days)).isoformat()
    df = pd.read_sql_query(
        "SELECT date, close, adj_close FROM price_data WHERE ticker = ? AND date >= ? AND date <= ? ORDER BY date",
        conn, params=(ticker, start, end),
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def find_cliff_pair(df: pd.DataFrame, center_date: str) -> "tuple | None":
    """Find the consecutive-row pair with the most extreme close ratio in
    this window -- should be the exact pair the original alert flagged."""
    if len(df) < 2:
        return None
    close = df["close"].to_numpy()
    ratios = close[1:] / close[:-1]
    extremity = abs(pd.Series(ratios).apply(lambda r: max(r, 1 / r) if r > 0 else float("inf")))
    idx = extremity.idxmax()
    return idx


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    results = []

    for ticker, date in CLIFFS:
        print(f"=== {ticker} {date} ===", flush=True)
        df = load_window(conn, ticker, date)
        if len(df) < 2:
            print(f"  INSUFFICIENT DATA in window ({len(df)} rows)", flush=True)
            results.append({"ticker": ticker, "alert_date": date, "status": "INSUFFICIENT_DATA"})
            continue

        idx = find_cliff_pair(df, date)
        row_before = df.iloc[idx]
        row_after = df.iloc[idx + 1]

        close_ratio = row_after["close"] / row_before["close"]
        adj_close_ratio = (
            row_after["adj_close"] / row_before["adj_close"]
            if pd.notna(row_after["adj_close"]) and pd.notna(row_before["adj_close"]) and row_before["adj_close"]
            else None
        )
        adj_close_smooth = adj_close_ratio is not None and 0.85 <= adj_close_ratio <= 1.15

        # yfinance .splits near this date
        matched_split_factor = None
        matched_split_date = None
        days_off = None
        try:
            tk = yf.Ticker(_yf_symbol(ticker))
            splits = tk.splits
            if splits is not None and not splits.empty:
                cliff_date = pd.Timestamp(row_after["date"])
                best = None
                for split_date, factor in splits.items():
                    split_date_naive = pd.Timestamp(split_date).tz_localize(None)
                    delta = abs((cliff_date - split_date_naive).days)
                    if best is None or delta < best[0]:
                        best = (delta, split_date_naive, factor)
                if best and best[0] <= 30:
                    days_off, matched_split_date, matched_split_factor = best[0], best[1].date().isoformat(), float(best[2])
        except Exception as exc:
            print(f"  WARN: splits fetch failed: {exc}", flush=True)
        time.sleep(0.3)

        result = {
            "ticker": ticker, "alert_date": date,
            "date_before": row_before["date"].date().isoformat(), "close_before": float(row_before["close"]),
            "date_after": row_after["date"].date().isoformat(), "close_after": float(row_after["close"]),
            "close_ratio": float(close_ratio),
            "adj_close_before": float(row_before["adj_close"]) if pd.notna(row_before["adj_close"]) else None,
            "adj_close_after": float(row_after["adj_close"]) if pd.notna(row_after["adj_close"]) else None,
            "adj_close_ratio": float(adj_close_ratio) if adj_close_ratio is not None else None,
            "adj_close_smooth": adj_close_smooth,
            "matched_split_factor": matched_split_factor, "matched_split_date": matched_split_date,
            "split_days_off": days_off,
        }
        print(f"  {row_before['date'].date()} close={row_before['close']:.2f} adj={result['adj_close_before']}  ->  "
              f"{row_after['date'].date()} close={row_after['close']:.2f} adj={result['adj_close_after']}", flush=True)
        print(f"  close_ratio={close_ratio:.3f}  adj_close_ratio={result['adj_close_ratio']}  "
              f"adj_close_smooth={adj_close_smooth}  matched_split={matched_split_factor}@{matched_split_date} "
              f"({days_off}d off)", flush=True)
        results.append(result)

    df_out = pd.DataFrame(results)
    df_out.to_csv(OUT_DIR / "step1_classification_evidence.csv", index=False)
    print(f"\nWrote step1_classification_evidence.csv ({len(df_out)} rows)", flush=True)


if __name__ == "__main__":
    main()
