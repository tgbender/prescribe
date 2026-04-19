__version__ = "0.1.0"

from prescribe.adapters import (
    Json5Adapter,
    JsoncAdapter,
    TomlAdapter,
    YamlAdapter,
    adapter_for_path,
    json5_adapter,
    jsonc_adapter,
    toml_adapter,
    yaml_adapter,
)
from prescribe.core import (
    ConflictResult,
    DesiredState,
    FileFingerprint,
    Operation,
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
from prescribe.spec import Spec, SpecError, SpecLoader, SpecTarget
from prescribe.state import EventRecord, RunRecord, SnapshotRecord, StateStore

__all__ = [
    "Adapter",
    "Document",
    "ConflictResult",
    "DesiredState",
    "EventRecord",
    "FileFingerprint",
    "Json5Adapter",
    "JsoncAdapter",
    "Operation",
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
    "detect_conflict",
    "file_fingerprint",
    "json5_adapter",
    "jsonc_adapter",
    "current_machine",
    "machine_matches",
    "platform_matches",
    "toml_adapter",
    "yaml_adapter",
]
