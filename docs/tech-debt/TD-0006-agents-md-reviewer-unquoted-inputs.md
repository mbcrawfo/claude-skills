# TD-0006: agents-md-cleanup passes paths to its reviewers unquoted and space-separated

## Problem

The dispatch template in [`SKILL.md`](../../plugins/maintenance/skills/agents-md-cleanup/SKILL.md) (step 3) and the inputs in [`agents-md-reviewer.md`](../../plugins/maintenance/agents/agents-md-reviewer.md) (*Inputs* and *Start*) assume no path holds a space:

- `SCOPE` and `AGENTS_MD_DIRS` are space-separated lists of directories, so `docs/my notes` reads as two directories.
- The agent is told to run `cd <REPO> && python3 <SCRIPT> blocks --repo <REPO> <FILE>`, writing the values in as they are. `REPO`, `SCRIPT` and `FILE` are unquoted there, so a repo, plugin cache or file path with a space (or a shell character) breaks the command or runs something else.

## Why it exists

It was deferred by a task review: the paths come from the inventory and the plugin cache, which rarely hold spaces, and a reviewer usually quotes paths on its own.

## Impact

Low. With a space in a path, a reviewer may judge the wrong scope, miss an AGENTS.md when looking for duplicates, or fail its first command and report the file as an error, which the skill then lists as not reviewed. Nothing is written: the reviewer is read-only.

## Proposed fix

- Pass `SCOPE` and `AGENTS_MD_DIRS` as JSON arrays (`SCOPE: ["src", "docs/my notes"]`) in the SKILL.md template, and describe them that way in the agent's *Inputs*.
- In the agent's *Start*, write the command with each value single-quoted (`cd '<REPO>' && python3 '<SCRIPT>' blocks --repo '<REPO>' '<FILE>'`), with the same `'\''` rule SKILL.md step 7 uses.
- Update `test_names_every_input` in `plugins/maintenance/tests/test_agents_md_skill.py` for the new command text.

## Trigger

A repo or plugin path with a space in it, or the next change to the dispatch template.
