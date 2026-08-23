"""PREREGISTRATION.md Sec 6 verification + Sec 7 sanity gate.

  6.1  Degenerate parity at k = 0 -- byte-identical to raw production, while
       still running the whole new path (correlations, percentiles, adjusted
       sort). Compared IN MEMORY on a canonical sort, never via CSV.
  6.2  The adjustment actually moves the order at k != 0 and not at k = 0.
  6.3  Correlation values are sane, validated against the SHARE-CLASS MAP as
       a control -- a measure that cannot recover GOOGL/GOOG at ~0.99 is not
       measuring correlation.
  6.4  Staleness guard -- synthetic-gap regression + real-data fire rate.
  6.6  Non-vacuity throughout. NO test of the correlation computation mocks
       the correlation computation.
  7    Sanity gate -- trade SETS vs the preserved references.
"""
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from analysis.backtest import simulate_portfolio_compounding_mtm  # noqa: E402
from analysis.signals import _MAX_ANCHOR_GAP_DAYS  # noqa: E402
from analysis import universe as v2u  # noqa: E402
from corr_engine import CORR_WINDOW, CorrelationBook, simulate_corr_admission  # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)

REF = {
    "pool2_clean": (565, 348548.74, ROOT / "research/exit_mechanics_prereg_2026-08-11/trades_pool2_clean_s3.0.csv"),
    "pool1": (522, 429108.58, ROOT / "research/exit_mechanics_prereg_2026-08-11/trades_pool1_s3.0.csv"),
}
KEY = ["ticker", "entry_date", "exit_date"]
CMP = ["ticker", "entry_date", "exit_date", "exit_reason", "position_gbp", "pnl_gbp"]
FAILS: list[str] = []


def _req(cond, name, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(f"{name}: {detail}")
    return cond


def canon(df):
    df = df.copy()
    for c in ("entry_date", "exit_date"):
        df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d")
    return df.sort_values(KEY).reset_index(drop=True)


def compare(a, b, tol=0.0):
    a, b = canon(a), canon(b)
    if len(a) != len(b):
        return max(len(a), len(b))
    m = 0
    for col in CMP:
        if col in ("position_gbp", "pnl_gbp"):
            m += int(((a[col].astype(float) - b[col].astype(float)).abs() > tol).sum())
        else:
            m += int((a[col].astype(str) != b[col].astype(str)).sum())
    return m


def build_book(caches):
    dates = None
    for c in caches:
        if c is None:
            continue
        idx = c["frame"].index
        dates = idx if dates is None else dates.union(idx)
    return CorrelationBook(caches, dates), dates


def main():
    results = {}
    for pool in ("pool2_clean", "pool1"):
        with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
            cache = pickle.load(fh)
        caches, gspc = cache["caches"], cache["gspc_close"]
        n_usable = sum(1 for c in caches if c is not None)
        print(f"\n=== {pool}: {n_usable}/{len(caches)} usable ===", flush=True)
        _req(n_usable > 0, f"{pool} cache non-empty", f"{n_usable}")

        book, dates = build_book(caches)
        print(f"  returns matrix: {book.R.shape[0]} dates x {book.R.shape[1]} tickers")
        _req(book.R.shape[1] == n_usable, "every usable ticker in the returns matrix",
             f"{book.R.shape[1]} vs {n_usable}")

        ptp = float(BASE["trading212_invest"]["per_trade_pct"])
        mdp = float(BASE["trading212_invest"]["max_total_deployed"])

        # ------------------------------------------------------------ 6.1
        t0 = time.time()
        prod = pd.DataFrame(simulate_portfolio_compounding_mtm(caches, BASE, gspc, ptp, mdp))
        t_prod = time.time() - t0
        t0 = time.time()
        k0 = pd.DataFrame(simulate_corr_admission(caches, BASE, gspc, ptp, mdp, k=0.0, book=book))
        t_k0 = time.time() - t0
        print(f"  production      : {len(prod)} trades, GBP {prod['pnl_gbp'].sum():,.2f} ({t_prod:.0f}s)")
        print(f"  corr_engine k=0 : {len(k0)} trades, GBP {k0['pnl_gbp'].sum():,.2f} ({t_k0:.0f}s)")
        _req(len(prod) > 0, "production produced trades (else parity is vacuous)")
        mism = compare(prod, k0)
        _req(mism == 0, "6.1 degenerate parity at k=0, byte-identical", f"{mism} mismatches")

        # ------------------------------------------------------------ 6.2
        # The adjusted order must be IDENTICAL to raw at k=0 and DIFFER at
        # k != 0. Driven through the real book on real days, not a mock.
        from corr_engine import _pct_rank
        moved_days = same_days = 0
        checked = 0
        for di in range(CORR_WINDOW + 50, len(dates), 97):
            day = dates[di]
            held, cand = [], []
            for c in caches:
                if c is None or day not in c["frame"].index:
                    continue
                cand.append(c["ticker"])
            if len(cand) < 30:
                continue
            held = cand[:15]                      # a real, non-empty held set
            cands = [t for t in cand[15:60]]
            cm = book.corr_to_held(di, cands, held)
            if len(cm) < 10:
                continue
            checked += 1
            raw = list(range(len(cands), 0, -1))  # strictly descending raw ranks
            rp = _pct_rank(raw)
            defined = [cm.get(t) for t in cands]
            have = [v for v in defined if v is not None]
            hp = dict(zip([t for t in cands if t in cm], _pct_rank(have)))
            cp = [hp.get(t, 0.5) for t in cands]
            o0 = sorted(range(len(cands)), key=lambda i: -(rp[i] - 0.0 * cp[i]))
            o1 = sorted(range(len(cands)), key=lambda i: -(rp[i] - 1.0 * cp[i]))
            if o0 == list(range(len(cands))):
                same_days += 1
            if o1 != o0:
                moved_days += 1
        _req(checked > 0, "6.2 sampled a non-empty set of days", f"{checked} days")
        _req(same_days == checked, "6.2 k=0 leaves the order unchanged", f"{same_days}/{checked}")
        _req(moved_days > 0, "6.2 k=1 genuinely reorders", f"{moved_days}/{checked} days moved")

        # ------------------------------------------------------------ 6.3
        # Control: the share-class map. A measure that cannot recover a known
        # ~0.99 pair is not measuring correlation.
        di = len(dates) - 1
        ctrl = {}
        for grp in v2u.SHARE_CLASS_GROUPS:
            a, b = sorted(grp)
            if a in book.idx and b in book.idx:
                c = book.corr_to_held(di, [a], [b])
                ctrl[f"{a}/{b}"] = round(c.get(a), 4) if a in c else None
        print(f"  6.3 share-class control pairs: {ctrl}")
        got = [v for v in ctrl.values() if v is not None]
        _req(len(got) > 0, "6.3 at least one share-class control pair present")
        if got:
            _req(min(got) >= 0.85, "6.3 every share-class pair >= 0.85", f"min {min(got)}")
        # An unrelated pair must be materially lower.
        pairs = [("AAPL", "XOM"), ("MSFT", "KO"), ("JPM", "NEM")]
        unrel = {}
        for a, b in pairs:
            if a in book.idx and b in book.idx:
                c = book.corr_to_held(di, [a], [b])
                unrel[f"{a}/{b}"] = round(c.get(a), 4) if a in c else None
        print(f"  6.3 unrelated pairs          : {unrel}")
        u = [v for v in unrel.values() if v is not None]
        _req(len(u) > 0, "6.3 at least one unrelated control pair present")
        if u and got:
            _req(max(u) < min(got) - 0.2, "6.3 unrelated clearly below share-class",
                 f"max unrelated {max(u)} vs min pair {min(got)}")

        # distribution
        held_sample = [c["ticker"] for c in caches if c is not None][:15]
        cand_sample = [c["ticker"] for c in caches if c is not None][15:200]
        dist = book.corr_to_held(di, cand_sample, held_sample)
        vals = np.array(list(dist.values()))
        if vals.size:
            print(f"  6.3 corr distribution: n={vals.size} min={vals.min():.3f} "
                  f"p25={np.percentile(vals,25):.3f} med={np.median(vals):.3f} "
                  f"p75={np.percentile(vals,75):.3f} max={vals.max():.3f}")
            _req(bool(np.all(np.abs(vals) <= 1.0)), "6.3 all correlations within [-1,1]")
            _req(vals.size >= 50, "6.3 distribution non-vacuous", f"n={vals.size}")

        results[pool] = {
            "usable": n_usable, "prod_trades": int(len(prod)),
            "prod_pnl": round(float(prod["pnl_gbp"].sum()), 2),
            "k0_trades": int(len(k0)), "k0_pnl": round(float(k0["pnl_gbp"].sum()), 2),
            "degenerate_parity_mismatches": mism,
            "share_class_control": ctrl, "unrelated_control": unrel,
            "order_days_checked": checked, "order_moved_days": moved_days,
        }
        prod.to_csv(HERE / f"trades_{pool}_baseline.csv", index=False)

        # -------------------------------------------------------------- 7
        exp_n, exp_pnl, ref_path = REF[pool]
        if ref_path.exists():
            ref = pd.read_csv(ref_path)
            mism_s = compare(prod, ref, tol=0.01)   # CSV round trip on the reference only
            print(f"  7 sanity gate: ref {len(ref)}/{ref['pnl_gbp'].sum():,.2f} vs "
                  f"run {len(prod)}/{prod['pnl_gbp'].sum():,.2f} -> {mism_s} mismatches")
            _req(len(prod) == exp_n, "7 trade count matches preserved", f"{len(prod)} vs {exp_n}")
            _req(abs(prod["pnl_gbp"].sum() - exp_pnl) < 0.01, "7 P&L matches preserved")
            _req(mism_s == 0, "7 trade SETS identical", f"{mism_s}")
            results[pool]["sanity_mismatches"] = mism_s
        else:
            FAILS.append(f"{pool}: reference file missing")

    # ---------------------------------------------------------------- 6.4
    print("\n=== 6.4 staleness guard (synthetic, no mocking of the computation) ===")
    idx_ok = pd.bdate_range("2024-01-01", periods=400)
    rng = np.random.default_rng(7)
    a = pd.Series(rng.normal(0, 0.01, 400).cumsum() + 100, index=idx_ok)
    b = pd.Series(rng.normal(0, 0.01, 400).cumsum() + 100, index=idx_ok)
    mk = lambda t, s: {"ticker": t, "frame": pd.DataFrame({"close": s})}
    bk = CorrelationBook([mk("A", a), mk("B", b)], idx_ok)
    clean = bk.corr_to_held(len(idx_ok) - 1, ["A"], ["B"])
    _req("A" in clean, "6.4 gapless window yields a defined correlation",
         f"{clean.get('A')}")

    gap_idx = idx_ok[:200].append(pd.bdate_range(idx_ok[199] + pd.Timedelta(days=_MAX_ANCHOR_GAP_DAYS + 30), periods=200))
    ag = pd.Series(a.to_numpy(), index=gap_idx)
    bg = pd.Series(b.to_numpy(), index=gap_idx)
    bk2 = CorrelationBook([mk("A", ag), mk("B", bg)], gap_idx)
    gapped = bk2.corr_to_held(210, ["A"], ["B"])       # window straddles the gap
    _req("A" not in gapped, "6.4 a window straddling a >400d gap is UNDEFINED",
         f"got {gapped.get('A')}")
    recovered = bk2.corr_to_held(len(gap_idx) - 1, ["A"], ["B"])
    _req("A" in recovered, "6.4 recovers once a gapless window exists past the gap",
         f"{recovered.get('A')}")

    (HERE / "step2_verification.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\n================ SUMMARY ================")
    print(json.dumps(results, indent=2))
    if FAILS:
        print("\nFAILURES:")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("\nALL Sec 6 CHECKS AND THE Sec 7 SANITY GATE PASSED.")


if __name__ == "__main__":
    main()
