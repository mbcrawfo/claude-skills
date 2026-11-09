# Starting children

If this session already did other work, call `mcp__ccd_session__mark_chapter` first. Write a bold title line and one sentence for each session right before its call. Each call: `initiation: "user_asked"`, `context: "fresh"`, `use_worktree: true`, `name`, `title` and `branch_name` from the unit. In integration-branch mode, start the integration child first, then every unit child, and give every call `source_branch: integration/<slug>` too.

## The brief

The child's prompt is a brief file the run script writes from the plugin's templates: parts 1 to 11, the gate included, and in integration mode the mode's changes to a unit's prompt (its base, its start, its syncs, the sandbox line). The integration unit gets the integration child's own prompt. Setup gives the script what it needs: `run.py set <run> manager <your id>`, each unit's `--spec`, and `<run>/facts.md` with the repository facts every child needs (formatter, validation commands, PR rules), which every brief includes in part 6. For each unit:

1. Write the unit's own part 6 text to a file outside the repository (this session's scratchpad if it has one, otherwise `<run>/`). Put in exactly this, leaving out what does not apply:
   - The owner's decisions and constraints for this unit.
   - For a parallel sibling: which other unit is running, which shared files you both touch, and to keep those changes small. Also: read the "Shared changes" section of `<run>/notes.md`; cherry-pick a sibling's unmerged commit only with `git cherry-pick -x`, and name it in the PR description; before the final review round, sync with `<base>` if it moved.
   - For a unit with merge-order units: name them, and say its push request waits until they merge; the `go` then comes on its own, so the wait needs no message.
   - In integration mode, `<base>` in this text is `integration/<slug>`, and any sync it mentions takes `base: integration/<slug>` (ccd_host `sync_with_base_branch`; the tool accepts the branch the worktree was cut from).
2. Run `run.py brief <run> <unit> --extra-file <file>`. Add `--no-gate` for a unit added mid-run whose gate `run.py open-ready <run>` already opened, and `--designed` for a unit the owner designs with the child (below). It prints the brief's path, `<run>/briefs/<unit>.md`.
3. Call `start_session` with this prompt, `<name>` the unit and `<run>` written out: ``You are unit `<name>` of an implementation-manager run. Read `<run>/briefs/<name>.md` in full and follow it; it is your whole task.``

## A unit the owner designs with the child

For a unit added mid-run that the owner will design in the child's session, run `brief` with `--designed`, and with `--no-gate` when `run.py open-ready <run>` opened its gate. `--designed` adds the paragraph that sends the child's owner questions to the owner in its own session until its first push request, and after it a one-line notice to you for a question that blocks the merge (the events.md row for that notice says what to do).

## A card for work outside every unit

Propose it as a card (`start_session` with `initiation: "own_initiative"`), once. Write what the work is to a spec file in `<run>/`, add it as a unit (`run.py unit <run> <name> --title "<title>" --spec <file>`), write its brief as above, gate included, and give the card the start prompt above: `brief` needs the unit and its spec. It merges like any unit and gates nothing. If the owner turns the card down, stop the unit (`run.py stop <run> <name> <reason>`).

## Red flags

| Thought | Reality |
|---|---|
| "I'll start the next unit when this one merges" | That turn is a background event; the start becomes a card. Start everything now, gated. |
| "The next prompt should mention what 5-3 changed" | Prompts are written up front. Put it in `notes.md` before opening the gate. |
