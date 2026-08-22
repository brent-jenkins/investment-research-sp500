"""Build Pool 1 and Pool 2 CLEAN caches. Uses production precompute_ticker()
unmodified -- the sweep needs only close/open/low/atr/signal/momentum/relative_rank,
all of which it already returns. (`high` is NOT needed: the trailing high-water
mark uses the CLOSE by design, Sec 4.)

atr_stop_multiplier and stock_max_holding_days are read fresh at simulation time
(_resolve_entry_stop / get_max_holding_days), never baked into the cache --
confirmed by reading those functions, so one cache serves every cell.
"""
import pickle
import sys
import time
from pathlib import Path

import pandas as pd
import yaml
from sqlalchemy import text

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis import universe                                    # noqa: E402
from analysis.backtest import precompute_ticker, slice_ticker    # noqa: E402
from data.database import get_engine                             # noqa: E402
from data.ingestion import get_price_df                          # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)
START = pd.Timestamp("2007-01-01")


def build(tickers, label, end):
    t0 = time.time()
    caches, ok = [], 0
    for i, tk in enumerate(tickers):
        c = precompute_ticker(tk, CONFIG)
        if c is not None:
            c = slice_ticker(c, START, end)
        if c is not None:
            ok += 1
        caches.append(c)
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(tickers)} ({time.time()-t0:.0f}s)", flush=True)
    g = get_price_df("^GSPC", days=8000).set_index("date")
    g.index = pd.to_datetime(g.index)
    gc = g["close"].astype(float)
    with open(HERE / f"_cache_{label}.pkl", "wb") as fh:
        pickle.dump({"caches": caches, "gspc_close": gc[(gc.index >= START) & (gc.index <= end)],
                     "tickers": tickers, "end": end}, fh)
    print(f"{label}: {ok}/{len(tickers)} usable ({time.time()-t0:.0f}s)", flush=True)


def main() -> None:
    eng = get_engine()
    with eng.connect() as conn:
        end = pd.Timestamp(conn.execute(text("SELECT MAX(date) FROM price_data")).fetchone()[0])
        clean = {r[0] for r in conn.execute(
            text("SELECT ticker FROM ticker_data_quality WHERE status='CLEAN'"))}
    print(f"price_data through {end.date()}; CLEAN={len(clean)} (fresh queries)", flush=True)
    p2 = sorted(set(universe.get_research_stock_candidates()) & clean)
    p1 = sorted(universe.get_live_stock_candidates())
    print(f"Pool 2 CLEAN={len(p2)}  Pool 1={len(p1)}", flush=True)
    build(p2, "pool2_clean", end)
    build(p1, "pool1", end)


if __name__ == "__main__":
    main()
