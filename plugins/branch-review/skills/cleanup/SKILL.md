---
name: cleanup
description: Use when review state for finished or stale branches should be listed or removed, or when the user asks how much branch-review state exists.
argument-hint: "[--branch | --repo | --all] [--older-than DAYS] [--yes] [--state-dir PATH]"
---

# Branch review cleanup

Lists the review state that `/branch-review:review` keeps, or removes some of it. State lives outside the repo, so it piles up until someone clears it.

Every state command runs through the `rs` wrapper, by its full path, written out in each Bash call:

```
"${CLAUDE_SKILL_DIR}/../../scripts/rs" <command> ...
```

Below, `rs <command>` means this. Never put the path or a command in a shell variable: variables do not survive between Bash calls, and zsh does not split `$RS` into words. The wrapper adds `--state-dir "$CLAUDE_PLUGIN_DATA"`, and `--repo` (the repo's top level) when you give none and the current directory is in a git repo. When the user gave a `--state-dir` (the same directory they gave `/branch-review:review`), and only then, pass `--state-dir "<path>"` to every command. Each command prints one JSON object. On failure it exits 1 and prints one line on stderr. If that line is `error: CLAUDE_PLUGIN_DATA is not set; pass --state-dir`, stop and report it. Never substitute a directory of your own.

Check the arguments first. Each of these is a usage error: report it with the usage line `[--branch | --repo | --all] [--older-than DAYS] [--yes] [--state-dir PATH]` and stop, removing nothing.

- `--older-than` or `--yes` without a scope flag (`--branch`, `--repo` or `--all`).
- More than one scope flag.
- A DAYS that is not a non-negative integer.

## 1. List

Run `rs list`. Each entry in `reviews` has `repo_key`, `branch`, `rounds`, `open`, `updated` and `dir`, newest first. An entry with an `error` key has a corrupt `review.json`; its `rounds`, `open` and `updated` are null. Show those as `?` and print the error.

With no `--branch`, `--repo` or `--all`, print a table of `repo_key`, `branch`, `rounds`, `open`, `updated` and stop. If `reviews` is empty, say there is no review state.

## 2. Pick what to remove

1. For `--branch` or `--repo`, the current directory must be a git repo. Run `git rev-parse --show-toplevel` and keep the path as `<repo-path>`; it is the script's `--repo` argument, not the `--repo` scope flag. If it fails, report that and stop. Then run `rs locate --repo "<repo-path>"` and keep `repo_key` and `branch`. With `--branch`, if `branch` is `HEAD` (a detached HEAD), report that there is no current branch to clean up and stop.
2. Filter the `reviews` from section 1:
   - `--branch`: entries whose `repo_key` and `branch` equal the current ones.
   - `--repo`: entries whose `repo_key` equals the current one.
   - `--all`: every entry.
3. With `--older-than DAYS`, keep only entries updated more than DAYS days ago: drop those whose `updated` is null or DAYS days ago or less. The script uses the same strict rule and treats an unknown age as not old, so it would not remove the dropped entries anyway.
4. If nothing is left, say there is nothing to remove and stop.
5. Print the remaining entries as the section 1 table, headed "Would remove".

## 3. Confirm and remove

1. Unless `--yes` was given, ask the user to confirm removing exactly those entries, and wait for a clear yes. On anything else, stop and remove nothing.
2. Run `rs cleanup --repo "<repo-path>" --scope <scope>`, adding `--older-than <DAYS>` when it was given. `<scope>` is `branch`, `repo` or `all` for `--branch`, `--repo` or `--all`. For `--all`, omit the script's `--repo` argument; the script ignores any `--repo` the wrapper adds.
3. Print each path in `removed`, or say nothing was removed if the list is empty. If it differs from the list you showed, say so. If the command exits 1 after printing its JSON, `errors` lists the directories it could not remove, each with its `dir` and `error`: print them as well. The script keeps going past such a failure, so `removed` is still accurate.
4. If `skipped` is not empty, print those paths: they are symlinks, or hold neither `review.json` nor `rounds/`, so the script treats them as something other than review state and leaves them in place. Do not delete them yourself.
