# maintenance

Skills for keeping a repo tidy. Today it holds one skill, `superpowers-cleanup`.

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
