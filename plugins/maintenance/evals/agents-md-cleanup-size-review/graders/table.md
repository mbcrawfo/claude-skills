---
type: llm
focus: last_message
---

The message is the AGENTS.md cleanup skill's questions and provisional approval table, for a repo whose root AGENTS.md already holds the policy lines (nested files allowed, file-specific quirks as comments, aggressive push-down). The run was told "no size limits", so no limits are in force. Pass only if every one of these holds, and name each item that fails. Judge the content, not the exact wording or the table layout.

1. One message holds the questions (if any are open) and the bulk table.
2. Statement 1, "Run tests with `python3 -m unittest discover -s test`.", is a fix, and the replacement names `tests` (the folder is `tests/`).
3. Statement 2, "Write clear, readable code.", is deleted.
4. Statement 3, the wordy "It is really very important ..." rule, is trimmed, and the replacement still names `ruff format .`.
5. Statement 4, "Use `uv` for dependencies; never call `pip` directly.", is kept or put as a question to the user. It is not in the bulk deletions.
6. Statement 5, the `MAX_ATTEMPTS` quirk for `src/payments/retry.py`, moves to a comment in `src/payments/retry.py`.
7. Statement 6, "API handlers in `src/` return `Result` objects, never raise.", moves to `src/AGENTS.md`.
8. The `src/AGENTS.md` line "`parse_date()` accepts ISO 8601 dates only." is deleted as a duplicate of the `parse_date()` docstring.
9. The message reports nothing as already applied, edited or committed: every change is proposed and waits for approval.
10. Statement 7, the long payment-retry bullet, is split so that its four rules stand as separate units, never merged back into one: keep `MAX_ATTEMPTS` at 3, log every retry through `log_retry`, never retry a `ValueError`, and add a test in `tests/test_retry.py` for a new retry reason. Each unit names its subject. A unit may be a bullet that stays in AGENTS.md or a comment in the code file it concerns (the guidelines section says file-specific quirks go in comments); either counts. No clause of the rules is lost.
11. The explanatory sentences of statement 7 (how the retry loop backs off, why the processor bans clients) are proposed for `docs/architecture.md` under its `Retry` heading, not dropped. A one-line pointer left in AGENTS.md is optional; the message may say none stays.
12. Statement 8, "The retry loop sleeps between attempts, doubling the wait each time.", is deleted (or trimmed to the pointer) as a duplicate of what `docs/architecture.md` already says under `Retry`.
13. The message proposes the line `Size limits: none.` for the guidelines section, and does not offer or propose a size test.
