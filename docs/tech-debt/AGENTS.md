# AGENTS.md - docs/tech-debt

Tracks problems that were identified and not resolved, and intentional choices that are known to be sub-optimal: shortcuts, workarounds, suppressed warnings, known bugs left in place, fragile or slow code accepted for now.

- If planned work is simply missing, it belongs in `docs/deferred`, not here.
- Record problems you find in code you did not write or change, too.  Finding it and not fixing it is what triggers the rule.

`README.md` holds the table of active items.  Each item is documented in its own file in this folder.

## Adding an item

Do all of this in the same PR that introduces or discovers the problem.

1. Take the id from `Next id` in `README.md` and increment that line.  Ids are `TD-NNNN` and are never reused.
2. Create `TD-NNNN-<short-slug>.md` in this folder with these sections:
   - **Problem** - what is wrong or sub-optimal, linking the code.
   - **Why it exists** - why it was chosen or left unresolved.
   - **Impact** - what it costs today and what it risks as the code grows.
   - **Proposed fix** - the better approach, specific enough to pick up without the original context.
   - **Trigger** - the condition that should prompt fixing it, if known.
3. Add a row to the table in `README.md` with the id linked to the item file.  Use the status and impact values defined there.

Code may reference an item by id in a comment (`TD-0003`) where the compromise lives.

Parallel branches can take the same id.  If `Next id` conflicts on merge or rebase, the branch that merges later takes the next free id and renames its item file, row and references.

## Changing an item

- Keep the row's status and impact current as the code around the problem changes.
- After a partial fix, rewrite the item file and the row to describe only what remains.

## Resolving an item

In the same PR as the fix:

1. Delete the item file.
2. Remove its row from the table in `README.md`.  The table lists active items only; git history is the record of resolved ones.
3. Remove any references to the id from code and docs by searching for the item's actual id, for example `grep -r "TD-0003" .`.

Never mark an item resolved without the fix, and never merge a fix that leaves its item behind.

> Maintenance: this file names specific files and folders.  If you rename, move, or remove them, update this file in the same commit.
