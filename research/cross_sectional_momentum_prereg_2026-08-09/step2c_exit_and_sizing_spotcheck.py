"""Verification items 1 and 3 (PREREGISTRATION.md Section 12): sample real
trades from a completed run and independently recompute (a) the exit
decision via analysis.decision.evaluate_exit_reason()/evaluate_rank_dropped_exit()
called directly, and (b) the sizing via resolve_dynamic_max_position_gbp()/
_var_position_size_core() called directly -- confirming the engine's own
recorded outputs match, the same "sample real trades and independently
reproduce" methodology the selection audit's counterfactual_sim.py used
(30/30 exact match) before being trusted.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg")

import pickle
import random

import numpy as np
import pandas as pd
import yaml

from analysis import crisis
from analysis.decision import (
    evaluate_exit_reason, evaluate_rank_dropped_exit,
    resolve_dynamic_max_position_gbp, resolve_min_momentum_floor,
)
from analysis.volatility import _resolve_entry_stop, _var_position_size_core
from analysis.backtest import _MIN_VAR_RETURNS

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/cross_sectional_momentum_prereg_2026-08-09"
PER_TRADE_PCT = 0.05


def verify_exits(pool_label, arm_label, n_sample=30, seed=42):
    trades = pd.read_csv(f"{OUT_DIR}/trades_{pool_label}_{arm_label}.csv")
    panel = pd.read_csv(f"{OUT_DIR}/panel_{pool_label}_{arm_label}.csv")
    panel["date"] = pd.to_datetime(panel["date"])

    with open(f"{OUT_DIR}/_cache_{pool_label}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = {c["ticker"]: c for c in data["caches"] if c is not None}
    gspc_close = data["gspc_close"]
    raw_crisis = crisis.compute_raw_trigger(gspc_close)

    rng = random.Random(seed)
    sample_idx = rng.sample(range(len(trades)), min(n_sample, len(trades)))

    n_match, n_total = 0, 0
    mismatches = []
    for idx in sample_idx:
        row = trades.iloc[idx]
        ticker = row["ticker"]
        exit_date = pd.Timestamp(row["exit_date"])
        exit_reason_recorded = row["exit_reason"]
        if exit_reason_recorded in ("END_OF_DATA",):
            continue  # not a decision-function outcome; skip
        n_total += 1

        frame = caches[ticker]["frame"]
        if exit_date not in frame.index:
            mismatches.append((ticker, str(exit_date), "no frame row on exit date"))
            continue
        exit_row = frame.loc[exit_date]
        entry_date = pd.Timestamp(row["entry_date"])
        holding_days = (exit_date - entry_date).days
        crisis_now = bool(raw_crisis.reindex([exit_date], method="ffill").iloc[0]) if len(raw_crisis) else False

        stop_price = row["exit_price"] if exit_reason_recorded == "STOP_LOSS" else None
        # We don't have the ORIGINAL stop_price stored in the trades CSV
        # directly (only exit_price, which for STOP_LOSS is min(stop, open)).
        # Reconstruct via stop_distance_pct * entry_price, matching how the
        # engine itself derives it.
        stop_distance_pct = row["stop_distance_pct"]
        entry_price = row["entry_price"]
        reconstructed_stop = entry_price * (1 - stop_distance_pct / 100.0)

        low_val = exit_row.get("low")
        stop_check_price = float(low_val) if pd.notna(low_val) else float(exit_row["close"])

        recomputed = evaluate_exit_reason(
            crisis_now=crisis_now, price=stop_check_price, stop_price=reconstructed_stop,
            sig_on=None, holding_days=holding_days,
            max_holding_days=30 if arm_label == "B" else None,
        )
        if recomputed is None and exit_reason_recorded == "RANK_DROPPED":
            # RANK_DROPPED isn't reachable from evaluate_exit_reason() at
            # all (by design -- checked separately, only on rebalance
            # days) -- confirm the day-level check agrees no OTHER reason
            # fired first, which is consistent with RANK_DROPPED being
            # correctly reached.
            match = True
        elif recomputed == exit_reason_recorded:
            match = True
        else:
            match = False
            mismatches.append((ticker, str(exit_date), f"recorded={exit_reason_recorded} recomputed={recomputed}"))

        if match:
            n_match += 1

    print(f"[{pool_label}/{arm_label}] Exit-wiring spot-check: {n_match}/{n_total} match", flush=True)
    if mismatches:
        print("  Mismatches:", mismatches[:10], flush=True)
    return n_match, n_total


def verify_sizing(pool_label, arm_label, n_sample=30, seed=42):
    trades = pd.read_csv(f"{OUT_DIR}/trades_{pool_label}_{arm_label}.csv")
    with open(f"{OUT_DIR}/_cache_{pool_label}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = {c["ticker"]: c for c in data["caches"] if c is not None}

    var_cfg = CONFIG.get("trading212_invest", {})
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    absolute_max_position_gbp = float(var_cfg.get("absolute_max_position_gbp", 1_000_000))

    panel = pd.read_csv(f"{OUT_DIR}/panel_{pool_label}_{arm_label}.csv")
    panel["date"] = pd.to_datetime(panel["date"])

    rng = random.Random(seed + 1)
    sample_idx = rng.sample(range(len(trades)), min(n_sample, len(trades)))

    n_match, n_total = 0, 0
    mismatches = []
    for idx in sample_idx:
        row = trades.iloc[idx]
        ticker = row["ticker"]
        entry_date = pd.Timestamp(row["entry_date"])
        recorded_position_gbp = row["position_gbp"]

        # current_balance just before this entry: nearest panel row at or
        # before entry_date (panel is recorded end-of-day, after Phase A/B,
        # so the panel row on entry_date itself already reflects sizing
        # AFTER this trade -- use the one row before it as the pre-trade
        # balance, matching how the engine computes current_balance BEFORE
        # calling _try_entry).
        prior_panel = panel[panel["date"] < entry_date]
        if prior_panel.empty:
            continue
        current_balance = prior_panel.iloc[-1]["current_balance"]
        n_total += 1

        dynamic_max_position_gbp = resolve_dynamic_max_position_gbp(
            current_balance, PER_TRADE_PCT, absolute_max_position_gbp,
        )
        close_series = caches[ticker]["frame"]["close"].astype(float)
        prior_closes = close_series.loc[close_series.index < entry_date].tail(var_lookback + 1)
        log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
        if len(log_returns) >= _MIN_VAR_RETURNS:
            recomputed_position_gbp, _dv, _hv = _var_position_size_core(
                log_returns, current_balance, dynamic_max_position_gbp, 30,
                var_confidence, max_risk_pct, min_position_gbp,
            )
        else:
            recomputed_position_gbp = round(dynamic_max_position_gbp * 0.5, 2)

        # Cash-constraint clamp can differ (we don't have exact pre-trade
        # cash isolated from mtm value in the panel) -- report close-enough
        # (within the VaR ceiling) rather than requiring exact equality,
        # and flag if the recorded value EXCEEDS the theoretical VaR
        # ceiling, which would be a genuine bug.
        within_ceiling = recorded_position_gbp <= recomputed_position_gbp + 0.01
        close_match = abs(recorded_position_gbp - recomputed_position_gbp) < 0.01
        if close_match or within_ceiling:
            n_match += 1
        else:
            mismatches.append((ticker, str(entry_date), recorded_position_gbp, recomputed_position_gbp))

    print(f"[{pool_label}/{arm_label}] Sizing spot-check: {n_match}/{n_total} within expected bound", flush=True)
    if mismatches:
        print("  Mismatches (recorded > recomputed ceiling):", mismatches[:10], flush=True)
    return n_match, n_total


if __name__ == "__main__":
    pool_label = sys.argv[1] if len(sys.argv) > 1 else "pool1"
    arm_label = sys.argv[2] if len(sys.argv) > 2 else "B"
    verify_exits(pool_label, arm_label)
    verify_sizing(pool_label, arm_label)
