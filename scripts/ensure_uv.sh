#!/bin/bash
# Print a usable uv path; bootstrap a verified portable binary when needed.
set -euo pipefail

UV_VERSION=0.12.23
UV_DIR="${SVNICP_UV_DIR:-/work/tools/uv-${UV_VERSION}}"
install_managed=0
case "${1:-}" in
  --install) install_managed=1; shift ;;
  -h|--help)
    echo 'Usage: bash scripts/ensure_uv.sh [--install]'
    echo 'SVNICP_UV selects an existing executable; SVNICP_UV_DIR selects the managed install directory.'
    echo '--install prepares the pinned portable binary even when another uv is on PATH.'
    exit 0 ;;
esac
[[ $# = 0 ]] || { echo 'Error: unknown ensure_uv.sh argument' >&2; exit 1; }
die() { echo "Error: $*" >&2; exit 1; }
usable() { [[ -f "$1" && -x "$1" ]] && "$1" --version >/dev/null 2>&1; }

if [[ "$install_managed" = 0 ]]; then
  if [[ -n "${SVNICP_UV:-}" ]]; then
    selected="$(command -v "$SVNICP_UV" || true)"
    usable "$selected" || die "SVNICP_UV is not executable in this container: $SVNICP_UV"
    realpath "$selected"
    exit 0
  fi
  selected="$(command -v uv || true)"
  if usable "$selected"; then
    realpath "$selected"
    exit 0
  fi
  for selected in /usr/local/bin/uv "${HOME}/.local/bin/uv" "${HOME}/.cargo/bin/uv"; do
    if usable "$selected"; then
      realpath "$selected"
      exit 0
    fi
  done
fi
if [[ -e "$UV_DIR/uv" ]]; then
  usable "$UV_DIR/uv" || die "Managed uv is unusable; choose a new SVNICP_UV_DIR: $UV_DIR"
  realpath "$UV_DIR/uv"
  exit 0
fi
[[ "$(uname -s)" = Linux ]] || die 'Automatic uv installation supports Linux; set SVNICP_UV to an installed uv'
case "$(uname -m)" in
  x86_64|amd64) target=x86_64-unknown-linux-musl ;;
  aarch64|arm64) target=aarch64-unknown-linux-musl ;;
  *) die 'Unsupported CPU architecture for automatic uv installation' ;;
esac
for tool in curl sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || die "uv bootstrap requires $tool"
done
mkdir -p -- "$UV_DIR"
UV_DIR="$(realpath "$UV_DIR")"
temporary="$(mktemp -d "${UV_DIR}/.install.XXXXXX")"
trap 'rm -rf -- "$temporary"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
archive="uv-${target}.tar.gz"
base="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}"
echo "Installing verified uv ${UV_VERSION} into ${UV_DIR}" >&2
curl -fLsS "${base}/${archive}" -o "${temporary}/${archive}"
curl -fLsS "${base}/${archive}.sha256" -o "${temporary}/${archive}.sha256"
mapfile -t checksums < "${temporary}/${archive}.sha256"
[[ ${#checksums[@]} = 1 ]] || die 'Unexpected uv checksum file format'
read -r checksum filename extra <<< "${checksums[0]}"
[[ "$checksum" =~ ^[[:xdigit:]]{64}$ && "$filename" = "$archive" && -z "$extra" ]] ||
  die 'Unexpected uv checksum entry'
(cd "$temporary" && sha256sum --check "${archive}.sha256") >&2
tar -xzf "${temporary}/${archive}" -C "$temporary"
candidate="${temporary}/uv-${target}/uv"
usable "$candidate" || die 'Downloaded uv cannot execute in this container'
# Publish without replacing an existing install, including a concurrent install.
if ! ln -- "$candidate" "$UV_DIR/uv" 2>/dev/null; then
  usable "$UV_DIR/uv" || die "Cannot publish uv in $UV_DIR"
fi
printf '%s\n' "$UV_DIR/uv"
