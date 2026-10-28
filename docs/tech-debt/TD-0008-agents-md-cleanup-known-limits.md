# TD-0008: agents-md-cleanup's known limits: a symlinked `.claude`, a coarse project-choice check, hard links, and move targets' resolved paths

## Problem

- **A symlinked `.claude` stops the run.** `_rulings` in [`agents_md_inventory.py`](../../plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py) fails with `error: .claude is a symlink; git can't track the rulings file through it` when `.claude` is a symlink, whether it leads inside or outside the repo. `inventory` stops there, so the skill can't run in that repo at all.
- **The project-choice check is coarse.** `_CHOICE` (used by `_guard_choice` in `check-verdicts`) turns a `valuable` or `brief` delete into an `ask` when the block has an inline code span or one of `rather than`, `instead of`, `always`, `never`, `not`, `only`, `prefer`, `avoid`. It sends some generic statements to the user, and it misses a tool named without backticks (`Use uv`).
- **Hard-linked AGENTS.md files are not merged.** `_entries` groups paths by `_resolved`, which follows symlinks only. Two hard links to one file, such as `AGENTS.md` and `d/AGENTS.md`, become two entries, each with no `aliases`. Each gets its own reviewer, and an edit through one path changes the other. The second entry's approved edits then meet text that has already changed. When only one entry has edits, the commit stages that path alone and leaves the other path modified.
- **Only SKILL.md gives a move target its resolved path.** `check-verdicts` accepts a move target that is itself a symlink to another file in the repo, such as a comment target `src/link.py -> real.py`. Edits must go to the file the link leads to. The target gets that path only because [SKILL.md](../../plugins/maintenance/skills/agents-md-cleanup/SKILL.md) step 4 ("Other files") runs `blocks` on it and keeps its `resolved` path. The script does not enforce this: its report carries no resolved path for a target. An apply that skips that step edits and stages the link.

## Why it exists

Git can't track a path through a symlinked directory, so a rulings file there could be neither committed nor safely guarded. Treating it as absent would let the skill draft a rulings file it then fails to stage. Supporting it needs a decision about where the rulings live. The heuristic leans toward asking, since a wrong deletion costs more than a question, and step 6 groups these asks into one pattern question.

The symlink merge fixed the common case, an AGENTS.md linked to CLAUDE.md or to a shared file. Git doesn't record hard links, so they appear only when a local tool makes them, and merging them was left out. Step 4 already runs `blocks` on every other file it edits, to take its `sha256`, so the resolved path comes from the same call and was not added to `check-verdicts`.

## Impact

Low. A repo whose `.claude` is a symlink (from a dotfiles manager, say) can't run agents-md-cleanup until the link is replaced by a directory. The heuristic costs some extra questions, and a bare tool name still rests on the reviewer alone. A hard-linked AGENTS.md is reviewed twice, and its second set of edits can fail to apply or leave a path modified and uncommitted. A move to a symlinked target relies on the skill's instructions alone, so a run that skips step 4's `blocks` call would replace the link with a file or commit the wrong path.

## Proposed fix

- When `.claude` is a symlink, report the rulings as unusable (`exists: false` plus a field such as `trackable: false`). SKILL.md would then skip writing them and say why, instead of stopping the run.
- Tune `_CHOICE` against real reviewer output. Drop the noisiest words (`not`, `only`), and consider matching names from the repo's config files (`pyproject.toml` tools, `package.json` scripts). Extend `ProjectChoiceDeletionTest` in `plugins/maintenance/tests/test_agents_md_verdicts.py` to match.
- Group paths in `_entries` by `(st_dev, st_ino)` as well as by resolved path, so hard links merge into one entry with `aliases`. A hard link, unlike a symlink, is a file git tracks, so SKILL.md step 8 must stage every path of such an entry, not only `resolved`. Add a `LinkedFileTest` case in `plugins/maintenance/tests/test_agents_md_inventory.py`.
- Have `check-verdicts` add each move target's resolved path (for example `target_resolved`) to the move verdicts it reports, `proposed` moves included. SKILL.md step 4 would then use that path. Test it with a symlinked comment target.

## Trigger

A user report of a symlinked `.claude`, too many or missed project-choice questions, or a hard-linked AGENTS.md. Also the next change to `_rulings`, `_guard_choice`, `_entries` or move-target handling.
