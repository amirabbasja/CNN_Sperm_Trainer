#!/usr/bin/env bash
# Applies proxy environment variables to a child shell/command, not OS routes.
if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
  printf '%s\n' 'Run this script with bash, or use: eval "$(bash proxy_connect.sh --print-env)"' >&2
  return 1
fi
set -euo pipefail
connection_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$connection_dir/assets/manage_connection.py" connect "$@"
