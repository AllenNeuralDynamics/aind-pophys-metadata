"""Tests for aind_pophys_metadata.quality_control."""

import tempfile
import unittest
from pathlib import Path

from aind_data_schema.core.quality_control import QualityControl, Status

from aind_pophys_metadata import quality_control as qc


class TestQualityControl(unittest.TestCase):
    """QC status, dropdown metric, wrapper, and writer."""

    def _metric(self):
        """Build a dropdown metric for reuse."""
        return qc.dropdown_metric(
            name="plane_0 FOV Quality",
            evaluation="FOV Quality",
            options=["good", "bad"],
            status=[Status.PASS, Status.FAIL],
            reference="plane_0/proj.png",
            description="Assess FOV quality.",
        )

    def test_pending_status_default(self):
        """Default pending status is PENDING with the default evaluator."""
        status = qc.pending_qc_status()
        self.assertEqual(status.status, Status.PENDING)
        self.assertEqual(status.evaluator, "Pending review")

    def test_pending_status_custom_evaluator(self):
        """A custom evaluator is honored."""
        self.assertEqual(
            qc.pending_qc_status("Automated").evaluator, "Automated"
        )

    def test_dropdown_metric_tags(self):
        """The metric carries evaluation and type tags."""
        metric = self._metric()
        self.assertEqual(metric.tags["evaluation"], "FOV Quality")
        self.assertEqual(metric.tags["type"], "Operational QC")
        self.assertEqual(metric.reference, "plane_0/proj.png")

    def test_dropdown_value_is_always_empty(self):
        """A pending dropdown carries "" -- qcportal's pending contract."""
        metric = self._metric()
        self.assertEqual(metric.value.value, "")
        self.assertEqual(metric.status_history[0].status, Status.PENDING)

    def test_dropdown_metric_takes_no_preset_value(self):
        """There is deliberately no parameter to preselect a value.

        A preselected value on a PENDING metric renders as already
        answered, so a reviewer treats it as done and never opens it --
        and a preselection mapping to PASS silently auto-passes review.
        """
        with self.assertRaises(TypeError):
            qc.dropdown_metric(
                name="n",
                evaluation="e",
                options=["a"],
                status=[Status.PASS],
                value="a",
            )

    def test_build_quality_control_grouping(self):
        """The wrapper sets the standard default grouping."""
        doc = qc.build_quality_control([self._metric()])
        self.assertEqual(doc.default_grouping, qc.DEFAULT_GROUPING)
        self.assertEqual(len(doc.metrics), 1)

    def test_write_quality_control_json(self):
        """The writer emits quality_control.json and returns its path."""
        doc = qc.build_quality_control([self._metric()])
        self.assertIsInstance(doc, QualityControl)
        with tempfile.TemporaryDirectory() as tmp:
            out = qc.write_quality_control_json(doc, Path(tmp) / "sub")
            self.assertTrue(out.exists())
            self.assertEqual(out.name, "quality_control.json")


if __name__ == "__main__":
    unittest.main()
