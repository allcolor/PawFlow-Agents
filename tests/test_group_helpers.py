"""Tests for gui/components/group_helpers.py — group bounds, position conversion, collapse logic."""

import pytest
from gui.components.group_helpers import (
    compute_group_bounds, absolute_to_relative, relative_to_absolute,
    remap_edges_for_collapse, GROUP_HEADER_HEIGHT, GROUP_PADDING,
)


class TestComputeGroupBounds:
    def test_empty_positions(self):
        bounds = compute_group_bounds({})
        assert bounds["width"] == 250
        assert bounds["height"] == 160

    def test_single_node(self):
        positions = {"task1": (100, 200)}
        bounds = compute_group_bounds(positions, node_width=150, node_height=60)
        assert bounds["x"] == 100 - GROUP_PADDING
        assert bounds["y"] == 200 - GROUP_PADDING - GROUP_HEADER_HEIGHT
        assert bounds["width"] == 150 + 2 * GROUP_PADDING
        assert bounds["height"] == 60 + 2 * GROUP_PADDING + GROUP_HEADER_HEIGHT

    def test_multiple_nodes(self):
        positions = {
            "task1": (100, 100),
            "task2": (300, 200),
        }
        bounds = compute_group_bounds(positions, node_width=150, node_height=60)
        assert bounds["x"] == 100 - GROUP_PADDING
        assert bounds["y"] == 100 - GROUP_PADDING - GROUP_HEADER_HEIGHT
        # width: 300 + 150 + padding - (100 - padding) = 300+150+2*padding
        expected_width = (300 + 150 + GROUP_PADDING) - (100 - GROUP_PADDING)
        assert bounds["width"] == expected_width


class TestPositionConversion:
    def test_absolute_to_relative(self):
        rel = absolute_to_relative((120, 200), (100, 100))
        assert rel == (20, 200 - 100 - GROUP_HEADER_HEIGHT)

    def test_relative_to_absolute(self):
        abs_pos = relative_to_absolute((20, 60), (100, 100))
        assert abs_pos == (120, 60 + 100 + GROUP_HEADER_HEIGHT)

    def test_roundtrip(self):
        group_pos = (50, 50)
        original = (200, 300)
        rel = absolute_to_relative(original, group_pos)
        back = relative_to_absolute(rel, group_pos)
        assert back == original


class TestRemapEdgesForCollapse:
    def test_internal_edges_removed(self):
        edges = [
            {"id": "e1", "source": "a", "target": "b"},
            {"id": "e2", "source": "b", "target": "c"},
        ]
        result = remap_edges_for_collapse(
            edges, "g1", {"a", "b", "c"}, "group_g1_summary"
        )
        # All edges are internal → all removed
        assert len(result) == 0

    def test_external_incoming_remapped(self):
        edges = [
            {"id": "e1", "source": "external", "target": "member1"},
        ]
        result = remap_edges_for_collapse(
            edges, "g1", {"member1", "member2"}, "summary"
        )
        assert len(result) == 1
        assert result[0]["source"] == "external"
        assert result[0]["target"] == "summary"

    def test_external_outgoing_remapped(self):
        edges = [
            {"id": "e1", "source": "member1", "target": "external"},
        ]
        result = remap_edges_for_collapse(
            edges, "g1", {"member1", "member2"}, "summary"
        )
        assert len(result) == 1
        assert result[0]["source"] == "summary"
        assert result[0]["target"] == "external"

    def test_deduplication(self):
        edges = [
            {"id": "e1", "source": "ext", "target": "m1"},
            {"id": "e2", "source": "ext", "target": "m2"},
        ]
        result = remap_edges_for_collapse(
            edges, "g1", {"m1", "m2"}, "summary"
        )
        assert len(result) == 1
        assert result[0]["label"] == "2 connections"

    def test_mixed_edges(self):
        edges = [
            {"id": "e1", "source": "m1", "target": "m2"},  # internal
            {"id": "e2", "source": "ext1", "target": "m1"},  # incoming
            {"id": "e3", "source": "m2", "target": "ext2"},  # outgoing
            {"id": "e4", "source": "ext3", "target": "ext4"},  # unrelated
        ]
        result = remap_edges_for_collapse(
            edges, "g1", {"m1", "m2"}, "summary"
        )
        assert len(result) == 3  # incoming + outgoing + unrelated
        sources = {e["source"] for e in result}
        targets = {e["target"] for e in result}
        assert "summary" in sources
        assert "summary" in targets
        assert "ext3" in sources
        assert "ext4" in targets


class TestProcessGroup:
    """Test the enhanced ProcessGroup model."""

    def test_new_fields(self):
        from core.process_group import ProcessGroup
        pg = ProcessGroup(
            group_id="test", name="Test Group",
            color="#ff0000", collapsed=True,
            flow_ref={"path": "flows/test.json", "version": "1.0.0"},
        )
        assert pg.color == "#ff0000"
        assert pg.collapsed is True
        assert pg.is_subflow is True
        assert pg.flow_ref["version"] == "1.0.0"

    def test_default_values(self):
        from core.process_group import ProcessGroup
        pg = ProcessGroup()
        assert pg.color == "#4285f4"
        assert pg.collapsed is False
        assert pg.is_subflow is False
        assert pg.flow_ref is None

    def test_to_dict_includes_new_fields(self):
        from core.process_group import ProcessGroup
        pg = ProcessGroup(color="#abcdef", collapsed=True)
        d = pg.to_dict()
        assert d["color"] == "#abcdef"
        assert d["collapsed"] is True
        assert d["flow_ref"] is None

    def test_from_dict_new_format(self):
        from core.process_group import ProcessGroup
        data = {
            "id": "g1",
            "name": "API Layer",
            "color": "#0d6efd",
            "collapsed": False,
            "flow_ref": None,
            "tasks": {"t1": {"type": "fetchHTTP", "parameters": {}}},
            "relations": [{"from": "t1", "to": "t2", "type": "success"}],
            "input_ports": ["in1"],
            "output_ports": ["out1"],
        }
        pg = ProcessGroup.from_dict(data)
        assert pg.name == "API Layer"
        assert pg.color == "#0d6efd"
        assert len(pg.tasks) == 1
        assert pg.input_ports == ["in1"]
        assert not pg.is_subflow

    def test_from_dict_legacy_format(self):
        from core.process_group import ProcessGroup
        data = {
            "color": "#ff0000",
            "tasks": ["task1", "task2"],
        }
        pg = ProcessGroup.from_dict(data)
        assert pg.color == "#ff0000"
        assert hasattr(pg, '_legacy_task_ids')
        assert pg.get_member_task_ids() == ["task1", "task2"]

    def test_get_member_task_ids_new_format(self):
        from core.process_group import ProcessGroup
        pg = ProcessGroup()
        pg.add_task("t1", "log")
        pg.add_task("t2", "wait")
        ids = pg.get_member_task_ids()
        assert set(ids) == {"t1", "t2"}

    def test_roundtrip_serialization(self):
        from core.process_group import ProcessGroup
        pg = ProcessGroup(
            group_id="rnd", name="Round Trip",
            color="#123456", collapsed=True,
            flow_ref={"path": "test.json", "version": "2.0"},
        )
        pg.add_task("t1", "log")
        pg.add_input_port("in1")
        pg.add_output_port("out1")

        d = pg.to_dict()
        pg2 = ProcessGroup.from_dict(d)
        assert pg2.id == "rnd"
        assert pg2.color == "#123456"
        assert pg2.collapsed is True
        assert pg2.is_subflow is True
        assert "t1" in pg2.tasks
        assert "in1" in pg2.input_ports

    def test_load_from_ref_nonexistent(self):
        from core.process_group import ProcessGroup
        pg = ProcessGroup(flow_ref={"path": "nonexistent.json"})
        assert pg.load_from_ref() is False

    def test_load_from_ref_success(self, tmp_path):
        import json
        from core.process_group import ProcessGroup

        flow_data = {
            "tasks": {"t1": {"type": "log", "parameters": {}}},
            "relations": [{"from": "t1", "to": "t2", "type": "success"}],
            "entries": ["t1"],
            "exits": ["t2"],
        }
        flow_file = tmp_path / "test_flow.json"
        flow_file.write_text(json.dumps(flow_data))

        pg = ProcessGroup(flow_ref={"path": str(flow_file)})
        result = pg.load_from_ref()
        assert result is True
        assert "t1" in pg.tasks
        assert pg.tasks["t1"]["group_id"] == pg.id
        assert pg.input_ports == ["t1"]
        assert pg.output_ports == ["t2"]
