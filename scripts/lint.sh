#!/usr/bin/env bash

set -e
set -x

# Type checking lives in scripts/typecheck.sh: mypy is an optional
# dependency and refuses to run on PyPy, so it cannot go here.
ruff check mode tests
ruff format mode tests --check
