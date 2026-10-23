# Implementation Manager Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the implementation-manager skill into this marketplace as its own plugin, with its CodeRabbit review gate replaced by a branch-review gate that the manager verifies before every PR, push and merge.

**Architecture:** branch-review gains two state-script features: clean updates from the base stay follow-up reviews, and a `covered` subcommand answers "is this commit covered by a clean review?". A thin `branch-review:check` skill exposes `covered` to other plugins. The new `implementation-manager` plugin carries the rewritten skill and a Python port of the PR watch script. Children request pushes by status token, and the manager answers through `pushes.log` in the run folder.

**Tech Stack:** Python 3.9+ (standard library only), git 2.40+ (`merge-tree --write-tree --merge-base`), `gh` CLI, Claude Code plugin skills, `unittest`, ruff 0.17.2.

**Spec:** `docs/superpowers/specs/2026-10-22-implementation-manager-design.md`

## Global Constraints

- Python 3.9+, standard library only, invoked through `python3`; use `from __future__ import annotations` for `X | None` hints, as `review_state.py` does.
- Tests live in `plugins/<name>/tests/` and run with `python3 -m unittest discover -s plugins/<name>/tests`; CI runs every plugin's tests on Python 3.9 and the `.python-version` interpreter.
- Run `ruff check .` and `ruff format .` (ruff `==0.17.2`, line length 120, double quotes) before every commit that touches Python.
- Run `claude plugin validate .` and `claude plugin validate plugins/<name>` before committing plugin changes.
- Plugin and skill names are kebab-case; skill frontmatter `name` matches its folder.
- Bump `branch-review` to `0.7.0`. `implementation-manager` starts at `0.1.0`.
- Skill files must never contain a dollar sign followed by a digit.
- No CodeRabbit support anywhere in the new plugin.
- The repo is public: no private repository names or content in skills, examples, fixtures or tests.
- AGENTS.md is the only agent-instruction file; never create CLAUDE.md.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A child resolves conflicts while merging main.** The conflict resolution is unreviewed code. `covered` must say not covered (`base-conflict`), and `locate` must say deep `base-moved`. Tested in Task 1 and Task 2.
2. **The reviewed snapshot was dirty, and the child then commits exactly those changes.** Expect covered, because the trees match even though the commit is new. Tested in Task 2.
3. **main advanced on the remote but the branch never merged it.** The merge base is unchanged, so coverage is unaffected and the result is covered. Tested in Task 2.
4. **The child amends or rebases after the review.** `locate` already says `history-rewritten`. `covered` must also say not covered, even when the trees happen to match. Tested in Task 2.
5. **The PR head changes between two watch polls (a push during the check).** The watch must not print `READY` for a state it has seen only once. Tested in Task 4.

---

### Task 1: branch-review: clean base updates stay follow-ups

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py`: `make_snapshot` (factor out the identity env), `choose_mode` (around line 315), `cmd_begin_round` (around line 401)
- Modify: `plugins/branch-review/skills/review/SKILL.md` (mode list in item 2.3, `OPEN_FINDINGS` paragraph in section 5)
- Modify: `plugins/branch-review/skills/review/finding-schema.md` (Mode selection table)
- Modify: `plugins/branch-review/README.md` (the set-up bullet list)
- Test: `plugins/branch-review/tests/test_snapshot.py` (the "base moved" block around line 462)

**Interfaces:**
- Produces: `simulate_base_update(repo: Path, anchor: dict, new_merge_base: str) -> str | None`. It returns the tree sha of `git merge-tree --write-tree --merge-base=<anchor merge_base> <anchor snapshot> <new_merge_base>`. It returns `None` on conflicts (exit 1) or any other failure, such as a git too old for the option. Only the first stdout line is the tree. Task 2 uses it.
- Produces: `_identity_env(repo: Path) -> dict`, the fallback-identity environment `make_snapshot` uses today, shared with the base-update commit.
- Produces: the new `locate` reason `base-updated`, in follow-up mode.

- [ ] **Step 1: Write the failing tests** in `test_snapshot.py`, next to `test_locate_deep_when_base_merged_into_branch`:
  - `test_locate_followup_when_base_merged_cleanly`: begin, record with one finding, commit `upstream.txt` on main, merge main into `feature/x`. Then `locate` returns `("followup", "base-updated")` and `open_findings` ids are `["R1-F1"]`.
  - `test_followup_after_clean_base_update_reports_only_branch_work`: as above, then commit `c.txt` on the branch. `begin(mode="followup")` gives `changed_files == ["c.txt"]`, and `diff_from` is a commit whose parents are the anchor snapshot and the new merge base (`git rev-list --parents -n1`).
  - `test_followup_after_clean_base_update_alone_is_empty`: merge main and nothing else. `begin(mode="followup")` returns `empty: True` and `saved: False`.
  - Rewrite `test_locate_deep_when_base_merged_into_branch` as `test_locate_deep_when_base_merge_had_conflicts`. Before the first round, the branch changes `a.txt` to `branch\n`. Main changes `a.txt` to `main\n`. Merge with `git merge main`, resolve by writing `resolved\n`, then `git add -A` and `git commit --no-edit`. Then `locate` returns `("deep", "base-moved")`, and a deep `begin` lists `a.txt` and `b.txt` but not main-only files.
  - `test_begin_followup_rejects_conflicting_base_update`: in the conflict setup, `begin(mode="followup")` exits 1 with stderr containing `conflicts`. Use `self.call`.
  - `test_simulate_base_update_without_merge_tree_support_is_none`: mock `review_state._git_try` to return `None` for `merge-tree`, and assert `simulate_base_update(...)` is `None`.

- [ ] **Step 2: Run the tests and check that they fail**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -k base -v`
Expected: the new tests FAIL, either on `base-moved` instead of `base-updated` or on a missing `simulate_base_update`.

- [ ] **Step 3: Implement**
  - `choose_mode`: when the merge base differs from the anchor's, call `simulate_base_update`. A tree means `followup`/`base-updated`. `None` means `deep`/`base-moved`. Check `paths-changed` after this, as today, so that it still forces deep.
  - `cmd_begin_round` in follow-up mode: if `_merge_base(repo, base_ref, head)` differs from the anchor's `merge_base`, simulate. On `None`, raise `StateError("cannot start a follow-up round: the base update since round <n> has conflicts; run a deep round")`. Otherwise set `diff_from` to `git commit-tree --no-gpg-sign <tree> -p <anchor snapshot> -p <new merge base> -m "branch-review base update"`, run with `_identity_env(repo)`.

- [ ] **Step 4: Run the whole suite and check that it passes**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Update the docs**
  - Review `SKILL.md` item 2.3 gets a new `base-updated` bullet: the base branch was merged in cleanly, so the follow-up's delta excludes the base's code. Mode is follow-up.
  - The `base-moved` bullet becomes: the base was merged in and the merge had conflicts (or git is older than 2.40). Mode is deep.
  - The finding schema's Mode selection table gets the same two rows.
  - The README's set-up list gets the same change.

- [ ] **Step 6: Lint and commit**

```bash
ruff check . && ruff format .
git add plugins/branch-review
git commit -m "feat(branch-review): clean base updates stay follow-up reviews"
```

### Task 2: branch-review: `covered` subcommand

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py`: new `cmd_covered`, plus its parser entry in `_build_parser`
- Test: create `plugins/branch-review/tests/test_covered.py`
- Modify: `plugins/branch-review/tests/test_state_dir.py`: add `("covered",)` to `COMMANDS`

**Interfaces:**
- Consumes: `simulate_base_update` (Task 1), `_recorded`, `_anchor`, `_merge_base`, `_is_ancestor`, `_object_exists`, `_stored_base`, `default_base`, `STATUSES`.
- Produces: `review_state.py covered [--commit SHA] [--base REF] --state-dir PATH --repo PATH`. It prints `{"covered": bool, "commit": <full sha>, "branch": str, "reasons": [str], "counts": {status: int}, "rounds": int}` and exits 0 whether or not the commit is covered. It exits 1 only on errors, such as an unknown commit, which raises `StateError("commit not found: <sha>")`. Task 3 and the manager rely on this.

`reasons` values, in this order, with every one that applies:

| reason | when |
| --- | --- |
| `no-rounds` | no state, or no recorded round (stop here; nothing else is checked) |
| `open-findings` | any finding `open` or `still-open` |
| `unverified-fixes` | any finding `fixed` |
| `escalated` | any finding `escalated` |
| `partial-scope` | the anchor round's `paths` is not empty |
| `snapshot-missing` | the anchor snapshot object is gone |
| `history-rewritten` | the anchor `head` is not an ancestor of the commit |
| `base-conflict` | the commit's merge base with the base ref differs from the anchor's, and `simulate_base_update` returned `None` |
| `changes-since-review` | the commit's tree (`<sha>^{tree}`) differs from the reviewed tree: the anchor snapshot's tree when the merge base is unchanged, else the simulated tree |

Skip the tree comparison when `snapshot-missing` or `history-rewritten` applies.

- [ ] **Step 1: Write the failing tests** in `test_covered.py`. Use the fixture style from `test_snapshot.py` (`make_repo`, `run`, `commit_file`, and `report`/`output`/`finding` from `test_ledger`). Each test asserts `covered` and the exact `reasons` list:
  - `test_no_state_is_not_covered`: `reasons == ["no-rounds"]`.
  - `test_clean_deep_round_covers_head`: a deep round recorded with no findings gives `covered: True, reasons: []`.
  - `test_open_finding_blocks`: `["open-findings"]`.
  - `test_fixed_unverified_blocks`: after `set-status fixed`, the result is `["unverified-fixes", "changes-since-review"]` when the fix was committed after the round.
  - `test_escalated_blocks` gives `["escalated"]`. `test_deferred_after_resolve_is_covered` gives `covered: True`.
  - `test_findings_closed_without_code_change_is_covered`: round 1 has one finding, and `set-status invalid` with a note. Covered, even though the last round added 1.
  - `test_new_commit_after_review_blocks`: `["changes-since-review"]`.
  - `test_dirty_snapshot_then_committed_is_covered` (Review Focus 2): edit `b.txt` without committing, then snapshot, begin, record clean, then commit the edit. Covered.
  - `test_clean_base_merge_is_covered`: merge main cleanly after a clean round. Covered.
  - `test_clean_base_merge_plus_work_blocks`: `["changes-since-review"]`.
  - `test_conflicting_base_merge_blocks` (Review Focus 1): `["base-conflict"]`.
  - `test_base_advanced_without_merge_is_covered` (Review Focus 3): main gets a commit that is not merged into the branch. Covered.
  - `test_rewritten_history_blocks` (Review Focus 4): after a clean round, `git commit --amend --no-edit` gives the same tree but a new head, and the result is `["history-rewritten"]`.
  - `test_partial_scope_blocks`: a round begun with `--paths b.txt` gives `["partial-scope"]`.
  - `test_commit_option_judges_that_commit_not_worktree`: dirty working tree, `--commit HEAD~0` of a covered commit. Covered.
  - `test_unknown_commit_errors`: `--commit deadbeef` exits 1 with `commit not found`.

- [ ] **Step 2: Run the tests and check that they fail**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -p 'test_covered.py' -v`
Expected: FAIL with `invalid choice: 'covered'`.

- [ ] **Step 3: Implement `cmd_covered(args) -> dict`** and register `covered` with `--commit` (default `HEAD`) and `--base`. The base is `args.base`, else the stored base, else `default_base(repo)`. Resolve the commit with `rev-parse --verify --quiet <sha>^{commit}`.

- [ ] **Step 4: Run the whole suite and check that it passes**

Run: `python3 -m unittest discover -s plugins/branch-review/tests -v`
Expected: all tests PASS, including `test_state_dir` with `covered` added.

- [ ] **Step 5: Lint and commit**

```bash
ruff check . && ruff format .
git add plugins/branch-review
git commit -m "feat(branch-review): covered subcommand checks a commit against the review"
```

### Task 3: branch-review: `check` skill, docs and version

**Files:**
- Create: `plugins/branch-review/skills/check/SKILL.md`
- Modify: `plugins/branch-review/README.md` (a new "Check a commit" section)
- Modify: `plugins/branch-review/.claude-plugin/plugin.json` (`"version": "0.7.0"`)

**Interfaces:**
- Consumes: `covered` (Task 2).
- Produces: the `branch-review:check` skill, with argument hint `--repo PATH [--commit SHA] [--state-dir PATH]`. It runs `python3 "${CLAUDE_SKILL_DIR}/../../scripts/review_state.py" covered --state-dir "<state>" --repo "<repo>" --commit "<sha>"`. `<state>` is `--state-dir` when given, else `${CLAUDE_PLUGIN_DATA}`. `<repo>` defaults to `git rev-parse --show-toplevel` of the cwd. The skill prints the JSON verdict verbatim and then one line: `covered` or `not covered: <reasons joined by ", ">`. It fixes nothing and starts no review. On exit 1 it reports the stderr line. The description says when to use it: an agent, often from another plugin, needs to know whether a commit is covered by a clean branch-review before a push, PR or merge.

- [ ] **Step 1: Write `skills/check/SKILL.md`** to the interface above. Follow the cleanup skill's prefix and state-dir wording, including the rule never to substitute a directory of your own.
- [ ] **Step 2: Write the README section, then bump the version.** The section covers what `covered` checks (the reasons table from Task 2, in prose) and that `/branch-review:check` exists for other agents. Then set the version to `0.7.0`.
- [ ] **Step 3: Validate**

Run: `claude plugin validate . && claude plugin validate plugins/branch-review`
Expected: both pass.

- [ ] **Step 4: Commit**

```bash
git add plugins/branch-review
git commit -m "feat(branch-review): check skill exposes the coverage check (0.7.0)"
```

### Task 4: implementation-manager plugin and PR watch script

**Files:**
- Create: `plugins/implementation-manager/.claude-plugin/plugin.json` (name `implementation-manager`, version `0.1.0`, author and license as branch-review's, keywords `orchestration`, `pull-requests`, `sessions`)
- Modify: `.claude-plugin/marketplace.json` (add the plugin with `"source": "./plugins/implementation-manager"`)
- Modify: root `README.md` (plugin list line)
- Create: `plugins/implementation-manager/scripts/wait_pr_ready.py`
- Test: create `plugins/implementation-manager/tests/helpers.py` and `plugins/implementation-manager/tests/test_wait_pr_ready.py`
- Modify: `pyproject.toml` (add `"wait_pr_ready"` to isort `known-first-party`)

**Interfaces:**
- Produces: `python3 wait_pr_ready.py <owner/repo> <branch> [stall-hours]`. Env: `POLL_SECONDS` (default 120), `SKIP_HEAD`, `ONCE`. Output lines, exactly one, then exit 0:
  - `READY <pr> <sha>`
  - `ATTENTION <pr> <sha> behind-base`
  - `MERGED <pr>`, `CLOSED <pr>`
  - `STALLED <pr> head=<sha7><reasons>`
  - `ERROR <message>`
  - with `ONCE=1`, also `NOT-READY <pr> head=<sha7><reasons>` or `NOT-READY - no open PR from <branch>`

  `<reasons>` is a space-led list from: ` draft`, ` merge=<state>`, ` no-checks`, ` checks-running=<n>`, ` checks-failed=<n>`, ` unresolved-threads=<n>`, ` over-100-threads`, ` skip-head`. Task 5's skill quotes these.
- Internal, for tests: `summarize(pr: dict) -> Summary` is a pure function over the GraphQL `pullRequest` object. `watch(repo, branch, *, stall_seconds, poll_seconds, skip_head, once, gh, sleep, now) -> str` returns the output line, with `gh(args: list[str]) -> str` raising `GhError` on failure.

Port `~/.claude/skills/implementation-manager/scripts/wait-pr-ready.sh` behavior for behavior, minus the CodeRabbit field, `REQUIRE_CODERABBIT` and `coderabbit-skipped`. It keeps the same GraphQL query, with output parsed by `json` instead of jq. It keeps the same rules: READY needs two identical consecutive summaries; the stall signature is head plus reasons; 10 consecutive gh failures mean ERROR (with `ONCE`, the first failure); there is no stall limit before a PR exists; `ONCE` polls every 30 s and reports after 2 polls; a `SKIP_HEAD` prefix match suppresses READY and ATTENTION. The header docstring carries the bash header's explanation, minus CodeRabbit.

- [ ] **Step 1: Write the failing tests** in `test_wait_pr_ready.py`, driving `watch` with a scripted fake `gh` (a list of responses), a no-op `sleep` and a fake clock:
  - `test_no_pr_waits_then_ready_after_two_identical_polls`
  - `test_ready_requires_two_identical_polls_head_change_resets` (Review Focus 5): poll 1 is clean at head A, poll 2 is clean at head B, and poll 3 is clean at head B. READY reports B only after poll 3.
  - `test_behind_base_is_attention` and `test_skip_head_suppresses_attention_and_ready`
  - `test_merged_and_closed`
  - `test_reasons` (table-driven over the `summarize` reasons above, including a StatusContext `PENDING` counted as running and a CheckRun `NEUTRAL` counted as passed)
  - `test_stalled_after_unchanged_signature`
  - `test_error_after_ten_failures` and `test_once_error_on_first_failure`
  - `test_once_not_ready_after_two_polls` and `test_once_no_pr`
  - `test_cli_prints_one_line`: runs the script as a subprocess with a fake `gh` executable on `PATH` that answers from a JSON file, plus `POLL_SECONDS=0`. Assert stdout is `READY 7 <sha>\n` and the exit code is 0.

- [ ] **Step 2: Run the tests and check that they fail**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests -v`
Expected: FAIL with `No module named 'wait_pr_ready'`.

- [ ] **Step 3: Implement `wait_pr_ready.py`, then the manifest, marketplace entry, root README line and `pyproject.toml` change.**

- [ ] **Step 4: Run the tests, lint and validate**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests -v && ruff check . && ruff format --check . && claude plugin validate . && claude plugin validate plugins/implementation-manager`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin/marketplace.json README.md pyproject.toml plugins/implementation-manager
git commit -m "feat(implementation-manager): plugin scaffold and Python PR watch script"
```

### Task 5: implementation-manager skill

**Files:**
- Create: `plugins/implementation-manager/skills/implementation-manager/SKILL.md`, starting from a copy of `~/.claude/skills/implementation-manager/SKILL.md`
- Create: `plugins/implementation-manager/README.md`

**Interfaces:**
- Consumes: `branch-review:review`, `branch-review:check` (Task 3) and its JSON `covered`/`reasons`, and `wait_pr_ready.py` (Task 4), at `${CLAUDE_SKILL_DIR}/../../scripts/wait_pr_ready.py`.
- Produces: the run-folder file `pushes.log`, with lines `<unit> go <sha>` and `<unit> redo <sha> <reason>`, written only by the manager. Also the status token `[push-request] <sha>`.

Edits to the copied skill, all from the spec's "Decided design":

- [ ] **Step 1: Remove CodeRabbit.** That means:
  - the argument-hint's CodeRabbit link, and the opening paragraph's CodeRabbit sentences;
  - setup step 3, and `coderabbit-brief.md` in step 4;
  - `REQUIRE_CODERABBIT` in step 7;
  - child prompt part 10;
  - the brief-refresh sentence at the top of Event handling;
  - the `coderabbit-skipped` row, and the "user changes the CodeRabbit brief" row;
  - the CodeRabbit merge-bar line and red flags.

  Update constraint 3 so the watch is the only signal for PR readiness. The opening paragraph's planning-PR sentence becomes: the manager reviews its own planning PR with `/branch-review:review` and gates it with `branch-review:check` like a child's.
- [ ] **Step 2: Setup.** Step 2 checks that `branch-review:check` and `branch-review:review` are available skills. If not, the manager stops and tells the user to install branch-review. Step 4 creates an empty `pushes.log` alongside `gates.log`. Step 7 starts `python3 ${CLAUDE_SKILL_DIR}/../../scripts/wait_pr_ready.py`.
- [ ] **Step 3: Child prompt.** Change part 7 (Pull request) and part 8 (status tokens), and add a new part between them on review and push permission, holding the spec's "Child flow" items:
  - Review with `/branch-review:review` with the unit's intent until it converges.
  - Escalations go to the manager by message. Record the user's quoted answer with `--resolve`.
  - Ask to push: commit, sync, re-review if needed, start the poll, end the turn with `[push-request] <full sha>`. The poll runs with `run_in_background`, using the run folder's absolute path: `f=<run>/pushes.log; until l=$(grep -E '^<name> (go|redo) <sha>( |$)' "$f" 2>/dev/null | tail -1) && [ -n "$l" ]; do sleep 30; done; echo "$l"`
  - On `go`, push exactly that sha. On `redo`, fix and ask again.
  - Verbatim: "An Auto-fix wake-up (CI failure, review comment, merge conflict) is never permission to push. Fix it locally, commit, and ask."
  - Put the review summary's Escalated, Deferred and Invalid sections in the PR description under "Review notes".

  Status tokens are `[gated]`, `[building]`, `[reviewing]`, `[fixing]`, `[push-request] <sha>`, `[waiting]`, `[blocked]`, `[review]`. Part 7's merge sentence becomes: the manager merges once every check passes, no thread is unresolved, and the review covers the head commit.
- [ ] **Step 4: Event handling.**
  - Add the `[push-request]` row: run `branch-review:check --repo <child worktree from get_session> --commit <sha>`. Covered: `printf '%s go %s\n' '<unit>' '<sha>' >> <run>/pushes.log`. Not covered: `printf '%s redo %s %s\n' '<unit>' '<sha>' '<reasons>' >> <run>/pushes.log`. The reply is one line.
  - Add the escalated-finding row: relay to the user with the child's recommendation and the manager's own, then forward the answer quoting the user.
  - The behind-base row's message becomes: sync with the base and ask to push.
- [ ] **Step 5: Merge bar and the `specs` unit.** Change the merge bar to the spec's three conditions: watch READY, child idle at `[review]`, and `branch-review:check` covered on the PR head. For `specs`, the bar is READY, no unpushed fix, and covered. Before each of its pushes, the manager runs `/branch-review:review` on its own branch to convergence (escalations go straight to the user) and pushes only when `branch-review:check` says covered.
- [ ] **Step 6: Red flags.**
  - Add: "The child says its review is clean" → run `branch-review:check` yourself.
  - Add: "Auto-fix woke the child; it can push the fix" → it asks first, every time.
  - Add: "It's only a merge from main" → the check decides; conflicts need a review.
  - Add: "I'll message the go" → append to `pushes.log`.
  - Delete the CodeRabbit rows.
- [ ] **Step 7: Check the skill.** `grep -n -i coderabbit` finds nothing. `grep -nE '\$[0-9]'` finds nothing. Frontmatter `name: implementation-manager`, with the description kept from the profile copy.
- [ ] **Step 8: Write the README:** what the plugin does, that it requires branch-review and the Claude desktop app's session tools, install lines, run-folder layout (`gates.log`, `pushes.log`, `notes.md`), and the watch script's usage.
- [ ] **Step 9: Validate and commit**

```bash
claude plugin validate . && claude plugin validate plugins/implementation-manager
git add plugins/implementation-manager
git commit -m "feat(implementation-manager): skill gated on branch-review instead of CodeRabbit"
```

### Task 6: implementation-manager eval case

**Files:**
- Create: `plugins/implementation-manager/evals/push-gate/case.yaml`, `scaffold.sh`, `prompt.md`, `graders/skill-fired.md`, `graders/gate.md`

**Interfaces:**
- Consumes: the `[push-request]` handling (Task 5) and `review_state.py covered` (Task 2).

`scaffold.sh` builds, in the empty workspace, one fixture repo (`repo/`, with an `origin` URL set so both checkouts share one state key). It has a branch for each of two units: `u1`, whose clean review covers HEAD, and `u2`, which has a commit after its clean review. `u2` is checked out in a linked worktree (`git worktree add ../wt-u2 u2`), because `covered` keys state by the checkout's current branch. It records state in `state/` with `../../../branch-review/scripts/review_state.py` (`begin-round` plus `record-findings`, using a minimal report like the tests' `report(output())`). It writes a run folder `run/` with `notes.md` (the units table, with worktree paths `repo/` for u1 and `wt-u2/` for u2) and an empty `pushes.log`. Graders check that `pushes.log` ends up with exactly `u1 go <sha1>` and `u2 redo <sha2> changes-since-review`. The prompt plays the manager. It names the run folder and says this run's state dir is `<workspace>/state`, to pass as `--state-dir`. It delivers two child notes: `[push-request] <sha1>` from u1 and `[push-request] <sha2>` from u2.

- [ ] **Step 1: Write the case files.** Follow the shape of `plugins/branch-review/evals/review-loop/`.
- [ ] **Step 2: Hand the run to Michael.** `claude plugin eval` is user-run: it needs `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` in the same shell. Ask Michael to run this from `plugins/implementation-manager`:

```bash
claude plugin eval . --case push-gate --runs 1 --scaffold --allow-tools Bash Write Edit Skill ToolSearch
```

  The eval loads only this plugin, so `branch-review:check` may not be available in the sandbox. If the run shows that, stop and report it to Michael. Per the spec, do not fake the check.
- [ ] **Step 3: Commit**

```bash
git add plugins/implementation-manager/evals
git commit -m "test(implementation-manager): push-gate eval case"
```

### Task 7: Final verification and retiring the profile copies

- [ ] **Step 1: Run every check**

Run: `for d in plugins/*/tests; do python3 -m unittest discover -s "$d" -v || exit 1; done && ruff check . && ruff format --check . && claude plugin validate . && for d in plugins/*/; do claude plugin validate "$d" || exit 1; done`
Expected: all pass.

- [ ] **Step 2: Retire the profile copies. Do this after the PR merges and Michael has installed `implementation-manager@mbcrawfo-skills`.** First ask Michael to confirm that no run is in progress under the old skills; `~/.claude/implementation-manager/` holds run folders. Then delete `~/.claude/skills/implementation-manager/` and `~/.claude/skills/coderabbit-manager/`. Leave `~/.claude/implementation-manager/` and `~/.claude/coderabbit-manager/` alone.
