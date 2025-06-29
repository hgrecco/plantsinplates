import pickle
from typing import Any
import pathlib

from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd

from . import io
from . import imsingleroot
from . import visualize

THRESHOLD = 100


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
        lambda x: str(pathlib.Path(pathlib.PurePosixPath(x).as_posix()))
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


def measure_plate(preflight_dict: dict[str, Any]) -> list[dict[Any, Any]]:
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
                rec = imsingleroot.measure_image(fluo_record["path"])
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


def analyze_experiment_folder(experiment_path: pathlib.Path) -> None | pathlib.Path:
    """Analyze all plates in an experiment folder.

    Parameters
    ----------
    experiment_path : pathlib.Path
        Path to the experiment folder.
    """
    io.logger.info(f"Analyzing experiment folder: {experiment_path.name}")
    df_paths: list[pathlib.Path | None] = []
    for plate_dir in sorted(experiment_path.glob("plate_*")):
        if not plate_dir.is_dir():
            continue
        df_paths.append(analyze_plate_folder(plate_dir))

    df_paths = [df_path for df_path in df_paths if df_path is not None]

    if df_paths:
        io.logger.info("Merging dataframes from all plates")
        df = pd.concat(
            [pd.read_pickle(df_path) for df_path in df_paths], ignore_index=True
        )
        df.sort_values(["plate", "date", "genotype", "row", "col"], inplace=True)

        # Save the merged dataframe
        merged_df_path = io.build_dataframe_path(experiment_path)
        df.to_pickle(merged_df_path)
        io.logger.info(f"Saved merged dataframe to {merged_df_path}")

        # Generate a summary PDF for the experiment
        pdf_file = io.build_summary_pdf_path(experiment_path)
        if not pdf_file.exists():
            io.logger.info("Generating visualization")
            with PdfPages(pdf_file) as pdf:
                visualize.generate_experimentview(pdf, df)
                io.logger.info(f"Saved experiment summary to {pdf_file}")

    io.logger.info(f"Finished analyzing experiment folder: {experiment_path.name}")


def analyze_plate_folder(plate_dir: pathlib.Path) -> None | pathlib.Path:
    """Analyze a plate folder: preflight, measure, save results and summary.

    Parameters
    ----------
    plate_dir : pathlib.Path
        Path to the plate folder.
    """
    io.logger.info(f"Analyzing plate folder: {plate_dir.name}")

    preflight_path = io.build_preflight_path(plate_dir)
    if preflight_path.exists():
        io.logger.info("Loading preflight from cache")
        preflight_dict = pickle.load(open(preflight_path, "rb"))
    else:
        try:
            preflight_dict = preflight_plate(
                plate_dir, prepare_info(pd.read_excel(plate_dir / "info.xlsx"))
            )
        except Exception as ex:
            io.logger.error(f"Error while preflighting plate {plate_dir.name}: {ex}")
            return
        pickle.dump(preflight_dict, open(preflight_path, "wb"))

    df_path = io.build_dataframe_path(plate_dir)
    if df_path.exists():
        io.logger.info("Loading existing dataframe from cache")
        df = pd.read_pickle(df_path)
    else:
        records = measure_plate(preflight_dict)
        df = pd.DataFrame.from_records(records)
        try:
            df.sort_values(["date", "genotype", "row", "col"], inplace=True)
        except Exception as ex:
            print(df.columns)
            raise ex

        df.attrs["overview_path"] = {
            date: pdd["overview_path"] for date, pdd in preflight_dict["dates"].items()
        }
        df["plate"] = preflight_dict["plate"]
        df["tip_mean_intensity"] = df["tip_fg_mean"] - df["tip_bg_mean"]

        df["date_float"] = df["date"].map(float)

        df["delta_date"] = df.groupby(["plate", "row", "col"])["date_float"].transform(
            lambda x: x.diff()
        )
        df["delta_tip_mean_intensity"] = df.groupby(["plate", "row", "col"])[
            "tip_mean_intensity"
        ].transform(lambda x: x.diff())
        df["delta_length"] = df.groupby(["plate", "row", "col"])["length"].transform(
            lambda x: x.diff()
        )

        df["avg_tip_mean_intensity"] = df.groupby(["plate", "row", "col"])[
            "tip_mean_intensity"
        ].transform(lambda x: x.rolling(2).mean())

        df["delta_length_per_day"] = df["delta_length"] / df["delta_date"]
        df["delta_tip_mean_intensity_per_day"] = (
            df["delta_tip_mean_intensity"] / df["delta_date"]
        )

        df.to_pickle(df_path)

        xls_file = io.build_summary_excel_path(plate_dir)
        df.to_excel(xls_file, index=False)
        io.logger.info(f"Saved dataframe to {xls_file}")

    pdf_file = io.build_summary_pdf_path(plate_dir)
    if not pdf_file.exists():
        io.logger.info("Generating visualization")
        with PdfPages(pdf_file) as pdf:
            try:
                visualize.generate_plateview(pdf, df)
                io.logger.info(f"Saved summary to {pdf_file}")
            except Exception as ex:
                io.logger.error(f"Error while visualizing plate {plate_dir.name}: {ex}")

    io.logger.info(f"Finished analyzing plate folder: {plate_dir.name}")

    return df_path
