# Silent rot

**A mechanism that never runs looks exactly like a mechanism that runs and finds nothing.**

This repo is a method, a skill file, and a small checker for finding the second kind — the automation, validator, reminder, or counter that has quietly stopped mattering while continuing to look healthy.

It comes from one audit of a live 27-project workspace driven by an AI coding agent. The workspace had looked fine for two months. The audit found about thirty defects. None had ever announced itself, and **six of them were inside the audit tooling itself** — checks that ran, counted, and were structurally incapable of finding their target.

[The full case study is here.](case-study.md)

## What it finds

Five classes, ordered by how well they hide:

1. **Written but never wired** — a validator with no trigger, a workflow no event calls. One in the source audit was documented as blocking; it had never run. When finally triggered it found five real violations that had sat for months.
2. **Wired but looking at the wrong thing** — a monitor searching for a status word the data has never used; a regex that cannot match its own format; a scanner that stops at a symlink.
3. **Hand-written counts** — "all 15 repositories" against 16 rows and 27 directories. Harmless, invisible, everywhere.
4. **Apparatus with no expiry** — scaffolding built for work that never arrived, plausible forever because nothing about it ever fails. Watch for the subtle variant: an expiry anchored to a start that never happened.
5. **Unwatched queues** — an intake point nothing monitors is not a queue, it is a pile.

## What it will not find

Not a linter, not a security scanner, not code review. Zero bugs in program logic. It is about process and automation going quietly dead.

Pair it with tools that cover the technical perimeter — `gitleaks` for secrets, `lychee` for dead links, `actionlint` for workflow syntax. None of those can see any of the five classes above, and this cannot see what they see.

## Requirements

- Python 3.11+ (standard library only — no dependencies)
- macOS or Linux
- Optional: [Claude Code](https://claude.com/claude-code) or any agent that reads `SKILL.md`-style instructions, if you want the method driven for you

## Use

Run the machine-checkable subset against a directory:

```bash
python3 checks/liveness.py /path/to/your/workspace
```

It reports declared paths that do not resolve, hooks declared but missing, hook commands it cannot read statically, invalid settings files, declared folders sitting empty, queue rows gone stale, hand-written counts worth verifying, and automation that nothing can start. It is a report, not a gate: the exit code is 0 either way.

The last of those is the class this repo leads with — **written but never wired**. Three forms are decidable without running anything:

- a workflow no event in the repository can start (only `workflow_dispatch`, so it runs when a human remembers to press the button);
- a workflow whose `paths:` filter matches no file that exists;
- a script a document says runs by itself, that no workflow, hook or other script ever names.

All three are `info`, and the third only fires when the document itself claims automation ("runs automatically", "blocks the merge", "on every commit"). A script you run by hand is not a defect and is never reported. A trigger block written in YAML this checker does not parse is reported as *unread* rather than passed — the whole point being that silence about an unchecked mechanism is what this tool exists to break.

Each finding carries a stable rule id:

```text
[warning] SR-REF-001 Research/CLAUDE.md:62 — declared but missing: inbox/queue.md
```

A reference is resolved against the directory of the document that declares it, then against that document's project, and only then against the workspace root — and only when the leading segment names a real top-level project. Spans that describe how names are formed rather than naming a file (`YYYY-MM-DD-slug.md`, `cases/<id>/`, `style-tokens/*.yaml`), and bare filenames mentioned in prose, are counted as skipped rather than checked. On a 17-project workspace those two families produced dozens of findings and every one of them was false — a bare `inbox.md` names the file each *other* project keeps, and `CLAUDE.md` in a sentence about what plugins may ship names a convention. What is still checked is any reference carrying a separator, which is what a claim about structure looks like.

The empty-folder rule reads the same declarations: it fires on a directory a document presents as part of the structure, not on every empty folder it can find.

`warning` is a fact the checker established and you can act on — a settings file that does not parse, a hook whose file is not there. `info` is a fact whose significance only you can settle — a hand-written count may be right, a parked queue row may be parked on purpose. The id is what stays put: message wording changes between releases, `SR-REF-001` does not, so it is what to grep for, script against, and cite in a bug report.

Every run ends with what was looked at, class by class:

```text
coverage:
  paths   41 discovered, 38 checked, 3 skipped (Owner/Repo slug, not a path on disk 2, document marks it as planned 1) — paths declared in documents
  queues   6 discovered, 5 checked, 1 skipped (pending row carries no date 1) — pending queue rows
```

A class the scan never reached prints its zero rather than being left out, and a pending row with no date is counted as unchecked rather than as fine. Read the skipped column before you read the findings: a clean report from a scan that checked nothing is the failure this repo is named after.

For a machine, `--format json` writes exactly one object on stdout — `tool`, `root`, `started_at`, `summary`, `coverage`, `findings` — and every human-readable diagnostic to stderr, so the output can be piped straight into a parser:

```bash
python3 checks/liveness.py ~/work --format json | jq '.summary.by_rule'
```

This scan **never runs anything it finds.** Hooks are read, not executed, and the report says so on its own line:

```text
hooks: 3 declared, 3 inspected statically, 0 executed — static inspection is not proof that they run
```

Take that sentence literally. A hook that exists and parses can still be dead, and only running it settles the question. In a workspace whose code you own:

```bash
python3 checks/liveness.py ~/work --execute-hooks --trusted-root ~/work
```

Both flags are required and must name the same directory, so no single forgotten flag turns a scan into an execution. Hooks then run without a shell, with a minimal environment (no API tokens from your session), with a 25-second timeout, and with their state files restored afterwards — an audit that consumes a nudge's "already reported" flag silences the next real notification.

## Four settings, and no file until you need one

Everything works with no configuration. When the defaults do not match your workspace, put `.silent-rot.toml` in the scan root:

```toml
instruction_files = ["CLAUDE.md", "AGENTS.md", "README.md"]
pending_statuses  = ["unprocessed", "pending", "todo", "не обработано"]
stale_days        = 14
exclude_globs     = ["**/.git/**", "**/node_modules/**", "**/.venv/**"]
```

That is the entire surface. These four are what actually differs between workspaces, and `pending_statuses` is the one that matters most: **a queue whose rows say `todo` is invisible to a checker looking for `unprocessed`, and the scan reports a clean run** — this project's own failure mode, performed by this project. Every run therefore prints the vocabulary it used:

```text
config: .silent-rot.toml applied for pending_statuses, stale_days; pending statuses in force: todo, blocked
```

A key that does nothing is reported rather than ignored — `pendingstatuses` misspelled, a string where a list belongs, an empty list that would switch a check off. Silently dropping it would leave a queue unwatched while its owner believes it is configured.

**The config is data, never permission.** It is read out of the tree being scanned, so it is written by whoever wrote that tree. There is no key that enables hook execution, entries pointing outside the scan root are refused, and nothing in the file can widen what the scan may do — only narrow it.

## The scanned tree is untrusted data

Everything in `ROOT` — settings files, hook commands, documents — is read as data written by someone else, including when that someone is you six months ago.

- No network calls, ever. No telemetry. Nothing is written into the scanned tree.
- No `shell=True`, in any mode. A command containing a pipe, a redirection, a substitution or a variable is reported as unsupported rather than interpreted; only a plain `argv` (optionally with a `2>/dev/null` or `|| true` tail) is understood.
- A hook path that resolves outside `ROOT` is never executed, even in trusted mode, and is counted separately in the report.
- Trusted mode is for a workspace you own. It is not a sandbox and does not claim to be one; a sandbox is a different tool.

**Before v0.1.1 none of this was true:** a plain scan executed any `.sh` or `.py` path named in a settings file, including paths outside the scanned tree, and reported "no problems found" while doing it. If you ran an older revision against a repository you did not write, treat it as having run that repository's code. See [CHANGELOG.md](CHANGELOG.md).

Prove the checker actually speaks before you trust it:

```bash
python3 checks/test_liveness.py
```

This builds a fixture workspace, breaks one thing at a time, and fails if the checker stays quiet about any of them. On success it writes a local receipt, which `liveness.py` reads back on every scan.

The receipt is bound to a `sha256` of the checker and its suite, not just to a date. Change one byte in either and the next scan says so:

```text
[warning] SR-SELFTEST-005 .mutation-receipt.json — the mutation test last passed against different code (rerun test_liveness.py)
```

A receipt that recorded only a date would attest to a Tuesday: edit the checker afterwards and it still looks fresh, while nothing has proved the code now running can speak at all.

To run the full five-pass audit (the parts a script cannot judge), point an agent at [`SKILL.md`](SKILL.md). With Claude Code, copy it into your skills directory — for a default install that is ~/.claude/skills/silent-rot/SKILL.md — and invoke the skill by name.

## A clean report is a claim, not proof

The single idea this whole repo exists to carry:

> **A check that has never reported anything must be treated as unexecuted until proven otherwise.**

Not "probably fine". Unexecuted. The proof is a negative test — break what it watches, confirm it speaks, put it back. That is why `test_liveness.py` matters more than `liveness.py`, and why the checker complains when its own receipt goes stale.

A related habit, learned the same day: **a sweep that returns zero for every object at once is more likely a broken measurement than a clean system.** That held three separate times in one audit, including inside the test harness written to catch it.

## Cadence

One frequency for everything is wrong — a full manual sweep has sharply diminishing returns, a machine pass is nearly free.

| What | How often |
|---|---|
| Machine checks | weekly, automated |
| Mechanism liveness, counters, queues | monthly |
| All five passes by hand | quarterly, plus on events |

Events that earn an off-schedule sweep: a project renamed or moved, a new mechanism added, a status vocabulary changed, work delivered outside the system.

Measure one thing above all — **time from a defect existing to it being found**. In the source audit that was 5 to 30 days. If your next sweep finds three-day-old defects, the tooling works. If it finds thirty-day-old ones again, it does not, however many checks it runs.

## How wrong is it?

Pointed at 26 public repositories nobody here wrote, on 2026-08-16, in three rounds. Each round's repositories were unseen when it started, so each measures the tool rather than the tuning:

| sample | at first contact | true | after the fixes it exposed |
|---|---|---|---|
| six repositories | 44 | 2 | 3 |
| ten more, unseen | 73 | 6 | 18 |
| ten more, unseen again | 75 | ~1 | 44 |

**About one finding in twenty was true, and three rounds of fixing did not converge.** Every round found new false families rather than the same ones: documents describing a layout the repo installs elsewhere, then paths written relative to a package directory, then an indented tree where a child folder written bare means its parent's folder plus that name, then a C++ header-and-source pair written as one word, then Go module paths that look exactly like directories.

The honest reading is that **`SR-REF-001` is a home-domain rule**: on the workspace it was built for it runs at 17 true findings out of 20, and on repositories written by other people it runs at about one in twenty. A document does not have to describe the tree it sits in, and outside one particular way of working it usually does not.

It is `info` for that reason, not because a missing path is unimportant. Severity here reports measured precision, not how much the tool would like you to care.

One number is worth more than the precision: **all 8 true findings survived every fix.** Each narrowing was checked against that, or a rule that reports nothing would score perfectly.

Eleven defects in the checker came out of these two runs, including hook paths resolved beside the settings file instead of the project root (three healthy hooks reported missing), `~/…` paths answered from the *auditor's* home directory, a hook command naming a CLI the repository expects installed (`entire hooks …`) reported as a missing file because it was absent from *this* machine's PATH, and one README that produced 47 separate findings for the counts in its own section table.

The uncomfortable half: precision improved partly by **checking less**. Across the first six repositories the scan now examines 21 of 131 declared references — four of the six are dotfiles or template repositories whose documents describe a layout they install *somewhere else*, and there is no static way to tell that from a broken link. The coverage line says so on every run rather than letting a quiet report imply a clean one:

```text
paths  69 discovered, 9 checked, 60 skipped (this tree installs itself elsewhere 51, …)
```

To recompute the number yourself, on your own sample:

```bash
git clone --depth 1 https://github.com/OWNER/REPO /tmp/field/REPO
python3 checks/liveness.py /tmp/field/REPO --format json | jq '.summary, .coverage.paths'
```

Then classify every finding by hand, true or false, with the reason. A rate nobody can recompute is a hand-written count, which is the thing `SR-COUNT-001` exists to flag.

Three classes of false positive are known and unfixed, so you can recognise them rather than trust them:

- **paths that exist only after something runs** — a build output directory named in an npm script, a log file a hook writes, a directory a skill creates on first use;
- **references to your repository, not to theirs** — when a tool's README names the rules file it reads, it means the one in the project you point it at, not one of its own;
- **counts nobody can settle statically** — `SR-COUNT-001` is a request to verify, and a correct number answers it with "fine".

## License

MIT — see [LICENSE](LICENSE).

## Author

Pavel Evgrafov · p.evgrafov@mail.ru
