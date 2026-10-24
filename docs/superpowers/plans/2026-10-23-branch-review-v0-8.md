# Branch review v0.8 (recall and mechanics) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship branch-review 0.8.0. It adds an automatic second-opinion deep round, a stricter reviewer, scoped merge-check rounds, an `outside` status, the intent check in the summary, an `rs` wrapper, and fixes to `status`, the round cap and report recording.

**Architecture:**
- Deterministic behavior lives in `scripts/review_state.py`, with unit tests: second-opinion "due", the cap, merge-check scope, generated files, large-delta promotion, the `outside` migration and the summary.
- `scripts/rs` is a thin `sh` wrapper over that script.
- Reviewer and orchestrator behavior lives in Markdown: `agents/reviewer.md`, `skills/review/SKILL.md`, `skills/review/reviewer-checklist.md` and `skills/review/finding-schema.md`.
- The `review-loop` eval checks the whole loop.

**Tech Stack:** Python 3.9+ standard library, POSIX `sh`, git ≥ 2.40 (with fallbacks), unittest, ruff, `claude plugin validate`, `claude plugin eval`.

**Spec:** `docs/superpowers/specs/2026-10-23-branch-review-recall-and-mechanics-design.md`. Read it before any task; section ids (A1, B5, …) below refer to it.

## Global Constraints

- Plugin scripts are Python 3.9+, standard library only, invoked through `python3`.
- Tests live in `plugins/branch-review/tests/` and run with `python3 -m unittest discover -s plugins/branch-review/tests`.
- Run `ruff check .` and `ruff format .` from the repo root before each commit that touches Python.
- Run `claude plugin validate .` and `claude plugin validate plugins/branch-review` before the final commit.
- `plugins/branch-review/.claude-plugin/plugin.json` `version` becomes `0.8.0`.
- Every command keeps the v0.7 contract: it prints one JSON object and exits 0; errors print one stderr line and exit 1.
- v0.7 `review.json` files must load and continue: a follow-up on v0.7 state works, and `covered` gives the same answer as v0.7 until a v0.8 round is recorded.
- Second-opinion triggers apply only over the current cycle: deep `merge_risk.level` ∈ {`moderate`, `high`}, or a `change`-scope finding of severity ∈ {`critical`, `major`}. There are no size or path triggers.
- Large-delta threshold: `LARGE_FILES = 40` non-generated files or `LARGE_LINES = 2000` changed lines in them.
- `MAX_ROUNDS = 6`. The forced-deep reasons are `snapshot-missing`, `history-rewritten` and `paths-changed`.
- Skill text follows the superpowers:writing-skills standard. Keep `skills/review/SKILL.md` within about 15% of its v0.7 length (153 lines) by moving detail into the checklist and schema.
- The reviewer keeps its tools (Read, Grep, Glob, Bash; no Write or Edit), and never modifies tracked files.

## Review Focus

1. **v0.7 state, then a v0.8 run.** A `review.json` with deep rounds that carry no `plugin_version`, plus an outside finding stored as `deferred`. A v0.8 `locate`, a follow-up, `status` and `covered` must load it, map the finding to `outside`, report no second opinion due, and give the v0.7 `covered` verdict. *(Task 1 and Task 2 tests.)*
2. **Escalated majors only.** Round 1 finds one major and the orchestrator escalates it. `status` must ask for a second opinion, not loop on `new-findings`. After a second-opinion round that finds nothing, `status` is `converged`, and `covered` still says `escalated` (and not `second-opinion-pending`). *(Task 2 test.)*
3. **Modify/delete conflict.** The base modifies a file the branch deleted. `locate` must report `merge-check` with that path in `conflicts`, and `begin-round --mode merge-check` must put the path in `changed_files`. *(Task 5 test.)*
4. **`rs` from a subdirectory, and outside any repo.** From a subdirectory it must pass the repo's top level as `--repo`. Outside any git repo, `rs list` must still run (no `--repo` added). *(Task 7 test.)*
5. **Paths with spaces and non-ASCII names** in generated-file detection and the conflict list. They must round-trip exactly, with `-z` parsing and no quoting artifacts. *(Task 4 and Task 5 tests.)*

---

### Task 1: `outside` status, migration and its summary section (spec B6)

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py`: `STATUSES`, `load_state`, `cmd_record_findings`, `render_summary`
- Test: `plugins/branch-review/tests/test_ledger.py`, `plugins/branch-review/tests/test_covered.py`

**Interfaces:**
- Produces:
  - `STATUSES = ("open", "fixed", "still-open", "verified", "invalid", "escalated", "deferred", "outside")`.
  - `_migrate(state: dict) -> dict`, called by `load_state` on every load. It changes each finding with `scope == "outside"`, `status == "deferred"` and resolution note `"outside the change set"` to `status: "outside"` and `resolution.status: "outside"`. Idempotent.

- [ ] **Step 1: Write the failing tests** in `test_ledger.py`:
  - Rename `test_record_outside_change_is_deferred` to `test_record_outside_change_is_outside`. Assert the finding's `status == "outside"`, `resolution["status"] == "outside"`, and `self.cli("status")["counts"]["outside"] == 1`.
  - `test_load_maps_v07_outside_deferred_to_outside`: write a v0.7-shaped `review.json` by hand (one recorded deep round without `plugin_version`; one finding with `scope: "outside"`, `status: "deferred"`, resolution note `"outside the change set"`). Assert `status` reports `counts["outside"] == 1` and `counts["deferred"] == 0`.
  - `test_developer_deferred_stays_deferred`: an escalated finding resolved with `--decision defer` keeps `status == "deferred"` after a reload.
  - `test_set_status_escalated_from_outside`: `set-status --id <outside id> --status escalated --note n` gives `escalated`. Then `status` with no open findings is `converged` (the escalation does not loop).
  - `test_summary_lists_outside_section`: `summary` text contains `"## Outside the change"` between `"## Invalid"` and `"## Fixed and verified"`, holding the outside finding's id. That id is absent from `"## Deferred"`.
- [ ] **Step 2: Add a test to `test_covered.py`:** `test_outside_finding_is_covered`. A clean deep round whose only finding is outside is covered.
- [ ] **Step 3: Run the tests** with `python3 -m unittest discover -s plugins/branch-review/tests -k outside -k v07 -k developer_deferred`. Expected: FAIL (status `deferred`, no section).
- [ ] **Step 4: Implement it.**
  - Add `"outside"` to `STATUSES`.
  - Write `_migrate` and call it from `load_state` before returning.
  - In `record-findings`, give outside entries `status: "outside"` and `resolution.status: "outside"`; keep the note `"outside the change set"`.
  - In `render_summary`, add the section tuple `("Outside the change", "outside", _resolution_note)` after `"Invalid"`.
- [ ] **Step 5: Run the full suite:** `python3 -m unittest discover -s plugins/branch-review/tests`. Expected: PASS. Update any v0.7 test that asserted `deferred` for outside findings.
- [ ] **Step 6: Commit:** `feat(branch-review): outside status for findings outside the change`.

### Task 2: Plugin version, second-opinion record and "due" (spec A1)

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py`: begin-round parser and `cmd_begin_round`, `cmd_status`, `cmd_covered`
- Test: `plugins/branch-review/tests/test_second_opinion.py` (new), `plugins/branch-review/tests/test_covered.py`

**Interfaces:**
- Consumes: `STATUSES` from Task 1.
- Produces:
  - `PLUGIN_VERSION: str`, read once from `../.claude-plugin/plugin.json` relative to the script (`"unknown"` if unreadable).
  - `FORCED_DEEP = ("snapshot-missing", "history-rewritten", "paths-changed")`.
  - `_cycle_start(rounds: list) -> int`: the number of the last recorded round whose `mode_reason` is in `FORCED_DEEP`, else `1`.
  - `second_opinion_due(state: dict) -> tuple[bool, list[str]]`: reasons from `("requested", "merge-risk", "major-finding")`, in that order.
  - `begin-round` gains:
    - `--focus {second-opinion}`, stored as round `focus`;
    - `--mode-reason REASON`, stored as round `mode_reason`;
    - `--second-opinion` and `--no-second-opinion` (mutually exclusive), stored in top-level `second_opinion: {"requested": int | None, "skipped": bool}`;
    - every new round gets `plugin_version: PLUGIN_VERSION`.
  - `status` output gains `next_mode` (str or null), `next_focus` (str or null) and `second_opinion_reasons` (list).
  - New `status` reason: `"second-opinion"`.
  - `covered` gains the reason `"second-opinion-pending"`, listed last.

**`second_opinion_due` rules, exactly:**
- `(False, [])` when no recorded round has `plugin_version` (the v0.7 exemption).
- `requested = state["second_opinion"]["requested"]`. When it is set and no recorded round with `focus == "second-opinion"` has `number >= requested`, the result includes `"requested"`.
- When `skipped` is true, or any recorded round has `focus == "second-opinion"`, there are no automatic reasons.
- Otherwise, over the recorded rounds numbered `>= _cycle_start(rounds)`:
  - `"merge-risk"` when a round with `mode == "deep"` has `merge_risk.level` in `("moderate", "high")`;
  - `"major-finding"` when a finding with `scope == "change"`, `round_opened >= cycle start` and severity in `("critical", "major")` exists, whatever its status.
- Due is `bool(reasons)`.

**Flag storage:**
- `--second-opinion` sets `requested` to the new round's number and `skipped` to false.
- `--no-second-opinion` sets `skipped` to true and `requested` to None.

- [ ] **Step 1: Write the failing tests** in the new `test_second_opinion.py`, reusing `LedgerTest`'s helpers. Import them, or move `finding`, `output`, `report` and the `begin`/`record`/`set_status` helpers into `helpers.py`.
  - `test_low_risk_minor_only_is_not_due`: deep round, `merge_risk.level == "low"`, one minor fixed and verified. `status` gives `converged`, `second_opinion_reasons == []`.
  - `test_moderate_risk_is_due_at_convergence`: deep round with `"moderate"`, no findings. `status` gives `continue: True`, `reason: "second-opinion"`, `next_mode: "deep"`, `next_focus: "second-opinion"`, `second_opinion_reasons: ["merge-risk"]`.
  - `test_major_finding_is_due_even_when_escalated`: one major, set to escalated. `status` gives `reason: "second-opinion"`, reasons `["major-finding"]`. *(Review Focus 2.)*
  - `test_second_opinion_round_clears_due`: after the case above, `begin --focus second-opinion` and record no findings. `status` gives `converged`, and `covered` reasons include `"escalated"` and not `"second-opinion-pending"`. *(Review Focus 2.)*
  - `test_only_one_automatic_second_opinion`: after a second-opinion round, a new follow-up records a major. Once it is fixed and verified, `status` is `converged`.
  - `test_requested_overrides_skip_and_repeats`: `--no-second-opinion` on round 1 (moderate) gives `converged`. A later `begin-round --second-opinion` gives `reason: "second-opinion"` with `["requested"]` once that round converges.
  - `test_v07_state_is_exempt`: hand-written v0.7 state with a `"moderate"` deep round and no `plugin_version`. `status` is `converged`, and `covered` is unchanged from v0.7. *(Review Focus 1.)*
  - `test_forced_deep_starts_new_cycle`: a moderate deep round, then a deep round with `--mode-reason history-rewritten` rated `"low"` with no findings. `second_opinion_due` is `(False, [])` for the new cycle when no second opinion ran.
  - `test_begin_round_records_plugin_version_focus_and_reason`: the round has `plugin_version == review_state.PLUGIN_VERSION`, `focus == "second-opinion"`, `mode_reason == "continuing"`.
- [ ] **Step 2: Add to `test_covered.py`:** `test_second_opinion_pending_blocks`. A v0.8 moderate deep round, converged, gives `covered: False` with `reasons == ["second-opinion-pending"]`.
- [ ] **Step 3: Run the tests.** `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_second_opinion.py'`. Expected: FAIL.
- [ ] **Step 4: Implement the pieces in the Interfaces block.**
  - In `cmd_status`, where v0.7 returns `converged`, call `second_opinion_due` and return `second-opinion` when due.
  - Also check it when the only non-terminal findings are `escalated` (they never block `second-opinion`).
  - In `cmd_covered`, append `"second-opinion-pending"` when due.
- [ ] **Step 5: Run the full suite.** Expected: PASS.
- [ ] **Step 6: Commit:** `feat(branch-review): second-opinion decision from recorded state`.

### Task 3: `status` convergence and the round cap (spec B3, B4)

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py`: `cmd_status`
- Test: `plugins/branch-review/tests/test_ledger.py`

**Interfaces:**
- Consumes: `_cycle_start`, `second_opinion_due` and `FORCED_DEEP` from Task 2.
- Produces:
  - `_cap_floor(rounds: list, since_round: int) -> int`. It returns `max(since_round, _cycle_start(rounds) - 1)`, where the cycle start counts only when a forced round exists; otherwise it returns `since_round`.
  - Status reasons: `open-findings`, `unverified-fixes`, `second-opinion`, `final-verification`, `converged`, `max-rounds`.
  - `new-findings` is no longer produced. Its v0.7 job is covered by `open-findings`, and it looped on escalated-only rounds.

**Cap rule:**
- `n` is the number of recorded rounds numbered above `_cap_floor`.
- When the loop would continue and `n >= MAX_ROUNDS`:
  - `second-opinion` stays allowed when no second-opinion round has been recorded;
  - `unverified-fixes` becomes `final-verification` (with `next_mode: "followup"`) only when `n == MAX_ROUNDS`;
  - everything else becomes `max-rounds`.

- [ ] **Step 1: Write the failing tests:**
  - Replace `test_status_continue_when_last_round_added` with `test_status_new_findings_all_invalid_or_escalated_converges`: both findings were set to invalid or escalated, and the round's risk was low, so the result is `converged`.
  - `test_cap_counts_from_last_forced_deep_round`: five rounds with open findings, then a deep round with `--mode-reason history-rewritten` with open findings. The result is `continue: True`.
  - `test_final_verification_at_cap`: six rounds; the last leaves only `fixed` findings. The result is `reason: "final-verification"`, `next_mode: "followup"`. After a seventh round that verifies them, it is `converged` (or `second-opinion` if due).
  - `test_second_opinion_runs_past_cap`: six rounds, the first `"moderate"`, converged on round 6. The result is `reason: "second-opinion"`.
  - Keep `test_status_max_rounds` and `test_status_max_rounds_counts_rounds_after_since_round` passing.
- [ ] **Step 2: Run them.** `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_ledger.py'`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement `_cap_floor` and the cap rule in `cmd_status`.**
- [ ] **Step 4: Run the full suite.** Expected: PASS.
- [ ] **Step 5: Commit:** `fix(branch-review): status stops on escalated-only rounds; cap counts from forced restarts`.

### Task 4: Generated files, large-delta promotion and oversized (spec A3)

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py`: `cmd_begin_round`, the begin-round parser
- Test: `plugins/branch-review/tests/test_snapshot.py`, or a new `test_generated.py`

**Interfaces:**
- Produces:
  - `LARGE_FILES = 40`, `LARGE_LINES = 2000`.
  - `generated_files(repo: Path, snapshot: str, files: list[str], globs: list[str]) -> set[str]`. A file is generated when `git check-attr -z --source=<snapshot> linguist-generated diff -- <files>` reports `linguist-generated: set` or `diff: unset`, or when it matches one of `globs` (`path_matches`). If `--source` fails (git < 2.40), it reads attributes from the working tree.
  - `begin-round` gains:
    - `--generated GLOB...`, stored as top-level `generated` (given replaces, absent reuses the stored list);
    - `--deep-model NAME` (default: the `--model` value).
  - `begin-round` output gains `generated: list[str]`, `counted_files: int` and `counted_lines: int`. `oversized` uses the counted, non-generated values.
  - Promotion: on `--mode followup`, when the non-generated delta has `counted_files >= LARGE_FILES` or `counted_lines >= LARGE_LINES`, the round is saved with:
    - `mode: "deep"` and `mode_reason: "large-delta"`;
    - `diff_from` set to the merge base, and `changed_files` recomputed for the whole change set;
    - `model` set to the `--deep-model` value.

    The output shows the same.

- [ ] **Step 1: Write the failing tests:**
  - `test_linguist_generated_excluded_from_counts`: `.gitattributes` has `fixtures/** linguist-generated`, and the branch adds 50 files under `fixtures/` plus one source file, as a follow-up. The round stays `followup`. `counted_files == 1`, and all 51 files are in `changed_files`.
  - `test_followup_with_large_delta_is_promoted`: a follow-up delta of 40 non-generated files with `--model sonnet --deep-model opus` gives `mode == "deep"`, `mode_reason == "large-delta"`, `model == "opus"`, and `diff_from` equal to the merge base.
  - `test_generated_glob_is_stored_and_reused`: `--generated 'gen/**'` on round 1. The next begin-round without `--generated` still excludes `gen/` files.
  - `test_generated_paths_with_spaces_and_unicode`: `fixtures/a b/é.json` marked generated round-trips exactly in `generated`. *(Review Focus 5.)*
  - `test_oversized_ignores_generated`: 160 generated files plus 1 source file gives `oversized: False`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement `generated_files`, the stored globs, the counted values and the promotion in `cmd_begin_round`.** Parse `check-attr -z` output as NUL-separated triples (path, attribute, value).
- [ ] **Step 4: Run the full suite.** Expected: PASS.
- [ ] **Step 5: Commit:** `feat(branch-review): generated files and large-delta promotion`.

### Task 5: Merge-check rounds (spec B5)

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py`: `choose_mode`, `cmd_locate`, `cmd_begin_round`, the begin-round parser
- Test: `plugins/branch-review/tests/test_snapshot.py` (mode selection), `plugins/branch-review/tests/test_covered.py`

**Interfaces:**
- Consumes: `_numstat`, `_anchor`, `_merge_base`; Task 4's counted values (unchanged for merge-check).
- Produces:
  - `base_conflicts(repo: Path, anchor: dict, new_merge_base: str) -> list[str] | None`. It runs `git merge-tree --write-tree --name-only -z --merge-base=<anchor merge_base> <anchor snapshot> <new_merge_base>`. Exit 0 returns `[]`. Exit 1 returns the conflicted paths, de-duplicated and in order. Any other failure (including git < 2.40) returns `None`.
  - `choose_mode` returns:
    - `("merge-check", "base-moved")` when `base_conflicts` returns a non-empty list;
    - `("deep", "base-moved")` when it returns `None`;
    - the clean-update path as before.
  - `locate` output gains `conflicts: list[str]` (empty unless the mode is `merge-check`).
  - `begin-round --mode` accepts `merge-check`. It requires a recorded anchor; otherwise it errors with `cannot start a merge-check round: no recorded round`.
    - `diff_from = anchor["snapshot"]`, `diff_to = --snapshot`.
    - `changed_files` = (files in `diff_from..diff_to` that are also in `merge_base..diff_to`) ∪ `conflicts`, in diff order, with conflicts not already present appended.
    - The round stores `conflicts`. The output gains `conflicts` and `upstream_overlap`: the files in `anchor merge_base..new merge_base` that are also in `merge_base..diff_to`.
    - If `base_conflicts` returns `None` at begin-round, it errors with `cannot start a merge-check round: the base update cannot be simulated; run a deep round`.

- [ ] **Step 1: Write the failing tests:**
  - Rework the existing `base-moved` test in `test_snapshot.py` as `test_conflicting_base_merge_gives_merge_check`. `locate` gives `mode == "merge-check"`, `reason == "base-moved"`, and `conflicts == ["b.txt"]`.
  - `test_merge_check_scope_includes_branch_work_and_excludes_base_only_files`: since the anchor, the base adds `upstream.txt` (base-only) and conflicts on `b.txt`, and the branch adds `fix.txt`. `changed_files` contains `b.txt` and `fix.txt` and not `upstream.txt`.
  - `test_modify_delete_conflict_is_listed`: the branch deletes `b.txt`, and the base modifies it. `b.txt` is in `conflicts` and in `changed_files`. *(Review Focus 3.)*
  - `test_conflict_path_with_space_and_unicode`: a conflict on `dir a/é.txt` is listed exactly. *(Review Focus 5.)*
  - `test_merge_check_falls_back_to_deep_when_merge_tree_fails`: patch `review_state.base_conflicts` to return `None`. `locate` gives `mode == "deep"`, `reason == "base-moved"`.
  - In `test_covered.py`, `test_recorded_merge_check_round_covers_head`: a recorded merge-check round with no findings covers HEAD.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement `base_conflicts`, then the `choose_mode`, `locate` and `begin-round` changes.** Run git through `subprocess.run` directly, so the stdout of an exit-1 run is kept.
- [ ] **Step 4: Run the full suite.** Expected: PASS. `test_conflicting_base_merge_blocks` in `test_covered.py` still passes: the conflict is not reviewed yet.
- [ ] **Step 5: Commit:** `feat(branch-review): scoped merge-check rounds for conflicted base merges`.

### Task 6: Intent check and focus in the summary (spec B7)

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py`: `render_summary`
- Test: `plugins/branch-review/tests/test_ledger.py`

**Interfaces:**
- Produces:
  - `_intent_check_lines(state: dict) -> list[str]`, rendered as section `"## Intent check"` after `"## Open and unverified"`. It has two sub-lists:
    - `"Unmet claims:"`: from the latest round with `mode == "deep"` and every later round;
    - `"Behavior the intent does not mention:"`: the union over all rounds, de-duplicated by exact text, in first-seen order.

    Either sub-list shows `None.` when empty.
  - The header's mode reads `deep (second opinion)` for a round with `focus == "second-opinion"`, and `merge-check` for that mode.

- [ ] **Step 1: Write the failing tests:**
  - `test_summary_intent_check_section`: round 1 has an unmet claim `"A"` and unmentioned `"X"`. Round 2 (follow-up) has unmentioned `"X"` and `"Y"`. The summary has `"## Intent check"` with `A` once, and `X` then `Y`, each once.
  - `test_summary_header_names_second_opinion`: the header contains `last deep (second opinion) with`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement the section and the header text.**
- [ ] **Step 4: Run the full suite.** Expected: PASS.
- [ ] **Step 5: Commit:** `feat(branch-review): intent check in the summary`.

### Task 7: The `rs` wrapper (spec B1)

**Files:**
- Create: `plugins/branch-review/scripts/rs` (mode 100755: `chmod +x`, and check with `git ls-files -s`)
- Test: `plugins/branch-review/tests/test_rs.py`

**Interfaces:**
- Produces: `scripts/rs <command> [args…]`. It runs `python3 "<dir of rs>/review_state.py" "$@"`, appending:
  - `--state-dir "$CLAUDE_PLUGIN_DATA"` when no argument is `--state-dir` or starts with `--state-dir=`, and the variable is non-empty;
  - `--repo "<top level>"` when no argument is `--repo` or starts with `--repo=`, and `git rev-parse --show-toplevel` succeeds in the current directory.

  The exit status is the script's. POSIX `sh` only: no arrays, no `local`.

- [ ] **Step 1: Write the failing tests.** Run `subprocess.run(["sh", RS, ...])`, and the same under `zsh` with `@unittest.skipUnless(shutil.which("zsh"))`.
  - `test_rs_defaults_state_dir_and_repo`: in a fixture repo's root, with `CLAUDE_PLUGIN_DATA=<tmp>/state`, `rs locate` exits 0 and its JSON `dir` is under `<tmp>/state`.
  - `test_rs_from_subdirectory_uses_top_level`: run from `repo/sub/`, `repo_key` and `dir` match the run from the root. *(Review Focus 4.)*
  - `test_rs_outside_a_repo_adds_no_repo`: in a temp dir outside git, with `CLAUDE_PLUGIN_DATA` set, `rs list` exits 0. *(Review Focus 4.)*
  - `test_rs_explicit_args_win`: `rs locate --state-dir <other>` uses `<other>`.
  - `test_rs_without_plugin_data_reports_error`: `CLAUDE_PLUGIN_DATA` unset and no `--state-dir` exits 1, with one stderr line. Run it inside the `sandbox_cwd()` helper.
- [ ] **Step 2: Run them.** Expected: FAIL (no file).
- [ ] **Step 3: Write `scripts/rs`.**
- [ ] **Step 4: Run the full suite.** Expected: PASS.
- [ ] **Step 5: Commit:** `feat(branch-review): rs wrapper with state-dir and repo defaults`.

### Task 8: Reviewer agent and checklist (spec A1 focus, A2, A3, A4, A5, B2, B5, B9)

**Files:**
- Modify: `plugins/branch-review/agents/reviewer.md`, `plugins/branch-review/skills/review/reviewer-checklist.md`

**Interfaces:**
- Consumes these dispatch inputs (Task 9 writes the template):
  - `MODE` ∈ {`deep`, `followup`, `merge-check`};
  - `FOCUS` (absent, or `second-opinion`);
  - `REPORT_PATH`;
  - `VALIDATED` (optional);
  - `CONFLICTS` and `UPSTREAM_OVERLAP` (merge-check only);
  - `PRIOR_FINDINGS` (second-opinion only: id, title, status of every earlier finding).

- [ ] **Step 1: Write reviewer.md.**
  - Inputs: document the new ones above.
  - Deep process: add the A2 steps (break tests on purpose, untruncated doc greps, related tech-debt docs, try doc promises, broad mappings, finish the checklist, intent both ways including non-goals). Add the A4 rule for reading AGENTS.md (list sizes, read each relevant file in full one at a time, never several at once).
  - "Process for second-opinion focus": the whole deep process, with the four most-skipped steps first. Don't report a `PRIOR_FINDINGS` entry again.
  - "Process for merge-check mode": the follow-up process over `CHANGED_FILES`, plus the four B5 checks.
  - Follow-up process: the A3 setup (schema, checklist, AGENTS.md path), the grep for old wording, running the delta's tests, and the "not checked" line.
  - Evidence rules: one defect per finding; report it or drop it; check your own fix; no process items.
  - `VALIDATED`: skip that same full suite when its sha's tree matches `DIFF_TO`.
  - A new "Environment" section with the A5 notes. Extend Scratch files to cover containers and installed tools.
  - Output: write the complete report to `REPORT_PATH` with a Bash heredoc whose delimiter is `BRANCH_REVIEW_REPORT_<8 random hex>`, chosen so it does not occur in the report. Then return the same report as the final message.
- [ ] **Step 2: Write reviewer-checklist.md.**
  - Under `logic`, add "broad mappings" with the TD-0049 08P01 example (public SqlSource).
  - Under `tests`, add the "break it on purpose" requirement with the TD-0051 `?? 23L` example.
  - Under `docs`, add the untruncated-grep rule with the TD-0036 example.
  - Under "Outside the change", add: "A pre-existing claim or defect that the change repeats or relies on is part of the change", with the TD-0048 example.
  - Under "Not reported", add process items.
  - Keep examples from public repos only (SqlSource, vibefun).
- [ ] **Step 3: Check it.**
  - `grep -n "REPORT_PATH\|FOCUS\|merge-check\|VALIDATED" plugins/branch-review/agents/reviewer.md` shows each input documented and used.
  - The frontmatter still has `tools: Read, Grep, Glob, Bash` and `disallowedTools: Agent, Edit, Write, NotebookEdit`.
- [ ] **Step 4: Commit:** `feat(branch-review): stricter reviewer, second-opinion and merge-check processes`.

### Task 9: Review skill, check skill, schema, README and version (spec B1, B2, B6, B7, B8, B9, B10, Arguments)

**Files:**
- Modify:
  - `plugins/branch-review/skills/review/SKILL.md`
  - `plugins/branch-review/skills/check/SKILL.md`
  - `plugins/branch-review/skills/review/finding-schema.md`
  - `plugins/branch-review/README.md`
  - `plugins/branch-review/.claude-plugin/plugin.json`

**Interfaces:**
- Consumes everything from Tasks 1–8: the command names, flags, output keys and reasons listed in their Interfaces blocks.

- [ ] **Step 1: Write review/SKILL.md.**
  - **Setup:**
    - Commands are `"${CLAUDE_SKILL_DIR}/../../scripts/rs" <command>`. Never put a command in a shell variable.
    - `--state-dir` is passed only when the invoker gave one.
    - The `argument-hint` adds `--second-opinion`, `--no-second-opinion` and `--generated GLOB...`.
    - Every new run, including after a merge or a redo request, starts at section 2 and ends with `rs summary` (B10).
  - **Mode handling:**
    - `locate`'s `merge-check` mode is new.
    - `begin-round` gains `--mode-reason <locate reason>`, `--deep-model`, `--generated`, and the second-opinion flags on the run's first `begin-round`.
    - When `begin-round` returns `mode: deep` with `mode_reason: large-delta`, dispatch a deep round with that model.
    - `--focus second-opinion` is used when `status` says `next_focus: second-opinion`.
  - **Dispatch template:**
    - add `REPORT_PATH: <tmp>/round-<n>.md`, `FOCUS`, `VALIDATED`, `CONFLICTS`, `UPSTREAM_OVERLAP` and `PRIOR_FINDINGS`;
    - add one line: INTENT, OPEN_FINDINGS and CHANGED_FILES are inlined text, never paths.
  - **Record:**
    - Run `rs record-findings --file <REPORT_PATH> --report`.
    - If the file is missing or has no JSON block, Write the agent's final message to it (never create the file beforehand), then retry.
    - The re-emit exchange is unchanged.
  - **Triage:**
    - The three B8 rules: "developer decision" means escalate; a regression test must fail without the fix; notes start with `Checked: <what>`.
    - The B7 handling of unmentioned behavior.
    - The B6 handling of outside findings: escalate them when AGENTS.md forbids deferral, and fix them when the developer says so.
  - **Loop:** the reasons `second-opinion` and `final-verification`, with `next_mode`/`next_focus`. `new-findings` is gone.
  - **Report:** copy Escalated, Deferred, Invalid, Outside the change and Intent check into "Review notes".
  - **Red flags:** add rows for `$RS`, for creating the round file before the reviewer reports, and for settling a "developer decision" yourself.
- [ ] **Step 2: Write check/SKILL.md.** Switch the command to `rs covered`, and document `second-opinion-pending` in the reasons list (last).
- [ ] **Step 3: Write finding-schema.md.** Document:
  - the `outside` status and its migration;
  - round keys `focus`, `mode_reason`, `conflicts`, `plugin_version`, and `mode: merge-check`;
  - top-level `second_opinion` and `generated`;
  - the `status` output keys and reasons, the cap rule and `second_opinion_due`;
  - the `locate` key `conflicts` and its mode table row;
  - the `begin-round` promotion;
  - the `covered` reason.
- [ ] **Step 4: Write README.md.** Cover the new flags, the second opinion, merge-check, `outside` and the summary sections.
- [ ] **Step 5: Bump the version.** Set `plugin.json` `version` to `"0.8.0"`.
- [ ] **Step 6: Verify:**
  - `claude plugin validate .` and `claude plugin validate plugins/branch-review` print no errors;
  - `wc -l plugins/branch-review/skills/review/SKILL.md` is ≤ 176;
  - `python3 -m unittest discover -s plugins/branch-review/tests` passes.
- [ ] **Step 7: Commit:** `feat(branch-review): v0.8 skill, schema and docs`.

### Task 10: Eval update and run

**Files:**
- Modify:
  - `plugins/branch-review/evals/review-loop/graders/loop.md`
  - `plugins/branch-review/evals/review-loop/prompt.md` (timeout only, if needed)
  - `plugins/branch-review/evals/review-loop/case.yaml`
- Create: `plugins/branch-review/evals/review-loop/graders/rs-wrapper.md`, and `graders/verbatim-report.md` if the eval supports it (Step 2).

- [ ] **Step 1: Update the `loop.md` grader.** The fixture's off-by-one is a major, so a second opinion is due. Item 1 becomes: N ≥ 3 rounds, and the summary's header or body shows a second-opinion round ran (`deep (second opinion)`), with the last round a second opinion or a follow-up. Add an item: the summary has an "Intent check" section.
- [ ] **Step 2: Add graders.**
  - `rs-wrapper.md`, as `type: tool_used`, `tool: Bash`, `input_match: 'scripts/rs"? (locate|begin-round|record-findings|status)'`, `min: 4`.
  - The verbatim grader: ask the `claude-code-guide` agent whether `claude plugin eval` graders can read a file by glob, or the reviewer subagent's messages. If one can, add `verbatim-report.md`, checking that `state/*/*/rounds/1.md` holds the reviewer's prose and JSON (not a JSON-only file). If not, skip it and say so in the PR description.
- [ ] **Step 3: Raise the time limit.** Set `timeout_seconds` to `2700` in `prompt.md`'s frontmatter for the extra Opus round.
- [ ] **Step 4: Run the eval** per `/Users/michael/.claude/projects/-Users-michael-Projects-claude-skills/memory/plugin-eval-in-worktree-sessions.md`: `claude plugin eval` on `plugins/branch-review/evals/review-loop`, with `--scaffold` and the state dir inside the run workspace. Expected: every grader passes with the plugin, and the baseline without it fails as before.
- [ ] **Step 5: Commit:** `test(branch-review): eval covers the second opinion and the rs wrapper`.
