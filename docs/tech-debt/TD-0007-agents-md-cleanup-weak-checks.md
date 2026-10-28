# TD-0007: Three agents-md-cleanup checks rest on weaker signals than they claim

## Problem

- **Change check by porcelain status.** SKILL.md step 7.1 ([`SKILL.md`](../../plugins/maintenance/skills/agents-md-cleanup/SKILL.md)) checks a file outside `files` (a comment move's target, say) for changes since step 4 by comparing `git status --porcelain -- <file>`. A file that was already dirty stays ` M` after further edits, so a change made while the user read the approval message goes unnoticed, and the approved edit lands on top of it.
- **Project choices protected only by the reviewer.** Step 5 puts deletions of "obvious" statements in bulk. Whether a deleted statement is really a project choice between real alternatives (which the guidelines say is never obvious) is decided only by the reviewer agent ([`agents-md-reviewer.md`](../../plugins/maintenance/agents/agents-md-reviewer.md), *Valuable*). The skill does not check, for example, that a `valuable` delete naming a tool or an alternative carries a quoted duplicate as evidence, so a reviewer mistake reaches the bulk table.
- **A no-dispatch grader that can pass trivially.** The first-run eval's [`no-dispatch.md`](../../plugins/maintenance/evals/agents-md-cleanup-first-run/graders/no-dispatch.md) grader asserts no `Agent` call. The case doesn't grant `Agent`, so the grader passes whether or not the skill would have dispatched. The `pre-dispatch` llm grader is what really pins the behaviour.

## Why it exists

Each was deferred by a task review. A content hash needs the file read at step 4, which the skill does only for `files`. A skill-side check of "obvious" needs judgment the script can't make. Granting `Agent` in the first-run eval would let a broken skill start paid reviewer runs.

## Impact

Low to medium. Every bulk row is shown before approval, so a wrong deletion is visible, but the user may not read every row; the guidance is lost until someone notices. The porcelain gap needs a concurrent edit during the approval wait. The grader gap only weakens the eval, not the skill.

## Proposed fix

- At step 4, record a sha256 of every non-inventory file the run will edit (`blocks` already hashes any in-repo file, or use `git hash-object`), and compare it at step 7.1 instead of porcelain status.
- In SKILL.md step 5, send to Ask any `valuable` delete whose evidence quotes no other file (`path:line`), so only proven duplicates and generic advice go in bulk; or add that check to `check-verdicts`.
- Grant `Agent` in the first-run case with a mock or a tight `max_turns`, or drop `no-dispatch` and rely on `pre-dispatch`, saying so in the grader text.

## Trigger

A report of a lost project choice or an overwritten edit, or the next change to SKILL.md steps 5 or 7, or to the first-run eval.
