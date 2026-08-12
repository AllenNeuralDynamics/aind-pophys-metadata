"""Tests for aind_pophys_metadata.io."""

import json
import tempfile
import unittest
from pathlib import Path

from aind_data_schema.components.configs import ImagingConfig

from aind_pophys_metadata import io


class TestIo(unittest.TestCase):
    """File discovery, JSON reads, and schema-version dispatch."""

    def setUp(self) -> None:
        """Create a temp working directory."""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        """Clean up the temp directory."""
        self._tmp.cleanup()

    def _write(self, rel: str, obj: dict) -> Path:
        """Write ``obj`` as JSON to ``root/rel`` and return the path."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))
        return path

    def test_load_json(self) -> None:
        """load_json round-trips a dict."""
        path = self._write("a.json", {"k": 1})
        self.assertEqual(io.load_json(path), {"k": 1})

    def test_find_hit_and_miss(self) -> None:
        """find returns a nested match, or None when absent."""
        self._write("sub/dir/target.json", {})
        self.assertIsNotNone(
            io.find_one(self.root, "target.json", required=False)
        )
        self.assertIsNone(
            io.find_one(self.root, "missing.json", required=False)
        )

    def test_require_present(self) -> None:
        """require returns the value when the key exists."""
        self.assertEqual(io.require({"x": 5}, "x", Path("f.json")), 5)

    def test_require_missing_raises(self) -> None:
        """require raises ValueError naming the field and file."""
        with self.assertRaises(ValueError) as ctx:
            io.require({}, "x", Path("f.json"))
        self.assertIn("x", str(ctx.exception))
        self.assertIn("f.json", str(ctx.exception))

    def test_find_core_file_prefers_v2(self) -> None:
        """Only acquisition.json present -> returns it."""
        self._write("acquisition.json", {})
        self.assertEqual(io.find_core_file(self.root).name, "acquisition.json")

    def test_find_core_file_v1_fallback(self) -> None:
        """Only session.json present -> returns it."""
        self._write("session.json", {})
        self.assertEqual(io.find_core_file(self.root).name, "session.json")

    def test_find_core_file_minimal_fallback(self) -> None:
        """Only metadata.json present -> returns it."""
        self._write("metadata.json", {})
        self.assertEqual(io.find_core_file(self.root).name, "metadata.json")

    def test_find_core_file_ambiguous_raises(self) -> None:
        """Both v1 and v2 files present -> ValueError."""
        self._write("acquisition.json", {})
        self._write("session.json", {})
        with self.assertRaises(ValueError):
            io.find_core_file(self.root)

    def test_find_core_file_prefers_a_schema_file_over_minimal(self) -> None:
        """A whole-record metadata.json beside a core file is ignored.

        Raw assets routinely ship both, so the pairing is not ambiguous.
        """
        self._write("session.json", {})
        self._write("metadata.json", {"session": {}})
        self.assertEqual(io.find_core_file(self.root).name, "session.json")

    def test_find_core_file_ambiguous_minimal_raises(self) -> None:
        """Two metadata.json files and no core file -> ValueError."""
        self._write("metadata.json", {})
        nested = self.root / "sub"
        nested.mkdir()
        (nested / "metadata.json").write_text("{}")
        with self.assertRaises(ValueError):
            io.find_core_file(self.root)

    def test_find_core_file_missing_raises(self) -> None:
        """No core file present -> FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            io.find_core_file(self.root)

    def test_detect_schema_version_minimal(self) -> None:
        """metadata.json declares the minimal schema version."""
        self.assertEqual(
            io.detect_schema_version(self._write("metadata.json", {})),
            io.SCHEMA_MINIMAL,
        )

    def test_reject_whole_record_accepts_a_minimal_document(self) -> None:
        """A document with no nested core file is not a whole record."""
        self.assertIsNone(io.reject_whole_record({"instrument_id": "MESO.1"}))

    def test_reject_whole_record_raises_on_a_nested_core(self) -> None:
        """A metadata.json carrying a nested core document raises.

        The whole-record export shares the minimal document's filename, and
        reading it as a minimal document would find no fields rather than
        failing.
        """
        for key, named in (
            ("acquisition", "acquisition.json"),
            ("session", "session.json"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as ctx:
                    io.reject_whole_record({key: {"instrument_id": "M"}})
                self.assertIn("whole-record", str(ctx.exception))
                self.assertIn(named, str(ctx.exception))

    def test_detect_schema_version(self) -> None:
        """session.json -> v1, acquisition.json -> v2."""
        self.assertEqual(
            io.detect_schema_version(Path("x/session.json")), io.SCHEMA_V1
        )
        self.assertEqual(
            io.detect_schema_version(Path("x/acquisition.json")),
            io.SCHEMA_V2,
        )

    def test_detect_schema_version_rejects_unknown_name(self) -> None:
        """An unrecognised core filename is an error, not a v2 guess."""
        with self.assertRaises(ValueError) as ctx:
            io.detect_schema_version(Path("x/sesion.json"))
        self.assertIn("sesion.json", str(ctx.exception))

    def test_object_type_value(self) -> None:
        """object_type_value reads a non-empty discriminator default."""
        value = io.object_type_value(ImagingConfig)
        self.assertIsInstance(value, str)
        self.assertTrue(value)


class TestLoadOptional(unittest.TestCase):
    """Sibling-file loading, which tolerates an absent file."""

    def setUp(self) -> None:
        """Create a temp working directory."""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_load_optional_present(self) -> None:
        """A sibling that exists is parsed."""
        (self.root / io.SUBJECT_FILE).write_text(
            json.dumps({"subject_id": "123"})
        )
        self.assertEqual(
            io.load_optional(self.root, io.SUBJECT_FILE),
            {"subject_id": "123"},
        )

    def test_load_optional_absent(self) -> None:
        """load_optional returns None when the file is not there."""
        self.assertIsNone(io.load_optional(self.root, "nope.json"))

    def test_load_optional_uses_sorted_first_match(self) -> None:
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


class TestFindOne(unittest.TestCase):
    """Glob-with-a-default discovery helper."""

    def test_finds_nested_match(self) -> None:
        """A recursive search descends into subdirectories."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a" / "b").mkdir(parents=True)
            target = root / "a" / "b" / "traces.h5"
            target.touch()
            found = io.find_one(root, "*.h5")
        self.assertEqual(found.name, "traces.h5")

    def test_non_recursive_skips_subdirectories(self) -> None:
        """recursive=False globs only the given directory."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "a" / "traces.h5").touch()
            found = io.find_one(root, "*.h5", recursive=False, required=False)
        self.assertIsNone(found)

    def test_multiple_matches_pick_is_deterministic(self) -> None:
        """With several matches the lexicographically first one wins."""
        names = ["c.h5", "a.h5", "b.h5"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "z").mkdir()
            for name in names:
                (root / "z" / name).touch()
            picks = {io.find_one(root, "*.h5").name for _ in range(5)}
        self.assertEqual(picks, {"a.h5"})

    def test_multiple_matches_sort_spans_subdirectories(self) -> None:
        """The sort is over full paths, not per-directory arrival order."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ("b_dir", "a_dir"):
                (root / sub).mkdir()
                (root / sub / "traces.h5").touch()
            found = io.find_one(root, "*.h5")
        self.assertEqual(found.parent.name, "a_dir")

    def test_optional_miss_returns_none(self) -> None:
        """required=False returns None rather than raising."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(io.find_one(Path(tmp), "*.nope", required=False))

    def test_required_miss_names_pattern_and_directory(self) -> None:
        """The error carries both the pattern and the directory."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as ctx:
                io.find_one(Path(tmp), "*.nope")
        message = str(ctx.exception)
        self.assertIn("*.nope", message)
        self.assertIn(tmp, message)


class TestRelativeToRoot(unittest.TestCase):
    """Provenance path rendering."""

    def test_path_inside_root(self) -> None:
        """A path under the root renders relative."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "VISp_0" / "dff"
            target.mkdir(parents=True)
            self.assertEqual(
                io.relative_to_root(root, target),
                str(Path("VISp_0") / "dff"),
            )

    def test_path_outside_root_is_returned_absolute(self) -> None:
        """A path genuinely outside the root renders absolute."""
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "elsewhere"
            self.assertEqual(
                io.relative_to_root(Path("/nonexistent-root"), other),
                str(other.resolve()),
            )

    def test_relative_path_outside_root_is_made_absolute(self) -> None:
        """A relative input never renders as another relative path.

        Provenance has to say where the file is; ``../elsewhere/file`` on its
        own identifies nothing once the working directory is forgotten.
        """
        rendered = io.relative_to_root(
            Path("/nonexistent-root"), Path("../elsewhere/file")
        )
        self.assertTrue(Path(rendered).is_absolute())

    def test_symlinked_root_resolves_both_sides(self) -> None:
        """A symlinked root still yields a relative path, not an absolute."""
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            (real / "plane_0").mkdir(parents=True)
            link = Path(tmp) / "results"
            link.symlink_to(real)
            self.assertEqual(
                io.relative_to_root(link, real / "plane_0"), "plane_0"
            )


if __name__ == "__main__":
    unittest.main()
