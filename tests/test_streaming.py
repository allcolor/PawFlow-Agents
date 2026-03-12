"""Tests for FlowFile streaming / disk-spill support."""

import io
import os
import pytest

from core.stream import ContentReference, set_spill_threshold, SPILL_THRESHOLD, _get_spill_dir
from core import FlowFile


class TestContentReference:

    def test_small_content_in_memory(self):
        ref = ContentReference(data=b"hello")
        assert ref.size == 5
        assert not ref.is_on_disk
        assert ref.get_bytes() == b"hello"

    def test_empty_content(self):
        ref = ContentReference(data=b"")
        assert ref.size == 0
        assert ref.get_bytes() == b""

    def test_stream_from_memory(self):
        ref = ContentReference(data=b"stream me")
        stream = ref.get_stream()
        assert stream.read() == b"stream me"
        stream.close()

    def test_spill_to_disk(self):
        original_threshold = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)  # 10 bytes threshold
            big_data = b"x" * 50
            ref = ContentReference(data=big_data)
            assert ref.is_on_disk
            assert ref.size == 50
            assert ref.get_bytes() == big_data
        finally:
            set_spill_threshold(original_threshold)

    def test_stream_from_disk(self):
        original_threshold = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)
            big_data = b"abcdefghijklmnop"
            ref = ContentReference(data=big_data)
            assert ref.is_on_disk
            stream = ref.get_stream()
            assert stream.read() == big_data
            stream.close()
        finally:
            set_spill_threshold(original_threshold)

    def test_ref_counting(self):
        ref = ContentReference(data=b"shared")
        assert ref.ref_count == 1
        ref.increment_ref()
        assert ref.ref_count == 2
        ref.release()
        assert ref.ref_count == 1
        # Data still accessible
        assert ref.get_bytes() == b"shared"
        ref.release()
        assert ref.ref_count == 0

    def test_disk_cleanup_on_release(self):
        original_threshold = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)
            ref = ContentReference(data=b"x" * 20)
            assert ref.is_on_disk
            file_path = ref._file_path
            assert file_path.exists()
            ref.release()
            assert not file_path.exists()
        finally:
            set_spill_threshold(original_threshold)

    def test_clone_data_creates_independent_copy(self):
        ref1 = ContentReference(data=b"original")
        ref2 = ref1.clone_data()
        assert ref2.get_bytes() == b"original"
        assert ref1.ref_count == 1
        assert ref2.ref_count == 1
        ref1.release()
        # ref2 still works
        assert ref2.get_bytes() == b"original"

    def test_from_stream_small(self):
        stream = io.BytesIO(b"from stream")
        ref = ContentReference.from_stream(stream, size_hint=11)
        assert ref.get_bytes() == b"from stream"
        assert not ref.is_on_disk

    def test_from_stream_large(self):
        original_threshold = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)
            data = b"large stream content here"
            stream = io.BytesIO(data)
            ref = ContentReference.from_stream(stream, size_hint=100)
            assert ref.is_on_disk
            assert ref.get_bytes() == data
        finally:
            set_spill_threshold(original_threshold)


class TestFlowFileStreaming:

    def test_basic_creation_unchanged(self):
        ff = FlowFile(content=b"hello", attributes={"key": "val"})
        assert ff.get_content() == b"hello"
        assert ff.size() == 5
        assert ff.get_attribute("key") == "val"

    def test_content_property_backward_compat(self):
        ff = FlowFile(content=b"data")
        assert ff.content == b"data"

    def test_set_content_backward_compat(self):
        ff = FlowFile(content=b"old")
        ff.set_content(b"new")
        assert ff.get_content() == b"new"
        assert ff.size() == 3

    def test_content_setter_property(self):
        ff = FlowFile(content=b"old")
        ff.content = b"new via setter"
        assert ff.content == b"new via setter"

    def test_is_empty(self):
        ff = FlowFile()
        assert ff.is_empty()
        ff.set_content(b"x")
        assert not ff.is_empty()

    def test_clone_deep(self):
        ff = FlowFile(content=b"original", attributes={"a": "1"})
        clone = ff.clone(deep=True)
        assert clone.get_content() == b"original"
        assert clone.get_attribute("a") == "1"
        assert clone.process_id != ff.process_id
        # Independent content
        clone.set_content(b"modified")
        assert ff.get_content() == b"original"

    def test_clone_shallow_shares_content(self):
        ff = FlowFile(content=b"shared")
        clone = ff.clone(deep=False)
        assert clone.get_content() == b"shared"
        assert clone.process_id != ff.process_id
        # Both ref the same content
        assert ff._content_ref is clone._content_ref
        assert ff._content_ref.ref_count == 2

    def test_clone_default_is_deep(self):
        ff = FlowFile(content=b"data")
        clone = ff.clone()
        assert clone.get_content() == b"data"
        # Independent refs
        assert ff._content_ref is not clone._content_ref

    def test_streaming_api(self):
        ff = FlowFile(content=b"stream test")
        stream = ff.get_content_stream()
        assert stream.read() == b"stream test"
        stream.close()

    def test_set_from_stream(self):
        ff = FlowFile()
        ff.set_content_from_stream(io.BytesIO(b"from stream"))
        assert ff.get_content() == b"from stream"

    def test_large_content_spills_to_disk(self):
        original_threshold = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)
            big_data = b"x" * 100
            ff = FlowFile(content=big_data)
            assert ff.is_content_on_disk
            assert ff.size() == 100
            assert ff.get_content() == big_data
            # Stream also works
            stream = ff.get_content_stream()
            assert stream.read() == big_data
            stream.close()
        finally:
            set_spill_threshold(original_threshold)

    def test_set_from_stream_large(self):
        original_threshold = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)
            data = b"y" * 200
            ff = FlowFile()
            ff.set_content_from_stream(io.BytesIO(data), size_hint=200)
            assert ff.is_content_on_disk
            assert ff.get_content() == data
        finally:
            set_spill_threshold(original_threshold)

    def test_to_dict_includes_on_disk(self):
        ff = FlowFile(content=b"data")
        d = ff.to_dict()
        assert "on_disk" in d
        assert d["size"] == 4
        assert not d["on_disk"]

    def test_repr(self):
        ff = FlowFile(content=b"test")
        r = repr(ff)
        assert "mem" in r
        assert "size=4" in r

    def test_size_without_loading(self):
        """size() should return correct value without loading content."""
        original_threshold = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)
            ff = FlowFile(content=b"z" * 50)
            assert ff.is_content_on_disk
            # size() should work without get_content()
            assert ff.size() == 50
        finally:
            set_spill_threshold(original_threshold)

    def test_get_attributes_set_attributes(self):
        ff = FlowFile(attributes={"a": "1", "b": "2"})
        attrs = ff.get_attributes()
        assert attrs == {"a": "1", "b": "2"}
        ff.set_attributes({"c": "3"})
        assert ff.get_attributes() == {"c": "3"}

    def test_delete_attribute(self):
        ff = FlowFile(attributes={"x": "1"})
        ff.delete_attribute("x")
        assert ff.get_attribute("x") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
