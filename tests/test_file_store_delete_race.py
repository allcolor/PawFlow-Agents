"""A FileStore write must not outlive the conversation it belongs to.

beta.55 moved the bytes outside the global lock so a multi-megabyte upload
stops stalling every other caller. That is the right trade, but it opened a
window: ``delete_by`` snapshots the entries it can see, and a store() that
reserved its path before the wipe is not in that snapshot yet. It used to
reacquire the lock afterwards and register anyway -- resurrecting a file for a
conversation the user had just deleted, with nothing left to ever collect it.
"""

import threading

from core.file_store import FileStore


def _store(tmp_path):
    return FileStore(base_dir=str(tmp_path))


def test_a_write_that_lands_after_its_conversation_was_wiped_is_discarded(tmp_path):
    store = _store(tmp_path)
    store.store("before.txt", b"x", conversation_id="conv", user_id="u")

    started = threading.Event()
    wiped = threading.Event()
    original = store._reserve_scope

    def _slow_reserve(conversation_id, user_id, name):
        result = original(conversation_id, user_id, name)
        # The path is reserved; hand control to the deletion, exactly as a slow
        # disk write would.
        started.set()
        wiped.wait(5)
        return result

    store._reserve_scope = _slow_reserve

    result = {}

    def _writer():
        result["id"] = store.store(
            "during.txt", b"y" * 1024, conversation_id="conv", user_id="u")

    thread = threading.Thread(target=_writer)
    thread.start()
    assert started.wait(5)
    assert store.delete_by(conversation_id="conv") == 1
    wiped.set()
    thread.join(10)

    assert result["id"] == "", "the write was registered after the wipe"
    remaining = [e for e in store._entries.values()
                 if e.get("conversation_id") == "conv"]
    assert remaining == [], f"conversation survived its deletion: {remaining}"


def test_the_abandoned_bytes_do_not_stay_on_disk(tmp_path):
    store = _store(tmp_path)
    store.store("seed.txt", b"x", conversation_id="conv", user_id="u")

    started = threading.Event()
    wiped = threading.Event()
    original = store._reserve_scope
    reserved = {}

    def _slow_reserve(conversation_id, user_id, name):
        result = original(conversation_id, user_id, name)
        reserved["path"] = result[2]
        started.set()
        wiped.wait(5)
        return result

    store._reserve_scope = _slow_reserve

    thread = threading.Thread(
        target=lambda: store.store("during.txt", b"y" * 1024,
                                   conversation_id="conv", user_id="u"))
    thread.start()
    assert started.wait(5)
    store.delete_by(conversation_id="conv")
    wiped.set()
    thread.join(10)

    assert not reserved["path"].exists(), "orphan bytes left behind"


def test_store_file_is_guarded_the_same_way(tmp_path):
    store = _store(tmp_path)
    store.store("seed.txt", b"x", conversation_id="conv", user_id="u")
    source = tmp_path / "source.bin"
    source.write_bytes(b"z" * 2048)

    started = threading.Event()
    wiped = threading.Event()
    original = store._reserve_scope

    def _slow_reserve(conversation_id, user_id, name):
        result = original(conversation_id, user_id, name)
        started.set()
        wiped.wait(5)
        return result

    store._reserve_scope = _slow_reserve

    result = {}
    thread = threading.Thread(
        target=lambda: result.__setitem__(
            "id", store.store_file("during.bin", str(source),
                                   conversation_id="conv", user_id="u")))
    thread.start()
    assert started.wait(5)
    store.delete_by(conversation_id="conv")
    wiped.set()
    thread.join(10)

    assert result["id"] == ""


def test_an_ordinary_write_is_untouched(tmp_path):
    """The guard must only fire on a real wipe, not on every write."""
    store = _store(tmp_path)
    file_id = store.store("a.txt", b"hello", conversation_id="conv", user_id="u")
    assert file_id
    assert store.get(file_id, user_id="u")


def test_a_wipe_of_another_conversation_does_not_discard_this_write(tmp_path):
    store = _store(tmp_path)
    store.store("other.txt", b"x", conversation_id="other", user_id="u")

    started = threading.Event()
    wiped = threading.Event()
    original = store._reserve_scope

    def _slow_reserve(conversation_id, user_id, name):
        result = original(conversation_id, user_id, name)
        started.set()
        wiped.wait(5)
        return result

    store._reserve_scope = _slow_reserve

    result = {}
    thread = threading.Thread(
        target=lambda: result.__setitem__(
            "id", store.store("mine.txt", b"y", conversation_id="conv",
                              user_id="u")))
    thread.start()
    assert started.wait(5)
    store.delete_by(conversation_id="other")
    wiped.set()
    thread.join(10)

    assert result["id"], "an unrelated wipe discarded this write"


def test_a_category_scoped_delete_is_not_a_wipe(tmp_path):
    """Only a whole-conversation delete bumps the counter; a category sweep
    leaves the conversation alive and must not discard concurrent writes."""
    store = _store(tmp_path)
    store.store("t.txt", b"x", conversation_id="conv", user_id="u",
                category="tool_result")
    before = store._wipe_count("conv")
    store.delete_by(conversation_id="conv", category="tool_result")
    assert store._wipe_count("conv") == before
