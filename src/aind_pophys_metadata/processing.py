"""Builders for ``processing.json``."""

import importlib.metadata
import logging
import platform as platform_mod
from typing import List, Optional

from aind_data_schema.components.identifiers import Code, DataAsset

logger = logging.getLogger(__name__)


def library_version(library_name: str) -> str:
    """Return the installed backing library's released version.

    The backing library's version names a released artifact; a capsule
    wrapper's ``VERSION`` does not. An unresolvable version warns and yields
    an empty string rather than failing a completed run.

    Parameters
    ----------
    library_name : str
        Distribution name of the backing library (e.g.
        ``"aind-ophys-dff-library"``).

    Returns
    -------
    str
        The installed version, or ``""`` when the distribution is not
        installed.
    """
    try:
        return importlib.metadata.version(library_name)
    except importlib.metadata.PackageNotFoundError:
        logger.warning(
            "Backing library %s is not installed; emitting Code without a "
            "version. Provenance for this step will not identify the exact "
            "artifact.",
            library_name,
        )
        return ""


def build_code(
    name: str,
    *,
    library_name: str,
    url: str,
    parameters: Optional[dict] = None,
    input_data: Optional[List[str]] = None,
    language_version: Optional[str] = None,
) -> Code:
    """Construct a v2 ``Code`` block for a process.

    ``version`` is the installed backing library's released version; neither a
    caller-supplied version nor the capsule's ``VERSION`` env var is consulted.
    ``url`` is supplied by the caller, since only the consuming repo knows
    where its own code lives.

    Parameters
    ----------
    name : str
        Human-readable code name.
    library_name : str
        Distribution name of the backing library (e.g.
        ``"aind-ophys-dff-library"``). The sole source of the version.
    url : str
        Repository url for the code that ran.
    parameters : dict, optional
        Run parameters recorded on the code block.
    input_data : list of str, optional
        Input data asset name(s) for provenance; wrapped in ``DataAsset``.
    language_version : str, optional
        Python version; defaults to the running interpreter's version.

    Returns
    -------
    Code
        The populated code block.
    """
    version = library_version(library_name)
    return Code(
        url=url,
        name=name,
        version=version,
        language="Python",
        language_version=language_version or platform_mod.python_version(),
        parameters=parameters or {},
        input_data=(
            [DataAsset(name=n) for n in input_data] if input_data else None
        ),
    )
