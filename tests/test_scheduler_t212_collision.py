"""A general safeguard against a scheduling collision between two real
T212 API-calling jobs, added 2026-08-03 as a direct follow-up to the
same-day production incident (see CLAUDE.md's "Fix rate-limit race
behind today's order-placement alert" section): market_hours_check and
account_state_refresh were both registered at the identical cron
minute=0, and on their first joint firing the combined T212 API burst
exhausted the rate limit, silently skipping that hour's exit check.

That incident was fixed correctly at the specific-instance level
(offsetting account_state_refresh to minute=5). This module closes the
GENERAL class of bug: nothing previously stopped a THIRD job, added
later, from landing on the same cron minute as any existing T212-
calling job. This is the same failure shape as the 30-day re-entry
cooldown gap (see analysis/decision.py's own docstring and CLAUDE.md's
"Structural enforcement for live/backtest parity" section) — a real
instance fixed correctly, but the class of failure left free to recur
in a different form until a general, self-updating safeguard was built.

WHAT THIS CATCHES, PRECISELY: any two scheduler jobs, each of which
(a) transitively calls into brokers.trading212 (the single module every
real T212 API interaction in this codebase routes through, via a real
`from brokers.trading212 import X` / `import brokers.trading212`
statement — an AST-parsed import check, deliberately NOT a raw
substring search over the source text, since several functions in this
heavily-documented codebase mention "brokers/trading212.py" purely as a
prose docstring cross-reference with no actual T212 call in their own
code — see _imports_trading212()'s own docstring for the real false
positive this caused during development, caught by the ground-truth
test below before it ever reached the collision scan itself) and (b)
uses a CronTrigger, that would ever fire at the identical wall-clock
instant. "Transitively" matters — a job function doesn't need to import
brokers.trading212 directly itself; it's classified correctly even when
it only calls a LOCAL data.scheduler helper (e.g.
_fetch_t212_snapshot()) that does, exactly the real shape _eod_job()
and _dispatch_pending_jobs() have. This is self-updating by design: a
newly added job's T212 usage and fire-time schedule are both discovered
directly from its own real code and registration, with no
hand-maintained "which jobs call T212" list for someone to forget to
update.

WHAT THIS DOES NOT CATCH, stated plainly rather than overclaimed:

  - job_dispatcher's IntervalTrigger(seconds=60) is deliberately EXCLUDED
    from the collision scan (see find_trigger_collisions()'s own
    CronTrigger-only filtering at its real call site below). It IS
    T212-calling (conditionally, only when a UI
    "refresh account" click has set the pending_t212_fetch flag) — but
    an interval-based trigger firing every 60 seconds will, by
    construction, land on the exact same wall-clock minute as literally
    every CronTrigger job at some point within any multi-day window,
    which would make this scanner flag a "collision" against every
    other T212-calling job unconditionally and uselessly. That's a
    structurally different risk category — a rare, UI-triggered,
    mostly-no-op job overlapping in practice with a scheduled job's own
    firing, not two fixed schedules that were declared to collide. See
    CLAUDE.md's "structural safeguards against future T212 API
    scheduling collisions" section, Part B, for why this residual risk
    is intentionally left to the runtime rate-limiter/backoff mechanism
    instead of a static schedule check.
  - A collision between a T212-calling job and a job that ISN'T
    classified as T212-calling because it calls into brokers.trading212
    through something other than a plain function call this scanner's
    AST walk recognises (e.g. a dynamically constructed call, a
    dict-based dispatch) — the same "does not catch" boundary Layer 1
    (tests/test_decision_logic_placement.py) and Layer 3
    (tests/test_one_sided_decision_rules.py) already state for their
    own AST-based scans. Every current real job in this codebase makes
    its T212 calls via plain `from brokers.trading212 import X` /
    `X(...)` calls, so this is not a live gap today — recorded here as
    an honest limitation of the mechanism, not a claim it's exhaustive.
  - Real runtime overlap between two jobs that DON'T share a declared
    cron time but happen to run concurrently anyway (one job running
    long, a retry backing into another job's start) — this is a static
    schedule check only. See Part B / CLAUDE.md for the runtime-level
    discussion of this separate risk.
"""
import ast
import inspect
import textwrap
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.cron import CronTrigger

import data.scheduler as scheduler_module


# ---------------------------------------------------------------------------
# Pure classification logic — "does this function transitively call into
# brokers.trading212" — operates on a plain name -> source-text mapping so
# it can be exercised directly against a synthetic mapping in the tests
# below, exactly as faithfully as it runs against the real, live
# data.scheduler module. Mirrors test_one_sided_decision_rules.py's own
# separation of "parse real files/objects" from "pure decision logic".
# ---------------------------------------------------------------------------

def _calls_within_source(source: str, candidate_names: set[str]) -> set[str]:
    """Every name in candidate_names that appears as a plain function
    call (ast.Call with a bare ast.Name func) inside `source`."""
    tree = ast.parse(textwrap.dedent(source))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in candidate_names:
                found.add(node.func.id)
    return found


def _imports_trading212(source: str) -> bool:
    """True only if `source` contains a REAL import statement
    referencing brokers.trading212 (e.g. `from brokers.trading212
    import X`, the lazy-import-inside-the-function pattern every real
    T212 call in this file uses — confirmed directly: data/scheduler.py
    has no module-level `from brokers.trading212 import ...` at all,
    only local, per-function imports).

    Deliberately AST-based (only real ast.ImportFrom/ast.Import nodes),
    NOT a raw substring search over the full source text — found the
    hard way while building this: several functions in this
    heavily-documented codebase mention "brokers/trading212.py" purely
    as a prose docstring cross-reference to RELATED code elsewhere
    (e.g. _weekly_summary_job's own docstring: "...deliberately
    separate from brokers/trading212.py's error-alert cooldown"), with
    no actual T212 API call anywhere in the function's real code. A
    naive `"trading212" in source` check produced exactly this false
    positive (classifying _weekly_summary_job as T212-calling, which
    then reported a fabricated "collision" against market_hours_check
    purely because both share Sunday 09:00 in some week) before this
    AST-based fix replaced it — see CLAUDE.md's "structural safeguards
    against future T212 API scheduling collisions" section, Part A."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "trading212" in node.module:
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "trading212" in alias.name:
                    return True
    return False


def function_calls_t212(name: str, sources: dict[str, str], _visited: set[str] | None = None) -> bool:
    """True if sources[name] itself contains a real import statement
    referencing brokers.trading212, or it transitively calls another
    function present in `sources` that does (recursing through any
    depth of local helper calls). Pure — no filesystem or live-object
    introspection here at all, so the algorithm itself is directly
    testable against a synthetic mapping. Bounded by _visited against
    infinite recursion on a mutual-call cycle (none exist in the real
    module today, but the guard costs nothing and makes no assumption
    that one never will)."""
    if _visited is None:
        _visited = set()
    if name in _visited or name not in sources:
        return False
    _visited.add(name)

    source = sources[name]
    if _imports_trading212(source):
        return True

    called = _calls_within_source(source, set(sources))
    return any(function_calls_t212(c, sources, _visited) for c in called)


def _real_scheduler_function_sources() -> dict[str, str]:
    """name -> source text for every local (module-level, name starting
    with a single "_" — the local-helper naming convention this file
    uses throughout) function defined DIRECTLY in data.scheduler (not
    imported into it). Derived dynamically so a newly added job/helper
    is automatically covered with no maintenance needed here — the same
    "don't hand-maintain a list that will go stale" preference
    test_one_sided_decision_rules.py already established for
    analysis/decision.py's own function set.

    Dunder names (e.g. "__annotate__") are explicitly excluded, not just
    "name.startswith('_')" — found the hard way while building this:
    Python's PEP 649 lazy-annotation machinery synthesises a
    module-level "__annotate__" function whenever a module has
    type-annotated globals (this module does), and inspect.getsource()
    on that synthetic function returns unpredictable, unrelated text —
    it isn't a real local helper this scanner has any business
    inspecting, and including it produced two genuine false-positive
    test failures before this exclusion was added."""
    return {
        name: inspect.getsource(obj)
        for name, obj in inspect.getmembers(scheduler_module, inspect.isfunction)
        if obj.__module__ == scheduler_module.__name__
        and name.startswith("_")
        and not name.startswith("__")
    }


# ---------------------------------------------------------------------------
# Pure collision-detection logic — walks a trigger's own get_next_fire_time()
# repeatedly rather than hand-parsing cron field semantics, so it handles
# any trigger shape generically (a fixed hour:minute, an hour="*" wildcard
# firing every hour, a day_of_week restriction, a differing timezone) with
# no special-casing that could itself miss a real collision. This is the
# exact mechanism this project's own existing tests already use to prove a
# single trigger's real fire behaviour (e.g.
# test_market_hours_check_trigger_actually_fires_on_a_real_weekend_date in
# tests/test_scheduler.py), just walked repeatedly instead of checked once.
# ---------------------------------------------------------------------------

def _fire_times_within(trigger, start: datetime, end: datetime, cap: int = 3000) -> list[datetime]:
    """Every fire time `trigger` produces in [start, end)."""
    times: list[datetime] = []
    t = start
    while t < end and len(times) < cap:
        nxt = trigger.get_next_fire_time(None, t)
        if nxt is None or nxt >= end:
            break
        times.append(nxt)
        t = nxt + timedelta(seconds=1)
    return times


def find_trigger_collisions(
    jobs: list[tuple[str, object]], window_days: int = 8,
) -> list[tuple[str, str, datetime]]:
    """Pairwise-compares every job's real fire times over `window_days`
    days starting from a fixed, real anchor (an ordinary Monday — long
    enough to cover every weekday, both weekend days, and multiple
    passes of any hourly job, so an hour="*" job firing 24x/day is
    compared across enough distinct calendar days that a genuine
    collision can't be missed by bad luck of which single day got
    sampled). Returns every (job_id_a, job_id_b, collision_time) triple
    where both jobs would genuinely fire at the identical UTC instant.
    Pure — takes (job_id, trigger) pairs directly, no scheduler/DB
    dependency, so it's exercisable against synthetic triggers exactly
    as faithfully as against the real registered jobs."""
    anchor = datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)  # a real, ordinary Monday
    window_end = anchor + timedelta(days=window_days)

    fire_sets = {job_id: set(_fire_times_within(trigger, anchor, window_end)) for job_id, trigger in jobs}

    collisions: list[tuple[str, str, datetime]] = []
    ids = list(fire_sets)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            for t in sorted(fire_sets[ids[i]] & fire_sets[ids[j]]):
                collisions.append((ids[i], ids[j], t))
    return collisions


# ---------------------------------------------------------------------------
# Ground-truth validation — confirm the classifier against real, manually-
# verified cases BEFORE trusting it for the actual production collision
# scan below. Mirrors test_one_sided_decision_rules.py's own
# test_checks_both_directions_using_real_project_evidence().
# ---------------------------------------------------------------------------

def test_t212_calling_jobs_identified_correctly_against_known_ground_truth():
    """Every one of these classifications was independently verified by
    reading the real function source directly (see CLAUDE.md's
    "structural safeguards against future T212 API scheduling
    collisions" section, Part A):
      - _eod_job: directly imports purge_old_pending_orders from
        brokers.trading212, and calls _run_analysis_pipeline() (which
        directly imports maybe_place_orders) and _fetch_t212_snapshot()
        (which directly constructs a Trading212Client).
      - _dispatch_pending_jobs: never mentions "trading212" in its own
        body at all — only calls the local _fetch_t212_snapshot()
        helper, which does. The exact shape this classifier's
        transitive lookup exists to catch.
      - _market_hours_check_job / _account_state_job /
        _stock_order_submission_job: all directly import a real
        brokers.trading212 function.
      - _membership_refresh_job: refreshes S&P 500 membership windows
        from a public GitHub CSV source — zero T212 involvement.
      - _weekly_summary_job: sends the weekly email by reading
        system_metadata's already-cached t212_account_snapshot and a
        pure local FX-rate helper (Trading212Client._fx_rates(), a
        price_data DB read with no HTTP call) — never constructs a
        live client or calls the T212 REST API itself.
    """
    sources = _real_scheduler_function_sources()

    known_t212_calling = {
        "_eod_job", "_dispatch_pending_jobs", "_market_hours_check_job",
        "_account_state_job", "_stock_order_submission_job",
    }
    known_not_t212_calling = {"_membership_refresh_job", "_weekly_summary_job"}

    for name in known_t212_calling:
        assert name in sources, f"{name} not found in data.scheduler — ground truth out of date?"
        assert function_calls_t212(name, sources), f"{name} should be classified as T212-calling"

    for name in known_not_t212_calling:
        assert name in sources, f"{name} not found in data.scheduler — ground truth out of date?"
        assert not function_calls_t212(name, sources), f"{name} should NOT be classified as T212-calling"


def test_dispatch_pending_jobs_classified_via_transitive_lookup_not_direct_mention():
    """Specifically confirms _dispatch_pending_jobs is caught through the
    RECURSIVE path, not a coincidental direct mention — its own source
    text must not literally contain "trading212" (only a "T212" log
    message, which is a different substring), so if this test starts
    failing because the direct-substring check alone happens to catch
    it, that's a real, silent change to this function worth noticing."""
    sources = _real_scheduler_function_sources()
    own_source = sources["_dispatch_pending_jobs"]
    assert "trading212" not in own_source, (
        "_dispatch_pending_jobs now mentions 'trading212' directly — the "
        "transitive-lookup test below no longer proves what it claims to; "
        "update this test's own reasoning if this function's shape changed."
    )
    assert function_calls_t212("_dispatch_pending_jobs", sources)


# ---------------------------------------------------------------------------
# The actual, production-facing check.
# ---------------------------------------------------------------------------

def test_no_two_t212_calling_cron_jobs_share_a_fire_time(monkeypatch):
    """The real safeguard: every currently-registered scheduler job that
    (a) transitively calls brokers.trading212 and (b) uses a CronTrigger
    must never share an exact wall-clock fire time with another such
    job. Exactly today's real incident shape (market_hours_check and
    account_state_refresh both at cron minute=0) — this test would have
    failed, naming both jobs by name, the moment that registration was
    written, rather than only being discovered by a live rate-limit
    alert firing in production."""
    fake_config = {
        "scheduler": {
            "timezone": "UTC",
            "runs": {
                "eod": {"hour": 21, "minute": 30, "label": "End of Day", "type": "full"},
            },
        },
    }
    monkeypatch.setattr(scheduler_module, "_load_config", lambda: fake_config)
    monkeypatch.setattr(scheduler_module, "get_metadata_str", lambda key: None)
    monkeypatch.setattr(scheduler_module, "set_metadata", lambda *a, **k: None)

    sched = scheduler_module.start_scheduler()
    try:
        sources = _real_scheduler_function_sources()
        cron_t212_jobs = [
            (job.id, job.trigger)
            for job in sched.get_jobs()
            if isinstance(job.trigger, CronTrigger)
            and function_calls_t212(job.func.__name__, sources)
        ]
        # Sanity check the scan actually found real jobs to compare —
        # an empty list here would make the assertion below trivially,
        # uselessly pass regardless of whether the classifier or the
        # scheduler registration is broken.
        assert {job_id for job_id, _t in cron_t212_jobs} >= {
            "scheduled_eod", "market_hours_check", "account_state_refresh", "stock_order_submission",
        }, f"Expected the known real T212-calling CronTrigger jobs to be found; got {cron_t212_jobs}"

        collisions = find_trigger_collisions(cron_t212_jobs)
        assert not collisions, (
            f"T212-calling scheduler job(s) share an identical cron fire "
            f"time — exactly the shape of the 2026-08-03 rate-limit "
            f"incident (see CLAUDE.md's 'Fix rate-limit race behind "
            f"today's order-placement alert' section): {collisions}. "
            f"Offset one job's hour/minute so the two never fire "
            f"concurrently."
        )
    finally:
        scheduler_module.stop_scheduler()


# ---------------------------------------------------------------------------
# Proof the mechanism itself works — the same verification standard every
# prior architectural safeguard in this project was held to (Layer 1's
# test_scanner_actually_fails_on_a_deliberately_injected_violation, Layer
# 3's test_scanner_correctly_flags_an_injected_one_sided_rule_with_no_
# exemption).
# ---------------------------------------------------------------------------

def test_find_trigger_collisions_detects_an_injected_identical_time_collision():
    """Two triggers deliberately registered at the exact same cron
    hour/minute — the literal shape of today's real incident."""
    t1 = CronTrigger(hour=9, minute=0, timezone="UTC")
    t2 = CronTrigger(hour=9, minute=0, timezone="UTC")
    collisions = find_trigger_collisions([("job_a", t1), ("job_b", t2)])
    assert collisions, "Scanner failed to detect two triggers sharing an identical cron time"
    assert {(a, b) for a, b, _t in collisions} == {("job_a", "job_b")}


def test_find_trigger_collisions_detects_an_hourly_job_colliding_with_a_fixed_time_job():
    """The shape most likely for a FUTURE collision in this specific
    scheduler: a new once-daily fixed-time job landing on an existing
    hourly (hour="*") job's own minute — e.g. a hypothetical new job at
    09:00 would collide with market_hours_check's real minute=0 hourly
    firing. Proves the scanner catches this cross-shape case, not just
    two identical once-daily triggers."""
    hourly = CronTrigger(minute=0, timezone="UTC")          # fires every hour, minute 0
    fixed = CronTrigger(hour=9, minute=0, timezone="UTC")   # fires once/day, 09:00 -- collides
    collisions = find_trigger_collisions([("hourly_job", hourly), ("fixed_job", fixed)])
    assert collisions, "Scanner failed to detect an hourly job colliding with a same-minute fixed-time job"


def test_find_trigger_collisions_correctly_passes_genuinely_non_colliding_triggers():
    """A pair of genuinely different fire times must never be flagged —
    confirms the scanner isn't just flagging every pair indiscriminately,
    the same control-case discipline Layer 1's own injected-violation
    test uses (a clean comparison alongside the injected one)."""
    t1 = CronTrigger(hour=9, minute=0, timezone="UTC")
    t2 = CronTrigger(hour=14, minute=30, timezone="UTC")
    assert find_trigger_collisions([("job_a", t1), ("job_b", t2)]) == []

    # The two REAL, currently-registered hourly jobs (minute=0 vs
    # minute=5) — confirms the actual production fix (offsetting
    # account_state_refresh) is genuinely collision-free under this
    # exact mechanism, not just "no error was thrown".
    real_a = CronTrigger(minute=0, timezone="UTC")
    real_b = CronTrigger(minute=5, timezone="UTC")
    assert find_trigger_collisions([("market_hours_check", real_a), ("account_state_refresh", real_b)]) == []


def test_function_calls_t212_detects_a_direct_reference():
    sources = {
        "_my_job": "def _my_job():\n"
                   "    from brokers.trading212 import poll_placed_orders\n"
                   "    poll_placed_orders()\n",
    }
    assert function_calls_t212("_my_job", sources)


def test_function_calls_t212_detects_a_transitive_reference_through_a_local_helper():
    """The exact real shape _dispatch_pending_jobs()/_eod_job() have: the
    job function itself never mentions "trading212" directly, only
    calling a local helper that does — proves the recursive lookup
    genuinely works, not just the trivial direct-substring case."""
    sources = {
        "_my_job": "def _my_job():\n    _my_helper()\n",
        "_my_helper": "def _my_helper():\n"
                      "    from brokers.trading212 import Trading212Client\n"
                      "    Trading212Client()\n",
    }
    assert function_calls_t212("_my_job", sources)


def test_function_calls_t212_correctly_returns_false_for_an_unrelated_function():
    """Confirms the classifier isn't just returning True unconditionally —
    a function with no T212 involvement anywhere in its own or its
    callees' source must be classified False."""
    sources = {
        "_my_job": "def _my_job():\n    _unrelated_helper()\n",
        "_unrelated_helper": "def _unrelated_helper():\n    return 1 + 1\n",
    }
    assert not function_calls_t212("_my_job", sources)


def test_scenario_a_future_third_job_landing_on_an_existing_minute_is_caught_end_to_end():
    """The full, deliberately-injected end-to-end proof asked for: a
    synthetic THIRD T212-calling job, registered at the exact minute an
    existing real T212-calling job already fires at, is caught by
    running the real classifier and the real collision finder together
    — exactly the class of bug this whole module exists to prevent
    (nothing currently stops a future job from silently landing on an
    existing job's minute), reproduced and confirmed caught without
    needing to actually modify data/scheduler.py's own registration."""
    sources = dict(_real_scheduler_function_sources())
    # A synthetic job whose own source directly references
    # brokers.trading212, exactly like market_hours_check_job's real
    # shape, injected as though it were a genuine new scheduler.py
    # function about to be registered.
    sources["_future_third_job"] = (
        "def _future_third_job():\n"
        "    from brokers.trading212 import poll_placed_orders\n"
        "    poll_placed_orders()\n"
    )
    assert function_calls_t212("_future_third_job", sources)

    real_market_hours_check = CronTrigger(minute=0, timezone="UTC")  # the real, current trigger
    injected_future_job = CronTrigger(minute=0, timezone="UTC")      # lands on the SAME minute
    collisions = find_trigger_collisions([
        ("market_hours_check", real_market_hours_check),
        ("_future_third_job", injected_future_job),
    ])
    assert collisions, (
        "A future job registered at market_hours_check's own minute "
        "should have been caught as a collision, but was not — the "
        "scanner failed to protect against the exact class of bug this "
        "module exists to prevent."
    )
