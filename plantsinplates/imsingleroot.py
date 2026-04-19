from typing import Any, Callable
import pathlib

import numpy as np
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


def measure_skeleton(
    im: IntensityImage, skeleton: MaskImage, perpendicular_width: int, length: int
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

    coordinates = np.asarray(
        [result["coordinates"] for result in results], dtype=np.int64
    )

    assert coordinates.ndim == 2, f"Expected 2d, found {coordinates.ndim}"
    assert coordinates.shape[1] == 2, (
        f"Expected shape [_, 2], found {coordinates.shape}"
    )

    intensities = np.asarray([np.sum(result["intensities"]) for result in results])

    last_argmax = len(intensities) - np.argmax(intensities[::-1]) - 1

    distance_to_max = np.linalg.norm(coordinates - coordinates[last_argmax, :], axis=1)

    sel = np.abs(distance_to_max <= length)

    return {
        "intensities": intensities,
        "intensity": float(np.sum(intensities[sel])),
        "pixel_count": np.sum(sel),
        "_coordinates": coordinates,  # [sel, :],
    }


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

    return {
        **prefix_keys(
            "skel_",
            measure_skeleton(
                im,
                np.logical_and(skeleton, root_mask),
                measurement_config.perpendicular_width,
                measurement_config.length,
            ),
        ),
    }


def measure_image(
    path: pathlib.Path,
    measurement_config: MeasurementConfig = MeasurementConfig(),
    reuse_artifacts: bool = True,
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

    return _measure_image(im, mask, measurement_config, skeleton)
