import json
import hashlib
import logging
import pathlib
from typing import Any

import bioio_czi
from skimage import io as skio

from .types import IntensityImage, is_intensity_image
from .workflow import CACHE_DIRECTORY

__version__ = "2026.07.17"

PREFIX = "_output_"

OVERVIEW_SUFFIXES = [".jpg", ".jpeg", ".tiff", ".tif", ".png"]
FLUO_SUFFIXES = [".jpg", ".jpeg", ".tiff", ".tif", ".png", ".czi"]

logger = logging.getLogger("plantsinplates")


def build_mask_path(image_path: pathlib.Path) -> pathlib.Path:
    """Build standardized mask path from the provided image path.

    Parameters
    ----------
    image_path : pathlib.Path
        Path to the original image file.

    Returns
    -------
    pathlib.Path
        Path where the corresponding mask file should be stored.
    """
    cache_root, relative = _image_cache_location(image_path)
    return cache_root / "images" / relative.parent / (relative.stem + ".mask.png")


def build_skeleton_path(image_path: pathlib.Path) -> pathlib.Path:
    """Build standardized skeleton path from the provided image path.

    Parameters
    ----------
    image_path : pathlib.Path
        Path to the original image file.

    Returns
    -------
    pathlib.Path
        Path where the corresponding skeleton file should be stored.
    """
    cache_root, relative = _image_cache_location(image_path)
    return cache_root / "images" / relative.parent / (relative.stem + ".skeleton.png")


def build_artifact_manifest_path(image_path: pathlib.Path) -> pathlib.Path:
    """Build standardized artifact manifest path for an image.

    If the image belongs to a plate folder, this returns a single plate-level
    manifest path (`<plate>/_output_manifest.json`) shared by all image artifacts.
    Otherwise, it falls back to a per-image manifest file.
    """
    plate_dir = find_parent_plate_dir(image_path)
    if plate_dir is not None:
        return plate_dir / CACHE_DIRECTORY / "artifact_manifest.json"

    return image_path.parent / CACHE_DIRECTORY / "artifact_manifest.json"


def _image_cache_location(
    image_path: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Return a cache root and stable image-relative path."""
    plate_dir = find_parent_plate_dir(image_path)
    if plate_dir is None:
        return image_path.parent / CACHE_DIRECTORY, pathlib.Path(image_path.name)
    try:
        relative = image_path.relative_to(plate_dir)
    except ValueError:
        relative = pathlib.Path(image_path.name)
    return plate_dir / CACHE_DIRECTORY, relative


def build_preflight_cache_path(plate_dir: pathlib.Path) -> pathlib.Path:
    return plate_dir / CACHE_DIRECTORY / "preflight.pickle"


def build_measurement_cache_path(
    image_path: pathlib.Path, cache_key: dict[str, Any]
) -> pathlib.Path:
    """Build a content-keyed cache path for a complete image measurement."""
    cache_root, relative = _image_cache_location(image_path)
    encoded = json.dumps(cache_key, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:20]
    return (
        cache_root
        / "measurements"
        / relative.parent
        / relative.stem
        / f"{digest}.pickle"
    )


def build_preflight_path(folder: pathlib.Path) -> pathlib.Path:
    """Build standardized preflight data path for the given folder.

    Parameters
    ----------
    folder : pathlib.Path
        Folder where the preflight file should be stored.

    Returns
    -------
    pathlib.Path
        Path to the preflight pickle file.
    """
    return folder / f"{PREFIX}preflight.pickle"


def build_dataframe_path(folder: pathlib.Path) -> pathlib.Path:
    """Build standardized dataframe path for the given folder.

    Parameters
    ----------
    folder : pathlib.Path
        Folder where the dataframe file should be stored.

    Returns
    -------
    pathlib.Path
        Path to the dataframe pickle file.
    """
    return folder / f"{PREFIX}df.pickle"


def build_summary_excel_path(folder: pathlib.Path) -> pathlib.Path:
    """Build standardized summary excel path for the given folder.

    Parameters
    ----------
    folder : pathlib.Path
        Folder where the excel file should be stored.

    Returns
    -------
    pathlib.Path
        Path to the summary excel file.
    """
    return folder / f"{PREFIX}summary.xlsx"


def build_summary_pdf_path(folder: pathlib.Path) -> pathlib.Path:
    """Build standardized summary pdf path for the given folder.

    Parameters
    ----------
    folder : pathlib.Path
        Folder where the pdf file should be stored.

    Returns
    -------
    pathlib.Path
        Path to the summary pdf file.
    """
    return folder / f"{PREFIX}summary.pdf"


def read(p: str | pathlib.Path) -> IntensityImage:
    """Reads an image from a file.

    Parameters
    ----------
    p : str
        Path to the image file.

    Returns
    -------
    np.ndarray
        The image data.
    """
    p = pathlib.Path(p)

    if p.suffix.lower() in (".czi",):
        im = bioio_czi.Reader(p)
        out = im.get_image_data()[0, :, :]  # type: ignore
    else:
        out = skio.imread(p)  # type: ignore

    assert is_intensity_image(out), f"Image {p} is not a valid intensity image."

    return out


def build_source_signature(path: pathlib.Path) -> dict[str, Any]:
    """Build a stable signature for an input file."""
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def find_parent_plate_dir(path: pathlib.Path) -> pathlib.Path | None:
    """Find the nearest parent directory that looks like a plate folder."""
    current = path if path.is_dir() else path.parent
    for parent in (current, *current.parents):
        if parent.stem.startswith("plate"):
            return parent
    return None


def build_image_manifest_key(image_path: pathlib.Path, plate_dir: pathlib.Path) -> str:
    """Build stable image key for a shared plate manifest."""
    return pathlib.PurePosixPath(
        image_path.relative_to(plate_dir).as_posix()
    ).as_posix()


def source_signature_matches(
    source_signature: dict[str, Any] | None,
    expected_signature: dict[str, Any],
) -> bool:
    """Return whether two source signatures match."""
    if source_signature is None:
        return False
    return source_signature.get("size") == expected_signature.get(
        "size"
    ) and source_signature.get("mtime_ns") == expected_signature.get("mtime_ns")


def read_artifact_manifest(image_path: pathlib.Path) -> dict[str, Any]:
    """Read artifact manifest for an image, returning empty data on failure.

    For images inside plate folders, this reads the shared plate manifest and
    returns only the entry corresponding to the provided image.
    """
    manifest_path = build_artifact_manifest_path(image_path)
    if not manifest_path.exists():
        return {}
    try:
        raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw_data, dict):
        return {}

    plate_dir = find_parent_plate_dir(image_path)
    if plate_dir is None:
        return raw_data

    images = raw_data.get("images")
    if not isinstance(images, dict):
        return {}

    image_key = build_image_manifest_key(image_path, plate_dir)
    image_data = images.get(image_key)
    return image_data if isinstance(image_data, dict) else {}


def write_artifact_manifest(image_path: pathlib.Path, manifest: dict[str, Any]) -> None:
    """Write artifact manifest for an image.

    For images inside plate folders, this updates the image entry in a shared
    plate-level manifest.
    """
    manifest_path = build_artifact_manifest_path(image_path)
    plate_dir = find_parent_plate_dir(image_path)

    if plate_dir is None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
        )
        return

    existing: dict[str, Any]
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except Exception:
            existing = {}
    else:
        existing = {}

    images = existing.get("images")
    if not isinstance(images, dict):
        images = {}
        existing["images"] = images

    image_key = build_image_manifest_key(image_path, plate_dir)
    images[image_key] = manifest

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(existing, sort_keys=True, indent=2), encoding="utf-8"
    )


def find_file_with_extension(
    path: pathlib.Path, pattern: str, suffixes: list[str], case_sensitive: bool = False
) -> pathlib.Path | None:
    """Find the first file in a path matching a pattern and allowed suffixes.

    Parameters
    ----------
    path : pathlib.Path
        Directory to search.
    pattern : str
        Glob pattern to use for searching.
    suffixes : list of str
        Allowed file suffixes/extensions.
    case_sensitive : bool, optional
        Whether the search should be case sensitive. Default is False.

    Returns
    -------
    pathlib.Path or None
        Path to the first matching file, or None if not found.
    """
    for p in path.glob(pattern, case_sensitive=case_sensitive):
        if p.is_dir():
            continue
        if p.suffix in suffixes:
            return p
    return None


def delete_cache(folder: pathlib.Path) -> None:
    """Delete managed reusable caches without touching immutable run folders.

    Parameters
    ----------
    folder : pathlib.Path
        Directory from which to delete cache files and folders.

    Notes
    -----
    This compatibility helper now has the same safe scope as the GUI's
    ``Clear reusable cache`` action.  Legacy ``_output_*`` files and managed
    run directories are intentionally left untouched.
    """
    from .workflow import clear_reusable_cache

    clear_reusable_cache(folder)


def count_output_artifacts(folder: pathlib.Path) -> int:
    """Count manifest-backed run directories directly under the given folder.

    Parameters
    ----------
    folder : pathlib.Path
        Folder to inspect.

    Returns
    -------
    int
        Number of managed run directories. Legacy output files are ignored.
    """
    if not folder.exists():
        return 0
    return sum(
        1
        for path in folder.glob(f"{PREFIX}*")
        if path.is_dir() and (path / "run.json").is_file()
    )


def has_output_artifacts(folder: pathlib.Path) -> bool:
    """Return whether output artifacts exist under the given folder."""
    return count_output_artifacts(folder) > 0
