"""Layer 3 of the structural live/backtest parity enforcement (2026-08-03
— see CLAUDE.md's "Structural enforcement for live/backtest parity"
section for Layers 1-2, and "One-sided decision rule detection" for why
this one exists).

Layers 1-2 both catch DISAGREEMENT between two things that exist on both
sides. The 30-day re-entry cooldown gap was a different failure shape
entirely: live had a rule (_load_open_order_tickers()'s bulk SQL query),
backtest had NOTHING — no comparison to duplicate, no divergent behaviour
a replay scenario could trigger by surprise, because there was no second
implementation to disagree with in the first place. A check tuned to spot
disagreement between two existing things has no structural reason to
notice an omission on one side.

WHAT THIS CATCHES, PRECISELY: for every function analysis/decision.py
exports, whether it is called anywhere in brokers/trading212.py (live)
and anywhere in analysis/backtest.py (backtest) — checked in BOTH
directions, not just "live has something backtest lacks". A function
called by only one side FAILS unless it is in the explicit, reasoned
exemption list below (mirroring Layer 1's own _WHITELISTED_SOURCE_SNIPPETS
pattern — an exemption is a recorded decision, not a way to silence the
test).

WHAT THIS DOES NOT CATCH, stated plainly rather than overclaimed: a rule
that was never extracted into analysis/decision.py at all, and exists
purely as ad-hoc inline logic on one side with no shared function to even
check for — that is exactly what Layer 1 exists to catch instead (a raw
comparison outside decision.py). The two checks are complementary:
Layer 1 catches an unshared rule sitting inline; this layer catches a
shared function that only one side actually calls. Neither alone would
have caught the 30-day gap by itself in real time — Layer 1 wasn't built
yet, and this layer only works once the rule has a name in decision.py at
all — but from today onward, any NEW shared function added to
decision.py must have real call sites on both sides (or a stated
exemption) or this test fails, closing the specific gap that let the
30-day cooldown ship without a backtest counterpart for as long as it
did.
"""
import ast
import inspect
from pathlib import Path

import analysis.decision as decision_module

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIVE_FILE = _REPO_ROOT / "brokers" / "trading212.py"
_BACKTEST_FILE = _REPO_ROOT / "analysis" / "backtest.py"


def _decision_function_names() -> set[str]:
    """Every function analysis/decision.py actually defines (not
    imported into it) — derived dynamically so a newly-added shared
    function is automatically covered with no maintenance needed here,
    the same "don't hand-maintain a list that will go stale" preference
    this project applies elsewhere."""
    return {
        name for name, obj in inspect.getmembers(decision_module, inspect.isfunction)
        if obj.__module__ == decision_module.__name__
    }


def _called_function_names(path: Path, candidates: set[str]) -> set[str]:
    """Every name in *candidates* that appears as a plain function call
    (ast.Call with a bare ast.Name func, e.g. `passes_entry_gate(...)`,
    not `module.passes_entry_gate(...)`) anywhere in *path*. Both scanned
    files import every analysis.decision function by bare name
    (`from analysis.decision import X`), so this is the correct call
    shape to look for — matches Layer 1's own scanning convention of
    reading the real file text rather than importing and introspecting
    live objects (a comment mentioning a function name is not a call and
    is correctly never matched, since ast.Call requires actual call
    syntax)."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in candidates:
                found.add(node.func.id)
    return found


# Functions genuinely called by only ONE side, each with a specific,
# verified reason — not assumed, checked directly against real call
# sites before being added here. Adding an entry is a real, reviewed
# decision (matching Layer 1's own whitelist discipline), not a way to
# silence this test.
EXEMPT_LIVE_ONLY: dict[str, str] = {
    "is_asset_class_enabled": (
        "Genuinely called by both sides, but not inside analysis/backtest.py "
        "itself — analysis/validation.py:run_full_validation() calls it "
        "UPSTREAM, filtering the candidate ticker universe before any engine's "
        "tickers_cache is even built (verified directly: analysis/validation.py "
        "lines ~372-392), so a disabled asset class is never a candidate for "
        "any backtest engine, the same effect as a per-day check inside the "
        "engine would have. See CLAUDE.md 'Clean, independent ETF/stock "
        "trading switches' section and is_asset_class_enabled()'s own "
        "docstring."
    ),
}

EXEMPT_BACKTEST_ONLY: dict[str, str] = {
    "is_reentry_blocked": (
        "The threshold IS shared (resolve_reentry_cooldown_days() is called "
        "by both sides) — only the PREDICATE call is one-sided, because live "
        "enforces the cooldown via a single bulk SQL query "
        "(_load_open_order_tickers()) rather than a per-candidate call to "
        "this pure function, while the backtest engine's day-by-day walk has "
        "no equivalent bulk-query mechanism to reuse and calls the predicate "
        "directly instead. Documented, deliberate, reviewed duplication of a "
        "simple rule — see REENTRY_COOLDOWN_DAYS's own docstring in "
        "analysis/decision.py and CLAUDE.md's '30-day re-entry cooldown' "
        "section for the identical precedent this mirrors "
        "(candidate-priority-ranking's SQL-ORDER-BY-vs-Python-sort split)."
    ),
}


def _find_one_sided_violations(
    live_calls: set[str],
    backtest_calls: set[str],
    exempt_live_only: dict[str, str],
    exempt_backtest_only: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Pure comparison logic, extracted so it can be tested directly
    against synthetic call-sets without needing real files — mirrors
    Layer 1's own separation of "parse a file" from "decide what's a
    violation". Returns (unexplained_live_only, unexplained_backtest_only)."""
    only_live = live_calls - backtest_calls
    only_backtest = backtest_calls - live_calls
    unexplained_live_only = only_live - exempt_live_only.keys()
    unexplained_backtest_only = only_backtest - exempt_backtest_only.keys()
    return unexplained_live_only, unexplained_backtest_only


def test_no_unexplained_one_sided_decision_rules():
    """The actual Layer 3 check, run against the real project files in
    both directions. Fails loudly, naming exactly which function and
    which side, if a decision.py function is called by only one of live
    order placement / the backtest engine with no recorded exemption."""
    candidates = _decision_function_names()
    live_calls = _called_function_names(_LIVE_FILE, candidates)
    backtest_calls = _called_function_names(_BACKTEST_FILE, candidates)

    unexplained_live_only, unexplained_backtest_only = _find_one_sided_violations(
        live_calls, backtest_calls, EXEMPT_LIVE_ONLY, EXEMPT_BACKTEST_ONLY,
    )

    assert not unexplained_live_only, (
        f"analysis/decision.py function(s) called by live order placement "
        f"(brokers/trading212.py) but NEVER by the backtest engine "
        f"(analysis/backtest.py), with no recorded exemption: "
        f"{unexplained_live_only}. Either wire the backtest engine to call "
        f"the same function (or an equally shared implementation), or if "
        f"there's a genuine, reviewed reason it can't/needn't, add it to "
        f"EXEMPT_LIVE_ONLY above with that reason."
    )
    assert not unexplained_backtest_only, (
        f"analysis/decision.py function(s) called by the backtest engine "
        f"(analysis/backtest.py) but NEVER by live order placement "
        f"(brokers/trading212.py), with no recorded exemption: "
        f"{unexplained_backtest_only}. This is exactly the shape the 30-day "
        f"re-entry cooldown gap had. Either wire live to call the same "
        f"function (or an equally shared implementation), or if there's a "
        f"genuine, reviewed reason it can't/needn't, add it to "
        f"EXEMPT_BACKTEST_ONLY above with that reason."
    )


def test_exemptions_correspond_to_real_currently_one_sided_calls():
    """Both real exemptions must actually be needed right now — if a
    future change makes is_asset_class_enabled or is_reentry_blocked
    genuinely called by both sides, the exemption becomes stale and
    should be removed (an unneeded exemption silently hides the fact
    that a rule DID get properly unified, rather than confirming it's
    still a deliberate split)."""
    candidates = _decision_function_names()
    live_calls = _called_function_names(_LIVE_FILE, candidates)
    backtest_calls = _called_function_names(_BACKTEST_FILE, candidates)

    for name in EXEMPT_LIVE_ONLY:
        assert name in live_calls and name not in backtest_calls, (
            f"EXEMPT_LIVE_ONLY['{name}'] is stale — this function is no "
            f"longer live-only (or no longer called by live at all); "
            f"re-check whether the exemption is still needed."
        )
    for name in EXEMPT_BACKTEST_ONLY:
        assert name in backtest_calls and name not in live_calls, (
            f"EXEMPT_BACKTEST_ONLY['{name}'] is stale — this function is no "
            f"longer backtest-only (or no longer called by backtest at all); "
            f"re-check whether the exemption is still needed."
        )


def test_checks_both_directions_using_real_project_evidence():
    """Direct proof this check is genuinely bidirectional, using the
    project's own real, already-verified cases rather than only a
    synthetic one: is_asset_class_enabled is a real LIVE-only call (of
    these two files) and is_reentry_blocked is a real BACKTEST-only
    call — one exemption of each shape exists in production code today,
    confirming both directions are actually exercised, not just
    theoretically supported."""
    candidates = _decision_function_names()
    live_calls = _called_function_names(_LIVE_FILE, candidates)
    backtest_calls = _called_function_names(_BACKTEST_FILE, candidates)

    assert "is_asset_class_enabled" in live_calls
    assert "is_asset_class_enabled" not in backtest_calls
    assert "is_reentry_blocked" in backtest_calls
    assert "is_reentry_blocked" not in live_calls


def test_scanner_correctly_flags_an_injected_one_sided_rule_with_no_exemption(tmp_path):
    """Proves the mechanism itself works, the same verification standard
    every prior architectural safeguard in this project was held to.
    Synthetic live file calls a fake shared function; synthetic backtest
    file doesn't call it at all, and no exemption is recorded — must be
    flagged."""
    live_file = tmp_path / "fake_live.py"
    live_file.write_text(
        "from analysis.decision import fake_shared_rule\n"
        "def maybe_place_orders(config):\n"
        "    if not fake_shared_rule(config):\n"
        "        return\n",
        encoding="utf-8",
    )
    backtest_file = tmp_path / "fake_backtest.py"
    backtest_file.write_text(
        "def simulate_portfolio_compounding_mtm(config):\n"
        "    pass  # never calls fake_shared_rule at all\n",
        encoding="utf-8",
    )

    candidates = {"fake_shared_rule"}
    live_calls = _called_function_names(live_file, candidates)
    backtest_calls = _called_function_names(backtest_file, candidates)

    unexplained_live_only, unexplained_backtest_only = _find_one_sided_violations(
        live_calls, backtest_calls, exempt_live_only={}, exempt_backtest_only={},
    )
    assert unexplained_live_only == {"fake_shared_rule"}, (
        "Scanner failed to detect an injected live-only rule with no exemption"
    )
    assert unexplained_backtest_only == set()


def test_scanner_correctly_flags_an_injected_backtest_only_rule_too(tmp_path):
    """The reverse direction, explicitly — a rule injected into the
    BACKTEST-only synthetic file this time, confirming the scanner
    doesn't only check "live has something backtest lacks" (the actual
    30-day-gap direction) but genuinely also catches the opposite."""
    live_file = tmp_path / "fake_live2.py"
    live_file.write_text(
        "def maybe_place_orders(config):\n"
        "    pass  # never calls fake_shared_rule at all\n",
        encoding="utf-8",
    )
    backtest_file = tmp_path / "fake_backtest2.py"
    backtest_file.write_text(
        "from analysis.decision import fake_shared_rule\n"
        "def simulate_portfolio_compounding_mtm(config):\n"
        "    if not fake_shared_rule(config):\n"
        "        return\n",
        encoding="utf-8",
    )

    candidates = {"fake_shared_rule"}
    live_calls = _called_function_names(live_file, candidates)
    backtest_calls = _called_function_names(backtest_file, candidates)

    unexplained_live_only, unexplained_backtest_only = _find_one_sided_violations(
        live_calls, backtest_calls, exempt_live_only={}, exempt_backtest_only={},
    )
    assert unexplained_backtest_only == {"fake_shared_rule"}, (
        "Scanner failed to detect an injected backtest-only rule with no exemption"
    )
    assert unexplained_live_only == set()


def test_scanner_correctly_passes_a_properly_shared_rule(tmp_path):
    """A rule called by both sides must never be flagged — the scanner
    isn't just flagging every function name it sees."""
    live_file = tmp_path / "fake_live3.py"
    live_file.write_text(
        "from analysis.decision import fake_shared_rule\n"
        "def maybe_place_orders(config):\n"
        "    if not fake_shared_rule(config):\n"
        "        return\n",
        encoding="utf-8",
    )
    backtest_file = tmp_path / "fake_backtest3.py"
    backtest_file.write_text(
        "from analysis.decision import fake_shared_rule\n"
        "def simulate_portfolio_compounding_mtm(config):\n"
        "    if not fake_shared_rule(config):\n"
        "        return\n",
        encoding="utf-8",
    )

    candidates = {"fake_shared_rule"}
    live_calls = _called_function_names(live_file, candidates)
    backtest_calls = _called_function_names(backtest_file, candidates)

    unexplained_live_only, unexplained_backtest_only = _find_one_sided_violations(
        live_calls, backtest_calls, exempt_live_only={}, exempt_backtest_only={},
    )
    assert unexplained_live_only == set()
    assert unexplained_backtest_only == set()


def test_scanner_correctly_passes_a_legitimately_exempted_one_sided_rule(tmp_path):
    """A one-sided rule WITH a recorded exemption must not be flagged --
    confirms the exemption mechanism itself works, not just that
    unexempted violations are caught."""
    live_file = tmp_path / "fake_live4.py"
    live_file.write_text(
        "from analysis.decision import fake_shared_rule\n"
        "def maybe_place_orders(config):\n"
        "    if not fake_shared_rule(config):\n"
        "        return\n",
        encoding="utf-8",
    )
    backtest_file = tmp_path / "fake_backtest4.py"
    backtest_file.write_text(
        "def simulate_portfolio_compounding_mtm(config):\n"
        "    pass  # deliberately, legitimately doesn't call fake_shared_rule\n",
        encoding="utf-8",
    )

    candidates = {"fake_shared_rule"}
    live_calls = _called_function_names(live_file, candidates)
    backtest_calls = _called_function_names(backtest_file, candidates)

    unexplained_live_only, unexplained_backtest_only = _find_one_sided_violations(
        live_calls, backtest_calls,
        exempt_live_only={"fake_shared_rule": "a made-up, legitimate reason for this test"},
        exempt_backtest_only={},
    )
    assert unexplained_live_only == set(), "A legitimately exempted rule must not be flagged"
    assert unexplained_backtest_only == set()


def test_decision_function_names_derived_dynamically_includes_real_functions():
    """Sanity check that the dynamic derivation actually finds real,
    known functions -- if inspect.getmembers()'s filter were subtly
    wrong (e.g. matching imported names too), every other test in this
    file would be exercising the wrong candidate set without any of them
    necessarily failing to notice."""
    names = _decision_function_names()
    for expected in ("evaluate_exit_reason", "passes_entry_gate", "is_reentry_blocked",
                      "is_asset_class_enabled", "passes_momentum_floor"):
        assert expected in names
    # Imported names (Optional, etc.) must never appear -- only functions
    # genuinely DEFINED in analysis/decision.py itself.
    assert "Optional" not in names
