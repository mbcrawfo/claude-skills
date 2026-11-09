# Implementation Manager Token Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the implementation manager session's own token use: owner-prompted compaction, a skill that still works after compaction, briefs rendered from templates, and watcher output that carries the status and the coverage verdict.

**Architecture:** Three new script features in `plugins/implementation-manager/scripts/` (`context.py`, `coverage.py`, `brief.py`) wired into `run.py`, then a restructure of `SKILL.md` into a short core plus `references/*.md`, then eval and doc updates. The manager still decides every `go`, `redo`, `queue` and merge; scripts only gather and print.

**Tech Stack:** Python 3.9+ standard library, `unittest`, ruff; Markdown skill files; `claude plugin eval`.

**Spec:** `docs/superpowers/specs/2026-11-09-implementation-manager-token-efficiency-design.md`

## Global Constraints

- Plugin scripts: Python 3.9+, standard library only, `from __future__ import annotations` at the top, run through `python3`.
- Run `ruff check .` and `ruff format .` from the repo root before each commit that touches Python.
- Tests: `python3 -m unittest discover -s plugins/implementation-manager/tests` must pass after every task.
- Compaction thresholds: ask at **300,000** tokens of context; ask again only after **150,000** more since the last ask.
- The core `SKILL.md` stays under **17,500 characters** (the proxy for 5,000 tokens).
- The compact line the manager gives the owner, exactly (with `<run>` replaced by the absolute run folder): `/compact Resume as the implementation manager for <run>: read notes.md, then run.py status, before acting.`
- `SKILL.md` must never contain a dollar sign followed by a digit (Claude Code substitutes skill arguments).
- Every `${...}` placeholder (`${CLAUDE_SKILL_DIR}`, `${CLAUDE_PLUGIN_DATA}`, `${user_config.retention_days}`, `$ARGUMENTS`) stays in `SKILL.md`: reference files are read with Read and are never substituted.
- Moved skill text keeps its meaning. Trim duplicates; do not rewrite rules.
- Plugin version becomes `0.10.0`.
- README in Simplified Technical English (AGENTS.md "Documentation"). Internals go in `ARCHITECTURE.md`.
- New tech-debt items start at `TD-0012` (`docs/tech-debt/README.md` "Next id").
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Review Focus

1. **Tests run inside a Claude session.** `CLAUDE_CODE_SESSION_ID` is set in the test process's environment, so `status` would read the real transcript. `CliTestCase.setUp` must remove it and point `CLAUDE_CONFIG_DIR` at the temp folder (Task 1).
2. **A huge or half-written transcript.** The last line may be partial and the file several MB. Read only the tail and skip lines that are not valid JSON (Task 1 test `test_partial_last_line_is_skipped`).
3. **branch-review updated mid-run.** The recorded `rs` path disappears. `wait --full` must print `verdict <sha> unavailable: rs not found: <path>`, never crash or skip the event line (Task 2 test).
4. **The event line stays first and unchanged.** Existing tests and the evals' graders read the first line of `wait`; `--full` only appends lines after it, and plain `wait` is unchanged (Task 2 tests).
5. **A brief with an unfilled placeholder.** A missing spec, manager id or title must refuse with a fix-it message, never write a brief that contains `{{` (Task 3 tests).

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/context.py` (new) | Find this session's transcript and return its current context size. |
| `scripts/coverage.py` (new) | Run branch-review's recorded `rs covered` for an event and return the verdict line. |
| `scripts/brief.py` (new) | Render a child brief from templates and the run state. |
| `templates/unit-brief.md`, `templates/integration-brief.md`, `templates/fragments/*.md` (new) | Fixed child-prompt text with `{{...}}` placeholders. |
| `scripts/runstate.py` | New state fields and their backfill; `set` keys `manager`. |
| `scripts/run.py` | New commands `check-cmd`, `compact-asked`, `brief`; `unit --spec`; `wait --full`; the `context` line in `status`. |
| `skills/implementation-manager/SKILL.md` | The core, under 17,500 characters. |
| `skills/implementation-manager/references/*.md` (new) | The moved procedures. |
| `tests/test_context.py`, `tests/test_coverage.py`, `tests/test_brief.py` (new), `tests/test_run_cli.py`, `tests/test_skill_text.py` | Tests. |
| `tests/golden/brief-unit.md`, `tests/golden/brief-integration-unit.md`, `tests/golden/brief-integration-child.md` (new) | Golden renders. |
| `evals/push-queue/*`, `evals/compaction-resume/*` (new) | Evals. |
| `README.md`, `ARCHITECTURE.md`, `.claude-plugin/plugin.json`, `docs/tech-debt/*` | Docs and version. |

All paths below are relative to `plugins/implementation-manager/` unless they start with `docs/`.

---

### Task 1: Context size in `status`, and `compact-asked`

**Files:**
- Create: `scripts/context.py`, `tests/test_context.py`
- Modify: `scripts/runstate.py` (`init_run` state, `load` backfill), `scripts/run.py` (`_status`, parser, `_run`, module docstring)
- Test: `tests/test_run_cli.py`

**Interfaces:**
- Produces:
  - `context.transcript_path(env: Mapping[str, str]) -> Path | None`: the file `<CLAUDE_CONFIG_DIR or ~/.claude>/projects/*/<CLAUDE_CODE_SESSION_ID>.jsonl`; `None` when the variable is unset or no file matches.
  - `context.current_tokens(env: Mapping[str, str]) -> int | None`: from the last line, reading backwards, that parses as JSON with `type == "assistant"` and `message.usage`: `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` (missing keys count 0). Reads at most the last 4 MiB. `None` when there is no transcript or no such line.
  - `context.COMPACT_AT = 300_000`, `context.COMPACT_EVERY = 150_000`.
  - `context.compact_due(tokens: int, asked: int | None, quiet: bool) -> bool`: `quiet and tokens >= COMPACT_AT and (asked is None or tokens >= asked + COMPACT_EVERY)`.
  - State field `compact_asked: int | None` (run-level, default `None`, backfilled by `load`).
  - `status` prints a final line `- context: <N>k` (`N = round(tokens / 1000)`), with `, compact due` appended when due; no line when `current_tokens` is `None`. Quiet means: the "needs the manager" list is empty and every unit whose gate is open and is not done has a worktree.
  - `run.py compact-asked <dir>`: stores `current_tokens` in `compact_asked`; refuses `context size unknown: no transcript for this session` when `None`. Prints nothing.

- [ ] **Step 1: Isolate the CLI tests from the real session.** In `CliTestCase.setUp` (`tests/test_run_cli.py`), after `self.env = dict(os.environ)`: `self.env.pop("CLAUDE_CODE_SESSION_ID", None)` and `self.env["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude")`. Run the suite: all pass.

- [ ] **Step 2: Write the failing tests** in `tests/test_context.py`, building transcripts in a temp `CLAUDE_CONFIG_DIR/projects/p/<id>.jsonl`:
  - `test_no_session_id_gives_none`: env without `CLAUDE_CODE_SESSION_ID` → `current_tokens(env) is None`.
  - `test_missing_file_gives_none`.
  - `test_last_assistant_usage_is_summed`: two assistant lines, usages `{input_tokens: 5, cache_read_input_tokens: 1000, cache_creation_input_tokens: 200}` then `{input_tokens: 3, cache_read_input_tokens: 400000, cache_creation_input_tokens: 2000}`, then a `user` line → `402003`.
  - `test_partial_last_line_is_skipped`: the file ends with `{"type": "assist` (no newline) → the previous assistant line's total.
  - `test_large_file_reads_tail_only`: 5 MiB of filler `user` lines before one assistant line → its total (and the test finishes in under 2 s).
  - `test_compact_due`: `compact_due(299_999, None, True)` is False; `(300_000, None, True)` True; `(300_000, None, False)` False; `(420_000, 300_000, True)` False; `(450_000, 300_000, True)` True.

- [ ] **Step 3: Run them to see them fail.** `python3 -m unittest discover -s plugins/implementation-manager/tests -p 'test_context.py'` → ImportError on `context`.

- [ ] **Step 4: Implement `scripts/context.py`** with the interface above. Read the tail by seeking to `max(0, size - 4 * 1024 * 1024)`, dropping the first (possibly cut) line when the seek was not at 0, and scanning lines in reverse.

- [ ] **Step 5: Run `test_context.py`: PASS.**

- [ ] **Step 6: Write the failing CLI tests** in `tests/test_run_cli.py` (new class `ContextTests(CliTestCase)`), with a helper that writes a transcript for session `s1` under `self.env["CLAUDE_CONFIG_DIR"]` and sets `self.env["CLAUDE_CODE_SESSION_ID"] = "s1"`:
  - `test_status_has_no_context_line_without_transcript`: no line starting `- context:`.
  - `test_status_prints_context`: 120,400 tokens → last line `- context: 120k`.
  - `test_status_says_compact_due_when_quiet`: 310,000 tokens, no units → last line `- context: 310k, compact due`.
  - `test_not_due_with_a_request_waiting`: 310,000 tokens and an unanswered request (append a request line, run `wait`, answer nothing) → last line `- context: 310k`.
  - `test_not_due_for_an_open_unit_without_worktree`: open a unit's gate with no `--worktree` → no `compact due`.
  - `test_compact_asked_records_and_suppresses`: at 310,000 run `compact-asked`; `run.json` has `"compact_asked": 310000`; status says no `compact due`; rewrite the transcript at 470,000 → `compact due` again.
  - `test_compact_asked_without_transcript_refuses`: exit 1, stderr contains `context size unknown`.
  - `test_old_run_json_backfills_compact_asked`: delete the key from `run.json`, run `status` → exit 0.

- [ ] **Step 7: Run them: FAIL.**

- [ ] **Step 8: Implement.** `runstate.init_run` adds `"compact_asked": None`; `load` does `state.setdefault("compact_asked", None)`. In `run.py`: `_status(run_dir, state, env=os.environ)` appends the context line last, computing quiet from `_needs(...)` and the open units' `worktree`; add the `compact-asked` subcommand (under the lock) and its line in the module docstring.

- [ ] **Step 9: Run the whole suite: PASS.** `python3 -m unittest discover -s plugins/implementation-manager/tests`

- [ ] **Step 10: ruff, then commit.**

```bash
git add plugins/implementation-manager/scripts/context.py plugins/implementation-manager/scripts/run.py plugins/implementation-manager/scripts/runstate.py plugins/implementation-manager/tests/test_context.py plugins/implementation-manager/tests/test_run_cli.py
git commit -m "feat(implementation-manager): context size in status and compact-asked"
```

---

### Task 2: Coverage verdict and `wait --full`

**Files:**
- Create: `scripts/coverage.py`, `tests/test_coverage.py`
- Modify: `scripts/runstate.py` (state field `check`, backfill), `scripts/run.py` (`check-cmd`, `wait --full`, docstring)
- Test: `tests/test_run_cli.py`

**Interfaces:**
- Consumes: `_status(run_dir, state, env)` from Task 1.
- Produces:
  - State field `check: {"rs": str, "state_dir": str} | None` (default `None`, backfilled).
  - `run.py check-cmd <dir> --rs <path> --state-dir <dir>`: stores both as absolute paths; refuses `rs not found: <path>` when the file is missing. Prints nothing.
  - `coverage.event_target(line: str) -> tuple[str, str] | None`: `(unit, sha)` for `REQUEST <u> <sha> <branch>`, `PENDING <u> <sha> <branch>`, `NEXT <u> <sha>`, `READY <u> <pr> <sha>`, `REMIND <u> <pr> <sha>`; `None` for every other line, `FINAL-READY` included.
  - `coverage.verdict_line(state: dict, unit: str, sha: str, *, run: Callable = subprocess.run) -> str`: `verdict <sha> <compact JSON>` (`json.dumps(obj, separators=(",", ":"))`), or `verdict <sha> unavailable: <reason>`. Reasons, checked in this order: `no check command recorded`, `rs not found: <path>`, `unit <unit> has no worktree`, `check timed out` (120 s), `check failed: <first stderr line>`, `check printed no JSON`. Command: `["sh", rs, "covered", "--repo", <unit worktree>, "--commit", sha, "--state-dir", state_dir]`.
  - `coverage.log_lines(run_dir: Path, line: str) -> list[str]`: for a `STUCK <unit> …` event, the unit's last line in `pushes.log` prefixed `pushes: ` and its last line in `requests.log` prefixed `requests: ` (each omitted when the unit has none); `[]` for every other event. These are the lines the `STUCK` rows send the manager to read.
  - `run.py wait <dir> --full`: prints the event line (unchanged), then the verdict line when `event_target` matches, then the `log_lines`, then one empty line, then the `status` block. A `status` that raises prints `status unavailable: <message>` in its place. Exit 0 always. `wait` without `--full` is unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_coverage.py`, with a fake `run` returning `subprocess.CompletedProcess`:
  - `test_event_target`: the five matching shapes return `(unit, sha)`; `FINAL-READY a 7 <sha>`, `MERGED a 7`, `ERROR x` return `None`.
  - `test_no_check_recorded`, `test_rs_missing` (`rs` points at a deleted file), `test_unit_without_worktree`: the matching `unavailable` reasons.
  - `test_covered_json_is_compacted`: fake stdout `{\n "covered": true,\n "reasons": []\n}` → `verdict <sha> {"covered":true,"reasons":[]}`.
  - `test_exit_1_reports_stderr`: returncode 1, stderr `commit not found: abc\nmore` → `verdict <sha> unavailable: check failed: commit not found: abc`.
  - `test_timeout`: fake raises `subprocess.TimeoutExpired` → `check timed out`.
  - `test_command_shape`: the fake records argv; assert it equals the command above with the unit's worktree and the stored `state_dir`.
  - `test_log_lines_for_stuck`: `pushes.log` holds `a go <SHA1>` then `b go <SHA2>`, `requests.log` holds `a syncing <SHA3> integration/x`; `log_lines(run, "STUCK a - syncing")` == `["pushes: a go <SHA1>", "requests: a syncing <SHA3> integration/x"]`; `log_lines(run, "READY a 7 <SHA1>")` == `[]`.

- [ ] **Step 2: Run: FAIL** (no module).

- [ ] **Step 3: Implement `scripts/coverage.py`** and the `check` state field (`init_run` default `None`, `load` backfill).

- [ ] **Step 4: Run `test_coverage.py`: PASS.**

- [ ] **Step 5: Write the failing CLI tests** (`tests/test_run_cli.py`, class `WaitFullTests(CliTestCase)`). Use a fake `rs` shell script in the temp folder that echoes `{"covered": true, "reasons": [], "branch": "feat/a"}` and records its arguments to a file:
  - `test_plain_wait_is_one_line`: `wait` on a request prints exactly `REQUEST a <SHA1> feat/a\n` (existing behavior).
  - `test_full_wait_prints_event_verdict_and_status`: after `check-cmd` and `unit a --branch feat/a --worktree <tmp>/wt`, `wait --full` stdout lines are: `REQUEST a <SHA1> feat/a`, `verdict <SHA1> {"covered":true,"reasons":[],"branch":"feat/a"}`, `""`, then a line starting `Run my-run (`.
  - `test_full_wait_without_check_cmd`: second line `verdict <SHA1> unavailable: no check command recorded`.
  - `test_full_wait_with_rs_removed`: record, delete the fake `rs`, `wait --full` → second line `verdict <SHA1> unavailable: rs not found: <path>`, exit 0.
  - `test_full_wait_next_event_has_verdict`: the existing NEXT setup → second line starts `verdict <SHA2> `.
  - `test_check_cmd_refuses_missing_rs`: exit 1, stderr contains `rs not found`.
  - In `GhTests`: `test_full_wait_ready_verdict_uses_head_sha`: fake gh returns a clean open PR whose head is `HEAD_A` (from `fakes`) → second line starts `verdict <HEAD_A> `; the fake `rs` was called with `--commit <HEAD_A>`.

- [ ] **Step 6: Run: FAIL.**

- [ ] **Step 7: Implement** `check-cmd` (under the lock) and `--full` in `_wait`: build the extra lines after `watcher.wait` returns, loading the state with `runstate.load`; keep the existing `" ".join(line.split())` for the event line. Add both to the module docstring.

- [ ] **Step 8: Run the whole suite: PASS.**

- [ ] **Step 9: ruff, then commit.**

```bash
git add plugins/implementation-manager/scripts/coverage.py plugins/implementation-manager/scripts/run.py plugins/implementation-manager/scripts/runstate.py plugins/implementation-manager/tests/test_coverage.py plugins/implementation-manager/tests/test_run_cli.py
git commit -m "feat(implementation-manager): wait --full prints the coverage verdict and status"
```

---

### Task 3: Briefs from templates

**Files:**
- Create: `scripts/brief.py`, `templates/unit-brief.md`, `templates/integration-brief.md`, `templates/fragments/` (integration-mode and designed-unit fragments), `tests/test_brief.py`, `tests/golden/brief-unit.md`, `tests/golden/brief-integration-unit.md`, `tests/golden/brief-integration-child.md`
- Modify: `scripts/runstate.py` (`add_unit(..., spec=None)`, unit backfill `spec`, run-level `manager`, `set_option` key `manager`), `scripts/run.py` (`unit --spec`, `set manager`, `brief`, docstring)

**Interfaces:**
- Produces:
  - Unit field `spec: str | None` (set with `run.py unit <dir> <name> --spec <path>`); run field `manager: str | None` (set with `run.py set <dir> manager <local_id>`; the value must match `local_[0-9a-f-]+`).
  - Template placeholders (script-filled): `{{run}}` (absolute run folder), `{{name}}`, `{{title}}` (unit title, else the name), `{{spec}}`, `{{base}}` (`runstate.unit_base(state)`: the integration branch in integration mode), `{{run_base}}` (the run's own base), `{{integration}}` (the integration branch), `{{repo}}`, `{{manager}}`, `{{facts}}` (`<run>/facts.md` if present, else empty), `{{extra}}` (the `--extra-file` text, else empty), plus the fragment slots the builder defines. Child-filled placeholders stay in their current `<sha>`, `<branch>`, `<reasons>`, `<id>`, `<run-id>`, `k` form.
  - `brief.render(run_dir: Path, state: dict, unit: str, *, extra: str = "", gate: bool = True, designed: bool = False) -> str`.
  - `run.py brief <dir> <unit> [--extra-file P] [--no-gate] [--designed]`: writes `<dir>/briefs/<unit>.md` and prints that path. The integration unit uses `integration-brief.md`; other units use `unit-brief.md` with the integration-mode fragments when the run has an integration branch. Refusals: `no manager session id: run.py set <dir> manager <id>`, `unit <unit> has no spec: run.py unit <dir> <unit> --spec P`, `unknown unit`, and `brief left placeholders: <list>` if any `{{` survives.
- The start prompt the skill will use (Task 4): ``You are unit `<name>` of an implementation-manager run. Read `<run>/briefs/<name>.md` in full and follow it; it is your whole task.``

- [ ] **Step 1: Move the text into templates.** From `SKILL.md` "Starting children" (parts 1–11), "Unit prompts in integration-branch mode", "The integration child" and "A unit the owner designs with the child", copy the fixed text into the templates and fragments, replacing only the script-filled placeholders with `{{...}}`. Part 6 becomes the header `6. **Unit-specific instructions:**` followed by `{{facts}}` and `{{extra}}`. Part 5's quoted block and parts 8–11 stay word for word.

- [ ] **Step 2: Verify the move is verbatim.** Write a throwaway script in the scratchpad that renders `unit-brief.md` with each `{{x}}` replaced by the literal token the old text used (`{{run}}`→`<run>`, `{{name}}`→`<name>`, `{{base}}`→`<base>`, `{{manager}}`→`<manager id>`, …) and diffs parts 8–11 and part 5's quoted block against the fenced block and quote in `git show HEAD:plugins/implementation-manager/skills/implementation-manager/SKILL.md`. Expected: no differences. Do the same for the integration child's fenced block.

- [ ] **Step 3: Write the failing tests** in `tests/test_brief.py` (build state with `runstate.init_run` and `add_unit` in a temp run folder):
  - `test_unit_brief_matches_golden`: run `/tmp/x/run`, repo `o/r`, base `main`, unit `u1` titled `Add widgets`, spec `docs/specs/widgets.md`, manager `local_abc`, a `facts.md` of `Run ./format.sh before validating.`, extra `Touch only src/widgets/.` → equals `tests/golden/brief-unit.md`.
  - `test_integration_unit_brief_matches_golden` and `test_integration_child_brief_matches_golden`, the same with `integration/widgets`.
  - `test_no_placeholders_left`: none of the three renders contains `{{`.
  - `test_plain_run_mentions_no_integration_branch`: the plain render does not contain `integration/`.
  - `test_integration_unit_rules`: the integration render's part 3 starts with `` `git fetch origin integration/widgets` ``; it contains `/branch-review:review --base origin/integration/widgets`; it contains the sandbox-protected-files line; every `sync_with_base_branch` occurrence is followed, within the same sentence, by `base: integration/widgets`.
  - `test_no_gate_drops_part_2`, `test_designed_adds_the_designing_paragraph` (contains `[designing]`).
  - CLI (in `tests/test_run_cli.py`): `test_brief_writes_file_and_prints_path`, `test_brief_refuses_without_manager`, `test_brief_refuses_without_spec`, `test_set_manager_validates_id`.

- [ ] **Step 4: Run: FAIL.**

- [ ] **Step 5: Implement** the state fields, `brief.py` (templates found at `Path(__file__).resolve().parent.parent / "templates"`), and the CLI. Then generate the three golden files with the implementation, read each one against the old `SKILL.md` text (`git show HEAD:...`), and fix the templates until they read right. Commit the goldens only once they read right.

- [ ] **Step 6: Run the whole suite: PASS.**

- [ ] **Step 7: ruff, then commit.**

```bash
git add plugins/implementation-manager/scripts plugins/implementation-manager/templates plugins/implementation-manager/tests
git commit -m "feat(implementation-manager): run.py brief renders child prompts from templates"
```

---

### Task 4: The compaction-safe skill

**Files:**
- Modify: `skills/implementation-manager/SKILL.md`
- Create: `skills/implementation-manager/references/` files. Proposed split (the builder may adjust within the size limit): `commands.md` (full command table, both modes), `setup.md` (Setup and the integration-mode setup bullets), `children.md` (starting children with `run.py brief`, the designed unit, cards), `events.md` (Event handling and "Answering a push request"), `merge-bar.md`, `owner-queue.md`, `integration.md` (late work in integration mode), `finishing.md` (`specs` unit, Finishing, Tracker).
- Test: `tests/test_skill_text.py`

**Interfaces:**
- Consumes: `status`'s `- context:` line and `compact-asked` (Task 1); `check-cmd`, `wait --full` and the `verdict` line (Task 2); `brief`, `unit --spec`, `set manager` (Task 3).

**Core `SKILL.md` contents, most important first:**
1. Frontmatter (unchanged) and the editing note.
2. Role and "Invoking this skill authorizes" (unchanged meaning).
3. The four constraints, with constraint 3's command now `run.py wait <run> --full`.
4. The `run.py` shorthand: the absolute path `python3 ${CLAUDE_SKILL_DIR}/../../scripts/run.py`, and that references are in `${CLAUDE_SKILL_DIR}/references/`. The run folder rule (`${CLAUDE_PLUGIN_DATA}/<owner>-<repo>/<slug>`, absolute) and the prune command `run.py prune <run> --days '${user_config.retention_days}'`, so these placeholders are substituted.
5. **Resume rule.** A turn that finds the conversation compacted, or has no memory of the run's details: read `<run>/notes.md` (its Decisions section included) and run `run.py status` before acting; then read the reference file for what you are about to do. Never re-run Setup or start sessions again.
6. **The wake loop.** Handle what woke you, using the event, `verdict` and status lines `wait --full` printed; make sure exactly one `run.py wait <run> --full` runs before the turn ends. The one-line reply rule.
7. **The verdict rule.** A `verdict <sha> {…}` line for the event's own sha is the check: covered gets `go`; not covered gets `redo` with its `reasons`. For `READY`/`REMIND` it satisfies the merge bar's check only when its sha is the event's head sha. `unavailable`, or any other sha: invoke `branch-review:check` as before; if the reason is `rs not found`, re-record with `check-cmd` from the check skill's paths.
8. **Compaction ask.** When `status`'s last line ends `, compact due` and a `wait` is running, end the reply with the exact compact line (Global Constraints), then run `run.py compact-asked <run>`.
9. **Decisions.** An owner decision that is not an item answer goes into `notes.md` under `## Decisions` in the same turn: one line, `YYYY-MM-DD: <decision>`.
10. The red flags that guard pushes and merges (the "Red flags" table, trimmed), with "All checks pass, merge" and "The child says its review is clean" reworded to allow the same-sha printed verdict.
11. A "Situation → read" table naming each reference file.

**Changes inside the moved text:**
- `setup.md`: step 2 also loads `branch-review:check` once and runs `run.py check-cmd <run> --rs <its rs path> --state-dir <its CLAUDE_PLUGIN_DATA>` (the paths the loaded check skill shows); step 3 also runs `run.py set <run> manager <own id>` (from `get_session "self"`), writes `<run>/facts.md` with repository facts every child needs (formatter, validation commands, PR rules), records each unit's `--spec`, and adds an empty `## Decisions` section to `notes.md`; step 6 starts `run.py wait <run> --full`.
- `children.md`: for each unit, write the unit-specific text to a file, run `run.py brief <run> <unit> --extra-file <file>` (`--no-gate` / `--designed` where the old text said so), then `start_session` with the start prompt from Task 3. The parts list itself is gone; it lives in the templates.
- `events.md`: the `STUCK` rows take `<sha>` from the `pushes:` / `requests:` lines `wait --full` printed, instead of reading the logs. "Answering a push request" step 3 reads the `verdict` line when it is for the request's sha, and invokes `branch-review:check` only otherwise. "On every user message" still runs `run.py status` (a user message has no `wait` output).
- `merge-bar.md`: the check bullet accepts the same-sha `verdict` line from a `READY`/`REMIND` event.

- [ ] **Step 1: Write the failing tests** in `tests/test_skill_text.py`:
  - `test_core_under_limit`: `len(SKILL.md text) < 17500`.
  - `test_no_dollar_digit`: no match for `\$[0-9]` in `SKILL.md`.
  - `test_references_have_no_placeholders`: no `${` and no `$ARGUMENTS` in any `references/*.md`.
  - `test_every_named_reference_exists`: every `references/<name>.md` named in `SKILL.md` exists, and every file in `references/` is named in `SKILL.md`.
  - `test_core_has_compact_line`: `SKILL.md` contains `/compact Resume as the implementation manager for <run>: read notes.md, then run.py status, before acting.`
  - `test_core_has_resume_rule`: `SKILL.md` contains `notes.md` and `run.py status` in the same paragraph as `compacted`.
  - `test_prune_uses_retention_option`: `SKILL.md` contains `run.py prune <run> --days '${user_config.retention_days}'`; `references/setup.md` names prune before `run.py init`. (Replaces `test_prune_runs_before_init_with_retention_option`.)
  - `test_wait_is_full_everywhere`: every `run.py wait` in `SKILL.md` and `references/` is followed by `<run> --full` (allowing the `--stall-hours` option).
  - `test_merge_bar_same_sha`: `references/merge-bar.md` contains `same` and `sha` in the bullet that mentions `verdict`.

- [ ] **Step 2: Run: FAIL.**

- [ ] **Step 3: Restructure** `SKILL.md` and create the references as described above. Move whole sections; trim only duplicates.

- [ ] **Step 4: Run the whole suite: PASS.** Also check: `wc -c skills/implementation-manager/SKILL.md` prints under 17500.

- [ ] **Step 5: Read-through.** Read the core as a compacted manager would (core only, no references), and confirm a `REQUEST`, a `READY`, an owner answer and a `compact due` line can each be handled by following the core to the right reference. Fix the table where it cannot.

- [ ] **Step 6: Commit.**

```bash
git add plugins/implementation-manager/skills plugins/implementation-manager/tests/test_skill_text.py
git commit -m "refactor(implementation-manager): compaction-safe skill core with reference files"
```

---

### Task 5: Evals

**Files:**
- Modify: `evals/push-queue/scaffold.sh`, `evals/push-queue/prompt.md`
- Delete: `evals/push-queue/graders/check-ran.md`
- Create: `evals/push-queue/graders/verdict-used.md`, `evals/compaction-resume/{case.yaml,prompt.md,scaffold.sh,graders/*.md}`
- Unchanged: `evals/push-gate/*`, `evals/owner-queue/*`

**Interfaces:**
- Consumes: `check-cmd` (Task 2), `set manager` (Task 3), the core's resume rule (Task 4).

- [ ] **Step 1: push-queue.** At the end of `scaffold.sh`, record the check: `python3 "$runpy" check-cmd run --rs "$here/../../../branch-review/scripts/rs" --state-dir "$ws/state"`. In `prompt.md`, say `run.py wait --full` instead of `run.py wait`. Replace `check-ran.md` with `verdict-used.md`: an `llm` grader that passes when the manager answered both requests from the `verdict` lines and never invoked the `branch-review:check` skill. `queue.md` and `hold.md` stay as they are.

- [ ] **Step 2: compaction-resume case.** Copy push-queue's scaffold (two covered units, both requests in `requests.log`), record `check-cmd`, `set manager local_00000000-0000-0000-0000-000000000000`, and add to `notes.md`:

```
## Decisions

2026-11-08: Do not let u2 push until the owner confirms its migration order.
```

  `prompt.md` opens as a compacted session: "This session was compacted. Summary: you are the implementation manager for the run in `run` under the current directory; two push requests arrived before the compaction." Then the same environment notes as push-queue. Graders:
  - `skill-fired.md` (`tool_used`, the skill), as in push-queue.
  - `resume.md` (`llm`): passes when the agent read `run/notes.md` and ran `run.py status` before its first `go`, `redo`, `queue` or `hold`, and never ran `run.py init` or `run.py unit`.
  - `pushes.md` (`llm`, focus `run/pushes.log`): `u1 go <sha1>` present; no `u2 go` line (a `u2 redo … held` or other `redo` is fine).

- [ ] **Step 3: Run the evals** from the repo root, one run each, via `zsh -lc` so the token in `~/.zprofile` is loaded:

```bash
zsh -lc 'claude plugin eval plugins --case push-gate --scaffold --allow-tools Bash Write Edit SendMessage ToolSearch --runs 1 --ablation none --json /tmp/im-push-gate.json'
```

  Repeat for `push-queue`, `owner-queue` and `compaction-resume`. Expected: every grader passes. Then run each with the default three runs and baseline (drop `--runs 1 --ablation none`). Record the results in the PR description.

- [ ] **Step 4: Commit.**

```bash
git add plugins/implementation-manager/evals
git commit -m "test(implementation-manager): push-queue uses the printed verdict; compaction-resume eval"
```

---

### Task 6: Docs, tech debt and version

**Files:**
- Modify: `README.md`, `ARCHITECTURE.md`, `.claude-plugin/plugin.json` (`"version": "0.10.0"`), `docs/tech-debt/README.md`
- Create: `docs/tech-debt/TD-0012-implementation-manager-context-from-transcript.md`, `docs/tech-debt/TD-0013-implementation-manager-recorded-check-path.md`, `docs/tech-debt/TD-0014-implementation-manager-compaction-skill-limit.md`

- [ ] **Step 1: README.** Add a short section in Simplified Technical English: the manager can ask you to compact; paste the line it gives; it is safe at any time; if you do not compact, the run continues and uses more tokens.

- [ ] **Step 2: ARCHITECTURE.md.** Document: the core-plus-references layout and the 17,500-character rule with its test; that `${...}` placeholders live only in the core; `run.py brief` and the templates (placeholder list, goldens and how to regenerate them); `wait --full` output; `check-cmd` and the verdict fallback; the `context` line, `compact due` and `compact-asked`; the new `compaction-resume` eval in the evals list; the measurement method (weighted input-token equivalents: cache reads 0.1, cache writes 1.25, output 5, from the manager transcript's `usage` fields, per unit).

- [ ] **Step 3: Tech debt.** Three items, Impact `Low`, Added `2026-11-09`, following `docs/tech-debt/README.md`:
  - TD-0012: the `context` line parses the transcript's undocumented JSON layout; if it changes, the line disappears and the manager never asks to compact.
  - TD-0013: the recorded `rs` path goes stale when branch-review updates mid-run; each event then costs a fallback check until the manager re-records it.
  - TD-0014: the compaction design relies on the 5,000-token skill re-injection limit documented on 2026-11-09; a smaller limit would cut the core.
  Update "Next id" to `TD-0015` and add the three rows.

- [ ] **Step 4: Validate.** `claude plugin validate .` and `claude plugin validate plugins/implementation-manager`: both pass. Full test suite: PASS. `ruff check .` clean.

- [ ] **Step 5: Commit.**

```bash
git add plugins/implementation-manager/README.md plugins/implementation-manager/ARCHITECTURE.md plugins/implementation-manager/.claude-plugin/plugin.json docs/tech-debt
git commit -m "docs(implementation-manager): compaction, briefs and verdict docs; tech debt; 0.10.0"
```
