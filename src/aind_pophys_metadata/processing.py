"""Builders for ``processing.json``."""

import hashlib
import importlib.metadata
import logging
import os
import platform as platform_mod
from datetime import datetime as dt
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from aind_data_schema.components.identifiers import Code, DataAsset
from aind_data_schema.core.processing import (
    DataProcess,
    Processing,
    ProcessStage,
    ResourceUsage,
)
from aind_data_schema_models.process_names import ProcessName
from aind_data_schema_models.units import MemoryUnit

from aind_pophys_metadata.io import load_json

logger = logging.getLogger(__name__)

PROCESSING_JSON = "processing.json"

#: ``[project.urls]`` label naming the repository a ``Code`` url points to.
REPOSITORY_URL_LABEL = "Repository"

#: Model bytes were already present locally when the loader ran.
MODEL_SOURCE_ASSET = "code_ocean_data_asset"

#: Model bytes were downloaded at runtime by the loader.
MODEL_SOURCE_NETWORK = "network_download"

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
PIPELINE_VERSION_ENV = "PIPELINE_VERSION"
PIPELINE_URL_ENV = "PIPELINE_URL"


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


def pipeline_code() -> Optional[Code]:
    """Build the pipeline-level ``Code`` block from the environment.

    All three env vars are expected inside the pipeline; a partial set is a
    misconfiguration and raises.

    ``Processing`` validates that every ``DataProcess.pipeline_name`` resolves
    to an entry in ``Processing.pipelines``, so both derive from
    ``PIPELINE_NAME``.

    Returns
    -------
    Code or None
        The pipeline code block, or ``None`` when none of the three are set
        (a standalone capsule run outside the pipeline).

    Raises
    ------
    ValueError
        If only some of the three are set.
    """
    values = {
        env: os.getenv(env)
        for env in (
            PIPELINE_NAME_ENV,
            PIPELINE_URL_ENV,
            PIPELINE_VERSION_ENV,
        )
    }
    missing = [env for env, value in values.items() if not value]
    if len(missing) == len(values):
        logger.warning(
            "None of %s are set; emitting processing.json without pipeline "
            "linkage. Expected inside the pipeline, which exports all three.",
            ", ".join(values),
        )
        return None
    if missing:
        raise ValueError(
            f"Incomplete pipeline environment: {', '.join(missing)} "
            f"missing. The pipeline must export {PIPELINE_NAME_ENV}, "
            f"{PIPELINE_URL_ENV} and {PIPELINE_VERSION_ENV}."
        )
    return Code(
        name=values[PIPELINE_NAME_ENV],
        url=values[PIPELINE_URL_ENV],
        version=values[PIPELINE_VERSION_ENV],
    )


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


def _distribution_metadata(package: str) -> importlib.metadata.PackageMetadata:
    """Return the installed distribution metadata for ``package``.

    Parameters
    ----------
    package : str
        A module name inside the package, typically the caller's
        ``__name__``. Only the top-level package is used, and it resolves to
        the distribution of the same normalized name.

    Returns
    -------
    importlib.metadata.PackageMetadata
        The distribution's core metadata.

    Raises
    ------
    importlib.metadata.PackageNotFoundError
        If no installed distribution matches the top-level package.
    """
    top_level = package.split(".")[0]
    try:
        return importlib.metadata.metadata(top_level)
    except importlib.metadata.PackageNotFoundError as exc:
        raise importlib.metadata.PackageNotFoundError(
            f"No installed distribution for package {top_level!r}; install it "
            "so its Code identity can be read from package metadata."
        ) from exc


def _repository_url(metadata: importlib.metadata.PackageMetadata) -> str:
    """Return the ``Repository`` entry of a distribution's project urls.

    Parameters
    ----------
    metadata : importlib.metadata.PackageMetadata
        The distribution's core metadata.

    Returns
    -------
    str
        The repository url.

    Raises
    ------
    ValueError
        If the distribution declares no ``Repository`` project url.
    """
    for entry in metadata.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() == REPOSITORY_URL_LABEL.lower():
            return url.strip()
    raise ValueError(
        f"Distribution {metadata['Name']!r} declares no "
        f"[project.urls] {REPOSITORY_URL_LABEL} entry."
    )


def build_code(
    package: str,
    *,
    parameters: Optional[dict] = None,
    input_data: Optional[List[str]] = None,
    language_version: Optional[str] = None,
) -> Code:
    """Construct a v2 ``Code`` block for a process.

    ``name``, ``version`` and ``url`` are read from the installed
    distribution that provides ``package``: its name, its version, and the
    ``Repository`` entry of its project urls.

    Parameters
    ----------
    package : str
        A module name inside the calling package, typically ``__name__``.
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

    Raises
    ------
    importlib.metadata.PackageNotFoundError
        If ``package`` is not provided by an installed distribution.
    ValueError
        If that distribution declares no ``Repository`` project url.
    """
    metadata = _distribution_metadata(package)
    return Code(
        url=_repository_url(metadata),
        name=metadata["Name"],
        version=metadata["Version"],
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


def collect_upstream_process_names(
    input_dir: Path,
    *,
    exclude: Optional[str] = None,
) -> List[str]:
    """Collect every upstream ``DataProcess`` name under ``input_dir``.

    All names are collected as flat siblings. No ordering is inferred and no
    parent is told from a grandparent: the input tree cannot support that
    distinction. Unreadable documents are skipped with a warning.

    Parameters
    ----------
    input_dir : Path
        Directory searched recursively for ``processing.json`` files.
    exclude : str, optional
        This process's own name, dropped from the result so a step can never
        become its own dependency.

    Returns
    -------
    list of str
        Deduplicated upstream process names in first-seen order.
    """
    names: List[str] = []
    for path in sorted(Path(input_dir).rglob(PROCESSING_JSON)):
        try:
            blob = load_json(path)
            processes = blob.get("data_processes") or []
            if not isinstance(processes, list):
                raise ValueError("data_processes must be a list")
        except (OSError, ValueError, AttributeError) as exc:
            logger.warning("Skipping unreadable %s: %s", path, exc)
            continue
        for process in processes:
            if not isinstance(process, dict):
                logger.warning(
                    "Skipping malformed process in %s: %r", path, process
                )
                continue
            name = process.get("name")
            if name and name != exclude and name not in names:
                names.append(name)
    return names


def build_dependency_graph(
    process_name: str,
    upstream_names: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """Build a single-node ``dependency_graph`` for one process.

    ``Processing`` validates the graph's *keys* against its
    ``data_processes`` names but never its values, which is what lets a
    per-capsule document name processes living in a different document.

    Parameters
    ----------
    process_name : str
        This process's ``DataProcess.name``.
    upstream_names : list of str, optional
        Upstream process names (see
        :func:`collect_upstream_process_names`).

    Returns
    -------
    dict of str to list of str
        Mapping of ``process_name`` to its upstream names.
    """
    return {process_name: list(upstream_names or [])}


def build_processing(
    data_processes: List[DataProcess],
    *,
    pipelines: Optional[List[Code]] = None,
    notes: Optional[str] = None,
    dependency_graph: Optional[Dict[str, List[str]]] = None,
) -> Processing:
    """Wrap data processes into a full v2 ``Processing`` document.

    Parameters
    ----------
    data_processes : list of DataProcess
        The processes describing this run.
    pipelines : list of Code, optional
        Pipeline-level code blocks. Defaults to the pipeline described by
        the ``PIPELINE_*`` environment variables, and stays ``None``
        outside a pipeline run (see :func:`pipeline_code`).
    notes : str, optional
        Document-level notes.
    dependency_graph : dict of str to list of str, optional
        Upstream dependencies keyed by process name (see
        :func:`build_dependency_graph`). Omitted when ``None``.

    Returns
    -------
    Processing
        The assembled processing document.
    """
    if pipelines is None:
        code = pipeline_code()
        pipelines = [code] if code is not None else None
    return Processing(
        data_processes=list(data_processes),
        pipelines=pipelines,
        notes=notes,
        dependency_graph=dependency_graph,
    )


def write_processing_json(processing: Processing, output_dir: Path) -> Path:
    """Write ``processing.json`` to ``output_dir`` via the schema helper.

    Uses ``Processing.write_standard_file`` so the file format matches other
    v2 metadata artifacts (``describedBy`` / ``schema_version`` populated).

    Parameters
    ----------
    processing : Processing
        Object built by :func:`build_processing`.
    output_dir : Path
        Destination directory; created if it does not exist.

    Returns
    -------
    Path
        Path to the written ``processing.json``.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    processing.write_standard_file(output_directory=str(output_dir))
    return output_dir / PROCESSING_JSON


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes.

    Read in chunks so a multi-hundred-megabyte model file does not have to be
    held in memory.

    Parameters
    ----------
    path : Path
        File to hash.

    Returns
    -------
    str
        Lowercase hex digest.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_provenance(
    directory: Path,
    filenames: Iterable[str],
    *,
    source_name: str,
    source: str = MODEL_SOURCE_ASSET,
) -> List[Dict[str, str]]:
    """Describe the model files a run actually loaded, for ``code.parameters``.

    Records provenance; does **not** verify integrity. No digest is compared
    and an absent file is skipped rather than raising -- the model loader
    should fail on a missing model.

    ``source_name`` is a caller-chosen logical label, never the resolved path:
    a task scratch directory does not survive the task.

    Parameters
    ----------
    directory : Path
        Directory the files were loaded from.
    filenames : iterable of str
        File names to describe, relative to ``directory``.
    source_name : str
        Stable logical label recorded against every file.
    source : str, optional
        How the bytes were obtained; :data:`MODEL_SOURCE_ASSET` by default,
        or :data:`MODEL_SOURCE_NETWORK` when a download supplied them.

    Returns
    -------
    list of dict
        One record per file found, each with ``filename``, ``sha256``,
        ``source_name`` and ``source``.
    """
    records: List[Dict[str, str]] = []
    for filename in filenames:
        path = Path(directory) / filename
        if not path.is_file():
            logger.warning(
                "Model file %s not found under %s; omitting from provenance",
                filename,
                directory,
            )
            continue
        records.append(
            {
                "filename": filename,
                "sha256": file_sha256(path),
                "source_name": source_name,
                "source": source,
            }
        )
    return records
