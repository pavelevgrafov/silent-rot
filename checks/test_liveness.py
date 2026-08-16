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
from liveness import (CONFIG_NAME, RECEIPT_NAME, RULES,  # noqa: E402
                      SELF_FILES, self_hash)

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


def _declare_empty_dir(ws: Path) -> None:
    """A folder the document presents as part of the structure, and nothing in
    it. The declaration is what makes it a broken promise rather than somebody's
    scratch space."""
    (ws / "alpha" / "artifacts").mkdir()
    (ws / "alpha" / "CLAUDE.md").write_text(
        "# alpha\n\nThe queue lives in `queue.md`.\nArtifacts go in `artifacts/`.\n",
        encoding="utf-8")


def _portable(tmp: Path, name: str) -> tuple[Path, Path]:
    """A copy of the checker in a directory of its own, beside a fresh fixture.

    Everything about the receipt has to be tested this way. The receipt belongs
    to the checker, not to the tree being scanned, so mutating the workspace
    cannot reach it — and against the real checker the answer would depend on
    who last ran the suite on this machine rather than on the case."""
    tool, work = tmp / f"tool-{name}", tmp / f"ws-{name}"
    for d in (tool, work):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
    for f in SELF_FILES:
        shutil.copy(CHECKER.parent / f, tool / f)
    return tool / "liveness.py", build_fixture(work)


def _receipt_case(name: str, receipt):
    """One mutation per receipt state the checker claims to recognise.

    `receipt` is a dict, raw text, or None for no receipt at all. The string
    "<self>" stands for the digest of the copy — a case about staleness needs
    an otherwise valid receipt, or the mismatch rule answers first and the case
    proves the wrong thing."""
    def case(tmp: Path) -> tuple[set[str], str]:
        checker, ws = _portable(tmp, name)
        if receipt is not None:
            body = receipt
            if isinstance(body, dict):
                body = json.dumps({k: (self_hash(checker.parent)
                                       if v == "<self>" else v)
                                   for k, v in body.items()})
            (checker.parent / RECEIPT_NAME).write_text(body, encoding="utf-8")
        ids = rule_ids(_finding_lines(ws, checker))
        return ids, ", ".join(sorted(i for i in ids if "SELFTEST" in i)) or "quiet"
    return case


def _receipt_survives_an_edit(tmp: Path) -> tuple[set[str], str]:
    """The receipt has to expire when the code it attests to changes.

    The run *before* the edit is half the test. A checker that complained about
    its receipt unconditionally would pass the after-check while proving
    nothing — the failure mode this repo keeps meeting."""
    checker, ws = _portable(tmp, "edit")
    (checker.parent / RECEIPT_NAME).write_text(json.dumps({
        "date": date.today().isoformat(), "mutations": 1, "passed": 1,
        "code_sha256": self_hash(checker.parent)}), encoding="utf-8")
    before = {i for i in rule_ids(_finding_lines(ws, checker)) if "SELFTEST" in i}
    if before:
        return set(), f"the receipt was rejected before the edit: {sorted(before)}"

    checker.write_bytes(checker.read_bytes() + b"\n")   # one byte
    after = rule_ids(_finding_lines(ws, checker))
    return after, f"quiet with a matching receipt, {sorted(after)} after one byte"


def _hook_hangs(tmp: Path) -> tuple[set[str], str]:
    """A hook that does not come back.

    The real threshold is 25 seconds, and waiting it out on every run of this
    suite is 25 seconds of nothing. The copy runs with the threshold rewritten
    to one second against a hook that sleeps three: same branch, same code path,
    a wait a test can afford. The substitution is asserted — without that check
    a renamed constant would turn this into a test of a checker that never times
    out, passing quietly."""
    checker, ws = _portable(tmp, "hang")
    src = checker.read_text(encoding="utf-8")
    patched = src.replace("timeout=HOOK_TIMEOUT,", "timeout=1,")
    if patched == src:
        return set(), "the timeout is no longer written as HOOK_TIMEOUT; case is stale"
    checker.write_text(patched, encoding="utf-8")
    (ws / "alpha" / ".claude" / "hooks" / "nudge.sh").write_text(
        "#!/bin/bash\nsleep 3\n", encoding="utf-8")
    ids = rule_ids(_finding_lines(ws, checker, execute=True))
    return ids, "threshold rewritten to 1s in a copy, hook sleeps 3s"


def _finding_lines(ws: Path, checker: Path, execute: bool = False) -> set[str]:
    out = scan(ws, execute=execute, checker=checker).stdout
    return {l.strip() for l in out.splitlines() if l.strip().startswith("[")}


def _workflow(ws: Path, body: str, name: str = "ci.yml") -> Path:
    d = ws / "alpha" / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")
    return d / name


def _script_called_automatic(ws: Path) -> None:
    """The class this whole repo exists for: the check is written, it is
    documented as running by itself, and nothing calls it."""
    (ws / "alpha" / "scripts").mkdir()
    (ws / "alpha" / "scripts" / "validate.sh").write_text(
        "#!/bin/bash\nexit 0\n", encoding="utf-8")
    (ws / "alpha" / "CLAUDE.md").write_text(
        "# alpha\n\nThe queue lives in `queue.md`.\n"
        "`scripts/validate.sh` runs automatically on every commit and blocks "
        "the merge when it fails.\n", encoding="utf-8")


def _script_wired_to_a_hook(ws: Path) -> None:
    """The same claim as _script_called_automatic, and this time it is true:
    a settings file names the script. The rule must stay silent."""
    _script_called_automatic(ws)
    hook = ws / "alpha" / "scripts" / "validate.sh"
    (ws / "alpha" / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": f'"{hook}"'}]}]}}), encoding="utf-8")


def _config(ws: Path, body: str) -> None:
    (ws / CONFIG_NAME).write_text(body, encoding="utf-8")


def _config_grants_execution(ws: Path) -> None:
    """The config file lives inside the tree being scanned, so it is written by
    whoever wrote that tree. It may narrow a scan; it may never widen what the
    scan is allowed to do."""
    _hostile(ws)
    _config(ws, "execute_hooks = true\ntrusted = true\n")


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
         mutate=_declare_empty_dir, expect="SR-EMPTY-001"),

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

    # These break the checker rather than the fixture, so they run their own
    # copy of it. Every receipt state the checker claims to recognise gets one:
    # before this, four of them were rules nothing here ever made speak.
    dict(name="receipt no longer matches the code it attests to",
         custom=_receipt_survives_an_edit, expect="SR-SELFTEST-005"),

    dict(name="mutation test has never passed here",
         custom=_receipt_case("none", None), expect="SR-SELFTEST-001"),

    dict(name="receipt is not readable",
         custom=_receipt_case("broken", "{ not json"), expect="SR-SELFTEST-002"),

    dict(name="receipt records a run that did not finish",
         custom=_receipt_case("partial", {
             "date": date.today().isoformat(), "mutations": 17, "passed": 16,
             "code_sha256": "<self>"}),
         expect="SR-SELFTEST-003"),

    dict(name="receipt is older than the threshold",
         custom=_receipt_case("old", {
             "date": OLD, "mutations": 1, "passed": 1, "code_sha256": "<self>"}),
         expect="SR-SELFTEST-004"),

    dict(name="hook hangs and is reported rather than waited on forever",
         custom=_hook_hangs, expect="SR-HOOK-005"),

    # The class the case study leads with: written, documented, wired to nothing.
    dict(name="workflow nothing in the repository can start",
         mutate=lambda ws: _workflow(ws, "name: ci\non:\n  workflow_dispatch:\n"
                                         "jobs:\n  a:\n    runs-on: ubuntu-latest\n"),
         expect="SR-WIRE-001"),

    dict(name="workflow path filter matches no file in the tree",
         mutate=lambda ws: _workflow(ws, "name: ci\non:\n  push:\n    paths:\n"
                                         "      - 'src/**'\n"
                                         "jobs:\n  a:\n    runs-on: ubuntu-latest\n"),
         expect="SR-WIRE-002"),

    dict(name="documented as automatic, named by nothing that runs",
         mutate=_script_called_automatic, expect="SR-WIRE-003"),

    dict(name="trigger block this checker cannot read is reported as unread",
         mutate=lambda ws: _workflow(ws, "name: ci\non: &base\n  push:\n"
                                         "jobs:\n  a:\n    runs-on: ubuntu-latest\n"),
         expect="SR-WIRE-004"),

    dict(name="config file does not parse",
         mutate=lambda ws: _config(ws, "stale_days = \n"),
         expect="SR-CONFIG-001"),

    dict(name="config key that does nothing is reported, not dropped",
         mutate=lambda ws: _config(ws, 'pendingstatuses = ["todo"]\n'),
         expect="SR-CONFIG-002"),

    dict(name="config cannot grant the scan permission to execute",
         mutate=_config_grants_execution, expect="SR-CONFIG-002",
         check=_no_sentinel),

    dict(name="config path pointing out of the root is refused",
         mutate=lambda ws: _config(ws, 'instruction_files = ["../../etc/hosts"]\n'),
         expect="SR-CONFIG-002"),
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
    _declare_empty_dir(ws)
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


def acc_config_keys_do_something(ws: Path, tmp: Path) -> tuple[bool, str]:
    """Each of the four keys has to change what the scan does.

    The first of these is the case the whole key exists for: a queue whose rows
    say `todo` is invisible to a checker looking for `unprocessed`, and the run
    comes back clean. Both directions are asserted — a key that is read but
    ignored, and a key that fires whatever the config says, look identical from
    one run.
    """
    recent = (date.today() - timedelta(days=20)).isoformat()

    def fixture(name: str, arrange) -> Path:
        work = tmp / f"cfg-{name}"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir()
        w = build_fixture(work)
        arrange(w)
        return w

    def queue(ws: Path, status: str, when: str) -> None:
        (ws / "alpha" / "queue.md").write_text(
            f"| Date | Item | Status |\n|---|---|---|\n| {when} | thing | {status} |\n",
            encoding="utf-8")

    cases = [
        ("pending_statuses", lambda w: queue(w, "todo", OLD),
         'pending_statuses = ["todo"]\n', "SR-QUEUE-001", True),
        ("stale_days", lambda w: queue(w, "unprocessed", recent),
         "stale_days = 30\n", "SR-QUEUE-001", False),
        ("instruction_files",
         lambda w: (w / "alpha" / "NOTES.md").write_text(
             "See `docs/handbook.md`.\n", encoding="utf-8"),
         'instruction_files = ["NOTES.md"]\n', "SR-REF-001", True),
        ("exclude_globs", _declare_empty_dir,
         'exclude_globs = ["alpha/**"]\n', "SR-EMPTY-001", False),
    ]

    for key, arrange, body, rule, appears in cases:
        w = fixture(key, arrange)
        before = rule in rule_ids(_finding_lines(w, CHECKER))
        _config(w, body)
        after = rule in rule_ids(_finding_lines(w, CHECKER))
        if (before, after) != (not appears, appears):
            return False, (f"{key}: {rule} was {'there' if before else 'absent'} "
                           f"on defaults and {'there' if after else 'absent'} "
                           f"with the config — the key changed nothing")
    return True, "all four keys change the result, in both directions"


def acc_quiet_about_the_ordinary(ws: Path, tmp: Path) -> tuple[bool, str]:
    """The other half of a rule: what it must not say.

    On a real 17-project workspace `SR-REF-001` produced 92 findings, all false,
    and a rule that wrong is worse than an absent one — it spends the attention
    the next real finding needs. Each case below is one family from that run.
    The last two are the control: with the noise gone, a genuinely broken
    reference and a genuinely broken promise still have to speak.
    """
    work = tmp / "quiet"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    w = build_fixture(work)
    (w / "alpha" / "inbox").mkdir()
    (w / "alpha" / "inbox" / "note.md").write_text("x\n", encoding="utf-8")
    (w / "alpha" / "scratch").mkdir()                      # empty, undeclared
    (w / "alpha" / "CLAUDE.md").write_text(
        "# alpha\n"
        "The queue lives in `queue.md`, intake in `inbox/`.\n"          # own project
        "Reports are named `YYYY-MM-DD-slug.md` under `cases/<id>/`.\n"  # patterns
        "Tokens live in `style-tokens/*.yaml`, see `[текст](../x/y.md)`.\n"
        "Silence stderr with `2>/dev/null`.\n"
        # scratch/ is deliberately not named anywhere in this document.
        "But `docs/handbook.md` really is missing.\n",
        encoding="utf-8")
    ids = rule_ids(_finding_lines(w, CHECKER))
    lines = sorted(l for l in _finding_lines(w, CHECKER) if "SR-REF-001" in l)

    # `inbox/` resolves inside alpha, not beside it; the patterns are patterns;
    # `scratch/` is nobody's promise. Exactly one reference is broken.
    if len(lines) != 1 or "handbook" not in lines[0]:
        return False, f"expected one broken reference, got {lines}"
    if "SR-EMPTY-001" in ids:
        return False, "an empty folder no document declares was reported"
    return True, "one true reference finding, no folder noise"


def acc_quiet_about_wired_things(ws: Path, tmp: Path) -> tuple[bool, str]:
    """A healthy workflow, and a script that really is called, must produce
    nothing. `SR-WIRE-*` is `info` on a subject where the tool is guessing at
    intent, so its silence matters more than its speech: four findings about
    working automation would end anyone's habit of reading the list."""
    cases = [
        ("an ordinary trigger", lambda w: _workflow(
            w, "name: ci\non: [push, pull_request]\njobs:\n  a:\n"
               "    runs-on: ubuntu-latest\n")),
        # Filters are relative to the repository the workflow lives in, which
        # here is alpha and not the scan root. Getting this wrong in the fixture
        # is the same mistake the checker used to make about declared paths.
        ("a filter that does match", lambda w: _workflow(
            w, "name: ci\non:\n  push:\n    paths:\n      - '**/*.md'\n"
               "jobs:\n  a:\n    runs-on: ubuntu-latest\n")),
        ("a reusable workflow", lambda w: _workflow(
            w, "name: lib\non:\n  workflow_call:\njobs:\n  a:\n"
               "    runs-on: ubuntu-latest\n")),
        ("a script a hook really calls", _script_wired_to_a_hook),
    ]
    for label, arrange in cases:
        work = tmp / f"wired-{abs(hash(label))}"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir()
        w = build_fixture(work)
        arrange(w)
        noisy = sorted(i for i in rule_ids(_finding_lines(w, CHECKER))
                       if i.startswith("SR-WIRE"))
        if noisy:
            return False, f"{label}: reported {noisy}"
    return True, f"{len(cases)} wired things, nothing said about any of them"


ACCEPTANCE = [
    ("every config key changes what the scan does", acc_config_keys_do_something),
    ("quiet about automation that is wired", acc_quiet_about_wired_things),
    ("quiet about ordinary structure", acc_quiet_about_the_ordinary),
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
