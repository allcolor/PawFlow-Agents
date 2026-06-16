"""dbConnectionPool: rollback recovery, named params, serialized concurrency.

The service holds ONE shared connection (base_service). These tests cover the
hardening that makes that safe for concurrent flow tasks (pink_skin dispatcher
+ cron sweeps): per-call lock, rollback-on-error, and SQLite cross-thread use.
"""

import threading

import pytest

from services.db_connection_pool import DBConnectionPoolService


def _svc():
    s = DBConnectionPoolService({"db_type": "sqlite", "database": ":memory:"})
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


def test_concurrent_access_serialized():
    s = _svc()
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
