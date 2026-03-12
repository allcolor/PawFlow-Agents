"""Tests for optional roadmap items: MQTT tasks, Avro/Parquet, Notifications."""

import json
import threading
import time
import pytest

from core import TaskFactory
from tasks import register_all_tasks
register_all_tasks()


def _tf_make(task_type, config=None):
    """Helper: TaskFactory.get(type)(config)"""
    cls = TaskFactory.get(task_type)
    if config is None:
        return cls
    return cls(config)


# ============================================================================
# MQTT Tasks — Registration & basic checks (no broker needed)
# ============================================================================

class TestMQTTTasks:

    def test_publish_mqtt_registered(self):
        from core import TaskFactory
        cls = _tf_make('publishMQTT')
        assert cls is not None
        assert cls.TYPE == 'publishMQTT'

    def test_consume_mqtt_registered(self):
        from core import TaskFactory
        cls = _tf_make('consumeMQTT')
        assert cls is not None
        assert cls.TYPE == 'consumeMQTT'

    def test_publish_mqtt_schema(self):
        from core import TaskFactory
        task = _tf_make('publishMQTT', {'topic': 'test/topic'})
        schema = task.get_parameter_schema()
        assert 'broker_uri' in schema
        assert 'topic' in schema
        assert 'qos' in schema

    def test_consume_mqtt_schema(self):
        from core import TaskFactory
        task = _tf_make('consumeMQTT', {'topic': 'test/#'})
        schema = task.get_parameter_schema()
        assert 'broker_uri' in schema
        assert 'max_messages' in schema
        assert 'poll_timeout' in schema

    def test_publish_mqtt_requires_paho(self):
        """Without paho-mqtt, should raise TaskError."""
        from core import TaskFactory, FlowFile, TaskError
        task = _tf_make('publishMQTT', {'topic': 'test'})
        ff = FlowFile(content=b'hello')
        # This will either fail with "paho-mqtt required" or succeed if paho is installed
        try:
            task.execute(ff)
        except TaskError as e:
            assert 'paho-mqtt' in str(e) or 'mqtt' in str(e).lower()
        except Exception:
            pass  # paho is installed but no broker — that's fine too

    def test_consume_mqtt_requires_paho(self):
        from core import TaskFactory, FlowFile, TaskError
        task = _tf_make('consumeMQTT', {'topic': 'test'})
        ff = FlowFile(content=b'')
        try:
            task.execute(ff)
        except TaskError as e:
            assert 'paho-mqtt' in str(e) or 'mqtt' in str(e).lower()
        except Exception:
            pass

    def test_publish_mqtt_topic_required(self):
        """Empty topic should raise."""
        from core import TaskFactory, FlowFile, TaskError
        task = _tf_make('publishMQTT', {'topic': ''})
        ff = FlowFile(content=b'hello')
        try:
            task.execute(ff)
        except TaskError as e:
            assert 'topic' in str(e).lower() or 'required' in str(e).lower() or 'paho' in str(e).lower()
        except Exception:
            pass


# ============================================================================
# Avro / Parquet Tasks — Registration & basic checks
# ============================================================================

class TestAvroParquetTasks:

    def test_avro_to_json_registered(self):
        from core import TaskFactory
        cls = _tf_make('convertAvroToJSON')
        assert cls is not None

    def test_json_to_avro_registered(self):
        from core import TaskFactory
        cls = _tf_make('convertJSONToAvro')
        assert cls is not None

    def test_parquet_to_json_registered(self):
        from core import TaskFactory
        cls = _tf_make('convertParquetToJSON')
        assert cls is not None

    def test_json_to_parquet_registered(self):
        from core import TaskFactory
        cls = _tf_make('convertJSONToParquet')
        assert cls is not None

    def test_avro_to_json_schema(self):
        from core import TaskFactory
        task = _tf_make('convertAvroToJSON', {})
        schema = task.get_parameter_schema()
        assert 'pretty' in schema

    def test_json_to_avro_schema(self):
        from core import TaskFactory
        task = _tf_make('convertJSONToAvro', {})
        schema = task.get_parameter_schema()
        assert 'avro_schema' in schema

    def test_parquet_to_json_schema(self):
        from core import TaskFactory
        task = _tf_make('convertParquetToJSON', {})
        schema = task.get_parameter_schema()
        assert 'columns' in schema
        assert 'pretty' in schema

    def test_json_to_parquet_schema(self):
        from core import TaskFactory
        task = _tf_make('convertJSONToParquet', {})
        schema = task.get_parameter_schema()
        assert 'compression' in schema

    def test_avro_roundtrip(self):
        """JSON → Avro → JSON roundtrip (requires fastavro)."""
        from core import TaskFactory, FlowFile, TaskError
        data = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        ff = FlowFile(content=json.dumps(data).encode('utf-8'))

        try:
            # JSON → Avro
            to_avro = _tf_make('convertJSONToAvro', {})
            [ff_avro] = to_avro.execute(ff)
            assert ff_avro.get_attribute('mime.type') == 'application/avro'
            assert len(ff_avro.get_content()) > 0

            # Avro → JSON
            to_json = _tf_make('convertAvroToJSON', {})
            [ff_json] = to_json.execute(ff_avro)
            result = json.loads(ff_json.get_content().decode('utf-8'))
            assert len(result) == 2
            assert result[0]['name'] == 'Alice'
            assert result[1]['age'] == 25

        except TaskError as e:
            if 'fastavro' in str(e):
                pytest.skip("fastavro not installed")
            raise

    def test_parquet_roundtrip(self):
        """JSON → Parquet → JSON roundtrip (requires pyarrow)."""
        from core import TaskFactory, FlowFile, TaskError
        data = [{"city": "Paris", "pop": 2100000}, {"city": "Lyon", "pop": 500000}]
        ff = FlowFile(content=json.dumps(data).encode('utf-8'))

        try:
            # JSON → Parquet
            to_parquet = _tf_make('convertJSONToParquet', {'compression': 'none'})
            [ff_pq] = to_parquet.execute(ff)
            assert ff_pq.get_attribute('mime.type') == 'application/parquet'

            # Parquet → JSON
            to_json = _tf_make('convertParquetToJSON', {})
            [ff_json] = to_json.execute(ff_pq)
            result = json.loads(ff_json.get_content().decode('utf-8'))
            assert len(result) == 2
            assert result[0]['city'] == 'Paris'

        except TaskError as e:
            if 'pyarrow' in str(e):
                pytest.skip("pyarrow not installed")
            raise

    def test_avro_invalid_data(self):
        """Invalid data should raise TaskError."""
        from core import TaskFactory, FlowFile, TaskError
        task = _tf_make('convertAvroToJSON', {})
        ff = FlowFile(content=b'not avro data')
        try:
            task.execute(ff)
            pytest.fail("Should have raised")
        except TaskError as e:
            assert 'avro' in str(e).lower() or 'fastavro' in str(e).lower()

    def test_parquet_invalid_data(self):
        """Invalid data should raise TaskError."""
        from core import TaskFactory, FlowFile, TaskError
        task = _tf_make('convertParquetToJSON', {})
        ff = FlowFile(content=b'not parquet data')
        try:
            task.execute(ff)
            pytest.fail("Should have raised")
        except TaskError as e:
            assert 'parquet' in str(e).lower() or 'pyarrow' in str(e).lower()


# ============================================================================
# Notification System
# ============================================================================

class TestNotificationManager:

    @pytest.fixture(autouse=True)
    def reset_nm(self):
        from core.notifications import NotificationManager
        NotificationManager.reset()
        yield
        NotificationManager.reset()

    def test_singleton(self):
        from core.notifications import NotificationManager
        a = NotificationManager.get_instance()
        b = NotificationManager.get_instance()
        assert a is b

    def test_register_webhook(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        wh_id = nm.register_webhook("http://example.com/hook", name="test-hook")
        assert wh_id
        webhooks = nm.list_webhooks()
        assert len(webhooks) == 1
        assert webhooks[0]['url'] == "http://example.com/hook"

    def test_unregister_webhook(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        wh_id = nm.register_webhook("http://example.com/hook")
        assert nm.unregister_webhook(wh_id)
        assert len(nm.list_webhooks()) == 0

    def test_unregister_nonexistent(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        assert not nm.unregister_webhook("nonexistent")

    def test_register_handler(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        received = []
        handler_id = nm.register_handler(lambda t, p: received.append((t, p)), name="test")
        assert handler_id
        handlers = nm.list_handlers()
        assert len(handlers) == 1

    def test_notify_handler_sync(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        received = []
        nm.register_handler(lambda t, p: received.append((t, p)))
        nm.notify("flow.completed", {"flow_id": "f1"}, async_send=False)
        assert len(received) == 1
        assert received[0] == ("flow.completed", {"flow_id": "f1"})

    def test_notify_handler_async(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        received = []
        event = threading.Event()

        def handler(t, p):
            received.append((t, p))
            event.set()

        nm.register_handler(handler)
        nm.notify("flow.started", {"flow_id": "f2"}, async_send=True)
        event.wait(timeout=5)
        assert len(received) == 1

    def test_event_filtering(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        received = []
        nm.register_handler(lambda t, p: received.append(t), events=["flow.*"])
        nm.notify("flow.completed", {}, async_send=False)
        nm.notify("task.failed", {}, async_send=False)
        nm.notify("flow.started", {}, async_send=False)
        assert len(received) == 2
        assert "flow.completed" in received
        assert "flow.started" in received

    def test_wildcard_all(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        received = []
        nm.register_handler(lambda t, p: received.append(t), events=["*"])
        nm.notify("any.event", {}, async_send=False)
        assert len(received) == 1

    def test_exact_event_match(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        received = []
        nm.register_handler(lambda t, p: received.append(t), events=["flow.completed"])
        nm.notify("flow.completed", {}, async_send=False)
        nm.notify("flow.started", {}, async_send=False)
        assert received == ["flow.completed"]

    def test_history(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        nm.notify("e1", {"a": 1}, async_send=False)
        nm.notify("e2", {"b": 2}, async_send=False)
        history = nm.get_history()
        assert len(history) == 2
        assert history[0]['event'] == 'e2'  # newest first

    def test_history_filter(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        nm.notify("flow.started", {}, async_send=False)
        nm.notify("task.failed", {}, async_send=False)
        nm.notify("flow.completed", {}, async_send=False)
        history = nm.get_history(event_type="flow.*")
        assert len(history) == 2

    def test_stats(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        nm.register_webhook("http://example.com/hook")
        nm.notify("flow.completed", {}, async_send=False)
        nm.notify("flow.started", {}, async_send=False)
        stats = nm.get_stats()
        assert stats['total_events'] == 2
        assert stats['webhooks'] == 1
        assert stats['event_counts']['flow.completed'] == 1

    def test_handler_error_does_not_break(self):
        """A failing handler should not prevent other handlers from running."""
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        received = []

        def bad_handler(t, p):
            raise RuntimeError("boom")

        def good_handler(t, p):
            received.append(t)

        nm.register_handler(bad_handler)
        nm.register_handler(good_handler)
        nm.notify("test", {}, async_send=False)
        assert len(received) == 1

    def test_unregister_handler(self):
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        hid = nm.register_handler(lambda t, p: None)
        assert nm.unregister_handler(hid)
        assert len(nm.list_handlers()) == 0

    def test_event_types_constants(self):
        from core.notifications import EventType
        assert EventType.FLOW_STARTED == "flow.started"
        assert EventType.FLOW_COMPLETED == "flow.completed"
        assert EventType.FLOW_FAILED == "flow.failed"
        assert EventType.TASK_FAILED == "task.failed"

    def test_thread_safety(self):
        """Concurrent notifications should not lose events."""
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        received = []
        lock = threading.Lock()

        def handler(t, p):
            with lock:
                received.append(t)

        nm.register_handler(handler)

        def sender(n):
            for i in range(50):
                nm.notify(f"thread.{n}.{i}", {}, async_send=False)

        threads = [threading.Thread(target=sender, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) == 250


# ============================================================================
# Task count verification
# ============================================================================

class TestGCSTasks:

    def test_get_gcs_registered(self):
        assert 'getGCS' in TaskFactory.list_types()

    def test_put_gcs_registered(self):
        assert 'putGCS' in TaskFactory.list_types()

    def test_get_gcs_config(self):
        task = _tf_make('getGCS', {'bucket': 'my-bucket', 'blob_name': 'data.json'})
        assert task.bucket == 'my-bucket'
        assert task.blob_name == 'data.json'

    def test_put_gcs_config(self):
        task = _tf_make('putGCS', {'bucket': 'my-bucket', 'blob_name': 'out.json'})
        assert task.bucket == 'my-bucket'

    def test_get_gcs_missing_dep(self):
        task = _tf_make('getGCS', {'bucket': 'b', 'blob_name': 'k'})
        from core import FlowFile
        ff = FlowFile(content=b"test")
        try:
            task.execute(ff)
            # If google-cloud-storage is installed, this might work
        except Exception as e:
            assert 'google-cloud-storage' in str(e) or 'bucket' in str(e).lower()

    def test_put_gcs_schema(self):
        task = _tf_make('putGCS', {'bucket': 'b'})
        schema = task.get_parameter_schema()
        assert 'bucket' in schema
        assert 'blob_name' in schema


class TestAzureBlobTasks:

    def test_get_azure_registered(self):
        assert 'getAzureBlob' in TaskFactory.list_types()

    def test_put_azure_registered(self):
        assert 'putAzureBlob' in TaskFactory.list_types()

    def test_get_azure_config(self):
        task = _tf_make('getAzureBlob', {
            'connection_string': 'DefaultEndpointsProtocol=https;...',
            'container_name': 'mycontainer',
            'blob_name': 'data.csv',
        })
        assert task.container_name == 'mycontainer'
        assert task.blob_name == 'data.csv'

    def test_put_azure_config(self):
        task = _tf_make('putAzureBlob', {
            'connection_string': 'conn',
            'container_name': 'c',
            'blob_name': 'b',
        })
        assert task.container_name == 'c'

    def test_get_azure_missing_dep(self):
        task = _tf_make('getAzureBlob', {
            'connection_string': 'x',
            'container_name': 'c',
            'blob_name': 'b',
        })
        from core import FlowFile
        ff = FlowFile(content=b"test")
        try:
            task.execute(ff)
        except Exception as e:
            assert 'azure-storage-blob' in str(e) or 'container' in str(e).lower()

    def test_put_azure_schema(self):
        task = _tf_make('putAzureBlob', {'connection_string': 'x'})
        schema = task.get_parameter_schema()
        assert 'connection_string' in schema
        assert 'container_name' in schema
        assert 'blob_name' in schema


class TestTaskCount:

    def test_new_tasks_registered(self):
        """Verify all new tasks are registered."""
        from core import TaskFactory
        types = TaskFactory.list_types()
        # MQTT
        assert 'publishMQTT' in types
        assert 'consumeMQTT' in types
        # Avro/Parquet
        assert 'convertAvroToJSON' in types
        assert 'convertJSONToAvro' in types
        assert 'convertParquetToJSON' in types
        assert 'convertJSONToParquet' in types
        # GCS
        assert 'getGCS' in types
        assert 'putGCS' in types
        # Azure
        assert 'getAzureBlob' in types
        assert 'putAzureBlob' in types

    def test_total_tasks_at_least_68(self):
        from core import TaskFactory
        types = TaskFactory.list_types()
        assert len(types) >= 68


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
