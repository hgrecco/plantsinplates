# Plants in Plates deep-dive guide

This is the operational reference for Plants in Plates. It covers installation,
input data, measurement settings, generated artifacts, development tasks, and
troubleshooting.

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
derived time-series values, see [math.md](math.md).

## Requirements

- [Pixi](https://pixi.sh/) 0.82.1 or later on your `PATH` to create the locked environment and
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

For a packaged installation, download `plantsinplates-<version>.zip` from
[GitHub Releases](https://github.com/hgrecco/plantsinplates/releases). Extract
the complete ZIP into a dedicated writable application folder, then run:

```console
pixi run --locked gui
```

Pixi installs Python and the locked dependencies on first launch. Keep the
bundle, `pixi.toml`, `pixi.lock`, and updater helper together. The included
`INSTALL.md` contains installation and recovery instructions. Use the installation
asset rather than GitHub's automatically generated source-code ZIP.

For development, clone the repository and use its locked environment:

```console
git clone https://github.com/hgrecco/plantsinplates.git
cd plantsinplates
pixi install --locked
pixi run gui-dev
```

For a source checkout, use `gui-dev`, which runs `__main__.py` from the working
tree. The `gui` task instead runs the bundled `plantsinplates-app` ZIP archive.
Run `pixi run build` before using bundled tasks in a source checkout.

## Application updates

Open **Preferences → Check for updates** to compare the installed version with
the latest stable GitHub release and open its release notes. Choose
**Update and restart** to download, verify, install, and reopen the selected
data folder. Installation is disabled while analysis, folder validation,
calibration audits, or template creation is active. Close other application
instances and batch jobs before updating.

The command-line equivalents are:

```console
pixi run --locked update-app --check
pixi run --locked update-app
```

`update` is an alias for `update-app`. Checks never install anything. Updates
are initiated explicitly and only consider newer stable date versions; drafts,
prereleases, and downgrades are excluded. No GitHub login is needed for downloads.
Development checkouts can check releases, but installation changes belong in Git.

The helper verifies the ZIP's SHA-256 checksum and its contents before replacing
application files. It runs in a separate Python environment after the application
exits, installs the locked dependencies, and verifies the bundle before restarting.
Inputs, completed runs, and reusable analysis caches are untouched. Backups and
logs stay in the application's `.plantsinplates_updates` directory.

If installation fails, the helper restores the previous application files and
environment. Read `.plantsinplates_updates/update.log` and `helper.log` if recovery
also fails. After closing all app instances, retry with:

```console
pixi run --locked update-app --recover
```

If mismatched environment files prevent Pixi from running, use the already
installed updater Python directly: `.pixi/envs/updater/bin/python plantsinplates-updater.py --recover` on macOS/Linux, or
`.pixi\envs\updater\python.exe plantsinplates-updater.py --recover` on Windows.
The installation's `INSTALL.md` also explains how to use the retained staging
helper if the wrapper itself was damaged. Keep backups until recovery succeeds.

Installations using the old university server require a one-time download and
installation of a GitHub release. Their updater cannot discover the new server.

## Quick start

Prepare an experiment or plate using the folder, workbook, image-path, and
calibration-file contract in [Data organization](data_organization.md). That
page is the canonical reference for input and managed-output placement.

Run the experiment from the command line:

```console
pixi run --locked batch /absolute/path/to/experiment_014
```

This creates one managed run directory in the selected experiment. To run one
plate only, pass its `plate_*` path. See
[Data organization](data_organization.md#managed-outputs-and-cache-directories)
for the exact placement.
For a source checkout, use `pixi run test <data-dir>` instead.

## Common usage

### GUI

```console
pixi run --locked gui /absolute/path/to/experiment_014
```

Use `gui-dev` instead of `gui` when editing a source checkout. The GUI has five stages: data folder and previous runs, calibration, method and
settings, run analysis, and results. The folder path can be pasted or selected
with the browser. **Open folder** opens even a structurally invalid selection,
and **Re-check** performs a fresh workbook/date/image-reference check after the
input has been repaired.

To prepare missing plate workbooks, select **Build info templates…**. It works
even when `info.xlsx` is missing or invalid. Selecting an experiment creates a
separate `info_template.xlsx` in each usable direct `plate_*` child; selecting a
plate creates just that plate's template. Existing `info.xlsx` files are left
untouched. Repeating the action asks before replacing any existing templates;
declining writes no files.

The scan fills image paths and infers positions from `fluo_<row>_<col>` filenames.
Genotype and externally measured root lengths remain blank. Unknown filenames
and images with conflicting positions get separate rows with blank positions.
Review **Show template details**, complete the template, and save it as
`info.xlsx`, then select **Re-check**. See
[Data organization](data_organization.md#generating-a-workbook-template) for the
matching rules and exclusions.

The interface uses `ttkbootstrap` with the light Litera theme for consistent
cross-platform controls, status colors, focus states, and spacing. Pixi installs
the themed widget dependency from the lockfile; no separate GUI package setup is
required.

Calibration is an explicit choice between per-image CZI metadata, one shared
`cal.txt` value, or one manually entered shared value. **Check image
calibrations** is optional and scans all referenced images without starting an
analysis. In metadata mode an unusable or non-square calibration causes only
that image to be skipped; it is recorded in the run's calibration report.
Visible size settings are micrometers and are converted separately for every
image when metadata calibration is selected.

The method-specific fields sit directly under the selected method. Before
starting, the GUI summarizes the image count, calibration/audit state, method,
reuse policy, and proposed output location. During measurement it reports
image-level progress and supports cancellation after the current image.
Previous runs are listed beside the folder selection, where their settings can
be inspected and explicitly loaded.

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

Choose cache behavior when required:

```console
pixi run test /absolute/path/to/plate_002_treatment --method centerline --reuse-policy none
```

`--reuse-policy` accepts `compatible`, `preprocessing`, or `none`. It never
deletes a previous run. Batch method settings remain pixel-based and therefore
do not require physical calibration.

### Outputs

Every invocation creates a unique, immutable
`_output_YYYYMMDD-HHMMSS-<id>` directory. It contains `run.json`,
`analysis.log`, `calibration_report.csv`, `dataframe.pickle`, `summary.xlsx`,
and `summary.pdf`. Experiment runs also contain plate-specific results below
`plates/<plate-name>/`. The complete directory trees are documented in
[Data organization](data_organization.md#managed-outputs-and-cache-directories).

The plate dataframe includes the supplied `length`, derived fluorescence signal,
per-plant differences and rates, such as `delta_length_per_day` and
`delta_signal_intensity_per_day`. Centerline modes also record centerline and
regional measurements. Calibration provenance and the resolved pixel
configuration are stored per image. Plate-only runs use the same file names at
the top of their unique run directory.

## Configuration

### Data layout

See [Data organization](data_organization.md) for the complete and authoritative
directory tree, naming conventions, `info.xlsx` schema, relative image paths,
validation rules, calibration-file placement, and managed output/cache layout.

### Calibration and environment

`DATA_DIR` is the only environment variable read by the CLI. It is an optional
alternative to the positional data-directory argument:

```console
DATA_DIR=/absolute/path/to/experiment_014 pixi run gui-dev
DATA_DIR=/absolute/path/to/plate_002_treatment pixi run test
```

The calibration source remains explicit: image metadata, a shared `cal.txt`, or
a manual shared value. See [Data organization](data_organization.md) for
`cal.txt` content and placement. Embedded CZI X/Y values must both be positive
and agree within 1%; greater anisotropy is unsupported by the current
square-pixel algorithms. The batch command does not read `cal.txt`, because its
method options are already pixels.

### Artifact reuse

Reusable work lives under `.plantsinplates_cache` in each plate. The default
**Reuse compatible work** policy can reuse preflight data, masks, centerlines,
and complete image measurements when their source signatures, calibration,
settings, and cache schema match. **Recompute measurements** reuses only
preprocessing. **Recompute everything** ignores the cache without deleting it.
Completed run directories are never cache inputs and are never overwritten.

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
│   ├── workflow.py             # validation, calibration, runs, progress models
│   └── visualize.py            # PDF report generation
├── docs/                       # user, mathematical, and data-structure docs
│   ├── data_organization.md    # canonical data-layout reference
│   ├── guide.md                # detailed operating guide
│   └── math.md                 # measurement-model summary
├── tests/                      # workflow and GUI decision-logic tests
├── pixi.toml                   # environments and task definitions
├── pixi.lock                   # locked dependency resolution
├── plantsinplates-app          # bundled application ZIP archive
└── .pre-commit-config.yaml     # formatting and lint hooks
```

## Development, testing, and formatting

Publishing requires Git push access to the repository. User updates only need
HTTPS access to GitHub and the locked package channels.

| Command | Purpose |
| --- | --- |
| `pixi run show-version` | Print the runtime version from the bundled application. |
| `pixi run gui` | Start the GUI from `plantsinplates-app`. |
| `pixi run gui-dev` | Start the GUI from the working-tree source. |
| `pixi run test <data-dir>` | Run batch analysis on an experiment or plate directory; this is not an automated test command. |
| `pixi run batch <data-dir>` | Run batch analysis from the installed application bundle. |
| `pixi run build` | Build the local bundle and the installation ZIP/checksums under `dist/`. |
| `pixi run publish` | Validate a clean, synchronized `main` checkout, create its version tag, and push it to GitHub. |
| `pixi run update` / `pixi run update-app` | Check, download, verify, and install a newer stable release using the updater environment. |
| `pixi run update-app --check` | Check for updates without installing. |
| `pixi run update-app --recover` | Restore the backup from an interrupted update. |
| `pixi run -e lint pre-commit-install` | Install repository pre-commit hooks. |
| `pixi run -e lint lint` | Run the configured pre-commit checks. |

The lint environment supplies Ruff, mdformat, Taplo, and pre-commit hooks. Run
the automated suite with `pixi run python -m unittest discover -s tests`, and
use a representative plate or experiment with the batch analysis command for
scientific workflow validation.

### Publishing a version

The version in `pixi.toml` is authoritative for the source, bundle, GUI, and
reports. Use `YYYY.MM.DD`, optionally followed by a positive same-day revision
such as `2026.11.20.1`. Choose a version newer than the latest published release.

```console
pixi workspace version set 2026.11.20
pixi lock
pixi run --locked python -m unittest discover -s tests
pixi run build
```

Review and commit the changes, push `main`, then run `pixi run publish`. You can
instead create and push the matching `v2026.11.20` Git tag yourself. The tag must
point to a commit on `main`. Pushing a tag is the publication action; building
locally does not publish anything.

GitHub Actions builds one reproducible bundle and tests that exact installation
ZIP on macOS ARM64, Linux x86-64, and Windows x86-64. Checks include a real Pixi
update that adds a dependency and restarts the application. The workflow creates
a draft, attaches the bundle, installation ZIP, and `SHA256SUMS`, and publishes
only after every platform passes. Published versions are never overwritten;
fix a failed published release by incrementing the version. Failed unpublished
drafts can be completed by rerunning their workflow.

The workflow uses GitHub's built-in token with write access only in the publishing
job. No SSH server or stored publishing token is required. Enable GitHub Actions
for the repository; the workflow declares its needed permissions.

To run the real updater integration locally, set
`PLANTSINPLATES_UPDATE_INTEGRATION=1` when running the automated suite. It needs
network access and creates temporary Pixi environments. GUI decision tests avoid
creating a window; manually inspect Preferences, progress, cancellation, and
resizing with `gui-dev` when changing the interface.

## Troubleshooting

### Folder or workbook validation fails

Use the validation checklist in [Data organization](data_organization.md) to
check folder prefixes, date keys, workbook column pairs, and relative image
paths. After repairing the dataset, select **Re-check** in the GUI.

### Results look stale or settings changed

Choose **Recompute measurements** or **Recompute everything** in the GUI, or
pass `--reuse-policy preprocessing`/`none` to the batch command. This creates a
new run and leaves earlier results untouched. Use **Clear reusable cache…** only
when the reusable preprocessing itself should be removed.

### The GUI does not start

Run on a graphical desktop with Tk support, or use the non-interactive batch
command. For a checkout, prefer `pixi run gui-dev` so the current source is
used instead of the bundled archive.

### Parameter units are unexpected

The GUI accepts visible size settings in micrometers and converts them with the
explicit metadata, `cal.txt`, or manual calibration source. The batch command
accepts the same settings in pixels. Do not copy a GUI value into the batch
command without performing that conversion.

## License

Plants in Plates is distributed under the [MIT License](../LICENSE).
