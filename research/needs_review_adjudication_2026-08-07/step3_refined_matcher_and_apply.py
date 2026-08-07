"""Refined Wikipedia cross-reference + application, per explicit user
approval of the exact rule (2026-08-07). Two changes from step2's raw
matcher, both systematic fixes, not per-ticker judgement calls:

1. Better name normalization: "&" <-> "and", trailing "(Class A/B/C)"
   share-class suffixes, period-separated abbreviations ("V.F." -> "VF").
2. An explicit, curated known-renames list -- ONLY the three confirmed,
   unambiguous pure renames found by individually checking step2's 9
   mismatches (ATI/Allegheny Technologies, CLF/Cliffs Natural Resources,
   OI/Owens-Illinois). CNX (Consol Energy) is deliberately NOT included --
   it involved a real spinoff alongside the rename, not a pure identity
   continuation, so it stays a plain mismatch.

Asymmetric application rule (explicit, not the same confidence in both
directions):
  - Wikipedia match (incl. via the renames list / better normalization)
    -> promote to CLEAN. A match is real two-source corroboration.
  - Wikipedia mismatch is NOT evidence for QUARANTINED on its own --
    step2 already proved a third of raw mismatches were matcher
    artifacts. Promoted to QUARANTINED ONLY when corroborated by a
    SECOND, independent signal: prior_reversal_classification ==
    'LIKELY_RECYCLED' (the original investigation's reversal-shape
    classifier, a completely different methodology). CPWR is the only
    ticker meeting this bar among the current 9 mismatches -- the
    explicit model for what "corroborated" means here.
  - No Wikipedia coverage, or genuinely ambiguous name overlap -> stays
    NEEDS_REVIEW. Absence of coverage is not evidence either way.

Every promoted/quarantined ticker's evidence JSON records the Wikipedia
source explicitly, including the caveat: community-maintained, not a
system of record, corroborating evidence only -- never sole authority
for a CLEAN promotion. (It isn't the sole authority here either: every
promotion is a two-source match, cached long_name AND Wikipedia.)
"""
import json
import re
import sqlite3
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pandas as pd

from data.database import write_data_quality_status

DB_PATH = "database/investment_research.db"
OUT_DIR = "research/needs_review_adjudication_2026-08-07"

KNOWN_RENAMES = {
    # ticker -> old (Wikipedia) name, confirmed via step2's individual review
    "ATI": "Allegheny Technologies",
    "CLF": "Cliffs Natural Resources",
    "OI": "Owens-Illinois",
}


def normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s*\(class [a-z]\)\s*$", "", s)  # trailing "(Class A)" etc.
    s = s.replace("&", "and")
    s = re.sub(r"(?<=[a-z])\.(?=[a-z])", "", s)  # "v.f." -> "vf" (periods between single letters)
    s = s.replace(".", "")
    suffixes = [" inc", " corp", " corporation", " company", " co", " ltd", " plc", " llc",
                " lp", " group", " holdings"]
    for suf in suffixes:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    s = re.sub(r"[,]", "", s)
    return s.strip()


def names_match(wiki_name, cached_name, ticker) -> "bool | None":
    if ticker in KNOWN_RENAMES and isinstance(wiki_name, str) and KNOWN_RENAMES[ticker].lower() in wiki_name.lower():
        return True
    if not isinstance(wiki_name, str) or not isinstance(cached_name, str) or not wiki_name or not cached_name:
        return None
    w, c = normalize(wiki_name), normalize(cached_name)
    if not w or not c:
        return None
    if w == c or w in c or c in w:
        return True
    w_words = set(w.split())
    c_words = set(c.split())
    stop = {"the", "and", "group", "holdings", "corp", "corporation", "company", "inc"}
    if (w_words - stop) & (c_words - stop):
        return None
    return False


def main():
    prior = pd.read_csv("research/data_quality_remediation_2026-08-07/identity_verification_results.csv")
    prior_class = dict(zip(prior["ticker"], prior["prior_reversal_classification"]))

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT ticker, reason, evidence FROM ticker_data_quality WHERE status = 'NEEDS_REVIEW'"
    ).fetchall()
    nr_evidence = {t: (r, json.loads(e)) for t, r, e in rows}
    print(f"NEEDS_REVIEW population: {len(nr_evidence)} tickers", flush=True)

    step2 = pd.read_csv(f"{OUT_DIR}/step2_cross_reference_results.csv")

    promoted_clean, promoted_quarantine, stayed = [], [], []
    for _, row in step2.iterrows():
        ticker = row["ticker"]
        old_reason, old_evidence = nr_evidence[ticker]

        wiki_covered = bool(row.get("wiki_match_found"))
        wiki_name = row.get("wiki_removed_name")
        wiki_date = row.get("wiki_removed_date")
        wiki_reason = row.get("wiki_reason")
        cached_name = row.get("long_name")

        match = names_match(wiki_name, cached_name, ticker) if wiki_covered else None
        wiki_evidence_block = {
            "source": "Wikipedia: List of S&P 500 companies, Selected changes table",
            "source_caveat": "community-maintained, not a system of record -- corroborating evidence only, never sole authority for a CLEAN promotion",
            "covered": wiki_covered,
            "wiki_removed_name": wiki_name if wiki_covered else None,
            "wiki_removed_date": wiki_date if wiki_covered else None,
            "wiki_reason": wiki_reason if wiki_covered else None,
            "name_match": match,
        }
        merged_evidence = dict(old_evidence)
        merged_evidence["wikipedia_cross_reference"] = wiki_evidence_block

        if match is True:
            new_reason = (
                f"PROMOTED_CLEAN (Wikipedia cross-reference): stored ticker corresponds to a real, dated "
                f"S&P 500 constituent removal ({wiki_name!r}, {wiki_date}) matching the currently-cached "
                f"identity ({cached_name!r}). Community-maintained source, corroborating not sole evidence "
                f"-- see wikipedia_cross_reference in evidence JSON. Original reason retained: {old_reason}"
            )
            write_status = "CLEAN"
            promoted_clean.append(ticker)
        elif match is False and prior_class.get(ticker) == "LIKELY_RECYCLED":
            new_reason = (
                f"PROMOTED_QUARANTINED (corroborated: Wikipedia mismatch + prior reversal classifier): "
                f"Wikipedia's real removal record ({wiki_name!r}, {wiki_date}) does not match the currently-"
                f"cached identity ({cached_name!r}), AND the original investigation's reversal-shape "
                f"classifier independently flagged this ticker LIKELY_RECYCLED. Two independent signals "
                f"agreeing is the bar for promotion here -- a Wikipedia mismatch alone is not sufficient "
                f"(step2 found 3/9 raw mismatches were rename false positives). Original reason retained: {old_reason}"
            )
            write_status = "QUARANTINED"
            promoted_quarantine.append(ticker)
        else:
            new_reason = old_reason  # unchanged -- stays NEEDS_REVIEW
            write_status = "NEEDS_REVIEW"
            stayed.append((ticker, wiki_covered, match))

        write_data_quality_status(ticker, write_status, new_reason, json.dumps(merged_evidence))

    print(f"\nPromoted to CLEAN: {len(promoted_clean)}", flush=True)
    print(f"Promoted to QUARANTINED (corroborated only): {len(promoted_quarantine)} -> {promoted_quarantine}", flush=True)
    print(f"Stayed NEEDS_REVIEW: {len(stayed)}", flush=True)

    conn2 = sqlite3.connect(DB_PATH)
    final = conn2.execute("SELECT status, COUNT(*) FROM ticker_data_quality GROUP BY status").fetchall()
    print(f"\nFinal ticker_data_quality counts: {dict(final)}", flush=True)

    pd.DataFrame({"ticker": promoted_clean}).to_csv(f"{OUT_DIR}/step3_promoted_clean.csv", index=False)
    pd.DataFrame({"ticker": promoted_quarantine}).to_csv(f"{OUT_DIR}/step3_promoted_quarantined.csv", index=False)
    pd.DataFrame(stayed, columns=["ticker", "wiki_covered", "match"]).to_csv(f"{OUT_DIR}/step3_stayed_needs_review.csv", index=False)
    print("\nWrote step3_promoted_clean.csv, step3_promoted_quarantined.csv, step3_stayed_needs_review.csv", flush=True)


if __name__ == "__main__":
    main()
