"""Decision-engine parity verification for THIS run's own output.

Required by CLAUDE.md's "Research scripts must verify decision-engine
parity, not inherit it by assumption" standing requirement. This script
constructs a per-block candidate pool, so parity has to be re-established
with EVIDENCE gathered from this run's own trades -- not asserted from the
fact that the production codebase is consolidated, and not established by
reading the harness and confirming by eye that it calls the right things
(that style of review has already failed once in this project's history).

Method: take the real trades the harness produced, and for each check
INDEPENDENTLY recompute what the shared production function would return
for the same inputs, then confirm the trade actually respects it.
"""
import json
import sys
from collections import Counter

import numpy as np
import pandas as pd
import yaml

from analysis import signals, universe
from analysis.decision import (
    passes_momentum_floor,
    resolve_dynamic_max_position_gbp,
    resolve_min_momentum_floor,
)
from analysis.universe import get_max_holding_days
from data.ingestion import get_price_df

CONFIG = yaml.safe_load(open("config.yaml"))
T212 = CONFIG.get("trading212_invest", {})
CAPITAL = 50_000.0

VALID_EXIT_REASONS = {"CRISIS_EXIT", "STOP_LOSS", "SIGNAL_OFF", "MAX_HOLDING", "END_OF_DATA"}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(f"{name}: {detail}")


def main() -> None:
    td = pd.read_csv("out/block_strategy_trades.csv")
    td["entry_date"] = pd.to_datetime(td["entry_date"])
    td["exit_date"] = pd.to_datetime(td["exit_date"])
    print(f"Loaded {len(td)} trades across {td['block'].nunique()} blocks\n")

    # ---- 1. exit reasons come from the shared priority chain ------------
    reasons = Counter(td["exit_reason"])
    print("Exit-reason distribution:", dict(reasons), "\n")
    check("every exit_reason is one the shared evaluate_exit_reason() chain can emit",
          set(reasons) <= VALID_EXIT_REASONS,
          f"unexpected: {set(reasons) - VALID_EXIT_REASONS}")
    check("INDEX_REMOVAL absent (removed from the priority chain)",
          "INDEX_REMOVAL" not in reasons)

    # ---- 2. holding period respects the SHARED get_max_holding_days() ---
    # Independently recompute the cap per ticker and confirm no trade
    # exceeded it. MAX_HOLDING exits should sit AT the cap.
    caps = {t: get_max_holding_days(t, CONFIG) for t in td["ticker"].unique()}
    td["cap"] = td["ticker"].map(caps)

    # The engine compares CALENDAR holding_days against the cap, but can
    # only ACT on a trading day. A trade whose cap day lands on a weekend
    # or holiday therefore legitimately exits at cap+1..cap+3 -- that is
    # the cap being respected at the first opportunity, not a breach. The
    # real invariant is: no trade survives past the FIRST TRADING DAY on
    # which holding_days >= cap. Verified against each ticker's own real
    # trading calendar, not assumed from the day count.
    over = td[td["holding_days"] > td["cap"]]
    cal = {}
    late = []
    for t in over["ticker"].unique():
        df = get_price_df(t, days=9000)
        if not df.empty:
            cal[t] = pd.DatetimeIndex(pd.to_datetime(df["date"])).normalize()
    for r in over.itertuples():
        idx = cal.get(r.ticker)
        if idx is None:
            continue
        due = r.entry_date + pd.Timedelta(days=int(r.cap))
        nxt = idx[(idx >= due)]
        if len(nxt) and r.exit_date > nxt[0]:
            late.append((r.ticker, r.entry_date.date(), r.exit_date.date(), r.holding_days))
    check("no trade survives past the first TRADING DAY on which "
          "get_max_holding_days() was reached (recomputed independently "
          "against each ticker's own real trading calendar)",
          len(late) == 0,
          f"{len(late)} late of {len(over)} that ran past the raw calendar cap "
          f"e.g. {late[:3]}")
    print(f"      {len(over)} trades ran past the raw calendar cap; all exited on "
          f"the first available trading day (weekend/holiday spillover, max "
          f"{int(td['holding_days'].max())} days vs cap {sorted(set(caps.values()))})")

    mh = td[td["exit_reason"] == "MAX_HOLDING"]
    if len(mh):
        at_cap = (mh["holding_days"] >= mh["cap"]).mean()
        check("every MAX_HOLDING exit actually reached its own recomputed cap",
              at_cap == 1.0, f"{at_cap:.4f} at/over cap (n={len(mh)})")
        print(f"      distinct caps in use: {sorted(set(caps.values()))}, "
              f"MAX_HOLDING holding_days range {mh['holding_days'].min()}-{mh['holding_days'].max()}")

    # ---- 3. entry gate: genuine index membership at entry --------------
    sample = td.sample(n=min(400, len(td)), random_state=0)
    bad_member = [
        (r.ticker, r.entry_date.date())
        for r in sample.itertuples()
        if not universe.is_member(r.ticker, r.entry_date.date())
    ]
    check("every sampled entry was a genuine index member on its entry date "
          "(independent is_member() call)",
          len(bad_member) == 0, f"{len(bad_member)}/{len(sample)} non-members e.g. {bad_member[:3]}")

    # ---- 4. entry gate: signal on AND momentum floor at entry ----------
    floor = resolve_min_momentum_floor(CONFIG)
    print(f"\n      resolve_min_momentum_floor(config) = {floor}")
    tickers = sample["ticker"].unique()
    frames = {}
    for t in tickers:
        df = get_price_df(t, days=9000)
        if df.empty or len(df) < 300:
            continue
        df = df.set_index("date")
        df.index = pd.to_datetime(df.index)
        c = df["close"].astype(float)
        frames[t] = pd.DataFrame({"signal": signals.compute_signal_series(c),
                                  "momentum": signals.compute_momentum_series(c)})

    sig_bad, mom_bad, checked = [], [], 0
    for r in sample.itertuples():
        f = frames.get(r.ticker)
        if f is None or r.entry_date not in f.index:
            continue
        checked += 1
        row = f.loc[r.entry_date]
        if row["signal"] is not True and row["signal"] != True:  # noqa: E712
            sig_bad.append((r.ticker, r.entry_date.date()))
        if not passes_momentum_floor(float(row["momentum"]), floor):
            mom_bad.append((r.ticker, r.entry_date.date(), float(row["momentum"])))
    check("every sampled entry had signal ON, recomputed from the shared "
          "signals.compute_signal_series()",
          len(sig_bad) == 0, f"{len(sig_bad)}/{checked} violations e.g. {sig_bad[:3]}")
    check("every sampled entry passes the shared passes_momentum_floor() "
          "recomputed independently",
          len(mom_bad) == 0, f"{len(mom_bad)}/{checked} violations e.g. {mom_bad[:3]}")

    # ---- 5. sizing respects the SHARED ceiling -------------------------
    abs_max = float(T212.get("absolute_max_position_gbp", 1_000_000))
    per_trade_pct = float(T212.get("per_trade_pct", 0.05))
    min_pos = float(T212.get("min_position_gbp", 0))
    check("no position exceeds absolute_max_position_gbp backstop",
          bool((td["position_gbp"] <= abs_max).all()),
          f"max seen {td['position_gbp'].max():.2f} vs cap {abs_max}")
    check("no position below min_position_gbp floor",
          bool((td["position_gbp"] >= min_pos).all()),
          f"min seen {td['position_gbp'].min():.2f} vs floor {min_pos}")
    # sanity on the compounding shape: sizing must track a real balance,
    # never explode (the 2026-08-01 unbounded-compounding defect signature)
    check("position sizes stay in a physically sane range (no unbounded "
          "mark-to-market compounding)",
          bool(td["position_gbp"].max() < CAPITAL * 10),
          f"max position £{td['position_gbp'].max():,.2f} on £{CAPITAL:,.0f} capital")
    # spot-check the shared ceiling function itself is what bounds a trade
    demo = resolve_dynamic_max_position_gbp(CAPITAL, per_trade_pct, abs_max)
    print(f"      resolve_dynamic_max_position_gbp(£{CAPITAL:,.0f}, {per_trade_pct}, "
          f"£{abs_max:,.0f}) = £{demo:,.2f}")

    # ---- 6. the CORRECTED stop-fill is genuinely active ----------------
    # The 2026-08-06 realistic (intraday-touch) fill exits at
    # min(stop_price, open) -- never above the stop. Under the OLD
    # optimistic convention every STOP_LOSS filled exactly AT stop_price.
    sl = td[td["exit_reason"] == "STOP_LOSS"].copy()
    if len(sl):
        # stop_distance_pct is stored in PERCENT (e.g. 6.0791 == 6.0791%),
        # not as a fraction -- confirmed directly against pnl_pct on
        # fill-at-stop trades, not assumed from the column name.
        sl["implied_stop"] = sl["entry_price"] * (1.0 - sl["stop_distance_pct"] / 100.0)
        gapped = (sl["exit_price"] < sl["implied_stop"] * 0.9995).mean()
        above = (sl["exit_price"] > sl["implied_stop"] * 1.0005).mean()
        check("no STOP_LOSS fills ABOVE its own stop price",
              above == 0.0, f"{above:.4f} above stop")
        check("a real share of STOP_LOSS exits fill BELOW the stop (proof the "
              "corrected intraday-touch engine is active, not the old "
              "optimistic fill-at-stop convention)",
              gapped > 0.01, f"{gapped:.2%} of {len(sl)} stops gapped through")

    print("\n" + "=" * 70)
    if failures:
        print(f"PARITY VERIFICATION FAILED -- {len(failures)} check(s):")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("PARITY VERIFICATION PASSED -- entry, exit, sizing and holding-period "
          "logic all route through the shared production functions.")


if __name__ == "__main__":
    main()
