# TD-0015: Known gaps in the implementation-manager evals

## Problem

Task 5 of the token-efficiency work found these problems in the evals and did not fix them:

- `push-gate`: the `check-ran` indicator fired in 4 of 7 runs with the plugin. No failing trace was kept. No run answered a push without a coverage check, because the grader's expected `redo` reason comes only from `rs covered`. Either the manager sometimes checks by a route other than the `branch-review:check` skill after `verdict ... unavailable: no check command recorded`, or it invokes the skill without `--state-dir` in the Skill args and the strict grader regex misses it.
- Eval runs that leave a background `run.py wait` running hang until about 580 to 720 s, or until the 1200 s timeout.
- `push-queue`: the haiku judge in `graders/hold.md` still splits its votes (FAIL, PASS, PASS) on correct state.
- `push-queue`: `graders/verdict-used.md` only counts zero `branch-review:check` Skill calls, and it is unscored. It does not prove that the manager used the printed verdict line.
- `compaction-resume`: `graders/resume.md` uses trace focus, which elides middle messages, and the baseline passes it 2 of 3 runs.

## Why it exists

These checks gave a usable signal, and tightening them needed more eval runs than the token-efficiency branch had time for.

## Impact

Low. The evals can pass or fail for the wrong reason, and some runs are slow. Product code is not affected.

## Proposed fix

These are suggestions. Keep a trace of a failing `push-gate` run and match its Skill call, or accept a call without `--state-dir`. End eval runs by stopping the background `wait`. Check state in `hold.md` by script instead of a judge. Score `verdict-used` on a trace that shows the manager read the verdict line. Give `resume.md` the full trace.

## Trigger

An eval run that fails or hangs with no product cause, or a manager change that the evals should catch and do not.
