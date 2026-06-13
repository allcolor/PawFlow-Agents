"""Phase 2 tests: conversation field-level cipher + SegmentedJsonl codec seam.

Proves (encryption-at-rest RFC phase 2, decision #3 'encrypt content, metadata
clear'):
  * content fields encrypted, metadata fields left clear (readable w/o the key)
  * roundtrip incl. dict/list `arguments` (type preserved)
  * idempotent encode, passthrough decode (mixed/partial logs)
  * field-bound AAD (a content blob can't be reused as arguments)
  * SegmentedJsonl read/write through the codec, on-disk bytes are ciphertext
  * msg_id-keyed ops (truncate/patch/delete) work on an ENCRYPTED log without
    the codec -- i.e. metadata stays clear on disk
  * patch merges plaintext fields and re-encrypts them
  * encrypt_log / decrypt_log migration primitives (resumable / reversible)
"""

import json
import os

import pytest

from core.conversation_cipher import (
    CONTENT_FIELDS,
    RowCodec,
    decrypt_log,
    encrypt_log,
    is_encrypted_value,
)
from core.secrets import SecretDecryptError
from core.segmented_jsonl import SegmentedJsonl

DEK = bytes(range(32))
DEK2 = bytes(range(31, -1, -1))


def _row(**kw):
    base = {"msg_id": "abc123", "ts": 111.0, "seq": 4, "role": "user",
            "conversation_id": "c1", "user_id": "u1"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Row codec: content vs metadata
# ---------------------------------------------------------------------------

def test_content_encrypted_metadata_clear():
    codec = RowCodec(DEK)
    enc = codec.encode(_row(content="secret text"))
    # content hidden
    assert is_encrypted_value(enc["content"])
    assert "secret text" not in enc["content"]
    # every metadata field untouched and readable without the key
    for f in ("msg_id", "ts", "seq", "role", "conversation_id", "user_id"):
        assert enc[f] == _row()[f]


def test_roundtrip_string_content():
    codec = RowCodec(DEK)
    row = _row(content="héllo 🐱 中文")
    assert codec.decode(codec.encode(row)) == row


def test_roundtrip_dict_arguments_preserves_type():
    codec = RowCodec(DEK)
    row = _row(role="tool_call", tool_name="edit",
               arguments={"path": "a.py", "n": 3, "flag": True})
    enc = codec.encode(row)
    assert is_encrypted_value(enc["arguments"])
    assert enc["tool_name"] == "edit"  # tool name is metadata, clear
    out = codec.decode(enc)
    assert out["arguments"] == {"path": "a.py", "n": 3, "flag": True}
    assert isinstance(out["arguments"], dict)


def test_roundtrip_list_arguments():
    codec = RowCodec(DEK)
    row = _row(role="tool_call", arguments=[1, "two", {"k": "v"}])
    assert codec.decode(codec.encode(row))["arguments"] == [1, "two", {"k": "v"}]


def test_empty_and_missing_content_untouched():
    codec = RowCodec(DEK)
    assert codec.encode(_row(content=""))["content"] == ""
    assert "content" not in codec.encode(_row())  # missing stays missing


def test_encode_is_idempotent():
    codec = RowCodec(DEK)
    once = codec.encode(_row(content="x"))
    twice = codec.encode(once)
    assert twice["content"] == once["content"]  # not double-wrapped
    assert codec.decode(twice)["content"] == "x"


def test_decode_passthrough_on_plaintext():
    # a non-encrypted (plaintext) row decodes to itself -> mixed logs are safe
    codec = RowCodec(DEK)
    plain = _row(content="clear", arguments={"a": 1})
    assert codec.decode(plain) == plain


def test_wrong_dek_fails_loud():
    enc = RowCodec(DEK).encode(_row(content="secret"))
    with pytest.raises(SecretDecryptError):
        RowCodec(DEK2).decode(enc)


def test_field_aad_prevents_cross_field_reuse():
    codec = RowCodec(DEK)
    enc = codec.encode(_row(content="secret", arguments="args"))
    # move the content blob into the arguments slot -> AAD mismatch on decrypt
    swapped = dict(enc)
    swapped["arguments"] = enc["content"]
    with pytest.raises(SecretDecryptError):
        codec.decode(swapped)


def test_codec_rejects_bad_dek_length():
    with pytest.raises(ValueError):
        RowCodec(b"short")


def test_content_fields_set():
    assert CONTENT_FIELDS == ("content", "arguments")


# ---------------------------------------------------------------------------
# SegmentedJsonl codec seam
# ---------------------------------------------------------------------------

def _seg(tmp_path, codec=None):
    return SegmentedJsonl(tmp_path / "log.jsonl", codec=codec)


def test_segmented_write_then_read_through_codec(tmp_path):
    codec = RowCodec(DEK)
    log = _seg(tmp_path, codec)
    log.append_dicts([_row(msg_id="m1", content="alpha"),
                      _row(msg_id="m2", content="beta")])
    out = list(log.iter_rows())
    assert [r["content"] for r in out] == ["alpha", "beta"]
    assert [r["msg_id"] for r in out] == ["m1", "m2"]


def test_segmented_on_disk_bytes_are_ciphertext(tmp_path):
    log = _seg(tmp_path, RowCodec(DEK))
    log.append_dicts([_row(msg_id="m1", content="TOPSECRET")])
    SegmentedJsonl.flush_all_append_handles()
    blob = "".join(p.read_text() for p in log.iter_paths())
    assert "TOPSECRET" not in blob
    assert "m1" in blob  # msg_id stays clear on disk


def test_segmented_no_codec_is_plaintext_passthrough(tmp_path):
    log = _seg(tmp_path, None)
    log.append_dicts([_row(content="clear")])
    SegmentedJsonl.flush_all_append_handles()
    blob = "".join(p.read_text() for p in log.iter_paths())
    assert "clear" in blob


def test_metadata_ops_work_on_encrypted_log_without_codec(tmp_path):
    # Write encrypted, then operate with a codec-less handle: msg_id-keyed
    # ops must still work because metadata is clear on disk.
    path = tmp_path / "log.jsonl"
    SegmentedJsonl(path, codec=RowCodec(DEK)).append_dicts([
        _row(msg_id="m1", content="one"),
        _row(msg_id="m2", content="two"),
        _row(msg_id="m3", content="three"),
    ])
    plain = SegmentedJsonl(path)  # no codec -- the storage/maintenance view
    res = plain.truncate_after_msg_id("m2")
    assert res["found"] and res["kept_rows"] == 2
    # and the surviving content is still decryptable -> truncate kept ciphertext
    after = list(SegmentedJsonl(path, codec=RowCodec(DEK)).iter_rows())
    assert [r["content"] for r in after] == ["one", "two"]


def test_delete_by_msg_id_on_encrypted_log(tmp_path):
    path = tmp_path / "log.jsonl"
    SegmentedJsonl(path, codec=RowCodec(DEK)).append_dicts([
        _row(msg_id="m1", content="one"), _row(msg_id="m2", content="two")])
    assert SegmentedJsonl(path).delete_by_msg_ids({"m1"}) == 1
    after = list(SegmentedJsonl(path, codec=RowCodec(DEK)).iter_rows())
    assert [r["content"] for r in after] == ["two"]


def test_patch_merges_plaintext_and_reencrypts(tmp_path):
    path = tmp_path / "log.jsonl"
    log = SegmentedJsonl(path, codec=RowCodec(DEK))
    log.append_dicts([_row(msg_id="m1", content="orig")])
    patched = log.patch_first_by_msg_id("m1", {"content": "updated"})
    assert patched["content"] == "updated"  # returned in plaintext
    # on disk it is ciphertext
    SegmentedJsonl.flush_all_append_handles()
    blob = "".join(p.read_text() for p in log.iter_paths())
    assert "updated" not in blob and "orig" not in blob
    # reads back decrypted
    assert list(log.iter_rows())[0]["content"] == "updated"


# ---------------------------------------------------------------------------
# Migration primitives
# ---------------------------------------------------------------------------

def test_encrypt_log_migrates_plaintext_in_place(tmp_path):
    path = tmp_path / "log.jsonl"
    SegmentedJsonl(path).append_dicts([_row(msg_id="m1", content="a"),
                                       _row(msg_id="m2", content="b")])
    n = encrypt_log(path, DEK)
    assert n == 2
    SegmentedJsonl.flush_all_append_handles()
    blob = "".join(p.read_text() for p in SegmentedJsonl(path).iter_paths())
    assert "\"content\":\"a\"" not in blob.replace(" ", "")
    assert list(SegmentedJsonl(path, codec=RowCodec(DEK)).iter_rows())[0]["content"] == "a"


def test_encrypt_log_is_resumable(tmp_path):
    # running twice must not double-encrypt
    path = tmp_path / "log.jsonl"
    SegmentedJsonl(path).append_dicts([_row(msg_id="m1", content="a")])
    encrypt_log(path, DEK)
    encrypt_log(path, DEK)  # second pass: idempotent
    assert list(SegmentedJsonl(path, codec=RowCodec(DEK)).iter_rows())[0]["content"] == "a"


def test_decrypt_log_reverses_migration(tmp_path):
    path = tmp_path / "log.jsonl"
    SegmentedJsonl(path).append_dicts([_row(msg_id="m1", content="a"),
                                       _row(msg_id="m2", content="b")])
    encrypt_log(path, DEK)
    assert decrypt_log(path, DEK) == 2
    SegmentedJsonl.flush_all_append_handles()
    blob = "".join(p.read_text() for p in SegmentedJsonl(path).iter_paths())
    assert "a" in blob and "b" in blob  # back to clear
    rows = list(SegmentedJsonl(path).iter_rows())
    assert [r["content"] for r in rows] == ["a", "b"]


def test_migration_primitives_noop_on_missing_log(tmp_path):
    assert encrypt_log(tmp_path / "nope.jsonl", DEK) == 0
    assert decrypt_log(tmp_path / "nope.jsonl", DEK) == 0
