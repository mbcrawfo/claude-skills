# Implementation Manager Run Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the implementation manager's note-driven push gate and per-PR watches with one run watcher script, file-based push requests, and a serial push queue in which a stuck PR gives up its slot.

**Architecture:**
- Children append push requests to `requests.log`.
- One stdlib CLI, `scripts/run.py`, owns the run's state in `run.json` (units, gates, WIP, slot, queue, watcher marks) and is the only writer of `run.json`, `gates.log` and `pushes.log`.
- Its `wait` subcommand blocks until one event, prints one line and exits. The manager runs it in the background and restarts it after every event.
- `SKILL.md` is rewritten around those events.

**Tech Stack:** Python 3.9+ standard library (`argparse`, `json`, `fcntl`, `subprocess`), `unittest`, `gh` CLI (GraphQL), ruff 0.17.2, Claude Code plugin eval.

**Spec:** `docs/superpowers/specs/2026-10-23-implementation-manager-run-watcher-design.md`

## Global Constraints

- Python 3.9+, standard library only, invoked through `python3`. Every module starts with `from __future__ import annotations`.
- Tests live in `plugins/implementation-manager/tests/` and run with `python3 -m unittest discover -s plugins/implementation-manager/tests`.
- `ruff check .` and `ruff format .` are clean. New first-party module names (`prstate`, `runstate`, `watcher`, `run`) are added to `known-first-party` in `pyproject.toml`.
- Never write a dollar sign followed by a digit in `SKILL.md`.
- No SqlSource names or details in the skill, README, examples or fixtures (public repo).
- Plugin version `0.1.0` → `0.2.0` in `plugins/implementation-manager/.claude-plugin/plugin.json`.
- `claude plugin validate .` and `claude plugin validate plugins/implementation-manager` pass.
- Line formats children already parse stay byte-identical: `gates.log` is `<unit> open` or `<unit> stop <reason>`; `pushes.log` is `<unit> go <sha>` or `<unit> redo <sha> <reasons>`.
- New child line format: `<unit> push <sha> <branch>` in `requests.log`, where the sha is a full 40-hex sha.
- Timings from the spec:
  - `requests.log` checked every 5 s; PRs polled every 60 s.
  - A READY candidate is confirmed by a second poll 30 s later.
  - `REMIND` 30 min after `READY`.
  - `STUCK` after 20 min of failure, or 30 min with no push after `go`.
  - `STALLED` after 3 h (default).
  - `ERROR` after 10 consecutive `gh` failures.
  - A gated child gets a message only after 5 min.

## Review Focus

1. **Two `wait` processes at once** (the manager starts a new one before the old one exited). Each event must still be reported exactly once: marks are saved under the lock before printing, and a waiter re-reads state every iteration. Task 4 pins it with `test_two_waiters_report_a_request_once`.
2. **A command runs while `wait` is between load and save.** For example, `go` lands while `wait` is polling `gh`. The waiter must not overwrite it: `wait` never holds state across a `gh` call; it re-loads under the lock and applies only its marks. Task 4 pins it with `test_go_during_poll_is_not_lost`.
3. **A request line caught half written** (no trailing newline yet). It is not consumed until the newline arrives. Task 4 pins it with `test_partial_request_line_waits_for_newline`.
4. **`gh` failing** (logged out, offline). New requests are still reported, and `ERROR` comes only after 10 consecutive failed polls. Task 5 pins it with `test_requests_reported_while_gh_fails`.
5. **A malformed or foreign request line** (short sha, unknown unit, extra words). It is skipped, kept for `status`, and never crashes the watcher. Task 4 pins it with `test_bad_request_lines_are_skipped_and_listed`.

---

## File Structure

```
plugins/implementation-manager/
  scripts/
    prstate.py        # NEW: PR reading via gh (moved from wait_pr_ready.py): GhError, run_gh, Summary, summarize, find_open_pr, poll_pr, check_once
    runstate.py       # NEW: run.json model, lock, units, gates/WIP, slot and queue operations
    watcher.py        # NEW: wait(): requests.log reader, queue events, PR events, marks
    run.py            # NEW: argparse CLI over runstate/watcher/prstate; status text
    wait_pr_ready.py  # DELETED in Task 6
  tests/
    helpers.py        # unchanged
    fakes.py          # NEW: FakeGh, FakeClock, make_pr, graphql (moved from test_wait_pr_ready.py)
    test_prstate.py   # NEW: summarize/reasons/poll tests moved from test_wait_pr_ready.py
    test_runstate.py  # NEW
    test_watcher.py   # NEW
    test_run_cli.py   # NEW
    test_wait_pr_ready.py  # DELETED in Task 6
  skills/implementation-manager/SKILL.md   # rewritten in Task 6
  README.md                                # rewritten sections in Task 6
  evals/push-gate/...                      # moved to new protocol in Task 7
  evals/push-queue/...                     # NEW in Task 7
pyproject.toml                             # known-first-party
```

---

### Task 1: Move PR reading into `prstate.py`

**Files:**
- Create: `plugins/implementation-manager/scripts/prstate.py`
- Create: `plugins/implementation-manager/tests/fakes.py`
- Create: `plugins/implementation-manager/tests/test_prstate.py`
- Modify: `plugins/implementation-manager/scripts/wait_pr_ready.py` (import from `prstate` instead of defining; deleted in Task 6)
- Modify: `plugins/implementation-manager/tests/test_wait_pr_ready.py` (import fakes from `fakes`)
- Modify: `pyproject.toml` (`known-first-party` adds `"prstate", "runstate", "watcher", "run", "fakes"`)

**Interfaces:**
- Produces, in `prstate.py`:
  - `GhError(Exception)`.
  - `run_gh(args: list[str]) -> str`.
  - `QUERY: str`.
  - `Summary`: frozen dataclass, same fields as today, plus a `conflict` property, true when `merge in ("DIRTY", "CONFLICTING")`.
  - `Summary.reasons() -> str`.
  - `summarize(pr: dict) -> Summary`.
  - `find_open_pr(repo: str, branch: str, gh) -> int | None`.
  - `poll_pr(repo: str, pr: int, gh) -> Summary`.
  - `check_once(repo: str, branch: str, *, gh, sleep, confirm_seconds: float = 30) -> str`. It returns `READY <pr> <sha>`, `NOT-READY <pr> head=<sha7><reasons>`, `NOT-READY - no open PR from <branch>`, or `ERROR <message>`; it is the logic `ONCE=1` has today.
- Produces, in `fakes.py`: `HEAD_A`, `HEAD_B`, `check_run`, `status_context`, `make_pr`, `graphql`, `FakeGh`, `FakeClock`, `PR7`, `NO_PR`. These are moved verbatim from `test_wait_pr_ready.py`.

- [ ] **Step 1: Move the fakes.** Move the helper functions and classes listed above from `test_wait_pr_ready.py` into `tests/fakes.py`, and import them back in `test_wait_pr_ready.py`.
- [ ] **Step 2: Write `test_prstate.py`.** Move the classes holding `test_reasons`, `test_missing_rollup_means_no_checks`, `test_summary_carries_head_and_state`, `test_equal_summaries_compare_equal` and the malformed-output failure tests (`test_empty_output_is_a_failure` through `test_null_head_ref_oid_is_a_failure`, calling `poll_pr` directly). Retarget them to `prstate`. Add:

```python
def test_conflict_property(self):
    self.assertTrue(summarize(make_pr(merge="DIRTY")).conflict)
    self.assertFalse(summarize(make_pr(merge="BEHIND")).conflict)


def test_check_once_ready_after_two_clean_polls(self):
    gh = FakeGh([PR7], [make_pr(), make_pr()])
    clock = FakeClock()
    self.assertEqual(check_once("owner/repo", "feat/x", gh=gh, sleep=clock.sleep), f"READY 7 {HEAD_A}")
    self.assertEqual(clock.sleeps, [30])


def test_check_once_no_pr(self):
    gh = FakeGh([NO_PR])
    self.assertEqual(
        check_once("owner/repo", "feat/x", gh=gh, sleep=FakeClock().sleep), "NOT-READY - no open PR from feat/x"
    )
```

- [ ] **Step 3: Run the new tests and confirm they fail.** Run `python3 -m unittest discover -s plugins/implementation-manager/tests -p 'test_prstate.py' -v`. Expected: ImportError for `prstate`.
- [ ] **Step 4: Create `prstate.py`.** Move `GhError`, `run_gh`, `QUERY`, `Summary`, `summarize`, `find_open_pr` and `poll_pr` out of `wait_pr_ready.py`. Add `conflict` and `check_once`; its two polls 30 s apart share code with `watch(once=True)`. `wait_pr_ready.py` imports these names from `prstate`.
- [ ] **Step 5: Run the whole suite and lint.** Run `python3 -m unittest discover -s plugins/implementation-manager/tests -v && ruff check . && ruff format --check .`. Expected: all pass, including the remaining `test_wait_pr_ready.py` tests.
- [ ] **Step 6: Commit.** Run `git add -A plugins/implementation-manager pyproject.toml && git commit -m "refactor(implementation-manager): PR reading in prstate.py"`, with the attribution trailer.

### Task 2: Run state, units, gates and WIP (`runstate.py`)

**Files:**
- Create: `plugins/implementation-manager/scripts/runstate.py`
- Create: `plugins/implementation-manager/tests/test_runstate.py`

**Interfaces:**
- Produces:
  - `RunError(Exception)`. Its message is the CLI's one stderr line, exit 1.
  - `STATE_FILE = "run.json"`, `LOCK_FILE = "run.lock"`, `REQUESTS = "requests.log"`, `PUSHES = "pushes.log"`, `GATES = "gates.log"`.
  - `UNIT_RE = r"[A-Za-z0-9][A-Za-z0-9._-]*"`, `SHA_RE = r"[0-9a-f]{40}"`.
  - `init_run(run_dir: Path, *, repo: str, base: str, method: str, wip: int | None, queue_mode: str, now: float) -> dict` creates the dir, `run.json`, and empty `requests.log`, `pushes.log` and `gates.log`. It refuses (`RunError`) if `run.json` exists. `method` is one of `merge`, `squash`, `rebase`; `queue_mode` is `serial` or `parallel`.
  - `locked(run_dir: Path) -> ContextManager[dict]`, a context manager. It takes `fcntl.flock` on `run.lock`, loads `run.json`, yields the dict, and on normal exit writes it atomically (temp file plus `os.replace`). Nothing is written on an exception.
  - `load(run_dir: Path) -> dict` reads without the lock.
  - `add_unit(state, name, *, prereqs: list[str], branch: str | None, worktree: str | None, specs: bool) -> None`. It adds a unit or updates the fields given; `None` keeps the old value. An unknown prerequisite is a `RunError`. A unit's `order` is its insertion index.
  - `set_option(state, key: str, value: str) -> None`, where `key` is `wip` (an int ≥ 1 or `none`) or `queue` (`serial` or `parallel`).
  - `openable(state) -> list[str]`: units with a closed gate whose prerequisites are all `done`, in `order`, cut to the free WIP. Free WIP is `wip` minus the units with an open gate, not done and not specs; with no limit, it is unbounded. Specs units are never cut.
  - `open_gate(run_dir, state, unit, now) -> None` appends `<unit> open\n` to `gates.log` and sets `gate="open"`, `gate_at=now`. Opening twice is a `RunError`.
  - `stop_gate(run_dir, state, unit, reason) -> None` appends `<unit> stop <reason>\n`.
- `run.json` shape (version 1):

```json
{"version": 1, "repo": "o/r", "base": "main", "method": "merge", "queue_mode": "serial", "wip": null,
 "units": {"u1": {"order": 0, "prereqs": [], "branch": null, "worktree": null, "specs": false,
                  "gate": "closed", "gate_at": null, "done": false, "prs": {}, "yielded": null}},
 "slot": null, "queue": [], "pending": [], "bad_requests": [], "requests_offset": 0, "marks": {}}
```

`prs` maps a PR number (string) to `"OPEN"`, `"MERGED"` or `"CLOSED"`.

- [ ] **Step 1: Write the failing tests.** Use temp dirs and `now=1000.0`:

```python
def test_init_creates_files(self):
    state = init_run(d, repo="o/r", base="main", method="merge", wip=None, queue_mode="serial", now=1000.0)
    for name in ("run.json", "requests.log", "pushes.log", "gates.log"):
        self.assertTrue((d / name).exists())
    self.assertEqual(state["slot"], None)

def test_init_refuses_existing_run(self): ...  # second init raises RunError

def test_openable_respects_prereqs_and_order(self):
    # units a (none), b (a), c (none): openable == ["a", "c"]

def test_openable_respects_wip(self):
    # wip=2, a open and not done, b and c closed, no prereqs: openable == ["b"]

def test_specs_unit_not_counted_toward_wip(self):
    # wip=1, specs unit open; a closed with no prereqs: openable == ["a"]

def test_lowering_wip_closes_nothing(self):
    # wip 3 with a, b, c open; set wip 1: gates stay open, openable == []

def test_open_gate_appends_line(self):
    # gates.log == "a open\n"; gate_at == now; opening again raises RunError

def test_stop_gate_appends_line(self):
    # "b stop dropped by owner\n"

def test_locked_writes_atomically_and_not_on_error(self):
    # mutate inside `with locked(d)`, then raise; run.json unchanged

def test_unknown_prereq_rejected(self): ...
def test_bad_unit_name_rejected(self): ...  # "a b" and "-x" raise RunError
```

- [ ] **Step 2: Run the tests and confirm they fail.** Run `python3 -m unittest discover -s plugins/implementation-manager/tests -p 'test_runstate.py' -v`. Expected: ImportError.
- [ ] **Step 3: Implement the Produces list in `runstate.py`.**
- [ ] **Step 4: Run the tests and confirm they pass.** Run the same command. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(implementation-manager): run state with units, gates and WIP`.

### Task 3: Slot and queue operations (`runstate.py`)

**Files:**
- Modify: `plugins/implementation-manager/scripts/runstate.py`
- Modify: `plugins/implementation-manager/tests/test_runstate.py`

**Interfaces:**
- Consumes: Task 2's state, `locked` and `RunError`.
- Produces:
  - `has_open_pr(state, unit) -> bool`.
  - `go(run_dir, state, unit, sha, now) -> None` appends `<unit> go <sha>\n`. It removes the unit's `pending` and `queue` entries and clears `yielded`. In serial mode it sets `slot = {"unit", "sha", "at": now, "head": marks[unit].get("head")}`; it is a `RunError` if another unit holds the slot (message names the holder and says to use `queue`). Always a `RunError` if the unit is `done` (message says to answer `redo <sha> pr-merged`).
  - `redo(run_dir, state, unit, sha, reasons: str) -> None` appends `<unit> redo <sha> <reasons>\n` and removes the unit's `pending` and `queue` entries. If the unit holds the slot, it frees the slot and sets `yielded = "redo"`.
  - `enqueue(state, unit, sha, now) -> None` removes the unit's `pending` entry and replaces any queue entry for the unit with `{"unit", "sha", "at": now, "front": has_open_pr(state, unit)}`. It is a `RunError` in parallel mode, or if the unit holds the slot.
  - `queue_order(state) -> list[dict]`: `front` entries by `at`, then the rest by `at`.
  - `yield_slot(state, unit, reason, now) -> None` frees the slot and sets `yielded = reason`. It is a `RunError` if the unit does not hold the slot.
  - `record_merged(state, unit, pr: int, *, more: bool) -> None` sets `prs[str(pr)] = "MERGED"`, frees the slot if the unit holds it, and sets `done = not more`.
  - `record_closed(state, unit, pr: int) -> None` sets `prs[str(pr)] = "CLOSED"` and frees the slot if the unit holds it.
  - `reportable(state, unit) -> bool`: true in parallel mode, or when the slot is free or held by `unit`. The watcher uses it for `READY`/`REMIND`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_go_takes_free_slot(self):       # slot["unit"] == "a"; pushes.log == f"a go {SHA1}\n"
def test_go_refused_while_other_holds(self):   # RunError mentioning "a" and "queue"
def test_holder_go_again_keeps_slot(self):     # a holds; go a SHA2 -> slot sha SHA2, no error
def test_parallel_go_never_takes_slot(self):   # queue_mode parallel: slot stays None
def test_go_refused_for_done_unit(self):       # message contains "pr-merged"
def test_redo_frees_holders_slot(self):        # slot None, units["a"]["yielded"] == "redo"
def test_redo_line_format(self):               # f"b redo {SHA1} changes-since-review, escalated\n"
def test_queue_order_open_pr_first(self):
    # c queued at t=1 (no PR), b queued at t=2 with prs {"5": "OPEN"}: order == ["b", "c"]
def test_enqueue_replaces_units_entry(self):   # one entry per unit, newest sha
def test_yield_requires_holder(self): ...
def test_merged_frees_slot_and_marks_done(self):    # more=False -> done True
def test_merged_part_keeps_unit_open(self):         # more=True -> done False
def test_reportable(self):
    # serial, slot held by a: reportable(a) True, reportable(b) False; slot None: both True
```

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: AttributeError/ImportError for the new names.
- [ ] **Step 3: Implement the Produces list.**
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** as `feat(implementation-manager): push slot and queue`.

### Task 4: Watcher — requests and queue events (`watcher.py`)

**Files:**
- Create: `plugins/implementation-manager/scripts/watcher.py`
- Create: `plugins/implementation-manager/tests/test_watcher.py`

**Interfaces:**
- Consumes: `runstate.locked`, `load`, `queue_order`, `REQUESTS`, `UNIT_RE`, `SHA_RE`.
- Produces:
  - `REQUEST_RE = rf"^({UNIT_RE}) push ({SHA_RE}) (\S+)$"`.
  - `take_request(run_dir: Path, state: dict, now: float) -> str | None`. It reads `requests.log` from `requests_offset` and consumes whole lines (ending in `\n`) up to and including the first valid line for a known, not-done unit. Skipped lines are appended to `bad_requests`, keeping the last 20. The valid line is added to `pending` as `{"unit", "sha", "branch", "at"}`, replacing that unit's older pending entry. It returns `REQUEST <unit> <sha> <branch>`, or `None`. A request from a done unit is still reported (the manager answers `pr-merged`).
  - `next_event(state) -> str | None`. In serial mode, when the slot is free and the queue is non-empty, it returns `NEXT <unit> <sha>` for `queue_order(state)[0]`, at most once per `(unit, sha)`. The mark is stored in `state["marks"]["_next"]`.
  - `wait(run_dir: Path, *, gh, sleep, now, request_seconds=5.0, poll_seconds=60.0, confirm_seconds=30.0, stall_seconds=10800.0, stuck_seconds=1200.0, no_push_seconds=1800.0, remind_seconds=1800.0) -> str`. It loops; each iteration it:
    1. Takes the lock and runs `take_request`. On a hit it saves and returns the line.
    2. PR polling (Task 5) when due.
    3. Takes the lock and runs `next_event`; on a hit it saves and returns.
    4. Sleeps `request_seconds`.

    It never holds the lock across a `gh` call or a sleep.

- [ ] **Step 1: Write the failing tests.** No units have branches, so `gh` must never be called (`FakeGh([], [])` raises on any call):

```python
def test_request_reported_and_pending(self):
    append(d, f"a push {SHA1} feat/a\n")
    self.assertEqual(run_wait(d), f"REQUEST a {SHA1} feat/a")
    self.assertEqual(load(d)["pending"][0]["sha"], SHA1)

def test_two_waiters_report_a_request_once(self):
    # one line; first wait -> REQUEST; second wait (FakeClock that raises after 3 sleeps) -> no REQUEST

def test_partial_request_line_waits_for_newline(self):
    # write without "\n": take_request returns None and offset is unchanged; append "\n": REQUEST

def test_bad_request_lines_are_skipped_and_listed(self):
    # lines "a push abc feat/a", "zz push <SHA1> feat/z" (unknown unit), "a push <SHA1> b extra",
    # then a valid one: REQUEST for the valid one; bad_requests holds the three

def test_next_after_slot_frees(self):
    # a holds, b queued; yield a; wait -> f"NEXT b {SHA2}"; second wait does not repeat it

def test_go_during_poll_is_not_lost(self):
    # The FakeClock sleep hook runs runstate.go(...) inside locked(d) between iterations;
    # after wait returns its next event, the slot set by go is still there.
```

`run_wait(d)` calls `wait` with a `FakeClock` whose `sleep` raises `Timeout` after N calls, so a test that expects no event ends.

- [ ] **Step 2: Run the tests and confirm they fail.** Run `python3 -m unittest discover -s plugins/implementation-manager/tests -p 'test_watcher.py' -v`. Expected: ImportError.
- [ ] **Step 3: Implement `take_request`, `next_event` and the `wait` loop skeleton** (PR polling is a no-op until Task 5).
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** as `feat(implementation-manager): run watcher reports requests and queue turns`.

### Task 5: Watcher — PR events

**Files:**
- Modify: `plugins/implementation-manager/scripts/watcher.py`
- Modify: `plugins/implementation-manager/tests/test_watcher.py`

**Interfaces:**
- Consumes: `prstate.find_open_pr`, `poll_pr`, `GhError`, `Summary`; `runstate.reportable`.
- Produces:
  - `poll_units(state, *, gh) -> dict[str, Summary | None]`. For every unit with a branch that is not done, it finds the open PR when none is recorded as `OPEN`, records it in `prs` (`"OPEN"`) and `marks[unit]["pr"]`, and polls it. A PR that `poll_pr` returns as `MERGED`/`CLOSED` is reported once (below). It raises `GhError` on failure.
  - `pr_event(state, polled: dict, now, *, timings) -> str | None`. It applies marks and returns at most one line, in this priority order (first match wins, units in `order`):
    1. `READY <unit> <pr> <sha>`. `summary.reasons() == ""`, confirmed by an equal summary from a poll at least `confirm_seconds` later (the first clean poll stores `marks[unit]["candidate"]`; the watcher schedules its next PR poll at `now + confirm_seconds`). Also: no `pending` or `queue` entry for the unit, `reportable(state, unit)` true, and `marks[unit]["ready_head"] != head`. It sets `ready_head` and `ready_at`.
    2. `REMIND <unit> <pr> <sha>`. Same head still ready, `now - ready_at >= remind_seconds`, `remind_head != head`, and still reportable.
    3. `MERGED <unit> <pr>` / `CLOSED <unit> <pr>`. Only for a PR recorded `OPEN` by the watcher; skipped silently when `prs[pr]` is already `MERGED`/`CLOSED` (the manager recorded it first). Reported once (`marks[unit]["ended"]`).
    4. `BEHIND <unit> <pr> <sha>`. `merge == "BEHIND"`, and the unit holds the slot (or the queue mode is parallel). Once per head (`behind_head`).
    5. `STUCK <unit> <pr> <reasons>`, where the unit holds the slot (or the queue mode is parallel):
       - Failure: `failed > 0 or unresolved > 0 or conflict` has held for `stuck_seconds` on the same head with no `pending` request, keyed by `head + reasons`, once per key.
       - No push: in serial mode, the holder's head equals `slot["head"]` (or no PR exists) for `no_push_seconds` after `slot["at"]`, once per `slot["at"]`. It prints `STUCK <unit> - no-push` when there is no PR.
    6. `STALLED <unit> <pr> head=<sha7><reasons>`. Any open PR whose `head + reasons` signature has been unchanged for `stall_seconds`; once per signature.
  - In `wait`, a PR poll happens when due (every `poll_seconds`, or at the confirm time). It runs without the lock; then the watcher takes the lock, re-loads, and applies `pr_event` to the fresh state. Ten consecutive `GhError`s make `wait` return `ERROR <message>`; a successful poll resets the count. The `ERROR` line is checked after `take_request`, so requests still come through.

- [ ] **Step 1: Write the failing tests** with `FakeGh` and `FakeClock`. Unit `a` has branch `feat/a` and holds the slot unless a test says otherwise:

```python
def test_ready_after_confirm_poll(self):        # two clean polls 30 s apart -> f"READY a 7 {HEAD_A}"
def test_ready_not_repeated_for_same_head(self): # second wait: no READY for HEAD_A
def test_ready_suppressed_while_request_pending(self)
def test_ready_held_for_non_holder_until_slot_frees(self):
    # b holds the slot; a's PR clean -> no READY for a; yield b -> READY a
def test_remind_after_thirty_minutes(self)
def test_merged_reported_once_and_skipped_if_recorded(self)
def test_behind_only_for_holder(self):          # serial: b (non-holder) behind -> nothing; a -> BEHIND
def test_stuck_after_twenty_minutes_failing(self)
def test_stuck_no_push(self):                    # go at t; PR head unchanged 30 min -> "STUCK a 7 no-push"
def test_stalled_after_three_hours(self)
def test_requests_reported_while_gh_fails(self):  # gh raises GhError; a request line still -> REQUEST
def test_error_after_ten_failures(self):          # -> line starts "ERROR"
def test_parallel_mode_reports_behind_for_all(self)
```

- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement `poll_units`, `pr_event` and the polling in `wait`.**
- [ ] **Step 4: Run the whole suite and confirm it passes.** Run `python3 -m unittest discover -s plugins/implementation-manager/tests -v`. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(implementation-manager): run watcher reports PR readiness, merges and stuck holders`.

### Task 6: CLI, skill, README and retiring `wait_pr_ready.py`

**Files:**
- Create: `plugins/implementation-manager/scripts/run.py`
- Create: `plugins/implementation-manager/tests/test_run_cli.py`
- Rewrite: `plugins/implementation-manager/skills/implementation-manager/SKILL.md`
- Modify: `plugins/implementation-manager/README.md`, `README.md` (root plugin line: "watching each PR" becomes "with one run watcher and a push queue")
- Modify: `plugins/implementation-manager/.claude-plugin/plugin.json` (version `0.2.0`)
- Delete: `plugins/implementation-manager/scripts/wait_pr_ready.py`, `plugins/implementation-manager/tests/test_wait_pr_ready.py`; also remove `wait_pr_ready` from `known-first-party`.

**Interfaces:**
- Consumes: every function from Tasks 1–5.
- Produces the CLI. The run folder is always the first positional argument after the subcommand. Exit codes: 0 on success, 1 for `RunError`/`GhError` with one stderr line, 2 for usage. `wait` always exits 0.

```
run.py init <dir> --repo O/R --base B --method merge|squash|rebase [--wip N] [--queue serial|parallel]
run.py unit <dir> <name> [--prereq U ...] [--branch B] [--worktree P] [--specs]
run.py set <dir> wip <N|none> | queue <serial|parallel>
run.py open <dir> <unit> | open-ready <dir>          # open-ready opens every openable() gate, prints one name per line
run.py stop <dir> <unit> <reason...>
run.py go <dir> <unit> <sha>
run.py redo <dir> <unit> <sha> <reasons...>          # reasons joined with ", " when several words
run.py queue <dir> <unit> <sha>
run.py yield <dir> <unit> <reason...>
run.py merged <dir> <unit> <pr> [--more]
run.py closed <dir> <unit> <pr>
run.py status <dir>
run.py check <dir> <unit>                            # prstate.check_once on the unit's branch
run.py wait <dir> [--stall-hours N]
```

Words after `<reason>`/`<reasons>` are kept as given. For `redo`, the manager passes the check's reasons already joined by `, ` (one quoted argument); several plain words are joined with spaces.

`status` prints:

```
Run <dir name> (<repo>), queue <mode>, WIP <n|none>
- slot: <unit> <sha7> since <HH:MM> | slot: free
- queue: <unit> <sha7>, ... | queue: empty
- merged: <unit> #<pr>, ...
- open: <unit> PR #<pr> | <unit> working
- gated: <unit> on <prereqs or "WIP">
- needs the manager: <one line each> | needs the manager: nothing
```

"Needs the manager" lists:
- each `pending` request ("request <unit> <sha7> unanswered since HH:MM");
- each unit whose `ready_head` is unmerged ("PR #n ready since HH:MM");
- "slot free with a queue" when the slot is free and the queue is not empty;
- each `bad_requests` line;
- each unit with a closed gate in `openable()` ("gate <unit> may open").

- [ ] **Step 1: Write the failing CLI tests.** They use subprocess with `sys.executable`, temp dirs, and units without branches, so no `gh` is called:

```python
def test_init_unit_open_ready_flow(self):
    # init (wip 1); unit a; unit b --prereq a; open-ready prints "a\n"; gates.log == "a open\n"
def test_go_then_queue_then_yield_then_wait_next(self):
    # go a SHA1; queue b SHA2; yield a "owner question"; wait prints f"NEXT b {SHA2}"
def test_go_refused_exit_1(self):        # second unit's go: exit 1, stderr names holder
def test_redo_reasons_joined(self):      # redo b SHA1 "changes-since-review, escalated" -> exact pushes.log line
def test_wait_prints_request(self):      # requests.log line -> stdout f"REQUEST a {SHA1} feat/a\n", exit 0
def test_status_lists_pending_and_bad(self)
def test_merged_more_keeps_unit(self)
def test_usage_exit_2(self)
```

- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement `run.py`** with argparse subparsers and a `main(argv) -> int`. Each mutating command uses `with locked(dir) as state`. `wait` gets `gh=run_gh`, `sleep=time.sleep` and `now=time.time`.
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Rewrite `SKILL.md`.** Keep the existing frontmatter, the editing note, the authorization paragraph, Finishing and the Tracker shape (with lines for the slot and the queue added). Replace the rest per the spec's "Decided design":
  - **Three constraints** stay. Constraint 3 now names the run watcher: `python3 ${CLAUDE_SKILL_DIR}/../../scripts/run.py wait <run>` with `run_in_background`, restarted after every event. Add a fourth constraint: child notes can be held for hours, so nothing waits on a note.
  - **Setup:**
    - step 3 is `run.py init` plus `run.py unit` per unit, with the WIP limit if the owner gave one;
    - every unit's prompt carries the gate;
    - step 6 records `--branch` and `--worktree` from `get_session`, then starts one `wait`;
    - step 7 is `run.py open-ready`.
  - **Child prompt part 8** ("Review and push permission"):
    - the request is three steps: append `printf '%s push %s %s\n' '<unit>' '<sha>' '<branch>' >> <run>/requests.log`, start the existing `pushes.log` poll, then end the turn with `[push-request] <sha>`;
    - remove "sync with base" and "run the review again if anything but a clean base update changed" from asking;
    - `go` handling as in the spec, steps 1–5: confirm HEAD, sync, clean merge → validate → push HEAD, conflict → resolve, commit, ask, validation failure → fix and ask;
    - `redo` keeps its reason table and adds `pr-merged`: stop, and say in the recap whether the change still matters;
    - after an owner answer while the PR is open, always ask again, with the same sha if nothing changed.
  - **Child prompt parts 9–10, plus a new part:**
    - every turn's final message begins with a token, subagent hand-back turns included;
    - the token form for a turn that applies a manager message;
    - bot-only comments (coverage reports, "Review skipped" notices) end the turn at once with the unchanged token;
    - one open PR per unit: part k+1's code waits for part k's merge, and planning is allowed;
    - out-of-scope findings are escalated, never recorded as debt or follow-ups without the owner.
  - **Event handling** is one table over the watcher's lines (`REQUEST`, `NEXT`, `READY`/`REMIND`, `MERGED`, `CLOSED`, `BEHIND`, `STUCK`, `STALLED`, `ERROR`), plus rows for child messages (question, escalation: relay in text, then `run.py yield` if the unit holds the slot), user plan changes (`run.py set`, `stop`, a new unit), and "every user message: `run.py status`, act, ensure a `wait` is running". For a `REQUEST`:
    - covered goes to `go` (when `go` refuses, `queue`);
    - not covered goes to `redo` with the check's reasons joined by `, ` (no prefix);
    - a branch mismatch between the request and the check JSON's `branch` is `redo … wrong-branch`;
    - a done unit is `redo … pr-merged`, then ask the owner whether it becomes a follow-up;
    - `get_session` with no worktree path, or a check error, is `redo` with `no-worktree-path` or `check-error`, plus one line to the user.
  - **Merge bar:**
    - `READY`/`REMIND`/`check` ready for the head;
    - `branch-review:check` covered on that head in the unit's worktree;
    - no unanswered request.

    Merge with `--match-head-commit`. Then:
    - run `run.py merged` (`--more` for part k < N, plus one message to the child);
    - notes go in `notes.md`;
    - run `run.py open-ready`.
  - **Questions to the owner** go in reply text, never the pop-up question tool; the tracker's "Waiting on you" shows them.
  - **Messages:** never alongside a `pushes.log`/`gates.log` write; never about reviews; a gated child gets a message only 5+ minutes after its gate opened.
  - **The `specs` unit:** it uses `go`/`queue` for itself, and its merge bar is the same.
  - **Red flags:** replace the `SKIP_HEAD`, idle-child and "I'll message the go" rows. Add:
    - "I'll wait for the child's note" / "it's only a clean sync, it needs a review" / "I'll ask with the question tool";
    - "the PR is ready but the child is running";
    - "the next queued unit should sync first" (the `go` covers it).

  Then:
  - Check the file for a dollar sign followed by a digit: `grep -nE '[$][0-9]' plugins/implementation-manager/skills/implementation-manager/SKILL.md` must print nothing.
  - Check for SqlSource names: `grep -niE 'sqlsource|SQLSRC|td-00' plugins/implementation-manager/skills/implementation-manager/SKILL.md` must print nothing.
- [ ] **Step 6: Update `README.md`.**
  - Run folder: the five files.
  - "Watch script" becomes "Run script": the commands, the event lines and their meanings, and the timings.
  - The merge bar and the push queue, including yielding and parallel mode.
  - The root README line.
- [ ] **Step 7: Delete `wait_pr_ready.py`, its test file and its `known-first-party` entry. Bump the version to `0.2.0`.**
- [ ] **Step 8: Verify.** Run `python3 -m unittest discover -s plugins/implementation-manager/tests -v && ruff check . && ruff format --check . && claude plugin validate . && claude plugin validate plugins/implementation-manager`. Expected: all pass. `grep -rn wait_pr_ready plugins/ pyproject.toml` prints nothing.
- [ ] **Step 9: Commit** as `feat(implementation-manager): run watcher CLI and skill on the push queue (0.2.0)`.

### Task 7: Evals

**Files:**
- Modify: `plugins/implementation-manager/evals/push-gate/scaffold.sh`, `prompt.md`, `graders/check-ran.md` (unchanged unless the skill invocation changes), `graders/gate.md` (unchanged expectation)
- Create: `plugins/implementation-manager/evals/push-queue/case.yaml`, `scaffold.sh`, `prompt.md`, `graders/skill-fired.md`, `graders/queue.md`, `graders/yield-ran.md`

**Interfaces:**
- Consumes: `run.py` (scaffold calls `python3 "$here/../../scripts/run.py" init/unit/open`), branch-review's `review_state.py` as today.

- [ ] **Step 1: Move `push-gate` to the new protocol.**
  - The scaffold builds the run with `run.py init <ws>/run --repo fixture/shop --base main --method merge`. Units `u1` (`--branch u1 --worktree <ws>/repo`) and `u2` (`--branch u2 --worktree <ws>/wt-u2`) get open gates.
  - It appends `u1 push <sha1> u1` and `u2 push <sha2> u2` to `requests.log`.
  - `notes.md` keeps the units table and drops the "child notes received" list.
  - The prompt says the children asked to push through the run's request log, that there is no `gh` (so the watcher's PR polling will report errors, which can be ignored), and that the outcome is what ends up in `run/pushes.log`.
  - The expected `pushes.log` is unchanged: `u1 go <sha1>`, `u2 redo <sha2> changes-since-review`.
- [ ] **Step 2: Create `push-queue`.**
  - The scaffold builds two units, both reviewed clean at HEAD (u2 in a linked worktree, like push-gate's u2 but without the extra commit), with both requests in `requests.log`.
  - The prompt says to handle the requests as the skill says. It then says: "u1's child then sends you this message: it needs an owner decision on escalated finding R1-F1 (keep or drop the retry), recommending keep." The manager relays it, and the outcome checked is the run folder.
  - `graders/queue.md` (llm, focus `run/pushes.log`): exactly two lines, `u1 go <sha1>` first and `u2 go <sha2>` second (the shas fixed by the scaffold's dates; compute them once and write them in). It fails on any `redo`, a missing line, or the order reversed.
  - `graders/yield-ran.md` (`tool_used` Bash): `input_match: 'run\.py[^"]*\byield\b[^"]*\bu1\b'`.
  - `graders/skill-fired.md`: as in push-gate.
- [ ] **Step 3: Validate and run both evals.** Run `claude plugin validate plugins/implementation-manager` and `claude plugin eval plugins/implementation-manager --scaffold`. Expected: both cases pass with the skill and fail without it, as push-gate did at 0.1.0. The eval needs a setup token; in a worktree session, run it from the main checkout as the plugin-eval memory note describes. If it can't run here, say so and leave it for the user.
- [ ] **Step 4: Commit** as `test(implementation-manager): evals for the run watcher protocol and push queue`.
