#!/bin/sh

export PREFIX=""
if [ -d 'venv' ] ; then
    export PREFIX="venv/bin/"
fi

if ! command -v "${PREFIX}mypy" > /dev/null 2>&1 ; then
    echo "mypy is not installed -- it is an optional dependency." >&2
    echo "Install it with: pip install -r requirements-typecheck.txt" >&2
    echo "(mypy refuses to run on PyPy; use CPython to type-check.)" >&2
    exit 1
fi

set -ex

${PREFIX}mypy -p mode
