# Reviewer checklist

What the branch-review reviewer hunts for, in order of value. The order comes from an analysis of CodeRabbit's reviews on two public repos, mbcrawfo/SqlSource (C#, a source generator and CLI with heavy docs) and mbcrawfo/vibefun (TypeScript, a compiler). Work the categories top to bottom; spend most of the time on the first four.

Each heading ends with the `category` value the finding gets in the JSON block. Every example below was a real finding the author accepted, unless it says otherwise.

## Severity

- `critical`: security hole, data loss or corruption, or a crash or wrong result on an ordinary input that ships to users.
- `major`: wrong behavior on a realistic input, a promise in the intent the code does not keep, a test that cannot fail for the behavior it claims to cover, or a violation of a repo rule that changes behavior.
- `minor`: real and worth fixing, but low impact: a stale doc, a convention slip with no behavior change, a missing boundary test.

There is no nitpick tier. If it is not worth fixing, it is not reported.

## 1. Logic and correctness bugs with a concrete failing input (`logic`)

The code does the wrong thing for an input you can write down. Look for wrong order of operations (a substitution applied after unify, a cap enforced after the blowup), state that survives cancellation, a race between two signals, off-by-one and bounds reads, comparisons that use `>` where the rule says "or more", null and empty cases, wrong join semantics, 32-bit coercion, timezone and infinity edge cases. Compare every boundary in the code with the boundary stated in the intent, the docs or the tests, and run the code at the boundary value. A logic finding names the input, the actual result and the expected result.

- vibefun PR69, `infer-primitives.ts`: the substitution was applied after the directional `unify`, so a stale type variable let `unifyVar` overwrite an earlier constraint and missing-field errors vanished.
- SqlSource PR59, `PlanExpression.TypeOid`: a bare `'x'::` cast read `_tokens[2]` out of range and surfaced as the wrong diagnostic (SQLSRC200).

## 2. Sibling paths (`sibling-path`)

The same defect, or the same fix still missing, in code next to the change: the other branch of an edited conditional, the twin function that handles the mirror case, a helper the new code calls and inherits a flaw from. When the change fixes something, find every other place with the same shape and check it. When the change calls an existing function, read that function and check that what it returns meets the rules the new code must meet. Fix commits are where this matters most: on vibefun, 7 of 15 Critical findings arrived in a later round, on a sibling of the previous fix.

- vibefun PR73, `typechecker.ts`: after the `let` branch was fixed, `finalSubst` was still not threaded through the single-binding `let rec` branch, so stale schemes leaked into later declarations.
- vibefun PR74, `check-coverage-decrease.ts`: the head-missing case was made to fail closed; its base-missing twin still passed silently and was fixed in the next round.

## 3. Security and hardening (`security`)

Input that reaches a place it should not. Injection through an allowlist, path escape via `..`, fail-open CI and coverage checks, secrets or connection strings reaching logs, missing workflow permissions, cookie attribute rules, unquoted paths in shell hooks, bind mounts escaping an isolation boundary. Write the hostile input and trace it to the sink.

- SqlSource PR43, `PREPARE` type list: the declared-type allowlist accepted `)` and letters, so `int) AS SELECT 1` closed the type list and injected SQL into the generated statement.
- vibefun PR59, `compile.ts`: multi-file output joined a `relative()` path containing `..`, so a package import could write outside the project's output root.

## 4. Tests that prove nothing (`tests`)

A test that would still pass if the code under test were wrong. Assertions that only check a call returned, or returned something not `None`; assertions that pass on the crash path; substring matches that false-pass; fixtures with duplicate keys that silently overwrite; a missing regression test for the fix being reviewed; a missing boundary case for a limit the change introduces. For each test the change adds, ask what wrong implementation it would catch. If the answer is none, say so and show it: break the code in a scratch copy, or name the wrong value that still passes.

- vibefun PR78, property and spec-validation suites: crash paths were counted as passing outcomes, so a test passed when the compiler threw.
- SqlSource PR67, test-case dictionaries: rows built with the indexer let a duplicate key silently overwrite an earlier case; 11 rows were converted to `Add` so duplicates throw.

## 5. Intent versus code (`intent`)

The intent is the PR description: what the change is for, what it does and what it deliberately does not do. Check it in both directions. Every claim in the intent ("rejects X", "keeps Y", "works for Z") needs code that does it; try the claim with an input and report each claim the code does not keep as an `intent` finding, with the input that shows it. Behavior the code adds that the intent does not mention goes in `intent_check.unmentioned_behavior`, and becomes a finding only when it is risky.

- SqlSource PR89, `Orders.sql`: the example promised a count per user, but an inner join dropped users with no orders; a `LEFT JOIN` was needed.
- vibefun PR73, `typechecker.ts`: the PR set out to close the value-restriction hole, but top-level `let mut` still did not enforce VF4018 at all.

## 6. Repo conventions (`conventions`)

Rules the repo writes down in `AGENTS.md` or `CLAUDE.md`, at the root or nested in the directory of the changed file, and in the docs those files point to. Cite the file and quote the rule in `source`. Typical rules: test placement, types to use for a kind of value, keep-lists-in-sync footers, file size limits, naming. Conventions also tell you what not to report: honor any instruction about docs that are records of intent or areas not to review.

- vibefun, many PRs: tests missing from the colocated test file the repo's guidelines require; cited as "As per coding guidelines" in 81 findings.
- SqlSource PR2, PR30, PR76 (declined, a counter-example): CodeRabbit asked for specs to be updated to match code although `docs/superpowers/AGENTS.md` says those specs are records of intent. Reading that rule would have prevented all three.

## 7. Docs the change made stale (`docs`)

Only docs the repo treats as living: README, manuals, architecture docs, `AGENTS.md` itself. Stale cross-references, counts that no longer match, examples that no longer compile, claims about behavior the change altered. Skip docs the repo marks as records of intent (plans, specs, decision records), and skip wording and markdown style.

- SqlSource PR82, `README`: it said provenance fields are excluded from the comparison, but the test compares them.
- vibefun PR66, a docs page: the dependency-injection wiring snippet no longer matched the code after the change.

## 8. CI, build and config correctness (`build-ci`)

Build scripts, project files, CI workflows and container config that do something other than what the change expects. MSBuild item types, localized tool output in greps, missing image pins where the repo pins, retry logic that reuses a failed instance. Check what the tool actually does, not what the config looks like it does.

- SqlSource PR75, Web SDK projects: the SDK puts `*.json` into `Content`, not `None`, so `DependentUpon` was never applied to `.sql.json` sidecars.
- SqlSource PR14, `check-package-install.sh`: the script grepped compiler output for ASCII quotes, which fails under a German or Chinese SDK (`DOTNET_CLI_UI_LANGUAGE=de`).

## Not reported

Explicitly not reported: praise, anything a formatter or linter in the repo would catch, generic dependency-bump advice, docstring coverage, requests to maintain hard-coded line numbers in docs, suggestions that are purely taste.

A repo rule in `AGENTS.md` that requires something (for example a docstring on every public function) is a convention, not docstring coverage; report a violation of it under `conventions` with the rule quoted.

## Outside the change

Pre-existing issues outside the change go in a separate "outside the change" list, never mixed with findings on the change.

A defect in existing code is part of the change when the changed code calls it or depends on it and the change's behavior is wrong because of it; report that as a finding on the change (usually `sibling-path`), at the existing code's file and lines. Pre-existing code that the change neither touches nor relies on goes in `outside_change`.
