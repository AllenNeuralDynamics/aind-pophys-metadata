"""Tests for the aind_pophys_metadata package's public surface."""

import importlib
import unittest

import aind_pophys_metadata

SUBMODULES = (
    "fields",
    "io",
    "paths",
    "processing",
    "quality_control",
    "runner",
)


class TestPublicSurface(unittest.TestCase):
    """__all__ must describe what the package actually exports."""

    def test_every_exported_name_is_importable(self) -> None:
        """Nothing in __all__ is missing from the package namespace."""
        for name in aind_pophys_metadata.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(aind_pophys_metadata, name))

    def test_exports_are_unique(self) -> None:
        """A name listed twice would hide a copy-paste merge error."""
        self.assertEqual(
            len(aind_pophys_metadata.__all__),
            len(set(aind_pophys_metadata.__all__)),
        )

    def test_no_private_names_are_exported(self) -> None:
        """Private helpers stay reachable only at their submodule path."""
        for name in aind_pophys_metadata.__all__:
            self.assertFalse(name.startswith("_"), name)

    def test_exports_are_the_submodule_objects(self) -> None:
        """Re-export never shadows the submodule path a consumer imported."""
        for module_name in SUBMODULES:
            module = importlib.import_module(
                f"aind_pophys_metadata.{module_name}"
            )
            for name in aind_pophys_metadata.__all__:
                if hasattr(module, name):
                    with self.subTest(module=module_name, name=name):
                        self.assertIs(
                            getattr(aind_pophys_metadata, name),
                            getattr(module, name),
                        )

    def test_public_module_names_are_all_exported(self) -> None:
        """A new public helper cannot drift out of __all__ unnoticed."""
        exported = set(aind_pophys_metadata.__all__)
        for module_name in SUBMODULES:
            module = importlib.import_module(
                f"aind_pophys_metadata.{module_name}"
            )
            defined = {
                name
                for name, value in vars(module).items()
                if not name.startswith("_")
                and getattr(value, "__module__", None) == module.__name__
                and name != "logger"
            }
            missing = defined - exported
            with self.subTest(module=module_name):
                self.assertEqual(missing, set())


if __name__ == "__main__":
    unittest.main()
