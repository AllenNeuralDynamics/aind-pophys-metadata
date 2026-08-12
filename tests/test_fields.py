"""Tests for aind_pophys_metadata.fields."""

import logging
import unittest
from pathlib import Path
from typing import List, Optional

from aind_data_schema.components.configs import ImagingConfig, PlanarImage

import aind_pophys_metadata
from aind_pophys_metadata import fields, io

_IMAGING = io.object_type_value(ImagingConfig)
_PLANAR = io.object_type_value(PlanarImage)


def _v1_session(
    frame_rate: Optional[float] = None,
    fovs: Optional[List[dict]] = None,
) -> dict:
    """Build a minimal v1 session dict with one data stream."""
    fov = {}
    if frame_rate is not None:
        fov["frame_rate"] = frame_rate
    return {"data_streams": [{"ophys_fovs": fovs or [fov]}]}


def _v2_acquisition(
    frame_rate: Optional[float] = None,
    planes: Optional[List[dict]] = None,
) -> dict:
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

    def test_v1(self) -> None:
        """v1 frame rate comes from the first ophys_fov."""
        self.assertEqual(fields.get_frame_rate_v1(_v1_session(11.0)), 11.0)

    def test_v1_none(self) -> None:
        """v1 returns None when no FOV declares a frame rate."""
        self.assertIsNone(fields.get_frame_rate_v1(_v1_session()))
        self.assertIsNone(fields.get_frame_rate_v1({}))

    def test_v2(self) -> None:
        """v2 frame rate comes from the imaging config sampling strategy."""
        self.assertEqual(fields.get_frame_rate_v2(_v2_acquisition(9.5)), 9.5)

    def test_v2_none(self) -> None:
        """v2 returns None when sampling strategy omits frame rate."""
        self.assertIsNone(fields.get_frame_rate_v2(_v2_acquisition()))

    def test_v2_ignores_non_imaging_config(self) -> None:
        """A non-imaging configuration is skipped."""
        acq = {"data_streams": [{"configurations": [{"object_type": "x"}]}]}
        self.assertIsNone(fields.get_frame_rate_v2(acq))

    def test_platform(self) -> None:
        """platform.json frame rate comes from imaging_plane_groups."""
        platform = {
            "imaging_plane_groups": [{"acquisition_framerate_Hz": 7.0}]
        }
        self.assertEqual(fields.get_frame_rate_platform(platform), 7.0)
        self.assertIsNone(fields.get_frame_rate_platform({}))

    def test_dispatch_v1_and_v2(self) -> None:
        """get_frame_rate dispatches on version."""
        self.assertEqual(
            fields.get_frame_rate(io.SCHEMA_V1, _v1_session(3.0)), 3.0
        )
        self.assertEqual(
            fields.get_frame_rate(io.SCHEMA_V2, _v2_acquisition(4.0)), 4.0
        )

    def test_dispatch_platform_fallback(self) -> None:
        """get_frame_rate falls back to platform when core lacks it."""
        platform = {
            "imaging_plane_groups": [{"acquisition_framerate_Hz": 5.0}]
        }
        self.assertEqual(
            fields.get_frame_rate(io.SCHEMA_V1, _v1_session(), platform),
            5.0,
        )

    def test_dispatch_none(self) -> None:
        """get_frame_rate returns None with no core or platform rate."""
        self.assertIsNone(fields.get_frame_rate(io.SCHEMA_V1, _v1_session()))


class TestIdentifiers(unittest.TestCase):
    """Instrument, subject, and dataset getters."""

    def test_instrument_v1(self) -> None:
        """v1 instrument id comes from rig_id."""
        self.assertEqual(
            fields.get_instrument_id(io.SCHEMA_V1, {"rig_id": "MESO.1"}),
            "MESO.1",
        )

    def test_instrument_v2(self) -> None:
        """v2 instrument id comes from instrument_id."""
        self.assertEqual(
            fields.get_instrument_id(
                io.SCHEMA_V2, {"instrument_id": "MESO.2"}
            ),
            "MESO.2",
        )

    def test_instrument_missing(self) -> None:
        """Missing instrument id -> None."""
        self.assertIsNone(fields.get_instrument_id(io.SCHEMA_V2, {}))

    def test_instrument_required_raises(self) -> None:
        """required=True raises KeyError naming the field when missing."""
        with self.assertRaises(KeyError) as ctx:
            fields.get_instrument_id(
                io.SCHEMA_V2, {}, required=True, file_path=Path("acq.json")
            )
        self.assertIn("instrument_id", str(ctx.exception))

    def test_instrument_required_present(self) -> None:
        """required=True returns the value when present."""
        self.assertEqual(
            fields.get_instrument_id(
                io.SCHEMA_V1, {"rig_id": "MESO.1"}, required=True
            ),
            "MESO.1",
        )

    def test_subject_canonical(self) -> None:
        """subject_id is read from the core dict first."""
        self.assertEqual(fields.get_subject_id({"subject_id": 12345}), "12345")

    def test_subject_fallback(self) -> None:
        """subject_id falls back to subject.json when core omits it."""
        self.assertEqual(
            fields.get_subject_id({}, {"subject_id": "999"}), "999"
        )

    def test_subject_none(self) -> None:
        """subject_id is None when no source provides it."""
        self.assertIsNone(fields.get_subject_id({}))
        self.assertIsNone(fields.get_subject_id({}, {}))

    def test_dataset_name(self) -> None:
        """dataset name comes from data_description name."""
        self.assertEqual(
            fields.get_dataset_name({"name": "ecephys_1"}), "ecephys_1"
        )
        self.assertIsNone(fields.get_dataset_name(None))
        self.assertIsNone(fields.get_dataset_name({}))


class TestFovNaming(unittest.TestCase):
    """Targeted-structure parsing and FOV id construction."""

    def test_acronym_shapes(self) -> None:
        """acronym parsing tolerates str, dict, None, and other."""
        self.assertIsNone(fields.acronym_from_targeted_structure(None))
        self.assertEqual(fields.acronym_from_targeted_structure("DLS"), "DLS")
        self.assertEqual(
            fields.acronym_from_targeted_structure({"acronym": "VISp"}),
            "VISp",
        )
        self.assertIsNone(fields.acronym_from_targeted_structure({"other": 1}))
        self.assertIsNone(fields.acronym_from_targeted_structure(42))

    def test_fov_id_schemes(self) -> None:
        """fov_id switches between single- and multi-plane naming."""
        self.assertEqual(
            fields.fov_id(0, "VISp", single_plane=True), "plane_0"
        )
        self.assertEqual(
            fields.fov_id(1, "VISp", single_plane=False), "VISp_1"
        )
        self.assertEqual(fields.fov_id(2, None, single_plane=False), "plane_2")

    def test_build_fov_ids(self) -> None:
        """build_fov_ids sorts by index and applies the naming rule."""
        self.assertEqual(fields.build_fov_ids([]), ())
        self.assertEqual(fields.build_fov_ids([(0, "VISp")]), ("plane_0",))
        self.assertEqual(
            fields.build_fov_ids([(1, "VISl"), (0, "VISp")]),
            ("VISp_0", "VISl_1"),
        )

    def test_fov_pairs_v1(self) -> None:
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

    def test_fov_pairs_v2_filters(self) -> None:
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

    def test_get_fov_ids_dispatch(self) -> None:
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


class TestResolveFrameRate(unittest.TestCase):
    """CLI-override-then-raise layer over get_frame_rate."""

    def test_metadata_wins(self) -> None:
        """A frame rate in the core file is used as-is."""
        session = _v1_session(frame_rate=30.0)
        self.assertEqual(
            fields.resolve_frame_rate(io.SCHEMA_V1, session), 30.0
        )

    def test_cli_override_used_when_metadata_silent(self) -> None:
        """The CLI override fills in and is announced."""
        with self.assertLogs(fields.logger, level="WARNING"):
            rate = fields.resolve_frame_rate(
                io.SCHEMA_V1, _v1_session(), cli_override=11.0
            )
        self.assertEqual(rate, 11.0)

    def test_platform_fallback(self) -> None:
        """platform.json supplies the rate when the core file omits it."""
        platform = {"imaging_plane_groups": [{"acquisition_framerate_Hz": 9}]}
        self.assertEqual(
            fields.resolve_frame_rate(io.SCHEMA_V1, _v1_session(), platform),
            9.0,
        )

    def test_optional_miss_returns_none(self) -> None:
        """required=False yields None instead of raising."""
        self.assertIsNone(
            fields.resolve_frame_rate(
                io.SCHEMA_V1, _v1_session(), required=False
            )
        )

    def test_required_miss_names_files_and_version(self) -> None:
        """The error names the core file, platform.json and the version."""
        with self.assertRaises(ValueError) as ctx:
            fields.resolve_frame_rate(
                io.SCHEMA_V2,
                _v2_acquisition(),
                core_file=Path("/data/acquisition.json"),
                input_dir=Path("/data"),
            )
        message = str(ctx.exception)
        self.assertIn("acquisition.json", message)
        self.assertIn(io.PLATFORM_FILE, message)
        self.assertIn(io.SCHEMA_V2, message)

    def test_required_miss_without_paths_still_readable(self) -> None:
        """Omitting the paths falls back to generic labels, not None."""
        with self.assertRaises(ValueError) as ctx:
            fields.resolve_frame_rate(io.SCHEMA_V1, _v1_session())
        message = str(ctx.exception)
        self.assertIn("the core file", message)
        self.assertIn("the input directory", message)


class TestAsInt(unittest.TestCase):
    """Safe int coercion over raw metadata values."""

    def test_coerces_int_float_and_string(self) -> None:
        """Numeric shapes produced by both v1 and v2 are accepted."""
        self.assertEqual(fields._as_int(3), 3)
        self.assertEqual(fields._as_int(3.7), 3)
        self.assertEqual(fields._as_int("4"), 4)

    def test_none_is_not_an_error(self) -> None:
        """An explicit JSON null yields None rather than TypeError."""
        self.assertIsNone(fields._as_int(None))

    def test_uncoercible_warns_and_returns_none(self) -> None:
        """A non-numeric value is reported and dropped."""
        with self.assertLogs(fields.logger, level="WARNING"):
            self.assertIsNone(fields._as_int("abc"))

    def test_public_name_is_the_private_alias(self) -> None:
        """as_int is the public spelling of the same helper."""
        self.assertIs(fields.as_int, fields._as_int)
        self.assertEqual(fields.as_int("4"), 4)

    def test_exported_from_package(self) -> None:
        """as_int is importable from the package root."""
        self.assertIs(aind_pophys_metadata.as_int, fields.as_int)
        self.assertIn("as_int", aind_pophys_metadata.__all__)


class TestNullPlaneIndices(unittest.TestCase):
    """An explicit null index must not crash FOV pairing."""

    def test_v1_null_index_defaults_to_zero(self) -> None:
        """A v1 FOV with index: null is treated as plane 0."""
        session = _v1_session(
            fovs=[{"index": None, "targeted_structure": "VISp"}]
        )
        self.assertEqual(fields.get_fov_pairs_v1(session), [(0, "VISp")])

    def test_v2_null_plane_index_defaults_to_zero(self) -> None:
        """A v2 plane with plane_index: null is treated as plane 0."""
        acq = _v2_acquisition(
            planes=[{"plane_index": None, "targeted_structure": "VISp"}]
        )
        self.assertEqual(fields.get_fov_pairs_v2(acq), [(0, "VISp")])


class TestValidateScavengedIds(unittest.TestCase):
    """Cross-check between directory-scavenged and canonical plane ids."""

    def test_agreement_is_silent(self) -> None:
        """Matching id sets return True."""
        self.assertTrue(
            fields.validate_scavenged_ids(
                ["VISp_0", "VISp_1"], ("VISp_1", "VISp_0")
            )
        )

    def test_divergence_warns_but_never_raises(self) -> None:
        """A mismatch is reported once with both sets and returns False."""
        with self.assertLogs(fields.logger, level="WARNING") as logs:
            result = fields.validate_scavenged_ids(["plane_0"], ["VISp_0"])
        self.assertFalse(result)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("plane_0", logs.output[0])
        self.assertIn("VISp_0", logs.output[0])

    def test_custom_logger_is_used(self) -> None:
        """A caller-supplied logger receives the warning."""
        log = logging.getLogger("test_scavenged")
        with self.assertLogs(log, level="WARNING"):
            fields.validate_scavenged_ids(["a"], ["b"], log)

    def test_single_mode_membership_hit_is_silent(self) -> None:
        """A per-plane task's one id is fine among all canonical ids."""
        with self.assertNoLogs(fields.logger, level="WARNING"):
            self.assertTrue(
                fields.validate_scavenged_ids(
                    ["VISp_1"],
                    ["VISp_0", "VISp_1", "VISp_2"],
                    mode=fields.SCAVENGE_MODE_SINGLE,
                )
            )

    def test_single_mode_membership_miss_warns(self) -> None:
        """An id absent from the canonical set still warns, not raises."""
        with self.assertLogs(fields.logger, level="WARNING") as logs:
            result = fields.validate_scavenged_ids(
                ["plane_9"],
                ["VISp_0", "VISp_1"],
                mode=fields.SCAVENGE_MODE_SINGLE,
            )
        self.assertFalse(result)
        self.assertIn("plane_9", logs.output[0])
        self.assertIn("VISp_0", logs.output[0])

    def test_all_mode_equality_miss_warns(self) -> None:
        """A partial scavenge still diverges under the default mode."""
        with self.assertLogs(fields.logger, level="WARNING"):
            self.assertFalse(
                fields.validate_scavenged_ids(
                    ["VISp_1"],
                    ["VISp_0", "VISp_1"],
                    mode=fields.SCAVENGE_MODE_ALL,
                )
            )

    def test_empty_canonical_never_warns(self) -> None:
        """An acquisition listing no planes offers nothing to disagree with."""
        for mode in fields.SCAVENGE_MODES:
            with self.subTest(mode=mode):
                with self.assertNoLogs(fields.logger, level="WARNING"):
                    self.assertTrue(
                        fields.validate_scavenged_ids(
                            ["plane_0"], [], mode=mode
                        )
                    )

    def test_unknown_mode_is_a_caller_bug(self) -> None:
        """A mistyped mode is rejected rather than silently defaulted."""
        with self.assertRaises(ValueError) as ctx:
            fields.validate_scavenged_ids(["a"], ["a"], mode="both")
        self.assertIn("both", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
