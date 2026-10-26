# Implementation manager: phase 8 review changes, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add merge-order prerequisites, merge holds and display titles to the implementation manager's run script, and update the skill text and child prompt with the lessons from SqlSource phase 8. Ships as plugin version 0.4.0.

**Architecture:**
- Run state stays in `run.json`, through `runstate.py`. Three new unit fields hold the new state: `after`, `hold` and `title`.
- One predicate, `runstate.mergeable()`, combines "every `--after` unit merged or stopped" with "not held". `reportable()`, `go()`, the queue head and the watcher's `NEXT` all use it.
- The CLI (`run.py`) wires the new flags and commands and shows the new state in `status`.
- SKILL.md and the README describe it. One eval grader checks a hold.

**Tech Stack:** Python 3.9+ standard library, `unittest`, ruff; Claude Code skill markdown; `claude plugin eval`.

**Spec:** `docs/superpowers/specs/2026-10-25-implementation-manager-phase-8-review-design.md`

## Global Constraints

- Scripts: Python 3.9+, standard library only, invoked through `python3`. Lint and format with `ruff check .` and `ruff format .` (line length 120).
- Tests: `python3 -m unittest discover -s plugins/implementation-manager/tests` from the repo root. All must pass after every task.
- A `run.json` written by 0.3.1 must still load. Units missing `after`, `hold` or `title` get `[]`, `None` and `None`.
- Only `run.py` writes `run.json`, `gates.log` and `pushes.log`. A refused command exits 1 with one stderr line and changes nothing.
- Plugin version becomes `0.4.0` in `plugins/implementation-manager/.claude-plugin/plugin.json`.
- Run `claude plugin validate .` and `claude plugin validate plugins/implementation-manager` before the last commit.
- SKILL.md must never contain a dollar sign followed by a digit (its editing note).
- Parts 8–11 of the child prompt stay in the prompt, word for word, never in a file (spec anti-goal).

## Review Focus

1. **A WIP limit full of units waiting on `--after`.** The unit they wait for must still open (exemption in `openable()`). Test in Task 1.
2. **A held or `--after`-blocked unit first in the serial queue.** It must not block `go` for the next unit (`_first_queued` skips it, `NEXT` reports the next mergeable entry). Tests in Tasks 1 and 2.
3. **`hold` while the unit has a request the watcher already read.** It must get exactly one `redo <sha> held` line in `pushes.log`, and that request must not come back as `PENDING`. Test in Task 2.
4. **A 0.3.1 `run.json` loaded by 0.4.0, then written back.** Every unit gains the three fields, and `status` works. Test in Task 1.
5. **`--after` naming a unit that is also a `--prereq`, or one that closes a cycle through `--prereq` edges.** It must be refused with nothing written. Test in Task 1.

---

### Task 1: Merge-order prerequisites (`--after`)

**Files:**
- Modify: `plugins/implementation-manager/scripts/runstate.py` (`load`, `add_unit`, `openable`, `_first_queued`, `go`, `reportable`; new `mergeable`)
- Modify: `plugins/implementation-manager/scripts/watcher.py` (`next_event`)
- Modify: `plugins/implementation-manager/scripts/run.py` (`unit` parser, `_run`, `_status`, module docstring)
- Test: `plugins/implementation-manager/tests/test_runstate.py`, `tests/test_watcher.py`, `tests/test_run_cli.py`

**Interfaces:**
- Produces:
  - `add_unit(state, name, *, prereqs, branch, worktree, specs, after: list[str] | None = None, title: str | None = None) -> None`. `after=[]` clears the list; `None` keeps it.
  - Unit record keys `"after": []`, `"hold": None`, `"title": None`.
  - `mergeable(state: dict, unit: str) -> bool`.
  - `go` error text, serial mode: `unit {unit!r} merges after {u!r}; use queue to line it up until it merges`.

- [ ] **Step 1: Write the failing tests** in `test_runstate.py`. A new class `AfterTests(RunStateTestCase)`, with the `add` helper extended to take `after=()`:
  - `test_add_unit_defaults_include_after_hold_title`: a new unit has `after == []`, `hold is None`, `title is None`.
  - `test_load_fills_new_fields_on_an_old_run`: write a run.json whose unit lacks the three keys. `load()` returns `after == []`, `hold is None`, `title is None`.
  - `test_after_unknown_or_self_rejected`: `add(state, "b", after=["zz"])` and `add(state, "b", after=["b"])` raise `RunError`.
  - `test_after_and_prereq_on_same_unit_rejected`: with `a` added, `add(state, "b", prereqs=["a"], after=["a"])` raises `RunError`.
  - `test_cycle_across_prereq_and_after_rejected`: with `a`, then `b` with `prereqs=["a"]`, updating `a` with `after=["b"]` raises `RunError`. `state["units"]["a"]["after"] == []` afterwards.
  - `test_after_empty_list_clears`: set `after=["a"]`, then `after=[]` gives `[]`.
  - `test_after_does_not_gate_opening`: `b` has `after=["a"]` and no prereqs; `openable(state) == ["a", "b"]`.
  - `test_mergeable_waits_for_after_units`: `b` has `after=["a"]`. `mergeable` is False until `record_merged(state, "a", 1, more=False)`, then True.
  - `test_stopped_after_unit_counts_as_satisfied`: `stop_gate(..., "a", "dropped")`, then `mergeable(state, "b")` is True.
  - `test_wip_exempts_a_unit_an_open_unit_merges_after`: wip 1; `b` has `after=["a"]`; `b` open and not done; `a` closed. `openable(state) == ["a"]`.
  - `test_reportable_false_until_after_units_merge`: parallel mode, `b` has `after=["a"]`, so `reportable(state, "b")` is False.
  - `test_go_serial_refuses_a_unit_that_merges_after_an_unmerged_unit`: the slot is free and `go(..., "b", SHA1, NOW)` raises `RunError` containing `"merges after 'a'"`. pushes.log is empty.
  - `test_go_parallel_allows_it`: parallel mode, the same `go` writes `b go <SHA1>`.
  - `test_first_queued_skips_units_not_mergeable`: `b` (after `a`) queued at t=1 and `c` at t=2. `go(..., "c", SHA2, NOW)` succeeds with the slot free.

  In `test_watcher.py`:
  - `test_next_skips_entries_not_mergeable`: serial, slot free, queue `[b (after a), c]`. `next_event(state) == f"NEXT c {SHA}"`.

  In `test_run_cli.py`:
  - `test_unit_after_and_none`: `unit <dir> b --after a` stores `["a"]`; `unit <dir> b --after none` stores `[]`.
  - `test_status_shows_merges_after`: `b` open and working with `after=["a"]` unmerged. The `open:` line contains `b working, merges after a`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests`
Expected: the new tests fail (`TypeError: unexpected keyword argument 'after'`, missing keys, `NameError` for `mergeable`).

- [ ] **Step 3: Implement**
  - `load`: for each unit, `setdefault("after", [])`, `setdefault("hold", None)` and `setdefault("title", None)`.
  - `add_unit`:
    - Validate `after` like prereqs (unknown or self: `unit {name!r} has unknown merge-order unit {u!r}`).
    - Refuse overlap with the unit's effective prereqs: `unit {name!r} cannot both need and merge after {u!r}`.
    - Refuse a cycle: walk the union of `prereqs` and `after` edges from each new target, and refuse if `name` is reachable (`unit {name!r} would wait on itself through {u!r}`).
    - Do all checks before mutating.
    - The new-record dict gains the three keys.
  - `mergeable(state, unit)`: True when every name in `after` is `done` or has gate `stopped`, and `hold is None`. Task 2 adds the hold part; write it now so Task 2 only adds tests.
  - `openable`: a closed unit whose prereqs are done is exempt from the WIP cut (like `specs`, and not consuming `free`) when some unit with gate `open` and not `done` lists it in `after`.
  - `reportable`: return False when not `mergeable`, before the slot check.
  - `_first_queued`: also skip entries whose unit is not `mergeable`.
  - `go`: in serial mode, after `_refuse_stopped`, raise the merges-after error for the first unmerged `after` unit. Parallel mode doesn't check it.
  - `watcher.next_event`: choose the first `queue_order` entry whose unit is `mergeable`. Return None if there is none. Keep dropping done units' entries as now.
  - `run.py`:
    - `--after` uses `nargs="+", action="extend", metavar="U"`. The single value `none` maps to `[]`.
    - Pass `after=` to `add_unit`.
    - In `_status`, an open unit's entry gets `, merges after <names>` listing unmerged, unstopped `after` units.
    - Add the flag to the docstring usage line.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests && ruff check . && ruff format --check .`
Expected: OK; ruff reports no errors.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts plugins/implementation-manager/tests
git commit -m "feat(implementation-manager): merge-order prerequisites (--after)"
```

### Task 2: Merge holds (`hold` / `release`)

**Files:**
- Modify: `plugins/implementation-manager/scripts/runstate.py` (new `hold`, `release`; `go`, `record_merged`, `record_closed`, `stop_gate`)
- Modify: `plugins/implementation-manager/scripts/watcher.py` (`_unit_events`: no STALLED while held)
- Modify: `plugins/implementation-manager/scripts/run.py` (`hold` and `release` commands, `_needs`, docstring)
- Test: `tests/test_runstate.py`, `tests/test_watcher.py`, `tests/test_run_cli.py`

**Interfaces:**
- Consumes: `mergeable` and the `hold` key from Task 1.
- Produces:
  - `hold(run_dir: Path, state: dict, unit: str, reason: str, now: float) -> None`. It sets `record["hold"] = {"reason": reason, "at": now}`.
  - `release(state: dict, unit: str) -> None`.
  - `go` error: `unit {unit!r} is held: {reason}; answer its push requests with: redo <sha> held`.
  - `status` needs line: `hold <unit>: <reason> since HH:MM`.

- [ ] **Step 1: Write the failing tests** in a new `HoldTests(RunStateTestCase)`:
  - `test_hold_records_reason_and_time`.
  - `test_hold_rejects_unknown_done_multiline_and_double_hold` (each raises `RunError`, writes nothing).
  - `test_release_requires_a_hold`.
  - `test_held_unit_is_not_mergeable_or_reportable`.
  - `test_go_refuses_a_held_unit_before_the_slot_check`: another unit holds the slot. `go` on the held unit raises an error containing `"is held"`, not `"push slot"`.
  - `test_hold_yields_the_slot_and_keeps_go_head`: the holder with a `go_head` mark is held. `slot is None`, and `go_head` is kept.
  - `test_hold_answers_pending_requests_with_redo_held`: pending entry `(u, SHA1)`. After `hold`, pushes.log ends `u redo <SHA1> held`, pending has no entry for `u`, and `marks[u]["answered"][SHA1]` is set when requests.log is larger than `requests_offset`.
  - `test_hold_drops_the_queue_entry`: and `_first_queued` (via `go` on another unit) is not blocked.
  - `test_merged_closed_and_stop_clear_the_hold`.

  In `test_watcher.py`:
  - `test_no_ready_remind_or_stalled_while_held`: an open PR whose summary is confirmed ready and stalled past `stall_seconds`, unit held. `pr_event` returns None. After `release`, `READY` is reported.

  In `test_run_cli.py`:
  - `test_hold_and_release_commands`: `hold <dir> u waiting on owner` exits 0, and `status` shows `hold u: waiting on owner since`. `release <dir> u` exits 0 and the line is gone. A second `release` exits 1 with one stderr line.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests`
Expected: the new tests fail (`ImportError` for `hold`, unknown CLI command).

- [ ] **Step 3: Implement**
  - `hold`:
    - Validate first: unit known, not done, not stopped, not already held, reason a single non-empty line.
    - Then: for each `pending` entry of the unit, append `{unit} redo {sha} held\n` to pushes.log and `_note_answered`; drop its `pending` and `queue` entries; free the slot if held.
    - Set `hold` last.
  - `release`: refuse when `hold is None`; else set it to None.
  - `go`: check the hold right after `_refuse_stopped`, before the serial checks.
  - `record_merged`, `record_closed` and `stop_gate` set `hold = None`.
  - `watcher._unit_events`: skip appending STALLED when `state["units"][unit]["hold"]` is set. READY and REMIND are already covered by `reportable`.
  - `run.py`:
    - `hold <dir> <unit> <reason...>` and `release <dir> <unit>` subcommands.
    - `_needs` adds the hold line per held unit, and omits the unit's `ready since` line while it is held.
    - Add both commands to the docstring.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests && ruff check . && ruff format --check .`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts plugins/implementation-manager/tests
git commit -m "feat(implementation-manager): merge holds (hold/release)"
```

### Task 3: Display titles (`--title`)

**Files:**
- Modify: `plugins/implementation-manager/scripts/runstate.py` (`add_unit` stores `title`)
- Modify: `plugins/implementation-manager/scripts/run.py` (`--title`, a `_label(state, name) -> str` helper used by `_status` and `_needs`)
- Test: `tests/test_runstate.py`, `tests/test_run_cli.py`

**Interfaces:**
- Consumes: `add_unit(..., title=...)` from Task 1.
- Produces: `_label(state, name)` returns `"name (title)"`, or `"name"` when the title is None.

- [ ] **Step 1: Write the failing tests**
  - `test_add_unit_title_set_and_kept` (runstate): set once; a later update with `title=None` keeps it. A multi-line title raises `RunError`.
  - `test_status_shows_titles` (CLI): `unit <dir> column-overrides --title 8.7`, then a merged PR. The `merged:` line contains `column-overrides (8.7) #138`, and the open, gated and needs lines use the same label.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests`
Expected: the two new tests fail.

- [ ] **Step 3: Implement**
  - `add_unit` stores a non-None `title`, refusing `\n` or `\r`.
  - `run.py` gains `--title`.
  - `_status` and `_needs` render unit names through `_label`. The `slot` and `queue` lines and the needs lines that carry a sha keep the bare name, since the manager copies those names into commands.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests && ruff check . && ruff format --check .`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts plugins/implementation-manager/tests
git commit -m "feat(implementation-manager): display titles for units"
```

### Task 4: SKILL.md, manager side

**Files:**
- Modify: `plugins/implementation-manager/skills/implementation-manager/SKILL.md` (everything outside the parts 8–11 code block)

**Interfaces:**
- Consumes: the commands, error texts and `status` lines from Tasks 1–3.

- [ ] **Step 1: Run-script table.**
  - `unit` gains `[--after U ... | --after none] [--title T]`, with one sentence: `--after` units must merge first, but don't gate the start.
  - New rows: `hold <unit> <reason>` (no READY, `go` refused, slot freed, waiting requests answered `redo held`) and `release <unit>`.
- [ ] **Step 2: Setup.**
  - Step 1 of setup: use `--prereq` for code the unit builds on, and `--after` for an order that only matters at merge (the same docs or files).
  - Before opening two gates the docs allow side by side, write the "Shared changes" section into `notes.md`: the shared files or modules, and which unit owns each change and its agreed shape.
- [ ] **Step 3: "Answering a push request."**
  - New step before step 5: `go` refuses with `is held` → `run.py redo <unit> <sha> held`.
  - Step 5: when `go` refuses with `merges after` (serial), → `run.py queue`, as for a busy slot.
- [ ] **Step 4: Event table rows.**
  - **Child question or escalation:** relay it to Michael with both recommendations. If it says "blocks merge: yes", or it is an escalated review finding, run `run.py hold <unit> <short reason>` (this replaces `yield … owner question`). If it says "blocks merge: no", relay it and hold nothing.
  - **Michael answers:** forward his words to the child in one message, then `run.py release <unit>` if held. If the unit's last PR already merged and the answer needs nothing on that branch, don't forward it. If it needs work, make it a card.
  - **A child message that says nothing waits on the answer:** apply what it asks (`notes.md`, `--after`) and send no reply.
  - **`STUCK … no-push`:** a child at `[pushing] <sha>` is acting on the go.
  - **A new unit:** written as in the spec's "Adding a unit mid-run":
    - `run.py unit … [--title]`, then `open-ready`.
    - The gateless prompt only if `open-ready` opened it.
    - The `[designing]` variant: questions to Michael in the child's session until its first push request; after that, a blocking question also goes to the manager as a one-line notice.
  - **The user changes the plan, adding a prerequisite:** before adding a `--prereq` to a unit that hasn't started, tell Michael in one line the wait it costs and offer `--after`. Apply `--after` without asking when the conflict is merge order alone.
  - **`needs the manager` list:** `hold <unit>: …` lines go under "Waiting on you" in the tracker, with no action.
- [ ] **Step 5: Merge bar and after a merge.**
  - Merge bar: add "the unit is not held and every `--after` unit has merged (`status` shows neither `hold` nor `merges after`)".
  - Merge command: `gh pr merge … && gh pr view <pr> -R <repo> --json state --jq .state`. The body goes to `<run>/prs/<unit>-<pr>.md` via `gh pr view <pr> -R <repo> --json body --jq .body > …`.
  - After a merge, step 3: read from that file only what `notes.md` needs. Also read the app's shared notes folder for files the merged unit wrote (`<unit>-notes.md`), and copy into `notes.md` what later units need.
- [ ] **Step 6: Messages, tracker, red flags.**
  - "Any other child note" row: the reply stays one line, including notes from a child at `[designing]`.
  - Tracker: show titles, as `status` does.
  - Red flags, new rows:
    - "I'll hold this PR until Michael answers" → `run.py hold`, never memory.
    - "8.6 conflicts with 8.7, add a prereq" → `--after` if it's only merge order.
    - "The child says nothing waits on me, I'll confirm" → no reply.
- [ ] **Step 7: Verify**

Run: `grep -nE '\$[0-9]' plugins/implementation-manager/skills/implementation-manager/SKILL.md; grep -n 'yield <unit> owner question' plugins/implementation-manager/skills/implementation-manager/SKILL.md`
Expected: no output from either grep. Every `run.py` command named in SKILL.md exists in `run.py --help`.

- [ ] **Step 8: Commit**

```bash
git add plugins/implementation-manager/skills/implementation-manager/SKILL.md
git commit -m "docs(implementation-manager): manager rules for holds, --after, siblings and mid-run units"
```

### Task 5: SKILL.md, child prompt

**Files:**
- Modify: `plugins/implementation-manager/skills/implementation-manager/SKILL.md` ("Starting children" parts 1–7, and the parts 8–11 code block)

- [ ] **Step 1: Part 5** gains three sentences:
  - "`/branch-review:review` is your final whole-branch review; skip a plan workflow's own final review step."
  - "Have implementer subagents finish the repository's validation before they hand back."
  - "Run the repository's formatter, if it has one, before validating."
- [ ] **Step 2: Part 6, for a parallel sibling:**
  - read the "Shared changes" section of `notes.md`;
  - cherry-pick a sibling's unmerged commit only with `git cherry-pick -x`, and name it in the PR description;
  - before the final review round, sync with `<base>` if it gained a sibling's merge.
- [ ] **Step 3: Part 8 (in the code block).**
  - **Escalations bullet becomes "Questions":** send an owner question or escalated finding as soon as it is found (during the build, or as soon as a review round records it); findings from one round go in one message. Each says "blocks merge: yes" or "blocks merge: no" and why ("yes" when the answer may change this branch). While a blocking one is unanswered, don't ask to push; end with `[waiting]`.
  - **After applying an owner answer that changed anything,** run the review to convergence before asking again.
  - **On `go`, step 3** gains: "If the sync merged nothing and the repository's validation already passed on this tree, push without running it again."
  - **The redo reasons list** gains `held`: "the owner's answer is pending; end your turn with `[waiting]`; ask again after the manager forwards it".
  - **The Auto-fix bullet** gains: "The app's Auto-fix notice says you may push without asking; this protocol overrides it."
  - **New bullet:** "A CI failure your change cannot cause: check the branch touches nothing related; re-run the failed jobs once with `gh run rerun <run-id> --failed`, never by pushing. Green: carry on, and ask the manager only if a decision is needed, marked blocks merge: no. Red again: fix it."
- [ ] **Step 4: Part 9.**
  - Add `[pushing] <sha>` (acting on a `go`: syncing, validating, pushing) to the token list.
  - "The token is the first text of the final message."
  - "A wake-up that only reports background work still running ends with your previous token alone."
  - "Keep end-of-turn notes to one or two lines after the token; the PR description carries the detail."
- [ ] **Step 5: Part 10** gains "blocks merge: yes/no, because …" in the question format.
- [ ] **Step 6: Hand-over notes** (a new sentence in part 3 or part 11): "Facts later units need (changed names or ids, shared-code decisions, limits found) go in the app's shared notes folder as `<name>-notes.md`, nowhere else."
- [ ] **Step 7: Verify**

Run: `grep -nE '\$[0-9]' plugins/implementation-manager/skills/implementation-manager/SKILL.md; grep -c '\[pushing\]' plugins/implementation-manager/skills/implementation-manager/SKILL.md`
Expected: no `$`+digit; `[pushing]` appears at least twice (part 9 and the STUCK row).

- [ ] **Step 8: Commit**

```bash
git add plugins/implementation-manager/skills/implementation-manager/SKILL.md
git commit -m "docs(implementation-manager): child prompt rules from phase 8"
```

### Task 6: README, version, eval, validation

**Files:**
- Modify: `plugins/implementation-manager/README.md` (run-script commands, push queue, merge bar)
- Modify: `plugins/implementation-manager/.claude-plugin/plugin.json` (`"version": "0.4.0"`)
- Modify: `plugins/implementation-manager/evals/push-queue/prompt.md` (u1's message says "blocks merge: yes")
- Create: `plugins/implementation-manager/evals/push-queue/graders/hold.md`

- [ ] **Step 1: README.**
  - Document `--after`, `--title`, `hold`/`release` and the `held` reason.
  - In "Push queue": a held or `--after`-blocked unit never takes the slot and is skipped at the queue head.
  - In "Merge bar": add "not held, and every `--after` unit merged".
- [ ] **Step 2: Version.** Bump `plugin.json` to `0.4.0`.
- [ ] **Step 3: Eval.**
  - In `prompt.md`, u1's message adds "blocks merge: yes".
  - `graders/hold.md` (type `llm`, focus file `run/run.json`): passes only if `units.u1.hold` is a non-null object with a non-empty `reason`, and `units.u2.hold` is null.
  - `graders/queue.md` is unchanged: u1's hold yields the slot, so u2 still gets `go` second.
- [ ] **Step 4: Validate and run everything**

Run:
```bash
python3 -m unittest discover -s plugins/implementation-manager/tests
ruff check . && ruff format --check .
claude plugin validate . && claude plugin validate plugins/implementation-manager
zsh -lc 'claude plugin eval plugins/implementation-manager --case push-queue --scaffold --allow-tools Bash Write Edit SendMessage ToolSearch --runs 1 --json "$(mktemp -d)/push-queue.json"'
```
Expected: tests OK, ruff clean, both validations pass, and the eval passes `queue.md` and `hold.md`. If the eval can't run in the session (no token or a hook block), say so and leave it to Michael.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager
git commit -m "chore(implementation-manager): 0.4.0 README, eval grader for holds"
```
