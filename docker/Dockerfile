FROM nvidia/cuda:12.4.0-devel-ubuntu22.04

SHELL ["/bin/bash", "-c"]

ARG ROS_DISTRO=humble
ARG TORCH_VERSION=2.5.1
ARG TORCH_CUDA=cu124
ARG GTSAM_VERSION=4.2.0
ARG UV_VERSION=0.12.23
ARG CMAKE_CUDA_ARCHITECTURES=75;80;86;89;90

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV ROS_DISTRO=${ROS_DISTRO}
ENV SVNICP_WS=/root/svnicp_ws
ENV CMAKE_CUDA_ARCHITECTURES=${CMAKE_CUDA_ARCHITECTURES}

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    git \
    gnupg \
    locales \
    lsb-release \
    python3-dev \
    python3-pip \
    software-properties-common \
    unzip \
    wget \
    && locale-gen en_US en_US.UTF-8 \
    && update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
    && add-apt-repository universe \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && /usr/local/bin/uv --version

RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo ${UBUNTU_CODENAME}) main" \
    > /etc/apt/sources.list.d/ros2.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    libboost-all-dev \
    libeigen3-dev \
    libmetis-dev \
    libpcl-dev \
    libtbb-dev \
    robin-map-dev \
    ros-${ROS_DISTRO}-ament-cmake \
    ros-${ROS_DISTRO}-geometry-msgs \
    ros-${ROS_DISTRO}-nav-msgs \
    ros-${ROS_DISTRO}-pcl-conversions \
    ros-${ROS_DISTRO}-pcl-msgs \
    ros-${ROS_DISTRO}-rclcpp \
    ros-${ROS_DISTRO}-ros-base \
    ros-${ROS_DISTRO}-rosbag2 \
    ros-${ROS_DISTRO}-rosbag2-storage-mcap \
    ros-${ROS_DISTRO}-rviz2 \
    ros-${ROS_DISTRO}-sensor-msgs \
    ros-${ROS_DISTRO}-std-msgs \
    ros-${ROS_DISTRO}-tf2-ros \
    python3-colcon-common-extensions \
    python3-numpy \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q --show-progress \
    "https://download.pytorch.org/libtorch/${TORCH_CUDA}/libtorch-cxx11-abi-shared-with-deps-${TORCH_VERSION}%2B${TORCH_CUDA}.zip" \
    -O /tmp/libtorch.zip \
    && unzip -q /tmp/libtorch.zip -d /opt \
    && rm /tmp/libtorch.zip

ENV Torch_DIR=/opt/libtorch/share/cmake/Torch
ENV CMAKE_PREFIX_PATH=${Torch_DIR}
ENV LD_LIBRARY_PATH=/opt/libtorch/lib:${LD_LIBRARY_PATH}

WORKDIR /tmp
RUN git clone --depth 1 --branch ${GTSAM_VERSION} https://github.com/borglab/gtsam.git \
    && cmake -S gtsam -B gtsam/build \
    -DCMAKE_BUILD_TYPE=Release \
    -DGTSAM_BUILD_TESTS=OFF \
    -DGTSAM_BUILD_EXAMPLES_ALWAYS=OFF \
    -DGTSAM_BUILD_WRAP=OFF \
    -DGTSAM_BUILD_UNSTABLE=ON \
    -DGTSAM_USE_SYSTEM_EIGEN=ON \
    -DGTSAM_BUILD_WITH_MARCH_NATIVE=OFF \
    && cmake --build gtsam/build --target install --parallel "$(nproc)" \
    && ldconfig \
    && rm -rf /tmp/gtsam

RUN mkdir -p ${SVNICP_WS}/src

RUN printf '%s\n' \
    '#!/bin/bash' \
    'set -e' \
    'source "/opt/ros/${ROS_DISTRO}/setup.bash"' \
    'if [ -f "/root/svnicp_ws/install/setup.bash" ]; then source "/root/svnicp_ws/install/setup.bash"; fi' \
    'exec "$@"' \
    > /ros_entrypoint.sh \
    && chmod +x /ros_entrypoint.sh

WORKDIR ${SVNICP_WS}
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
