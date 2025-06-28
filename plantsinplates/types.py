from typing import Any, Literal, TypedDict, TypeGuard

import numpy as np


type MaskImage = np.ndarray[tuple[int, int], np.dtype[np.bool]]
type LabeledImage = np.ndarray[tuple[int, int], np.dtype[np.integer]]
type IntensityImage = np.ndarray[tuple[int, int], np.dtype[np.integer]]
type FloatCoodArray = np.ndarray[tuple[int, Literal[2]], np.dtype[np.floating]]
type FloatVector = np.ndarray[tuple[int, ...], np.dtype[np.floating]]

type Float = float | np.floating[Any]


def _check(obj: Any, dtype: np.typing.DTypeLike) -> bool:
    if not isinstance(obj, np.ndarray):
        return False
    if obj.ndim != 2:
        return False
    if not np.issubdtype(obj.dtype, dtype):  # type: ignore
        return False
    return True


def is_mask_image(obj: Any) -> TypeGuard[MaskImage]:
    return _check(obj, np.bool_)


def is_labeled_image(obj: Any) -> TypeGuard[LabeledImage]:
    return _check(obj, np.unsignedinteger)


def is_intensity_image(obj: Any) -> TypeGuard[IntensityImage]:
    return _check(obj, np.unsignedinteger)


class LineMeasurement(TypedDict):
    centerline: FloatCoodArray
    intensity: FloatVector
    amplitude: FloatVector
    sigma: FloatVector
    bg: FloatVector


class RoiMeasurement(TypedDict):
    fg_mean: Float
    fg_std: Float
    fg_count: int
    bg_mean: Float
    bg_std: Float
    fg_count: int
    position: tuple[Float, Float] | tuple[int, int] | None
    box: tuple[int, int] | None
