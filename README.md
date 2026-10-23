# claude-skills

A [Claude Code](https://claude.com/claude-code) plugin marketplace for my skills.

## Install

Add the marketplace, then install any plugin from it:

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install <plugin-name>@mbcrawfo-skills
```

Run `/plugin` to browse what's available. Pull updates with `/plugin marketplace update mbcrawfo-skills`.

## Available plugins

- [`branch-review`](plugins/branch-review/README.md): a local replacement for CodeRabbit PR reviews. Agents review a branch, fix the findings and re-check until it converges.
- [`implementation-manager`](plugins/implementation-manager): runs a planned piece of work to completion through child sessions that each deliver a pull request, watching each PR until it is ready to merge.

## Layout

```
.claude-plugin/marketplace.json   # marketplace manifest: lists every plugin
plugins/<plugin-name>/
  .claude-plugin/plugin.json      # plugin manifest
  skills/<skill-name>/SKILL.md    # one folder per skill
```

A plugin can hold several related skills. Plugins can also ship `commands/`, `agents/` and `hooks/` alongside `skills/`.

## Adding a plugin

Skills are added by agents following [AGENTS.md](AGENTS.md). Each plugin needs a folder under `plugins/` and an entry in `marketplace.json`.

## Testing locally

```bash
claude plugin validate .
claude plugin validate plugins/<plugin-name>
```

To try it end to end, add your local checkout as a marketplace: `/plugin marketplace add ./path/to/claude-skills`.

## License

MIT
