# claude-skills

This repo is a Claude Code plugin marketplace (`mbcrawfo-skills`). See README.md for layout.

## Agent instructions

- AGENTS.md is the standard instruction file for this repo. Put all agent guidance here. Do not create CLAUDE.md, GEMINI.md or other tool-specific instruction files.

## Plugins and skills

- Each plugin lives in `plugins/<name>/` with a manifest at `plugins/<name>/.claude-plugin/plugin.json`.
- Every plugin must have an entry in `.claude-plugin/marketplace.json` with `"source": "./plugins/<name>"`.
- Skills go in `plugins/<plugin>/skills/<skill-name>/SKILL.md`. The frontmatter `name` matches the folder name, and `description` says when Claude should use the skill.
- Plugin and skill names are kebab-case.
- Bump the plugin's `version` in `plugin.json` when changing its skills, so installed copies update.
- Run `claude plugin validate .` and `claude plugin validate plugins/<name>` before committing.
- Plugin scripts are Python 3.9+, standard library only, invoked through `python3`.
- Tests go in `plugins/<name>/tests/` and run with `python3 -m unittest discover -s plugins/<name>/tests`.
- Skills ship an eval case under `plugins/<name>/evals/`. Test with `claude plugin eval` before release.
- Plugin agents are dispatched as `<plugin>:<agent>`.
