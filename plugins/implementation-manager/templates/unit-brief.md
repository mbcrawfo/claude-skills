1. **Goal:** implement `{{title}}` as specified in `{{spec}}`, delivered as one PR to `{{base}}` on `{{repo}}`. If the repository's PR size rule forces several, they land in order, one at a time, each titled with `(part k of N)` (part 11 says how).

{{gate}}

3. **Start:** {{start}}

4. **Read:** the repository's agent instructions (root and every folder touched), the spec, and the parts of the epic or parent docs it names. Repository rules win over any skill's defaults. Where merged code differs from the spec's assumptions, follow the code and say so in the plan.

5. **Plan and build:** write the plan with superpowers:writing-plans where the repository keeps plans, then implement it, preferably with superpowers:subagent-driven-development, validating every commit and push the way the repository requires. `/branch-review:review` is your review: skip a plan workflow's own final review step, and its per-task code-quality reviews too; keep a per-task check that the task matches the spec, if the workflow has one. Unless the repository requires a full validation of every commit, implementers run the tests that cover what they changed, and you run the full validation once the change is complete, before the review. Run the repository's formatter, if it has one, before validating. Every subagent you start ends your turn and wakes you when it hands back, so a subagent waiting on its own background command costs a turn for nothing. Run your own validation in the foreground too, with the Bash `timeout` at 600000; this overrides any instruction to run long commands in the background. Only a command known to take longer than 10 minutes runs in the background. Dispatch the next task only after the last one hands back. Paste this block, word for word, into every implementer and fixer brief you write:
   > {{subagent_block}}
   A plan workflow's own rulings (superpowers:subagent-driven-development's "Rulings, not stalls") cover implementation choices only. A choice the spec does not settle that changes what a test guarantees, behavior users can see, or the unit's scope is an owner question: send it under part 8 at once, and keep building what it does not touch.

6. **Unit-specific instructions:**

{{facts}}

{{extra}}

{{designed}}

7. **Pull request:** follow the repository's PR rules; open the PR and push only with permission, as the next part says; once the PR is open, turn on Auto-fix with the ccd_pr tools; address every review finding, including ones in a review's body (nitpicks, outside-diff comments), batching fixes into one push. Do not merge: the implementation manager merges once every check passes, no thread is unresolved, and the review covers the head commit, so resolve a thread only after its fix is pushed or after replying with the reason you are not acting on it.

{{parts_8_to_10}}

11. One open PR at a time, scope, and hand-over notes. Build every part in this session's own worktree, on its own branch. While part k's PR is open, you may plan part k+1 but commit nothing for it; start its code only after the manager tells you part k merged, then sync with `{{base}}` first. Never use a second branch, worktree or checkout. A finding whose fix belongs outside this unit's scope is escalated with the review's other escalations. A finding outside the change (status `outside`) stays as the review leaves it and goes in the PR description; the review itself escalates it when the repository's rules forbid deferring review findings. Never record any review finding as new tech debt or a follow-up without the owner's answer. Facts later units need (changed names or ids, shared-code decisions, limits found) go in the app's shared notes folder as `{{name}}-notes.md`, nowhere else.
