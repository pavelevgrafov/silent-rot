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
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

CHECKER = Path(__file__).resolve().parent / "liveness.py"
RECEIPT = Path(__file__).resolve().parent / ".mutation-receipt.json"

# The rule table is read from the checker itself, so a rule that exists in the
# code but is proved by nothing here shows up as a stated gap rather than as
# silence. Importing runs no scan: liveness.py does its work under __main__.
sys.path.insert(0, str(CHECKER.parent))
from liveness import RECEIPT_NAME, RULES, SELF_FILES, self_hash  # noqa: E402

RULE_ID = re.compile(r"\bSR-[A-Z]+-\d{3}\b")


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


def scan(ws: Path, execute: bool = False, fmt: str = "text",
         checker: Path = CHECKER) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(checker), str(ws), "--format", fmt]
    if execute:
        argv += ["--execute-hooks", "--trusted-root", str(ws)]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


def run(ws: Path, execute: bool = False) -> set[str]:
    """The finding lines of one scan, verbatim.

    Whole lines, not ids: the delta against the baseline has to distinguish two
    findings that share a rule. The id is what the assertion then reads out of
    the delta."""
    out = scan(ws, execute).stdout
    return {l.strip() for l in out.splitlines() if l.strip().startswith("[")}


def rule_ids(lines: set[str]) -> set[str]:
    return {m.group(0) for l in lines for m in [RULE_ID.search(l)] if m}


def report(ws: Path, execute: bool = False) -> str:
    return scan(ws, execute).stdout


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


def _receipt_survives_an_edit(tmp: Path) -> tuple[set[str], str]:
    """The receipt has to expire when the code it attests to changes.

    Mutating the workspace cannot test this: the receipt belongs to the checker,
    not to the tree being scanned. So the checker is copied somewhere it has no
    receipt, given a valid one, and then edited by a single byte.

    The run *before* the edit is half the test. A checker that complained about
    its receipt unconditionally would pass the after-check while proving
    nothing — the failure mode this repo keeps meeting.
    """
    tool = tmp / "receipt-tool"
    if tool.exists():
        shutil.rmtree(tool)
    tool.mkdir()
    for name in SELF_FILES:
        shutil.copy(CHECKER.parent / name, tool / name)
    work = tmp / "receipt-ws"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    ws = build_fixture(work)
    checker = tool / "liveness.py"

    (tool / RECEIPT_NAME).write_text(json.dumps({
        "date": date.today().isoformat(), "mutations": 1, "passed": 1,
        "code_sha256": self_hash(tool)}), encoding="utf-8")
    before = {i for i in rule_ids(_finding_lines(ws, checker)) if "SELFTEST" in i}
    if before:
        return set(), f"the receipt was rejected before the edit: {sorted(before)}"

    checker.write_bytes(checker.read_bytes() + b"\n")   # one byte
    after = rule_ids(_finding_lines(ws, checker))
    return after, f"quiet with a matching receipt, {sorted(after)} after one byte"


def _finding_lines(ws: Path, checker: Path) -> set[str]:
    out = scan(ws, checker=checker).stdout
    return {l.strip() for l in out.splitlines() if l.strip().startswith("[")}


# Each case: what is broken, how, and what proves the checker noticed. `mode`
# picks the plain scan or the explicit trusted run; `check` carries the
# assertion when the evidence is a side effect rather than a finding.
#
# `expect` is a rule id, never a phrase from the message. It used to be a
# substring — "declared but missing", "BROKEN JSON" — which made every one of
# these tests a test of the wording. Rewrite a sentence and the mutation stops
# being caught, with nothing on screen to say a rule went unproven.
MUTATIONS = [
    dict(name="declared path does not exist",
         mutate=lambda ws: (ws / "beta" / "CLAUDE.md").write_text(
             "# beta\n\nSee `docs/handbook.md`.\n", encoding="utf-8"),
         expect="SR-REF-001"),

    dict(name="hook declared, file absent",
         mutate=lambda ws: (ws / "alpha" / ".claude" / "hooks" / "nudge.sh").unlink(),
         expect="SR-HOOK-001"),

    dict(name="hook exits non-zero",
         mutate=lambda ws: (ws / "alpha" / ".claude" / "hooks" / "nudge.sh").write_text(
             "#!/bin/bash\nexit 3\n", encoding="utf-8"),
         expect="SR-HOOK-004", mode="execute"),

    dict(name="settings file is not valid JSON",
         mutate=lambda ws: (ws / "alpha" / ".claude" / "settings.json").write_text(
             "{ broken", encoding="utf-8"),
         expect="SR-SETTINGS-001"),

    dict(name="declared folder is empty",
         mutate=lambda ws: (ws / "alpha" / "artifacts").mkdir(),
         expect="SR-EMPTY-001"),

    dict(name="queue row has gone stale",
         mutate=lambda ws: (ws / "alpha" / "queue.md").write_text(
             f"| Date | Item | Status |\n|---|---|---|\n| {OLD} | thing | unprocessed |\n",
             encoding="utf-8"),
         expect="SR-QUEUE-001"),

    dict(name="hand-written count in an instruction file",
         mutate=lambda ws: (ws / "beta" / "CLAUDE.md").write_text(
             "# beta\n\nThis workspace holds 15 projects.\n", encoding="utf-8"),
         expect="SR-COUNT-001"),

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
         mutate=_shell_operator, expect="SR-HOOK-002"),

    dict(name="hook outside the scanned root is not executed",
         mutate=_outside_root, expect="SR-HOOK-003", mode="execute",
         check=_no_sentinel),

    # Breaks the checker rather than the fixture, so it runs its own copy.
    dict(name="receipt no longer matches the code it attests to",
         custom=_receipt_survives_an_edit, expect="SR-SELFTEST-005"),
]


# --- The machine-readable report --------------------------------------------
# Not mutations: nothing is broken below. These assert that the two renderings
# of one scan agree, and that the JSON shape cannot drift without saying so.

GOLDEN = Path(__file__).resolve().parent / "golden" / "report.json"
COVERAGE_LINE = re.compile(r"^(\w+)\s+(\d+) discovered, (\d+) checked, (\d+) skipped")


def build_reportable(root: Path) -> Path:
    """The fixture plus enough defects that the report has something to say. A
    golden file over an empty findings list proves the envelope and nothing
    inside it. The undated row is deliberate: a pending row with no date cannot
    be aged, and has to be counted as unchecked rather than as fine."""
    ws = build_fixture(root)
    (ws / "beta" / "CLAUDE.md").write_text(
        "# beta\n\nSee `docs/handbook.md`. This workspace holds 15 projects.\n",
        encoding="utf-8")
    (ws / "alpha" / "artifacts").mkdir()
    (ws / "alpha" / "queue.md").write_text(
        "| Date | Item | Status |\n|---|---|---|\n"
        f"| {OLD} | thing | unprocessed |\n"
        "| | undated | pending |\n", encoding="utf-8")
    return ws


def portable_checker(tmp: Path) -> Path:
    """A copy of the checker, run from a directory of its own.

    The real one reads its mutation receipt from the directory it lives in, and
    whether that receipt exists is a property of this machine rather than of the
    fixture — a golden file that depends on it is green or red by accident."""
    d = tmp / "tool"
    d.mkdir(exist_ok=True)
    copy = d / "liveness.py"
    shutil.copy(CHECKER, copy)
    return copy


def masked(doc: dict, ws: Path, tmp: Path) -> dict:
    """Everything the fixture cannot control: the clock, the temporary path the
    fixture was built in, and the version string."""
    s = json.dumps(doc, ensure_ascii=False)
    s = s.replace(str(ws), "<ROOT>").replace(str(tmp), "<TMP>")
    out = json.loads(s)
    out["started_at"] = "<TIMESTAMP>"
    out["root"] = "<ROOT>"
    out["tool"]["version"] = "<VERSION>"
    return out


def acc_json_matches_text(ws: Path, tmp: Path) -> tuple[bool, str]:
    """One scan, two renderings, one set of numbers. Coverage that disagrees
    with itself is worse than no coverage: both numbers look authoritative."""
    text = scan(ws).stdout
    doc = json.loads(scan(ws, fmt="json").stdout)
    from_text = {m.group(1): (int(m.group(2)), int(m.group(3)), int(m.group(4)))
                 for l in text.splitlines()
                 if (m := COVERAGE_LINE.match(l.strip()))}
    from_json = {k: (v["discovered"], v["checked"], v["skipped"])
                 for k, v in doc["coverage"].items()}
    if not from_text:
        return False, "the text report printed no coverage lines at all"
    if from_text != from_json:
        differ = {k for k in from_json if from_text.get(k) != from_json[k]}
        return False, f"classes disagree: {sorted(differ)}"
    in_text = len([l for l in text.splitlines() if l.strip().startswith("[")])
    if in_text != doc["summary"]["findings"]:
        return False, f"findings: {in_text} in text, {doc['summary']['findings']} in json"
    if off := unbalanced(doc["coverage"]):
        return False, f"discovered != checked + skipped in {off}"
    return True, f"{len(from_text)} classes, {in_text} findings"


def unbalanced(cov: dict) -> list[str]:
    """Every discovered object was either checked or skipped. Without this,
    "0 discovered, 1 skipped" is printable, and a reader cannot tell whether the
    class was thin or the counting was wrong."""
    return sorted(k for k, v in cov.items()
                  if v["discovered"] != v["checked"] + v["skipped"])


def acc_json_is_alone_on_stdout(ws: Path, tmp: Path) -> tuple[bool, str]:
    r = scan(ws, fmt="json")
    try:
        json.loads(r.stdout)
    except ValueError as e:
        return False, f"stdout is not exactly one json object: {e}"
    # Negative half: a run that fails must not put its complaint where the
    # parser is reading. Scanning a path that does not exist is the cheapest way
    # to make the tool talk.
    bad = scan(ws / "does-not-exist", fmt="json")
    if bad.stdout.strip():
        return False, f"a failed json run wrote to stdout: {bad.stdout[:80]!r}"
    if not bad.stderr.strip():
        return False, "a failed json run reported nothing on stderr either"
    return True, "one object on stdout, diagnostics on stderr"


def acc_golden(ws: Path, tmp: Path) -> tuple[bool, str]:
    doc = masked(json.loads(scan(ws, fmt="json",
                                 checker=portable_checker(tmp)).stdout), ws, tmp)
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if "--update-golden" in sys.argv:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
        return True, f"rewritten: {GOLDEN.name}"
    if not GOLDEN.is_file():
        return False, f"no golden file at {GOLDEN} — run with --update-golden"
    if GOLDEN.read_text(encoding="utf-8") != text:
        return False, ("the report has changed shape; read the diff, then "
                       "rerun with --update-golden if the change is intended")
    return True, "byte-identical once the clock and the temp path are masked"


def acc_bad_input_is_a_skip(ws: Path, tmp: Path) -> tuple[bool, str]:
    """Input the checker cannot read must become a counted skip.

    Both halves crashed the whole scan in 0.1.1: `2026-13-45` matches the shape
    of a date and is not one, and a file the process cannot open raised out of
    the middle of the pass. A traceback is not a report."""
    work = tmp / "badinput"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    bad = build_fixture(work)
    (bad / "alpha" / "queue.md").write_text(
        "| Date | Item | Status |\n|---|---|---|\n"
        "| 2026-13-45 | thing | unprocessed |\n", encoding="utf-8")
    locked = bad / "beta" / "CLAUDE.md"
    locked.chmod(0o000)
    try:
        r = scan(bad, fmt="json")
        if r.returncode != 0:
            return False, f"the scan died: {r.stderr.strip().splitlines()[-1:]}"
        cov = json.loads(r.stdout)["coverage"]
        reasons = {r for c in cov.values() for r in c["skipped_reasons"]}
        if not any("does not parse" in r for r in reasons):
            return False, f"the malformed date was not counted as a skip: {reasons}"
        # The only fixture here whose classes carry skips, so the only place the
        # counting invariant can actually come apart.
        if off := unbalanced(cov):
            return False, f"discovered != checked + skipped in {off}"
        if not any("unreadable" in r for r in reasons):
            # Root reads anything; say so rather than pass the half silently.
            return True, "date skip counted; the unreadable half needs a non-root user"
        return True, "both counted as skips, exit code 0"
    finally:
        locked.chmod(0o644)


ACCEPTANCE = [
    ("json and text report the same numbers", acc_json_matches_text),
    ("json mode keeps stdout parseable", acc_json_is_alone_on_stdout),
    ("unreadable input is a skip, not a crash", acc_bad_input_is_a_skip),
    ("report shape matches the golden file", acc_golden),
]


def main() -> int:
    failures = []          # mutations the checker stayed quiet about
    refused: list[tuple[str, str]] = []   # acceptance checks that did not hold
    proven: set[str] = set()
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
            if case.get("custom"):
                # A case that breaks the checker instead of the workspace, and
                # therefore arranges its own run.
                ids, detail = case["custom"](Path(tmp))
                proven.add(case["expect"])
                caught = case["expect"] in ids
                print(f"  {'caught ' if caught else 'MISSED '} {name} — {detail}")
                if not caught:
                    failures.append((name, case["expect"], sorted(ids)))
                continue
            work = Path(tmp) / "case"
            if work.exists():
                shutil.rmtree(work)
            work.mkdir()
            ws = build_fixture(work)
            case["mutate"](ws)
            new = run(ws, execute=(mode == "execute")) - baselines[mode]

            caught = True
            if case.get("expect"):
                caught = case["expect"] in rule_ids(new)
                proven.add(case["expect"])
            if caught and case.get("check"):
                caught = case["check"](ws)

            print(f"  {'caught ' if caught else 'MISSED '} {name} [{mode}]")
            if not caught:
                failures.append((name, case.get("expect"), sorted(new)))

        work = Path(tmp) / "reportable"
        work.mkdir()
        ws = build_reportable(work)
        print()
        for name, fn in ACCEPTANCE:
            ok, detail = fn(ws, Path(tmp))
            print(f"  {'ok     ' if ok else 'FAILED '} {name} — {detail}")
            if not ok:
                refused.append((name, detail))

    # A rule nothing here breaks is a rule this suite says nothing about. Naming
    # them is the same discipline the checker applies to the tree it scans: an
    # unchecked class that goes unmentioned reads as a checked one.
    unproven = sorted(set(RULES) - proven)
    print(f"\nrules with a mutation: {len(proven)}/{len(RULES)}")
    if unproven:
        print(f"rules proved by nothing here: {', '.join(unproven)}")

    if failures:
        print(f"\nmutations not caught: {len(failures)}/{len(MUTATIONS)}")
        for name, expected, new in failures:
            print(f"\n  {name}\n    expected rule: {expected}\n    new findings: {new or '—'}")
    if refused:
        print(f"\nacceptance checks that did not hold: {len(refused)}/{len(ACCEPTANCE)}")
        for name, detail in refused:
            print(f"\n  {name}\n    {detail}")
    if failures or refused:
        return 1

    RECEIPT.write_text(json.dumps({
        "date": date.today().isoformat(),
        "mutations": len(MUTATIONS),
        "passed": len(MUTATIONS),
        # What the run actually attests to. Without it the receipt says the
        # suite passed on a day, not that it passed on this code.
        "code_sha256": self_hash(),
    }), encoding="utf-8")
    print(f"\nall {len(MUTATIONS)} mutations caught; receipt written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
