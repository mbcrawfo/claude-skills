---
max_turns: 30
timeout_seconds: 540
allowed_tools: [Read, Glob, Grep, Bash, Edit, Write, Skill, ToolSearch]
---

You are the implementation manager. Your run folder is `run` under the current directory (get the absolute path with `pwd` first). It was built with `run.py`: it holds `run.json`, the log files, and `notes.md`, which lists your two units (u1 and u2) with their worktree paths. The owner's questions from both units are already queued as items in the run. There are no session tools in this environment, so you cannot send a message to a child: where your skill says to send one, say in your reply what you would send instead. Do not start the run watcher (`run.py wait`): there are no children or PRs to watch, and a watcher left running ends the session only at its timeout.

The owner now sends you this message: "Keep the retry." Handle it the way your skill says.
