"""Sec 7 check 5 -- decision-engine parity for THIS research script.

Per CLAUDE.md's standing requirement, evidence must come from this script's own
run, never inherited from "production is consolidated". For 30 sampled trades
per pool per arm, independently recompute and compare against what the engine
actually did:

  a) exit reason   -- analysis/decision.py:evaluate_exit_reason() called directly
  b) entry gate    -- passes_entry_gate() + passes_momentum_floor()
  c) sizing horizon-- analysis/decision.py:resolve_var_horizon_days()
  d) position size -- analysis/volatility.py:_var_position_size_core() driven by
                      the engine's OWN emitted balance_at_entry_gbp / cash, never
                      a reconstructed proxy (the error that produced the
                      cross-sectional study's false 21/30)
  e) entry stop    -- analysis/volatility.py:_resolve_entry_stop()

Sec 7 check 6 (non-vacuity): every sampled population must be non-empty and the
sample must actually reach the requested size or the reason is reported.
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from analysis import crisis, universe  # noqa: E402
from analysis.decision import (  # noqa: E402
    evaluate_exit_reason,
    passes_entry_gate,
    passes_momentum_floor,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
    resolve_min_momentum_floor,
    resolve_var_horizon_days,
)
from analysis.universe import get_max_holding_days  # noqa: E402
from analysis.volatility import (  # noqa: E402
    _MIN_VAR_RETURNS,
    _resolve_entry_stop,
    _var_position_size_core,
)
from step3_run_arms import MAX_DEPLOYED, arm_params  # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)

SAMPLE = 30
SEED = 20260822


def load_cache(pool):
    with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
        c = pickle.load(fh)
    by_ticker = {}
    for cached in c["caches"]:
        if cached is not None:
            by_ticker[cached["ticker"]] = cached
    return by_ticker, c["gspc_close"]


def check_arm(pool, tag, cfg, per_trade_pct, cash_fraction, by_ticker, gspc, rng):
    tr_path = HERE / f"trades_{tag}.csv"
    ad_path = HERE / f"admissions_{tag}.csv"
    if not tr_path.exists() or not ad_path.exists():
        return {"error": f"missing {tr_path.name} or {ad_path.name}"}
    trades = pd.read_csv(tr_path)
    adm = pd.read_csv(ad_path)
    if trades.empty or adm.empty:
        return {"error": "empty trades or admissions -- checks would be VACUOUS"}

    var_cfg = cfg["trading212_invest"]
    var_conf = float(var_cfg["var_confidence"])
    var_lb = int(var_cfg["var_lookback_days"])
    max_risk = float(var_cfg["max_risk_pct"])
    min_pos = float(var_cfg["min_position_gbp"])
    abs_max = float(var_cfg["absolute_max_position_gbp"])
    floor = resolve_min_momentum_floor(cfg)

    merged = trades.merge(
        adm[["date", "ticker", "balance_at_entry_gbp", "cash_before_entry_gbp",
             "position_gbp", "rank_within_day", "n_eligible"]].rename(
            columns={"date": "entry_date", "position_gbp": "adm_position_gbp"}),
        on=["ticker", "entry_date"], how="inner",
    )
    res = {"n_trades": int(len(trades)), "n_admissions": int(len(adm)),
           "n_joined": int(len(merged))}
    if merged.empty:
        res["error"] = "trade<->admission join empty -- VACUOUS"
        return res

    idx = rng.choice(len(merged), size=min(SAMPLE, len(merged)), replace=False)
    sample = merged.iloc[sorted(idx)]
    res["sample_size"] = int(len(sample))

    ok = {"exit_reason": 0, "entry_gate": 0, "horizon": 0, "size": 0, "stop": 0,
          "size_exact_from_rounded": 0, "size_exact_within_rounding_interval": 0}
    bad = {k: [] for k in ok}
    for _, r in sample.iterrows():
        tk = r["ticker"]
        cached = by_ticker[tk]
        frame = cached["frame"]
        ed = pd.Timestamp(r["entry_date"])
        xd = pd.Timestamp(r["exit_date"])
        erow, xrow = frame.loc[ed], frame.loc[xd]
        raw_c = crisis.compute_raw_trigger(gspc).reindex(frame.index).ffill().fillna(False)
        blocked = crisis.extend_trigger(raw_c).reindex(frame.index).ffill().fillna(False)
        max_hold = get_max_holding_days(tk, cfg)

        # (e) entry stop
        atr = float(erow["atr"]) if pd.notna(erow["atr"]) else 0.0
        stop, sdp = _resolve_entry_stop("LONG", float(erow["close"]), atr, cfg)
        if abs(sdp - float(r["stop_distance_pct"])) < 1e-6:
            ok["stop"] += 1
        else:
            bad["stop"].append(f"{tk}@{ed.date()} {sdp} vs {r['stop_distance_pct']}")

        # (b) entry gate
        sig_on = bool(erow["signal"]) if pd.notna(erow["signal"]) else False
        is_mem = universe.is_member(tk, ed.date())
        blk = bool(blocked.loc[ed])
        mom = float(erow["momentum"]) if pd.notna(erow["momentum"]) else None
        if passes_entry_gate(sig_on=sig_on, is_member=is_mem, entry_blocked=blk) and \
                passes_momentum_floor(mom, floor):
            ok["entry_gate"] += 1
        else:
            bad["entry_gate"].append(f"{tk}@{ed.date()} sig={sig_on} mem={is_mem} blk={blk} mom={mom}")

        # (c) horizon
        h = resolve_var_horizon_days(max_hold, cfg)
        if h == int(cfg["trading212_invest"]["var_horizon_days"]) and max_hold is None:
            ok["horizon"] += 1
        elif max_hold is not None and h == max_hold:
            ok["horizon"] += 1
        else:
            bad["horizon"].append(f"{tk} max_hold={max_hold} horizon={h}")

        # (d) position size -- driven by the ENGINE'S OWN emitted balance/cash
        # (never a reconstructed proxy). The engine LOGS balance/cash rounded
        # to 2dp, so the recomputation's input is known only to +/-0.005; since
        # _var_position_size_core() rounds its OUTPUT to 2dp, that input
        # imprecision can flip the last penny whenever the true value sits near
        # a half-penny boundary. Rather than widen the tolerance to 1p (which
        # would also accept a genuine 1p divergence), the test demands an EXACT
        # match, allowing only the known input imprecision: exact from the
        # rounded balance, or -- failing that -- exact at SOME balance inside
        # that balance's own rounding interval. A real divergence cannot
        # satisfy either.
        bal = float(r["balance_at_entry_gbp"])
        cash_pre = float(r["cash_before_entry_gbp"])
        cs = cached["frame"]["close"].astype(float)
        prior = cs.loc[cs.index < ed].tail(var_lb + 1)
        lr = np.log(prior / prior.shift(1)).dropna()
        eng_pos = float(r["position_gbp"])

        def _size_at(b: float) -> float:
            ceiling = resolve_dynamic_max_position_gbp(b, per_trade_pct, abs_max)
            if len(lr) >= _MIN_VAR_RETURNS:
                p, _dv, _hv = _var_position_size_core(
                    lr, b, ceiling, h, var_conf, max_risk, min_pos)
            else:
                p = round(ceiling * 0.5, 2)
            return resolve_cash_constrained_position_gbp(p, cash_pre, cash_fraction=cash_fraction)

        pos = _size_at(bal)
        if abs(pos - eng_pos) < 1e-9:
            ok["size"] += 1
            ok["size_exact_from_rounded"] += 1
        elif any(abs(_size_at(bal + k * 1e-4) - eng_pos) < 1e-9 for k in range(-50, 51)):
            ok["size"] += 1
            ok["size_exact_within_rounding_interval"] += 1
        else:
            bad["size"].append(
                f"{tk}@{ed.date()} recomputed {pos:.2f} vs engine {eng_pos:.2f} "
                f"(NOT reproducible anywhere in the balance rounding interval)")

        # (a) exit reason
        low = xrow.get("low")
        chk = float(low) if low is not None and pd.notna(low) else float(xrow["close"])
        sig_x = bool(xrow["signal"]) if pd.notna(xrow["signal"]) else False
        exp = evaluate_exit_reason(
            crisis_now=bool(raw_c.loc[xd]), price=chk, stop_price=stop, sig_on=sig_x,
            holding_days=int((xd - ed).days), max_holding_days=max_hold,
        )
        if exp is None and xd == frame.index[-1]:
            exp = "END_OF_DATA"
        if exp == r["exit_reason"]:
            ok["exit_reason"] += 1
        else:
            bad["exit_reason"].append(f"{tk}@{xd.date()} recomputed {exp} vs engine {r['exit_reason']}")

    res["checks"] = {k: f"{v}/{len(sample)}" for k, v in ok.items()
                     if not k.startswith("size_exact")}
    res["size_breakdown"] = {k: ok[k] for k in
                             ("size_exact_from_rounded", "size_exact_within_rounding_interval")}
    res["failures"] = {k: v[:5] for k, v in bad.items() if v}
    return res


def main() -> None:
    rng = np.random.default_rng(SEED)
    out = {}
    for pool in ("pool2_clean", "pool1"):
        by_ticker, gspc = load_cache(pool)
        # baseline (uncapped, live constants)
        out[f"{pool}_baseline"] = check_arm(
            pool, f"{pool}_baseline", BASE,
            float(BASE["trading212_invest"]["per_trade_pct"]), 0.25, by_ticker, gspc, rng)
        for n in (14, 8, 5, 3):
            p = arm_params(n)
            cfg = yaml.safe_load(yaml.safe_dump(BASE))
            cfg["trading212_invest"]["max_risk_pct"] = p["max_risk_pct"]
            out[f"{pool}_n{n}"] = check_arm(
                pool, f"{pool}_n{n}", cfg, p["per_trade_pct"], p["cash_fraction"],
                by_ticker, gspc, rng)
    (HERE / "step2b_decision_parity.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))

    fails = []
    for tag, r in out.items():
        if "error" in r:
            fails.append(f"{tag}: {r['error']}")
            continue
        if r.get("sample_size", 0) == 0:
            fails.append(f"{tag}: VACUOUS -- zero sampled trades")
        for k, v in r.get("checks", {}).items():
            a, b = v.split("/")
            if a != b:
                fails.append(f"{tag}: {k} {v}")
    print("\nFAILURES:" if fails else "\nALL DECISION-ENGINE PARITY CHECKS PASSED.")
    for f in fails:
        print(f"  - {f}")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
