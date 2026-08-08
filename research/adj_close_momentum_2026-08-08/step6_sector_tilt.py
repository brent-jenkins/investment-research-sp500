"""Direction check: does adj_close-based admission skew toward higher-yielding
sectors (utilities, staples/consumer defensive, REITs/real estate) as the
dividend-exclusion theory predicts? Cheap sanity check -- if Arm B wins but
shows no such tilt, the mechanism isn't what the theory says and the result
deserves suspicion. Reuses research/entry_volume_sector_2026-08-07/sector_map.csv
(prior study's cached sector data) rather than re-fetching from yfinance.
Usage: python step6_sector_tilt.py <pool1|pool2_clean|pool2_all>
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import pandas as pd

OUT_DIR = "research/adj_close_momentum_2026-08-08"
SECTOR_MAP_PATH = "research/entry_volume_sector_2026-08-07/sector_map.csv"
DIVIDEND_SECTORS = {"Utilities", "Consumer Defensive", "Real Estate"}


def main():
    pool = sys.argv[1]
    a = pd.read_csv(f"{OUT_DIR}/trades_{pool}_A.csv")
    b = pd.read_csv(f"{OUT_DIR}/trades_{pool}_B.csv")
    sector_map = pd.read_csv(SECTOR_MAP_PATH).set_index("ticker")["sector"].to_dict()

    a_counts = a["ticker"].value_counts()
    b_counts = b["ticker"].value_counts()
    all_tickers = sorted(set(a_counts.index) | set(b_counts.index))

    rows = []
    for t in all_tickers:
        na, nb = int(a_counts.get(t, 0)), int(b_counts.get(t, 0))
        rows.append({"ticker": t, "n_A": na, "n_B": nb, "delta_B_minus_A": nb - na,
                      "sector": sector_map.get(t, "UNKNOWN")})
    df = pd.DataFrame(rows)
    n_unknown = int((df["sector"] == "UNKNOWN").sum())
    print(f"{pool}: {len(df)} distinct tickers traded by either arm, {n_unknown} not in sector map "
          f"({100*n_unknown/len(df):.1f}%)\n")

    df["is_dividend_sector"] = df["sector"].isin(DIVIDEND_SECTORS)
    known = df[df["sector"] != "UNKNOWN"]

    div_mean = known.loc[known["is_dividend_sector"], "delta_B_minus_A"].mean()
    non_div_mean = known.loc[~known["is_dividend_sector"], "delta_B_minus_A"].mean()
    print(f"Mean (n_B - n_A) per ticker, dividend-heavy sectors (Utilities/Consumer Defensive/Real Estate): "
          f"{div_mean:.3f}  (n={known['is_dividend_sector'].sum()})")
    print(f"Mean (n_B - n_A) per ticker, all other sectors: {non_div_mean:.3f}  (n={(~known['is_dividend_sector']).sum()})")
    print(f"Tilt {'CONFIRMS' if div_mean > non_div_mean else 'CONTRADICTS'} the dividend-exclusion theory's predicted direction")

    print("\nPer-sector breakdown (mean delta, n tickers):")
    by_sector = known.groupby("sector")["delta_B_minus_A"].agg(["mean", "count"]).sort_values("mean", ascending=False)
    print(by_sector.to_string())

    df.to_csv(f"{OUT_DIR}/step6_sector_tilt_{pool}.csv", index=False)


if __name__ == "__main__":
    main()
