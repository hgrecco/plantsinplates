# Plants in Plates deep-dive guide

[← Return to the project overview](../README.md)

This is the operational reference for Plants in Plates. It covers installation,
input data, measurement settings, generated artifacts, development tasks, and
troubleshooting. For a concise, outcome-focused overview, start with the
[project README](../README.md).

## Capabilities

- Validates the correspondence between plate metadata, date folders, and
  referenced fluorescence images before measuring them.

- Reads `.czi` fluorescence images and builds reusable root-mask artifacts.

- Measures the largest segmented root with one of three methods:

  - `box`: foreground-minus-background intensity in a box near the root tip.
  - `centerline`: intensity integrated from perpendicular profiles along a
    smoothed centerline.
  - `centerline_gaussian`: centerline profiles summarized with Gaussian fits.

- Exports plate-level data as Excel and pickle files, and renders PDF reports
  with image/measurement views and time-series comparisons.

- Combines plate data into experiment-level pickle and PDF summaries.

- Reuses masks, centerlines, preflight results, and dataframes when the source
  files and relevant measurement settings still match.

Root length is **not** measured from the microscopy image by this code. The
`length` values used in rate calculations come from `info.xlsx`.

For the mathematical description of the implemented measurement models and
derived time-series values, see [MATH.md](MATH.md).

## Status

No formal stability, support, or release policy is included in this repository.
The runtime reports version `2026.04.15` with `pixi run show-version`,
while the Pixi project metadata is `0.1.0`; the repository does not explain this
version difference.

## Requirements

- [Pixi](https://pixi.sh/) on your `PATH` to create the locked environment and
  run project tasks.
- One of the platforms listed in `pixi.toml`: macOS on Apple Silicon, Linux
  x86_64, or Windows x86_64.
- Python 3.12; Pixi installs the pinned runtime and declared packages,
  including BioIO/CZI support, NumPy, SciPy, scikit-image, pandas, Matplotlib,
  seaborn, and openpyxl.
- A graphical desktop with Tk support to use the GUI. The batch command does
  not open the GUI.
- Plate metadata in `.xlsx` format and referenced fluorescence images in
  lowercase `.czi` files.

There is no Python package build configuration in the repository, so installing
this project with `pip install` is not a documented workflow.

## Installation

Clone the repository and let Pixi resolve the lockfile:

```console
git clone <repository-url>
cd plantsinplates
pixi install
pixi run show-version
```

The repository does not record a canonical remote URL, so replace
`<repository-url>` with the source you were given.

For a source checkout, use `gui-dev`, which runs `__main__.py` from the working
tree. The `gui` task instead runs the bundled `plantsinplates-app` ZIP archive.

## Quick start

Create an experiment directory whose names and workbook date prefixes match.
For example:

```text
experiment_014/
├── cal.txt                                # optional: one positive µm/pixel value
└── plate_002_treatment/
    ├── info.xlsx
    └── date_20250613.0930/
        ├── overview.png                   # optional plate overview
        └── row_001/
            └── fluo_1_1.czi
```

The first worksheet in `info.xlsx` needs at least `row`, `col`, and `genotype`,
plus paired date columns. The date prefix must match the identifier after
`date_` in the corresponding directory.

| row | col | genotype | 20250613.0930_length | 20250613.0930_fluo |
| --- | --- | --- | ---: | --- |
| 1 | 1 | 606-CC1 | 12.4 | `row_001/fluo_1_1.czi` |

The fluorescence path is relative to its `date_*` directory and must use
forward slashes (`/`). Date keys use `YYYYMMDD` and may include `.HHmm`, such
as `20250613.0930`.

Run the experiment from the command line:

```console
pixi run test /absolute/path/to/experiment_014
```

This creates plate outputs under each `plate_*` directory and merged outputs in
the experiment directory. To run one plate only, pass its `plate_*` path.

## Common usage

### GUI

```console
pixi run gui-dev /absolute/path/to/experiment_014
```

The GUI guides you through choosing a valid `experiment_*` or `plate_*` folder,
confirming image calibration, choosing a measurement method, and adjusting only
the settings relevant to that method. Select **Analyze experiment** or
**Analyze plate** when the required inputs are valid. Size settings are shown in
micrometers and converted to pixels using the displayed calibration. A valid
`cal.txt` is read-only; otherwise the calibration can be entered manually.

The **Reuse valid previous results** option keeps compatible output artifacts;
turn it off to force recalculation. **Delete previous output…** shows every
generated artifact for confirmation before removing it. After a run, the
Results panel provides the output path, available Excel/PDF actions, and a
collapsible Technical log for detailed diagnostics.

### Batch analysis

The CLI command is named `test`, but it is an analysis runner rather than an
automated test suite:

```console
pixi run test /absolute/path/to/plate_002_treatment --method centerline --perpendicular-width 50 --length 50 --savgol-window 25 --intensity-savgol-window 25
```

Batch option values are pixels. The available methods and their defaults are:

| Method | Relevant options | Defaults |
| --- | --- | --- |
| `box` | `--box-size`, `--box-offset` | `680`, `0.0` |
| `centerline` | `--perpendicular-width`, `--length`, `--savgol-window`, `--intensity-savgol-window` | `50`, `50`, `25`, `25` |
| `centerline_gaussian` | Same centerline options | `50`, `50`, `25`, `25` |

`--box-offset` is expressed in box-size units: `0` keeps the box centered at
the tip, positive values shift it toward the tip direction, and negative values
shift it away. Set `--intensity-savgol-window 0` to disable longitudinal profile
smoothing.

Force new artifacts and summaries when required:

```console
pixi run test /absolute/path/to/plate_002_treatment --method centerline --force-recompute
```

For the batch command, `--force-recompute` first deletes all generated
`_output_*` entries below the target directory. Treat it as a destructive
operation on generated results.

### Outputs

Analysis writes generated files with the `_output_` prefix:

```text
experiment_014/
├── _output_df.pickle                      # merged experiment dataframe
├── _output_summary.pdf                    # experiment report
└── plate_002_treatment/
    ├── _output_df.pickle                  # plate dataframe
    ├── _output_manifest.json              # image artifact cache metadata
    ├── _output_preflight.pickle           # metadata/filesystem matching result
    ├── _output_summary.pdf                # plate report
    ├── _output_summary.xlsx               # plate table for Excel
    └── date_20250613.0930/row_001/
        ├── _output_mask/fluo_1_1.png      # root mask
        └── _output_skeleton/fluo_1_1.png  # centerline modes only
```

The plate dataframe includes the supplied `length`, derived fluorescence signal,
per-plant differences and rates, such as `delta_length_per_day` and
`delta_signal_intensity_per_day`. Centerline modes also record centerline and
regional measurements. A GUI run additionally saves its visible log as
`_output_log.txt` in the selected folder.

## Configuration

### Data layout

The source code expects the following naming convention:

- `experiment_<number>[_description]` contains one or more `plate_*`
  directories.
- `plate_<number>[_description]` contains `info.xlsx` and one or more
  `date_<date-key>[_description]` directories.
- Each date key must match the prefix of the paired `<date-key>_length` and
  `<date-key>_fluo` columns in `info.xlsx`. In practice, use names such as
  `20250613` or `20250613.0930`.
- The preflight scan matches referenced CZI files by their path relative to the
  date directory. Unreferenced or missing files are reported in the preflight
  data rather than measured.
- An optional `overview.jpg`, `.jpeg`, `.tiff`, `.tif`, or `.png` in a date
  directory is included in the plate report when found.

The corresponding English input-structure reference is
[estructura.docx](estructura.docx). `Datos VAMP721.docx` contains
experiment-specific background, not a general installation guide.

### Calibration and environment

`DATA_DIR` is the only environment variable read by the CLI. It is an optional
alternative to the positional data-directory argument:

```console
DATA_DIR=/absolute/path/to/experiment_014 pixi run gui-dev
DATA_DIR=/absolute/path/to/plate_002_treatment pixi run test
```

An optional `cal.txt` can contain exactly one positive finite number representing
micrometers per pixel. The GUI looks first in the selected folder; when a plate
is selected, it falls back to `cal.txt` in its parent `experiment_*` folder. A
valid calibration is read-only in the GUI; otherwise the default `1.0` is
editable. The batch command does not read `cal.txt`, because its measurement
options are already expressed in pixels.

### Artifact reuse

Artifact reuse is enabled by default. Root masks and centerlines are reused only
when their source file size/modification time and relevant artifact settings
match. Cached plate dataframes are reused only when the saved measurement
configuration matches the active configuration. Disable reuse with
`--force-recompute` in the CLI or the GUI checkbox.

## Repository structure

```text
.
├── __main__.py                 # Typer CLI and Tkinter application
├── plantsinplates/
│   ├── analyze.py              # preflight, measurement orchestration, exports
│   ├── imsingleroot.py         # root segmentation and measurement methods
│   ├── immultiroot.py          # centerline helpers and multi-root utilities
│   ├── io.py                   # image I/O, output naming, artifact cache
│   ├── measurement_config.py   # validated measurement configuration
│   └── visualize.py            # PDF report generation
├── docs/                       # user, mathematical, and data-structure docs
│   ├── MATH.md                 # measurement-model summary
│   ├── estructura.docx         # English input-structure reference
│   └── guide.md                # detailed operating guide
├── examples/                   # exploratory Jupyter notebooks
├── pixi.toml                   # environments and task definitions
├── pixi.lock                   # locked dependency resolution
├── plantsinplates-app          # bundled application ZIP archive
└── .pre-commit-config.yaml     # formatting and lint hooks
```

The notebooks are examples and exploratory work, not a supported test suite.
Several use local, machine-specific paths or imports that are not declared in
`pixi.toml`; use `examples/new_format.ipynb` as the closest example of the
current plate-analysis workflow.

## Development, testing, and formatting

All defined Pixi tasks are listed below. Commands that upload, publish, or
update application files need the appropriate external server access.

| Command | Purpose |
| --- | --- |
| `pixi run show-version` | Print the runtime version from the bundled application. |
| `pixi run gui` | Start the GUI from `plantsinplates-app`. |
| `pixi run gui-dev` | Start the GUI from the working-tree source. |
| `pixi run test <data-dir>` | Run batch analysis on an experiment or plate directory; this is not an automated test command. |
| `pixi run build` | Rebuild `plantsinplates-app` as a ZIP archive. |
| `pixi run upload` | Copy the bundled archive to the configured `df` SSH destination. |
| `pixi run publish` | Run `build`, then `upload`. |
| `pixi run update` | Download an update archive to `.tmp.new_version` and extract its `pixi.toml`/`pixi.lock`. |
| `pixi run update-app` | Show version, update, then show version again. |
| `pixi run -e lint pre-commit-install` | Install repository pre-commit hooks. |
| `pixi run -e lint lint` | Run the configured pre-commit checks. |

The lint environment supplies Ruff, mdformat, Taplo, and pre-commit hooks. No
`tests/` directory, pytest configuration, or automated unit/integration test
command is present. Use a representative plate or experiment with the batch
analysis command to validate a workflow change.

## Troubleshooting

### No matching date columns

The date key after `date_` must be identical to the prefix of both workbook
columns. For example, `date_20250613.0930` needs
`20250613.0930_length` and `20250613.0930_fluo`.

### Images are not measured

Check that each non-empty `*_fluo` cell names an existing lowercase `.czi` file
relative to its date directory, such as `row_001/fluo_1_1.czi`. Use forward
slashes in the workbook path. The analyzer logs an error per image and continues
with the remaining records.

### Results look stale or settings changed

Use `--force-recompute` for the batch command, or disable **Reuse output
artifacts** in the GUI. If needed, use the GUI's **Delete previous output**
button; it removes generated entries beginning with `_output_` below the chosen
folder.

### The GUI does not start

Run on a graphical desktop with Tk support, or use the non-interactive batch
command. For a checkout, prefer `pixi run gui-dev` so the current source is
used instead of the bundled archive.

### Parameter units are unexpected

The GUI accepts the visible size settings in micrometers and converts them with
`cal.txt` or the editable calibration field. The batch command accepts the same
settings in pixels. Do not copy a GUI value into the batch command without
performing that conversion.

## Contributing

No contribution guide, code-of-conduct file, or canonical issue/merge-request
location is included. If you are working with the repository maintainers, keep
changes focused, run the available lint command, and validate analysis on a
representative data directory before proposing a change.

## License

Plants in Plates is distributed under the [MIT License](../LICENSE).
