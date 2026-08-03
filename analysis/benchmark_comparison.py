"""Standing, reusable three-way performance comparison: the actual,
current live production configuration vs. genuine S&P 500 buy-and-hold
vs. the most recently recorded prior baseline. Reset-at-boundary
convention (see CLAUDE.md's "Two backtest conventions" section) — this
answers "which configuration is better," not "what would one account
realistically show since inception" (that question is
analysis/validation.py:run_full_validation()'s job, a continuous walk,
deliberately different).

Standing practice (see CLAUDE.md's own dedicated section): run this any
time a change to entry/exit/sizing logic is validated, before
considering it fully settled. Reads every strategy parameter
(per_trade_pct, max_total_deployed, reentry_cooldown_days,
min_momentum_pct) from *config* at call time — never hardcoded — so a
config predating or postdating any of today's specific values is
reflected automatically, and this script never goes stale the way a
one-off comparison tied to today's named parameters would.

The log (research/strategy_vs_benchmark_log.jsonl, git-tracked, never
gitignored) is the single source of truth this module reads its own
"most recently recorded baseline" from — genuinely append-only (a run
only ever adds a new line, never edits or removes an existing one).
CLAUDE.md's own human-readable table is a REGENERATED VIEW of that same
JSONL file, not a second, independently-maintained copy — regenerating
the rendered table on every run is what keeps the two from ever
drifting apart; the underlying log itself is what's genuinely appended
to, never overwritten, satisfying that requirement in substance even
though the markdown bytes themselves are rewritten each time.
"""
import datetime
import json
import subprocess
from pathlib import Path
from typing import Optional

import pandas as pd

from analysis import universe
from analysis.backtest import (
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
    precompute_ticker,
    simulate_portfolio_compounding_mtm,
    slice_ticker,
)
from analysis.decision import is_asset_class_enabled
from analysis.validation import (
    VALIDATION_TRAIN_TEST_SPLIT,
    _cagr_dd_for_series,
    _sp500_ending_balance_gbp,
    _strategy_ending_balance_gbp,
)
from data.ingestion import get_price_df

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = _REPO_ROOT / "research" / "strategy_vs_benchmark_log.jsonl"

# The "standard test period" this comparison always uses, matching every
# reset-at-boundary comparison already run this session — the real
# train/test boundary this whole codebase's validated figures split on.
TEST_PERIOD_START = VALIDATION_TRAIN_TEST_SPLIT  # 2020-01-01


def _detect_git_commit() -> Optional[str]:
    """Best-effort: the commit this code is actually running from right
    now. Never raises — a detection failure just means the log entry's
    "commit" field is None, not a reason to fail the whole comparison."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return None


def _build_live_universe_caches(config: dict, start: pd.Timestamp, end: pd.Timestamp):
    """The identical live candidate-pool construction
    analysis.validation.run_full_validation(live_universe=True) uses —
    ETFs structurally absent (get_live_stock_candidates() excludes them
    entirely), still respecting trading212_invest.stock_trading_enabled."""
    stock_trading_enabled = is_asset_class_enabled(is_etf=False, config=config)
    candidate_tickers = (
        universe.get_live_stock_candidates(as_of=end.date()) if stock_trading_enabled else []
    )
    caches = []
    for ticker in candidate_tickers:
        c = precompute_ticker(ticker, config)
        if c is not None:
            c = slice_ticker(c, start, end)
        caches.append(c)
    n_ok = sum(1 for c in caches if c is not None)
    return caches, len(candidate_tickers), n_ok


def run_current_strategy_leg(
    config: dict, start: pd.Timestamp, end: pd.Timestamp, starting_capital: float,
) -> dict:
    """The live, currently-deployed engine (simulate_portfolio_
    compounding_mtm()) against the live, currently-deployed candidate
    universe — reset-at-boundary: the walk starts fresh at *start* with
    *starting_capital*, never visiting any date before it, so there is
    no train-period carryover to reset away (unlike a mid-walk reset,
    starting the walk there in the first place is sufficient and
    avoids needing any engine-level modification at all).

    per_trade_pct/max_total_deployed are read from *config* here (the
    two values simulate_portfolio_compounding_mtm() needs as explicit
    arguments); reentry_cooldown_days/min_momentum_pct are resolved
    INSIDE that function itself, from the same *config* — every
    strategy parameter this leg reflects comes from *config* at call
    time, nothing hardcoded.
    """
    caches, n_total, n_ok = _build_live_universe_caches(config, start, end)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= start) & (gspc_close_full.index <= end)]

    t212_cfg = config.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.5))

    trades = simulate_portfolio_compounding_mtm(
        caches, config, gspc_close, per_trade_pct, max_deployed_pct, collect_rejections=False,
    )
    metrics = compute_metrics_fixed(trades, starting_capital)
    peak_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)
    ending_gbp = _strategy_ending_balance_gbp(trades, starting_capital)
    total_pct_change = (ending_gbp - starting_capital) / starting_capital * 100.0

    return {
        "n_tickers_total": n_total,
        "n_tickers_with_data": n_ok,
        "n_trades": len(trades),
        "cagr_pct": metrics["cagr_pct"],
        "max_drawdown_pct_fixed": metrics["max_drawdown_pct"],
        "max_drawdown_pct_peak_relative": peak_dd,
        "profit_factor": metrics.get("profit_factor", 0.0),
        "win_rate_pct": round(metrics.get("win_rate", 0.0) * 100, 1),
        "ending_balance_gbp": round(ending_gbp, 2),
        "total_pct_change": round(total_pct_change, 2),
        "per_trade_pct": per_trade_pct,
        "max_total_deployed": max_deployed_pct,
        "reentry_cooldown_days": t212_cfg.get("reentry_cooldown_days"),
        "min_momentum_pct": t212_cfg.get("min_momentum_pct"),
    }


def run_sp500_benchmark_leg(
    gspc_close_full: pd.Series, start: pd.Timestamp, end: pd.Timestamp, starting_capital: float,
) -> dict:
    """Genuine S&P 500 buy-and-hold over the identical window and
    starting capital — the same ^GSPC series (price_data, price-only,
    no dividend reinvestment) every other S&P comparison in this
    project already uses. max_drawdown_pct here is genuinely peak-
    relative by construction (_cagr_dd_for_series() computes it as
    (price - running_peak) / running_peak) — a buy-and-hold position
    has no separate "fixed starting-capital denominator" convention to
    disambiguate from, unlike a compounding trading strategy's own
    equity curve."""
    cagr, dd = _cagr_dd_for_series(gspc_close_full, start, end)
    ending_gbp = _sp500_ending_balance_gbp(gspc_close_full, start, end, starting_capital)
    if ending_gbp is None:
        ending_gbp = starting_capital
    total_pct_change = (ending_gbp - starting_capital) / starting_capital * 100.0
    return {
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "max_drawdown_pct_peak_relative": round(dd, 2) if dd is not None else None,
        "ending_balance_gbp": round(ending_gbp, 2),
        "total_pct_change": round(total_pct_change, 2),
    }


def read_last_log_entry(log_path: Path = DEFAULT_LOG_PATH) -> Optional[dict]:
    """The most recently recorded entry -- the last line of the JSONL
    log, or None if the log doesn't exist yet or is empty (a genuine
    "no prior baseline recorded" state, not an error)."""
    if not log_path.exists():
        return None
    last_line = None
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last_line = line
    if last_line is None:
        return None
    return json.loads(last_line)


def append_log_entry(entry: dict, log_path: Path = DEFAULT_LOG_PATH) -> None:
    """Append-only -- opens in "a" mode, never truncates or rewrites an
    existing line. This is the log's own genuine source of truth;
    render_markdown_log() below only ever reads it, never the reverse."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def read_all_log_entries(log_path: Path = DEFAULT_LOG_PATH) -> list[dict]:
    if not log_path.exists():
        return []
    entries = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


_MD_HEADER = (
    "| Date | Commit | Trades | Strategy CAGR | Strategy DD (fixed) | Strategy DD (peak) | "
    "Strategy £50k→ | Strategy Δ% | S&P CAGR | S&P DD (peak) | S&P £50k→ | S&P Δ% | "
    "Strategy − S&P (Δpp on £ change) | Strategy vs. prior run (Δpp on £ change) |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)


def _fmt(v, suffix="") -> str:
    return "—" if v is None else f"{v}{suffix}"


def render_markdown_log(log_path: Path = DEFAULT_LOG_PATH) -> str:
    """Rebuilds the full markdown table from every entry in the JSONL
    log -- a regenerated VIEW, not a second independently-maintained
    copy (see this module's own docstring for why that keeps the two
    from ever drifting apart)."""
    entries = read_all_log_entries(log_path)
    rows = [_MD_HEADER]
    for e in entries:
        s = e["strategy"]
        sp = e["sp500"]
        rows.append(
            f"| {e['date']} | `{_fmt(e.get('commit'))[:9] if e.get('commit') else '—'}` "
            f"| {s['n_trades']} | {_fmt(s['cagr_pct'], '%')} "
            f"| {_fmt(s['max_drawdown_pct_fixed'], '%')} | {_fmt(s['max_drawdown_pct_peak_relative'], '%')} "
            f"| £{s['ending_balance_gbp']:,.2f} | {_fmt(s['total_pct_change'], '%')} "
            f"| {_fmt(sp['cagr_pct'], '%')} | {_fmt(sp['max_drawdown_pct_peak_relative'], '%')} "
            f"| £{sp['ending_balance_gbp']:,.2f} | {_fmt(sp['total_pct_change'], '%')} "
            f"| {_fmt(e.get('delta_vs_sp500_pp'), 'pp')} | {_fmt(e.get('delta_vs_prior_run_pp'), 'pp')} |\n"
        )
    return "".join(rows)


def run_comparison(
    config: dict,
    start: pd.Timestamp = TEST_PERIOD_START,
    end: Optional[pd.Timestamp] = None,
    starting_capital: float = 50_000.0,
    log_path: Path = DEFAULT_LOG_PATH,
    run_date: Optional[str] = None,
) -> dict:
    """The full orchestration: current-strategy leg + S&P leg + prior-
    baseline lookup + delta computation + log append. Returns the
    complete result dict (also what gets logged) so a caller can render
    or print it without needing to re-read the log itself."""
    end = end or pd.Timestamp(datetime.date.today())
    run_date = run_date or datetime.date.today().isoformat()

    prior = read_last_log_entry(log_path)

    strategy = run_current_strategy_leg(config, start, end, starting_capital)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    sp500 = run_sp500_benchmark_leg(gspc_close_full, start, end, starting_capital)

    delta_vs_sp500_pp = round(strategy["total_pct_change"] - sp500["total_pct_change"], 2)
    delta_vs_prior_run_pp = (
        round(strategy["total_pct_change"] - prior["strategy"]["total_pct_change"], 2)
        if prior is not None else None
    )

    entry = {
        "date": run_date,
        "commit": _detect_git_commit(),
        "period_start": start.date().isoformat(),
        "period_end": end.date().isoformat(),
        "starting_capital_gbp": starting_capital,
        "strategy": strategy,
        "sp500": sp500,
        "delta_vs_sp500_pp": delta_vs_sp500_pp,
        "delta_vs_prior_run_pp": delta_vs_prior_run_pp,
        "prior_run_date": prior["date"] if prior is not None else None,
    }

    append_log_entry(entry, log_path)

    return {"entry": entry, "prior": prior}
