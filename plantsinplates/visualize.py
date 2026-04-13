import contextlib
from typing import Generator, Literal, Any
import datetime
import textwrap
from matplotlib.axes import Axes
from matplotlib.figure import Figure

import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd
import skimage.io as skio
import skimage.morphology as skimorph
from seaborn import objects as so
from seaborn import axes_style
from matplotlib.colors import ListedColormap, BoundaryNorm


from . import io

CMAP3 = ListedColormap(["black", "gray", "cyan"])
NORM_CMAP3 = BoundaryNorm([0, 1, 2, 3], CMAP3.N)


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


def axes_as_2d(axs: Axes, nrows: int, ncols: int) -> np.ndarray:
    if nrows == 1 and ncols == 1:
        return np.array([[axs]])
    if nrows == 1:
        return np.array([axs])
    if ncols == 1:
        return np.array([[ax] for ax in axs])
    return axs


def extract_centerline_coords(
    record: pd.Series, skeleton_mask: np.ndarray
) -> np.ndarray:
    if "skel__coordinates" in record and isinstance(
        record["skel__coordinates"], np.ndarray
    ):
        coords = record["skel__coordinates"]
        if coords.ndim == 2 and coords.shape[1] == 2 and len(coords) > 0:
            return coords

    rows, cols = np.where(skeleton_mask > 0)
    if len(rows) == 0:
        return np.empty((0, 2), dtype=np.int64)
    order = np.argsort(rows)
    return np.column_stack((rows[order], cols[order]))


def centerline_mask_style(df: pd.DataFrame) -> tuple[int, float]:
    config = df.attrs.get("measurement_config", {})
    perpendicular_width = int(config.get("perpendicular_width", 1))
    # Match measurement semantics: profiles use half_width = perpendicular_width // 2.
    dilation_radius = max(0, perpendicular_width // 2)
    alpha = 0.6
    return dilation_radius, alpha


def relabel(s: str) -> str:
    if s == "length":
        return "length [mm]"
    if s == "delta_length_per_day":
        return "growth [mm/day]"
    if s == "delta_signal_intensity_per_day":
        return "signal intensity change [1/day]"
    if s == "signal_intensity":
        return "signal intensity"
    if s == "avg_signal_intensity":
        return "average signal intensity"
    if s == "day_number":
        return "day"
    if s == "plant_id_in_gt":
        return "plant number (gt)"
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
            .add(so.Dot(), marker="plant_id_in_gt", legend=False)
            .theme(
                axes_style("whitegrid")
                | {"legend.fontsize": 8, "legend.title_fontsize": 10}
            )
            .layout(extent=(0.05, 0.05, 0.80, 0.95), engine="tight")
            .scale(color=TAB10_COLORS, alpha=(1, 0.3))  # type: ignore
            .label(x=relabel, y=relabel, alpha=relabel, marker=relabel)
            .facet(**facet_kwargs)
            .on(fig)
            .plot(pyplot=True)
        )


def generate_analysis_details_page(
    pdf_writer: PdfPages,
    details: list[tuple[str, str]],
    *,
    title: str = "Analysis details",
):
    """Generate a PDF page with analysis details and configuration."""

    lines: list[str] = []
    for key, value in details:
        wrapped = textwrap.wrap(
            f"{key}: {value}",
            width=112,
            break_long_words=False,
            break_on_hyphens=False,
        )
        if wrapped:
            lines.extend(wrapped)
        else:
            lines.append(f"{key}:")

    with pdf_figure(pdf_writer, 1, 1, ax_style="off", layout="none") as (fig, axs):
        ax = axs if isinstance(axs, Axes) else axs.flatten()[0]
        ax.axis(False)
        fig.suptitle(title, size="medium")
        ax.text(
            0.03,
            0.95,
            "\n".join(lines),
            ha="left",
            va="top",
            fontsize=9,
            family="monospace",
        )
        default_footnote(fig)


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

    #################
    # y-categoricals
    #################

    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="length",
            y="day_number",
            color="genotype",
        ),
        dict(row="genotype", col="plate"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="delta_length_per_day",
            y="day_number",
            color="genotype",
            marker="plate",
        ),
        dict(row="genotype", col="plate"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="signal_intensity",
            y="day_number",
            color="genotype",
        ),
        dict(row="genotype", col="plate"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="delta_signal_intensity_per_day",
            y="day_number",
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
            x="signal_intensity",
            y="length",
            color="genotype",
            alpha="day_number",
        ),
        dict(row="genotype", col="plate"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=experiment_df,
            x="delta_signal_intensity_per_day",
            y="delta_length_per_day",
            color="genotype",
            alpha="day_number",
        ),
        dict(row="genotype", col="plate"),
    )


def generate_plateview(
    pdf_writer: PdfPages,
    plate_df: pd.DataFrame,
    *,
    measurement_method: Literal["box", "centerline"] = "box",
):
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

    with pdf_figure(pdf_writer, 4, 4, ax_style="off") as (fig, axs):
        ax_rest = axs[1:3, :].flatten()
        gs = axs[0, 0].get_gridspec()

        ax_bottom = fig.add_subplot(gs[-1, :])

        axs[0, 0].remove()
        axs[0, 1].remove()
        axs[0, 2].remove()
        axs[0, 3].remove()

        axbig = fig.add_subplot(gs[0, :])
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
                    "No plate\noverview\nimage",
                    size=10,
                    horizontalalignment="center",
                    verticalalignment="center",
                )
            else:
                im = skio.imread(overview_path)
                ax.imshow(im)
                ax.set_anchor("N")

        title = f"Plate {plate_df['plate'].iloc[0]} - {len(plate_df)} records"
        fig.suptitle(f"{title}\n{nrows} rows x {ncols} cols", size="small")

        out = plate_df.pivot_table(
            values="col",
            columns="date",
            index="genotype",
            aggfunc="count",
            fill_value=0,
        )
        out_reset = out.reset_index()
        columns = ["Genotype"] + [f"{col}" for col in out.columns]
        cell_text = out_reset[["genotype"] + [col for col in out.columns]].values

        genotypes = cell_text[:, 0].tolist()

        cell_text = np.column_stack(([""] * cell_text.shape[0], cell_text))
        table = axbig.table(
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

        _tmp = pd.DataFrame({"x": np.arange(1, out.values.max() + 2)})
        _tmp["x"] = _tmp["x"].astype(str)
        (
            so.Plot(data=_tmp, x="x", y=1, marker="x")
            .add(so.Dot(), legend=False)
            .layout(engine="tight")
            .theme(
                {
                    "font.size": 1,
                    "axes.titlesize": 1,
                    "axes.labelsize": 1,
                    "xtick.labelsize": 1,
                    "ytick.labelsize": 1,
                    "legend.fontsize": 1,
                }
            )
            .label(x="plant number (gt)", y="")
            .scale(y=so.Nominal().label(None))
            .on(ax_bottom)
            .plot()
        )

    centerline_dilation_radius, centerline_alpha = centerline_mask_style(plate_df)
    for ndx, (date, gdf) in enumerate(plate_df.groupby("date")):
        generate_dateview(
            pdf_writer,
            gdf,
            measurement_method=measurement_method,
            centerline_dilation_radius=centerline_dilation_radius,
            centerline_alpha=centerline_alpha,
        )

    #################
    # y-categoricals
    #################

    seaborn_plot(
        pdf_writer,
        dict(data=plate_df, x="length", y="day_number", color="genotype"),
        dict(row="genotype"),
    )
    seaborn_plot(
        pdf_writer,
        dict(data=plate_df, x="delta_length_per_day", y="day_number", color="genotype"),
        dict(row="genotype"),
    )
    seaborn_plot(
        pdf_writer,
        dict(data=plate_df, x="signal_intensity", y="day_number", color="genotype"),
        dict(row="genotype"),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=plate_df,
            x="delta_signal_intensity_per_day",
            y="day_number",
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
            x="signal_intensity",
            y="length",
            color="genotype",
            alpha="day_number",
        ),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=plate_df,
            x="delta_signal_intensity_per_day",
            y="delta_length_per_day",
            color="genotype",
            alpha="day_number",
        ),
    )
    seaborn_plot(
        pdf_writer,
        dict(
            data=plate_df,
            x="avg_signal_intensity",
            y="delta_length_per_day",
            color="genotype",
            alpha="day_number",
        ),
    )


def generate_dateview(
    pdf_writer: PdfPages,
    date_df: pd.DataFrame,
    *,
    measurement_method: Literal["box", "centerline"] = "box",
    centerline_dilation_radius: int = 0,
    centerline_alpha: float = 0.6,
):
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
        axs = axes_as_2d(axs, 2 * nrows, ncols)
        fig.suptitle(title, size="small")
        for _name, record in date_df.iterrows():
            row, col = record["row"], record["col"]
            ax_image: Axes = axs[2 * row - 2][col - 1]

            try:
                im = io.read(record["path"])
            except FileNotFoundError as ex:
                io.logger.error(f"Could not read image {record['path']}: {ex}")
                ax_image.axis(False)
                axs[2 * row - 1][col - 1].axis(False)
                continue

            ax_image.axis(True)
            ax_image.set_xticks([])
            ax_image.set_yticks([])
            ax_image.imshow(im, cmap="gray")

            ax_image.text(
                0.02,
                0.98,
                record["plant_id_in_gt"],
                transform=ax_image.transAxes,  # Coordinates relative to Axes (0 to 1)
                fontsize=5,
                color="white",
                ha="left",
                va="top",
            )

            if genotypes:
                colorize_axes(
                    ax_image,
                    TAB10_COLORS[genotypes.index(record["genotype"])],
                    width=1.5,
                )

            if row == 1:
                ax_image.set_title(col, fontsize="xx-small")
            if col == 1:
                ax_image.set_ylabel(row, fontsize="xx-small")

            # mask
            ax_mask: Axes = axs[2 * row - 1][col - 1]
            try:
                mask = io.read(io.build_mask_path(record["path"]))
            except FileNotFoundError as ex:
                io.logger.error(
                    f"No mask found for {record['path']} at {io.build_mask_path(record['path'])}: {ex}"
                )
                mask = np.zeros(im.shape, dtype=np.bool_)

            try:
                skeleton_mask = io.read(io.build_skeleton_path(record["path"]))
            except FileNotFoundError as ex:
                io.logger.error(
                    f"No skeleton mask found for {record['path']} at {io.build_skeleton_path(record['path'])}: {ex}"
                )
                skeleton_mask = np.zeros(im.shape, dtype=np.bool_)

            ax_mask.axis(True)
            ax_mask.set_xticks([])
            ax_mask.set_yticks([])

            ax_mask.imshow(
                1 * mask / mask.max() + 1 * skeleton_mask / skeleton_mask.max(),
                cmap=CMAP3,
                norm=NORM_CMAP3,
                interpolation="none",
            )
            # ax.set_title(f"{np.count_nonzero(mask)}\n{np.count_nonzero(skeleton_mask)}", fontsize=4)
            if (
                measurement_method == "box"
                and "tip_position" in date_df.columns
                and "tip_box" in date_df.columns
                and record["tip_position"]
            ):
                ax_mask.add_patch(
                    get_rectangle_from_box(
                        record["tip_position"],
                        record["tip_box"],
                        linewidth=2,
                        edgecolor="red",
                        facecolor="none",
                    )
                )
            elif measurement_method == "centerline":
                centerline_coords = extract_centerline_coords(record, skeleton_mask)
                if len(centerline_coords) > 0:
                    centerline_mask = np.zeros_like(skeleton_mask, dtype=np.bool_)
                    coords = centerline_coords.astype(np.int64)
                    rows = np.clip(coords[:, 0], 0, centerline_mask.shape[0] - 1)
                    cols = np.clip(coords[:, 1], 0, centerline_mask.shape[1] - 1)
                    centerline_mask[rows, cols] = True

                    if centerline_dilation_radius > 0:
                        centerline_mask = skimorph.binary_dilation(
                            centerline_mask,
                            skimorph.disk(centerline_dilation_radius),
                        )

                    overlay = np.zeros((*centerline_mask.shape, 4), dtype=np.float32)
                    overlay[..., 0] = 1.0  # red
                    overlay[..., 3] = (
                        centerline_mask.astype(np.float32) * centerline_alpha
                    )
                    ax_image.imshow(overlay, interpolation="none")

    if measurement_method != "centerline" or "skel_intensities" not in date_df.columns:
        return

    with pdf_figure(
        pdf_writer,
        2 * nrows,
        ncols,
        ax_style="off",
        height_fraction=1,
        layout="constrained",
    ) as (fig, axs):
        axs = axes_as_2d(axs, 2 * nrows, ncols)
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

            ax.text(
                0.02,
                0.98,
                record["plant_id_in_gt"],
                transform=ax.transAxes,
                fontsize=5,
                color="white",
                ha="left",
                va="top",
            )

            if genotypes:
                colorize_axes(
                    ax, TAB10_COLORS[genotypes.index(record["genotype"])], width=1.5
                )

            if row == 1:
                ax.set_title(col, fontsize="xx-small")
            if col == 1:
                ax.set_ylabel(row, fontsize="xx-small")

            ax.plot(record["skel_intensities"])

            # Keep bottom row empty for the method-specific page layout.
            ax = axs[2 * row - 1][col - 1]
            ax.axis(False)
