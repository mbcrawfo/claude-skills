# Plugin data retention: design

Short design (small change). Covers `branch-review` and `implementation-manager`.

## Why

Claude Code deletes `~/.claude/plugins/data/<id>/` only when a plugin is uninstalled from its last scope (unless `--keep-data`) or its marketplace is removed. Updates keep it, `cleanupPeriodDays` does not cover it, and no size limit or retention guidance exists ([CLI reference](https://code.claude.com/docs/en/plugins/cli-reference.md), [.claude directory](https://code.claude.com/docs/en/claude-directory.md)). Cleanup is each plugin's job.

Today `branch-review` prunes its own state (branches that are gone, and state untouched for 30 days, hard-coded), but only for the repo being reviewed. `implementation-manager` never removes a run folder.

## Goals

- Both plugins remove their own state after a retention period: 30 days by default, which the user can change.
- `implementation-manager` removes old run folders, finished or not.

## Non-goals

- Cleaning repos where the plugin no longer runs. Pruning stays per repo, at the point where each plugin starts work (approach 1 below).
- Checking whether a run's manager session is still alive. No hook reports an archived or deleted session, and the desktop app's `get_session` tool works only in the app. The owner chose age alone, for now.
- Removing data that other plugins left, and stray folders under `~/.claude/plugins/data/` such as `branch-review/owner__repo/feature__x` (the script's default state directory, from a manual or test run). The user can delete those by hand.

## Anti-goals

- Removing the state of the branch or run in use.
- Removing anything that is not the plugin's own state: keep `branch-review`'s current rules (skip symlinks and directories that are not review state, report errors and go on).

## Approaches

1. **Prune where each plugin starts work, this repo only.** Chosen by the owner. The smallest change, and it matches what `branch-review` does today.
2. A `SessionStart` hook in each plugin that sweeps all old state. Covers everything, but adds the first hooks to both plugins and runs at every session start.
3. Prune at the same points, but sweep every repo folder by age. Recommended, not chosen: close to 2's coverage, no hooks, but a wider change.

## Decisions

- **The option.** Each plugin's `plugin.json` declares `userConfig.retention_days`: `type: number`, `default: 30`, `min: 1`, with a title and description. Users set it in `/config` or with `claude plugin configure`. It requires Claude Code v2.1.269 or later for the `/config` row.
- **How it reaches the scripts.** Skill text passes `${user_config.retention_days}`, the same way it passes `${CLAUDE_PLUGIN_DATA}` today.
- **branch-review.** The review skill's set-up step passes the option to `rs prune --days`. `STALE_DAYS = 30` stays the script default for manual use.
- **implementation-manager.** A new `run.py prune` removes run folders under this repo's `${CLAUDE_PLUGIN_DATA}/<owner>-<repo>/` whose newest file is older than the retention period. The age is taken from the newest file in the run folder, since children append to `requests.log` while `run.json` sits still. It never removes the run being started, and it removes only folders that hold `run.json`. It skips symlinks and reports `removed`, `skipped` and `errors` as JSON, like `branch-review`'s `prune`. The skill runs it in step 3 (the run folder), before `run.py init`, and prints what it removed.
- **Docs and versions.** Each README says, in STE, where state lives, how long it is kept, and how to change it. Each `ARCHITECTURE.md` documents the prune rules. Bump both plugin versions.
- **Tech debt.** The same PR adds a tech-debt item: state for a repo where the plugin no longer runs is never removed, and run liveness is judged by age alone.

## Left to the builder

- **Check two unknowns first.** Find out whether `${user_config.retention_days}` is filled in with the default when the user never set it, and whether declaring the option makes Claude Code prompt the user at install or enable. If the default is not filled in, the scripts must treat an unexpanded `${user_config.retention_days}` (or an empty value) as the default, 30. *(My call: fall back rather than fail, so an unset option never breaks a review or a run.)* Record what you find in each `ARCHITECTURE.md`.
- Whether `run.py prune` is its own command or part of `init`. *(My call: its own command, like `branch-review`'s.)*
- Tests for `run.py prune` (age by newest file, the run being started kept, symlinks and non-run folders skipped) and for the `--days` value reaching `branch-review`'s prune from skill text.
