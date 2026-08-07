"""Cross-reference the 130 still-unresolved NEEDS_REVIEW tickers against
Wikipedia's "Selected changes" table -- an independent, dated, company-
named source this project's own membership data (fja05680/sp500,
ticker-only) does not have. For each ticker, find the Wikipedia "removed"
row whose date is closest to this project's own last_membership_end, and
report both the Wikipedia-sourced historical name AND the current cached
long_name side by side.

PROPOSAL ONLY -- no writes to ticker_data_quality. Matches this project's
own established discipline (Phase 1 of the contiguity adjudication):
report both strings, a proposed verdict where the match is unambiguous,
and an explicit "still needs a human" bucket for everything else. Nothing
here is applied without separate review and approval.
"""
import json
import sqlite3

import pandas as pd

DB_PATH = "database/investment_research.db"
OUT_DIR = "research/needs_review_adjudication_2026-08-07"
DATE_TOLERANCE_DAYS = 200  # fja05680/sp500 and Wikipedia can disagree on the exact effective date by a small margin


def load_needs_review():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT ticker, reason, evidence FROM ticker_data_quality WHERE status = 'NEEDS_REVIEW'"
    ).fetchall()
    conn.close()
    out = []
    for ticker, reason, evidence_json in rows:
        ev = json.loads(evidence_json)
        out.append({
            "ticker": ticker,
            "last_membership_end": ev.get("last_membership_end"),
            "long_name": ev.get("long_name"),
            "quote_type": ev.get("quote_type"), "exchange": ev.get("exchange"),
            "date_overrun_days": ev.get("date_overrun_days"),
            "gap_missing_trading_days": (ev.get("contiguity_check") or {}).get("gap_missing_trading_days"),
        })
    return pd.DataFrame(out)


def names_plausibly_match(wiki_name: str, cached_name: str) -> "bool | None":
    """Conservative, transparent string check -- NOT fuzzy matching that
    auto-decides. Returns True only if one name is clearly contained in
    the other after stripping common corporate suffixes; False only if
    both names are present and share no common word; None (genuinely
    unclear) otherwise -- callers must not treat None as either verdict."""
    if not isinstance(wiki_name, str) or not isinstance(cached_name, str) or not wiki_name or not cached_name:
        return None
    suffixes = [" inc.", " inc", " corp.", " corp", " corporation", " company", " co.", " co",
                " ltd.", " ltd", " plc", " llc", " l.p.", " lp", " group", " holdings", ", inc.", ", inc"]

    def norm(s):
        s = s.lower().strip()
        for suf in suffixes:
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
        return s

    w, c = norm(wiki_name), norm(cached_name)
    if not w or not c:
        return None
    if w == c or w in c or c in w:
        return True
    w_words = set(w.replace(",", "").replace(".", "").split())
    c_words = set(c.replace(",", "").replace(".", "").split())
    # ignore very common/uninformative words when checking for ANY overlap
    stop = {"the", "&", "and", "group", "holdings", "corp", "corporation", "company", "inc"}
    w_words -= stop
    c_words -= stop
    if w_words & c_words:
        return None  # partial overlap -- genuinely ambiguous, not auto-decidable
    return False


def main():
    nr = load_needs_review()
    print(f"NEEDS_REVIEW population: {len(nr)} tickers", flush=True)

    wiki = pd.read_csv(f"{OUT_DIR}/wikipedia_sp500_changes_raw.csv")
    wiki["date_parsed"] = pd.to_datetime(wiki["date_parsed"])
    wiki_removed = wiki[wiki["removed_ticker"] != ""].copy()

    results = []
    for _, row in nr.iterrows():
        ticker = row["ticker"]
        if not row["last_membership_end"]:
            results.append({**row.to_dict(), "wiki_match_found": False, "verdict": "NO_MEMBERSHIP_DATE"})
            continue
        end_date = pd.Timestamp(row["last_membership_end"])
        candidates = wiki_removed[wiki_removed["removed_ticker"] == ticker].copy()
        if candidates.empty:
            results.append({**row.to_dict(), "wiki_match_found": False, "verdict": "NOT_IN_WIKIPEDIA_TABLE"})
            continue
        candidates["date_diff_days"] = (candidates["date_parsed"] - end_date).abs().dt.days
        best = candidates.sort_values("date_diff_days").iloc[0]
        within_tolerance = best["date_diff_days"] <= DATE_TOLERANCE_DAYS

        match = names_plausibly_match(best["removed_name"], row["long_name"]) if within_tolerance else None
        if not within_tolerance:
            verdict = "WIKI_DATE_TOO_FAR"
        elif match is True:
            verdict = "PROPOSED_CLEAN"
        elif match is False:
            verdict = "PROPOSED_QUARANTINED"
        else:
            verdict = "STILL_AMBIGUOUS"

        results.append({
            **row.to_dict(), "wiki_match_found": True,
            "wiki_removed_date": best["date_parsed"].date().isoformat(),
            "wiki_date_diff_days": int(best["date_diff_days"]),
            "wiki_removed_name": best["removed_name"], "wiki_reason": best["reason"],
            "verdict": verdict,
        })

    df = pd.DataFrame(results)
    print("\n=== Verdict counts ===", flush=True)
    print(df["verdict"].value_counts(), flush=True)

    df.to_csv(f"{OUT_DIR}/step2_cross_reference_results.csv", index=False)
    print(f"\nWrote step2_cross_reference_results.csv ({len(df)} rows)", flush=True)


if __name__ == "__main__":
    main()
