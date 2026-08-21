"""Tests for aind_pophys_metadata.processing."""

import importlib.metadata
import os
import unittest
from unittest.mock import patch

from aind_data_schema.components.identifiers import Code

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
