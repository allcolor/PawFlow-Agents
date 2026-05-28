# Prompt: Install PawFlow Server With A Local Coding Agent

Give this prompt to Codex, Claude Code, Gemini CLI, or another local coding agent
running on the target machine.

```text
You are installing PawFlow Server on this machine.

Goal:
- Start PawFlow Server in Docker.
- Preserve data in persistent host directories.
- Stop once the PawFlow bootstrap wizard is reachable in a browser, or after the user asks you to help fill the wizard.
- Do not configure client relays during server installation.

Safety rules:
- Do not delete existing PawFlow data without explicit confirmation.
- Do not print secrets, tokens, OAuth client secrets, or private keys.
- Do not configure relays during this install; server workspace relays are spawned later by PawFlow and client relays are configured from webchat.
- Use the complete PawFlow installer so the server, CLI LLM, minimal relay, and full relay images are all prepared before first start.
- If the user provides a version, pass it as `--version VERSION`; otherwise install latest.
- Use `--from-source` only when the user explicitly wants source build semantics. With a version, it must checkout that exact git tag; without a version, it must checkout `main`.
- On Windows, use Docker Desktop Linux containers. Run the Bash installer from native Git Bash/native Bash or from WSL2 with Docker Desktop WSL integration.
- Use Docker volumes or bind mounts for persistent data.

Prerequisites to check first:
1. Detect OS and shell.
2. Verify `docker` exists.
3. Verify Docker daemon is reachable with `docker info`.
4. Verify the selected port is available. The port must be chosen explicitly during install.
5. Verify internet access to GitHub and the Docker registry.
6. Prefer running `bash scripts/doctor-pawflow.sh` when this repository is available; follow its OS-specific remediation instructions.
7. On Windows, run `powershell -ExecutionPolicy Bypass -File scripts/doctor-pawflow.ps1` as a host prerequisite check. Docker Desktop Linux containers are required. WSL2 integration is required only when installing from WSL.

Install path: complete from-scratch bootstrap
1. Clone or update the repository if needed:
   git clone https://github.com/allcolor/PawFlow-Agents.git ~/pawflow-src
   cd ~/pawflow-src
2. Run the installer with the selected port:
   bash scripts/install-pawflow.sh --port PORT
3. Confirm the installer first tries the prebuilt PawFlow server image, then builds from source only if needed, and builds these local runtime images before starting the server:
   - ghcr.io/allcolor/pawflow:latest, or ghcr.io/allcolor/pawflow:VERSION when a version is requested
   - pawflow-claude-code:latest
   - pawflow-relay-minimal:latest
   - pawflow-relay-dev:latest
4. If the user requested source mode, use:
   bash scripts/install-pawflow.sh --from-source --port PORT
   or:
   bash scripts/install-pawflow.sh --from-source --version VERSION --port PORT
5. If the user requested a native PawFlow server instead of a server container, use:
   bash scripts/install-pawflow.sh --native --port PORT
6. If release testing must require a pulled server image while still building local runtime images, use:
   bash scripts/install-pawflow.sh --pull-server --port PORT

After starting:
1. Follow logs:
   docker logs -f pawflow-server
2. Wait until PawFlow reports that the web server is listening.
3. Open:
   https://localhost:PORT
   The first run uses a self-signed bootstrap certificate; browser trust warnings are expected until the wizard configures final certificates.
4. Use the initial Private Gateway bootstrap key:
   RoyBetty
5. Tell the user that the bootstrap wizard must replace RoyBetty before finalization.
6. If the user asks you to help finish the wizard, collect the final Private Gateway key, admin username/password, LLM service id, provider, model, and optional API key. Do not print the passwords or API key back to the terminal.
7. After finalization, PawFlow creates the persistent Private Gateway, builtin auth gateway, admin user, selected LLM service, summarizer service (`summarizer_service`), variables, secrets, `pawflow-agent` deployment, and a starter conversation with `assistant` selected. Client relays are still configured later from webchat.

Expected final answer:
- Whether Docker prerequisites passed.
- Which install path was used.
- Container name.
- URL to open.
- Bootstrap key.
- Where persistent data is stored.
- Any manual action still required.
```
