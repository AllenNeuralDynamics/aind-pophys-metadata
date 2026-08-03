"""Tests for aind_pophys_metadata.processing."""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from aind_data_schema.components.identifiers import Code
from aind_data_schema.core.processing import Processing, ResourceUsage
from aind_data_schema_models.process_names import ProcessName
from pydantic import ValidationError

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

    def test_plane_id_qualifies_derived_name(self):
        """plane_id prefixes the process-type label when name is omitted."""
        dp = processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
            plane_id="VISp_0",
        )
        self.assertEqual(dp.name, "VISp_0: Video motion correction")

    def test_plane_id_qualifies_explicit_name(self):
        """plane_id prefixes an explicit name instead of replacing it."""
        dp = processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
            name="Suite2P motion correction",
            plane_id="VISl_3",
        )
        self.assertEqual(dp.name, "VISl_3: Suite2P motion correction")

    def _plane_process(self, plane_id):
        """Build one per-plane data process for the uniqueness tests."""
        return processing.build_data_process(
            process_type=ProcessName.VIDEO_ROI_TIMESERIES_EXTRACTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
            plane_id=plane_id,
        )

    def test_plane_ids_keep_merged_names_unique(self):
        """A merged multi-plane document validates when plane_id is set.

        ``DataProcess.name`` is the ``dependency_graph`` key, so the schema
        requires it unique across every process in one document.
        """
        procs = [self._plane_process(p) for p in ("VISp_0", "VISp_1")]
        doc = Processing(
            data_processes=procs,
            dependency_graph={p.name: [] for p in procs},
        )
        self.assertEqual(len(doc.dependency_graph), 2)

    def test_without_plane_id_merged_names_collide(self):
        """Omitting plane_id makes every plane derive the same name.

        That collision is what breaks run-level aggregation, so pin both
        halves of it: the names match, and the schema rejects the merge.
        """
        procs = [self._plane_process(None), self._plane_process(None)]
        self.assertEqual(procs[0].name, procs[1].name)
        with self.assertRaises(ValidationError):
            Processing(
                data_processes=procs,
                dependency_graph={procs[0].name: []},
            )

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


_PIPELINE_ENV = {
    processing.PIPELINE_NAME_ENV: "aind-pophys-pipeline",
    processing.PIPELINE_VERSION_ENV: "2.0.0",
    processing.PIPELINE_URL_ENV: "https://example.com/pipeline",
}


class TestPipelineIdentity(unittest.TestCase):
    """Pipeline identity is read from the environment, never hardcoded.

    ``Processing`` validates that every ``DataProcess.pipeline_name``
    resolves to an entry in ``pipelines``; these tests pin that the two
    defaults stay in lockstep.
    """

    def _code(self) -> Code:
        """Build a minimal Code block for reuse."""
        return processing.build_code(
            url="https://example.com/repo",
            name="Example",
            version="0.0.1",
        )

    def _data_process(self) -> object:
        """Build a data process using the env-derived pipeline default."""
        return processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_no_pipeline_env_omits_both(self):
        """Outside a pipeline, neither pipeline_name nor pipelines is set."""
        self.assertIsNone(processing.pipeline_name_from_env())
        with self.assertLogs(processing.logger, level="WARNING"):
            self.assertIsNone(processing.pipeline_code())
        with self.assertLogs(processing.logger, level="WARNING"):
            doc = processing.build_processing([self._data_process()])
        self.assertIsNone(doc.pipelines)
        self.assertIsNone(doc.data_processes[0].pipeline_name)

    @patch.dict(os.environ, _PIPELINE_ENV, clear=True)
    def test_complete_pipeline_env_does_not_warn(self):
        """The expected in-pipeline case is silent."""
        with self.assertNoLogs(processing.logger, level="WARNING"):
            processing.build_processing([self._data_process()])

    @patch.dict(os.environ, _PIPELINE_ENV, clear=True)
    def test_pipeline_env_populates_both(self):
        """In a pipeline, pipeline_name and pipelines agree and validate."""
        doc = processing.build_processing([self._data_process()])
        self.assertEqual(len(doc.pipelines), 1)
        self.assertEqual(doc.pipelines[0].name, "aind-pophys-pipeline")
        self.assertEqual(doc.pipelines[0].version, "2.0.0")
        self.assertEqual(
            doc.data_processes[0].pipeline_name, doc.pipelines[0].name
        )

    @patch.dict(os.environ, _PIPELINE_ENV, clear=True)
    def test_pipeline_doc_round_trips(self):
        """A pipeline-linked document serializes and re-validates."""
        doc = processing.build_processing([self._data_process()])
        with tempfile.TemporaryDirectory() as tmp:
            out = processing.write_processing_json(doc, Path(tmp))
            Processing.model_validate_json(out.read_text())

    @patch.dict(
        os.environ,
        {processing.PIPELINE_NAME_ENV: "p"},
        clear=True,
    )
    def test_missing_url_and_version_raise(self):
        """A partial pipeline environment fails loudly, with no placeholder."""
        with self.assertRaises(ValueError) as ctx:
            processing.pipeline_code()
        self.assertIn(processing.PIPELINE_URL_ENV, str(ctx.exception))
        self.assertIn(processing.PIPELINE_VERSION_ENV, str(ctx.exception))

    @patch.dict(
        os.environ,
        {
            processing.PIPELINE_URL_ENV: "https://example.com/p",
            processing.PIPELINE_VERSION_ENV: "2.0.0",
        },
        clear=True,
    )
    def test_missing_name_raises(self):
        """A missing name alone is a partial environment, not standalone."""
        with self.assertRaises(ValueError) as ctx:
            processing.pipeline_code()
        self.assertIn(processing.PIPELINE_NAME_ENV, str(ctx.exception))

    @patch.dict(
        os.environ,
        {
            processing.PIPELINE_NAME_ENV: "p",
            processing.PIPELINE_URL_ENV: "https://example.com/p",
        },
        clear=True,
    )
    def test_missing_version_raises(self):
        """A missing version alone still fails loudly."""
        with self.assertRaises(ValueError) as ctx:
            processing.pipeline_code()
        self.assertIn(processing.PIPELINE_VERSION_ENV, str(ctx.exception))

    @patch.dict(os.environ, _PIPELINE_ENV, clear=True)
    def test_explicit_pipeline_name_wins(self):
        """An explicit pipeline_name overrides the environment default."""
        dp = processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
            pipeline_name="explicit",
        )
        self.assertEqual(dp.pipeline_name, "explicit")


if __name__ == "__main__":
    unittest.main()
