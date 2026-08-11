"""Sec 11.1 / Sec 12 -- compare persisted trade sets. Cheap, instant, rerunnable.

(a) DEGENERATE PARITY: ls_engine with enable_shorts=False must reproduce raw
    production simulate_portfolio_compounding_mtm() byte-identical, under BOTH
    admission_ranking_mode settings.
(b) SANITY GATE: the production run is compared as TRADE SETS against the
    relative-ranking study's published reference figures.

Comparison is on a CANONICAL SORT (ticker, entry_date, exit_date): the
relative-ranking study found raw insertion order produces phantom mismatches
despite identical trade sets, because the day-by-day event walk does not
guarantee stable row order between runs.
"""
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent

KEY = ["ticker", "entry_date", "exit_date"]
CMP = ["ticker", "entry_date", "exit_date", "exit_reason", "position_gbp", "pnl_gbp"]

REFERENCE = {
    "relative_rank": {"n_trades": 4876, "total_pnl": 166830.11, "label": "Arm B (current live ordering)"},
    "absolute_momentum": {"n_trades": 4919, "total_pnl": 103926.50, "label": "Arm A (pre-adoption ordering)"},
}

SOURCES = {
    # mode: (production csv, ls_engine csv)
    "relative_rank": ("_ref_production_relative_rank.csv", "trades_pool2_clean_A.csv"),
    "absolute_momentum": ("_ref_production_absolute_momentum.csv", "_ref_lsengine_absolute_momentum.csv"),
}


def canon(path):
    df = pd.read_csv(HERE / path)
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.strftime("%Y-%m-%d")
    return df.sort_values(KEY).reset_index(drop=True)


def main() -> None:
    ok = True
    for mode, (prod_path, ours_path) in SOURCES.items():
        print(f"\n=== (a) Degenerate parity -- admission_ranking_mode={mode} ===")
        if not (HERE / prod_path).exists() or not (HERE / ours_path).exists():
            print(f"    SKIP -- missing {prod_path if not (HERE/prod_path).exists() else ours_path}")
            ok = False
            continue
        a, b = canon(prod_path), canon(ours_path)
        print(f"    production : {len(a):,} trades, £{a['pnl_gbp'].sum():,.2f}  [{prod_path}]")
        print(f"    ls_engine  : {len(b):,} trades, £{b['pnl_gbp'].sum():,.2f}  [{ours_path}]")

        if len(a) != len(b):
            print(f"    *** TRADE COUNT DIFFERS: {len(a):,} vs {len(b):,}")
            ka = set(a["ticker"] + "|" + a["entry_date"])
            kb = set(b["ticker"] + "|" + b["entry_date"])
            print(f"    production-only keys: {len(ka-kb)}, ls_engine-only keys: {len(kb-ka)}")
            for k in list(ka - kb)[:5]:
                print(f"      prod-only: {k}")
            for k in list(kb - ka)[:5]:
                print(f"      ours-only: {k}")
            ok = False
        else:
            mism = 0
            for col in CMP:
                x, y = a[col], b[col]
                bad = ~((x - y).abs() < 1e-6) if x.dtype.kind == "f" else (x != y)
                n = int(bad.sum())
                if n:
                    mism += n
                    print(f"    *** column {col}: {n} row-level mismatches")
                    print(a.loc[bad, CMP].head(3).to_string(index=False))
                    print(b.loc[bad, CMP].head(3).to_string(index=False))
            if mism == 0:
                print("    PASS -- byte-identical on the canonical sort, 0 mismatches")
            else:
                ok = False

        ref = REFERENCE[mode]
        n, pnl = len(a), float(a["pnl_gbp"].sum())
        print(f"--- (b) Sanity gate vs published reference: {ref['label']} ---")
        print(f"    reference: {ref['n_trades']:,} trades, £{ref['total_pnl']:,.2f}")
        print(f"    this run : {n:,} trades, £{pnl:,.2f}")
        if n == ref["n_trades"] and abs(pnl - ref["total_pnl"]) < 0.01:
            print("    PASS -- exact reproduction of the published figure")
        else:
            print(f"    *** DIVERGES by {n - ref['n_trades']:+,} trades, "
                  f"£{pnl - ref['total_pnl']:+,.2f} -- must be row-diffed and explained")
            ok = False

    print("\n" + "=" * 72)
    print("OVERALL: PASS -- engine certified" if ok
          else "OVERALL: FAIL -- arm results are NOT trustworthy until resolved")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
