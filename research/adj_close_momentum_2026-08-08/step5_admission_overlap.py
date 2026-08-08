"""Admission overlap: how many (ticker, entry_date) positions do Arm A and
Arm B actually share, vs trade uniquely to one arm? Reported prominently
per explicit instruction -- if overlap is high, any CAGR difference is being
driven by a handful of names and the honest conclusion is "immaterial at
these holding periods" regardless of what the four decisive criteria say.
Usage: python step5_admission_overlap.py <pool1|pool2_clean|pool2_all>
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import json

import pandas as pd

OUT_DIR = "research/adj_close_momentum_2026-08-08"


def main():
    pool = sys.argv[1]
    a = pd.read_csv(f"{OUT_DIR}/trades_{pool}_A.csv")
    b = pd.read_csv(f"{OUT_DIR}/trades_{pool}_B.csv")

    key = lambda df: set(zip(df["ticker"], df["entry_date"]))
    ka, kb = key(a), key(b)
    shared = ka & kb
    a_only = ka - kb
    b_only = kb - ka

    n_a, n_b, n_shared = len(ka), len(kb), len(shared)
    overlap_pct_of_a = 100 * n_shared / n_a if n_a else 0.0
    overlap_pct_of_b = 100 * n_shared / n_b if n_b else 0.0
    jaccard = n_shared / len(ka | kb) if (ka | kb) else 0.0

    result = {
        "pool": pool,
        "n_trades_A": n_a, "n_trades_B": n_b,
        "n_shared_positions": n_shared,
        "n_A_only": len(a_only), "n_B_only": len(b_only),
        "overlap_pct_of_A": round(overlap_pct_of_a, 1),
        "overlap_pct_of_B": round(overlap_pct_of_b, 1),
        "jaccard_similarity_pct": round(jaccard * 100, 1),
    }
    print(f"=== {pool} admission overlap ===")
    print(f"  Arm A: {n_a} trades   Arm B: {n_b} trades   Shared (identical ticker+entry_date): {n_shared}")
    print(f"  Overlap: {overlap_pct_of_a:.1f}% of A's positions also in B, {overlap_pct_of_b:.1f}% of B's also in A")
    print(f"  Jaccard similarity: {jaccard*100:.1f}%")
    print(f"  A-only: {len(a_only)}   B-only: {len(b_only)}")

    with open(f"{OUT_DIR}/step5_admission_overlap_{pool}.json", "w") as fh:
        json.dump(result, fh, indent=2)


if __name__ == "__main__":
    main()
