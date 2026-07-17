import hashlib
import json
import logging
import math
import pathlib
import pickle
import threading
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from typing import Any, Literal

from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd

from . import io
from . import imsingleroot
from . import workflow
from .measurement_config import MeasurementConfig
from . import visualize

THRESHOLD = 100
CENTERLINE_METHODS = {"centerline", "centerline_gaussian"}
CENTERLINE_REGION_COLUMNS = [
    "skel_cap_present",
    "skel_cap_start_idx",
    "skel_cap_end_idx",
    "skel_cap_mean",
    "skel_cap_integrated",
    "skel_cap_count",
    "skel_tip_start_idx",
    "skel_tip_end_idx",
    "skel_tip_mean",
    "skel_tip_integrated",
    "skel_tip_count",
    "skel_middle_start_idx",
    "skel_middle_end_idx",
    "skel_middle_mean",
    "skel_middle_integrated",
    "skel_middle_count",
    "skel_far_start_idx",
    "skel_far_end_idx",
    "skel_far_mean",
    "skel_far_integrated",
    "skel_far_count",
]
CENTERLINE_GAUSSIAN_COLUMNS = [
    "skel__peak_coordinates",
    "skel__peak_left_coordinates",
    "skel__peak_right_coordinates",
    "skel__gauss_peak",
    "skel__gauss_offset",
    "skel__gauss_sigma",
]


def date_to_float(s: str) -> Fraction:
    value = Fraction(s)
    days = int(value)
    rest = (value - days) * 10_000
    if rest >= 2400:
        raise Exception(
            f"The hour/minute part must be four digits (HHmm), and smaller than 2359. Found {rest} in {s}"
        )
    return days + rest / 2400


def day_number(days: Decimal, min_level: Literal["h", "m"] | None = None) -> str:
    int_days = int(days)
    rest = days - int_days

    current = str(int_days) + "d"

    if rest < 1 / 24 / 60 and min_level is None:
        return current

    hours = rest * 24
    int_hours = int(hours)
    rest = hours - int_hours

    current += f"{int_hours:02d}h"

    if rest < 1 / 60 and min_level in (None, "h"):
        return current

    minutes = rest * 60
    int_minutes = int(minutes)
    rest = minutes - int_minutes

    current += f"{int_minutes:02d}m"
    return current


def summarize_unique_values(
    df: pd.DataFrame, column: str, *, max_items: int = 6
) -> str:
    """Summarize unique values in a column as count + preview."""
    if column not in df.columns:
        return "n/a"

    values = sorted(df[column].dropna().astype(str).unique().tolist())
    if not values:
        return "0"

    preview = ", ".join(values[:max_items])
    if len(values) > max_items:
        preview += f", ... (+{len(values) - max_items} more)"

    return f"{len(values)} ({preview})"


def build_analysis_details(
    analysis_type: Literal["experiment", "plate"],
    analysis_path: pathlib.Path,
    pdf_path: pathlib.Path,
    df: pd.DataFrame,
    measurement_config: MeasurementConfig,
    *,
    dataframe_source: Literal["computed", "cache", "merged"] = "computed",
) -> list[tuple[str, str]]:
    """Build analysis metadata shared by logs and PDF details page."""
    return [
        ("Analysis type", analysis_type),
        ("Analysis path", str(analysis_path)),
        ("Dataframe source", dataframe_source),
        ("Rows analyzed", str(len(df))),
        ("Measurement method", measurement_config.method),
        (
            "Measurement config",
            json.dumps(measurement_config.to_dict(), sort_keys=True),
        ),
        ("Plates", summarize_unique_values(df, "plate")),
        ("Dates", summarize_unique_values(df, "date")),
        ("Genotypes", summarize_unique_values(df, "genotype")),
        ("Output PDF", str(pdf_path)),
    ]


def log_analysis_details(details: list[tuple[str, str]]) -> None:
    """Log analysis metadata to the UI/file log."""
    io.logger.info("Analysis details:")
    for key, value in details:
        io.logger.info(f"  {key}: {value}")


def diff(
    df: pd.DataFrame, group_by: list[str], numerator_col: str, denominator_col: str
) -> pd.DataFrame:
    """Compute per-group difference of a numerator column normalized by denominator difference.

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame.
    group_by : list of str
        Columns to group by.
    numerator_col : str
        Column to compute differences on.
    denominator_col : str
        Column for denominator of difference ratio.

    Returns
    -------
    pd.DataFrame
        Series with computed difference ratios.
    """
    return df.groupby(group_by)[numerator_col].transform(
        lambda x: x.diff() / df.loc[x.index, denominator_col].diff()
    )


def melt_many(
    adf: pd.DataFrame,
    id_vars: list[str] = ["row", "col", "genotype"],
    var_name: str = "date",
    var_suffixes: list[str] = ["length", "fluo"],
) -> pd.DataFrame:
    """Melt wide-format DataFrame into long format for multiple variable suffixes.

    Parameters
    ----------
    adf : pd.DataFrame
        Input wide-format DataFrame.
    id_vars : list of str, optional
        Columns to keep fixed during melt.
    var_name : str, optional
        Name of the variable column in long format.
    var_suffixes : list of str, optional
        List of suffixes to melt separately.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame with columns for each suffix.
    """
    dfs: list[pd.DataFrame] = []
    for var_suffix in var_suffixes:
        value_vars = [col for col in adf.columns if col.endswith("_" + var_suffix)]

        tmp = adf.melt(
            id_vars=id_vars,
            value_vars=value_vars,
            var_name=var_name,
            value_name=var_suffix,
        )

        tmp["date"] = tmp["date"].str.replace("_" + var_suffix, "")

        dfs.append(tmp)

    df, dfs = dfs[0], dfs[1:]
    for nextdf in dfs:
        df = df.merge(nextdf, on=id_vars + [var_name], how="outer")

    return df


def prepare_info(
    info: pd.DataFrame,
    *,
    id_vars: list[str] = ["row", "col", "genotype"],
    var_name: str = "date",
    var_suffixes: list[str] = ["length", "fluo"],
) -> pd.DataFrame:
    """Prepare and validate info DataFrame, transforming to long format.

    Parameters
    ----------
    info : pd.DataFrame
        Input DataFrame with info.
    id_vars : list of str, optional
        Columns identifying each record.
    var_name : str, optional
        Name of the variable column.
    var_suffixes : list of str, optional
        Suffixes for columns to extract.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame with extracted and renamed columns.
    """
    for col in id_vars:
        if col not in info.columns:
            io.logger.error(f"Column '{col}' not found in info.")

    for suffix in var_suffixes:
        if not any(info.columns.str.endswith("_" + suffix)):
            io.logger.error(f"No columns found ending with '_{suffix}' in info.")

    long_df = melt_many(
        info, id_vars=id_vars, var_name=var_name, var_suffixes=var_suffixes
    )

    long_df["fluo"] = long_df["fluo"].replace(np.nan, pd.NA)

    long_df["fluo"] = long_df["fluo"].map(
        lambda x: x
        if pd.isna(x)
        else str(pathlib.Path(pathlib.PurePosixPath(str(x)).as_posix()))
    )

    return long_df


def preflight_plate(plate_dir: pathlib.Path, long_df: pd.DataFrame) -> dict[str, Any]:
    """Prepare preflight dictionary summarizing a plate folder's contents.

    Parameters
    ----------
    plate_dir : pathlib.Path
        Path to the plate folder.
    long_df : pd.DataFrame
        Long-format DataFrame for the plate.

    Returns
    -------
    dict of str to Any
        Dictionary summarizing the plate and its dates.
    """
    prefix, plate_number, *_ = plate_dir.name.split("_")

    if prefix != "plate":
        io.logger.error(
            f"Invalid plate directory name: {plate_dir.name}. Expected 'plate_<number>_...'"
        )

    in_fs = {
        date_dir.name.split("_")[1]: date_dir
        for date_dir in plate_dir.rglob("date_*")
        if date_dir.is_dir()
        and workflow.CACHE_DIRECTORY not in date_dir.relative_to(plate_dir).parts
        and not any(
            part.startswith(workflow.RUN_PREFIX)
            for part in date_dir.relative_to(plate_dir).parts
        )
    }
    in_df: set[str] = set(long_df["date"].unique())

    _in_both = in_df.intersection(in_fs.keys())
    only_in_fs = set(in_fs.keys()) - in_df
    only_in_df = in_df - set(in_fs.keys())

    dates = {}
    for date, gdf in long_df.groupby("date"):
        if date in in_fs:
            dates[date] = {
                **preflight_date(in_fs[date], gdf),
                "path": in_fs[date],
                "date": date,
            }

    return {
        "plate": plate_number,
        "only_in_fs": sorted(only_in_fs),
        "only_in_df": sorted(only_in_df),
        "dates": dates,
    }


def preflight_date(date_dir: pathlib.Path, long_df: pd.DataFrame) -> dict[str, Any]:
    """Prepare preflight dictionary summarizing a date folder's contents.

    Parameters
    ----------
    date_dir : pathlib.Path
        Path to the date folder.
    long_df : pd.DataFrame
        Long-format DataFrame for the date.

    Returns
    -------
    dict of str to Any
        Dictionary summarizing files and missing data for the date.
    """
    in_fs = {
        str(fluo_file.relative_to(date_dir)): fluo_file
        for fluo_file in date_dir.rglob("*")
        if fluo_file.is_file()
        and fluo_file.suffix.lower() in io.FLUO_SUFFIXES
        and not fluo_file.stem.startswith(".")
        and not fluo_file.stem.lower().startswith("overview")
        and not any(
            part.startswith(workflow.RUN_PREFIX)
            for part in fluo_file.relative_to(date_dir).parts
        )
    }

    in_df: set[str] = set(long_df["fluo"][~long_df["fluo"].isna()].unique())

    in_both = in_df.intersection(in_fs.keys())
    only_in_fs = set(in_fs.keys()) - in_df
    only_in_df = in_df - set(in_fs.keys())

    fluo = {}
    for k, row in long_df.iterrows():
        if row["fluo"] in in_both:
            fluo[row["fluo"]] = {
                **row.to_dict(),
                "path": in_fs[row["fluo"]],
            }

    return {
        "only_in_fs": sorted(only_in_fs),
        "only_in_df": sorted(only_in_df),
        "overview_path": io.find_file_with_extension(
            date_dir, "overview*", io.OVERVIEW_SUFFIXES
        ),
        "fluos": fluo,
    }


def measure_plate(
    preflight_dict: dict[str, Any],
    measurement_config: MeasurementConfig = MeasurementConfig(),
    reuse_artifacts: bool = True,
) -> list[dict[Any, Any]]:
    """Measure all valid root images for a plate.

    Parameters
    ----------
    preflight_dict : dict of str to Any
        Preflight summary dictionary for the plate.

    Returns
    -------
    list of dict
        List of measurement records.
    """
    records: list[dict[Any, Any]] = []
    for date, date_record in preflight_dict["dates"].items():
        for _name, fluo_record in date_record["fluos"].items():
            try:
                io.logger.info(f"Analyzing {fluo_record['path']}")
                rec = imsingleroot.measure_image(
                    fluo_record["path"],
                    measurement_config=measurement_config,
                    reuse_artifacts=reuse_artifacts,
                )
            except Exception as ex:
                io.logger.error(f"Could not measure {fluo_record['path']}: {ex}")
                rec = None
            if rec:
                records.append(
                    {
                        **fluo_record,
                        **rec,
                        "date": date,
                    }
                )

    return records


def analyze_experiment_folder(
    experiment_path: pathlib.Path,
    measurement_config: MeasurementConfig = MeasurementConfig(),
    reuse_artifacts: bool = True,
) -> None | pathlib.Path:
    """Analyze all plates in an experiment folder.

    Parameters
    ----------
    experiment_path : pathlib.Path
        Path to the experiment folder.
    """
    request = workflow.AnalysisRequest(
        input_path=experiment_path,
        input_kind="experiment",
        settings=measurement_config,
        settings_unit="px",
        calibration=workflow.CalibrationSpec("pixels"),
        reuse_policy="compatible" if reuse_artifacts else "none",
    )
    result = run_analysis(request)
    return result.outputs.get("Data table")

    # Historical in-place implementation retained below for the moment as a
    # readable migration reference.  New callers always return above.
    io.logger.info(f"Analyzing experiment folder: {experiment_path.name}")
    io.logger.info(f"Artifact reuse is {'enabled' if reuse_artifacts else 'disabled'}")
    df_paths: list[pathlib.Path | None] = []
    for plate_dir in sorted(experiment_path.glob("plate_*")):
        if not plate_dir.is_dir():
            continue
        df_paths.append(
            analyze_plate_folder(
                plate_dir,
                measurement_config=measurement_config,
                reuse_artifacts=reuse_artifacts,
            )
        )

    df_paths = [df_path for df_path in df_paths if df_path is not None]

    if df_paths:
        io.logger.info("Merging dataframes from all plates")
        df = pd.concat(
            [pd.read_pickle(df_path) for df_path in df_paths], ignore_index=True
        )
        df.sort_values(["plate", "date", "genotype", "row", "col"], inplace=True)
        df.attrs["measurement_method"] = measurement_config.method
        df.attrs["measurement_config"] = measurement_config.to_dict()

        # Save the merged dataframe
        merged_df_path = io.build_dataframe_path(experiment_path)
        df.to_pickle(merged_df_path)
        io.logger.info(f"Saved merged dataframe to {merged_df_path}")

        # Generate a summary PDF for the experiment
        pdf_file = io.build_summary_pdf_path(experiment_path)
        details = build_analysis_details(
            "experiment",
            experiment_path,
            pdf_file,
            df,
            measurement_config,
            dataframe_source="merged",
        )
        log_analysis_details(details)
        io.logger.info("Generating visualization")
        with PdfPages(pdf_file) as pdf:
            visualize.generate_analysis_details_page(
                pdf, details, title="Analysis details - Experiment"
            )
            visualize.generate_experimentview(pdf, df)
            io.logger.info(f"Saved experiment summary to {pdf_file}")

    io.logger.info(f"Finished analyzing experiment folder: {experiment_path.name}")


def analyze_plate_folder(
    plate_dir: pathlib.Path,
    measurement_config: MeasurementConfig = MeasurementConfig(),
    reuse_artifacts: bool = True,
) -> None | pathlib.Path:
    """Analyze a plate folder: preflight, measure, save results and summary.

    Parameters
    ----------
    plate_dir : pathlib.Path
        Path to the plate folder.
    """
    request = workflow.AnalysisRequest(
        input_path=plate_dir,
        input_kind="plate",
        settings=measurement_config,
        settings_unit="px",
        calibration=workflow.CalibrationSpec("pixels"),
        reuse_policy="compatible" if reuse_artifacts else "none",
    )
    result = run_analysis(request)
    return result.outputs.get("Data table")

    # Historical in-place implementation retained below for the moment as a
    # readable migration reference.  New callers always return above.
    io.logger.info(f"Analyzing plate folder: {plate_dir.name}")
    io.logger.info(f"Artifact reuse is {'enabled' if reuse_artifacts else 'disabled'}")

    preflight_path = io.build_preflight_path(plate_dir)
    if reuse_artifacts and preflight_path.exists():
        io.logger.info("Loading preflight from cache")
        with open(preflight_path, "rb") as fi:
            preflight_dict = pickle.load(fi)
    else:
        try:
            preflight_dict = preflight_plate(
                plate_dir, prepare_info(pd.read_excel(plate_dir / "info.xlsx"))
            )
            if len(preflight_dict["dates"]) == 0:
                io.logger.error(
                    "No matching date columns\n"
                    f"Dates only in info.xlsx: {preflight_dict['only_in_df']}\n"
                    f"Dates only in filesystem: {preflight_dict['only_in_fs']}\n"
                )
                return
        except Exception as ex:
            io.logger.error(f"Error while preflighting plate {plate_dir.name}: {ex}")
            return
        with open(preflight_path, "wb") as fo:
            pickle.dump(preflight_dict, fo)

    df_path = io.build_dataframe_path(plate_dir)
    use_cached_dataframe = False
    if reuse_artifacts and df_path.exists():
        io.logger.info("Loading existing dataframe from cache")
        df = pd.read_pickle(df_path)
        cached_config = df.attrs.get("measurement_config", None)
        if cached_config == measurement_config.to_dict():
            use_cached_dataframe = True
            if measurement_config.method in CENTERLINE_METHODS:
                missing_columns = [
                    column
                    for column in CENTERLINE_REGION_COLUMNS
                    if column not in df.columns
                ]
                if measurement_config.method == "centerline_gaussian":
                    missing_columns.extend(
                        [
                            column
                            for column in CENTERLINE_GAUSSIAN_COLUMNS
                            if column not in df.columns
                        ]
                    )
                if missing_columns:
                    use_cached_dataframe = False
                    io.logger.info(
                        "Cached centerline dataframe is missing regional columns. Recomputing."
                    )
                else:
                    io.logger.info(
                        "Cached dataframe measurement configuration matches. Reusing dataframe."
                    )
            else:
                io.logger.info(
                    "Cached dataframe measurement configuration matches. Reusing dataframe."
                )
        else:
            io.logger.info(
                "Cached dataframe was created with a different measurement configuration. Recomputing."
            )

    if not use_cached_dataframe:
        records = measure_plate(
            preflight_dict,
            measurement_config=measurement_config,
            reuse_artifacts=reuse_artifacts,
        )
        df = pd.DataFrame.from_records(records)

        if len(df) == 0:
            io.logger.error("No images could be measured for this plate.")
            return

        df["plate"] = preflight_dict["plate"]

        if measurement_config.method == "box":
            df["signal_intensity"] = df["tip_fg_mean"] - df["tip_bg_mean"]
        else:
            df["signal_intensity"] = df["skel_intensity"]

        df["date_float"] = df["date"].map(date_to_float)
        df["delta_date_from_min"] = df["date_float"] - df["date_float"].min()

        df["day_number"] = df["delta_date_from_min"].map(
            lambda x: "0" if pd.isna(x) else "+" + day_number(x)
        )

        # Find max length of the day string which is typically
        # +XdYYhZZm
        tmp = (
            df["day_number"]
            .map(lambda x: len(x.split("d")[-1]) if "d" in x else 0)
            .max()
        )
        if tmp == 3:
            # YYh
            df["day_number"] = df["delta_date_from_min"].map(
                lambda x: "0" if pd.isna(x) else "+" + day_number(x, "h")
            )
        elif tmp == 6:
            # YYhZZm
            df["day_number"] = df["delta_date_from_min"].map(
                lambda x: "0" if pd.isna(x) else "+" + day_number(x, "m")
            )
        elif tmp != 0:
            print(f"Unexpected max length: {tmp}")

        df["date_float"] = df["date_float"].astype(float)
        df["delta_date_from_min"] = df["delta_date_from_min"].astype(float)

        df.sort_values(["date_float", "genotype", "row", "col"], inplace=True)

        df["delta_signal_intensity"] = df.groupby(["plate", "row", "col"])[
            "signal_intensity"
        ].transform(lambda x: x.diff())
        df["delta_length"] = df.groupby(["plate", "row", "col"])["length"].transform(
            lambda x: x.diff()
        )
        df["delta_date"] = df.groupby(["plate", "row", "col"])[
            "delta_date_from_min"
        ].transform(lambda x: x.diff())

        df["avg_signal_intensity"] = df.groupby(["plate", "row", "col"])[
            "signal_intensity"
        ].transform(lambda x: x.rolling(2).mean())

        df["delta_length_per_day"] = df["delta_length"] / df["delta_date"]
        df["delta_signal_intensity_per_day"] = (
            df["delta_signal_intensity"] / df["delta_date"]
        )

        df["plant_id_in_gt"] = df.groupby(["plate", "date", "genotype"]).cumcount()
        df["plant_id_in_gt"] = (df["plant_id_in_gt"] + 1).astype(str)

        df.attrs["measurement_method"] = measurement_config.method
        df.attrs["measurement_config"] = measurement_config.to_dict()
        df.attrs["overview_path"] = {
            date: pdd["overview_path"] for date, pdd in preflight_dict["dates"].items()
        }

        df.to_pickle(df_path)
        io.logger.info(f"Saved dataframe to {df_path}")

        xls_file = io.build_summary_excel_path(plate_dir)
        df.to_excel(xls_file, index=False)
        io.logger.info(f"Saved dataframe to {xls_file}")
    else:
        if "measurement_method" not in df.attrs:
            df.attrs["measurement_method"] = measurement_config.method
        if "measurement_config" not in df.attrs:
            df.attrs["measurement_config"] = measurement_config.to_dict()
        if "overview_path" not in df.attrs:
            df.attrs["overview_path"] = {
                date: pdd["overview_path"]
                for date, pdd in preflight_dict["dates"].items()
            }

    pdf_file = io.build_summary_pdf_path(plate_dir)
    if (not pdf_file.exists()) or (not use_cached_dataframe):
        details = build_analysis_details(
            "plate",
            plate_dir,
            pdf_file,
            df,
            measurement_config,
            dataframe_source="cache" if use_cached_dataframe else "computed",
        )
        log_analysis_details(details)
        io.logger.info("Generating visualization")
        with PdfPages(pdf_file) as pdf:
            try:
                visualize.generate_analysis_details_page(
                    pdf,
                    details,
                    title=f"Analysis details - Plate {df['plate'].iloc[0]}",
                )
                visualize.generate_plateview(
                    pdf, df, measurement_method=measurement_config.method
                )
                io.logger.info(f"Saved summary to {pdf_file}")
            except Exception as ex:
                io.logger.error(f"Error while visualizing plate {plate_dir.name}: {ex}")

    io.logger.info(f"Finished analyzing plate folder: {plate_dir.name}")

    return df_path


# ---------------------------------------------------------------------------
# Run-scoped workflow used by the redesigned GUI and batch command.


MEASUREMENT_CACHE_VERSION = 1


@dataclass
class _RunStats:
    total: int = 0
    completed: int = 0
    reused: int = 0
    recomputed: int = 0
    skipped: int = 0
    errors: int = 0
    artifact_counts: dict[str, int] = field(default_factory=dict)


def _emit_progress(
    callback: workflow.ProgressCallback | None,
    event: workflow.ProgressEvent,
) -> None:
    if callback is not None:
        callback(event)


def _plate_input_signature(plate_dir: pathlib.Path) -> str:
    """Hash the workbook and relevant filesystem entries for preflight reuse."""
    entries: list[tuple[str, int, int]] = []
    info_path = plate_dir / "info.xlsx"
    if info_path.exists():
        stat = info_path.stat()
        entries.append(("info.xlsx", stat.st_size, stat.st_mtime_ns))
    for path in sorted(plate_dir.rglob("*"), key=str):
        relative_parts = path.relative_to(plate_dir).parts
        if workflow.CACHE_DIRECTORY in relative_parts or any(
            part.startswith(workflow.RUN_PREFIX) for part in relative_parts
        ):
            continue
        if path.is_dir() and path.name.startswith("date_"):
            stat = path.stat()
            entries.append((str(path.relative_to(plate_dir)), 0, stat.st_mtime_ns))
        elif path.is_file() and path.suffix.lower() in workflow.IMAGE_SUFFIXES:
            stat = path.stat()
            entries.append(
                (str(path.relative_to(plate_dir)), stat.st_size, stat.st_mtime_ns)
            )
    encoded = json.dumps(entries, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_run_preflight(
    plate_dir: pathlib.Path, reuse_policy: workflow.ReusePolicy
) -> tuple[dict[str, Any], bool]:
    signature = _plate_input_signature(plate_dir)
    cache_path = io.build_preflight_cache_path(plate_dir)
    if reuse_policy != "none" and cache_path.exists():
        try:
            with cache_path.open("rb") as stream:
                cached = pickle.load(stream)
            if (
                isinstance(cached, dict)
                and cached.get("signature") == signature
                and isinstance(cached.get("preflight"), dict)
            ):
                io.logger.info(f"Reusing validated preflight for {plate_dir.name}")
                return cached["preflight"], True
        except Exception:
            io.logger.warning(
                f"Could not reuse preflight cache for {plate_dir.name}; rebuilding."
            )

    preflight = preflight_plate(
        plate_dir, prepare_info(pd.read_excel(plate_dir / "info.xlsx"))
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as stream:
        pickle.dump({"signature": signature, "preflight": preflight}, stream)
    return preflight, False


def _round_um_to_px(value_um: float, um_per_pixel: float) -> int:
    return max(1, int(np.floor((value_um / um_per_pixel) + 0.5)))


def resolve_pixel_config(
    request: workflow.AnalysisRequest, um_per_pixel: float | None
) -> MeasurementConfig:
    """Resolve user-facing settings to the pixel configuration used by algorithms."""
    if request.settings_unit == "px":
        return request.settings
    if um_per_pixel is None or not math.isfinite(um_per_pixel) or um_per_pixel <= 0:
        raise ValueError("A valid micrometers-per-pixel value is required")
    settings = request.settings
    return MeasurementConfig(
        method=settings.method,
        box_size=_round_um_to_px(settings.box_size, um_per_pixel),
        box_offset=settings.box_offset,
        perpendicular_width=_round_um_to_px(settings.perpendicular_width, um_per_pixel),
        length=_round_um_to_px(settings.length, um_per_pixel),
        savgol_window=_round_um_to_px(settings.savgol_window, um_per_pixel),
        intensity_savgol_window=(
            0
            if settings.intensity_savgol_window == 0
            else _round_um_to_px(settings.intensity_savgol_window, um_per_pixel)
        ),
    )


def _resolve_image_calibration(
    request: workflow.AnalysisRequest, image_path: pathlib.Path
) -> workflow.CalibrationReading:
    spec = request.calibration
    if request.settings_unit == "px" or spec.mode == "pixels":
        return workflow.CalibrationReading(
            image_path,
            "ok",
            um_per_pixel=1.0,
            message="Pixel-based settings; physical calibration is not required",
        )
    if spec.mode == "metadata":
        # Always read again during analysis; an optional audit is informative and
        # may be stale if the source file changed after the audit.
        return workflow.read_image_calibration(image_path)
    value = spec.shared_um_per_pixel
    assert value is not None
    audited = request.audit_results.get(str(image_path.resolve()))
    return workflow.CalibrationReading(
        image_path,
        "ok",
        x_um_per_pixel=(audited.x_um_per_pixel if audited else None),
        y_um_per_pixel=(audited.y_um_per_pixel if audited else None),
        um_per_pixel=value,
        message=(
            "Shared calibration from cal.txt"
            if spec.mode == "cal_file"
            else "Shared manual calibration"
        ),
    )


def _calibration_report_row(
    reading: workflow.CalibrationReading,
    request: workflow.AnalysisRequest,
    *,
    measurement_status: str,
    detail: str = "",
) -> dict[str, Any]:
    audited = request.audit_results.get(str(reading.path.resolve()))
    embedded = audited or (reading if request.calibration.mode == "metadata" else None)
    return {
        "image": str(reading.path),
        "calibration_mode": request.calibration.mode,
        "calibration_status": reading.status,
        "x_um_per_pixel": reading.x_um_per_pixel,
        "y_um_per_pixel": reading.y_um_per_pixel,
        "calibration_um_per_pixel": (
            None if request.settings_unit == "px" else reading.um_per_pixel
        ),
        "calibration_message": reading.message,
        "embedded_metadata_status": embedded.status if embedded else "not_checked",
        "embedded_metadata_message": embedded.message if embedded else "",
        "measurement_status": measurement_status,
        "detail": detail,
    }


def _measurement_cache_key(
    request: workflow.AnalysisRequest,
    pixel_config: MeasurementConfig,
    calibration: workflow.CalibrationReading,
) -> dict[str, Any]:
    return {
        "version": MEASUREMENT_CACHE_VERSION,
        "user_settings": request.settings.to_dict(),
        "settings_unit": request.settings_unit,
        "pixel_config": pixel_config.to_dict(),
        "calibration_mode": request.calibration.mode,
        "calibration_um_per_pixel": calibration.um_per_pixel,
    }


def _read_measurement_cache(
    path: pathlib.Path, source_signature: dict[str, Any]
) -> dict[str, Any] | None:
    try:
        with path.open("rb") as stream:
            cached = pickle.load(stream)
        if (
            isinstance(cached, dict)
            and io.source_signature_matches(cached.get("source"), source_signature)
            and isinstance(cached.get("record"), dict)
        ):
            return cached["record"]
    except Exception:
        return None
    return None


def _write_measurement_cache(
    path: pathlib.Path,
    source_signature: dict[str, Any],
    record: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    with temporary.open("wb") as stream:
        pickle.dump({"source": source_signature, "record": record}, stream)
    temporary.replace(path)


def _measure_plate_for_run(
    preflight_dict: dict[str, Any],
    request: workflow.AnalysisRequest,
    stats: _RunStats,
    calibration_rows: list[dict[str, Any]],
    progress: workflow.ProgressCallback | None,
    cancel_event: threading.Event,
) -> list[dict[Any, Any]]:
    records: list[dict[Any, Any]] = []
    for date, date_record in preflight_dict["dates"].items():
        for _name, fluo_record in date_record["fluos"].items():
            if cancel_event.is_set():
                raise workflow.AnalysisCancelled()
            path = pathlib.Path(fluo_record["path"])
            plate = str(preflight_dict["plate"])
            reading = _resolve_image_calibration(request, path)
            if not reading.valid:
                stats.skipped += 1
                stats.completed += 1
                detail = reading.message or "Calibration is unusable"
                io.logger.warning(f"Skipping {path}: {detail}")
                calibration_rows.append(
                    _calibration_report_row(
                        reading, request, measurement_status="skipped", detail=detail
                    )
                )
                _emit_progress(
                    progress,
                    workflow.ProgressEvent(
                        "measurement",
                        stats.completed,
                        stats.total,
                        f"Skipped {path.name}: unusable calibration",
                        current_path=path,
                        plate=plate,
                        reused=stats.reused,
                        skipped=stats.skipped,
                        errors=stats.errors,
                    ),
                )
                continue

            pixel_config = resolve_pixel_config(request, reading.um_per_pixel)
            cache_key = _measurement_cache_key(request, pixel_config, reading)
            cache_path = io.build_measurement_cache_path(path, cache_key)
            try:
                source_signature = io.build_source_signature(path)
            except Exception as ex:
                stats.errors += 1
                stats.completed += 1
                io.logger.error(f"Could not inspect {path}: {ex}")
                calibration_rows.append(
                    _calibration_report_row(
                        reading,
                        request,
                        measurement_status="error",
                        detail=str(ex),
                    )
                )
                _emit_progress(
                    progress,
                    workflow.ProgressEvent(
                        "measurement",
                        stats.completed,
                        stats.total,
                        f"Error inspecting {path.name}",
                        current_path=path,
                        plate=plate,
                        reused=stats.reused,
                        skipped=stats.skipped,
                        errors=stats.errors,
                    ),
                )
                continue
            record: dict[str, Any] | None = None
            reused_measurement = False
            if request.reuse_policy == "compatible" and cache_path.exists():
                record = _read_measurement_cache(cache_path, source_signature)
                reused_measurement = record is not None
                if reused_measurement:
                    io.logger.info(f"Reusing compatible measurement: {path}")

            if record is None:
                try:
                    measured = imsingleroot.measure_image(
                        path,
                        measurement_config=pixel_config,
                        reuse_artifacts=request.reuse_policy != "none",
                        artifact_stats=stats.artifact_counts,
                    )
                    if measured is None:
                        raise ValueError("No valid root was measured")
                    record = {
                        **fluo_record,
                        **measured,
                        "date": date,
                        "calibration_um_per_pixel": (
                            None
                            if request.settings_unit == "px"
                            else reading.um_per_pixel
                        ),
                        "calibration_source": request.calibration.mode,
                        "calibration_x_um_per_pixel": reading.x_um_per_pixel,
                        "calibration_y_um_per_pixel": reading.y_um_per_pixel,
                        "measurement_config_px": json.dumps(
                            pixel_config.to_dict(), sort_keys=True
                        ),
                    }
                    try:
                        _write_measurement_cache(cache_path, source_signature, record)
                    except Exception as cache_error:
                        io.logger.warning(
                            f"Measured {path}, but could not update its reusable cache: {cache_error}"
                        )
                except Exception as ex:
                    stats.errors += 1
                    stats.completed += 1
                    io.logger.error(f"Could not measure {path}: {ex}")
                    calibration_rows.append(
                        _calibration_report_row(
                            reading,
                            request,
                            measurement_status="error",
                            detail=str(ex),
                        )
                    )
                    _emit_progress(
                        progress,
                        workflow.ProgressEvent(
                            "measurement",
                            stats.completed,
                            stats.total,
                            f"Error measuring {path.name}",
                            current_path=path,
                            plate=plate,
                            reused=stats.reused,
                            skipped=stats.skipped,
                            errors=stats.errors,
                        ),
                    )
                    continue

            if reused_measurement:
                stats.reused += 1
            else:
                stats.recomputed += 1
            records.append(record)
            stats.completed += 1
            calibration_rows.append(
                _calibration_report_row(
                    reading,
                    request,
                    measurement_status="reused" if reused_measurement else "measured",
                )
            )
            _emit_progress(
                progress,
                workflow.ProgressEvent(
                    "measurement",
                    stats.completed,
                    stats.total,
                    (
                        f"Reused {path.name}"
                        if reused_measurement
                        else f"Measured {path.name}"
                    ),
                    current_path=path,
                    plate=plate,
                    reused=stats.reused,
                    skipped=stats.skipped,
                    errors=stats.errors,
                ),
            )
    return records


def _prepare_result_dataframe(
    records: list[dict[Any, Any]],
    preflight_dict: dict[str, Any],
    request: workflow.AnalysisRequest,
) -> pd.DataFrame:
    df = pd.DataFrame.from_records(records)
    if len(df) == 0:
        return df
    df["plate"] = preflight_dict["plate"]
    if request.settings.method == "box":
        df["signal_intensity"] = df["tip_fg_mean"] - df["tip_bg_mean"]
    else:
        df["signal_intensity"] = df["skel_intensity"]

    df["date_float"] = df["date"].map(date_to_float)
    df["delta_date_from_min"] = df["date_float"] - df["date_float"].min()
    df["day_number"] = df["delta_date_from_min"].map(
        lambda value: "0" if pd.isna(value) else "+" + day_number(value)
    )
    maximum_suffix = (
        df["day_number"]
        .map(lambda value: len(value.split("d")[-1]) if "d" in value else 0)
        .max()
    )
    if maximum_suffix == 3:
        df["day_number"] = df["delta_date_from_min"].map(
            lambda value: "0" if pd.isna(value) else "+" + day_number(value, "h")
        )
    elif maximum_suffix == 6:
        df["day_number"] = df["delta_date_from_min"].map(
            lambda value: "0" if pd.isna(value) else "+" + day_number(value, "m")
        )

    df["date_float"] = df["date_float"].astype(float)
    df["delta_date_from_min"] = df["delta_date_from_min"].astype(float)
    df.sort_values(["date_float", "genotype", "row", "col"], inplace=True)
    groups = df.groupby(["plate", "row", "col"])
    df["delta_signal_intensity"] = groups["signal_intensity"].transform(
        lambda values: values.diff()
    )
    df["delta_length"] = groups["length"].transform(lambda values: values.diff())
    df["delta_date"] = groups["delta_date_from_min"].transform(
        lambda values: values.diff()
    )
    df["avg_signal_intensity"] = groups["signal_intensity"].transform(
        lambda values: values.rolling(2).mean()
    )
    df["delta_length_per_day"] = df["delta_length"] / df["delta_date"]
    df["delta_signal_intensity_per_day"] = (
        df["delta_signal_intensity"] / df["delta_date"]
    )
    df["plant_id_in_gt"] = (
        df.groupby(["plate", "date", "genotype"]).cumcount() + 1
    ).astype(str)
    df.attrs["measurement_method"] = request.settings.method
    df.attrs["measurement_config"] = request.settings.to_dict()
    df.attrs["measurement_settings_unit"] = request.settings_unit
    df.attrs["calibration"] = request.calibration.to_dict()
    df.attrs["overview_path"] = {
        date: date_data["overview_path"]
        for date, date_data in preflight_dict["dates"].items()
    }
    return df


def _write_plate_run_outputs(
    plate_dir: pathlib.Path,
    output_dir: pathlib.Path,
    df: pd.DataFrame,
    request: workflow.AnalysisRequest,
    *,
    dataframe_source: Literal["computed", "cache", "merged"] = "computed",
) -> dict[str, pathlib.Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    dataframe_path = output_dir / "dataframe.pickle"
    excel_path = output_dir / "summary.xlsx"
    pdf_path = output_dir / "summary.pdf"
    df.to_pickle(dataframe_path)
    df.to_excel(excel_path, index=False)
    details = build_analysis_details(
        "plate",
        plate_dir,
        pdf_path,
        df,
        request.settings,
        dataframe_source=dataframe_source,
    )
    details.append(("Measurement settings unit", request.settings_unit))
    details.append(("Calibration", json.dumps(request.calibration.to_dict())))
    log_analysis_details(details)
    with PdfPages(pdf_path) as pdf:
        visualize.generate_analysis_details_page(
            pdf,
            details,
            title=f"Analysis details - Plate {df['plate'].iloc[0]}",
        )
        visualize.generate_plateview(
            pdf, df, measurement_method=request.settings.method
        )
    return {
        "Data table": dataframe_path,
        "Excel summary": excel_path,
        "PDF summary": pdf_path,
    }


def _write_experiment_run_outputs(
    experiment_path: pathlib.Path,
    run_dir: pathlib.Path,
    dataframes: list[pd.DataFrame],
    request: workflow.AnalysisRequest,
) -> tuple[pd.DataFrame, dict[str, pathlib.Path]]:
    df = pd.concat(dataframes, ignore_index=True)
    df.sort_values(["plate", "date", "genotype", "row", "col"], inplace=True)
    df.attrs["measurement_method"] = request.settings.method
    df.attrs["measurement_config"] = request.settings.to_dict()
    df.attrs["measurement_settings_unit"] = request.settings_unit
    df.attrs["calibration"] = request.calibration.to_dict()
    dataframe_path = run_dir / "dataframe.pickle"
    excel_path = run_dir / "summary.xlsx"
    pdf_path = run_dir / "summary.pdf"
    df.to_pickle(dataframe_path)
    df.to_excel(excel_path, index=False)
    details = build_analysis_details(
        "experiment",
        experiment_path,
        pdf_path,
        df,
        request.settings,
        dataframe_source="merged",
    )
    details.append(("Measurement settings unit", request.settings_unit))
    details.append(("Calibration", json.dumps(request.calibration.to_dict())))
    log_analysis_details(details)
    with PdfPages(pdf_path) as pdf:
        visualize.generate_analysis_details_page(
            pdf, details, title="Analysis details - Experiment"
        )
        visualize.generate_experimentview(pdf, df)
    return df, {
        "Data table": dataframe_path,
        "Excel summary": excel_path,
        "PDF summary": pdf_path,
    }


def run_analysis(
    request: workflow.AnalysisRequest,
    *,
    progress: workflow.ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> workflow.RunResult:
    """Execute one immutable, manifest-backed plate or experiment run."""
    cancel = cancel_event or threading.Event()
    run_id, run_dir = workflow.create_run_directory(request.input_path)
    manifest_path = run_dir / "run.json"
    log_path = run_dir / "analysis.log"
    calibration_path = run_dir / "calibration_report.csv"
    started_at = workflow.utc_now_text()
    stats = _RunStats()
    outputs: dict[str, pathlib.Path] = {"Analysis log": log_path}
    calibration_rows: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {
        "schema_version": workflow.RUN_SCHEMA_VERSION,
        "app_version": io.__version__,
        "run_id": run_id,
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "request": request.to_manifest_dict(),
        "stats": {},
        "outputs": {},
        "message": "Analysis is running.",
    }
    workflow.atomic_write_json(manifest_path, manifest)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)s  %(message)s")
    )
    io.logger.addHandler(file_handler)
    status: workflow.RunStatus = "failed"
    message = "Analysis failed."

    try:
        _emit_progress(
            progress,
            workflow.ProgressEvent(
                "validation", message="Checking the selected folder"
            ),
        )
        validation = workflow.validate_folder(request.input_path)
        if not validation.valid or validation.kind != request.input_kind:
            detail = "; ".join(validation.details)
            raise ValueError(validation.message + (f" {detail}" if detail else ""))
        if cancel.is_set():
            raise workflow.AnalysisCancelled()

        plate_dirs = (
            [request.input_path]
            if request.input_kind == "plate"
            else sorted(
                path for path in request.input_path.glob("plate_*") if path.is_dir()
            )
        )
        preflights: list[tuple[pathlib.Path, dict[str, Any], bool]] = []
        for index, plate_dir in enumerate(plate_dirs, 1):
            if cancel.is_set():
                raise workflow.AnalysisCancelled()
            preflight, reused_preflight = _load_run_preflight(
                plate_dir, request.reuse_policy
            )
            preflight_key = (
                "preflights_reused" if reused_preflight else "preflights_recomputed"
            )
            stats.artifact_counts[preflight_key] = (
                stats.artifact_counts.get(preflight_key, 0) + 1
            )
            preflights.append((plate_dir, preflight, reused_preflight))
            _emit_progress(
                progress,
                workflow.ProgressEvent(
                    "validation",
                    index,
                    len(plate_dirs),
                    f"Validated {plate_dir.name}",
                    plate=plate_dir.name,
                ),
            )
        stats.total = sum(
            len(date_data["fluos"])
            for _plate, preflight, _reused in preflights
            for date_data in preflight["dates"].values()
        )
        if stats.total == 0:
            raise ValueError("No referenced images are available to measure")

        plate_dataframes: list[pd.DataFrame] = []
        plate_outputs: dict[str, dict[str, str]] = {}
        for plate_dir, preflight, _reused_preflight in preflights:
            records = _measure_plate_for_run(
                preflight,
                request,
                stats,
                calibration_rows,
                progress,
                cancel,
            )
            if cancel.is_set():
                raise workflow.AnalysisCancelled()
            if not records:
                io.logger.error(f"No images could be measured for {plate_dir.name}")
                continue
            _emit_progress(
                progress,
                workflow.ProgressEvent(
                    "aggregation",
                    stats.completed,
                    stats.total,
                    f"Calculating derived values for {plate_dir.name}",
                    plate=plate_dir.name,
                    reused=stats.reused,
                    skipped=stats.skipped,
                    errors=stats.errors,
                ),
            )
            df = _prepare_result_dataframe(records, preflight, request)
            plate_dataframes.append(df)
            output_dir = (
                run_dir
                if request.input_kind == "plate"
                else run_dir / "plates" / plate_dir.name
            )
            _emit_progress(
                progress,
                workflow.ProgressEvent(
                    "reporting",
                    stats.completed,
                    stats.total,
                    f"Writing reports for {plate_dir.name}",
                    plate=plate_dir.name,
                    reused=stats.reused,
                    skipped=stats.skipped,
                    errors=stats.errors,
                ),
            )
            try:
                current_outputs = _write_plate_run_outputs(
                    plate_dir,
                    output_dir,
                    df,
                    request,
                    dataframe_source=(
                        "cache"
                        if stats.reused and stats.reused == len(records)
                        else "computed"
                    ),
                )
            except Exception as ex:
                stats.errors += 1
                io.logger.error(f"Could not write reports for {plate_dir.name}: {ex}")
                current_outputs = {}
            if request.input_kind == "plate":
                outputs.update(current_outputs)
            else:
                plate_outputs[plate_dir.name] = {
                    name: str(path.relative_to(run_dir))
                    for name, path in current_outputs.items()
                }

        if cancel.is_set():
            raise workflow.AnalysisCancelled()
        if not plate_dataframes:
            raise ValueError("No images could be measured in the selected folder")
        if request.input_kind == "experiment":
            _emit_progress(
                progress,
                workflow.ProgressEvent(
                    "aggregation",
                    stats.completed,
                    stats.total,
                    "Merging plate results",
                    reused=stats.reused,
                    skipped=stats.skipped,
                    errors=stats.errors,
                ),
            )
            _merged, experiment_outputs = _write_experiment_run_outputs(
                request.input_path, run_dir, plate_dataframes, request
            )
            outputs.update(experiment_outputs)
            manifest["plate_outputs"] = plate_outputs

        status = (
            "completed_with_warnings" if stats.skipped or stats.errors else "completed"
        )
        message = (
            "Analysis completed with warnings."
            if status == "completed_with_warnings"
            else "Analysis completed successfully."
        )
    except workflow.AnalysisCancelled:
        status = "cancelled"
        message = "Analysis cancelled after the current image."
        io.logger.warning(message)
    except Exception as ex:
        status = "failed"
        message = f"Analysis failed: {ex}"
        io.logger.exception(message)
        stats.errors += 1
    finally:
        columns = [
            "image",
            "calibration_mode",
            "calibration_status",
            "x_um_per_pixel",
            "y_um_per_pixel",
            "calibration_um_per_pixel",
            "calibration_message",
            "embedded_metadata_status",
            "embedded_metadata_message",
            "measurement_status",
            "detail",
        ]
        pd.DataFrame(calibration_rows, columns=columns).to_csv(
            calibration_path, index=False
        )
        outputs["Calibration report"] = calibration_path
        io.logger.removeHandler(file_handler)
        file_handler.close()

    manifest.update(
        {
            "status": status,
            "finished_at": workflow.utc_now_text(),
            "stats": {
                "total": stats.total,
                "completed": stats.completed,
                "reused_measurements": stats.reused,
                "recomputed_measurements": stats.recomputed,
                "skipped": stats.skipped,
                "errors": stats.errors,
                **stats.artifact_counts,
            },
            "outputs": {
                name: str(path.relative_to(run_dir))
                for name, path in outputs.items()
                if path.exists()
            },
            "message": message,
        }
    )
    workflow.atomic_write_json(manifest_path, manifest)
    return workflow.RunResult(
        run_id=run_id,
        run_dir=run_dir,
        status=status,
        outputs={name: path for name, path in outputs.items() if path.exists()},
        total=stats.total,
        completed=stats.completed,
        reused=stats.reused,
        recomputed=stats.recomputed,
        skipped=stats.skipped,
        errors=stats.errors,
        message=message,
    )
