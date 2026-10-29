# branch-review architecture

How the review loop, its state script and its helper skills work. This page is for contributors; users start at the [README](README.md).

The skills drive `scripts/review_state.py`, the state script. Run `python3 scripts/review_state.py --help` (or `<command> --help`) for its commands and options.

## The review loop

- **Set up.** The skill runs the state script through its `rs` wrapper, which fills in the state directory and the repo. It prunes old state, then locates this branch's earlier recorded rounds. The first review is deep. A later run on a branch that builds on an earlier round is a follow-up, including after the base branch was merged in cleanly (`base-updated`: the delta excludes the base's code). When the base was merged in with conflicts, the next round is a **merge-check**: it checks each conflict resolution, the files both sides changed, and the branch's own work since the last round, instead of re-reviewing the whole change. These force a deep review instead, with open findings carried forward for the reviewer to verify:
  - `snapshot-missing` or `history-rewritten`: a rebase, amend or reset that loses the earlier snapshot or rewrites history;
  - `base-moved` with a deep mode: the base was merged in with conflicts that could not be listed (git older than 2.40);
  - `paths-changed`: `--paths` differs from the earlier round's.

  A round whose reviewer never reported is not recorded and never anchors a follow-up, and a round with no changes is never saved. Every run, including one after a merge or a redo, starts from set-up and ends with the summary.
- **Round.** The branch's working tree is snapshotted, and a fresh `branch-review:reviewer` agent reviews the change set against the intent, or verifies the earlier findings and the new delta in a follow-up. The reviewer writes its report to a file outside the repo, which is recorded as written.
- **Model choice.** `--model` overrides everything below. A deep, second-opinion or merge-check round runs on `opus`; a follow-up runs on `sonnet`. A follow-up whose delta reaches 40 non-generated files or 2,000 changed lines becomes a deep round over the whole change set, on `opus`. A follow-up that verifies a claimed fix of a finding the developer resolved with `--decision fix` stays a follow-up but runs on `opus` (`model_reason: decided-fix`).
- **Record and triage.** The reviewer's findings are recorded. For each open one, the orchestrating agent checks the claim against the code and records what it checked, then fixes it with a regression test shown to fail without the fix, disputes it with evidence, or escalates it. A finding whose fix is a choice for the developer is always escalated, unless the intent settles it. A finding with fix options cannot be marked fixed without the developer's decision or a quote from the intent. Findings outside the change get the status `outside` and stay out of the fix loop, unless the fix is trivial and in changed files or the developer asks for it; when the repo's `AGENTS.md` forbids deferring findings, they are escalated instead.
- **Second opinion.** Once no finding is open, the state script decides whether a second deep round is due: it is when a deep round rated the merge risk `moderate` or `high`, or the review found a critical or major issue. A fresh reviewer then reviews the whole change set once more, putting first the steps a first pass most often skips. It runs in place of the follow-up that claimed fixes would otherwise get, and verifies them. It also runs in place of a merge-check, and then checks each conflict resolution against both sides as well. It waits while a finding is escalated, since the developer's answer may add code, and runs once the decisions are recorded. Each branch gets at most one automatic second opinion; `--second-opinion` asks for one and `--no-second-opinion` skips it. Branches whose rounds all predate v0.8 get one only when `--second-opinion` asks.
- **Repeat.** Each round gets a new reviewer. The loop continues while findings are open, fixes are unverified or a second opinion is due, and stops when the state script reports convergence (or `awaiting-decisions`). It stops after six rounds, counted from the start of the run or the last forced deep review, whichever is later. At the cap, one more follow-up may run to verify claimed fixes, and a due second opinion still runs.

Run the review from the agent that owns the whole change. An implementer working one slice of a larger plan hands the review to its orchestrator.

## Summary and review notes

The summary's header names the round count, the last round and the merge risk, and the next line lists every round with its mode and model (`Rounds: 1 deep (opus), 2 deep (second opinion) (opus), 3 followup (sonnet)`). Then it lists counts, the findings still open or unverified, an intent check (claims the code does not keep, and behavior the intent does not mention, from the latest deep round on), what the latest deep round could not check, then the escalated findings, behavior changes for the developer to confirm, and the deferred, accepted, invalid, outside-the-change and verified findings. Recorded rounds appear as `<n> recorded` (no model), with a "Recorded without review" section listing each round's commit and reason. The state script rewrites the summary after every change to the review state.

Beside it, `review-notes.md` holds the generated "Review notes" block: a line of round counts, models and risk, the non-empty sections, and the fixed findings by title. The agent that opens the PR puts that block in the PR description as it is.

## Passing text to the state script

Notes, discussion entries and decisions reach the state script through files (`--note-file`, `--text-file`) rather than quoted shell arguments, since reviewer text can contain quotes, `$` and backticks.

An agent may record the developer's decision directly with the state script's `resolve` command; it takes effect at the next review run, which must still run before a push.

## State

State lives in `<state dir>/<owner__repo>/<branch>/`. Slashes in the branch name become `__`, and every path segment of the origin URL is kept, so a GitLab subgroup repo is `group__sub__repo`. A repo without an origin URL gets a `local-<hash>` key that its linked worktrees share. A detached HEAD is `detached-<sha7>`.

Claude Code writes the plugin data path into a skill's text when it loads it, but does not set `CLAUDE_PLUGIN_DATA` in Bash shells, so the skills prefix every `rs` call with `CLAUDE_PLUGIN_DATA="<that path>"`. `--state-dir PATH` replaces the plugin data directory with an absolute path; it is meant for test harnesses, and `/branch-review:cleanup` needs the same value. The state script refuses an empty or relative `--state-dir`, so an unset `CLAUDE_PLUGIN_DATA` is an error rather than the current directory; it also refuses the filesystem root and the home directory itself.

### Pruning and cleanup

Every review run prunes stale state first. It removes the state of branches that exist neither locally nor as `origin/<branch>`, and state untouched for more than 30 days. Detached-HEAD state matches no branch, so it goes only by age. It never removes the current branch's state.

Pruning and cleanup only remove `<repo>/<branch>` directories that hold `review.json` or `rounds/`. Any other directory under the state dir, and any symlink (including a symlinked repo directory, which is never walked into), is reported as `skipped` and left alone. A directory that cannot be checked or removed is reported under `errors` without stopping the rest, and the command then exits 1.

## `check`

`branch-review:check` prints the JSON verdict and one line, `covered` or `not covered: <reasons>`, and changes no refs, index or files. A `recorded` round anchors coverage like a reviewed round. A commit is covered only when none of these hold:

- No recorded round exists for the branch.
- A finding is open or still open, fixed but not yet verified, or escalated and awaiting a decision.
- A round from the latest back to the most recent deep round was limited with `--paths`, so part of the change went unreviewed.
- The reviewed snapshot is missing from the repo, or the reviewed head is not an ancestor of the commit (a rebase, amend or reset rewrote history).
- The base branch was merged in and the merge could not be simulated cleanly, or the base ref does not resolve, so the reviewed code cannot be compared to the commit.
- The commit's tree differs from what was reviewed. A clean merge of the base branch since the review does not count as a difference.
- A second opinion is due and has not run yet. Branches whose rounds all predate v0.8 are held back only when `--second-opinion` asked for one.

The reasons, in the same order, are `no-rounds`, `open-findings`, `unverified-fixes`, `escalated`, `partial-scope`, `snapshot-missing`, `history-rewritten`, `base-conflict`, `changes-since-review` and `second-opinion-pending`. The JSON also carries `second_opinion_held` (a second opinion waits for escalated findings) and `behavior_to_confirm` (a count of behavior changes for the developer to confirm); neither changes the verdict. The command exits 0 whether or not the commit is covered, and exits 1 with one line on stderr on an error such as an unknown commit.

## `record`

`branch-review:record` accepts only a commit already on `origin/<branch>` (equal to it or an ancestor), so it cannot bless local work; the agent fetches the branch first. `--base` sets the base ref the branch's review state uses from then on, as `/branch-review:review --base` does. It prints the JSON `{"recorded", "round", "commit", "branch", "files", "dir"}`; `recorded: false` means the commit is already the head of the latest round. It refuses a detached HEAD, a missing `origin/<branch>` ref, a commit not on that ref and an empty reason (exit 1, one line on stderr).

A recorded round never counts as review: it does not count toward the six-round cap and neither satisfies nor triggers a second opinion. The skill is hidden from the `/` menu (`user-invocable: false`), and its description tells the model to use it only on the integration-branch protocol's instruction, never at a user's request.

## Tests and evals

```bash
python3 -m unittest discover -s plugins/branch-review/tests -v
```

The review-loop eval lives in `evals/review-loop/`. Its scaffold script builds a small Python repo with four planted defects in `repo/` inside the run's empty workspace, plus an empty `state/` directory next to it, so the run needs `--scaffold`. The eval sandbox confines writes to the workspace, so the prompt tells the agent to pass `--state-dir` pointing at that `state/` directory. The agent writes the review summary to `review-summary.md` in the workspace, which the `loop` grader reads. Run it from this plugin's directory, with a credential the sandbox can use (`claude setup-token` and `CLAUDE_CODE_OAUTH_TOKEN`, since the sandbox cannot read the Keychain login):

```bash
claude plugin eval . --case review-loop --runs 1 --scaffold --allow-tools Bash Write Edit SendMessage ToolSearch
```

`tests/fixture/make_fixture.sh <dir>` builds the same repo for a manual run.

Results go to `evals/results/` (under the plugin, or under the repo root when the eval is run from there); both are gitignored.
