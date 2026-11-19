# Windows Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every plugin script and test suite passes on Windows under Git Bash, and a nightly Windows workflow keeps it that way.

**Architecture:** Fix each plugin's scripts at the root cause (locking, text encoding, line endings, Unix-only calls), with each plugin's tests made platform-neutral and symlink tests skipped only where Windows refuses symlinks. Plugins share no code, so each plugin gets its own small helpers. A separate scheduled workflow runs the existing suites on `windows-latest`.

**Tech Stack:** Python 3.9+ standard library (`msvcrt`, `fcntl`, `unittest`), Git Bash, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-19-windows-compatibility-design.md`

## Global Constraints

- Python 3.9+, standard library only. `Path.write_text(newline=...)` does not exist on 3.9: use `open(..., newline="")`.
- Shell is Git Bash. `python3` stays the command in skills, `rs` and `verdict.py`.
- Linux and macOS behaviour is unchanged; the existing `ci.yml` stays green.
- No `PYTHONUTF8=1` or `PYTHONIOENCODING` on the Windows runner, and no whole-module Windows skips.
- `ruff check .` and `ruff format .` pass; `claude plugin validate .` and `claude plugin validate plugins/<name>` pass.
- Bump each changed plugin's patch version: branch-review 0.15.0 → 0.15.1, implementation-manager 0.10.0 → 0.10.1, maintenance 0.4.0 → 0.4.1.
- Run tests with `python3 -m unittest discover -s plugins/<name>/tests` (on Windows, `python` if `python3` is not on `PATH`). On Windows, run without `PYTHONIOENCODING` set, since the runner won't have it.

### Windows rules (apply in every task)

- **W1 subprocess text:** every `subprocess.run(..., text=True)` (scripts and tests) also passes `encoding="utf-8"`. 26 calls today lack it.
- **W2 script output:** every script entry point (`main()`) calls `sys.stdout.reconfigure(encoding="utf-8")` and the same for `sys.stderr` before printing. Without it, a piped stdout on Windows is cp1252, and `json.dumps(..., ensure_ascii=False)` in maintenance crashes on non-cp1252 characters.
- **W3 files written:** every text file a script or test helper writes is LF: `open(path, mode, encoding="utf-8", newline="")`, never bare `Path.write_text`/`open(..., "w"/"a")` without `newline=""`.
- **W4 files read:** text a script returns or measures from a repo file treats CRLF as LF. A Windows checkout with `core.autocrlf=true` has CRLF on disk.
- **W5 shell scripts:** run `rs` and `.sh` files through `sh` (`["sh", path, ...]`), never by executing them directly (`WinError 193`).
- **W6 test skips:** a test that creates a symlink uses that plugin's `requires_symlinks` decorator. A test whose file name Windows forbids (for example one containing `\n`) uses `unittest.skipIf(os.name == "nt", "<reason>")`. Nothing else is skipped on Windows.

## Review Focus

1. Two processes contend for the implementation-manager run lock on Windows. The second must wait, not raise, even past `msvcrt.LK_LOCK`'s ~10 s retry limit. Test in Task 1.
2. A `requests.log` line ends in `\r\n` (written by a Windows tool). The watcher must take it as the same request, not as a bad line. Test in Task 1.
3. An `AGENTS.md` or rulings file is checked out with CRLF. Its byte and rule counts and its returned text must equal the LF version's. Test in Task 3.
4. A script prints a path holding a character outside cp1252 (`日本.md`) to a pipe. It must print UTF-8, not crash. Tests in Tasks 2 and 3.
5. The branch-review home-directory guard on Windows: `Path.home()` reads `USERPROFILE`, not `HOME`. The guard must still reject the user's home. Test in Task 2.

---

### Task 1: implementation-manager on Windows

**Files:**
- Modify: `plugins/implementation-manager/scripts/runstate.py` (`import fcntl` at line 7, `locked()` at 188, `_append()` at 489, `_write_state()` at 48)
- Modify: `plugins/implementation-manager/scripts/watcher.py` (`take_request()` at 57)
- Modify: `plugins/implementation-manager/scripts/run.py`, `prstate.py`, `verdict.py`, plus any other script with a `main()` or a write (W1–W3)
- Modify: `plugins/implementation-manager/tests/test_prune.py` (`age()` at 22, `os.geteuid()` at 124 and 193)
- Modify: `plugins/implementation-manager/tests/helpers.py`, and any other test that writes files the scripts parse (W3)
- Test: `plugins/implementation-manager/tests/test_runstate.py`, `test_watcher.py`
- Modify: `plugins/implementation-manager/.claude-plugin/plugin.json` (version 0.10.1)

**Interfaces:**
- Produces: `runstate._lock(f: IO) -> None` and `runstate._unlock(f: IO) -> None`. Blocking exclusive lock on an open lock file: `fcntl.flock` on POSIX, and `msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)` after `f.seek(0)` on Windows, retried while it raises `OSError`. `locked()` calls `_unlock` before the file closes. Import `fcntl` or `msvcrt` by `os.name`.

Known Windows failures (from the 2026-11-19 local run, Python 3.14): `fcntl` import breaks every module that imports `runstate`. With `fcntl` stubbed, `test_prune` has 14 errors (`utime(follow_symlinks=False)`, `geteuid`), and `test_run_cli` and `test_watcher` hang. `wait()` never sees a request, because `_append` opens `"a"` in text mode, which writes `\r\n`.

- [ ] **Step 1: Write the failing tests**
  - `test_runstate.LockedTests.test_second_process_waits_for_the_lock`: a child process (`subprocess` running `python -c` with `sys.path` set to the scripts dir) enters `locked(dir)`, prints `held`, and sleeps 12 s. The parent reads `held`, then times its own `locked(dir)` entry. Assert the parent entered after the child exited, with no exception. 12 s is past the `LK_LOCK` retry window.
  - `test_runstate`: after `go(...)` and `redo(...)`, `(run_dir / PUSHES).read_bytes()` contains no `b"\r"`.
  - `test_watcher.test_crlf_request_line_is_one_request`: write `b"<unit> <sha> <branch>\r\n"` to `requests.log` in binary. Assert `take_request` returns `REQUEST <unit> <sha> <branch>` and records no bad line.
- [ ] **Step 2: Run them; expect failure** (on Linux, the CRLF and lock tests fail or pass according to today's behaviour; on Windows, the whole module fails on `fcntl`).
- [ ] **Step 3: Implement**
  - Add `_lock`/`_unlock` and use them in `locked()`.
  - In `take_request`, strip one trailing `b"\r"` from each raw line before parsing. Keep the offset at `len(raw) + 1` of the unstripped line.
  - Apply W1–W3 across the plugin's scripts.
  - In `test_prune.age()`, call `os.utime(path, (when, when), follow_symlinks=False)` only when `os.utime in os.supports_follow_symlinks`; otherwise call `os.utime(path, ...)` (a symlink test is already skipped by W6 there).
  - Replace `os.geteuid() == 0` with `hasattr(os, "geteuid") and os.geteuid() == 0`. On Windows, `chmod(0)` does not make a directory unreadable, so skip `unreadable()` there with `skipTest("Windows ignores POSIX permissions")`.
  - Add `requires_symlinks` to `tests/helpers.py` (the shape Task 2's Interfaces block defines) and apply it per W6.
- [ ] **Step 4: Run the suite on Windows and Linux.** `python3 -m unittest discover -s plugins/implementation-manager/tests`. Expect `OK` on both, with no hang. Fix any remaining Windows failure under W1–W6.
- [ ] **Step 5: Bump the version, run ruff, then commit** `fix(implementation-manager): run on Windows`.

### Task 2: branch-review on Windows

**Files:**
- Modify: `plugins/branch-review/scripts/review_state.py` (subprocess calls at 77, 250, 355, 576, 2059; W1–W3)
- Modify: `plugins/branch-review/evals/replay/replay.py` (`Runner.rs()` at 300 → W5; `_run()` at 287 → W1)
- Modify: `plugins/branch-review/evals/usage/usage_report.py` (W1–W3 if it applies)
- Modify: `plugins/branch-review/tests/helpers.py` (W1, W3, `requires_symlinks`)
- Modify: `plugins/branch-review/tests/test_cleanup.py` (symlink tests at 390, 453), `test_replay.py` (95, 363, 604), `test_state_dir.py` (68), `test_generated.py`, `test_snapshot.py`
- Modify: `plugins/branch-review/.claude-plugin/plugin.json` (version 0.15.1)

**Interfaces:**
- Produces: `helpers.can_symlink() -> bool` (creates a probe symlink in a temp dir; `False` on `OSError`) and `helpers.requires_symlinks = unittest.skipUnless(can_symlink(), "creating symlinks needs admin rights or Developer Mode on Windows")`. Tasks 1 and 3 copy this into their own `helpers.py`.

Known Windows failures (15 of 467):
- `test_cleanup`: 2 symlink tests (`WinError 1314`).
- `test_replay`:
  - `Runner.rs` executes `rs` directly (`WinError 193`).
  - Lines 95 and 363 assert `/`-separated paths that the script renders through `Path` (`\t\agent-1.jsonl`, `D:\plugin`).
  - Line 604's regex expects `/plugin`.
- `test_generated` (2) and `test_snapshot` (1): `é` and `ü` file names come back as `�`. This is a W1 decoding problem in the script or the fixture.
- `test_state_dir.test_root_and_home_state_dirs_are_rejected` (6 subtests): the test patches only `HOME`, but `Path.home()` on Windows reads `USERPROFILE`.

- [ ] **Step 1: Write the failing tests**
  - `test_state_dir.test_root_and_home_state_dirs_are_rejected` patches both `HOME` and `USERPROFILE` to the fake home (Review Focus 5). On Windows it now fails only where the script mishandles the home.
  - `test_generated`/`test_snapshot`: add a case with a file named `日本 x.txt`, asserted the same way as the existing `é` case (Review Focus 4).
- [ ] **Step 2: Run on Windows; expect the listed failures.**
- [ ] **Step 3: Fix**
  - Apply W1–W3 and W5 in the scripts.
  - In `test_replay`, assert against `str(Path(...))` or the runner's actual plugin path, not literal `/` strings.
  - Mark both `test_cleanup` symlink tests `@requires_symlinks`.
- [ ] **Step 4: Run the suite on Windows and Linux.** Expect `OK`, with only `requires_symlinks` skips added on Windows without Developer Mode.
- [ ] **Step 5: Bump the version, run ruff, then commit** `fix(branch-review): run on Windows`.

### Task 3: maintenance on Windows

**Files:**
- Modify: `plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py` (`print` at 1270 → W2; repo-file reads → W4; W1, W3)
- Modify: `plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_limits.py` (byte and rule measurement → W4)
- Modify: `plugins/maintenance/skills/superpowers-cleanup/scripts/docs_inventory.py` (`print` at 430 → W2; W1, W3, W4)
- Modify: `plugins/maintenance/tests/helpers.py` (`write()` at 71 → W3, `git()`/`call()` → W1, add `requires_symlinks`), `agents_helpers.py` (`call()` → W1)
- Modify: `plugins/maintenance/tests/test_agents_md_inventory.py`, `test_agents_md_verdicts.py`
- Modify: `plugins/maintenance/.claude-plugin/plugin.json` (version 0.4.1)

**Interfaces:**
- Consumes: the `requires_symlinks` shape from Task 2, copied into this plugin's `tests/helpers.py`.

Known Windows failures:
- `test_agents_md_inventory`: 23 errors and 4 failures.
- `test_agents_md_verdicts`: 10 errors.
- 33 of these are symlink creation (`WinError 1314`).
- 1 is a file name with `\n` (`Errno 22`).
- 4 are CRLF from `write_text`: rulings text `keep uv\r\n`, plus byte counts `24 != 20` and `187 != 194`.
- `discover` over the whole suite hung after about 85 tests when run without `PYTHONIOENCODING`. With it set, each module ran alone without a hang. W2 is the likely cause; confirm it.

- [ ] **Step 1: Write the failing tests** (Review Focus 3, 4)
  - `test_agents_md_inventory.SizeTest.test_crlf_file_measures_as_lf`: write the same `AGENTS.md` content once with LF and once with CRLF (binary write). Assert equal byte and rule counts.
  - `InventoryTest.test_crlf_rulings_text_is_lf`: write a rulings file with CRLF. Assert the returned `text` is `"# Rulings\n\n- keep uv\n"`.
  - `test_inventory.test_non_cp1252_path_prints_utf8`: run `docs_inventory.py inventory` through `subprocess` on a repo holding `docs/日本.md`, decoding stdout as UTF-8, with `PYTHONIOENCODING` removed from the child env. Assert exit 0 and that the path appears in the JSON.
- [ ] **Step 2: Run on Windows; expect them to fail.**
- [ ] **Step 3: Fix**
  - Apply W1–W4 in all three scripts and W3 in `helpers.write()`.
  - Mark the 33 symlink tests `@requires_symlinks`.
  - Mark `test_references_survive_a_newline_in_a_file_name` with `skipIf(os.name == "nt", "Windows file names cannot hold a newline")`.
- [ ] **Step 4: Run the whole suite with `discover` on Windows (no `PYTHONIOENCODING`) and on Linux.** Expect `OK` with no hang. If it still hangs, run `-v` to find the last test started and fix that cause.
- [ ] **Step 5: Bump the version, run ruff, then commit** `fix(maintenance): run on Windows`.

### Task 4: Nightly Windows workflow, line endings and docs

**Files:**
- Create: `.github/workflows/nightly-windows.yml`
- Create: `.gitattributes`
- Modify: `README.md` ("Requirements" section)
- Create: `docs/tech-debt/TD-0017-windows-symlink-tests-and-temp-paths.md`
- Modify: `docs/tech-debt/README.md` (add the row; `Next id` → `TD-0018`)

- [ ] **Step 1: Add `.gitattributes`** containing `* text=auto eol=lf`. Windows checkouts of this repo then get LF goldens and fixtures, matching `.editorconfig`'s `end_of_line = lf`. Run `git add --renormalize .` and confirm `git status` shows no content changes.
- [ ] **Step 2: Write `nightly-windows.yml`.**
  - `name: Nightly Windows`.
  - `on: schedule: [cron: "17 7 * * *"]` plus `workflow_dispatch`.
  - `permissions: contents: read`.
  - One `test` job on `windows-latest` with `defaults.run.shell: bash`.
  - Matrix: `python-version: "3.9"` and `python-version-file: .python-version`, with `fail-fast: false`.
  - Steps: checkout and setup-python, pinned to the same SHAs as `ci.yml`. Then run the same `for dir in plugins/*/tests` loop as `ci.yml`, calling `python` (setup-python on Windows provides `python`, not `python3`).
  - Comment why it is outside `ci.yml`: a Windows-only break must not block PRs.
- [ ] **Step 3: Verify the workflow.**
  - Push the branch.
  - Run `gh workflow run nightly-windows.yml --ref <branch>`. A dispatch on a non-default branch works once the file exists on that branch.
  - Watch it with `gh run watch`.
  - Expect both matrix jobs green. Hosted runners are admin, so `requires_symlinks` tests run there and exercise the scripts' symlink handling on Windows. Fix any failure in the owning plugin's code under W1–W6.
  - If 3.9 is unavailable on `windows-latest`, pin `windows-2022` for that entry, as `ci.yml` pins `ubuntu-24.04`.
- [ ] **Step 4: README.** Add a Windows paragraph to "Requirements" in STE (sentences of 20 words or fewer, imperative, no "-ing" forms), saying that:
  - on Windows, the plugins need Git Bash as the shell for Claude Code;
  - `python3` must be on the Git Bash `PATH`;
  - pyenv-win and the Microsoft Store Python supply `python3`, and the python.org installer does not.
- [ ] **Step 5: TD-0017.** Use the layout of the other TD files: Problem, Why it exists, Impact (Low), Proposed fix. It records two items:
  - Symlink tests skip on Windows machines without admin rights or Developer Mode, so local runs there do not cover symlink handling. The nightly runner does.
  - Unverified: skills tell the agent to use `mktemp -d`, which prints a Git Bash path (`/tmp/...`). Git Bash converts that path for `python3` arguments, but Claude Code's Write tool may not resolve it the same way. The affected skills are branch-review `review`, maintenance `agents-md-cleanup` and the reviewer agent.

  Add the row to the tech-debt README table.
- [ ] **Step 6: Commit** `ci: nightly Windows test run`, with the README and TD changes.

### Final check (after all tasks)

- [ ] Full suites green on Windows locally (without `PYTHONIOENCODING`) and on Linux CI. `ruff check .`, `ruff format --check .` and both `claude plugin validate` commands pass. The nightly workflow is green on manual dispatch.
