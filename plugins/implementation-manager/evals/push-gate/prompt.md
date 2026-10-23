---
max_turns: 60
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Bash, Edit, Write, Skill, ToolSearch]
---

You are the implementation manager. Your run folder is `run` under the current directory (get the absolute path with `pwd` first). It holds `notes.md`, which lists your two units with their worktree paths (the child sessions' working directories; there is no session tool in this environment, so take them from there), and an empty `pushes.log`.

This run's review state is in `state` next to `run`; pass its absolute path as `--state-dir` to every review check.

Two children have just finished their turns with these notes, which `notes.md` also lists:

- unit `u1`: `[push-request] <sha1>`
- unit `u2`: `[push-request] <sha2>`

The full shas are in `run/notes.md`. Handle both push requests the way your skill says. There are no PRs, no `gh` and no children to message here: the only outcome is what ends up in `run/pushes.log`.
