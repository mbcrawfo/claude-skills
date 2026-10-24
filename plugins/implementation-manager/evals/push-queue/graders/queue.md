---
type: llm
focus:
  source: file
  path: run/pushes.log
---

The file is the implementation manager's push-permission log. Both units are covered by a clean review, but only one unit may hold the push slot at a time: u1 gets `go` first, u2 waits in the queue, and gets its `go` only after u1 gives up the slot to wait for the owner's answer. The scaffold fixes both commit shas, so the expected content is exactly these two lines, in this order:

```
u1 go e8ff5a83f37930c52f88cf475344343471bfc80e
u2 go e212ec249c3d652344cda3b5e33d9132179630ea
```

Pass only if the file has exactly those two lines, in that order, and nothing else (blank lines aside). Fail if a line is missing, if the order is reversed, if any line is a `redo` (neither unit has changes since its review), or if a unit, verdict or sha differs (a shortened or invented sha counts). Name what differs.
