__version__ = "0.3.3"

from prescribe.adapters import (
    JsoncAdapter,
    TomlAdapter,
    YamlAdapter,
    adapter_for_path,
    jsonc_adapter,
    toml_adapter,
    yaml_adapter,
)
from prescribe.core import (
    ConflictResult,
    DesiredState,
    FileFingerprint,
    OrchestrationResult,
    PlannedOperation,
    Planner,
    PlanResult,
    apply_operations,
    detect_conflict,
    file_fingerprint,
)
from prescribe.document import Adapter, Document
from prescribe.materialize import materialize
from prescribe.orchestrator import (
    Orchestrator,
    arch_matches,
    condition_matches,
    current_arch,
    current_machine,
    machine_matches,
    platform_matches,
)
from prescribe.paths import config_dir, data_dir, default_state_path
from prescribe.presets import Presets, apply_all, default_spec_dir, discover_specs, list_specs, load_specs, status
from prescribe.shell import render_shell_block
from prescribe.shell_extract import (
    ShellEnvUpdate,
    ShellExtraction,
    ShellExtractionIssue,
    extract_shell_env,
    extract_shell_env_file,
    infer_shell_type,
)
from prescribe.spec import (
    AssetTarget,
    EnvTarget,
    FileTarget,
    Location,
    LocationCandidate,
    ShellTarget,
    Spec,
    SpecError,
    SpecLoader,
)
from prescribe.state import EventRecord, RunRecord, SnapshotRecord, StateStore
from prescribe.tools import (
    InstalledTool,
    ManagerInspector,
    ManagerResult,
    ToolCandidate,
    ToolEntrypoint,
    ToolInventory,
    ToolPathSource,
    inspect_installed_tools,
    inspect_tool_paths,
)

__all__ = [
    # adapters
    "Adapter",
    "Document",
    "JsoncAdapter",
    "TomlAdapter",
    "YamlAdapter",
    "adapter_for_path",
    "jsonc_adapter",
    "toml_adapter",
    "yaml_adapter",
    # core
    "ConflictResult",
    "DesiredState",
    "FileFingerprint",
    "OrchestrationResult",
    "PlannedOperation",
    "Planner",
    "PlanResult",
    "apply_operations",
    "detect_conflict",
    "file_fingerprint",
    # orchestrator
    "Orchestrator",
    "arch_matches",
    "condition_matches",
    "current_arch",
    "current_machine",
    "machine_matches",
    "platform_matches",
    # paths
    "config_dir",
    "data_dir",
    "default_state_path",
    # presets
    "Presets",
    "apply_all",
    "default_spec_dir",
    "discover_specs",
    "list_specs",
    "load_specs",
    "status",
    # materialize
    "materialize",
    # shell
    "render_shell_block",
    "ShellEnvUpdate",
    "ShellExtraction",
    "ShellExtractionIssue",
    "extract_shell_env",
    "extract_shell_env_file",
    "infer_shell_type",
    # spec
    "AssetTarget",
    "EnvTarget",
    "FileTarget",
    "Location",
    "LocationCandidate",
    "ShellTarget",
    "Spec",
    "SpecError",
    "SpecLoader",
    # state
    "EventRecord",
    "RunRecord",
    "SnapshotRecord",
    "StateStore",
    # tools
    "InstalledTool",
    "ManagerInspector",
    "ManagerResult",
    "ToolCandidate",
    "ToolEntrypoint",
    "ToolInventory",
    "ToolPathSource",
    "inspect_installed_tools",
    "inspect_tool_paths",
]
