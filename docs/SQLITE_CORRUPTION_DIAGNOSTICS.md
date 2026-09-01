# SQLite corruption diagnostics

PawFlow has two SQLite stores with dedicated corruption boundaries:

- `data/system/mcp_servers.sqlite3` is durable. It is checked before schema
  work and fails closed without deleting or replacing the database or sidecars.
- `data/runtime/scratchdirs/scratchdirs.sqlite3` contains disposable temporary
  metadata. A proven corruption signature quarantines the database and its
  WAL/SHM sidecars before an empty store is created.

Both stores use WAL journaling, `synchronous=FULL`, and
`cell_size_check=ON`. WAL sends commit-time page writes to the log instead of
overwriting the main file during each commit. Checkpoints still copy WAL frames
into the main database, so WAL is a mitigation and not evidence of the original
corruption mechanism.

## Opt-in bootstrap canary

Set `PAWFLOW_SQLITE_BOOT_CANARY=1` only while diagnosing corruption. The server
then checks both existing main files at four ordered checkpoints:

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

The first failing phase narrows the mutation window but does not identify a
native library by itself. In the standard server image, task registration loads
native modules including PyArrow, fastavro, lxml, NumPy, cryptography, and
bcrypt. Torch and sentence-transformers remain lazy, while FUSE runs in the
separate relay process. For a diagnostic run, combine the canary with allocator
checks such as `PYTHONMALLOC=debug` and `MALLOC_CHECK_=3`. A positive allocator
failure or an instrumented ASan build is still required to prove a native
memory overwrite.

Interpretation:

- failure before registration means the damaged main file predates this boot;
- first failure after registration isolates the global import/registration
  phase;
- first failure after flow restoration isolates the flow/service restore phase;
- four green checkpoints do not exclude a later runtime mutation.

Never enable an automatic repair while collecting this evidence. Stop the
server before copying the database and every existing `-wal` and `-shm` file.
