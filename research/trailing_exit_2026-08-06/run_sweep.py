"""16 blocks x 4 variants (baseline, T1, T2, T3), reset-at-boundary.

Conventions (identical to the 2026-08-06 benchmark run, so the preserved
leg C / leg D curves remain directly comparable without re-solving):
  * reset-at-boundary, GBP 50,000 fresh per block, zero positions
  * window 2007-01-01 -> 2026-08-03 (the verified reproduction vintage)
  * corrected engine, intraday-touch STOP_LOSS fill; the TRAILING_STOP fill
    uses the IDENTICAL convention (trigger on the day's low, fill at
    min(trail_price, that day's open))
  * CAGR annualised over the block's own calendar span for every leg
"""
import json
from pathlib import Path

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import (
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
    make_blocks,
    slice_ticker,
)
from analysis.validation import _strategy_ending_balance_gbp
from data.ingestion import get_price_df
from trailing.precompute import precompute_ticker_extended
from trailing.variant_engine import VARIANTS, simulate_variant

OUT = Path("trailing/out")
OUT.mkdir(parents=True, exist_ok=True)

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
CAPITAL = 50_000.0


def _era(mid: pd.Timestamp) -> str:
    y = mid.year
    if y <= 2009:
        return "2007-2009_GFC"
    if y <= 2019:
        return "2010-2019_bull"
    return "2020-2026_recent"


def main() -> None:
    config = yaml.safe_load(open("config.yaml"))
    t212 = config.get("trading212_invest", {})
    per_trade_pct = float(t212.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212.get("max_total_deployed", 0.5))

    gspc = get_price_df("^GSPC", days=9000).set_index("date")
    gspc.index = pd.to_datetime(gspc.index)
    gspc_close = gspc["close"].astype(float)

    blocks = make_blocks(START, END, n_blocks=16)
    per_block_cands = {i: universe.get_live_stock_candidates(as_of=be.date())
                       for i, (_, be) in enumerate(blocks)}
    all_tickers = sorted({t for v in per_block_cands.values() for t in v})
    print(f"Precomputing {len(all_tickers)} tickers (extended) ...", flush=True)
    cache = {}
    for n, t in enumerate(all_tickers, 1):
        cache[t] = precompute_ticker_extended(t, config)
        if n % 200 == 0:
            print(f"  {n}/{len(all_tickers)}", flush=True)

    rows, all_trades = [], []
    for i, (bs, be) in enumerate(blocks):
        gb = gspc_close[(gspc_close.index >= bs) & (gspc_close.index <= be)]
        if len(gb) < 2:
            continue
        years = (gb.index[-1] - gb.index[0]).days / 365.25
        cands = per_block_cands[i]
        caches = [slice_ticker(cache.get(t), bs, be) for t in cands]

        line = [f"block {i:2d} {bs.date()}->{be.date()}"]
        rec = {"block": i, "start": bs.date().isoformat(), "end": be.date().isoformat(),
               "era": _era(bs + (be - bs) / 2), "years": round(years, 3)}
        for v in VARIANTS:
            trades = simulate_variant(
                caches, config, gb, per_trade_pct, max_deployed_pct,
                collect_rejections=False, variant=v,
            )
            for t in trades:
                r = dict(t)
                r.update({"block": i, "variant": v,
                          "block_start": bs.date().isoformat(),
                          "block_end": be.date().isoformat()})
                all_trades.append(r)

            m = compute_metrics_fixed(trades, CAPITAL)
            dd_peak = compute_peak_relative_drawdown_pct(trades, CAPITAL)
            end_bal = _strategy_ending_balance_gbp(trades, CAPITAL)
            cagr = ((end_bal / CAPITAL) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
            hd = pd.Series([t["holding_days"] for t in trades]) if trades else pd.Series(dtype=float)
            reasons = pd.Series([t["exit_reason"] for t in trades]).value_counts().to_dict() if trades else {}
            rec.update({
                f"{v}_n_trades": len(trades),
                f"{v}_cagr": round(cagr, 2),
                f"{v}_total": round((end_bal - CAPITAL) / CAPITAL * 100.0, 2),
                f"{v}_dd_peak": dd_peak,
                f"{v}_dd_fixed": m["max_drawdown_pct"],
                f"{v}_pf": m.get("profit_factor", 0.0),
                f"{v}_ending_gbp": round(end_bal, 2),
                f"{v}_rpd": round(cagr / abs(dd_peak), 3) if dd_peak else None,
                f"{v}_mean_hold": round(float(hd.mean()), 1) if len(hd) else None,
                f"{v}_max_hold": int(hd.max()) if len(hd) else None,
                f"{v}_exit_mix": json.dumps(reasons),
            })
            line.append(f"{v}: {len(trades):4d}tr {cagr:6.2f}% dd{dd_peak}")
        rows.append(rec)
        print("  ".join(line), flush=True)

    pd.DataFrame(rows).to_csv(OUT / "variant_blocks.csv", index=False)
    td = pd.DataFrame(all_trades)
    td.to_csv(OUT / "variant_trades.csv", index=False)
    print(f"\nWrote {OUT/'variant_blocks.csv'} and {OUT/'variant_trades.csv'} "
          f"({len(td)} trades across {td['variant'].nunique()} variants)")


if __name__ == "__main__":
    main()
