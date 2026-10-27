# Rename superpowers-cleanup plugin to maintenance

## Intent

The `superpowers-cleanup` plugin becomes `maintenance`, a home for repo-maintenance skills. Its one skill, `cleanup`, becomes `superpowers-cleanup`. The command changes from `/superpowers-cleanup:cleanup` to `/maintenance:superpowers-cleanup`. The new name exists to leave room for more skills; none are planned yet.

## Goals

- The plugin is named `maintenance` everywhere: its folder, `plugin.json`, the `marketplace.json` entry, and both READMEs.
- The skill is named `superpowers-cleanup` (folder and frontmatter `name`), and every slash command written in the repo uses the new form.
- The skill owns its script, so future skills in the plugin don't share files by accident.
- Behavior is unchanged: the same tests pass and the eval still runs.

## Non-goals

- No change to what the skill does.
- No new skills.
- No edits to existing specs and plans (`2026-10-24-superpowers-cleanup-*`). They are point-in-time records and keep the old name.

## Approaches

- **A. Straight rename.** The script stays in the plugin-level `scripts/` folder. This is the least churn, but a script used by one skill would sit in a spot that looks shared.
- **B. Rename, and move the script into the skill (chosen).** `docs_inventory.py` moves to `skills/superpowers-cleanup/scripts/`. More change now, but each skill owns its own files from the start. The user picked B.

## Changes

- `git mv plugins/superpowers-cleanup plugins/maintenance`
- `git mv plugins/maintenance/skills/cleanup plugins/maintenance/skills/superpowers-cleanup`
- `git mv plugins/maintenance/scripts/docs_inventory.py plugins/maintenance/skills/superpowers-cleanup/scripts/docs_inventory.py`, then remove the empty plugin-level `scripts/` folder.
- **SKILL.md:**
  - Frontmatter `name: superpowers-cleanup`.
  - The description names `/maintenance:superpowers-cleanup`.
  - Both script calls use `${CLAUDE_SKILL_DIR}/scripts/docs_inventory.py`.
- **plugin.json:** `name: maintenance`, version `0.2.0`, and a general description: repo-maintenance skills, starting with superpowers spec/plan/research cleanup. Keywords gain `maintenance`.
- **marketplace.json:** the entry's name, source (`./plugins/maintenance`) and description match `plugin.json`.
- **Tests stay in `plugins/maintenance/tests/`**, where CI's `plugins/*/tests` loop finds them:
  - `helpers.py`: `SCRIPTS_DIR` points at `skills/superpowers-cleanup/scripts`.
  - `test_skill_md.py`: the skill path, the asserted name `superpowers-cleanup`, and the grader path.
  - `test_eval_fixture.py`: the scaffold path.
- **Eval:** the folder is renamed `cleanup-run` → `superpowers-cleanup-run` (with `case.yaml` `name`), and `prompt.md` uses the new command. `plugins: ["../.."]` still resolves to the plugin root.
- **docs_inventory.py:** the docstring names the `maintenance` plugin's `superpowers-cleanup` skill.
- **Plugin README:**
  - Title `maintenance`, with a short intro saying it holds repo-maintenance skills.
  - The current content becomes a `superpowers-cleanup` section.
  - Install and usage lines use the new names.
  - A note for existing users: uninstall `superpowers-cleanup@mbcrawfo-skills` and install `maintenance@mbcrawfo-skills`.
- **Root README:** the plugin list entry is renamed and its description generalized.

## Builder's calls

- Version `0.2.0` rather than a reset to `0.1.0`: it's a continuation of the same code.
- Eval case renamed to match the skill.
- The ruff `known-first-party` list keeps `docs_inventory`, since the module name doesn't change.

## Verification

- `python3 -m unittest discover -s plugins/maintenance/tests`
- `ruff check .` and `ruff format --check .`
- `claude plugin validate .` and `claude plugin validate plugins/maintenance`
- `grep -rn "superpowers-cleanup:cleanup\|skills/cleanup\|plugins/superpowers-cleanup"` outside `docs/superpowers/` returns nothing.
- Optionally run the eval with `claude plugin eval` to confirm the script path resolves in a real run.
