"""Builders for ``processing.json``."""

import importlib.metadata
import logging
import os
import platform as platform_mod
from typing import List, Optional

from aind_data_schema.components.identifiers import Code, DataAsset
from aind_data_schema.core.processing import ResourceUsage
from aind_data_schema_models.units import MemoryUnit

logger = logging.getLogger(__name__)

# Sources for the container's memory limit: cgroup v2 first, then v1.
CGROUP_MEMORY_LIMIT_FILES = (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
)
CPU_INFO_FILE = "/proc/cpuinfo"
CPU_MODEL_KEY = "model name"
UNKNOWN = "unknown"


def _cpu_model() -> Optional[str]:
    """Return the CPU model string from ``/proc/cpuinfo``, or ``None``.

    Returns
    -------
    str or None
        The first ``model name`` value, or ``None`` when the file is absent
        or unreadable (notably on macOS, where there is no procfs).
    """
    try:
        with open(CPU_INFO_FILE) as handle:
            for line in handle:
                key, _, value = line.partition(":")
                if key.strip() == CPU_MODEL_KEY and value.strip():
                    return value.strip()
    except OSError:
        logger.debug("Could not read %s", CPU_INFO_FILE)
    return None


def _cgroup_memory_bytes() -> Optional[float]:
    """Return the container's memory limit in bytes, or ``None``.

    The cgroup limit, not the host's total memory, so it describes what the
    task was allowed to use. An unlimited cgroup reports ``"max"``.

    Returns
    -------
    float or None
        The limit in bytes, or ``None`` when no cgroup file is readable or
        the limit is unbounded.
    """
    for path in CGROUP_MEMORY_LIMIT_FILES:
        try:
            with open(path) as handle:
                return float(handle.read().strip())
        except (OSError, ValueError):
            continue
    return None


def collect_static_resources() -> ResourceUsage:
    """Return a ``ResourceUsage`` of what is truthfully known at startup.

    Nothing is sampled over time, so every ``*_usage`` field stays ``None``
    rather than carrying one misleading instantaneous reading. Each read is
    guarded independently, so a missing procfs or cgroup leaves that field
    ``None`` rather than failing the job.

    Returns
    -------
    ResourceUsage
        The populated static resource description.
    """
    memory_bytes = _cgroup_memory_bytes()
    memory_unit = MemoryUnit.B if memory_bytes is not None else None
    return ResourceUsage(
        os=platform_mod.system() or UNKNOWN,
        architecture=platform_mod.machine() or UNKNOWN,
        cpu=_cpu_model(),
        cpu_cores=os.cpu_count(),
        system_memory=memory_bytes,
        system_memory_unit=memory_unit,
        ram=memory_bytes,
        ram_unit=memory_unit,
    )


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
