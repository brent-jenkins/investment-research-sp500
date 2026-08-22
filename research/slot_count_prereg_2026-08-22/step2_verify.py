"""Sec 7 verification + Sec 8 sanity gate. Runs before any arm.

  7.1  Degenerate parity  -- slot_cap=None, live per_trade_pct / max_risk_pct /
                             cash_fraction must reproduce raw production
                             simulate_portfolio_compounding_mtm() BYTE-IDENTICAL
                             on a canonical (ticker, entry_date, exit_date) sort,
                             compared IN MEMORY (never via a CSV round trip).
  7.1b Instrumentation is purely additive -- instrument=True must produce the
                             identical trade set as instrument=False.
  7.2  Cash-clamp inertness at baseline -- ZERO admissions with binding_clamp
                             == "CASH". Falsifiable: the label is recorded for
                             every admission independently of the Sec 3.3 proof.
  7.6  Non-vacuity -- every assertion above must have operated on a non-empty
                             population; _require() enforces it.
  8    Sanity gate -- baseline trade SET vs the preserved exit-mechanics s3.0
                             reference (Pool 2 CLEAN 565 / GBP 348,548.74).

Read-only apart from writing its own outputs into this directory.
"""
import json
import pickle
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from analysis.backtest import simulate_portfolio_compounding_mtm  # noqa: E402
from slot_engine import simulate_slot_capped  # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)

REF_DIR = ROOT / "research" / "exit_mechanics_prereg_2026-08-11"
REFERENCE = {
    "pool2_clean": (565, 348548.74, REF_DIR / "trades_pool2_clean_s3.0.csv"),
    "pool1": (None, None, REF_DIR / "trades_pool1_s3.0.csv"),
}
KEY = ["ticker", "entry_date", "exit_date"]
CMP = ["ticker", "entry_date", "exit_date", "exit_reason", "position_gbp", "pnl_gbp"]

FAILS: list[str] = []


def _require(cond: bool, name: str, detail: str = "") -> bool:
    if not cond:
        FAILS.append(f"{name}: {detail}")
    return cond


def canon(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.strftime("%Y-%m-%d")
    return df.sort_values(KEY).reset_index(drop=True)


def compare(a: pd.DataFrame, b: pd.DataFrame, label: str, tol: float = 0.0) -> int:
    """Row-for-row comparison on a canonical sort. tol=0.0 means exact."""
    a, b = canon(a), canon(b)
    if len(a) != len(b):
        print(f"    {label}: LENGTH MISMATCH {len(a)} vs {len(b)}")
        return max(len(a), len(b))
    mism = 0
    for col in CMP:
        if col in ("position_gbp", "pnl_gbp"):
            d = (a[col].astype(float) - b[col].astype(float)).abs()
            bad = int((d > tol).sum())
        else:
            bad = int((a[col].astype(str) != b[col].astype(str)).sum())
        if bad:
            print(f"    {label}: {bad} mismatches in {col}")
        mism += bad
    return mism


def main() -> None:
    results = {}
    for pool in ("pool2_clean", "pool1"):
        cache_path = HERE / f"_cache_{pool}.pkl"
        if not cache_path.exists():
            FAILS.append(f"cache missing: {cache_path}")
            continue
        with open(cache_path, "rb") as fh:
            cache = pickle.load(fh)
        caches, gspc = cache["caches"], cache["gspc_close"]
        n_usable = sum(1 for c in caches if c is not None)
        print(f"\n=== {pool}: {n_usable}/{len(caches)} usable tickers ===", flush=True)
        _require(n_usable > 0, f"{pool} cache", "zero usable tickers -- every check would be vacuous")

        # ---------------------------------------------------------- 7.1
        t0 = time.time()
        prod = pd.DataFrame(simulate_portfolio_compounding_mtm(
            caches, BASE, gspc,
            per_trade_pct=float(BASE["trading212_invest"]["per_trade_pct"]),
            max_deployed_pct=float(BASE["trading212_invest"]["max_total_deployed"]),
        ))
        t_prod = time.time() - t0
        t0 = time.time()
        res = pd.DataFrame(simulate_slot_capped(
            caches, BASE, gspc,
            per_trade_pct=float(BASE["trading212_invest"]["per_trade_pct"]),
            max_deployed_pct=float(BASE["trading212_invest"]["max_total_deployed"]),
            slot_cap=None, cash_fraction=0.25, instrument=False,
        ))
        t_res = time.time() - t0
        print(f"  production : {len(prod)} trades, GBP {prod['pnl_gbp'].sum():,.2f} ({t_prod:.0f}s)")
        print(f"  slot_engine: {len(res)} trades, GBP {res['pnl_gbp'].sum():,.2f} ({t_res:.0f}s)")
        _require(len(prod) > 0, f"{pool} 7.1", "production produced NO trades -- parity would be vacuous")
        mism = compare(prod, res, "7.1 degenerate parity")
        print(f"  7.1 degenerate parity: {mism} mismatches "
              f"({'PASS' if mism == 0 else 'FAIL'})")
        _require(mism == 0, f"{pool} 7.1", f"{mism} row-level mismatches vs production")

        # --------------------------------------------------------- 7.1b
        out = simulate_slot_capped(
            caches, BASE, gspc,
            per_trade_pct=float(BASE["trading212_invest"]["per_trade_pct"]),
            max_deployed_pct=float(BASE["trading212_invest"]["max_total_deployed"]),
            slot_cap=None, cash_fraction=0.25, instrument=True,
        )
        trades_i, panel, admissions, slot_full, rejections = out
        ti = pd.DataFrame(trades_i)
        mism_i = compare(prod, ti, "7.1b instrumented parity")
        print(f"  7.1b instrumentation additive: {mism_i} mismatches "
              f"({'PASS' if mism_i == 0 else 'FAIL'})")
        _require(mism_i == 0, f"{pool} 7.1b", f"{mism_i} mismatches with instrument=True")

        adf = pd.DataFrame(admissions)
        pdf = pd.DataFrame(panel)
        _require(len(adf) > 0, f"{pool} 7.2", "no admissions logged -- clamp check would be vacuous")
        _require(len(pdf) > 0, f"{pool} panel", "empty panel")

        # ---------------------------------------------------------- 7.2
        clamp_counts = adf["binding_clamp"].value_counts().to_dict()
        n_cash = int(clamp_counts.get("CASH", 0))
        print(f"  7.2 binding clamp distribution: {clamp_counts}")
        print(f"  7.2 CASH-bound admissions at baseline: {n_cash} "
              f"({'PASS' if n_cash == 0 else 'FAIL -- Sec 3.3 proof falsified'})")
        _require(n_cash == 0, f"{pool} 7.2", f"{n_cash} CASH-bound baseline admissions")

        pinned = int((adf["binding_clamp"] == "CEILING").sum())
        print(f"  ceiling-pinned share (baseline): {pinned}/{len(adf)} "
              f"= {100.0 * pinned / len(adf):.1f}%")

        # ------------------------------------------------------------ 8
        exp_n, exp_pnl, ref_path = REFERENCE[pool]
        print(f"  8 sanity gate vs {ref_path.name}")
        if not ref_path.exists():
            FAILS.append(f"{pool} sanity gate: reference file missing {ref_path}")
        else:
            ref = pd.read_csv(ref_path)
            _require(len(ref) > 0, f"{pool} sanity", "reference file empty")
            # tol=0.01 ONLY here: the reference is a CSV round trip, which is
            # measurably not bit-exact (trailing study: 16.2% of float64 values
            # altered). 7.1 above is the exact in-memory check.
            mism_s = compare(prod, ref, "8 sanity gate", tol=0.01)
            print(f"    reference: {len(ref)} trades, GBP {ref['pnl_gbp'].sum():,.2f}")
            print(f"    this run : {len(prod)} trades, GBP {prod['pnl_gbp'].sum():,.2f}")
            print(f"    trade-SET mismatches: {mism_s} "
                  f"({'PASS' if mism_s == 0 else 'INVESTIGATE'})")
            if exp_n is not None:
                _require(len(prod) == exp_n, f"{pool} sanity",
                         f"trade count {len(prod)} != preserved {exp_n}")
            if exp_pnl is not None:
                _require(abs(prod["pnl_gbp"].sum() - exp_pnl) < 0.01, f"{pool} sanity",
                         f"total P&L {prod['pnl_gbp'].sum():,.2f} != preserved {exp_pnl:,.2f}")
            _require(mism_s == 0, f"{pool} sanity", f"{mism_s} trade-set mismatches vs reference")

        results[pool] = {
            "usable_tickers": n_usable,
            "trades": int(len(prod)),
            "total_pnl_gbp": round(float(prod["pnl_gbp"].sum()), 2),
            "degenerate_parity_mismatches": mism,
            "instrumented_parity_mismatches": mism_i,
            "clamp_distribution": {k: int(v) for k, v in clamp_counts.items()},
            "cash_bound_admissions": n_cash,
            "ceiling_pinned_pct": round(100.0 * pinned / len(adf), 2) if len(adf) else None,
            "panel_rows": int(len(pdf)),
            "slot_full_events": int(len(slot_full)),
        }
        # Preserve the baseline artefacts -- these ARE the control arm.
        pd.DataFrame(prod).to_csv(HERE / f"trades_{pool}_baseline.csv", index=False)
        pdf.to_csv(HERE / f"panel_{pool}_baseline.csv", index=False)
        adf.to_csv(HERE / f"admissions_{pool}_baseline.csv", index=False)
        pd.DataFrame(rejections).to_csv(HERE / f"rejections_{pool}_baseline.csv", index=False)

    (HERE / "step2_verification.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\n================ SUMMARY ================")
    print(json.dumps(results, indent=2))
    if FAILS:
        print("\nFAILURES:")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print("\nALL Sec 7 CHECKS AND THE Sec 8 SANITY GATE PASSED.")


if __name__ == "__main__":
    main()
