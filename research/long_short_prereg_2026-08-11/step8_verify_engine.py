"""Sec 11.3-11.8 -- engine verification against real arm output.

Evidence gathered from THIS study's own runs, not inherited from "the
production codebase is consolidated" (a fact about production, not about this
engine), per the standing requirement for research scripts that construct a
custom candidate pool or reach into the pipeline.

  11.3 short exit wiring   -- reasons recomputed; STOP_LOSS fired off `high`
                              and filled at max(stop, open); zero CRISIS_EXIT
                              on any short (Sec 3's design, asserted directly)
  11.4 sizing              -- against the engine's OWN emitted equity_at_entry
  11.5 borrow arithmetic   -- recomputed by hand from daily MTM notional
  11.7 signal separation   -- relative_rank never reaches a gate or evaluator
  11.8 short candidate set -- Sec 3 gate hand-computed on sampled dates

Usage: python step8_verify_engine.py <pool>
"""
import pickle
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from analysis import universe, crisis                                   # noqa: E402
from analysis.decision import (                                         # noqa: E402
    resolve_min_momentum_floor, resolve_dynamic_max_position_gbp,
    is_position_size_viable,
)
from analysis.volatility import _resolve_entry_stop, _var_position_size_core   # noqa: E402
from analysis.backtest import get_max_holding_days, _MIN_VAR_RETURNS           # noqa: E402
import ls_primitives as lsp                                             # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

SEED = 20260811
N_SAMPLE = 40
TOL = 0.02


def main() -> None:
    pool = sys.argv[1]
    with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
        cache = pickle.load(fh)
    frames = {c["ticker"]: c["frame"] for c in cache["caches"] if c is not None}
    gspc = cache["gspc_close"]
    rally_raw = lsp.compute_rally_raw_trigger(gspc)
    rally_block = lsp.extend_rally_trigger(rally_raw)
    min_floor = resolve_min_momentum_floor(CONFIG)
    var_cfg = CONFIG["trading212_invest"]
    failures, notes = [], []

    td = pd.read_csv(HERE / f"trades_{pool}_B_s1x.csv")
    td["entry_date"] = pd.to_datetime(td["entry_date"])
    td["exit_date"] = pd.to_datetime(td["exit_date"])
    shorts = td[td["direction"] == "SHORT"]
    print(f"{pool}: Arm B has {len(td):,} trades ({len(shorts):,} short)")

    # ---- 11.3a: no short may ever exit via CRISIS_EXIT (Sec 3 design) ----
    n_crisis = int((shorts["exit_reason"] == "CRISIS_EXIT").sum())
    print(f"\n11.3a  short trades with exit_reason=CRISIS_EXIT: {n_crisis} (must be 0)")
    if n_crisis:
        failures.append(f"{n_crisis} short trades exited via CRISIS_EXIT -- Sec 3 says the "
                        "crisis breaker blocks new short ENTRIES but never exits shorts")
    print(f"       short exit-reason mix: {shorts['exit_reason'].value_counts().to_dict()}")

    # ---- 11.3b: short exit reasons recomputed independently ----
    rng = random.Random(SEED)
    sample = shorts.sample(min(N_SAMPLE, len(shorts)), random_state=SEED) if len(shorts) else shorts
    n_ok = 0
    for _, t in sample.iterrows():
        fr = frames[t["ticker"]]
        dt = t["exit_date"]
        if dt not in fr.index:
            failures.append(f"{t['ticker']} exit date {dt.date()} not in frame")
            continue
        row = fr.loc[dt]
        hold = (dt - t["entry_date"]).days
        if bool(rally_raw.reindex(fr.index).ffill().fillna(False).loc[dt]):
            want = "RALLY_EXIT"
        else:
            want = lsp.evaluate_short_exit_reason(
                crisis_now=False, price=float(row["high"]), stop_price=float(t["entry_stop_price"]),
                sig_on=bool(row["momentum"] < 0) if pd.notna(row["momentum"]) else False,
                holding_days=hold, max_holding_days=get_max_holding_days(t["ticker"], CONFIG))
            if want is None and dt == fr.index[-1]:
                want = "END_OF_DATA"
        if want != t["exit_reason"]:
            failures.append(f"exit-reason mismatch {t['ticker']} {dt.date()}: "
                            f"engine={t['exit_reason']} recomputed={want}")
        else:
            n_ok += 1
        # STOP_LOSS must have fired off `high` and filled at max(stop, open)
        if t["exit_reason"] == "STOP_LOSS":
            if float(row["high"]) < float(t["entry_stop_price"]) - 1e-9:
                failures.append(f"{t['ticker']} {dt.date()}: STOP_LOSS but high "
                                f"{row['high']:.4f} < stop {t['entry_stop_price']:.4f}")
            want_fill = max(float(t["entry_stop_price"]), float(row["open"]))
            if abs(want_fill - float(t["exit_price"])) > 1e-6:
                failures.append(f"{t['ticker']} {dt.date()}: fill {t['exit_price']:.4f} "
                                f"!= max(stop, open) {want_fill:.4f}")
    print(f"11.3b  short exit reasons independently recomputed: {n_ok}/{len(sample)} match")

    # ---- 11.4: sizing against the engine's OWN emitted equity_at_entry ----
    n_size_ok = 0
    size_sample = td.sample(min(N_SAMPLE, len(td)), random_state=SEED + 1)
    for _, t in size_sample.iterrows():
        fr = frames[t["ticker"]]
        dt = t["entry_date"]
        eq = float(t["equity_at_entry"])
        dyn_max = resolve_dynamic_max_position_gbp(
            eq, 0.05, float(var_cfg.get("absolute_max_position_gbp", 1_000_000)))
        cs = fr["close"].astype(float)
        prior = cs.loc[cs.index < dt].tail(int(var_cfg.get("var_lookback_days", 252)) + 1)
        lr = np.log(prior / prior.shift(1)).dropna()
        if len(lr) >= _MIN_VAR_RETURNS:
            want, _, _ = _var_position_size_core(
                lr, eq, dyn_max, get_max_holding_days(t["ticker"], CONFIG),
                float(var_cfg.get("var_confidence", 0.95)), float(var_cfg.get("max_risk_pct", 0.01)),
                float(var_cfg.get("min_position_gbp", 50)))
        else:
            want = round(dyn_max * 0.5, 2)
        got = float(t["position_gbp"])
        # A long may additionally be clamped by free cash; a short never is.
        if abs(want - got) < TOL or (t["direction"] == "LONG" and got < want):
            n_size_ok += 1
        else:
            failures.append(f"sizing {t['direction']} {t['ticker']} {dt.date()}: "
                            f"engine={got:.2f} recomputed={want:.2f}")
    print(f"11.4   sizing vs engine's own equity_at_entry: {n_size_ok}/{len(size_sample)} match")

    # ---- 11.4b: entry stop direction ----
    n_stop_ok = 0
    for _, t in size_sample.iterrows():
        fr = frames[t["ticker"]]
        row = fr.loc[t["entry_date"]]
        atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
        want_stop, _ = _resolve_entry_stop(t["direction"], float(row["close"]), atr, CONFIG)
        if abs(want_stop - float(t["entry_stop_price"])) < 1e-6:
            n_stop_ok += 1
        else:
            failures.append(f"stop {t['direction']} {t['ticker']}: engine="
                            f"{t['entry_stop_price']:.4f} recomputed={want_stop:.4f}")
    print(f"11.4b  entry stops (SHORT above entry, LONG below): "
          f"{n_stop_ok}/{len(size_sample)} match")
    if len(shorts):
        bad_dir = int((shorts["entry_stop_price"] <= shorts["entry_price"]).sum())
        print(f"       short stops at or below entry price: {bad_dir} (must be 0)")
        if bad_dir:
            failures.append(f"{bad_dir} short trades have a stop at/below entry")

    # ---- 11.5: borrow accrual recomputed by hand ----
    n_borrow_ok = 0
    bsample = shorts.sample(min(20, len(shorts)), random_state=SEED + 2) if len(shorts) else shorts
    for _, t in bsample.iterrows():
        fr = frames[t["ticker"]]
        win = fr.loc[(fr.index >= t["entry_date"]) & (fr.index <= t["exit_date"])]
        N, E = float(t["position_gbp"]), float(t["entry_price"])
        total, prev_dt, prev_px = 0.0, win.index[0], E
        for dt2, row in win.iloc[1:].iterrows():
            days = (dt2 - prev_dt).days
            liab_prev = N * (prev_px / E)
            rate = lsp.borrow_rate_annual(row["close"], row["dd252"], row["vol60"], 1.0)
            total += lsp.accrue_borrow_gbp(liab_prev, rate, days)
            prev_dt, prev_px = dt2, float(row["close"])
        if abs(total - float(t["borrow_cost_gbp"])) < 0.01:
            n_borrow_ok += 1
        else:
            failures.append(f"borrow {t['ticker']} {t['entry_date'].date()}: engine="
                            f"{t['borrow_cost_gbp']:.4f} recomputed={total:.4f}")
    print(f"11.5   borrow accrual recomputed by hand: {n_borrow_ok}/{len(bsample)} match")
    if len(shorts):
        htb = shorts[shorts["htb_at_entry"]]
        print(f"       shorts entered HTB: {len(htb):,}/{len(shorts):,} "
              f"({100.0*len(htb)/len(shorts):.2f}%)  "
              f"borrow total £{shorts['borrow_cost_gbp'].sum():,.0f}")

    # ---- 11.8: short candidate gate hand-computed on sampled dates ----
    dates = sorted({d for d in shorts["entry_date"]}) if len(shorts) else []
    rng2 = random.Random(SEED + 3)
    check_dates = rng2.sample(dates, min(5, len(dates))) if dates else []
    for dt in check_dates:
        admitted = set(shorts[shorts["entry_date"] == dt]["ticker"])
        for tk in admitted:
            fr = frames[tk]
            row = fr.loc[dt]
            mv = float(row["momentum"]) if pd.notna(row["momentum"]) else None
            rate = lsp.borrow_rate_annual(row["close"], row["dd252"], row["vol60"], 1.0)
            need = lsp.required_short_floor(min_floor, rate)
            ok = (mv is not None and mv < 0 and mv <= -need
                  and universe.is_member(tk, dt.date()) is True
                  and not bool(rally_block.reindex(fr.index).ffill().fillna(False).loc[dt]))
            if not ok:
                failures.append(f"short gate violation {tk} {dt.date()}: mom={mv}, "
                                f"required<=-{need:.4f}")
    print(f"11.8   short-gate hand-check on {len(check_dates)} dates: "
          f"{sum(len(shorts[shorts['entry_date']==d]) for d in check_dates)} admissions checked")

    # ---- 11.7: signal separation ----
    src = (HERE / "ls_engine.py").read_text()
    gate_leaks = []
    for fn in ("passes_entry_gate(", "passes_momentum_floor(", "evaluate_exit_reason(",
               "evaluate_short_exit_reason("):
        for line in src.split("\n"):
            if fn in line and "relative_rank" in line:
                gate_leaks.append(line.strip())
    print(f"11.7   relative_rank reaching a gate/evaluator call: {len(gate_leaks)} (must be 0)")
    if gate_leaks:
        failures.extend(gate_leaks)

    print("\n" + "=" * 70)
    if failures:
        print(f"FAIL -- {len(failures)} issues:")
        for f_ in failures[:25]:
            print("  ", f_)
        sys.exit(1)
    print("PASS -- all engine verification checks clean")


if __name__ == "__main__":
    main()
