# Plugin Data Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `branch-review` and `implementation-manager` remove their own state after a retention period that defaults to 30 days and that users can change.

**Architecture:** Each plugin declares a `userConfig.retention_days` option. Skill text passes `${user_config.retention_days}` to the plugin's prune command, at the point where the plugin starts work in a repo. `branch-review` already prunes (`rs prune --days`); `implementation-manager` gets a new `run.py prune` that removes this repo's run folders by age.

**Tech Stack:** Python 3.9+ standard library, `unittest`, ruff, Claude Code plugin manifests.

**Spec:** `docs/superpowers/specs/2026-11-05-plugin-data-retention-design.md`

## Global Constraints

- Option, both plugins, in `plugin.json`: `"userConfig": {"retention_days": {"type": "number", "title": "Retention (days)", "description": "Remove state untouched for this many days", "default": 30, "min": 1}}`.
- A retention value that is empty or still the unexpanded text `${user_config.retention_days}` means 30. A value that is not a whole number of at least 1 is a usage error (argparse, exit 2).
- Skill text quotes the option in single quotes, `--days '${user_config.retention_days}'`. If Claude Code leaves it unexpanded, a double-quoted `${user_config.retention_days}` is a shell "bad substitution" error before the script runs.
- Never remove the current branch's state or the run being started. Never follow or remove symlinks. Remove only directories that are the plugin's own state.
- Versions: `branch-review` 0.12.1 → 0.13.0; `implementation-manager` 0.8.0 → 0.9.0. `implementation-manager`'s dependency on `branch-review >=0.12.1` stays.
- READMEs in Simplified Technical English (AGENTS.md rules). Internals go in `ARCHITECTURE.md`.
- Python 3.9+, standard library only. Run `ruff check .` and `ruff format .` (or `uvx ruff@<pinned version>` if `ruff` is not on PATH) before each Python commit.

## Review Focus

1. **The option was never set.** The user never opened `/config`. Both scripts must still prune at 30 days, whether the skill text has `30`, an empty string or the literal `${user_config.retention_days}`. Tests: Task 2 Step 1 and Task 3 Step 1.
2. **A live run whose `run.json` is old.** Children append to `requests.log` while `run.json` sits still, so a live run must be kept. Test: Task 3 `test_prune_age_is_newest_file`.
3. **The run being started already exists**, for example a manager restarted on the same slug. It must never be removed, however old. Test: Task 3 `test_prune_keeps_the_given_run`.
4. **Symlinks.** A symlinked run folder must not be removed, and a symlink inside a run must not make the run look fresh by pointing at a new file. Tests: Task 3 `test_prune_skips_symlinked_run` and `test_prune_age_ignores_symlink_targets`.
5. **The first run in a repo.** `<owner>-<repo>/` does not exist yet, so prune prints an empty result and exits 0. Test: Task 3 `test_prune_without_repo_folder`.

---

### Task 1: Probe how `userConfig` reaches skill text

A throwaway probe outside the repo. Nothing from it is committed except its findings, which Tasks 2 and 3 write into each `ARCHITECTURE.md`.

**Files:**
- Create (scratch only): `<scratch>/probe/.claude-plugin/plugin.json`, `<scratch>/probe/skills/show/SKILL.md`

- [ ] **Step 1: Write the probe plugin.** Give `plugin.json` `"name": "probe"`, `"version": "0.0.1"` and the `userConfig` block from Global Constraints. Give `SKILL.md` frontmatter `name: show` and a `description`, and this body: `Print exactly this line and nothing else: DAYS=[${user_config.retention_days}] DATA=[${CLAUDE_PLUGIN_DATA}]`

- [ ] **Step 2: Validate it.** Run `claude plugin validate <scratch>/probe`. Expected: no errors.

- [ ] **Step 3: Run it with no saved value.** Run `zsh -lc 'cd <scratch> && claude -p --plugin-dir <scratch>/probe "/probe:show"'` (the login shell loads the token from `~/.zprofile`). Record the `DAYS=[…]` value: `30`, empty, or the literal text.

- [ ] **Step 4: Record whether the option prompts on enable.** Note any prompt or warning in Step 3's output about unset options. If headless output cannot show it, record "not observable headless". The `claude plugin configure probe` output after a local install would show this too, if it can be done without changing the user's real settings. Otherwise skip it.

- [ ] **Step 5: Note the findings** (two lines) for Tasks 2 and 3. Delete the probe.

---

### Task 2: branch-review reads the retention option

**Files:**
- Modify: `plugins/branch-review/.claude-plugin/plugin.json` (add `userConfig`; version `0.13.0`)
- Modify: `plugins/branch-review/scripts/review_state.py:2344` (`prune --days` type)
- Modify: `plugins/branch-review/skills/review/SKILL.md:30` (set-up step 2)
- Modify: `plugins/branch-review/README.md` ("Where state lives")
- Modify: `plugins/branch-review/ARCHITECTURE.md` ("Pruning and cleanup")
- Test: `plugins/branch-review/tests/test_cleanup.py`, `tests/test_skill_text.py`, `tests/test_args.py`

**Interfaces:**
- Produces: `review_state._retention_days(value: str) -> int`. Returns `STALE_DAYS` for `""` or any value starting with `"${"`, the int for a whole number ≥ 1, and raises `argparse.ArgumentTypeError` otherwise. It is used as the `type=` of `prune --days`.

- [ ] **Step 1: Write the failing tests.**
  - In `test_cleanup.py`, beside `test_prune_days_option`, add `test_prune_days_unset_means_30`. Seed `old` at `days_ago(31)` and `recent` at `days_ago(29)`, with both branches existing. Then for each of `"${user_config.retention_days}"` and `""`, `self.cli("prune", "--days", value)["removed"]` equals `[str(old)]` the first time; re-seed between runs.
  - Add `test_prune_days_rejects_zero`: running `prune --days 0` exits 2. Use whatever helper `test_cleanup.py` or `helpers.py` has for a failing CLI call.
  - In `test_skill_text.py` `ReviewSkillTextTest`, add `test_prune_passes_retention_option`. The line holding `rs prune` contains `--days '${user_config.retention_days}'`.
  - In `test_args.py`, change the version assertion to `"0.13.0"`.

- [ ] **Step 2: Run them and see them fail.** Run `python3 -m unittest discover -s plugins/branch-review/tests -v`. Expected: the four new or changed tests FAIL. The literal `${…}` is not an int, and the version is still 0.12.1.

- [ ] **Step 3: Implement.** Add `_retention_days` next to `STALE_DAYS` and use it as `type=` for `prune --days`. In SKILL.md step 2, change `Run \`rs prune\`.` to `Run \`rs prune --days '${user_config.retention_days}'\`.` and keep the rest of the step. Add the `userConfig` block and bump the version in `plugin.json`.

- [ ] **Step 4: Run the tests and see them pass.** Same command. Expected: all pass.

- [ ] **Step 5: Update the docs.**
  - README "Where state lives": replace "It also removes state older than 30 days." with two sentences. State older than the retention period is removed, 30 days by default. Change the period with the `retention_days` option in `/config`, or with `claude plugin configure branch-review@mbcrawfo-skills`.
  - ARCHITECTURE "Pruning and cleanup": the review skill passes `${user_config.retention_days}` to `prune --days`, and an empty or unexpanded value means `STALE_DAYS`. Say why the value is in single quotes (see Global Constraints). Add Task 1's findings in one or two sentences.

- [ ] **Step 6: Validate and lint.** Run `claude plugin validate plugins/branch-review`, `ruff check .` and `ruff format --check .`. Expected: clean.

- [ ] **Step 7: Commit.** `feat(branch-review): retention_days option for prune`

---

### Task 3: implementation-manager prunes old runs

**Files:**
- Create: `plugins/implementation-manager/scripts/prune.py`
- Modify: `plugins/implementation-manager/scripts/run.py` (docstring command list, `_parser`, `main`)
- Modify: `plugins/implementation-manager/.claude-plugin/plugin.json` (add `userConfig`; version `0.9.0`)
- Modify: `plugins/implementation-manager/skills/implementation-manager/SKILL.md:71` (step 3, "The run folder")
- Modify: `plugins/implementation-manager/README.md`, `plugins/implementation-manager/ARCHITECTURE.md` ("Run folder")
- Test: `plugins/implementation-manager/tests/test_prune.py` (new), `tests/test_skill_text.py` (new)

**Interfaces:**
- Produces: `prune.retention_days(value: str) -> int`, with the same rules as branch-review's `_retention_days` and a default of `DEFAULT_DAYS = 30`.
- Produces: `prune.prune_runs(keep: Path, days: int, now: float) -> dict`. It scans `keep.parent`. The result is `{"removed": [str], "skipped": [str], "errors": [{"dir": str, "error": str}]}`, each list sorted.
  - An entry that is a symlink, or a directory without `run.json`, goes in `skipped`. Files that are not directories are ignored.
  - `keep` (compared by resolved path) is never touched or listed.
  - A run's age is `now` minus the newest `lstat().st_mtime` of the folder and everything under it, walked without following symlinks. A run is removed when its age is over `days * 86400`, with `shutil.rmtree`.
  - An `OSError` while checking or removing one folder goes in `errors`, and the scan goes on. A missing `keep.parent` gives empty lists.
- Produces: CLI `run.py prune <dir> [--days D]`. `<dir>` is the run folder about to be started, and it need not exist. It prints the result as one JSON object and exits 1 if `errors` is non-empty, 0 otherwise. `main` dispatches it before `_run`, as it does `wait`, because it takes no lock.

- [ ] **Step 1: Write the failing tests** in `tests/test_prune.py`. Call `prune_runs` directly, with a temp `<tmp>/owner-repo/` holding run folders that each have a `run.json`, and with ages set by `os.utime`. Add one subprocess test for the CLI, in the style of `test_run_cli.py`.
  - `test_prune_removes_old_run`: a run at 31 days is removed and listed. A run at 29 days stays.
  - `test_prune_days_option`: a run at 5 days goes with `days=3` and stays with `days=30`.
  - `test_prune_age_is_newest_file`: `run.json` is at 40 days and `requests.log` at 1 day, so the run stays.
  - `test_prune_keeps_the_given_run`: `keep` points at a run at 90 days. It stays and is in neither list.
  - `test_prune_skips_symlinked_run`: a symlink at 90 days to a run folder elsewhere is listed in `skipped`, and its target still exists.
  - `test_prune_age_ignores_symlink_targets`: a run at 40 days holds a symlink to a fresh file outside it, and the symlink's own mtime is set to 40 days with `os.utime(..., follow_symlinks=False)`. The run is removed, and the outside file still exists.
  - `test_prune_skips_non_run_folder`: a folder at 90 days with no `run.json` is in `skipped` and still exists.
  - `test_prune_without_repo_folder`: `keep.parent` does not exist, so the result has three empty lists.
  - `test_retention_days`: `retention_days("")` and `retention_days("${user_config.retention_days}")` are `30`, `retention_days("7")` is `7`, and `"0"`, `"-1"` and `"x"` raise `argparse.ArgumentTypeError`.
  - `test_cli_prune`: `run.py prune <tmp>/owner-repo/new-run --days '${user_config.retention_days}'` exits 0 and prints JSON whose `removed` lists the 31-day run. `--days 0` exits 2.
  - In a new `tests/test_skill_text.py`: SKILL.md has a line containing `run.py prune` and `--days '${user_config.retention_days}'`, and that line comes before the first `run.py init` in the text.

- [ ] **Step 2: Run them and see them fail.** Run `python3 -m unittest discover -s plugins/implementation-manager/tests -v`. Expected: the new tests fail with `ModuleNotFoundError: prune` or an assertion; the existing tests pass.

- [ ] **Step 3: Implement** `prune.py`, then the `prune` subparser in `run.py`. The subparser uses the existing `command()` helper and `--days` with `type=prune.retention_days` and `default=prune.DEFAULT_DAYS`. Add its line to the docstring's command list: `prune <dir> [--days D]   remove run folders beside <dir> untouched for D days (default 30), never <dir>; print {removed, skipped, errors}`. Add the `userConfig` block and bump the version in `plugin.json`.

- [ ] **Step 4: Update SKILL.md step 3.** Right before "Run `run.py init`", add this text. First run `run.py prune --days '${user_config.retention_days}'`, which removes this repo's runs untouched for the retention period, never `<run>`. Print each path in `removed`. On exit 1, print each entry in `errors` and go on, since a failed prune does not affect this run.

- [ ] **Step 5: Run the tests and see them pass.** Same command. Expected: all pass.

- [ ] **Step 6: Update the docs.**
  - README: add a short section, "Where state lives", in STE. The run's files are in `~/.claude/plugins/data/implementation-manager-mbcrawfo-skills/<owner>-<repo>/<run>/`. When a run starts, it removes runs of the same repo that are older than the retention period, 30 days by default. Change the period with the `retention_days` option in `/config`. Runs in a repo where you start no new run stay until you remove them.
  - ARCHITECTURE "Run folder": the prune rules from the Interfaces block in one paragraph, why the age comes from the newest file, and Task 1's findings.

- [ ] **Step 7: Validate and lint.** Run `claude plugin validate plugins/implementation-manager`, `ruff check .` and `ruff format --check .`. Expected: clean.

- [ ] **Step 8: Commit.** `feat(implementation-manager): prune old run folders by retention_days`

---

### Task 4: Record the tech debt

**Files:**
- Create: `docs/tech-debt/TD-0011-plugin-data-per-repo-retention.md`
- Modify: `docs/tech-debt/README.md` (row, and `Next id` → `TD-0012`)

- [ ] **Step 1: Write TD-0011** with the sections of TD-0001: Problem, Why it exists, Impact, Proposed fix, Trigger.
  - **Problem:** both plugins prune only the repo they are working in, so state for a repo where neither runs again is never removed until the plugin is uninstalled. `implementation-manager` also judges a run by age alone: it cannot tell a live quiet run from one whose manager session is archived or deleted, because no hook reports that.
  - **Why it exists:** the owner chose per-repo pruning (design approach 1) and age alone.
  - **Impact:** Low.
  - **Proposed fix:** sweep every repo folder by age at the same entry points (design approach 3). Optionally record the manager's session id and check it with the desktop app's `get_session` before removing a run.
  - **Trigger:** plugin data growing large, or a live run lost to pruning.

- [ ] **Step 2: Add the README row**: `| [TD-0011](TD-0011-plugin-data-per-repo-retention.md) | Open | 2026-11-05 | Low | branch-review and implementation-manager prune only the repo they run in, and implementation-manager judges a run's liveness by age alone |`. Set `Next id` to `TD-0012`.

- [ ] **Step 3: Commit.** `docs(tech-debt): TD-0011 per-repo plugin data retention`

---

### Task 5: Verify the whole change

- [ ] **Step 1: Run all tests.** Run `python3 -m unittest discover -s plugins/branch-review/tests` and `python3 -m unittest discover -s plugins/implementation-manager/tests`. Expected: OK for both.

- [ ] **Step 2: Validate.** Run `claude plugin validate .`, `claude plugin validate plugins/branch-review` and `claude plugin validate plugins/implementation-manager`. Expected: no errors.

- [ ] **Step 3: Run one eval per plugin.** Each eval takes one run, through `zsh -lc` for the token. For `branch-review`, the `review-loop` command from its ARCHITECTURE.md. For `implementation-manager`, the `push-gate` case with the command from its ARCHITECTURE.md, adding `--runs 1 --ablation none`. Expected: both pass. A failed prune inside the eval sandbox should be printed and passed over, not stop the run.
