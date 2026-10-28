#!/usr/bin/env bash
set -Eeuo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
if [[ -f "$project_dir/config/build.env" ]]; then
    set -a
    source "$project_dir/config/build.env"
    set +a
fi
