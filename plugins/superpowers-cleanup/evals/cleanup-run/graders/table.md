---
type: llm
focus: last_message
---

The message is the cleanup skill's questions and provisional approval table for the request "remove specs older than two weeks". Pass only if every one of these holds, and name each item that fails:

1. One message holds both numbered questions and the approval table.
2. A question asks whether specs and plans that stay may keep links to removed files (the repo states no policy), and it gives a recommended answer.
3. *Files to remove* lists `2020-01-15-retry-design.md` and its plan `2020-01-16-retry.md`.
4. *Needs your confirmation* lists the export spec (`2020-01-20-export-design.md`) or its plan (`2020-01-21-export.md`), or both, with a reason naming the missing `src/export.py`. Neither is in *Files to remove*.
5. The research folder `2020-01-10-upstream-limits` is kept, with a reason saying the search spec still cites it.
6. Nothing dated today (the search spec and plan) is listed for removal.
7. *Reference fixes* has an inline fix for the comment in `src/client.py`. Its replacement text gives the upstream's 5-requests-per-second burst limit as the reason for the 2, 4, 8 backoff. There is also a remove fix for the comment in `src/util.py`.
8. The search spec's reference to the retry spec appears as a row tagged with the policy question's number.
