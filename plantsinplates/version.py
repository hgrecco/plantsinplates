"""Read the canonical version from a checkout or the application archive."""

import pathlib
import tomllib


def read_version() -> str:
    """Return the version of the code being executed, including ZIP execution."""
    manifest = pathlib.Path(__file__).parent.parent / "pixi.toml"
    loader = globals().get("__loader__")
    read_data = getattr(loader, "get_data", None)
    data = read_data(str(manifest)) if read_data else manifest.read_bytes()
    return tomllib.loads(data.decode("utf-8"))["workspace"]["version"]


__version__ = read_version()
