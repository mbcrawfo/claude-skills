---
name: cleanup
description: Use when review state for finished or stale branches should be listed or removed, or when the user asks how much branch-review state exists.
argument-hint: "[--branch | --repo | --all] [--older-than DAYS] [--yes] [--state-dir PATH]"
---

# Branch review cleanup

Lists the review state that `/branch-review:review` keeps, or removes some of it. State lives outside the repo, so it piles up until someone clears it.

Every state command is the same prefix plus a subcommand. Shell variables do not survive between Bash calls, so write the prefix out in full each time:

```
python3 "${CLAUDE_SKILL_DIR}/../../scripts/review_state.py" <command> --state-dir "<state>" ...
```

`<state>` is the `--state-dir` value when the user gave one (the same directory they gave `/branch-review:review`), else `${CLAUDE_PLUGIN_DATA}`. Below, `rs <command>` means this prefix plus `<command>`. Each command prints one JSON object. On failure it exits 1 and prints one line on stderr. The script refuses an empty or relative `--state-dir`; if it reports that and no `--state-dir` was given, `CLAUDE_PLUGIN_DATA` is not set, so stop and report it. Never substitute a directory of your own.

## 1. List

Run `rs list`. Each entry in `reviews` has `repo_key`, `branch`, `rounds`, `open`, `updated` and `dir`, newest first. An entry with an `error` key has a corrupt `review.json`; its `rounds`, `open` and `updated` are null. Show those as `?` and print the error.

With no `--branch`, `--repo` or `--all`, print a table of `repo_key`, `branch`, `rounds`, `open`, `updated` and stop. If `reviews` is empty, say there is no review state.

## 2. Pick what to remove

1. For `--branch` or `--repo`, the current directory must be a git repo. Run `git rev-parse --show-toplevel` and keep the path as `<repo>`. If it fails, report that and stop. Then run `rs locate --repo "<repo>"` and keep `repo_key` and `branch`.
2. Filter the `reviews` from section 1:
   - `--branch`: entries whose `repo_key` and `branch` equal the current ones.
   - `--repo`: entries whose `repo_key` equals the current one.
   - `--all`: every entry.
3. With `--older-than DAYS`, also drop entries whose `updated` is null or less than DAYS days ago. The script treats an unknown age the same way, so it would not remove them anyway.
4. If nothing is left, say there is nothing to remove and stop.
5. Print the remaining entries as the section 1 table, headed "Would remove".

## 3. Confirm and remove

1. Unless `--yes` was given, ask the user to confirm removing exactly those entries, and wait for a clear yes. On anything else, stop and remove nothing.
2. Run `rs cleanup --repo "<repo>" --scope <scope>`, adding `--older-than <DAYS>` when it was given. `<scope>` is `branch`, `repo` or `all` for `--branch`, `--repo` or `--all`. For `--all`, omit `--repo`.
3. Print each path in `removed`, or say nothing was removed if the list is empty. If it differs from the list you showed, say so.
4. If `skipped` is not empty, print those paths: they hold neither `review.json` nor `rounds/`, so the script treats them as something other than review state and leaves them in place. Do not delete them yourself.
