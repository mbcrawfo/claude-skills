# Superpowers cleanup plugin: design

Date: 2026-10-24. Status: draft for approval.

## Intent

Superpowers writes a spec for every design (`docs/superpowers/specs/`), a plan for every spec it builds (`docs/superpowers/plans/`), and sometimes research notes (`docs/superpowers/research/`). Nothing ever removes them. Over time they pile up, go stale, and leave code comments and docs pointing at files that describe decisions long since settled.

This plugin adds one skill that Michael runs by hand to prune them. He describes what to remove in plain words ("older than two weeks", "specs from Oct 22", "the foobar epic"). The skill finds the matching files and their dependents. It deals with every reference to them, so nothing is left pointing at a missing file and no needed fact is lost. Then it shows one table of everything it will do, and after his approval removes the files, fixes the references and commits.

## Goals

- Never runs unless Michael invokes it by name.
- Selection from a plain-words description: by age, by date, by topic, or a mix.
- Removing a spec removes every plan built from it, and any research that only removed specs cite.
- Every reference to a removed file, anywhere in the repo, is found and gets a decision: inline the fact it depended on, remove the pointer, or ask.
- References between specs, plans and research follow the repo's stated policy when there is one, and Michael's decision when there isn't.
- Work that looks unfinished is never removed without Michael confirming that file.
- One approval covers the whole run. Nothing is deleted or edited before it.
- The result is one commit that holds only the skill's own changes.

## Non-goals

- No automatic or scheduled runs, and no triggering from other skills or plans.
- No archiving (moving files elsewhere instead of deleting). Git history is the archive.
- No pushing, PRs or branch creation.
- No cleanup of anything other than spec, plan and research files: not branch-review state, worktrees or branches.
- No rewriting of specs or plans that stay, beyond fixing their references to removed files.

## Anti-goals

- Deleting a spec or plan for work still in progress without Michael saying yes to that file.
- Losing a fact that code depends on: a comment that pointed at a spec for *why* the code is odd ends up with the why or a question to Michael, never with nothing.
- Dangling links left in living code or docs.
- Sweeping Michael's unrelated working-tree changes into the cleanup commit.
- Asking the same policy question once per link instead of once per run.

## Constraints

- Lives in this marketplace and follows AGENTS.md: plugin in `plugins/superpowers-cleanup/`, an entry in `.claude-plugin/marketplace.json`, kebab-case names, `claude plugin validate` passes for both, and a version bump on every skill change.
- Scripts are Python 3.9+, standard library only, run through `python3`. They are linted and formatted with ruff under the root `pyproject.toml`. Tests live in `plugins/superpowers-cleanup/tests/` and run with `python3 -m unittest discover -s plugins/superpowers-cleanup/tests`.
- The main workflow skill ships an eval case under `plugins/superpowers-cleanup/evals/`. `claude plugin eval` can't run from a worktree session, because a hook blocks the word `eval`. Michael runs it in his own terminal, with `CLAUDE_CODE_OAUTH_TOKEN` exported from `claude setup-token`. Eval cases build their fixtures through a `case.yaml` scaffold script, as `branch-review/evals/review-loop` does, and use no absolute paths or `/tmp`.
- The skill works in any repo that uses superpowers, not just this one. It can't assume `docs/superpowers/`: superpowers lets user preferences move the specs and plans folders.

## Facts the design rests on

- A plan names its spec in its header, on a line like `**Spec:** \`docs/superpowers/specs/2026-10-22-branch-review-skill-design.md\``. Plan and spec filenames don't match: `branch-review-skill-design.md` pairs with `branch-review-plugin.md`. So the pairing comes from that header line.
- Spec and plan filenames start with `YYYY-MM-DD-`. A research entry is a top-level file or folder under the research directory, also date-prefixed, e.g. `research/2026-10-22-coderabbit-analysis/`.
- Plan checkboxes aren't a progress signal. Both plans in this repo were fully built and merged, and all 90 of their steps are still `- [ ]`.
- Claude Code's skill frontmatter `disable-model-invocation: true` hides a skill from the model, so only the user's `/plugin:skill` invocation runs it.

## Approaches

1. **Instructions only.** A single SKILL.md; Claude does discovery, pairing, dates, git checks and reference search with its own tools on every run. Least code. But the mechanical steps get redone from scratch each time, so a missed `**Spec:**` line or a wrong date slips through quietly. The eval would be the only test.
2. **Instructions plus an inventory script (chosen).** A stdlib Python script reports the facts: every file with its dates, git state and links, plus a literal search for references. The skill does the judgment: reading the filter, spotting unfinished work, choosing inline, remove or ask, the approval table and the commit. It mirrors branch-review's split between `review_state.py` and its SKILL.md. The facts get unit tests and the judgment gets the eval.
3. **Script does most of it.** The script also applies filters (`--older-than`, `--date`) and performs deletions and the commit. More predictable, but topic filters still need Claude to read the files. That means two selection paths, and a script that deletes things.

Michael chose 2.

## Decided design

### Packaging

- New plugin `superpowers-cleanup`, starting at version `0.1.0`, with one skill `cleanup`, invoked as `/superpowers-cleanup:cleanup <what to remove>`. *(My call on the names; Michael can rename.)*
- Skill frontmatter: `disable-model-invocation: true`. The `description` says it's for explicit manual invocation only, never on the model's own initiative. `argument-hint` is `<what to remove, in plain words>`.
- The plugin README and the repo README's plugin list get an entry for it.

### The inventory script

`plugins/superpowers-cleanup/scripts/docs_inventory.py`, invoked as `python3 "${CLAUDE_SKILL_DIR}/../../scripts/docs_inventory.py" <command> ...`. Each command prints one JSON object to stdout. On failure it exits 1 and prints one line to stderr. The script never writes, deletes or edits anything.

**`inventory`** takes `--repo PATH`, optional `--specs DIR`, `--plans DIR` and `--research DIR` (defaults `docs/superpowers/{specs,plans,research}`, relative to the repo root), and optional `--base REF`. It reports:

- `base`: the ref it compared against. When `--base` isn't given, it uses the default branch from `origin/HEAD`, falling back to a local `main`, then `master`. If none exists, it says so, and the skill asks Michael for the base.
- `current_branch`: the current branch, or null if HEAD is detached.
- `files`: one entry per spec file, plan file, and top-level research entry (a file or a folder). Each entry has:
  - `kind`: `spec`, `plan` or `research`
  - `path`
  - `filename_date`: the `YYYY-MM-DD` prefix, or null if there isn't one
  - `first_added`: the date of the commit that first added the path, or null if it was never committed
  - `tracked`: whether git tracks the file. For a research folder, this is true only if every file inside is tracked.
  - `dirty`: whether it has uncommitted changes
  - `on_base`: whether the path exists on the base ref with the same content as in the working tree
  - for plans, `spec`: the path from the `**Spec:**` line, or null, plus `spec_exists`
  - for specs, `research`: the research entries the spec mentions by path or by entry name
  - `title`: the first heading
- `unreadable`: files that couldn't be parsed, each with a reason.

**`refs`** takes `--repo PATH` and one or more target paths. For each target, it searches tracked and untracked-but-not-ignored files for three things: the repo-relative path, the bare filename, and the filename stem without its date prefix. The stem catches links written in other forms, like `../specs/x.md` or `[spec](x)`. It returns every match with its `file`, `line`, `text`, `match` (`path`, `filename` or `stem`), and `in_target` (true when the matching file is itself a target). Matches inside a research folder that is a target count as `in_target`.

The script doesn't search for loose prose mentions like "the branch-review spec". That search is the skill's job (below).

### The skill's flow

1. **Read the request.** With no argument, run `inventory`, show a short summary (counts per kind, the oldest and newest dates, and the titles), and ask what to remove. Stop there until Michael answers.
2. **Find the folders.** Check the repo's AGENTS.md and CLAUDE.md files, and any Claude preferences in view, for overrides of the specs and plans locations. Pass any overrides to `inventory`. Run `inventory`.
3. **Select.** Turn Michael's description into a list of candidate files:
   - Dates and ages use `filename_date`, falling back to `first_added`. A file with neither is never matched by a date or age filter; it's listed as "no date" if the filter is date-based.
   - Topic filters ("the foobar epic") match by reading each file's title, goal and content, not just the filename. A loose match is included and marked as loose in the table, so Michael can drop it.
   - Mixed descriptions combine in the obvious way. If a description is genuinely ambiguous, for example "old specs" with no age, ask before selecting.
4. **Add dependents.**
   - Every selected spec brings every plan whose `spec` points at it.
   - A research entry is added when at least one removed spec cites it and no kept spec does. Research that nothing cites is never swept in this way. A research entry cited by a spec that stays is kept, and the table says why. *(My call on the "at least one" wording.)*
   - A filter can also select plans or research directly. A selected plan doesn't bring its spec along.
   - Plans whose `spec` is null or missing are orphans. They're selectable directly, and the table marks them as orphans.
5. **Flag unfinished work.** A candidate looks unfinished if any of these hold:
   - It isn't on the base: `on_base` is false. That covers untracked files, dirty files, and files committed only on this branch.
   - It's a plan that names files, commands or features that don't exist on the base. The skill spot-checks the plan's file paths and key names with `git ls-tree` and `git grep` against the base ref.
   - It's a spec with no plan, and nothing on the base matches what it describes.

   A spec and its plans stand or fall together: an unfinished plan holds back its spec, and a held-back spec holds back all its plans. Confirming a spec confirms its plans. *(Added after the final review.)*

   Flagged files sit in their own group with one line of reasons each. They're kept unless Michael confirms that file. Files with `tracked: false` also get the warning "not in git, deletion is permanent".
6. **Find references.** Run `refs` on every candidate, then do a judgment pass. Grep the repo for each candidate's title and topic words, and read the hits for prose mentions ("see the implementation-manager design"). Skip matches where `in_target` is true.
7. **Decide each reference.**
   - **In living code and docs** (anything that isn't a spec, plan or research file that stays): read what the reference relies on in the target file, then pick one fix:
     - *inline*: a short version of the fact goes into the comment or doc in place of the pointer;
     - *remove*: the pointer goes, because the code or doc already says everything it needs;
     - *ask*: when the skill isn't sure which fix fits, or the inline text would be long.
   - **In a spec, plan or research file that stays:** look for a repo policy on whether these docs are point-in-time snapshots that may hold stale links. Check AGENTS.md and CLAUDE.md (root and nested), the docs they name, and any README in the docs folders.
     - If there's a policy, follow it.
     - If not, ask once for the whole run. If Michael allows stale links, leave those references alone. If he doesn't, treat them like living docs.
     - After a no-policy answer, offer to add a one-line policy to the repo's AGENTS.md, or CLAUDE.md if the repo has no AGENTS.md. Adding it becomes part of this run's commit.
   - Every *ask* and the policy question go in the same message as the approval table (step 8), not in a message before it. A reference fix in a file that already has uncommitted changes is flagged, and the skill asks before touching it, since the commit would sweep those changes in.
8. **Questions and approval table.** One message. It opens with the open questions, numbered and most important first, each with the skill's recommended answer. Then comes the provisional table, in three groups:
   1. *Files to remove*: path, kind, why selected (the filter match, "plan of X", "research cited only by removed specs"), and any warnings.
   2. *Needs your confirmation*: the unfinished files and their reasons. They're kept unless confirmed.
   3. *Reference fixes*: location, the reference, the fix, and the replacement text for inlines.

   A row that depends on a question is filled in using the recommended answer and tagged with that question's number. For example, the row for a kept spec's link to a removed spec shows the fix that applies if Michael doesn't allow stale links, tagged "Q1". Michael answers and approves in one reply. He can also drop items or change fixes.

   If his answers differ from the recommendations, the affected rows change. When nothing else is in doubt, his reply counts as approval of the table with those changes applied. If an answer raises a new question, or changes rows in a way he couldn't have foreseen (for example, a new inline text), the skill shows the revised table and asks again. When a change affects other items, the skill recomputes and shows the table again: dropping a spec keeps its plans and may keep its research. Nothing changes on disk until he approves.
9. **Apply.** Use `git rm -r` for tracked paths and a plain delete for untracked ones. Apply the reference edits and the policy line if accepted.
10. **Commit.**
    - Stage only the paths this run removed or edited.
    - If the current branch is the base, or HEAD is detached, ask before committing.
    - The message follows the repo's own style, read from `git log`; here that's `docs: remove ...`. The body lists the removed files and the edited references, and ends with the session's attribution lines.
    - No push.
    - Report the commit hash and a one-line summary.
11. **Nothing matched:** say what was searched for and how many files were checked, and stop without changes.

### Tests and eval

- Unit tests build temporary git repos (the way branch-review's tests do) and cover:
  - spec–plan pairing from the `**Spec:**` line, including missing and broken lines
  - research citations
  - filename and first-added dates
  - `tracked`, `dirty` and `on_base` for files on the base, on a branch only, dirty and untracked
  - base detection and its fallbacks
  - `refs` match kinds and `in_target`
  - folder-override flags
- Eval case `plugins/superpowers-cleanup/evals/cleanup-run/`: a scaffolded fixture repo on its default branch, holding:
  - an old finished spec and its plan, both on the base, referenced from two code comments. One comment needs a fact that lives only in the spec, so it should be inlined. The other adds nothing, so it should be removed.
  - an old spec whose plan names a file that doesn't exist, so it should be flagged unfinished.
  - a research folder cited by the removed spec and by a spec that stays, so it should be kept.
  - a kept spec that references the removed spec, with no policy in AGENTS.md, so the skill should ask the policy question.
  - newer files that the filter must not touch.

  The prompt invokes `/superpowers-cleanup:cleanup remove specs older than two weeks`. The eval has no user to answer, so graders check that the run stops after a single message holding both the questions (including the policy question) and the provisional table. The table must show the inline fix with its replacement text, the remove fix, the flagged unfinished spec and plan, the kept research with its reason, and the policy-dependent row tagged with its question, and that the working tree and HEAD are unchanged.

## Left to the builder

- Exact JSON field names beyond those above, the CLI's error messages, and the internal structure of the script.
- The wording of SKILL.md, the table layout and the commit-message body.
- How deep step 5's spot-check goes. It should stay cheap: names and paths, not a full read of the base.
- Whether to add a second eval case: a plain "clean up old specs" prompt with no slash command, graded on the skill *not* firing.
- Confirming that `claude plugin eval` runs a prompt that opens with a slash command for a `disable-model-invocation` skill. If it doesn't, stop and tell Michael before redesigning the eval.
