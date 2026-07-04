"""Market regime detection using ^GSPC stored indicators as the reference."""

import logging
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy import text

from data.database import get_engine, get_metadata_str, set_metadata

logger = logging.getLogger(__name__)

# Reference asset for regime detection — must exist in the watchlist
_REFERENCE_TICKER = "^GSPC"


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


class Regime(str, Enum):
    """Four named market regimes, each carrying its own indicator weight profile."""
    TRENDING_BULL   = "TRENDING_BULL"
    TRENDING_BEAR   = "TRENDING_BEAR"
    RANGING_LOW_VOL = "RANGING_LOW_VOL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    MIXED           = "MIXED"


# Human-readable descriptions shown in the UI
REGIME_DESCRIPTIONS: dict[str, str] = {
    Regime.TRENDING_BULL:   "Market trending strongly upward — trend signals carry extra weight.",
    Regime.TRENDING_BEAR:   "Market trending strongly downward — trend and volatility signals dominate.",
    Regime.RANGING_LOW_VOL: "Low-volatility consolidation — mean-reversion signals carry extra weight.",
    Regime.HIGH_VOLATILITY: "Elevated volatility or fear — volatility and macro signals dominate.",
    Regime.MIXED:           "Market conditions are mixed — no regime rule applies cleanly, equal weights used.",
}

# UI accent colours for each regime
REGIME_COLOURS: dict[str, str] = {
    Regime.TRENDING_BULL:   "#26a69a",   # teal / green
    Regime.TRENDING_BEAR:   "#ef5350",   # red
    Regime.RANGING_LOW_VOL: "#6366f1",   # indigo / blue
    Regime.HIGH_VOLATILITY: "#f59e0b",   # amber
    Regime.MIXED:           "#6b7280",   # grey
}

# Module weight profiles — must sum to 1.0 per regime.
# Correlation is a cross-asset confirming signal; it gets 0.10 across all regimes
# with the savings taken from the dominant module(s) in each regime.
REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    Regime.TRENDING_BULL: {
        # Rebalanced 2026-07-04 from 347-trade TRENDING_BULL-only backtest
        # module attribution — see CLAUDE.md's REGIME_WEIGHTS table note
        # for the full rationale. Trend/volume near-
        # universally agree with the LONG direction in this regime (100%
        # and 98.8% agreement respectively) so they confirm the regime but
        # do not discriminate between good and bad entries; mean reversion
        # is almost always contrarian (0.6% agreement) and slightly drags
        # scores down; macro has by far the highest discriminating power
        # (66.7% win rate when agreeing vs. 41.6% when disagreeing, despite
        # only 0.9% agreement) so it is weighted highest.
        "trend":          0.20,  # was 0.30 — 100% agreement, zero discrimination
        "mean_reversion": 0.05,  # was 0.10 — almost always contrarian in trends
        "volatility":     0.20,  # was 0.15 — moderate discriminating power
        "volume":         0.15,  # was 0.20 — 98.8% agreement, low discrimination
        "macro":          0.30,  # was 0.15 — highest discriminating power
        "correlation":    0.10,
    },
    Regime.TRENDING_BEAR: {
        "trend":          0.25,  # was 0.30
        "mean_reversion": 0.15,
        "volatility":     0.20,  # was 0.25
        "volume":         0.15,
        "macro":          0.15,
        "correlation":    0.10,
    },
    Regime.RANGING_LOW_VOL: {
        "trend":          0.10,
        "mean_reversion": 0.35,  # was 0.40
        "volatility":     0.15,
        "volume":         0.15,  # was 0.20
        "macro":          0.15,
        "correlation":    0.10,
    },
    Regime.HIGH_VOLATILITY: {
        "trend":          0.10,
        "mean_reversion": 0.15,
        "volatility":     0.35,  # was 0.40
        "volume":         0.15,
        "macro":          0.15,  # was 0.20
        "correlation":    0.10,
    },
    Regime.MIXED: {
        "trend":          0.18,  # was 0.20
        "mean_reversion": 0.18,
        "volatility":     0.18,
        "volume":         0.18,
        "macro":          0.18,
        "correlation":    0.10,
    },
}


def _detect_regime_rules(details: dict) -> Regime:
    """Apply the priority-ordered ADX/VIX/SMA200 rules to already-fetched
    indicator values. Pure — no DB access — so it can be unit tested and
    reused independently of where ``details`` came from.

    1. HIGH_VOLATILITY  — VIX > 30 (overrides all other conditions)
    2. TRENDING_BULL    — price > SMA200, ADX > 25, VIX < 20
    3. TRENDING_BEAR    — price < SMA200, ADX > 25, VIX > 20
    4. RANGING_LOW_VOL  — ADX < 20, VIX < 15
    5. MIXED            — fallback when indicators are unavailable or ambiguous
    """
    price   = details.get("price")
    sma_200 = details.get("sma_200")
    adx     = details.get("adx")
    vix     = details.get("vix")

    if vix is not None and float(vix) > 30:
        return Regime.HIGH_VOLATILITY
    if (
        price is not None and sma_200 is not None
        and adx is not None and vix is not None
        and float(price) > float(sma_200)
        and float(adx) > 25
        and float(vix) < 20
    ):
        return Regime.TRENDING_BULL
    if (
        price is not None and sma_200 is not None
        and adx is not None and vix is not None
        and float(price) < float(sma_200)
        and float(adx) > 25
        and float(vix) > 20
    ):
        return Regime.TRENDING_BEAR
    if (
        adx is not None and vix is not None
        and float(adx) < 20
        and float(vix) < 15
    ):
        return Regime.RANGING_LOW_VOL
    return Regime.MIXED


def detect_regime() -> dict:
    """Detect the current market regime using stored ^GSPC indicators.

    Two layers, in order:

    1. **Rule-based (primary)** — ``_detect_regime_rules()`` applies the
       priority-ordered ADX/VIX/SMA200 thresholds described there. This is
       always computed and is never overridden except by step 2 below.
    2. **HMM confidence gate (validator, not a replacement)** — when
       ``signals.hmm_enabled`` is true (default), a rule-based
       ``TRENDING_BULL`` call is downgraded to ``MIXED`` if
       ``analysis.hmm_regime.get_hmm_regime_confidence()`` reports a
       bull-state probability below ``signals.hmm_bull_confidence_threshold``
       (default 0.60). Every other rule-based regime is left untouched — the
       HMM is informational only outside TRENDING_BULL. This exists because
       the rule-based thresholds create sharp regime boundaries (ADX 24.9 is
       MIXED, ADX 25.1 is TRENDING_BULL) that a single noisy data point can
       flip; the HMM's continuous state probability smooths that boundary
       by validating whether the broader feature pattern actually looks like
       a genuine bull regime before trusting the rule's call. See
       analysis/hmm_regime.py's module docstring and CLAUDE.md for the full
       design.

    Returns:
        Dict with:
        - ``regime``      — final :class:`Regime` (post-HMM-gate)
        - ``weights``     — module weight dict for this regime
        - ``description`` — human-readable regime explanation
        - ``colour``      — hex accent colour for the UI
        - ``details``     — raw indicator values used for classification
        - ``hmm``         — dict with the full HMM confidence output (see
          analysis.hmm_regime.get_hmm_regime_confidence()) plus
          ``hmm_downgraded`` (bool) and ``rule_regime`` (the pre-gate
          :class:`Regime` name) — empty dict when hmm_enabled is false or
          the HMM check itself failed
    """
    details: dict = {
        "ticker": _REFERENCE_TICKER,
        "price":  None,
        "sma_200": None,
        "adx":    None,
        "vix":    None,
    }

    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT i.sma_200, i.adx, i.vix_level, p.close AS price "
                    "FROM indicators i "
                    "JOIN price_data p ON i.ticker = p.ticker AND i.date = p.date "
                    "WHERE i.ticker = :ticker "
                    "ORDER BY i.date DESC LIMIT 1"
                ),
                {"ticker": _REFERENCE_TICKER},
            ).mappings().first()
    except Exception as exc:
        logger.error("Regime detection DB query failed: %s", exc)
        row = None

    if row is not None:
        details.update({
            "price": row.get("price"), "sma_200": row.get("sma_200"),
            "adx": row.get("adx"), "vix": row.get("vix_level"),
        })

    rule_regime = _detect_regime_rules(details)
    final_regime = rule_regime
    downgraded = False
    hmm_info: dict = {}

    try:
        config = _load_config()
    except Exception as exc:
        logger.warning("Regime detection: could not load config for HMM gate: %s", exc)
        config = {}

    if config.get("signals", {}).get("hmm_enabled", True):
        try:
            from analysis.hmm_regime import get_hmm_regime_confidence  # noqa: PLC0415  local import — avoid a module-load-time dependency on hmmlearn for callers that never enable this feature
            hmm_info = get_hmm_regime_confidence(config)
            threshold = float(config.get("signals", {}).get("hmm_bull_confidence_threshold", 0.60))
            if (
                rule_regime == Regime.TRENDING_BULL
                and hmm_info.get("hmm_model_available")
                and hmm_info.get("hmm_bull_probability") is not None
                and hmm_info["hmm_bull_probability"] < threshold
            ):
                final_regime = Regime.MIXED
                downgraded = True
        except Exception as exc:
            logger.warning("HMM regime confidence check failed: %s", exc)
            hmm_info = {}

    result = _build_result(final_regime, details)
    result["hmm"] = {**hmm_info, "hmm_downgraded": downgraded, "rule_regime": rule_regime.name}
    return result


def get_latest_regime_name() -> Optional[str]:
    """Return the most recently stored regime name for the reference ticker.

    Unlike detect_regime() (which recomputes live from the indicators table),
    this reads the regime that was actually stamped on the signals table by
    the most recent generate_signals() run — the same regime that produced
    the current signal classifications and confluence thresholds. Automation
    that acts on those classifications (T212 Invest orders) should key its
    per-regime entry overrides off this value so the regime used for the
    entry decision matches the regime that scored the signal.

    Returns None if no ^GSPC signal row exists yet or the read fails.
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT COALESCE(regime, notes) AS regime FROM signals "
                    "WHERE ticker = :ticker ORDER BY date DESC LIMIT 1"
                ),
                {"ticker": _REFERENCE_TICKER},
            ).mappings().first()
        if row and row["regime"]:
            return str(row["regime"])
    except Exception as exc:
        logger.warning("Could not read stored regime from signals table: %s", exc)
    return None


def resolve_entry_signal(cfg: dict, regime_name: Optional[str]) -> str:
    """Resolve the effective entry_signal threshold for *cfg*, applying a
    per-regime override from cfg['entry_signal_overrides'] when one exists.

    cfg is the trading212_invest, trading212_stocks, or backtest config
    section — all three use the same entry_signal / entry_signal_overrides
    shape. Falls back to cfg['entry_signal'] (default "STRONG_BUY") when
    regime_name is None or has no override entry.
    """
    default = cfg.get("entry_signal", "STRONG_BUY")
    overrides = cfg.get("entry_signal_overrides") or {}
    if regime_name and regime_name in overrides:
        return overrides[regime_name]
    return default


# Bond ETF tickers that use the bonds max-holding-days limit (checked after
# ucits_mapping and us_large_cap below, since several of these — TLT, IEF,
# HYG, LQD, TIP, EMB, MUB — are also ETF automation instruments and should
# use the ETF backstop instead; SHY and JNK have no ucits_mapping entry and
# fall through to this bucket).
_BOND_TICKERS: frozenset[str] = frozenset(
    {"TLT", "IEF", "SHY", "TIP", "LQD", "JNK", "EMB", "MUB", "HYG"}
)


def get_max_holding_days(ticker: str, config: dict) -> int:
    """Return the max holding days for *ticker*, matching live automation limits.

    Priority order (first match wins):
      1. ticker in trading212_invest.ucits_mapping → trading212_invest.max_holding_days
         (ETF backstop, e.g. 90 — no leverage/swap costs, held longer)
      2. ticker in watchlist.us_large_cap           → trading212_stocks.max_holding_days
         (individual-stock backstop, e.g. 30 — higher idiosyncratic risk than an ETF)
      3. ticker in the bond-ETF set                 → backtest.max_holding_days_bonds
         (e.g. 45 — slow-moving, longer trend duration)
      4. ticker ends with '=X'   (forex)             → backtest.max_holding_days_forex
         (e.g. 5 — swap costs make long holds expensive)
      5. ticker ends with '-USD' (crypto)            → backtest.max_holding_days_crypto
         (e.g. 14 — high volatility, shorter cycles)
      6. everything else                             → backtest.max_holding_days (default)

    This is the single source of truth for per-asset holding limits, used by
    simulate_trades() (analysis/backtest.py) and by the live T212 exit checks
    and trade-alert job (data/scheduler.py) so backtested and live holding
    limits always agree for the same ticker.
    """
    bt_cfg = config.get("backtest", {})
    default = int(bt_cfg.get("max_holding_days", 30))

    ucits_mapping = config.get("trading212_invest", {}).get("ucits_mapping") or {}
    if ticker in ucits_mapping:
        return int(config.get("trading212_invest", {}).get("max_holding_days", default))

    us_large_cap = config.get("watchlist", {}).get("us_large_cap") or []
    if any(isinstance(e, dict) and e.get("ticker") == ticker for e in us_large_cap):
        return int(config.get("trading212_stocks", {}).get("max_holding_days", default))

    if ticker in _BOND_TICKERS:
        return int(bt_cfg.get("max_holding_days_bonds", default))
    if ticker.endswith("=X"):
        return int(bt_cfg.get("max_holding_days_forex", default))
    if ticker.endswith("-USD"):
        return int(bt_cfg.get("max_holding_days_crypto", default))
    return default


def is_entry_allowed(config: dict, regime_name: Optional[str], section: str) -> bool:
    """Return True if new entries are allowed in *regime_name* for config[section].

    Reads config[section]["allowed_entry_regimes"] — a regime not in that list
    is blocked entirely (checked before resolve_entry_signal(); a blocked
    regime never reaches the signal-threshold check at all). If the key is
    absent from config[section], every regime is allowed — this is the
    backwards-compatible default for configs written before this gate existed.

    section is "trading212_invest", "trading212_stocks", or "backtest" — all
    three use the same allowed_entry_regimes shape (currently kept in sync at
    ["TRENDING_BULL"] across all three — see config.yaml for the per-regime
    backtest rationale). This gate only affects new entries; existing positions
    always continue to exit normally regardless of regime.
    """
    section_cfg = config.get(section, {})
    allowed = section_cfg.get("allowed_entry_regimes")
    if allowed is None:
        return True
    return regime_name in allowed


def detect_overlays() -> dict[str, bool]:
    """Check stored correlations for significant pair divergences that signal a regime overlay.

    Reads the most recent row for each key pair from the ``correlations`` table.
    Returns immediately with all-False if no correlation data exists yet (fresh install).

    Overlay definitions:
    - ``MACRO_STRESS``:      TLT/^GSPC DIVERGING (z ≥ 1.5) — bonds and equities moving together,
                             breaking the traditional flight-to-safety relationship.
    - ``VOL_REGIME_SHIFT``:  ^VIX/^GSPC DIVERGING (z ≥ 1.5) — VIX and the market moving in the
                             same direction, signalling an atypical fear-gauge environment.

    Results are stored in ``system_metadata`` under ``active_overlays`` (comma-separated list
    of active keys, or ``none``) so the Signals page can display them without re-querying.

    Returns:
        Dict mapping overlay name → bool (True = currently active).
    """
    overlays: dict[str, bool] = {
        "MACRO_STRESS":     False,
        "VOL_REGIME_SHIFT": False,
        "CREDIT_STRESS":    False,
        "RECESSION_RISK":   False,
        "EM_STRESS":        False,
    }

    _PAIR_OVERLAY = [
        ("TLT",  "^GSPC", "MACRO_STRESS"),
        ("^VIX", "^GSPC", "VOL_REGIME_SHIFT"),
    ]

    try:
        engine = get_engine()
        with engine.connect() as conn:
            for ticker_a, ticker_b, overlay_key in _PAIR_OVERLAY:
                row = conn.execute(
                    text(
                        "SELECT divergence, z_score FROM correlations "
                        "WHERE ticker_a = :a AND ticker_b = :b "
                        "ORDER BY date DESC LIMIT 1"
                    ),
                    {"a": ticker_a, "b": ticker_b},
                ).mappings().first()

                if row and row["divergence"] == "DIVERGING":
                    z = abs(float(row["z_score"] or 0))
                    if z >= 1.5:
                        overlays[overlay_key] = True
                        logger.info(
                            "Overlay %s active: %s/%s z=%.2f",
                            overlay_key, ticker_a, ticker_b, z,
                        )
    except Exception as exc:
        logger.warning("Overlay detection failed: %s", exc)

    # CREDIT_STRESS: HYG underperforming TLT by > 1 standard deviation.
    # The spread z-score is computed in compute_all_pairs() (which runs before
    # generate_signals()) and stored in system_metadata to avoid a full
    # price-data recalculation here.
    try:
        spread_z_str = get_metadata_str("hyg_tlt_spread_z")
        if spread_z_str is not None:
            spread_z = float(spread_z_str)
            if spread_z < -1.0:
                overlays["CREDIT_STRESS"] = True
                logger.info(
                    "Overlay CREDIT_STRESS active: HYG/TLT return-spread z=%.2f", spread_z
                )
    except Exception as exc:
        logger.warning("CREDIT_STRESS detection failed: %s", exc)

    # RECESSION_RISK: CFNAI below recession threshold for 2+ consecutive months
    try:
        engine = get_engine()
        with engine.connect() as conn:
            cfnai_rows = conn.execute(
                text(
                    "SELECT value FROM macro_indicators WHERE series_id = 'CFNAI' "
                    "ORDER BY date DESC LIMIT 2"
                )
            ).fetchall()
            if len(cfnai_rows) >= 2 and all(float(r[0]) < -0.70 for r in cfnai_rows):
                overlays["RECESSION_RISK"] = True
                logger.info("Overlay RECESSION_RISK active: CFNAI < -0.70 for 2+ months")
    except Exception as exc:
        logger.warning("RECESSION_RISK detection failed: %s", exc)

    # EM_STRESS: trade-weighted dollar surging > 3% in 30 days
    try:
        engine = get_engine()
        with engine.connect() as conn:
            dtwex_row = conn.execute(
                text(
                    "SELECT pct_change_30d FROM macro_indicators WHERE series_id = 'DTWEXBGS' "
                    "ORDER BY date DESC LIMIT 1"
                )
            ).mappings().first()
            if dtwex_row and dtwex_row["pct_change_30d"] is not None:
                if float(dtwex_row["pct_change_30d"]) > 3.0:
                    overlays["EM_STRESS"] = True
                    logger.info(
                        "Overlay EM_STRESS active: DTWEXBGS 30d change %.2f%%",
                        float(dtwex_row["pct_change_30d"]),
                    )
    except Exception as exc:
        logger.warning("EM_STRESS detection failed: %s", exc)

    # Persist so the UI can display without recomputing
    active_str = ",".join(k for k, v in overlays.items() if v)
    try:
        set_metadata("active_overlays", active_str or "none")
    except Exception as exc:
        logger.warning("Could not store active_overlays: %s", exc)

    return overlays


# Plain-English descriptions shown on the Signals page
OVERLAY_DESCRIPTIONS: dict[str, dict] = {
    "MACRO_STRESS": {
        "label":  "Macro Stress",
        "colour": "#dc2626",
        "text": (
            "Long bonds (TLT) and equities (S&P 500) are moving together — this breaks "
            "the traditional flight-to-safety relationship where bonds rise when equities fall. "
            "Historically unusual and often precedes a macro regime shift. "
            "Buy signal confidence is reduced by 20% for all equity and bond assets."
        ),
    },
    "VOL_REGIME_SHIFT": {
        "label":  "Vol Regime Shift",
        "colour": "#f59e0b",
        "text": (
            "VIX and the S&P 500 are moving in the same direction — normally they are "
            "inversely correlated (VIX spikes when equities fall). This atypical behaviour "
            "suggests the fear gauge is mis-calibrated or that a structural shift is underway. "
            "Treat directional signals with extra caution."
        ),
    },
    "CREDIT_STRESS": {
        "label":  "Credit Stress",
        "colour": "#7c3aed",
        "text": (
            "High-yield bonds (HYG) are underperforming long Treasuries (TLT) by more than "
            "one standard deviation — a widening credit spread that historically precedes "
            "equity market stress. Falling junk bond prices relative to safe-haven bonds "
            "signal rising default-risk perception in the credit market. "
            "Buy signal confidence is reduced by 15% across all equity assets."
        ),
    },
    "RECESSION_RISK": {
        "label":  "Recession Risk",
        "colour": "#b91c1c",
        "text": (
            "The Chicago Fed National Activity Index (CFNAI) has fallen below -0.70 for two "
            "consecutive months — a historically reliable recession signal. A sustained reading "
            "below this threshold indicates broad-based economic deterioration across the 85 "
            "component indicators. Buy signal confidence is reduced by 25% for all equity assets."
        ),
    },
    "EM_STRESS": {
        "label":  "EM Stress",
        "colour": "#b45309",
        "text": (
            "The trade-weighted US dollar (DTWEXBGS) has surged more than 3% in 30 days. "
            "A rapidly strengthening dollar tightens financial conditions in emerging markets, "
            "increases the cost of USD-denominated debt, and typically weighs on EM equities "
            "and currencies. Buy signal confidence is reduced by 15% for EM assets."
        ),
    },
}


def _build_result(regime: Regime, details: dict) -> dict:
    return {
        "regime":      regime,
        "weights":     REGIME_WEIGHTS[regime],
        "description": REGIME_DESCRIPTIONS[regime],
        "colour":      REGIME_COLOURS[regime],
        "details":     details,
    }
