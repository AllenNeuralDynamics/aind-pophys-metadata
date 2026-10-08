"""Shared AIND pophys metadata reads and output builders."""

from aind_pophys_metadata.core import (
    DEFAULT_LENGTH_UNIT,
    CoreMetadata,
)
from aind_pophys_metadata.io import (
    DATA_DESCRIPTION_FILE,
    MINIMAL_CORE_FILE,
    PLATFORM_FILE,
    SCHEMA_MINIMAL,
    SCHEMA_V1,
    SCHEMA_V2,
    SUBJECT_FILE,
    V1_CORE_FILE,
    V2_CORE_FILE,
    SchemaVersion,
    detect_schema_version,
    find_core_file,
    find_one,
    load_json,
    load_optional,
    object_type_value,
    reject_whole_record,
    relative_to_root,
    require,
)
from aind_pophys_metadata.naming import (
    build_fov_ids,
    fov_id,
)
from aind_pophys_metadata.processing import (
    MODEL_SOURCE_ASSET,
    MODEL_SOURCE_NETWORK,
    PIPELINE_NAME_ENV,
    PIPELINE_URL_ENV,
    PIPELINE_VERSION_ENV,
    PROCESSING_JSON,
    build_code,
    build_data_process,
    build_dependency_graph,
    build_processing,
    collect_static_resources,
    collect_upstream_process_names,
    file_sha256,
    model_provenance,
    pipeline_code,
    pipeline_name_from_env,
    write_processing_json,
)
from aind_pophys_metadata.quality_control import (
    DEFAULT_GROUPING,
    QUALITY_CONTROL_JSON,
    build_quality_control,
    dropdown_metric,
    pending_qc_status,
    write_quality_control_json,
)
from aind_pophys_metadata.runner import (
    EVENT_TYPE_FIELD,
    STAGE_COMPLETE,
    STAGE_ERROR,
    STAGE_START,
    StageContext,
    stage_guard,
)

__version__ = "0.1.0"

__all__ = [
    # core
    "CoreMetadata",
    "DEFAULT_LENGTH_UNIT",
    # io
    "DATA_DESCRIPTION_FILE",
    "MINIMAL_CORE_FILE",
    "PLATFORM_FILE",
    "SCHEMA_MINIMAL",
    "SCHEMA_V1",
    "SCHEMA_V2",
    "SUBJECT_FILE",
    "SchemaVersion",
    "V1_CORE_FILE",
    "V2_CORE_FILE",
    "detect_schema_version",
    "find_core_file",
    "find_one",
    "load_json",
    "load_optional",
    "object_type_value",
    "reject_whole_record",
    "relative_to_root",
    "require",
    # naming
    "build_fov_ids",
    "fov_id",
    # processing
    "MODEL_SOURCE_ASSET",
    "MODEL_SOURCE_NETWORK",
    "PIPELINE_NAME_ENV",
    "PIPELINE_URL_ENV",
    "PIPELINE_VERSION_ENV",
    "PROCESSING_JSON",
    "build_code",
    "build_data_process",
    "build_dependency_graph",
    "build_processing",
    "collect_static_resources",
    "collect_upstream_process_names",
    "file_sha256",
    "model_provenance",
    "pipeline_code",
    "pipeline_name_from_env",
    "write_processing_json",
    # quality_control
    "DEFAULT_GROUPING",
    "QUALITY_CONTROL_JSON",
    "build_quality_control",
    "dropdown_metric",
    "pending_qc_status",
    "write_quality_control_json",
    # runner
    "EVENT_TYPE_FIELD",
    "STAGE_COMPLETE",
    "STAGE_ERROR",
    "STAGE_START",
    "StageContext",
    "stage_guard",
]
