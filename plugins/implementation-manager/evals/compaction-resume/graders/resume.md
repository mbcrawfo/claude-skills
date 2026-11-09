---
type: llm
focus: trace
---

The session starts as a compacted one: the agent is the implementation manager for the run in `run`, with no memory of its earlier work. The skill's resume rule says to read `run/notes.md` (including its `## Decisions` section) and run `run.py status`, and never to re-run Setup.

Pass only if the trace shows the agent read `run/notes.md` and ran `run.py status` before its first `run.py go`, `redo`, `queue` or `hold` command, and never ran `run.py init` or `run.py unit`. Fail if any of those holds is false. Name which one.
