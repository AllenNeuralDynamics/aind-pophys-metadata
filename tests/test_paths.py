"""Tests for aind_pophys_metadata.paths."""

import tempfile
import unittest
from pathlib import Path

from aind_pophys_metadata import paths


class TestFindOne(unittest.TestCase):
    """Glob-with-a-default discovery helper."""

    def test_finds_nested_match(self):
        """A recursive search descends into subdirectories."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a" / "b").mkdir(parents=True)
            target = root / "a" / "b" / "traces.h5"
            target.touch()
            found = paths.find_one(root, "*.h5")
        self.assertEqual(found.name, "traces.h5")

    def test_non_recursive_skips_subdirectories(self):
        """recursive=False globs only the given directory."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "a" / "traces.h5").touch()
            found = paths.find_one(
                root, "*.h5", recursive=False, required=False
            )
        self.assertIsNone(found)

    def test_multiple_matches_pick_is_deterministic(self):
        """With several matches the lexicographically first one wins."""
        names = ["c.h5", "a.h5", "b.h5"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "z").mkdir()
            for name in names:
                (root / "z" / name).touch()
            picks = {paths.find_one(root, "*.h5").name for _ in range(5)}
        self.assertEqual(picks, {"a.h5"})

    def test_multiple_matches_sort_spans_subdirectories(self):
        """The sort is over full paths, not per-directory arrival order."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ("b_dir", "a_dir"):
                (root / sub).mkdir()
                (root / sub / "traces.h5").touch()
            found = paths.find_one(root, "*.h5")
        self.assertEqual(found.parent.name, "a_dir")

    def test_optional_miss_returns_none(self):
        """required=False returns None rather than raising."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                paths.find_one(Path(tmp), "*.nope", required=False)
            )

    def test_required_miss_names_pattern_and_directory(self):
        """The error carries both the pattern and the directory."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as ctx:
                paths.find_one(Path(tmp), "*.nope")
        message = str(ctx.exception)
        self.assertIn("*.nope", message)
        self.assertIn(tmp, message)


class TestRelativeToRoot(unittest.TestCase):
    """Provenance path rendering."""

    def test_path_inside_root(self):
        """A path under the root renders relative."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "VISp_0" / "dff"
            target.mkdir(parents=True)
            self.assertEqual(
                paths.relative_to_root(root, target),
                str(Path("VISp_0") / "dff"),
            )

    def test_path_outside_root_is_returned_whole(self):
        """A path genuinely outside the root keeps its full value."""
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "elsewhere"
            self.assertEqual(
                paths.relative_to_root(Path("/nonexistent-root"), other),
                str(other),
            )

    def test_symlinked_root_resolves_both_sides(self):
        """A symlinked root still yields a relative path, not an absolute."""
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            (real / "plane_0").mkdir(parents=True)
            link = Path(tmp) / "results"
            link.symlink_to(real)
            self.assertEqual(
                paths.relative_to_root(link, real / "plane_0"), "plane_0"
            )


if __name__ == "__main__":
    unittest.main()
