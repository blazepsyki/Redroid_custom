FROM ubuntu:22.04@sha256:e0f80c829509ef4df9e76bd8944eb7b8ec25cbb379b112a7f9448b2c77a77c9b

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bc bison build-essential ca-certificates ccache curl file flex fontconfig \
        git git-lfs gnupg jq lib32z1-dev libc6-dev-i386 libelf-dev libgl1-mesa-dev \
        libssl-dev libx11-dev libxml2-utils locales lz4 patch python-is-python3 \
        python3 repo rsync unzip x11proto-core-dev xsltproc zip zlib1g-dev zstd \
    && locale-gen en_US.UTF-8 \
    && mkdir -p /opt/builder \
    && dpkg-query -W -f='${Package}\t${Version}\n' > /opt/builder/packages.tsv \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
WORKDIR /src
CMD ["/bin/bash"]
