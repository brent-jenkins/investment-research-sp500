"""Sec 4 / Sec 11.2 -- THE REFLECTION TEST.

Proves evaluate_short_exit_reason() is an exact mirror of the production
analysis/decision.py:evaluate_exit_reason(), mechanically, over >=100,000
randomized input tuples plus explicit boundary cases.

The identity: for a short at entry E with stop S = E+d, reflecting price about
the entry (P' = 2E-P) and reflecting the stop (S' = E-d) gives

    P >= S   <=>   2E-P <= 2E-S   <=>   P' <= S'

so the short's stop condition IS the long's stop condition on the reflected
series. Everything else (priority order, None-tolerance, boundaries) must pass
through unchanged. A single mismatch anywhere blocks the study.

This is the answer to an invariant that cannot be satisfied directly: the
production evaluator hardcodes `price <= stop_price` and cannot be called for
shorts, and analysis/decision.py must not grow a research-only branch. Rather
than write the mirror carefully and hope, the mirror is verified against the
real production function itself.
"""
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from analysis.decision import evaluate_exit_reason          # noqa: E402
from ls_primitives import evaluate_short_exit_reason        # noqa: E402

N_RANDOM = 200_000
SEED = 20260811


def check(crisis_now, E, P, d, sig_on, holding_days, max_holding_days):
    """Return (short_reason, reflected_long_reason)."""
    short_stop = E + d
    long_price = 2 * E - P
    long_stop = E - d
    got = evaluate_short_exit_reason(
        crisis_now=crisis_now, price=P, stop_price=short_stop, sig_on=sig_on,
        holding_days=holding_days, max_holding_days=max_holding_days,
    )
    want = evaluate_exit_reason(
        crisis_now=crisis_now, price=long_price, stop_price=long_stop, sig_on=sig_on,
        holding_days=holding_days, max_holding_days=max_holding_days,
    )
    return got, want


def main() -> None:
    rng = random.Random(SEED)
    mismatches = []
    n_by_reason = {}

    # ---- 1. Randomized sweep -------------------------------------------
    for _ in range(N_RANDOM):
        E = rng.uniform(1.0, 500.0)
        d = rng.uniform(0.01, 0.60) * E
        # Sample price across, at, and beyond the stop on both sides.
        P = E + d * rng.uniform(-3.0, 3.0)
        crisis_now = rng.choice([True, False, None])
        sig_on = rng.choice([True, False, None])
        holding_days = rng.choice([None, 0, 1, 5, 29, 30, 31, 100])
        max_holding_days = rng.choice([None, 30])
        got, want = check(crisis_now, E, P, d, sig_on, holding_days, max_holding_days)
        n_by_reason[want] = n_by_reason.get(want, 0) + 1
        if got != want:
            mismatches.append((crisis_now, E, P, d, sig_on, holding_days, max_holding_days, got, want))

    # ---- 2. Explicit boundary cases ------------------------------------
    boundary = []
    E, d = 100.0, 10.0
    for P in (E + d, E + d - 1e-12, E + d + 1e-12, E, E - d, 0.0):
        for crisis_now in (True, False, None):
            for sig_on in (True, False, None):
                for hd, mh in ((None, None), (30, 30), (29, 30), (31, 30), (0, None), (None, 30)):
                    boundary.append((crisis_now, E, P, d, sig_on, hd, mh))
    # None price / None stop combinations -- the None-tolerance contract.
    none_cases = []
    for price in (None, 105.0):
        for stop in (None, 110.0):
            for crisis_now in (True, False, None):
                for sig_on in (True, False, None):
                    got = evaluate_short_exit_reason(
                        crisis_now=crisis_now, price=price, stop_price=stop, sig_on=sig_on,
                        holding_days=None, max_holding_days=None)
                    # Reflect only when both are real numbers; otherwise the
                    # long call must receive the same None-ness.
                    if price is None or stop is None:
                        lp, ls_ = price, stop
                    else:
                        lp, ls_ = 2 * 100.0 - price, 2 * 100.0 - stop
                    want = evaluate_exit_reason(
                        crisis_now=crisis_now, price=lp, stop_price=ls_, sig_on=sig_on,
                        holding_days=None, max_holding_days=None)
                    none_cases.append((price, stop, crisis_now, sig_on, got, want))
                    if got != want:
                        mismatches.append(("NONE-CASE", price, stop, crisis_now, sig_on, got, want))

    for case in boundary:
        got, want = check(*case)
        n_by_reason[want] = n_by_reason.get(want, 0) + 1
        if got != want:
            mismatches.append((*case, got, want))

    total = N_RANDOM + len(boundary) + len(none_cases)
    print(f"Reflection test: {total:,} cases "
          f"({N_RANDOM:,} randomized + {len(boundary)} boundary + {len(none_cases)} None-contract)")
    print("Outcome coverage (reflected long evaluator's own verdicts):")
    for reason, n in sorted(n_by_reason.items(), key=lambda kv: -kv[1]):
        print(f"  {str(reason):12s}: {n:,}")

    # Coverage guard -- a test that never exercised STOP_LOSS on both sides of
    # the boundary would pass for a reason unrelated to what it checks.
    for required in ("CRISIS_EXIT", "STOP_LOSS", "SIGNAL_OFF", "MAX_HOLDING", None):
        if n_by_reason.get(required, 0) < 100:
            print(f"\nFATAL: outcome {required!r} exercised only "
                  f"{n_by_reason.get(required,0)} times -- insufficient coverage.")
            sys.exit(1)

    if mismatches:
        print(f"\nFATAL: {len(mismatches)} MISMATCHES. First 10:")
        for m in mismatches[:10]:
            print("  ", m)
        sys.exit(1)

    print("\nPASS -- 0 mismatches. evaluate_short_exit_reason() is an exact "
          "reflection of the production evaluate_exit_reason().")


if __name__ == "__main__":
    main()
