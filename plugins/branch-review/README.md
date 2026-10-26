# branch-review

A local, agent-run replacement for CodeRabbit PR reviews that checks a branch before it becomes a pull request. A fresh reviewer agent reviews the whole change set, you fix what it finds, and a new reviewer checks the fixes until the review converges.

## Install

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install branch-review@mbcrawfo-skills
```

## Review a branch

Run `/branch-review:review` from the branch once the whole change is complete:

```
/branch-review:review [--full] [--model NAME] [--base REF] [--intent TEXT | --intent-file PATH] [--paths GLOB...] [--generated GLOB...] [--second-opinion | --no-second-opinion] [--notes-file PATH] [--related-branch REF...] [--resolve ID NOTE]
```

- `--full`: force a deep review of the whole change set, even if earlier rounds exist.
- `--model NAME`: model for the reviewer agents, as an Agent-tool model name (`opus`, `sonnet`, `haiku` or `fable`). Defaults to `opus` for a deep, second-opinion or merge-check round and `sonnet` for a follow-up.
- `--base REF`: the ref the change will merge into, overriding the detected default branch. Later runs reuse the stored base.
- `--intent TEXT` or `--intent-file PATH`: the PR description, meaning what the change is for, what it does and what it deliberately does not do. Later runs reuse the intent stored with the review.
- `--paths GLOB...`: limit the review to matching files, useful for a change set too large to review at once. A directory, or a pattern ending in `/`, means everything under it; `.` means the whole repo. Changing `--paths` between runs forces a deep review, so no file in the new scope goes unreviewed.
- `--generated GLOB...`: extra globs for generated files, on top of those `.gitattributes` marks `linguist-generated` or `-diff`. Generated files are still reviewed, but they do not count toward the size warning or the large-delta rule. The globs are stored with the review and reused by later runs; `--generated .` clears them.
- `--second-opinion`: run a second-opinion deep round once no finding is open (claimed fixes are carried in for verdicts), whatever the risk signals say.
- `--no-second-opinion`: skip the automatic second opinion for this branch. It is stored with the review, so `/branch-review:check` does not wait for one, until a later run passes `--second-opinion`.
- `--notes-file PATH`: notes earlier per-task reviewers left for the whole-branch review. Deep rounds treat them as leads to check. Stored with the review; an empty file clears them.
- `--related-branch REF...`: branches built alongside this one that mirror the change, such as the same feature for another engine. The reviewer compares the mirrored files. Stored with the review; `--related-branch .` clears them.
- `--resolve ID NOTE`: record your decision on an escalated finding. A note that asks for a fix reopens the finding; one that says to fix it later defers it; one that says to keep it as it is accepts it.
- `--state-dir PATH`: keep state in this absolute directory instead of the plugin data directory. Meant for test harnesses; give `/branch-review:cleanup` the same value.

The loop:

- **Set up.** The skill runs the state script through its `rs` wrapper, which fills in the state directory and the repo. It prunes old state, then locates this branch's earlier recorded rounds. The first review is deep; a later run on a branch that builds on an earlier round is a follow-up, including after the base branch was merged in cleanly (`base-updated`: the delta excludes the base's code). When the base was merged in with conflicts, the next round is a **merge-check**: it checks each conflict resolution, the files both sides changed, and the branch's own work since the last round, instead of re-reviewing the whole change. These force a deep review instead, with open findings carried forward for the reviewer to verify:
  - `snapshot-missing` or `history-rewritten`: a rebase, amend or reset that loses the earlier snapshot or rewrites history;
  - `base-moved` with a deep mode: the base was merged in with conflicts that could not be listed (git older than 2.40);
  - `paths-changed`: `--paths` differs from the earlier round's.

  A round whose reviewer never reported is not recorded and never anchors a follow-up, and a round with no changes is never saved. Every run, including one after a merge or a redo, starts from set-up and ends with the summary.
- **Round.** The branch's working tree is snapshotted, and a fresh `branch-review:reviewer` agent reviews the change set against your intent, or verifies the earlier findings and the new delta in a follow-up. A follow-up whose delta reaches 40 non-generated files or 2,000 changed lines becomes a deep round over the whole change set, with Opus unless `--model` says otherwise. The reviewer writes its report to a file outside the repo, which is recorded as it wrote it.
- **Record and triage.** The reviewer's findings are recorded. For each open one, the orchestrating agent checks the claim against the code and records what it checked, then fixes it with a regression test shown to fail without the fix, disputes it with evidence, or escalates it. A finding whose fix is a choice for the developer is always escalated, unless the intent settles it. Findings outside the change get the status `outside` and stay out of the fix loop, unless the fix is trivial and in changed files or the developer asks for it; when the repo's `AGENTS.md` forbids deferring findings, they are escalated instead.
- **Second opinion.** Once no finding is open, the state script decides whether a second deep round is due, which it is when a deep round rated the merge risk `moderate` or `high` or the review found a critical or major issue. A fresh reviewer, Opus unless `--model` says otherwise, then reviews the whole change set once more, putting first the steps a first pass most often skips. It runs in place of the follow-up that claimed fixes would otherwise get, and verifies them. It waits while a finding is escalated, since the developer's answer may add code, and runs once the decisions are recorded. Each branch gets at most one automatic second opinion; `--second-opinion` asks for one and `--no-second-opinion` skips it.
- **Repeat.** Each round gets a new reviewer. The loop continues while findings are open, fixes are unverified or a second opinion is due, and stops when the state script reports convergence. It stops after six rounds, counted from the start of the run or the last forced deep review, whichever is later. At the cap, one more follow-up may run to verify claimed fixes, and a due second opinion still runs.
- **Report.** A summary's header names the round count, the last round and the merge risk, and the next line lists every round with its mode and model (`Rounds: 1 deep (opus), 2 deep (second opinion) (opus), 3 followup (sonnet)`). Then it lists counts, the findings still open or unverified, an intent check (claims the code does not keep, and behavior the intent does not mention, from the latest deep round on), what the latest deep round could not check, then the escalated findings, behavior changes for the developer to confirm, and the deferred, accepted, invalid, outside-the-change and verified findings. The state script rewrites it after every change to the review state. Beside it, `review-notes.md` holds a generated "Review notes" block: a line of round counts, models and risk, the non-empty sections, and the fixed findings by title. Paste that block into the PR description as it is.

Notes, discussion entries and decisions reach the state script through files (`--note-file`, `--text-file`) rather than quoted shell arguments, since reviewer text can contain quotes, `$` and backticks.

An agent may also record the developer's decision directly with the state script's `resolve` command; it takes effect at the next review run, which must still run before a push.

Run the review from the agent that owns the whole change. An implementer working one slice of a larger plan should hand the review to its orchestrator.

## Where state lives

State is kept outside the repo, in the plugin's data directory, which Claude Code resolves to `~/.claude/plugins/data/<plugin-id>/`:

```
<owner__repo>/<branch>/
  review.json    # findings, status and round metadata
  rounds/<N>.md  # each reviewer's report
  summary.md     # the latest summary
  review-notes.md  # the Review notes block to paste into the PR
```

Slashes in the branch name become `__`, and every path segment of the origin URL is kept, so a GitLab subgroup repo is `group__sub__repo`. A repo without an origin URL gets a `local-<hash>` key that its linked worktrees share. A detached HEAD is `detached-<sha7>`.

Every review run prunes stale state first. It removes the state of branches that exist neither locally nor as `origin/<branch>`, and state untouched for more than 30 days. Detached-HEAD state matches no branch, so it goes only by age. It never removes the current branch's state.

Claude Code writes the plugin data path into a skill's text when it loads it, but does not set `CLAUDE_PLUGIN_DATA` in Bash shells, so the skills prefix every `rs` call with `CLAUDE_PLUGIN_DATA="<that path>"`. The state script refuses an empty or relative `--state-dir`, so an unset `CLAUDE_PLUGIN_DATA` is an error rather than the current directory; it also refuses the filesystem root and your home directory itself. Pruning and cleanup only remove `<repo>/<branch>` directories that hold `review.json` or `rounds/`; any other directory under the state dir, and any symlink (including a symlinked repo directory, which is never walked into), is reported as `skipped` and left alone. A directory that cannot be checked or removed is reported under `errors` without stopping the rest, and the command then exits 1.

## Clean up

`/branch-review:cleanup` lists review state, or removes some of it:

```
/branch-review:cleanup [--branch | --repo | --all] [--older-than DAYS] [--yes]
```

With no scope it prints a table of what exists. `--branch` and `--repo` target the current branch or repository, `--all` everything. `--older-than DAYS` narrows that scope to state updated more than DAYS days ago; state whose age cannot be read is kept. It shows what it would remove and asks for confirmation unless you pass `--yes`. `--older-than` and `--yes` need a scope, and only one scope may be given.

## For other plugins

Two skills exist for agents in other plugins, such as an implementation manager that cannot find this plugin's script or data directory itself.

### Check a commit

`/branch-review:check` answers whether a clean review covers a commit. It exists for other agents, such as a manager in another plugin that must know a commit was reviewed before a push, PR or merge, and cannot find this plugin's script or data directory itself. It prints the JSON verdict and one line, `covered` or `not covered: <reasons>`, and changes no refs, index or files:

```
/branch-review:check --repo PATH [--commit SHA] [--state-dir PATH]
```

`--commit` defaults to `HEAD` of the repo. A commit is covered only when none of these hold:

- No recorded round exists for the branch.
- A finding is open or still open, fixed but not yet verified, or escalated and awaiting a decision.
- A round from the latest back to the most recent deep round was limited with `--paths`, so part of the change went unreviewed.
- The reviewed snapshot is missing from the repo, or the reviewed head is not an ancestor of the commit (a rebase, amend or reset rewrote history).
- The base branch was merged in and the merge could not be simulated cleanly, or the base ref does not resolve, so the reviewed code cannot be compared to the commit.
- The commit's tree differs from what was reviewed. A clean merge of the base branch since the review does not count as a difference.
- A second opinion is due and has not run yet. Branches whose rounds all predate v0.8 are held back only when `--second-opinion` asked for one.

The JSON also carries `second_opinion_held` (a second opinion waits for escalated findings) and `behavior_to_confirm` (a count of behavior changes for the developer to confirm); neither changes the verdict. The reasons, in the same order, are `no-rounds`, `open-findings`, `unverified-fixes`, `escalated`, `partial-scope`, `snapshot-missing`, `history-rewritten`, `base-conflict`, `changes-since-review` and `second-opinion-pending`. The command exits 0 whether or not the commit is covered, and exits 1 with one line on stderr on an error such as an unknown commit.

### Record commits already on the remote branch

`branch-review:record` records a commit without reviewing it, for the implementation-manager integration-branch protocol, where child pull requests are merged into a shared remote branch after their own reviews. It accepts only a commit that is already on `origin/<branch>` (equal to it or an ancestor), so it cannot be used to bless local work; the agent fetches the branch first. It takes the commit and a file with the reason:

```
branch-review:record --repo PATH --commit SHA --reason-file PATH
```

It prints the JSON `{"recorded", "round", "commit", "branch", "files", "dir"}`; `recorded: false` means the commit is already the head of the latest round. It refuses a detached HEAD, a missing `origin/<branch>` ref, a commit not on that ref and an empty reason (exit 1, one line on stderr). A recorded round is listed as `recorded` in the summary and the review notes, and never counts as review: it does not count toward the six-round cap and neither satisfies nor triggers a second opinion. The skill is hidden from the `/` menu (`user-invocable: false`), and its description tells the model to use it only on the protocol's instruction, never at a user's request.

## What's new in 0.10

- New `record` command in the state script and hidden skill `branch-review:record`: record, without review, commits already on `origin/<branch>`. See "For other plugins".
- Recorded rounds appear as `<n> recorded` (no model) in the summary's header and Rounds line, in `review-notes.md`, and in a "Recorded without review" section that is shown only when some exist, with each round's commit and reason.
- Recorded rounds never count toward the six-round cap and never satisfy or trigger a second opinion.

## What's new in 0.9

- Deep reports must account for every checklist category and required step (`coverage`), and list what they could not check (`not_checked`), which the next deep round picks up.
- The reviewer probes driver and database claims on the pinned systems, runs tests that measure process-wide state repeatedly, builds generated output as a consumer would, checks the plan's assumptions and greps for promises the change fulfils.
- A finding with fix options cannot be marked fixed without the developer's decision or a quote from the intent.
- The second opinion runs once nothing is open, and waits for decisions; the loop can stop with `awaiting-decisions`.
- New status `accepted` for a developer's "won't fix", and `confirm-behavior` for behavior changes the developer should confirm.
- `begin-round` takes its own snapshot; `--state-dir` and `--repo` work before the subcommand; the summary is never stale, and `review-notes.md` is ready to paste.

## Review guidance in AGENTS.md

The reviewer reads every `AGENTS.md` and `CLAUDE.md` from the repo root down to each changed file. Put review-specific guidance in the repo's `AGENTS.md`: what the reviewer should not review (generated code, vendored files), and which documents are records of intent rather than documentation to keep current (plans, specs, changelogs).

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
