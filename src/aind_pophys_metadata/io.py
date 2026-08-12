"""Filesystem, JSON and path helpers, and schema-version detection."""

import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class SchemaVersion(str, Enum):
    """Which metadata schema a core document is written in.

    An enum so an unrecognised version cannot exist past construction.
    Subclasses ``str``, so a member formats and compares as its value.
    """

    V1 = "v1"
    V2 = "v2"
    MINIMAL = "minimal"

    __str__ = str.__str__


# Readable spellings of the members, used throughout for symmetry with the
# core-filename constants below.
SCHEMA_V1 = SchemaVersion.V1
SCHEMA_V2 = SchemaVersion.V2
SCHEMA_MINIMAL = SchemaVersion.MINIMAL

V1_CORE_FILE = "session.json"
V2_CORE_FILE = "acquisition.json"
MINIMAL_CORE_FILE = "metadata.json"

# Ordered so the richest available document wins when more than one is present.
_CORE_FILE_VERSIONS = (
    (V2_CORE_FILE, SCHEMA_V2),
    (V1_CORE_FILE, SCHEMA_V1),
    (MINIMAL_CORE_FILE, SCHEMA_MINIMAL),
)

PLATFORM_FILE = "platform.json"
SUBJECT_FILE = "subject.json"
DATA_DESCRIPTION_FILE = "data_description.json"


def find_one(
    directory: Path,
    pattern: str,
    recursive: bool = True,
    required: bool = True,
) -> Optional[Path]:
    """Return the first sorted match for ``pattern``, or ``None``/raise.

    Every glob should go through here so no ``next(glob(...))`` is ever left
    without a default: a bare ``StopIteration`` carries no filename and no
    directory, which is the least debuggable way for a pipeline to fail the
    moment an upstream capsule renames an artifact - exactly what a schema
    migration does.

    Parameters
    ----------
    directory : pathlib.Path
        Directory to search.
    pattern : str
        Glob pattern to match.
    recursive : bool, optional
        Search subdirectories too. Note ``rglob`` does not follow symlinks on
        Python <= 3.12, so pass ``False`` and glob the exact directory when
        the target sits behind one.
    required : bool, optional
        Raise when nothing matches instead of returning ``None``.

    Returns
    -------
    pathlib.Path or None
        The lexicographically first match, or ``None`` when nothing matched
        and ``required`` is False. Matches are sorted before selection
        because ``glob``/``rglob`` yield in filesystem order, which differs
        between machines and between runs - an unstable pick is not
        reproducible provenance.

    Raises
    ------
    FileNotFoundError
        If nothing matched and ``required`` is True. The message names both
        the pattern and the directory.
    """
    directory = Path(directory)
    matches = (
        directory.rglob(pattern) if recursive else directory.glob(pattern)
    )
    match = next(iter(sorted(matches)), None)
    if match is None and required:
        raise FileNotFoundError(
            f"No file matching {pattern!r} found under {directory}"
        )
    return match


def relative_to_root(root: Path, path: Path) -> str:
    """Return ``path`` relative to ``root``, as a string.

    Both sides are resolved first. Inside the pipeline ``/results`` and
    ``/data`` are symlinks into the task work directory, so comparing a
    resolved root against an unresolved path raises ``ValueError`` and leaks
    an absolute container path (e.g. ``/results/VISp_0/dff``) into the
    metadata.

    Parameters
    ----------
    root : pathlib.Path
        Directory the result should be relative to (the results or data root).
    path : pathlib.Path
        Path to express relative to ``root``.

    Returns
    -------
    str
        ``path`` relative to ``root``, or the resolved absolute ``path`` when
        it is genuinely outside ``root`` (so provenance is never dropped
        entirely).
    """
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(Path(root).resolve()))
    except ValueError:
        # Resolved, not the caller's input: a relative path outside the root
        # would otherwise be recorded as another relative path, which says
        # nothing about where the file actually is.
        return str(resolved)


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


def require(blob: dict, key: str, file_path: Path) -> Any:
    """Return ``blob[key]`` or raise, naming the field and file.

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
    ValueError
        If ``key`` is not present in ``blob``. ValueError throughout, so a
        caller catches one type for every missing-field failure.
    """
    if key not in blob:
        raise ValueError(
            f"Required field '{key}' missing from {Path(file_path).name}"
        )
    return blob[key]


def find_core_file(input_dir: Path) -> Path:
    """Locate the core acquisition file under ``input_dir``.

    Prefers v2 ``acquisition.json``, then v1 ``session.json``, then the
    minimal ``metadata.json``. A ``metadata.json`` beside a v1 or v2 file is
    ignored rather than called ambiguous, since raw assets routinely ship the
    whole-record export under that name.

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
        If none of the three core files is present.
    """
    input_dir = Path(input_dir)
    acq_matches = sorted(input_dir.rglob(V2_CORE_FILE))
    session_matches = sorted(input_dir.rglob(V1_CORE_FILE))
    schema_matches = acq_matches + session_matches
    if len(schema_matches) > 1:
        raise ValueError(
            "Ambiguous input: found multiple core metadata files under "
            f"{input_dir}: {', '.join(map(str, schema_matches))}. "
            "Keep exactly one session.json or acquisition.json."
        )
    if schema_matches:
        return schema_matches[0]
    minimal_matches = sorted(input_dir.rglob(MINIMAL_CORE_FILE))
    if len(minimal_matches) > 1:
        raise ValueError(
            f"Ambiguous input: found multiple {MINIMAL_CORE_FILE} files "
            f"under {input_dir}: {', '.join(map(str, minimal_matches))}."
        )
    if minimal_matches:
        return minimal_matches[0]
    raise FileNotFoundError(
        f"No {V2_CORE_FILE} (v2), {V1_CORE_FILE} (v1) or "
        f"{MINIMAL_CORE_FILE} (minimal) found under {input_dir}"
    )


def detect_schema_version(core_file: Path) -> SchemaVersion:
    """Return the schema version a core file's name declares.

    Parameters
    ----------
    core_file : Path
        Path to the core acquisition file (typically from
        :func:`find_core_file`).

    Returns
    -------
    SchemaVersion
        The member the file name maps to.

    Raises
    ------
    ValueError
        If the file name is not one of the three recognised core files.
    """
    name = Path(core_file).name
    for core_name, version in _CORE_FILE_VERSIONS:
        if name == core_name:
            return version
    raise ValueError(f"Unsupported core metadata file: {core_file}")


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
    path = find_one(input_dir, name, recursive=True, required=False)
    return load_json(path) if path else None


def reject_whole_record(core_raw: dict) -> None:
    """Raise if a ``metadata.json`` is a whole-record export.

    The whole-record DocDB export shares the minimal document's filename and
    nests the core document under ``session`` or ``acquisition``. Read as
    minimal it would find no fields rather than failing.

    Parameters
    ----------
    core_raw : dict
        Raw ``metadata.json`` dict.

    Raises
    ------
    ValueError
        If the dict carries a ``session`` or ``acquisition`` key.
    """
    for key in (V1_CORE_FILE, V2_CORE_FILE):
        nested = key.removesuffix(".json")
        if nested in core_raw:
            raise ValueError(
                f"{MINIMAL_CORE_FILE} is a whole-record metadata export (it "
                f"carries {nested!r}), not a minimal document. Extract "
                f"the {key} core file and read that instead."
            )


def object_type_value(cls) -> str:
    """Return a v2 schema class's ``object_type`` discriminator default.

    Read from ``model_fields`` rather than hardcoded, so callers stay correct
    if the schema re-words a discriminator.

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
