"""Tests unitaires pour les nouvelles tasks (Phase 5)."""

import pytest
import json
import csv
import sqlite3
import os
from io import StringIO

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile, TaskFactory
from tasks.control.funnel import FunnelTask
from tasks.data.convert_csv import ConvertCSVToJSONTask, ConvertJSONToCSVTask
from tasks.data.execute_sql import ExecuteSQLTask, PutSQLTask
from tasks.control.ports import InputPortTask, OutputPortTask


class TestFunnelTask:

    def test_funnel_passthrough(self):
        task = FunnelTask({})
        ff = FlowFile(content=b"test data", attributes={"key": "value"})
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_content() == b"test data"
        assert results[0].get_attribute("key") == "value"

    def test_funnel_preserves_attributes(self):
        task = FunnelTask({})
        ff = FlowFile(
            content=b"data",
            attributes={"attr1": "val1", "attr2": "val2", "mime.type": "text/plain"}
        )
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("attr1") == "val1"
        assert results[0].get_attribute("attr2") == "val2"
        assert results[0].get_attribute("mime.type") == "text/plain"

    def test_funnel_empty_content(self):
        task = FunnelTask({})
        ff = FlowFile(content=b"", attributes={})
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_content() == b""


class TestConvertCSVToJSONTask:

    def test_convert_csv_with_header(self):
        task = ConvertCSVToJSONTask({})
        csv_content = "name,age,city\nAlice,30,Paris\nBob,25,Lyon"
        ff = FlowFile(content=csv_content.encode('utf-8'))
        results = task.execute(ff)

        assert len(results) == 1
        output = json.loads(results[0].get_content().decode('utf-8'))
        assert len(output) == 2
        assert output[0] == {"name": "Alice", "age": "30", "city": "Paris"}
        assert output[1] == {"name": "Bob", "age": "25", "city": "Lyon"}
        assert results[0].get_attribute("mime.type") == "application/json"

    def test_convert_csv_without_header(self):
        task = ConvertCSVToJSONTask({"has_header": False})
        csv_content = "Alice,30,Paris\nBob,25,Lyon"
        ff = FlowFile(content=csv_content.encode('utf-8'))
        results = task.execute(ff)

        assert len(results) == 1
        output = json.loads(results[0].get_content().decode('utf-8'))
        assert len(output) == 2
        assert output[0] == ["Alice", "30", "Paris"]
        assert output[1] == ["Bob", "25", "Lyon"]

    def test_convert_csv_custom_delimiter(self):
        task = ConvertCSVToJSONTask({"delimiter": ";", "has_header": True})
        csv_content = "name;age;city\nAlice;30;Paris"
        ff = FlowFile(content=csv_content.encode('utf-8'))
        results = task.execute(ff)

        assert len(results) == 1
        output = json.loads(results[0].get_content().decode('utf-8'))
        assert len(output) == 1
        assert output[0] == {"name": "Alice", "age": "30", "city": "Paris"}


class TestConvertJSONToCSVTask:

    def test_convert_json_dict_list(self):
        task = ConvertJSONToCSVTask({})
        json_content = '[{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]'
        ff = FlowFile(content=json_content.encode('utf-8'))
        results = task.execute(ff)

        assert len(results) == 1
        output = results[0].get_content().decode('utf-8')
        reader = csv.DictReader(StringIO(output))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0] == {"name": "Alice", "age": "30"}
        assert rows[1] == {"name": "Bob", "age": "25"}

    def test_convert_json_nested_list(self):
        task = ConvertJSONToCSVTask({"include_header": False})
        json_content = '[[1, 2, 3], [4, 5, 6]]'
        ff = FlowFile(content=json_content.encode('utf-8'))
        results = task.execute(ff)

        assert len(results) == 1
        output = results[0].get_content().decode('utf-8')
        reader = csv.reader(StringIO(output))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0] == ["1", "2", "3"]
        assert rows[1] == ["4", "5", "6"]

    def test_convert_json_empty_array(self):
        task = ConvertJSONToCSVTask({})
        ff = FlowFile(content=b'[]')
        results = task.execute(ff)

        assert len(results) == 1
        output = results[0].get_content().decode('utf-8')
        assert output.strip() == ""

    def test_convert_json_invalid(self):
        task = ConvertJSONToCSVTask({})
        ff = FlowFile(content=b'{"key": "value"}')

        with pytest.raises(Exception) as exc_info:
            task.execute(ff)
        assert "array" in str(exc_info.value)


class TestExecuteSQLTask:

    @pytest.fixture
    def temp_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name TEXT,
                age INTEGER,
                city TEXT
            )
        ''')
        cursor.executemany(
            'INSERT INTO users (name, age, city) VALUES (?, ?, ?)',
            [
                ("Alice", 30, "Paris"),
                ("Bob", 25, "Lyon"),
                ("Charlie", 35, "Marseille")
            ]
        )
        conn.commit()
        conn.close()
        return db_path

    def test_execute_sql_select(self, temp_db):
        task = ExecuteSQLTask({
            "sql_query": "SELECT * FROM users WHERE age > 25",
            "db_path": temp_db
        })
        ff = FlowFile(content=b"")
        results = task.execute(ff)

        assert len(results) == 1
        output = json.loads(results[0].get_content().decode('utf-8'))
        assert len(output) == 2
        assert output[0]["name"] == "Alice"
        assert output[1]["name"] == "Charlie"
        assert results[0].get_attribute("sql.row_count") == "2"
        assert results[0].get_attribute("mime.type") == "application/json"

    def test_execute_sql_insert(self, temp_db):
        task = ExecuteSQLTask({
            "sql_query": "INSERT INTO users (name, age, city) VALUES ('David', 28, 'Bordeaux')",
            "db_path": temp_db
        })
        ff = FlowFile(content=b"")
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("sql.rows_affected") == "1"

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 4

    def test_execute_sql_missing_query(self, temp_db):
        """Validation catches missing required param at init time."""
        with pytest.raises(ValueError, match="sql_query"):
            ExecuteSQLTask({"db_path": temp_db})

    def test_execute_sql_missing_db(self):
        # No service_id and no db_path -> connection cannot be resolved at run.
        from core import TaskError
        task = ExecuteSQLTask({"sql_query": "SELECT 1"})
        with pytest.raises(TaskError, match="service_id"):
            task.execute(FlowFile(content=b""))

    def test_execute_sql_via_pool_service(self, temp_db):
        from services.db_connection_pool import DBConnectionPoolService
        svc = DBConnectionPoolService({
            "_service_id": "db", "db_type": "sqlite", "database": temp_db})
        task = ExecuteSQLTask({
            "sql_query": "SELECT name FROM users WHERE age > 25 ORDER BY name",
            "service_id": "db"})
        task.set_services({"db": svc})
        out = task.execute(FlowFile(content=b""))[0]
        rows = json.loads(out.get_content().decode())
        assert [r["name"] for r in rows] == ["Alice", "Charlie"]
        assert out.get_attribute("sql.row_count") == "2"

    def test_execute_sql_insert_via_pool_commits(self, temp_db):
        from services.db_connection_pool import DBConnectionPoolService
        svc = DBConnectionPoolService({
            "_service_id": "db", "db_type": "sqlite", "database": temp_db})
        task = ExecuteSQLTask({
            "sql_query": "INSERT INTO users (name, age, city) "
                         "VALUES ('Eve', 40, 'Nice')",
            "service_id": "db"})
        task.set_services({"db": svc})
        out = task.execute(FlowFile(content=b""))[0]
        assert out.get_attribute("sql.rows_affected") == "1"
        # Re-read through a fresh connection: the routed write was committed.
        conn = sqlite3.connect(temp_db)
        n = conn.execute("SELECT COUNT(*) FROM users WHERE name='Eve'").fetchone()[0]
        conn.close()
        assert n == 1


class TestPutSQLTask:

    @pytest.fixture
    def temp_db_with_table(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                name TEXT,
                price REAL
            )
        ''')
        conn.commit()
        conn.close()
        return db_path

    def test_put_sql_with_content(self, temp_db_with_table):
        task = PutSQLTask({
            "sql_statement": "INSERT INTO products (name, price) VALUES ('${content}', 19.99)",
            "db_path": temp_db_with_table
        })
        ff = FlowFile(content=b"Widget")
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("sql.rows_affected") == "1"

        conn = sqlite3.connect(temp_db_with_table)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM products WHERE name = 'Widget'")
        result = cursor.fetchone()
        conn.close()
        assert result is not None
        assert result[0] == "Widget"

    def test_put_sql_update(self, temp_db_with_table):
        conn = sqlite3.connect(temp_db_with_table)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO products (name, price) VALUES ('Test', 10.0)")
        conn.commit()
        conn.close()

        task = PutSQLTask({
            "sql_statement": "UPDATE products SET price = CAST('${content}' AS REAL) WHERE name = 'Test'",
            "db_path": temp_db_with_table
        })
        ff = FlowFile(content=b"29.99")
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("sql.rows_affected") == "1"

        conn = sqlite3.connect(temp_db_with_table)
        cursor = conn.cursor()
        cursor.execute("SELECT price FROM products WHERE name = 'Test'")
        result = cursor.fetchone()
        conn.close()
        assert result is not None
        assert result[0] == 29.99

    def test_put_sql_missing_statement(self, temp_db_with_table):
        with pytest.raises(ValueError, match="sql_statement"):
            PutSQLTask({"db_path": temp_db_with_table})

    def test_put_sql_named_params_sqlite(self, temp_db_with_table):
        task = PutSQLTask({
            "sql_statement": "INSERT INTO products (name, price) "
                             "VALUES (:name, :price)",
            "params": '{"name": "${tg.name}", "price": 4.5}',
            "db_path": temp_db_with_table,
        })
        ff = FlowFile(content=b"", attributes={"tg.name": "Gadget"})
        out = task.execute(ff)[0]
        assert out.get_attribute("sql.rows_affected") == "1"
        conn = sqlite3.connect(temp_db_with_table)
        row = conn.execute(
            "SELECT name, price FROM products WHERE name='Gadget'").fetchone()
        conn.close()
        assert row == ("Gadget", 4.5)

    def test_put_sql_named_params_are_injection_safe(self, temp_db_with_table):
        # A value that looks like SQL must be bound, not interpreted.
        evil = "x'); DROP TABLE products; --"
        task = PutSQLTask({
            "sql_statement": "INSERT INTO products (name, price) "
                             "VALUES (:name, 1.0)",
            "params": '{"name": "${tg.name}"}',
            "db_path": temp_db_with_table,
        })
        task.execute(FlowFile(content=b"", attributes={"tg.name": evil}))
        conn = sqlite3.connect(temp_db_with_table)
        # Table still exists and stored the literal string.
        row = conn.execute(
            "SELECT name FROM products WHERE price=1.0").fetchone()
        conn.close()
        assert row[0] == evil

    def test_named_param_pyformat_translation(self):
        from tasks.data.execute_sql import _to_pyformat
        assert _to_pyformat(
            "UPDATE t SET a=:a WHERE id::text = :id"
        ) == "UPDATE t SET a=%(a)s WHERE id::text = %(id)s"


class TestInputPortTask:

    def test_input_port_default_name(self):
        task = InputPortTask({})
        ff = FlowFile(content=b"test data", attributes={"key": "value"})
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("port.name") == "input"
        assert results[0].get_content() == b"test data"
        assert results[0].get_attribute("key") == "value"

    def test_input_port_custom_name(self):
        task = InputPortTask({"port_name": "my_input"})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("port.name") == "my_input"

    def test_input_port_passthrough(self):
        task = InputPortTask({"port_name": "test"})
        ff = FlowFile(
            content=b"original content",
            attributes={"attr1": "val1", "attr2": "val2"}
        )
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_content() == b"original content"
        assert results[0].get_attribute("attr1") == "val1"
        assert results[0].get_attribute("attr2") == "val2"


class TestOutputPortTask:

    def test_output_port_default_name(self):
        task = OutputPortTask({})
        ff = FlowFile(content=b"test data", attributes={"key": "value"})
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("port.name") == "output"
        assert results[0].get_content() == b"test data"

    def test_output_port_custom_name(self):
        task = OutputPortTask({"port_name": "my_output"})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("port.name") == "my_output"

    def test_output_port_passthrough(self):
        task = OutputPortTask({"port_name": "test"})
        ff = FlowFile(
            content=b"original content",
            attributes={"attr1": "val1", "attr2": "val2"}
        )
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_content() == b"original content"
        assert results[0].get_attribute("attr1") == "val1"
        assert results[0].get_attribute("attr2") == "val2"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
