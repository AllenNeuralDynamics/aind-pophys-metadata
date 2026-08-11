"""Tests for aind_pophys_metadata.runner."""

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aind_data_schema.core.processing import Processing, ProcessStage
from aind_data_schema_models.process_names import ProcessName

from aind_pophys_metadata import processing, runner

_INSTALLED_LIBRARY = "aind-pophys-metadata"


def _code():
    """Build a minimal Code block for reuse."""
    return processing.build_code(
        name="Example", library_name=_INSTALLED_LIBRARY
    )


def _load(output_dir: Path) -> dict:
    """Read the written processing.json back as a dict."""
    return json.loads((output_dir / processing.PROCESSING_JSON).read_text())


class TestStageGuard(unittest.TestCase):
    """Stage lifecycle logging, timing and write-on-either-path."""

    def test_success_writes_valid_document(self):
        """A clean stage writes a schema-valid processing.json."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with runner.stage_guard(
                out, ProcessName.DF_F_ESTIMATION, _code()
            ) as ctx:
                ctx.output_parameters["frames"] = 10
            doc = _load(out)
        Processing.model_validate(doc)
        process = doc["data_processes"][0]
        self.assertEqual(process["output_parameters"]["frames"], 10)
        self.assertIsNone(process["notes"])

    def test_error_writes_valid_document_and_reraises(self):
        """A failed stage still emits a document that round-trips."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with self.assertRaises(RuntimeError):
                with runner.stage_guard(
                    out, ProcessName.DF_F_ESTIMATION, _code()
                ):
                    raise RuntimeError("boom")
            doc = _load(out)
        # The error document must be as valid as the success document.
        Processing.model_validate(doc)
        self.assertIn("boom", doc["data_processes"][0]["notes"])
        self.assertIn(runner.STAGE_ERROR, doc["data_processes"][0]["notes"])

    def test_start_time_survives_an_immediate_exception(self):
        """A body that raises at once still records a real start time."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with self.assertRaises(ValueError):
                with runner.stage_guard(
                    out, ProcessName.DF_F_ESTIMATION, _code()
                ):
                    raise ValueError("immediate")
            process = _load(out)["data_processes"][0]
        self.assertTrue(process["start_date_time"])
        self.assertLessEqual(
            process["start_date_time"], process["end_date_time"]
        )

    def test_lifecycle_log_lines(self):
        """stage_start and stage_complete are emitted on the clean path."""
        log = logging.getLogger("test_stage_guard")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs(log, level="INFO") as logs:
                with runner.stage_guard(
                    Path(tmp),
                    ProcessName.DF_F_ESTIMATION,
                    _code(),
                    logger=log,
                ):
                    pass
        output = "\n".join(logs.output)
        self.assertIn(runner.STAGE_START, output)
        self.assertIn(runner.STAGE_COMPLETE, output)

    def test_error_log_line(self):
        """stage_error carries the exception text."""
        log = logging.getLogger("test_stage_guard_error")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs(log, level="ERROR") as logs:
                with self.assertRaises(RuntimeError):
                    with runner.stage_guard(
                        Path(tmp),
                        ProcessName.DF_F_ESTIMATION,
                        _code(),
                        logger=log,
                    ):
                        raise RuntimeError("kaboom")
        self.assertIn("kaboom", "\n".join(logs.output))

    def test_plane_id_and_dependency_graph(self):
        """plane_id namespaces the name and keys the dependency graph."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with runner.stage_guard(
                out,
                ProcessName.DF_F_ESTIMATION,
                _code(),
                plane_id="VISp_0",
            ) as ctx:
                ctx.upstream_names = ["VISp_0: Video motion correction"]
            doc = _load(out)
        name = doc["data_processes"][0]["name"]
        self.assertTrue(name.startswith("VISp_0: "))
        self.assertEqual(
            doc["dependency_graph"],
            {name: ["VISp_0: Video motion correction"]},
        )

    def test_data_process_kwargs_forwarded(self):
        """Extra keyword arguments reach build_data_process."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with runner.stage_guard(
                out,
                ProcessName.DF_F_ESTIMATION,
                _code(),
                name="Custom name",
                stage=ProcessStage.ANALYSIS,
                output_path="VISp_0/dff",
            ):
                pass
            process = _load(out)["data_processes"][0]
        self.assertEqual(process["name"], "Custom name")
        self.assertEqual(process["stage"], ProcessStage.ANALYSIS.value)

    @patch.dict("os.environ", {}, clear=True)
    def test_pipeline_kwargs_forwarded_together(self):
        """Explicit pipeline metadata can be supplied without env vars."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            pipeline = processing.build_code(
                name="explicit", library_name=_INSTALLED_LIBRARY
            )
            with runner.stage_guard(
                out,
                ProcessName.DF_F_ESTIMATION,
                _code(),
                pipeline_name="explicit",
                pipelines=[pipeline],
            ):
                pass
            doc = _load(out)
        Processing.model_validate(doc)
        self.assertEqual(doc["pipelines"][0]["name"], "explicit")
        self.assertEqual(
            doc["data_processes"][0]["pipeline_name"], "explicit"
        )

    def test_resources_are_prepopulated(self):
        """The context arrives with a static resource capture attached."""
        with tempfile.TemporaryDirectory() as tmp:
            with runner.stage_guard(
                Path(tmp), ProcessName.DF_F_ESTIMATION, _code()
            ) as ctx:
                self.assertIsNotNone(ctx.resources)
                self.assertTrue(ctx.resources.os)
                self.assertIsNone(ctx.resources.cpu_usage)

    def test_notes_set_by_body_are_kept(self):
        """A note recorded by a clean body survives to the document."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with runner.stage_guard(
                out, ProcessName.DF_F_ESTIMATION, _code()
            ) as ctx:
                ctx.notes = "all good"
            self.assertEqual(
                _load(out)["data_processes"][0]["notes"], "all good"
            )


class TestMidRunInputData(unittest.TestCase):
    """Input artifacts resolved inside the guarded body."""

    def test_input_data_populated_mid_run_reaches_the_document(self):
        """A name recorded during the body lands on Code.input_data."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with runner.stage_guard(
                out, ProcessName.DF_F_ESTIMATION, _code()
            ) as ctx:
                ctx.input_data.append("motion_corrected.h5")
            code = _load(out)["data_processes"][0]["code"]
        self.assertEqual(
            [asset["name"] for asset in code["input_data"]],
            ["motion_corrected.h5"],
        )

    def test_up_front_and_mid_run_input_data_compose(self):
        """build_code names are kept and mid-run names append, deduped."""
        code = processing.build_code(
            name="Example",
            library_name=_INSTALLED_LIBRARY,
            input_data=["raw.h5"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with runner.stage_guard(
                out, ProcessName.DF_F_ESTIMATION, code
            ) as ctx:
                ctx.input_data.extend(["raw.h5", "traces.h5"])
            written = _load(out)["data_processes"][0]["code"]
        self.assertEqual(
            [asset["name"] for asset in written["input_data"]],
            ["raw.h5", "traces.h5"],
        )
        # The caller's Code object is not mutated by the guard.
        self.assertEqual([a.name for a in code.input_data], ["raw.h5"])

    def test_ephemeral_input_data_added_mid_run_is_rejected(self):
        """A scratch path resolved during the body still fails the guard."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx_manager:
                with runner.stage_guard(
                    Path(tmp), ProcessName.DF_F_ESTIMATION, _code()
                ) as ctx:
                    ctx.input_data.append("/tmp/nxf.AbCd/movie.h5")
        self.assertIn("input_data", str(ctx_manager.exception))


class TestMidRunParameters(unittest.TestCase):
    """Run parameters only knowable once the stage has run."""

    def test_parameters_populated_mid_run_reach_the_document(self):
        """Resolved values recorded during the body land on Code."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with runner.stage_guard(
                out, ProcessName.DF_F_ESTIMATION, _code()
            ) as ctx:
                ctx.parameters["resolved_diameter"] = 12
            code = _load(out)["data_processes"][0]["code"]
        self.assertEqual(code["parameters"]["resolved_diameter"], 12)

    def test_mid_run_parameters_override_configured_ones(self):
        """A key set both up front and mid-run keeps the resolved value."""
        code = processing.build_code(
            name="Example",
            library_name=_INSTALLED_LIBRARY,
            parameters={"diameter": None, "batch_size": 500},
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with runner.stage_guard(
                out, ProcessName.DF_F_ESTIMATION, code
            ) as ctx:
                ctx.parameters["diameter"] = 14
            written = _load(out)["data_processes"][0]["code"]
        self.assertEqual(written["parameters"]["diameter"], 14)
        self.assertEqual(written["parameters"]["batch_size"], 500)
        self.assertIsNone(dict(code.parameters)["diameter"])

    def test_ephemeral_parameter_added_mid_run_is_rejected(self):
        """A scratch path in a resolved parameter still fails the guard."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx_manager:
                with runner.stage_guard(
                    Path(tmp), ProcessName.DF_F_ESTIMATION, _code()
                ) as ctx:
                    ctx.parameters["ops_path"] = "/tmp/nxf.9z/ops.npy"
        self.assertIn("parameters.ops_path", str(ctx_manager.exception))


class _CapturingHandler(logging.Handler):
    """Collect raw LogRecords so structured extras stay inspectable."""

    def __init__(self):
        """Start with an empty record list."""
        super().__init__()
        self.records = []

    def emit(self, record):
        """Store the record verbatim.

        Parameters
        ----------
        record : logging.LogRecord
            The emitted record.
        """
        self.records.append(record)


def _capture(callable_):
    """Run ``callable_`` with a capturing logger and return its records.

    Parameters
    ----------
    callable_ : callable
        Takes the logger to pass to ``stage_guard``.

    Returns
    -------
    list of logging.LogRecord
        Every record the guard emitted.
    """
    log = logging.getLogger("test_event_type")
    log.setLevel(logging.INFO)
    handler = _CapturingHandler()
    log.addHandler(handler)
    try:
        callable_(log)
    finally:
        log.removeHandler(handler)
    return handler.records


class TestStructuredEventType(unittest.TestCase):
    """The log-schema structured marker CloudWatch queries key on."""

    def test_success_path_emits_start_and_complete(self):
        """Both lifecycle records carry the structured event_type field."""

        def body(log):
            """Run a clean stage against the capturing logger."""
            with tempfile.TemporaryDirectory() as tmp:
                with runner.stage_guard(
                    Path(tmp),
                    ProcessName.DF_F_ESTIMATION,
                    _code(),
                    logger=log,
                ):
                    pass

        events = [
            getattr(record, runner.EVENT_TYPE_FIELD, None)
            for record in _capture(body)
        ]
        self.assertEqual(events, [runner.STAGE_START, runner.STAGE_COMPLETE])

    def test_error_path_emits_stage_error(self):
        """A failed stage tags its record with the error event_type."""

        def body(log):
            """Run a failing stage against the capturing logger."""
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(RuntimeError):
                    with runner.stage_guard(
                        Path(tmp),
                        ProcessName.DF_F_ESTIMATION,
                        _code(),
                        logger=log,
                    ):
                        raise RuntimeError("boom")

        events = [
            getattr(record, runner.EVENT_TYPE_FIELD, None)
            for record in _capture(body)
        ]
        self.assertEqual(events, [runner.STAGE_START, runner.STAGE_ERROR])

    def test_message_text_is_kept_alongside_the_field(self):
        """The readable line survives; the field is additive, not a swap."""

        def body(log):
            """Run a clean stage against the capturing logger."""
            with tempfile.TemporaryDirectory() as tmp:
                with runner.stage_guard(
                    Path(tmp),
                    ProcessName.DF_F_ESTIMATION,
                    _code(),
                    logger=log,
                ):
                    pass

        records = _capture(body)
        self.assertIn(runner.STAGE_START, records[0].getMessage())
        self.assertIn(runner.STAGE_COMPLETE, records[1].getMessage())


if __name__ == "__main__":
    unittest.main()
