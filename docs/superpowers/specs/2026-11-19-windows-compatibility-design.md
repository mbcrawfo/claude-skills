# Windows compatibility — design

Date: 2026-11-19

## Intent

The owner uses these plugins on Windows. Today the scripts break there: run natively on Windows (Python 3.14, Git Bash), implementation-manager fails 175 of 211 tests, branch-review fails 15 of 467, and the maintenance suite hangs after about 85 tests. Make every plugin script work on Windows, and add a nightly Windows CI run so it stays that way.

## Goals

- Every plugin script and every test suite works on Windows under Git Bash, with Python 3.9 and the pinned version.
- Linux and macOS behaviour is unchanged; the existing CI stays green.
- A nightly GitHub Actions workflow runs the test suites on a Windows runner.
- The root README states the Windows requirements.

## Non-goals

- Native PowerShell or cmd support. The shell is Git Bash, which is what Claude Code on Windows uses for its Bash tool.
- A `python` or `py` fallback for `python3`. The README requires `python3` on the Git Bash `PATH`.
- Running evals on Windows. Eval `scaffold.sh` files and `claude plugin eval` stay out of scope. Eval-side Python covered by unit tests (`replay.py`, `usage_report.py`) is in scope, because its tests run in the suite.
- Making Windows a required PR check.

## Anti-goals

- Hiding real bugs with CI-only settings. No `PYTHONUTF8=1` on the runner, and no skipping of whole test modules on Windows. The owner's machine does not run with those settings, so the tests must not either.

## Constraints

- Python 3.9+, standard library only (AGENTS.md).
- ruff config unchanged; `ruff check .` and `ruff format .` pass.
- Bump `version` in each changed plugin's `plugin.json` (branch-review 0.15.0, implementation-manager 0.10.0, maintenance 0.4.0 today).
- GitHub Actions are pinned by commit SHA with a version comment, as in `ci.yml`.

## Approaches

How far the fixes go:

- **A. Fix the code (chosen).** Platform-conditional locking, explicit UTF-8 on every file and subprocess text boundary, guards for Unix-only calls, separator-neutral test assertions, symlink tests skipped only when Windows refuses to create a symlink, and a root-cause fix for the maintenance hang. Most edits, but the tests catch bugs the owner would hit.
- B. Paper over it in CI: `PYTHONUTF8=1` on the runner and Windows skips for failing test groups. Fast, but the encoding bug stays live on the owner's machine. Rejected for that reason.

Where the Windows run lives:

- **1. A separate `.github/workflows/nightly-windows.yml` (chosen).** `schedule` plus `workflow_dispatch`, `windows-latest`, `defaults.run.shell: bash`, the same two Python versions as the Linux `test` job. Outside the required `ci` check, so a Windows-only break never blocks a PR; GitHub emails on scheduled-run failure.
- 2. A schedule-only Windows entry in `ci.yml`. One file, but the `ci` aggregator's needs-and-skip logic gets fiddly. Rejected.

## Known failures to fix (from the 2026-11-19 local run)

- `implementation-manager/scripts/runstate.py` imports `fcntl` (Unix-only): ~160 failures and every import of `runstate`. Use `msvcrt.locking` on Windows, `fcntl.flock` elsewhere, behind one small helper.
- `os.geteuid` (Unix-only) in branch-review.
- `os.utime(..., follow_symlinks=False)` raises `NotImplementedError` on Windows (12 errors).
- Non-ASCII filenames come back mangled (`é` → `�`): text decoding with the locale code page instead of UTF-8, most likely subprocess output from git. Pass `encoding="utf-8"` (and an error policy) wherever text crosses a boundary; give every `open()` an explicit encoding.
- Tests that assert `/`-separated paths (`'\\t\\agent-1.jsonl' != '/t/agent-1.jsonl'`, `not /plugin` vs `D:\\plugin`).
- `WinError 193`: a test or script executes a file directly that Windows cannot run (likely the `rs` shell script or a `.sh` fixture). Run it through `sh`/`bash` explicitly.
- `WinError 1314`: symlink creation needs admin or Developer Mode. Skip those tests when a probe symlink cannot be created; keep them running wherever symlinks work.
- Several `(0, '{...}') != (1, '')` and `1 != 0` assertion failures in branch-review: root-cause each (likely the above).
- maintenance suite hang after ~85 tests, with 4 errors and 1 failure before it. Likely a subprocess waiting on stdin or an editor (for example git opening an editor); find the cause and fix it.

This list is a starting point, not the full set: the builder reruns all three suites on Windows and fixes whatever remains.

## Decided

- Shell is Git Bash. `python3` stays the command in skills, `rs` and `verdict.py`.
- The README "Requirements" section gains a Windows paragraph: Git Bash as the shell, `python3` on its `PATH` (pyenv-win or the Microsoft Store Python provide it; the python.org installer does not). Written in STE per AGENTS.md.
- Nightly workflow: separate file, schedule + manual trigger, `windows-latest`, bash shell, Python 3.9 and `.python-version`, runs every `plugins/*/tests` suite the same way the Linux job does.
- Symlink-dependent tests skip on Windows only when symlinks can't be created. Record that gap as a tech-debt item (`docs/tech-debt`), since symlink handling then goes untested on a stock Windows runner.

## Left to the builder

- The nightly cron time.
- The exact shape of the locking helper and where the symlink-probe skip helper lives (each plugin has its own `tests/helpers.py`).
- Whether the 3.9 Windows job needs a pinned runner image, as the Linux 3.9 job does.
- Any further Windows failures found when rerunning the suites, fixed under the same rules.
