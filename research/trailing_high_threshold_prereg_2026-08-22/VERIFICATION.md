# Verification V1–V7 (2026-08-22)

**All items pass. One failure was reported, investigated, and traced to the verification harness
rather than the engine — the detail is in §1, because the harness bug is the more instructive part.**

No cell result is trusted until every item here passes (§6 of the pre-registration).

---

## Summary

| # | check | Pool 2 CLEAN | Pool 1 |
|---|---|---|---|
| **V1** | degenerate parity vs preserved `s3.0` | **PASS** — 0 mismatches on all 17 columns | **PASS** |
| **V2** | zero `TRAILING_STOP` when activation off | **PASS** (0) | **PASS** (0) |
| **V3** | four trailing unit tests, **non-vacuous** | **4/4 PASS** | — |
| **V4** | anti-lookahead demonstration, test (d) | **PASS** | — |
| **V5** | activation never retroactive | **PASS** (0 retro) | **PASS** (0 retro) |
| **V6** | ratchet monotonicity + §4.4 `max(entry_stop, trail)` | **PASS** | **PASS** |
| **V7** | unit tests re-derived for current-date ATR | **4/4 PASS + parameter proven live** | — |

---

## 1. V1 — reported FAIL, was a harness bug, and the gate behaved correctly

**What was reported.** Trade count matched exactly (565/565, 522/522) and total P&L matched to the
penny (£348,548.74, £429,108.58) — but **90 rows on Pool 2 and 85 on Pool 1 compared unequal**.

**Per §7, diagnosed row by row rather than waved through on the matching total.** Result:

```
identity sets equal (ticker, entry_date, exit_date) : True
  only in got: 0     only in exp: 0
ticker      differ: 0
entry_date  differ: 0
exit_date   differ: 0
exit_reason differ: 0
```

and on a like-for-like numeric comparison of **every** column — `entry_price`, `exit_price`,
`pnl_gbp`, `pnl_pct`, `position_gbp`, `holding_days`, `initial_stop`, `final_stop`,
`stop_distance_pct`, `equity_at_entry` — **max absolute difference 0.000e+00, on all ten.** Zero
duplicate keys on either side, so sort order cannot be ambiguous.

**Root cause, confirmed by direct measurement rather than inferred.** The original check compared
**in-memory float64** against the reference **CSV** with exact `!=`. A synthetic test of 20,000
float64 values through pandas' default `to_csv`/`read_csv`:

| | |
|---|---|
| bit-exact round trips | 16,770 |
| **changed by the round trip** | **3,230 (16.2%)** |
| max absolute change | 1.819e-12 |
| max relative change | 2.852e-16 |

**16.2% predicted against 15.9% observed (90/565).** The rate matches almost exactly, which is what
turns "plausible explanation" into "confirmed mechanism". The reference file had been through the
round trip; the fresh engine output had not, so the two carried different low-order bits for the same
computation.

**Also confirmed by reading the code, independently of the numbers**: `trail_active` is set at exactly
one place (`trail_engine.py:296`), guarded by `trail_activation is not None`. In the degenerate case
the trail block at line 298 — the *only* line the §4.3 ruling changed — is **unreachable**. A
behavioural difference was not merely unlikely, it was structurally impossible.

**Fixed** by routing the engine output through the identical round trip before comparing, and by
switching to a stable `mergesort` so ties can never reorder between frames.

**Recorded rather than quietly corrected, because the failure was useful.** The gate refused to pass
a result it could not confirm, which is exactly its job — and the same too-strict comparison would
have been indistinguishable from a real divergence had one existed. The general lesson is narrow and
portable: **a float that has been through a text format and one that has not are not the same number,
and comparing them with `==` tests the serializer, not the computation.** This project has hit the
neighbouring version of this once before — the relative-rank promotion's own first comparison pass
reported hundreds of spurious mismatches on identical trade sets, traced then to row *ordering*.
Same shape, different cause.

---

## 2. V5 — passes, with its coverage limit stated

`V5` checks that no position is flagged `trail_activated` unless its own path actually reached the
activation threshold, validated against the Stage 0 peak table.

**0 retro-activations** in both pools. But coverage is partial and is reported as such: **36 of 187**
activated positions on Pool 2, **33 of 202** on Pool 1 could be checked. The remainder are positions
the *armed* arm holds that baseline never held at all — capacity cascade — so no baseline peak exists
to check them against. This is a real limit of the instrument, not a silent pass: it confirms the
property on every position where confirmation is possible.

---

## 3. V7 — the ruling's carry-over was verified, and the parameter proven live

The §4.3 ruling changed the ATR source, and the four reused unit tests were written against the
frozen-entry convention. Their fixtures pin `atr` at a constant 1.0, so they were **expected** to
carry over unchanged.

**Expectation verified, not assumed**: all four produce identical `(exit_reason, final_stop)` under
`trail_atr_source="current"` and `"entry"` on the constant-`atr` fixtures.

**And the converse was tested too — V7b.** A fixture with a *rising* ATR must make the two
conventions disagree, or the new parameter would be silently inert and V7's four passes would be
meaningless:

```
V7b rising-ATR fixture: conventions DIFFER -> True (current=140.000, entry=161.000)
```

Without V7b, four tests agreeing under both settings is equally consistent with "the fixtures cannot
distinguish them" and with "the parameter does nothing" — a check that would have reported clean for
a reason unrelated to what it was checking. V7b separates the two.

---

## 4. V3 non-vacuity

Every unit test asserts it produced at least one trade (`_require`), carried over from the exit
sweep's own vacuous-pass incident. Test (a) reports `n=2 trades, non-vacuous`; (b), (c) and (d) each
index `[0]` on a confirmed non-empty result. **A test that has not been observed producing a
non-empty result has not been observed at all.**

Test (d), the anti-lookahead demonstration, resolves to `MAX_HOLDING` rather than `TRAILING_STOP` —
correct: a day-3 low of 114 sits above yesterday's trail of 113, so no trail exit fires, where the
wrong ordering would have raised the trail to 119 first and exited.

---

## 5. Files

`step4_verify.py`, `step5b_diagnose_v1.py`, `trail_engine.py`, `_v1diag.log`.

`trail_engine.py` is a copy of the sweep's `sweep_engine.py` with exactly one behavioural change —
the `trail_atr_source` parameter implementing the §4.3 ruling, defaulting to `"current"`, with
`"entry"` retained solely so the exit sweep's own cells stay reproducible. §4.4 holds by construction
(`stop_price` is seeded to `initial_stop` and only ever raised via `max()`) and is asserted in V6
rather than assumed.
