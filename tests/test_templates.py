"""Tests for the Flow Templates system."""

import json
import os
import shutil
import tempfile
import unittest

from gui.services.template_service import TemplateService, TEMPLATE_CATEGORIES


class TestTemplateService(unittest.TestCase):
    """Tests for TemplateService."""

    def setUp(self):
        self.svc = TemplateService()
        # Use a temporary directory for file-based templates
        self._orig_dir = self.svc.templates_dir
        self.svc.templates_dir = tempfile.mkdtemp(prefix="pyfi2_tpl_test_")

    def tearDown(self):
        shutil.rmtree(self.svc.templates_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def test_list_all_templates_count(self):
        """Should have at least 10 builtin templates."""
        templates = self.svc.list_templates()
        self.assertGreaterEqual(len(templates), 10)

    def test_list_templates_returns_dicts(self):
        """Each entry should have required summary fields."""
        templates = self.svc.list_templates()
        for t in templates:
            self.assertIn("id", t)
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertIn("category", t)
            self.assertIn("tags", t)
            self.assertIn("difficulty", t)
            self.assertIn("task_count", t)
            self.assertIn("builtin", t)

    def test_all_builtin_are_marked(self):
        """All builtin templates should have builtin=True."""
        templates = self.svc.list_templates()
        builtins = [t for t in templates if t["builtin"]]
        self.assertGreaterEqual(len(builtins), 10)

    # ------------------------------------------------------------------
    # Get by ID
    # ------------------------------------------------------------------

    def test_get_template_by_id(self):
        """Should retrieve a specific template by ID."""
        tpl = self.svc.get_template("builtin_simple_pipeline")
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl["name"], "Simple Pipeline")

    def test_get_template_not_found(self):
        """Should return None for non-existent template."""
        tpl = self.svc.get_template("nonexistent_id")
        self.assertIsNone(tpl)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def test_search_by_name(self):
        """Search should match template names."""
        results = self.svc.search_templates("Simple Pipeline")
        self.assertTrue(any(t["name"] == "Simple Pipeline" for t in results))

    def test_search_by_tag(self):
        """Search should match template tags."""
        results = self.svc.search_templates("kafka")
        self.assertTrue(len(results) > 0)
        # Kafka Stream Processor should match
        self.assertTrue(any("kafka" in t.get("name", "").lower() or
                            "kafka" in str(t.get("tags", [])).lower()
                            for t in results))

    def test_search_no_results(self):
        """Search for nonsense should return empty."""
        results = self.svc.search_templates("xyznonexistent999")
        self.assertEqual(len(results), 0)

    # ------------------------------------------------------------------
    # Filter by category
    # ------------------------------------------------------------------

    def test_filter_by_category_etl(self):
        """Should return only ETL templates."""
        results = self.svc.get_templates_by_category("ETL")
        self.assertTrue(len(results) > 0)
        for t in results:
            self.assertEqual(t["category"], "ETL")

    def test_filter_by_category_integration(self):
        """Should return only Integration templates."""
        results = self.svc.get_templates_by_category("Integration")
        self.assertTrue(len(results) > 0)
        for t in results:
            self.assertEqual(t["category"], "Integration")

    # ------------------------------------------------------------------
    # Save custom template
    # ------------------------------------------------------------------

    def test_save_and_retrieve_custom_template(self):
        """Save a custom template and retrieve it."""
        flow = {
            "tasks": {
                "log_1": {"type": "log", "parameters": {"message": "hello"}},
            },
            "relations": [],
            "entries": ["log_1"],
            "exits": ["log_1"],
        }
        filepath = self.svc.save_as_template(
            flow, "My Custom", "A test template",
            category="Custom", tags=["test", "custom"],
        )
        self.assertTrue(os.path.exists(filepath))

        # Should appear in listing
        templates = self.svc.list_templates()
        custom = [t for t in templates if t["name"] == "My Custom"]
        self.assertEqual(len(custom), 1)
        self.assertFalse(custom[0]["builtin"])
        self.assertEqual(custom[0]["category"], "Custom")

    # ------------------------------------------------------------------
    # Import / Export roundtrip
    # ------------------------------------------------------------------

    def test_export_template(self):
        """Export should return valid JSON string."""
        json_str = self.svc.export_template("builtin_simple_pipeline")
        data = json.loads(json_str)
        self.assertEqual(data["name"], "Simple Pipeline")
        self.assertIn("tasks", data)

    def test_import_export_roundtrip(self):
        """Import an exported template and verify it matches."""
        json_str = self.svc.export_template("builtin_etl_database")
        original = json.loads(json_str)

        # Import it
        imported_id = self.svc.import_template_from_json(json_str)

        # Retrieve the imported template
        imported = self.svc.get_template(imported_id)
        self.assertIsNotNone(imported)
        self.assertEqual(imported["name"], original["name"])
        self.assertEqual(len(imported["tasks"]), len(original["tasks"]))

    # ------------------------------------------------------------------
    # Template structure validation
    # ------------------------------------------------------------------

    def test_all_templates_have_valid_structure(self):
        """Every builtin template should have tasks, relations, entries, exits."""
        for tpl in self.svc.get_builtin_templates():
            with self.subTest(template=tpl["name"]):
                self.assertIn("tasks", tpl)
                self.assertIsInstance(tpl["tasks"], dict)
                self.assertTrue(len(tpl["tasks"]) > 0, f"{tpl['name']} has no tasks")

                self.assertIn("relations", tpl)
                self.assertIsInstance(tpl["relations"], list)

                self.assertIn("entries", tpl)
                self.assertIsInstance(tpl["entries"], list)
                self.assertTrue(len(tpl["entries"]) > 0, f"{tpl['name']} has no entries")

                self.assertIn("exits", tpl)
                self.assertIsInstance(tpl["exits"], list)
                self.assertTrue(len(tpl["exits"]) > 0, f"{tpl['name']} has no exits")

                # Each relation should reference existing tasks
                task_ids = set(tpl["tasks"].keys())
                for rel in tpl["relations"]:
                    self.assertIn(rel["from"], task_ids,
                                  f"{tpl['name']}: relation from '{rel['from']}' not in tasks")
                    self.assertIn(rel["to"], task_ids,
                                  f"{tpl['name']}: relation to '{rel['to']}' not in tasks")

                # Each task should have a type
                for tid, tconf in tpl["tasks"].items():
                    self.assertIn("type", tconf, f"{tpl['name']}: task '{tid}' has no type")


if __name__ == "__main__":
    unittest.main()
