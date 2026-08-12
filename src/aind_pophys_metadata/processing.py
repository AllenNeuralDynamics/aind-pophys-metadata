"""Builders for the v2 ``processing.json`` output.

Capsule-agnostic constructors for a v2 aind-data-schema ``Processing``
document. Callers supply the varying bits (process type, code URL/name,
parameters, timings); this module assembles the ``Code`` / ``DataProcess`` /
``Processing`` objects and serializes via the schema's own
``write_standard_file`` (so ``describedBy`` / ``schema_version`` are
populated and the on-disk format matches other v2 artifacts).
"""

import hashlib
import importlib.metadata
import logging
import os
import platform as platform_mod
import re
from datetime import datetime as dt
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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

# Landing page for an AIND backing library. The sole source of ``Code.url``
# (see :func:`build_code`), so url and version always name one artifact.
LIBRARY_URL_TEMPLATE = "https://github.com/AllenNeuralDynamics/{name}"

# Nextflow stages every task into a private scratch directory under
# ``/tmp/nxf.<random>``. Those paths are deleted when the task ends, so they
# are meaningless in a permanent metadata document.
EPHEMERAL_PATH_MARKER = "/tmp/nxf."

#: Model bytes were already present locally when the loader ran.
MODEL_SOURCE_ASSET = "code_ocean_data_asset"

#: Model bytes were downloaded at runtime by the loader.
MODEL_SOURCE_NETWORK = "network_download"
_EPHEMERAL_PATH_RE = re.compile(r"^/tmp/nxf\.")

# Sources for the container's memory limit: cgroup v2 first, then v1.
CGROUP_MEMORY_LIMIT_FILES = (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
)
CPU_INFO_FILE = "/proc/cpuinfo"
CPU_MODEL_KEY = "model name"
UNKNOWN = "unknown"

# Pipeline identity is injected by the pipeline runtime (the pophys
# pipeline's nextflow.config ``env`` block), never hardcoded in a capsule.
# All three are expected whenever a capsule runs inside the pipeline.
PIPELINE_NAME_ENV = "PIPELINE_NAME"
PIPELINE_VERSION_ENV = "PIPELINE_VERSION"
PIPELINE_URL_ENV = "PIPELINE_URL"


def pipeline_name_from_env() -> Optional[str]:
    """Return the running pipeline's name, or ``None`` outside a pipeline.

    A plain read — completeness of the pipeline environment is validated by
    :func:`pipeline_code`. Every write path calls both (``build_data_process``
    then ``build_processing``), so a partial environment still fails loudly
    before any document is written.

    Returns
    -------
    str or None
        The ``PIPELINE_NAME`` environment value, or ``None`` when unset
        (a standalone capsule run).
    """
    return os.getenv(PIPELINE_NAME_ENV) or None


def pipeline_code() -> Optional[Code]:
    """Build the pipeline-level ``Code`` block from the environment.

    The pipeline exports ``PIPELINE_NAME``, ``PIPELINE_URL`` and
    ``PIPELINE_VERSION`` into every capsule, so all three are expected
    whenever a capsule runs inside the pipeline. Any partial set is a
    misconfigured pipeline and fails loudly — never a placeholder.

    ``Processing`` validates that every ``DataProcess.pipeline_name``
    resolves to an entry in ``Processing.pipelines``, so these two must be
    populated together — both are derived from ``PIPELINE_NAME`` to keep
    that invariant true by construction.

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


def resource_usage() -> ResourceUsage:
    """Return a ``ResourceUsage`` describing the current host.

    Returns
    -------
    ResourceUsage
        Populated with OS, CPU architecture, and logical core count.
    """
    return ResourceUsage(
        os=platform_mod.system() or "unknown",
        architecture=platform_mod.machine() or "unknown",
        cpu_cores=os.cpu_count(),
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

    Reads the cgroup limit rather than the host's total memory, so the value
    describes what the task was actually allowed to use. An unlimited cgroup
    reports ``"max"`` and yields ``None``.

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

    Only cheap, immediately available facts are recorded: OS, architecture,
    logical core count, CPU model and the container's memory limit. Nothing
    is sampled over time, so every ``*_usage`` field stays ``None`` rather
    than carrying a single misleading instantaneous reading.

    Each individual read is guarded independently — a host that exposes no
    procfs or no cgroup leaves that one field ``None`` instead of failing a
    job over a metadata nicety.

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


def reject_ephemeral_paths(value: Any, key: str = "") -> None:
    """Raise if ``value`` contains a Nextflow task scratch path.

    Nextflow stages each task under ``/tmp/nxf.<random>``, a directory that
    is destroyed the moment the task ends. Recording one in ``processing.json``
    produces a document that points at nothing — worse than omitting the
    field, because it reads as real provenance. Containers are walked
    recursively so a path nested inside a parameter dict is caught too.

    Parameters
    ----------
    value : Any
        Value to scan; dicts, lists and tuples are walked recursively and
        anything that is not a string is ignored.
    key : str, optional
        Dotted key path of ``value``, used only to name the offender in the
        error message.

    Raises
    ------
    ValueError
        If any string within ``value`` is a Nextflow task scratch path.
    """
    if isinstance(value, dict):
        for sub_key, sub_value in value.items():
            child = f"{key}.{sub_key}" if key else str(sub_key)
            reject_ephemeral_paths(sub_value, child)
        return
    if isinstance(value, (list, tuple)):
        for index, sub_value in enumerate(value):
            reject_ephemeral_paths(sub_value, f"{key}[{index}]")
        return
    if not isinstance(value, str):
        return
    if _EPHEMERAL_PATH_RE.match(value) or EPHEMERAL_PATH_MARKER in value:
        raise ValueError(
            f"Ephemeral Nextflow work path in '{key or 'value'}': {value!r}. "
            "Task scratch directories do not survive the task and must not "
            "be written into permanent metadata; record a path relative to "
            "the results root instead."
        )


def library_version(library_name: str) -> str:
    """Return the installed backing library's released version.

    The version of the backing library is the authoritative identity of the
    code that ran: those versions bump automatically when each package
    merges to main through the shared org CI/CD workflow, so they name a
    released artifact. A capsule wrapper's ``VERSION`` does not.

    An unresolvable version is never fatal - losing a completed processing
    run over a metadata string is the wrong trade - so it warns and yields
    an empty string.

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
    parameters: Optional[dict] = None,
    input_data: Optional[List[str]] = None,
    language_version: Optional[str] = None,
) -> Code:
    """Construct a v2 ``Code`` block for a process.

    Both ``url`` and ``version`` are derived from ``library_name`` and from
    nothing else. ``version`` is the installed backing library's released
    version (see :func:`library_version`); ``url`` is that library's landing
    page. Neither a caller-supplied version nor the capsule wrapper's
    ``VERSION`` environment variable is consulted: url and version must
    always describe the same artifact, and the released library version is
    the one that identifies production pipeline code.

    An unresolvable version is never fatal; the document is still written
    with an empty version and the library url, which remains correct.

    Parameters
    ----------
    name : str
        Human-readable code name.
    library_name : str
        Distribution name of the backing library (e.g.
        ``"aind-ophys-dff-library"``). Required: it is the sole source of
        both the url and the version.
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
    ValueError
        If ``parameters`` or ``input_data`` carry a Nextflow task scratch
        path (see :func:`reject_ephemeral_paths`).
    """
    reject_ephemeral_paths(parameters or {}, "parameters")
    reject_ephemeral_paths(list(input_data or []), "input_data")
    version = library_version(library_name)
    return Code(
        url=LIBRARY_URL_TEMPLATE.format(name=library_name),
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
        Host resource usage (see :func:`resource_usage`).
    pipeline_name : str, optional
        Name of the pipeline this process belongs to. Defaults to
        ``PIPELINE_NAME`` from the environment, and is omitted entirely
        outside a pipeline run (see :func:`pipeline_code`).

    Returns
    -------
    DataProcess
        The populated data process.

    Raises
    ------
    ValueError
        If ``output_path`` is a Nextflow task scratch path (see
        :func:`reject_ephemeral_paths`).
    """
    # ``output_path`` leaks the same task scratch directory as a parameter
    # would; coerced first so a Path argument is scanned, not skipped.
    if output_path is not None:
        reject_ephemeral_paths(str(output_path), "output_path")
    if pipeline_name is None:
        pipeline_name = pipeline_name_from_env()
    # ``DataProcess.name`` doubles as the ``Processing.dependency_graph``
    # key, so aind-data-schema requires it unique across every process in
    # one document ("data_processes must have unique names."). A per-plane
    # capsule writes one document per plane, each deriving the same default
    # name from ``process_type``, so the names collide the moment the
    # aggregator merges them. Composing here rather than at each call site
    # is what keeps the format identical across capsules.
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
    # Only pass optionals when set: DataProcess.name is typed ``str`` and
    # rejects an explicit ``None`` (the schema derives it from
    # ``process_type`` when omitted).
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

    Every ``processing.json`` staged into this task is read and *all* of its
    process names are collected as flat siblings. No ordering is inferred and
    no attempt is made to tell a parent from a grandparent: if two upstream
    documents both landed in this task's inputs, they are equally upstream,
    and guessing a depth from file layout would encode a fiction the input
    tree cannot support.

    Unreadable or invalid documents are skipped with a warning — a malformed
    upstream artifact must not cost a completed run.

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

    ``Processing`` validates that the graph's *keys* exactly match its
    ``data_processes`` names, so a capsule writing one process contributes
    exactly one key. The upstream names appear only as values and are left
    unvalidated by the schema, which is what lets a per-capsule document
    name processes that live in a different document.

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

    This records provenance; it does **not** verify integrity. No expected
    digest is compared and a mismatch cannot be detected here, because the
    point is to state which bytes were used, not to police them. A file that
    is absent is skipped rather than raising -- the model loader is what
    should fail on a missing model, with its own message.

    ``source_name`` is a stable logical label chosen by the caller, never the
    resolved filesystem path: a task scratch directory does not survive the
    task, so recording one would read as provenance while pointing at
    nothing.

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

    Raises
    ------
    ValueError
        If ``source_name`` is a Nextflow task scratch path.
    """
    reject_ephemeral_paths(source_name, "source_name")
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
