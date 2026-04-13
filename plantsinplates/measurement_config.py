from dataclasses import asdict, dataclass
from typing import Literal

type MeasurementMethod = Literal["box", "centerline"]


@dataclass(frozen=True)
class MeasurementConfig:
    method: MeasurementMethod = "box"
    box_size: int = 680
    perpendicular_width: int = 3
    length: int = 10
    savgol_window: int = 100

    def __post_init__(self):
        if self.method not in ("box", "centerline"):
            raise ValueError(f"Invalid method: {self.method}")

        if self.box_size <= 0:
            raise ValueError("box_size must be > 0")

        if self.perpendicular_width <= 0:
            raise ValueError("perpendicular_width must be > 0")

        if self.length <= 0:
            raise ValueError("length must be > 0")

        if self.savgol_window <= 0:
            raise ValueError("savgol_window must be > 0")

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)
