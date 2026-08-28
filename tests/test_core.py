"""Tests for aind_pophys_metadata.core."""

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional

from aind_data_schema.components.configs import ImagingConfig, PlanarImage

from aind_pophys_metadata import core, io
from aind_pophys_metadata.core import CoreMetadata

_IMAGING = io.object_type_value(ImagingConfig)
_PLANAR = io.object_type_value(PlanarImage)

UNKNOWN_VERSION = "v3"


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


if __name__ == "__main__":
    unittest.main()
