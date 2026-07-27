"""Tests for aind_pophys_metadata.fields."""

import unittest

from aind_data_schema.components.configs import ImagingConfig, PlanarImage

from aind_pophys_metadata import fields, io

_IMAGING = io.object_type_value(ImagingConfig)
_PLANAR = io.object_type_value(PlanarImage)


def _v1_session(frame_rate=None, fovs=None):
    """Build a minimal v1 session dict with one data stream."""
    fov = {}
    if frame_rate is not None:
        fov["frame_rate"] = frame_rate
    return {"data_streams": [{"ophys_fovs": fovs or [fov]}]}


def _v2_acquisition(frame_rate=None, planes=None):
    """Build a minimal v2 acquisition dict with one imaging config."""
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


class TestFrameRate(unittest.TestCase):
    """Frame-rate getters and dispatch."""

    def test_v1(self):
        """v1 frame rate comes from the first ophys_fov."""
        self.assertEqual(fields.get_frame_rate_v1(_v1_session(11.0)), 11.0)

    def test_v1_none(self):
        """v1 returns None when no FOV declares a frame rate."""
        self.assertIsNone(fields.get_frame_rate_v1(_v1_session()))
        self.assertIsNone(fields.get_frame_rate_v1({}))

    def test_v2(self):
        """v2 frame rate comes from the imaging config sampling strategy."""
        self.assertEqual(fields.get_frame_rate_v2(_v2_acquisition(9.5)), 9.5)

    def test_v2_none(self):
        """v2 returns None when sampling strategy omits frame rate."""
        self.assertIsNone(fields.get_frame_rate_v2(_v2_acquisition()))

    def test_v2_ignores_non_imaging_config(self):
        """A non-imaging configuration is skipped."""
        acq = {"data_streams": [{"configurations": [{"object_type": "x"}]}]}
        self.assertIsNone(fields.get_frame_rate_v2(acq))

    def test_platform(self):
        """platform.json frame rate comes from imaging_plane_groups."""
        platform = {
            "imaging_plane_groups": [{"acquisition_framerate_Hz": 7.0}]
        }
        self.assertEqual(fields.get_frame_rate_platform(platform), 7.0)
        self.assertIsNone(fields.get_frame_rate_platform({}))

    def test_dispatch_v1_and_v2(self):
        """get_frame_rate dispatches on version."""
        self.assertEqual(
            fields.get_frame_rate(io.SCHEMA_V1, _v1_session(3.0)), 3.0
        )
        self.assertEqual(
            fields.get_frame_rate(io.SCHEMA_V2, _v2_acquisition(4.0)), 4.0
        )

    def test_dispatch_platform_fallback(self):
        """get_frame_rate falls back to platform when core lacks it."""
        platform = {
            "imaging_plane_groups": [{"acquisition_framerate_Hz": 5.0}]
        }
        self.assertEqual(
            fields.get_frame_rate(io.SCHEMA_V1, _v1_session(), platform),
            5.0,
        )

    def test_dispatch_none(self):
        """get_frame_rate returns None with no core or platform rate."""
        self.assertIsNone(fields.get_frame_rate(io.SCHEMA_V1, _v1_session()))


class TestIdentifiers(unittest.TestCase):
    """Instrument, subject, and dataset getters."""

    def test_instrument_v1(self):
        """v1 instrument id comes from rig_id."""
        self.assertEqual(
            fields.get_instrument_id(io.SCHEMA_V1, {"rig_id": "MESO.1"}),
            "MESO.1",
        )

    def test_instrument_v2(self):
        """v2 instrument id comes from instrument_id."""
        self.assertEqual(
            fields.get_instrument_id(
                io.SCHEMA_V2, {"instrument_id": "MESO.2"}
            ),
            "MESO.2",
        )

    def test_instrument_missing(self):
        """Missing instrument id -> None."""
        self.assertIsNone(fields.get_instrument_id(io.SCHEMA_V2, {}))

    def test_subject_canonical(self):
        """subject_id is read from the core dict first."""
        self.assertEqual(fields.get_subject_id({"subject_id": 12345}), "12345")

    def test_subject_fallback(self):
        """subject_id falls back to subject.json when core omits it."""
        self.assertEqual(
            fields.get_subject_id({}, {"subject_id": "999"}), "999"
        )

    def test_subject_none(self):
        """subject_id is None when no source provides it."""
        self.assertIsNone(fields.get_subject_id({}))
        self.assertIsNone(fields.get_subject_id({}, {}))

    def test_dataset_name(self):
        """dataset name comes from data_description name."""
        self.assertEqual(
            fields.get_dataset_name({"name": "ecephys_1"}), "ecephys_1"
        )
        self.assertIsNone(fields.get_dataset_name(None))
        self.assertIsNone(fields.get_dataset_name({}))


class TestFovNaming(unittest.TestCase):
    """Targeted-structure parsing and FOV id construction."""

    def test_acronym_shapes(self):
        """acronym parsing tolerates str, dict, None, and other."""
        self.assertIsNone(fields.acronym_from_targeted_structure(None))
        self.assertEqual(fields.acronym_from_targeted_structure("DLS"), "DLS")
        self.assertEqual(
            fields.acronym_from_targeted_structure({"acronym": "VISp"}),
            "VISp",
        )
        self.assertIsNone(fields.acronym_from_targeted_structure({"other": 1}))
        self.assertIsNone(fields.acronym_from_targeted_structure(42))

    def test_fov_id_schemes(self):
        """fov_id switches between single- and multi-plane naming."""
        self.assertEqual(
            fields.fov_id(0, "VISp", single_plane=True), "plane_0"
        )
        self.assertEqual(
            fields.fov_id(1, "VISp", single_plane=False), "VISp_1"
        )
        self.assertEqual(fields.fov_id(2, None, single_plane=False), "plane_2")

    def test_build_fov_ids(self):
        """build_fov_ids sorts by index and applies the naming rule."""
        self.assertEqual(fields.build_fov_ids([]), ())
        self.assertEqual(fields.build_fov_ids([(0, "VISp")]), ("plane_0",))
        self.assertEqual(
            fields.build_fov_ids([(1, "VISl"), (0, "VISp")]),
            ("VISp_0", "VISl_1"),
        )

    def test_fov_pairs_v1(self):
        """v1 pairs read index (default 0) and targeted_structure."""
        session = {
            "data_streams": [
                {
                    "ophys_fovs": [
                        {"index": 1, "targeted_structure": "VISl"},
                        {"targeted_structure": {"acronym": "VISp"}},
                    ]
                }
            ]
        }
        self.assertEqual(
            sorted(fields.get_fov_pairs_v1(session)),
            [(0, "VISp"), (1, "VISl")],
        )

    def test_fov_pairs_v2_filters(self):
        """v2 pairs skip non-imaging configs and non-planar images."""
        acq = {
            "data_streams": [
                {
                    "configurations": [
                        {"object_type": "other"},
                        {
                            "object_type": _IMAGING,
                            "images": [
                                {"object_type": "not planar"},
                                {
                                    "object_type": _PLANAR,
                                    "planes": [
                                        {
                                            "plane_index": 0,
                                            "targeted_structure": "VISp",
                                        }
                                    ],
                                },
                            ],
                        },
                    ]
                }
            ]
        }
        self.assertEqual(fields.get_fov_pairs_v2(acq), [(0, "VISp")])

    def test_get_fov_ids_dispatch(self):
        """get_fov_ids dispatches on version and returns canonical ids."""
        session = {
            "data_streams": [
                {"ophys_fovs": [{"index": 0, "targeted_structure": "VISp"}]}
            ]
        }
        self.assertEqual(
            fields.get_fov_ids(io.SCHEMA_V1, session), ("plane_0",)
        )
        acq = _v2_acquisition(
            planes=[{"plane_index": 0, "targeted_structure": "VISp"}]
        )
        self.assertEqual(fields.get_fov_ids(io.SCHEMA_V2, acq), ("plane_0",))


if __name__ == "__main__":
    unittest.main()
