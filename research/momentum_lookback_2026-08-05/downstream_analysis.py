"""Downstream effects of changing the lookback -- the part that matters
because this is the CORE SIGNAL, not a peripheral parameter.

Headline CAGR/drawdown alone would miss whether a different lookback changes
WHICH candidates exist at all, and whether it interacts with things already
tested tonight. Measured here, per the brief:

  (a) Signal frequency / candidate-pool size: how often is the signal on, and
      how often is momentum even computable (the anchor-staleness guard).
  (b) Momentum-floor binding: how often does a candidate pass `momentum > 0`
      (the signal) but fail config's `min_momentum_pct: 0.02` floor? If a
      shorter lookback compresses momentum magnitudes, the floor silently
      becomes a much stronger filter -- an interaction with an already-tested
      parameter that a CAGR number would never reveal.
  (c) Momentum-strength band mix of the candidate pool.
  (d) Realized trade frequency and holding-period mix, from the actual
      reset-at-boundary trade lists.

This is pure measurement over precomputed frames + already-produced trade
lists. It runs the real signals.compute_momentum_series()/compute_signal_series()
and the real analysis.decision.passes_momentum_floor() -- it does not
re-derive either.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
import yaml

import analysis.signals as signals
import analysis.universe as universe
from analysis.backtest import precompute_ticker
from analysis.decision import resolve_min_momentum_floor, passes_momentum_floor
from lookback_worker import apply_lookback, months_to_days

CONFIG_PATH = ROOT / "config.yaml"
END = pd.Timestamp("2026-08-03")
TEST_SPLIT = pd.Timestamp("2020-01-01")
MONTHS = [3, 6, 9, 12, 15, 18, 24]
RESULTS = Path(__file__).resolve().parent / "results"


def main():
    config = yaml.safe_load(open(CONFIG_PATH, encoding="utf-8"))
    floor = resolve_min_momentum_floor(config)
    print(f"min_momentum_pct floor (from real resolve_min_momentum_floor): {floor}")

    tickers = universe.get_live_stock_candidates(as_of=END.date())
    out = {"min_momentum_floor": floor, "n_candidates": len(tickers), "arms": {}}

    for m in MONTHS:
        days = months_to_days(m)
        gap = apply_lookback(days)
        caches = [precompute_ticker(t, config) for t in tickers]

        n_rows = n_mom = n_sig = n_floor_blocked = 0
        bands = {"0-2%": 0, "2-10%": 0, "10-25%": 0, "25-50%": 0, "50-100%": 0, ">100%": 0}
        for c in caches:
            if c is None:
                continue
            f = c["frame"]
            f = f[(f.index >= TEST_SPLIT) & (f.index <= END)]
            if f.empty:
                continue
            mom = f["momentum"]
            n_rows += len(f)
            n_mom += int(mom.notna().sum())
            on = f["signal"].astype(bool)
            n_sig += int(on.sum())
            # Floor binding: signal on (momentum > 0) but fails the real floor.
            live = mom[on & mom.notna()]
            blocked = [v for v in live.to_numpy() if not passes_momentum_floor(float(v), floor)]
            n_floor_blocked += len(blocked)
            for v in live.to_numpy():
                v = float(v)
                if v <= 0.02: bands["0-2%"] += 1
                elif v <= 0.10: bands["2-10%"] += 1
                elif v <= 0.25: bands["10-25%"] += 1
                elif v <= 0.50: bands["25-50%"] += 1
                elif v <= 1.00: bands["50-100%"] += 1
                else: bands[">100%"] += 1
            med = float(live.median()) if len(live) else float("nan")

        arm = {
            "lookback_days": days, "anchor_gap_days": gap,
            "ticker_days": n_rows,
            "pct_momentum_computable": round(n_mom / n_rows * 100, 2),
            "pct_signal_on": round(n_sig / n_rows * 100, 2),
            "signal_on_days": n_sig,
            "floor_blocked_days": n_floor_blocked,
            "pct_of_signal_on_blocked_by_floor": round(n_floor_blocked / max(n_sig, 1) * 100, 2),
            "momentum_bands_of_signal_on": bands,
        }
        # realized trades, from the already-produced reset run
        p = RESULTS / f"reset_{m}m.json"
        if p.exists():
            d = json.load(open(p))
            tr = d["trades"]
            hold = [t["holding_days"] for t in tr]
            reasons = {}
            for t in tr:
                reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
            arm.update({
                "realized_trades": len(tr),
                "median_holding_days": float(np.median(hold)) if hold else None,
                "exit_reason_mix": {k: round(v / len(tr) * 100, 1) for k, v in sorted(reasons.items())},
                "distinct_tickers_traded": len({t["ticker"] for t in tr}),
                "cagr_pct": d["metrics"].get("cagr_pct"),
            })
        out["arms"][f"{m}m"] = arm
        print(f"{m:>2}m: mom_computable={arm['pct_momentum_computable']:5.1f}%  "
              f"signal_on={arm['pct_signal_on']:5.1f}%  "
              f"floor_blocks={arm['pct_of_signal_on_blocked_by_floor']:5.2f}% of signal-on  "
              f"trades={arm.get('realized_trades')}  "
              f"tickers={arm.get('distinct_tickers_traded')}", flush=True)

    json.dump(out, open(RESULTS / "downstream.json", "w"), indent=2, default=str)

    print("\nMomentum-strength band mix of signal-on candidate-days (%):")
    hdr = ["0-2%", "2-10%", "10-25%", "25-50%", "50-100%", ">100%"]
    print(f"{'arm':<5}" + "".join(f"{h:>9}" for h in hdr))
    for m in MONTHS:
        b = out["arms"][f"{m}m"]["momentum_bands_of_signal_on"]
        tot = sum(b.values()) or 1
        print(f"{str(m)+'m':<5}" + "".join(f"{b[h]/tot*100:>8.1f}%" for h in hdr))

    print("\nExit-reason mix (% of realized trades):")
    keys = sorted({k for m in MONTHS for k in out["arms"][f"{m}m"].get("exit_reason_mix", {})})
    print(f"{'arm':<5}" + "".join(f"{k:>14}" for k in keys))
    for m in MONTHS:
        mix = out["arms"][f"{m}m"].get("exit_reason_mix", {})
        print(f"{str(m)+'m':<5}" + "".join(f"{mix.get(k,0):>13.1f}%" for k in keys))


if __name__ == "__main__":
    main()
