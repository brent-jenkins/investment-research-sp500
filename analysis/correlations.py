"""Cross-asset correlation analysis: tracks relationship divergences between asset pairs."""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import get_engine, set_metadata
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)


# Predefined cross-asset relationships with their expected correlation direction.
# expected_sign: +1 = positive correlation expected, -1 = inverse correlation expected.
RELATIONSHIP_MAP: list[dict] = [
    {
        "ticker_a": "GLD",
        "ticker_b": "UUP",
        "expected_sign": -1,
        "label": "Gold vs Dollar",
        "desc": "inverse — gold rises when dollar falls",
        "diverge_template": "Gold and dollar moving together — safe-haven/dollar relationship has broken down",
        "confirm_template": "Gold/dollar inverse relationship is intact",
    },
    {
        "ticker_a": "USO",
        "ticker_b": "XLE",
        "expected_sign": 1,
        "label": "Oil vs Energy Stocks",
        "desc": "positive — oil price should lead energy stocks",
        "diverge_template": "Oil rising but energy stocks lagging — potential catch-up opportunity in energy stocks",
        "confirm_template": "Oil and energy stocks tracking together as expected",
    },
    {
        "ticker_a": "CPER",
        "ticker_b": "^GSPC",
        "expected_sign": 1,
        "label": "Copper vs S&P 500",
        "desc": "positive — copper leads economic growth expectations",
        "diverge_template": "Copper and equities diverging — economic growth signal is mixed",
        "confirm_template": "Copper confirming equity market growth signal",
    },
    {
        "ticker_a": "HYG",
        "ticker_b": "^GSPC",
        "expected_sign": 1,
        "label": "High Yield Bonds vs S&P 500",
        "desc": "positive — credit stress precedes equity stress",
        "diverge_template": "Credit and equities diverging — credit market may be signalling stress ahead",
        "confirm_template": "Credit market tracking equities — no stress signals from bonds",
    },
    {
        "ticker_a": "^VIX",
        "ticker_b": "^GSPC",
        "expected_sign": -1,
        "label": "VIX vs S&P 500",
        "desc": "inverse — volatility spikes when the market falls",
        "diverge_template": "VIX and equities moving in the same direction — unusual divergence in fear gauge",
        "confirm_template": "VIX/equity inverse relationship is intact",
    },
    {
        "ticker_a": "TLT",
        "ticker_b": "^GSPC",
        "expected_sign": -1,
        "label": "Long Bonds vs S&P 500",
        "desc": "varies by regime — flight to safety in risk-off environments",
        "diverge_template": "Bonds and equities moving together — the traditional flight-to-safety relationship has broken down; historically unusual and may signal a macro regime shift",
        "confirm_template": "Bond/equity relationship consistent with current risk regime",
    },
    {
        "ticker_a": "GBPUSD=X",
        "ticker_b": "^FTSE",
        "expected_sign": -1,
        "label": "GBP/USD vs FTSE 100",
        "desc": "inverse — strong pound hurts FTSE exporters",
        "diverge_template": "Pound and FTSE moving together — exporter/currency relationship has weakened",
        "confirm_template": "GBP/FTSE inverse exporter-currency relationship is intact",
    },
    {
        "ticker_a": "EURUSD=X",
        "ticker_b": "^GSPC",
        "expected_sign": 1,
        "label": "EUR/USD vs S&P 500",
        "desc": "contextual — dollar strength affects S&P multinational earnings",
        "diverge_template": "EUR/USD and S&P 500 diverging — dollar dynamics may be distorting earnings expectations",
        "confirm_template": "EUR/USD and equities moving in tandem",
    },
    {
        "ticker_a": "BTC-USD",
        "ticker_b": "^GSPC",
        "expected_sign": 1,
        "label": "Bitcoin vs S&P 500",
        "desc": "positive in risk-on, decorrelates in stress",
        "diverge_template": "Bitcoin and equities diverging — crypto-specific event or risk-appetite break",
        "confirm_template": "Bitcoin tracking equities as expected in current risk environment",
    },
    {
        "ticker_a": "SLV",
        "ticker_b": "GLD",
        "expected_sign": 1,
        "label": "Silver vs Gold",
        "desc": "positive — silver follows gold with higher beta",
        "diverge_template": "Silver and gold diverging — industrial demand may be distorting the metals relationship",
        "confirm_template": "Silver tracking gold as expected",
    },
    # ── New pairs added to expand macro coverage ─────────────────────────────
    {
        "ticker_a": "TLT",
        "ticker_b": "GLD",
        "expected_sign": 1,
        "label": "Long Bonds vs Gold",
        "desc": "positive — both benefit from falling real yields",
        "diverge_template": (
            "Long bonds and gold diverging — shift in rate expectations or a change in what "
            "is driving gold (inflation vs rate vs safe-haven demand); one leg of the real-yield "
            "trade is out of sync"
        ),
        "confirm_template": "Bonds and gold moving together — consistent with a falling real-yield environment",
    },
    {
        "ticker_a": "USO",
        "ticker_b": "UNG",
        "expected_sign": 1,
        "label": "Oil vs Natural Gas",
        "desc": "positive — oil and gas usually move together on broad energy demand",
        "diverge_template": (
            "Oil and natural gas prices diverging — energy mix shift underway; supply or demand "
            "dynamics are specific to one fuel (LNG trade flows, pipeline constraints, or "
            "weather-driven gas demand)"
        ),
        "confirm_template": "Oil and natural gas tracking together — broad energy demand signals are aligned",
    },
    {
        "ticker_a": "XLF",
        "ticker_b": "TLT",
        "expected_sign": -1,
        "label": "Financials vs Long Bonds",
        "desc": "inverse — banks benefit from rising rates (falling TLT); hurt when rates fall",
        "diverge_template": (
            "Financials and long bonds moving together — rate sensitivity is breaking down; "
            "possible systemic concern overriding normal bank margin dynamics or a credit event "
            "where both bonds and bank stocks fall simultaneously"
        ),
        "confirm_template": (
            "Financial stocks and long bonds inversely correlated — rate outlook is driving "
            "bank valuations normally"
        ),
    },
    {
        "ticker_a": "XLK",
        "ticker_b": "^VIX",
        "expected_sign": -1,
        "label": "Technology vs Volatility",
        "desc": "inverse — tech is most sensitive to risk appetite; VIX spikes hit high-beta growth hardest",
        "diverge_template": (
            "Technology stocks and volatility moving in the same direction — tech is losing its "
            "growth-premium sensitivity, or a volatility spike is not triggering the usual "
            "rotation out of high-duration growth stocks"
        ),
        "confirm_template": (
            "Technology and VIX inversely correlated — risk appetite is driving tech valuations "
            "as expected"
        ),
    },
    {
        "ticker_a": "XLU",
        "ticker_b": "TLT",
        "expected_sign": 1,
        "label": "Utilities vs Long Bonds",
        "desc": "positive — utilities act like bonds; yield-seeking investors rotate between them",
        "diverge_template": (
            "Utilities and long bonds diverging — yield-seeking rotation is disrupted; utilities "
            "may be pricing in sector-specific headwinds (regulation, capex) or bonds are moving "
            "on macro factors that haven't reached equities yet"
        ),
        "confirm_template": (
            "Utilities tracking long bonds as expected — yield-sensitive sector behaving normally "
            "as a bond proxy"
        ),
    },
    {
        "ticker_a": "GLD",
        "ticker_b": "^GSPC",
        "expected_sign": -1,
        "label": "Gold vs S&P 500",
        "desc": "inverse — gold/equity ratio is the classic risk-off indicator",
        "diverge_template": (
            "Gold and equities moving together — the traditional risk-off role of gold is "
            "suspended; may signal a macro regime with no clear safe-harbour assets, or gold "
            "is being driven by currency debasement rather than fear"
        ),
        "confirm_template": "Gold and S&P 500 inversely correlated — risk-off/risk-on dynamic is intact",
    },
    {
        "ticker_a": "EEM",
        "ticker_b": "UUP",
        "expected_sign": -1,
        "label": "Emerging Markets vs Dollar",
        "desc": "inverse — strong dollar raises EM debt costs and weighs on commodity exporters",
        "diverge_template": (
            "Emerging markets and dollar moving together — strong dollar is not suppressing EM "
            "equities as expected; possible commodity tailwind or capital inflow offsetting the "
            "dollar headwind"
        ),
        "confirm_template": (
            "Emerging markets inversely tracking the dollar — dollar strength/weakness is "
            "driving EM equities as expected"
        ),
    },
    {
        "ticker_a": "HYG",
        "ticker_b": "TLT",
        "expected_sign": -1,
        "label": "High Yield vs Long Bonds",
        "desc": "inverse — credit spread proxy; HYG falling vs TLT rising = credit stress building",
        "diverge_template": (
            "High yield and long bonds moving together — credit spreads are compressing "
            "atypically, or both are falling simultaneously (a CREDIT_STRESS signal: HYG "
            "underperforming in a rising-rate environment)"
        ),
        "confirm_template": (
            "High yield and long bonds inversely correlated — credit spreads behaving normally "
            "relative to rate moves"
        ),
    },
    {
        "ticker_a": "BTC-USD",
        "ticker_b": "GLD",
        "expected_sign": 1,
        "label": "Bitcoin vs Gold",
        "desc": "positive loosely — both treated as inflation hedges; decorrelates in stress",
        "diverge_template": (
            "Bitcoin and gold decorrelating — one is acting as an inflation hedge while the "
            "other is not; often driven by crypto-specific regulatory events or when gold is "
            "geopolitical- rather than monetary-driven"
        ),
        "confirm_template": (
            "Bitcoin and gold tracking together — both acting as inflation hedges or alternative "
            "stores of value"
        ),
    },
    {
        "ticker_a": "XLE",
        "ticker_b": "USO",
        "expected_sign": 1,
        "label": "Energy Stocks vs Oil",
        "desc": "positive — energy stocks should track oil; XLE/USO ratio signals relative value",
        "diverge_template": (
            "Energy stocks and crude oil diverging — XLE/USO ratio has shifted; energy equities "
            "may be cheap relative to oil price (catch-up opportunity) or structural headwinds "
            "(energy transition, capex concerns) are overriding the commodity signal"
        ),
        "confirm_template": (
            "Energy stocks tracking oil price as expected — commodity signal is feeding through "
            "to equity valuations"
        ),
    },
    # ── New pairs: expanded coverage ─────────────────────────────────────────
    {
        "ticker_a": "SOXX",
        "ticker_b": "^GSPC",
        "expected_sign": 1,
        "label": "Semiconductors vs S&P 500",
        "desc": "positive — semiconductors are a leading indicator for the broader market",
        "diverge_template": (
            "Semiconductors and equities diverging — chip stocks are not confirming the market "
            "move; either a sector-specific headwind (inventory cycles, export controls, capex "
            "slowdown) or a leading warning that broad market momentum is fading"
        ),
        "confirm_template": (
            "Semiconductors tracking equities — chip stocks are confirming broad market direction "
            "as expected"
        ),
    },
    {
        "ticker_a": "KRE",
        "ticker_b": "TLT",
        "expected_sign": -1,
        "label": "Regional Banks vs Long Bonds",
        "desc": "inverse — regional banks benefit from rising rates (falling TLT); hurt by falling rates",
        "diverge_template": (
            "Regional banks and long bonds moving together — rate sensitivity is breaking down; "
            "a simultaneous fall could signal credit quality concerns overriding normal net "
            "interest margin dynamics in smaller banks"
        ),
        "confirm_template": (
            "Regional banks inversely tracking long bonds — rate outlook is driving bank "
            "valuations normally"
        ),
    },
    {
        "ticker_a": "VNQ",
        "ticker_b": "TLT",
        "expected_sign": 1,
        "label": "REITs vs Long Bonds",
        "desc": "positive — REITs act as bond proxies; both benefit from falling rates",
        "diverge_template": (
            "REITs and long bonds diverging — the yield-seeking bond-proxy relationship has "
            "broken down; REITs may be facing property-specific headwinds (vacancy rates, "
            "refinancing risk) that bonds do not share"
        ),
        "confirm_template": (
            "REITs tracking long bonds as expected — both responding to rate expectations "
            "in tandem"
        ),
    },
    {
        "ticker_a": "FXI",
        "ticker_b": "UUP",
        "expected_sign": -1,
        "label": "China Equities vs Dollar",
        "desc": "inverse — strong dollar raises USD-denominated costs for China; yuan weakness weighs on Chinese assets",
        "diverge_template": (
            "Chinese equities and the dollar moving together — the typical dollar headwind "
            "is not suppressing FXI; possible stimulus-driven domestic demand or a commodity "
            "tailwind offsetting dollar pressure"
        ),
        "confirm_template": (
            "China equities inversely tracking the dollar — dollar strength/weakness is "
            "flowing through to Chinese asset prices as expected"
        ),
    },
    {
        "ticker_a": "EMB",
        "ticker_b": "UUP",
        "expected_sign": -1,
        "label": "EM Bonds vs Dollar",
        "desc": "inverse — strong dollar raises USD-denominated debt costs for emerging market sovereigns",
        "diverge_template": (
            "EM bonds and the dollar moving together — dollar strength is not widening EM "
            "spreads as expected; possible commodity tailwind or credit quality improvement "
            "offsetting the currency headwind in EM sovereign debt"
        ),
        "confirm_template": (
            "EM bonds inversely tracking the dollar — USD strength/weakness is driving "
            "emerging market bond prices as expected"
        ),
    },
]

# ---------------------------------------------------------------------------
# Catch-up opportunity configuration
# ---------------------------------------------------------------------------

# Pairs where a divergence implies one asset is lagging the other and should
# catch up.  Each entry maps (ticker_a, ticker_b) — matching RELATIONSHIP_MAP
# order — to a config dict describing who leads, who lags, and the trigger.
CATCH_UP_MAP: dict[tuple[str, str], dict] = {
    ("USO", "XLE"): {
        "lead": "USO",
        "lag":  "XLE",
        "direction": "positive",        # both should move in same direction
        "min_return_gap": 0.03,         # lead must outperform lag by ≥3% over 30d
        "description": (
            "Oil has outpaced energy stocks — XLE has not yet reflected USO's recent gains. "
            "Energy stock prices typically follow crude oil within days to weeks."
        ),
    },
    ("GLD", "UUP"): {
        "lead": "UUP",
        "lag":  "GLD",
        "direction": "inverse",         # they should move inversely
        "min_lead_move": 0.02,          # UUP must have fallen ≥2% over 30d
        "description": (
            "Dollar weakness has not yet been reflected in Gold's price — "
            "GLD typically rises when UUP declines, and the relationship has "
            "diverged from historical norms."
        ),
    },
    ("SLV", "GLD"): {
        "lead": "GLD",
        "lag":  "SLV",
        "direction": "positive",
        "min_return_gap": 0.03,
        "description": (
            "Gold has outpaced Silver — SLV has not yet caught up with GLD's recent move. "
            "Silver typically follows gold with higher beta, suggesting a catch-up ahead."
        ),
    },
    ("XLF", "TLT"): {
        "lead": "TLT",
        "lag":  "XLF",
        "direction": "inverse",         # TLT falls (rates rise) → XLF should rise
        "min_lead_move": 0.03,
        "description": (
            "Rate expectations have shifted: TLT has fallen (yields rising) but financial "
            "stocks haven't fully reflected the benefit yet. XLF typically expands net "
            "interest margins in rising-rate environments — catch-up ahead."
        ),
    },
    ("XLU", "TLT"): {
        "lead": "TLT",
        "lag":  "XLU",
        "direction": "positive",        # TLT rises (rates fall) → XLU should also rise
        "min_return_gap": 0.03,
        "description": (
            "Long bonds (TLT) have rallied but utilities (XLU) haven't fully reflected the "
            "yield decline. Utilities typically track bond prices as a yield-seeking "
            "alternative — rotation into XLU may follow as income investors rebalance."
        ),
    },
    ("EEM", "UUP"): {
        "lead": "UUP",
        "lag":  "EEM",
        "direction": "inverse",         # UUP falls (dollar weakens) → EEM should rise
        "min_lead_move": 0.02,
        "description": (
            "The dollar (UUP) has weakened but emerging market equities (EEM) haven't caught "
            "up yet. Dollar weakness historically boosts EM by reducing USD-denominated debt "
            "costs and lifting commodity prices — catch-up opportunity in EEM."
        ),
    },
    ("XLE", "USO"): {
        "lead": "USO",
        "lag":  "XLE",
        "direction": "positive",        # USO rises (oil price up) → XLE should follow
        "min_return_gap": 0.03,
        "description": (
            "Oil (USO) has outpaced energy stocks (XLE) — the XLE/USO price ratio is below "
            "its historical norm, suggesting energy equities are underpriced relative to the "
            "commodity. Sector-level catch-up typically follows within weeks as earnings "
            "revisions respond to sustained oil prices."
        ),
    },
}

# All unique tickers referenced across the relationship map
_ALL_PAIR_TICKERS: frozenset[str] = frozenset(
    t for pair in RELATIONSHIP_MAP for t in (pair["ticker_a"], pair["ticker_b"])
)


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _compute_pair(
    ticker_a: str,
    ticker_b: str,
    expected_sign: int,
    days: int = 365,
) -> dict:
    """Compute rolling correlation metrics for a single asset pair.

    Args:
        ticker_a:      First ticker symbol.
        ticker_b:      Second ticker symbol.
        expected_sign: +1 (positive relationship expected) or -1 (inverse).
        days:          Calendar days of price history to fetch.

    Returns:
        Dict containing corr_30d, corr_90d, hist_mean, hist_std, z_score,
        divergence (CONFIRMING/DIVERGING/NEUTRAL), and pair_score (-1/0/1).
        All float fields are None when data is insufficient.
    """
    _empty = {
        "corr_30d":    None,
        "corr_90d":    None,
        "hist_mean":   None,
        "hist_std":    None,
        "z_score":     None,
        "divergence":  "NEUTRAL",
        "pair_score":  0,
        "return_30d_a": None,
        "return_30d_b": None,
    }

    try:
        df_a = get_price_df(ticker_a, days=days)
        df_b = get_price_df(ticker_b, days=days)
    except Exception as exc:
        logger.warning("Data fetch failed for pair %s/%s: %s", ticker_a, ticker_b, exc)
        return _empty

    if df_a.empty or df_b.empty:
        return _empty

    # Compute 30-day returns on raw close prices (for catch-up opportunity detection).
    def _recent_return(df: pd.DataFrame, window: int = 30) -> Optional[float]:
        try:
            close = df.set_index("date")["close"].astype(float).sort_index().dropna()
            n = min(window, len(close) - 1)
            if n < 1:
                return None
            return float((close.iloc[-1] - close.iloc[-1 - n]) / close.iloc[-1 - n])
        except Exception:
            return None

    return_30d_a = _recent_return(df_a)
    return_30d_b = _recent_return(df_b)

    try:
        # Use daily returns rather than raw price levels.  Price levels are
        # non-stationary: two trending series can show high correlation within
        # any 30-day window purely because of shared autocorrelation, producing
        # a hist_std so inflated that meaningful divergences collapse to near-zero
        # z-scores.  Return correlations are stationary and measure the actual
        # day-to-day co-movement relationship.
        s_a = df_a.set_index("date")["close"].astype(float).pct_change().rename(ticker_a)
        s_b = df_b.set_index("date")["close"].astype(float).pct_change().rename(ticker_b)
        combined = pd.concat([s_a, s_b], axis=1, join="inner").dropna()
    except Exception as exc:
        logger.warning("Alignment failed for %s/%s: %s", ticker_a, ticker_b, exc)
        return _empty

    if len(combined) < 31:
        return _empty

    roll_30 = combined[ticker_a].rolling(30).corr(combined[ticker_b])
    roll_90 = combined[ticker_a].rolling(90).corr(combined[ticker_b]) if len(combined) >= 90 else roll_30

    corr_30d = float(roll_30.iloc[-1]) if not np.isnan(float(roll_30.iloc[-1])) else None
    corr_90d = float(roll_90.iloc[-1]) if not np.isnan(float(roll_90.iloc[-1])) else None

    if corr_30d is None:
        return _empty

    valid_30 = roll_30.dropna()
    if len(valid_30) < 10:
        return {**_empty, "corr_30d": corr_30d, "corr_90d": corr_90d}

    hist_mean = float(valid_30.mean())
    hist_std  = float(valid_30.std())
    if hist_std < 0.001:
        hist_std = 0.001

    z_score = (corr_30d - hist_mean) / hist_std

    # CONFIRMING: sign of current corr matches expected AND z-score close to norm
    # DIVERGING:  sign contradicts expected AND z-score notably far from norm
    expected_pos  = expected_sign > 0
    current_pos   = corr_30d > 0
    sign_mismatch = expected_pos != current_pos
    abs_z         = abs(z_score)

    if sign_mismatch and abs_z >= 1.5:
        divergence = "DIVERGING"
        pair_score = -1
    elif not sign_mismatch and abs_z <= 1.0:
        divergence = "CONFIRMING"
        pair_score = 1
    else:
        divergence = "NEUTRAL"
        pair_score = 0

    return {
        "corr_30d":     round(corr_30d, 4),
        "corr_90d":     round(corr_90d, 4) if corr_90d is not None else None,
        "hist_mean":    round(hist_mean, 4),
        "hist_std":     round(hist_std, 4),
        "z_score":      round(z_score, 3),
        "divergence":   divergence,
        "pair_score":   pair_score,
        "return_30d_a": round(return_30d_a, 5) if return_30d_a is not None else None,
        "return_30d_b": round(return_30d_b, 5) if return_30d_b is not None else None,
    }


def compute_correlations(ticker: str, config: Optional[dict] = None) -> dict:
    """Compute a cross-asset correlation score for a single ticker.

    Finds all predefined pairs that involve *ticker*, computes current rolling
    correlations, checks for divergence from historical norms, and aggregates
    into a single score for use in confluence scoring.

    Args:
        ticker: The asset being scored.
        config: Loaded config.yaml dict. Loaded from file if not provided.

    Returns:
        Dict with:
        - ``score``             — -1 / 0 / 1 (for confluence weighting)
        - ``pairs``             — list of per-pair detail dicts
        - ``correlation_score`` — continuous average ∈ [-1, 1]
    """
    if config is None:
        config = _load_config()

    days      = config.get("data", {}).get("history_days", 365)
    pairs_data: list[dict] = []

    for rel in RELATIONSHIP_MAP:
        if ticker not in (rel["ticker_a"], rel["ticker_b"]):
            continue
        result = _compute_pair(rel["ticker_a"], rel["ticker_b"], rel["expected_sign"], days)
        pairs_data.append({**rel, **result})

    if not pairs_data:
        return {"score": 0, "pairs": [], "correlation_score": 0.0}

    pair_scores = [p["pair_score"] for p in pairs_data]
    avg   = sum(pair_scores) / len(pair_scores)
    score = 1 if avg >= 0.5 else (-1 if avg <= -0.5 else 0)

    return {
        "score":             score,
        "pairs":             pairs_data,
        "correlation_score": round(avg, 3),
    }


def _persist_pair(
    engine,
    ticker_a: str,
    ticker_b: str,
    date: datetime,
    result: dict,
) -> None:
    """Upsert one pair row into the correlations table."""
    from data.database import CorrelationData  # late import avoids circular reference
    stmt = sqlite_insert(CorrelationData).values(
        ticker_a=ticker_a,
        ticker_b=ticker_b,
        date=date,
        corr_30d=result["corr_30d"],
        corr_90d=result["corr_90d"],
        hist_mean=result["hist_mean"],
        hist_std=result["hist_std"],
        z_score=result["z_score"],
        divergence=result["divergence"],
        pair_score=result["pair_score"],
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker_a", "ticker_b", "date"],
        set_={
            "corr_30d":   stmt.excluded.corr_30d,
            "corr_90d":   stmt.excluded.corr_90d,
            "hist_mean":  stmt.excluded.hist_mean,
            "hist_std":   stmt.excluded.hist_std,
            "z_score":    stmt.excluded.z_score,
            "divergence": stmt.excluded.divergence,
            "pair_score": stmt.excluded.pair_score,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def _detect_catch_up(rel: dict, result: dict) -> Optional[dict]:
    """Return a catch-up opportunity flag if a diverging pair implies one, else None.

    Conditions:
    - Pair must be DIVERGING (sign mismatch + z-score ≥ 1.5).
    - The pair must have a CATCH_UP_MAP entry.
    - The lead asset must have outpaced the lag asset by the configured threshold.
    """
    if result.get("divergence") != "DIVERGING":
        return None
    if abs(float(result.get("z_score") or 0)) < 1.5:
        return None

    key = (rel["ticker_a"], rel["ticker_b"])
    cfg = CATCH_UP_MAP.get(key)
    if cfg is None:
        return None

    ret_a = result.get("return_30d_a")
    ret_b = result.get("return_30d_b")
    if ret_a is None or ret_b is None:
        return None

    lead = cfg["lead"]
    lag  = cfg["lag"]
    lead_ret = ret_a if lead == rel["ticker_a"] else ret_b
    lag_ret  = ret_a if lag  == rel["ticker_a"] else ret_b

    triggered = False
    if cfg["direction"] == "positive":
        min_gap = cfg.get("min_return_gap", 0.03)
        triggered = lead_ret > lag_ret + min_gap
    elif cfg["direction"] == "inverse":
        # Lead falling (dollar weakness) but lag hasn't responded with a rise
        min_move = cfg.get("min_lead_move", 0.02)
        triggered = lead_ret < -min_move and lag_ret <= 0.005

    if not triggered:
        return None

    return {
        "ticker":       lag,
        "flag_type":    "CATCH_UP_OPPORTUNITY",
        "flag_source":  f"{rel['ticker_a']}/{rel['ticker_b']} divergence",
        "description":  cfg["description"],
        "z_score":      result.get("z_score"),
        "pair_corr_30d": result.get("corr_30d"),
    }


def _persist_opportunity_flags(flags: list[dict], engine, as_of: datetime) -> None:
    """Upsert catch-up opportunity flags into the opportunity_flags table."""
    from data.database import OpportunityFlag  # late import — avoids circular reference

    for flag in flags:
        stmt = sqlite_insert(OpportunityFlag).values(
            date=as_of,
            ticker=flag["ticker"],
            flag_type=flag["flag_type"],
            flag_source=flag.get("flag_source", ""),
            description=flag.get("description", ""),
            z_score=flag.get("z_score"),
            pair_corr_30d=flag.get("pair_corr_30d"),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "flag_type", "date"],
            set_={
                "flag_source":   stmt.excluded.flag_source,
                "description":   stmt.excluded.description,
                "z_score":       stmt.excluded.z_score,
                "pair_corr_30d": stmt.excluded.pair_corr_30d,
            },
        )
        try:
            with engine.begin() as conn:
                conn.execute(stmt)
        except Exception as exc:
            logger.warning(
                "Failed to persist opportunity flag for %s: %s",
                flag["ticker"], exc,
            )


def get_active_opportunity_flags(lookback_hours: int = 28) -> list[dict]:
    """Return catch-up opportunity flags detected within the last *lookback_hours*.

    Falls back to an empty list if the opportunity_flags table does not yet exist
    (e.g. before the first analysis run on a fresh install).
    """
    engine = get_engine()
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT * FROM opportunity_flags "
                    "WHERE date >= :cutoff "
                    "ORDER BY flag_type, ticker"
                ),
                {"cutoff": cutoff},
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Could not read opportunity flags: %s", exc)
        return []


def compute_credit_stress_z(config: Optional[dict] = None) -> Optional[float]:
    """Compute the z-score of the current HYG-minus-TLT 30-day rolling return spread.

    A z-score below -1.0 means HYG is underperforming TLT by more than one historical
    standard deviation — a widening credit spread that is the basis for the CREDIT_STRESS
    overlay in :func:`~analysis.regime.detect_overlays`.

    Returns:
        Float z-score, or None when there is insufficient price history.
    """
    if config is None:
        config = _load_config()
    days = config.get("data", {}).get("history_days", 365)

    try:
        df_hyg = get_price_df("HYG", days=days)
        df_tlt = get_price_df("TLT", days=days)
        if df_hyg.empty or df_tlt.empty:
            return None

        hyg = df_hyg.set_index("date")["close"].astype(float).sort_index()
        tlt = df_tlt.set_index("date")["close"].astype(float).sort_index()

        combined = pd.concat(
            [hyg.rename("HYG"), tlt.rename("TLT")], axis=1, join="inner"
        ).dropna()

        if len(combined) < 60:
            return None

        # Rolling 30-day percentage return for each asset; spread = HYG excess over TLT
        hyg_30d = combined["HYG"].pct_change(30)
        tlt_30d = combined["TLT"].pct_change(30)
        spread  = (hyg_30d - tlt_30d).dropna()

        if len(spread) < 20:
            return None

        hist_mean = float(spread.mean())
        hist_std  = float(spread.std())
        if hist_std < 0.0001:
            return None

        return round((float(spread.iloc[-1]) - hist_mean) / hist_std, 3)

    except Exception as exc:
        logger.warning("compute_credit_stress_z failed: %s", exc)
        return None


def compute_all_pairs(config: Optional[dict] = None) -> list[dict]:
    """Compute correlation metrics for every pair in RELATIONSHIP_MAP and persist to DB.

    Called once from the analysis engine rather than once per ticker, since
    pair data is shared across multiple assets.

    Args:
        config: Loaded config.yaml dict. Loaded from file if not provided.

    Returns:
        List of pair result dicts (one entry per pair in RELATIONSHIP_MAP).
    """
    if config is None:
        config = _load_config()

    days  = config.get("data", {}).get("history_days", 365)
    as_of = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    engine = get_engine()
    results:      list[dict] = []
    catch_up_flags: list[dict] = []

    for rel in RELATIONSHIP_MAP:
        result = _compute_pair(rel["ticker_a"], rel["ticker_b"], rel["expected_sign"], days)
        row    = {**rel, **result, "date": as_of}
        results.append(row)

        if result["corr_30d"] is not None:
            try:
                _persist_pair(engine, rel["ticker_a"], rel["ticker_b"], as_of, result)
            except Exception as exc:
                logger.warning(
                    "Failed to persist correlation %s/%s: %s",
                    rel["ticker_a"], rel["ticker_b"], exc,
                )

        flag = _detect_catch_up(rel, result)
        if flag:
            catch_up_flags.append(flag)
            logger.info(
                "Catch-up opportunity detected: %s (source: %s, z=%.2f)",
                flag["ticker"], flag["flag_source"], flag.get("z_score") or 0,
            )

    if catch_up_flags:
        _persist_opportunity_flags(catch_up_flags, engine, as_of)

    # Update HYG/TLT return-spread z-score for the CREDIT_STRESS overlay detection.
    # Stored in system_metadata so detect_overlays() can read it without a full
    # price-data recalculation on every generate_signals() call.
    try:
        spread_z = compute_credit_stress_z(config)
        if spread_z is not None:
            set_metadata("hyg_tlt_spread_z", str(spread_z))
            logger.info("HYG/TLT return-spread z=%.2f", spread_z)
    except Exception as exc:
        logger.warning("Credit stress z update failed: %s", exc)

    return results


def get_stored_correlations() -> list[dict]:
    """Return the most recent correlation row per pair from the database.

    Falls back to None values (NEUTRAL) for any pair with no stored data yet.

    Returns:
        List of dicts merging static pair metadata with the latest computed values.
    """
    engine = get_engine()
    results: list[dict] = []

    _null_result = {
        "corr_30d": None, "corr_90d": None,
        "hist_mean": None, "hist_std": None,
        "z_score": None, "divergence": "NEUTRAL",
        "pair_score": 0, "date": None,
    }

    try:
        with engine.connect() as conn:
            for rel in RELATIONSHIP_MAP:
                row = conn.execute(
                    text(
                        "SELECT corr_30d, corr_90d, hist_mean, hist_std, z_score, "
                        "divergence, pair_score, date "
                        "FROM correlations "
                        "WHERE ticker_a = :a AND ticker_b = :b "
                        "ORDER BY date DESC LIMIT 1"
                    ),
                    {"a": rel["ticker_a"], "b": rel["ticker_b"]},
                ).mappings().first()
                results.append({**rel, **(dict(row) if row else _null_result)})
    except Exception as exc:
        logger.warning("Could not read stored correlations: %s", exc)
        return [{**rel, **_null_result} for rel in RELATIONSHIP_MAP]

    return results


def build_correlation_matrix(window: int = 30) -> tuple[pd.DataFrame, list[str]]:
    """Compute a pairwise correlation matrix for all tickers in the relationship map.

    Uses the last *window* trading days of close prices for each ticker that
    has stored data.

    Args:
        window: Number of trading days to use for correlation calculation.

    Returns:
        Tuple of (corr_matrix_DataFrame, ticker_list).  The DataFrame is
        square and indexed/columned by ticker symbol.  Returns an empty
        DataFrame and empty list when insufficient data is available.
    """
    tickers = sorted(_ALL_PAIR_TICKERS)
    series: dict[str, pd.Series] = {}

    for ticker in tickers:
        try:
            df = get_price_df(ticker, days=max(window * 3, 180))
            if df.empty:
                continue
            # Daily returns — consistent with _compute_pair; avoids spurious
            # level-based correlations from shared autocorrelation in price series.
            s = df.set_index("date")["close"].astype(float).pct_change()
            series[ticker] = s
        except Exception:
            pass

    if len(series) < 2:
        return pd.DataFrame(), []

    combined = pd.concat(series.values(), axis=1, keys=series.keys(), join="inner").dropna()

    if len(combined) < window:
        corr_matrix = combined.corr()
    else:
        corr_matrix = combined.tail(window).corr()

    return corr_matrix, list(corr_matrix.columns)
