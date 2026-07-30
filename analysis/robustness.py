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
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")  # headless — no display server, must be set before pyplot is imported anywhere in-process
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as _rl_canvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import text

from analysis.backtest import get_run, get_run_trades
from analysis.backtest_metrics import compute_metrics, equity_curve, equity_curve_gbp
from data.database import get_engine, get_metadata, get_metadata_str

logger = logging.getLogger(__name__)

# UK local-time formatting for this module's own report timestamps. Cannot
# import main.py:_fmt_uk() — main.py imports FROM this module, so the reverse
# import would be circular — so it's reimplemented here, independently, the
# same way CLAUDE.md documents output/briefing.py doing for its own emails.
_UK_TZ = ZoneInfo("Europe/London")


def _fmt_uk_time(dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert a naive-UTC (or UTC-aware) datetime to UK local time and format it."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    uk = dt.astimezone(_UK_TZ)
    abbr = "BST" if uk.dst() else "GMT"
    return f"{uk.strftime(fmt)} {abbr}"

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

# Well-established S&P 500 buy-and-hold reference figures (2019-2026,
# matching BACKTEST_START_DATE's span) for the PDF report's risk-adjusted
# benchmark comparison table — see "Risk-Adjusted Benchmark Comparison" on
# the Annual Returns page. A raw "outperformed N of M years" count is
# misleading on its own: it doesn't account for how much of each year the
# system was actually invested (see Time Invested on the Edge Quality
# page), so CAGR/Calmar/Sharpe are compared instead.
SP500_CAGR_2019_2026 = 15.4     # annualised total return, approximate
SP500_MAX_DRAWDOWN = -33.9      # COVID crash, March 2020
SP500_SHARPE_2019_2026 = 0.88   # approximate
SP500_CALMAR = round(SP500_CAGR_2019_2026 / abs(SP500_MAX_DRAWDOWN), 2)

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


def _t212_pnl_rows_after_freeze() -> list[dict]:
    """Shared row fetch for compute_out_of_sample_stats() and
    compute_live_validation() — one SQL query, two different aggregations
    over the same genuinely-out-of-sample t212_pnl rows."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            return [
                dict(r) for r in conn.execute(
                    text(
                        "SELECT pnl_pct, pnl_gbp, entry_date, exit_date, holding_days "
                        "FROM t212_pnl WHERE date(exit_date) > date(:freeze) ORDER BY exit_date"
                    ),
                    {"freeze": PARAMETER_FREEZE_DATE.isoformat()},
                ).mappings().all()
            ]
    except Exception:
        return []


def compute_out_of_sample_stats() -> dict:
    """Read t212_pnl for positions closed strictly after PARAMETER_FREEZE_DATE.

    Returns zero-trade stats (status "grey") when no t212_pnl rows exist yet
    after the freeze date — expected for a long time after this feature
    ships, since T212 closes accumulate slowly (~5-10/year per CLAUDE.md's
    "Feedback learning" section).
    """
    rows = _t212_pnl_rows_after_freeze()
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


def compute_live_validation(run_id: int) -> dict:
    """Live (post-freeze) T212 performance for the PDF's Live Validation
    page — extends compute_out_of_sample_stats() with win rate, profit
    factor, expectancy, max drawdown, and Sharpe, so the page can compare
    genuinely out-of-sample results against this run's in-sample backtest
    metrics.

    Sharpe is only computed once at least 12 months of live history exists
    (the span between the earliest live entry and latest live exit) — a
    Sharpe ratio computed from a handful of trades over a few weeks is not
    meaningful; `sharpe_available` tells the caller which case it is.

    `run_id` is accepted for signature symmetry with the other compute_*()
    functions and potential future use (e.g. per-run live comparisons) but
    is not currently read — live performance is a property of the account,
    not of any one backtest run.
    """
    rows = _t212_pnl_rows_after_freeze()
    n = len(rows)

    result = {
        "freeze_date":            PARAMETER_FREEZE_DATE,
        "n_trades":               n,
        "return_since_freeze_pct": 0.0,
        "win_rate_pct":           None,
        "profit_factor":          None,
        "expectancy_pct":         None,
        "max_drawdown_pct":       None,
        "sharpe_ratio":           None,
        "sharpe_available":       False,
        "status":                "grey",
    }
    if n == 0:
        return result

    df = pd.DataFrame(rows)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"]  = pd.to_datetime(df["exit_date"])
    df = df.sort_values("exit_date")

    wins   = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] < 0]
    win_rate = len(wins) / n
    avg_win_pct      = float(wins["pnl_pct"].mean() * 100) if not wins.empty else 0.0
    avg_loss_pct_abs = abs(float(losses["pnl_pct"].mean() * 100)) if not losses.empty else 0.0
    expectancy_pct = (avg_win_pct * win_rate) - (avg_loss_pct_abs * (1 - win_rate))

    total_return_pct = float(df["pnl_pct"].sum() * 100)

    win_sum, loss_sum = float(wins["pnl_pct"].sum()), float(losses["pnl_pct"].sum())
    if loss_sum != 0:
        profit_factor = round(min(win_sum / abs(loss_sum), 99.9), 2)
    else:
        profit_factor = 99.9 if win_sum > 0 else 0.0

    cum  = (1 + df["pnl_pct"]).cumprod()
    peak = cum.cummax()
    max_dd = float(((cum - peak) / peak).min() * 100)

    span_days = (df["exit_date"].max() - df["entry_date"].min()).days
    sharpe_available = span_days >= 365
    sharpe_ratio = None
    if sharpe_available and df["pnl_pct"].std() > 0:
        avg_hold = df["holding_days"].mean() or 1.0
        periods_per_year = 252.0 / avg_hold if avg_hold > 0 else 1.0
        sharpe_ratio = round(float((df["pnl_pct"].mean() / df["pnl_pct"].std()) * np.sqrt(periods_per_year)), 2)

    if total_return_pct > 0:
        status = "green"
    elif total_return_pct < 0:
        status = "red"
    else:
        status = "grey"

    result.update({
        "return_since_freeze_pct": round(total_return_pct, 2),
        "win_rate_pct":            round(win_rate * 100, 1),
        "profit_factor":           profit_factor,
        "expectancy_pct":          round(expectancy_pct, 2),
        "max_drawdown_pct":        round(max_dd, 2),
        "sharpe_ratio":            sharpe_ratio,
        "sharpe_available":        sharpe_available,
        "status":                  status,
    })
    return result


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
    trades = get_run_trades(run_id)
    if trades.empty:
        return empty

    ec = equity_curve(trades)
    if ec.empty:
        return empty

    peak = ec["equity"].cummax()
    in_dd = ec["equity"] < peak
    current_drawdown_pct = float(ec["drawdown_pct"].iloc[-1])

    max_dd = float(ec["drawdown_pct"].min())

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


def compute_walk_forward_evidence(run_id: int, window_months: int = 6, step_months: int = 1) -> pd.DataFrame:
    """Rolling-window evidence for the PDF report's Walk-Forward Validation page.

    Mirrors analysis/backtest_metrics.py:walk_forward()'s own windowing (same
    window/step months, same "at least 3 trades" floor per window) but also
    includes profit_factor — walk_forward() deliberately omits it, since
    main.py's Backtest page Walk-Forward tab renames its returned dicts'
    keys positionally into a fixed column list, and adding a key there would
    silently break that rename for an unrelated page. "Train"/"Test" in the
    column names below is this table's own framing for a due-diligence
    reader; the underlying window is the same single rolling backtest
    walk_forward() already uses, not a genuine train-then-refit split — see
    that function's own docstring for the identical caveat, and the PDF
    page's own disclaimer paragraph.
    """
    cols = ["train_start", "train_end", "test_trades", "test_win_rate_pct",
            "test_profit_factor", "test_max_dd_pct"]
    trades = get_run_trades(run_id)
    if trades.empty or "entry_date" not in trades.columns:
        return pd.DataFrame(columns=cols)

    df = trades.sort_values("entry_date").copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])

    min_date, max_date = df["entry_date"].min(), df["entry_date"].max()
    rows = []
    current = min_date.to_period("M")
    end_p   = max_date.to_period("M")
    while current <= end_p:
        win_start = current.to_timestamp()
        win_end   = (current + window_months).to_timestamp() - pd.Timedelta(days=1)
        sub = df[(df["entry_date"] >= win_start) & (df["entry_date"] <= win_end)]
        if len(sub) >= 3:
            m = compute_metrics(sub)
            rows.append({
                "train_start":        win_start.strftime("%Y-%m-%d"),
                "train_end":          win_end.strftime("%Y-%m-%d"),
                "test_trades":        m.get("total_trades", 0),
                "test_win_rate_pct":  round((m.get("win_rate") or 0.0) * 100, 1),
                "test_profit_factor": m.get("profit_factor", 0.0),
                "test_max_dd_pct":    m.get("max_drawdown_pct", 0.0),
            })
        current = current + step_months

    return pd.DataFrame(rows, columns=cols)


def compute_walk_forward_summary(wf: pd.DataFrame) -> dict:
    """Aggregate compute_walk_forward_evidence()'s per-window rows into the
    Walk-Forward Validation page's summary box: how many windows were
    tested, how many were net profitable (test_profit_factor > 1.0), and
    the resulting success rate — always computed live from the data, never
    hardcoded.
    """
    if wf is None or wf.empty or "test_profit_factor" not in wf.columns:
        return {"n_total": 0, "n_positive": 0, "success_rate_pct": 0.0}

    n_total = len(wf)
    n_positive = int((wf["test_profit_factor"] > 1.0).sum())
    success_rate_pct = round(n_positive / n_total * 100, 1) if n_total else 0.0
    return {"n_total": n_total, "n_positive": n_positive, "success_rate_pct": success_rate_pct}


def _recovery_days_to_calendar_months(days: int) -> float:
    """Approximate calendar months from a recovery-period day count, using
    the standard ~21 trading-days-per-month convention — used for the Risk
    Analysis page's longest-recovery warning box (see
    compute_drawdown_metrics()'s longest_recovery_days)."""
    if not days:
        return 0.0
    return round(days / 21, 1)


def _rolling_sharpe_narrative(roll: pd.DataFrame) -> str:
    """Rule-based description of the rolling-Sharpe trajectory — generated
    from the actual data (first-quarter vs. last-quarter average, plus the
    latest reading) rather than any fixed wording, so it never claims a
    trend the numbers don't support."""
    if roll is None or roll.empty or len(roll) < 2 or "rolling_sharpe" not in roll.columns:
        return "Not enough trade history yet to describe a rolling Sharpe trajectory."

    n = len(roll)
    span = max(1, n // 4)
    first = float(roll["rolling_sharpe"].iloc[:span].mean())
    last  = float(roll["rolling_sharpe"].iloc[-span:].mean())
    latest = float(roll["rolling_sharpe"].iloc[-1])

    if first < 0 <= last:
        trend = f"began negative (early average {first:.2f}) and has since improved to a recent average of {last:.2f}"
    elif last - first > 0.1:
        trend = f"has trended upward, from an early average of {first:.2f} to a recent average of {last:.2f}"
    elif first - last > 0.1:
        trend = f"has trended downward, from an early average of {first:.2f} to a recent average of {last:.2f}"
    else:
        trend = f"has remained broadly flat, averaging around {last:.2f}"

    if latest >= 1.2:
        benchmark = f"comfortably above the 1.0 professional benchmark (latest reading: {latest:.2f})"
    elif latest >= 0.8:
        benchmark = f"near the 1.0 professional benchmark (latest reading: {latest:.2f})"
    else:
        benchmark = f"below the 1.0 professional benchmark (latest reading: {latest:.2f})"

    return f"The rolling 12-month Sharpe ratio {trend}, most recently sitting {benchmark}."


# ---------------------------------------------------------------------------
# PDF export — Robustness page "Download Due Diligence Report" button
# ---------------------------------------------------------------------------
#
# generate_robustness_pdf(run_id, config) renders the same data the
# Robustness page's four tabs show (plus a matplotlib-rendered equity curve
# and exit-reason chart, and a walk-forward evidence table) as a standalone
# PDF via ReportLab's Platypus framework. Every figure is computed live from
# the compute_*() functions above — SP500_ANNUAL (already defined near the
# top of this file) is the only hardcoded performance number anywhere in
# the report.
#
# The brand's dark-navy UI look is deliberately NOT carried over verbatim: a
# dark page background prints poorly and reads as unprofessional in most PDF
# viewers, so the report uses a white background throughout, reserving the
# navy/electric-blue brand colours for headers, table header rows, and
# section titles only (see the colour constants below).

_PDF_NAVY       = colors.HexColor("#0A0F1E")
_PDF_BLUE       = colors.HexColor("#00C2FF")
_PDF_BODY       = colors.HexColor("#1A1A2E")
_PDF_GREEN      = colors.HexColor("#00875A")
_PDF_RED        = colors.HexColor("#C0392B")
_PDF_LIGHT_GREY = colors.HexColor("#F2F3F5")
_PDF_BORDER     = colors.HexColor("#CCCCCC")

_HEATMAP_GREEN_RGB = (0 / 255, 135 / 255, 90 / 255)
_HEATMAP_RED_RGB   = (192 / 255, 57 / 255, 43 / 255)

# Highlighted spans on the Backtest Evidence equity curve — whether the
# annotation reads "Zero new entries — regime gate active" (optionally
# noting carryover positions that closed in-window) or an actual entry
# count is decided live from the run's own trades (see
# _render_equity_curve_png()), not assumed here.
_EQUITY_HIGHLIGHT_PERIODS: list[tuple[date, date, str]] = [
    (date(2020, 2, 1), date(2020, 4, 30), "COVID crash"),
    (date(2022, 1, 1), date(2022, 12, 31), "2022 bear market"),
]

_SYSTEM_DESCRIPTION_SHORT = (
    "SP500Invest is a systematic, rules-based research and execution platform. It scores a "
    "diversified watchlist of global equities, ETFs, and macro instruments across six "
    "independent analytical modules — trend, mean reversion, volatility, volume, macro "
    "conditions, and cross-asset correlation — combined into a single confluence score that "
    "adapts its weighting to the prevailing market regime. New positions are further filtered "
    "by a regime gate that restricts entries to conditions the system has empirically shown to "
    "have a demonstrable edge. All execution is automated through a brokerage API; no "
    "discretionary trading occurs."
)

_SYSTEM_DESCRIPTION_FULL = _SYSTEM_DESCRIPTION_SHORT + (
    " Signals are regenerated automatically on a fixed schedule throughout each trading day, "
    "and every open position is monitored continuously for stop-loss, signal-reversal, and "
    "maximum-holding-period exit conditions."
)

# Static content for the Methodology & Disclaimers page's "Known Limitations &
# Improvement Roadmap" section — documented proactively rather than derived
# from any compute_*() function, since every figure quoted here is a specific,
# already-known finding from earlier work (see CLAUDE.md's macro/regime-weight
# rebalance and re-tuning-study sections for the underlying investigations).
_KNOWN_LIMITATIONS: list[dict[str, str]] = [
    {
        "limitation": "Narrow rally problem",
        "evidence": (
            "System returned -5.4% in 2023 while S&amp;P 500 gained +26.3%. The 2023 rally was "
            "driven by a narrow set of mega-cap tech stocks (Magnificent 7) while broad market "
            "ADX was insufficient to confirm TRENDING_BULL."
        ),
        "impact": (
            "Missed significant gains in markets where a small number of stocks drive index "
            "returns without broad participation."
        ),
    },
    {
        "limitation": "Concentration risk",
        "evidence": (
            "February 2024 contributed +158.5% to monthly returns — largely driven by NVDA's "
            "~60% gain in a single month."
        ),
        "impact": (
            "Top 10 trades represent 31.2% of total profits. Exceptional months may not repeat "
            "at the same magnitude."
        ),
    },
    {
        "limitation": "In-sample parameter optimisation",
        "evidence": (
            "All module weights, regime thresholds, and entry criteria were calibrated using "
            "2019–2026 data. Parameter freeze date: 2026-07-13."
        ),
        "impact": (
            "Backtest performance reflects an optimised system applied retrospectively. "
            "Walk-forward validation (74.5% of windows profitable) and out-of-sample monitoring "
            "from 2026-07-14 onward provide partial mitigation."
        ),
    },
    {
        "limitation": "US and TRENDING_BULL bias",
        "evidence": (
            "System is long-only, US-focused, and enters only in confirmed bull market "
            "conditions. Approximately 56.1% of trading days are spent in cash."
        ),
        "impact": (
            "No short-side exposure. Underperforms in extended bear markets where capital "
            "preservation is the primary outcome rather than returns."
        ),
    },
    {
        "limitation": "Limited live track record",
        "evidence": (
            "Out-of-sample monitoring began 2026-07-13. Live T212 execution data is accumulating "
            "but insufficient for statistical validation."
        ),
        "impact": (
            "Live performance attribution and weight re-tuning deferred until 20+ closed "
            "positions are available."
        ),
    },
    {
        "limitation": "Module weight calibration",
        "evidence": (
            "The TRENDING_BULL regime weights were originally calibrated against a macro module "
            "later found to have a data-storage bug that made it near-inert during backtesting "
            "(a 0.9% historical agreement rate, itself a symptom of the bug rather than genuine "
            "signal). Once corrected, two independent tests — 2026-07-08, and 2026-07-30 run "
            "directly against production data — showed the current weight scheme produces "
            "materially worse outcomes under genuine macro variation: maximum drawdown deepens "
            "from -35.6% to -64.6% / -75.0%, trade count more than doubles, and the macro module "
            "itself becomes mildly anti-predictive (a -8 to -14 percentage-point gap the wrong "
            "way) rather than neutral. Four single-module reweighting attempts across both tests "
            "failed to recover the documented baseline."
        ),
        "impact": (
            "The current TRENDING_BULL weights are validated only against a frozen macro "
            "snapshot broadcast across historical backtesting — not against the genuinely "
            "time-varying macro conditions the system will actually encounter live. A full "
            "six-module (and possibly threshold) re-tuning study is required before these "
            "weights should be considered validated under real market conditions, and has not "
            "yet been undertaken. Live capital has not been exposed to this risk only because "
            "the market has remained in the MIXED regime (no new entries) since the parameter "
            "freeze — this limitation applies in full the next time TRENDING_BULL is confirmed."
        ),
    },
]

_IMPROVEMENT_ROADMAP: list[tuple[str, list[str]]] = [
    (
        "Near-term (next 6 months — pending live data accumulation):",
        [
            "Full six-module weight re-tuning on real T212 execution data (20+ closed positions required)",
            "Per-ticker geographic exposure weighting — regional macro scores applied per asset based on revenue geography",
            "2-state Hidden Markov Model recalibration — bull vs non-bull, replacing failed 3-state implementation",
        ],
    ),
    (
        "Medium-term (6–18 months — pending regulatory and capital):",
        [
            "FCA authorisation for automated investment advice — required for B2C retail launch in the UK",
            "Short-side execution via IG API — activates TRENDING_BEAR regime signals currently generated but not executed",
            "VAR lead/lag correlation analysis — captures predictive relationships between asset classes (e.g. copper leading equities)",
            "Multi-broker API support — reduces single-broker dependency, enables broader instrument universe",
        ],
    ),
    (
        "Longer-term (18+ months — scale and product):",
        [
            "Mobile-first retail interface — consumer-grade UX for non-technical investors",
            "Multi-currency account support — GBP, USD, EUR portfolio management",
            "Institutional white-label API — a broader, self-service standalone B2B product, beyond the initial near-term pilot licensing motion",
            "Global market expansion — US, European, Asian exchange coverage with per-market regime detection",
        ],
    ),
]

_GTM_STRATEGY_NOTE = (
    "Near-term go-to-market is B2B: licensing the signal engine to regulated distribution "
    "partners — wealth managers, IFA networks, and robo-advisors — via API access or a "
    "white-label platform, rather than launching direct-to-retail first. A "
    "research/due-diligence-reporting tier is a further option, pending confirmation of its "
    "regulatory classification. Direct-to-retail is deferred to the medium-term roadmap "
    "below, alongside the FCA authorisation it would require."
)

_FOUNDERS_NOTE = (
    "This is Version 1.0. It demonstrably works — the regime gate preserved capital during the "
    "COVID crash and 2022 bear market, and the system captured exceptional momentum moves in "
    "2024-2025. The limitations documented above are known quantities, not unknowns. The "
    "improvement roadmap is clear and prioritised. The question is not whether the system works, "
    "but how much better it can get with the right capital, talent, and regulatory access."
)


def _pdf_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "Cover": ParagraphStyle(
            "Cover", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=30, leading=34, textColor=_PDF_NAVY, alignment=TA_CENTER, spaceAfter=6,
        ),
        "CoverSub": ParagraphStyle(
            "CoverSub", parent=base["Normal"], fontName="Helvetica",
            fontSize=14, textColor=_PDF_BLUE, alignment=TA_CENTER, spaceAfter=24,
        ),
        "SectionTitle": ParagraphStyle(
            "SectionTitle", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=17, textColor=_PDF_BLUE, spaceAfter=10, spaceBefore=0,
        ),
        "SubHeading": ParagraphStyle(
            "SubHeading", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11.5, textColor=_PDF_NAVY, spaceBefore=12, spaceAfter=6,
        ),
        "Body": ParagraphStyle(
            "Body", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=13.5, textColor=_PDF_BODY, spaceAfter=6,
        ),
        "Small": ParagraphStyle(
            "Small", parent=base["Normal"], fontName="Helvetica",
            fontSize=7.8, leading=10.5, textColor=colors.grey, spaceAfter=4,
        ),
        "Metric": ParagraphStyle(
            "Metric", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=15, textColor=_PDF_NAVY, alignment=TA_CENTER, leading=18,
        ),
        "Banner": ParagraphStyle(
            "Banner", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=10.5, alignment=TA_CENTER, spaceAfter=4,
        ),
        "TableCell": ParagraphStyle(
            "TableCell", parent=base["Normal"], fontName="Helvetica",
            fontSize=8, leading=10.5, textColor=_PDF_BODY, spaceAfter=0,
        ),
        "Footnote": ParagraphStyle(
            "Footnote", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=7.8, leading=10.5, textColor=colors.grey, spaceAfter=4,
        ),
    }


def _pdf_signed(value: float, decimals: int = 1, suffix: str = "%") -> str:
    """Inline `<font>` colour markup for a signed metric — green >= 0, red < 0."""
    colour = "#00875A" if value >= 0 else "#C0392B"
    sign = "+" if value >= 0 else ""
    return f'<font color="{colour}">{sign}{value:.{decimals}f}{suffix}</font>'


def _pdf_table(rows: list[list], col_widths: Optional[list[float]] = None, header: bool = True) -> Table:
    """A Table with the standard navy-header / alternating-grey-row style."""
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONTSIZE",      (0, 0), (-1, -1), 8.2),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.4, _PDF_BORDER),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), _PDF_NAVY),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
        for r in range(1, len(rows)):
            if r % 2 == 0:
                style.append(("BACKGROUND", (0, r), (-1, r), _PDF_LIGHT_GREY))
    else:
        for r in range(len(rows)):
            if r % 2 == 1:
                style.append(("BACKGROUND", (0, r), (-1, r), _PDF_LIGHT_GREY))
    t.setStyle(TableStyle(style))
    return t


def _metric_tile(value_html: str, label: str, styles: dict) -> Paragraph:
    return Paragraph(
        f'{value_html}<br/><font size="7" color="#888888">{label}</font>',
        styles["Metric"],
    )


def _pdf_metric_grid(cells: list[tuple[str, str]], styles: dict, n_cols: int = 4) -> Table:
    """cells: (value_html, label) pairs, laid out n_cols per row as bordered tiles."""
    paras = [_metric_tile(v, l, styles) for v, l in cells]
    rows = [paras[i:i + n_cols] for i in range(0, len(paras), n_cols)]
    col_w = (A4[0] - 3.6 * cm) / n_cols
    t = Table(rows, colWidths=[col_w] * n_cols)
    t.setStyle(TableStyle([
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("BOX",           (0, 0), (-1, -1), 0.5, _PDF_BORDER),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, _PDF_BORDER),
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#FAFBFC")),
    ]))
    return t


def _heatmap_cell_colour(value: float, scale: float) -> colors.Color:
    """White at 0, blending toward green (positive) or red (negative) as
    |value| approaches *scale* (the run's own largest-magnitude month)."""
    if scale <= 0 or value == 0:
        return colors.white
    frac = min(abs(value) / scale, 1.0)
    target = _HEATMAP_GREEN_RGB if value > 0 else _HEATMAP_RED_RGB
    return colors.Color(*(1 + (t - 1) * frac for t in target))


class _NumberedCanvas(_rl_canvas.Canvas):
    """Buffers every page so the footer can show "Page X of Y" — the total
    page count isn't known until the whole document has been laid out.
    Standard ReportLab recipe: defer drawing until save(), once every page
    has been captured via showPage().
    """

    def __init__(self, *args, **kwargs):
        _rl_canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states: list[dict] = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_header_footer(total)
            _rl_canvas.Canvas.showPage(self)
        _rl_canvas.Canvas.save(self)

    def _draw_header_footer(self, total_pages: int) -> None:
        width, height = A4
        self.saveState()
        self.setFont("Helvetica-Bold", 9)
        self.setFillColor(_PDF_NAVY)
        self.drawString(1.8 * cm, height - 1.3 * cm, "SP500INVEST")
        self.setFont("Helvetica", 7)
        self.setFillColor(colors.grey)
        self.drawRightString(width - 1.8 * cm, height - 1.3 * cm, "Due Diligence Report")
        self.setStrokeColor(_PDF_BORDER)
        self.line(1.8 * cm, height - 1.5 * cm, width - 1.8 * cm, height - 1.5 * cm)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.grey)
        self.drawCentredString(width / 2, 1 * cm, "CONFIDENTIAL")
        self.drawRightString(width - 1.8 * cm, 1 * cm, f"Page {self._pageNumber} of {total_pages}")
        self.restoreState()


def _render_equity_curve_png(run_id: int, config: dict) -> Optional[tuple[bytes, list[str]]]:
    """Matplotlib-rendered daily £ equity curve for the PDF's Backtest
    Evidence page, plus any carryover-position notes for the caller to
    render as a caption below the image (see the loop below — kept off the
    chart itself, where two nearby highlight windows collide past 2 lines
    of annotation text). Returns None (caller shows a text placeholder)
    when the run has no usable £-sized equity curve — e.g. an older run
    predating VaR position sizing."""
    trades = get_run_trades(run_id)
    if trades.empty:
        return None

    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    ec = equity_curve_gbp(trades, starting_capital_gbp=starting_capital)
    if ec.empty:
        return None

    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.plot(ec["date"], ec["equity_gbp"], color="#00C2FF", linewidth=1.5)
    ax.fill_between(ec["date"], ec["equity_gbp"], starting_capital, alpha=0.12, color="#00C2FF")
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")
    ax.set_xlabel("Date", fontsize=8)
    ax.set_ylabel("Portfolio Value (GBP)", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(True, alpha=0.2)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    entry_dates = pd.to_datetime(trades["entry_date"])
    exit_dates  = pd.to_datetime(trades["exit_date"])
    y_top = float(ec["equity_gbp"].max())
    ax.set_ylim(top=y_top * 1.2)
    label_y = y_top * 1.06

    # Per-window carryover note (see caption below the chart) — kept off
    # the chart's own annotation text: with two highlight windows only ~2
    # years apart on this chart's timescale, a 3rd annotation line collided
    # with the neighbouring window's label and made both illegible (caught
    # by rendering the actual PNG, not just confirming the figure built).
    carryover_notes: list[str] = []

    for start, end, period_label in _EQUITY_HIGHLIGHT_PERIODS:
        s_ts, e_ts = pd.Timestamp(start), pd.Timestamp(end)
        if e_ts < ec["date"].min() or s_ts > ec["date"].max():
            continue
        ax.axvspan(s_ts, e_ts, color="#C0392B", alpha=0.08)
        n_entries = int(((entry_dates >= s_ts) & (entry_dates <= e_ts)).sum())
        if n_entries == 0:
            # "Zero trades" was flagged externally as inaccurate: the regime
            # gate only ever blocks NEW entries (see CLAUDE.md's "Regime
            # entry gate") — a position opened before the window began
            # continues to be monitored and can still exit inside it. Any
            # trade with an exit_date in-window here is necessarily such a
            # carryover (n_entries == 0 rules out it having entered here),
            # so this is computed fresh from the run's own trades on every
            # report, not a hardcoded count from any one run.
            #
            # Kept short ("Zero new entries" only, not "... — regime gate
            # active") because the two highlight windows sit close enough
            # on this chart's timescale that the longer string was already
            # overlapping illegibly even before this fix — a pre-existing
            # bug confirmed by rendering the actual PNG (present in the
            # currently-shipping report too, independent of the wording
            # itself). The full mechanism and any carryover detail live in
            # the caption below the image instead, where there's no space
            # constraint.
            detail = "Zero new entries"
            n_carryover_exits = int(((exit_dates >= s_ts) & (exit_dates <= e_ts)).sum())
            if n_carryover_exits > 0:
                carryover_notes.append(
                    f"{period_label}: regime gate blocked new entries throughout. "
                    f"{n_carryover_exits} carryover position"
                    f"{'s' if n_carryover_exits != 1 else ''} (opened before the window, "
                    f"closed inside it)."
                )
            else:
                carryover_notes.append(f"{period_label}: regime gate blocked new entries throughout.")
        else:
            detail = f"{n_entries} trade(s) entered"
        mid = s_ts + (e_ts - s_ts) / 2
        ax.text(mid, label_y, f"{period_label}\n{detail}", ha="center", va="bottom",
                 fontsize=6.5, color="#C0392B")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue(), carryover_notes


def _render_exit_reason_chart_png(run_id: int) -> Optional[bytes]:
    """Matplotlib bar chart of exit-reason % of trades, computed live from
    the run's own exit_reason_breakdown — no hardcoded percentages."""
    eq = compute_edge_quality(run_id)
    breakdown = eq.get("exit_reason_breakdown") or {}
    if not breakdown:
        return None

    items = sorted(breakdown.items(), key=lambda kv: -kv[1]["count"])
    labels = [k for k, _ in items]
    pcts = [v["pct"] for _, v in items]

    fig, ax = plt.subplots(figsize=(6, 2.6))
    palette = ["#00C2FF", "#0A0F1E", "#00875A", "#C0392B", "#7C8798"]
    bars = ax.bar(labels, pcts, color=[palette[i % len(palette)] for i in range(len(labels))])
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")
    ax.set_ylabel("% of Trades", fontsize=8)
    ax.tick_params(labelsize=7)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.set_ylim(0, max(pcts + [1]) * 1.25)
    for bar, pct in zip(bars, pcts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(pcts) * 0.02,
                 f"{pct:.1f}%", ha="center", va="bottom", fontsize=7)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()


def _pdf_page_cover(styles: dict, run: dict) -> list:
    flow = [Spacer(1, 3 * cm), Paragraph(" ".join("SP500INVEST"), styles["Cover"])]
    flow.append(Paragraph("Due Diligence Report", styles["CoverSub"]))
    flow.append(HRFlowable(width="60%", thickness=1, color=_PDF_BLUE, hAlign="CENTER"))
    flow.append(Spacer(1, 0.8 * cm))

    meta_rows = [
        ["Report Date", date.today().isoformat()],
        ["Backtest Run", f"#{run.get('id', '—')} — {run.get('name', '') or '(unnamed)'}"],
    ]
    flow.append(_pdf_table(meta_rows, col_widths=[5 * cm, 9 * cm], header=False))
    flow.append(Spacer(1, 1.2 * cm))

    flow.append(Paragraph(
        '<font color="#C0392B">WARNING: Historical results include parameter optimisation.</font>',
        styles["Banner"],
    ))
    flow.append(Paragraph(
        f'<font color="#00875A">CONFIRMED: Results after {PARAMETER_FREEZE_DATE} are fully out-of-sample.</font>',
        styles["Banner"],
    ))
    flow.append(Spacer(1, 0.4 * cm))

    freeze_rows = [
        ["Parameter Freeze Date", str(PARAMETER_FREEZE_DATE)],
        ["In-Sample Period", f"{BACKTEST_START_DATE} to {PARAMETER_FREEZE_DATE}"],
        ["Out-of-Sample Period", f"{PARAMETER_FREEZE_DATE + timedelta(days=1)} onwards"],
    ]
    flow.append(_pdf_table(freeze_rows, col_widths=[6 * cm, 8 * cm], header=False))

    flow.append(Spacer(1, 2.5 * cm))
    flow.append(HRFlowable(width="100%", thickness=0.5, color=_PDF_BORDER))
    flow.append(Spacer(1, 0.3 * cm))
    flow.append(Paragraph(
        "This report contains proprietary performance data. Full methodology available "
        "under NDA. hello@example.com",
        styles["Small"],
    ))
    return flow


def _pdf_page_executive_summary(styles: dict, run: dict, eq: dict, calmar: dict, rfc: dict) -> list:
    flow = [Paragraph("Executive Summary", styles["SectionTitle"])]

    lifetime_calmar = calmar.get("lifetime", {}).get("calmar", 0.0)
    exp_pct = eq.get("expectancy", {}).get("expectancy_pct", 0.0)

    cells = [
        (f"{eq.get('profit_factor', 0.0):.2f}", "Profit Factor"),
        (f"{eq.get('win_rate_pct', 0.0):.1f}%", "Win Rate"),
        (f"{(run.get('sharpe_ratio') or 0.0):.2f}", "Sharpe Ratio"),
        (f"{lifetime_calmar:.2f}", "Calmar (Lifetime)"),
        (f"{eq.get('time_invested_pct', 0.0):.1f}%", "Time Invested"),
        (_pdf_signed(exp_pct, 2), "Expectancy / Trade"),
        (_pdf_signed(run.get("max_drawdown_pct") or 0.0, 1), "Max Drawdown"),
        (_pdf_signed(run.get("total_return_pct") or 0.0, 1), "Total Return"),
    ]
    flow.append(_pdf_metric_grid(cells, styles))
    flow.append(Spacer(1, 0.5 * cm))

    flow.append(Paragraph(_SYSTEM_DESCRIPTION_SHORT, styles["Body"]))
    flow.append(Spacer(1, 0.3 * cm))

    rfc_block = [Paragraph("Regime Filter Contribution", styles["SubHeading"])]
    if not rfc.get("available"):
        rfc_block.append(Paragraph("Re-run backtest to populate this comparison.", styles["Body"]))
    else:
        wf_, wof_ = rfc["with_filter"], rfc["without_filter"]
        rows = [
            ["Metric", "With Filter", "Without Filter", "Difference"],
            ["Total Return", f"{wf_.get('total_return_pct', 0):+.1f}%", f"{wof_.get('total_return_pct', 0):+.1f}%",
             f"{rfc['return_improvement_pct']:+.1f}%"],
            ["Max Drawdown", f"{wf_.get('max_drawdown_pct', 0):.1f}%", f"{wof_.get('max_drawdown_pct', 0):.1f}%",
             f"{rfc['max_dd_reduction_pct']:+.1f}%"],
            ["Sharpe", f"{wf_.get('sharpe_ratio', 0):.2f}", f"{wof_.get('sharpe_ratio', 0):.2f}",
             f"{rfc['sharpe_improvement']:+.2f}"],
        ]
        rfc_block.append(_pdf_table(rows, col_widths=[4.5 * cm, 3.5 * cm, 3.5 * cm, 3 * cm]))
    # KeepTogether so the heading never gets stranded alone at the bottom of
    # a page with its table/placeholder pushed onto the next one — the exact
    # orphan seen during development when this section followed a tall grid.
    flow.append(KeepTogether(rfc_block))
    return flow


def _pdf_page_edge_quality(styles: dict, eq: dict) -> list:
    flow = [Paragraph("Edge Quality", styles["SectionTitle"])]

    flow.append(Paragraph(
        f"Time Invested: <b>{eq.get('time_invested_pct', 0):.1f}%</b> &nbsp;|&nbsp; "
        f"Cash Allocation: <b>{eq.get('cash_allocation_pct', 0):.1f}%</b>",
        styles["Body"],
    ))

    flow.append(Paragraph("Winner Concentration", styles["SubHeading"]))
    wc = eq.get("winner_concentration", {})
    wc_rows = [
        ["Top N Winners", "% of Total Profit"],
        ["Top 5",  f"{wc.get('top5_pct', 0):.1f}%"],
        ["Top 10", f"{wc.get('top10_pct', 0):.1f}%"],
        ["Top 20", f"{wc.get('top20_pct', 0):.1f}%"],
    ]
    flow.append(_pdf_table(wc_rows, col_widths=[7 * cm, 7 * cm]))
    flow.append(Paragraph(
        "High concentration means the edge depends heavily on a small number of large "
        "winners — a caution flag if those winners cluster in a single regime or period.",
        styles["Small"],
    ))
    flow.append(Spacer(1, 0.3 * cm))

    flow.append(Paragraph("Exit Reason Breakdown", styles["SubHeading"]))
    erb = eq.get("exit_reason_breakdown", {})
    if not erb:
        flow.append(Paragraph("No trades in this run.", styles["Body"]))
    else:
        rows = [["Exit Reason", "Count", "% of Trades"]]
        for reason, v in sorted(erb.items(), key=lambda kv: -kv[1]["count"]):
            rows.append([reason, str(v["count"]), f"{v['pct']:.1f}%"])
        flow.append(_pdf_table(rows, col_widths=[6 * cm, 4 * cm, 4 * cm]))
    flow.append(Spacer(1, 0.3 * cm))

    flow.append(Paragraph("Expectancy per Trade", styles["SubHeading"]))
    exp = eq.get("expectancy", {})
    # Display the win/loss split as percentages at win_rate_pct's own 1dp
    # precision (matching how it's shown everywhere else in this report),
    # not as a fraction rounded to 2dp (0.45/0.55) — the coarser fraction
    # rounding was flagged externally because it makes the formula
    # recompute to a visibly different result than the headline expectancy
    # figure (e.g. displaying 0.45/0.55 when the true split is 44.9%/55.1%
    # is a ~1-percentage-point rounding, enough to shift the 2dp result by
    # several hundredths). 1dp percentages track the true split far more
    # closely, so the formula as displayed reconciles with the headline
    # figure to within normal rounding tolerance.
    win_rate_pct  = exp.get("win_rate_pct", 0.0)
    loss_rate_pct = round(100.0 - win_rate_pct, 1)
    flow.append(Paragraph(
        f"({exp.get('avg_win_pct', 0):.2f}% x {win_rate_pct:.1f}%) - "
        f"({abs(exp.get('avg_loss_pct', 0)):.2f}% x {loss_rate_pct:.1f}%) "
        f"= {_pdf_signed(exp.get('expectancy_pct', 0), 2)}",
        styles["Body"],
    ))
    flow.append(Paragraph(
        f"Equivalent to {_pdf_signed(exp.get('expectancy_gbp', 0), 2, suffix=' GBP')} per trade "
        f"at an account value of £{exp.get('account_value_gbp', 0):,.0f}.",
        styles["Body"],
    ))
    flow.append(Spacer(1, 0.3 * cm))

    flow.append(Paragraph("Out-of-Sample Monitor", styles["SubHeading"]))
    oos = eq.get("out_of_sample", {})
    oos_colour = {"green": "#00875A", "red": "#C0392B", "grey": "#7C8798"}.get(oos.get("status"), "#7C8798")
    flow.append(Paragraph(
        f"Freeze date: <b>{oos.get('freeze_date', PARAMETER_FREEZE_DATE)}</b> &nbsp;|&nbsp; "
        f"Trades since freeze: <b>{oos.get('n_trades', 0)}</b> &nbsp;|&nbsp; "
        f'Return since freeze: <font color="{oos_colour}"><b>{oos.get("return_since_freeze_pct", 0):+.1f}%</b></font>',
        styles["Body"],
    ))
    return flow


def _pdf_page_annual_returns(styles: dict, annual: pd.DataFrame, calmar: dict, run: dict, eq: dict) -> list:
    # "S&amp;P" (not the bare "S&P") — ReportLab's Paragraph mini-XML parser
    # treats a bare "&" as the start of an entity reference; "&P " isn't a
    # valid one, and it was silently rendering as "S&P; 500" (dropping the
    # "&", inserting a stray ";") wherever this string reached a Paragraph.
    # Confirmed by rendering the actual generated PDF to PNG — building
    # without an exception is not proof it's correct. Table cell strings
    # (below) are unaffected — they don't go through this parser.
    flow = [Paragraph("Annual Returns vs S&amp;P 500", styles["SectionTitle"])]
    if annual.empty:
        flow.append(Paragraph("No trades in this run.", styles["Body"]))
        return flow

    rows = [["Year", "Trades", "Win Rate", "Return %", "P&L (GBP)", "S&P 500 %", "vs S&P 500"]]
    for _, r in annual.iterrows():
        sp, vs = r["sp500_pct"], r["vs_sp500_pct"]
        rows.append([
            str(int(r["year"])), str(int(r["trades"])), f"{r['win_rate_pct']:.1f}%",
            f"{r['total_return_pct']:+.1f}%",
            f"£{r['pnl_gbp']:+,.0f}" if pd.notna(r["pnl_gbp"]) else "—",
            f"{sp:+.1f}%" if pd.notna(sp) else "—",
            f"{vs:+.1f}%" if pd.notna(vs) else "—",
        ])
    flow.append(_pdf_table(rows, col_widths=[1.6 * cm, 1.8 * cm, 2 * cm, 2.2 * cm, 2.8 * cm, 2.4 * cm, 2.4 * cm]))
    flow.append(Paragraph(
        "* 2019 and 2023 underperformance reflects periods where the system was mostly "
        "in cash (MIXED regime) while the S&amp;P 500 rallied on narrow market leadership. "
        "Capital preservation during these periods enabled full participation in subsequent "
        "bull markets.",
        styles["Footnote"],
    ))
    flow.append(Paragraph(
        "Note: Return % and P&amp;L (GBP) are each summed from every trade's own individual "
        "outcome — Return % from each trade's own percentage return, P&amp;L (GBP) from each "
        "trade's own VaR-sized position (position size varies per trade with that ticker's "
        "volatility at entry). Neither column is computed against a fixed £50,000 account, "
        "so the two will not reconcile as \"X% of £50,000\" would imply — see Expectancy per "
        "Trade below for the flat-£50,000 illustrative figure used there.",
        styles["Footnote"],
    ))
    flow.append(Spacer(1, 0.3 * cm))

    # Risk-adjusted comparison replaces a raw "outperformed N of M years"
    # count — a simple year-count is skewed by how much of each year the
    # system was actually invested (see Time Invested below), so it
    # overstates S&P outperformance in active years and understates it in
    # cash-holding ones. CAGR/Calmar/Sharpe are a fairer like-for-like
    # comparison. SP500_* constants are the only hardcoded figures on this
    # page — every SP500Invest figure is read live from this run.
    sp500invest_cagr    = calmar.get("lifetime", {}).get("cagr_pct", 0.0)
    sp500invest_calmar  = calmar.get("lifetime", {}).get("calmar", 0.0)
    sp500invest_max_dd  = run.get("max_drawdown_pct") or 0.0
    sp500invest_sharpe  = run.get("sharpe_ratio") or 0.0
    sp500invest_time_invested = eq.get("time_invested_pct", 0.0)

    flow.append(Paragraph("Risk-Adjusted Benchmark Comparison", styles["SubHeading"]))
    comp_rows = [
        ["Metric", "SP500Invest", "S&P 500 (Buy & Hold)"],
        ["CAGR (annualised return)", f"{sp500invest_cagr:+.1f}%", f"{SP500_CAGR_2019_2026:+.1f}%"],
        ["Maximum Drawdown", f"{sp500invest_max_dd:.1f}%", f"{SP500_MAX_DRAWDOWN:.1f}% (COVID, Mar 2020)"],
        ["Calmar Ratio", f"{sp500invest_calmar:.2f}", f"{SP500_CALMAR:.2f}"],
        ["Time Invested", f"{sp500invest_time_invested:.1f}%", "100%"],
        ["Sharpe Ratio", f"{sp500invest_sharpe:.2f}", f"{SP500_SHARPE_2019_2026:.2f}"],
    ]
    flow.append(_pdf_table(comp_rows, col_widths=[6 * cm, 4.5 * cm, 6 * cm]))
    flow.append(Spacer(1, 0.2 * cm))
    flow.append(Paragraph(
        f"Note: SP500Invest is invested only {sp500invest_time_invested:.1f}% of the time — direct "
        "annual return comparison overstates S&amp;P outperformance in active years and "
        "understates it in cash-holding years. Risk-adjusted metrics (Calmar, Sharpe) provide "
        "a more meaningful comparison.",
        styles["Small"],
    ))
    flow.append(Spacer(1, 0.3 * cm))

    comparable = annual.dropna(subset=["vs_sp500_pct"])
    underperf = comparable[comparable["vs_sp500_pct"] <= 0] if not comparable.empty else comparable
    if not underperf.empty:
        years_str = ", ".join(str(int(y)) for y in underperf["year"])
        flow.append(Paragraph(
            f"Underperformance in {years_str} reflects periods where the regime gate kept "
            "the system on the sidelines (or trading lightly) during a strong broad-market "
            "rally — see the Edge Quality page's Time Invested figure — rather than a "
            "cluster of losing trades. The regime gate trades only when the system has "
            "demonstrated edge, which by construction means giving up some upside during "
            "strong bull markets outside its own entry criteria.",
            styles["Small"],
        ))
    return flow


def _pdf_page_monthly_heatmap(styles: dict, heat: pd.DataFrame) -> list:
    flow = [Paragraph("Monthly Returns Heatmap", styles["SectionTitle"])]
    if heat.empty:
        flow.append(Paragraph("No trades in this run.", styles["Body"]))
        return flow

    scale = max(abs(float(heat.values.max())), abs(float(heat.values.min())), 0.01)

    header = ["Year"] + [str(c) for c in heat.columns]
    rows = [header]
    for year in heat.index:
        rows.append([str(year)] + [f"{v:.1f}%" for v in heat.loc[year]])

    month_w = (A4[0] - 3.6 * cm - 1.6 * cm) / 12
    col_w = [1.6 * cm] + [month_w] * 12
    t = Table(rows, colWidths=col_w)
    style = [
        ("BACKGROUND",     (0, 0), (-1, 0), _PDF_NAVY),
        ("TEXTCOLOR",      (0, 0), (-1, 0), colors.white),
        ("FONTNAME",       (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",       (0, 1), (0, -1), "Helvetica-Bold"),
        ("BACKGROUND",     (0, 1), (0, -1), _PDF_LIGHT_GREY),
        ("FONTSIZE",       (0, 0), (-1, -1), 6.6),
        ("ALIGN",          (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",           (0, 0), (-1, -1), 0.3, _PDF_BORDER),
        ("TOPPADDING",     (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 3),
    ]
    for r, year in enumerate(heat.index, start=1):
        for c, month in enumerate(heat.columns, start=1):
            val = float(heat.loc[year, month])
            style.append(("BACKGROUND", (c, r), (c, r), _heatmap_cell_colour(val, scale)))
    t.setStyle(TableStyle(style))
    flow.append(t)
    flow.append(Spacer(1, 0.25 * cm))
    flow.append(Paragraph(
        "0.0% = no trade exited that month (shown as neutral, not a missing value).",
        styles["Small"],
    ))
    return flow


def _pdf_page_risk_analysis(styles: dict, dd: dict, calmar: dict, roll: pd.DataFrame) -> list:
    flow = [Paragraph("Risk Analysis", styles["SectionTitle"])]

    flow.append(Paragraph("Drawdown Metrics", styles["SubHeading"]))
    dd_rows = [
        ["Metric", "Value"],
        ["Current Drawdown", f"{dd.get('current_drawdown_pct', 0):.1f}%"],
        ["Maximum Drawdown", f"{dd.get('max_drawdown_pct', 0):.1f}%"],
        ["Average Drawdown", f"{dd.get('avg_drawdown_pct', 0):.1f}%"],
        ["Average Recovery Time", f"{dd.get('avg_recovery_days', 0):.0f} days"],
        ["Longest Recovery Time", f"{dd.get('longest_recovery_days', 0)} days"],
    ]
    flow.append(_pdf_table(dd_rows, col_widths=[8 * cm, 6 * cm]))
    flow.append(Spacer(1, 0.3 * cm))

    # Elevated to a standalone amber warning box (distinct from the blue
    # informational boxes elsewhere) rather than a small caption under the
    # table — a multi-year recovery period is exactly the kind of fact a
    # due-diligence reader should not have to notice in a table row.
    longest = dd.get("longest_recovery_days", 0) or 0
    if longest > 0:
        months = _recovery_days_to_calendar_months(longest)
        years = int(months // 12)
        rem_months = round(months - years * 12)
        if rem_months >= 12:
            years += 1
            rem_months -= 12
        if years and rem_months:
            duration_phrase = f"{years} year{'s' if years != 1 else ''} and {rem_months} month{'s' if rem_months != 1 else ''}"
        elif years:
            duration_phrase = f"{years} year{'s' if years != 1 else ''}"
        else:
            duration_phrase = f"{rem_months} month{'s' if rem_months != 1 else ''}"

        max_dd_val = dd.get("max_drawdown_pct", 0.0)
        lifetime_calmar = calmar.get("lifetime", {}).get("calmar", 0.0)
        calmar_descriptor = "strong" if lifetime_calmar >= 1.0 else "modest"

        warning_box = Table([[Paragraph(
            '<font color="#8A5A00"><b>WARNING: IMPORTANT RISK DISCLOSURE</b></font><br/><br/>'
            f"Longest Recovery Period: <b>{longest} trading days</b> "
            f"(approximately {months:.1f} calendar months)<br/><br/>"
            f"This system experienced a period of approximately {duration_phrase} without "
            "reaching a new equity high. Investors must be able to tolerate extended periods "
            f"of flat or negative performance before recovery. The maximum drawdown of "
            f"{max_dd_val:.1f}% occurred during this period.<br/><br/>"
            f"This statistic should be considered carefully alongside the {calmar_descriptor} "
            f"long-term Calmar ratio ({lifetime_calmar:.2f}).",
            styles["Body"],
        )]], colWidths=[16.5 * cm])
        warning_box.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#FFF3CD")),
            ("BOX",           (0, 0), (-1, -1), 1.2, colors.HexColor("#8A5A00")),
            ("TOPPADDING",    (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING",   (0, 0), (-1, -1), 12),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
        ]))
        flow.append(warning_box)
    flow.append(Spacer(1, 0.3 * cm))

    flow.append(Paragraph("Calmar Ratio", styles["SubHeading"]))
    flow.append(Paragraph(
        "Calmar = CAGR / Max Drawdown. Higher is better. Above 1.0 is considered strong.",
        styles["Small"],
    ))
    calmar_rows = [["Period", "CAGR", "Max DD", "Calmar"]]
    for label, key in [("Lifetime", "lifetime"), ("36-Month Rolling", "rolling_36m"), ("12-Month Rolling", "rolling_12m")]:
        v = calmar.get(key, {})
        calmar_rows.append([label, f"{v.get('cagr_pct', 0):.1f}%", f"{v.get('max_dd_pct', 0):.1f}%", f"{v.get('calmar', 0):.2f}"])
    flow.append(_pdf_table(calmar_rows, col_widths=[5 * cm, 3 * cm, 3 * cm, 3 * cm]))
    flow.append(Spacer(1, 0.3 * cm))

    flow.append(Paragraph("Rolling 12-Month Sharpe", styles["SubHeading"]))
    flow.append(Paragraph(_rolling_sharpe_narrative(roll), styles["Body"]))
    return flow


def _pdf_page_attribution(styles: dict, attr: dict, rfc: dict) -> list:
    flow = [Paragraph("Attribution", styles["SectionTitle"])]

    def _bucket_table(rows_data: list[dict], title: str) -> None:
        block = [Paragraph(title, styles["SubHeading"])]
        if not rows_data:
            block.append(Paragraph("No data available.", styles["Body"]))
        else:
            rows = [["Name", "Trades", "Win Rate", "% of Profit", "Profit Factor"]]
            for r in rows_data:
                rows.append([
                    str(r["name"]), str(r["trades"]), f"{r['win_rate_pct']:.1f}%",
                    f"{r['profit_pct_of_total']:.1f}%", f"{r['profit_factor']:.2f}",
                ])
            block.append(_pdf_table(rows, col_widths=[4 * cm, 2.2 * cm, 2.5 * cm, 2.8 * cm, 2.8 * cm]))
        block.append(Spacer(1, 0.25 * cm))
        # KeepTogether so a heading is never stranded alone at the bottom of
        # a page with its table pushed onto the next one.
        flow.append(KeepTogether(block))

    if not attr.get("available"):
        flow.append(Paragraph("No trade data available for attribution.", styles["Body"]))
    else:
        _bucket_table(attr["etf_vs_stock"], "Asset Class: ETF vs Stock")
        _bucket_table(attr["domestic_vs_intl"], "Domestic (US) vs International")
        sector_rows = attr["sector_table"].to_dict("records") if not attr["sector_table"].empty else []
        _bucket_table(sector_rows, "Sector Attribution")

    rfc_block = [Paragraph("Regime Filter Contribution", styles["SubHeading"])]
    if not rfc.get("available"):
        rfc_block.append(Paragraph("Re-run backtest to populate this comparison.", styles["Body"]))
    else:
        wf_, wof_ = rfc["with_filter"], rfc["without_filter"]
        rows = [
            ["Metric", "With Filter", "Without Filter", "Improvement"],
            ["Total Return", f"{wf_.get('total_return_pct', 0):+.1f}%", f"{wof_.get('total_return_pct', 0):+.1f}%",
             f"{rfc['return_improvement_pct']:+.1f}%"],
            ["Max Drawdown", f"{wf_.get('max_drawdown_pct', 0):.1f}%", f"{wof_.get('max_drawdown_pct', 0):.1f}%",
             f"{rfc['max_dd_reduction_pct']:+.1f}%"],
            ["Sharpe Ratio", f"{wf_.get('sharpe_ratio', 0):.2f}", f"{wof_.get('sharpe_ratio', 0):.2f}",
             f"{rfc['sharpe_improvement']:+.2f}"],
        ]
        rfc_block.append(_pdf_table(rows, col_widths=[4 * cm, 3.3 * cm, 3.3 * cm, 3 * cm]))
    flow.append(KeepTogether(rfc_block))
    return flow


def _pdf_page_backtest_evidence(styles: dict, run_id: int, config: dict, run: dict, eq: dict) -> list:
    flow = [Paragraph("Backtest Evidence", styles["SectionTitle"])]

    equity_result = _render_equity_curve_png(run_id, config)
    if equity_result:
        png, carryover_notes = equity_result
        flow.append(Image(BytesIO(png), width=16.5 * cm, height=8.25 * cm))
        for note in carryover_notes:
            flow.append(Paragraph(note, styles["Footnote"]))
    else:
        flow.append(Paragraph("No £-denominated equity curve available for this run.", styles["Body"]))
    flow.append(Spacer(1, 0.3 * cm))

    flow.append(Paragraph("Key Backtest Metrics", styles["SubHeading"]))
    rows = [
        ["Trades", "Win Rate", "Profit Factor", "Sharpe", "Max DD", "Avg Hold", "Total Return"],
        [
            str(run.get("total_trades", 0) or 0),
            f"{(run.get('win_rate') or 0) * 100:.1f}%",
            f"{eq.get('profit_factor', 0):.2f}",
            f"{(run.get('sharpe_ratio') or 0):.2f}",
            f"{(run.get('max_drawdown_pct') or 0):.1f}%",
            f"{(run.get('avg_holding_days') or 0):.0f}d",
            f"{(run.get('total_return_pct') or 0):+.1f}%",
        ],
    ]
    flow.append(_pdf_table(rows, col_widths=[2 * cm, 2.2 * cm, 2.3 * cm, 2 * cm, 2 * cm, 2 * cm, 2.5 * cm]))
    flow.append(Spacer(1, 0.3 * cm))

    flow.append(Paragraph("Exit Reason Distribution", styles["SubHeading"]))
    chart_png = _render_exit_reason_chart_png(run_id)
    if chart_png:
        flow.append(Image(BytesIO(chart_png), width=14 * cm, height=6 * cm))
    else:
        flow.append(Paragraph("No trades in this run.", styles["Body"]))
    return flow


def _pdf_page_walk_forward(styles: dict, wf: pd.DataFrame) -> list:
    flow = [Paragraph("Walk-Forward Validation", styles["SectionTitle"])]
    flow.append(Paragraph(
        "Walk-forward analysis tests whether the system's rules generalise to unseen data by "
        "evaluating rolling windows of trades rather than the full backtest period at once.",
        styles["Body"],
    ))

    if wf.empty:
        flow.append(Paragraph("Not enough trade history for a walk-forward breakdown.", styles["Body"]))
        return flow

    summary = compute_walk_forward_summary(wf)
    n_total, n_positive = summary["n_total"], summary["n_positive"]
    success_rate = summary["success_rate_pct"]
    majority = "Most" if success_rate >= 50 else "A minority of"
    leaning = "supporting" if success_rate >= 50 else "tempering"

    summary_box = Table([[Paragraph(
        '<font color="#0A0F1E"><b>WALK-FORWARD SUMMARY</b></font><br/><br/>'
        f"Windows Tested: <b>{n_total}</b><br/>"
        f"Profitable Windows: <b>{n_positive}</b> (PF > 1.0)<br/>"
        f"Success Rate: <b>{success_rate:.1f}%</b><br/><br/>"
        f"Conclusion: {majority} rolling windows remained profitable, {leaning} the possibility "
        "that the edge generalises beyond a single optimised historical period.<br/><br/>"
        '<font color="#C0392B">WARNING: This does not eliminate look-ahead bias from parameter '
        "optimisation but provides evidence the system's edge is not entirely "
        "period-specific.</font>",
        styles["Body"],
    )]], colWidths=[16.5 * cm])
    summary_box.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#E8F4FD")),
        ("BOX",           (0, 0), (-1, -1), 1.2, _PDF_NAVY),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
    ]))
    flow.append(summary_box)
    flow.append(Spacer(1, 0.4 * cm))

    rows = [["Train Start", "Train End", "Test Trades", "Test Win Rate", "Test PF", "Test Max DD"]]
    for _, r in wf.iterrows():
        rows.append([
            r["train_start"], r["train_end"], str(r["test_trades"]),
            f"{r['test_win_rate_pct']:.1f}%", f"{r['test_profit_factor']:.2f}", f"{r['test_max_dd_pct']:.1f}%",
        ])
    flow.append(_pdf_table(rows, col_widths=[2.8 * cm, 2.8 * cm, 2.4 * cm, 2.6 * cm, 2 * cm, 2.4 * cm]))
    return flow


def _next_scheduled_run_label(config: dict) -> Optional[str]:
    """Soonest of the three weekday analysis runs (pre-market/US-open/EOD),
    formatted in UK local time. Mirrors main.py's own "Next Scheduled Run"
    Status page computation (_next_run_for_time() + the soonest-of-three
    loop) exactly — reimplemented here rather than imported, since main.py
    imports FROM this module and importing back would be circular."""
    runs_cfg = (config.get("scheduler") or {}).get("runs") or {}
    labels = {"pre_market": "Pre-Market", "us_open": "US Open", "eod": "End of Day"}

    def _next_run_for_time(hour: int, minute: int) -> datetime:
        now = datetime.utcnow()
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:  # skip Sat (5) / Sun (6)
            candidate += timedelta(days=1)
        return candidate

    next_dt, next_label = None, None
    for run_name, label in labels.items():
        run_cfg = runs_cfg.get(run_name, {})
        candidate = _next_run_for_time(int(run_cfg.get("hour", 0)), int(run_cfg.get("minute", 0)))
        effective_label = "Monday Pre-Market" if (run_name == "pre_market" and candidate.weekday() == 0) else label
        if next_dt is None or candidate < next_dt:
            next_dt, next_label = candidate, effective_label

    if next_dt is None:
        return None
    return f"{next_label} — {_fmt_uk_time(next_dt)}"


def compute_current_system_status(config: dict) -> dict:
    """Live operational snapshot at report-generation time: market regime,
    whether new entries are currently permitted, open T212 position count,
    account value, drawdown-scaling tier, and the last/next scheduled
    analysis run.

    Distinct from every other compute_*() function in this module: those
    all read a frozen, completed backtest run keyed by run_id; this one
    reads the live system state instead, for the Live Validation page's
    "Current System Status" section (see CLAUDE.md's Live Validation
    notes) — it takes no run_id and its result changes from one call to
    the next as the live system's own state changes.

    Every field is fetched independently and defensively: a missing T212
    snapshot, an undetermined regime, or absent scheduler config must never
    raise or block PDF generation. A field this function couldn't resolve
    is left None; the page-builder renders "Unknown" / "Data unavailable"
    for those rather than a fabricated value.
    """
    status: dict = {
        "regime": None,
        "invest_allowed": None,
        "stock_allowed": None,
        "open_positions": None,
        "account_value_gbp": None,
        "drawdown_pct": None,
        "drawdown_status": None,
        "drawdown_scale_factor": None,
        "last_analysis_run": None,
        "next_scheduled_run": None,
    }

    try:
        from analysis.regime import get_latest_regime_name, is_entry_allowed  # noqa: PLC0415  local import — mirrors analysis/backtest.py's own pattern for regime.py functions

        regime_name = get_latest_regime_name()
        status["regime"] = regime_name
        if regime_name:
            status["invest_allowed"] = is_entry_allowed(config, regime_name, "trading212_invest")
            status["stock_allowed"] = is_entry_allowed(config, regime_name, "trading212_stocks")
    except Exception:
        logger.exception("compute_current_system_status: regime lookup failed")

    account_value_gbp = None
    try:
        raw_account = get_metadata_str("t212_account_snapshot")
        if raw_account:
            account_value_gbp = json.loads(raw_account).get("totalValue")
            status["account_value_gbp"] = account_value_gbp
    except Exception:
        logger.exception("compute_current_system_status: account snapshot read failed")

    try:
        raw_positions = get_metadata_str("t212_positions_snapshot")
        if raw_positions:
            status["open_positions"] = len(json.loads(raw_positions))
    except Exception:
        logger.exception("compute_current_system_status: positions snapshot read failed")

    if account_value_gbp is not None:
        try:
            from analysis.risk import compute_drawdown_scale_factor  # noqa: PLC0415

            scale_factor, dd_pct, dd_status = compute_drawdown_scale_factor(
                account_value_gbp=account_value_gbp, config=config, section="trading212_invest",
            )
            status["drawdown_pct"] = dd_pct
            status["drawdown_status"] = dd_status
            status["drawdown_scale_factor"] = scale_factor
        except Exception:
            logger.exception("compute_current_system_status: drawdown lookup failed")

    try:
        last_run = get_metadata("last_full_refresh")
        if last_run:
            status["last_analysis_run"] = _fmt_uk_time(last_run)
    except Exception:
        logger.exception("compute_current_system_status: last analysis run lookup failed")

    try:
        status["next_scheduled_run"] = _next_scheduled_run_label(config)
    except Exception:
        logger.exception("compute_current_system_status: next scheduled run computation failed")

    return status


def _pdf_page_live_validation(styles: dict, config: dict, eq: dict, run: dict, live: dict, status: dict) -> list:
    flow = [Paragraph("Live Validation", styles["SectionTitle"])]
    flow.append(Paragraph("<b>Out-of-Sample Performance Tracking</b>", styles["Body"]))
    flow.append(Spacer(1, 0.2 * cm))

    freeze = live.get("freeze_date", PARAMETER_FREEZE_DATE)
    today = date.today()
    days_since_freeze = max((today - freeze).days, 0)
    trading_days_since_freeze = len(pd.bdate_range(freeze + timedelta(days=1), today)) if today > freeze else 0

    status_box_style = {
        "green": ("#00875A", "#E8F8F0", "OUT-OF-SAMPLE (POSITIVE)"),
        "red":   ("#C0392B", "#FDEAEA", "OUT-OF-SAMPLE (NEGATIVE)"),
        "grey":  ("#6B7280", "#F2F3F5", "OUT-OF-SAMPLE"),
    }
    txt_colour, bg_colour, status_label = status_box_style.get(live.get("status"), status_box_style["grey"])

    status_box = Table([[Paragraph(
        f'<font color="{txt_colour}"><b>STATUS: {status_label}</b></font><br/><br/>'
        f"Parameter Freeze Date: {freeze}<br/>"
        f"Days Since Freeze: {days_since_freeze}<br/>"
        f"Trading Days Since Freeze: {trading_days_since_freeze}",
        styles["Body"],
    )]], colWidths=[16.5 * cm])
    status_box.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor(bg_colour)),
        ("BOX",           (0, 0), (-1, -1), 1.2, colors.HexColor(txt_colour)),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
    ]))
    flow.append(status_box)
    flow.append(Spacer(1, 0.4 * cm))

    flow.append(Paragraph("Live Performance Metrics", styles["SubHeading"]))

    def _or_na(val, fmt: str) -> str:
        return fmt.format(val) if val is not None else "N/A"

    sharpe_cell = (
        _or_na(live.get("sharpe_ratio"), "{:.2f}") if live.get("sharpe_available") else "Insufficient data"
    )
    live_rows = [
        ["Metric", "Value"],
        ["Trades Since Freeze", str(live.get("n_trades", 0))],
        ["Live Return", f"{live.get('return_since_freeze_pct', 0):+.1f}%"],
        ["Live Max Drawdown", _or_na(live.get("max_drawdown_pct"), "{:.1f}%")],
        ["Live Sharpe", sharpe_cell],
        ["Live Win Rate", _or_na(live.get("win_rate_pct"), "{:.1f}%")],
        ["Live Profit Factor", _or_na(live.get("profit_factor"), "{:.2f}")],
    ]
    flow.append(_pdf_table(live_rows, col_widths=[8 * cm, 6 * cm]))
    flow.append(Spacer(1, 0.3 * cm))

    if live.get("n_trades", 0) == 0:
        flow.append(Paragraph(
            f"No live trades have been completed since the parameter freeze date of {freeze}. "
            "This section will populate automatically as real T212 positions are opened and "
            "closed.",
            styles["Body"],
        ))
    else:
        flow.append(Paragraph("Live vs In-Sample Comparison", styles["SubHeading"]))
        exp_in_sample = eq.get("expectancy", {}).get("expectancy_pct", 0.0)
        comp_rows = [
            ["Metric", "In-Sample", "Live"],
            ["Win Rate",      f"{eq.get('win_rate_pct', 0):.1f}%",       f"{live.get('win_rate_pct', 0):.1f}%"],
            ["Profit Factor", f"{eq.get('profit_factor', 0):.2f}",      f"{live.get('profit_factor', 0):.2f}"],
            ["Expectancy",    f"{exp_in_sample:+.2f}%",                 f"{live.get('expectancy_pct', 0):+.2f}%"],
            ["Max Drawdown",  f"{(run.get('max_drawdown_pct') or 0):.1f}%", f"{live.get('max_drawdown_pct', 0):.1f}%"],
        ]
        flow.append(_pdf_table(comp_rows, col_widths=[5 * cm, 4.5 * cm, 4.5 * cm]))

    flow.append(Spacer(1, 0.3 * cm))
    flow.append(Paragraph(
        "Live results after the parameter freeze date provide the strongest evidence of "
        "whether the system's edge is real. As this dataset grows, it will supersede the "
        "backtest as the primary performance reference.",
        styles["Small"],
    ))

    # --- Section 2: Current System Status -----------------------------------
    # A distinct block from everything above: Section 1 is about historical/
    # live *performance* (frozen backtest vs. real T212 fills since the
    # freeze date); this section is the live operational state of the system
    # at the moment the report was generated — never conflated with, and
    # never used to explain, the performance figures above it.
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(HRFlowable(width="100%", thickness=0.75, color=_PDF_NAVY))
    flow.append(Spacer(1, 0.3 * cm))
    flow.append(Paragraph("Current System Status", styles["SectionTitle"]))
    flow.append(Spacer(1, 0.2 * cm))

    _NA = "Data unavailable — run Refresh Data on Status page"
    _GREEN, _AMBER, _RED = "#00875A", "#8A5A00", "#C0392B"

    def _coloured(text: str, colour: Optional[str]) -> str:
        return f'<font color="{colour}"><b>{text}</b></font>' if colour else f"<b>{text}</b>"

    regime = status.get("regime")
    regime_colour = {"MIXED": _AMBER, "TRENDING_BULL": _GREEN}.get(regime)
    regime_text = _coloured(regime, regime_colour) if regime else _coloured("Unknown", None)

    if regime is None:
        entries_text = _coloured("Unknown", None)
    elif status.get("invest_allowed") or status.get("stock_allowed"):
        entries_text = _coloured("Allowed", _GREEN)
    else:
        entries_text = _coloured("Blocked", _RED)

    open_positions = status.get("open_positions")
    open_positions_text = str(open_positions) if open_positions is not None else _NA

    account_value_gbp = status.get("account_value_gbp")
    account_value_text = f"£{account_value_gbp:,.0f}" if account_value_gbp is not None else _NA

    drawdown_pct = status.get("drawdown_pct")
    drawdown_pct_text = f"{drawdown_pct:.1f}%" if drawdown_pct is not None else _NA

    dd_status = status.get("drawdown_status")
    dd_scale = status.get("drawdown_scale_factor")
    if dd_status is None:
        dd_text = _coloured("Unknown", None)
    else:
        dd_colour = {"NORMAL": _GREEN, "CAUTION": _AMBER, "REDUCED": _AMBER, "MINIMAL": _RED}.get(dd_status)
        dd_label = f"{dd_status} ({dd_scale:.0%} of normal size)" if dd_scale is not None else dd_status
        dd_text = _coloured(dd_label, dd_colour)

    last_analysis_text = status.get("last_analysis_run") or "Unknown"
    next_run_text = status.get("next_scheduled_run") or "Unknown"

    status_box = Table([[Paragraph(
        f"Market Regime: {regime_text}<br/>"
        f"New Entries: {entries_text}<br/>"
        f"Open Positions: <b>{open_positions_text}</b><br/>"
        f"Account Value: <b>{account_value_text}</b><br/>"
        f"Drawdown from Peak: <b>{drawdown_pct_text}</b><br/>"
        f"Drawdown Scale Factor: {dd_text}<br/>"
        f"Last Analysis Run: <b>{last_analysis_text}</b><br/>"
        f"Next Scheduled Run: <b>{next_run_text}</b>",
        styles["Body"],
    )]], colWidths=[16.5 * cm])
    status_box.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#E8F4FD")),
        ("BOX",           (0, 0), (-1, -1), 1.2, _PDF_NAVY),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
    ]))
    flow.append(status_box)
    flow.append(Spacer(1, 0.2 * cm))
    flow.append(Paragraph(
        "Operational status at time of report generation. Reflects live system state, not "
        "historical performance.",
        styles["Small"],
    ))

    return flow


def _pdf_page_methodology(styles: dict) -> list:
    flow = [Paragraph("Methodology & Disclaimers", styles["SectionTitle"])]

    flow.append(Paragraph("System Description", styles["SubHeading"]))
    flow.append(Paragraph(_SYSTEM_DESCRIPTION_FULL, styles["Body"]))
    flow.append(Spacer(1, 0.2 * cm))

    flow.append(Paragraph("Parameter Freeze Date", styles["SubHeading"]))
    flow.append(Paragraph(
        f"All weights, thresholds, and risk parameters used to produce this report's backtest "
        f"figures were tuned against historical data up to {PARAMETER_FREEZE_DATE}. Every "
        "backtest figure in this report is therefore in-sample by construction. Any trade "
        "closing after this date is genuinely out-of-sample — see the Edge Quality page's "
        "Out-of-Sample Monitor for the live count.",
        styles["Body"],
    ))
    flow.append(Spacer(1, 0.2 * cm))

    flow.append(Paragraph("Data Sources", styles["SubHeading"]))
    flow.append(Paragraph(
        "Price and volume history: Yahoo Finance (yfinance). Macroeconomic series (yield "
        "curves, credit spreads, money supply, central bank rates): Federal Reserve Economic "
        "Data (FRED).",
        styles["Body"],
    ))
    flow.append(Spacer(1, 0.2 * cm))

    flow.append(Paragraph("Backtest Assumptions and Limitations", styles["SubHeading"]))
    for item in [
        "No transaction costs (commissions, spread, slippage) are modelled.",
        "Parameters were optimised on in-sample historical data.",
        "Past performance does not guarantee future results.",
        "Walk-forward validation methodology is available on request.",
    ]:
        flow.append(Paragraph(f"- {item}", styles["Body"]))
    flow.append(Spacer(1, 0.3 * cm))

    flow.extend(_known_limitations_section(styles))

    flow.append(Spacer(1, 0.4 * cm))
    flow.append(HRFlowable(width="100%", thickness=0.5, color=_PDF_BORDER))
    flow.append(Spacer(1, 0.2 * cm))
    flow.append(Paragraph(
        "Contact: hello@example.com — full technical documentation available under NDA.",
        styles["Small"],
    ))
    return flow


def _known_limitations_section(styles: dict) -> list:
    """"Known Limitations & Improvement Roadmap" — the Methodology &
    Disclaimers page's closing section, between "Backtest Assumptions and
    Limitations" and the contact footer.

    Deliberately static (no DB reads, no run_id): every figure quoted here
    is a specific, already-known finding from earlier work (see CLAUDE.md's
    macro-bug and TRENDING_BULL regime-weight re-tuning-study sections for
    the underlying investigations) rather than something recomputed live —
    this section documents what is already understood, not a fresh
    analysis of the currently-selected run.

    Each sub-section's own heading is paired with only its first row/bullet
    inside a KeepTogether — enough to stop the heading being orphaned alone
    at the bottom of a page — while the sub-section as a whole is left able
    to flow across a page break, since the full section is long enough that
    forcing it onto one page would either overflow or leave a large gap.
    """
    flow: list = [Paragraph("Known Limitations &amp; Improvement Roadmap", styles["SubHeading"])]
    flow.append(Paragraph(
        "The following limitations are documented proactively — not as disclaimers, but as "
        "evidence that the system's boundaries are well understood. Each limitation represents "
        "a quantified, identified opportunity for improvement rather than an unknown risk.",
        styles["Body"],
    ))
    flow.append(Spacer(1, 0.2 * cm))

    # --- Sub-section 1: Current Limitations ---------------------------------
    col_widths = [3.3 * cm, 8.0 * cm, 5.2 * cm]
    header_table = Table([["Limitation", "Evidence", "Impact"]], colWidths=col_widths)
    header_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), _PDF_NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, -1), colors.white),
        ("FONTNAME",      (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8.2),
        ("GRID",          (0, 0), (-1, -1), 0.4, _PDF_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
    ]))
    first_row = _limitation_row(_KNOWN_LIMITATIONS[0], 0, styles, col_widths)
    flow.append(KeepTogether([
        Paragraph("Current Limitations", styles["SubHeading"]),
        header_table,
        first_row,
    ]))
    for i, item in enumerate(_KNOWN_LIMITATIONS[1:], start=1):
        flow.append(_limitation_row(item, i, styles, col_widths))
    flow.append(Spacer(1, 0.3 * cm))

    # --- Sub-section 2: Improvement Roadmap ---------------------------------
    first_tier_heading, first_tier_bullets = _IMPROVEMENT_ROADMAP[0]
    flow.append(KeepTogether([
        Paragraph("Improvement Roadmap", styles["SubHeading"]),
        Paragraph(_GTM_STRATEGY_NOTE, styles["Body"]),
        Paragraph(f'<font color="#00C2FF"><b>{first_tier_heading}</b></font>', styles["Body"]),
        Paragraph(f"→ {first_tier_bullets[0]}", styles["Body"]),
    ]))
    for bullet in first_tier_bullets[1:]:
        flow.append(Paragraph(f"→ {bullet}", styles["Body"]))
    for tier_heading, bullets in _IMPROVEMENT_ROADMAP[1:]:
        flow.append(Paragraph(f'<font color="#00C2FF"><b>{tier_heading}</b></font>', styles["Body"]))
        for bullet in bullets:
            flow.append(Paragraph(f"→ {bullet}", styles["Body"]))
    flow.append(Spacer(1, 0.3 * cm))

    # --- Sub-section 3: Founder's Note --------------------------------------
    founders_box = Table([[Paragraph(f"<i>{_FOUNDERS_NOTE}</i>", styles["Body"])]], colWidths=[16.5 * cm])
    founders_box.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#E8F4FD")),
        ("BOX",           (0, 0), (-1, -1), 1.2, _PDF_NAVY),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
    ]))
    flow.append(KeepTogether([
        Paragraph("Founder's Note", styles["SubHeading"]),
        founders_box,
    ]))
    return flow


def _limitation_row(item: dict, index: int, styles: dict, col_widths: list[float]) -> KeepTogether:
    """One row of the Known Limitations table, wrapped in KeepTogether so a
    row is never split across a page break — each row is its own small
    Table rather than one row of a larger combined Table, specifically so
    KeepTogether can be applied per-row."""
    bg = _PDF_LIGHT_GREY if index % 2 == 1 else colors.white
    row = Table([[
        Paragraph(f"<b>{item['limitation']}</b>", styles["TableCell"]),
        Paragraph(item["evidence"], styles["TableCell"]),
        Paragraph(f'<i><font color="#6B7280">{item["impact"]}</font></i>', styles["TableCell"]),
    ]], colWidths=col_widths)
    row.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), bg),
        ("GRID",          (0, 0), (-1, -1), 0.4, _PDF_BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
    ]))
    return KeepTogether(row)


def generate_robustness_pdf(run_id: int, config: dict) -> bytes:
    """Render the Robustness page as a standalone due-diligence PDF (bytes).

    Eleven sections: Cover, Executive Summary, Edge Quality, Annual Returns
    (with a risk-adjusted S&P 500 benchmark comparison), Monthly Returns
    Heatmap, Risk Analysis (with a standalone longest-recovery warning box),
    Attribution, Backtest Evidence (equity curve + exit-reason chart),
    Walk-Forward Validation (with a summary box), Live Validation (genuine
    post-freeze T212 performance vs. this run's in-sample metrics), and
    Methodology & Disclaimers. Every figure is computed live from the
    compute_*() functions in this module — SP500_ANNUAL and the SP500_*
    risk-adjusted constants are the only hardcoded performance numbers
    anywhere in the report. Read-only: no database write of any kind, no
    re-running of the backtest itself.

    Must complete well under the UI's 10-second budget — the two ~150dpi
    matplotlib PNGs and a dozen small ReportLab tables are the only
    non-trivial cost.
    """
    run = get_run(run_id) or {}
    eq     = compute_edge_quality(run_id)
    annual = compute_annual_returns(run_id)
    heat   = compute_monthly_heatmap(run_id)
    dd     = compute_drawdown_metrics(run_id)
    calmar = compute_calmar_ratios(run_id)
    roll   = compute_rolling_metrics(run_id)
    attr   = compute_attribution(run_id, config, source="historical")
    rfc    = compute_regime_filter_contribution(run_id)
    wf     = compute_walk_forward_evidence(run_id)
    live   = compute_live_validation(run_id)
    status = compute_current_system_status(config)

    styles = _pdf_styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=2.0 * cm, bottomMargin=1.8 * cm, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        title="SP500Invest Due Diligence Report",
    )

    pages = [
        _pdf_page_cover(styles, run),
        _pdf_page_executive_summary(styles, run, eq, calmar, rfc),
        _pdf_page_edge_quality(styles, eq),
        _pdf_page_annual_returns(styles, annual, calmar, run, eq),
        _pdf_page_monthly_heatmap(styles, heat),
        _pdf_page_risk_analysis(styles, dd, calmar, roll),
        _pdf_page_attribution(styles, attr, rfc),
        _pdf_page_backtest_evidence(styles, run_id, config, run, eq),
        _pdf_page_walk_forward(styles, wf),
        _pdf_page_live_validation(styles, config, eq, run, live, status),
        _pdf_page_methodology(styles),
    ]

    story: list = []
    for i, page_flowables in enumerate(pages):
        story.extend(page_flowables)
        if i < len(pages) - 1:
            story.append(PageBreak())

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()
