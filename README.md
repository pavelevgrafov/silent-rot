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

It reports declared paths that do not resolve, hooks declared but missing or failing, invalid settings files, declared folders sitting empty, queue rows gone stale, and hand-written counts worth verifying. It is a report, not a gate: the exit code is 0 either way.

Prove the checker actually speaks before you trust it:

```bash
python3 checks/test_liveness.py
```

This builds a fixture workspace, breaks one thing at a time, and fails if the checker stays quiet about any of them. On success it writes a dated receipt — which `liveness.py` reads back and complains about when it goes stale.

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

## License

MIT — see [LICENSE](LICENSE).

## Author

Pavel Evgrafov · p.evgrafov@mail.ru
