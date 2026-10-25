#!/bin/bash
# One GPU per speed/sequence under the retimed RAL 2026 dataset.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  collect) exec bash "$SCRIPT_DIR/submit_all.sh" "$@" ;;
  submit) shift ;;
esac
exec bash "$SCRIPT_DIR/submit_all.sh" submit \
  --dataset-dir "$HOME/dataset/rosbag/ral2026" --layout ral2026 "$@"
