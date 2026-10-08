"""Tests for aind_pophys_metadata.processing."""

import email.message
import importlib.metadata
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import mock_open, patch

from aind_data_schema.components.identifiers import Code
from aind_data_schema.core.processing import (
    DataProcess,
    Processing,
    ResourceUsage,
)
from aind_data_schema_models.process_names import ProcessName
from aind_data_schema_models.units import MemoryUnit
from pydantic import ValidationError

from aind_pophys_metadata import processing

# The library under test is always installed in its own test run, so its
# package metadata is the identity build_code reads.
_PACKAGE = "aind_pophys_metadata"
_DISTRIBUTION = "aind-pophys-metadata"
_URL = "https://github.com/AllenNeuralDynamics/aind-pophys-metadata"

_START = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
_END = datetime(2024, 1, 1, 12, 30, tzinfo=timezone.utc)


class TestProcessing(unittest.TestCase):
    """Code / DataProcess / Processing builders and the JSON writer."""

    def _code(self) -> Code:
        """Build a minimal Code block for reuse."""
        return processing.build_code(_PACKAGE)

    def test_static_resources(self) -> None:
        """collect_static_resources reports OS, architecture, cores."""
        ru = processing.collect_static_resources()
        self.assertIsInstance(ru, ResourceUsage)
        self.assertTrue(ru.os)
        self.assertTrue(ru.architecture)

    def test_build_code_minimal(self) -> None:
        """A minimal code block omits input data and defaults language."""
        code = self._code()
        self.assertIsNone(code.input_data)
        self.assertTrue(code.language_version)

    def test_build_code_with_input_data(self) -> None:
        """input_data names are wrapped and language_version is honored."""
        code = processing.build_code(
            _PACKAGE,
            parameters={"a": 1},
            input_data=["asset_a", "asset_b"],
            language_version="3.10.0",
        )
        self.assertEqual(len(code.input_data), 2)
        self.assertEqual(code.language_version, "3.10.0")

    def test_build_data_process_minimal(self) -> None:
        """A minimal data process carries its type and empty experimenters."""
        dp = processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
        )
        self.assertEqual(dp.experimenters, [])
        self.assertEqual(dp.process_type, ProcessName.VIDEO_MOTION_CORRECTION)

    def test_build_data_process_full(self) -> None:
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
            resources=processing.collect_static_resources(),
            pipeline_name="pipe",
        )
        self.assertEqual(dp.name, "step")
        self.assertEqual(dp.pipeline_name, "pipe")
        self.assertEqual(dp.notes, "ok")

    def test_plane_id_qualifies_derived_name(self) -> None:
        """plane_id prefixes the process-type label when name is omitted."""
        dp = processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
            plane_id="VISp_0",
        )
        self.assertEqual(dp.name, "VISp_0: Video motion correction")

    def test_plane_id_qualifies_explicit_name(self) -> None:
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

    def _plane_process(self, plane_id: Optional[str]) -> DataProcess:
        """Build one per-plane data process for the uniqueness tests."""
        return processing.build_data_process(
            process_type=ProcessName.VIDEO_ROI_TIMESERIES_EXTRACTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
            plane_id=plane_id,
        )

    def test_plane_ids_keep_merged_names_unique(self) -> None:
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

    def test_without_plane_id_merged_names_collide(self) -> None:
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

    def test_build_processing_and_write(self) -> None:
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
        return processing.build_code(_PACKAGE)

    def _data_process(self) -> object:
        """Build a data process using the env-derived pipeline default."""
        return processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_no_pipeline_env_omits_both(self) -> None:
        """Outside a pipeline, neither pipeline_name nor pipelines is set."""
        self.assertIsNone(processing.pipeline_name_from_env())
        with self.assertLogs(processing.logger, level="WARNING"):
            self.assertIsNone(processing.pipeline_code())
        with self.assertLogs(processing.logger, level="WARNING"):
            doc = processing.build_processing([self._data_process()])
        self.assertIsNone(doc.pipelines)
        self.assertIsNone(doc.data_processes[0].pipeline_name)

    @patch.dict(os.environ, _PIPELINE_ENV, clear=True)
    def test_complete_pipeline_env_does_not_warn(self) -> None:
        """The expected in-pipeline case is silent."""
        with self.assertNoLogs(processing.logger, level="WARNING"):
            processing.build_processing([self._data_process()])

    @patch.dict(os.environ, _PIPELINE_ENV, clear=True)
    def test_pipeline_env_populates_both(self) -> None:
        """In a pipeline, pipeline_name and pipelines agree and validate."""
        doc = processing.build_processing([self._data_process()])
        self.assertEqual(len(doc.pipelines), 1)
        self.assertEqual(doc.pipelines[0].name, "aind-pophys-pipeline")
        self.assertEqual(doc.pipelines[0].version, "2.0.0")
        self.assertEqual(
            doc.data_processes[0].pipeline_name, doc.pipelines[0].name
        )

    @patch.dict(os.environ, _PIPELINE_ENV, clear=True)
    def test_pipeline_doc_round_trips(self) -> None:
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
    def test_missing_url_and_version_raise(self) -> None:
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
    def test_missing_name_raises(self) -> None:
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
    def test_missing_version_raises(self) -> None:
        """A missing version alone still fails loudly."""
        with self.assertRaises(ValueError) as ctx:
            processing.pipeline_code()
        self.assertIn(processing.PIPELINE_VERSION_ENV, str(ctx.exception))

    @patch.dict(os.environ, _PIPELINE_ENV, clear=True)
    def test_explicit_pipeline_name_wins(self) -> None:
        """An explicit pipeline_name overrides the environment default."""
        dp = processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=self._code(),
            start_time=_START,
            end_time=_END,
            pipeline_name="explicit",
        )
        self.assertEqual(dp.pipeline_name, "explicit")


class TestStaticResources(unittest.TestCase):
    """Static resource capture and its per-field guards."""

    def test_collect_static_resources_always_populated(self) -> None:
        """OS and architecture are always present; usage stays unsampled."""
        ru = processing.collect_static_resources()
        self.assertTrue(ru.os)
        self.assertTrue(ru.architecture)
        self.assertIsNone(ru.cpu_usage)
        self.assertIsNone(ru.ram_usage)
        self.assertIsNone(ru.gpu_usage)

    def test_collect_static_resources_prefers_code_ocean_environment(
        self,
    ) -> None:
        """Code Ocean's requested CPU and memory are recorded exactly."""
        with (
            patch.dict(
                os.environ,
                {"CO_CPUS": "4", "CO_MEMORY": "34359738368"},
            ),
            patch.object(
                processing,
                "_cgroup_memory_bytes",
                side_effect=AssertionError("cgroup should be fallback only"),
            ),
            patch.object(
                os,
                "cpu_count",
                side_effect=AssertionError(
                    "cpu_count should be fallback only"
                ),
            ),
        ):
            ru = processing.collect_static_resources()

        self.assertEqual(ru.cpu_cores, 4)
        self.assertEqual(ru.system_memory, 34359738368.0)
        self.assertEqual(ru.system_memory_unit, MemoryUnit.B)
        self.assertEqual(ru.ram, 34359738368.0)
        self.assertEqual(ru.ram_unit, MemoryUnit.B)

    def test_collect_static_resources_uses_standalone_fallbacks(self) -> None:
        """Standalone runs use logical CPUs and the cgroup memory limit."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(os, "cpu_count", return_value=6),
            patch.object(
                processing,
                "_cgroup_memory_bytes",
                return_value=2147483648.0,
            ),
        ):
            ru = processing.collect_static_resources()

        self.assertEqual(ru.cpu_cores, 6)
        self.assertEqual(ru.system_memory, 2147483648.0)
        self.assertEqual(ru.system_memory_unit, MemoryUnit.B)

    def test_cpu_model_read_from_cpuinfo(self) -> None:
        """The first model-name line is returned."""
        content = "processor\t: 0\nmodel name\t: Fake CPU X1\n"
        with patch("builtins.open", mock_open(read_data=content)):
            self.assertEqual(processing._cpu_model(), "Fake CPU X1")

    def test_cpu_model_absent_key_returns_none(self) -> None:
        """A cpuinfo without a model name yields None."""
        with patch("builtins.open", mock_open(read_data="processor\t: 0\n")):
            self.assertIsNone(processing._cpu_model())

    def test_cpu_model_unreadable_returns_none(self) -> None:
        """An unreadable procfs leaves the field None, never raising."""
        with patch("builtins.open", side_effect=OSError("no procfs")):
            self.assertIsNone(processing._cpu_model())

    def test_cgroup_memory_read(self) -> None:
        """A numeric cgroup limit is returned in bytes."""
        with patch("builtins.open", mock_open(read_data="2147483648\n")):
            self.assertEqual(processing._cgroup_memory_bytes(), 2147483648.0)

    def test_cgroup_memory_unbounded_returns_none(self) -> None:
        """An unlimited ('max') or unreadable cgroup yields None."""
        with patch("builtins.open", mock_open(read_data="max\n")):
            self.assertIsNone(processing._cgroup_memory_bytes())

    def test_memory_units_paired_with_values(self) -> None:
        """The memory unit is set exactly when a limit was readable."""
        with patch.object(
            processing, "_cgroup_memory_bytes", return_value=1024.0
        ):
            ru = processing.collect_static_resources()
        self.assertEqual(ru.system_memory, 1024.0)
        self.assertEqual(ru.ram_unit, MemoryUnit.B)
        with patch.object(
            processing, "_cgroup_memory_bytes", return_value=None
        ):
            ru = processing.collect_static_resources()
        self.assertIsNone(ru.system_memory_unit)


class TestModelProvenance(unittest.TestCase):
    """SHA-256 records for the model bytes a run loaded."""

    def test_file_sha256_matches_hashlib(self) -> None:
        """The chunked digest equals a whole-file digest."""
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob"
            payload = b"x" * (1024 * 1024 + 7)
            path.write_bytes(payload)
            self.assertEqual(
                processing.file_sha256(path),
                hashlib.sha256(payload).hexdigest(),
            )

    def test_records_each_present_file(self) -> None:
        """Every located file yields filename, digest, label and source."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "net").write_bytes(b"a")
            (root / "size.npy").write_bytes(b"b")
            records = processing.model_provenance(
                root, ["net", "size.npy"], source_name="cellpose_models"
            )
        self.assertEqual([r["filename"] for r in records], ["net", "size.npy"])
        self.assertTrue(all(len(r["sha256"]) == 64 for r in records))
        self.assertEqual(
            {r["source_name"] for r in records}, {"cellpose_models"}
        )
        self.assertEqual(
            {r["source"] for r in records}, {processing.MODEL_SOURCE_ASSET}
        )

    def test_absent_file_is_omitted_not_raised(self) -> None:
        """Provenance is not an integrity gate; the loader reports absence."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "net").write_bytes(b"a")
            with self.assertLogs(processing.logger, level="WARNING"):
                records = processing.model_provenance(
                    root, ["net", "gone"], source_name="models"
                )
        self.assertEqual([r["filename"] for r in records], ["net"])

    def test_network_source_is_recordable(self) -> None:
        """A fallback download is labelled as such, not as a local asset."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "net").write_bytes(b"a")
            records = processing.model_provenance(
                root,
                ["net"],
                source_name="cellpose.org",
                source=processing.MODEL_SOURCE_NETWORK,
            )
        self.assertEqual(records[0]["source"], processing.MODEL_SOURCE_NETWORK)


class TestDependencyGraph(unittest.TestCase):
    """Upstream-name collection and dependency-graph emission."""

    def _write_doc(self, directory: Path, name: str, *names: str) -> None:
        """Write a minimal processing.json carrying the given names."""
        directory.mkdir(parents=True, exist_ok=True)
        (directory / processing.PROCESSING_JSON).write_text(
            json.dumps(
                {"data_processes": [{"name": n} for n in (name, *names)]}
            )
        )

    def test_collects_all_names_as_flat_siblings(self) -> None:
        """Every name in every document is collected, deduplicated."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_doc(root / "a", "Motion correction", "Shared")
            self._write_doc(root / "b", "Extraction", "Shared")
            names = processing.collect_upstream_process_names(root)
        self.assertEqual(
            sorted(names), ["Extraction", "Motion correction", "Shared"]
        )

    def test_excludes_own_name(self) -> None:
        """A step never becomes its own dependency."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_doc(root / "a", "Mine", "Theirs")
            names = processing.collect_upstream_process_names(
                root, exclude="Mine"
            )
        self.assertEqual(names, ["Theirs"])

    def test_unreadable_document_is_skipped_with_warning(self) -> None:
        """Invalid JSON does not cost the run its metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad").mkdir()
            (root / "bad" / processing.PROCESSING_JSON).write_text("{oops")
            self._write_doc(root / "good", "Good")
            with self.assertLogs(processing.logger, level="WARNING"):
                names = processing.collect_upstream_process_names(root)
        self.assertEqual(names, ["Good"])

    def test_non_object_document_is_skipped(self) -> None:
        """A JSON list where an object was expected is skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / processing.PROCESSING_JSON).write_text("[]")
            with self.assertLogs(processing.logger, level="WARNING"):
                self.assertEqual(
                    processing.collect_upstream_process_names(root), []
                )

    def test_non_list_data_processes_is_skipped(self) -> None:
        """A mapping where a list was expected is skipped, not iterated."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / processing.PROCESSING_JSON).write_text(
                json.dumps({"data_processes": {"name": "Motion"}})
            )
            with self.assertLogs(processing.logger, level="WARNING"):
                self.assertEqual(
                    processing.collect_upstream_process_names(root), []
                )

    def test_unnamed_process_is_ignored(self) -> None:
        """A process entry without a name contributes nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / processing.PROCESSING_JSON).write_text(
                json.dumps({"data_processes": [{}, None]})
            )
            self.assertEqual(
                processing.collect_upstream_process_names(root), []
            )

    def test_build_dependency_graph(self) -> None:
        """The graph is a single key mapping to the upstream names."""
        self.assertEqual(
            processing.build_dependency_graph("Mine", ["A", "B"]),
            {"Mine": ["A", "B"]},
        )
        self.assertEqual(
            processing.build_dependency_graph("Mine"), {"Mine": []}
        )

    def test_build_processing_emits_graph(self) -> None:
        """build_processing round-trips a dependency graph."""
        dp = processing.build_data_process(
            process_type=ProcessName.VIDEO_MOTION_CORRECTION,
            code=processing.build_code(_PACKAGE),
            start_time=_START,
            end_time=_END,
            name="Mine",
        )
        doc = processing.build_processing(
            [dp], dependency_graph={"Mine": ["Upstream"]}
        )
        self.assertEqual(doc.dependency_graph, {"Mine": ["Upstream"]})


if __name__ == "__main__":
    unittest.main()


class TestBuildCodeIdentity(unittest.TestCase):
    """``Code`` name, version and url come from package metadata."""

    def test_identity_comes_from_the_installed_distribution(self) -> None:
        """name, version and url all describe the distribution."""
        code = processing.build_code(_PACKAGE)
        self.assertEqual(code.name, _DISTRIBUTION)
        self.assertEqual(
            code.version, importlib.metadata.version(_DISTRIBUTION)
        )
        self.assertEqual(code.url, _URL)

    def test_any_module_of_the_package_resolves_the_same(self) -> None:
        """A submodule ``__name__`` resolves its top-level distribution."""
        self.assertEqual(
            processing.build_code(processing.__name__),
            processing.build_code(_PACKAGE),
        )

    def test_version_environment_variable_is_ignored(self) -> None:
        """A wrapper's VERSION never reaches the document."""
        with patch.dict(os.environ, {"VERSION": "9.9.9"}):
            code = processing.build_code(_PACKAGE)
        self.assertNotEqual(code.version, "9.9.9")

    def test_uninstalled_package_raises_naming_it(self) -> None:
        """A package with no installed distribution fails loudly."""
        with self.assertRaises(importlib.metadata.PackageNotFoundError) as cm:
            processing.build_code("not_a_real_package.module")
        self.assertIn("not_a_real_package", str(cm.exception))

    def test_repository_url_is_matched_by_label(self) -> None:
        """Only the Repository entry is used, case-insensitively."""
        metadata = _metadata(
            "Homepage, https://example.invalid/home",
            "repository, https://example.invalid/repo",
        )
        with patch.object(
            processing.importlib.metadata, "metadata", return_value=metadata
        ):
            code = processing.build_code(_PACKAGE)
        self.assertEqual(code.url, "https://example.invalid/repo")

    def test_missing_repository_url_raises(self) -> None:
        """A distribution without a Repository url cannot identify code."""
        metadata = _metadata("Homepage, https://example.invalid/home")
        with patch.object(
            processing.importlib.metadata, "metadata", return_value=metadata
        ):
            with self.assertRaises(ValueError) as cm:
                processing.build_code(_PACKAGE)
        self.assertIn("example-dist", str(cm.exception))


def _metadata(*project_urls: str) -> email.message.Message:
    """Build distribution metadata with the given ``Project-URL`` entries."""
    metadata = email.message.Message()
    metadata["Name"] = "example-dist"
    metadata["Version"] = "1.2.3"
    for entry in project_urls:
        metadata["Project-URL"] = entry
    return metadata
