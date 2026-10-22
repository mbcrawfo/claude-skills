---
max_turns: 80
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite]
---

Run `bash /Users/michael/Projects/claude-skills/.claude/worktrees/branch-review-plugin/plugins/branch-review/tests/fixture/make_fixture.sh /tmp/br-fixture`, `cd` there, check out `feature/discount`, read `INTENT.md`, and do a full code review of this branch before it becomes a PR, fixing what the review finds.
