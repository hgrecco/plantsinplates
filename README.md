# Plants in Plates

Turn organized fluorescence microscopy images into a clear view of root signal
and growth over time.

Plants in Plates helps plant researchers analyze individual-root fluorescence
images across plates and dates, then share the result as Excel tables and PDF
reports. It combines the fluorescence signal measured from each image with the
root lengths already recorded in the plate workbook.

## What it can do for you

- **Connect fluorescence to growth.** Compare image-derived root-tip signal,
  supplied root length, and their changes per day for every plant.
- **Turn a folder of images into reviewable results.** The application checks
  that your plate workbook, date folders, and referenced CZI images agree
  before it measures them.
- **Use the measurement approach that fits your experiment.** Choose a simple
  tip box, a centerline profile, or a Gaussian-based centerline profile.
- **Make reruns less tedious.** Reuse valid masks, centerlines, and analysis
  artifacts when the source images and measurement settings have not changed.
- **Hand results to collaborators.** Produce plate-level Excel workbooks and
  visual PDF summaries, plus a merged experiment report.

## A workflow built around your experiment

1. Organize the experiment as `experiment_*` → `plate_*` → `date_*`, with
   an `info.xlsx` workbook in each plate folder.
1. Open the desktop application, select a plate or whole experiment, choose a
   measurement method, and analyze it.
1. Review the generated `_output_summary.xlsx` and `_output_summary.pdf`
   files, or use the pickle dataframes in a notebook.

The software reads individual fluorescence images referenced as `.czi` files.
It does not calculate root length from those images: length is supplied in
`info.xlsx`.

## Start here

Install the locked Pixi environment, then open the source version of the GUI:

```console
pixi install
pixi run gui-dev /absolute/path/to/experiment_014
```

Prefer a non-interactive run? Analyze a whole experiment or one plate directly:

```console
pixi run test /absolute/path/to/experiment_014
```

Despite its current name, `test` is the batch analysis command, not an
automated test suite.

## Go deeper

- [Deep-dive guide](docs/guide.md): installation, input workbook and folder
  contract, calibration, measurement options, outputs, caches, all Pixi tasks,
  troubleshooting, and contributing.
- [Mathematical summary](docs/MATH.md): measurement models and derived
  quantities.
- [Input-structure reference](docs/estructura.docx): data-layout reference.

## Project status and license

The repository includes a working application, but no formal stability, support,
contribution, or release policy. Its runtime reports version `2026.04.15`;
Pixi's project metadata lists `0.1.0`.

Plants in Plates is available under the [MIT License](LICENSE).
