"""Shared aind-data-schema v1/v2 metadata reads and output builders.

Public API grouped by module:

- :mod:`aind_pophys_metadata.io` — file discovery, schema-version dispatch,
  raw JSON reads, discriminator resolution.
- :mod:`aind_pophys_metadata.fields` — field-level v1/v2 getters (frame rate,
  identifiers, FOV/plane naming).
- :mod:`aind_pophys_metadata.paths` — glob-with-a-default input discovery and
  provenance-safe relative paths.
- :mod:`aind_pophys_metadata.processing` — ``processing.json`` builders.
- :mod:`aind_pophys_metadata.quality_control` — ``quality_control.json``
  builders.
- :mod:`aind_pophys_metadata.runner` — the stage guard that writes
  ``processing.json`` on both the success and the failure path.

Every name in ``__all__`` is re-exported here and also remains importable
from its own submodule.
"""

from aind_pophys_metadata.fields import (
    SCAVENGE_MODE_ALL,
    SCAVENGE_MODE_SINGLE,
    SCAVENGE_MODES,
    acronym_from_targeted_structure,
    as_int,
    build_fov_ids,
    fov_id,
    get_dataset_name,
    get_fov_ids,
    get_fov_pairs_v1,
    get_fov_pairs_v2,
    get_frame_rate,
    get_frame_rate_platform,
    get_frame_rate_v1,
    get_frame_rate_v2,
    get_instrument_id,
    get_subject_id,
    resolve_frame_rate,
    validate_scavenged_ids,
)
from aind_pophys_metadata.io import (
    DATA_DESCRIPTION_FILE,
    PLATFORM_FILE,
    SCHEMA_V1,
    SCHEMA_V2,
    SUBJECT_FILE,
    V1_CORE_FILE,
    V2_CORE_FILE,
    CommonMetadata,
    detect_schema_version,
    find,
    find_acquisition_file,
    load_common,
    load_json,
    load_optional,
    object_type_value,
    require,
)
from aind_pophys_metadata.paths import find_one, relative_to_root
from aind_pophys_metadata.processing import (
    MODEL_SOURCE_ASSET,
    MODEL_SOURCE_NETWORK,
    EPHEMERAL_PATH_MARKER,
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
    library_version,
    model_provenance,
    pipeline_code,
    pipeline_name_from_env,
    reject_ephemeral_paths,
    resource_usage,
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
    # io
    "CommonMetadata",
    "DATA_DESCRIPTION_FILE",
    "PLATFORM_FILE",
    "SCHEMA_V1",
    "SCHEMA_V2",
    "SUBJECT_FILE",
    "V1_CORE_FILE",
    "V2_CORE_FILE",
    "detect_schema_version",
    "find",
    "find_acquisition_file",
    "load_common",
    "load_json",
    "load_optional",
    "object_type_value",
    "require",
    # fields
    "SCAVENGE_MODES",
    "SCAVENGE_MODE_ALL",
    "SCAVENGE_MODE_SINGLE",
    "acronym_from_targeted_structure",
    "as_int",
    "build_fov_ids",
    "fov_id",
    "get_dataset_name",
    "get_fov_ids",
    "get_fov_pairs_v1",
    "get_fov_pairs_v2",
    "get_frame_rate",
    "get_frame_rate_platform",
    "get_frame_rate_v1",
    "get_frame_rate_v2",
    "get_instrument_id",
    "get_subject_id",
    "resolve_frame_rate",
    "validate_scavenged_ids",
    # paths
    "find_one",
    "relative_to_root",
    # processing
    "EPHEMERAL_PATH_MARKER",
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
    "library_version",
    "pipeline_code",
    "pipeline_name_from_env",
    "reject_ephemeral_paths",
    "resource_usage",
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
