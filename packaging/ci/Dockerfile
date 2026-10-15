# Match the release runner's glibc baseline for local package verification.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config libgtk-3-dev libwebkit2gtk-4.1-dev librsvg2-dev \
    libssl-dev libwayland-dev clang lld aapt apksigner patchelf rpm zstd cpio \
    python3 openjdk-17-jdk-headless patch git ca-certificates curl unzip \
    xvfb xauth dbus-x11 libayatana-appindicator3-1 \
    && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH=/usr/lib/jvm/java-17-openjdk-amd64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
