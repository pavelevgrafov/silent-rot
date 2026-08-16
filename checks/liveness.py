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
import functools
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

VERSION = "0.2.0"

# The four things that actually differ between workspaces, and nothing else.
# A queue whose rows say `todo` is invisible to a checker looking for
# `unprocessed`, and the run comes back clean — this project's own failure mode,
# performed by this project. The defaults carry Russian alternatives on purpose:
# instruction files and queue tables are often written in the author's language
# while the code around them is English.
DEFAULTS = {
    "instruction_files": ["CLAUDE.md", "AGENTS.md", "README.md"],
    "pending_statuses": ["unprocessed", "pending", "не обработано", "не разобрано"],
    "stale_days": 14,
    "exclude_globs": ["**/.git/**", "**/node_modules/**", "**/.venv/**"],
}
CONFIG_NAME = ".silent-rot.toml"

STATE_GLOB = "*last*"          # files hooks use to remember what they reported
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

# Every rule this checker can emit, with the severity that belongs to the rule
# rather than to the call site. The id is the stable name — what a test asserts,
# what a reader greps for, what a future suppression list would name. The
# message beside it is prose and may be rewritten at any time.
#
# `warning` is a fact the checker established and a reader can act on;
# `info` is a fact whose significance only a human can settle. Neither is a
# verdict: the tool cannot know that a stale row was forgotten rather than
# parked on purpose.
RULES = {
    "SR-SETTINGS-001": "warning",   # settings file does not parse
    "SR-HOOK-001": "warning",       # hook declared, file not on disk
    "SR-HOOK-002": "info",          # hook command not statically understandable
    "SR-HOOK-003": "info",          # hook resolves outside the scanned root
    "SR-HOOK-004": "warning",       # executed hook exits non-zero
    "SR-HOOK-005": "warning",       # executed hook hangs
    "SR-REF-001": "warning",        # path declared in a document does not resolve
    "SR-EMPTY-001": "info",         # declared folder exists and is empty
    "SR-QUEUE-001": "info",         # queue rows unprocessed past the threshold
    "SR-COUNT-001": "info",         # hand-written count worth verifying
    "SR-SELFTEST-001": "warning",   # mutation test has never passed here
    "SR-SELFTEST-002": "warning",   # receipt unreadable
    "SR-SELFTEST-003": "warning",   # mutation test passed only in part
    "SR-SELFTEST-004": "warning",   # receipt older than the threshold
    "SR-SELFTEST-005": "warning",   # receipt attests to different code
    "SR-CONFIG-001": "warning",     # config file present and unusable
    "SR-CONFIG-002": "warning",     # a key in the config does nothing
}

# The files a passing mutation run actually attests to. Sorted and named, so
# that swapping two files or renaming one changes the digest.
SELF_FILES = ("liveness.py", "test_liveness.py")


def self_hash(base: Path | None = None) -> str:
    """A fingerprint of the code the mutation suite ran against.

    A receipt that records only a date attests to a Tuesday. Edit the checker
    afterwards and the receipt still looks fresh, which is this project's own
    failure mode: apparatus that keeps signalling health after the thing it
    watched has changed underneath it.

    When configuration moves out of this file, the resolved profile has to join
    this digest — a suite that passed under one status vocabulary proves nothing
    about a scan run under another.
    """
    base = base or Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in sorted(SELF_FILES):
        f = base / name
        h.update(name.encode())
        h.update(f.read_bytes() if f.is_file() else b"<missing>")
    return h.hexdigest()


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    message: str
    path: str = ""
    line: int | None = None

    def render(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        where = f" {loc}" if loc else ""
        return f"[{self.severity}] {self.rule}{where} — {self.message}"


# The classes of object this checker knows how to look at, and what each one
# counts. A single "checks run: 41" cannot say that an entire class was skipped,
# and a class nobody looked at reads exactly like a class that came back clean —
# which is the defect this repo is named after, committed by the tool itself.
CLASSES = {
    "settings": "settings files",
    "hooks": "hook commands",
    "paths": "paths declared in documents",
    "folders": "folders inside projects",
    "queues": "pending queue rows",
    "counters": "instruction files scanned for counts",
    "selftest": "this checker's own mutation receipt",
}

findings: list[Finding] = []
coverage = {c: {"discovered": 0, "checked": 0, "skipped": 0, "skipped_reasons": {}}
            for c in CLASSES}
# Hooks carry their own breakdown: "checked" for a hook means read, and reading
# a hook proves nothing about whether it runs.
coverage["hooks"] |= {"inspected_statically": 0, "executed": 0,
                      "unsupported": 0, "outside": 0, "missing": 0}


def add(rule: str, message: str, path: str = "", line: int | None = None) -> None:
    # KeyError on an unregistered id, deliberately: a rule that emits an id
    # nothing declares is a rule no test can assert and no reader can look up.
    findings.append(Finding(rule, RULES[rule], message, path, line))


def seen(cls: str, n: int = 1) -> None:
    coverage[cls]["discovered"] += n


def load_config(root: Path) -> tuple[dict, list[str]]:
    """Read `.silent-rot.toml` from the scan root, if it is there at all.

    Config is data, never permission. There is no key that turns on hook
    execution, and there is no code path from this function to `--execute-hooks`
    — a file inside the tree being scanned must never be able to widen what the
    scan is allowed to do.

    A key that does nothing is reported rather than dropped: `pendingstatuses`
    silently ignored leaves the queue unwatched while its owner believes it is
    configured, which is the failure this whole repo is about.
    """
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    f = root / CONFIG_NAME
    if not f.is_file():
        return cfg, []
    try:
        raw = tomllib.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        add("SR-CONFIG-001", f"config unusable, scanning with defaults: {e}",
            CONFIG_NAME)
        return cfg, []

    applied = []
    for key, value in raw.items():
        if key not in DEFAULTS:
            add("SR-CONFIG-002", f"unknown key ignored: {key}", CONFIG_NAME)
            continue
        want = type(DEFAULTS[key])
        if type(value) is not want:
            add("SR-CONFIG-002",
                f"{key} ignored: expected {want.__name__}, got {type(value).__name__}",
                CONFIG_NAME)
            continue
        if want is list and not all(isinstance(x, str) for x in value):
            add("SR-CONFIG-002", f"{key} ignored: every entry must be a string",
                CONFIG_NAME)
            continue
        if key in ("instruction_files", "pending_statuses") and not value:
            # An empty list would build a pattern that matches nothing (or, for
            # a regex, everything). Either way the check stops meaning what its
            # name says while still counting as configured.
            add("SR-CONFIG-002",
                f"{key} ignored: an empty list turns the check off silently",
                CONFIG_NAME)
            continue
        if key == "stale_days" and (value < 0 or isinstance(value, bool)):
            add("SR-CONFIG-002", f"{key} ignored: not a number of days: {value!r}",
                CONFIG_NAME)
            continue
        if want is list and (bad := [x for x in value if escapes_root(x)]):
            # The config is inside the tree being scanned, so it is written by
            # whoever wrote that tree. It may narrow the scan, never point it
            # somewhere else.
            add("SR-CONFIG-002",
                f"{key} ignored: entries may not leave the scan root: {bad}",
                CONFIG_NAME)
            continue
        cfg[key] = value
        applied.append(key)
    return cfg, applied


def escapes_root(entry: str) -> bool:
    return (entry.startswith(("/", "~"))
            or ".." in Path(entry).parts
            or os.path.isabs(entry))


@functools.lru_cache(maxsize=None)
def glob_re(pattern: str) -> re.Pattern:
    """`fnmatch` cannot tell `*` from `**`, and the difference is the whole
    point of an exclude list: `*` stays inside one path segment, `**` crosses."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def excluded(p: Path, root: Path, cfg: dict) -> bool:
    """True when the path, or any directory above it, is excluded."""
    try:
        rel_ = p.relative_to(root).as_posix()
    except ValueError:
        return False
    parts = rel_.split("/")
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    for g in cfg["exclude_globs"]:
        # "node_modules/**" is written to exclude the directory as well as its
        # contents; matching only the contents would leave the folder itself
        # counted as a project.
        for pattern in (g, g[:-3]) if g.endswith("/**") else (g,):
            if any(glob_re(pattern).match(c) for c in candidates):
                return True
    return False


def check(cls: str, n: int = 1) -> None:
    """Count every check actually performed. A check that never runs cannot
    find anything, and the count is the only way to notice that it didn't."""
    coverage[cls]["checked"] += n


def skip(cls: str, reason: str, n: int = 1) -> None:
    """An object discovered and then not checked. Recorded with its reason,
    because "skipped 3" that nobody can explain is not better than silence."""
    coverage[cls]["skipped"] += n
    coverage[cls]["skipped_reasons"][reason] = \
        coverage[cls]["skipped_reasons"].get(reason, 0) + n


def read_text(f: Path, cls: str) -> str | None:
    """A file that could not be opened is a file that was not checked.

    The failure counts as one discovered-and-skipped unit of its class, even
    where the class otherwise counts spans or rows: `discovered = checked +
    skipped` has to hold, or the numbers cannot be read at a glance."""
    try:
        return f.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        seen(cls)
        skip(cls, f"unreadable: {type(e).__name__}")
        return None


def projects(root: Path, cfg: dict) -> list[Path]:
    # pathlib's glob matches dotted names, unlike the shell — without this
    # filter `.git` and `.claude` count as projects and every reference
    # resolves from the wrong root.
    def ok(d: Path) -> bool:
        return d.is_dir() and not d.name.startswith(".") and not excluded(d, root, cfg)

    out = [d for d in root.glob("*") if ok(d)]
    return out + [g / d.name for g in out for d in g.glob("*") if ok(d)]


# 1. Settings files parse. A malformed one disables every setting it holds —
#    hooks included — and nothing announces it.
def check_settings_valid(root: Path, cfg: dict) -> list[Path]:
    found = []
    for p in root.glob("**/.claude/settings*.json"):
        if excluded(p, root, cfg):
            continue
        text = read_text(p, "settings")
        if text is None:
            # Not added to `found`: the hook pass cannot read it either, and
            # letting it through would count its hooks as inspected.
            continue
        found.append(p)
        seen("settings")
        check("settings")
        try:
            json.loads(text)
        except ValueError as e:
            add("SR-SETTINGS-001", f"settings file is not valid JSON: {e}",
                rel(p, root), getattr(e, "lineno", None))
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
    declared: set[str] = set()
    for p in settings:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # already reported by check_settings_valid
        for event, groups in (data.get("hooks") or {}).items():
            for group in groups:
                for hook in group.get("hooks", []):
                    command = hook.get("command", "")
                    if not command.strip() or command in declared:
                        continue
                    declared.add(command)
                    seen("hooks")
                    check("hooks")
                    kind, argv, resolved, note = classify_command(
                        command, p.parent, root)

                    if kind == "missing":
                        coverage["hooks"]["missing"] += 1
                        add("SR-HOOK-001",
                            f"hook does not exist: {resolved} (event {event})",
                            rel(p, root))
                        continue
                    if kind == "unsupported":
                        coverage["hooks"]["unsupported"] += 1
                        add("SR-HOOK-002",
                            f"hook command not statically supported, not executed "
                            f"({note}): {command.strip()[:100]} (event {event})",
                            rel(p, root))
                        continue
                    if kind == "outside":
                        coverage["hooks"]["outside"] += 1
                        if execute:
                            add("SR-HOOK-003",
                                f"hook outside the trusted root, not executed: "
                                f"{resolved} (event {event})",
                                rel(p, root))
                        continue

                    coverage["hooks"]["inspected_statically"] += 1
                    if not execute:
                        continue
                    coverage["hooks"]["executed"] += 1
                    try:
                        # Hooks are fed JSON on stdin; with no stdin a reader
                        # blocks forever and the check misreports it as a hang.
                        r = subprocess.run(
                            argv, input="{}", capture_output=True, text=True,
                            timeout=25, cwd=str(p.parent), shell=False,
                            env={k: os.environ[k] for k in SAFE_ENV_KEYS
                                 if k in os.environ})
                        if r.returncode != 0:
                            add("SR-HOOK-004",
                                f"hook fails (code {r.returncode}): "
                                f"{(r.stderr or '').strip()[:120]}",
                                rel(resolved, root))
                    except subprocess.TimeoutExpired:
                        add("SR-HOOK-005", "hook hangs (>25s)", rel(resolved, root))

    # Running a hook that reports only on change makes it record what it just
    # reported, so the audit itself silences the next real notification. Put
    # every state file back exactly as the hooks found it.
    for f, content in before.items():
        if f.is_file() and f.read_bytes() != content:
            f.write_bytes(content)


# A span that describes how names are formed rather than naming a file. These
# were 40-odd of the 92 false alarms this rule produced on a real workspace:
# `YYYY-MM-DD-slug.md`, `cases/<id>-<slug>/`, `prompts/{prompt_id}/v{N}.md`,
# `style-tokens/*.yaml`, `kebab-case.md`. Checking them for existence is a
# category error, and the reader who reads three of them stops reading the list.
PATTERN_SPAN = re.compile(
    r"[<>{}*?()|$\\]"                       # placeholder or shell metacharacter
    r"|\]\("                                # a markdown link, not a path
    r"|\bYYYY\b|\bMM\b|\bDD\b"              # date template
    r"|\bslug\b|\bkebab-case\b|\bsnake_case\b|\bcamelCase\b"
    r"|название", re.I)


# 3. Paths quoted in instruction files still resolve. The resolution rules were
#    each learned by getting them wrong — see the comment on `base` below, which
#    cost this checker 92 false findings in a single run.
def check_paths(root: Path, cfg: dict) -> set[Path]:
    pat = re.compile(r"`([^`\s]+?/[^`\s]*|[^`\s]+?\.(?:md|sh|py|json|ya?ml))`")
    planned = re.compile(r"not created|planned|future|later|не создан|планир")
    # Only real top-level projects may pull a reference back to the root. The
    # previous version used every first- and second-level directory, so ordinary
    # folder names — inbox, reports, tasks, audit — counted as project names.
    tops = {d.name for d in root.glob("*")
            if d.is_dir() and not d.name.startswith(".") and not excluded(d, root, cfg)}
    declared_dirs: set[Path] = set()
    files = [f for pr in [root] + projects(root, cfg)
             for n in cfg["instruction_files"] if (f := pr / n).is_file()]
    for f in files:
        text = read_text(f, "paths")
        if text is None:
            continue
        # The project this document belongs to, which is a second legitimate
        # base for a relative reference. For a project's own CLAUDE.md this is
        # the directory the file is already in.
        parts = Path(rel(f, root)).parts
        owner = root / parts[0] if len(parts) > 1 else root
        lines = text.splitlines()
        # A folder marked planned on any one line is planned everywhere in that
        # document — the same folders get enumerated again in later sections.
        deferred = {raw for l in lines if planned.search(l.lower())
                    for raw in pat.findall(l)}
        already: set[str] = set()
        for lineno, line in enumerate(lines, 1):
            for raw in sorted(set(pat.findall(line))):
                if raw in already:
                    continue
                already.add(raw)
                seen("paths")
                if raw in deferred or planned.search(line.lower()):
                    skip("paths", "document marks it as planned")
                    continue
                if raw.startswith("http"):
                    skip("paths", "not a path on disk")
                    continue
                if PATTERN_SPAN.search(raw):
                    skip("paths", "naming pattern, not a path")
                    continue
                if raw.count("/") == 1 and "." not in raw and not raw.endswith("/"):
                    skip("paths", "Owner/Repo slug, not a path on disk")
                    continue
                if "/" not in raw and not raw.startswith("~"):
                    # A bare filename in prose is not a structural reference.
                    # `inbox.md` in one project's document means the file each
                    # *other* project keeps; `CLAUDE.md` in a sentence about
                    # what plugins may ship names a convention, not a file here.
                    # Measured on a 17-project workspace: bare names produced
                    # ten findings and not one of them was true. A reference to
                    # structure carries a separator, and those are still checked.
                    skip("paths", "filename in prose, not a structural reference")
                    continue
                check("paths")
                if raw.startswith(("~", "/")):
                    target = Path(os.path.expanduser(raw))
                    ok = target.exists()
                else:
                    # The document's own directory, first and by default. The
                    # previous order tried the workspace root first whenever the
                    # leading segment matched any known folder name, so `inbox/`
                    # written in Research/CLAUDE.md was looked for beside
                    # Research rather than inside it — 92 of 95 findings on a
                    # real workspace, every one of them false.
                    target = f.parent / raw
                    ok = target.exists()
                    if not ok and owner != f.parent:
                        # A README in a subdirectory routinely writes paths from
                        # the root of its own project: `docs/04.md` in
                        # Bannerzila/prompts/README.md means Bannerzila/docs.
                        target = owner / raw
                        ok = target.exists()
                    first = raw.split("/")[0]
                    if not ok and first in tops:
                        target = root / raw
                        ok = target.exists()
                    if not ok:
                        # "In `Research`, see `inbox/`" — the project named in
                        # the prose beside the reference, and only that.
                        for n in tops:
                            if n in line and (root / n / raw).exists():
                                target, ok = root / n / raw, True
                                break
                if ok and raw.endswith("/") and target.is_dir():
                    # The document presents this directory as part of the
                    # structure. That declaration is what makes an empty one
                    # worth reporting; see check_declared_but_empty.
                    declared_dirs.add(target.resolve())
                if not ok:
                    add("SR-REF-001", f"declared but missing: {raw}",
                        rel(f, root), lineno)
    return declared_dirs


# 4. A folder a document presents as part of the structure, that exists and is
#    empty, is a promise nobody kept. Only declared folders: the rule used to
#    fire on any empty directory anywhere, which is a report about someone's
#    scratch space, not about a promise. A span ending in `/` in an instruction
#    file is the declaration — the parser already had to find it to check that
#    it resolves. (Asking for a hand-written list of directories instead would
#    invert the tool: the person who remembers to list a directory is not the
#    person who forgets to fill it.)
def check_declared_but_empty(root: Path, cfg: dict, declared: set[Path]) -> None:
    for d in sorted(declared):
        seen("folders")
        check("folders")
        if not any(d.rglob("*")):
            add("SR-EMPTY-001", "declared folder is empty", rel(d, root))


# 5. Rows in a queue that have sat unprocessed long enough that "parked on
#    purpose" and "forgotten" stop being distinguishable.
def check_stale_rows(root: Path, cfg: dict) -> None:
    pending = re.compile("|".join(re.escape(s) for s in cfg["pending_statuses"]), re.I)
    datep = re.compile(r"(\d{4}-\d{2}-\d{2})")
    stale_days = cfg["stale_days"]
    for f in root.rglob("*.md"):
        if excluded(f, root, cfg):
            continue
        text = read_text(f, "queues")
        if text is None:
            continue
        rows = [(n, l) for n, l in enumerate(text.splitlines(), 1)
                if l.startswith("|") and pending.search(l)]
        if not rows:
            continue
        stale = []
        for n, l in rows:
            seen("queues")
            m = datep.search(l)
            if not m:
                # No date, no age: the row is pending and this checker cannot
                # say for how long. Counted as unchecked, not as fine.
                skip("queues", "pending row carries no date")
                continue
            try:
                when = date.fromisoformat(m.group(1))
            except ValueError:
                # 2026-13-45 matches the shape and is not a date. Crashing here
                # would take the whole scan down over one typo in one row.
                skip("queues", "row date does not parse")
                continue
            check("queues")
            age = (date.today() - when).days
            if age >= stale_days:
                stale.append((age, n))
        if stale:
            oldest, oldest_line = max(stale)
            # The line of the oldest row, not of the first: the reader opening
            # the file wants the row that has been waiting longest.
            add("SR-QUEUE-001",
                f"{len(stale)} row(s) unprocessed for {stale_days}+ days "
                f"(oldest {oldest})",
                rel(f, root), oldest_line)


# 6. A number written into a document that the reader could count. Reported as
#    a candidate, never as a verdict — the point is to delete the sentence, not
#    to correct the number.
def check_manual_counters(root: Path, cfg: dict) -> None:
    pat = re.compile(r"\b(\d{1,4})\s+(projects?|repos?|repositories|files?|"
                     r"skills?|folders?|проект\w*|репозитор\w*|файл\w*|папк\w*)",
                     re.I)
    for pr in [root] + projects(root, cfg):
        for n in cfg["instruction_files"]:
            f = pr / n
            if not f.is_file():
                continue
            text = read_text(f, "counters")
            if text is None:
                continue
            seen("counters")
            check("counters")
            for lineno, line in enumerate(text.splitlines(), 1):
                for m in pat.finditer(line):
                    # A count inside quotation marks is being discussed, not
                    # asserted — usually an example of somebody else's stale
                    # number. Reporting it trains the reader to skim the list.
                    before, after = line[:m.start()], line[m.end():]
                    if any(q in before for q in '"«“') and any(q in after for q in '"»”'):
                        continue
                    add("SR-COUNT-001",
                        f"hand-written count to verify: \"{m.group(0)}\"",
                        rel(f, root), lineno)


# 7. This checker's own claim to work. "N checks, no problems" proves nothing
#    on its own; test_liveness.py breaks things on purpose and leaves a receipt
#    here when the checker caught all of them.
def check_mutation_receipt() -> None:
    receipt = Path(__file__).resolve().parent / RECEIPT_NAME
    seen("selftest")
    check("selftest")
    where = RECEIPT_NAME
    if not receipt.is_file():
        add("SR-SELFTEST-001", "mutation test has never passed here — "
            "run test_liveness.py", where)
        return
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
        when = datetime.strptime(data["date"], "%Y-%m-%d").date()
    except Exception as e:
        add("SR-SELFTEST-002", f"mutation receipt unreadable: {e}", where)
        return
    if data.get("passed") != data.get("mutations"):
        add("SR-SELFTEST-003",
            f"mutation test incomplete: {data.get('passed')}/{data.get('mutations')}",
            where)
    elif data.get("code_sha256") != self_hash():
        # Checked before the age: a receipt for code that no longer exists is
        # not made better by being recent.
        why = ("the receipt predates this check" if not data.get("code_sha256")
               else "rerun test_liveness.py")
        add("SR-SELFTEST-005",
            f"the mutation test last passed against different code ({why})",
            where)
    elif (date.today() - when).days > RECEIPT_MAX_AGE_DAYS:
        add("SR-SELFTEST-004",
            f"mutation test last passed {(date.today() - when).days} days ago "
            f"(threshold {RECEIPT_MAX_AGE_DAYS})", where)


def rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def hook_summary(execute: bool) -> str:
    """Never let a quiet report pass for an end-to-end check. What was only read
    and what was actually run have to be visible in the same line."""
    c = coverage["hooks"]
    if not c["discovered"]:
        return "hooks: none declared"
    tail = (f", {c['unsupported']} unsupported" if c["unsupported"] else "") + \
           (f", {c['outside']} outside the root" if c["outside"] else "")
    if execute:
        return (f"hooks: {c['discovered']} declared, {c['executed']} executed{tail}")
    return (f"hooks: {c['discovered']} declared, {c['inspected_statically']} "
            f"inspected statically, 0 executed{tail} — static inspection is not "
            f"proof that they run")


def coverage_lines() -> list[str]:
    """One line per class, including the classes that found nothing.

    Omitting an empty class would be the same mistake the tool exists to
    report: a class nobody looked at and a class that came back clean print
    identically once one of them is left out."""
    width = max(len(c) for c in CLASSES)
    out = []
    for cls, what in CLASSES.items():
        c = coverage[cls]
        why = ", ".join(f"{r} {n}" for r, n in sorted(c["skipped_reasons"].items()))
        out.append(f"  {cls.ljust(width)}  {c['discovered']} discovered, "
                   f"{c['checked']} checked, {c['skipped']} skipped"
                   + (f" ({why})" if why else "")
                   + f" — {what}")
    return out


def config_summary(cfg: dict, applied: list[str]) -> str:
    """What this run was actually looking for.

    A scan for `unprocessed` over a queue that says `todo` reports nothing and
    looks identical to a clean one. The vocabulary in force is therefore part of
    the result, not a setting the reader has to go and look up."""
    if not applied:
        return (f"config: defaults ({', '.join(DEFAULTS)}) — "
                f"no {CONFIG_NAME} applied in the scan root")
    return (f"config: {CONFIG_NAME} applied for {', '.join(sorted(applied))}; "
            f"pending statuses in force: {', '.join(cfg['pending_statuses'])}")


def as_json(root: Path, started_at: str, execute: bool,
            cfg: dict, applied: list[str]) -> str:
    by_rule: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
    doc = {
        "tool": {"name": "silent-rot", "version": VERSION},
        "root": str(root),
        "started_at": started_at,
        "summary": {
            "mode": "execute-hooks" if execute else "static",
            # The profile in force, not the file on disk: what the scan looked
            # for is part of what the scan found.
            "config": {"source": CONFIG_NAME if applied else "defaults",
                       "applied": sorted(applied), "profile": cfg},
            "checks": sum(c["checked"] for c in coverage.values()),
            "findings": len(findings),
            "by_severity": dict(sorted(by_severity.items())),
            "by_rule": dict(sorted(by_rule.items())),
        },
        "coverage": coverage,
        "findings": [
            {"rule": f.rule, "severity": f.severity, "path": f.path,
             "line": f.line, "message": f.message}
            for f in findings
        ],
    }
    return json.dumps(doc, indent=2, sort_keys=False, ensure_ascii=False)


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
    ap.add_argument("--format", choices=("text", "json"), default="text",
                    help="json emits one object on stdout and nothing else")
    args = ap.parse_args()

    # In json mode stdout carries the document and nothing but the document, so
    # that a consumer can pipe it straight into a parser. Everything a human
    # would read goes to stderr.
    def diag(msg: str) -> None:
        print(msg, file=sys.stderr if args.format == "json" else sys.stdout)

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    root = Path(args.root).resolve()
    if not root.is_dir():
        diag(f"not a directory: {root}")
        return 2

    execute = args.execute_hooks
    if execute:
        # Two flags that must agree, so that no single forgotten flag, alias or
        # copied command line can turn a scan into an execution.
        if not args.trusted_root:
            diag("--execute-hooks requires --trusted-root ABS_PATH naming the same root")
            return 2
        if Path(args.trusted_root).resolve() != root:
            diag(f"--trusted-root does not match the scan root:\n"
                 f"  scan root:    {root}\n"
                 f"  trusted root: {Path(args.trusted_root).resolve()}")
            return 2

    cfg, applied = load_config(root)
    settings = check_settings_valid(root, cfg)
    check_hooks(root, settings, execute)
    declared_dirs = check_paths(root, cfg)
    check_declared_but_empty(root, cfg, declared_dirs)
    check_stale_rows(root, cfg)
    check_manual_counters(root, cfg)
    check_mutation_receipt()

    if args.format == "json":
        print(as_json(root, started_at, execute, cfg, applied))
        return 0

    print(f"checks run: {sum(c['checked'] for c in coverage.values())}")
    print(config_summary(cfg, applied))
    print(hook_summary(execute))
    print("coverage:")
    for line in coverage_lines():
        print(line)
    if not findings:
        print("no problems found")
        # Worth distrusting: see README, "A clean report is a claim, not proof".
        return 0
    print(f"\nfindings: {len(findings)}\n")
    for f in findings:
        print(f"  {f.render()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
