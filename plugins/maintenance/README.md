# maintenance

Skills for keeping a repo tidy. It holds two skills, `superpowers-cleanup` and `agents-md-cleanup`.

## Install

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install maintenance@mbcrawfo-skills
```

This plugin used to be called `superpowers-cleanup`. Once the marketplace updates, an install under that name fails to load and its `/superpowers-cleanup:cleanup` command goes away. If you have it, swap it for the new one:

```
/plugin uninstall superpowers-cleanup@mbcrawfo-skills
/plugin install maintenance@mbcrawfo-skills
```

## superpowers-cleanup

Removes old [superpowers](https://github.com/obra/superpowers) spec, plan and research files from a repo. Before anything is deleted, it handles every reference to those files, so no comment or doc is left pointing at a missing file and no fact the code depends on is lost.

### Use

The skill runs only when you invoke it. Claude never starts it on its own. Describe what to remove in plain words:

```
/maintenance:superpowers-cleanup specs older than two weeks
/maintenance:superpowers-cleanup specs created on 2026-10-22
/maintenance:superpowers-cleanup specs about the foobar epic
```

What it does:

- **Pairs files:** removing a spec also removes the plans built from it (found from each plan's `**Spec:**` line). Research goes too, when only removed specs cite it.
- **Protects unfinished work:** a file that isn't on the main branch yet, or whose plan names code that doesn't exist, is held back until you confirm it.
- **Handles references:** every reference to a removed file gets one of three fixes:
  - the fact it depended on is written inline,
  - the pointer is removed,
  - or you're asked.
- **One approval:** a single message holds the open questions and a provisional table of every removal and every reference fix. You answer and approve in one reply. Nothing changes before that.
- **Commits but never pushes:** one commit, holding only its own changes.

With no description, it summarizes the specs, plans and research it found and asks what to remove.

### Repo policy

Specs and plans are often point-in-time snapshots, so a kept spec may reasonably still link to a removed one. State your repo's rule in `AGENTS.md`, for example:

> Specs and plans under docs/superpowers are point-in-time records; links between them may go stale when old ones are removed.

Without a rule, the skill asks once per run, and offers to add the line for you.

### Folders

It looks in `docs/superpowers/specs`, `docs/superpowers/plans` and `docs/superpowers/research` by default. If your `AGENTS.md` or `CLAUDE.md` moves them, it uses those folders instead.

## agents-md-cleanup

Reviews every statement in the repo's `AGENTS.md` files and removes the ones that don't earn their place. Each statement must be accurate, valuable (non-obvious), brief, and at the deepest level it applies to. Statements that fail move down to a nested `AGENTS.md` or a code comment, shrink, or go.

### Use

The skill runs only when you invoke it. Claude never starts it on its own. Pass directories to limit the run, and say how many reviewers may run at once:

```
/maintenance:agents-md-cleanup
/maintenance:agents-md-cleanup tests/
/maintenance:agents-md-cleanup 4 at a time
```

What it does:

- **Confirms before dispatch:** it shows the file and block counts and a concurrency limit (4 unless you say otherwise), and waits. No reviewer starts until you answer. When you give a limit yourself (`4 at a time`) on a repeat run, it states the counts and goes straight on.
- **Prefers a clean branch:** if the repo has uncommitted work, untracked files included, it says so first, strongly recommends running on a clean branch, and asks whether to proceed, even on a repeat run with a limit. If you proceed, every edit to an uncommitted file is a question, and for a file that isn't committed yet the question says the commit adds the whole file.
- **Asks three policy questions on the first run:** whether new nested `AGENTS.md` files are allowed, whether a quirk of one file goes in a comment there or in the nearest `AGENTS.md`, and whether push-down is aggressive (anything that applies to one subdirectory) or conservative (only statements naming specific files or folders).
- **Records the answers** in a short `AGENTS.md guidelines` section in the root `AGENTS.md`, so later runs and your other agents follow the same rules. The section is proposed in the approval message and written only after you approve. If there is no root file, it offers to create one; if you decline, the answers apply to that run only.
- **Protects project choices:** "use `uv`, never pip" looks obvious but is a choice between real alternatives, so it is kept or put to you, never deleted in bulk.
- **Remembers your rulings:** when you keep a statement the reviewers doubted, the ruling goes in `.claude/agents-md-cleanup.md`, so the next run doesn't ask again. If `.claude/` is git-ignored, the approval message shows the exact `.gitignore` change that lets the file be committed and shared. You can decline, and the file then stays on your machine.
- **One approval:** a single message holds the questions and a table of every proposed edit. Nothing changes before you approve. A file you edit while it waits is skipped.
- **Commits but never pushes:** one commit, holding only its own changes.

On a partial run such as `tests/`, only moves and questions touch ancestor files (the root one, for example); their other rules are left alone.

### Assumption

Moving a rule into a nested `AGENTS.md` only works if your harness loads nested `AGENTS.md` files when it works in that directory. The skill assumes it does.
