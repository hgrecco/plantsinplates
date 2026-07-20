# Data organization

This is the canonical reference for arranging Plants in Plates input data and
understanding where managed outputs are written. The application can analyze a
whole experiment or one plate, but the selected folder and everything below it
must follow the same naming and workbook contract.

## Recommended layout

```text
experiment_014/
├── cal.txt                                  # optional shared GUI calibration
├── plate_001_control/
│   ├── info.xlsx
│   ├── date_20250613/
│   │   ├── overview.png                     # optional plate overview
│   │   └── row_001/
│   │       └── fluo_1_1.czi
│   └── date_20250614.0930_after-treatment/
│       └── row_001/
│           └── fluo_1_1.czi
└── plate_002_treatment/
    ├── info.xlsx
    └── date_20250613/
        └── row_001/
            └── fluo_1_1.czi
```

Date folders may be nested below a plate, but keeping them directly inside the
plate makes the dataset easier to inspect. Image subdirectories such as
`row_001` are optional; their names become part of the relative image path in
`info.xlsx`.

## Folder names and identifiers

Plants in Plates recognizes folder types by their lowercase prefixes.

- An experiment folder begins with `experiment_` and contains one or more
  direct child directories beginning with `plate_`.
- A plate folder begins with `plate_` and contains `info.xlsx` plus the date
  directories used by that workbook.
- A date directory begins with `date_`. The date key is the text after `date_`
  and before the next underscore.
- A descriptive suffix may follow an identifier, such as
  `experiment_014_long-roots`, `plate_002_treatment`, or
  `date_20250613_control`.

The token immediately after `plate_` becomes the plate identifier in generated
data and reports, so keep it stable and unique within an experiment.

Use a numeric date key in `YYYYMMDD` form. If measurements within a day need a
time component, append `.HHmm`, for example `20250613.0930`. The same complete
key must be used in the folder and workbook columns.

| Directory | Date key | Matching workbook columns |
| --- | --- | --- |
| `date_20250613` | `20250613` | `20250613_length`, `20250613_fluo` |
| `date_20250613.0930_control` | `20250613.0930` | `20250613.0930_length`, `20250613.0930_fluo` |

Each date key must identify exactly one date directory within a plate. Duplicate
directories with the same key, missing directories, and date directories with
no matching workbook columns fail validation.

## The `info.xlsx` workbook

Every plate has its own `info.xlsx`. The analyzer reads the first worksheet,
and each row represents one plant position.

### Required identifier columns

The following column names are lowercase and exact:

- `row`: plant row position on the plate.
- `col`: plant column position on the plate.
- `genotype`: genotype, line, or experimental group assigned to that plant.

The `row`, `col`, and `genotype` values identify a plant across measurement
dates. Additional metadata columns may remain in the workbook, but the current
analysis imports only these identifiers and the date-specific columns described
below.

### Date-specific column pairs

Every measurement date needs both of these columns:

- `<date-key>_length`: externally measured root length for that plant and date.
  Plants in Plates does not derive root length from the fluorescence image.
- `<date-key>_fluo`: path to the fluorescence image, relative to the matching
  date directory.

At least one complete `_length`/`_fluo` pair is required. A missing partner
column fails validation. A blank `_fluo` cell means that no fluorescence image
is scheduled for that plant and date.

Example:

| row | col | genotype | 20250613_length | 20250613_fluo | 20250614.0930_length | 20250614.0930_fluo |
| ---: | ---: | --- | ---: | --- | ---: | --- |
| 1 | 1 | control | 12.4 | `row_001/fluo_1_1.czi` | 15.8 | `row_001/fluo_1_1.czi` |
| 1 | 2 | treatment | 11.7 | `row_001/fluo_1_2.czi` | 14.1 | `row_001/fluo_1_2.czi` |

## Image paths and files

Values in `_fluo` columns are relative to the corresponding date directory.
Use forward slashes (`/`) in the workbook on every operating system.

```text
Full file:
/data/experiment_014/plate_001_control/date_20250613/row_001/fluo_1_1.czi

Workbook value:
row_001/fluo_1_1.czi
```

Do not use an absolute path or `..` parent references in a workbook cell. A
referenced file that does not exist fails folder validation. Recognized image
extensions are `.czi`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, and `.png`; embedded
per-image calibration is currently available only for CZI images.

An image found under a date directory but absent from `info.xlsx` is reported as
unreferenced and ignored. This does not invalidate an otherwise consistent
plate. Files whose names begin with `overview` are reserved for the optional
plate overview and are not treated as root images.

## Optional overview images

A date directory may contain one overview image for reports. Name it with the
`overview` prefix and use `.jpg`, `.jpeg`, `.tif`, `.tiff`, or `.png`, for
example `overview.png` or `overview_plate-001.jpg`. Use lowercase extensions for
portable behavior.

## Calibration-file placement

`cal.txt` is optional and is used only when the GUI's shared `cal.txt`
calibration source is selected. It must contain exactly one non-empty line with
a positive, finite micrometers-per-pixel value:

```text
0.325
```

- When an experiment is selected, place `cal.txt` in the experiment directory.
- When a plate is selected, a `cal.txt` in the plate takes precedence; otherwise
  the GUI checks its parent experiment directory.
- Image-metadata mode and manual calibration do not read this file.
- Batch settings are expressed in pixels and do not use `cal.txt`.

## What validation checks

Before analysis, the GUI and analysis workflow verify that:

1. The selected folder exists and begins with `experiment_` or `plate_`.
1. An experiment has at least one direct `plate_*` child.
1. Every plate has a readable `info.xlsx` with the required identifier columns.
1. Date-specific `_length` and `_fluo` columns occur in matched pairs.
1. Every workbook date has exactly one matching `date_*` directory, with no
   unexpected date directories.
1. Every non-empty referenced image path exists.

The validation details also list unreferenced images. After repairing a folder,
use **Re-check** in the GUI to force a fresh validation.

## Managed outputs and cache directories

Input files are never placed inside a run directory. Every analysis creates a
new immutable directory named `_output_YYYYMMDD-HHMMSS-<8-character-id>` in the
selected plate or experiment.

For a plate run:

```text
plate_001_control/
├── info.xlsx
├── date_20250613/
├── .plantsinplates_cache/
└── _output_20260717-153012-a1b2c3d4/
    ├── run.json
    ├── analysis.log
    ├── calibration_report.csv
    ├── dataframe.pickle
    ├── summary.xlsx
    └── summary.pdf
```

For an experiment run, the run directory is created in the experiment and the
plate-specific reports are nested below `plates/<plate-name>/`:

```text
experiment_014/
└── _output_20260717-153012-a1b2c3d4/
    ├── run.json
    ├── analysis.log
    ├── calibration_report.csv
    ├── dataframe.pickle
    ├── summary.xlsx
    ├── summary.pdf
    └── plates/
        ├── plate_001_control/
        └── plate_002_treatment/
```

Reusable masks, centerlines, preflight information, and measurement caches live
under `.plantsinplates_cache` in each plate. The cache may be cleared without
deleting input data or previous run directories. Folder validation ignores
managed run and cache contents when scanning dates and images.

## Preflight checklist

Before selecting a dataset, confirm that:

- Folder prefixes and date keys use the documented lowercase spelling.
- Every plate has its own `info.xlsx`.
- The workbook's first worksheet contains `row`, `col`, and `genotype`.
- Every date has a complete `_length`/`_fluo` column pair and one matching date
  directory.
- Workbook image paths use forward slashes and resolve from their date
  directories.
- Optional `cal.txt` and overview files follow their placement and content
  rules.

For calibration choices, analysis settings, reuse policies, and running the
application, continue with the [deep-dive guide](guide.md).
