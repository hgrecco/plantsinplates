import contextlib
from typing import Generator, Literal, Any
import datetime
from matplotlib.axes import Axes
from matplotlib.figure import Figure

import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd
import skimage.io as skio
from seaborn import objects as so
from seaborn import axes_style

from . import io


TAB10_COLORS = [
    "tab:blue",
    "tab:orange",
    "tab:green",
    "tab:red",
    "tab:purple",
    "tab:brown",
    "tab:pink",
    "tab:gray",
    "tab:olive",
    "tab:cyan",
    "tab:blue",
    "tab:orange",
    "tab:green",
    "tab:red",
    "tab:purple",
    "tab:brown",
    "tab:pink",
    "tab:gray",
    "tab:olive",
    "tab:cyan",
]

FOOTNOTE_DATE: str = ""


def footnote(fig: Figure, *, left_footer: str = "", right_footer: str = ""):
    """Add left and right footnotes to a matplotlib figure.

    Parameters
    ----------
    fig : Figure
        The figure to annotate.
    left_footer : str, optional
        Text for the left footer.
    right_footer : str, optional
        Text for the right footer.
    """
    if left_footer:
        fig.text(0.02, 0.01, left_footer, ha="left", fontsize=6, wrap=True)
    if right_footer:
        fig.text(0.98, 0.01, right_footer, ha="right", fontsize=6, wrap=True)


def default_footnote(fig: Figure | None):
    """Set or annotate the default footnote with date and version.

    Parameters
    ----------
    fig : Figure or None
        The figure to annotate, or None to set the default date.
    """
    global FOOTNOTE_DATE
    if fig is None or FOOTNOTE_DATE == "":
        FOOTNOTE_DATE = datetime.datetime.now().isoformat(timespec="seconds")  # type: ignore

    if fig is not None:
        footnote(
            fig,
            left_footer=f"Analysis datetime: {FOOTNOTE_DATE}",
            right_footer=f"Analysis version: {io.__version__}",
        )


def get_rectangle_from_box(
    position: tuple[int, int], box: tuple[int, int], **kwargs: Any
) -> patches.Rectangle:
    """Create a rectangle patch given a position and box size.

    Parameters
    ----------
    position : tuple of int
        Center (row, column) of the rectangle.
    box : tuple of int
        Size (width, height) of the rectangle.
    **kwargs : Any
        Additional keyword arguments for the Rectangle.

    Returns
    -------
    patches.Rectangle
        The rectangle patch.
    """
    cx, cy = position
    w, h = box
    x = cx - w / 2
    y = cy - h / 2

    rect = patches.Rectangle((y, x), h, w, **kwargs)
    return rect


@contextlib.contextmanager
def build_figure(
    nrows: int,
    ncols: int,
    *,
    ax_style: Literal["off", "no-ticks", "full"] = "full",
    height_fraction: float = 1,
    layout: Literal["constrained", "tight"] = "tight",
) -> Generator[tuple[Figure, Axes], None, None]:
    """Context manager to build a figure with optional axis styling.

    Parameters
    ----------
    nrows : int
        Number of rows of subplots.
    ncols : int
        Number of columns of subplots.
    ax_style : {'off', 'no-ticks', 'full'}, optional
        Style for axis visibility.
    height_fraction : float, optional
        Fraction of default figure height.
    layout : {'constrained', 'tight', 'none'}, optional
        Layout option for subplots.

    Yields
    ------
    Figure
        The created figure.
    Axes
        The created axes array.
    """
    if layout == "constrained":
        fig, axs = plt.subplots(
            nrows,
            ncols,
            figsize=(297 / 40, 210 / 40 * height_fraction),
            constrained_layout=True,
        )
    elif layout == "tight":
        fig, axs = plt.subplots(
            nrows, ncols, figsize=(297 / 40, 210 / 40 * height_fraction)
        )
        fig.tight_layout()
    elif layout == "none":
        fig, axs = plt.subplots(
            nrows, ncols, figsize=(297 / 40, 210 / 40 * height_fraction)
        )
    else:
        raise ValueError(f"Unknown layout {layout}")

    if nrows == 1 and ncols == 1:
        axf = [
            axs,
        ]
    else:
        axf = axs.flatten()

    match ax_style:
        case "off":
            for ax in axf:
                ax.axis(False)
        case "no-ticks":
            for ax in axf:
                ax.set_xticks([])
                ax.set_yticks([])
        case "full":
            pass

    yield fig, axs

    plt.close(fig)


@contextlib.contextmanager
def pdf_figure(
    pdf_writer: PdfPages,
    nrows: int,
    ncols: int,
    *,
    ax_style: Literal["off", "no-ticks", "full"] = "full",
    height_fraction: float = 1,
    layout: Literal["constrained", "tight", "none"] = "tight",
) -> Generator[tuple[Figure, Axes], None, None]:
    """Context manager to build a figure and save it to a PDF.

    Parameters
    ----------
    pdf_writer : PdfPages
        PDF writer to save the figure.
    nrows : int
        Number of rows of subplots.
    ncols : int
        Number of columns of subplots.
    ax_style : {'off', 'no-ticks', 'full'}, optional
        Style for axis visibility.
    height_fraction : float, optional
        Fraction of default figure height.
    layout : {'constrained', 'tight', 'none'}, optional
        Layout option for subplots.

    Yields
    ------
    Figure
        The created figure.
    Axes
        The created axes array.
    """
    with build_figure(
        nrows, ncols, ax_style=ax_style, height_fraction=height_fraction, layout=layout
    ) as (fig, axs):
        yield fig, axs
        pdf_writer.savefig(fig)


@contextlib.contextmanager
def pdf_empty_figure(
    pdf_writer: PdfPages, layout: str = "tight"
) -> Generator[Figure, None, None]:
    """Context manager to build a blank figure and save it to a PDF.

    Parameters
    ----------
    pdf_writer : PdfPages
        PDF writer to save the figure.
    layout : str, optional
        Layout option for the figure.

    Yields
    ------
    Figure
        The created blank figure.
    """
    fig = plt.figure(figsize=(297 / 40, 210 / 40), layout=layout)
    yield fig
    pdf_writer.savefig(fig)
    plt.close(fig)


def colorize_axes(ax: Axes, color: str, width: int | float = 2):
    """Color the spines of a matplotlib Axes.

    Parameters
    ----------
    ax : Axes
        Axes to color.
    color : str
        Color to apply.
    width : int or float, optional
        Line width of the spines.
    """
    for spine in ax.spines.values():
        spine.set_edgecolor(color)
        spine.set_linewidth(width)


def relabel(s: str) -> str:
    if s == "length":
        return "length [mm]"
    if s == "delta_length_per_day":
        return "growth [mm/day]"
    if s == "delta_tip_mean_intensity_per_day":
        return "tip mean intensity change [1/day]"
    if s == "tip_mean_intensity":
        return "tip mean intensity"
    if s == "edate":
        return "day"
    return s


def seaborn_plot(
    pdf_writer: PdfPages,
    plot_kwargs: dict[str, Any],
    facet_kwargs: dict[str, Any] | None = None,
):
    """Generate a seaborn plot with a specific theme and layout."""

    facet_kwargs = facet_kwargs or {}

    with pdf_empty_figure(pdf_writer) as fig:
        (
            so.Plot(**plot_kwargs)
            .add(so.Dot(), so.Shift(y=np.nan))
            .add(so.Dot(), marker="plant_id_internal", legend=False)
            .theme(axes_style("whitegrid"))
            .layout(extent=(0.05, 0.05, 0.80, 0.95), engine="tight")
            .scale(color=TAB10_COLORS, alpha=(1, 0.3))  # type: ignore
            .label(x=relabel, y=relabel, alpha=relabel)
            .facet(**facet_kwargs)
            .on(fig)
            .plot(pyplot=True)
        )


def generate_experimentview(pdf_writer: PdfPages, experiment_df: pd.DataFrame):
    """Generate a multi-page PDF with experiment overview and summary plots.

    Parameters
    ----------
    pdf_writer : PdfPages
        PDF writer to save the pages.
    experiment_df : pd.DataFrame
        DataFrame with experiment data.
    """

    with pdf_figure(pdf_writer, 1, 1, ax_style="off") as (fig, axs):
        fig.suptitle(f"{len(experiment_df)} rows", size="small")

        out = experiment_df.pivot_table(
            values="date",
            columns="plate",
            index="genotype",
            aggfunc="count",
            fill_value=0,
        )
        out_reset = out.reset_index()
        columns = ["Genotype"] + [f"Plate {col}" for col in out.columns]
        cell_text = out_reset[["genotype"] + [col for col in out.columns]].values

        genotypes = cell_text[:, 0].tolist()

        cell_text = np.column_stack(([""] * cell_text.shape[0], cell_text))

        table = axs.table(
            cellText=cell_text,
            colLabels=[""] + columns,
            loc="center",
            cellLoc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table[(0, 0)].set_width(0.05)

        for ndx, genotype in enumerate(genotypes, 1):
            table[(ndx, 0)].set_facecolor(TAB10_COLORS[genotypes.index(genotype)])
            table[(ndx, 0)].set_width(0.05)

        default_footnote(fig)

    return
    experiment_df["edate"] = experiment_df["delta_date"].map(
        lambda x: "0" if pd.isna(x) else f"+{x}"
    )
    experiment_df["plant_id_internal"] = (
        experiment_df.groupby(["plate", "date", "genotype"]).cumcount().astype(str)
    )

    #################
    # y-categoricals
    #################

    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="length",
            y="edate",
            color="genotype",
        ),
        dict(row="genotype", col="plate"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="delta_length_per_day",
            y="edate",
            color="genotype",
            marker="plate",
        ),
        dict(row="genotype", col="plate"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="tip_mean_intensity",
            y="edate",
            color="genotype",
        ),
        dict(row="genotype", col="plate"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="delta_tip_mean_intensity_per_day",
            y="edate",
            color="genotype",
        ),
        dict(row="genotype", col="plate"),
    )

    #################
    # y-numerical
    #################

    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="tip_mean_intensity",
            y="length",
            color="genotype",
            alpha="edate",
        ),
        dict(row="genotype", col="plate"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="delta_tip_mean_intensity_per_day",
            y="delta_length_per_day",
            color="genotype",
            alpha="edate",
        ),
        dict(row="genotype", col="plate"),
    )


def generate_plateview(pdf_writer: PdfPages, plate_df: pd.DataFrame):
    """Generate a multi-page PDF with plate overview and summary plots.

    Parameters
    ----------
    pdf_writer : PdfPages
        PDF writer to save the pages.
    plate_df : pd.DataFrame
        DataFrame with plate data.
    """
    assert plate_df["plate"].nunique() == 1, (
        "Plate dataframe must contain only one plate"
    )

    plate_df["plant_id"] = (
        plate_df["row"].astype(str) + " | " + plate_df["col"].astype(str)
    )

    with pdf_figure(pdf_writer, 3, 4, ax_style="off") as (fig, axs):
        ax_rest = axs[1:, :].flatten()
        gs = axs[0, 0].get_gridspec()

        axs[0, 0].remove()
        axs[0, 1].remove()

        axbig = fig.add_subplot(gs[0, :2])
        axbig.axis(False)

        genotypes: list[str] = sorted(plate_df["genotype"].unique())
        nrows: int = plate_df["row"].max()
        ncols: int = plate_df["col"].max()

        for ndx, (date, gdf) in enumerate(plate_df.groupby("date")):
            ax = ax_rest[ndx]

            ax.set_title(f"{date} - {len(gdf)} files", size="xx-small")
            if (overview_path := gdf.attrs["overview_path"][date]) is None:
                ax.text(
                    0.5,
                    0.5,
                    "No plate overview image",
                    size=15,
                    horizontalalignment="center",
                    verticalalignment="center",
                )
            else:
                im = skio.imread(overview_path)
                ax.imshow(im)
                ax.set_anchor("N")

        title = f"Plate {plate_df['plate'].iloc[0]} - {len(plate_df)} records"
        fig.suptitle(f"{title}\n{nrows} rows x {ncols} cols", size="small")

        cell_text = (
            plate_df.groupby("genotype", as_index=False)
            .size()
            .sort_values("genotype")
            .values
        )
        cell_text = np.column_stack(([""] * cell_text.shape[0], cell_text))
        table = axbig.table(
            cellText=cell_text,
            colLabels=["", "Genotype", "Plants"],
            loc="center",
            cellLoc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table[(0, 0)].set_width(0.05)
        for ndx, genotype in enumerate(genotypes, 1):
            table[(ndx, 0)].set_facecolor(TAB10_COLORS[genotypes.index(genotype)])
            table[(ndx, 0)].set_width(0.05)

        default_footnote(fig)

    for ndx, (date, gdf) in enumerate(plate_df.groupby("date")):
        generate_dateview(pdf_writer, gdf)

    #################
    # y-categoricals
    #################

    plate_df["edate"] = plate_df["delta_date"].map(
        lambda x: "0" if pd.isna(x) else f"+{x}"
    )
    plate_df["plant_id_internal"] = (
        plate_df.groupby(["plate", "date", "genotype"]).cumcount().astype(str)
    )

    seaborn_plot(
        pdf_writer,
        dict(data=plate_df, x="length", y="edate", color="genotype"),
        dict(row="genotype"),
    )
    seaborn_plot(
        pdf_writer,
        dict(data=plate_df, x="delta_length_per_day", y="edate", color="genotype"),
        dict(row="genotype"),
    )
    seaborn_plot(
        pdf_writer,
        dict(data=plate_df, x="tip_mean_intensity", y="edate", color="genotype"),
        dict(row="genotype"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=plate_df,
            x="delta_tip_mean_intensity_per_day",
            y="edate",
            color="genotype",
        ),
        dict(row="genotype"),
    )

    #################
    # y-numerical
    #################
    seaborn_plot(
        pdf_writer,
        dict(
            data=plate_df,
            x="tip_mean_intensity",
            y="length",
            color="genotype",
            alpha="edate",
        ),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=plate_df,
            x="delta_tip_mean_intensity_per_day",
            y="delta_length_per_day",
            color="genotype",
            alpha="edate",
        ),
    )


def generate_dateview(pdf_writer: PdfPages, date_df: pd.DataFrame):
    """Generate a page showing images and masks for a specific date.

    Parameters
    ----------
    pdf_writer : PdfPages
        PDF writer to save the page.
    date_df : pd.DataFrame
        DataFrame with records for a single date.
    """
    assert date_df["date"].nunique() == 1, "Date dataframe must contain only one date"

    title = f"Date {date_df['date'].iloc[0]} - {len(date_df)} records"

    genotypes: list[str] = sorted(date_df["genotype"].unique())
    nrows: int = date_df["row"].max()
    ncols: int = date_df["col"].max()

    with pdf_figure(
        pdf_writer,
        2 * nrows,
        ncols,
        ax_style="off",
        height_fraction=1,
        # height_fraction=297/210*nrows/ncols,
        layout="constrained",
    ) as (fig, axs):
        fig.suptitle(title, size="small")
        for _name, record in date_df.iterrows():
            row, col = record["row"], record["col"]
            ax: Axes = axs[2 * row - 2][col - 1]

            try:
                im = io.read(record["path"])
            except FileNotFoundError as ex:
                io.logger.error(f"Could not read image {record['path']}: {ex}")
                ax.axis(False)
                axs[2 * row - 1][col - 1].axis(False)
                continue

            ax.axis(True)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.imshow(im, cmap="gray")

            if genotypes:
                colorize_axes(
                    ax, TAB10_COLORS[genotypes.index(record["genotype"])], width=0.5
                )

            if row == 1:
                ax.set_title(col, fontsize="xx-small")
            if col == 1:
                ax.set_ylabel(row, fontsize="xx-small")

            # mask
            ax: Axes = axs[2 * row - 1][col - 1]
            try:
                mask = io.read(io.build_mask_path(record["path"]))
            except FileNotFoundError as ex:
                io.logger.error(
                    f"No mask found for {record['path']} at {io.build_mask_path(record['path'])}: {ex}"
                )
                mask = np.zeros(im.shape, dtype=np.bool_)

            ax.axis(True)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.imshow(mask, cmap="gray")
            if record["tip_position"]:
                ax.add_patch(
                    get_rectangle_from_box(
                        record["tip_position"],
                        record["tip_box"],
                        linewidth=2,
                        edgecolor="red",
                        facecolor="none",
                    )
                )
