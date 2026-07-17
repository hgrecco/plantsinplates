"""GUI-independent workflow models and filesystem discovery helpers.

This module deliberately contains no Tkinter code.  Folder validation,
calibration inspection, run discovery, and manifest handling are shared by the
desktop application, the batch command, and the workflow-level tests.
"""

from __future__ import annotations

import json
import math
import pathlib
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import bioio_czi
import pandas as pd

from .measurement_config import MeasurementConfig


RUN_SCHEMA_VERSION = 1
CACHE_DIRECTORY = ".plantsinplates_cache"
RUN_PREFIX = "_output_"
IMAGE_SUFFIXES = {".czi", ".jpg", ".jpeg", ".tif", ".tiff", ".png"}

type FolderKind = Literal["experiment", "plate"]
type ValidationState = Literal["empty", "valid", "invalid", "inconsistent"]
type CalibrationMode = Literal["metadata", "cal_file", "manual", "pixels"]
type ReusePolicy = Literal["compatible", "preprocessing", "none"]
type RunStatus = Literal[
    "running", "completed", "completed_with_warnings", "failed", "cancelled"
]


@dataclass(frozen=True)
class FolderValidation:
    valid: bool
    kind: FolderKind | None
    state: ValidationState
    message: str
    details: tuple[str, ...] = ()
    referenced_images: tuple[pathlib.Path, ...] = ()
    unreferenced_images: tuple[pathlib.Path, ...] = ()

    @property
    def image_count(self) -> int:
        return len(self.referenced_images)


@dataclass(frozen=True)
class CalibrationReading:
    path: pathlib.Path
    status: Literal[
        "ok", "missing", "unreadable", "unsupported", "invalid", "non_square"
    ]
    x_um_per_pixel: float | None = None
    y_um_per_pixel: float | None = None
    um_per_pixel: float | None = None
    message: str = ""

    @property
    def valid(self) -> bool:
        return self.status == "ok" and self.um_per_pixel is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "status": self.status,
            "x_um_per_pixel": self.x_um_per_pixel,
            "y_um_per_pixel": self.y_um_per_pixel,
            "um_per_pixel": self.um_per_pixel,
            "message": self.message,
        }


@dataclass(frozen=True)
class CalibrationSpec:
    mode: CalibrationMode
    shared_um_per_pixel: float | None = None
    source_path: pathlib.Path | None = None

    def __post_init__(self) -> None:
        if self.mode in ("cal_file", "manual"):
            value = self.shared_um_per_pixel
            if value is None or not math.isfinite(value) or value <= 0:
                raise ValueError("Shared calibration must be greater than 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "shared_um_per_pixel": self.shared_um_per_pixel,
            "source_path": str(self.source_path) if self.source_path else None,
        }


@dataclass(frozen=True)
class ProgressEvent:
    stage: Literal[
        "validation", "calibration", "measurement", "aggregation", "reporting"
    ]
    completed: int = 0
    total: int = 0
    message: str = ""
    current_path: pathlib.Path | None = None
    plate: str | None = None
    reused: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(frozen=True)
class AnalysisRequest:
    input_path: pathlib.Path
    input_kind: FolderKind
    settings: MeasurementConfig
    settings_unit: Literal["um", "px"]
    calibration: CalibrationSpec
    reuse_policy: ReusePolicy = "compatible"
    audit_results: dict[str, CalibrationReading] = field(
        default_factory=dict, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.input_kind not in ("experiment", "plate"):
            raise ValueError(f"Unsupported input kind: {self.input_kind}")
        if self.settings_unit not in ("um", "px"):
            raise ValueError(f"Unsupported settings unit: {self.settings_unit}")
        if self.reuse_policy not in ("compatible", "preprocessing", "none"):
            raise ValueError(f"Unsupported reuse policy: {self.reuse_policy}")
        if self.settings_unit == "um" and self.calibration.mode == "pixels":
            raise ValueError("Micrometer settings require a physical calibration")

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "input_path": str(self.input_path.resolve()),
            "input_kind": self.input_kind,
            "settings": self.settings.to_dict(),
            "settings_unit": self.settings_unit,
            "calibration": self.calibration.to_dict(),
            "reuse_policy": self.reuse_policy,
        }


@dataclass(frozen=True)
class RunResult:
    run_id: str
    run_dir: pathlib.Path
    status: RunStatus
    outputs: dict[str, pathlib.Path]
    total: int
    completed: int
    reused: int
    recomputed: int
    skipped: int
    errors: int
    message: str

    @property
    def successful(self) -> bool:
        return self.status in ("completed", "completed_with_warnings")


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    run_dir: pathlib.Path
    started_at: str
    status: str
    method: str
    calibration_mode: str
    reuse_policy: str
    settings_unit: str
    settings: dict[str, Any]
    outputs: dict[str, str]
    manifest: dict[str, Any] = field(repr=False)


class AnalysisCancelled(Exception):
    """Raised internally when cancellation is observed between images."""


ProgressCallback = Callable[[ProgressEvent], None]


def folder_kind(path: pathlib.Path) -> FolderKind | None:
    if path.stem.startswith("experiment"):
        return "experiment"
    if path.stem.startswith("plate"):
        return "plate"
    return None


def normalize_path_text(
    value: str, *, base: pathlib.Path | None = None
) -> pathlib.Path:
    """Normalize a pasted path without expanding environment variables."""
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1]
    path = pathlib.Path(cleaned).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path.resolve(strict=False)


def _date_key(path: pathlib.Path) -> str | None:
    if not path.name.startswith("date_"):
        return None
    rest = path.name.removeprefix("date_")
    return rest.split("_", 1)[0] or None


def _read_plate_contract(
    folder: pathlib.Path,
) -> tuple[
    pd.DataFrame | None,
    list[str],
    dict[str, pathlib.Path],
    list[str],
]:
    problems: list[str] = []
    info = folder / "info.xlsx"
    if not info.is_file():
        return None, ["missing info.xlsx"], {}, []
    try:
        info_df = pd.read_excel(info)
    except Exception as ex:
        return None, [f"could not read info.xlsx: {ex}"], {}, []

    missing_columns = sorted({"row", "col", "genotype"} - set(info_df.columns))
    if missing_columns:
        problems.append("missing columns: " + ", ".join(missing_columns))

    length_dates = {
        str(column).removesuffix("_length")
        for column in info_df.columns
        if str(column).endswith("_length")
    }
    fluo_dates = {
        str(column).removesuffix("_fluo")
        for column in info_df.columns
        if str(column).endswith("_fluo")
    }
    if not length_dates or not fluo_dates:
        problems.append("no paired _length/_fluo date columns were found")
    missing_fluo = sorted(length_dates - fluo_dates)
    missing_length = sorted(fluo_dates - length_dates)
    if missing_fluo:
        problems.append("missing _fluo columns for dates: " + ", ".join(missing_fluo))
    if missing_length:
        problems.append(
            "missing _length columns for dates: " + ", ".join(missing_length)
        )

    date_dirs_by_key: dict[str, list[pathlib.Path]] = {}
    for date_dir in folder.rglob("date_*"):
        if not date_dir.is_dir():
            continue
        relative_parts = date_dir.relative_to(folder).parts
        if CACHE_DIRECTORY in relative_parts or any(
            part.startswith(RUN_PREFIX) for part in relative_parts
        ):
            continue
        key = _date_key(date_dir)
        if key:
            date_dirs_by_key.setdefault(key, []).append(date_dir)
    date_dirs: dict[str, pathlib.Path] = {}
    for key, paths in date_dirs_by_key.items():
        if len(paths) > 1:
            problems.append(
                f"multiple date folders match {key}: "
                + ", ".join(str(path.relative_to(folder)) for path in paths)
            )
        else:
            date_dirs[key] = paths[0]

    expected_dates = length_dates | fluo_dates
    missing_dirs = sorted(expected_dates - set(date_dirs))
    unexpected_dirs = sorted(set(date_dirs) - expected_dates)
    if missing_dirs:
        problems.append("missing date folders: " + ", ".join(missing_dirs))
    if unexpected_dirs:
        problems.append("unexpected date folders: " + ", ".join(unexpected_dirs))
    return info_df, problems, date_dirs, sorted(expected_dates)


def validate_plate_folder(folder: pathlib.Path) -> FolderValidation:
    """Validate structure and referenced images without changing the input."""
    info_df, problems, date_dirs, expected_dates = _read_plate_contract(folder)
    if info_df is None:
        return FolderValidation(
            False,
            "plate",
            "inconsistent",
            "Missing or unreadable plate input.",
            tuple(problems),
        )

    referenced: set[pathlib.Path] = set()
    missing_references: list[str] = []
    for date in expected_dates:
        column = f"{date}_fluo"
        date_dir = date_dirs.get(date)
        if column not in info_df.columns or date_dir is None:
            continue
        for raw in info_df[column].dropna().tolist():
            value = str(raw).strip()
            if not value:
                continue
            relative = pathlib.PurePosixPath(value)
            image_path = date_dir.joinpath(*relative.parts)
            if image_path.is_file():
                referenced.add(image_path.resolve())
            else:
                missing_references.append(f"{date}: {value}")
    if missing_references:
        preview = missing_references[:8]
        suffix = (
            ""
            if len(missing_references) <= 8
            else f" (+{len(missing_references) - 8} more)"
        )
        problems.append("missing referenced images: " + ", ".join(preview) + suffix)

    found_images: set[pathlib.Path] = set()
    for date_dir in date_dirs.values():
        for path in date_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            relative_parts = path.relative_to(date_dir).parts
            if (
                path.stem.lower().startswith("overview")
                or CACHE_DIRECTORY in relative_parts
                or any(part.startswith(RUN_PREFIX) for part in relative_parts)
            ):
                continue
            found_images.add(path.resolve())
    unreferenced = sorted(found_images - referenced, key=str)

    fatal = bool(problems)
    details = list(problems)
    if unreferenced:
        preview = [str(path.relative_to(folder.resolve())) for path in unreferenced[:8]]
        suffix = "" if len(unreferenced) <= 8 else f" (+{len(unreferenced) - 8} more)"
        details.append("unreferenced images: " + ", ".join(preview) + suffix)
    if fatal:
        return FolderValidation(
            False,
            "plate",
            "inconsistent",
            "info.xlsx, date folders, or referenced images are inconsistent.",
            tuple(details),
            tuple(sorted(referenced, key=str)),
            tuple(unreferenced),
        )
    message = f"Valid plate — {len(referenced)} referenced image{'s' if len(referenced) != 1 else ''} ready."
    if unreferenced:
        message += f" {len(unreferenced)} unreferenced image{'s' if len(unreferenced) != 1 else ''} will be ignored."
    return FolderValidation(
        True,
        "plate",
        "valid",
        message,
        tuple(details),
        tuple(sorted(referenced, key=str)),
        tuple(unreferenced),
    )


def validate_folder(folder: pathlib.Path | None) -> FolderValidation:
    if folder is None:
        return FolderValidation(
            False,
            None,
            "empty",
            "Choose or paste an experiment_* or plate_* folder to begin.",
        )
    if not folder.is_dir():
        return FolderValidation(False, None, "invalid", "Folder does not exist.")
    kind = folder_kind(folder)
    if kind is None:
        return FolderValidation(
            False,
            None,
            "invalid",
            "Folder name must begin with experiment_ or plate_.",
        )
    if kind == "plate":
        return validate_plate_folder(folder)

    plates = sorted(path for path in folder.glob("plate_*") if path.is_dir())
    if not plates:
        return FolderValidation(
            False,
            "experiment",
            "inconsistent",
            "No plate_* folders were found.",
        )
    results = [(plate, validate_plate_folder(plate)) for plate in plates]
    invalid = [(plate, result) for plate, result in results if not result.valid]
    referenced = tuple(
        sorted(
            (path for _plate, result in results for path in result.referenced_images),
            key=str,
        )
    )
    unreferenced = tuple(
        sorted(
            (path for _plate, result in results for path in result.unreferenced_images),
            key=str,
        )
    )
    if invalid:
        details = tuple(
            f"{plate.name}: {result.message}"
            + (f" ({'; '.join(result.details)})" if result.details else "")
            for plate, result in invalid
        )
        return FolderValidation(
            False,
            "experiment",
            "inconsistent",
            f"{len(invalid)} of {len(plates)} plates have invalid inputs.",
            details,
            referenced,
            unreferenced,
        )
    details = tuple(
        f"{plate.name}: {detail}"
        for plate, result in results
        for detail in result.details
    )
    message = (
        f"Valid experiment — {len(plates)} plate{'s' if len(plates) != 1 else ''}, "
        f"{len(referenced)} referenced image{'s' if len(referenced) != 1 else ''} ready."
    )
    return FolderValidation(
        True,
        "experiment",
        "valid",
        message,
        details,
        referenced,
        unreferenced,
    )


def parse_calibration_file(path: pathlib.Path) -> float:
    try:
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError as ex:
        raise ValueError(str(ex)) from ex
    if len(lines) != 1:
        raise ValueError("cal.txt must contain exactly one non-empty line")
    try:
        value = float(lines[0])
    except ValueError as ex:
        raise ValueError("cal.txt must contain a number") from ex
    if not math.isfinite(value) or value <= 0:
        raise ValueError("cal.txt must be a finite number greater than 0")
    return value


def find_calibration_file(folder: pathlib.Path) -> pathlib.Path | None:
    candidates = [folder / "cal.txt"]
    if folder_kind(folder) == "plate" and folder_kind(folder.parent) == "experiment":
        candidates.append(folder.parent / "cal.txt")
    return next((path for path in candidates if path.is_file()), None)


def read_image_calibration(path: pathlib.Path) -> CalibrationReading:
    if not path.is_file():
        return CalibrationReading(path, "missing", message="Image file is missing")
    if path.suffix.lower() != ".czi":
        return CalibrationReading(
            path,
            "unsupported",
            message=f"Embedded calibration is not supported for {path.suffix or 'this format'}",
        )
    try:
        reader = bioio_czi.Reader(path)
        sizes = reader.physical_pixel_sizes
        x = float(sizes.X) if sizes.X is not None else None
        y = float(sizes.Y) if sizes.Y is not None else None
    except Exception as ex:
        return CalibrationReading(path, "unreadable", message=str(ex))
    if x is None or y is None:
        return CalibrationReading(
            path,
            "invalid",
            x_um_per_pixel=x,
            y_um_per_pixel=y,
            message="X or Y physical pixel size is missing",
        )
    if not math.isfinite(x) or not math.isfinite(y) or x <= 0 or y <= 0:
        return CalibrationReading(
            path,
            "invalid",
            x_um_per_pixel=x,
            y_um_per_pixel=y,
            message="Physical pixel sizes must be finite and greater than 0",
        )
    if not math.isclose(x, y, rel_tol=0.01, abs_tol=0.0):
        return CalibrationReading(
            path,
            "non_square",
            x_um_per_pixel=x,
            y_um_per_pixel=y,
            message="X and Y pixel sizes differ by more than 1%",
        )
    return CalibrationReading(
        path,
        "ok",
        x_um_per_pixel=x,
        y_um_per_pixel=y,
        um_per_pixel=(x + y) / 2,
        message="Readable square-pixel calibration",
    )


def audit_calibrations(
    paths: tuple[pathlib.Path, ...] | list[pathlib.Path],
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> list[CalibrationReading]:
    readings: list[CalibrationReading] = []
    total = len(paths)
    for index, path in enumerate(paths, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise AnalysisCancelled()
        reading = read_image_calibration(path)
        readings.append(reading)
        if progress:
            progress(
                ProgressEvent(
                    "calibration",
                    index,
                    total,
                    f"Checking calibration {index}/{total}: {path.name}",
                    current_path=path,
                )
            )
    return readings


def summarize_calibrations(readings: list[CalibrationReading]) -> str:
    counts: dict[str, int] = {}
    values: set[float] = set()
    for reading in readings:
        counts[reading.status] = counts.get(reading.status, 0) + 1
        if reading.um_per_pixel is not None:
            values.add(round(reading.um_per_pixel, 9))
    valid = counts.get("ok", 0)
    invalid = len(readings) - valid
    value_text = f"{len(values)} distinct value{'s' if len(values) != 1 else ''}"
    return (
        f"Calibration audit: {valid}/{len(readings)} readable; "
        f"{invalid} unusable; {value_text}."
    )


def generate_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def create_run_directory(input_path: pathlib.Path) -> tuple[str, pathlib.Path]:
    for _attempt in range(10):
        run_id = generate_run_id()
        run_dir = input_path / f"{RUN_PREFIX}{run_id}"
        try:
            run_dir.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            continue
        return run_id, run_dir
    raise RuntimeError("Could not allocate a unique output directory")


def atomic_write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover_runs(folder: pathlib.Path | None) -> list[RunRecord]:
    if folder is None or not folder.is_dir():
        return []
    records: list[RunRecord] = []
    for run_dir in folder.glob(f"{RUN_PREFIX}*"):
        if not run_dir.is_dir():
            continue
        manifest_path = run_dir / "run.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            request = manifest.get("request", {})
            settings = request.get("settings", {})
            calibration = request.get("calibration", {})
            records.append(
                RunRecord(
                    run_id=str(
                        manifest.get("run_id", run_dir.name.removeprefix(RUN_PREFIX))
                    ),
                    run_dir=run_dir,
                    started_at=str(manifest.get("started_at", "")),
                    status=str(manifest.get("status", "unknown")),
                    method=str(settings.get("method", "unknown")),
                    calibration_mode=str(calibration.get("mode", "unknown")),
                    reuse_policy=str(request.get("reuse_policy", "unknown")),
                    settings_unit=str(request.get("settings_unit", "unknown")),
                    settings=settings if isinstance(settings, dict) else {},
                    outputs=manifest.get("outputs", {})
                    if isinstance(manifest.get("outputs"), dict)
                    else {},
                    manifest=manifest,
                )
            )
        except Exception:
            continue
    return sorted(records, key=lambda record: record.started_at, reverse=True)


def delete_run(record: RunRecord) -> None:
    if not (record.run_dir / "run.json").is_file():
        raise ValueError("Selected directory is not a managed analysis run")
    shutil.rmtree(record.run_dir)


def cache_directories(folder: pathlib.Path) -> list[pathlib.Path]:
    kind = folder_kind(folder)
    if kind == "plate":
        candidates = [folder / CACHE_DIRECTORY]
    elif kind == "experiment":
        candidates = [
            plate / CACHE_DIRECTORY
            for plate in folder.glob("plate_*")
            if plate.is_dir()
        ]
    else:
        candidates = []
    return [candidate for candidate in candidates if candidate.exists()]


def clear_reusable_cache(folder: pathlib.Path) -> int:
    caches = cache_directories(folder)
    for cache in caches:
        shutil.rmtree(cache)
    return len(caches)


def run_record_summary(record: RunRecord) -> str:
    calibration = record.manifest.get("request", {}).get("calibration", {})
    value = calibration.get("shared_um_per_pixel")
    value_text = f" ({value:g} µm/pixel)" if isinstance(value, (int, float)) else ""
    settings = ", ".join(
        f"{key}={value}" for key, value in record.settings.items() if key != "method"
    )
    return (
        f"Run {record.run_id}\n"
        f"Status: {record.status}\n"
        f"Method: {record.method}\n"
        f"Calibration: {record.calibration_mode}{value_text}\n"
        f"Reuse policy: {record.reuse_policy}\n"
        f"Settings ({record.settings_unit}): {settings or 'defaults'}"
    )
