"""Context manager wrapping one processing stage's metadata lifecycle.

A capsule's ``run()`` wraps its work in :func:`stage_guard`. The guard stamps
the timings, captures static resource usage, collects the upstream dependency
graph, and writes ``processing.json`` on the way out - including when the body
raised. A failed stage that leaves no metadata behind is indistinguishable
from a stage that never ran, which is exactly the ambiguity that makes a
truncated pipeline run hard to detect after the fact.
"""

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime as dt
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from aind_data_schema.components.identifiers import Code, DataAsset
from aind_data_schema.core.processing import ResourceUsage
from aind_data_schema_models.process_names import ProcessName

from aind_pophys_metadata.processing import (
    build_data_process,
    build_dependency_graph,
    build_processing,
    collect_static_resources,
    reject_ephemeral_paths,
    write_processing_json,
)

STAGE_START = "stage_start"
STAGE_COMPLETE = "stage_complete"
STAGE_ERROR = "stage_error"

# log-schema carries the lifecycle marker in a structured field rather than
# in the message text, so a CloudWatch query keys on the field and keeps
# matching when the wording changes. Emitted alongside the readable line,
# never instead of it. The three values match what the capsules already
# emitted before they adopted this guard; log-schema's README spells the
# failure case ``stage_failure`` and ships no enum to import, but changing
# the value here would silently break the queries this field exists to keep
# working.
EVENT_TYPE_FIELD = "event_type"


@dataclass
class StageContext:
    """Mutable handle the stage body populates before the document is built.

    Attributes
    ----------
    output_parameters : dict
        Actual parameters used and/or output metrics; written to
        ``DataProcess.output_parameters``.
    notes : str or None
        Free-text notes. Replaced with the exception text when the stage
        body raises.
    upstream_names : list of str
        Upstream ``DataProcess`` names for the dependency graph; see
        ``processing.collect_upstream_process_names``.
    resources : ResourceUsage or None
        Host resource description; pre-populated with the static capture.
    input_data : list of str
        Input artifact names resolved during the stage body, merged into
        ``Code.input_data`` when the document is built. Capsules that only
        learn their inputs partway through the run record them here rather
        than resolving artifacts outside the guard, which would leave the
        most likely failure - a missing upstream file - with no
        ``processing.json`` at all.
    parameters : dict
        Run parameters resolved during the stage body, merged into
        ``Code.parameters`` when the document is built. For values a capsule
        can only know once it has run - resolved suite2p ops, for instance -
        which would otherwise be lost to the guard taking its ``Code`` at
        entry. A key present both here and in ``build_code`` takes the
        mid-run value: the resolved value is what the run actually used,
        and the configured value it supersedes is by definition the less
        accurate of the two.
    """

    output_parameters: Dict[str, Any] = field(default_factory=dict)
    notes: Optional[str] = None
    upstream_names: List[str] = field(default_factory=list)
    resources: Optional[ResourceUsage] = None
    input_data: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)


@contextmanager
def stage_guard(
    output_dir: Path,
    process_type: ProcessName,
    code: Code,
    *,
    plane_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    **data_process_kwargs: Any,
) -> Iterator[StageContext]:
    """Time a processing stage and write its ``processing.json`` either way.

    The start time is captured *before* the body is entered, so an exception
    raised anywhere inside - including on the first statement - still yields
    a document with a real start time rather than a missing or invented one.

    Parameters
    ----------
    output_dir : Path
        Directory the ``processing.json`` is written to.
    process_type : ProcessName
        The process-name enum for this step.
    code : Code
        The code block (see
        :func:`aind_pophys_metadata.processing.build_code`).
    plane_id : str, optional
        Plane/FOV id for per-plane capsules; makes ``DataProcess.name``
        unique once every plane's document is merged.
    logger : logging.Logger, optional
        Logger for the stage lifecycle lines; defaults to this module's.
    **data_process_kwargs
        Forwarded to
        :func:`aind_pophys_metadata.processing.build_data_process` (``name``,
        ``stage``, ``experimenters``, ``output_path``, ``pipeline_name``)
        and :func:`aind_pophys_metadata.processing.build_processing`
        (``pipelines``).

    Yields
    ------
    StageContext
        Handle for the body to record output parameters, notes and upstream
        process names on.

    Raises
    ------
    BaseException
        Whatever the stage body raised, re-raised after the document has
        been written.
    """
    log = logger if logger is not None else logging.getLogger(__name__)
    start_time = dt.now(timezone.utc)
    context = StageContext(resources=collect_static_resources())
    log.info(
        "%s: process_type=%s plane_id=%s start=%s",
        STAGE_START,
        getattr(process_type, "value", process_type),
        plane_id,
        start_time.isoformat(),
        extra={EVENT_TYPE_FIELD: STAGE_START},
    )
    try:
        yield context
    except BaseException as exc:
        log.error(
            "%s: %s",
            STAGE_ERROR,
            exc,
            extra={EVENT_TYPE_FIELD: STAGE_ERROR},
        )
        context.notes = f"{STAGE_ERROR}: {exc}"
        try:
            _write(
                output_dir,
                process_type,
                code,
                start_time,
                plane_id,
                context,
                data_process_kwargs,
            )
        except Exception:
            log.exception("Failed to write processing metadata")
        raise
    try:
        _write(
            output_dir,
            process_type,
            code,
            start_time,
            plane_id,
            context,
            data_process_kwargs,
        )
    except Exception as exc:
        log.error(
            "%s: %s",
            STAGE_ERROR,
            exc,
            extra={EVENT_TYPE_FIELD: STAGE_ERROR},
        )
        raise
    log.info(
        "%s: process_type=%s",
        STAGE_COMPLETE,
        getattr(process_type, "value", process_type),
        extra={EVENT_TYPE_FIELD: STAGE_COMPLETE},
    )


def _merge_context_into_code(code: Code, context: StageContext) -> Code:
    """Return ``code`` with the context's mid-run additions folded in.

    Values supplied up front through
    :func:`aind_pophys_metadata.processing.build_code` and values resolved
    during the body compose rather than conflict. ``input_data`` names are
    appended in first-seen order with duplicates dropped; ``parameters``
    keys resolved mid-run overwrite same-named entries from ``build_code``,
    because the resolved value is the one the run actually used and the
    configured value it replaces is the less accurate of the two. The
    original ``Code`` is never mutated.

    Parameters
    ----------
    code : Code
        The code block built at stage entry.
    context : StageContext
        The populated stage context.

    Returns
    -------
    Code
        ``code`` unchanged when the context added nothing, otherwise a copy
        carrying the merged values.

    Raises
    ------
    ValueError
        If any added value is a Nextflow task scratch path (see
        :func:`aind_pophys_metadata.processing.reject_ephemeral_paths`).
        Mid-run values are the likeliest place for a task-specific path to
        appear, so the guard is re-applied here rather than only at entry.
    """
    if not context.input_data and not context.parameters:
        return code
    reject_ephemeral_paths(list(context.input_data), "input_data")
    reject_ephemeral_paths(dict(context.parameters), "parameters")
    assets = list(code.input_data or [])
    known = {asset.name for asset in assets}
    for name in context.input_data:
        if name not in known:
            assets.append(DataAsset(name=name))
            known.add(name)
    parameters = dict(code.parameters or {})
    parameters.update(context.parameters)
    return Code.model_validate(
        {
            **code.model_dump(),
            "input_data": [asset.model_dump() for asset in assets] or None,
            "parameters": parameters,
        }
    )


def _write(
    output_dir: Path,
    process_type: ProcessName,
    code: Code,
    start_time: dt,
    plane_id: Optional[str],
    context: StageContext,
    data_process_kwargs: dict,
) -> Path:
    """Assemble and write the stage's ``processing.json``.

    Parameters
    ----------
    output_dir : Path
        Destination directory.
    process_type : ProcessName
        The process-name enum for this step.
    code : Code
        The code block.
    start_time : datetime.datetime
        Timezone-aware start time captured before the body ran.
    plane_id : str or None
        Plane/FOV id for per-plane capsules.
    context : StageContext
        The populated stage context.
    data_process_kwargs : dict
        Extra keyword arguments; ``pipelines`` is routed to
        ``build_processing`` and the remainder to ``build_data_process``.

    Returns
    -------
    Path
        Path to the written ``processing.json``.
    """
    data_process_kwargs = dict(data_process_kwargs)
    pipelines = data_process_kwargs.pop("pipelines", None)
    data_process = build_data_process(
        process_type,
        _merge_context_into_code(code, context),
        start_time,
        dt.now(timezone.utc),
        plane_id=plane_id,
        output_parameters=context.output_parameters,
        notes=context.notes,
        resources=context.resources,
        **data_process_kwargs,
    )
    processing = build_processing(
        [data_process],
        pipelines=pipelines,
        dependency_graph=build_dependency_graph(
            data_process.name, context.upstream_names
        ),
    )
    return write_processing_json(processing, output_dir)
