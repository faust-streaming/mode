"""Type checking as an integration test.

mypy is an optional dependency (`requirements-typecheck.txt`), so this
skips rather than fails when it is not installed -- contributors do not
have to install it to run the suite. CI installs it on the CPython legs
of the matrix, which is where the check actually runs.
"""

import importlib.util
import platform
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent

# `mypy.main` calls sys.exit(2) at import time under PyPy, so mypy must not
# even be imported there -- run it out of process and skip the leg entirely.
pytestmark = pytest.mark.skipif(
    platform.python_implementation() == "PyPy",
    reason="mypy refuses to run under PyPy",
)


def test_mypy_reports_no_errors():
    if importlib.util.find_spec("mypy") is None:
        pytest.skip(
            "mypy is not installed: pip install -r requirements-typecheck.txt"
        )
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "-p", "mode"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"mypy -p mode failed:\n{completed.stdout}{completed.stderr}"
    )
