from typing import Any, Literal, NotRequired, TypedDict, TypeGuard

import numpy as np


type MaskImage = np.ndarray[tuple[int, int], np.dtype[np.bool]]
type LabeledImage = np.ndarray[tuple[int, int], np.dtype[np.integer]]
type IntensityImage = np.ndarray[tuple[int, int], np.dtype[np.integer]]
type IntCoodArray = np.ndarray[tuple[int, Literal[2]], np.dtype[np.integer]]
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
    bg_count: int
    position: tuple[Float, Float] | tuple[int, int] | None
    box: tuple[int, int] | None


class PerpendicularProfile(TypedDict):
    coordinates: tuple[int, int]
    intensities: FloatVector
    normal: NotRequired[FloatVector]
    offsets: NotRequired[FloatVector]


class SkeletonMeasurement(TypedDict):
    intensities: FloatVector
    intensity: float
    pixel_count: int
    _coordinates: IntCoodArray
    _peak_coordinates: NotRequired[FloatCoodArray]
    _peak_left_coordinates: NotRequired[FloatCoodArray]
    _peak_right_coordinates: NotRequired[FloatCoodArray]
    _gauss_peak: NotRequired[FloatVector]
    _gauss_offset: NotRequired[FloatVector]
    _gauss_sigma: NotRequired[FloatVector]
    _gauss_mu_offset: NotRequired[FloatVector]
    _gauss_fit_ok: NotRequired[FloatVector]
    cap_present: bool
    cap_start_idx: int
    cap_end_idx: int
    cap_mean: float
    cap_integrated: float
    cap_count: int
    tip_start_idx: int
    tip_end_idx: int
    tip_mean: float
    tip_integrated: float
    tip_count: int
    middle_start_idx: int
    middle_end_idx: int
    middle_mean: float
    middle_integrated: float
    middle_count: int
    far_start_idx: int
    far_end_idx: int
    far_mean: float
    far_integrated: float
    far_count: int
