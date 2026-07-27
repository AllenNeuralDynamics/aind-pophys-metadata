"""Tests for aind_pophys_metadata.processing."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aind_data_schema.components.identifiers import Code
from aind_data_schema.core.processing import ResourceUsage
from aind_data_schema_models.process_names import ProcessName

from aind_pophys_metadata import processing

_START = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
_END = datetime(2024, 1, 1, 12, 30, tzinfo=timezone.utc)


class TestProcessing(unittest.TestCase):
    """Code / DataProcess / Processing builders and the JSON writer."""

    def _code(self) -> Code:
        """Build a minimal Code block for reuse."""
        return processing.build_code(
            url="https://example.com/repo",
            name="Example",
            version="0.0.1",
        )

    def test_resource_usage(self):
        """resource_usage reports OS, architecture, and cores."""
        ru = processing.resource_usage()
        self.assertIsInstance(ru, ResourceUsage)
        self.assertTrue(ru.os)
        self.assertTrue(ru.architecture)

    def test_build_code_minimal(self):
        """A minimal code block omits input data and defaults language."""
        code = self._code()
        self.assertIsNone(code.input_data)
        self.assertTrue(code.language_version)

    def test_build_code_with_input_data(self):
        """input_data names are wrapped and language_version is honored."""
        code = processing.build_code(
            url="u",
            name="n",
            version="v",
            parameters={"a": 1},
            input_data=["asset_a", "asset_b"],
            language_version="3.10.0",
        )
        self.assertEqual(len(code.input_data), 2)
        self.assertEqual(code.language_version, "3.10.0")

    def test_build_data_process_minimal(self):
        """A minimal data process carries its type and empty experimenters."""
        dp = processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
        )
        self.assertEqual(dp.experimenters, [])
        self.assertEqual(dp.process_type, ProcessName.VIDEO_MOTION_CORRECTION)

    def test_build_data_process_full(self):
        """All optional fields flow through to the data process."""
        dp = processing.build_data_process(
            process_type=ProcessName.OTHER,
            code=self._code(),
            start_time=_START,
            end_time=_END,
            name="step",
            experimenters=["AIND"],
            output_path="results/",
            output_parameters={"m": 1},
            notes="ok",
            resources=processing.resource_usage(),
            pipeline_name="pipe",
        )
        self.assertEqual(dp.name, "step")
        self.assertEqual(dp.pipeline_name, "pipe")
        self.assertEqual(dp.notes, "ok")

    def test_build_processing_and_write(self):
        """build_processing wraps processes; the writer emits a JSON file."""
        dp = processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
        )
        doc = processing.build_processing(
            [dp],
            pipelines=[self._code()],
            notes="run notes",
        )
        self.assertEqual(len(doc.data_processes), 1)
        with tempfile.TemporaryDirectory() as tmp:
            out = processing.write_processing_json(doc, Path(tmp) / "sub")
            self.assertTrue(out.exists())
            self.assertEqual(out.name, "processing.json")


if __name__ == "__main__":
    unittest.main()
