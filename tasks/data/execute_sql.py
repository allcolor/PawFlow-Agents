# ExecuteSQL Task

"""
Exécuter une requête SQL et retourner les résultats.
"""

import sqlite3
import json
from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


class ExecuteSQLTask(BaseTask):
    """Exécuter une requête SQL sur une base SQLite."""

    TYPE = "executeSQL"
    VERSION = "1.0.0"
    NAME = "Execute SQL"
    DESCRIPTION = "Exécuter une requête SQL et retourner les résultats en JSON"
    ICON = "database"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.sql_query = self.config.get('sql_query', '')
        self.db_path = self.config.get('db_path', '')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.sql_query:
            raise TaskError("Le paramètre 'sql_query' est requis.")
        if not self.db_path:
            raise TaskError("Le paramètre 'db_path' est requis.")

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(self.sql_query)

            if cursor.description:
                # SELECT query - return results as JSON
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                result = json.dumps(rows, ensure_ascii=False, indent=2)
                flowfile.set_content(result.encode('utf-8'))
                flowfile.set_attribute('sql.row_count', str(len(rows)))
                flowfile.set_attribute('mime.type', 'application/json')
            else:
                # INSERT/UPDATE/DELETE
                conn.commit()
                flowfile.set_attribute('sql.rows_affected', str(cursor.rowcount))
        except Exception as e:
            raise TaskError(f"Erreur SQL: {e}")
        finally:
            conn.close()

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'sql_query': {
                'type': 'textarea', 'required': True,
                'description': 'Requête SQL à exécuter',
            },
            'db_path': {
                'type': 'string', 'required': True,
                'description': 'Chemin vers la base SQLite',
            },
        }


class PutSQLTask(BaseTask):
    """Exécuter un statement SQL avec le contenu du FlowFile."""

    TYPE = "putSQL"
    VERSION = "1.0.0"
    NAME = "Put SQL"
    DESCRIPTION = "Exécuter un statement SQL paramétré par le contenu du FlowFile"
    ICON = "database"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.sql_statement = self.config.get('sql_statement', '')
        self.db_path = self.config.get('db_path', '')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.sql_statement:
            raise TaskError("Le paramètre 'sql_statement' est requis.")
        if not self.db_path:
            raise TaskError("Le paramètre 'db_path' est requis.")

        content_str = flowfile.get_content().decode('utf-8')
        sql = self.sql_statement.replace('${content}', content_str)

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(sql)
            conn.commit()
            flowfile.set_attribute('sql.rows_affected', str(cursor.rowcount))
        except Exception as e:
            raise TaskError(f"Erreur SQL: {e}")
        finally:
            conn.close()

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'sql_statement': {
                'type': 'textarea', 'required': True,
                'description': 'Statement SQL (utiliser ${content} pour le contenu du FlowFile)',
            },
            'db_path': {
                'type': 'string', 'required': True,
                'description': 'Chemin vers la base SQLite',
            },
        }


TaskFactory.register(ExecuteSQLTask)
TaskFactory.register(PutSQLTask)
