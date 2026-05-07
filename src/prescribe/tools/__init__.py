"""Read-only tool and tool-manager inspection."""

from prescribe.tools.inventory import inspect_installed_tools, inspect_tool_paths
from prescribe.tools.models import (
    InstalledTool,
    ManagerInspector,
    ManagerResult,
    ToolCandidate,
    ToolEntrypoint,
    ToolInventory,
    ToolManager,
    ToolPathSource,
    ToolScope,
)

__all__ = [
    "InstalledTool",
    "ManagerInspector",
    "ManagerResult",
    "ToolCandidate",
    "ToolEntrypoint",
    "ToolInventory",
    "ToolManager",
    "ToolPathSource",
    "ToolScope",
    "inspect_tool_paths",
    "inspect_installed_tools",
]
