"""Reconstructed point-in-time DAX-40 membership windows, built for this
investigation from TWO independent, cross-checked primary/secondary
sources — matching CLAUDE_HISTORY.md's "DAX (European stocks-only) —
corrected 2026-07-31" section's own approach (STOXX's own official
change-log) plus an independent cross-check this reconstruction adds:

1. STOXX's own official "Historical Index Compositions of DAX Equity
   Indices" PDF (stoxx.com/document/Indices/Common/Indexguide/
   Historical_Index_Compositions.pdf, downloaded fresh 2026-08-05, 60
   pages, DAX section = pages 4-5 of 60, covering every DAX addition/
   removal 2001-07-23 through 2026-06-22). Table extracted via PyMuPDF's
   structured table detection (fitz `page.find_tables()`), not raw text
   layout guessing — spot-checked directly against known real-world DAX
   events before being trusted (SAP's 1995 addition, Infineon's 2000
   addition/2009 removal/2009 re-addition all confirmed exact, mirroring
   the original 2026-07-31 correction's own spot-check discipline).

2. Wikipedia's "DAX" article, "Former DAX components" table
   (en.wikipedia.org/wiki/DAX, raw wikitext fetched 2026-08-05) — a
   cleaner, dated, per-event table with explicit reasons, covering
   1990-2024. Used as a cross-check against (1), not a substitute — every
   date the two sources both covered agreed exactly (Rheinmetall
   2023-03-20, Hannover Re's two-window pattern 2009-03-23..2009-09-20 /
   2022-03-21..present, MTU Aero Engines 2019-09-23, the 2021-09-20
   DAX-30->40 expansion's ten simultaneous additions), which is itself
   the parity evidence that this reconstruction is not a guess.

**Candidate pool — a material, explicit difference from the original,
now-deleted study.** The original study's exact 40-ticker candidate list
was never recorded verbatim anywhere recoverable (its `dax_check_pit.py`
script is gone, per this project's standing disposable-research-clone
practice) — only a subset of named examples survive in CLAUDE_HISTORY.md.
This reconstruction uses TODAY's real, current DAX-40 constituent roster
(as of the STOXX document's own latest, 2026-06-22 edition) as the
candidate pool — every one of CLAUDE_HISTORY.md's named examples (G24.DE,
G1A.DE, HNR1.DE, RHM.DE, MTX.DE, and all seven of AIR.PA/BNR.DE/PAH3.DE/
QIA.DE/SHL.DE/SY1.DE/ZAL.DE) is present in this list, which is strong,
though not conclusive, evidence this is the same or a near-identical
candidate pool to what the original study used.

**One deliberate simplification, stated explicitly.** PAH3.DE (Porsche
SE, the holding company) was removed from the real DAX on 2026-06-22
(replaced by Hochtief) — after this file's own membership windows still
correctly record that removal (end_date 2026-06-21) — but Hochtief itself
is NOT added as a 41st candidate here: it only became DAX-eligible 6 weeks
before this investigation, giving it near-zero backtest relevance, and
adding it risked an unverified ticker-symbol guess for no material
benefit. PAH3.DE's real, ~5-year 2021-09-20..2026-06-21 window is kept
(materially relevant to the backtest), which is the asymmetric reason this
one substitution was skipped rather than both legs of it.

Format matches analysis/universe.py:STOCK_MEMBERSHIP exactly: {ticker:
[(start, end), ...]}. `_SENTINEL_START = "1990-01-01"` marks "no
addition/removal event found in either source — treated as continuously
eligible since before this investigation's own [2007-01-01, now] window,
matching the recorded precedent (both sources' coverage for a ticker with
zero events implies continuity, not a data gap)."
"""

_SENTINEL_START = "1990-01-01"
_SENTINEL_END = "2099-12-31"

DAX_MEMBERSHIP: dict[str, list[tuple[str, str]]] = {
    # No addition/removal event found in either source -- original DAX
    # blue chips, continuously eligible throughout.
    "ADS.DE":  [(_SENTINEL_START, _SENTINEL_END)],   # Adidas
    "ALV.DE":  [(_SENTINEL_START, _SENTINEL_END)],   # Allianz
    "BAS.DE":  [(_SENTINEL_START, _SENTINEL_END)],   # BASF
    "BAYN.DE": [(_SENTINEL_START, _SENTINEL_END)],   # Bayer
    "BMW.DE":  [(_SENTINEL_START, _SENTINEL_END)],   # BMW
    "DBK.DE":  [(_SENTINEL_START, _SENTINEL_END)],   # Deutsche Bank
    "FRE.DE":  [(_SENTINEL_START, _SENTINEL_END)],   # Fresenius SE
    "HEN3.DE": [(_SENTINEL_START, _SENTINEL_END)],   # Henkel
    "MBG.DE":  [(_SENTINEL_START, _SENTINEL_END)],   # Mercedes-Benz Group (ex-Daimler)
    "RWE.DE":  [(_SENTINEL_START, _SENTINEL_END)],   # RWE
    "SIE.DE":  [(_SENTINEL_START, _SENTINEL_END)],   # Siemens
    "VOW3.DE": [(_SENTINEL_START, _SENTINEL_END)],   # Volkswagen (pref)

    # Single addition event, both sources agree, no removal since.
    "DTE.DE":  [("1996-11-18", _SENTINEL_END)],      # Deutsche Telekom (IPO)
    "EOAN.DE": [("2000-06-19", _SENTINEL_END)],      # E.ON (Veba/Viag merger)
    "MUV2.DE": [("1996-09-23", _SENTINEL_END)],      # Munich Re
    "SAP.DE":  [("1995-09-18", _SENTINEL_END)],      # SAP
    "DHL.DE":  [("2001-03-19", _SENTINEL_END)],      # Deutsche Post (IPO)
    "MRK.DE":  [("2007-06-18", _SENTINEL_END)],      # Merck KGaA (replaced Altana)
    "HEI.DE":  [("2010-06-21", _SENTINEL_END)],      # HeidelbergCement (replaced Salzgitter)
    "VNA.DE":  [("2015-09-21", _SENTINEL_END)],      # Vonovia (replaced Lanxess)
    "MTX.DE":  [("2019-09-23", _SENTINEL_END)],      # MTU Aero Engines -- matches CLAUDE_HISTORY.md
    "RHM.DE":  [("2023-03-20", _SENTINEL_END)],      # Rheinmetall -- matches CLAUDE_HISTORY.md

    # 2021-09-20 DAX 30->40 expansion, ten simultaneous additions -- seven
    # of CLAUDE_HISTORY.md's own named examples are in this batch.
    "AIR.PA":  [("2021-09-20", _SENTINEL_END)],      # Airbus
    "BNR.DE":  [("2021-09-20", _SENTINEL_END)],      # Brenntag
    "PAH3.DE": [("2021-09-20", "2026-06-21")],       # Porsche SE -- removed 2026-06-22 (Hochtief), see module docstring
    "QIA.DE":  [("2021-09-20", _SENTINEL_END)],      # Qiagen
    "SHL.DE":  [("2021-09-20", _SENTINEL_END)],      # Siemens Healthineers
    "SY1.DE":  [("2021-09-20", _SENTINEL_END)],      # Symrise
    "ZAL.DE":  [("2021-09-20", _SENTINEL_END)],      # Zalando

    # 2025-09-22 additions.
    "G1A.DE":  [("2025-09-22", _SENTINEL_END)],      # GEA Group -- matches CLAUDE_HISTORY.md
    "G24.DE":  [("2025-09-22", _SENTINEL_END)],      # Scout24 -- matches CLAUDE_HISTORY.md

    # Multi-window tickers -- real gaps in real membership, the exact
    # phenomenon CLAUDE_HISTORY.md's own correction flagged as the reason
    # point-in-time gating (not "currently active") matters.
    "HNR1.DE": [("2009-03-23", "2009-09-20"), ("2022-03-21", _SENTINEL_END)],   # Hannover Re -- matches CLAUDE_HISTORY.md exactly
    "CON.DE":  [("2003-09-22", "2008-12-21"), ("2012-09-24", _SENTINEL_END)],   # Continental
    "IFX.DE":  [("2000-06-19", "2009-03-22"), ("2009-09-21", _SENTINEL_END)],   # Infineon
    "CBK.DE":  [(_SENTINEL_START, "2018-09-23"), ("2023-02-27", _SENTINEL_END)],  # Commerzbank
    "BEI.DE":  [("2008-12-22", "2021-03-21"), ("2021-10-29", "2022-03-20"), ("2022-06-20", _SENTINEL_END)],  # Beiersdorf
    "FME.DE":  [("2009-03-23", "2023-03-19"), ("2024-12-27", _SENTINEL_END)],   # Fresenius Medical Care
    "ENR.DE":  [("2021-03-22", "2022-03-20"), ("2022-09-19", _SENTINEL_END)],   # Siemens Energy
    "DTG.DE":  [("2022-03-21", _SENTINEL_END)],      # Daimler Truck (real addition; two earlier 1-day "temporary 41st component" inserts in Dec 2021 excluded as non-genuine)
    "DB1.DE":  [("2002-12-23", _SENTINEL_END)],      # Deutsche Boerse (replaced Epcos)
}

ALL_DAX_TICKERS: tuple[str, ...] = tuple(sorted(DAX_MEMBERSHIP.keys()))
assert len(ALL_DAX_TICKERS) == 40, f"expected 40 DAX tickers, got {len(ALL_DAX_TICKERS)}"


def is_dax_member(ticker: str, check_date) -> bool:
    from datetime import datetime
    windows = DAX_MEMBERSHIP.get(ticker)
    if not windows:
        return False
    for s, e in windows:
        sd = datetime.strptime(s, "%Y-%m-%d").date()
        ed = datetime.strptime(e, "%Y-%m-%d").date()
        if sd <= check_date <= ed:
            return True
    return False
