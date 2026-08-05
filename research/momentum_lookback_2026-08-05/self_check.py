"""Fidelity + decision-engine-parity check for the lookback sweep.

PART 1: patching the lookback to its production value (252 / anchor gap 400)
and running the sweep's own pipeline must reproduce the real, unpatched
analysis.backtest.simulate_portfolio_compounding_mtm() BYTE-IDENTICALLY.
This proves the monkeypatch mechanism itself introduces nothing.

PART 2: the patch must ACTUALLY take effect for other values -- a patch that
silently didn't apply would also pass Part 1 and would make the whole sweep
a no-op producing seven identical arms.

PART 3: decision-engine parity, per CLAUDE.md's "Research scripts must verify
decision-engine parity, not inherit it by assumption" -- this script
monkeypatches a module constant, which is exactly the interception shape that
rule exists for. Evidence gathered from this run's own trades, not inferred.

PART 4: the scaled anchor guard must not be silently destroying the signal at
long lookbacks -- the specific failure this sweep's design exists to avoid.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "risk_adjusted_momentum_2026-08-05"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import yaml

import analysis.signals as signals
import analysis.universe as universe
from analysis.backtest import precompute_ticker, simulate_portfolio_compounding_mtm
from analysis.universe import get_max_holding_days
from analysis.decision import evaluate_exit_reason
from data.ingestion import get_price_df
from risk_adjusted_engine import simulate_with_risk_adjusted_ranking
from lookback_worker import apply_lookback, months_to_days

CONFIG_PATH = ROOT / "config.yaml"


def main():
    config = yaml.safe_load(open(CONFIG_PATH, encoding="utf-8"))
    gspc_df = get_price_df("^GSPC", days=3000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close = gspc_df["close"].astype(float)
    tickers = universe.get_live_stock_candidates(as_of=pd.Timestamp("2026-08-03").date())[:60]
    t212 = config["trading212_invest"]
    ptp, mdp = float(t212["per_trade_pct"]), float(t212["max_total_deployed"])

    # ---- PART 1: unpatched real pipeline vs patched-to-252 -----------------
    print(f"production constants: lookback={signals.MOMENTUM_LOOKBACK_DAYS} "
          f"anchor_gap={signals._MAX_ANCHOR_GAP_DAYS}")
    caches_real = [precompute_ticker(t, config) for t in tickers]
    real = simulate_portfolio_compounding_mtm(caches_real, config, gspc_close, ptp, mdp)

    gap = apply_lookback(252)
    print(f"patched to 12m: lookback={signals.MOMENTUM_LOOKBACK_DAYS} anchor_gap={gap}")
    caches_p = [precompute_ticker(t, config) for t in tickers]
    patched = simulate_with_risk_adjusted_ranking(
        caches_p, config, gspc_close, ptp, mdp,
        corr_haircut_threshold=None, corr_haircut_factor=1.0, rank_mode="raw")

    identical = real == patched
    print(f"real trades={len(real)} patched-12m trades={len(patched)}")
    print(f"PART 1 BYTE-IDENTICAL AT PRODUCTION LOOKBACK: {identical}")
    if not identical:
        for i, (a, b) in enumerate(zip(real, patched)):
            if a != b:
                print("FIRST DIFF", i, "\n real:", a, "\n patched:", b)
                break
        sys.exit(1)

    # ---- PART 2: the patch genuinely takes effect --------------------------
    close = get_price_df(tickers[0], days=8000).set_index("date")["close"].astype(float)
    close.index = pd.to_datetime(close.index)
    sigs = {}
    for m in (3, 12, 24):
        apply_lookback(months_to_days(m))
        mom = signals.compute_momentum_series(close)
        sigs[m] = (signals.MOMENTUM_LOOKBACK_DAYS, signals._MAX_ANCHOR_GAP_DAYS,
                   int(mom.notna().sum()), round(float(mom.dropna().std()), 5))
        print(f"PART 2 {m:>2}m -> lookback={sigs[m][0]:>3}d gap={sigs[m][1]:>3}d "
              f"non-NaN={sigs[m][2]} std={sigs[m][3]}")
    distinct = len({v[3] for v in sigs.values()}) == 3
    print(f"PART 2 PATCH GENUINELY CHANGES THE SIGNAL: {distinct}")

    # ---- PART 3: decision-engine parity evidence ---------------------------
    apply_lookback(months_to_days(24))
    caches24 = [precompute_ticker(t, config) for t in tickers]
    tr24 = simulate_with_risk_adjusted_ranking(
        caches24, config, gspc_close, ptp, mdp,
        corr_haircut_threshold=None, corr_haircut_factor=1.0, rank_mode="raw")
    viol = [t for t in tr24
            if t["exit_reason"] == "MAX_HOLDING"
            and (pd.Timestamp(t["exit_date"]) - pd.Timestamp(t["entry_date"])).days
                < get_max_holding_days(t["ticker"], config)]
    allowed = {"CRISIS_EXIT", "STOP_LOSS", "SIGNAL_OFF", "MAX_HOLDING", "END_OF_DATA"}
    reasons = sorted({t["exit_reason"] for t in tr24})
    over = [t for t in tr24
            if t["position_gbp"] > t.get("account_value_at_entry", float("inf")) * ptp + 0.01]
    print(f"PART 3a holding-period violations (24m arm): {len(viol)}/{len(tr24)}")
    print(f"PART 3b exit reasons {reasons} within shared vocabulary: {set(reasons) <= allowed}")
    print(f"PART 3c shared exit priority: "
          f"{[evaluate_exit_reason(crisis_now=c, price=p, stop_price=60.0, sig_on=s, holding_days=h, max_holding_days=30) for c,p,s,h in [(True,50.0,False,999),(False,50.0,False,999),(False,70.0,False,999),(False,70.0,True,999),(False,70.0,True,1)]]}")
    print(f"PART 3d sizing over per_trade_pct ceiling: {len(over)}")

    # ---- PART 4: long lookbacks are not silently signal-dead ---------------
    print("PART 4 signal liveness by lookback (fraction of rows with a real signal):")
    for m in (3, 6, 9, 12, 15, 18, 24):
        apply_lookback(months_to_days(m))
        c = [precompute_ticker(t, config) for t in tickers[:10]]
        on = sum(int(x["frame"]["signal"].sum()) for x in c if x)
        rows = sum(len(x["frame"]) for x in c if x)
        nn = sum(int(x["frame"]["momentum"].notna().sum()) for x in c if x)
        print(f"   {m:>2}m: signal_on={on:>6}/{rows} ({on/rows*100:5.1f}%)  "
              f"momentum_non_nan={nn/rows*100:5.1f}%")


if __name__ == "__main__":
    main()
