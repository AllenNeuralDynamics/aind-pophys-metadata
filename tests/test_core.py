"""Tests for aind_pophys_metadata.core."""

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from aind_data_schema.components.configs import (
    ImagingConfig,
    LaserConfig,
    PlanarImage,
)
from aind_data_schema.components.coordinates import Scale

from aind_pophys_metadata import core, io
from aind_pophys_metadata.core import CoreMetadata

_IMAGING = io.object_type_value(ImagingConfig)
_PLANAR = io.object_type_value(PlanarImage)
_SCALE = io.object_type_value(Scale)
_LASER = io.object_type_value(LaserConfig)

UNKNOWN_VERSION = "v3"


def _v1_session(
    frame_rate: Optional[float] = None,
    fovs: Optional[List[dict]] = None,
) -> dict:
    """Build a minimal v1 session dict with one data stream.

    Parameters
    ----------
    frame_rate : float, optional
        Frame rate declared by the single default FOV.
    fovs : list of dict, optional
        Explicit ``ophys_fovs`` entries, replacing the default FOV.

    Returns
    -------
    dict
        A raw ``session.json`` dict.
    """
    fov = {}
    if frame_rate is not None:
        fov["frame_rate"] = frame_rate
    return {"data_streams": [{"ophys_fovs": fovs or [fov]}]}


def _v2_acquisition(
    frame_rate: Optional[float] = None,
    planes: Optional[List[dict]] = None,
) -> dict:
    """Build a minimal v2 acquisition dict with one imaging config.

    Parameters
    ----------
    frame_rate : float, optional
        Frame rate declared by the imaging config's sampling strategy.
    planes : list of dict, optional
        Raw ``planes`` entries of the single planar image.

    Returns
    -------
    dict
        A raw ``acquisition.json`` dict.
    """
    sampling = {}
    if frame_rate is not None:
        sampling["frame_rate"] = frame_rate
    image = {"object_type": _PLANAR, "planes": planes or []}
    config = {
        "object_type": _IMAGING,
        "sampling_strategy": sampling,
        "images": [image],
    }
    return {"data_streams": [{"configurations": [config]}]}


def _v2_from_images(
    images: List[dict],
    coordinate_system: Optional[dict] = None,
    channels: Optional[List[dict]] = None,
) -> dict:
    """Build a v2 acquisition around explicit image and channel dicts.

    Parameters
    ----------
    images : list of dict
        Raw ``ImagingConfig.images`` entries.
    coordinate_system : dict, optional
        Raw ``ImagingConfig.coordinate_system`` dict.
    channels : list of dict, optional
        Raw ``ImagingConfig.channels`` entries.

    Returns
    -------
    dict
        A raw ``acquisition.json`` dict with one imaging config.
    """
    config: Dict[str, Any] = {
        "object_type": _IMAGING,
        "images": images,
        "channels": channels or [],
    }
    if coordinate_system is not None:
        config["coordinate_system"] = coordinate_system
    return {"data_streams": [{"configurations": [config]}]}


def _planar_image(
    planes: List[dict],
    dimensions: Optional[List[float]] = None,
    transform: Optional[List[dict]] = None,
    dimensions_unit: Optional[str] = None,
) -> dict:
    """Build a raw v2 ``PlanarImage`` dict.

    Parameters
    ----------
    planes : list of dict
        Raw ``planes`` entries.
    dimensions : list of float, optional
        ``dimensions.scale`` list (the image's pixel dimensions).
    transform : list of dict, optional
        Raw ``image_to_acquisition_transform`` list.
    dimensions_unit : str, optional
        Unit the image declares for its dimensions.

    Returns
    -------
    dict
        The raw image dict.
    """
    image: Dict[str, Any] = {"object_type": _PLANAR, "planes": planes}
    if dimensions is not None:
        image["dimensions"] = {"scale": dimensions}
    if transform is not None:
        image["image_to_acquisition_transform"] = transform
    if dimensions_unit is not None:
        image["dimensions_unit"] = dimensions_unit
    return image


def _scale_transform(*components: float) -> dict:
    """Build a raw v2 ``Scale`` transform entry.

    Parameters
    ----------
    *components : float
        Per-axis scale components.

    Returns
    -------
    dict
        The raw transform dict.
    """
    return {"object_type": _SCALE, "scale": list(components)}


def _direct(
    version: str,
    core_raw: dict,
    platform_raw: Optional[dict] = None,
    name: str = "acquisition.json",
) -> CoreMetadata:
    """Construct a :class:`CoreMetadata` from a bare document.

    Direct construction is a supported entry point for a caller holding a
    document and no directory (a DocDB record, a fixture).

    Parameters
    ----------
    version : str
        Schema version token the object should carry.
    core_raw : dict
        Raw core document.
    platform_raw : dict, optional
        Raw ``platform.json`` dict.
    name : str, optional
        File name to record as the core path.

    Returns
    -------
    CoreMetadata
        The constructed object.
    """
    return CoreMetadata(
        core_path=Path("/data") / name,
        version=version,
        core_raw=core_raw,
        input_dir=Path("/data"),
        platform_raw=platform_raw,
    )


class _CoreFileCase(unittest.TestCase):
    """Base case that loads method input through the real file path."""

    def setUp(self) -> None:
        """Create a temp input directory."""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def load(self, name: str, blob: dict) -> CoreMetadata:
        """Write a core file into the input dir and load it.

        Parameters
        ----------
        name : str
            Core file name, which is the authoritative version signal.
        blob : dict
            Document to write.

        Returns
        -------
        CoreMetadata
            The loaded core metadata.
        """
        (self.root / name).write_text(json.dumps(blob))
        return CoreMetadata.load(self.root)


class TestLoad(_CoreFileCase):
    """The find-core-file / detect-version / load-siblings preamble."""

    def _write(self, name: str, blob: dict) -> None:
        """Write a metadata file into the temp directory.

        Parameters
        ----------
        name : str
            File name.
        blob : dict
            Document to write.
        """
        (self.root / name).write_text(json.dumps(blob))

    def test_loads_v2_core_and_all_siblings(self) -> None:
        """Every optional sibling present is loaded."""
        self._write(io.V2_CORE_FILE, {"instrument_id": "MESO.1"})
        self._write(io.PLATFORM_FILE, {"imaging_plane_groups": []})
        self._write(io.SUBJECT_FILE, {"subject_id": "123"})
        self._write(io.DATA_DESCRIPTION_FILE, {"name": "ds"})
        loaded = CoreMetadata.load(self.root)
        self.assertEqual(loaded.version, io.SCHEMA_V2)
        self.assertEqual(loaded.core_path.name, io.V2_CORE_FILE)
        self.assertEqual(loaded.core_raw["instrument_id"], "MESO.1")
        self.assertEqual(loaded.subject_raw["subject_id"], "123")
        self.assertEqual(loaded.data_description_raw["name"], "ds")
        self.assertEqual(loaded.input_dir, self.root)
        self.assertIsNotNone(loaded.platform_raw)

    def test_absent_siblings_are_none(self) -> None:
        """A v1 asset with no siblings still loads."""
        loaded = self.load(io.V1_CORE_FILE, {"rig_id": "rig"})
        self.assertEqual(loaded.version, io.SCHEMA_V1)
        self.assertIsNone(loaded.platform_raw)
        self.assertIsNone(loaded.subject_raw)
        self.assertIsNone(loaded.data_description_raw)

    def test_reads_a_minimal_document(self) -> None:
        """metadata.json is detected as the minimal schema version."""
        loaded = self.load(io.MINIMAL_CORE_FILE, {"instrument_id": "MESO.1"})
        self.assertEqual(loaded.version, io.SCHEMA_MINIMAL)
        self.assertEqual(loaded.core_raw["instrument_id"], "MESO.1")

    def test_rejects_a_whole_record_export(self) -> None:
        """A metadata.json carrying a nested core document raises.

        The whole-record export shares the minimal document's filename, and
        reading it as a minimal document would find no fields rather than
        failing.
        """
        with self.assertRaises(ValueError) as ctx:
            self.load(
                io.MINIMAL_CORE_FILE, {"acquisition": {"instrument_id": "M"}}
            )
        self.assertIn("whole-record", str(ctx.exception))
        self.assertIn("acquisition.json", str(ctx.exception))

    def test_a_core_file_is_not_checked_for_nesting(self) -> None:
        """The whole-record guard applies to metadata.json only."""
        loaded = self.load(io.V2_CORE_FILE, {"acquisition": {"a": 1}})
        self.assertEqual(loaded.version, io.SCHEMA_V2)

    def test_missing_core_file_raises(self) -> None:
        """An input directory with no core file fails loudly."""
        with self.assertRaises(FileNotFoundError):
            CoreMetadata.load(self.root)

    def test_is_frozen(self) -> None:
        """The result is immutable; callers spread rather than mutate."""
        loaded = self.load(io.V1_CORE_FILE, {})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            loaded.version = io.SCHEMA_V2

    def test_direct_construction_needs_no_directory(self) -> None:
        """A caller holding only a document can still read fields."""
        held = CoreMetadata(
            core_path=Path("acquisition.json"),
            version=io.SCHEMA_V2,
            core_raw=_v2_acquisition(30.0),
        )
        self.assertIsNone(held.input_dir)
        self.assertEqual(held.get_frame_rate(), 30.0)


class TestFrameRate(unittest.TestCase):
    """Per-version frame-rate readers."""

    def test_v1(self) -> None:
        """v1 frame rate comes from the first ophys_fov."""
        self.assertEqual(core._frame_rate_v1(_v1_session(11.0)), 11.0)

    def test_v1_none(self) -> None:
        """v1 returns None when no FOV declares a frame rate."""
        self.assertIsNone(core._frame_rate_v1(_v1_session()))
        self.assertIsNone(core._frame_rate_v1({}))

    def test_v2(self) -> None:
        """v2 frame rate comes from the imaging config sampling strategy."""
        self.assertEqual(core._frame_rate_v2(_v2_acquisition(9.5)), 9.5)

    def test_v2_none(self) -> None:
        """v2 returns None when sampling strategy omits frame rate."""
        self.assertIsNone(core._frame_rate_v2(_v2_acquisition()))

    def test_v2_ignores_non_imaging_config(self) -> None:
        """A non-imaging configuration is skipped."""
        acq = {"data_streams": [{"configurations": [{"object_type": "x"}]}]}
        self.assertIsNone(core._frame_rate_v2(acq))

    def test_platform(self) -> None:
        """platform.json frame rate comes from imaging_plane_groups."""
        platform = {
            "imaging_plane_groups": [{"acquisition_framerate_Hz": 7.0}]
        }
        self.assertEqual(core._frame_rate_platform(platform), 7.0)
        self.assertIsNone(core._frame_rate_platform({}))

    def test_minimal_coerces(self) -> None:
        """The key is read and coerced from v1-style strings."""
        self.assertEqual(
            core._frame_rate_minimal({"frame_rate": "12.5"}), 12.5
        )

    def test_minimal_absent(self) -> None:
        """A document without the key yields None."""
        self.assertIsNone(core._frame_rate_minimal({}))


class TestGetFrameRate(_CoreFileCase):
    """The one frame-rate method: fallback ladder, override and raise."""

    def test_metadata_wins(self) -> None:
        """A frame rate in the core file is used as-is."""
        loaded = self.load(io.V1_CORE_FILE, _v1_session(30.0))
        self.assertEqual(loaded.get_frame_rate(), 30.0)

    def test_each_version_reads_its_own_layout(self) -> None:
        """The same method covers all three document shapes."""
        self.assertEqual(
            _direct(io.SCHEMA_V1, _v1_session(3.0)).get_frame_rate(), 3.0
        )
        self.assertEqual(
            _direct(io.SCHEMA_V2, _v2_acquisition(4.0)).get_frame_rate(), 4.0
        )
        self.assertEqual(
            _direct(io.SCHEMA_MINIMAL, {"frame_rate": 6.0}).get_frame_rate(),
            6.0,
        )

    def test_minimal_through_load(self) -> None:
        """A real metadata.json on disk dispatches to the minimal reader."""
        loaded = self.load(io.MINIMAL_CORE_FILE, {"frame_rate": 6.0})
        self.assertEqual(loaded.version, io.SCHEMA_MINIMAL)
        self.assertEqual(loaded.get_frame_rate(), 6.0)

    def test_platform_fallback(self) -> None:
        """platform.json supplies the rate when the core file omits it."""
        platform = {"imaging_plane_groups": [{"acquisition_framerate_Hz": 9}]}
        loaded = _direct(io.SCHEMA_V1, _v1_session(), platform)
        self.assertEqual(loaded.get_frame_rate(), 9.0)

    def test_platform_fallback_is_version_independent(self) -> None:
        """The minimal document gets the same fallback."""
        platform = {
            "imaging_plane_groups": [{"acquisition_framerate_Hz": 4.0}]
        }
        loaded = _direct(io.SCHEMA_MINIMAL, {}, platform)
        self.assertEqual(loaded.get_frame_rate(), 4.0)

    def test_cli_override_used_when_metadata_silent(self) -> None:
        """The CLI override fills in and is announced."""
        loaded = _direct(io.SCHEMA_V1, _v1_session())
        with self.assertLogs(core.logger, level="WARNING"):
            rate = loaded.get_frame_rate(cli_override=11.0)
        self.assertEqual(rate, 11.0)

    def test_cli_override_never_beats_metadata(self) -> None:
        """The override is a fallback, not an override of a real value."""
        loaded = _direct(io.SCHEMA_V1, _v1_session(30.0))
        with self.assertNoLogs(core.logger, level="WARNING"):
            self.assertEqual(loaded.get_frame_rate(cli_override=11.0), 30.0)

    def test_optional_miss_returns_none(self) -> None:
        """required=False yields None instead of raising."""
        loaded = _direct(io.SCHEMA_V1, _v1_session())
        self.assertIsNone(loaded.get_frame_rate(required=False))

    def test_required_miss_names_files_and_version(self) -> None:
        """The error names the core file, platform.json and the version."""
        loaded = _direct(io.SCHEMA_V2, _v2_acquisition())
        with self.assertRaises(ValueError) as ctx:
            loaded.get_frame_rate()
        message = str(ctx.exception)
        self.assertIn("acquisition.json", message)
        self.assertIn(io.PLATFORM_FILE, message)
        self.assertIn(io.SCHEMA_V2, message)
        self.assertIn("/data", message)

    def test_required_miss_without_an_input_dir_still_readable(self) -> None:
        """A directory-less object falls back to a generic label."""
        held = CoreMetadata(
            core_path=Path("session.json"),
            version=io.SCHEMA_V1,
            core_raw=_v1_session(),
        )
        with self.assertRaises(ValueError) as ctx:
            held.get_frame_rate()
        self.assertIn("the input directory", str(ctx.exception))


class TestIdentifiers(_CoreFileCase):
    """Instrument, subject, and dataset reads."""

    def test_instrument_v1(self) -> None:
        """v1 instrument id comes from rig_id."""
        loaded = _direct(io.SCHEMA_V1, {"rig_id": "MESO.1"})
        self.assertEqual(loaded.get_instrument_id(), "MESO.1")

    def test_instrument_v2(self) -> None:
        """v2 instrument id comes from instrument_id."""
        loaded = _direct(io.SCHEMA_V2, {"instrument_id": "MESO.2"})
        self.assertEqual(loaded.get_instrument_id(), "MESO.2")

    def test_instrument_minimal(self) -> None:
        """The minimal document spells it as v2 does."""
        loaded = self.load(io.MINIMAL_CORE_FILE, {"instrument_id": "BERG.1"})
        self.assertEqual(loaded.get_instrument_id(), "BERG.1")

    def test_instrument_missing(self) -> None:
        """Missing instrument id -> None."""
        self.assertIsNone(_direct(io.SCHEMA_V2, {}).get_instrument_id())

    def test_instrument_required_raises(self) -> None:
        """required=True raises ValueError naming the field and file."""
        loaded = _direct(io.SCHEMA_V2, {})
        with self.assertRaises(ValueError) as ctx:
            loaded.get_instrument_id(required=True)
        self.assertIn("instrument_id", str(ctx.exception))
        self.assertIn("acquisition.json", str(ctx.exception))

    def test_instrument_v1_required_error_names_rig_id(self) -> None:
        """The v1 error names the key v1 actually uses."""
        loaded = _direct(io.SCHEMA_V1, {}, name="session.json")
        with self.assertRaises(ValueError) as ctx:
            loaded.get_instrument_id(required=True)
        self.assertIn("rig_id", str(ctx.exception))

    def test_instrument_required_present(self) -> None:
        """required=True returns the value when present."""
        loaded = _direct(io.SCHEMA_V1, {"rig_id": "MESO.1"})
        self.assertEqual(loaded.get_instrument_id(required=True), "MESO.1")

    def test_subject_canonical(self) -> None:
        """subject_id is read from the core dict first."""
        self.assertEqual(core._subject_id({"subject_id": 12345}), "12345")

    def test_subject_fallback(self) -> None:
        """subject_id falls back to subject.json when core omits it."""
        self.assertEqual(core._subject_id({}, {"subject_id": "999"}), "999")

    def test_subject_none(self) -> None:
        """subject_id is None when no source provides it."""
        self.assertIsNone(core._subject_id({}))
        self.assertIsNone(core._subject_id({}, {}))

    def test_subject_method_prefers_the_core_document(self) -> None:
        """The method wires the core dict ahead of subject.json."""
        (self.root / io.V1_CORE_FILE).write_text(
            json.dumps({"subject_id": "canonical"})
        )
        (self.root / io.SUBJECT_FILE).write_text(
            json.dumps({"subject_id": "fallback"})
        )
        self.assertEqual(
            CoreMetadata.load(self.root).get_subject_id(), "canonical"
        )

    def test_subject_method_falls_back_to_the_sibling(self) -> None:
        """subject.json covers a core document that omits the id."""
        (self.root / io.V1_CORE_FILE).write_text(json.dumps({}))
        (self.root / io.SUBJECT_FILE).write_text(
            json.dumps({"subject_id": "999"})
        )
        self.assertEqual(CoreMetadata.load(self.root).get_subject_id(), "999")

    def test_dataset_name(self) -> None:
        """dataset name comes from data_description name."""
        self.assertEqual(
            core._dataset_name({"name": "ecephys_1"}), "ecephys_1"
        )
        self.assertIsNone(core._dataset_name(None))
        self.assertIsNone(core._dataset_name({}))

    def test_dataset_name_falls_back_to_the_core_dict(self) -> None:
        """A minimal document with no data_description supplies it itself."""
        self.assertEqual(
            core._dataset_name(None, {"dataset_name": "meso_1"}),
            "meso_1",
        )
        self.assertEqual(
            core._dataset_name({}, {"dataset_name": 772414}), "772414"
        )
        self.assertIsNone(core._dataset_name(None, {}))

    def test_data_description_wins_over_the_core_dict(self) -> None:
        """data_description.json is the authority on the asset name."""
        self.assertEqual(
            core._dataset_name(
                {"name": "canonical"}, {"dataset_name": "fallback"}
            ),
            "canonical",
        )

    def test_dataset_name_method_reads_the_sibling(self) -> None:
        """The method wires data_description.json ahead of the core dict."""
        (self.root / io.MINIMAL_CORE_FILE).write_text(
            json.dumps({"dataset_name": "fallback"})
        )
        (self.root / io.DATA_DESCRIPTION_FILE).write_text(
            json.dumps({"name": "canonical"})
        )
        self.assertEqual(
            CoreMetadata.load(self.root).get_dataset_name(), "canonical"
        )

    def test_dataset_name_method_falls_back_to_the_core_dict(self) -> None:
        """A minimal document with no sibling still names itself."""
        loaded = self.load(io.MINIMAL_CORE_FILE, {"dataset_name": "meso_1"})
        self.assertEqual(loaded.get_dataset_name(), "meso_1")


class TestFovPairs(unittest.TestCase):
    """Targeted-structure parsing and per-version pair reduction."""

    def test_acronym_shapes(self) -> None:
        """acronym parsing tolerates str, dict, None, and other."""
        self.assertIsNone(core._acronym_from_targeted_structure(None))
        self.assertEqual(core._acronym_from_targeted_structure("DLS"), "DLS")
        self.assertEqual(
            core._acronym_from_targeted_structure({"acronym": "VISp"}),
            "VISp",
        )
        self.assertIsNone(core._acronym_from_targeted_structure({"other": 1}))
        self.assertIsNone(core._acronym_from_targeted_structure(42))

    def test_pairs_from_records_drops_everything_else(self) -> None:
        """Only the index and the structure survive the reduction."""
        records = core._plane_records_v1(
            _v1_session(fovs=[{"index": 3, "imaging_depth": 100}])
        )
        self.assertEqual(core._pairs_from_records(records), [(3, None)])


class TestPlaneRecordDeduplication(unittest.TestCase):
    """Repeated epoch descriptions resolve to physical planes."""

    def test_v1_repeated_epoch_fovs_collapse(self) -> None:
        """Identical v1 FOVs on separate streams produce one plane."""
        fov = {
            "index": 0,
            "targeted_structure": "Primary Motor Cortex",
            "imaging_depth": 110,
        }
        session = _v1_session(fovs=[fov, dict(fov), dict(fov)])
        metadata = _direct(io.SCHEMA_V1, session)
        self.assertEqual(len(metadata.get_plane_records()), 1)
        self.assertEqual(metadata.get_fov_ids(), ("plane_0",))

    def test_distinct_plane_indices_remain_distinct(self) -> None:
        """Deduplication does not collapse true multiplane records."""
        session = _v1_session(
            fovs=[
                {"index": 0, "targeted_structure": "VISp"},
                {"index": 1, "targeted_structure": "VISp"},
            ]
        )
        metadata = _direct(io.SCHEMA_V1, session)
        self.assertEqual(
            [r["plane_index"] for r in metadata.get_plane_records()], [0, 1]
        )

    def test_minimal_repeated_planes_collapse(self) -> None:
        """Repeated minimal plane descriptions also produce one plane."""
        plane = {"plane_index": 0, "structure": "VISp", "depth": 150}
        metadata = _direct(
            io.SCHEMA_MINIMAL,
            {"planes": [plane, dict(plane)]},
        )
        self.assertEqual(len(metadata.get_plane_records()), 1)
        self.assertEqual(metadata.get_fov_ids(), ("plane_0",))

    def test_conflicting_records_raise(self) -> None:
        """A repeated index with different metadata fails loudly."""
        session = _v1_session(
            fovs=[
                {"index": 0, "imaging_depth": 100},
                {"index": 0, "imaging_depth": 110},
            ]
        )
        with self.assertRaisesRegex(ValueError, "plane index 0"):
            _direct(io.SCHEMA_V1, session).get_plane_records()


class TestGetFovIds(_CoreFileCase):
    """Canonical plane ids from every document shape."""

    def test_v1(self) -> None:
        """A single v1 plane is named for the single-plane scheme."""
        session = _v1_session(
            fovs=[{"index": 0, "targeted_structure": "VISp"}]
        )
        self.assertEqual(
            _direct(io.SCHEMA_V1, session).get_fov_ids(), ("plane_0",)
        )

    def test_v2(self) -> None:
        """The v2 layout yields the same id for the same acquisition."""
        acq = _v2_acquisition(
            planes=[{"plane_index": 0, "targeted_structure": "VISp"}]
        )
        self.assertEqual(
            _direct(io.SCHEMA_V2, acq).get_fov_ids(), ("plane_0",)
        )

    def test_minimal_through_load(self) -> None:
        """A minimal document on disk yields canonical multi-plane ids."""
        loaded = self.load(
            io.MINIMAL_CORE_FILE,
            {"planes": [{"structure": "VISl"}, {"structure": "VISp"}]},
        )
        self.assertEqual(loaded.get_fov_ids(), ("VISl_0", "VISp_1"))

    def test_no_planes_yields_no_ids(self) -> None:
        """A document listing no planes has no ids to build."""
        self.assertEqual(_direct(io.SCHEMA_V1, {}).get_fov_ids(), ())


class TestPlaneRecordsV2Filtering(unittest.TestCase):
    """Only planar images contribute planes."""

    PLANES = [{"plane_index": 0, "targeted_structure": "VISp"}]

    def test_a_non_planar_image_is_skipped(self) -> None:
        """An image of another object_type yields no planes."""
        acq = _v2_acquisition(30.0, planes=self.PLANES)
        images = acq["data_streams"][0]["configurations"][0]["images"]
        images[0] = dict(images[0], object_type="Some other image")
        self.assertEqual(core._plane_records_v2(acq), [])

    def test_the_same_image_as_planar_does_contribute(self) -> None:
        """The control: only object_type differs between the two cases."""
        acq = _v2_acquisition(30.0, planes=self.PLANES)
        self.assertEqual(len(core._plane_records_v2(acq)), 1)


class TestNullPlaneIndices(unittest.TestCase):
    """An explicit null index must not crash plane reading."""

    def test_v1_null_index_defaults_to_zero(self) -> None:
        """A v1 FOV with index: null is treated as plane 0."""
        session = _v1_session(
            fovs=[{"index": None, "targeted_structure": "VISp"}]
        )
        records = core._plane_records_v1(session)
        self.assertEqual([r["plane_index"] for r in records], [0])

    def test_v2_null_plane_index_defaults_to_zero(self) -> None:
        """A v2 plane with plane_index: null is treated as plane 0."""
        acq = _v2_acquisition(
            30.0, planes=[{"plane_index": None, "targeted_structure": "VISp"}]
        )
        records = core._plane_records_v2(acq)
        self.assertEqual([r["plane_index"] for r in records], [0])


class TestUnknownVersionRefusedAtConstruction(unittest.TestCase):
    """An unrecognised version cannot reach a read at all.

    Holding the version as a :class:`SchemaVersion` is what lets every method
    dispatch exhaustively. There used to be seven defensive branches, each an
    opportunity to write ``else`` and have it silently mean v2; the check is
    now one coercion, before any read happens.
    """

    def test_an_unknown_token_is_rejected(self) -> None:
        """Construction raises, naming the offending value."""
        with self.assertRaises(ValueError) as ctx:
            _direct(UNKNOWN_VERSION, {})
        self.assertIn(UNKNOWN_VERSION, str(ctx.exception))

    def test_a_v2_shaped_document_does_not_excuse_it(self) -> None:
        """A readable document is still refused under a bad version."""
        with self.assertRaises(ValueError):
            _direct(UNKNOWN_VERSION, _v2_acquisition(30.0))

    def test_a_plain_string_is_coerced(self) -> None:
        """A caller may pass the string it already has."""
        built = _direct("v1", {"rig_id": "MESO.1"})
        self.assertIs(built.version, io.SchemaVersion.V1)
        self.assertEqual(built.get_instrument_id(), "MESO.1")

    def test_a_member_is_accepted_unchanged(self) -> None:
        """Passing the member itself is the canonical form."""
        built = _direct(io.SchemaVersion.MINIMAL, {})
        self.assertIs(built.version, io.SchemaVersion.MINIMAL)

    def test_the_member_set_is_exactly_three(self) -> None:
        """Canary: adding a version means revisiting every dispatch.

        The methods dispatch exhaustively over these three, with the last
        branch handling MINIMAL rather than raising. A fourth member would
        silently take that branch, so this test fails the moment one is added.
        """
        self.assertEqual(
            set(io.SchemaVersion),
            {
                io.SchemaVersion.V1,
                io.SchemaVersion.V2,
                io.SchemaVersion.MINIMAL,
            },
        )


class TestAsInt(unittest.TestCase):
    """Safe int coercion over raw metadata values."""

    def test_coerces_int_float_and_string(self) -> None:
        """Numeric shapes produced by both v1 and v2 are accepted."""
        self.assertEqual(core._as_int(3), 3)
        self.assertEqual(core._as_int(3.7), 3)
        self.assertEqual(core._as_int("4"), 4)

    def test_none_is_not_an_error(self) -> None:
        """An explicit JSON null yields None rather than TypeError."""
        self.assertIsNone(core._as_int(None))

    def test_uncoercible_warns_and_returns_none(self) -> None:
        """A non-numeric value is reported and dropped."""
        with self.assertLogs(core.logger, level="WARNING"):
            self.assertIsNone(core._as_int("abc"))


class TestAsFloat(unittest.TestCase):
    """Safe float coercion over raw metadata values."""

    def test_coerces_numeric_strings(self) -> None:
        """v1 assets carry numbers as strings."""
        self.assertEqual(core._as_float("1.5"), 1.5)
        self.assertEqual(core._as_float(2), 2.0)

    def test_none_is_not_an_error(self) -> None:
        """An explicit JSON null yields None rather than TypeError."""
        self.assertIsNone(core._as_float(None))

    def test_uncoercible_warns_and_returns_none(self) -> None:
        """A non-numeric value is reported and dropped."""
        with self.assertLogs(core.logger, level="WARNING") as logs:
            self.assertIsNone(core._as_float("abc"))
        self.assertIn("abc", logs.output[0])


class TestToMicrometers(unittest.TestCase):
    """Length conversion, including the per-pixel spellings v1 carries."""

    def test_none_value(self) -> None:
        """No magnitude means no result, whatever the unit."""
        self.assertIsNone(core._to_micrometers(None, "mm"))

    def test_none_unit_is_micrometres(self) -> None:
        """An absent unit matches the schema default of micrometres."""
        self.assertEqual(core._to_micrometers(1.5, None), 1.5)

    def test_micrometre_aliases(self) -> None:
        """Every spelling the documents use resolves to a factor of one."""
        for unit in ("micrometer", "micrometre", "micron", "microns", "um"):
            with self.subTest(unit=unit):
                self.assertEqual(core._to_micrometers(2.0, unit), 2.0)

    def test_millimetre_conversion_changes_the_number(self) -> None:
        """A real conversion is applied, not assumed to be a no-op."""
        self.assertEqual(core._to_micrometers(0.5, "mm"), 500.0)

    def test_per_pixel_spellings(self) -> None:
        """'um/pixel' is what real v1 assets carry, so it must convert."""
        for unit in (
            "um/pixel",
            "um/px",
            "micron per pixel",
            "  UM/Pixel  ",
        ):
            with self.subTest(unit=unit):
                self.assertEqual(core._to_micrometers(0.8, unit), 0.8)

    def test_per_pixel_spelling_with_conversion(self) -> None:
        """The denominator is stripped before the factor is applied."""
        self.assertEqual(core._to_micrometers(0.001, "mm/pixel"), 1.0)

    def test_unrecognised_unit_warns_and_drops_the_value(self) -> None:
        """An unknown unit is never passed through as micrometres."""
        with self.assertLogs(core.logger, level="WARNING") as logs:
            self.assertIsNone(core._to_micrometers(3.0, "furlongs"))
        self.assertIn("furlongs", logs.output[0])


class TestPlaneRecordsV1(unittest.TestCase):
    """Rich per-plane records from a v1 session."""

    def test_full_fov(self) -> None:
        """Every v1 FOV field maps onto its record key."""
        session = _v1_session(
            fovs=[
                {
                    "index": 2,
                    "targeted_structure": {"acronym": "VISp"},
                    "imaging_depth": "150",
                    "imaging_depth_unit": "micrometer",
                    "coupled_fov_index": 3,
                    "fov_scale_factor": 0.78,
                    "fov_scale_factor_unit": "um/pixel",
                    "fov_width": 512,
                    "fov_height": 512,
                }
            ]
        )
        (record,) = core._plane_records_v1(session)
        self.assertEqual(
            record,
            {
                "plane_index": 2,
                "structure": "VISp",
                "depth": 150.0,
                "depth_unit": "micrometer",
                "coupled_plane_index": 3,
                "scale_factor": 0.78,
                "scale_factor_unit": "um/pixel",
                "um_per_pixel": 0.78,
                "width": 512,
                "height": 512,
            },
        )

    def test_bare_structure_string_and_missing_index(self) -> None:
        """An early-v1 acronym string and an index-less FOV both read."""
        session = _v1_session(fovs=[{"targeted_structure": "DLS"}])
        (record,) = core._plane_records_v1(session)
        self.assertEqual(record["plane_index"], 0)
        self.assertEqual(record["structure"], "DLS")

    def test_coordinate_unit_wins_over_scale_factor_unit(self) -> None:
        """fov_coordinate_unit is the richer of the two v1 spellings."""
        session = _v1_session(
            fovs=[
                {
                    "index": 0,
                    "fov_scale_factor": 0.5,
                    "fov_coordinate_unit": "mm",
                    "fov_scale_factor_unit": "um/pixel",
                }
            ]
        )
        (record,) = core._plane_records_v1(session)
        self.assertEqual(record["scale_factor_unit"], "mm")
        self.assertEqual(record["um_per_pixel"], 500.0)

    def test_missing_geometry_stays_none(self) -> None:
        """Absent depth and dimensions are None, never a guessed default."""
        session = _v1_session(fovs=[{"index": 0}])
        (record,) = core._plane_records_v1(session)
        for key in ("depth", "width", "height", "um_per_pixel"):
            with self.subTest(key=key):
                self.assertIsNone(record[key])
        self.assertEqual(record["depth_unit"], core.DEFAULT_LENGTH_UNIT)


class TestPlaneRecordsV2(unittest.TestCase):
    """Rich per-plane records from a v2 acquisition."""

    def test_width_and_height_come_from_dimensions_scale(self) -> None:
        """PlanarImage.dimensions supplies the pixel dimensions."""
        acq = _v2_from_images(
            [_planar_image([{"plane_index": 0}], dimensions=[512, 480])]
        )
        (record,) = core._plane_records_v2(acq)
        self.assertEqual((record["width"], record["height"]), (512, 480))

    def test_short_and_absent_dimensions_stay_none(self) -> None:
        """A one-entry or absent scale list does not index out of range."""
        acq = _v2_from_images(
            [
                _planar_image([{"plane_index": 0}], dimensions=[512]),
                _planar_image([{"plane_index": 1}]),
            ]
        )
        short, absent = core._plane_records_v2(acq)
        self.assertEqual(short["width"], 512)
        self.assertIsNone(short["height"])
        self.assertIsNone(absent["width"])
        self.assertIsNone(absent["height"])

    def test_axis_unit_wins_over_dimensions_unit(self) -> None:
        """The imaging config's coordinate system is the better source."""
        acq = _v2_from_images(
            [
                _planar_image(
                    [{"plane_index": 0}],
                    transform=[_scale_transform(0.002)],
                    dimensions_unit="meter",
                )
            ],
            coordinate_system={"axis_unit": "millimeter"},
        )
        (record,) = core._plane_records_v2(acq)
        self.assertEqual(record["scale_factor_unit"], "millimeter")
        self.assertEqual(record["um_per_pixel"], 2.0)

    def test_dimensions_unit_used_when_no_coordinate_system(self) -> None:
        """The image's own unit is the fallback, then micrometres."""
        acq = _v2_from_images(
            [
                _planar_image(
                    [{"plane_index": 0}],
                    transform=[_scale_transform(0.5)],
                    dimensions_unit="um/pixel",
                ),
            ]
        )
        (record,) = core._plane_records_v2(acq)
        self.assertEqual(record["scale_factor_unit"], "um/pixel")
        self.assertEqual(record["um_per_pixel"], 0.5)

    def test_default_unit_when_the_document_names_none(self) -> None:
        """Neither spelling present means micrometres, per the schema."""
        acq = _v2_from_images(
            [
                _planar_image(
                    [{"plane_index": 0}], transform=[_scale_transform(0.7)]
                )
            ]
        )
        (record,) = core._plane_records_v2(acq)
        self.assertEqual(record["scale_factor_unit"], core.DEFAULT_LENGTH_UNIT)
        self.assertEqual(record["um_per_pixel"], 0.7)

    def test_plane_fields(self) -> None:
        """Depth and coupling read from the plane, not the image."""
        acq = _v2_from_images(
            [
                _planar_image(
                    [
                        {
                            "plane_index": 1,
                            "targeted_structure": {"acronym": "VISl"},
                            "depth": 200,
                            "depth_unit": "micrometer",
                            "coupled_plane_index": 0,
                        }
                    ]
                )
            ]
        )
        (record,) = core._plane_records_v2(acq)
        self.assertEqual(record["structure"], "VISl")
        self.assertEqual(record["depth"], 200.0)
        self.assertEqual(record["coupled_plane_index"], 0)
        self.assertEqual(record["depth_unit"], "micrometer")

    def test_plane_without_a_depth_unit_gets_the_default(self) -> None:
        """A plane that omits the unit is read as micrometres."""
        acq = _v2_from_images([_planar_image([{"plane_index": 0}])])
        (record,) = core._plane_records_v2(acq)
        self.assertEqual(record["depth_unit"], core.DEFAULT_LENGTH_UNIT)


class TestPlaneRecordsMinimal(_CoreFileCase):
    """Rich per-plane records from a minimal document."""

    def test_plane_index_defaults_to_document_position(self) -> None:
        """A minimal plane without an index is identified by position."""
        loaded = self.load(
            io.MINIMAL_CORE_FILE,
            {"planes": [{"structure": "VISp"}, {"structure": "VISl"}]},
        )
        records = core._plane_records_minimal(loaded.core_raw)
        self.assertEqual([r["plane_index"] for r in records], [0, 1])

    def test_explicit_index_wins_over_position(self) -> None:
        """A stated plane_index is preserved, including zero."""
        records = core._plane_records_minimal(
            {"planes": [{"plane_index": 4}, {"plane_index": 0}]}
        )
        self.assertEqual([r["plane_index"] for r in records], [4, 0])

    def test_um_per_pixel_is_already_micrometres(self) -> None:
        """The minimal document states the scale in micrometres by name."""
        (record,) = core._plane_records_minimal(
            {
                "planes": [
                    {
                        "plane_index": 0,
                        "structure": "VISp",
                        "depth": "100",
                        "coupled_plane_index": 1,
                        "um_per_pixel": "0.9",
                        "width": 512,
                        "height": 512,
                    }
                ]
            }
        )
        self.assertEqual(record["um_per_pixel"], 0.9)
        self.assertEqual(record["scale_factor"], 0.9)
        self.assertEqual(record["scale_factor_unit"], core.DEFAULT_LENGTH_UNIT)
        self.assertEqual(record["depth"], 100.0)
        self.assertEqual(record["depth_unit"], core.DEFAULT_LENGTH_UNIT)
        self.assertEqual((record["width"], record["height"]), (512, 512))

    def test_explicit_depth_unit_is_kept(self) -> None:
        """A minimal plane may name its own depth unit."""
        (record,) = core._plane_records_minimal(
            {"planes": [{"depth": 1, "depth_unit": "mm"}]}
        )
        self.assertEqual(record["depth_unit"], "mm")


class TestGetPlaneRecords(_CoreFileCase):
    """The plane-records method over all three document shapes."""

    def test_v1(self) -> None:
        """A v1 session's FOV becomes a record."""
        loaded = self.load(io.V1_CORE_FILE, _v1_session(fovs=[{"index": 7}]))
        self.assertEqual(loaded.get_plane_records()[0]["plane_index"], 7)

    def test_v2(self) -> None:
        """A v2 acquisition's plane becomes the same shape of record."""
        loaded = self.load(
            io.V2_CORE_FILE, _v2_acquisition(planes=[{"plane_index": 8}])
        )
        self.assertEqual(loaded.get_plane_records()[0]["plane_index"], 8)

    def test_minimal(self) -> None:
        """A minimal document's plane becomes the same shape of record."""
        loaded = self.load(
            io.MINIMAL_CORE_FILE, {"planes": [{"plane_index": 9}]}
        )
        self.assertEqual(loaded.get_plane_records()[0]["plane_index"], 9)

    def test_the_same_logical_plane_reads_alike_from_all_three(self) -> None:
        """One plane, three document shapes, one record."""
        expected = {
            "plane_index": 0,
            "structure": "VISp",
            "depth": 150.0,
            "depth_unit": "micrometer",
            "coupled_plane_index": 1,
            "scale_factor": 0.8,
            "scale_factor_unit": "micrometer",
            "um_per_pixel": 0.8,
            "width": 512,
            "height": 480,
        }
        documents = {
            io.SCHEMA_V1: _v1_session(
                fovs=[
                    {
                        "index": 0,
                        "targeted_structure": "VISp",
                        "imaging_depth": 150,
                        "coupled_fov_index": 1,
                        "fov_scale_factor": 0.8,
                        "fov_width": 512,
                        "fov_height": 480,
                    }
                ]
            ),
            io.SCHEMA_V2: _v2_from_images(
                [
                    _planar_image(
                        [
                            {
                                "plane_index": 0,
                                "targeted_structure": {"acronym": "VISp"},
                                "depth": 150,
                                "coupled_plane_index": 1,
                            }
                        ],
                        dimensions=[512, 480],
                        transform=[_scale_transform(0.8)],
                    )
                ]
            ),
            io.SCHEMA_MINIMAL: {
                "planes": [
                    {
                        "plane_index": 0,
                        "structure": "VISp",
                        "depth": 150,
                        "coupled_plane_index": 1,
                        "um_per_pixel": 0.8,
                        "width": 512,
                        "height": 480,
                    }
                ]
            },
        }
        for version, document in documents.items():
            with self.subTest(version=version):
                loaded = _direct(version, document)
                self.assertEqual(loaded.get_plane_records(), [expected])
                self.assertEqual(loaded.get_fov_ids(), ("plane_0",))
                self.assertEqual(loaded.get_um_per_pixel(), 0.8)


class TestFirstScaleV2(unittest.TestCase):
    """The v2 replacement for v1's scalar fov_scale_factor."""

    def test_first_component_wins(self) -> None:
        """Consumers need one isotropic number."""
        self.assertEqual(
            core._first_scale_v2([_scale_transform(0.78, 0.78)]), 0.78
        )

    def test_dimensions_are_never_read_as_a_scale(self) -> None:
        """A 512-pixel image with no transform yields None, not 512."""
        acq = _v2_from_images(
            [_planar_image([{"plane_index": 0}], dimensions=[512, 512])]
        )
        (record,) = core._plane_records_v2(acq)
        self.assertIsNone(record["scale_factor"])
        self.assertIsNone(record["um_per_pixel"])
        self.assertEqual(record["width"], 512)

    def test_no_transforms(self) -> None:
        """An absent or empty transform chain yields None."""
        self.assertIsNone(core._first_scale_v2(None))
        self.assertIsNone(core._first_scale_v2([]))

    def test_non_dict_entry_is_skipped(self) -> None:
        """A malformed entry does not stop the scan."""
        self.assertIsNone(core._first_scale_v2(["translation"]))
        self.assertEqual(
            core._first_scale_v2(["translation", _scale_transform(1.25)]),
            1.25,
        )

    def test_non_scale_transform_is_skipped(self) -> None:
        """Only the Scale entry of the chain carries the scale factor."""
        self.assertIsNone(
            core._first_scale_v2([{"object_type": "Translation"}])
        )

    def test_empty_scale_list_is_skipped(self) -> None:
        """A Scale with no components has nothing to return."""
        self.assertIsNone(core._first_scale_v2([_scale_transform()]))


class TestUmPerPixel(unittest.TestCase):
    """The acquisition-level micrometres-per-pixel read."""

    def test_v1_reads_the_lowest_plane_index_not_document_order(self) -> None:
        """Planes are listed out of order in real v1 assets."""
        session = _v1_session(
            fovs=[
                {"index": 2, "fov_scale_factor": 2.0},
                {"index": 0, "fov_scale_factor": 0.5},
                {"index": 1, "fov_scale_factor": 1.0},
            ]
        )
        self.assertEqual(
            _direct(io.SCHEMA_V1, session).get_um_per_pixel(), 0.5
        )

    def test_v2_reads_the_lowest_plane_index(self) -> None:
        """The same ordering rule holds for the v2 layout."""
        acq = _v2_from_images(
            [
                _planar_image(
                    [{"plane_index": 3}], transform=[_scale_transform(3.0)]
                ),
                _planar_image(
                    [{"plane_index": 1}], transform=[_scale_transform(1.0)]
                ),
            ]
        )
        self.assertEqual(_direct(io.SCHEMA_V2, acq).get_um_per_pixel(), 1.0)

    def test_minimal(self) -> None:
        """The minimal document's per-plane value is used directly."""
        blob = {
            "planes": [
                {"plane_index": 1, "um_per_pixel": 1.5},
                {"plane_index": 0, "um_per_pixel": 0.75},
            ]
        }
        self.assertEqual(
            _direct(io.SCHEMA_MINIMAL, blob).get_um_per_pixel(), 0.75
        )

    def test_a_plane_without_a_scale_is_skipped(self) -> None:
        """The lowest plane that declares one wins, not the lowest plane."""
        blob = {
            "planes": [
                {"plane_index": 0},
                {"plane_index": 1, "um_per_pixel": 1.25},
            ]
        }
        self.assertEqual(
            _direct(io.SCHEMA_MINIMAL, blob).get_um_per_pixel(), 1.25
        )

    def test_unconvertible_scale_yields_none(self) -> None:
        """A unit that cannot be converted is not defaulted to 1.0."""
        session = _v1_session(
            fovs=[
                {
                    "index": 0,
                    "fov_scale_factor": 0.8,
                    "fov_scale_factor_unit": "furlongs",
                }
            ]
        )
        with self.assertLogs(core.logger, level="WARNING"):
            self.assertIsNone(
                _direct(io.SCHEMA_V1, session).get_um_per_pixel()
            )

    def test_no_planes_yields_none(self) -> None:
        """A document listing no planes has no scale to report."""
        self.assertIsNone(_direct(io.SCHEMA_V1, {}).get_um_per_pixel())


class TestImagingConfigsV2(unittest.TestCase):
    """v2 configuration traversal."""

    def test_non_imaging_configurations_are_skipped(self) -> None:
        """A stream mixes imaging with other configuration types."""
        acq = {
            "data_streams": [
                {
                    "configurations": [
                        {"object_type": "Detector config"},
                        {"object_type": _IMAGING, "images": []},
                    ]
                }
            ]
        }
        configs = core._imaging_configs_v2(acq)
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["object_type"], _IMAGING)

    def test_no_streams(self) -> None:
        """A document with no data streams has no imaging configs."""
        self.assertEqual(core._imaging_configs_v2({}), [])


class TestExcitationWavelength(_CoreFileCase):
    """Excitation wavelength across the three versions."""

    def test_v1_light_sources(self) -> None:
        """v1 reads the first data stream light source with a wavelength."""
        session = {
            "data_streams": [
                {"light_sources": [{}, {"wavelength": "920"}]},
            ]
        }
        self.assertEqual(core._excitation_wavelength_v1(session), 920.0)

    def test_v1_none(self) -> None:
        """No light source declaring a wavelength yields None."""
        self.assertIsNone(core._excitation_wavelength_v1({}))

    def test_v2_laser_config_under_a_channel(self) -> None:
        """v2 reads the channel's LaserConfig."""
        acq = _v2_from_images(
            [],
            channels=[
                {"light_sources": [{"object_type": _LASER, "wavelength": 910}]}
            ],
        )
        self.assertEqual(core._excitation_wavelength_v2(acq), 910.0)

    def test_v2_missing_object_type_is_treated_as_a_laser(self) -> None:
        """An untyped light source is read rather than skipped."""
        acq = _v2_from_images(
            [], channels=[{"light_sources": [{"wavelength": 940}]}]
        )
        self.assertEqual(core._excitation_wavelength_v2(acq), 940.0)

    def test_v2_other_config_type_is_skipped(self) -> None:
        """A non-laser light source is not read for excitation."""
        acq = _v2_from_images(
            [],
            channels=[
                {
                    "light_sources": [
                        {
                            "object_type": "Light emitting diode config",
                            "wavelength": 470,
                        },
                        {"object_type": _LASER, "wavelength": 920},
                    ]
                }
            ],
        )
        self.assertEqual(core._excitation_wavelength_v2(acq), 920.0)

    def test_v2_none(self) -> None:
        """A channel with no light sources yields None."""
        self.assertIsNone(core._excitation_wavelength_v2(_v2_from_images([])))

    def test_method_reads_all_three_versions(self) -> None:
        """Each version reads its own spelling through one method."""
        session = {"data_streams": [{"light_sources": [{"wavelength": 920}]}]}
        self.assertEqual(
            _direct(io.SCHEMA_V1, session).get_excitation_wavelength(), 920.0
        )
        acq = _v2_from_images(
            [],
            channels=[
                {"light_sources": [{"object_type": _LASER, "wavelength": 910}]}
            ],
        )
        self.assertEqual(
            _direct(io.SCHEMA_V2, acq).get_excitation_wavelength(), 910.0
        )
        loaded = self.load(io.MINIMAL_CORE_FILE, {"excitation_nm": "900"})
        self.assertEqual(loaded.get_excitation_wavelength(), 900.0)


class TestEmissionWavelength(_CoreFileCase):
    """Emission wavelength, which v1 simply does not record."""

    def test_v1_is_none_by_design(self) -> None:
        """v1 has no per-channel emission wavelength to read."""
        acq_shaped = _v2_from_images(
            [], channels=[{"emission_wavelength": 520}]
        )
        self.assertIsNone(
            _direct(io.SCHEMA_V1, acq_shaped).get_emission_wavelength()
        )

    def test_v2_reads_the_channel(self) -> None:
        """v2 reads channels[*].emission_wavelength."""
        acq = _v2_from_images(
            [], channels=[{}, {"emission_wavelength": "520"}]
        )
        self.assertEqual(
            _direct(io.SCHEMA_V2, acq).get_emission_wavelength(), 520.0
        )

    def test_v2_none(self) -> None:
        """No channel declaring one yields None."""
        self.assertIsNone(
            _direct(
                io.SCHEMA_V2, _v2_from_images([])
            ).get_emission_wavelength()
        )

    def test_minimal(self) -> None:
        """The minimal document spells it emission_nm."""
        loaded = self.load(io.MINIMAL_CORE_FILE, {"emission_nm": 515})
        self.assertEqual(loaded.get_emission_wavelength(), 515.0)


if __name__ == "__main__":
    unittest.main()
