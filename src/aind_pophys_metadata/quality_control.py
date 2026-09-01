"""Builders for ``quality_control.json``."""

from datetime import datetime as dt
from pathlib import Path
from typing import List, Optional

import pytz
from aind_data_schema.core.quality_control import (
    QCMetric,
    QCStatus,
    QualityControl,
    Stage,
    Status,
)
from aind_data_schema_models.modalities import Modality
from aind_qcportal_schema.metric_value import DropdownMetric

QUALITY_CONTROL_JSON = "quality_control.json"

# Group metrics by their ``evaluation`` tag value so the aggregator and every
# capsule agree on the grouping key.
DEFAULT_GROUPING: List[str] = ["evaluation"]

SEATTLE_TZ = pytz.timezone("America/Los_Angeles")

_OPERATIONAL_QC = "Operational QC"


def pending_qc_status(evaluator: str = "Pending review") -> QCStatus:
    """Return a PENDING ``QCStatus`` timestamped to US/Pacific.

    Parameters
    ----------
    evaluator : str, optional
        Evaluator label; defaults to ``"Pending review"``.

    Returns
    -------
    QCStatus
        A status with ``status=PENDING`` and a Seattle-local timestamp.
    """
    return QCStatus(
        evaluator=evaluator,
        status=Status.PENDING,
        timestamp=dt.now(SEATTLE_TZ).isoformat(),
    )


def dropdown_metric(
    name: str,
    evaluation: str,
    options: List[str],
    status: List[Status],
    *,
    reference: Optional[str] = None,
    description: str = "",
    modality: Modality = Modality.POPHYS,
    stage: Stage = Stage.PROCESSING,
    metric_type: str = _OPERATIONAL_QC,
    evaluator: str = "Pending review",
) -> QCMetric:
    """Construct a dropdown-valued ``QCMetric`` (the common QC skeleton).

    Parameters
    ----------
    name : str
        Metric name (typically ``f"{fov_id} <Metric>"``).
    evaluation : str
        Value of the ``evaluation`` tag; names the evaluation group this
        metric belongs to.
    options : list of str
        Dropdown option labels.
    status : list of Status
        Pass/fail status per option (parallel to ``options``).
    reference : str, optional
        Path to the reference image/artifact, relative to the asset.
    description : str, optional
        Reviewer-facing description of what to assess.
    modality : Modality, optional
        Data modality; defaults to ``Modality.POPHYS``.
    stage : Stage, optional
        QC stage; defaults to ``Stage.PROCESSING``.
    metric_type : str, optional
        Value of the ``type`` tag; defaults to ``"Operational QC"``.
    evaluator : str, optional
        Evaluator label passed to :func:`pending_qc_status`.

    Returns
    -------
    QCMetric
        The constructed metric (pending review).
    """
    return QCMetric(
        name=name,
        modality=modality,
        stage=stage,
        tags={"evaluation": evaluation, "type": metric_type},
        description=description,
        status_history=[pending_qc_status(evaluator)],
        reference=reference,
        # Always "". A preselected value on a PENDING metric renders as
        # already answered, so the reviewer never opens it -- and index 0
        # maps to Status.PASS, silently auto-passing. Hence no parameter.
        value=DropdownMetric(
            value="",
            options=list(options),
            status=list(status),
        ),
    )


def build_quality_control(metrics: List[QCMetric]) -> QualityControl:
    """Wrap metrics into a full v2 ``QualityControl`` with the standard
    grouping.

    Parameters
    ----------
    metrics : list of QCMetric
        Metrics produced during the run. Callers should skip writing QC when
        no metrics were produced.

    Returns
    -------
    QualityControl
        The assembled QC document.
    """
    return QualityControl(
        metrics=list(metrics),
        default_grouping=DEFAULT_GROUPING,
    )


def write_quality_control_json(
    quality_control: QualityControl,
    output_dir: Path,
    validate: bool = True,
) -> Path:
    """Write ``quality_control.json`` to ``output_dir`` via the schema helper.

    Uses ``QualityControl.write_standard_file`` so the file format matches
    other v2 metadata artifacts (``describedBy`` / ``schema_version``
    populated).

    Parameters
    ----------
    quality_control : QualityControl
        Object built by :func:`build_quality_control`.
    output_dir : Path
        Destination directory; created if it does not exist.
    validate : bool, optional
        When True (the default), the written file is re-read and validated
        back through ``QualityControl`` to catch serialize/read asymmetries
        that construction does not. Leaves no extra artifact; set False to
        skip the check.

    Returns
    -------
    Path
        Path to the written ``quality_control.json``.

    Raises
    ------
    pydantic.ValidationError
        If ``validate`` and the written file does not re-validate as a
        ``QualityControl`` document.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    quality_control.write_standard_file(output_directory=str(output_dir))
    path = output_dir / QUALITY_CONTROL_JSON
    if validate:
        QualityControl.model_validate_json(path.read_text())
    return path
