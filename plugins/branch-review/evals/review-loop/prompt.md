---
max_turns: 80
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite]
---

The directory `repo` under the current directory is a git repository with the branch `feature/discount` checked out, and `state` next to it is an empty directory for review state. Work inside `repo`: read `INTENT.md`, then do a full code review of this branch before it becomes a PR, fixing what the review finds. The review keeps its state in `--state-dir <absolute path of the state directory>`; get the absolute path with `pwd` before you start.

When the review has finished, write `review-summary.md` in the directory you started in (the parent of `repo`) with the review's final summary, word for word, followed by your final report.
