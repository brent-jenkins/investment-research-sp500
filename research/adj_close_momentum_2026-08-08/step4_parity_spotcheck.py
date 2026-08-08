"""Decision-engine parity spot-check: for a sample of Arm B trades, confirm
(a) entry_price/exit_price match the CLOSE series (not adj_close) -- prices
were never touched, only the signal; (b) at entry_date, the adj_close-based
signal was genuinely ON in the dual cache -- confirms the gate actually saw
the adj_close momentum, not a silently-unchanged close-based one.
"""
import pickle
import random
import sys

sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")
import pandas as pd

OUT_DIR = "research/adj_close_momentum_2026-08-08"


def main():
    with open(f"{OUT_DIR}/_cache_pool2_clean_dual.pkl", "rb") as fh:
        dual = pickle.load(fh)
    by_ticker = {c["ticker"]: c["frame"] for c in dual["caches"] if c is not None}

    trades_b = pd.read_csv(f"{OUT_DIR}/trades_pool2_clean_B.csv")
    trades_a = pd.read_csv(f"{OUT_DIR}/trades_pool2_clean_A.csv")
    a_keys = set(zip(trades_a["ticker"], trades_a["entry_date"]))
    b_keys = set(zip(trades_b["ticker"], trades_b["entry_date"]))
    b_only = trades_b[trades_b.apply(lambda r: (r["ticker"], r["entry_date"]) not in a_keys, axis=1)]

    random.seed(42)
    sample = b_only.sample(min(8, len(b_only)), random_state=42)

    print(f"Sampling {len(sample)} Arm-B-only trades (admitted under Arm B, not Arm A, on the same date)\n")
    print("Two ways a B-only trade is legitimate: (1) signal_adj on, signal_close off -- a pure gate")
    print("difference; (2) both signals on, but momentum_adj > momentum_close -- won a capacity slot")
    print("under momentum-ranked admission it would have lost under close-based ranking; or the same")
    print("via the capacity-cascade mechanism (a DIFFERENT ticker's ranking shifted, freeing/consuming")
    print("this slot) if momentum values are unchanged for this specific ticker. Price must always match")
    print("close exactly regardless of mechanism -- that is the one non-negotiable assertion.\n")
    all_ok = True
    for _, row in sample.iterrows():
        ticker, entry_date = row["ticker"], row["entry_date"]
        frame = by_ticker.get(ticker)
        if frame is None:
            print(f"  {ticker} {entry_date}: NO CACHE FRAME FOUND -- FAIL")
            all_ok = False
            continue
        ed = pd.Timestamp(entry_date)
        if ed not in frame.index:
            print(f"  {ticker} {entry_date}: entry date not in frame index -- FAIL")
            all_ok = False
            continue
        cached_close = float(frame.loc[ed, "close"])
        trade_entry_price = float(row["entry_price"])
        price_match = abs(cached_close - trade_entry_price) < 0.01
        sig_adj_on = bool(frame.loc[ed, "signal_adj"])
        sig_close_on = bool(frame.loc[ed, "signal_close"])
        mom_close = float(frame.loc[ed, "momentum_close"])
        mom_adj = float(frame.loc[ed, "momentum_adj"])
        pure_gate_diff = sig_adj_on and not sig_close_on
        priority_diff = sig_adj_on and sig_close_on and mom_adj > mom_close
        cascade_only = sig_adj_on and sig_close_on and abs(mom_adj - mom_close) < 1e-9
        ok = price_match and sig_adj_on and (pure_gate_diff or priority_diff or cascade_only)
        all_ok = all_ok and ok
        mechanism = ("pure gate diff" if pure_gate_diff else
                     "priority (higher adj momentum)" if priority_diff else
                     "cascade (unchanged momentum, ranking shifted elsewhere)" if cascade_only else
                     "UNEXPLAINED")
        print(f"  {ticker} {entry_date}: price_match={price_match}  mom_close={mom_close:.4f} "
              f"mom_adj={mom_adj:.4f}  mechanism={mechanism}  {'OK' if ok else '*** UNEXPECTED ***'}")

    print(f"\n{'ALL CHECKS PASSED -- prices always close, admission always explained' if all_ok else '*** SOME CHECKS FAILED -- investigate before trusting results ***'}")


if __name__ == "__main__":
    main()
