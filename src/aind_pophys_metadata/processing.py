"""Builders for ``processing.json``."""

import importlib.metadata
import logging
import os
import platform as platform_mod
from datetime import datetime as dt
from typing import List, Optional

from aind_data_schema.components.identifiers import Code, DataAsset
from aind_data_schema.core.processing import (
    DataProcess,
    ProcessStage,
    ResourceUsage,
)
from aind_data_schema_models.process_names import ProcessName
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
CO_CPUS_ENV = "CO_CPUS"
CO_MEMORY_ENV = "CO_MEMORY"

# Injected by the pipeline runtime, never hardcoded in a capsule. All three
# are expected whenever a capsule runs inside the pipeline.
PIPELINE_NAME_ENV = "PIPELINE_NAME"


def pipeline_name_from_env() -> Optional[str]:
    """Return the running pipeline's name, or ``None`` outside a pipeline.

    A plain read; :func:`pipeline_code` validates completeness, and every
    write path calls both.

    Returns
    -------
    str or None
        The ``PIPELINE_NAME`` environment value, or ``None`` when unset
        (a standalone capsule run).
    """
    return os.getenv(PIPELINE_NAME_ENV) or None


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
    ``None`` rather than failing the job. Code Ocean's requested resources
    take precedence over host and cgroup observations when those environment
    values are available.

    Returns
    -------
    ResourceUsage
        The populated static resource description.
    """
    cpu_cores = (
        int(os.environ[CO_CPUS_ENV])
        if os.getenv(CO_CPUS_ENV)
        else os.cpu_count()
    )
    memory_bytes = (
        float(os.environ[CO_MEMORY_ENV])
        if os.getenv(CO_MEMORY_ENV)
        else _cgroup_memory_bytes()
    )
    memory_unit = MemoryUnit.B if memory_bytes is not None else None
    return ResourceUsage(
        os=platform_mod.system() or UNKNOWN,
        architecture=platform_mod.machine() or UNKNOWN,
        cpu=_cpu_model(),
        cpu_cores=cpu_cores,
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


def build_data_process(
    process_type: ProcessName,
    code: Code,
    start_time: dt,
    end_time: dt,
    *,
    name: Optional[str] = None,
    plane_id: Optional[str] = None,
    stage: ProcessStage = ProcessStage.PROCESSING,
    experimenters: Optional[List[str]] = None,
    output_path: Optional[str] = None,
    output_parameters: Optional[dict] = None,
    notes: Optional[str] = None,
    resources: Optional[ResourceUsage] = None,
    pipeline_name: Optional[str] = None,
) -> DataProcess:
    """Construct a v2 ``DataProcess``.

    Parameters
    ----------
    process_type : ProcessName
        The process-name enum for this step.
    code : Code
        The code block (see :func:`build_code`).
    start_time : datetime.datetime
        Timezone-aware process start time.
    end_time : datetime.datetime
        Timezone-aware process end time.
    name : str, optional
        Human-readable process name.
    plane_id : str, optional
        Plane/FOV id of the plane this process ran on. Per-plane capsules
        pass it so the name is unique once every plane's document is
        merged; the name becomes ``"{plane_id}: {base}"``, where ``base``
        is ``name`` when given and the ``process_type`` label otherwise.
        Session-level capsules (one task per run) omit it.
    stage : ProcessStage, optional
        Processing stage; defaults to ``ProcessStage.PROCESSING``.
    experimenters : list of str, optional
        Experimenter names; defaults to an empty list.
    output_path : str, optional
        Relative path to this process's output directory.
    output_parameters : dict, optional
        Actual parameters used and/or output metrics.
    notes : str, optional
        Free-text notes (e.g. runtime status or error text).
    resources : ResourceUsage, optional
        Host resource usage (see :func:`collect_static_resources`).
    pipeline_name : str, optional
        Name of the pipeline this process belongs to. Defaults to
        ``PIPELINE_NAME`` from the environment, and is omitted entirely
        outside a pipeline run (see :func:`pipeline_code`).

    Returns
    -------
    DataProcess
        The populated data process.
    """
    if pipeline_name is None:
        pipeline_name = pipeline_name_from_env()
    # DataProcess.name doubles as the dependency_graph key and must be
    # unique per document, so per-plane names would collide once the
    # aggregator merges them. Composed here to keep the format identical.
    if plane_id:
        base = name or getattr(process_type, "value", process_type)
        name = f"{plane_id}: {base}"
    kwargs = dict(
        process_type=process_type,
        stage=stage,
        code=code,
        experimenters=experimenters or [],
        start_date_time=start_time,
        end_date_time=end_time,
        output_parameters=output_parameters or {},
    )
    # DataProcess.name is typed str and rejects an explicit None; the schema
    # derives it from process_type when omitted.
    optionals = {
        "name": name,
        "output_path": output_path,
        "notes": notes,
        "resources": resources,
        "pipeline_name": pipeline_name,
    }
    kwargs.update({k: v for k, v in optionals.items() if v is not None})
    return DataProcess(**kwargs)
