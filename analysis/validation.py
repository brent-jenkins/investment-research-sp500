"""Full validated-battery runner — momentum signal + crisis override, the
current point-in-time-gated universe, train (2007-2019) / test (2020-2026)
split, 16 historical blocks each checked against the real S&P 500 over the
same window, a concentration check (ex-best-trade), transaction-cost
sensitivity, and walk-forward. Shared by the Backtest page (main.py) and the
due-diligence report generator (analysis/dd_report.py) so both are always
computed the exact same way from the exact same run.

Default trade-generation engine changed 2026-08-01 -- see
run_full_validation()'s own docstring below for the full detail, and
CLAUDE.md's "Live execution switched to the compounding two-parameter
scheme" section for the rationale. In short: a bare run_full_validation(config)
call now uses the identical genuinely-compounding, portfolio-capped engine
(and the identical trading212_invest.per_trade_pct / max_total_deployed
config values) that live order placement uses, not the original
no-portfolio-constraint engine every pre-2026-08-01 figure in this project
was generated with.

Block pass/fail rule (reconciled 2026-07-31 -- see CLAUDE.md's "Block
pass/fail formula reconciliation" section for the full investigation):
strategy max drawdown no worse than 1.5x the S&P's own over the same
window, AND strategy profit factor > 1.0 over the same window (the same
">1.0 = profitable" convention this project already uses in
walk_forward_pf() below and in the pre-V1-removal
compute_walk_forward_summary()). A per-block CAGR-vs-S&P comparison and an
invented "-20% of capital" floor were both tried in an earlier
reconstruction and produced the wrong two failing blocks; neither survives
against the real reproduction data (see CLAUDE.md) and neither is used
here. `_block_passes()` is exported specifically so a regression test can
pin this exact formula against real historical block metrics and catch any
future silent drift.

`make_blocks()`'s boundaries (imported from analysis.backtest, unchanged
byte-for-byte since the very first V2 commit) are NOT a reconstruction --
confirmed via git history to be identical to what the original
reproducibility check used, so a boundary mismatch was ruled out before
looking at the formula itself.
"""
import datetime
import logging
from typing import Optional

import pandas as pd

from analysis import universe
from analysis.decision import is_asset_class_enabled
from analysis.backtest import (
    compute_cost_sensitivity,
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
    equity_curve_gbp,
    full_metrics_fixed,
    make_blocks,
    precompute_ticker,
    run_candidate,
    simulate_portfolio,
    simulate_portfolio_compounding,
    simulate_portfolio_compounding_mtm,
    slice_ticker,
    walk_forward_pf,
)
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)

VALIDATION_START = pd.Timestamp("2007-01-01")
VALIDATION_TRAIN_TEST_SPLIT = pd.Timestamp("2020-01-01")


def _cagr_dd_for_series(close: pd.Series, b_start: pd.Timestamp, b_end: pd.Timestamp):
    s = close[(close.index >= b_start) & (close.index <= b_end)]
    if len(s) < 2:
        return None, None
    start_v, end_v = float(s.iloc[0]), float(s.iloc[-1])
    days = (s.index[-1] - s.index[0]).days
    years = days / 365.25 if days > 0 else 0.0
    cagr = ((end_v / start_v) ** (1.0 / years) - 1.0) * 100 if years > 0 and start_v > 0 else 0.0
    peak = s.cummax()
    dd = float(((s - peak) / peak * 100).min())
    return cagr, dd


def _sp500_ending_balance_gbp(
    close: pd.Series, start: pd.Timestamp, end: pd.Timestamp, starting_capital: float,
) -> Optional[float]:
    """£ ending balance for a buy-and-hold position in *close* over
    [start, end], sized at starting_capital -- computed directly from the
    window's first/last price rather than re-derived from a rounded CAGR
    percentage, so it matches _cagr_dd_for_series()'s own CAGR exactly
    (same start_v/end_v, same window bounds) rather than compounding a
    second, lossier calculation on top of it. None only when the series
    has fewer than 2 rows in the window (nothing to compare against) --
    same "no data, not zero" convention _cagr_dd_for_series() uses.
    """
    s = close[(close.index >= start) & (close.index <= end)]
    if len(s) < 2 or float(s.iloc[0]) <= 0:
        return None
    return starting_capital * (float(s.iloc[-1]) / float(s.iloc[0]))


def _strategy_ending_balance_gbp(trades: list[dict], starting_capital: float) -> float:
    """£ ending balance for *trades* under the additive-£ methodology
    (equity_curve_gbp()) -- the same one behind every other £ figure this
    project reports (compute_metrics_fixed(), the DD report's own equity
    curve, the Run #26 real-return reconciliation). No trades -> the
    account never moved, so it's still sitting at starting_capital.
    """
    if not trades:
        return starting_capital
    ec = equity_curve_gbp(pd.DataFrame(trades), starting_capital_gbp=starting_capital)
    if ec.empty:
        return starting_capital
    return float(ec["equity_gbp"].iloc[-1])


def compute_gbp_growth(
    gspc_close_full: pd.Series,
    train_trades: list[dict],
    test_trades: list[dict],
    starting_capital: float,
    start: pd.Timestamp,
    split: pd.Timestamp,
    end: pd.Timestamp,
) -> dict:
    """What a real £starting_capital investment would have actually become,
    V2 vs. buy-and-hold S&P 500 -- train and test kept separate, never
    blended into one full-period number (blending would hide how much of
    any headline figure comes from the in-sample training window).

    The S&P leg reuses the identical ^GSPC series (price_data, sourced via
    get_price_df() -- a price-only index level, no dividend reinvestment;
    ^SP500TR, the total-return variant, is not tracked anywhere in this
    project) already used for train_sp/test_sp's CAGR figures elsewhere in
    run_full_validation() -- same series, same window boundaries, so this
    is a £-terms restatement of the exact same comparison already
    validated, not a second, differently-sourced or differently-
    conventioned one running alongside it.
    """
    return {
        "starting_capital_gbp": starting_capital,
        "train": {
            "strategy_ending_gbp": round(_strategy_ending_balance_gbp(train_trades, starting_capital), 2),
            "sp500_ending_gbp": round(
                _sp500_ending_balance_gbp(gspc_close_full, start, split, starting_capital) or starting_capital, 2,
            ),
        },
        "test": {
            "strategy_ending_gbp": round(_strategy_ending_balance_gbp(test_trades, starting_capital), 2),
            "sp500_ending_gbp": round(
                _sp500_ending_balance_gbp(gspc_close_full, split, end, starting_capital) or starting_capital, 2,
            ),
        },
    }


def _block_passes(strat_metrics: dict, sp_dd: Optional[float]) -> bool:
    """The reconciled per-block pass/fail rule -- see this module's
    docstring. Pure and separately exported so a regression test can pin it
    directly against real historical block metrics, independent of the full
    (expensive, production-data-scale) battery run.

    dd_ok: strategy's own max drawdown no worse than 1.5x the S&P's own
    over the same window (sp_dd is None only when the S&P series itself
    has fewer than 2 rows in the window -- treated as an automatic pass
    for that leg, since there is nothing to compare against).

    pf_ok: strategy profit factor strictly greater than 1.0 -- this
    project's own standing "did this window actually make money" bar
    (analysis/backtest.py:walk_forward_pf()'s identical `pf > 1.0` check).
    """
    dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
    pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
    return bool(dd_ok and pf_ok)


def universe_snapshot(as_of: Optional[datetime.date] = None) -> dict:
    """Live, current universe composition -- candidate counts plus how many
    of the 40 stock candidates are genuinely S&P 500 members right now
    (not assumed from before any refactor)."""
    as_of = as_of or datetime.date.today()
    members_today = [t for t in universe.STOCK_MEMBERSHIP if universe.is_member(t, as_of)]
    non_members = [t for t in universe.STOCK_MEMBERSHIP if t not in members_today]
    return {
        "as_of": as_of.isoformat(),
        "etf_count": len(universe.ETF_TICKERS),
        "stock_candidate_count": len(universe.STOCK_MEMBERSHIP),
        "stock_member_count": len(members_today),
        "non_members": non_members,
        "total_candidates": len(universe.ALL_TICKERS),
        "live_tradeable_count": len(universe.ETF_TICKERS) + len(members_today),
    }


def live_universe_snapshot(as_of: Optional[datetime.date] = None) -> dict:
    """Live, current DYNAMIC S&P 500 candidate-pool composition — what
    run_full_validation(live_universe=True) and live order placement
    (brokers/trading212.py:maybe_place_orders()'s own candidate_tickers)
    both actually use. Distinct from universe_snapshot() above, which
    reports the OLD, frozen 73-ticker (33 ETF + 40 curated stock) research
    universe — still used, unchanged, by the three historical-reproduction
    engine overrides (uncapped=True / portfolio_mode=True / an explicit
    compounding-sweep call) specifically so their already-published
    figures stay exactly reproducible. See CLAUDE.md's "ETF sleeve removed
    from live trading" and "wire the momentum-only, full-S&P-500 selection
    into live execution" sections.

    Reports genuine S&P 500 INDEX MEMBERSHIP eligibility only — never T212
    tradeability, a materially different, live-execution-only concern.
    ETFs are structurally absent from this universe entirely (not merely
    flagged off), and stock instrument mappings are resolved dynamically
    per-ticker (brokers.trading212.resolve_and_cache_instrument()), not
    gated by any fixed list — so there is no T212-mapping-status concept
    left here to conflate with membership eligibility in the first place.
    """
    as_of = as_of or datetime.date.today()
    all_known = universe.get_all_known_stock_tickers()
    eligible_today = set(universe.get_live_stock_candidates(as_of=as_of))
    excluded = sorted(all_known - eligible_today)
    return {
        "as_of": as_of.isoformat(),
        "candidate_universe_count": len(all_known),
        "live_eligible_count": len(eligible_today),
        "excluded_count": len(excluded),
        "excluded_sample": excluded[:10],
    }


def run_full_validation(
    config: dict,
    start: pd.Timestamp = VALIDATION_START,
    end: Optional[pd.Timestamp] = None,
    portfolio_mode: bool = False,
    per_trade_pct: Optional[float] = None,
    max_deployed_pct_compounding: Optional[float] = None,
    uncapped: bool = False,
    mark_to_market: bool = False,
    live_universe: bool = False,
) -> dict:
    """Run the complete validated battery over [start, end] and return every
    figure the Backtest page / DD report need. end defaults to today.

    **Default behaviour (no flags passed) changed 2026-08-01: this now runs
    the SAME engine live order placement uses** —
    analysis.backtest.simulate_portfolio_compounding(), a genuinely
    compounding shared-account walk, sized via
    trading212_invest.per_trade_pct / trading212_invest.max_total_deployed
    read straight from *config* — the identical two config keys
    brokers/trading212.py reads for real order placement. This closes the
    exact gap CLAUDE.md's "Position-sizing caps ... binding-frequency
    investigation" and the two universe-broadening studies found: every
    prior default run here used an engine with no cross-ticker portfolio
    cap at all, which never modelled the one constraint shaping live trade
    selection the most. See CLAUDE.md's "Live execution switched to the
    compounding two-parameter scheme" section for the full rationale.

    Three explicit overrides exist purely to reproduce specific historical
    figures already published in CLAUDE.md on demand — none of them is the
    default, and passing one never changes config.yaml or live behaviour:

    - `uncapped=True` — forces analysis.backtest.run_candidate(), the
      original engine with no portfolio constraint of any kind (every
      ticker simulated fully independently against the same notional
      starting-capital baseline). Reproduces the "Current baseline" figures
      and every study run before 2026-08-01.
    - `portfolio_mode=True` — forces analysis.backtest.simulate_portfolio(),
      the fixed-notional three-parameter cap (max_position_gbp /
      max_open_positions / max_total_deployed, all read from config).
      Reproduces the "Portfolio-capped validation findings" figures.
    - `per_trade_pct` / `max_deployed_pct_compounding` (both must be
      provided together) — still calls simulate_portfolio_compounding(),
      but with these EXPLICIT values instead of reading per_trade_pct /
      max_total_deployed from config. Used for the 20-combination research
      sweep in CLAUDE.md's "Compounding portfolio backtest" section; not
      needed for normal use, where the config-sourced default already is
      "the real thing."

    A fourth, ORTHOGONAL flag — `mark_to_market` — switches whichever
    compounding variant is selected (explicit or config-default) from
    `simulate_portfolio_compounding()` (cost-basis deployment tracking) to
    `simulate_portfolio_compounding_mtm()` (genuine mark-to-market
    deployment tracking, matching what live trading's `investments.
    currentValue` actually reflects — see CLAUDE.md's "Live/backtest
    deployment-cap parity" section for why the two diverge and which one
    live trading really uses). Has no effect when `uncapped` or
    `portfolio_mode` is set, since neither of those engines has a
    deployment-cap concept this flag could apply to. Defaults `False` so
    every existing caller's behaviour (and every figure already published
    against the cost-basis engine) is completely unchanged unless a caller
    opts in explicitly.

    Priority when more than one is passed (only one is ever expected in
    practice): uncapped > explicit per_trade_pct/max_deployed_pct_compounding
    > portfolio_mode > the new config-sourced compounding default.
    `mark_to_market` layers on top of whichever of these wins, wherever it
    applies.

    A fifth, also-ORTHOGONAL flag — `live_universe` (2026-08-02) — switches
    the CANDIDATE UNIVERSE itself, independently of which engine or
    deployment-tracking basis is selected above. `False` (the default)
    keeps the OLD frozen 73-ticker (33 ETF + 40 curated stock)
    `analysis.universe.ALL_TICKERS` — required for `uncapped`,
    `portfolio_mode`, and the explicit compounding-sweep interface to keep
    reproducing their own already-published historical figures exactly,
    and left as every other caller's unchanged default. `True` replaces it
    with `analysis.universe.get_live_stock_candidates(as_of=end.date())` —
    the exact same dynamically-refreshed, point-in-time S&P 500 stock
    membership `brokers/trading212.py:maybe_place_orders()` itself reads
    for real order placement, with ETFs structurally absent (that function
    already excludes them) rather than merely flag-gated to zero. Still
    respects `trading212_invest.stock_trading_enabled` (defaults `True`) —
    disabling it empties the candidate pool, mirroring the per-ticker gate
    live order placement itself checks before ever considering a ticker.
    Meant to be paired with `mark_to_market=True` (see CLAUDE.md's "rebuild
    the backtest/DD-report page to genuinely run against the current, real
    live engine" — the only two flags this specific pairing needs; the
    default-sourced `per_trade_pct`/`max_total_deployed` engine selection
    above already matches live with no further flags required). The
    returned `"universe"` key reflects whichever universe this run
    actually used — `live_universe_snapshot()` when `True`,
    `universe_snapshot()` (unchanged) otherwise — so a caller never has to
    infer which figures are behind it.

    This is a CONTINUOUS-WALK run regardless of any flag above — one
    single walk over `[start, end]` with train/test computed by
    partitioning the resulting trades by `entry_date` after the fact, never
    a reset-at-boundary comparison between variants. See CLAUDE.md's "Two
    backtest conventions" section for why this distinction must always be
    stated explicitly to a reader, not left implicit — every caller of
    this function should surface that label itself; this function does not
    format it into any UI string.
    """
    end = end or pd.Timestamp(datetime.date.today())
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 50000))
    t212_cfg = config.get("trading212_invest", {})
    explicit_compounding = per_trade_pct is not None and max_deployed_pct_compounding is not None

    if uncapped:
        engine_used = "uncapped"
    elif explicit_compounding:
        engine_used = "compounding_explicit"
    elif portfolio_mode:
        engine_used = "portfolio_capped"
    else:
        engine_used = "compounding_default"

    if engine_used == "compounding_explicit":
        effective_per_trade_pct = per_trade_pct
        effective_max_deployed_pct = max_deployed_pct_compounding
    elif engine_used == "compounding_default":
        effective_per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))
        effective_max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.5))
    else:
        effective_per_trade_pct = None
        effective_max_deployed_pct = None

    gspc_df = get_price_df("^GSPC", days=8000)
    gspc_df = gspc_df.set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= start) & (gspc_close_full.index <= end)]

    # Asset-class candidacy gate — the identical shared function
    # (analysis.decision.is_asset_class_enabled()) brokers/trading212.py's
    # live decide phase checks before ever queuing a decision. Filtering
    # the ticker universe HERE, before any engine's tickers_cache is even
    # built, is what makes every downstream engine (run_candidate(),
    # simulate_portfolio(), simulate_portfolio_compounding()) respect the
    # switch identically with no per-engine change needed — a disabled
    # asset class is never a candidate for any of them, exactly as if it
    # had been removed from analysis.universe.ALL_TICKERS entirely. See
    # CLAUDE.md's "Clean, independent ETF/stock trading switches" section.
    etf_trading_enabled = is_asset_class_enabled(is_etf=True, config=config)
    stock_trading_enabled = is_asset_class_enabled(is_etf=False, config=config)
    if live_universe:
        # The genuinely dynamic, live-execution candidate pool (2026-08-02)
        # — ETFs are structurally absent from get_live_stock_candidates()
        # itself (not merely flag-gated to zero, matching how live order
        # placement's own candidate_tickers is built — see
        # brokers/trading212.py:maybe_place_orders()), so etf_trading_
        # enabled has no candidate-universe effect here; only
        # stock_trading_enabled (checked once, since every ticker in this
        # pool is is_etf=False by construction — a per-ticker call to
        # is_asset_class_enabled() would always resolve identically) can
        # empty the pool, mirroring the per-ticker gate live's own decide
        # phase checks before ever considering a ticker.
        candidate_tickers = (
            universe.get_live_stock_candidates(as_of=end.date()) if stock_trading_enabled else []
        )
    else:
        candidate_tickers = [
            t for t in universe.ALL_TICKERS
            if is_asset_class_enabled(is_etf=t in universe.ETF_TICKERS, config=config)
        ]

    caches = []
    for ticker in candidate_tickers:
        c = precompute_ticker(ticker, config)
        if c is not None:
            c = slice_ticker(c, start, end)
        caches.append(c)
    n_ok = sum(1 for c in caches if c is not None)

    if engine_used == "uncapped":
        all_trades = run_candidate(caches, config, gspc_close)
    elif engine_used == "portfolio_capped":
        all_trades = simulate_portfolio(caches, config, gspc_close)
    elif mark_to_market:  # compounding_explicit or compounding_default, mark-to-market variant
        all_trades = simulate_portfolio_compounding_mtm(
            caches, config, gspc_close, effective_per_trade_pct, effective_max_deployed_pct,
        )
    else:  # compounding_explicit or compounding_default, cost-basis variant (unchanged default)
        all_trades = simulate_portfolio_compounding(
            caches, config, gspc_close, effective_per_trade_pct, effective_max_deployed_pct,
        )
    trades_df = pd.DataFrame(all_trades)
    if not trades_df.empty:
        trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"])
        trades_df["exit_date"] = pd.to_datetime(trades_df["exit_date"])

    if trades_df.empty:
        train_trades, test_trades = [], []
    else:
        train_trades = trades_df[trades_df["entry_date"] < VALIDATION_TRAIN_TEST_SPLIT].to_dict("records")
        test_trades = trades_df[trades_df["entry_date"] >= VALIDATION_TRAIN_TEST_SPLIT].to_dict("records")

    train_metrics = compute_metrics_fixed(train_trades, starting_capital)
    test_metrics = compute_metrics_fixed(test_trades, starting_capital)
    full_metrics = full_metrics_fixed(all_trades, starting_capital)

    # Genuine peak-relative drawdown, alongside (never replacing) the
    # fixed-starting-capital-denominator max_drawdown_pct already inside
    # train_metrics/test_metrics/full_metrics above -- the two answer
    # different questions and both must stay visible, per this project's
    # own standing "state the convention explicitly" discipline (see
    # CLAUDE.md's "No market-cap gate at all" section for why the gap
    # between them can be large, not a rounding difference). Computed
    # unconditionally, not only for live_universe runs -- any run with a
    # real equity curve can show this same distortion.
    peak_relative_drawdown = {
        "train": compute_peak_relative_drawdown_pct(train_trades, starting_capital),
        "test": compute_peak_relative_drawdown_pct(test_trades, starting_capital),
        "full": compute_peak_relative_drawdown_pct(all_trades, starting_capital),
    }

    train_sp_cagr, train_sp_dd = _cagr_dd_for_series(gspc_close_full, start, VALIDATION_TRAIN_TEST_SPLIT)
    test_sp_cagr, test_sp_dd = _cagr_dd_for_series(gspc_close_full, VALIDATION_TRAIN_TEST_SPLIT, end)
    full_sp_cagr, full_sp_dd = _cagr_dd_for_series(gspc_close_full, start, end)

    blocks_raw = make_blocks(start, end, n_blocks=16)
    block_results = []
    n_pass = 0
    for i, (b_start, b_end) in enumerate(blocks_raw):
        if trades_df.empty:
            strat_trades = []
        else:
            strat_trades = trades_df[
                (trades_df["exit_date"] >= b_start) & (trades_df["exit_date"] <= b_end)
            ].to_dict("records")
        strat_m = compute_metrics_fixed(strat_trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)

        block_pass = _block_passes(strat_m, sp_dd)
        if block_pass:
            n_pass += 1
        block_results.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "n_trades": len(strat_trades), "strat_cagr": strat_m["cagr_pct"],
            "strat_dd": strat_m["max_drawdown_pct"],
            "strat_pf": strat_m.get("profit_factor", 0.0),
            "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
            "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
            "passed": block_pass,
        })

    wf = walk_forward_pf(all_trades)
    cost_sensitivity = compute_cost_sensitivity(all_trades, starting_capital, cost_bps_list=(0, 10, 25))
    gbp_growth = compute_gbp_growth(
        gspc_close_full, train_trades, test_trades, starting_capital,
        start, VALIDATION_TRAIN_TEST_SPLIT, end,
    )

    return {
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "starting_capital_gbp": starting_capital,
        "engine_used": engine_used,
        "deployment_basis": (
            "mark_to_market" if (mark_to_market and engine_used.startswith("compounding")) else "cost_basis"
        ),
        "per_trade_pct": effective_per_trade_pct,
        "max_deployed_pct": effective_max_deployed_pct,
        "live_universe": live_universe,
        "backtest_convention": "continuous_walk",
        # Reflects whichever universe THIS run actually used -- the
        # dynamic live pool when live_universe=True, the frozen 73-ticker
        # research universe otherwise -- so a caller never has to infer
        # which figures are behind the returned counts.
        "universe": live_universe_snapshot(as_of=end.date()) if live_universe else universe_snapshot(),
        # n_tickers_total reflects the CANDIDATE pool actually attempted for
        # this run (after the etf_trading_enabled/stock_trading_enabled
        # gate below) -- equal to len(universe.ALL_TICKERS) whenever both
        # switches are at their default (True), so this is a no-op change
        # for every config predating this feature.
        "n_tickers_with_data": n_ok, "n_tickers_total": len(candidate_tickers),
        "etf_trading_enabled": etf_trading_enabled,
        "stock_trading_enabled": stock_trading_enabled,
        "total_trades": len(all_trades),
        "train": train_metrics, "test": test_metrics,
        "train_sp": {"cagr_pct": train_sp_cagr, "max_drawdown_pct": train_sp_dd},
        "test_sp": {"cagr_pct": test_sp_cagr, "max_drawdown_pct": test_sp_dd},
        "full_sp": {"cagr_pct": full_sp_cagr, "max_drawdown_pct": full_sp_dd},
        "full_raw": full_metrics["raw"], "full_ex_best": full_metrics["ex_best"],
        "blocks": block_results, "n_blocks_pass": n_pass, "n_blocks_total": len(blocks_raw),
        "walk_forward": wf,
        "cost_sensitivity": cost_sensitivity,
        "gbp_growth": gbp_growth,
        "peak_relative_drawdown": peak_relative_drawdown,
        "trades": all_trades,
    }
