"""DIAGNOSTIC A -- do historical losing trades contain identifiable distress at entry?

READ-ONLY. No arms, no engine, no adoption, no fix proposed. Reads the PRESERVED
trade sets for the current live configuration plus read-only derivations from
price_data and universe_membership. No database writes.

THE HONEST LIMIT: no historical filings data exists in this project -- no
leverage, no interest coverage, no going-concern flags, no credit ratings. Every
"distress" measure below is a PROXY built from stored price and membership data.
A null therefore does not close the question; it closes the question FOR THESE
PROXIES.

PROXIES, all defined before computing (per the pre-committed instruction).

  Group I -- DIAGNOSTIC ONLY. These use information from AFTER the entry, so
  they can say whether a trade was in a name heading for trouble; they can never
  support an entry filter. Reported separately for that reason.

    P1  removal_3m / removal_6m / removal_12m
        The ticker's last real membership end_date (excluding the 2099-12-31
        still-active sentinel) falls within 3 / 6 / 12 calendar months AFTER the
        exit date.
    P2  data_stop_12m
        The ticker's last price_data row falls within 12 calendar months after
        the exit date AND is at least 30 calendar days before the table's global
        max date -- i.e. the series genuinely stopped rather than the sample
        simply ending.

  Group II -- ACTIONABLE. Computable from information available at entry, so a
  hit here would be a candidate for an entry filter (this diagnostic proposes
  none either way).

    P3a vol20_ann, P3b vol60_ann
        Annualised stdev of daily log close returns over the 20 / 60 trading
        rows ending at the entry row.
    P3c vol_change = vol20_ann / vol60_ann - 1
        Short-horizon volatility relative to medium-horizon. Positive = vol
        rising into the entry.
    P4  below_peak_pct = (max(close over [t-252, t]) - close(t)) / max * 100
        Max drawdown inside the momentum lookback window. EXACTLY the stale-peak
        diagnostic's definition, window bounded by the momentum calculation's own
        two anchors, so the two studies' numbers are directly comparable.
    P5a worst_day_60
        Most negative single-day return in the 60 rows ending at entry, in %.
        Jump / news risk that a volatility number averages away.
    P5b down_up_vol_ratio
        semi-deviation of negative daily returns / semi-deviation of positive
        daily returns, 60 rows ending at entry. >1 = downside-skewed.
    P5c entry_price_usd
        Raw close at entry. Distressed equities frequently trade at low absolute
        prices; included because it is the crudest possible proxy and therefore
        a useful control -- if it beats the constructed ones, the constructed
        ones are not adding anything.
    P5d volume_ratio = mean(volume, last 20 rows) / mean(volume, last 252 rows)
        Attention / news spike into the entry.
    P5e membership_tenure_td
        Trading rows between the start of the ticker's membership window covering
        the entry date and the entry date itself. A recent entrant is a different
        population from a long-standing member.

REPORTING (a-d per the instruction), then the pre-committed interpretation:
|Spearman| < 0.05 is reported as NULL and NOT sliced for structure.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from data.database import get_engine, get_db_path  # noqa: E402

REF = ROOT / "research/exit_mechanics_prereg_2026-08-11"
POOLS = ("pool2_clean", "pool1")
SENTINEL_END = pd.Timestamp("2099-12-31")

# Direction each proxy is hypothesised to point if distress-at-entry is real:
# +1 means "a HIGHER value indicates more distress", -1 the reverse.
DISTRESS_DIRECTION = {
    "vol20_ann": +1, "vol60_ann": +1, "vol_change": +1,
    "below_peak_pct": +1, "worst_day_60": -1, "down_up_vol_ratio": +1,
    "entry_price_usd": -1, "volume_ratio": +1, "membership_tenure_td": -1,
}
CONTINUOUS = list(DISTRESS_DIRECTION)
BINARY = ["removal_3m", "removal_6m", "removal_12m", "data_stop_12m"]
GROUP_I = set(BINARY)


def load_prices(tickers):
    eng = get_engine()
    out = {}
    with eng.connect() as c:
        for i in range(0, len(tickers), 400):
            chunk = tickers[i:i + 400]
            q = text("SELECT ticker, date, close, volume FROM price_data "
                     f"WHERE ticker IN ({','.join(f':t{j}' for j in range(len(chunk)))}) "
                     "ORDER BY ticker, date")
            df = pd.read_sql(q, c, params={f"t{j}": t for j, t in enumerate(chunk)})
            df["date"] = pd.to_datetime(df["date"]).dt.normalize()
            for tk, g in df.groupby("ticker"):
                out[tk] = g.reset_index(drop=True)
    return out


def load_membership():
    with get_engine().connect() as c:
        df = pd.read_sql(text(
            "SELECT ticker, start_date, end_date FROM universe_membership"), c)
    df["start_date"] = pd.to_datetime(df["start_date"]).dt.normalize()
    df["end_date"] = pd.to_datetime(df["end_date"]).dt.normalize()
    return df


def semidev(r, positive):
    s = r[r > 0] if positive else r[r < 0]
    if len(s) < 5:
        return np.nan
    return float(np.sqrt((s ** 2).mean()))


def compute(trades, prices, memb, global_max):
    rows = []
    for _, tr in trades.iterrows():
        tk = tr["ticker"]
        e = pd.Timestamp(tr["entry_date"]).normalize()
        x = pd.Timestamp(tr["exit_date"]).normalize()
        rec = {"ticker": tk, "entry_date": e, "exit_date": x,
               "pnl_pct": float(tr["pnl_pct"]) * 100.0,
               "pnl_gbp": float(tr["pnl_gbp"]),
               "exit_reason": tr["exit_reason"]}

        p = prices.get(tk)
        if p is None or p.empty:
            rows.append(rec)
            continue

        # ---- Group I -------------------------------------------------------
        m = memb[memb["ticker"] == tk]
        real_ends = m.loc[m["end_date"] < SENTINEL_END, "end_date"]
        last_end = real_ends.max() if len(real_ends) else pd.NaT
        for months, key in ((3, "removal_3m"), (6, "removal_6m"), (12, "removal_12m")):
            rec[key] = bool(pd.notna(last_end) and
                            x <= last_end <= x + pd.DateOffset(months=months))
        last_px = p["date"].iloc[-1]
        rec["data_stop_12m"] = bool(
            last_px <= x + pd.DateOffset(months=12)
            and last_px <= global_max - pd.Timedelta(days=30))

        # ---- Group II ------------------------------------------------------
        # index of the entry row: last row on or before the entry date
        pos = int(p["date"].searchsorted(e, side="right")) - 1
        if pos < 0:
            rows.append(rec)
            continue
        rec["entry_price_usd"] = float(p["close"].iloc[pos])

        close = p["close"].to_numpy(dtype=float)
        vol = p["volume"].to_numpy(dtype=float)
        lr = np.diff(np.log(np.where(close > 0, close, np.nan)))  # lr[i] = ret into row i+1

        def rets(n):
            lo = pos - n
            if lo < 1:
                return None
            s = lr[lo:pos]                      # n returns ending at the entry row
            s = s[np.isfinite(s)]
            return s if len(s) >= max(5, n // 2) else None

        r20, r60 = rets(20), rets(60)
        if r20 is not None:
            rec["vol20_ann"] = float(r20.std(ddof=1) * np.sqrt(252) * 100.0)
        if r60 is not None:
            rec["vol60_ann"] = float(r60.std(ddof=1) * np.sqrt(252) * 100.0)
            rec["worst_day_60"] = float(r60.min() * 100.0)
            du, uu = semidev(r60, False), semidev(r60, True)
            if np.isfinite(du) and np.isfinite(uu) and uu > 0:
                rec["down_up_vol_ratio"] = float(du / uu)
        if "vol20_ann" in rec and rec.get("vol60_ann"):
            rec["vol_change"] = rec["vol20_ann"] / rec["vol60_ann"] - 1.0

        lo = max(0, pos - 252)
        peak = float(close[lo:pos + 1].max())
        if peak > 0:
            rec["below_peak_pct"] = (peak - close[pos]) / peak * 100.0

        if pos >= 252:
            v20 = np.nanmean(vol[pos - 19:pos + 1])
            v252 = np.nanmean(vol[pos - 251:pos + 1])
            if np.isfinite(v20) and np.isfinite(v252) and v252 > 0:
                rec["volume_ratio"] = float(v20 / v252)

        win = m[(m["start_date"] <= e) & (m["end_date"] >= e)]
        if len(win):
            start = win["start_date"].min()
            rec["membership_tenure_td"] = int(
                ((p["date"] >= start) & (p["date"] <= e)).sum())

        rows.append(rec)
    return pd.DataFrame(rows)


def spearman(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 30:
        return None, int(m.sum())
    return round(float(pd.Series(a[m]).corr(pd.Series(b[m]), method="spearman")), 4), int(m.sum())


def summarise(df):
    res = {"n_trades": int(len(df))}
    win = df["pnl_pct"] > 0
    res["n_winners"] = int(win.sum())
    res["n_losers"] = int((~win).sum())
    tot_gain = float(df.loc[df["pnl_gbp"] > 0, "pnl_gbp"].sum())
    tot_loss = float(-df.loc[df["pnl_gbp"] < 0, "pnl_gbp"].sum())
    res["total_gains_gbp"] = round(tot_gain, 2)
    res["total_losses_gbp"] = round(tot_loss, 2)

    proxies = {}
    for name in BINARY + CONTINUOUS:
        if name not in df.columns:
            continue
        col = df[name]
        entry = {"group": "I (diagnostic only)" if name in GROUP_I else "II (actionable)"}

        if name in BINARY:
            flag = col.fillna(False).astype(bool)
        else:
            v = pd.to_numeric(col, errors="coerce")
            d = DISTRESS_DIRECTION[name]
            thr = v.quantile(0.80 if d > 0 else 0.20)
            flag = (v >= thr) if d > 0 else (v <= thr)
            flag = flag.fillna(False)
            entry["flag_rule"] = (f"top quintile: {name} >= {thr:.4f}" if d > 0
                                  else f"bottom quintile: {name} <= {thr:.4f}")
            entry["n_defined"] = int(v.notna().sum())
            for label, sub in (("winners", v[win]), ("losers", v[~win])):
                s = sub.dropna()
                entry[label] = {
                    "n": int(len(s)),
                    "mean": round(float(s.mean()), 4) if len(s) else None,
                    "median": round(float(s.median()), 4) if len(s) else None,
                    "p25": round(float(s.quantile(0.25)), 4) if len(s) else None,
                    "p75": round(float(s.quantile(0.75)), 4) if len(s) else None,
                }
            rho, n = spearman(v.to_numpy(dtype=float),
                              df["pnl_pct"].to_numpy(dtype=float))
            entry["spearman_vs_pnl_pct"] = rho
            entry["spearman_n"] = n
            entry["verdict"] = (None if rho is None else
                                ("NULL (|rho| < 0.05)" if abs(rho) < 0.05
                                 else "CLEARS the 0.05 threshold"))

        # (c) and (d) for every proxy, binary and continuous alike
        entry["fires_n"] = int(flag.sum())
        entry["fires_pct_of_trades"] = round(100.0 * float(flag.mean()), 2)
        entry["fires_pct_of_winners"] = round(100.0 * float(flag[win].mean()), 2) if win.any() else None
        entry["fires_pct_of_losers"] = round(100.0 * float(flag[~win].mean()), 2) if (~win).any() else None
        fl = float(-df.loc[flag & (df["pnl_gbp"] < 0), "pnl_gbp"].sum())
        fg = float(df.loc[flag & (df["pnl_gbp"] > 0), "pnl_gbp"].sum())
        entry["share_of_total_losses_pct"] = round(100.0 * fl / tot_loss, 2) if tot_loss else None
        entry["share_of_total_gains_pct"] = round(100.0 * fg / tot_gain, 2) if tot_gain else None
        entry["net_pnl_of_flagged_gbp"] = round(float(df.loc[flag, "pnl_gbp"].sum()), 2)
        proxies[name] = entry

    res["proxies"] = proxies
    return res


def main():
    print(f"DB: {get_db_path()}")
    with get_engine().connect() as c:
        global_max = pd.Timestamp(c.execute(
            text("SELECT MAX(date) FROM price_data")).scalar()).normalize()
    print(f"price_data global max date: {global_max.date()}")
    memb = load_membership()

    out = {"price_data_max_date": str(global_max.date()), "pools": {}}
    for pool in POOLS:
        t = pd.read_csv(REF / f"trades_{pool}_s3.0.csv")
        t["entry_date"] = pd.to_datetime(t["entry_date"])
        t["exit_date"] = pd.to_datetime(t["exit_date"])
        prices = load_prices(sorted(t["ticker"].unique()))
        df = compute(t, prices, memb, global_max)
        df.to_csv(HERE / f"proxies_{pool}.csv", index=False)
        out["pools"][pool] = summarise(df)
        print(f"{pool}: {len(df)} trades, proxies written")

    (HERE / "diagnostic_a.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    for pool, res in out["pools"].items():
        print(f"\n{'='*100}\n{pool}  n={res['n_trades']}  winners={res['n_winners']} "
              f"losers={res['n_losers']}  gains=GBP{res['total_gains_gbp']:,.0f} "
              f"losses=GBP{res['total_losses_gbp']:,.0f}\n{'='*100}")
        print(f"{'proxy':<24}{'rho':>9}{'W med':>10}{'L med':>10}{'fires%':>9}"
              f"{'%loss':>8}{'%gain':>8}  verdict")
        for name, e in res["proxies"].items():
            rho = e.get("spearman_vs_pnl_pct")
            wm = e.get("winners", {}).get("median")
            lm = e.get("losers", {}).get("median")
            print(f"{name:<24}"
                  f"{('n/a' if rho is None else f'{rho:+.4f}'):>9}"
                  f"{('-' if wm is None else f'{wm:.3f}'):>10}"
                  f"{('-' if lm is None else f'{lm:.3f}'):>10}"
                  f"{e['fires_pct_of_trades']:>8.1f}%"
                  f"{e['share_of_total_losses_pct']:>7.1f}%"
                  f"{e['share_of_total_gains_pct']:>7.1f}%"
                  f"  {e.get('verdict') or e['group']}")


if __name__ == "__main__":
    main()
