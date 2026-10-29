# claude-skills

A [Claude Code](https://claude.com/claude-code) plugin marketplace for my skills.

## Install

Add the marketplace, then install a plugin from it:

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install <plugin-name>@mbcrawfo-skills
```

Run `/plugin` to see the available plugins. To get updates, run `/plugin marketplace update mbcrawfo-skills`.

## Requirements

The plugins run Python scripts. You must have `python3` 3.9 or later. The scripts use only the standard library, so you do not install other packages.

## Available plugins

### [`branch-review`](plugins/branch-review/README.md)

A local replacement for CodeRabbit pull request reviews. Use it when a change on a branch is complete and before you open a PR. A reviewer agent examines the full change. Claude fixes the findings, and a new reviewer agent checks the fixes. The loop stops when no findings are open.

### [`implementation-manager`](plugins/implementation-manager/README.md)

Completes a planned piece of work that has many phases. The session becomes a manager. It starts one child session for each unit of work, and each child delivers one pull request. The manager uses `branch-review` to approve each push, and it merges each PR when it is ready. This plugin needs the Claude desktop app, a `gh` login and the `branch-review` plugin. The `branch-review` plugin installs with it.

### [`maintenance`](plugins/maintenance/README.md)

Skills that keep a repo clean. `superpowers-cleanup` removes old superpowers specs, plans and research files, and corrects every reference to them. `agents-md-cleanup` keeps `AGENTS.md` files accurate, short, at the correct level and in their size limits. Each skill runs only when you start it.
