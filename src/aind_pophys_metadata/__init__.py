"""Shared aind-data-schema v1/v2 metadata reads and output builders.

Public API grouped by module:

- :mod:`aind_pophys_metadata.io` — file discovery, schema-version dispatch,
  raw JSON reads, discriminator resolution.
- :mod:`aind_pophys_metadata.fields` — field-level v1/v2 getters (frame rate,
  identifiers, FOV/plane naming).
- :mod:`aind_pophys_metadata.processing` — ``processing.json`` builders.
- :mod:`aind_pophys_metadata.quality_control` — ``quality_control.json``
  builders.
"""

from aind_pophys_metadata.fields import (
    acronym_from_targeted_structure,
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
)
from aind_pophys_metadata.io import (
    SCHEMA_V1,
    SCHEMA_V2,
    V1_CORE_FILE,
    V2_CORE_FILE,
    detect_schema_version,
    find,
    find_acquisition_file,
    load_json,
    object_type_value,
    require,
)
from aind_pophys_metadata.processing import (
    build_code,
    build_data_process,
    build_processing,
    resource_usage,
    write_processing_json,
)
from aind_pophys_metadata.quality_control import (
    DEFAULT_GROUPING,
    build_quality_control,
    dropdown_metric,
    pending_qc_status,
    write_quality_control_json,
)

__version__ = "0.1.0"

__all__ = [
    # io
    "SCHEMA_V1",
    "SCHEMA_V2",
    "V1_CORE_FILE",
    "V2_CORE_FILE",
    "detect_schema_version",
    "find",
    "find_acquisition_file",
    "load_json",
    "object_type_value",
    "require",
    # fields
    "acronym_from_targeted_structure",
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
    # processing
    "build_code",
    "build_data_process",
    "build_processing",
    "resource_usage",
    "write_processing_json",
    # quality_control
    "DEFAULT_GROUPING",
    "build_quality_control",
    "dropdown_metric",
    "pending_qc_status",
    "write_quality_control_json",
]
