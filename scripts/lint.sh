#!/usr/bin/env bash

set -e
set -x

# Type checking lives in scripts/typecheck.sh: mypy needs CPython 3.10+,
# so it cannot run on every leg of the test matrix.
ruff check mode tests
ruff format mode tests --check
