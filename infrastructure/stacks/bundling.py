"""Local bundling of the Lambda code (no Docker needed)"""

import shutil
import subprocess
import sys
from pathlib import Path

import jsii
from aws_cdk import ILocalBundling

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "runtime"
SLAY_SOURCE = REPO_ROOT / "src" / "slay"

# pip platform tag for Lambda's default architecture (x86_64)
LAMBDA_PLATFORM = "manylinux2014_x86_64"


@jsii.implements(ILocalBundling)
class LocalBundler:
    """
    Build the Lambda bundle on this machine.

    The bundle holds runtime/handler.py, a copy of src/slay, and the packages
    in runtime/requirements.txt. pip installs them for the Lambda platform and
    the given Python version, so the bundle does not depend on what is
    installed locally.
    """

    def __init__(self, python_version: str):
        self.python_version = python_version

    def try_bundle(self, output_dir: str, *_options, **_keyword_options) -> bool:
        """Fill output_dir with the bundle; CDK's bundling options are not needed"""
        out = Path(output_dir)
        shutil.copy2(RUNTIME_DIR / "handler.py", out / "handler.py")
        shutil.copytree(
            SLAY_SOURCE,
            out / "slay",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            dirs_exist_ok=True,
        )
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--quiet",
                "--disable-pip-version-check",
                "--no-compile",
                "--target", str(out),
                "--platform", LAMBDA_PLATFORM,
                "--implementation", "cp",
                "--python-version", self.python_version,
                "--only-binary=:all:",
                "--requirement", str(RUNTIME_DIR / "requirements.txt"),
            ],
            check=True,
        )
        return True
