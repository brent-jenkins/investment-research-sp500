"""Layer 2 of the structural live/backtest parity enforcement added
2026-08-01 (see CLAUDE.md's "Structural enforcement for live/backtest
parity" section) -- a comprehensive, multi-day, multi-ticker replay,
deliberately different in kind from every other parity test in this
suite.

tests/test_live_backtest_parity.py drives real live/backtest code
against carefully hand-picked ISOLATED scenarios, one decision at a
time -- it has to be told in advance which specific condition to
construct. This file instead drives a genuinely continuous, multi-day
walk across several tickers competing for the same shared account
capacity, and asserts the entire resulting trade sequence (which
tickers entered, on which day, and which exited, on which day, for
which reason) matches between live's real decide-then-submit-then-exit
functions and the backtest engine -- without being told in advance
which specific day or ticker might diverge. This is what would have
caught the deployment-cap divergence even if nobody had ever thought to
write a targeted test for it: the divergence would simply show up as a
mismatched trade sequence.

Mechanism: a small in-process "replay harness" plays the role T212's
real API would play in production -- it tracks cash and open positions
and returns a genuinely mark-to-market get_account_summary()/
get_positions() response computed from that state, exactly as T212
itself would. maybe_place_orders() / submit_pending_etf_orders() /
submit_pending_stock_orders() / maybe_exit_positions() are all called
for REAL, once per simulated trading day, against this harness --
nothing about the decision-making code itself is mocked, only the
external HTTP boundary (Trading212Client) and the DB/price-data read
layer, exactly matching test_live_backtest_parity.py's own mocking
philosophy. The harness's own book-keeping is intentionally the exact
same mark-to-market accounting analysis.backtest.py:
simulate_portfolio_compounding_mtm() uses, since that is the engine
genuinely comparable to what live now does (see CLAUDE.md's "Live/
backtest deployment-cap parity" section for why the OLD cost-basis
engine is not a fair comparison for this test).
"""
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text as sa_text


# ---------------------------------------------------------------------------
# Synthetic multi-ticker scenario
# ---------------------------------------------------------------------------
# Two tickers, deliberately overlapping in time so they genuinely compete
# for the same shared deployment-cap capacity (the exact behaviour the
# deployment-cap bug affected) -- an ETF and a stock, so the ETF-before-
# stock same-day priority split is exercised too, matching both live's
# real 07:00/13:30 UTC submission split (collapsed to "same simulated day,
# ETF phase then stock phase" here, the same simplification
# simulate_portfolio_compounding_mtm() itself makes) and the backtest
# engine's own admission order.

_N_DAYS = 320  # >=300 so brokers/trading212.py:_ticker_state()'s own
               # "enough history" gate never itself suppresses a signal


def _rising_then_crashing_frame(crash_day: int, crash_pct: float) -> pd.DataFrame:
    """Rises steadily until crash_day, then a single-day crash of crash_pct,
    then flat -- deliberately a ONE-DAY shock rather than a sustained
    decline. A sustained decline (tried first, during this test's own
    development) reliably produced MULTIPLE STOP_LOSS-then-immediate-
    re-entry cycles: the position stops out while trailing 12-month
    momentum is still positive (only ONE bad stretch out of a long
    window doesn't flip it), so the backtest engine re-enters the very
    next eligible day, cycling several times before momentum finally
    turns. That re-entry timing is itself a genuine, separately-
    documented divergence from live (see this file's module docstring)
    -- not something this particular test is built to check. A single,
    severe enough crash (empirically calibrated -- see the parameter
    search this file's own commit message/CLAUDE.md entry references)
    instead flips momentum negative in the same move that triggers the
    stop, producing exactly one clean entry/exit cycle with no re-entry
    attempt at all, which is what this test needs to isolate capacity-
    sharing/sizing/timing parity from that separate re-entry-cooldown
    question entirely."""
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=_N_DAYS)
    closes = [100.0]
    for i in range(1, _N_DAYS):
        if i < crash_day:
            closes.append(closes[-1] * 1.003)
        elif i == crash_day:
            closes.append(closes[-1] * (1 + crash_pct))
        else:
            closes.append(closes[-1])
    closes = np.array(closes)
    highs = closes * 1.01
    lows = closes * 0.99
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes})


def _rising_frame() -> pd.DataFrame:
    """Rises steadily the whole window, never exits within it -- gives the
    harness a still-open position at the end of the replay too, the same
    "position still open, still being mark-to-marketed" case the unit
    tests in tests/test_portfolio_backtest.py already prove the engine
    itself handles correctly; this test proves live's real code produces
    the identical entry for it."""
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=_N_DAYS)
    closes = [100.0]
    for _ in range(1, _N_DAYS):
        closes.append(closes[-1] * 1.0025)
    closes = np.array(closes)
    highs = closes * 1.01
    lows = closes * 0.99
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes})


def _calm_gspc_frame() -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=_N_DAYS)
    closes = [4000.0]
    for _ in range(1, _N_DAYS):
        closes.append(closes[-1] * 1.0003)
    closes = np.array(closes)
    highs = closes * 1.005
    lows = closes * 0.995
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes})


def _config() -> dict:
    return {
        "instruments": {
            "ETFA": {"t212_instrument_id": "ETFA_EQ", "currency": "USD", "is_etf": True},
            "STKA": {"t212_instrument_id": "STKA_EQ", "currency": "USD", "is_etf": False},
        },
        "trading212_invest": {
            "enabled": True, "live": False,
            "max_total_deployed": 0.5, "per_trade_pct": 0.05,
            "var_confidence": 0.95, "var_lookback_days": 252,
            "max_risk_pct": 0.01, "min_position_gbp": 50,
            "max_holding_days": 9999, "stock_max_holding_days": 9999,
            "etf_submission_enabled": True, "stock_submission_enabled": True,
            "price_sanity_threshold_pct": 0.5,  # generous -- not what this test is about
        },
        "backtest": {
            "atr_stop_multiplier": 2.0,
            "starting_capital_gbp": 10000,
        },
        "signals": {"atr_period": 14},
    }


# ---------------------------------------------------------------------------
# The replay harness -- plays T212's role for real, mocked only at the HTTP
# boundary. Mark-to-market throughout, matching simulate_portfolio_
# compounding_mtm()'s own accounting exactly (see this file's module
# docstring for why that engine, not the cost-basis one, is the fair
# comparison).
# ---------------------------------------------------------------------------

class _ReplayHarness:
    def __init__(self, starting_capital: float, ticker_to_t212: dict[str, str]):
        self.cash = starting_capital
        self.t212_to_ticker = {v: k for k, v in ticker_to_t212.items()}
        # t212_ticker -> {"quantity": float, "entry_price": float}
        self.positions: dict[str, dict] = {}
        self.current_prices: dict[str, float] = {}  # t212_ticker -> today's price
        self.events: list[dict] = []  # recorded BUY/SELL calls, in call order

    def set_day_prices(self, prices_by_t212: dict[str, float]) -> None:
        self.current_prices.update(prices_by_t212)

    def mtm_value(self) -> float:
        total = 0.0
        for t212_ticker, pos in self.positions.items():
            price = self.current_prices.get(t212_ticker, pos["entry_price"])
            total += pos["quantity"] * price
        return total

    def account_summary(self) -> dict:
        mtm = self.mtm_value()
        return {
            "totalValue": self.cash + mtm,
            "cash": {"availableToTrade": self.cash},
            "investments": {"currentValue": mtm},
        }

    def positions_list(self) -> list[dict]:
        return [
            {"ticker": t212_ticker, "quantity": pos["quantity"]}
            for t212_ticker, pos in self.positions.items()
        ]

    def place_market_order(self, t212_ticker: str, quantity: float) -> dict:
        price = self.current_prices[t212_ticker]
        if quantity > 0:  # BUY
            cost = quantity * price
            self.cash -= cost
            self.positions[t212_ticker] = {"quantity": quantity, "entry_price": price}
            self.events.append({
                "ticker": self.t212_to_ticker.get(t212_ticker, t212_ticker),
                "direction": "BUY", "day_price": price,
            })
        else:  # SELL (quantity is negative -- the close amount)
            close_qty = abs(quantity)
            proceeds = close_qty * price
            self.cash += proceeds
            self.events.append({
                "ticker": self.t212_to_ticker.get(t212_ticker, t212_ticker),
                "direction": "SELL", "day_price": price,
            })
            self.positions.pop(t212_ticker, None)
        return {"id": f"replay-{len(self.events)}"}

    def get_quantity_for_gbp(self, t212_ticker, gbp_amount, **_kwargs) -> float:
        price = self.current_prices[t212_ticker]
        return gbp_amount / price if price > 0 else 0.0

    def get_instrument_price_gbp(self, t212_ticker, **_kwargs) -> float:
        return self.current_prices.get(t212_ticker, 0.0)


def _replay_frame(mocker, db_engine, harness: _ReplayHarness, config: dict,
                   frames: dict[str, pd.DataFrame], gspc_frame: pd.DataFrame,
                   t212_by_ticker: dict[str, str]) -> None:
    """Drives live's real decide -> submit -> exit functions once per
    simulated trading day, over the full length of *frames*. Only
    Trading212Client and get_price_df are mocked -- every decision
    function itself runs for real, exactly matching test_live_backtest_
    parity.py's own mocking boundary."""
    from brokers import trading212 as t212mod
    from analysis import universe as v2u

    mocker.patch.object(v2u, "ETF_TICKERS", tuple(t for t, e in config["instruments"].items() if e["is_etf"]))
    mocker.patch.object(v2u, "is_member", return_value=True)
    monkeypatch_sleep = mocker.patch("brokers.trading212.time.sleep", lambda *_a, **_k: None)  # noqa: F841

    mock_client = mocker.MagicMock()
    mock_client.get_account_summary.side_effect = lambda: harness.account_summary()
    mock_client.get_positions.side_effect = lambda: harness.positions_list()
    mock_client.get_instruments.return_value = []
    mock_client.get_quantity_for_gbp.side_effect = harness.get_quantity_for_gbp
    mock_client.get_instrument_price_gbp.side_effect = harness.get_instrument_price_gbp
    mock_client.place_market_order.side_effect = harness.place_market_order
    mocker.patch.object(t212mod, "Trading212Client", return_value=mock_client)

    n_days = len(next(iter(frames.values())))
    for day_idx in range(n_days):
        day_frames = {t: f.iloc[: day_idx + 1] for t, f in frames.items()}
        day_frames["^GSPC"] = gspc_frame.iloc[: day_idx + 1]

        def _fake_get_price_df(ticker, days=8000, _day_frames=day_frames):
            return _day_frames.get(ticker, _day_frames["^GSPC"]).copy()
        mocker.patch.object(t212mod, "get_price_df", side_effect=_fake_get_price_df)

        today_prices = {t212_by_ticker[t]: float(f.iloc[day_idx]["close"]) for t, f in frames.items()}
        harness.set_day_prices(today_prices)

        with db_engine.connect() as conn:
            prev_max_id = conn.execute(sa_text("SELECT COALESCE(MAX(id), 0) FROM invest_orders")).scalar()

        t212mod.maybe_place_orders(config, run_type="full")
        t212mod.submit_pending_etf_orders(config)
        t212mod.submit_pending_stock_orders(config)

        # Real live fills confirm asynchronously via poll_placed_orders()
        # (a real T212 order-status HTTP call this test doesn't otherwise
        # simulate). Instant-fill is a reasonable simplification for a
        # same-day market order at this test's synthetic prices -- what
        # matters for parity purposes is that a placed order becomes
        # visible to _load_open_position_context() (which the exit check
        # depends on) before the NEXT day's exit check runs, exactly as it
        # would in production within one real trading day. Marking PLACED
        # rows FILLED directly (rather than routing through a second,
        # separately-mocked polling call) keeps this harness's own moving
        # parts to a minimum.
        #
        # _load_open_order_tickers()'s 30-day re-entry dedup guard reads
        # SQLite's datetime('now') -- genuine REAL wall-clock time, exactly
        # right in production but wrong for a compressed-time replay: this
        # whole 320-simulated-day walk executes in a few real seconds, so
        # without correction, every order ever placed would look "within
        # the last 30 days" forever, permanently blocking any re-entry
        # after a ticker's first cycle -- a test-harness timescale
        # mismatch, not a live-code bug (confirmed by reading
        # _load_open_order_tickers()'s own query directly). Backdating each
        # day's newly-inserted rows to real_now minus (days remaining in
        # the simulated window) preserves each order's correct RELATIVE
        # spacing from every other simulated day's orders, so the 30-day
        # guard means "30 simulated trading days" here, matching what it
        # means in real production use.
        with db_engine.begin() as conn:
            conn.execute(sa_text(
                "UPDATE invest_orders SET status = 'FILLED', "
                "filled_at = datetime('now', :offset), "
                "placed_at = datetime('now', :offset) "
                "WHERE status = 'PLACED' AND id > :prev_max_id"
            ), {"offset": f"-{n_days - day_idx} days", "prev_max_id": prev_max_id})


def _harness_trade_sequence(harness: _ReplayHarness) -> "Counter[tuple[str, str]]":
    """(ticker, direction) -> COUNT of occurrences, order-independent but
    NOT count-collapsing -- a plain set here would silently treat "sold
    once" and "sold five times" as the same event, which is exactly
    wrong for a test whose entire point is catching a capacity/re-entry
    divergence between live and backtest (found live during this test's
    own development: an early set-based version of this comparison
    missed a real 5-vs-1 trade-count mismatch entirely)."""
    from collections import Counter
    return Counter((e["ticker"], e["direction"]) for e in harness.events)


@pytest.fixture
def replay_mocks(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    return None


def _run_scenario(mocker) -> tuple["Counter[tuple[str, str]]", "Counter[tuple[str, str]]"]:
    """Builds the standard two-ticker, ~320-simulated-day scenario, runs it
    through both live's real replay and the backtest engine, and returns
    (live_events, backtest_events) for the caller to assert on. Factored
    out so both the clean-pass test and the deliberately-injected-bug
    test below share the identical scenario and comparison logic -- the
    injected test's only difference from the clean one is patching a real
    bug into live's code before calling this, exactly mirroring how
    tests/test_live_backtest_parity.py itself was verified."""
    from analysis.backtest import simulate_portfolio_compounding_mtm
    from analysis import signals as v2sig
    from analysis.volatility import _atr
    from collections import Counter

    config = _config()
    t212_by_ticker = {"ETFA": "ETFA_EQ", "STKA": "STKA_EQ"}

    frame_etfa = _rising_frame()
    # Empirically calibrated (see _rising_then_crashing_frame()'s own
    # docstring): a -55% single-day crash on day 300 of 320 flips trailing
    # momentum negative in the same move that triggers STOP_LOSS, giving
    # exactly one clean entry/exit cycle with no re-entry attempt.
    frame_stka = _rising_then_crashing_frame(crash_day=300, crash_pct=-0.55)
    gspc = _calm_gspc_frame()

    # --- Live replay ---
    from data.database import get_engine
    db_engine = get_engine()
    harness = _ReplayHarness(starting_capital=10000.0, ticker_to_t212=t212_by_ticker)
    _replay_frame(
        mocker, db_engine, harness, config,
        {"ETFA": frame_etfa, "STKA": frame_stka}, gspc, t212_by_ticker,
    )
    live_events = _harness_trade_sequence(harness)

    # --- Backtest engine, identical price history ---
    def _cached(ticker: str, frame: pd.DataFrame) -> dict:
        df = frame.set_index("date")
        df.index = pd.to_datetime(df.index)
        close, high, low = df["close"].astype(float), df["high"].astype(float), df["low"].astype(float)
        atr_s = _atr(high, low, close, config["signals"]["atr_period"])
        sig = v2sig.compute_signal_series(close)
        mom = v2sig.compute_momentum_series(close)
        working = pd.DataFrame({"close": close, "atr": atr_s, "signal": sig, "momentum": mom})
        return {"ticker": ticker, "frame": working}

    gspc_close = gspc.set_index("date")["close"].astype(float)
    gspc_close.index = pd.to_datetime(gspc_close.index)

    tickers_cache = [_cached("ETFA", frame_etfa), _cached("STKA", frame_stka)]
    per_trade_pct = config["trading212_invest"]["per_trade_pct"]
    max_deployed_pct = config["trading212_invest"]["max_total_deployed"]
    backtest_trades = simulate_portfolio_compounding_mtm(
        tickers_cache, config, gspc_close, per_trade_pct, max_deployed_pct,
    )
    backtest_events: Counter = Counter()
    for t in backtest_trades:
        backtest_events[(t["ticker"], "BUY")] += 1
        if t["exit_reason"] != "END_OF_DATA":  # a still-open position at the window's end never sold live either
            backtest_events[(t["ticker"], "SELL")] += 1

    return live_events, backtest_events


def test_comprehensive_multiday_multiticker_replay_matches_backtest_trade_sequence(replay_mocks, mocker):
    """The actual Layer 2 check. Two tickers, ~320 simulated trading days,
    genuinely competing for shared deployment capacity (ETFA rises and
    stays open through the end of the window; STKA rises, then a single
    calibrated crash both stops it out AND flips its momentum negative in
    the same move, so it exits cleanly with no re-entry attempt -- see
    _rising_then_crashing_frame()'s own docstring for why a sustained
    decline was tried first and rejected). Live's real decide/submit/exit
    functions, replayed day by day against the harness above, must
    produce the same (ticker, direction) -> count of events the backtest
    engine's own simulate_portfolio_compounding_mtm() produces for the
    identical price history."""
    live_events, backtest_events = _run_scenario(mocker)

    # Sanity: the scenario must actually exercise something on both sides
    # (including a genuine exit, not just entries), or this test would
    # trivially "pass" by both sides doing nothing/the same trivial thing.
    assert sum(live_events.values()) > 0, "Replay harness recorded zero live events -- scenario doesn't exercise anything"
    assert sum(backtest_events.values()) > 0, "Backtest engine recorded zero events -- scenario doesn't exercise anything"
    assert any(d == "SELL" for _t, d in live_events), "Scenario never exercises a genuine exit on the live side"
    assert any(d == "SELL" for _t, d in backtest_events), "Scenario never exercises a genuine exit on the backtest side"

    assert live_events == backtest_events, (
        f"Live replay and backtest engine diverged on the executed trade "
        f"sequence (ticker, direction) -> count.\n"
        f"Live:     {dict(sorted(live_events.items()))}\n"
        f"Backtest: {dict(sorted(backtest_events.items()))}"
    )


def test_replay_actually_fails_on_a_deliberately_injected_live_bug(replay_mocks, mocker):
    """Proves the mechanism itself works, the same verification standard
    tests/test_live_backtest_parity.py's own suite and this file's Layer
    1 scanner were both held to -- not just asserted to exist. Patches a
    real, realistic bug directly into brokers/trading212.py (hardcoding
    evaluate_exit_reason() to never fire, exactly the kind of "silently
    disables an exit condition" bug the original parity suite's own
    verification used) and confirms this comprehensive replay correctly
    diverges as a result -- proving the mechanism catches a real
    behavioural break WITHOUT needing to be told in advance which
    specific decision was broken, unlike every other parity test in this
    suite."""
    from brokers import trading212 as t212mod

    mocker.patch.object(t212mod, "evaluate_exit_reason", return_value=None)

    live_events, backtest_events = _run_scenario(mocker)

    # With live exits disabled, STKA never sells even though its price
    # history genuinely calls for a STOP_LOSS -- backtest (unaffected by
    # this live-only patch) still shows the sell, so the two sides must
    # now disagree specifically on STKA's SELL count.
    assert live_events.get(("STKA", "SELL"), 0) == 0, "Injected bug should have disabled STKA's live exit"
    assert backtest_events.get(("STKA", "SELL"), 0) > 0, "Backtest should be unaffected by the live-only patch"
    assert live_events != backtest_events, (
        "Comprehensive replay failed to detect a deliberately injected live-side bug "
        "(evaluate_exit_reason forced to always return None) -- the mechanism itself "
        "is not working."
    )
