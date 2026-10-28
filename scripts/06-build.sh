#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
exec python3 "$project_dir/scripts/build.py" "$@"
