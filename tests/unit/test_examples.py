"""
The examples run without errors on the local backend.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_example_runs(example):
    """Each example exits with status 0"""
    env = dict(os.environ, SLAY_BACKEND="local", PYTHONPATH=str(ROOT / "src"))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("SLAY_STORAGE_BUCKET", None)

    result = subprocess.run(
        [sys.executable, str(example)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
