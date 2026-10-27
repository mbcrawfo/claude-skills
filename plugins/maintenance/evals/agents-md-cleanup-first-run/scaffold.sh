#!/usr/bin/env bash
# Eval scaffold: runs in the case's empty workspace and builds the fixture repo
# right there, so the agent's working directory is the repo. No network, and no
# dependence on the user's git config.
#
# Everything is committed on `main`; `chore/agents-md` is checked out at the end.
# The agents-md-cleanup-review scaffold is identical except that its root
# AGENTS.md ends with an `AGENTS.md guidelines` section holding the policy lines.
# This one has no such section, so the skill sees a first run.
#
# Planted statements, numbered as the review grader numbers them:
#   1. root: "unittest discover -s test", but the folder is `tests/`: a fix.
#   2. root: "Write clear, readable code.": obvious, a delete.
#   3. root: a wordy ruff rule: a trim that must keep `ruff format .`.
#   4. root: "Use `uv` ... never `pip`": a project choice, keep or ask, never a delete.
#   5. root: `src/payments/retry.py` MAX_ATTEMPTS quirk: a single-file move.
#   6. root: API handlers return `Result`: belongs in src/AGENTS.md, a move.
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

for existing in .git AGENTS.md src tests pyproject.toml uv.lock; do
  if [ -e "$existing" ]; then
    echo "error: $existing already exists in $(pwd -P); run the scaffold in an empty directory" >&2
    exit 1
  fi
done

git init -q .
git symbolic-ref HEAD refs/heads/main
git config commit.gpgsign false
git config core.hooksPath .git/no-hooks

mkdir -p src/payments tests

cat >pyproject.toml <<'EOF'
[project]
name = "fixture"
version = "0.1.0"
requires-python = ">=3.9"
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

cat >src/payments/retry.py <<'EOF'
MAX_ATTEMPTS = 3
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
EOF

cat >src/AGENTS.md <<'EOF'
# src

- `parse_date()` accepts ISO 8601 dates only.
EOF

git add -A
git commit -q -m "Add fixture project"

git checkout -q -b chore/agents-md
echo "fixture repo in $(pwd -P)"
