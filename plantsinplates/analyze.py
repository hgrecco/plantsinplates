import pickle
from typing import Any, Literal
import pathlib
import json
from decimal import Decimal
from fractions import Fraction

from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd

from . import io
from . import imsingleroot
from .measurement_config import MeasurementConfig
from . import visualize

THRESHOLD = 100
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
        for fluo_file in date_dir.rglob("*.czi")
        if fluo_file.suffix in io.FLUO_SUFFIXES and not fluo_file.stem.startswith(".")
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
            if measurement_config.method == "centerline":
                missing_columns = [
                    column
                    for column in CENTERLINE_REGION_COLUMNS
                    if column not in df.columns
                ]
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
