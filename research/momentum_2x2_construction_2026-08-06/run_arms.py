"""16-block x 4-arm momentum-construction sweep.

CONVENTIONS (stated explicitly, per CLAUDE.md's standing requirement --
never left implicit):

  * RESET-AT-BOUNDARY. Every block starts fresh at CAPITAL (£50,000) with
    zero open positions, truncated to its own window. This is a
    CROSS-VARIANT comparison (which momentum construction is better), which
    CLAUDE.md's "Two backtest conventions" section is explicit is the
    reset-at-boundary case. Never the continuous walk.
  * ENGINE: simulate_portfolio_compounding_mtm() -- the one engine
    confirmed to genuinely match live execution (mark-to-market capacity
    tracking, momentum-ranked priority admission, shared re-entry-cooldown /
    momentum-floor / realistic intraday-touch STOP_LOSS fill). Not the
    three frozen exempt engines.
  * BLOCKS: make_blocks(2007-01-01, 2026-08-03, n_blocks=16) -- the EXACT
    existing definition from research/block_benchmark_comparison_2026-08-06,
    reused verbatim via the production make_blocks() function. Not
    re-invented. END is 2026-08-03 (not -08-06) for the reason recorded in
    that study's own REPORT.md.
  * CAGR for every arm is annualised over the SAME block calendar span,
    derived from the S&P's own first/last bar inside the block -- identical
    to the prior study's convention, so arm 1's figures are directly
    comparable to its preserved output. The production-convention
    trade-span figure is carried alongside as strat_cagr_tradespan_pct.
  * BOTH drawdown conventions reported per arm per block: peak-relative
    (compute_peak_relative_drawdown_pct) and fixed-denominator
    (compute_metrics_fixed()["max_drawdown_pct"]).

ISOLATION: only the `momentum`/`signal` columns differ between arms. See
arms.py's module docstring for the full constraint and the exact formulas.

Parallelism: one process per arm (4 arms, 4 physical cores). SQLite is
read-only here and safe for concurrent readers.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import (
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
    make_blocks,
    simulate_portfolio_compounding_mtm,
    slice_ticker,
)
from analysis.validation import _strategy_ending_balance_gbp
from data.ingestion import get_price_df

import arms as ARMS_MOD

RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
N_BLOCKS = 16
CAPITAL = 50_000.0

# Excluded (2026-08-06): a full-database ratio-break scan (script
# diag_block3.py -> results/full_db_ratio_breaks.csv) found 68 tickers with
# a consecutive-day close ratio outside [0.5, 2.0] in the current, expanded
# (834-ticker) database -- up from 13 tickers/14 hits when Task A1 audited
# the smaller 540-ticker snapshot a few hours earlier (this database has
# since grown via other, concurrent ingestion work). Traced directly: this
# is NOT the split-adjustment cliff Task A fixed (a clean one-time step) --
# it is erratic, alternating garbage data (e.g. TIE: momentum reading of
# 295,022% in block 3, price alternating almost day-to-day between ~$8-28
# and ~$15,990-28,200), consistent with the "symbol-recycling" risk
# CLAUDE.md's own coverage-remediation section already names for exactly
# this class of long-delisted ticker (NCC/CFC among them, both explicitly
# named there as confirmed cases of "a different, unrelated company under
# the same reused symbol"). Confirmed via a targeted diagnostic
# (diag_block3.py) that arm3/arm4 block 3's absurd CAGR (+779%/+11,250%
# vs. arm1's real -12.26%) was caused by exactly this.
#
# Of the 68 flagged tickers, 20 show an UNAMBIGUOUS corruption signature --
# 18 to 568 ratio-break hits each, which is not possible for a real stock's
# genuine trading history (a real single-name crash/spinoff/split produces
# at most a small handful of such hits across up to 19 years -- confirmed
# directly: the 13 tickers Task A1 already verified as genuine market moves
# -- AIG, APA, EXPE, GL, HIG, JCI, KDP, MO, OXY, PCG, STT, TRGP, WY -- and
# three more spot-checked here (GME's real Jan-2021 squeeze, RX's real
# decade-spread moves, FMCC's real 2008 GSE-conservatorship crash) all have
# 1-4 hits each). The remaining ~10 borderline tickers (3-9 hits) showed
# MIXED reversion evidence on a direct check (some hits revert to the
# pre-spike level within 10 trading days -- corruption; some don't --
# plausibly genuine) and are deliberately left IN rather than excluded on
# weaker evidence -- their per-block impact is far smaller than the 20
# below, and blanket-excluding them risks discarding real trading history
# for real companies. See results/full_db_ratio_breaks.csv for the full
# scan and results/diag_block3_full.log for the block-3 root-cause trace.
CORRUPTED_TICKERS = {
    "NCC", "KRI", "CBE", "TIE", "CFC", "BOL", "MEE", "HM", "SRR", "MCIC",
    "CPWR", "COMS", "CIN", "PBG", "BMC", "DIGI", "MII", "GLK", "ORX", "HPC",
}


def _era(mid: pd.Timestamp) -> str:
    y = mid.year
    if y <= 2009:
        return "2007-2009_GFC"
    if y <= 2019:
        return "2010-2019_bull"
    return "2020-2026_recent"


def legacy_universe() -> set:
    """The EXACT stock universe the prior study
    (research/block_benchmark_comparison_2026-08-06) actually had price data
    for: the 503 tickers that are S&P 500 members as of the final block end.

    NOT a guess. Established by direct reconstruction: intersecting each
    block's real member list with this 503-ticker set reproduces that
    study's own preserved `universe_coverage_by_block.csv`
    `n_with_any_price_data` column EXACTLY for all 16 blocks
    (249/270/279/289/301/308/320/343/361/372/401/414/433/452/474/503), and
    503 + the 6 index/ETF series that study states it ingested into its own
    copy (^GSPC already present, plus ^NDX/AGG/^IRX/SPY/QQQ) = the 509
    price_data tickers its REPORT.md reports. The current database holds 834
    -- the coverage-remediation ingestion has since added ~325 ex-member
    tickers that study's database did not have.
    """
    return set(universe.get_live_stock_candidates(as_of=END.date()))


def run_arm(spec) -> str:
    arm, legacy = spec if isinstance(spec, tuple) else (spec, False)
    t0 = time.time()
    config = yaml.safe_load(open(HERE.parent.parent / "config.yaml"))
    t212 = config.get("trading212_invest", {})
    per_trade_pct = float(t212.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212.get("max_total_deployed", 0.5))

    tag = f"arm{arm}" + ("_legacy" if legacy else "")

    def log(msg):
        print(f"[{tag} {time.time()-t0:6.0f}s] {msg}", flush=True)

    gspc = get_price_df("^GSPC", days=9000).set_index("date")
    gspc.index = pd.to_datetime(gspc.index)
    gspc = gspc["close"].astype(float).sort_index()

    blocks = make_blocks(START, END, n_blocks=N_BLOCKS)
    per_block_cands = {i: universe.get_live_stock_candidates(as_of=be.date())
                       for i, (_, be) in enumerate(blocks)}
    per_block_cands = {i: [t for t in v if t not in CORRUPTED_TICKERS]
                       for i, v in per_block_cands.items()}
    log(f"Excluded {len(CORRUPTED_TICKERS)} confirmed-corrupted tickers "
        f"(symbol-recycled/garbage data, see CORRUPTED_TICKERS docstring)")
    if legacy:
        keep = legacy_universe()
        per_block_cands = {i: [t for t in v if t in keep] for i, v in per_block_cands.items()}
        log(f"LEGACY universe mode: candidates restricted to the {len(keep)} "
            f"tickers the prior study's database actually held price data for")
    all_tickers = sorted({t for v in per_block_cands.values() for t in v})
    log(f"{len(all_tickers)} distinct tickers across all 16 block ends")

    cache = {}
    nan_stats = {"rows_total": 0, "momentum_nan": 0, "signal_true": 0}
    for n, t in enumerate(all_tickers, 1):
        c = ARMS_MOD.precompute_ticker_arm(t, config, arm)
        cache[t] = c
        if c is not None:
            f = c["frame"]
            nan_stats["rows_total"] += len(f)
            nan_stats["momentum_nan"] += int(f["momentum"].isna().sum())
            nan_stats["signal_true"] += int(f["signal"].sum())
        if n % 150 == 0:
            log(f"  precomputed {n}/{len(all_tickers)}")
    n_ok = sum(1 for v in cache.values() if v is not None)
    log(f"{n_ok}/{len(all_tickers)} usable; momentum NaN "
        f"{nan_stats['momentum_nan']}/{nan_stats['rows_total']} "
        f"({nan_stats['momentum_nan']/max(nan_stats['rows_total'],1):.4%}); "
        f"signal ON {nan_stats['signal_true']/max(nan_stats['rows_total'],1):.4%}")

    rows = []
    all_trades = []
    for i, (bs, be) in enumerate(blocks):
        gb = gspc[(gspc.index >= bs) & (gspc.index <= be)]
        if len(gb) < 2:
            continue
        bidx = gb.index
        years = (bidx[-1] - bidx[0]).days / 365.25

        cands = per_block_cands[i]
        caches = [slice_ticker(cache.get(t), bs, be) for t in cands]
        trades = simulate_portfolio_compounding_mtm(
            caches, config, gb, per_trade_pct, max_deployed_pct, collect_rejections=False,
        )
        for tr in trades:
            r = dict(tr)
            r["arm"] = arm
            r["block"] = i
            r["block_start"] = bs.date().isoformat()
            r["block_end"] = be.date().isoformat()
            # momentum at entry, from THIS arm's own precomputed frame --
            # the ranking value the admission decision actually used.
            c = cache.get(r["ticker"])
            m = None
            if c is not None:
                ts = pd.Timestamp(r["entry_date"])
                if ts in c["frame"].index:
                    v = c["frame"].loc[ts, "momentum"]
                    m = None if pd.isna(v) else float(v) * 100.0
            r["momentum_at_entry_pct"] = m
            all_trades.append(r)

        m = compute_metrics_fixed(trades, CAPITAL)
        dd_peak = compute_peak_relative_drawdown_pct(trades, CAPITAL)
        end_bal = _strategy_ending_balance_gbp(trades, CAPITAL)
        strat_total = (end_bal - CAPITAL) / CAPITAL * 100.0
        cagr_window = ((end_bal / CAPITAL) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0

        rows.append({
            "arm": arm, "legacy_universe": legacy, "block": i,
            "start": bs.date().isoformat(), "end": be.date().isoformat(),
            "era": _era(bs + (be - bs) / 2), "years": round(years, 3),
            "n_candidates": len(cands),
            "strat_n_trades": len(trades),
            "strat_cagr_pct": round(cagr_window, 2),
            "strat_cagr_tradespan_pct": m["cagr_pct"],
            "strat_total_return_pct": round(strat_total, 2),
            "strat_dd_peak_pct": dd_peak,
            "strat_dd_fixed_pct": m["max_drawdown_pct"],
            "strat_win_rate_pct": m.get("win_rate", 0.0),
            "strat_profit_factor": m.get("profit_factor", 0.0),
            "strat_sharpe": m.get("sharpe_ratio", 0.0),
            "strat_ending_gbp": round(end_bal, 2),
            "strat_return_per_unit_dd": round(cagr_window / abs(dd_peak), 3) if dd_peak else None,
        })
        log(f"block {i:2d} {bs.date()}->{be.date()}: {len(trades):4d} trades, "
            f"CAGR {cagr_window:7.2f}%, ddPeak {dd_peak}, PF {m.get('profit_factor')}")

    out_json = RESULTS / f"{tag}_blocks.json"
    out_json.write_text(json.dumps(
        {"arm": arm, "legacy_universe": legacy, "nan_stats": nan_stats, "blocks": rows},
        indent=2, default=str))
    td = pd.DataFrame(all_trades)
    td["legacy_universe"] = legacy
    td.to_csv(RESULTS / f"{tag}_trades.csv", index=False)
    log(f"DONE. {len(rows)} blocks, {len(td)} trades -> {out_json.name}")
    return f"{tag}: {len(rows)} blocks, {len(td)} trades, {time.time()-t0:.0f}s"


def main() -> None:
    """Usage: run_arms.py [--legacy] [arm ...]"""
    argv = sys.argv[1:]
    legacy = "--legacy" in argv
    which = [int(a) for a in argv if not a.startswith("--")] or [1, 2, 3, 4]
    specs = [(a, legacy) for a in which]
    if len(specs) == 1:
        print(run_arm(specs[0]))
        return
    with mp.Pool(processes=len(specs)) as pool:
        for r in pool.imap_unordered(run_arm, specs):
            print(r, flush=True)


if __name__ == "__main__":
    mp.freeze_support()
    main()
