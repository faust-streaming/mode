#!/usr/bin/env bash

set -e
set -x

# Type checking is not here: mypy is an optional dependency that refuses
# to run on PyPy.  It runs from tests/functional/test_typecheck.py (and
# scripts/typecheck.sh, for a direct run).
ruff check mode tests
ruff format mode tests --check
