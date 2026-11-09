# TD-0013: The recorded check path goes stale when branch-review updates

## Problem

`run.py check-cmd` records the absolute path of branch-review's `rs` script and its state folder at setup, for `wait --full`. The path is inside the installed plugin's versioned folder. If branch-review updates in the middle of a run, the path points to a folder that is gone. `wait --full` then prints `verdict <sha> unavailable: <reason>` for each push or merge event, and each event costs a fallback `branch-review:check` call until the manager records the path again.

## Why it exists

The script path has no stable alias, and recording it once keeps the manager from looking for it at each event.

## Impact

Low. The fallback gives the same answer. Each event costs one extra skill call until the manager re-records the path.

## Proposed fix

Have `wait --full` find the script itself when the recorded path is missing, for example from the plugin root. Or have the manager record the path again when it first sees `unavailable`.

## Trigger

Repeated `verdict ... unavailable` lines in a run that started with a valid recorded path.
