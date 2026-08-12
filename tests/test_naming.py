"""Tests for aind_pophys_metadata.naming."""

import unittest

from aind_pophys_metadata import naming


class TestFovId(unittest.TestCase):
    """Canonical plane-id construction."""

    def test_fov_id_schemes(self) -> None:
        """fov_id switches between single- and multi-plane naming."""
        self.assertEqual(
            naming.fov_id(0, "VISp", single_plane=True), "plane_0"
        )
        self.assertEqual(
            naming.fov_id(1, "VISp", single_plane=False), "VISp_1"
        )
        self.assertEqual(naming.fov_id(2, None, single_plane=False), "plane_2")

    def test_empty_acronym_never_yields_none_0(self) -> None:
        """A malformed plane falls back to the index-only spelling."""
        self.assertEqual(naming.fov_id(0, "", single_plane=False), "plane_0")

    def test_build_fov_ids(self) -> None:
        """build_fov_ids sorts by index and applies the naming rule."""
        self.assertEqual(naming.build_fov_ids([]), ())
        self.assertEqual(naming.build_fov_ids([(0, "VISp")]), ("plane_0",))
        self.assertEqual(
            naming.build_fov_ids([(1, "VISl"), (0, "VISp")]),
            ("VISp_0", "VISl_1"),
        )


if __name__ == "__main__":
    unittest.main()
