"""Handler modules — re-exported for backward compatibility.

All handlers can be imported from core.tool_registry or core.handlers."""

from core.handlers.agent_tools import (  # noqa: F401
    BrowserActionHandler,
    LinkIdentityHandler,
    ConfigurableToolHandler,
    HTTPToolHandler,
    TaskToolHandler,
    MCPToolHandler,
)
from core.handlers.devops import (  # noqa: F401
    RunTestsHandler,
    ReadParentContextHandler,
    GitHubHandler,
    SecurityScanHandler,
)
from core.handlers.file_ops import (  # noqa: F401
    CreateFileHandler,
    ScheduleContinuationHandler,
    ScheduleRecheckHandler,
    LocalFilesHandler,
)
from core.handlers.filesystem import (  # noqa: F401
    FilesystemToolHandler,
)
from core.handlers.flow_management import (  # noqa: F401
    CreateToolHandler,
    FlowManagerHandler,
    AskAgentHandler,
    CreatePlanHandler,
    UpdatePlanHandler,
)
from core.handlers.help_secrets import (  # noqa: F401
    PawFlowHelpHandler,
    StoreSecretHandler,
    ListSecretsHandler,
)
from core.handlers.media import (  # noqa: F401
    ImageGenerationHandler,
    VideoGenerationHandler,
)
from core.handlers.memory import (  # noqa: F401
    RememberHandler,
    SemanticRecallHandler,
    RecallHandler,
    ForgetHandler,
)
from core.handlers.remote_exec import (  # noqa: F401
    RemoteExecutorHandler,
)
from core.handlers.resource_agent import (  # noqa: F401
    ManageResourceHandler,
    SpawnAgentsHandler,
    GetAgentResultsHandler,
    UseSkillHandler,
    ShowFileHandler,
)
from core.handlers.task_management import (  # noqa: F401
    AssignTaskHandler,
    CompleteTaskHandler,
    VerifyTaskHandler,
)
from core.handlers.user_interaction import (  # noqa: F401
    NotifyUserHandler,
    AskUserHandler,
)
from core.handlers.web_fetch import (  # noqa: F401
    ExecuteScriptHandler,
    ReadFileHandler,
    WebSearchHandler,
    WebFetchHandler,
    ScraplingFetchHandler,
)
