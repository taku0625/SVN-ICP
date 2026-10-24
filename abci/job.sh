#!/bin/sh

bash ~/docker/SVN-ICP/docker/start_singularity.sh \
  --image ~/docker/SVN-ICP/images/svnicp-humble-cuda124-uv.sif \
  -- bash ~/docker/SVN-ICP/scripts/my_startup.sh