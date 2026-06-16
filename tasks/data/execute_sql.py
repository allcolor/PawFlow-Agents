# ExecuteSQL / PutSQL Tasks

"""
Execute SQL through a dbConnectionPool service (SQLite or PostgreSQL).

The connection is resolved from a declared `service_id` (a dbConnectionPool
controller service) so the same task works against SQLite or Postgres without
hardcoding a driver. A plain `db_path` is still accepted as a standalone
SQLite fallback for simple flows that do not declare a service.

Parameterized statements use named ``:name`` placeholders bound from a JSON
`params` object (anti-injection). Binding is backend-aware: SQLite consumes
``:name`` natively, while psycopg2 needs ``%(name)s`` — the task rewrites the
statement for Postgres. Prefer ``:name`` over ${content} string interpolation
for any untrusted value.
"""

import re
import sqlite3
import json
from typing import Dict, Any, List, Optional, Tuple
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

# A `:name` placeholder: a colon NOT preceded by another colon or a word char
# (so PostgreSQL `::type` casts are left intact), followed by an identifier.
_NAMED_PARAM_RE = re.compile(r'(?<![:\w]):(\w+)')


def _to_pyformat(sql: str) -> str:
    """Rewrite `:name` placeholders to psycopg2 `%(name)s` pyformat."""
    return _NAMED_PARAM_RE.sub(lambda m: '%%(%s)s' % m.group(1), sql)


def _resolve_connection(task: BaseTask) -> Tuple[Any, bool, str]:
    """Return (connection, owns_connection, dialect).

    Prefers a declared dbConnectionPool service (`service_id`); otherwise opens
    an ad-hoc SQLite connection from `db_path`. `owns_connection` is True only
    when this task opened the connection and must close it; pool connections are
    owned by the service. `dialect` is 'postgresql' or 'sqlite'.
    """
    service_id = task.config.get('service_id', '')
    if service_id:
        svc = task.get_service(service_id)
        if svc is None:
            raise TaskError(
                f"dbConnectionPool service '{service_id}' not found")
        conn = svc.get_connection()
        dialect = getattr(svc, 'db_type', 'sqlite') or 'sqlite'
        return conn, False, dialect
    db_path = task.config.get('db_path', '')
    if db_path:
        return sqlite3.connect(db_path), True, 'sqlite'
    raise TaskError(
        "Provide either 'service_id' (a dbConnectionPool) or 'db_path'.")


def _resolve_named_params(task: BaseTask,
                          flowfile: FlowFile) -> Optional[Dict[str, Any]]:
    """Parse the JSON `params` config into a name->value bind dict.

    String values support ${attr} expressions; non-string JSON values (int,
    bool, null) are passed through unchanged so callers control typing.
    """
    raw = (task.config.get('params') or '').strip()
    if not raw:
        return None
    rendered = task.resolve_value(raw, flowfile=flowfile)
    try:
        parsed = json.loads(rendered)
    except (ValueError, TypeError) as e:
        raise TaskError(f"'params' is not valid JSON: {e}")
    if not isinstance(parsed, dict):
        raise TaskError("'params' must be a JSON object of name->value")
    return {
        k: (task.resolve_value(v, flowfile=flowfile) if isinstance(v, str) else v)
        for k, v in parsed.items()
    }


def _bind(cursor: Any, sql: str, params: Optional[Dict[str, Any]],
          dialect: str) -> None:
    """Execute `sql` with optional named params, rewriting for the backend."""
    if params:
        exec_sql = _to_pyformat(sql) if dialect == 'postgresql' else sql
        cursor.execute(exec_sql, params)
    else:
        cursor.execute(sql)


def _safe_rollback(conn: Any) -> None:
    # Best-effort: a failing rollback means the connection is already broken,
    # and there is nothing actionable to do beyond letting the original error
    # surface to the caller.
    try:
        conn.rollback()
    except Exception:  # nosec B110 - intentional best-effort cleanup
        pass


_PARAMS_SCHEMA = {
    'type': 'textarea', 'required': False,
    'description': (
        'JSON object binding :name placeholders (anti-injection). '
        'String values support ${attr} expressions.'
    ),
}
_SERVICE_SCHEMA = {
    'type': 'string', 'required': False,
    'description': 'ID of a dbConnectionPool service (SQLite/Postgres)',
}
_DBPATH_SCHEMA = {
    'type': 'string', 'required': False,
    'description': 'Standalone SQLite path (fallback if no service_id)',
}


class ExecuteSQLTask(BaseTask):
    """Execute a SQL query and return the results as JSON."""

    TYPE = "executeSQL"
    VERSION = "1.0.0"
    NAME = "Execute SQL"
    DESCRIPTION = "Execute a SQL query and return the results en JSON"
    ICON = "database"

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        sql = self.config.get('sql_query', '')
        if not sql:
            raise TaskError("The 'sql_query' parameter is required.")

        params = _resolve_named_params(self, flowfile)
        conn, owns, dialect = _resolve_connection(self)
        try:
            cursor = conn.cursor()
            _bind(cursor, sql, params, dialect)
            if cursor.description:
                # SELECT-like query - return rows as JSON
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                result = json.dumps(rows, ensure_ascii=False, indent=2)
                flowfile.set_content(result.encode('utf-8'))
                flowfile.set_attribute('sql.row_count', str(len(rows)))
                flowfile.set_attribute('mime.type', 'application/json')
            else:
                # INSERT/UPDATE/DELETE/DDL
                conn.commit()
                flowfile.set_attribute('sql.rows_affected', str(cursor.rowcount))
        except TaskError:
            _safe_rollback(conn)
            raise
        except Exception as e:
            _safe_rollback(conn)
            raise TaskError(f"Erreur SQL: {e}")
        finally:
            if owns:
                conn.close()

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'sql_query': {
                'type': 'textarea', 'required': True,
                'description': 'SQL query to execute (supports :name placeholders)',
            },
            'params': _PARAMS_SCHEMA,
            'service_id': _SERVICE_SCHEMA,
            'db_path': _DBPATH_SCHEMA,
        }


class PutSQLTask(BaseTask):
    """Execute a SQL statement parameterized by the FlowFile content."""

    TYPE = "putSQL"
    VERSION = "1.0.0"
    NAME = "Put SQL"
    DESCRIPTION = "Execute a SQL statement parameterized by the FlowFile content"
    ICON = "database"

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        stmt = self.config.get('sql_statement', '')
        if not stmt:
            raise TaskError("The 'sql_statement' parameter is required.")

        content_str = flowfile.get_content().decode('utf-8')
        sql = stmt.replace('${content}', content_str)
        params = _resolve_named_params(self, flowfile)

        conn, owns, dialect = _resolve_connection(self)
        try:
            cursor = conn.cursor()
            _bind(cursor, sql, params, dialect)
            conn.commit()
            flowfile.set_attribute('sql.rows_affected', str(cursor.rowcount))
        except TaskError:
            _safe_rollback(conn)
            raise
        except Exception as e:
            _safe_rollback(conn)
            raise TaskError(f"Erreur SQL: {e}")
        finally:
            if owns:
                conn.close()

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'sql_statement': {
                'type': 'textarea', 'required': True,
                'description': (
                    'SQL statement; use :name placeholders (preferred) or '
                    '${content} for the FlowFile content'
                ),
            },
            'params': _PARAMS_SCHEMA,
            'service_id': _SERVICE_SCHEMA,
            'db_path': _DBPATH_SCHEMA,
        }


TaskFactory.register(ExecuteSQLTask)
TaskFactory.register(PutSQLTask)
