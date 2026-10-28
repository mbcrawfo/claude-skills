# TD-0004: The agents-md-cleanup block splitter misreads three rare markdown shapes

## Problem

`split_blocks` and `find_guidelines` in [`agents_md_inventory.py`](../../plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py) handle the markdown agents write, not all of CommonMark. Three known shapes come out wrong:

- **An unclosed `<!--`** runs to the end of the file. `_parse` extends a comment block until a line holding `-->`, so `<!-- note\n\n- a\n\n- b\n` is one `comment` block covering lines 1-5, and the statements after it are never judged on their own.
- **A fence indented 4 or more spaces inside a list item** (the code of a nested item) is not seen as a fence: `_FENCE` allows at most 3 leading spaces, and `_list_item_end` only skips fences it recognises. Lines inside it are read as markdown, so a `# comment` line in the code becomes a heading, and the closing fence becomes a stray paragraph.
- **A guidelines heading that wraps the name in backticks** (`` ## `AGENTS.md` guidelines ``) doesn't match: `find_guidelines` looks for the plain substring `agents.md guidelines` in the heading title, so the run is treated as a first run and the policy questions are asked again.

## Why it exists

Each was found by a task review and parked: they need malformed or unusual files, and a full CommonMark parser is out of scope for a stdlib-only script. Blocks are only addresses for verdicts, and every edit is shown before approval.

## Impact

Low. A swallowed or split block gets a coarser or odder verdict, which the user sees in the approval table. The backtick heading costs a repeated set of policy questions on every run, and the skill may draft a second guidelines section.

## Proposed fix

- End an unclosed comment at the first blank line (or treat `<!--` without a closing `-->` as a paragraph), with a test that the following list items stay separate blocks.
- In `_list_item_end`, recognise a fence indented up to the item's content indent plus 3, and skip to its end.
- In `find_guidelines`, strip backticks (and other inline markup) from the title before matching, with a test for `` ## `AGENTS.md` guidelines ``.

Add each case to `plugins/maintenance/tests/test_agents_md_blocks.py` or `test_agents_md_inventory.py`.

## Trigger

A user report of a file split oddly or a guidelines section not found, or the next change to the block splitter.
