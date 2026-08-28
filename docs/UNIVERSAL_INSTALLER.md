# PawFlow Universal Installer

The universal installer provides one resumable installation engine through a
terminal interface and a native Tkinter interface on Windows, Linux, and macOS.

## Entry points

Use the installed command:

```text
pawflow-install plan ...
pawflow-install run ...
pawflow-install resume OPERATION_ID
pawflow-install status [OPERATION_ID]
pawflow-install cancel OPERATION_ID
pawflow-install cleanup OPERATION_ID
pawflow-install gui
```

The source-tree equivalent is `python -m pawflow_installer`.

A complete request can be supplied as JSON with `--request FILE`. The schema is
implemented by `pawflow_installer.models.InstallRequest`. Required values are
never replaced with an anonymous server, implicit share, broad capability, or
public exposure mode.

## Safety model

`plan` is read-only. `run` and `resume` require explicit confirmation before
the first mutating step, unless `--yes` was supplied. SSH uses the system OpenSSH
client, an explicit host-key policy, and argv-safe commands. Tailscale is the
recommended remote reachability mode; public exposure is never provisioned by the
installer.

Operation state is stored atomically in the platform state directory. It contains
the validated non-secret request, request digest, target fingerprint, step state,
and redacted evidence. It never contains passwords, gateway keys, cookies, OAuth
tokens, session tokens, SSH private keys, or relay registration tokens.

Self-signed TLS certificates require confirmation of their exact SHA-256
fingerprint. The bootstrap Private Gateway key is intercepted from the canonical
installer output, shown through a non-persistent secret channel, and redacted from
events and state.

## Existing installer contracts

The engine calls the existing `scripts/install-pawflow.sh`,
`scripts/install-pawflow.ps1`, and doctor scripts. It does not reimplement the
PawFlow browser wizard. After the bootstrap endpoint is healthy it opens
`https://HOST:PORT/install`.

## Relay Desktop

Relay Desktop installation and association are optional and explicit. Every
shared path and capability is represented in the request. Multiple paths become
separate relay workspace profiles. Server login uses the existing browser login
flow.

Gateway keys, gateway cookies, and PawFlow session tokens are stored through the
operating-system credential vault using `keyring`; `servers.json` contains only
non-secret profile metadata and status booleans. If no secure credential backend
is available, Relay Desktop onboarding fails closed.

Completion requires `pawflow-relay verify WORKSPACE`, which asks the PawFlow
server for `relay_list_available` and succeeds only when the expected relay id is
reported with `connected=true`.

Autostart is opt-in. Generated definitions contain executable and workspace names
only, never secrets.

## Build

Run:

```text
python scripts/build-pawflow-universal-installer.py
```

The builder derives its version from `pyproject.toml` and creates a
platform-specific portable archive and adjacent `.sha256` manifest under
`dist/pawflow-installers/`. Each archive contains the single-file executable,
license, third-party notices, this guide, version marker, and the validated
`InstallRequest` JSON schema.

The release-assets workflow builds and smoke-tests these archives independently
on Windows, Linux, and macOS. Release publication and platform signing remain
separate release-procedure actions.
