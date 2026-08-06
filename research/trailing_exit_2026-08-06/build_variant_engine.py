"""Generate variant_engine.py from the PRODUCTION mtm engine by applying a
small number of targeted, auditable string replacements.

Deliberately mechanical rather than hand-transcribed: everything this script
does not explicitly replace is byte-identical to
analysis/backtest.py:simulate_portfolio_compounding_mtm(). The fidelity gate
(run_fidelity_check.py) then proves that variant="baseline" reproduces the
production engine's output exactly, which is what makes the copy trustworthy.
"""
from pathlib import Path

SRC = Path("analysis/backtest.py")
DST = Path("trailing/variant_engine.py")
FN_START, FN_END = 1174, 1668          # simulate_portfolio_compounding_mtm

body = "\n".join(SRC.read_text().splitlines()[FN_START - 1:FN_END])

# --- 1. signature ----------------------------------------------------------
old_sig = """def simulate_portfolio_compounding_mtm(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    collect_rejections: bool = False,
):"""
new_sig = """def simulate_variant(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    collect_rejections: bool = False,
    variant: str = "baseline",
):"""
assert body.count(old_sig) == 1, "signature not found exactly once"
body = body.replace(old_sig, new_sig)

# --- 2. exit evaluation ----------------------------------------------------
old_exit = """            exit_reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=stop_check_price,
                stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
                max_holding_days=st["max_hold"],
            )
            if exit_reason is None and dt == st["frame"].index[-1]:
                exit_reason = "END_OF_DATA\""""
new_exit = '''            # ---- variant-aware exit evaluation ------------------------
            # Non-look-ahead by construction: the trailing level is built
            # from the highest high and the ATR observed THROUGH YESTERDAY
            # (high_water/trail_atr are ratcheted only AFTER today's exit
            # check, below), then tested against TODAY's low. Setting the
            # level from today's own high and triggering it on today's low
            # would silently assume the high preceded the low intraday.
            trail_price = None
            if variant in ("T1", "T2") and ot.get("high_water") is not None and ot.get("trail_atr"):
                mult = TRAIL_ATR_MULT[variant]
                trail_price = ot["high_water"] - mult * ot["trail_atr"]

            if variant in ("T1", "T2"):
                # 30-day cap removed. max_holding_days=None is the SHARED
                # function's own documented "unknown, don't trigger this
                # check" input -- not a local re-implementation of its
                # priority chain.
                exit_reason = evaluate_exit_reason(
                    crisis_now=crisis_now, price=stop_check_price,
                    stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
                    max_holding_days=None,
                )
                # The trailing stop takes MAX_HOLDING's slot: LAST in the
                # chain, so CRISIS_EXIT / STOP_LOSS / SIGNAL_OFF all keep
                # strict precedence over it, exactly as specified.
                if exit_reason is None and trail_price is not None and stop_check_price <= trail_price:
                    exit_reason = "TRAILING_STOP"
            elif variant == "T3":
                exit_reason = evaluate_exit_reason(
                    crisis_now=crisis_now, price=stop_check_price,
                    stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
                    max_holding_days=st["max_hold"],
                )
                # Trend-conditional cap release: defer the cap exit while
                # the close is above MA_14, to a hard 60-calendar-day
                # ceiling. Every other exit reason is untouched.
                if exit_reason == "MAX_HOLDING" and holding_days < T3_HARD_CEILING_DAYS:
                    ma14 = row.get("ma14")
                    if ma14 is not None and pd.notna(ma14) and price > float(ma14):
                        exit_reason = None
            else:
                exit_reason = evaluate_exit_reason(
                    crisis_now=crisis_now, price=stop_check_price,
                    stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
                    max_holding_days=st["max_hold"],
                )

            if exit_reason is None and dt == st["frame"].index[-1]:
                exit_reason = "END_OF_DATA"'''
assert body.count(old_exit) == 1, "exit block not found exactly once"
body = body.replace(old_exit, new_exit)

# --- 3. trailing fill (same intraday-touch convention as STOP_LOSS) --------
old_fill = """                exit_price = min(stop_price, open_price)
            else:
                exit_price = price"""
new_fill = """                exit_price = min(stop_price, open_price)
            elif exit_reason == "TRAILING_STOP":
                # IDENTICAL intraday-touch fill convention to STOP_LOSS
                # above: achievable at the trailing level itself if the
                # open was still above it, at the open if the day gapped
                # straight through it.
                open_val = row.get("open")
                open_price = float(open_val) if open_val is not None and pd.notna(open_val) else price
                exit_price = min(trail_price, open_price)
            else:
                exit_price = price"""
assert body.count(old_fill) == 1, "fill block not found exactly once"
body = body.replace(old_fill, new_fill)

# --- 4. ratchet the trailing state forward on a position that stays open ---
old_close = """                cash += ot["position_gbp"] + pnl_gbp
                st["open_trade"] = None
                st["last_exit_date"] = dt"""
new_close = '''                cash += ot["position_gbp"] + pnl_gbp
                st["open_trade"] = None
                st["last_exit_date"] = dt
            elif variant in ("T1", "T2"):
                # Position stays open -> ratchet the trailing state with
                # TODAY's bar, so tomorrow's level reflects data through
                # today and never through tomorrow.
                hi = row.get("high")
                if hi is not None and pd.notna(hi):
                    hi = float(hi)
                    ot["high_water"] = hi if ot.get("high_water") is None else max(ot["high_water"], hi)
                atr_now = row.get("atr")
                if atr_now is not None and pd.notna(atr_now):
                    ot["trail_atr"] = float(atr_now)'''
assert body.count(old_close) == 1, "close block not found exactly once"
body = body.replace(old_close, new_close)

# --- 5. seed the trailing state at entry -----------------------------------
old_open = """            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
            }"""
new_open = """            entry_high = row.get("high")
            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
                # Trailing state seeded from the ENTRY bar; unused unless
                # variant is T1/T2, and inert for baseline/T3.
                "high_water": (float(entry_high) if entry_high is not None and pd.notna(entry_high) else price),
                "trail_atr": atr,
            }"""
assert body.count(old_open) == 1, "open block not found exactly once"
body = body.replace(old_open, new_open)

HEADER = '''"""Variant trade-generation engine for the trailing-exit investigation.

GENERATED by trailing/build_variant_engine.py from
analysis/backtest.py:simulate_portfolio_compounding_mtm() (lines %d-%d).
Everything not explicitly replaced by that generator is byte-identical to
production. Do not hand-edit; regenerate instead.

variant="baseline" must reproduce the production engine exactly -- proved by
trailing/run_fidelity_check.py before any variant comparison is trusted.

Variants:
  T1  Chandelier exit, 3.0 x ATR(14) below the highest high since entry;
      30-day MAX_HOLDING cap removed.
  T2  As T1 at 2.5 x ATR(14).
  T3  30-day cap retained but its exit deferred while close > MA_14,
      hard ceiling 60 calendar days.

In every variant CRISIS_EXIT / STOP_LOSS / SIGNAL_OFF are untouched and keep
their existing priority; the trailing stop occupies MAX_HOLDING's slot at the
END of the chain. All exit decisions still route through the shared
analysis/decision.py:evaluate_exit_reason().
"""
import numpy as np
import pandas as pd

from analysis import crisis, universe
from analysis.backtest import (
    _MIN_VAR_RETURNS,
    _would_deploy_pct,
)
from analysis.decision import (
    evaluate_exit_reason,
    is_position_size_viable,
    is_reentry_blocked,
    passes_entry_gate,
    passes_momentum_floor,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
    resolve_min_momentum_floor,
    resolve_reentry_cooldown_days,
)
from analysis.universe import get_max_holding_days
from analysis.volatility import _resolve_entry_stop, _var_position_size_core

TRAIL_ATR_MULT = {"T1": 3.0, "T2": 2.5}
T3_HARD_CEILING_DAYS = 60
VARIANTS = ("baseline", "T1", "T2", "T3")


''' % (FN_START, FN_END)

DST.write_text(HEADER + body + "\n")
print(f"wrote {DST} ({len(body.splitlines())} body lines, 5 replacements applied)")
