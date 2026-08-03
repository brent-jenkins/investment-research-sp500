"""Backtest engine — momentum signal + crisis override, validated across the
phased rebuild (see CLAUDE.md's "V2 architecture — phased rebuild" section
for the full validation history). This is the module the reproducibility
check ran against to confirm the production execution path
(brokers/trading212.py) matches what the research clone validated.

Also holds the metrics/equity-curve functions the engine itself needs
(compute_metrics, equity_curve_gbp) — these were originally in a separate
analysis/backtest_metrics.py alongside several V1-only analysis functions
(module attribution, signal decay, walk-forward by calendar window, per-
regime performance) that had no equivalent here; consolidated into this
file since these two are the only ones this engine (or live execution)
actually uses.
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

from analysis import crisis, signals
from analysis import universe
from analysis.decision import (
    evaluate_exit_reason,
    is_position_size_viable,
    is_reentry_blocked,
    passes_entry_gate,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
    resolve_reentry_cooldown_days,
)
from analysis.universe import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _atr, _resolve_entry_stop, _var_position_size_core
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metrics / equity curve
# ---------------------------------------------------------------------------

def compute_metrics(df: pd.DataFrame) -> dict:
    """Compute full performance metrics from a trades DataFrame.

    Args:
        df: DataFrame with columns pnl_pct, is_win, holding_days, entry_date.
            An optional pnl_gbp column (VaR-scaled £ P&L) adds a parallel set
            of "_gbp"-suffixed keys to the returned dict below; all existing
            "_pct"-suffixed keys are always computed from pnl_pct.

    Returns:
        Dict of metrics suitable for display.
    """
    if df is None or df.empty:
        return {"total_trades": 0}

    n        = len(df)
    wins     = df[df["is_win"] == True]
    losses   = df[df["is_win"] == False]

    win_rate = len(wins) / n if n > 0 else 0.0
    avg_ret  = df["pnl_pct"].mean() * 100
    avg_win  = wins["pnl_pct"].mean() * 100  if not wins.empty  else 0.0
    avg_loss = losses["pnl_pct"].mean() * 100 if not losses.empty else 0.0

    profit_factor = (
        (wins["pnl_pct"].sum() / abs(losses["pnl_pct"].sum()))
        if not losses.empty and losses["pnl_pct"].sum() != 0
        else float("inf")
    )

    avg_hold = df["holding_days"].mean() if "holding_days" in df.columns else 0.0
    total_return = df["pnl_pct"].sum() * 100

    # Equity curve (compounded)
    cum = (1 + df["pnl_pct"]).cumprod()
    final_equity = float(cum.iloc[-1]) - 1 if not cum.empty else 0.0
    peak = cum.cummax()
    max_dd = float(((cum - peak) / peak).min() * 100) if not cum.empty else 0.0

    # Sharpe (annualised, treating each trade return as a period)
    if n > 1 and df["pnl_pct"].std() > 0 and avg_hold > 0:
        periods_per_year = 252.0 / avg_hold
        sr = (df["pnl_pct"].mean() / df["pnl_pct"].std()) * np.sqrt(periods_per_year)
    else:
        sr = 0.0

    # Calmar
    calmar = (final_equity * 100 / abs(max_dd)) if max_dd < 0 else float("inf")

    # Longest drawdown streak
    streak = max_loss_streak = 0
    for w in df["is_win"]:
        if not w:
            streak += 1
            max_loss_streak = max(max_loss_streak, streak)
        else:
            streak = 0

    result = {
        "total_trades":       n,
        "win_rate":           round(win_rate, 4),
        "avg_return_pct":     round(avg_ret, 3),
        "avg_win_pct":        round(avg_win, 3),
        "avg_loss_pct":       round(avg_loss, 3),
        "profit_factor":      round(min(profit_factor, 99.9), 2),
        "total_return_pct":   round(total_return, 2),
        "compounded_return_pct": round(final_equity * 100, 2),
        "max_drawdown_pct":   round(max_dd, 2),
        "sharpe_ratio":       round(sr, 3),
        "calmar_ratio":       round(min(calmar, 99.9), 2),
        "avg_holding_days":   round(avg_hold, 1),
        "max_loss_streak":    max_loss_streak,
        "has_pnl_gbp":        False,
    }

    if "pnl_gbp" in df.columns and df["pnl_gbp"].notna().any():
        gbp = df["pnl_gbp"].dropna().astype(float)
        gbp_wins   = df[(df["is_win"] == True)  & df["pnl_gbp"].notna()]["pnl_gbp"]
        gbp_losses = df[(df["is_win"] == False) & df["pnl_gbp"].notna()]["pnl_gbp"]

        profit_factor_gbp = (
            (gbp_wins.sum() / abs(gbp_losses.sum()))
            if not gbp_losses.empty and gbp_losses.sum() != 0
            else float("inf")
        )

        result.update({
            "has_pnl_gbp":       True,
            "total_pnl_gbp":     round(float(gbp.sum()), 2),
            "avg_pnl_gbp":       round(float(gbp.mean()), 2),
            "avg_win_gbp":       round(float(gbp_wins.mean()), 2)  if not gbp_wins.empty  else 0.0,
            "avg_loss_gbp":      round(float(gbp_losses.mean()), 2) if not gbp_losses.empty else 0.0,
            "profit_factor_gbp": round(min(profit_factor_gbp, 999999.0), 2),
        })

    return result


def equity_curve_gbp(df: pd.DataFrame, starting_capital_gbp: float = 10000.0) -> pd.DataFrame:
    """Return a daily £ portfolio equity curve DataFrame from VaR-sized trades.

    A true £ equity curve: with VaR position sizing, each trade only ever
    risks a small slice of the account (position_gbp), so £ P&L ADDS onto a
    starting capital baseline: equity_t = starting_capital_gbp +
    cumsum(pnl_gbp), realised in exit-date order (the order P&L actually
    lands in a real account, not entry-date order — entry-date order can
    produce backwards jumps in the time axis whenever trades of different
    holding periods overlap). Returns an empty-columns DataFrame (not a
    curve pinned at starting_capital_gbp) when df has no usable pnl_gbp, so
    callers can tell "no £-sized trades in this run" apart from "a flat
    £ curve".
    """
    if df is None or df.empty or "pnl_gbp" not in df.columns or df["pnl_gbp"].isna().all():
        return pd.DataFrame(columns=["date", "equity_gbp", "drawdown_gbp"])

    df = df.dropna(subset=["pnl_gbp"]).copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.normalize()
    exit_col = "exit_date" if "exit_date" in df.columns else "entry_date"
    df["exit_date"] = pd.to_datetime(df[exit_col]).dt.normalize()

    df = df.sort_values("exit_date").reset_index(drop=True)
    cum_at_exit = starting_capital_gbp + df["pnl_gbp"].cumsum()

    exit_equity = pd.Series(cum_at_exit.values, index=df["exit_date"])
    exit_equity = exit_equity.groupby(level=0).last()

    all_days = pd.date_range(df["entry_date"].min(), df["exit_date"].max(), freq="D")
    equity = exit_equity.reindex(all_days).ffill().fillna(starting_capital_gbp)

    peak = equity.cummax()
    dd   = equity - peak

    return pd.DataFrame({
        "date":           all_days,
        "equity_gbp":     equity.values,
        "drawdown_gbp":   dd.values,
    })


# ---------------------------------------------------------------------------
# Signal precomputation
# ---------------------------------------------------------------------------

def precompute_ticker(ticker: str, config: dict) -> Optional[dict]:
    """Load a ticker's full available price history and compute its ATR,
    momentum signal, and raw momentum value. Membership is looked up
    per-date at simulation time (not baked in here) so a live universe.py
    refresh is always reflected without needing to re-run this step.

    `momentum` (added 2026-08-01) is the same continuous trailing-12-month
    return `signals.compute_momentum_series()` always computed internally
    to derive the boolean `signal` column — now also kept on its own, since
    `simulate_portfolio_compounding()`'s priority ranking needs a genuine
    strength/confidence measure, not just the on/off `signal` column. The
    two older engines (`simulate_trades()`/`simulate_portfolio()`) ignore
    this column entirely; adding it here is additive and harmless for them.
    """
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    atr_s = _atr(high, low, close, config["signals"]["atr_period"])
    momentum_s = signals.compute_momentum_series(close)
    sig = signals.compute_signal_series(close)

    frame = pd.DataFrame({"close": close, "atr": atr_s, "signal": sig, "momentum": momentum_s})
    return {"ticker": ticker, "frame": frame}


def slice_ticker(cached: Optional[dict], ts_start: pd.Timestamp, ts_end: pd.Timestamp) -> Optional[dict]:
    if cached is None:
        return None
    frame = cached["frame"]
    in_range = frame[(frame.index >= ts_start) & (frame.index <= ts_end)].dropna(subset=["close"])
    if in_range.empty:
        return None
    return {"ticker": cached["ticker"], "frame": in_range}


def make_blocks(start: pd.Timestamp, end: pd.Timestamp, n_blocks: int = 16) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    boundaries = pd.date_range(start, end, periods=n_blocks + 1)
    return [(boundaries[i], boundaries[i + 1]) for i in range(n_blocks)]


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def simulate_trades(
    frame: pd.DataFrame,
    ticker: str,
    config: dict,
    gspc_close: pd.Series,
    is_etf: bool,
) -> list[dict]:
    """Walk forward through *frame*, applying (in priority order): the
    crisis override (force-exit + entry block), index-membership gating
    (stocks only), the ATR stop, the momentum signal turning off, and the
    per-ticker max-holding backstop. Position sizing is the shared VaR
    formula every prior research path has used.

    Deliberately exempt from the 2026-08-03 re-entry-cooldown fix (see
    analysis/decision.py:is_reentry_blocked()'s own docstring) — this is
    the frozen engine every "Current baseline" figure in CLAUDE.md was
    generated with; adding new behaviour here would break that
    reproducibility guarantee rather than close a live-parity gap that
    matters for anything this engine is still used for.
    """
    bt_cfg = config.get("backtest", {})
    max_hold = get_max_holding_days(ticker, config)

    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(bt_cfg.get("starting_capital_gbp", 10000))
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    # max_position_gbp is no longer a config.yaml key (2026-08-01 -- live
    # order placement switched to a dynamic, account-value-relative ceiling
    # via trading212_invest.per_trade_pct; see CLAUDE.md's "Live execution
    # switched to the compounding two-parameter scheme" section). This
    # function (run_full_validation(uncapped=True)'s engine) is frozen to
    # the historical £2,500 flat ceiling every "Current baseline" figure in
    # CLAUDE.md was generated with, so re-running it on demand still
    # reproduces those figures exactly with no config dependency left to
    # drift out from under it.
    max_position_gbp = float(var_cfg.get("max_position_gbp", 2500))
    close_series = frame["close"].astype(float)

    raw_crisis = crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
    entry_blocked = crisis.extend_trigger(raw_crisis).reindex(frame.index).ffill().fillna(False)

    trades: list[dict] = []
    open_trade = None

    for dt, row in frame.iterrows():
        price = float(row["close"])
        atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
        sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
        is_member = is_etf or universe.is_member(ticker, dt.date() if hasattr(dt, "date") else dt)
        crisis_now = bool(raw_crisis.loc[dt])
        blocked_now = bool(entry_blocked.loc[dt])

        if open_trade is None:
            if not passes_entry_gate(sig_on=sig_on, is_member=is_member, entry_blocked=blocked_now):
                continue
            direction = "LONG"
            stop, stop_distance_pct = _resolve_entry_stop(direction, price, atr, config)

            prior_closes = close_series.loc[close_series.index < dt].tail(var_lookback + 1)
            log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, starting_capital, max_position_gbp, max_hold,
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(max_position_gbp * 0.5, 2)

            open_trade = {
                "ticker": ticker, "direction": direction, "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
            }
            continue

        entry_price = open_trade["entry_price"]
        stop_price = open_trade["stop_price"]
        holding_days = (dt - open_trade["entry_date"]).days

        # Exit priority — the single shared implementation
        # (analysis/decision.py:evaluate_exit_reason()), also called by
        # brokers/trading212.py, simulate_portfolio(),
        # simulate_portfolio_compounding(), and output/performance.py. See
        # CLAUDE.md's "Live/backtest decision-logic parity" section.
        # `is_member` is not passed here (2026-08-02) — membership no
        # longer governs an already-open position's exit; it is still
        # used above for this same row's entry gate.
        exit_reason = evaluate_exit_reason(
            crisis_now=crisis_now, price=price,
            stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
            max_holding_days=max_hold,
        )
        exit_price = stop_price if exit_reason == "STOP_LOSS" else price

        if exit_reason:
            pnl_pct = (exit_price - entry_price) / entry_price
            trades.append({
                "ticker": ticker, "direction": "LONG",
                "entry_date": open_trade["entry_date"].date() if hasattr(open_trade["entry_date"], "date") else open_trade["entry_date"],
                "exit_date": dt.date() if hasattr(dt, "date") else dt,
                "entry_price": entry_price, "exit_price": float(exit_price),
                "exit_reason": exit_reason, "pnl_pct": float(pnl_pct),
                "position_gbp": open_trade["position_gbp"], "pnl_gbp": open_trade["position_gbp"] * float(pnl_pct),
                "stop_distance_pct": open_trade["stop_distance_pct"],
                "holding_days": holding_days, "is_win": pnl_pct > 0,
            })
            open_trade = None

    if open_trade is not None:
        last = frame.iloc[-1]
        exit_p = float(last["close"])
        pnl_pct = (exit_p - open_trade["entry_price"]) / open_trade["entry_price"]
        hd = (frame.index[-1] - open_trade["entry_date"]).days
        trades.append({
            "ticker": ticker, "direction": "LONG",
            "entry_date": open_trade["entry_date"].date() if hasattr(open_trade["entry_date"], "date") else open_trade["entry_date"],
            "exit_date": frame.index[-1].date(),
            "entry_price": open_trade["entry_price"], "exit_price": exit_p,
            "exit_reason": "END_OF_DATA", "pnl_pct": float(pnl_pct),
            "position_gbp": open_trade["position_gbp"], "pnl_gbp": open_trade["position_gbp"] * float(pnl_pct),
            "stop_distance_pct": open_trade["stop_distance_pct"],
            "holding_days": hd, "is_win": pnl_pct > 0,
        })

    return trades


def run_candidate(tickers_cache: list, config: dict, gspc_close: pd.Series) -> list[dict]:
    all_trades: list[dict] = []
    for cached in tickers_cache:
        if cached is None:
            continue
        ticker = cached["ticker"]
        is_etf = ticker in universe.ETF_TICKERS
        all_trades.extend(simulate_trades(cached["frame"], ticker, config, gspc_close, is_etf))
    return all_trades


# ---------------------------------------------------------------------------
# Portfolio-capped simulation (added 2026-08-01) — a shared-account mode
# selectable alongside run_candidate()/simulate_trades() above, which are
# left completely unchanged. See CLAUDE.md's "Portfolio-capped backtest
# engine" section for the full design writeup and the live-code priority
# rule this was derived from (brokers/trading212.py's decide-then-submit
# pipeline: maybe_place_orders() / _submit_pending_orders() /
# _load_pending_orders()).
# ---------------------------------------------------------------------------

def simulate_portfolio(tickers_cache: list, config: dict, gspc_close: pd.Series) -> list[dict]:
    """Walk every ticker together in one shared chronological pass, enforcing
    trading212_invest.max_open_positions / max_total_deployed as a single
    cap shared across the whole universe — the cross-ticker portfolio
    constraint run_candidate()/simulate_trades() do not model at all (every
    ticker there is simulated fully independently against the same notional
    starting_capital_gbp baseline).

    Per-ticker signal/exit/stop/VaR-sizing math is byte-identical to
    simulate_trades() — only entries are additionally gated by shared
    portfolio state. Priority rule, derived from brokers/trading212.py's
    live decide-then-submit pipeline (see that module's maybe_place_orders()
    / _submit_pending_orders() / _load_pending_orders()): on any given day,
    (1) ETFs are prioritised before stocks (mirroring the live 07:00-UTC
    ETF-submission-before-13:30-UTC-stock-submission ordering), then (2)
    within each group, tickers are prioritised in the order they appear in
    *tickers_cache* (mirroring config.yaml's `instruments:` dict insertion
    order, which the live decide phase iterates and the live submit phase's
    `ORDER BY decided_at ASC` ties break by — both alphabetical in the
    current config.yaml, which is also the order analysis.universe.ALL_TICKERS
    already produces). A cap binding stops admitting further candidates for
    THE REST OF THAT DAY entirely (mirrors _submit_pending_orders()'s
    `break`, not a per-candidate `continue`) — both max_open_positions and
    max_total_deployed only ever get more binding as more orders are
    admitted within a single day's pass, so break/continue are equivalent
    for max_open_positions, but matter for max_total_deployed's "already at
    / over cap BEFORE this candidate" check (see below).

    Deliberate simplifications (stated explicitly, not silently):

    1. `account_value_gbp` for VaR sizing stays the fixed notional
       `backtest.starting_capital_gbp` — never compounding, identical to
       run_candidate()/simulate_trades(). This isolates the comparison to
       *only* the cap-enforcement difference; it deliberately does not also
       make the account compound (a second, separate change).
    2. `max_total_deployed` is checked as "is deployed capital already at or
       over the cap BEFORE this candidate's own size is added" — not "would
       admitting this candidate push deployed capital over the cap" — this
       exactly mirrors _submit_pending_orders()'s own check order (cap
       checked at the top of the loop using the running total from
       *previously admitted* orders only, before this iteration's order is
       priced in), including that a single admitted order CAN push the
       running total past the cap; the next candidate is what actually gets
       blocked. See CLAUDE.md's "V2 first-cycle deployment cap overshoot"
       section for the live incident this check shape was built to prevent.
    3. Deployed capital is tracked as the sum of admitted trades' own
       committed `position_gbp` (cost basis at entry, incremented on entry
       and decremented by the same amount on exit) — not a continuous
       mark-to-market valuation the way T212's live `investments.currentValue`
       is. The backtest has no per-day valuation of every open position
       across tickers to mirror that field exactly; cost-basis bookkeeping
       is the defensible approximation.
    4. A candidate rejected by either cap on a given day is simply not
       entered that day — it is NOT queued/deferred the way a live PENDING
       row is retried at the next submission window. It becomes a fresh
       candidate again on any later day its signal is still on and it still
       has no open position — the closest daily-resolution analogue of the
       live retry behaviour, since this engine has no sub-daily submission
       windows to defer within.
    5. Exits for every ticker are processed before any entries are
       considered, for every day in the walk, so a position closing today
       can free capacity for a DIFFERENT ticker's entry the same day.
       However — mirroring simulate_trades()'s own per-ticker control flow
       exactly, where the `open_trade is None` entry check is evaluated
       once per row, using the state as of before that row is processed —
       a ticker can never exit and re-enter on the same day itself: entry
       candidacy each day is restricted to tickers already flat BEFORE
       today's exits are processed, not tickers a same-day exit just
       freed up. A position that closes today becomes entry-eligible again
       starting its own next trading day at the earliest.

    `max_position_gbp` is enforced exactly as before, inside
    _var_position_size_core() itself — unaffected by any of the above.

    Deliberately exempt from the 2026-08-03 re-entry-cooldown fix (see
    analysis/decision.py:is_reentry_blocked()'s own docstring) — a
    position that closes today is genuinely entry-eligible again its own
    next trading day, no 30-day gap, matching this function's already-
    documented same-day-only restriction above. This is a frozen,
    historical-reproduction engine (see "Portfolio-capped validation
    findings" in CLAUDE.md); adding new behaviour here would break the
    reproducibility of already-published figures rather than close a
    live-parity gap that matters for anything this engine still drives.
    """
    bt_cfg = config.get("backtest", {})
    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(bt_cfg.get("starting_capital_gbp", 10000))
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    # max_position_gbp / max_open_positions are no longer config.yaml keys
    # (2026-08-01 -- see analysis/backtest.py:simulate_trades()'s identical
    # comment above, and CLAUDE.md's "Live execution switched to the
    # compounding two-parameter scheme" section). This function
    # (run_full_validation(portfolio_mode=True)'s engine) is frozen to the
    # historical £2,500 / 20-position values every "Portfolio-capped
    # validation findings" figure in CLAUDE.md was generated with.
    max_position_gbp = float(var_cfg.get("max_position_gbp", 2500))
    max_open_positions = int(var_cfg.get("max_open_positions", 20))
    max_total_deployed = float(var_cfg.get("max_total_deployed", 999.0))

    # Per-ticker precomputation — identical inputs/derivations to
    # simulate_trades()'s own per-ticker setup, just stored so the merged
    # day-by-day walk below can address any ticker's row for any of its own
    # trading days in O(1).
    per_ticker: dict[str, dict] = {}
    events: list[tuple] = []  # (date, priority_rank, ticker)

    for idx, cached in enumerate(tickers_cache):
        if cached is None:
            continue
        ticker = cached["ticker"]
        frame = cached["frame"]
        if frame is None or frame.empty:
            continue
        is_etf = ticker in universe.ETF_TICKERS
        close_series = frame["close"].astype(float)

        raw_crisis = crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
        entry_blocked = crisis.extend_trigger(raw_crisis).reindex(frame.index).ffill().fillna(False)

        priority_rank = (0 if is_etf else 1, idx)  # ETFs first, then input order within group
        per_ticker[ticker] = {
            "frame": frame,
            "rows": frame.to_dict("index"),
            "is_etf": is_etf,
            "close_series": close_series,
            "raw_crisis": raw_crisis,
            "entry_blocked": entry_blocked,
            "max_hold": get_max_holding_days(ticker, config),
            "priority_rank": priority_rank,
            "open_trade": None,
        }
        for dt in frame.index:
            events.append((dt, priority_rank, ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    trades: list[dict] = []
    current_open = 0
    current_deployed = 0.0

    i = 0
    n_events = len(events)
    while i < n_events:
        dt = events[i][0]
        j = i
        day_tickers: list[str] = []
        while j < n_events and events[j][0] == dt:
            day_tickers.append(events[j][2])
            j += 1

        # Snapshot which tickers were flat BEFORE today's exits are
        # processed — only these are entry-eligible today. This mirrors
        # simulate_trades()'s own per-ticker control flow exactly: its
        # `if open_trade is None:` entry check is evaluated once, at the
        # top of a row's processing, using the state as of before that row
        # — a position that exits ON this row falls through to `continue`
        # to the NEXT row without ever re-checking entry on the same row.
        # A ticker can therefore never exit and re-enter on the same day;
        # without this snapshot, Phase B below would (incorrectly) treat a
        # position Phase A just closed today as an entry candidate today,
        # which simulate_trades() never allows and would break exact
        # equivalence with run_candidate() whenever caps don't bind.
        flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}

        # Phase A — exits, every ticker with an open trade whose today's row
        # exists. Frees current_open/current_deployed for Phase B below.
        for ticker in day_tickers:
            st = per_ticker[ticker]
            ot = st["open_trade"]
            if ot is None:
                continue
            row = st["rows"][dt]
            price = float(row["close"])
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            crisis_now = bool(st["raw_crisis"].loc[dt])

            entry_price = ot["entry_price"]
            stop_price = ot["stop_price"]
            holding_days = (dt - ot["entry_date"]).days

            # Shared exit-priority function — see simulate_trades()'s
            # identical comment above. Membership is not computed here at
            # all (2026-08-02) — it no longer participates in an
            # already-open position's exit; Phase B below still computes
            # its own is_member for entry-gate purposes.
            exit_reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=price,
                stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
                max_holding_days=st["max_hold"],
            )
            # A ticker's own price data can end mid-simulation (a genuine
            # delisting/acquisition — the rolling quarterly-reconstitution
            # universe, unlike the old curated-40, routinely includes
            # candidates whose real history ends years before the overall
            # backtest's end date) while OTHER tickers' data continues for
            # years afterward. `events` only ever contains a row for dates a
            # ticker actually has data on, so once its last row is reached
            # here, the day-by-day walk below never revisits it again — if
            # its position is still open at that point, it must be forced
            # closed HERE, on this exact row, not deferred to the trailing
            # end-of-simulation loop below. Deferring it (the original,
            # pre-fix behaviour) left its position_gbp permanently "stuck" in
            # current_open/current_deployed for the ENTIRE remainder of the
            # simulation, silently starving every other candidate's capacity
            # for however many years of simulated time were left — verified
            # directly (2026-08-01): a 2-ticker synthetic fixture with
            # max_open_positions=1, one ticker's data ending 1.5 years before
            # the other's, produced exactly 1 trade total (the survivor
            # ticker, which had a continuously valid signal for that entire
            # 1.5-year stretch, got zero).
            if exit_reason is None and dt == st["frame"].index[-1]:
                exit_reason = "END_OF_DATA"
            exit_price = stop_price if exit_reason == "STOP_LOSS" else price

            if exit_reason:
                pnl_pct = (exit_price - entry_price) / entry_price
                trades.append({
                    "ticker": ticker, "direction": "LONG",
                    "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
                    "exit_date": dt.date() if hasattr(dt, "date") else dt,
                    "entry_price": entry_price, "exit_price": float(exit_price),
                    "exit_reason": exit_reason, "pnl_pct": float(pnl_pct),
                    "position_gbp": ot["position_gbp"], "pnl_gbp": ot["position_gbp"] * float(pnl_pct),
                    "stop_distance_pct": ot["stop_distance_pct"],
                    "holding_days": holding_days, "is_win": pnl_pct > 0,
                })
                current_open -= 1
                current_deployed -= ot["position_gbp"]
                st["open_trade"] = None

        # Phase B — entries. Gather today's eligible candidates (flat BEFORE
        # today — see flat_before_today above; signal on, member, not
        # crisis-blocked, via the shared entry-gate function), sort by
        # priority, admit while the shared caps allow.
        candidates: list[str] = []
        for ticker in flat_before_today:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(
                ticker, dt.date() if hasattr(dt, "date") else dt
            )
            blocked_now = bool(st["entry_blocked"].loc[dt])
            if not passes_entry_gate(sig_on=sig_on, is_member=is_member, entry_blocked=blocked_now):
                continue
            candidates.append(ticker)

        candidates.sort(key=lambda t: per_ticker[t]["priority_rank"])

        for ticker in candidates:
            # Cap check FIRST, using state as of before this candidate — see
            # simplification (2) above. Binding either cap stops the rest of
            # today's candidates entirely (mirrors _submit_pending_orders()'s
            # `break`).
            if current_open >= max_open_positions:
                break
            if starting_capital > 0 and (current_deployed / starting_capital) >= max_total_deployed:
                break

            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
            stop, stop_distance_pct = _resolve_entry_stop("LONG", price, atr, config)

            close_series = st["close_series"]
            prior_closes = close_series.loc[close_series.index < dt].tail(var_lookback + 1)
            log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, starting_capital, max_position_gbp, st["max_hold"],
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(max_position_gbp * 0.5, 2)

            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
            }
            current_open += 1
            current_deployed += position_gbp

        i = j

    # Defensive backstop only, post-fix — every ticker's own last row is now
    # forced closed inline in Phase A above the moment it's reached, so this
    # loop should find nothing left open EXCEPT the one edge case Phase A
    # can't cover: a position entered in Phase B on a ticker's own literal
    # final day has no later day left for Phase A to ever revisit. Kept as a
    # genuine catch-all (not dead code) for that case and any other
    # unanticipated one — never expected to run for a position whose ticker
    # had any further trading day in this backtest.
    for ticker, st in per_ticker.items():
        ot = st["open_trade"]
        if ot is None:
            continue
        frame = st["frame"]
        last = frame.iloc[-1]
        exit_p = float(last["close"])
        pnl_pct = (exit_p - ot["entry_price"]) / ot["entry_price"]
        hd = (frame.index[-1] - ot["entry_date"]).days
        trades.append({
            "ticker": ticker, "direction": "LONG",
            "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
            "exit_date": frame.index[-1].date(),
            "entry_price": ot["entry_price"], "exit_price": exit_p,
            "exit_reason": "END_OF_DATA", "pnl_pct": float(pnl_pct),
            "position_gbp": ot["position_gbp"], "pnl_gbp": ot["position_gbp"] * float(pnl_pct),
            "stop_distance_pct": ot["stop_distance_pct"],
            "holding_days": hd, "is_win": pnl_pct > 0,
        })

    return trades


def _would_deploy_pct(current_deployed: float, position_gbp: float, current_balance: float) -> float:
    """Shared by both compounding engines' skip-and-continue capacity
    check (simulate_portfolio_compounding()'s cost-basis current_deployed
    and simulate_portfolio_compounding_mtm()'s mark-to-market equivalent)
    -- extracted 2026-08-01 after Layer 1 of the structural live/backtest
    parity enforcement (see CLAUDE.md's "Structural enforcement for
    live/backtest parity" section) flagged this exact comparison
    duplicated verbatim between the two engines. Real, found-by-the-
    scanner-itself duplication, not a hypothetical: the mark-to-market
    engine was built the same day as this extraction and had already
    reproduced the identical two-line pattern from the original engine.
    Deliberately NOT shared with live's own `deployed_after_pct >
    max_deployed_pct` check in brokers/trading212.py's
    _submit_pending_orders() -- that side reads a genuinely different
    kind of value (a live, HTTP-sourced account snapshot) and can't
    literally call into a backtest-simulation helper; see this function's
    entry in tests/test_decision_logic_placement.py's whitelist for the
    live side specifically.
    """
    return (current_deployed + position_gbp) / current_balance


def simulate_portfolio_compounding(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    collect_rejections: bool = False,
):
    """Shared-account portfolio simulation, identical in structure to
    simulate_portfolio() (added 2026-08-01, same day) EXCEPT the account
    balance genuinely compounds and both caps are expressed as a percentage
    of that live, moving balance rather than a fixed notional constant.
    simulate_portfolio()/run_candidate()/simulate_trades() are all left
    completely unchanged -- this is a third, additional, selectable mode.

    Confirmed before building this (see CLAUDE.md's "Compounding portfolio
    backtest" section for the full writeup): simulate_portfolio() shares
    capital across tickers via current_open/current_deployed running state,
    but sizes and cap-checks everything against the FIXED
    backtest.starting_capital_gbp constant throughout -- it never
    compounds. Live trading (brokers/trading212.py) already compounds
    correctly by construction, since it queries T212's real, current
    account balance (client.get_account_summary()["totalValue"]) fresh on
    every sizing call rather than using any notional reference figure --
    the compounding gap is backtest-methodology-only. This function closes
    that gap for backtest research.

    Two parameters replace the old three (max_position_gbp flat-£,
    max_open_positions count, max_total_deployed %) -- both passed in
    directly rather than read from config, since they are the swept
    parameters this mode exists to study:

    - per_trade_pct: the VaR sizing ceiling for a single new entry, as a
      fraction of the CURRENT (not starting) balance --
      max_position_gbp_dynamic = current_balance * per_trade_pct, fed into
      the exact same _var_position_size_core() formula every other path in
      this codebase uses (account_value_gbp also = current_balance).
    - max_deployed_pct: the capacity ceiling a candidate's FULL position
      must fit under to be admitted -- see "Trade prioritization and
      capacity-allocation engine" (2026-08-01) below for how admission
      actually works; this is no longer a simple "stop once at/over the
      cap" break.

    No max_open_positions cap at all -- position count is an EMERGENT
    consequence of per_trade_pct and max_deployed_pct together (roughly
    1/per_trade_pct positions at full deployment), not a third,
    independently-enforced rule. Reuses simulate_portfolio()'s exact
    structural skeleton (day-by-day merged walk, Phase A exits before
    Phase B entries, the "flat before today's exits are processed"
    same-day-no-reentry rule) and its exact per-ticker exit/stop/crisis
    logic verbatim -- see that function's own docstring for the full
    semantics; only the balance/sizing/cap/priority mechanics described
    here differ.

    current_balance is updated the moment each Phase A exit is processed
    (trade["pnl_gbp"] added immediately) -- so a later same-day exit sees
    the balance already updated by an earlier one, and Phase B's entries
    that day are sized against the day's ENDING balance, after all of
    today's exits. Phase B itself never changes current_balance (only
    current_deployed, the cost-basis bookkeeping) -- entries realise no
    P&L at the moment they're opened -- so it is stable across the whole
    of a single day's candidate-admission loop.

    Graceful handling of a non-positive balance (a real possibility here
    that the fixed-notional modes above can never hit): if current_balance
    <= 0 on any day, Phase B (new entries) is skipped entirely for that day
    -- there is nothing meaningful to size a position against -- while
    Phase A (exits) always still runs normally. No exception, no divide-by-
    zero, no guessed position size.

    **Trade prioritization and capacity-allocation engine (2026-08-01)** --
    replaces the confirmed-arbitrary `(0 if is_etf else 1, idx)` priority
    rule (ETF-group-then-config-order) with signal strength, and replaces
    "stop admitting the instant the cap binds" with skip-and-continue. Built
    to mirror brokers/trading212.py's live submission logic exactly (see
    that module's `_load_pending_orders()` / `_submit_pending_orders()` and
    CLAUDE.md's own "Trade prioritization and capacity-allocation engine"
    section for the full design writeup and live-code cross-reference):

    1. **Priority is signal strength (trailing 12-month momentum,
       descending), never position size.** These are deliberately
       independent questions -- "is this worth doing" vs. "can we fit it."
       Each day, ETF candidates and stock candidates are ranked SEPARATELY
       by that day's own `momentum` value (the `precompute_ticker()`
       column, NaN sorting last) -- never combined into one cross-asset-
       class list. ETF candidates are still evaluated as a full group
       before any stock candidate, but this is temporal sequencing that
       mirrors live's two physically separate submission jobs (ETF
       always submits at 07:00 UTC, stocks always at 13:30 UTC -- an ETF
       candidate can never lose out to a same-day stock candidate in live
       execution, so the backtest must not let that happen either), not a
       priority judgement that ETFs matter more than stocks.
    2. **Skip-and-continue, never resize-to-fit.** Each candidate's FULL
       VaR-calibrated `position_gbp` is computed first; if
       `(current_deployed + position_gbp) / current_balance` would exceed
       `max_deployed_pct`, the candidate is rejected at its full intended
       size (never shrunk) and the walk continues to the NEXT candidate in
       momentum order -- a smaller, lower-priority candidate may still fit
       even where a larger, higher-priority one didn't.
    3. Every rejected candidate is recorded (ticker, date, is_etf,
       momentum, the position_gbp it would have taken, and the account
       state at the moment of rejection) if `collect_rejections=True` --
       default `False` preserves this function's original `list[dict]`
       return signature for every existing caller
       (`analysis/validation.py`, the compounding sweep). When
       `collect_rejections=True`, the return value is instead
       `(trades, rejections)`.
    4. Capacity is evaluated against `current_balance`/`current_deployed`,
       the same in-memory running state already described above -- reset
       fresh at the start of a real production run from precomputed price
       history, never a cross-run tracked total. This is the backtest
       mirror of live's "read the account's real, current state once per
       job" design (see brokers/trading212.py's `_submit_pending_orders()`
       docstring) -- there is no equivalent of "yesterday's leftover
       capacity" to accidentally carry over, by construction.

    Deliberately exempt from the 2026-08-03 re-entry-cooldown fix (see
    analysis/decision.py:is_reentry_blocked()'s own docstring) -- this
    cost-basis engine is frozen specifically so every already-published
    figure that cites it (e.g. the "Compounding portfolio backtest" sweep
    in CLAUDE.md) stays exactly reproducible; simulate_portfolio_
    compounding_mtm() below is the corrected, live-matching engine.
    """
    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    absolute_max_position_gbp = float(var_cfg.get("absolute_max_position_gbp", 1_000_000))

    per_ticker: dict[str, dict] = {}
    events: list[tuple] = []

    for idx, cached in enumerate(tickers_cache):
        if cached is None:
            continue
        ticker = cached["ticker"]
        frame = cached["frame"]
        if frame is None or frame.empty:
            continue
        is_etf = ticker in universe.ETF_TICKERS
        close_series = frame["close"].astype(float)

        raw_crisis = crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
        entry_blocked = crisis.extend_trigger(raw_crisis).reindex(frame.index).ffill().fillna(False)

        # idx here is purely a stable, deterministic tiebreaker for
        # iteration/event ordering -- it no longer determines ADMISSION
        # priority (that's momentum, computed fresh per day in Phase B
        # below). Kept as (is_etf group, idx) so day_tickers construction
        # stays deterministic run to run.
        event_rank = (0 if is_etf else 1, idx)
        per_ticker[ticker] = {
            "frame": frame,
            "rows": frame.to_dict("index"),
            "is_etf": is_etf,
            "idx": idx,
            "close_series": close_series,
            "raw_crisis": raw_crisis,
            "entry_blocked": entry_blocked,
            "max_hold": get_max_holding_days(ticker, config),
            "open_trade": None,
        }
        for dt in frame.index:
            events.append((dt, event_rank, ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    trades: list[dict] = []
    rejections: list[dict] = []
    current_balance = starting_capital
    current_deployed = 0.0

    def _admit_group(group_tickers: list[str], dt, current_deployed: float) -> float:
        """Rank *group_tickers* by that day's momentum (strongest first),
        walk skip-and-continue against remaining capacity. Returns the
        updated current_deployed. Mutates per_ticker[...]["open_trade"] and
        appends to trades/rejections (both closed over from the enclosing
        scope) -- current_balance is read-only here, never mutated (see
        this function's own docstring: Phase B never changes the balance,
        only current_deployed)."""
        candidates: list[tuple[str, float]] = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(
                ticker, dt.date() if hasattr(dt, "date") else dt
            )
            blocked_now = bool(st["entry_blocked"].loc[dt])
            # Shared entry-gate function — see simulate_trades()'s identical
            # comment above.
            if not passes_entry_gate(sig_on=sig_on, is_member=is_member, entry_blocked=blocked_now):
                continue
            momentum_val = row.get("momentum")
            momentum_val = float(momentum_val) if pd.notna(momentum_val) else float("-inf")
            candidates.append((ticker, momentum_val))

        # Strongest momentum first; a NaN/missing momentum value (should
        # not happen for a real signal-on row, but defensive) sorts last
        # via the -inf substitute above, same as SQLite's NULLS LAST
        # convention _load_pending_orders() relies on live. `idx` (each
        # ticker's original input-list position) is a deterministic
        # LAST-RESORT tiebreak only -- reached only on a genuine momentum
        # tie (two tickers with the exact same trailing-12m return, or both
        # missing momentum entirely) -- needed because group_tickers is
        # built from a Python set (flat_before_today) whose own iteration
        # order is not guaranteed stable across runs/processes; without
        # this, a tie would silently resolve via that nondeterministic
        # order instead. This is not a re-introduction of the old
        # ETF-then-config-order priority rule -- it only ever breaks a real
        # tie in the actual priority metric (momentum), never overrides it.
        candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))

        for ticker, momentum_val in candidates:
            if current_balance <= 0:
                break

            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
            stop, stop_distance_pct = _resolve_entry_stop("LONG", price, atr, config)

            dynamic_max_position_gbp = resolve_dynamic_max_position_gbp(
                current_balance, per_trade_pct, absolute_max_position_gbp,
            )
            close_series = st["close_series"]
            prior_closes = close_series.loc[close_series.index < dt].tail(var_lookback + 1)
            log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, current_balance, dynamic_max_position_gbp, st["max_hold"],
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(dynamic_max_position_gbp * 0.5, 2)

            # Minimum-viable-order backstop -- added 2026-08-01 alongside the
            # _var_position_size_core() clamp-ordering fix (see
            # analysis/volatility.py's "CLAMP ORDER IS DELIBERATE" docstring
            # note). This engine has no cash clamp (see this function's own
            # docstring: no cash concept to clamp against), so it's the one
            # path where the ceiling-wins fix can genuinely produce a
            # position below min_position_gbp — a severely depleted
            # current_balance whose dynamic_max_position_gbp has fallen
            # below the floor. Previously that could only ever happen if the
            # floor silently won (sizing ABOVE the intended ceiling, the bug
            # this fix closes); now it's the ceiling correctly winning
            # (sizing below the floor instead) -- this check is what stops a
            # resulting degenerate, not-worth-the-order size from being
            # admitted, mirroring the identical check already present in
            # live's maybe_place_orders() and
            # simulate_portfolio_compounding_mtm().
            if not is_position_size_viable(position_gbp):
                continue

            # Skip-and-continue capacity check -- evaluated against this
            # candidate's FULL, VaR-calibrated size, never shrunk to fit.
            would_deploy_pct = _would_deploy_pct(current_deployed, position_gbp, current_balance)
            if would_deploy_pct > max_deployed_pct:
                if collect_rejections:
                    rejections.append({
                        "ticker": ticker, "date": dt.date() if hasattr(dt, "date") else dt,
                        "is_etf": st["is_etf"], "momentum": momentum_val,
                        "position_gbp": position_gbp,
                        "would_deploy_pct": round(would_deploy_pct, 4),
                        "max_deployed_pct": max_deployed_pct,
                        "current_deployed_gbp": round(current_deployed, 2),
                        "current_balance_gbp": round(current_balance, 2),
                        "reason": "REJECTED_CAPACITY",
                    })
                continue

            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
            }
            current_deployed += position_gbp

        return current_deployed

    i = 0
    n_events = len(events)
    while i < n_events:
        dt = events[i][0]
        j = i
        day_tickers: list[str] = []
        while j < n_events and events[j][0] == dt:
            day_tickers.append(events[j][2])
            j += 1

        flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}

        # Phase A — exits. current_balance is updated immediately as each
        # trade closes, so later same-day exits/entries see the grown (or
        # shrunk) balance.
        for ticker in day_tickers:
            st = per_ticker[ticker]
            ot = st["open_trade"]
            if ot is None:
                continue
            row = st["rows"][dt]
            price = float(row["close"])
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            crisis_now = bool(st["raw_crisis"].loc[dt])

            entry_price = ot["entry_price"]
            stop_price = ot["stop_price"]
            holding_days = (dt - ot["entry_date"]).days

            # Shared exit-priority function — see simulate_trades()'s
            # identical comment above. Membership is not computed here at
            # all (2026-08-02) — it no longer participates in an
            # already-open position's exit; the admission helper above
            # still computes its own is_member for entry-gate purposes.
            exit_reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=price,
                stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
                max_holding_days=st["max_hold"],
            )
            # See simulate_portfolio()'s identical fix/comment above for the
            # full incident: a ticker's own data can end mid-simulation
            # (genuine delisting/acquisition) while other tickers continue
            # for years — without forcing END_OF_DATA here, on this exact
            # row, the position's capital stays falsely "stuck" in
            # current_deployed (and current_balance never gets its P&L
            # credited back) for the rest of the simulation, silently
            # starving every later candidate's capacity/sizing. Verified
            # directly against this function too, not just simulate_portfolio().
            if exit_reason is None and dt == st["frame"].index[-1]:
                exit_reason = "END_OF_DATA"
            exit_price = stop_price if exit_reason == "STOP_LOSS" else price

            if exit_reason:
                pnl_pct = (exit_price - entry_price) / entry_price
                pnl_gbp = ot["position_gbp"] * float(pnl_pct)
                trades.append({
                    "ticker": ticker, "direction": "LONG",
                    "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
                    "exit_date": dt.date() if hasattr(dt, "date") else dt,
                    "entry_price": entry_price, "exit_price": float(exit_price),
                    "exit_reason": exit_reason, "pnl_pct": float(pnl_pct),
                    "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
                    "stop_distance_pct": ot["stop_distance_pct"],
                    "holding_days": holding_days, "is_win": pnl_pct > 0,
                    "balance_at_exit_gbp": round(current_balance + pnl_gbp, 2),
                })
                current_deployed -= ot["position_gbp"]
                current_balance += pnl_gbp
                st["open_trade"] = None

        # Phase B — entries, only when there is a positive balance to size
        # against. Two sequential group passes -- ETF candidates fully
        # ranked and admitted first, THEN stock candidates against
        # whatever capacity remains -- mirroring live's physically separate
        # ETF (07:00 UTC) / stock (13:30 UTC) submission jobs exactly (see
        # this function's own docstring, point 1). current_balance does not
        # change within Phase B itself (only current_deployed does), so
        # it's safe to read once for the whole of today's two passes.
        if current_balance > 0:
            etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
            stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
            current_deployed = _admit_group(etf_group, dt, current_deployed)
            current_deployed = _admit_group(stock_group, dt, current_deployed)

        i = j

    # Defensive backstop only, post-fix — see simulate_portfolio()'s
    # identical comment above; Phase A now force-closes every ticker's own
    # last row inline, so this should only ever catch a position entered on
    # a ticker's own literal final day (balance updated for
    # completeness/symmetry, though nothing reads it after this point).
    for ticker, st in per_ticker.items():
        ot = st["open_trade"]
        if ot is None:
            continue
        frame = st["frame"]
        last = frame.iloc[-1]
        exit_p = float(last["close"])
        pnl_pct = (exit_p - ot["entry_price"]) / ot["entry_price"]
        pnl_gbp = ot["position_gbp"] * float(pnl_pct)
        hd = (frame.index[-1] - ot["entry_date"]).days
        trades.append({
            "ticker": ticker, "direction": "LONG",
            "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
            "exit_date": frame.index[-1].date(),
            "entry_price": ot["entry_price"], "exit_price": exit_p,
            "exit_reason": "END_OF_DATA", "pnl_pct": float(pnl_pct),
            "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
            "stop_distance_pct": ot["stop_distance_pct"],
            "holding_days": hd, "is_win": pnl_pct > 0,
            "balance_at_exit_gbp": round(current_balance + pnl_gbp, 2),
        })
        current_balance += pnl_gbp

    if collect_rejections:
        return trades, rejections
    return trades


def simulate_portfolio_compounding_mtm(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    collect_rejections: bool = False,
):
    """Mark-to-market variant of simulate_portfolio_compounding() — added
    2026-08-01 after confirming (see CLAUDE.md's "Live/backtest deployment-
    cap parity" section) that the cost-basis engine above does NOT match
    what live trading (brokers/trading212.py) actually does. Live reads
    T212's real `investments.currentValue` — genuine mark-to-market,
    reflecting every open position's unrealized gain/loss — for both the
    capacity check (`deployed_after_pct`) and position sizing
    (`per_trade_pct` against `totalValue`). The cost-basis engine's
    `current_deployed`/`current_balance` never incorporated unrealized P&L
    at all; a winning position never "grew" for capacity purposes, and a
    losing one never "shrank." This function closes that gap.

    Deliberately kept as a SEPARATE, explicitly-named function rather than
    a mode flag on simulate_portfolio_compounding() — that function is what
    every result reported before this fix was validated against, and stays
    completely unchanged and independently callable, the same "frozen
    engine, new one added alongside it" pattern simulate_portfolio() and
    simulate_trades() already established. Reuses the identical event-walk
    skeleton, per-ticker exit/stop/crisis logic, and momentum-priority/
    skip-and-continue admission design verbatim from
    simulate_portfolio_compounding() — see that function's own docstring
    for anything not called out below. Only the balance/capacity accounting
    itself differs.

    **The accounting model.** Cash and mark-to-market position value are
    tracked SEPARATELY, mirroring T212's own `cash.availableToTrade` +
    `investments.currentValue` = `totalValue` split:
    - `cash` starts at `starting_capital_gbp`. Decreases by a position's
      `position_gbp` the moment it's entered (money leaves cash, buys the
      position at cost). Increases by `position_gbp + pnl_gbp` the moment
      a position exits (the position's exit-time value returns to cash) —
      exactly the real cash movement a live account experiences.
    - Every open position's CURRENT value is computed fresh, on demand, as
      `position_gbp * (last_known_price / entry_price)` — `last_known_price`
      is whatever that ticker's most recent stored close is as of the
      current event date (forward-filled from its own last row if it has
      no row on today's exact date, mirroring how a real account's
      valuation uses the last traded price for a holding with no fresh
      quote yet).
    - `current_balance` (total account value, what `per_trade_pct` sizing
      is measured against) = `cash + sum(every open position's current
      value)`, recomputed once per day, after Phase A's exits and before
      Phase B's entries — stable across Phase B's two group passes for the
      same reason the cost-basis engine's own `current_balance` is: a new
      entry moves cash into an equally-valued position, so total account
      value doesn't move at the instant of entry, only the split between
      cash and invested does.
    - `current_deployed` (what `max_deployed_pct` is checked against) =
      the same `sum(every open position's current value)` component of
      `current_balance` above — genuinely mark-to-market, unlike the
      cost-basis engine's running cost total.

    Everything else — exit priority via evaluate_exit_reason(), entry gate
    via passes_entry_gate(), END_OF_DATA force-close on a ticker's own last
    row, momentum-priority ETF-then-stock skip-and-continue admission,
    graceful handling of a non-positive balance, collect_rejections'
    return-shape toggle — is identical in behaviour to
    simulate_portfolio_compounding(); only the numbers feeding the capacity
    check and the sizing formula differ.

    **Re-entry cooldown (2026-08-03, config-driven)** — this is the one
    engine that enforces analysis/decision.py:is_reentry_blocked(), since
    this is the engine analysis/validation.py:run_full_validation()
    actually defaults to and the one confirmed to match live order
    placement. The cooldown length itself is resolved once per call via
    resolve_reentry_cooldown_days(config) — trading212_invest.
    reentry_cooldown_days, falling back to the 30-day REENTRY_COOLDOWN_DAYS
    constant only when that key is absent. Every other engine in this
    file is deliberately exempt — see is_reentry_blocked()'s own
    docstring for the full reasoning.
    """
    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    absolute_max_position_gbp = float(var_cfg.get("absolute_max_position_gbp", 1_000_000))
    reentry_cooldown_days = resolve_reentry_cooldown_days(config)

    per_ticker: dict[str, dict] = {}
    events: list[tuple] = []

    for idx, cached in enumerate(tickers_cache):
        if cached is None:
            continue
        ticker = cached["ticker"]
        frame = cached["frame"]
        if frame is None or frame.empty:
            continue
        is_etf = ticker in universe.ETF_TICKERS
        close_series = frame["close"].astype(float)

        raw_crisis = crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
        entry_blocked = crisis.extend_trigger(raw_crisis).reindex(frame.index).ffill().fillna(False)

        event_rank = (0 if is_etf else 1, idx)
        per_ticker[ticker] = {
            "frame": frame,
            "rows": frame.to_dict("index"),
            "is_etf": is_etf,
            "idx": idx,
            "close_series": close_series,
            "raw_crisis": raw_crisis,
            "entry_blocked": entry_blocked,
            "max_hold": get_max_holding_days(ticker, config),
            "open_trade": None,
            "last_price": None,
            # Re-entry cooldown (2026-08-03) -- set the instant a trade
            # closes (Phase A below); is_reentry_blocked() reads it in
            # _admit_group(). None means "never traded", never blocked.
            "last_exit_date": None,
        }
        for dt in frame.index:
            events.append((dt, event_rank, ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    trades: list[dict] = []
    rejections: list[dict] = []
    cash = starting_capital

    def _open_positions_mtm_value() -> float:
        """Sum of every currently-open position's mark-to-market value,
        using each ticker's last known price (forward-filled) — never the
        entry price. A position whose ticker has never had a priced row
        since entry (should not happen in practice — every ticker's own
        entry event always sets last_price first) falls back to its own
        cost basis rather than raising, defensive only."""
        total = 0.0
        for st in per_ticker.values():
            ot = st["open_trade"]
            if ot is None:
                continue
            last_price = st["last_price"]
            if last_price is None or ot["entry_price"] <= 0:
                total += ot["position_gbp"]
            else:
                total += ot["position_gbp"] * (last_price / ot["entry_price"])
        return total

    def _admit_group(group_tickers: list[str], dt, current_balance: float, current_deployed: float) -> float:
        """Identical admission logic to simulate_portfolio_compounding()'s
        own _admit_group() — momentum-descending ranking, skip-and-continue
        against remaining capacity — except current_balance/current_deployed
        are both mark-to-market figures passed in fresh for the day, not
        cost-basis running totals. Returns the updated current_deployed;
        current_balance is read-only here (unchanged for the reason
        documented in this function's own docstring). cash IS mutated here
        (nonlocal) -- unlike current_balance, a real cash account genuinely
        does change the instant a new position is bought (money leaves
        cash), so this closure has to update it directly rather than
        thread it through a return value the way current_deployed is."""
        nonlocal cash
        candidates: list[tuple[str, float]] = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(
                ticker, dt.date() if hasattr(dt, "date") else dt
            )
            blocked_now = bool(st["entry_blocked"].loc[dt])
            if not passes_entry_gate(sig_on=sig_on, is_member=is_member, entry_blocked=blocked_now):
                continue
            # Re-entry cooldown (2026-08-03) -- mirrors live's
            # _load_open_order_tickers(): a ticker whose last trade
            # closed fewer than REENTRY_COOLDOWN_DAYS calendar days ago
            # is not yet eligible for a new entry, regardless of how
            # strong today's signal is. See analysis/decision.py:
            # is_reentry_blocked()'s own docstring for why this can't be
            # one shared function with live's bulk SQL check.
            last_exit = st.get("last_exit_date")
            days_since_exit = (dt - last_exit).days if last_exit is not None else None
            if is_reentry_blocked(days_since_exit, cooldown_days=reentry_cooldown_days):
                continue
            momentum_val = row.get("momentum")
            momentum_val = float(momentum_val) if pd.notna(momentum_val) else float("-inf")
            candidates.append((ticker, momentum_val))

        candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))

        for ticker, momentum_val in candidates:
            if current_balance <= 0:
                break

            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
            stop, stop_distance_pct = _resolve_entry_stop("LONG", price, atr, config)

            dynamic_max_position_gbp = resolve_dynamic_max_position_gbp(
                current_balance, per_trade_pct, absolute_max_position_gbp,
            )
            close_series = st["close_series"]
            prior_closes = close_series.loc[close_series.index < dt].tail(var_lookback + 1)
            log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, current_balance, dynamic_max_position_gbp, st["max_hold"],
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(dynamic_max_position_gbp * 0.5, 2)

            # Cash-availability clamp -- mirrors brokers/trading212.py's
            # own pre-existing min(position_gbp, free_cash * 0.25) via the
            # same shared function. Genuine cash tracking (never inflated
            # by another position's unrealized appreciation, unlike
            # current_balance above) is what already gave live order
            # placement real, structural protection against the worst of
            # the mark-to-market defect this engine was missing entirely
            # before this fix -- see CLAUDE.md's "Mark-to-market unbounded
            # compounding defect" section.
            position_gbp = resolve_cash_constrained_position_gbp(position_gbp, cash)

            # Minimum-viable-order backstop -- matches live's identical
            # check in maybe_place_orders(), added here 2026-08-01 (found
            # missing during the live/backtest consolidation audit that
            # followed the same day's mark-to-market fix: the cash clamp
            # immediately above was ported from live that same day, but
            # this corresponding floor was not, meaning a thin-cash
            # scenario could previously have opened a degenerate,
            # near-worthless position here that live would have correctly
            # skipped). A non-viable candidate is simply passed over, same
            # skip-and-continue treatment as a capacity miss below -- not
            # recorded as a rejection, mirroring live's plain `continue`
            # (no pending_orders row is ever written for it there either).
            if not is_position_size_viable(position_gbp):
                continue

            would_deploy_pct = _would_deploy_pct(current_deployed, position_gbp, current_balance)
            if would_deploy_pct > max_deployed_pct:
                if collect_rejections:
                    rejections.append({
                        "ticker": ticker, "date": dt.date() if hasattr(dt, "date") else dt,
                        "is_etf": st["is_etf"], "momentum": momentum_val,
                        "position_gbp": position_gbp,
                        "would_deploy_pct": round(would_deploy_pct, 4),
                        "max_deployed_pct": max_deployed_pct,
                        "current_deployed_gbp": round(current_deployed, 2),
                        "current_balance_gbp": round(current_balance, 2),
                        "reason": "REJECTED_CAPACITY",
                    })
                continue

            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
            }
            st["last_price"] = price
            current_deployed += position_gbp
            # A real, separate bug found and fixed alongside the unbounded-
            # compounding defect (2026-08-01): cash was previously credited
            # on exit (see Phase A below) but never DEBITED on entry, so
            # every open position's cost basis was effectively double-
            # counted in current_balance = cash + mtm_value -- once as
            # "still sitting in cash" (never actually spent) and again as
            # mtm_value. This alone compounds current_balance upward on
            # every single entry/exit cycle, independent of whether the
            # position was ever profitable or ever appreciated while
            # open -- confirmed to be a major contributor to the
            # magnitude of the runaway, not just the unrealized-
            # appreciation mechanism documented above. cash must decrease
            # by exactly what was spent, mirroring a real cash account.
            cash -= position_gbp

        return current_deployed

    i = 0
    n_events = len(events)
    while i < n_events:
        dt = events[i][0]
        j = i
        day_tickers: list[str] = []
        while j < n_events and events[j][0] == dt:
            day_tickers.append(events[j][2])
            j += 1

        flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}

        # Phase A — exits. cash is credited immediately as each trade
        # closes (position_gbp + pnl_gbp returned to cash), so later
        # same-day exits/entries see the already-updated cash balance.
        # last_price is updated for every ticker with a row today,
        # regardless of whether it has an open position, so a later entry
        # on the same ticker (impossible same-day per the
        # flat_before_today rule, but kept for correctness/symmetry) or a
        # future day's mark-to-market read always has the freshest price.
        for ticker in day_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            st["last_price"] = price

            ot = st["open_trade"]
            if ot is None:
                continue
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            crisis_now = bool(st["raw_crisis"].loc[dt])

            entry_price = ot["entry_price"]
            stop_price = ot["stop_price"]
            holding_days = (dt - ot["entry_date"]).days

            # Membership is not computed here at all (2026-08-02) — it no
            # longer participates in an already-open position's exit; the
            # admission helper for this group still computes its own
            # is_member for entry-gate purposes.
            exit_reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=price,
                stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
                max_holding_days=st["max_hold"],
            )
            if exit_reason is None and dt == st["frame"].index[-1]:
                exit_reason = "END_OF_DATA"
            exit_price = stop_price if exit_reason == "STOP_LOSS" else price

            if exit_reason:
                pnl_pct = (exit_price - entry_price) / entry_price
                pnl_gbp = ot["position_gbp"] * float(pnl_pct)
                trades.append({
                    "ticker": ticker, "direction": "LONG",
                    "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
                    "exit_date": dt.date() if hasattr(dt, "date") else dt,
                    "entry_price": entry_price, "exit_price": float(exit_price),
                    "exit_reason": exit_reason, "pnl_pct": float(pnl_pct),
                    "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
                    "stop_distance_pct": ot["stop_distance_pct"],
                    "holding_days": holding_days, "is_win": pnl_pct > 0,
                })
                cash += ot["position_gbp"] + pnl_gbp
                st["open_trade"] = None
                st["last_exit_date"] = dt

        # Phase B — entries. current_balance/current_deployed are both
        # recomputed fresh, mark-to-market, once for the whole day (after
        # today's exits, before either group pass) — this is the one real
        # difference from simulate_portfolio_compounding()'s cost-basis
        # running totals.
        mtm_value = _open_positions_mtm_value()
        current_balance = cash + mtm_value
        current_deployed = mtm_value
        if current_balance > 0:
            etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
            stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
            current_deployed = _admit_group(etf_group, dt, current_balance, current_deployed)
            current_deployed = _admit_group(stock_group, dt, current_balance, current_deployed)

        i = j

    # Defensive backstop only — see simulate_portfolio_compounding()'s
    # identical comment; Phase A now force-closes every ticker's own last
    # row inline, so this should only ever catch a position entered on a
    # ticker's own literal final day.
    for ticker, st in per_ticker.items():
        ot = st["open_trade"]
        if ot is None:
            continue
        frame = st["frame"]
        last = frame.iloc[-1]
        exit_p = float(last["close"])
        pnl_pct = (exit_p - ot["entry_price"]) / ot["entry_price"]
        pnl_gbp = ot["position_gbp"] * float(pnl_pct)
        hd = (frame.index[-1] - ot["entry_date"]).days
        trades.append({
            "ticker": ticker, "direction": "LONG",
            "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
            "exit_date": frame.index[-1].date(),
            "entry_price": ot["entry_price"], "exit_price": exit_p,
            "exit_reason": "END_OF_DATA", "pnl_pct": float(pnl_pct),
            "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
            "stop_distance_pct": ot["stop_distance_pct"],
            "holding_days": hd, "is_win": pnl_pct > 0,
        })
        cash += ot["position_gbp"] + pnl_gbp

    if collect_rejections:
        return trades, rejections
    return trades


def ex_best_trades(trades: list[dict]) -> list[dict]:
    if not trades:
        return []
    df = pd.DataFrame(trades)
    if df.empty:
        return trades
    idx_best = df["pnl_pct"].idxmax()
    return df.drop(index=idx_best).to_dict("records")


def compute_metrics_fixed(trades: list[dict], starting_capital: float) -> dict:
    """Additive-GBP, exit-date-sorted drawdown/CAGR — the corrected
    methodology, the standard for every result reported since."""
    if not trades:
        return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0, "cagr_pct": 0.0,
                "total_return_pct": 0.0}
    df = pd.DataFrame(trades)
    base = compute_metrics(df)

    ec = equity_curve_gbp(df, starting_capital_gbp=starting_capital)
    if ec.empty or len(ec) < 2:
        base["max_drawdown_pct"] = 0.0
        base["cagr_pct"] = 0.0
        base["total_return_pct"] = 0.0
        return base

    peak = ec["equity_gbp"].cummax()
    dd_pct = (ec["equity_gbp"] - peak) / starting_capital * 100.0
    max_dd = float(dd_pct.min())

    start_eq = float(ec["equity_gbp"].iloc[0])
    end_eq = float(ec["equity_gbp"].iloc[-1])
    days = (ec["date"].iloc[-1] - ec["date"].iloc[0]).days
    years = days / 365.25 if days > 0 else 0.0

    total_return_pct = (end_eq - starting_capital) / starting_capital * 100.0
    cagr = ((end_eq / starting_capital) ** (1.0 / years) - 1.0) * 100.0 if years > 0 and end_eq > 0 else 0.0

    base["max_drawdown_pct"] = round(max_dd, 2)
    base["cagr_pct"] = round(cagr, 2)
    base["total_return_pct"] = round(total_return_pct, 2)
    return base


def compute_peak_relative_drawdown_pct(trades: list[dict], starting_capital: float) -> Optional[float]:
    """The genuine, peak-relative max drawdown — `(equity - running_peak) /
    running_peak`, never the fixed-`starting_capital` denominator
    `compute_metrics_fixed()`'s own `max_drawdown_pct` uses. Both are real,
    correctly-computed numbers off the identical equity_curve_gbp() curve —
    they answer different questions. The fixed-denominator convention (kept
    unchanged everywhere else in this codebase, including
    compute_metrics_fixed() itself, for consistency with every already-
    published figure) answers "how far below the ORIGINAL starting capital
    did this account ever fall" — a number that can look small even after a
    huge intervening gain-then-crash, and can look huge on a run that grew
    the account substantially before any real decline at all (see CLAUDE.md's
    "No market-cap gate at all" section for a worked example: a
    fixed-denominator -27.43% vs. a genuine peak-relative -11.19% on the
    identical equity curve — roughly a 2.45x overstatement). Peak-relative
    answers "how far below its OWN HIGH did this account ever fall" — the
    number that matters for anyone who could have been invested at any
    point along the way, not only from day one. Returns None (never 0.0,
    which would misleadingly read as "no drawdown") when there's no usable
    £-sized equity curve to compute from, matching equity_curve_gbp()'s own
    empty-vs-flat distinction.
    """
    if not trades:
        return None
    ec = equity_curve_gbp(pd.DataFrame(trades), starting_capital_gbp=starting_capital)
    if ec.empty or len(ec) < 2:
        return None
    peak = ec["equity_gbp"].cummax()
    dd_pct = (ec["equity_gbp"] - peak) / peak * 100.0
    return round(float(dd_pct.min()), 2)


def full_metrics_fixed(trades: list[dict], starting_capital: float) -> dict:
    return {"raw": compute_metrics_fixed(trades, starting_capital),
            "ex_best": compute_metrics_fixed(ex_best_trades(trades), starting_capital)}


def compute_cost_sensitivity(
    trades: list[dict], starting_capital: float, cost_bps_list: tuple[int, ...] = (0, 10, 25),
) -> dict[int, dict]:
    """Re-run compute_metrics_fixed() with a round-trip transaction cost
    (entry + exit, each cost_bps/10000 of position value) subtracted from
    every trade's pnl_pct, for each cost level in cost_bps_list. 0 always
    reproduces the uncosted result exactly; 10/25bps are the levels this
    system's own due-diligence work has used elsewhere (e.g. the rejected
    sector-rotation/mean-reversion MIXED-regime research)."""
    results: dict[int, dict] = {}
    if not trades:
        for bps in cost_bps_list:
            results[bps] = compute_metrics_fixed([], starting_capital)
        return results

    base_df = pd.DataFrame(trades)
    for bps in cost_bps_list:
        cost_frac = (bps / 10000.0) * 2  # entry + exit
        df = base_df.copy()
        df["pnl_pct"] = df["pnl_pct"] - cost_frac
        if "pnl_gbp" in df.columns:
            df["pnl_gbp"] = df["pnl_gbp"] - (df.get("position_gbp", 0) * cost_frac)
        df["is_win"] = df["pnl_pct"] > 0
        results[bps] = compute_metrics_fixed(df.to_dict("records"), starting_capital)
    return results


def walk_forward_pf(trades: list[dict], window_months: int = 6, step_months: int = 1) -> dict:
    if not trades:
        return {"n_total": 0, "n_positive": 0, "success_rate_pct": 0.0}
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df = df.sort_values("entry_date")

    min_date, max_date = df["entry_date"].min(), df["entry_date"].max()
    current = min_date.to_period("M")
    end_p = max_date.to_period("M")
    windows = []
    while current <= end_p:
        win_start = current.to_timestamp()
        win_end = (current + window_months).to_timestamp() - pd.Timedelta(days=1)
        sub = df[(df["entry_date"] >= win_start) & (df["entry_date"] <= win_end)]
        if len(sub) >= 3:
            m = compute_metrics(sub)
            windows.append(m.get("profit_factor", 0.0))
        current = current + step_months

    n_total = len(windows)
    n_positive = sum(1 for pf in windows if pf > 1.0)
    success_rate_pct = round(n_positive / n_total * 100, 1) if n_total else 0.0
    return {"n_total": n_total, "n_positive": n_positive, "success_rate_pct": success_rate_pct}
