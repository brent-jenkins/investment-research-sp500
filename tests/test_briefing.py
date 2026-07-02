"""Tests for output/briefing.py:_generate_daily_narrative() — the rule-based
plain-English market narrative shown at the top of the Daily Briefing page.

This is a pure function of its arguments (no DB access, no network calls,
no Anthropic/Claude API calls), so these are plain unit tests with no
fixtures required.
"""

import pytest

from output.briefing import _generate_daily_narrative


def _regime(name: str, *, adx=19.9, vix=16.6, price=5000.0, sma_200=4900.0) -> dict:
    return {
        "regime": name,
        "details": {"price": price, "sma_200": sma_200, "adx": adx, "vix": vix},
        "description": "",
        "colour": "#000000",
    }


def _macro_data(**overrides) -> dict:
    base = {
        "vix": 16.6,
        "yield_spread": 0.3,
        "yield_regime": "normal",
        "dollar_trend": "flat",
        "dollar_pct": 0.1,
        "has_data": True,
        "composite_score": 0.1,
        "n_bullish": 8,
        "n_bearish": 3,
        "n_neutral": 3,
        "n_series": 14,
        "real_yield": 1.8,
        "real_yield_trend": "flat",
        "breakeven": 2.3,
        "breakeven_trend": "flat",
        "credit_status": "NORMAL",
        "fed_rate": 4.5,
        "fed_rate_trend": "unchanged",
        "extra_series": [
            {"series_id": "CFNAI", "display_value": "-0.10", "trend": "falling", "context": "Below-trend growth", "score": 0},
        ],
    }
    base.update(overrides)
    return base


def _signals(**overrides) -> dict:
    base = {
        "counts": {"STRONG BUY": 5, "BUY": 10, "NEUTRAL": 60, "SELL": 8, "STRONG SELL": 2},
        "tradeable_counts": {"STRONG BUY": 3, "BUY": 6, "NEUTRAL": 30, "SELL": 4, "STRONG SELL": 1},
        "total": 85,
        "tradeable_total": 44,
    }
    base.update(overrides)
    return base


_CONFIG_ALL_TRENDING_BULL_ONLY = {
    "trading212_invest":  {"enabled": True, "allowed_entry_regimes": ["TRENDING_BULL"]},
    "trading212_stocks":  {"enabled": True, "allowed_entry_regimes": ["TRENDING_BULL"]},
    "virtual_trading":    {"enabled": True, "allowed_entry_regimes": ["TRENDING_BULL"]},
}


def test_mixed_regime_produces_mixed_or_unclear_narrative():
    html_out = _generate_daily_narrative(
        _regime("MIXED", adx=19.9, vix=16.6),
        _macro_data(),
        [],
        _signals(),
        set(),
        [],
        _CONFIG_ALL_TRENDING_BULL_ONLY,
    )
    lowered = html_out.lower()
    assert "mixed" in lowered or "unclear" in lowered


def test_diverging_pair_above_threshold_is_named_in_narrative():
    relationships = [
        {
            "label": "Gold vs Dollar",
            "divergence": "DIVERGING",
            "z_score": 2.10,
            "corr_30d": 0.42,
            "diverge_template": "Gold and dollar moving together — safe-haven/dollar relationship has broken down",
            "confirm_template": "Gold/dollar inverse relationship is intact",
        },
        {
            "label": "VIX vs S&P 500",
            "divergence": "CONFIRMING",
            "z_score": 0.2,
            "corr_30d": -0.7,
            "diverge_template": "",
            "confirm_template": "VIX/equity inverse relationship is intact",
        },
    ]
    html_out = _generate_daily_narrative(
        _regime("MIXED"),
        _macro_data(),
        relationships,
        _signals(),
        set(),
        [],
        _CONFIG_ALL_TRENDING_BULL_ONLY,
    )
    assert "Gold vs Dollar" in html_out
    assert "2.10" in html_out


def test_trending_bull_no_overlays_is_actively_entering():
    html_out = _generate_daily_narrative(
        _regime("TRENDING_BULL", adx=32.0, vix=14.0),
        _macro_data(),
        [],
        _signals(),
        set(),
        [],
        _CONFIG_ALL_TRENDING_BULL_ONLY,
    )
    lowered = html_out.lower()
    assert "actively entering" in lowered


def test_recession_risk_overlay_is_mentioned():
    html_out = _generate_daily_narrative(
        _regime("MIXED"),
        _macro_data(),
        [],
        _signals(),
        {"RECESSION_RISK"},
        [],
        _CONFIG_ALL_TRENDING_BULL_ONLY,
    )
    lowered = html_out.lower()
    assert "recession" in lowered


@pytest.mark.parametrize(
    "regime_name",
    ["TRENDING_BULL", "TRENDING_BEAR", "RANGING_LOW_VOL", "HIGH_VOLATILITY", "MIXED"],
)
def test_returns_non_empty_string_for_every_regime(regime_name):
    html_out = _generate_daily_narrative(
        _regime(regime_name),
        _macro_data(),
        [],
        _signals(),
        set(),
        [],
        _CONFIG_ALL_TRENDING_BULL_ONLY,
    )
    assert isinstance(html_out, str)
    assert html_out.strip() != ""


def test_narrative_never_claims_entering_when_regime_is_blocked():
    """TRENDING_BEAR is not in any path's allowed_entry_regimes in the shared
    config — the narrative must say entries are blocked, not that the system
    is actively entering, so it never contradicts the status bar's own
    "⛔ Entries blocked" caption for the same regime."""
    html_out = _generate_daily_narrative(
        _regime("TRENDING_BEAR", adx=30.0, vix=25.0),
        _macro_data(),
        [],
        _signals(),
        set(),
        [],
        _CONFIG_ALL_TRENDING_BULL_ONLY,
    )
    lowered = html_out.lower()
    assert "actively entering" not in lowered
    assert "not opening new positions" in lowered


def test_open_trade_status_breakdown_is_reflected():
    open_trades = [
        {"status": "ON TRACK"},
        {"status": "ON TRACK"},
        {"status": "WEAKENING"},
    ]
    html_out = _generate_daily_narrative(
        _regime("MIXED"),
        _macro_data(),
        [],
        _signals(),
        set(),
        open_trades,
        _CONFIG_ALL_TRENDING_BULL_ONLY,
    )
    lowered = html_out.lower()
    assert "3 existing position" in lowered or "3 existing positions" in lowered
    assert "on track" in lowered
    assert "weakening" in lowered


def test_no_external_calls_no_db_no_scheduler_side_effects():
    """Purely a smoke test that the function is a pure computation over its
    arguments — asserting the absence of any DB/network dependency by simply
    calling it with no patched infrastructure at all and confirming it
    returns cleanly."""
    result = _generate_daily_narrative(
        _regime("RANGING_LOW_VOL", adx=15.0, vix=12.0),
        _macro_data(composite_score=-0.4, n_bullish=2, n_series=14),
        [],
        _signals(),
        set(),
        [],
        {},
    )
    assert isinstance(result, str) and result
