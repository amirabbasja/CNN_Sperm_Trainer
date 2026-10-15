#!/usr/bin/env bash
# Rootless setup and lifecycle management; all paths follow this script.
set -euo pipefail
umask 077
connection_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Python 3 is required. No sudo or pip packages are used.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$connection_dir/assets/manage_connection.py" "$@"
