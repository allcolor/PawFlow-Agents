"""Optional-dependency guard for the LiveKit realtime engine.

The LiveKit stack (docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md) is an optional
dependency group: minimal PawFlow installs must not pull livekit-agents and
its provider plugins. Anything that needs LiveKit imports calls
``require_livekit()`` first and gets one clear, actionable setup error
instead of a bare ImportError from deep inside a plugin.
"""

import importlib.util

# import name -> PyPI distribution (pyproject [realtime-livekit] group)
REQUIRED_MODULES = {
    "livekit": "livekit",
    "livekit.agents": "livekit-agents",
    "livekit.plugins.openai": "livekit-plugins-openai",
    "livekit.plugins.google": "livekit-plugins-google",
    "livekit.plugins.silero": "livekit-plugins-silero",
}

INSTALL_HINT = 'pip install "pawflow[realtime-livekit]"'


def missing_livekit_modules() -> list:
    """Return the PyPI distribution names whose module cannot be imported."""
    missing = []
    for module_name, dist_name in REQUIRED_MODULES.items():
        try:
            found = importlib.util.find_spec(module_name) is not None
        except (ImportError, ModuleNotFoundError):
            # find_spec on a submodule raises when the parent is absent
            found = False
        if not found:
            missing.append(dist_name)
    return missing


def require_livekit() -> None:
    """Raise a clear setup error when the LiveKit dependency group is absent."""
    missing = missing_livekit_modules()
    if missing:
        raise RuntimeError(
            "LiveKit realtime support requires missing packages: "
            + ", ".join(missing)
            + f". Install them with: {INSTALL_HINT}"
        )
