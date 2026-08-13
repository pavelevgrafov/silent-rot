#!/usr/bin/env python3
"""Mutation test for liveness.py.

A check that has never reported anything is indistinguishable from a check that
cannot report anything. Counting checks does not help: in the audit this repo
comes from, six defects were found *inside the checker* — every one of them a
check that ran, counted, and was structurally unable to find its target.

So this harness does not assert "the workspace is clean". It builds a fixture
workspace, breaks one thing at a time, and fails if the checker stays quiet.
Mutations are compared against the fixture's own baseline rather than against
zero, so a new check landing in liveness.py does not break the harness.

Run:  python3 test_liveness.py     (exit 0 = every mutation was caught)
"""
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

CHECKER = Path(__file__).resolve().parent / "liveness.py"
RECEIPT = Path(__file__).resolve().parent / ".mutation-receipt.json"


def build_fixture(root: Path) -> Path:
    """A small but structurally honest workspace: two projects, one owning a
    queue and a hook.

    Built fresh for every mutation rather than copied: a settings file carries
    the absolute path of its hook, and in a copied tree that path still points
    at the original — so deleting the copy's hook proves nothing. The first
    version of this harness copied, and reported five checker defects that were
    all bugs in the harness.
    """
    ws = root / "workspace"
    alpha = ws / "alpha"
    (alpha / ".claude" / "hooks").mkdir(parents=True)
    (ws / "beta").mkdir(parents=True)

    (alpha / "CLAUDE.md").write_text(
        "# alpha\n\nThe queue lives in `queue.md`.\n", encoding="utf-8")
    (ws / "beta" / "CLAUDE.md").write_text("# beta\n\nNothing declared.\n", encoding="utf-8")

    hook = alpha / ".claude" / "hooks" / "nudge.sh"
    hook.write_text('#!/bin/bash\necho \'{"continue": true}\'\n', encoding="utf-8")
    hook.chmod(0o755)
    (alpha / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": f'"{hook}" 2>/dev/null || true'}]}]}}),
        encoding="utf-8")

    fresh = (date.today() - timedelta(days=2)).isoformat()
    (alpha / "queue.md").write_text(
        f"| Date | Item | Status |\n|---|---|---|\n| {fresh} | thing | unprocessed |\n",
        encoding="utf-8")
    return ws


def run(ws: Path) -> set[str]:
    r = subprocess.run([sys.executable, str(CHECKER), str(ws)],
                       capture_output=True, text=True, timeout=120)
    return {l.strip().lstrip("— ").strip()
            for l in r.stdout.splitlines() if l.strip().startswith("—")}


LONG_RULE = ("Every hypothesis gets a kill criterion before the test starts, "
             "with the number and the deadline written down in advance.\n")
OLD = (date.today() - timedelta(days=90)).isoformat()

MUTATIONS = [
    ("declared path does not exist",
     lambda ws: (ws / "beta" / "CLAUDE.md").write_text(
         "# beta\n\nSee `docs/handbook.md`.\n", encoding="utf-8"),
     "declared but missing"),

    ("hook declared, file absent",
     lambda ws: (ws / "alpha" / ".claude" / "hooks" / "nudge.sh").unlink(),
     "hook does not exist"),

    ("hook exits non-zero",
     lambda ws: (ws / "alpha" / ".claude" / "hooks" / "nudge.sh").write_text(
         "#!/bin/bash\nexit 3\n", encoding="utf-8"),
     "hook fails"),

    ("settings file is not valid JSON",
     lambda ws: (ws / "alpha" / ".claude" / "settings.json").write_text(
         "{ broken", encoding="utf-8"),
     "BROKEN JSON"),

    ("declared folder is empty",
     lambda ws: (ws / "alpha" / "artifacts").mkdir(),
     "declared folder is empty"),

    ("queue row has gone stale",
     lambda ws: (ws / "alpha" / "queue.md").write_text(
         f"| Date | Item | Status |\n|---|---|---|\n| {OLD} | thing | unprocessed |\n",
         encoding="utf-8"),
     "unprocessed for"),

    ("hand-written count in an instruction file",
     lambda ws: (ws / "beta" / "CLAUDE.md").write_text(
         "# beta\n\nThis workspace holds 15 projects.\n", encoding="utf-8"),
     "hand-written count"),

    ("stateful hook is not consumed by the audit",
     lambda ws: _stateful(ws),
     None),  # asserted separately, see below
]


def _stateful(ws: Path) -> None:
    """A nudge that reports only on change, plus a state file holding a stale
    value. After the checker runs, the state must be untouched — otherwise the
    audit has eaten the notification the user was owed."""
    hook = ws / "alpha" / ".claude" / "hooks" / "nudge.sh"
    state = ws / "alpha" / ".claude" / "nudge-last-count"
    state.write_text("99\n", encoding="utf-8")
    hook.write_text(
        f'#!/bin/bash\necho 1 > "{state}"\necho \'{{"continue": true}}\'\n',
        encoding="utf-8")


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "pristine"
        base.mkdir()
        baseline = run(build_fixture(base))
        print(f"fixture baseline: {len(baseline)} problem(s)")

        for name, mutate, expected in MUTATIONS:
            work = Path(tmp) / "case"
            if work.exists():
                shutil.rmtree(work)
            work.mkdir()
            ws = build_fixture(work)
            mutate(ws)
            new = run(ws) - baseline

            if expected is None:  # state-preservation case
                state = ws / "alpha" / ".claude" / "nudge-last-count"
                caught = state.read_text().strip() == "99"
            else:
                caught = any(expected in p for p in new)

            print(f"  {'caught ' if caught else 'MISSED '} {name}")
            if not caught:
                failures.append((name, expected, sorted(new)))

    if failures:
        print(f"\nmutations not caught: {len(failures)}/{len(MUTATIONS)}")
        for name, expected, new in failures:
            print(f"\n  {name}\n    expected substring: {expected}\n    new problems: {new or '—'}")
        return 1

    RECEIPT.write_text(json.dumps({
        "date": date.today().isoformat(),
        "mutations": len(MUTATIONS),
        "passed": len(MUTATIONS),
    }), encoding="utf-8")
    print(f"\nall {len(MUTATIONS)} mutations caught; receipt written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
