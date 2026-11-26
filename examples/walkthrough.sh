#!/usr/bin/env bash
set -euo pipefail

export DAYTHREAD_HOME="$(mktemp -d)"
trap 'rm -rf "$DAYTHREAD_HOME"' EXIT

python3 -m daythread demo
python3 -m daythread brief --evening
python3 -m daythread policy show
python3 -m daythread export --format md
