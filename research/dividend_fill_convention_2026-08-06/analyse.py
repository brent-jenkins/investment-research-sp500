"""Dividend adjustment, commission, and fill-price convention audit.

Post-hoc analysis of the 3,161-trade continuous-walk run in
research/concentration_risk_2026-08-05/results/full_trades_continuous.json
(backtest_convention: continuous_walk, live 503-ticker universe, real
unmodified simulate_portfolio_compounding_mtm(), starting capital GBP 50,000,
per_trade_pct 0.05, window end 2026-08-03).

This script does NOT run the backtest engine, construct a candidate pool, or
monkeypatch analysis.universe.is_member(). It only reads an existing trade
set plus the price_data table, so CLAUDE.md's "Research scripts must verify
decision-engine parity" requirement does not apply -- there is no pipeline
interception here to verify. What it does assert is that the trade set it
reads is genuinely the engine's own output, by reproducing the recorded
entry_price from price_data (see verify_fill_convention()).

Writes full trade-level output to results/ per CLAUDE.md's "preserve full
trade-level output, not just the formula" standard, since the figures below
are cited in CLAUDE_HISTORY.md.

Run from the project root:  python3 research/dividend_fill_convention_2026-08-06/analyse.py
"""
import csv
import json
import os
import sqlite3
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "database", "investment_research.db")
TRADES = os.path.join(ROOT, "research", "concentration_risk_2026-08-05",
                      "results", "full_trades_continuous.json")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# Ex-dividend detection threshold. The cumulative adjustment factor
#     f(t) = adj_close(t) / close(t)
# steps UP on each ex-dividend date. An ex-dividend is recorded iff
#     f(t) > f(t-1) * (1 + EXDIV_REL_THRESHOLD)
# Calibrated directly, not guessed: for KO over the full 4,926-row history the
# steps separate cleanly into 78 real quarterly dividends (every one > 1e-3)
# and floating-point rounding noise (every one < 1e-6), with nothing in
# between. 78 == 4 dividends/yr * 19.6 yr of coverage. At 1e-9 the same scan
# returns 2,443 "events" for KO alone (noise-dominated); at 1e-5 it returns 78.
EXDIV_REL_THRESHOLD = 1e-5


def load():
    doc = json.load(open(TRADES))
    trades = doc["trades"]
    con = sqlite3.connect(DB)
    tickers = sorted({t["ticker"] for t in trades})
    bars = defaultdict(dict)
    q = ("select ticker, date, open, low, close, adj_close from price_data "
         "where ticker in (%s)" % ",".join("?" * len(tickers)))
    for tk, dt, op, lo, cl, ac in con.execute(q, tickers):
        bars[tk][dt[:10]] = {"open": op, "low": lo, "close": cl, "adj": ac}
    return doc, trades, bars


def verify_fill_convention(trades, bars):
    """Confirm the recorded fill convention against price_data directly."""
    n = ec = eo = xc = 0
    for t in trades:
        b = bars[t["ticker"]].get(t["entry_date"])
        if not b or not b["close"]:
            continue
        n += 1
        if abs(t["entry_price"] - b["close"]) < 1e-6 * max(1.0, b["close"]):
            ec += 1
        if b["open"] and abs(t["entry_price"] - b["open"]) < 1e-6 * max(1.0, b["open"]):
            eo += 1
        xb = bars[t["ticker"]].get(t["exit_date"])
        if xb and xb["close"] and abs(t["exit_price"] - xb["close"]) < 1e-6 * max(1.0, xb["close"]):
            xc += 1
    return {"trades_checked": n,
            "entry_price_equals_same_day_close": ec,
            "entry_price_equals_same_day_open": eo,
            "exit_price_equals_same_day_close": xc}


def detect_exdiv(bars):
    """ticker -> [(date, div_per_share, drop_frac_of_prior_close)]."""
    ev = defaultdict(list)
    for tk, series in bars.items():
        prev_f = prev_close = None
        for dt in sorted(series):
            b = series[dt]
            cl, ac = b["close"], b["adj"]
            if not cl or not ac or cl <= 0:
                prev_f = prev_close = None
                continue
            f = ac / cl
            if prev_f and f > prev_f * (1 + EXDIV_REL_THRESHOLD):
                # D = prev_close * (1 - prev_f/f)
                dps = prev_close * (1 - prev_f / f)
                if dps > 0:
                    ev[tk].append((dt, dps, dps / prev_close))
            prev_f, prev_close = f, cl
    return ev


def main():
    os.makedirs(OUT, exist_ok=True)
    doc, trades, bars = load()
    reported = doc["metrics_fixed_denominator"]
    res = {
        "source_trade_file": os.path.relpath(TRADES, ROOT),
        "backtest_convention": doc["backtest_convention"],
        "n_trades": len(trades),
        "reported_total_pnl_gbp": reported["total_pnl_gbp"],
        "reported_avg_return_pct": reported["avg_return_pct"],
        "exdiv_rel_threshold": EXDIV_REL_THRESHOLD,
    }

    res["fill_convention"] = verify_fill_convention(trades, bars)
    exdiv = detect_exdiv(bars)
    res["exdiv_events_detected"] = sum(len(v) for v in exdiv.values())

    # ---- Q5a: dividend contribution -------------------------------------
    # For a hold t0 -> t1:
    #     PR      = close(t1)/close(t0)      (what the engine actually used)
    #     TR      = adj(t1)/adj(t0)          (price + reinvested dividends)
    #     div_ret = TR - PR                  (additive return the engine drops)
    #     impact  = position_gbp * div_ret
    rows, crossed, tot_impact, tot_div = [], 0, 0.0, 0.0
    for t in trades:
        s = bars[t["ticker"]]
        b0, b1 = s.get(t["entry_date"]), s.get(t["exit_date"])
        if not b0 or not b1 or not all((b0["close"], b1["close"], b0["adj"], b1["adj"])):
            continue
        PR = b1["close"] / b0["close"]
        TR = b1["adj"] / b0["adj"]
        div_ret = TR - PR
        impact = t["position_gbp"] * div_ret
        n_ex = len([e for e in exdiv.get(t["ticker"], []) if t["entry_date"] < e[0] <= t["exit_date"]])
        crossed += 1 if n_ex else 0
        tot_impact += impact
        tot_div += div_ret
        rows.append({**t, "price_return_factor": PR, "total_return_factor": TR,
                     "dividend_return": div_ret, "dividend_impact_gbp": impact,
                     "n_exdiv_crossed": n_ex})
    n = len(rows)
    res["dividends"] = {
        "trades_analysed": n,
        "trades_crossing_exdiv": crossed,
        "pct_crossing": round(100 * crossed / n, 2),
        "mean_div_return_pct_all": round(100 * tot_div / n, 4),
        "mean_div_return_pct_crossing": round(
            100 * sum(r["dividend_return"] for r in rows if r["n_exdiv_crossed"]) / crossed, 4),
        "aggregate_understatement_gbp": round(tot_impact, 2),
        "pct_of_reported_pnl": round(100 * tot_impact / reported["total_pnl_gbp"], 2),
        "avg_return_pct_corrected": round(reported["avg_return_pct"] + 100 * tot_div / n, 3),
    }
    _csv(os.path.join(OUT, "per_trade_dividend_impact.csv"), rows)

    # ---- Q5b: stop-loss triggered by a price-only artefact ---------------
    # The engine fires STOP_LOSS iff close(t) <= stop_price, i.e.
    #     PR <= 1 - stop_distance_pct/100
    # The total-return equivalent of that same test is TR <= 1 - stop_dist/100.
    # Contaminated iff:  PR <= threshold < TR
    contam = []
    for r in rows:
        if r["exit_reason"] != "STOP_LOSS":
            continue
        thr = 1 - (r["stop_distance_pct"] / 100.0)
        if r["price_return_factor"] <= thr < r["total_return_factor"]:
            contam.append({
                "ticker": r["ticker"], "entry_date": r["entry_date"], "exit_date": r["exit_date"],
                "stop_distance_pct": round(r["stop_distance_pct"], 4),
                "price_return_pct": round((r["price_return_factor"] - 1) * 100, 4),
                "total_return_pct": round((r["total_return_factor"] - 1) * 100, 4),
                "position_gbp": r["position_gbp"], "recorded_pnl_gbp": round(r["pnl_gbp"], 2),
            })
    n_stop = sum(1 for r in rows if r["exit_reason"] == "STOP_LOSS")
    res["stop_contamination"] = {
        "stop_loss_trades": n_stop,
        "contaminated": len(contam),
        "pct_of_stops": round(100 * len(contam) / n_stop, 2),
        "pct_of_all_trades": round(100 * len(contam) / n, 2),
        "recorded_pnl_on_contaminated_gbp": round(sum(c["recorded_pnl_gbp"] for c in contam), 2),
    }
    _csv(os.path.join(OUT, "stop_loss_contaminated_by_exdiv.csv"), contam)

    # ---- Q4 follow-on: is the assumed STOP_LOSS fill achievable? ---------
    # The engine fills STOP_LOSS at exactly stop_price. That is impossible on
    # a true gap-through, i.e. when  open(exit_date) < stop_price.
    gaps, gap_gbp = [], 0.0
    for r in rows:
        if r["exit_reason"] != "STOP_LOSS":
            continue
        b = bars[r["ticker"]].get(r["exit_date"])
        sp = r["exit_price"]
        if not b or not b["open"] or not (b["open"] < sp * (1 - 1e-9)):
            continue
        unach = r["position_gbp"] * ((sp - b["open"]) / sp)
        gap_gbp += unach
        gaps.append({"ticker": r["ticker"], "entry_date": r["entry_date"],
                     "exit_date": r["exit_date"], "assumed_fill": round(sp, 4),
                     "actual_open": round(b["open"], 4),
                     "gap_pct": round(100 * (sp - b["open"]) / sp, 4),
                     "position_gbp": r["position_gbp"],
                     "unachievable_gbp": round(unach, 2)})
    res["stop_gap_through"] = {
        "stop_loss_trades": n_stop, "gapped_through": len(gaps),
        "pct_of_stops": round(100 * len(gaps) / n_stop, 2),
        "unachievable_gain_assumed_gbp": round(gap_gbp, 2),
        "pct_of_reported_pnl": round(100 * gap_gbp / reported["total_pnl_gbp"], 2),
    }
    _csv(os.path.join(OUT, "stop_loss_gapped_through_open.csv"), gaps)

    # ---- Q4 follow-on: daily-close stop is LATE vs a real intraday stop ---
    #     stop_price = entry_price * (1 - stop_distance_pct/100)
    # Early touch iff  exists d in (entry_date, exit_date): low(d) <= stop_price.
    # delta_gbp = position_gbp * ((stop_price - entry_price)/entry_price - pnl_pct)
    # delta_gbp < 0  =>  the intraday stop yields LESS than the recorded trade
    #                    =>  the backtest OVERSTATES by holding on.
    early, early_gbp = [], 0.0
    for r in rows:
        sp = r["entry_price"] * (1 - r["stop_distance_pct"] / 100.0)
        s = bars[r["ticker"]]
        hit = next((d for d in sorted(s)
                    if r["entry_date"] < d < r["exit_date"] and s[d]["low"] and s[d]["low"] <= sp), None)
        if not hit:
            continue
        delta = r["position_gbp"] * (((sp - r["entry_price"]) / r["entry_price"]) - r["pnl_pct"])
        early_gbp += delta
        early.append({"ticker": r["ticker"], "entry_date": r["entry_date"],
                      "exit_date": r["exit_date"], "exit_reason": r["exit_reason"],
                      "first_intraday_touch": hit, "stop_price": round(sp, 4),
                      "recorded_pnl_gbp": round(r["pnl_gbp"], 2),
                      "intraday_stop_pnl_gbp": round(r["position_gbp"] * (sp - r["entry_price"]) / r["entry_price"], 2),
                      "delta_gbp": round(delta, 2)})
    res["stop_late_trigger"] = {
        "trades_touching_stop_before_exit_day": len(early),
        "pct_of_all_trades": round(100 * len(early) / n, 2),
        "delta_gbp_if_intraday_stop_fired": round(early_gbp, 2),
        "sign_convention": "negative => intraday stop yields less => backtest OVERSTATES",
        "pct_of_reported_pnl": round(100 * abs(early_gbp) / reported["total_pnl_gbp"], 2),
    }
    _csv(os.path.join(OUT, "stop_touched_intraday_before_exit.csv"), early)

    json.dump(res, open(os.path.join(OUT, "summary.json"), "w"), indent=2)
    print(json.dumps(res, indent=2))


def _csv(path, rows):
    if not rows:
        open(path, "w").close()
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
