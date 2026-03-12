"""Tests for queue management features: peek_all, get_connection, clear."""

import pytest
from core import FlowFile
from core.connection import Connection, ConnectionManager
from core.prioritizer import PrioritizedQueue, PrioritizerType


# --- PrioritizedQueue.peek_all ---

class TestPrioritizedQueuePeekAll:

    def test_peek_all_empty(self):
        q = PrioritizedQueue()
        result = q.peek_all()
        assert result == []

    def test_peek_all_returns_all_items(self):
        q = PrioritizedQueue()
        ff1 = FlowFile(content=b"one")
        ff2 = FlowFile(content=b"two")
        ff3 = FlowFile(content=b"three")
        q.put(ff1)
        q.put(ff2)
        q.put(ff3)
        result = q.peek_all()
        assert len(result) == 3
        assert result[0] is ff1
        assert result[1] is ff2
        assert result[2] is ff3

    def test_peek_all_does_not_remove(self):
        q = PrioritizedQueue()
        q.put(FlowFile(content=b"data"))
        q.peek_all()
        assert q.size() == 1

    def test_peek_all_respects_limit(self):
        q = PrioritizedQueue()
        for i in range(10):
            q.put(FlowFile(content=f"item{i}".encode()))
        result = q.peek_all(limit=3)
        assert len(result) == 3

    def test_peek_all_limit_larger_than_size(self):
        q = PrioritizedQueue()
        q.put(FlowFile(content=b"only"))
        result = q.peek_all(limit=100)
        assert len(result) == 1

    def test_peek_all_returns_copy(self):
        """Modifying the returned list should not affect the queue."""
        q = PrioritizedQueue()
        q.put(FlowFile(content=b"data"))
        result = q.peek_all()
        result.clear()
        assert q.size() == 1


# --- Connection.peek_all ---

class TestConnectionPeekAll:

    def test_peek_all_empty_connection(self):
        conn = Connection("src", "tgt")
        assert conn.peek_all() == []

    def test_peek_all_with_items(self):
        conn = Connection("src", "tgt")
        ff1 = FlowFile(content=b"a")
        ff2 = FlowFile(content=b"b")
        conn.enqueue(ff1)
        conn.enqueue(ff2)
        result = conn.peek_all()
        assert len(result) == 2
        assert result[0] is ff1

    def test_peek_all_preserves_queue(self):
        conn = Connection("src", "tgt")
        conn.enqueue(FlowFile(content=b"data"))
        conn.peek_all()
        assert conn.queue_size() == 1

    def test_peek_all_with_limit(self):
        conn = Connection("src", "tgt")
        for i in range(20):
            conn.enqueue(FlowFile(content=f"ff{i}".encode()))
        result = conn.peek_all(limit=5)
        assert len(result) == 5


# --- ConnectionManager.get_connection ---

class TestConnectionManagerGetConnection:

    def test_get_connection_found(self):
        mgr = ConnectionManager()
        conn = Connection("a", "b")
        mgr.add_connection(conn)
        result = mgr.get_connection("a", "b")
        assert result is conn

    def test_get_connection_not_found(self):
        mgr = ConnectionManager()
        mgr.add_connection(Connection("a", "b"))
        assert mgr.get_connection("x", "y") is None

    def test_get_connection_multiple(self):
        mgr = ConnectionManager()
        c1 = Connection("a", "b")
        c2 = Connection("a", "c")
        c3 = Connection("b", "c")
        mgr.add_connection(c1)
        mgr.add_connection(c2)
        mgr.add_connection(c3)
        assert mgr.get_connection("a", "c") is c2
        assert mgr.get_connection("b", "c") is c3

    def test_connections_property(self):
        mgr = ConnectionManager()
        c1 = Connection("a", "b")
        c2 = Connection("b", "c")
        mgr.add_connection(c1)
        mgr.add_connection(c2)
        conns = mgr.connections
        assert len(conns) == 2
        # Returns a copy
        conns.clear()
        assert len(mgr.connections) == 2


# --- Connection.clear ---

class TestConnectionClear:

    def test_clear_empties_queue(self):
        conn = Connection("src", "tgt")
        for i in range(5):
            conn.enqueue(FlowFile(content=f"item{i}".encode()))
        assert conn.queue_size() == 5
        conn.clear()
        assert conn.queue_size() == 0
        assert conn.queue_bytes() == 0

    def test_clear_resets_bytes(self):
        conn = Connection("src", "tgt")
        conn.enqueue(FlowFile(content=b"x" * 1000))
        assert conn.queue_bytes() > 0
        conn.clear()
        assert conn.queue_bytes() == 0


# --- ConnectionManager.clear_all ---

class TestConnectionManagerClearAll:

    def test_clear_all(self):
        mgr = ConnectionManager()
        c1 = Connection("a", "b")
        c2 = Connection("b", "c")
        mgr.add_connection(c1)
        mgr.add_connection(c2)
        c1.enqueue(FlowFile(content=b"data1"))
        c2.enqueue(FlowFile(content=b"data2"))
        mgr.clear_all()
        assert c1.queue_size() == 0
        assert c2.queue_size() == 0
