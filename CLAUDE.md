# Working on this repository

`silent-rot` is a method for finding automation that has quietly stopped mattering, plus a small checker that demonstrates it. The method (`SKILL.md`, `case-study.md`) is the product; `checks/liveness.py` is the part that proves the method is executable, not the other way round.

## Invariants

Settled as of v0.1.1. A change that weakens one of these is wrong even when the tests pass.

1. **A plain `scan` never executes anything from the scanned tree** — no `subprocess`, no shell, no network, nothing written into the tree. Running hooks requires `--execute-hooks` *and* `--trusted-root` resolving to the scan root.
2. **No `shell=True`, in any mode.** Commands are parsed with `shlex`; a pipe, redirection, substitution or variable makes the command unsupported and reported, never interpreted.
3. **Standard library only.** Python 3.11+, macOS and Linux, zero runtime dependencies.
4. **Every rule owns a mutation** in `checks/test_liveness.py`. A rule with nothing proving it can speak is not implemented, however carefully it is written.
5. **Reports state what was not checked.** A class that was skipped, a hook that was only read, a file that could not be opened — each shows up in the report. Silence about unchecked things is the defect this repo is named after.
6. **Findings are candidates, not verdicts.** The tool cannot know whether a stale row was forgotten or parked on purpose, so it never says so. A false alarm spends the reader's attention and devalues the next real finding.

## Adding a rule

In one change: the rule id in `RULES`, the check itself, a mutation that asserts that id, and a line in `README.md` if a user would otherwise not know the rule exists. Run `python3 checks/test_liveness.py` — it writes a local receipt on success, which `liveness.py` reads back and complains about when it goes stale. The receipt is bound to a hash of both files, so any edit invalidates it: expect `SR-SELFTEST-005` until you rerun the suite, and read that as the intended behaviour rather than as noise. The receipt is never committed: a receipt from someone else's machine would tell you the checks work when you have not run them.

A new rule usually changes the report shape, so the golden file goes red. Read that diff — it is the review of your own change — then regenerate it with `python3 checks/test_liveness.py --update-golden` and commit `checks/golden/report.json` alongside the rule. Never regenerate a red golden without reading the diff first; that turns the tripwire into a rubber stamp.

Two habits from the audit this repo came out of:

- **A negative test can fail for the wrong reason.** The first attempt at proving the scanner refuses secrets used AWS's own documentation key, which scanners deliberately allowlist — the test failed and the tool was fine. Before concluding the code is broken, check the test.
- **A run that returns zero for every object at once is a suspect measurement.** That held three separate times in one audit, including inside the harness written to catch it.

## Conventions

- All repository text is English — README, SKILL, CHANGELOG, code comments, commit messages.
- Comments explain why a line exists, usually by naming the failure that produced it. Restating what the code does is noise.
- Commits are small and describe the defect, not the diff.
- Nothing is pushed without the owner's explicit go-ahead, per release: branch → PR carrying the evidence → merge → tag.
