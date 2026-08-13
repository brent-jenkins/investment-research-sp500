"""Feature screen, step 1 -- derive the 16 pre-registered entry-time features.

READ-ONLY. No backtest re-run. Same derivation approach as the stale-peak
diagnostic: features are computed from price_data at each trade's entry date
using production functions where they exist.

Feature list and justifications are fixed in PREREGISTRATION.md Sec 4 and were
committed before this file was written.
"""
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

from analysis import universe                                   # noqa: E402
from analysis.volatility import _atr                            # noqa: E402
from data.database import get_engine                            # noqa: E402
from data.ingestion import get_price_df                         # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

ATR_PERIOD = CONFIG["signals"]["atr_period"]
SRC = ROOT / "research" / "relative_ranking_prereg_2026-08-09"
TRADE_FILES = {
    "B_relative_rank": SRC / "trades_pool2_clean_B.csv",
    "A_absolute_momentum": SRC / "trades_pool2_clean_A.csv",
}


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    """Standard Wilder RSI (EMA-style smoothing, alpha = 1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).fillna(100.0)


def build(ticker: str):
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)
    logret = np.log(close / close.shift(1))

    adv20 = vol.rolling(20, min_periods=20).mean()
    adv60 = vol.rolling(60, min_periods=60).mean()
    ma50 = close.rolling(50, min_periods=50).mean()
    ma200 = close.rolling(200, min_periods=200).mean()

    return pd.DataFrame({
        "close": close,
        # volume
        "adv20": adv20,
        "adv60": adv60,
        "vol_ratio_entry": vol / adv20,
        "vol_trend": adv20 / adv60,
        "dollar_volume20": close * adv20,          # LIQUIDITY PROXY, not market cap
        # rsi
        "rsi14": rsi_wilder(close, 14),
        # volatility
        "atr_pct": _atr(high, low, close, ATR_PERIOD) / close,
        "realised_vol60": logret.rolling(60, min_periods=60).std() * np.sqrt(252.0),
        # price level
        "price_at_entry": close,
        # additional, justified in Sec 4
        "gap_to_ma50": close / ma50 - 1.0,
        "gap_to_ma200": close / ma200 - 1.0,
        "ma50_above_ma200": (ma50 > ma200).astype(float),
        "up_day_fraction60": (logret > 0).rolling(60, min_periods=60).mean(),
        "return_5d": close / close.shift(5) - 1.0,
        "return_21d": close / close.shift(21) - 1.0,
        "dist_from_52w_low": close / close.rolling(252, min_periods=252).min() - 1.0,
    })


FEATURES = ["adv20", "adv60", "vol_ratio_entry", "vol_trend", "dollar_volume20",
            "rsi14", "atr_pct", "realised_vol60", "price_at_entry",
            "gap_to_ma50", "gap_to_ma200", "ma50_above_ma200", "up_day_fraction60",
            "return_5d", "return_21d", "dist_from_52w_low"]


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        clean = {r[0] for r in conn.execute(
            text("SELECT ticker FROM ticker_data_quality WHERE status = 'CLEAN'"))}
        maxd = conn.execute(text("SELECT MAX(date) FROM price_data")).fetchone()[0]
    pool = sorted(set(universe.get_research_stock_candidates()) & clean)
    print(f"Pool 2 CLEAN: {len(pool)} tickers; price_data through {pd.Timestamp(maxd).date()}",
          flush=True)

    series, t0 = {}, time.time()
    for i, tk in enumerate(pool):
        f = build(tk)
        if f is not None:
            series[tk] = f
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(pool)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"  built {len(series)} ticker frames ({time.time()-t0:.0f}s)", flush=True)

    for label, path in TRADE_FILES.items():
        td = pd.read_csv(path)
        td["entry_date"] = pd.to_datetime(td["entry_date"])
        rows = []
        for t in td.itertuples():
            fr = series.get(t.ticker)
            if fr is None or pd.Timestamp(t.entry_date) not in fr.index:
                rows.append({f: np.nan for f in FEATURES} | {"close_chk": np.nan})
                continue
            r = fr.loc[pd.Timestamp(t.entry_date)]
            rows.append({f: float(r[f]) for f in FEATURES} | {"close_chk": float(r["close"])})
        out = pd.concat([td, pd.DataFrame(rows, index=td.index)], axis=1)

        # Anchoring check -- identical to the stale-peak diagnostic's.
        ok = (out["close_chk"] - out["entry_price"]).abs() < 1e-6
        print(f"\n{label}: {len(out):,} trades; entry_price == stored close: "
              f"{int(ok.sum()):,}/{len(out):,}", flush=True)
        cov = out[FEATURES].notna().mean() * 100
        print("  feature coverage (% non-null): " +
              ", ".join(f"{f}={cov[f]:.1f}" for f in FEATURES), flush=True)

        out.to_csv(HERE / f"features_{label}.csv", index=False)
        print(f"  wrote features_{label}.csv", flush=True)


if __name__ == "__main__":
    main()
