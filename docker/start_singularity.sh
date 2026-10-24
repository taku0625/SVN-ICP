#!/bin/bash
# Host launcher: mount source/data/work, initialize ROS, then run the chosen task.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(realpath "${SCRIPT_DIR}/..")"
image="${SIF_IMAGE:-${REPO_ROOT}/images/svnicp-humble-cuda124.sif}"
dataset="${SVNICP_DATASET_DIR:-${HOME}/dataset}"
work="${SVNICP_WORK_DIR:-${HOME}/svnicp-work}"
runtime=""
gpu=1
action=oxford
action_set=0
task_args=()

usage() {
  cat <<'USAGE'
Usage (on the host): bash docker/start_singularity.sh [options] [action]

Start the SIF and immediately execute a task. Default: build and evaluate Oxford Spires.

  --image FILE          SIF image (default: images/svnicp-humble-cuda124.sif)
  --dataset-dir DIR     HOST dataset root mounted at /dataset:ro (default: ~/dataset)
  --work-dir DIR        HOST writable directory mounted at /work (default: ~/svnicp-work)
  --runtime COMMAND     SingularityCE/Apptainer executable (auto-detected)
  --no-gpu              Omit --nv, e.g. for sequence discovery and validation
  --oxford-spires ...   Run Oxford Spires; remaining arguments go to the evaluation runner
  --shell               Open an interactive ROS-initialized Bash shell
  -- COMMAND ...        Run an arbitrary command/script in the container
  -h, --help            Show this help

Put launcher options before --oxford-spires or --. Commands run in /workspace/SVN-ICP.
The source and dataset are read-only; uv environments, builds and results go to /work.
Environment: SIF_IMAGE, SVNICP_DATASET_DIR, SVNICP_WORK_DIR set host defaults.
BUILD_JOBS, CMAKE_CUDA_ARCHITECTURES, ROS_DOMAIN_ID and ROS_LOG_DIR are forwarded when set.
USAGE
}
die() { echo "Error: $*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "$1 requires a value"; }
select_action() {
  [[ "$action_set" = 0 ]] || die "Choose one action: --shell, --oxford-spires, or -- COMMAND"
  action="$1"
  action_set=1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) need_value "$@"; image="$2"; shift 2 ;;
    --dataset-dir) need_value "$@"; dataset="$2"; shift 2 ;;
    --work-dir) need_value "$@"; work="$2"; shift 2 ;;
    --runtime) need_value "$@"; runtime="$2"; shift 2 ;;
    --no-gpu) gpu=0; shift ;;
    --shell) select_action shell; shift ;;
    --oxford-spires) select_action oxford; shift; task_args=("$@"); break ;;
    --) select_action command; shift; [[ $# -gt 0 ]] || die "Supply a command after --"; task_args=("$@"); break ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown launcher option: $1; put evaluator options after --oxford-spires" ;;
  esac
done

[[ -z "${SINGULARITY_CONTAINER:-}${APPTAINER_CONTAINER:-}" ]] ||
  die "Run this launcher on the host; exit the current container first"
[[ -f "$image" && -r "$image" && -s "$image" ]] || die "SIF image is missing, empty or unreadable: $image"
[[ -d "$dataset" && -r "$dataset" && -x "$dataset" ]] || die "Dataset directory is not accessible: $dataset"
if [[ -z "$runtime" ]]; then
  if command -v singularity >/dev/null 2>&1; then
    runtime=singularity
  elif command -v apptainer >/dev/null 2>&1; then
    runtime=apptainer
  else
    die "Install SingularityCE or Apptainer"
  fi
fi
command -v "$runtime" >/dev/null 2>&1 || die "Runtime not found: $runtime"
mkdir -p -- "$work" || die "Cannot create work directory: $work; use --work-dir with a writable path"
[[ -w "$work" && -x "$work" ]] || die "Work directory is not writable: $work"
image="$(realpath "$image")"
dataset="$(realpath "$dataset")"
work="$(realpath "$work")"
for directory in "$REPO_ROOT" "$dataset" "$work"; do
  [[ "$directory" != *:* && "$directory" != *,* ]] || die "Bind paths cannot contain ':' or ',': $directory"
done

container_args=(exec)
[[ "$gpu" = 0 ]] || container_args+=(--nv)
container_args+=(
  --bind "${REPO_ROOT}:/workspace/SVN-ICP:ro"
  --bind "${dataset}:/dataset:ro"
  --bind "${work}:/work:rw"
  --env UV_PROJECT_ENVIRONMENT=/work/uv-env
  --env UV_CACHE_DIR=/work/uv-cache
  --env UV_PYTHON=/usr/bin/python3
  --env SVNICP_PYTHON=/usr/bin/python3
  --env SVNICP_WORKSPACE=/work/svnicp_ws
  --env SVNICP_EVAL_ENV=/work/uv-evaluation
  --env DATASET_DIR=/dataset
  --pwd /workspace/SVN-ICP
)
for name in BUILD_JOBS CMAKE_CUDA_ARCHITECTURES ROS_DOMAIN_ID ROS_LOG_DIR; do
  if [[ -v "$name" ]]; then
    container_args+=(--env "${name}=${!name}")
  fi
done
case "$action" in
  oxford) command=(bash /workspace/SVN-ICP/scripts/run_oxford_spires_singularity.sh "${task_args[@]}") ;;
  shell) command=(bash) ;;
  command) command=("${task_args[@]}") ;;
esac

printf 'SIF: %s\nDataset: %s -> /dataset\nWork: %s -> /work\n' "$image" "$dataset" "$work" >&2
exec "$runtime" "${container_args[@]}" "$image" /ros_entrypoint.sh "${command[@]}"
