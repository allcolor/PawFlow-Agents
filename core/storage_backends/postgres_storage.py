# PostgreSQL Storage

"""
Implémentation du stockage PostgreSQL.
Pour les environnements de production avec persistance fiable.
"""

import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class PostgresStorage:
    """Stockage PostgreSQL pour environnements de production.

    Stocke les flows, tasks et services dans des tables PostgreSQL.
    Utilise psycopg2 pour les connexions.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser le stockage PostgreSQL.

        Args:
            config: Configuration avec:
                - host: hôte (défaut: localhost)
                - port: port (défaut: 5432)
                - database: nom de la base (défaut: pawflow)
                - user: utilisateur (défaut: pawflow)
                - password: mot de passe
                - schema: schéma SQL (défaut: public)
        """
        self.host = config.get('host', 'localhost')
        self.port = config.get('port', 5432)
        self.database = config.get('database', 'pawflow')
        self.user = config.get('user', 'pawflow')
        self.password = config.get('password', '')
        self.schema = config.get('schema', 'public')
        self._conn = None
        self._init_database()

    def _get_connection(self):
        """Obtenir une connexion PostgreSQL."""
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
        """Créer les tables si elles n'existent pas."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.flows (
                    id TEXT PRIMARY KEY,
                    config JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    modified_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.tasks_config (
                    id SERIAL PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    config JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.services_config (
                    id SERIAL PRIMARY KEY,
                    service_type TEXT NOT NULL,
                    config JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            conn.commit()
            logger.info("PostgreSQL storage initialized")
        except ImportError:
            logger.warning("psycopg2 not available — PostgresStorage will not work")
        except Exception as e:
            logger.error(f"Error initializing PostgreSQL storage: {e}")
            if self._conn:
                self._conn.rollback()

    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder un flux dans PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            config_json = json.dumps(config, ensure_ascii=False)

            cur.execute(f"""
                INSERT INTO {self.schema}.flows (id, config, modified_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (id) DO UPDATE
                SET config = EXCLUDED.config, modified_at = NOW()
            """, (flow_id, config_json))

            conn.commit()
            logger.info(f"Flow saved to PostgreSQL: {flow_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving flow {flow_id} to PostgreSQL: {e}")
            if self._conn:
                self._conn.rollback()
            return False

    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Charger un flux depuis PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            cur.execute(f"""
                SELECT config FROM {self.schema}.flows WHERE id = %s
            """, (flow_id,))

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
        """Supprimer un flux de PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            cur.execute(f"""
                DELETE FROM {self.schema}.flows WHERE id = %s
            """, (flow_id,))

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
        """Lister tous les flux dans PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            cur.execute(f"""
                SELECT id FROM {self.schema}.flows ORDER BY id
            """)

            return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Error listing flows from PostgreSQL: {e}")
            return []

    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder une tâche dans PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            config_json = json.dumps(config, ensure_ascii=False)

            cur.execute(f"""
                INSERT INTO {self.schema}.tasks_config (task_type, config)
                VALUES (%s, %s::jsonb)
            """, (task_type, config_json))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving task {task_type} to PostgreSQL: {e}")
            if self._conn:
                self._conn.rollback()
            return False

    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder un service dans PostgreSQL."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            config_json = json.dumps(config, ensure_ascii=False)

            cur.execute(f"""
                INSERT INTO {self.schema}.services_config (service_type, config)
                VALUES (%s, %s::jsonb)
            """, (service_type, config_json))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving service {service_type} to PostgreSQL: {e}")
            if self._conn:
                self._conn.rollback()
            return False

    def close(self):
        """Fermer la connexion."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None
