FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# Force clean PATH — Docker Desktop WSL2 injects host PATH otherwise
ENV PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# ── System user ───────────────────────────────────────────────────────
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
    libfontconfig1-dev libxkbcommon-dev libgtk-3-dev libayatana-appindicator3-dev \
    fuse3 libfuse3-dev \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu's rclone package can lag behind upstream and has produced noisy FUSE
# xattr EIOs on long listings. Use the official static binary for mounts.
ARG DOWNLOAD_RCLONE_VERSION=current
RUN curl -fsSL "https://downloads.rclone.org/rclone-${DOWNLOAD_RCLONE_VERSION}-linux-amd64.zip" -o /tmp/rclone.zip \
    && unzip -q /tmp/rclone.zip -d /tmp/rclone-dist \
    && install -m 0755 /tmp/rclone-dist/*/rclone /usr/local/bin/rclone \
    && rclone version \
    && rm -rf /tmp/rclone.zip /tmp/rclone-dist

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
       GitPython tiktoken json-repair apscheduler diff-match-patch \
       click typer rich textual prompt-toolkit \
       celery dramatiq \
       docker boto3 google-cloud-storage azure-storage-blob \
       jinja2 mako \
       python-dateutil arrow pendulum \
       pydantic-settings loguru structlog \
       tenacity backoff \
       tqdm tabulate colorama \
       python-docx openpyxl python-pptx striprtf PyPDF2 ebooklib \
       playwright patchright 'scrapling[all]>=0.4.0' \
       sentence-transformers kafka-python paho-mqtt fastavro pyarrow \
       networkx graphifyy tree-sitter \
       pyfuse3 trio \
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
       zod ajv @openai/codex \
       # Database
       prisma drizzle-kit knex typeorm \
    && npm cache clean --force \
    && rm -rf /root/.npm /tmp/* /var/tmp/* \
    && rm -rf /var/lib/apt/lists/*

# ── Rust ───────────────────────────────────────────────────────────
ENV CARGO_HOME="/opt/local/rust/cargo"
ENV RUSTUP_HOME="/opt/local/rust/rustup"
RUN mkdir -p "$CARGO_HOME" "$RUSTUP_HOME" \
    && chown -R pawflow:pawflow /opt/local/rust
USER pawflow
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --default-toolchain stable --profile minimal
ENV PATH="/opt/local/rust/cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
RUN cargo install cargo-watch cargo-edit cargo-expand \
    && rustup component add clippy rustfmt rust-analyzer \
    && rm -rf "$CARGO_HOME/registry" "$CARGO_HOME/git" /tmp/* /var/tmp/*
USER root

# ── Go ──────────────────────────────────────────────────────────────
RUN curl -fsSL https://go.dev/dl/go1.23.6.linux-amd64.tar.gz | \
    tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV GOPATH="/opt/local/go-path"
ENV GOBIN="/opt/local/bin"
ENV GOCACHE="/tmp/pawflow-go-build"
ENV GOMODCACHE="/tmp/pawflow-go-mod"
RUN mkdir -p "$GOPATH" "$GOBIN" \
    && chown -R pawflow:pawflow "$GOPATH" "$GOBIN"
USER pawflow
RUN go install golang.org/x/tools/gopls@latest \
    && go install gotest.tools/gotestsum@latest \
    && go install github.com/air-verse/air@latest \
    && (chmod -R u+w "$GOMODCACHE" "$GOPATH/pkg/sumdb" 2>/dev/null || true) \
    && rm -rf "$GOMODCACHE" "$GOPATH/pkg/sumdb" /tmp/* /var/tmp/*
USER root

# Runtime caches must stay out of the persistent /home/pawflow Docker volume.
ENV XDG_CACHE_HOME="/tmp/pawflow-cache"
ENV HF_HOME="/tmp/pawflow-cache/huggingface"
ENV HUGGINGFACE_HUB_CACHE="/tmp/pawflow-cache/huggingface/hub"
ENV SENTENCE_TRANSFORMERS_HOME="/tmp/pawflow-cache/sentence-transformers"
ENV TRANSFORMERS_CACHE="/tmp/pawflow-cache/huggingface/transformers"

# ── Java (OpenJDK) + Kotlin ─────────────────────────────────────────
# ── Java (OpenJDK) ─────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jdk-headless maven gradle \
    && rm -rf /var/lib/apt/lists/*
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

# ── Database CLIs ───────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client mysql-client redis-tools \
    && rm -rf /var/lib/apt/lists/*

# ── code-server (VS Code in browser) ──────────────────────────────
RUN curl -fsSL https://code-server.dev/install.sh | sh
RUN rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

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
    ffmpeg \
    feh evince file-roller eog \
    nmap net-tools iproute2 dnsutils tcpdump iputils-ping httpie \
    tmux \
    meld ncdu galculator mousepad \
    && rm -rf /var/lib/apt/lists/*

# Browser runtime for manual desktop use only.
# Ubuntu 24.04's chromium-browser package is a Snap transition wrapper, which
# does not work reliably in this Docker desktop. XtraDeb provides a native
# Chromium deb for Noble, while Playwright remains managed by application code.
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common \
    && add-apt-repository -y ppa:xtradeb/apps \
    && apt-get update && apt-get install -y --no-install-recommends chromium \
    && printf '#!/bin/bash\nset -e\nprofile_dir="$PAWFLOW_CHROMIUM_USER_DATA_DIR"\ncache_dir="$PAWFLOW_CHROMIUM_CACHE_DIR"\nif [ -z "$profile_dir" ]; then profile_dir="$HOME/.chromium-profile"; fi\nif [ -z "$cache_dir" ]; then cache_dir=/tmp/pawflow-chromium-cache; fi\nmkdir -p "$profile_dir" "$cache_dir"\nexec /usr/bin/chromium --no-sandbox --disable-dev-shm-usage --user-data-dir="$profile_dir" --disk-cache-dir="$cache_dir" "$@"\n' \
       > /usr/local/bin/chromium \
    && chmod +x /usr/local/bin/chromium \
    && ln -sf /usr/local/bin/chromium /usr/local/bin/chromium-browser \
    && mkdir -p /usr/local/share/applications \
    && printf '[Desktop Entry]\nType=Application\nName=Chromium\nExec=/usr/local/bin/chromium %%U\nMimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;\n' \
       > /usr/local/share/applications/chromium.desktop \
    && update-alternatives --install /usr/bin/x-www-browser x-www-browser /usr/local/bin/chromium 200 \
    && update-alternatives --set x-www-browser /usr/local/bin/chromium \
    && mkdir -p /etc/xdg /etc/xdg/xfce4 /usr/share/xfce4/helpers \
    && printf '[Default Applications]\ntext/html=chromium.desktop\nx-scheme-handler/http=chromium.desktop\nx-scheme-handler/https=chromium.desktop\n' \
       > /etc/xdg/mimeapps.list \
    && printf 'WebBrowser=chromium\n' > /etc/xdg/xfce4/helpers.rc \
    && printf '[Desktop Entry]\nNoDisplay=true\nType=X-XFCE-Helper\nName=Chromium\nX-XFCE-Category=WebBrowser\nX-XFCE-Commands=/usr/local/bin/chromium\nX-XFCE-CommandsWithParameter=/usr/local/bin/chromium "%%s"\nIcon=chromium\n' \
       > /usr/share/xfce4/helpers/chromium.desktop \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache/ms-playwright

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

# Give pawflow user ownership of workspace and docker group.
# /cc_sessions, /filestore and /skills are the canonical mountpoints
# used by CC and the file tools. The relay process FUSE-mounts a single
# combined FS under /tmp (tmpfs, always writable) then `mount --bind`s
# its subtrees onto these canonical paths. We pre-create all three so
# the unprivileged pawflow user can mount onto them — root-owned dirs
# would refuse the bind-mount.
RUN mkdir -p /workspace /cc_sessions /filestore /skills /ms-playwright \
    && chown pawflow:pawflow /workspace /cc_sessions /filestore /skills /ms-playwright \
    && (groupadd -f docker; usermod -aG docker pawflow) 2>/dev/null || true

# ── PawFlow relay runtime mount point ──────────────────────────────
# Server/desktop launchers bind-mount the relay runtime code here so this image
# stays a clean dependency image and does not need rebuilds for relay code edits.
RUN mkdir -p /opt/pawflow \
    && chown pawflow:pawflow /opt/pawflow
# /workspace/.pylib is the host-mounted vendored deps dir — putting it on
# PYTHONPATH means any python invocation inside the container (including
# the relay itself and user scripts) sees the pinned/vendored packages
# (fastavro, pyarrow, tiktoken, regex, requests, certifi...) without
# re-running pip after each container restart.
ENV PYTHONPATH="/opt/pawflow:/workspace/.pylib"

# ── Init script: repair bind-mount permissions, then exec as pawflow ─────
RUN printf '#!/bin/bash\nset -e\nchronyd 2>/dev/null || true\nrm -rf /home/pawflow/.rustup /home/pawflow/.cache/go-build /home/pawflow/.cache/huggingface /home/pawflow/.config/chromium 2>/dev/null || true\nfor d in /workspace /cc_sessions /filestore /skills; do\n  mkdir -p "$d"\n  chown -R pawflow:pawflow "$d" 2>/dev/null || true\n  gid=$(stat -c %g "$d" 2>/dev/null || echo 1000)\n  group=$(getent group "$gid" | cut -d: -f1 || true)\n  if [ -z "$group" ]; then group=pfmount_$gid; groupadd -g "$gid" "$group" 2>/dev/null || true; fi\n  usermod -aG "$group" pawflow 2>/dev/null || true\n  chmod g+rwx "$d" 2>/dev/null || true\ndone\nexec sudo -E -u pawflow "$@"\n' > /usr/local/bin/init.sh \
    && chmod +x /usr/local/bin/init.sh

# ── Fix home ownership (usermod -m may keep old uid on preexisting files) ──
RUN chown pawflow:pawflow /home/pawflow \
    && find /home/pawflow -maxdepth 1 ! -user pawflow -exec chown -R pawflow:pawflow {} +

# ── Start as root; init.sh drops to pawflow after bind-mount repair ──────
USER root
ENV HOME="/home/pawflow"
ENV USER="pawflow"
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/init.sh"]
CMD ["bash"]
