"""Tests for gui/components/color_scheme.py — category-based task coloring."""

import pytest
from gui.components.color_scheme import (
    get_task_color, get_task_category, get_category_base_color,
    get_legend_data, CATEGORY_PALETTES, TASK_CATEGORIES, CATEGORY_ICONS,
)


class TestGetTaskCategory:
    def test_known_task(self):
        assert get_task_category("fetchHTTP") == "IO"
        assert get_task_category("transformJSON") == "Data"
        assert get_task_category("routeOnAttribute") == "Control"
        assert get_task_category("inferLLM") == "AI"

    def test_unknown_task(self):
        assert get_task_category("unknownTask123") == "Plugins"

    def test_system_tasks(self):
        for t in ["log", "updateAttribute", "wait", "fail", "executeScript"]:
            assert get_task_category(t) == "System"


class TestGetTaskColor:
    def test_returns_hex_color(self):
        color = get_task_color("fetchHTTP")
        assert color.startswith("#")
        assert len(color) == 7

    def test_deterministic(self):
        c1 = get_task_color("fetchHTTP")
        c2 = get_task_color("fetchHTTP")
        assert c1 == c2

    def test_same_category_different_shades(self):
        """Tasks in the same category should get shades from the same palette."""
        c1 = get_task_color("getFile")
        c2 = get_task_color("putFile")
        # Both should be from the IO palette
        io_shades = CATEGORY_PALETTES["IO"]["shades"]
        assert c1 in io_shades
        assert c2 in io_shades

    def test_explicit_category(self):
        color = get_task_color("myCustomTask", "Data")
        data_shades = CATEGORY_PALETTES["Data"]["shades"]
        assert color in data_shades

    def test_unknown_category_returns_grey(self):
        color = get_task_color("unknownPlugin")
        assert color.startswith("#")

    def test_all_known_tasks_have_colors(self):
        for task_type in TASK_CATEGORIES:
            color = get_task_color(task_type)
            assert color.startswith("#"), f"No color for {task_type}"


class TestGetCategoryBaseColor:
    def test_known_category(self):
        assert get_category_base_color("IO") == "#0d6efd"
        assert get_category_base_color("System") == "#6c757d"

    def test_unknown_category(self):
        assert get_category_base_color("UnknownCat") == "#adb5bd"


class TestGetLegendData:
    def test_returns_list(self):
        legend = get_legend_data()
        assert isinstance(legend, list)
        assert len(legend) > 0

    def test_includes_all_categories(self):
        legend = get_legend_data()
        categories = {item["category"] for item in legend}
        for cat in CATEGORY_PALETTES:
            assert cat in categories
        assert "Plugins" in categories

    def test_legend_item_structure(self):
        legend = get_legend_data()
        for item in legend:
            assert "category" in item
            assert "color" in item
            assert "icon" in item
            assert item["color"].startswith("#")


class TestPaletteConsistency:
    def test_all_palettes_have_shades(self):
        for cat, palette in CATEGORY_PALETTES.items():
            assert "base" in palette, f"Missing base in {cat}"
            assert "shades" in palette, f"Missing shades in {cat}"
            assert len(palette["shades"]) >= 3, f"Too few shades in {cat}"

    def test_all_categories_have_icons(self):
        for cat in CATEGORY_PALETTES:
            assert cat in CATEGORY_ICONS, f"Missing icon for {cat}"
