"""Reset-at-boundary sweep: baseline (real, unmodified engine) vs each
band-conditioned sizing-multiplier schedule. Convention: RESET-AT-BOUNDARY
(a variant comparison, per CLAUDE.md's "Two backtest conventions" standing
requirement) -- test period 2020-01-01 to 2026-08-03, live universe (503
candidates), mark-to-market, current production per_trade_pct=0.05 /
max_total_deployed=0.5, everything else at current production config.

Schedules tested -- a small, specific, bounded set, exact numeric values
stated as literal code (per the task's own instruction not to open this
into a sweep):

    SCHEDULE_MODEST      = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.15, "Very Strong": 1.3}
    SCHEDULE_AGGRESSIVE  = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.3,  "Very Strong": 1.6}
    SCHEDULE_WHOLE_DIST  = {"Weak": 1.0, "Moderate": 1.1, "Strong": 1.2,  "Very Strong": 1.35}

("—" -- the "insufficient candidates to form 4 bands" fallback label -- is
implicitly 1.0 in every schedule via band_multiplier.get(label, 1.0); this
essentially never binds with a 503-ticker live pool, confirmed in
downstream_analysis.py.)
"""
import json
import pickle
import sys
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05')

import pandas as pd
import yaml

from analysis.backtest import slice_ticker, simulate_portfolio_compounding_mtm
from multiplier_engine import simulate_portfolio_compounding_mtm_multiplier

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

RESEARCH_DIR = r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05'
TEST_START = pd.Timestamp("2020-01-01")
TEST_END = pd.Timestamp("2026-08-03")

SCHEDULE_MODEST     = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.15, "Very Strong": 1.3}
SCHEDULE_AGGRESSIVE = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.3,  "Very Strong": 1.6}
SCHEDULE_WHOLE_DIST = {"Weak": 1.0, "Moderate": 1.1, "Strong": 1.2,  "Very Strong": 1.35}

SCHEDULES = {
    "baseline": None,
    "modest": SCHEDULE_MODEST,
    "aggressive": SCHEDULE_AGGRESSIVE,
    "whole_dist": SCHEDULE_WHOLE_DIST,
}

PER_TRADE_PCT = float(CONFIG["trading212_invest"]["per_trade_pct"])
MAX_DEPLOYED_PCT = float(CONFIG["trading212_invest"]["max_total_deployed"])


def _slice_caches(caches, start, end):
    out = []
    for c in caches:
        out.append(slice_ticker(c, start, end) if c is not None else None)
    return out


def run_one(name, schedule, caches_sliced, gspc_close):
    t0 = time.time()
    if name == "baseline":
        trades = simulate_portfolio_compounding_mtm(
            caches_sliced, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
        )
        stats = []
    else:
        trades, stats = simulate_portfolio_compounding_mtm_multiplier(
            caches_sliced, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
            band_multiplier=schedule, collect_stats=True,
        )
    elapsed = time.time() - t0
    print(f"  {name}: {len(trades)} trades, {elapsed:.0f}s", flush=True)
    with open(f"{RESEARCH_DIR}/results/reset_{name}.json", "w") as f:
        json.dump(trades, f, default=str)
    if stats:
        with open(f"{RESEARCH_DIR}/results/reset_{name}_stats.json", "w") as f:
            json.dump(stats, f, default=str)
    return trades, stats


if __name__ == "__main__":
    print("Loading caches...", flush=True)
    with open(f"{RESEARCH_DIR}/caches.pkl", "rb") as f:
        data = pickle.load(f)
    caches, gspc_close_full = data["caches"], data["gspc_close_full"]

    caches_sliced = _slice_caches(caches, TEST_START, TEST_END)
    gspc_close = gspc_close_full[(gspc_close_full.index >= TEST_START) & (gspc_close_full.index <= TEST_END)]
    print(f"per_trade_pct={PER_TRADE_PCT}, max_deployed_pct={MAX_DEPLOYED_PCT}", flush=True)

    for name, schedule in SCHEDULES.items():
        run_one(name, schedule, caches_sliced, gspc_close)

    print("DONE", flush=True)
