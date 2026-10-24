#!/bin/bash
# Convert Dockerfile to a definition with spython, then build a SIF without Docker.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(realpath "${SCRIPT_DIR}/..")"
output="${REPO_ROOT}/images/svnicp-humble-cuda124.sif"
definition=""
dockerfile="${SCRIPT_DIR}/Dockerfile"
runtime=""
definition_only=0
build_args=()
runtime_args=()

usage() {
  cat <<'USAGE'
Usage: bash docker/build_singularity.sh [options] [-- SINGULARITY_BUILD_OPTIONS...]

Convert Dockerfile with spython and build SIF. Docker is not required.
Python dependencies are managed by uv using docker/pyproject.toml and uv.lock.

  --output FILE         SIF destination (default: images/svnicp-humble-cuda124.sif)
  --definition FILE     Generated definition (default: SIF path with .def extension)
  --definition-only     Generate the definition without a Singularity runtime
  --dockerfile FILE     Source Dockerfile (default: docker/Dockerfile)
  --build-arg NAME=VALUE Override a Dockerfile ARG; may be repeated
  --runtime COMMAND     SingularityCE/Apptainer executable (auto-detected)
  --fakeroot            Build with the runtime's --fakeroot option
  -h, --help            Show this help

Existing output files are never overwritten. The generated definition is kept
for review even if SIF building fails. By default, uv prepares the Python environment.
SPYTHON_PYTHON optionally selects an already prepared Python executable instead.
USAGE
}

die() { echo "Error: $*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "$1 requires a value"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) need_value "$@"; output="$2"; shift 2 ;;
    --definition) need_value "$@"; definition="$2"; shift 2 ;;
    --dockerfile) need_value "$@"; dockerfile="$2"; shift 2 ;;
    --runtime) need_value "$@"; runtime="$2"; shift 2 ;;
    --build-arg) need_value "$@"; build_args+=(--build-arg "$2"); shift 2 ;;
    --definition-only) definition_only=1; shift ;;
    --fakeroot) runtime_args+=(--fakeroot); shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; runtime_args+=("$@"); break ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

definition="${definition:-${output%.sif}.def}"
[[ "$definition" != "$output" ]] || die "Definition and SIF must have different paths"
[[ ! -e "$definition" && ! -L "$definition" ]] || die "Definition already exists: $definition"
[[ -f "$dockerfile" ]] || die "Dockerfile not found: $dockerfile"
if [[ "$definition_only" = 0 ]]; then
  [[ ! -e "$output" && ! -L "$output" ]] || die "SIF already exists: $output"
  if [[ -z "$runtime" ]]; then
    if command -v singularity >/dev/null 2>&1; then
      runtime=singularity
    elif command -v apptainer >/dev/null 2>&1; then
      runtime=apptainer
    else
      die "Install SingularityCE/Apptainer, or use --definition-only"
    fi
  fi
  command -v "$runtime" >/dev/null 2>&1 || die "Runtime not found: $runtime"
elif [[ ${#runtime_args[@]} -gt 0 || -n "$runtime" ]]; then
  die "Runtime options have no effect with --definition-only"
fi

if [[ -n "${SPYTHON_PYTHON:-}" ]]; then
  "$SPYTHON_PYTHON" "${SCRIPT_DIR}/dockerfile_to_singularity.py" \
    "$dockerfile" "$definition" "${build_args[@]}"
else
  command -v uv >/dev/null 2>&1 || die "Install uv, then rerun this script"
  uv run --project "$SCRIPT_DIR" --locked --no-dev \
    python "${SCRIPT_DIR}/dockerfile_to_singularity.py" \
    "$dockerfile" "$definition" "${build_args[@]}"
fi
echo "Created definition: $definition"
[[ "$definition_only" = 0 ]] || exit 0

definition="$(realpath "$definition")"
mkdir -p "$(dirname "$output")"
output="$(cd "$(dirname "$output")" && pwd)/$(basename "$output")"
temporary="$(mktemp -d "$(dirname "$output")/.svnicp-image.XXXXXX")"
trap 'rm -rf -- "$temporary"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Keep relative COPY/ADD paths relative to the repository build context.
cd "$REPO_ROOT"
"$runtime" build "${runtime_args[@]}" "${temporary}/image.sif" "$definition"
"$runtime" inspect "${temporary}/image.sif" >/dev/null
[[ -s "${temporary}/image.sif" ]] || die "No SIF was produced"
ln -- "${temporary}/image.sif" "$output"
echo "Created SIF: $output"
