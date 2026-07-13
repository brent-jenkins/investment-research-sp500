"""Due-diligence / robustness metrics for the backtest engine.

Distinct from analysis/backtest_metrics.py: that module answers "what
happened" (win rate, profit factor, drawdown, module attribution, signal
decay) for the Backtest and Performance pages. This module answers "why
should these results be trusted" — parameter-freeze / out-of-sample
tracking, winner concentration, annual/monthly return breakdowns, Calmar
ratios, rolling Sharpe, sector/asset-class attribution, and the regime-filter
contribution — for the dedicated Robustness page (main.py:
_render_robustness_page()).

Every compute_*() function here is read-only: it reads a completed
backtest_runs / backtest_trades run (via analysis.backtest.get_run() /
get_run_trades()) and/or t212_pnl directly, and returns a plain dict or
pandas DataFrame for the UI to render. Nothing here writes to the database
except the two-pass regime-filter contribution, which is written by
analysis/backtest.py:run_backtest() itself (see that module) — this module
only reads the resulting with_regime_filter / without_regime_filter columns.
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import text

from analysis.backtest import get_run, get_run_trades
from analysis.backtest_metrics import compute_metrics, equity_curve
from data.database import get_engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parameter freeze — every result dated on or before this date used a config
# (regime weights, entry/exit thresholds, VaR risk parameters, stop
# multiplier, etc.) that was tuned and re-tuned against that same historical
# data — see the many backtest-and-re-tune sections in CLAUDE.md (TRENDING_
# BULL weight rebalances, indicator-redundancy removal, GARCH/HMM removal,
# and so on). Those results are therefore in-sample by construction. Any
# T212 trade that actually closes after this date is genuinely out-of-sample:
# the config was frozen (this file authored) before that outcome existed.
PARAMETER_FREEZE_DATE = date(2026, 7, 13)

# Earliest date the production backtest baseline documented in CLAUDE.md
# ("Current baseline") actually covers.
BACKTEST_START_DATE = date(2019, 4, 12)

# Known S&P 500 total-return-adjacent calendar-year returns, for the
# vs-S&P-500 column on the annual returns table. Hardcoded per the feature
# request rather than fetched, since ^GSPC price data in this DB is a price
# index (no dividends) and these are well-known reference figures.
SP500_ANNUAL: dict[int, float] = {
    2019: 31.5, 2020: 18.4, 2021: 28.7, 2022: -18.1,
    2023: 26.3, 2024: 25.0, 2025: 12.0,
}

# ---------------------------------------------------------------------------
# Sector / asset-class classification
# ---------------------------------------------------------------------------

# us_large_cap tickers get a real sector rather than the literal group name
# "us_large_cap" — mirrors the sector table in CLAUDE.md's "Adding assets to
# the watchlist" section. Every other watchlist ticker's "sector" is simply
# its watchlist group name (see _ticker_sector_map() below) — those groups
# (sector_etfs, bonds, commodities, ...) already function as coarse
# asset-class buckets, so no second hardcoded map is needed for them.
US_LARGE_CAP_SECTORS: dict[str, str] = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMZN": "Technology", "ADBE": "Technology", "CRM": "Technology", "ORCL": "Technology",
    "NFLX": "Communication Services", "DIS": "Communication Services",
    "JPM": "Financials", "GS": "Financials", "BAC": "Financials",
    "V": "Financials", "MA": "Financials", "SPGI": "Financials",
    "LLY": "Healthcare", "UNH": "Healthcare", "JNJ": "Healthcare",
    "PFE": "Healthcare", "MRK": "Healthcare", "TMO": "Healthcare", "ISRG": "Healthcare",
    "AMD": "Semiconductors", "AVGO": "Semiconductors", "QCOM": "Semiconductors",
    "COST": "Consumer Staples", "WMT": "Consumer Staples", "PG": "Consumer Staples",
    "HD": "Consumer Discretionary",
    "XOM": "Energy", "CVX": "Energy",
    "CAT": "Industrials", "RTX": "Industrials", "GE": "Industrials",
    "LIN": "Materials",
    "NEM": "Gold Miners", "GOLD": "Gold Miners",
}

_BOND_GROUPS      = frozenset({"bonds", "fixed_income_expanded"})
_COMMODITY_GROUPS = frozenset({"commodities", "commodities_expanded", "real_assets", "energy"})
_CRYPTO_GROUPS    = frozenset({"crypto", "crypto_expanded"})
_INTERNATIONAL_GROUPS = frozenset({"international", "international_expanded", "uk_etfs"})


def _load_config() -> dict:
    p = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(p) as f:
        return yaml.safe_load(f)


def _watchlist_ticker_groups(config: dict) -> dict[str, str]:
    """Return {ticker: watchlist group name} for every entry in config.yaml."""
    out: dict[str, str] = {}
    for group, entries in (config.get("watchlist") or {}).items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if isinstance(e, dict) and "ticker" in e:
                out[e["ticker"]] = group
    return out


def _ticker_sector_map(config: dict) -> dict[str, str]:
    """Map every watchlist ticker to a display "sector".

    us_large_cap tickers get a real sector from US_LARGE_CAP_SECTORS above;
    every other ticker's sector is its watchlist group name, title-cased
    (e.g. "sector_etfs" -> "Sector Etfs", "bonds" -> "Bonds").
    """
    out: dict[str, str] = {}
    for group, entries in (config.get("watchlist") or {}).items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not (isinstance(e, dict) and "ticker" in e):
                continue
            ticker = e["ticker"]
            if group == "us_large_cap":
                out[ticker] = US_LARGE_CAP_SECTORS.get(ticker, "Other")
            else:
                out[ticker] = group.replace("_", " ").title()
    return out


def _asset_class_for(ticker: str, group: str) -> str:
    if ticker.endswith("=X"):
        return "Currency"
    if ticker.endswith("-USD") or group in _CRYPTO_GROUPS:
        return "Crypto"
    if group in _BOND_GROUPS:
        return "Fixed Income"
    if group in _COMMODITY_GROUPS:
        return "Commodity"
    return "Equity"


def _region_for(group: str) -> str:
    return "International" if group in _INTERNATIONAL_GROUPS else "Domestic (US)"


def _etf_or_stock_for(group: str) -> str:
    return "Stock" if group == "us_large_cap" else "ETF"


# ---------------------------------------------------------------------------
# Panel 1 — Edge Quality
# ---------------------------------------------------------------------------

def compute_winner_concentration(run_id: int) -> dict:
    """Return {top5_pct, top10_pct, top20_pct} — % of total winning pnl_pct
    contributed by the N largest winning trades, for N in (5, 10, 20)."""
    trades = get_run_trades(run_id)
    empty = {"top5_pct": 0.0, "top10_pct": 0.0, "top20_pct": 0.0}
    if trades.empty or "pnl_pct" not in trades.columns:
        return empty

    winners = trades[trades["pnl_pct"] > 0].sort_values("pnl_pct", ascending=False)
    total_profit = float(winners["pnl_pct"].sum())
    if total_profit <= 0:
        return empty

    out = dict(empty)
    for n in (5, 10, 20):
        topn_profit = float(winners.head(n)["pnl_pct"].sum())
        out[f"top{n}_pct"] = round(topn_profit / total_profit * 100, 1)
    return out


def compute_expectancy(run_id: int) -> dict:
    """Expectancy per trade = (avg_win * win_rate) - (avg_loss * loss_rate),
    where avg_loss is used as a positive magnitude (compute_metrics()'s
    avg_loss_pct is negative — the mean of negative pnl_pct values — so it is
    abs()'d here to match the intended "average win minus average loss
    magnitude" formula rather than double-counting the sign).

    Also expresses the result in £ at the account value configured for the
    backtest (backtest.starting_capital_gbp), since a %-only expectancy
    doesn't communicate what a real position is expected to earn.
    """
    trades = get_run_trades(run_id)
    if trades.empty:
        return {
            "expectancy_pct": 0.0, "expectancy_gbp": 0.0,
            "win_rate_pct": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "account_value_gbp": 0.0,
        }

    mets = compute_metrics(trades)
    win_rate  = mets.get("win_rate") or 0.0
    loss_rate = 1.0 - win_rate
    avg_win   = mets.get("avg_win_pct") or 0.0
    avg_loss_abs = abs(mets.get("avg_loss_pct") or 0.0)

    expectancy_pct = (avg_win * win_rate) - (avg_loss_abs * loss_rate)

    config = _load_config()
    account_value = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    expectancy_gbp = expectancy_pct / 100.0 * account_value

    return {
        "expectancy_pct":     round(expectancy_pct, 3),
        "expectancy_gbp":     round(expectancy_gbp, 2),
        "win_rate_pct":       round(win_rate * 100, 1),
        "avg_win_pct":        round(avg_win, 3),
        "avg_loss_pct":       round(-avg_loss_abs, 3),
        "account_value_gbp":  account_value,
    }


def compute_out_of_sample_stats() -> dict:
    """Read t212_pnl for positions closed strictly after PARAMETER_FREEZE_DATE.

    Returns zero-trade stats (status "grey") when no t212_pnl rows exist yet
    after the freeze date — expected for a long time after this feature
    ships, since T212 closes accumulate slowly (~5-10/year per CLAUDE.md's
    "Feedback learning" section).
    """
    engine = get_engine()
    rows: list[dict] = []
    try:
        with engine.connect() as conn:
            rows = [
                dict(r) for r in conn.execute(
                    text(
                        "SELECT pnl_pct, pnl_gbp, exit_date FROM t212_pnl "
                        "WHERE date(exit_date) > date(:freeze)"
                    ),
                    {"freeze": PARAMETER_FREEZE_DATE.isoformat()},
                ).mappings().all()
            ]
    except Exception:
        rows = []

    n = len(rows)
    return_since_freeze_pct = round(sum((r.get("pnl_pct") or 0.0) for r in rows) * 100, 2)
    pnl_since_freeze_gbp    = round(sum((r.get("pnl_gbp") or 0.0) for r in rows), 2)

    if n == 0:
        status = "grey"
    elif return_since_freeze_pct > 0:
        status = "green"
    elif return_since_freeze_pct < 0:
        status = "red"
    else:
        status = "grey"

    return {
        "freeze_date":               PARAMETER_FREEZE_DATE,
        "n_trades":                  n,
        "return_since_freeze_pct":   return_since_freeze_pct,
        "pnl_since_freeze_gbp":      pnl_since_freeze_gbp,
        "status":                    status,
    }


def compute_edge_quality(run_id: int) -> dict:
    """Aggregate everything Panel 1 ("Edge Quality") needs into one dict."""
    run    = get_run(run_id)
    trades = get_run_trades(run_id)

    result: dict = {
        "available":              run is not None,
        "run_id":                 run_id,
        "parameter_freeze_date":  PARAMETER_FREEZE_DATE,
        "in_sample_start":        BACKTEST_START_DATE,
        "in_sample_end":          PARAMETER_FREEZE_DATE,
        "out_of_sample":          compute_out_of_sample_stats(),
    }

    if trades.empty:
        result.update({
            "time_invested_pct":    0.0,
            "cash_allocation_pct":  100.0,
            "profit_factor":        0.0,
            "win_rate_pct":         0.0,
            "expectancy":           compute_expectancy(run_id),
            "winner_concentration": compute_winner_concentration(run_id),
            "exit_reason_breakdown": {},
        })
        return result

    df = trades.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"]  = pd.to_datetime(df["exit_date"])

    start, end = df["entry_date"].min(), df["exit_date"].max()
    all_days = pd.bdate_range(start, end)
    invested_days: set = set()
    for _, t in df.iterrows():
        invested_days.update(pd.bdate_range(t["entry_date"], t["exit_date"]))
    time_invested_pct = round(len(invested_days) / len(all_days) * 100, 1) if len(all_days) else 0.0

    mets = compute_metrics(df)
    exit_counts = df["exit_reason"].value_counts() if "exit_reason" in df.columns else pd.Series(dtype=int)
    n_trades = len(df)
    exit_breakdown = {
        str(reason): {"count": int(c), "pct": round(float(c) / n_trades * 100, 1)}
        for reason, c in exit_counts.items()
    }

    result.update({
        "time_invested_pct":     time_invested_pct,
        "cash_allocation_pct":   round(100.0 - time_invested_pct, 1),
        "profit_factor":         mets.get("profit_factor", 0.0),
        "win_rate_pct":          round((mets.get("win_rate") or 0.0) * 100, 1),
        "expectancy":            compute_expectancy(run_id),
        "winner_concentration":  compute_winner_concentration(run_id),
        "exit_reason_breakdown": exit_breakdown,
    })
    return result


# ---------------------------------------------------------------------------
# Panel 2 — Returns Analysis
# ---------------------------------------------------------------------------

def compute_annual_returns(run_id: int) -> pd.DataFrame:
    """Per-exit-year trade count, win rate, return %, £ P&L, and vs-S&P-500."""
    cols = ["year", "trades", "win_rate_pct", "total_return_pct", "pnl_gbp", "sp500_pct", "vs_sp500_pct"]
    trades = get_run_trades(run_id)
    if trades.empty:
        return pd.DataFrame(columns=cols)

    df = trades.copy()
    df["exit_year"] = pd.to_datetime(df["exit_date"]).dt.year

    rows = []
    for year, sub in df.groupby("exit_year"):
        m = compute_metrics(sub)
        has_gbp = "pnl_gbp" in sub.columns and sub["pnl_gbp"].notna().any()
        pnl_gbp = round(float(sub["pnl_gbp"].sum()), 2) if has_gbp else None
        sp = SP500_ANNUAL.get(int(year))
        total_return_pct = m.get("total_return_pct", 0.0)
        rows.append({
            "year":              int(year),
            "trades":            m.get("total_trades", 0),
            "win_rate_pct":       round((m.get("win_rate") or 0.0) * 100, 1),
            "total_return_pct":   total_return_pct,
            "pnl_gbp":            pnl_gbp,
            "sp500_pct":          sp,
            "vs_sp500_pct":       round(total_return_pct - sp, 1) if sp is not None else None,
        })
    return pd.DataFrame(rows, columns=cols).sort_values("year").reset_index(drop=True)


def compute_monthly_heatmap(run_id: int) -> pd.DataFrame:
    """Year x month grid of summed pnl_pct (%) for trades exiting in that month.

    Months/years with no exiting trade contribute 0.0, not a missing cell —
    per the feature spec, "empty" means neutral, not absent.
    """
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    trades = get_run_trades(run_id)
    if trades.empty:
        years = [BACKTEST_START_DATE.year]
    else:
        exit_years = pd.to_datetime(trades["exit_date"]).dt.year
        years = list(range(int(exit_years.min()), int(exit_years.max()) + 1))

    heat = pd.DataFrame(0.0, index=years, columns=range(1, 13))

    if not trades.empty:
        df = trades.copy()
        df["exit_dt"] = pd.to_datetime(df["exit_date"])
        df["y"] = df["exit_dt"].dt.year
        df["m"] = df["exit_dt"].dt.month
        grouped = df.groupby(["y", "m"])["pnl_pct"].sum() * 100
        for (y, m), v in grouped.items():
            if y in heat.index:
                heat.loc[y, m] = round(float(v), 2)

    heat.columns = month_names
    return heat


def compute_rolling_metrics(run_id: int, window_days: int = 365) -> pd.DataFrame:
    """Rolling trailing-window return % and Sharpe, one row per month-end.

    Each row's window is (as_of - window_days, as_of], evaluated over trades'
    exit_date — mirrors analysis/backtest_metrics.py:walk_forward()'s
    trade-level annualised Sharpe convention (rather than a daily-equity-
    curve Sharpe), since the equity curve here is a sparse step function
    (flat between trade exits) that would make a daily-return Sharpe mostly
    measure the gaps between exits rather than genuine risk-adjusted return.
    """
    cols = ["date", "rolling_return_pct", "rolling_sharpe"]
    trades = get_run_trades(run_id)
    if trades.empty:
        return pd.DataFrame(columns=cols)

    df = trades.copy()
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df = df.sort_values("exit_date")

    start, end = df["exit_date"].min(), df["exit_date"].max()
    month_ends = pd.date_range(start, end, freq="ME")
    if len(month_ends) == 0 or month_ends[-1] < end:
        month_ends = month_ends.append(pd.DatetimeIndex([end]))

    rows = []
    for as_of in month_ends:
        window_start = as_of - pd.Timedelta(days=window_days)
        sub = df[(df["exit_date"] > window_start) & (df["exit_date"] <= as_of)]
        if sub.empty:
            rows.append({"date": as_of, "rolling_return_pct": 0.0, "rolling_sharpe": 0.0})
            continue
        m = compute_metrics(sub)
        rows.append({
            "date":               as_of,
            "rolling_return_pct": m.get("total_return_pct", 0.0),
            "rolling_sharpe":     m.get("sharpe_ratio", 0.0),
        })
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Panel 3 — Risk Analysis
# ---------------------------------------------------------------------------

def compute_drawdown_metrics(run_id: int) -> dict:
    """Current/max/average drawdown and recovery-time stats from the equity curve.

    "Days" here are calendar days from analysis/backtest_metrics.py:
    equity_curve()'s daily (not business-day) date axis — consistent with
    how holding_days is measured elsewhere in this codebase (calendar days
    between entry/exit timestamps), not a stricter trading-day count.
    """
    empty = {
        "current_drawdown_pct": 0.0, "max_drawdown_pct": 0.0, "avg_drawdown_pct": 0.0,
        "avg_recovery_days": 0.0, "longest_recovery_days": 0,
    }
    run    = get_run(run_id)
    trades = get_run_trades(run_id)
    if trades.empty:
        return empty

    ec = equity_curve(trades)
    if ec.empty:
        return empty

    peak = ec["equity"].cummax()
    in_dd = ec["equity"] < peak
    current_drawdown_pct = float(ec["drawdown_pct"].iloc[-1])

    max_dd = run.get("max_drawdown_pct") if run and run.get("max_drawdown_pct") is not None \
        else float(ec["drawdown_pct"].min())

    groups = (in_dd != in_dd.shift()).cumsum()
    depths: list[float] = []
    recovery_lengths: list[int] = []
    for _, idxs in ec.groupby(groups).groups.items():
        sub_mask = in_dd.loc[idxs]
        if sub_mask.empty or not bool(sub_mask.iloc[0]):
            continue
        depths.append(float(ec.loc[idxs, "drawdown_pct"].min()))
        recovery_lengths.append(len(idxs))

    material = [d for d in depths if d <= -1.0]
    avg_dd = float(np.mean(material)) if material else 0.0
    avg_recovery = float(np.mean(recovery_lengths)) if recovery_lengths else 0.0
    longest_recovery = int(max(recovery_lengths)) if recovery_lengths else 0

    return {
        "current_drawdown_pct":  round(current_drawdown_pct, 2),
        "max_drawdown_pct":      round(max_dd, 2),
        "avg_drawdown_pct":      round(avg_dd, 2),
        "avg_recovery_days":     round(avg_recovery, 1),
        "longest_recovery_days": longest_recovery,
    }


def _calmar_for_slice(sub_ec: pd.DataFrame) -> dict:
    if sub_ec is None or sub_ec.empty or len(sub_ec) < 2:
        return {"cagr_pct": 0.0, "max_dd_pct": 0.0, "calmar": 0.0}

    start_eq = float(sub_ec["equity"].iloc[0])
    end_eq   = float(sub_ec["equity"].iloc[-1])
    days     = (sub_ec["date"].iloc[-1] - sub_ec["date"].iloc[0]).days
    years    = days / 365.25 if days > 0 else 0.0

    cagr = ((end_eq / start_eq) ** (1.0 / years) - 1.0) * 100.0 if years > 0 and start_eq > 0 else 0.0

    peak   = sub_ec["equity"].cummax()
    max_dd = float(((sub_ec["equity"] - peak) / peak).min() * 100.0)

    calmar = round(cagr / abs(max_dd), 2) if max_dd < 0 else 0.0
    return {"cagr_pct": round(cagr, 2), "max_dd_pct": round(max_dd, 2), "calmar": calmar}


def compute_calmar_ratios(run_id: int) -> dict:
    """Calmar = CAGR / |max drawdown|, computed lifetime and over rolling
    36-month / 12-month trailing windows of the equity curve."""
    empty_variant = {"cagr_pct": 0.0, "max_dd_pct": 0.0, "calmar": 0.0}
    trades = get_run_trades(run_id)
    if trades.empty:
        return {"lifetime": dict(empty_variant), "rolling_36m": dict(empty_variant), "rolling_12m": dict(empty_variant)}

    ec = equity_curve(trades)
    if ec.empty:
        return {"lifetime": dict(empty_variant), "rolling_36m": dict(empty_variant), "rolling_12m": dict(empty_variant)}

    lifetime = _calmar_for_slice(ec)

    last_date = ec["date"].max()
    ec_36m = ec[ec["date"] >= last_date - pd.Timedelta(days=36 * 30)]
    ec_12m = ec[ec["date"] >= last_date - pd.Timedelta(days=365)]

    return {
        "lifetime":     lifetime,
        "rolling_36m":  _calmar_for_slice(ec_36m),
        "rolling_12m":  _calmar_for_slice(ec_12m),
    }


# ---------------------------------------------------------------------------
# Panel 4 — Attribution
# ---------------------------------------------------------------------------

def _get_t212_pnl_rows_for_attribution() -> list[dict]:
    """Minimal t212_pnl read (ticker, pnl_pct, pnl_gbp) for live attribution."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT ticker, pnl_pct, pnl_gbp FROM t212_pnl")
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _bucket_stats(df: pd.DataFrame, bucket_col: str) -> list[dict]:
    total_profit_all = float(df[df["pnl_pct"] > 0]["pnl_pct"].sum())
    rows = []
    for name, sub in df.groupby(bucket_col):
        wins   = float(sub[sub["pnl_pct"] > 0]["pnl_pct"].sum())
        losses = float(sub[sub["pnl_pct"] < 0]["pnl_pct"].sum())
        if losses != 0:
            pf = round(min(wins / abs(losses), 99.9), 2)
        else:
            pf = 99.9 if wins > 0 else 0.0
        pct_of_profit = round(wins / total_profit_all * 100, 1) if total_profit_all else 0.0
        rows.append({
            "name":                str(name),
            "trades":              int(len(sub)),
            "win_rate_pct":        round(float(sub["is_win"].mean()) * 100, 1) if len(sub) else 0.0,
            "profit_pct_of_total": pct_of_profit,
            "profit_factor":       pf,
        })
    return rows


def compute_attribution(run_id: int, config: dict, source: str = "backtest") -> dict:
    """Sector and asset-class breakdown of trade profit.

    source: "historical" (backtest_trades for run_id), "live" (t212_pnl,
    typically empty until enough real closes accumulate), or "combined"
    (both concatenated). "backtest" is accepted as a synonym for
    "historical" for callers that pass the run's own source label directly.
    """
    groups     = _watchlist_ticker_groups(config)
    sector_map = _ticker_sector_map(config)

    def _historical() -> pd.DataFrame:
        df = get_run_trades(run_id)
        if df.empty:
            return pd.DataFrame(columns=["ticker", "pnl_pct", "pnl_gbp", "is_win"])
        out = df[["ticker", "pnl_pct", "is_win"]].copy()
        out["pnl_gbp"] = df["pnl_gbp"] if "pnl_gbp" in df.columns else np.nan
        return out

    def _live() -> pd.DataFrame:
        rows = _get_t212_pnl_rows_for_attribution()
        if not rows:
            return pd.DataFrame(columns=["ticker", "pnl_pct", "pnl_gbp", "is_win"])
        out = pd.DataFrame(rows)
        out["is_win"] = out["pnl_gbp"] > 0
        return out[["ticker", "pnl_pct", "pnl_gbp", "is_win"]]

    if source == "live":
        df = _live()
    elif source == "combined":
        df = pd.concat([_historical(), _live()], ignore_index=True)
    else:  # "historical" / "backtest" (default)
        df = _historical()

    empty_result = {
        "available": False, "source": source,
        "sector_table": pd.DataFrame(), "etf_vs_stock": [],
        "domestic_vs_intl": [], "asset_class": [],
    }
    if df.empty:
        return empty_result

    df["group"]        = df["ticker"].map(groups).fillna("unknown")
    df["sector"]        = df["ticker"].map(sector_map).fillna("Other")
    df["asset_class"]   = [_asset_class_for(t, g) for t, g in zip(df["ticker"], df["group"])]
    df["region"]         = [_region_for(g) for g in df["group"]]
    df["etf_or_stock"]  = [_etf_or_stock_for(g) for g in df["group"]]

    sector_rows = _bucket_stats(df, "sector")
    sector_table = (
        pd.DataFrame(sector_rows).sort_values("trades", ascending=False).reset_index(drop=True)
        if sector_rows else pd.DataFrame()
    )

    return {
        "available":         True,
        "source":            source,
        "sector_table":      sector_table,
        "etf_vs_stock":      _bucket_stats(df, "etf_or_stock"),
        "domestic_vs_intl":  _bucket_stats(df, "region"),
        "asset_class":       _bucket_stats(df, "asset_class"),
    }


def compute_regime_filter_contribution(run_id: int) -> dict:
    """Read the two-pass regime-filter comparison stored on backtest_runs.

    analysis/backtest.py:run_backtest() writes with_regime_filter /
    without_regime_filter (JSON quick-metrics dicts, see _quick_metrics())
    for every run — see that module and CLAUDE.md's "Regime filter
    contribution" section. Returns available=False for older runs
    (pre-this-feature) that have no stored comparison.
    """
    run = get_run(run_id)
    if not run:
        return {"available": False}

    raw_with    = run.get("with_regime_filter")
    raw_without = run.get("without_regime_filter")
    if not raw_with or not raw_without:
        return {"available": False}

    try:
        with_filter    = json.loads(raw_with)    if isinstance(raw_with, str)    else raw_with
        without_filter = json.loads(raw_without) if isinstance(raw_without, str) else raw_without
    except (TypeError, ValueError):
        return {"available": False}

    wf_return  = with_filter.get("total_return_pct", 0.0) or 0.0
    wof_return = without_filter.get("total_return_pct", 0.0) or 0.0
    wf_dd      = with_filter.get("max_drawdown_pct", 0.0) or 0.0
    wof_dd     = without_filter.get("max_drawdown_pct", 0.0) or 0.0
    wf_sharpe  = with_filter.get("sharpe_ratio", 0.0) or 0.0
    wof_sharpe = without_filter.get("sharpe_ratio", 0.0) or 0.0

    return {
        "available":              True,
        "with_filter":            with_filter,
        "without_filter":         without_filter,
        "return_improvement_pct": round(wf_return - wof_return, 1),
        "max_dd_reduction_pct":   round(wf_dd - wof_dd, 1),
        "sharpe_improvement":     round(wf_sharpe - wof_sharpe, 2),
    }
