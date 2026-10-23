"""
No tracked file writes to a fixed path under /tmp.

/tmp is small and shared by all users, and a fixed name collides across
concurrent runs. Temporary files belong under $TMPDIR, with unique names.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = "tests/unit/test_no_fixed_tmp.py"


def test_no_tracked_file_uses_fixed_tmp_paths():
    """git grep finds /tmp/ in no tracked file but this one"""
    result = subprocess.run(
        ["git", "grep", "-l", "-F", "/tmp/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), result.stderr

    offenders = set(result.stdout.split()) - {THIS_FILE}

    assert not offenders, f"fixed /tmp paths in: {sorted(offenders)}"
