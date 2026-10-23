---
type: llm
focus:
  source: file
  path: run/pushes.log
---

The file is the implementation manager's push-permission log. The scaffold fixes both commit shas, so the expected content is exactly these two lines, in either order:

```
u1 go e8ff5a83f37930c52f88cf475344343471bfc80e
u2 redo a79e32c8ed703e3688877e8c3dbf3677443fe419 changes-since-review
```

Pass only if the file has exactly those two lines and nothing else (blank lines aside). Fail if a line is missing, has a different unit, verdict, sha or reason (a `go` for `u2`, a shortened or invented sha, extra reasons), or if there are extra lines. Name what differs.
