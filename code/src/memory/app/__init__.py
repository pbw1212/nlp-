"""Personal Agent Product Application Package."""

from src.memory.app.console import PersonalAgentApp
from src.memory.app.tools import build_product_tool_registry
from src.memory.app.workspace import WorkspaceManager, WorkspaceScopeError
from src.memory.app.mcp import McpManager, McpServerConfig
from src.memory.app.storage import AgentStorageLayout, default_agent_data_dir, migrate_legacy_config, migrate_legacy_project_data

__all__ = [
    "PersonalAgentApp",
    "build_product_tool_registry",
    "WorkspaceManager",
    "WorkspaceScopeError",
    "AgentStorageLayout",
    "default_agent_data_dir",
    "migrate_legacy_project_data",
    "migrate_legacy_config",
    "McpManager",
    "McpServerConfig",
]
