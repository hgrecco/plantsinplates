from typing import Any, Callable
import pathlib

import numpy as np
from skimage import morphology, measure, io as skio
from skimage.measure import regionprops


from . import io
from . import types as _types
from . import skeleton_utils

from .types import (
    MaskImage,
    LabeledImage,
    IntensityImage,
    FloatVector,
    RoiMeasurement,
    SkeletonMeasurement,
)


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


def measure_roi_at_tip_simple(
    im: IntensityImage, mask: MaskImage, box: int | tuple[int, int]
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

    s0 = slice_around(tip0, box[0])
    s1 = slice_around(tip1, box[1])

    return {
        **measure_roi(im[s0, s1], mask[s0, s1]),
        "position": (tip0, tip1),
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


def _measure_image(
    im: IntensityImage, mask: MaskImage, skeleton: MaskImage
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
    labeled_lines, num_lines = measure.label(mask, return_num=True)

    if num_lines > 1:
        io.logger.warn(f"{num_lines} roots found, expected 1")
        _, largest = sorted(
            (rp["area"], rp["label"]) for rp in regionprops(labeled_lines)
        )[-1]
        label = largest
    else:
        label = 1

    root_mask = labeled_lines == label

    assert _types.is_mask_image(root_mask), "Not a mask image"

    conf: dict[str, int] = dict(
        box=680,
        perpendicular_width=3,
        length=10,
    )

    if max(im.shape) > 1000:
        # Probably a 8x
        pass
    else:
        # Probably a 1x
        conf = {k: int(v / 8) for k, v in conf.items()}

    return {
        **prefix_keys("tip_", measure_roi_at_tip_simple(im, root_mask, conf["box"])),
        **prefix_keys("full_", measure_roi(im, root_mask)),
        **prefix_keys(
            "skel_",
            measure_skeleton(
                im,
                np.logical_and(skeleton, root_mask),
                conf["perpendicular_width"],
                conf["length"],
            ),
        ),
    }


def measure_image(path: pathlib.Path) -> dict[str, Any] | None:
    im = io.read(path)
    mask_path = io.build_mask_path(path)
    if mask_path.exists():
        mask = skio.imread(mask_path) > 0  # type: ignore
        assert _types.is_mask_image(mask), "Not a mask image"
    else:
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        mask = im > 130
        mask = morphology.remove_small_objects(mask)
        mask = morphology.remove_small_holes(mask)
        assert _types.is_mask_image(mask), "Not a mask image"
        skio.imsave(mask_path, mask.astype(np.uint8) * 255, check_contrast=False)

    skeleton_path = io.build_skeleton_path(path)
    if skeleton_path.exists():
        skeleton = skio.imread(skeleton_path) > 0  # type: ignore
        assert _types.is_mask_image(skeleton), "Not a mask image"
    else:
        from .immultiroot import find_center_line

        skeleton_path.parent.mkdir(parents=True, exist_ok=True)
        skeleton_coords = find_center_line(mask)
        skeleton = np.zeros_like(mask)
        skeleton[
            skeleton_coords[:, 0].astype(int), skeleton_coords[:, 1].astype(int)
        ] = True
        skio.imsave(
            skeleton_path, skeleton.astype(np.uint8) * 255, check_contrast=False
        )

    return _measure_image(im, mask, skeleton)
