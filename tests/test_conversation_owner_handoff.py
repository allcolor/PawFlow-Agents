"""A conversation handed to a collaborator must arrive whole.

When an owner's account is gone, the first write by a write collaborator moves
the conversation into their directory -- otherwise nobody could reach it again.
Two things were missing from that move:

- it moved the conversation directory and nothing else. A conversation-scoped
  agent, skill, prompt, MCP or task lives under the OWNER, and so do the
  conversation's attachments, both resolved through whoever owns it *now*. The
  collaborator got a conversation with its history intact and its agent,
  skills and files gone;
- the decision to move was taken before the lock and never re-checked under
  it, so two collaborators who both resolved the departed owner moved it
  twice, the second taking it from the first.
"""

import json

import pytest

import core.paths as _paths
from core.conversation_store import ConversationStore
from core.file_store import FileStore
from core.security import Role, SecurityManager, User
import core.conversation_access as ca

OWNER = "alice"
COLLAB = "bob"
OTHER = "carol"


@pytest.fixture(autouse=True)
def users(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "SECURITY_FILE", tmp_path / "security.json")
    monkeypatch.setattr(_paths, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(_paths, "SESSIONS_FILE", tmp_path / "sessions.json")
    SecurityManager._instance = None
    sm = SecurityManager.get_instance()
    for name in (OWNER, COLLAB, OTHER):
        sm._users[name] = User(username=name, role=Role.USER)
    yield sm
    SecurityManager._instance = None


@pytest.fixture(autouse=True)
def repository(tmp_path, monkeypatch):
    """A repository root of our own: the move walks every resource type."""
    root = tmp_path / "repository"
    root.mkdir()
    monkeypatch.setattr(_paths, "REPOSITORY_DIR", root)
    return root


@pytest.fixture(autouse=True)
def files(tmp_path):
    FileStore._instance = FileStore(base_dir=str(tmp_path / "files"))
    yield FileStore._instance
    FileStore._instance = None


@pytest.fixture(autouse=True)
def reset_store():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(store_dir=str(tmp_path / "conversations"))


@pytest.fixture
def conv(store):
    cid = store.generate_id()
    store.save(cid, [{"role": "user", "content": "ship it"}], user_id=OWNER)
    return cid


def _share(store, cid, target=COLLAB):
    ca.set_collaborator(cid, target, role=ca.ROLE_WRITE,
                        status=ca.STATUS_ACCEPTED, invited_by=OWNER,
                        store=store)


def _conv_agent(repository, owner, cid, name="house-agent"):
    """A conversation-scoped agent, laid out the way repo_dir does."""
    d = repository / "agents" / "users" / owner / cid
    d.mkdir(parents=True)
    (d / f"{name}.json").write_text(json.dumps({"name": name,
                                                "prompt": "be useful"}),
                                    encoding="utf-8")
    return d


# ── what has to travel with the conversation ─────────────────────────

def test_the_conversations_own_agent_follows_it(store, conv, users,
                                                repository):
    _share(store, conv)
    _conv_agent(repository, OWNER, conv)
    users._users.pop(OWNER, None)

    access = ca.require_write(conv, COLLAB, store=store)

    assert access.owner_user_id == COLLAB
    assert (repository / "agents" / "users" / COLLAB / conv
            / "house-agent.json").is_file()
    assert not (repository / "agents" / "users" / OWNER / conv).exists()


def test_every_resource_type_follows_not_just_agents(store, conv, users,
                                                     repository):
    for rtype in ("agents", "skills", "prompts", "mcps", "tasks"):
        d = repository / rtype / "users" / OWNER / conv
        d.mkdir(parents=True)
        (d / "thing.json").write_text("{}", encoding="utf-8")
    _share(store, conv)
    users._users.pop(OWNER, None)

    ca.require_write(conv, COLLAB, store=store)

    for rtype in ("agents", "skills", "prompts", "mcps", "tasks"):
        assert (repository / rtype / "users" / COLLAB / conv
                / "thing.json").is_file(), rtype


def test_the_attachments_follow_it(store, conv, users, files):
    _share(store, conv)
    fid = files.store("contract.pdf", b"the contract", user_id=OWNER,
                      conversation_id=conv)
    users._users.pop(OWNER, None)

    ca.require_write(conv, COLLAB, store=store)

    listed = files.list_files(user_id=COLLAB, conversation_id=conv)
    assert [f["file_id"] for f in listed] == [fid]
    assert files.get_metadata(fid)["user_id"] == COLLAB
    # Still readable, and read through the new owner's own access.
    assert files.get(fid, user_id=COLLAB)[1] == b"the contract"


def test_a_resource_of_another_conversation_is_left_alone(store, conv, users,
                                                          repository):
    other = store.generate_id()
    store.save(other, [], user_id=OWNER)
    _conv_agent(repository, OWNER, other, name="not-yours")
    _share(store, conv)
    users._users.pop(OWNER, None)

    ca.require_write(conv, COLLAB, store=store)

    assert (repository / "agents" / "users" / OWNER / other
            / "not-yours.json").is_file()


# ── the move happens once ────────────────────────────────────────────

def test_two_writers_that_both_resolved_the_departed_owner_move_it_once(
        store, conv, users):
    """The race a sequential call cannot show.

    Both collaborators resolve access while the owner is still the departed
    account -- that is the window -- and only then does either take the lock.
    The second move used to succeed and take the conversation from the first,
    who carried on writing against a directory it had already left.
    """
    _share(store, conv)
    _share(store, conv, target=OTHER)
    users._users.pop(OWNER, None)

    stale_for_collab = ca.resolve_conversation_access(conv, COLLAB, store=store)
    stale_for_other = ca.resolve_conversation_access(conv, OTHER, store=store)
    assert stale_for_collab.owner_user_id == OWNER
    assert stale_for_other.owner_user_id == OWNER

    first = ca._reassign_if_owner_is_gone(conv, stale_for_collab, COLLAB,
                                          store=store)
    second = ca._reassign_if_owner_is_gone(conv, stale_for_other, OTHER,
                                           store=store)

    assert first.owner_user_id == COLLAB
    assert store.resolve_owner(conv) == COLLAB, "it was moved a second time"
    # And the loser is told the truth rather than handed a stale owner.
    assert second.owner_user_id == COLLAB
    assert second.role == ca.ROLE_WRITE
    assert second.storage_user_id == COLLAB


def test_a_refused_move_leaves_the_conversation_readable(store, conv, users):
    _share(store, conv)
    _share(store, conv, target=OTHER)
    users._users.pop(OWNER, None)

    stale = ca.resolve_conversation_access(conv, OTHER, store=store)
    ca.require_write(conv, COLLAB, store=store)          # the real move
    after = ca._reassign_if_owner_is_gone(conv, stale, OTHER, store=store)

    assert after.can_write
    assert store.load(conv, user_id=after.storage_user_id)


def test_owner_publication_waits_for_resources_and_filestore_under_concurrency(
        store, conv, repository, files, monkeypatch):
    import threading

    resource = _conv_agent(repository, OWNER, conv)
    files.store("before.txt", b"before", user_id=OWNER,
                conversation_id=conv)
    real_reassign = files.reassign_conversation_owner
    sidecars_locked = threading.Event()
    release_sidecars = threading.Event()

    def blocked_reassign(*args):
        with files._store_lock:
            sidecars_locked.set()
            assert release_sidecars.wait(timeout=10)
            return real_reassign(*args)

    monkeypatch.setattr(files, "reassign_conversation_owner", blocked_reassign)
    outcome = []
    transfer = threading.Thread(
        target=lambda: outcome.append(store.reassign_owner(conv, COLLAB,
                                                            OWNER)))
    transfer.start()
    assert sidecars_locked.wait(timeout=10)

    observed_owner = []
    reader = threading.Thread(
        target=lambda: observed_owner.append(store.resolve_owner(conv)))
    reader.start()
    late_file = []
    writer = threading.Thread(
        target=lambda: late_file.append(files.store(
            "during.txt", b"during", user_id=OWNER,
            conversation_id=conv)))
    writer.start()
    assert reader.is_alive(), "owner became observable before sidecars arrived"
    assert writer.is_alive(), "FileStore write escaped the handoff transaction"

    release_sidecars.set()
    transfer.join(timeout=10)
    reader.join(timeout=10)
    writer.join(timeout=10)

    assert outcome == [True]
    assert observed_owner == [COLLAB]
    assert not resource.exists()
    assert (repository / "agents" / "users" / COLLAB / conv
            / "house-agent.json").is_file()
    assert files.get_metadata(late_file[0])["user_id"] == COLLAB


def test_one_attachment_with_no_bytes_does_not_strand_the_others(
        store, conv, users, files):
    """A row pointing at nothing must not hold the whole transfer hostage.

    Its bytes are already gone -- under either owner. Refusing the handoff for
    it would leave every intact file of a departed owner's conversation
    unreachable, which is the failure the transfer exists to prevent.
    """
    _share(store, conv)
    ghost = files.store("gone.txt", b"gone", user_id=OWNER,
                       conversation_id=conv)
    kept = files.store("kept.txt", b"kept", user_id=OWNER,
                      conversation_id=conv)
    from pathlib import Path as _Path
    _Path(files._entries[ghost]["path"]).unlink()
    users._users.pop(OWNER, None)

    access = ca.require_write(conv, COLLAB, store=store)

    assert access.owner_user_id == COLLAB
    assert files.get_metadata(kept)["user_id"] == COLLAB
    assert files.get(kept, user_id=COLLAB)[1] == b"kept"


def test_publication_failure_rolls_back_conversation_resources_and_files(
        store, conv, repository, files, monkeypatch):
    resource = _conv_agent(repository, OWNER, conv)
    fid = files.store("contract.pdf", b"contract", user_id=OWNER,
                      conversation_id=conv)
    real_write = store._write_extras

    def fail_new_owner(cid, data, *args, **kwargs):
        if data.get("_meta_user_id") == COLLAB:
            raise OSError("metadata publication failed")
        return real_write(cid, data, *args, **kwargs)

    monkeypatch.setattr(store, "_write_extras", fail_new_owner)

    assert store.reassign_owner(conv, COLLAB, OWNER) is False
    assert store.resolve_owner(conv) == OWNER
    assert resource.is_dir()
    assert not (repository / "agents" / "users" / COLLAB / conv).exists()
    assert files.get_metadata(fid)["user_id"] == OWNER
    assert files.get(fid, user_id=OWNER)[1] == b"contract"
    assert store.load(conv, user_id=OWNER)
