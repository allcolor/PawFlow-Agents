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
- Prefer the published Docker image when available.
- If building from source, clone the official repository and build locally.
- Use Docker volumes or bind mounts for persistent data.

Prerequisites to check first:
1. Detect OS and shell.
2. Verify `docker` exists.
3. Verify Docker daemon is reachable with `docker info`.
4. Verify the selected port is available. Default port: 9090.
5. Verify internet access to GitHub and the Docker registry.
6. Prefer running `bash scripts/doctor-pawflow.sh` when this repository is available; follow its OS-specific remediation instructions.
7. On Windows, PawFlow requires WSL2 plus Docker Desktop with WSL integration. Run `powershell -ExecutionPolicy Bypass -File scripts/doctor-pawflow.ps1` only as a host prerequisite check; if it reports missing WSL2 or Docker Desktop WSL integration, stop and ask the user to install/enable them, then run the install commands inside WSL.

Install path A: published image, preferred
1. Create persistent directories:
   - ~/pawflow/data
   - ~/pawflow/config
   - ~/pawflow/certs
   - ~/pawflow/logs
2. Pull the image:
   docker pull ghcr.io/allcolor/pawflow:latest
3. Run the container:
   docker run -d \
     --name pawflow-server \
     --restart unless-stopped \
     -p 9090:9090 \
     -v "$HOME/pawflow/data:/app/data" \
     -v "$HOME/pawflow/config:/app/config" \
     -v "$HOME/pawflow/certs:/app/certs" \
     -v "$HOME/pawflow/logs:/app/logs" \
     -e PAWFLOW_BOOTSTRAP_GATEWAY_KEY=RoyBetty \
     ghcr.io/allcolor/pawflow:latest \
     python cli.py start --host 0.0.0.0 --port 9090

Install path B: build from source
1. Clone or update the repository:
   git clone https://github.com/allcolor/PawFlow-Agents.git ~/pawflow-src
   cd ~/pawflow-src
2. Run the host prerequisite doctor:
   bash scripts/doctor-pawflow.sh --source
3. Build the server image:
   bash scripts/build-pawflow-docker.sh
4. Run the server:
   bash scripts/run-pawflow-docker.sh

After starting:
1. Follow logs:
   docker logs -f pawflow-server
2. Wait until PawFlow reports that the web server is listening.
3. Open:
   https://localhost:9090
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
