"""Builders for the v2 ``processing.json`` output.

Capsule-agnostic constructors for a v2 aind-data-schema ``Processing``
document. Callers supply the varying bits (process type, code URL/name,
parameters, timings); this module assembles the ``Code`` / ``DataProcess`` /
``Processing`` objects and serializes via the schema's own
``write_standard_file`` (so ``describedBy`` / ``schema_version`` are
populated and the on-disk format matches other v2 artifacts).
"""

import logging
import os
import platform as platform_mod
from datetime import datetime as dt
from pathlib import Path
from typing import List, Optional

from aind_data_schema.components.identifiers import Code, DataAsset
from aind_data_schema.core.processing import (
    DataProcess,
    Processing,
    ProcessStage,
    ResourceUsage,
)
from aind_data_schema_models.process_names import ProcessName

logger = logging.getLogger(__name__)

PROCESSING_JSON = "processing.json"

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


def build_code(
    url: str,
    name: str,
    version: str,
    *,
    parameters: Optional[dict] = None,
    input_data: Optional[List[str]] = None,
    language_version: Optional[str] = None,
) -> Code:
    """Construct a v2 ``Code`` block for a process or pipeline.

    Parameters
    ----------
    url : str
        Source-code URL for the capsule/pipeline.
    name : str
        Human-readable code name.
    version : str
        Code/library version string.
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
        Host resource usage (see :func:`resource_usage`).
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


def build_processing(
    data_processes: List[DataProcess],
    *,
    pipelines: Optional[List[Code]] = None,
    notes: Optional[str] = None,
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
