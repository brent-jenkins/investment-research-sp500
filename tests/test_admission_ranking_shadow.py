"""Forward shadow comparison for the relative-ranking promotion (2026-08-09)
-- see CLAUDE.md's "Forward shadow comparison" section. This is the only
forward evidence stream for the OPEN CANDIDATE decision (research/
relative_ranking_prereg_2026-08-09/, research/relranking_robustness_
2026-08-09/): does relative-rank admission ordering actually beat
absolute-momentum ordering in live operation, not just in a backtest.

Three things this file must prove, matching the task's own requirements:
1. The dual-ranking capacity simulation (_simulate_dual_ranking_admission())
   computes correct rank positions and admit/reject verdicts for both
   rankings, under real capacity constraints.
2. record_admission_ranking_shadow() writes correct, idempotent rows --
   and, decisively, an exception ANYWHERE in the shadow-recording path
   (the dual-ranking simulation itself, or the later buy-order-linking
   step inside the real submission loop) leaves the REAL order-placement
   loop completely unaffected -- verified by deliberately injecting a
   failure and confirming the real order still places successfully.
3. reconcile_admission_ranking_shadow_outcomes() correctly joins realised
   P&L from the EXISTING pnl table (never reimplementing FIFO pairing).
4. compute_admission_ranking_shadow_summary() keeps the structural
   asymmetry (only the active-mode-unique bucket can ever have real
   outcomes) explicit rather than blending it into one misleading number,
   and compute_admission_ranking_shadow_review_status() derives the
   review-trigger date from the first recorded decision, never a
   hardcoded build-time constant.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text as sa_text

from tests.test_execution import (
    _config as _v2_config,
    _mock_price_data,
    _seed_pending_order,
    v2_mocks,  # noqa: F401 -- reused fixture
)


# ---------------------------------------------------------------------------
# _simulate_dual_ranking_admission() -- pure function, no DB/network
# ---------------------------------------------------------------------------

def _row(id_, ticker, *, momentum_pct, relative_rank_pct, position_gbp, is_etf=False, decided_at="2026-01-01T00:00:00"):
    return {
        "id": id_, "ticker": ticker, "is_etf": is_etf, "momentum_pct": momentum_pct,
        "relative_rank_pct": relative_rank_pct, "position_gbp": position_gbp,
        "decided_at": decided_at,
    }


def test_dual_ranking_simulation_agrees_when_both_metrics_agree():
    """Three candidates, capacity for two -- momentum and relative rank
    agree on the ordering (A > B > C on both), so both rankings must admit
    the identical top-two set and agree on rank position."""
    from brokers.trading212 import _simulate_dual_ranking_admission

    pending = [
        _row(1, "A", momentum_pct=0.30, relative_rank_pct=0.30, position_gbp=4000.0),
        _row(2, "B", momentum_pct=0.20, relative_rank_pct=0.20, position_gbp=4000.0),
        _row(3, "C", momentum_pct=0.10, relative_rank_pct=0.10, position_gbp=4000.0),
    ]
    # total_val=10,000, max_deployed_pct=0.85 -> room for exactly two £4,000 positions (8,000/10,000=0.80 <= 0.85), not three (12,000/10,000=1.20 > 0.85)
    result = _simulate_dual_ranking_admission(pending, total_val=10_000.0, invested=0.0, free_cash=10_000.0, max_deployed_pct=0.85)

    assert result[1]["momentum_rank_position"] == 1
    assert result[1]["relative_rank_rank_position"] == 1
    assert result[1]["admitted_under_absolute_momentum"] is True
    assert result[1]["admitted_under_relative_rank"] is True

    assert result[2]["momentum_rank_position"] == 2
    assert result[2]["relative_rank_rank_position"] == 2
    assert result[2]["admitted_under_absolute_momentum"] is True
    assert result[2]["admitted_under_relative_rank"] is True

    assert result[3]["momentum_rank_position"] == 3
    assert result[3]["relative_rank_rank_position"] == 3
    assert result[3]["admitted_under_absolute_momentum"] is False
    assert result[3]["admitted_under_relative_rank"] is False


def test_dual_ranking_simulation_diverges_when_metrics_disagree():
    """Two candidates, capacity for only ONE -- A has higher momentum, B
    has higher relative rank. The two rankings must admit DIFFERENT
    tickers -- this is the exact shape of disagreement the whole shadow
    comparison exists to observe and record."""
    from brokers.trading212 import _simulate_dual_ranking_admission

    pending = [
        _row(1, "A_HIGHMOM", momentum_pct=0.30, relative_rank_pct=0.10, position_gbp=6000.0),
        _row(2, "B_HIGHRANK", momentum_pct=0.10, relative_rank_pct=0.30, position_gbp=6000.0),
    ]
    # total_val=10,000, cap=0.65 -> exactly one £6,000 position fits (0.60 <= 0.65), not two (1.20 > 0.65)
    result = _simulate_dual_ranking_admission(pending, total_val=10_000.0, invested=0.0, free_cash=10_000.0, max_deployed_pct=0.65)

    assert result[1]["admitted_under_absolute_momentum"] is True
    assert result[1]["admitted_under_relative_rank"] is False
    assert result[2]["admitted_under_absolute_momentum"] is False
    assert result[2]["admitted_under_relative_rank"] is True

    assert result[1]["momentum_rank_position"] == 1
    assert result[1]["relative_rank_rank_position"] == 2
    assert result[2]["momentum_rank_position"] == 2
    assert result[2]["relative_rank_rank_position"] == 1


def test_dual_ranking_simulation_never_mutates_caller_state():
    """The two internal capacity walks must be fully independent of each
    other AND of whatever the real caller does afterward with its own
    invested/free_cash -- this function takes floats (immutable), so
    there is nothing to mutate, but this test pins that contract down
    directly: calling it twice with the same inputs gives the same
    result, proving no hidden shared mutable state leaked between calls."""
    from brokers.trading212 import _simulate_dual_ranking_admission

    pending = [
        _row(1, "A", momentum_pct=0.30, relative_rank_pct=0.10, position_gbp=6000.0),
        _row(2, "B", momentum_pct=0.10, relative_rank_pct=0.30, position_gbp=6000.0),
    ]
    r1 = _simulate_dual_ranking_admission(pending, total_val=10_000.0, invested=0.0, free_cash=10_000.0, max_deployed_pct=0.65)
    r2 = _simulate_dual_ranking_admission(pending, total_val=10_000.0, invested=0.0, free_cash=10_000.0, max_deployed_pct=0.65)
    assert r1 == r2


def test_dual_ranking_simulation_missing_value_sorts_last():
    """A candidate with relative_rank_pct=None (should not happen for a
    real queued row post-promotion, but defensively) sorts last under
    relative-rank ordering -- never crashes, never wins priority by
    accident, matching _load_pending_orders()'s own established
    NULL-handling convention for admission_rank_value."""
    from brokers.trading212 import _simulate_dual_ranking_admission

    pending = [
        _row(1, "A_MISSING_RANK", momentum_pct=0.30, relative_rank_pct=None, position_gbp=1000.0),
        _row(2, "B_HAS_RANK", momentum_pct=0.10, relative_rank_pct=0.05, position_gbp=1000.0),
    ]
    result = _simulate_dual_ranking_admission(pending, total_val=10_000.0, invested=0.0, free_cash=10_000.0, max_deployed_pct=0.90)
    assert result[1]["relative_rank_rank_position"] == 2
    assert result[2]["relative_rank_rank_position"] == 1


# ---------------------------------------------------------------------------
# record_admission_ranking_shadow() -- writes correct, idempotent rows
# ---------------------------------------------------------------------------

def _shadow_rows(engine) -> list:
    with engine.connect() as conn:
        rows = conn.execute(sa_text("SELECT * FROM admission_ranking_shadow")).mappings().all()
    return [dict(r) for r in rows]


def test_record_admission_ranking_shadow_writes_correct_rows(db_engine):
    from brokers.trading212 import record_admission_ranking_shadow

    pending = [
        _row(101, "A", momentum_pct=0.30, relative_rank_pct=0.10, position_gbp=6000.0),
        _row(102, "B", momentum_pct=0.10, relative_rank_pct=0.30, position_gbp=6000.0),
    ]
    cfg = {"trading212_invest": {"max_total_deployed": 0.65}}  # admission_ranking_mode absent -> absolute_momentum
    record_admission_ranking_shadow(cfg, pending, total_val=10_000.0, invested=0.0, free_cash=10_000.0, max_deployed_pct=0.65)

    rows = {r["pending_order_id"]: r for r in _shadow_rows(db_engine)}
    assert set(rows.keys()) == {101, 102}
    assert rows[101]["ticker"] == "A"
    assert rows[101]["active_mode"] == "absolute_momentum"
    assert bool(rows[101]["admitted_under_absolute_momentum"]) is True
    assert bool(rows[101]["admitted_under_relative_rank"]) is False
    assert rows[101]["outcome_available"] in (0, False)
    assert rows[101]["real_buy_order_id"] is None


def test_record_admission_ranking_shadow_active_mode_reflects_config():
    pass  # covered by the relative_rank variant below, kept as a marker for readability


def test_record_admission_ranking_shadow_active_mode_relative_rank(db_engine):
    from brokers.trading212 import record_admission_ranking_shadow

    pending = [_row(201, "A", momentum_pct=0.30, relative_rank_pct=0.10, position_gbp=1000.0)]
    cfg = {"trading212_invest": {"max_total_deployed": 0.65, "admission_ranking_mode": "relative_rank"}}
    record_admission_ranking_shadow(cfg, pending, total_val=10_000.0, invested=0.0, free_cash=10_000.0, max_deployed_pct=0.65)

    rows = {r["pending_order_id"]: r for r in _shadow_rows(db_engine)}
    assert rows[201]["active_mode"] == "relative_rank"


def test_record_admission_ranking_shadow_is_idempotent_first_write_wins(db_engine):
    """A retried submission run for a still-PENDING row must never
    overwrite an already-recorded shadow snapshot with a different
    (later, already-mutated) capacity state -- write_admission_ranking_
    shadow_row() is INSERT-or-ignore, not upsert. Simulated here by
    calling record_admission_ranking_shadow() twice with genuinely
    different capacity state the second time."""
    from brokers.trading212 import record_admission_ranking_shadow

    pending = [_row(301, "A", momentum_pct=0.30, relative_rank_pct=0.10, position_gbp=1000.0)]
    cfg = {"trading212_invest": {"max_total_deployed": 0.65}}

    record_admission_ranking_shadow(cfg, pending, total_val=10_000.0, invested=0.0, free_cash=10_000.0, max_deployed_pct=0.65)
    first = {r["pending_order_id"]: r for r in _shadow_rows(db_engine)}[301]

    # Second call: wildly different capacity state (simulating a retry
    # after other candidates consumed capacity) -- must be silently
    # ignored, not overwrite the first snapshot.
    record_admission_ranking_shadow(cfg, pending, total_val=10_000.0, invested=9_500.0, free_cash=500.0, max_deployed_pct=0.65)
    rows = _shadow_rows(db_engine)
    assert len(rows) == 1
    second = {r["pending_order_id"]: r for r in rows}[301]
    assert second["recorded_at"] == first["recorded_at"]
    assert bool(second["admitted_under_absolute_momentum"]) == bool(first["admitted_under_absolute_momentum"])


def test_record_admission_ranking_shadow_empty_pending_is_a_noop(db_engine):
    from brokers.trading212 import record_admission_ranking_shadow

    record_admission_ranking_shadow({"trading212_invest": {}}, [], total_val=10_000.0, invested=0.0, free_cash=10_000.0, max_deployed_pct=0.5)
    assert _shadow_rows(db_engine) == []


# ---------------------------------------------------------------------------
# THE decisive fail-safe proof: an exception anywhere in the shadow path
# must leave the real order-placement loop completely unaffected.
# ---------------------------------------------------------------------------

def test_shadow_simulation_exception_does_not_block_real_submission(db_engine, v2_mocks, mocker):
    """Deliberately breaks _simulate_dual_ranking_admission() (called from
    inside record_admission_ranking_shadow(), called from inside
    _submit_pending_orders() BEFORE the real loop) so it raises -- and
    confirms the real BUY still gets placed, and the real pending_orders
    row still resolves to SUBMITTED, exactly as if the shadow code did not
    exist. This is the proof the promotion task's own requirement asks
    for: "a test proving an exception in the shadow path leaves real
    decisions untouched.\""""
    from brokers.trading212 import submit_pending_stock_orders

    mapping = {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}}
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False, position_gbp=500.0)
    _mock_price_data(mocker, {})

    mocker.patch(
        "brokers.trading212._simulate_dual_ranking_admission",
        side_effect=RuntimeError("injected shadow-path failure"),
    )

    cfg = _v2_config(mapping, mocker=mocker, db_engine=db_engine)
    n_submitted = submit_pending_stock_orders(cfg)

    assert n_submitted == 1
    v2_mocks.place_market_order.assert_called_once()
    with db_engine.connect() as conn:
        status = conn.execute(sa_text("SELECT status FROM pending_orders WHERE ticker = 'AAPL'")).scalar()
    assert status == "SUBMITTED"
    # And, per record_admission_ranking_shadow()'s own docstring, the
    # exception was swallowed before any row was written -- confirmed
    # directly, not just inferred from the real order having placed.
    assert _shadow_rows(db_engine) == []


def test_shadow_buy_order_linking_exception_does_not_block_real_submission(db_engine, v2_mocks, mocker):
    """The SECOND place an exception could leak into the real loop: after
    a real BUY places successfully, linking it to its shadow row
    (set_admission_ranking_shadow_buy_order()) must not be able to break
    the loop either, even though this call happens INSIDE the same
    per-row try/except as the real order logic."""
    from brokers.trading212 import submit_pending_stock_orders

    mapping = {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}}
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False, position_gbp=500.0)
    _mock_price_data(mocker, {})

    mocker.patch(
        "data.database.set_admission_ranking_shadow_buy_order",
        side_effect=RuntimeError("injected buy-order-linking failure"),
    )

    cfg = _v2_config(mapping, mocker=mocker, db_engine=db_engine)
    n_submitted = submit_pending_stock_orders(cfg)

    assert n_submitted == 1
    v2_mocks.place_market_order.assert_called_once()
    with db_engine.connect() as conn:
        status = conn.execute(sa_text("SELECT status FROM pending_orders WHERE ticker = 'AAPL'")).scalar()
    assert status == "SUBMITTED"


def test_real_submission_records_shadow_and_links_buy_order(db_engine, v2_mocks, mocker):
    """The clean (unbroken) path end to end: submitting a real order both
    records a shadow row AND links it to the real invest_orders BUY --
    the positive-case counterpart to the two fail-safe tests above."""
    from brokers.trading212 import submit_pending_stock_orders

    mapping = {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}}
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False, position_gbp=500.0)
    _mock_price_data(mocker, {})

    cfg = _v2_config(mapping, mocker=mocker, db_engine=db_engine)
    submit_pending_stock_orders(cfg)

    rows = _shadow_rows(db_engine)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert bool(rows[0]["admitted_under_absolute_momentum"]) is True

    with db_engine.connect() as conn:
        invest_order_id = conn.execute(sa_text(
            "SELECT id FROM invest_orders WHERE ticker = 'AAPL' AND direction = 'BUY'"
        )).scalar()
    assert invest_order_id is not None
    assert rows[0]["real_buy_order_id"] == invest_order_id


# ---------------------------------------------------------------------------
# reconcile_admission_ranking_shadow_outcomes() -- reuses the EXISTING pnl
# table, never reimplements FIFO pairing.
# ---------------------------------------------------------------------------

def _seed_shadow_row(engine, *, pending_order_id, ticker="AAPL", real_buy_order_id=None,
                      active_mode="absolute_momentum", admitted_momentum=True, admitted_rank=False):
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO admission_ranking_shadow
                (pending_order_id, decided_at, ticker, is_etf, momentum_pct, relative_rank_pct,
                 position_gbp, momentum_rank_position, relative_rank_rank_position,
                 admitted_under_absolute_momentum, admitted_under_relative_rank, active_mode,
                 real_buy_order_id, outcome_available, recorded_at)
            VALUES (:pid, :now, :ticker, 0, 0.10, 0.05, 500.0, 1, 1, :am, :ar, :mode, :buy_id, 0, :now)
        """), {
            "pid": pending_order_id, "now": datetime.utcnow().isoformat(), "ticker": ticker,
            "am": admitted_momentum, "ar": admitted_rank, "mode": active_mode, "buy_id": real_buy_order_id,
        })


def _seed_invest_order_and_pnl(engine, *, ticker="AAPL", pnl_gbp=42.5, pnl_pct=0.05, exit_reason="SIGNAL_OFF") -> tuple:
    """Seeds a real FILLED BUY/SELL pair and a matching pnl row (mirroring
    what _reconstruct_pnl() would have produced) -- returns (buy_order_id, sell_order_id)."""
    now = datetime.utcnow().isoformat()
    with engine.begin() as conn:
        buy_id = conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, quantity, status, is_etf, placed_at, filled_at, created_at)
            VALUES (:t, :tt, 'BUY', 10, 'FILLED', 0, :now, :now, :now)
        """), {"t": ticker, "tt": f"{ticker}_US_EQ", "now": now}).lastrowid
        sell_id = conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, quantity, status, is_etf, placed_at, filled_at, created_at)
            VALUES (:t, :tt, 'SELL', 10, 'FILLED', 0, :now, :now, :now)
        """), {"t": ticker, "tt": f"{ticker}_US_EQ", "now": now}).lastrowid
        conn.execute(sa_text("""
            INSERT INTO pnl (ticker, t212_ticker, buy_order_id, sell_order_id, entry_date, exit_date,
                              entry_price, exit_price, quantity, pnl_gbp, pnl_pct, exit_reason, created_at)
            VALUES (:t, :tt, :bid, :sid, :now, :now, 100.0, 105.0, 10, :pnl_gbp, :pnl_pct, :er, :now)
        """), {
            "t": ticker, "tt": f"{ticker}_US_EQ", "bid": buy_id, "sid": sell_id,
            "now": now, "pnl_gbp": pnl_gbp, "pnl_pct": pnl_pct, "er": exit_reason,
        })
    return buy_id, sell_id


def test_reconcile_populates_outcome_from_existing_pnl_row(db_engine):
    from brokers.trading212 import reconcile_admission_ranking_shadow_outcomes

    buy_id, _sell_id = _seed_invest_order_and_pnl(db_engine, pnl_gbp=42.5, pnl_pct=0.045, exit_reason="SIGNAL_OFF")
    _seed_shadow_row(db_engine, pending_order_id=1, real_buy_order_id=buy_id)

    n = reconcile_admission_ranking_shadow_outcomes()
    assert n == 1

    with db_engine.connect() as conn:
        row = dict(conn.execute(sa_text(
            "SELECT * FROM admission_ranking_shadow WHERE pending_order_id = 1"
        )).mappings().fetchone())
    assert bool(row["outcome_available"]) is True
    assert row["pnl_gbp"] == 42.5
    assert row["pnl_pct"] == 0.045
    assert row["exit_reason"] == "SIGNAL_OFF"
    assert row["closed_at"] is not None


def test_reconcile_leaves_row_untouched_when_position_not_yet_closed(db_engine):
    """A shadow row with a real BUY but no matching pnl row yet (position
    still open) must be left alone -- not marked outcome_available, no
    exception -- and picked up automatically on a LATER cycle once it
    genuinely closes (reconciliation is idempotent/retriable by design)."""
    from brokers.trading212 import reconcile_admission_ranking_shadow_outcomes

    _seed_shadow_row(db_engine, pending_order_id=1, real_buy_order_id=999)  # no matching invest_orders/pnl row
    n = reconcile_admission_ranking_shadow_outcomes()
    assert n == 0
    with db_engine.connect() as conn:
        row = dict(conn.execute(sa_text(
            "SELECT * FROM admission_ranking_shadow WHERE pending_order_id = 1"
        )).mappings().fetchone())
    assert bool(row["outcome_available"]) is False


def test_reconcile_skips_rows_with_no_real_buy_order(db_engine):
    """A shadow row for a candidate the ACTIVE mode never admitted (no
    real_buy_order_id set) is structurally never reconcilable -- confirmed
    it is never even selected as a candidate for reconciliation, matching
    get_unreconciled_admission_ranking_shadow_rows()'s own WHERE clause."""
    from data.database import get_unreconciled_admission_ranking_shadow_rows

    _seed_shadow_row(db_engine, pending_order_id=1, real_buy_order_id=None)
    assert get_unreconciled_admission_ranking_shadow_rows() == []


def test_reconcile_never_raises_on_a_db_read_failure(db_engine, mocker):
    from brokers.trading212 import reconcile_admission_ranking_shadow_outcomes

    _seed_shadow_row(db_engine, pending_order_id=1, real_buy_order_id=999)
    mocker.patch("data.database.get_engine", side_effect=RuntimeError("db unavailable"))
    n = reconcile_admission_ranking_shadow_outcomes()
    assert n == 0


def test_reconcile_returns_zero_and_does_nothing_when_nothing_unreconciled(db_engine):
    from brokers.trading212 import reconcile_admission_ranking_shadow_outcomes

    assert reconcile_admission_ranking_shadow_outcomes() == 0


# ---------------------------------------------------------------------------
# Small supporting helpers used by the review-trigger mechanism
# ---------------------------------------------------------------------------

def test_get_admission_ranking_shadow_first_decided_at_returns_none_when_empty(db_engine):
    from data.database import get_admission_ranking_shadow_first_decided_at

    assert get_admission_ranking_shadow_first_decided_at() is None


def test_get_admission_ranking_shadow_first_decided_at_returns_earliest(db_engine):
    from data.database import get_admission_ranking_shadow_first_decided_at

    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO admission_ranking_shadow
                (pending_order_id, decided_at, ticker, is_etf, admitted_under_absolute_momentum,
                 admitted_under_relative_rank, active_mode, outcome_available, recorded_at)
            VALUES (1, '2026-08-09T21:30:00', 'A', 0, 1, 0, 'absolute_momentum', 0, '2026-08-09T21:30:00')
        """))
        conn.execute(sa_text("""
            INSERT INTO admission_ranking_shadow
                (pending_order_id, decided_at, ticker, is_etf, admitted_under_absolute_momentum,
                 admitted_under_relative_rank, active_mode, outcome_available, recorded_at)
            VALUES (2, '2026-08-10T21:30:00', 'B', 0, 1, 0, 'absolute_momentum', 0, '2026-08-10T21:30:00')
        """))
    first = get_admission_ranking_shadow_first_decided_at()
    assert first is not None
    assert first.isoformat().startswith("2026-08-09T21:30:00")


# ---------------------------------------------------------------------------
# compute_admission_ranking_shadow_summary() -- pure aggregation, no DB
# ---------------------------------------------------------------------------

def _summary_row(*, active_mode="absolute_momentum", admitted_momentum, admitted_rank,
                  outcome_available=False, pnl_gbp=None, pnl_pct=None):
    return {
        "active_mode": active_mode,
        "admitted_under_absolute_momentum": admitted_momentum,
        "admitted_under_relative_rank": admitted_rank,
        "outcome_available": outcome_available,
        "pnl_gbp": pnl_gbp, "pnl_pct": pnl_pct,
    }


def test_summary_shared_admission_never_counted_as_a_unique_bucket():
    from output.performance import compute_admission_ranking_shadow_summary

    rows = [_summary_row(admitted_momentum=True, admitted_rank=True, outcome_available=True, pnl_gbp=10.0, pnl_pct=0.01)]
    summary = compute_admission_ranking_shadow_summary(rows)
    assert summary["shared"]["count"] == 1
    assert summary["active_unique"]["count"] == 0
    assert summary["inactive_unique"]["count"] == 0


def test_summary_active_unique_gets_real_stats_once_the_display_gate_clears():
    """5 active-mode-unique closed trades, 3 wins/2 losses -- win rate and
    mean P&L% only appear once ADMISSION_SHADOW_MIN_CLOSED_FOR_STATS (5)
    is cleared, matching the house Performance Metrics convention."""
    from output.performance import compute_admission_ranking_shadow_summary

    rows = [
        _summary_row(admitted_momentum=True, admitted_rank=False, outcome_available=True, pnl_gbp=+10.0, pnl_pct=+0.05),
        _summary_row(admitted_momentum=True, admitted_rank=False, outcome_available=True, pnl_gbp=+10.0, pnl_pct=+0.05),
        _summary_row(admitted_momentum=True, admitted_rank=False, outcome_available=True, pnl_gbp=+10.0, pnl_pct=+0.05),
        _summary_row(admitted_momentum=True, admitted_rank=False, outcome_available=True, pnl_gbp=-5.0, pnl_pct=-0.02),
    ]
    below_gate = compute_admission_ranking_shadow_summary(rows)
    assert below_gate["active_unique"]["closed_count"] == 4
    assert below_gate["active_unique"]["win_rate"] is None  # gate not cleared yet (4 < 5)

    rows.append(_summary_row(admitted_momentum=True, admitted_rank=False, outcome_available=True, pnl_gbp=-5.0, pnl_pct=-0.02))
    at_gate = compute_admission_ranking_shadow_summary(rows)
    assert at_gate["active_unique"]["closed_count"] == 5
    assert at_gate["active_unique"]["win_rate"] == pytest.approx(3 / 5)
    assert at_gate["active_unique"]["mean_pnl_pct"] == pytest.approx((0.05 * 3 - 0.02 * 2) / 5)


def test_summary_inactive_unique_never_shows_stats_even_with_a_large_count():
    """The structural limitation, pinned down directly: even 100
    inactive-mode-unique rows, none of which can ever have a real
    outcome, must show count-only -- never a fabricated 0% or blank
    treated as a real number."""
    from output.performance import compute_admission_ranking_shadow_summary

    rows = [_summary_row(admitted_momentum=False, admitted_rank=True, outcome_available=False) for _ in range(100)]
    summary = compute_admission_ranking_shadow_summary(rows)
    assert summary["inactive_unique"]["count"] == 100
    assert summary["inactive_unique"]["closed_count"] == 0
    assert summary["inactive_unique"]["win_rate"] is None
    assert summary["inactive_unique"]["mean_pnl_pct"] is None


def test_summary_classification_flips_correctly_when_active_mode_is_relative_rank():
    """Confirms classification is genuinely per-row (own active_mode),
    not hardcoded to absolute_momentum -- a row recorded while
    relative_rank was active must classify relative_rank's own admission
    as the 'active' side."""
    from output.performance import compute_admission_ranking_shadow_summary

    rows = [_summary_row(active_mode="relative_rank", admitted_momentum=False, admitted_rank=True,
                          outcome_available=True, pnl_gbp=10.0, pnl_pct=0.02)]
    summary = compute_admission_ranking_shadow_summary(rows)
    assert summary["active_unique"]["count"] == 1
    assert summary["inactive_unique"]["count"] == 0


def test_summary_neither_admitted_is_excluded_from_every_bucket():
    from output.performance import compute_admission_ranking_shadow_summary

    rows = [_summary_row(admitted_momentum=False, admitted_rank=False)]
    summary = compute_admission_ranking_shadow_summary(rows)
    assert summary["shared"]["count"] == 0
    assert summary["active_unique"]["count"] == 0
    assert summary["inactive_unique"]["count"] == 0
    assert summary["total_decisions"] == 1


def test_summary_empty_rows_returns_all_zero_not_an_error():
    from output.performance import compute_admission_ranking_shadow_summary

    summary = compute_admission_ranking_shadow_summary([])
    assert summary["total_decisions"] == 0
    assert summary["current_active_mode"] is None
    assert summary["active_unique"]["count"] == 0


def test_get_admission_ranking_shadow_rows_reads_from_the_real_table(db_engine):
    from output.performance import get_admission_ranking_shadow_rows

    _seed_shadow_row(db_engine, pending_order_id=1, ticker="AAPL")
    rows = get_admission_ranking_shadow_rows()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"


def test_get_admission_ranking_shadow_rows_never_raises_on_db_failure(db_engine, mocker):
    from output.performance import get_admission_ranking_shadow_rows

    mocker.patch("data.database.get_all_admission_ranking_shadow_rows", side_effect=RuntimeError("db error"))
    assert get_admission_ranking_shadow_rows() == []


# ---------------------------------------------------------------------------
# compute_admission_ranking_shadow_review_status() -- derived, not hardcoded
# ---------------------------------------------------------------------------

def test_review_status_none_when_no_shadow_data_yet():
    from output.performance import compute_admission_ranking_shadow_review_status

    status = compute_admission_ranking_shadow_review_status(active_unique_closed_count=0, first_decided_at=None)
    assert status["review_date"] is None
    assert status["review_due"] is False


def test_review_status_not_due_before_90_days_even_with_a_large_sample():
    from output.performance import compute_admission_ranking_shadow_review_status

    first = datetime.utcnow() - timedelta(days=10)
    status = compute_admission_ranking_shadow_review_status(active_unique_closed_count=500, first_decided_at=first)
    assert status["days_reached"] is False
    assert status["sample_reached"] is True
    assert status["review_due"] is False  # whichever comes LATER -- date not yet reached


def test_review_status_not_due_after_90_days_with_too_small_a_sample():
    from output.performance import compute_admission_ranking_shadow_review_status

    first = datetime.utcnow() - timedelta(days=200)
    status = compute_admission_ranking_shadow_review_status(active_unique_closed_count=2, first_decided_at=first)
    assert status["days_reached"] is True
    assert status["sample_reached"] is False
    assert status["review_due"] is False  # whichever comes LATER -- sample not yet reached


_CLEAN_CONCENTRATION = {
    "top_ticker": "AAA", "top_share": 0.20, "concentrated": False,
    "measurable": True, "ticker_count": 9,
}


def test_review_status_due_once_all_three_conditions_clear():
    from output.performance import compute_admission_ranking_shadow_review_status

    first = datetime.utcnow() - timedelta(days=200)
    status = compute_admission_ranking_shadow_review_status(
        active_unique_closed_count=55, first_decided_at=first,
        concentration=_CLEAN_CONCENTRATION,
    )
    assert status["days_reached"] is True
    assert status["sample_reached"] is True
    assert status["concentration_ok"] is True
    assert status["review_due"] is True


def test_review_status_sample_floor_is_50_not_30():
    """Pins the 2026-08-10 raise. 45 cleared the old floor and must not
    clear the new one -- asserted from behaviour at the boundary rather
    than by reading the constant back, so an accidental revert fails."""
    from output.performance import compute_admission_ranking_shadow_review_status

    first = datetime.utcnow() - timedelta(days=200)
    below = compute_admission_ranking_shadow_review_status(
        active_unique_closed_count=45, first_decided_at=first,
        concentration=_CLEAN_CONCENTRATION,
    )
    at_floor = compute_admission_ranking_shadow_review_status(
        active_unique_closed_count=50, first_decided_at=first,
        concentration=_CLEAN_CONCENTRATION,
    )
    assert below["sample_reached"] is False
    assert below["review_due"] is False
    assert at_floor["sample_reached"] is True
    assert at_floor["review_due"] is True


def test_review_status_blocked_when_one_ticker_dominates_the_effect():
    """The AMAT-precedent guard: date and sample both cleared, but one name
    carries more than the ceiling share, so the trigger must NOT fire."""
    from output.performance import compute_admission_ranking_shadow_review_status

    first = datetime.utcnow() - timedelta(days=200)
    status = compute_admission_ranking_shadow_review_status(
        active_unique_closed_count=80, first_decided_at=first,
        concentration={"top_ticker": "AMAT", "top_share": 0.62, "concentrated": True,
                       "measurable": True, "ticker_count": 11},
    )
    assert status["days_reached"] is True
    assert status["sample_reached"] is True
    assert status["concentration_ok"] is False
    assert status["review_due"] is False
    # The blocking reason must be reportable, not just a bare False.
    assert status["top_ticker"] == "AMAT"
    assert status["top_share"] == 0.62


def test_review_status_unmeasurable_concentration_does_not_count_as_clean():
    """"Cannot measure" must never read as "no concentration problem" --
    the same reasoning the compute function's own `measurable` flag exists
    for. Both an explicitly-unmeasurable dict and an omitted one block."""
    from output.performance import compute_admission_ranking_shadow_review_status

    first = datetime.utcnow() - timedelta(days=200)
    unmeasurable = compute_admission_ranking_shadow_review_status(
        active_unique_closed_count=80, first_decided_at=first,
        concentration={"top_ticker": None, "top_share": None, "concentrated": False,
                       "measurable": False, "ticker_count": 0},
    )
    omitted = compute_admission_ranking_shadow_review_status(
        active_unique_closed_count=80, first_decided_at=first,
    )
    for status in (unmeasurable, omitted):
        assert status["sample_reached"] is True
        assert status["concentration_ok"] is False
        assert status["review_due"] is False


def test_review_status_date_is_derived_from_first_decided_at_not_hardcoded():
    """Two different first_decided_at values must produce two different
    review dates, exactly 90 days later each -- proves the date is
    genuinely derived, not a fixed constant that happens to look right."""
    from output.performance import compute_admission_ranking_shadow_review_status

    first_a = datetime(2026, 8, 9, 21, 30)
    first_b = datetime(2026, 9, 1, 21, 30)
    status_a = compute_admission_ranking_shadow_review_status(active_unique_closed_count=0, first_decided_at=first_a)
    status_b = compute_admission_ranking_shadow_review_status(active_unique_closed_count=0, first_decided_at=first_b)
    assert status_a["review_date"] == first_a + timedelta(days=90)
    assert status_b["review_date"] == first_b + timedelta(days=90)
    assert status_a["review_date"] != status_b["review_date"]


# ---------------------------------------------------------------------------
# compute_admission_ranking_shadow_concentration() -- the AMAT-precedent guard
# ---------------------------------------------------------------------------

def _conc_row(ticker, pnl_pct, *, active_mode="relative_rank", own=True, other=False,
              outcome_available=True):
    """An active-mode-unique closed shadow row by default (own admitted,
    other not) -- the only population the concentration guard measures."""
    row = {"ticker": ticker, "active_mode": active_mode,
           "outcome_available": outcome_available, "pnl_pct": pnl_pct,
           "pnl_gbp": (pnl_pct or 0) * 100}
    if active_mode == "relative_rank":
        row["admitted_under_relative_rank"] = own
        row["admitted_under_absolute_momentum"] = other
    else:
        row["admitted_under_absolute_momentum"] = own
        row["admitted_under_relative_rank"] = other
    return row


def test_concentration_flags_a_single_dominant_ticker():
    from output.performance import compute_admission_ranking_shadow_concentration

    rows = [_conc_row("AMAT", 0.80)] + [_conc_row(f"T{i}", 0.02) for i in range(10)]
    conc = compute_admission_ranking_shadow_concentration(rows)
    assert conc["measurable"] is True
    assert conc["top_ticker"] == "AMAT"
    assert conc["top_share"] == pytest.approx(0.80 / (0.80 + 0.20))
    assert conc["concentrated"] is True


def test_concentration_clean_when_effect_is_spread_across_names():
    from output.performance import compute_admission_ranking_shadow_concentration

    conc = compute_admission_ranking_shadow_concentration(
        [_conc_row(f"T{i}", 0.05) for i in range(10)]
    )
    assert conc["measurable"] is True
    assert conc["concentrated"] is False
    assert conc["top_share"] == pytest.approx(0.10)
    assert conc["ticker_count"] == 10


def test_concentration_uses_absolute_pnl_so_a_big_loser_counts_as_much_as_a_big_winner():
    """A signed share would let one large loser look diluting rather than
    dominating -- the question is how much of the measured MOVEMENT one name
    accounts for, and a -60% name carries the read exactly as much as +60%."""
    from output.performance import compute_admission_ranking_shadow_concentration

    rows = [_conc_row("BIGLOSS", -0.60)] + [_conc_row(f"T{i}", 0.02) for i in range(5)]
    conc = compute_admission_ranking_shadow_concentration(rows)
    assert conc["top_ticker"] == "BIGLOSS"
    assert conc["concentrated"] is True


def test_concentration_groups_repeat_entries_of_one_ticker_together():
    """A name entered several times is ONE concentration risk, not several
    independent small ones -- grouping by row would hide exactly the case
    this guard exists to catch."""
    from output.performance import compute_admission_ranking_shadow_concentration

    rows = [_conc_row("REPEAT", 0.20), _conc_row("REPEAT", 0.20), _conc_row("REPEAT", 0.20)]
    rows += [_conc_row(f"T{i}", 0.05) for i in range(4)]
    conc = compute_admission_ranking_shadow_concentration(rows)
    assert conc["top_ticker"] == "REPEAT"
    assert conc["top_share"] == pytest.approx(0.60 / 0.80)
    assert conc["concentrated"] is True


def test_concentration_ignores_shared_and_inactive_unique_rows():
    """Only active-mode-unique rows carry a discriminating signal. A shared
    row (both rankings admit) and an inactive-unique row (never traded) must
    not enter the denominator, or a dominant name could be diluted by
    outcomes that say nothing about which ranking is better."""
    from output.performance import compute_admission_ranking_shadow_concentration

    rows = [
        _conc_row("DOMINANT", 0.90),
        _conc_row("SHARED", 0.90, own=True, other=True),
        _conc_row("INACTIVE", 0.90, own=False, other=True),
        _conc_row("SMALL", 0.10),
    ]
    conc = compute_admission_ranking_shadow_concentration(rows)
    assert conc["ticker_count"] == 2  # DOMINANT + SMALL only
    assert conc["top_ticker"] == "DOMINANT"
    assert conc["top_share"] == pytest.approx(0.90)
    assert conc["concentrated"] is True


def test_concentration_unmeasurable_with_no_closed_outcomes():
    from output.performance import compute_admission_ranking_shadow_concentration

    conc = compute_admission_ranking_shadow_concentration(
        [_conc_row("A", None, outcome_available=False)]
    )
    assert conc["measurable"] is False
    assert conc["concentrated"] is False
    assert conc["top_ticker"] is None


def test_concentration_all_flat_outcomes_is_unmeasurable_not_perfectly_diverse():
    """Every outcome exactly 0% is a real possibility and genuinely carries
    no information about concentration -- it must not report clean."""
    from output.performance import compute_admission_ranking_shadow_concentration

    conc = compute_admission_ranking_shadow_concentration(
        [_conc_row(f"T{i}", 0.0) for i in range(6)]
    )
    assert conc["measurable"] is False
    assert conc["concentrated"] is False


def test_concentration_respects_each_rows_own_active_mode():
    """Rows recorded before and after an admission_ranking_mode switch must
    each be classified against their OWN active_mode, not today's config."""
    from output.performance import compute_admission_ranking_shadow_concentration

    rows = [
        _conc_row("OLD", 0.50, active_mode="absolute_momentum"),
        _conc_row("NEW", 0.50, active_mode="relative_rank"),
        _conc_row("ALSO_OLD", 0.50, active_mode="absolute_momentum"),
    ]
    conc = compute_admission_ranking_shadow_concentration(rows)
    # All three are active-unique under their OWN mode -- if the classifier
    # wrongly used one fixed mode for every row, the two recorded under the
    # other mode would fall out of the population entirely.
    assert conc["ticker_count"] == 3
    assert conc["top_share"] == pytest.approx(1 / 3)
    assert conc["concentrated"] is False


def test_concentration_structurally_trips_on_a_very_small_ticker_count():
    """A deliberate, load-bearing property rather than an accident of the
    numbers: with fewer than three contributing names, some ticker must hold
    >40% arithmetically, so the guard always trips. That is the correct
    answer -- a two-name sample cannot distinguish a ranking rule from one
    lucky position, which is exactly what the guard exists to say. Pinned
    explicitly so nobody 'fixes' it later by special-casing small samples."""
    from output.performance import compute_admission_ranking_shadow_concentration

    two_names = compute_admission_ranking_shadow_concentration(
        [_conc_row("A", 0.50), _conc_row("B", 0.50)]
    )
    assert two_names["measurable"] is True
    assert two_names["top_share"] == pytest.approx(0.50)
    assert two_names["concentrated"] is True
