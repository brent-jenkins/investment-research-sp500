"""Tests for the feedback-learning system reading from t212_pnl (Stage 5).

_fetch_trades_for_learning() and recalculate_learned_weights() in
analysis/confluence.py, and get_t212_closed_positions() in output/performance.py,
were rewired from the legacy `trades` table to `t212_pnl` — the only
closed-position data source left now that virtual trading and manual CFD
trade logging have both been removed. All DB reads/writes go through the
db_engine fixture's in-memory SQLite database — see tests/conftest.py.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from data.database import T212Pnl, get_metadata_str


def _pnl_row(**overrides) -> dict:
    row = {
        "ticker":               "AAPL",
        "t212_ticker":          "AAPL",
        "buy_order_id":         1,
        "sell_order_id":        2,
        "entry_date":           datetime(2026, 6, 1),
        "exit_date":            datetime(2026, 6, 10),
        "entry_price":          100.0,
        "exit_price":           110.0,
        "quantity":             5.0,
        "currency":             "USD",
        "pnl_gbp":              39.4,
        "pnl_pct":              0.10,
        "holding_days":         9,
        "signal_type":          "STRONG_BUY",
        "confluence_score":     0.6,
        "regime":               "TRENDING_BULL",
        "exit_reason":          "STRONG_SELL_SIGNAL",
        "trend_score":          1,
        "mean_reversion_score": 0,
        "volatility_score":     1,
        "volume_score":         1,
        "macro_score":          0,
    }
    row.update(overrides)
    return row


def test_fetch_trades_for_learning_empty_when_no_t212_pnl_rows(db_engine):
    from analysis.confluence import _fetch_trades_for_learning

    assert _fetch_trades_for_learning() == []


def test_fetch_trades_for_learning_reads_from_t212_pnl(db_engine):
    """pnl_gbp must be aliased to `pnl` — the win/loss comparisons downstream
    expect that key, matching the shape the legacy `trades` table used to
    provide (Trade.pnl was the absolute-currency P&L, same as pnl_gbp here)."""
    from analysis.confluence import _fetch_trades_for_learning

    with Session(db_engine) as session:
        session.add(T212Pnl(**_pnl_row(buy_order_id=1, sell_order_id=2)))
        session.add(T212Pnl(**_pnl_row(
            ticker="XLF", buy_order_id=3, sell_order_id=4,
            pnl_gbp=-20.0, pnl_pct=-0.08, signal_type="BUY", regime="MIXED",
            trend_score=-1, mean_reversion_score=1, volatility_score=0,
            volume_score=-1, macro_score=1,
        )))
        session.commit()

    trades = _fetch_trades_for_learning()
    assert len(trades) == 2

    by_signal = {t["signal_type"]: t for t in trades}
    assert by_signal["STRONG_BUY"]["pnl"] == 39.4
    assert by_signal["STRONG_BUY"]["regime"] == "TRENDING_BULL"
    assert by_signal["STRONG_BUY"]["trend_score"] == 1
    assert by_signal["BUY"]["pnl"] == -20.0
    assert by_signal["BUY"]["macro_score"] == 1


def test_recalculate_learned_weights_returns_none_below_minimum(db_engine):
    from analysis.confluence import recalculate_learned_weights

    with Session(db_engine) as session:
        session.add(T212Pnl(**_pnl_row(buy_order_id=1, sell_order_id=2)))
        session.commit()

    config = {"learning": {"enabled": True, "min_trades_before_adjustment": 5}}
    assert recalculate_learned_weights(config) is None


def test_recalculate_learned_weights_computes_from_t212_pnl(db_engine):
    """Seed rows where `trend` always agrees with the win/loss outcome (100%
    win rate) and `volume` always disagrees (0% win rate) — trend's learned
    weight must end up higher than volume's."""
    from analysis.confluence import recalculate_learned_weights

    with Session(db_engine) as session:
        for i in range(5):
            win = i % 2 == 0
            session.add(T212Pnl(**_pnl_row(
                ticker=f"T{i}", buy_order_id=i * 2 + 1, sell_order_id=i * 2 + 2,
                pnl_gbp=10.0 if win else -10.0,
                trend_score=1 if win else -1,     # always agrees with outcome
                volume_score=-1 if win else 1,    # always disagrees with outcome
                mean_reversion_score=0, volatility_score=0, macro_score=0,
            )))
        session.commit()

    config = {"learning": {
        "enabled": True,
        "min_trades_before_adjustment": 5,
        "max_single_weight": 0.30,
        "min_single_weight": 0.05,
    }}
    weights = recalculate_learned_weights(config)

    assert weights is not None
    assert weights["trend"] > weights["volume"]

    stored = get_metadata_str("learned_weights")
    assert stored is not None
    import json
    payload = json.loads(stored)
    assert payload["n_trades"] == 5
    assert payload["win_rates"]["trend"] == 1.0
    assert payload["win_rates"]["volume"] == 0.0


def test_get_t212_closed_positions_reads_from_t212_pnl(db_engine):
    from output.performance import get_t212_closed_positions

    with Session(db_engine) as session:
        session.add(T212Pnl(**_pnl_row(buy_order_id=1, sell_order_id=2)))
        session.commit()

    trades = get_t212_closed_positions()
    assert len(trades) == 1
    assert trades[0]["direction"] == "LONG"
    assert trades[0]["pnl"] == 39.4
    assert trades[0]["ticker"] == "AAPL"


def test_get_t212_closed_positions_filters_by_exit_date_range(db_engine):
    from output.performance import get_t212_closed_positions

    with Session(db_engine) as session:
        session.add(T212Pnl(**_pnl_row(
            buy_order_id=1, sell_order_id=2,
            exit_date=datetime(2026, 5, 1),
        )))
        session.add(T212Pnl(**_pnl_row(
            ticker="XLF", buy_order_id=3, sell_order_id=4,
            exit_date=datetime(2026, 6, 15),
        )))
        session.commit()

    recent = get_t212_closed_positions(from_date=datetime(2026, 6, 1))
    assert len(recent) == 1
    assert recent[0]["ticker"] == "XLF"


def test_get_t212_closed_positions_empty_when_no_t212_pnl_rows(db_engine):
    from output.performance import get_t212_closed_positions

    assert get_t212_closed_positions() == []
