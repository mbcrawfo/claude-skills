#!/usr/bin/env bash
# Eval scaffold: runs in the case's empty workspace and builds the fixture repo
# right there, so the agent's working directory is the repo. No network, and no
# dependence on the user's git config.
#
# Everything is committed on `main`; `chore/agents-md` is checked out at the end.
# The agents-md-cleanup-size-ask and -size-review cases share this script (keep
# the two copies byte-identical). The review fixture's files, plus statements 7
# and 8, docs/architecture.md and a little retry code. The guidelines section
# holds the three policy lines but no `Size limits:` line, so the skill sees a
# later run and a first size run: one rule (statement 7) is over the 80-word
# limit and no file is over its byte limit.
#
# Planted statements, numbered as the review grader numbers them:
#   1. root: "unittest discover -s test", but the folder is `tests/`: a fix.
#   2. root: "Write clear, readable code.": obvious, a delete.
#   3. root: a wordy ruff rule: a trim that must keep `ruff format .`
#      (pyproject.toml has [tool.ruff] and .github/workflows/ci.yml runs `ruff format --check .`).
#   4. root: "Use `uv` ... never `pip`": a project choice, keep or ask, never a delete.
#   5. root: `src/payments/retry.py` MAX_ATTEMPTS quirk: a single-file move.
#   6. root: API handlers return `Result`: belongs in src/AGENTS.md, a move
#      (src/handlers.py defines `Result` and a handler that returns it).
#   7. root: one ~250-word bullet holding four rules about src/payments/retry.py
#      (MAX_ATTEMPTS, log_retry, ValueError, a test) interleaved with explanation
#      of the backoff and the ban: split into four bullets, explanation to
#      docs/architecture.md under Retry, a pointer left behind.
#   8. root: the doubling-wait sentence docs/architecture.md#retry already says
#      word for word: a delete (or a pointer).
#   src/AGENTS.md: repeats the parse_date() docstring: a delete.
set -euo pipefail
unset CDPATH # a set CDPATH makes `cd` print and can send it elsewhere

# Isolate from the user's git configuration, signing and hooks.
for var in GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_NAMESPACE GIT_PREFIX GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT; do
  unset "$var"
done
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="Fixture Author" GIT_AUTHOR_EMAIL="fixture@example.invalid"
export GIT_COMMITTER_NAME="Fixture Author" GIT_COMMITTER_EMAIL="fixture@example.invalid"

for existing in .git .github AGENTS.md docs src tests pyproject.toml uv.lock; do
  if [ -e "$existing" ]; then
    echo "error: $existing already exists in $(pwd -P); run the scaffold in an empty directory" >&2
    exit 1
  fi
done

git init -q .
git symbolic-ref HEAD refs/heads/main
git config commit.gpgsign false
git config core.hooksPath .git/no-hooks

mkdir -p src/payments tests docs .github/workflows

cat >pyproject.toml <<'EOF'
[project]
name = "fixture"
version = "0.1.0"
requires-python = ">=3.9"

[tool.ruff]
line-length = 120
EOF

cat >.github/workflows/ci.yml <<'EOF'
name: ci
on: [push, pull_request]
jobs:
  ruff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pipx run ruff format --check .
EOF

cat >uv.lock <<'EOF'
version = 1
requires-python = ">=3.9"

[[package]]
name = "fixture"
version = "0.1.0"
source = { editable = "." }
EOF

cat >src/dates.py <<'EOF'
from datetime import date


def parse_date(text):
    """Parse a date string. Accepts ISO 8601 dates only."""
    return date.fromisoformat(text)
EOF

cat >src/handlers.py <<'EOF'
from dataclasses import dataclass
from typing import Optional

from src.dates import parse_date


@dataclass
class Result:
    value: Optional[object] = None
    error: Optional[str] = None


def get_date(params):
    try:
        return Result(value=parse_date(params["date"]))
    except (KeyError, ValueError) as exc:
        return Result(error=str(exc))
EOF

cat >src/payments/retry.py <<'EOF'
import time

MAX_ATTEMPTS = 3


def log_retry(attempt):
    print(f"retrying after attempt {attempt}")


def call_with_retry(fn):
    wait = 1
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fn()
        except ValueError:
            raise
        except OSError:
            if attempt == MAX_ATTEMPTS:
                raise
            log_retry(attempt)
            time.sleep(wait)
            wait *= 2
EOF

cat >tests/test_retry.py <<'EOF'
import unittest

from src.payments.retry import call_with_retry


class RetryTest(unittest.TestCase):
    def test_value_error_is_not_retried(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("bad")

        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
EOF

cat >docs/architecture.md <<'EOF'
# Architecture

## Retry

The retry loop sleeps between attempts, doubling the wait each time. It gives up after `MAX_ATTEMPTS` failed attempts and re-raises the last error.

## Dates

`parse_date()` accepts ISO 8601 dates only.
EOF

cat >tests/test_dates.py <<'EOF'
import unittest
from datetime import date

from src.dates import parse_date


class ParseDateTest(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(parse_date("2020-02-01"), date(2020, 2, 1))


if __name__ == "__main__":
    unittest.main()
EOF

cat >AGENTS.md <<'EOF'
# Agent instructions

## Workflow

- Run tests with `python3 -m unittest discover -s test`.
- Write clear, readable code.
- It is really very important that you always make sure to run `ruff format .` before you commit any Python code, because otherwise CI will fail.
- Use `uv` for dependencies; never call `pip` directly.

## Code notes

- `src/payments/retry.py`: keep `MAX_ATTEMPTS` at 3; the processor bans clients after 4 failed attempts a minute.
- API handlers in `src/` return `Result` objects, never raise.
- Payment retries (`src/payments/retry.py`): the loop sleeps between attempts and doubles the wait each time, starting at one second, so a failing call is tried at most three times over about three seconds. The short ladder is deliberate: the wait never grows past two seconds, the whole sequence finishes well inside a single request timeout, and the caller can report a clean failure instead of hanging. It also matters because the payment processor bans a client after four failed attempts within a minute and a longer ladder would trip that ban. Keep `MAX_ATTEMPTS` at 3 so the ladder never reaches the fourth attempt. Log every retry through `log_retry`, never with a bare print or logger call, because the on-call dashboards count retries by that one function and a retry that bypasses it hides a struggling processor from them. Never retry a `ValueError`: it means the request itself is malformed, so another attempt fails the same way and only burns one of the few attempts the processor allows before it bans the client. When you add a new reason to retry, add a test for it in `tests/test_retry.py`, because the retry paths are the ones that fail rarely and silently, and a regression there shows up only as a ban in production, long after the change that caused it has shipped and been forgotten.
- The retry loop sleeps between attempts, doubling the wait each time.

## AGENTS.md guidelines

- Accurate: true of the code today.
- Valuable: not a duplicate of a code comment or other doc, and not something almost any project does anyway.
- Brief: as few words as the meaning allows.
- Right level: in the deepest place that covers everything it applies to.
- New nested AGENTS.md files: allowed.
- File-specific quirks: comment in that file.
- Push-down: aggressive.
EOF

cat >src/AGENTS.md <<'EOF'
# src

- `parse_date()` accepts ISO 8601 dates only.
EOF

git add -A
git commit -q -m "Add fixture project"

git checkout -q -b chore/agents-md
echo "fixture repo in $(pwd -P)"
