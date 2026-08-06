"""Fidelity gate, required before anything downstream is trusted (per the
task spec): the cap=0.50 arm of this sweep must reproduce the prior
2026-08-06 block_benchmark_comparison run's block-by-block strategy figures
exactly. If not, stop and report that first -- do not proceed to analysis.

Both runs call the identical production function
(simulate_portfolio_compounding_mtm) with identical config, identical
window (2007-01-01 -> 2026-08-03), identical block boundaries
(make_blocks(START, END, 16)), and identical per-block candidate pools
(analysis.universe.get_live_stock_candidates(as_of=block_end)). The only
difference in this script's own harness is collect_rejections=True (a
native toggle confirmed not to alter trade/admission logic) and a
pickle-cached ticker precompute step -- neither should move a single
figure.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
PRIOR = HERE.parent / "block_benchmark_comparison_2026-08-06" / "block_results_raw.json"
SWEEP = HERE / "results" / "sweep_block_results_raw.json"

FIELDS = [
    "strat_n_trades", "strat_cagr_pct", "strat_cagr_tradespan_pct",
    "strat_total_return_pct", "strat_dd_peak_pct", "strat_dd_fixed_pct",
    "strat_ending_gbp",
]


def main() -> None:
    prior = {r["block"]: r for r in json.loads(PRIOR.read_text())}
    sweep = json.loads(SWEEP.read_text())
    sweep_050 = {r["block"]: r for r in sweep if abs(r["cap"] - 0.50) < 1e-9}

    print(f"Prior run: {len(prior)} blocks. Sweep cap=0.50 arm: {len(sweep_050)} blocks.\n")

    mismatches = []
    for b in sorted(prior):
        p = prior[b]
        s = sweep_050.get(b)
        if s is None:
            mismatches.append((b, "MISSING in sweep cap=0.50 arm", None, None))
            continue
        for f in FIELDS:
            pv, sv = p.get(f), s.get(f)
            if pv is None and sv is None:
                continue
            if pv is None or sv is None or abs(float(pv) - float(sv)) > 1e-6:
                mismatches.append((b, f, pv, sv))

    if mismatches:
        print(f"PARITY CHECK FAILED -- {len(mismatches)} mismatch(es):\n")
        for b, f, pv, sv in mismatches:
            print(f"  block {b}, {f}: prior={pv}  sweep={sv}")
        print("\nSTOPPING per task spec: 'If it is not byte-identical, stop and "
              "report that first.'")
        sys.exit(1)

    print("PARITY CHECK PASSED -- all 16 blocks' strategy figures at "
          "max_total_deployed=0.50 are identical (tolerance 1e-6) between "
          "this sweep's own harness and the prior 2026-08-06 block-benchmark "
          "run. Proceeding to analysis is justified.")
    for b in sorted(prior):
        p, s = prior[b], sweep_050[b]
        print(f"  block {b}: trades {p['strat_n_trades']}=={s['strat_n_trades']}, "
              f"CAGR {p['strat_cagr_pct']}=={s['strat_cagr_pct']}, "
              f"ddPeak {p['strat_dd_peak_pct']}=={s['strat_dd_peak_pct']}, "
              f"ending £{p['strat_ending_gbp']}==£{s['strat_ending_gbp']}")


if __name__ == "__main__":
    main()
