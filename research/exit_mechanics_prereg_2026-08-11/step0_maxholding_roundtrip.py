"""STAGE 0 (PREREGISTRATION Sec 3) -- read-only, runs before any sweep cell.

No backtest. Computed from the preserved baseline trade sets alone.

Question: with reentry_cooldown_days = 0, a position forced out by MAX_HOLDING
can be re-admitted into the same ticker immediately, paying FX both ways for a
round trip nothing asked for. How often, and at what cost?

Method reuses the stop-loss reconfiguration study's whipsaw quantification
verbatim, substituting the exit reason (STOP_LOSS -> MAX_HOLDING).

STATED IN ADVANCE (Sec 3): this SIZES an opportunity, it does not PRICE one.
The "just held it" counterfactual is not observable here, because the re-entry
leg's own outcome was produced under a different capacity state. Only the sweep
can price it.
"""
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

SRC = ROOT / "research" / "relative_ranking_prereg_2026-08-09"
TRADES = {
    "pool2_clean": SRC / "trades_pool2_clean_B.csv",   # decisive
    "pool1": SRC / "trades_pool1_B.csv",               # direction only
}
FX_ROUND_TRIP = 0.0030
GAPS = [1, 5, 10, 21, 30]


def analyse(label: str, path: Path) -> dict:
    td = pd.read_csv(path)
    td["entry_date"] = pd.to_datetime(td["entry_date"])
    td["exit_date"] = pd.to_datetime(td["exit_date"])
    td = td.sort_values(["ticker", "entry_date"]).reset_index(drop=True)

    # Trading-day calendar from the union of all observed dates in the file.
    cal = pd.DatetimeIndex(sorted(set(td["entry_date"]) | set(td["exit_date"])))
    pos = {d: i for i, d in enumerate(cal)}

    out = {"label": label, "n_trades": int(len(td))}
    mix = td["exit_reason"].value_counts()
    out["exit_reason_mix"] = {k: int(v) for k, v in mix.items()}
    print(f"\n{'='*84}\n{label}: {len(td):,} trades   exit mix: {out['exit_reason_mix']}")

    mh = td[td["exit_reason"] == "MAX_HOLDING"]
    out["n_max_holding_exits"] = int(len(mh))
    out["max_holding_pnl_gbp"] = round(float(mh["pnl_gbp"].sum()), 2)
    print(f"  MAX_HOLDING exits: {len(mh):,} "
          f"({100.0*len(mh)/len(td):.1f}% of exits), P&L £{mh['pnl_gbp'].sum():,.2f}")

    pairs = []
    for t in mh.itertuples():
        later = td[(td["ticker"] == t.ticker) & (td["entry_date"] > t.exit_date)]
        if later.empty:
            continue
        nxt = later.iloc[0]
        gap = pos[nxt["entry_date"]] - pos[t.exit_date]
        pairs.append({
            "ticker": t.ticker, "exit_date": t.exit_date, "reentry_date": nxt["entry_date"],
            "gap_td": int(gap),
            "mh_pnl": float(t.pnl_gbp), "re_pnl": float(nxt["pnl_gbp"]),
            "combined": float(t.pnl_gbp) + float(nxt["pnl_gbp"]),
            "mh_pos": float(t.position_gbp), "re_pos": float(nxt["position_gbp"]),
            "re_exit_reason": nxt["exit_reason"],
        })
    pr = pd.DataFrame(pairs)
    out["n_max_holding_with_any_later_reentry"] = int(len(pr))

    print(f"\n  {'within':>8} {'n':>6} {'% of MH':>8} {'MH leg P&L':>13} {'re-entry P&L':>14} "
          f"{'combined':>12} {'FX paid':>10} {'net+/net-':>12}")
    rows = []
    for g in GAPS:
        s = pr[pr["gap_td"] <= g] if len(pr) else pr
        if not len(s):
            continue
        fx = (s["mh_pos"].sum() + s["re_pos"].sum()) * FX_ROUND_TRIP
        r = {
            "within_trading_days": g, "n": int(len(s)),
            "pct_of_max_holding_exits": round(100.0 * len(s) / len(mh), 2) if len(mh) else 0.0,
            "max_holding_leg_pnl_gbp": round(float(s["mh_pnl"].sum()), 2),
            "reentry_leg_pnl_gbp": round(float(s["re_pnl"].sum()), 2),
            "combined_pnl_gbp": round(float(s["combined"].sum()), 2),
            "fx_paid_on_roundtrip_gbp": round(float(fx), 2),
            "n_pairs_net_positive": int((s["combined"] > 0).sum()),
            "n_pairs_net_negative": int((s["combined"] <= 0).sum()),
            "median_gap_td": float(s["gap_td"].median()),
        }
        rows.append(r)
        print(f"  {g:>8} {r['n']:>6} {r['pct_of_max_holding_exits']:>7.1f}% "
              f"{r['max_holding_leg_pnl_gbp']:>13,.0f} {r['reentry_leg_pnl_gbp']:>14,.0f} "
              f"{r['combined_pnl_gbp']:>12,.0f} {r['fx_paid_on_roundtrip_gbp']:>10,.0f} "
              f"{r['n_pairs_net_positive']:>5}/{r['n_pairs_net_negative']:<6}")
    out["by_gap"] = rows

    if len(pr):
        print(f"\n  gap distribution (trading days to re-entry): "
              f"p10={pr['gap_td'].quantile(.10):.0f} p25={pr['gap_td'].quantile(.25):.0f} "
              f"median={pr['gap_td'].median():.0f} p75={pr['gap_td'].quantile(.75):.0f} "
              f"p90={pr['gap_td'].quantile(.90):.0f}")
        out["gap_distribution"] = {f"p{p}": float(pr["gap_td"].quantile(p / 100))
                                   for p in (10, 25, 50, 75, 90)}
        # What did the re-entry leg go on to do? (composition, not a counterfactual)
        near = pr[pr["gap_td"] <= 30]
        if len(near):
            print(f"  re-entry leg exit reasons (within 30td): "
                  f"{near['re_exit_reason'].value_counts().to_dict()}")
            out["reentry_exit_reasons_within_30td"] = {
                k: int(v) for k, v in near["re_exit_reason"].value_counts().items()}
        pr.to_csv(HERE / f"stage0_pairs_{label}.csv", index=False)

    # Same measurement for STOP_LOSS, as the comparison the earlier study made,
    # so the MAX_HOLDING figure is read against a known reference rather than
    # in isolation.
    sl = td[td["exit_reason"] == "STOP_LOSS"]
    sl_pairs = []
    for t in sl.itertuples():
        later = td[(td["ticker"] == t.ticker) & (td["entry_date"] > t.exit_date)]
        if later.empty:
            continue
        nxt = later.iloc[0]
        gap = pos[nxt["entry_date"]] - pos[t.exit_date]
        if gap <= 30:
            sl_pairs.append(float(t.pnl_gbp) + float(nxt["pnl_gbp"]))
    if sl_pairs:
        out["stop_loss_reference_within_30td"] = {
            "n": len(sl_pairs), "pct_of_stop_exits": round(100.0 * len(sl_pairs) / len(sl), 2),
            "combined_pnl_gbp": round(float(sum(sl_pairs)), 2),
        }
        print(f"\n  [reference] STOP_LOSS re-entered within 30td: {len(sl_pairs):,} "
              f"({100.0*len(sl_pairs)/len(sl):.1f}% of {len(sl):,}), "
              f"combined £{sum(sl_pairs):,.0f}")
    return out


def main() -> None:
    res = {}
    for label, path in TRADES.items():
        res[label] = analyse(label, path)
    (HERE / "stage0_results.json").write_text(json.dumps(res, indent=2, default=str))
    print(f"\nWrote stage0_results.json")


if __name__ == "__main__":
    main()
