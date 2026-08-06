# Block 8 parity divergence — root cause test (2026-08-06)

**Question:** the deployment-cap sweep's cap=0.50 arm parity-checked against
`research/block_benchmark_comparison_2026-08-06`'s own published figures
and found 15/16 blocks reproduce exactly, but block 8 diverged (258 vs. 259
trades). Two explanations remained open: the two investigations' local
databases genuinely differ (routine bookkeeping), or something in the
shared production decision path (`simulate_portfolio_compounding_mtm()`
and everything it calls) is not deterministic across execution contexts (a
much more serious problem). The two are distinguishable by a direct test:
run the prior investigation's own script, unmodified, against this
checkout's current database, and see whether block 8 reproduces the
originally-published figures.

**Method.** `research_block_benchmarks.py` copied byte-for-byte from
`research/block_benchmark_comparison_2026-08-06/` — zero lines changed.
`config.yaml` copied alongside it as a read-only reference copy purely so
the script's own bare `open("config.yaml")` relative-path call resolves
when run from this directory (the tracked root `config.yaml` is the real
source; this is not a second live config). `PYTHONPATH` set to the repo
root at invocation time rather than touching the script's own imports.

**Result.** Block 8 reproduced **258 trades, CAGR 10.24%, ending
£56,307.45** — matching the deployment-cap sweep's own cap=0.50 arm
exactly, not the originally-published **259 trades, CAGR 9.10%, ending
£55,600.12**. All other 15 blocks were not individually re-checked here
(not needed for this specific question) but the full run completed
without error; raw output in `out/block_results_raw.json`.

**Conclusion: the two investigations' databases genuinely differ. This is
not nondeterminism in the shared production decision path.** The
identical, unmodified code produces different answers only when run
against different databases, and produces the identical answer every time
it's run against the same one — confirmed three independent ways across
this session (the deployment-cap sweep's original run, an in-process
double-run of block 8 alone, and this fully separate script invocation).
The specific upstream data point where the two database snapshots first
diverge was not isolated — not needed, once the
database-vs-nondeterminism question itself is answered, and this
checkout's own current database is what every other figure in the
deployment-cap sweep was computed from throughout.

Run before Task A's pre-warm (the planned mass re-ingestion of several
hundred additional tickers), per the reviewer's own explicit instruction —
the pre-warm will change this checkout's database again and make this
specific comparison harder to isolate cleanly afterward.

## Files

- `research_block_benchmarks.py` — unmodified copy of the prior
  investigation's own script.
- `config.yaml` — read-only reference copy (see "Method" above).
- `run.log` — full run output.
- `out/block_results_raw.json`, `out/block_strategy_trades.csv` — this
  run's own raw output, all 16 blocks.
