# Prompt: Install PawFlow Server With A Local Coding Agent

Give this prompt to Codex, Claude Code, Gemini CLI, or another local coding agent
running on the target machine.

```text
You are installing PawFlow Server on this machine.

Goal:
- Start PawFlow Server in Docker.
- Preserve data in persistent host directories.
- Stop once the PawFlow bootstrap wizard is reachable in a browser.
- Do not configure relays during server installation.

Safety rules:
- Do not delete existing PawFlow data without explicit confirmation.
- Do not print secrets, tokens, OAuth client secrets, or private keys.
- Prefer the published Docker image when available.
- If building from source, clone the official repository and build locally.
- Use Docker volumes or bind mounts for persistent data.

Prerequisites to check first:
1. Detect OS and shell.
2. Verify `docker` exists.
3. Verify Docker daemon is reachable with `docker info`.
4. Verify the selected port is available. Default port: 9090.
5. Verify internet access to GitHub and the Docker registry.

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
2. Build the server image:
   bash scripts/build-pawflow-docker.sh
3. Run the server:
   bash scripts/run-pawflow-docker.sh

After starting:
1. Follow logs:
   docker logs -f pawflow-server
2. Wait until PawFlow reports that the web server is listening.
3. Open:
   http://localhost:9090
4. Use the initial Private Gateway bootstrap key:
   RoyBetty
5. Tell the user that the bootstrap wizard must replace RoyBetty before finalization.
6. Stop here. The wizard will configure server settings, auth, LLM services, summarizer service, variables, secrets, CLI credential pools, and final flows.

Expected final answer:
- Whether Docker prerequisites passed.
- Which install path was used.
- Container name.
- URL to open.
- Bootstrap key.
- Where persistent data is stored.
- Any manual action still required.
```
