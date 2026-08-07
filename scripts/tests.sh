#!/bin/sh

export PREFIX=""
if [ -d 'venv' ] ; then
    export PREFIX="venv/bin/"
fi

set -ex

# Coverage settings (source, omit, branch, fail_under) live in the
# [tool.coverage.*] sections of pyproject.toml.  Without --cov nothing is
# measured, which meant the configured `fail_under` was never enforced and
# the Codecov upload in CI had no report to find.
${PREFIX}pytest tests/unit tests/functional \
    --cov \
    --cov-report=term-missing \
    --cov-report=xml
