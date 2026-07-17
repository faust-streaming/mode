#!/bin/sh -e

set -x

python3 -m build .
twine check dist/*
