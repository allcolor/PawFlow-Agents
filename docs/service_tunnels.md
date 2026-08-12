# Service Tunnels

Service Tunnels provide user-scoped TCP access between two PawFlow relays. A
service relay exposes one explicitly approved local service through FRP, and an
access relay opens a loopback-only listener for the user. A typical example is
connecting WinSCP to `127.0.0.1:22022` on a laptop while the approved SSH
service runs on a remote PawFlow relay.

Service Tunnels are opt-in. They do not expose arbitrary relay ports and they do
not create public listeners on the access machine.

## Security model

The control plane enforces all of these conditions:

- tunnel records are stored in the owning user's repository scope;
- the requested target must match an entry in the service relay's approved
  catalogue;
- only TCP services are supported;
- the access listener must bind to `127.0.0.1`, `::1`, or `localhost`;
- the access and service relays must be different;
- both relays must explicitly enable the `allow_service_tunnels` permission;
- each FRP client receives a short-lived HMAC-SHA256 grant bound to the owner,
  tunnel, relay, role, server name, issue time, and expiry;
- FRPS checks every client at `Login`;
- FRPS checks the service role again at `NewProxy`, including the exact STCP
  proxy name, proxy type, and secret;
- disabled, deleted, expired, malformed, cross-owner, and cross-relay requests
  fail closed with a generic denial;
- FRP transport TLS is mandatory in the supplied server configuration;
- FRPS publishes only its TCP and QUIC transport port. Its dashboard and HTTP
  virtual-host listeners remain disabled.

The FRP shared token protects the transport entry point. It is not sufficient
to create a proxy: the PawFlow plugin grant and current tunnel record must also
match.

## Server configuration

Set four deployment secrets/parameters:

| Variable | Purpose |
|---|---|
| `PAWFLOW_FRPS_SERVER` | Hostname or IP that relays use to reach FRPS. |
| `PAWFLOW_FRPS_PORT` | Published FRP TCP/UDP port; defaults to `7000` in Compose. |
| `PAWFLOW_FRPS_TOKEN` | High-entropy FRP transport token shared by server and clients. |
| `PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY` | Independent high-entropy key used to sign short-lived PawFlow grants. |

The token and signing key must be different random values. For example:

```bash
openssl rand -base64 48
openssl rand -base64 48
```

Store them in the deployment environment or secret manager, never in
`docker/frps.toml` or source control.

With the repository Compose file:

```bash
export PAWFLOW_PORT=9090
export PAWFLOW_FRPS_SERVER=tunnels.example.org
export PAWFLOW_FRPS_PORT=7000
export PAWFLOW_FRPS_TOKEN='replace-with-random-token'
export PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY='replace-with-independent-random-key'

docker compose --profile service-tunnels up -d
```

Publish both `7000/tcp` and `7000/udp` when QUIC is enabled. Do not route the
FRP port through an HTTP reverse proxy. The FRPS plugin callback stays on the
private Compose network at
`/internal/service-tunnels/frp`; PawFlow marks the route public to session
authentication but private-network-only by source address.

## Relay requirements

Relay Desktop, standalone Relay CLI archives, and the managed relay image ship
the FRP 0.70.1 client. Build pipelines verify official SHA-256 release digests
before packaging or installing it. Source-development relays may instead set
`PAWFLOW_FRPC_BIN` to an explicit `frpc` executable.

A relay must advertise `allow_service_tunnels: true`. Desktop/host relays run
the service-tunnel process through the local host helper; managed server relays
run it in their managed relay container. The relay dispatcher refuses all
service-tunnel actions when the dedicated permission is off.

For Relay Desktop, enable **Allow service tunnels (FRP)** in the relay settings.
For a PawFlow-managed server relay, an administrator enables **Allow tunnels
(FRP)** under **Server settings → Server relays**, then reconnects that relay so
the replacement container advertises the capability. Both settings default to
off.

## Create and use a tunnel

1. Open the webchat Resources panel and select **Service Tunnels**.
2. Choose the service relay.
3. Add or select an approved TCP service in that relay's local catalogue. For
   SSH, use target host `127.0.0.1` and target port `22`.
4. Choose a different access relay.
5. Choose an unused loopback port on the access relay, such as `22022`.
6. Create the tunnel and wait for both roles to report connected.
7. Connect the local application to the access listener. For example:

```bash
ssh -p 22022 user@127.0.0.1
```

For WinSCP, set host `127.0.0.1`, port `22022`, and the credentials of the
approved remote SSH service. PawFlow and FRP do not replace the target
service's own authentication.

Deleting a tunnel stops both relay roles and removes its owner-scoped record.
Stopping a tunnel disables automatic refresh and causes new FRP login/proxy
attempts to be rejected. Starting it explicitly re-enables the tunnel.

## Reconnection and grant refresh

A grant is valid for one hour and is checked only when FRPS processes a login
or proxy registration. PawFlow refreshes active persistent tunnel
configurations every 45 minutes and after a participating relay reconnects.
A refresh restarts the local FRP client only when its rendered configuration
changes. If refresh cannot reach either relay, the tunnel remains fail-closed
and reports a disconnected or error state; it never falls back to an arbitrary
target or listener.

## Troubleshooting

- **Service tunnels are not configured**: one or more of the four server
  variables is missing.
- **Service tunnels are disabled on this relay**: enable the dedicated relay
  permission; general shell or filesystem permission is not enough.
- **Service is not approved**: add the exact target to the service relay's
  catalogue. The create request cannot supply an arbitrary replacement host or
  port.
- **Listener is already used**: select another loopback port on the access
  relay.
- **FRP client is not installed**: use a current Relay Desktop/CLI artifact,
  rebuild the managed relay image, or set `PAWFLOW_FRPC_BIN`.
- **Tunnel reconnects are denied after a long outage**: confirm the PawFlow
  relay WebSocket is connected so the server can deliver a fresh grant, then
  restart or reconcile the tunnel from the Resources panel.
- **Public firewall**: allow the configured FRPS port over TCP and UDP only.
  Never expose the PawFlow internal plugin path as a standalone public route.

Logs are written under the relay home in `service-tunnels/`, with one FRP
configuration and log per tunnel role. Configuration files are mode `0600`
where the platform supports POSIX permissions.
