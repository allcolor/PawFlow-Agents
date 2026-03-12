# DBConnectionPool Service

"""
Service de pool de connexions base de données.
Supporte SQLite et PostgreSQL.
"""

import sqlite3
from typing import Dict, Any, List, Optional, Tuple
from core.base_service import BaseService
from core import ServiceFactory, ServiceError

try:
    import psycopg2
except ImportError:
    psycopg2 = None


class DBConnectionPoolService(BaseService):
    """Pool de connexions base de données."""

    TYPE = "dbConnectionPool"
    VERSION = "1.0.0"
    NAME = "DB Connection Pool"
    DESCRIPTION = "Pool de connexions SQLite/PostgreSQL"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.db_type = self.config.get("db_type", "sqlite")
        self.database = self.config.get("database", "")
        self.max_connections = self.config.get("max_connections", 5)

    def _create_connection(self) -> Any:
        if not self.database:
            raise ServiceError("Le paramètre 'database' est requis.")

        if self.db_type == "sqlite":
            return sqlite3.connect(self.database)
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
        if self._connection:
            self._connection.close()

    def get_connection(self) -> Any:
        return self._get_connection()

    def execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params or ())
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def execute_update(self, query: str, params: Optional[tuple] = None) -> int:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params or ())
            conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()

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
