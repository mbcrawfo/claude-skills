---
type: llm
focus:
  source: file
  path: run/run.json
---

The file is the implementation manager's run state. The scaffold queued three owner items, in this order: `u1-1` (u1, question "keep or drop the retry", blocks merge), `u2-1` (u2, question "rename the setting") and `u1-2` (u1, question "log level for retries", depends on `u1-1`). `u1-1` was the current item. The owner then answered it with "Keep the retry.", so the manager must have closed it, and the next current item must be `u1-2` (the sticky unit shows u1's items together, ahead of the older `u2-1`). u1 still has an open item, so none of its answers may be forwarded yet.

Look at `items.list`: each entry has an `id` and a `status` (`queued`, `current`, `parked`, `answered`, `forwarded`). Pass only if all of these hold: item `u1-1` is `answered`; item `u1-2` is `current`; item `u2-1` is `queued`; and no item is `forwarded`. Fail otherwise, and name each item whose state differs from this.
