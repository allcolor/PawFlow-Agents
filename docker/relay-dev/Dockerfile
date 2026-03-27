FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ── Core tools ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget ca-certificates gnupg2 software-properties-common \
    build-essential make cmake pkg-config autoconf automake libtool \
    jq sqlite3 libsqlite3-dev openssh-client unzip zip \
    && rm -rf /var/lib/apt/lists/*

# ── Python ──────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && pip3 install --break-system-packages --no-cache-dir \
       pip setuptools wheel pipx poetry \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js / TypeScript ────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g typescript ts-node tsx pnpm yarn \
    && rm -rf /var/lib/apt/lists/*

# ── Rust ────────────────────────────────────────────────────────────
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --default-toolchain stable --profile minimal \
    && echo 'source $HOME/.cargo/env' >> /etc/profile.d/cargo.sh
ENV PATH="/root/.cargo/bin:${PATH}"

# ── Go ──────────────────────────────────────────────────────────────
RUN curl -fsSL https://go.dev/dl/go1.23.6.linux-amd64.tar.gz | \
    tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/root/go"
ENV PATH="${GOPATH}/bin:${PATH}"

# ── Java (OpenJDK) + Kotlin + Scala ─────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jdk-headless maven gradle \
    && rm -rf /var/lib/apt/lists/*
# Kotlin
RUN curl -fsSL https://github.com/JetBrains/kotlin/releases/download/v2.1.10/kotlin-compiler-2.1.10.zip \
    -o /tmp/kotlin.zip && unzip -q /tmp/kotlin.zip -d /opt && rm /tmp/kotlin.zip
ENV PATH="/opt/kotlinc/bin:${PATH}"

# ── C# / .NET ──────────────────────────────────────────────────────
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 9.0 --install-dir /usr/share/dotnet \
    && ln -sf /usr/share/dotnet/dotnet /usr/bin/dotnet
ENV DOTNET_ROOT="/usr/share/dotnet"

# ── Ruby ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ruby ruby-dev \
    && rm -rf /var/lib/apt/lists/*

# ── PHP ─────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    php-cli php-mbstring php-xml php-curl php-zip \
    && rm -rf /var/lib/apt/lists/*

# ── Perl ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    perl cpanminus \
    && rm -rf /var/lib/apt/lists/*

# ── Lua ─────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    lua5.4 liblua5.4-dev luarocks \
    && rm -rf /var/lib/apt/lists/*

# ── Zig ─────────────────────────────────────────────────────────────
RUN curl -fsSL https://ziglang.org/download/0.13.0/zig-linux-x86_64-0.13.0.tar.xz | \
    tar -C /opt -xJf - && ln -sf /opt/zig-linux-x86_64-0.13.0/zig /usr/local/bin/zig

# ── PawFlow relay + SDK (for containerized mode) ────────────────────
COPY pawflow_relay.py /opt/pawflow/pawflow_relay.py
COPY fs_actions.py /opt/pawflow/fs_actions.py
COPY pawflow.py /opt/pawflow/pawflow.py
ENV PYTHONPATH="/opt/pawflow:${PYTHONPATH}"
ENV PAWFLOW_DOCKER_IMAGE="pawflow-relay-dev:latest"

# ── Cleanup ─────────────────────────────────────────────────────────
RUN apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /workspace
CMD ["bash"]
