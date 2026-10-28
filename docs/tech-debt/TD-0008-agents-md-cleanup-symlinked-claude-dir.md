# TD-0008: agents-md-cleanup stops when `.claude` is a symlink, and its project-choice check is coarse

## Problem

- **A symlinked `.claude` stops the run.** `_rulings` in [`agents_md_inventory.py`](../../plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py) fails with `error: .claude is a symlink; git can't track the rulings file through it` when `.claude` is a symlink, whether it leads inside or outside the repo. `inventory` stops there, so the skill can't run in that repo at all.
- **The project-choice check is coarse.** `_CHOICE` (used by `_guard_choice` in `check-verdicts`) turns a `valuable` or `brief` delete into an `ask` when the block has an inline code span or one of `rather than`, `instead of`, `always`, `never`, `not`, `only`, `prefer`, `avoid`. It sends some generic statements to the user, and it misses a tool named without backticks (`Use uv`).

## Why it exists

Git can't track a path through a symlinked directory, so a rulings file there could be neither committed nor safely guarded. Treating it as absent would let the skill draft a rulings file it then fails to stage. Supporting it needs a decision about where the rulings live. The heuristic leans toward asking, since a wrong deletion costs more than a question, and step 6 groups these asks into one pattern question.

## Impact

Low. A repo whose `.claude` is a symlink (from a dotfiles manager, say) can't run agents-md-cleanup until the link is replaced by a directory. The heuristic costs some extra questions, and a bare tool name still rests on the reviewer alone.

## Proposed fix

- When `.claude` is a symlink, report the rulings as unusable (`exists: false` plus a field such as `trackable: false`). SKILL.md would then skip writing them and say why, instead of stopping the run.
- Tune `_CHOICE` against real reviewer output. Drop the noisiest words (`not`, `only`), and consider matching names from the repo's config files (`pyproject.toml` tools, `package.json` scripts). Extend `ProjectChoiceDeletionTest` in `plugins/maintenance/tests/test_agents_md_verdicts.py` to match.

## Trigger

A user report of a symlinked `.claude`, too many or missed project-choice questions, or the next change to `_rulings` or `_guard_choice`.
