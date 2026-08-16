# Changelog

## Unreleased

### The rule this project is actually about

The case study leads with a class the checker had no rule for: the check exists, it is documented, and nothing ever calls it. One validator in that audit was described as blocking; it had never run, and the first time it was triggered by hand it found five real violations that had sat for months.

Three decidable forms, all `info`:

- `SR-WIRE-001` — a workflow no event in the repository can start. Only `workflow_dispatch` and `repository_dispatch` count as manual; `workflow_call` deliberately does not, because a reusable workflow is called by another one, and that is wiring rather than silence.
- `SR-WIRE-002` — a `paths:` filter that matches no file that exists. Filters resolve against the repository the workflow lives in, not against the scan root.
- `SR-WIRE-003` — a script a document says runs by itself, named by no workflow, hook or other script. It fires only when the document claims automation in so many words ("runs automatically", "blocks the merge", "on every commit"): a script somebody runs by hand is not a defect, and reporting one would teach the reader to skim the list.

`SR-WIRE-004` is the honest half: a trigger block written in YAML this checker does not parse — anchors, aliases, merge keys, block scalars — is reported as *unread*, never as fine. The parser covers the shapes workflow files actually use and refuses the rest rather than guessing, the same rule the hook command parser has followed since 0.1.1. It also validates what it produced, not only what went in: an anchor on the `on:` line itself first slipped through as an event named `&base`, and the workflow then looked perfectly wired.

Each rule has a mutation, and all four together have a test that asserts silence — an ordinary trigger, a filter that does match, a reusable workflow, and a script a hook really calls. On a rule that guesses at intent, silence is the half that matters.

### The five rules nothing proved

0.2.0 shipped printing its own gap: five of seventeen rules had no mutation, four of them receipt states and one a hook that hangs. A rule with nothing making it speak is not implemented, however carefully it is written — so the suite said so on every run, and this closes it. 22 mutations, 17 of 17 rules.

The receipt cases all run a copy of the checker in a directory of its own. Against the real one the answer would depend on who last ran the suite on this machine rather than on the case. The staleness case needs an otherwise valid digest, or the mismatch rule answers first and the case proves the wrong thing.

The hang case rewrites the 25-second threshold to one second in its copy and gives the hook three seconds to sleep — same branch, same code path, a wait a test suite can afford. The substitution is asserted: without that check, renaming the constant would turn this into a test of a checker that never times out, and it would pass quietly.

## 0.2.0 — 2026-08-16

### The reference rule stopped crying wolf

On a real 17-project workspace this tool produced 95 findings, 92 of them from one rule. It now produces 24, and 20 from that rule — of which 17 are true on inspection. The prediction that "effectively all 92 were false" turned out wrong in a useful way: 62 were false and 30 were real, hidden inside the noise. A rule that wrong is worse than an absent one, because it spends the attention the next real finding needs.

Three causes, all in how a reference was resolved:

- **The base was wrong.** Every first- and second-level directory counted as a project, so ordinary folder names — `inbox`, `reports`, `tasks`, `audit` — were treated as project names and `inbox/` written in `Research/CLAUDE.md` was looked for beside `Research` instead of inside it. A reference now resolves against the document's own directory first, then that document's project root (a nested `README` writing `docs/04.md` means its project's `docs`), and against the workspace root only when the leading segment names a real top-level project.
- **Patterns were checked as paths.** `YYYY-MM-DD-slug.md`, `cases/<id>-<slug>/`, `prompts/{prompt_id}/v{N}.md`, `style-tokens/*.yaml`, markdown links, and one `2>/dev/null` that this repo's own README contributed.
- **Bare filenames.** Dropped from the rule, not merely from their workspace-wide fallback. Measured: ten findings on that workspace, none true. A structural claim carries a separator.

The empty-folder rule fired on any empty directory, including scratch space that no document ever promised. It now fires only on a directory some document presents as part of the structure — a code span ending in `/`, which the reference parser already had to find. (A hand-written list of directories in the config was rejected: the person who remembers to list a directory is not the person who forgets to fill it.)

Both narrowings are defended by tests that assert *silence* — ordinary structure, patterns and undeclared empty folders produce nothing — alongside the mutations that still prove a genuinely broken reference and a genuinely empty promise are caught.

### Four settings, in an optional file

`.silent-rot.toml` in the scan root, absent by default: `instruction_files`, `pending_statuses`, `stale_days`, `exclude_globs`. That is the whole surface. All four were hardcoded, and they are exactly what differs between workspaces — a queue whose rows say `todo` is invisible to a checker looking for `unprocessed`, and the run comes back clean, which is this project's failure mode performed by this project. Every report now prints the vocabulary that was in force, and `--format json` carries the resolved profile in `summary.config`.

A key that does nothing is a finding (`SR-CONFIG-002`), not a silent drop: a misspelled `pendingstatuses`, a string where a list belongs, an empty list that would switch a check off, an entry pointing outside the scan root. A config file that does not parse is `SR-CONFIG-001` and the scan continues on defaults rather than dying.

Config is data, never permission. The file is read out of the tree being scanned, so it is written by whoever wrote that tree; there is no key that enables hook execution, and a mutation asserts that a config declaring `execute_hooks = true` beside a hostile hook changes nothing except adding a finding about the unknown key.

The default exclusions are `**/.git/**`, `**/node_modules/**`, `**/.venv/**` rather than the unanchored form: a nested `node_modules` was already skipped before this change, and anchoring the patterns at the root would have quietly narrowed what gets skipped while looking like a faithful port.

The user's config deliberately stays out of the receipt digest. It is input to a scan, not part of the tool: folding it in would turn the receipt red for everyone who configures anything, which teaches people to ignore it. What a run used is reported instead, every time.

### The receipt expires when the code changes, not when the month does

The mutation receipt recorded a date, a count and a pass count. Edit the checker afterwards and it still read as fresh — an attestation to a Tuesday rather than to a version. That is the fourth class in this repo's own list: apparatus that keeps signalling health after the thing it watched moved.

It now carries a `sha256` over `liveness.py` and `test_liveness.py`, sorted and named. One changed byte in either and the next scan reports `SR-SELFTEST-005`, distinguishing a receipt from other code from a receipt written before this check existed. When configuration moves out of the source file, the resolved profile has to join the digest — a suite that passed under one status vocabulary proves nothing about a scan run under another.

Proved by a mutation of its own, and this one had to break the checker rather than the fixture: it runs a copy of the checker from a directory of its own, gives it a valid receipt, and edits one byte. The run *before* the edit is half the case — a checker that complained about its receipt unconditionally would pass the after-check while proving nothing.

### Coverage per class, and a report a machine can read

`--format json` writes one object on stdout and nothing else; diagnostics go to stderr, so a failed run cannot land in the middle of a parser's input. The document carries `tool`, `root`, `started_at`, `summary`, `coverage` and `findings`.

Coverage is now reported for every class the checker knows — settings files, hooks, declared paths, folders, queue rows, instruction files, its own receipt — as discovered / checked / skipped, with the reason for each skip. 0.1.1 did this for hooks only, on the argument that a hook merely read is not a hook proved to run. The same argument covers the rest: a pending queue row with no date cannot be aged, a file that will not open cannot be checked, and until now both were silently absent from the count. A class that found nothing still prints its line.

Three acceptance checks now gate the receipt alongside the mutations: the JSON and text renderings must report the same numbers, JSON mode must keep stdout parseable (verified by making a run fail on purpose), and the report shape is compared against a golden file with the clock and the temporary root masked. Each was confirmed to fail when the property it watches is broken.

Two crashes found while adding the counters: a queue row dated `2026-13-45` matched the date shape and took down the whole scan, and an unreadable file did the same. Both are now skips with a reason.

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
