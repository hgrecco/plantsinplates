# Working on Plants in Plates

These instructions apply to the whole repository. Extend the existing application
with focused changes that follow nearby code and preserve its scientific behavior.

## Project overview

Plants in Plates is a Python 3.12 desktop and batch application for analyzing
individual-root fluorescence microscopy images across plates and dates. It combines
image-derived fluorescence measurements with root lengths supplied in `info.xlsx`,
then exports pandas dataframes, Excel workbooks, and PDF reports. Root length is
not calculated from the fluorescence images.

Pixi manages the runtime, development tools, and dependencies through `pixi.toml`
and `pixi.lock`. Supported platforms are macOS ARM64, Linux x86-64, and Windows
x86-64. The main libraries are NumPy, SciPy, scikit-image, pandas, BioIO/CZI,
Matplotlib, seaborn, openpyxl, Typer, Tkinter, and ttkbootstrap.

Read [README.md](README.md) for orientation,
[docs/guide.md](docs/guide.md) for operation and development,
[docs/data_organization.md](docs/data_organization.md) for the canonical input and
output contract, and [docs/math.md](docs/math.md) for measurement models. Check the
implementation before changing a scientific assumption.

## Code map and boundaries

| File | Responsibility |
| --- | --- |
| `__main__.py` | Typer commands, `AnalyzeApp`, five-stage GUI, configuration controls, and worker coordination. |
| `plantsinplates/workflow.py` | GUI-independent validation, calibration, request/result/progress dataclasses, run discovery, manifests, and cache management. Keep Tkinter out of this module. |
| `plantsinplates/workflow_chrome.py` | Reusable themed sidebar, footer, step/status models, and application icons. |
| `plantsinplates/measurement_config.py` | Frozen, validated `MeasurementConfig` and the supported method names. |
| `plantsinplates/analyze.py` | Preflight, measurement orchestration, unit conversion, cache reuse, dataframe derivation, and exports. `run_analysis()` is the shared entry point used by the current GUI and batch command. Older folder-analysis helpers also remain here. |
| `plantsinplates/imsingleroot.py` | Single-root segmentation, masks, skeletons, tip-box and centerline measurements, and preprocessing artifact validation. |
| `plantsinplates/immultiroot.py` | Multi-root utilities and centerline/profile helpers. |
| `plantsinplates/skeleton_utils.py` | Skeleton ordering and perpendicular intensity sampling. |
| `plantsinplates/io.py` | Image loading, centralized path builders, source signatures, artifact manifests, shared logger, and runtime `__version__`. |
| `plantsinplates/types.py` | NumPy image/coordinate aliases, runtime image guards, and measurement `TypedDict` schemas. |
| `plantsinplates/visualize.py` | Matplotlib/seaborn figures and PDF report pages. |
| `tests/` | Standard-library `unittest` coverage for workflow behavior and GUI decision logic. |
| `plantsinplates/assets/` | Existing application icons in multiple sizes. |

Keep new analysis and filesystem behavior in the shared modules so the GUI and
batch command behave consistently. Reuse existing models and path builders rather
than duplicating validation, configuration, or output naming in the UI.

## Development commands

Run commands from the repository root:

```console
pixi install -e default -e lint
pixi run gui-dev
pixi run gui-dev /absolute/path/to/experiment_014
pixi run test /absolute/path/to/plate_002_treatment --method centerline
pixi run python -m unittest discover -s tests
pixi run -e lint pre-commit run --files path/to/changed_file.py
pixi run build
```

`pixi run test` runs batch analysis on real input data; it is **not** the automated
test suite. `DATA_DIR` can supply the GUI/batch input path instead of the positional
argument. Use `gui-dev` while editing: `gui` and `show-version` read the generated
`plantsinplates-app` archive. `build` creates that ignored ZIP application from
`__main__.py`, `pixi.toml`, `pixi.lock`, and `plantsinplates/`.

The pre-commit hooks run whitespace/end-of-file fixes, Ruff lint fixes and
formatting, mdformat for Markdown, and Taplo for TOML. The first hook installs the
default and lint Pixi environments. Scope checks to changed files; avoid unrelated
formatting churn. `upload`/`publish` transfer the archive to the configured SSH
server; `update`/`update-app` download an archive and replace environment files.
Use those tasks only for an explicitly requested release or update.

## Python and documentation style

- Use four-space indentation, double-quoted strings, `snake_case` functions and
  variables, `PascalCase` classes, and uppercase module constants.
- Follow the surrounding module's import conventions. Existing aliases include
  `np`, `pd`, `skio`, and `ttk`; package modules generally use relative imports.
- Use Python 3.12 annotations: built-in generics, `X | None`, `Literal`, and
  `type` aliases. Reuse the image and measurement types in `types.py`.
- Prefer small functions for numerical/filesystem work and dataclasses for shared
  workflow state. Keep `MeasurementConfig` immutable and validate settings when
  constructing them. Private implementation helpers use a leading underscore.
- Write concise docstrings explaining purpose. For substantial numerical APIs,
  follow the existing NumPy-style `Parameters` and `Returns` sections; document
  units, array shapes, and coordinate conventions when relevant.
- Use `pathlib.Path` for filesystem operations and the shared `plantsinplates`
  logger (`io.logger`); reserve Typer output for CLI feedback.
- Keep user documentation in plain Markdown with working relative links. Update
  the relevant guide when changing inputs, settings, outputs, or measurement
  meaning. Manage dependency changes through Pixi and update the lockfile with it.

## GUI style and threading

Preserve the ttkbootstrap `litera` theme and the five stages: Data folder,
Calibration, Method, Run analysis, and Results. Reuse `WorkflowSidebar`,
`ApplicationFooter`, existing card/label helpers, semantic status styles, and
theme-derived colors. Use the existing `SPACE_XS/SM/MD/LG` spacing scale
(4/8/16/24), named/copied Tk fonts, and resizing layout conventions.

Keep controls and helper text concise, show only settings relevant to the chosen
method, and provide actionable validation feedback. Put expensive analysis and
inspection on worker threads. Schedule widget updates through `after(...)`, pass
structured `ProgressEvent` objects, and honor cancellation between images through
the existing `threading.Event` mechanism.

## Scientific and data contracts

- Preserve `experiment_*` / `plate_*` / `date_*` discovery and the first-sheet
  `info.xlsx` contract: `row`, `col`, `genotype`, and paired `<date>_length` /
  `<date>_fluo` columns. Image paths are relative to the matching date folder.
  Follow `docs/data_organization.md` for the full validation rules.
- Supported methods are `box`, `centerline`, and `centerline_gaussian`. Preserve
  their distinct signal definitions and existing dataframe columns/attributes
  unless the task intentionally changes them. Do not silently change thresholds,
  smoothing, fitted models, date conversion, or per-plant grouping.
- Preserve NumPy image dtype/shape guards and `(row, col)` image coordinates;
  convert explicitly where plots use `(x, y)`.
- Keep units explicit. The current GUI builds requests with micrometer settings
  resolved to pixels per image; the batch command uses pixel settings and pixel
  calibration. `box_offset` is dimensionless. CZI metadata calibration requires
  positive, finite X/Y values that agree within 1%; shared `cal.txt` and manual
  calibration must also be positive and finite.
- Every current workflow run creates a fresh `_output_<timestamp>-<id>` directory
  with `run.json`, logs, calibration report, and result artifacts. Preserve
  completed runs and source inputs. Experiment reports include per-plate outputs
  under `plates/<plate-name>/`.
- Reusable work belongs in each plate's `.plantsinplates_cache`, separate from
  completed runs. Honor `compatible`, `preprocessing`, and `none` reuse policies;
  `none` ignores existing caches without deleting them. Discovery must ignore
  managed cache and run directories.
- Cache reuse depends on source signatures, settings, calibration, and artifact
  versions. When algorithms or stored records change, update the relevant cache
  key/version (`MEASUREMENT_CACHE_VERSION`, `MASK_ARTIFACT_VERSION`, or
  `SKELETON_ARTIFACT_VERSION`) so stale results cannot be reused. Review
  `RUN_SCHEMA_VERSION` when changing run manifests.

## Extending and verifying behavior

For a new measurement setting or method, follow it through `MeasurementConfig`,
CLI options and GUI fields, request serialization and unit conversion,
measurement implementation, cache keys, dataframe exports, and report rendering.
Update the corresponding documentation and behavioral tests together.

Follow the existing `unittest.TestCase`, `tempfile.TemporaryDirectory`, openpyxl
workbook fixtures, and `unittest.mock` patterns. Test meaningful observable
behavior such as validation failures, calibration conversion, cache invalidation,
isolated run outputs, progress, and cancellation. GUI decision tests load
`__main__.py` with `importlib` and avoid constructing a real Tk window.

Run the automated suite for behavior changes. Numerical or report changes also
need a representative dataset or synthetic numerical case; the existing workflow
tests mock much of image measurement and plotting. For GUI changes, manually
inspect the affected stages and resizing in `gui-dev`. Report which checks ran
and any limits, such as unavailable microscopy data or a display.
