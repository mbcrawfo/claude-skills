# Integration-Branch Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An opt-in run mode in which every unit merges into an integration branch and one final PR lands the whole change in `main`, plus a branch-review `record` step that lets the integration session sign off on already-merged work without reviewing it.

**Architecture:** branch-review gains a `recorded` round type (`review_state.py record`) that anchors coverage like a reviewed round, and a hidden `record` skill. The implementation manager's run state gains an `integration` block, an integration unit kind, a final gate, a "sync in flight" merge block and a `SYNCED` event for direct pushes. The skill text adds the integration child's prompt and the new event rows.

**Tech Stack:** Python 3.9+ standard library, `unittest`, `gh` CLI, Claude Code skills (SKILL.md).

**Spec:** `docs/superpowers/specs/2026-10-26-implementation-manager-integration-branch-design.md`

## Global Constraints

- Plugin scripts are Python 3.9+, standard library only, invoked through `python3`; keep `from __future__ import annotations` at the top of each script.
- Tests: `python3 -m unittest discover -s plugins/<name>/tests`. Lint/format: `ruff check .` and `ruff format .` from the repo root.
- `claude plugin validate .` and `claude plugin validate plugins/<name>` pass before each commit that touches a plugin.
- branch-review version becomes `0.10.0`; implementation-manager becomes `0.5.0`.
- The default (non-integration) run must behave exactly as today: every existing test passes unchanged.
- Never write a dollar sign followed by a digit in a SKILL.md file.
- Deferred work goes in `docs/deferred/`, known sub-optimal choices in `docs/tech-debt/`, in the same PR.
- `record` accepts only commits equal to or an ancestor of `refs/remotes/origin/<current branch>`.
- No default for who merges the final PR: `--final-merge` is required with `--integration`.

## Review Focus

1. `record` run with a stale or missing `origin/<branch>` ref (the caller forgot to fetch): it must refuse with one line, never record a local commit. Test in Task 1.
2. A sync push while the final PR is already open: `SYNCED` must still fire when the branch head equals the `go` sha, or late units deadlock behind the slot. Test in Task 6.
3. A unit naming the integration unit as `--prereq` or `--after`, or `--integration` on a run without the mode: refused. Test in Task 4.
4. A `run.json` written by 0.4.0 (no `integration` key, unit records without `integration`) loads and runs exactly as before. Test in Task 4.
5. Every unit stopped: the final gate never opens and `status` says so, so the manager asks the owner. Test in Tasks 4 and 7.

---

## File Structure

branch-review (`plugins/branch-review/`):
- Modify `scripts/review_state.py`: `cmd_record`, the `record` subparser, recorded rounds in `cmd_status`'s cap, `render_summary`, `_notes_line`, `render_review_notes`.
- Create `tests/test_record.py`.
- Create `skills/record/SKILL.md`.
- Modify `tests/test_skill_text.py`, `README.md`, `.claude-plugin/plugin.json`.

implementation-manager (`plugins/implementation-manager/`):
- Modify `scripts/runstate.py`: integration config, unit kind, final gate, merge block, `record_synced`, queue priority.
- Modify `scripts/prstate.py`: `branch_head`.
- Modify `scripts/watcher.py`: `SYNCED`, `FINAL-READY`.
- Modify `scripts/run.py`: new flags and commands, status lines, `check` mapping.
- Modify `tests/fakes.py`, `tests/test_runstate.py`, `tests/test_watcher.py`, `tests/test_run_cli.py`, `tests/test_prstate.py`.
- Modify `skills/implementation-manager/SKILL.md`, `README.md`, `.claude-plugin/plugin.json`.

Repo docs:
- Create `docs/deferred/D-0003-implementation-manager-integration-eval.md`.
- Create `docs/tech-debt/TD-0002-integration-merge-check-second-opinion.md`.

---

### Task 1: branch-review `record` command

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (new `cmd_record` after `cmd_begin_round`; subparser in `_build_parser` after `begin-round`)
- Test: `plugins/branch-review/tests/test_record.py`

**Interfaces:**
- Produces: CLI `review_state.py record [--commit SHA] --reason-file PATH [--base REF]` and `cmd_record(args: argparse.Namespace) -> dict`. It returns `{"recorded": bool, "round": int | None, "commit": str, "branch": str, "files": int, "dir": str}`. `recorded` is False only when the commit is already the anchor's head.
- Produces: a round dict with every key `begin-round` writes (`number`, `mode`, `model`, `started`, `snapshot`, `head`, `base_ref`, `merge_base`, `diff_from`, `diff_to`, `changed_files`, `paths`, `summary`, `merge_risk`, `focus`, `mode_reason`, `plugin_version`, `intent`), plus `reason` and `added`:
  - `mode` is `"recorded"`; `model`, `summary`, `merge_risk` and `focus` are `None`;
  - `snapshot`, `head` and `diff_to` are the commit;
  - `paths` and `added` are `[]`;
  - `mode_reason` is `"recorded"`;
  - `reason` is the stripped text of the reason file.

- [ ] **Step 1: Write the failing tests** in `tests/test_record.py`. The class `RecordTest` uses `make_repo(self.tmp / "r")` (branch `feature/x`), `run(...)` from `helpers`, and a `review()` helper copied from `test_covered.py`. A helper `publish()` sets `refs/remotes/origin/feature/x` to HEAD with `git update-ref`, and `reason(text)` writes a reason file and returns its path. Tests:
  - `test_records_a_published_commit`: publish, then record. `recorded` is True and `round` is 1. `covered` then reports `covered: True`.
  - `test_first_record_saves_an_empty_change`: on a branch reset to `main` and published, `files == 0` and `recorded` is True; `covered` is True.
  - `test_refuses_a_commit_not_on_the_remote_ref`: commit a file after `publish()`, then record HEAD. Exit 1, stderr contains `not on origin/feature/x`.
  - `test_refuses_without_a_remote_ref`: no publish; exit 1, stderr contains `origin/feature/x`.
  - `test_refuses_an_empty_reason`: a reason file holding only whitespace gives exit 1.
  - `test_refuses_a_detached_head`: after `git checkout --detach`, exit 1.
  - `test_no_op_at_the_anchor_head`: record twice at the same commit. The second call returns `recorded: False`, and `review.json` still has 1 round.
  - `test_keeps_open_findings`: a deep `review()` with one open finding, then publish and record. `covered` reasons contain `open-findings`.
  - `test_clean_base_merge_after_record_is_covered`: record at the published head, commit on `main`, and merge `main` into the branch cleanly. `covered` is True with no new review.
  - `test_conflicting_base_merge_after_record_is_merge_check`: record, then make a conflicting `main` commit, merge it, resolve and commit. `locate` returns `mode == "merge-check"`. A merge-check `begin-round` has `changed_files` equal to the conflicted file only.
  - `test_record_starts_from_previous_anchor`: deep `review()` at commit A, publish, commit B (published), record. The round's `diff_from` is A's snapshot and `changed_files` is B's files.

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest plugins/branch-review/tests/test_record.py -v`
Expected: FAIL (`invalid choice: 'record'`).

- [ ] **Step 3: Implement `cmd_record(args) -> dict`** in `review_state.py`:
  - Locate the state with `_locate_keys`. A detached HEAD (`branch_key` gives `detached-<sha7>`) is refused with `record needs a branch, not a detached HEAD`.
  - Resolve `--commit` (default `HEAD`) with `rev-parse --verify`.
  - Resolve the remote ref `refs/remotes/origin/<branch>`. Missing: `StateError("no origin/<branch> ref; fetch the branch first")`. The commit not `_is_ancestor` of it: `StateError("commit <sha12> is not on origin/<branch>; record only what is already pushed")`.
  - Read the reason with `_read_text_file`; empty after strip is a `StateError`.
  - Create the state dict exactly as `cmd_begin_round` does when none exists (factor that block into `_new_state(rkey, branch, base_ref) -> dict` and call it from both).
  - Drop unrecorded rounds with `_recorded`. If the anchor's `head` equals the commit, return `recorded: False` without saving.
  - `diff_from` is the anchor's snapshot, or the merge base when there are no rounds. `changed_files` are the names from `_numstat(repo, diff_from, commit)`.
  - Append the round and call `save_state`.

  Register the subparser: `--commit` (default `HEAD`), `--reason-file` (required, `metavar="PATH"`), `--base`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -m unittest discover -s plugins/branch-review/tests`
Expected: all OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/branch-review/scripts/review_state.py plugins/branch-review/tests/test_record.py
git commit -m "feat(branch-review): record command for commits already on the remote branch"
```

### Task 2: Recorded rounds in the cap, second opinion and summaries

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (`cmd_status`, `render_summary`, `_notes_line`, `render_review_notes`)
- Test: `plugins/branch-review/tests/test_record.py` (new class `RecordedRoundsTest`)

**Interfaces:**
- Consumes: Task 1's `record` CLI and round shape.
- Produces: summary text `Rounds: 1 recorded, 2 merge-check (opus)`; a `## Recorded without review` section in `summary.md` and a `### Recorded without review` section in `review-notes.md`, each line `- round <n> at <sha7>: <reason>`; `_notes_line` mode text `<n> recorded`.

- [ ] **Step 1: Write the failing tests:**
  - `test_recorded_rounds_do_not_count_toward_the_cap`: five deep rounds with open findings, then a record, then a sixth reviewed round. `status` returns `continue: True` and `reason` other than `max-rounds`. Use `--since-round 0`; the seventh round overall is the sixth counted.
  - `test_recorded_round_does_not_satisfy_a_requested_second_opinion`: a deep round with `--second-opinion` stored, no findings, then a record. `covered` reasons contain `second-opinion-pending`.
  - `test_recorded_round_triggers_no_second_opinion`: record only. `status` reason is `converged`.
  - `test_summary_lists_recorded_rounds`: a deep round, then a record with reason `merged #12`. `summary.md` contains `Rounds: 1 deep (test-model), 2 recorded`, `## Recorded without review` and `- round 2 at <sha7>: merged #12`. The header has `last recorded` with no `with None`.
  - `test_review_notes_name_recorded_rounds`: `review-notes.md`'s first content line contains `1 recorded` and the block has `### Recorded without review`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest plugins/branch-review/tests/test_record.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement:**
  - In `cmd_status`, `run_rounds` skips rounds whose `mode == "recorded"`.
  - In `render_summary`, the header drops ` with <model>` when `model` is None, and `listed` writes `<n> recorded` for recorded rounds. Add the `## Recorded without review` section after the Rounds line (`None.` when there are none).
  - In `_notes_line`, add `"recorded"` to the mode loop, rendered `<n> recorded` with no models.
  - In `render_review_notes`, add a `("Recorded without review", <lines>)` entry to `sections` before `Intent check`.

- [ ] **Step 4: Run all branch-review tests**

Run: `python3 -m unittest discover -s plugins/branch-review/tests`
Expected: all OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/branch-review/scripts/review_state.py plugins/branch-review/tests/test_record.py
git commit -m "feat(branch-review): recorded rounds never count as review"
```

### Task 3: branch-review `record` skill, README and version

**Files:**
- Create: `plugins/branch-review/skills/record/SKILL.md`
- Modify: `plugins/branch-review/tests/test_skill_text.py`, `plugins/branch-review/README.md`, `plugins/branch-review/.claude-plugin/plugin.json`

**Interfaces:**
- Consumes: Task 1's CLI.
- Produces: skill `branch-review:record`, invoked with `--repo PATH --commit SHA --reason-file PATH`.

- [ ] **Step 1: Write the failing tests** in `test_skill_text.py`:
  - Add `"record"` to the tuple in `test_every_rs_command_sets_plugin_data`.
  - Add a class `RecordSkillTest`:
    - `test_hidden_from_users`: the frontmatter has `user-invocable: false` and does not contain `disable-model-invocation`.
    - `test_description_is_a_back_door`: the description contains `implementation-manager` and `Never in response to a user's request`.
    - `test_fetches_first`: the body contains `git fetch origin`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest plugins/branch-review/tests/test_skill_text.py -v`
Expected: FAIL (file not found).

- [ ] **Step 3: Write `skills/record/SKILL.md`.**

  Frontmatter:
  - `name: record`
  - `description: Only when the implementation-manager integration-branch protocol tells you to record commits already merged on the remote branch. Never in response to a user's request, and never to get a branch past review.`
  - `user-invocable: false`
  - `argument-hint: "--repo PATH --commit SHA --reason-file PATH"`

  Body, modelled on `skills/check/SKILL.md`:
  - It records, without review, a commit that is already on `origin/<branch>`. The script refuses anything else.
  - Run `git -C <repo> fetch origin <branch>` first.
  - The command, written out in full: `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_SKILL_DIR}/../../scripts/rs" record --repo "<repo>" --commit "<sha>" --reason-file "<path>"`.
  - Print the JSON verbatim, then one line: `recorded round <n>` or `already recorded`.
  - On exit 1, report the stderr line and stop. Never retry with another commit.

- [ ] **Step 4: Update the README and version:**
  - Add a `## For other plugins` heading after `## Check a commit`. It covers both `check` and `record`, and moves the check text under it unchanged. Its record paragraph says what record does, the remote-only rule, that recorded rounds are listed as such and never count as review, and that it's not offered in the `/` menu.
  - Add a `## What's new in 0.10` section.
  - Set `"version": "0.10.0"` in `plugin.json`.

- [ ] **Step 5: Run the tests and validate**

Run: `python3 -m unittest discover -s plugins/branch-review/tests && claude plugin validate plugins/branch-review && claude plugin validate .`
Expected: all OK, validation passes.

- [ ] **Step 6: Commit**

```bash
git add plugins/branch-review
git commit -m "feat(branch-review): hidden record skill for the implementation manager (0.10.0)"
```

### Task 4: Run state: integration config, unit kind and final gate

**Files:**
- Modify: `plugins/implementation-manager/scripts/runstate.py`
- Test: `plugins/implementation-manager/tests/test_runstate.py` (new classes `IntegrationConfigTests`, `IntegrationUnitTests`, `FinalGateTests`)

**Interfaces:**
- Produces:
  - `FINAL_MERGES = ("manager", "owner")`.
  - `init_run(run_dir, *, repo, base, method, wip, queue_mode, now, integration: dict | None = None) -> dict`. `integration` is `{"branch": str, "final_merge": str, "feature_review": bool}`; it is stored with `"final_gate": False` added. It is refused when `branch` is empty or holds whitespace, or when `final_merge` is not in `FINAL_MERGES`.
  - `state["integration"]`: dict or None. `load` sets it to None when it is absent, and each unit's `"integration"` to False when absent.
  - `unit_base(state) -> str`: the integration branch in the mode, else `state["base"]`.
  - `integration_unit(state) -> str | None`.
  - `add_unit(..., integration: bool | None = None)`:
    - Refuses `integration=True` when the mode is off (`run has no integration branch`), for a second unit, with prereqs or after, or with a branch other than the integration branch. The branch defaults to the integration branch.
    - `_check_order_edges` refuses any `--prereq` or `--after` naming the integration unit (`no unit may wait on the integration unit`).
  - `set_option(state, key, value)` gains `final-merge` (`manager|owner`) and `feature-review` (`yes|no`). Both are refused when the mode is off, or when `final_gate` is True (`the final gate is open`).
  - `openable`: the integration unit is never cut by WIP and never counts toward it (like `specs`).
  - `late_units(state) -> list[str]`: non-integration units neither done nor stopped, in order.
  - `final_due(state) -> bool`: the mode is on, the integration unit exists with its gate open, `final_gate` is False, `late_units` is empty, and at least one non-integration unit is done.
  - `all_stopped(state) -> bool`: the mode is on, at least one non-integration unit exists, and every one is stopped.
  - `open_final(run_dir, state) -> str`: appends `<unit> final review` or `<unit> final no-review` (from `feature_review`) to `gates.log`, sets `final_gate`, and returns the line without its newline. It is refused unless `final_due`.

- [ ] **Step 1: Write the failing tests:**
  - `init` stores the block with `final_gate: False`.
  - `init` refuses `final_merge="later"` and branch `"a b"`.
  - `load` of a state without `integration` and without unit `integration` keys gives `None` and `False`. Write `run.json` by hand from a 0.4.0-shaped dict.
  - `unit_base` returns `integration/x` in the mode and `main` without it.
  - `add_unit(integration=True)` refusals: mode off; a second integration unit; `prereqs=["a"]`; `branch="other"`. Its default branch equals `state["integration"]["branch"]`.
  - `add_unit("b", prereqs=["int"])` and `after=["int"]` are refused when `int` is the integration unit.
  - `set_option(state, "final-merge", "owner")` and `("feature-review", "no")` update the block. Both are refused once `final_gate` is True, and when the mode is off. `("feature-review", "maybe")` is refused.
  - With `wip=1` and one other open unit, `openable` still lists the integration unit.
  - `final_due`:
    - False while one unit is open and undone;
    - True once it is done;
    - False when the only other unit is stopped (and `all_stopped` is True then);
    - False after `open_final`.
  - `open_final` writes `int final review` to `gates.log` when `feature_review` is True, and `int final no-review` otherwise. A second call is refused.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest plugins/implementation-manager/tests/test_runstate.py -v`
Expected: the new tests FAIL; existing ones pass.

- [ ] **Step 3: Implement the interfaces above in `runstate.py`.** Unit records gain `"integration": False` in `add_unit`'s new-record dict. `openable` treats `unit["specs"] or unit["integration"]` the same way in both the `in_flight` count and the cut.

- [ ] **Step 4: Run all implementation-manager tests**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests`
Expected: all OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts/runstate.py plugins/implementation-manager/tests/test_runstate.py
git commit -m "feat(implementation-manager): integration mode, unit kind and final gate in run state"
```

### Task 5: Run state: merge block, synced pushes and queue priority

**Files:**
- Modify: `plugins/implementation-manager/scripts/runstate.py` (`go`, `redo`, `stop_gate`, `queue_order`, `reportable`, new functions)
- Modify: `plugins/implementation-manager/scripts/watcher.py` (`take_request`: pop `go_sha` beside `go_head`)
- Test: `plugins/implementation-manager/tests/test_runstate.py` (new class `IntegrationMergeTests`)

**Interfaces:**
- Consumes: Task 4's `integration_unit`, `late_units`.
- Produces:
  - `go(...)`: for the integration unit only, also sets `marks[unit]["go_sha"] = sha`.
  - `redo`, `stop_gate` and `take_request` pop `go_sha` wherever they pop `go_head`. `hold` and `yield_slot` keep it: the `go` stays outstanding.
  - `sync_in_flight(state) -> bool`: the integration unit has a `pending` entry or `go_sha` in its marks. A `queue` entry does not count: blocking the slot holder's merge behind a queued sync would deadlock serial mode.
  - `integration_block(state, unit) -> str | None`:
    - `"integration sync in flight"` for a non-integration unit while `sync_in_flight`;
    - `"waits on <a>, <b>"` for the integration unit while `late_units` is non-empty;
    - else None. Always None when the mode is off.
  - `reportable(state, unit)`: also False when `integration_block` is not None.
  - `queue_order(state)`: integration-unit entries first, then the existing order.
  - `record_synced(state, unit, sha) -> None`: refuses a non-integration unit (`only the integration unit pushes directly`) and a sha that isn't its `go_sha` (`no outstanding go for <sha7>`). It pops `go_sha` and `go_head`, and frees the slot if the unit holds it.

- [ ] **Step 1: Write the failing tests:**
  - After `go(int, SHA1)`, `sync_in_flight` is True and `integration_block(state, "a")` is `"integration sync in flight"`. `reportable(state, "a")` is False in parallel mode too.
  - `record_synced(state, "int", SHA1)` clears it and frees the slot.
  - `record_synced` with `SHA2` is refused, and so is `record_synced(state, "a", SHA1)`.
  - `hold(int)` after `go` keeps `go_sha`; `redo(int, SHA1, "x")` clears it.
  - A queued request from `int` leaves `sync_in_flight` False.
  - `enqueue` order `a`, `b`, then `int` gives `queue_order` starting with `int`, and `queue_head` is `int`.
  - With unit `a` open and undone, `integration_block(state, "int")` is `"waits on a"`; once `a` is done, None.
  - In a run without the mode, `integration_block` is None for every unit and `go` sets no `go_sha`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest plugins/implementation-manager/tests/test_runstate.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement the interfaces above.**

- [ ] **Step 4: Run all implementation-manager tests**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests`
Expected: all OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts plugins/implementation-manager/tests/test_runstate.py
git commit -m "feat(implementation-manager): integration syncs block unit merges and jump the queue"
```

### Task 6: Watcher: `SYNCED` and `FINAL-READY`

**Files:**
- Modify: `plugins/implementation-manager/scripts/prstate.py` (new `branch_head`)
- Modify: `plugins/implementation-manager/scripts/watcher.py` (`_poll_snapshot`, `_apply_poll`, `pr_event`, `_unit_events`, the kinds list)
- Modify: `plugins/implementation-manager/tests/fakes.py` (`FakeGh` answers branch-head calls)
- Test: `plugins/implementation-manager/tests/test_prstate.py`, `plugins/implementation-manager/tests/test_watcher.py` (new class `IntegrationWatcherTests`)

**Interfaces:**
- Consumes: Task 5's `go_sha` mark and `record_synced`.
- Produces:
  - `branch_head(repo: str, branch: str, gh) -> str`: runs `gh api repos/<repo>/branches/<branch> --jq .commit.sha`. A result that isn't a 40-hex sha raises `GhError`.
  - `FakeGh(pr_list, polls=(), branches=None)`: `branches` maps a branch name to a queue of answers for that call (the last repeats).
  - A new event kind `SYNCED`, ordered after `ENDED` and before `ORPHAN`. Its line is `SYNCED <unit> <sha>`, reported once per sha (mark `synced_sha`), when the integration unit has `go_sha` and the polled branch head equals it. This happens whether or not the unit has an open PR, and whether or not it holds the slot.
  - `FINAL-READY <unit> <pr> <sha>`: printed in place of `READY` for the integration unit when `final_merge == "owner"`. It uses the same `ready_head` mark, and that unit gets no `REMIND`.

- [ ] **Step 1: Write the failing tests:**
  - `test_prstate`: `branch_head` returns the sha from a scripted answer and raises `GhError` on `"Not Found"`.
  - Watcher: a serial run with `int` holding the slot after `go(int, SHA1)` and no PR, branch head `SHA1`. `wait` returns `SYNCED int <SHA1>`; a second `wait` (with `allow`) does not repeat it.
  - Watcher: branch head still the old sha means no `SYNCED`, and after `no_push_seconds` the existing `STUCK int - no-push`.
  - Watcher (Review Focus 2): the final PR is open (PR 7 on `integration/x`), and `go(int, SHA1)`. The poll shows the PR head `SHA1` and branch head `SHA1`. The result is `SYNCED int <SHA1>`, not `READY`.
  - Watcher: `final_merge: owner` with a clean final PR and no late units gives `FINAL-READY int 7 <head>`. After `remind_seconds`, no `REMIND`.
  - Watcher: with unit `a` open and undone, a clean final PR gives no `READY` or `FINAL-READY`.
  - Watcher: a unit PR clean while `int` has `go_sha` gives no `READY` for it.
  - Watcher: in a run without the mode, no branch-head call is made (`gh.count(["api", "repos/o/r/branches/..."])` stays 0; match on the `repos/` prefix).

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest plugins/implementation-manager/tests/test_watcher.py plugins/implementation-manager/tests/test_prstate.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement:**
  - `_poll_snapshot` also returns `heads: dict[str, str]`: one `branch_head` call for the integration unit when its marks hold `go_sha`. `_apply_poll` passes them through for units whose identity is unchanged.
  - `pr_event` takes `synced: dict[str, str] | None` and adds the `SYNCED` candidates.
  - `_unit_events` swaps the `READY` prefix and skips `REMIND` for an owner-merged integration unit; `reportable` already blocks the other cases.

- [ ] **Step 4: Run all implementation-manager tests**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests`
Expected: all OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts plugins/implementation-manager/tests
git commit -m "feat(implementation-manager): watcher reports SYNCED and FINAL-READY"
```

### Task 7: `run.py` command line

**Files:**
- Modify: `plugins/implementation-manager/scripts/run.py` (module docstring, `_parser`, `_run`, `_status`, `_needs`)
- Test: `plugins/implementation-manager/tests/test_run_cli.py` (new class `IntegrationCliTests`)

**Interfaces:**
- Consumes: everything from Tasks 4–6.
- Produces:
  - `init ... --integration BRANCH --final-merge manager|owner --feature-review yes|no`. Either choice without `--integration`, or `--integration` without both, is a refusal (exit 1, one stderr line).
  - `unit <dir> <name> --integration`.
  - `set <dir> final-merge <manager|owner>` and `set <dir> feature-review <yes|no>`.
  - `synced <dir> <unit> <sha>` calls `record_synced`.
  - `open-ready` prints the opened gate names, then `open_final`'s line when `final_due`.
  - `status` adds these lines after `- gated:`:
    - `- integration: <branch> into <base> · final merge: <manager|owner> · feature review: <yes|no>`;
    - `- integration sync in flight`, when `sync_in_flight`;
    - `- final PR #<pr> waits on <units>`, when the integration unit has an open PR and `late_units`;
    - `- final PR #<pr> ready: the owner merges it`, when `final_merge` is owner and its `ready_head` matches its head.
  - `_needs` adds `final gate for <unit> may open: run open-ready` when `final_due`, and `every unit stopped: ask the owner what to do with <branch>` when `all_stopped`. The existing `PR ready since` line is skipped for a unit with an `integration_block`, and for an owner-merged integration unit.
  - `check <dir> <unit>`:
    - A `READY <pr> <sha>` line becomes `NOT-READY <pr> head=<sha7> blocked: <reason>` when `integration_block` is set.
    - Otherwise it becomes `FINAL-READY <pr> <sha>` for an owner-merged integration unit.

- [ ] **Step 1: Write the failing tests** (subprocess style, as the existing `CliTestCase`):
  - init refusals: `--final-merge owner` alone, `--integration integration/x` alone, `--integration` with only `--final-merge`.
  - init plus `unit int --integration` plus `unit a`. `status` contains the integration line with `final merge: owner · feature review: no`.
  - `set final-merge manager` changes the status line. After a manual `open_final` (via `merged` and `open-ready`), `set` exits 1.
  - `open-ready` prints `int` (and `a`). After `merged a 7`, the next `open-ready` prints `int final no-review`, and `gates.log` ends with that line.
  - `stop a reason`: `status` lists `every unit stopped: ask the owner what to do with integration/x`, and `open-ready` prints nothing.
  - `go int <SHA1>`: `status` shows `- integration sync in flight`. Then `synced int <SHA1>` and status no longer shows it. `synced int <SHA2>` exits 1.
  - `check int` with the fake gh answering a clean PR:
    - `FINAL-READY 7 <head>` under owner;
    - `READY 7 <head>` under manager;
    - `NOT-READY 7 head=<sha7> blocked: waits on a` while `a` is open.
  - A run made without `--integration` prints no `integration` status line (Review Focus 4).

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m unittest plugins/implementation-manager/tests/test_run_cli.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement the interfaces above,** and update the module docstring's command list.

- [ ] **Step 4: Run all tests and ruff**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests && ruff check . && ruff format --check .`
Expected: all OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts/run.py plugins/implementation-manager/tests/test_run_cli.py
git commit -m "feat(implementation-manager): integration-mode commands and status in run.py"
```

### Task 8: Skill text, README, version and docs

**Files:**
- Modify: `plugins/implementation-manager/skills/implementation-manager/SKILL.md`, `plugins/implementation-manager/README.md`, `plugins/implementation-manager/.claude-plugin/plugin.json`
- Create: `docs/deferred/D-0003-implementation-manager-integration-eval.md`, `docs/tech-debt/TD-0002-integration-merge-check-second-opinion.md`

**Interfaces:**
- Consumes: the commands and event lines from Tasks 1–7, exactly as named there.

- [ ] **Step 1: Edit SKILL.md.** Keep every existing rule; add these.
  - **Run script table:** add rows for the new `init` options, `unit --integration`, the two `set` keys, `synced`, and `open-ready`'s final line.
  - **Setup.** Add an "Integration-branch mode" subsection, used only when the owner asks for it:
    - **Choices.** Ask in the reply text for who merges the final PR and whether to run a whole-feature review, unless the owner said. Never assume; setup waits for the answer.
    - **CI check.** Check the workflow `pull_request` triggers for the integration branch, and stop and tell the owner if they exclude it.
    - **Create the branch.** The manager does this itself, before starting any session. Run `gh api repos/<repo>/git/refs -f ref=refs/heads/integration/<slug> -f sha=<base head>`, then `git fetch origin integration/<slug>:integration/<slug>` so a local branch exists for `source_branch`.
    - **Init.** `run.py init` with the three options, then `run.py unit integration --integration --title "integration"`.
    - **Planning PR.** Retarget an open `specs` PR with `gh pr edit <pr> -R <repo> --base integration/<slug>`.
    - **Skills.** Confirm `branch-review:record` is available, next to `check` and `review`.
  - **Starting children in integration mode.** Start the integration child first, then every unit child, all with `source_branch: integration/<slug>` beside the existing `start_session` arguments.
  - **Unit prompt in integration mode.**
    - Wherever `<base>` appears in the unit prompt, use the integration branch, including the `/branch-review:review --base origin/<integration branch>` command.
    - Part 3's and part 8's `sync_with_base_branch` stays as it is, with no `base` argument: its default is the branch the worktree was cut from, then the PR's base, and both are the integration branch.
    - Add one line to part 8: if the tool refuses because the sync touches sandbox-protected files, end `[blocked]` with "blocks merge: no" and the path.
  - **A new "The integration child" section** with its prompt. It reuses parts 8–10 word for word, with `<name>` set to the integration unit, plus:
    - **Goal and branch.** Its worktree is cut from `integration/<slug>`; it runs `git switch integration/<slug>` at start, and its push requests name that branch.
    - **Gate loop.** Matches `^<name> (open|stop|final)`.
    - **Watch loop.** One background poll that ends when `<base>`'s remote head (`git ls-remote origin refs/heads/<base>`) differs from the last one merged, or when a `final` or `stop` line appears. After the final PR opens, it also ends when the integration branch's remote head moves.
    - **On a `<base>` change:**
      1. Fetch and fast-forward.
      2. `branch-review:record` with the merged PR numbers from `git log --first-parent --format=%s <last recorded>..HEAD`.
      3. Merge `<base>` with `sync_with_base_branch` and `base: <base>`, the default branch the tool allows.
      4. Clean: ask to push. Conflicts: resolve, commit, then `/branch-review:review --base origin/<base>` to convergence, then ask to push.
    - **On `go`.** If the remote integration branch moved past the merge's first parent: `git reset --hard origin/integration/<slug>` (its own unpushed merge only), then record, merge again and ask again. Otherwise push HEAD to `integration/<slug>`, never force.
    - **On the final line.** One last sync. Then, if the line says `review`, run `/branch-review:review --full --base origin/<base>` with the run's intent, fix findings and escalate as a unit would. Ask to push, open the final PR into `<base>`, and turn on Auto-fix.
    - **After the final PR is open.** A late unit merge moves the head: fetch, then review (final line `review`) or record (`no-review`). No push.
    - **Status tokens.** Add `[watching]`.
  - **Event table rows:**
    - `SYNCED <unit> <sha>`: `run.py synced`, one line.
    - `FINAL-READY <unit> <pr> <sha>`: tell the owner once, with the PR link, under "Waiting on you"; never merge it.
    - The `open-ready` final line: nothing to send.
  - **Needs list:** the two new `_needs` lines. The status line `every unit stopped` means asking the owner whether to close the integration branch.
  - **Late work.** In integration mode every card, added unit and ORPHAN targets the integration branch.
  - **Merge bar:** add "the run is not in integration mode, or `run.py status` lists no `integration sync in flight` for a unit PR, no `final PR … waits on` for the final PR, and the final PR is not owner-merged".
  - **Finishing:** delete the integration branch after the final PR merges (`gh api -X DELETE repos/<repo>/git/refs/heads/integration/<slug>`).
  - **Tracker:** add the line `- Integration: <branch> into <base> · final merge: … · feature review: …` in integration mode.
  - **Red flags:**
    - "The final PR is ready; merge it" → only when the owner chose `manager`; `FINAL-READY` is never merged.
    - "I'll sync main into the integration branch myself" → the integration child does it; the manager writes no code.
- [ ] **Step 2: Edit README.md:** add an "Integration-branch mode" section (what it is, the two choices, the integration child, `record`, late work, the final PR), and note that branch-review 0.10.0 is required. Set `"version": "0.5.0"` in `plugin.json`. If the Claude Code plugin manifest docs show that `dependencies` entries accept a version constraint, require branch-review `>=0.10.0` there; if they don't, leave the plain name, and rely on setup's `branch-review:record` check.
- [ ] **Step 3: Write `docs/deferred/D-0003-implementation-manager-integration-eval.md`,** in the format of `D-0001`: no eval case for integration mode in this release. The record-then-merge sequence is covered by unit tests and the skill text; an eval needs a scaffold with a remote and two branches, which the eval sandbox setup does not have yet.
- [ ] **Step 4: Write `docs/tech-debt/TD-0002-integration-merge-check-second-opinion.md`,** in the format of `TD-0001`. A merge-check round on the integration branch that finds a critical or major issue makes branch-review's automatic second opinion due. That is a deep review of the whole integration branch against `main`: a whole-feature review the owner may not have asked for. It's accepted for now because such findings are rare, and the second opinion is the safe direction. A fix would scope second-opinion triggers to findings in non-recorded deltas.
- [ ] **Step 5: Validate**

Run: `claude plugin validate plugins/implementation-manager && claude plugin validate . && python3 -m unittest discover -s plugins/implementation-manager/tests && python3 -m unittest discover -s plugins/branch-review/tests && grep -nE '\$[0-9]' plugins/implementation-manager/skills/implementation-manager/SKILL.md plugins/branch-review/skills/record/SKILL.md`
Expected: validation passes, tests OK, grep prints nothing.

- [ ] **Step 6: Commit**

```bash
git add plugins/implementation-manager docs/deferred docs/tech-debt
git commit -m "feat(implementation-manager): integration-branch mode in the skill (0.5.0)"
```
