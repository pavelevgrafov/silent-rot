# Case study: thirty silent failures in a workspace that looked fine

One audit, one long session, a 27-project workspace run day to day with an AI coding agent. Everything below is measured, not estimated. Names, employers and any business detail are stripped; the numbers and the defect classes are the content.

## Why look at all

Nothing was broken. No incident, no complaint, no failing build.

The trigger was a small thing: one automation turned out to have been written, never run, and to look **exactly** like the ones that worked. That raises a question observation cannot answer — how much more of this is there, and why does nothing surface it?

## The method

A sweep by **layer, not by project**. This was the single most consequential decision: defects cluster by kind of mechanism, not by subject matter, so a project-by-project pass would have found the same class five times and called it five unrelated problems.

| Pass | Subject | Question |
|---|---|---|
| 1 | instruction/doc layer | does what is claimed exist? |
| 2 | automation library | has it ever run, and what did it leave behind? |
| 3 | active pipelines | does it produce what it promises? |
| 4 | dormant parts | dormant, or abandoned? |
| 5 | config, state, memory | who watches the watchers? |

One rule inside every pass: **no statement in a document is taken on faith.** Each is checked against actual state — mechanically where possible (370 automated checks), by reading where a machine cannot judge.

## What came out

About **thirty defects, every one of them weeks old, none of which had ever produced a symptom.** They collapse into five repeating classes — and the classes are the real finding, because a class transfers to any other system and a specific breakage does not.

### 1. Written but never wired

A quality validator, described in the project's own documentation as blocking, with no trigger attached. It had never executed. Wired up during the audit, it immediately found five genuine violations that had been sitting for months.

### 2. The check exists, but it is not the one that runs

The sharpest class, because it is invisible from both sides — the check reports success, and success is what you expected.

An environment-dependency check had reported "clean" since the day it was written, at zero coverage, for **three independent reasons at once**, each sufficient on its own: a regular expression that could not match the standard naming format, a search restricted to files where the thing it looked for is never written, and a directory walk that silently stops at symbolic links — half the targets being symlinks.

A second instance: a monitor watching a shared work queue, running on every session start, searching for a status value **the data has never once used** in its entire history. A task from an outside collaborator sat unanswered for five days with a watchdog running over it the whole time.

### 3. Hand-written counts

"The map of all 15 repositories" — against 16 rows in the table below it and 27 project directories on disk. Three independent instances in one day, in three different documents.

Nobody had noticed any of them, and the reason is worth stating: **the drift breaks nothing.** The sentence still parses, the document still reads as authoritative. The fix is not to correct the number but to delete it — do not write down what the reader can count.

### 4. Apparatus with no expiry

A set of four tools and sixteen templates, internally consistent, cross-referenced, with no broken links — and **not one document produced by it** in ten days of existence. Built in anticipation of work that had not arrived.

Ten days is not enough to declare something dead. The actual defect is different: it had no expiry condition and no review date, so it would look plausible forever, because nothing about it can ever fail.

A subtler variant in another project: a self-measurement system that **does** define its own shutdown review — "at day 90, decide: scale, simplify, or close." Day 90 counts from a setup step that was never performed. The safety catch is written, and unreachable.

### 5. Unwatched queues

Every intake point was inventoried and asked one question: what tells me this has something in it, and when? One shared handoff directory had no answer at all — see the five-day-old task above.

## Why this case is worth trusting

Here is where it separates from "an AI wrote me a report".

**Six of the thirty defects were in the audit tooling itself.** The instrument used to run the review was wrong three times in one day, and fixing it took priority over everything else — a wrong check is more dangerous than no check, because it manufactures confidence.

**One finding was a false alarm, and it is written into the report rather than deleted.** The audit flagged an external commitment as overdue: by the files, the work had not moved in two weeks. The owner's answer: it had been delivered directly to the person waiting, never touching the repository. The lesson is worth more than the finding would have been — **no file-based check can distinguish "delivered outside the system" from "abandoned"**, so an external obligation must be reported as a question to the owner, never as a fact. A false alarm spends attention and devalues the next true one.

**Three separate measurements returned a plausible zero** during the day, each from a mechanical quirk rather than a real zero. What saved them was implausibility: "zero for every object at once" is too uniform to believe. That became a standing rule — and it promptly caught a fourth instance, inside the test harness written to enforce it.

## What was left running afterwards

Not a report. Working equipment:

- **370 automated checks** at session start, with — more importantly — **negative tests** on a growing share of them: proof that the check speaks when its target breaks. Until a check passes that test, it counts as unexecuted.
- **A mutation harness**: it builds a fixture, breaks one thing at a time, fails if the checker stays quiet, and writes a dated receipt on success. The checker reads that receipt back and raises a problem when it is missing or older than a month. "370 checks, no problems" is now accompanied by evidence rather than standing on its own.
- **Three cadences instead of one**: machine checks weekly, mechanism liveness monthly, the full five passes quarterly and on events (a project renamed, a mechanism added, a status vocabulary changed).
- **One metric that decides whether any of it works**: time from a defect existing to it being found. Baseline: 5 to 30 days. If the next sweep finds three-day-old defects, the equipment works. If it finds thirty-day-old ones again, it does not — regardless of how many checks it runs.

## Transferable in one line

Systems do not usually fail loudly. They stop being true, one document and one silent watchdog at a time, while every surface you look at keeps saying they are fine.

The method and the tooling from this audit are open source: [SKILL.md](SKILL.md) for the five passes, [`checks/`](checks/) for the machine-checkable subset.
