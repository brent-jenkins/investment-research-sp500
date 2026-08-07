"""INVESTIGATION ONLY -- no schema changes, no data changes, no exclusions,
no production code touched. Runs against a local, gitignored COPY of the
production database (database/investment_research.db in this checkout,
scp'd down earlier today, 871 tickers / 3,461,961 rows / through
2026-08-06). Writes only to this research/ directory.

Detects likely recycled-ticker-symbol corruption across the ENTIRE
price_data table (not just the 796-usable-candidate subset), per the
mechanism CLAUDE.md already documents for CFC/NCC/WB: an unrelated
company's price history landing under a delisted company's old ticker
symbol.

Step 1: per ticker, day-over-day CLOSE ratio across the ticker's own
        trading-day sequence (not calendar days).
Step 2: flag ratios outside [0.2, 5.0] (wider than production
        detect_price_cliffs()'s [0.5, 2.0] per the task spec) that are
        NOT "explained by a split factor recorded on that row". price_data
        has no split-factor column, so this is operationalised using the
        one signal actually available: adj_close alongside close. A
        genuinely recognised, correctly-adjusted split/corporate action
        shows adj_close moving smoothly (ratio near 1.0) even where close
        jumps (see research/split_adjustment_audit_2026-08-06's own
        established finding: "a properly split-adjusted series shows
        close_after/close_before ~= 1.0" is the SIGNAL of correct
        adjustment on the adjusted side). If adj_close ALSO shows the same
        extreme jump as close (both move together, in lockstep), nothing
        in the stored data explains it as a recognised adjustment -- flag
        it as UNEXPLAINED.
Step 3: for every ticker with >=1 UNEXPLAINED flag, segment its close
        series into price "regimes" at the flagged transition dates
        (median close within each regime). Classify:
          - 1 flagged transition: LIKELY_CORPORATE_ACTION (monotone by
            construction -- nothing to test a reversal against).
          - >=2 flagged transitions: check whether any regime's median is
            within 20% of an EARLIER, already-departed regime's median
            (i.e. the series returns to a price level it had already left)
            -> LIKELY_RECYCLED (interleaved companies). If every regime is
            a genuinely new level, never revisited -> LIKELY_CORPORATE_
            ACTION (e.g. a real sequence of splits/reverse-splits, each
            new level distinct). Anything that doesn't cleanly resolve
            either way (fewer than 3 usable regimes after de-duplication,
            or a borderline near-20%-threshold call) -> AMBIGUOUS, stated
            explicitly rather than forced into one bucket.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import sqlite3
import time

import numpy as np
import pandas as pd

DB_PATH = r"c:\GitHub\investment-app\database\investment_research.db"
OUT_DIR = "research/recycled_ticker_investigation_2026-08-07"

RATIO_LOW = 0.2
RATIO_HIGH = 5.0
REGIME_REVISIT_TOLERANCE = 0.20  # 20% -- "close enough to a prior regime to call it a return"


def load_price_data() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT ticker, date, close, adj_close FROM price_data ORDER BY ticker, date", conn
    )
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df


def find_flagged_transitions(df: pd.DataFrame) -> pd.DataFrame:
    hits = []
    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("date")
        close = g["close"].to_numpy()
        adj = g["adj_close"].to_numpy()
        dates = g["date"].to_numpy()
        if len(close) < 2:
            continue
        close_ratio = close[1:] / close[:-1]
        # adj_close ratio at the same transitions -- guard against zero/NaN.
        with np.errstate(divide="ignore", invalid="ignore"):
            adj_ratio = adj[1:] / adj[:-1]

        bad = (close_ratio < RATIO_LOW) | (close_ratio > RATIO_HIGH)
        idx = bad.nonzero()[0]
        for i in idx:
            ar = adj_ratio[i] if i < len(adj_ratio) else np.nan
            # "Explained by a recorded split" = adj_close stayed smooth
            # (near 1.0) while close jumped -- the adjustment mechanism
            # already recognised and corrected for it.
            explained = bool(pd.notna(ar) and 0.85 <= ar <= 1.15)
            hits.append({
                "ticker": ticker,
                "date_prev": pd.Timestamp(dates[i]).date().isoformat(),
                "date": pd.Timestamp(dates[i + 1]).date().isoformat(),
                "close_prev": float(close[i]),
                "close": float(close[i + 1]),
                "close_ratio": float(close_ratio[i]),
                "adj_close_ratio": float(ar) if pd.notna(ar) else None,
                "explained_by_adj_close": explained,
            })
    return pd.DataFrame(hits)


def classify_tickers(df_prices: pd.DataFrame, flagged: pd.DataFrame) -> pd.DataFrame:
    unexplained = flagged[~flagged["explained_by_adj_close"]]
    results = []
    for ticker, g in unexplained.groupby("ticker"):
        transitions = sorted(pd.Timestamp(d) for d in g["date"])
        n = len(transitions)

        # Build the full close series for regime medians.
        series = df_prices[df_prices["ticker"] == ticker].sort_values("date")
        series_dates = series["date"].to_numpy()
        series_close = series["close"].to_numpy()

        boundaries = [series_dates.min()] + [pd.Timestamp(t).to_datetime64() for t in transitions] + [series_dates.max() + pd.Timedelta(days=1)]
        regimes = []
        for i in range(len(boundaries) - 1):
            mask = (series_dates >= boundaries[i]) & (series_dates < boundaries[i + 1])
            if mask.sum() == 0:
                continue
            regimes.append({
                "start": pd.Timestamp(boundaries[i]).date().isoformat(),
                "end": pd.Timestamp(min(boundaries[i + 1], series_dates.max() + np.timedelta64(1, "D"))).date().isoformat(),
                "median_close": float(np.median(series_close[mask])),
                "n_rows": int(mask.sum()),
            })

        if n == 1:
            classification = "LIKELY_CORPORATE_ACTION"
            reason = "single unexplained transition -- monotone by construction, nothing to test a reversal against"
        else:
            # Check whether any regime revisits an EARLIER, already-departed regime's level.
            revisit_found = False
            for i in range(2, len(regimes)):  # regime i compared against any regime before regime i-1 (already departed)
                for j in range(0, i - 1):
                    a, b = regimes[i]["median_close"], regimes[j]["median_close"]
                    if a <= 0 or b <= 0:
                        continue
                    if abs(a - b) / max(a, b) <= REGIME_REVISIT_TOLERANCE:
                        revisit_found = True
                        break
                if revisit_found:
                    break

            if len(regimes) < 3:
                classification = "AMBIGUOUS"
                reason = f"{n} unexplained transitions but fewer than 3 distinguishable regimes after segmentation"
            elif revisit_found:
                classification = "LIKELY_RECYCLED"
                reason = "series revisits a price level it had already departed from -- reversal pattern"
            else:
                classification = "LIKELY_CORPORATE_ACTION"
                reason = "every regime is a distinct, never-revisited level -- monotone sequence"

        results.append({
            "ticker": ticker, "n_unexplained_transitions": n,
            "classification": classification, "reason": reason,
            "n_regimes": len(regimes), "regimes": regimes,
        })
    return pd.DataFrame(results)


def main():
    t0 = time.time()
    print(f"Loading price_data (read-only) from {DB_PATH} ...", flush=True)
    df = load_price_data()
    print(f"  {len(df):,} rows, {df['ticker'].nunique()} tickers, "
          f"{df['date'].min().date()} -> {df['date'].max().date()} ({time.time()-t0:.0f}s)", flush=True)

    print(f"\nStep 1+2: ratio scan [{RATIO_LOW}, {RATIO_HIGH}], cross-checked against adj_close ...", flush=True)
    flagged = find_flagged_transitions(df)
    print(f"  {len(flagged)} raw ratio-break transitions across {flagged['ticker'].nunique() if not flagged.empty else 0} tickers ({time.time()-t0:.0f}s)", flush=True)
    flagged.to_csv(f"{OUT_DIR}/step1_flagged_transitions_raw.csv", index=False)

    n_explained = flagged["explained_by_adj_close"].sum()
    n_unexplained = (~flagged["explained_by_adj_close"]).sum()
    print(f"  explained by adj_close (real, recognised adjustment): {n_explained}", flush=True)
    print(f"  UNEXPLAINED (adj_close also jumps -- nothing recognises this as a real adjustment): {n_unexplained}", flush=True)

    print(f"\nStep 3: classifying tickers with >=1 unexplained transition ...", flush=True)
    classified = classify_tickers(df, flagged)
    classified.to_json(f"{OUT_DIR}/step3_classified_tickers.json", orient="records", indent=2)
    # Flat CSV without the nested regimes column for easy scanning.
    classified.drop(columns=["regimes"]).to_csv(f"{OUT_DIR}/step3_classified_tickers.csv", index=False)

    print(f"\nClassification counts:", flush=True)
    print(classified["classification"].value_counts().to_string() if not classified.empty else "  (none)", flush=True)
    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
