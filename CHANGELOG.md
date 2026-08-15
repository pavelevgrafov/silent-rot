# Changelog

## Unreleased

### Findings have stable ids, and the self-test asserts them

Every finding now renders as `[severity] SR-AREA-NNN path:line — message`. The id is the part that does not move; the message is prose and will be rewritten.

This started as a defect in the test harness rather than in the checker. Each mutation asserted a substring of the message it expected — `"declared but missing"`, `"BROKEN JSON"` — so the suite was testing the wording. Rewrite a sentence and the mutation stops being caught, the run stays green, and nothing on screen says a rule went unproven. Mutations now assert the rule id.

The harness also prints which rules no mutation breaks (`SR-HOOK-005` and the four `SR-SELFTEST-*` at the time of writing). Same rule as the hooks line: an unchecked class that goes unmentioned reads as a checked one.

Severity belongs to the rule, not to the call site: `warning` for a fact the checker established, `info` for a fact whose significance only a reader can settle. Neither is a verdict.

## 0.1.1 — 2026-08-15

### Security — the scanner executed code from the tree it was scanning

`checks/liveness.py` proved a hook was alive by running it. It extracted any `.sh` or `.py` path from a hook command in `.claude/settings*.json` and executed it, with no check that the path was inside the scanned root, and no flag to opt out.

Pointing the tool at a repository you had just cloned ran that repository's hooks as you. Reproduced against `d869f30`: a hook wrote a file outside the scan root and the report ended with `no problems found`.

**If you ran any earlier revision against code you did not write, treat it as having executed that code.**

Fixed:

- A plain scan performs static inspection only. There is no code path from `scan` to `subprocess`.
- Execution requires `--execute-hooks` **and** `--trusted-root ABS_PATH`, which must resolve to the scan root. Two flags that have to agree, so one forgotten flag or one copied command line cannot turn a scan into an execution.
- Hook paths resolving outside the scan root are never executed, in any mode, and are counted separately.
- Commands are parsed with `shlex`, never a shell. A pipe, redirection, command substitution or variable makes the command `unsupported` — reported, not guessed at. A trailing `2>/dev/null`, `|| true`, `; true` or `; exit 0` is recognised as a no-op and does not disqualify a command.
- Executed hooks get a minimal environment (`PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, `NO_COLOR`), so no token from the calling shell reaches them. `cwd` is the directory of the settings file that declared the hook.

### Reports no longer let silence pass for a check

Every run now prints what was inspected against what was executed:

```text
hooks: 3 declared, 3 inspected statically, 0 executed — static inspection is not proof that they run
```

This is the project's own rule applied to the tool: a quiet report is a claim, and a scan that only read a hook has not proved anything about it.

### Mutation harness

Twelve cases, up from eight, with four new ones covering this release. The four fail against `d869f30` and pass against this revision — the fix is demonstrated by the harness, not asserted in a commit message:

- a hostile hook is not run by a plain scan;
- a plain scan states that nothing was executed;
- a shell operator in a hook command is refused rather than interpreted;
- a hook outside the scanned root is not executed in trusted mode.

Cases that need a hook to actually run are now compared against a trusted-mode baseline of their own; scoring them against a static baseline would have credited the difference between the two modes as a caught mutation.
