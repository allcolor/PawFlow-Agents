# ExecuteSQL Task

"""
Execute a SQL query and return the results.
"""

import sqlite3
import json
from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


class ExecuteSQLTask(BaseTask):
    """Execute a SQL query on a SQLite database."""

    TYPE = "executeSQL"
    VERSION = "1.0.0"
    NAME = "Execute SQL"
    DESCRIPTION = "Execute a SQL query and return the results en JSON"
    ICON = "database"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.sql_query = self.config.get('sql_query', '')
        self.db_path = self.config.get('db_path', '')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.sql_query:
            raise TaskError("The 'sql_query' parameter is required.")
        if not self.db_path:
            raise TaskError("The 'db_path' parameter is required.")

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
                'description': 'SQL query to execute',
            },
            'db_path': {
                'type': 'string', 'required': True,
                'description': 'Chemin vers la base SQLite',
            },
        }


class PutSQLTask(BaseTask):
    """Execute a SQL statement with the FlowFile content."""

    TYPE = "putSQL"
    VERSION = "1.0.0"
    NAME = "Put SQL"
    DESCRIPTION = "Execute a SQL statement parameterized by the FlowFile content"
    ICON = "database"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.sql_statement = self.config.get('sql_statement', '')
        self.db_path = self.config.get('db_path', '')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.sql_statement:
            raise TaskError("The 'sql_statement' parameter is required.")
        if not self.db_path:
            raise TaskError("The 'db_path' parameter is required.")

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
                'description': 'SQL statement (use ${content} for FlowFile content)',
            },
            'db_path': {
                'type': 'string', 'required': True,
                'description': 'Chemin vers la base SQLite',
            },
        }


TaskFactory.register(ExecuteSQLTask)
TaskFactory.register(PutSQLTask)
