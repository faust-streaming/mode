#!/usr/bin/env bash

set -e
set -x

# Type checking lives in scripts/typecheck.sh: mypy cannot run under
# PyPy, so it cannot go on every leg of the test matrix.
ruff check mode tests
ruff format mode tests --check
