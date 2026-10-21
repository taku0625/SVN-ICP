#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(realpath "${SCRIPT_DIR}/..")"
RVIZ="${RVIZ:-False}"
PLAY_RATE="${PLAY_RATE:-0.05}"
READ_AHEAD_QUEUE_SIZE="${READ_AHEAD_QUEUE_SIZE:-10}"
SAVE_TUM_TRAJECTORY="${SAVE_TUM_TRAJECTORY:-True}"
TUM_TRAJECTORY_PATH="${TUM_TRAJECTORY_PATH:-/output/niwa2_svnicp.tum}"
CMAKE_CLEAN_CACHE="${CMAKE_CLEAN_CACHE:-0}"

CLEAN_CACHE_ARG=""
if [ "${CMAKE_CLEAN_CACHE}" = "1" ]; then
  CLEAN_CACHE_ARG="--cmake-clean-cache"
fi

"${REPO_ROOT}/docker/start_docker.sh" bash -lc "
set -e
cd /root/svnicp_ws
colcon build --symlink-install ${CLEAN_CACHE_ARG} --cmake-args \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=\"\${CMAKE_CUDA_ARCHITECTURES}\"
source install/setup.bash
ros2 launch svn-icp niwa2.launch.py \
  bag_path:=/storage/rosbag/niwa2 \
  rviz:=${RVIZ} \
  play_rate:=${PLAY_RATE} \
  read_ahead_queue_size:=${READ_AHEAD_QUEUE_SIZE} \
  save_tum_trajectory:=${SAVE_TUM_TRAJECTORY} \
  tum_trajectory_path:=${TUM_TRAJECTORY_PATH}
"
