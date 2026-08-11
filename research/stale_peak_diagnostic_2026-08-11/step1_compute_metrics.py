"""Stale-peak diagnostic, step 1 -- compute per-trade entry-context metrics.

READ-ONLY. No backtest is re-run. Every metric here is derived from price_data
plus the already-preserved trade output; the trades themselves are decisions the
system already made and are used exactly as preserved.

WHY A COMPUTE STEP IS NEEDED AT ALL: the preserved trade CSVs carry
{ticker, direction, entry_date, exit_date, entry_price, exit_price, exit_reason,
pnl_pct, position_gbp, pnl_gbp, stop_distance_pct, holding_days, is_win} and do
NOT carry peak-drawdown-at-entry, days-since-peak, momentum-at-entry, or
rank-within-day. Those four are genuinely absent and are recomputed here from
price_data using the SAME production functions the engine itself calls
(analysis.signals.compute_momentum_series / compute_relative_rank_series /
compute_signal_series, analysis.universe.is_member, analysis.crisis). This is a
read-only derivation over decisions already made, not a re-simulation.

THE METRIC, defined before any measurement (per the task's own instruction):

  compute_momentum_series() is close(t) / close(t-252) - 1.0 -- a TWO-POINT
  calculation. The interval it cannot see is exactly rows [t-252, t]. So:

    peak_close      = max(close) over rows [t-252, t] inclusive
    below_peak_pct  = (peak_close - close(t)) / peak_close * 100
    days_since_peak = (trading rows between the peak row and t)

  below_peak_pct is expressed as a POSITIVE magnitude (0 = entering at the
  window peak; 30 = entering 30% below it), because the question is "how far
  down from its own high was this thing when we bought it". The denominator is
  the peak, not the entry price, so it reads as the drawdown a holder of the
  peak would have suffered -- the conventional direction for a drawdown figure.

  The window is [t-252, t] inclusive rather than the trailing 252 rows, because
  t-252 is precisely the momentum calculation's own back anchor: the interval
  bounded by the two points the signal actually reads.
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

from analysis import signals, universe, crisis                      # noqa: E402
from analysis.decision import resolve_min_momentum_floor            # noqa: E402
from data.database import get_engine                                # noqa: E402
from data.ingestion import get_price_df                             # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

LOOKBACK = signals.MOMENTUM_LOOKBACK_DAYS          # 252, the real production constant
SRC = ROOT / "research" / "relative_ranking_prereg_2026-08-09"
TRADE_FILES = {
    "B_relative_rank": SRC / "trades_pool2_clean_B.csv",   # current live ordering -- PRIMARY
    "A_absolute_momentum": SRC / "trades_pool2_clean_A.csv",
}


def load_pool():
    engine = get_engine()
    with engine.connect() as conn:
        clean = {r[0] for r in conn.execute(
            text("SELECT ticker FROM ticker_data_quality WHERE status = 'CLEAN'"))}
        maxd = pd.Timestamp(conn.execute(text("SELECT MAX(date) FROM price_data")).fetchone()[0])
    pool = sorted(set(universe.get_research_stock_candidates()) & clean)
    print(f"Pool 2 CLEAN: {len(pool)} tickers; price_data through {maxd.date()}", flush=True)
    return pool, maxd


def build_series(tickers):
    """close / momentum / relative_rank / signal per ticker, full history."""
    out = {}
    t0 = time.time()
    for i, tk in enumerate(tickers):
        df = get_price_df(tk, days=8000)
        if df.empty or len(df) < 300:
            continue
        df = df.set_index("date")
        df.index = pd.to_datetime(df.index)
        close = df["close"].astype(float)
        out[tk] = pd.DataFrame({
            "close": close,
            "momentum": signals.compute_momentum_series(close),
            "relative_rank": signals.compute_relative_rank_series(close),
            "signal": signals.compute_signal_series(close),
        })
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(tickers)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"  built {len(out)} ticker series ({time.time()-t0:.0f}s)", flush=True)
    return out


def per_trade_metrics(trades: pd.DataFrame, series: dict) -> pd.DataFrame:
    rows = []
    for t in trades.itertuples():
        fr = series.get(t.ticker)
        if fr is None:
            rows.append({})
            continue
        dt = pd.Timestamp(t.entry_date)
        if dt not in fr.index:
            rows.append({})
            continue
        i = fr.index.get_loc(dt)
        lo = max(0, i - LOOKBACK)
        win = fr["close"].iloc[lo:i + 1]
        if len(win) < 2:
            rows.append({})
            continue
        peak = float(win.max())
        peak_pos = int(win.values.argmax())
        entry_close = float(fr["close"].iloc[i])
        rows.append({
            "peak_close": peak,
            "entry_close": entry_close,
            "below_peak_pct": (peak - entry_close) / peak * 100.0 if peak > 0 else np.nan,
            "days_since_peak": (len(win) - 1) - peak_pos,
            "window_rows": len(win),
            "momentum_at_entry_pct": float(fr["momentum"].iloc[i]) * 100.0
            if pd.notna(fr["momentum"].iloc[i]) else np.nan,
            "relative_rank_at_entry_pct": float(fr["relative_rank"].iloc[i]) * 100.0
            if pd.notna(fr["relative_rank"].iloc[i]) else np.nan,
        })
    return pd.DataFrame(rows, index=trades.index)


def rank_within_day(trades: pd.DataFrame, series: dict, gspc_close: pd.Series,
                    rank_col: str) -> pd.Series:
    """Percentile position of each admitted ticker within that day's own
    gate-passing eligible set, ranked by *rank_col* descending. 0.0 = the single
    best candidate that day; 100.0 = the worst that still passed the gate.

    Reconstructs the eligible set with the real production gate conditions
    (signal on, momentum floor, membership, crisis entry-block). This is the
    Part 4 / smoothness-§6 question: were these admitted from the top of the
    ranking or from deep in it.
    """
    min_floor = resolve_min_momentum_floor(CONFIG)
    entry_blocked = crisis.extend_trigger(crisis.compute_raw_trigger(gspc_close))

    dates = sorted({pd.Timestamp(d) for d in trades["entry_date"]})
    tickers = list(series.keys())

    mom = pd.DataFrame({tk: series[tk]["momentum"] for tk in tickers})
    rnk = pd.DataFrame({tk: series[tk][rank_col] for tk in tickers})
    sig = pd.DataFrame({tk: series[tk]["signal"] for tk in tickers})
    mom = mom.reindex(dates); rnk = rnk.reindex(dates); sig = sig.reindex(dates)

    # Membership mask, built vectorised per ticker from the real windows.
    memb = pd.DataFrame(False, index=dates, columns=tickers)
    for tk in tickers:
        wins = universe.get_membership_windows(tk)
        if not wins:
            continue
        col = np.zeros(len(dates), dtype=bool)
        di = np.array([d.date() for d in dates])
        for s, e in wins:
            col |= (di >= s) & (di <= e)
        memb[tk] = col

    out = {}
    for d in dates:
        if bool(entry_blocked.get(d, False)):
            out[d] = None
            continue
        elig = (sig.loc[d].fillna(False).astype(bool)
                & (mom.loc[d] >= min_floor)
                & memb.loc[d])
        vals = rnk.loc[d][elig].dropna()
        if vals.empty:
            out[d] = None
            continue
        out[d] = vals.rank(ascending=False, method="min")  # 1 = best
    counts = {d: (len(v) if v is not None else 0) for d, v in out.items()}

    res = []
    for t in trades.itertuples():
        d = pd.Timestamp(t.entry_date)
        r = out.get(d)
        if r is None or t.ticker not in r.index or counts[d] < 2:
            res.append(np.nan)
        else:
            res.append(100.0 * (r[t.ticker] - 1) / (counts[d] - 1))
    return pd.Series(res, index=trades.index)


def main() -> None:
    pool, _ = load_pool()
    series = build_series(pool)

    gspc = get_price_df("^GSPC", days=8000).set_index("date")
    gspc.index = pd.to_datetime(gspc.index)
    gspc_close = gspc["close"].astype(float)

    for label, path in TRADE_FILES.items():
        td = pd.read_csv(path)
        td["entry_date"] = pd.to_datetime(td["entry_date"])
        td["exit_date"] = pd.to_datetime(td["exit_date"])
        print(f"\n{label}: {len(td):,} trades from {path.name}", flush=True)

        m = per_trade_metrics(td, series)
        td = pd.concat([td, m], axis=1)

        # Sanity: the engine enters at the close, so entry_price must equal the
        # stored close on that date. A mismatch would invalidate every metric.
        ok = (td["entry_close"] - td["entry_price"]).abs() < 1e-6
        print(f"  entry_price == price_data close: {int(ok.sum()):,}/{len(td):,}", flush=True)

        rank_col = "relative_rank" if label.startswith("B") else "momentum"
        t0 = time.time()
        td["rank_pct_within_day"] = rank_within_day(td, series, gspc_close, rank_col)
        print(f"  rank-within-day computed ({time.time()-t0:.0f}s), "
              f"{td['rank_pct_within_day'].notna().sum():,} resolved", flush=True)

        td.to_csv(HERE / f"metrics_{label}.csv", index=False)
        print(f"  wrote metrics_{label}.csv", flush=True)


if __name__ == "__main__":
    main()
