"""Resource/agent tool handlers — facade re-exporting the split handler set.

ManageResourceHandler, SpawnAgentsHandler (+ its _SpawnDeliveryMixin),
FlashAgentHandler and ShowFileHandler were split into one module each to keep
files <=800 lines. The core.handlers.resource_agent import path is unchanged.
"""

from core.handlers.manage_resource import ManageResourceHandler  # noqa: F401
from core.handlers.spawn_agents import SpawnAgentsHandler  # noqa: F401
from core.handlers.flash_agent import FlashAgentHandler  # noqa: F401
from core.handlers.show_file import ShowFileHandler  # noqa: F401
