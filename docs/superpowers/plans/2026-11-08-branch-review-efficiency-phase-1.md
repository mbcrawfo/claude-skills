# branch-review efficiency, Phase 1: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every branch-review reviewer cheaper per turn, and build the two cheap measuring tools (usage report, small replay) that later phases are decided with.

**Architecture:** Reviewer-side changes are text only: a short reviewer-facing schema split out of `finding-schema.md`, and a trimmed `agents/reviewer.md` that asks for parallel tool calls, skips double reads of added files and ends with a one-line message. Two standard-library Python contributor tools live under `plugins/branch-review/evals/`: `usage/usage_report.py` reads local transcripts and review state into cost and findings tables, and `replay/replay.py` re-runs a reviewer version on 3–4 past deep rounds and scores it against what those rounds cost and found.

**Tech Stack:** Markdown agent/skill text; Python 3.9+ standard library; `unittest`; ruff 0.17.2; the `claude` CLI (`-p`, `--plugin-dir`, `--agent`, `--output-format stream-json`, `--max-budget-usd`) for the replay only.

**Spec:** `docs/superpowers/specs/2026-11-08-branch-review-split-deep-round-design.md` (Phase 1 only). Phase 2 (split deep round) and Phase 3 (second-opinion decision) get their own plan once Phase 1 has run in real use; Task 5 records them in `docs/deferred`.

## Global Constraints

- Python 3.9+, standard library only, invoked through `python3`; `from __future__ import annotations` where `X | None` hints are used.
- Tests live in `plugins/branch-review/tests/` and run with `python3 -m unittest discover -s plugins/branch-review/tests`. No test calls the `claude` CLI or reads the real `~/.claude`.
- `ruff check .` and `ruff format .` clean; add new first-party module names to `known-first-party` in the root `pyproject.toml`.
- Prices: Opus $5 input / $25 output, Sonnet $3 / $15 per million tokens. Cache write 5 min = 1.25× input, 1 h = 2× input, cache read = 0.1× input. Haiku and Fable rows: look up current list prices with the `claude-api` skill when writing the table.
- Each check costs tens of dollars at most: the replay's default total cap is $40, and each reviewer run is capped with `--max-budget-usd`.
- Nothing from mbcrawfo/stashman in the repo. Replay sets and outputs are written under `plugins/branch-review/evals/results/` (already gitignored).
- Plugin version becomes `0.15.0`.
- READMEs follow the repo's Simplified Technical English rules; internals go in `ARCHITECTURE.md` or the scripts' docstrings and `--help`.
- Run `claude plugin validate .` and `claude plugin validate plugins/branch-review` before committing.

## Review Focus

1. **Transcripts with repeated assistant lines.** Claude Code writes one assistant message as several JSONL lines with the same `message.id`; counting each line's `usage` double-counts. Expect one usage per message id (the last line's). Test in Task 1.
2. **State files from the test suite.** `~/.claude/plugins/data/*` holds `review.json` files written by tests (models `m`, `test-model`). Expect the usage report to skip any state whose rounds name a model outside `opus|sonnet|haiku|fable`. Test in Task 1.
3. **A replay whose `begin-round` lands on a different diff.** If the base moved, `begin-round` could compute a `diff_from` that is not round 1's. Expect the replay to abort that branch with a clear message, not review the wrong range. Test in Task 4.
4. **A reviewer run that hits its budget or writes no report.** Expect the replay to record the branch as `failed` with its spend, count it as a fail in the score, and go on to the next branch. Test in Task 4.
5. **Second-opinion findings about code round 1 never saw.** Expect them excluded from the known list when the file is absent at `DIFF_TO` or its start line is past the file's end, and listed as excluded. Test in Task 4.

---

## File Structure

| Path | Change | Responsibility |
| --- | --- | --- |
| `plugins/branch-review/evals/usage/usage_report.py` | Create | Read transcripts and `review.json`; cost, join, summarize; CLI |
| `plugins/branch-review/evals/replay/replay.py` | Create | `pick`, `run`, `score` subcommands for the small replay |
| `plugins/branch-review/evals/replay/judge-prompt.md` | Create | Prompt for the Sonnet judge |
| `plugins/branch-review/skills/review/report-schema.md` | Create | Reviewer-facing output block rules (moved from `finding-schema.md`) |
| `plugins/branch-review/skills/review/finding-schema.md` | Modify | Keep the state reference; point to `report-schema.md` |
| `plugins/branch-review/skills/review/SKILL.md` | Modify | `SCHEMA_PATH` → `report-schema.md` |
| `plugins/branch-review/agents/reviewer.md` | Modify | Parallel calls, no double reads, one-line final message, trim |
| `plugins/branch-review/tests/helpers.py` | Modify | Put `evals/usage` and `evals/replay` on `sys.path` |
| `plugins/branch-review/tests/test_usage_report.py` | Create | Task 1 tests |
| `plugins/branch-review/tests/test_replay.py` | Create | Task 4 tests |
| `plugins/branch-review/tests/test_prompt_text.py` | Modify | Tasks 2–3 tests |
| `plugins/branch-review/tests/test_args.py` | Modify | Version literal `0.15.0` |
| `plugins/branch-review/.claude-plugin/plugin.json` | Modify | `0.15.0` |
| `plugins/branch-review/ARCHITECTURE.md` | Modify | Schema split; usage report and replay under Tests and evals |
| `pyproject.toml` | Modify | `known-first-party` += `usage_report`, `replay` (test modules are already covered by `test_*`) |
| `docs/deferred/D-0004-branch-review-split-deep-round.md`, `docs/deferred/README.md` | Create / Modify | Phases 2–3 |

---

### Task 1: Usage report

**Files:**
- Create: `plugins/branch-review/evals/usage/usage_report.py`
- Modify: `plugins/branch-review/tests/helpers.py` (add `EVALS_DIR / "usage"` and `EVALS_DIR / "replay"` to `sys.path`, as it does for `scripts/`)
- Modify: `pyproject.toml` (`known-first-party` += `"usage_report"`, `"replay"`)
- Test: `plugins/branch-review/tests/test_usage_report.py`

**Interfaces:**
- Produces:
  - `PRICES: dict[str, tuple[float, float]]` — family → (input, output) USD per million tokens.
  - `model_family(model: str | None) -> str | None` — `"opus"`, `"sonnet"`, `"haiku"`, `"fable"` by substring of a model id or Agent-tool name; `None` otherwise.
  - `message_usages(lines: Iterable[str]) -> list[tuple[str | None, dict]]` — one `(model, usage)` per distinct assistant `message.id`, last line wins, in first-seen order. Works for subagent transcripts and for `claude -p --output-format stream-json` output.
  - `cost_usd(usage: dict, family: str) -> float` — formula in Global Constraints; reads `cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`, falling back to `cache_creation_input_tokens` as 5 min when the breakdown is absent.
  - `parse_dispatch(prompt: str) -> dict` — keys `mode`, `focus`, `repo`, `diff_from`, `diff_to`, `changed_files: list[str]`, `plugin_version` (from the `branch-review/<version>/` segment of `CHECKLIST_PATH`, else `None`).
  - `@dataclass ReviewerRun`: `path: Path`, `started: str`, `model: str | None`, `cost: float`, `turns: int`, `peak_context: int`, `tool_calls: int`, `dispatch: dict`.
  - `reviewer_runs(projects_dir: Path, since: date, until: date) -> list[ReviewerRun]` — every `*/*/subagents/*.meta.json` whose `agentType` is `branch-review:reviewer` and whose transcript has a timestamp in range.
  - `state_rounds(state_dirs: list[Path], since: date, until: date) -> list[dict]` — one dict per round from every `review.json` under the dirs: `branch`, `number`, `mode`, `focus`, `model`, `shape` (`round.get("shape", "single")`), `plugin_version`, `diff_from`, `diff_to`, `changed_files`, `findings: list[dict]` (opened in that round, `scope == "change"`), `started`. Skips a whole state file when any round's `model` has no `model_family`.
  - `join(runs, rounds) -> list[dict]` — round dict plus `run` (the `ReviewerRun` with the same `diff_from`/`diff_to`, else `None`).
  - `summarize(joined: list[dict]) -> dict` — groups keyed `"<mode>/<focus or ->/<model>/<shape>/<plugin_version>"`, each with `rounds`, `cost_total`, `cost_median`, `cost_p90`, `turns_median`, `peak_median`, `calls_per_turn`, `major`, `minor`, `critical`, `invalid`, `duplicate`; plus `second_opinions: list[{"branch", "majors", "prior_shape"}]`, `prior_shape` being the shape of the latest deep round before it on that branch.
  - CLI: `python3 usage_report.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--projects-dir PATH] [--state-dir PATH ...] [--json]`. Defaults: last 14 days, `~/.claude/projects`, every directory under `~/.claude/plugins/data/` whose name starts with `branch-review`. Text output is one table row per group plus a second-opinion line per prior shape (count, majors per second opinion).

- [ ] **Step 1: Write the failing tests** in `test_usage_report.py`. Build fixtures in `tempfile.TemporaryDirectory()`: a projects dir with one session holding two reviewer subagents (`.meta.json` + `.jsonl`) and one non-reviewer subagent, and a state dir with two `review.json` files (one real, one with model `test-model`). Tests:
  - `test_repeated_message_lines_count_once`: three lines with the same `message.id` and growing `output_tokens` 10, 20, 30 → one usage with `output_tokens == 30`.
  - `test_cost_uses_cache_breakdown`: usage `{"input_tokens": 1_000_000, "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}, "cache_read_input_tokens": 1_000_000, "output_tokens": 1_000_000}` on opus → `5 + 6.25 + 10 + 0.5 + 25 == 46.75`.
  - `test_cost_falls_back_to_total_cache_creation`: no `cache_creation` key, `cache_creation_input_tokens: 1_000_000` on sonnet → `3.75`.
  - `test_parse_dispatch_reads_fields`: a prompt in the SKILL.md template shape with three `CHANGED_FILES` lines and `CHECKLIST_PATH: /x/branch-review/0.14.0/skills/review/reviewer-checklist.md` → mode, focus `None`, three files, version `"0.14.0"`.
  - `test_only_reviewer_subagents_are_read`: `reviewer_runs` returns 2 runs.
  - `test_test_model_state_is_skipped`: `state_rounds` returns only the real file's rounds.
  - `test_join_matches_on_diff_range` and `test_summarize_groups_and_counts`: expected group keys, `rounds`, `major`/`minor` counts, and a second opinion whose `prior_shape` is `"single"`.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_usage_report.py' -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'usage_report'`.

- [ ] **Step 3: Implement `usage_report.py`** with the interfaces above. The module docstring explains what it reads, the price table's source and that it writes nothing.

- [ ] **Step 4: Run the tests and see them pass**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -v`
Expected: all tests OK.

- [ ] **Step 5: Check against the analysis.** Run `python3 plugins/branch-review/evals/usage/usage_report.py --since 2026-10-25 --until 2026-11-08`. Expected: deep/opus first-deep rounds total near $1,279 and second opinions near $481 (within a few percent; the spec's numbers came from the same method).

- [ ] **Step 6: Lint and commit**

```bash
ruff check . && ruff format .
git add plugins/branch-review/evals/usage plugins/branch-review/tests/helpers.py plugins/branch-review/tests/test_usage_report.py pyproject.toml
git commit -m "feat(branch-review): usage report for reviewer cost and findings"
```

---

### Task 2: Reviewer-facing report schema

**Files:**
- Create: `plugins/branch-review/skills/review/report-schema.md`
- Modify: `plugins/branch-review/skills/review/finding-schema.md`, `plugins/branch-review/skills/review/SKILL.md` (section 5 template line `SCHEMA_PATH`), `plugins/branch-review/agents/reviewer.md` (Inputs fallback path)
- Test: `plugins/branch-review/tests/test_prompt_text.py`

**Interfaces:**
- Produces: `report-schema.md`, the file `SCHEMA_PATH` names from now on. It holds what is today `finding-schema.md`'s "Reviewer output block" section (block, `variants`/`fix_options`, Coverage, required keys, Errors, check-report, verdict rules, `outside_change`), nothing about `review.json`. `finding-schema.md` keeps every other section and opens with one line pointing to `report-schema.md` for the reviewer's block.

- [ ] **Step 1: Write the failing tests** in `test_prompt_text.py`: add `REPORT_SCHEMA` read from `report-schema.md`; move `SchemaDocTest.test_schema_documents_every_coverage_item` to check `REPORT_SCHEMA`; keep `test_schema_documents_every_status` on `SCHEMA`; add:
  - `test_report_schema_is_small`: `len(REPORT_SCHEMA.encode()) <= 8000`.
  - `test_report_schema_has_no_state_reference`: `"review.json" not in REPORT_SCHEMA` and `"## Statuses" not in REPORT_SCHEMA`.
  - `test_dispatch_passes_report_schema`: section `5. Dispatch` of `SKILL` contains `SCHEMA_PATH: ${CLAUDE_SKILL_DIR}/report-schema.md`; `Inputs` of `REVIEWER` contains `skills/review/report-schema.md`.

- [ ] **Step 2: Run and see them fail**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_prompt_text.py' -v`
Expected: FAIL / ERROR on the new tests (`report-schema.md` missing).

- [ ] **Step 3: Split the file and update the two references.** Move text, do not rewrite rules; tighten only where the 8,000-byte limit needs it.

- [ ] **Step 4: Run the whole suite and see it pass**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -v`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/branch-review/skills/review plugins/branch-review/agents/reviewer.md plugins/branch-review/tests/test_prompt_text.py
git commit -m "refactor(branch-review): reviewer reads a short report schema"
```

---

### Task 3: Leaner reviewer definition

**Files:**
- Modify: `plugins/branch-review/agents/reviewer.md`
- Test: `plugins/branch-review/tests/test_prompt_text.py`

**Interfaces:**
- Consumes: Task 2's `report-schema.md`.
- Produces: the reviewer's final message is one line naming `REPORT_PATH` and the check result; the full report goes in the final message only when the reviewer could not write `REPORT_PATH`. SKILL.md section 6.2 (save the final message when the file is missing) stays as it is and still covers that case.

Required text changes:
1. **Context management** gains: send independent reads, greps and `git` commands in one message as parallel tool calls (all rule files at once; a batch of greps at once; the diffs of unrelated files at once); keep a call sequential only when it needs an earlier result.
2. **Deep step 2**: for a file the change adds, the diff is the whole file; do not read it again unless line numbers the diff lacks are needed. Setup step 3 reads the rule files in one parallel batch (the "one at a time" wording goes; never several in one shell command stays).
3. **Output**: replace "Your final message is the same report, word for word." with the one-line rule above.
4. **Trim** to at most 24,000 bytes (from 30,589): each process's last step becomes one line pointing to Output's write-and-check procedure (still naming `REPORT_PATH`, `tail -n 3` and `CHECK_COMMAND`, which existing tests require); remove restatements of Output inside the process sections; shorten Inputs entries to what the reviewer acts on. Do not remove any required step, Evidence rule, Scratch-files rule or the inlined JSON shape.

- [ ] **Step 1: Write the failing tests** in `test_prompt_text.py`:
  - `test_reviewer_definition_is_small`: `len(REVIEWER.encode()) <= 24000`.
  - `test_asks_for_parallel_tool_calls`: `"parallel tool calls"` in section `Context management`.
  - `test_final_message_is_one_line`: section `Output` does not contain `"same report, word for word"` and contains `"one line"`.
  - `test_added_files_are_not_read_twice`: section `Process for deep mode` contains `"adds"` and `"the diff"` in the same numbered step 2.
  - `test_required_steps_still_named`: section `Process for deep mode` contains each of `"Break each new test on purpose"`, `"Grep the docs for the old statement"`, `"Probe claims about real systems"`, `"Grep for promises the change fulfils"`, `"Run tests that measure process-wide state"`, `"Build what users build"`, `"Check broad mappings"`, `"Probe equivalence classes"`, `"Name the whole family"`, `"Compare related branches"`.

- [ ] **Step 2: Run and see the new tests fail** (the last one passes today; it guards the trim)

Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_prompt_text.py' -v`
Expected: 4 new FAILs.

- [ ] **Step 3: Edit `reviewer.md`** as listed.

- [ ] **Step 4: Run the whole suite and see it pass**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -v`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/branch-review/agents/reviewer.md plugins/branch-review/tests/test_prompt_text.py
git commit -m "perf(branch-review): parallel tool calls, no double reads, one-line hand-back"
```

---

### Task 4: Small replay

**Files:**
- Create: `plugins/branch-review/evals/replay/replay.py`, `plugins/branch-review/evals/replay/judge-prompt.md`
- Test: `plugins/branch-review/tests/test_replay.py`

**Interfaces:**
- Consumes: `usage_report.reviewer_runs`, `state_rounds`, `join`, `message_usages`, `cost_usd`, `model_family`, `parse_dispatch` (Task 1).
- Produces:
  - Replay set file (JSON list), one entry per branch: `repo`, `branch`, `base_ref`, `diff_from`, `diff_to`, `intent`, `state_file`, `round`, `old_cost`, `old_transcript`, `second_opinion_round`.
  - `pick_candidates(joined: list[dict], states: dict[str, dict], min_files=5, max_files=15, min_version="0.12.0") -> list[dict]` — first deep rounds (`mode == "deep"`, no focus, `mode_reason` in `no-state`, `no-rounds`) with a matched run, a later second-opinion round on the same branch, `min_files <= len(changed_files) <= max_files`, plugin version at least `min_version`; returns set entries.
  - `known_findings(state: dict, entry: dict, line_count: Callable[[str], int | None]) -> tuple[list[dict], list[dict]]` — `(known, excluded)`. Known = round-1 findings plus second-opinion findings (`scope == "change"`), each tagged `origin: "round-1" | "second-opinion"`. A second-opinion finding is excluded when `line_count(file)` is `None` (absent at `DIFF_TO`) or the first number of its `lines` exceeds it.
  - `dispatch_prompt(fields: dict) -> str` — the SKILL.md section 5 template for a deep round with no optional lines: `MODE`, `REPO`, `BASE_REF`, `DIFF_FROM`, `DIFF_TO`, `REPORT_PATH`, `CHECKLIST_PATH`, `SCHEMA_PATH`, `CHECK_COMMAND`, `CHANGED_FILES`, `INTENT`, `OPEN_FINDINGS` (`[]`) and the closing sentence.
  - `score(results: list[dict]) -> dict` — per branch: `old_cost`, `new_cost`, `status` (`ok` or `failed`), `majors_missed` (round-1 majors not matched), `minors_found`, `new_held`, `new_not_real`, `duplicates`; overall `passed: bool` = every branch `ok`, no round-1 major missed, `new_not_real == 0` across the set, and `new_cost < old_cost` on all but at most one branch; plus `spend` (reviewer + judge).
  - CLI:
    - `replay.py pick [--since] [--until] [--projects-dir] [--state-dir ...] [--min-files 5] [--max-files 15] [--min-version 0.12.0] [-o PATH]` — writes candidates (default `plugins/branch-review/evals/results/replay-set.json`) and prints them with sizes and old costs, for the owner to trim to 3–4.
    - `replay.py run --set PATH --plugin PATH [--out DIR] [--round-budget-usd 15] [--max-total-usd 40]` — per entry, in order: stop before an entry if spend ≥ `--max-total-usd`; `git worktree add --detach <out>/<i>/repo <diff_to>` in the entry's repo; `rs begin-round --state-dir <out>/<i>/state --repo <worktree> --mode deep --mode-reason replay --model opus --snapshot <diff_to> --base-ref <diff_from> --intent-file <out>/<i>/intent.md` using `<plugin>/scripts/rs`; abort the entry (status `failed`, reason `diff mismatch`) unless the returned `diff_from` equals the entry's; run `claude -p --plugin-dir <plugin> --agent branch-review:reviewer --model opus --output-format stream-json --verbose --max-budget-usd <round budget> --allowedTools Read Grep Glob Bash` in the worktree with `dispatch_prompt` on stdin, saving `<out>/<i>/stream.jsonl`; mark `failed` if `REPORT_PATH` is missing or has no JSON block; run the judge (`claude -p --model sonnet --output-format stream-json --verbose --max-budget-usd 2`, prompt from `judge-prompt.md` filled with known findings and the replay findings, in the same worktree); save `<out>/<i>/result.json`; remove the worktree. Print running spend after each entry and `score` at the end.
    - `replay.py score --out DIR` — re-scores saved results, spending nothing.
  - Judge output: the last fenced `json` block, a list of `{"replay": <index>, "label": "matches" | "new" | "duplicate", "of": "<known id or replay index>", "holds": true | false | null, "check": "<what it ran or read>"}`; `holds` is set only for `new`.

- [ ] **Step 1: Write the failing tests** in `test_replay.py` (no `claude` calls; the runner is injected as a callable so `run` can be tested with a fake):
  - `test_pick_filters_by_size_version_and_second_opinion`: fixture joined rounds → only the 5–15-file, ≥0.12.0, followed-by-second-opinion branch is picked.
  - `test_second_opinion_findings_outside_snapshot_are_excluded`: `line_count` returns `None` for one file and `10` for another; findings at `lines "12-14"` and on the absent file are excluded, one at `"3"` is kept.
  - `test_dispatch_prompt_matches_skill_template`: every label `dispatch_prompt` emits appears as a line start in section `5. Dispatch` of `skills/review/SKILL.md`, and `SCHEMA_PATH` ends with `report-schema.md`.
  - `test_score_fails_on_missed_round1_major`, `test_score_fails_on_not_real_finding`, `test_score_allows_one_costlier_branch`, `test_score_fails_on_two_costlier_branches`.
  - `test_run_aborts_on_diff_mismatch`: fake `rs` result with a different `diff_from` → entry `failed`, reason `diff mismatch`, no reviewer call.
  - `test_run_marks_budget_hit_as_failed_and_continues`: fake reviewer writes no report for entry 1 → entry 1 `failed` with its spend, entry 2 still runs.
  - `test_run_stops_at_total_cap`: spend reaches `--max-total-usd` after entry 1 → entry 2 not started, reported as `skipped`.

- [ ] **Step 2: Run and see them fail**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_replay.py' -v`
Expected: ERROR, `No module named 'replay'`.

- [ ] **Step 3: Write `judge-prompt.md`.** It tells the judge: compare each replay finding to the known list by defect, not wording; label `duplicate` when two replay findings are one defect; for each `new` finding, check it in the worktree (read the lines, run the trigger when cheap) and set `holds`; end with the JSON block only once.

- [ ] **Step 4: Implement `replay.py`** with the interfaces above. Its module docstring and `--help` carry the procedure and the spend caps.

- [ ] **Step 5: Run the whole suite and see it pass**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -v`
Expected: OK.

- [ ] **Step 6: Lint and commit**

```bash
ruff check . && ruff format .
git add plugins/branch-review/evals/replay plugins/branch-review/tests/test_replay.py
git commit -m "feat(branch-review): small replay of past deep rounds"
```

---

### Task 5: Version, docs and deferred record

**Files:**
- Modify: `plugins/branch-review/.claude-plugin/plugin.json` (`"version": "0.15.0"`), `plugins/branch-review/tests/test_args.py:44` (`"0.15.0"`)
- Modify: `plugins/branch-review/ARCHITECTURE.md` — Round bullet: the reviewer reads `report-schema.md`; `finding-schema.md` is the state reference. Tests and evals: a "Usage report" paragraph (what it reads, the command, that it writes nothing) and a "Small replay" paragraph (`pick`, trim to 3–4, `run` with its caps and the `--allowedTools` it grants, `score`; that results stay in `evals/results/`).
- Modify: `plugins/branch-review/README.md` only where it describes the reviewer's hand-back or schema file; otherwise unchanged.
- Create: `docs/deferred/D-0004-branch-review-split-deep-round.md` (sections per `docs/deferred/AGENTS.md`: Planned = spec Phases 2 and 3; Delivered instead = Phase 1, the usage report and the replay; Why deferred = the spec builds Phase 2 only after Phase 1 runs in real use; Remaining work = the split deep round, `deep_round` option, multi-part recording, duplicates, worktree per part, real-use gates, second-opinion decision; Trigger = the usage report shows at least 15 Phase 1 first deep rounds with majors per round not below 0.58). Modify `docs/deferred/README.md`: add the row, `Next id: D-0005`.

- [ ] **Step 1: Make the edits above.**
- [ ] **Step 2: Verify**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -v && ruff check . && claude plugin validate . && claude plugin validate plugins/branch-review`
Expected: OK, no lint errors, both validations pass.

- [ ] **Step 3: Commit**

```bash
git add plugins/branch-review docs/deferred
git commit -m "chore(branch-review): 0.15.0, docs, defer split deep round (D-0004)"
```

---

### Task 6: Run the Phase 1 small replay

Spends real money: confirm with the owner before Step 2.

- [ ] **Step 1: Pick the set.** Run `python3 plugins/branch-review/evals/replay/replay.py pick --since 2026-10-25`. Trim the output file to 3–4 entries of different sizes, preferring SqlSource and this repo, none from stashman.
- [ ] **Step 2: Run it** (owner approves, cap $40): `zsh -lc 'python3 plugins/branch-review/evals/replay/replay.py run --set plugins/branch-review/evals/results/replay-set.json --plugin plugins/branch-review'` (the token is in `~/.zprofile`).
Expected: a score table and `passed: true`; spend at most $40.
- [ ] **Step 3: Owner spot-checks** the judge's `new` verdicts in each `result.json`.
- [ ] **Step 4: Report** the score and spend to the owner. On `passed: false`, stop: Phase 1 does not ship until the cause is fixed.
