# claude-skills

This repo is a Claude Code plugin marketplace (`mbcrawfo-skills`). Layout:

```
.claude-plugin/marketplace.json   # marketplace manifest: lists every plugin
plugins/<plugin-name>/
  .claude-plugin/plugin.json      # plugin manifest
  skills/<skill-name>/SKILL.md    # one folder per skill
docs/superpowers/specs/           # design docs
docs/deferred/                    # planned work not delivered as planned
docs/tech-debt/                   # known problems and sub-optimal choices
```

A plugin can hold several related skills. Plugins can also ship `commands/`, `agents/` and `hooks/` alongside `skills/`, and an `ARCHITECTURE.md` with internals, tests and evals for contributors.

## Core rules

These apply to all development in this project.

1. **Document deferred work.**  Any work that is not implemented as planned must be documented in `docs/deferred`, in the same PR that defers it.
2. **Document tech debt.**  Any problem identified and not resolved, or any intentional choice that is known to be sub-optimal, must be documented in `docs/tech-debt`, in the same PR that discovers or introduces it.

## Agent instructions

- AGENTS.md is the standard instruction file for this repo. Put all agent guidance here. Do not create CLAUDE.md, GEMINI.md or other tool-specific instruction files.

## Documentation

- READMEs are for people who install and use the plugins. Write them in Simplified Technical English (ASD-STE100):
  - Keep procedural sentences to 20 words or fewer and descriptive sentences to 25 or fewer.
  - Use active voice. Write instructions in the imperative, one per sentence.
  - Keep paragraphs to six sentences or fewer, one topic each.
  - Do not use "-ing" verb forms. Keep articles. Use one term for one thing.
  - Use the STE approved word where you know it ("use", not "utilize"). Keep technical names as they are.
- READMEs hold no changelogs and no script internals. Document internals in the script itself (docstring, comments, `--help`), or in the plugin's `ARCHITECTURE.md` when the script cannot hold them.
- A plugin's tests and evals instructions go in its `ARCHITECTURE.md`.

## Pushing

- Run a branch review (`branch-review:review`) before every push, and push only once it is clean for the commit being pushed. No exceptions.
- Bring the branch up to date with `main` before every push. If that changes the branch, the review must cover the updated commit.
- CI's `ci` job depends on every other job and is the required PR check.

## Plugins and skills

- Each plugin lives in `plugins/<name>/` with a manifest at `plugins/<name>/.claude-plugin/plugin.json`.
- Every plugin must have an entry in `.claude-plugin/marketplace.json` with `"source": "./plugins/<name>"`.
- Skills go in `plugins/<plugin>/skills/<skill-name>/SKILL.md`. The frontmatter `name` matches the folder name, and `description` says when Claude should use the skill.
- Plugin and skill names are kebab-case.
- Bump the plugin's `version` in `plugin.json` when changing its skills, so installed copies update.
- Run `claude plugin validate .` and `claude plugin validate plugins/<name>` before committing. To try a plugin end to end, add your local checkout as a marketplace: `/plugin marketplace add ./path/to/claude-skills`.
- Plugin scripts are Python 3.9+, standard library only, invoked through `python3`.
- Tests go in `plugins/<name>/tests/` and run with `python3 -m unittest discover -s plugins/<name>/tests`.
- Python is linted and formatted with ruff, configured in the root `pyproject.toml`, whose `required-version` pins the ruff release (keep it in step with `version:` in the `ruff` job in `.github/workflows/ci.yml`). Run `ruff check .` and `ruff format .` before committing Python.
- `.editorconfig` mirrors the ruff settings (line length, quote style). Change both together.
- `.python-version` pins the interpreter for local use and CI.
- A plugin's main workflow skill ships an eval case under `plugins/<name>/evals/`. Test with `claude plugin eval` before release.
- Plugin agents are dispatched as `<plugin>:<agent>`.
