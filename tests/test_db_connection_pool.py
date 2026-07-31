"""dbConnectionPool: rollback recovery, named params, serialized concurrency.

The service holds ONE shared connection (base_service). These tests cover the
hardening that makes that safe for concurrent flow tasks (pink_skin dispatcher
+ cron sweeps): per-call lock, rollback-on-error, and SQLite cross-thread use.
"""

import threading

import pytest

from services.db_connection_pool import DBConnectionPoolService


def _svc(database=":memory:", max_connections=5):
    s = DBConnectionPoolService({
        "db_type": "sqlite", "database": database,
        "max_connections": max_connections,
    })
    s.connect()
    return s


def test_rollback_keeps_connection_usable():
    s = _svc()
    s.execute_update("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    s.execute_update("INSERT INTO t (v) VALUES (:v)", {"v": "a"})
    assert s.execute_query("SELECT v FROM t") == [{"v": "a"}]
    # A failing statement must not wedge the shared connection.
    with pytest.raises(Exception):
        s.execute_query("SELECT * FROM does_not_exist")
    assert s.execute_query("SELECT v FROM t") == [{"v": "a"}]


def test_named_params_sqlite():
    s = _svc()
    s.execute_update("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    s.execute_update("INSERT INTO t (v) VALUES (:v)", {"v": "x"})
    rows = s.execute_query("SELECT v FROM t WHERE v = :v", {"v": "x"})
    assert rows == [{"v": "x"}]


def test_concurrent_pool_access(tmp_path):
    # Real multi-connection pool needs a SHARED database -> file-backed SQLite
    # (':memory:' would give each pooled connection a separate empty DB).
    s = _svc(str(tmp_path / "pool.db"), max_connections=5)
    s.execute_update("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")
    errors = []

    def worker(n):
        try:
            for _ in range(20):
                s.execute_update("INSERT INTO t (v) VALUES (:v)", {"v": n})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    cnt = s.execute_query("SELECT count(*) AS c FROM t")[0]["c"]
    assert cnt == 8 * 20


def test_write_lock_wait_matches_the_pool_acquire_budget(tmp_path):
    """A pooled connection waits as long for the lock as for the connection.

    SQLite serializes writers: the loser waits for its busy timeout and then
    raises "database is locked". sqlite3's 5s default is unrelated to anything
    this pool promises -- `_acquire` already blocks up to `_acquire_timeout` for
    a free connection, so a caller has already agreed to wait that long. Leaving
    the write lock on the shorter budget is what made
    ``test_concurrent_pool_access`` fail under CI load while passing locally:
    the failure is a timeout, not a deadlock, so it only shows up when the
    machine is slow enough.
    """
    s = _svc(str(tmp_path / "pool.db"))
    conn = s._acquire()
    try:
        busy_timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        s._release(conn)

    assert busy_timeout_ms == s._acquire_timeout * 1000
