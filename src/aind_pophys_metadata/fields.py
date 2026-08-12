"""Field-level readers over raw v1/v2 metadata dicts.

Each getter knows the exact dict path for a single field in both schema
versions and returns a raw scalar (or tuple). Capsule libraries compose these
into their own minimal metadata dataclass — the shared layer never imposes
one, because each capsule consumes a different subset of fields.

The ``*_v1`` / ``*_v2`` functions are the per-version primitives; the
``get_*`` dispatchers pick the right one from a schema-version string (see
:func:`aind_pophys_metadata.io.detect_schema_version`).
"""

import logging
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from aind_data_schema.components.configs import (
    ImagingConfig,
    PlanarImage,
    PlanarImageStack,
)

from aind_pophys_metadata.io import (
    PLATFORM_FILE,
    SCHEMA_V1,
    object_type_value,
    require,
)

logger = logging.getLogger(__name__)

# v2 discriminator values, read from the schema classes (not hardcoded) so
# they track the installed aind-data-schema version.
_V2_IMAGING_CONFIG_TYPE = object_type_value(ImagingConfig)
_V2_PLANAR_IMAGE_TYPES = {
    object_type_value(PlanarImage),
    object_type_value(PlanarImageStack),
}

# Comparison modes for :func:`validate_scavenged_ids`.
SCAVENGE_MODE_ALL = "all"
SCAVENGE_MODE_SINGLE = "single"
SCAVENGE_MODES = (SCAVENGE_MODE_ALL, SCAVENGE_MODE_SINGLE)


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
        for fov in stream.get("ophys_fovs") or []:
            if fov.get("frame_rate") is not None:
                return float(fov["frame_rate"])
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


def resolve_frame_rate(
    version: str,
    core_raw: dict,
    platform_raw: Optional[dict] = None,
    *,
    core_file: Optional[Path] = None,
    input_dir: Optional[Path] = None,
    cli_override: Optional[float] = None,
    required: bool = True,
) -> Optional[float]:
    """Frame rate from metadata, then a CLI override, then raise.

    Wraps :func:`get_frame_rate` with the fallback layer every consuming
    capsule needs, so the failure message is identical across the fleet
    instead of being reinvented per repo.

    Parameters
    ----------
    version : str
        Schema version string.
    core_raw : dict
        Raw ``session.json`` (v1) or ``acquisition.json`` (v2) dict.
    platform_raw : dict, optional
        Raw ``platform.json`` dict, used only if the core file omits it.
    core_file : Path, optional
        Path to the core file, named in the error message.
    input_dir : Path, optional
        Input directory, named in the error message.
    cli_override : float, optional
        Operator-supplied frame rate, used when metadata provides none.
    required : bool, optional
        Raise when nothing supplies a frame rate instead of returning
        ``None``.

    Returns
    -------
    float or None
        The frame rate in Hz, or ``None`` when unavailable and not
        ``required``.

    Raises
    ------
    ValueError
        If ``required`` and neither the metadata nor ``cli_override``
        supplies one. The message names the files tried and the schema
        version.
    """
    rate = get_frame_rate(version, core_raw, platform_raw)
    if rate is None and cli_override is not None:
        logger.warning("Using CLI fallback frame rate: %s", cli_override)
        rate = cli_override
    if rate is None and required:
        core_name = Path(core_file).name if core_file else "the core file"
        raise ValueError(
            f"No frame rate found in {core_name} (schema {version}) or "
            f"{PLATFORM_FILE} under {input_dir or 'the input directory'}, "
            "and no CLI override was supplied."
        )
    return None if rate is None else float(rate)


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


def as_int(value: Any) -> Optional[int]:
    """Coerce a raw metadata value to int, or return ``None``.

    An explicit JSON ``null`` is distinct from an absent key: ``dict.get``
    returns the ``None`` rather than the default, so a bare ``int(...)``
    raises ``TypeError`` on a field a real asset is allowed to leave blank.

    Parameters
    ----------
    value : Any
        Raw value from the metadata dict; strings are common in v1.

    Returns
    -------
    int or None
        The coerced value, or ``None`` when absent or uncoercible.
    """
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        logger.warning("Ignoring non-numeric metadata value %r", value)
        return None


# Retained for consumers that imported the helper by its former private name.
_as_int = as_int


def acronym_from_targeted_structure(value: Any) -> Optional[str]:
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
            pairs.append((as_int(fov.get("index")) or 0, acronym))
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
                    pairs.append(
                        (as_int(plane.get("plane_index")) or 0, acronym)
                    )
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


def validate_scavenged_ids(
    scavenged: Iterable[str],
    canonical: Iterable[str],
    logger: Optional[logging.Logger] = None,
    *,
    mode: str = SCAVENGE_MODE_ALL,
) -> bool:
    """Warn when directory-scavenged plane ids differ from canonical ones.

    Some capsules recover plane ids from the names of upstream output
    directories rather than re-deriving them from the acquisition metadata.
    Uniqueness is the hard requirement and directory names already guarantee
    it; matching the canonical ids only buys readability in the emitted
    document. A divergence is therefore reported and never raised - killing
    a completed run over a cosmetic id mismatch is the wrong trade.

    Two comparisons are needed, and which one applies is a property of the
    caller, not of the data. A per-plane capsule sees one plane per task
    while :func:`get_fov_ids` returns every plane in the acquisition, so
    set equality would warn on every plane of every multiplane run - noise
    that trains readers to ignore the warning. The mode is therefore
    explicit rather than inferred from how many ids were scavenged: a
    one-plane multiplane acquisition would otherwise take the wrong branch
    silently.

    An empty ``canonical`` short-circuits to ``True``: an acquisition file
    that lists no planes offers nothing to disagree with, and warning there
    would be a guaranteed false positive.

    Emits at most one warning per call, carrying both id sets so the
    divergence is diagnosable from the run log alone.

    Parameters
    ----------
    scavenged : iterable of str
        Plane ids recovered from the input directory layout.
    canonical : iterable of str
        Plane ids derived from the acquisition metadata (see
        :func:`get_fov_ids`).
    logger : logging.Logger, optional
        Logger to warn on; defaults to this module's logger.
    mode : str, optional
        ``SCAVENGE_MODE_ALL`` (the default) checks set equality and suits a
        caller that scavenged every plane in the acquisition.
        ``SCAVENGE_MODE_SINGLE`` checks membership - every scavenged id must
        appear among the canonical ids - and suits a per-plane capsule whose
        task sees exactly one plane.

    Returns
    -------
    bool
        ``True`` when the scavenged ids are consistent with the canonical
        ones under ``mode``, ``False`` when they diverge.

    Raises
    ------
    ValueError
        If ``mode`` is not one of the two supported values. A mistyped mode
        is a caller bug, not a data divergence, so it is not warned past.
    """
    if mode not in SCAVENGE_MODES:
        raise ValueError(
            f"Unknown mode {mode!r}; expected one of {sorted(SCAVENGE_MODES)}."
        )
    scavenged_set = set(scavenged)
    canonical_set = set(canonical)
    if not canonical_set:
        return True
    if mode == SCAVENGE_MODE_SINGLE:
        consistent = scavenged_set <= canonical_set
    else:
        consistent = scavenged_set == canonical_set
    if consistent:
        return True
    log = logger if logger is not None else logging.getLogger(__name__)
    log.warning(
        "Scavenged plane ids %s are not consistent with the canonical ids "
        "%s derived from the acquisition metadata (mode=%s). Proceeding "
        "with the scavenged ids: they are unique, which is what the "
        "document requires.",
        sorted(scavenged_set),
        sorted(canonical_set),
        mode,
    )
    return False
