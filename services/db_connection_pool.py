# DBConnectionPool Service

"""
Database connection pool service.
Supporte SQLite et PostgreSQL.
"""

import queue
import sqlite3
import threading
from typing import Dict, Any, List, Optional, Tuple
from core.base_service import BaseService
from core import ServiceFactory, ServiceError

try:
    import psycopg2
except ImportError:
    psycopg2 = None


class DBConnectionPoolService(BaseService):
    """Database connection pool."""

    TYPE = "dbConnectionPool"
    VERSION = "1.0.0"
    NAME = "DB Connection Pool"
    DESCRIPTION = "Pool de connexions SQLite/PostgreSQL"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.db_type = self.config.get("db_type", "sqlite")
        self.database = self.config.get("database", "")
        try:
            self.max_connections = max(1, int(self.config.get("max_connections", 5)))
        except (TypeError, ValueError):
            self.max_connections = 5
        # A SQLite ':memory:' database is private to each connection, so a pool
        # of several would each see a DIFFERENT empty DB. Pin it to one shared
        # connection. File-backed SQLite and Postgres pool normally.
        if self.db_type == "sqlite" and self.database == ":memory:":
            self.max_connections = 1
        # A real pool: up to max_connections live connections, one handed to each
        # concurrent caller (true parallelism for the moderation dispatcher and
        # the cron sweeps), returned to the pool after each call.
        self._pool = queue.Queue(maxsize=self.max_connections)
        self._pool_lock = threading.Lock()
        self._pool_created = 0
        self._acquire_timeout = 30

    def _acquire(self) -> Any:
        """Check out a connection: reuse an idle one, grow up to max, else wait."""
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass
        with self._pool_lock:
            if self._pool_created < self.max_connections:
                conn = self._create_connection()
                self._pool_created += 1
                return conn
        # Pool saturated: block until a peer returns a connection.
        return self._pool.get(timeout=self._acquire_timeout)

    def _release(self, conn: Any, broken: bool = False) -> None:
        """Return a connection to the pool, or discard it if broken."""
        if broken:
            self._discard(conn)
            return
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            self._discard(conn)

    def _discard(self, conn: Any) -> None:
        try:
            conn.close()
        except Exception:
            pass
        with self._pool_lock:
            self._pool_created = max(0, self._pool_created - 1)

    def _create_connection(self) -> Any:
        if not self.database:
            raise ServiceError("Le paramètre 'database' est requis.")

        if self.db_type == "sqlite":
            # A pooled connection may be reused by whichever worker thread checks
            # it out next, so it must not be pinned to its creating thread.
            return sqlite3.connect(self.database, check_same_thread=False)
        elif self.db_type == "postgresql":
            if psycopg2 is None:
                raise ServiceError("psycopg2 non installé. pip install psycopg2-binary")
            return psycopg2.connect(
                host=self.config.get("host", "localhost"),
                port=self.config.get("port", 5432),
                dbname=self.database,
                user=self.config.get("user", ""),
                password=self.config.get("password", ""),
            )
        else:
            raise ServiceError(f"Type de base non supporté: {self.db_type}")

    def _close_connection(self):
        # Drain and close the whole pool (plus the base probe connection).
        if getattr(self, "_connection", None):
            try:
                self._connection.close()
            except Exception:
                pass
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
            except Exception:
                pass
        with self._pool_lock:
            self._pool_created = 0

    def get_connection(self) -> Any:
        # NOTE: returns the base single connection (not pooled, no checkout/
        # release contract). Prefer execute_query/execute_update for pooled use.
        return self._get_connection()

    def execute_query(self, query: str, params: Optional[Any] = None) -> List[Dict[str, Any]]:
        conn = self._acquire()
        broken = False
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(query, params if params is not None else ())
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                cursor.close()
            # End the (read) transaction so the connection returns to the pool
            # clean -- Postgres would otherwise keep it idle-in-transaction.
            conn.commit()
            return rows
        except Exception:
            try:
                conn.rollback()
            except Exception:
                broken = True
            raise
        finally:
            self._release(conn, broken)

    def execute_update(self, query: str, params: Optional[Any] = None) -> int:
        conn = self._acquire()
        broken = False
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(query, params if params is not None else ())
                conn.commit()
                return cursor.rowcount
            finally:
                cursor.close()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                broken = True
            raise
        finally:
            self._release(conn, broken)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'db_type': {
                'type': 'select', 'required': False, 'default': 'sqlite',
                'options': ['sqlite', 'postgresql'],
                'description': 'Type de base de données',
            },
            'database': {
                'type': 'string', 'required': True,
                'description': 'Chemin SQLite ou nom de base PostgreSQL',
            },
            'max_connections': {
                'type': 'integer', 'required': False, 'default': 5,
                'description': 'Nombre maximum de connexions',
            },
        }


ServiceFactory.register(DBConnectionPoolService)
