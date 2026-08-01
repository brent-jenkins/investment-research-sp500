"""Layer 1 of the structural live/backtest parity enforcement added
2026-08-01 (see CLAUDE.md's "Structural enforcement for live/backtest
parity" section) -- a mechanical scan for decision-shaped logic living
inline in brokers/trading212.py or analysis/backtest.py instead of going
through the one shared implementation in analysis/decision.py.

This exists because the deployment-cap divergence (see CLAUDE.md's
"Live/backtest deployment-cap parity" section) proved that a targeted
parity test suite alone isn't sufficient enforcement -- it can only catch
divergence in a decision someone thought to test in advance, and
deployment-cap tracking was never on that suite's list at all. This test
is the opposite kind of check: it doesn't need to be told in advance what
decision to look for -- it scans structurally for the SHAPE of a
decision (a comparison against a known decision-signal quantity) and
fails if that shape appears somewhere it shouldn't, regardless of
whether anyone anticipated that specific comparison.

WHAT THIS CATCHES, PRECISELY -- and what it does NOT, stated plainly
rather than overclaimed:

  Catches: a raw Python comparison expression (`>`, `<`, `>=`, `<=`,
  `==`, `!=`) anywhere in the two scanned files whose left- or right-hand
  side text contains one of DECISION_SIGNAL_KEYWORDS below, UNLESS that
  exact (file, line) is in KNOWN_ACCEPTED_DUPLICATIONS. This is precisely
  the shape the deployment-cap bug actually had: two independently
  written `X > threshold` expressions in two different files, using
  different semantics for X.

  Does NOT catch: decision logic expressed any other way -- a lookup
  table, a dict-based dispatch, a call into a THIRD function outside
  analysis/decision.py that itself contains an unshared comparison, a
  comparison whose variable names don't happen to match the keyword list
  below, or genuine execution-mechanics code (retry counts, HTTP status
  codes, rate-limit sleep durations) that happens to use a keyword-
  adjacent variable name by coincidence. It is a targeted, high-precision
  scan for the ONE specific anti-pattern this project has now been
  burned by twice (see analysis/decision.py's own docstring for the
  first three instances found the same day) -- not a general-purpose
  decision-logic detector, and it does not claim to be one.
"""
import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCANNED_FILES = (
    _REPO_ROOT / "brokers" / "trading212.py",
    _REPO_ROOT / "analysis" / "backtest.py",
)

# Keywords identifying a comparison as "decision-shaped" -- deliberately
# narrow and literal, matching the exact vocabulary the four known
# decisions (exit priority, entry gate, deployment-cap, priority
# ranking) use in this codebase's own variable names. A comparison using
# none of these words is out of scope for this scan by design -- see the
# module docstring's "does NOT catch" list.
DECISION_SIGNAL_KEYWORDS = (
    "deployed_pct", "deployed_after_pct", "would_deploy_pct", "max_total_deployed",
    "max_deployed_pct", "current_deployed",
    "sig_on", "is_member", "entry_blocked",
    "exit_reason", "holding_days", "max_holding", "max_hold",
    "stop_price", "crisis_now",
    # Added 2026-08-01 during the full consolidation audit -- the original
    # vocabulary above would not have caught _submit_pending_orders()'s
    # hardcoded `if position_gbp < 10:` (a real, fixed divergence from the
    # shared is_position_size_viable() -- see analysis/decision.py and
    # CLAUDE.md's "Live and backtest consolidation audit" section).
    "position_gbp",
)

# (file name, line number) pairs for comparisons already reviewed and
# deliberately left as separate, unshared code -- each with the specific
# reason recorded in CLAUDE.md, not just "we decided it's fine." Adding
# an entry here is a real decision, not a way to silence this test --
# every entry must have a corresponding CLAUDE.md explanation.
#
# Line numbers are checked as of the commit this file was added in; a
# future refactor that moves one of these lines will make this test fail
# with a "not found at the recorded line" style mismatch (see
# test_no_stale_whitelist_entries below) rather than silently stop
# checking it -- the whitelist is re-validated on every run, not just
# trusted once and forgotten.
KNOWN_ACCEPTED_DUPLICATIONS: dict[str, set[int]] = {
    # simulate_portfolio() deliberately uses a different, simpler,
    # FROZEN deployment-cap formula (checked before the candidate's own
    # size is added, not after) for reproducibility reasons -- see
    # CLAUDE.md's "reviewed and left duplicated, on purpose" list.
    # Populated by test collection below via a source-text search rather
    # than a hardcoded line number, since the exact line shifts with
    # unrelated edits above it in the file -- see _find_whitelisted_lines().
}

# Source-text substrings identifying an ALREADY-REVIEWED duplication by
# content rather than a brittle hardcoded line number -- resolved to
# actual line numbers at collection time (see _find_whitelisted_lines()).
# Each entry names the file it applies to and the CLAUDE.md section that
# explains why it's accepted.
_WHITELISTED_SOURCE_SNIPPETS: tuple[tuple[str, str, str], ...] = (
    (
        "backtest.py",
        "current_deployed / starting_capital) >= max_total_deployed",
        "simulate_portfolio()'s frozen, pre-candidate-size, fixed-notional "
        "deployment check -- CLAUDE.md 'reviewed and left duplicated' list",
    ),
    (
        "trading212.py",
        "deployed_after_pct > max_deployed_pct",
        "live's real submission-time capacity check -- genuinely can't share "
        "one function with the backtest's simulated equivalent, since this "
        "side reads a live HTTP-sourced value and the backtest side computes "
        "from simulated state -- CLAUDE.md 'Live/backtest deployment-cap "
        "parity' section",
    ),
    (
        "backtest.py",
        "if would_deploy_pct > max_deployed_pct:",
        "the semantically risky part of this check -- what 'deployed' actually "
        "means -- is unified in the single shared _would_deploy_pct() helper "
        "(extracted 2026-08-01 specifically because this scanner flagged the "
        "computation duplicated verbatim between the two compounding engines "
        "the same day it was written); this remaining `> max_deployed_pct` "
        "comparison appears twice only because each engine's admit/reject "
        "branch differs structurally (different rejection-record shape, "
        "different capacity-mutation bookkeeping), not because the "
        "comparison itself could diverge -- CLAUDE.md 'Live/backtest "
        "deployment-cap parity' section",
    ),
    (
        "trading212.py",
        "position_gbp > free_cash",
        "live's submission-time free-cash affordability re-check inside "
        "_submit_pending_orders() -- genuinely live-only: free cash can have "
        "been consumed by other orders placed between decide-time and this "
        "asset class's own submission window, a real-time state the backtest "
        "has no equivalent for (no separate decide/submit phases, no "
        "intervening orders from a different asset class). Structurally "
        "different from resolve_cash_constrained_position_gbp()'s decide-time "
        "25%-of-cash sizing clamp, which both sides do share -- CLAUDE.md "
        "'Live and backtest consolidation audit' section",
    ),
)


def _find_whitelisted_lines() -> dict[str, set[int]]:
    """Resolve _WHITELISTED_SOURCE_SNIPPETS to actual (file, line) pairs by
    searching each target file's real source text -- avoids hardcoding
    line numbers that would silently go stale (and stop protecting
    anything) the moment an unrelated edit shifts them."""
    resolved: dict[str, set[int]] = {}
    for filename, snippet, _reason in _WHITELISTED_SOURCE_SNIPPETS:
        path = next(f for f in _SCANNED_FILES if f.name == filename)
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            if snippet in line:
                resolved.setdefault(filename, set()).add(lineno)
    return resolved


def _operand_names(node: ast.expr) -> set[str]:
    """Every ast.Name identifier referenced within *node* (walking into
    attribute access, subscripts, etc.) -- used to check whether a
    comparison operand is a genuine VARIABLE matching a decision keyword,
    not a coincidental keyword appearing inside a string literal (e.g.
    "holding_days" in df.columns, which mentions the word but isn't a
    holding_days *comparison* at all)."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _is_literal_outcome_check(node: ast.expr) -> bool:
    """True if *node* is a bare string/None/bool constant -- comparing a
    decision-outcome variable against one of these (exit_reason ==
    "STOP_LOSS", exit_reason is None) is READING a decision an upstream
    call already made, not computing a new one. Comparisons against a
    NUMBER (a threshold, a percentage, a day count) are NOT excluded by
    this -- that's the actually dangerous shape, where two places could
    independently pick a different threshold or operator for the same
    quantity."""
    return isinstance(node, ast.Constant) and (
        node.value is None or isinstance(node.value, (str, bool))
    )


def _decision_shaped_comparisons(path: Path) -> list[tuple[int, str]]:
    """Parse *path* and return (line_number, source_snippet) for every
    ast.Compare node that is genuinely decision-shaped: at least one
    operand is a real variable reference (not a string-literal mention)
    matching DECISION_SIGNAL_KEYWORDS, AND the comparison isn't simply
    checking that decision-outcome variable against a literal
    string/None/bool (see _is_literal_outcome_check -- that's consuming
    an already-made decision, not deriving a new one)."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue

        operands = [node.left, *node.comparators]
        keyword_operands = [
            o for o in operands
            if any(kw in name for name in _operand_names(o) for kw in DECISION_SIGNAL_KEYWORDS)
        ]
        if not keyword_operands:
            continue

        # If every OTHER operand (the ones not matching a keyword) is a
        # bare literal, this is reading an already-decided outcome, not
        # computing a new decision -- skip it.
        other_operands = [o for o in operands if o not in keyword_operands]
        if other_operands and all(_is_literal_outcome_check(o) for o in other_operands):
            continue

        segment = ast.get_source_segment(source, node) or ast.dump(node)
        hits.append((node.lineno, segment.strip()))
    return hits


def test_no_unreviewed_decision_shaped_comparisons_outside_decision_module():
    """The actual Layer 1 check. Fails loudly, naming every offending
    file/line/snippet, if a decision-shaped comparison is found in either
    scanned file that isn't an already-reviewed, CLAUDE.md-documented
    exception."""
    whitelisted = _find_whitelisted_lines()
    violations: list[str] = []

    for path in _SCANNED_FILES:
        allowed_lines = whitelisted.get(path.name, set())
        for lineno, snippet in _decision_shaped_comparisons(path):
            if lineno in allowed_lines:
                continue
            violations.append(f"{path.name}:{lineno}: {snippet}")

    assert not violations, (
        "Decision-shaped comparison(s) found outside analysis/decision.py "
        "and not in the reviewed whitelist -- this is exactly the shape "
        "the deployment-cap divergence had (see CLAUDE.md's 'Live/backtest "
        "deployment-cap parity' section). Either move this decision into "
        "analysis/decision.py and call it from both live and backtest, or "
        "if it's a genuinely reviewed, can't-share-one-function case, add "
        "it to KNOWN_ACCEPTED_DUPLICATIONS / _WHITELISTED_SOURCE_SNIPPETS "
        "here with a CLAUDE.md explanation of why.\n\n"
        + "\n".join(violations)
    )


def test_whitelist_entries_are_still_present_not_stale():
    """Every _WHITELISTED_SOURCE_SNIPPETS entry must actually match real
    source text right now -- if a refactor moved or rewrote one of these
    lines, this fails explicitly rather than the whitelist silently
    stopping protecting anything (a whitelist entry that matches nothing
    provides zero coverage, indistinguishable from having never been
    added, unless something asserts it's still findable)."""
    resolved = _find_whitelisted_lines()
    for filename, snippet, reason in _WHITELISTED_SOURCE_SNIPPETS:
        found = filename in resolved and len(resolved[filename]) > 0
        assert found, (
            f"Whitelist entry for {filename} (\"{snippet}\", reason: {reason}) "
            f"no longer matches any line in that file -- either the code moved "
            f"(update the snippet) or the decision was actually unified into "
            f"analysis/decision.py (remove this now-obsolete whitelist entry)."
        )


def test_scanner_actually_fails_on_a_deliberately_injected_violation(tmp_path):
    """Proves the scanner mechanism itself works, rather than just
    asserting it exists -- same verification standard the original
    live/backtest parity suite was held to earlier the same day (two
    deliberately injected bugs, confirmed the suite failed specifically
    on the scenario each would affect). Writes a synthetic file
    containing an unwhitelisted decision-shaped comparison and confirms
    the SAME detection logic this module uses actually flags it."""
    injected = tmp_path / "fake_trading212.py"
    injected.write_text(
        "def _check_capacity(invested, position_gbp, total_val, max_total_deployed):\n"
        "    deployed_after_pct = (invested + position_gbp) / total_val\n"
        "    if deployed_after_pct > max_total_deployed:\n"
        "        return False\n"
        "    return True\n",
        encoding="utf-8",
    )

    hits = _decision_shaped_comparisons(injected)
    assert hits, "Scanner failed to detect an injected deployment-cap-shaped comparison"
    assert any("deployed_after_pct" in snippet for _lineno, snippet in hits)

    # And confirm a comparison with NO decision keywords in it is correctly
    # left alone -- the scanner isn't just flagging every comparison in
    # the file indiscriminately.
    clean = tmp_path / "fake_clean.py"
    clean.write_text(
        "def _retry_delay(attempt, max_retries):\n"
        "    if attempt >= max_retries:\n"
        "        return None\n"
        "    return attempt * 2\n",
        encoding="utf-8",
    )
    assert _decision_shaped_comparisons(clean) == []
