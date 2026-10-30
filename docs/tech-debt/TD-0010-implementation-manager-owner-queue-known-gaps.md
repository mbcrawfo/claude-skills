# TD-0010: implementation-manager owner-queue known gaps

## Problem

The final review of the owner queue (implementation-manager 0.8.0) left these gaps in place. Paths are relative to `plugins/implementation-manager/`.

Code:

- **One ref scope, built twice.** [`scripts/items.py:124`](../../plugins/implementation-manager/scripts/items.py) (`_validate_new`) and `scripts/items.py:157` (`add_item`) each build the unit's ref-to-item map for its current generation. Impact: duplicated logic that can drift if the scope rule changes.
- **A missing body file shows as an empty body.** `scripts/items.py:98` (`current_text`) prints the header and nothing below it when `items/<id>.md` is gone. Impact: the owner sees an item with no text and no hint that its file is missing.
- **`depends` is not deduplicated.** `scripts/items.py:158`: `--depends Q1 Q1` stores the id twice and the header shows it twice. Impact: cosmetic.
- **Single resume slot.** `scripts/items.py:267` and [`ARCHITECTURE.md:65`](../../plugins/implementation-manager/ARCHITECTURE.md) (documented): a second unpark while a resume item is set displaces the first one back to arrival order. Impact: after two "back to" moves in a row, the first displaced item can come later than the owner expects.
- **An unreachable resume clear.** `scripts/items.py:271` (`unpark_item`): the item being unparked is never the resume item, since `park_item` clears the resume slot when it parks it. Impact: dead code.
- **`item drop` and `stop` print no queue state.** [`scripts/run.py:568`](../../plugins/implementation-manager/scripts/run.py) prints nothing and `scripts/run.py:641` prints only `dropped <id>` lines: neither prints the new current item or `forward due` lines, unlike `item done` and `item park`. [`SKILL.md:50`](../../plugins/implementation-manager/skills/implementation-manager/SKILL.md) has the manager run `status` after them. Impact: one extra command per drop; a manager that skips it misses a `forward due`.
- **Duplicated id formatting.** `scripts/run.py:270` (`_item_name`) repeats the `<id> (<ref>)` formatting of `scripts/items.py:31` (`header`). Impact: the two can drift.
- **`--depends` and `--with` take `nargs="+"`.** `scripts/run.py:426` and `scripts/run.py:436`: a note or other positional placed after them is read as more ids. It is refused at run time (unknown ref or id), not by the parser. Impact: a confusing refusal for a misordered command line.
- **A unit named `run`.** `scripts/run.py:259` labels run-wide items `run` (`scripts/items.py:22`) in the `status` counts, the same label as a unit named `run`. Impact: the counts merge the two groups for that one unit name.

Tests:

- **Loose refusal regexes.** `tests/test_items.py:190` (`test_refusals`): patterns such as `run-wide`, `ref`, `kind` and `depends` match more than one refusal message. Impact: a case can pass on the wrong refusal.
- **No true chain in the done/drop parked-parent tests.** `tests/test_items.py:573` and `tests/test_items.py:578`: `park_item` parks a-2 and a-3 both with a-1, so `_release` walks one level only. `tests/test_items.py:496` (`test_unpark_returns_a_chain`) covers a real chain through `_release`. Impact: low; the shared `_release` is covered once.
- **`setUp()` inside `subTest`.** `tests/test_items.py:591` (`test_closing_the_resume_item_clears_resume`) calls `self.setUp()` for each sub-test, which registers a new temporary folder cleanup each time. Impact: works, but an unusual pattern that a later change to `setUp` can break.

Skill text (`skills/implementation-manager/SKILL.md`):

- **Headerless items and `[blocked]` disagree.** `SKILL.md:271` (Splitting: an item with no header blocks unless it says "blocks merge: no") and `SKILL.md:248` (a `[blocked]` message blocks only when it says "blocks merge: yes") give opposite defaults for an unmarked `[blocked]` message. Impact: the manager has to pick one, and two managers can pick differently.
- **The Showing count needs a `status`.** `SKILL.md:272`: `item add` prints no per-unit counts, so the `<N> more waiting: <unit> ×<n>` line needs a `run.py status` after it. Impact: one extra command per shown item.
- **`specs` findings and `--blocks`.** `SKILL.md:312` files the `specs` unit's escalated findings as items but leaves `--blocks yes` to the general Splitting rule at `SKILL.md:271`. Impact: an indirect rule the manager can miss.
- **The deferring red flag says "question".** `SKILL.md:354` still says to give outside findings to the owner "as a question", not as an item. Impact: wording only; the owner-queue rules still file it as an item.
- **The tracker's `Parked:` template differs from the CLI.** `SKILL.md:331` (`<id> (<summary>), <note>, since <HH:MM>`) differs from `status`'s `- parked:` line at `scripts/run.py:265` (`<id> (<ref>) <summary>, <note>, since <HH:MM>`). Impact: the manager reformats the line, or shows it in a slightly different shape.

Eval (`evals/owner-queue/`):

- **The done-ran grader's regex.** `evals/owner-queue/graders/done-ran.md:4` matches only when the item id follows the run path directly (`item done <run> u1-1`). Impact: a valid call with options before the id fails the grader.
- **No check against a forward in the reply text.** No grader fails a run that "sends" the answer to the child only in its reply text before `forward due`. Impact: that regression goes unnoticed.
- **The prompt forbids the watcher.** `evals/owner-queue/prompt.md:7` tells the manager not to start `run.py wait`. Impact: the case does not test the "restart the watcher" rule.
- **One run so far.** The case has run once (`--runs 1 --ablation none`). Impact: no variance data and no baseline; run the default three runs before release.
- **The eval command is verified on one case.** `ARCHITECTURE.md:153` documents the eval command, verified only on `owner-queue`. Impact: `push-gate` and `push-queue` may need other options.

## Why it exists

Each one is low impact and was ruled "can stay" in the final branch review, to keep the 0.8.0 change set focused on the queue's behavior.

## Impact

Low overall. None of these makes an item reach the wrong child, merges a PR early, or loses an owner answer. The costs are extra commands, cosmetic output, duplicated code and test or eval coverage gaps.

## Proposed fix

- Code: build the ref scope once and pass it from `_validate_new` to `add_item`; show `(body file missing)` in `current_text`; deduplicate `depends`; drop the unreachable resume clear; have `item drop` and `stop` print the new current header and `forward due` lines like `item done`; use one id formatter from `items`; reserve the unit name `run`; reject positionals after `--depends`/`--with` at parse time or document the order.
- Tests: anchor each refusal regex to its full message; add a three-level chain for done/drop of a parked parent; build a fresh state per sub-test with a helper rather than `setUp()`.
- Skill: settle one default for an unmarked `[blocked]` message; have `item add` print the per-unit count; say `--blocks yes` in the `specs` rule; change the red flag to "file it as an item"; match the `Parked:` template to the CLI line.
- Eval: loosen the done-ran regex; add a grader that fails a forward in the reply text; run the default three runs; verify the eval command on every case.

## Trigger

The next change to `items.py`, the owner-queue rules in `SKILL.md` or the `owner-queue` eval, or a release of implementation-manager.
