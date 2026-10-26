# branch-review v0.9 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship branch-review 0.9.0. It closes the review gaps, triage lapses and mechanics failures that the phase 8 SqlSource run exposed.

**Architecture:**

- **State script.** Most behavior lands in `plugins/branch-review/scripts/review_state.py`:
  - new report keys;
  - statuses and decisions;
  - second-opinion timing;
  - a generated Review notes file.
- **Prompts.** The reviewer's prompt and checklist get the new required steps.
- **Skill.** SKILL.md gets the new triage, loop and dispatch rules.
- **File layout.** No module split: the script keeps its one-file layout.

**Tech Stack:** Python 3.9+ standard library, `unittest`, ruff, POSIX sh (`rs`), Claude Code skill and agent markdown, `claude plugin eval`.

**Spec:** `docs/superpowers/specs/2026-10-25-branch-review-phase-8-findings-design.md` (approved 2026-10-25). Section ids (A1 to C6) below refer to it.

## Global Constraints

- Python 3.9+, standard library only, invoked through `python3`. Tests run with `python3 -m unittest discover -s plugins/branch-review/tests`.
- `ruff check .` and `ruff format .` are clean.
- `claude plugin validate .` and `claude plugin validate plugins/branch-review` are clean.
- `plugins/branch-review/.claude-plugin/plugin.json` `version` is `0.9.0`.
- v0.8 state loads unchanged. Its `deferred` findings stay `deferred`. Its reports, which have no `coverage` or `not_checked`, stay valid. Coverage is enforced only for rounds whose `plugin_version` is 0.9.0 or later.
- `covered` keeps its contract. `accepted` does not block it. Its verdict does not change; only its output gains fields.
- `summary.md` keeps the headings "Escalated", "Deferred", "Invalid", "Outside the change" and "Intent check", because the current implementation-manager copies them.
- The reviewer's tools stay Read, Grep, Glob and Bash (no Write or Edit), and it never modifies tracked files.
- No implementation-manager behavior change. The only edits there are the two eval fixture reports (Task 3).
- Every `rs` command line in a SKILL.md starts with `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_SKILL_DIR}/../../scripts/rs"`, as `tests/test_skill_text.py` checks.

## Review Focus

1. **A v0.8 branch continuing under 0.9.** Its rounds have `plugin_version` 0.8.x or none, and no per-round `intent`. Its findings have no `decision`, `variants` or `fix_options`. Every command must work on it: no coverage refusal on a follow-up, and no fix-options gate. Task 3 and Task 4 each add a v0.8-state test.
2. **An empty or whitespace-only `--intent-quote-file`.** The empty string occurs in every intent, so an unguarded check passes anything. It must be refused. Task 4 pins this.
3. **`--state-dir` in both positions with different values.** The one after the subcommand wins, and `rs` adds nothing when it sees either. Task 1 pins this.
4. **A held second opinion after the developer defers or accepts every escalation.** The next `status`, and the next `locate`, make it due. `--no-second-opinion` still suppresses it under the new early timing. Task 6 pins this.
5. **A state-changing command while the last round is begun but unrecorded.** `discuss` and `set-status` can run then. The automatic summary rewrite must skip quietly, not fail the command. Task 7 pins this.

---

### Task 1: Arguments in any order, version 0.9.0, `plugin_version` in `locate`

Spec: C2, and part of C4.

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (`_build_parser`, `main`, `cmd_locate`; new `_version_at_least`)
- Modify: `plugins/branch-review/.claude-plugin/plugin.json` (`version` → `0.9.0`)
- Test: `plugins/branch-review/tests/test_args.py` (new), `plugins/branch-review/tests/test_rs.py`

**Interfaces:**
- Produces:
  - `_version_at_least(version: str | None, floor: tuple[int, int, int]) -> bool`. It is false for `None` and for text that does not parse as `X.Y.Z`.
  - `V09 = (0, 9, 0)`.
  - `locate` output key `plugin_version: str`.

- [ ] **Step 1: Write the failing tests** in `tests/test_args.py`, calling `review_state.main` directly with `LedgerHarness`'s repo:
  - `test_state_dir_before_subcommand`: `main(["--state-dir", S, "--repo", R, "locate", "--base", "main"])` → `dir` starts with `S`.
  - `test_repo_before_subcommand`: same with only `--repo` before → `repo_key` is the fixture's `owner__repo`.
  - `test_after_wins_over_before`: `main(["--state-dir", S1, "locate", "--state-dir", S2, "--repo", R])` → `dir` starts with `S2`.
  - `test_default_state_dir_when_absent`: parse `["locate"]` with `_build_parser()` and apply the defaults step → `args.state_dir == DEFAULT_STATE_DIR` and `args.repo == "."`.
  - `test_version_at_least`: `("0.9.0", V09)` → True; `("0.8.1", V09)` → False; `("0.10.0", V09)` → True; `(None, V09)` → False; `("x", V09)` → False.
  - `test_locate_reports_plugin_version`: `locate` output `plugin_version == review_state.PLUGIN_VERSION == "0.9.0"`.
- [ ] **Step 2: Add to `tests/test_rs.py`** `test_state_dir_before_command_is_not_doubled`: `rs --state-dir <state> locate` with `CLAUDE_PLUGIN_DATA` set to a different dir → exit 0, and `dir` is under `<state>`.
- [ ] **Step 3: Run them and see them fail**
  - Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_args.py' -v`
  - Expected: FAIL, with `invalid choice` on the before-subcommand cases and `AttributeError` for `_version_at_least`.
- [ ] **Step 4: Implement.**
  - Declare `--state-dir` and `--repo` with `default=argparse.SUPPRESS` on the top-level parser and on `common`.
  - In `main`, after parsing, set any missing ones: `state_dir = DEFAULT_STATE_DIR`, `repo = "."`. The defaults step is a function `_apply_defaults(args: argparse.Namespace) -> argparse.Namespace`, which the test also calls.
  - Add `_version_at_least` and `V09`.
  - Add `plugin_version` to `cmd_locate`'s return.
  - Bump `plugin.json` to `0.9.0`.
- [ ] **Step 5: Run the whole suite**
  - Run: `python3 -m unittest discover -s plugins/branch-review/tests`
  - Expected: all pass.
- [ ] **Step 6: Commit**

```bash
git add plugins/branch-review/scripts/review_state.py plugins/branch-review/.claude-plugin/plugin.json plugins/branch-review/tests/test_args.py plugins/branch-review/tests/test_rs.py
git commit -m "feat(branch-review): accept --state-dir and --repo before the subcommand; 0.9.0"
```

### Task 2: `begin-round` takes its own snapshot, stores the intent per round, notes and related branches

Spec: C3; B2's per-round intent; A10's storage.

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (`cmd_begin_round`, `_build_parser`)
- Test: `plugins/branch-review/tests/test_snapshot.py`, `plugins/branch-review/tests/test_ledger.py`

**Interfaces:**
- Produces:
  - `begin-round --snapshot` is optional; without it, `begin-round` calls `make_snapshot(repo)`.
  - The round object gains `intent: str | None`: the state's intent after this round's `--intent-file` is applied.
  - New `begin-round` arguments:
    - `--notes-file PATH`: stores the file's text as `state["notes"]`; an empty file sets it to `None`.
    - `--related-branch REF...`: stores `state["related_branches"]: list[str]`; `--related-branch .` clears it to `[]`.
  - Absent arguments keep the stored values.
  - The `begin-round` output gains `notes` and `related_branches`, the stored values, so the skill can inline them.

- [ ] **Step 1: Write the failing tests**
  - In `test_snapshot.py`:
    - `test_begin_round_without_snapshot_takes_one`: with an uncommitted file in the tree, `begin-round --mode deep --model m --base-ref main` (no `--snapshot`) → `snapshot` is a sha whose tree holds the uncommitted file (`git show <snapshot>:<file>`), and `head` equals `HEAD`.
    - `test_explicit_snapshot_still_used`: passing `--snapshot X` → `snapshot == X`.
  - In `test_ledger.py`:
    - `test_round_stores_intent`: round 1 with an intent file "A", round 2 with "B" → `rounds[0]["intent"] == "A"` and `rounds[1]["intent"] == "B"`.
    - `test_round_without_intent_file_copies_stored`: round 2 has no `--intent-file` → `rounds[1]["intent"] == "A"`.
    - `test_notes_and_related_branches_stored`: `--notes-file` holding "leads" and `--related-branch origin/x origin/y` → state `notes == "leads"` and `related_branches == ["origin/x", "origin/y"]`. A later round without the arguments keeps both. `--related-branch .` → `[]`. The output carries both values.
- [ ] **Step 2: Run them and see them fail**
  - Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_snapshot.py' -k without_snapshot -v`
  - Expected: FAIL, with `--snapshot` required.
- [ ] **Step 3: Implement** in `cmd_begin_round` and the parser. Set `rnd["intent"] = state.get("intent")` after the intent update. Read the notes with `_read_text_file(...).strip()`.
- [ ] **Step 4: Run the whole suite.** Expected: all pass.
- [ ] **Step 5: Commit**

```bash
git commit -am "feat(branch-review): begin-round takes its own snapshot and stores intent, notes and related branches per run"
```

### Task 3: Report keys `coverage`, `not_checked`, `variants`, `fix_options`; `earlier_not_checked` in `locate`

Spec: A6, A9, B2's schema.

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (`_validate_output`, `cmd_record_findings`, `cmd_locate`; new constants)
- Modify: `plugins/branch-review/tests/helpers.py` (`output()` default)
- Modify: `plugins/implementation-manager/evals/push-gate/scaffold.sh` and `plugins/implementation-manager/evals/push-queue/scaffold.sh` (fixture report JSON only)
- Test: `plugins/branch-review/tests/test_coverage.py` (new)

**Interfaces:**
- Consumes: `_version_at_least`, `V09` (Task 1).
- Produces:
  - `COVERAGE_STEPS = ("break-tests", "doc-grep", "fulfilled-promises", "real-system", "related-records", "broad-mappings", "measurement-tests", "consumer-build")`.
  - `COVERAGE_ITEMS = CATEGORIES + COVERAGE_STEPS`.
  - `VARIANT_RESULTS = ("fails", "ok")`.
  - The round object gains `coverage: list[{"item", "done"}]` and `not_checked: list[{"what", "reason"}]`. Both are `[]` when absent.
  - Findings gain `variants: list[{"input", "result"}]` and `fix_options: list[str]`. Both are `[]` when absent.
  - The `locate` output gains `earlier_not_checked: list[{"what", "reason", "round"}]`: the entries from recorded deep rounds of the current cycle (`_cycle_start`), de-duplicated by `what` with the latest round kept.

- [ ] **Step 1: Write the failing tests** in `tests/test_coverage.py` (a `LedgerHarness` mixin).
  - Refusals:
    - `test_deep_report_missing_coverage_item_is_refused`: drop `"consumer-build"` from the coverage → `assert_error`. stderr contains `coverage is missing consumer-build`, and the ledger is unchanged (no `added` on the round).
    - `test_deep_report_without_not_checked_is_refused` → error naming `not_checked`.
    - `test_empty_done_is_refused`: `{"item": "logic", "done": ""}` → error.
    - `test_fix_options_with_one_entry_is_refused` → error naming `fix_options`.
    - `test_variant_result_enum`: `result: "maybe"` → error.
  - Acceptances:
    - `test_followup_report_may_omit_coverage`: deep round 1 with full coverage, then a follow-up whose report has no `coverage` or `not_checked` → recorded.
    - `test_v08_round_is_not_checked`: begin a deep round, set its `plugin_version` to `"0.8.1"` in review.json, and record a report with no `coverage` → recorded.
  - Storage:
    - `test_coverage_and_not_checked_stored` on the round.
    - `test_variants_and_fix_options_stored` on the finding, `[]` by default.
  - `locate`:
    - `test_locate_earlier_not_checked`: deep round 1 with `not_checked [{what: "probe X", reason: "no docker"}]` → `locate` returns it with `round: 1`.
    - After a second deep round lists the same `what` with a new reason → one entry, with `round: 2`.
- [ ] **Step 2: Update `helpers.output()`.** It gains `coverage` (every item of `review_state.COVERAGE_ITEMS`, `done: "checked"`) and `not_checked: []` by default, so the existing tests keep passing.
- [ ] **Step 3: Run them and see them fail**
  - Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_coverage.py' -v`
  - Expected: FAIL, because nothing refuses yet.
- [ ] **Step 4: Implement.** `_validate_output(data, known, *, require_coverage: bool)` validates and returns `coverage`, `not_checked` and the per-finding `variants` and `fix_options`.
  - `cmd_record_findings` passes `require_coverage = rnd["mode"] == "deep" and _version_at_least(rnd.get("plugin_version"), V09)`.
  - The missing-items error lists them joined by `, `, in `COVERAGE_ITEMS` order.
  - Extra unknown items are kept, not refused.
- [ ] **Step 5: Update the two IM eval fixtures.** Add `"coverage"` (all 16 items, `"done": "not applicable: fixture"`) and `"not_checked": []` to the JSON report in each `scaffold.sh`. Run: `bash plugins/implementation-manager/evals/push-gate/scaffold.sh` in a temp dir (the way `case.yaml` runs it), then the same for push-queue. Expected: exit 0.
- [ ] **Step 6: Run the whole suite.** Expected: all pass.
- [ ] **Step 7: Commit**

```bash
git commit -am "feat(branch-review): require coverage and not_checked in 0.9 deep reports; store variants and fix_options"
```

### Task 4: Decisions — `accepted`, the kept `decision`, the fix-options gate

Spec: B2, B8.

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (`STATUSES`, `cmd_resolve`, `cmd_set_status`, `_build_parser`)
- Test: `plugins/branch-review/tests/test_decisions.py` (new)

**Interfaces:**
- Consumes: the round `intent` (Task 2), the finding `fix_options` (Task 3).
- Produces:
  - `STATUSES` gains `"accepted"`, after `"deferred"`.
  - `resolve --decision` accepts `fix | defer | accept`. `accept` → status `accepted` and `round_closed` set.
  - `resolve` sets `finding["decision"] = {"decision": ..., "note": ..., "by": "developer", "round": N}`, which `set-status` never touches.
  - `set-status --intent-quote-file PATH`. On success, `resolution["intent_quote"]` holds the normalized quote.
  - `_normalize_ws(text: str) -> str`: collapses runs of whitespace to one space and strips. Case is kept.

- [ ] **Step 1: Write the failing tests** in `tests/test_decisions.py`. The fixture is a deep round with intent "Keys are written bare.\nNo other spelling is accepted." and a finding with `fix_options: ["a", "b"]`.
  - `accepted`:
    - `test_accept_decision`: escalate, then `resolve --decision accept` → status `accepted`, `round_closed` set, and `decision.decision == "accept"`.
    - `test_accepted_does_not_block_covered`: also verify every other finding → `covered` is true.
  - The gate refuses:
    - `test_fixed_with_options_refused_without_basis` → error text contains `resolve --decision fix` and `--intent-quote-file`.
    - `test_fixed_with_options_refused_with_quote_not_in_intent`, with quote "Keys may be spelled any way".
    - `test_empty_quote_refused`: a file holding only `"  \n"` → refused.
    - `test_quote_checked_against_opening_round`: round 2's intent adds the line "Option b is chosen." A quote of that line on R1's finding is refused.
  - The gate accepts:
    - `test_fixed_with_options_allowed_after_developer_fix`: escalate → `resolve --decision fix` → `set-status fixed --note n` → ok, and `decision` is still present afterwards.
    - `test_fixed_with_options_allowed_with_quote`: a quote `"Keys are written   bare."`, with extra spaces → ok, and `resolution.intent_quote == "Keys are written bare."`.
    - `test_v08_finding_without_fix_options_unaffected`: a finding with no `fix_options` key (the v0.8 shape, written into review.json) → `set-status fixed --note n` is ok.
- [ ] **Step 2: Run them and see them fail**
  - Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_decisions.py' -v`
  - Expected: FAIL.
- [ ] **Step 3: Implement.** The gate runs only when `args.status == "fixed"` and `len(finding.get("fix_options") or []) >= 2`.
  - The opening round is the round in `state["rounds"]` whose `number` equals `finding["round_opened"]`.
  - When it has no `intent`, refuse with `no intent stored for round N`.
  - Both refusals are one stderr line: `R1-F1 offers fix options; record the developer's decision with resolve --decision fix, or pass --intent-quote-file with a line of the intent that settles the choice`.
- [ ] **Step 4: Run the whole suite.** Expected: all pass. Check that `list` counts and `covered` reasons treat `accepted` as terminal.
- [ ] **Step 5: Commit**

```bash
git commit -am "feat(branch-review): accepted status, kept developer decision, and the fix-options gate on set-status"
```

### Task 5: `confirm-behavior`

Spec: B7.

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (new `cmd_confirm_behavior`, `_build_parser`)
- Test: `plugins/branch-review/tests/test_decisions.py`

**Interfaces:**
- Produces:
  - `rs confirm-behavior --text-file PATH`. The file is required and must not be empty. It appends `{"text": <stripped>, "round": N}` to `state["behavior_to_confirm"]`, skipping exact duplicate texts.
  - It returns `{"behavior_to_confirm": <count>}`.

- [ ] **Step 1: Write the failing tests.**
  - `test_confirm_behavior_appends`: two texts → count 2. The same text again → count 2.
  - `test_confirm_behavior_empty_refused`.
  - `test_confirm_behavior_does_not_block_covered`: a clean branch plus one item → `covered` is true.
- [ ] **Step 2: Run them and see them fail.** Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_decisions.py' -k confirm -v`. Expected: FAIL, `invalid choice: 'confirm-behavior'`.
- [ ] **Step 3: Implement** with `_ledger_state` and `_text_arg(None, args.text_file, "--text", required=True)`.
- [ ] **Step 4: Run the whole suite.** Expected: all pass.
- [ ] **Step 5: Commit**

```bash
git commit -am "feat(branch-review): confirm-behavior records behavior changes for the developer to confirm"
```

### Task 6: Second-opinion timing, `awaiting-decisions`, `locate` returning a due second opinion, `covered` fields

Spec: B4.

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (`second_opinion_due`, new `_second_opinion_triggers` and `second_opinion_held`, `cmd_status`, `cmd_locate`, `cmd_covered`)
- Modify: `plugins/branch-review/tests/test_second_opinion.py` (rewrite the tests that encode the v0.8 timing), `plugins/branch-review/tests/test_covered.py`

**Interfaces:**
- Consumes: `behavior_to_confirm` (Task 5).
- Produces:
  - `_second_opinion_triggers(state: dict) -> list[str]`: today's reasons logic, unchanged.
  - `second_opinion_due(state) -> tuple[bool, list[str]]`: due only when there are triggers and no finding is `escalated`.
  - `second_opinion_held(state) -> bool`: there are triggers and some finding is `escalated`.
  - `status` order of reasons:
    1. `open-findings`;
    2. `second-opinion` (due, even with `fixed` findings, which get carried);
    3. `unverified-fixes`;
    4. `awaiting-decisions` (`continue: false`, any `escalated`);
    5. `converged`.
  - The cap rules are unchanged. `status` output gains `second_opinion_held: bool`.
  - `locate`: when `choose_mode` returns `followup` with reason `continuing` or `base-updated`, no finding is `open` or `still-open`, and `second_opinion_due` → `mode: "deep"`, `reason: "second-opinion"`, `focus: "second-opinion"`. The output always has `focus`, which is `None` otherwise.
  - `covered` output gains `second_opinion_held: bool` and `behavior_to_confirm: int`. Its `reasons` are unchanged.

- [ ] **Step 1: Rewrite and add tests** in `test_second_opinion.py`.
  - Early timing:
    - `test_due_with_only_fixed_findings`: round 1 with a major, then `set-status fixed` → `reason: second-opinion`, `next_mode: deep`, and `counts.fixed == 1`.
  - Held for decisions:
    - `test_held_while_escalated` replaces `test_major_finding_is_due_even_when_escalated`. A major, escalated → `reason: awaiting-decisions`, `continue: false`, `second_opinion_held: true`.
    - `test_awaiting_decisions_without_trigger`: a minor at low risk, escalated → `awaiting-decisions` with `second_opinion_held: false`.
    - `test_due_after_resolve_defer`: held → `resolve --decision defer` → `second-opinion`.
    - `test_due_after_resolve_accept`: the same with `accept`.
  - Other rules still hold:
    - `test_skip_still_suppresses_early`: `--no-second-opinion` plus a fixed major → `unverified-fixes`.
    - Keep `test_open_findings_come_before_second_opinion`, the cap tests and the v0.7 exemption tests passing; adjust only the expectations the new order changes.
  - `locate`:
    - `test_locate_returns_second_opinion`: after a fixed major (and a new commit so the mode would be `followup`) → `mode: deep`, `reason: second-opinion`, `focus: second-opinion`, and `open_findings` holds the fixed finding.
    - `test_locate_forced_deep_wins`: rewrite history → `reason: history-rewritten`, `focus: None`.
- [ ] **Step 2: Add to `test_covered.py`.**
  - `test_covered_reports_held_and_behavior_count`: an escalated major plus one `confirm-behavior` → `second_opinion_held: true`, `behavior_to_confirm: 1`, `reasons` contains `escalated` and not `second-opinion-pending`.
  - `test_covered_pending_after_accept`: then `resolve --decision accept` → `reasons` contains `second-opinion-pending`.
- [ ] **Step 3: Run them and see them fail**
  - Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_second_opinion.py' -v`
  - Expected: FAIL on the new and rewritten tests.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run the whole suite.** Expected: all pass.
- [ ] **Step 6: Commit**

```bash
git commit -am "feat(branch-review): run the second opinion once nothing is open, hold it for decisions, report awaiting-decisions"
```

### Task 7: The summary is never stale, and the Review notes block

Spec: C4, C5, B10, plus the summary sections that B7 and B8 add.

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (`render_summary`, `_intent_check_lines`; new `render_review_notes`, `_write_summary`; `cmd_summary`; calls from `cmd_record_findings`, `cmd_set_status`, `cmd_resolve`, `cmd_discuss`, `cmd_confirm_behavior`)
- Test: `plugins/branch-review/tests/test_summary.py` (new; move nothing out of the existing files)

**Interfaces:**
- Consumes: `accepted` (Task 4), `behavior_to_confirm` (Task 5), round `not_checked` (Task 3), `PLUGIN_VERSION` (Task 1).
- Produces:
  - `_write_summary(directory: Path, state: dict) -> None`: writes `summary.md` and `review-notes.md`. It does nothing when the state has no rounds, or when the last round has no `added`.
  - `render_review_notes(state: dict) -> str`.
  - `cmd_summary` output gains `review_notes: str` and `review_notes_path: str`.
- **`summary.md` changes:**
  - The header ends with `; written by branch-review <PLUGIN_VERSION>`.
  - New sections: "Behavior changes to confirm" after "Escalated", "Accepted" after "Deferred", and "Not checked" right after "Intent check".
  - "Not checked" holds the latest deep round's entries, as `- <what>: <reason>`.
  - "Behavior the intent does not mention" takes rounds from the latest deep round on.
- **`review-notes.md`** has exactly:
  - the line `## Review notes`;
  - a blank line;
  - the generated line, which is pinned by the test below;
  - then, in order and only when non-empty: Escalated, Behavior changes to confirm, Deferred, Accepted, Invalid, Outside the change, Intent check (only its non-empty lists), Not checked. Each is a `### <name>` heading with the same entry format as `summary.md`;
  - then `### Found and fixed`, with one `- <id> (<severity>) <title>` per `verified` finding;
  - when any of those sections is empty, a last line `Nothing to report under: <names joined by ", ">.`
- **The generated line:** `<N> rounds (<k> deep, <k> followup, <k> merge-check); second opinion: <yes|no>; merge risk <level> (round <n>, the latest deep round).`
  - It says `1 round` for one round.
  - Modes with a count of 0 are left out.
  - The second-opinion round counts as deep.

- [ ] **Step 1: Write the failing tests** in `tests/test_summary.py`.
  - The rewrite:
    - `test_summary_rewritten_by_set_status`: record round 1, delete `summary.md`, then `set-status` → `summary.md` exists and shows the new status. Do the same for `record-findings`, `resolve`, `discuss` and `confirm-behavior` (a `subTest` each).
    - `test_rewrite_skipped_on_unrecorded_round`: recorded round 1, then `begin-round` for round 2, then `discuss` → exit 0, and `summary.md` unchanged.
  - `summary.md`:
    - `test_header_names_plugin_version`.
    - `test_accepted_and_behavior_sections`.
    - `test_not_checked_section_from_latest_deep`.
    - `test_unmentioned_from_latest_deep_round_on`: R1 deep lists "old", R2 follow-up lists "mid", R3 deep lists "new", R4 follow-up lists "late" → the summary shows "new" and "late" only.
  - `review-notes.md`:
    - `test_review_notes_generated_line`: 3 rounds (deep, followup, deep second opinion), latest deep risk low → the line is `3 rounds (2 deep, 1 followup); second opinion: yes; merge risk low (round 3, the latest deep round).`
    - `test_review_notes_collapses_empty`: only verified findings → no `### Escalated`, the line `Nothing to report under: Escalated, Behavior changes to confirm, Deferred, Accepted, Invalid, Outside the change, Intent check, Not checked.`, and `### Found and fixed` listing `- R1-F1 (minor) Handle the empty case.`
    - `test_summary_command_returns_review_notes`: `review_notes` equals the file's text.
- [ ] **Step 2: Run them and see them fail**
  - Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_summary.py' -v`
  - Expected: FAIL.
- [ ] **Step 3: Implement.** Each state-changing command calls `_write_summary` after `save_state`. A failure inside `_write_summary` must not change the command's exit code: catch `OSError` and leave the old files.
- [ ] **Step 4: Run the whole suite.** Expected: all pass. Existing summary assertions in `test_ledger.py` may need the new header suffix and sections; update only those expectations.
- [ ] **Step 5: Commit**

```bash
git commit -am "feat(branch-review): rewrite the summary on every state change and generate review-notes.md"
```

### Task 8: Reviewer prompt, checklist and schema doc

Spec: A1 to A5, A7 to A9, A11, C1, and the documentation side of A6 and B2.

**Files:**
- Modify: `plugins/branch-review/agents/reviewer.md`
- Modify: `plugins/branch-review/skills/review/reviewer-checklist.md`
- Modify: `plugins/branch-review/skills/review/finding-schema.md`
- Test: `plugins/branch-review/tests/test_prompt_text.py` (new)

**Interfaces:**
- Consumes: `COVERAGE_ITEMS`, `STATUSES` (Tasks 3 and 4).
- Produces dispatch inputs, which SKILL.md fills in Task 9:
  - `EARLIER_NOT_CHECKED` (JSON list; deep rounds);
  - `EARLIER_REVIEW_NOTES` (inlined text; deep rounds);
  - `RELATED_BRANCHES` (one ref per line).

- [ ] **Step 1: Write the failing tests** in `tests/test_prompt_text.py`.
  - `test_every_process_section_ends_with_report_write`: in `reviewer.md`, each of the four sections ("Process for deep mode", "Process for second-opinion focus", "Process for follow-up mode", "Process for merge-check mode") contains `REPORT_PATH` and `tail -n 3` in its last numbered step.
  - `test_schema_documents_every_coverage_item`: every `review_state.COVERAGE_ITEMS` value appears in `finding-schema.md` in backticks.
  - `test_schema_documents_every_status`: every `review_state.STATUSES` value appears in the schema's statuses table.
  - `test_reviewer_lists_new_inputs`: `reviewer.md` Inputs names `EARLIER_NOT_CHECKED`, `EARLIER_REVIEW_NOTES` and `RELATED_BRANCHES`.
- [ ] **Step 2: Run them and see them fail**
  - Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_prompt_text.py' -v`
  - Expected: FAIL.
- [ ] **Step 3: Edit `reviewer.md`.**
  - Inputs: the three new inputs.
  - Deep step 3: related records include the plan or spec, and each assumption the plan states about existing code is checked (A3).
  - Deep step 5: the A1 probe rule, with its `not_checked` reason rule and the boundary values. Add A2, A4, A5, A8 and A9 as required bullets.
  - Second-opinion focus: `EARLIER_NOT_CHECKED` goes first in its do-first list.
  - Follow-up step 1: spell out reading the schema and the checklist (C1).
  - Every process section: the same last numbered step, writing `REPORT_PATH` then `tail -n 3` (C1).
  - Output: `coverage` and `not_checked` are required in deep mode, `fix_options` is used instead of prose alternatives, and `variants` is described.
  - Environment: the three A11 notes.
- [ ] **Step 4: Edit `reviewer-checklist.md`.**
  - §4: a "Tests that measure process-wide state" subsection, with the 8.3 example.
  - §5: a claim the change relies on is a promise (A1); measurements match production use (A8, with the TD-0078 example).
  - §7: the fulfilled-promises grep (A5, the SQLSRC232 example). Tighten "Try doc promises" per A1.
  - §8: build output as a consumer would (A4, the CA2100 example).
  - Severity: the two `major` lines (A7).
  - §1: an equivalence-class paragraph (A9, the R3-F1 → R4-F1 example).
  - Examples follow the file's style: the repo, the PR or unit, one sentence.
- [ ] **Step 5: Edit `finding-schema.md`.** Document:
  - `coverage`, `not_checked`, `variants`, `fix_options`;
  - `accepted` and its transitions;
  - `resolve --decision accept`;
  - the `decision` field;
  - `set-status --intent-quote-file` and its refusals;
  - `confirm-behavior`;
  - per-round `intent`, `notes` and `related_branches`;
  - the new `status` reason order and `second_opinion_held`;
  - `locate` keys `focus`, `plugin_version` and `earlier_not_checked`, and the `second-opinion` row in the mode table;
  - `covered`'s new keys;
  - `summary`'s `review_notes`;
  - arguments before the subcommand.
- [ ] **Step 6: Run the whole suite.** Expected: all pass.
- [ ] **Step 7: Commit**

```bash
git commit -am "docs(branch-review): reviewer steps, checklist and schema for 0.9"
```

### Task 9: Review skill, check skill and README

Spec: B1, B3, B5, B6, B7, B9, C1's fallback, C3, C5, C6, the B8 mapping in item 2.5, and the dispatch of the A10 and A6 inputs.

**Files:**
- Modify: `plugins/branch-review/skills/review/SKILL.md`
- Modify: `plugins/branch-review/skills/check/SKILL.md`
- Modify: `plugins/branch-review/README.md`
- Test: `plugins/branch-review/tests/test_skill_text.py`

- [ ] **Step 1: Write the failing tests** in `test_skill_text.py`.
  - `test_review_skill_has_no_snapshot_step`: no line of `skills/review/SKILL.md` runs `rs snapshot`.
  - `test_review_skill_pastes_review_notes`: the text contains `review_notes`.
  - `test_argument_hint_lists_new_arguments`: the frontmatter `argument-hint` contains `--notes-file` and `--related-branch`.
- [ ] **Step 2: Run them and see them fail.** Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_skill_text.py' -v`. Expected: FAIL.
- [ ] **Step 3: Edit `skills/review/SKILL.md`.**
  - Frontmatter `argument-hint`: add `[--notes-file PATH] [--related-branch REF...]`.
  - 2.3: keep `plugin_version`, `focus` and `earlier_not_checked` from `locate`. Print `plugin_version`.
  - 2.5:
    - A note that keeps the behavior, documents it as a known issue or says not to fix it → `--decision accept`.
    - After `--decision fix` on a finding with `fix_options`, that decision is what lets `set-status fixed` through.
  - 4.1 is removed. 4.2:
    - no `--snapshot`;
    - pass `--focus second-opinion` when `locate` returned `focus`;
    - pass `--notes-file` and `--related-branch` when given;
    - keep `notes` and `related_branches` from the output.
  - 5, the template, gains `EARLIER_NOT_CHECKED` (deep rounds, from `locate`), `EARLIER_REVIEW_NOTES` (deep rounds, when `notes` is set) and `RELATED_BRANCHES` (when set), with their leave-out rules.
  - 5, the `VALIDATED` rule: per B9.
  - 7, Valid:
    - the three B1 rules, which a fixer's brief must carry word for word;
    - B5's "your own check";
    - a finding with `fix_options` needs `--intent-quote-file` or the developer's decision.
  - 7, escalation: B3's "raise it now".
  - 7, unmentioned behavior: B7's rule and `rs confirm-behavior`.
  - 8: `awaiting-decisions` is handled like `converged`, with the decisions first.
  - 9: run `rs summary`, print `text`, and tell the invoker to paste `review_notes` as it is.
  - Red flags: the B5 and B6 rows. Update the alternatives row to name the script's check.
  - Every new `rs` line keeps the prefix.
- [ ] **Step 4: Edit `skills/check/SKILL.md`.**
  - "Run it" item 3: after the verdict, append `; second opinion held` when `second_opinion_held` is true, and `; <n> behavior changes to confirm` when `behavior_to_confirm` is above 0.
  - Add C6's line: an agent may record the developer's decision with `rs resolve`, and it takes effect at the next review run.
- [ ] **Step 5: Edit `README.md`.**
  - The Report paragraph describes `review-notes.md` and the pasting.
  - Add a short "What's new in 0.9" list.
  - Add C6's line.
- [ ] **Step 6: Verify**
  - Run: `python3 -m unittest discover -s plugins/branch-review/tests && ruff check . && ruff format --check . && claude plugin validate . && claude plugin validate plugins/branch-review`
  - Expected: all pass and validate clean.
- [ ] **Step 7: Commit**

```bash
git commit -am "feat(branch-review): 0.9 triage, loop and dispatch rules in the review skill"
```

### Task 10: Eval graders and an eval run

Spec: Testing (eval).

**Files:**
- Modify: `plugins/branch-review/evals/review-loop/graders/loop.md`
- Create: `plugins/branch-review/evals/review-loop/graders/reviewer-wrote-reports.md`

- [ ] **Step 1: Edit `loop.md` item 1.** The review ran at least two rounds, and a second-opinion round ran. Under B4, a fixed major goes straight to the second opinion, so two rounds can be a full run.
- [ ] **Step 2: Create `reviewer-wrote-reports.md`.**

```markdown
---
type: tool_used
tool: Write
input_match: 'round-\d+\.md'
min: 0
max: 0
---

The orchestrator never wrote a round report itself. The reviewer has no Write tool, so a Write to `round-<n>.md` is the orchestrator's fallback for a reviewer that skipped `REPORT_PATH`; 0.9 makes every mode end by writing it.
```

- [ ] **Step 3: Run the eval** (needs the token in `~/.zprofile`; see the repo's eval note).
  - Run: `zsh -lc 'claude plugin eval plugins/branch-review --case review-loop --scaffold --allow-tools Bash Write Edit SendMessage ToolSearch --runs 1 --json <scratchpad>/eval-0.9.json'`
  - Expected: every grader passes. On a failure, read the run's trace before changing a grader; a grader is changed only when the trace shows that the skill behaved as the spec says.
- [ ] **Step 4: Commit**

```bash
git add plugins/branch-review/evals/review-loop/graders
git commit -m "test(branch-review): eval graders for 0.9 timing and reviewer-written reports"
```

- [ ] **Step 5: Run the branch review.** The repo's AGENTS.md requires `/branch-review:review` over the whole change before any push. Give it the spec as its intent.
