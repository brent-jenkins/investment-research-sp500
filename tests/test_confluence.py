"""Tests for the confluence score -> signal classification boundaries.

get_regime_thresholds() and _classify() in analysis/confluence.py are pure
functions over a config dict and a float score — no DB or network access —
so these tests use a small synthetic config rather than the real
config.yaml, keeping them independent of any future threshold tuning there.
"""

from analysis.confluence import _classify, get_regime_thresholds

_CONFIG = {
    "confluence": {
        "regime_thresholds": {
            "MIXED": {"buy_threshold": 0.60, "sell_threshold": 0.60},
            "TRENDING_BULL": {"buy_threshold": 0.55, "sell_threshold": 0.70},
        }
    }
}

_MID = 0.30  # fixed BUY/SELL midpoint boundary, independent of regime


def _thresholds(regime_name: str) -> tuple[float, float]:
    return get_regime_thresholds(regime_name, _CONFIG)


def test_get_regime_thresholds_reads_mixed_and_trending_bull_from_config():
    """Sanity check the fixture config round-trips through get_regime_thresholds()
    before relying on it for the boundary tests below."""
    assert _thresholds("MIXED") == (0.60, 0.60)
    assert _thresholds("TRENDING_BULL") == (0.55, 0.70)


def test_score_at_strong_buy_threshold_classifies_strong_buy_mixed():
    """A score exactly at buy_threshold is STRONG_BUY — the boundary is
    inclusive on the strong side, so this must not fall through to BUY."""
    buy_th, sell_th = _thresholds("MIXED")
    assert _classify(buy_th, buy_th, sell_th) == "STRONG_BUY"


def test_score_just_below_strong_buy_threshold_classifies_buy_mixed():
    """The smallest score below buy_threshold must classify as BUY, not STRONG_BUY."""
    buy_th, sell_th = _thresholds("MIXED")
    assert _classify(buy_th - 0.001, buy_th, sell_th) == "BUY"


def test_score_at_strong_buy_threshold_classifies_strong_buy_trending_bull():
    """Same STRONG_BUY boundary check, but for TRENDING_BULL's looser 0.55 threshold."""
    buy_th, sell_th = _thresholds("TRENDING_BULL")
    assert _classify(buy_th, buy_th, sell_th) == "STRONG_BUY"


def test_score_just_below_strong_buy_threshold_classifies_buy_trending_bull():
    """Same just-below-threshold check for TRENDING_BULL."""
    buy_th, sell_th = _thresholds("TRENDING_BULL")
    assert _classify(buy_th - 0.001, buy_th, sell_th) == "BUY"


def test_score_at_buy_mid_boundary_classifies_buy_not_neutral():
    """The fixed 0.30 BUY/NEUTRAL midpoint belongs to BUY (inclusive on the BUY
    side), mirroring the inclusive-strong convention on the STRONG_BUY boundary."""
    buy_th, sell_th = _thresholds("MIXED")
    assert _classify(_MID, buy_th, sell_th) == "BUY"


def test_score_just_below_buy_mid_boundary_classifies_neutral():
    """Just under the 0.30 midpoint must drop to NEUTRAL."""
    buy_th, sell_th = _thresholds("MIXED")
    assert _classify(_MID - 0.001, buy_th, sell_th) == "NEUTRAL"


def test_score_at_strong_sell_threshold_classifies_sell_not_strong_sell_mixed():
    """Unlike the buy side, the -sell_threshold boundary belongs to SELL, not
    STRONG_SELL — _classify() checks SELL's range before STRONG_SELL's when
    sorted by descending lower bound, so the boundary point matches SELL first."""
    buy_th, sell_th = _thresholds("MIXED")
    assert _classify(-sell_th, buy_th, sell_th) == "SELL"


def test_score_just_past_strong_sell_threshold_classifies_strong_sell_mixed():
    """Just beyond -sell_threshold (more negative) must classify as STRONG_SELL."""
    buy_th, sell_th = _thresholds("MIXED")
    assert _classify(-sell_th - 0.001, buy_th, sell_th) == "STRONG_SELL"


def test_score_at_strong_sell_threshold_classifies_sell_not_strong_sell_trending_bull():
    """Same SELL-vs-STRONG_SELL boundary check for TRENDING_BULL's wider 0.70
    sell threshold (harder to go short in a confirmed bull trend)."""
    buy_th, sell_th = _thresholds("TRENDING_BULL")
    assert _classify(-sell_th, buy_th, sell_th) == "SELL"


def test_score_just_past_strong_sell_threshold_classifies_strong_sell_trending_bull():
    """Same just-past-threshold check for TRENDING_BULL."""
    buy_th, sell_th = _thresholds("TRENDING_BULL")
    assert _classify(-sell_th - 0.001, buy_th, sell_th) == "STRONG_SELL"


def test_zero_score_classifies_neutral_for_both_regimes():
    """A perfectly flat score must always be NEUTRAL regardless of regime."""
    for regime in ("MIXED", "TRENDING_BULL"):
        buy_th, sell_th = _thresholds(regime)
        assert _classify(0.0, buy_th, sell_th) == "NEUTRAL"
