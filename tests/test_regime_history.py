"""Tests for analysis/regime_history.py — the Daily Briefing/Charts trend
chart's persistence layer (record_regime_snapshot(), get_regime_metrics_history(),
compute_shading_bands(), backfill_regime_metrics_history()).

record_regime_snapshot()/get_regime_metrics_history()/backfill_regime_metrics_history()
touch the DB, so those tests use the db_engine fixture (in-memory SQLite).
_count_signals() and compute_shading_bands() are pure and need no DB.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from analysis.regime import Regime
from analysis.regime_history import (
    _count_signals,
    _regime_permits_entries,
    backfill_regime_metrics_history,
    compute_shading_bands,
    get_regime_metrics_history,
    record_regime_snapshot,
    summarise_resolution,
)
from data.database import Indicator, MacroIndicator, PriceData, RegimeMetricsHistory, Signal

# Both automation paths explicitly gated to TRENDING_BULL-only, matching the
# shipped production config referenced throughout CLAUDE.md.
_GATE_CFG = {
    "trading212_invest": {"allowed_entry_regimes": ["TRENDING_BULL"]},
    "trading212_stocks": {"allowed_entry_regimes": ["TRENDING_BULL"]},
}


def _regime_result(regime=Regime.TRENDING_BULL, price=110, sma_200=100, adx=30, vix=15) -> dict:
    return {
        "regime": regime,
        "details": {"price": price, "sma_200": sma_200, "adx": adx, "vix": vix},
    }


def _signal_rows(buy=2, strong_buy=1, sell=1, strong_sell=0, neutral=3) -> list[dict]:
    rows = []
    for _ in range(buy):
        rows.append({"ticker": "X", "signal": "BUY"})
    for _ in range(strong_buy):
        rows.append({"ticker": "X", "signal": "STRONG_BUY"})
    for _ in range(sell):
        rows.append({"ticker": "X", "signal": "SELL"})
    for _ in range(strong_sell):
        rows.append({"ticker": "X", "signal": "STRONG_SELL"})
    for _ in range(neutral):
        rows.append({"ticker": "X", "signal": "NEUTRAL"})
    return rows


# ---------------------------------------------------------------------------
# _count_signals() — pure
# ---------------------------------------------------------------------------

def test_count_signals_tallies_buy_sell_and_strong_variants():
    counts = _count_signals(_signal_rows(buy=2, strong_buy=1, sell=1, strong_sell=1, neutral=3))
    assert counts == {
        "buy_count": 3,       # BUY + STRONG_BUY
        "sell_count": 2,      # SELL + STRONG_SELL
        "strong_buy_count": 1,
        "strong_sell_count": 1,
        "total_signals": 8,
    }


def test_count_signals_handles_empty_list():
    assert _count_signals([]) == {
        "buy_count": 0, "sell_count": 0,
        "strong_buy_count": 0, "strong_sell_count": 0,
        "total_signals": 0,
    }


# ---------------------------------------------------------------------------
# _regime_permits_entries() — pure
# ---------------------------------------------------------------------------

def test_regime_permits_entries_true_for_trending_bull():
    assert _regime_permits_entries(_GATE_CFG, "TRENDING_BULL") is True


def test_regime_permits_entries_false_for_mixed_when_both_paths_gated():
    assert _regime_permits_entries(_GATE_CFG, "MIXED") is False


def test_regime_permits_entries_true_if_any_enabled_path_allows_it():
    """If only one section carries a gate and the other is missing entirely,
    is_entry_allowed() treats the missing section as ungated (backwards
    compatible default) — so `any()` across both sections resolves True even
    though the configured path is blocked. This is a real, documented
    interaction between the two functions, not a bug to fix here."""
    cfg = {"trading212_invest": {"allowed_entry_regimes": ["TRENDING_BULL"]}}
    assert _regime_permits_entries(cfg, "MIXED") is True


# ---------------------------------------------------------------------------
# record_regime_snapshot() / get_regime_metrics_history() — DB-backed
# ---------------------------------------------------------------------------

def test_record_regime_snapshot_writes_expected_values(db_engine):
    ts = datetime(2026, 7, 20, 13, 30)
    record_regime_snapshot(
        _regime_result(price=110, sma_200=100, adx=30, vix=15),
        _signal_rows(buy=2, strong_buy=1, sell=1, strong_sell=0, neutral=3),
        _GATE_CFG,
        macro_health_score=0.42,
        timestamp=ts,
    )

    with Session(db_engine) as session:
        row = session.query(RegimeMetricsHistory).one()

    assert row.timestamp == ts
    assert row.regime == "TRENDING_BULL"
    assert row.entries_allowed == 1
    assert row.adx == 30
    assert row.vix == 15
    assert abs(row.sp500_pct_vs_sma200 - 10.0) < 1e-9  # (110/100 - 1) * 100
    assert row.macro_health_score == 0.42
    assert row.buy_count == 3
    assert row.sell_count == 1
    assert row.strong_buy_count == 1
    assert row.total_signals == 7
    assert row.is_backfilled == 0


def test_record_regime_snapshot_blocked_regime_sets_entries_allowed_false(db_engine):
    record_regime_snapshot(
        _regime_result(regime=Regime.MIXED), _signal_rows(), _GATE_CFG,
        timestamp=datetime(2026, 7, 20, 7, 0),
    )
    with Session(db_engine) as session:
        row = session.query(RegimeMetricsHistory).one()
    assert row.entries_allowed == 0


def test_record_regime_snapshot_truncates_seconds_for_upsert_key(db_engine):
    """A rapid double-invocation within the same minute (e.g. a double-clicked
    manual run) must upsert the same row rather than duplicate it."""
    ts1 = datetime(2026, 7, 20, 13, 30, 5)
    ts2 = datetime(2026, 7, 20, 13, 30, 45)
    record_regime_snapshot(_regime_result(adx=20), _signal_rows(), _GATE_CFG, timestamp=ts1)
    record_regime_snapshot(_regime_result(adx=40), _signal_rows(), _GATE_CFG, timestamp=ts2)

    with Session(db_engine) as session:
        rows = session.query(RegimeMetricsHistory).all()
    assert len(rows) == 1
    assert rows[0].adx == 40  # the second call's value won — genuine upsert, not insert-or-ignore


def test_record_regime_snapshot_never_raises_on_bad_regime_result(db_engine):
    """The 'never raises' contract — malformed input must be logged and
    swallowed, never propagate into the generate_signals() run it's attached to."""
    record_regime_snapshot({}, [], _GATE_CFG)  # no "regime" key at all
    with Session(db_engine) as session:
        assert session.query(RegimeMetricsHistory).count() == 0


def test_get_regime_metrics_history_filters_by_start_date(db_engine):
    old_ts = datetime(2026, 1, 1, 8, 0)
    new_ts = datetime(2026, 7, 1, 8, 0)
    record_regime_snapshot(_regime_result(), _signal_rows(), _GATE_CFG, timestamp=old_ts)
    record_regime_snapshot(_regime_result(), _signal_rows(), _GATE_CFG, timestamp=new_ts)

    df_all = get_regime_metrics_history()
    assert len(df_all) == 2

    df_filtered = get_regime_metrics_history(start=datetime(2026, 6, 1))
    assert len(df_filtered) == 1
    assert df_filtered.iloc[0]["timestamp"] == new_ts


def test_get_regime_metrics_history_returns_empty_df_when_no_data(db_engine):
    df = get_regime_metrics_history()
    assert df.empty


def test_get_regime_metrics_history_entries_allowed_is_bool(db_engine):
    record_regime_snapshot(_regime_result(), _signal_rows(), _GATE_CFG,
                            timestamp=datetime(2026, 7, 1, 8, 0))
    df = get_regime_metrics_history()
    assert df.iloc[0]["entries_allowed"] == True  # noqa: E712 — checking real bool, not truthiness
    assert df["entries_allowed"].dtype == bool


# ---------------------------------------------------------------------------
# compute_shading_bands() — pure
# ---------------------------------------------------------------------------

def _df_from_pairs(pairs):
    """pairs: list of (timestamp, allowed_bool)."""
    import pandas as pd
    return pd.DataFrame(
        {"timestamp": [p[0] for p in pairs], "entries_allowed": [p[1] for p in pairs]}
    )


def test_compute_shading_bands_empty_df_returns_no_bands():
    import pandas as pd
    assert compute_shading_bands(pd.DataFrame(columns=["timestamp", "entries_allowed"])) == []


def test_compute_shading_bands_single_row_returns_no_bands():
    df = _df_from_pairs([(datetime(2026, 1, 1), True)])
    assert compute_shading_bands(df) == []


def test_compute_shading_bands_all_same_state_is_one_band():
    ts = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(4)]
    df = _df_from_pairs([(t, True) for t in ts])
    bands = compute_shading_bands(df)
    assert len(bands) == 1
    assert bands[0] == {"start": ts[0], "end": ts[-1], "allowed": True}


def test_compute_shading_bands_splits_on_state_change():
    ts = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(4)]
    # allowed, allowed, blocked, blocked
    df = _df_from_pairs([(ts[0], True), (ts[1], True), (ts[2], False), (ts[3], False)])
    bands = compute_shading_bands(df)
    assert bands == [
        {"start": ts[0], "end": ts[2], "allowed": True},
        {"start": ts[2], "end": ts[3], "allowed": False},
    ]


def test_compute_shading_bands_represents_same_day_flip_as_its_own_band():
    """Two runs on the same calendar day with different regimes must produce
    two distinct bands — the chart's resolution follows whatever resolution
    the underlying data has, per the module's own documented approach."""
    morning = datetime(2026, 7, 20, 7, 0)
    evening = datetime(2026, 7, 20, 21, 30)
    df = _df_from_pairs([(morning, True), (evening, False)])
    bands = compute_shading_bands(df)
    assert len(bands) == 2
    assert bands[0]["allowed"] is True
    assert bands[1]["allowed"] is False


# ---------------------------------------------------------------------------
# summarise_resolution() — pure
# ---------------------------------------------------------------------------

def _df_with_backfill_flags(pairs):
    """pairs: list of (timestamp, is_backfilled_int)."""
    import pandas as pd
    return pd.DataFrame(
        {"timestamp": [p[0] for p in pairs], "is_backfilled": [p[1] for p in pairs]}
    )


def test_summarise_resolution_empty_df():
    import pandas as pd
    result = summarise_resolution(pd.DataFrame(columns=["timestamp", "is_backfilled"]))
    assert result == {"n_backfilled": 0, "n_live": 0, "boundary_timestamp": None}


def test_summarise_resolution_all_backfilled_has_no_boundary():
    """A pure-backfill view (e.g. a 6M range entirely before any live data
    exists yet) must not claim a boundary — there's nothing to mark."""
    ts = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(3)]
    df = _df_with_backfill_flags([(t, 1) for t in ts])
    result = summarise_resolution(df)
    assert result == {"n_backfilled": 3, "n_live": 0, "boundary_timestamp": None}


def test_summarise_resolution_all_live_has_no_boundary():
    ts = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(3)]
    df = _df_with_backfill_flags([(t, 0) for t in ts])
    result = summarise_resolution(df)
    assert result == {"n_backfilled": 0, "n_live": 3, "boundary_timestamp": None}


def test_summarise_resolution_mixed_boundary_is_last_backfilled_timestamp():
    """The boundary must be the LAST backfilled row's timestamp, not the
    first live row's — the chart marks 'backfilled data ends here', and a
    consumer reading the boundary should see it sitting at the edge of the
    daily-resolution stretch."""
    backfilled_ts = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(3)]
    live_ts = [datetime(2026, 7, 20, h, 0) for h in (7, 13, 21)]
    df = _df_with_backfill_flags(
        [(t, 1) for t in backfilled_ts] + [(t, 0) for t in live_ts]
    )
    result = summarise_resolution(df)
    assert result["n_backfilled"] == 3
    assert result["n_live"] == 3
    assert result["boundary_timestamp"] == backfilled_ts[-1]


# ---------------------------------------------------------------------------
# backfill_regime_metrics_history() — DB-backed
# ---------------------------------------------------------------------------

def _seed_day(engine, d: datetime, *, adx, vix, sma_200, price, regime, extra_tickers_signal="NEUTRAL"):
    """Seed one full day's worth of ^GSPC indicators/price + a signals row for
    ^GSPC (regime) plus a couple of other tickers (for buy/sell counting)."""
    with Session(engine) as session:
        session.add(PriceData(ticker="^GSPC", date=d, close=price))
        session.add(Indicator(ticker="^GSPC", date=d, sma_200=sma_200, adx=adx, vix_level=vix))
        session.add(Signal(ticker="^GSPC", date=d, signal="NEUTRAL", regime=regime))
        session.add(Signal(ticker="AAA", date=d, signal="BUY", regime=regime))
        session.add(Signal(ticker="BBB", date=d, signal="STRONG_SELL", regime=regime))
        session.commit()


def test_backfill_reconstructs_rows_from_existing_indicators_and_signals(db_engine):
    d1 = datetime(2026, 6, 1)
    d2 = datetime(2026, 6, 2)
    _seed_day(db_engine, d1, adx=30, vix=15, sma_200=100, price=110, regime="TRENDING_BULL")
    _seed_day(db_engine, d2, adx=15, vix=25, sma_200=100, price=90, regime="MIXED")

    result = backfill_regime_metrics_history(_GATE_CFG)

    assert result["rows_written"] == 2
    assert result["earliest_date"] == "2026-06-01"
    assert result["latest_date"] == "2026-06-02"

    df = get_regime_metrics_history()
    assert len(df) == 2
    row1 = df[df["timestamp"] == d1].iloc[0]
    assert row1["regime"] == "TRENDING_BULL"
    assert row1["entries_allowed"] == True  # noqa: E712
    assert row1["adx"] == 30
    assert row1["buy_count"] == 1   # AAA
    assert row1["sell_count"] == 1  # BBB (STRONG_SELL)
    assert row1["strong_sell_count"] == 1
    assert row1["is_backfilled"] == 1

    row2 = df[df["timestamp"] == d2].iloc[0]
    assert row2["regime"] == "MIXED"
    assert row2["entries_allowed"] == False  # noqa: E712


def test_backfill_is_idempotent_and_skips_already_recorded_dates(db_engine):
    d1 = datetime(2026, 6, 1)
    _seed_day(db_engine, d1, adx=30, vix=15, sma_200=100, price=110, regime="TRENDING_BULL")

    first = backfill_regime_metrics_history(_GATE_CFG)
    assert first["rows_written"] == 1

    second = backfill_regime_metrics_history(_GATE_CFG)
    assert second["rows_written"] == 0

    df = get_regime_metrics_history()
    assert len(df) == 1


def test_backfill_skips_dates_with_no_signals_row(db_engine):
    """A date with ^GSPC indicators but no signals row at all (e.g. analysis
    ran but signal generation failed) has no genuine 'acted-on' regime to
    record, so it must be skipped rather than guessed."""
    d1 = datetime(2026, 6, 1)
    with Session(db_engine) as session:
        session.add(PriceData(ticker="^GSPC", date=d1, close=110))
        session.add(Indicator(ticker="^GSPC", date=d1, sma_200=100, adx=30, vix_level=15))
        session.commit()

    result = backfill_regime_metrics_history(_GATE_CFG)
    assert result["rows_written"] == 0
    assert get_regime_metrics_history().empty


def test_backfill_does_not_overwrite_a_live_recorded_date(db_engine):
    """A date that already has a live (non-backfilled) snapshot must be left
    alone — backfill only ever fills genuine gaps."""
    d1 = datetime(2026, 6, 1, 21, 30)
    record_regime_snapshot(
        _regime_result(regime=Regime.TRENDING_BULL, adx=99), _signal_rows(), _GATE_CFG, timestamp=d1,
    )
    _seed_day(db_engine, datetime(2026, 6, 1), adx=1, vix=1, sma_200=100, price=110, regime="MIXED")

    backfill_regime_metrics_history(_GATE_CFG)

    df = get_regime_metrics_history()
    assert len(df) == 1
    assert df.iloc[0]["adx"] == 99  # the live row, untouched


def test_backfill_populates_macro_health_from_vix_when_no_fred_series_configured(db_engine):
    """With no macro.fred_series configured at all (as in _GATE_CFG), the
    as-of macro health reconstruction should still fall back to VIX alone
    rather than leaving macro_health_score permanently None."""
    d1 = datetime(2026, 6, 1)
    # Low VIX (15) classifies bullish (+1) per _classify_vix()'s <20 -> 0 band...
    # use a genuinely low VIX (<12) so the score is unambiguous (+1).
    _seed_day(db_engine, d1, adx=30, vix=10, sma_200=100, price=110, regime="TRENDING_BULL")

    backfill_regime_metrics_history(_GATE_CFG)

    df = get_regime_metrics_history()
    assert df.iloc[0]["macro_health_score"] == 1.0


def test_backfill_macro_health_uses_nearest_prior_fred_observation(db_engine):
    """The as-of lookup must pick the latest FRED observation on or before the
    backfilled date — never a later one, which would be look-ahead. Uses the
    real config.yaml (DFII10 is a genuine 'us' series in production config,
    matching tests/test_macro.py's own convention of relying on the real
    region tags rather than re-declaring them)."""
    with Session(db_engine) as session:
        session.add(MacroIndicator(series_id="DFII10", date=datetime(2026, 5, 1),
                                    value=1.0, score=1))
        session.add(MacroIndicator(series_id="DFII10", date=datetime(2026, 6, 15),
                                    value=-1.0, score=-1))  # after the backfilled date — must be ignored
        session.commit()

    d1 = datetime(2026, 6, 1)
    _seed_day(db_engine, d1, adx=30, vix=None, sma_200=100, price=110, regime="TRENDING_BULL")

    cfg = {
        "trading212_invest": {}, "trading212_stocks": {},
        "macro": {"fred_series": {"DFII10": {"region": "us"}}},
    }
    backfill_regime_metrics_history(cfg)

    df = get_regime_metrics_history()
    # Only the 2026-05-01 (score +1) observation should have been visible —
    # composite must not reflect the later, score -1 observation.
    assert df.iloc[0]["macro_health_score"] > 0
