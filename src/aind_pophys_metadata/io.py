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
from pathlib import Path
from typing import Any, Optional

SCHEMA_V1 = "v1"
SCHEMA_V2 = "v2"

V1_CORE_FILE = "session.json"
V2_CORE_FILE = "acquisition.json"


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
    return next(Path(input_dir).rglob(name), None)


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
    acq_matches = list(input_dir.rglob(V2_CORE_FILE))
    session_matches = list(input_dir.rglob(V1_CORE_FILE))
    if acq_matches and session_matches:
        raise ValueError(
            f"Ambiguous input: found both v2 {V2_CORE_FILE} "
            f"({acq_matches[0]}) and v1 {V1_CORE_FILE} "
            f"({session_matches[0]}) under {input_dir}. "
            f"Remove one to disambiguate."
        )
    if acq_matches:
        return acq_matches[0]
    if session_matches:
        return session_matches[0]
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
    return SCHEMA_V1 if Path(core_file).name == V1_CORE_FILE else SCHEMA_V2


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
