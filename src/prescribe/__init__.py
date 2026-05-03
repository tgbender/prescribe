__version__ = "0.1.1"

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
from prescribe.orchestrator import (
    Orchestrator,
    current_machine,
    machine_matches,
    platform_matches,
)
from prescribe.paths import config_dir, data_dir, default_state_path
from prescribe.spec import Spec, SpecError, SpecLoader, SpecTarget
from prescribe.state import EventRecord, RunRecord, SnapshotRecord, StateStore

__all__ = [
    "Adapter",
    "Document",
    "ConflictResult",
    "DesiredState",
    "EventRecord",
    "FileFingerprint",
    "JsoncAdapter",
    "OrchestrationResult",
    "Orchestrator",
    "PlanResult",
    "Planner",
    "PlannedOperation",
    "RunRecord",
    "SnapshotRecord",
    "Spec",
    "SpecError",
    "SpecLoader",
    "SpecTarget",
    "StateStore",
    "TomlAdapter",
    "YamlAdapter",
    "adapter_for_path",
    "apply_operations",
    "config_dir",
    "data_dir",
    "default_state_path",
    "detect_conflict",
    "file_fingerprint",
    "jsonc_adapter",
    "current_machine",
    "machine_matches",
    "platform_matches",
    "toml_adapter",
    "yaml_adapter",
]
