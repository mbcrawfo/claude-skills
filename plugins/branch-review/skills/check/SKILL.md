---
name: check
description: Use when an agent, often from another plugin, needs to know whether a commit is covered by a clean branch-review before a push, PR or merge. Reports the verdict only; it fixes nothing and starts no review.
argument-hint: "--repo PATH [--commit SHA] [--state-dir PATH]"
---

# Branch review check

Answers one question: has a clean `/branch-review:review` seen everything in this commit? It reads the review state that `/branch-review:review` keeps and prints a verdict. It fixes nothing, records nothing and starts no review.

The state script runs through the `rs` wrapper, by its full path. Write the command out in full; never put it in a shell variable, since variables do not survive between Bash calls and zsh does not split them into words:

```
"${CLAUDE_SKILL_DIR}/../../scripts/rs" covered --repo "<repo>" --commit "<sha>"
```

- The wrapper adds `--state-dir "$CLAUDE_PLUGIN_DATA"`. Add `--state-dir "<path>"` yourself only when one was given (the same directory the review ran with). If the command fails with `error: CLAUDE_PLUGIN_DATA is not set; pass --state-dir`, stop and report it. Never substitute a directory of your own.
- `<repo>` is the `--repo` value, the path of the repository checkout to check. Without one, use the output of `git rev-parse --show-toplevel` in the current directory; if that fails, report it and stop.
- `<sha>` is the `--commit` value, any ref that names a commit. Without one, leave `--commit "<sha>"` off the command and the script checks `HEAD` of `<repo>`.

## Run it

1. Run the command above.
2. The command prints one JSON object. Print it verbatim.
3. Then print one line: `covered` when `covered` is true, else `not covered: ` followed by the `reasons` joined by `, `.

The command exits 0 whether or not the commit is covered, so read `covered` from the JSON, never from the exit code. On exit 1 it prints one line on stderr and no JSON (for example `commit not found: <sha>`): report that line and stop. Do not retry with other arguments.

## Reading the verdict

`reasons` are listed in this order, and each stands on its own:

- `no-rounds`: no review state, or no recorded round, for this branch. It can also mean the wrong state directory: pass the same `--state-dir` the review used (or none, when the review used the plugin data dir).
- `open-findings`: a finding is open or still open.
- `unverified-fixes`: a finding was fixed but no later round has verified it.
- `escalated`: a finding is waiting on a decision.
- `partial-scope`: the latest deep round, or a later one, was limited with `--paths`.
- `snapshot-missing`: the reviewed snapshot is gone from the repo.
- `history-rewritten`: the reviewed head is not an ancestor of the commit.
- `base-conflict`: the base branch was merged in and that merge could not be simulated cleanly, or the base ref does not resolve.
- `changes-since-review`: the commit's tree differs from what was reviewed, allowing for a clean base merge.
- `second-opinion-pending`: a second deep review is due and has not run. The review's risk signals (a deep round rated `moderate` or `high` merge risk, or a critical or major finding) or the invoker's `--second-opinion` call for it, unless a review run passed `--no-second-opinion`. A branch whose rounds all predate v0.8 is never pending.

`counts` is the number of findings per status and `rounds` the number of recorded rounds. Do not fix any reason yourself; the caller decides what to do, which usually means running `/branch-review:review` again.
