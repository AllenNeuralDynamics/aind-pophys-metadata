# aind-pophys-metadata

[![License](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)
![Code Style](https://img.shields.io/badge/code%20style-black-black)
[![semantic-release: angular](https://img.shields.io/badge/semantic--release-angular-e10079?logo=semantic-release)](https://github.com/semantic-release/semantic-release)
![Interrogate](https://img.shields.io/badge/interrogate-100.0%25-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)
![Python](https://img.shields.io/badge/python->=3.10,<3.13-blue?logo=python)

Shared `aind-data-schema` metadata reads and `Processing` / `QualityControl` output builders.

## Modules

- **`core`** — `CoreMetadata`, the central class that loads metadata files and provides schema-version agnostic
  getters. Constructed once with `CoreMetadata.load(input_dir)`
  - The readers behind those methods are **private**. Most come in `_v1` / `_v2` / `_minimal` triples
    chosen by the detected version; a few fields are spelled the same in every version and need no
    branch at all.
  - currently supported fields are:
    - frame_rate
    - instrument_id
    - subject_id
    - dataset_name
    - plane_records — one dict per unique imaging plane; repeated epoch FOV
      descriptions are collapsed and stack-image descriptions are excluded
    - fov_ids — canonical plane ids, ordered by plane index
    - um_per_pixel
    - excitation_wavelength
    - emission_wavelength
    - epoch_records — one dict per stimulus epoch, in the order recorded

- **`io`** — filesystem, paths, and JSON only: finds metadata files in an input directory, detects
  the schema version from the filename, reads raw dicts.

- **`naming`** — plane and FOV id construction. Version independent by construction: these take
  already-extracted indices and acronyms, never direct metadata.

- **`processing`** — builds a V2 schema-valid `processing.json`: the `Code` block, complete `DataProcess` objects,
  `dependency_graph` assembled from upstream steps, pipeline metadata read from the
  `PIPELINE_*` environment variables, and `model_provenance` hashes recording which model files a run
  loaded, when relevant.

- **`quality_control`** — builds a full `quality_control.json` from metrics a capsule collects,
  including pending review metrics for QC Portal.

- **`runner`** — `stage_guard`, a context manager that times a stage and writes its `processing.json`
  on both the success and the failure path. `StageContext` is the handle a body uses to record
  output parameters, notes, upstream names, input data, parameters and resources mid-run.

## aind-data-schema version agnostic

**Version is determined by file presence, not by parsing `schema_version`:** `session.json` means
v1, `acquisition.json` means v2, `metadata.json` means minimal. Both a v1 and a v2 file present is
ambiguous and raises. A `metadata.json` alongside either one is ignored.

`CoreMetadata` is intended to be the **only** place in the pophys architecture that compares a schema version.

`CoreMetadata.load(input_dir)` performs the find-core-files and detect-version task once and returns a **frozen** object carrying `version`, `core_path`,
`core_raw` and the optional `platform_raw` / `subject_raw` / `data_description_raw` dicts.

Input files are read as **raw dicts**. Nothing here validates an input document just to read a field
from it, so metadata that is messy but sufficient still runs.

## The minimal `metadata.json`

This package also reads a `metadata.json`, for producers with no `aind-data-schema` core files at all.

Example:
```json
{
  "frame_rate": 9.48,
  "instrument_id": "MESO.1",
  "subject_id": "772414",
  "dataset_name": "single-plane-ophys_772414_2025-04-11_17-41-16",
  "excitation_nm": 920,
  "emission_nm": 514,
  "planes": [
    {
      "plane_index": 0,
      "structure": "VISp",
      "depth": 150,
      "depth_unit": "micrometer",
      "um_per_pixel": 0.78,
      "width": 512,
      "height": 512
    }
  ],
  "epochs": [
    {
      "stimulus_name": "spontaneous activity",
      "start_time": "2025-04-11T17:49:00-07:00",
      "tiff_stem": "spont",
      "modalities": ["None"]
    }
  ]
}
```

## Usage

Reading metadata — locate the file(s) once, then ask it for fields:

```python
from aind_pophys_metadata import CoreMetadata

metadata = CoreMetadata.load(input_dir)

frame_rate = metadata.get_frame_rate(required=True)
instrument_id = metadata.get_instrument_id()
fov_ids = metadata.get_fov_ids()
um_per_pixel = metadata.get_um_per_pixel()
planes = metadata.get_plane_records()
epochs = metadata.get_epoch_records()
```

Writing outputs — `stage_guard` emits a `processing.json` on both the success and the failure path:

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
