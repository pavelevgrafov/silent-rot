#!/usr/bin/env python3
"""Silent-rot checker: does the workspace still do what its documents claim?

Every check here exists because the failure it catches was found in a real
workspace on 2026-08-13, after weeks of the whole thing looking healthy. None
of them is clever; they are the checks nobody writes because the answer feels
obvious until you look.

The scanned tree is treated as untrusted data. A plain scan never runs anything
it finds; hooks are inspected statically and counted as inspected, not proven.
Proving a hook still works means running it, and that needs two explicit flags
naming the workspace you own.

Usage:
    python3 liveness.py [ROOT]        # ROOT defaults to the current directory
    python3 liveness.py ROOT --execute-hooks --trusted-root ROOT

Exit code is 0 whether or not problems are found — this is a report, not a
gate. Wire it into a hook or a weekly job and read the output.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# Several patterns below carry both English and Russian alternatives on
# purpose: instruction files and queue tables are often written in the author's
# own language while the code around them is English, and a status word the
# checker cannot match is the exact defect this repo is about. Add your own
# language's words to those alternations.
INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md", "README.md")
STATE_GLOB = "*last*"          # files hooks use to remember what they reported
STALE_DAYS = 14
RECEIPT_NAME = ".mutation-receipt.json"
RECEIPT_MAX_AGE_DAYS = 30

# A child process gets nothing but what it needs to run. Inheriting the parent
# environment hands every API token in the shell to somebody else's script.
SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "NO_COLOR")

# Real hook commands almost always carry a tail that keeps a broken hook from
# breaking the session. These exact forms change nothing about what executes,
# so they are removed before parsing. Everything else that looks like shell is
# refused rather than interpreted — guessing shell semantics is how a checker
# starts executing what it meant to inspect.
NOOP_TAIL = re.compile(r"\s*(?:[12]?>\s*/dev/null|\|\|\s*true|;\s*true|;\s*exit\s+0)")
SHELL_META = re.compile(r"[|&;<>$`*?~\[\]{}()!\\]")
INTERPRETERS = ("bash", "sh", "python3", "python")

problems: list[str] = []
checks = 0
coverage = {"found": 0, "inspected": 0, "executed": 0, "unsupported": 0, "outside": 0}


def check(_label: str) -> None:
    """Count every check actually performed. A check that never runs cannot
    find anything, and the count is the only way to notice that it didn't."""
    global checks
    checks += 1


def projects(root: Path) -> list[Path]:
    # pathlib's glob matches dotted names, unlike the shell — without this
    # filter `.git` and `.claude` count as projects and every reference
    # resolves from the wrong root.
    out = [d for d in root.glob("*") if d.is_dir() and not d.name.startswith(".")]
    return out + [g / d.name for g in out for d in g.glob("*")
                  if d.is_dir() and not d.name.startswith(".")]


# 1. Settings files parse. A malformed one disables every setting it holds —
#    hooks included — and nothing announces it.
def check_settings_valid(root: Path) -> list[Path]:
    found = []
    for p in root.glob("**/.claude/settings*.json"):
        if "node_modules" in p.parts:
            continue
        found.append(p)
        check("settings json")
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"BROKEN JSON: {rel(p, root)} — {e}")
    return found


# 2. Every hook a settings file names exists, is statically understandable, and
#    — only when explicitly asked — actually exits cleanly. Declaring a hook is
#    not running it: seven were wired into session startup here and had never
#    once executed. But reading a settings file is not permission to run what it
#    names: until v0.1.1 this function executed any `.sh`/`.py` path it found in
#    the scanned tree, including paths outside that tree.
def classify_command(command: str, base: Path, root: Path) -> tuple[str, list[str], Path | None, str]:
    """Decide what a hook command is, without a shell and without running it.

    Returns (kind, argv, resolved_path, note) where kind is one of
    supported | unsupported | missing | outside.
    """
    stripped = NOOP_TAIL.sub(" ", command).strip()
    if not stripped:
        return "unsupported", [], None, "nothing left after the no-op suffixes"
    if SHELL_META.search(stripped):
        return "unsupported", [], None, "shell operator, redirection or expansion"
    try:
        argv = shlex.split(stripped)
    except ValueError as e:
        return "unsupported", [], None, f"cannot be parsed: {e}"
    if not argv:
        return "unsupported", [], None, "empty command"

    if argv[0] in INTERPRETERS:
        if len(argv) < 2:
            return "unsupported", [], None, f"interpreter without a script: {argv[0]}"
        target, rest, prefix = argv[1], argv[2:], [argv[0]]
    else:
        target, rest, prefix = argv[0], argv[1:], []

    resolved = (base / target).resolve() if not os.path.isabs(target) else Path(target).resolve()
    if not resolved.is_file():
        return "missing", [], resolved, "declared, not on disk"
    if root not in resolved.parents:
        # Outside the scanned tree the file may exist and be perfectly healthy;
        # what cannot be claimed is that this scan checked it.
        return "outside", [], resolved, "lives outside the scanned root"
    return "supported", prefix + [str(resolved)] + rest, resolved, ""


def check_hooks(root: Path, settings: list[Path], execute: bool) -> None:
    before = {f: f.read_bytes() for d in root.glob("**/.claude")
              for f in d.glob(STATE_GLOB) if f.is_file()} if execute else {}
    seen: set[str] = set()
    for p in settings:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for event, groups in (data.get("hooks") or {}).items():
            for group in groups:
                for hook in group.get("hooks", []):
                    command = hook.get("command", "")
                    if not command.strip() or command in seen:
                        continue
                    seen.add(command)
                    check("hook")
                    coverage["found"] += 1
                    kind, argv, resolved, note = classify_command(
                        command, p.parent, root)

                    if kind == "missing":
                        problems.append(
                            f"hook does not exist: {resolved} (event {event}, "
                            f"declared in {rel(p, root)})")
                        continue
                    if kind == "unsupported":
                        coverage["unsupported"] += 1
                        problems.append(
                            f"hook command not statically supported, not executed "
                            f"({note}): {command.strip()[:100]} (event {event})")
                        continue
                    if kind == "outside":
                        coverage["outside"] += 1
                        if execute:
                            problems.append(
                                f"hook outside the trusted root, not executed: "
                                f"{resolved} (event {event})")
                        continue

                    coverage["inspected"] += 1
                    if not execute:
                        continue
                    coverage["executed"] += 1
                    try:
                        # Hooks are fed JSON on stdin; with no stdin a reader
                        # blocks forever and the check misreports it as a hang.
                        r = subprocess.run(
                            argv, input="{}", capture_output=True, text=True,
                            timeout=25, cwd=str(p.parent), shell=False,
                            env={k: os.environ[k] for k in SAFE_ENV_KEYS
                                 if k in os.environ})
                        if r.returncode != 0:
                            problems.append(
                                f"hook fails (code {r.returncode}): {resolved} — "
                                f"{(r.stderr or '').strip()[:120]}")
                    except subprocess.TimeoutExpired:
                        problems.append(f"hook hangs (>25s): {resolved}")

    # Running a hook that reports only on change makes it record what it just
    # reported, so the audit itself silences the next real notification. Put
    # every state file back exactly as the hooks found it.
    for f, content in before.items():
        if f.is_file() and f.read_bytes() != content:
            f.write_bytes(content)


# 3. Paths quoted in instruction files still resolve. Three resolution rules,
#    each learned by getting it wrong: resolve inside the file's own project
#    (not a neighbour's), treat a folder the document itself marks as planned
#    as honest, and accept a path qualified by prose on the same line.
def check_paths(root: Path) -> None:
    pat = re.compile(r"`([^`\s]+?/[^`\s]*|[^`\s]+?\.(?:md|sh|py|json|ya?ml))`")
    planned = re.compile(r"not created|planned|future|later|не создан|планир")
    names = {p.name for p in projects(root)}
    files = [f for pr in [root] + projects(root)
             for n in INSTRUCTION_FILES if (f := pr / n).is_file()]
    for f in files:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        # A folder marked planned on any one line is planned everywhere in that
        # document — the same folders get enumerated again in later sections.
        skip = {raw for l in lines if planned.search(l.lower()) for raw in pat.findall(l)}
        seen: set[str] = set()
        for line in lines:
            if planned.search(line.lower()):
                continue
            for raw in sorted(set(pat.findall(line)) - skip):
                if raw in seen or raw.startswith(("http", "<", "{")):
                    continue
                seen.add(raw)
                if raw.count("/") == 1 and "." not in raw and not raw.endswith("/"):
                    continue  # Owner/Repo slug, not a path on disk
                check("declared path")
                if raw.startswith(("~", "/")):
                    ok = Path(os.path.expanduser(raw)).exists()
                elif "/" not in raw:
                    # Documents routinely name a file by its bare name and leave
                    # the folder implicit ("run test_liveness.py"). Only a name
                    # that exists nowhere in the tree is a broken reference.
                    ok = (f.parent / raw).exists() or any(
                        p.name == raw for p in root.rglob(raw))
                elif raw.startswith(".."):
                    ok = (f.parent / raw).exists()
                else:
                    base = root if raw.split("/")[0] in names else f.parent
                    ok = (base / raw).exists()
                if not ok:
                    ok = any((root / n / raw).exists() for n in names if n in line)
                if not ok:
                    problems.append(f"declared but missing: {raw} (in {rel(f, root)})")


# 4. A folder a document presents as part of the structure, that exists and is
#    empty, is a promise nobody kept.
def check_declared_but_empty(root: Path) -> None:
    for pr in projects(root):
        for d in pr.glob("*"):
            if not d.is_dir() or d.name.startswith("."):
                continue
            check("folder not empty")
            if not any(d.rglob("*")):
                problems.append(f"declared folder is empty: {rel(d, root)}")


# 5. Rows in a queue that have sat unprocessed long enough that "parked on
#    purpose" and "forgotten" stop being distinguishable.
def check_stale_rows(root: Path) -> None:
    pending = re.compile(r"unprocessed|pending|не (обработано|разобрано)", re.I)
    datep = re.compile(r"(\d{4}-\d{2}-\d{2})")
    for f in root.rglob("*.md"):
        if "node_modules" in f.parts or ".git" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        rows = [l for l in text.splitlines() if l.startswith("|") and pending.search(l)]
        if not rows:
            continue
        check("stale rows")
        stale = []
        for l in rows:
            m = datep.search(l)
            if not m:
                continue
            age = (date.today() - date.fromisoformat(m.group(1))).days
            if age >= STALE_DAYS:
                stale.append(age)
        if stale:
            problems.append(
                f"{rel(f, root)}: {len(stale)} row(s) unprocessed for {STALE_DAYS}+ days "
                f"(oldest {max(stale)})")


# 6. A number written into a document that the reader could count. Reported as
#    a candidate, never as a verdict — the point is to delete the sentence, not
#    to correct the number.
def check_manual_counters(root: Path) -> None:
    pat = re.compile(r"\b(\d{1,4})\s+(projects?|repos?|repositories|files?|"
                     r"skills?|folders?|проект\w*|репозитор\w*|файл\w*|папк\w*)",
                     re.I)
    for pr in [root] + projects(root):
        for n in INSTRUCTION_FILES:
            f = pr / n
            if not f.is_file():
                continue
            check("manual counter")
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                for m in pat.finditer(line):
                    # A count inside quotation marks is being discussed, not
                    # asserted — usually an example of somebody else's stale
                    # number. Reporting it trains the reader to skim the list.
                    before, after = line[:m.start()], line[m.end():]
                    if any(q in before for q in '"«“') and any(q in after for q in '"»”'):
                        continue
                    problems.append(
                        f"hand-written count to verify: \"{m.group(0)}\" in {rel(f, root)}")


# 7. This checker's own claim to work. "N checks, no problems" proves nothing
#    on its own; test_liveness.py breaks things on purpose and leaves a receipt
#    here when the checker caught all of them.
def check_mutation_receipt() -> None:
    receipt = Path(__file__).resolve().parent / RECEIPT_NAME
    check("mutation receipt")
    if not receipt.is_file():
        problems.append(f"mutation test has never passed: no {RECEIPT_NAME} — "
                        "run test_liveness.py")
        return
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
        when = datetime.strptime(data["date"], "%Y-%m-%d").date()
    except Exception as e:
        problems.append(f"mutation receipt unreadable: {e}")
        return
    if data.get("passed") != data.get("mutations"):
        problems.append(f"mutation test incomplete: {data.get('passed')}/{data.get('mutations')}")
    elif (date.today() - when).days > RECEIPT_MAX_AGE_DAYS:
        problems.append(f"mutation test last passed {(date.today() - when).days} days ago "
                        f"(threshold {RECEIPT_MAX_AGE_DAYS})")


def rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def hook_summary(execute: bool) -> str:
    """Never let a quiet report pass for an end-to-end check. What was only read
    and what was actually run have to be visible in the same line."""
    c = coverage
    if not c["found"]:
        return "hooks: none declared"
    tail = (f", {c['unsupported']} unsupported" if c["unsupported"] else "") + \
           (f", {c['outside']} outside the root" if c["outside"] else "")
    if execute:
        return (f"hooks: {c['found']} declared, {c['executed']} executed{tail}")
    return (f"hooks: {c['found']} declared, {c['inspected']} inspected statically, "
            f"0 executed{tail} — static inspection is not proof that they run")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Report mechanisms that look alive and are not. "
                    "The scanned tree is treated as untrusted data.")
    ap.add_argument("root", nargs="?", default=".", help="directory to scan")
    ap.add_argument("--execute-hooks", action="store_true",
                    help="run the hooks declared inside ROOT; requires --trusted-root")
    ap.add_argument("--trusted-root", metavar="ABS_PATH",
                    help="absolute path that must equal ROOT; your explicit statement "
                         "that you own the code in it")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2

    execute = args.execute_hooks
    if execute:
        # Two flags that must agree, so that no single forgotten flag, alias or
        # copied command line can turn a scan into an execution.
        if not args.trusted_root:
            print("--execute-hooks requires --trusted-root ABS_PATH naming the same root")
            return 2
        if Path(args.trusted_root).resolve() != root:
            print(f"--trusted-root does not match the scan root:\n"
                  f"  scan root:    {root}\n"
                  f"  trusted root: {Path(args.trusted_root).resolve()}")
            return 2

    settings = check_settings_valid(root)
    check_hooks(root, settings, execute)
    check_paths(root)
    check_declared_but_empty(root)
    check_stale_rows(root)
    check_manual_counters(root)
    check_mutation_receipt()

    print(f"checks run: {checks}")
    print(hook_summary(execute))
    if not problems:
        print("no problems found")
        # Worth distrusting: see README, "A clean report is a claim, not proof".
        return 0
    print(f"problems: {len(problems)}\n")
    for p in problems:
        print(f"  — {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
