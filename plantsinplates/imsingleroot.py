from typing import Any, Callable
import pathlib

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import savgol_filter
from skimage import morphology, measure, io as skio
from skimage.measure import regionprops


from . import io
from . import types as _types
from . import skeleton_utils
from .measurement_config import MeasurementConfig

from .types import (
    MaskImage,
    LabeledImage,
    IntensityImage,
    FloatVector,
    RoiMeasurement,
    SkeletonMeasurement,
)

MASK_ARTIFACT_VERSION = 1
SKELETON_ARTIFACT_VERSION = 1


def prefix_keys(
    prefix: str, adict: dict[str, Any], skip_: bool = False
) -> dict[str, Any]:
    """Prefix all keys in a dictionary.

    Parameters
    ----------
    prefix : str
        String to prepend to each key.
    adict : dict[str, Any]
        Original dictionary.

    Returns
    -------
    dict[str, Any]
        New dictionary with prefixed keys.
    """
    if skip_:
        return {(prefix + k): v for k, v in adict.items() if not k.startswith("_")}
    else:
        return {(prefix + k): v for k, v in adict.items()}


def sort_labels(
    labeled_image: LabeledImage, key_func: Callable[[MaskImage], float]
) -> LabeledImage:
    """Sort the labels in a labeled image based on a key function.

    Parameters
    ----------
    labeled_image : LabeledImage
        Labeled image.
    key_func : Callable[[MaskImage], float]
        Function to compute the key for sorting each label.

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


def _compute_mask(im: IntensityImage) -> MaskImage:
    mask = im > 130
    mask = morphology.remove_small_objects(mask)
    mask = morphology.remove_small_holes(mask)
    assert _types.is_mask_image(mask), "Not a mask image"
    return mask


def _mask_manifest_matches(
    mask_entry: dict[str, Any] | None, source_signature: dict[str, Any]
) -> bool:
    if mask_entry is None:
        return False
    return mask_entry.get(
        "version"
    ) == MASK_ARTIFACT_VERSION and io.source_signature_matches(
        mask_entry.get("source"), source_signature
    )


def _skeleton_manifest_matches(
    skeleton_entry: dict[str, Any] | None,
    source_signature: dict[str, Any],
    savgol_window: int,
) -> bool:
    if skeleton_entry is None:
        return False
    return (
        skeleton_entry.get("version") == SKELETON_ARTIFACT_VERSION
        and skeleton_entry.get("savgol_window") == savgol_window
        and io.source_signature_matches(skeleton_entry.get("source"), source_signature)
    )


def interpolate_line(mask: MaskImage, axis: int) -> tuple[FloatVector, FloatVector]:
    """Interpolate a line along a specified axis of a binary mask.

    Parameters
    ----------
    mask : MaskImage
        Binary mask of the object.
    axis : int
        Axis along which to compute interpolation.

    Returns
    -------
    tuple[FloatVector, FloatVector]
        Independent and dependent variables of the interpolated line.
    """
    m = np.ones_like(mask, dtype=np.float64)
    m[~mask] = np.nan
    indices = np.indices(mask.shape)
    indep = np.nansum(indices[axis] * mask, axis=axis)
    count = np.nansum(mask, axis=axis)
    dep = np.arange(len(indep)).astype(np.float64)
    return dep[count > 0], indep[count > 0] / count[count > 0]


def measure_roi(im: IntensityImage, mask: MaskImage) -> RoiMeasurement:
    """Measure intensity statistics inside and outside a region of interest.

    Parameters
    ----------
    im : IntensityImage
        Image data.
    mask : MaskImage
        Binary mask indicating the ROI.

    Returns
    -------
    RoiMeasurement
        Measurements of the ROI and background.
    """
    return {
        "fg_mean": np.mean(im[mask]),
        "fg_std": np.std(im[mask]),
        "fg_count": np.sum(mask, initial=0).astype(int),
        "bg_mean": np.mean(im[~mask]),
        "bg_std": np.std(im[~mask]),
        "bg_count": np.sum(~mask, initial=0).astype(int),
        "box": None,
        "position": None,
    }


def slice_around(center: int, width: int) -> slice:
    """Create a slice centered around a given position.

    Parameters
    ----------
    center : int
        Center position of the slice.
    width : int
        Total width of the slice.

    Returns
    -------
    slice
        Slice object spanning the desired range.
    """
    return slice(center - width // 2, center + (width - width // 2))


def _shifted_box_center_from_tip(
    tip: tuple[int, int],
    box: tuple[int, int],
    mask: MaskImage,
    box_offset: float,
) -> tuple[int, int]:
    if box_offset == 0:
        return tip

    points = np.argwhere(mask)
    if len(points) == 0:
        return tip

    centroid = np.mean(points, axis=0)
    direction = np.asarray(tip, dtype=np.float64) - centroid
    norm = np.linalg.norm(direction)
    if norm <= 1e-9:
        direction = np.asarray([1.0, 0.0], dtype=np.float64)
    else:
        direction = direction / norm

    box_extent = abs(direction[0]) * float(box[0]) + abs(direction[1]) * float(box[1])
    shift = direction * box_extent * box_offset
    center = np.asarray(tip, dtype=np.float64) + shift

    row, col = np.rint(center).astype(np.int64)
    return int(row), int(col)


def measure_roi_at_tip_simple(
    im: IntensityImage,
    mask: MaskImage,
    box: int | tuple[int, int],
    box_offset: float = 0.0,
) -> RoiMeasurement:
    """Measure a region of interest around the tip of a root.

    Parameters
    ----------
    im : IntensityImage
        Image data.
    mask : MaskImage
        Binary mask of the root.
    box : int or tuple[int, int]
        Size of the measurement box around the tip.
    box_offset : float
        Signed offset in box-size units.
        0 centers at the tip, +1 shifts one box size towards the tip,
        and -1 shifts one box size away from the tip.

    Returns
    -------
    RoiMeasurement
        Measurements of the tip region.
    """
    if isinstance(box, int):
        box = (box, box)

    # 1. Measure the width of the line
    # 2. center the tip minus the width // 2

    widths = np.sum(mask, axis=1)
    approx_width = np.median(widths[widths > 0]) if np.any(widths > 0) else 1

    tip0 = int(np.max(np.argwhere(widths)) - approx_width // 2)
    tip1 = int(np.argwhere(mask[tip0, :])[0][0])
    position = _shifted_box_center_from_tip((tip0, tip1), box, mask, box_offset)

    s0 = slice_around(position[0], box[0])
    s1 = slice_around(position[1], box[1])

    return {
        **measure_roi(im[s0, s1], mask[s0, s1]),
        "position": position,
        "box": box,
    }


def _empty_region_measurement() -> dict[str, int | float]:
    return {
        "start_idx": -1,
        "end_idx": -1,
        "mean": np.nan,
        "integrated": np.nan,
        "count": 0,
    }


def _measure_region(
    intensities: np.ndarray, start_idx: int | None, end_idx: int | None
) -> dict[str, int | float]:
    if (
        start_idx is None
        or end_idx is None
        or start_idx < 0
        or end_idx < 0
        or end_idx < start_idx
        or start_idx >= len(intensities)
    ):
        return _empty_region_measurement()

    clipped_start = int(max(0, start_idx))
    clipped_end = int(min(len(intensities) - 1, end_idx))
    if clipped_end < clipped_start:
        return _empty_region_measurement()

    region = intensities[clipped_start : clipped_end + 1]
    if region.size == 0:
        return _empty_region_measurement()

    return {
        "start_idx": clipped_start,
        "end_idx": clipped_end,
        "mean": float(np.mean(region)),
        "integrated": float(np.sum(region)),
        "count": int(region.size),
    }


def _centerline_profile_model(
    x: np.ndarray,
    offset: float,
    tip_peak: float,
    tip_mu: float,
    tip_sigma_left: float,
    tip_sigma_right: float,
    cap_peak: float,
    cap_mu: float,
    cap_sigma: float,
) -> np.ndarray:
    """Continuous model: asymmetric tip Gaussian + optional cap Gaussian."""
    x = np.asarray(x, dtype=np.float64)
    dx = x - float(tip_mu)

    sigma_left = max(float(tip_sigma_left), 1e-6)
    sigma_right = max(float(tip_sigma_right), 1e-6)
    cap_sigma_safe = max(float(cap_sigma), 1e-6)

    tip_sigma = np.where(dx <= 0.0, sigma_left, sigma_right)
    tip_component = float(tip_peak) * np.exp(-(dx**2) / (2.0 * tip_sigma**2))

    cap_component = float(cap_peak) * np.exp(
        -((x - float(cap_mu)) ** 2) / (2.0 * cap_sigma_safe**2)
    )

    return float(offset) + tip_component + cap_component


def _fit_centerline_profile(
    profile: np.ndarray,
) -> tuple[np.ndarray, dict[str, float], bool]:
    y = np.asarray(profile, dtype=np.float64)
    n = len(y)
    if n == 0:
        return np.asarray([], dtype=np.float64), {}, False

    x = np.arange(n, dtype=np.float64)
    y_min = float(np.min(y))
    y_max = float(np.max(y))
    y_range = max(y_max - y_min, 1e-9)

    tip_mu0 = float(np.argmax(y))
    tip_peak0 = max(y_max - y_min, 0.0)

    tip_idx0 = int(round(tip_mu0))
    left_points = max(tip_idx0, 1)
    right_points = max(n - tip_idx0 - 1, 1)
    sigma_left0 = max(left_points / 3.0, 1.0)
    sigma_right0 = max(right_points / 5.0, 0.75)

    if tip_idx0 < n - 1:
        edge_width_prior = max(4, int(round(0.12 * n)))
        edge_start = max(0, n - edge_width_prior)
        edge_segment = y[edge_start:]
        cap_idx0 = int(edge_start + int(np.argmax(edge_segment)))
        cap_mu0 = float(cap_idx0)
        baseline_segment = y[max(0, edge_start - edge_width_prior) : edge_start]
        baseline0 = (
            float(np.median(baseline_segment))
            if baseline_segment.size > 0
            else float(y_min)
        )
        cap_peak0 = max(float(y[cap_idx0] - baseline0), 0.0)
        cap_sigma0 = max(float(edge_width_prior) / 6.0, 0.75)
    else:
        cap_mu0 = tip_mu0
        cap_peak0 = 0.0
        cap_sigma0 = 0.75

    edge_width_prior = max(4, int(round(0.12 * n)))
    edge_start = max(0, n - edge_width_prior)
    cap_sigma_upper = max(1.0, float(edge_width_prior) / 2.0)

    p0 = np.asarray(
        [
            y_min,
            tip_peak0,
            tip_mu0,
            sigma_left0,
            sigma_right0,
            cap_peak0,
            cap_mu0,
            cap_sigma0,
        ],
        dtype=np.float64,
    )

    lower_bounds = np.asarray(
        [
            y_min - 2.0 * y_range,
            0.0,
            0.0,
            0.5,
            0.3,
            0.0,
            float(edge_start),
            0.3,
        ],
        dtype=np.float64,
    )
    upper_bounds = np.asarray(
        [
            y_max + 2.0 * y_range,
            np.inf,
            float(n - 1),
            float(max(n, 2)),
            float(max(n, 2)),
            np.inf,
            float(max(n - 1, 1)),
            float(cap_sigma_upper),
        ],
        dtype=np.float64,
    )
    p0 = np.clip(p0, lower_bounds, upper_bounds)

    if n < 6:
        popt = p0
        fit_ok = False
    else:
        try:
            popt, _ = curve_fit(
                _centerline_profile_model,
                x,
                y,
                p0=p0,
                bounds=(lower_bounds, upper_bounds),
                maxfev=10000,
            )
            fit_ok = True
        except Exception:
            popt = p0
            fit_ok = False

    fitted = _centerline_profile_model(x, *popt)
    tip_mu = float(np.clip(popt[2], 0.0, n - 1))
    cap_mu = float(np.clip(popt[6], 0.0, n - 1))
    params = {
        "offset": float(popt[0]),
        "tip_peak": float(max(popt[1], 0.0)),
        "tip_mu": tip_mu,
        "tip_sigma_left": float(max(popt[3], 0.3)),
        "tip_sigma_right": float(max(popt[4], 0.3)),
        "cap_peak": float(max(popt[5], 0.0)),
        "cap_mu": cap_mu,
        "cap_sigma": float(max(popt[7], 0.3)),
    }
    return fitted, params, fit_ok


def _segment_centerline_regions(intensities: np.ndarray) -> dict[str, Any]:
    n = len(intensities)
    if n == 0:
        return {
            "cap_present": False,
            **{f"cap_{k}": v for k, v in _empty_region_measurement().items()},
            **{f"tip_{k}": v for k, v in _empty_region_measurement().items()},
            **{f"middle_{k}": v for k, v in _empty_region_measurement().items()},
            **{f"far_{k}": v for k, v in _empty_region_measurement().items()},
        }

    fitted_profile, fitted_params, fit_ok = _fit_centerline_profile(intensities)
    profile = fitted_profile if fit_ok else intensities

    profile_range = float(np.max(profile) - np.min(profile))
    eps = max(profile_range * 1e-9, 1e-12)

    tip_mu = float(
        np.clip(fitted_params.get("tip_mu", float(np.argmax(profile))), 0, n - 1)
    )
    tip_peak = float(max(fitted_params.get("tip_peak", profile_range), 0.0))
    tip_sigma_left = max(float(fitted_params.get("tip_sigma_left", 1.0)), 0.3)
    tip_sigma_right = max(float(fitted_params.get("tip_sigma_right", 1.0)), 0.3)
    tip_sigma_right_region = min(tip_sigma_right, tip_sigma_left)

    tip_support_sigma = 1.5
    tip_start_idx = int(
        np.clip(np.floor(tip_mu - tip_support_sigma * tip_sigma_left), 0, n - 1)
    )
    tip_end_idx = int(
        np.clip(np.ceil(tip_mu + tip_support_sigma * tip_sigma_right_region), 0, n - 1)
    )

    cap_present = False
    cap_start_idx: int | None = None
    cap_end_idx: int | None = None

    cap_peak = float(max(fitted_params.get("cap_peak", 0.0), 0.0))
    cap_mu = float(np.clip(fitted_params.get("cap_mu", float(n - 1)), 0, n - 1))
    cap_sigma = max(float(fitted_params.get("cap_sigma", 1.0)), 0.3)

    edge_width = max(4, int(round(0.12 * n)))
    edge_start = max(0, n - edge_width)
    edge_values = profile[edge_start:]
    edge_median = float(np.median(edge_values))
    edge_mad = float(np.median(np.abs(edge_values - edge_median)))
    edge_noise = max(1.4826 * edge_mad, eps)

    cap_peak_min = max(profile_range * 0.10, 3.0 * edge_noise)
    cap_is_near_edge = cap_mu >= edge_start
    cap_is_separated = (cap_mu - 1.0 * cap_sigma) > (
        tip_mu + 0.2 * tip_sigma_right_region
    )
    cap_is_large_vs_tip = cap_peak >= max(0.15 * tip_peak, eps)

    cap_support_sigma = 2.0
    if (
        fit_ok
        and cap_peak >= cap_peak_min
        and cap_is_near_edge
        and cap_is_separated
        and cap_is_large_vs_tip
    ):
        cap_start_idx = int(
            np.clip(np.floor(cap_mu - cap_support_sigma * cap_sigma), 0, n - 1)
        )
        cap_end_idx = int(n - 1)
        if cap_start_idx <= cap_end_idx:
            cap_present = True
            tip_end_idx = min(tip_end_idx, cap_start_idx - 1)

    if tip_end_idx < tip_start_idx:
        tip_center_idx = int(np.clip(round(tip_mu), 0, n - 1))
        tip_start_idx = tip_center_idx
        tip_end_idx = tip_center_idx

    tip_width = max(1, tip_end_idx - tip_start_idx + 1)

    middle_end_idx = tip_start_idx - 1
    middle_start_idx = middle_end_idx - tip_width + 1
    if middle_end_idx < 0:
        middle_start_idx = -1
        middle_end_idx = -1
    else:
        middle_start_idx = max(0, middle_start_idx)

    far_end_idx = middle_start_idx - 1
    far_start_idx = far_end_idx - tip_width + 1
    if far_end_idx < 0:
        far_start_idx = -1
        far_end_idx = -1
    else:
        far_start_idx = max(0, far_start_idx)

    cap_measurement = _measure_region(intensities, cap_start_idx, cap_end_idx)
    tip_measurement = _measure_region(intensities, tip_start_idx, tip_end_idx)
    middle_measurement = _measure_region(intensities, middle_start_idx, middle_end_idx)
    far_measurement = _measure_region(intensities, far_start_idx, far_end_idx)

    return {
        "cap_present": bool(cap_present),
        "_profile_fit_ok": bool(fit_ok),
        "_profile_fit": fitted_profile,
        "_profile_tip_mu": fitted_params.get("tip_mu", np.nan),
        "_profile_tip_sigma_left": fitted_params.get("tip_sigma_left", np.nan),
        "_profile_tip_sigma_right": fitted_params.get("tip_sigma_right", np.nan),
        "_profile_cap_mu": fitted_params.get("cap_mu", np.nan),
        "_profile_cap_sigma": fitted_params.get("cap_sigma", np.nan),
        **{f"cap_{k}": v for k, v in cap_measurement.items()},
        **{f"tip_{k}": v for k, v in tip_measurement.items()},
        **{f"middle_{k}": v for k, v in middle_measurement.items()},
        **{f"far_{k}": v for k, v in far_measurement.items()},
    }


def _smooth_skeleton_intensities(
    intensities: np.ndarray, intensity_savgol_window: int
) -> np.ndarray:
    if intensity_savgol_window <= 0:
        return intensities

    if len(intensities) <= 3:
        return intensities

    window = min(intensity_savgol_window, len(intensities))
    if window % 2 == 0:
        window -= 1

    polyorder = 3
    if window <= polyorder:
        return intensities

    return np.asarray(
        savgol_filter(intensities, window_length=window, polyorder=polyorder),
        dtype=np.float64,
    )


def _gaussian_profile_model(
    x: np.ndarray, peak: float, mu: float, sigma: float, offset: float
) -> np.ndarray:
    sigma_safe = max(float(sigma), 1e-6)
    return offset + peak * np.exp(-((x - mu) ** 2) / (2.0 * sigma_safe**2))


def _fit_gaussian_profile(
    profile: np.ndarray,
) -> tuple[float, float, float, float, bool]:
    y = np.asarray(profile, dtype=np.float64)
    x = np.arange(len(y), dtype=np.float64)

    if len(y) == 0:
        return 0.0, 0.0, 1.0, 0.0, False

    offset0 = float(np.min(y))
    peak0 = float(max(np.max(y) - offset0, 0.0))
    mu0 = float(np.argmax(y))
    sigma0 = max(float(len(y)) / 8.0, 1.0)

    if len(y) < 4:
        return peak0, mu0, sigma0, offset0, False

    bounds = (
        [0.0, 0.0, 0.5, -np.inf],
        [np.inf, float(len(y) - 1), float(len(y)), np.inf],
    )

    try:
        popt, _ = curve_fit(
            _gaussian_profile_model,
            x,
            y,
            p0=[peak0, mu0, sigma0, offset0],
            bounds=bounds,
            maxfev=3000,
        )
        peak = float(max(popt[0], 0.0))
        mu = float(np.clip(popt[1], 0.0, len(y) - 1))
        sigma = float(max(popt[2], 0.5))
        offset = float(popt[3])
        return peak, mu, sigma, offset, True
    except Exception:
        return peak0, mu0, sigma0, offset0, False


def _compose_skeleton_measurement(
    intensities: np.ndarray, coordinates: np.ndarray, length: int
) -> SkeletonMeasurement:
    intensities = np.asarray(intensities, dtype=np.float64)
    coordinates = np.asarray(coordinates, dtype=np.float64)
    if coordinates.ndim != 2 or coordinates.shape[1] != 2:
        raise ValueError(
            f"Expected coordinates shape [_, 2], found {coordinates.shape}"
        )

    if len(intensities) == 0:
        raise ValueError("No points in perpendicular profiles")

    last_argmax = len(intensities) - np.argmax(intensities[::-1]) - 1
    distance_to_max = np.linalg.norm(coordinates - coordinates[last_argmax, :], axis=1)
    sel = np.abs(distance_to_max <= length)
    region_measurements = _segment_centerline_regions(intensities)

    return {
        "intensities": intensities,
        "intensity": float(np.sum(intensities[sel])),
        "pixel_count": int(np.sum(sel)),
        "_coordinates": np.rint(coordinates).astype(np.int64),
        **region_measurements,
    }


def measure_skeleton(
    im: IntensityImage,
    skeleton: MaskImage,
    perpendicular_width: int,
    length: int,
    intensity_savgol_window: int = 0,
) -> SkeletonMeasurement:
    """Measure a region of interest around the tip of a root.

    Parameters
    ----------
    im : IntensityImage
        Image data.
    mask : MaskImage
        Binary mask of the root.
    um_per_pixel : float

    Returns
    -------
    RoiMeasurement
        Measurements of the tip region.
    """

    assert np.sum(skeleton) > 0, "No points in the skeleton"
    results = skeleton_utils.get_ordered_perpendicular_profiles(
        im, skeleton, perpendicular_width
    )
    assert len(results) > 0, "No points in perpendicular profiles"

    intensities = np.asarray(
        [np.sum(result["intensities"]) for result in results], dtype=np.float64
    )
    intensities = _smooth_skeleton_intensities(intensities, intensity_savgol_window)
    coordinates = np.asarray(
        [result["coordinates"] for result in results], dtype=np.float64
    )

    return _compose_skeleton_measurement(intensities, coordinates, length)


def measure_skeleton_gaussian(
    im: IntensityImage,
    skeleton: MaskImage,
    perpendicular_width: int,
    length: int,
    intensity_savgol_window: int = 0,
) -> SkeletonMeasurement:
    assert np.sum(skeleton) > 0, "No points in the skeleton"
    line_width = max(1, 5 * int(perpendicular_width))
    results = skeleton_utils.get_ordered_perpendicular_profiles(
        im, skeleton, line_width
    )
    assert len(results) > 0, "No points in perpendicular profiles"

    peak_profile: list[float] = []
    gauss_offset: list[float] = []
    gauss_sigma: list[float] = []
    gauss_mu_offset: list[float] = []
    gauss_fit_ok: list[float] = []
    peak_coordinates: list[np.ndarray] = []
    peak_left_coordinates: list[np.ndarray] = []
    peak_right_coordinates: list[np.ndarray] = []

    for result in results:
        profile = np.asarray(result["intensities"], dtype=np.float64)
        peak, mu, sigma, offset, fit_ok = _fit_gaussian_profile(profile)

        offsets = np.asarray(
            result.get("offsets", np.arange(len(profile), dtype=np.float64)),
            dtype=np.float64,
        )
        if len(offsets) == len(profile):
            mu_offset = float(
                np.interp(mu, np.arange(len(profile), dtype=np.float64), offsets)
            )
        else:
            mu_offset = float(mu - (len(profile) - 1) / 2.0)

        normal = np.asarray(result.get("normal", np.zeros(2, dtype=np.float64)))
        normal_norm = float(np.linalg.norm(normal))
        if normal_norm > 0:
            normal = normal / normal_norm
        else:
            normal = np.zeros(2, dtype=np.float64)

        center = np.asarray(result["coordinates"], dtype=np.float64)
        peak_coord = center + normal * mu_offset
        peak_left_coord = peak_coord - normal * sigma
        peak_right_coord = peak_coord + normal * sigma

        peak_profile.append(float(peak))
        gauss_offset.append(float(offset))
        gauss_sigma.append(float(sigma))
        gauss_mu_offset.append(float(mu_offset))
        gauss_fit_ok.append(1.0 if fit_ok else 0.0)
        peak_coordinates.append(peak_coord)
        peak_left_coordinates.append(peak_left_coord)
        peak_right_coordinates.append(peak_right_coord)

    intensities = np.asarray(peak_profile, dtype=np.float64)
    intensities = _smooth_skeleton_intensities(intensities, intensity_savgol_window)

    measurement = _compose_skeleton_measurement(
        intensities,
        np.asarray(peak_coordinates, dtype=np.float64),
        length,
    )
    measurement["_peak_coordinates"] = np.asarray(peak_coordinates, dtype=np.float64)
    measurement["_peak_left_coordinates"] = np.asarray(
        peak_left_coordinates, dtype=np.float64
    )
    measurement["_peak_right_coordinates"] = np.asarray(
        peak_right_coordinates, dtype=np.float64
    )
    measurement["_gauss_peak"] = np.asarray(peak_profile, dtype=np.float64)
    measurement["_gauss_offset"] = np.asarray(gauss_offset, dtype=np.float64)
    measurement["_gauss_sigma"] = np.asarray(gauss_sigma, dtype=np.float64)
    measurement["_gauss_mu_offset"] = np.asarray(gauss_mu_offset, dtype=np.float64)
    measurement["_gauss_fit_ok"] = np.asarray(gauss_fit_ok, dtype=np.float64)

    return measurement


def _select_largest_root_mask(mask: MaskImage) -> MaskImage:
    labeled_lines, num_lines = measure.label(mask, return_num=True)

    if num_lines > 1:
        io.logger.warning(f"{num_lines} roots found, expected 1")
        _, largest = sorted(
            (rp["area"], rp["label"]) for rp in regionprops(labeled_lines)
        )[-1]
        label = largest
    else:
        label = 1

    root_mask = labeled_lines == label
    assert _types.is_mask_image(root_mask), "Not a mask image"
    return root_mask


def _measure_image(
    im: IntensityImage,
    mask: MaskImage,
    measurement_config: MeasurementConfig,
    skeleton: MaskImage | None = None,
) -> dict[str, Any] | None:
    """Measure features of the largest root in an image.

    Parameters
    ----------
    im : IntensityImage
        Image data.
    mask : MaskImage
        Binary mask of the roots.

    Returns
    -------
    dict[str, Any] or None
        Dictionary with root measurements, or None if no valid root was found.
    """
    root_mask = _select_largest_root_mask(mask)

    if measurement_config.method == "box":
        return {
            **prefix_keys(
                "tip_",
                measure_roi_at_tip_simple(
                    im,
                    root_mask,
                    measurement_config.box_size,
                    measurement_config.box_offset,
                ),
            ),
            **prefix_keys("full_", measure_roi(im, root_mask)),
        }

    if skeleton is None:
        raise ValueError("skeleton is required for centerline measurements")

    if measurement_config.method == "centerline_gaussian":
        skeleton_measurement = measure_skeleton_gaussian(
            im,
            np.logical_and(skeleton, root_mask),
            measurement_config.perpendicular_width,
            measurement_config.length,
            measurement_config.intensity_savgol_window,
        )
    else:
        skeleton_measurement = measure_skeleton(
            im,
            np.logical_and(skeleton, root_mask),
            measurement_config.perpendicular_width,
            measurement_config.length,
            measurement_config.intensity_savgol_window,
        )

    return {
        **prefix_keys(
            "skel_",
            skeleton_measurement,
        ),
    }


def measure_image(
    path: pathlib.Path,
    measurement_config: MeasurementConfig = MeasurementConfig(),
    reuse_artifacts: bool = True,
    artifact_stats: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    source_signature = io.build_source_signature(path)
    manifest = io.read_artifact_manifest(path) if reuse_artifacts else {}
    im = io.read(path)

    mask_path = io.build_mask_path(path)
    raw_mask_entry = manifest.get("mask")
    mask_entry = raw_mask_entry if isinstance(raw_mask_entry, dict) else None

    reuse_mask = (
        reuse_artifacts
        and mask_path.exists()
        and _mask_manifest_matches(mask_entry, source_signature)
    )

    if reuse_mask:
        try:
            mask = skio.imread(mask_path) > 0  # type: ignore
            assert _types.is_mask_image(mask), "Not a mask image"
            io.logger.info(f"Reusing mask artifact: {mask_path}")
        except Exception:
            io.logger.warning(
                f"Could not reuse mask artifact {mask_path}, recomputing."
            )
            reuse_mask = False

    if not reuse_mask:
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        mask = _compute_mask(im)
        skio.imsave(mask_path, mask.astype(np.uint8) * 255, check_contrast=False)
        io.logger.info(f"Recomputed mask artifact: {mask_path}")
        manifest["mask"] = {
            "version": MASK_ARTIFACT_VERSION,
            "source": source_signature,
        }
        io.write_artifact_manifest(path, manifest)

    if artifact_stats is not None:
        key = "masks_reused" if reuse_mask else "masks_recomputed"
        artifact_stats[key] = artifact_stats.get(key, 0) + 1

    if measurement_config.method == "box":
        return _measure_image(im, mask, measurement_config, None)

    from .immultiroot import find_center_line

    skeleton_path = io.build_skeleton_path(path)
    raw_skeleton_entry = manifest.get("skeleton")
    skeleton_entry = (
        raw_skeleton_entry if isinstance(raw_skeleton_entry, dict) else None
    )

    reuse_skeleton = (
        reuse_artifacts
        and skeleton_path.exists()
        and _skeleton_manifest_matches(
            skeleton_entry, source_signature, measurement_config.savgol_window
        )
    )

    if reuse_skeleton:
        try:
            skeleton = skio.imread(skeleton_path) > 0  # type: ignore
            assert _types.is_mask_image(skeleton), "Not a mask image"
            io.logger.info(f"Reusing skeleton artifact: {skeleton_path}")
        except Exception:
            io.logger.warning(
                f"Could not reuse skeleton artifact {skeleton_path}, recomputing."
            )
            reuse_skeleton = False

    if not reuse_skeleton:
        skeleton_path.parent.mkdir(parents=True, exist_ok=True)
        skeleton_coords = find_center_line(
            mask,
            savgol_window=measurement_config.savgol_window,
        )
        skeleton = np.zeros_like(mask)
        skeleton[
            skeleton_coords[:, 0].astype(int), skeleton_coords[:, 1].astype(int)
        ] = True
        skio.imsave(
            skeleton_path, skeleton.astype(np.uint8) * 255, check_contrast=False
        )
        io.logger.info(f"Recomputed skeleton artifact: {skeleton_path}")
        manifest["skeleton"] = {
            "version": SKELETON_ARTIFACT_VERSION,
            "savgol_window": measurement_config.savgol_window,
            "source": source_signature,
        }
        io.write_artifact_manifest(path, manifest)

    if artifact_stats is not None:
        key = "skeletons_reused" if reuse_skeleton else "skeletons_recomputed"
        artifact_stats[key] = artifact_stats.get(key, 0) + 1

    return _measure_image(im, mask, measurement_config, skeleton)
