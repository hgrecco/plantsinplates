import pathlib
import shutil
import bioio_czi
from skimage import io as skio

import logging

from .types import IntensityImage, is_intensity_image

__version__ = "2026.04.13"

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
    mask_folder = image_path.parent / f"{PREFIX}mask"
    mask_path = mask_folder / (image_path.stem + ".png")
    return mask_path


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
    skeleton_folder = image_path.parent / f"{PREFIX}skeleton"
    skeleton_path = skeleton_folder / (image_path.stem + ".png")
    return skeleton_path


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
    """Delete all cache files and folders in the given directory.

    Parameters
    ----------
    folder : pathlib.Path
        Directory from which to delete cache files and folders.

    Notes
    -----
    Files and folders starting with the global `PREFIX` will be deleted.
    """
    for p in folder.rglob(f"{PREFIX}*"):
        if p.is_file():
            p.unlink()
        else:
            shutil.rmtree(p, ignore_errors=True)
