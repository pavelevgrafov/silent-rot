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


def run(ws: Path, execute: bool = False) -> set[str]:
    argv = [sys.executable, str(CHECKER), str(ws)]
    if execute:
        argv += ["--execute-hooks", "--trusted-root", str(ws)]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    return {l.strip().lstrip("— ").strip()
            for l in r.stdout.splitlines() if l.strip().startswith("—")}


def report(ws: Path, execute: bool = False) -> str:
    argv = [sys.executable, str(CHECKER), str(ws)]
    if execute:
        argv += ["--execute-hooks", "--trusted-root", str(ws)]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120).stdout


LONG_RULE = ("Every hypothesis gets a kill criterion before the test starts, "
             "with the number and the deadline written down in advance.\n")
OLD = (date.today() - timedelta(days=90)).isoformat()

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


def _hostile(ws: Path) -> None:
    """The hook a stranger's repository ships: it writes outside the tree being
    scanned. Until v0.1.1 a plain scan ran this."""
    (ws / "alpha" / ".claude" / "hooks" / "nudge.sh").write_text(
        f'#!/bin/bash\ntouch "{ws.parent / "SENTINEL"}"\necho \'{{"continue": true}}\'\n',
        encoding="utf-8")


def _shell_operator(ws: Path) -> None:
    """A command the checker must refuse to interpret rather than guess at."""
    hook = ws / "alpha" / ".claude" / "hooks" / "nudge.sh"
    (ws / "alpha" / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": f'cat /etc/hosts | "{hook}"'}]}]}}),
        encoding="utf-8")


def _outside_root(ws: Path) -> None:
    """A hook that exists, is healthy, and is not in the tree being scanned:
    the scan may not claim to have checked it."""
    outside = ws.parent / "elsewhere.sh"
    outside.write_text('#!/bin/bash\necho \'{"continue": true}\'\n', encoding="utf-8")
    outside.chmod(0o755)
    (ws / "alpha" / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": f'bash "{outside}"'}]}]}}),
        encoding="utf-8")


def _no_sentinel(ws: Path) -> bool:
    return not (ws.parent / "SENTINEL").exists()


# Each case: what is broken, how, and what proves the checker noticed. `mode`
# picks the plain scan or the explicit trusted run; `check` replaces the
# substring assertion when the evidence is a side effect rather than a line.
MUTATIONS = [
    dict(name="declared path does not exist",
         mutate=lambda ws: (ws / "beta" / "CLAUDE.md").write_text(
             "# beta\n\nSee `docs/handbook.md`.\n", encoding="utf-8"),
         expect="declared but missing"),

    dict(name="hook declared, file absent",
         mutate=lambda ws: (ws / "alpha" / ".claude" / "hooks" / "nudge.sh").unlink(),
         expect="hook does not exist"),

    dict(name="hook exits non-zero",
         mutate=lambda ws: (ws / "alpha" / ".claude" / "hooks" / "nudge.sh").write_text(
             "#!/bin/bash\nexit 3\n", encoding="utf-8"),
         expect="hook fails", mode="execute"),

    dict(name="settings file is not valid JSON",
         mutate=lambda ws: (ws / "alpha" / ".claude" / "settings.json").write_text(
             "{ broken", encoding="utf-8"),
         expect="BROKEN JSON"),

    dict(name="declared folder is empty",
         mutate=lambda ws: (ws / "alpha" / "artifacts").mkdir(),
         expect="declared folder is empty"),

    dict(name="queue row has gone stale",
         mutate=lambda ws: (ws / "alpha" / "queue.md").write_text(
             f"| Date | Item | Status |\n|---|---|---|\n| {OLD} | thing | unprocessed |\n",
             encoding="utf-8"),
         expect="unprocessed for"),

    dict(name="hand-written count in an instruction file",
         mutate=lambda ws: (ws / "beta" / "CLAUDE.md").write_text(
             "# beta\n\nThis workspace holds 15 projects.\n", encoding="utf-8"),
         expect="hand-written count"),

    dict(name="stateful hook is not consumed by the audit",
         mutate=_stateful, expect=None, mode="execute",
         check=lambda ws: (ws / "alpha" / ".claude" / "nudge-last-count"
                           ).read_text().strip() == "99"),

    # v0.1.1. The first of these is the regression test for the defect this
    # release exists to fix: a plain scan must not run what the tree declares.
    dict(name="hostile hook is NOT run by a plain scan",
         mutate=_hostile, expect=None, check=_no_sentinel),

    dict(name="plain scan states that nothing was executed",
         mutate=lambda ws: None, expect=None,
         check=lambda ws: "0 executed" in report(ws)),

    dict(name="shell operator in a hook command is refused, not interpreted",
         mutate=_shell_operator, expect="not statically supported"),

    dict(name="hook outside the scanned root is not executed",
         mutate=_outside_root, expect="outside the trusted root", mode="execute",
         check=_no_sentinel),
]


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "pristine"
        base.mkdir()
        pristine = build_fixture(base)
        # One baseline per mode: the trusted run reaches findings the plain scan
        # cannot, so comparing an executed case against a static baseline would
        # score the difference between the modes as a caught mutation.
        baselines = {"static": run(pristine), "execute": run(pristine, execute=True)}
        print(f"fixture baseline: {len(baselines['static'])} static, "
              f"{len(baselines['execute'])} with hooks executed")

        for case in MUTATIONS:
            name, mode = case["name"], case.get("mode", "static")
            work = Path(tmp) / "case"
            if work.exists():
                shutil.rmtree(work)
            work.mkdir()
            ws = build_fixture(work)
            case["mutate"](ws)
            new = run(ws, execute=(mode == "execute")) - baselines[mode]

            caught = True
            if case.get("expect"):
                caught = any(case["expect"] in p for p in new)
            if caught and case.get("check"):
                caught = case["check"](ws)

            print(f"  {'caught ' if caught else 'MISSED '} {name} [{mode}]")
            if not caught:
                failures.append((name, case.get("expect"), sorted(new)))

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
