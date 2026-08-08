"""Counterfactual single-position simulator for Parts 5 & 6.

Given a (ticker, entry_date) candidate-day that did NOT result in a real
admitted trade (either REJECTED_CAPACITY or a gate failure), simulate what a
standalone LONG position entered at that day's close would have returned,
walking forward using the EXACT same real exit logic every other engine in
this project uses (evaluate_exit_reason(), the realistic intraday-touch
STOP_LOSS fill, get_max_holding_days()) -- imported directly, not
re-derived. Capacity and re-entry-cooldown/other-candidates are deliberately
NOT modelled here -- this is precisely the counterfactual being asked
("what WOULD this trade have returned if admitted"), so removing the very
constraint that caused the rejection is correct, not an oversight.

APPROXIMATION AND ITS LIMITS, stated plainly:
  - No portfolio interaction: sizing/compounding effects (capacity used by
    this position competing with what capacity constraints would separately
    have been binding on other, real trades) are not modelled -- this
    function answers "how would THIS position's OWN price path have
    resolved," not "what would the portfolio's balance have been." pnl_pct
    is a percentage return, not a £ figure, for exactly this reason.
  - Re-entry cooldown is not applied to the ticker's own subsequent real
    admission history, since there is no real admission history to protect
    against in a standalone counterfactual.
  - Uses the same cached per-ticker frame every real run uses (same close/
    low/open/atr/signal/momentum columns), so the exit mechanics themselves
    (stop resolution, realistic fill, crisis override) are identical to
    production, not approximated.
"""
import pandas as pd

from analysis.decision import evaluate_exit_reason
from analysis.volatility import _resolve_entry_stop


def simulate_single_position(cached_ticker, entry_date, config, max_hold, raw_crisis):
    """cached_ticker: one entry from tickers_cache (dict with 'frame').
    entry_date: pd.Timestamp, must be in cached_ticker['frame'].index.
    raw_crisis: pd.Series, crisis.compute_raw_trigger(gspc_close) already
    reindexed to this ticker's own frame.index (exactly how the real engine
    builds it per ticker) -- passed in rather than recomputed per call, since
    it depends only on gspc_close and is identical for every ticker/date.
    Returns a dict with exit_reason/exit_date/holding_days/pnl_pct, or None
    if entry_date isn't in the frame or there's no subsequent data at all.
    """
    frame = cached_ticker["frame"]
    if entry_date not in frame.index:
        return None
    idx = frame.index.get_loc(entry_date)
    entry_row = frame.iloc[idx]
    entry_price = float(entry_row["close"])
    atr = float(entry_row["atr"]) if pd.notna(entry_row["atr"]) else 0.0
    stop_price, stop_distance_pct = _resolve_entry_stop("LONG", entry_price, atr, config)

    if idx + 1 >= len(frame):
        return {"exit_reason": "NO_FORWARD_DATA", "exit_date": entry_date, "holding_days": 0, "pnl_pct": 0.0}

    for fwd_idx in range(idx + 1, len(frame)):
        row = frame.iloc[fwd_idx]
        dt = frame.index[fwd_idx]
        price = float(row["close"])
        sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
        crisis_now = bool(raw_crisis.loc[dt]) if dt in raw_crisis.index else False
        holding_days = (dt - entry_date).days

        stop_check_price = price
        low_val = row.get("low")
        if low_val is not None and pd.notna(low_val):
            stop_check_price = float(low_val)

        exit_reason = evaluate_exit_reason(
            crisis_now=crisis_now, price=stop_check_price, stop_price=stop_price,
            sig_on=sig_on, holding_days=holding_days, max_holding_days=max_hold,
        )
        is_last_row = (fwd_idx == len(frame) - 1)
        if exit_reason is None and is_last_row:
            exit_reason = "END_OF_DATA"

        if exit_reason:
            if exit_reason == "STOP_LOSS":
                open_val = row.get("open")
                open_price = float(open_val) if open_val is not None and pd.notna(open_val) else price
                exit_price = min(stop_price, open_price)
            else:
                exit_price = price
            pnl_pct = (exit_price - entry_price) / entry_price
            return {
                "exit_reason": exit_reason, "exit_date": dt, "holding_days": holding_days,
                "pnl_pct": float(pnl_pct), "entry_price": entry_price, "exit_price": float(exit_price),
                "stop_distance_pct": stop_distance_pct,
            }

    return {"exit_reason": "NO_EXIT_FOUND", "exit_date": frame.index[-1], "holding_days": 0, "pnl_pct": 0.0}
