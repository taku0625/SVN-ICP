#!/bin/bash
# Run inside the SIF: read-only source/data, writable build and results in /work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(realpath "${SCRIPT_DIR}/..")"
WORKSPACE="${SVNICP_WORKSPACE:-/work/svnicp_ws}"
DATASET_DIR="${DATASET_DIR:-/dataset}"
OUTPUT_DIR="${OUTPUT_DIR:-/work/results/oxford_spires_$(date +%Y%m%d_%H%M%S)_$$}"
IMAGE_PYTHON="${SVNICP_PYTHON:-/usr/bin/python3}"
BUILD_JOBS="${BUILD_JOBS:-2}"
skip_build=0
build_only=0
inspection=0
evaluator_args=()

usage() {
  cat <<'USAGE'
Usage (inside Singularity): bash scripts/run_oxford_spires_singularity.sh [options]

  --dataset-dir DIR    Sequence root (default: $DATASET_DIR or /dataset)
  --output-dir DIR     New results directory (default: a unique path in /work/results)
  --list-sequences     List paths without ROS, a GPU or a node build
  --dry-run            Validate bags/metadata/GT without ROS, a GPU or a node build
  --build-only         Build and check the node without evaluating a dataset
  --skip-build         Evaluate using an existing workspace install
  -h, --help           Show this help

Other options (--sequence, --play-rate, --lidar-topic, --config, timeouts) go to
evaluate_oxford_spires.py. All sequences are evaluated sequentially by default.
SVNICP_WORKSPACE sets the build workspace (default: /work/svnicp_ws).
SVNICP_EVAL_ENV sets a dedicated uv environment (default: /work/uv-evaluation).
SVNICP_PYTHON selects the image's Python; ROS Humble uses /usr/bin/python3 (3.10).
BUILD_JOBS limits compilation (default: 2). CMAKE_CUDA_ARCHITECTURES selects GPUs.
USAGE
}
die() { echo "Error: $*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "$1 requires a value"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset-dir) need_value "$@"; DATASET_DIR="$2"; shift 2 ;;
    --dataset-dir=*) DATASET_DIR="${1#*=}"; shift ;;
    --output-dir) need_value "$@"; OUTPUT_DIR="$2"; shift 2 ;;
    --output-dir=*) OUTPUT_DIR="${1#*=}"; shift ;;
    --skip-build) skip_build=1; shift ;;
    --build-only) build_only=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; evaluator_args+=("$@"); break ;;
    *) evaluator_args+=("$1"); shift ;;
  esac
done
for argument in "${evaluator_args[@]}"; do
  case "$argument" in --dry-run|--list-sequences|-h|--help) inspection=1 ;; esac
done
[[ "$BUILD_JOBS" =~ ^[1-9][0-9]*$ ]] || die "BUILD_JOBS must be a positive integer"
[[ "$build_only" = 0 || ( "$skip_build" = 0 && "$inspection" = 0 ) ]] ||
  die "--build-only cannot be combined with --skip-build or inspection options"
command -v uv >/dev/null 2>&1 || die "uv is required"
command -v "$IMAGE_PYTHON" >/dev/null 2>&1 || die "Image Python not found: $IMAGE_PYTHON"
IMAGE_PYTHON="$(command -v "$IMAGE_PYTHON")"

if [[ "$inspection" = 0 ]]; then
  ros_setup="${SVNICP_ROS_SETUP:-/opt/ros/${ROS_DISTRO:-humble}/setup.bash}"
  [[ -f "$ros_setup" ]] || die "ROS setup not found: $ros_setup (run inside the SIF)"
  set +u
  source "$ros_setup"
  set -u
fi

# Keep evaluation dependencies separate from the converter/test environment.
# System site packages provide ROS/rosbag dependencies installed in the image.
export UV_PROJECT_ENVIRONMENT="${SVNICP_EVAL_ENV:-/work/uv-evaluation}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/work/uv-cache}"
if [[ ! -e "$UV_PROJECT_ENVIRONMENT" ]]; then
  uv venv --python "$IMAGE_PYTHON" --system-site-packages "$UV_PROJECT_ENVIRONMENT"
fi
grep -Eq '^include-system-site-packages = true[[:space:]]*$' "$UV_PROJECT_ENVIRONMENT/pyvenv.cfg" ||
  die "Evaluation environment must use --system-site-packages; choose a new SVNICP_EVAL_ENV"
uv sync --project "${REPO_ROOT}/docker" --locked --no-dev --group evaluation --python "$IMAGE_PYTHON"
grep -Eq '^include-system-site-packages = true[[:space:]]*$' "$UV_PROJECT_ENVIRONMENT/pyvenv.cfg" ||
  die "Python changed; create a fresh system-site-packages environment with SVNICP_EVAL_ENV"
evaluation_python="${UV_PROJECT_ENVIRONMENT}/bin/python"
evaluator="${SCRIPT_DIR}/evaluate_oxford_spires.py"

if [[ "$inspection" = 1 ]]; then
  exec "$evaluation_python" "$evaluator" --dataset-dir "$DATASET_DIR" \
    --output-dir "$OUTPUT_DIR" "${evaluator_args[@]}"
fi
if [[ "$build_only" = 0 ]]; then
  # Parse options and check discovery before compiling the node.
  "$evaluation_python" "$evaluator" --dataset-dir "$DATASET_DIR" --output-dir "$OUTPUT_DIR" \
    --list-sequences "${evaluator_args[@]}"
  [[ ! -e "$OUTPUT_DIR/summary.json" ]] || die "Results already exist; choose a new --output-dir"
fi
"$evaluation_python" -c 'import numpy, yaml, rclpy, rosbag2_py; from sensor_msgs.msg import PointCloud2; from rclpy.type_support import check_for_type_support; check_for_type_support(PointCloud2)'

mkdir -p "$WORKSPACE"
WORKSPACE="$(realpath "$WORKSPACE")"
if [[ "$skip_build" = 0 ]]; then
  command -v colcon >/dev/null 2>&1 || die "colcon is required in the image"
  export CMAKE_BUILD_PARALLEL_LEVEL="$BUILD_JOBS"
  export MAKEFLAGS="-j${BUILD_JOBS}"
  colcon --log-base "${WORKSPACE}/log" build \
    --base-paths "${REPO_ROOT}/stein_msgs" "${REPO_ROOT}/svn-icp" \
    --packages-up-to svn-icp --symlink-install --executor sequential \
    --build-base "${WORKSPACE}/build" --install-base "${WORKSPACE}/install" \
    --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
    "-DCMAKE_CUDA_ARCHITECTURES=${CMAKE_CUDA_ARCHITECTURES:-75;80;86;89;90}" \
    "-DPython3_EXECUTABLE=${IMAGE_PYTHON}" "-DPYTHON_EXECUTABLE=${IMAGE_PYTHON}"
fi
[[ -f "$WORKSPACE/install/setup.bash" ]] || die "No install/setup.bash; build the workspace first"
set +u
source "$WORKSPACE/install/setup.bash"
set -u
prefix="$(ros2 pkg prefix svn-icp)"
[[ "$(realpath "$prefix")" = "$(realpath "$WORKSPACE/install/svn-icp")" ]] ||
  die "ROS selected another svn-icp installation: $prefix"
node="${prefix}/lib/svn-icp/svnicp_lio_node"
[[ -x "$node" ]] || die "Node executable not found: $node"
dependencies="$(ldd "$node")" || die "Cannot inspect node shared libraries: $node"
if [[ "$dependencies" = *"not found"* ]]; then
  printf '%s\n' "$dependencies" >&2
  die "Missing node libraries; check the install and GPU bindings (--nv)"
fi
echo "Node ready: $node"
[[ "$build_only" = 0 ]] || exit 0

export ROS_LOCALHOST_ONLY=1
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-74}"
echo "Results: $OUTPUT_DIR"
exec "$evaluation_python" "$evaluator" --dataset-dir "$DATASET_DIR" \
  --output-dir "$OUTPUT_DIR" "${evaluator_args[@]}"
