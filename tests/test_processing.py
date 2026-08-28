"""Tests for aind_pophys_metadata.processing."""

import importlib.metadata
import os
import unittest
from unittest.mock import mock_open, patch

from aind_data_schema.components.identifiers import Code
from aind_data_schema.core.processing import ResourceUsage
from aind_data_schema_models.units import MemoryUnit

from aind_pophys_metadata import processing

# An installed distribution, so the version resolver has something real
# to find; the library under test is always installed in its own test run.
_INSTALLED_LIBRARY = "aind-pophys-metadata"
_URL = "https://github.com/AllenNeuralDynamics/aind-pophys-metadata"


class TestProcessing(unittest.TestCase):
    """Code / DataProcess / Processing builders and the JSON writer."""

    def _code(self) -> Code:
        """Build a minimal Code block for reuse."""
        return processing.build_code(
            url=_URL, name="Example", library_name=_INSTALLED_LIBRARY
        )

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
            url=_URL,
            name="n",
            library_name=_INSTALLED_LIBRARY,
            parameters={"a": 1},
            input_data=["asset_a", "asset_b"],
            language_version="3.10.0",
        )
        self.assertEqual(len(code.input_data), 2)
        self.assertEqual(code.language_version, "3.10.0")


class TestLibraryVersion(unittest.TestCase):
    """The standalone backing-library version resolver."""

    def test_installed_distribution_resolves(self) -> None:
        """An installed distribution reports its own version."""
        self.assertEqual(
            processing.library_version(_INSTALLED_LIBRARY),
            importlib.metadata.version(_INSTALLED_LIBRARY),
        )

    def test_missing_distribution_returns_empty_and_warns(self) -> None:
        """An absent distribution warns once and yields an empty string."""
        with self.assertLogs(processing.logger, level="WARNING") as logs:
            self.assertEqual(processing.library_version("nope-not-real"), "")
        self.assertEqual(len(logs.output), 1)


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


if __name__ == "__main__":
    unittest.main()


class TestBuildCodeIdentity(unittest.TestCase):
    """Where ``Code.url`` and ``Code.version`` come from."""

    def test_version_comes_from_the_installed_library(self) -> None:
        """code.version is the backing library's released version."""
        code = processing.build_code(
            url=_URL, name="n", library_name=_INSTALLED_LIBRARY
        )
        self.assertEqual(
            code.version, importlib.metadata.version(_INSTALLED_LIBRARY)
        )

    def test_url_is_whatever_the_caller_supplied(self) -> None:
        """Only the consuming repo knows where its own code lives."""
        code = processing.build_code(
            url="https://example.invalid/some-repo",
            name="n",
            library_name=_INSTALLED_LIBRARY,
        )
        self.assertEqual(code.url, "https://example.invalid/some-repo")

    def test_version_environment_variable_is_ignored(self) -> None:
        """A capsule wrapper's VERSION never reaches the document."""
        with patch.dict(os.environ, {"VERSION": "9.9.9"}):
            code = processing.build_code(
                url=_URL, name="n", library_name=_INSTALLED_LIBRARY
            )
        self.assertNotEqual(code.version, "9.9.9")

    def test_missing_package_warns_and_never_raises(self) -> None:
        """An uninstalled library yields an empty version, not an error."""
        with self.assertLogs(processing.logger, level="WARNING") as logs:
            code = processing.build_code(
                url=_URL, name="n", library_name="not-a-real-distribution"
            )
        self.assertEqual(code.version, "")
        self.assertEqual(code.url, _URL)
        self.assertTrue(
            any("not-a-real-distribution" in m for m in logs.output)
        )

    def test_library_name_and_url_are_required(self) -> None:
        """Neither can be defaulted; both identify the code that ran."""
        with self.assertRaises(TypeError):
            processing.build_code(name="n", url=_URL)
        with self.assertRaises(TypeError):
            processing.build_code(name="n", library_name=_INSTALLED_LIBRARY)
