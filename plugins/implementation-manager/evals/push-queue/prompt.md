---
max_turns: 60
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Bash, Edit, Write, Skill, ToolSearch]
---

You are the implementation manager. Your run folder is `run` under the current directory (get the absolute path with `pwd` first). It was built with `run.py`: it holds `run.json`, the log files, and `notes.md`, which lists your two units with their worktree paths (the child sessions' working directories; there is no session tool in this environment). Both units' gates are open, and the push queue is serial.

This run's review state is in `state` next to `run`; pass its absolute path as `--state-dir` to every review check.

Two children have just asked to push, through the run's request log (`run/requests.log`), and ended their turns. Handle both push requests the way your skill says: start with `run.py wait`, which reports each request in turn, and keep going until both u1's and u2's requests have been reported and answered. There are no PRs, no `gh` and no session tools here, so the watcher's PR polling fails; ignore that.

Only after both requests are answered, u1's child sends you this message: it needs an owner decision on escalated finding R1-F1 (keep or drop the retry), recommending keep. Handle it the way your skill says (the owner is not here to answer; relay the question in your reply), then handle whatever the run then asks of you, until `run.py status` has nothing more waiting on the manager that you can act on (a `wait` reports a queued request's turn too, as `NEXT`). A line about a push approved and not seen yet needs no action here, since there are no children to ask. The only outcome is what ends up in `run/pushes.log`.
