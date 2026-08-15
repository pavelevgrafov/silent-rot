---
name: silent-rot
description: Audit a workspace, repo, or pipeline for mechanisms that look alive but never run — checks that have never reported anything, counters nobody recomputes, queues nobody watches, apparatus with no expiry. Use when something "seems fine but has never been verified end to end", before trusting an automation you did not just watch execute, when taking over someone else's setup, or on a periodic sweep. Not a code-quality review: this finds silent failures of process and automation, not bugs in program logic.
---

# Silent rot

A mechanism that never runs looks exactly like a mechanism that runs and finds nothing. That resemblance is the whole subject of this skill.

Everything below comes from one audit of a live 27-project workspace that had looked healthy for two months. It found about thirty defects. Not one had ever announced itself, and six of them were in the audit tooling itself.

## The one idea

**A check that has never reported anything must be treated as unexecuted until proven otherwise.** Not "probably fine", not "suspicious" — unexecuted. The proof is a negative test: break the thing it watches, confirm it speaks, put it back.

Everything else here follows from that.

## The five classes to hunt

Look for these, in this order. They are ordered by how well they hide.

**1. Written but never wired.** A validator with no trigger, a workflow file that no event calls, a rule enforced by nothing. In the source audit, a validator described in the docs as blocking had never run; when finally triggered it found five real violations that had sat for months.

*Probe:* for each mechanism, find evidence of **execution** — an artifact, a log line, a state file, a CI run — not evidence of existence.

**2. Wired but looking at the wrong thing.** The check runs, passes, and is structurally incapable of finding its target. A monitor searching for a status word the data has never used. A regex that cannot match the format it was written for. A scanner that stops at a symlink.

*Probe:* grep the data for the exact string the monitor searches. Zero hits against a non-empty source is a defect, not an empty queue.

**3. Hand-written counts.** "All 15 repositories" against 16 rows and 27 directories. Harmless, invisible, and everywhere — the count drifts, the sentence still reads fine.

*Probe:* every number next to a countable noun gets counted. Then **delete the sentence rather than fix the number**: do not write down what the reader can count.

**4. Apparatus with no expiry.** A framework, template set, or pipeline built in anticipation of work that never came. It stays plausible forever because nothing about it ever fails.

*Probe:* ratio of scaffolding to filled-in documents. If nothing has been produced, ask what specific work it waits for and by what date it winds down. Watch for the subtle variant: an expiry that exists but is anchored to a start that never happened — a safety catch that is written and unreachable.

**5. Unwatched queues.** Anything that receives work from outside — an inbox, a task folder, a shared handoff directory. Ask of each: what tells me this has something in it, and when?

*Probe:* list every intake point, then name its monitor. A queue with no monitor is not a queue, it is a pile.

## Rules that cost something to learn

- **Search for the mechanism before writing one.** A silent monitor looks identical to a missing monitor. In the source audit a new watcher was built for a queue that already had one — the old one had been matching a status word that never existed. Before adding a monitor, grep the config layer for the thing it would watch, and if something is already there, ask why it is quiet.
- **Running a stateful component consumes its signal.** Anything that "reports only on change" records what it just reported. Execute it during an audit and every queue now looks already-announced; the next real session says nothing. Snapshot state files before, restore after.
- **A uniform result is a suspect measurement.** A sweep returning zero for every object at once is far more likely to be a broken measurement than a clean system. This held three times in one day, including inside the test harness written to catch it.
- **Report an external obligation as a question, never as a fact.** Files cannot distinguish "delivered outside the system" from "abandoned". A false alarm spends the owner's attention and devalues the next true one.
- **Audit by layer, not by project.** Defects cluster by kind of mechanism, not by subject matter. Sweeping instructions, then automations, then live pipelines, then dormant ones, then configuration, finds far more than sweeping project by project.

## How to run it

Five passes. Each ends with findings written down, because the value compounds across passes — the third one recognises patterns the first could not.

| Pass | Subject | Question |
|---|---|---|
| 1 | instruction/doc layer | does what is claimed exist? |
| 2 | automation library | has it ever run, and what did it leave behind? |
| 3 | active pipelines | does it produce what it promises? |
| 4 | dormant parts | dormant, or abandoned? |
| 5 | config, state, memory | who watches the watchers? |

Then a sixth question the first five cannot answer, because all of them measure the system against itself: **is anyone outside waiting on a date?** Read for commitments — names, deadlines, promised deliveries — and check them against today. End that pass with a question to the owner.

## The tooling in this repo

`checks/liveness.py` implements the machine-checkable subset: declared paths that do not resolve, hooks declared but missing or unreadable statically, invalid settings, empty declared folders, stale queue rows, hand-written counts. Point it at a directory. It never executes what it finds; whether a hook still works is settled by `--execute-hooks --trusted-root` on a workspace you own, and the report distinguishes the two.

`checks/test_liveness.py` is the part that matters more. It builds a fixture, breaks one thing at a time, and fails if the checker stays quiet — then writes a dated receipt that the checker reads back and complains about when it goes stale. This is how "N checks, no problems" becomes evidence instead of a claim.

## What this is not

Not a linter, not a security scan, not code review. It finds no bugs in program logic. Pair it with `gitleaks` for secrets, `lychee` for dead links, `actionlint` for workflow syntax — those cover the technical perimeter, and none of them can see any of the five classes above.

## Cadence

One frequency for everything is wrong: a full manual sweep has sharply diminishing returns, while a machine pass is nearly free.

- Machine checks: **weekly**, automated.
- Mechanism liveness, counters, queues: **monthly**.
- All five passes by hand: **quarterly**, and on events — a project renamed or moved, a new mechanism added, a status vocabulary changed, work delivered outside the system.

Measure one thing above all: **time from a defect existing to it being found**. In the source audit that was 5 to 30 days. If the next sweep finds three-day-old defects, the tooling works. If it finds thirty-day-old ones again, it does not, however many checks it runs.
