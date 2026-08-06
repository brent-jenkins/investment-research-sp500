"""Split-adjustment integrity scan across every ticker actually traded in
this sweep -- prompted by a user challenge that the KLAC $7.199 price used
as evidence of "no data problem" in the parity investigation could equally
be evidence OF one (identical-and-wrong is the same signature as
identical-and-stale, per backfill_price_history()'s own docstring warning
about never-re-fetched rows).

KLAC itself was independently verified clean (WebSearch/WebFetch against
KLA's own IR press release + current market price: a genuine 10-for-1
split effective 2026-06-12, and $7.199 = the correctly split-adjusted
2016-10-17 level given that real, confirmed split). But that resolves only
KLAC -- it does not establish the REST of the sweep's ~500 traded tickers
are clean. This script reuses the methodology CLAUDE_HISTORY.md's own
2026-08-02 "Part B" investigation established: scan for a single-day
return matching a clean split ratio, then cross-check any hit against a
fresh, live yfinance pull (bypassing the stored DB) and the ticker's own
recorded corporate actions.

Local-only pass (fast, no network): flags any (ticker, date) whose
day-over-day return sits within tolerance of a clean split-ratio step.
This catches the FAILURE MODE that matters here -- a series with an
internal discontinuity (some rows adjusted, some not) -- not a smoothly,
consistently adjusted series like KLAC's, which produces no jump at all.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd

from data.ingestion import get_price_df

HERE = Path(__file__).parent
RESULTS = HERE / "results"

# clean split ratios as day-over-day multiplicative factors (new/old), both
# forward splits (factor < 1, e.g. 2-for-1 => 0.5) and reverse splits
# (factor > 1)
RATIOS = [1/2, 1/3, 2/3, 1/4, 3/4, 1/5, 1/7, 1/10, 1/20, 1/50,
          2, 3, 4, 5, 7, 10, 20, 50]
TOL = 0.02  # +/- 2% band around the clean ratio


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    tickers = set()
    for tag in ("05", "07", "095"):
        p = RESULTS / f"trades_cap{tag}.csv"
        if p.exists():
            tickers |= set(pd.read_csv(p)["ticker"].unique())
    tickers = sorted(tickers)
    log(f"Scanning {len(tickers)} distinct traded tickers for split-ratio discontinuities ...")

    hits = []
    for n, t in enumerate(tickers, 1):
        df = get_price_df(t, days=9000)
        if df.empty or len(df) < 10:
            continue
        df = df.set_index("date").sort_index()
        close = df["close"].astype(float)
        ratio = close / close.shift(1)
        for dt, r in ratio.items():
            if pd.isna(r) or r <= 0:
                continue
            for clean in RATIOS:
                if abs(r - clean) <= clean * TOL:
                    hits.append({"ticker": t, "date": dt, "day_ratio": round(float(r), 4),
                                "nearest_clean_ratio": clean,
                                "prev_close": round(float(close.shift(1).loc[dt]), 4),
                                "close": round(float(close.loc[dt]), 4)})
                    break
        if n % 100 == 0:
            log(f"  {n}/{len(tickers)}")

    hdf = pd.DataFrame(hits)
    hdf.to_csv(RESULTS / "split_detection_hits.csv", index=False)
    log(f"Scan complete: {len(hdf)} candidate discontinuities across {hdf['ticker'].nunique() if not hdf.empty else 0} tickers")
    if not hdf.empty:
        print(hdf.sort_values(["ticker", "date"]).to_string(index=False))


if __name__ == "__main__":
    main()
