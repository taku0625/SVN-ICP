#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(realpath "${SCRIPT_DIR}/..")"
DATASET_DIR="${DATASET_DIR:-/home/nakao/dataset/rosbag/iros2026/niwa2}"
DATASET_MOUNT_PATH="${DATASET_MOUNT_PATH:-/storage/rosbag/niwa2}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/output}"
IMAGE_NAME="${IMAGE_NAME:-svnicp:humble-cuda124}"
VOLUME_PREFIX="${VOLUME_PREFIX:-svnicp}"
NVIDIA_LIB_DIR="${NVIDIA_LIB_DIR:-/usr/lib/x86_64-linux-gnu}"
NVIDIA_SMI="${NVIDIA_SMI:-/usr/bin/nvidia-smi}"

if [ ! -d "${DATASET_DIR}" ]; then
  echo "Required dataset directory does not exist: ${DATASET_DIR}" >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

DOCKER_ARGS=(
  -i
  --rm
  --privileged
  --network host
  --ipc host
  --runtime=runc
  -e NVIDIA_VISIBLE_DEVICES=all
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics
  -e CMAKE_CUDA_ARCHITECTURES="${CMAKE_CUDA_ARCHITECTURES:-75;80;86;89;90}"
  -v "${REPO_ROOT}:/root/svnicp_ws/src/SVN-ICP"
  -v "${DATASET_DIR}:${DATASET_MOUNT_PATH}:ro"
  -v "${OUTPUT_DIR}:/output"
  -v "${VOLUME_PREFIX}_build:/root/svnicp_ws/build"
  -v "${VOLUME_PREFIX}_install:/root/svnicp_ws/install"
  -v "${VOLUME_PREFIX}_log:/root/svnicp_ws/log"
)

if [ -t 0 ]; then
  DOCKER_ARGS+=(-t)
fi

if [ -x "${NVIDIA_SMI}" ]; then
  DOCKER_ARGS+=(-v "${NVIDIA_SMI}:/usr/bin/nvidia-smi:ro")
fi

for lib in libcuda.so.1 libnvidia-ml.so.1 libnvidia-ptxjitcompiler.so.1 libnvidia-nvvm.so.4; do
  host_lib="${NVIDIA_LIB_DIR}/${lib}"
  if [ -e "${host_lib}" ]; then
    host_lib="$(realpath "${host_lib}")"
    DOCKER_ARGS+=(-v "${host_lib}:/usr/lib/x86_64-linux-gnu/${lib}:ro")
  fi
done

if [ -n "${DISPLAY:-}" ] && [ -d /tmp/.X11-unix ]; then
  DOCKER_ARGS+=(
    -e "DISPLAY=${DISPLAY}"
    -e QT_X11_NO_MITSHM=1
    -v /tmp/.X11-unix:/tmp/.X11-unix:rw
  )

  XAUTH_SOURCE="${XAUTHORITY:-${HOME}/.Xauthority}"
  if command -v xauth >/dev/null 2>&1; then
    XAUTH_DOCKER="/tmp/${VOLUME_PREFIX}.docker.xauth"
    XAUTH_NLIST="$(mktemp "/tmp/${VOLUME_PREFIX}.xauth.XXXXXX")"
    touch "${XAUTH_DOCKER}"
    chmod a+r "${XAUTH_DOCKER}"
    if xauth nlist "${DISPLAY}" > "${XAUTH_NLIST}" 2>/dev/null \
      && [ -s "${XAUTH_NLIST}" ] \
      && sed -e 's/^..../ffff/' "${XAUTH_NLIST}" | xauth -f "${XAUTH_DOCKER}" nmerge - >/dev/null 2>&1; then
      DOCKER_ARGS+=(
        -e XAUTHORITY=/tmp/.docker.xauth
        -v "${XAUTH_DOCKER}:/tmp/.docker.xauth:ro"
      )
    elif [ -f "${XAUTH_SOURCE}" ]; then
      DOCKER_ARGS+=(
        -e XAUTHORITY=/tmp/.docker.xauth
        -v "${XAUTH_SOURCE}:/tmp/.docker.xauth:ro"
      )
    fi
    rm -f "${XAUTH_NLIST}"
  elif [ -f "${XAUTH_SOURCE}" ]; then
    DOCKER_ARGS+=(
      -e XAUTHORITY=/tmp/.docker.xauth
      -v "${XAUTH_SOURCE}:/tmp/.docker.xauth:ro"
    )
  fi
fi

if [ "$#" -gt 0 ]; then
  CONTAINER_CMD=("$@")
else
  CONTAINER_CMD=(bash)
fi

NVIDIA_DEVICE_INIT='
set -e

create_node() {
  path="$1"
  major="$2"
  minor="$3"

  if [ ! -e "${path}" ]; then
    mknod -m 666 "${path}" c "${major}" "${minor}"
  fi
  chmod 666 "${path}" 2>/dev/null || true
}

nvidia_major="$(awk '\''$2 == "nvidia" { print $1; exit }'\'' /proc/devices)"
uvm_major="$(awk '\''$2 == "nvidia-uvm" { print $1; exit }'\'' /proc/devices)"

if [ -n "${nvidia_major}" ]; then
  create_node /dev/nvidiactl "${nvidia_major}" 255
  create_node /dev/nvidia-modeset "${nvidia_major}" 254
  gpu_found=0
  for info in /proc/driver/nvidia/gpus/*/information; do
    [ -e "${info}" ] || continue
    minor="$(awk -F: '\''$1 == "Device Minor" { gsub(/[[:space:]]/, "", $2); print $2; exit }'\'' "${info}")"
    if [ -n "${minor}" ]; then
      create_node "/dev/nvidia${minor}" "${nvidia_major}" "${minor}"
      gpu_found=1
    fi
  done
  if [ "${gpu_found}" -eq 0 ]; then
    create_node /dev/nvidia0 "${nvidia_major}" 0
  fi
fi

if [ -n "${uvm_major}" ]; then
  create_node /dev/nvidia-uvm "${uvm_major}" 0
  create_node /dev/nvidia-uvm-tools "${uvm_major}" 1
fi

exec "$@"
'

docker run "${DOCKER_ARGS[@]}" "${IMAGE_NAME}" bash -lc "${NVIDIA_DEVICE_INIT}" -- "${CONTAINER_CMD[@]}"
