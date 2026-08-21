"""The CoreMetadata class and schema-agnostic readers"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from aind_data_schema.components.configs import ImagingConfig

from aind_pophys_metadata import io
from aind_pophys_metadata.io import (
    PLATFORM_FILE,
    SchemaVersion,
    object_type_value,
    require,
)

logger = logging.getLogger(__name__)

# v2 discriminator values, read from the schema classes (not hardcoded) so
# they track the installed aind-data-schema version.
_V2_IMAGING_CONFIG_TYPE = object_type_value(ImagingConfig)

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
        else:
            rate = _frame_rate_minimal(self.core_raw)
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
