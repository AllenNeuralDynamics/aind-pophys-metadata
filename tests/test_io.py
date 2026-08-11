"""Tests for aind_pophys_metadata.io."""

import json
import tempfile
import unittest
from pathlib import Path

from aind_data_schema.components.configs import ImagingConfig

from aind_pophys_metadata import io


class TestIo(unittest.TestCase):
    """File discovery, JSON reads, and schema-version dispatch."""

    def setUp(self):
        """Create a temp working directory."""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        """Clean up the temp directory."""
        self._tmp.cleanup()

    def _write(self, rel: str, obj: dict) -> Path:
        """Write ``obj`` as JSON to ``root/rel`` and return the path."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))
        return path

    def test_load_json(self):
        """load_json round-trips a dict."""
        path = self._write("a.json", {"k": 1})
        self.assertEqual(io.load_json(path), {"k": 1})

    def test_find_hit_and_miss(self):
        """find returns a nested match, or None when absent."""
        self._write("sub/dir/target.json", {})
        self.assertIsNotNone(io.find(self.root, "target.json"))
        self.assertIsNone(io.find(self.root, "missing.json"))

    def test_require_present(self):
        """require returns the value when the key exists."""
        self.assertEqual(io.require({"x": 5}, "x", Path("f.json")), 5)

    def test_require_missing_raises(self):
        """require raises KeyError naming the field and file."""
        with self.assertRaises(KeyError) as ctx:
            io.require({}, "x", Path("f.json"))
        self.assertIn("x", str(ctx.exception))
        self.assertIn("f.json", str(ctx.exception))

    def test_find_acquisition_prefers_v2(self):
        """Only acquisition.json present -> returns it."""
        self._write("acquisition.json", {})
        self.assertEqual(
            io.find_acquisition_file(self.root).name, "acquisition.json"
        )

    def test_find_acquisition_v1_fallback(self):
        """Only session.json present -> returns it."""
        self._write("session.json", {})
        self.assertEqual(
            io.find_acquisition_file(self.root).name, "session.json"
        )

    def test_find_acquisition_ambiguous_raises(self):
        """Both files present -> ValueError."""
        self._write("acquisition.json", {})
        self._write("session.json", {})
        with self.assertRaises(ValueError):
            io.find_acquisition_file(self.root)

    def test_find_acquisition_missing_raises(self):
        """Neither file present -> FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            io.find_acquisition_file(self.root)

    def test_detect_schema_version(self):
        """session.json -> v1, acquisition.json -> v2."""
        self.assertEqual(
            io.detect_schema_version(Path("x/session.json")), io.SCHEMA_V1
        )
        self.assertEqual(
            io.detect_schema_version(Path("x/acquisition.json")),
            io.SCHEMA_V2,
        )

    def test_object_type_value(self):
        """object_type_value reads a non-empty discriminator default."""
        value = io.object_type_value(ImagingConfig)
        self.assertIsInstance(value, str)
        self.assertTrue(value)


class TestLoadCommon(unittest.TestCase):
    """The shared find-core-file / detect-version / load-siblings preamble."""

    def setUp(self):
        """Create a temp working directory."""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name: str, blob: dict) -> None:
        """Write a metadata file into the temp directory."""
        (self.root / name).write_text(json.dumps(blob))

    def test_loads_v2_core_and_all_siblings(self):
        """Every optional sibling present is loaded."""
        self._write(io.V2_CORE_FILE, {"instrument_id": "MESO.1"})
        self._write(io.PLATFORM_FILE, {"imaging_plane_groups": []})
        self._write(io.SUBJECT_FILE, {"subject_id": "123"})
        self._write(io.DATA_DESCRIPTION_FILE, {"name": "ds"})
        common = io.load_common(self.root)
        self.assertEqual(common.version, io.SCHEMA_V2)
        self.assertEqual(common.core_path.name, io.V2_CORE_FILE)
        self.assertEqual(common.core_raw["instrument_id"], "MESO.1")
        self.assertEqual(common.subject_raw["subject_id"], "123")
        self.assertEqual(common.data_description_raw["name"], "ds")
        self.assertIsNotNone(common.platform_raw)

    def test_absent_siblings_are_none(self):
        """A v1 asset with no siblings still loads."""
        self._write(io.V1_CORE_FILE, {"rig_id": "rig"})
        common = io.load_common(self.root)
        self.assertEqual(common.version, io.SCHEMA_V1)
        self.assertIsNone(common.platform_raw)
        self.assertIsNone(common.subject_raw)
        self.assertIsNone(common.data_description_raw)

    def test_result_is_frozen(self):
        """The result is immutable; callers spread rather than mutate."""
        self._write(io.V1_CORE_FILE, {})
        common = io.load_common(self.root)
        with self.assertRaises(Exception):
            common.version = io.SCHEMA_V2

    def test_missing_core_file_raises(self):
        """An input directory with no core file fails loudly."""
        with self.assertRaises(FileNotFoundError):
            io.load_common(self.root)

    def test_load_optional_absent(self):
        """load_optional returns None when the file is not there."""
        self.assertIsNone(io.load_optional(self.root, "nope.json"))

    def test_load_optional_uses_sorted_first_match(self):
        """load_optional chooses the lexicographically first duplicate."""
        (self.root / "b").mkdir()
        (self.root / "b" / io.SUBJECT_FILE).write_text(
            json.dumps({"subject_id": "second"})
        )
        (self.root / "a").mkdir()
        (self.root / "a" / io.SUBJECT_FILE).write_text(
            json.dumps({"subject_id": "first"})
        )
        self.assertEqual(
            io.load_optional(self.root, io.SUBJECT_FILE),
            {"subject_id": "first"},
        )


if __name__ == "__main__":
    unittest.main()
