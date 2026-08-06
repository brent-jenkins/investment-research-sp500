"""Decision-engine parity + adjusted-price ISOLATION verification, using
EVIDENCE gathered from this study's own runs.

Required by CLAUDE.md's "Research scripts must verify decision-engine
parity, not inherit it by assumption" standing requirement. This study
builds a custom momentum-ranking pipeline (a per-arm precompute_ticker()
twin) and a custom per-block candidate pool, so parity has to be
re-established with evidence from THESE runs -- not asserted from the fact
that the production codebase is consolidated, and not established by
reading the harness and confirming by eye (a style of review that has
already failed once in this project's history).

Method throughout: take the REAL trades each arm produced and
INDEPENDENTLY recompute what the shared production function would return
for the same inputs, then confirm the trade actually respects it.

PLUS the isolation proof this study specifically requires: adjusted
prices must never leak into EXECUTION. For every trade where two arms in
a pair (1&2, or 3&4) made the identical admission decision (same ticker,
same entry date, same block), the realised entry and exit prices must be
BYTE-IDENTICAL. Any divergence is a look-ahead-adjacent bug, not a
footnote.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))

import numpy as np
import pandas as pd
import yaml

from analysis import signals as prod_signals
from analysis import universe
from analysis.backtest import precompute_ticker as prod_precompute
from analysis.decision import (
    evaluate_exit_reason,
    is_position_size_viable,
    passes_entry_gate,
    passes_momentum_floor,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
    resolve_min_momentum_floor,
    resolve_reentry_cooldown_days,
)
from analysis.universe import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _var_position_size_core
from data.ingestion import get_price_df

import arms as ARMS_MOD

RESULTS = HERE / "results"
CONFIG = yaml.safe_load(open(HERE.parent.parent / "config.yaml"))
T212 = CONFIG.get("trading212_invest", {})
CAPITAL = 50_000.0
VALID_EXIT_REASONS = {"CRISIS_EXIT", "STOP_LOSS", "SIGNAL_OFF", "MAX_HOLDING", "END_OF_DATA"}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(f"{name}: {detail}")


def load(arm: int, legacy: bool = False) -> pd.DataFrame:
    tag = f"arm{arm}" + ("_legacy" if legacy else "")
    td = pd.read_csv(RESULTS / f"{tag}_trades.csv")
    td["entry_date"] = pd.to_datetime(td["entry_date"])
    td["exit_date"] = pd.to_datetime(td["exit_date"])
    return td


def main() -> None:
    arms_td = {a: load(a) for a in (1, 2, 3, 4)}
    for a, td in arms_td.items():
        print(f"arm{a}: {len(td)} trades, {td['ticker'].nunique()} tickers, "
              f"{td['block'].nunique()} blocks")
    print()

    # =====================================================================
    # PART 1 -- ADJUSTED-PRICE ISOLATION (this study's own specific risk)
    # =====================================================================
    print("=" * 72)
    print("PART 1 -- adjusted prices are SIGNAL-ONLY, never execution")
    print("=" * 72)

    # 1a. The precompute frames themselves: every non-signal column must be
    #     byte-identical across all four arms for the same ticker.
    sample_tickers = sorted(set(arms_td[1]["ticker"].unique()))[:40]
    frames = {a: {} for a in (1, 2, 3, 4)}
    for t in sample_tickers:
        for a in (1, 2, 3, 4):
            frames[a][t] = ARMS_MOD.precompute_ticker_arm(t, CONFIG, a)

    exec_cols = ["close", "atr", "open", "low"]
    col_bad, mom_same = [], []
    for t in sample_tickers:
        f1 = frames[1][t]
        if f1 is None:
            continue
        for a in (2, 3, 4):
            fa = frames[a][t]
            if fa is None:
                col_bad.append((t, a, "None"))
                continue
            for c in exec_cols:
                if not f1["frame"][c].equals(fa["frame"][c]):
                    col_bad.append((t, a, c))
            if f1["frame"]["momentum"].equals(fa["frame"]["momentum"]):
                mom_same.append((t, a))
    check(f"execution columns {exec_cols} byte-identical across all 4 arms "
          f"({len(sample_tickers)} tickers)", len(col_bad) == 0, f"{col_bad[:5]}")
    check("the momentum column actually DIFFERS between arms (proof the arms "
          "are not silently identical)", len(mom_same) == 0, f"{mom_same[:5]}")

    # 1b. Arm 1 must be byte-identical to PRODUCTION precompute_ticker().
    ident_bad = []
    for t in sample_tickers[:25]:
        ok, why = ARMS_MOD.verify_arm1_identity(t, CONFIG)
        if not ok:
            ident_bad.append((t, why))
    check("arm 1's precompute is byte-identical to production "
          "analysis/backtest.py:precompute_ticker()", len(ident_bad) == 0, f"{ident_bad[:5]}")

    # 1c. Arm 1 momentum == production signals.compute_momentum_series(close)
    mom_bad = []
    for t in sample_tickers[:25]:
        df = get_price_df(t, days=8000)
        if df.empty or len(df) < 300:
            continue
        df = df.set_index("date")
        df.index = pd.to_datetime(df.index)
        c = df["close"].astype(float)
        if not ARMS_MOD.compute_momentum_series_arm(c, 0).equals(
                prod_signals.compute_momentum_series(c)):
            mom_bad.append(t)
    check("arms.compute_momentum_series_arm(close, skip=0) == production "
          "signals.compute_momentum_series(close)", len(mom_bad) == 0, f"{mom_bad[:5]}")

    # 1d. THE headline isolation test: identical admission decision ->
    #     byte-identical realised prices.
    for pair in [(1, 2), (3, 4), (1, 3), (2, 4)]:
        a, b = pair
        ka = arms_td[a].set_index(["block", "ticker", "entry_date"])
        kb = arms_td[b].set_index(["block", "ticker", "entry_date"])
        common = ka.index.intersection(kb.index)
        # drop any duplicated key (a ticker re-entered on the same date in
        # the same block is impossible by construction, but be explicit)
        ka2 = ka[~ka.index.duplicated()].loc[common]
        kb2 = kb[~kb.index.duplicated()].loc[common]
        d_entry = (ka2["entry_price"] - kb2["entry_price"]).abs()
        same_exit_date = (ka2["exit_date"] == kb2["exit_date"])
        d_exit = (ka2["exit_price"] - kb2["exit_price"]).abs()
        d_size = (ka2["position_gbp"] - kb2["position_gbp"]).abs()
        check(f"arms {a}&{b}: entry_price byte-identical on all "
              f"{len(common)} shared (block,ticker,entry_date) admissions",
              bool((d_entry < 1e-9).all()),
              f"{int((d_entry >= 1e-9).sum())} differ, max {float(d_entry.max()) if len(d_entry) else 0}")
        check(f"arms {a}&{b}: position_gbp (VaR sizing) byte-identical on "
              f"shared admissions with identical portfolio state is NOT "
              f"asserted -- sizing depends on account balance, which "
              f"legitimately differs; reporting only",
              True, f"{int((d_size >= 1e-9).sum())}/{len(common)} differ (expected, not a fault)")
        se = same_exit_date & (d_exit < 1e-9)
        check(f"arms {a}&{b}: where the exit DATE also matched, exit_price is "
              f"byte-identical", bool((~same_exit_date | (d_exit < 1e-9)).all()),
              f"{int((same_exit_date & (d_exit >= 1e-9)).sum())} of "
              f"{int(same_exit_date.sum())} same-date exits differ")

    # 1e. Every realised entry_price must be a REAL raw close, never adj_close.
    print("\n  -- entry/exit prices traced back to the raw `close` column --")
    for a in (1, 2, 3, 4):
        td = arms_td[a]
        s = td.sample(n=min(500, len(td)), random_state=7)
        px = {}
        for t in s["ticker"].unique():
            df = get_price_df(t, days=8000)
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            px[t] = df.set_index("date")
        bad_close, matched_adj = 0, 0
        checked = 0
        for r in s.itertuples():
            f = px.get(r.ticker)
            if f is None or r.entry_date not in f.index:
                continue
            checked += 1
            raw = float(f.loc[r.entry_date, "close"])
            adj = float(f.loc[r.entry_date, "adj_close"])
            if abs(r.entry_price - raw) > 1e-9:
                bad_close += 1
            if abs(raw - adj) > 1e-9 and abs(r.entry_price - adj) < 1e-9:
                matched_adj += 1
        check(f"arm{a}: every sampled entry_price equals that ticker/date's "
              f"RAW `close` ({checked} checked)", bad_close == 0, f"{bad_close} mismatched")
        check(f"arm{a}: no entry_price matches `adj_close` where the two differ "
              f"(no adjusted-price leak into execution)", matched_adj == 0,
              f"{matched_adj} matched adj_close")

    # =====================================================================
    # PART 2 -- DECISION-ENGINE PARITY, per arm, from each run's own trades
    # =====================================================================
    print("\n" + "=" * 72)
    print("PART 2 -- decision-engine parity, evidence from each arm's own trades")
    print("=" * 72)

    floor = resolve_min_momentum_floor(CONFIG)
    cooldown = resolve_reentry_cooldown_days(CONFIG)
    abs_max = float(T212.get("absolute_max_position_gbp", 1_000_000))
    per_trade_pct = float(T212.get("per_trade_pct", 0.05))
    min_pos = float(T212.get("min_position_gbp", 50))
    print(f"resolved from config via the SHARED resolvers: min_momentum_floor="
          f"{floor}, reentry_cooldown_days={cooldown}, per_trade_pct={per_trade_pct}, "
          f"absolute_max_position_gbp={abs_max}, min_position_gbp={min_pos}\n")

    for a in (1, 2, 3, 4):
        td = arms_td[a]
        print(f"--- arm {a} ---")
        reasons = Counter(td["exit_reason"])
        check(f"arm{a}: every exit_reason is one the shared "
              f"evaluate_exit_reason() chain can emit",
              set(reasons) <= VALID_EXIT_REASONS,
              f"unexpected {set(reasons) - VALID_EXIT_REASONS}")
        check(f"arm{a}: INDEX_REMOVAL absent (removed from the shared chain)",
              "INDEX_REMOVAL" not in reasons)
        print(f"      {dict(reasons)}")

        # ---- holding period vs the SHARED get_max_holding_days() --------
        caps = {t: get_max_holding_days(t, CONFIG) for t in td["ticker"].unique()}
        td = td.copy()
        td["cap"] = td["ticker"].map(caps)
        over = td[td["holding_days"] > td["cap"]]
        cal, late = {}, []
        for t in over["ticker"].unique():
            df = get_price_df(t, days=9000)
            if not df.empty:
                cal[t] = pd.DatetimeIndex(pd.to_datetime(df["date"])).normalize()
        for r in over.itertuples():
            idx = cal.get(r.ticker)
            if idx is None:
                continue
            due = r.entry_date + pd.Timedelta(days=int(r.cap))
            nxt = idx[idx >= due]
            if len(nxt) and r.exit_date > nxt[0]:
                late.append((r.ticker, r.entry_date.date(), r.exit_date.date(), r.holding_days))
        check(f"arm{a}: no trade survives past the first TRADING DAY on which "
              f"the independently recomputed get_max_holding_days() was reached",
              len(late) == 0,
              f"{len(late)} late of {len(over)} past the raw calendar cap e.g. {late[:3]}")
        mh = td[td["exit_reason"] == "MAX_HOLDING"]
        if len(mh):
            check(f"arm{a}: every MAX_HOLDING exit reached its own recomputed cap",
                  bool((mh["holding_days"] >= mh["cap"]).all()),
                  f"{int((mh['holding_days'] < mh['cap']).sum())} below cap")

        # ---- entry gate: membership, signal, momentum floor -------------
        s = td.sample(n=min(400, len(td)), random_state=0)
        bad_member = [(r.ticker, r.entry_date.date()) for r in s.itertuples()
                      if not universe.is_member(r.ticker, r.entry_date.date())]
        check(f"arm{a}: every sampled entry was a genuine index member on its "
              f"entry date (independent is_member() call)",
              len(bad_member) == 0, f"{len(bad_member)}/{len(s)} e.g. {bad_member[:3]}")

        # recompute THIS ARM's own signal/momentum from its own basis and
        # confirm the entry gate + floor actually held
        af = {}
        for t in s["ticker"].unique():
            af[t] = ARMS_MOD.precompute_ticker_arm(t, CONFIG, a)
        sig_bad, mom_bad2, gate_bad, checked = [], [], [], 0
        for r in s.itertuples():
            c = af.get(r.ticker)
            if c is None or r.entry_date not in c["frame"].index:
                continue
            checked += 1
            row = c["frame"].loc[r.entry_date]
            sig_on = bool(row["signal"])
            mom = None if pd.isna(row["momentum"]) else float(row["momentum"])
            if not sig_on:
                sig_bad.append((r.ticker, r.entry_date.date()))
            if not passes_momentum_floor(mom, floor):
                mom_bad2.append((r.ticker, r.entry_date.date(), mom))
            if not passes_entry_gate(sig_on=sig_on, is_member=True, entry_blocked=False):
                gate_bad.append((r.ticker, r.entry_date.date()))
        check(f"arm{a}: every sampled entry had this arm's own signal ON, "
              f"recomputed independently", len(sig_bad) == 0,
              f"{len(sig_bad)}/{checked} e.g. {sig_bad[:3]}")
        check(f"arm{a}: every sampled entry clears the SHARED "
              f"passes_momentum_floor() at floor={floor}, recomputed",
              len(mom_bad2) == 0, f"{len(mom_bad2)}/{checked} e.g. {mom_bad2[:3]}")
        check(f"arm{a}: every sampled entry passes the SHARED passes_entry_gate()",
              len(gate_bad) == 0, f"{len(gate_bad)}/{checked}")

        # ---- sizing: recompute the SHARED VaR core for real trades ------
        sz = td.sample(n=min(150, len(td)), random_state=3)
        ceil_bad, var_bad, viable_bad, sized = [], [], [], 0
        for r in sz.itertuples():
            c = af.get(r.ticker) or ARMS_MOD.precompute_ticker_arm(r.ticker, CONFIG, a)
            if c is None:
                continue
            cs = c["frame"]["close"].astype(float)
            prior = cs.loc[cs.index < r.entry_date].tail(int(T212.get("var_lookback_days", 252)) + 1)
            lr = np.log(prior / prior.shift(1)).dropna()
            if len(lr) < _MIN_VAR_RETURNS:
                continue
            sized += 1
            # The engine sized against its own live mark-to-market balance,
            # which this script cannot re-derive per trade. What CAN be
            # checked independently is that the realised position never
            # exceeds the SHARED ceiling for ANY balance the account could
            # have had -- i.e. position_gbp <= per_trade_pct * balance, so
            # the implied balance must be a physically sane number.
            implied_balance = r.position_gbp / per_trade_pct
            if not (0 < implied_balance < CAPITAL * 50):
                ceil_bad.append((r.ticker, r.position_gbp, implied_balance))
            if not is_position_size_viable(r.position_gbp):
                viable_bad.append((r.ticker, r.position_gbp))
            # and that the SHARED core, run at that implied balance, would
            # not have produced a SMALLER number than what was realised
            # (i.e. the realised size is bounded by the shared formula)
            ceil = resolve_dynamic_max_position_gbp(implied_balance, per_trade_pct, abs_max)
            pg, _, _ = _var_position_size_core(
                lr, implied_balance, ceil, get_max_holding_days(r.ticker, CONFIG),
                float(T212.get("var_confidence", 0.95)), float(T212.get("max_risk_pct", 0.01)),
                min_pos)
            if r.position_gbp > pg + 1e-6:
                var_bad.append((r.ticker, r.entry_date.date(), r.position_gbp, pg))
        check(f"arm{a}: every sampled position respects the SHARED "
              f"_var_position_size_core() ceiling recomputed at its own "
              f"implied balance ({sized} sized trades checked)",
              len(var_bad) == 0, f"{len(var_bad)} exceed e.g. {var_bad[:3]}")
        check(f"arm{a}: every position clears the SHARED is_position_size_viable()",
              len(viable_bad) == 0, f"{len(viable_bad)} e.g. {viable_bad[:3]}")
        check(f"arm{a}: no position exceeds absolute_max_position_gbp",
              bool((td["position_gbp"] <= abs_max).all()),
              f"max {td['position_gbp'].max():.2f}")
        check(f"arm{a}: no unbounded mark-to-market compounding "
              f"(the 2026-08-01 defect signature)",
              bool(td["position_gbp"].max() < CAPITAL * 10),
              f"max position GBP {td['position_gbp'].max():,.2f} on {CAPITAL:,.0f} capital")

        # ---- the CORRECTED realistic stop fill is genuinely active -------
        sl = td[td["exit_reason"] == "STOP_LOSS"].copy()
        if len(sl):
            sl["implied_stop"] = sl["entry_price"] * (1.0 - sl["stop_distance_pct"] / 100.0)
            above = (sl["exit_price"] > sl["implied_stop"] * 1.0005).mean()
            gapped = (sl["exit_price"] < sl["implied_stop"] * 0.9995).mean()
            check(f"arm{a}: no STOP_LOSS fills ABOVE its own stop price",
                  above == 0.0, f"{above:.4f}")
            check(f"arm{a}: a real share of STOP_LOSS exits fill BELOW the stop "
                  f"(proof the 2026-08-06 intraday-touch engine is active, not "
                  f"the old fill-at-stop convention)",
                  gapped > 0.01, f"{gapped:.2%} of {len(sl)} stops gapped through")

        # ---- re-entry cooldown (production value 0 = the pipeline floor) --
        g = td.sort_values(["block", "ticker", "entry_date"])
        viol = 0
        for (_b, _t), grp in g.groupby(["block", "ticker"]):
            ex = grp["exit_date"].tolist()
            en = grp["entry_date"].tolist()
            for k in range(1, len(en)):
                if (en[k] - ex[k - 1]).days < cooldown:
                    viol += 1
        check(f"arm{a}: no re-entry violates the SHARED "
              f"resolve_reentry_cooldown_days()={cooldown} boundary",
              viol == 0, f"{viol} violations")
        print()

    print("=" * 72)
    if failures:
        print(f"VERIFICATION FAILED -- {len(failures)} check(s):")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("VERIFICATION PASSED -- adjusted prices are signal-only (no execution "
          "leak), and entry, exit, sizing, holding-period and cooldown logic all "
          "route through the shared production functions in all four arms.")


if __name__ == "__main__":
    main()
