"""Read-only extraction of ^GSPC from the verified local research database.

No writes of any kind. Nothing else is read: this diagnostic is a property of
one index series, not a strategy test.

Per CLAUDE.md's derived-state rule the source file is NAMED and re-queried
fresh here rather than inherited from any prior session's figure.
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

HERE = Path(__file__).resolve().parent
SOURCE_DB = Path(r"C:\GitHub\investment-app\database\investment_research.db")
START = "2007-01-03"
END = "2026-08-07"


def main() -> None:
    if not SOURCE_DB.exists():
        sys.exit(f"FATAL: source DB not found at {SOURCE_DB}")
    # read-only URI -- the connection itself cannot write
    eng = create_engine(f"sqlite:///file:{SOURCE_DB.as_posix()}?mode=ro&uri=true")
    with eng.connect() as c:
        total, dmin, dmax = c.execute(text(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM price_data WHERE ticker='^GSPC'"
        )).fetchone()
        df = pd.read_sql(
            text("SELECT date, open, high, low, close, adj_close FROM price_data "
                 "WHERE ticker='^GSPC' ORDER BY date"), c)

    print(f"source DB   : {SOURCE_DB}  (opened read-only)")
    print(f"^GSPC in DB : {total} rows, {dmin} -> {dmax}")

    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date").sort_index()
    df = df[(df.index >= START) & (df.index <= END)]

    # Integrity checks -- a diagnostic built on a gapped or duplicated series
    # would produce confident nonsense.
    assert df.index.is_unique, "duplicate dates in ^GSPC"
    assert df.index.is_monotonic_increasing, "^GSPC not sorted"
    assert df["close"].notna().all(), "NaN closes in ^GSPC"
    assert (df["close"] > 0).all(), "non-positive closes in ^GSPC"
    ratio = df["close"] / df["close"].shift(1)
    breaks = ratio[(ratio < 0.5) | (ratio > 2.0)].dropna()

    print(f"window      : {df.index[0].date()} -> {df.index[-1].date()}, {len(df)} rows")
    print(f"largest 1-day moves: min x{ratio.min():.4f}  max x{ratio.max():.4f}")
    print(f"price-cliff breaks outside [0.5, 2.0]: {len(breaks)}")
    gaps = df.index.to_series().diff().dt.days.dropna()
    print(f"calendar gaps between rows: max {int(gaps.max())} days, "
          f"{int((gaps > 5).sum())} gaps over 5 days")

    out = HERE / "gspc.csv"
    df.to_csv(out)
    print(f"\nwrote {out} ({len(df)} rows) -- research-local, DB untouched")


if __name__ == "__main__":
    main()
