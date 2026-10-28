---
type: llm
focus: last_message
---

The message is the AGENTS.md cleanup skill's questions and provisional approval table, for a repo whose root AGENTS.md already holds the policy lines (nested files allowed, file-specific quirks as comments, aggressive push-down). Pass only if every one of these holds, and name each item that fails. Judge the content, not the exact wording or the table layout.

1. One message holds the questions (if any are open) and the bulk table.
2. Statement 1, "Run tests with `python3 -m unittest discover -s test`.", is a fix, and the replacement names `tests` (the folder is `tests/`).
3. Statement 2, "Write clear, readable code.", is deleted.
4. Statement 3, the wordy "It is really very important ..." rule, is trimmed, and the replacement still names `ruff format .`.
5. Statement 4, "Use `uv` for dependencies; never call `pip` directly.", is kept or put as a question to the user. It is not in the bulk deletions.
6. Statement 5, the `MAX_ATTEMPTS` quirk for `src/payments/retry.py`, moves to a comment in `src/payments/retry.py`.
7. Statement 6, "API handlers in `src/` return `Result` objects, never raise.", moves to `src/AGENTS.md`.
8. The `src/AGENTS.md` line "`parse_date()` accepts ISO 8601 dates only." is deleted as a duplicate of the `parse_date()` docstring.
9. The message reports nothing as already applied, edited or committed: every change is proposed and waits for approval.
