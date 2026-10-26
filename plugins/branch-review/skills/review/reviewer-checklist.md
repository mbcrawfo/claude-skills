# Reviewer checklist

What the branch-review reviewer hunts for, in order of value. The order comes from an analysis of CodeRabbit's reviews on two public repos, mbcrawfo/SqlSource (C#, a source generator and CLI with heavy docs) and mbcrawfo/vibefun (TypeScript, a compiler). Work the categories top to bottom; spend most of the time on the first four, and finish every category even after you find a critical or major.

Each heading ends with the `category` value the finding gets in the JSON block. Most examples below are real findings the author accepted; one that was declined says so. Examples marked `TD-` come from branch-review's own rounds on SqlSource tech-debt branches: defects a first deep pass missed and a later pass found.

## Severity

- `critical`: security hole, data loss or corruption, or a crash or wrong result on an ordinary input that ships to users.
- `major`: wrong behavior on a realistic input, a promise in the intent the code does not keep, a test that cannot fail for the behavior it claims to cover, or a violation of a repo rule that changes behavior. Two cases are always at least major:
  - a wrong result at run time with no diagnostic, on an input the docs or the intent allow;
  - an input that makes a whole unit of output disappear, such as a file's generated members or a module's exports.
- `minor`: real and worth fixing, but low impact: a stale doc, a convention slip with no behavior change, a missing boundary test.

There is no nitpick tier. If it is not worth fixing, it is not reported.

## 1. Logic and correctness bugs with a concrete failing input (`logic`)

The code does the wrong thing for an input you can write down. Look for wrong order of operations (a substitution applied after unify, a cap enforced after the blowup), state that survives cancellation, a race between two signals, off-by-one and bounds reads, an inclusive bound implemented as exclusive or the reverse, null and empty cases, wrong join semantics, 32-bit coercion, timezone and infinity edge cases. Compare every boundary in the code with the boundary stated in the intent, the docs or the tests, and run the code at the boundary value. A logic finding names the input, the actual result and the expected result.

- vibefun PR69, `infer-primitives.ts`: the substitution was applied after the directional `unify`, so a stale type variable let `unifyVar` overwrite an earlier constraint and missing-field errors vanished.
- SqlSource PR59, `PlanExpression.TypeOid`: a bare `'x'::` cast read `_tokens[2]` out of range and surfaced as the wrong diagnostic (SQLSRC200).

**Broad mappings.** When code maps an error code, a prefix, a type or a message to one diagnosis, list everything else that produces the same value, and check that each one gets the right answer. A mapping is only as narrow as the narrowest thing that matches it.

- SqlSource TD-0049, `PostgresLive.cs`: every SQLSTATE `08P01` was read as "the pooler refused a transaction in statement mode". PgBouncer also raises `08P01` for `query_wait_timeout` on a saturated pool in transaction mode, so a user already on the advised setup got advice to switch to it. Matching the statement-mode message text as well fixed it.

**Equivalence classes.** When code compares, normalizes or looks up by text (type names, paths, keys, identifiers), list every spelling of the same thing (the keyword and the framework name, `T?` and `System.Nullable<T>`, a relative and an absolute path, case variants), try each, and put what you tried in the finding's `variants`. The class is one finding, and its `fix` covers every variant that fails.

- SqlSource phase 8.2, `ClrTypeName`: an override written `App.Id<System.Int64>` never matched its converters, which are written `App.Id<long>`, and failed at run time. The fix normalized framework names, and the next round found `System.Nullable<T>` written in full still unmatched: one probe of the class would have found both.

## 2. Sibling paths (`sibling-path`)

The same defect, or the same fix still missing, in code next to the change: the other branch of an edited conditional, the twin function that handles the mirror case, a helper the new code calls and inherits a flaw from. When the change fixes something, find every other place with the same shape and check it. When the change calls an existing function, read that function and check that what it returns meets the rules the new code must meet. Fix commits are where this matters most: on vibefun, 7 of 15 Criticals arrived in round 2 or later, many on a sibling path of the fix.

- vibefun PR73, `typechecker.ts`: after the `let` branch was fixed, `finalSubst` was still not threaded through the single-binding `let rec` branch, so stale schemes leaked into later declarations.
- vibefun PR74, `check-coverage-decrease.ts`: the head-missing case was made to fail closed; its base-missing twin still passed silently and was fixed in the next round.

## 3. Security and hardening (`security`)

Input that reaches a place it should not. Injection through an allowlist, path escape via `..`, fail-open CI and coverage checks, secrets or connection strings reaching logs, missing workflow permissions, cookie attribute rules, unquoted paths in shell hooks, bind mounts escaping an isolation boundary. Write the hostile input and trace it to the sink.

- SqlSource PR43, `PREPARE` type list: the declared-type allowlist accepted `)` and letters, so `int) AS SELECT 1` closed the type list and injected SQL into the generated statement.
- vibefun PR59, `compile.ts`: multi-file output joined a `relative()` path containing `..`, so a package import could write outside the project's output root.

## 4. Tests that prove nothing (`tests`)

A test that would still pass if the code under test were wrong. Assertions that only check a call returned; assertions that pass on the crash path; substring matches that false-pass; fixtures with duplicate keys that silently overwrite; a missing regression test for the fix being reviewed; a missing boundary case for a limit the change introduces. For each test the change adds, ask what wrong implementation it would catch. If the answer is none, say so and show it.

**Break it on purpose.** For each test the change adds or rewrites for a behavior, break the code under test in a `mktemp -d` copy of the repo, never in a `zz_` copy of a module: the existing test never imports such a copy, so it would survive every breakage, and in some languages (C#) a duplicate class breaks the build. Run the test in the copy, and report one finding per weak test, listing the breakages it survives. Break the rule the test exists for: drop a condition, flip a comparison, return the fallback. When running it is impractical, name the wrong implementation the test would miss and say why it could not be run.

- vibefun PR78, property and spec-validation suites: crash paths were counted as passing outcomes, so a test passed when the compiler threw.
- SqlSource PR67, test-case dictionaries: rows built with the indexer let a duplicate key silently overwrite an earlier case; 11 rows were converted to `Add` so duplicates throw.
- SqlSource TD-0051, `NullabilityWalk.Expressions.cs`: a mutated copy that dropped the rule that each side's column of a full-join `COALESCE` must be NOT NULL (`ProvedTypeThrough(...) ?? 23L`) still passed all 5,198 tests, because every full-join case used NOT NULL columns.

**Tests that measure process-wide state.** A test that asserts on something other threads, the runtime or the machine can change: allocation counters, wall-clock time, timeouts, GC counts, process-wide caches, global counters, port or file availability. For each one the change adds or rewrites, name what else can move the measurement (test-platform telemetry, driver background threads, the GC, a loaded CI runner), run it inside the full suite once and alone five times, and report a failure or a spread close to the bound. A breakage it should catch that still passes is a weak test, as above.

- SqlSource phase 8.3, `AllocationTests`: the test summed `GC.GetTotalAllocatedBytes(precise: true)`, which counts every thread, and the test platform's telemetry made it fail once in a full run on PostgreSQL 15. Its SQL Server twin compared medians of paired blocks and held.

## 5. Intent versus code (`intent`)

The intent is the PR description: what the change is for, what it does and what it deliberately does not do. Check it in both directions. Every claim in the intent ("rejects X", "keeps Y", "works for Z") needs code that does it; try the claim with an input. List every claim the code does not keep in `intent_check.unmet_claims`. Each one is also an `intent` finding, with the input that shows it, unless the same defect is already a finding under another category; then it is listed in `unmet_claims` only, so one defect is never reported twice. Behavior the code adds that the intent does not mention goes in `intent_check.unmentioned_behavior`, and becomes a finding only when it is risky.

- SqlSource PR89, `Orders.sql`: the example promised a count per user, but an inner join dropped users with no orders; a `LEFT JOIN` was needed.
- vibefun PR73, `typechecker.ts`: the PR set out to close the value-restriction hole, but top-level `let mut` still did not enforce VF4018 at all.

A claim the change relies on is a promise too, whether or not a user reads it: a spec line, a code comment or a remark that says how a driver, a database or a runtime behaves. Probe it on the real system at its edges (see §7). When the change contains or relies on measurements (benchmarks, allocation figures, spike results), check that the setup matches how production code uses the thing measured.

- SqlSource phase 8.0, the 8.4 spec: it said SqlClient rounds a decimal parameter "half away from zero to that scale". At scale 0, SqlClient treats the scale as unset and `numeric(5,0)` with `12345.00m` throws "out of range". A probe at scale 0 shows it; the review that checked only scale 4 did not.
- SqlSource phase 8.0, the spike: it reported the same allocation "with or without `DataTypeName`", measured on a reused parameter. A generated method makes a new parameter per call, which costs 113 to 118 bytes more (TD-0078).

## 6. Repo conventions (`conventions`)

Rules the repo writes down in `AGENTS.md` or `CLAUDE.md`, at the root or nested in the directory of the changed file, and in the docs those files point to. Cite the file and quote the rule in `source`. Typical rules: test placement, types to use for a kind of value, keep-lists-in-sync footers, file size limits, naming. Conventions also tell you what not to report: honor any instruction about docs that are records of intent or areas not to review.

- vibefun, many PRs: tests missing from the colocated test file the repo's guidelines require. CodeRabbit cited "As per coding guidelines" in 81 findings, not all of them about test placement.
- SqlSource PR2, PR30, PR76 (declined, a counter-example): CodeRabbit asked for specs to be updated to match code although `docs/superpowers/AGENTS.md` says those specs are records of intent. Reading that rule would have prevented all three.

## 7. Docs the change made stale (`docs`)

Only docs the repo treats as living: README, manuals, architecture docs, `AGENTS.md` itself. Stale cross-references, counts that no longer match, examples that no longer compile, claims about behavior the change altered. Skip docs the repo marks as records of intent (plans, specs, decision records), and skip wording and markdown style.

**Grep without truncation.** For each behavior the change alters, `git grep -n` the living docs for the old statement (its key words, the flag, the diagnostic id) and read around every hit. Never pipe the search through `head` or `tail`: the stale line is often the one past the cut. When there are too many hits to read, narrow the pattern or the paths and search again.

**Try doc promises.** When the change adds or changes a user-facing promise ("never null when …", "waits until …"), try it against the real system when one runs locally (a container, the CLI), on the inputs a user would write, and at the edges of every value range it covers. When the repo pins an image for that system, starting it is part of the review: a probe goes under `not_checked` only with a reason that rules it out. When nothing runs locally, trace the promise through the code.

**Grep for promises the change fulfils.** For the feature the change delivers, `git grep` the living docs and the diagnostics' text for forward-looking statements about it ("a later version", "not yet", "planned", "in the future", "coming", "is not supported", with the feature's key words), and read around every hit. A statement the change makes untrue is a finding.

- SqlSource PR82, `README`: it said provenance fields are excluded from the comparison, but the test compares them.
- vibefun PR66, a docs page: the dependency-injection wiring snippet no longer matched the code after the change.
- SqlSource TD-0036, `docs/manual/diagnostics.md`: the change made `describe --check --database` report a gone entry of any database and reworded `tool.md`, but the SQLSRC219 bullet in the manual still said such an entry is not reported. A search for the old statement that reads every hit finds it.
- SqlSource TD-0051, `docs/manual/postgresql.md`: the manual promised `array_agg` is never null with a GROUP BY, but on PostgreSQL 18 a `WHERE` that fixes the grouped column to a parameter makes the planner drop the group key, and the property came out nullable. Running the query on a server showed it.
- SqlSource phase 8.2, SQLSRC232's help text: it still said "a later version may let a project declare a conversion" after the change delivered converters. A grep for "later version" finds it.

## 8. CI, build and config correctness (`build-ci`)

Build scripts, project files, CI workflows and container config that do something other than what the change expects. MSBuild item types, localized tool output in greps, missing image pins where the repo pins, retry logic that reuses a failed instance. Check what the tool actually does, not what the config looks like it does.

- SqlSource PR75, Web SDK projects: the SDK puts `*.json` into `Content`, not `None`, so `DependentUpon` was never applied to `.sql.json` sidecars.
- SqlSource PR14, `check-package-install.sh`: the script grepped compiler output for ASCII quotes, which fails under a German or Chinese SDK (`DOTNET_CLI_UI_LANGUAGE=de`).

**Build what users build.** When the change emits code or artifacts that another project compiles or consumes (a source generator, templates, code generation, a published package), build a consumer for each new shape of output the way a user would, with the warnings and analyzers a typical consumer turns on. A warning or error the repo's rules forbid in output is a finding.

- SqlSource phase 8.1, generated query methods: a method for a query with tokens raised CA2100 in a consumer built with the .NET analyzers, though `src/SqlSource/AGENTS.md` says a compiler warning inside generated code is a bug. It showed only once a later phase compiled the methods in an end-to-end project.

## Not reported

Explicitly not reported: praise, anything a formatter or linter in the repo would catch, generic dependency-bump advice, docstring coverage, requests to maintain hard-coded line numbers in docs, suggestions that are purely taste, and process items: branch freshness, CI state, push readiness.

A repo rule in `AGENTS.md` that requires something (for example a docstring on every public function) is a convention, not docstring coverage; report a violation of it under `conventions` with the rule quoted.

## Outside the change

Pre-existing issues outside the change go in a separate "outside the change" list, never mixed with findings on the change. The state script records them with status `outside`, and they stay out of the fix loop, so put an issue there only when the change has no part in it.

A pre-existing claim or defect that the change repeats or relies on is part of the change. When the changed code calls existing code or depends on it and the change's behavior is wrong because of it, or when the change restates an existing claim (in remarks, docs or `AGENTS.md`) that does not hold, report it as a finding on the change (usually `sibling-path`, `logic` or `docs`), at the existing code's file and lines. Pre-existing code that the change neither touches, relies on nor repeats goes in `outside_change`.

- SqlSource TD-0048, `PostgresConnection.cs`: the change allowed Npgsql's command events 2000 and 2001 on the strength of the existing claim that parameter logging is off, which the logger's remarks, `src/SqlSource.Tool/AGENTS.md` and the architecture doc repeat. A connection string with `Log Parameters=true` turns it on, and those events then carry the parameters. The first pass listed it as outside the change; it was a finding on the change.
