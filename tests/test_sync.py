"""Tests pour le systeme de synchronisation (Wait/Notify + DistributedMapCache)."""

import pytest
import threading
import time

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile
from core.signals import SignalRegistry
from tasks.control.wait_notify import WaitTask, NotifyTask
from tasks.data.dist_cache_tasks import FetchDistributedMapCacheTask, PutDistributedMapCacheTask
from services.distributed_cache import DistributedMapCacheService, get_default_cache


class TestSignalRegistry:

    def setup_method(self):
        SignalRegistry.get_instance().clear_all()

    def test_notify_creates_signal(self):
        reg = SignalRegistry.get_instance()
        result = reg.notify("test_signal", "hello", 1)
        assert result["count"] == 1
        assert result["value"] == "hello"

    def test_notify_increments(self):
        reg = SignalRegistry.get_instance()
        reg.notify("sig1")
        reg.notify("sig1")
        result = reg.notify("sig1")
        assert result["count"] == 3

    def test_check_below_threshold(self):
        reg = SignalRegistry.get_instance()
        reg.notify("sig1")
        assert not reg.check("sig1", target_count=3)

    def test_check_at_threshold(self):
        reg = SignalRegistry.get_instance()
        reg.notify("sig1", delta=5)
        assert reg.check("sig1", target_count=5)

    def test_check_nonexistent(self):
        reg = SignalRegistry.get_instance()
        assert not reg.check("nonexistent")

    def test_wait_for_immediate(self):
        reg = SignalRegistry.get_instance()
        reg.notify("ready", delta=1)
        assert reg.wait_for("ready", target_count=1, timeout=1)

    def test_wait_for_timeout(self):
        reg = SignalRegistry.get_instance()
        assert not reg.wait_for("never", target_count=1, timeout=0.5)

    def test_wait_for_async_signal(self):
        reg = SignalRegistry.get_instance()

        def delayed_notify():
            time.sleep(0.3)
            reg.notify("async_sig", "done")

        t = threading.Thread(target=delayed_notify)
        t.start()

        result = reg.wait_for("async_sig", target_count=1, timeout=5)
        assert result is True
        assert reg.get_value("async_sig") == "done"
        t.join()

    def test_clear(self):
        reg = SignalRegistry.get_instance()
        reg.notify("to_clear")
        reg.clear("to_clear")
        assert reg.get_signal("to_clear") is None

    def test_list_signals(self):
        reg = SignalRegistry.get_instance()
        reg.notify("a")
        reg.notify("b")
        signals = reg.list_signals()
        assert "a" in signals
        assert "b" in signals


class TestNotifyTask:

    def setup_method(self):
        SignalRegistry.get_instance().clear_all()

    def test_notify_basic(self):
        task = NotifyTask({"signal_id": "my_signal"})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("notify.signal.id") == "my_signal"
        assert results[0].get_attribute("notify.signal.count") == "1"

    def test_notify_with_value(self):
        task = NotifyTask({"signal_id": "val_sig", "signal_value": "completed"})
        ff = FlowFile(content=b"data")
        task.execute(ff)

        reg = SignalRegistry.get_instance()
        assert reg.get_value("val_sig") == "completed"

    def test_notify_missing_signal_id(self):
        """Validation catches missing required param at init time."""
        with pytest.raises(ValueError, match="signal_id"):
            NotifyTask({})

    def test_notify_multiple_increments(self):
        task = NotifyTask({"signal_id": "multi", "delta": 3})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)
        assert results[0].get_attribute("notify.signal.count") == "3"


class TestWaitTask:

    def setup_method(self):
        SignalRegistry.get_instance().clear_all()

    def test_wait_immediate_signal(self):
        reg = SignalRegistry.get_instance()
        reg.notify("ready_sig")

        task = WaitTask({"signal_id": "ready_sig", "timeout": 2})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_attribute("wait.status") == "signaled"

    def test_wait_timeout(self):
        task = WaitTask({"signal_id": "never_sig", "timeout": 0.5})
        ff = FlowFile(content=b"data")
        with pytest.raises(Exception, match="timeout"):
            task.execute(ff)

    def test_wait_for_async_notify(self):
        def delayed():
            time.sleep(0.3)
            SignalRegistry.get_instance().notify("delayed_sig", "hello")

        t = threading.Thread(target=delayed)
        t.start()

        task = WaitTask({"signal_id": "delayed_sig", "timeout": 5})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)

        assert results[0].get_attribute("wait.status") == "signaled"
        assert results[0].get_attribute("wait.signal.value") == "hello"
        t.join()

    def test_wait_with_target_count(self):
        reg = SignalRegistry.get_instance()
        reg.notify("count_sig", delta=2)

        task = WaitTask({"signal_id": "count_sig", "target_count": 2, "timeout": 1})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)
        assert results[0].get_attribute("wait.status") == "signaled"


class TestDistributedMapCache:

    def test_put_and_get(self):
        cache = DistributedMapCacheService({"backend": "memory"})
        cache.put("key1", b"value1")
        assert cache.get("key1") == b"value1"

    def test_get_missing(self):
        cache = DistributedMapCacheService({"backend": "memory"})
        assert cache.get("missing") is None

    def test_delete(self):
        cache = DistributedMapCacheService({"backend": "memory"})
        cache.put("key1", b"val")
        assert cache.delete("key1") is True
        assert cache.get("key1") is None

    def test_contains(self):
        cache = DistributedMapCacheService({"backend": "memory"})
        cache.put("exists", b"yes")
        assert cache.contains("exists") is True
        assert cache.contains("nope") is False

    def test_size_and_clear(self):
        cache = DistributedMapCacheService({"backend": "memory"})
        cache.put("a", b"1")
        cache.put("b", b"2")
        assert cache.size() == 2
        cache.clear()
        assert cache.size() == 0

    def test_keys(self):
        cache = DistributedMapCacheService({"backend": "memory"})
        cache.put("x", b"1")
        cache.put("y", b"2")
        keys = cache.keys()
        assert sorted(keys) == ["x", "y"]


class TestDistCacheTasks:

    def test_put_and_fetch(self):
        # Put content
        put_task = PutDistributedMapCacheTask({"cache_key": "test_key"})
        ff = FlowFile(content=b"cached data")
        results = put_task.execute(ff)
        assert results[0].get_attribute("cache.key") == "test_key"

        # Fetch it back
        fetch_task = FetchDistributedMapCacheTask({"cache_key": "test_key"})
        ff2 = FlowFile(content=b"empty")
        results2 = fetch_task.execute(ff2)
        assert results2[0].get_content() == b"cached data"
        assert results2[0].get_attribute("cache.hit") == "true"

    def test_fetch_miss(self):
        fetch_task = FetchDistributedMapCacheTask({"cache_key": "nonexistent_key_xyz"})
        ff = FlowFile(content=b"data")
        with pytest.raises(Exception, match="introuvable"):
            fetch_task.execute(ff)

    def test_put_with_ttl(self):
        put_task = PutDistributedMapCacheTask({"cache_key": "ttl_key", "ttl": 3600})
        ff = FlowFile(content=b"expiring data")
        results = put_task.execute(ff)
        assert results[0].get_attribute("cache.key") == "ttl_key"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
