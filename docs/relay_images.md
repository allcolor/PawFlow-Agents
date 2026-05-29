# Relay Image Profiles

PawFlow supports three relay image strategies:

- **Server workspace relays** use the official full image. They are centralized,
  controlled by the PawFlow administrator, and expose the broadest stable
  capability set for interactive workspaces.
- **Server minimal execution relays** use the official minimal server image. They
  are explicit flow execution targets for PFP flow tasks and ExecuteScript when
  the desired security boundary is "run inside a protected server Docker relay,
  never directly on the PawFlow server process or filesystem."
- **Client relays** are generated from a configurable profile. The user decides
  which languages, desktop tools, browser support, media tools, and GUI apps are
  installed on their machine.

The profile catalog lives in `config/relay_image_catalog.json`. It is designed
for both the installer wizard and command-line generation.

## Required Base

Every Docker relay image includes the non-optional `relay.base` feature. This is
not a development language preset; it is the minimum runtime PawFlow needs:

- `pawflow` user at UID/GID 1000 for bind-mounted workspace ownership
- Python runtime for `pawflow_relay_launcher.py` and relay workers
- FUSE support for server session and FileStore mounts
- `pyfuse3` and `trio`
- `rclone` for conversation-linked remote filesystem mounts under `/remote`
- `/workspace`, `/cc_sessions`, `/filestore`, and `/opt/pawflow` mountpoints
- basic network/TLS and shell tools
- Docker runtime requirements: `SYS_ADMIN`, `/dev/fuse`, and
  `apparmor:unconfined`

The base must stay small, but it cannot drop Python or FUSE without breaking
relay functionality.

## Optional Features

Optional features are individually selectable. The wizard should expose presets
for common cases and an advanced section with granular checkboxes.

Language features:

- `lang.python-dev`
- `lang.node`
- `lang.rust`
- `lang.go`
- `lang.java-kotlin`
- `lang.dotnet`
- `lang.ruby`
- `lang.php`
- `lang.perl`
- `lang.lua`
- `lang.zig`
- `lang.tree-sitter`

Desktop and browser features:

- `desktop.runtime`
- `desktop.audio`
- `desktop.ocr`
- `browser.chromium`

GUI applications are also individually selectable:

- `gui.gimp`
- `gui.inkscape`
- `gui.libreoffice-writer`
- `gui.libreoffice-calc`
- `gui.libreoffice-impress`
- `gui.libreoffice-draw`
- `gui.vlc`
- `gui.audacity`
- `gui.pdf-viewer`
- `gui.image-viewer`
- `gui.archive-manager`
- `gui.meld`
- `gui.mousepad`
- `gui.calculator`

Tool/media features:

- `dev.build-essential`
- `dev.shell-tools`
- `db.clients`
- `media.cli`
- `network.tools`
- `code-server`

Features may declare `implies`. For example, GUI apps imply
`desktop.runtime`, and audio/video GUI apps may also imply `desktop.audio`.
The wizard should show implied features as locked dependencies rather than
silently hiding them.

## Presets

The initial catalog ships these presets:

- `client-minimal` — relay base only
- `client-python` — Python development and AST tooling
- `client-frontend` — Node/frontend and Chromium
- `client-desktop` — desktop automation, audio, OCR, Chromium, media CLI
- `server-minimal` — protected server execution relay with `relay.base` only
- `server-full` — official full server relay capability set

Server workspace creation should use `server-full`. Server execution relay
creation should use `server-minimal`. Client relay creation should default to
`client-minimal` and let the user add capabilities.

The minimal server relay is the protected default execution image for
server-side scripts, tools, and package runtimes that do not receive an explicit
user relay. Build it on the PawFlow host with:

```bash
bash scripts/build-server-minimal-relay.sh
```

That script generates `docker/relay-generated/server-minimal/` from the
`server-minimal` profile and builds the tag configured by
`server_relay_minimal_image` (`pawflow-relay-minimal:latest` by default, or a
GHCR tag during prebuilt installs). Use `PAWFLOW_SERVER_MINIMAL_RELAY_IMAGE` to
build a custom tag. Docker installs pass `PAWFLOW_SERVER_RELAY_IMAGE` and
`PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE` into the server container so the server uses
the same prebuilt or locally built tags selected by the installer.

A flow may still choose a specific relay explicitly by setting a normal relay
parameter, for example `relay: "${relay_secure}"`, where `relay_secure` contains
the provisioned `srv_min_*` relay id. Different tasks in the same flow may use
different relay parameters.

## Generate a Client Relay Image

List available presets and features:

```bash
python scripts/generate-relay-image.py --list
```

Generate a preset:

```bash
python scripts/generate-relay-image.py \
  --profile client-frontend \
  --out docker/relay-generated/frontend \
  --image pawflow-relay:frontend
```

Add individual features:

```bash
python scripts/generate-relay-image.py \
  --profile client-minimal \
  --feature lang.node \
  --feature gui.gimp \
  --out docker/relay-generated/node-gimp \
  --image pawflow-relay:node-gimp
```

The generator writes:

- `Dockerfile`
- `manifest.json`
- `build.sh`
- `run-relay.sh`
- `runtime/` with the PawFlow relay launcher, filesystem actions, SDK shim, and `pawflow_relay` package copied into the Docker build context

`manifest.json` lists resolved features, implied dependencies, estimated size,
and required Docker runtime args. The installer wizard can use the same catalog
to generate a downloadable client relay installer.
