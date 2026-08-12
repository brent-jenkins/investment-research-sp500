"""Fundamentals coverage probe -- the FIRST deliverable, before anything is
built or wired.

READ-ONLY. No database writes, no schema, no scheduler change. Establishes what
yfinance actually returns for this universe so that sparse coverage or badly
stale reporting periods are discovered NOW rather than after a year of thin rows.

WHAT IT ANSWERS
  1. Of ~50 sampled live candidates, how many return fundamentals AT ALL?
  2. Per required field, what fraction of tickers have a usable value?
  3. How STALE is the most recent reporting period (fiscal period end vs today)?
     This is the point-in-time question -- a figure without a known reporting
     period is undated and useless for vintage-correct work.
  4. Do quarterly and annual statements differ in coverage/staleness?

FIELDS PROBED (raw, never derived -- ratios are computable later, a stored
score would lock in tonight's definition permanently):
  total debt, total equity, total assets, revenue, net income,
  operating cash flow, free cash flow, shares outstanding

Deliberately does NOT touch verify_ticker_identity() or
scan_unverified_tickers() -- that scoping error has recurred three times in this
project and these tickers have no business going through an identity screen.
"""
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis import universe                       # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

RATE_LIMIT = float(CONFIG.get("data", {}).get("rate_limit_seconds", 2.5))
SAMPLE_N = 50
SEED = 20260812

# yfinance row labels differ between statements and have changed across
# versions; probe several aliases per concept and record which one hit.
FIELD_ALIASES = {
    "total_debt":          (["Total Debt", "TotalDebt"], "balance"),
    "total_equity":        (["Stockholders Equity", "Total Stockholder Equity",
                             "StockholdersEquity", "Total Equity Gross Minority Interest"], "balance"),
    "total_assets":        (["Total Assets", "TotalAssets"], "balance"),
    "revenue":             (["Total Revenue", "TotalRevenue", "Operating Revenue"], "income"),
    "net_income":          (["Net Income", "NetIncome", "Net Income Common Stockholders"], "income"),
    "operating_cash_flow": (["Operating Cash Flow", "Total Cash From Operating Activities",
                             "OperatingCashFlow"], "cash"),
    "free_cash_flow":      (["Free Cash Flow", "FreeCashFlow"], "cash"),
    "shares_outstanding":  (["Ordinary Shares Number", "Share Issued"], "balance"),
}


def _pick(df, aliases):
    """Return (value, alias_used, period_end) from the most recent column."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None, None, None
    cols = sorted([c for c in df.columns], reverse=True)
    if not cols:
        return None, None, None
    latest = cols[0]
    for a in aliases:
        if a in df.index:
            v = df.loc[a, latest]
            if pd.notna(v):
                return float(v), a, latest
    return None, None, latest


def probe(ticker: str, quarterly: bool) -> dict:
    t = yf.Ticker(ticker)
    try:
        bs = t.quarterly_balance_sheet if quarterly else t.balance_sheet
        inc = t.quarterly_financials if quarterly else t.financials
        cf = t.quarterly_cashflow if quarterly else t.cashflow
    except Exception as exc:  # noqa: BLE001
        return {"ticker": ticker, "error": f"{type(exc).__name__}: {exc}"}

    stmts = {"balance": bs, "income": inc, "cash": cf}
    out = {"ticker": ticker, "error": None, "fields": {}, "periods": {}}
    for name, (aliases, which) in FIELD_ALIASES.items():
        v, alias, period = _pick(stmts.get(which), aliases)
        out["fields"][name] = {"present": v is not None, "alias": alias,
                               "period_end": str(period.date()) if hasattr(period, "date") else None}
    for which, df in stmts.items():
        if isinstance(df, pd.DataFrame) and not df.empty and len(df.columns):
            latest = sorted(df.columns, reverse=True)[0]
            out["periods"][which] = str(latest.date()) if hasattr(latest, "date") else str(latest)
        else:
            out["periods"][which] = None
    out["any_data"] = any(f["present"] for f in out["fields"].values())
    return out


def summarise(results, label, today):
    n = len(results)
    errs = [r for r in results if r.get("error")]
    nodata = [r for r in results if not r.get("error") and not r.get("any_data")]
    ok = [r for r in results if not r.get("error") and r.get("any_data")]
    print(f"\n{'='*78}\n{label}: {n} tickers probed")
    print(f"  hard errors      : {len(errs)} ({100*len(errs)/n:.0f}%)")
    print(f"  returned NOTHING : {len(nodata)} ({100*len(nodata)/n:.0f}%)")
    print(f"  usable           : {len(ok)} ({100*len(ok)/n:.0f}%)")
    if errs:
        print(f"  error kinds: {dict(Counter(e['error'].split(':')[0] for e in errs))}")

    print(f"\n  {'field':<22}{'present':>9}{'  %':>6}   most-common row label")
    for fname in FIELD_ALIASES:
        have = [r for r in results if not r.get("error") and r["fields"][fname]["present"]]
        alias = Counter(r["fields"][fname]["alias"] for r in have).most_common(1)
        print(f"  {fname:<22}{len(have):>9}{100*len(have)/n:>6.0f}%   "
              f"{alias[0][0] if alias else '--'}")

    print(f"\n  reporting-period staleness (days from {today.date()} to latest period end):")
    for which in ("balance", "income", "cash"):
        ages = []
        for r in results:
            p = r.get("periods", {}).get(which)
            if p:
                try:
                    ages.append((today - datetime.fromisoformat(p).replace(tzinfo=timezone.utc)).days)
                except Exception:  # noqa: BLE001
                    pass
        if ages:
            ages.sort()
            print(f"    {which:<8} n={len(ages):<4} min={ages[0]:<5} "
                  f"median={ages[len(ages)//2]:<5} p90={ages[int(len(ages)*0.9)]:<5} max={ages[-1]}")
        else:
            print(f"    {which:<8} no dated periods at all")
    return {"n": n, "errors": len(errs), "nodata": len(nodata), "usable": len(ok),
            "field_presence": {f: sum(1 for r in results if not r.get("error")
                                      and r["fields"][f]["present"]) for f in FIELD_ALIASES}}


def main() -> None:
    import random
    pool = sorted(universe.get_live_stock_candidates())
    sample = random.Random(SEED).sample(pool, min(SAMPLE_N, len(pool)))
    today = datetime.now(timezone.utc)
    print(f"live candidate pool: {len(pool)}; probing {len(sample)} "
          f"(seed {SEED}), rate limit {RATE_LIMIT}s")

    out = {}
    for quarterly in (True, False):
        label = "QUARTERLY" if quarterly else "ANNUAL"
        res, t0 = [], time.time()
        for i, tk in enumerate(sample):
            res.append(probe(tk, quarterly))
            time.sleep(RATE_LIMIT)
            if (i + 1) % 10 == 0:
                print(f"  {label} {i+1}/{len(sample)} ({time.time()-t0:.0f}s)", flush=True)
        out[label] = {"summary": summarise(res, label, today), "rows": res}
        print(f"  [{label} elapsed {time.time()-t0:.0f}s]")

    (HERE / "coverage_results.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote coverage_results.json")


if __name__ == "__main__":
    main()
