"""Build deterministic, platform-independent GitHub release assets."""

import argparse
import hashlib
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plantsinplates.updates import (  # noqa: E402
    INSTALL_FILES,
    installed_version,
    validate_package,
    version_key,
)

INSTALLATION_GUIDE = """# Install and update Plants in Plates

1. Install Pixi from https://pixi.sh/ (version 0.82.1 or later).
2. Extract this complete ZIP into its own writable application folder.
3. In a terminal in that folder, run `pixi run --locked gui`.

Pixi installs Python and the locked dependencies on first launch. Supported
platforms: macOS Apple Silicon, Linux x86-64, and Windows x86-64.

Use Preferences → Check for updates → Update and restart in the application.
You can also run `pixi run --locked update-app`, or add `--check` to only check.
Close other Plants in Plates windows and batch jobs before installing an update.
Use `pixi run --locked batch /path/to/experiment_014` for batch analysis.

Updates only replace application files. Keep experiment data in a separate
folder. Previous run outputs, image inputs, and reusable caches are preserved.

## Recovery

If an update fails, the helper restores the previous files and environment.
Details are in `.plantsinplates_updates/update.log` and `helper.log`.
If recovery was interrupted, keep the staging folders and close all app instances.
Run `pixi run --locked update-app --recover` to retry recovery.

If Pixi cannot read the partially replaced manifest/lockfile, run the already
installed updater Python directly, bypassing Pixi's environment check:

- macOS/Linux: `.pixi/envs/updater/bin/python plantsinplates-updater.py --recover`
- Windows: `.pixi\\envs\\updater\\python.exe plantsinplates-updater.py --recover`

If that wrapper was damaged, replace `plantsinplates-updater.py` in the command
with `.plantsinplates_updates/staging-<id>/helper.py --root .` using the staging
folder named in the log. Backups are in that folder's `backup` directory.
If dependencies cannot be downloaded, restore connectivity and retry recovery.

For a fresh installation, download the full installation ZIP from
https://github.com/hgrecco/plantsinplates/releases/latest and extract it into a
new folder. Do not use GitHub's automatically generated source-code ZIP.
"""

BUNDLE_ENTRYPOINT = b'''"""Lightweight bundle entry point; version needs no scientific imports."""
import runpy
import sys

if sys.argv[1:] == ["version"]:
    from plantsinplates.version import __version__
    print(__version__)
else:
    runpy.run_module("plantsinplates._application", run_name="__main__", alter_sys=True)
'''


def write_zip(path: Path, files: dict[str, bytes]) -> None:
    """Use fixed timestamps, permissions, ordering, and compression."""
    with zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        # Explicit directories let zipimport recognize the namespace package.
        directories = {
            parent.as_posix() + "/"
            for name in files
            for parent in Path(name).parents
            if str(parent) != "."
        }
        for name in sorted(directories | files.keys()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o40755 if name.endswith("/") else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(
                info, b"" if name.endswith("/") else files[name], compresslevel=9
            )


def build_release(root: Path, output: Path) -> tuple[Path, Path, Path]:
    version = installed_version(root)
    version_key(version)
    output.mkdir(parents=True, exist_ok=True)
    files = {
        name: (root / name).read_bytes()
        for name in ("__main__.py", "pixi.toml", "pixi.lock")
    }
    files["plantsinplates/_application.py"] = files["__main__.py"]
    files["__main__.py"] = BUNDLE_ENTRYPOINT
    for path in sorted((root / "plantsinplates").rglob("*")):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and (
                path.suffix == ".py"
                or (
                    path.parent == root / "plantsinplates" / "assets"
                    and path.suffix in {".png", ".ico"}
                )
            )
        ):
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    bundle = output / "plantsinplates-app"
    write_zip(bundle, files)
    installation = {
        "plantsinplates-app": bundle.read_bytes(),
        "pixi.toml": files["pixi.toml"],
        "pixi.lock": files["pixi.lock"],
        "plantsinplates-updater.py": (root / "plantsinplates-updater.py").read_bytes(),
        "INSTALL.md": INSTALLATION_GUIDE.encode("utf-8"),
        "LICENSE": (root / "LICENSE").read_bytes(),
    }
    assert set(installation) == set(INSTALL_FILES)
    package = output / f"plantsinplates-{version}.zip"
    write_zip(package, installation)
    validate_package(package, version)
    checksums = output / "SHA256SUMS"
    checksums.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in (bundle, package)
        ),
        encoding="utf-8",
    )
    return bundle, package, checksums


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    paths = build_release(ROOT, args.output.resolve())
    # Keep the documented development tasks using the local archive.
    (ROOT / "plantsinplates-app").write_bytes(paths[0].read_bytes())
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
