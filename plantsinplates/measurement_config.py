from dataclasses import asdict, dataclass
from math import isfinite
from typing import Literal

type MeasurementMethod = Literal["box", "centerline"]


@dataclass(frozen=True)
class MeasurementConfig:
    method: MeasurementMethod = "box"
    box_size: int = 680
    box_offset: float = 0.0
    perpendicular_width: int = 50
    length: int = 50
    savgol_window: int = 25
    intensity_savgol_window: int = 25

    def __post_init__(self):
        if self.method not in ("box", "centerline"):
            raise ValueError(f"Invalid method: {self.method}")

        if self.box_size <= 0:
            raise ValueError("box_size must be > 0")

        if not isfinite(self.box_offset):
            raise ValueError("box_offset must be finite")

        if self.perpendicular_width <= 0:
            raise ValueError("perpendicular_width must be > 0")

        if self.length <= 0:
            raise ValueError("length must be > 0")

        if self.savgol_window <= 0:
            raise ValueError("savgol_window must be > 0")

        if self.intensity_savgol_window < 0:
            raise ValueError("intensity_savgol_window must be >= 0")

    def to_dict(self) -> dict[str, int | float | str]:
        return asdict(self)
