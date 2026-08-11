"""Filesystem and schema-version helpers at the aind-data-schema boundary.

Shared, capsule-agnostic I/O for reading AIND metadata:

- locate the core acquisition file under an input directory,
- dispatch schema version by which core file is present
  (``session.json`` -> v1, ``acquisition.json`` -> v2 — the v1->v2 rename is
  the authoritative version signal, so no ``schema_version`` field is read),
- read raw JSON dicts (no Pydantic validation on the read path),
- resolve v2 ``object_type`` discriminators from the schema classes
  themselves, so they track the installed aind-data-schema version.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from aind_pophys_metadata.paths import find_one

SCHEMA_V1 = "v1"
SCHEMA_V2 = "v2"

V1_CORE_FILE = "session.json"
V2_CORE_FILE = "acquisition.json"

PLATFORM_FILE = "platform.json"
SUBJECT_FILE = "subject.json"
DATA_DESCRIPTION_FILE = "data_description.json"


def load_json(path: Path) -> dict:
    """Parse a JSON file into a dict.

    Parameters
    ----------
    path : Path
        Path to the JSON file.

    Returns
    -------
    dict
        The parsed JSON object.
    """
    with open(path) as f:
        return json.load(f)


def find(input_dir: Path, name: str) -> Optional[Path]:
    """Return the first recursive match for ``name``, or ``None``.

    Parameters
    ----------
    input_dir : Path
        Directory searched recursively.
    name : str
        Exact file name to match.

    Returns
    -------
    Path or None
        The first match, or ``None`` if there is none.
    """
    return find_one(input_dir, name, recursive=True, required=False)


def require(blob: dict, key: str, file_path: Path) -> Any:
    """Return ``blob[key]`` or raise a KeyError naming the field and file.

    Parameters
    ----------
    blob : dict
        The raw JSON dict.
    key : str
        Required key.
    file_path : Path
        Source file path, used only for the error message.

    Returns
    -------
    Any
        The value at ``blob[key]``.

    Raises
    ------
    KeyError
        If ``key`` is not present in ``blob``.
    """
    if key not in blob:
        raise KeyError(
            f"Required field '{key}' missing from {Path(file_path).name}"
        )
    return blob[key]


def find_acquisition_file(input_dir: Path) -> Path:
    """Locate the core v1/v2 acquisition file under ``input_dir``.

    Prefers v2 ``acquisition.json``; falls back to v1 ``session.json``.
    Raises if both are present (ambiguous) or neither is.

    Parameters
    ----------
    input_dir : Path
        Directory searched recursively.

    Returns
    -------
    Path
        Path to the located core file.

    Raises
    ------
    ValueError
        If both ``acquisition.json`` and ``session.json`` are present.
    FileNotFoundError
        If neither file is present.
    """
    input_dir = Path(input_dir)
    acq_matches = sorted(input_dir.rglob(V2_CORE_FILE))
    session_matches = sorted(input_dir.rglob(V1_CORE_FILE))
    matches = acq_matches + session_matches
    if len(matches) > 1:
        raise ValueError(
            "Ambiguous input: found multiple core metadata files under "
            f"{input_dir}: {', '.join(map(str, matches))}. "
            "Keep exactly one session.json or acquisition.json."
        )
    if matches:
        return matches[0]
    raise FileNotFoundError(
        f"No {V2_CORE_FILE} (v2) or {V1_CORE_FILE} (v1) found under "
        f"{input_dir}"
    )


def detect_schema_version(core_file: Path) -> str:
    """Return ``"v1"`` or ``"v2"`` from a core file's name.

    Parameters
    ----------
    core_file : Path
        Path to the core acquisition file (typically from
        :func:`find_acquisition_file`).

    Returns
    -------
    str
        :data:`SCHEMA_V1` for ``session.json``, else :data:`SCHEMA_V2`.
    """
    name = Path(core_file).name
    if name == V1_CORE_FILE:
        return SCHEMA_V1
    if name == V2_CORE_FILE:
        return SCHEMA_V2
    raise ValueError(f"Unsupported core metadata file: {core_file}")


@dataclass(frozen=True)
class CommonMetadata:
    """Raw dicts and version every capsule's metadata read starts from.

    Deliberately *not* a base class for capsule metadata dataclasses. The
    capsules consume anywhere from 3 to 21 fields, so each spreads this
    result into its own dataclass rather than inheriting a shape that would
    fit none of them. This carries only the shared preamble.

    Attributes
    ----------
    core_path : Path
        Path to the located ``session.json`` / ``acquisition.json``.
    version : str
        :data:`SCHEMA_V1` or :data:`SCHEMA_V2`, from
        :func:`detect_schema_version`.
    core_raw : dict
        Raw core acquisition dict.
    platform_raw : dict or None
        Raw ``platform.json`` dict, or ``None`` when absent.
    subject_raw : dict or None
        Raw ``subject.json`` dict, or ``None`` when absent.
    data_description_raw : dict or None
        Raw ``data_description.json`` dict, or ``None`` when absent.
    """

    core_path: Path
    version: str
    core_raw: dict
    platform_raw: Optional[dict] = None
    subject_raw: Optional[dict] = None
    data_description_raw: Optional[dict] = None


def load_optional(input_dir: Path, name: str) -> Optional[dict]:
    """Load a sibling metadata file if present, else return ``None``.

    Parameters
    ----------
    input_dir : Path
        Directory searched recursively.
    name : str
        Exact file name to load.

    Returns
    -------
    dict or None
        The parsed dict, or ``None`` when the file is absent.
    """
    path = find(input_dir, name)
    return load_json(path) if path else None


def load_common(input_dir: Path) -> CommonMetadata:
    """Locate the core file, detect its version, and load common siblings.

    Parameters
    ----------
    input_dir : Path
        Directory searched recursively for the metadata files.

    Returns
    -------
    CommonMetadata
        The core path, schema version and the four raw dicts.

    Raises
    ------
    ValueError
        If both core files are present (see :func:`find_acquisition_file`).
    FileNotFoundError
        If neither core file is present.
    """
    input_dir = Path(input_dir)
    core_path = find_acquisition_file(input_dir)
    return CommonMetadata(
        core_path=core_path,
        version=detect_schema_version(core_path),
        core_raw=load_json(core_path),
        platform_raw=load_optional(input_dir, PLATFORM_FILE),
        subject_raw=load_optional(input_dir, SUBJECT_FILE),
        data_description_raw=load_optional(input_dir, DATA_DESCRIPTION_FILE),
    )


def object_type_value(cls) -> str:
    """Return a v2 schema class's ``object_type`` discriminator default.

    In aind-data-schema v2 the ``object_type`` field is a ``Literal`` whose
    value is derived from the class name (e.g. ``ImagingConfig`` ->
    ``"Imaging config"``) and used as the discriminator in serialized JSON.
    Reading it from ``model_fields`` instead of hardcoding the string keeps
    callers correct if the schema re-words a discriminator.

    Parameters
    ----------
    cls : type
        An aind-data-schema v2 model class with an ``object_type`` field.

    Returns
    -------
    str
        The discriminator string the class serializes to.
    """
    return cls.model_fields["object_type"].default
