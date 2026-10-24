#!/bin/bash
# Container startup task: build SVN-ICP and evaluate exactly one Oxford sequence.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sequence="${OXFORD_SEQUENCE:-2024-03-12-keble-college-02}"
runner_args=()

usage() {
  cat <<'USAGE'
Usage (inside the SIF): bash scripts/my_startup.sh [--sequence NAME] [EVALUATION_OPTIONS...]

Evaluate one Oxford Spires sequence (default: 2024-03-12-keble-college-02).
--sequence NAME selects another sequence; OXFORD_SEQUENCE also sets the default.
Other options (--dataset-dir, --output-dir, --play-rate, --dry-run, --skip-build,
--lidar-topic, --config, timeouts) are forwarded to the container evaluation runner.
Data defaults to /dataset. New results go to /work/results/<sequence>_<time>_<PID>.

From the host, automatically start the container and this task:
  bash docker/start_singularity.sh -- bash scripts/my_startup.sh
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sequence)
      [[ $# -ge 2 && -n "$2" ]] || { echo "Error: --sequence requires a name" >&2; exit 1; }
      sequence="$2"; shift 2 ;;
    --sequence=*) sequence="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift ;;
    *) runner_args+=("$1"); shift ;;
  esac
done

[[ -n "$sequence" && "$sequence" != */* && "$sequence" != -* && "$sequence" != . && "$sequence" != .. ]] ||
  { echo "Error: --sequence must be a sequence directory name" >&2; exit 1; }
export OUTPUT_DIR="${OUTPUT_DIR:-/work/results/${sequence}_$(date +%Y%m%d_%H%M%S)_$$}"
printf 'Oxford Spires sequence: %s\n' "$sequence"
exec bash "${SCRIPT_DIR}/run_oxford_spires_singularity.sh" \
  --dataset-dir "${DATASET_DIR:-/dataset}" --sequence "$sequence" \
  --play-rate 0.05 "${runner_args[@]}"
