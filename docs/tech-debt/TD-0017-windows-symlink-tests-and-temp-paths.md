# TD-0017: Windows: symlink tests skip locally, and skill temp paths are unverified

## Problem

Two known gaps remain after the plugins were made to run on Windows (design: [`2026-11-19-windows-compatibility-design.md`](../superpowers/specs/2026-11-19-windows-compatibility-design.md)):

- Windows creates symlinks only with admin rights or Developer Mode. The tests that create one (31 in maintenance, 2 in branch-review, 3 in implementation-manager) skip through each plugin's `requires_symlinks` when they can't, so a local run on such a machine does not cover the scripts' symlink handling. GitHub's Windows runners are admin, so the [nightly Windows workflow](../../.github/workflows/nightly-windows.yml) runs them.
- Unverified: the skills tell the agent to make a temp directory with `mktemp -d`, which in Git Bash prints a POSIX path (`/tmp/tmp.XXXX`). Git Bash converts that path when it passes it to `python3` as an argument, but Claude Code's Write tool may not resolve `/tmp/...` to the same place. It affects branch-review's `review` skill and [`reviewer.md`](../../plugins/branch-review/agents/reviewer.md), and maintenance's `agents-md-cleanup`.

## Why it exists

Symlink creation is an OS permission the tests cannot grant themselves, and skipping is the only way to keep the suites green on a stock Windows machine. The temp-path question is about agent behavior in a live session, which the unit tests do not reach; the owner has not hit it in use.

## Impact

Low. The nightly run covers symlinks on Windows. A wrong temp path would show as a note or report file the script can't find, and the agent can recover by retrying with a Windows path.

## Proposed fix

For temp paths, have the skills make the directory with `python3 -c "import tempfile, pathlib; print(pathlib.Path(tempfile.mkdtemp()).as_posix())"`, which gives a `C:/...` path that Git Bash, Python and the Write tool all read the same way, and cover it with an eval run on Windows.

## Trigger

A Windows session that loses a note or report file under `/tmp`, or symlink handling breaking on Windows in real use.
