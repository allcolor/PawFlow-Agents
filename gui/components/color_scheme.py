"""Color scheme for flow canvas — category-based task coloring.

Each task category has a base color and 4-8 shades. Individual tasks
get a deterministic shade based on hash(task_type) within their category palette.
Supports dark and light themes.
"""

from typing import Dict, List, Optional, Tuple


# Category palettes: base color + shades (light→dark progression)
CATEGORY_PALETTES = {
    "System": {
        "base": "#6c757d",
        "shades": ["#5a6268", "#6c757d", "#868e96", "#adb5bd"],
    },
    "IO": {
        "base": "#0d6efd",
        "shades": ["#0a58ca", "#0d6efd", "#3d8bfd", "#6ea8fe"],
    },
    "Cloud": {
        "base": "#0dcaf0",
        "shades": ["#0aa2c0", "#0dcaf0", "#3dd5f3", "#6edff6"],
    },
    "Data": {
        "base": "#198754",
        "shades": ["#146c43", "#198754", "#479f76", "#75b798"],
    },
    "Control": {
        "base": "#fd7e14",
        "shades": ["#ca6510", "#fd7e14", "#fd9843", "#feb272"],
    },
    "Messaging": {
        "base": "#20c997",
        "shades": ["#1aa179", "#20c997", "#4dd4ac", "#79dfc1"],
    },
    "Sync": {
        "base": "#6f42c1",
        "shades": ["#59359a", "#6f42c1", "#8c68cd", "#a98eda"],
    },
    "Monitoring": {
        "base": "#6610f2",
        "shades": ["#520dc2", "#6610f2", "#8540f5", "#a370f7"],
    },
    "AI": {
        "base": "#d63384",
        "shades": ["#ab296a", "#d63384", "#de5c9d", "#e685b5"],
    },
}

# Task → Category mapping (canonical source: core.task_categories)
from core.task_categories import TASK_CATEGORIES

# Icons per category
CATEGORY_ICONS = {
    "System": "⚙️",
    "IO": "📁",
    "Cloud": "☁️",
    "Data": "🔄",
    "Control": "🔀",
    "Messaging": "✉️",
    "Sync": "🔗",
    "Monitoring": "📊",
    "AI": "🤖",
    "Plugins": "🧩",
}


def get_task_category(task_type: str) -> str:
    """Get the category for a task type."""
    return TASK_CATEGORIES.get(task_type, "Plugins")


def get_task_color(task_type: str, category: Optional[str] = None) -> str:
    """Get a deterministic color for a task type based on its category.

    Uses hash of task_type to pick a shade within the category palette.
    Unknown categories get a neutral grey shade.
    """
    if category is None:
        category = get_task_category(task_type)

    palette = CATEGORY_PALETTES.get(category)
    if not palette:
        # Plugins / unknown → neutral grey shades
        fallback_shades = ["#6c757d", "#868e96", "#adb5bd", "#ced4da"]
        idx = hash(task_type) % len(fallback_shades)
        return fallback_shades[idx]

    shades = palette["shades"]
    idx = hash(task_type) % len(shades)
    return shades[idx]


def get_category_base_color(category: str) -> str:
    """Get the base color for a category."""
    palette = CATEGORY_PALETTES.get(category)
    return palette["base"] if palette else "#adb5bd"


def get_legend_data() -> List[Dict[str, str]]:
    """Get legend data for display: list of {category, color, icon}."""
    legend = []
    for cat_name, palette in CATEGORY_PALETTES.items():
        legend.append({
            "category": cat_name,
            "color": palette["base"],
            "icon": CATEGORY_ICONS.get(cat_name, ""),
        })
    # Add Plugins entry
    legend.append({
        "category": "Plugins",
        "color": "#adb5bd",
        "icon": CATEGORY_ICONS.get("Plugins", "🧩"),
    })
    return legend
