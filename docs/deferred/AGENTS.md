# AGENTS.md - docs/deferred

Tracks planned work that was not delivered as planned: scope cut from a spec or plan, a requirement delivered only in part, or a planned approach replaced by a stand-in.

- If the work shipped but is flawed or knowingly sub-optimal, it belongs in `docs/tech-debt`, not here.
- A spec that drifted from the code is not by itself a deferred item.  Record one only when the change leaves planned work undelivered.

`README.md` holds the table of active items.  Each item is documented in its own file in this folder.

## Adding an item

Do all of this in the same PR that defers the work.

1. Take the id from `Next id` in `README.md` and increment that line.  Ids are `D-NNNN` and are never reused.
2. Create `D-NNNN-<short-slug>.md` in this folder with these sections:
   - **Planned** - what was supposed to be delivered, linking the spec, plan or doc that called for it.
   - **Delivered instead** - what exists today, linking the code.
   - **Why deferred** - the reason the planned work was not done.
   - **Remaining work** - what resolves the item, specific enough to pick up without the original context.
   - **Trigger** - the condition or dependency that should prompt picking it up, if known.
3. Add a row to the table in `README.md` with the id linked to the item file.  Use the status values defined there.

Code may reference an item by id in a comment (`D-0003`) where the stand-in lives.

Parallel branches can take the same id.  If `Next id` conflicts on merge or rebase, the branch that merges later takes the next free id and renames its item file, row and references.

## Changing an item

- Keep the row's status current when work starts or becomes blocked.
- After a partial fix, rewrite the item file and the row to describe only what remains.

## Resolving an item

In the same PR as the fix:

1. Delete the item file.
2. Remove its row from the table in `README.md`.  The table lists active items only; git history is the record of resolved ones.
3. Remove any references to the id from code and docs by searching for the item's actual id, for example `grep -r "D-0003" .`.

Never mark an item resolved without the fix, and never merge a fix that leaves its item behind.

> Maintenance: this file names specific files and folders.  If you rename, move, or remove them, update this file in the same commit.
