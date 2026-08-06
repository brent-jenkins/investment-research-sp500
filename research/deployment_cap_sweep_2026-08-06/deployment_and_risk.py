"""Realised-vs-permitted deployment, and joint-risk-gap, per cap value.

Two independent things are computed here, both required by the task spec
and neither optional at this cap range:

1. REALISED DEPLOYMENT (mean/median daily deployed %, mean concurrent
   positions). Reconstructed by exactly replaying each block's own trade
   list against the SAME account-model formulas
   simulate_portfolio_compounding_mtm() documents and uses internally
   (cash -= position_gbp on entry, cash += position_gbp + pnl_gbp on exit,
   open-position mark-to-market value = position_gbp * price(day)/
   entry_price, balance = cash + mtm, deployed = mtm). This is an EXACT
   replay of the real trades the production function actually produced,
   not an approximation or a separate re-simulation -- the trades
   themselves are the ground truth, and cash/mtm are fully determined by
   them plus the price series. Snapshot taken END OF DAY (after that
   day's exits AND entries) -- stated explicitly since the engine's own
   internal admission check instead uses a PRE-entry snapshot; the two
   answer different questions (this section answers "how much of the
   account is actually invested," not "what did the engine see when
   deciding whether to admit a candidate").

   "Share of days the cap actually bound" uses a DIFFERENT, more direct
   signal: the real REJECTED_CAPACITY rows the production function itself
   recorded (collect_rejections=True) -- the exact days on which a real
   candidate was turned away for lack of capacity, not a deployed%
   threshold heuristic.

2. JOINT-RISK-GAP (indep_gap_60d), reusing research/deployment_cap_
   tradeoff_2026-08-05/joint_risk_engine.py's own formula VERBATIM (per
   this task spec's explicit instruction to use "the existing definition
   from the concentration-risk investigation"). That function only needs
   a trades list + a log-return panel + a calendar; it does not touch
   cash/capital at all, so it is safe to run across ALL 16 blocks'
   concatenated trades on one continuous 2007-2026 calendar even though
   the underlying backtest is reset-at-boundary -- adjacent blocks' open
   positions never overlap in real calendar time by construction, so
   nothing is double-counted or blended across a reset.
"""
import json
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
RESULTS = HERE / "results"
CAPS = [0.50, 0.70, 0.95]
WLEN = 60
MIN_OBS = 35  # verbatim from concentration_risk_2026-08-05 / joint_risk_engine.py


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cap_tag(cap):
    return str(cap).replace(".", "")


# ---------------------------------------------------------------------------
# 1. Realised daily deployment -- exact replay per block
# ---------------------------------------------------------------------------

def replay_block_deployment(block_trades: pd.DataFrame, price_lookup: dict,
                             bidx: pd.DatetimeIndex, capital: float) -> pd.DataFrame:
    """Exact replay of one block's own real trades against the account
    model simulate_portfolio_compounding_mtm() documents. Returns one row
    per day in bidx: cash, mtm, balance, deployed_pct, n_open."""
    entries_by_day: dict = {}
    exits_by_day: dict = {}
    for r in block_trades.itertuples():
        entries_by_day.setdefault(r.entry_date, []).append(r)
        exits_by_day.setdefault(r.exit_date, []).append(r)

    cash = capital
    active: dict = {}  # ticker -> (position_gbp, entry_price)
    rows = []
    for d in bidx:
        d_date = d.date() if hasattr(d, "date") else d
        # ENTRIES before EXITS. A block's last day can hold a trade whose
        # entry_date == exit_date (a fresh admission that the block-end
        # END_OF_DATA backstop force-closes the same day it opened -- the
        # production engine's Phase B admission runs before its outer
        # backstop loop). Exits-before-entries left such a trade's exit
        # event firing as a no-op (not yet in `active`) and its entry
        # event then leaving it stuck in `active` forever -- silently
        # dropping its cash credit AND inflating every later day's n_open
        # (materially, in the continuous joint-risk panel, since nothing
        # ever removes a stuck phantom until/unless that same ticker
        # trades again). Entries-first makes the round-trip net correctly
        # to its own P&L with no leftover position, and is provably safe
        # for every other case: the production engine's own
        # `flat_before_today` rule already forbids a ticker with a
        # position carried in from a prior day from also getting a fresh
        # entry today, so entry+exit can only coincide on the same ticker
        # in this same-day-round-trip case.
        for r in entries_by_day.get(d_date, []):
            cash -= r.position_gbp
            active[r.ticker] = (r.position_gbp, r.entry_price)
        for r in exits_by_day.get(d_date, []):
            if r.ticker in active:
                cash += r.position_gbp + r.pnl_gbp
                del active[r.ticker]

        mtm = 0.0
        for ticker, (pos_gbp, entry_price) in active.items():
            px = price_lookup.get(ticker, {}).get(d)
            if px is None or entry_price <= 0:
                mtm += pos_gbp
            else:
                mtm += pos_gbp * (px / entry_price)
        balance = cash + mtm
        deployed_pct = (mtm / balance) if balance > 0 else 0.0
        rows.append({"date": d, "cash": cash, "mtm": mtm, "balance": balance,
                     "deployed_pct": deployed_pct, "n_open": len(active)})
    return pd.DataFrame(rows)


def build_price_lookup(cache: dict, tickers: set) -> dict:
    """ticker -> {Timestamp: close} for fast per-day lookup, ffilled onto
    a daily grid so a stale (non-trading) day still resolves to the last
    known price, mirroring the engine's own last_price persistence."""
    out = {}
    for t in tickers:
        c = cache.get(t)
        if c is None or c.get("frame") is None:
            continue
        s = c["frame"]["close"].astype(float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
        out[t] = s.reindex(full_idx).ffill().to_dict()
    return out


def deployment_summary_for_cap(cap: float, cache: dict, blocks_meta: list) -> dict:
    tag = cap_tag(cap)
    td = pd.read_csv(RESULTS / f"trades_cap{tag}.csv")
    if td.empty:
        return {"cap": cap, "error": "no trades"}
    td["entry_date"] = pd.to_datetime(td["entry_date"]).dt.date
    td["exit_date"] = pd.to_datetime(td["exit_date"]).dt.date

    rj = pd.read_csv(RESULTS / f"rejections_capacity_cap{tag}.csv")
    if not rj.empty:
        rj["date"] = pd.to_datetime(rj["date"]).dt.date

    tickers_needed = set(td["ticker"].unique())
    price_lookup = build_price_lookup(cache, tickers_needed)

    all_panels = []
    for bm in blocks_meta:
        bs, be, block_i = bm["start"], bm["end"], bm["block"]
        bidx = bm["bidx"]
        bt = td[td["block"] == block_i]
        panel = replay_block_deployment(bt, price_lookup, bidx, capital=50_000.0)
        panel["block"] = block_i
        all_panels.append(panel)
    full = pd.concat(all_panels, ignore_index=True)
    full.to_csv(RESULTS / f"deployment_panel_cap{tag}.csv", index=False)

    n_total_days = len(full)
    n_reject_days = rj["date"].nunique() if not rj.empty else 0
    # denominator: total distinct (block, day) pairs across all 16 blocks
    n_block_days = sum(len(bm["bidx"]) for bm in blocks_meta)

    return {
        "cap": cap,
        "mean_deployed_pct": round(float(full["deployed_pct"].mean()) * 100, 2),
        "median_deployed_pct": round(float(full["deployed_pct"].median()) * 100, 2),
        "p10_deployed_pct": round(float(full["deployed_pct"].quantile(0.10)) * 100, 2),
        "p90_deployed_pct": round(float(full["deployed_pct"].quantile(0.90)) * 100, 2),
        "mean_n_open": round(float(full["n_open"].mean()), 2),
        "median_n_open": float(full["n_open"].median()),
        "max_n_open": int(full["n_open"].max()),
        "n_capacity_reject_days_total": int(n_reject_days) if not rj.empty else 0,
        "n_block_days_total": int(n_block_days),
        "pct_days_cap_bound": round(100.0 * (rj["date"].nunique() if not rj.empty else 0) / n_block_days, 2),
        "n_capacity_rejections_total": int(len(rj)),
    }


# ---------------------------------------------------------------------------
# 2. Joint-risk-gap -- verbatim from deployment_cap_tradeoff_2026-08-05
# ---------------------------------------------------------------------------

def dist(series):
    s = pd.Series(series).dropna()
    if s.empty:
        return None
    return {
        "n": int(len(s)), "mean": float(s.mean()), "std": float(s.std()),
        "p5": float(s.quantile(0.05)), "p25": float(s.quantile(0.25)),
        "p50": float(s.quantile(0.50)), "p75": float(s.quantile(0.75)),
        "p95": float(s.quantile(0.95)), "min": float(s.min()), "max": float(s.max()),
    }


def compute_indep_gap_for_trades(trades_df: pd.DataFrame, log_ret_panel: pd.DataFrame,
                                  master_cal: pd.DatetimeIndex, day_pos: dict):
    """Verbatim adaptation of research/deployment_cap_tradeoff_2026-08-05/
    joint_risk_engine.py's compute_indep_gap_for_trades -- same formula,
    same WLEN/MIN_OBS, ported to read a DataFrame instead of a list of
    dicts (this script already has trades as a DataFrame from CSV)."""
    if trades_df.empty:
        return {"error": "no trades"}, pd.DataFrame()

    tdf = trades_df.copy()
    tdf["entry_date"] = pd.to_datetime(tdf["entry_date"])
    tdf["exit_date"] = pd.to_datetime(tdf["exit_date"])
    tdf["trade_id"] = np.arange(len(tdf))

    opens_by_date, closes_by_date = {}, {}
    for row in tdf.itertuples():
        opens_by_date.setdefault(row.entry_date, []).append(row)
        closes_by_date.setdefault(row.exit_date, []).append(row)

    active = {}
    rows = []
    cal_start = tdf["entry_date"].min()
    cal_end = tdf["exit_date"].max()
    window_cal = master_cal[(master_cal >= cal_start) & (master_cal <= cal_end)]

    for day in window_cal:
        pos_idx = day_pos.get(day)
        if pos_idx is None:
            continue
        # OPENS before CLOSES -- same fix, same reason, as
        # replay_block_deployment() above: a same-day entry+exit trade
        # (a block-end END_OF_DATA backstop closure on its own entry day)
        # must net to "not open" by end of day, not get stuck in `active`
        # forever because its close event fired before it existed. This
        # bug previously inflated n_open/indep_gap_60d for every day
        # after a phantom was created, until/unless that same ticker
        # traded again -- a much larger effect here than in the per-block
        # deployment reconstruction, since this function runs as one
        # continuous walk across all 16 blocks with no reset to clear a
        # stuck phantom.
        if day in opens_by_date:
            for row in opens_by_date[day]:
                active[row.ticker] = {"position_gbp": row.position_gbp, "trade_id": row.trade_id}
        if day in closes_by_date:
            for row in closes_by_date[day]:
                tk = row.ticker
                if tk in active and active[tk]["trade_id"] == row.trade_id:
                    del active[tk]

        n_open = len(active)
        open_tickers = list(active.keys())
        indep_gap = None
        n_pairs = None
        if n_open >= 2:
            lo = max(0, pos_idx - WLEN)
            hi = pos_idx
            if hi - lo >= MIN_OBS:
                sub = log_ret_panel.iloc[lo:hi][open_tickers] if all(
                    c in log_ret_panel.columns for c in open_tickers) else log_ret_panel.iloc[lo:hi][
                    [c for c in open_tickers if c in log_ret_panel.columns]]
                valid_cols = [c for c in open_tickers if c in sub.columns and sub[c].notna().sum() >= MIN_OBS]
                if len(valid_cols) >= 2:
                    weights = np.array([active[c]["position_gbp"] for c in valid_cols])
                    weights = weights / weights.sum()
                    vols = sub[valid_cols].std().values
                    cov = sub[valid_cols].cov().values
                    p_real = float(np.sqrt(weights @ cov @ weights))
                    p_ind = float(np.sqrt(np.sum((weights ** 2) * (vols ** 2))))
                    if p_real > 0 and p_ind > 0:
                        indep_gap = p_real / p_ind - 1.0
                    n_pairs = len(valid_cols) * (len(valid_cols) - 1) // 2
        rows.append({"date": day, "n_open": n_open, "indep_gap_60d": indep_gap, "n_pairs_60d": n_pairs})

    panel = pd.DataFrame(rows)
    summary = {
        "n_days_total": len(panel),
        "indep_gap_60d_distribution_n_open_ge_2": dist(panel.loc[panel["n_open"] >= 2, "indep_gap_60d"]),
        "mean_n_open": float(panel["n_open"].mean()),
        "max_n_open": int(panel["n_open"].max()),
    }
    return summary, panel


def main() -> None:
    log("Loading ticker caches ...")
    with open(HERE / "ticker_caches.pkl", "rb") as f:
        cache = pickle.load(f)

    sweep_rows = json.loads((RESULTS / "sweep_block_results_raw.json").read_text())
    blocks_meta = []
    seen = set()
    for r in sweep_rows:
        if r["block"] in seen:
            continue
        seen.add(r["block"])
        bidx = pd.bdate_range(r["start"], r["end"])
        blocks_meta.append({"block": r["block"],
                            "start": pd.Timestamp(r["start"]), "end": pd.Timestamp(r["end"]),
                            "bidx": bidx})
    blocks_meta.sort(key=lambda b: b["block"])

    log("Building full-history log-return panel for joint-risk-gap ...")
    all_tickers = sorted(cache.keys())
    master_cal = pd.date_range("2006-11-01", "2026-08-03", freq="D")
    price_panel = pd.DataFrame(index=master_cal)
    for t in all_tickers:
        c = cache.get(t)
        if c is None or c.get("frame") is None:
            continue
        s = c["frame"]["close"].astype(float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        price_panel[t] = s.reindex(master_cal).ffill(limit=5)
    log_ret_panel = np.log(price_panel / price_panel.shift(1))
    day_pos = {d: i for i, d in enumerate(master_cal)}
    log(f"  price panel shape {price_panel.shape}")

    deployment_results = {}
    risk_results = {}
    for cap in CAPS:
        tag = cap_tag(cap)
        log(f"cap={cap}: realised deployment ...")
        deployment_results[tag] = deployment_summary_for_cap(cap, cache, blocks_meta)
        log(f"  {deployment_results[tag]}")

        log(f"cap={cap}: joint-risk-gap ...")
        td = pd.read_csv(RESULTS / f"trades_cap{tag}.csv")
        summary, panel = compute_indep_gap_for_trades(td, log_ret_panel, master_cal, day_pos)
        risk_results[tag] = summary
        panel.to_csv(RESULTS / f"joint_risk_panel_cap{tag}.csv", index=False)
        log(f"  mean_indep_gap={summary.get('indep_gap_60d_distribution_n_open_ge_2', {}).get('mean')}")

    out = {"deployment": deployment_results, "joint_risk": risk_results}
    (RESULTS / "deployment_and_risk_summary.json").write_text(json.dumps(out, indent=2, default=str))
    log(f"Wrote {RESULTS/'deployment_and_risk_summary.json'}")


if __name__ == "__main__":
    main()
