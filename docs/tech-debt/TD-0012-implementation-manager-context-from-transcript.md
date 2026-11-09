# TD-0012: The context line parses an undocumented transcript layout

## Problem

`run.py status` prints the manager's context size (`- context: <N>k`, and `compact due`). `scripts/context.py` reads the size from the manager's session transcript, `<config>/projects/*/<session id>.jsonl`: the `usage` of the last assistant message. Claude Code does not document that file's layout. If the layout or location changes, the line disappears and the manager never asks the owner to compact.

## Why it exists

No documented interface gives a session its own context size, so the transcript is the only source the script can read. `context.py` returns nothing, rather than a wrong number, when it cannot parse the file.

## Impact

Low. A run without the line still works. It uses more tokens, because the context is never compacted.

## Proposed fix

Switch to a documented source when Claude Code offers one, for example a hook or an environment value with the context size. Until then, a test that reads a real transcript from a current Claude Code release would catch a layout change.

## Trigger

A `status` that prints no `context` line in a long run, or a Claude Code release that changes the transcript format.
