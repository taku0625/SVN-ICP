#!/bin/bash
# Build the existing Dockerfile and convert its Docker archive into a SIF.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(realpath "${SCRIPT_DIR}/..")"
IMAGE_NAME="${IMAGE_NAME:-svnicp:humble-cuda124}"
output="${REPO_ROOT}/images/svnicp-humble-cuda124.sif"
runtime=""
archive_only=""
from_archive=""
skip_build=0
output_set=0
docker_args=()

usage() {
  cat <<'USAGE'
Usage: bash docker/build_singularity.sh [options] [-- DOCKER_BUILD_OPTIONS...]

Build docker/Dockerfile, export it with docker image save, then convert to SIF.
Requires Docker and either SingularityCE or Apptainer. No registry push is needed.

  --output FILE         SIF destination (default: images/svnicp-humble-cuda124.sif)
  --image NAME          Docker image tag (default: $IMAGE_NAME or svnicp:humble-cuda124)
  --runtime COMMAND     Singularity/Apptainer executable (auto-detected by default)
  --skip-docker-build   Convert an existing local Docker image without rebuilding
  --archive-only FILE   Build and save a Docker archive; no SIF runtime is needed
  --from-archive FILE   Convert an existing Docker archive; no Docker is needed
  -h, --help            Show this help

Existing destination files are never overwritten. Options after -- go to
docker build, e.g. -- --build-arg GTSAM_VERSION=4.2.0.
USAGE
}

die() { echo "Error: $*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "$1 requires a value"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) need_value "$@"; output="$2"; output_set=1; shift 2 ;;
    --image) need_value "$@"; IMAGE_NAME="$2"; shift 2 ;;
    --runtime) need_value "$@"; runtime="$2"; shift 2 ;;
    --archive-only) need_value "$@"; archive_only="$2"; shift 2 ;;
    --from-archive) need_value "$@"; from_archive="$2"; shift 2 ;;
    --skip-docker-build) skip_build=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; docker_args=("$@"); break ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

if [[ -n "$archive_only" ]]; then
  [[ -z "$from_archive" && -z "$runtime" && "$output_set" = 0 ]] ||
    die "--archive-only cannot be combined with --from-archive, --runtime or --output"
fi
if [[ -n "$from_archive" ]]; then
  [[ "$skip_build" = 0 && ${#docker_args[@]} = 0 ]] ||
    die "--from-archive cannot be combined with Docker build options"
  [[ -f "$from_archive" && -s "$from_archive" ]] || die "Docker archive is missing or empty: $from_archive"
  from_archive="$(realpath "$from_archive")"
fi
if [[ "$skip_build" = 1 && ${#docker_args[@]} -gt 0 ]]; then
  die "Docker build options have no effect with --skip-docker-build"
fi

destination="${archive_only:-$output}"
[[ ! -e "$destination" && ! -L "$destination" ]] || die "Destination already exists: $destination"
[[ "$destination" != */ ]] || die "Destination must be a file path"

# Check tools before starting the expensive CUDA/ROS build.
if [[ -z "$archive_only" ]]; then
  if [[ -z "$runtime" ]]; then
    if command -v singularity >/dev/null 2>&1; then
      runtime=singularity
    elif command -v apptainer >/dev/null 2>&1; then
      runtime=apptainer
    else
      die "Install SingularityCE/Apptainer, or use --archive-only FILE and convert on another machine"
    fi
  fi
  command -v "$runtime" >/dev/null 2>&1 || die "Runtime not found: $runtime"
fi
if [[ -z "$from_archive" ]]; then
  command -v docker >/dev/null 2>&1 || die "Docker is required to build/export the image"
  docker info >/dev/null
fi

mkdir -p "$(dirname "$destination")"
destination="$(cd "$(dirname "$destination")" && pwd)/$(basename "$destination")"
temporary="$(mktemp -d "$(dirname "$destination")/.svnicp-image.XXXXXX")"
trap 'rm -rf -- "$temporary"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -n "$from_archive" ]]; then
  archive="$from_archive"
else
  if [[ "$skip_build" = 0 ]]; then
    IMAGE_NAME="$IMAGE_NAME" bash "${SCRIPT_DIR}/build.sh" "${docker_args[@]}"
  fi
  # Pin the export to the inspected image even if another build retags it.
  image_id="$(docker image inspect --format '{{.Id}}' "$IMAGE_NAME")"
  archive="${temporary}/docker-image.tar"
  docker image save --output "$archive" "$image_id"
fi

if [[ -n "$archive_only" ]]; then
  artifact="$archive"
else
  artifact="${temporary}/image.sif"
  "$runtime" build "$artifact" "docker-archive://${archive}"
  "$runtime" inspect "$artifact" >/dev/null
fi
[[ -s "$artifact" ]] || die "No image was produced"
# A hard link publishes the complete file atomically without replacing a
# destination that another process created while this build was running.
ln -- "$artifact" "$destination"
echo "Created: $destination"
