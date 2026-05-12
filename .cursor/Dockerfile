FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
    iptables \
    python3 \
    python3-pip \
    python3-venv \
    sudo \
    uidmap \
    fuse-overlayfs \
    && rm -rf /var/lib/apt/lists/*

RUN install -m 0755 -d /etc/apt/keyrings && \
    curl --retry 3 --retry-delay 5 -fsSL https://download.docker.com/linux/ubuntu/gpg | \
      gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu noble stable" \
      > /etc/apt/sources.list.d/docker.list && \
    apt-get update && apt-get install -y --no-install-recommends \
      containerd.io \
      docker-buildx-plugin \
      docker-ce \
      docker-ce-cli \
      docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/docker && \
    printf '%s\n' \
      '{' \
      '  "storage-driver": "fuse-overlayfs"' \
      '}' > /etc/docker/daemon.json

RUN update-alternatives --set iptables /usr/sbin/iptables-legacy || true && \
    update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy || true

RUN id -u ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu && \
    groupadd -f docker && \
    usermod -aG docker ubuntu && \
    usermod -aG sudo ubuntu && \
    echo "ubuntu ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ubuntu
