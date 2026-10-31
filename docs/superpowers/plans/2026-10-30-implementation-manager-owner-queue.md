# Implementation Manager Owner Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The manager shows the owner one waiting item at a time, grouped by unit, with parking, and forwards a unit's answers in one message, all from state kept by `run.py`.

**Architecture:** A new `scripts/items.py` holds the owner queue: pure functions over the `items` block of `run.json` plus the item body and answer files in `<run>/items/`. `run.py` gains an `item` command with subcommands, `stop` drops a unit's items, and `status` prints the queue and `forward due` lines. SKILL.md routes every owner-facing item through the queue and gives children the `Q<n>` header rule.

**Tech Stack:** Python 3.9+ standard library (`argparse`, `json`, `pathlib`), `unittest`, ruff; Markdown skill and docs; `claude plugin eval`.

**Spec:** `docs/superpowers/specs/2026-10-30-implementation-manager-owner-queue-design.md`

## Global Constraints

- Plugin scripts are Python 3.9+, standard library only, invoked through `python3`; `from __future__ import annotations` at the top of each module, as the existing scripts do.
- Run `ruff check .` and `ruff format .` before each commit that touches Python.
- Tests run with `python3 -m unittest discover -s plugins/implementation-manager/tests`.
- CLI conventions: the run folder comes right after the (sub)command; a refused command exits 1 with one line on stderr (`run.py item <sub>: <message>`) and changes nothing; usage errors exit 2. Only the script writes `run.json` and the files in `<run>/items/`.
- Kinds are exactly `question`, `finding`, `command`, `decision`. Statuses are exactly `queued`, `current`, `parked`, `answered`, `forwarded`, `dropped`.
- Filed ids are `<unit>-<n>`, numbered per unit across the run; run-wide items are `run-<n>`. A unit named `run` shares the `run` counter, so ids never collide. Ids are never reused.
- A unit's `generation` starts at 1 and goes up by one when `unit --worktree P` records a worktree different from a stored non-null one.
- Blocking status never changes the order.
- `plugin.json` version `0.8.0`.
- SKILL.md: never write a dollar sign followed by a digit (editing note at its top).
- README.md is in STE (AGENTS.md "Documentation"); ARCHITECTURE.md gets plain prose.

## Review Focus

- **Manager compacted between an answer and its forward.** The owner's words exist only on disk. `status` must list `forward due <unit> <ids>` until `item forwarded` runs. Pinned by `test_status_lists_forward_due` (Task 4).
- **A child reuses a `Q<n>` in the same session.** The refusal must name the existing filed id, so the manager can tell the child or file it without a ref. Pinned by `test_duplicate_ref_names_existing_item` (Task 2).
- **A unit is stopped while its item is current.** The next item must become current and every dropped id is printed. Pinned by `test_stop_drops_items_and_promotes` (Task 4).
- **A missing or unreadable `--body-file`.** Nothing changes: no state, no counter bump, no file in `items/`. Pinned by `test_add_missing_body_file_changes_nothing` (Task 4).
- **A run started before 0.8.0.** Its `run.json` has no `items` key and no unit `generation`; every command must work on it and the first write adds them. Pinned by `test_load_backfills_items_and_generation` (Task 1).

---

### Task 1: Items state and session generations

**Files:**
- Modify: `plugins/implementation-manager/scripts/runstate.py` (`empty_items`, `init_run`, `load`, `add_unit`)
- Test: `plugins/implementation-manager/tests/test_runstate.py`

**Interfaces:**
- Produces: `runstate.empty_items() -> dict` returning `{"list": [], "current": None, "sticky": None, "resume": None, "seq": 0, "counters": {}}`. `state["items"]` always has this shape after `init_run` or `load`. Each unit record has `"generation": int`.
- `sticky` holds a unit name, `""` for the run-wide group, or `None` (no sticky group). Run-wide items store `"unit": ""`.

- [ ] **Step 1: Write the failing tests** in `test_runstate.py`:

```python
def test_init_has_empty_items(self):
    state = init_run(self.dir, repo="o/r", base="main", method="squash", wip=None, queue_mode="serial", now=0)
    self.assertEqual(state["items"], {"list": [], "current": None, "sticky": None, "resume": None, "seq": 0, "counters": {}})

def test_load_backfills_items_and_generation(self):
    # a 0.7 run.json: no items key, a unit without generation
    ...write a run.json with STATE_KEYS only and units {"a": {...no generation...}}
    state = load(self.dir)
    self.assertEqual(state["items"]["list"], [])
    self.assertEqual(state["units"]["a"]["generation"], 1)

def test_worktree_change_bumps_generation(self):
    add_unit(state, "a", prereqs=None, branch=None, worktree=None, specs=None)
    self.assertEqual(state["units"]["a"]["generation"], 1)
    add_unit(state, "a", prereqs=None, branch=None, worktree="/w1", specs=None)
    self.assertEqual(state["units"]["a"]["generation"], 1)   # first recording
    add_unit(state, "a", prereqs=None, branch=None, worktree="/w1", specs=None)
    self.assertEqual(state["units"]["a"]["generation"], 1)   # same path
    add_unit(state, "a", prereqs=None, branch=None, worktree="/w2", specs=None)
    self.assertEqual(state["units"]["a"]["generation"], 2)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests -k generation -k items -v`
Expected: FAIL (`KeyError: 'items'` / `'generation'`).

- [ ] **Step 3: Implement.** `runstate.empty_items()`; `init_run` stores `"items": empty_items()`; `load` does `state.setdefault("items", empty_items())` and `unit.setdefault("generation", 1)` (leave `STATE_KEYS` unchanged so 0.7 runs load); `add_unit` creates new units with `"generation": 1` and bumps it when `worktree` is given, the stored worktree is not `None`, and they differ. Imports go one way only: `items.py` imports `runstate` (for `RunError`), and `runstate` never imports `items`.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts plugins/implementation-manager/tests
git commit -m "feat(implementation-manager): items state block and unit session generations"
```

---

### Task 2: Queue core — add, order, done, forward

**Files:**
- Create: `plugins/implementation-manager/scripts/items.py`
- Test: `plugins/implementation-manager/tests/test_items.py` (new)

**Interfaces:**
- Consumes: `state["items"]`, `state["units"][u]["generation"]`, `state["units"][u]["gate"]` from Task 1; `runstate.RunError`.
- Produces (all mutate `state` in place; the caller holds the lock; `run_dir` is the run folder; `now` is a float timestamp):
  - `KINDS = ("question", "finding", "command", "decision")`
  - `add_item(run_dir: Path, state: dict, unit: str, *, ref: str | None, kind: str, blocks: bool, depends: list[str], summary: str, body: str, now: float) -> str` — `unit=""` means run-wide. Returns `current <header>` or `queued <id> for <unit|run>, <N> waiting`.
  - `queue_order(state: dict) -> list[dict]` — queued items in the order they will be shown, current excluded.
  - `current_text(run_dir: Path, state: dict) -> str` — header, blank line, body; or `none`.
  - `done_item(run_dir: Path, state: dict, item_id: str, answer: str | None) -> str`
  - `forward_due(state: dict) -> list[str]` — `forward due <unit> <id> <id> …`
  - `mark_forwarded(state: dict, unit: str) -> str` — `forwarded <id> …`
  - `drop_item(state: dict, item_id: str, reason: str) -> None`
  - `drop_unit_items(state: dict, unit: str, reason: str) -> list[str]` — `dropped <id>` lines.
  - `header(item: dict) -> str` — `<id>[ (<ref>)] · <unit|run> · <kind> · blocks merge: yes|no[ · depends on: <id>, <id>] · <summary>`
  - `_promote(state: dict) -> None` — if `current` is None and `queue_order` is non-empty, make its first item current, set `sticky` to its unit, and clear `resume` if it was that item. Every mutating function calls it last.

Item record fields: `id, unit, ref, generation, kind, blocks, depends (filed ids), summary, status, added, seq, note, parked_at, parked_with, reason`.

**Ordering algorithm** (`queue_order`), which the tests below determine:

```
pending = queued items sorted by seq; sticky = items.sticky; resume = items.resume; out = []
while pending:
    same = [i for i in pending if i.unit == sticky] if sticky is not None else []
    if same:            pick = same[0]
    elif resume queued and in pending: pick = that item
    else:               pick = pending[0]           # unit whose oldest queued item is oldest
    out.append(pick); pending.remove(pick); sticky = pick.unit
    if pick is the resume item: resume = None
```

**Validation in `add_item`** (each a `RunError`, checked before anything is written): unknown unit; stopped unit (`unit 'x' is stopped`); kind not in `KINDS`; run-wide with a kind other than `decision`; `ref` given with run-wide or `decision`; `ref` not matching `Q[1-9][0-9]*`; `depends` without `ref`; a `ref` already used in the unit's current generation, any status (`ref Q2 of unit 'a' is already a-3`); a `depends` ref not in the unit's current generation; a summary that is empty or not one line. Then: write `<run>/items/<id>.md` (create the folder), append the record with `seq = ++items.seq`, `counters[prefix] += 1` where prefix is the unit or `run`. An item whose `depends` include a parked item is stored `parked`, `parked_with` that item's id, note `depends on <id>`, `parked_at=now` (Task 3 consumes this).

**`done_item`:** refuses an unknown id and any status other than `current`, `queued`, `parked` (`item a-1 is answered`); refuses `answer=None` for kinds other than `decision`. Writes `<run>/items/<id>.answer.md` when an answer is given. A `decision` goes to `forwarded`; others to `answered`. Clears `current` if it was current, promotes, and returns the new `current_text` header line (header only, not the body) or `none`, followed by `forward_due` lines.

**`forward_due`:** one line per unit (in order of each unit's earliest answered seq) that has `answered` items and no `current` or `queued` item.

**`mark_forwarded`:** refuses a unit with no `answered` items.

**`drop_item`:** refuses unknown, `forwarded` or `dropped` items; sets `dropped` and `reason`; promotes. **`drop_unit_items`:** drops every `queued`, `current`, `parked` and `answered` item of the unit; promotes.

- [ ] **Step 1: Write the failing tests** in `test_items.py` (a `setUp` that `init_run`s a temp run and adds units `a`, `b`, `c`; a helper `add(unit, ref=None, kind="question", depends=(), blocks=False)` that calls `add_item` with `summary=f"{unit} {ref}"`, `body="text"`, `now=next(clock)`):

```python
def test_first_item_becomes_current(self):
    self.assertTrue(self.add("a", "Q1").startswith("current a-1 (Q1) · a · question"))
    self.assertEqual(self.state["items"]["current"], "a-1")

def test_sticky_unit_beats_older_other_unit(self):
    self.add("a", "Q1"); self.add("b", "Q1"); self.add("a", "Q2")
    self.assertEqual([i["id"] for i in queue_order(self.state)], ["a-2", "b-1"])
    done_item(self.dir, self.state, "a-1", "yes")
    self.add("a", "Q3")                       # arrives while the owner is on a
    self.assertEqual([i["id"] for i in queue_order(self.state)], ["a-3", "b-1"])

def test_next_unit_is_the_one_with_the_oldest_item(self):
    self.add("a", "Q1"); self.add("c", "Q1"); self.add("b", "Q1"); self.add("b", "Q2"); self.add("c", "Q2")
    done_item(self.dir, self.state, "a-1", "x")
    self.assertEqual([self.state["items"]["current"]] + [i["id"] for i in queue_order(self.state)],
                     ["c-1", "c-2", "b-1", "b-2"])

def test_blocking_does_not_jump(self):
    self.add("a", "Q1"); self.add("a", "Q2"); self.add("b", "Q1", kind="finding", blocks=True)
    self.assertEqual([i["id"] for i in queue_order(self.state)], ["a-2", "b-1"])

def test_queued_output_counts_waiting(self):
    self.add("a", "Q1")
    self.assertEqual(self.add("b", "Q1"), "queued b-1 for b, 1 waiting")

def test_duplicate_ref_names_existing_item(self):
    self.add("a", "Q1")
    with self.assertRaisesRegex(RunError, r"ref Q1 of unit 'a' is already a-1"):
        self.add("a", "Q1")

def test_new_generation_reuses_refs_and_scopes_depends(self):
    self.add("a", "Q1")
    add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w1", specs=None)
    add_unit(self.state, "a", prereqs=None, branch=None, worktree="/w2", specs=None)
    self.add("a", "Q1")                                   # a-2, generation 2
    self.add("a", "Q2", depends=["Q1"])
    self.assertEqual(self.item("a-3")["depends"], ["a-2"])

def test_refusals(self):
    # each of: unknown unit, stopped unit, bad kind, run-wide question, ref on decision,
    # ref "q1", depends without ref, unknown depends ref, two-line summary
    ...one assertRaises(RunError) per case, and state["items"] unchanged after each

def test_done_answered_then_forward_due_when_leaving_unit(self):
    self.add("a", "Q1"); self.add("a", "Q2"); self.add("b", "Q1")
    out = done_item(self.dir, self.state, "a-1", "keep it")
    self.assertEqual(out, header(self.item("a-2")))          # no forward due yet
    self.assertEqual((self.dir / "items" / "a-1.answer.md").read_text(), "keep it")
    out = done_item(self.dir, self.state, "a-2", "drop it")
    self.assertEqual(out.splitlines(), [header(self.item("b-1")), "forward due a a-1 a-2"])
    self.assertEqual(mark_forwarded(self.state, "a"), "forwarded a-1 a-2")
    self.assertEqual(forward_due(self.state), [])

def test_out_of_order_answer(self):
    self.add("a", "Q1"); self.add("b", "Q1")
    self.assertIn("forward due b b-1", done_item(self.dir, self.state, "b-1", "yes"))
    self.assertEqual(self.state["items"]["current"], "a-1")

def test_decision_needs_no_answer_and_is_not_forwarded(self):
    add_item(self.dir, self.state, "", ref=None, kind="decision", blocks=False, depends=[],
             summary="every unit stopped", body="x", now=1)
    done_item(self.dir, self.state, "run-1", None)
    self.assertEqual(self.item("run-1")["status"], "forwarded"); self.assertEqual(forward_due(self.state), [])

def test_done_refuses_missing_answer_and_closed_items(self): ...
def test_drop_unit_items_promotes_next(self):
    self.add("a", "Q1"); self.add("a", "Q2"); self.add("b", "Q1")
    self.assertEqual(drop_unit_items(self.state, "a", "unit stopped"), ["dropped a-1", "dropped a-2"])
    self.assertEqual(self.state["items"]["current"], "b-1")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests -p test_items.py -v`
Expected: FAIL (`ImportError: cannot import name 'add_item'`).

- [ ] **Step 3: Implement the functions above in `items.py`.**

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts/items.py plugins/implementation-manager/tests/test_items.py
git commit -m "feat(implementation-manager): owner queue ordering, answers and forwards"
```

---

### Task 3: Parking and unparking

**Files:**
- Modify: `plugins/implementation-manager/scripts/items.py`
- Test: `plugins/implementation-manager/tests/test_items.py`

**Interfaces:**
- Consumes: Task 2's record fields, `_promote`, `header`, `forward_due`.
- Produces:
  - `park_item(state: dict, item_id: str, note: str, with_ids: list[str], now: float) -> str` — `parked <id>` per item, then the new current header or `none`, then `forward_due` lines.
  - `unpark_item(state: dict, item_id: str, *, to_queue: bool) -> str` — the new current header, or `queued <id>` with `to_queue`.

**Rules:**
- `park_item` refuses an id not `queued` or `current`, a `with_ids` id not `queued` or `current`, and a note that is empty or not one line. It parks the item (note, `parked_at=now`, `parked_with=None`), then, repeatedly until nothing changes, every `queued` or `current` item whose `depends` contain a parked id, plus each `with_ids` item, with `parked_with=<root id>` and note `with <root id>`. If the current item was parked, clear `current` and promote.
- `unpark_item` refuses an item not `parked`. Without `to_queue`: if there is a current item, set it `queued` and `resume` to its id; make the item `current`, `sticky` its unit. With `to_queue`: set it `queued` (its seq unchanged), then promote. Either way, every item with `parked_with == item_id` returns to `queued` with note and `parked_with` cleared.
- An item added later with `depends` on a parked item is parked with `parked_with` that item (Task 2), so it returns with it.

- [ ] **Step 1: Write the failing tests:**

```python
def test_park_takes_marked_dependents_and_promotes(self):
    self.add("a", "Q1")
    self.add("a", "Q2", depends=["Q1"])
    self.add("a", "Q3")
    self.add("b", "Q1")
    out = park_item(self.state, "a-1", "ask Sam", [], now=50)
    self.assertEqual(out.splitlines()[:2], ["parked a-1", "parked a-2"])
    self.assertEqual(self.state["items"]["current"], "a-3")  # unmarked a-3 stays, sticky a


def test_park_with_and_keep_it(self):
    self.add("a", "Q1")
    self.add("a", "Q2")
    self.add("b", "Q1")
    park_item(self.state, "a-1", "ask Sam", ["a-2"], now=50)
    self.assertEqual(self.item("a-2")["parked_with"], "a-1")
    self.assertEqual(unpark_item(self.state, "a-2", to_queue=True), "queued a-2")
    self.assertEqual(self.state["items"]["current"], "b-1")
    self.assertEqual([i["id"] for i in queue_order(self.state)], ["a-2"])


def test_unpark_resumes_displaced_item_after_unit(self):
    self.add("a", "Q1")
    self.add("a", "Q2", depends=["Q1"])
    self.add("b", "Q1")
    self.add("b", "Q2")
    self.add("c", "Q1")
    park_item(self.state, "a-1", "later", [], now=50)  # a-2 goes too; b-1 current
    unpark_item(self.state, "a-1", to_queue=False)
    self.assertEqual(self.state["items"]["current"], "a-1")
    self.assertEqual([i["id"] for i in queue_order(self.state)], ["a-2", "b-1", "b-2", "c-1"])


def test_item_depending_on_parked_item_is_parked_on_add(self):
    self.add("a", "Q1")
    park_item(self.state, "a-1", "later", [], now=50)
    self.add("a", "Q2", depends=["Q1"])
    self.assertEqual(self.item("a-2")["status"], "parked")
    unpark_item(self.state, "a-1", to_queue=False)
    self.assertEqual(self.item("a-2")["status"], "queued")


def test_parking_rest_of_unit_makes_forward_due(self):
    self.add("a", "Q1")
    self.add("a", "Q2")
    self.add("b", "Q1")
    done_item(self.dir, self.state, "a-1", "yes")
    self.assertIn("forward due a a-1", park_item(self.state, "a-2", "later", [], now=60))


def test_park_refusals(self): ...  # parked/answered id, bad with id, two-line note
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests -p test_items.py -v`
Expected: FAIL (`ImportError: cannot import name 'park_item'`).

- [ ] **Step 3: Implement `park_item` and `unpark_item`.**

- [ ] **Step 4: Run the whole suite** — Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/implementation-manager/scripts/items.py plugins/implementation-manager/tests/test_items.py
git commit -m "feat(implementation-manager): park and unpark owner items with their dependents"
```

---

### Task 4: `run.py item`, `stop` and `status`; ARCHITECTURE.md

**Files:**
- Modify: `plugins/implementation-manager/scripts/run.py` (module docstring, `_parser`, `_run` — its `stop` branch calls `items.drop_unit_items(state, unit, "unit stopped")` after `stop_gate` and returns the lines — `_needs`, `_status`)
- Modify: `plugins/implementation-manager/ARCHITECTURE.md`
- Test: `plugins/implementation-manager/tests/test_run_cli.py`

**Interfaces:**
- Consumes: every Task 2 and Task 3 function.
- Produces the CLI (`<dir>` always right after the subcommand):

```
item add <dir> [unit] [--run-wide] [--ref Q<n>] --kind K --blocks yes|no [--depends Q<n> ...] --summary S --body-file P
item current <dir>
item done <dir> <id> [--answer-file P]
item park <dir> <id> <note...> [--with ID ...]
item unpark <dir> <id> [--queue]
item forwarded <dir> <unit>
item drop <dir> <id> <reason...>
item list <dir>
```

- `item` is a subparser with its own required subparsers (`dest="item_command"`); errors print as `run.py item <sub>: …`. Exactly one of `unit` and `--run-wide` (else `RunError`). `--body-file` and `--answer-file` are read before the lock is taken; an unreadable file is a refusal with nothing changed.
- `item list` prints `current <header>`, then `queued <header>` in `queue_order`, then `parked <header> · <note> since HH:MM`, then `answered <header> (not forwarded)`; `none` when all are empty.
- `stop` prints the `dropped <id>` lines (it printed nothing before).
- `status` adds, after the `- gated:` line (before integration lines): `- items: current <id>[ (<ref>)] <summary> · <N> more: <unit> ×<n>, …` (counts in `queue_order` order; `run` for run-wide; `· nothing more` when none queued) or `- items: none`; and `- parked: <id>[ (<ref>)] <summary>, <note>, since HH:MM; …` only when something is parked. `_needs` adds each `forward_due` line.

- [ ] **Step 1: Write the failing CLI tests** (new `ItemCliTests(CliTestCase)`; `setUp` inits a run with units `a` and `b`; helper `item_add(unit, ref, *extra)` writes `text of <unit> <ref>` to a body file in `self.tmp` and calls `ok("item", "add", str(self.dir), unit, "--ref", ref, "--kind", "question", "--blocks", "no", "--summary", f"{unit} {ref}", "--body-file", path, *extra)`):

```python
def test_add_current_done_flow(self):
    out = self.item_add("a", "Q1")
    self.assertTrue(out.startswith("current a-1 (Q1) · a · question · blocks merge: no"))
    self.assertEqual(self.item_add("b", "Q1"), "queued b-1 for b, 1 waiting\n")
    self.assertIn("text of a Q1", self.ok("item", "current", str(self.dir)))
    answer = self.tmp / "ans.md"; answer.write_text("keep the retry")
    out = self.ok("item", "done", str(self.dir), "a-1", "--answer-file", str(answer))
    self.assertEqual(out.splitlines()[-1], "forward due a a-1")

def test_status_lists_forward_due(self):
    self.item_add("a", "Q1"); self.item_add("b", "Q1")
    ...done a-1 with an answer file
    status = self.ok("status", str(self.dir))
    self.assertIn("- items: current b-1 (Q1) b Q1 · nothing more", status)
    self.assertIn("  - forward due a a-1", status)
    self.ok("item", "forwarded", str(self.dir), "a")
    self.assertNotIn("forward due", self.ok("status", str(self.dir)))

def test_status_parked_line(self): ...   # park a-1 "ask Sam" → "- parked: a-1 (Q1) a Q1, ask Sam, since"

def test_stop_drops_items_and_promotes(self):
    self.item_add("a", "Q1"); self.item_add("a", "Q2"); self.item_add("b", "Q1")
    self.assertEqual(self.ok("stop", str(self.dir), "a", "cancelled"), "dropped a-1\ndropped a-2\n")
    self.assertEqual(self.state()["items"]["current"], "b-1")

def test_add_missing_body_file_changes_nothing(self):
    before = self.read("run.json")
    result = self.run_cli("item", "add", str(self.dir), "a", "--ref", "Q1", "--kind", "question",
                          "--blocks", "no", "--summary", "s", "--body-file", str(self.tmp / "nope.md"))
    self.assertEqual(result.returncode, 1)
    self.assertTrue(result.stderr.startswith("run.py item add: "))
    self.assertEqual(self.read("run.json"), before)
    self.assertFalse((self.dir / "items").exists())

def test_unit_and_run_wide_are_exclusive(self): ...   # both → exit 1; neither → exit 1
def test_list_order(self): ...                        # current, queued, parked, answered lines in that order
def test_old_run_json_without_items(self): ...        # strip "items" and unit "generation" from run.json, then item add works
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests -p test_run_cli.py -k Item -v`
Expected: FAIL (`invalid choice: 'item'`, exit 2).

- [ ] **Step 3: Implement the CLI, `stop_gate`'s drop, and the `status` lines.** Add the `item` lines to the module docstring in the same style as the existing commands.

- [ ] **Step 4: Run the whole suite, then lint**

Run: `python3 -m unittest discover -s plugins/implementation-manager/tests && ruff check . && ruff format --check .`
Expected: OK, no ruff findings.

- [ ] **Step 5: Add an "Owner queue" section to `ARCHITECTURE.md`** after "Holds and ordering": what an item is, the state and `items/` files, the ordering rule (sticky unit, oldest unit, resume), parking and `parked_with`, `forward due` and `item forwarded`, session generations, `stop` dropping items, and that holds stay separate. Add `items/` to the "Run folder" tree.

- [ ] **Step 6: Commit**

```bash
git add plugins/implementation-manager/scripts plugins/implementation-manager/tests plugins/implementation-manager/ARCHITECTURE.md
git commit -m "feat(implementation-manager): run.py item commands; stop drops items; status shows the queue"
```

---

### Task 5: SKILL.md, README and version

**Files:**
- Modify: `plugins/implementation-manager/skills/implementation-manager/SKILL.md`
- Modify: `plugins/implementation-manager/README.md`
- Modify: `plugins/implementation-manager/.claude-plugin/plugin.json` (`"version": "0.8.0"`)

**Interfaces:**
- Consumes: the CLI from Task 4, exactly as specified there.

- [ ] **Step 1: The run script tables.** Add rows for `item add`, `item current`, `item done`, `item park`, `item unpark`, `item forwarded`, `item drop`, `item list` with one line each, matching Task 4; add to the `stop` row that it prints `dropped <id>` for the unit's items; add to the `unit` row that a changed `--worktree` starts a new session generation.

- [ ] **Step 2: A new "The owner queue" section** before "Questions to the owner", replacing that paragraph's "go in the reply text" rule. It says:
  - Every item for the owner goes through `run.py item add` (body: the question or command and why, `fix_options` quoted word for word, the child's recommendation and yours), never straight into the reply.
  - The reply after `item add`: when it printed `current …`, run `item current` and show it in full with `<N> more waiting: <unit> ×<n>, …`; when it printed `queued …`, one line, `queued for <unit>, <N> waiting`.
  - An owner reply that answers the current item: write the owner's words to a file in the scratchpad, `item done <id> --answer-file <file>`, then show the next item (`item current`). A reply that asks back keeps the item current. A command item: done when the owner says it ran; pasted output goes in the answer file. The owner may answer a queued item by name; `item done` takes it.
  - "Park this" and the like: `item park <id> <note>`; add `--with <ids>` for unmarked items that plainly build on it, and say so in one line. "Keep it": `item unpark <id> --queue`. "Back to <id>": `item unpark <id>`, then show it.
  - Every `forward due <unit> <ids>` line (from `done`, `park`, or `status`): send the unit's child one message quoting each answer from `<run>/items/<id>.answer.md` with its `ref`, run `item forwarded <unit>`, then the hold steps of "The user answers a question you relayed".
  - Splitting: a child message with several items becomes one `item add` per item, in order. Read `ref`, kind, `blocks merge` and `depends on` from each item's header line; an item with no header gets no `--ref` and no `--depends`, and blocks unless it says "blocks merge: no".
  - After a merge, drop (`item drop <id> <reason>`) the unit's items that no longer matter and say so in one line each; `stop` drops its items itself, so list what it printed in one line.
  - **AFK return:** on any owner message that does not answer or discuss the current item, handle it, then end the reply with the current item in full.

- [ ] **Step 3: Event-table rows.** In each row that today gives the owner something in the reply text — the escalated finding / question / `[blocked]` row, `FINAL-READY`, `ORPHAN`, `CLOSED`, the `pr-merged` follow-up in "Answering a push request" step 1, `check-error` and `no-worktree-path` (steps 2–3), failed merges ("Merge"), and "every unit stopped" — replace "tell the user" / "ask the owner" with `item add` (`--kind decision` for manager items; `--run-wide` for "every unit stopped"). Keep each row's other actions (holds, `yield`, notes) unchanged. Replace "note it under the unit's open questions in `notes.md`" with the item, except for the designed-unit (`own session`) notice row, which keeps `notes.md`.

- [ ] **Step 4: "The user answers a question you relayed" row** becomes the answer flow from Step 2: the forward happens on `forward due`, quoting the owner's words from the answer files; hold release and `hold <unit> answer sent: <topic>` run then, once the unit has no open blocking item (`item list`). "Answering a push request" step 5 keeps striking `own session` questions in `notes.md`, and also requires no open blocking item for the unit before `release`.

- [ ] **Step 5: Tracker.** Replace `- Waiting on you: …` with `- Waiting on you: <id> (<ref>) <summary> · <N> more | nothing`, and add `- Parked: <id> (<summary>), <note>, since <HH:MM> | none` and `- Elsewhere: <unit> own session (<topic>) · card for <unit> waiting for you to start it | none`, all from `run.py status`, `item list` and `notes.md`. Setup step 5's card starts and the designed-unit notice row go on `Elsewhere:`.

- [ ] **Step 6: Child prompt.** In part 8's Questions bullet and part 10, add word for word:

```
Number every item you send the manager (a question, an escalated finding, or something the owner must run) Q1, Q2, … for the life of this session, and never reuse a number. Begin each item with one header line: `Q<n> · kind: question|finding|command · blocks merge: yes|no · depends on: Q<m>`. Leave out `depends on` when the item does not build on an earlier one; give it whenever it does. An escalated finding needs no `blocks merge`. A command item gives the exact command and why you need it.
```

- [ ] **Step 7: Red flags.** Add rows: "Three new questions came in; I'll show them all" → "One current item. The rest are queued; say how many."; "I'll forward this answer now" → "Only on `forward due`: one message per unit."; "The owner is back; I'll summarize the queue" → "Show the current item in full, with the count."

- [ ] **Step 8: README.** Replace the "When a child has a question…" bullet under "During the run" with STE text: the manager shows one item at a time; items of one child come together; you can park an item and come back to it; the tracker lists parked items. Keep the blocking sentences.

- [ ] **Step 9: Verify**

Run: `grep -nE '\$[0-9]' plugins/implementation-manager/skills/implementation-manager/SKILL.md; claude plugin validate . && claude plugin validate plugins/implementation-manager`
Expected: no grep output; both validations pass.

- [ ] **Step 10: Commit**

```bash
git add plugins/implementation-manager
git commit -m "feat(implementation-manager): route owner items through the queue; child Q<n> headers; 0.8.0"
```

---

### Task 6: Eval case `owner-queue`

**Files:**
- Create: `plugins/implementation-manager/evals/owner-queue/case.yaml`, `scaffold.sh`, `prompt.md`
- Create: `plugins/implementation-manager/evals/owner-queue/graders/skill-fired.md` (copy of push-queue's), `done-ran.md`, `state.md`, `reply.md`

**Interfaces:**
- Consumes: the CLI from Task 4 and SKILL.md from Task 5.

- [ ] **Step 1: `case.yaml`** as push-queue's (`schema_version: "1.1"`, `plugins: ["../..", "../../../branch-review"]`, `scaffold_script: scaffold.sh`).

- [ ] **Step 2: `scaffold.sh`** (same strict header as push-queue's, no git repo needed): `run.py init run --repo fixture/shop --base main --method merge`; units `u1`, `u2` with `--branch` and `--worktree "$ws/u1"` / `"$ws/u2"` (create the empty folders); open both gates; then `item add` in this order: u1 `Q1` question "keep or drop the retry" blocks yes; u2 `Q1` question "rename the setting"; u1 `Q2` question "log level for retries", depends `Q1`. Write `run/notes.md` with the units table. Ids are then u1-1 (current), u2-1, u1-2.

- [ ] **Step 3: `prompt.md`** (`max_turns: 40`, `timeout_seconds: 900`, tools as push-queue's): you are the implementation manager of the run in `run` (absolute path via `pwd`); the items are already queued; there are no session tools, so a message to a child cannot be sent — note in the reply what you would send instead. Then the owner's message: "Keep the retry."

- [ ] **Step 4: Graders**
  - `done-ran.md`: `type: tool_used`, `tool: Bash`, `input_match: '(?=[\s\S]*run\.py)[\s\S]*\bitem\s+done\s+\S+\s+u1-1\b'`.
  - `state.md`: `type: llm` on `run/run.json`: pass only if item `u1-1` is `answered`, `u1-2` is `current`, `u2-1` is `queued`, and no item is `forwarded`.
  - `reply.md`: `type: llm` on the final response: pass only if it shows u1's log-level question (u1-2) as the one item to answer and does not present u2's rename question as a question to answer now (a count or a one-line mention of it waiting is fine).

- [ ] **Step 5: Run the scaffold locally** in a new empty folder under the session's scratchpad, then `python3 <repo>/plugins/implementation-manager/scripts/run.py item list "$PWD/run"`.

Expected: three lines in the order `current u1-1`, `queued u1-2`, `queued u2-1` (the sticky unit puts u1-2 ahead of the older u2-1).

- [ ] **Step 6: Run the eval** (token in `~/.zprofile`, run through `zsh -lc`):

Run: `zsh -lc 'claude plugin eval plugins/implementation-manager --case owner-queue'`
Expected: every grader passes with the skill. If the eval cannot run here, record it in `docs/deferred` (next id from `docs/deferred/README.md`) in this commit.

- [ ] **Step 7: Commit**

```bash
git add plugins/implementation-manager/evals/owner-queue docs/deferred
git commit -m "test(implementation-manager): owner-queue eval case"
```
