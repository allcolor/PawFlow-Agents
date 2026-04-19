FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
# Force clean PATH — Docker Desktop WSL2 injects host PATH otherwise
ENV PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# ── System user (created early so Rust/Go install into /home/pawflow) ──
# ubuntu:24.04 ships with a default user 'ubuntu' at UID 1000. We rename it to
# 'pawflow' so our user owns UID 1000 — this matches the typical Linux/WSL
# first-user uid, so bind-mounted host files aren't stuck as a different uid
# and pawflow can read/write them. (claude-code's Dockerfile does the same
# trick with node:22-slim's 'node' user.) If the base image ever drops the
# ubuntu user, fall back to creating pawflow fresh at UID 1000.
RUN apt-get update && apt-get install -y --no-install-recommends sudo \
    && rm -rf /var/lib/apt/lists/* \
    && if id -u ubuntu >/dev/null 2>&1; then \
           usermod -l pawflow -d /home/pawflow -m ubuntu \
        && groupmod -n pawflow ubuntu \
        && usermod -aG sudo pawflow; \
       else \
           groupadd -g 1000 pawflow \
        && useradd -u 1000 -g 1000 -m -s /bin/bash -G sudo pawflow; \
       fi \
    && echo 'pawflow ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/pawflow \
    && chmod 440 /etc/sudoers.d/pawflow

# ── Core tools ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget ca-certificates gnupg2 software-properties-common \
    build-essential make cmake pkg-config autoconf automake libtool \
    jq sqlite3 libsqlite3-dev openssh-client unzip zip \
    ripgrep fd-find bat tree htop strace ltrace \
    file less man-db chrony \
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
       python-docx openpyxl python-pptx striprtf PyPDF2 ebooklib \
       networkx graphifyy tree-sitter \
       tree-sitter-python tree-sitter-javascript tree-sitter-typescript \
       tree-sitter-go tree-sitter-rust tree-sitter-java \
       tree-sitter-c tree-sitter-cpp tree-sitter-ruby \
       tree-sitter-c-sharp tree-sitter-kotlin tree-sitter-scala \
       tree-sitter-php tree-sitter-swift tree-sitter-lua \
       tree-sitter-zig tree-sitter-powershell tree-sitter-elixir \
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

# ── Rust (installed as pawflow user → /home/pawflow/.cargo) ────────
USER pawflow
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --default-toolchain stable --profile default
ENV PATH="/home/pawflow/.cargo/bin:${PATH}"
RUN cargo install cargo-watch cargo-edit cargo-expand \
    && rustup component add clippy rustfmt rust-analyzer
USER root

# ── Go ──────────────────────────────────────────────────────────────
RUN curl -fsSL https://go.dev/dl/go1.23.6.linux-amd64.tar.gz | \
    tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/home/pawflow/go"
ENV PATH="${GOPATH}/bin:${PATH}"
# Go dev tools (as pawflow so binaries land in /home/pawflow/go/bin)
USER pawflow
RUN go install golang.org/x/tools/gopls@latest \
    && go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest \
    && go install gotest.tools/gotestsum@latest \
    && go install github.com/air-verse/air@latest
USER root

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

ENV PAWFLOW_DOCKER_IMAGE="pawflow-relay-dev:latest"

# ── Desktop environment (VNC + audio) ───────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb xfce4 xfce4-goodies dbus-x11 \
    x11vnc novnc websockify \
    pulseaudio pulseaudio-utils \
    xdotool xclip xsel scrot wmctrl \
    fonts-dejavu fonts-liberation fonts-noto-color-emoji \
    libgtk-3-0 libnotify4 \
    tesseract-ocr tesseract-ocr-fra \
    && pip3 install --break-system-packages --no-cache-dir \
       mss pyautogui pytesseract opencv-python-headless \
    && apt-get purge -y xfce4-pulseaudio-plugin 2>/dev/null || true \
    && rm -rf /var/lib/apt/lists/*

# ── Desktop apps: image, video, audio, office, viewers ─────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    gimp gimp-plugin-registry inkscape imagemagick \
    ffmpeg vlc audacity \
    libreoffice-calc libreoffice-writer libreoffice-impress libreoffice-draw \
    feh evince file-roller eog \
    nmap net-tools iproute2 dnsutils tcpdump iputils-ping httpie \
    tmux \
    meld ncdu galculator mousepad \
    && rm -rf /var/lib/apt/lists/*

# ── VS Code (desktop app) ────────────────────────────────────────
RUN curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/code stable main" \
       > /etc/apt/sources.list.d/vscode.list \
    && apt-get update && apt-get install -y --no-install-recommends code \
    && rm -rf /var/lib/apt/lists/* \
    # Wrapper: Docker containers can't use Electron's sandbox
    && mv /usr/share/code/code /usr/share/code/code-real \
    && printf '#!/bin/bash\nexec /usr/share/code/code-real --no-sandbox "$@"\n' \
       > /usr/share/code/code \
    && chmod +x /usr/share/code/code

# ── Google Chrome (real deb — chromium-browser is snap stub on 24.04) ──
RUN curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
       -o /tmp/chrome.deb \
    && apt-get update && apt-get install -y /tmp/chrome.deb \
    && rm /tmp/chrome.deb && rm -rf /var/lib/apt/lists/* \
    # Wrapper: Docker containers can't use Chrome's sandbox
    && mv /opt/google/chrome/google-chrome /opt/google/chrome/google-chrome-real \
    && printf '#!/bin/bash\nrm -f "$HOME/.chrome-profile/SingletonLock" "$HOME/.chrome-profile/SingletonCookie" "$HOME/.chrome-profile/SingletonSocket" 2>/dev/null\nexec /opt/google/chrome/google-chrome-real --no-sandbox --disable-gpu --disable-dev-shm-usage --user-data-dir=$HOME/.chrome-profile "$@"\n' \
       > /opt/google/chrome/google-chrome \
    && chmod +x /opt/google/chrome/google-chrome \
    && ln -sf /usr/bin/google-chrome-stable /usr/bin/chromium-browser

# ── Docker CLI (for DinD — exec shells via docker.sock) ────────────
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu noble stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# ── Cleanup ─────────────────────────────────────────────────────────
RUN apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Refresh desktop menu cache so all .desktop files appear in XFCE
RUN update-desktop-database /usr/share/applications 2>/dev/null || true

# Give pawflow user ownership of workspace and docker group
RUN mkdir -p /workspace && chown pawflow:pawflow /workspace \
    && (groupadd -f docker; usermod -aG docker pawflow) 2>/dev/null || true

# ── PawFlow relay + SDK (LAST — changes often, don't bust cache) ──
COPY pawflow_relay.py /opt/pawflow/pawflow_relay.py
COPY fs_common.py /opt/pawflow/fs_common.py
COPY fs_actions.py /opt/pawflow/fs_actions.py
COPY fs_exec.py /opt/pawflow/fs_exec.py
COPY fs_screen.py /opt/pawflow/fs_screen.py
COPY fs_mcp.py /opt/pawflow/fs_mcp.py
COPY fs_http.py /opt/pawflow/fs_http.py
COPY pawflow.py /opt/pawflow/pawflow.py
COPY audio_capture.py /opt/pawflow/audio_capture.py
# /workspace/.pylib is the host-mounted vendored deps dir — putting it on
# PYTHONPATH means any python invocation inside the container (including
# the relay itself and user scripts) sees the pinned/vendored packages
# (fastavro, pyarrow, tiktoken, regex, requests, certifi...) without
# re-running pip after each container restart.
ENV PYTHONPATH="/opt/pawflow:/workspace/.pylib"

# ── Init script: start chronyd then exec CMD ──────────────────────
RUN printf '#!/bin/bash\nsudo chronyd 2>/dev/null || true\nexec "$@"\n' > /usr/local/bin/init.sh \
    && chmod +x /usr/local/bin/init.sh

# ── Fix home ownership (usermod -m may keep old uid on preexisting files) ──
RUN chown pawflow:pawflow /home/pawflow \
    && find /home/pawflow -maxdepth 1 ! -user pawflow -exec chown -R pawflow:pawflow {} +

# ── Run as pawflow ──────────────────────────────────────────────────
USER pawflow
ENV HOME="/home/pawflow"
ENV USER="pawflow"
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/init.sh"]
CMD ["bash"]
