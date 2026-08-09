"""Promotion requirement 4: re-run the relative-ranking study's own Arm A /
Arm B comparison via the PRODUCTION code path (analysis/backtest.py's real,
unmodified simulate_portfolio_compounding_mtm(), never
research/relative_ranking_prereg_2026-08-09/relative_ranking_engine.py) on
Pool 2 CLEAN, and confirm it reproduces the study's own trade files. If it
doesn't reproduce, stop and report -- a discrepancy means the promotion
changed behaviour relative to the research finding it's promoting, and that
has to be understood before anything ships.

Arm A = admission_ranking_mode absent (production default). Arm B =
admission_ranking_mode="relative_rank" (the promoted mode). Both otherwise
identical to the study's own step2_run_arms.py: PER_TRADE_PCT=0.05,
MAX_DEPLOYED_PCT=0.70, Pool 2 CLEAN, START=2007-01-01.
"""
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import yaml
from sqlalchemy import text

from analysis import universe
from analysis.backtest import precompute_ticker, slice_ticker, simulate_portfolio_compounding_mtm
from data.database import get_engine
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/relative_ranking_promotion_verify_2026-08-09"
STUDY_DIR = "research/relative_ranking_prereg_2026-08-09"
START = pd.Timestamp("2007-01-01")
PER_TRADE_PCT = 0.05
MAX_DEPLOYED_PCT = 0.70


def _build_pool2_clean_cache():
    engine = get_engine()
    with engine.connect() as conn:
        end = pd.Timestamp(conn.execute(text("SELECT MAX(date) FROM price_data")).scalar())
        clean = {row[0] for row in conn.execute(text(
            "SELECT ticker FROM ticker_data_quality WHERE status = 'CLEAN'"
        ))}
    pool2_clean = sorted(set(universe.get_research_stock_candidates()) & clean)
    print(f"Pool 2 CLEAN: {len(pool2_clean)} tickers, price data through {end.date()}", flush=True)

    t0 = time.time()
    caches = []
    n_ok = 0
    for i, ticker in enumerate(pool2_clean):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            c = slice_ticker(c, START, end)
        if c is not None:
            n_ok += 1
        caches.append(c)
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(pool2_clean)} done ({time.time()-t0:.0f}s, {n_ok} usable)", flush=True)
    print(f"  {n_ok}/{len(pool2_clean)} usable ({time.time()-t0:.0f}s)", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= end)]

    with open(f"{OUT_DIR}/_cache_pool2_clean.pkl", "wb") as fh:
        pickle.dump({"caches": caches, "gspc_close": gspc_close, "pool2_clean": pool2_clean, "end": end}, fh)
    return caches, gspc_close


def _run_arm(caches, gspc_close, admission_ranking_mode):
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in CONFIG.items()}
    cfg["trading212_invest"] = dict(cfg["trading212_invest"])
    if admission_ranking_mode is not None:
        cfg["trading212_invest"]["admission_ranking_mode"] = admission_ranking_mode
    else:
        cfg["trading212_invest"].pop("admission_ranking_mode", None)
    trades = simulate_portfolio_compounding_mtm(
        caches, cfg, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT, collect_rejections=False,
    )
    return pd.DataFrame(trades)


def _compare(label, produced_df, study_csv_path):
    """Order-independent comparison -- simulate_portfolio_compounding_mtm()'s
    own trade list is not guaranteed to come back in the same row order
    across two separate runs (it's built by a day-by-day event walk, not a
    stable per-ticker sort), so comparing raw insertion order produced a
    false-positive "843 row-level differences" on the first version of this
    function despite trade count and total pnl_gbp matching exactly to the
    penny -- caught by checking totals first and finding them identical,
    then re-comparing after sorting both frames onto a canonical
    (ticker, entry_date, exit_date) order, which showed 0 real differences.
    Numeric columns compared with a small tolerance (float round-trip
    through CSV), string columns compared exactly."""
    study_df = pd.read_csv(study_csv_path)
    print(f"\n=== {label} ===")
    print(f"  produced: {len(produced_df)} trades, total pnl_gbp={produced_df['pnl_gbp'].sum():.2f}")
    print(f"  study:    {len(study_df)} trades, total pnl_gbp={study_df['pnl_gbp'].sum():.2f}")
    if len(produced_df) != len(study_df):
        print(f"  MISMATCH: trade count differs ({len(produced_df)} vs {len(study_df)})")
        return False
    cols = [c for c in ("ticker", "entry_date", "exit_date", "exit_reason", "position_gbp", "pnl_gbp") if c in study_df.columns]
    str_cols = ["ticker", "entry_date", "exit_date", "exit_reason"]
    num_cols = ["position_gbp", "pnl_gbp"]
    p = produced_df[cols].sort_values(["ticker", "entry_date", "exit_date"]).reset_index(drop=True)
    s = study_df[cols].sort_values(["ticker", "entry_date", "exit_date"]).reset_index(drop=True)
    same_str = (p[str_cols].astype(str) == s[str_cols].astype(str)).all(axis=1)
    close_num = (p[num_cols].sub(s[num_cols]).abs() < 1e-6).all(axis=1)
    match_mask = same_str & close_num
    n_diff = int((~match_mask).sum())
    if n_diff > 0:
        print(f"  MISMATCH: {n_diff} row-level differences (of {len(p)}), after canonical sort")
        print(p[~match_mask].head(10))
        print(s[~match_mask].head(10))
        return False
    print("  MATCH: 0 row-level mismatches after canonical (ticker, entry_date, exit_date) sort")
    return True


def main():
    caches, gspc_close = _build_pool2_clean_cache()

    trades_a = _run_arm(caches, gspc_close, admission_ranking_mode=None)
    trades_a.to_csv(f"{OUT_DIR}/trades_pool2_clean_A_reproduced.csv", index=False)
    match_a = _compare("Arm A (absolute_momentum, production default)", trades_a, f"{STUDY_DIR}/trades_pool2_clean_A.csv")

    trades_b = _run_arm(caches, gspc_close, admission_ranking_mode="relative_rank")
    trades_b.to_csv(f"{OUT_DIR}/trades_pool2_clean_B_reproduced.csv", index=False)
    match_b = _compare("Arm B (relative_rank, promoted mode)", trades_b, f"{STUDY_DIR}/trades_pool2_clean_B.csv")

    print(f"\nArm A reproduces study: {match_a}")
    print(f"Arm B reproduces study: {match_b}")


if __name__ == "__main__":
    main()
