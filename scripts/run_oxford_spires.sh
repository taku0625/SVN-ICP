#!/bin/bash
# Run on the host; reuse the existing CUDA image and persistent build volumes.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(realpath "${SCRIPT_DIR}/..")"
: "${DATASET_DIR:?Set DATASET_DIR to the oxford_spires directory containing sequence directories}"
export OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/output/oxford_spires}"
export DATASET_MOUNT_PATH=/storage/oxford_spires
"${REPO_ROOT}/docker/start_docker.sh" bash -c '
set -e
cd /root/svnicp_ws
dry_run=0
for arg in "$@"; do
  if [ "$arg" = --dry-run ]; then dry_run=1; fi
done
if [ "$dry_run" = 0 ]; then
  colcon build --symlink-install --executor sequential --cmake-args \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES="${CMAKE_CUDA_ARCHITECTURES}"
  source install/setup.bash
fi
export ROS_LOCALHOST_ONLY=1
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-74}"
exec python3 src/SVN-ICP/scripts/evaluate_oxford_spires.py \
  --dataset-dir /storage/oxford_spires --output-dir /output "$@"
' -- "$@"
