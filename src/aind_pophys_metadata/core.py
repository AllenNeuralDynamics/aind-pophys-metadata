"""The CoreMetadata class and schema-agnostic readers"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aind_data_schema.components.configs import (
    ImagingConfig,
    LaserConfig,
    PlanarImage,
)
from aind_data_schema.components.coordinates import Scale

from aind_pophys_metadata import io
from aind_pophys_metadata.io import (
    PLATFORM_FILE,
    SchemaVersion,
    object_type_value,
    require,
)
from aind_pophys_metadata.naming import build_fov_ids

logger = logging.getLogger(__name__)

# v2 discriminator values, read from the schema classes (not hardcoded) so
# they track the installed aind-data-schema version.
_V2_IMAGING_CONFIG_TYPE = object_type_value(ImagingConfig)
# PlanarImageStack describes stack/channel acquisition, not a time-series FOV.
_V2_FOV_IMAGE_TYPE = object_type_value(PlanarImage)
_V2_SCALE_TYPE = object_type_value(Scale)
_V2_LASER_CONFIG_TYPE = object_type_value(LaserConfig)

DEFAULT_LENGTH_UNIT = "micrometer"


# ---------------------------------------------------------------------------
# Frame rate
# ---------------------------------------------------------------------------


def _frame_rate_v1(session: dict) -> Optional[float]:
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


def _frame_rate_v2(acquisition: dict) -> Optional[float]:
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


def _frame_rate_platform(platform: dict) -> Optional[float]:
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


def _frame_rate_minimal(blob: dict) -> Optional[float]:
    """Frame rate from a minimal ``metadata.json``.

    Parameters
    ----------
    blob : dict
        Raw ``metadata.json`` dict.

    Returns
    -------
    float or None
        The frame rate in Hz, or ``None`` when the key is absent.
    """
    return _as_float(blob.get("frame_rate"))


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


def _subject_id(
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


def _dataset_name(
    data_description_raw: Optional[dict] = None,
    core_raw: Optional[dict] = None,
) -> Optional[str]:
    """Dataset name — canonical from ``data_description.json``.

    Falls back to a ``dataset_name`` key on the core dict, which is how a
    minimal document supplies it. Note the precedence is the opposite of
    :func:`_subject_id`'s, which prefers the core file.

    Parameters
    ----------
    data_description_raw : dict, optional
        Raw ``data_description.json`` dict, or ``None`` when absent.
    core_raw : dict, optional
        Raw core metadata dict, used only as a fallback.

    Returns
    -------
    str or None
        The dataset name, or ``None`` if no source provides one.
    """
    name = (data_description_raw or {}).get("name")
    if name is None:
        name = (core_raw or {}).get("dataset_name")
    return None if name is None else str(name)


# ---------------------------------------------------------------------------
# Coercion and units
# ---------------------------------------------------------------------------

# An explicit table rather than a unit library: the set of units these
# documents carry is closed, and an unrecognised one must be visible.
_MICROMETERS_PER_UNIT = {
    "micrometer": 1.0,
    "micrometre": 1.0,
    "micron": 1.0,
    "microns": 1.0,
    "um": 1.0,
    "µm": 1.0,
    "nanometer": 1e-3,
    "nanometre": 1e-3,
    "nm": 1e-3,
    "millimeter": 1e3,
    "millimetre": 1e3,
    "mm": 1e3,
    "centimeter": 1e4,
    "centimetre": 1e4,
    "cm": 1e4,
    "meter": 1e6,
    "metre": 1e6,
    "m": 1e6,
}

# Real v1 assets spell fov_scale_factor_unit as "um/pixel"; the denominator
# is implied by the field, so strip it before lookup.
_PER_PIXEL_SUFFIXES = ("/pixel", "/pix", "/px", " per pixel")


def _as_float(value: Any) -> Optional[float]:
    """Coerce a raw metadata value to float, or return ``None``.

    Parameters
    ----------
    value : Any
        Raw value from the metadata dict; strings are common in v1.

    Returns
    -------
    float or None
        The coerced value, or ``None`` when absent or uncoercible.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        logger.warning("Ignoring non-numeric metadata value %r", value)
        return None


def _to_micrometers(
    value: Optional[float], unit: Optional[str]
) -> Optional[float]:
    """Convert a length to micrometres, or return ``None`` if it cannot be.

    An unrecognised unit warns and yields ``None`` rather than assuming
    micrometres, so a caller can refuse to default instead of being handed a
    value wrong by a factor of a thousand.

    Parameters
    ----------
    value : float or None
        The magnitude.
    unit : str or None
        The unit ``value`` is expressed in; ``None`` is read as micrometres,
        matching the schema default.

    Returns
    -------
    float or None
        The value in micrometres, or ``None``.
    """
    if value is None:
        return None
    if unit is None:
        return float(value)
    text = str(unit).strip().lower()
    for suffix in _PER_PIXEL_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    factor = _MICROMETERS_PER_UNIT.get(text)
    if factor is None:
        logger.warning(
            "Unrecognised length unit %r; not converting to micrometres.",
            unit,
        )
        return None
    return float(value) * factor


# ---------------------------------------------------------------------------
# FOV / plane naming
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> Optional[int]:
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


def _acronym_from_targeted_structure(value: Any) -> Optional[str]:
    """Extract a structure acronym from a v1/v2 ``targeted_structure`` value.

    The field type changed across schema versions, so tolerate all shapes:
    a bare acronym string, a dict carrying an ``"acronym"`` key, or absent.

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


def _pairs_from_records(
    records: List[Dict[str, Any]],
) -> List[Tuple[int, Optional[str]]]:
    """Reduce plane records to the ``(plane_index, acronym)`` pairs.

    Parameters
    ----------
    records : list of dict
        Plane records from a ``get_plane_records_*`` reader.

    Returns
    -------
    list of (int, str or None)
        Unsorted per-plane pairs.
    """
    return [(record["plane_index"], record["structure"]) for record in records]


def _deduplicate_plane_records(
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Collapse repeated descriptions of the same physical plane.

    Some single-plane v1 sessions store an ``ophys_fov`` on every epoch data
    stream. The v2 upgrader preserves those epoch-level descriptions as
    repeated ``PlanarImage``
    entries. Both layouts can therefore describe one physical plane more
    than once. Identical records are safe to collapse; conflicting records
    are not, because choosing one would hide a metadata inconsistency.

    Parameters
    ----------
    records : list of dict
        Normalized per-plane records.

    Returns
    -------
    list of dict
        One record per physical plane, retaining first-seen order.

    Raises
    ------
    ValueError
        If records with one plane index disagree.
    """
    unique: Dict[int, Dict[str, Any]] = {}
    for record in records:
        plane_index = record["plane_index"]
        if plane_index not in unique:
            unique[plane_index] = record
            continue
        if unique[plane_index] != record:
            raise ValueError(
                "Conflicting metadata for physical plane index "
                f"{plane_index}: repeated plane records disagree."
            )
    return list(unique.values())


# ---------------------------------------------------------------------------
# Per-plane records
# ---------------------------------------------------------------------------

# Plane records are plain dicts, so a new per-plane field is additive.
# Keys: plane_index, structure, depth, depth_unit, coupled_plane_index,
# scale_factor, scale_factor_unit, um_per_pixel, width, height.


def _plane_records_v1(session: dict) -> List[Dict[str, Any]]:
    """Rich per-plane records from a v1 ``session.json``.

    v1 layout: ``data_streams[*].ophys_fovs[*]``; each FOV may carry an
    ``index`` (defaults to 0 for single-plane) and a ``targeted_structure``.

    Parameters
    ----------
    session : dict
        Raw ``session.json`` dict.

    Returns
    -------
    list of dict
        Unsorted per-plane records.
    """
    records: List[Dict[str, Any]] = []
    for stream in session.get("data_streams") or []:
        for fov in stream.get("ophys_fovs") or []:
            unit = (
                fov.get("fov_coordinate_unit")
                or fov.get("fov_scale_factor_unit")
                or DEFAULT_LENGTH_UNIT
            )
            scale_factor = _as_float(fov.get("fov_scale_factor"))
            records.append(
                {
                    "plane_index": _as_int(fov.get("index")) or 0,
                    "structure": _acronym_from_targeted_structure(
                        fov.get("targeted_structure")
                    ),
                    "depth": _as_float(fov.get("imaging_depth")),
                    "depth_unit": fov.get("imaging_depth_unit")
                    or DEFAULT_LENGTH_UNIT,
                    "coupled_plane_index": _as_int(
                        fov.get("coupled_fov_index")
                    ),
                    "scale_factor": scale_factor,
                    "scale_factor_unit": unit,
                    "um_per_pixel": _to_micrometers(scale_factor, unit),
                    "width": _as_int(fov.get("fov_width")),
                    "height": _as_int(fov.get("fov_height")),
                }
            )
    return records


def _plane_records_v2(acquisition: dict) -> List[Dict[str, Any]]:
    """Rich per-plane records from a v2 ``acquisition.json``.

    Layout: ``data_streams[*].configurations[ImagingConfig].images[*]
    .planes[*]``.

    Parameters
    ----------
    acquisition : dict
        Raw ``acquisition.json`` dict.

    Returns
    -------
    list of dict
        Unsorted per-plane records.
    """
    records: List[Dict[str, Any]] = []
    for config in _imaging_configs_v2(acquisition):
        axis_unit = (config.get("coordinate_system") or {}).get("axis_unit")
        for image in config.get("images") or []:
            if image.get("object_type") != _V2_FOV_IMAGE_TYPE:
                continue
            dimensions = (image.get("dimensions") or {}).get("scale") or []
            scale_factor = _first_scale_v2(
                image.get("image_to_acquisition_transform")
            )
            unit = (
                axis_unit
                or image.get("dimensions_unit")
                or DEFAULT_LENGTH_UNIT
            )
            for plane in image.get("planes") or []:
                records.append(
                    {
                        "plane_index": _as_int(plane.get("plane_index")) or 0,
                        "structure": _acronym_from_targeted_structure(
                            plane.get("targeted_structure")
                        ),
                        "depth": _as_float(plane.get("depth")),
                        "depth_unit": plane.get("depth_unit")
                        or DEFAULT_LENGTH_UNIT,
                        "coupled_plane_index": _as_int(
                            plane.get("coupled_plane_index")
                        ),
                        "scale_factor": scale_factor,
                        "scale_factor_unit": unit,
                        "um_per_pixel": _to_micrometers(scale_factor, unit),
                        "width": (
                            _as_int(dimensions[0])
                            if len(dimensions) > 0
                            else None
                        ),
                        "height": (
                            _as_int(dimensions[1])
                            if len(dimensions) > 1
                            else None
                        ),
                    }
                )
    return records


def _plane_records_minimal(blob: dict) -> List[Dict[str, Any]]:
    """Rich per-plane records from a minimal ``metadata.json``.

    Each entry of ``planes`` carries the same key names the records use, so
    the read is a copy with coercion. ``plane_index`` defaults to the entry's
    position, and ``um_per_pixel`` is already in micrometres by definition.

    Parameters
    ----------
    blob : dict
        Raw ``metadata.json`` dict.

    Returns
    -------
    list of dict
        Per-plane records in document order.
    """
    records: List[Dict[str, Any]] = []
    for position, plane in enumerate(blob.get("planes") or []):
        um_per_pixel = _as_float(plane.get("um_per_pixel"))
        index = _as_int(plane.get("plane_index"))
        records.append(
            {
                "plane_index": position if index is None else index,
                "structure": plane.get("structure"),
                "depth": _as_float(plane.get("depth")),
                "depth_unit": plane.get("depth_unit") or DEFAULT_LENGTH_UNIT,
                "coupled_plane_index": _as_int(
                    plane.get("coupled_plane_index")
                ),
                "scale_factor": um_per_pixel,
                "scale_factor_unit": DEFAULT_LENGTH_UNIT,
                "um_per_pixel": um_per_pixel,
                "width": _as_int(plane.get("width")),
                "height": _as_int(plane.get("height")),
            }
        )
    return records


# ---------------------------------------------------------------------------
# v2 traversal helpers
# ---------------------------------------------------------------------------


def _imaging_configs_v2(acquisition: dict) -> List[dict]:
    """Return the v2 ``ImagingConfig`` dicts of an acquisition.

    Parameters
    ----------
    acquisition : dict
        Raw ``acquisition.json`` dict.

    Returns
    -------
    list of dict
        The imaging configuration dicts, in document order.
    """
    configs = []
    for stream in acquisition.get("data_streams") or []:
        for config in stream.get("configurations") or []:
            if config.get("object_type") == _V2_IMAGING_CONFIG_TYPE:
                configs.append(config)
    return configs


def _first_scale_v2(transforms: Any) -> Optional[float]:
    """First scale component of a v2 image-to-acquisition transform chain.

    Consumers need one isotropic number, so the first component wins.

    ``PlanarImage.dimensions`` is deliberately not read here: those are pixel
    dimensions, so reading them would silently yield e.g. 512.

    Parameters
    ----------
    transforms : Any
        Raw ``image_to_acquisition_transform`` list.

    Returns
    -------
    float or None
        The first scale component, or ``None`` when no ``Scale`` is present.
    """
    for transform in transforms or []:
        if not isinstance(transform, dict):
            continue
        if transform.get("object_type") != _V2_SCALE_TYPE:
            continue
        scale = transform.get("scale") or []
        if scale:
            return _as_float(scale[0])
    return None


# ---------------------------------------------------------------------------
# Wavelengths
# ---------------------------------------------------------------------------


def _excitation_wavelength_v1(session: dict) -> Optional[float]:
    """Excitation wavelength from v1 ``data_streams[*].light_sources``.

    Parameters
    ----------
    session : dict
        Raw ``session.json`` dict.

    Returns
    -------
    float or None
        The wavelength in nm, or ``None`` when no light source declares one.
    """
    for stream in session.get("data_streams") or []:
        for source in stream.get("light_sources") or []:
            wavelength = _as_float(source.get("wavelength"))
            if wavelength is not None:
                return wavelength
    return None


def _excitation_wavelength_v2(acquisition: dict) -> Optional[float]:
    """Excitation wavelength from the first v2 ``LaserConfig`` of a channel.

    Parameters
    ----------
    acquisition : dict
        Raw ``acquisition.json`` dict.

    Returns
    -------
    float or None
        The wavelength in nm, or ``None`` when no light source declares one.
    """
    for config in _imaging_configs_v2(acquisition):
        for channel in config.get("channels") or []:
            for source in channel.get("light_sources") or []:
                if source.get("object_type") not in (
                    None,
                    _V2_LASER_CONFIG_TYPE,
                ):
                    continue
                wavelength = _as_float(source.get("wavelength"))
                if wavelength is not None:
                    return wavelength
    return None


# ---------------------------------------------------------------------------
# The public surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoreMetadata:
    """A located core metadata document, and every read over it.

    Construct with :meth:`load`, or directly when the document is already in
    hand and there is no directory to search. Frozen but not hashable: it
    holds raw dicts.

    Attributes
    ----------
    core_path : Path
        Path to the located core metadata file.
    version : SchemaVersion
        Which schema the document is written in.
    core_raw : dict
        Raw core acquisition dict.
    input_dir : Path or None
        Directory the document was found under, named in error messages.
    platform_raw : dict or None
        Raw ``platform.json`` dict, or ``None`` when absent.
    subject_raw : dict or None
        Raw ``subject.json`` dict, or ``None`` when absent.
    data_description_raw : dict or None
        Raw ``data_description.json`` dict, or ``None`` when absent.
    """

    core_path: Path
    version: SchemaVersion
    core_raw: dict
    input_dir: Optional[Path] = None
    platform_raw: Optional[dict] = None
    subject_raw: Optional[dict] = None
    data_description_raw: Optional[dict] = None

    def __post_init__(self) -> None:
        """Coerce ``version`` to a :class:`SchemaVersion`.

        The one place an unrecognised version is rejected, which is what
        lets each method dispatch exhaustively. Coercing rather than checking
        lets a caller pass the plain string it already has.

        Raises
        ------
        ValueError
            If ``version`` is not a recognised schema version. Raised by
            :class:`SchemaVersion` itself.
        """
        object.__setattr__(self, "version", SchemaVersion(self.version))

    @classmethod
    def load(cls, input_dir: Path) -> "CoreMetadata":
        """Locate the core file under ``input_dir`` and load its siblings.

        Parameters
        ----------
        input_dir : Path
            Directory searched recursively for the metadata files.

        Returns
        -------
        CoreMetadata
            The located document and its optional siblings.

        Raises
        ------
        FileNotFoundError
            If no core file is present.
        ValueError
            If both v1 and v2 core files are present, or if a
            ``metadata.json`` turns out to be a whole-record export.
        """
        input_dir = Path(input_dir)
        core_path = io.find_core_file(input_dir)
        core_raw = io.load_json(core_path)
        if core_path.name == io.MINIMAL_CORE_FILE:
            io.reject_whole_record(core_raw)
        return cls(
            core_path=core_path,
            version=io.detect_schema_version(core_path),
            core_raw=core_raw,
            input_dir=input_dir,
            platform_raw=io.load_optional(input_dir, PLATFORM_FILE),
            subject_raw=io.load_optional(input_dir, io.SUBJECT_FILE),
            data_description_raw=io.load_optional(
                input_dir, io.DATA_DESCRIPTION_FILE
            ),
        )

    def get_frame_rate(
        self,
        *,
        cli_override: Optional[float] = None,
        required: bool = True,
    ) -> Optional[float]:
        """Frame rate in Hz, from the core file then ``platform.json``.

        The fallback ladder lives here so the whole fleet fails with one
        message rather than one reinvented per repo.

        Parameters
        ----------
        cli_override : float, optional
            Operator-supplied frame rate, used only when the metadata
            provides none.
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
            If ``required`` and no source supplies one, or if this document's
            schema version is unrecognised.
        """
        if self.version is SchemaVersion.V1:
            rate = _frame_rate_v1(self.core_raw)
        elif self.version is SchemaVersion.V2:
            rate = _frame_rate_v2(self.core_raw)
        elif self.version is SchemaVersion.MINIMAL:
            rate = _frame_rate_minimal(self.core_raw)
        else:
            raise ValueError(
                f"Unrecognised schema version {self.version!r}; "
                "cannot read frame rate."
            )
        if rate is None and self.platform_raw is not None:
            rate = _frame_rate_platform(self.platform_raw)
        if rate is None and cli_override is not None:
            logger.warning("Using CLI fallback frame rate: %s", cli_override)
            rate = cli_override
        if rate is None and required:
            raise ValueError(
                f"No frame rate found in {self.core_path.name} (schema "
                f"{self.version}) or {PLATFORM_FILE} under "
                f"{self.input_dir or 'the input directory'}, and no CLI "
                "override was supplied."
            )
        return None if rate is None else float(rate)

    def get_instrument_id(self, *, required: bool = False) -> Optional[str]:
        """Instrument/rig identifier (v1 ``rig_id``, else ``instrument_id``).

        Parameters
        ----------
        required : bool, optional
            Raise when the id is missing instead of returning
            ``None``. Capsules that cannot proceed without it pass ``True``.

        Returns
        -------
        str or None
            The instrument id, or ``None`` if absent and not ``required``.

        Raises
        ------
        ValueError
            If ``required`` and the id is missing.
        """
        key = "rig_id" if self.version is SchemaVersion.V1 else "instrument_id"
        if required:
            value = require(self.core_raw, key, self.core_path)
        else:
            value = self.core_raw.get(key)
        return None if value is None else str(value)

    def get_subject_id(self) -> Optional[str]:
        """Subject id; the core document is canonical.

        Returns
        -------
        str or None
            The subject id, or ``None`` if no source provides one.
        """
        return _subject_id(self.core_raw, self.subject_raw)

    def get_dataset_name(self) -> Optional[str]:
        """Dataset name; ``data_description.json`` is canonical.

        Returns
        -------
        str or None
            The dataset name, or ``None`` if no source provides one.
        """
        return _dataset_name(self.data_description_raw, self.core_raw)

    def get_plane_records(self) -> List[Dict[str, Any]]:
        """Per-plane records as plain dicts.

        Plain dicts rather than a dataclass, so a new per-plane field is an
        additive change to one reader instead of a change to a shared type
        plus every version's construction of it.

        Returns
        -------
        list of dict
            Unsorted unique records, keyed ``plane_index``, ``structure``,
            ``depth``, ``depth_unit``, ``coupled_plane_index``,
            ``scale_factor``, ``scale_factor_unit``, ``um_per_pixel``,
            ``width``, ``height``.
            Pair them with canonical ids by sorting on ``plane_index`` and
            zipping against :meth:`get_fov_ids`.

        Raises
        ------
        ValueError
            If this document's schema version is unrecognised, or repeated
            records for one physical plane conflict.
        """
        if self.version is SchemaVersion.V1:
            records = _plane_records_v1(self.core_raw)
        elif self.version is SchemaVersion.V2:
            records = _plane_records_v2(self.core_raw)
        elif self.version is SchemaVersion.MINIMAL:
            records = _plane_records_minimal(self.core_raw)
        else:
            raise ValueError(
                f"Unrecognised schema version {self.version!r}; "
                "cannot read plane records."
            )
        return _deduplicate_plane_records(records)

    def get_fov_ids(self) -> Tuple[str, ...]:
        """Canonical plane ids, ordered by plane index.

        The ids name output folders, so they must be identical across every
        capsule reading the same input.

        Returns
        -------
        tuple of str
            One id per plane; empty when the document lists no planes.

        Raises
        ------
        ValueError
            If this document's schema version is unrecognised.
        """
        return build_fov_ids(_pairs_from_records(self.get_plane_records()))

    def get_um_per_pixel(self) -> Optional[float]:
        """Micrometres per pixel for the acquisition.

        Read from the lowest-indexed plane, since the value is a property of
        the optical path and every plane of one acquisition shares it.

        Returns
        -------
        float or None
            Micrometres per pixel, or ``None`` when the document declares no
            scale or declares it in a unit that cannot be converted. A caller
            computing with the value should refuse to default rather than
            assume 1.0.

        Raises
        ------
        ValueError
            If this document's schema version is unrecognised.
        """
        records = self.get_plane_records()
        for record in sorted(records, key=lambda r: r["plane_index"]):
            if record["um_per_pixel"] is not None:
                return record["um_per_pixel"]
        return None

    def get_excitation_wavelength(self) -> Optional[float]:
        """Excitation wavelength in nm.

        Returns
        -------
        float or None
            The wavelength, or ``None`` when no light source declares one.

        Raises
        ------
        ValueError
            If this document's schema version is unrecognised.
        """
        if self.version is SchemaVersion.V1:
            return _excitation_wavelength_v1(self.core_raw)
        if self.version is SchemaVersion.V2:
            return _excitation_wavelength_v2(self.core_raw)
        return _as_float(self.core_raw.get("excitation_nm"))

    def get_emission_wavelength(self) -> Optional[float]:
        """Emission wavelength in nm.

        v1 has no per-channel emission wavelength, so this is ``None`` for v1
        input and a caller keeps whatever placeholder it used before.

        Returns
        -------
        float or None
            The wavelength, or ``None`` when unavailable.

        Raises
        ------
        ValueError
            If this document's schema version is unrecognised.
        """
        if self.version is SchemaVersion.V1:
            return None
        if self.version is SchemaVersion.MINIMAL:
            return _as_float(self.core_raw.get("emission_nm"))
        for config in _imaging_configs_v2(self.core_raw):
            for channel in config.get("channels") or []:
                wavelength = _as_float(channel.get("emission_wavelength"))
                if wavelength is not None:
                    return wavelength
        return None
