# AWS-Native Deployment — Remote Execution Mode Plan

Status: **proposed** (design + phased implementation plan, not yet built).

This document specifies a new, additive execution mode ("**remote**") that lets
PawFlow run on AWS managed compute (ECS Fargate, EKS) — and, by generalization,
on plain EC2 and ECS-on-EC2 — **without** a local Docker socket, **without** a
shared host filesystem, and **without** host-gateway networking.

The existing Docker mode is the optimized single-host special case and stays
**bit-for-bit unchanged**. Remote mode is the general case.

---

## 0. Goals and non-negotiable invariants

1. **Zero regression.** With `PAWFLOW_EXEC_MODE` unset or `docker`, the code path
   is identical to today's. New code only activates behind an explicit flag.
2. **100% parity.** Everything Docker mode does — CLI agent pools, interactive
   (tmux) sessions, the antigravity TLS-MITM observer, containerized
   `executeScript`, service-flow auth-login flows, server relays, VNC/terminal
   proxies — must have a working remote path.
3. **Transitivity C ⇒ A, B.** The remote backend assumes no socket, no shared
   host FS, no host-gateway. Any environment that satisfies those
   non-assumptions (plain EC2 = A, ECS-on-EC2 = B, Fargate/EKS = C) runs the
   same backend. Validating C validates A and B.
4. **One seam.** Everything already funnels through `core/docker_utils.py`. The
   abstraction is inserted there; nothing else invents its own Docker access.

---

## 1. Current architecture (what binds PawFlow to a single Docker host)

PawFlow runs as one container with `/var/run/docker.sock` bind-mounted
(`docker-compose.yml`) and spawns **sibling** containers on the host daemon via
the `docker` CLI — there is no Docker SDK, just `subprocess`.

The single abstraction today is `core/docker_utils.py`:
`docker_run` / `docker_popen` / `docker_exec` / `docker_rm` / `list_containers`
/ `kill_containers` (lines 225-371), plus `docker_cmd()` and the path-translation
helpers `to_host_path()` / `translate_path()` (lines 67-154).

Three hard assumptions are wired throughout:

1. **Local Docker daemon** reachable at `docker.sock`.
2. **Shared host filesystem** — children receive *host paths* via
   `to_host_path()` (e.g. `-v {sessions_host_path}:/cc_sessions_host`, the bridge
   dev-mounts, the `executeScript` tmpdir). PawFlow and its children must see the
   same disk.
3. **host-gateway networking** — `--add-host host.docker.internal:host-gateway`
   plus LAN-IP aliases from `get_host_ip()` (`docker_utils.py:195-212`) so the
   child can reach the server's HTTPS listener.

`detect_exec_mode()` (`docker_utils.py:29-64`) already returns `sidecar` under
ECS/EKS, **but no code branches on it** — the pools and `executeScript` call
`docker run` unconditionally. So on Fargate/EKS PawFlow believes it is in
sidecar mode yet still tries `docker.sock` and fails. This plan makes
`sidecar`/`remote` real.

State/storage today: `DATA_DIR` is the local filesystem (`core/paths.py:18`)
with file-backed `repository/ runtime/ system/` trees; SQLite by default
(`core/storage_backends/sqlite_storage.py`) with an existing Postgres backend
(`core/storage_backends/postgres_storage.py`); secrets encrypted at rest via
`PAWFLOW_SECRET_KEY_B64`; the relay/server-fs uses FUSE (CAP_SYS_ADMIN,
`/dev/fuse`, apparmor, `rshared`).

---

## 2. The core abstraction — `ExecBackend`

New file `core/exec_backend.py`:

```python
class WorkerHandle:           # replaces the bare "container_name: str"
    id: str                   # docker name OR remote task id
    backend: "ExecBackend"
    meta: dict                # image, WS endpoint, node, etc.

class RemoteProcess:          # quacks like subprocess.Popen (see section 3)
    stdin / stdout / stderr   # file-like (real pipes, or WS shims)
    pid: int
    def poll() -> Optional[int]
    def wait(timeout=None) -> int
    def kill() / terminate() / send_signal(sig)
    def kill_process_group()  # replaces `kill -9 -<PGID>`

class ExecBackend(ABC):
    def spawn_worker(spec: WorkerSpec) -> WorkerHandle
    def exec(handle, argv, *, env, cwd, user, stdin, stream=True,
             namespace: Optional[NamespaceSpec]=None,
             **popen_kwargs) -> RemoteProcess
    def copy_in(handle, src, dst)            # docker cp
    def is_alive(handle) -> bool             # docker inspect
    def signal(handle, proc, sig)            # group kill
    def remove(handle, force=True)           # docker rm -f
    def list_workers(owner_prefix) -> list   # docker ps
    def info() -> dict                       # docker info / health
```

`WorkerSpec` is a **declarative** description of everything today's `run_args`
encodes (image, cpus, memory, mounts, add_hosts, caps, tmpfs, shm, user,
entrypoint, env). Each backend translates it: the Docker backend to `docker run`
flags, the remote backend to an ECS RunTask / K8s Job / fleet-acquire.

Two implementations ship:

- **`DockerExecBackend`** — wraps `docker_utils.py` *as-is*. `spawn_worker` →
  `docker run`; `exec` → `subprocess.Popen(docker exec ...)`; `RemoteProcess` is
  the real `subprocess.Popen` (passthrough). Behavior is bit-identical. **This is
  the non-regression guarantee: we extract, we do not rewrite.**
- **`RemoteExecBackend`** — mode C (section 4).

Factory:

```python
def get_exec_backend() -> ExecBackend:
    mode = os.environ.get("PAWFLOW_EXEC_BACKEND") or detect_exec_mode()
    return {"docker": DockerExecBackend, "local": DockerExecBackend,
            "remote": RemoteExecBackend, "sidecar": RemoteExecBackend}[mode]()
```

Singleton, like the pools. `detect_exec_mode()` keeps its logic; we only add that
`sidecar`/ECS/EKS resolves to `remote`. **Default stays `docker`.**

---

## 3. `RemoteProcess` — parity without touching callers

This is the most delicate piece. Callers (`core/llm_providers/claude_code.py:880`,
the interactive pool, etc.) expect a `subprocess.Popen` with `.stdin/.stdout/
.stderr`, read **stream-json** line by line, capture `__PF_CLAUDE_PID` from
stderr (`core/claude_code_pool.py:326+`), and kill by **negative PID** (process
group).

`RemoteExecBackend.exec()` must return an object that behaves exactly like Popen:

- `.stdout` / `.stderr`: real `os.pipe()` fed by a thread that reads WS frames
  (`pawflow_relay/ws_frame.py`) `stdout.v1` / `stderr.v1` and writes them into
  the pipe. Callers keep doing `proc.stdout.readline()` unchanged.
- `.stdin`: a pipe whose writes a thread relays as `stdin.v1` frames.
- `.pid`: the real PID of the remote process, returned by the worker in a
  `started.v1` frame. `__PF_CLAUDE_PID` on stderr keeps working because stderr is
  relayed verbatim.
- `kill_process_group()`: sends a `signal.v1{pgid, sig}` frame; the worker runs
  `kill -9 -<PGID>` **locally** in its own namespace. The "negative-PID local
  kill" semantics become "control message → local kill on the worker", identical
  in effect.
- `poll()` / `wait()`: resolved by an `exit.v1{code}` frame.

Consequence: **no caller's read/kill logic changes** — they receive a
`RemoteProcess` instead of a `Popen`. This is what makes the migration
mechanical.

A shared contract test suite runs against both `subprocess.Popen` and
`RemoteProcess` to prove behavioral equivalence (stream read, stdin, group kill,
exit code, timeout).

---

## 4. Remote transport — worker fleet + orchestrator

Remote mode has no `docker.sock`. Two bricks, one of which already exists.

### 4a. Reuse the relay protocol (primary transport)

The relay is already a **WS-reachable execution sandbox**: it connects back to
`.../ws/relay/<id>`, runs `pawflow_relay`, has `--allow-exec`, and executes via
`tools/fs_exec.py:action_exec`. `core/server_relay_manager.py` already knows how
to spawn/cleanup these (`spawn`, `spawn_minimal`, `spawn_service_relay`,
`ensure`, `destroy`, `list_all`, `restart_orphans`).

Remote mode generalizes this into a **fleet of pre-deployed runtime-workers**
(ECS Service / K8s Deployment) that connect to the server over WS.
`RemoteExecBackend`:

- `spawn_worker` → **acquire** a free worker from the fleet (or trigger a
  scale-out, 4b), not a `docker run`.
- `exec` → send `exec.v1{argv, env, cwd, namespace}` to the worker's WS, receive
  the stream → `RemoteProcess`.
- `remove` → return the worker to the pool (or drain it).

The relay protocol must be **extended** from one-shot captured exec (`fs_exec`
returns stdout/returncode in one block) to **long-lived bidirectional streamed**
exec: new frames `exec.v1 / started.v1 / stdin.v1 / stdout.v1 / stderr.v1 /
signal.v1 / exit.v1` in `pawflow_relay/ws_frame.py`, `pawflow_relay/worker.py`,
and `pawflow_relay/proc_registry.py` (which already tracks relay-side processes).

### 4b. Orchestrator (elasticity)

For real scaling, an orchestrator strategy (a sub-strategy of
`RemoteExecBackend`) creates workers that join the WS fleet of 4a:

- `EcsRunTaskStrategy`: `RunTask` / `StopTask` via boto3, discovery via Cloud
  Map.
- `K8sJobStrategy`: create/delete Jobs/Pods via the K8s API.

There is still **one** exec path (streamed WS); only *how workers are born*
varies. A static pre-provisioned fleet is enough for v1; elasticity comes later.

---

## 5. Filesystem and per-exec namespace

The second large parity item. Today `to_host_path()` / `translate_path()`
(`docker_utils.py:67-154`) assume the same disk, and `exec_claude` runs
`unshare -m` + `mount --bind /cc_sessions_host/<user> /cc_sessions` + `setpriv`
(`claude_code_pool.py:326+`) for per-exec isolation under CAP_SYS_ADMIN.

Remote strategy:

1. **Network-shared session store** — EFS mounted at the **same path** in server
   and workers (so `to_host_path` becomes the identity and the whole translation
   layer is neutralized without being removed), **or** the server-fs FUSE relay
   (`pawflow_relay/server_fs_mount.py`, `services/relay_server_fs.py`) as the
   canonical store — the network-native answer already present.
2. **Declarative sharing contract** — `WorkerSpec.session_store` tells the
   backend how the worker sees `/cc_sessions`. Docker backend → bind-mount
   (current); remote backend → EFS/server-fs already mounted.
3. **Per-exec namespace** moves **to the worker** (it runs the bind/unshare in
   its own environment where it has the rights). On EKS-EC2: a Pod with a
   `securityContext` allowing SYS_ADMIN. On Fargate (no SYS_ADMIN): a
   **path-prefix** fallback — no bind, point `CLAUDE_CONFIG_DIR` directly at
   `/cc_sessions/<user>/<conv>/<agent>`, and get cross-user isolation by
   dedicating **one worker per user** (no worker sharing across users) plus FS
   permissions. The "CC sees its tree at the canonical path" semantics are
   preserved; only the isolation technique changes, exposed via
   `NamespaceSpec{strategy: "unshare" | "prefix"}`.

Apparmor (`core/apparmor.py:79`, `apparmor_security_opts` in the pools) is
host-specific; remote confinement is expressed through K8s `securityContext` /
ECS `linuxParameters`, via `WorkerSpec.security`.

---

## 6. Networking and callback

Today: `get_host_ip()` + `--add-host host.docker.internal:host-gateway` + the
`public_hostname` alias trick (`claude_code_pool.py`, `docker_utils.py:195`).
Inoperative off a single host.

Remote mode: the backend supplies an explicit **`callback_url`** (Cloud Map DNS /
K8s Service / ALB+ACM) injected into the worker env in place of the host-gateway
hacks:

- `PAWFLOW_HOST`, `ANTHROPIC_BASE_URL`, and `PAWFLOW_TOOL_RELAY_URL` (the
  tool-callback channel, `services/tool_relay_service.py` + `get_tool_relay_env`)
  point at the stable service name.
- `WorkerSpec.callbacks` replaces `_extra_add_hosts`. Docker backend → keeps
  `--add-host` (current); remote backend → real DNS.

---

## 7. Consumers to migrate (the definition of "100%")

Each category moves from direct `docker_utils` use to `get_exec_backend()`,
ordered simplest → riskiest:

| # | Consumer | Files | Shape | Difficulty |
|---|----------|-------|-------|-----------|
| 1 | CLI pools (claude/gemini/codex) | `core/claude_code_pool.py`, `core/gemini_pool.py`, `core/codex_pool.py` (symmetric: `acquire`/`release`/`exec_claude`/`_spawn_container`/`_kill`/`_is_alive`/`_cleanup_orphans`) | `run sleep∞` + streamed `exec` | Medium |
| 2 | Containerized executeScript | `tasks/system/execute_script.py:153` `_execute_docker` (one-shot `run -i` + host-call stdin/stdout via `core/flow_script_host.py`) | one-shot bidir | Medium |
| 3 | Server relay manager | `core/server_relay_manager.py` (`spawn`/`spawn_minimal`/`spawn_service_relay`/volumes) | relay container spawn | Medium |
| 4 | Service-flow auth-login | `tasks/ai/actions/service_flow.py` (rclone/gemini/codex/agy; ~30 `run`/`exec`/`rm` across lines 2181-5164) | interactive one-shots | Medium-high (site count) |
| 5 | Interactive pool (tmux) | `core/claude_code_interactive_pool.py` (`ensure_started`/`send_text`/`send_keys`/`send_interrupt`/`force_stop`/`sweep_idle`, tmux via `docker exec`) | persistent interactive sessions | **High** |
| 6 | MITM observer | `core/antigravity_observer_pool.py` (interactive + TLS observer) | same + MITM | **High** |
| 7 | VNC / terminal proxy | `services/vnc_proxy.py`, `services/terminal_proxy.py` | `rm` cleanup + proxy | Low-medium |
| 8 | apparmor | `core/apparmor.py:79` (loads the profile via docker) | host-only | N/A remote (replace) |
| 9 | Relay-side exec | `tools/fs_exec.py`, `tools/fs_common.py` | exec *inside* the worker | extend, not migrate |

---

## 8. Phased plan

**Phase 0 — Safety net (prerequisite, no behavior change).**
Characterization tests of Docker mode: capture the exact `argv` produced today
(`run_args`, `exec_args`) for each pool/consumer and freeze them as golden tests.
The whole migration must reproduce these argv in Docker mode. Baseline the
existing 5382-test coverage.

**Phase 1 — Abstraction + Docker backend (pure refactor, identical behavior).**
`core/exec_backend.py` with interfaces + `DockerExecBackend` (passthrough to
`docker_utils`). Migrate one simple consumer (claude pool) to
`get_exec_backend()`; golden tests green proves non-regression. Then migrate
gemini/codex pools (symmetric), executeScript, server_relay_manager,
service-flows, vnc/terminal — **all still on the Docker backend**. End state: no
`docker_*` call outside `DockerExecBackend`, full `pytest` green. Shippable on its
own.

**Phase 2 — Remote transport (pools not yet switched).**
Extend `pawflow_relay/ws_frame.py` + `worker.py` + `proc_registry.py` for
long-lived streamed bidir exec. Implement `RemoteProcess` (Popen shim) +
`RemoteExecBackend` (static WS worker fleet). Integration tests: an in-process
local relay worker, streamed `cat`/`bash`, group kill, exit code. Parity of the
`RemoteProcess` vs `Popen` contract via the shared suite.

**Phase 3 — CLI pools on remote.**
Adapt `_spawn_container`→`spawn_worker`, `exec_claude`→`backend.exec(namespace=...)`.
Move `unshare/mount/setpriv` to the worker. Shared FS via EFS/server-fs; verify
CC sees `/cc_sessions/<conv>/<agent>`. End-to-end: a real agent conversation in
remote mode on a test cluster.

**Phase 4 — executeScript + service-flows + server relays on remote.**
`_execute_docker` via the backend (host-call protocol unchanged, only the process
transport changes). Service-flow auth-login `docker exec` interactions become
`backend.exec`.

**Phase 5 — Interactive (tmux) + MITM observer.**
Hardest: long tmux sessions, `send_keys`, TLS MITM. The worker hosts tmux; the
server drives it via control frames. MITM observer: the TLS proxy runs in the
worker, certs provisioned via secrets.

**Phase 6 — Orchestrator & elasticity.**
`EcsRunTaskStrategy` / `K8sJobStrategy` for dynamic fleet scale-out.

**Phase 7 — State & multi-node infra.**
`PostgresStorage` (exists) → RDS/Aurora. `repository/runtime/system` trees
(`core/paths.py`) on shared EFS (or progressive DB/S3 migration). Externalize
pool bookkeeping if the server runs multi-replica (otherwise: one server replica
+ a worker fleet, sufficient for v1). Secrets → Secrets Manager/SSM; images →
ECR (retarget the CI `docker-publish.yml`).

---

## 9. Configuration & detection

- `PAWFLOW_EXEC_BACKEND` ∈ `docker | remote` (explicit override); otherwise the
  extended `detect_exec_mode()` (ECS/EKS → `remote`).
- New parameters (in `global_parameters.json`, like the existing `server_relay_*`
  keys): `remote_fleet_min/max`, `remote_session_store` (`efs` | `serverfs`),
  `remote_callback_url`, `remote_worker_image` (ECR), `remote_namespace_strategy`
  (`unshare` | `prefix`), `remote_orchestrator` (`none` | `ecs` | `k8s`).
- **Absolute default = `docker`** ⇒ existing installs are unchanged.

---

## 10. Testing

1. **Golden/characterization** (phase 0): frozen Docker argv.
2. **Contract parity** `RemoteProcess` ↔ `Popen`: the same suite run against
   both (stream read, stdin, group kill, exit, timeout).
3. **Relay integration**: in-process worker, streamed exec.
4. **Per-category e2e**: one "real conversation" test per mode (docker vs remote)
   — neither may diverge functionally from the other.
5. **AWS e2e**: an optional, manually-triggered CI job that stands up a minimal
   cluster (Fargate or kind) and runs phases 3-5.
6. The full suite (5382+) must stay green in Docker mode at **every** phase.

---

## 11. Rollout / feature flag

- Everything behind `PAWFLOW_EXEC_BACKEND`. Until it is set to `remote`, **nothing
  changes**.
- Progressive per-category cutover: executeScript can route remote while the
  pools stay on docker (the backend can be resolved per consumer via a
  `PAWFLOW_EXEC_BACKEND_<CONSUMER>` override).
- Kill-switch: reverting to `docker` restores the old path instantly.

---

## 12. Why C ⇒ A and B (transitivity)

The `RemoteExecBackend` assumes no socket, no host FS, no host-gateway. Therefore:

- **A (EC2 alone)**: can run the Docker backend (optimal) **or** the remote
  backend pointed at a local fleet — both work.
- **B (ECS-on-EC2)**: same; remote even avoids the fragility of the
  `PAWFLOW_HOST_*` path-translation envs.
- **C (Fargate/EKS)**: only remote works, and it works.

The remote backend being strictly more general, validating it covers A and B.
Docker mode remains the single-host optimization; neither mode is removed.

---

## 13. Risks & open questions

1. **Fargate without SYS_ADMIN** ⇒ no `unshare/mount`. Mitigation: the `prefix`
   strategy + one-worker-per-user. Validate that cross-user isolation stays
   acceptable (otherwise EKS-EC2 privileged is required).
2. **Exec latency**: local `docker exec` ≈ ms; WS + orchestrator can add
   latency/cold-start. Mitigation: a pre-warmed fleet (the `prewarm` concept
   already exists in the pools).
3. **MITM observer** (phase 5): the most uncertain — cert provisioning and TLS
   interception inside a managed worker. Prototype early.
4. **Multi-replica state**: recommended v1 = **one server replica + a worker
   fleet** to avoid externalizing in-memory singletons; true multi-server is a
   separate effort (phase 7+).
5. **server-fs FUSE vs EFS**: decide early — EFS is operationally simpler,
   server-fs reuses what exists but needs FUSE. Structuring decision for phases
   1/4.

---

## 14. Critical path summary

1. Extract `ExecBackend` with a passthrough `DockerExecBackend` proven identical
   by golden tests →
2. `RemoteProcess` Popen shim + streamed WS transport →
3. CLI pools remote on shared FS →
4-5. executeScript/service-flows, then interactive/MITM →
6-7. orchestrator + AWS state.

The non-regression lock is phase 1 (pure refactor); the parity lock is
`RemoteProcess` (phase 2).
