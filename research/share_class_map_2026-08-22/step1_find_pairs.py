"""Find share-class sibling groups actually present in this system's data.

READ-ONLY. Generates candidates structurally (never from memory), then
CONFIRMS each with daily-return correlation over the overlapping history --
two share classes of one company track each other almost exactly, unrelated
companies do not. Controls are checked in both directions before any pair is
trusted, per this project's "validate a classifier against known controls
BEFORE applying it" standard.
"""
import itertools
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DB = Path(r"C:\GitHub\investment-app\database\investment_research.db")
HERE = Path(__file__).resolve().parent
CLASS_SUFFIXES = ("A", "B", "K", "L", "C")
CORR_CONFIRM = 0.85
MIN_OVERLAP = 60

# Known TRUE pairs (must be found) and known FALSE pairs (must be rejected).
POSITIVE_CONTROLS = [("GOOG", "GOOGL"), ("FOX", "FOXA"), ("NWS", "NWSA"), ("UA", "UAA")]
NEGATIVE_CONTROLS = [("A", "AA"), ("C", "CA"), ("K", "KO"), ("V", "VZ"), ("T", "TA")]


def candidate_pairs(tickers: set[str]) -> set[tuple[str, str]]:
    """Structural generation only -- no company knowledge used here."""
    out = set()
    ts = sorted(tickers)
    # (a) dotted class forms: BRK.B <-> BRK, BF.B <-> BF, X.A <-> X.B
    bases: dict[str, set[str]] = {}
    for t in ts:
        base = t.split(".")[0]
        bases.setdefault(base, set()).add(t)
    for base, group in bases.items():
        if base in tickers:
            group = group | {base}
        for a, b in itertools.combinations(sorted(group), 2):
            out.add((a, b))
    # (b) suffix-class forms: GOOG/GOOGL, FOX/FOXA, NWS/NWSA, UA/UAA, DISCA/DISCK
    stems: dict[str, set[str]] = {}
    for t in ts:
        if "." in t or "^" in t:
            continue
        stems.setdefault(t, set()).add(t)
        for suf in CLASS_SUFFIXES:
            if t.endswith(suf) and len(t) - len(suf) >= 2:
                stems.setdefault(t[: -len(suf)], set()).add(t)
    for stem, group in stems.items():
        if len(group) < 2 or len(stem) < 2:
            continue
        for a, b in itertools.combinations(sorted(group), 2):
            out.add((a, b))
    return out


def main() -> None:
    eng = create_engine(f"sqlite:///file:{DB.as_posix()}?mode=ro&uri=true")
    with eng.connect() as c:
        px_tickers = {r[0] for r in c.execute(text("SELECT DISTINCT ticker FROM price_data"))}
        um_tickers = {r[0] for r in c.execute(text("SELECT DISTINCT ticker FROM universe_membership"))}
        prices = pd.read_sql(text(
            "SELECT ticker, date, close FROM price_data WHERE ticker != '^GSPC'"), c)
    universe = (px_tickers | um_tickers) - {"^GSPC"}
    print(f"tickers considered: {len(universe)} "
          f"(price_data {len(px_tickers)}, universe_membership {len(um_tickers)})")

    prices["date"] = pd.to_datetime(prices["date"])
    wide = prices.pivot_table(index="date", columns="ticker", values="close")
    rets = np.log(wide / wide.shift(1))

    cands = candidate_pairs(universe)
    print(f"structural candidate pairs: {len(cands)}")

    rows = []
    for a, b in sorted(cands):
        n_overlap, corr = 0, None
        if a in rets.columns and b in rets.columns:
            j = rets[[a, b]].dropna()
            n_overlap = len(j)
            if n_overlap >= MIN_OVERLAP:
                corr = float(j[a].corr(j[b]))
        rows.append({
            "a": a, "b": b,
            "a_has_prices": a in px_tickers, "b_has_prices": b in px_tickers,
            "a_in_universe": a in um_tickers, "b_in_universe": b in um_tickers,
            "overlap_days": n_overlap,
            "return_corr": None if corr is None else round(corr, 4),
            "verdict": ("CONFIRMED" if corr is not None and corr >= CORR_CONFIRM
                        else "REJECTED" if corr is not None
                        else "UNTESTABLE (insufficient overlapping price data)"),
        })
    df = pd.DataFrame(rows).sort_values(
        ["verdict", "return_corr"], ascending=[True, False], na_position="last")
    df.to_csv(HERE / "candidate_pairs.csv", index=False)

    for v in ("CONFIRMED", "UNTESTABLE (insufficient overlapping price data)", "REJECTED"):
        sub = df[df["verdict"] == v]
        print(f"\n=== {v}: {len(sub)}")
        show = sub if v != "REJECTED" else sub.head(15)
        for _, r in show.iterrows():
            print(f"   {r['a']:<7} {r['b']:<7} corr={str(r['return_corr']):>8} "
                  f"overlap={r['overlap_days']:>5}  px={int(r['a_has_prices'])}{int(r['b_has_prices'])}"
                  f" univ={int(r['a_in_universe'])}{int(r['b_in_universe'])}")
        if v == "REJECTED" and len(sub) > 15:
            print(f"   ... {len(sub)-15} more rejected (see candidate_pairs.csv)")

    print("\n=== CONTROLS")
    idx = {(r.a, r.b): r for r in df.itertuples()}
    for a, b in POSITIVE_CONTROLS:
        k = (a, b) if (a, b) in idx else (b, a)
        r = idx.get(k)
        ok = r is not None and r.verdict == "CONFIRMED"
        print(f"   POSITIVE {a}/{b}: {'PASS' if ok else 'FAIL'} "
              f"({'not generated' if r is None else f'{r.verdict} corr={r.return_corr}'})")
    for a, b in NEGATIVE_CONTROLS:
        k = (a, b) if (a, b) in idx else (b, a)
        r = idx.get(k)
        bad = r is not None and r.verdict == "CONFIRMED"
        print(f"   NEGATIVE {a}/{b}: {'FAIL - wrongly confirmed' if bad else 'PASS'}"
              f"{'' if r is None else f' ({r.verdict} corr={r.return_corr})'}")


if __name__ == "__main__":
    main()
