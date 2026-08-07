"""Fetch and parse Wikipedia's "Selected changes to the list of S&P 500
components" table -- an independent, real, dated, company-named source of
historical S&P 500 membership changes. Investigated as a candidate to help
adjudicate the 130 still-unresolved NEEDS_REVIEW tickers per user request
(2026-08-07), after contiguity was confirmed exhausted (found no detectable
trace for ~47% of even the KNOWN-corrupt controls -- see
research/data_quality_remediation_2026-08-07/PHASE1_CONTIGUITY_REPORT.md).

Raw wikitext, not the rendered page (WebFetch's own AI-summarization step
on a 350+ row table is not reliable enough to adjudicate individual
tickers against -- this parses the actual table rows directly).
"""
import re
import urllib.request

import pandas as pd

URL = "https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=raw&section=2"
OUT_DIR = "research/needs_review_adjudication_2026-08-07"


def fetch_wikitext() -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": "research-script/1.0 (adjudication research)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def strip_wikilinks(s: str) -> str:
    """[[Foo Bar|Baz]] -> Baz ; [[Foo Bar]] -> Foo Bar"""
    s = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", s)
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.DOTALL)
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"'{2,}", "", s)  # bold/italic markup
    return s.strip()


def parse_table(wikitext: str) -> pd.DataFrame:
    # Isolate the table body (between the header row and the closing |})
    start = wikitext.index('id="changes"')
    body = wikitext[start:]
    end = body.index("\n|}")
    body = body[:end]

    # Rows are separated by "|-"; each row has 5 pipe-delimited cells
    # (Effective Date, Added Ticker, Added Security, Removed Ticker,
    # Removed Security, Reason) -- 6 cells actually, matching the 6 header
    # sub-columns (rowspan Effective Date + 2 Added + 2 Removed + rowspan Reason).
    rows = body.split("\n|-")[1:]
    records = []
    for row in rows:
        row = row.strip()
        if not row or row.startswith("!"):
            continue
        # cells separated by "||" possibly across multiple lines starting with "|"
        line = row.replace("\n|", "||").lstrip("|")
        cells = [c.strip() for c in re.split(r"\|\|", line)]
        if len(cells) < 5:
            continue
        cells = (cells + [""] * 6)[:6]
        date_str, add_ticker, add_name, rem_ticker, rem_name, reason = cells
        records.append({
            "date": strip_wikilinks(date_str),
            "added_ticker": strip_wikilinks(add_ticker),
            "added_name": strip_wikilinks(add_name),
            "removed_ticker": strip_wikilinks(rem_ticker),
            "removed_name": strip_wikilinks(rem_name),
            "reason": strip_wikilinks(reason),
        })
    return pd.DataFrame(records)


def main():
    wikitext = fetch_wikitext()
    print(f"Fetched {len(wikitext)} chars", flush=True)
    df = parse_table(wikitext)
    print(f"Parsed {len(df)} rows", flush=True)

    df["date_parsed"] = pd.to_datetime(df["date"], errors="coerce")
    print(f"Rows with a parseable date: {df['date_parsed'].notna().sum()}/{len(df)}", flush=True)
    print(f"Date range: {df['date_parsed'].min()} -> {df['date_parsed'].max()}", flush=True)
    print(df.head(10).to_string(), flush=True)
    print("...", flush=True)
    print(df.tail(5).to_string(), flush=True)

    df.to_csv(f"{OUT_DIR}/wikipedia_sp500_changes_raw.csv", index=False)
    print(f"\nWrote {OUT_DIR}/wikipedia_sp500_changes_raw.csv", flush=True)


if __name__ == "__main__":
    main()
