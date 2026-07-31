"""V2 architecture (Stage 1 — inert, not wired to live orders).

See analysis/v2/universe.py, signals.py, crisis.py, and backtest.py.
Deliberately isolated from analysis/regime.py, analysis/macro.py, and
every other file V1 (the six-module confluence architecture) depends
on — see CLAUDE.md for the phased validation history behind this
module's design (simple 12-month momentum, point-in-time-gated
universe, rare circuit-breaker override; no blended score, no
per-ticker regime classifier).
"""
