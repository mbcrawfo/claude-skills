"""Shared fixtures for the agents_md_inventory tests.

Tests import the script as `from agents_helpers import agents_md_inventory`, never with a
bare `import agents_md_inventory`: isort puts `agents_md_inventory` above `agents_helpers`,
and the bare import would run before this module puts the scripts dir on sys.path.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys

from helpers import commit_all, git, make_repo, write

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "agents-md-cleanup" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import agents_md_inventory  # noqa: E402

__all__ = ["agents_md_inventory", "call", "commit_all", "git", "make_repo", "run", "write"]


def call(*argv: str) -> tuple[int, str, str]:
    """Invoke the CLI's main(); return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = agents_md_inventory.main(list(argv))
        except SystemExit as exc:  # --help
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def run(*argv: str) -> dict:
    """Invoke the CLI's main(), assert it succeeded, and return the parsed JSON."""
    code, out, err = call(*argv)
    assert code == 0, f"main exited with {code!r}: {err}"
    return json.loads(out)
