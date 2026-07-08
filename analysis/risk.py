"""Mechanical, non-predictive risk management: drawdown-based position scaling.

Reduces new BUY position sizes automatically while the T212 Invest account is
in drawdown from its own high water mark — no new signal, regime call, or
prediction is involved. This responds to whatever *caused* the drawdown
(a regime transition, a late-cycle bull rollover, an unexpected event) without
needing to identify or predict the cause: it only ever looks at the account's
own equity curve. See CLAUDE.md's "Drawdown-based position scaling" section
for the full rationale and backtest validation.
"""

import logging
from datetime import datetime

from data.database import get_metadata_str, set_metadata

logger = logging.getLogger(__name__)

# Tier order from least to most severe — shared by the live path and the
# backtest's own in-memory hysteresis tracking (analysis/backtest.py) so both
# resolve tiers identically for the same drawdown_pct/config.
_TIER_ORDER = ("NORMAL", "CAUTION", "REDUCED", "MINIMAL")


def update_peak_portfolio_value(current_value: float, config: dict) -> None:
    """Update the account high water mark used by drawdown-based position scaling.

    Called on every scheduler-maintained account snapshot fetch
    (data/scheduler.py:_fetch_t212_snapshot()) so the peak stays current
    without any manual intervention. Also called defensively from
    compute_drawdown_scale_factor() below, since every caller of that
    function already has a genuine, freshly-read account_value_gbp in hand
    (from client.get_account_summary()) — updating the peak there too means
    drawdown scaling degrades gracefully even if the snapshot job hasn't run
    recently, rather than depending on job ordering.

    The peak is a simple, monotonic high water mark — it only ever increases
    (as the account makes new highs) and never decays with time. The
    trading212_invest.drawdown_scaling.lookback_days config key is accepted
    for parity with a true rolling-window peak, but this codebase has no
    persisted time series of portfolio values to reconstruct one from (only
    the current snapshot is ever stored) — implementing a genuine trailing-
    N-day peak would require storing a new history table, which is out of
    scope for this feature. A monotonic all-time high is also the more
    conservative choice for a risk control: it never lets an old high
    "expire" and quietly re-inflate position sizes before the account has
    actually recovered.
    """
    peak_str = get_metadata_str("t212_peak_value_gbp")
    try:
        peak = float(peak_str) if peak_str not in (None, "") else None
    except (TypeError, ValueError):
        peak = None

    if peak is None or current_value > peak:
        set_metadata("t212_peak_value_gbp", str(current_value))
        set_metadata("t212_peak_value_date", datetime.utcnow().isoformat())


def _drawdown_tier_from_pct(drawdown_pct: float, dd_cfg: dict, prior_tier: str) -> tuple[str, float]:
    """Pure core: resolve the drawdown tier + scale factor for *drawdown_pct*.

    No DB access — shared by compute_drawdown_scale_factor() below (live path,
    prior_tier read from/written to system_metadata) and
    analysis/backtest.py:simulate_trades() (prior_tier tracked in-memory per
    ticker, since the backtest has no shared cross-ticker portfolio state —
    see that module's own drawdown-scaling integration note). This is the
    same shared-core pattern used elsewhere in this codebase for VaR sizing
    (a DB-free core plus a DB-aware wrapper), so live and backtested
    drawdown scaling can never quietly drift apart.

    Three tiers rather than a single threshold gives a graduated response —
    position size steps down progressively as drawdown deepens rather than
    a single cliff edge:
      drawdown < threshold_1                       -> NORMAL   (scale 1.0)
      threshold_1 <= drawdown < threshold_2         -> CAUTION  (scale_factor_1)
      threshold_2 <= drawdown < threshold_3         -> REDUCED  (scale_factor_2)
      drawdown >= threshold_3                       -> MINIMAL  (scale_factor_3)

    Hysteresis: escalating to a more severe tier happens immediately (risk
    control should never be slow to react to a deepening drawdown), but
    recovering to a less severe tier only happens once drawdown_pct clears
    that tier's own lower boundary by recovery_buffer — e.g. once CAUTION
    (>= threshold_1) is active, recovery back to NORMAL requires drawdown_pct
    to fall below (threshold_1 - recovery_buffer), not just below
    threshold_1 itself. This prevents rapid oscillation for an account
    sitting right at a threshold.

    Returns (new_tier, scale_factor).
    """
    threshold_1 = float(dd_cfg.get("threshold_1", 0.05))
    threshold_2 = float(dd_cfg.get("threshold_2", 0.10))
    threshold_3 = float(dd_cfg.get("threshold_3", 0.15))
    scale_1 = float(dd_cfg.get("scale_factor_1", 0.75))
    scale_2 = float(dd_cfg.get("scale_factor_2", 0.50))
    scale_3 = float(dd_cfg.get("scale_factor_3", 0.25))
    recovery_buffer = float(dd_cfg.get("recovery_buffer", 0.02))

    boundaries = {"NORMAL": 0.0, "CAUTION": threshold_1, "REDUCED": threshold_2, "MINIMAL": threshold_3}
    scales = {"NORMAL": 1.0, "CAUTION": scale_1, "REDUCED": scale_2, "MINIMAL": scale_3}

    # Raw tier from drawdown_pct alone, ignoring hysteresis — the tier this
    # drawdown would land in with no memory of the prior call.
    escalated = "NORMAL"
    for tier in _TIER_ORDER:
        if drawdown_pct >= boundaries[tier]:
            escalated = tier

    prior_tier = prior_tier if prior_tier in _TIER_ORDER else "NORMAL"
    prior_idx = _TIER_ORDER.index(prior_tier)
    escalated_idx = _TIER_ORDER.index(escalated)

    if escalated_idx >= prior_idx:
        # Drawdown is the same or worse than the prior call — escalate (or
        # stay put) immediately, no hysteresis on the way into more risk.
        new_tier = escalated
    else:
        # Drawdown has improved past the prior tier's raw boundary — step
        # down one tier at a time, only once recovery clears each tier's own
        # lower boundary by recovery_buffer.
        idx = prior_idx
        while idx > 0 and drawdown_pct < (boundaries[_TIER_ORDER[idx]] - recovery_buffer):
            idx -= 1
        new_tier = _TIER_ORDER[idx]

    return new_tier, scales[new_tier]


def compute_drawdown_scale_factor(
    account_value_gbp: float,
    config: dict,
    section: str = "trading212_invest",
) -> tuple[float, float, str]:
    """Compute the position-size scale factor for the account's current drawdown.

    Reads the peak portfolio value from system_metadata (t212_peak_value_gbp
    — a single account-level high water mark shared by both T212 automation
    paths, since trading212_invest and trading212_stocks trade the same
    underlying T212 account), updates it first if *account_value_gbp* is a
    new high, then computes the current drawdown and resolves it to a tier
    via _drawdown_tier_from_pct() above — using config[section]'s own
    threshold/scale-factor values, since trading212_invest and
    trading212_stocks can in principle tune these independently even though
    both ship with identical defaults (matching the existing VaR-sizing
    precedent in this codebase).

    Args:
        account_value_gbp: Current total T212 account value in GBP (from
            client.get_account_summary()) — the same fresh read callers
            already have in hand before calling this.
        config: Loaded config.yaml dict.
        section: "trading212_invest" or "trading212_stocks" — which
            drawdown_scaling block's thresholds/scale factors to apply.

    Returns:
        (scale_factor, current_drawdown_pct, status_label)
        scale_factor: 1.0 (no scaling) down to 0.25 (maximum scaling).
        current_drawdown_pct: current drawdown from peak as a positive
            fraction (0.05 = 5%) — returned regardless of whether scaling
            is enabled, so callers/UI can always display it.
        status_label: "NORMAL" / "CAUTION" / "REDUCED" / "MINIMAL".
    """
    dd_cfg = config.get(section, {}).get("drawdown_scaling", {})

    update_peak_portfolio_value(account_value_gbp, config)

    peak_str = get_metadata_str("t212_peak_value_gbp")
    try:
        peak = float(peak_str) if peak_str not in (None, "") else account_value_gbp
    except (TypeError, ValueError):
        peak = account_value_gbp

    drawdown_pct = max(0.0, (peak - account_value_gbp) / peak) if peak > 0 else 0.0

    if not bool(dd_cfg.get("enabled", False)):
        return 1.0, drawdown_pct, "NORMAL"

    prior_tier = get_metadata_str("t212_drawdown_tier") or "NORMAL"
    new_tier, scale_factor = _drawdown_tier_from_pct(drawdown_pct, dd_cfg, prior_tier)

    if new_tier != prior_tier:
        logger.info(
            "Drawdown scaling: %s -> %s (drawdown %.1f%% from peak £%.0f, scale %.0f%%).",
            prior_tier, new_tier, drawdown_pct * 100, peak, scale_factor * 100,
        )
    set_metadata("t212_drawdown_tier", new_tier)

    return scale_factor, drawdown_pct, new_tier
