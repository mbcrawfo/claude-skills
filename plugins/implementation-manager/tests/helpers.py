"""Puts the implementation-manager scripts directory on sys.path for the tests, and holds shared test helpers."""

from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def can_symlink() -> bool:
    """Whether this process can create a symlink: Windows refuses without admin rights or Developer Mode."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            (Path(tmp) / "link").symlink_to(Path(tmp) / "target")
        except OSError:
            return False
    return True


requires_symlinks = unittest.skipUnless(
    can_symlink(), "creating symlinks needs admin rights or Developer Mode on Windows"
)
