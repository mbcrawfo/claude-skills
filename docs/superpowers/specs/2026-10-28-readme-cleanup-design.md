# README cleanup design

## Intent

The READMEs are for people who install and use the plugins. Today they mix user guidance with script internals, changelogs and contributor notes, and they are dense. This pass makes every README short, user-facing and written in Simplified Technical English (STE), and moves the rest to where its readers look.

## Scope

In: `README.md`, `plugins/branch-review/README.md`, `plugins/implementation-manager/README.md`, `plugins/maintenance/README.md`, `docs/deferred/README.md`, `docs/tech-debt/README.md`, plus the destinations below (`AGENTS.md` and two new `ARCHITECTURE.md` files).

Non-goals: `SKILL.md` files, agent definitions, evals, specs, plans, research, and the deferred and tech-debt item files. No plugin behavior changes, so no version bumps.

Anti-goal: losing information. Every fact removed from a README either moves to its new home or is a changelog entry (dropped, since git history keeps it).

## Decisions

### Root README

- Ends at the plugin list. Layout, Adding a plugin and Testing locally move to `AGENTS.md`, so its "see README.md for layout" pointer is replaced by the content. The License section is dropped; GitHub shows `LICENSE`.
- States once that the plugins need `python3` 3.9 or later, with the standard library only.
- Each plugin gets a short paragraph: what it does, when to use it, and any extra requirement.

### Plugin READMEs

- No changelogs: remove branch-review's "What's new" sections, maintenance's rename migration note and implementation-manager's 0.1.0 run-folder note.
- No script internals. Each plugin with internals gets `plugins/<name>/ARCHITECTURE.md` for content that the script's own docstring or `--help` does not already cover. Content `run.py`'s docstring and argparse help already cover (the command tables) is dropped from the README, not copied.
- No tests or evals instructions; they move to the plugin's `ARCHITECTURE.md`. Maintenance has no `ARCHITECTURE.md` today; its README has no test section, so none is created unless the edit finds internals to move.
- Each plugin README lists its requirements beyond Python (for implementation-manager: `gh`, the desktop app, branch-review).

**implementation-manager README keeps:** what it does, requirements, install, how to start a run and what that authorizes, a short account of how pushes and merges work, the integration-branch mode's two setup choices, the merge bar in brief, known issues.
**Moves to its `ARCHITECTURE.md`:** the child push protocol, push queue, holds and ordering rules, the full merge bar, integration-branch mechanics, run folder files, `wait` events and timings, tests.

**branch-review README keeps:** what it does, install, review options, a short account of the loop, where state lives (the path), cleanup, the `check` and `record` skills in brief, review guidance in `AGENTS.md`.
**Moves to its `ARCHITECTURE.md`:** the round-mode rules (follow-up, merge-check, forced deep reasons, model choice, second opinion, round cap), summary and review-notes contents, the `rs` wrapper and `CLAUDE_PLUGIN_DATA`, state-key naming, prune and cleanup safety rules, `check`'s coverage conditions, JSON fields and reason codes, `record`'s output and refusals, tests and evals.

### docs/deferred and docs/tech-debt READMEs

Editor and STE pass only. Their table format and `Next id` line stay as they are, since the folders' `AGENTS.md` files depend on them.

### STE

Approach A: the STE writing rules, with STE vocabulary preferred but not checked against the official dictionary.

- Procedural sentences of 20 words or fewer; descriptive sentences of 25 or fewer.
- Active voice. Instructions in the imperative, one per sentence.
- Paragraphs of six sentences or fewer, one topic each.
- No "-ing" verb forms; keep articles; one term for one thing.
- Use the STE approved word where known ("use", not "utilize"). Technical names (PR, branch, commit, `gh`, option names) stay as they are.

Applies to READMEs only. `ARCHITECTURE.md` files are contributor docs and get a plain editor pass, not STE.

`AGENTS.md` gains a short "Documentation" section: READMEs are user-facing, written in STE (the rules above, condensed), with no changelogs or script internals, and internals go in the script or the plugin's `ARCHITECTURE.md`.

## Approaches considered

- **A. STE writing rules, STE words preferred** (chosen): plain and checkable without the official dictionary.
- **B. Full ASD-STE100 with the dictionary:** most uniform, but stilted, and needs the dictionary or accepts errors.
- **Lint script for sentence length:** dropped; code to maintain for five files.

## Left to the builder

Exact wording and section order within each file; whether a fact belongs in the README's brief account or only in `ARCHITECTURE.md`, following the keep/move lists above.
