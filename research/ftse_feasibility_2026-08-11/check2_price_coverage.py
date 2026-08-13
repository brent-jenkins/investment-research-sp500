"""Check 2 — yfinance price coverage for .L-suffixed FTSE tickers.

READ-ONLY. Fetches into memory, prints a table, writes ONE csv into this
research directory. Does NOT touch price_data, ticker_data_quality, or any
production table.

Delisted names are the population that matters: that is where the
recycled-symbol and unadjusted-corporate-action problems concentrated for US
tickers (see CLAUDE.md's universe-coverage remediation and recycled-ticker
quarantine sections).
"""
import time

import pandas as pd
import yfinance as yf

CURRENT = [
    ("SHEL.L", "Shell"), ("AZN.L", "AstraZeneca"), ("HSBA.L", "HSBC"),
    ("BP.L", "BP"), ("GSK.L", "GSK"), ("RIO.L", "Rio Tinto"),
    ("ULVR.L", "Unilever"), ("BATS.L", "British American Tobacco"),
    ("VOD.L", "Vodafone"), ("LLOY.L", "Lloyds"), ("BARC.L", "Barclays"),
    ("TSCO.L", "Tesco"), ("GLEN.L", "Glencore"), ("REL.L", "RELX"),
]
# Removed from the FTSE 100 at various points -- the survivorship-critical set.
DELISTED = [
    ("CBRY.L", "Cadbury (Kraft 2010)"),
    ("BOOT.L", "Alliance Boots (2007)"),
    ("SCTN.L", "Scottish & Newcastle (2008)"),
    ("NRK.L", "Northern Rock (nationalised 2008)"),
    ("BB.L", "Bradford & Bingley (2008)"),
    ("HBOS.L", "HBOS (Lloyds 2009)"),
    ("BGY.L", "British Energy (EDF 2009)"),
    ("CW.L", "Cable & Wireless"),
    ("AMFW.L", "Amec Foster Wheeler (2017)"),
    ("MRW.L", "Wm Morrison (private 2021)"),
    ("RSA.L", "RSA Insurance (2021)"),
    ("AGK.L", "Aggreko (private 2021)"),
    ("GFS.L", "G4S (2021)"),
    ("MGGT.L", "Meggitt (2022)"),
    ("MCRO.L", "Micro Focus (2023)"),
    ("PSN.L", "Persimmon (current, control)"),
]

rows = []
for group, items in (("current", CURRENT), ("delisted", DELISTED)):
    for tkr, label in items:
        rec = {"group": group, "ticker": tkr, "label": label}
        try:
            h = yf.Ticker(tkr).history(start="2000-01-01", auto_adjust=False)
            if h is None or h.empty:
                rec.update(rows_n=0, first=None, last=None, note="NO DATA")
            else:
                h.index = h.index.tz_localize(None)
                cl = h["Close"].astype(float)
                rec["rows_n"] = len(h)
                rec["first"] = str(h.index.min().date())
                rec["last"] = str(h.index.max().date())
                rec["currency"] = yf.Ticker(tkr).fast_info.get("currency", "?")
                # Cliff detection, same [0.5, 2.0] band production uses.
                r = (cl / cl.shift(1)).dropna()
                cliffs = r[(r < 0.5) | (r > 2.0)]
                rec["cliffs"] = len(cliffs)
                rec["worst_cliff"] = round(float(cliffs.min()), 3) if len(cliffs) else None
                # Largest calendar gap inside the series.
                gaps = h.index.to_series().diff().dt.days.dropna()
                rec["max_gap_days"] = int(gaps.max()) if len(gaps) else None
                rec["note"] = ""
        except Exception as exc:
            rec.update(rows_n=-1, note=f"ERROR {type(exc).__name__}")
        rows.append(rec)
        print(f"  {tkr:10s} {rec.get('rows_n'):>6} rows  "
              f"{str(rec.get('first')):>10} -> {str(rec.get('last')):>10}  "
              f"cur={rec.get('currency','?'):>4}  cliffs={rec.get('cliffs')}  "
              f"maxgap={rec.get('max_gap_days')}  {rec.get('note','')}", flush=True)
        time.sleep(1.0)

df = pd.DataFrame(rows)
df.to_csv("research/ftse_feasibility_2026-08-11/check2_coverage.csv", index=False)
print("\n=== SUMMARY ===")
for g in ("current", "delisted"):
    s = df[df["group"] == g]
    ok = s[s["rows_n"] > 0]
    print(f"{g:9s}: {len(ok)}/{len(s)} returned data")
    if len(ok):
        print(f"           median rows {int(ok['rows_n'].median())}, "
              f"earliest start {ok['first'].min()}, "
              f"tickers with cliffs: {int((ok['cliffs'] > 0).sum())}")
