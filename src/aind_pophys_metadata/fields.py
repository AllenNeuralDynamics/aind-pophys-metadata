"""Field-level readers over raw v1/v2 metadata dicts.

Each getter knows the exact dict path for a single field in both schema
versions and returns a raw scalar (or tuple). Capsule libraries compose these
into their own minimal metadata dataclass — the shared layer never imposes
one, because each capsule consumes a different subset of fields.

The ``*_v1`` / ``*_v2`` functions are the per-version primitives; the
``get_*`` dispatchers pick the right one from a schema-version string (see
:func:`aind_pophys_metadata.io.detect_schema_version`).
"""

from pathlib import Path
from typing import List, Optional, Tuple

from aind_data_schema.components.configs import (
    ImagingConfig,
    PlanarImage,
    PlanarImageStack,
)

from aind_pophys_metadata.io import (
    SCHEMA_V1,
    object_type_value,
    require,
)

# v2 discriminator values, read from the schema classes (not hardcoded) so
# they track the installed aind-data-schema version.
_V2_IMAGING_CONFIG_TYPE = object_type_value(ImagingConfig)
_V2_PLANAR_IMAGE_TYPES = {
    object_type_value(PlanarImage),
    object_type_value(PlanarImageStack),
}


# ---------------------------------------------------------------------------
# Frame rate
# ---------------------------------------------------------------------------


def get_frame_rate_v1(session: dict) -> Optional[float]:
    """Frame rate from a v1 session: first ophys_fov carrying a frame_rate.

    Parameters
    ----------
    session : dict
        Raw ``session.json`` dict.

    Returns
    -------
    float or None
        The frame rate in Hz, or ``None`` if no FOV declares one.
    """
    for stream in session.get("data_streams") or []:
        fovs = stream.get("ophys_fovs") or []
        if fovs and fovs[0].get("frame_rate") is not None:
            return float(fovs[0]["frame_rate"])
    return None


def get_frame_rate_v2(acquisition: dict) -> Optional[float]:
    """Frame rate from a v2 ``ImagingConfig.sampling_strategy.frame_rate``.

    Parameters
    ----------
    acquisition : dict
        Raw ``acquisition.json`` dict.

    Returns
    -------
    float or None
        The frame rate in Hz, or ``None`` if no imaging config declares one.
    """
    for stream in acquisition.get("data_streams") or []:
        for config in stream.get("configurations") or []:
            if config.get("object_type") == _V2_IMAGING_CONFIG_TYPE:
                sampling = config.get("sampling_strategy") or {}
                if sampling.get("frame_rate") is not None:
                    return float(sampling["frame_rate"])
    return None


def get_frame_rate_platform(platform: dict) -> Optional[float]:
    """Frame rate from a v1 ``platform.json`` imaging_plane_groups fallback.

    Parameters
    ----------
    platform : dict
        Raw ``platform.json`` dict.

    Returns
    -------
    float or None
        The acquisition frame rate in Hz, or ``None`` if unavailable.
    """
    groups = platform.get("imaging_plane_groups") or []
    if groups and groups[0].get("acquisition_framerate_Hz") is not None:
        return float(groups[0]["acquisition_framerate_Hz"])
    return None


def get_frame_rate(
    version: str,
    core_raw: dict,
    platform_raw: Optional[dict] = None,
) -> Optional[float]:
    """Frame rate for either schema version, with a platform.json fallback.

    Parameters
    ----------
    version : str
        Schema version (:data:`aind_pophys_metadata.io.SCHEMA_V1` or
        ``SCHEMA_V2``).
    core_raw : dict
        Raw ``session.json`` (v1) or ``acquisition.json`` (v2) dict.
    platform_raw : dict, optional
        Raw ``platform.json`` dict, used only if the core file omits the
        frame rate.

    Returns
    -------
    float or None
        The frame rate in Hz, or ``None`` if no source provides one.
    """
    if version == SCHEMA_V1:
        rate = get_frame_rate_v1(core_raw)
    else:
        rate = get_frame_rate_v2(core_raw)
    if rate is None and platform_raw is not None:
        rate = get_frame_rate_platform(platform_raw)
    return rate


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


def get_instrument_id(
    version: str,
    core_raw: dict,
    *,
    required: bool = False,
    file_path: Optional[Path] = None,
) -> Optional[str]:
    """Instrument/rig identifier (v1 ``rig_id`` / v2 ``instrument_id``).

    Parameters
    ----------
    version : str
        Schema version string.
    core_raw : dict
        Raw core acquisition dict.
    required : bool, optional
        When ``True``, raise ``KeyError`` if the id is missing (via
        :func:`aind_pophys_metadata.io.require`) instead of returning
        ``None``. Capsules that cannot proceed without it pass ``True``.
    file_path : Path, optional
        Source file path used only for the ``required`` error message.

    Returns
    -------
    str or None
        The instrument id; ``None`` if absent and not ``required``.

    Raises
    ------
    KeyError
        If ``required`` and the id is missing.
    """
    key = "rig_id" if version == SCHEMA_V1 else "instrument_id"
    if required:
        value = require(core_raw, key, file_path or Path(key))
    else:
        value = core_raw.get(key)
    return None if value is None else str(value)


def get_subject_id(
    core_raw: dict,
    subject_raw: Optional[dict] = None,
) -> Optional[str]:
    """Subject identifier — canonical from the core acquisition dict.

    Reads ``subject_id`` from the session/acquisition file (the canonical
    source); falls back to ``subject.json`` only when the core file omits it.

    Parameters
    ----------
    core_raw : dict
        Raw ``session.json`` / ``acquisition.json`` dict.
    subject_raw : dict, optional
        Raw ``subject.json`` dict, used only as a fallback.

    Returns
    -------
    str or None
        The subject id, or ``None`` if no source provides one.
    """
    value = core_raw.get("subject_id")
    if value is None and subject_raw is not None:
        value = subject_raw.get("subject_id")
    return None if value is None else str(value)


def get_dataset_name(
    data_description_raw: Optional[dict] = None,
) -> Optional[str]:
    """Dataset name from ``data_description.json``.

    Parameters
    ----------
    data_description_raw : dict, optional
        Raw ``data_description.json`` dict, or ``None`` when absent.

    Returns
    -------
    str or None
        The dataset name, or ``None`` if unavailable.
    """
    if not data_description_raw:
        return None
    return data_description_raw.get("name")


# ---------------------------------------------------------------------------
# FOV / plane naming
# ---------------------------------------------------------------------------


def acronym_from_targeted_structure(value) -> Optional[str]:
    """Extract a structure acronym from a v1/v2 ``targeted_structure`` value.

    The field type changed across schema versions, so tolerate all shapes: a
    bare acronym string (``"DLS"`` — early v1), a dict carrying an
    ``"acronym"`` key (later v1's ``CCFStructure`` and v2's structure model),
    or absent (``None``).

    Parameters
    ----------
    value : Any
        The raw ``targeted_structure`` value.

    Returns
    -------
    str or None
        The acronym, or ``None`` when none is available.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        acronym = value.get("acronym")
        return str(acronym) if acronym is not None else None
    return None


def fov_id(
    plane_index: int,
    acronym: Optional[str],
    *,
    single_plane: bool,
) -> str:
    """Return the canonical FOV/plane id under the active naming scheme.

    The id flows downstream (capsules key their outputs/folders on it), so it
    must be identical across every capsule for the same input.

    - single-plane -> ``"plane_{plane_index}"`` (stable, independent of
      targeted-structure metadata that single-plane acquisitions may omit).
    - multi-plane -> ``"{acronym}_{plane_index}"``, falling back to
      ``"plane_{plane_index}"`` when the acronym is missing so a malformed
      plane never yields ``"None_0"``.

    Parameters
    ----------
    plane_index : int
        Index of the plane within the imaging session.
    acronym : str or None
        CCF acronym of the plane's targeted structure, or ``None``.
    single_plane : bool
        Whether the acquisition has exactly one plane.

    Returns
    -------
    str
        The FOV/plane identifier.
    """
    if single_plane or not acronym:
        return f"plane_{plane_index}"
    return f"{acronym}_{plane_index}"


def build_fov_ids(pairs: List[Tuple[int, Optional[str]]]) -> Tuple[str, ...]:
    """Resolve canonical FOV ids for ``(plane_index, acronym)`` pairs.

    Sorts by plane index and applies :func:`fov_id` once per plane; single-
    vs multi-plane is decided here (exactly one plane -> single).

    Parameters
    ----------
    pairs : list of (int, str or None)
        Per-plane ``(plane_index, targeted_structure_acronym)`` pairs.

    Returns
    -------
    tuple of str
        Canonical FOV ids ordered by plane index; empty when ``pairs`` is
        empty.
    """
    pairs = sorted(pairs, key=lambda p: p[0])
    single_plane = len(pairs) == 1
    return tuple(
        fov_id(idx, acronym, single_plane=single_plane)
        for idx, acronym in pairs
    )


def get_fov_pairs_v1(session: dict) -> List[Tuple[int, Optional[str]]]:
    """Per-plane ``(plane_index, acronym)`` pairs from a v1 session.

    v1 layout: ``session["data_streams"][*]["ophys_fovs"][*]``; each FOV may
    carry an ``index`` (defaults to 0 for single-plane) and a
    ``targeted_structure``.

    Parameters
    ----------
    session : dict
        Raw ``session.json`` dict.

    Returns
    -------
    list of (int, str or None)
        Unsorted per-plane pairs.
    """
    pairs: List[Tuple[int, Optional[str]]] = []
    for stream in session.get("data_streams") or []:
        for fov in stream.get("ophys_fovs", []):
            acronym = acronym_from_targeted_structure(
                fov.get("targeted_structure")
            )
            pairs.append((int(fov.get("index", 0)), acronym))
    return pairs


def get_fov_pairs_v2(acquisition: dict) -> List[Tuple[int, Optional[str]]]:
    """Per-plane ``(plane_index, acronym)`` pairs from a v2 acquisition.

    v2 layout: ``acquisition["data_streams"][*]["configurations"][*]``
    (imaging configs) -> ``images[*]`` (planar images/stacks) ->
    ``planes[*]``; each plane may carry a ``plane_index`` (defaults to 0) and
    a ``targeted_structure``.

    Parameters
    ----------
    acquisition : dict
        Raw ``acquisition.json`` dict.

    Returns
    -------
    list of (int, str or None)
        Unsorted per-plane pairs.
    """
    pairs: List[Tuple[int, Optional[str]]] = []
    for stream in acquisition.get("data_streams") or []:
        for config in stream.get("configurations", []):
            if config.get("object_type") != _V2_IMAGING_CONFIG_TYPE:
                continue
            for image in config.get("images", []):
                if image.get("object_type") not in _V2_PLANAR_IMAGE_TYPES:
                    continue
                for plane in image.get("planes", []):
                    acronym = acronym_from_targeted_structure(
                        plane.get("targeted_structure")
                    )
                    pairs.append((int(plane.get("plane_index", 0)), acronym))
    return pairs


def get_fov_ids(version: str, core_raw: dict) -> Tuple[str, ...]:
    """Canonical FOV ids for either schema version.

    Parameters
    ----------
    version : str
        Schema version string.
    core_raw : dict
        Raw core acquisition dict.

    Returns
    -------
    tuple of str
        Canonical FOV ids ordered by plane index.
    """
    if version == SCHEMA_V1:
        pairs = get_fov_pairs_v1(core_raw)
    else:
        pairs = get_fov_pairs_v2(core_raw)
    return build_fov_ids(pairs)
