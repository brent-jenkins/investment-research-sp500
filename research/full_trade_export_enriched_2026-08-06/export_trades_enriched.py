"""Enriched 2007-2026 trade-history CSV export -- follow-up to tonight's
first export (sp500invest_full_trade_export_2007-2026.csv, 3,161 trades).
Same purpose: personal manual exploration, NOT a hypothesis test. No
variant comparison, no block validation needed.

Re-run FRESH against the current implementation (same discipline as the
first export) -- environment re-verified current before running anything:
git status clean (only expected untracked research-output directories),
no code changes since the first export's own run tonight, price_data
still spans 2007-01-01 -> 2026-08-03 across 540 tickers, unchanged.

CRITICAL DESIGN CHOICE, stated per the task's own explicit instruction:
"already computed internally by the real engine" fields (daily_var,
holding_var, ATR-at-entry) are captured via a MONKEYPATCH of the two
low-level shared functions (analysis.volatility._resolve_entry_stop,
analysis.volatility._var_position_size_core) as bound in
analysis.backtest's own module namespace -- NOT by re-deriving the VaR/
ATR formulas independently in this script. The wrapper calls straight
through to the real function every time (zero behavioural change to the
simulation -- verified below by reproducing the exact established
3,161-trade/56.8% MAX_HOLDING baseline) and simply records each call's
real arguments/return values. analysis/backtest.py itself is NEVER
edited -- Python resolves _resolve_entry_stop/_var_position_size_core as
module-global names at call time via analysis.backtest.__dict__, so
reassigning them from outside the module redirects every call made
inside simulate_portfolio_compounding_mtm() without touching the file on
disk at all. This is the same "mock only what's necessary, run the real
decision code" pattern this project's own live/backtest parity tests use
throughout.

WHY CALL-ORDER PAIRING ISN'T ENOUGH, AND WHAT'S USED INSTEAD: at the real
call site (analysis/backtest.py:1344/1353, inside _admit_group()),
_var_position_size_core() is only called when the candidate has >=
_MIN_VAR_RETURNS days of prior returns -- otherwise position sizing falls
back to a flat rule and _var_position_size_core() is never called at all
for that candidate. So a naive "zip the two call lists in order" would
misalign the moment any single candidate takes the fallback path. Instead:
each _resolve_entry_stop() call opens a new pending record; each
_var_position_size_core() call (when it happens) fills in the MOST
RECENTLY opened pending record, since real execution is single-threaded
and never interleaves two candidates' stop/sizing calls. The resulting
records are then joined back to the engine's own final trade list via an
exact-match composite key (entry_price, stop_distance_pct) -- both values
are stored verbatim (same Python float, not recomputed) on the emitted
trade dict, so this join is exact, not approximate; collision-checked
directly before trusting it (see the run log).

Columns NOT actually engine-internal, despite being listed alongside
engine-internal ones in the task brief -- stated honestly here per the
task's own "clearly distinguish" instruction:
  - momentum-strength band label at entry: the engine only ever uses the
    raw continuous momentum value (for the >floor gate and the priority
    sort) -- it has no concept of Weak/Moderate/Strong/Very Strong bands
    anywhere. The band is main.py's own display-only reconstruction
    (_compute_momentum_strength_bands()/_momentum_strength_label()),
    applied here at each trade's ENTRY date against that date's real
    eligible-candidate pool -- identical methodology to two of tonight's
    earlier investigations (momentum_horizon, momentum_volume_reeval_cap),
    reused verbatim, not re-derived.
  - already_peaked_at_exit: purely retrospective price-history check
    (max close during the hold vs. the actual exit price) -- the engine
    has no such concept; this is the same definition momentum_horizon
    used, recomputed here from the real, stored close-price series.

Genuinely new data (moving averages, raw entry/exit price, volume,
sector/industry) is pulled/computed fresh for this export and is NOT a
re-test of anything -- stated plainly in the report. Exit-point volume
specifically was already formally tested against outcome earlier tonight
(momentum_volume_reeval_cap_2026-08-06 -- rejected, no relationship
found); included here for descriptive completeness/cross-referencing
only, not because it's an open question.

Honest limitation carried over from the first export: price_data is
daily bars only -- entry_date/exit_date are dates, no intraday timestamp
exists or is fabricated.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import time

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

import analysis.backtest as bt_module
from analysis import crisis, universe
from analysis.decision import passes_entry_gate, passes_momentum_floor, resolve_min_momentum_floor
from analysis.validation import run_full_validation
from analysis.volatility import _resolve_entry_stop as _real_resolve_entry_stop
from analysis.volatility import _var_position_size_core as _real_var_position_size_core
from data.ingestion import get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\full_trade_export_enriched_2026-08-06'
DOWNLOADS_PATH = r'C:\Users\user\Downloads\sp500invest_full_trade_export_enriched_2007-2026.csv'
END = pd.Timestamp("2026-08-03")
START = pd.Timestamp("2007-01-01")

MA_WINDOWS = (14, 30, 50)
VOL_WINDOW = 20  # unused here beyond matching the earlier investigation's own convention; kept for reference


# ---------------------------------------------------------------------------
# Monkeypatch: capture real engine-internal sizing/stop state, in call order,
# without editing analysis/backtest.py.
# ---------------------------------------------------------------------------
_captured: list[dict] = []


def _wrapped_resolve_entry_stop(direction, price, atr, config):
    stop, stop_distance_pct = _real_resolve_entry_stop(direction, price, atr, config)
    _captured.append({
        "entry_price": price,
        "atr_at_entry": atr,
        "stop_price": stop,
        "stop_distance_pct": stop_distance_pct,
        "daily_var": None,
        "holding_var": None,
    })
    return stop, stop_distance_pct


def _wrapped_var_position_size_core(log_returns, account_value_gbp, max_position_gbp,
                                     holding_days, var_confidence, max_risk_pct, min_position_gbp):
    position_gbp, daily_var, holding_var = _real_var_position_size_core(
        log_returns, account_value_gbp, max_position_gbp, holding_days,
        var_confidence, max_risk_pct, min_position_gbp,
    )
    if _captured:
        _captured[-1]["daily_var"] = daily_var
        _captured[-1]["holding_var"] = holding_var
    return position_gbp, daily_var, holding_var


def _bands(values: pd.Series) -> dict:
    """Byte-identical to main.py:_compute_momentum_strength_bands()."""
    vals = values.dropna()
    if len(vals) < 4:
        return {}
    return {"q25": vals.quantile(0.25), "q75": vals.quantile(0.75), "q90": vals.quantile(0.90)}


def _label(val, bands: dict) -> str:
    """Byte-identical to main.py:_momentum_strength_label()."""
    if not bands or pd.isna(val):
        return "-"
    if val >= bands["q90"]:
        return "Very Strong"
    if val >= bands["q75"]:
        return "Strong"
    if val >= bands["q25"]:
        return "Moderate"
    return "Weak"


def precompute_ticker_export(ticker: str, config: dict):
    """Extends analysis.backtest.precompute_ticker() with volume and
    14/30/50-day moving averages -- additive only, does not change what
    the real engine itself computes or uses (the real engine's own
    precompute_ticker() is untouched; this is a separate, parallel
    function for this export's own descriptive fields)."""
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    from analysis.backtest import _atr
    from analysis import signals as signals_mod
    atr_s = _atr(high, low, close, config["signals"]["atr_period"])
    momentum_s = signals_mod.compute_momentum_series(close)
    sig = signals_mod.compute_signal_series(close)

    frame = pd.DataFrame({
        "close": close, "atr": atr_s, "signal": sig, "momentum": momentum_s, "volume": volume,
    })
    for w in MA_WINDOWS:
        frame[f"ma_{w}"] = close.rolling(w).mean()
    return frame


def main():
    t0 = time.time()
    print("=== Enriched 2007-2026 trade-history CSV export ===", flush=True)

    # ------------------------------------------------------------------
    # 1. Real backtest run -- FRESH, current code, with the monkeypatch
    #    in place to capture engine-internal sizing/stop state.
    # ------------------------------------------------------------------
    bt_module._resolve_entry_stop = _wrapped_resolve_entry_stop
    bt_module._var_position_size_core = _wrapped_var_position_size_core
    try:
        print("Running real backtest (simulate_portfolio_compounding_mtm, "
              "live_universe=True, continuous walk 2007-2026, "
              "engine-internal capture active)...", flush=True)
        result = run_full_validation(
            CONFIG, start=START, end=END, mark_to_market=True, live_universe=True,
        )
    finally:
        bt_module._resolve_entry_stop = _real_resolve_entry_stop
        bt_module._var_position_size_core = _real_var_position_size_core

    print(f"  engine_used={result['engine_used']} deployment_basis={result['deployment_basis']} "
          f"backtest_convention={result['backtest_convention']}", flush=True)
    print(f"  total_trades={result['total_trades']}  n_tickers_with_data={result['n_tickers_with_data']}"
          f"/{result['n_tickers_total']}", flush=True)

    trades = result["trades"]
    trades_df = pd.DataFrame(trades)
    exit_reason_counts = trades_df["exit_reason"].value_counts()
    n_max_hold = int(exit_reason_counts.get("MAX_HOLDING", 0))
    print("  exit_reason breakdown:\n" + exit_reason_counts.to_string(), flush=True)
    print(f"  MAX_HOLDING share: {100*n_max_hold/len(trades_df):.1f}% "
          f"(established baseline: 3161 trades / 56.8% MAX_HOLDING)", flush=True)
    assert len(trades_df) == 3161, f"Trade count drifted from baseline: {len(trades_df)}"
    assert n_max_hold == 1796, f"MAX_HOLDING count drifted from baseline: {n_max_hold}"
    print("  Baseline reproduction confirmed exactly -- monkeypatch is behaviourally transparent.", flush=True)

    print(f"\n  {len(_captured)} engine-internal stop/sizing calls captured "
          f"(includes candidates later rejected on capacity/viability grounds, "
          f"not just admitted trades)", flush=True)

    # ------------------------------------------------------------------
    # 2. Join captured engine-internal records to the final trade list via
    #    the exact-match (entry_price, stop_distance_pct) composite key.
    #    Collision-checked before trusting it.
    # ------------------------------------------------------------------
    join_map: dict[tuple, dict] = {}
    n_collisions = 0
    for rec in _captured:
        key = (rec["entry_price"], rec["stop_distance_pct"])
        if key in join_map:
            n_collisions += 1
        join_map[key] = rec  # last-write-wins; collision count tracked separately
    print(f"  join key collisions among {len(_captured)} captured records: {n_collisions}", flush=True)

    n_matched = 0
    n_var_fallback = 0
    for tr in trades:
        key = (tr["entry_price"], tr["stop_distance_pct"])
        rec = join_map.get(key)
        if rec is not None:
            n_matched += 1
            tr["_atr_at_entry"] = rec["atr_at_entry"]
            tr["_daily_var"] = rec["daily_var"]
            tr["_holding_var"] = rec["holding_var"]
            if rec["daily_var"] is None:
                n_var_fallback += 1
        else:
            tr["_atr_at_entry"] = None
            tr["_daily_var"] = None
            tr["_holding_var"] = None
    print(f"  {n_matched}/{len(trades)} trades matched to a captured engine-internal record", flush=True)
    print(f"  {n_var_fallback}/{n_matched} matched trades used the flat-sizing fallback "
          f"(insufficient VaR history at entry -- daily_var/holding_var genuinely None, "
          f"not a join failure)", flush=True)
    assert n_matched == len(trades), "Every real trade must join to exactly one captured record"

    # ------------------------------------------------------------------
    # 3. Reference series (unchanged from the first export).
    # ------------------------------------------------------------------
    print("\nLoading reference series (^GSPC, GBPUSD=X, EURUSD=X)...", flush=True)

    def _series_for(ticker):
        df = get_price_df(ticker, days=8000).set_index("date")
        df.index = pd.to_datetime(df.index)
        return df["close"].astype(float).sort_index()

    def _lookup(series, date):
        sub = series.loc[:date]
        return float(sub.iloc[-1]) if len(sub) else None

    gspc = _series_for("^GSPC")
    gbpusd = _series_for("GBPUSD=X")
    eurusd = _series_for("EURUSD=X")

    # ------------------------------------------------------------------
    # 4. Per-ticker frames (close/atr/signal/momentum/volume/MAs) for
    #    every live candidate -- needed for momentum-band, already-peaked,
    #    moving-average, and volume fields.
    # ------------------------------------------------------------------
    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"\nBuilding export frames for {len(candidate_tickers)} live candidates...", flush=True)
    frames = {}
    for i, ticker in enumerate(candidate_tickers):
        f = precompute_ticker_export(ticker, CONFIG)
        if f is not None:
            frames[ticker] = f
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(candidate_tickers)} tickers cached ({time.time()-t0:.0f}s elapsed)", flush=True)
    print(f"  {len(frames)}/{len(candidate_tickers)} tickers have usable frames", flush=True)

    gspc_close_full = gspc
    raw_crisis_gspc = crisis.compute_raw_trigger(gspc_close_full)
    entry_blocked_gspc = crisis.extend_trigger(raw_crisis_gspc)
    min_momentum_floor = resolve_min_momentum_floor(CONFIG)

    # ------------------------------------------------------------------
    # 5. Sector/industry -- genuinely new data pull, one yfinance .info
    #    call per unique ticker appearing in the trade list.
    # ------------------------------------------------------------------
    unique_tickers = sorted(trades_df["ticker"].unique())
    print(f"\nPulling sector/industry for {len(unique_tickers)} unique traded tickers via yfinance...", flush=True)
    sector_map = {}
    n_sector_failed = 0
    for i, ticker in enumerate(unique_tickers):
        try:
            info = yf.Ticker(ticker).info
            sector_map[ticker] = (info.get("sector"), info.get("industry"))
        except Exception as e:
            sector_map[ticker] = (None, None)
            n_sector_failed += 1
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(unique_tickers)} sector lookups done ({time.time()-t0:.0f}s elapsed)", flush=True)
    print(f"  sector/industry lookup failed for {n_sector_failed}/{len(unique_tickers)} tickers", flush=True)

    # ------------------------------------------------------------------
    # 6. Build export rows.
    # ------------------------------------------------------------------
    print(f"\nBuilding {len(trades)} export rows...", flush=True)
    pool_cache = {}
    rows = []
    n_band_missing = 0
    for tr in trades:
        ticker = tr["ticker"]
        entry_date = pd.Timestamp(tr["entry_date"])
        exit_date = pd.Timestamp(tr["exit_date"])
        frame = frames.get(ticker)

        momentum_at_entry = None
        strength_band = "-"
        already_peaked = None
        ma_entry = {w: None for w in MA_WINDOWS}
        ma_exit = {w: None for w in MA_WINDOWS}
        vol_entry = None
        vol_exit = None

        if frame is not None and entry_date in frame.index:
            m = frame.loc[entry_date, "momentum"]
            momentum_at_entry = None if pd.isna(m) else float(m)

            if entry_date not in pool_cache:
                pool_momentum = []
                crisis_val = bool(entry_blocked_gspc.reindex([entry_date]).ffill().iloc[0]) \
                    if len(entry_blocked_gspc) else False
                for t2, f2 in frames.items():
                    if entry_date not in f2.index:
                        continue
                    row2 = f2.loc[entry_date]
                    sig_on2 = bool(row2["signal"]) if pd.notna(row2["signal"]) else False
                    if not sig_on2:
                        continue
                    is_mem2 = universe.is_member(t2, entry_date.date())
                    mom2 = row2["momentum"]
                    mom2 = float(mom2) if pd.notna(mom2) else None
                    if not passes_entry_gate(sig_on=sig_on2, is_member=is_mem2, entry_blocked=crisis_val):
                        continue
                    if not passes_momentum_floor(mom2, min_momentum_floor):
                        continue
                    pool_momentum.append(mom2)
                pool_cache[entry_date] = _bands(pd.Series(pool_momentum))
            bands = pool_cache[entry_date]
            strength_band = _label(momentum_at_entry, bands)
            if strength_band == "-":
                n_band_missing += 1

            vol_entry = frame.loc[entry_date, "volume"]
            vol_entry = None if pd.isna(vol_entry) else float(vol_entry)
            for w in MA_WINDOWS:
                v = frame.loc[entry_date, f"ma_{w}"]
                ma_entry[w] = None if pd.isna(v) else float(v)

        if frame is not None and exit_date in frame.index:
            close = frame["close"].astype(float)
            entry_loc = frame.index.get_loc(entry_date) if entry_date in frame.index else None
            exit_loc = frame.index.get_loc(exit_date)
            exit_price = float(tr["exit_price"])
            if entry_loc is not None:
                during_hold_max = float(close.iloc[entry_loc:exit_loc + 1].max())
                already_peaked = bool(exit_price < during_hold_max * 0.999)
            vol_exit = frame.loc[exit_date, "volume"]
            vol_exit = None if pd.isna(vol_exit) else float(vol_exit)
            for w in MA_WINDOWS:
                v = frame.loc[exit_date, f"ma_{w}"]
                ma_exit[w] = None if pd.isna(v) else float(v)

        sector, industry = sector_map.get(ticker, (None, None))

        rows.append({
            "ticker": ticker,
            "win_loss": "WIN" if tr["is_win"] else "LOSS",
            "pnl_gbp": round(tr["pnl_gbp"], 2),
            "pnl_pct": round(tr["pnl_pct"] * 100, 4),
            "entry_date": entry_date.date().isoformat(),
            "exit_date": exit_date.date().isoformat(),
            "holding_days": tr["holding_days"],
            "entry_price": tr["entry_price"],
            "exit_price": tr["exit_price"],
            "entry_sp500_level": _lookup(gspc, entry_date),
            "exit_sp500_level": _lookup(gspc, exit_date),
            "entry_gbpusd_reporting_only": _lookup(gbpusd, entry_date),
            "exit_gbpusd_reporting_only": _lookup(gbpusd, exit_date),
            "entry_eurusd_reporting_only": _lookup(eurusd, entry_date),
            "exit_eurusd_reporting_only": _lookup(eurusd, exit_date),
            "exit_reason": tr["exit_reason"],
            "momentum_at_entry_pct": round(momentum_at_entry * 100, 4) if momentum_at_entry is not None else None,
            "momentum_strength_band_at_entry": strength_band,
            # --- engine-internal (captured via monkeypatch, Section 1-2) ---
            "position_gbp_at_entry": tr["position_gbp"],
            "stop_distance_pct": tr["stop_distance_pct"],
            "stop_distance_gbp": round(tr["position_gbp"] * tr["stop_distance_pct"] / 100.0, 2),
            "atr_at_entry": tr["_atr_at_entry"],
            "daily_var_at_entry": tr["_daily_var"],
            "holding_var_at_entry": tr["_holding_var"],
            # --- retrospective, price-history-derived (not engine state) ---
            "already_peaked_at_exit": already_peaked,
            # --- genuinely new descriptive fields ---
            "volume_at_entry": vol_entry,
            "volume_at_exit": vol_exit,
            "ma_14_at_entry": ma_entry[14], "ma_30_at_entry": ma_entry[30], "ma_50_at_entry": ma_entry[50],
            "ma_14_at_exit": ma_exit[14], "ma_30_at_exit": ma_exit[30], "ma_50_at_exit": ma_exit[50],
            "sector": sector,
            "industry": industry,
        })

    out_df = pd.DataFrame(rows).sort_values("entry_date").reset_index(drop=True)
    print(f"  momentum_strength_band_at_entry = '-' (pool < 4 candidates that day) for "
          f"{n_band_missing}/{len(trades)} trades", flush=True)

    out_df.to_csv(f"{OUT_DIR}/full_trade_export_enriched_2007-2026.csv", index=False)
    print(f"\nWrote {len(out_df)} rows -> {OUT_DIR}/full_trade_export_enriched_2007-2026.csv", flush=True)
    out_df.to_csv(DOWNLOADS_PATH, index=False)
    print(f"Also wrote a copy -> {DOWNLOADS_PATH}", flush=True)

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
