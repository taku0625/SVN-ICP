#!/bin/bash
# Host-side entry point for PBS batch submission and result collection.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_COMMAND="$(SVNICP_UV_DIR="${SVNICP_UV_DIR:-$HOME/svnicp-work/tools/uv-0.12.23}" bash "$REPO_ROOT/scripts/ensure_uv.sh")"
case "${1:-}" in submit|collect) ;; *) set -- submit "$@" ;; esac
exec "$UV_COMMAND" run --project "$REPO_ROOT/docker" --locked --group test \
  python "$REPO_ROOT/abci/submit_oxford_spires.py" "$@"
