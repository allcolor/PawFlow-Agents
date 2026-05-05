import threading

from services.filesystem_service import RelayService


class _Reader:
    pass


def _pending(reader=None):
    evt = threading.Event()
    holder = {}
    if reader is not None:
        holder["_relay_reader"] = reader
    return evt, holder


def test_clear_relay_unblocks_pending_for_closed_connection_only():
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    old_reader = _Reader()
    live_reader = _Reader()
    old_evt, old_holder = _pending(old_reader)
    live_evt, live_holder = _pending(live_reader)

    svc._relay_pool = [
        {"reader": old_reader, "tasks": set()},
        {"reader": live_reader, "tasks": set()},
    ]
    svc._pending = {
        "old": (old_evt, old_holder),
        "live": (live_evt, live_holder),
    }

    svc._clear_relay(reader=old_reader)

    assert old_evt.is_set()
    assert old_holder["error"] == "Relay disconnected"
    assert not live_evt.is_set()
    assert "old" not in svc._pending
    assert "live" in svc._pending
    assert svc._relay_pool == [{"reader": live_reader, "tasks": set()}]


def test_clear_last_relay_unblocks_all_pending_even_without_reader_tag():
    svc = RelayService({"_service_id": "fs1", "token": "tok"})
    reader = _Reader()
    tagged_evt, tagged_holder = _pending(reader)
    untagged_evt, untagged_holder = _pending()

    svc._relay_pool = [{"reader": reader, "tasks": set()}]
    svc._pending = {
        "tagged": (tagged_evt, tagged_holder),
        "untagged": (untagged_evt, untagged_holder),
    }

    svc._clear_relay(reader=reader)

    assert tagged_evt.is_set()
    assert untagged_evt.is_set()
    assert tagged_holder["error"] == "Relay disconnected"
    assert untagged_holder["error"] == "Relay disconnected"
    assert svc._pending == {}
    assert svc._relay_pool == []
