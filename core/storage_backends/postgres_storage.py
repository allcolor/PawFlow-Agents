# PostgreSQL Storage

"""
PostgreSQL storage implementation.
For production environments with reliable persistence.
"""

import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class PostgresStorage:
    """PostgreSQL storage for production environments.

    Stores flows, tasks and services in PostgreSQL tables.
    Uses psycopg2 for connections.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize PostgreSQL storage.

        Args:
            config: Configuration with:
                - host: host (default: localhost)
                - port: port (default: 5432)
                - database: database name (default: pawflow)
                - user: user (default: pawflow)
                - password: password
                - schema: SQL schema (default: public)
        """
        self.host = config.get('host', 'localhost')
        self.port = config.get('port', 5432)
        self.database = config.get('database', 'pawflow')
        self.user = config.get('user', 'pawflow')
        self.password = config.get('password', '')
        self.schema = config.get('schema', 'public')
        self._conn = None
        self._init_database()

    def _table(self, name: str):
        from psycopg2 import sql
        return sql.Identifier(self.schema, name)

    def _get_connection(self):
        """Get a PostgreSQL connection."""
        if self._conn is None or self._conn.closed:
            import psycopg2
            self._conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
            )
            self._conn.autocommit = False
        return self._conn

    def _init_database(self):
        """Create tables if they don't exist."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            from psycopg2 import sql

            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    id TEXT PRIMARY KEY,
                    config JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    modified_at TIMESTAMPTZ DEFAULT NOW()
                )
            """).format(self._table("flows")))

            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    id SERIAL PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    config JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """).format(self._table("tasks_config")))

            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    id SERIAL PRIMARY KEY,
                    service_type TEXT NOT NULL,
                    config JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """).format(self._table("services_config")))

            conn.commit()
            logger.info("PostgreSQL storage initialized")
        except ImportError:
            logger.warning("psycopg2 not available — PostgresStorage will not work")
        except Exception as e:
            logger.error(f"Error initializing PostgreSQL storage: {e}")
            if self._conn:
                self._conn.rollback()

    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """Save a flow to PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            config_json = json.dumps(config, ensure_ascii=False)

            from psycopg2 import sql

            cur.execute(sql.SQL("""
                INSERT INTO {} (id, config, modified_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (id) DO UPDATE
                SET config = EXCLUDED.config, modified_at = NOW()
            """).format(self._table("flows")), (flow_id, config_json))

            conn.commit()
            logger.info(f"Flow saved to PostgreSQL: {flow_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving flow {flow_id} to PostgreSQL: {e}")
            if self._conn:
                self._conn.rollback()
            return False

    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Load a flow from PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            from psycopg2 import sql

            cur.execute(sql.SQL("""
                SELECT config FROM {} WHERE id = %s
            """).format(self._table("flows")), (flow_id,))

            row = cur.fetchone()
            if row:
                config = row[0]
                # psycopg2 returns JSONB as dict already
                if isinstance(config, str):
                    return json.loads(config)
                return config
            return None
        except Exception as e:
            logger.error(f"Error loading flow {flow_id} from PostgreSQL: {e}")
            return None

    def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow from PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            from psycopg2 import sql

            cur.execute(sql.SQL("""
                DELETE FROM {} WHERE id = %s
            """).format(self._table("flows")), (flow_id,))

            deleted = cur.rowcount > 0
            conn.commit()
            if deleted:
                logger.info(f"Flow deleted from PostgreSQL: {flow_id}")
            return deleted
        except Exception as e:
            logger.error(f"Error deleting flow {flow_id} from PostgreSQL: {e}")
            if self._conn:
                self._conn.rollback()
            return False

    def list_flows(self) -> List[str]:
        """List all flows in PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            from psycopg2 import sql

            cur.execute(sql.SQL("""
                SELECT id FROM {} ORDER BY id
            """).format(self._table("flows")))

            return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Error listing flows from PostgreSQL: {e}")
            return []

    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """Save a task to PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            config_json = json.dumps(config, ensure_ascii=False)

            from psycopg2 import sql

            cur.execute(sql.SQL("""
                INSERT INTO {} (task_type, config)
                VALUES (%s, %s::jsonb)
            """).format(self._table("tasks_config")), (task_type, config_json))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving task {task_type} to PostgreSQL: {e}")
            if self._conn:
                self._conn.rollback()
            return False

    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """Save a service to PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            config_json = json.dumps(config, ensure_ascii=False)

            from psycopg2 import sql

            cur.execute(sql.SQL("""
                INSERT INTO {} (service_type, config)
                VALUES (%s, %s::jsonb)
            """).format(self._table("services_config")), (service_type, config_json))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving service {service_type} to PostgreSQL: {e}")
            if self._conn:
                self._conn.rollback()
            return False

    def close(self):
        """Close the connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None
