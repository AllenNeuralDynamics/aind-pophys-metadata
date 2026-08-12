# aind-pophys-metadata

[![License](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)
![Code Style](https://img.shields.io/badge/code%20style-black-black)
[![semantic-release: angular](https://img.shields.io/badge/semantic--release-angular-e10079?logo=semantic-release)](https://github.com/semantic-release/semantic-release)
![Interrogate](https://img.shields.io/badge/interrogate-100.0%25-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue?logo=python)

Shared `aind-data-schema` v1/v2 metadata reads and `Processing` / `QualityControl` output builders.

Reading a v1 `session.json` or a v2 `acquisition.json`, deciding which one you have, and emitting a
schema-valid `processing.json` are implemented here once, so that no caller has to reimplement them.

## Schema version dispatch

**Version is determined by file presence, not by parsing `schema_version`:** `session.json` means
v1, `acquisition.json` means v2. Both present is ambiguous and raises. An unrecognised core filename
raises rather than defaulting to v2.

`load_common(input_dir)` performs the whole find-core-file / detect-version / load-optional-siblings
preamble once and returns a **frozen** `CommonMetadata` carrying `version`, `core_path`, `core_raw`
and the optional `platform_raw` / `subject_raw` / `data_description_raw`. Callers spread it into
their own dataclass; there is deliberately no shared base class, because the fields a caller needs
vary too widely for one to fit.

Getters take the version as an argument, so a caller can thread the token through to them instead of
branching on it.

Input files are read as **raw dicts**. Nothing here validates an input document just to read a field
from it, so metadata that is messy but sufficient still runs.

## Modules

Every name in `__all__` is re-exported from the package root and is equally importable from its own
submodule. Both spellings are supported; `tests/test_public_api.py` asserts they stay in step.

- **`io`** — file discovery, schema-version dispatch, raw JSON reads, discriminator resolution.
  Exports `load_common`, `find_acquisition_file`, `detect_schema_version`, `find`, `load_optional`,
  `load_json`, `require`, `object_type_value`, `CommonMetadata`, and the `SCHEMA_V1` / `SCHEMA_V2`
  and core-filename constants.

- **`fields`** — field-level v1/v2 getters. Exports `resolve_frame_rate`, `get_frame_rate` (with its
  `_v1` / `_v2` / `_platform` variants), `get_fov_ids`, `build_fov_ids`, `fov_id`,
  `get_fov_pairs_v1` / `_v2`, `get_instrument_id`, `get_subject_id`, `get_dataset_name`,
  `acronym_from_targeted_structure`, `as_int`, `validate_scavenged_ids`, and the `SCAVENGE_MODE_*`
  constants.
  - `resolve_frame_rate` folds the core-file → `platform.json` → CLI-override → raise ladder into
    one call with one error message.
  - `validate_scavenged_ids` **warns, never raises.** Uniqueness of scavenged ids is the hard
    requirement and directory names already guarantee it, so divergence from the canonical set must
    not kill a run. Pass `SCAVENGE_MODE_SINGLE` when the caller scavenges a single id and the
    canonical set holds many; the default `SCAVENGE_MODE_ALL` compares the two as sets.
  - `as_int` returns `None` for an absent, null or uncoercible value rather than raising, because an
    explicit JSON `null` is distinct from a missing key and a bare `int(...)` would fail on a field
    a real asset is allowed to leave blank.

- **`paths`** — glob-with-a-default input discovery and provenance-safe relative paths. Exports
  `find_one`, `relative_to_root`.
  - `find_one(directory, pattern, recursive=, required=)` replaces a bare `next(glob(...))`, which
    raises a context-free `StopIteration` the moment an expected file is renamed.
  - `relative_to_root` resolves both sides first, so a symlinked root still yields a relative
    result. A path genuinely outside the root comes back resolved and absolute, so provenance is
    never reduced to a bare relative fragment.

- **`processing`** — `processing.json` builders. Exports `build_code`, `build_data_process`,
  `build_processing`, `write_processing_json`, `build_dependency_graph`,
  `collect_upstream_process_names`, `reject_ephemeral_paths`, `resource_usage`,
  `collect_static_resources`, `library_version`, `pipeline_code`, `pipeline_name_from_env`,
  `PROCESSING_JSON`, `EPHEMERAL_PATH_MARKER`, and the `PIPELINE_*_ENV` constants.
  - **Code identity is derived, not supplied.** `build_code` takes a required keyword-only
    `library_name` and resolves `code.version` from `importlib.metadata` and `code.url` from the
    GitHub org path. Caller-supplied `url=` / `version=` parameters were removed rather than
    accepted-and-ignored, so a stale call site fails loudly instead of emitting an unidentifiable
    `Code`.
  - **`DataProcess.name` must be unique within a document** — it is the `dependency_graph` key, and
    `Processing.validate_process_graph` rejects duplicates. Passing `plane_id=` composes
    `"{plane_id}: {base}"`; a caller writing one document per run passes an explicit distinct `name`
    instead.
  - **`Processing.pipelines` and `DataProcess.pipeline_name` are written together or not at all**, as
    the schema validates that the name resolves to an entry in the list. Both derive from the
    `PIPELINE_NAME` / `PIPELINE_URL` / `PIPELINE_VERSION` environment variables: all three set means
    populated, all three absent means omitted with a warning, and a partial set raises.
  - **`dependency_graph` is built from siblings.** `collect_upstream_process_names(input_dir)` treats
    every `processing.json` present in the input directory as an upstream dependency, with no
    ordering and no attempt to tell a parent from a grandparent — if two upstream documents both
    reached these inputs, both are equally upstream. Do not deduplicate to one edge per step type;
    the repeated edges are the real shape of what produced these inputs. The schema validates the
    graph's keys but never its values, which is what lets a single-process document name upstreams
    living in other documents.
  - `reject_ephemeral_paths` raises on a `/tmp/nxf.*` scratch path anywhere in `parameters`,
    `input_data` or `output_path`. Such a path points at a directory destroyed when the task ends,
    which is worse than an absent field because it reads as real provenance.

- **`quality_control`** — `quality_control.json` builders. Exports `build_quality_control`,
  `write_quality_control_json`, `dropdown_metric`, `pending_qc_status`, `DEFAULT_GROUPING`,
  `QUALITY_CONTROL_JSON`.
  - `build_quality_control` emits a full document with `default_grouping=["evaluation"]`, so every
    metric needs an `"evaluation"` tag. Every metric also needs a non-empty `status_history`, or
    deriving the document status raises `IndexError`.
  - A pending `DropdownMetric` always carries `value=""`, and `dropdown_metric` exposes no `value`
    parameter at all: a preselected value on a metric whose status is pending renders in QC Portal
    as already answered, so the reviewer never opens it.

- **`runner`** — the stage guard that writes `processing.json` on both the success and the failure
  path. Exports `stage_guard`, `StageContext`, `EVENT_TYPE_FIELD`, `STAGE_START`, `STAGE_COMPLETE`,
  `STAGE_ERROR`.
  - The guard times the stage and re-raises whatever the body raised. Ordering is deliberate:
    `stage_complete` is logged only after the write succeeds, so monitoring never records success
    for a stage that produced no document.
  - If the write fails while the body is already failing, the write error is logged and the
    **body's** exception propagates, since that one is the diagnostic worth keeping.
  - `StageContext` is the handle a body uses to record `output_parameters`, `notes`,
    `upstream_names`, `input_data`, `parameters` and `resources` mid-run.

## Usage

```python
from aind_data_schema_models.process_names import ProcessName

from aind_pophys_metadata import (
    build_code,
    collect_upstream_process_names,
    stage_guard,
)

code = build_code(name="dF/F estimation", library_name="my-backing-library")
# stage_guard ensures a processing.json is always emitted, even on errors.
with stage_guard(
    output_dir, ProcessName.DF_F_ESTIMATION, code, plane_id="VISp_0"
) as ctx:
    # track dependency graph
    ctx.upstream_names = collect_upstream_process_names(input_dir)
    result = my_process(...)
    # set output parameters
    ctx.output_parameters["my_process_output_param"] = result["my_param"]
```

## Installation

```bash
uv sync
```

Or with pip, from the repository root:

```bash
pip install -e . --group dev
```

The `--group` flag needs pip >= 25.1.

This package is not published to PyPI. Depend on it by **exact git commit**, never a branch or tag:

```
aind-pophys-metadata @ git+https://github.com/AllenNeuralDynamics/aind-pophys-metadata.git@<sha>
```

Because a given commit pins `aind-data-schema` and `aind-data-schema-models` exactly, a dependent
project cannot combine an older pin of this package with a newer schema requirement of its own — the
two constraints are unsatisfiable and locking will refuse. Bump the pin in the same change that
moves the schema.

## Development

The CI gate is `flake8 . && interrogate --verbose . && coverage run -m unittest discover &&
coverage report` — **unittest, not pytest**. Reproduce it locally before pushing:

```bash
uv run flake8 . && uv run interrogate --verbose . && uv run coverage run -m unittest discover && uv run coverage report
```

Coverage and interrogate are both gated at 100%; black and flake8 are set to 79 columns. Coverage
counts `tests` as well as the package, so a non-test helper dropped into `tests/` is measured and
will fail the gate — keep tooling out of that directory. Every function in `src` and `tests` carries
argument and return type annotations.

## Level of Support

- [x] Supported: We are releasing this code to the public as a tool we expect others to use. Issues
  are welcomed, and we expect to address them promptly; pull requests will be vetted by our staff
  before inclusion.

## Release Status

Pre-release. This package is consumed by git commit rather than from PyPI, so the version is
informational until the first tagged release.
