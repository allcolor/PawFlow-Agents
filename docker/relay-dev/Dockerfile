FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ── Core tools ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget ca-certificates gnupg2 software-properties-common \
    build-essential make cmake pkg-config autoconf automake libtool \
    jq sqlite3 libsqlite3-dev openssh-client unzip zip \
    ripgrep fd-find bat tree htop strace ltrace \
    file less man-db \
    libffi-dev libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
    libncurses-dev libgdbm-dev liblzma-dev tk-dev uuid-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Python ──────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && apt-get remove -y python3-blinker || true \
    && pip3 install --break-system-packages --no-cache-dir --ignore-installed \
       pip setuptools wheel pipx poetry \
       pytest pytest-cov pytest-asyncio pytest-mock pytest-xdist \
       hypothesis tox nox \
       mypy ruff black isort bandit pylint flake8 \
       pyright types-requests types-pyyaml \
       requests httpx aiohttp websockets grpcio grpcio-tools \
       pydantic fastapi uvicorn flask django starlette \
       sqlalchemy alembic psycopg2-binary pymongo redis asyncpg \
       pillow numpy pandas matplotlib scipy scikit-learn \
       pyyaml toml python-dotenv msgpack orjson \
       beautifulsoup4 lxml html5lib cssselect \
       cryptography paramiko bcrypt pyjwt \
       click typer rich textual prompt-toolkit \
       celery dramatiq \
       docker boto3 google-cloud-storage azure-storage-blob \
       jinja2 mako \
       python-dateutil arrow pendulum \
       pydantic-settings loguru structlog \
       tenacity backoff \
       tqdm tabulate colorama \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js / TypeScript ────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g \
       typescript ts-node tsx pnpm yarn \
       # Testing
       jest vitest mocha chai @types/jest @types/mocha \
       # Linting / formatting
       eslint prettier @typescript-eslint/parser @typescript-eslint/eslint-plugin \
       # Build tools
       esbuild rollup webpack webpack-cli vite \
       # Frameworks
       express @nestjs/cli next create-react-app \
       # Utilities
       nodemon concurrently pm2 http-server \
       zod ajv \
       # Database
       prisma drizzle-kit knex typeorm \
    && rm -rf /var/lib/apt/lists/*

# ── Rust ────────────────────────────────────────────────────────────
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --default-toolchain stable --profile default \
    && echo 'source $HOME/.cargo/env' >> /etc/profile.d/cargo.sh
ENV PATH="/root/.cargo/bin:${PATH}"
# Rust dev tools
RUN cargo install cargo-watch cargo-edit cargo-expand \
    && rustup component add clippy rustfmt rust-analyzer

# ── Go ──────────────────────────────────────────────────────────────
RUN curl -fsSL https://go.dev/dl/go1.23.6.linux-amd64.tar.gz | \
    tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/root/go"
ENV PATH="${GOPATH}/bin:${PATH}"
# Go dev tools
RUN go install golang.org/x/tools/gopls@latest \
    && go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest \
    && go install gotest.tools/gotestsum@latest \
    && go install github.com/air-verse/air@latest

# ── Java (OpenJDK) + Kotlin ─────────────────────────────────────────
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
    ruby ruby-dev ruby-bundler \
    && gem install rspec rubocop solargraph rake \
    && rm -rf /var/lib/apt/lists/*

# ── PHP ─────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    php-cli php-mbstring php-xml php-curl php-zip php-sqlite3 php-mysql php-pgsql \
    && curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer \
    && rm -rf /var/lib/apt/lists/*

# ── Perl ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    perl cpanminus libtest-simple-perl \
    && rm -rf /var/lib/apt/lists/*

# ── Lua ─────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    lua5.4 liblua5.4-dev luarocks \
    && luarocks install busted \
    && rm -rf /var/lib/apt/lists/*

# ── Zig ─────────────────────────────────────────────────────────────
RUN curl -fsSL https://ziglang.org/download/0.13.0/zig-linux-x86_64-0.13.0.tar.xz | \
    tar -C /opt -xJf - && ln -sf /opt/zig-linux-x86_64-0.13.0/zig /usr/local/bin/zig

# ── Database CLIs ───────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client mysql-client redis-tools \
    && rm -rf /var/lib/apt/lists/*

# ── code-server (VS Code in browser) ──────────────────────────────
RUN curl -fsSL https://code-server.dev/install.sh | sh

# ── PawFlow relay + SDK (for containerized mode) ────────────────────
COPY pawflow_relay.py /opt/pawflow/pawflow_relay.py
COPY fs_actions.py /opt/pawflow/fs_actions.py
COPY fs_exec.py /opt/pawflow/fs_exec.py
COPY fs_screen.py /opt/pawflow/fs_screen.py
COPY fs_mcp.py /opt/pawflow/fs_mcp.py
COPY pawflow.py /opt/pawflow/pawflow.py
ENV PYTHONPATH="/opt/pawflow:${PYTHONPATH}"
ENV PAWFLOW_DOCKER_IMAGE="pawflow-relay-dev:latest"

# ── Cleanup ─────────────────────────────────────────────────────────
RUN apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /workspace
CMD ["bash"]
