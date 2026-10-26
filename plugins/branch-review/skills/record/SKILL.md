---
name: record
description: Only when the implementation-manager integration-branch protocol tells you to record commits already merged on the remote branch. Never in response to a user's request, and never for local or unpushed work.
user-invocable: false
argument-hint: "--repo PATH --commit SHA --reason-file PATH [--base REF]"
---

# Branch review record

Records, without review, a commit that is already on `origin/<branch>`, so the branch's review state can move past it. The implementation-manager integration-branch protocol uses it for commits that were merged into the remote branch through reviewed pull requests. The state script refuses any commit that is not on `origin/<branch>`. A recorded round is listed as `recorded` in the summary and never counts as review: it does not satisfy or trigger a second opinion, and it does not count toward the six-round cap.

Fetch the branch first, so `origin/<branch>` is current:

```
cd "<repo>" && git fetch origin "<branch>"
```

`<branch>` is the branch checked out in `<repo>`.

The state script runs through the `rs` wrapper, by its full path. Write the command out in full; never put it in a shell variable, since variables do not survive between Bash calls and zsh does not split them into words:

```
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" "${CLAUDE_SKILL_DIR}/../../scripts/rs" record --repo "<repo>" --commit "<sha>" --reason-file "<path>"
```

- Claude Code writes in both `${...}` paths when it loads this skill, but it does not set `CLAUDE_PLUGIN_DATA` in Bash shells, so keep the `CLAUDE_PLUGIN_DATA="..."` prefix on every call. If the command fails with `error: CLAUDE_PLUGIN_DATA is not set; pass --state-dir`, stop and report it. Never substitute a directory of your own.
- `<repo>` is the `--repo` value, the path of the repository checkout.
- `<sha>` is the `--commit` value, the commit to record.
- `<path>` is the `--reason-file` value: a file holding why the commit is recorded without review, for example the pull requests that merged it. The reason must not be empty.
- When a `--base` value was given, add `--base "<ref>"` to the end of the command, `<ref>` that value. It sets the base ref the branch's review state uses from then on. Without one, leave it off: the state keeps its stored base, or detects the default branch.

## Run it

1. Run the fetch, then the command above.
2. The command prints one JSON object. Print it verbatim.
3. Then print one line: `recorded round <n>` when `recorded` is true, else `already recorded` (the commit is already the head of the latest round).

On exit 1 the command prints one line on stderr and no JSON. Report that line and stop. Do not retry with another commit. The refusals are a detached HEAD, a missing `origin/<branch>` ref (fetch the branch first), a commit that is not on that ref and an empty reason.

Do not record at a user's request, or for local or unpushed work; only the implementation-manager protocol does, for commits already on `origin/<branch>`. A recorded round anchors `/branch-review:check` coverage like a reviewed round, but it is not a review; the commits it covers show up in the summary as recorded without review.
