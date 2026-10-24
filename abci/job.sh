#!/bin/bash
set -euo pipefail

# The batch submitter uses a private working directory for each PBS job.
# PBS supplies this path without exporting custom paths through qsub -v.
if [[ -n "${PBS_O_WORKDIR:-}" && -f "${PBS_O_WORKDIR}/oxford-task.sh" ]]; then
  exec bash "${PBS_O_WORKDIR}/oxford-task.sh"
fi

# Preserve direct single-sequence submission from the repository root.
repo_root="${SVNICP_REPO_ROOT:-${PBS_O_WORKDIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}"
exec bash "${repo_root}/docker/start_singularity.sh" \
  --image "${SIF_IMAGE:-${repo_root}/images/svnicp-humble-cuda124-uv.sif}" \
  -- bash scripts/my_startup.sh
