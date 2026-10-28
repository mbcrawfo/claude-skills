# TD-0005: agents_md_inventory.py breaks its one-error-line contract on usage errors, and reads rulings through symlinks

## Problem

- **Usage errors.** [`agents_md_inventory.py`](../../plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py) promises that on failure it exits 1 with one `error: ...` line. An argparse usage error (a missing `--file`, an unknown `--role`) instead exits 2 and prints a multi-line usage message, because `main` lets argparse's `SystemExit` through. `docs_inventory.py` in `superpowers-cleanup` has the same behaviour, which this script inherited. `test_bad_role_is_a_usage_error` pins the exit 2.
- **Rulings through a symlink.** `_rulings` reads `.claude/agents-md-cleanup.md` with `is_file()` and `read_bytes()`, which follow symlinks. If the rulings file (or `.claude`) is a symlink to a file outside the repo, its content is read into the inventory and passed to every reviewer as `RULINGS`. Every other file the script reads must resolve inside the repo (`_is_inside`).

## Why it exists

Both were found by task reviews and deferred: the skill always calls the script with the arguments it documents, so a usage error means a bug in the skill, and a rulings symlink out of the repo is something the user set up.

## Impact

Low. A usage error's extra lines are still reported, just not in the documented one-line form. Outside rulings content only steers the reviewers (rulings can't change their role or tools), and nothing is written through the link by the script.

## Proposed fix

- Subclass `argparse.ArgumentParser` with an `error()` that raises `CliError`, so a usage error exits 1 with one `error: ...` line; update `test_bad_role_is_a_usage_error`. Consider doing the same in `docs_inventory.py`.
- In `_rulings`, check the path with `_is_inside(root, root / RULINGS_PATH)`, and report a rulings file that resolves outside the repo as `exists: false` with a reason, or fail with an error line.

## Trigger

The next change to the script's CLI, or a report of a confusing usage error or of rulings read from outside a repo.
