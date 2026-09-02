# SQLite corruption diagnostics

## Root cause: descriptor reuse under a live TLS read

Every corrupted PawFlow database seen so far (`mcp_servers`, `scratchdirs`,
`ui_surfaces`, `agent_inbox`) carries the same signature in page 1: bytes 0 to
38 and 63 onwards are intact, and bytes 39 to 62 contain exactly one TLS
record:

```
17 03 03 00 13  <19 encrypted bytes>
```

`17` is the TLS application-data content type, `03 03` the legacy record
version, `00 13` a 19-byte payload. In TLS 1.3 a 19-byte encrypted payload is
an alert (2 alert bytes, 1 inner content-type byte, 16-byte AEAD tag). The
writer is OpenSSL, not SQLite, and not the storage layer.

The mechanism, reproduced end to end on the affected server:

1. A streaming LLM request runs in a worker thread. `http.client` reads the
   response through an `SSLSocket`; OpenSSL holds the raw descriptor number.
2. A user preempt or force stop called `abort()` from another thread, which
   used to `close()` that connection. `close()` releases the descriptor
   number immediately, while the worker thread is still inside `SSL_read`.
3. The next `open()` in the process receives the same number. SQLite stores
   open their main file lazily on first use, so the number lands on a
   database file.
4. The worker's `SSL_read` continues: it reads the rest of the TLS record it
   was waiting for from the database file, the MAC check fails, and OpenSSL
   writes a 24-byte alert record with `write()` at the current file offset.
   In blocking mode that offset equals the body length of the next record
   (39 bytes in production); with a socket timeout it is 5. The later
   `close()` from the worker thread then closes SQLite's descriptor, which
   surfaces as `disk I/O error` on the store.

Journal mode is irrelevant: `ui_surfaces` and `agent_inbox` were already WAL
when they were damaged. Native bootstrap imports are not involved either; the
damage happens at runtime, whenever a stream is aborted.

### Rules

- Never `close()` a socket from a thread that does not own it. Interrupt it
  with `shutdown(SHUT_RDWR)` via `core.socket_teardown.shutdown_socket` or
  `abort_http_connection`; the owning thread closes the socket in its own
  `finally`. Pending reads return EOF and pending writes fail immediately, so
  the interruption is as fast as before, but the descriptor number stays
  reserved until the owner releases it.
- `LLMClient.abort()` follows this rule for the in-flight HTTP stream, and the
  relay WebSocket bridge (`services._relay_ws._attach_sync_sock_to_loop`)
  lets its reader thread release the socket after a cross-thread close.
- Reviewers must reject new code that closes sockets, pipes, or descriptors
  held by another thread. Owners close; other threads shut down.

## Fail-closed durable stores

Durable stores never delete, replace, checkpoint, or repair a damaged
database on their own: the file is evidence.

- `data/system/mcp_servers.sqlite3` checks its main file before schema work
  and disables MCP publication on failure.
- `data/runtime/ui_surfaces.sqlite3` and `data/runtime/agent_inbox.sqlite3`
  use `core.sqlite_store_guard.SqliteStoreGuard`: the main file is checked
  read-only (`mode=ro&immutable=1`, `PRAGMA quick_check`) before the store
  opens it, and a corruption signature raised while creating the schema also
  trips the guard. A tripped store logs one CRITICAL line with sizes, mtimes
  and SHA-256 digests of the database and sidecars, keeps its singleton alive,
  and raises `SqliteStoreUnavailableError` on every later call. The
  `ui_surface_list` action maps that error to HTTP 503.
- `data/runtime/scratchdirs/scratchdirs.sqlite3` holds disposable metadata
  only. A proven corruption signature quarantines the database and its
  WAL/SHM sidecars before an empty store is created.

The MCP, ScratchDir, and guarded stores use WAL journaling; MCP and ScratchDir
add `synchronous=FULL` and `cell_size_check=ON`. WAL is a mitigation for
commit-time page writes and not evidence about the corruption mechanism.

## Repairing a damaged store

The TLS alert only overwrites header bytes 39 to 62 (freelist count, schema
cookie, schema format, page-cache size, largest root page, text encoding,
user version). Everything else in the file is intact, so the data can be
recovered without a backup:

1. Stop the writer or make sure no connection holds the file, then copy the
   database and any `-wal`/`-shm` sidecar to `dbforensics/` with a timestamp.
2. On a copy, rewrite bytes 36 to 63 with sane values: freelist count `0`
   (if bytes 32 to 35 are zero), schema cookie `1`, schema format `4`,
   page-cache size `0`, largest root page `0`, text encoding `1` (UTF-8),
   user version `0`. Each field is a 4-byte big-endian integer.
3. Open the copy with `?mode=ro&immutable=1` and confirm
   `PRAGMA integrity_check` returns `ok`.
4. Dump it with `Connection.iterdump()` into a fresh database, verify the
   integrity check and row counts, then move the fresh file into place with
   `os.replace`.

Never repair in place and never enable an automatic repair while collecting
evidence.

## Opt-in bootstrap canary

Set `PAWFLOW_SQLITE_BOOT_CANARY=1` only while diagnosing corruption. The server
then checks the existing main files of `mcp_servers`, `scratchdirs`,
`ui_surfaces`, and `agent_inbox` at four ordered checkpoints:

1. before task and service registration;
2. after task and service registration;
3. before deployed-flow restoration;
4. after deployed-flow restoration.

Each check opens the main file read-only with `mode=ro&immutable=1`, so SQLite
does not recover, create, checkpoint, or remove WAL/SHM sidecars. It records the
main-file size and modification time, the first-page SHA-256, change counters,
header bytes 36 through 62, sidecar metadata, and `PRAGMA integrity_check`.
Missing databases are valid because a first boot has not created them yet.

The canary is fail-fast when enabled. A corrupt result logs at CRITICAL and
stops startup at the named checkpoint, preserving the files for analysis. With
the environment variable absent, all four calls return immediately and do no
filesystem work.

Interpretation:

- failure before registration means the damaged main file predates this boot;
- four green checkpoints do not exclude a later runtime mutation, which is
  the case described above.
