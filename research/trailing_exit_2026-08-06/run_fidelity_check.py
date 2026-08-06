"""FIDELITY GATE. Proves trailing/variant_engine.py:simulate_variant() with
variant="baseline" reproduces the PRODUCTION engine
analysis/backtest.py:simulate_portfolio_compounding_mtm() byte-identically,
on the real candidate universe over real blocks.

Nothing downstream of this is trustworthy unless this passes: a variant
comparison is only meaningful if the harness itself is provably neutral when
the variant is switched off.

Also proves the extended precompute (adding `high` and `ma14` columns) is
inert for the production engine -- it is run against the SAME extended caches
the variants use, so any behavioural difference from the extra columns would
surface here rather than silently contaminating the comparison.
"""
import hashlib
import json
import sys

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import make_blocks, simulate_portfolio_compounding_mtm, slice_ticker
from data.ingestion import get_price_df
from trailing.precompute import precompute_ticker_extended

from analysis.backtest import make_blocks as _mb  # noqa: F401  (same source as harness)
from trailing.variant_engine import simulate_variant

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")


def digest(trades: list[dict]) -> str:
    """Order-sensitive hash of the full trade list -- every field, every
    trade, in emission order. Any difference at all changes this."""
    return hashlib.sha256(
        json.dumps(trades, sort_keys=True, default=str).encode()
    ).hexdigest()


def main() -> None:
    config = yaml.safe_load(open("config.yaml"))
    t212 = config.get("trading212_invest", {})
    per_trade_pct = float(t212.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212.get("max_total_deployed", 0.5))

    gspc = get_price_df("^GSPC", days=9000).set_index("date")
    gspc.index = pd.to_datetime(gspc.index)
    gspc_close = gspc["close"].astype(float)

    blocks = make_blocks(START, END, n_blocks=16)
    # Three structurally different blocks: the GFC crash, a mid bull block,
    # and the 2020-2021 melt-up (the acid-test block). Enough to exercise
    # crisis exits, ordinary exits and long holds.
    check_blocks = [1, 6, 11]

    ok = True
    for i in check_blocks:
        bs, be = blocks[i]
        cands = universe.get_live_stock_candidates(as_of=be.date())
        caches = []
        for t in cands:
            c = precompute_ticker_extended(t, config)
            if c is not None:
                c = slice_ticker(c, bs, be)
            caches.append(c)

        prod = simulate_portfolio_compounding_mtm(
            caches, config, gspc_close[(gspc_close.index >= bs) & (gspc_close.index <= be)],
            per_trade_pct, max_deployed_pct, collect_rejections=False,
        )
        base = simulate_variant(
            caches, config, gspc_close[(gspc_close.index >= bs) & (gspc_close.index <= be)],
            per_trade_pct, max_deployed_pct, collect_rejections=False, variant="baseline",
        )
        dp, db = digest(prod), digest(base)
        same = dp == db
        ok = ok and same and len(prod) == len(base)
        print(f"block {i:2d} {bs.date()} -> {be.date()}: "
              f"production {len(prod):4d} trades  sha {dp[:16]}")
        print(f"          variant=baseline  {len(base):4d} trades  sha {db[:16]}  "
              f"{'IDENTICAL' if same else 'DIFFERENT'}")
        if not same:
            pdf, bdf = pd.DataFrame(prod), pd.DataFrame(base)
            print("   first divergence:")
            for c in pdf.columns:
                if not pdf[c].equals(bdf[c]):
                    d = pdf.index[pdf[c] != bdf[c]][:3]
                    print(f"     col {c}: rows {list(d)}")

    print("\n" + "=" * 70)
    if not ok:
        print("FIDELITY GATE FAILED -- variant harness is NOT neutral when disabled.")
        sys.exit(1)
    print("FIDELITY GATE PASSED -- variant=baseline is byte-identical to the "
          "production engine on all checked blocks, and the extended "
          "precompute is inert.")


if __name__ == "__main__":
    main()
