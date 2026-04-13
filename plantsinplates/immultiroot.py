from typing import Callable, Iterable
import pathlib

import numpy as np
from skimage import morphology, measure
from skimage.measure import regionprops
from scipy.optimize import curve_fit
from scipy.signal import savgol_filter
import pandas as pd

from .types import (
    MaskImage,
    LabeledImage,
    IntensityImage,
    FloatCoodArray,
    FloatVector,
    LineMeasurement,
    RoiMeasurement,
)
from . import io


def keep_lines(
    labeled_lines: LabeledImage, min_length: float, max_area: float
) -> tuple[LabeledImage, int]:
    """
    Keeps only the lines that satisfy certain region criteria.

    Parameters
    ----------
    labeled_lines : np.ndarray
        Labeled lines.
    min_length : float
        Minimum length of lines to keep.
    max_area: float
        Maximum area of the lines to keep.

    Returns
    -------
    tuple[np.ndarray, int]
        Labeled long lines and the number of long lines.
    """
    ndx = 0
    long_lines = np.zeros_like(labeled_lines)
    for region in regionprops(labeled_lines):
        min_row, min_col, max_row, max_col = region.bbox
        if abs(max_row - min_row) > min_length and region.area < max_area:
            ndx += 1
            long_lines[labeled_lines == region.label] = ndx

    return long_lines, long_lines.max()


def keep_lines_in_center(
    labeled_lines: LabeledImage, region: tuple[tuple[int, int], tuple[int, int]]
) -> tuple[LabeledImage, int]:
    """
    Keeps only the lines in center

    Parameters
    ----------
    labeled_lines : np.ndarray
        Labeled lines.
    min_length : float
        Minimum length of lines to keep.

    Returns
    -------
    tuple[np.ndarray, int]
        Labeled long lines and the number of long lines.
    """

    (a, b), (c, d) = region

    touching = set()
    if a > 0:
        touching = touching.union(np.unique(labeled_lines[:a, :]))
    if b > 0:
        touching = touching.union(np.unique(labeled_lines[-b:, :]))
    if c > 0:
        touching = touching.union(np.unique(labeled_lines[:, :c]))
    if d > 0:
        touching = touching.union(np.unique(labeled_lines[:, -d:]))

    center_lines = np.zeros_like(labeled_lines)
    ndx = 0
    for label in sorted(np.unique(labeled_lines)):
        if label == 0:
            continue
        if label not in touching:
            ndx += 1
            center_lines[labeled_lines == label] = ndx

    return center_lines, center_lines.max()


def sort_labels(
    labeled_image: LabeledImage, key_func: Callable[[MaskImage], float]
) -> LabeledImage:
    """
    Sorts the labels in a labeled image based on a key function.

    Parameters
    ----------
    labeled_image : LabeledImage
        Labeled image.
    key_func : Callable[[MaskImage], float]
        Function to compute the key for sorting.

    Returns
    -------
    LabeledImage
        Labeled image with sorted labels.
    """
    values = [
        (key_func(labeled_image == label), label)
        for label in np.unique(labeled_image)
        if label != 0
    ]
    values = sorted(values)
    out = np.zeros_like(labeled_image)
    for new_label, (_score, label) in enumerate(values, 1):
        out[labeled_image == label] = new_label

    return out


def find_lines(mask: MaskImage) -> tuple[LabeledImage, int]:
    """
    Finds lines in a binary mask.

    Parameters
    ----------
    mask : np.ndarray
        Binary mask.

    Returns
    -------
    tuple[np.ndarray, int]
        Labeled lines and the number of lines.
    """
    mask = morphology.opening(mask, morphology.disk(1))
    labeled_lines, num_lines = measure.label(mask, return_num=True)
    # labeled_lines, num_lines = keep_lines(labeled_lines, 150, 2000*20)
    # labeled_lines, num_lines = keep_lines_in_center(labeled_lines, ((0, 50), (0, 0)))
    labeled_lines = sort_labels(labeled_lines, lambda x: np.argwhere(x)[:, 1].min())
    return labeled_lines, num_lines


def interpolate_line(mask: MaskImage, axis: int) -> tuple[FloatVector, FloatVector]:
    """
    Interpolate a line along a specified axis.

    Parameters
    ----------
    mask : np.ndarray
        Binary mask.
    axis : int
        Axis along which to create the line.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Independent and dependent variables of the line.
    """
    m = np.ones_like(mask, dtype=np.float64)
    m[~mask] = np.nan
    indices = np.indices(mask.shape)
    indep = np.nansum(indices[axis] * mask, axis=axis)
    count = np.nansum(mask, axis=axis)
    dep = np.arange(len(indep)).astype(np.float64)
    return dep[count > 0], indep[count > 0] / count[count > 0]


def find_center_line(
    mask: MaskImage, axis: int = 1, savgol_window: int = 100
) -> FloatCoodArray:
    """
    Finds the center line of a binary mask.

    Parameters
    ----------
    mask : np.ndarray
        Binary mask.
    axis : int, optional
        Axis along which to find the center line, by default 1.
    savgol_window : int, optional
        Window size for Savitzky-Golay filter, by default 100.

    Returns
    -------
    np.ndarray
        Center line coordinates.
    """
    x, y = interpolate_line(mask, axis)
    ys = savgol_filter(y, savgol_window, 5)
    return np.column_stack((x, ys))


def perpendicular_profile(
    im: IntensityImage, y: float, x: float, tangent: FloatCoodArray, width: int = 10
) -> FloatCoodArray:
    """
    Measures intensity along a perpendicular line to the skeleton.

    Parameters
    ----------
    im : np.ndarray
        Image data.
    y : float
        Y-coordinate of the point.
    x : float
        X-coordinate of the point.
    tangent : np.ndarray
        Tangent vector at the point.
    width : int, optional
        Width of the profile, by default 10.

    Returns
    -------
    np.ndarray
        Intensity profile.
    """
    normal = np.array([-tangent[1], tangent[0]])  # Perpendicular direction
    normal = normal / np.linalg.norm(normal)  # Normalize

    half_width = width // 2
    profile_points = np.array(
        [
            (y + i * normal[0], x + i * normal[1])
            for i in range(-half_width, half_width + 1)
        ]
    )

    profile_points = np.clip(profile_points, 0, np.array(im.shape) - 1).astype(int)
    intensities = im[profile_points[:, 0], profile_points[:, 1]]

    return intensities


def gaussian(
    x: FloatCoodArray, a: float, mu: float, sigma: float, bg: float
) -> FloatCoodArray:
    """
    Gaussian function.

    Parameters
    ----------
    x : np.ndarray
        Independent variable.
    a : float
        Amplitude.
    mu : float
        Mean.
    sigma : float
        Standard deviation.
    bg : float
        Background.

    Returns
    -------
    np.ndarray
        Gaussian function values.
    """
    return a * np.exp(-((x - mu) ** 2) / (2 * sigma**2)) + bg


def measure_roi(im: IntensityImage, mask: MaskImage) -> RoiMeasurement:
    """
    Measures the region of interest (ROI) in the image.

    Parameters
    ----------
    im : np.ndarray
        Image data.
    mask : np.ndarray
        Binary mask indicating the ROI.

    Returns
    -------
    RoiMeasurement
        Measurements of the ROI including foreground mean, foreground standard deviation,
        foreground count, background mean, background standard deviation, and background count.
    """
    return {
        "fg_mean": np.mean(im[mask]),
        "fg_std": np.std(im[mask]),
        "fg_count": np.sum(mask, initial=0).astype(int),
        "bg_mean": np.mean(im[~mask]),
        "bg_std": np.std(im[~mask]),
        "bg_count": np.sum(~mask, initial=0).astype(int),
        "box": None,
        "tip": None,
    }


def slice_around(center: int, width: int) -> slice:
    return slice(center - width // 2, center + (width - width // 2))


def measure_roi_at_tip(
    im: IntensityImage,
    mask: MaskImage,
    box: int | tuple[int, int],
    measured_line: LineMeasurement | None = None,
) -> RoiMeasurement:
    if measured_line is None:
        measured_line = measure_lines(im, 1 * mask)[1]

    if isinstance(box, int):
        box = (box, box)

    tip = measured_line["centerline"][np.argmax(measured_line["amplitude"]), :]

    s0 = slice_around(int(tip[0]), box[0])
    s1 = slice_around(int(tip[1]), box[1])

    return {
        "tip": tip,
        "box": box,
        **measure_roi(im[s0, s1], mask[s0, s1]),
    }


def measure_lines(
    im: IntensityImage, labeled_lines: LabeledImage
) -> dict[int, LineMeasurement]:
    """
    Measures intensity and FWHM along the skeleton of each line.

    Parameters
    ----------
    im : np.ndarray
        Image data.
    labeled_lines : np.ndarray
        Labeled lines.

    Returns
    -------
    dict[int, dict[str, list[float]]]
        Line properties including centerline, intensity, amplitude, FWHM, and background.
    """
    line_properties = {}

    for label in np.unique(labeled_lines):
        if label == 0:
            continue  # Skip background

        line_mask = labeled_lines == label
        centerline = find_center_line(line_mask)

        dy_dx = np.gradient(centerline, axis=0)
        tangents = dy_dx / np.linalg.norm(dy_dx, axis=1)[:, None]  # Normalize

        # Measure perpendicular intensity profiles
        intensity = []
        amplitude = []
        sigma = []
        bg = []
        for (y, x), tangent in zip(centerline, tangents):
            profile = perpendicular_profile(im, y, x, tangent, width=100)

            try:
                popt, _ = curve_fit(
                    gaussian,
                    np.arange(len(profile)),
                    profile,
                    p0=[np.max(profile), 50, 10, 100],
                )

                intensity.append(im[int(y), int(x)])
                amplitude.append(popt[0])
                sigma.append(popt[2])
                bg.append(popt[3])
            except Exception:
                print(f"Skipping, probably not a line {np.sum(line_mask)}")
                pass

        line_properties[label] = {
            "centerline": centerline,
            "intensity": np.asarray(intensity),
            "amplitude": np.asarray(amplitude),
            "sigma": np.asarray(sigma),
            "bg": np.asarray(bg),
        }

    return line_properties


def analyze_image(im: IntensityImage, mask: MaskImage) -> pd.DataFrame:
    records = []

    labeled_lines, num_lines = find_lines(mask)

    try:
        line_properties = measure_lines(im, labeled_lines)
    except:
        raise

    for label, line_props in line_properties.items():
        records.append(
            {
                "label": label,
                **line_props,
                **measure_roi_at_tip(
                    im, labeled_lines == label, (100, 100), line_props
                ),
            }
        )

    df = pd.DataFrame.from_records(records)
    return df


def analyze_paths(paths: Iterable[pathlib.Path]) -> pd.DataFrame:
    out = []
    for p in paths:
        im = io.read(p)
        mask = im > 130
        df = analyze_image(im, mask)
        df["fullpath"] = str(p)
        out.append(df)

    return pd.concat(out)
