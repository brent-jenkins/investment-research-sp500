"""Tests for output/performance.py:close_trade() — the P&L/FX conversion math.

This is real money-math: for leveraged CFD trades it converts an
instrument-currency price move into GBP account P&L, applies overnight swap
cost, and deducts an FX conversion fee. Uses the db_engine fixture (in-memory
SQLite) for the trades table; the one live call inside the JPY conversion
path (get_latest_price("GBPUSD=X")) is monkeypatched — no real yfinance call.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from data.database import Trade
from output.performance import _swap_basis_amount, close_trade


def _seed_trade(engine, **overrides) -> int:
    defaults = dict(
        ticker="AAPL",
        direction="LONG",
        entry_price=100.0,
        quantity=1.0,
        leverage=1,
        is_open=True,
        entry_date=datetime.utcnow(),
        account_type="PRACTICE",
        fx_rate_at_entry=1.0,
        fx_fee_pct=0.0,
        overnight_rate=0.0,
        swap_basis="investment",
        investment_amount=0.0,
        margin_required=0.0,
        position_value=0.0,
    )
    defaults.update(overrides)
    with Session(engine) as session:
        trade = Trade(**defaults)
        session.add(trade)
        session.commit()
        return trade.id


# ---------------------------------------------------------------------------
# _swap_basis_amount — pure function, no DB
# ---------------------------------------------------------------------------

def test_swap_basis_amount_selects_margin_when_requested():
    """basis='margin' must use the margin amount, not investment or position."""
    assert _swap_basis_amount("margin", investment=100, margin=20, position=500) == 20


def test_swap_basis_amount_selects_position_when_requested():
    """basis='position' must use the full position value."""
    assert _swap_basis_amount("position", investment=100, margin=20, position=500) == 500


def test_swap_basis_amount_defaults_to_investment():
    """Any basis other than 'margin'/'position' (including the documented
    default 'investment') must use the investment amount."""
    assert _swap_basis_amount("investment", investment=100, margin=20, position=500) == 100


def test_swap_basis_amount_falls_back_to_investment_when_chosen_basis_is_zero():
    """A zero/unset margin or position must fall back to investment rather
    than silently applying the swap rate to a zero basis."""
    assert _swap_basis_amount("margin", investment=100, margin=0, position=500) == 100
    assert _swap_basis_amount("position", investment=100, margin=20, position=0) == 100


# ---------------------------------------------------------------------------
# close_trade() — leveraged CFD P&L / FX conversion
# ---------------------------------------------------------------------------

def test_close_trade_leveraged_non_jpy_converts_via_entry_fx_rate(db_engine):
    """For a leveraged non-JPY CFD, the instrument-currency P&L move must be
    divided by fx_rate_at_entry (the rate stored at open) to get GBP — not
    a freshly fetched live rate."""
    trade_id = _seed_trade(
        db_engine, ticker="EURUSD=X", direction="LONG",
        entry_price=1.1000, quantity=1000, leverage=10, fx_rate_at_entry=1.27,
    )
    result = close_trade(trade_id, exit_price=1.1050)

    pnl_instr = (1.1050 - 1.1000) * 1000  # USD move
    assert result["pnl"] == pytest.approx(pnl_instr / 1.27, abs=1e-4)
    assert result["pnl_pct"] == pytest.approx((1.1050 - 1.1000) / 1.1000, abs=1e-6)


def test_close_trade_jpy_pair_uses_live_gbpusd_not_entry_rate(db_engine, monkeypatch):
    """JPY-quoted pairs (USDJPY and similar) need a two-step conversion:
    divide by the exit price (itself the USDJPY rate) to get USD, then by
    the LIVE GBPUSD rate — not fx_rate_at_entry, which is irrelevant here."""
    monkeypatch.setattr(
        "output.performance.get_latest_price",
        lambda t: 1.25 if t == "GBPUSD=X" else None,
    )
    trade_id = _seed_trade(
        db_engine, ticker="USDJPY=X", direction="LONG",
        entry_price=150.00, quantity=1000, leverage=10,
        fx_rate_at_entry=999.0,  # deliberately wrong — must NOT be used for JPY pairs
    )
    result = close_trade(trade_id, exit_price=151.50)

    pnl_instr = (151.50 - 150.00) * 1000       # JPY move
    pnl_usd   = pnl_instr / 151.50              # JPY -> USD via exit price
    expected  = round(pnl_usd / 1.25, 4)        # USD -> GBP via live rate
    assert result["pnl"] == pytest.approx(expected, abs=1e-4)


def test_close_trade_non_leveraged_trade_skips_fx_conversion(db_engine):
    """With leverage=1, the leveraged FX-conversion branch is skipped
    entirely — P&L is simply (exit - entry) * direction * quantity, even if
    a (wrong) fx_rate_at_entry happens to be stored on the row."""
    trade_id = _seed_trade(
        db_engine, ticker="AAPL", direction="LONG",
        entry_price=100.0, quantity=10, leverage=1, fx_rate_at_entry=1.27,
    )
    result = close_trade(trade_id, exit_price=105.0)

    assert result["pnl"] == pytest.approx(50.0, abs=1e-4)  # (105-100)*10, NOT /1.27


def test_close_trade_short_direction_inverts_pnl_sign(db_engine):
    """A SHORT position must profit when price falls — the multiplier flips
    the sign of both pnl and pnl_pct relative to a LONG at the same prices."""
    trade_id = _seed_trade(
        db_engine, ticker="AAPL", direction="SHORT",
        entry_price=100.0, quantity=10, leverage=1,
    )
    result = close_trade(trade_id, exit_price=95.0)

    assert result["pnl"] == pytest.approx(50.0, abs=1e-4)   # (95-100)*(-1)*10
    assert result["pnl_pct"] == pytest.approx(0.05, abs=1e-4)


def test_close_trade_fx_fee_deducted_from_realised_pnl(db_engine):
    """fx_fee_pct must be applied to the magnitude of the realised P&L
    (post-swap), not the notional trade value — verify the exact deduction."""
    trade_id = _seed_trade(
        db_engine, ticker="AAPL", direction="LONG",
        entry_price=100.0, quantity=10, leverage=1, fx_fee_pct=0.5,
    )
    result = close_trade(trade_id, exit_price=110.0)

    pnl_before_fee = 100.0  # (110-100)*10
    fee = round(abs(pnl_before_fee) * 0.5 / 100, 4)
    assert result["pnl"] == pytest.approx(pnl_before_fee - fee, abs=1e-4)


def test_close_trade_overnight_swap_cost_accrues_per_holding_day(db_engine):
    """Overnight swap cost must scale with holding_days * swap_amount *
    rate/100 and be deducted from gross P&L — verified here with a flat
    price move so P&L is driven entirely by the swap charge."""
    entry_dt = datetime.utcnow() - timedelta(days=5)
    trade_id = _seed_trade(
        db_engine, ticker="EURUSD=X", direction="LONG",
        entry_price=1.10, quantity=1000, leverage=10, fx_rate_at_entry=1.0,
        investment_amount=100.0, overnight_rate=0.05, swap_basis="investment",
        entry_date=entry_dt,
    )
    result = close_trade(trade_id, exit_price=1.10)  # no price move

    swap_total = round(5 * 100.0 * 0.05 / 100, 4)
    assert result["pnl"] == pytest.approx(-swap_total, abs=1e-4)
    assert result["holding_days"] == 5


def test_close_trade_persists_pnl_to_the_trades_table(db_engine):
    """close_trade() must write pnl/pnl_pct/is_open back to the DB row, not
    just return them — callers that re-query the trade must see the update."""
    from sqlalchemy import text

    trade_id = _seed_trade(
        db_engine, ticker="AAPL", direction="LONG",
        entry_price=100.0, quantity=10, leverage=1,
    )
    close_trade(trade_id, exit_price=110.0)

    with db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT pnl, is_open FROM trades WHERE id = :id"), {"id": trade_id}
        ).mappings().first()
    assert row["pnl"] == pytest.approx(100.0, abs=1e-4)
    assert row["is_open"] == 0
